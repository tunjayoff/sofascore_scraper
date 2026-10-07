"""
Maç dışı varlıklar: sezon listelerinin, program sayfalarının ve onlardan gelen liste satırlarının
dizinlenmesi (docs/design/01-storage.md, bölüm 3.4 adım 2, 5.2 ve 8.2) ve okuma API'si `EntityStore`
(bölüm 2.3).

İki düzen birlikte dizinlenir. Yazıcılar (plan maddesi ST-22) sezon listelerini ve program sayfalarını
`EntityStore.put` ile v3 düzenine yazar: turnuva `v3/tournaments/<ut>/` (`manifest.json`,
`seasons.json.gz`), sezon `v3/tournaments/<ut>/seasons/<sid>/` (`manifest.json`, `schedule/<alt anahtar>.json.gz`).
Eski düzen dosyaları (`seasons/`, `matches/`) yerinde kalır ve okunur. Kural: bir turnuvanın v3 sezon listesi
varsa eski düzen listesi kullanılmaz; bir sezonun aynı alt anahtarlı sayfası iki düzende de varsa v3'teki
geçerlidir, öteki sayfalar birlikte okunur (`read_season`). v3 dizinlerinin liste olmayan dilimleri ve geçmiş
dosyaları `apply_v3_entities` / `index_v3_entity` ile dizinlenir (dizinleyici onları adıyla çağırır).

`EntityStore` okur ve yazar: turnuva, sezon, yarışmacı, kategori ve spor satırları, varlık dilimlerinin
durumu ve yükleri (dosyanın yerini `entity_slices` satırı söyler; okuma kuralı sofascore_scraper/store/events.py'deki ile
aynıdır) ve `put`.

Katalogda neyin nereden geldiği:

  * `entity_slices`: her geçerli sezon listesi (`tournament` / `seasons`) ve her geçerli program sayfası
    (`season` / `schedule` / `round_12`, `last_0`, ...) için bir satır; eski düzende `layout = 'legacy'` ve
    `path` dosyanın yolu, v3'te `layout = 'v3'` ve `path` varlık dizini (sütunlar manifestten). Tur dosyasının `_complete` anahtarı `meta_json`'a `{"complete": ...}` olarak, süzülerek yazılmış
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
    da herhangi bir skor alanında ondan farklıysa. Karşılaştırılan alanlar `sofascore_scraper/refresh.diff_basic` ile
    aynıdır (`diff_fields`; Store `sofascore_scraper.refresh`'i içe aktaramaz, eşitliği test denetler). Olay yükünün zamanı
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

import contextlib
import copy
import csv
import dataclasses
import hashlib
import json
import logging
import math
import os
import re
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

from sofascore_scraper.slices import SLICE_FAILED, SLICE_OK, Outcome
from sofascore_scraper.sports import event_sport_slug
from sofascore_scraper.store import codec, derive, files, history, layout, legacy
from sofascore_scraper.store import manifest as manifest_mod
from sofascore_scraper.store.catalog import Catalog
from sofascore_scraper.store.errors import LayoutError, PayloadCorrupt, PayloadMissing, StoreBusy, StoreError
from sofascore_scraper.store.events import (
    ABSENT,
    SLICE_COLUMNS,
    STEP_COMMIT,
    STEP_DONE,
    STEP_HISTORY,
    STEP_INDEXED,
    STEP_LOCKED,
    STEP_MANIFEST,
    STEP_MARKER,
    STEP_PAYLOAD,
    PutResult,
    Ref,
    SliceInfo,
    SliceKey,
    check_int,
    int_list,
    like_pattern,
    not_requested,
    _MARKER_ATTEMPTS,
    _NOTHING_WRITTEN,
    _aware,
    _history_keys,
    _Item,
    _natural,
    _slice_file,
    _utc_now,
    _Write,
    read_with_retry,
    slice_info,
)
from sofascore_scraper.store.events import _items as _outcome_items
from sofascore_scraper.store.manifest import EmptyMark, ErrorMark, Manifest, SliceEntry
from sofascore_scraper.store.legacy import (
    LegacyProblem,
    LegacyReader,
    LegacySchedulePage,
    LegacySeasonList,
    LegacySummaryFile,
)

if TYPE_CHECKING:
    from sofascore_scraper.store.api import Store

logger = logging.getLogger(__name__)

T = TypeVar("T")
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
    `startTimestamp` ve `homeScore` / `awayScore`'un bütün alt alanları (sofascore_scraper/refresh.py `_flatten`).
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


# --- v3 varlık dizinleri: okuma -----------------------------------------------------------------------

V3_SEASONS_DIR = "seasons"  # turnuva dizininin altında sezon dizinleri (v3/tournaments/<ut>/seasons/<sid>)
# Liste dilimleri: katalogdaki karşılıkları (sezon satırları, liste satırları) eski düzen dosyalarıyla birlikte
# `apply_season_lists` ve `apply_season` tarafından yazılır; v3 dizininin öteki dilimleri `apply_v3_entities`'te
LISTING_SLICES: Tuple[Tuple[str, str], ...] = ((KIND_TOURNAMENT, KEY_SEASONS), (KIND_SEASON, KEY_SCHEDULE))


@dataclass(frozen=True)
class V3Entity:
    """v3 ağacındaki, manifesti okunabilen bir varlık dizini (maç dışı)."""

    kind: str
    entity_id: int
    directory: str  # DATA_DIR'e göre
    manifest: Manifest
    tournament_id: Optional[int] = None  # sezonda: dizinin altında durduğu turnuva


@dataclass(frozen=True)
class V3SchedulePage:
    """v3 sezon dizinindeki bir program sayfası (`schedule/<alt anahtar>`), manifestteki kaydıyla."""

    tournament_id: int
    season_id: int
    sub: str
    directory: str  # sezon dizini (DATA_DIR'e göre)
    entry: SliceEntry

    @property
    def path(self) -> str:
        """Yük dosyası (DATA_DIR'e göre)."""
        return layout.slice_path(self.directory, KEY_SCHEDULE, self.sub)

    @property
    def fetched_ns(self) -> int:
        """Sayfanın sırası için zaman (nanosaniye): yükün alındığı an, yoksa son deneme."""
        moment = self.entry.fetched_at or self.entry.checked_at
        if moment is None:
            return 0
        aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
        delta = aware - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86_400 + delta.seconds) * _NS + delta.microseconds * 1000


def _numbered_dirs(data_dir: str, rel: str, problems: Optional[List[LegacyProblem]] = None) -> List[int]:
    """`rel` altındaki, adı kurallı bir kimlik olan dizinler (sayı sırasıyla). Dizin yoksa boş liste."""
    root = layout.resolve(data_dir, rel)
    try:
        names = os.listdir(root)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        if problems is not None:
            problems.append(LegacyProblem(rel, legacy.PROBLEM_UNREADABLE, str(exc)))
        return []
    found = []
    for name in names:
        if name.isascii() and name.isdigit() and str(int(name)) == name and os.path.isdir(os.path.join(root, name)):
            found.append(int(name))
    return sorted(found)


def read_v3_manifest(data_dir: str, directory: str, kind: str, entity_id: int,
                     problems: Optional[List[LegacyProblem]] = None) -> Optional[Manifest]:
    """
    Varlık dizininin manifesti; dosya yoksa None (dizin henüz bir varlık değildir: ör. yalnızca sezon dizinlerini
    taşıyan turnuva dizini). Okunamayan ya da başka bir varlığa ait manifest sorun olarak bildirilir ve None döner.
    """
    rel = layout.manifest_path(directory)
    try:
        found = manifest_mod.read_manifest(layout.resolve(data_dir, rel))
    except PayloadMissing:
        return None
    except StoreError as exc:
        if problems is not None:
            problems.append(LegacyProblem(rel, legacy.PROBLEM_CORRUPT, str(exc.detail or exc)))
        return None
    if found.kind != kind or found.id != entity_id:
        if problems is not None:
            problems.append(LegacyProblem(rel, legacy.PROBLEM_ID_MISMATCH,
                                          f"manifest {found.kind} {found.id!r}, dizin {kind} {entity_id}"))
        return None
    return found


def v3_entities(data_dir: str, problems: Optional[List[LegacyProblem]] = None) -> List[V3Entity]:
    """
    v3 ağacındaki maç dışı varlıklar, sabit sırayla: turnuvalar (her biri sezonlarından önce), takımlar,
    oyuncular, sporlar. Manifesti olmayan dizin atlanır; kurallı yerinde durmayan dizin sorun olarak bildirilir.
    """
    out: List[V3Entity] = []
    for tournament_id in _numbered_dirs(data_dir, layout.TOURNAMENTS_DIR, problems):
        directory = layout.tournament_dir(tournament_id)
        found = read_v3_manifest(data_dir, directory, KIND_TOURNAMENT, tournament_id, problems)
        if found is not None:
            out.append(V3Entity(KIND_TOURNAMENT, tournament_id, directory, found))
        for season_id in _numbered_dirs(data_dir, f"{directory}/{V3_SEASONS_DIR}", problems):
            season_dir = layout.season_dir(tournament_id, season_id)
            season = read_v3_manifest(data_dir, season_dir, KIND_SEASON, season_id, problems)
            if season is not None:
                out.append(V3Entity(KIND_SEASON, season_id, season_dir, season, tournament_id))
    buckets: Tuple[Tuple[str, str, Callable[[int], str]], ...] = (
        ("team", layout.TEAMS_DIR, layout.team_dir), ("player", layout.PLAYERS_DIR, layout.player_dir))
    for kind, root, place in buckets:
        for bucket in _numbered_dirs(data_dir, root, problems):
            for entity_id in _numbered_dirs(data_dir, f"{root}/{bucket}", problems):
                directory = place(entity_id)
                if directory != f"{root}/{bucket}/{entity_id}":
                    if problems is not None:
                        problems.append(LegacyProblem(f"{root}/{bucket}/{entity_id}", legacy.PROBLEM_NAME,
                                                      f"{kind} dizini kurallı yerinde değil ({directory})"))
                    continue
                found = read_v3_manifest(data_dir, directory, kind, entity_id, problems)
                if found is not None:
                    out.append(V3Entity(kind, entity_id, directory, found))
    for sport_id in _numbered_dirs(data_dir, layout.SPORTS_DIR, problems):
        directory = layout.sport_dir(sport_id)
        found = read_v3_manifest(data_dir, directory, "sport", sport_id, problems)
        if found is not None:
            out.append(V3Entity("sport", sport_id, directory, found))
    return out


def v3_season_tournaments(data_dir: str, season_id: int) -> List[int]:
    """Sezonun v3 dizininin altında durduğu turnuvalar (normalde en çok bir tane)."""
    return [tournament_id for tournament_id in _numbered_dirs(data_dir, layout.TOURNAMENTS_DIR)
            if os.path.isdir(layout.resolve(data_dir, layout.season_dir(tournament_id, season_id)))]


def _split_slices(found: Manifest, problems: Optional[List[LegacyProblem]],
                  directory: str) -> List[Tuple[Tuple[str, str], SliceEntry]]:
    """Manifestin dilimleri ((anahtar, alt anahtar), kayıt), ada göre sıralı; geçersiz ad sorun olarak bildirilir."""
    out = []
    for name in sorted(found.slices):
        try:
            out.append((layout.split_slice_name(name), found.slices[name]))
        except LayoutError as exc:
            if problems is not None:
                problems.append(LegacyProblem(layout.manifest_path(directory), legacy.PROBLEM_MALFORMED, str(exc)))
    return out


def v3_season_pages(data_dir: str, tournament_id: int, season_id: int,
                    problems: Optional[List[LegacyProblem]] = None) -> List[V3SchedulePage]:
    """Sezonun v3 dizinindeki program sayfaları (manifestteki `schedule/<alt anahtar>` dilimleri)."""
    directory = layout.season_dir(tournament_id, season_id)
    found = read_v3_manifest(data_dir, directory, KIND_SEASON, season_id, problems)
    if found is None:
        return []
    return [V3SchedulePage(tournament_id, season_id, sub, directory, entry)
            for (key, sub), entry in _split_slices(found, problems, directory) if key == KEY_SCHEDULE]


def v3_schedule_seasons(data_dir: str, problems: Optional[List[LegacyProblem]] = None) -> List[SeasonKey]:
    """
    v3 ağacında program sayfası olan sezonlar: (turnuva, sezon). Dizinleyici bunları eski düzendeki sayfalı
    sezonlarla birlikte dizinler (`read_season` iki düzeni birleştirir).
    """
    out: List[SeasonKey] = []
    for tournament_id in _numbered_dirs(data_dir, layout.TOURNAMENTS_DIR, problems):
        for season_id in _numbered_dirs(data_dir, f"{layout.tournament_dir(tournament_id)}/{V3_SEASONS_DIR}"):
            if v3_season_pages(data_dir, tournament_id, season_id, problems):
                out.append((tournament_id, season_id))
    return out


def v3_slice_row(kind: str, entity_id: int, key: str, sub: str, entry: SliceEntry, directory: str) -> Row:
    """v3 varlık diliminin `entity_slices` satırı: manifestteki kayıttan (bölüm 4.2); `path` varlık dizinidir."""
    empty, error, mark = entry.empty, entry.error, entry.history
    return {
        "kind": kind,
        "entity_id": entity_id,
        "key": key,
        "sub": sub,
        "state": entry.state,
        "has_payload": int(entry.has_payload),
        "fetched_at": derive.epoch_seconds(entry.fetched_at),
        "checked_at": derive.epoch_seconds(entry.checked_at),
        "empty_count": empty.count if empty else 0,
        "unverified_empty_count": empty.unverified if empty else 0,
        "error_reason": error.reason if error else None,
        "error_status": error.status if error else None,
        "error_at": derive.epoch_seconds(error.at) if error else None,
        "error_count": error.count if error else 0,
        "stored_bytes": entry.stored_bytes,
        "raw_bytes": entry.raw_bytes,
        "history_count": mark.count if mark else 0,
        "meta_json": meta_json(entry.meta),
        "layout": LAYOUT_V3,
        "path": directory,
    }


@dataclass(frozen=True)
class V3SeasonList:
    """v3 turnuva dizinindeki sezon listesi (`seasons` dilimi) ve okunabildiyse yükü."""

    tournament_id: int
    directory: str
    entry: SliceEntry
    payload: Optional[Mapping[str, Any]]


def _v3_season_list(data_dir: str, tournament_id: int, found: Optional[Manifest],
                    problems: Optional[List[LegacyProblem]]) -> Optional[V3SeasonList]:
    entry = found.slices.get(KEY_SEASONS) if found is not None else None
    if entry is None:
        return None
    directory = layout.tournament_dir(tournament_id)
    payload: Optional[Mapping[str, Any]] = None
    if entry.has_payload:
        path = layout.slice_path(directory, KEY_SEASONS)
        try:
            data = codec.read_payload(layout.resolve(data_dir, path))
        except StoreError as exc:
            if problems is not None:
                problems.append(_problem(exc, path))
        else:
            payload = data if isinstance(data, Mapping) else None
    return V3SeasonList(tournament_id, directory, entry, payload)


def v3_season_lists(data_dir: str, problems: Optional[List[LegacyProblem]] = None) -> List[V3SeasonList]:
    """v3 ağacındaki bütün sezon listeleri, turnuva kimliği sırasıyla."""
    out: List[V3SeasonList] = []
    for tournament_id in _numbered_dirs(data_dir, layout.TOURNAMENTS_DIR, problems):
        found = read_v3_manifest(data_dir, layout.tournament_dir(tournament_id), KIND_TOURNAMENT, tournament_id,
                                 problems)
        item = _v3_season_list(data_dir, tournament_id, found, problems)
        if item is not None:
            out.append(item)
    return out


# --- bir sezonun listesi --------------------------------------------------------------------------------

def read_season(reader: LegacyReader, tournament_id: int, season_id: int,
                pages: Sequence[LegacySchedulePage] = (),
                summaries: Sequence[LegacySummaryFile] = ()) -> SeasonListing:
    """
    Bir sezonun dosyalarını okur. pages: sezonun eski düzendeki bütün tur / sayfa dosyaları,
    `LegacyReader.schedule_pages` sırasıyla (mtime, sonra yol); geçersiz kopyalar (`superseded_by`) okunmaz.
    Sezonun v3 dizinindeki sayfalar (`v3/tournaments/<ut>/seasons/<sid>/schedule/`) burada bulunur ve iki düzen
    birleşir: aynı alt anahtarın v3 kopyası eski düzendekinin yerine geçer (yazıcılar artık yalnızca v3'e
    yazar), öteki sayfalar birlikte okunur. Sayfalar zaman sırasıyla (eski düzende mtime, v3'te manifestteki
    `fetched_at`; eşitlikte yol) uygulanır; aynı maç birden çok sayfada geçiyorsa en yeni sayfadaki hali
    geçerlidir. summaries: sezonun özet dosyaları; yalnızca hiç tur / sayfa dosyası yoksa ve yalnızca CSV'leri
    okunur (bölüm 3.4, adım 2).
    """
    out = SeasonListing(tournament_id, season_id)
    current = v3_season_pages(reader.data_dir, tournament_id, season_id, out.problems)
    if pages or current:
        out.source = SOURCE_PAGES
        replaced = {page.sub for page in current}
        ordered: List[Tuple[int, str, Union[V3SchedulePage, LegacySchedulePage]]] = [
            (page.fetched_ns, page.path, page) for page in current]
        ordered.extend((page.mtime_ns, page.path, page) for page in pages
                       if page.superseded_by is None and page.sub not in replaced)
        for _ns, _path, page in sorted(ordered, key=lambda item: (item[0], item[1])):
            if isinstance(page, V3SchedulePage):
                _read_v3_page(reader.data_dir, page, out)
            else:
                _read_page(reader, page, out)
        return out
    tables = sorted((s for s in summaries if s.path.endswith(".csv")), key=lambda s: (s.mtime_ns, s.path))
    if tables:
        out.source = SOURCE_SUMMARY
        for summary in tables:
            _read_summary(reader, summary, out)
    return out


def _read_v3_page(data_dir: str, page: V3SchedulePage, out: SeasonListing) -> None:
    """v3 program sayfası: dilim satırı manifestten, liste öğeleri yük dosyasından."""
    out.slices.append(v3_slice_row(KIND_SEASON, out.season_id, KEY_SCHEDULE, page.sub, page.entry,
                                   page.directory))
    if not page.entry.has_payload:
        return
    try:
        payload = codec.read_payload(layout.resolve(data_dir, page.path))
    except StoreError as exc:
        out.problems.append(_problem(exc, page.path))
        return
    listed = payload.get("events") if isinstance(payload, Mapping) else None
    fetched_at = derive.epoch_seconds(page.entry.fetched_at) or 0
    unusable = 0
    for item in listed if isinstance(listed, list) else []:
        event_id = _plain_int(item.get("id")) if isinstance(item, Mapping) else None
        if event_id is None:
            unusable += 1
            continue
        _add(out, event_id, item, page.sub, fetched_at, page.path, True)
    if unusable:
        out.problems.append(LegacyProblem(page.path, legacy.PROBLEM_MALFORMED,
                                          f"kimliği olmayan liste öğesi: {unusable}"))


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


def _apply_v3_season_list(cat: Catalog, item: V3SeasonList) -> None:
    """v3 sezon listesinin `seasons` satırları ve dilim satırı (yükü okunamadıysa yalnızca dilim satırı)."""
    if item.payload is not None:
        rows = derive.season_list_rows(item.payload, tournament_id=item.tournament_id,
                                       updated_at=derive.epoch_seconds(item.entry.fetched_at) or 0)
        cat.upsert("seasons", [storable(row) for row in rows])
    cat.upsert("entity_slices", [v3_slice_row(KIND_TOURNAMENT, item.tournament_id, KEY_SEASONS, "", item.entry,
                                              item.directory)])


def _apply_legacy_season_list(cat: Catalog, reader: LegacyReader, item: LegacySeasonList) -> None:
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


def apply_season_lists(cat: Catalog, reader: LegacyReader, lists: Sequence[LegacySeasonList], *,
                       fresh: bool = False, problems: Optional[List[LegacyProblem]] = None) -> int:
    """
    Sezon listelerini kataloğa yazar ve yazılan liste sayısını döndürür; `write()` bloğunun içinde çağrılır.
    lists: `LegacyReader.season_lists` sonucu; turnuvası bulunamayan ve geçersiz (daha yeni kopyası olan)
    dosyalar atlanır. v3 ağacındaki listeler (`v3/tournaments/<ut>/seasons.json.gz`) burada bulunur ve önce
    yazılır; v3 listesi olan turnuvanın eski düzen dosyası kullanılmaz (yazıcılar artık yalnızca v3'e yazar).
    fresh=False: önce bütün listelerin izleri silinir (`listed`, `position` ve iki düzenin `entity_slices`
    satırları), sonra hepsi yeniden yazılır; dosyalar küçük olduğu için parça parça güncellenmez. `seasons`
    satırı silinmez: listeden çıkan sezon `listed = 0` olarak kalır.
    """
    conn = cat.connection()
    if not fresh:
        conn.execute("UPDATE seasons SET listed = 0, position = NULL WHERE listed != 0 OR position IS NOT NULL")
        conn.execute("DELETE FROM entity_slices WHERE kind = ? AND key = ?", (KIND_TOURNAMENT, KEY_SEASONS))
    written = 0
    covered = set()
    for found in v3_season_lists(reader.data_dir, problems):
        _apply_v3_season_list(cat, found)
        covered.add(found.tournament_id)
        written += 1
    for item in lists:
        if item.tournament_id is None or item.superseded_by is not None or item.tournament_id in covered:
            continue
        _apply_legacy_season_list(cat, reader, item)
        written += 1
    return written


def apply_tournament_season_list(cat: Catalog, reader: LegacyReader, tournament_id: int, *,
                                 problems: Optional[List[LegacyProblem]] = None,
                                 lists: Optional[Sequence[LegacySeasonList]] = None) -> bool:
    """
    Tek bir turnuvanın sezon listesini yeniden yazar (`apply_season_lists` ile aynı kural, yalnızca o turnuva
    için); `write()` bloğunun içinde çağrılır. v3 listesi yoksa eski düzen dosyası kullanılır (lists verilmezse
    dosyalar adlar olmadan taranır: adında kimlik olmayan `<ad>_seasons.json` bu yolda bulunmaz). Bir liste
    yazıldıysa True.
    """
    conn = cat.connection()
    conn.execute("UPDATE seasons SET listed = 0, position = NULL WHERE tournament_id = ? "
                 "AND (listed != 0 OR position IS NOT NULL)", (tournament_id,))
    conn.execute("DELETE FROM entity_slices WHERE kind = ? AND entity_id = ? AND key = ?",
                 (KIND_TOURNAMENT, tournament_id, KEY_SEASONS))
    directory = layout.tournament_dir(tournament_id)
    found = _v3_season_list(reader.data_dir, tournament_id,
                            read_v3_manifest(reader.data_dir, directory, KIND_TOURNAMENT, tournament_id, problems),
                            problems)
    if found is not None:
        _apply_v3_season_list(cat, found)
        return True
    for item in lists if lists is not None else reader.season_lists():
        if item.tournament_id == tournament_id and item.superseded_by is None:
            _apply_legacy_season_list(cat, reader, item)
            return True
    return False


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
    if not fresh:  # iki düzenin sayfaları da `listing.slices`'tan yeniden yazılır
        conn.execute("DELETE FROM entity_slices WHERE kind = ? AND entity_id = ? AND key = ?",
                     (KIND_SEASON, season_id, KEY_SCHEDULE))
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


# --- v3 varlık dizinleri: dizinleme (bölüm 3.4, adım 1 ve 3) -------------------------------------------

_LISTING_SQL = " OR ".join(f"(kind = '{kind}' AND key = '{key}')" for kind, key in LISTING_SLICES)


def _entity_rows_v3(entity: V3Entity, problems: Optional[List[LegacyProblem]]) -> List[Row]:
    """Varlık dizininin liste dilimi olmayan dilimlerinin `entity_slices` satırları."""
    return [v3_slice_row(entity.kind, entity.entity_id, key, sub, entry, entity.directory)
            for (key, sub), entry in _split_slices(entity.manifest, problems, entity.directory)
            if (entity.kind, key) not in LISTING_SLICES]


def _write_v3_entity(cat: Catalog, data_dir: str, entity: V3Entity, problems: Optional[List[LegacyProblem]]) -> None:
    cat.upsert("entity_slices", _entity_rows_v3(entity, problems))
    cat.upsert("slice_history", history.history_rows(data_dir, entity.kind, entity.entity_id, entity.directory))


def apply_v3_entities(cat: Catalog, data_dir: Union[str, "os.PathLike[str]"], *, fresh: bool,
                      problems: List[LegacyProblem]) -> None:
    """
    v3 ağacındaki maç dışı varlıkları dizinler (bölüm 3.4, adım 1 ve 3); `write()` bloğunun içinde, eski düzen
    listelerinden önce çağrılır (sofascore_scraper/store/indexer.py, `V3_ENTITY_SCAN`). Burada yazılanlar: liste dilimi
    olmayan her dilimin `entity_slices` satırı ve geçmiş dosyalarının `slice_history` satırları. Liste dilimleri
    (turnuvanın `seasons`'ı, sezonun `schedule/*` sayfaları) eski düzen dosyalarıyla birlikte
    `apply_season_lists` ve `apply_season` tarafından yazılır: iki düzenin hangisinin geçerli olduğuna orada
    karar verilir. fresh=False: v3 varlıklarının bu satırları önce silinir.
    """
    root = os.fspath(data_dir)
    if not fresh:
        conn = cat.connection()
        conn.execute(f"DELETE FROM entity_slices WHERE layout = ? AND NOT ({_LISTING_SQL})", (LAYOUT_V3,))
        conn.execute("DELETE FROM slice_history WHERE kind != ?", (KIND_EVENT,))
    for entity in v3_entities(root, problems):
        _write_v3_entity(cat, root, entity, problems)


def _season_files(reader: LegacyReader, tournament_id: int,
                  season_id: int) -> Tuple[List[LegacySchedulePage], List[LegacySummaryFile]]:
    """Bir sezonun eski düzendeki sayfaları ve özet dosyaları (bütün `matches/` ağacı listelenir, dosya okunmaz)."""
    found = legacy.LegacyReport()
    key = (tournament_id, season_id)
    pages = [page for page in reader.schedule_pages(found) if (page.tournament_id, page.season_id) == key]
    summaries = [item for item in reader.summary_files(found) if (item.tournament_id, item.season_id) == key]
    return pages, summaries


def index_season(cat: Catalog, reader: LegacyReader, tournament_id: int, season_id: int, *,
                 problems: Optional[List[LegacyProblem]] = None) -> SeasonCounts:
    """
    Bir sezonun listesini iki düzenin dosyalarından yeniden dizinler (`read_season` + `apply_season`); `write()`
    bloğunun içinde çağrılır. Sezonun eski düzen dosyaları için `matches/` ağacı listelenir.
    """
    pages, summaries = _season_files(reader, tournament_id, season_id)
    listing = read_season(reader, tournament_id, season_id, pages, summaries)
    return apply_season(cat, reader, listing, problems=problems)


def index_v3_entity(cat: Catalog, data_dir: Union[str, "os.PathLike[str]"], kind: str, entity_id: int, *,
                    problems: List[LegacyProblem]) -> None:
    """
    Maç olmayan tek bir varlığı dosyalarından yeniden dizinler; `write()` bloğunun içinde çağrılır. Yazmanın
    kendisi (`EntityStore.put`) ve uzlaştırmanın yarım yazma işaretleri (`V3_ENTITY_INDEX`) bunu çağırır.
    Varlığın v3 satırları ve geçmiş satırları dosyalardan yeniden yazılır; turnuvada sezon listesi, sezonda
    program sayfalarının listesi (iki düzen birlikte) de yeniden dizinlenir. Dizini kalmamış varlığın v3
    satırları silinir; listesi eski düzen dosyalarından kurulur.
    """
    root = os.fspath(data_dir)
    if kind not in layout.KINDS or kind == KIND_EVENT:
        raise ValueError(f"kind: expected a non-event entity kind, got {kind!r}")
    conn = cat.connection()
    conn.execute(f"DELETE FROM entity_slices WHERE kind = ? AND entity_id = ? AND layout = ? AND NOT ({_LISTING_SQL})",
                 (kind, entity_id, LAYOUT_V3))
    conn.execute("DELETE FROM slice_history WHERE kind = ? AND entity_id = ?", (kind, entity_id))
    reader = LegacyReader(root)
    if kind == KIND_SEASON:
        homes = v3_season_tournaments(root, entity_id)
        for tournament_id in homes:
            directory = layout.season_dir(tournament_id, entity_id)
            found = read_v3_manifest(root, directory, kind, entity_id, problems)
            if found is not None:
                _write_v3_entity(cat, root, V3Entity(kind, entity_id, directory, found, tournament_id), problems)
        if not homes:
            row = conn.execute("SELECT tournament_id FROM seasons WHERE id = ?", (entity_id,)).fetchone()
            homes = [int(row[0])] if row is not None and row[0] is not None else []
        if not homes:  # turnuvası bilinmeyen sezonun yalnızca program sayfası satırları kalmış olabilir
            conn.execute("DELETE FROM entity_slices WHERE kind = ? AND entity_id = ? AND key = ? AND layout = ?",
                         (KIND_SEASON, entity_id, KEY_SCHEDULE, LAYOUT_V3))
        for tournament_id in homes:
            index_season(cat, reader, tournament_id, entity_id, problems=problems)
        return
    directory = layout.entity_dir(kind, entity_id)
    found = read_v3_manifest(root, directory, kind, entity_id, problems)
    if found is not None:
        _write_v3_entity(cat, root, V3Entity(kind, entity_id, directory, found), problems)
    if kind == KIND_TOURNAMENT:
        apply_tournament_season_list(cat, reader, entity_id, problems=problems)


def clear_v3_listings(data_dir: Union[str, "os.PathLike[str]"], key: str) -> bool:
    """
    `Store.clear` için: v3 ağacındaki program sayfalarını (key `schedule`: her turnuvanın `seasons/` dizini) ya
    da sezon listelerini (key `seasons`: turnuva dizinindeki `seasons` dilimi, dosyası, geçmişi ve manifest
    kaydı) siler. Manifestinde dilim kalmayan turnuva dizini de silinir. Katalog sonradan yeniden kurulur;
    burada dokunulmaz. Bir şey silindiyse True.
    """
    root = os.fspath(data_dir)
    if key not in (KEY_SCHEDULE, KEY_SEASONS):
        raise ValueError(f"key: expected {KEY_SCHEDULE!r} or {KEY_SEASONS!r}, got {key!r}")
    removed = False
    for tournament_id in _numbered_dirs(root, layout.TOURNAMENTS_DIR):
        directory = layout.tournament_dir(tournament_id)
        if key == KEY_SCHEDULE:
            removed = files.remove_tree(layout.resolve(root, f"{directory}/{V3_SEASONS_DIR}")) or removed
            continue
        manifest_file = layout.resolve(root, layout.manifest_path(directory))
        found = read_v3_manifest(root, directory, KIND_TOURNAMENT, tournament_id)
        if found is None or KEY_SEASONS not in found.slices:
            continue
        del found.slices[KEY_SEASONS]
        if found.slices:
            manifest_mod.write_manifest(manifest_file, found)
        else:
            files.remove(manifest_file)
        files.remove(layout.resolve(root, layout.slice_path(directory, KEY_SEASONS)))
        files.remove_tree(layout.resolve(root, f"{directory}/{layout.HISTORY_DIR_NAME}/{KEY_SEASONS}"))
        removed = True
    for tournament_id in _numbered_dirs(root, layout.TOURNAMENTS_DIR):
        path = layout.resolve(root, layout.tournament_dir(tournament_id))
        for leftover in (os.path.join(path, layout.HISTORY_DIR_NAME), path):  # yalnızca boş kalan dizinler
            with contextlib.suppress(OSError):
                os.rmdir(leftover)
    return removed


# --- yazma: EntityStore.put ------------------------------------------------------------------------------

def schedule_sub(kind: str, number: Union[int, str], slug: Optional[str] = None) -> str:
    """
    Program sayfasının alt anahtarı: tur `round_<n>` ya da `round_<n>_<slug>`, olay sayfası `last_<n>` /
    `next_<n>`. Eski düzendeki dosya adının alt anahtarıyla aynıdır (sofascore_scraper/store/legacy.py `schedule_sub`):
    SofaScore'un slug'ı küçük harfe çevrilir (FX-4), alt anahtarda geçemeyen karakterler `-` olur.
    """
    if kind == "round":
        text = f"round_{number}" + (f"_{slug}" if slug else "")
    elif kind in ("last", "next"):
        text = f"{kind}_{number}"
    else:
        raise ValueError(f"kind: expected 'round', 'last' or 'next', got {kind!r}")
    return layout.validate_sub(re.sub(r"[^a-z0-9_.-]", "-", text.lower())[:80])


# --- okuma API'si -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TournamentRow:
    """`tournaments` tablosunun bir satırı (benzersiz turnuva). `sport` kısa addır; bilinmiyorsa None."""

    id: int
    sport: Optional[str]
    category_id: Optional[int]
    name: Optional[str]
    slug: Optional[str]
    updated_at: int


@dataclass(frozen=True)
class SeasonRow:
    """
    `seasons` tablosunun bir satırı. listed: sezon, turnuvanın sezon listesi yükünde geçiyor; position: o
    listedeki sırası (0 = ilk). Listede olmayan sezon bir program sayfasından ya da olay yükünden bilinir.
    """

    id: int
    tournament_id: int
    name: Optional[str]
    year: Optional[str]
    sort_key: float
    listed: bool
    position: Optional[int]
    updated_at: int


@dataclass(frozen=True)
class ParticipantRow:
    """`participants` tablosunun bir satırı: takım, tek oyuncu ya da çift (yarışmacı kimlik uzayı)."""

    id: int
    sport: Optional[str]
    name: Optional[str]
    short_name: Optional[str]
    slug: Optional[str]
    name_code: Optional[str]
    country: Optional[str]
    gender: Optional[str]
    type: Optional[int]
    national: Optional[bool]
    updated_at: int


@dataclass(frozen=True)
class CategoryRow:
    """
    `categories` tablosunun bir satırı: turnuvanın ülkesi ya da turu (SofaScore `category`). `sport` kısa addır;
    bilinmiyorsa None. alpha2: ülke kodu (varsa).
    """

    id: int
    sport: Optional[str]
    name: Optional[str]
    slug: Optional[str]
    alpha2: Optional[str]


@dataclass(frozen=True)
class SportRow:
    """`sports` tablosunun bir satırı. Birincil anahtar kısa addır (`slug`); SofaScore kimliği bilinmiyorsa None."""

    slug: str
    id: Optional[int]
    name: Optional[str]


KIND_EVENT = "event"
_CATEGORY_SELECT = "SELECT id, sport, name, slug, alpha2 FROM categories"
_SPORT_SELECT = "SELECT slug, id, name FROM sports"
_TOURNAMENT_SELECT = "SELECT id, sport, category_id, name, slug, updated_at FROM tournaments"
_SEASON_SELECT = "SELECT id, tournament_id, name, year, sort_key, listed, position, updated_at FROM seasons"
_PARTICIPANT_SELECT = ("SELECT id, sport, name, short_name, slug, name_code, country, gender, type, national, "
                       "updated_at FROM participants")
_NAME_LIKE = "name_folded LIKE ? ESCAPE '\\'"


def _tournament_row(row: Sequence[Any]) -> TournamentRow:
    return TournamentRow(row[0], row[1] or None, row[2], row[3], row[4], row[5])


def _season_row(row: Sequence[Any]) -> SeasonRow:
    return SeasonRow(row[0], row[1], row[2], row[3], float(row[4] or 0.0), bool(row[5]), row[6], row[7])


def _category_row(row: Sequence[Any]) -> CategoryRow:
    return CategoryRow(row[0], row[1] or None, row[2], row[3], row[4])


def _sport_row(row: Sequence[Any]) -> SportRow:
    return SportRow(str(row[0]), row[1], row[2])


def _participant_row(row: Sequence[Any]) -> ParticipantRow:
    return ParticipantRow(row[0], row[1] or None, row[2], row[3], row[4], row[5], row[6], row[7], row[8],
                          None if row[9] is None else bool(row[9]), row[10])


def _name_filter(sport: Optional[str], text: Optional[str]) -> Tuple[List[str], List[Any]]:
    """Turnuva ve yarışmacı listelerinin ortak süzgeçleri: spor ve adda geçen metin."""
    conditions: List[str] = []
    params: List[Any] = []
    if sport is not None:
        conditions.append("sport = ?")
        params.append(sport)
    pattern = like_pattern(text) if text is not None else None
    if pattern is not None:
        conditions.append(_NAME_LIKE)
        params.append(pattern)
    return conditions, params


@dataclass(frozen=True)
class _EntityFile:
    """Bir varlık diliminin yükünün durduğu yer (`entity_slices` satırından)."""

    layout: str
    path: str  # eski düzende yük dosyası; v3'te varlık dizini (ikisi de DATA_DIR'e göre)


class EntityStore:
    """
    Maç dışı varlıkların okuma API'si (`Store.entities`): turnuvalar, sezonlar, yarışmacılar ve bunların
    dilimleri (sezon listesi, program sayfaları, ...). Yazma yöntemi (`put`) sonraki adımlarda eklenir.

    Dilimlerin sahibi `Ref` ile verilir; `Ref.event(...)` verilirse çağrı `Store.events`'e gider.
    """

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

    # -- dilimler ve yükleri ---------------------------------------------------------------------------

    def payload(self, ref: Ref, key: str, sub: str = "", *, raw: bool = False) -> Any:
        """
        Varlığın bir diliminin yükü; katalogda o dilimin yükü yoksa None.

        Eski düzende sezon listesi `seasons/` altındaki dosyadır (yalnızca `league_seasons.csv`'de duran
        liste `{"seasons": [...]}` olarak kurulur); program sayfasının yükünde bizim eklediğimiz `_complete`
        anahtarı yoktur, o bilgi `SliceInfo.meta["complete"]`tedir. raw=True ayrıştırmadan bayt döndürür:
        dosyadaki baytlar; yük dosyadakinden farklıysa (CSV'den kurulan liste, `_complete`'i çıkarılmış
        sayfa) kurallı JSON baytları. Katalog yük var derken dosya yoksa PayloadMissing (bir kez yeniden
        denendikten sonra), dosya bozuksa PayloadCorrupt.
        """
        if ref.kind == KIND_EVENT:
            return self._store.events.payload(ref.id, key, sub, raw=raw)
        layout.validate_key(key)
        layout.validate_sub(sub)
        value = read_with_retry(lambda: self._locate(ref, key, sub),
                                lambda where: self._read_file(where, ref, key, sub, raw), ABSENT)
        return None if value is ABSENT else value

    def _locate(self, ref: Ref, key: str, sub: str) -> Optional[_EntityFile]:
        with self._read() as conn:
            row = conn.execute(
                "SELECT layout, path FROM entity_slices WHERE kind = ? AND entity_id = ? AND key = ? AND sub = ? "
                "AND has_payload = 1", (ref.kind, ref.id, key, sub)).fetchone()
        return _EntityFile(str(row[0]), str(row[1])) if row is not None else None

    def _read_file(self, where: _EntityFile, ref: Ref, key: str, sub: str, raw: bool) -> Any:
        if where.layout == LAYOUT_V3:
            path = layout.resolve(self._data_dir, layout.slice_path(where.path, key, sub))
            return codec.read_raw(path) if raw else codec.read_payload(path)
        derived: Any = ABSENT  # dosyadaki baytlardan farklı bir yük
        if where.path.endswith(".csv"):
            derived = self._csv_season_list(ref.id, where.path)
        elif key == KEY_SCHEDULE:
            kind = "round" if sub.startswith("round_") else "page"
            schedule = self._reader.read_schedule(
                LegacySchedulePage(ref.tournament_id or 0, ref.id, sub, kind, where.path, 0, 0))
            if not raw or "complete" in schedule.meta:  # `_complete` yükten çıkarıldı
                derived = schedule.payload
        if derived is not ABSENT:
            return codec.canonical_bytes(derived) if raw else derived
        full = self._reader.resolve(where.path)
        return codec.read_raw(full) if raw else codec.read_payload(full)

    def _csv_season_list(self, tournament_id: int, path: str) -> Mapping[str, Any]:
        for item in self._reader.season_lists():
            if item.kind == "csv" and item.path == path and item.tournament_id == tournament_id:
                return item.payload
        full = self._reader.resolve(path)
        raise PayloadMissing(f"Sezon listesi bulunamadı (turnuva {tournament_id}): {full}", path=full)

    def slices(self, ref: Ref) -> List[SliceInfo]:
        """Varlığın katalogdaki bütün dilim satırları, (anahtar, alt anahtar) sırasıyla (metin sırası)."""
        if ref.kind == KIND_EVENT:
            return self._store.events.slices(ref.id)
        with self._read() as conn:
            found = conn.execute(
                f"SELECT {SLICE_COLUMNS} FROM entity_slices WHERE kind = ? AND entity_id = ? ORDER BY key, sub",
                (ref.kind, ref.id)).fetchall()
        return [slice_info(ref, row) for row in found]

    def slice(self, ref: Ref, key: str, sub: str = "") -> SliceInfo:
        """Bir dilimin durumu; katalogda satırı yoksa durumu `not_requested` olan bir kayıt."""
        if ref.kind == KIND_EVENT:
            return self._store.events.slice(ref.id, key, sub)
        layout.validate_key(key)
        layout.validate_sub(sub)
        with self._read() as conn:
            row = conn.execute(
                f"SELECT {SLICE_COLUMNS} FROM entity_slices WHERE kind = ? AND entity_id = ? AND key = ? AND sub = ?",
                (ref.kind, ref.id, key, sub)).fetchone()
        return slice_info(ref, row) if row is not None else not_requested(ref, key, sub)

    # -- turnuvalar, sezonlar, yarışmacılar ------------------------------------------------------------

    def tournament(self, tournament_id: int) -> Optional[TournamentRow]:
        """Turnuvanın katalog satırı; bilinmiyorsa None."""
        check_int(tournament_id, "tournament_id")
        with self._read() as conn:
            row = conn.execute(f"{_TOURNAMENT_SELECT} WHERE id = ?", (tournament_id,)).fetchone()
        return _tournament_row(row) if row is not None else None

    def tournaments(self, *, sport: Optional[str] = None, text: Optional[str] = None,
                    limit: int = 100) -> List[TournamentRow]:
        """Turnuvalar, ada göre sıralı. text: adda geçen metin (büyük-küçük harf ve aksan ayrımı yok)."""
        conditions, params = _name_filter(sport, text)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(check_int(limit, "limit", minimum=1))
        with self._read() as conn:
            found = conn.execute(f"{_TOURNAMENT_SELECT}{where} ORDER BY name_folded, id LIMIT ?", params).fetchall()
        return [_tournament_row(row) for row in found]

    def tournaments_with_slice(self, key: str, *, layout_name: Optional[str] = None) -> List[int]:
        """
        Katalogda `key` diliminin yükü saklanan turnuvaların kimlikleri, artan sırayla (ör. `seasons`: sezon
        listesi olan turnuvalar; turnuvanın maçı, takibi ya da satırı olmasa da). layout_name: yalnızca bu
        düzende duran yükler ("v3" ya da "legacy"); None ise ikisi de.
        """
        layout.validate_key(key)
        sql = "SELECT DISTINCT entity_id FROM entity_slices WHERE kind = ? AND key = ? AND has_payload = 1"
        params: List[Any] = [KIND_TOURNAMENT, key]
        if layout_name is not None:
            sql += " AND layout = ?"
            params.append(str(layout_name))
        with self._read() as conn:
            found = conn.execute(sql + " ORDER BY entity_id", params).fetchall()
        return [int(row[0]) for row in found]

    def seasons(self, tournament_id: int) -> List[SeasonRow]:
        """
        Turnuvanın katalogdaki bütün sezonları, en yeni önce (`sort_key`, eşitlikte kimlik). Sezon listesinde
        geçenler `listed` ile işaretlidir; listenin kendi sırası `position`dadır.
        """
        check_int(tournament_id, "tournament_id")
        with self._read() as conn:
            found = conn.execute(
                f"{_SEASON_SELECT} WHERE tournament_id = ? ORDER BY sort_key DESC, id DESC",
                (tournament_id,)).fetchall()
        return [_season_row(row) for row in found]

    def season(self, season_id: int) -> Optional[SeasonRow]:
        """Sezonun katalog satırı; bilinmiyorsa None."""
        check_int(season_id, "season_id")
        with self._read() as conn:
            row = conn.execute(f"{_SEASON_SELECT} WHERE id = ?", (season_id,)).fetchone()
        return _season_row(row) if row is not None else None

    def participants(self, *, text: Optional[str] = None, sport: Optional[str] = None, ids: Sequence[int] = (),
                     limit: int = 50) -> List[ParticipantRow]:
        """Yarışmacılar, ada göre sıralı. Süzgeçler birlikte uygulanır; text adda geçen metindir."""
        conditions, params = _name_filter(sport, text)
        listed = int_list(ids, "ids")
        if listed:
            conditions.append(f"id IN ({listed})")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(check_int(limit, "limit", minimum=1))
        with self._read() as conn:
            found = conn.execute(f"{_PARTICIPANT_SELECT}{where} ORDER BY name_folded, id LIMIT ?", params).fetchall()
        return [_participant_row(row) for row in found]

    def sport_of_tournament(self, tournament_id: int) -> Optional[str]:
        """
        Turnuvanın sporu (kısa ad, katalogda yazıldığı gibi): turnuva satırındaki; orada yoksa turnuvanın
        maçlarında en çok geçen spor. Hiçbiri bilinmiyorsa None.
        """
        check_int(tournament_id, "tournament_id")
        with self._read() as conn:
            row = conn.execute("SELECT sport FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
            if row is not None and row[0]:
                return str(row[0])
            row = conn.execute(
                "SELECT sport FROM events WHERE tournament_id = ? AND sport != '' "
                "GROUP BY sport ORDER BY count(*) DESC, sport LIMIT 1", (tournament_id,)).fetchone()
        return str(row[0]) if row is not None else None


    # -- kategoriler ve sporlar -------------------------------------------------------------------------

    def category(self, category_id: int) -> Optional[CategoryRow]:
        """Kategorinin (ülke / tur) katalog satırı; bilinmiyorsa None."""
        check_int(category_id, "category_id")
        with self._read() as conn:
            row = conn.execute(f"{_CATEGORY_SELECT} WHERE id = ?", (category_id,)).fetchone()
        return _category_row(row) if row is not None else None

    def categories(self, *, sport: Optional[str] = None, ids: Sequence[int] = (),
                   limit: int = 100) -> List[CategoryRow]:
        """Kategoriler, ada göre sıralı (eşitlikte kimlik). sport: kısa ad; ids: yalnızca bu kimlikler."""
        conditions: List[str] = []
        params: List[Any] = []
        if sport is not None:
            conditions.append("sport = ?")
            params.append(sport)
        listed = int_list(ids, "ids")
        if listed:
            conditions.append(f"id IN ({listed})")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(check_int(limit, "limit", minimum=1))
        with self._read() as conn:
            found = conn.execute(f"{_CATEGORY_SELECT}{where} ORDER BY name, id LIMIT ?", params).fetchall()
        return [_category_row(row) for row in found]

    def sport(self, slug: str) -> Optional[SportRow]:
        """Sporun katalog satırı, kısa adıyla (`football`); bilinmiyorsa None."""
        if not isinstance(slug, str):
            raise ValueError(f"slug: expected a string, got {slug!r}")
        with self._read() as conn:
            row = conn.execute(f"{_SPORT_SELECT} WHERE slug = ?", (slug,)).fetchone()
        return _sport_row(row) if row is not None else None

    def sports(self) -> List[SportRow]:
        """Katalogdaki bütün sporlar, kısa ada göre sıralı."""
        with self._read() as conn:
            found = conn.execute(f"{_SPORT_SELECT} ORDER BY slug").fetchall()
        return [_sport_row(row) for row in found]

    # -- yazma -------------------------------------------------------------------------------------------

    def put(self, ref: Ref, outcomes: Mapping[SliceKey, Outcome], *,
            count_empties: Union[bool, Collection[str]] = True,
            keep_history: Collection[str] = ()) -> PutResult:
        """
        Maç dışı bir varlığın dilim sonuçlarını v3 düzenine yazar (bölüm 2.3 ve 4.2): turnuva
        `v3/tournaments/<ut>/`, sezon `v3/tournaments/<ut>/seasons/<sid>/`, takım, oyuncu ve spor kendi
        dizinlerinde. Kurallar `EventStore.put` ile aynıdır (`ok`, iki `empty` biçimi, `failed`, `skipped`,
        `count_empties`, `keep_history`, `Outcome.meta`); olay yükü ve gözlem yoktur.

        Sezon için `Ref.season(tournament_id, season_id)` gerekir. Bir sezon kimliği tek bir turnuvanın altında
        durur: başka bir turnuvanın altında v3 dizini olan sezona yazmak ValueError verir.

        Katalog yazmayla aynı kritik bölümde güncellenir ve sıfırdan kurulmuş haline eşit kalır. Turnuvanın
        `seasons` dilimi sezon listesidir (`seasons` satırları); sezonun `schedule/<alt anahtar>` dilimleri
        program sayfalarıdır: yükün `events` dizisindeki her maç bir liste satırı alır ya da (olay yükü
        varsa) sayfaya bağlanır (bölüm 8.2). Aynı alt anahtarın eski düzen dosyası bundan sonra okunmaz ama
        silinmez. Yazma kilidi `busy_timeout` içinde alınamazsa StoreBusy; açık bir `Catalog.write()` bloğunun
        içinden çağrılmamalıdır.
        """
        if not isinstance(ref, Ref):
            raise ValueError(f"ref: expected a Ref, got {type(ref).__name__}")
        if ref.kind == KIND_EVENT:
            raise ValueError("ref: events are written with Store.events.put")
        if ref.kind == KIND_SEASON and ref.tournament_id is None:
            raise ValueError("ref: a season is written with Ref.season(tournament_id, season_id)")
        items = _outcome_items(ref.id, outcomes, count_empties)
        keep = _history_keys(keep_history)
        self._writable()
        if not items:
            return _NOTHING_WRITTEN
        return self._locked(ref, lambda write: self._apply(write, ref, items, keep))

    def _writable(self) -> None:
        self._store._require_open()
        if self._store.readonly:
            raise StoreError(f"Depo salt okunur açılmış: {self._data_dir}", path=self._data_dir)

    def _checkpoint(self, step: str) -> None:
        """Protokolün adımları arasında çağrılır (`STEP_*`). Hiçbir şey yapmaz; testler süreci burada öldürür."""

    def _locked(self, ref: Ref, body: Callable[[_Write], T]) -> T:
        """
        Yazmanın çerçevesi (bölüm 6.2; `EventStore._entity_write` ile aynı): yarım yazma işareti (kendi
        işleminde, türü `ref.kind`), yazma kilidi, `body`, işaretin silinmesi, commit. Diske dokunulduktan sonra
        hata olursa işaret kalır; açılıştaki uzlaştırma varlığı dosyalarından yeniden dizinler.
        """
        assert self._catalog is not None
        cat = self._catalog
        for _ in range(_MARKER_ATTEMPTS):
            with cat.write() as conn:
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO pending_writes (kind, entity_id, started_at) VALUES (?, ?, ?)",
                    (ref.kind, ref.id, int(time.time()))).rowcount
            self._checkpoint(STEP_MARKER)
            write = _Write(recover=not inserted)
            try:
                with cat.write() as conn:
                    if conn.execute("SELECT 1 FROM pending_writes WHERE kind = ? AND entity_id = ?",
                                    (ref.kind, ref.id)).fetchone() is None:
                        continue
                    self._checkpoint(STEP_LOCKED)
                    result = body(write)
                    conn.execute("DELETE FROM pending_writes WHERE kind = ? AND entity_id = ?", (ref.kind, ref.id))
                    self._checkpoint(STEP_COMMIT)
            except BaseException:
                if inserted and not write.touched:
                    with contextlib.suppress(StoreError, sqlite3.Error):
                        with cat.write() as conn:
                            conn.execute("DELETE FROM pending_writes WHERE kind = ? AND entity_id = ?",
                                         (ref.kind, ref.id))
                raise
            self._checkpoint(STEP_DONE)
            return result
        raise StoreBusy(f"{ref.kind} {ref.id} için yarım yazma işareti korunamadı: veri dizini başka süreçlerce "
                        f"sürekli uzlaştırılıyor: {self._data_dir}", path=self._data_dir)

    def _open_manifest(self, ref: Ref, directory: str, now: datetime) -> Tuple[Manifest, bool]:
        """Varlığın manifesti (kilit altında) ve "yeni mi"; okunamayan manifest baştan kurulur (uyarı)."""
        if ref.kind == KIND_SEASON:
            others = [t for t in v3_season_tournaments(self._data_dir, ref.id) if t != ref.tournament_id]
            if others:
                raise ValueError(f"ref: season {ref.id} is stored under tournament {others[0]}, "
                                 f"not {ref.tournament_id}")
        manifest_file = layout.resolve(self._data_dir, layout.manifest_path(directory))
        try:
            found = manifest_mod.read_manifest(manifest_file)
        except PayloadMissing:
            found = None
        except PayloadCorrupt as exc:
            logger.warning(f"{ref.kind} {ref.id}: the manifest cannot be read and is written anew "
                           f"({exc.detail or exc})")
            found = None
        if found is None:
            return Manifest(kind=ref.kind, id=ref.id, created_at=now, updated_at=now), True
        if found.kind != ref.kind or found.id != ref.id:
            raise StoreError(f"Manifest bu varlığın dizinine ait değil ({found.kind} {found.id!r}): {manifest_file}",
                             path=manifest_file)
        return found, False

    def _apply(self, write: _Write, ref: Ref, items: Sequence[_Item], keep: Collection[str]) -> PutResult:
        """`put`'un gövdesi; yazma kilidi altında çalışır. Önce manifest bellekte kurulur, sonra diske yazılır."""
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        directory = layout.entity_dir(ref.kind, ref.id, ref.tournament_id)
        root = layout.resolve(self._data_dir, directory)
        base, created = self._open_manifest(ref, directory, now)
        found = copy.deepcopy(base)

        payloads: List[Tuple[_Item, codec.Encoded]] = []
        snapshots: List[Tuple[_Item, codec.Encoded, datetime]] = []
        dirty = created
        for item in items:
            outcome = item.outcome
            at = _aware(outcome.fetched_at, now, f"outcomes[{item.name!r}].fetched_at")
            entry = found.slices.get(item.name)
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
                if previous != encoded.sha256 or not os.path.isfile(_slice_file(root, item.key, item.sub)):
                    payloads.append((item, encoded))
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
                mark_empty = entry.empty if entry.empty is not None else EmptyMark()
                mark_empty.count += 1
                mark_empty.reason = outcome.reason or ("empty" if outcome.data is not None else "404")
                mark_empty.at = at
                entry.empty = mark_empty
            found.slices[item.name] = entry
        if dirty:
            found.updated_at = max(found.updated_at, now)

        # --- disk: yük dosyaları, geçmiş üyeleri, en son manifest (bölüm 4.4) ---
        written: List[str] = []
        kept: List[str] = []
        for item, encoded in payloads:
            write.touched = True
            files.write_bytes(_slice_file(root, item.key, item.sub), encoded.stored)
            written.append(item.name)
            self._checkpoint(f"{STEP_PAYLOAD}:{item.name}")
        for item, encoded, moment in snapshots:
            write.touched = True
            path = layout.resolve(self._data_dir, layout.history_path(directory, item.key, item.sub))
            entry = found.slices[item.name]
            n, _offset, _length = history.append(
                path, history.encode_member(encoded.raw, encoded.sha256, moment),
                known=(0, 0) if created else self._history_end(ref, item, entry.history))
            entry.history = manifest_mod.HistoryMark(
                count=n, last_sha256=encoded.sha256,
                extra=dict(entry.history.extra) if entry.history is not None else {})
            kept.append(item.name)
            self._checkpoint(f"{STEP_HISTORY}:{item.name}")
        if dirty:
            write.touched = True
            manifest_mod.write_manifest(layout.resolve(self._data_dir, layout.manifest_path(directory)), found)
            self._checkpoint(STEP_MANIFEST)

        assert self._catalog is not None
        problems: List[LegacyProblem] = []
        index_v3_entity(self._catalog, self._data_dir, ref.kind, ref.id, problems=problems)
        for problem in problems:
            logger.debug(f"{ref.kind} {ref.id} indexed with a problem: {problem.kind} {problem.path} {problem.detail}")
        self._checkpoint(STEP_INDEXED)
        return PutResult(created=created, event_written=False, superseded=False, written=tuple(written),
                         change_seq=None, promoted=False, history=tuple(kept))

    def _history_end(self, ref: Ref, item: _Item,
                     mark: Optional[manifest_mod.HistoryMark]) -> Optional[Tuple[int, int]]:
        """Geçmiş dosyasının katalogdaki son hali (üye sayısı, bittiği bayt); manifestle uyuşmuyorsa None."""
        if mark is None:
            return None
        assert self._catalog is not None
        row = self._catalog.connection().execute(
            "SELECT n, offset + length FROM slice_history WHERE kind = ? AND entity_id = ? AND key = ? AND sub = ? "
            "ORDER BY n DESC LIMIT 1", (ref.kind, ref.id, item.key, item.sub)).fetchone()
        if row is None or int(row[0]) != mark.count:
            return None
        return int(row[0]), int(row[1])

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
    "TournamentRow",
    "SeasonRow",
    "ParticipantRow",
    "CategoryRow",
    "SportRow",
    "EntityStore",
    "V3_SEASONS_DIR",
    "LISTING_SLICES",
    "V3Entity",
    "V3SchedulePage",
    "V3SeasonList",
    "read_v3_manifest",
    "v3_entities",
    "v3_season_tournaments",
    "v3_season_pages",
    "v3_schedule_seasons",
    "v3_season_lists",
    "v3_slice_row",
    "apply_tournament_season_list",
    "apply_v3_entities",
    "index_season",
    "index_v3_entity",
    "schedule_sub",
]
