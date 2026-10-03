"""
API v1: sağlık, durum, sporlar ve sink'ler (docs/design/02-services.md bölüm 6; docs/design/05-web-ui.md 7).

    GET  /api/v1/health          sunucu ayakta mı, sürümü, SofaScore'a erişimin durumu, istek bütçesi
    GET  /api/v1/status          yukarıdakiler + çalışan iş + belirteç gerekiyor mu + canlı servisin durumu +
                                 veri özeti + tutulan kilitler + zamanlayıcının sonraki çalışmaları (P29) +
                                 isteğe bağlı yetenekler
    POST /api/v1/status/check    SofaScore'a tek bir istekle bağlantı denemesi (yalnızca kullanıcı istediğinde)
    GET  /api/v1/sports          kayıtlı sporlar ve maç detay dilimleri (src/sports.py)
    GET  /api/v1/sports/{slug}   tek spor
    GET  /api/v1/sinks           yapılandırılmış sink'ler: konumları, gecikmeleri, son hataları (salt okunur)

Sürümsüz `/health` yerinde durur (yük dengeleyiciler ve başlatıcı ona bakar; belirteçsiz çağırana yalnızca
"ayakta" der). Buradaki uçlar `/api` altındadır: belirteç ayarlıysa onu isterler.

Durum, servisin sağlığıdır; canlı veri değildir. Canlı servis için yalnızca kilidi, önde giden kaynağı ve son
kalp atışı verilir (`services.live.live_status`); canlı olayların HTTP ucu yoktur (sahibin kararı). Veri özeti
katalogdan sayılır (`StatusService.summary`). Depo açılamıyorsa özet, canlı servis ve kilitler boş kalır, nedenin
kodu `storage_error`dadır; durum yine yanıtlanır (arayüzün sağlık göstergesi buna bakar).
"""
from __future__ import annotations

import importlib.util
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Mapping, Optional, Tuple

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from src import sports
from src.errors import NotFoundError, to_platform_error
from src.schema import SCHEMA_VERSION, utc_text
from src.version import __version__
from src.web import deps, security
from src.web.api.v1 import PageInfo
from src.web.api.v1.jobs import Job, job_model
from src.web.errors import error_responses

if TYPE_CHECKING:
    from src.store import Store

logger = logging.getLogger("WebAPI")
router = APIRouter(tags=["meta"])

API_VERSION = "v1"


# --- modeller ------------------------------------------------------------------------------------


class BridgeLastError(BaseModel):
    kind: str = Field(description="challenge, forbidden or browser.")
    detail: Optional[str] = None
    at: Optional[str] = Field(default=None, description="ISO-8601, UTC.")


class BridgeHealth(BaseModel):
    """Whether SofaScore answers the requests of this process."""

    state: Literal["ok", "degraded", "blocked"]
    consecutive_failures: int
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    failing_since: Optional[str] = None
    changed_at: Optional[str] = None
    last_error: Optional[BridgeLastError] = None
    thresholds: Dict[str, float]


class ThrottleStatus(BaseModel):
    """The request budget shared by all processes of this machine."""

    enabled: bool
    requests_per_second: float
    shared: bool
    error: Optional[str] = None


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    api_version: Literal["v1"]
    bridge: BridgeHealth
    throttle: ThrottleStatus


class HealthResponse(BaseModel):
    data: Health


class LiveStatus(BaseModel):
    """The live service of the data directory (`ssc watch`): its state only, never live data."""

    running: bool = Field(description="A live service holds the `live` lease right now, in any process.")
    pid: Optional[int] = None
    host: Optional[str] = None
    source: Optional[str] = Field(default=None, description="page, direct or poll; null when not running.")
    sports: List[str] = Field(default_factory=list, description="The sports it watches; empty when not running.")
    heartbeat_at: Optional[float] = Field(
        default=None, description="Epoch seconds of the last heartbeat, also of a run that has ended.",
    )
    blocked: bool = Field(default=False, description="SofaScore refuses the service right now.")
    leaders: Dict[str, str] = Field(
        default_factory=dict, description="Sport to the source that leads it now (page, direct or poll).",
    )
    last_switch: Optional[Dict[str, Any]] = Field(default=None, description="The last change of a leading source.")


class TournamentSummary(BaseModel):
    """Counts of one tournament. `tournament_id` null: the events without a unique tournament."""

    tournament_id: Optional[int]
    name: Optional[str] = Field(default=None, description="Name of the follow, else the stored tournament name.")
    followed: bool = Field(description="A tournament of the configured leagues.")
    matches: int = Field(description="Events counted as matches (the `only_finished` rule of the summary).")
    details: int = Field(description="Events with a stored event payload.")
    events: int = Field(description="Every stored event, unfinished schedule rows included.")
    finished: int
    seasons: int = Field(description="Seasons in the tournament's stored season list.")
    seasons_with_events: int
    coverage: float = Field(description="details / matches in percent, one decimal; 0 without matches.")
    last_update_utc: Optional[str] = Field(default=None, description="Newest change of a stored payload.")


class DiskSummary(BaseModel):
    """Disk use of the data directory in bytes; a measurement may be up to a minute old."""

    entries: Dict[str, int] = Field(description="Every top-level entry of the data directory.")
    seasons: int
    matches: int
    details: int
    datasets: int
    total: int = Field(description="seasons + matches + details + datasets.")
    measured_at_utc: Optional[str] = None


class DataSummary(BaseModel):
    """What the data directory holds, counted from its catalog."""

    only_finished: bool = Field(description="Matches count only finished events or events with details.")
    matches: int
    details: int
    seasons: int
    legacy_events: int = Field(description="Events stored in the old layout; `ssc migrate` moves them.")
    catalog_rebuild_reason: Optional[str] = Field(
        default=None, description="Set when the catalog does not describe the files; the counts are then partial.",
    )
    tournaments: List[TournamentSummary]
    disk: Optional[DiskSummary] = None


class LeaseHolder(BaseModel):
    """A lease of the data directory that is held right now."""

    name: str = Field(description="writer, live, sinks, maintenance or watcher:<sport>.")
    purpose: str = ""
    pid: Optional[int] = None
    host: Optional[str] = None
    since_utc: Optional[str] = None


class Capabilities(BaseModel):
    """Optional features this server can offer."""

    parquet: bool = Field(description="Parquet exports (the optional package pyarrow is installed).")
    sse: bool = Field(description="Server-sent event streams of jobs (the package sse-starlette is installed).")
    scheduler: bool = Field(description="The in-app scheduler runs inside this server.")


class ScheduledRun(BaseModel):
    """One task of the in-app scheduler (config file `[[schedule.task]]`) and its next run."""

    index: int = Field(description="Position of the task in the config file, from 1.")
    run: str = Field(description="sync, fetch, refresh or backup.")
    every: Optional[str] = Field(default=None, description="The interval as written (`6h`); null for a cron task.")
    cron: Optional[str] = Field(default=None, description="The cron expression (local time); null for an interval.")
    options: Dict[str, Any] = Field(default_factory=dict, description="The task's options (`league_id`, `scope`).")
    next_run_at_utc: str
    last_run_at_utc: Optional[str] = Field(default=None, description="When the task was last due; null before.")
    last_job_id: Optional[str] = Field(default=None, description="The last job the task started.")
    last_result: Optional[Literal["started", "skipped_running", "skipped_busy", "failed_to_start"]] = Field(
        default=None,
        description="`skipped_running`: its previous run still ran; `skipped_busy`: another job or data operation "
                    "held the data directory.",
    )


class ScheduleStatus(BaseModel):
    """The in-app scheduler of this server (`ssc serve --scheduler`); off by default."""

    enabled: bool = Field(description="The scheduler runs inside this server.")
    next_runs: List[ScheduledRun] = Field(default_factory=list, description="Empty when the scheduler is off.")


class Status(BaseModel):
    version: str
    api_version: Literal["v1"]
    schema_version: int = Field(
        description="Version of the normalized schema of the records this API returns (`sofascore.data/<n>`).",
    )
    auth_required: bool = Field(description="Whether an access token is configured.")
    bridge: BridgeHealth
    throttle: ThrottleStatus
    active_job: Optional[Job] = Field(
        default=None, description="The job that runs on the data directory right now, in any process.",
    )
    live: Optional[LiveStatus] = Field(default=None, description="The live service; null when the store cannot be read.")
    summary: Optional[DataSummary] = Field(default=None, description="Null when the store cannot be read.")
    leases: List[LeaseHolder] = Field(default_factory=list, description="Leases held right now, in any process.")
    schedule: ScheduleStatus = Field(description="The in-app scheduler and the next runs of its tasks.")
    capabilities: Capabilities
    storage_error: Optional[str] = Field(
        default=None,
        description="Error code when the data directory could not be read; the fields that need it are empty.",
    )


class StatusResponse(BaseModel):
    data: Status


class StatusCheckRequest(BaseModel):
    """What to check."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["sofascore"] = Field(description="`sofascore`: one request for the live list of football.")


class StatusCheck(BaseModel):
    """The outcome of one connection check; a failed check is a result, not an error."""

    ok: bool
    reason: Optional[Literal["blocked", "browser", "rate_limited", "network", "upstream"]] = Field(
        default=None, description="Why the check failed; null when it succeeded.",
    )
    message: str = Field(description="English text; clients translate by `reason`.")
    events_count: Optional[int] = Field(default=None, description="Live events in the answer; null on failure.")
    checked_at_utc: str
    bridge: BridgeHealth


class StatusCheckResponse(BaseModel):
    data: StatusCheck


class SportSlice(BaseModel):
    key: str
    path: str = Field(description="Path of the slice in SofaScore's API, with `{event_id}`.")
    required: bool
    default_enabled: bool


class Sport(BaseModel):
    slug: str
    name: str = Field(description="SofaScore's English name.")
    i18n_key: str = Field(description="Translation key of the name for clients.")
    score_family: str
    slices: List[SportSlice] = Field(description="Every slice that applies to the sport, disabled ones included.")


class SportResponse(BaseModel):
    data: Sport


class SportListResponse(BaseModel):
    data: List[Sport]
    page: PageInfo


class SinkStatus(BaseModel):
    """A configured output sink and how far it has delivered the event log."""

    name: str
    type: str = Field(description="stdout, file or webhook.")
    target: Optional[str] = Field(
        default=None, description="The file path, or the webhook address with its credentials masked.",
    )
    events: List[str] = Field(description="Event type patterns the sink takes.")
    state: Literal["ok", "error", "pending"] = Field(
        description="`pending`: the sink has delivered nothing yet; `error`: the last delivery failed.",
    )
    served: bool = Field(description="A process holds the `sinks` lease and delivers right now.")
    cursor: int = Field(description="Sequence number of the last event delivered to the sink.")
    head_seq: int = Field(description="Sequence number of the newest event of the log.")
    lag_events: int = Field(description="Events of the log after the cursor (all types, before the sink's filter).")
    lag_seconds: Optional[float] = Field(default=None, description="Age of the oldest undelivered event; null when none.")
    last_delivered_at_utc: Optional[str] = None
    last_error: Optional[str] = None
    dropped: int = Field(description="Undelivered events given up as too old, as far as the retained log tells.")


class SinkListResponse(BaseModel):
    data: List[SinkStatus]
    page: PageInfo


def _bridge() -> BridgeHealth:
    from src import bridge_health

    return BridgeHealth.model_validate(bridge_health.snapshot())


def _throttle() -> ThrottleStatus:
    from src import throttle

    return ThrottleStatus.model_validate(throttle.status())


def _sport(spec: sports.SportSpec) -> Sport:
    return Sport(
        slug=spec.slug,
        name=spec.name,
        i18n_key=spec.i18n_key,
        score_family=spec.score_family,
        slices=[
            SportSlice(key=s.key, path=s.path, required=s.counts_in(spec.slug), default_enabled=s.default_enabled)
            for s in sports.DETAIL_SLICES
            if s.applies_to(spec.slug)
        ],
    )


# --- durum ---------------------------------------------------------------------------------------

# Kilit adları: sabitler ve her kayıtlı spor için `watcher:<spor>` (src/store/lease.py)
_LEASES: Tuple[str, ...] = ("writer", "live", "sinks", "maintenance")
_LEGACY_LAYOUT = "legacy"


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def capabilities() -> Capabilities:
    """İsteğe bağlı paketler ve zamanlayıcının bu sunucuda çalışıp çalışmadığı (P29)."""
    from src.jobs import scheduler

    return Capabilities(parquet=_installed("pyarrow"), sse=_installed("sse_starlette"),
                        scheduler=scheduler.current() is not None)


def schedule() -> ScheduleStatus:
    """Zamanlayıcının durumu (src/services/status.schedule_status) v1 modeliyle."""
    from src.services.status import schedule_status

    found = schedule_status()
    return ScheduleStatus(enabled=found.enabled, next_runs=[
        ScheduledRun(
            index=s.index, run=s.run, every=s.every, cron=s.cron, options=dict(s.options),
            next_run_at_utc=utc_text(s.next_run_at) or "",
            last_run_at_utc=utc_text(s.last_run_at) if s.last_run_at is not None else None,
            last_job_id=s.last_job_id, last_result=s.last_result,
        )
        for s in found.next_runs
    ])


def _live(store: "Store") -> LiveStatus:
    from src.services.live import live_status

    raw = live_status(store)
    last_switch = raw.get("last_switch")
    return LiveStatus(
        running=bool(raw.get("running")),
        pid=raw.get("pid"),
        host=raw.get("host"),
        source=raw.get("source"),
        sports=[str(s) for s in raw.get("sports") or ()],
        heartbeat_at=raw.get("heartbeat_at"),
        blocked=bool(raw.get("blocked")),
        leaders={str(k): str(v) for k, v in (raw.get("leaders") or {}).items()},
        last_switch=dict(last_switch) if isinstance(last_switch, Mapping) else None,
    )


def lease_holders(store: "Store") -> List[LeaseHolder]:
    """Şu an tutulan kilitler (bütün süreçler), `_LEASES` sırasıyla, sonra izleyiciler."""
    names = list(_LEASES) + [f"watcher:{spec.slug}" for spec in sports.SPORTS]
    held: List[LeaseHolder] = []
    for name in names:
        info = store.lease_holder(name)
        if info is not None:
            held.append(LeaseHolder(
                name=info.name, purpose=info.purpose or "", pid=info.pid, host=info.host,
                since_utc=utc_text(info.acquired_at) if info.acquired_at is not None else None,
            ))
    return held


def _tournament_names(store: "Store", ids: List[Optional[int]], followed: Mapping[int, str]) -> Dict[int, str]:
    names: Dict[int, str] = {}
    for tid in ids:
        if tid is None:
            continue
        if tid in followed:
            names[tid] = followed[tid]
            continue
        row = store.entities.tournament(tid)
        if row is not None and row.name:
            names[tid] = row.name
    return names


def data_summary(store: "Store", followed: Mapping[int, str]) -> DataSummary:
    """Veri dizininin özeti (StatusService.summary) v1 modeliyle; takip edilen ligler maçları olmasa da dökümdedir."""
    from src.services.status import StatusService

    data = StatusService(store).summary(tournament_ids=tuple(followed))
    info = store.info(sizes=False)
    names = _tournament_names(store, [t.tournament_id for t in data.tournaments], followed)
    disk = data.disk
    return DataSummary(
        only_finished=data.only_finished,
        matches=data.matches,
        details=data.details,
        seasons=data.seasons,
        legacy_events=int(info.events_by_layout.get(_LEGACY_LAYOUT, 0)),
        catalog_rebuild_reason=data.catalog_rebuild_reason,
        tournaments=[
            TournamentSummary(
                tournament_id=t.tournament_id,
                name=names.get(t.tournament_id) if t.tournament_id is not None else None,
                followed=t.tournament_id in followed,
                matches=t.matches, details=t.details, events=t.events, finished=t.finished,
                seasons=t.seasons, seasons_with_events=t.seasons_with_events, coverage=t.coverage,
                last_update_utc=utc_text(t.last_update) if t.last_update is not None else None,
            )
            for t in data.tournaments
        ],
        disk=None if disk is None else DiskSummary(
            entries={str(k): int(v) for k, v in disk.entries.items()},
            seasons=disk.seasons, matches=disk.matches, details=disk.details, datasets=disk.datasets,
            total=disk.total, measured_at_utc=utc_text(disk.measured_at) if disk.measured_at else None,
        ),
    )


# --- rotalar -------------------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, operation_id="getHealth", summary="Health of the server")
def health() -> HealthResponse:
    """The server is up; whether SofaScore answers it; the request budget."""
    return HealthResponse(data=Health(
        status="ok", version=__version__, api_version=API_VERSION, bridge=_bridge(), throttle=_throttle(),
    ))


@router.get("/status", response_model=StatusResponse, operation_id="getStatus", summary="Status of the service")
def status() -> StatusResponse:
    """
    Service state: versions, access to SofaScore, the request budget, the job that is running now, the live
    service, what the data directory holds, the leases held right now and the optional features. When the
    data directory cannot be read, `live` and `summary` are null and `storage_error` has the error code.
    """
    active = deps.job_manager().active()
    live: Optional[LiveStatus] = None
    summary: Optional[DataSummary] = None
    leases: List[LeaseHolder] = []
    storage_error: Optional[str] = None
    try:
        store = deps.store()
        live = _live(store)
        leases = lease_holders(store)
        summary = data_summary(store, deps.config_manager().get_leagues())
    except Exception as e:  # depo açılamadı ya da okunamadı: durum yine yanıtlanır
        error = to_platform_error(e)
        if error.code == "internal":
            raise
        logger.warning("Status: the data directory could not be read (%s): %s", error.code, type(e).__name__)
        storage_error = error.code
    return StatusResponse(data=Status(
        version=__version__,
        api_version=API_VERSION,
        schema_version=SCHEMA_VERSION,
        auth_required=bool(security.api_token()),
        bridge=_bridge(),
        throttle=_throttle(),
        active_job=job_model(active) if active is not None else None,
        live=live,
        summary=summary,
        leases=leases,
        schedule=schedule(),
        capabilities=capabilities(),
        storage_error=storage_error,
    ))


def check_sofascore() -> StatusCheck:
    """
    Canlı maç listesine tek bir istek (istemcinin API kökü ve ortak istek bütçesiyle). Başarısızlığın nedeni
    src/web/upstream.py'deki sözlüktendir.
    """
    from src import bridge_health
    from src.client import api_url, endpoints
    from src.exceptions import SofaScoreScraperError
    from src.utils import make_api_request
    from src.web import upstream

    before = bridge_health.snapshot()
    reason: Optional[str] = None
    count: Optional[int] = None
    try:
        # Etkileşimli: 403 bekleme döngüsüyle bir sunucu işçisini dakikalarca tutma
        data = make_api_request(api_url(endpoints.live_events("football")), max_retries=1, timeout=10,
                                raise_errors=True)
    except SofaScoreScraperError as e:
        reason = upstream.reason_for(e, before)
        if reason == upstream.NOT_FOUND:
            reason = upstream.UPSTREAM
        logger.warning("Connection check failed (%s): %s", reason, type(e).__name__)
    else:
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            count = len(data["events"])
        else:
            reason = upstream.UPSTREAM
    message = "SofaScore answered." if reason is None else upstream.detail(reason)["message"]
    return StatusCheck(
        ok=reason is None, reason=reason, message=message, events_count=count,  # type: ignore[arg-type]
        checked_at_utc=utc_text(time.time()) or "", bridge=_bridge(),
    )


@router.post(
    "/status/check",
    response_model=StatusCheckResponse,
    operation_id="checkConnection",
    summary="Check the connection to SofaScore",
    responses=error_responses("forbidden_origin"),
)
def check_connection(body: StatusCheckRequest) -> StatusCheckResponse:
    """
    Send one request to SofaScore now (the live list of football) and report whether it was answered. Only
    called when the user asks; it takes a turn of the shared request budget. A failed check answers 200 with
    `ok: false` and the `reason`: blocked, browser, rate_limited, network or upstream.
    """
    return StatusCheckResponse(data=check_sofascore())


@router.get("/sports", response_model=SportListResponse, operation_id="listSports", summary="List sports")
def list_sports() -> SportListResponse:
    """The registered sports, in registry order, each with the detail slices that apply to it."""
    items = [_sport(spec) for spec in sports.SPORTS]
    return SportListResponse(data=items, page=PageInfo(limit=len(items), next_cursor=None))


@router.get(
    "/sports/{slug}",
    response_model=SportResponse,
    operation_id="getSport",
    summary="Get a sport",
    responses=error_responses("not_found"),
)
def get_sport(slug: str) -> SportResponse:
    spec = sports.get_sport(slug)
    if spec is None:
        raise NotFoundError("No sport has this slug.", {"slug": slug})
    return SportResponse(data=_sport(spec))


# --- sink'ler ------------------------------------------------------------------------------------


@router.get("/sinks", response_model=SinkListResponse, operation_id="listSinks", summary="List the output sinks")
def list_sinks() -> SinkListResponse:
    """
    The sinks of the configuration (config file `[[sink]]` or SOFASCORE_SINKS), in their order, with the
    position each has delivered up to and how far it lags behind the event log. Sinks are configured in the
    file only; this route only reads. Credentials in webhook addresses are masked. Whether a process
    delivers right now is `served` (the holder of the `sinks` lease is in `/status` `leases`).
    """
    from src.services.sink_status import sink_states

    specs = list(deps.loaded_settings().settings.sinks)
    states = sink_states(deps.store(), specs) if specs else []
    items = [
        SinkStatus(
            name=st.name, type=st.type, target=st.target, events=list(st.events),
            state=st.state,  # type: ignore[arg-type]
            served=st.served, cursor=st.cursor, head_seq=st.head_seq, lag_events=st.lag_events,
            lag_seconds=st.lag_seconds,
            last_delivered_at_utc=utc_text(st.delivered_at) if st.delivered_at is not None else None,
            last_error=st.last_error, dropped=st.dropped,
        )
        for st in states
    ]
    return SinkListResponse(data=items, page=PageInfo(limit=len(items), next_cursor=None))


__all__ = ["API_VERSION", "capabilities", "check_sofascore", "data_summary", "lease_holders", "router"]
