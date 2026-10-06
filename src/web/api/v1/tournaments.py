"""
API v1: turnuvalar ve sezonlar (docs/design/02-services.md bölüm 6; docs/design/05-web-ui.md 7.2; plan maddesi P21).

    GET /api/v1/tournaments                         katalogdaki turnuvalar (spor, ad, takip süzgeçleri)
    POST /api/v1/tournaments/search                 SofaScore'da ada göre arama: turnuva, takım, oyuncu (tek istek;
                                                    takım ve oyuncu FX-19)
    GET /api/v1/catalog/suggest                     yazarken öneri: adında metin geçen kayıtlı turnuvalar ve takımlar
                                                    (yalnızca katalog, SofaScore'a istek yok; FX-20)
    GET /api/v1/tournaments/{tournament_id}         tek turnuva, kategorisiyle
    GET /api/v1/tournaments/{tournament_id}/seasons turnuvanın sezonları, en yeni önce; `include=counts` ile sezon
                                                    başına sayımlar (FX-13, 05-web-ui.md G17)
    GET /api/v1/seasons/{season_id}                 tek sezon
    GET /api/v1/seasons/{season_id}/slices          sezonun saklanan dilimleri, yüksüz (P28)
    GET /api/v1/seasons/{season_id}/slices/{key}    sezonun bir dilimi (puan durumu ...), saklanan yüküyle
    GET /api/v1/seasons/{season_id}/standings       sezonun puan durumu, satır satır (şema v1 StandingsRow; P28)

Kayıtlar şema v1'indir (src/schema; docs/design/04-schema-v1.md): Tournament, Category, Season, Slice. Bir turnuva
kaydı iki alan ekler (ek alan eklemek sürüm artırmaz): `category` (katalogdaki kategori kaydı, yoksa null) ve
`followed` (turnuva takip ediliyor). Okumalar katalogdandır (QueryService) ve SofaScore'a istek atmaz; arama
SofaScore'a sorar ve bu yüzden POST'tur (GET olsaydı başka bir site onu kullanıcının adına tetikleyebilirdi).

Sezonun maç dışı dilimleri (puan durumu, sezon bilgisi, kupa ağacı, en iyiler; plan maddesi P28) varsayılan olarak
indirilmez: grupları ya da adları bir seçimde geçince eşitleme onları sezon başına bir kez, sezon sürerken
`max_age`'den eskiyse yeniden okur.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Annotated, Any, Callable, Dict, List, Literal, Optional, TypeVar

from fastapi import APIRouter, Path, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from src.errors import NotFoundError, UsageError
from src.schema import utc_text
from src.web import deps
from src.schema import models as schema_models
from src.web.api.v1 import PageInfo, records
from src.web.errors import error_responses

if TYPE_CHECKING:
    from src.services.owner_data import OwnerDataService
    from src.services.query import QueryService, TournamentEntry

router = APIRouter(tags=["tournaments"])
logger = logging.getLogger("WebAPI")

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
DEFAULT_SUGGEST_LIMIT = 8
MAX_SUGGEST_LIMIT = 20
# İstemci gidince verilen yanıt (nginx'in "istemci kapattı" kodu): kimse okumaz, yalnızca erişim kaydında görünür
CLIENT_CLOSED = 499

T = TypeVar("T")


class TournamentRecord(records.Tournament):  # type: ignore[misc,valid-type]
    """A tournament (schema v1 Tournament) with its category and whether it is followed."""

    category: Optional[records.Category] = Field(  # type: ignore[valid-type]
        default=None, description="The tournament's Category; null when the catalog does not know it.",
    )
    followed: bool = Field(default=False, description="A follow of any origin names the tournament.")


class TournamentResponse(BaseModel):
    data: TournamentRecord


class TournamentListResponse(BaseModel):
    data: List[TournamentRecord]
    page: PageInfo


class SeasonResponse(BaseModel):
    data: records.Season  # type: ignore[valid-type]


class SeasonCounts(BaseModel):
    """Counts of one season of a tournament, from the catalog (`include=counts`)."""

    events: int = Field(description="Every stored event of the season, unfinished schedule rows included.")
    finished: int = Field(description="Events that ended (completed or decided without play).")
    details: int = Field(description="Events with a stored event payload.")
    complete: int = Field(description="Events with details and no missing slice.")
    completion_rate: float = Field(description="complete / details in percent, two decimals; 0 without details.")
    missing: Dict[str, int] = Field(description="Slice to the number of events with details that miss it.")
    schedule_fetched_at_utc: Optional[str] = Field(
        default=None, description="When the newest page of the season's schedule was fetched; null: never.",
    )


class SeasonEntry(records.Season):  # type: ignore[misc,valid-type]
    """A season (schema v1 Season); with `include=counts` also its counts."""

    counts: Optional[SeasonCounts] = Field(default=None, description="Only with `include=counts`.")


class SeasonListResponse(BaseModel):
    data: List[SeasonEntry]
    page: PageInfo


class SliceResponse(BaseModel):
    data: records.Slice  # type: ignore[valid-type]


class SeasonSliceListResponse(BaseModel):
    data: List[records.Slice]  # type: ignore[valid-type]
    page: PageInfo


StandingsRowRecord = records.mirror(schema_models.StandingsRow)


class StandingsResponse(BaseModel):
    data: List[StandingsRowRecord]  # type: ignore[valid-type]
    page: PageInfo


SearchKind = Literal["tournament", "team", "player"]


class TournamentSearch(BaseModel):
    """What to look for on SofaScore."""

    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=2, max_length=100, description="Text of the name.")
    sport: Optional[str] = Field(default=None, max_length=40, description="Only hits of this sport (slug).")
    kinds: List[SearchKind] = Field(
        default_factory=lambda: ["tournament"], min_length=1, max_length=3,
        description="What to look for: tournaments (the default), teams and players. Tournaments alone ask "
                    "SofaScore's tournament search; any other choice asks its general search (one request either "
                    "way).",
    )


class TournamentHitCategory(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    country_code: Optional[str] = Field(default=None, description="SofaScore's country code, as given.")


class SearchHitCountry(BaseModel):
    code: Optional[str] = Field(default=None, description="SofaScore's country code (`alpha2`), as given.")
    name: Optional[str] = None


class SearchHitTeam(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None


class TournamentHit(BaseModel):
    """
    A tournament, a team or a player SofaScore found (`kind`). Follow it with `POST /follows` (`kind`,
    `entity_id` = `id`, `name`, `sport`).
    """

    kind: SearchKind = "tournament"
    id: int
    name: str
    slug: Optional[str] = None
    sport: Optional[str] = Field(default=None, description="Slug of a registered sport; null for others.")
    category: TournamentHitCategory = Field(
        description="The tournament's category; for a team or a player only `country_code` can be set.",
    )
    country: Optional[SearchHitCountry] = Field(
        default=None, description="Country of the tournament's category, of the team or of the player.",
    )
    team: Optional[SearchHitTeam] = Field(default=None, description="A player's team; null for the other kinds.")
    followed: bool = Field(description="A follow of any origin names this tournament, team or player already.")


class TournamentHitListResponse(BaseModel):
    data: List[TournamentHit]
    page: PageInfo


def _record(entry: "TournamentEntry") -> Dict[str, Any]:
    category = entry.category
    return {**records.as_json(entry.tournament), "category": records.as_json(category) if category else None,
            "followed": entry.followed}


def _query() -> "QueryService":
    from src.services.query import QueryService

    return QueryService(deps.store())


def _owner_data() -> "OwnerDataService":
    from src.services.owner_data import OwnerDataService

    return OwnerDataService(deps.store())


def _season_not_found(season_id: int) -> NotFoundError:
    return NotFoundError("The data directory knows no season with this id.", {"season_id": season_id})


def _offset(cursor: Optional[str]) -> int:
    if cursor is None:
        return 0
    if not cursor.isascii() or not cursor.isdigit():
        raise UsageError("The cursor does not belong to this list.", {"cursor": cursor})
    return int(cursor)


# --- turnuvalar ----------------------------------------------------------------------------------


@router.get(
    "/tournaments",
    response_model=TournamentListResponse,
    operation_id="listTournaments",
    summary="List tournaments",
    responses=error_responses("invalid_request"),
)
def list_tournaments(
    sport: Optional[str] = Query(None, max_length=40, description="Only tournaments of this sport (slug)."),
    q: Optional[str] = Query(None, min_length=1, max_length=100, description="Text in the name; case and accents ignored."),
    followed: Optional[bool] = Query(None, description="true: only followed tournaments; false: only the others."),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None, max_length=20, description="`page.next_cursor` of the previous page."),
) -> TournamentListResponse:
    """
    The tournaments the data directory knows (from stored season lists, schedules and events), by name. They
    are not looked up on SofaScore; `POST /tournaments/search` does that.
    """
    offset = _offset(cursor)
    entries, more = _query().tournaments(sport=sport, text=q, followed=followed, limit=limit, offset=offset)
    return TournamentListResponse(
        data=[_record(e) for e in entries],  # type: ignore[misc]
        page=PageInfo(limit=limit, next_cursor=str(offset + limit) if more else None),
    )


@router.post(
    "/tournaments/search",
    response_model=TournamentHitListResponse,
    operation_id="searchTournaments",
    summary="Search tournaments, teams and players on SofaScore",
    responses=error_responses("invalid_request", "forbidden_origin", "blocked", "upstream_error"),
)
async def search_tournaments(body: TournamentSearch, request: Request) -> Any:
    """
    Look the text up on SofaScore (one request, the shared request budget), at most 20 hits of the kinds asked
    for (`kinds`; tournaments by default), each typed by `kind`. An empty list: SofaScore answered and found
    nothing. 503 `blocked` / `rate_limited` and 502 `upstream_error` when it did not answer; `details.reason` is
    blocked, browser, rate_limited, network or upstream. POST, because every call sends a request to SofaScore (a
    GET could be triggered by another site).

    Made for typing: the server keeps each answer for 10 minutes, so the same text again (case and spaces
    ignored) sends nothing to SofaScore. When the client closes the connection before the request was sent
    (for example while it waits for its turn in the request budget), it is not sent at all.
    """
    from src.client.context import FetchCancelled

    try:
        hits = await run_while_connected(
            request, lambda: deps.follows_service().search(body.q, sport=body.sport, kinds=tuple(body.kinds)))
    except FetchCancelled:
        return Response(status_code=CLIENT_CLOSED)
    return TournamentHitListResponse(
        data=[
            TournamentHit(
                kind=h.kind,  # type: ignore[arg-type]
                id=h.id, name=h.name, slug=h.slug, sport=h.sport, followed=h.followed,
                category=TournamentHitCategory(id=h.category_id, name=h.category_name, slug=h.category_slug,
                                               country_code=h.country_code),
                country=SearchHitCountry(code=h.country_code, name=h.country_name)
                if h.country_code or h.country_name else None,
                team=SearchHitTeam(id=h.team_id, name=h.team_name) if h.team_id is not None else None,
            )
            for h in hits
        ],
        page=PageInfo(limit=len(hits), next_cursor=None),
    )


@router.get(
    "/catalog/suggest",
    response_model=TournamentHitListResponse,
    operation_id="suggestCatalog",
    summary="Suggest stored tournaments and teams by name",
    responses=error_responses("invalid_request"),
)
def suggest_catalog(
    q: str = Query(min_length=1, max_length=100, description="Text in the name; case and accents ignored."),
    sport: Optional[str] = Query(None, max_length=40, description="Only names of this sport (slug)."),
    limit: int = Query(DEFAULT_SUGGEST_LIMIT, ge=1, le=MAX_SUGGEST_LIMIT),
) -> TournamentHitListResponse:
    """
    Names to suggest while the user types: the tournaments and the teams (competitors) the data directory knows,
    in the shape of a search hit (`kind` `tournament` or `team`). Names that start with the text come first,
    then names with a word that starts with it, then the others; followed ones first within each. Nothing is
    sent to SofaScore; `POST /tournaments/search` does that.
    """
    found = _query().suggest(q, sport=sport, limit=limit)
    return TournamentHitListResponse(
        data=[
            TournamentHit(
                kind=s.kind,  # type: ignore[arg-type]
                id=s.id, name=s.name, slug=s.slug, sport=s.sport, followed=s.followed,
                category=TournamentHitCategory(id=s.category_id, name=s.category_name, slug=s.category_slug,
                                               country_code=s.country_code),
                country=SearchHitCountry(code=s.country_code) if s.country_code else None,
            )
            for s in found
        ],
        page=PageInfo(limit=limit, next_cursor=None),
    )


async def run_while_connected(request: Request, work: Callable[[], T]) -> T:
    """
    `work`ü bir işçi thread'inde, iptal kontrolü istemcinin bağlantısına bağlı bir istek bağlamında çalıştırır
    (plan maddesi FX-20). İstemci bağlantıyı keserse bağlam iptal edilir: henüz gönderilmemiş SofaScore isteği
    gönderilmez (ortak bütçede sıra beklerken kesilen istek sırasını geri verir, src/throttle.py) ve `work`
    FetchCancelled ile biter. Gönderilmiş bir istek kesilemez: yanıtı gelir ve (aramada) saklanır.
    """
    from src.client.context import request_context

    gone = threading.Event()

    def run() -> T:
        with request_context(cancel=gone.is_set):
            return work()

    task = asyncio.ensure_future(run_in_threadpool(run))
    watcher = asyncio.ensure_future(_until_disconnected(request))
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            gone.set()
            logger.debug("Search: the client left; a request not sent yet is not sent")
        return await task
    finally:
        gone.set()
        watcher.cancel()


async def _until_disconnected(request: Request) -> None:
    """
    İstemci bağlantıyı kesene kadar bekler. `Request.is_disconnected()` kullanılmaz: anında iptal edilen okuması
    uygulamanın `@app.middleware("http")` katmanından (Starlette BaseHTTPMiddleware) geçerken kesilme iletisini
    yitirir; burada ileti gelene kadar beklenir (gövde FastAPI tarafından zaten okunmuştur).
    """
    while True:
        try:
            message = await request.receive()
        except Exception:  # okunamayan bağlantı: istemci gitmiş sayılır
            return
        if message.get("type") == "http.disconnect":
            return


@router.get(
    "/tournaments/{tournament_id}",
    response_model=TournamentResponse,
    operation_id="getTournament",
    summary="Get a tournament",
    responses=error_responses("not_found"),
)
def get_tournament(tournament_id: Annotated[int, Path(ge=1)]) -> TournamentResponse:
    entry = _query().tournament(tournament_id)
    if entry is None:
        raise NotFoundError("The data directory knows no tournament with this id.", {"tournament_id": tournament_id})
    return TournamentResponse(data=_record(entry))  # type: ignore[arg-type]


@router.get(
    "/tournaments/{tournament_id}/seasons",
    response_model=SeasonListResponse,
    operation_id="listTournamentSeasons",
    summary="List the seasons of a tournament",
    responses=error_responses("not_found"),
)
def list_tournament_seasons(
    tournament_id: Annotated[int, Path(ge=1)],
    include: Annotated[Optional[List[Literal["counts"]]], Query(
        description="`counts`: each season's event, detail and completeness counts and the age of its schedule.",
    )] = None,
) -> SeasonListResponse:
    """
    The tournament's seasons the data directory knows, newest first. An empty list: the tournament is known
    but no season is stored (its season list was not downloaded yet).
    """
    seasons = _query().seasons(tournament_id)
    if seasons is None:
        raise NotFoundError("The data directory knows no tournament with this id.", {"tournament_id": tournament_id})
    found: Dict[Optional[int], Any] = {}
    if include and "counts" in include:
        from src.services.status import StatusService

        found = {c.season_id: c for c in StatusService(deps.store()).season_counts(tournament_id)}

    def entry(season: Any) -> Dict[str, Any]:
        body = records.as_json(season)
        counts = found.get(body.get("id"))
        if counts is not None:
            body["counts"] = SeasonCounts(
                events=counts.events, finished=counts.finished, details=counts.details, complete=counts.complete,
                completion_rate=counts.completion_rate, missing=dict(counts.missing),
                schedule_fetched_at_utc=utc_text(counts.schedule_fetched_at),
            )
        elif found:
            body["counts"] = SeasonCounts(events=0, finished=0, details=0, complete=0, completion_rate=0.0, missing={})
        return body

    return SeasonListResponse(
        data=[entry(s) for s in seasons],  # type: ignore[misc]
        page=PageInfo(limit=len(seasons), next_cursor=None),
    )


# --- sezonlar ------------------------------------------------------------------------------------


@router.get(
    "/seasons/{season_id}",
    response_model=SeasonResponse,
    operation_id="getSeason",
    summary="Get a season",
    responses=error_responses("not_found"),
)
def get_season(season_id: Annotated[int, Path(ge=1)]) -> SeasonResponse:
    season = _query().season(season_id)
    if season is None:
        raise NotFoundError("The data directory knows no season with this id.", {"season_id": season_id})
    return SeasonResponse(data=records.as_json(season))


@router.get(
    "/seasons/{season_id}/slices",
    response_model=SeasonSliceListResponse,
    operation_id="listSeasonSlices",
    summary="List the slices of a season",
    responses=error_responses("not_found"),
)
def list_season_slices(season_id: Annotated[int, Path(ge=1)]) -> SeasonSliceListResponse:
    """
    Every stored slice of the season, ordered by key and sub-key, without payloads: its schedule pages and,
    when selected for download, its standings, season info, cup tree, leaders and season odds.
    """
    found = _owner_data().season_slices(season_id)
    if found is None:
        raise _season_not_found(season_id)
    return SeasonSliceListResponse(
        data=[records.as_json(s) for s in found],  # type: ignore[misc]
        page=PageInfo(limit=len(found), next_cursor=None),
    )


@router.get(
    "/seasons/{season_id}/standings",
    response_model=StandingsResponse,
    operation_id="getSeasonStandings",
    summary="Get the standings of a season",
    responses=error_responses("not_found", "invalid_request"),
)
def get_season_standings(
    season_id: Annotated[int, Path(ge=1)],
    table: Optional[Literal["total", "home"]] = Query(None, description="One table only; both when omitted."),
) -> StandingsResponse:
    """
    The stored standings of the season as rows (schema v1 StandingsRow), table by table and in rank order.
    Empty when no standings are stored (they are downloaded only when selected).
    """
    found = _owner_data().standings(season_id, table)
    if found is None:
        raise _season_not_found(season_id)
    return StandingsResponse(
        data=[records.as_json(row) for row in found],  # type: ignore[misc]
        page=PageInfo(limit=len(found), next_cursor=None),
    )


@router.get(
    "/seasons/{season_id}/slices/{key}",
    response_model=SliceResponse,
    operation_id="getSeasonSlice",
    summary="Get a slice of a season",
    responses=error_responses("not_found", "invalid_request"),
)
def get_season_slice(
    season_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,39}$", description="Name of the slice, for example `standings`.")],
    sub: str = Query("", pattern=r"^[a-z0-9_.-]{0,80}$", description="Sub-key of a slice with several payloads; empty for none."),
) -> SliceResponse:
    """A stored slice of the season (standings and other season data) with its payload; 404 when none is stored."""
    try:
        found = _query().season_slice(season_id, key, sub)
    except ValueError as e:
        raise UsageError("The slice name is not valid.", {"key": key, "sub": sub}) from e
    if found is None:
        raise NotFoundError("No such slice is stored for this season.", {"season_id": season_id, "key": key, "sub": sub})
    return SliceResponse(data=records.as_json(found))


__all__ = ["router"]
