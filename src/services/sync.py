"""
Eşitleme servisi: seçilen ligleri, sezonları ve maçları indirir (docs/design/02-services.md 2.7).

Bugünkü web işinin akışını taşır (src/web/fetch_job.py'den buraya geldi): sezon listeleri → maç listeleri →
maç detayları. Akış modül değişkenleri yerine bir iş tutamacıyla (JobHandle) konuşur: iptal sorusu,
ilerleme (JobProgress), iş günlüğü satırı. Tutamaç verilmezse iş kaydı olmadan çalışır.

Komut satırı da aynı servisi çağırır (main.py: `--headless --update-all` ve `--refresh-only`); yalnızca yenileme
ayrı bir kiptir (`mode="refresh"`: kayıtlı geçici maçların /event'i yeniden okunur, başka istek atılmaz).

Maç detayları (detay aşaması, kimliğiyle seçilen maçlar, yalnızca yenileme) tek getirme boru hattıyla indirilir
(src/services/pipeline.py, plan maddesi P13). Servis ona bağlamdaki MatchDataFetcher'ın eski adlı giriş noktalarıyla
(`fetch_detail_ids`, `fetch_matches_batch`, `refresh_matches`) ulaşır: bunlar yalnızca iş birimlerini kurar; çekici
P15'te kalktığında çağrılar buraya taşınır. Bitmemiş maç atlanır ve başarısız sayılmaz.

İşin sonunda CSV yazılmaz (karar D9, plan maddesi EX-1): dışa aktarma istendiğinde üretilir
(src/services/export.py; web'de `GET /api/export/csv`, komut satırında `--csv-export`).

İşin tek bir devre kesicisi vardır (src/breaker.py). İstek katmanı her isteğin sonucunu ona bildirir; her aşama
döngüsünde ona bakar. SofaScore engellediğinde kalan lig/sezon/maç için istek atılmaz ve neden iş kartına
yazılır. Servis SofaScore kaynaklı hiçbir durumda fırlatmaz; kalıcı depolama hatası (StorageError: disk dolu,
izin yok) çağırana çıkar.

Servis yazdırmaz (print) ve işin bitiş durumunu kendisi yazmaz: sonucu SyncResult olarak döndürür, onu iş
kaydına ve kullanıcı metnine çeviren çağıran yüzdür (web: src/web/fetch_job.py).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Protocol, Tuple

from src import breaker as request_breaker
from src.client.context import FetchCancelled, request_context
from src.jobs.progress import JobProgress
from src.logger import get_logger
from src.services.context import ServiceContext

logger = get_logger("SyncService")

SyncMode = Literal["full", "details", "refresh"]
SyncState = Literal["succeeded", "partial", "cancelled"]

# JobProgress aşamaları, çalıştıkları sırayla
FULL_PHASES: Tuple[str, ...] = ("seasons", "matches", "details")
DETAILS_PHASES: Tuple[str, ...] = ("details",)
# Yalnızca yenileme: kayıtlı maçlar yeniden okunur; ilerleme detay aşamasının sayacıyla gösterilir
REFRESH_PHASES: Tuple[str, ...] = ("details",)

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
                "refresh": yalnızca kayıtlı geçici maçların yenilenmesi (`selections` okunmaz)
    league_id   tek lig; yoksa yapılandırılmış bütün ligler. `selections` varsa okunmaz.
    selections  hedefli seçimler; boşsa `league_id` geçerlidir
    export      okunmaz. CSV aşaması kalktı (EX-1); alan, onu veren çağıranlar ve iş kayıtlarında saklanmış
                belirtimler geçerli kalsın diye duruyor ve belirtim `targets`/`phases`'e geçerken (P13) kalkar.
    """

    mode: SyncMode = "full"
    league_id: Optional[int] = None
    selections: Tuple[SyncSelection, ...] = ()
    export: bool = True

    @property
    def job_phases(self) -> Tuple[str, ...]:
        """Bu işin geçeceği JobProgress aşamaları (tutamacın ilerleme nesnesi bunlarla kurulur)."""
        if self.mode == "refresh":
            return REFRESH_PHASES
        return DETAILS_PHASES if self.mode == "details" else FULL_PHASES


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
class SyncResult:
    """
    Bir eşitlemenin sonucu.

    state                    "cancelled": iptal edildi; "partial": devre kesici durdurdu ya da en az bir maç
                             indirilemedi / yenilenemedi; "succeeded": diğer her durum (02-services.md 2.8,
                             bitiş durumu kuralı)
    schedule_empty_seasons   maç listesi boş dönen ya da çekilemeyen sezon sayısı (tam kip)
    breaker                  devre kesildiyse neden: "403" | "429" | "5xx" | "other"; yoksa None
    progress                 JobProgress.result(): detay sayaçları, başarısız maçlar, yenileme sayıları
    refresh                  yalnızca yenileme kipinde: o çalıştırmanın sayıları; diğer kiplerde ve yenileme
                             başlamadan iptal edildiyse None
    """

    state: SyncState
    schedule_empty_seasons: int
    breaker: Optional[str]
    progress: Mapping[str, Any]
    refresh: Optional[RefreshCounts] = None


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
        self.empty_schedule = 0

    # --- yardımcılar ---------------------------------------------------------------------------------

    def lname(self, lid: Optional[int]) -> Optional[str]:
        return self.league_names.get(int(lid)) if lid is not None else None

    def report_breaker(self, reason: str, what: str) -> None:
        """Devre kesildiğini karta ve iş günlüğüne bir kez yazar."""
        if self.tracker.result().get("breaker"):
            return
        self.tracker.breaker(reason)
        self.job.log(f"Too many failed requests ({reason}); stopped fetching {what}.")

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
        elif breaker or progress.get("failed_count") or (refresh is not None and refresh.failed):
            state = "partial"
        else:
            state = "succeeded"
        return SyncResult(
            state=state,
            schedule_empty_seasons=self.empty_schedule,
            breaker=breaker,
            progress=progress,
            refresh=refresh,
        )

    # --- akış ----------------------------------------------------------------------------------------

    def execute(self) -> SyncResult:
        spec, job, tracker = self.spec, self.job, self.tracker
        cancelled = job.cancelled
        if spec.mode == "refresh":
            return self._refresh()
        self.league_names = self.ctx.config.get_leagues()

        detail_plan: DetailPlan = {}
        explicit_match_ids: List[int] = []

        if spec.mode != "details":
            detail_plan = self._listings()
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
            ids = md.refresh_due_ids(league_id=self.spec.league_id)
            tracker.start_phase("details", len(ids))
            job.log(f"Refreshing {len(ids)} provisional records...")
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

    def _listings(self) -> DetailPlan:
        """Tam kipin ilk iki aşaması: sezon listeleri ve maç listeleri. Detay aşamasının planını döndürür."""
        spec, job, tracker, ctx = self.spec, self.job, self.tracker, self.ctx
        cancelled = job.cancelled
        detail_plan: DetailPlan = {}

        if spec.selections:
            unique_leagues = sorted({s.league_id for s in spec.selections})
        elif spec.league_id:
            unique_leagues = [spec.league_id]
        else:
            unique_leagues = sorted(self.league_names)

        # 1. Sezon listeleri
        tracker.start_phase("seasons", len(unique_leagues))
        for i, lid in enumerate(unique_leagues):
            if cancelled() or self.blocked("season lists"):
                break
            tracker.set_context(league_id=lid, league_name=self.lname(lid))
            job.log(f"Refreshing season list for league {lid}...")
            try:
                ctx.season_fetcher.fetch_seasons_for_league(lid)
            except Exception as e:
                logger.error("Season list failed for league %s: %s", lid, e)
            tracker.advance(i + 1)

        # 2. Maç listeleri: hangi (lig, sezon) çiftleri
        steps: List[SeasonStep] = []
        seen: set = set()

        def season_names(lid: int) -> Dict[int, Optional[str]]:
            return {
                int(s["id"]): s.get("name") or s.get("year")
                for s in ctx.season_fetcher.get_seasons_for_league(lid)
                if s.get("id") is not None
            }

        if spec.selections:
            for s in spec.selections:
                names = season_names(s.league_id)
                for sid in s.season_ids:
                    resolved = ctx.season_fetcher.resolve_season_id(s.league_id, sid)
                    if resolved != sid:
                        job.log(f"Season {sid} outdated → using {resolved} for league {s.league_id}")
                    if (s.league_id, resolved) not in seen:
                        seen.add((s.league_id, resolved))
                        steps.append((s.league_id, resolved, names.get(resolved)))
            for lid, sid, _ in steps:
                detail_plan.setdefault(lid, []).append(sid)  # type: ignore[union-attr]
        else:
            for lid in unique_leagues:
                for sid, name in season_names(lid).items():
                    steps.append((lid, sid, name))
            if spec.league_id:
                detail_plan[spec.league_id] = None
            else:
                detail_plan[None] = None

        if not cancelled():
            tracker.start_phase("matches", len(steps))
        for idx, (lid, sid, sname) in enumerate(steps):
            if cancelled() or self.blocked("match lists"):
                break
            tracker.set_context(league_id=lid, league_name=self.lname(lid), season_name=sname)
            job.log(f"Fetching matches: league {lid}, season {sid}")
            try:
                ok = ctx.match_fetcher.fetch_matches_for_season(lid, sid)
            except Exception as e:
                logger.error("Match list failed for league %s season %s: %s", lid, sid, e)
                ok = False
            if not ok:
                self.empty_schedule += 1
            tracker.advance(idx + 1)
        job.publish({"schedule_empty_seasons": self.empty_schedule})
        if self.blocked("match lists"):
            pass  # son sezonun istekleri devreyi kesti: "maç yok" değil, engellendik
        elif steps and self.empty_schedule >= len(steps):
            from src.i18n import get_i18n

            job.log(get_i18n().t("fetch_zero_matches"))
        return detail_plan

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
            job.log(f"Fetching details for {len(explicit_match_ids)} selected matches...")

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
            job.log("Checking which matches need details...")
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
                job.log(
                    f"Fetching match details: league {lid if lid is not None else 'all'} ({len(pending)} matches)…"
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
