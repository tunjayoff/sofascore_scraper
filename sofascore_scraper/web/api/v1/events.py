"""
API v1: maçlar, dilimleri, ham yükler ve değişiklik günlüğü (docs/design/02-services.md bölüm 6;
docs/design/04-schema-v1.md bölüm 7; docs/design/05-web-ui.md 7.2 ve 7.3: G6, G7, G8; plan maddesi P21).

    GET /api/v1/events                                  süzgeçli, sıralı, imleçle sayfalanan maç listesi
    GET /api/v1/events/{event_id}                       tek maç
    GET /api/v1/events/{event_id}/extra                 yükün başlık bilgileri: sonuç notu, seri skoru, saha, hakem
                                                        (FX-26)
    GET /api/v1/events/{event_id}/slices                maçın dilimleri (yük olmadan)
    GET /api/v1/events/{event_id}/slices/{key}          tek dilim, saklanan yüküyle
    GET /api/v1/events/{event_id}/raw                   olay yükü, olduğu gibi
    GET /api/v1/events/{event_id}/slices/{key}/raw      dilimin yükü, olduğu gibi
    GET /api/v1/events/{event_id}/odds                  bahis oranı dilimleri (yük olmadan; P28)
    GET /api/v1/events/{event_id}/odds/{key}            bir oran diliminin anlık görüntüleri, şema v1 Odds
    GET /api/v1/changes                                 değişiklik günlüğü, kendi sıra numarasıyla; spor ve "geri
                                                        düştü" süzgeçleri, `include=names` ile maçın yarışmacı adları (FX-13,
                                                        G19)

Kayıtlar şema v1'indir (Event, Slice, Change). Liste satırı istenirse `slices_summary` alanını da taşır
(`include=slices_summary`): seçilen dilimler ve onlardan kaçının `ok`, `empty`, `error` olduğu.

Ham yük (bölüm 7): saklanan SofaScore yükü, zarfsız ve değiştirilmeden; `ETag` yükün sha256'sı,
`X-Sofascore-Fetched-At` alındığı an. Yük saklanmıyorsa 404 `not_found`; boş bir yük uydurulmaz. Yük her zaman
sıkıştırması açılmış döner (depo gzip baytlarını dışarı vermez; `Content-Encoding` ile geçirmek yok).

Okumalar katalogdandır (QueryService; oranlar OwnerDataService); hiçbiri SofaScore'a istek atmaz. Oranlar
varsayılan olarak indirilmez: `odds` grubu bir seçimde adlandırılınca (plan maddesi P28). Bir okuma bir anlık
görüntüdür; oranın zaman içindeki seyri ancak yeniden okunduysa (dilimin geçmişi) vardır.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Literal, Optional, Set, Tuple

from fastapi import APIRouter, Header, Path, Query, Response
from pydantic import BaseModel, Field

from sofascore_scraper.errors import NotFoundError, UsageError
from sofascore_scraper.web import deps
from sofascore_scraper.schema import models as schema_models
from sofascore_scraper.web.api.v1 import PageInfo, records
from sofascore_scraper.web.errors import ValidationFailed, error_responses

if TYPE_CHECKING:
    from sofascore_scraper.services.owner_data import OwnerDataService
    from sofascore_scraper.services.query import QueryService, RawPayload

router = APIRouter(tags=["events"])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
ETAG_HEADER = "ETag"
FETCHED_AT_HEADER = "X-Sofascore-Fetched-At"
JSON = "application/json"

StatusClassName = Literal["not_started", "live", "completed", "decided_without_play", "void", "unknown"]
EventSort = Literal["start_utc", "-start_utc"]
_SORTS: Dict[str, str] = {"start_utc": "start_asc", "-start_utc": "start_desc"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- modeller ------------------------------------------------------------------------------------


class SliceSummary(BaseModel):
    """How much of an event's selected data is stored."""

    selected: int = Field(description="Slices selected for the event's sport and phase.")
    ok: int = Field(description="Selected slices with data.")
    empty: int = Field(description="Selected slices SofaScore answered without data.")
    error: int = Field(description="Selected slices whose last request failed.")


class EventListItem(records.Event):  # type: ignore[misc,valid-type]
    """An event (schema v1 Event); with `include=slices_summary` also the summary of its slices."""

    slices_summary: Optional[SliceSummary] = Field(
        default=None, description="Present when the list was asked with `include=slices_summary`; null otherwise.",
    )


class EventListResponse(BaseModel):
    data: List[EventListItem]
    page: PageInfo


class EventResponse(BaseModel):
    data: records.Event  # type: ignore[valid-type]


class EventExtra(BaseModel):
    """
    What SofaScore says about an event beyond the schema v1 Event, read from its stored event payload: the match
    page's header and overview show it (FX-26). Every field is null when the payload does not give it.
    """

    note: Optional[str] = Field(
        default=None, description="SofaScore's note on the result, as given (in English), for example `India beat "
                                  "West Indies by 8 wickets` (cricket).",
    )
    series: Optional[records.mirror(schema_models.ScorePair)] = Field(  # type: ignore[valid-type]
        default=None, description="Games each side has won so far in the best-of series the event belongs to (a "
                                  "baseball postseason series), this event's home side as `home`.",
    )
    venue: Optional[str] = Field(default=None, description="Name of the venue, as given.")
    referee: Optional[str] = Field(default=None, description="Name of the referee, as given.")


class EventExtraResponse(BaseModel):
    data: EventExtra


class SliceListResponse(BaseModel):
    data: List[records.Slice]  # type: ignore[valid-type]
    page: PageInfo


class EventSliceResponse(BaseModel):
    data: records.Slice  # type: ignore[valid-type]


OddsRecord = records.mirror(schema_models.Odds)


class OddsListResponse(BaseModel):
    data: List[OddsRecord]  # type: ignore[valid-type]
    page: PageInfo


class Change(records.Change):  # type: ignore[misc,valid-type]
    """
    A change (schema v1 Change); with `include=names` also the participant names the catalog has for its event
    now (FX-13). The model keeps the name of the schema record: it is the `Change` of the generated client types.
    """

    home_name: Optional[str] = Field(
        default=None, description="Only with `include=names`; null when the catalog does not know the event.",
    )
    away_name: Optional[str] = Field(
        default=None, description="Only with `include=names`; null when the catalog does not know the event.",
    )


class ChangeListResponse(BaseModel):
    data: List[Change]
    page: PageInfo


# --- yardımcılar ---------------------------------------------------------------------------------


def _query() -> "QueryService":
    from sofascore_scraper.services.query import QueryService

    return QueryService(deps.store())


def _owner_data() -> "OwnerDataService":
    from sofascore_scraper.services.owner_data import OwnerDataService

    return OwnerDataService(deps.store())


def _moment(text: Optional[str], name: str, *, end: bool) -> Optional[float]:
    """
    ISO 8601 tarih ya da tarih-saat → epoch saniye. Saat dilimi olmayan değer UTC'dir. Yalnızca tarih verilirse
    `from` o günün başı, `to` o günün sonudur (iki uç dahil).
    """
    if text is None:
        return None
    raw = text.strip()
    try:
        if _DATE.match(raw):
            day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end:
                return (day + timedelta(days=1)).timestamp() - 1
            return day.timestamp()
        moment = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
    except ValueError:
        raise ValidationFailed(
            "The request is not valid.",
            {"errors": [{"loc": ["query", name], "message": "expected an ISO 8601 date or date-time",
                         "type": "datetime_parsing"}]},
        ) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _raw_response(raw: "RawPayload", if_none_match: Optional[str]) -> Response:
    etag = f'"{raw.sha256}"'
    headers = {ETAG_HEADER: etag, "Cache-Control": "private, no-cache"}
    if raw.fetched_at:
        headers[FETCHED_AT_HEADER] = raw.fetched_at
    if if_none_match and etag in {tag.strip() for tag in if_none_match.split(",")}:
        return Response(status_code=304, headers=headers)
    return Response(content=raw.data, media_type=JSON, headers=headers)


_RAW_RESPONSES: Dict[Any, Dict[str, Any]] = {
    200: {
        "description": (
            "The stored SofaScore payload, unchanged and without an envelope. `ETag` is the payload's sha256, "
            "`X-Sofascore-Fetched-At` when it was read (ISO 8601, UTC)."
        ),
        "content": {JSON: {"schema": {"description": "SofaScore's payload as stored."}}},
        "headers": {
            ETAG_HEADER: {"description": "sha256 of the payload, quoted.", "schema": {"type": "string"}},
            FETCHED_AT_HEADER: {"description": "When the payload was read.", "schema": {"type": "string"}},
        },
    },
    304: {"description": "`If-None-Match` names the stored payload."},
    **error_responses("not_found", "invalid_request"),
}


# Dilim adlarının biçimi (sofascore_scraper/store/layout.py): uymayan ad 422 olur; deponun öteki kurallarına (ayrılmış aygıt
# adları) takılan ad 400 `invalid_request`
KEY_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
SUB_PATTERN = r"^[a-z0-9_.-]{0,80}$"
SliceKey = Annotated[str, Path(pattern=KEY_PATTERN, description="Name of the slice, for example `statistics`.")]
SliceSub = Annotated[str, Query(pattern=SUB_PATTERN, description="Sub-key of a slice with several payloads; empty for none.")]


def _invalid_name(key: str, sub: str) -> UsageError:
    return UsageError("The slice name is not valid.", {"key": key, "sub": sub})


# --- maçlar --------------------------------------------------------------------------------------


@router.get(
    "/events",
    response_model=EventListResponse,
    operation_id="listEvents",
    summary="List events",
    responses=error_responses("invalid_request"),
)
def list_events(
    sport: Optional[str] = Query(None, max_length=40, description="Only events of this sport (slug)."),
    tournament: Annotated[Optional[List[int]], Query(description="Only events of these tournaments.")] = None,
    season: Annotated[Optional[List[int]], Query(description="Only events of these seasons.")] = None,
    participant: Annotated[Optional[List[int]], Query(description="Only events of these participants.")] = None,
    follow: Optional[str] = Query(
        None, pattern=r"^(tournament|team|player|event):[1-9][0-9]{0,11}$",
        description="Only the stored events of this follow (`kind:entity_id`, as `FollowRecord.id`): a tournament's, "
                    "a team's, the event itself, or the events of a player's last match list (none until the "
                    "player's first download in this version). The follow need not exist.",
    ),
    status: Annotated[Optional[List[StatusClassName]], Query(description="Only events in these status classes.")] = None,
    from_: Optional[str] = Query(None, alias="from", max_length=40, description="Start at or after; ISO 8601 date or date-time (UTC without an offset)."),
    to: Optional[str] = Query(None, max_length=40, description="Start at or before; a date includes the whole day."),
    has: Optional[Literal["details", "missing"]] = Query(
        None, description="`details`: the event payload is stored; `missing`: known from a listing only.",
    ),
    q: Optional[str] = Query(None, min_length=1, max_length=100, description="Text in a participant's name."),
    followed: bool = Query(False, description="Only events of followed tournaments."),
    sort: Annotated[EventSort, Query(description="`-start_utc`: newest first; `start_utc`: oldest first.")] = "-start_utc",
    include: Annotated[Optional[List[Literal["slices_summary"]]], Query(description="Extra fields per event.")] = None,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None, max_length=200, description="`page.next_cursor` of the previous page."),
) -> EventListResponse:
    """
    Stored events, by start time (ties by id; events without a start time come last when newest first). The
    filters are combined with AND. Every status class the catalog holds is returned unless `status` narrows it.
    """
    from sofascore_scraper.services import follow_sync
    from sofascore_scraper.services.query import EventFilter

    tournament_ids, participant_ids, event_ids = tuple(tournament or ()), tuple(participant or ()), ()
    if follow is not None:
        kind, _, number = follow.partition(":")
        scope = follow_sync.follow_scope(deps.store(), kind, int(number))
        # Süzgeçler VE ile birleşir: takibin turnuvası ya da takımı verilenlerle kesişir
        tournament_ids, none_left = _narrow(tournament_ids, tuple(scope.tournament_ids) if scope else ())
        participant_ids, none_also = _narrow(participant_ids, tuple(scope.participant_ids) if scope else ())
        if scope is None or none_left or none_also:  # oyuncunun listesi yok ya da boş: maçı da yok
            return EventListResponse(data=[], page=PageInfo(limit=limit, next_cursor=None))
        event_ids = tuple(scope.event_ids)
    flt = EventFilter(
        sport=sport,
        tournament_ids=tournament_ids,
        season_ids=tuple(season or ()),
        participant_ids=participant_ids,
        event_ids=event_ids,
        status_classes=tuple(status or ()),
        start_from=_moment(from_, "from", end=False),
        start_to=_moment(to, "to", end=True),
        has_details={"details": True, "missing": False}.get(has or ""),
        text=q,
        followed=followed,
    )
    summary = "slices_summary" in (include or ())
    try:
        page = _query().events(flt, sort=_SORTS[sort], limit=limit, cursor=cursor, slices_summary=summary)
    except ValueError as e:
        raise UsageError("The cursor does not belong to this list or this order.", {"cursor": cursor}) from e
    items: List[Dict[str, Any]] = []
    for event in page.items:
        item = records.as_json(event)
        counts = page.slices.get(event.id) if page.slices is not None else None
        item["slices_summary"] = None if counts is None else {
            "selected": counts.selected, "ok": counts.ok, "empty": counts.empty, "error": counts.error,
        }
        items.append(item)
    return EventListResponse(data=items, page=PageInfo(limit=limit, next_cursor=page.next_cursor))  # type: ignore[arg-type]


def _narrow(given: Tuple[int, ...], wanted: Tuple[int, ...]) -> Tuple[Tuple[int, ...], bool]:
    """İki kimlik süzgecinin kesişimi (VE): (kimlikler, hiçbiri kalmadı mı). Biri boşsa öteki süzer."""
    if not wanted or not given:
        return given or wanted, False
    both = tuple(number for number in given if number in wanted)
    return both, not both


def _event_not_found(event_id: int) -> NotFoundError:
    return NotFoundError("The data directory knows no event with this id.", {"event_id": event_id})


@router.get(
    "/events/{event_id}",
    response_model=EventResponse,
    operation_id="getEvent",
    summary="Get an event",
    responses=error_responses("not_found"),
)
def get_event(event_id: Annotated[int, Path(ge=1)]) -> EventResponse:
    event = _query().event(event_id)
    if event is None:
        raise _event_not_found(event_id)
    return EventResponse(data=records.as_json(event))


@router.get(
    "/events/{event_id}/extra",
    response_model=EventExtraResponse,
    operation_id="getEventExtra",
    summary="Get the header facts of an event",
    responses=error_responses("not_found"),
)
def get_event_extra(event_id: Annotated[int, Path(ge=1)]) -> EventExtraResponse:
    """
    What the stored event payload says beyond the Event record: SofaScore's result note, the series score, the
    venue and the referee (FX-26). Every field is null when no event payload is stored (a match known from a
    schedule only). 404 for an unknown event.
    """
    query = _query()
    if query.event(event_id) is None:
        raise _event_not_found(event_id)
    found = query.event_extra(event_id)
    if found is None:
        return EventExtraResponse(data=EventExtra())
    series = None if found.series is None else {"home": found.series[0], "away": found.series[1]}
    return EventExtraResponse(data=EventExtra(note=found.note, series=series, venue=found.venue,
                                              referee=found.referee))


@router.get(
    "/events/{event_id}/slices",
    response_model=SliceListResponse,
    operation_id="listEventSlices",
    summary="List the slices of an event",
    responses=error_responses("not_found"),
)
def list_event_slices(event_id: Annotated[int, Path(ge=1)]) -> SliceListResponse:
    """
    Every slice of the event the catalog has a row for, and the slices selected for its sport that were never
    requested (`not_requested`), ordered by key. Payloads are not included: get one slice for its payload.
    """
    found = _query().event_slices(event_id)
    if found is None:
        raise _event_not_found(event_id)
    return SliceListResponse(
        data=[records.as_json(s) for s in found],  # type: ignore[misc]
        page=PageInfo(limit=len(found), next_cursor=None),
    )


@router.get(
    "/events/{event_id}/slices/{key}",
    response_model=EventSliceResponse,
    operation_id="getEventSlice",
    summary="Get a slice of an event",
    responses=error_responses("not_found", "invalid_request"),
)
def get_event_slice(event_id: Annotated[int, Path(ge=1)], key: SliceKey, sub: SliceSub = "") -> EventSliceResponse:
    """
    One slice of the event with its stored payload (`payload` null when none is stored). 404 for an unknown
    event and for a slice that neither has a row nor is selected for the event's sport.
    """
    try:
        found = _query().event_slice(event_id, key, sub)
    except ValueError as e:
        raise _invalid_name(key, sub) from e
    if found is None:
        raise NotFoundError("No such slice for this event.", {"event_id": event_id, "key": key, "sub": sub})
    return EventSliceResponse(data=records.as_json(found))


@router.get(
    "/events/{event_id}/raw",
    operation_id="getEventRaw",
    summary="Get the stored event payload",
    response_class=Response,
    responses=_RAW_RESPONSES,
)
def get_event_raw(
    event_id: Annotated[int, Path(ge=1)],
    if_none_match: Optional[str] = Header(None, description="An earlier `ETag`; 304 when it is still current."),
) -> Response:
    """SofaScore's event object as stored (the object, not the `{"event": ...}` wrapper of the response)."""
    from sofascore_scraper.services.query import EVENT_KEY

    raw = _query().raw(event_id, EVENT_KEY)
    if raw is None:
        raise NotFoundError("No event payload is stored for this event.", {"event_id": event_id})
    return _raw_response(raw, if_none_match)


@router.get(
    "/events/{event_id}/slices/{key}/raw",
    operation_id="getEventSliceRaw",
    summary="Get the stored payload of a slice",
    response_class=Response,
    responses=_RAW_RESPONSES,
)
def get_event_slice_raw(
    event_id: Annotated[int, Path(ge=1)],
    key: SliceKey,
    sub: SliceSub = "",
    if_none_match: Optional[str] = Header(None, description="An earlier `ETag`; 304 when it is still current."),
) -> Response:
    """The stored payload of exactly this slice, unchanged. 404 when no payload is stored for it."""
    try:
        raw = _query().raw(event_id, key, sub)
    except ValueError as e:
        raise _invalid_name(key, sub) from e
    if raw is None:
        raise NotFoundError("No payload is stored for this slice.", {"event_id": event_id, "key": key, "sub": sub})
    return _raw_response(raw, if_none_match)


@router.get(
    "/events/{event_id}/odds",
    response_model=SliceListResponse,
    operation_id="listEventOdds",
    summary="List the odds of an event",
    responses=error_responses("not_found"),
)
def list_event_odds(event_id: Annotated[int, Path(ge=1)]) -> SliceListResponse:
    """
    The event's stored odds slices (the `odds` group of the slice registry: `odds_all`, `odds_featured`,
    `odds_changes`, `winning_odds`), one per provider (`sub`), without payloads. Empty unless odds were selected
    for download (they are off by default). Each slice's `fetched_at_utc` is the time of its latest snapshot.
    """
    found = _owner_data().odds_slices(event_id)
    if found is None:
        raise _event_not_found(event_id)
    return SliceListResponse(
        data=[records.as_json(s) for s in found],  # type: ignore[misc]
        page=PageInfo(limit=len(found), next_cursor=None),
    )


@router.get(
    "/events/{event_id}/odds/{key}",
    response_model=OddsListResponse,
    operation_id="listEventOddsSnapshots",
    summary="Get the odds of an event, snapshot by snapshot",
    responses=error_responses("not_found", "invalid_request"),
)
def list_event_odds_snapshots(
    event_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=KEY_PATTERN,
                             description="Odds slice: `odds_all` (all markets) or `odds_featured` (the featured ones).")],
    sub: Optional[str] = Query(None, pattern=r"^[0-9]{1,9}$",
                               description="Provider id (the slice's sub-key); all stored providers when omitted."),
    history: bool = Query(True, description="Every stored snapshot, oldest first; false: the latest one only."),
) -> OddsListResponse:
    """
    The odds of the event as normalized records (schema v1 Odds), oldest snapshot first. A read is a snapshot:
    odds change until the event ends, and a later price exists only when the odds were read again. Empty when no
    odds of this kind are stored; 404 for another slice name.
    """
    from sofascore_scraper.services.owner_data import NORMALIZED_ODDS_KEYS

    if key not in NORMALIZED_ODDS_KEYS:
        raise NotFoundError("Normalized odds exist for odds_all and odds_featured only.",
                            {"key": key, "keys": list(NORMALIZED_ODDS_KEYS)})
    found = _owner_data().odds(event_id, key, sub, history=history)
    if found is None:
        raise _event_not_found(event_id)
    return OddsListResponse(
        data=[records.as_json(o) for o in found],  # type: ignore[misc]
        page=PageInfo(limit=len(found), next_cursor=None),
    )


# --- değişiklikler -------------------------------------------------------------------------------


@router.get(
    "/changes",
    response_model=ChangeListResponse,
    response_model_exclude_unset=True,
    operation_id="listChanges",
    summary="List recorded changes",
    responses=error_responses("invalid_request"),
)
def list_changes(
    since: int = Query(0, ge=0, description="Only changes with a sequence number above this one."),
    event_id: Optional[int] = Query(None, ge=1, description="Only the changes of this event."),
    tournament: Annotated[Optional[List[int]], Query(description="Only changes of these tournaments.")] = None,
    sport: Optional[str] = Query(None, max_length=40, description="Only changes of events of this sport (slug)."),
    regressed: Optional[bool] = Query(
        None, description="true: only changes from completed to void (`status_regressed`); false: only the others.",
    ),
    include: Annotated[Optional[List[Literal["names"]]], Query(
        description="`names`: each change also carries `home_name` and `away_name` (the catalog's names now). "
                    "Without it the records are exactly the schema v1 Change records (as the `changes` export).",
    )] = None,
    from_: Optional[str] = Query(None, alias="from", max_length=40, description="Recorded at or after (ISO 8601)."),
    to: Optional[str] = Query(None, max_length=40, description="Recorded at or before; a date includes the whole day."),
    order: Literal["asc", "desc"] = Query("asc", description="`asc`: oldest first (for syncing); `desc`: newest first."),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None, max_length=20, description="`page.next_cursor` of the previous page."),
) -> ChangeListResponse:
    """
    Changes found in stored events (score corrections, status changes), by their own sequence number. A
    consumer that syncs passes the last `seq` it has seen as `since`; the sequence only grows.
    """
    position: Optional[int] = None
    if cursor is not None:
        if not cursor.isascii() or not cursor.isdigit():
            raise UsageError("The cursor does not belong to this list.", {"cursor": cursor})
        position = int(cursor)
    after = max(since, position) if order == "asc" and position is not None else since
    page = _query().changes(
        after=after, before=position if order == "desc" else None, event_id=event_id,
        tournament_ids=tuple(tournament or ()), since=_moment(from_, "from", end=False),
        until=_moment(to, "to", end=True), order=order, limit=limit, sport=sport, regressed=regressed,
    )
    items = [records.as_json(c) for c in page.items]
    if include and "names" in include:
        names = _participant_names({c.event_id for c in page.items})
        items = [{**item, **names.get(item["event_id"], _NO_NAMES)} for item in items]
    return ChangeListResponse(
        data=items,  # type: ignore[arg-type]
        page=PageInfo(limit=limit, next_cursor=page.next_cursor),
    )


_NO_NAMES: Dict[str, Optional[str]] = {"home_name": None, "away_name": None}


def _participant_names(event_ids: Set[int]) -> Dict[int, Dict[str, Optional[str]]]:
    """Maçların katalogdaki yarışmacı adları (tek sorgu; bilinmeyen maç sözlükte yoktur)."""
    if not event_ids:
        return {}
    from sofascore_scraper.store import EventQuery, Scope

    rows = deps.store().events.list(EventQuery(scope=Scope(event_ids=tuple(sorted(event_ids))),
                                               limit=len(event_ids))).items
    return {row.id: {"home_name": row.home_name, "away_name": row.away_name} for row in rows}


__all__ = ["router"]
