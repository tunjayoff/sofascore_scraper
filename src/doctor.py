"""
Ortam ön denetimi ("doctor"): uygulamanın çalışması için gerekenler yerinde mi?

    python main.py --doctor            # okunur metin; hata varsa çıkış kodu 1
    python main.py --doctor --json     # aynı sonuç JSON olarak (otomasyon, başlatıcı)
    python -m src.doctor               # aynısı (başlatıcı sanal ortamın Python'u ile böyle çağırır)

Denetimler SofaScore'a bağlanmaz: Python sürümü, gerekli paketler, köprünün başlatacağı tarayıcı
(patchright'ın Chromium'u; yerel bir about:blank sayfasıyla denenir), tarayıcı profili, veri ve
yapılandırma dizinleri, web arayüzü derlemesi ve .env. Her denetim ok / warn / fail ile tek satırlık
bir çözüm döndürür. `--live` açıkça istenirse köprü üzerinden TEK gerçek istek atılır; varsayılan
olarak ve testlerde çalışmaz.

Bu modül yüklenirken yalnızca standart kütüphaneyi kullanır: eksik paket tam da bulması gereken
sorundur, o yüzden src.logger (rich) ya da dotenv'e bağımlı olamaz. Python 3.10'dan eski sürümlerde
de içe aktarılabilmelidir (sürüm denetimi hatayı kendisi bildirir).

Başka kod için: `run_checks()` sonuç listesini, `report()` JSON'a çevrilebilir sözlüğü verir.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from src.paths import DEFAULT_BROWSER_PROFILE_DIR  # yalnızca standart kütüphane

ROOT = Path(__file__).resolve().parents[1]

OK = "ok"
WARN = "warn"
FAIL = "fail"
_SEVERITY = {OK: 0, WARN: 1, FAIL: 2}

MIN_PYTHON = (3, 10)
SUPPORTED_LANGUAGES = ("tr", "en")
DEFAULT_LANGUAGE = "tr"  # src/i18n.py ile aynı varsayılan

# requirements.txt'teki her paket için içe aktarma adı; patchright ve playwright,
# scrapling[fetchers] ile gelir ve köprü onlarsız çalışmaz. tests/test_doctor.py bu listeyi
# requirements.txt ile karşılaştırır.
REQUIRED_MODULES: Tuple[Tuple[str, str], ...] = (
    ("curl_cffi", "curl_cffi"),
    ("scrapling", "scrapling"),
    ("patchright", "patchright"),
    ("playwright", "playwright"),
    ("pandas", "pandas"),
    ("colorama", "colorama"),
    ("tqdm", "tqdm"),
    ("rich", "rich"),
    ("dotenv", "python-dotenv"),
    ("fastapi", "fastapi"),
    ("pydantic", "pydantic"),
    ("uvicorn", "uvicorn"),
    ("sse_starlette", "sse-starlette"),
)

# Chromium'un (POSIX) profil kilidi: "SingletonLock -> <hostname>-<pid>" sembolik bağı
_PROFILE_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")

BROWSER_PROBE_TIMEOUT = 90.0
# Canlı denetimin tek isteği: küçük ve durağan bir uç nokta (challenge_solver._PROBE_URL ile aynı)
LIVE_PROBE_PATH = "/unique-tournament/17/seasons"

_ALLOWED_API_HOSTS = ("www.sofascore.com", "api.sofascore.com")
_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_RATE_OFF_WORDS = ("off", "false", "no", "none", "disabled")  # src/throttle.py ile aynı
_TRUTHY = ("1", "true", "yes")

# (anahtar, tam sayı mı, alt sınır, alt sınır dahil mi, üst sınır) — okuyan kodun kabul ettiği aralıklar
_NUMBER_RULES: Tuple[Tuple[str, bool, float, bool, Optional[float]], ...] = (
    ("REQUEST_TIMEOUT", True, 1, True, None),
    ("MAX_RETRIES", True, 0, True, None),
    ("MAX_CONCURRENT", True, 1, True, None),
    ("WAIT_TIME_MIN", False, 0, True, None),
    ("WAIT_TIME_MAX", False, 0, True, None),
    ("RATE_LIMIT_THRESHOLD_CONSECUTIVE", True, 1, True, None),
    ("RATE_LIMIT_THRESHOLD_RATIO", False, 0, False, 1),
    ("SERVER_ERROR_THRESHOLD_CONSECUTIVE", True, 1, True, None),
    ("REFRESH_WINDOW_HOURS", False, 0, True, None),
    ("REFRESH_MIN_INTERVAL_HOURS", False, 0, True, None),
    ("BRIDGE_DEGRADED_AFTER", False, 1, True, None),
    ("BRIDGE_BLOCKED_AFTER", False, 1, True, None),
    ("BRIDGE_BLOCKED_MIN_SECONDS", False, 0, True, None),
    ("WATCH_MAX_EVENT_POLLS", True, 1, True, None),
)
# Kod bunları `.lower() == "true"` ile okur: "1" ya da "yes" sessizce false olur
_BOOL_KEYS = ("USE_PROXY", "USE_COLOR", "FETCH_ONLY_FINISHED", "SAVE_EMPTY_ROUNDS")


# --- sonuç ve bağlam --------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Tek bir denetimin sonucu. `code` makineler için kararlı alt nedendir (ör. browser_missing)."""

    id: str
    status: str
    code: str
    label: str
    summary: str
    fix: Optional[str] = None
    # Kullanıcıya sormadan çalıştırılabilecek çözüm komutu (başlatıcı bunu kullanır); yoksa None
    fix_command: Optional[List[str]] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_messages(lang: str, locale_dir: Path) -> Dict[str, str]:
    try:
        with open(locale_dir / (lang + ".json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass
class Context:
    """Denetimlerin baktığı ortam. Testler sahte bir kök dizin ve ortam ile kurar."""

    root: Path = ROOT
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    lang: Optional[str] = None
    python: str = sys.executable
    platform: str = sys.platform
    version_info: Tuple[int, ...] = tuple(sys.version_info[:3])
    hostname: str = field(default_factory=socket.gethostname)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.env_file = self._resolve(self.environ.get("SOFASCORE_ENV_FILE") or ".env")
        self.file_env, self.env_bad_lines, self.env_error = read_env_file(self.env_file)
        if self.lang not in SUPPORTED_LANGUAGES:
            self.lang = self._app_language()
        self._messages = [
            _load_messages(code, ROOT / "locales")
            for code in dict.fromkeys((self.lang, "en", "tr"))  # seçilen dil, sonra yedekler
        ]

    def _resolve(self, path: str) -> Path:
        p = Path(os.path.expanduser(path))
        return p if p.is_absolute() else self.root / p

    def _app_language(self) -> str:
        # src/i18n.app_language ile aynı kural (LANGUAGE yalnızca desteklenen bir koda eşitse)
        for key in ("APP_LANGUAGE", "LANGUAGE"):
            value = self.get(key).lower()
            if value in SUPPORTED_LANGUAGES:
                return value
        return DEFAULT_LANGUAGE

    def get(self, key: str, default: str = "") -> str:
        """Geçerli değer: süreç ortamı .env'in önündedir (uygulama load_dotenv'i override'sız çağırır)."""
        value = self.environ.get(key)
        if value is None:
            value = self.file_env.get(key)
        return default if value is None else str(value).strip()

    def t(self, _message_key: str, **kwargs: Any) -> str:
        for messages in self._messages:
            text = messages.get(_message_key)
            if text is not None:
                try:
                    return text.format(**kwargs)
                except (KeyError, IndexError, ValueError):
                    return text
        return _message_key

    # Uygulamanın kullandığı yollar (göreli olanlar proje köküne göre: main.py oraya chdir eder)
    def data_dir(self) -> Path:
        return self._resolve(self.get("DATA_DIR") or "data")

    def config_dir(self) -> Path:
        return self._resolve(self.get("SOFASCORE_CONFIG_DIR") or "config")

    def profile_dir(self) -> Path:
        # src/paths.browser_profile_dir ile aynı kural (boş = varsayılan); burada .env de hesaba katılır
        return self._resolve(self.get("SOFASCORE_BROWSER_PROFILE") or DEFAULT_BROWSER_PROFILE_DIR)

    def frontend_index(self) -> Path:
        return self.root / "frontend" / "dist" / "index.html"


def read_env_file(path: Path) -> Tuple[Dict[str, str], List[Tuple[int, str]], Optional[str]]:
    """
    .env'i okur: (değerler, ayrıştırılamayan satırlar [(satır no, metin)], okuma hatası).

    python-dotenv kuruluysa onun ayrıştırıcısı kullanılır (uygulamanın gördüğü değerlerle aynı);
    değilse basit KEY=VALUE okuyucu — paketler eksikken de dizinler ve profil doğru bulunmalı.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return {}, [], None
    except (OSError, UnicodeDecodeError) as e:
        return {}, [], "{}: {}".format(e.__class__.__name__, e)

    values: Dict[str, str] = {}
    bad: List[Tuple[int, str]] = []
    try:
        import io

        from dotenv import dotenv_values
        from dotenv.parser import parse_stream
    except Exception:
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            key, sep, value = stripped.partition("=")
            if not sep or not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", key.strip()):
                bad.append((number, line.strip()[:60]))
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            else:
                value = value.split(" #", 1)[0].strip()
            values[key.strip()] = value
        return values, bad, None

    for binding in parse_stream(io.StringIO(text)):
        if binding.error:
            bad.append((binding.original.line, binding.original.string.strip()[:60]))
    for key, value in dotenv_values(stream=io.StringIO(text)).items():
        if value is not None:
            values[key] = value
    return values, bad, None


def _result(ctx: Context, check_id: str, status: str, code: str, summary: str, **extra: Any) -> CheckResult:
    return CheckResult(
        id=check_id, status=status, code=code, label=ctx.t("doctor_label_" + check_id), summary=summary, **extra
    )


def _command_text(argv: Sequence[str]) -> str:
    return " ".join('"{}"'.format(a) if " " in a else a for a in argv)


# --- Python ve paketler -----------------------------------------------------------------------


def check_python(ctx: Context) -> CheckResult:
    version = ".".join(str(n) for n in ctx.version_info[:3])
    minimum = ".".join(str(n) for n in MIN_PYTHON)
    if tuple(ctx.version_info[:2]) < MIN_PYTHON:
        return _result(
            ctx, "python", FAIL, "python_too_old",
            ctx.t("doctor_python_old", version=version, minimum=minimum),
            fix=ctx.t("doctor_python_fix", minimum=minimum),
            detail={"version": version, "minimum": minimum, "executable": ctx.python},
        )
    return _result(
        ctx, "python", OK, "python_ok", ctx.t("doctor_python_ok", version=version),
        detail={"version": version, "executable": ctx.python},
    )


def pip_install_command(ctx: Context) -> List[str]:
    return [ctx.python, "-m", "pip", "install", "-r", str(ctx.root / "requirements.txt")]


def _pinned_requirements(path: Path, _seen: Optional[set] = None) -> Dict[str, str]:
    """requirements.txt'teki (ve `-c` ile gösterdiği kısıt dosyalarındaki) `ad==sürüm` satırları."""
    seen = _seen if _seen is not None else set()
    pins: Dict[str, str] = {}
    if path in seen:
        return pins
    seen.add(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return pins
    for line in lines:
        line = line.split("#", 1)[0].strip()
        include = re.match(r"^(?:-c|--constraint|-r|--requirement)[ =]+(\S+)$", line)
        if include:
            pins.update(_pinned_requirements(path.parent / include.group(1), seen))
            continue
        pin = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([^\s;,]+)$", line)
        if pin:
            pins[pin.group(1).lower().replace("_", "-")] = pin.group(2)
    return pins


def _installed_version(dist: str) -> Optional[str]:
    try:
        from importlib import metadata

        return metadata.version(dist)
    except Exception:
        return None


def check_packages(
    ctx: Context,
    import_module: Callable[[str], Any] = importlib.import_module,
    installed_version: Callable[[str], Optional[str]] = _installed_version,
) -> CheckResult:
    missing: Dict[str, str] = {}
    for module, dist in REQUIRED_MODULES:
        try:
            import_module(module)
        except Exception as e:  # ImportError ve bozuk kurulumların attığı her şey
            missing[dist] = "{}: {}".format(e.__class__.__name__, e)
    command = pip_install_command(ctx)
    if missing:
        return _result(
            ctx, "packages", FAIL, "packages_missing",
            ctx.t("doctor_packages_missing", names=", ".join(sorted(missing))),
            fix=ctx.t("doctor_run_command", command=_command_text(command)),
            fix_command=command,
            detail={"missing": missing},
        )

    # Sabitlenmiş sürümler: challenge çözümü o sürümlerle doğrulandı
    drift = {}
    for dist, wanted in sorted(_pinned_requirements(ctx.root / "requirements.txt").items()):
        have = installed_version(dist)
        if have and have != wanted:
            drift[dist] = {"installed": have, "required": wanted}
    if drift:
        names = ", ".join("{} {} (requirements: {})".format(d, v["installed"], v["required"]) for d, v in drift.items())
        return _result(
            ctx, "packages", WARN, "packages_version_mismatch",
            ctx.t("doctor_packages_version", names=names),
            fix=ctx.t("doctor_run_command", command=_command_text(command)),
            detail={"mismatch": drift},
        )
    return _result(
        ctx, "packages", OK, "packages_ok", ctx.t("doctor_packages_ok", count=len(REQUIRED_MODULES)),
        detail={"count": len(REQUIRED_MODULES)},
    )


# --- tarayıcı ---------------------------------------------------------------------------------

# Ayrı bir süreçte çalışır (zaman aşımı uygulanabilsin, asyncio döngüsüyle çakışmasın). Köprünün
# yaptığının aynısını yapar: Scrapling'in StealthySession'ı (patchright, channel="chromium",
# headless, kalıcı profil) — ama geçici bir profille ve yalnızca about:blank açarak.
_PROBE_SCRIPT = r"""
import json, os, sys
out = {"stage": "import", "installed": False, "launched": False, "versions": {}}
try:
    from importlib import metadata
    for name in ("scrapling", "patchright", "playwright"):
        try:
            out["versions"][name] = metadata.version(name)
        except Exception:
            out["versions"][name] = None
    from patchright.sync_api import sync_playwright
    from scrapling.fetchers import StealthySession
    out["stage"] = "driver"
    with sync_playwright() as p:
        out["executable"] = p.chromium.executable_path
    out["installed"] = os.path.isfile(out["executable"])
    if out["installed"] and sys.argv[2] == "launch":
        out["stage"] = "launch"
        session = StealthySession(headless=True, user_data_dir=sys.argv[1])
        session.start()
        try:
            page = session.context.new_page()
            page.goto("about:blank")
            browser = session.context.browser
            out["browser_version"] = browser.version if browser else None
        finally:
            session.close()
        out["launched"] = True
        out["stage"] = "done"
except BaseException as e:
    out["error"] = "{}: {}".format(e.__class__.__name__, e)
print("\nDOCTOR_PROBE " + json.dumps(out))
"""


def browser_install_command(python: str = sys.executable) -> List[str]:
    """
    Köprünün kullandığı tarayıcıyı kuran komut. Scrapling, patchright ile channel="chromium"
    başlatır: patchright'ın kendi Chromium derlemesi gerekir (kurulu Google Chrome kullanılmaz;
    playwright'ın kurulum komutu yalnızca iki paketin sürümü denk geldiğinde aynı derlemeyi indirir).
    --no-shell: headless modda da tam Chromium çalışır, headless shell hiç kullanılmaz.
    """
    return [python, "-m", "patchright", "install", "chromium", "--no-shell"]


def probe_browser(python: str = sys.executable, launch: bool = True, timeout: float = BROWSER_PROBE_TIMEOUT) -> Dict[str, Any]:
    """Tarayıcı kurulu mu ve başlıyor mu? Ağ isteği yapmaz (yalnızca about:blank)."""
    profile = tempfile.mkdtemp(prefix="sofascore-doctor-")
    try:
        proc = subprocess.run(
            [python, "-c", _PROBE_SCRIPT, profile, "launch" if launch else "installed-only"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"stage": "launch", "installed": True, "launched": False, "timed_out": True, "timeout": timeout}
    except OSError as e:
        return {"stage": "import", "installed": False, "launched": False, "error": "{}: {}".format(e.__class__.__name__, e)}
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    stdout = proc.stdout.decode("utf-8", "replace")
    for line in reversed(stdout.splitlines()):
        if line.startswith("DOCTOR_PROBE "):
            try:
                return json.loads(line[len("DOCTOR_PROBE "):])
            except ValueError:
                break
    stderr = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return {
        "stage": "import",
        "installed": False,
        "launched": False,
        "error": "probe exited with code {}: {}".format(proc.returncode, stderr[-1] if stderr else "no output"),
    }


def _first_line(text: Any, limit: int = 200) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    return (lines[0] if lines else "")[:limit]


# Chromium'un başlatma hatasında asıl neden ilk satırda değil, tarayıcı günlüğünün içindedir
_LAUNCH_HINTS = ("error while loading shared libraries", "Host system is missing dependencies", "Missing X server")


def _launch_error(text: Any) -> str:
    for line in str(text or "").splitlines():
        if any(hint in line for hint in _LAUNCH_HINTS):
            return line.strip()[:200]
    return _first_line(text)


def check_browser(ctx: Context, probe: Optional[Callable[..., Dict[str, Any]]] = None) -> CheckResult:
    res = (probe or probe_browser)(ctx.python)
    install = browser_install_command(ctx.python)
    detail = {k: res.get(k) for k in ("executable", "versions", "browser_version", "stage") if res.get(k) is not None}
    if res.get("error"):
        detail["error"] = str(res["error"])[:2000]
    patchright = (res.get("versions") or {}).get("patchright") or "?"

    if res.get("launched"):
        return _result(
            ctx, "browser", OK, "browser_ok",
            ctx.t("doctor_browser_ok", version=res.get("browser_version") or "?", patchright=patchright),
            detail=detail,
        )
    if res.get("timed_out"):
        return _result(
            ctx, "browser", FAIL, "browser_launch_timeout",
            ctx.t("doctor_browser_timeout", seconds=int(res.get("timeout") or BROWSER_PROBE_TIMEOUT)),
            fix=ctx.t("doctor_browser_launch_fix", command=_command_text(install + ["--force"])),
            detail=detail,
        )
    if res.get("error") and res.get("stage") in ("import", "driver"):
        # patchright / scrapling yok ya da sürücüsü başlamıyor: asıl çözüm paketler denetiminde
        return _result(
            ctx, "browser", FAIL, "browser_driver_missing",
            ctx.t("doctor_browser_no_driver", error=_first_line(res.get("error"))),
            fix=ctx.t("doctor_run_command", command=_command_text(pip_install_command(ctx))),
            detail=detail,
        )
    if not res.get("installed") and res.get("executable"):
        return _result(
            ctx, "browser", FAIL, "browser_missing",
            ctx.t("doctor_browser_missing", path=res.get("executable"), patchright=patchright),
            fix=ctx.t("doctor_run_command", command=_command_text(install)),
            fix_command=install,
            detail=detail,
        )
    if ctx.platform.startswith("linux"):
        fix = ctx.t(
            "doctor_browser_launch_fix_linux",
            deps=_command_text(["sudo", ctx.python, "-m", "patchright", "install-deps", "chromium"]),
        )
    else:
        fix = ctx.t("doctor_browser_launch_fix", command=_command_text(install + ["--force"]))
    return _result(
        ctx, "browser", FAIL, "browser_launch_failed",
        ctx.t("doctor_browser_launch_failed", error=_launch_error(res.get("error")) or "?"),
        fix=fix,
        detail=detail,
    )


# --- dizinler ---------------------------------------------------------------------------------


def _dir_state(path: Path) -> Tuple[str, Optional[str]]:
    """
    ("ok" | "new" | "bad", neden). Dizin oluşturulmaz: yoksa, var olan en yakın üst dizine
    yazılabiliyor mu diye bakılır (uygulama dizini ilk kullanımda kendisi açar).
    """
    if path.is_dir():
        try:
            fd, tmp = tempfile.mkstemp(prefix=".doctor-", dir=str(path))
            os.close(fd)
            os.unlink(tmp)
            return "ok", None
        except OSError as e:
            return "bad", e.strerror or str(e)
    if path.exists() or path.is_symlink():
        return "bad", "not a directory"
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if parent.is_dir() and os.access(str(parent), os.W_OK | os.X_OK):
        return "new", None
    return "bad", "cannot be created in {}".format(parent)


def _check_dir(ctx: Context, check_id: str, path: Path, fix_key: str) -> CheckResult:
    state, reason = _dir_state(path)
    detail = {"path": str(path)}
    if state == "bad":
        return _result(
            ctx, check_id, FAIL, check_id + "_not_writable",
            ctx.t("doctor_dir_unwritable", path=path, error=reason),
            fix=ctx.t(fix_key),
            detail=detail,
        )
    key = "doctor_dir_ok" if state == "ok" else "doctor_dir_new"
    return _result(ctx, check_id, OK, check_id + "_ok", ctx.t(key, path=path), detail=detail)


def check_data_dir(ctx: Context) -> CheckResult:
    return _check_dir(ctx, "data_dir", ctx.data_dir(), "doctor_data_dir_fix")


def check_config_dir(ctx: Context) -> CheckResult:
    return _check_dir(ctx, "config_dir", ctx.config_dir(), "doctor_config_dir_fix")


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # Windows'ta os.kill(pid, 0) yoklamaz, süreci SONLANDIRIR. Orada profil kilidi sembolik bağ
        # olmadığı için buraya gelinmez; yine de asla denenmez (bilinmiyor = çalışıyor say).
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_command(pid: int) -> Optional[str]:
    """Sürecin komut satırı (Linux: /proc, diğer POSIX: ps); okunamazsa None."""
    try:
        with open("/proc/{}/cmdline".format(pid), "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
        )
        return out.stdout.decode("utf-8", "replace").strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def check_profile(
    ctx: Context,
    pid_alive: Callable[[int], bool] = _pid_alive,
    pid_command: Callable[[int], Optional[str]] = _pid_command,
) -> CheckResult:
    path = ctx.profile_dir()
    state, reason = _dir_state(path)
    detail: Dict[str, Any] = {"path": str(path)}
    if state == "bad":
        return _result(
            ctx, "profile", FAIL, "profile_not_writable",
            ctx.t("doctor_dir_unwritable", path=path, error=reason),
            fix=ctx.t("doctor_profile_unwritable_fix"),
            detail=detail,
        )
    if state == "new":
        return _result(ctx, "profile", OK, "profile_new", ctx.t("doctor_dir_new", path=path), detail=detail)

    # Kilit: Windows'ta süreçle birlikte kapanan bir dosya tutamacıdır; sembolik bağ yalnızca POSIX'te
    lock = path / "SingletonLock"
    try:
        holder = os.readlink(str(lock))
    except (OSError, AttributeError, NotImplementedError):
        return _result(ctx, "profile", OK, "profile_ok", ctx.t("doctor_dir_ok", path=path), detail=detail)

    host, _, pid_text = holder.rpartition("-")
    files = ", ".join(str(path / name) for name in _PROFILE_LOCK_FILES)
    detail.update({"lock": holder, "lock_host": host})
    if not host or not pid_text.isdigit() or host != ctx.hostname:
        # Başka makine/kapsayıcının adı: Chromium bu profille başlamayı reddeder (çıkış kodu 21)
        return _result(
            ctx, "profile", FAIL, "profile_locked_other_host",
            ctx.t("doctor_profile_other_host", path=path, holder=holder),
            fix=ctx.t("doctor_profile_lock_fix", files=files),
            detail=detail,
        )
    pid = int(pid_text)
    detail["lock_pid"] = pid
    alive = pid_alive(pid)
    command = pid_command(pid) if alive else None
    # Canlı ve gerçekten bu profili kullanan bir tarayıcı mı? (pid başka bir sürece geçmiş olabilir;
    # komut satırı okunamıyorsa kullanımda sayılır)
    if alive and (command is None or str(path) in command):
        return _result(
            ctx, "profile", WARN, "profile_in_use",
            ctx.t("doctor_profile_in_use", path=path, pid=pid),
            fix=ctx.t("doctor_profile_in_use_fix", pid=pid),
            detail=detail,
        )
    return _result(
        ctx, "profile", WARN, "profile_stale_lock",
        ctx.t("doctor_profile_stale", path=path, pid=pid),
        fix=ctx.t("doctor_profile_stale_fix", files=files),
        detail=detail,
    )


# --- web arayüzü ------------------------------------------------------------------------------


def installed_node_version() -> Optional[Tuple[int, ...]]:
    """Kurulu Node.js sürümü; node ya da npm yoksa None."""
    node = shutil.which("node")
    if not node or not shutil.which("npm"):
        return None
    try:
        out = subprocess.run(
            [node, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", out.stdout.decode("ascii", "replace").strip())
    return tuple(int(n) for n in match.groups()) if match else None


def node_is_supported(version: Optional[Sequence[int]]) -> bool:
    """Vite'ın istediği: Node.js 20.19+ ya da 22.12+ (21.x ve 22.0–22.11 desteklenmez)."""
    if not version:
        return False
    major, minor = version[0], version[1]
    if major == 20:
        return minor >= 19
    if major == 22:
        return minor >= 12
    return major > 22


def check_frontend(ctx: Context, node_version: Callable[[], Optional[Tuple[int, ...]]] = installed_node_version) -> CheckResult:
    index = ctx.frontend_index()
    if index.is_file():
        return _result(
            ctx, "frontend", OK, "frontend_ok", ctx.t("doctor_frontend_ok", path=index.parent),
            detail={"path": str(index.parent)},
        )
    version = node_version()
    supported = node_is_supported(version)
    return _result(
        ctx, "frontend", WARN, "frontend_missing",
        ctx.t("doctor_frontend_missing", path=index.parent),
        fix=ctx.t("doctor_frontend_fix" if supported else "doctor_frontend_fix_node"),
        detail={
            "path": str(index.parent),
            "node": ".".join(str(n) for n in version) if version else None,
            "node_supported": supported,
        },
    )


# --- .env -------------------------------------------------------------------------------------


def _number_problem(ctx: Context, key: str, raw: str, integer: bool, low: float, inclusive: bool, high: Optional[float]) -> Optional[str]:
    try:
        value = int(raw) if integer else float(raw)
        valid = value == value and abs(value) != float("inf")  # nan / inf
    except ValueError:
        valid = False
    if valid:
        valid = (value >= low if inclusive else value > low) and (high is None or value <= high)
    if valid:
        return None
    bounds = "{} {:g}".format("≥" if inclusive else ">", low)
    if high is not None:
        bounds += ", ≤ {:g}".format(high)
    return ctx.t("doctor_env_not_int" if integer else "doctor_env_not_number", key=key, value=raw, range=bounds)


def _env_problems(ctx: Context) -> List[Tuple[str, str, str]]:
    """(status, anahtar, ileti) listesi: uygulamanın gerçekten göreceği değerler denetlenir."""
    problems: List[Tuple[str, str, str]] = []

    for number, text in ctx.env_bad_lines:
        problems.append((FAIL, "line {}".format(number), ctx.t("doctor_env_bad_line", line=number, text=text)))

    for key, integer, low, inclusive, high in _NUMBER_RULES:
        raw = ctx.get(key)
        if raw:  # boş = ayarlanmamış: varsayılan kullanılır
            message = _number_problem(ctx, key, raw, integer, low, inclusive, high)
            if message:
                problems.append((FAIL, key, message))

    for key in _BOOL_KEYS:
        raw = ctx.get(key)
        if raw and raw.lower() not in ("true", "false"):
            problems.append((FAIL, key, ctx.t("doctor_env_not_bool", key=key, value=raw)))

    rate = ctx.get("REQUEST_RATE_LIMIT").lower()
    if rate and rate not in _RATE_OFF_WORDS:
        try:
            ok = abs(float(rate)) != float("inf") and float(rate) == float(rate)
        except ValueError:
            ok = False
        if not ok:
            problems.append((FAIL, "REQUEST_RATE_LIMIT", ctx.t("doctor_env_rate", value=rate)))

    # Proxy: değer hiçbir zaman yazdırılmaz (kimlik bilgisi içerebilir)
    use_proxy = ctx.get("USE_PROXY").lower() == "true"
    proxy = ctx.get("PROXY_URL")
    if use_proxy and not proxy:
        problems.append((FAIL, "PROXY_URL", ctx.t("doctor_env_proxy_missing")))
    elif proxy:
        try:
            parsed = urlparse(proxy)
            proxy_ok = parsed.scheme in _PROXY_SCHEMES and bool(parsed.hostname)
        except ValueError:
            proxy_ok = False
        if not proxy_ok:
            problems.append((FAIL if use_proxy else WARN, "PROXY_URL", ctx.t("doctor_env_proxy_invalid")))

    api = ctx.get("API_BASE_URL")
    if api:
        try:
            parsed = urlparse(api)
            api_ok = parsed.scheme == "https" and parsed.hostname in _ALLOWED_API_HOSTS
        except ValueError:
            api_ok = False
        if not api_ok:
            problems.append((WARN, "API_BASE_URL", ctx.t("doctor_env_api_url", value=api)))

    language = ctx.get("APP_LANGUAGE")
    if language and language.lower() not in SUPPORTED_LANGUAGES:
        problems.append((WARN, "APP_LANGUAGE", ctx.t("doctor_env_language", value=language)))

    level = ctx.get("LOG_LEVEL")
    if level and level.upper() not in _LOG_LEVELS:
        problems.append((WARN, "LOG_LEVEL", ctx.t("doctor_env_log_level", value=level)))

    if ctx.get("SOFASCORE_CHROME_PROFILE"):
        problems.append((WARN, "SOFASCORE_CHROME_PROFILE", ctx.t("doctor_env_legacy_profile")))

    headed = ctx.get("SOFASCORE_BROWSER_HEADED").lower() in _TRUTHY
    if headed and ctx.platform.startswith("linux") and not (ctx.get("DISPLAY") or ctx.get("WAYLAND_DISPLAY")):
        problems.append((WARN, "SOFASCORE_BROWSER_HEADED", ctx.t("doctor_env_headed")))

    if ctx.env_file.is_file() and not os.access(str(ctx.env_file), os.W_OK):
        problems.append((WARN, ".env", ctx.t("doctor_env_readonly", path=ctx.env_file)))

    return problems


def check_env(ctx: Context) -> CheckResult:
    path = ctx.env_file
    detail: Dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if ctx.env_error:
        return _result(
            ctx, "env", FAIL, "env_unreadable",
            ctx.t("doctor_env_unreadable", path=path, error=ctx.env_error),
            fix=ctx.t("doctor_env_fix", path=path),
            detail=detail,
        )
    problems = _env_problems(ctx)
    if problems:
        status = max((p[0] for p in problems), key=_SEVERITY.__getitem__)
        detail["problems"] = [{"status": s, "key": k, "message": m} for s, k, m in problems]
        if len(problems) == 1:
            summary = problems[0][2]
        else:
            summary = ctx.t("doctor_env_problems", count=len(problems), keys=", ".join(p[1] for p in problems))
        return _result(
            ctx, "env", status, "env_invalid", summary, fix=ctx.t("doctor_env_fix", path=path), detail=detail
        )
    if not path.is_file():
        return _result(ctx, "env", OK, "env_missing", ctx.t("doctor_env_missing", path=path), detail=detail)
    return _result(ctx, "env", OK, "env_ok", ctx.t("doctor_env_ok", path=path, count=len(ctx.file_env)), detail=detail)


# --- canlı denetim (yalnızca açıkça istenirse) ------------------------------------------------


def check_live(ctx: Context, fetch: Optional[Callable[[str], Any]] = None) -> CheckResult:
    """
    Köprü üzerinden SofaScore'a TEK gerçek istek. Yalnızca `--live` / `run_checks(live=True)` ile
    çalışır; ortak istek bütçesine (src/throttle.py) uyar. Profil başka bir süreçte açıksa
    (çalışan web uygulaması) tarayıcı başlayamaz ve denetim başarısız olur.
    """
    if fetch is None:
        from src.challenge_solver import fetch_api_via_browser_sync as fetch

    try:
        data = fetch(LIVE_PROBE_PATH)
        error = None
    except Exception as e:
        data, error = None, "{}: {}".format(e.__class__.__name__, e)
    if data:
        return _result(ctx, "live", OK, "live_ok", ctx.t("doctor_live_ok"), detail={"path": LIVE_PROBE_PATH})
    detail: Dict[str, Any] = {"path": LIVE_PROBE_PATH}
    if error is None:
        try:
            from src import bridge_health

            last = (bridge_health.snapshot() or {}).get("last_error") or {}
            detail["bridge"] = last
            error = "{}: {}".format(last.get("kind"), last.get("detail")) if last else None
        except Exception:
            pass
    return _result(
        ctx, "live", FAIL, "live_failed",
        ctx.t("doctor_live_failed", error=_first_line(error) or "?"),
        fix=ctx.t("doctor_live_fix"),
        detail=detail,
    )


# --- çalıştırma ve çıktı ----------------------------------------------------------------------

CHECKS: Tuple[Tuple[str, Callable[[Context], CheckResult]], ...] = (
    ("python", check_python),
    ("packages", check_packages),
    ("browser", check_browser),
    ("profile", check_profile),
    ("data_dir", check_data_dir),
    ("config_dir", check_config_dir),
    ("frontend", check_frontend),
    ("env", check_env),
)
CHECK_IDS = tuple(check_id for check_id, _ in CHECKS)


def run_checks(
    ctx: Optional[Context] = None,
    only: Optional[Iterable[str]] = None,
    skip: Optional[Iterable[str]] = None,
    live: bool = False,
    browser_probe: Optional[Callable[..., Dict[str, Any]]] = None,
) -> List[CheckResult]:
    """Denetimleri sırayla çalıştırır. Bir denetimin çökmesi diğerlerini durdurmaz."""
    ctx = ctx or Context()
    wanted = set(only) if only else None
    skipped = set(skip or ())
    checks: List[Tuple[str, Callable[[Context], CheckResult]]] = list(CHECKS)
    if browser_probe is not None:
        checks = [
            (cid, (lambda c: check_browser(c, probe=browser_probe)) if cid == "browser" else fn) for cid, fn in checks
        ]
    if live:
        checks.append(("live", check_live))

    results: List[CheckResult] = []
    for check_id, fn in checks:
        if check_id in skipped or (wanted is not None and check_id not in wanted):
            continue
        try:
            results.append(fn(ctx))
        except Exception as e:
            results.append(
                _result(
                    ctx, check_id, FAIL, "check_crashed",
                    ctx.t("doctor_check_crashed", error="{}: {}".format(e.__class__.__name__, e)),
                    fix=ctx.t("doctor_check_crashed_fix"),
                )
            )
    return results


def overall_status(results: Sequence[CheckResult]) -> str:
    return max((r.status for r in results), key=_SEVERITY.__getitem__, default=OK)


def report(results: Sequence[CheckResult], ctx: Optional[Context] = None) -> Dict[str, Any]:
    """JSON'a çevrilebilir özet: `status` en kötü sonuç, `ok` hata (fail) yoksa True."""
    counts = {status: sum(1 for r in results if r.status == status) for status in (OK, WARN, FAIL)}
    status = overall_status(results)
    out: Dict[str, Any] = {
        "status": status,
        "ok": status != FAIL,
        "counts": counts,
        "checks": [r.to_dict() for r in results],
    }
    if ctx is not None:
        out["root"] = str(ctx.root)
        out["language"] = ctx.lang
    return out


_TAGS = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


def render_text(results: Sequence[CheckResult], ctx: Context) -> str:
    lines = [ctx.t("doctor_title"), ""]
    for r in results:
        lines.append("{} {}: {}".format(_TAGS[r.status], r.label, r.summary))
        problems = r.detail.get("problems") or []
        if len(problems) > 1:
            lines.extend("       - {}".format(p["message"]) for p in problems)
        if r.fix and r.status != OK:
            lines.append("       {}: {}".format(ctx.t("doctor_fix_label"), r.fix))
    counts = report(results)["counts"]
    lines.append("")
    if counts[FAIL] or counts[WARN]:
        lines.append(ctx.t("doctor_summary", fail=counts[FAIL], warn=counts[WARN], ok=counts[OK]))
    else:
        lines.append(ctx.t("doctor_summary_ok"))
    return "\n".join(lines)


def exit_code(results: Sequence[CheckResult], strict: bool = False) -> int:
    status = overall_status(results)
    return 1 if status == FAIL or (strict and status == WARN) else 0


def _split_ids(raw: Optional[str]) -> Optional[List[str]]:
    return [part.strip() for part in raw.split(",") if part.strip()] if raw else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Dil, yardım metni kurulmadan önce bilinmeli
    lang = None
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            lang = argv[i + 1]
        elif arg.startswith("--lang="):
            lang = arg.split("=", 1)[1]
    ctx = Context(lang=lang)

    parser = argparse.ArgumentParser(prog="python main.py --doctor", description=ctx.t("doctor_cli_description"))
    parser.add_argument("--json", action="store_true", help=ctx.t("doctor_cli_json"))
    parser.add_argument("--strict", action="store_true", help=ctx.t("doctor_cli_strict"))
    parser.add_argument("--live", action="store_true", help=ctx.t("doctor_cli_live"))
    parser.add_argument("--only", metavar="IDS", help=ctx.t("doctor_cli_only", ids=", ".join(CHECK_IDS)))
    parser.add_argument("--skip", metavar="IDS", help=ctx.t("doctor_cli_skip"))
    parser.add_argument("--lang", choices=SUPPORTED_LANGUAGES, help=ctx.t("doctor_cli_lang"))
    args = parser.parse_args(argv)

    unknown = [i for i in (_split_ids(args.only) or []) + (_split_ids(args.skip) or []) if i not in CHECK_IDS + ("live",)]
    if unknown:
        parser.error("unknown check id: {} (valid: {})".format(", ".join(unknown), ", ".join(CHECK_IDS)))

    if args.live:
        # Canlı istek uygulamanın ayarlarıyla (proxy, profil, istek bütçesi) atılır: .env yüklenir
        try:
            import dotenv

            dotenv.load_dotenv(str(ctx.env_file))
        except Exception:
            pass

    # Denetimler sırasında stdout'a yazılan her şey (ör. --live'da uygulamanın log satırları)
    # stderr'e gider: stdout'ta yalnızca rapor kalır, JSON çıktısı ayrıştırılabilir olur
    with contextlib.redirect_stdout(sys.stderr):
        results = run_checks(ctx, only=_split_ids(args.only), skip=_split_ids(args.skip), live=args.live)

    if args.json:
        # ensure_ascii: konsolun kod sayfası ne olursa olsun ayrıştırılabilir çıktı
        print(json.dumps(report(results, ctx), ensure_ascii=True, indent=2))
    else:
        try:
            sys.stdout.reconfigure(errors="replace")  # cp1252 gibi konsollarda Türkçe karakter çökmesin
        except (AttributeError, ValueError):
            pass
        print(render_text(results, ctx))
    return exit_code(results, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
