"""
Tanılama: son log satırları ve hata bildirimine eklenebilecek paket.

Paket (zip) üç dosya içerir:
  diagnostics.json  uygulama sürümü ve commit'i, Python/işletim sistemi, paket sürümleri, ayarlar
                    (gizli değerler maskeli), köprü sağlığı, istek bütçesi, kurulum denetimi
                    (sofascore_scraper.doctor; tarayıcı başlatılmadan), son işler, log dosyaları
  log_tail.txt      log dosyasının son satırları
  README.txt        içinde ne olduğu

Her şey sofascore_scraper.redact'ten geçer (token, cookie, proxy parolası `***` olur) ve ev dizini `~` ile
değiştirilir. İş geçmişinin sütunları adıyla seçilir (_JOB_COLUMNS) ve işi başlatan sürecin makine adı
pakete girmez; iş metinlerinde, tarayıcı profili kilidinde ve log kuyruğunda geçen makine adı `***` olur
(log kuyruğunda: bu makinenin adı ve seçilen iş satırlarında kayıtlı adlar; bkz. log_tail_text).
Okunan dosyalar sabittir: log dosyası yalnızca sofascore_scraper.logger'ın yazdığı dosyadır,
dışarıdan yol alınmaz. Paket üretmek hiçbir şeyi değiştirmez (iş geçmişi salt okunur açılır).

Web: GET /api/v1/logs, GET /api/v1/diagnostics, GET /api/v1/diagnostics/bundle (sofascore_scraper/web/api/v1/).
CLI: ssc diagnostics [--out YOL]
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import platform
import re
import shutil
import socket
import sqlite3
import sys
import time
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import dotenv

from sofascore_scraper import logger as app_logger
from sofascore_scraper.paths import default_league_config_path, env_file_path
from sofascore_scraper.redact import MASK, mask_value, redact_obj, redact_text
from sofascore_scraper.version import __version__

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Tek istekte log dosyalarından en fazla bu kadar bayt okunur (yanıtın ve sürenin üst sınırı)
MAX_SCAN_BYTES = 8 * 1024 * 1024
_FIRST_WINDOW_BYTES = 256 * 1024
MAX_LOG_ENTRIES = 2000
MAX_TAIL_LINES = 5000
DEFAULT_TAIL_LINES = 1000
_JOB_LIMIT = 3
_JOB_LIST_CAP = 50  # bir işin sonucundaki listelerden (başarısız maçlar) pakete giren en fazla öğe
# İş geçmişinden pakete giren sütunlar. Sütunlar adıyla seçilir (`SELECT *` değil): `jobs` tablosuna sonradan
# eklenen bir sütun buraya yazılmadıkça pakete girmez. Dosyada olmayan sütun atlanır: 2.x'in jobs.db'sinde
# `kind`, geçiş 0002'den önce yazılmış bir state.db'de `created_at`, `heartbeat_at` ve son üç JSON sütunu yoktur.
#   origin_json            yalnızca işi başlatan yüz ve pid girer, makine adı girmez (bkz. _job_origin)
#   spec_json, error_json  çözülür; içlerindeki metinler diğer bölümler gibi maskelenir (collect)
# Bilerek dışarıda: `owner` (kilit sahibinin rastgele kimliği; `leases` tablosu pakette olmadığı için
# tanıya bir şey katmaz).
_JOB_COLUMNS: Tuple[str, ...] = (
    "id", "status", "kind", "progress", "current_task",
    "created_at", "started_at", "finished_at", "heartbeat_at", "cancel_requested",
    "matches_total", "matches_done", "matches_failed", "schedule_empty_seasons",
    "circuit_breaker_triggered", "circuit_breaker_reason", "eta_seconds", "current_batch",
    "payload_json", "log_json", "result_json", "spec_json", "error_json", "origin_json",
)
_JOB_JSON_COLUMNS: Tuple[str, ...] = (
    "payload_json", "log_json", "result_json", "spec_json", "error_json", "origin_json",
)
# Pakete girmeyen kurulum denetimleri: "browser" ayrı bir süreçte Chromium başlatır (saniyeler sürer,
# uç nokta her çağrıda bunu yapmamalı); köprünün gerçek durumu zaten "bridge" bölümündedir.
_DOCTOR_SKIP: Tuple[str, ...] = ("browser",)

# Değeri pakette gösterilen ayarlar (gizli olanlar mask_value ile `***` olur). Burada olmayan
# .env anahtarlarının yalnızca adı ve dolu olup olmadığı yazılır.
SETTING_KEYS: Tuple[str, ...] = (
    "API_BASE_URL", "REQUEST_TIMEOUT", "MAX_RETRIES", "DATA_DIR",
    "SOFA_CAPTCHA_TOKEN",
    "MAX_CONCURRENT", "WAIT_TIME_MIN", "WAIT_TIME_MAX", "REQUEST_RATE_LIMIT",
    "USE_PROXY", "PROXY_URL",
    "FETCH_ONLY_FINISHED", "SAVE_EMPTY_ROUNDS", "REFRESH_WINDOW_HOURS", "REFRESH_MIN_INTERVAL_HOURS",
    "BRIDGE_DEGRADED_AFTER", "BRIDGE_BLOCKED_AFTER", "BRIDGE_BLOCKED_MIN_SECONDS",
    "RATE_LIMIT_THRESHOLD_CONSECUTIVE", "RATE_LIMIT_THRESHOLD_RATIO", "SERVER_ERROR_THRESHOLD_CONSECUTIVE",
    "IGNORE_RATE_LIMIT",
    "USE_COLOR", "DATE_FORMAT", "APP_LANGUAGE",
    "LOG_LEVEL", "DEBUG", "LOG_DIR", "LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT",
)
_PACKAGES = (
    "curl_cffi", "scrapling", "playwright", "patchright", "rich",
    "python-dotenv", "fastapi", "pydantic", "uvicorn", "sse-starlette",
)

_BUNDLE_README = """SofaScore Scraper diagnostics bundle

diagnostics.json  app version/commit, Python and OS, package versions, settings, bridge health,
                  request budget, the setup check (ssc doctor, without starting the
                  browser), the most recent download jobs, log file list
log_tail.txt      the last lines of the application log

Secrets are masked before anything is written here: tokens, cookies, proxy credentials and any
.env value whose key looks like a secret appear as ***; your home directory appears as ~.
Values of .env keys the app does not know are never included, only their names.
The job history, the setup check and the log tail do not carry the host name of this machine
(where a message names it, it appears as ***); the same holds for the host names recorded in
the listed jobs.
Have a quick look before attaching this file to a bug report.

---
Bu paket hata bildirimine eklenmek içindir. Gizli değerler (token, cookie, proxy parolası,
.env'deki gizli görünen değerler) yazılmadan önce *** ile maskelenir; ev dizininiz ~ olarak
görünür. İş geçmişinde, kurulum denetiminde ve log satırlarında makinenizin adı yer almaz (bir
iletide geçiyorsa *** olur); listelenen işlerde kayıtlı makine adları da öyle. Göndermeden önce
içeriğe göz atın.
"""


# --- yardımcılar --------------------------------------------------------------------------

def _home_scrub(text: str) -> str:
    """Ev dizini yolunu `~` yapar (kullanıcı adı hata bildirimine girmesin)."""
    home = os.path.expanduser("~")
    if len(home) > 1 and home in text:
        text = text.replace(home, "~")
    return text


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    if isinstance(obj, str):
        return _home_scrub(obj)
    return obj


def _host_names(names: Iterable[Any]) -> List[str]:
    """Maskelenecek makine adları: verilen adlar ve noktalı bir adın ilk parçası ("pc.lan" → "pc"); uzun olan önce."""
    found = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        found.add(name)
        label = name.split(".", 1)[0]
        if label and not label.isdigit():  # IP adresi biçimindeki bir adın ilk parçası her sayıyı maskelerdi
            found.add(label)
    return sorted(found, key=len, reverse=True)


def _mask_hosts(obj: Any, hosts: Sequence[str]) -> Any:
    """
    Metinlerde geçen makine adlarını `***` yapar; sözlük anahtarlarına dokunmaz. Ad yalnızca tek başına
    geçtiği yerde maskelenir (bir harfe ya da rakama bitişik değilse): "pc" adlı makine "upcoming" sözcüğünü
    bozmaz, "pc-4242" ise "***-4242" olur.
    """
    if not hosts:
        return obj
    pattern = re.compile(
        "(?<![A-Za-z0-9])(?:" + "|".join(re.escape(host) for host in hosts) + ")(?![A-Za-z0-9])", re.IGNORECASE
    )

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            return pattern.sub(MASK, value)
        return value

    return walk(obj)


def _section(fn: Callable[[], Any]) -> Any:
    """Bir bölüm hata verirse paket yine üretilir; hata o bölümün yerine yazılır."""
    try:
        return fn()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


# --- log okuma ----------------------------------------------------------------------------

def _read_tail(path: str, max_bytes: int) -> Tuple[str, int, bool]:
    """Dosyanın son `max_bytes` baytı (yarım kalan ilk satır atılır), okunan bayt, dosyanın tamamı mı."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - max_bytes)
        f.seek(start)
        data = f.read()
    if start > 0:
        cut = data.find(b"\n")
        data = data[cut + 1:] if cut >= 0 else b""
    return data.decode("utf-8", errors="replace"), len(data), start == 0


def _scan_tail(path: str, budget: int, needed: int, parse: Callable[[str], List[Any]]) -> Tuple[List[Any], int]:
    """
    Dosyanın sonundan, `needed` öğe çıkana kadar büyüyen bir pencere okur (en fazla `budget` bayt).
    Son 200 kayıt için 5 MB'lık dosyanın tamamı okunmaz.
    """
    window = min(_FIRST_WINDOW_BYTES, budget)
    while True:
        text, used, whole = _read_tail(path, window)
        items = parse(text)
        if whole or window >= budget or len(items) >= needed:
            return items, used
        window = min(window * 4, budget)


def _levelno(name: str) -> int:
    value = logging.getLevelName(name)
    return value if isinstance(value, int) else logging.NOTSET


def _parse_entries(text: str) -> List[Dict[str, Any]]:
    """Log metnini kayıtlara ayırır; başlığa uymayan satırlar (traceback) önceki kayda eklenir."""
    entries: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        m = app_logger.LINE_RE.match(line)
        if m:
            current = {
                "time": m.group("time"),
                "level": m.group("level"),
                "pid": int(m.group("pid")),
                "logger": m.group("logger"),
                "message": m.group("message"),
            }
            entries.append(current)
        elif current is not None:
            current["message"] += "\n" + line
    return entries


def read_log_entries(limit: int = 200, min_level: Optional[str] = None) -> Dict[str, Any]:
    """
    Log dosyasının son `limit` kaydı (eskiden yeniye). `min_level` verilirse yalnızca o seviye
    ve üstü. Gerekirse çevrilmiş eski dosyalara (.1, .2, ...) da bakılır; toplam okuma sınırlıdır.
    """
    limit = max(1, min(MAX_LOG_ENTRIES, int(limit)))
    threshold = _levelno(min_level.upper()) if min_level else logging.NOTSET
    if min_level and not threshold:
        raise ValueError(f"unknown log level: {min_level}")

    def parse(text: str) -> List[Dict[str, Any]]:
        parsed = _parse_entries(text)
        if threshold:
            parsed = [e for e in parsed if _levelno(e["level"]) >= threshold]
        return parsed

    newest_first: List[Dict[str, Any]] = []
    budget = MAX_SCAN_BYTES
    for path in app_logger.log_files():
        if budget <= 0 or len(newest_first) >= limit:
            break
        try:
            parsed, used = _scan_tail(path, budget, limit - len(newest_first), parse)
        except OSError:
            continue
        budget -= max(used, 1)
        newest_first.extend(reversed(parsed))

    entries = list(reversed(newest_first[:limit]))
    for e in entries:
        e["message"] = redact_text(e["message"])
    path = app_logger.log_file_path()
    return {
        "enabled": path is not None,
        "file": path,
        "level": app_logger.current_level_name(),
        "min_level": min_level.upper() if min_level else None,
        "count": len(entries),
        "entries": entries,
    }


def _log_hosts() -> List[str]:
    """
    Log kuyruğunda maskelenecek makine adları: bu makinenin adı ve paketin seçtiği iş satırlarında kayıtlı
    adlar (işi başlatan süreç, hatada adı geçen kilit sahibi). İş geçmişi okunamıyorsa yalnızca bu makinenin adı.
    """
    names: List[Any] = [socket.gethostname()]
    try:
        _jobs(names)
    except Exception:
        pass
    return _host_names(names)


def log_tail_text(lines: int = DEFAULT_TAIL_LINES) -> str:
    """
    Log dosyasının son `lines` satırı düz metin olarak (maskelenmiş): gizli değerler `***`, ev dizini `~`,
    makine adları `***` olur (adlar: _log_hosts; kural: _mask_hosts). Bir LeaseHeld iletisi kilit sahibinin
    makinesini söyler; çağıran o hatayı ya da traceback'ini log'a yazdığında ad log dosyasına girer.
    """
    lines = max(0, min(MAX_TAIL_LINES, int(lines)))
    if lines == 0:
        return ""
    chunks: List[List[str]] = []
    have = 0
    budget = MAX_SCAN_BYTES
    for path in app_logger.log_files():
        if budget <= 0 or have >= lines:
            break
        try:
            part, used = _scan_tail(path, budget, lines - have, str.splitlines)
        except OSError:
            continue
        budget -= max(used, 1)
        chunks.append(part)
        have += len(part)
    all_lines = [line for part in reversed(chunks) for line in part][-lines:]
    if not all_lines:
        return ""
    # Sıra: önce gizli değerler ve ev dizini, sonra makine adı (kullanıcı adı makine adıyla aynıysa ev
    # dizini yine `~` olur)
    return _mask_hosts(_home_scrub(redact_text("\n".join(all_lines))), _log_hosts()) + "\n"


# --- paketin bölümleri --------------------------------------------------------------------

def _git_head() -> Dict[str, Optional[str]]:
    """Çalışan kodun commit'i; git çağrılmaz, .git dosyaları okunur (git deposu değilse boş)."""
    none: Dict[str, Optional[str]] = {"commit": None, "ref": None}
    git = _REPO_ROOT / ".git"
    try:
        if git.is_file():  # worktree: "gitdir: <yol>"
            pointer = git.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir:"):
                return none
            gitdir = Path(pointer[len("gitdir:"):].strip())
            if not gitdir.is_absolute():
                gitdir = _REPO_ROOT / gitdir
        elif git.is_dir():
            gitdir = git
        else:
            return none
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return {"commit": head[:12], "ref": None}
        ref = head[len("ref:"):].strip()
        common = gitdir
        commondir = gitdir / "commondir"
        if commondir.is_file():
            common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
        for base in (gitdir, common):
            if (base / ref).is_file():
                return {"commit": (base / ref).read_text(encoding="utf-8").strip()[:12], "ref": ref}
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return {"commit": line.split(" ", 1)[0][:12], "ref": ref}
        return {"commit": None, "ref": ref}
    except (OSError, UnicodeDecodeError):
        return none


def _packages() -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for name in _PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def _runtime() -> Dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "docker": os.path.exists("/.dockerenv"),
        "pid": os.getpid(),
        "utc_offset": time.strftime("%z"),
        "packages": _packages(),
    }


def _settings() -> Dict[str, Any]:
    """Etkin ayarlar (ortamdan). None: ayarlanmamış, uygulama varsayılanı kullanıyor."""
    values: Dict[str, Optional[str]] = {key: mask_value(key, os.environ.get(key)) for key in SETTING_KEYS}
    for key in sorted(os.environ):
        if key.startswith("SOFASCORE_") and key not in values:
            values[key] = mask_value(key, os.environ[key])  # SOFASCORE_SINKS: redact.mask_value'nin kuralı

    path = env_file_path()
    # Uygulamanın tanımadığı .env anahtarları: değerleri hiç yazılmaz, yalnızca adları
    other_set: List[str] = []
    other_empty: List[str] = []
    exists = os.path.isfile(path)
    if exists:
        for key, value in dotenv.dotenv_values(path).items():
            if key not in values:
                (other_set if value else other_empty).append(key)
    return {
        "values": values,
        "env_file": {
            "path": os.path.abspath(path),
            "exists": exists,
            "other_keys_set": sorted(other_set),
            "other_keys_empty": sorted(other_empty),
        },
    }


def _leagues() -> Dict[str, Any]:
    path = default_league_config_path()
    count = 0
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            count = sum(1 for line in f if line.strip() and not line.strip().startswith("#"))
    return {"config_file": os.path.abspath(path), "exists": os.path.isfile(path), "configured": count}


def _data_dir() -> Dict[str, Any]:
    path = os.path.abspath(os.getenv("DATA_DIR", "data"))
    info: Dict[str, Any] = {"path": path, "exists": os.path.isdir(path)}
    probe = path if info["exists"] else os.path.dirname(path)
    if os.path.isdir(probe):
        usage = shutil.disk_usage(probe)
        info["disk_free_mb"] = usage.free // (1024 * 1024)
    return info


def _logging() -> Dict[str, Any]:
    files = []
    for path in app_logger.log_files():
        st = os.stat(path)
        files.append({"name": os.path.basename(path), "bytes": st.st_size, "modified": _iso(st.st_mtime)})
    return {
        "level": app_logger.current_level_name(),
        "to_file": app_logger.log_to_file_enabled(),
        "file": app_logger.log_file_path(),
        "file_error": app_logger.log_file_error(),
        "dir": app_logger.log_dir(),
        "max_mb": round(app_logger.log_max_bytes() / (1024 * 1024), 3),
        "backup_count": app_logger.log_backup_count(),
        "files": files,
    }


def _cap_lists(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _cap_lists(v) for k, v in value.items()}
    if isinstance(value, list):
        capped = [_cap_lists(v) for v in value[:_JOB_LIST_CAP]]
        if len(value) > _JOB_LIST_CAP:
            capped.append(f"... {len(value) - _JOB_LIST_CAP} more")
        return capped
    return value


def _job_origin(raw: Any, this_host: str) -> Optional[Dict[str, Any]]:
    """
    `origin_json`'dan pakete girenler: işi başlatan yüz (cli, api, ...) ve pid (log satırlarındaki pid ile
    eşleştirmek için). Makine adı girmez; yerine yalnızca işin, paketi üreten makinede başlatılıp
    başlatılmadığı yazılır (`same_host`; ad kayıtlı değilse None). Alanlar adıyla alınır: kayda sonradan
    eklenen bir alan pakete kendiliğinden girmez.
    """
    if not isinstance(raw, dict):
        return None
    face, pid, host = raw.get("face"), raw.get("pid"), raw.get("host")
    return {
        "face": face if isinstance(face, str) else None,
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        "same_host": host == this_host if isinstance(host, str) and host else None,
    }


def _job_hosts(origin: Any, error: Any) -> List[Any]:
    """Bir iş satırında kayıtlı makine adları: işi başlatan süreç ve hatada adı geçen kilit sahibi."""
    hosts: List[Any] = []
    if isinstance(origin, dict):
        hosts.append(origin.get("host"))
    details = error.get("details") if isinstance(error, dict) else None
    holder = details.get("holder") if isinstance(details, dict) else None
    if isinstance(holder, dict):
        hosts.append(holder.get("host"))
    return hosts


def _jobs(found_hosts: Optional[List[Any]] = None) -> Dict[str, Any]:
    """
    Son işler, iş geçmişinden SALT OKUNUR okunur. JobStore kullanılmaz: kurulurken "running" işleri
    "interrupted" yapar; CLI'dan paket üretmek çalışan web sunucusunun işini bozmamalı.
    İş geçmişi state.db'dedir (sofascore_scraper/store/jobs.py); 3.x'in henüz açmadığı bir dizinde yalnızca 2.x'in
    jobs.db'si vardır.

    Yalnızca _JOB_COLUMNS'taki sütunlar okunur. Makine adı pakete girmez: işi başlatan sürecin adı atılır
    (_job_origin); satırlarda kayıtlı adlar ve bu makinenin adı, işin metinlerinde de (ör. kilidin sahibini
    söyleyen hata iletisi) `***` olur. `found_hosts` verilirse satırlarda kayıtlı adlar ona da eklenir
    (log kuyruğu aynı adları maskeler: _log_hosts).
    """
    meta = os.path.join(os.path.abspath(os.getenv("DATA_DIR", "data")), ".meta")
    db = os.path.join(meta, "state.db")
    if not os.path.isfile(db):
        db = os.path.join(meta, "jobs.db")
    if not os.path.isfile(db):
        return {"db": db, "exists": False, "recent": []}
    conn = sqlite3.connect(f"{Path(db).as_uri()}?mode=ro", uri=True, timeout=2.0)
    try:
        conn.row_factory = sqlite3.Row
        present = {str(row["name"]) for row in conn.execute("PRAGMA table_info(jobs)")}
        columns = [column for column in _JOB_COLUMNS if column in present]
        if not columns:
            raise LookupError("the job history has no jobs table with known columns")
        order = "COALESCE(started_at, '') DESC" if "started_at" in present else "rowid DESC"
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM jobs ORDER BY {order} LIMIT ?", (_JOB_LIMIT,)
        ).fetchall()
    finally:
        conn.close()
    this_host = socket.gethostname()
    hosts: List[Any] = [this_host]
    recent = []
    for row in rows:
        job = dict(row)
        for column in _JOB_JSON_COLUMNS:
            if column not in job:
                continue
            raw = job.pop(column)
            try:
                job[column[: -len("_json")]] = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                job[column[: -len("_json")]] = raw
        hosts.extend(_job_hosts(job.get("origin"), job.get("error")))
        if "origin" in job:
            job["origin"] = _job_origin(job["origin"], this_host)
        recent.append(_cap_lists(job))
    if found_hosts is not None:
        found_hosts.extend(hosts)
    return {"db": db, "exists": True, "recent": _mask_hosts(recent, _host_names(hosts))}


def _bridge() -> Dict[str, Any]:
    from sofascore_scraper import bridge_health

    return bridge_health.snapshot()


def _throttle() -> Dict[str, Any]:
    from sofascore_scraper import throttle

    return throttle.status()


def _doctor() -> Dict[str, Any]:
    """
    Kurulum denetimi (`ssc doctor` ile aynı denetimler, tarayıcıyı başlatan hariç).
    SofaScore'a istek atılmaz; hiçbir şey değiştirilmez.
    """
    from sofascore_scraper import doctor

    ctx = doctor.Context()
    # Ayrıştırılamayan .env satırının metni rapora girmez (satır numarası yeter): içinde, anahtarı
    # tanınmadığı için maskelenemeyen bir gizli değer olabilir
    ctx.env_bad_lines = [(number, MASK) for number, _text in ctx.env_bad_lines]
    out = doctor.report(doctor.run_checks(ctx, skip=_DOCTOR_SKIP), ctx)
    out["skipped"] = list(_DOCTOR_SKIP)
    for check in out["checks"]:
        # Sorunlu ayarın adı "setting" olarak yazılır: "key" adlı alan redact_obj'de gizli değer
        # sayılır (API_KEY gibi) ve `***` olurdu
        for problem in check["detail"].get("problems") or []:
            problem["setting"] = problem.pop("key", None)
        # Tarayıcı profilinin kilidi "<makine adı>-<pid>" biçimindedir (Chromium'un SingletonLock bağı):
        # denetimin sonucu ve pid kalır; ad (bu makinenin ya da kilidi bırakan kapsayıcının) `***` olur
        if check["detail"].get("lock_host"):
            hosts = _host_names([ctx.hostname, check["detail"]["lock_host"]])
            for field in ("summary", "fix", "detail"):
                check[field] = _mask_hosts(check[field], hosts)
    return out


def collect(source: str = "web") -> Dict[str, Any]:
    """
    Tanılama özeti (JSON'a hazır, maskelenmiş). `source`: paketi kim üretti ("web" / "cli").
    Köprü sağlığı süreç başınadır: "cli" paketinde çalışan web sunucusunun değil, paketi
    üreten sürecin (yeni başlamış) durumu görünür.
    """
    doc = {
        "generated_at": _iso(time.time()),
        "source": source,
        "app": {"name": "sofascore-scraper", "version": __version__, **_section(_git_head)},
        "runtime": _section(_runtime),
        "settings": _section(_settings),
        "leagues": _section(_leagues),
        "data_dir": _section(_data_dir),
        "logging": _section(_logging),
        "bridge": _section(_bridge),
        "throttle": _section(_throttle),
        "doctor": _section(_doctor),
        "jobs": _section(_jobs),
    }
    return _scrub_obj(redact_obj(doc))


# --- paket --------------------------------------------------------------------------------

def bundle_filename(now: Optional[dt.datetime] = None) -> str:
    return f"sofascore-diagnostics-{(now or dt.datetime.now()).strftime('%Y%m%d-%H%M%S')}.zip"


def build_bundle(source: str = "web", log_lines: int = DEFAULT_TAIL_LINES) -> bytes:
    """Tanılama paketini (zip) bellekte üretir."""
    summary = json.dumps(collect(source), ensure_ascii=False, indent=2, default=str)
    tail = _section(lambda: log_tail_text(log_lines))
    if not isinstance(tail, str):
        tail = f"(log could not be read: {tail.get('error')})\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.json", summary)
        zf.writestr("log_tail.txt", tail or "(no log lines: file logging is off or nothing was logged yet)\n")
        zf.writestr("README.txt", _BUNDLE_README)
    return buffer.getvalue()


def write_bundle(path: Optional[str] = None, source: str = "cli", log_lines: int = DEFAULT_TAIL_LINES) -> str:
    """
    Paketi diske yazar ve tam yolunu döndürür. `path` verilmezse log dizinine, var olan bir
    dizinse onun içine zaman damgalı adla yazılır.
    """
    if not path:
        path = os.path.join(app_logger.log_dir(), bundle_filename())
    elif os.path.isdir(path):
        path = os.path.join(path, bundle_filename())
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = build_bundle(source=source, log_lines=log_lines)
    with open(path, "wb") as f:
        f.write(data)
    return path


__all__ = [
    "DEFAULT_TAIL_LINES",
    "MAX_LOG_ENTRIES",
    "MAX_TAIL_LINES",
    "build_bundle",
    "bundle_filename",
    "collect",
    "log_tail_text",
    "read_log_entries",
    "write_bundle",
]
