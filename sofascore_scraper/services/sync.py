"""
Eşitleme servisi: seçilen ligleri, sezonları ve maçları indirir (docs/design/02-services.md 2.7).

İndirme işinin akışı (eskiden web işinin modülündeydi; plan maddesi P08 taşıdı): sezon listeleri → maç listeleri →
maç detayları. Akış modül değişkenleri yerine bir iş tutamacıyla (JobHandle) konuşur: iptal sorusu,
ilerleme (JobProgress), iş günlüğü satırı. Tutamaç verilmezse iş kaydı olmadan çalışır.

Listeler (sezon listesi, sezon programı) tipli iş birimleridir (sofascore_scraper/services/listing.py, plan maddesi P14): servis
onları bağlamdaki SeasonFetcher / MatchFetcher sarmalayıcılarının `list_seasons` / `list_schedule` yüzüyle çalıştırır
(getirme boru hattı: çalıştırma başına tek ısıtılmış oturum, yazıcı thread'i). Çekilemeyen bir sezon listesi ya da
tur "sezon yok" / "maç yok" gibi görünmez: başarısız bir iş birimidir (`SyncResult.failed_listings`) ve iş
`partial` biter. Taze bir liste (sezon listesi SEASON_LIST_TTL_SECONDS, program SCHEDULE_TTL_SECONDS içinde
çekilmiş) yeniden istenmez.

Komut satırı da aynı servisi çağırır (main.py: `--headless --update-all` ve `--refresh-only`); yalnızca yenileme
ayrı bir kiptir (`mode="refresh"`: kayıtlı geçici maçların /event'i yeniden okunur, başka istek atılmaz). Yalnızca
sezon listeleri de bir kiptir (`mode="seasons"`, plan maddesi FX-13): listeler tazelik süresine bakılmadan yeniden
okunur, program ve detay istenmez.

Hangi turnuvalar (plan maddesi FX-13): belirtim bir lig ya da seçim vermiyorsa takip tablosunun etkin turnuva
takipleri (`FollowsService.sync_tournaments`: leagues.txt'in aynası, yapılandırma dosyasının `[[follow]]`
girdileri, API'den ve `ssc follows add` ile eklenenler). Her takibin `seasons` seçimi uygulanır: "all" listedeki
her sezon, "current" listenin ilki (SofaScore en yeniyi önce verir), "last:N" ilk N, kimlikler o sezonlar.
`follows` verilirse yalnızca o takipler. Takip tablosu okunamazsa (depo açılamadı) ligler eskisi gibi
yapılandırmadan okunur (`ConfigManager.get_leagues`, bütün sezonlar).

Takım, oyuncu ve maç takipleri (plan maddesi FX-19; sofascore_scraper/services/follow_sync.py): tam kipte, belirtim bir lig ya da
seçim vermiyorsa her etkin takım, oyuncu ve maç takibi (`follows` verilirse yalnızca adı verilenler) maçlarını
indirir. Takımın ve oyuncunun maç listesi maç listesi aşamasında okunur (takibin penceresiyle), maçlar detay
aşamasının sonunda getirme boru hattıyla, takiplerin veri seçimiyle indirilir. Okunamayan liste
`failed_listings`'te (`kind` "team_events" ya da "player_events", `league_id` takımın ya da oyuncunun kimliği)
ve iş `partial` biter.

Maç detayları (detay aşaması, kimliğiyle seçilen maçlar, yalnızca yenileme) tek getirme boru hattıyla indirilir
(sofascore_scraper/services/pipeline.py, plan maddesi P13). Servis ona bağlamdaki MatchDataFetcher'ın eski adlı giriş noktalarıyla
(`fetch_detail_ids`, `fetch_matches_batch`, `refresh_matches`) ulaşır: bunlar yalnızca iş birimlerini kurar; çekici
P15'te kalktığında çağrılar buraya taşınır. Bitmemiş maç atlanır ve başarısız sayılmaz.

İşin sonunda CSV yazılmaz (karar D9, plan maddesi EX-1): dışa aktarma istendiğinde üretilir
(sofascore_scraper/services/export.py; web'de `GET /api/export/csv` ve dışa aktarma işi, komut satırında `ssc export`).

İşin tek bir devre kesicisi vardır (sofascore_scraper/breaker.py). İstek katmanı her isteğin sonucunu ona bildirir; her aşama
döngüsünde ona bakar. SofaScore engellediğinde kalan lig/sezon/maç için istek atılmaz ve neden iş kartına
yazılır. Servis SofaScore kaynaklı hiçbir durumda fırlatmaz; kalıcı depolama hatası (StorageError: disk dolu,
izin yok) çağırana çıkar.

Servis yazdırmaz (print) ve işin bitiş durumunu kendisi yazmaz: sonucu SyncResult olarak döndürür, onu iş
kaydına ve kullanıcı metnine çeviren çağıran yüzdür (`ssc sync`, `POST /api/v1/jobs`, eski `/api/fetch`
(sofascore_scraper/web/api/legacy.py) ve zamanlayıcı).
"""
from __future__ import annotations

import inspect
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Protocol, Sequence, Tuple, Union

from sofascore_scraper import breaker as request_breaker
from sofascore_scraper.client.context import FetchCancelled, request_context
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.jobs.progress import JobProgress
from sofascore_scraper.logger import get_logger
from sofascore_scraper.services import follow_sync, listing
from sofascore_scraper.services.context import ServiceContext

logger = get_logger("SyncService")

SyncMode = Literal["full", "details", "refresh", "seasons"]
SyncState = Literal["succeeded", "partial", "cancelled"]

# JobProgress aşamaları, çalıştıkları sırayla
FULL_PHASES: Tuple[str, ...] = ("seasons", "matches", "details")
DETAILS_PHASES: Tuple[str, ...] = ("details",)
# Yalnızca yenileme: kayıtlı maçlar yeniden okunur; ilerleme detay aşamasının sayacıyla gösterilir
REFRESH_PHASES: Tuple[str, ...] = ("details",)
# Yalnızca sezon listeleri (plan maddesi FX-13)
SEASONS_PHASES: Tuple[str, ...] = ("seasons",)
_LAST_N = re.compile(r"^last:([1-9][0-9]*)$")

# (lig, sezon, sezon adı): maç listesi aşamasının bir adımı
SeasonStep = Tuple[int, int, Optional[str]]
# Detay aşamasının planı: lig → yalnızca bu sezonlar (None: tüm sezonlar). Anahtar None ise maç dizinindeki
# tüm ligler taranır.
DetailPlan = Dict[Optional[int], Optional[List[int]]]


@dataclass(frozen=True)
class SyncSelection:
    """
    Hedefli seçim: bir lig ve onun sezonları ya da maçları.

    Tam kipte yalnızca `season_ids`, detay kipinde yalnızca `match_ids` okunur (bugünkü akış böyledir).
    """

    league_id: int
    season_ids: Tuple[int, ...] = ()
    match_ids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class SyncSpec:
    """
    Ne indirilecek.

    mode        "full": sezon listeleri + maç listeleri + detaylar; "details": yalnızca detaylar;
                "refresh": yalnızca kayıtlı geçici maçların yenilenmesi; `selections` verilirse yalnızca onların
                maçları, yenilenmeleri gerekmese de (yalnızca /event; plan maddesi FX-13, 05-web-ui.md G23);
                "seasons": yalnızca sezon listeleri, tazelik süresine bakılmadan
    league_id   tek lig; yoksa takip edilen bütün turnuvalar. `selections` ya da `follows` varsa okunmaz.
    selections  hedefli seçimler; boşsa `league_id` geçerlidir

    Takip kimlikleriyle hedeflenen çalışma `FollowsSyncSpec`tir (alt sınıf: bu sınıfın iş kaydındaki sözlüğü,
    `dataclasses.asdict`, FX-13'ten önceki gibi kalır). İşin sonunda CSV aşaması yoktur (EX-1); okunmayan `export`
    alanı plan maddesi FX-15'te kalktı (eski iş kayıtlarının saklanan belirtimi bir sözlüktür, yeniden okunmaz).
    """

    mode: SyncMode = "full"
    league_id: Optional[int] = None
    selections: Tuple[SyncSelection, ...] = ()

    @property
    def job_phases(self) -> Tuple[str, ...]:
        """Bu işin geçeceği JobProgress aşamaları (tutamacın ilerleme nesnesi bunlarla kurulur)."""
        if self.mode == "refresh":
            return REFRESH_PHASES
        if self.mode == "seasons":
            return SEASONS_PHASES
        return DETAILS_PHASES if self.mode == "details" else FULL_PHASES


@dataclass(frozen=True)
class FollowsSyncSpec(SyncSpec):
    """
    Adı verilen takiplerin eşitlemesi (plan maddesi FX-13, 05-web-ui.md G23). follows: takip kimlikleri
    (`tournament:17`); yalnızca bu turnuva takipleri, kendi sezon seçimleriyle indirilir (tam ve sezon kipi).
    `league_id` ve `selections` okunmaz.
    """

    follows: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncTarget:
    """İndirilecek bir turnuva: takibin adı ve sezon seçimi ("all", "current", "last:N" ya da kimlikler)."""

    tournament_id: int
    name: Optional[str] = None
    seasons: Union[str, Tuple[int, ...]] = "all"


def pick_seasons(listed: Sequence[int], choice: Union[str, Sequence[int]]) -> List[int]:
    """
    Takibin sezon seçimi, sezon listesindeki kimliklere uygulanır (liste SofaScore'un sırasıyla, en yeni önce):
    "all" hepsi, "current" ilki, "last:N" ilk N; kimlik listesi verilen kimliklerdir (listede olmasalar da;
    eskimiş kimliği çağıran çözer). Tanınmayan seçim "all" sayılır.
    """
    if isinstance(choice, str):
        if choice == "current":
            return list(listed[:1])
        found = _LAST_N.match(choice)
        if found:
            return list(listed[:int(found.group(1))])
        return list(listed)
    return list(dict.fromkeys(int(v) for v in choice))


def sync_targets(ctx: ServiceContext) -> Dict[int, SyncTarget]:
    """
    Eşitlemenin turnuvaları: takip tablosunun etkin turnuva takipleri (kimlik → hedef), takip sırasıyla. Tablo
    okunamazsa (depo açılamadı ya da meşgul) yapılandırmanın ligleri, bütün sezonlarıyla (bugünkü yedek yol).
    """
    from sofascore_scraper.services.follows import ConfigLeagues, FollowsService

    try:
        try:
            store = ctx.store
        except AttributeError:  # deposu olmayan bağlam (gömülü kullanım, testlerin sahte bağlamı)
            store = None
        if store is None:
            return {int(lid): SyncTarget(int(lid), name) for lid, name in ctx.config.get_leagues().items()}
        # Lig dosyasının aynası şimdi yenilenir: bağlam kurulurken veri dizininin state.db'si henüz yoksa ayna
        # yazılmamıştır (yeni bir veri dizininin ilk eşitlemesi)
        mirror = getattr(ctx.config, "mirror_follows", None)
        if callable(mirror):
            mirror(ctx.data_dir)
        rows = FollowsService(store, ConfigLeagues(), config_file=True).sync_tournaments()
    except StorageError as e:
        logger.warning("The follows table could not be read; the sync uses the leagues of the configuration: %s", e)
        return {int(lid): SyncTarget(int(lid), name) for lid, name in ctx.config.get_leagues().items()}
    return {row.entity_id: SyncTarget(row.entity_id, row.name, row.seasons) for row in rows}


@dataclass(frozen=True)
class RefreshCounts:
    """
    Yalnızca yenileme kipinin sayıları (MatchDataFetcher.refresh_matches'in döndürdükleri).

    due        yenilenmesi gereken kayıt (denenmeyenler dahil)
    refreshed  yeniden okunan kayıt
    changed    bunlardan SofaScore'da değişmiş olan (score_changes.jsonl'a yazılan)
    failed     /event'i alınamayan ya da yazılamayan kayıt
    skipped    devre kesildiği için hiç denenmeyen kayıt
    """

    due: int = 0
    refreshed: int = 0
    changed: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class FailedListing:
    """
    Çekilemeyen bir liste: sezon listesi (`kind` "seasons"), sezon programı ("schedule") ya da bir takım ya da
    oyuncu takibinin maç listesi ("team_events", "player_events"; FX-19: `league_id` o takımın ya da oyuncunun
    kimliğidir; iş kayıtlarının ve komut satırının biçimi değişmesin diye ayrı alan yok).

    reason  istek katmanının nedeni ("403", "429", "5xx", "timeout", "network", "parse", "other"), "not_found"
            (SofaScore'da böyle bir turnuva yok), "storage" (alındı ama yazılamadı)
    """

    kind: str
    league_id: int
    season_id: Optional[int]
    reason: str


@dataclass(frozen=True)
class SyncResult:
    """
    Bir eşitlemenin sonucu.

    state                    "cancelled": iptal edildi; "partial": devre kesici durdurdu ya da en az bir maç
                             indirilemedi / yenilenemedi; "succeeded": diğer her durum (02-services.md 2.8,
                             bitiş durumu kuralı)
    schedule_empty_seasons   maç listesi boş dönen sezon sayısı (tam kip; çekilemeyen sezon `failed_listings`tedir)
    breaker                  devre kesildiyse neden: "403" | "429" | "5xx" | "other"; yoksa None
    progress                 JobProgress.result(): detay sayaçları, başarısız maçlar, yenileme sayıları
    refresh                  yalnızca yenileme kipinde: o çalıştırmanın sayıları; diğer kiplerde ve yenileme
                             başlamadan iptal edildiyse None
    failed_listings          çekilemeyen sezon listeleri ve programlar (tam kip); varsa iş `partial` biter
    """

    state: SyncState
    schedule_empty_seasons: int
    breaker: Optional[str]
    progress: Mapping[str, Any]
    refresh: Optional[RefreshCounts] = None
    failed_listings: Tuple[FailedListing, ...] = ()


class JobHandle(Protocol):
    """
    Servisin gördüğü iş (02-services.md 2.8). Web bunu iş deposuna bağlar; JobManager (P11) kendi tutamacını verir.

    `progress`, bu işin `SyncSpec.job_phases` aşamalarıyla kurulmuş olmalıdır.
    """

    @property
    def id(self) -> str: ...

    @property
    def progress(self) -> JobProgress: ...

    def cancelled(self) -> bool:
        """İptal istendi mi. İstek bağlamına da kurulur: beklemeler ve yeniden denemeler de buna bakar."""
        ...

    def log(self, message: str) -> None:
        """İş günlüğüne ve kartın görev satırına bir satır yazar; yüzdeye dokunmaz."""
        ...

    def publish(self, fields: Mapping[str, Any]) -> None:
        """JobProgress'in taşımadığı iş alanlarını yazar (bugün yalnızca `schedule_empty_seasons`)."""
        ...


class DetachedHandle:
    """İş kaydı olmayan çalıştırma: ilerleme hiçbir yere yazılmaz, iptal edilemez."""

    id = ""

    def __init__(self, spec: SyncSpec) -> None:
        self.progress = JobProgress(list(spec.job_phases), lambda fields: None)

    def cancelled(self) -> bool:
        return False

    def log(self, message: str) -> None:
        logger.info("%s", message)

    def publish(self, fields: Mapping[str, Any]) -> None:
        pass


def _follows_of(spec: SyncSpec) -> Tuple[str, ...]:
    return tuple(getattr(spec, "follows", ()) or ())


def _accepts_fields(log: Callable[..., Any]) -> bool:
    """Tutamacın `log`u kod ve parametre alabiliyor mu (`log(message, **fields)`; iş yöneticisinin tutamacı)."""
    try:
        parameters = inspect.signature(log).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind is inspect.Parameter.VAR_KEYWORD or p.name == "code" for p in parameters)


def _status_counts_reason(status_counts: Optional[Mapping[str, int]]) -> str:
    """Devreyi kesen hatanın türü: en sık görülen 403 / 429 / 5xx."""
    relevant = Counter({k: v for k, v in (status_counts or {}).items() if k in ("403", "429", "5xx")})
    return relevant.most_common(1)[0][0] if relevant else "other"


class SyncService:
    """Sezon listeleri, maç listeleri, maç detayları ve CSV: tek bir iş olarak."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    def run(self, spec: SyncSpec, *, handle: Optional[JobHandle] = None) -> SyncResult:
        """
        Eşitlemeyi çağıranın thread'inde baştan sona çalıştırır.

        İşin istekleri (ve yeniden denemeler arasındaki beklemeler) blok boyunca tutamacın iptal sorusuna bakar,
        uzun beklemeleri ilerlemeye bildirir ve tek bir devre kesiciyi besler; çıkışta üçü de geri alınır.
        StorageError fırlatır; iptal bir sonuçtur ("cancelled"), hata değil.
        """
        job: JobHandle = handle if handle is not None else DetachedHandle(spec)
        # İşin tek devre kesicisi: tüm aşamaların istekleri (sezon, maç programı, detay, yenileme) aynı
        # sayaçları besler; açıldığında istek katmanı bu iş için yeni istek göndermez.
        breaker = request_breaker.CircuitBreaker.from_config(self._ctx.config)
        run = _SyncRun(self._ctx, spec, job, breaker)
        with request_context(cancel=job.cancelled, on_wait=job.progress.wait, breaker=breaker):
            try:
                return run.execute()
            except FetchCancelled:
                # İstek katmanı iptali bir isteğin ya da beklemenin ortasında gördü
                logger.info("Sync cancelled while a request was in flight. job_id=%s", job.id)
                return run.result(cancelled=True)


class _SyncRun:
    """Tek bir çalıştırmanın durumu: bağlam, tutamaç, kesici ve aşamalar arasında taşınan sayaçlar."""

    def __init__(
        self, ctx: ServiceContext, spec: SyncSpec, job: JobHandle, breaker: request_breaker.CircuitBreaker
    ) -> None:
        self.ctx = ctx
        self.spec = spec
        self.job = job
        self.tracker = job.progress
        self.breaker = breaker
        self.league_names: Dict[int, str] = {}
        self.targets: Dict[int, SyncTarget] = {}
        self.empty_schedule = 0
        self.failed_listings: List[FailedListing] = []
        self._coded_log = _accepts_fields(job.log)

    # --- yardımcılar ---------------------------------------------------------------------------------

    def lname(self, lid: Optional[int]) -> Optional[str]:
        return self.league_names.get(int(lid)) if lid is not None else None

    def log(self, message: str, code: str, **params: Any) -> None:
        """
        İş günlüğüne bir satır, koduyla (05-web-ui.md G24): istemci metni `code` ve `params`tan üretir; kodu
        taşıyamayan tutamaca (eski imza `log(message)`) yalnızca metin gider.
        """
        if self._coded_log:
            self.job.log(message, code=code, params=params)
        else:
            self.job.log(message)

    def report_breaker(self, reason: str, what: str) -> None:
        """Devre kesildiğini karta ve iş günlüğüne bir kez yazar."""
        if self.tracker.result().get("breaker"):
            return
        self.tracker.breaker(reason)
        self.log(f"Too many failed requests ({reason}); stopped fetching {what}.", "sync_breaker_stopped",
                 reason=reason, what=what)

    def blocked(self, what: str) -> bool:
        if not self.breaker.tripped:
            return False
        self.report_breaker(self.breaker.reason(), what)
        return True

    def details_breaker_reason(self) -> str:
        """İşin kesicisi açıldıysa onun nedeni; yoksa detay indiricinin kendi sayımı."""
        if self.breaker.tripped:
            return self.breaker.reason()
        return _status_counts_reason(self.ctx.match_data_fetcher.last_status_counts)

    def result(self, *, cancelled: bool, refresh: Optional[RefreshCounts] = None) -> SyncResult:
        progress = self.tracker.result()
        breaker = progress.get("breaker")
        state: SyncState
        if cancelled:
            state = "cancelled"
        elif (breaker or progress.get("failed_count") or (refresh is not None and refresh.failed)
              or self.failed_listings):
            state = "partial"
        else:
            state = "succeeded"
        return SyncResult(
            state=state,
            schedule_empty_seasons=self.empty_schedule,
            breaker=breaker,
            progress=progress,
            refresh=refresh,
            failed_listings=tuple(self.failed_listings),
        )

    def _listing(self, run: Callable[[], "listing.ListingResult"], kind: str, league_id: int,
                 season_id: Optional[int] = None) -> Optional["listing.ListingResult"]:
        """
        Bir liste biriminin çalıştırılması. Başarısız liste (ya da beklenmeyen hata) `failed_listings`'e ve iş
        günlüğüne yazılır; None döner. İptal ve kalıcı depolama hatası çağırana çıkar.
        """
        what = f"league {league_id}" + (f", season {season_id}" if season_id is not None else "")
        try:
            result: Optional[listing.ListingResult] = run()
            reason = result.reason if result is not None and result.failed else None
        except (FetchCancelled, KeyboardInterrupt):
            raise
        except StorageError as e:
            if e.fatal:
                raise
            logger.error("Storing the %s of %s failed: %s", "season list" if kind == "seasons" else "schedule",
                         what, e)
            result, reason = None, "storage"
        except Exception as e:
            logger.error("%s failed for %s: %s", "Season list" if kind == "seasons" else "Match list", what, e)
            result, reason = None, "other"
        if reason is None:
            return result
        self.failed_listings.append(FailedListing(kind, int(league_id), season_id, str(reason)))
        if kind == "seasons":
            self.log(f"The season list of league {league_id} could not be fetched ({reason}).",
                     "sync_season_list_failed", league_id=int(league_id), reason=str(reason))
        else:
            self.log(f"The match list of {what} could not be fetched completely ({reason}).",
                     "sync_schedule_failed", league_id=int(league_id), season_id=season_id, reason=str(reason))
        return None

    # --- akış ----------------------------------------------------------------------------------------

    def execute(self) -> SyncResult:
        spec, job, tracker = self.spec, self.job, self.tracker
        cancelled = job.cancelled
        if spec.mode == "refresh":
            return self._refresh()
        self.targets = sync_targets(self.ctx)
        self.league_names = {lid: target.name for lid, target in self.targets.items() if target.name}
        if spec.mode == "seasons":
            self._season_lists(self._leagues(), max_age=None)
            return self.result(cancelled=cancelled())

        detail_plan: DetailPlan = {}
        explicit_match_ids: List[int] = []

        others: Optional[Tuple[List["follow_sync.FollowListing"], List[Any]]] = None
        if spec.mode != "details":
            detail_plan = self._listings()
            # Takım, oyuncu ve maç takiplerinin listeleri (FX-19): maç listesi aşamasının sonunda
            others = self._follow_listings()
        elif spec.selections:
            for s in spec.selections:
                explicit_match_ids.extend(s.match_ids)
            explicit_match_ids = list(dict.fromkeys(explicit_match_ids))
        elif spec.league_id:
            detail_plan[spec.league_id] = None
        else:
            detail_plan[None] = None

        # 3. Maç detayları (devre önceki aşamalarda kesildiyse hiç başlamaz)
        if not cancelled() and not self.blocked("match details"):
            md = self.ctx.match_data_fetcher
            # Geçici kayıtların yenilenmesi kartta ayrı sayılır (JobProgress.detail()["refreshed"])
            md.refresh_listener = tracker.add_refreshed
            try:
                self._details(detail_plan, explicit_match_ids)
            finally:
                md.refresh_listener = None

        # 3b. Takım, oyuncu ve maç takiplerinin maçları (FX-19)
        if others is not None and not cancelled() and not self.blocked("match details"):
            self._follow_details(*others)

        # 4. Bahis oranları ve maç dışı veriler (plan maddesi P28): yalnızca seçildilerse; seçilmediyse hiçbir
        # şey okunmaz ve istenmez
        if detail_plan and not cancelled() and not self.blocked("match details"):
            self._extras(detail_plan)

        if cancelled():
            return self.result(cancelled=True)
        return self.result(cancelled=False)

    def _refresh(self) -> SyncResult:
        """
        Yalnızca yenileme: yenilenmesi gereken kayıtlı maçlar bulunur ve her biri için yalnızca /event istenir.

        Çağrı sırası `main.py --refresh-only`nin satır içi kodundan taşındı ve G-01 goldenıyla sabittir:
        begin_job_cache → refresh_due_ids → refresh_matches → end_job_cache. Devre kesilirse kalan maçlar
        denenmez (`RefreshCounts.skipped`); kalıcı depolama hatası StorageError olarak çağırana çıkar.
        """
        md = self.ctx.match_data_fetcher
        job, tracker = self.job, self.tracker
        md.refresh_listener = tracker.add_refreshed
        md.begin_job_cache()
        try:
            explicit = [str(mid) for s in self.spec.selections for mid in s.match_ids]
            ids = list(dict.fromkeys(explicit)) if explicit else md.refresh_due_ids(league_id=self.spec.league_id)
            tracker.start_phase("details", len(ids))
            self.log(f"Refreshing {len(ids)} provisional records...", "sync_refreshing", count=len(ids))
            stats = md.refresh_matches(
                ids,
                progress_callback=lambda done, _total, _msg: tracker.advance(done),
                should_cancel=job.cancelled,
            )
        finally:
            md.end_job_cache()
            md.refresh_listener = None
        if stats.get("breaker"):
            self.report_breaker(str(stats["breaker"]), "provisional records")
        counts = RefreshCounts(
            due=len(ids),
            refreshed=int(stats.get("refreshed", 0)),
            changed=int(stats.get("changed", 0)),
            failed=int(stats.get("failed", 0)),
            skipped=int(stats.get("skipped", 0)),
        )
        return self.result(cancelled=job.cancelled(), refresh=counts)

    def _leagues(self) -> List[int]:
        """
        Çalışmanın ligleri: seçimlerin, tek ligin, verilen takiplerin ya da (hiçbiri yoksa) takip edilen bütün
        turnuvaların. Takip edilmeyen bir takip kimliği atlanır ve iş günlüğüne yazılır.
        """
        spec = self.spec
        follows = _follows_of(spec)
        if follows:
            found: List[int] = []
            for follow in follows:
                kind, _, number = str(follow).partition(":")
                if kind in follow_sync.SYNCED_KINDS:
                    continue  # takım, oyuncu ve maç takipleri: `_other_follows` (FX-19)
                lid = int(number) if kind == "tournament" and number.isdigit() else None
                if lid is None or lid not in self.targets:
                    self.log(f"The follow {follow} is not an enabled tournament follow; skipped.",
                             "sync_follow_skipped", follow=str(follow))
                    continue
                if lid not in found:
                    found.append(lid)
            return sorted(found)
        if spec.selections:
            return sorted({s.league_id for s in spec.selections})
        if spec.league_id:
            return [spec.league_id]
        return sorted(self.targets)

    def _season_lists(self, leagues: Sequence[int], *, max_age: Optional[float]) -> None:
        """Sezon listeleri aşaması (tam kipin ilk aşaması; sezon kipinin tamamı)."""
        job, tracker, ctx = self.job, self.tracker, self.ctx
        tracker.start_phase("seasons", len(leagues))
        for i, lid in enumerate(leagues):
            if job.cancelled() or self.blocked("season lists"):
                break
            tracker.set_context(league_id=lid, league_name=self.lname(lid))
            self.log(f"Refreshing season list for league {lid}...", "sync_season_list", league_id=int(lid))
            self._listing(lambda _l=lid: ctx.season_fetcher.list_seasons(_l, max_age=max_age), "seasons", lid)
            tracker.advance(i + 1)

    def _followed_seasons(self, lid: int, names: Mapping[int, Optional[str]]) -> List[int]:
        """Bir takibin indirilecek sezonları (sezon listesine takibin seçimi uygulanır; `pick_seasons`)."""
        target = self.targets.get(int(lid))
        listed = list(names)
        if target is None or target.seasons == "all":
            return listed
        chosen = pick_seasons(listed, target.seasons)
        if isinstance(target.seasons, str):
            return chosen
        resolved: List[int] = []
        for sid in chosen:
            current = self.ctx.season_fetcher.resolve_season_id(lid, sid)
            if current not in resolved:
                resolved.append(current)
        return resolved

    def _listings(self) -> DetailPlan:
        """Tam kipin ilk iki aşaması: sezon listeleri ve maç listeleri. Detay aşamasının planını döndürür."""
        spec, job, tracker, ctx = self.spec, self.job, self.tracker, self.ctx
        cancelled = job.cancelled
        detail_plan: DetailPlan = {}

        unique_leagues = self._leagues()
        # Takibin sezon seçimi, turnuvalar takiplerden geldiğinde uygulanır; tek lig (`league_id`, ör. `ssc fetch
        # tournament`) bugünkü gibi listedeki her sezonu indirir
        follows = _follows_of(spec)
        by_follow = bool(follows) or (not spec.selections and not spec.league_id)

        # 1. Sezon listeleri
        self._season_lists(unique_leagues, max_age=listing.SEASON_LIST_TTL_SECONDS)

        # 2. Maç listeleri: hangi (lig, sezon) çiftleri
        steps: List[SeasonStep] = []
        seen: set = set()

        def season_names(lid: int) -> Dict[int, Optional[str]]:
            return {
                int(s["id"]): s.get("name") or s.get("year")
                for s in ctx.season_fetcher.get_seasons_for_league(lid)
                if s.get("id") is not None
            }

        if spec.selections and not follows:
            for s in spec.selections:
                names = season_names(s.league_id)
                for sid in s.season_ids:
                    resolved = ctx.season_fetcher.resolve_season_id(s.league_id, sid)
                    if resolved != sid:
                        self.log(f"Season {sid} outdated → using {resolved} for league {s.league_id}",
                                 "sync_season_outdated", season_id=sid, resolved=resolved, league_id=s.league_id)
                    if (s.league_id, resolved) not in seen:
                        seen.add((s.league_id, resolved))
                        steps.append((s.league_id, resolved, names.get(resolved)))
            for lid, sid, _ in steps:
                detail_plan.setdefault(lid, []).append(sid)  # type: ignore[union-attr]
        else:
            for lid in unique_leagues:
                names = season_names(lid)
                chosen = self._followed_seasons(lid, names) if by_follow else list(names)
                for sid in chosen:
                    steps.append((lid, sid, names.get(sid)))
            if follows:
                for lid in unique_leagues:
                    detail_plan[lid] = None
            elif spec.league_id:
                detail_plan[spec.league_id] = None
            else:
                detail_plan[None] = None

        if not cancelled():
            tracker.start_phase("matches", len(steps))
        for idx, (lid, sid, sname) in enumerate(steps):
            if cancelled() or self.blocked("match lists"):
                break
            tracker.set_context(league_id=lid, league_name=self.lname(lid), season_name=sname)
            self.log(f"Fetching matches: league {lid}, season {sid}", "sync_schedule", league_id=int(lid),
                     season_id=int(sid))
            result = self._listing(lambda _l=lid, _s=sid: ctx.match_fetcher.list_schedule(
                _l, _s, max_age=listing.SCHEDULE_TTL_SECONDS), "schedule", lid, sid)
            # Boş program: listelendi ama maç yok. Başarısız ya da devre kesici yüzünden yarım kalan program
            # "maç yok" sayılmaz
            if result is not None and (result.ok or result.fresh) and not result.has_matches:
                self.empty_schedule += 1
            tracker.advance(idx + 1)
        job.publish({"schedule_empty_seasons": self.empty_schedule})
        if self.blocked("match lists"):
            pass  # son sezonun istekleri devreyi kesti: "maç yok" değil, engellendik
        elif steps and self.empty_schedule >= len(steps):
            from sofascore_scraper.i18n import get_i18n

            self.log(get_i18n().t("fetch_zero_matches"), "fetch_zero_matches")
        return detail_plan

    # --- takım, oyuncu ve maç takipleri (FX-19) ------------------------------------------------------

    def _store(self) -> Any:
        """Bağlamın deposu; yoksa ya da açılamıyorsa None (uyarıyla)."""
        from sofascore_scraper.store import StoreError

        try:
            return getattr(self.ctx, "store", None)
        except StoreError as e:
            logger.warning("Team, player and match follows skipped: the data store could not be opened (%s)", e)
            return None

    def _other_follows(self) -> List[Any]:
        """
        Maçları indirilecek takım, oyuncu ve maç takipleri: tam kipte, belirtim lig ya da seçim vermiyorsa etkin
        olanların hepsi; `follows` verildiyse yalnızca adı verilenler (etkin olmayan ya da olmayan takip atlanır ve
        günlüğe yazılır).
        """
        from sofascore_scraper.services.follows import ConfigLeagues, FollowsService

        spec = self.spec
        named = [text for text in _follows_of(spec) if str(text).partition(":")[0] in follow_sync.SYNCED_KINDS]
        if spec.mode != "full" or (not named and (_follows_of(spec) or spec.selections or spec.league_id)):
            return []
        store = self._store()
        if store is None:
            return []
        try:
            rows = FollowsService(store, ConfigLeagues()).sync_others()
        except StorageError as e:
            if e.fatal:
                raise
            logger.warning("Team, player and match follows could not be read: %s", e)
            return []
        if not named:
            return rows
        by_id = {f"{row.kind}:{row.entity_id}": row for row in rows}
        chosen: List[Any] = []
        for text in dict.fromkeys(str(t) for t in named):
            row = by_id.get(text)
            if row is None:
                self.log(f"The follow {text} is not an enabled follow; skipped.", "sync_follow_skipped", follow=text)
                continue
            chosen.append(row)
        return chosen

    def _follow_listings(self) -> Optional[Tuple[List["follow_sync.FollowListing"], List[Any]]]:
        """
        Takım ve oyuncu takiplerinin maç listeleri (maç listesi aşamasının sonunda, aşamanın sayacına eklenerek);
        maç takipleri olduğu gibi döner. Takip yoksa None.
        """
        from sofascore_scraper.client import Client

        follows = self._other_follows()
        if not follows or self.job.cancelled():
            return None
        listed = [row for row in follows if row.kind in follow_sync.LISTED_KINDS]
        events = [row for row in follows if row.kind not in follow_sync.LISTED_KINDS]
        tracker = self.tracker
        if tracker.phase != "matches":
            tracker.start_phase("matches", 0)
        base = int(tracker.detail()["done"])
        tracker.set_total(int(tracker.detail()["total"]) + len(listed))
        client = Client()
        listings: List[follow_sync.FollowListing] = []
        for index, follow in enumerate(listed):
            if self.job.cancelled() or self.blocked("match lists"):
                break
            fid = f"{follow.kind}:{follow.entity_id}"
            tracker.set_context(league_name=follow.name)
            self.log(f"Fetching the match list of {fid} ({follow.name})...", "sync_follow_listing", follow=fid,
                     name=follow.name)
            found = follow_sync.list_follow(follow, client.get_sync, cancelled=self.job.cancelled)
            listings.append(found)
            if found.failed is not None:
                self.failed_listings.append(FailedListing(f"{follow.kind}_events", int(follow.entity_id), None,
                                                          found.failed))
                self.log(f"The match list of {fid} could not be fetched completely ({found.failed}).",
                         "sync_follow_listing_failed", follow=fid, name=follow.name, reason=found.failed)
            tracker.advance(base + index + 1)
        return listings, events

    def _follow_details(self, listings: List["follow_sync.FollowListing"], events: List[Any]) -> None:
        """Takiplerin maçları: plan (`follow_sync.plan_items`), sonra boru hattı; detay aşamasının sayacına eklenir."""
        from sofascore_scraper.services import planning
        from sofascore_scraper.services.pipeline import FAIL_STORAGE
        from sofascore_scraper.services.query import RefreshPolicy

        store = self._store()
        if store is None:
            return
        items, policy = follow_sync.plan_items(store, listings, events, planning.configured_policy(store),
                                               RefreshPolicy.current())
        if not items:
            return
        tracker = self.tracker
        if tracker.phase != "details":
            tracker.start_phase("details", 0)
        base = int(tracker.detail()["done"])
        tracker.set_total(int(tracker.detail()["total"]) + len(items))
        tracker.set_context()
        self.log(f"Fetching {len(items)} matches of team, player and match follows...", "sync_follow_details",
                 count=len(items))
        done = 0

        def on_result(result: Any) -> None:
            nonlocal done
            done += 1
            tracker.advance(base + done)
            if result.item.need == "refresh" and result.ok:
                tracker.add_refreshed(str(result.event_id), bool(result.changed))
            elif result.failed and (result.reason == FAIL_STORAGE or not self.breaker.tripped):
                tracker.add_failed(str(result.event_id), None)

        concurrency = getattr(self.ctx.config, "get_max_concurrent", lambda: 5)()
        summary = follow_sync.run_items(store, items, policy, concurrency=int(concurrency),
                                        cancelled=self.job.cancelled, on_result=on_result)
        if summary.breaker:
            self.report_breaker(summary.breaker, "match details")

    def _extras(self, detail_plan: DetailPlan) -> None:
        """
        P28 aşaması (sofascore_scraper/services/pipeline.py `run_extras`): planın liglerindeki başlamamış maçların oranları ve
        sezonların, takımların, oyuncuların ve sporların seçilen dilimleri. Lig sezonsuz verildiyse en yeni sezonu.
        """
        from sofascore_scraper.services import planning
        from sofascore_scraper.services.pipeline import run_extras

        from sofascore_scraper.store import StoreError

        try:
            store = getattr(self.ctx, "store", None)
        except StoreError as e:
            logger.warning("Odds and non-match data skipped: the data store could not be opened (%s)", e)
            return
        if store is None:
            return
        policy = planning.configured_policy(store)
        if not planning.extras_selected(policy):
            return
        leagues = sorted(self.league_names) if None in detail_plan else []
        plan = {**{lid: None for lid in leagues}, **{lid: sids for lid, sids in detail_plan.items() if lid is not None}}
        seasons: List[Tuple[int, int]] = []
        for lid, sids in plan.items():
            chosen = list(sids) if sids else [row.id for row in store.entities.seasons(int(lid))[:1]]
            seasons.extend((int(lid), int(sid)) for sid in chosen)
        summary = run_extras(store, seasons=seasons, tournament_ids=tuple(int(lid) for lid in plan),
                             cancelled=self.job.cancelled, concurrency=self.ctx.config.get_max_concurrent(),
                             selection=policy)
        if summary is not None and summary.total:
            self.log(f"Odds and non-match data: {summary.ok} stored, {summary.failed} failed.", "sync_extras",
                     stored=int(summary.ok), failed=int(summary.failed))
            if summary.breaker:
                self.report_breaker(summary.breaker, "odds and non-match data")

    def _details(self, detail_plan: DetailPlan, explicit_match_ids: List[int]) -> None:
        """Detay aşaması: önce tüm liglerde eksik maçları sayar, sonra tek bir sayaçla indirir."""
        md = self.ctx.match_data_fetcher
        job, tracker = self.job, self.tracker
        cancelled = job.cancelled
        tracker.start_phase("details", 0)

        if explicit_match_ids:
            league_of = {int(m): s.league_id for s in self.spec.selections for m in s.match_ids}
            leagues = set(league_of.values())
            lid = next(iter(leagues)) if len(leagues) == 1 else None
            if lid is not None:
                tracker.set_context(league_id=lid, league_name=self.lname(lid))
            self.log(f"Fetching details for {len(explicit_match_ids)} selected matches...", "sync_details_selected",
                     count=len(explicit_match_ids))

            def cb(done: int, total: int, _msg: str) -> None:
                if total != tracker.detail()["total"]:
                    tracker.set_total(total)
                tracker.advance(done)

            md.fetch_matches_batch(
                explicit_match_ids,
                progress_callback=cb,
                should_cancel=cancelled,
                failed_callback=lambda mid: tracker.add_failed(mid, league_of.get(int(mid))),
            )
            if getattr(md, "rate_limit_breaker_triggered", False):
                self.report_breaker(self.details_breaker_reason(), "match details")
            return

        md.begin_job_cache()
        try:
            self.log("Checking which matches need details...", "sync_details_checking")
            work: List[Tuple[Optional[int], List[str]]] = []
            for lid, only_sids in detail_plan.items():
                if cancelled():
                    return
                ids = md.collect_detail_match_ids(
                    league_id=str(lid) if lid is not None else None,
                    max_seasons=0,
                    only_season_ids=only_sids,
                ) or []
                pending = md.pending_detail_ids(ids)
                if pending:
                    work.append((lid, pending))
            tracker.set_total(sum(len(p) for _, p in work))

            offset = 0
            for lid, pending in work:
                if cancelled():
                    break
                tracker.set_context(league_id=lid, league_name=self.lname(lid))
                self.log(
                    f"Fetching match details: league {lid if lid is not None else 'all'} ({len(pending)} matches)…",
                    "sync_details", league_id=lid, count=len(pending),
                )
                md.fetch_detail_ids(
                    pending,
                    progress_callback=lambda done, _t, _m, _o=offset: tracker.advance(_o + done),
                    should_cancel=cancelled,
                    failed_callback=lambda mid, _l=lid: tracker.add_failed(mid, _l),
                )
                offset += len(pending)
                if md.rate_limit_breaker_triggered:
                    self.report_breaker(self.details_breaker_reason(), "match details")
                    break
        finally:
            md.end_job_cache()
