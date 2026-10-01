"""
Maçların okuma API'si: `EventStore` ve bütün okuma API'lerinin ortak türleri
(docs/design/01-storage.md, bölüm 2.3, 3.7, 6.3 ve 8.4).

Her soru kataloğa sorulur; dosya ağacı gezilmez. Katalog bir dizindir: `open_store` onu kurmaz, kuran ve
güncel tutan dizinleyicidir (src/store/indexer.py). Kurulmamış katalogda bütün sorular boş yanıt verir.

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
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import dataclasses
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
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
)

from src.status import StatusClass
from src.store import codec, derive, layout, legacy
from src.store.errors import PayloadMissing
from src.store.legacy import LegacyReader

if TYPE_CHECKING:
    from src.store.api import Store

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

def _int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not _INT64_MIN <= value <= _INT64_MAX:
        raise ValueError(f"{what}: expected an integer, got {value!r}")
    return value


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


# --- EventStore ---------------------------------------------------------------------------------------

class EventStore:
    """Maçların ve dilimlerinin okuma API'si (`Store.events`). Yazma yöntemleri sonraki adımlarda eklenir."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._catalog = store._catalog
        self._data_dir = str(store.data_dir)
        self._reader = LegacyReader(self._data_dir)

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
                limit: Optional[int] = None) -> Iterator[MissingRow]:
        """
        Eksiği olan maçlar, kimlik sırasıyla. required: spor kısa adı → gereken dilim anahtarları; "" her
        spor için geçerlidir (sporu bilinmeyen maç yalnızca onları bekler).

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
                if pair not in rows and not (sport and key in common):  # "" zaten her sporu kapsar
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
        sql = (
            f"WITH req(sport, key, ord) AS ({requirement}) "
            "SELECT e.id, e.sport, e.has_event_payload, group_concat(r.ord) FROM events e "
            "LEFT JOIN req r ON r.sport = e.sport OR r.sport = '' "
            "LEFT JOIN event_slices s ON s.event_id = e.id AND s.key = r.key AND s.sub = ''"
            f"{_where(conditions)} GROUP BY e.id ORDER BY e.id"
        )
        bound = [*bound, *params, threshold]
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
