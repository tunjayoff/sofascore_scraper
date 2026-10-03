"""
Maçların API'si: `EventStore` (okuma ve yazma) ve bütün okuma API'lerinin ortak türleri
(docs/design/01-storage.md, bölüm 2.3, 3.7, 5.3, 6.2, 6.3 ve 8.4).

Her soru kataloğa sorulur; dosya ağacı gezilmez. Katalog bir dizindir: açılışta dosyalardan kurulur ya da
uzlaştırılır, sonra her yazma onu günceller (src/store/indexer.py). Kurulmamış katalogda bütün sorular boş
yanıt verir.

Yükler dosyadan okunur, ama yerini katalog söyler: `event_slices` satırı "yük var" diyorsa dosya açılır
(v3: `v3/events/.../<id>/<anahtar>.json.gz`; eski düzen: `<dizin>/<anahtar>.json` ya da birleşik dosya),
demiyorsa dosyaya bakılmadan None döner. Satır dosyayı gösterirken dosya yerinde değilse (maç o sırada
v3'e taşınmış ya da silinmiş olabilir) satır bir kez yeniden okunur ve yeni yeriyle bir kez daha denenir;
yine yoksa PayloadMissing (bölüm 6.3).

Okuyucular kilit almaz. Tek bir çağrının sorguları aynı anlık görüntüyü görür (`Catalog.read`); parça
parça okuyan çağrılar (`iter`, `states`) her parçada yeni bir görüntü alır, böylece uzun bir dışa aktarma
WAL'ı tutmaz. Parçalar arasında yazılan bir satır bu yüzden iki kez gelmez ama atlanabilir ya da eski
haliyle gelebilir.

Sorguların yazımı bölüm 3.7'deki dizinlere göredir ve tests/test_store_read_api.py her birinin planını
sabitler. Planlayıcının kendiliğinden çıkaramadığı üç kural: kısmi dizinlerin koşulu sorguda dizindeki
yazımıyla ve sabit olarak geçer (`state != 'ok'`; `has_event_payload = 1 AND observed_at IS NOT NULL`;
`status_class IN ('not_started', 'live', 'unknown')`, bu sırayla); durum sınıfları bu yüzden bağlı
parametre olarak değil, bilinen değerler kümesinden doğrulanmış sabitler olarak yazılır.

Store politika bilmez: hangi dilimlerin gerektiği (`missing(required=...)`), yenileme penceresi
(`refresh_candidates(window_s=...)`) ve "bitmiş" sayılan durum sınıfları çağırandan gelir.

Yazma (`put`, `observe`, `reset_empty_markers`, `delete`). Olay yükleri diske yalnızca `put` ile ulaşır ve her
yazma v3 düzenine gider (`v3/events/.../<id>/`, bölüm 4.2). Bir maça yazmanın protokolü (bölüm 6.2):

  1. Katalogda yarım yazma işareti (`pending_writes`), kendi işleminde.
  2. `BEGIN IMMEDIATE`: kataloğun yazma kilidi, veri dizinindeki bütün yazmaların süreçler arası kilididir.
  3. Maçın `manifest.json`'ı diskten okunur. Maç eski düzendeyse önce v3'e yükseltilir (bölüm 5.3): bütün
     dosyaları okunur, v3 dizini `.meta/tmp` altında kurulur, geri okunarak doğrulanır ve tek yeniden
     adlandırmayla yerine konur. Eski dizine dokunulmaz.
  4. Olay yükü değişiyor ve `on_event_change` bir satır döndürdüyse satır, yeni yükün özetiyle birlikte
     niyet dosyasına yazılır (`.meta/pending_changes/<id>.json`). Sonra değişen yük dosyaları yazılır (geçici
     dosya + yerine koyma).
  5. Yeni manifest yazılır.
  6. Varsa değişiklik günlüğü satırı eklenir ve niyet dosyası silinir; katalog satırları dosyalardan yeniden
     türetilir, işaret silinir; commit.

Değişiklik satırı bu yüzden kaybolmaz: 4 ile 6 arasında kesilen yazmanın niyetini, aynı maça bir sonraki yazma
ya da bir sonraki uzlaştırma kapatır (`recover_change`): diskteki olay yükü niyettekiyse satır günlükte yoksa
eklenir, yük hiç değişmediyse niyet atılır. Satır günlüğe en çok bir kez girer.

Manifest kilit altında okunup yazıldığı için aynı maçın farklı dilimlerini yazan iki süreç birbirinin kaydını
kaybetmez. 1 ile 6 arasında ölen süreç işareti bırakır; bir sonraki açılış ya da aynı maça bir sonraki yazma
maçı dosyalardan toparlar (`indexer.heal_v3_event`). Katalog satırları elle kurulmaz, dizinleyicinin aynı
dosyalardan türettiği satırlardır: yazmadan sonra katalog, ağacın sıfırdan kurulmuş haline eşittir.

Hazırlık dizinleri sahibinin adını taşır (karar S16): süreç `writer` kilidini tutuyorsa `writer.<rastgele>`,
`live` kilidini tutuyorsa `live.<rastgele>`, hiçbirini tutmuyorsa `put.<rastgele>`. Kilidi alan yalnızca
kendi adını taşıyan girdileri siler (src/store/lease.py).
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import copy
import dataclasses
import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Collection,
    Dict,
    Generic,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)

from src.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from src.status import StatusClass
from src.store import codec, derive, files, layout, legacy
from src.store import manifest as manifest_mod
from src.store.errors import LayoutError, PayloadCorrupt, PayloadMissing, StoreBusy, StoreError, UnknownEvent
from src.store.legacy import LegacyEvent, LegacyReader
from src.store.manifest import EmptyMark, ErrorMark, Manifest, Observation, SliceEntry

if TYPE_CHECKING:
    from src.store.api import Store

logger = logging.getLogger(__name__)

T = TypeVar("T")

EVENT_KEY = legacy.EVENT_KEY
LAYOUT_V3 = "v3"
LAYOUT_LEGACY = "legacy"

STATE_NOT_REQUESTED = "not_requested"  # katalogda satırı olmayan dilimin bildirilen durumu
SORT_START_DESC = "start_desc"
SORT_START_ASC = "start_asc"
SORTS: Tuple[str, ...] = (SORT_START_DESC, SORT_START_ASC)
FINISHED_CLASSES: Tuple[str, ...] = (StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value)
DEFAULT_EMPTY_THRESHOLD = 2  # bu kadar kesin "veri yok" yanıtından sonra dilim artık beklenmez
DEFAULT_BATCH = 1000

_STATUS_ORDER: Tuple[str, ...] = tuple(member.value for member in StatusClass)
# `events_open` dizininin koşulu, dizindeki yazımıyla (başka sıra ya da bağlı parametre dizine ulaşmaz)
_OPEN_CLASSES: Tuple[str, ...] = ("not_started", "live", "unknown")
_OPEN_SQL = "e.status_class IN ('not_started', 'live', 'unknown')"
_UNSETTLED_SQL = "e.has_event_payload = 1 AND e.observed_at IS NOT NULL"  # `events_unsettled`
_UNOBSERVED_SQL = "e.has_event_payload = 1 AND e.observed_at IS NULL"  # `events_unobserved`
_FOLLOWED_SQL = ("e.tournament_id IN (SELECT f.entity_id FROM state.follows f "
                 "WHERE f.kind = 'tournament' AND f.enabled = 1)")
_TEXT_SQL = ("e.id IN (SELECT ep.event_id FROM participants p "
             "JOIN event_participants ep ON ep.participant_id = p.id WHERE p.name_folded LIKE ? ESCAPE '\\')")
_SORT_CODES = {SORT_START_DESC: "d", SORT_START_ASC: "a"}
SLICE_COLUMNS = ("key, sub, state, has_payload, fetched_at, checked_at, empty_count, unverified_empty_count, "
                 "error_reason, error_status, error_at, error_count, stored_bytes, raw_bytes, history_count, meta_json")
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1

Cursor = Tuple[Optional[int], int]  # (start_ts, id): sayfanın son satırı


# --- ortak türler -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ref:
    """
    Bir varlığın adresi: tür + kimlik. `tournament_id` yalnızca sezonda anlamlıdır (sezonun v3 dizini
    turnuvanın altındadır); okuma için gerekmez, yazan API'ler ister.
    """

    kind: str  # event | tournament | season | team | player | sport
    id: int
    tournament_id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.kind not in layout.KINDS:
            raise ValueError(f"kind: expected one of {', '.join(layout.KINDS)}, got {self.kind!r}")
        _int(self.id, "id")
        if self.tournament_id is not None:
            _int(self.tournament_id, "tournament_id")

    @classmethod
    def event(cls, event_id: int) -> "Ref":
        return cls("event", event_id)

    @classmethod
    def tournament(cls, tournament_id: int) -> "Ref":
        return cls("tournament", tournament_id)

    @classmethod
    def season(cls, tournament_id: int, season_id: int) -> "Ref":
        return cls("season", season_id, tournament_id)

    @classmethod
    def team(cls, team_id: int) -> "Ref":
        return cls("team", team_id)

    @classmethod
    def player(cls, player_id: int) -> "Ref":
        return cls("player", player_id)

    @classmethod
    def sport(cls, sport_id: int) -> "Ref":
        return cls("sport", sport_id)


@dataclass(frozen=True)
class SliceError:
    """Dilimin son başarısız isteği (ya da okunamayan dosyası: reason "corrupt")."""

    reason: str
    http_status: Optional[int] = None
    at: Optional[datetime] = None
    count: int = 1


@dataclass(frozen=True)
class SliceInfo:
    """Bir varlığın bir diliminin katalogdaki durumu. Satırı olmayan dilim `not_requested` olarak bildirilir."""

    ref: Ref
    key: str
    sub: str
    state: str  # ok | empty | error | not_requested
    has_payload: bool
    fetched_at: Optional[datetime]  # saklanan yükün alındığı an
    checked_at: Optional[datetime]  # sonucu ne olursa olsun son deneme
    empty_count: int  # kesin "veri yok" yanıtları
    unverified_empty_count: int  # kesin yanıtın desteklemediği eski sayımlar
    error: Optional[SliceError]
    stored_bytes: Optional[int]
    raw_bytes: Optional[int]
    history_count: int
    meta: Mapping[str, Any] = field(default_factory=dict)

    def settled_empty(self, threshold: int = DEFAULT_EMPTY_THRESHOLD) -> bool:
        """Yeterince denendi ve hep boş geldi: dilim bu varlıkta artık beklenmez."""
        return self.empty_count + self.unverified_empty_count >= threshold


@dataclass(frozen=True)
class Scope:
    """
    Sorgunun kapsamı; boş alan süzmez, dolu alanlar birlikte (VE) uygulanır.

    followed: yalnızca etkin turnuva takiplerinin maçları (`state.db`'deki `follows` tablosu). Takım, oyuncu
    ve maç takipleri kapsamı genişletmez.
    """

    sport: Optional[str] = None
    tournament_ids: Sequence[int] = ()
    season_ids: Sequence[int] = ()
    event_ids: Sequence[int] = ()
    participant_ids: Sequence[int] = ()
    followed: bool = False


@dataclass(frozen=True)
class EventQuery:
    """
    Maç listesi sorgusu.

    status_classes: boş = hepsi; "yalnızca bitmiş" = ("completed", "decided_without_play").
    start_from / start_to: başlangıç zamanı aralığı (epoch saniye), iki uç dahil.
    text: yarışmacı adında geçen metin; büyük-küçük harf ve aksan ayrımı yok. Yarışmacı kimliği olmayan
        satırlar (özet CSV'sinden gelen liste satırları) adla bulunmaz.
    has_details: True = `/event/{id}` yükü olanlar, False = yalnızca listeden bilinenler.
    cursor: `Page.next_cursor`'dan gelen opak konum (sıralamaya bağlıdır). offset: eski sayfalama; ikisi
        birlikte verilemez.
    """

    scope: Scope = Scope()
    status_classes: Sequence[str] = ()
    start_from: Optional[float] = None
    start_to: Optional[float] = None
    round: Optional[int] = None
    text: Optional[str] = None
    has_details: Optional[bool] = None
    updated_after: Optional[float] = None
    sort: str = SORT_START_DESC
    limit: int = 50
    cursor: Optional[str] = None
    offset: Optional[int] = None


@dataclass(frozen=True)
class Page(Generic[T]):
    """Bir sayfa. next_cursor: sonraki sayfa varsa onun konumu (offset kipinde hep None). total: istenirse."""

    items: Tuple[T, ...]
    next_cursor: Optional[str] = None
    total: Optional[int] = None


@dataclass(frozen=True)
class EventRow:
    """
    `events` tablosunun bir satırı, sütun sırasıyla (src/store/schema/catalog.sql). Zamanlar epoch saniyedir
    (UTC). `row_source = 'listing'` ve `has_event_payload = False`: maç yalnızca bir program sayfasından
    biliniyor.
    """

    id: int
    sport: str
    category_id: Optional[int]
    tournament_id: Optional[int]
    stage_id: Optional[int]
    stage_name: Optional[str]
    season_id: Optional[int]
    round: Optional[int]
    round_name: Optional[str]
    round_slug: Optional[str]
    start_ts: Optional[int]
    status_type: Optional[str]
    status_code: Optional[int]
    status_description: Optional[str]
    status_class: str
    home_id: Optional[int]
    away_id: Optional[int]
    home_name: Optional[str]
    away_name: Optional[str]
    home_score: Optional[int]
    away_score: Optional[int]
    home_score_current: Optional[int]
    away_score_current: Optional[int]
    winner_code: Optional[int]
    scores_json: Optional[str]
    slug: Optional[str]
    custom_id: Optional[str]
    observed_at: Optional[int]
    change_ts: Optional[int]
    observed_gap: Optional[int]
    status_regressed: bool
    tier_hint: Optional[int]
    stale: bool
    row_source: str
    listed_in: Optional[str]
    has_event_payload: bool
    layout: Optional[str]
    path: Optional[str]
    legacy_path: Optional[str]
    sig: Optional[str]
    first_seen_at: int
    updated_at: int

    def scores(self) -> Optional[Dict[str, Any]]:
        """`scores_json` ayrıştırılmış halde: spor ailesine özgü skor çizelgesi; yoksa None."""
        if not self.scores_json:
            return None
        try:
            parsed = json.loads(self.scores_json)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None


@dataclass(frozen=True)
class EventState:
    """Planlama için bir maç: katalog satırı ve bütün dilim satırları."""

    event: EventRow
    slices: Tuple[SliceInfo, ...]

    def slice(self, key: str, sub: str = "") -> SliceInfo:
        """Dilimin durumu; satırı yoksa `not_requested`."""
        for info in self.slices:
            if info.key == key and info.sub == sub:
                return info
        return not_requested(Ref.event(self.event.id), key, sub)


@dataclass(frozen=True)
class MissingRow:
    """`EventStore.missing` sonucu: maçın eksikleri. has_event_payload False ise olay yükü de yok."""

    event_id: int
    sport: str
    has_event_payload: bool
    missing_keys: Tuple[str, ...]


@dataclass(frozen=True)
class TournamentSummary:
    """
    Bir turnuvanın maç sayıları (gösterge paneli). tournament_id None: benzersiz turnuvası olmayan maçlar.
    events: bütün satırlar (yükü olanlar ve yalnızca listeden bilinenler); with_payload: `/event/{id}` yükü
    olanlar; finished: durum sınıfı completed ya da decided_without_play olanlar; seasons: maçlarda geçen
    farklı sezon sayısı.
    """

    tournament_id: Optional[int]
    events: int
    with_payload: int
    finished: int
    seasons: int
    by_status: Mapping[str, int]
    first_start_ts: Optional[int]
    last_start_ts: Optional[int]
    updated_at: Optional[int]


# --- doğrulama ----------------------------------------------------------------------------------------

def check_int(value: Any, what: str, *, minimum: Optional[int] = None) -> int:
    """
    Tam sayı argümanı (kimlik, sınır, sıra numarası): bool değil, SQLite'ın saklayabildiği 64 bit aralıkta ve
    verildiyse `minimum`dan küçük değil; değilse ValueError. Okuma API'lerinin ortak denetimi.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not _INT64_MIN <= value <= _INT64_MAX:
        raise ValueError(f"{what}: expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{what}: expected an integer >= {minimum}, got {value!r}")
    return value


_int = check_int


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ValueError(f"{what}: expected a number, got {value!r}")
    return value


def int_list(values: Iterable[Any], what: str) -> str:
    """
    Kimlik listesi, SQL'e sabit olarak yazılacak biçimde: "1, 2, 3". Her öğe tam sayı olarak doğrulanır;
    böylece liste SQLite'ın bağlı parametre sınırına takılmaz (3.32 öncesinde 999).
    """
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{what}: expected a sequence of integers, got {values!r}")
    return ", ".join(str(_int(value, what)) for value in values)


def like_pattern(text: object) -> Optional[str]:
    """Arama metni → `name_folded LIKE ? ESCAPE '\\'` kalıbı (katlanmış, joker karakterleri kaçırılmış); boşsa None."""
    folded = derive.fold_name(text)
    if not folded:
        return None
    escaped = folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _status_sql(status_classes: Sequence[str]) -> Optional[str]:
    """
    Durum sınıfı süzgeci, sabitlerle yazılmış. Tek sınıf eşitlik olur (`status_class = 'live'` ancak böyle
    `events_live`'a ulaşır); açık üç sınıf `events_open`'ın yazımıyla; gerisi enum sırasıyla bir IN listesi.
    """
    if isinstance(status_classes, str):
        raise ValueError(f"status_classes: expected a sequence of status classes, got {status_classes!r}")
    wanted = {str(getattr(item, "value", item)) for item in status_classes}
    unknown = sorted(wanted - set(_STATUS_ORDER))
    if unknown:
        raise ValueError(f"status_classes: unknown status class {unknown[0]!r}")
    if not wanted or wanted == set(_STATUS_ORDER):
        return None
    if len(wanted) == 1:
        return f"e.status_class = '{next(iter(wanted))}'"
    if wanted == set(_OPEN_CLASSES):
        return _OPEN_SQL
    return "e.status_class IN ({})".format(", ".join(f"'{name}'" for name in _STATUS_ORDER if name in wanted))


def _scope_sql(scope: Optional[Scope]) -> Tuple[List[str], List[Any]]:
    """Kapsamın koşulları (`events e` üzerinde) ve bağlı değerleri."""
    conditions: List[str] = []
    params: List[Any] = []
    if scope is None:
        return conditions, params
    if scope.sport is not None:
        if not isinstance(scope.sport, str):
            raise ValueError(f"sport: expected a sport slug or None, got {scope.sport!r}")
        conditions.append("e.sport = ?")
        params.append(scope.sport)
    for column, values, what in (("e.tournament_id", scope.tournament_ids, "tournament_ids"),
                                 ("e.season_id", scope.season_ids, "season_ids"),
                                 ("e.id", scope.event_ids, "event_ids")):
        listed = int_list(values, what)
        if listed:
            conditions.append(f"{column} IN ({listed})" if "," in listed else f"{column} = {listed}")
    participants = int_list(scope.participant_ids, "participant_ids")
    if participants:
        conditions.append("e.id IN (SELECT ep.event_id FROM event_participants ep "
                          f"WHERE ep.participant_id IN ({participants}))")
    if scope.followed:
        conditions.append(_FOLLOWED_SQL)
    return conditions, params


def _query_sql(q: EventQuery) -> Tuple[List[str], List[Any]]:
    """Sorgunun süzgeçleri (sayfalama ve sıralama hariç)."""
    conditions, params = _scope_sql(q.scope)
    status = _status_sql(q.status_classes)
    if status:
        conditions.append(status)
    if q.start_from is not None:
        conditions.append("e.start_ts >= ?")
        params.append(_number(q.start_from, "start_from"))
    if q.start_to is not None:
        conditions.append("e.start_ts <= ?")
        params.append(_number(q.start_to, "start_to"))
    if q.round is not None:
        conditions.append("e.round = ?")
        params.append(_int(q.round, "round"))
    if q.text is not None:
        pattern = like_pattern(q.text)
        if pattern is not None:
            conditions.append(_TEXT_SQL)
            params.append(pattern)
    if q.has_details is not None:
        conditions.append(f"e.has_event_payload = {1 if q.has_details else 0}")
    if q.updated_after is not None:
        conditions.append("e.updated_at > ?")
        params.append(_number(q.updated_after, "updated_after"))
    return conditions, params


def _where(conditions: Sequence[str]) -> str:
    return (" WHERE " + " AND ".join(conditions)) if conditions else ""


def _check_sort(sort: str) -> str:
    if sort not in _SORT_CODES:
        raise ValueError(f"sort: expected one of {', '.join(SORTS)}, got {sort!r}")
    return sort


def _check_limit(value: Any, what: str = "limit") -> int:
    if _int(value, what) < 1:
        raise ValueError(f"{what}: expected a positive integer, got {value!r}")
    return int(value)


def encode_cursor(sort: str, start_ts: Optional[int], event_id: int) -> str:
    """Sayfanın son satırından opak konum. Sıralama da içindedir: başka sıralamayla kullanılamaz."""
    raw = json.dumps([_SORT_CODES[sort], start_ts, event_id], separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, sort: str) -> Cursor:
    """`encode_cursor`ın tersi; bozuk ya da başka sıralamaya ait konumda ValueError."""
    try:
        if not isinstance(cursor, str) or not cursor:
            raise ValueError("empty")
        padded = cursor + "=" * (-len(cursor) % 4)
        code, start_ts, event_id = json.loads(base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True))
        if code != _SORT_CODES[sort]:
            raise ValueError("sort")
        if start_ts is not None:
            _int(start_ts, "start_ts")
        return start_ts, _int(event_id, "id")
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise ValueError(f"cursor: not a cursor of this query ({sort}): {cursor!r}") from exc


# --- satırlardan türler --------------------------------------------------------------------------------

EVENT_COLUMNS: Tuple[str, ...] = tuple(f.name for f in dataclasses.fields(EventRow))
_EVENT_SELECT = "SELECT " + ", ".join(f"e.{name}" for name in EVENT_COLUMNS) + " FROM events e"
_EVENT_FLAGS = tuple(index for index, f in enumerate(dataclasses.fields(EventRow)) if f.type == "bool")


def _event_row(row: Sequence[Any]) -> EventRow:
    values = list(row)
    for index in _EVENT_FLAGS:
        values[index] = bool(values[index])
    return EventRow(*values)


def _moment(value: Any) -> Optional[datetime]:
    """Epoch saniye → UTC `datetime`; temsil edilemeyen değerde None."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def slice_info(ref: Ref, row: Mapping[str, Any]) -> SliceInfo:
    """`event_slices` / `entity_slices` satırından `SliceInfo` (iki tablonun ortak sütunları)."""
    meta: Any = None
    if row["meta_json"]:
        try:
            meta = json.loads(row["meta_json"])
        except ValueError:
            meta = None
    error = None
    if row["error_reason"] is not None:
        error = SliceError(reason=str(row["error_reason"]), http_status=row["error_status"],
                           at=_moment(row["error_at"]), count=int(row["error_count"] or 0))
    return SliceInfo(
        ref=ref,
        key=str(row["key"]),
        sub=str(row["sub"]),
        state=str(row["state"]),
        has_payload=bool(row["has_payload"]),
        fetched_at=_moment(row["fetched_at"]),
        checked_at=_moment(row["checked_at"]),
        empty_count=int(row["empty_count"] or 0),
        unverified_empty_count=int(row["unverified_empty_count"] or 0),
        error=error,
        stored_bytes=row["stored_bytes"],
        raw_bytes=row["raw_bytes"],
        history_count=int(row["history_count"] or 0),
        meta=meta if isinstance(meta, dict) else {},
    )


def not_requested(ref: Ref, key: str, sub: str = "") -> SliceInfo:
    """Katalogda satırı olmayan dilim."""
    return SliceInfo(ref=ref, key=key, sub=sub, state=STATE_NOT_REQUESTED, has_payload=False, fetched_at=None,
                     checked_at=None, empty_count=0, unverified_empty_count=0, error=None, stored_bytes=None,
                     raw_bytes=None, history_count=0)


ABSENT: Any = object()  # "katalogda bu dilimin yükü yok": içeriği JSON `null` olan yükten ayırt edilir


def read_with_retry(locate: Callable[[], Optional[T]], read: Callable[[T], Any], absent: Any = None) -> Any:
    """
    Bölüm 6.3: yükün yeri katalogdan alınır (`locate`; None = yük yok) ve okunur. Dosya o yerde yoksa
    (PayloadMissing) satır bir kez yeniden okunur ve yeni yerle bir kez daha denenir. O da başarısızsa
    PayloadMissing çağırana gider. Katalog yük göstermiyorsa (ilk bakışta ya da yeniden bakışta) `absent` döner.
    """
    where = locate()
    if where is None:
        return absent
    try:
        return read(where)
    except PayloadMissing:
        pass
    where = locate()
    if where is None:
        return absent
    return read(where)


@dataclass(frozen=True)
class _EventFile:
    """Bir maç diliminin yükünün durduğu yer (katalog satırlarından)."""

    layout: str
    path: Optional[str]  # eski düzende maç dizini; v3'te None (yol kimlikten türer)


# --- yazma: türler ve yardımcılar ----------------------------------------------------------------------

SliceKey = Union[str, Tuple[str, str]]  # dilim anahtarı ya da (anahtar, alt anahtar)
# Olay yükü değişmek üzereyken çağrılır: (saklanan yük ya da None, yeni yük) → değişiklik günlüğü satırı ya da None
EventChange = Callable[[Optional[Mapping[str, Any]], Mapping[str, Any]], Optional[Mapping[str, Any]]]

PENDING_KIND = "event"  # `pending_writes.kind`
STAGING_WRITER = "writer"  # hazırlık dizininin etiketi (karar S16): süreç `writer` kilidini tutuyor
STAGING_LIVE = "live"  # süreç `live` kilidini tutuyor
STAGING_UNLEASED = "put"  # süreç ikisini de tutmuyor; girdi yalnızca bir günden eskiyse silinir
BREAKER_REASON = "breaker"  # açık devre kesici: istek gönderilmedi (src/breaker.BREAKER_OPEN)
_MARKER_ATTEMPTS = 5

# `_checkpoint` adımları, protokolün sırasıyla (testler süreci bu noktalarda öldürür)
STEP_MARKER = "marker"  # işaret yazıldı
STEP_LOCKED = "locked"  # yazma kilidi alındı
STEP_CHANGE_INTENT = "change_intent"  # değişiklik satırının niyet dosyası yazıldı (diske ilk dokunuş)
STEP_STAGED = "staged"  # v3 dizini hazırlık alanında kuruldu (yükseltme ya da yeni maç)
STEP_PUBLISHED = "published"  # hazırlık dizini yerine kondu
STEP_PAYLOAD = "payload"  # bir yük dosyası yerine yazıldı ("payload:<dilim adı>")
STEP_HISTORY = "history"  # geçmiş dosyasına bir üye eklendi ("history:<dilim adı>")
STEP_MANIFEST = "manifest"  # manifest yazıldı
STEP_CHANGE_LOG = "change_log"  # değişiklik günlüğü satırı dosyaya eklendi
STEP_INDEXED = "indexed"  # katalog satırları yazıldı
STEP_COMMIT = "commit"  # işaret silindi, commit'ten hemen önce
STEP_DONE = "done"  # commit edildi

# Değişiklik satırının niyet dosyaları (bölüm 6.2): `<dizin>/<maç kimliği>.json`, DATA_DIR'e göre
CHANGE_INTENT_DIR = f"{layout.META_DIR}/pending_changes"
_LOGGED_ROWS_CHECKED = 20  # niyetteki satır günlükte mi: maçın son bu kadar satırına bakılır


@dataclass(frozen=True)
class PutResult:
    """`EventStore.put` / `observe` sonucu."""

    created: bool  # maçın saklanan yükü yoktu (yeni v3 dizini kuruldu; yükseltme sayılmaz)
    event_written: bool  # `event` yükünün dosyası değişti
    superseded: bool  # `event` sonucu yok sayıldı: daha yeni bir gözlem saklı
    written: Tuple[str, ...]  # yük dosyası değişen dilimlerin adları ("statistics", "odds_all/1")
    change_seq: Optional[int]  # değişiklik günlüğüne satır yazıldıysa sıra numarası (ya da toparlanan satırınki)
    promoted: bool  # maç bu çağrıda eski düzenden v3'e yükseltildi
    history: Tuple[str, ...] = ()  # geçmiş dosyasına anlık görüntü eklenen dilimlerin adları (`keep_history`)


_NOTHING_WRITTEN = PutResult(created=False, event_written=False, superseded=False, written=(), change_seq=None,
                             promoted=False)


@dataclass(frozen=True)
class _Item:
    """Uygulanacak bir sonuç: doğrulanmış dilim adı ve sayılıp sayılmayacağı."""

    name: str
    key: str
    sub: str
    outcome: Outcome
    counted: bool


@dataclass
class _Write:
    """Bir yazmanın durumu (`EventStore._entity_write`)."""

    recover: bool  # işaret zaten duruyordu: önceki yazma yarım kalmış olabilir
    touched: bool = False  # diske dokunuldu: hata olursa işaret kalır ve maç sonradan toparlanır
    recovered_seq: Optional[int] = None  # yarım kalmış önceki yazmanın günlüğe eklenen satırı (`recover_change`)


@dataclass
class _Opened:
    """Yazılacak maçın bulunduğu hal."""

    manifest: Manifest  # üzerinde çalışılacak manifest (bellekte)
    legacy: Optional[LegacyEvent] = None  # eski düzende duruyor: yazmadan önce yükseltilecek
    encoded: Optional[Dict[str, codec.Encoded]] = None  # eski düzendeki yüklerin yazılmaya hazır hali
    created: bool = False  # hiçbir düzende saklanan yükü yok: yeni v3 dizini kurulacak
    adopt: bool = False  # v3 dizini var ama manifesti yok ya da okunamıyor: yük dosyaları sonradan kaydedilir


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Any, default: datetime, what: str) -> datetime:
    """Sonucun zamanı: None "şimdi"dir; saat dilimi olmayan zaman UTC sayılır."""
    if value is None:
        return default
    if not isinstance(value, datetime):
        raise ValueError(f"{what}: expected a datetime, got {value!r}")
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _slice_file(root: str, key: str, sub: str = "") -> str:
    """Varlık dizini (gerçek yol) içindeki yük dosyası."""
    return os.path.join(root, *f"{layout.slice_name(key, sub)}{layout.PAYLOAD_SUFFIX}".split("/"))


def _counts_for(count_empties: Union[bool, Collection[str]]) -> Callable[[str], bool]:
    if isinstance(count_empties, bool):
        return lambda key: bool(count_empties)
    if isinstance(count_empties, (str, bytes)):
        raise ValueError(f"count_empties: expected a bool or a collection of slice keys, got {count_empties!r}")
    wanted = {layout.validate_key(key) for key in count_empties}
    return wanted.__contains__


def _history_keys(keep_history: Collection[str]) -> frozenset:
    """`keep_history`: geçmişi tutulacak dilim anahtarları (alt anahtarsız; anahtarın bütün alt anahtarlarını kapsar)."""
    if isinstance(keep_history, (str, bytes)) or not isinstance(keep_history, Collection):
        raise ValueError(f"keep_history: expected a collection of slice keys, got {keep_history!r}")
    return frozenset(layout.validate_key(key) for key in keep_history)


def _items(event_id: int, outcomes: Mapping[SliceKey, Outcome],
           count_empties: Union[bool, Collection[str]]) -> List[_Item]:
    """
    `put`'un sonuçlarını doğrular ve uygulanacakları döndürür: `event` önce, gerisi verildiği sırayla.
    İstek gönderilmemiş sonuçlar (`skipped`; bugünkü çağıranların `failed` / `breaker`'ı) listeye girmez.
    """
    if not isinstance(outcomes, Mapping):
        raise ValueError(f"outcomes: expected a mapping, got {type(outcomes).__name__}")
    counted = _counts_for(count_empties)
    found: Dict[str, _Item] = {}
    for raw, outcome in outcomes.items():
        if isinstance(raw, str):
            key, sub = raw, ""
        elif isinstance(raw, tuple) and len(raw) == 2:
            key, sub = raw
        else:
            raise ValueError(f"outcomes: expected a slice key or a (key, sub) pair, got {raw!r}")
        name = layout.slice_name(key, sub)
        if name in found:
            raise ValueError(f"outcomes: slice {name!r} is given twice")
        if not isinstance(outcome, Outcome):
            raise ValueError(f"outcomes[{name!r}]: expected an Outcome, got {type(outcome).__name__}")
        status = outcome.status
        if status not in (SLICE_OK, SLICE_EMPTY, SLICE_FAILED, SLICE_SKIPPED):
            raise ValueError(f"outcomes[{name!r}]: unknown status {status!r}")
        if status == SLICE_SKIPPED or (status == SLICE_FAILED and outcome.reason == BREAKER_REASON):
            continue
        if status == SLICE_OK and outcome.data is None:
            raise ValueError(f"outcomes[{name!r}]: an ok outcome needs data")
        if name == EVENT_KEY:
            payload = outcome.data
            if status != SLICE_OK:
                raise ValueError(f"outcomes['event']: the event outcome must be ok, got {status!r}")
            if (not isinstance(payload, Mapping) or isinstance(payload.get("id"), bool)
                    or payload.get("id") != event_id):
                got = payload.get("id") if isinstance(payload, Mapping) else type(payload).__name__
                raise ValueError(f"outcomes['event']: the payload's id ({got!r}) is not {event_id}")
        found[name] = _Item(name, key, sub, outcome, counted(key))
    return sorted(found.values(), key=lambda item: item.name != EVENT_KEY)


def _whole_second(value: Optional[float]) -> Optional[datetime]:
    """Dosya zamanı (epoch saniye) → UTC zaman, tam saniyeye indirilmiş (katalogdaki değerle aynı)."""
    if value is None:
        return None
    return datetime.fromtimestamp(math.floor(value), timezone.utc)


def _natural(value: Optional[int]) -> Optional[int]:
    """Manifestin kabul ettiği tam sayı (negatif değil); değilse None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _change_ts(payload: Mapping[str, Any]) -> Optional[int]:
    """Olay yükündeki `changes.changeTimestamp`; negatif olmayan bir tam sayı değilse None."""
    changes = payload.get("changes")
    return _natural(changes.get("changeTimestamp")) if isinstance(changes, Mapping) else None


def manifest_from_legacy(event: LegacyEvent) -> Tuple[Manifest, Dict[str, codec.Encoded]]:
    """
    Eski düzendeki bir maçın manifesti (bölüm 2.3'teki eşleme) ve yüklerinin yazılmaya hazır hali (dilim
    anahtarı → kurallı baytlar, gzip, özet). `event`, yükleriyle okunmuş olmalıdır.

    Manifest dilim durumlarını, sayaçları, hata kayıtlarını ve gözlemi taşır. Zamanlar katalogdaki eski düzen
    satırlarının değerleridir (dosya zamanları, tam saniye): `created_at` en eski, `updated_at` en yeni yük
    dosyasının zamanı; `checked_at` yükün, "veri yok" işaretinin ve hata işaretinin zamanlarından en yenisi.
    Böylece yükseltilen maçın katalog satırları, düzen sütunları dışında değişmez.
    """
    if event.payloads is None:
        raise ValueError("event: expected an event read with its payloads")
    slices: Dict[str, SliceEntry] = {}
    encoded: Dict[str, codec.Encoded] = {}
    times: List[datetime] = []
    for entry in event.slices:
        fetched = _whole_second(entry.fetched_at) if entry.has_payload else None
        out = SliceEntry(state=entry.state, fetched_at=fetched)
        if entry.has_payload:
            ready = encoded[entry.key] = codec.encode(event.payloads[entry.key])
            out.stored_bytes, out.raw_bytes, out.sha256 = ready.stored_bytes, ready.raw_bytes, ready.sha256
        error = entry.error
        seen = [moment for moment in (fetched, entry.empty_at, error.at if error else None) if moment is not None]
        out.checked_at = max(seen) if seen else None
        if entry.empty_count or entry.unverified_empty_count:
            out.empty = EmptyMark(count=max(entry.empty_count, 0), unverified=max(entry.unverified_empty_count, 0),
                                  at=entry.empty_at)
        if error is not None:
            out.error = ErrorMark(reason=error.reason, status=_natural(error.status), at=error.at,
                                  count=max(error.count, 1))
        if fetched is not None:
            times.append(fetched)
        slices[entry.key] = out
    start = datetime.fromtimestamp(0, timezone.utc)
    found = Manifest(kind="event", id=event.event_id, created_at=min(times) if times else start,
                     updated_at=max(times) if times else start, migrated_from=event.path, slices=slices)
    observation = event.observation
    if observation is not None:
        found.observation = Observation(observed_at=observation.observed_at,
                                        change_ts=_natural(observation.change_ts),
                                        status_regressed=bool(observation.status_regressed))
    return found, encoded


def promote_legacy(data_dir: Union[str, "os.PathLike[str]"], event: LegacyEvent, *, label: str,
                   checkpoint: Optional[Callable[[str], None]] = None,
                   prepared: Optional[Tuple[Manifest, Mapping[str, codec.Encoded]]] = None) -> Manifest:
    """
    Eski düzendeki bir maçın v3 kopyasını kurar ve yerine koyar (bölüm 5.3); manifestini döndürür. `event`,
    `LegacyReader.read_event(..., payloads=True)` sonucudur; `prepared`, aynı maç için `manifest_from_legacy`
    sonucudur (verilmezse burada hesaplanır). Katalogun yazma kilidi altında çağrılır; katalog satırlarını
    çağıran günceller.

    Dizin `.meta/tmp/<label>.<rastgele>` altında kurulur: yükü olan her dilim için bir `.json.gz` ve
    `migrated_from` taşıyan manifest. Yayımlamadan önce doğrulanır (bölüm 5.4, adım 3): her dosya diskten geri
    okunur, açılır, ayrıştırılır ve eski nesneyle karşılaştırılır; özeti manifesttekiyle karşılaştırılır.
    Uyuşmazlıkta hazırlık dizini silinir ve StoreError fırlatılır. Sonra tek yeniden adlandırmayla yerine
    konur. Eski dizine dokunulmaz ve silinmez; dilim, gözlem ya da işaret dosyası olmayan girdileri
    (`LegacyEvent.extra_files`) kopyalanmaz, eski dizinde kalır.
    """
    found, ready = prepared if prepared is not None else manifest_from_legacy(event)
    if event.payloads is None:
        raise ValueError("event: expected an event read with its payloads")
    rel = layout.event_dir(event.event_id)
    staged = files.new_staging_dir(data_dir, label)
    try:
        for key, encoded in ready.items():
            path = _slice_file(staged, key)
            files.write_bytes(path, encoded.stored)
            expected = codec.canonical_bytes(event.payloads[key])
            try:
                back = codec.read_raw(path)
                same = back == expected and codec.canonical_bytes(json.loads(back)) == expected
            except (StoreError, ValueError, RecursionError):
                back, same = b"", False
            if not same or codec.sha256_hex(back) != found.slices[key].sha256:
                raise StoreError(
                    f"Yükseltme doğrulanamadı: {key} dilimi geri okunduğunda eski yükle aynı değil "
                    f"(maç {event.event_id}, {event.path})", path=path, detail=f"{key}: read-back mismatch")
        manifest_file = os.path.join(staged, layout.MANIFEST_NAME)
        manifest_mod.write_manifest(manifest_file, found)
        manifest_mod.read_manifest(manifest_file)
        if checkpoint is not None:
            checkpoint(STEP_STAGED)
        files.publish_dir(staged, layout.resolve(data_dir, rel))
    except BaseException:
        with contextlib.suppress(StoreError):
            files.remove_tree(staged)
        raise
    if checkpoint is not None:
        checkpoint(STEP_PUBLISHED)
    return found


# --- değişiklik satırının niyet dosyası (bölüm 6.2) ---------------------------------------------------
#
# Olay yükü değişirken `on_event_change`'in döndürdüğü satır, yük dosyası değiştirilmeden önce niyet dosyasına
# yazılır: `{"event_id", "sha256" (yeni olay yükünün özeti), "row"}`. Satır günlüğe eklenince dosya silinir.
# Yazma arada ölür ya da hata verirse dosya kalır; maça bir sonraki yazma ya da bir sonraki uzlaştırma
# (açılışta) onu `recover_change` ile kapatır: diskteki olay yükü yeni yükse (özet aynı) satır günlükte yoksa
# eklenir, varsa yeniden eklenmez; yük yeni değilse (yük dosyası hiç değişmedi) niyet atılır, çünkü yükü
# yeniden getiren yazma farkı yeniden görür ve satırı kendisi yazar. Niyet dosyası yalnızca kataloğun yazma
# kilidi altında yazılır ve silinir: kilidi alan biri onu görüyorsa, onu yazan yazma bitmeden ölmüş ya da
# hata vermiştir.

def _intent_file(data_dir: Union[str, "os.PathLike[str]"], event_id: int) -> str:
    return layout.resolve(data_dir, f"{CHANGE_INTENT_DIR}/{event_id}.json")


def _stored_event_sha256(data_dir: Union[str, "os.PathLike[str]"], event_id: int) -> Optional[str]:
    """v3 dizinindeki olay yükü dosyasının (açılmış baytlarının) özeti; dosya yoksa ya da okunamıyorsa None."""
    path = layout.resolve(data_dir, layout.slice_path(layout.event_dir(event_id), EVENT_KEY))
    try:
        return codec.sha256_hex(codec.decode(files.read_bytes(path), path))
    except StoreError:
        return None


def _comparable(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Günlük satırının `seq` dışındaki hali, JSON'dan geçmiş olarak (dosyadaki satırla karşılaştırmak için)."""
    return {k: v for k, v in json.loads(json.dumps(row, ensure_ascii=False)).items() if k != "seq"}


def write_change_intent(data_dir: Union[str, "os.PathLike[str]"], event_id: int, sha256: str,
                        row: Mapping[str, Any]) -> None:
    """Niyet dosyasını yazar (atomik). Satır JSON'a çevrilemezse StoreError; o zaman diske dokunulmamıştır."""
    try:
        data = json.dumps({"event_id": event_id, "sha256": sha256, "row": row}, ensure_ascii=False,
                          sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:  # UnicodeEncodeError bir ValueError'dır
        raise StoreError(f"Değişiklik satırı JSON'a çevrilemedi ({exc})", detail=str(exc)) from exc
    files.write_bytes(_intent_file(data_dir, event_id), data)


def recover_change(cat: Any, data_dir: Union[str, "os.PathLike[str]"], event_id: int) -> Optional[int]:
    """
    Maçın niyet dosyası varsa onu kapatır (yukarıdaki kural) ve siler. Günlükte duran (ya da şimdi eklenen)
    satırın sıra numarasını döndürür; niyet yoksa ya da atıldıysa None. Kataloğun `Catalog.write()` bloğunun
    içinde çağrılır (satır `changes.append_row` ile eklenir). Okunamayan niyet dosyası bildirilir ve silinir.
    """
    from src.store import changes as changes_mod  # döngüsel içe aktarma: changes bu modülü kullanır

    path = _intent_file(data_dir, event_id)
    try:
        raw = files.read_bytes(path)
    except PayloadMissing:
        return None
    try:
        intent = json.loads(raw)
        sha256, row = intent["sha256"], intent["row"]
        if intent.get("event_id") != event_id or not isinstance(sha256, str) or not isinstance(row, dict):
            raise ValueError("unexpected content")
        if changes_mod.change_row(0, row, "", "") is None:
            raise ValueError("the row has no ts_utc or event_id")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        logger.warning(f"Event {event_id}: the change intent of an unfinished write cannot be read and is "
                       f"removed ({exc}): {path}")
        files.remove(path)
        return None
    seq: Optional[int] = None
    if _stored_event_sha256(data_dir, event_id) == sha256:
        changes_mod.sync(cat, data_dir)
        wanted = _comparable(row)
        for logged_seq, logged in cat.connection().execute(
                "SELECT seq, row_json FROM changes WHERE event_id = ? ORDER BY seq DESC LIMIT ?",
                (event_id, _LOGGED_ROWS_CHECKED)).fetchall():
            try:
                if _comparable(json.loads(logged)) == wanted:
                    seq = int(logged_seq)
                    break
            except (ValueError, TypeError, AttributeError, RecursionError):
                continue
        if seq is None:
            seq = changes_mod.append_row(cat, data_dir, row)
            logger.info(f"Event {event_id}: the change row of an unfinished write was added to the change log "
                        f"(seq {seq})")
    files.remove(path)
    return seq


def recover_changes(cat: Any, data_dir: Union[str, "os.PathLike[str]"]) -> int:
    """
    Bütün niyet dosyalarını kapatır (`recover_change`); uzlaştırma çağırır (`Catalog.write()` bloğunun içinde).
    Günlüğe eklenen ya da günlükte bulunan satır sayısını döndürür. Ölen bir yazmanın yarım geçici dosyası
    (`.<ad>.<rastgele>.tmp`) silinir; adı kurala uymayan öteki girdilere dokunulmaz.
    """
    directory = layout.resolve(data_dir, CHANGE_INTENT_DIR)
    try:
        names = sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return 0
    except OSError as exc:
        raise StoreError.from_exception(exc, directory, reading=True) from exc
    found = 0
    for name in names:
        stem, dot, suffix = name.partition(".")
        if dot and suffix == "json" and stem.isdigit() and str(int(stem)) == stem:
            found += recover_change(cat, data_dir, int(stem)) is not None
        elif name.startswith(".") and name.endswith(".tmp"):
            files.remove(os.path.join(directory, name))
    return found


# --- EventStore ---------------------------------------------------------------------------------------

class EventStore:
    """Maçların ve dilimlerinin API'si (`Store.events`): katalogdan okur, v3 düzenine yazar."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._catalog = store._catalog
        self._data_dir = str(store.data_dir)
        self._reader = LegacyReader(self._data_dir)
        self._clock: Callable[[], datetime] = _utc_now  # "şimdi": zamanı verilmeyen sonuçlar ve manifest için

    @contextlib.contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        self._store._require_open()
        assert self._catalog is not None
        with self._catalog.read() as conn:
            yield conn

    # -- satırlar --------------------------------------------------------------------------------------

    def get(self, event_id: int) -> Optional[EventRow]:
        """Maçın katalog satırı; bilinmiyorsa None."""
        _int(event_id, "event_id")
        with self._read() as conn:
            row = conn.execute(f"{_EVENT_SELECT} WHERE e.id = ?", (event_id,)).fetchone()
        return _event_row(row) if row is not None else None

    def list(self, q: EventQuery, *, with_total: bool = False) -> Page[EventRow]:
        """
        Sorguya uyan maçların bir sayfası. Sıra başlangıç zamanıdır (eşitlikte kimlik); başlangıcı bilinmeyen
        satırlar `start_desc`'te sona, `start_asc`'te başa gelir.

        Konumlu sayfalama (varsayılan): `next_cursor` bir sonraki çağrıya `cursor` olarak verilir; maliyeti
        sayfanın derinliğine bağlı değildir. `offset` verilirse eski sayfalama uygulanır ve `next_cursor`
        None kalır. with_total: süzgeçlere uyan bütün satırların sayısı da aynı görüntüden okunur.
        """
        sort = _check_sort(q.sort)
        limit = _check_limit(q.limit)
        conditions, params = _query_sql(q)
        if q.offset is not None and q.cursor is not None:
            raise ValueError("offset: cannot be combined with cursor")
        if q.offset is not None and _int(q.offset, "offset") < 0:
            raise ValueError(f"offset: expected a non-negative integer, got {q.offset!r}")
        after = decode_cursor(q.cursor, sort) if q.cursor is not None else None
        with self._read() as conn:
            next_cursor: Optional[str] = None
            if q.offset is not None:
                found = conn.execute(f"{_EVENT_SELECT}{_where(conditions)}{_order(sort)} LIMIT ? OFFSET ?",
                                     (*params, limit, q.offset)).fetchall()
                rows = [_event_row(row) for row in found]
            else:
                rows = _keyset_page(conn, conditions, params, sort, after, limit + 1)
                if len(rows) > limit:
                    rows = rows[:limit]
                    next_cursor = encode_cursor(sort, rows[-1].start_ts, rows[-1].id)
            total = _count(conn, conditions, params) if with_total else None
        return Page(tuple(rows), next_cursor, total)

    def count(self, q: EventQuery) -> int:
        """Sorgunun süzgeçlerine uyan satır sayısı (sayfalama alanları yok sayılır)."""
        conditions, params = _query_sql(q)
        with self._read() as conn:
            return _count(conn, conditions, params)

    def iter(self, q: EventQuery, *, batch: int = DEFAULT_BATCH) -> Iterator[EventRow]:
        """
        Sorguya uyan bütün maçlar, sorgunun sırasıyla, `batch` satırlık parçalar halinde (dışa aktarma için).
        `q.cursor` verilirse oradan sürer; `q.limit` ve `q.offset` yok sayılır. Her parça kendi anlık
        görüntüsünü görür.
        """
        sort = _check_sort(q.sort)
        size = _check_limit(batch, "batch")
        conditions, params = _query_sql(q)
        after = decode_cursor(q.cursor, sort) if q.cursor is not None else None
        return self._iter(conditions, params, sort, after, size)

    def _iter(self, conditions: List[str], params: List[Any], sort: str, after: Optional[Cursor],
              size: int) -> Iterator[EventRow]:
        while True:
            with self._read() as conn:
                rows = _keyset_page(conn, conditions, params, sort, after, size)
            yield from rows
            if len(rows) < size:
                return
            after = (rows[-1].start_ts, rows[-1].id)

    # -- yükler ----------------------------------------------------------------------------------------

    def payload(self, event_id: int, key: str = EVENT_KEY, sub: str = "", *, raw: bool = False) -> Any:
        """
        Maçın bir diliminin yükü; katalogda o dilimin yükü yoksa (ya da maç bilinmiyorsa) None.

        raw=True ayrıştırmadan bayt döndürür: v3'te saklanan JSON baytları (sıkıştırması açılmış), eski
        düzende dosyadaki baytlar, birleşik dosyadaki dilimde kurallı JSON baytları. Katalog yük var derken
        dosya yoksa PayloadMissing (bir kez yeniden denendikten sonra), dosya bozuksa PayloadCorrupt.
        """
        _int(event_id, "event_id")
        layout.validate_key(key)
        layout.validate_sub(sub)
        value = self._payload(event_id, key, sub, raw)
        return None if value is ABSENT else value

    def payloads(self, event_id: int, keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """
        Maçın yükü olan bütün dilimleri: dilim adı → yük. Ad, alt anahtarsız dilimde anahtarın kendisidir
        ("event", "statistics"), alt anahtarlı dilimde "anahtar/alt" ("odds_all/1"). keys: yalnızca bu
        anahtarlar (bütün alt anahtarlarıyla). Maç bilinmiyorsa boş sözlük.
        """
        _int(event_id, "event_id")
        wanted = None if keys is None else {layout.validate_key(key) for key in keys}
        with self._read() as conn:
            found = conn.execute(
                "SELECT key, sub FROM event_slices WHERE event_id = ? AND has_payload = 1 ORDER BY key, sub",
                (event_id,)).fetchall()
        out: Dict[str, Any] = {}
        for key, sub in found:
            if wanted is not None and key not in wanted:
                continue
            value = self._payload(event_id, key, sub, False)
            if value is not ABSENT:  # satır bu arada silinmiş olabilir
                out[layout.slice_name(key, sub)] = value
        return out

    def _payload(self, event_id: int, key: str, sub: str, raw: bool) -> Any:
        return read_with_retry(lambda: self._locate(event_id, key, sub),
                               lambda where: self._read_file(where, event_id, key, sub, raw), ABSENT)

    def _locate(self, event_id: int, key: str, sub: str) -> Optional[_EventFile]:
        with self._read() as conn:
            row = conn.execute(
                "SELECT e.layout, e.path FROM events e JOIN event_slices s ON s.event_id = e.id "
                "WHERE e.id = ? AND s.key = ? AND s.sub = ? AND s.has_payload = 1", (event_id, key, sub)).fetchone()
        if row is None or row[0] is None:
            return None
        return _EventFile(str(row[0]), row[1])

    def _read_file(self, where: _EventFile, event_id: int, key: str, sub: str, raw: bool) -> Any:
        if where.layout == LAYOUT_V3:
            path = layout.resolve(self._data_dir, layout.slice_path(layout.event_dir(event_id), key, sub))
            return codec.read_raw(path) if raw else codec.read_payload(path)
        directory = where.path or ""
        if not directory or sub:
            raise PayloadMissing(f"Eski düzende böyle bir dilim yok: {event_id} {layout.slice_name(key, sub)}",
                                 path=directory or None)
        value = self._reader.read_payload(directory, key, raw=raw)
        # Ayrıştırılmış None iki şey olabilir: dosya yok ya da dosyanın içeriği JSON `null`
        if value is None and (raw or self._reader.read_payload(directory, key, raw=True) is None):
            where_text = self._reader.resolve(directory)
            raise PayloadMissing(f"Yük dosyası bulunamadı: {where_text} ({key})", path=where_text)
        return value

    # -- dilim durumları -------------------------------------------------------------------------------

    def slices(self, event_id: int) -> List[SliceInfo]:
        """Maçın katalogdaki bütün dilim satırları, (anahtar, alt anahtar) sırasıyla."""
        ref = Ref.event(event_id)
        with self._read() as conn:
            found = conn.execute(
                f"SELECT {SLICE_COLUMNS} FROM event_slices WHERE event_id = ? ORDER BY key, sub",
                (event_id,)).fetchall()
        return [slice_info(ref, row) for row in found]

    def slice(self, event_id: int, key: str, sub: str = "") -> SliceInfo:
        """Bir dilimin durumu; katalogda satırı yoksa durumu `not_requested` olan bir kayıt."""
        ref = Ref.event(event_id)
        layout.validate_key(key)
        layout.validate_sub(sub)
        with self._read() as conn:
            row = conn.execute(
                f"SELECT {SLICE_COLUMNS} FROM event_slices WHERE event_id = ? AND key = ? AND sub = ?",
                (event_id, key, sub)).fetchone()
        return slice_info(ref, row) if row is not None else not_requested(ref, key, sub)

    # -- planlama sorguları ----------------------------------------------------------------------------

    def states(self, scope: Optional[Scope] = None, *, status_classes: Sequence[str] = (),
               batch: int = DEFAULT_BATCH) -> Iterator[EventState]:
        """
        Kapsamdaki her maç için katalog satırı ve dilim satırları, kimlik sırasıyla. Parça başına iki sorgu
        çalışır: maç satırları ve o maçların dilimleri (birincil anahtar aramaları).
        """
        size = _check_limit(batch, "batch")
        conditions, params = _scope_sql(scope)
        status = _status_sql(status_classes)
        if status:
            conditions.append(status)
        return self._states(conditions, params, size)

    def _states(self, conditions: List[str], params: List[Any], size: int) -> Iterator[EventState]:
        last: Optional[int] = None
        while True:
            where = _where(conditions + (["e.id > ?"] if last is not None else []))
            bound = (*params, *(() if last is None else (last,)), size)
            with self._read() as conn:
                events = [_event_row(row) for row in
                          conn.execute(f"{_EVENT_SELECT}{where} ORDER BY e.id LIMIT ?", bound)]
                by_event: Dict[int, List[SliceInfo]] = {}
                if events:
                    ids = ", ".join(str(event.id) for event in events)
                    for row in conn.execute(f"SELECT event_id, {SLICE_COLUMNS} FROM event_slices "
                                            f"WHERE event_id IN ({ids}) ORDER BY event_id, key, sub"):
                        by_event.setdefault(row["event_id"], []).append(slice_info(Ref.event(row["event_id"]), row))
            for event in events:
                yield EventState(event, tuple(by_event.get(event.id, ())))
            if len(events) < size:
                return
            last = events[-1].id

    def missing(self, scope: Optional[Scope], required: Mapping[str, Sequence[str]], *,
                status_classes: Sequence[str] = FINISHED_CLASSES, threshold: int = DEFAULT_EMPTY_THRESHOLD,
                limit: Optional[int] = None, exclusive: bool = False) -> Iterator[MissingRow]:
        """
        Eksiği olan maçlar, kimlik sırasıyla. required: spor kısa adı → gereken dilim anahtarları; "" her
        spor için geçerlidir (sporu bilinmeyen maç yalnızca onları bekler). exclusive: kendi girdisi olan spor
        yalnızca kendi anahtarlarını bekler, "" yalnızca girdisi olmayan sporlara (ve sporu bilinmeyen maça)
        uygulanır; bir spor ortak bir dilimi beklemeyebilir.

        Bir dilim eksiktir: satırı yoksa ya da durumu `ok` değilken kesin "veri yok" sayısı (doğrulanmış +
        doğrulanmamış) `threshold`un altındaysa. Olay yükü olmayan maç (yalnızca listeden bilinen) her zaman
        gelir ve sporunun bütün gereken dilimleri eksik sayılır. `missing_keys`, `required`'daki sırayladır.
        status_classes boşsa durum süzülmez.
        """
        rows: List[Tuple[str, str]] = []
        common = [layout.validate_key(key) for key in required.get("", ())]
        for sport, keys in required.items():
            if not isinstance(sport, str):
                raise ValueError(f"required: expected sport slugs as keys, got {sport!r}")
            for key in keys:
                pair = (sport, layout.validate_key(key))
                if pair not in rows and not (sport and key in common and not exclusive):  # "" her sporu kapsar
                    rows.append(pair)
        conditions, params = _scope_sql(scope)
        status = _status_sql(status_classes)
        if status:
            conditions.append(status)
        _int(threshold, "threshold")
        if rows:
            requirement = "VALUES " + ", ".join("(?, ?, ?)" for _ in rows)
            bound: List[Any] = [value for order, (sport, key) in enumerate(rows) for value in (sport, key, order)]
        else:
            requirement, bound = "SELECT NULL, NULL, NULL WHERE 0", []
        conditions.append(
            "(e.has_event_payload = 0 OR (r.key IS NOT NULL AND (s.event_id IS NULL "
            "OR (s.state != 'ok' AND s.empty_count + s.unverified_empty_count < ?))))")
        # exclusive: "" satırları yalnızca `required`'da kendi girdisi olmayan spora (girdisi boş olan da kendi
        # girdisidir: o spor hiçbir dilim beklemez)
        listed = sorted(sport for sport in required if sport) if exclusive else []
        default_rows = "r.sport = ''"
        if listed:
            default_rows += " AND e.sport NOT IN (" + ", ".join("?" for _ in listed) + ")"
        sql = (
            f"WITH req(sport, key, ord) AS ({requirement}) "
            "SELECT e.id, e.sport, e.has_event_payload, group_concat(r.ord) FROM events e "
            f"LEFT JOIN req r ON r.sport = e.sport OR ({default_rows}) "
            "LEFT JOIN event_slices s ON s.event_id = e.id AND s.key = r.key AND s.sub = ''"
            f"{_where(conditions)} GROUP BY e.id ORDER BY e.id"
        )
        bound = [*bound, *listed, *params, threshold]
        if limit is not None:
            sql += " LIMIT ?"
            bound.append(_check_limit(limit))
        with self._read() as conn:
            found = conn.execute(sql, bound).fetchall()
        out: List[MissingRow] = []
        for event_id, sport, has_payload, orders in found:
            picked = sorted(int(part) for part in str(orders).split(",")) if orders is not None else []
            out.append(MissingRow(int(event_id), str(sport), bool(has_payload), tuple(rows[n][1] for n in picked)))
        return iter(out)

    def refresh_candidates(self, *, now: float, window_s: float, min_interval_s: float,
                           scope: Optional[Scope] = None, status_classes: Sequence[str] = (),
                           include_unobserved: bool = False) -> List[int]:
        """
        Kaydı geçici olan ve yenileme zamanı gelen maçlar (bölüm 8.3 ve 8.4), kimlik sırasıyla: olay yükü ve
        gözlemi var, gözlem başlangıçtan `window_s` saniye geçmeden yapılmış (`observed_gap < window_s`) ve
        son gözlemin üzerinden en az `min_interval_s` saniye geçmiş. Durum sınıfına varsayılan olarak
        bakılmaz: tamamlanmışken iptale ya da bitmemiş bir duruma dönen kayıt da pencere kapanana kadar gelir.

        include_unobserved: gözlemi olmayan (eski) kayıtlar da eklenir. window_s <= 0 politikayı kapatır
        (boş liste). Gözlem anı katalogda tam saniyedir.
        """
        _number(now, "now")
        if _number(window_s, "window_s") <= 0:
            return []
        scoped, params = _scope_sql(scope)
        status = _status_sql(status_classes)
        if status:
            scoped.append(status)
        due = _where([_UNSETTLED_SQL, "e.observed_gap < ?", "e.observed_at <= ?", *scoped])
        observed_before = now - _number(min_interval_s, "min_interval_s")
        with self._read() as conn:
            ids = [int(row[0]) for row in conn.execute(
                f"SELECT e.id FROM events e{due}", (window_s, observed_before, *params))]
            if include_unobserved:
                ids.extend(int(row[0]) for row in conn.execute(
                    f"SELECT e.id FROM events e{_where([_UNOBSERVED_SQL, *scoped])}", params))
        return sorted(ids)

    def stale(self, scope: Optional[Scope] = None) -> List[int]:
        """Daha yeni bir listenin saklanan olay yüküyle çeliştiği maçlar (bölüm 8.2, kural 3), kimlik sırasıyla."""
        conditions, params = _scope_sql(scope)
        with self._read() as conn:
            return [int(row[0]) for row in conn.execute(
                f"SELECT e.id FROM events e{_where(['e.stale = 1', *conditions])} ORDER BY e.id", params)]

    def open_events(self, *, started_before: float, scope: Optional[Scope] = None) -> List[int]:
        """
        Başlaması gerekmiş ama katalogda hâlâ açık görünen maçlar: durum sınıfı not_started, live ya da
        unknown ve başlangıcı `started_before`'dan sonra değil. Başlangıç zamanı (sonra kimlik) sırasıyla.
        """
        conditions, params = _scope_sql(scope)
        where = _where([_OPEN_SQL, "e.start_ts <= ?", *conditions])
        with self._read() as conn:
            return [int(row[0]) for row in conn.execute(
                f"SELECT e.id FROM events e{where} ORDER BY e.start_ts, e.id",
                (_number(started_before, "started_before"), *params))]

    def summary(self, scope: Optional[Scope] = None) -> List[TournamentSummary]:
        """Turnuva başına maç sayıları, turnuva kimliği sırasıyla (turnuvasız maçlar en başta, kimliği None)."""
        conditions, params = _scope_sql(scope)
        where = _where(conditions)
        with self._read() as conn:
            totals = conn.execute(
                "SELECT e.tournament_id, count(*), sum(e.has_event_payload), count(DISTINCT e.season_id), "
                f"min(e.start_ts), max(e.start_ts), max(e.updated_at) FROM events e{where} "
                "GROUP BY e.tournament_id ORDER BY e.tournament_id", params).fetchall()
            by_status: Dict[Optional[int], Dict[str, int]] = {}
            for tournament_id, status, number in conn.execute(
                    f"SELECT e.tournament_id, e.status_class, count(*) FROM events e{where} "
                    "GROUP BY e.tournament_id, e.status_class", params):
                by_status.setdefault(tournament_id, {})[str(status)] = int(number)
        out: List[TournamentSummary] = []
        for tournament_id, events, with_payload, seasons, first, last, updated in totals:
            statuses = by_status.get(tournament_id, {})
            out.append(TournamentSummary(
                tournament_id=tournament_id,
                events=int(events),
                with_payload=int(with_payload or 0),
                finished=sum(statuses.get(name, 0) for name in FINISHED_CLASSES),
                seasons=int(seasons),
                by_status={name: statuses[name] for name in _STATUS_ORDER if name in statuses},
                first_start_ts=first,
                last_start_ts=last,
                updated_at=updated,
            ))
        return out

    # -- yazma -----------------------------------------------------------------------------------------

    def put(self, event_id: int, outcomes: Mapping[SliceKey, Outcome], *,
            count_empties: Union[bool, Collection[str]] = True,
            keep_history: Collection[str] = (),
            on_event_change: Optional[EventChange] = None,
            status_regressed: Optional[bool] = None) -> PutResult:
        """
        Bir maçın dilim sonuçlarını saklar; olay yüklerinin diske ulaştığı tek yoldur (bölüm 2.3).

        outcomes: dilim anahtarı (ya da `(anahtar, alt anahtar)`) → `Outcome`. Anahtar `[a-z][a-z0-9_]{0,39}`,
        alt anahtar `[a-z0-9_.-]{0,80}` (LayoutError). `"event"` sonucu `ok` olmalı ve yükünün `id`'si
        `event_id`'ye eşit olmalıdır (ValueError). Sonuç başına:

          * `ok`: kurallı baytların özeti manifesttekine eşitse dosya yazılmaz, yalnızca `fetched_at` /
            `checked_at` ilerler; değilse dosya atomik değiştirilir. Dilimin "veri yok" ve hata işaretleri silinir.
          * `empty`, verisi var (boş bir 200 gövdesi): yük saklanır, durum `empty`.
          * `empty`, verisi yok (404): dosya yazılmaz, var olan yük silinmez; yükü `ok` olan dilim `ok` kalır.
          * İki `empty` biçiminde de sayaç yalnızca `count_empties` anahtarı kapsıyorsa artar ve önceki hata
            silinir. Sayılmayan, verisi olmayan `empty` de dilime bir kayıt açar (durum `empty`, sayaç 0).
          * `failed`: `{reason, status, at, count + 1}` olarak kaydedilir; durum `error` olur, ama `ok` olan
            dilimin durumu düşmez. Sayaç değişmez.
          * `skipped`: yok sayılır. Bugünkü çağıranların açık devre kesici için ürettiği `failed` / `breaker`
            da böyledir: istek gönderilmemiştir.

        keep_history: geçmişi tutulan dilim anahtarları (ör. `odds_all`; anahtarın bütün alt anahtarları).
        Bu anahtarlardan verisi olan bir sonucun yükü, özeti dilimin geçmişindeki son anlık görüntüden farklıysa
        geçmiş dosyasına (`_history/<key>/<sub ya da "_">.jsonl.gz`) bir gzip üyesi olarak da eklenir
        (`PutResult.history`); manifestteki `history` alanı ve katalogdaki `slice_history` satırları güncellenir.
        Geçmişi olmayan dilimin ilk yükü, dosyası değişmese bile ilk anlık görüntü olur. Okuma ve budama:
        `Store.history` (src/store/history.py).

        `"event"` sonucunun zamanı (`fetched_at`) saklanan gözlemden eskiyse sonuç yok sayılır
        (`PutResult.superseded`); öteki sonuçlar yine uygulanır. Uygulanan her `"event"` sonucu gözlemi
        (`observed_at`, `change_ts`) yeniler; yük aynıysa dosya yazılmaz.

        on_event_change(saklanan yük ya da None, yeni yük): yalnızca olay yükü değişmek üzereyken, kritik
        bölümün içinde ve diske dokunulmadan önce çağrılır. Bir değişiklik günlüğü satırı (`ts_utc` ve
        `event_id` taşıyan nesne) ya da None döndürür; satır aynı kritik bölümde günlüğe eklenir
        (`PutResult.change_seq`). Karşılaştırma kuralı (`src/refresh.py`) böylece Store'un dışında kalır.
        Satır JSON'a çevrilemiyorsa StoreError, diske dokunulmadan. Yazma, yük yerine konduktan sonra ve satır
        eklenmeden kesildiyse satır kaybolmaz (modül açıklaması): maça bir sonraki yazma onu günlüğe ekler ve,
        kendisi satır yazmadıysa, onun sıra numarasını `PutResult.change_seq` olarak döndürür.
        Geri çağrı yazma kilidi tutulurken çalışır: Store'a yazmamalı ve uzun sürmemelidir. Hata fırlatırsa
        hiçbir şey yazılmamıştır. status_regressed=True, ya da dönen satırdaki doğru bir `status_regressed`
        alanı, yapışkan bayrağı kurar; bayrak bir daha silinmez.

        Maç eski düzendeyse önce v3'e yükseltilir (`PutResult.promoted`). `"event"` sonucu olmadan, saklanan
        olay yükü olmayan bir maça yazılamaz (UnknownEvent): yalnızca bir listeden bilinen maç da böyledir.
        Bütün çağrı bölüm 6.2'deki protokolle çalışır ve kataloğa göre atomiktir. Yazma kilidi `busy_timeout`
        içinde alınamazsa StoreBusy. Açık bir `Catalog.write()` bloğunun içinden çağrılmamalıdır.
        """
        _int(event_id, "event_id", minimum=0)
        if on_event_change is not None and not callable(on_event_change):
            raise ValueError("on_event_change: expected a callable")
        items = _items(event_id, outcomes, count_empties)
        keep = _history_keys(keep_history)
        self._writable()
        if not items and status_regressed is not True:
            return _NOTHING_WRITTEN
        return self._entity_write(
            event_id, lambda write: self._apply(write, event_id, items, on_event_change, status_regressed, keep))

    def observe(self, event_id: int, payload: Mapping[str, Any], *, observed_at: Optional[datetime] = None,
                on_event_change: Optional[EventChange] = None,
                status_regressed: Optional[bool] = None) -> PutResult:
        """
        Maçın `/event/{id}` yükünü gözlemler: `put(event_id, {"event": Outcome("ok", payload,
        fetched_at=observed_at)}, ...)`. Yük aynıysa dosya yazılmaz; manifestteki gözlem ve katalogdaki
        `observed_at` / `observed_gap` ilerler (bölüm 8.4).
        """
        outcome = Outcome(SLICE_OK, payload, fetched_at=observed_at)
        return self.put(event_id, {EVENT_KEY: outcome}, on_event_change=on_event_change,
                        status_regressed=status_regressed)

    def reset_empty_markers(self, scope: Optional[Scope] = None, *, include_confirmed: bool = False,
                            threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Dict[str, int]:
        """
        "Bu dilim bu maçta yok" sayaçlarını yeniden denetime açar (bugünkü `reset_unavailable_markers`). Ağ
        isteği yapmaz. Varsayılan olarak yalnızca kesin yanıtla desteklenmeyen sayımlar
        (`unverified_empty_count`) sıfırlanır; işlem bu yüzden tekrarlanabilir. include_confirmed=True
        doğrulanmış sayaçları da sıfırlar. Sayaçları sıfırlanan, yükü ve hata kaydı olmayan dilimin kaydı
        silinir (durumu `not_requested` olur).

        scope: kapsam (boş = bütün maçlar; her düzendeki maç). Eski düzendeki maç, sayaçları değişecekse önce
        v3'e yükseltilir. Dönen sözlük bugünkü anahtarları taşır: "scanned" sayacı olan maç, "matches"
        yeniden beklenir hale gelen dilimi olan maç, "slices" o dilimlerin sayısı (toplam sayımı `threshold`a
        ulaşmışken altına inenler).
        """
        self._writable()
        _int(threshold, "threshold", minimum=0)
        conditions, params = _scope_sql(scope)
        where = _where(["s.empty_count + s.unverified_empty_count > 0", *conditions])
        with self._read() as conn:
            found = conn.execute(
                "SELECT s.event_id, s.empty_count, s.unverified_empty_count FROM event_slices s "
                f"JOIN events e ON e.id = s.event_id{where} ORDER BY s.event_id", params).fetchall()
        changing: Dict[int, bool] = {}
        for event_id, confirmed, unverified in found:
            change = unverified > 0 or (include_confirmed and confirmed > 0)
            changing[int(event_id)] = changing.get(int(event_id), False) or bool(change)
        result = {"matches": 0, "slices": 0, "scanned": len(changing)}
        for event_id, change in changing.items():
            if not change:
                continue
            reopened = self._entity_write(
                event_id, lambda write, target=event_id: self._reset(write, target, include_confirmed, threshold))
            if reopened:
                result["matches"] += 1
                result["slices"] += reopened
        return result

    def delete(self, event_id: int) -> bool:
        """
        Maçı siler: v3 dizinini ve eski düzen ağacındaki bütün kopyalarını (aynı maç birden çok yerde durabilir;
        yalnızca kataloğun bildiği silinseydi maç, kalan eski kopyasıyla yeniden görünürdü). Dizinler önce
        `.meta/trash` altına taşınır, sonra silinir: yarıda kalan silme, yerinde yarım bir dizin bırakmaz.
        Katalog satırları aynı kritik bölümde silinir; maç bir program sayfasında listeleniyorsa liste satırına
        döner. Değişiklik günlüğüne dokunulmaz. Silinecek bir dizin yoksa False döner.

        Eski düzen kopyalarını bulmak için `match_details` ağacı bir kez listelenir (dosya okunmaz); maliyeti
        eski düzendeki maç dizini sayısıyla doğrusaldır.
        """
        _int(event_id, "event_id", minimum=0)
        self._writable()
        return self._entity_write(event_id, lambda write: self._delete(write, event_id))

    # -- yazma protokolü (bölüm 6.2) -------------------------------------------------------------------

    def _writable(self) -> None:
        self._store._require_open()
        if self._store.readonly:
            raise StoreError(f"Depo salt okunur açılmış: {self._data_dir}", path=self._data_dir)

    def _checkpoint(self, step: str) -> None:
        """Protokolün adımları arasında çağrılır (`STEP_*`). Hiçbir şey yapmaz; testler süreci burada öldürür."""

    def _staging_label(self) -> str:
        """Hazırlık dizininin sahibi (karar S16): bu sürecin tuttuğu kilit."""
        leases = self._store._leases
        if leases.held_here(STAGING_WRITER):
            return STAGING_WRITER
        if leases.held_here(STAGING_LIVE):
            return STAGING_LIVE
        return STAGING_UNLEASED

    def _entity_write(self, event_id: int, body: Callable[[_Write], T]) -> T:
        """
        Bir maça yazmanın çerçevesi: işaret (kendi işleminde), yazma kilidi, `body`, işaretin silinmesi, commit.

        İşaret ile kilit arasında başka bir sürecin açılıştaki uzlaştırması işareti silebilir (o anda diskte
        yarım iş yoktur, maçı yeniden dizinler ve işareti kaldırır). Bu yüzden kilit alındıktan sonra işaretin
        durduğuna bakılır; yoksa baştan başlanır. `body` hata verirse katalog işlemi geri alınır; diske
        dokunulduysa işaret kalır ve maç bir sonraki yazmada ya da açılışta toparlanır.
        """
        assert self._catalog is not None
        cat = self._catalog
        for _ in range(_MARKER_ATTEMPTS):
            with cat.write() as conn:
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO pending_writes (kind, entity_id, started_at) VALUES (?, ?, ?)",
                    (PENDING_KIND, event_id, int(time.time()))).rowcount
            self._checkpoint(STEP_MARKER)
            write = _Write(recover=not inserted)
            try:
                with cat.write() as conn:
                    if conn.execute("SELECT 1 FROM pending_writes WHERE kind = ? AND entity_id = ?",
                                    (PENDING_KIND, event_id)).fetchone() is None:
                        continue
                    self._checkpoint(STEP_LOCKED)
                    write.recovered_seq = recover_change(cat, self._data_dir, event_id)
                    try:
                        result = body(write)
                    except BaseException:
                        if not write.touched:
                            # Maçın dosyalarına dokunulmadı: olay yükü değişmedi, değişiklik satırının niyeti de
                            # geçersizdir. Kilit altında silinir (kilidi sonra alan bir yazmanın niyeti silinmesin)
                            with contextlib.suppress(StoreError):
                                files.remove(_intent_file(self._data_dir, event_id))
                        raise
                    conn.execute("DELETE FROM pending_writes WHERE kind = ? AND entity_id = ?",
                                 (PENDING_KIND, event_id))
                    self._checkpoint(STEP_COMMIT)
            except BaseException:
                if inserted and not write.touched:
                    # Diske dokunulmadı (doğrulama hatası, bilinmeyen maç): toparlanacak bir şey yok
                    with contextlib.suppress(StoreError, sqlite3.Error):
                        with cat.write() as conn:
                            conn.execute("DELETE FROM pending_writes WHERE kind = ? AND entity_id = ?",
                                         (PENDING_KIND, event_id))
                raise
            self._checkpoint(STEP_DONE)
            return result
        raise StoreBusy(f"Maç {event_id} için yarım yazma işareti korunamadı: veri dizini başka süreçlerce "
                        f"sürekli uzlaştırılıyor: {self._data_dir}", path=self._data_dir)

    def _open_for_write(self, write: _Write, event_id: int, *, has_event: bool, now: datetime) -> _Opened:
        """
        Yazılacak maçı bulur (kilit altında): v3 manifesti, yoksa kataloğun bildiği eski düzen dizini, o da
        yoksa yeni maç. Diske yazmaz. `has_event`: çağrı bir olay yükü getiriyor (yeni maç kurulabilir, manifesti
        okunamayan dizinin üzerine yazılabilir).
        """
        from src.store import indexer  # döngüsel içe aktarma: dizinleyici bu modülü kullanır

        if write.recover:
            indexer.heal_v3_event(self._data_dir, event_id)
        rel = layout.event_dir(event_id)
        directory = layout.resolve(self._data_dir, rel)
        manifest_file = layout.resolve(self._data_dir, layout.manifest_path(rel))
        try:
            found = manifest_mod.read_manifest(manifest_file)
        except PayloadMissing:
            found = None
        except PayloadCorrupt as exc:
            if not has_event:
                raise
            # Olay yükü bozuk işaretlenmiş dizinin üzerine yeni yük yazılabilmelidir (bölüm 3.6): manifest baştan
            # kurulur, dizindeki okunabilen öteki yük dosyaları yazmadan sonra yeniden kaydedilir
            logger.warning(f"Event {event_id}: the manifest cannot be read and is written anew ({exc.detail or exc})")
            found = None
        if found is not None:
            if found.kind != "event" or found.id != event_id:
                raise StoreError(f"Manifest bu maç dizinine ait değil ({found.kind} {found.id!r}): {manifest_file}",
                                 path=manifest_file)
            return _Opened(manifest=found)
        fresh = Manifest(kind="event", id=event_id, created_at=now, updated_at=now)
        if os.path.isdir(directory):
            return _Opened(manifest=fresh, adopt=True)

        assert self._catalog is not None
        row = self._catalog.connection().execute(
            "SELECT path, legacy_path FROM events WHERE id = ?", (event_id,)).fetchone()
        known = [path for path in ((row["path"], row["legacy_path"]) if row is not None else ()) if path]
        candidates = sorted(
            (c for c in (self._reader.event_dir_at(path) for path in dict.fromkeys(known))
             if c is not None and c.name == str(event_id)), key=indexer.legacy_order)
        if candidates:
            event = self._reader.read_event(candidates[0], payloads=True)
            found, encoded = manifest_from_legacy(event)
            return _Opened(manifest=found, legacy=event, encoded=encoded)
        return _Opened(manifest=fresh, created=True)

    def _stored_event(self, opened: _Opened, event_id: int) -> Optional[Mapping[str, Any]]:
        """Saklanan olay yükü (`on_event_change`'in ilk bağımsız değişkeni); yoksa ya da okunamıyorsa None."""
        if opened.legacy is not None:
            payload = (opened.legacy.payloads or {}).get(EVENT_KEY)
            return payload if isinstance(payload, Mapping) else None
        entry = opened.manifest.slices.get(EVENT_KEY)
        if entry is None or not entry.has_payload:
            return None
        path = layout.resolve(self._data_dir, layout.slice_path(layout.event_dir(event_id), EVENT_KEY))
        try:
            payload = codec.read_payload(path)
        except StoreError:
            return None
        return payload if isinstance(payload, Mapping) else None

    def _apply(self, write: _Write, event_id: int, items: Sequence[_Item], on_event_change: Optional[EventChange],
               status_regressed: Optional[bool], keep: Collection[str] = frozenset()) -> PutResult:
        """`put`'un gövdesi; yazma kilidi altında çalışır. Önce her şey bellekte hesaplanır, sonra diske yazılır."""
        from src.store import changes as changes_mod  # döngüsel içe aktarma: iki modül de bu modülü kullanır
        from src.store import history as history_mod
        from src.store import indexer

        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        has_event = any(item.name == EVENT_KEY for item in items)
        opened = self._open_for_write(write, event_id, has_event=has_event, now=now)
        base = opened.manifest
        found = copy.deepcopy(base)
        rel = layout.event_dir(event_id)
        directory = layout.resolve(self._data_dir, rel)

        payloads: List[Tuple[_Item, codec.Encoded]] = []  # yazılacak yük dosyaları
        snapshots: List[Tuple[_Item, codec.Encoded, datetime]] = []  # geçmiş dosyalarına eklenecek anlık görüntüler
        dirty = superseded = False
        change: Optional[Mapping[str, Any]] = None
        change_sha256 = ""  # olay yükünün, değişiklik satırını doğuran yeni özeti
        for item in items:
            outcome = item.outcome
            at = _aware(outcome.fetched_at, now, f"outcomes[{item.name!r}].fetched_at")
            entry = found.slices.get(item.name)
            if item.name == EVENT_KEY:
                observation = found.observation
                stored_at = observation.observed_at if observation is not None else None
                if stored_at is None and entry is not None and entry.has_payload:
                    stored_at = entry.fetched_at
                if stored_at is not None and at < stored_at:
                    superseded = True
                    continue
            dirty = True
            if outcome.status == SLICE_FAILED:
                entry = entry if entry is not None else SliceEntry(state="error")
                entry.error = ErrorMark(reason=outcome.reason or "other", status=_natural(outcome.http_status), at=at,
                                        count=(entry.error.count if entry.error is not None else 0) + 1)
                if entry.state != "ok":  # hata, verisi olan dilimin durumunu düşürmez
                    entry.state = "error"
                entry.checked_at = at
                found.slices[item.name] = entry
                continue
            if outcome.data is None:  # kesin "veri yok" (404): dosya yazılmaz, var olan yük silinmez
                entry = entry if entry is not None else SliceEntry(state="empty")
                if entry.state != "ok":
                    entry.state = "empty"
            else:
                encoded = codec.encode(outcome.data)
                previous = entry.sha256 if entry is not None else None
                path = _slice_file(directory, item.key, item.sub)
                # Özet aynıysa dosya yazılmaz; yerinde durmuyorsa (arkadan silinmiş) yeniden yazılır. Yükseltilecek
                # maçın değişmeyen dosyalarını yükseltme yazar.
                if previous != encoded.sha256 or (opened.legacy is None and not os.path.isfile(path)):
                    payloads.append((item, encoded))
                if item.name == EVENT_KEY and previous != encoded.sha256 and on_event_change is not None:
                    change = on_event_change(self._stored_event(opened, event_id), outcome.data)
                    change_sha256 = encoded.sha256
                if item.key in keep:
                    mark = entry.history if entry is not None else None
                    if mark is None or mark.last_sha256 != encoded.sha256:
                        snapshots.append((item, encoded, at))
                entry = entry if entry is not None else SliceEntry(state="ok")
                entry.state = "ok" if outcome.status == SLICE_OK else "empty"
                entry.sha256, entry.raw_bytes, entry.stored_bytes = (
                    encoded.sha256, encoded.raw_bytes, encoded.stored_bytes)
                entry.fetched_at = at
                entry.meta = dict(outcome.meta) if outcome.meta is not None else None
            entry.checked_at = at
            entry.error = None  # yanıt geldi: önceki hata geçersiz
            if outcome.status == SLICE_OK:
                entry.empty = None
            elif item.counted:
                mark = entry.empty if entry.empty is not None else EmptyMark()
                mark.count += 1
                mark.reason = outcome.reason or ("empty" if outcome.data is not None else "404")
                mark.at = at
                entry.empty = mark
            found.slices[item.name] = entry
            if item.name == EVENT_KEY:
                before = found.observation
                found.observation = Observation(
                    observed_at=at, change_ts=_change_ts(outcome.data),
                    status_regressed=bool(before and before.status_regressed),
                    extra=dict(before.extra) if before is not None else {})

        if change is not None:
            if not isinstance(change, Mapping):
                raise ValueError(f"on_event_change: expected a mapping or None, got {type(change).__name__}")
            if changes_mod.change_row(0, change, "", "") is None:
                raise ValueError("on_event_change: the row needs ts_utc (ISO 8601) and event_id (integer)")
        regressed = status_regressed is True or bool(change is not None and change.get("status_regressed"))
        if regressed and not (found.observation is not None and found.observation.status_regressed):
            if found.observation is None:
                found.observation = Observation()
            found.observation.status_regressed = True
            dirty = True

        event_entry = found.slices.get(EVENT_KEY)
        if event_entry is None or event_entry.state != "ok" or not event_entry.has_payload:
            # Olay yükü olmayan v3 dizini geçerli bir maç değildir (bölüm 3.4): yeniden kurma onu dizinlemez
            raise UnknownEvent(event_id=event_id)
        if dirty:
            found.updated_at = max(found.updated_at, now)

        # --- disk: önce değişiklik satırının niyeti, sonra yükseltme ya da yeni dizin, yük dosyaları ve geçmiş
        # üyeleri, en son manifest (bölüm 4.4 ve 6.2) ---
        if change is not None:
            # Yükü henüz değiştirmez: yazma burada biterse niyet atılır (`_entity_write`, `recover_change`)
            write_change_intent(self._data_dir, event_id, change_sha256, change)
            self._checkpoint(STEP_CHANGE_INTENT)
        written: List[str] = []
        kept: List[str] = []

        def add_snapshots(root: str, *, live: bool) -> None:
            """Geçmiş üyelerini ekler ve manifestteki `history` alanını dosyanın gerçek haline göre kurar.
            live=False: hazırlık dizinine yazılıyor (yayımlanmadan biten deneme diske dokunmuş sayılmaz)."""
            for item, encoded, moment in snapshots:
                write.touched = write.touched or live
                rel_file = layout.history_path("", item.key, item.sub).lstrip("/")
                path = os.path.join(root, *rel_file.split("/"))
                entry = found.slices[item.name]
                n, _offset, _length = history_mod.append(
                    path, history_mod.encode_member(encoded.raw, encoded.sha256, moment),
                    known=self._history_end(event_id, item, entry.history, opened.created))
                entry.history = manifest_mod.HistoryMark(
                    count=n, last_sha256=encoded.sha256,
                    extra=dict(entry.history.extra) if entry.history is not None else {})
                kept.append(item.name)
                self._checkpoint(f"{STEP_HISTORY}:{item.name}")
        if opened.legacy is not None and opened.encoded is not None:
            self._promote(write, opened.legacy, (base, opened.encoded))
        if opened.created:
            staged = files.new_staging_dir(self._data_dir, self._staging_label())
            try:
                for item, encoded in payloads:
                    files.write_bytes(_slice_file(staged, item.key, item.sub), encoded.stored)
                    written.append(item.name)
                add_snapshots(staged, live=False)
                manifest_mod.write_manifest(os.path.join(staged, layout.MANIFEST_NAME), found)
                self._checkpoint(STEP_STAGED)
                try:
                    files.publish_dir(staged, directory)
                finally:
                    write.touched = write.touched or os.path.isdir(directory)
            except BaseException:
                with contextlib.suppress(StoreError):
                    files.remove_tree(staged)
                raise
            self._checkpoint(STEP_PUBLISHED)
        else:
            for item, encoded in payloads:
                write.touched = True
                files.write_bytes(_slice_file(directory, item.key, item.sub), encoded.stored)
                written.append(item.name)
                self._checkpoint(f"{STEP_PAYLOAD}:{item.name}")
            add_snapshots(directory, live=True)
            if dirty or opened.adopt:
                write.touched = True
                manifest_mod.write_manifest(layout.resolve(self._data_dir, layout.manifest_path(rel)), found)
                self._checkpoint(STEP_MANIFEST)
            if opened.adopt:
                indexer.heal_v3_event(self._data_dir, event_id)  # dizindeki öteki yük dosyaları yeniden kaydedilir

        # --- değişiklik günlüğü ve katalog ---
        assert self._catalog is not None
        seq: Optional[int] = None
        if change is not None:
            write.touched = True
            seq = changes_mod.append_row(self._catalog, self._data_dir, change)
            self._checkpoint(STEP_CHANGE_LOG)
            files.remove(_intent_file(self._data_dir, event_id))
        self._index(event_id)
        return PutResult(created=opened.created, event_written=EVENT_KEY in written, superseded=superseded,
                         written=tuple(written), change_seq=seq if seq is not None else write.recovered_seq,
                         promoted=opened.legacy is not None,
                         history=tuple(kept))

    def _history_end(self, event_id: int, item: _Item, mark: Optional[manifest_mod.HistoryMark],
                     created: bool) -> Optional[Tuple[int, int]]:
        """
        Geçmiş dosyasının katalogdaki son hali: (üye sayısı, bittiği bayt). Manifestin sayısıyla uyuşuyorsa
        ekleme dosyayı taramadan yapılır (`history.append`); uyuşmuyorsa ya da bilinmiyorsa None (dosya taranır).
        """
        if created:
            return (0, 0)
        if mark is None:
            return None
        assert self._catalog is not None
        row = self._catalog.connection().execute(
            "SELECT n, offset + length FROM slice_history WHERE kind = 'event' AND entity_id = ? AND key = ? "
            "AND sub = ? ORDER BY n DESC LIMIT 1", (event_id, item.key, item.sub)).fetchone()
        if row is None or int(row[0]) != mark.count:
            return None
        return int(row[0]), int(row[1])

    def _promote(self, write: _Write, event: LegacyEvent,
                 prepared: Tuple[Manifest, Mapping[str, codec.Encoded]]) -> None:
        """Eski düzendeki maçı v3'e yükseltir (`promote_legacy`). Yayımlanmadan biten deneme diske dokunmuş sayılmaz."""
        try:
            promote_legacy(self._data_dir, event, label=self._staging_label(), checkpoint=self._checkpoint,
                           prepared=prepared)
        finally:
            write.touched = write.touched or os.path.isdir(
                layout.resolve(self._data_dir, layout.event_dir(event.event_id)))

    def _index(self, event_id: int) -> None:
        """Maçın katalog satırlarını dosyalardan yeniden türetir (yeniden kurmanın yazacağı satırlar)."""
        problems: List[Any] = []
        if self._store.catalog.index_event(event_id, problems=problems) != LAYOUT_V3:
            detail = "; ".join(f"{p.kind}: {p.detail}" for p in problems) or "v3 dizini dizinlenemedi"
            raise StoreError(f"Maç {event_id} yazıldı ama dizinlenemedi ({detail})",
                             path=layout.resolve(self._data_dir, layout.event_dir(event_id)), detail=detail)
        self._checkpoint(STEP_INDEXED)

    def _reset(self, write: _Write, event_id: int, include_confirmed: bool, threshold: int) -> int:
        """`reset_empty_markers`'ın bir maç için gövdesi; yeniden beklenir hale gelen dilim sayısını döndürür."""
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        opened = self._open_for_write(write, event_id, has_event=False, now=now)
        if opened.created or opened.adopt:
            return 0  # katalog bayat: maçın dizini yok
        found = copy.deepcopy(opened.manifest)
        reopened = 0
        dirty = False
        for name in list(found.slices):
            entry = found.slices[name]
            mark = entry.empty
            if mark is None:
                continue
            keep = 0 if include_confirmed else mark.count
            total = mark.count + mark.unverified
            if keep == total:
                continue
            dirty = True
            if total >= threshold > keep:
                reopened += 1
            if keep:
                mark.count, mark.unverified = keep, 0
            else:
                entry.empty = None
                if entry.state == "empty" and not entry.has_payload and entry.error is None:
                    del found.slices[name]  # hiçbir şey bilinmiyor: dilim yeniden "istenmedi" olur
        if not dirty:
            return 0  # katalog bayat: sıfırlanacak sayaç yok
        if opened.legacy is not None and opened.encoded is not None:
            self._promote(write, opened.legacy, (opened.manifest, opened.encoded))
        write.touched = True
        found.updated_at = max(found.updated_at, now)
        manifest_mod.write_manifest(
            layout.resolve(self._data_dir, layout.manifest_path(layout.event_dir(event_id))), found)
        self._checkpoint(STEP_MANIFEST)
        self._index(event_id)
        return reopened

    def _delete(self, write: _Write, event_id: int) -> bool:
        """`delete`'in gövdesi; yazma kilidi altında çalışır."""
        from src.store import indexer  # döngüsel içe aktarma: dizinleyici bu modülü kullanır

        doomed: List[str] = []
        directory = layout.resolve(self._data_dir, layout.event_dir(event_id))
        if os.path.lexists(directory):
            doomed.append(directory)
        for candidate in indexer.legacy_candidates(self._reader).get(str(event_id), []):
            try:
                self._reader.read_event(candidate, payloads=False)
            except LayoutError:
                continue  # dizinin adı bu kimlik ama içindeki olay yükü başka bir maçın: bu maçın kopyası değil
            except StoreError:
                pass  # okunamayan kopya da bu maçın dizinidir
            doomed.append(self._reader.resolve(candidate.path))
        for path in doomed:
            write.touched = True
            files.remove_tree(files.move_to_trash(self._data_dir, path))
        self._store.catalog.index_event(event_id, candidates=[])
        self._checkpoint(STEP_INDEXED)
        return bool(doomed)


# --- sayfalama ----------------------------------------------------------------------------------------

def _order(sort: str) -> str:
    return " ORDER BY e.start_ts DESC, e.id DESC" if sort == SORT_START_DESC else " ORDER BY e.start_ts, e.id"


def _count(conn: sqlite3.Connection, conditions: Sequence[str], params: Sequence[Any]) -> int:
    return int(conn.execute(f"SELECT count(*) FROM events e{_where(conditions)}", tuple(params)).fetchone()[0])


def _select(conn: sqlite3.Connection, conditions: Sequence[str], params: Sequence[Any], sort: str,
            limit: int) -> List[EventRow]:
    sql = f"{_EVENT_SELECT}{_where(conditions)}{_order(sort)} LIMIT ?"
    return [_event_row(row) for row in conn.execute(sql, (*params, limit))]


def _keyset_page(conn: sqlite3.Connection, conditions: Sequence[str], params: Sequence[Any], sort: str,
                 after: Optional[Cursor], limit: int) -> List[EventRow]:
    """
    `after` konumundan sonraki en çok `limit` satır. Satır değeri karşılaştırması (`(start_ts, id) < (?, ?)`)
    dizinde bir aralık okur, ama NULL içeren satırı hiç eşleştirmez. Başlangıcı bilinmeyen satırlar bu
    yüzden ayrı bir bölgedir: azalan sırada tarihli satırlardan sonra, artan sırada onlardan önce gelir
    (SQLite'ın NULL sıralaması) ve bölge sınırı ikinci bir sorguyla geçilir.
    """
    base, bound = list(conditions), list(params)
    if after is None:
        return _select(conn, base, bound, sort, limit)
    start_ts, event_id = after
    descending = sort == SORT_START_DESC
    if start_ts is None:
        undated = _select(conn, [*base, "e.start_ts IS NULL", "e.id < ?" if descending else "e.id > ?"],
                          [*bound, event_id], sort, limit)
        if descending or len(undated) >= limit:
            return undated
        return undated + _select(conn, [*base, "e.start_ts IS NOT NULL"], bound, sort, limit - len(undated))
    compare = "(e.start_ts, e.id) < (?, ?)" if descending else "(e.start_ts, e.id) > (?, ?)"
    dated = _select(conn, [*base, compare], [*bound, start_ts, event_id], sort, limit)
    if not descending or len(dated) >= limit:
        return dated
    return dated + _select(conn, [*base, "e.start_ts IS NULL"], bound, sort, limit - len(dated))


__all__ = [
    "EVENT_KEY",
    "EVENT_COLUMNS",
    "STATE_NOT_REQUESTED",
    "SORT_START_DESC",
    "SORT_START_ASC",
    "SORTS",
    "FINISHED_CLASSES",
    "DEFAULT_EMPTY_THRESHOLD",
    "DEFAULT_BATCH",
    "Ref",
    "SliceError",
    "SliceInfo",
    "Scope",
    "EventQuery",
    "Page",
    "EventRow",
    "EventState",
    "MissingRow",
    "TournamentSummary",
    "EventStore",
    "PutResult",
    "SliceKey",
    "EventChange",
    "PENDING_KIND",
    "STAGING_WRITER",
    "STAGING_LIVE",
    "STAGING_UNLEASED",
    "manifest_from_legacy",
    "promote_legacy",
    "check_int",
    "int_list",
    "like_pattern",
    "encode_cursor",
    "decode_cursor",
    "SLICE_COLUMNS",
    "ABSENT",
    "slice_info",
    "not_requested",
    "read_with_retry",
]
