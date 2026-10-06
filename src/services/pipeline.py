"""
Tek maç getirme boru hattı (docs/design/02-services.md 3.3): eski async toplu hattın ve sync tek maç / refill /
yenileme yollarının yerini alır.

    WorkItem ─→ işçiler (asyncio, `concurrency` tane) ─→ GET /event, dilimler (eşzamanlı) ─→ yazıcı thread'i
    (sınırlı kuyruk) ─→ store.events.put / observe ─→ değişiklik satırı varsa `change` akışına change.recorded

Her çağıran buradan geçer: eşitleme servisinin detay aşaması, kimliğiyle seçilen maçlar, tek maç uç noktası,
refill ve yalnızca yenileme. Böylece:

  * istek politikası tektir: istek katmanının yeniden denemesi (`MAX_RETRIES`); maç başına ek deneme döngüsü yoktur;
  * her yol aynı dilimleri ister: sporun seçilen bütün dilimleri, isteğe bağlılar dahil (`select_slices`);
  * her durumdaki maç saklanır (plan maddesi ST-27): bitmemiş maçın olay yükü ve gövdesi gelen dilimleri yazılır,
    "veri yok" yanıtları sayılmaz. Hangi maçın indirileceğine planlayıcı karar verir (src/services/planning.py:
    yalnızca listeden bilinen bitmemiş maç indirilmez); "yalnızca bitmiş maçlar" ayarı (FETCH_ONLY_FINISHED)
    artık yalnızca okurken uygulanır (src/services/query.py);
  * bitmiş maçta istenen her dilimin "veri yok" yanıtı sayılır ve hata kaydı tutulur (isteğe bağlılar dahil);
  * yanıt gelen dilimin gövdesi her zaman dilimin kuralıyla okunur (src.slices.slice_body_state): veri var →
    `ok`, veri yok → `empty`, okunamadı → `failed` / `parse` (sayılmaz, yazılmaz, sonraki çalıştırmada yeniden
    istenir). 404'ün nedeni her yolda "404"tür;
  * açık devre kesici yüzünden gönderilmeyen istek `skipped` / `breaker` sonucudur; kalan işler de öyle biter;

Bekleme yoktur: hızı ortak istek bütçesi (src/throttle.py) ve istek katmanı belirler. İptal ve devre kesici
çağıranın istek bağlamındadır (src.client.context.request_context); boru hattı onları her iş biriminden önce
yeniden okur. İstek katmanının gördüğü iptal (FetchCancelled) çağırana çıkar; kalıcı depolama hatası
(StorageError, `fatal`) da: kalan işler yapılmaz.

Liste iş birimleri (`listing`: sezon listesi, sezon programı; plan maddesi P14) de aynı çalıştırmada, aynı
oturumla ve aynı yazıcı thread'iyle yürür: boru hattı onları kurucuya verilen liste işleyicisine
(src/services/listing.py, `ListingHandler`) devreder. Bir birimin sonucu yeni iş birimleri getirebilir
(`ItemResult.follow_up`, ör. listede yeni biten maçlar): bunlar aynı çalıştırmanın kuyruğunun sonuna eklenir.

Yazmalar tek bir yazıcı thread'inde yapılır; döngü diske dokunmaz. Kuyruk sınırlıdır (`writer_queue`): depo
yavaşken işçiler bekler, bellek büyümez. Depo meşgulse (StoreBusy) yazma artan beklemeyle yeniden denenir.
"""
from __future__ import annotations

import asyncio
import dataclasses
import concurrent.futures
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Awaitable,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Tuple,
)

from src import breaker as request_breaker
from src.client import Client, endpoints
from src.exceptions import StorageError
from src.logger import get_logger
from src.refresh import change_row, diff_basic
from src.services.planning import CONFIGURED, SelectionPolicy, WorkItem, phase_of, resolve_policy
from src.slices import (
    BODY_DATA,
    BODY_MALFORMED,
    BODY_NO_DATA,
    SLICE_EMPTY,
    SLICE_FAILED,
    SLICE_OK,
    SLICE_SKIPPED,
    Outcome,
    slice_body_state,
)
from src.sports import DEFAULT_ODDS_PROVIDER, PROVIDER_SUBS, SliceSpec, event_sport_slug, get_slice, select_slices
from src.status import StatusClass, classify_status

if TYPE_CHECKING:
    from src.store import PutResult, Store

logger = get_logger("FetchPipeline")

EVENT_KEY = "event"

ItemStatus = Literal["ok", "failed", "skipped"]
ITEM_OK: ItemStatus = "ok"
ITEM_FAILED: ItemStatus = "failed"
ITEM_SKIPPED: ItemStatus = "skipped"

# `skipped` nedenleri
SKIP_BREAKER = request_breaker.BREAKER_OPEN  # "breaker": devre kesici açık, istek gönderilmedi
SKIP_NOT_DUE = "not_due"  # ST-27'den beri üretilmez (bitmemiş maç da yazılır); eski çağıranlar için durur
SKIP_CANCELLED = "cancelled"  # iş durduruldu, birim başlamadı
# `failed` nedenleri (istek nedenleri "403", "429", "5xx", "timeout", "network", "parse", "other" dışında)
FAIL_NOT_FOUND = "not_found"  # SofaScore'da böyle bir maç yok (404 ya da içinde olay olmayan yanıt)
FAIL_STORAGE = "storage"  # veri alındı ama yazılamadı
FAIL_NOT_STORED = "not_stored"  # yenilenecek kayıt depoda yok

# Bitmiş bir maçta bu kadar kesin "veri yok" yanıtından sonra dilim artık beklenmez (src/services/query.py)
UNAVAILABLE_AFTER_ATTEMPTS = 2

# Depo meşgulken (başka bir süreç yazıyor) bir yazma bu kadar kez, artan beklemeyle yeniden denenir
STORE_BUSY_ATTEMPTS = 4
STORE_BUSY_FIRST_WAIT = 0.5

# Yazıcı kuyruğunun varsayılan uzunluğu: bekleyen yazma sayısı (her biri bir maçın yükleri)
DEFAULT_WRITER_QUEUE = 16

CHANGE_STREAM = "change"
CHANGE_RECORDED = "change.recorded"

_FINISHED = (StatusClass.COMPLETED, StatusClass.DECIDED_WITHOUT_PLAY)


def is_finished(event: Mapping[str, Any]) -> bool:
    """Oynanıp biten ya da oynanmadan karara bağlanan maç (src/status.py; MatchFetcher._is_finished_event)."""
    return classify_status(dict(event)) in _FINISHED


@dataclass
class ItemResult:
    """
    Bir iş biriminin sonucu.

    status   ok: maç yazıldı (dilimlerin bazıları başarısız olabilir); failed: yazılmadı ya da /event alınamadı;
             skipped: hiç denenmedi ya da bilerek yazılmadı (`reason`: breaker | not_due | cancelled)
    reason   failed / skipped nedeni; ok'ta None
    event    /event isteğinin sonucu (gönderilmediyse None). İçinde olay olmayan 200 `empty` / `empty` olarak
    slices   bu birimde istenen dilimlerin sonuçları, istek sırasıyla (anahtar → sonuç)
    payload  alınan olay yükü (`event` nesnesi); yoksa None
    changed  yenilemede ve olay yükü değişen yazmada değişen alanlar ({alan: [eski, yeni]})
    put      Store'un yazma sonucu; yazılmadıysa None
    error    yazmayı bitiren depolama hatası; yoksa None
    listing  liste biriminde listenin sonucu (src.services.listing.ListingResult); diğer birimlerde None
    follow_up  bu sonucun getirdiği yeni iş birimleri (aynı çalıştırmanın kuyruğuna eklenir)
    """

    item: WorkItem
    status: ItemStatus
    reason: Optional[str] = None
    event: Optional[Outcome] = None
    slices: Dict[str, Outcome] = field(default_factory=dict)
    payload: Optional[Dict[str, Any]] = None
    changed: Dict[str, List[Any]] = field(default_factory=dict)
    put: Optional["PutResult"] = None
    error: Optional[StorageError] = None
    listing: Any = None
    follow_up: Tuple[WorkItem, ...] = ()

    @property
    def event_id(self) -> int:
        return self.item.owner.id

    @property
    def ok(self) -> bool:
        return self.status == ITEM_OK

    @property
    def failed(self) -> bool:
        return self.status == ITEM_FAILED

    @property
    def skipped(self) -> bool:
        return self.status == ITEM_SKIPPED

    @property
    def change_seq(self) -> Optional[int]:
        return self.put.change_seq if self.put is not None else None

    def upstream_failure(self) -> Optional[Outcome]:
        """SofaScore isteği reddettiyse o sonuç (`upstream_failure`), aksi halde None."""
        return upstream_failure(self.event, self.slices)


def upstream_failure(event: Optional[Outcome], slices: Mapping[str, Outcome]) -> Optional[Outcome]:
    """
    Çekimi SofaScore tarafı engellediyse o başarısız sonuç, aksi halde None (FX-1'in kuralı):
      - /event isteği başarısız olduysa onun sonucu;
      - dilim istendiyse ve HİÇBİRİ yanıt almadıysa (başarısız ya da açık devre kesici yüzünden gönderilmedi)
        başarısız olanların en sık nedeni (eşitlikte ilk istenen).
    Kesin "yok" (404, içinde veri olmayan 200) bir yanıttır: tek bir dilim bile yanıt aldıysa None döner.
    """
    if event is not None and event.failed:
        return event
    outcomes = list(slices.values())
    if not outcomes or not all(o.failed or o.status == SLICE_SKIPPED for o in outcomes):
        return None
    failures = [o for o in outcomes if o.failed]
    if not failures:
        return None
    reason = Counter(o.reason for o in failures).most_common(1)[0][0]
    return next(o for o in failures if o.reason == reason)


@dataclass
class PipelineSummary:
    """Bir çalıştırmanın özeti: birim sayıları, devre kesici ve iptal."""

    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: Counter = field(default_factory=Counter)
    refreshed: int = 0
    changed: int = 0
    cancelled: bool = False
    breaker: Optional[str] = None  # devre kesildiyse nedeni ("403" | "429" | "5xx" | "other")
    results: List[ItemResult] = field(default_factory=list)

    def add(self, result: ItemResult) -> None:
        self.results.append(result)
        if result.ok:
            self.ok += 1
            if result.item.need == "refresh":
                self.refreshed += 1
                self.changed += int(bool(result.changed))
        elif result.failed:
            self.failed += 1
        else:
            self.skipped[str(result.reason)] += 1


ResultCallback = Callable[[ItemResult], None]
CancelCheck = Callable[[], bool]

# Liste işleyicisinin gördüğü istek ve yazma: get(yol, deneme sayısı) → sonuç; write(fn) → fn'in değeri, yazıcı
# thread'inde (depoya her erişim, okuma da)
ListingGet = Callable[[str, Optional[int]], Awaitable[Outcome]]
ListingWrite = Callable[[Callable[[], Any]], Awaitable[Any]]


class ListingHandler(Protocol):
    """Liste iş birimlerini yürüten nesne (src/services/listing.py, ListingFetcher)."""

    async def run(self, item: WorkItem, get: ListingGet, write: ListingWrite) -> ItemResult: ...


class FetchPipeline:
    """
    Maç iş birimlerini (`full`, `refill`, `refresh`) yürüten boru hattı.

        pipeline = FetchPipeline(store, concurrency=5)
        with request_context(cancel=job.cancelled, breaker=breaker):
            summary = pipeline.run_sync(items, on_result=print)

    store          yazmaların ve okumaların deposu
    client         istemci; verilmezse ortamın ayarlarıyla yenisi. Oturum çalıştırma başına açılır ve kapanır.
    concurrency    aynı anda işlenen birim sayısı (uçuşan istekleri istek katmanının semaforu sınırlar)
    only_finished  emekli (ST-27): eski çağıranlar için kabul edilir, etkisi yoktur; her durumdaki maç yazılır
    selection      dilim seçimi: CONFIGURED (varsayılan) = etkin ayarların ve takip tablosunun seçimi, maç maç
                   (`planning.SelectionPolicy.for_payload`: maçın sporu, turnuvası, takımları); None = kayıt
                   defterinin varsayılanları; bir SliceSelection ya da ad listesi her maça aynen. Seçilmeyen dilim
                   hiç istenmez
    threshold      "veri yok" eşiği (bilgi için; sayaçları Store tutar)
    writer_queue   bekleyebilecek yazma sayısı
    source         `change.recorded` olaylarının kaynağı
    listing        liste iş birimlerinin işleyicisi; verilmezse liste birimi ValueError verir
    """

    def __init__(self, store: "Store", *, client: Optional[Client] = None, concurrency: int = 5,
                 only_finished: Optional[bool] = None, selection: Any = CONFIGURED,
                 threshold: int = UNAVAILABLE_AFTER_ATTEMPTS, writer_queue: int = DEFAULT_WRITER_QUEUE,
                 source: str = "job", listing: Optional[ListingHandler] = None) -> None:
        self._store = store
        self._client = client
        self._concurrency = max(1, int(concurrency))
        self._selection = selection
        self._active: Any = None if selection is CONFIGURED else selection
        self._threshold = threshold
        self._writer_queue = max(1, int(writer_queue))
        self._source = source
        self._listing = listing

    # --- çalıştırma ------------------------------------------------------------------------------------

    def run_sync(self, items: Iterable[WorkItem], *, cancelled: Optional[CancelCheck] = None,
                 on_result: Optional[ResultCallback] = None) -> PipelineSummary:
        """`run`, çağıranın thread'inde yeni bir asyncio döngüsüyle (iş başına bir döngü)."""
        return asyncio.run(self.run(items, cancelled=cancelled, on_result=on_result))

    async def run(self, items: Iterable[WorkItem], *, cancelled: Optional[CancelCheck] = None,
                  on_result: Optional[ResultCallback] = None) -> PipelineSummary:
        """
        Birimleri verildiği sırayla `concurrency` işçiye dağıtır; her birimin sonucu bittiği anda `on_result`'a
        verilir (döngünün thread'inde). İptal edildiyse (`cancelled()`) başlamamış birimler sonuç üretmez ve
        özet `cancelled` olur; devre kesildiyse başlamamış birimler `skipped` / `breaker` olur.
        """
        queue = list(items)
        summary = PipelineSummary(total=len(queue))
        if not queue:
            return summary
        client = self._client if self._client is not None else Client()
        writer = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline-writer")
        slots = asyncio.Semaphore(self._writer_queue)
        position = 0
        state: Dict[str, Any] = {"stop": None}

        def is_cancelled() -> bool:
            if cancelled is not None and cancelled():
                summary.cancelled = True
            return summary.cancelled

        def report(result: ItemResult) -> None:
            summary.add(result)
            if on_result is not None:
                on_result(result)

        async def worker() -> None:
            nonlocal position
            while position < len(queue):
                if state["stop"] is not None or is_cancelled():
                    return
                item = queue[position]
                position += 1
                breaker = request_breaker.current()
                if breaker is not None and breaker.tripped:
                    report(ItemResult(item, ITEM_SKIPPED, SKIP_BREAKER))
                    continue
                result = await self._process(client, item, writer, slots)
                if result.status == ITEM_SKIPPED and result.reason == SKIP_CANCELLED:
                    summary.cancelled = True
                    return
                report(result)
                if result.follow_up:
                    # Sonucun getirdiği birimler aynı çalıştırmada, kuyruğun sonunda yürür
                    queue.extend(result.follow_up)
                    summary.total += len(result.follow_up)
                if result.error is not None and result.error.fatal:
                    state["stop"] = result.error
                    return

        try:
            if self._selection is CONFIGURED and any(item.need != "refresh" for item in queue):
                # Seçim çalıştırma başına bir kez, yazıcı thread'inde çözülür (takip tablosu okunur); maç maç
                # `_fetch`'te. Liste birimleri sonradan `full` birimleri getirebilir
                self._active = await self._in_writer(writer, lambda: resolve_policy(self._selection, self._store))
            async with client:
                tasks = [asyncio.create_task(worker()) for _ in range(min(self._concurrency, len(queue)))]
                try:
                    await asyncio.gather(*tasks)
                except BaseException:
                    # İptal (FetchCancelled), beklenmeyen hata: kalan işçiler durdurulur ve beklenir, oturum
                    # kapanmadan ve döngü bitmeden hiçbiri askıda kalmasın
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
        finally:
            # Başlamış yazmalar biter (yarım yazma olmaz); yenisi kabul edilmez
            writer.shutdown(wait=True)
        breaker = request_breaker.current()
        if breaker is not None and breaker.tripped:
            summary.breaker = breaker.reason()
        if state["stop"] is not None:
            raise state["stop"]
        return summary

    # --- bir birim -------------------------------------------------------------------------------------

    async def _process(self, client: Client, item: WorkItem, writer: concurrent.futures.Executor,
                       slots: asyncio.Semaphore) -> ItemResult:
        if item.need == "listing":
            return await self._list(client, item, writer, slots)
        if item.need == "owner" and item.owner.kind != "event":
            return await self._owner(client, item, writer, slots)
        if item.owner.kind != "event":
            raise ValueError(f"the pipeline fetches events only, got a {item.owner.kind} work item")
        if item.need == "refresh":
            return await self._refresh(client, item, writer, slots)
        if item.need in ("full", "refill"):
            return await self._fetch(client, item, writer, slots)
        raise ValueError(f"unknown need of a work item: {item.need!r}")

    async def _list(self, client: Client, item: WorkItem, writer: concurrent.futures.Executor,
                    slots: asyncio.Semaphore) -> ItemResult:
        """Liste birimi: işleyiciye çalıştırmanın oturumu ve yazıcısı verilir."""
        handler = self._listing
        if handler is None:
            raise ValueError("listing work items need a listing handler (src.services.listing)")

        async def get(path: str, retries: Optional[int] = None) -> Outcome:
            return await client.get(path, retries=retries)

        async def write(fn: Callable[[], Any]) -> Any:
            async with slots:
                return await self._in_writer(writer, fn)

        return await handler.run(item, get, write)

    async def _get_event(self, client: Client, item: WorkItem) -> Tuple[Optional[ItemResult], Outcome]:
        """/event isteği. Birim burada bittiyse (başarısız, atlanan, maç yok) sonucu da döner."""
        event_id = item.owner.id
        outcome = await client.get(endpoints.event(event_id))
        if outcome.status == SLICE_SKIPPED:
            return ItemResult(item, ITEM_SKIPPED, outcome.reason or SKIP_BREAKER, event=outcome), outcome
        if outcome.failed:
            logger.warning("Event request for match %s failed (%s)", event_id, outcome.reason)
            return ItemResult(item, ITEM_FAILED, outcome.reason, event=outcome), outcome
        payload = outcome.data.get("event") if isinstance(outcome.data, dict) else None
        if not isinstance(payload, dict) or not payload:
            # 404 ya da içinde olay olmayan yanıt: SofaScore'da böyle bir maç yok
            missing = Outcome(SLICE_EMPTY, reason=outcome.reason if outcome.status == SLICE_EMPTY else "empty",
                              http_status=outcome.http_status, fetched_at=outcome.fetched_at, via=outcome.via)
            logger.info("Match %s: no such event on SofaScore", event_id)
            return ItemResult(item, ITEM_FAILED, FAIL_NOT_FOUND, event=missing), missing
        # Sonucun verisi olay nesnesidir (yanıtın `event` alanı), eski tek maç yolunun raporundaki gibi
        return None, dataclasses.replace(outcome, data=payload)

    async def _fetch(self, client: Client, item: WorkItem, writer: concurrent.futures.Executor,
                     slots: asyncio.Semaphore) -> ItemResult:
        """`full` ve `refill`: /event, sonra seçilen ve eksik dilimler, sonra tek bir `put`."""
        event_id = item.owner.id
        ended, event_outcome = await self._get_event(client, item)
        if ended is not None:
            return ended
        payload: Dict[str, Any] = event_outcome.data
        finished = is_finished(payload)
        if not finished:
            status = payload.get("status") or {}
            logger.info("Match %s is not finished (%s/%s); stored as it is now", event_id, status.get("description"),
                        status.get("type"))

        sport = event_sport_slug(payload) or ""
        phase = phase_of(classify_status(payload).value)
        chosen = self._active
        if isinstance(chosen, SelectionPolicy):
            chosen = chosen.for_payload(event_id, sport or None, payload)
        selected = select_slices("event", sport, chosen, phase=phase)
        provider = self._active.provider if isinstance(self._active, SelectionPolicy) else DEFAULT_ODDS_PROVIDER
        pairs = [(spec, sub) for spec in selected for sub in spec.sub_keys(provider)]
        if item.need == "refill":
            wanted = set(item.slices)
            pairs = [(spec, sub) for spec, sub in pairs if (spec.key, sub) in wanted]
        keys = [spec.key for spec, _sub in pairs]
        answers = await asyncio.gather(*(self._get_slice(client, event_id, spec.key, sub) for spec, sub in pairs))
        # Alt anahtarsız dilim adıyla, alt anahtarlı dilim (bahis oranları) "anahtar/alt anahtar" adıyla
        slices = {slice_label(spec.key, sub): answer for (spec, sub), answer in zip(pairs, answers, strict=True)}
        targets = {slice_label(spec.key, sub): (spec.key, sub) if sub else spec.key for spec, sub in pairs}

        outcomes: Dict[Any, Outcome] = {EVENT_KEY: Outcome(SLICE_OK, data=payload,
                                                            fetched_at=event_outcome.fetched_at)}
        counted: Tuple[str, ...] = ()
        if finished:
            # Bitmiş maç: her sonuç uygulanır; "veri yok" yanıtları istenen her dilimde sayılır
            outcomes.update((targets[name], o) for name, o in slices.items())
            counted = tuple(dict.fromkeys(keys))
        else:
            # Bitmemiş maçta sayaç ve hata kaydı tutulmaz: yalnızca gövdesi olan yanıtlar saklanır
            outcomes.update((targets[name], o) for name, o in slices.items()
                            if o.status in (SLICE_OK, SLICE_EMPTY) and o.data is not None)
        kept = tuple(dict.fromkeys(spec.key for spec, _sub in pairs if spec.keep_history))
        # Geçmişi tutulan dilim yoksa çağrı bugünküyle aynıdır (keep_history verilmez)
        history: Dict[str, Any] = {"keep_history": kept} if kept else {}
        result = ItemResult(item, ITEM_OK, event=event_outcome, slices=slices, payload=payload)
        found: Dict[str, Any] = {}
        await self._write(writer, slots, result, lambda: self._store.events.put(
            event_id, outcomes, count_empties=counted if counted else False,
            on_event_change=self._change_fn(found), **history), "the match details")
        result.changed = found.get("changed") or {}
        if result.put is not None:
            how = "promoted from the old layout and stored" if result.put.promoted else "stored"
            logger.info("Match %s %s (%d files written)", event_id, how, len(result.put.written))
        return result

    async def _refresh(self, client: Client, item: WorkItem, writer: concurrent.futures.Executor,
                       slots: asyncio.Semaphore) -> ItemResult:
        """`refresh`: yalnızca /event; Store'a gözlem olarak yazılır (`observe`)."""
        event_id = item.owner.id
        stored = await self._in_writer(writer, lambda: self._store.events.get(event_id))
        if stored is None or not stored.has_event_payload:
            return ItemResult(item, ITEM_FAILED, FAIL_NOT_STORED)
        ended, event_outcome = await self._get_event(client, item)
        if ended is not None:
            if ended.failed:
                logger.warning("Refresh of match %s: /event could not be fetched", event_id)
            return ended
        payload = event_outcome.data
        result = ItemResult(item, ITEM_OK, event=event_outcome, payload=payload)
        found: Dict[str, Any] = {}
        await self._write(writer, slots, result, lambda: self._store.events.observe(
            event_id, payload, on_event_change=self._change_fn(found)), "the refreshed match page")
        result.changed = found.get("changed") or {}
        if result.ok and result.changed:
            row = found.get("row") or {}
            if row.get("status_regressed"):
                logger.warning("Match %s counted as played is now %s; the record is kept", event_id,
                               payload.get("status"))
            changed = list(result.changed)
            logger.info("Match %s refreshed: %d fields changed (%s)", event_id, len(changed), ", ".join(changed[:5]))
        return result

    async def _get_slice(self, client: Client, event_id: int, key: str, sub: str = "") -> Outcome:
        """Bir dilim isteği; yanıt gelen gövde dilimin kuralıyla okunur (`answered_outcome`)."""
        outcome = await client.get(endpoints.event_slice(key, event_id, sub) if sub
                                   else endpoints.event_slice(key, event_id))
        if outcome.status == SLICE_OK or (outcome.status == SLICE_EMPTY and outcome.data is not None):
            return with_provenance(key, sub, answered_outcome(key, outcome), self._country())
        if outcome.failed:
            logger.warning("Slice %s of match %s could not be fetched (%s); it is requested again on the next run",
                           slice_label(key, sub), event_id, outcome.reason)
        return with_provenance(key, sub, outcome, self._country())

    def _country(self) -> str:
        """Oranların kaydına yazılacak ülke (`[client] odds_country`); seçim politikası yoksa ya da verilmemişse ""."""
        return self._active.country if isinstance(self._active, SelectionPolicy) else ""

    async def _owner(self, client: Client, item: WorkItem, writer: concurrent.futures.Executor,
                     slots: asyncio.Semaphore) -> ItemResult:
        """
        `owner` (plan maddesi P28): maç dışı bir sahibin (sezon, takım, oyuncu, spor) planlanan dilimleri
        eşzamanlı istenir ve tek bir `store.entities.put` ile yazılır. "Veri yok" yanıtları sayılır; geçmişi
        tutulan dilimin (sezonun oranları) değişen yükü geçmişine eklenir.
        """
        ref = item.owner
        ids = owner_path_ids(ref)
        pairs = [(key, sub) for key, sub in item.slices]
        answers = await asyncio.gather(*(self._get_owner_slice(client, ref, key, sub, ids) for key, sub in pairs))
        slices = {slice_label(key, sub): answer for (key, sub), answer in zip(pairs, answers, strict=True)}
        outcomes = {(key, sub) if sub else key: answer for (key, sub), answer in zip(pairs, answers, strict=True)}
        kept = tuple(dict.fromkeys(key for key, _sub in pairs if (get_slice(key) or _NO_SPEC).keep_history))
        history: Dict[str, Any] = {"keep_history": kept} if kept else {}
        result = ItemResult(item, ITEM_OK, slices=slices)
        if all(o.status == SLICE_SKIPPED or (o.failed and o.reason == SKIP_BREAKER) for o in outcomes.values()):
            # Hiçbir istek gönderilmedi (devre kesici): yazılacak bir şey yok
            result.status, result.reason = ITEM_SKIPPED, SKIP_BREAKER
            return result
        await self._write(writer, slots, result, lambda: self._store.entities.put(ref, outcomes, **history),
                          f"the {ref.kind} data")
        if result.status == ITEM_OK:
            failure = upstream_failure(None, slices)
            if failure is not None:
                # Hata kayıtları yazıldı; birim SofaScore'un reddettiği istek yüzünden başarısız sayılır
                result.status, result.reason = ITEM_FAILED, failure.reason
            elif result.put is not None:
                logger.info("%s %s: %d files written", ref.kind.capitalize(), ref.id, len(result.put.written))
        return result

    async def _get_owner_slice(self, client: Client, ref: Any, key: str, sub: str, ids: Mapping[str, int]) -> Outcome:
        outcome = await client.get(endpoints.owner_slice(key, sub, **ids))
        if outcome.status == SLICE_OK or (outcome.status == SLICE_EMPTY and outcome.data is not None):
            return with_provenance(key, sub, answered_outcome(key, outcome), self._country())
        if outcome.failed:
            logger.warning("Slice %s of %s %s could not be fetched (%s); it is requested again on the next run",
                           slice_label(key, sub), ref.kind, ref.id, outcome.reason)
        return with_provenance(key, sub, outcome, self._country())

    @staticmethod
    def _change_fn(found: Dict[str, Any]) -> Callable[[Optional[Mapping[str, Any]], Mapping[str, Any]],
                                                     Optional[Dict[str, Any]]]:
        """Olay yükü değişirken çağrılan karşılaştırma (yazma kilidi altında; Store'a yazmaz)."""

        def on_event_change(previous: Optional[Mapping[str, Any]],
                            payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
            if not previous:
                return None
            changed = diff_basic(dict(previous), dict(payload))
            found["changed"] = changed
            if not changed:
                return None
            found["row"] = change_row(dict(previous), dict(payload), changed,
                                      event_sport_slug(dict(payload)) or event_sport_slug(dict(previous)) or "")
            return found["row"]

        return on_event_change

    # --- yazıcı ----------------------------------------------------------------------------------------

    async def _in_writer(self, writer: concurrent.futures.Executor, fn: Callable[[], Any]) -> Any:
        """Depoya bir erişim, yazıcı thread'inde (döngü diske dokunmaz)."""
        return await asyncio.get_running_loop().run_in_executor(writer, fn)

    async def _write(self, writer: concurrent.futures.Executor, slots: asyncio.Semaphore, result: ItemResult,
                     write: Callable[[], "PutResult"], what: str) -> None:
        """
        Yazmayı yazıcının kuyruğuna koyar ve bitmesini bekler; kuyruk doluysa önce yer açılmasını bekler. Yazma
        bittikten sonra değişiklik satırı varsa `change.recorded` eklenir. Depolama hatası sonucu `failed` /
        `storage` yapar.
        """
        event_id = result.event_id
        async with slots:
            try:
                result.put = await self._in_writer(writer, lambda: self._put_and_announce(event_id, write, what))
            except StorageError as e:
                logger.error("Match %s could not be stored: %s", event_id, e)
                result.status, result.reason, result.error = ITEM_FAILED, FAIL_STORAGE, e

    def _put_and_announce(self, event_id: int, write: Callable[[], "PutResult"], what: str) -> "PutResult":
        put = put_retrying(self._store, event_id, what, write)
        if put.change_seq is not None:
            self._announce_change(event_id, put.change_seq)
        return put

    def _announce_change(self, event_id: int, seq: int) -> None:
        """`change` akışına change.recorded (en iyi çabayla: akışa eklenemezse yalnızca uyarı loglanır)."""
        from src.store import StreamEvent

        try:
            row = self._store.events.get(event_id)
            self._store.streams.append(CHANGE_STREAM, [StreamEvent(
                type=CHANGE_RECORDED, data={"change_seq": seq}, event_id=event_id,
                sport=row.sport if row is not None else None,
                tournament_id=row.tournament_id if row is not None else None,
                source=self._source, dedup_key=f"change:{seq}",
            )])
        except Exception as e:  # noqa: BLE001 - kayıt yazıldı; bildirim kaybı işi bozmamalı
            logger.warning("change.recorded for match %s (change %s) could not be appended: %s", event_id, seq, e)


_NO_SPEC = SliceSpec("_", "/_")


def slice_label(key: str, sub: str = "") -> str:
    """Bir dilimin adı (Store'un adıyla aynı): alt anahtarsız dilimde anahtar, alt anahtarlıda `anahtar/alt`."""
    return f"{key}/{sub}" if sub else key


def owner_path_ids(ref: Any) -> Dict[str, int]:
    """Maç dışı sahibin yol kimlikleri (src/sports.py dilim yollarının yer tutucuları)."""
    if ref.kind == "season":
        return {"tournament_id": ref.tournament_id, "season_id": ref.id}
    if ref.kind in ("team", "player", "tournament"):
        return {f"{ref.kind}_id": ref.id}
    return {}


def with_provenance(key: str, sub: str, outcome: Outcome, country: str = "") -> Outcome:
    """
    Sağlayıcı alt anahtarlı dilimin (bahis oranları) sonucuna yükün kaynağı yazılır: sağlayıcı kimliği her zaman
    (`meta.provider_id`), ülke yalnızca kullanıcı `[client] odds_country` verdiyse (`meta.country`). Hangi
    sağlayıcının döndüğü SofaScore'da ülkeye bağlıdır; ülke makineden (IP, konum) türetilmez ve istemcinin IP'si
    saklanmaz (sahibin kararı, 2026-10-06). Öteki dilimlerin sonucu değişmez.
    """
    spec = get_slice(key)
    if spec is None or spec.subs != PROVIDER_SUBS or not sub or outcome.status == SLICE_SKIPPED:
        return outcome
    meta: Dict[str, Any] = {**(outcome.meta or {}), "provider_id": int(sub)}
    if country:
        meta["country"] = country
    return dataclasses.replace(outcome, meta=meta)


def body_state(key: str, body: Any) -> str:
    """
    Yanıt gövdesinin üç yanıtından biri: kayıt defterinde `body_key`'i olan dilimde (P28) gövde bir nesne ve o
    anahtarın değeri doluysa veri var, None ya da boş gövde "veri yok", nesne olmayan gövde okunamaz; öteki
    dilimlerde src.slices.slice_body_state.
    """
    spec = get_slice(key)
    if spec is None or spec.body_key is None:
        return slice_body_state(key, body)
    if body is None or (isinstance(body, (dict, list)) and not body):
        return BODY_NO_DATA
    if not isinstance(body, dict):
        return BODY_MALFORMED
    return BODY_DATA if body.get(spec.body_key) else BODY_NO_DATA


def answered_outcome(key: str, outcome: Outcome) -> Outcome:
    """
    Yanıt gelen (hata olmayan) dilim isteği, gövdenin üç yanıtına göre (src.slices.slice_body_state):
      - veri var: `ok`
      - okunabiliyor ama içinde veri yok: kesin `empty` / "empty" (sayılır; gövde sonuçta durur ve saklanır)
      - okunamadı (gövde beklenen JSON türünde değil; 0, false ve "" dahil): başarısız istek, neden "parse".
        Kesin bir "yok" yanıtı değildir: sayılmaz, dilimin hata kaydına yazılır ve sonraki çalıştırmada yeniden
        istenir. Gövde sonuca konmaz, yani saklanmaz. Devre kesiciye bildirilmez: istek katmanı bu isteği yanıt
        almış olarak saymıştır ve engellenme belirtisi değildir.
    """
    data = outcome.data
    state = body_state(key, data)
    common = {"http_status": outcome.http_status, "fetched_at": outcome.fetched_at, "via": outcome.via}
    if state == BODY_DATA:
        return Outcome(SLICE_OK, data=data, **common)
    if state == BODY_MALFORMED:
        logger.warning("Slice %s: the answer has an unexpected shape (%s); treated as a failed request, it will be "
                       "requested again on the next run", key, type(data).__name__)
        # FX-5'in eşlemesi: okunamayan gövdenin hata kaydında HTTP kodu yoktur (istek katmanının "parse"ı gibi)
        return Outcome(SLICE_FAILED, reason=request_breaker.PARSE, fetched_at=outcome.fetched_at, via=outcome.via)
    return Outcome(SLICE_EMPTY, data=data, reason="empty", **common)


def run_extras(store: "Store", *, seasons: Iterable[Tuple[int, int]] = (), tournament_ids: Iterable[int] = (),
               cancelled: Optional[CancelCheck] = None, concurrency: int = 5, config_manager: Any = None,
               selection: Any = CONFIGURED, now: Optional[float] = None,
               on_result: Optional[ResultCallback] = None) -> Optional[PipelineSummary]:
    """
    Eşitlemenin P28 aşaması: başlamamış maçların bahis oranları (`planning.prematch_items`) ve maç dışı sahiplerin
    dilimleri (`planning.owner_items`), tek bir çalıştırmada. Seçim ne zamanlı bir maç dilimini ne de maç dışı
    bir dilimi seçiyorsa hiçbir şey okunmaz, istenmez ve None döner (varsayılan yapılandırma). config_manager
    verilirse işin devre kesicisi onunla kurulur (bağlamda biri yoksa).
    """
    import contextlib

    from src.services import planning
    from src.services.query import RefreshPolicy

    chosen = resolve_policy(selection, store)
    if not planning.extras_selected(chosen):
        return None
    policy = RefreshPolicy.current(now)
    items = (planning.prematch_items(store, policy, tournament_ids=tuple(tournament_ids), selection=chosen)
             + planning.owner_items(store, policy, seasons=tuple(seasons), selection=chosen))
    if not items:
        return PipelineSummary()
    logger.info("Odds and non-match data: %d work items", len(items))
    breaker = request_breaker.scope(config_manager) if config_manager is not None else contextlib.nullcontext()
    with breaker:
        return FetchPipeline(store, concurrency=concurrency, selection=chosen).run_sync(
            items, cancelled=cancelled, on_result=on_result)


def put_retrying(store: "Store", event_id: int, what: str, write: Callable[[], Any], *,
                 log: Any = None) -> Any:
    """
    Store'a bir yazma. Depo meşgulse (başka bir süreç yazıyor, StoreBusy) STORE_BUSY_ATTEMPTS kez, artan
    beklemeyle yeniden denenir; sonra StoreBusy (kalıcı olmayan bir depolama hatası) çağırana çıkar. Öteki
    depolama hataları olduğu gibi çıkar; beklenmeyen hata (ör. Store'un reddettiği bir yük, ValueError) kalıcı
    olmayan bir StorageError'a çevrilir: yalnızca o maç başarısızdır. log: satırların günlükçüsü (verilmezse bu modülün).
    """
    from src.store import StoreBusy

    log = log if log is not None else logger
    wait = STORE_BUSY_FIRST_WAIT
    for attempt in range(STORE_BUSY_ATTEMPTS):
        try:
            return write()
        except StoreBusy:
            if attempt == STORE_BUSY_ATTEMPTS - 1:
                raise
            log.warning("The data store is busy (another process is writing); retrying %s (match %s) in %.1f s",
                        what, event_id, wait)
            time.sleep(wait)
            wait *= 2
        except StorageError:
            raise
        except Exception as e:
            log.error("Could not store %s (match %s): %s", what, event_id, e)
            raise StorageError.from_exception(e, os.path.join(str(store.data_dir), "v3", "events")) from e
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = [
    "CHANGE_RECORDED", "CHANGE_STREAM", "EVENT_KEY", "FAIL_NOT_FOUND", "FAIL_NOT_STORED", "FAIL_STORAGE",
    "FetchPipeline", "ITEM_FAILED", "ITEM_OK", "ITEM_SKIPPED", "ItemResult", "ListingGet", "ListingHandler",
    "ListingWrite", "PipelineSummary", "SKIP_BREAKER",
    "SKIP_CANCELLED", "SKIP_NOT_DUE", "STORE_BUSY_ATTEMPTS", "STORE_BUSY_FIRST_WAIT", "UNAVAILABLE_AFTER_ATTEMPTS",
    "answered_outcome", "is_finished", "put_retrying", "upstream_failure",
    "body_state", "owner_path_ids", "run_extras", "slice_label", "with_provenance",
]
