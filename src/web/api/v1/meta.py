"""
API v1: sağlık, durum ve sporlar (docs/design/02-services.md bölüm 6).

    GET /api/v1/health          sunucu ayakta mı, sürümü, SofaScore'a erişimin durumu, istek bütçesi
    GET /api/v1/status          yukarıdakiler + veri dizininde şu an çalışan iş + belirteç gerekiyor mu
    GET /api/v1/sports          kayıtlı sporlar ve maç detay dilimleri (src/sports.py)
    GET /api/v1/sports/{slug}   tek spor

Sürümsüz `/health` yerinde durur (yük dengeleyiciler ve başlatıcı ona bakar; belirteçsiz çağırana yalnızca
"ayakta" der). Buradaki uçlar `/api` altındadır: belirteç ayarlıysa onu isterler.

Durum, servisin sağlığıdır; canlı veri değildir. Canlı servisin kilidi, önde giden kaynağı ve son kalp atışı
canlı servisle birlikte eklenir (P23); veri özeti ve kapsama P21'in işidir.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src import sports
from src.errors import NotFoundError
from src.version import __version__
from src.web import deps, security
from src.web.api.v1 import PageInfo
from src.web.api.v1.jobs import Job, job_model
from src.web.errors import error_responses

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


class Status(BaseModel):
    version: str
    api_version: Literal["v1"]
    auth_required: bool = Field(description="Whether an access token is configured.")
    bridge: BridgeHealth
    throttle: ThrottleStatus
    active_job: Optional[Job] = Field(
        default=None, description="The job that runs on the data directory right now, in any process.",
    )


class StatusResponse(BaseModel):
    data: Status


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
            SportSlice(key=s.key, path=s.path, required=s.required, default_enabled=s.default_enabled)
            for s in sports.DETAIL_SLICES
            if s.applies_to(spec.slug)
        ],
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
    """Service state: versions, access to SofaScore, the request budget and the job that is running now."""
    active = deps.job_manager().active()
    return StatusResponse(data=Status(
        version=__version__,
        api_version=API_VERSION,
        auth_required=bool(security.api_token()),
        bridge=_bridge(),
        throttle=_throttle(),
        active_job=job_model(active) if active is not None else None,
    ))


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


__all__ = ["API_VERSION", "router"]
