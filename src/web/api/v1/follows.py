"""
API v1: takipler (docs/design/02-services.md bölüm 6 ve 4.3; docs/design/05-web-ui.md 6.2 - 6.4; plan maddesi P21).

    GET    /api/v1/follows                 takipler, konum sırasıyla (kind, origin, enabled ve ad süzgeçleri)
    POST   /api/v1/follows                 yeni takip
    GET    /api/v1/follows/{follow_id}     tek takip
    PATCH  /api/v1/follows/{follow_id}     alanlarını değiştirir
    DELETE /api/v1/follows/{follow_id}     kaldırır (saklanan veri silinmez)

Takibin kimliği `<kind>:<id>`dir (`tournament:17`). Kaynağı (`origin`) neyin değiştirilebileceğini belirler
(`writable`): yapılandırma dosyasının takibi hiç (409 `follow_managed`), leagues.txt'in takibi yalnızca sporu,
API'nin takibi her alanı. Kurallar: src/services/follows.py.

Takip kaldırma, çalışan bir iş varken reddedilir (409 `job_running`): iş lig adını ve sporunu yapılandırmadan okur
(eski `DELETE /api/leagues/{id}` ile aynı kural).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from fastapi import APIRouter, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from src.errors import NotFoundError
from src.schema import utc_text
from src.web import deps
from src.web.api import V1_PREFIX
from src.web.api.v1 import PageInfo
from src.web.errors import error_responses

if TYPE_CHECKING:
    from src.services.follows import FollowsService
    from src.store import Follow

router = APIRouter(tags=["follows"])

FollowKind = Literal["tournament", "team", "player", "event"]
FollowId = Annotated[str, Path(pattern=r"^(tournament|team|player|event):[1-9][0-9]{0,18}$",
                               description="`<kind>:<id>`, for example `tournament:17`.")]
SeasonChoice = Union[str, List[int]]


class FollowRecord(BaseModel):
    """Something the platform downloads and watches: a tournament, a team, a player or one event."""

    id: str = Field(description="`<kind>:<entity_id>`.")
    kind: FollowKind
    entity_id: int = Field(description="SofaScore's id of the followed entity.")
    name: str
    sport: Optional[str] = Field(default=None, description="Stored sport, else (tournaments) the one its events show.")
    seasons: SeasonChoice = Field(description="`all`, `current`, `last:N` or a list of season ids.")
    slices: Optional[Dict[str, Any]] = Field(default=None, description="Data selection; null: the defaults.")
    live: bool = Field(description="The live service watches it (`ssc watch`).")
    enabled: bool
    origin: Literal["legacy", "config", "api"] = Field(
        description="legacy: config/leagues.txt; config: the config file (read-only here); api: added here.",
    )
    position: int
    writable: List[str] = Field(description="Fields PATCH can change on this follow.")
    created_at_utc: Optional[str] = None
    updated_at_utc: Optional[str] = None


class FollowResponse(BaseModel):
    data: FollowRecord


class FollowListResponse(BaseModel):
    data: List[FollowRecord]
    page: PageInfo


class FollowCreate(BaseModel):
    """A new follow. Without a config file a tournament follow is written to config/leagues.txt (name and sport)."""

    model_config = ConfigDict(extra="forbid")

    kind: FollowKind = "tournament"
    entity_id: int = Field(gt=0, lt=2 ** 63)
    name: str = Field(min_length=1, max_length=80)
    sport: Optional[str] = Field(default=None, max_length=40)
    seasons: SeasonChoice = "all"
    live: bool = False
    enabled: bool = True


class FollowPatch(BaseModel):
    """Fields to change; a field left out stays. `sport: null` clears the stored sport."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    sport: Optional[str] = Field(default=None, max_length=40)
    seasons: Optional[SeasonChoice] = None
    live: Optional[bool] = None
    enabled: Optional[bool] = None


def record(service: "FollowsService", follow: "Follow") -> FollowRecord:
    from src.services.follows import follow_id, writable_fields

    seasons = follow.seasons if isinstance(follow.seasons, str) else list(follow.seasons)
    return FollowRecord(
        id=follow_id(follow.kind, follow.entity_id), kind=follow.kind,  # type: ignore[arg-type]
        entity_id=follow.entity_id, name=follow.name, sport=service.sport_of(follow), seasons=seasons,
        slices=dict(follow.slices) if follow.slices is not None else None, live=follow.live, enabled=follow.enabled,
        origin=follow.origin,  # type: ignore[arg-type]
        position=follow.position, writable=list(writable_fields(follow.origin)),
        created_at_utc=utc_text(follow.created_at), updated_at_utc=utc_text(follow.updated_at),
    )


def _key(follow_id: str) -> Tuple[str, int]:
    from src.services.follows import parse_follow_id

    parsed = parse_follow_id(follow_id)
    if parsed is None:  # yol deseni bunu zaten reddeder
        raise NotFoundError("No follow has this id.", {"id": follow_id})
    return parsed


@router.get("/follows", response_model=FollowListResponse, operation_id="listFollows", summary="List follows")
def list_follows(
    kind: Annotated[Optional[FollowKind], Query(description="Only follows of this kind.")] = None,
    origin: Optional[Literal["legacy", "config", "api"]] = Query(None, description="Only follows of this origin."),
    enabled: Optional[bool] = Query(None, description="Only enabled (true) or disabled (false) follows."),
    q: Optional[str] = Query(None, min_length=1, max_length=80, description="Text in the name; case ignored."),
) -> FollowListResponse:
    """Every follow of every origin, in their order. The successor of `GET /api/leagues` and its search."""
    service = deps.follows_service()
    rows = service.list(kind=kind, origin=origin, enabled=enabled, text=q)
    return FollowListResponse(data=[record(service, row) for row in rows],
                              page=PageInfo(limit=len(rows), next_cursor=None))


@router.get(
    "/follows/{follow_id}",
    response_model=FollowResponse,
    operation_id="getFollow",
    summary="Get a follow",
    responses=error_responses("not_found"),
)
def get_follow(follow_id: FollowId) -> FollowResponse:
    kind, entity_id = _key(follow_id)
    service = deps.follows_service()
    found = service.get(kind, entity_id)
    if found is None:
        raise NotFoundError("No follow has this id.", {"id": follow_id})
    return FollowResponse(data=record(service, found))


@router.post(
    "/follows",
    response_model=FollowResponse,
    status_code=201,
    operation_id="addFollow",
    summary="Follow something",
    responses=error_responses("invalid_request", "forbidden_origin", "follow_exists", "storage_error"),
)
def add_follow(response: Response, body: FollowCreate) -> FollowResponse:
    """
    Add a follow and return it (201, with `Location`). A tournament follow is written to config/leagues.txt when
    no config file is in use (then only `name` and `sport` can be set), else to the follows table. A follow of a
    team, a player or an event is always kept in the follows table. 409 `follow_exists` for an entity or a
    tournament name that is followed already.
    """
    from src.services.follows import NewFollow, follow_id

    service = deps.follows_service()
    seasons: Any = body.seasons if isinstance(body.seasons, str) else tuple(body.seasons)
    created = service.add(NewFollow(kind=body.kind, entity_id=body.entity_id, name=body.name, sport=body.sport,
                                    seasons=seasons, live=body.live, enabled=body.enabled))
    response.headers["Location"] = f"{V1_PREFIX}/follows/{follow_id(created.kind, created.entity_id)}"
    return FollowResponse(data=record(service, created))


@router.patch(
    "/follows/{follow_id}",
    response_model=FollowResponse,
    operation_id="updateFollow",
    summary="Change a follow",
    responses=error_responses("not_found", "invalid_request", "forbidden_origin", "follow_managed", "follow_exists"),
)
def update_follow(follow_id: FollowId, body: FollowPatch) -> FollowResponse:
    """
    Change the given fields and return the follow. A follow of the config file cannot be changed here (409
    `follow_managed`); one of config/leagues.txt only its sport (`writable` says which fields can change).
    """
    kind, entity_id = _key(follow_id)
    changes: Dict[str, Any] = body.model_dump(exclude_unset=True)
    if isinstance(changes.get("seasons"), list):
        changes["seasons"] = tuple(changes["seasons"])
    for field in ("name", "seasons", "live", "enabled"):
        if field in changes and changes[field] is None:
            del changes[field]  # yalnızca spor null ile silinir
    service = deps.follows_service()
    return FollowResponse(data=record(service, service.update(kind, entity_id, changes)))


@router.delete(
    "/follows/{follow_id}",
    response_model=FollowResponse,
    operation_id="removeFollow",
    summary="Stop following",
    responses=error_responses("not_found", "forbidden_origin", "follow_managed", "job_running",
                              "data_operation_running"),
)
def remove_follow(follow_id: FollowId) -> FollowResponse:
    """
    Remove the follow and return it as it was. Stored data stays. Refused while a job runs (409), because a job
    reads the names and sports of the follows; a follow of the config file is removed there (409 `follow_managed`).
    """
    kind, entity_id = _key(follow_id)
    service = deps.follows_service()
    with deps.job_store().exclusive("follow_delete"):
        removed = service.remove(kind, entity_id)
    return FollowResponse(data=record(service, removed))


__all__ = ["FollowRecord", "record", "router"]
