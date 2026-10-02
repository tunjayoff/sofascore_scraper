"""
Normalleştirilmiş şema v1: platformun dışarıya verdiği kayıtların sözleşmesi
(docs/design/04-schema-v1.md; docs/design/00-platform.md bölüm 3).

  models      kayıt türleri: Sport, Category, Tournament, Season, Participant, Event, Slice, Change, LiveEvent
  mappers     Store satırlarından (EventRow, SliceInfo, ChangeRow, StreamRecord, ...) bu kayıtlara
  jsonschema  kayıtların JSON Schema belgesi (`ssc describe schemas`)

Paket saftır (docs/design/02-services.md 2.1, madde 4): disk, veritabanı, ağ, ayar ve saat yoktur ve
src.store çalışma anında içe aktarılmaz. Ham SofaScore yükü bu paketten geçmez; Store'dan olduğu gibi okunur.

Kullanım:

    from src import schema
    record = schema.event_from_row(store.events.get(event_id), refresh_window_s=window).to_dict()
"""
from src.schema.jsonschema import describe, json_schema, record_schema
from src.schema.mappers import (
    DEFAULT_REFRESH_WINDOW_S,
    category_from_row,
    change_from_row,
    event_from_row,
    live_event_from_record,
    participant_from_row,
    registered_sports,
    season_from_row,
    settlement,
    slice_from_info,
    sport_from_row,
    sport_from_spec,
    tournament_from_row,
    utc_text,
)
from src.schema.models import (
    EVENT_ENVELOPE_ID,
    MODELS,
    RECORDS,
    SCHEMA_ID,
    SCHEMA_VERSION,
    Aggregate,
    Category,
    Change,
    ChangedField,
    CricketInnings,
    CricketScore,
    Event,
    EventParticipant,
    EventParticipants,
    FightScore,
    FootballScore,
    InningScore,
    InningsScore,
    LiveEvent,
    Model,
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

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_ID",
    "EVENT_ENVELOPE_ID",
    "MODELS",
    "RECORDS",
    "DEFAULT_REFRESH_WINDOW_S",
    # modeller
    "Model",
    "Sport",
    "Category",
    "Tournament",
    "Season",
    "Participant",
    "Event",
    "Status",
    "EventParticipants",
    "EventParticipant",
    "Stage",
    "Round",
    "Aggregate",
    "Quality",
    "Score",
    "ScorePair",
    "PlainScore",
    "FootballScore",
    "PeriodsScore",
    "PeriodScore",
    "SetsScore",
    "SetScore",
    "InningsScore",
    "InningScore",
    "CricketScore",
    "CricketInnings",
    "FightScore",
    "Slice",
    "SliceError",
    "Change",
    "ChangedField",
    "LiveEvent",
    # eşleyiciler
    "sport_from_spec",
    "sport_from_row",
    "registered_sports",
    "category_from_row",
    "tournament_from_row",
    "season_from_row",
    "participant_from_row",
    "event_from_row",
    "settlement",
    "slice_from_info",
    "change_from_row",
    "live_event_from_record",
    "utc_text",
    # JSON Schema
    "json_schema",
    "record_schema",
    "describe",
]
