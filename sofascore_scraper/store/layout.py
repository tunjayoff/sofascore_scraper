"""
v3 düzeninin yol kuralları (docs/design/01-storage.md, bölüm 4.2). Saf işlevler: diske dokunmaz.

Bütün yollar DATA_DIR'e göre görelidir ve ayırıcı olarak "/" kullanır; katalog da yolları bu biçimde
saklar, böylece bir veri dizini işletim sistemleri arasında taşınabilir. Gerçek dosya yolu için
`resolve(data_dir, rel)` kullanılır.

Bir varlığın yolu yalnızca kimliğine bağlıdır: lig ya da sezon adı değişince hiçbir şey taşınmaz.
Bütün adlar ASCII rakam ve sabit sözcüklerden oluşur, bu yüzden Windows'ta da geçerlidir. Dilim anahtarı
ve alt anahtarında büyük harf yoktur: büyük/küçük harf ayırmayan dosya sistemlerinde (Windows, varsayılan
macOS) iki ad aynı dosyaya düşmez.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple, Union

from sofascore_scraper.store.errors import LayoutError

META_DIR = ".meta"
SCHEMA_FILE = ".meta/schema.json"
CATALOG_DB = ".meta/catalog.db"
STATE_DB = ".meta/state.db"
LEGACY_JOBS_DB = ".meta/jobs.db"  # 2.x iş geçmişi: bir kez okunur, 3.x yazmaz
LOCKS_DIR = ".meta/locks"
UNCLEAN_MARKER = ".meta/locks/unclean"
TMP_DIR = ".meta/tmp"  # hazırlık alanı: yükseltme, taşıma, yeniden kurma, dışa aktarma (aynı dosya sistemi)
TRASH_DIR = ".meta/trash"  # silinmek üzere olan eski dizinler

V3_DIR = "v3"
EVENTS_DIR = "v3/events"
TOURNAMENTS_DIR = "v3/tournaments"
TEAMS_DIR = "v3/teams"
PLAYERS_DIR = "v3/players"
SPORTS_DIR = "v3/sports"

CHANGES_DIR = "changes"
EXPORTS_DIR = "exports"
BACKUPS_DIR = "backups"

MANIFEST_NAME = "manifest.json"
HISTORY_DIR_NAME = "_history"
EXTRA_DIR_NAME = "_extra"  # taşınan eski maç dizininin tanınmayan dosyaları, olduğu gibi (migrate, bölüm 5.4)
PAYLOAD_SUFFIX = ".json.gz"  # 3.0 yalnızca gzip yazar; okuyucu sonekten seçer (sofascore_scraper/store/codec.py)
HISTORY_SUFFIX = ".jsonl.gz"

KINDS: Tuple[str, ...] = ("event", "tournament", "season", "team", "player", "sport")

_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
# Yalnızca küçük harf (karar S13): Windows ve varsayılan macOS dosya sistemleri büyük/küçük harf ayırmaz,
# "A" ile "a" alt anahtarları orada aynı dosyaya düşerdi
_SUB_RE = re.compile(r"[a-z0-9_.-]{0,80}")
_LEASE_RE = re.compile(r"([a-z][a-z0-9_]{0,39})(?::([a-z0-9][a-z0-9_-]{0,39}))?")
# Windows'ta uzantısı olsa bile dosya adı olamayan aygıt adları ("nul.json.gz" de geçersizdir)
_WINDOWS_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"] + [f"com{n}" for n in range(1, 10)] + [f"lpt{n}" for n in range(1, 10)]
)
# Geçmiş dosyasında alt anahtarı olmayan dilimin adı (_history/<key>/_.jsonl.gz); alt anahtar olarak kullanılamaz
_NO_SUB = "_"


def resolve(data_dir: Union[str, "os.PathLike[str]"], rel: str) -> str:
    """Göreli (her zaman "/" ayırıcılı) düzen yolunu `data_dir` altındaki gerçek yola çevirir."""
    return os.path.join(os.fspath(data_dir), *rel.split("/"))


def _reserved(name: str) -> bool:
    return name.split(".", 1)[0].lower() in _WINDOWS_RESERVED


def validate_key(key: str) -> str:
    """Dilim anahtarı: [a-z][a-z0-9_]{0,39}. Geçersizse LayoutError."""
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key) or _reserved(key):
        raise LayoutError(f"Invalid slice key: {key!r}")
    return key


def validate_sub(sub: str) -> str:
    """
    Alt anahtar: [a-z0-9_.-]{0,80} (boş = alt anahtar yok). Geçersizse LayoutError.

    Büyük harf kabul edilmez ve küçültülmez (karar S13): çağıran alt anahtarı küçük harfle üretir
    (`round_12`, `last_0`, `total`, sağlayıcı kimliği, `<ut>-<sid>`); sessizce küçültmek, yalnızca
    harf büyüklüğüyle ayrılan iki alt anahtarı tek dosyada birleştirirdi.
    """
    if not isinstance(sub, str) or not _SUB_RE.fullmatch(sub) or sub == _NO_SUB or (sub and _reserved(sub)):
        raise LayoutError(f"Invalid slice sub-key: {sub!r}")
    return sub


def _check_id(value: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LayoutError(f"Invalid {what} id: {value!r}")
    return value


# --- varlık dizinleri ---------------------------------------------------------------------------

def event_dir(event_id: int) -> str:
    """v3/events/<id // 1000000>/<(id // 1000) % 1000, 3 hane>/<id>: bir dizinde en çok 1000 maç dizini olur."""
    eid = _check_id(event_id, "maç")
    return f"{EVENTS_DIR}/{eid // 1_000_000}/{(eid // 1000) % 1000:03d}/{eid}"


def tournament_dir(tournament_id: int) -> str:
    return f"{TOURNAMENTS_DIR}/{_check_id(tournament_id, 'turnuva')}"


def season_dir(tournament_id: int, season_id: int) -> str:
    """Sezon, turnuvasının dizini altında durur."""
    return f"{tournament_dir(tournament_id)}/seasons/{_check_id(season_id, 'sezon')}"


def team_dir(team_id: int) -> str:
    tid = _check_id(team_id, "takım")
    return f"{TEAMS_DIR}/{tid // 1000}/{tid}"


def player_dir(player_id: int) -> str:
    pid = _check_id(player_id, "oyuncu")
    return f"{PLAYERS_DIR}/{pid // 1000}/{pid}"


def sport_dir(sport_id: int) -> str:
    return f"{SPORTS_DIR}/{_check_id(sport_id, 'spor')}"


def entity_dir(kind: str, entity_id: int, tournament_id: Optional[int] = None) -> str:
    """Tür adına göre varlık dizini. `season` için tournament_id zorunludur."""
    if kind == "event":
        return event_dir(entity_id)
    if kind == "tournament":
        return tournament_dir(entity_id)
    if kind == "season":
        if tournament_id is None:
            raise LayoutError(f"A season directory needs a tournament id (season {entity_id!r})")
        return season_dir(tournament_id, entity_id)
    if kind == "team":
        return team_dir(entity_id)
    if kind == "player":
        return player_dir(entity_id)
    if kind == "sport":
        return sport_dir(entity_id)
    raise LayoutError(f"Unknown entity kind: {kind!r}")


def event_id_from_dir(rel: str) -> Optional[int]:
    """`event_dir`in tersi: yol bir maç dizininin kurallı yolu ise kimliği, değilse None."""
    name = rel.rstrip("/").rsplit("/", 1)[-1]
    if not name.isascii() or not name.isdigit():
        return None
    event_id = int(name)
    return event_id if event_dir(event_id) == rel.rstrip("/") else None


# --- varlık dizininin içi -----------------------------------------------------------------------

def manifest_path(directory: str) -> str:
    return f"{directory}/{MANIFEST_NAME}"


def slice_name(key: str, sub: str = "") -> str:
    """Dilimin manifestteki adı: "statistics" ya da "odds_all/1"."""
    validate_key(key)
    return f"{key}/{sub}" if validate_sub(sub) else key


def split_slice_name(name: str) -> Tuple[str, str]:
    """`slice_name`in tersi: "odds_all/1" → ("odds_all", "1"). Geçersiz adda LayoutError."""
    if not isinstance(name, str):
        raise LayoutError(f"Invalid slice name: {name!r}")
    key, slash, sub = name.partition("/")
    if slash and not sub:
        raise LayoutError(f"Invalid slice name: {name!r}")
    return validate_key(key), validate_sub(sub)


def slice_path(directory: str, key: str, sub: str = "") -> str:
    """<dizin>/<key>.json.gz ya da <dizin>/<key>/<sub>.json.gz"""
    return f"{directory}/{slice_name(key, sub)}{PAYLOAD_SUFFIX}"


def history_path(directory: str, key: str, sub: str = "") -> str:
    """<dizin>/_history/<key>/<sub ya da "_">.jsonl.gz"""
    validate_key(key)
    return f"{directory}/{HISTORY_DIR_NAME}/{key}/{validate_sub(sub) or _NO_SUB}{HISTORY_SUFFIX}"


# --- dizin düzeyindeki diğer dosyalar -----------------------------------------------------------

def lock_path(name: str) -> str:
    """Kilit dosyası: "writer" → .meta/locks/writer.lock, "watcher:tennis" → .meta/locks/watcher-tennis.lock"""
    match = _LEASE_RE.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        raise LayoutError(f"Invalid lock name: {name!r}")
    base, qualifier = match.groups()
    return f"{LOCKS_DIR}/{base}-{qualifier}.lock" if qualifier else f"{LOCKS_DIR}/{base}.lock"


def change_segment(year: int, month: int) -> str:
    """Değişiklik günlüğünün aylık parçası: changes/<yyyy>-<aa>.jsonl (satırın ts_utc ayı)."""
    if not (isinstance(year, int) and isinstance(month, int) and 1 <= year <= 9999 and 1 <= month <= 12):
        raise LayoutError(f"Invalid change log month: {year!r}-{month!r}")
    return f"{CHANGES_DIR}/{year:04d}-{month:02d}.jsonl"
