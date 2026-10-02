"""
Store satırlarından normalleştirilmiş şema v1 kayıtlarına eşleyiciler (docs/design/04-schema-v1.md).

Girdi katalog satırıdır, yük değil: bir yükten skorun ve durum sınıfının okunduğu tek yer
src/store/derive.py'dir (`scores_json`, `status_class`), burası o satırı sözleşmenin biçimine çevirir.
Yeni okunmuş bir yükü kayda çevirmek isteyen önce `derive.event_row(yük, ...)` çağırır ve sonucu buraya
verir: eşleyiciler hem Store'un satır sınıflarını (`EventRow`, `TournamentRow`, ...) hem de aynı sütun
adlarını taşıyan sözlükleri kabul eder.

Saf işlevler: disk, veritabanı, ağ ve saat yoktur; ayar okunmaz. Politika değeri olan yenileme penceresi
çağırandan gelir (`refresh_window_s`). Bu modül src.store'u çalışma anında içe aktarmaz (alan modülleri
kendilerinden yukarıdaki hiçbir şeyi içe aktarmaz; docs/design/02-services.md 2.1, madde 4).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple, Union

from src.refresh import DEFAULT_REFRESH_WINDOW_HOURS
from src.schema.models import (
    Aggregate,
    Category,
    Change,
    ChangedField,
    Event,
    EventParticipant,
    EventParticipants,
    FootballScore,
    LiveEvent,
    Participant,
    PeriodScore,
    PeriodsScore,
    PlainScore,
    Quality,
    Round,
    Score,
    ScorePair,
    Season,
    SetScore,
    SetsScore,
    Slice,
    SliceError,
    Sport,
    Stage,
    Status,
    Tournament,
)
from src.sports import SPORTS, SportSpec, get_sport, score_family
from src.status import StatusClass

if TYPE_CHECKING:
    from src.store import ChangeRow, EventRow, ParticipantRow, SeasonRow, SliceInfo, StreamRecord, TournamentRow

RowLike = Union[Mapping[str, Any], Any]  # bir satır sınıfı ya da aynı sütun adlarını taşıyan sözlük

DEFAULT_REFRESH_WINDOW_S = DEFAULT_REFRESH_WINDOW_HOURS * 3600.0

SETTLEMENT_OPEN = "open"
SETTLEMENT_PROVISIONAL = "provisional"
SETTLEMENT_FINAL = "final"
# Bitmiş sayılan sınıflar: yalnızca bunlarda "geçici / kesin" ayrımı yapılır (01-storage.md 8.3)
TERMINAL_CLASSES = frozenset({StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value,
                              StatusClass.VOID.value})

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_STATUS_CLASSES = frozenset(member.value for member in StatusClass)
_SIDES = {1: "home", 2: "away", 3: "draw"}  # winnerCode / aggregatedWinnerCode
_PARTICIPANT_TYPES = {0: "team", 1: "player", 2: "pair"}  # homeTeam.type
_HALVES = {"period2": 1, "period4": 2}  # iki yarı formatında skorlar bu iki alanda gelir
_SLICE_STATES = frozenset({"ok", "empty", "error", "not_requested"})
_RECORD_SOURCES = frozenset({"event", "listing"})


# --- yardımcılar --------------------------------------------------------------------------------------

def _col(row: RowLike, name: str) -> Any:
    """Satırın bir sütunu; satır sözlük de olabilir, satır sınıfı da. Olmayan sütun None'dır."""
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _int(value: Any) -> Optional[int]:
    """Tam sayı (bool değil); tam sayıya eşit ondalık da kabul edilir. Gerisi None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value == math.floor(value):
        return int(value)
    return None


def _text(value: Any) -> Optional[str]:
    """Metin; boş metin None olur ("yok" her zaman null'dır, boş metin değil)."""
    if isinstance(value, str) and value != "":
        return value
    return None


def _flag(value: Any) -> Optional[bool]:
    """Katalogdaki 0 / 1 / NULL (ya da bool) → bool | None."""
    if value is None:
        return None
    return bool(value)


def utc_text(value: Any, *, milliseconds: bool = False) -> Optional[str]:
    """
    Epoch saniye (ya da datetime) → "2026-10-01T18:52:04Z". Saat dilimi olmayan datetime UTC sayılır.
    milliseconds: "2026-10-01T18:52:04.123Z". Gösterilemeyen değer (aralık dışı, sayı değil) None olur.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        moment = aware.astimezone(timezone.utc)
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        try:
            # fromtimestamp yerine toplama: Windows'ta negatif epoch'ta da çalışır
            moment = _EPOCH + timedelta(milliseconds=round(value * 1000))
        except OverflowError:
            return None
    else:
        return None
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if milliseconds:
        text += f".{moment.microsecond // 1000:03d}"
    return text + "Z"


def _required_utc(value: Any, what: str, *, milliseconds: bool = False) -> str:
    text = utc_text(value, milliseconds=milliseconds)
    if text is None:
        raise ValueError(f"{what}: not a representable time: {value!r}")
    return text


def _change_ts(value: Any) -> Optional[int]:
    """`changes.changeTimestamp`; SofaScore hiç değiştirmediği olayda 0 yollar, o "yok" demektir: None."""
    stamp = _int(value)
    return stamp if stamp else None


def _pair(value: Any) -> Optional[ScorePair]:
    """`scores_json`daki [ev, deplasman] çifti → ScorePair; çift yoksa None."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    home, away = _int(value[0]), _int(value[1])
    if home is None and away is None:
        return None
    return ScorePair(home=home, away=away)


def _side(code: Any) -> Optional[str]:
    return _SIDES.get(code) if isinstance(code, int) and not isinstance(code, bool) else None


def _sheet(row: RowLike) -> Dict[str, Any]:
    """Satırın skor çizelgesi (`scores_json`); yoksa ya da okunamıyorsa boş sözlük."""
    raw = _col(row, "scores_json")
    if not raw or not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --- varlıklar ----------------------------------------------------------------------------------------

def sport_from_spec(spec: SportSpec, *, sport_id: Optional[int] = None) -> Sport:
    """Kayıt defterindeki bir spor (src/sports.py). `sport_id`: SofaScore'un sayısal kimliği biliniyorsa."""
    return Sport(slug=spec.slug, name=spec.name, id=sport_id, score_family=spec.score_family)


def registered_sports() -> Tuple[Sport, ...]:
    """Platformun desteklediği sporlar, kayıt sırasıyla (kimlikler bilinmez: null)."""
    return tuple(sport_from_spec(spec) for spec in SPORTS)


def sport_from_row(row: RowLike) -> Optional[Sport]:
    """
    Kataloğun `sports` satırı (slug, id, name) → Sport. Ad yükte yoksa kayıt defterindeki ad kullanılır;
    skor ailesi her zaman kayıt defterinden gelir. Slug yoksa None.
    """
    slug = _text(_col(row, "slug"))
    if slug is None:
        return None
    slug = slug.lower()
    registered = get_sport(slug)
    return Sport(
        slug=slug,
        name=_text(_col(row, "name")) or (registered.name if registered else None),
        id=_int(_col(row, "id")),
        score_family=score_family(slug),
    )


def category_from_row(row: RowLike) -> Optional[Category]:
    """Kataloğun `categories` satırı (id, sport, name, slug, alpha2) → Category. Kimlik yoksa None."""
    category_id = _int(_col(row, "id"))
    if category_id is None:
        return None
    return Category(
        id=category_id,
        sport=_text(_col(row, "sport")),
        name=_text(_col(row, "name")),
        slug=_text(_col(row, "slug")),
        country_code=_text(_col(row, "alpha2")),
    )


def tournament_from_row(row: Union["TournamentRow", RowLike]) -> Optional[Tournament]:
    """`TournamentRow` (ya da `tournaments` satırı) → Tournament. Kimlik yoksa None."""
    tournament_id = _int(_col(row, "id"))
    if tournament_id is None:
        return None
    return Tournament(
        id=tournament_id,
        sport=_text(_col(row, "sport")),
        category_id=_int(_col(row, "category_id")),
        name=_text(_col(row, "name")),
        slug=_text(_col(row, "slug")),
    )


def season_from_row(row: Union["SeasonRow", RowLike]) -> Optional[Season]:
    """`SeasonRow` (ya da `seasons` satırı) → Season. Sezon ya da turnuva kimliği yoksa None."""
    season_id, tournament_id = _int(_col(row, "id")), _int(_col(row, "tournament_id"))
    if season_id is None or tournament_id is None:
        return None
    return Season(id=season_id, tournament_id=tournament_id, name=_text(_col(row, "name")),
                  year=_text(_col(row, "year")))


def participant_type(code: Any) -> Optional[str]:
    """`homeTeam.type` → team | player | pair; bilinmeyen kod `other`, kod yoksa None."""
    value = _int(code)
    if value is None:
        return None
    return _PARTICIPANT_TYPES.get(value, "other")


def participant_from_row(row: Union["ParticipantRow", RowLike]) -> Optional[Participant]:
    """`ParticipantRow` (ya da `participants` satırı) → Participant. Kimlik yoksa None."""
    participant_id = _int(_col(row, "id"))
    if participant_id is None:
        return None
    return Participant(
        id=participant_id,
        sport=_text(_col(row, "sport")),
        type=participant_type(_col(row, "type")),  # type: ignore[arg-type]
        name=_text(_col(row, "name")),
        short_name=_text(_col(row, "short_name")),
        slug=_text(_col(row, "slug")),
        name_code=_text(_col(row, "name_code")),
        country_code=_text(_col(row, "country")),
        gender=_text(_col(row, "gender")),
        national=_flag(_col(row, "national")),
    )


# --- skor ---------------------------------------------------------------------------------------------

def _periods(sheet: Mapping[str, Any]) -> Tuple[PeriodScore, ...]:
    """`periods` çizelgesi ({"period2": [38, 33], ...}) → sıra numaralı liste."""
    raw = sheet.get("periods")
    halves = sheet.get("format") == "halves"
    found: List[PeriodScore] = []
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        pair = _pair(value)
        if pair is None:
            continue
        if halves:
            number = _HALVES.get(key)
        else:
            digits = str(key)[len("period"):] if str(key).startswith("period") else ""
            number = int(digits) if digits.isdigit() else None
        if number is not None:
            found.append(PeriodScore(number=number, home=pair.home, away=pair.away))
    return tuple(sorted(found, key=lambda period: period.number))


def _sets(sheet: Mapping[str, Any]) -> Tuple[SetScore, ...]:
    """`games` listesi ile `tiebreaks` sözlüğü ({"1": [7, 5]}) → set listesi."""
    games = sheet.get("games")
    by_number: Dict[int, Optional[ScorePair]] = {}
    for index, value in enumerate(games if isinstance(games, list) else (), start=1):
        by_number[index] = _pair(value)
    tiebreaks: Dict[int, ScorePair] = {}
    raw = sheet.get("tiebreaks")
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        pair = _pair(value)
        if pair is not None and str(key).isdigit():
            tiebreaks[int(key)] = pair
            by_number.setdefault(int(key), None)  # seti olmayan tie-break de görünür kalır
    return tuple(
        SetScore(number=number, home=games_pair.home if games_pair else None,
                 away=games_pair.away if games_pair else None, tiebreak=tiebreaks.get(number))
        for number, games_pair in sorted(by_number.items())
    )


def score_from_row(row: Union["EventRow", RowLike]) -> Score:
    """
    Satırın skoru, sporun skor ailesinin yapısında. Başlık skoru (`home` / `away`) her ailede aynıdır:
    `display`, yoksa `current` (katalogdaki `home_score` / `away_score`). Skor ailesi olmayan sporda yalnızca
    başlık skoru vardır (PlainScore). Çizelgesi çıkarılamamış satır sporunun yapısında, alanları boş gelir.
    """
    home, away = _int(_col(row, "home_score")), _int(_col(row, "away_score"))
    sheet = _sheet(row)
    # Çizelge yoksa (çıkarılamamış) aile yine sporun ailesidir: bir sporun kayıtları hep aynı yapıda gelir
    family = sheet.get("family") or score_family(_col(row, "sport"))
    if family == "football":
        return FootballScore(
            family="football", home=home, away=away,
            half_time=_pair(sheet.get("ht")),
            regulation=_pair(sheet.get("ft90")),
            after_extra_time=_pair(sheet.get("aet")),
            penalties=_pair(sheet.get("penalties")),
        )
    if family == "periods":
        fmt = sheet.get("format")
        return PeriodsScore(
            family="periods", home=home, away=away,
            format=fmt if fmt in ("quarters", "halves") else None,
            periods=_periods(sheet),
            regulation=_pair(sheet.get("regulation")),
            overtime=_pair(sheet.get("overtime")),
            final=_pair(sheet.get("final")),
        )
    if family == "sets":
        return SetsScore(
            family="sets", home=home, away=away,
            sets_won=_pair(sheet.get("sets_won")),
            sets=_sets(sheet),
            match_tiebreak=bool(sheet.get("match_tiebreak")),
        )
    return PlainScore(family=None, home=home, away=away)


def aggregate_from_row(row: Union["EventRow", RowLike]) -> Optional[Aggregate]:
    """İki maçlı eşleşmenin toplam skoru; bugün yalnızca futbol çizelgesi taşır. Yoksa None."""
    sheet = _sheet(row)
    pair = _pair(sheet.get("aggregated"))
    winner = _side(sheet.get("aggregated_winner_code"))
    if pair is None and winner is None:
        return None
    return Aggregate(home=pair.home if pair else None, away=pair.away if pair else None,
                     winner=winner)  # type: ignore[arg-type]


# --- maç ----------------------------------------------------------------------------------------------

def settlement(*, has_event_payload: bool, status_class: str, observed_at: Optional[int],
               start_ts: Optional[int], refresh_window_s: float = DEFAULT_REFRESH_WINDOW_S) -> str:
    """
    Kaydın kesinliği (docs/design/01-storage.md 8.3, ST-27 sonrası kural):

      open         `/event/{id}` yükü yok, ya da durum sınıfı bitmiş değil (not_started, live, unknown)
      provisional  bitmiş; gözlem ve başlangıç zamanı var; gözlem, başlangıç + pencereden önce yapılmış
      final        gerisi: gözlem pencere kapandıktan sonra; ya da gözlem zamanı / başlangıç bilinmiyor;
                   ya da pencere 0 (yenileme politikası kapalı)
    """
    if not has_event_payload or status_class not in TERMINAL_CLASSES:
        return SETTLEMENT_OPEN
    if observed_at is None or start_ts is None or refresh_window_s <= 0:
        return SETTLEMENT_FINAL
    return SETTLEMENT_PROVISIONAL if observed_at - start_ts < refresh_window_s else SETTLEMENT_FINAL


def _event_participant(row: RowLike, side: str) -> Optional[EventParticipant]:
    participant_id, name = _int(_col(row, f"{side}_id")), _text(_col(row, f"{side}_name"))
    if participant_id is None and name is None:
        return None
    return EventParticipant(id=participant_id, name=name)


def event_from_row(row: Union["EventRow", RowLike], *,
                   refresh_window_s: float = DEFAULT_REFRESH_WINDOW_S) -> Event:
    """
    `EventRow` (ya da `derive.event_row` sözlüğü) → Event.

    refresh_window_s: yenileme penceresi, saniye (ayar `REFRESH_WINDOW_HOURS` × 3600; 0 = politika kapalı).
        `quality.settlement` ve `quality.provisional` buna göre hesaplanır; katalog bayrağı saklamaz.

    Satırın kimliği yoksa ValueError.
    """
    event_id = _int(_col(row, "id"))
    if event_id is None:
        raise ValueError(f"event row without an id: {_col(row, 'id')!r}")

    status_class = _col(row, "status_class")
    status_class = str(getattr(status_class, "value", status_class))
    if status_class not in _STATUS_CLASSES:
        status_class = StatusClass.UNKNOWN.value

    row_source = _col(row, "row_source")
    has_payload = bool(_col(row, "has_event_payload"))
    if row_source not in _RECORD_SOURCES:
        row_source = "event" if has_payload else "listing"

    start_ts, observed_at = _int(_col(row, "start_ts")), _int(_col(row, "observed_at"))
    settled = settlement(has_event_payload=has_payload, status_class=status_class, observed_at=observed_at,
                         start_ts=start_ts, refresh_window_s=refresh_window_s)

    stage_id, stage_name = _int(_col(row, "stage_id")), _text(_col(row, "stage_name"))
    round_number = _int(_col(row, "round"))
    round_name, round_slug = _text(_col(row, "round_name")), _text(_col(row, "round_slug"))
    has_round = not (round_number is None and round_name is None and round_slug is None)

    return Event(
        id=event_id,
        sport=_text(_col(row, "sport")),
        category_id=_int(_col(row, "category_id")),
        tournament_id=_int(_col(row, "tournament_id")),
        season_id=_int(_col(row, "season_id")),
        stage=None if stage_id is None and stage_name is None else Stage(id=stage_id, name=stage_name),
        round=Round(number=round_number, name=round_name, slug=round_slug) if has_round else None,
        start_utc=utc_text(start_ts),
        status=Status(
            type=_text(_col(row, "status_type")),
            code=_int(_col(row, "status_code")),
            description=_text(_col(row, "status_description")),
            class_=status_class,  # type: ignore[arg-type]
        ),
        participants=EventParticipants(home=_event_participant(row, "home"), away=_event_participant(row, "away")),
        score=score_from_row(row),
        winner=_side(_col(row, "winner_code")),  # type: ignore[arg-type]
        aggregate=aggregate_from_row(row),
        slug=_text(_col(row, "slug")),
        custom_id=_text(_col(row, "custom_id")),
        quality=Quality(
            source=row_source,
            observed_at_utc=utc_text(observed_at),
            change_ts=_change_ts(_col(row, "change_ts")),
            settlement=settled,  # type: ignore[arg-type]
            provisional=settled == SETTLEMENT_PROVISIONAL,
            tier_hint=_flag(_col(row, "tier_hint")),
            stale=bool(_col(row, "stale")),
            status_regressed=bool(_col(row, "status_regressed")),
        ),
    )


# --- dilim --------------------------------------------------------------------------------------------

def slice_from_info(info: "SliceInfo", *, payload: Any = None) -> Slice:
    """
    `SliceInfo` → Slice. Durumu bilinmeyen bir değerse ValueError.

    payload: ham yük istenmişse o (`store.events.payload(...)` sonucu); istenmemişse None kalır.
    Alt anahtarı olmayan dilimde `sub` null'dır (Store boş metin tutar).
    """
    error = None
    if info.error is not None:
        error = SliceError(reason=str(info.error.reason), http_status=_int(info.error.http_status),
                           at_utc=utc_text(info.error.at), count=int(info.error.count))
    state = str(info.state)
    if state not in _SLICE_STATES:
        raise ValueError(f"slice state: expected one of {', '.join(sorted(_SLICE_STATES))}, got {state!r}")
    return Slice(
        owner_kind=str(info.ref.kind),
        owner_id=int(info.ref.id),
        key=str(info.key),
        sub=_text(info.sub),
        state=state,  # type: ignore[arg-type]
        has_payload=bool(info.has_payload),
        fetched_at_utc=utc_text(info.fetched_at),
        checked_at_utc=utc_text(info.checked_at),
        error=error,
        payload=payload,
    )


# --- değişiklik ---------------------------------------------------------------------------------------

def _status_class_name(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value in _STATUS_CLASSES else None


def change_from_row(row: "ChangeRow") -> Change:
    """
    `ChangeRow` → Change. Günlük satırı (`src/refresh.change_row` biçimi) `row.row`dadır; okunamayan bir
    satırda yalnızca dizinin sütunları dolar ve değişen alanların değerleri null kalır.
    """
    line: Mapping[str, Any] = row.row if isinstance(row.row, Mapping) else {}
    changed = line.get("changed")
    if isinstance(changed, Mapping):
        fields = tuple(
            ChangedField(path=str(path), old=values[0], new=values[1])
            if isinstance(values, (list, tuple)) and len(values) == 2
            else ChangedField(path=str(path), old=None, new=None)
            for path, values in changed.items()
        )
    else:
        fields = tuple(ChangedField(path=str(path), old=None, new=None) for path in row.fields)

    start_ts = _int(line.get("start_ts"))
    new_change_ts = _change_ts(line.get("new_change_ts"))
    reference = new_change_ts if new_change_ts else int(row.ts)  # src/refresh.change_row ile aynı ölçü
    classes = line.get("status_class")
    old_class, new_class = classes if isinstance(classes, (list, tuple)) and len(classes) == 2 else (None, None)
    tier = line.get("tier_hint")
    return Change(
        seq=int(row.seq),
        recorded_at_utc=_required_utc(row.ts, "change ts"),
        event_id=int(row.event_id),
        sport=_text(row.sport),
        tournament_id=_int(row.tournament_id),
        start_utc=utc_text(start_ts),
        seconds_after_start=reference - start_ts if start_ts is not None else None,
        old_status_class=_status_class_name(old_class),  # type: ignore[arg-type]
        new_status_class=_status_class_name(new_class),  # type: ignore[arg-type]
        old_change_ts=_change_ts(line.get("old_change_ts")),
        new_change_ts=new_change_ts,
        status_regressed=bool(row.status_regressed),
        tier_hint=tier if isinstance(tier, bool) else None,
        fields=fields,
    )


# --- akış olayı ---------------------------------------------------------------------------------------

def live_event_from_record(record: "StreamRecord") -> LiveEvent:
    """
    `StreamRecord` → LiveEvent (`sofascore.event/1` zarfı). `data` üreticinin yazdığı haliyle taşınır:
    tipine göre biçimini canlı servis belirler (docs/design/04-schema-v1.md, "LiveEvent data").
    """
    data = record.data if isinstance(record.data, Mapping) else {}
    return LiveEvent(
        stream=str(record.stream),
        seq=int(record.seq),
        type=str(record.type),
        ts=_required_utc(record.ts, "stream event ts", milliseconds=True),
        event_id=_int(record.event_id),
        sport=_text(record.sport),
        tournament_id=_int(record.tournament_id),
        source=_text(record.source),
        data=dict(data),
    )


__all__ = [
    "DEFAULT_REFRESH_WINDOW_S",
    "SETTLEMENT_OPEN",
    "SETTLEMENT_PROVISIONAL",
    "SETTLEMENT_FINAL",
    "TERMINAL_CLASSES",
    "utc_text",
    "sport_from_spec",
    "registered_sports",
    "sport_from_row",
    "category_from_row",
    "tournament_from_row",
    "season_from_row",
    "participant_type",
    "participant_from_row",
    "score_from_row",
    "aggregate_from_row",
    "settlement",
    "event_from_row",
    "slice_from_info",
    "change_from_row",
    "live_event_from_record",
]
