"""Log dosyası (dönen, düz metin, maskeli), konsol markup'ı ve çalışırken seviye değişimi."""
from __future__ import annotations

import io
import logging
import os
import subprocess
import sys

import dotenv
import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from src import logger as app_logger
from src import redact
from src.paths import env_file_path

JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3OTAwMDAwMDAsInN1YiI6InNvZmEifQ.c2lnbmF0dXJlLXZhbHVlLTEyMw"
PROXY = "http://scraper:Pr0xy-P4ss!word@proxy.example.com:8080"
SECRETS = ("Pr0xy-P4ss!word", "scraper:", JWT, "abc123def", "sk-live-0123456789abcdef")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ENV_KEYS = ("LOG_DIR", "LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT", "LOG_LEVEL", "DEBUG")

# Windows'ta bilinen sınırlar (CI'da görüldü; Windows "elden geldiğince" desteklenen platformdur).
# Açık bir dosya Windows'ta yeniden adlandırılamaz ve silinemez; src/fsutil.file_lock da orada
# hiçbir şey kilitlemez. Sonuç: dosyayı birden çok yazıcı açık tutarken çevirme yapılamaz (dosya
# LOG_MAX_MB'ı aşar, yalnız tek yazıcı kaldığında çevrilir) ve başka bir süreç çevirmeyi denerken
# yazılan kayıt düşebilir. Düzeltilince işaretler kaldırılır (strict olan kendini belli eder).
no_shared_rotation_on_windows = pytest.mark.xfail(
    sys.platform == "win32",
    reason="Bilinen sınır (Windows): log dosyası başka bir yazıcıda açıkken yeniden adlandırılamaz; "
    "birden çok yazıcı varken dosya çevrilmez (src/logger.py: SharedRotatingFileHandler.doRollover)",
    strict=True,
)
shared_rotation_races_on_windows = pytest.mark.xfail(
    sys.platform == "win32",
    reason="Bilinen sınır (Windows): süreçler arası kilit yok (src/fsutil.file_lock) ve açık dosya yeniden "
    "adlandırılamaz; birden çok süreç yazarken dosya çevrilmez, çevirme denemesi sırasında kayıt düşebilir",
    strict=False,
)


@pytest.fixture
def make_logger(tmp_path):
    """Kök logger'dan bağımsız, kendi dosyasına yazan bir logger kurar."""
    created = []

    def _make(name="t", max_bytes=1_000_000, backup_count=3, filename="app.log"):
        path = str(tmp_path / filename)
        handler = app_logger.build_file_handler(path, max_bytes=max_bytes, backup_count=backup_count)
        log = logging.getLogger(f"test_logger.{name}.{len(created)}")
        log.setLevel(logging.DEBUG)
        log.propagate = False
        log.addHandler(handler)
        created.append((log, handler))
        return log, path

    yield _make
    for log, handler in created:
        log.removeHandler(handler)
        handler.close()


@pytest.fixture
def reconfigure():
    """Ortamı değiştirip kök logger'ı yeniden kurar; test sonunda eski haline döndürür."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    saved_level = logging.getLogger().level

    def _apply(**env):
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        app_logger.setup_logger(force=True)

    yield _apply
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    for key in ("LOG_LEVEL", "DEBUG"):
        dotenv.unset_key(env_file_path(), key, quote_mode="never")
    app_logger.setup_logger(force=True)
    logging.getLogger().setLevel(saved_level)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# --- dosya: biçim ------------------------------------------------------------------------

def test_file_line_is_plain_and_parseable(make_logger):
    log, path = make_logger()
    log.warning("Lig eklendi: %s (ID: %d)", "Premier League", 17)
    line = _read(path).rstrip("\n")
    m = app_logger.LINE_RE.match(line)
    assert m, line
    assert m.group("level") == "WARNING"
    assert int(m.group("pid")) == os.getpid()
    assert m.group("logger") == log.name
    assert m.group("message") == "Lig eklendi: Premier League (ID: 17)"
    assert "\x1b[" not in line  # renk kodu yok


def test_bracketed_text_is_written_literally(make_logger):
    log, path = make_logger()
    log.info("durum [red]kırmızı[/red] [17] [/] [bold")
    assert "durum [red]kırmızı[/red] [17] [/] [bold" in _read(path)


def test_exception_traceback_goes_to_the_file(make_logger):
    log, path = make_logger()
    try:
        raise RuntimeError("patladı")
    except RuntimeError:
        log.exception("Beklenmeyen hata")
    text = _read(path)
    assert "Beklenmeyen hata" in text
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: patladı" in text


# --- dosya: dönme ------------------------------------------------------------------------

def test_rotation_caps_size_and_file_count(make_logger, tmp_path):
    log, path = make_logger(max_bytes=2000, backup_count=2)
    for i in range(400):
        log.info("satır %04d %s", i, "x" * 40)
    files = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("app.log") and not p.name.endswith(".lock"))
    assert files == ["app.log", "app.log.1", "app.log.2"]
    for name in files:
        assert (tmp_path / name).stat().st_size <= 2000
    # En yeni satır etkin dosyada, en eskiler atılmış
    assert "satır 0399" in _read(path)
    everything = "".join(_read(str(tmp_path / name)) for name in files)
    assert "satır 0000" not in everything


@no_shared_rotation_on_windows
def test_two_writers_share_one_file_across_rotation(make_logger, tmp_path):
    # İki süreç (web sunucusu + CLI) aynı dosyaya yazar: biri dosyayı çevirince diğeri
    # yeniden adlandırılmış eski dosyaya değil, yeni dosyaya yazmaya devam etmeli.
    first, path = make_logger("a", max_bytes=1500, backup_count=2)
    second, _ = make_logger("b", max_bytes=1500, backup_count=2)
    first.info("ilk yazıcı başladı")
    second.info("ikinci yazıcı başladı")
    for i in range(30):
        first.info("dolgu %02d %s", i, "y" * 60)  # first dosyayı (birkaç kez) çevirir
    second.info("ikinci yazıcı çevirmeden sonra")
    first.info("ilk yazıcı son satır")
    current = _read(path)
    assert "ikinci yazıcı çevirmeden sonra" in current
    assert "ilk yazıcı son satır" in current
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("app.log.") and not p.name.endswith(".lock")]
    assert 1 <= len(backups) <= 2


_WRITER = """
import logging, sys
sys.path.insert(0, sys.argv[1])
from src import logger as app_logger
handler = app_logger.build_file_handler(sys.argv[2], max_bytes=20000, backup_count=500)
log = logging.getLogger("writer" + sys.argv[3])
log.propagate = False
log.setLevel(logging.INFO)
log.addHandler(handler)
for i in range(int(sys.argv[4])):
    log.info("yazici=%s satir=%05d %s", sys.argv[3], i, "x" * 30)
"""


@shared_rotation_races_on_windows
def test_real_processes_writing_and_rotating_lose_nothing(tmp_path):
    # Web sunucusu + CLI + --watch aynı anda: hiçbir satır kaybolmaz, yarım satır oluşmaz
    path = str(tmp_path / "app.log")
    lines_each, writers = 1200, 3
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER, ROOT, path, str(n), str(lines_each)])
        for n in range(writers)
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    files = [p for p in tmp_path.iterdir() if p.name.startswith("app.log") and not p.name.endswith(".lock")]
    assert len(files) > 3  # gerçekten çevrildi
    seen = set()
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = app_logger.LINE_RE.match(line)
            assert m, line
            seen.add(m.group("message").split(" x")[0])
    assert len(seen) == lines_each * writers
    assert not (tmp_path / "app.log.rotating").exists()


def test_failed_rollover_keeps_logging_and_leaves_backups_alone(make_logger, tmp_path, monkeypatch):
    # Windows'ta dosya başka süreçte açıkken yeniden adlandırılamaz: satırlar kaybolmamalı,
    # eski dosyalar kaydırılıp silinmemeli, her kayıtta yeniden denenmemeli.
    log, path = make_logger(max_bytes=1500, backup_count=2)
    for i in range(25):
        log.info("önce %02d %s", i, "y" * 60)
    backups_before = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.iterdir() if p.name != "app.log"}
    assert "app.log.1" in backups_before

    real_replace = os.replace
    attempts = []

    def locked(src, dst):
        if src == path:
            attempts.append(src)
            raise PermissionError(13, "file is in use by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(app_logger.os, "replace", locked)
    for i in range(40):
        log.info("kilitliyken %02d %s", i, "y" * 60)
    monkeypatch.setattr(app_logger.os, "replace", real_replace)

    assert len(attempts) == 1  # bir kez denendi, sonra bekleme süresi
    current = _read(path)
    assert all(f"kilitliyken {i:02d}" in current for i in range(40))
    for name, text in backups_before.items():
        if not name.endswith(".lock"):
            assert (tmp_path / name).read_text(encoding="utf-8") == text

    # Bekleme süresi dolunca çevirme yeniden çalışır
    handler = next(h for h in log.handlers if isinstance(h, app_logger.SharedRotatingFileHandler))
    handler._rollover_blocked_until = 0.0
    log.info("kilit kalktı")
    assert _read(path).count("\n") == 1 and "kilit kalktı" in _read(path)
    assert "kilitliyken 39" in (tmp_path / "app.log.1").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows'ta açık bir dosya silinemez: bu durum orada oluşmaz")
def test_writer_recovers_when_the_file_is_deleted(make_logger):
    log, path = make_logger()
    log.info("önce")
    os.remove(path)
    log.info("sonra")
    assert "sonra" in _read(path)


# --- gizli değerler ----------------------------------------------------------------------

def test_secrets_never_reach_the_file(make_logger, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("THIRD_PARTY_API_KEY=sk-live-0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setenv("SOFA_CAPTCHA_TOKEN", JWT)
    monkeypatch.setenv("PROXY_URL", PROXY)
    redact.refresh()
    try:
        log, path = make_logger()
        log.error(f"Proxy/Bağlantı hatası: Failed to connect to {PROXY} - proxy: açık")
        log.debug("istek başlıkları: %s", {"Cookie": "sofa_captcha=abc123def", "X-Captcha": JWT})
        log.info("token yenilendi: %s", JWT)
        log.warning("üçüncü taraf yanıtı: anahtar sk-live-0123456789abcdef geçersiz")
        try:
            raise ConnectionError(f"CONNECT tunnel failed via {PROXY}, Cookie: sofa_captcha=abc123def")
        except ConnectionError:
            log.exception("İstek hatası")
        text = _read(path)
    finally:
        monkeypatch.undo()
        redact.refresh()
    for secret in SECRETS:
        assert secret not in text, secret
    # Satırlar kaybolmadı, yalnızca gizli parçalar maskelendi
    assert text.count(redact.MASK) >= 5
    assert "proxy.example.com:8080" in text
    assert "ConnectionError" in text and "Traceback" in text


def test_unparseable_env_line_does_not_deadlock_logging(make_logger, tmp_path, monkeypatch):
    """
    python-dotenv ayrıştıramadığı satırı logging ile uyarır. Bu uyarı, maskelenecek değerler
    toplanırken yazılır ve kendisi de maskelenmek ister: eskiden aynı kilit ikinci kez istendiği
    için süreç kilitleniyordu (tanılama özeti, ayar kaydı; Python 3.12 ve öncesinde her log satırı).
    """
    import threading

    env = tmp_path / ".env"
    env.write_text("THIRD_PARTY_API_KEY=sk-live-0123456789abcdef\nthis line has no equals sign\n", encoding="utf-8")
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    log, path = make_logger()
    # dotenv'in uyarısı da aynı (maskeleyen) dosya handler'ına gitsin: uygulamada kök logger'a gider
    dotenv_log = logging.getLogger("dotenv.main")
    handler = app_logger.build_file_handler(path, max_bytes=1_000_000, backup_count=3)
    dotenv_log.addHandler(handler)
    monkeypatch.setattr(redact, "_CACHE_SECONDS", -1.0)  # her satırda yeniden topla
    redact.refresh()
    try:
        masked = []

        def write():
            # Önce log dışından (tanılama özeti ve ayar kaydı böyle çağırır), sonra log satırlarıyla
            masked.append(redact.redact_text("anahtar sk-live-0123456789abcdef"))
            for i in range(3):
                log.warning("satır %d: anahtar sk-live-0123456789abcdef geçersiz", i)

        worker = threading.Thread(target=write, daemon=True)
        worker.start()
        worker.join(10)
        assert not worker.is_alive(), "logging deadlocked while collecting the values to mask"
        text = _read(path)
    finally:
        dotenv_log.removeHandler(handler)
        handler.close()
        monkeypatch.undo()
        redact.refresh()
    assert masked == ["anahtar ***"]
    assert "sk-live-0123456789abcdef" not in text
    assert text.count("geçersiz") == 3
    # Uyarı dosya değişmedikçe bir kez yazılır, her log satırında yinelenmez
    assert text.count("could not parse statement") == 1


def _rich_output(record_fn) -> str:
    buffer = io.StringIO()
    handler = app_logger.RedactingRichHandler(
        console=Console(file=buffer, force_terminal=False, width=300, color_system=None),
        rich_tracebacks=True,
        markup=False,
    )
    log = logging.getLogger("test_logger.rich")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(handler)
    try:
        record_fn(log)
    finally:
        log.removeHandler(handler)
    return buffer.getvalue()


def test_console_does_not_parse_brackets_as_markup():
    # markup açıkken "[/]" MarkupError verir ve satır hiç yazılmaz; "[red]" ise yutulur
    out = _rich_output(lambda log: log.info("durum [red]kırmızı[/red] [/] bitti"))
    assert "durum [red]kırmızı[/red] [/] bitti" in out


def test_console_output_is_redacted_including_tracebacks():
    def emit(log):
        log.error(f"bağlantı kurulamadı: {PROXY}")
        try:
            raise ConnectionError(f"tunnel failed via {PROXY}")
        except ConnectionError:
            log.exception("İstek hatası")

    out = _rich_output(emit)
    assert "Pr0xy-P4ss!word" not in out
    assert "proxy.example.com:8080" in out
    assert "ConnectionError" in out


def test_console_keeps_rich_traceback_when_nothing_is_secret():
    def emit(log):
        try:
            raise ValueError("sıradan hata")
        except ValueError:
            log.exception("hata")

    out = _rich_output(emit)
    assert "ValueError" in out and "sıradan hata" in out


def test_console_off_a_terminal_is_plain_and_redacted(monkeypatch):
    # Docker / cron / yönlendirme: stdout terminal değil; satırlar dosyadakiyle aynı düz biçimde
    buffer = io.StringIO()
    monkeypatch.setattr(app_logger.sys, "stdout", buffer)
    handler = app_logger._build_console_handler()
    assert not isinstance(handler, app_logger.RedactingRichHandler)
    log = logging.getLogger("test_logger.plain")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(handler)
    try:
        log.warning(f"vekil {PROXY} [bold]x[/bold] [/]")
    finally:
        log.removeHandler(handler)
    m = app_logger.LINE_RE.match(buffer.getvalue().rstrip("\n"))
    assert m and m.group("level") == "WARNING"
    assert m.group("message") == "vekil http://***@proxy.example.com:8080 [bold]x[/bold] [/]"
    assert "\x1b[" not in buffer.getvalue()


def test_console_on_a_terminal_uses_rich_without_markup(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(app_logger.sys, "stdout", Tty())
    handler = app_logger._build_console_handler()
    assert isinstance(handler, app_logger.RedactingRichHandler)
    assert handler.markup is False


# --- kurulum: konum, kapatma, yazılamayan dizin -------------------------------------------

def test_log_dir_and_file_are_configurable(reconfigure, tmp_path):
    reconfigure(LOG_DIR=str(tmp_path / "custom-logs"), LOG_TO_FILE=None)
    expected = str(tmp_path / "custom-logs" / app_logger.LOG_FILE_NAME)
    assert app_logger.log_file_path() == expected
    logging.getLogger("WebAPI").warning("özel dizine yazıldı")
    assert "özel dizine yazıldı" in _read(expected)
    assert app_logger.log_files() == [expected]


def test_relative_log_dir_is_anchored_to_the_project_not_the_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_DIR", "logs-relative")
    assert app_logger.log_dir() == str(app_logger._REPO_ROOT / "logs-relative")
    monkeypatch.delenv("LOG_DIR")
    assert app_logger.log_dir() == str(app_logger._REPO_ROOT / "logs")


def test_file_logging_can_be_turned_off(reconfigure, tmp_path):
    reconfigure(LOG_DIR=str(tmp_path / "off"), LOG_TO_FILE="false")
    logging.getLogger("WebAPI").warning("yalnızca konsol")
    assert app_logger.log_file_path() is None
    assert app_logger.log_files() == []
    assert not (tmp_path / "off").exists()


def test_unwritable_log_dir_does_not_break_the_app(reconfigure, tmp_path, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    reconfigure(LOG_DIR=str(blocker / "logs"), LOG_TO_FILE="true")
    assert app_logger.log_file_path() is None
    assert app_logger.log_file_error()
    logging.getLogger("WebAPI").warning("konsola yazılmaya devam")  # hata fırlatmaz
    assert "log dosyası açılamadı" in capsys.readouterr().err


def test_console_handler_is_added_only_when_nobody_configured_the_root_logger(reconfigure, tmp_path):
    # Kendi logging.basicConfig'ini çağıran betik (ya da pytest): konsola ikinci handler eklenmez,
    # dosya handler'ı yine eklenir. Kök boşsa (main.py, uvicorn) konsol handler'ı bizimkidir.
    root = logging.getLogger()
    reconfigure(LOG_DIR=str(tmp_path / "host"))
    assert root.handlers and app_logger._console_handler is None  # pytest'in handler'ları var
    assert app_logger._file_handler in root.handlers

    foreign = list(root.handlers)
    for handler in foreign:
        root.removeHandler(handler)
    try:
        app_logger.setup_logger(force=True)
        assert app_logger._console_handler in root.handlers
        assert app_logger._file_handler in root.handlers
        assert len(root.handlers) == 2
        app_logger.setup_logger(force=True)  # yeniden kurulum çoğaltmaz
        assert len(root.handlers) == 2
    finally:
        app_logger.setup_logger(force=True)
        if app_logger._console_handler is not None:
            root.removeHandler(app_logger._console_handler)
            app_logger._console_handler = None
        for handler in foreign:
            if handler is not app_logger._file_handler and handler not in root.handlers:
                root.addHandler(handler)


def test_setup_does_not_duplicate_handlers(reconfigure, tmp_path):
    reconfigure(LOG_DIR=str(tmp_path / "dup"))
    before = list(logging.getLogger().handlers)
    app_logger.setup_logger()
    app_logger.setup_logger(force=True)
    after = logging.getLogger().handlers
    assert len(after) == len(before)
    logging.getLogger("WebAPI").warning("tek satır")
    assert _read(app_logger.log_file_path()).count("tek satır") == 1


def test_size_and_count_come_from_the_environment(reconfigure, tmp_path):
    reconfigure(LOG_DIR=str(tmp_path / "caps"), LOG_MAX_MB="0.002", LOG_BACKUP_COUNT="1")
    assert app_logger.log_max_bytes() == int(0.002 * 1024 * 1024)
    assert app_logger.log_backup_count() == 1
    log = logging.getLogger("WebAPI")
    for i in range(200):
        log.warning("dolgu %03d %s", i, "z" * 50)
    names = sorted(p.name for p in (tmp_path / "caps").iterdir() if not p.name.endswith(".lock"))
    assert names == [app_logger.LOG_FILE_NAME, app_logger.LOG_FILE_NAME + ".1"]


@pytest.mark.parametrize("key, value", [("LOG_MAX_MB", "abc"), ("LOG_MAX_MB", "-1"), ("LOG_BACKUP_COUNT", "x")])
def test_invalid_caps_fall_back_to_defaults(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    assert app_logger.log_max_bytes() == int(app_logger.DEFAULT_MAX_MB * 1024 * 1024)
    assert app_logger.log_backup_count() == app_logger.DEFAULT_BACKUP_COUNT


def test_backup_count_is_at_least_one(monkeypatch):
    # 0 eski dosya = standart handler hiç çevirmez, dosya sınırsız büyür
    monkeypatch.setenv("LOG_BACKUP_COUNT", "0")
    assert app_logger.log_backup_count() == 1


def test_propagating_logger_is_not_given_a_second_file_handler(reconfigure, tmp_path):
    reconfigure(LOG_DIR=str(tmp_path / "attach"))
    silent = logging.getLogger("test_logger.uvicorn_like")
    silent.propagate = False
    try:
        assert app_logger.attach_file_handler("test_logger.uvicorn_like") is True
        assert app_logger.attach_file_handler("test_logger.uvicorn_like") is False  # ikinci kez eklenmez
        silent.error("ASGI uygulamasında hata")
        assert "ASGI uygulamasında hata" in _read(app_logger.log_file_path())
    finally:
        silent.handlers.clear()
        silent.propagate = True
        app_logger._attached.clear()
    # Köke ileten logger zaten dosyaya yazar: eklenirse satır iki kez yazılırdı
    assert app_logger.attach_file_handler("test_logger.propagating") is False


def test_attached_logger_follows_a_reconfiguration(reconfigure, tmp_path):
    reconfigure(LOG_DIR=str(tmp_path / "first"))
    silent = logging.getLogger("test_logger.uvicorn_like2")
    silent.propagate = False
    try:
        app_logger.attach_file_handler("test_logger.uvicorn_like2")
        reconfigure(LOG_DIR=str(tmp_path / "second"))
        silent.error("yeni dosyaya")
        assert "yeni dosyaya" in _read(str(tmp_path / "second" / app_logger.LOG_FILE_NAME))
        assert len(silent.handlers) == 1 or all(
            not isinstance(h, app_logger.SharedRotatingFileHandler) or h.baseFilename.startswith(str(tmp_path / "second"))
            for h in silent.handlers
        )
    finally:
        silent.handlers.clear()
        silent.propagate = True
        app_logger._attached.clear()


# --- seviye: çalışırken ------------------------------------------------------------------

def test_resolve_level(monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "warning")
    assert app_logger.resolve_level() == logging.WARNING
    monkeypatch.setenv("DEBUG", "true")
    assert app_logger.resolve_level() == logging.DEBUG
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    assert app_logger.resolve_level() == logging.INFO


def test_apply_log_level_changes_the_running_level(reconfigure, tmp_path):
    reconfigure(LOG_DIR=str(tmp_path / "lvl"), LOG_LEVEL="INFO", DEBUG="false")
    log = logging.getLogger("WebAPI")
    log.debug("debug-1 görünmez")
    os.environ["LOG_LEVEL"] = "DEBUG"
    assert app_logger.apply_log_level() == logging.DEBUG
    log.debug("debug-2 görünür")
    os.environ["LOG_LEVEL"] = "ERROR"
    app_logger.apply_log_level()
    log.warning("warning-3 görünmez")
    log.error("error-4 görünür")
    text = _read(app_logger.log_file_path())
    assert "debug-1" not in text and "warning-3" not in text
    assert "debug-2 görünür" in text and "error-4 görünür" in text
    assert "Log seviyesi değişti: INFO -> DEBUG" in text


def test_config_manager_applies_log_level_without_restart(reconfigure, tmp_path):
    from src.web.routes import api as api_mod

    reconfigure(LOG_DIR=str(tmp_path / "cm"), LOG_LEVEL="INFO", DEBUG="false")
    assert logging.getLogger().level == logging.INFO
    assert api_mod.config_manager.update_env_variable("LOG_LEVEL", "DEBUG") is True
    assert logging.getLogger().level == logging.DEBUG
    assert api_mod.config_manager.update_env_variable("LOG_LEVEL", "WARNING") is True
    assert logging.getLogger().level == logging.WARNING


def test_settings_endpoint_applies_log_level_at_runtime(reconfigure, tmp_path):
    from src.web.app import app

    reconfigure(LOG_DIR=str(tmp_path / "web"), LOG_LEVEL="INFO", DEBUG="false")
    client = TestClient(app)
    log = logging.getLogger("WebAPI")

    r = client.post("/api/settings", json={"log_level": "ERROR", "debug": False})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["log_level"] == "ERROR"
    assert logging.getLogger().level == logging.ERROR
    log.warning("ayar sonrası warning")
    log.error("ayar sonrası error")

    r = client.post("/api/settings", json={"debug": True})
    assert r.status_code == 200
    assert logging.getLogger().level == logging.DEBUG
    log.debug("ayar sonrası debug")

    text = _read(app_logger.log_file_path())
    assert "ayar sonrası warning" not in text
    assert "ayar sonrası error" in text
    assert "ayar sonrası debug" in text


def test_secret_setting_is_masked_in_the_change_log_line(reconfigure, tmp_path):
    from src.web.routes import api as api_mod

    reconfigure(LOG_DIR=str(tmp_path / "mask"), LOG_LEVEL="INFO", DEBUG="false")
    saved = os.environ.get("PROXY_URL")
    try:
        assert api_mod.config_manager.update_env_variable("PROXY_URL", PROXY) is True
        logging.getLogger("Utils").error(f"Proxy/Bağlantı hatası: {PROXY}")
        text = _read(app_logger.log_file_path())
    finally:
        dotenv.unset_key(env_file_path(), "PROXY_URL", quote_mode="never")
        if saved is None:
            os.environ.pop("PROXY_URL", None)
        else:
            os.environ["PROXY_URL"] = saved
        redact.refresh()
    assert "PROXY_URL=***" in text
    assert "Pr0xy-P4ss!word" not in text

