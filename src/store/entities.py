"""
Maç dışı varlıkların dizinlenmesi: sezon listeleri, program sayfaları ve onlardan gelen liste satırları
(docs/design/01-storage.md, bölüm 3.4 adım 2, 5.2 ve 8.2).

Bu adımda kaynak yalnızca eski düzendir (`seasons/`, `matches/`); v3 ağacındaki turnuva ve sezon
dizinlerini yazan `EntityStore.put` ile birlikte eklenir. Okuma API'si (`EntityStore`) de sonraki adımdadır.

Katalogda neyin nereden geldiği:

  * `entity_slices`: her geçerli sezon listesi (`tournament` / `seasons`) ve her geçerli program sayfası
    (`season` / `schedule` / `round_12`, `last_0`, ...) için bir satır; `layout = 'legacy'`, `path` dosyanın
    yolu. Tur dosyasının `_complete` anahtarı `meta_json`'a `{"complete": ...}` olarak, süzülerek yazılmış
    sayfa `{"filtered": true}` olarak geçer. Okunamayan sayfa `state = 'error'`, `error_reason = 'corrupt'` alır.
  * `seasons`: sezon listesinin satırları (`listed = 1`, `position`); listeden gelen satırı başka hiçbir
    kaynak değiştirmez.
  * `tournaments` ve sezon listesinde olmayan sezonlar: program sayfalarındaki olay nesnelerinden; en yeni
    sayfa kazanır (`updated_at`), böylece adı değişen turnuva yeni adıyla görünür. Olay yükleri ve özet
    CSV'leri bu satırları yalnızca yoksa ekler. `participants` satırında her kaynak için en yenisi kazanır.
  * `events`: bir sayfada geçen ve `/event/{id}` yükü olmayan maç, onu listeleyen en yeni sayfadan türeyen
    bir liste satırı alır (`row_source = 'listing'`, `has_event_payload = 0`, `layout` NULL). Yükü olan maçın
    satırına liste dokunmaz; yalnızca `listed_in` (onu listeleyen en yeni sayfanın alt anahtarı) ve `stale`
    yazılır (bölüm 8.2).
  * `stale = 1`: liste, saklanan olay yükünden yeniyse **ve** durum üçlüsü, kazanan kodu, başlangıç zamanı ya
    da herhangi bir skor alanında ondan farklıysa. Karşılaştırılan alanlar `src/refresh.diff_basic` ile
    aynıdır (`diff_fields`; Store `src.refresh`'i içe aktaramaz, eşitliği test denetler). Olay yükünün zamanı
    gözlem anıdır (`observed_at`), gözlem yoksa olay diliminin `fetched_at`'i.

Bir sezonun birimi `matches/<turnuva>/<sezon>` dizinidir (aynı sezonun birden çok dizini olabilir). Bir
sayfada geçen maç o dizinin turnuvasına ve sezonuna yazılır: liste satırının `tournament_id` / `season_id`
sütunları dizinden gelir (yük başka bir sezon söylüyorsa `season_mismatch` sorunu bildirilir). Yükü olan
maç yalnızca kendi yükü de aynı turnuvayı ve sezonu söylüyorsa o sayfaya bağlanır. Bu kural sayesinde bir
sezonun katalogdaki payı yalnızca o sezonun dosyalarından yeniden kurulabilir (uzlaştırma, bölüm 3.5).

Hiç tur / sayfa dosyası olmayan sezon, özet CSV'lerinden (`*_summary.csv`, eski `*_matches.csv`) dizinlenir:
CSV satırından küçük bir olay nesnesi kurulur ve aynı türetme işlevinden geçer. Bu satırlarda takım
kimliği, durum kodu ve skor ayrıntısı yoktur; `match_date` yazıldığı gibi sürecin yerel saatiyle okunur.
Özetten gelen satır için `stale` hesaplanmaz.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.sports import event_sport_slug
from src.store import codec, derive, layout, legacy
from src.store.catalog import Catalog
from src.store.errors import StoreError
from src.store.legacy import (
    LegacyProblem,
    LegacyReader,
    LegacySchedulePage,
    LegacySeasonList,
    LegacySummaryFile,
)

Row = Dict[str, Any]
SeasonKey = Tuple[int, int]  # (turnuva kimliği, sezon kimliği)

LAYOUT_LEGACY = "legacy"
LAYOUT_V3 = "v3"
KIND_TOURNAMENT = "tournament"
KIND_SEASON = "season"
KEY_SEASONS = "seasons"
KEY_SCHEDULE = "schedule"
EVENT_KEY = legacy.EVENT_KEY

PROBLEM_SEASON_MISMATCH = "season_mismatch"  # listelenen maçın yükü başka bir turnuva / sezon söylüyor

SOURCE_PAGES = "pages"
SOURCE_SUMMARY = "summary"
SOURCE_NONE = "none"

PARTICIPANT_COLUMNS: Tuple[str, ...] = (
    "id", "sport", "name", "name_folded", "short_name", "slug", "name_code", "country", "gender", "type",
    "national", "updated_at",
)
_STATUS_FIELDS = ("type", "code", "description")
_SCORE_SIDES = ("homeScore", "awayScore")
_PAGE_SUB_RE = re.compile(r"(?:last|next)_[0-9]+")
_NS = 1_000_000_000
_CHUNK = 500  # `IN (...)` sorgularındaki kimlik sayısı (SQLite'ın değişken sınırının altında)
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1
# Beklenmeyen biçimdeki bir liste öğesi satır türetilirken bu hataları verebilir; öğe atlanır
_DERIVE_ERRORS = (StoreError, ArithmeticError, AttributeError, KeyError, TypeError, ValueError)


# --- yazılabilir değerler -----------------------------------------------------------------------------

def clean_text(value: str) -> str:
    """UTF-8'e çevrilemeyen metin (JSON'daki eşsiz vekil kod noktası) SQLite'a yazılamaz: '?' ile değiştirilir."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return value.encode("utf-8", "replace").decode("utf-8")
    return value


def storable(row: Row) -> Row:
    """Satırı yazılabilir hale getirir (yerinde): yalnızca ASCII olmayan metinlere bakılır."""
    for name, value in row.items():
        if type(value) is str and not value.isascii():
            row[name] = clean_text(value)
    return row


def meta_json(meta: Optional[Mapping[str, Any]]) -> Optional[str]:
    if meta is None:
        return None
    return json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


# --- liste ile olay yükünün karşılaştırılması ---------------------------------------------------------

def diff_fields(event: Any) -> Dict[str, Any]:
    """
    Bir olay nesnesinin karşılaştırılan alanları, düz sözlük olarak: durum üçlüsü, `winnerCode`,
    `startTimestamp` ve `homeScore` / `awayScore`'un bütün alt alanları (src/refresh.py `_flatten`).
    Değeri None olan alan yok sayılır: `diff_basic` de eksik alanla None'ı eşit tutar.
    """
    if not isinstance(event, Mapping):
        return {}
    status = event.get("status")
    status = status if isinstance(status, Mapping) else {}
    flat: Dict[str, Any] = {f"status.{name}": status.get(name) for name in _STATUS_FIELDS}
    flat["winnerCode"] = event.get("winnerCode")
    flat["startTimestamp"] = event.get("startTimestamp")
    for side in _SCORE_SIDES:
        score = event.get(side)
        if isinstance(score, Mapping):
            for name, value in score.items():
                flat[f"{side}.{name}"] = value
    return {name: value for name, value in flat.items() if value is not None}


def _normal(value: Any) -> Any:
    """Python'un eşit saydığı JSON değerleri aynı yazıma iner (1, 1.0 ve True)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        return int(value)
    if isinstance(value, Mapping):
        return {str(key): _normal(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normal(item) for item in value]
    return value


def compare_digest(event: Any) -> bytes:
    """
    Karşılaştırılan alanların özeti. İki olay nesnesinin özeti eşitse `diff_basic` aralarında fark bulmaz,
    farklıysa bulur. Yeniden kurma, listelerin kendisi yerine bu 16 baytı bellekte tutar.
    """
    text = json.dumps(_normal(diff_fields(event)), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=16).digest()


def is_stale(listing_at: Optional[int], listing_digest: Optional[bytes], payload_at: Optional[int],
             payload_digest: Optional[bytes]) -> bool:
    """
    Bölüm 8.2, kural 3: liste olay yükünden yeni **ve** karşılaştırılan alanlarda ondan farklı. Özeti
    olmayan liste (özet CSV'si) karşılaştırılamaz; zamanı bilinmeyen yük için "liste daha yeni" denemez.
    """
    if listing_digest is None or payload_digest is None or listing_at is None or payload_at is None:
        return False
    return listing_at > payload_at and listing_digest != payload_digest


# --- kayıtlar -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ListedEvent:
    """Bir sezonun dosyalarında geçen bir maç: onu listeleyen en yeni dosyadaki hali."""

    event_id: int
    payload: Mapping[str, Any]  # listedeki olay nesnesi (özet CSV'sinde: satırdan kurulan nesne)
    sub: Optional[str]  # `listed_in`: sayfanın alt anahtarı; özet satırında tur sütunundan, bilinmiyorsa None
    fetched_at: int  # o dosyanın zamanı (epoch saniye): liste satırının `updated_at`'i
    first_seen_at: int  # maçı listeleyen dosyaların en eskisinin zamanı
    path: str  # dosya (DATA_DIR'e göre)
    comparable: bool = True  # False: özet CSV'si; `stale` hesaplanmaz


@dataclass
class SeasonListing:
    """Bir sezonun dosyalarından okunanlar (`read_season`)."""

    tournament_id: int
    season_id: int
    source: str = SOURCE_NONE  # "pages" | "summary" | "none" (dosyası kalmamış sezon)
    slices: List[Row] = field(default_factory=list)  # `entity_slices` satırları (sayfa başına bir tane)
    events: Dict[int, ListedEvent] = field(default_factory=dict)  # maç kimliği → en yeni liste
    problems: List[LegacyProblem] = field(default_factory=list)


@dataclass(frozen=True)
class ListingMark:
    """Yeniden kurma sırasında bir maçın listesinden bellekte tutulan: olay satırı yazılırken kullanılır."""

    tournament_id: int
    season_id: int
    sub: Optional[str]
    fetched_at: int
    digest: Optional[bytes]  # None: karşılaştırılamayan liste (özet CSV'si)


@dataclass
class SeasonCounts:
    """`apply_season` sonucu."""

    pages: int = 0  # yazılan `entity_slices` satırı
    listed: int = 0  # bu sezona bağlanan maç
    rows: int = 0  # yazılan liste satırı (olay yükü olmayan maçlar)
    flagged: int = 0  # `listed_in` / `stale` yazılan, olay yükü olan maçlar
    removed: int = 0  # artık listelenmediği için silinen liste satırı
    cleared: int = 0  # artık listelenmediği için `listed_in`'i silinen, olay yükü olan maçlar


# --- dosyalardan okuma --------------------------------------------------------------------------------

def _plain_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _to_int(value: Any) -> Optional[int]:
    """CSV hücresi → tam sayı ("2", "2.0"); boş ya da sayı olmayan hücre None."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or number != int(number):
        return None
    return int(number) if _INT64_MIN <= int(number) <= _INT64_MAX else None


def _local_epoch(text: Any) -> Optional[int]:
    """
    Özet CSV'sindeki `match_date`: yazıcı `datetime.fromtimestamp(ts).isoformat()` ile, yani sürecin yerel
    saatiyle yazar. Aynı saat diliminde okununca aynı epoch saniyeyi verir; ayrıştırılamıyorsa None.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return math.floor(datetime.fromisoformat(text.strip()).timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def round_sub(label: Any) -> Optional[str]:
    """Özetin `round` sütunundan `listed_in`: "38" → "round_38", "last_0" → "last_0"; tanınmıyorsa None."""
    text = str(label).strip() if label is not None else ""
    number = _to_int(text)
    if number is not None and number >= 0:
        return f"round_{number}"
    return text if _PAGE_SUB_RE.fullmatch(text) else None


def summary_event(row: Mapping[str, Any], tournament_id: int, season_id: int) -> Optional[Dict[str, Any]]:
    """
    Özet CSV'sinin bir satırından olay nesnesi; `match_id` yoksa None. İki sütun kümesi de okunur: bugünkü
    on sütun (round, match_id, home_team, away_team, home_score, away_score, match_date, status, tournament,
    season) ve ilk sürümün tur dosyaları (home_team_id, away_team_id, status_type, start_timestamp, slug).
    """
    event_id = _to_int(row.get("match_id"))
    if event_id is None:
        return None
    tournament: Dict[str, Any] = {"uniqueTournament": {"id": tournament_id}}
    if row.get("tournament"):
        tournament["name"] = row["tournament"]
    season: Dict[str, Any] = {"id": season_id}
    if row.get("season"):
        season["name"] = row["season"]
    event: Dict[str, Any] = {"id": event_id, "tournament": tournament, "season": season}
    for side, score, prefix in (("homeTeam", "homeScore", "home"), ("awayTeam", "awayScore", "away")):
        team: Dict[str, Any] = {}
        if row.get(f"{prefix}_team"):
            team["name"] = row[f"{prefix}_team"]
        team_id = _to_int(row.get(f"{prefix}_team_id"))
        if team_id is not None:
            team["id"] = team_id
        if team:
            event[side] = team
        current = _to_int(row.get(f"{prefix}_score"))
        if current is not None:
            event[score] = {"current": current}
    status: Dict[str, Any] = {}
    if row.get("status"):
        status["description"] = row["status"]
    if row.get("status_type"):
        status["type"] = row["status_type"]
    if status:
        event["status"] = status
    round_number = _to_int(row.get("round"))
    if round_number is not None:
        event["roundInfo"] = {"round": round_number}
    start = _to_int(row.get("start_timestamp"))
    if start is None:
        start = _local_epoch(row.get("match_date"))
    if start is not None:
        event["startTimestamp"] = start
    if row.get("slug"):
        event["slug"] = row["slug"]
    return event


def _slice_row(kind: str, entity_id: int, key: str, sub: str, path: str, *, state: str, fetched_at: Optional[int],
               checked_at: Optional[int], size: Optional[int], meta: Optional[Mapping[str, Any]] = None,
               error: Optional[str] = None) -> Row:
    """Eski düzendeki bir varlık diliminin `entity_slices` satırı (maç dilimlerindeki kurallarla aynı)."""
    return {
        "kind": kind,
        "entity_id": entity_id,
        "key": key,
        "sub": sub,
        "state": state,
        "has_payload": int(error is None),
        "fetched_at": fetched_at,
        "checked_at": checked_at,
        "empty_count": 0,
        "unverified_empty_count": 0,
        "error_reason": error,
        "error_status": None,
        "error_at": None,
        "error_count": 1 if error is not None else 0,
        "stored_bytes": size,
        "raw_bytes": None,
        "history_count": 0,
        "meta_json": meta_json(meta),
        "layout": LAYOUT_LEGACY,
        "path": path,
    }


def _problem(exc: StoreError, rel: str) -> LegacyProblem:
    return legacy._problem_of(exc, rel)


def read_season(reader: LegacyReader, tournament_id: int, season_id: int,
                pages: Sequence[LegacySchedulePage] = (),
                summaries: Sequence[LegacySummaryFile] = ()) -> SeasonListing:
    """
    Bir sezonun dosyalarını okur. pages: sezonun bütün tur / sayfa dosyaları, `LegacyReader.schedule_pages`
    sırasıyla (mtime, sonra yol); geçersiz kopyalar (`superseded_by`) okunmaz. Aynı maç birden çok sayfada
    geçiyorsa en yeni sayfadaki hali geçerlidir. summaries: sezonun özet dosyaları; yalnızca hiç tur / sayfa
    dosyası yoksa ve yalnızca CSV'leri okunur (bölüm 3.4, adım 2).
    """
    out = SeasonListing(tournament_id, season_id)
    if pages:
        out.source = SOURCE_PAGES
        for page in pages:
            if page.superseded_by is None:
                _read_page(reader, page, out)
        return out
    tables = sorted((s for s in summaries if s.path.endswith(".csv")), key=lambda s: (s.mtime_ns, s.path))
    if tables:
        out.source = SOURCE_SUMMARY
        for summary in tables:
            _read_summary(reader, summary, out)
    return out


def _add(out: SeasonListing, event_id: int, payload: Mapping[str, Any], sub: Optional[str], fetched_at: int,
         path: str, comparable: bool) -> None:
    previous = out.events.get(event_id)
    first = min(previous.first_seen_at, fetched_at) if previous is not None else fetched_at
    out.events[event_id] = ListedEvent(event_id, payload, sub, fetched_at, first, path, comparable)


def _read_page(reader: LegacyReader, page: LegacySchedulePage, out: SeasonListing) -> None:
    fetched_at = page.mtime_ns // _NS
    try:
        schedule = reader.read_schedule(page)
    except StoreError as exc:
        out.problems.append(_problem(exc, page.path))
        out.slices.append(_slice_row(KIND_SEASON, out.season_id, KEY_SCHEDULE, page.sub, page.path, state="error",
                                     fetched_at=None, checked_at=fetched_at, size=None, error=legacy.REASON_CORRUPT))
        return
    listed = schedule.events
    out.slices.append(_slice_row(KIND_SEASON, out.season_id, KEY_SCHEDULE, page.sub, page.path,
                                 state="ok" if listed else "empty", fetched_at=fetched_at, checked_at=fetched_at,
                                 size=page.size, meta=schedule.meta))
    unusable = 0
    for item in listed:
        event_id = _plain_int(item.get("id")) if isinstance(item, Mapping) else None
        if event_id is None:
            unusable += 1
            continue
        _add(out, event_id, item, page.sub, fetched_at, page.path, True)
    if unusable:
        out.problems.append(LegacyProblem(page.path, legacy.PROBLEM_MALFORMED, f"kimliği olmayan liste öğesi: {unusable}"))


def _read_summary(reader: LegacyReader, summary: LegacySummaryFile, out: SeasonListing) -> None:
    fetched_at = summary.mtime_ns // _NS
    try:
        rows = reader.read_summary_rows(summary)
    except StoreError as exc:
        out.problems.append(_problem(exc, summary.path))
        return
    except csv.Error as exc:
        out.problems.append(LegacyProblem(summary.path, legacy.PROBLEM_CORRUPT, str(exc)))
        return
    unusable = 0
    for row in rows:
        event = summary_event(row, out.tournament_id, out.season_id)
        if event is None:
            unusable += 1
            continue
        _add(out, event["id"], event, round_sub(row.get("round")), fetched_at, summary.path, False)
    if unusable:
        out.problems.append(LegacyProblem(summary.path, legacy.PROBLEM_MALFORMED, f"match_id'siz satır: {unusable}"))


def find_listed(reader: LegacyReader, path: str, event_id: int) -> Optional[Mapping[str, Any]]:
    """Bir program sayfasındaki (DATA_DIR'e göre yol) olay nesnesi; sayfa okunamıyorsa ya da maç orada yoksa None."""
    try:
        data = codec.read_payload(reader.resolve(path))
    except StoreError:
        return None
    listed = data.get("events") if isinstance(data, Mapping) else None
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, Mapping) and _plain_int(item.get("id")) == event_id:
            return item
    return None


def stored_event_payload(reader: LegacyReader, event_id: int, layout_name: Optional[str],
                         path: Optional[str]) -> Optional[Any]:
    """Katalog satırının gösterdiği olay yükü (v3: event.json.gz; eski düzen: basic.json ya da birleşik dosya)."""
    try:
        if layout_name == LAYOUT_V3:
            return codec.read_payload(
                layout.resolve(reader.data_dir, layout.slice_path(layout.event_dir(event_id), EVENT_KEY)))
        if layout_name == LAYOUT_LEGACY and path:
            return reader.read_payload(path, EVENT_KEY)
    except StoreError:
        return None
    return None


# --- kataloğa yazma -----------------------------------------------------------------------------------

def _newest_wins(table: str, columns: Sequence[str], guard: str = "") -> str:
    """`id` çakışınca yalnızca en az o kadar yeni (`updated_at`) satırın yazdığı UPSERT deyimi."""
    return (
        "INSERT INTO {table} ({columns}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates} "
        "WHERE {guard}excluded.updated_at >= {table}.updated_at"
    ).format(
        table=table,
        columns=", ".join(columns),
        marks=", ".join("?" for _ in columns),
        updates=", ".join(f"{name} = excluded.{name}" for name in columns if name != "id"),
        guard=guard,
    )


_PARTICIPANT_UPSERT = _newest_wins("participants", PARTICIPANT_COLUMNS)
_TOURNAMENT_COLUMNS = ("id", "sport", "category_id", "name", "name_folded", "slug", "updated_at")
_TOURNAMENT_UPSERT = _newest_wins("tournaments", _TOURNAMENT_COLUMNS)
_SEASON_COLUMNS = ("id", "tournament_id", "name", "year", "sort_key", "updated_at")
# Sezon listesinden gelen satır (listed = 1) asıl kaynaktır: liste öğesindeki sezon nesnesi onu değiştirmez
_SEASON_UPSERT = _newest_wins("seasons", _SEASON_COLUMNS, guard="seasons.listed = 0 AND ")


def write_entity_rows(cat: Catalog, groups: Iterable[derive.EntityRows], *, newest: bool = False) -> None:
    """
    Olay nesnelerinde geçen varlıkların satırları; `Catalog.write()` bloğunun içinde çağrılır. Yarışmacı
    satırında en yeni kaynak kazanır (`updated_at`); spor ve kategori satırı yalnızca yoksa eklenir.

    newest=False (olay yükleri, özet CSV'leri): turnuva ve sezon satırı da yalnızca yoksa eklenir (bölüm 3.4,
    adım 4).
    newest=True (program sayfaları): turnuva satırında ve sezon listesinde olmayan sezonun satırında en yeni
    sayfa kazanır; turnuvanın adı değişince katalog yenisini gösterir. Adı olmayan turnuva satırı yazılmaz.
    """
    found = list(groups)
    conn = cat.connection()
    for table, pick in (("sports", lambda e: e.sport), ("categories", lambda e: e.category)):
        cat.upsert(table, [row for row in (pick(entity) for entity in found) if row is not None],
                   on_conflict="ignore")
    tournaments = [entity.tournament for entity in found if entity.tournament is not None]
    seasons = [entity.season for entity in found if entity.season is not None]
    if newest:
        conn.executemany(_TOURNAMENT_UPSERT, [tuple(row[name] for name in _TOURNAMENT_COLUMNS)
                                              for row in tournaments if row.get("name")])
        conn.executemany(_SEASON_UPSERT, [tuple(row[name] for name in _SEASON_COLUMNS) for row in seasons])
    else:
        cat.upsert("tournaments", tournaments, on_conflict="ignore")
        cat.upsert("seasons", seasons, on_conflict="ignore")
    conn.executemany(
        _PARTICIPANT_UPSERT,
        [tuple(row[name] for name in PARTICIPANT_COLUMNS) for entity in found for row in entity.participants],
    )


def apply_season_lists(cat: Catalog, reader: LegacyReader, lists: Sequence[LegacySeasonList], *,
                       fresh: bool = False) -> int:
    """
    Sezon listelerini kataloğa yazar ve yazılan liste sayısını döndürür; `write()` bloğunun içinde çağrılır.
    lists: `LegacyReader.season_lists` sonucu; turnuvası bulunamayan ve geçersiz (daha yeni kopyası olan)
    dosyalar atlanır. fresh=False: önce eski listelerin izleri silinir (`listed`, `position` ve eski düzen
    `entity_slices` satırları), sonra hepsi yeniden yazılır; dosyalar küçük olduğu için parça parça
    güncellenmez. `seasons` satırı silinmez: listeden çıkan sezon `listed = 0` olarak kalır.
    """
    conn = cat.connection()
    if not fresh:
        conn.execute("UPDATE seasons SET listed = 0, position = NULL WHERE listed != 0 OR position IS NOT NULL")
        conn.execute("DELETE FROM entity_slices WHERE kind = ? AND key = ? AND layout = ?",
                     (KIND_TOURNAMENT, KEY_SEASONS, LAYOUT_LEGACY))
    written = 0
    for item in lists:
        if item.tournament_id is None or item.superseded_by is not None:
            continue
        fetched_at = item.mtime_ns // _NS
        rows = derive.season_list_rows(item.payload, tournament_id=item.tournament_id, updated_at=fetched_at)
        cat.upsert("seasons", [storable(row) for row in rows])
        size: Optional[int] = None
        if item.kind == "json":  # CSV tek dosyada bütün turnuvaları tutar: boyutu bu listenin boyutu değil
            signature = reader.signature(item.path)
            size = int(signature.rsplit(":", 1)[1]) if signature else None
        cat.upsert("entity_slices", [_slice_row(
            KIND_TOURNAMENT, item.tournament_id, KEY_SEASONS, "", item.path, state="ok" if rows else "empty",
            fetched_at=fetched_at, checked_at=fetched_at, size=size)])
        written += 1
    return written


def tournament_sport(cat: Catalog, tournament_id: int) -> Optional[str]:
    """Turnuvanın katalogdaki sporu: sporunu söylemeyen liste öğesi için yedek değer."""
    row = cat.connection().execute("SELECT sport FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    return str(row[0]) if row is not None and row[0] else None


def _listed_sport(listing: SeasonListing) -> Optional[str]:
    """Listedeki öğelerden sporunu söyleyen ilkinin sporu (turnuvanın katalogda satırı yokken yedek değer)."""
    for listed in listing.events.values():
        slug = event_sport_slug(dict(listed.payload))
        if slug:
            return slug
    return None


def _listing_row(listed: ListedEvent, tournament_id: int, season_id: int, sport: Optional[str]) -> Tuple[Row, bool]:
    """Liste satırı ve "yük başka bir turnuva / sezon söylüyor" bilgisi."""
    row = derive.event_row(listed.payload, "listing", None, sport=sport)
    mismatch = row["tournament_id"] not in (None, tournament_id) or row["season_id"] not in (None, season_id)
    row["tournament_id"] = tournament_id
    row["season_id"] = season_id
    row.update({
        "status_regressed": 0, "stale": 0, "listed_in": listed.sub, "layout": None, "path": None,
        "legacy_path": None, "sig": None, "first_seen_at": listed.first_seen_at, "updated_at": listed.fetched_at,
    })
    return storable(row), mismatch


def _entity_rows(listed: ListedEvent, sport: Optional[str]) -> derive.EntityRows:
    """Liste öğesinde geçen varlıkların satırları, yazılabilir halde; zamanları listenin zamanıdır."""
    found = derive.event_entity_rows(listed.payload, updated_at=listed.fetched_at, sport=sport)
    for row in (found.sport, found.category, found.tournament, found.season, *found.participants):
        if row is not None:
            storable(row)
    return found


def _chunks(values: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


def apply_season(cat: Catalog, reader: LegacyReader, listing: SeasonListing, *, fresh: bool = False,
                 marks: Optional[Dict[int, ListingMark]] = None,
                 problems: Optional[List[LegacyProblem]] = None) -> SeasonCounts:
    """
    Bir sezonun listesini kataloğa uygular (bölüm 8.2); `Catalog.write()` bloğunun içinde çağrılır.

    fresh=True (yeniden kurma, tablolar boş): listelenen her maç için liste satırı yazılır ve `marks`
    doldurulur; olay yükü olan maçların satırı sonradan o yükten yazılır, `listed_in` / `stale` o sırada
    `marks`'tan gelir.
    fresh=False (uzlaştırma, tek sezon): sezonun önceki payı yenisiyle değiştirilir. Olay yükü olmayan maç
    liste satırı alır; olay yükü olan maçın yalnızca `listed_in` ve `stale` sütunları yazılır (`stale` için
    saklanan olay yükü okunur). Artık listelenmeyen maçın liste satırı silinir, olay yükü olan maçın
    `listed_in`'i boşaltılır.
    """
    conn = cat.connection()
    tournament_id, season_id = listing.tournament_id, listing.season_id
    notes: List[LegacyProblem] = problems if problems is not None else []
    notes.extend(listing.problems)
    counts = SeasonCounts(pages=len(listing.slices))

    previous: Dict[int, bool] = {}  # bu sezona bağlı maçlar → olay yükü var mı
    stored: Dict[int, Any] = {}
    if not fresh:
        conn.execute("DELETE FROM entity_slices WHERE kind = ? AND entity_id = ? AND key = ? AND layout = ?",
                     (KIND_SEASON, season_id, KEY_SCHEDULE, LAYOUT_LEGACY))
        for found in conn.execute(
                "SELECT id, has_event_payload, layout FROM events WHERE tournament_id = ? AND season_id = ? "
                "AND (row_source = 'listing' OR listed_in IS NOT NULL)", (tournament_id, season_id)):
            previous[int(found[0])] = bool(found[1]) or found[2] is not None
        for chunk in _chunks(list(listing.events)):
            marks_sql = ", ".join("?" for _ in chunk)
            for found in conn.execute(
                    "SELECT e.id, e.has_event_payload, e.layout, e.path, e.tournament_id, e.season_id, "
                    "coalesce(e.observed_at, s.fetched_at) AS compared_at FROM events e "
                    "LEFT JOIN event_slices s ON s.event_id = e.id AND s.key = 'event' AND s.sub = '' "
                    f"WHERE e.id IN ({marks_sql})", tuple(chunk)):
                stored[int(found["id"])] = found
    cat.upsert("entity_slices", (storable(row) for row in listing.slices))

    sport = tournament_sport(cat, tournament_id) or _listed_sport(listing)
    rows: List[Row] = []
    links: List[Row] = []
    entity_rows: List[derive.EntityRows] = []
    flags: List[Tuple[Optional[str], int, int]] = []
    attached = set()
    for event_id, listed in listing.events.items():
        existing = stored.get(event_id)
        if existing is not None and (existing["has_event_payload"] or existing["layout"] is not None):
            # Kural 1: olay yükü olan maçın satırına liste dokunmaz (listed_in ve stale dışında)
            if (existing["tournament_id"], existing["season_id"]) != (tournament_id, season_id):
                notes.append(LegacyProblem(
                    listed.path, PROBLEM_SEASON_MISMATCH,
                    f"maç {event_id}: olay yükü {existing['tournament_id']}/{existing['season_id']} diyor"))
                continue
            stale = False
            compared_at = existing["compared_at"]
            if listed.comparable and compared_at is not None and listed.fetched_at > compared_at:
                # Liste daha yeni: saklanan olay yükü okunur ve karşılaştırılır (yalnızca bu durumda)
                payload = stored_event_payload(reader, event_id, existing["layout"], existing["path"])
                if payload is not None:
                    stale = is_stale(listed.fetched_at, compare_digest(listed.payload), compared_at,
                                     compare_digest(payload))
            try:  # liste öğesindeki varlıklar (yarışmacılar, turnuva) maçın yükü olsa da yazılır
                entity_rows.append(_entity_rows(listed, sport))
            except _DERIVE_ERRORS as exc:
                notes.append(LegacyProblem(listed.path, legacy.PROBLEM_MALFORMED, f"maç {event_id}: {exc}"))
            flags.append((listed.sub, int(stale), event_id))
            attached.add(event_id)
            continue
        if marks is not None:  # satırı türetilemese de maç listelenmiştir: olay yükü gelirse listed_in alır
            marks[event_id] = ListingMark(tournament_id, season_id, listed.sub, listed.fetched_at,
                                          compare_digest(listed.payload) if listed.comparable else None)
        try:
            row, mismatch = _listing_row(listed, tournament_id, season_id, sport)
            entities = _entity_rows(listed, sport)
        except _DERIVE_ERRORS as exc:
            notes.append(LegacyProblem(listed.path, legacy.PROBLEM_MALFORMED, f"maç {event_id}: {exc}"))
            continue
        if mismatch:
            notes.append(LegacyProblem(listed.path, PROBLEM_SEASON_MISMATCH,
                                       f"maç {event_id}: liste öğesi başka bir turnuva / sezon söylüyor"))
        rows.append(row)
        links.extend(derive.event_participant_rows(row))
        entity_rows.append(entities)
        attached.add(event_id)

    conn.executemany("DELETE FROM event_participants WHERE event_id = ?", [(row["id"],) for row in rows])
    cat.upsert("events", rows)
    cat.upsert("event_participants", links)
    if listing.source == SOURCE_PAGES:
        write_entity_rows(cat, entity_rows, newest=True)
    else:
        # Özet CSV'sinden kurulan nesnede turnuvanın yalnızca kimliği, sezonun yalnızca adı vardır: boş bir
        # turnuva satırı yazılmaz (sonradan gelecek gerçek satırın yerini tutmasın), var olan sezon satırı
        # da değiştirilmez
        write_entity_rows(cat, (
            dataclasses.replace(found, tournament=None)
            if found.tournament is not None and not found.tournament.get("name") else found
            for found in entity_rows))
    conn.executemany("UPDATE events SET listed_in = ?, stale = ? WHERE id = ?", flags)

    gone = [event_id for event_id in previous if event_id not in attached]
    removed = [(event_id,) for event_id in gone if not previous[event_id]]
    cleared = [(event_id,) for event_id in gone if previous[event_id]]
    conn.executemany("DELETE FROM events WHERE id = ? AND has_event_payload = 0 AND layout IS NULL", removed)
    conn.executemany("DELETE FROM event_participants WHERE event_id = ?", removed)
    conn.executemany("UPDATE events SET listed_in = NULL, stale = 0 WHERE id = ?", cleared)

    counts.listed = len(attached)
    counts.rows = len(rows)
    counts.flagged = len(flags)
    counts.removed = len(removed)
    counts.cleared = len(cleared)
    return counts


def attached_seasons(cat: Catalog, tournament_id: Optional[int] = None) -> List[SeasonKey]:
    """Katalogda listelerden payı olan sezonlar (liste satırı ya da `listed_in`'i dolu maçı olanlar)."""
    sql = ("SELECT DISTINCT tournament_id, season_id FROM events WHERE (row_source = 'listing' OR listed_in IS NOT NULL) "
           "AND tournament_id IS NOT NULL AND season_id IS NOT NULL")
    args: Tuple[Any, ...] = ()
    if tournament_id is not None:
        sql += " AND tournament_id = ?"
        args = (tournament_id,)
    return sorted((int(row[0]), int(row[1])) for row in cat.connection().execute(sql, args))


def seasons_missing_sport(cat: Catalog) -> List[SeasonKey]:
    """
    Sporu bilinmeyen liste satırı olan ve turnuvasının sporu artık katalogda duran sezonlar. Sporunu
    söylemeyen liste öğesi (özet CSV'sinin her satırı) sporu turnuvanın satırından alır; o satır sonradan
    geldiyse (ör. turnuvanın ilk olay yükü) sezonun listesi yeniden dizinlenmelidir: skor çizelgesi de spora
    bağlıdır.
    """
    found = cat.connection().execute(
        "SELECT DISTINCT e.tournament_id, e.season_id FROM events e JOIN tournaments t ON t.id = e.tournament_id "
        "WHERE e.sport = '' AND e.has_event_payload = 0 AND e.layout IS NULL AND e.season_id IS NOT NULL "
        "AND t.sport IS NOT NULL AND t.sport != ''")
    return sorted((int(row[0]), int(row[1])) for row in found)


__all__ = [
    "KIND_TOURNAMENT",
    "KIND_SEASON",
    "KEY_SEASONS",
    "KEY_SCHEDULE",
    "PROBLEM_SEASON_MISMATCH",
    "SOURCE_PAGES",
    "SOURCE_SUMMARY",
    "SOURCE_NONE",
    "PARTICIPANT_COLUMNS",
    "SeasonKey",
    "ListedEvent",
    "SeasonListing",
    "ListingMark",
    "SeasonCounts",
    "clean_text",
    "storable",
    "meta_json",
    "diff_fields",
    "compare_digest",
    "is_stale",
    "round_sub",
    "summary_event",
    "read_season",
    "find_listed",
    "stored_event_payload",
    "write_entity_rows",
    "apply_season_lists",
    "tournament_sport",
    "apply_season",
    "attached_seasons",
    "seasons_missing_sport",
]
