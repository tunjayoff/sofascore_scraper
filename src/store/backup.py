"""
Veri yedeği, doğrulaması, geri yüklemesi ve eskilerin silinmesi (docs/design/01-storage.md bölüm 9; plan
maddeleri ST-19 ve ST-24).

Biçim 2 (ST-24). Dosya `backups/backup_<kapsam>[_with_env]_<yyyymmdd>_<hhmmss>.zip` (yerel saat); `.env`
pakete giriyorsa adı bunu söyler ve dosya yalnızca sahibince okunur (0600). `overrides.json` (web arayüzünde
kaydedilen ayarlar; proxy adresi parola taşıyabilir) pakete giriyorsa dosya yine 0600'dür, adı değişmez.
Üye adları veri dizinine göredir ("/" ayırıcılı):

    üye                                        all  state  data  config  seasons  matches  match_details
    backup.json (biçim, sürümler, sayımlar)    +    +      +     +       +        +        +
    .meta/schema.json                          +    +      +             +        +        +
    .meta/state.db (SQLite yedekleme API'si)   +    +
    v3/**                                      +           +             (1)      (2)      v3/events/**
    changes/**, score_changes.jsonl            +           +
    seasons/, matches/, match_details/         +           +             seasons/ matches/ match_details/
    config/<ad> (verilen ayar dosyaları)       +    +            +
    config/overrides.json (verildiyse)         +    +            +
    config/.env (yalnızca istenirse)           +    +            +

    (1) v3/tournaments altında turnuvaların `seasons/` dışında kalanı (sezon listeleri)
    (2) v3/tournaments/*/seasons/** (program sayfaları)

`state` ve `data` tasarımın kapsamlarıdır; `config`, `seasons`, `matches` ve `match_details` bugünkü web
API'sinin kapsamlarıdır ve aynı adlarla kalır. state.db, WAL kipinde olduğu için dosya kopyalanarak değil
SQLite'ın çevrimiçi yedekleme API'siyle `.meta/tmp` altına alınır; kopyadaki kilit sahibi satırları
(`leases`) silinir, çünkü onları tutan süreçler geri yüklemede yoktur. Sonu `.gz` olan üyeler (yükler,
geçmiş dosyaları) zaten sıkıştırılmıştır ve yeniden sıkıştırılmadan (`ZIP_STORED`) saklanır, ötekiler
sıkıştırılır. `exports/`, `backups/`, `.meta` altındaki öteki her şey (catalog.db, kilitler, hazırlık ve çöp
dizinleri, `pending_changes/`, `state.db.bak-v*`) ve yarım kalmış geçici dosyalar pakete girmez. Katalog
pakete girmez: geri yüklemeden sonra dosyalardan yeniden kurulur.

Biçim 1, 2.x'in ve ST-19'un zip'idir: `backup.json` yoktur, veri ağaçları `<veri dizininin adı>/seasons/...`
biçiminde, ayar dosyaları ve `.env` zip'in kökündedir. Biçim 1 geri yüklenebilir: veri ağaçları eski düzen
ağaçları olarak yerine konur (Store onları yerinde okur).

Geri yükleme yalnızca `backups/` dizinindeki bir yedeği adıyla alır (web arayüzü kararı 15: yükleme yok).
Ayar dosyaları ve `.env` geri yüklenmez: veri dizininin dışındadırlar ve çalışan kurulumun ayarlarını
değiştirmek geri yüklemenin işi değildir; rapor onları `skipped` olarak adlandırır. Tek istisna
`config/overrides.json`: web arayüzünde kaydedilen ayarlar yoksa geri yüklemeden sonra kaybolurdu (FX-22).
Çağıran onun yerini verirse (`restore(overrides_file=...)`) veriyle aynı adımda, 0600 izniyle yerine konur
ve bir adım başarısız olursa önceki hali geri gelir; vermezse ya da yedekte yoksa (eski yedekler) dosyaya
dokunulmaz. İçeriği hiçbir zaman günlüğe yazılmaz.

Kilitler: `create` kilit almaz, veri içeren bir yedek için `writer` kilidini (amaç `op:backup`) çağıran tutar
(web'in iş deposu, `JobStore.exclusive("backup")`, ya da `ssc backup create`). `restore` `maintenance`
kilidini (amaç `op:restore`) bu süreç tutmuyorsa kendisi alır. `verify`, `list` ve `prune` kilit almaz.
Yarım kalan zip silinir; dosya sistemi hatası StoreError olarak çıkar.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from src.store import files, layout
from src.store.errors import SchemaTooNew, StoreError

if TYPE_CHECKING:
    from src.store.api import Store
    from src.store.lease import Lease

logger = logging.getLogger("Store")

FORMAT = 2  # bu kodun yazdığı yedek biçimi
LEGACY_FORMAT = 1  # `backup.json` taşımayan zip: 2.x ve ST-19
MANIFEST_MEMBER = "backup.json"

# Kapsamlar: tasarımın üçü (`all`, `state`, `data`) ve bugünkü web API'sinin dördü
BACKUP_SCOPES: Tuple[str, ...] = ("all", "state", "data", "config", "seasons", "matches", "match_details")
CONFIG_SCOPES: Tuple[str, ...] = ("all", "state", "config")  # verilen ayar dosyaları (ve istenirse .env) girer
STATE_SCOPES: Tuple[str, ...] = ("all", "state")  # .meta/state.db girer
LEGACY_TREES: Tuple[str, ...] = ("seasons", "matches", "match_details")  # zip'e giriş sırası
LEGACY_CHANGES = "score_changes.jsonl"
# Veri dizininin geri yüklemenin yerine koyduğu (ve `force` ile çöpe taşıdığı) üst düzey girdileri
DATA_ENTRIES: Tuple[str, ...] = (layout.V3_DIR, layout.CHANGES_DIR, *LEGACY_TREES, LEGACY_CHANGES)
CONFIG_MEMBER_DIR = "config"
ENV_MEMBER = ".env"
OVERRIDES_MEMBER = f"{CONFIG_MEMBER_DIR}/overrides.json"  # web arayüzünde kaydedilen ayarlar (geri yüklenir)
_OVERRIDES_MAX_BYTES = 1024 * 1024  # ayar belgesi küçüktür; daha büyüğü açılmaz
STATE_MEMBER = layout.STATE_DB  # ".meta/state.db"
SCHEMA_MEMBER = layout.SCHEMA_FILE  # ".meta/schema.json"
CATALOG_MEMBER = layout.CATALOG_DB  # biçim 2'de yazılmaz; başka bir araçla eklenmişse yok sayılır
WITH_ENV_TAG = "_with_env"
RESTORE_PURPOSE = "op:restore"
_TIME_FORMAT = "%Y%m%d_%H%M%S"
_NAME_RE = re.compile(
    r"backup_(all|state|data|config|seasons|matches|match_details)(_with_env)?_(\d{8}_\d{6})\.zip")
_PRIVATE_MODE = 0o600
_COPY_CHUNK = 1024 * 1024
_STORED_SUFFIX = ".gz"  # zaten sıkıştırılmış üyeler
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class BackupNotFound(StoreError):
    """`backups/` altında bu adla bir yedek yok (ya da ad bir yedek adı değil)."""

    default_message = "No such backup"


class BackupInvalid(StoreError):
    """Yedek okunamıyor ya da geri yüklenemez: zip değil, `backup.json` bozuk, üye yolu güvensiz ya da tanınmıyor."""

    default_message = "The backup cannot be restored"


class RestoreRefused(StoreError):
    """
    Hedef dizin boş değil ve `force` verilmedi (bölüm 9.2: geri yükleme birleştirmez). `reasons` hedefte
    bulunanları adlandırır: "v3", "match_details", "follows", ...
    """

    default_message = "The data directory is not empty"

    def __init__(self, message: Optional[str] = None, path: Optional[str] = None,
                 errno_code: Optional[int] = None, detail: str = "", *, reasons: Sequence[str] = ()) -> None:
        super().__init__(message, path=path, errno_code=errno_code, detail=detail)
        self.reasons: Tuple[str, ...] = tuple(reasons)


@dataclass(frozen=True)
class BackupInfo:
    """
    Bir yedek dosyası.

    name        dosya adı (`backups/` içinde)
    path        tam yol
    scope       kapsam (BACKUP_SCOPES)
    with_env    pakette `.env` var (gizli değer taşıyabilir)
    created_at  addaki zaman damgası, ISO biçiminde (yerel saat, saat dilimi yok)
    size        bayt
    format      2 (`backup.json` var), 1 (2.x ya da ST-19 zip'i) ya da None (zip okunamadı)
    """

    name: str
    path: str
    scope: str
    with_env: bool
    created_at: str
    size: int
    format: Optional[int] = None


@dataclass(frozen=True)
class BackupCheck:
    """
    `BackupManager.verify` sonucu.

    ok        sorun yok: zip okunuyor, her üyenin CRC'si tutuyor, yollar güvenli, sürümler bu koddan yeni
              değil, state.db (varsa) okunuyor ve bütün
    format    2, 1 ya da None (zip ya da `backup.json` okunamadı)
    manifest  `backup.json`'ın içeriği (biçim 2); biçim 1'de boş
    members   üye sayısı (dizin girdileri hariç); bytes: açılmış toplam boyut
    problems  bulunan sorunlar, okunur metin olarak
    """

    name: str
    ok: bool
    format: Optional[int]
    scope: Optional[str]
    manifest: Mapping[str, Any] = field(default_factory=dict)
    members: int = 0
    bytes: int = 0
    problems: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreReport:
    """
    `BackupManager.restore` sonucu (deneme çalıştırmasında: olacak olan).

    restored         yerine konan üst düzey girdiler ("v3", "match_details", ".meta/state.db", ...) ve
                     yerine konan ayar belgesi ("config/overrides.json")
    replaced         hedefte bulunup çöpe taşınan girdiler (geri yükleme bitince silinir); üzerine yazılan
                     ayar belgesi de ("config/overrides.json") burada adlandırılır
    skipped          geri yüklenmeyen üyeler: ayar dosyaları, `.env`, katalog; yeri verilmeyen ya da
                     okunamayan `config/overrides.json`
    occupied         hedefte bulunan ve `force` gerektiren şeyler; boşsa hedef boştu
    counts           yedeğin sayımları (`backup.json`; biçim 1'de üyelerden sayılır)
    catalog_rebuilt  katalog geri yüklenen dosyalardan yeniden kuruldu
    verify_ok        geri yüklemeden sonraki tutarlılık denetimi (bölüm 3.6) sorunsuz; deneme çalıştırmasında None
    verify_issues    o denetimin giderilmemiş sorun sayısı
    """

    name: str
    format: int
    scope: Optional[str]
    dry_run: bool
    force: bool
    restored: Tuple[str, ...] = ()
    replaced: Tuple[str, ...] = ()
    skipped: Tuple[str, ...] = ()
    occupied: Tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    catalog_rebuilt: bool = False
    verify_ok: Optional[bool] = None
    verify_issues: int = 0


@dataclass
class _Plan:
    """Bir zip'in geri yükleme planı: hangi üye nereye gider (veri dizinine göre yol)."""

    format: int
    manifest: Dict[str, Any]
    data: List[Tuple[zipfile.ZipInfo, str]] = field(default_factory=list)  # (üye, hedef yol)
    state: Optional[zipfile.ZipInfo] = None
    schema: Optional[zipfile.ZipInfo] = None
    overrides: Optional[zipfile.ZipInfo] = None  # config/overrides.json
    skipped: List[str] = field(default_factory=list)

    @property
    def scope(self) -> Optional[str]:
        value = self.manifest.get("scope")
        return value if isinstance(value, str) else None

    def entries(self) -> List[str]:
        """Planın yerine koyacağı üst düzey veri girdileri (DATA_ENTRIES sırasıyla)."""
        found = {target.split("/", 1)[0] for _, target in self.data}
        return [entry for entry in DATA_ENTRIES if entry in found]


def _stamp_of(name: str) -> Optional[re.Match[str]]:
    return _NAME_RE.fullmatch(name)


def archive_format(path: str) -> Optional[int]:
    """Zip'in biçimi: `backup.json`'ı varsa onun `format` değeri, yoksa 1; zip okunamıyorsa None."""
    try:
        with zipfile.ZipFile(path) as zf:
            if MANIFEST_MEMBER not in zf.NameToInfo:
                return LEGACY_FORMAT
            value = json.loads(zf.read(MANIFEST_MEMBER).decode("utf-8")).get("format")
    except (OSError, zipfile.BadZipFile, ValueError, AttributeError, RuntimeError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def backup_info(path: str) -> Optional[BackupInfo]:
    """Yolun adı bir yedek adıysa ve dosya varsa bilgisi; değilse None."""
    name = os.path.basename(path)
    match = _stamp_of(name)
    if match is None:
        return None
    try:
        size = os.stat(path).st_size
    except OSError:
        return None
    created = datetime.strptime(match.group(3), _TIME_FORMAT).isoformat()
    return BackupInfo(name=name, path=path, scope=match.group(1), with_env=bool(match.group(2)),
                      created_at=created, size=size, format=archive_format(path))


def _unsafe(name: str) -> Optional[str]:
    """Üye adı veri dizininin dışına çıkabiliyorsa nedeni; güvenliyse None."""
    if not name or "\x00" in name or "\\" in name:
        return "contains a backslash or a NUL character" if name else "is empty"
    if name.startswith("/") or _DRIVE_RE.match(name):
        return "is an absolute path"
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "contains '..', '.' or an empty component"
    return None


def _is_dir_entry(info: zipfile.ZipInfo) -> bool:
    return info.filename.endswith("/")


def _plan(zf: zipfile.ZipFile) -> _Plan:
    """
    Zip'i okur ve planı çıkarır; hiçbir şey yazmaz. Güvensiz ya da tanınmayan üye, bozuk `backup.json` ve
    bilinmeyen biçim BackupInvalid. Sürümleri denetlemez (`_check_versions`).
    """
    infos = zf.infolist()
    for info in infos:
        reason = _unsafe(info.filename)
        if reason is not None:
            raise BackupInvalid(f"The backup has a member whose path {reason}: {info.filename!r}",
                                detail=info.filename)
    if MANIFEST_MEMBER in zf.NameToInfo:
        try:
            manifest = json.loads(zf.read(MANIFEST_MEMBER).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise BackupInvalid(f"backup.json cannot be read ({e})", detail=MANIFEST_MEMBER) from e
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            found = manifest.get("format") if isinstance(manifest, dict) else None
            raise BackupInvalid(f"Unknown backup format {found!r} (this version reads formats 1 and 2)",
                                detail=MANIFEST_MEMBER)
        return _plan_v2(infos, manifest)
    return _plan_v1(infos)


def _plan_v2(infos: Sequence[zipfile.ZipInfo], manifest: Dict[str, Any]) -> _Plan:
    plan = _Plan(format=FORMAT, manifest=manifest)
    for info in infos:
        name = info.filename
        if _is_dir_entry(info) or name == MANIFEST_MEMBER:
            continue
        top = name.split("/", 1)[0]
        if name == STATE_MEMBER:
            plan.state = info
        elif name == SCHEMA_MEMBER:
            plan.schema = info
        elif name == CATALOG_MEMBER:
            plan.skipped.append(name)
        elif name == OVERRIDES_MEMBER:
            plan.overrides = info
        elif top == CONFIG_MEMBER_DIR and name.count("/") == 1:
            plan.skipped.append(name)
        elif (top in DATA_ENTRIES and top != LEGACY_CHANGES and "/" in name) or name == LEGACY_CHANGES:
            plan.data.append((info, name))
        else:
            raise BackupInvalid(f"The backup has a member this version does not know: {name!r}", detail=name)
    return plan


def _plan_v1(infos: Sequence[zipfile.ZipInfo]) -> _Plan:
    """
    Biçim 1: kökteki dosyalar ayar dosyalarıdır (geri yüklenmez), veri ağaçları `<ad>/<ağaç>/...` biçimindedir
    ve hepsinin ilk bileşeni (eski veri dizininin adı) aynıdır.
    """
    plan = _Plan(format=LEGACY_FORMAT, manifest={})
    prefix: Optional[str] = None
    for info in infos:
        name = info.filename
        if _is_dir_entry(info):
            continue
        parts = name.split("/")
        if len(parts) == 1:
            plan.skipped.append(name)
            continue
        if len(parts) < 3 or parts[1] not in LEGACY_TREES or (prefix is not None and parts[0] != prefix):
            raise BackupInvalid(f"The backup (format 1) has a member this version does not know: {name!r}",
                                detail=name)
        prefix = parts[0]
        plan.data.append((info, "/".join(parts[1:])))
    return plan


def _counts_of(plan: _Plan) -> Dict[str, int]:
    """Biçim 2'de `backup.json`'ın sayımları; biçim 1'de üyelerden sayılanlar."""
    counted = plan.manifest.get("counts")
    if plan.format == FORMAT and isinstance(counted, dict):
        return {str(k): int(v) for k, v in counted.items() if isinstance(v, int) and not isinstance(v, bool)}
    return dict(_count_members(target for _, target in plan.data))


def _count_members(paths: Iterator[str]) -> Counter[str]:
    """Veri dizinine göre yollardan maç ve turnuva sayımları."""
    counts: Counter[str] = Counter()
    legacy_events = set()
    tournaments = set()
    for path in paths:
        parts = path.split("/")
        counts["files"] += 1
        if parts[0] == layout.V3_DIR and len(parts) == 6 and parts[1] == "events" and parts[5] == layout.MANIFEST_NAME:
            counts["v3_events"] += 1
        elif parts[0] == "match_details" and len(parts) >= 5:
            legacy_events.add("/".join(parts[1:4]))
        if parts[0] == layout.V3_DIR and len(parts) >= 3 and parts[1] == "tournaments":
            tournaments.add(("v3", parts[2]))
        elif parts[0] in LEGACY_TREES and len(parts) >= 3:
            tournaments.add(("legacy", parts[1]))
    counts["legacy_events"] = len(legacy_events)
    counts["tournaments"] = len(tournaments)
    return counts


def _rows_of(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Tuple[List[str], List[Any]]:
    """Sorgunun sütun adları ve satırları (satırları sütunlarıyla başka bir veritabanına geri yazmak için)."""
    cursor = conn.execute(sql, tuple(params))
    return [str(column[0]) for column in cursor.description or ()], cursor.fetchall()


def _overrides_payload(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Optional[bytes]:
    """
    Yedekteki `config/overrides.json`'ın baytları; bir JSON nesnesi değilse ya da çok büyükse None (uyarı
    günlüğüyle; içerik proxy parolası taşıyabileceği için günlüğe yazılmaz).
    """
    problem = ""
    data = b""
    if info.file_size > _OVERRIDES_MAX_BYTES:
        problem = f"larger than {_OVERRIDES_MAX_BYTES} bytes"
    else:
        try:
            data = zf.read(info)
            if not isinstance(json.loads(data.decode("utf-8")), dict):
                problem = "not a JSON object"
        except (UnicodeDecodeError, ValueError):
            problem = "not valid JSON"
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as e:
            problem = f"unreadable ({e.__class__.__name__})"
    if problem:
        logger.warning("The backup's %s is %s; the settings file was not restored", OVERRIDES_MEMBER, problem)
        return None
    return data


def _read_if_present(path: str) -> Optional[bytes]:
    """Dosyanın baytları; dosya yoksa None (başka okuma hatası StoreError)."""
    if not os.path.lexists(path):
        return None
    return files.read_bytes(path)


def _has_files(path: str) -> bool:
    """Yol bir dosyaysa (boş değilse) ya da altında en az bir dosya varsa True."""
    if os.path.isfile(path):
        try:
            return os.path.getsize(path) > 0
        except OSError:
            return True
    for _, _, names in os.walk(path):
        if names:
            return True
    return False


class BackupManager:
    """Veri dizininin yedekleri (`Store.backup`)."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    @property
    def directory(self) -> str:
        """Yedeklerin dizini: `<veri dizini>/backups` (web sunucusunun statik olarak servis etmediği bir yer)."""
        return layout.resolve(self._store.data_dir, layout.BACKUPS_DIR)

    # --- yazma -----------------------------------------------------------------------------------

    def create(self, scope: str = "all", *, config_files: Sequence[str] = (), env_file: Optional[str] = None,
               overrides_file: Optional[str] = None, now: Optional[datetime] = None) -> BackupInfo:
        """
        Yedeği biçim 2'de yazar ve bilgisini döndürür (modül belgesindeki tablo).

        config_files  kapsam `all`, `state` ya da `config` ise `config/<dosya adı>` olarak eklenecek ayar
                      dosyaları (verildiği sırayla; olmayan atlanır)
        env_file      verilirse, kapsam `all`, `state` ya da `config` ise ve dosya varsa `config/.env` olarak
                      eklenir; dosya adı `_with_env` taşır ve izni 0600 olur
        overrides_file  verilirse, aynı kapsamlarda ve dosya varsa `config/overrides.json` olarak eklenir;
                      proxy adresi parola taşıyabildiği için dosyanın izni 0600 olur (adı değişmez)
        now           dosya adındaki zaman (yerel); verilmezse şimdi

        Bilinmeyen kapsam ValueError; dosya sistemi hatası StoreError (yarım zip silinir).
        """
        if scope not in BACKUP_SCOPES:
            raise ValueError(f"unknown backup scope: {scope!r}")
        data_dir = str(self._store.data_dir)
        moment = now or datetime.now()
        stamp = moment.strftime(_TIME_FORMAT)
        with_env = env_file is not None and scope in CONFIG_SCOPES and os.path.exists(env_file)
        with_overrides = overrides_file is not None and scope in CONFIG_SCOPES and os.path.isfile(overrides_file)
        name = f"backup_{scope}{WITH_ENV_TAG if with_env else ''}_{stamp}.zip"
        directory = self.directory
        path = os.path.join(directory, name)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            raise StoreError.from_exception(e, directory) from e
        staging: Optional[str] = None
        try:
            if with_env or with_overrides:
                # İçinde .env ya da overrides.json var: dosya baştan yalnızca sahibince okunur yaratılır (zip
                # sonra içini yazar)
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_MODE))
                if os.name == "posix":
                    os.chmod(path, _PRIVATE_MODE)
            counts: Counter[str] = Counter()
            state_schema: Optional[int] = None
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                if scope in STATE_SCOPES:
                    staging = files.new_staging_dir(data_dir, "backup")
                    copy = os.path.join(staging, "state.db")
                    state_schema = self._snapshot_state(copy, counts)
                    zf.write(copy, STATE_MEMBER)
                if scope != "config":
                    schema = layout.resolve(data_dir, layout.SCHEMA_FILE)
                    if os.path.isfile(schema):
                        zf.write(schema, SCHEMA_MEMBER)
                members: List[str] = []
                for file_path, member in self._data_files(scope):
                    compress = zipfile.ZIP_STORED if member.endswith(_STORED_SUFFIX) else zipfile.ZIP_DEFLATED
                    zf.write(file_path, member, compress_type=compress)
                    members.append(member)
                counts.update(_count_members(iter(members)))
                if scope in CONFIG_SCOPES:
                    for config_file in config_files:
                        member = f"{CONFIG_MEMBER_DIR}/{os.path.basename(config_file)}"
                        if os.path.exists(config_file) and not (with_overrides and member == OVERRIDES_MEMBER):
                            zf.write(config_file, member)
                    if with_overrides:
                        assert overrides_file is not None
                        zf.write(overrides_file, OVERRIDES_MEMBER)
                    if with_env:
                        assert env_file is not None
                        zf.write(env_file, f"{CONFIG_MEMBER_DIR}/{ENV_MEMBER}")
                zf.writestr(MANIFEST_MEMBER, self._manifest(scope, with_env, moment, state_schema, counts))
        except BaseException as e:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            if isinstance(e, OSError):
                raise StoreError.from_exception(e, path) from e
            if isinstance(e, sqlite3.Error):
                raise StoreError(f"state.db could not be copied into the backup ({e})", path=path,
                                 detail=str(e)) from e
            raise
        finally:
            if staging is not None:
                with contextlib.suppress(StoreError):
                    files.remove_tree(staging)
        info = backup_info(path)
        if info is None:
            raise StoreError(f"Backup was written but cannot be read back: {path}", path=path)
        return info

    def _snapshot_state(self, target: str, counts: Counter[str]) -> int:
        """
        state.db'nin tutarlı bir kopyasını SQLite'ın yedekleme API'siyle `target`'a alır (WAL dosyası
        kopyalanmaz; kopya tek dosyadır). Kopyada kilit sahibi satırları silinir. Şema sürümünü döndürür.
        """
        source = self._store._state.connection()
        copy = sqlite3.connect(target, isolation_level=None)
        try:
            source.backup(copy)
            copy.execute("PRAGMA journal_mode = DELETE")
            copy.execute("DELETE FROM leases")
            for table in ("follows", "jobs", "stream_events", "sink_cursors"):
                counts[table] = int(copy.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            counts["api_follows"] = int(
                copy.execute("SELECT count(*) FROM follows WHERE origin = 'api'").fetchone()[0])
            return int(copy.execute("PRAGMA user_version").fetchone()[0])
        finally:
            copy.close()

    def _data_files(self, scope: str) -> Iterator[Tuple[str, str]]:
        """Kapsamın veri dosyaları: (gerçek yol, üye adı); ağaç sırası belirlidir (adlara göre sıralı)."""
        if scope in ("all", "data"):
            roots = [layout.V3_DIR, layout.CHANGES_DIR, *LEGACY_TREES, LEGACY_CHANGES]
        elif scope == "match_details":
            roots = [layout.EVENTS_DIR, "match_details"]
        elif scope in ("matches", "seasons"):
            roots = [layout.TOURNAMENTS_DIR, scope]
        else:
            return
        for root in roots:
            for file_path, member in self._walk(root):
                if member.startswith(layout.TOURNAMENTS_DIR + "/") and scope in ("matches", "seasons"):
                    # v3/tournaments/<id>/seasons/** program sayfalarıdır, geri kalanı sezon listeleri
                    schedule = member.split("/")[3:4] == ["seasons"]
                    if schedule != (scope == "matches"):
                        continue
                yield file_path, member

    def _walk(self, rel: str) -> Iterator[Tuple[str, str]]:
        """`rel` altındaki dosyalar (ya da `rel` bir dosyaysa kendisi); sembolik bağlar ve yarım geçici dosyalar atlanır."""
        root = layout.resolve(self._store.data_dir, rel)
        if os.path.isfile(root) and not os.path.islink(root):
            yield root, rel
            return
        for current, dirs, names in os.walk(root):
            dirs.sort()
            base = os.path.relpath(current, self._store.data_dir).replace(os.sep, "/")
            for file_name in sorted(names):
                file_path = os.path.join(current, file_name)
                if os.path.islink(file_path) or (file_name.startswith(".") and file_name.endswith(".tmp")):
                    continue
                yield file_path, f"{base}/{file_name}"

    def _manifest(self, scope: str, with_env: bool, moment: datetime, state_schema: Optional[int],
                  counts: Mapping[str, int]) -> str:
        from src.store.api import LAYOUT_VERSION
        from src.version import __version__

        document = {
            "format": FORMAT,
            "app_version": __version__,
            "layout_version": LAYOUT_VERSION,
            "state_schema": state_schema,
            "scope": scope,
            "with_env": with_env,
            "created_at": moment.replace(microsecond=0).isoformat(),
            "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "store_id": self._store.store_id,
            "counts": dict(sorted(counts.items())),
        }
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    # --- okuma -----------------------------------------------------------------------------------

    def list(self) -> List[BackupInfo]:
        """`backups/` altındaki yedekler, en yeni önce (addaki zamana, eşitse ada göre). Dizin yoksa boş."""
        directory = self.directory
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            return []
        except OSError as e:
            raise StoreError.from_exception(e, directory, reading=True) from e
        found = [info for info in (backup_info(os.path.join(directory, n)) for n in names) if info is not None]
        return sorted(found, key=lambda info: (info.created_at, info.name), reverse=True)

    def path_of(self, name: str) -> str:
        """`backups/` altındaki yedeğin yolu. Ad bir yedek adı değilse ya da dosya yoksa BackupNotFound."""
        if not isinstance(name, str) or _stamp_of(name) is None:
            raise BackupNotFound(f"Not a backup name: {name!r}", detail=str(name))
        path = os.path.join(self.directory, name)
        if not os.path.isfile(path):
            raise BackupNotFound(f"No backup with this name in {self.directory}: {name}", path=path)
        return path

    def verify(self, name: str) -> BackupCheck:
        """
        Yedeği okur ve denetler; hiçbir şey yazmaz (state.db denetim için `.meta/tmp` altına açılır). Her
        üyenin CRC'si okunarak denetlendiği için süre arşivin boyutuyla artar. Bilinmeyen ad BackupNotFound.
        """
        path = self.path_of(name)
        problems: List[str] = []
        try:
            zf = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            return BackupCheck(name=name, ok=False, format=None, scope=None, problems=(f"not a readable zip: {e}",))
        with zf:
            try:
                plan: Optional[_Plan] = _plan(zf)
            except BackupInvalid as e:
                plan = None
                problems.append(str(e))
            members = [info for info in zf.infolist() if not _is_dir_entry(info)]
            for info in members:
                try:
                    with zf.open(info) as stream:
                        while stream.read(_COPY_CHUNK):
                            pass
                except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError) as e:
                    problems.append(f"{info.filename}: {e}")
            if plan is not None:
                try:
                    self._check_versions(plan)
                except SchemaTooNew as e:
                    problems.append(str(e))
                if plan.state is not None and not any(p.startswith(STATE_MEMBER) for p in problems):
                    problems.extend(self._check_state_member(zf, plan.state))
        fmt = plan.format if plan is not None else archive_format(path)
        scope = plan.scope if plan is not None and plan.format == FORMAT else None
        if scope is None:
            match = _stamp_of(name)
            scope = match.group(1) if match else None
        return BackupCheck(name=name, ok=not problems, format=fmt, scope=scope,
                           manifest=dict(plan.manifest) if plan is not None else {},
                           members=len(members), bytes=sum(info.file_size for info in members),
                           problems=tuple(problems))

    def _check_state_member(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> List[str]:
        from src.store.state import APPLICATION_ID

        staging = files.new_staging_dir(self._store.data_dir, "backup")
        try:
            target = os.path.join(staging, "state.db")
            self._extract(zf, info, target)
            conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
                result = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
            finally:
                conn.close()
        except sqlite3.Error as e:
            return [f"{STATE_MEMBER}: {e}"]
        finally:
            with contextlib.suppress(StoreError):
                files.remove_tree(staging)
        problems = [] if result == ["ok"] else [f"{STATE_MEMBER}: {line}" for line in result]
        if app_id != APPLICATION_ID:
            problems.append(f"{STATE_MEMBER}: not a state.db (application_id {app_id:#x})")
        return problems

    def _check_versions(self, plan: _Plan) -> None:
        """Yedek bu kodun yazabildiğinden yeni bir düzen ya da state şemasıyla yazılmışsa SchemaTooNew."""
        from src.store.api import LAYOUT_VERSION

        if plan.format != FORMAT:
            return
        layout_version = plan.manifest.get("layout_version")
        if isinstance(layout_version, int) and layout_version > LAYOUT_VERSION:
            raise SchemaTooNew(path=MANIFEST_MEMBER, component="layout", found=layout_version,
                               supported=LAYOUT_VERSION)
        state_schema = plan.manifest.get("state_schema")
        latest = self._store._state.latest_version
        if isinstance(state_schema, int) and state_schema > latest:
            raise SchemaTooNew(path=MANIFEST_MEMBER, component="state", found=state_schema, supported=latest)

    # --- geri yükleme ----------------------------------------------------------------------------

    def restore(self, name: str, *, force: bool = False, dry_run: bool = False,
                overrides_file: Optional[str] = None) -> RestoreReport:
        """
        `backups/` altındaki yedeği bu veri dizinine geri yükler (bölüm 9.2).

          1. Zip okunur: her üye yolu göreli ve veri dizininin içinde kalmalı, `backup.json` (biçim 2) düzen ve
             state sürümleri bu koddan yeni olmamalı. Biçim 1 (2.x) eski düzen ağaçları olarak yüklenir.
          2. Hedefte v3 ağacı, eski düzen ağacı ya da API'den eklenmiş takip olmamalı (yedek değişiklik
             günlüğü taşıyorsa hedefinki de boş olmalı); varsa ve `force` yoksa RestoreRefused. `force` ile
             hedefteki her veri girdisi önce `.meta/trash/restore-<zaman>/` altına taşınır ve geri yükleme
             bitene kadar orada kalır. Birleştirme yoktur: `force` değiştirir, karıştırmaz.
          3. Üyeler `.meta/tmp/restore.<rastgele>/` altına açılır, sonra üst düzey girdiler yerine taşınır.
             state.db (varsa) önce orada eski sürümse güncellenir (geçişler), sonra SQLite'ın yedekleme
             API'siyle açık veritabanının üzerine yazılır; olay günlüğü yeni bir kimlik (`stream_id`) alır,
             kilit sahibi satırları korunur. Bir adım başarısız olursa yapılanlar geri alınır.
          4. Katalog dosyalardan yeniden kurulur ve tutarlılık denetimi (`verify`) çalışır.

        `maintenance` kilidi gerekir: bu süreç tutuyorsa onun altında çalışır, tutmuyorsa alır (amaç
        `op:restore`; dizini kullanan varsa LeaseHeld). dry_run=True: kilit almaz, hiçbir şey yazmaz; raporda
        olacak olan durur ve hedef boş değilse `occupied` doludur (hata değildir).

        Ayar dosyaları ve `.env` geri yüklenmez (`skipped`). İstisna `config/overrides.json`: `overrides_file`
        verilirse ve yedekte varsa 3. adımın sonunda oraya 0600 izniyle atomik olarak yazılır (önceki hali geri
        almada geri gelir); yedekte yoksa (eski yedekler) dosyaya dokunulmaz. Bir JSON nesnesi değilse geri
        yüklenmez (`skipped`, uyarı günlüğü; içerik günlüğe yazılmaz).

        Bilinmeyen ad BackupNotFound, okunamayan ya da güvensiz yedek BackupInvalid, daha yeni sürüm
        SchemaTooNew, salt okunur depo StoreError.
        """
        path = self.path_of(name)
        try:
            zf = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            raise BackupInvalid(f"The backup is not a readable zip ({e}): {name}", path=path) from e
        with zf:
            plan = _plan(zf)
            self._check_versions(plan)
            counts = _counts_of(plan)
            restored = plan.entries() + ([STATE_MEMBER] if plan.state is not None else [])
            skipped = list(plan.skipped)
            settings: Optional[Tuple[str, bytes]] = None  # (hedef yol, içerik)
            if plan.overrides is not None:
                payload = _overrides_payload(zf, plan.overrides) if overrides_file is not None else None
                if payload is None or overrides_file is None:
                    skipped.append(OVERRIDES_MEMBER)
                else:
                    settings = (overrides_file, payload)
                    restored.append(OVERRIDES_MEMBER)
            settings_replaced = [OVERRIDES_MEMBER] if settings is not None and os.path.lexists(settings[0]) else []
            if dry_run:
                occupied = self._occupied(plan)
                return RestoreReport(
                    name=name, format=plan.format, scope=plan.scope, dry_run=True, force=force,
                    restored=tuple(restored),
                    replaced=tuple(self._to_replace(plan, force or bool(occupied)) + settings_replaced),
                    skipped=tuple(skipped), occupied=tuple(occupied), counts=counts)
            if self._store.readonly:
                raise StoreError(f"A store opened read-only cannot be restored into: {self._store.data_dir}",
                                 path=str(self._store.data_dir))
            lease: Optional["Lease"] = None
            if not self._store._leases.held_here("maintenance"):
                lease = self._store._leases.acquire("maintenance", purpose=RESTORE_PURPOSE)
            try:
                occupied = self._occupied(plan)  # kilit altında yeniden: arada biri yazmış olabilir
                if occupied and not force:
                    raise RestoreRefused(
                        f"The data directory is not empty ({', '.join(occupied)}); a restore never merges. "
                        f"Restore with force to move the current data to the trash first: {self._store.data_dir}",
                        path=str(self._store.data_dir), reasons=occupied)
                replaced = self._apply(zf, plan, force=force, settings=settings) + settings_replaced
                rebuilt, verify_ok, verify_issues = self._after_restore()
            finally:
                if lease is not None:
                    lease.release()
        logger.info("Backup restored into %s: %s (format %s, restored=%s, replaced=%s, catalog_rebuilt=%s, "
                    "verify_ok=%s)", self._store.data_dir, name, plan.format, ",".join(restored) or "-",
                    ",".join(replaced) or "-", rebuilt, verify_ok)
        return RestoreReport(
            name=name, format=plan.format, scope=plan.scope, dry_run=False, force=force, restored=tuple(restored),
            replaced=tuple(replaced), skipped=tuple(skipped), occupied=tuple(occupied), counts=counts,
            catalog_rebuilt=rebuilt, verify_ok=verify_ok, verify_issues=verify_issues)

    def _occupied(self, plan: _Plan) -> List[str]:
        """Hedefte bulunan ve geri yüklemenin üzerine yazmayacağı şeyler (bölüm 9.2'nin boş hedef kuralı)."""
        data_dir = self._store.data_dir
        found = [entry for entry in (layout.V3_DIR, *LEGACY_TREES) if _has_files(layout.resolve(data_dir, entry))]
        if plan.data:
            found += [entry for entry in (layout.CHANGES_DIR, LEGACY_CHANGES)
                      if entry in plan.entries() and _has_files(layout.resolve(data_dir, entry))]
        if self._store.follows.list(origin="api"):
            found.append("follows")
        return found

    def _to_replace(self, plan: _Plan, force: bool) -> List[str]:
        """Çöpe taşınacak üst düzey girdiler: yerine konacak olanlar, `force` ile hedefteki her veri girdisi."""
        wanted = DATA_ENTRIES if force and plan.data else plan.entries()
        return [entry for entry in wanted if os.path.lexists(layout.resolve(self._store.data_dir, entry))]

    @staticmethod
    def _extract(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: str) -> None:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as source, open(target, "wb") as sink:
            shutil.copyfileobj(source, sink, _COPY_CHUNK)

    def _apply(self, zf: zipfile.ZipFile, plan: _Plan, *, force: bool,
               settings: Optional[Tuple[str, bytes]] = None) -> List[str]:
        """
        Planı uygular (kilit altında); çöpe taşınan girdileri döndürür. Hata olursa yapılanı geri alır.
        settings: (yol, içerik) verilirse ayar belgesi en son, 0600 izniyle yazılır.
        """
        data_dir = str(self._store.data_dir)
        staging = files.new_staging_dir(data_dir, "restore")
        trash = os.path.join(layout.resolve(data_dir, layout.TRASH_DIR),
                             f"restore-{datetime.now().strftime(_TIME_FORMAT)}.{uuid.uuid4().hex[:8]}")
        replaced: List[str] = []
        moved_aside: List[Tuple[str, str]] = []  # (asıl yer, çöpteki yer)
        placed: List[str] = []  # yerine konan girdiler
        state_saved: Optional[str] = None
        settings_saved: Optional[Tuple[str, Optional[bytes]]] = None  # (yol, önceki içerik; dosya yoksa None)
        keep_trash = False  # geri alma başarısız oldu: önceki veri çöpte kalır
        try:
            try:
                for info, target in plan.data:
                    self._extract(zf, info, os.path.join(staging, "data", *target.split("/")))
                staged_state: Optional[str] = None
                if plan.state is not None:
                    staged_state = os.path.join(staging, "state.db")
                    self._extract(zf, plan.state, staged_state)
                    self._upgrade_state(staged_state)
                os.makedirs(trash)
                replaced = self._to_replace(plan, force)
                for entry in replaced:
                    origin = layout.resolve(data_dir, entry)
                    aside = os.path.join(trash, entry)
                    os.rename(origin, aside)
                    moved_aside.append((origin, aside))
                for entry in plan.entries():
                    origin = layout.resolve(data_dir, entry)
                    os.rename(os.path.join(staging, "data", entry), origin)
                    placed.append(origin)
                if staged_state is not None:
                    saved = os.path.join(trash, "state.db")
                    self._copy_db(self._store._state.connection(), saved)
                    state_saved = saved  # yalnızca tam kopya geri yüklenir
                    self._load_state(staged_state)
                if settings is not None:
                    target, payload = settings
                    settings_saved = (target, _read_if_present(target))
                    files.write_bytes(target, payload, durable=True, mode=_PRIVATE_MODE)
            except BaseException as e:
                try:
                    self._roll_back(moved_aside, placed, state_saved, settings_saved)
                except StoreError:
                    keep_trash = True
                    raise
                if isinstance(e, OSError):
                    raise StoreError.from_exception(e) from e
                if isinstance(e, sqlite3.Error):
                    raise StoreError(f"state.db could not be restored ({e})", path=self._store._state.path,
                                     detail=str(e)) from e
                raise
        finally:
            for leftover in (staging,) if keep_trash else (staging, trash):
                try:
                    files.remove_tree(leftover)
                except StoreError as e:
                    logger.warning("A restore left files behind that could not be removed: %s", e)
        return replaced

    @staticmethod
    def _upgrade_state(path: str) -> None:
        """Açılmış state.db kopyasını bu kodun şemasına getirir (geçişler; dosya bir state.db değilse StoreError)."""
        from src.store.state import StateDb

        StateDb(path).close()
        with contextlib.suppress(OSError):  # geçişin yan dosyaları hazırlık alanında kalır, silinir
            for suffix in ("-wal", "-shm"):
                if os.path.exists(path + suffix):
                    os.remove(path + suffix)

    @staticmethod
    def _copy_db(source: sqlite3.Connection, target: str) -> None:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()

    def _load_state(self, path: str) -> None:
        """
        Açılmış state.db'yi SQLite'ın yedekleme API'siyle deponun açık state.db'sinin üzerine yazar. Bu süreçteki
        kilit sahibi satırları korunur (kilitleri işletim sistemi tutar, satırlar bilgidir), olay günlüğü yeni
        bir kimlik alır: geri yüklenen günlüğün sıra numaraları tüketicinin sakladığı konumla karşılaştırılamaz.
        Çalışan iş (geri yüklemeyi yapan iş, ör. API'nin `restore` işi) satırı ve olaylarıyla korunur: iş
        bittiğinde kendi satırına yazar; geri yüklenen geçmişte o satır yoktur (plan maddesi FX-13).

        Korunan satırlar ve yeni kimlik önce `path`teki kopyaya yazılır, sonra kopya tek bir yedekleme adımıyla
        yerine konur: açık veritabanını okuyan başka bir bağlantı (iş geçmişini soran web isteği) ya eski ya
        yeni içeriği görür, işin satırının olmadığı bir ara durumu görmez. Önceden satırlar yedeklemeden sonra
        ayrı bir işlemle geri yazılıyordu; aradaki an Windows'ta (yavaş `fsync`) okuyana `not_found` verdi.
        """
        from src.store.streams import META_STREAM_ID

        state = self._store._state
        live = state.connection()
        leases = _rows_of(live, "SELECT * FROM leases")
        running = _rows_of(live, "SELECT * FROM jobs WHERE status IN ('running', 'queued')")
        running_ids = [row[running[0].index("id")] for row in running[1]] if running[1] else []
        events = _rows_of(live, "SELECT * FROM job_events WHERE job_id IN ({})".format(
            ", ".join("?" * len(running_ids))), running_ids) if running_ids else ([], [])
        source = sqlite3.connect(path, isolation_level=None)
        try:
            source.execute("BEGIN IMMEDIATE")
            try:
                source.execute("DELETE FROM leases")
                for job_id in running_ids:
                    source.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                    source.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
                for table, (columns, rows) in (("leases", leases), ("jobs", running), ("job_events", events)):
                    if rows:
                        source.executemany(
                            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                            [tuple(row) for row in rows])
                source.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (META_STREAM_ID, uuid.uuid4().hex))
                source.execute("COMMIT")
            except BaseException:
                source.execute("ROLLBACK")
                raise
            source.backup(live)
        finally:
            source.close()
        self._store.streams._stream_id = None

    def _roll_back(self, moved_aside: Sequence[Tuple[str, str]], placed: Sequence[str],
                   state_saved: Optional[str], settings_saved: Optional[Tuple[str, Optional[bytes]]] = None) -> None:
        """
        Yarıda kalan geri yüklemeyi geri alır: yerine konanlar silinir, çöpe taşınanlar geri gelir, ayar belgesi
        önceki haline döner (yoksa silinir).
        """
        try:
            if settings_saved is not None:
                target, before = settings_saved
                if before is None:
                    files.remove(target)
                else:
                    files.write_bytes(target, before, durable=True, mode=_PRIVATE_MODE)
            for origin in reversed(placed):
                files.remove_tree(origin)
            for origin, aside in reversed(moved_aside):
                os.rename(aside, origin)
            if state_saved is not None and os.path.isfile(state_saved):
                self._load_state(state_saved)
        except (OSError, StoreError, sqlite3.Error) as e:
            logger.error("A failed restore could not be rolled back completely (%s); the previous data is in %s",
                         e, layout.resolve(self._store.data_dir, layout.TRASH_DIR))
            raise StoreError(f"A failed restore could not be rolled back ({e}); the previous data is in "
                             f"{layout.resolve(self._store.data_dir, layout.TRASH_DIR)}",
                             path=str(self._store.data_dir)) from e

    def _after_restore(self) -> Tuple[bool, Optional[bool], int]:
        """Katalog geri yüklenen dosyalardan yeniden kurulur, sonra tutarlılık denetimi çalışır."""
        from src.store import api

        rebuilt = api._shadow(self._store.data_dir, "a restore", api._rebuild_in_place, store=self._store)
        if not rebuilt:
            return False, None, 0
        try:
            report = self._store.catalog.verify()
        except (StoreError, sqlite3.Error) as e:
            logger.warning("The data directory could not be checked after the restore: %s", e)
            return True, None, 0
        if not report.ok:
            logger.warning("The check after the restore found %d issue(s) in %s; run the catalog check",
                           len(report.open_issues), self._store.data_dir)
        return True, report.ok, len(report.open_issues)

    # --- eskileri silme --------------------------------------------------------------------------

    def prune(self, keep: Optional[int] = None, max_age_days: Optional[float] = None, *,
              now: Optional[datetime] = None) -> List[BackupInfo]:
        """
        Eski yedekleri siler ve silinenleri döndürür (bölüm 9.3; varsayılan: hepsi kalır). keep: en yeni
        `keep` yedeğin dışındakiler silinir. max_age_days: addaki zamanı bundan eski olanlar silinir. İkisi de
        verilirse ikisinin de sildiği değil, herhangi birinin sildiği silinir (`StreamLog.prune` gibi).
        Negatif sınır ValueError; silinemeyen dosya StoreError.
        """
        if keep is not None and (isinstance(keep, bool) or not isinstance(keep, int) or keep < 0):
            raise ValueError(f"keep must be 0 or more: {keep!r}")
        if max_age_days is not None and max_age_days < 0:
            raise ValueError(f"max_age_days must be 0 or more: {max_age_days!r}")
        found = self.list()
        doomed: List[BackupInfo] = []
        cutoff = None if max_age_days is None else (now or datetime.now()) - timedelta(days=max_age_days)
        for position, info in enumerate(found):
            too_many = keep is not None and position >= keep
            too_old = cutoff is not None and datetime.fromisoformat(info.created_at) < cutoff
            if too_many or too_old:
                doomed.append(info)
        for info in doomed:
            files.remove(info.path)
        if doomed:
            logger.info("Removed %d old backup(s) from %s", len(doomed), self.directory)
        return doomed


__all__ = [
    "BACKUP_SCOPES",
    "BackupCheck",
    "BackupInfo",
    "BackupInvalid",
    "BackupManager",
    "BackupNotFound",
    "RestoreRefused",
    "RestoreReport",
    "backup_info",
]
