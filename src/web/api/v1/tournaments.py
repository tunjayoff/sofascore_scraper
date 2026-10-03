"""
API v1: turnuvalar ve sezonlar (docs/design/02-services.md bölüm 6; docs/design/05-web-ui.md 7.2; plan maddesi P21).

    GET /api/v1/tournaments                         katalogdaki turnuvalar (spor, ad, takip süzgeçleri)
    GET /api/v1/tournaments/{tournament_id}         tek turnuva, kategorisiyle
    GET /api/v1/tournaments/{tournament_id}/seasons turnuvanın sezonları, en yeni önce
    GET /api/v1/seasons/{season_id}                 tek sezon
    GET /api/v1/seasons/{season_id}/slices/{key}    sezonun bir dilimi (puan durumu ...), saklanan yüküyle

Kayıtlar şema v1'indir (src/schema; docs/design/04-schema-v1.md): Tournament, Category, Season, Slice. Bir turnuva
kaydı iki alan ekler (ek alan eklemek sürüm artırmaz): `category` (katalogdaki kategori kaydı, yoksa null) ve
`followed` (turnuva takip ediliyor). Okumalar katalogdandır (QueryService); hiçbiri SofaScore'a istek atmaz.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from src.errors import NotFoundError, UsageError
from src.web import deps
from src.web.api.v1 import PageInfo, records
from src.web.errors import error_responses

if TYPE_CHECKING:
    from src.services.query import QueryService, TournamentEntry

router = APIRouter(tags=["tournaments"])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


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


class SeasonListResponse(BaseModel):
    data: List[records.Season]  # type: ignore[valid-type]
    page: PageInfo


class SliceResponse(BaseModel):
    data: records.Slice  # type: ignore[valid-type]


def _record(entry: "TournamentEntry") -> Dict[str, Any]:
    category = entry.category
    return {**records.as_json(entry.tournament), "category": records.as_json(category) if category else None,
            "followed": entry.followed}


def _query() -> "QueryService":
    from src.services.query import QueryService

    return QueryService(deps.store())


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
def list_tournament_seasons(tournament_id: Annotated[int, Path(ge=1)]) -> SeasonListResponse:
    """
    The tournament's seasons the data directory knows, newest first. An empty list: the tournament is known
    but no season is stored (its season list was not downloaded yet).
    """
    seasons = _query().seasons(tournament_id)
    if seasons is None:
        raise NotFoundError("The data directory knows no tournament with this id.", {"tournament_id": tournament_id})
    return SeasonListResponse(
        data=[records.as_json(s) for s in seasons],  # type: ignore[misc]
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
