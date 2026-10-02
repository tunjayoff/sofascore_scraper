"""
Tek maç getirme boru hattı (docs/design/02-services.md 3.3): eski async toplu hattın ve sync tek maç / refill /
yenileme yollarının yerini alır.

    WorkItem ─→ işçiler (asyncio, `concurrency` tane) ─→ GET /event, dilimler (eşzamanlı) ─→ yazıcı thread'i
    (sınırlı kuyruk) ─→ store.events.put / observe ─→ değişiklik satırı varsa `change` akışına change.recorded

Her çağıran buradan geçer: eşitleme servisinin detay aşaması, kimliğiyle seçilen maçlar, tek maç uç noktası,
refill ve yalnızca yenileme. Böylece:

  * istek politikası tektir: istek katmanının yeniden denemesi (`MAX_RETRIES`); maç başına ek deneme döngüsü yoktur;
  * her yol aynı dilimleri ister: sporun seçilen bütün dilimleri, isteğe bağlılar dahil (`select_slices`);
  * "yalnızca bitmiş maçlar" (`only_finished`) her yolda aynı okunur; bitmemiş maç atlanır (`skipped` /
    `not_due`), başarısız sayılmaz ve yazılmaz;
  * bitmiş maçta istenen her dilimin "veri yok" yanıtı sayılır ve hata kaydı tutulur (isteğe bağlılar dahil);
  * yanıt gelen dilimin gövdesi her zaman dilimin kuralıyla okunur (src.slices.slice_body_state): veri var →
    `ok`, veri yok → `empty`, okunamadı → `failed` / `parse` (sayılmaz, yazılmaz, sonraki çalıştırmada yeniden
    istenir). 404'ün nedeni her yolda "404"tür;
  * açık devre kesici yüzünden gönderilmeyen istek `skipped` / `breaker` sonucudur; kalan işler de öyle biter;
  * refill vazgeçerse (maç artık bitmiş görünmüyor) /event ikinci kez istenmez.

Bekleme yoktur: hızı ortak istek bütçesi (src/throttle.py) ve istek katmanı belirler. İptal ve devre kesici
çağıranın istek bağlamındadır (src.client.context.request_context); boru hattı onları her iş biriminden önce
yeniden okur. İstek katmanının gördüğü iptal (FetchCancelled) çağırana çıkar; kalıcı depolama hatası
(StorageError, `fatal`) da: kalan işler yapılmaz.

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
    Literal,
    Mapping,
    Optional,
    Tuple,
)

from src import breaker as request_breaker
from src.client import Client, endpoints
from src.exceptions import StorageError
from src.logger import get_logger
from src.refresh import change_row, diff_basic
from src.services.planning import WorkItem, phase_of
from src.slices import (
    BODY_DATA,
    BODY_MALFORMED,
    SLICE_EMPTY,
    SLICE_FAILED,
    SLICE_OK,
    SLICE_SKIPPED,
    Outcome,
    slice_body_state,
)
from src.sports import event_sport_slug, select_slices
from src.status import StatusClass, classify_status

if TYPE_CHECKING:
    from src.sports import SliceSelection
    from src.store import PutResult, Store

logger = get_logger("FetchPipeline")

EVENT_KEY = "event"

ItemStatus = Literal["ok", "failed", "skipped"]
ITEM_OK: ItemStatus = "ok"
ITEM_FAILED: ItemStatus = "failed"
ITEM_SKIPPED: ItemStatus = "skipped"

# `skipped` nedenleri
SKIP_BREAKER = request_breaker.BREAKER_OPEN  # "breaker": devre kesici açık, istek gönderilmedi
SKIP_NOT_DUE = "not_due"  # maç bitmemiş ("yalnızca bitmiş maçlar" açık): yazılmadı
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


class FetchPipeline:
    """
    Maç iş birimlerini (`full`, `refill`, `refresh`) yürüten boru hattı.

        pipeline = FetchPipeline(store, concurrency=5, only_finished=True)
        with request_context(cancel=job.cancelled, breaker=breaker):
            summary = pipeline.run_sync(items, on_result=print)

    store          yazmaların ve okumaların deposu
    client         istemci; verilmezse ortamın ayarlarıyla yenisi. Oturum çalıştırma başına açılır ve kapanır.
    concurrency    aynı anda işlenen birim sayısı (uçuşan istekleri istek katmanının semaforu sınırlar)
    only_finished  True: bitmemiş maç atlanır (`skipped` / `not_due`); False: her durumdaki maç yazılır
    selection      dilim seçimi (None: kayıt defterinin varsayılanları; `select_slices`)
    threshold      "veri yok" eşiği (bilgi için; sayaçları Store tutar)
    writer_queue   bekleyebilecek yazma sayısı
    source         `change.recorded` olaylarının kaynağı
    """

    def __init__(self, store: "Store", *, client: Optional[Client] = None, concurrency: int = 5,
                 only_finished: bool = True, selection: "Optional[SliceSelection]" = None,
                 threshold: int = UNAVAILABLE_AFTER_ATTEMPTS, writer_queue: int = DEFAULT_WRITER_QUEUE,
                 source: str = "job") -> None:
        self._store = store
        self._client = client
        self._concurrency = max(1, int(concurrency))
        self._only_finished = bool(only_finished)
        self._selection = selection
        self._threshold = threshold
        self._writer_queue = max(1, int(writer_queue))
        self._source = source

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
                if result.error is not None and result.error.fatal:
                    state["stop"] = result.error
                    return

        try:
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
        if item.owner.kind != "event":
            raise ValueError(f"the pipeline fetches events only, got a {item.owner.kind} work item")
        if item.need == "refresh":
            return await self._refresh(client, item, writer, slots)
        if item.need in ("full", "refill"):
            return await self._fetch(client, item, writer, slots)
        raise ValueError(f"unknown need of a work item: {item.need!r}")

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
        if self._only_finished and not finished:
            status = payload.get("status") or {}
            logger.info("Match %s is not finished (%s/%s); skipped", event_id, status.get("description"),
                        status.get("type"))
            return ItemResult(item, ITEM_SKIPPED, SKIP_NOT_DUE, event=event_outcome, payload=payload)

        sport = event_sport_slug(payload) or ""
        phase = phase_of(classify_status(payload).value)
        selected = select_slices("event", sport, self._selection, phase=phase)
        if item.need == "refill":
            wanted = {key for key, _sub in item.slices}
            selected = tuple(spec for spec in selected if spec.key in wanted)
        keys = [spec.key for spec in selected]
        answers = await asyncio.gather(*(self._get_slice(client, event_id, key) for key in keys))
        slices = dict(zip(keys, answers, strict=True))

        outcomes: Dict[str, Outcome] = {EVENT_KEY: Outcome(SLICE_OK, data=payload,
                                                            fetched_at=event_outcome.fetched_at)}
        counted: Tuple[str, ...] = ()
        if finished:
            # Bitmiş maç: her sonuç uygulanır; "veri yok" yanıtları istenen her dilimde sayılır
            outcomes.update(slices)
            counted = tuple(keys)
        else:
            # Bitmemiş maçta sayaç ve hata kaydı tutulmaz: yalnızca gövdesi olan yanıtlar saklanır
            outcomes.update({key: o for key, o in slices.items() if o.status in (SLICE_OK, SLICE_EMPTY)
                             and o.data is not None})
        result = ItemResult(item, ITEM_OK, event=event_outcome, slices=slices, payload=payload)
        found: Dict[str, Any] = {}
        await self._write(writer, slots, result, lambda: self._store.events.put(
            event_id, outcomes, count_empties=counted if counted else False,
            on_event_change=self._change_fn(found)), "the match details")
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

    async def _get_slice(self, client: Client, event_id: int, key: str) -> Outcome:
        """Bir dilim isteği; yanıt gelen gövde dilimin kuralıyla okunur (`answered_outcome`)."""
        outcome = await client.get(endpoints.event_slice(key, event_id))
        if outcome.status == SLICE_OK or (outcome.status == SLICE_EMPTY and outcome.data is not None):
            return answered_outcome(key, outcome)
        if outcome.failed:
            logger.warning("Slice %s of match %s could not be fetched (%s); it is requested again on the next run",
                           key, event_id, outcome.reason)
        return outcome

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
    state = slice_body_state(key, data)
    common = {"http_status": outcome.http_status, "fetched_at": outcome.fetched_at, "via": outcome.via}
    if state == BODY_DATA:
        return Outcome(SLICE_OK, data=data, **common)
    if state == BODY_MALFORMED:
        logger.warning("Slice %s: the answer has an unexpected shape (%s); treated as a failed request, it will be "
                       "requested again on the next run", key, type(data).__name__)
        # FX-5'in eşlemesi: okunamayan gövdenin hata kaydında HTTP kodu yoktur (istek katmanının "parse"ı gibi)
        return Outcome(SLICE_FAILED, reason=request_breaker.PARSE, fetched_at=outcome.fetched_at, via=outcome.via)
    return Outcome(SLICE_EMPTY, data=data, reason="empty", **common)


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
    "FetchPipeline", "ITEM_FAILED", "ITEM_OK", "ITEM_SKIPPED", "ItemResult", "PipelineSummary", "SKIP_BREAKER",
    "SKIP_CANCELLED", "SKIP_NOT_DUE", "STORE_BUSY_ATTEMPTS", "STORE_BUSY_FIRST_WAIT", "UNAVAILABLE_AFTER_ATTEMPTS",
    "answered_outcome", "is_finished", "put_retrying", "upstream_failure",
]
