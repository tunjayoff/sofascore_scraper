"""
API v1: işler (docs/design/02-services.md bölüm 6 ve 2.8).

    GET  /api/v1/jobs                  iş geçmişi (en yeni önce), `state`, `kind`, `origin` ve `target` süzgeçleri,
                                       imleçle sayfalama
    POST /api/v1/jobs                  iş başlatır: {"kind": "sync" | "fetch" | "refresh" | "export" | "backup" |
                                       "clear" | "rebuild" | "restore", "spec": {...}}
    GET  /api/v1/jobs/{id}             tek iş
    POST /api/v1/jobs/{id}/cancel      iptal ister (iş hangi süreçte çalışırsa çalışsın)
    GET  /api/v1/jobs/{id}/events      işin olayları, SSE (src/web/sse.py)

İşler iş yöneticisinden (src/jobs/manager.py) okunur: komut satırından başlatılan işler de burada görünür ve
buradan iptal edilebilir. Yanıttaki iş, iş modelinin (src/jobs/model.py: Job) alanlarını taşır; durum adları
yeni modelinkilerdir (`succeeded`, `partial`, ...).

Başlatma: iş, web sürecinin iş deposu üzerinde (eski `/api/fetch` ile aynı depo, aynı `writer` kilidi) ayrı bir
thread'de çalışır. Belirtim (`spec`) bugünkü eşitleme servisinin belirtimidir (src/services/sync.py: SyncSpec):

    sync     sezon listeleri → maç listeleri → maç detayları           (SyncSpec mode="full")
             `only: "seasons"`: yalnızca sezon listeleri               (SyncSpec mode="seasons"; FX-13, G15)
             `follows`: yalnızca adı verilen takipler; takım, oyuncu ve  (FollowsSyncSpec; FX-13, G23;
             maç takipleri maçlarını indirir                            FX-19)
    fetch    yalnızca maç detayları                                     (SyncSpec mode="details")
             `event_ids`: turnuvası bilinmese de bu maçlar              (FX-13, G16; `ssc fetch event` gibi)
    refresh  kayıtlı geçici maçların yeniden okunması                   (SyncSpec mode="refresh")
             `event_ids`: yalnızca bu maçların /event'i                  (FX-13, G23)

Üçü de CSV yazmaz: dışa aktarma kendi iş türüdür (`export`). Veri işleri (P21):

    export   `exports/<iş>.<uzantı>`: normalleştirilmiş veri kümeleri (events, slices, changes; JSONL, CSV,
             Parquet, SQLite), 2.x'in geniş CSV'si ya da ham yükler (src/services/data_jobs.py)
    backup   `backups/` altına yedek (BackupService)
    clear    saklanan verinin bir kısmını siler (MaintenanceService.clear); `confirm: true` ister
    rebuild  kataloğu dosyalardan yeniden kurar (MaintenanceService.rebuild_catalog)
    restore  `dry_run: true` (varsayılan) denetler; `dry_run: false` geri yükler (FX-13, G2): `maintenance`
             kilidiyle; geri yüklenen state.db'nin iş geçmişine işin kendi satırı korunarak taşınır
             (src/store/backup.py `_load_state`), iş bitişini yine kendi satırına yazar

İndirmeler, dışa aktarma, yedek ve deneme `writer` kilidiyle; temizleme, katalog ve geri yükleme `maintenance` kilidiyle
çalışır
(docs/design/02-services.md 2.8). Belirtim, alma hattı hedeflere ve aşamalara geçtiğinde değişecektir;
değişiklik OpenAPI kaydında görünür.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Literal, Mapping, Optional, Tuple, Union

from fastapi import APIRouter, Body, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from src.errors import NotFoundError, UsageError
from src.jobs.model import Job as JobSnapshot
from src.jobs.model import JobKind, JobState
from src.web import deps, sse
from src.web.api import V1_PREFIX
from src.web.api.v1 import PageInfo
from src.web.errors import ValidationFailed, error_responses

if TYPE_CHECKING:
    from src.jobs.manager import JobHandle, JobOutcome
    from src.services.data_jobs import ExportRequest
    from src.services.sync import SyncSpec

logger = logging.getLogger("WebAPI")
router = APIRouter(tags=["jobs"])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
# İş geçmişi en çok bu kadar satır tutar (src/store/jobs.py: JOB_HISTORY_LIMIT); sayfalama onun üzerinde yapılır
HISTORY_LIMIT = 500


# --- modeller ------------------------------------------------------------------------------------


class JobOrigin(BaseModel):
    """Who started the job."""

    face: Literal["cli", "api", "scheduler", "library"]
    pid: Optional[int] = None
    host: Optional[str] = None


class JobError(BaseModel):
    """The error that stopped the job, or made it partial."""

    code: str = Field(description="A code of the error table, e.g. blocked, rate_limited, storage_error.")
    message: str
    details: Optional[Dict[str, Any]] = None


class Job(BaseModel):
    """A job: a download, a refresh or a data operation, started by any face in any process."""

    id: str
    kind: JobKind
    state: JobState
    origin: JobOrigin
    spec: Dict[str, Any] = Field(description="The service spec the job was started with.")
    progress: Optional[Dict[str, Any]] = Field(
        default=None, description="Phase, counters and failed items; the last progress event for a job of another process.",
    )
    result: Optional[Dict[str, Any]] = None
    error: Optional[JobError] = None
    created_at: Optional[str] = Field(default=None, description="ISO-8601, UTC.")
    started_at: Optional[str] = Field(default=None, description="ISO-8601, UTC.")
    finished_at: Optional[str] = Field(default=None, description="ISO-8601, UTC.")
    heartbeat_at: Optional[int] = Field(default=None, description="Epoch milliseconds; written every 5 s while running.")
    cancel_requested: bool = False


class JobResponse(BaseModel):
    data: Job


class JobListResponse(BaseModel):
    data: List[Job]
    page: PageInfo


class JobSelection(BaseModel):
    """One followed tournament and, optionally, the seasons or events of it to work on."""

    model_config = ConfigDict(extra="forbid")

    league_id: int = Field(gt=0)
    season_ids: List[int] = Field(default_factory=list, description="Read by `sync` only.")
    match_ids: List[int] = Field(default_factory=list, description="Read by `fetch` only.")


FollowId = Annotated[str, Field(pattern=r"^(tournament|team|player|event):[1-9][0-9]{0,18}$")]


class SyncJobSpec(BaseModel):
    """
    What to download. Without `selections`, `league_id` and `follows`: every enabled follow, each tournament with
    its own season choice, each team, player and event follow with its matches. Only one of `league_id`,
    `selections` and `follows` may be given.
    """

    model_config = ConfigDict(extra="forbid")

    league_id: Optional[int] = Field(default=None, gt=0, description="One tournament, every season of it.")
    selections: List[JobSelection] = Field(default_factory=list)
    follows: List[FollowId] = Field(
        default_factory=list, max_length=200,
        description="`sync` only: these follows (`tournament:17`, `team:42`, `player:7`, `event:123`). A "
                    "tournament with its season choice; a team or a player with its matches (the previous and next "
                    "pages of SofaScore's list within the follow's window: `seasons` `current` = the last 365 "
                    "days, `last:N` = N × 365 days, `all` = up to five pages back, season ids = those seasons); an "
                    "event follow that one match. Season lists only (`only: \"seasons\"`) read tournaments only.",
    )
    only: Optional[Literal["seasons"]] = Field(
        default=None,
        description="`sync` only. `seasons`: read the season lists from SofaScore again now, without schedules or "
                    "event details (the Get season list button).",
    )
    event_ids: List[Annotated[int, Field(gt=0)]] = Field(
        default_factory=list, max_length=500,
        description="`fetch` only: these events, whether or not their tournament is known or followed. Not with "
                    "`league_id` or `selections`.",
    )


class RefreshJobSpec(BaseModel):
    """Without `event_ids`: the stored records that are due (of one tournament with `league_id`)."""

    model_config = ConfigDict(extra="forbid")

    league_id: Optional[int] = Field(default=None, gt=0)
    event_ids: List[Annotated[int, Field(gt=0)]] = Field(
        default_factory=list, max_length=500,
        description="Read `/event` of these events again, whether or not they are due (the Fetch again button). "
                    "Not with `league_id`.",
    )


class StartSyncJob(BaseModel):
    """Season lists, schedules and event details."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["sync"]
    spec: SyncJobSpec = Field(default_factory=SyncJobSpec)


class StartFetchJob(BaseModel):
    """Event details only, for the schedules that are already stored."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fetch"]
    spec: SyncJobSpec = Field(default_factory=SyncJobSpec)


class StartRefreshJob(BaseModel):
    """Re-read the stored events that may still change."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["refresh"]
    spec: RefreshJobSpec = Field(default_factory=RefreshJobSpec)


class ExportFilter(BaseModel):
    """
    Which records to export; the fields are combined with AND, an empty field filters nothing. The meaning is that
    of the filters of `GET /events` (and of `GET /changes` for the changes dataset).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sport: Optional[str] = Field(default=None, max_length=40)
    tournament_ids: List[int] = Field(default_factory=list)
    season_ids: List[int] = Field(default_factory=list,
                                  description="Not for the legacy-wide-csv profile or the changes dataset.")
    event_ids: List[int] = Field(default_factory=list)
    status_classes: List[Literal["not_started", "live", "completed", "decided_without_play", "void", "unknown"]] = Field(
        default_factory=list, description="Only events in these status classes. Not for the legacy-wide-csv profile "
                                          "or the changes dataset.")
    from_: Optional[str] = Field(
        default=None, alias="from", max_length=40,
        description="At or after; ISO 8601 date or date-time (UTC without an offset). The start of the event; for "
                    "the changes dataset the time the change was recorded. Not for the legacy-wide-csv profile.")
    to: Optional[str] = Field(default=None, max_length=40,
                              description="At or before, as `from`; a date includes the whole day.")


class ExportJobSpec(BaseModel):
    """
    What to export. Schema `normalized`: the records of data schema v1 (`events`, `slices`, `changes`, `odds`: one
    row per outcome of each odds snapshot, `standings`: the standings rows of the matched events' seasons) as JSONL
    (one record per line), CSV, Parquet or SQLite (one column per leaf field, named by its path joined with `_`;
    lists as JSON text). Parquet needs the optional package pyarrow on the server (501 `not_supported` without
    it). Schema `raw`: the stored payloads as JSONL (dataset `events`: the event payload only, `slices`: every
    slice). The profile `legacy-wide-csv` is 2.x's wide CSV (dataset `events`, format `csv`).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    dataset: Literal["events", "slices", "changes", "odds", "standings"] = "events"
    format: Literal["csv", "jsonl", "parquet", "sqlite"] = "csv"
    schema_: Literal["normalized", "raw"] = Field(default="normalized", alias="schema")
    profile: Optional[Literal["legacy-wide-csv"]] = None
    filter: ExportFilter = Field(default_factory=ExportFilter)


class StartExportJob(BaseModel):
    """Write an export file into the data directory's `exports/`; download it through `/exports/{id}/download`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["export"]
    spec: ExportJobSpec = Field(default_factory=ExportJobSpec)


class BackupJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["all", "state", "data", "config", "seasons", "matches", "match_details"] = "all"
    include_env: bool = Field(default=False, description="Also `.env` (it can hold secrets); the file name says so.")


class StartBackupJob(BaseModel):
    """Write a backup zip into the data directory's `backups/`; download it through `/backups/{name}`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["backup"]
    spec: BackupJobSpec = Field(default_factory=BackupJobSpec)


class ClearJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["all", "events", "schedules", "seasons", "match_details", "matches"] = "all"
    confirm: bool = Field(default=False, description="Must be true: the stored data of the scope is deleted.")


class StartClearJob(BaseModel):
    """Delete stored data (follows, job history, change log, backups and exports stay)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["clear"]
    spec: ClearJobSpec = Field(default_factory=ClearJobSpec)


class RebuildJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "in_place", "recreate"] = "auto"


class StartRebuildJob(BaseModel):
    """Rebuild the catalog (`.meta/catalog.db`) from the stored files."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["rebuild"]
    spec: RebuildJobSpec = Field(default_factory=RebuildJobSpec)


class RestoreJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200, description="A backup of `/backups`.")
    force: bool = Field(
        default=False,
        description="Move the current data to the trash first (`.meta/trash/`); needed when the data directory is "
                    "not empty. With `dry_run`: report what that would move.",
    )
    dry_run: bool = Field(
        default=True, description="True: only check (nothing is written). False: restore the backup.",
    )


class StartRestoreJob(BaseModel):
    """
    Check what restoring a backup would do (`dry_run: true`, the Check step of the UI), or restore it
    (`dry_run: false`, the Choose step). A restore replaces the data and the job history; this job stays in it.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["restore"]
    spec: RestoreJobSpec


StartJob = Union[
    StartSyncJob, StartFetchJob, StartRefreshJob, StartExportJob, StartBackupJob, StartClearJob, StartRebuildJob,
    StartRestoreJob,
]
# Bakım işleri `maintenance` kilidiyle çalışır (docs/design/02-services.md 2.8): başka her işi ve veri işlemini dışlar
MAINTENANCE_LEASE = "maintenance"


def job_model(job: JobSnapshot) -> Job:
    """İş modelinin görüntüsü → yanıt modeli."""
    error = job.error
    return Job(
        id=job.id,
        kind=job.kind,
        state=job.state,
        origin=JobOrigin(face=job.origin.face, pid=job.origin.pid, host=job.origin.host),
        spec=dict(job.spec),
        progress=dict(job.progress) if job.progress is not None else None,
        result=dict(job.result) if job.result is not None else None,
        error=None if error is None else JobError(
            code=error.code, message=error.message, details=dict(error.details) if error.details else None,
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        heartbeat_at=job.heartbeat_at,
        cancel_requested=job.cancel_requested,
    )


def job_targets(spec: Mapping[str, Any]) -> List[str]:
    """
    Bir iş belirtiminin adını verdiği turnuvalar ve maçlar (`tournament:17`, `event:123`): `league_id`, seçimlerin
    ligleri ve maçları, takipler, `event_ids`. Bütün takipleri ya da bütün kataloğu kapsayan iş hiçbirini vermez.
    """
    found: Dict[str, None] = {}

    def add(kind: str, value: Any) -> None:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            found[f"{kind}:{value}"] = None

    add("tournament", spec.get("league_id"))
    for selection in spec.get("selections") or ():
        if isinstance(selection, Mapping):
            add("tournament", selection.get("league_id"))
            for event_id in selection.get("match_ids") or ():
                add("event", event_id)
    for follow in spec.get("follows") or ():
        kind, _, number = str(follow).partition(":")
        if kind in ("tournament", "team", "player", "event") and number.isdigit():
            add(kind, int(number))
    for event_id in spec.get("event_ids") or ():
        add("event", event_id)
    return list(found)


def _existing(job_id: str) -> JobSnapshot:
    job = deps.job_manager().get(job_id)
    if job is None:
        raise NotFoundError("No job has this id.", {"job_id": job_id})
    return job


# --- okuma ---------------------------------------------------------------------------------------


@router.get(
    "/jobs",
    response_model=JobListResponse,
    operation_id="listJobs",
    summary="List jobs",
    responses=error_responses("invalid_request"),
)
def list_jobs(
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None, max_length=64, description="`page.next_cursor` of the previous page."),
    state: Annotated[Optional[List[JobState]], Query(description="Only jobs in one of these states.")] = None,
    kind: Annotated[Optional[List[JobKind]], Query(description="Only jobs of one of these kinds.")] = None,
    origin: Annotated[Optional[List[Literal["cli", "api", "scheduler", "library"]]], Query(
        description="Only jobs started by one of these faces.")] = None,
    target: Optional[str] = Query(
        None, pattern=r"^(tournament|team|player|event):[1-9][0-9]{0,18}$",
        description="Only jobs whose spec names this tournament (`tournament:17`: its `league_id`, a selection or "
                    "a follow), this team or player (`team:42`, `player:7`: a follow) or this event (`event:123`: "
                    "a selection's, a follow's or `event_ids`' event). A job over every follow names none.",
    ),
) -> JobListResponse:
    """Jobs of the data directory, newest first: those of this server and those started from the command line."""
    jobs = deps.job_manager().list(limit=HISTORY_LIMIT, kinds=kind or None, states=state or None)
    if origin:
        faces = set(origin)
        jobs = [job for job in jobs if job.origin.face in faces]
    if target:
        jobs = [job for job in jobs if target in job_targets(job.spec)]
    start = 0
    if cursor:
        # İmleç, önceki sayfanın son işinin kimliğidir
        position = next((i for i, job in enumerate(jobs) if job.id == cursor), None)
        if position is None:
            raise UsageError("The cursor does not name a job of this list.", {"cursor": cursor})
        start = position + 1
    page = jobs[start:start + limit]
    more = start + limit < len(jobs)
    return JobListResponse(
        data=[job_model(job) for job in page],
        page=PageInfo(limit=limit, next_cursor=page[-1].id if more and page else None),
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    operation_id="getJob",
    summary="Get a job",
    responses=error_responses("not_found"),
)
def get_job(job_id: str) -> JobResponse:
    """One job, whichever process runs or ran it."""
    return JobResponse(data=job_model(_existing(job_id)))


# --- iptal ---------------------------------------------------------------------------------------


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobResponse,
    operation_id="cancelJob",
    summary="Cancel a job",
    responses=error_responses("not_found", "forbidden_origin"),
)
def cancel_job(job_id: str) -> JobResponse:
    """
    Ask a running job to stop. The process that runs it sees the request within a second; the job then ends
    as `cancelled`. A job that has already ended is returned unchanged.
    """
    _existing(job_id)
    deps.job_manager().cancel(job_id)
    deps.refresh_job_mirror()
    return JobResponse(data=job_model(_existing(job_id)))


# --- başlatma ------------------------------------------------------------------------------------


def _check_targets(body: Union[StartSyncJob, StartFetchJob]) -> None:
    """Belirtimin hedef alanlarının birlikte kullanımı; uymayan istek iş kaydı bırakmadan `invalid_request`tir."""
    spec = body.spec
    given = [name for name, value in (("league_id", spec.league_id), ("selections", spec.selections),
                                      ("follows", spec.follows), ("event_ids", spec.event_ids)) if value]
    if len(given) > 1:
        raise UsageError("Give only one of league_id, selections, follows and event_ids.", {"fields": given})
    wrong = [name for name, value, allowed in (("follows", spec.follows, StartSyncJob),
                                               ("only", spec.only, StartSyncJob),
                                               ("event_ids", spec.event_ids, StartFetchJob))
             if value and not isinstance(body, allowed)]
    if wrong:
        raise UsageError(f"These fields are not read by a {body.kind} job.", {"fields": wrong, "kind": body.kind})
    if spec.only == "seasons" and spec.selections:
        raise UsageError("A season-list job reads whole season lists; give league_id or follows.",
                         {"fields": ["selections", "only"]})
    if spec.follows:
        _check_follows(spec.follows)


def _check_follows(follows: List[str]) -> None:
    """Takipler var olmalı. Her tür eşitlenir (takım, oyuncu ve maç takipleri FX-19'dan beri)."""
    from src.services.follows import parse_follow_id

    service = deps.follows_service()
    missing: List[str] = []
    for text in dict.fromkeys(follows):
        parsed = parse_follow_id(text)
        if parsed is None or service.get(*parsed) is None:
            missing.append(text)
    if missing:
        raise NotFoundError("No follow has this id.", {"follows": missing})


def _event_selections(event_ids: List[int]) -> Tuple[Any, ...]:
    """
    Maç kimlikleri → turnuva başına seçimler (`ssc fetch event` gibi). Turnuva katalogdan; bilinmeyen maçın turnuvası
    0'dır (yalnızca iş kartında ve başarısız öğede görünür).
    """
    from src.services.sync import SyncSelection

    store = deps.store()
    by_tournament: Dict[int, List[int]] = {}
    for event_id in dict.fromkeys(event_ids):
        row = store.events.get(int(event_id))
        tournament = int(row.tournament_id) if row is not None and row.tournament_id else 0
        by_tournament.setdefault(tournament, []).append(int(event_id))
    return tuple(SyncSelection(league_id=tid, match_ids=tuple(ids)) for tid, ids in sorted(by_tournament.items()))


def _sync_spec(body: Union[StartSyncJob, StartFetchJob, StartRefreshJob]) -> "SyncSpec":
    """İstek gövdesi → servis belirtimi (SyncSpec). CSV aşaması istenmez: dışa aktarma ayrı bir iş türüdür."""
    from src.services.sync import FollowsSyncSpec, SyncSelection, SyncSpec

    if isinstance(body, StartRefreshJob):
        if body.spec.event_ids and body.spec.league_id:
            raise UsageError("Give only one of league_id and event_ids.", {"fields": ["league_id", "event_ids"]})
        if body.spec.event_ids:
            # Yenileme kipi seçimlerin maçlarını okur; turnuva yalnızca iş kartında görünür
            return SyncSpec(mode="refresh", selections=_event_selections(list(body.spec.event_ids)))
        return SyncSpec(mode="refresh", league_id=body.spec.league_id)
    _check_targets(body)
    spec = body.spec
    mode = "details" if isinstance(body, StartFetchJob) else "seasons" if spec.only == "seasons" else "full"
    if spec.follows:
        return FollowsSyncSpec(mode=mode, follows=tuple(dict.fromkeys(spec.follows)))  # type: ignore[arg-type]
    if spec.event_ids:
        return SyncSpec(mode="details", selections=_event_selections(list(spec.event_ids)))
    return SyncSpec(
        mode=mode,  # type: ignore[arg-type]
        league_id=spec.league_id,
        selections=tuple(
            SyncSelection(league_id=s.league_id, season_ids=tuple(s.season_ids), match_ids=tuple(s.match_ids))
            for s in spec.selections
        ),
    )


def _legacy_payload(spec: "SyncSpec") -> Dict[str, Any]:
    """Eski arayüzün iş kartı başlığı istek gövdesinden üretilir: aynı biçim (bkz. routes/scrape.FetchRequest)."""
    return {
        "league_id": spec.league_id,
        "mode": spec.mode,
        "selections": [
            {"league_id": s.league_id, "season_ids": list(s.season_ids) or None, "match_ids": list(s.match_ids) or None}
            for s in spec.selections
        ] or None,
    }


def _run_sync(handle: "JobHandle", spec: "SyncSpec") -> "JobOutcome":
    """
    İşin gövdesi (işin kendi thread'inde): servisi çalıştırır ve sonucunu işin bitişine çevirir. Servisten
    çıkan hata (ör. StorageError) iş yöneticisine gider: iş, hata tablosundaki koduyla `failed` kaydedilir.
    """
    from src.jobs.manager import JobOutcome
    from src.services.context import build_context
    from src.services.sync import SyncService

    ctx = build_context(deps.config_manager())
    try:
        # Bağlamın deposuna ilk erişim, veri dizinini tam bir depo yapar (schema.json, catalog.db)
        getattr(ctx, "store", None)
    except Exception as e:  # depo açılamadı (daha yeni düzen, meşgul): iş bugünkü gibi dosyalara yazar
        logger.warning("The store of the data directory could not be opened; the job runs without it: %s", e)
    result = SyncService(ctx).run(spec, handle=handle)
    if result.state == "cancelled":
        return JobOutcome(state=JobState.CANCELLED)
    summary: Dict[str, Any] = {"schedule_empty_seasons": result.schedule_empty_seasons, **result.progress,
                               "failed_listings": [dataclasses.asdict(item) for item in result.failed_listings]}
    if result.refresh is not None:
        summary["refresh"] = dataclasses.asdict(result.refresh)
    if result.breaker:
        # Bitiş metninin çeviri anahtarı işin `finished` olayına yazılır: istemci metni koddan üretir
        code, params = "fetch_stopped_by_breaker", {"reason": result.breaker}
        if result.refresh is not None:
            code, params = "refresh_stopped_by_breaker", {"reason": result.breaker, "skipped": result.refresh.skipped}
        return JobOutcome(
            state=JobState(result.state), result=summary, code=code, params=params,
            message=f"Stopped by the circuit breaker ({result.breaker})",
        )
    return JobOutcome(state=JobState(result.state), result=summary)


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=202,
    operation_id="startJob",
    summary="Start a job",
    responses=error_responses(
        "forbidden_origin", "job_running", "data_operation_running", "instance_running", "storage_error",
        "not_supported", "not_found", "invalid_request", "confirmation_required",
    ),
)
def start_job(response: Response, body: Annotated[StartJob, Body(discriminator="kind")]) -> JobResponse:
    """
    Start a job in the background and return it at once (202). Only one job writes to a data directory at a
    time: while another job or a data operation runs, in this server or in another process, the request is
    refused with 409 and the holder in `details`. `clear`, `rebuild` and a real `restore` take the data
    directory for themselves (no download, live service or other data operation may run). A `clear` needs
    `confirm: true` (400 `confirmation_required`). `restore` checks by default (`dry_run: true`); with
    `dry_run: false` it restores, and into a data directory that is not empty only with `force: true` (else 400
    `confirmation_required` with `details.occupied`). A Parquet export without pyarrow on the server is 501.
    A `sync` with `only: "seasons"` reads season lists only; `follows` names the follows to sync; a `fetch` with
    `event_ids` fetches events without their tournament, and a `refresh` with `event_ids` reads those events again.
    """
    from src.jobs.manager import local_origin

    if not isinstance(body, (StartSyncJob, StartFetchJob, StartRefreshJob)):
        job = _start_data_job(body)
        deps.refresh_job_mirror()
        response.headers["Location"] = f"{V1_PREFIX}/jobs/{job.id}"
        return JobResponse(data=job_model(job))
    spec = _sync_spec(body)
    recorded = dataclasses.asdict(spec)
    if isinstance(body, (StartFetchJob, StartRefreshJob)) and body.spec.event_ids:
        recorded["event_ids"] = list(dict.fromkeys(body.spec.event_ids))  # `target` süzgeci için (G12)
    job = deps.job_manager().submit(
        JobKind(body.kind),
        recorded,
        lambda handle: _run_sync(handle, spec),
        origin=local_origin("api"),
        background=True,
        phases=spec.job_phases,
        payload=_legacy_payload(spec),
        on_change=deps.refresh_job_mirror,
    )
    deps.refresh_job_mirror()
    response.headers["Location"] = f"{V1_PREFIX}/jobs/{job.id}"
    return JobResponse(data=job_model(job))


# --- veri işleri: dışa aktarma, yedek, temizleme, katalog, geri yükleme denemesi -------------------


def export_request(spec: Mapping[str, Any]) -> "ExportRequest":
    """İşin belirtimi (iş kaydındaki sözlük) → servis isteği."""
    from src.services.data_jobs import ExportRequest

    flt = spec.get("filter") or {}
    return ExportRequest(
        dataset=str(spec.get("dataset") or "events"), format=str(spec.get("format") or "csv"),
        schema=str(spec.get("schema") or "normalized"), profile=spec.get("profile"), sport=flt.get("sport"),
        tournament_ids=tuple(flt.get("tournament_ids") or ()), season_ids=tuple(flt.get("season_ids") or ()),
        event_ids=tuple(flt.get("event_ids") or ()), status_classes=tuple(flt.get("status_classes") or ()),
        start_from=flt.get("from"), start_to=flt.get("to"),
    )


def _config_files() -> Tuple[str, ...]:
    """Yedeğe giren ayar dosyaları: lig dosyası ve spor eşlemesi (yoksa atlanır)."""
    from src.web import league_sports

    path = deps.config_manager().league_config_path
    return (path, league_sports.sidecar_path(path))


def _export_body(spec: Mapping[str, Any]) -> Any:
    def body(handle: "JobHandle") -> "JobOutcome":
        from src.jobs.manager import JobOutcome
        from src.services.data_jobs import export_path, run_export

        store = deps.store()
        request = export_request(spec)
        result = run_export(store, request, export_path(str(store.data_dir), handle.id, request))
        result.pop("path", None)  # yol iş kaydına yazılmaz: indirme onu iş kimliğinden kurar
        return JobOutcome(result={"export": result})

    return body


def _backup_body(spec: Mapping[str, Any]) -> Any:
    def body(handle: "JobHandle") -> "JobOutcome":
        from src.jobs.manager import JobOutcome
        from src.services.backup import BackupService

        info = BackupService(deps.store()).create(
            spec["scope"], config_files=_config_files(), include_secrets=bool(spec.get("include_env")))
        return JobOutcome(result={"backup": {"name": info.name, "scope": info.scope, "with_env": info.with_env,
                                             "bytes": info.size, "format": info.format}})

    return body


def _clear_body(spec: Mapping[str, Any]) -> Any:
    def body(handle: "JobHandle") -> "JobOutcome":
        from src.jobs.manager import JobOutcome
        from src.services.maintenance import MaintenanceService

        report = MaintenanceService(store=deps.store()).clear(spec["scope"], confirm=True)
        return JobOutcome(result={"clear": {"scopes": list(report.scopes), "cleared": list(report.cleared),
                                            "v3_events": report.v3_events,
                                            "catalog_rebuilt": report.catalog_rebuilt}})

    return body


def _rebuild_body(spec: Mapping[str, Any]) -> Any:
    def body(handle: "JobHandle") -> "JobOutcome":
        from src.jobs.manager import JobOutcome
        from src.services.maintenance import MaintenanceService

        report = MaintenanceService(store=deps.store()).rebuild_catalog(mode=spec["mode"])
        return JobOutcome(result={"rebuild": {
            "mode": report.mode, "reason": report.reason, "completed": report.completed, "events": report.events,
            "events_v3": report.events_v3, "events_legacy": report.events_legacy, "slices": report.slices,
            "problems": len(report.problems), "seconds": round(report.seconds, 3),
        }})

    return body


def _restore_body(spec: Mapping[str, Any]) -> Any:
    def body(handle: "JobHandle") -> "JobOutcome":
        from src.jobs.manager import JobOutcome
        from src.services.backup import BackupService

        dry_run = bool(spec.get("dry_run", True))
        report = BackupService(deps.store()).restore(spec["name"], force=bool(spec.get("force")), dry_run=dry_run)
        result: Dict[str, Any] = {
            "name": report.name, "format": report.format, "scope": report.scope, "dry_run": dry_run,
            "force": report.force, "restored": list(report.restored), "replaced": list(report.replaced),
            "skipped": list(report.skipped), "occupied": list(report.occupied), "counts": dict(report.counts),
        }
        if not dry_run:
            result.update(catalog_rebuilt=report.catalog_rebuilt, verify_ok=report.verify_ok,
                          verify_issues=report.verify_issues)
            from src.services.status import forget_sizes

            forget_sizes()  # disk ölçümü geri yüklenen ağaca göre yeniden yapılsın
        return JobOutcome(result={"restore": result})

    return body


def _start_data_job(body: Any) -> JobSnapshot:
    """Bir veri işini denetler ve başlatır. Denetim iş başlamadan yapılır: reddedilen istek iş kaydı bırakmaz."""
    from src.jobs.manager import local_origin

    kind = JobKind(body.kind)
    lease: Optional[str] = None
    if isinstance(body, StartExportJob):
        from src.services.data_jobs import check_export

        spec: Dict[str, Any] = body.spec.model_dump(by_alias=True)
        check_export(export_request(spec))
        run = _export_body(spec)
    elif isinstance(body, StartBackupJob):
        spec = body.spec.model_dump()
        run = _backup_body(spec)
    elif isinstance(body, StartClearJob):
        spec = body.spec.model_dump()
        if not body.spec.confirm:
            raise UsageError("Clearing deletes stored data; send confirm: true.", {"scope": body.spec.scope},
                             code="confirmation_required")
        run, lease = _clear_body(spec), MAINTENANCE_LEASE
    elif isinstance(body, StartRebuildJob):
        spec = body.spec.model_dump()
        run, lease = _rebuild_body(spec), MAINTENANCE_LEASE
    else:
        spec = body.spec.model_dump()
        from src.services.backup import BackupService

        backups = BackupService(deps.store())
        if body.spec.name not in {info.name for info in backups.list()}:
            raise NotFoundError("No backup has this name.", {"name": body.spec.name})
        if not body.spec.dry_run:
            # Boş olmayan hedef: istek, iş başlamadan reddedilir (Choose adımı `occupied`ı gösterir, `force` gönderir)
            check = backups.restore(body.spec.name, force=body.spec.force, dry_run=True)
            if check.occupied and not body.spec.force:
                raise UsageError(
                    "The data directory is not empty; a restore never merges. Send force: true to move the current "
                    "data to the trash first.", {"name": body.spec.name, "occupied": list(check.occupied)},
                    code="confirmation_required")
            lease = MAINTENANCE_LEASE
        run = _restore_body(spec)
    return deps.job_manager().submit(
        kind, spec, run, origin=local_origin("api"), background=True, lease=lease,
        lease_purpose=kind.value if lease is not None else None, on_change=deps.refresh_job_mirror,
    )


# --- olaylar (SSE) -------------------------------------------------------------------------------


def _resume_position(after: Optional[int], last_event_id: Optional[str]) -> int:
    """
    Akışın başlayacağı yer. Tarayıcı yeniden bağlanırken aynı adresi `Last-Event-ID` ile ister: başlık varsa
    adresteki `after`ın önüne geçer.
    """
    if last_event_id is not None and last_event_id.strip():
        text = last_event_id.strip()
        if not text.isdigit():
            raise ValidationFailed(
                "Last-Event-ID must be the sequence number of a job event.",
                {"errors": [{"loc": ["header", "Last-Event-ID"], "message": "not a sequence number", "type": "int_parsing"}]},
            )
        return int(text)
    return after or 0


@router.get(
    "/jobs/{job_id}/events",
    operation_id="streamJobEvents",
    summary="Follow the events of a job (SSE)",
    response_class=Response,
    responses={
        200: {
            "description": (
                "A `text/event-stream`. Each message has `id: <seq>`, `event: <type>` and `data: <JobEvent>`; "
                "types are started, phase, progress, log, failed, breaker, cancel_requested and finished. "
                "The stream ends after the job's last event. When the requested position is older than the "
                "retained events, one `stream.gap` event with `oldest_seq` is sent and the stream ends."
            ),
            "content": {sse.MEDIA_TYPE: {"schema": {"type": "string"}}},
        },
        **error_responses("not_found", "not_supported"),
    },
)
async def stream_job_events(
    job_id: str,
    after: Optional[int] = Query(None, ge=0, description="Send only events with a sequence number above this."),
    last_event_id: Optional[str] = Header(None, description="Resume after this event; wins over `after`."),
) -> Response:
    """
    The job's own event log as server-sent events: everything that is retained (the newest 2,000 events per
    job), then new events until the job ends. A comment line is sent every 15 seconds. The stream also ends
    when the server shuts down.
    """
    from starlette.concurrency import run_in_threadpool

    position = _resume_position(after, last_event_id)
    await run_in_threadpool(_existing, job_id)
    return sse.job_event_response(deps.job_manager(), job_id, after=position)


__all__ = ["Job", "JobListResponse", "JobResponse", "job_model", "router"]
