"""
Listeler: sezon listeleri ve sezon programları, tipli iş birimleri olarak (docs/design/02-services.md 3.3, plan
maddesi P14).

İki tür liste iş birimi vardır (src/services/planning.py `season_list_item`, `schedule_item`):

  sezon listesi   GET /unique-tournament/{id}/seasons → turnuvanın `seasons` dilimi (`store.entities.put`)
  sezon programı  GET .../rounds; tur listesi haftalık görünüyorsa (1..50) her tur (`events/round/{n}`, kupada
                  `/slug/{slug}`), görünmüyorsa ya da turlar boş döndüyse `events/last/{n}` ve `events/next/{n}`
                  sayfaları → sezonun `schedule/<alt anahtar>` dilimleri

Sonuç tiplidir (`ListingResult`): `ok`, `failed` (neden: istek katmanının nedeni, "parse", "not_found",
"storage") ya da `skipped` (devre kesici açık, ya da liste taze: `fresh`). Çekilemeyen bir sezon listesi ya da tur
"sezon yok" / "maç yok" gibi görünmez: başarısız bir iş birimidir.

Kurallar (eski src/season_fetcher.py ve src/match_fetcher.py'den taşındı; davranış aynı):

  * Tur sayfası olduğu gibi saklanır, meta'sında `complete` (her maçı bitmiş ya da iptal mi) vardır. Tamamlanmış
    tur bir daha istenmez; tamamlanmamış tur ROUND_CACHE_TTL_SECONDS dolunca yeniden istenir. `complete`'i
    olmayan (eski sürümün süzerek yazdığı) tur bir kez yeniden istenir.
  * Olay sayfaları maç kimliğine göre tekilleştirilir; meta'sı `{"filtered": True}` (eski düzenin adı). Her
    durumdaki maç saklanır (plan maddesi ST-27): sayfa, maçı olduğu sürece bütün maçlarıyla yazılır. "Yalnızca
    bitmiş maçlar" (`only_finished`) yalnızca sonucun `chunks`'ını süzer (çağıranın "maç listelendi mi" sorusu);
    neyin saklandığını değiştirmez. Okuyanlar ayarı okurken uygular (src/services/query.py).
  * Maçı olmayan tur atlanır ve saklanmaz (SAVE_EMPTY_ROUNDS emekli, ST-27). 404 "yok"tur, hata değildir.
  * Deneme sayıları istek katmanınınki gibidir: tur listesi 1, olay sayfası 2, tur ve sezon listesi ayardaki
    (`MAX_RETRIES`).

Tazelik (02-services.md 3.5, yeniden çalıştırma): eşitleme işi bir listeyi, son çekiminin üzerinden belli bir
süre geçmediyse yeniden istemez (`max_age`; bkz. SEASON_LIST_TTL_SECONDS ve SCHEDULE_TTL_SECONDS). Program için
"taze": sezonun saklanan sayfalarından en yenisi bu süreden genç ve her sayfa ya tamamlanmış ya da bu süreden genç.
Tur listesinin kendisi saklanmaz; bu yüzden her turu tamamlanmış bir sezonun tur listesi süre dolduktan sonraki
her çalıştırmada bir kez istenir (turlar istenmez).

Yürütme: liste birimleri getirme boru hattında (src/services/pipeline.py) yürür: çalıştırma başına tek ısıtılmış
oturum, depoya her erişim (okuma da) yazıcı thread'inde. `ListingFetcher` boru hattının liste işleyicisidir;
`ListingService` onu tek bir çağrıyla çalıştıran yüzdür (src/season_fetcher.py ve src/match_fetcher.py'nin
sarmalayıcıları onu kullanır). `ScheduleLister` programın kendisidir ve istek ile yazmayı dışarıdan alır, böylece
eski çağıranların istek yolu (src.utils.make_api_request_async) da aynı kuralları kullanır.

`enqueue_events=True` ile bir program, listede bitmiş görünen ve katalogda eksik olan maçlar için maç iş birimleri
(`full` / `refill`) getirir (`ItemResult.follow_up`): boru hattı onları aynı çalıştırmada yürütür. Eşitleme
servisi bunu kullanmaz: detay aşaması listelerden hemen sonra planı katalogdan kurar ve aynı maçları aynı işte
indirir.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from src import breaker as request_breaker
from src.client import endpoints
from src.exceptions import StorageError
from src.logger import get_logger
from src.services import planning
from src.services.pipeline import (
    FAIL_NOT_FOUND,
    FAIL_STORAGE,
    ITEM_FAILED,
    ITEM_OK,
    ITEM_SKIPPED,
    SKIP_BREAKER,
    SKIP_CANCELLED,
    FetchPipeline,
    ItemResult,
    PipelineSummary,
    is_finished,
)
from src.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from src.store import Ref

if TYPE_CHECKING:
    from src.client import Client
    from src.store import SliceInfo, Store

logger = get_logger("Listing")

SEASONS_KEY = planning.LISTING_SEASONS
SCHEDULE_KEY = planning.LISTING_SCHEDULE

# Haftalık tur listesinde kabul edilen en büyük tur numarası (daha büyüğü kupa / play-off kimliğidir)
MAX_ROUND = 50
# Bir türün (`last` / `next`) en çok bu kadar sayfası istenir
EVENT_PAGE_LIMIT = 200
# İstek katmanının deneme sayıları (eski kodla aynı): tur listesi 1, olay sayfası 2; None = ayardaki MAX_RETRIES
ROUNDS_RETRIES = 1
EVENT_PAGE_RETRIES = 2

# Bitmiş sayılan ve bir daha değişmeyecek durumlar (ertelenen maç yeniden planlanabilir)
TERMINAL_STATUS_TYPES = ("finished", "canceled", "cancelled")
# Tamamlanmamış bir tur sayfası bu süre içinde yeniden kullanılır (aynı işte tekrar istek atmamak için)
ROUND_CACHE_TTL_SECONDS = 6 * 3600
# Eşitleme işinin tazelik kuralı (02-services.md 3.5): bu süreden genç sezon listesi ve program yeniden istenmez
SEASON_LIST_TTL_SECONDS = 6 * 3600
SCHEDULE_TTL_SECONDS = 15 * 60

# Sonuç nedenleri (pipeline'ınkilere ek olarak)
SKIP_FRESH = "fresh"  # liste taze: istenmedi
FAIL_PARSE = request_breaker.PARSE  # yanıtın biçimi beklenen gibi değil
FAIL_OTHER = request_breaker.OTHER

_SUB_UNSAFE = re.compile(r"[^a-z0-9_.-]")

Get = Callable[[str, Optional[int]], Awaitable[Outcome]]
Write = Callable[[Callable[[], Any]], Awaitable[Any]]


# --- saf kurallar ---------------------------------------------------------------------------------------

def schedule_sub(kind: str, number: Any, slug: Optional[str] = None) -> str:
    """
    Program sayfasının alt anahtarı: tur `round_<n>` ya da `round_<n>_<slug>`, olay sayfası `last_<n>` /
    `next_<n>`. Eski sürümün dosya adıyla aynı kural (`round_3_final.json` → `round_3_final`): alt anahtar küçük
    harftir (Store büyük harfi reddeder), slug'da alt anahtara giremeyen karakterler `-` olur.
    """
    if kind == "round":
        text = f"round_{number}" + (f"_{slug}" if slug else "")
    elif kind in ("last", "next"):
        text = f"{kind}_{number}"
    else:
        raise ValueError(f"kind: expected 'round', 'last' or 'next', got {kind!r}")
    return _SUB_UNSAFE.sub("-", text.lower())[:80]


def is_week_based_rounds(rounds: Sequence[Mapping[str, Any]], max_round: int = MAX_ROUND) -> bool:
    """
    Tur listesi sıralı lig haftalarına (1..N) benziyor mu; kupa kimliklerine değil. MLS play-off listelerinde tur
    kimlikleri büyüktür (ör. 227): haftalık değildir.
    """
    if not rounds:
        return False
    nums = [r.get("round") for r in rounds if isinstance(r.get("round"), int)]
    if not nums:
        return False
    return all(1 <= n <= max_round for n in nums)


def round_specs(rounds: Sequence[Mapping[str, Any]], max_round: int = MAX_ROUND) -> List[Tuple[int, Optional[str]]]:
    """İstenecek turlar: (numara, slug), tur listesinin sırasıyla; listede uygun tur yoksa 1..max_round."""
    specs: List[Tuple[int, Optional[str]]] = []
    for r in rounds:
        number = r.get("round")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number > max_round:
            continue
        slug = r.get("slug")
        specs.append((number, slug if isinstance(slug, str) and slug else None))
    return specs or [(n, None) for n in range(1, max_round + 1)]


def filter_finished(data: Mapping[str, Any]) -> Tuple[Dict[str, Any], int, int]:
    """
    Yalnızca bitmiş maçları tutan kopya, toplam maç sayısı ve bitmiş maç sayısı. Tur numarası (`roundInfo.round`)
    kopyada `round` olarak durur.
    """
    if not data or "events" not in data:
        return dict(data or {}), 0, 0
    events = list(data.get("events") or [])
    finished = [event for event in events if isinstance(event, dict) and is_finished(event)]
    filtered = dict(data)
    filtered["events"] = finished
    round_info = data.get("roundInfo")
    if isinstance(round_info, dict) and "round" in round_info:
        filtered["round"] = round_info["round"]
    return filtered, len(events), len(finished)


def kept_page(data: Optional[Mapping[str, Any]], only_finished: bool) -> Optional[Dict[str, Any]]:
    """
    Özete (ve olay sayfasında depoya) girecek kısım: "yalnızca bitmiş maçlar" açıksa bitmiş maçlar (yoksa None),
    kapalıysa sayfanın kendisi (maçı yoksa None).
    """
    if not data:
        return None
    filtered, total, finished = filter_finished(data)
    if only_finished:
        return filtered if finished else None
    return dict(data) if total else None


def is_empty_round(data: Optional[Mapping[str, Any]]) -> bool:
    """Tur yanıtında maç yok mu: hiç veri yok, ya da maç listesi boş ve sonraki sayfa yok."""
    if not data:
        return True
    return not data.get("events") and not data.get("hasNextPage", False)


def round_is_complete(data: Mapping[str, Any]) -> bool:
    """Turun her maçı bitmiş ya da iptal edilmiş mi (maçı olmayan tur tamamlanmış sayılmaz)."""
    events = data.get("events") or []
    return bool(events) and all(
        isinstance(event, dict)
        and (is_finished(event) or (event.get("status") or {}).get("type") in TERMINAL_STATUS_TYPES)
        for event in events
    )


def round_result(data: Mapping[str, Any], round_num: Any, only_finished: bool) -> Optional[Dict[str, Any]]:
    """Ham tur verisinden özete girecek kısım (`kept_page`); maçı yoksa None. Sonuçta `round` tur numarasıdır."""
    if is_empty_round(data):
        return None
    result = kept_page({k: v for k, v in data.items() if k != "_complete"}, only_finished)
    if not result:
        return None
    result = dict(result)
    result["round"] = round_num
    return result


def season_list_of(data: Any) -> Optional[List[Dict[str, Any]]]:
    """Sezon listesi yanıtındaki liste; yanıt beklenen biçimde değilse None."""
    if not isinstance(data, dict) or not isinstance(data.get("seasons"), list):
        return None
    return data["seasons"]


def sortable_year(year_str: Any) -> float:
    """
    Sezon yılı dizesi → sıralanabilir sayı (yüksek = daha yeni): "24/25", "2024/2025", "2024", "98/99".
    """
    year = str(year_str or "")
    if not year or year == "0":
        return 0.0
    if "/" in year:
        parts = year.split("/")
        start = parts[0].strip()
        end = parts[1].strip() if len(parts) > 1 else ""
        if len(start) == 2 and len(end) == 2 and start.isdigit() and end.isdigit():
            start_int, end_int = int(start), int(end)
            if start_int > end_int:  # yüzyıl geçişi: 99/00 → 2000
                return 2000.0 + float(end_int)
            if start_int < 50:
                return 2000.0 + float(start_int)
            return 1900.0 + float(start_int)
        try:
            return float(start)
        except ValueError:
            return 0.0
    try:
        return float(year)
    except ValueError:
        return 0.0


def preferred_season_id(seasons: Sequence[Mapping[str, Any]]) -> int:
    """İndirmeye tercih edilen sezon: yeniden eskiye ikinci (en yenisi çoğu zaman yalnızca fikstürdür)."""
    if not seasons:
        return 0
    ordered = sorted(seasons, key=lambda s: sortable_year(s.get("year", "0")), reverse=True)
    pick = ordered[1] if len(ordered) > 1 else ordered[0]
    return int(pick.get("id") or 0)


def resolve_season_id(seasons: Sequence[Mapping[str, Any]], requested_id: int,
                      preferred: Callable[[], int]) -> int:
    """
    Eskimiş olabilecek bir sezon kimliği → güncel listedeki kimlik. SofaScore zaman zaman sezon kimliğini
    değiştirir (ör. 77559 → 77806): listede olmayan kimlik yerine tercih edilen sezon (`preferred()`) kullanılır.
    Liste yoksa istenen kimlik olduğu gibi döner.
    """
    if not seasons:
        return requested_id
    known = {int(s["id"]) for s in seasons if s.get("id") is not None}
    if requested_id in known:
        return requested_id
    chosen = preferred()
    logger.warning("Season id %s is not in the refreshed season list; using %s", requested_id, chosen)
    return chosen or requested_id


# --- sonuçlar ---------------------------------------------------------------------------------------------

@dataclass
class ListingResult:
    """
    Bir liste iş biriminin sonucu.

    kind        "seasons" (sezon listesi) ya da "schedule" (sezon programı)
    status      ok | failed | skipped
    reason      failed / skipped nedeni ("fresh": liste taze, istenmedi; "breaker"; istek nedeni; "not_found";
                "parse"; "storage"); ok'ta None
    seasons     sezon listesinde gelen sezonlar (çekildiyse)
    pages       bu çalıştırmada saklanan program sayfası sayısı
    chunks      özete giren sayfalar ("yalnızca bitmiş maçlar" süzgecinden sonra; `round` alanıyla), eski
                `fetch_all_rounds_*`'ın dönüş değeri
    events      listede görülen maç kimlikleri (önbellekteki turlar dahil)
    finished    bunlardan bitmiş olanlar
    failures    başarısız istekler: (yol, neden)
    listed      taze sayılan programda: sezonun saklanan maç listesi var mı (çekilmediği için `chunks` boştur)
    """

    kind: str
    tournament_id: int
    season_id: Optional[int] = None
    status: str = ITEM_OK
    reason: Optional[str] = None
    seasons: Optional[List[Dict[str, Any]]] = None
    pages: int = 0
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    events: Tuple[int, ...] = ()
    finished: Tuple[int, ...] = ()
    failures: Tuple[Tuple[str, str], ...] = ()
    listed: bool = False

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
    def fresh(self) -> bool:
        return self.status == ITEM_SKIPPED and self.reason == SKIP_FRESH

    @property
    def has_matches(self) -> bool:
        """Program maç verdi mi (eski `fetch_matches_for_season`'ın True'su); taze programda saklanan liste."""
        return bool(self.chunks) or (self.fresh and self.listed)

    def describe(self) -> str:
        """Günlük satırları için: "league 17" ya da "league 17, season 61627"."""
        if self.kind == SCHEDULE_KEY:
            return f"league {self.tournament_id}, season {self.season_id}"
        return f"league {self.tournament_id}"

    @classmethod
    def for_item(cls, item: planning.WorkItem, **fields: Any) -> "ListingResult":
        """İş biriminin sahibinden kurulan sonuç."""
        owner = item.owner
        if owner.kind == "season":
            return cls(SCHEDULE_KEY, int(owner.tournament_id or 0), int(owner.id), **fields)
        return cls(SEASONS_KEY, int(owner.id), **fields)


def _status_of(failures: Sequence[Tuple[str, Outcome]], skipped: bool) -> Tuple[str, Optional[str]]:
    """İsteklerin sonuçlarından birimin durumu: hata varsa failed (en sık neden), devre kesildiyse skipped."""
    if failures:
        reasons = Counter(str(outcome.reason or FAIL_OTHER) for _path, outcome in failures)
        return ITEM_FAILED, reasons.most_common(1)[0][0]
    if skipped:
        return ITEM_SKIPPED, SKIP_BREAKER
    return ITEM_OK, None


@dataclass
class ScheduleRun:
    """Bir programın çekimi sırasında biriken bilgi."""

    chunks: List[Dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    events: Dict[int, Mapping[str, Any]] = field(default_factory=dict)
    failures: List[Tuple[str, Outcome]] = field(default_factory=list)
    skipped: bool = False

    def answered(self, path: str, outcome: Outcome) -> bool:
        """Yanıt alınamayan istek kaydedilir; yanıt (veri ya da kesin "yok") geldiyse True."""
        if outcome.status == SLICE_SKIPPED:
            self.skipped = True
            return False
        if outcome.status == SLICE_FAILED:
            self.failures.append((path, outcome))
            return False
        return True

    def malformed(self, path: str, what: str) -> None:
        logger.warning("%s: the answer of %s has an unexpected shape; treated as a failed request", what, path)
        self.failures.append((path, Outcome(SLICE_FAILED, reason=FAIL_PARSE)))

    def saw(self, data: Mapping[str, Any]) -> None:
        for event in data.get("events") or []:
            if isinstance(event, dict) and isinstance(event.get("id"), int) and not isinstance(event["id"], bool):
                self.events.setdefault(int(event["id"]), event)


# --- program -----------------------------------------------------------------------------------------------

class ScheduleLister:
    """
    Bir sezonun programını çeker ve saklar. İstek (`get`) ve depoya erişim (`write`) dışarıdan verilir: boru
    hattında istemcinin oturumu ve yazıcı thread'i, eski çağıranlarda istek katmanının async işlevi ve doğrudan
    çağrı.

    store              sayfaların yazıldığı ve önbellek kararının okunduğu depo
    only_finished      sonucun `chunks`'ı yalnızca bitmiş maçları sayar (FETCH_ONLY_FINISHED); saklananı değiştirmez
    save_empty_rounds  emekli (ST-27): eski çağıranlar için kabul edilir, etkisi yoktur; maçı olmayan tur saklanmaz
    concurrency        aynı anda istenen tur sayısı
    """

    def __init__(self, store: "Store", *, only_finished: bool, save_empty_rounds: Optional[bool] = None,
                 concurrency: int = 5, max_round: int = MAX_ROUND,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.only_finished = bool(only_finished)
        self.concurrency = max(1, int(concurrency))
        self.max_round = max(1, int(max_round or MAX_ROUND))
        self.clock = clock

    # depoya erişim (eşzamanlı; çağıran `write` ile sarar) ------------------------------------------------

    def save_page(self, league_id: int, season_id: int, sub: str, payload: Mapping[str, Any], *,
                  meta: Mapping[str, Any], empty: bool = False, fetched_at: Optional[dt.datetime] = None) -> None:
        """
        Program sayfasını Store'a yazar (`EntityStore.put`): sezonun `schedule/<sub>` dilimi. meta: tur için
        {"complete": bool}, olay sayfası için {"filtered": True}. empty: yük `empty` durumuyla saklanır.
        Depolama hatası (StoreError) çağırana çıkar.
        """
        outcome = Outcome(SLICE_EMPTY if empty else SLICE_OK, dict(payload), meta=dict(meta), fetched_at=fetched_at)
        self.store.entities.put(Ref.season(int(league_id), int(season_id)), {(SCHEDULE_KEY, sub): outcome},
                                count_empties=False)

    def cached_round(self, league_id: int, season_id: int, sub: str) -> Optional[Dict[str, Any]]:
        """
        Saklanan tur sayfası, yalnızca güncel olmaya devam ediyorsa (yük, `_complete` anahtarı olmadan).

        Bilgi dilimin katalogdaki kaydından gelir: `meta.complete` (tüm maçlar bitmiş mi) ve `fetched_at`. Bitmemiş
        maç içeren bir tur TTL dolunca yeniden çekilir; aksi halde sonradan biten maçlar listeye hiç girmez. Eski
        sürümlerin yazdığı (yalnız bitmiş maçları süzülmüş, `_complete`'siz) tur dosyaları bir kez yeniden çekilir.
        Eski düzendeki tur dosyası da (`matches/...`) aynı kuralla kullanılır. Okunamazsa None (yeniden istenir).
        """
        from src.store import StoreError

        ref = Ref.season(int(league_id), int(season_id))
        try:
            entities = self.store.entities
            info = entities.slice(ref, SCHEDULE_KEY, sub)
            if not info.has_payload or "complete" not in info.meta:
                return None
            if not info.meta["complete"]:
                fetched = info.fetched_at
                age = self.clock() - fetched.timestamp() if fetched is not None else None
                if age is None or age >= ROUND_CACHE_TTL_SECONDS:
                    return None
            data = entities.payload(ref, SCHEDULE_KEY, sub)
        except StoreError as e:
            logger.warning("Stored round %s of season %s cannot be read and is fetched again: %s", sub, season_id, e)
            return None
        return data if isinstance(data, dict) else None

    # istekler ------------------------------------------------------------------------------------------

    async def list(self, league_id: int, season_id: int, get: Get, write: Write) -> ListingResult:
        """
        Sezonun programı: tur listesi haftalıksa turlar, değilse (ya da turlar boş döndüyse) olay sayfaları. Her
        sayfa çekilir çekilmez saklanır. Sonuç tiplidir; özete giren sayfalar `chunks`'tadır.
        """
        run = ScheduleRun()
        rounds = await self.rounds(league_id, season_id, get, run)
        use_weeks = is_week_based_rounds(rounds, max_round=self.max_round)
        if use_weeks:
            specs = round_specs(rounds, self.max_round)
            semaphore = asyncio.Semaphore(self.concurrency)
            answers = await asyncio.gather(*(
                self.round(semaphore, league_id, season_id, number, slug, get, write, run) for number, slug in specs))
            run.chunks.extend(answer for answer in answers if answer is not None)
            logger.info("League %s, season %s: %d of %d rounds listed matches", league_id, season_id,
                        len(run.chunks), len(specs))
        if not run.chunks and not run.skipped:
            if use_weeks:
                logger.warning("League %s, season %s: the rounds listed no matches; falling back to the event pages",
                               league_id, season_id)
            else:
                logger.info("League %s, season %s: no week-based rounds (%d listed); using the event pages",
                            league_id, season_id, len(rounds))
            run.chunks.extend(await self.event_pages(league_id, season_id, get, write, run))
        if not run.chunks and not use_weeks:
            logger.warning("League %s, season %s: neither rounds nor event pages listed a match", league_id, season_id)
        status, reason = _status_of(run.failures, run.skipped)
        if status == ITEM_FAILED:
            logger.warning("League %s, season %s: %d schedule request(s) failed (%s); the schedule is incomplete",
                           league_id, season_id, len(run.failures), reason)
        finished = tuple(sorted(event_id for event_id, event in run.events.items() if is_finished(dict(event))))
        return ListingResult(
            SCHEDULE_KEY, int(league_id), int(season_id), status=status, reason=reason, pages=run.pages,
            chunks=run.chunks, events=tuple(sorted(run.events)), finished=finished,
            failures=tuple((path, str(outcome.reason or FAIL_OTHER)) for path, outcome in run.failures),
        )

    async def rounds(self, league_id: int, season_id: int, get: Get, run: ScheduleRun) -> List[Dict[str, Any]]:
        """GET /rounds: turlar; 404'te, boş yanıtta ya da hatada boş liste (hata `run`'a yazılır)."""
        path = endpoints.rounds(league_id, season_id)
        outcome = await get(path, ROUNDS_RETRIES)
        if not run.answered(path, outcome):
            if outcome.status == SLICE_FAILED:
                logger.warning("Rounds of league %s, season %s could not be fetched (%s)", league_id, season_id,
                               outcome.reason)
            return []
        if outcome.status != SLICE_OK:
            return []
        if not isinstance(outcome.data, dict):
            run.malformed(path, f"League {league_id}, season {season_id}")
            return []
        rounds = outcome.data.get("rounds") or []
        return [r for r in rounds if isinstance(r, dict)] if isinstance(rounds, list) else []

    async def round(self, semaphore: asyncio.Semaphore, league_id: int, season_id: int, round_num: int,
                    slug: Optional[str], get: Get, write: Write, run: ScheduleRun) -> Optional[Dict[str, Any]]:
        """Bir tur (kupada slug'ıyla): önbellekte güncelse o, değilse istenir ve ham hali saklanır."""
        safe_slug = slug.replace("/", "-").replace("\\", "-") if slug else None
        sub = schedule_sub("round", round_num, safe_slug)
        cached = await write(lambda: self.cached_round(league_id, season_id, sub))
        if cached is not None:
            run.saw(cached)
            return round_result(cached, round_num, self.only_finished)
        path = endpoints.round_events(league_id, season_id, round_num, slug)
        async with semaphore:
            outcome = await get(path, None)
        if not run.answered(path, outcome):
            if outcome.status == SLICE_FAILED:
                logger.warning("Round %s of league %s, season %s could not be fetched (%s)", round_num, league_id,
                               season_id, outcome.reason)
            return None
        data = outcome.data
        if outcome.status == SLICE_EMPTY and not data:
            logger.debug("Round %s of league %s, season %s does not exist", round_num, league_id, season_id)
            return None
        if not isinstance(data, dict):
            run.malformed(path, f"League {league_id}, season {season_id}")
            return None
        if is_empty_round(data):
            logger.debug("Round %s of league %s, season %s has no matches", round_num, league_id, season_id)
            return None
        run.saw(data)
        await self._save(write, run, path, league_id, season_id, sub, data, {"complete": round_is_complete(data)},
                         outcome)
        result = round_result(data, round_num, self.only_finished)
        if result is None:
            logger.info("Round %s of league %s, season %s has no finished match (%d listed)", round_num, league_id,
                        season_id, len(data.get("events") or []))
        return result

    async def event_pages(self, league_id: int, season_id: int, get: Get, write: Write,
                          run: ScheduleRun) -> List[Dict[str, Any]]:
        """`events/last` ve `events/next` sayfaları, sırayla; maç kimliğine göre tekilleştirilir ve saklanır."""
        seen: set = set()
        results: List[Dict[str, Any]] = []
        for kind in endpoints.SEASON_EVENT_KINDS:
            page = 0
            while page < EVENT_PAGE_LIMIT:
                path = endpoints.season_events_page(league_id, season_id, kind, page)
                outcome = await get(path, EVENT_PAGE_RETRIES)
                if not run.answered(path, outcome):
                    if outcome.status == SLICE_FAILED:
                        logger.warning("League %s, season %s: events/%s/%s could not be fetched (%s)", league_id,
                                       season_id, kind, page, outcome.reason)
                    break
                data = outcome.data
                if outcome.status == SLICE_EMPTY or not data:
                    break
                if not isinstance(data, dict):
                    run.malformed(path, f"League {league_id}, season {season_id}")
                    break
                if not data.get("events"):
                    break
                events = []
                for event in data.get("events") or []:
                    event_id = event.get("id") if isinstance(event, dict) else None
                    if event_id is None or event_id in seen:
                        continue
                    seen.add(event_id)
                    events.append(event)
                page_payload = {"events": events, "hasNextPage": bool(data.get("hasNextPage")),
                                "source": f"{kind}/{page}"}
                run.saw(page_payload)
                sub = schedule_sub(kind, page)
                if events:
                    # Sayfa her durumdaki maçıyla saklanır; sonuca (özete) ayarın süzdüğü kısmı girer
                    await self._save(write, run, path, league_id, season_id, sub, page_payload, {"filtered": True},
                                     outcome)
                    kept = kept_page(page_payload, self.only_finished)
                    if kept and kept.get("events"):
                        results.append(dict(kept, round=f"{kind}_{page}"))
                if not data.get("hasNextPage"):
                    break
                page += 1
        logger.info("League %s, season %s: %d distinct matches in the event pages, %d pages kept", league_id,
                    season_id, len(seen), len(results))
        return results

    async def _save(self, write: Write, run: ScheduleRun, path: str, league_id: int, season_id: int, sub: str,
                    data: Mapping[str, Any], meta: Mapping[str, Any], outcome: Outcome, *, empty: bool = False) -> None:
        """Sayfayı saklar. Kalıcı depolama hatası çağırana çıkar; geçici olanı sayfanın başarısızlığıdır."""
        try:
            await write(lambda: self.save_page(league_id, season_id, sub, data, meta=meta, empty=empty,
                                               fetched_at=outcome.fetched_at))
        except StorageError as e:
            if e.fatal:
                raise
            logger.error("Schedule page %s of league %s, season %s could not be stored: %s", sub, league_id,
                         season_id, e)
            run.failures.append((path, Outcome(SLICE_FAILED, reason=FAIL_STORAGE)))
            return
        run.pages += 1


# --- sezon listesi -----------------------------------------------------------------------------------------

def store_season_list(store: "Store", league_id: int, data: Mapping[str, Any], *,
                      fetched_at: Optional[dt.datetime] = None) -> int:
    """
    Bir lig için çekilen sezon listesini Store'a yazar: turnuvanın `seasons` dilimi
    (`v3/tournaments/<lig>/seasons.json.gz`), SofaScore'un yanıtı olduğu gibi. Boş liste de saklanır (durumu
    `empty`). Saklanan sezon sayısını döndürür; depolama hatası çağırana çıkar.
    """
    seasons = data.get("seasons") if isinstance(data, Mapping) else None
    outcome = Outcome(SLICE_OK if seasons else SLICE_EMPTY, dict(data), fetched_at=fetched_at)
    store.entities.put(Ref.tournament(int(league_id)), {SEASONS_KEY: outcome}, count_empties=False)
    return len(seasons or [])


# --- tazelik -------------------------------------------------------------------------------------------------

def _age(info: "SliceInfo", now: float) -> Optional[float]:
    return now - info.fetched_at.timestamp() if info.fetched_at is not None else None


def season_list_is_fresh(store: "Store", league_id: int, max_age: float, *, now: Optional[float] = None) -> bool:
    """Sezon listesi `max_age` saniyeden genç mi (saklanan ve okunabilir bir liste)."""
    info = store.entities.slice(Ref.tournament(int(league_id)), SEASONS_KEY)
    age = _age(info, time.time() if now is None else now)
    return info.has_payload and age is not None and 0 <= age < max_age


def schedule_is_fresh(store: "Store", league_id: int, season_id: int, max_age: float, *,
                      now: Optional[float] = None) -> bool:
    """
    Sezonun programı taze mi: saklanan sayfası var, en yeni sayfası `max_age` saniyeden genç ve her sayfası ya
    tamamlanmış (`meta.complete`) ya da `max_age`'den genç.
    """
    moment = time.time() if now is None else now
    pages = [info for info in store.entities.slices(Ref.season(int(league_id), int(season_id)))
             if info.key == SCHEDULE_KEY and info.has_payload]
    ages = [_age(info, moment) for info in pages]
    if not pages or any(age is None for age in ages):
        return False
    known = [age for age in ages if age is not None]
    if min(known) < 0 or min(known) >= max_age:
        return False
    return all(info.meta.get("complete") is True or age < max_age for info, age in zip(pages, known, strict=True))


# --- boru hattının liste işleyicisi --------------------------------------------------------------------------

class ListingFetcher:
    """
    Boru hattının liste işleyicisi (src.services.pipeline.ListingHandler): sezon listesi ve program birimlerini
    yürütür, sonucu `ItemResult.listing`'e koyar.

    season_max_age / schedule_max_age  tazelik süreleri (saniye); None = her zaman istenir
    enqueue_events                     programda bitmiş görünen eksik maçlar için maç birimleri getirilir
    """

    def __init__(self, store: "Store", *, only_finished: bool, save_empty_rounds: Optional[bool] = None,
                 concurrency: int = 5, max_round: int = MAX_ROUND, season_max_age: Optional[float] = None,
                 schedule_max_age: Optional[float] = None, enqueue_events: bool = False,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.schedule = ScheduleLister(store, only_finished=only_finished, concurrency=concurrency,
                                       max_round=max_round, clock=clock)
        self.season_max_age = season_max_age
        self.schedule_max_age = schedule_max_age
        self.enqueue_events = enqueue_events
        self.clock = clock

    async def run(self, item: planning.WorkItem, get: Get, write: Write) -> ItemResult:
        keys = {key for key, _sub in item.slices}
        if item.owner.kind == "tournament" and SEASONS_KEY in keys:
            listing = await self.season_list(item, get, write)
            return ItemResult(item, listing.status, listing.reason, listing=listing)
        if item.owner.kind == "season" and SCHEDULE_KEY in keys:
            listing = await self.season_schedule(item, get, write)
            follow_up: Tuple[planning.WorkItem, ...] = ()
            if self.enqueue_events and listing.finished:
                follow_up = tuple(await write(lambda: self._event_items(listing.finished)))
            return ItemResult(item, listing.status, listing.reason, listing=listing, follow_up=follow_up)
        raise ValueError(f"unknown listing work item: {item.owner.kind} {sorted(keys)}")

    async def season_list(self, item: planning.WorkItem, get: Get, write: Write) -> ListingResult:
        league_id = int(item.owner.id)
        if self.season_max_age is not None and await write(
                lambda: season_list_is_fresh(self.store, league_id, self.season_max_age or 0, now=self.clock())):
            logger.info("Season list of league %s is fresh; not requested", league_id)
            return ListingResult.for_item(item, status=ITEM_SKIPPED, reason=SKIP_FRESH)
        path = endpoints.seasons(league_id)
        outcome = await get(path, None)
        if outcome.status == SLICE_SKIPPED:
            return ListingResult.for_item(item, status=ITEM_SKIPPED, reason=outcome.reason or SKIP_BREAKER)
        if outcome.status == SLICE_FAILED:
            logger.error("Season list of league %s could not be fetched (%s)", league_id, outcome.reason)
            return ListingResult.for_item(item, status=ITEM_FAILED, reason=outcome.reason or FAIL_OTHER,
                                          failures=((path, str(outcome.reason or FAIL_OTHER)),))
        if outcome.status == SLICE_EMPTY and outcome.reason == request_breaker.NOT_FOUND:
            logger.error("Season list of league %s: SofaScore has no such tournament (404)", league_id)
            return ListingResult.for_item(item, status=ITEM_FAILED, reason=FAIL_NOT_FOUND,
                                          failures=((path, FAIL_NOT_FOUND),))
        seasons = season_list_of(outcome.data)
        if seasons is None:
            logger.error("Season list of league %s: the answer has no season list", league_id)
            return ListingResult.for_item(item, status=ITEM_FAILED, reason=FAIL_PARSE, failures=((path, FAIL_PARSE),))
        data = dict(outcome.data)
        try:
            await write(lambda: store_season_list(self.store, league_id, data, fetched_at=outcome.fetched_at))
        except StorageError as e:
            if e.fatal:
                raise
            logger.error("Season list of league %s could not be stored: %s", league_id, e)
            return ListingResult.for_item(item, status=ITEM_FAILED, reason=FAIL_STORAGE, seasons=seasons,
                                          failures=((path, FAIL_STORAGE),))
        logger.info("Season list of league %s stored (%d seasons)", league_id, len(seasons))
        return ListingResult.for_item(item, seasons=seasons)

    async def season_schedule(self, item: planning.WorkItem, get: Get, write: Write) -> ListingResult:
        league_id, season_id = int(item.owner.tournament_id or 0), int(item.owner.id)
        if self.schedule_max_age is not None and await write(lambda: schedule_is_fresh(
                self.store, league_id, season_id, self.schedule_max_age or 0, now=self.clock())):
            from src.services import tournaments

            listed = await write(lambda: tournaments.has_matches(self.store, league_id, season_id))
            logger.info("Schedule of league %s, season %s is fresh; not requested", league_id, season_id)
            return ListingResult.for_item(item, status=ITEM_SKIPPED, reason=SKIP_FRESH, listed=bool(listed))
        return await self.schedule.list(league_id, season_id, get, write)

    def _event_items(self, event_ids: Iterable[int]) -> List[planning.WorkItem]:
        """Bitmiş görünen maçlardan katalogda eksik olanların (full / refill) iş birimleri."""
        from src.services.query import RefreshPolicy

        items = planning.plan_items(self.store, list(event_ids), RefreshPolicy.current(now=self.clock()))
        return [item for item in items if item.need in ("full", "refill")]


class ListingService:
    """
    Liste birimlerini boru hattında, çağıranın thread'inde (yeni bir asyncio döngüsüyle) yürütür.

        service = ListingService(store, only_finished=True, concurrency=5)
        result = service.schedule(17, 61627)          # ListingResult

    Çalıştırma başına bir oturum açılır (ilk istekte ısınmayla); hiç istek gerekmezse (liste taze) açılmaz. İptal
    ve devre kesici çağıranın istek bağlamındadır (src.client.context.request_context).
    """

    def __init__(self, store: "Store", *, only_finished: bool, save_empty_rounds: Optional[bool] = None,
                 concurrency: int = 5, max_round: int = MAX_ROUND, client: Optional["Client"] = None,
                 enqueue_events: bool = False, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.only_finished = bool(only_finished)
        self.concurrency = max(1, int(concurrency))
        self.max_round = max_round
        self.client = client
        self.enqueue_events = enqueue_events
        self.clock = clock

    def run(self, items: Iterable[planning.WorkItem], *, season_max_age: Optional[float] = None,
            schedule_max_age: Optional[float] = None, cancelled: Optional[Callable[[], bool]] = None,
            on_result: Optional[Callable[[ItemResult], None]] = None) -> PipelineSummary:
        """Birimleri sırayla yürütür (liste birimleri birer birer; bir programın turları eşzamanlı)."""
        handler = ListingFetcher(self.store, only_finished=self.only_finished, concurrency=self.concurrency,
                                 max_round=self.max_round, season_max_age=season_max_age,
                                 schedule_max_age=schedule_max_age, enqueue_events=self.enqueue_events,
                                 clock=self.clock)
        pipeline = FetchPipeline(self.store, client=self.client, concurrency=1, listing=handler)
        return pipeline.run_sync(items, cancelled=cancelled, on_result=on_result)

    def season_list(self, league_id: int, *, max_age: Optional[float] = None) -> ListingResult:
        """Bir ligin sezon listesi; max_age: bu kadar saniyeden genç liste yeniden istenmez."""
        return self._one(planning.season_list_item(league_id), season_max_age=max_age)

    def schedule(self, league_id: int, season_id: int, *, max_age: Optional[float] = None) -> ListingResult:
        """Bir sezonun programı; max_age: bu kadar saniyeden genç program yeniden istenmez."""
        return self._one(planning.schedule_item(league_id, season_id), schedule_max_age=max_age)

    def _one(self, item: planning.WorkItem, **ages: Optional[float]) -> ListingResult:
        summary = self.run([item], **ages)
        for result in summary.results:
            if result.item == item:
                if isinstance(result.listing, ListingResult):
                    return result.listing
                # Boru hattı birimi başlatmadan bitirdi (devre kesici açık)
                return ListingResult.for_item(item, status=result.status, reason=result.reason)
        return ListingResult.for_item(item, status=ITEM_SKIPPED, reason=SKIP_CANCELLED)


__all__ = [
    "EVENT_PAGE_LIMIT", "EVENT_PAGE_RETRIES", "MAX_ROUND", "ROUNDS_RETRIES", "ROUND_CACHE_TTL_SECONDS",
    "SCHEDULE_KEY", "SCHEDULE_TTL_SECONDS", "SEASONS_KEY", "SEASON_LIST_TTL_SECONDS", "SKIP_FRESH",
    "TERMINAL_STATUS_TYPES", "ListingFetcher", "ListingResult", "ListingService", "ScheduleLister", "ScheduleRun",
    "filter_finished", "is_empty_round", "is_week_based_rounds", "kept_page", "preferred_season_id",
    "resolve_season_id", "round_is_complete", "round_result", "round_specs", "schedule_is_fresh", "schedule_sub",
    "season_list_is_fresh", "season_list_of", "sortable_year", "store_season_list",
]
