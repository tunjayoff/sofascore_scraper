"""
Loglama: konsol + dönen (rotating) log dosyası.

Konsol: terminalde Rich ile renkli; çıktı terminal değilse (Docker, cron, yönlendirme) dosyadakiyle
aynı düz biçimde. Kapsayıcıda birincil çıktı stdout'tur ve her zaman açıktır.

Konsol akışı seçilebilir (`set_console_stream`): varsayılan stdout'tur (web sunucusu, terminal menüsü); yeni
CLI (src/cli; `python main.py`nin bayrakları da ona çevrilir) stderr'i seçer, çünkü orada stdout yalnızca
komutun sonucunu taşır (docs/design/02-services.md 4.4).

Konsol satırının biçimi seçilebilir (`set_log_format`; `[log] format`, `--log-format`): "text" (varsayılan) ya
da "json", satır başına bir JSON nesnesi (`JsonFormatter`: time, level, logger, pid, message, varsa exc). JSON
biçimi yalnızca konsolu etkiler: dosya her zaman düz metindir, çünkü tanılama paketi (src/diagnostics.py)
satırlarını LINE_RE ile ayrıştırır.

Dosya: LOG_DIR (varsayılan: proje kökündeki logs/) altında `sofascore_scraper.log`. Boyutu
LOG_MAX_MB'ı (varsayılan 5) geçince çevrilir, en fazla LOG_BACKUP_COUNT (varsayılan 5) eski dosya
tutulur. LOG_TO_FILE=false dosyayı kapatır. Dizin yazılamıyorsa uygulama dosyasız devam eder.
Web sunucusu, CLI ve --watch aynı dosyaya yazabilir; satırda süreç numarası bulunur.

Dosyaya ve konsola yazılan her satır src.redact'ten geçer: token, cookie ve proxy parolası
loga düşmez. Rich markup kapalıdır: mesajdaki "[...]" olduğu gibi yazılır.

LOG_LEVEL / DEBUG çalışırken değişebilir: apply_log_level() (ayarlar kaydedilince çağrılır).
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import logging
import os
import re
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

import dotenv
from rich.console import Console
from rich.logging import RichHandler

from src.config_files import file_lock
from src.paths import env_file_path
from src.redact import redact_text

LOG_FILE_NAME = "sofascore_scraper.log"
DEFAULT_MAX_MB = 5.0
DEFAULT_BACKUP_COUNT = 5
# Çevirme başarısız olursa (Windows: dosya başka süreçte açık) bu kadar saniye yeniden denenmez
_ROLLOVER_RETRY_SECONDS = 60.0

# Dosyadaki (ve terminal olmayan konsoldaki) satır biçimi. src/diagnostics.py aynı biçimi ayrıştırır.
FILE_FORMAT = "%(asctime)s %(levelname)-8s [%(process)d] %(name)s: %(message)s"
LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (?P<level>[A-Z]+)\s+\[(?P<pid>\d+)\] (?P<logger>[^:\s]*): (?P<message>.*)$"
)
LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TRUTHY = ("true", "1", "yes", "t", "y", "on")
_FALSY = ("false", "0", "no", "f", "n", "off")

# Konsol log satırlarının yazıldığı akış (set_console_stream)
CONSOLE_STREAMS = ("stdout", "stderr")
_console_stream = "stdout"
# Konsol log satırlarının biçimi (set_log_format)
LOG_FORMATS = ("text", "json")
_log_format = "text"

# Flag to track if logging has been configured
_configured = False
_console_handler: Optional[logging.Handler] = None
_file_handler: Optional["SharedRotatingFileHandler"] = None
_file_error: Optional[str] = None
# attach_file_handler ile dosya handler'ı eklenen (kök dışı) logger adları
_attached: List[str] = []


# --- ayarlar ------------------------------------------------------------------------------

def log_dir() -> str:
    """Log dizini: LOG_DIR; göreli yol proje köküne göre çözülür (çalışma dizininden bağımsız)."""
    raw = os.getenv("LOG_DIR", "").strip()
    path = Path(raw).expanduser() if raw else Path("logs")
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return str(path)


def log_to_file_enabled() -> bool:
    return os.getenv("LOG_TO_FILE", "true").strip().lower() not in _FALSY


def _env_number(key: str, default: float, minimum: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def log_max_bytes() -> int:
    return int(_env_number("LOG_MAX_MB", DEFAULT_MAX_MB, 0.001) * 1024 * 1024)


def log_backup_count() -> int:
    # En az 1: eski dosya tutulmazsa RotatingFileHandler hiç çevirmez ve dosya sınırsız büyür
    return max(1, int(_env_number("LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT, 0)))


def resolve_level() -> int:
    """LOG_LEVEL ve DEBUG ortam değişkenlerinden geçerli seviye (geçersizse INFO)."""
    name = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    # Override if DEBUG env variable is truthy
    if os.getenv("DEBUG", "").strip().lower() in _TRUTHY:
        name = "DEBUG"
    if name not in LEVEL_NAMES:
        print(f"Warning: invalid LOG_LEVEL '{name}'; using INFO.", file=sys.stderr)
        return logging.INFO
    return getattr(logging, name)


# --- biçimlendirme ve maskeleme -----------------------------------------------------------

class RedactingFormatter(logging.Formatter):
    """Düz metin; biçimlenen satırın tamamı (traceback dahil) maskelenir."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _redacted_copy(record: logging.LogRecord) -> logging.LogRecord:
    """
    Rich konsolu için kaydın maskelenmiş kopyası (asıl kayıt diğer handler'lar için değişmez).
    Rich traceback'i istisna nesnesinden kendisi çizer; traceback'te gizli bir değer varsa
    düz metin olarak maskelenip mesaja eklenir.
    """
    clone = copy.copy(record)
    message = redact_text(record.getMessage())
    exc_info = record.exc_info
    if exc_info and exc_info[0] is not None:
        plain = "".join(traceback.format_exception(*exc_info))
        redacted = redact_text(plain)
        if redacted != plain:
            message = f"{message}\n{redacted.rstrip()}"
            clone.exc_info = None
            clone.exc_text = None
    clone.msg = message
    clone.args = None
    return clone


class JsonFormatter(logging.Formatter):
    """
    Satır başına bir JSON nesnesi (`--log-format json`): makinece okunur log. Alanlar: `time` (ISO-8601 UTC,
    milisaniyeli, `Z`), `level`, `logger`, `pid`, `message` ve varsa `exc` (iz dökümü). Metinler maskelenir.
    """

    def format(self, record: logging.LogRecord) -> str:
        stamp = _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc)
        line = {
            "time": stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "pid": record.process,
            "message": redact_text(record.getMessage()),
        }
        if record.exc_info and record.exc_info[0] is not None:
            line["exc"] = redact_text("".join(traceback.format_exception(*record.exc_info)).rstrip())
        return json.dumps(line, ensure_ascii=True, separators=(",", ":"))


class RedactingRichHandler(RichHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(_redacted_copy(record))


# --- dosya --------------------------------------------------------------------------------

class SharedRotatingFileHandler(RotatingFileHandler):
    """
    Birden çok sürecin (web sunucusu, CLI, --watch) aynı dosyaya yazabildiği RotatingFileHandler.

    Standart sınıf tek süreç varsayar: dosyayı bir süreç çevirince diğeri yeniden adlandırılmış
    eski dosyaya yazmaya devam eder. Burada her yazmadan önce yoldaki dosyanın hâlâ açık olan
    dosya olup olmadığına bakılır; çevirme süreçler arası kilit altında yapılır.
    """

    _warned = False
    _rollover_blocked_until = 0.0

    def _same_file(self) -> bool:
        try:
            on_disk = os.stat(self.baseFilename)
            opened = os.fstat(self.stream.fileno())
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return True  # bilinmiyor: dokunma
        return (on_disk.st_ino, on_disk.st_dev) == (opened.st_ino, opened.st_dev)

    def _reopen(self) -> None:
        if self.stream is not None:
            self.stream.close()
        self.stream = self._open()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.stream is not None and not self._same_file():
                self._reopen()  # başka bir süreç dosyayı çevirmiş (ya da silinmiş)
        except Exception:
            self.handleError(record)
            return
        super().emit(record)

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if time.monotonic() < self._rollover_blocked_until:
            return False
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:
        base = self.baseFilename
        try:
            with file_lock(base):
                if self.stream is not None and not self._same_file():
                    self._reopen()  # kilidi beklerken başka süreç çevirdi
                    return
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                # Önce etkin dosya kenara alınır: bu adım başarısız olursa (Windows'ta dosya başka
                # süreçte açıkken yeniden adlandırılamaz) eski dosyalara hiç dokunulmamış olur.
                staging = f"{base}.rotating"
                os.replace(base, staging)
                for i in range(self.backupCount - 1, 0, -1):
                    if os.path.exists(f"{base}.{i}"):
                        os.replace(f"{base}.{i}", f"{base}.{i + 1}")
                os.replace(staging, f"{base}.1")
        except OSError:
            # Çeviremedik: aynı dosyaya eklemeye devam, bir süre sonra yeniden denenir
            self._rollover_blocked_until = time.monotonic() + _ROLLOVER_RETRY_SECONDS
        # Akış kapalı kaldıysa bir sonraki yazma dosyayı yeniden açar (FileHandler.emit)

    def handleError(self, record: logging.LogRecord) -> None:
        # Disk dolu / izin yok: her kayıt için traceback basmak yerine bir kez söyle
        if not SharedRotatingFileHandler._warned:
            SharedRotatingFileHandler._warned = True
            err = sys.exc_info()[1]
            print(f"Warning: cannot write to the log file ({self.baseFilename}): {err}", file=sys.stderr)


def build_file_handler(
    path: str, max_bytes: Optional[int] = None, backup_count: Optional[int] = None
) -> SharedRotatingFileHandler:
    """Dönen dosya handler'ı (dizini oluşturur; dosya ilk kayıtta açılır)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    handler = SharedRotatingFileHandler(
        path,
        maxBytes=log_max_bytes() if max_bytes is None else max_bytes,
        backupCount=log_backup_count() if backup_count is None else max(1, backup_count),
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(RedactingFormatter(FILE_FORMAT))
    return handler


def console_stream() -> str:
    """Konsol log satırlarının yazıldığı akışın adı: "stdout" ya da "stderr"."""
    return _console_stream


def set_console_stream(name: str) -> None:
    """
    Konsol log satırlarının akışını seçer: "stdout" (varsayılan) ya da "stderr". Loglama kurulmuşsa konsol
    handler'ı yeni akışla yeniden kurulur; dosya logu ve seviye aynı kalır. Seçim süreç boyunca geçerlidir:
    sonraki `setup_logger(force=True)` çağrıları da onu kullanır.
    """
    global _console_stream
    if name not in CONSOLE_STREAMS:
        raise ValueError(f"unknown console stream: {name!r} (expected one of {', '.join(CONSOLE_STREAMS)})")
    if name == _console_stream:
        return
    _console_stream = name
    if _configured:
        setup_logger(force=True)


def log_format() -> str:
    """Konsol log satırlarının biçimi: "text" ya da "json"."""
    return _log_format


def set_log_format(name: str) -> None:
    """
    Konsol log satırlarının biçimini seçer: "text" (varsayılan) ya da "json". Loglama kurulmuşsa konsol
    handler'ı yeniden kurulur; dosya düz metin kalır. Seçim süreç boyunca geçerlidir.
    """
    global _log_format
    if name not in LOG_FORMATS:
        raise ValueError(f"unknown log format: {name!r} (expected one of {', '.join(LOG_FORMATS)})")
    if name == _log_format:
        return
    _log_format = name
    if _configured:
        setup_logger(force=True)


def _build_console_handler() -> logging.Handler:
    # USE_COLOR kontrolü
    if os.getenv("USE_COLOR", "true").strip().lower() != "true":
        os.environ["NO_COLOR"] = "1"
    to_stderr = _console_stream == "stderr"
    stream = sys.stderr if to_stderr else sys.stdout
    if _log_format == "json":
        # Makinece okunur satırlar terminalde de aynıdır: renk ve Rich biçimi yok
        as_json = logging.StreamHandler(stream)
        as_json.setFormatter(JsonFormatter())
        return as_json
    try:
        interactive = stream.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if interactive:
        # markup=False: mesajdaki "[...]" Rich etiketi olarak yorumlanmaz
        handler: logging.Handler = (
            RedactingRichHandler(rich_tracebacks=True, markup=False, console=Console(stderr=True))
            if to_stderr
            else RedactingRichHandler(rich_tracebacks=True, markup=False)
        )
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        return handler
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(FILE_FORMAT))
    return handler


# --- kurulum ------------------------------------------------------------------------------

def setup_logger(level: Optional[int] = None, force: bool = False) -> None:
    """
    Kök logger'ı konsol ve (açıksa) dosya handler'ı ile kurar. Bir kez çalışır;
    force=True LOG_DIR / LOG_TO_FILE değişikliğini uygulamak için yeniden kurar.
    """
    global _configured, _console_handler, _file_handler, _file_error
    if _configured and not force:
        return

    # LOG_* ayarları .env'den de gelebilir: giriş noktası .env'i henüz yüklememiş olabilir
    dotenv.load_dotenv(env_file_path())

    root = logging.getLogger()
    reattach = list(_attached)
    for name in reattach:
        logging.getLogger(name).removeHandler(_file_handler)
    _attached.clear()
    for old in (_console_handler, _file_handler):
        if old is not None:
            root.removeHandler(old)
            old.close()

    # Kök logger'ı başkası kurmuşsa (kendi logging.basicConfig'ini çağıran betik, pytest) konsola
    # ikinci bir handler eklenmez: satırlar iki kez yazılmasın. Dosya handler'ı yine eklenir.
    _console_handler = None
    if not root.handlers:
        _console_handler = _build_console_handler()
        root.addHandler(_console_handler)

    _file_handler = None
    _file_error = None
    if log_to_file_enabled():
        path = os.path.join(log_dir(), LOG_FILE_NAME)
        try:
            _file_handler = build_file_handler(path)
            root.addHandler(_file_handler)
        except OSError as e:
            # Salt okunur dizin (ör. kapsayıcı): konsol logu yeterli, uygulama dursun istemeyiz
            _file_error = str(e)
            print(f"Warning: cannot open the log file ({path}): {e}. Logging to the console only.", file=sys.stderr)

    root.setLevel(resolve_level() if level is None else level)
    _configured = True
    for name in reattach:
        attach_file_handler(name)


def attach_file_handler(logger_name: str) -> bool:
    """
    Kök logger'a iletmeyen (propagate=False) bir logger'ın kayıtlarını da dosyaya yazar.
    uvicorn kendi logger'larını böyle kurar; sunucu hataları ("Exception in ASGI application")
    dosyada da görünsün diye src/web/app.py bunu "uvicorn" için çağırır.
    """
    if not _configured:
        setup_logger()
    target = logging.getLogger(logger_name)
    if _file_handler is None or target.propagate or _file_handler in target.handlers:
        return False
    target.addHandler(_file_handler)
    _attached.append(logger_name)
    return True


def apply_log_level(level: Optional[int] = None) -> int:
    """Seviyeyi çalışırken uygular (LOG_LEVEL / DEBUG değişince). Geçerli seviyeyi döndürür."""
    if not _configured:
        setup_logger()
    new = resolve_level() if level is None else level
    root = logging.getLogger()
    old = root.level
    if old != new:
        # Değişim satırı, iki seviyeden daha ayrıntılı olanı etkinken yazılır (INFO'dan sessizse yazılmaz)
        root.setLevel(min(old, new))
        logging.getLogger("Logger").info(
            "Log level changed: %s -> %s", logging.getLevelName(old), logging.getLevelName(new)
        )
        root.setLevel(new)
    return new


def current_level_name() -> str:
    return logging.getLevelName(logging.getLogger().level)


def log_file_path() -> Optional[str]:
    """Yazılan log dosyasının yolu; dosya logu kapalıysa ya da açılamadıysa None."""
    return _file_handler.baseFilename if _file_handler is not None else None


def log_file_error() -> Optional[str]:
    """Dosya açılamadıysa nedeni (tanılama için)."""
    return _file_error


def log_files() -> List[str]:
    """Var olan log dosyaları, en yeniden en eskiye (etkin dosya, .1, .2, ...)."""
    base = log_file_path()
    if not base:
        return []
    candidates = [base] + [f"{base}.{i}" for i in range(1, log_backup_count() + 1)]
    return [p for p in candidates if os.path.isfile(p)]


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger instance.
    Ensures that the logging system is configured.
    """
    if not _configured:
        setup_logger()
    return logging.getLogger(name)


# Initialize on import to ensure all loggers benefit from Rich
setup_logger()
