"""
API v1: işler (docs/design/02-services.md bölüm 6 ve 2.8).

    GET  /api/v1/jobs                  iş geçmişi (en yeni önce), `state` ve `kind` süzgeçleri, imleçle sayfalama
    POST /api/v1/jobs                  iş başlatır: {"kind": "sync" | "fetch" | "refresh", "spec": {...}}
    GET  /api/v1/jobs/{id}             tek iş
    POST /api/v1/jobs/{id}/cancel      iptal ister (iş hangi süreçte çalışırsa çalışsın)
    GET  /api/v1/jobs/{id}/events      işin olayları, SSE (src/web/sse.py)

İşler iş yöneticisinden (src/jobs/manager.py) okunur: komut satırından başlatılan işler de burada görünür ve
buradan iptal edilebilir. Yanıttaki iş, iş modelinin (src/jobs/model.py: Job) alanlarını taşır; durum adları
yeni modelinkilerdir (`succeeded`, `partial`, ...).

Başlatma: iş, web sürecinin iş deposu üzerinde (eski `/api/fetch` ile aynı depo, aynı `writer` kilidi) ayrı bir
thread'de çalışır. Belirtim (`spec`) bugünkü eşitleme servisinin belirtimidir (src/services/sync.py: SyncSpec):

    sync     sezon listeleri → maç listeleri → maç detayları           (SyncSpec mode="full")
    fetch    yalnızca maç detayları                                     (SyncSpec mode="details")
    refresh  kayıtlı geçici maçların yeniden okunması                   (SyncSpec mode="refresh")

Üçü de CSV yazmaz: dışa aktarma kendi iş türüdür (`export`) ve servisi gelene kadar `not_supported` döner;
`backup`, `clear` ve `rebuild` için de öyle. Belirtim, alma hattı (P13) hedeflere ve aşamalara geçtiğinde
değişecektir; değişiklik OpenAPI kaydında görünür.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Body, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from src.errors import NotFoundError, NotSupportedError, UsageError
from src.jobs.model import Job as JobSnapshot
from src.jobs.model import JobKind, JobState
from src.web import deps, sse
from src.web.api import V1_PREFIX
from src.web.api.v1 import PageInfo
from src.web.errors import ValidationFailed, error_responses

if TYPE_CHECKING:
    from src.jobs.manager import JobHandle, JobOutcome
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


class SyncJobSpec(BaseModel):
    """What to download. Without `selections` and `league_id`: every followed tournament."""

    model_config = ConfigDict(extra="forbid")

    league_id: Optional[int] = Field(default=None, gt=0, description="One tournament; not read when `selections` is given.")
    selections: List[JobSelection] = Field(default_factory=list)


class RefreshJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    league_id: Optional[int] = Field(default=None, gt=0)


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


class StartOtherJob(BaseModel):
    """Job kinds of the contract that cannot be started through the API yet; answered with 501 `not_supported`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["export", "backup", "clear", "rebuild"]
    spec: Dict[str, Any] = Field(default_factory=dict)


StartJob = Union[StartSyncJob, StartFetchJob, StartRefreshJob, StartOtherJob]


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
) -> JobListResponse:
    """Jobs of the data directory, newest first: those of this server and those started from the command line."""
    jobs = deps.job_manager().list(limit=HISTORY_LIMIT, kinds=kind or None, states=state or None)
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


def _sync_spec(body: Union[StartSyncJob, StartFetchJob, StartRefreshJob]) -> "SyncSpec":
    """İstek gövdesi → servis belirtimi (SyncSpec). CSV aşaması istenmez: dışa aktarma ayrı bir iş türüdür."""
    from src.services.sync import SyncSelection, SyncSpec

    if isinstance(body, StartRefreshJob):
        return SyncSpec(mode="refresh", league_id=body.spec.league_id, export=False)
    return SyncSpec(
        mode="full" if isinstance(body, StartSyncJob) else "details",
        league_id=body.spec.league_id,
        selections=tuple(
            SyncSelection(league_id=s.league_id, season_ids=tuple(s.season_ids), match_ids=tuple(s.match_ids))
            for s in body.spec.selections
        ),
        export=False,
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
    summary: Dict[str, Any] = {"schedule_empty_seasons": result.schedule_empty_seasons, **result.progress}
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
        "not_supported",
    ),
)
def start_job(response: Response, body: Annotated[StartJob, Body(discriminator="kind")]) -> JobResponse:
    """
    Start a job in the background and return it at once (202). Only one job writes to a data directory at a
    time: while another job or a data operation runs, in this server or in another process, the request is
    refused with 409 and the holder in `details`.
    """
    from src.jobs.manager import local_origin

    if isinstance(body, StartOtherJob):
        raise NotSupportedError(
            f"Jobs of kind '{body.kind}' cannot be started through the API yet.", {"kind": body.kind},
        )
    spec = _sync_spec(body)
    job = deps.job_manager().submit(
        JobKind(body.kind),
        dataclasses.asdict(spec),
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
