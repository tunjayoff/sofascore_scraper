"""
Yeni CLI'nin iskeleti (src/cli; plan maddesi P18, docs/design/02-services.md bölüm 4).

Gruplar:

  hata tablosu      src/errors.py: her kodun sınıfı, çıkış kodu ve HTTP durumu tasarımdaki tabloyla aynı; her
                    PlatformError alt sınıfının bir satırı var; devralınan sınıflar doğru koda bağlanıyor
  çıkış kodları     src/cli/exit_codes.py: tablo, öncelik
  çıktı kuralları   JSON zarfı, metin kipi, stdout / stderr ayrımı
  komutlar          version, doctor, config show|validate|init|path, diagnostics: her biri için zarf ve çıkış
                    kodu (describe: tests/test_cli_describe.py)
  genel bayraklar   komuttan önce ve sonra; --lang; --config / --data-dir göreli yolları
  kayıt             komut modülleri kendini kaydeder
  log               src/logger.py: konsol akışı seçimi; yeni CLI'de log satırları stderr'de

Komutların çoğu aynı süreçte, `main()` çağrılarak sınanır (kapsam ölçümü alt süreçleri görmez); akış ayrımı,
çalışma dizini ve "paketler kurulmadan da çalışır" kuralı gerçek bir alt süreçte sınanır. Alt süreç G-03
goldenları gibi yalıtılmış bir ortamda ve istek bütçesi kapalı çalışır. Hiçbir test SofaScore'a bağlanmaz.
"""
from __future__ import annotations

import dataclasses
import gc
import io
import json
import logging
import os
import re
import site
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

import pytest

import conftest
from src import doctor, errors
from src import logger as app_logger
from src.cli import PROG, VERSION_TEXT, exit_codes, output
from src.cli import commands as registry
from src.cli import main as cli_main
from src.cli.commands import meta
from src.cli.output import CliWarning, CommandResult, Output
from src.config import loader
from src.exceptions import ConfigError as LegacyConfigError
from src.exceptions import StorageError
from src.sports import sport_slugs
from src.version import __version__

REPO = Path(__file__).resolve().parents[1]
CLI_SOURCES = sorted((REPO / "src" / "cli").rglob("*.py"))

# docs/design/02-services.md bölüm 2.6: kod -> (sınıf, çıkış kodu, HTTP durumları)
DESIGN_ERROR_TABLE: Dict[str, Any] = {
    "invalid_request": ("UsageError", 2, (400, 422)),
    "config_invalid": ("ConfigError", 2, (500,)),
    "confirmation_required": ("UsageError", 2, (400,)),
    "unauthorized": (None, None, (401,)),
    "forbidden_origin": (None, None, (403,)),
    "not_found": ("NotFoundError", 1, (404,)),
    "follow_managed": ("ConflictError", 1, (409,)),
    "follow_exists": ("ConflictError", 1, (409,)),
    "job_running": ("JobRunningError", 6, (409,)),
    "data_operation_running": ("ConflictError", 6, (409,)),
    "instance_running": ("InstanceRunningError", 6, (409,)),
    "blocked": ("UpstreamBlockedError", 4, (503,)),
    "rate_limited": ("UpstreamBlockedError", 4, (503,)),
    "upstream_error": ("UpstreamError", 4, (502,)),
    "partial": (None, 3, (200,)),
    "storage_error": ("StorageError", 5, (507,)),
    "not_supported": ("NotSupportedError", 2, (501,)),
    "cancelled": ("Cancelled", 130, ()),
    "internal": (None, 1, (500,)),
}


# --- yardımcılar ---------------------------------------------------------------------------------


def validate(instance: Any, schema: Mapping[str, Any], where: str = "$") -> List[str]:
    """JSON Schema'nın burada kullanılan alt kümesi için doğrulayıcı; sorunların listesini döndürür."""
    problems: List[str] = []
    if "oneOf" in schema:
        matches = [option for option in schema["oneOf"] if not validate(instance, option, where)]
        if len(matches) != 1:
            problems.append(f"{where}: matches {len(matches)} of the oneOf options")
        return problems
    if "const" in schema and instance != schema["const"]:
        problems.append(f"{where}: expected {schema['const']!r}, got {instance!r}")
    wanted = schema.get("type")
    if wanted is not None:
        names = [wanted] if isinstance(wanted, str) else list(wanted)
        kinds = {
            "object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None),
        }
        if not any(isinstance(instance, kinds[name]) and not (name == "integer" and isinstance(instance, bool)) for name in names):
            problems.append(f"{where}: expected {wanted}, got {type(instance).__name__}")
            return problems
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        problems += [f"{where}: missing {key!r}" for key in schema.get("required", ()) if key not in instance]
        if schema.get("additionalProperties") is False:
            problems += [f"{where}: unexpected {key!r}" for key in instance if key not in properties]
        for key, sub in properties.items():
            if key in instance:
                problems += validate(instance[key], sub, f"{where}.{key}")
    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            problems += validate(item, schema["items"], f"{where}[{index}]")
    return problems


@dataclass
class Run:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def json(self) -> Dict[str, Any]:
        document = json.loads(self.stdout)
        assert validate(document, output.ENVELOPE_SCHEMA) == []
        return document

    @property
    def data(self) -> Any:
        document = self.json
        assert document["ok"] is True, document
        return document["data"]

    @property
    def error(self) -> Dict[str, Any]:
        document = self.json
        assert document["ok"] is False and document["exit_code"] == self.exit_code, document
        return document["error"]


CliRunner = Callable[..., Run]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Iterator[CliRunner]:
    """
    `main()`i bu süreçte çalıştırır. CLI bir sürecin giriş noktasıdır ve süreç genelinde iz bırakır (çalışma
    dizini, etkin ayarlar ve ortama yazdıkları, log akışı ve seviyesi); hepsi testten sonra geri alınır.
    """
    cwd, environ, level = os.getcwd(), dict(os.environ), logging.getLogger().level

    def run(*args: str, cwd: Optional[Path] = None) -> Run:
        if cwd is not None:
            os.chdir(cwd)
        capsys.readouterr()
        code = cli_main.main([str(arg) for arg in args], prog=PROG)
        captured = capsys.readouterr()
        return Run(code, captured.out, captured.err)

    try:
        yield run
    finally:
        loader.reset()
        os.environ.clear()
        os.environ.update(environ)
        app_logger.set_console_stream("stdout")
        logging.getLogger().setLevel(level)
        os.chdir(cwd)


# Alt sürece testi çalıştıranın ortamından geçen değişkenler (tests/characterization/test_cli_goldens.py ile aynı liste)
_PASS_THROUGH = (
    "PATH", "TMPDIR", "TZ", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
    "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "OS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS",
)


@dataclass
class Sandbox:
    """Alt sürecin dokunabileceği her şey geçici dizindedir; ortam sıfırdan kurulur."""

    root: Path

    @property
    def cwd(self) -> Path:
        return self.root / "cwd"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @classmethod
    def create(cls, root: Path, env_lines: Sequence[str] = ()) -> "Sandbox":
        box = cls(root)
        for directory in (box.root / "config", box.cwd, box.root / "home"):
            directory.mkdir(parents=True)
        (box.root / "config" / "leagues.txt").write_text("Premier League: 17\n", encoding="utf-8", newline="\n")
        (box.root / "config" / "league_sports.json").write_text('{"17": "football"}', encoding="utf-8")
        box.env_file.write_text("\n".join(("MAX_CONCURRENT=5", *env_lines)) + "\n", encoding="utf-8", newline="\n")
        return box

    def environ(self, **extra: str) -> Dict[str, str]:
        env = {key: os.environ[key] for key in _PASS_THROUGH if key in os.environ}
        home = str(self.root / "home")
        env.update({
            "HOME": home,
            "USERPROFILE": home,
            "PYTHONPATH": str(REPO),
            "PYTHONIOENCODING": "utf-8",
            "LC_MESSAGES": "C",
            "DATA_DIR": str(self.root / "data"),
            "SOFASCORE_CONFIG_DIR": str(self.root / "config"),
            "SOFASCORE_ENV_FILE": str(self.env_file),
            "SOFASCORE_CONFIG": "none",
            "LOG_DIR": str(self.root / "logs"),
            # İstek bütçesi kapalı ve yalıtılmış (G-03 goldenları ve tests/conftest.py ile aynı)
            "REQUEST_RATE_LIMIT": "0",
            "SOFASCORE_THROTTLE_DIR": str(self.root / "throttle"),
            "SOFASCORE_BROWSER_PROFILE": str(self.root / "browser-profile"),
        })
        if site.ENABLE_USER_SITE:
            env["PYTHONUSERBASE"] = site.getuserbase()
        env.update(extra)
        return env

    def run(self, *args: str, code: Optional[str] = None, **extra_env: str) -> Run:
        command = [sys.executable, "-c", code] if code is not None else [sys.executable, "-m", "src.cli.main"]
        proc = subprocess.run(
            [*command, *args], cwd=self.cwd, env=self.environ(**extra_env), capture_output=True, timeout=120,
        )
        streams = [raw.decode("utf-8").replace("\r\n", "\n") for raw in (proc.stdout, proc.stderr)]
        return Run(proc.returncode, streams[0], streams[1])

    def files(self) -> List[str]:
        return sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file())


@pytest.fixture
def box(tmp_path: Path) -> Sandbox:
    return Sandbox.create(tmp_path / "box")


def same_path(left: Any, right: Any) -> bool:
    """Aynı dosya sistemi yolu mu (macOS'ta /var -> /private/var, Windows'ta harf büyüklüğü fark etmez)."""
    return os.path.normcase(os.path.realpath(str(left))) == os.path.normcase(os.path.realpath(str(right)))


def write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


BROKEN_CONFIG = 'schema = 1\n[client]\nrate = "fast"\n'
BROKEN_MESSAGE = '[client] rate: expected a number or "off", got \'fast\''
LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} [A-Z]+\s+\[\d+\] ", re.MULTILINE)


# === hata tablosu ================================================================================


def test_error_table_is_the_table_of_the_design():
    table = {
        spec.code: (spec.error_class, spec.exit_code, spec.http_status) for spec in errors.ERROR_TABLE
    }
    assert table == DESIGN_ERROR_TABLE
    assert [spec.code for spec in errors.ERROR_TABLE] == list(DESIGN_ERROR_TABLE)  # tasarımdaki sıra
    assert errors.ERRORS["cancelled"].exit_codes == (130, 143)
    assert [spec.code for spec in errors.ERROR_TABLE if not spec.raised] == ["partial"]
    assert all(spec.meaning for spec in errors.ERROR_TABLE)


def test_every_platform_error_subclass_has_a_row():
    classes = errors.platform_error_classes()
    assert {cls.__name__ for cls in classes} == {
        "UsageError", "NotFoundError", "ConflictError", "InstanceRunningError", "UpstreamError",
        "UpstreamBlockedError", "NotSupportedError", "Cancelled",
    }
    for cls in classes:
        assert cls.codes, cls
        for code in cls.codes:
            assert errors.ERRORS[code].error_class == cls.__name__, (cls, code)
            assert cls("text", code=code).code == code if cls is not errors.Cancelled else cls().code == code


def test_every_class_named_in_the_table_exists():
    own = {cls.__name__ for cls in errors.platform_error_classes()}
    for spec in errors.ERROR_TABLE:
        if spec.error_class is None or spec.error_class in own:
            continue
        module = __import__(errors.ADOPTED_CLASSES[spec.error_class], fromlist=[spec.error_class])
        assert isinstance(getattr(module, spec.error_class), type), spec.error_class
    # Devralınan iki sınıf bu modülden de alınabilir
    assert errors.ConfigError is LegacyConfigError and errors.StorageError is StorageError


def test_a_subclass_defined_elsewhere_must_have_a_row_too():
    def define() -> None:
        class Odd(errors._CodedError):
            codes = ("not_in_the_table",)

        assert Odd in errors.platform_error_classes()
        with pytest.raises(ValueError, match="unknown error code"):
            Odd("x")

    define()
    # Sınıf bu testle birlikte yok olmalı: tabloyu sınıflarla karşılaştıran diğer testler onu görmesin
    gc.collect()
    assert "Odd" not in {cls.__name__ for cls in errors.platform_error_classes()}


def test_platform_error_carries_code_message_and_details():
    error = errors.PlatformError("not_found", "no such event", {"event_id": 7})
    assert (error.code, error.message, error.details, str(error)) == ("not_found", "no such event", {"event_id": 7}, "no such event")
    assert error.to_dict() == {"code": "not_found", "message": "no such event", "details": {"event_id": 7}}
    assert error.spec.exit_code == 1
    assert errors.PlatformError("internal", "x").details is None
    with pytest.raises(ValueError, match="unknown error code"):
        errors.PlatformError("nope", "x")
    with pytest.raises(ValueError, match="cannot carry"):
        errors.UsageError("x", code="not_found")
    assert errors.UsageError("x").code == "invalid_request"
    assert errors.UsageError("x", code="confirmation_required").code == "confirmation_required"
    assert errors.UpstreamBlockedError("x").code == "blocked" and isinstance(errors.UpstreamBlockedError("x"), errors.UpstreamError)
    assert errors.error_spec("no-such-code").code == "internal"


def test_cancelled_knows_its_signal():
    import signal

    assert errors.Cancelled().exit_code == 130
    assert errors.Cancelled(signal_number=signal.SIGTERM).exit_code == 143
    assert exit_codes.exit_code_for(errors.Cancelled(signal_number=signal.SIGTERM)) == 143


def _lease_held(name: str, purpose: str = "") -> Exception:
    from src.store import LeaseHeld

    return LeaseHeld("kilit alınamadı", name=name, pid=4121, host="box", purpose=purpose, started_at=1790877602.0)


@pytest.mark.parametrize("name,purpose,code", [
    ("writer", "sync", "job_running"),
    ("writer", "op:backup", "data_operation_running"),
    ("maintenance", "restore", "data_operation_running"),
    ("live", "watch", "instance_running"),
    ("watcher:football", "watch", "instance_running"),
    ("sinks", "", "instance_running"),
])
def test_a_held_lease_becomes_the_running_code_of_its_name(name, purpose, code):
    error = errors.to_platform_error(_lease_held(name, purpose))
    assert (error.code, exit_codes.exit_code_for(error)) == (code, 6)
    assert error.details == {"holder": {
        "lease": name, "pid": 4121, "host": "box", "purpose": purpose or None, "since": "2026-10-01T18:00:02Z",
    }}
    assert errors.lease_error_code(name, purpose) == code


def test_a_held_lease_without_holder_fields_still_maps():
    from src.store import LeaseHeld

    error = errors.to_platform_error(LeaseHeld(name="writer"))
    assert error.code == "job_running"
    assert error.details == {"holder": {"lease": "writer", "pid": None, "host": None, "purpose": None, "since": None}}


def test_existing_exceptions_are_mapped_to_their_codes():
    from src.store import DataOperationRunningError, FollowExists, FollowManaged, JobRunningError, StoreBusy

    def code(exc: BaseException) -> str:
        return errors.to_platform_error(exc).code

    assert code(LegacyConfigError("sofascore.toml: bad")) == "config_invalid"
    assert errors.to_platform_error(LegacyConfigError("sofascore.toml: bad")).message == "sofascore.toml: bad"
    assert code(FollowExists()) == "follow_exists" and code(FollowManaged()) == "follow_managed"
    assert isinstance(errors.to_platform_error(FollowExists()), errors.ConflictError)
    assert code(JobRunningError()) == "job_running"
    assert code(DataOperationRunningError("backup")) == "data_operation_running"
    assert code(StoreBusy()) == "storage_error"  # her StoreError bir depolama hatasıdır
    assert code(KeyboardInterrupt()) == "cancelled"
    assert code(RuntimeError("boom")) == "internal"
    assert errors.to_platform_error(RuntimeError("boom")).message == "RuntimeError: boom"
    assert errors.to_platform_error(RuntimeError()).message == "RuntimeError"
    same = errors.NotFoundError("x")
    assert errors.to_platform_error(same) is same


def test_storage_error_keeps_path_and_reason():
    exc = StorageError.from_exception(OSError(28, "No space left on device"), "/data/x.json")
    error = errors.to_platform_error(exc)
    assert error.code == "storage_error"
    assert error.message == "storage error: No space left on device (/data/x.json)"
    assert error.details == {"class": "StorageError", "path": "/data/x.json", "errno": 28, "reason": "No space left on device"}
    assert exit_codes.exit_code_for(error) == 5


# === çıkış kodları ===============================================================================


def test_exit_code_table():
    assert [(spec.code, spec.name) for spec in exit_codes.EXIT_CODES] == [
        (0, "ok"), (1, "error"), (2, "usage"), (3, "partial"), (4, "upstream"), (5, "storage"),
        (6, "instance_running"), (130, "cancelled_sigint"), (143, "cancelled_sigterm"),
    ]
    by_code = {spec.code: spec.error_codes for spec in exit_codes.EXIT_CODES}
    assert by_code[0] == ()
    assert by_code[1] == ("not_found", "follow_managed", "follow_exists", "internal")
    assert by_code[2] == ("invalid_request", "config_invalid", "confirmation_required", "not_supported")
    assert by_code[3] == ("partial",)
    assert by_code[4] == ("blocked", "rate_limited", "upstream_error")
    assert by_code[5] == ("storage_error",)
    assert by_code[6] == ("job_running", "data_operation_running", "instance_running")
    assert by_code[130] == by_code[143] == ("cancelled",)


def test_exit_code_of_every_error_code():
    for code, (_cls, expected, _http) in DESIGN_ERROR_TABLE.items():
        # CLI'de karşılığı olmayan kodlar (unauthorized, forbidden_origin) genel hatadır
        assert exit_codes.exit_code_for(code) == (expected if expected is not None else 1), code
    assert exit_codes.exit_code_for("no-such-code") == 1
    assert exit_codes.exit_code_for(errors.UsageError("x")) == 2


def test_precedence_when_several_codes_apply():
    assert exit_codes.PRECEDENCE == (5, 4, 6, 3, 1)
    assert exit_codes.combine([3, 5, 4]) == 5
    assert exit_codes.combine([1, 6, 3]) == 6
    assert exit_codes.combine([3, 1, 0]) == 3
    assert exit_codes.combine([0, 0]) == 0 and exit_codes.combine([]) == 0
    assert exit_codes.combine([0, 2]) == 2 and exit_codes.combine([130, 1]) == 1


# === çıktı kuralları =============================================================================


def test_envelopes_have_the_shape_of_the_design():
    ok = output.success_envelope("sync", {"job_id": "01J"}, [CliWarning("legacy_name", "old name")])
    assert ok == {
        "ok": True, "command": "sync", "schema": "sofascore.cli/1", "version": __version__,
        "data": {"job_id": "01J"}, "warnings": [{"code": "legacy_name", "message": "old name"}],
    }
    failed = output.error_envelope("sync", errors.PlatformError("job_running", "busy", {"holder": {"pid": 1}}), 6)
    assert failed == {
        "ok": False, "command": "sync", "schema": "sofascore.cli/1", "version": __version__,
        "error": {"code": "job_running", "message": "busy", "details": {"holder": {"pid": 1}}}, "exit_code": 6,
    }
    assert validate(ok, output.ENVELOPE_SCHEMA) == [] and validate(failed, output.ENVELOPE_SCHEMA) == []
    assert validate({**ok, "extra": 1}, output.ENVELOPE_SCHEMA) != []
    assert validate({**failed, "exit_code": "6"}, output.ENVELOPE_SCHEMA) != []


def test_json_is_ascii_one_document_and_ndjson_is_one_line():
    document = {"name": "Beşiktaş", "path": Path("a") / "b", "tags": {"b", "a"}}
    pretty = output.dumps(document)
    assert pretty.isascii() and json.loads(pretty) == {"name": "Beşiktaş", "path": os.path.join("a", "b"), "tags": ["a", "b"]}
    line = output.dumps(document, output.NDJSON)
    assert "\n" not in line and json.loads(line) == json.loads(pretty)


def _streams() -> Dict[str, io.StringIO]:
    return {"stdout": io.StringIO(), "stderr": io.StringIO()}


def test_text_mode_puts_the_result_on_stdout_and_everything_else_on_stderr():
    streams = _streams()
    out = Output(mode=output.TEXT, t=lambda key, **kw: f"<{key} {kw.get('message', '')}>".replace(" >", ">"), **streams)
    out.result("x", CommandResult(
        data={"a": 1}, text="the result",
        warnings=[CliWarning("w1", "shown"), CliWarning("w2", "already in the log", logged=True)],
        notes=["a note"],
    ))
    assert streams["stdout"].getvalue() == "the result\n"
    assert streams["stderr"].getvalue() == "<ssc_warning shown>\na note\n"

    quiet = _streams()
    Output(mode=output.TEXT, quiet=True, **quiet).result("x", CommandResult(text=None, notes=["a note"]))
    assert (quiet["stdout"].getvalue(), quiet["stderr"].getvalue()) == ("", "")


def test_json_mode_prints_exactly_one_document_and_nothing_on_stderr():
    streams = _streams()
    out = Output(mode=output.JSON, **streams)
    out.result("x", CommandResult(data={"a": 1}, text="ignored", warnings=[CliWarning("w", "m", logged=True)], notes=["n"]))
    assert json.loads(streams["stdout"].getvalue()) == output.success_envelope("x", {"a": 1}, [CliWarning("w", "m")])
    assert streams["stderr"].getvalue() == ""

    failed = _streams()
    Output(mode=output.NDJSON, **failed).error("x", errors.UsageError("bad"), 2)
    assert failed["stdout"].getvalue().count("\n") == 1
    assert json.loads(failed["stdout"].getvalue())["error"]["code"] == "invalid_request"
    assert failed["stderr"].getvalue() == ""


def test_text_errors_are_labelled_in_the_chosen_language():
    for lang, label, hint in (("en", "Configuration error", "config validate"), ("tr", "Yapılandırma hatası", "config validate")):
        t, _ = cli_main.translator(lang)
        lines = Output(t=t, prog="ssc").error_lines(errors.to_platform_error(LegacyConfigError("x.toml: bad")))
        assert lines[0] == f"{label}: x.toml: bad" and hint in lines[1] and "ssc" in lines[1]
    t, _ = cli_main.translator("en")
    held = Output(t=t).error_lines(errors.to_platform_error(_lease_held("writer", "sync")))
    assert held == [
        "A job is already running: A job is already running on this data directory.",
        "Held by process 4121 on box (sync) since 2026-10-01T18:00:02Z",
    ]
    usage = Output(t=t, prog="ssc").error_lines(errors.UsageError("bad flag", {"usage": "usage: ssc x"}))
    assert usage == ["Usage error: bad flag", "usage: ssc x", "Run 'ssc --help' for the commands and options."]
    # Çevirisi olmayan kod: kodun kendisi başlık olur
    assert Output().error_lines(errors.PlatformError("unauthorized", "no token")) == ["unauthorized: no token"]


# === dil dosyaları ===============================================================================


def _locale(lang: str) -> Dict[str, str]:
    with open(REPO / "locales" / f"{lang}.json", encoding="utf-8") as f:
        return json.load(f)


def _keys_used_by_the_cli() -> set:
    used = set()
    for path in CLI_SOURCES:
        used |= set(re.findall(r'"((?:ssc|doctor_cli|diagnostics)_[a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    used.discard("ssc_error_")
    return used


def test_every_text_of_the_new_cli_exists_in_both_languages_and_is_used():
    en, tr = _locale("en"), _locale("tr")
    used = _keys_used_by_the_cli()
    labels = {"ssc_error_" + spec.code for spec in errors.ERROR_TABLE if spec.raised and spec.exit_code is not None}
    assert len(used) > 40
    assert used | labels <= set(en), sorted((used | labels) - set(en))
    ours = {key for key in en if key.startswith("ssc_")}
    assert ours == {key for key in tr if key.startswith("ssc_")}
    assert ours <= used | labels, sorted(ours - used - labels)  # kullanılmayan çeviri kalmasın
    assert not any(key.startswith("cli_") for key in used)  # `cli_*` anahtarları main.py'nindir (tests/test_language.py)


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_help_texts_survive_argparse_formatting(lang):
    """argparse yardım metinlerini %-biçimlendirir: yalın bir "%" --help'i çökertir."""
    for key, text in _locale(lang).items():
        if key.startswith(("ssc_help_", "ssc_description", "ssc_epilog", "ssc_usage_", "ssc_commands_", "ssc_global_")):
            assert re.sub(r"\{\w+\}", "", text) % {"prog": "ssc"}, key


# === version =====================================================================================


def test_version_flag_is_one_line(cli):
    run = cli("--version")
    assert (run.exit_code, run.stdout, run.stderr) == (0, VERSION_TEXT + "\n", "")
    assert VERSION_TEXT == f"SofaScore Scraper {__version__}"


def _store_versions() -> Dict[str, int]:
    from src.store import CATALOG_SCHEMA, LAYOUT_VERSION, load_migrations

    return {"layout": LAYOUT_VERSION, "catalog": CATALOG_SCHEMA, "state": load_migrations()[-1].version}


def test_version_command(cli):
    from src.schema import SCHEMA_VERSION

    store = _store_versions()
    text = cli("version")
    assert (text.exit_code, text.stderr) == (0, "")
    assert text.stdout.splitlines() == [
        VERSION_TEXT, "CLI output schema: sofascore.cli/1", "Config file schema: 1", f"Data schema: {SCHEMA_VERSION}",
        f"Store: layout {store['layout']}, catalog schema {store['catalog']}, state schema {store['state']}",
    ]

    as_json = cli("version", "--json")
    assert (as_json.exit_code, as_json.stderr) == (0, "")
    document = as_json.json
    assert document == {
        "ok": True, "command": "version", "schema": "sofascore.cli/1", "version": __version__,
        "data": {
            "name": "sofascore-scraper", "version": __version__,
            "python": ".".join(str(part) for part in sys.version_info[:3]),
            "schemas": {"cli": "sofascore.cli/1", "config": 1, "data": SCHEMA_VERSION},
            "store": store,
        },
        "warnings": [],
    }
    # Bugünkü sürümler (bir sürüm değişince bu satır bilerek güncellenir)
    assert (store["layout"], store["state"], SCHEMA_VERSION) == (3, 2, 1)


# === doctor ======================================================================================

PORTABLE_CHECKS = "profile,data_dir,config_dir,env,budget"


def test_doctor_json_envelope_and_exit_code(cli, monkeypatch):
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "5")
    run = cli("doctor", "--json", "--only", PORTABLE_CHECKS)
    report = run.data
    assert (run.exit_code, run.stderr, run.json["command"]) == (0, "", "doctor")
    assert sorted(report) == ["checks", "counts", "language", "ok", "root", "status"]
    assert [check["id"] for check in report["checks"]] == PORTABLE_CHECKS.split(",")
    assert (report["ok"], report["status"], report["language"], report["root"]) == (True, "ok", "en", str(REPO))
    assert report["checks"][-1]["detail"] == {"rate": 5.0, "default": 5.0, "source": "REQUEST_RATE_LIMIT"}


def test_doctor_warns_about_the_budget_and_strict_makes_it_exit_1(cli):
    """Testler (ve G-03 goldenları) istek bütçesi kapalı koşar: yeni CLI'nin doctor'ı bunu uyarı olarak bildirir."""
    assert os.environ["REQUEST_RATE_LIMIT"] == "0"
    run = cli("doctor", "--only", "budget")
    assert run.exit_code == 0 and run.stderr == ""
    assert "[WARN] Request budget: the limit is off" in run.stdout and "Fix: Set REQUEST_RATE_LIMIT=5" in run.stdout

    strict = cli("doctor", "--only", "budget", "--strict", "--json")
    assert strict.exit_code == 1
    assert strict.json["ok"] is True  # komut çalıştı; sonucun durumu çıkış kodunda ve data'da
    check = strict.data["checks"][0]
    assert (check["id"], check["status"], check["code"]) == ("budget", "warn", "budget_off")


def test_doctor_budget_follows_the_config_file_and_the_rate_flag(cli, tmp_path, monkeypatch):
    monkeypatch.delenv("REQUEST_RATE_LIMIT")  # süreç ortamı yapılandırma dosyasının önündedir
    config = write_config(tmp_path / "sofascore.toml", "schema = 1\n[client]\nrate = 20\n")
    from_file = cli("doctor", "--only", "budget", "--json", "--config", config).data["checks"][0]
    assert (from_file["code"], from_file["detail"]["rate"]) == ("budget_above_default", 20.0)
    assert from_file["detail"]["source"] == str(config)
    from_flag = cli("--rate", "3", "doctor", "--only", "budget", "--json", "--config", config).data["checks"][0]
    assert (from_flag["code"], from_flag["detail"]) == ("budget_ok", {"rate": 3.0, "default": 5.0, "source": "client.rate"})


def test_doctor_with_a_failed_check_exits_1(cli, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("REQUEST_TIMEOUT=soon\n", encoding="utf-8")
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env_file))
    text = cli("doctor", "--only", "env")
    assert text.exit_code == 1 and "[FAIL] Settings (.env)" in text.stdout and text.stderr == ""
    as_json = cli("doctor", "--only", "env", "--json")
    assert as_json.exit_code == 1
    assert (as_json.json["ok"], as_json.data["ok"], as_json.data["status"]) == (True, False, "fail")


def test_doctor_rejects_unknown_check_ids(cli):
    run = cli("doctor", "--only", "env,nope", "--json")
    assert run.exit_code == 2
    assert run.error["code"] == "invalid_request" and run.error["details"]["unknown"] == ["nope"]
    assert run.error["details"]["valid"] == [*doctor.CHECK_IDS, "budget", "live"]


def test_doctor_checks_the_data_dir_of_the_flag(cli, tmp_path):
    target = tmp_path / "elsewhere"
    check = cli("doctor", "--only", "data_dir", "--json", "--data-dir", "elsewhere", cwd=tmp_path).data["checks"][0]
    assert same_path(check["detail"]["path"], target)


def test_doctor_reports_a_broken_config_file_as_a_warning(cli, tmp_path):
    config = write_config(tmp_path / "sofascore.toml", BROKEN_CONFIG)
    run = cli("doctor", "--only", "budget", "--json", "--config", config)
    assert run.exit_code == 0
    assert [warning["code"] for warning in run.json["warnings"]] == ["config_invalid"]
    assert BROKEN_MESSAGE in run.json["warnings"][0]["message"]
    text = cli("doctor", "--only", "budget", "--config", config)
    assert text.stderr.startswith("Warning: ") and BROKEN_MESSAGE in text.stderr


def test_doctor_live_runs_only_when_asked_and_with_the_settings_loaded(cli, tmp_path, monkeypatch):
    """Gerçek istek atılmaz: canlı denetim sahtedir. `--live` ayarları yükler (istek uygulamanın ayarlarıyla atılır)."""
    monkeypatch.delenv("REQUEST_RATE_LIMIT")
    calls: List[Optional[str]] = []

    def fake_live(ctx: doctor.Context) -> doctor.CheckResult:
        calls.append(loader.active().config_file)
        return doctor.check_python(ctx)

    monkeypatch.setattr(doctor, "check_live", fake_live)
    config = write_config(tmp_path / "sofascore.toml", "schema = 1\n[client]\nrate = 2\n")

    assert cli("doctor", "--only", "budget", "--json", "--config", config).exit_code == 0
    assert calls == [] and loader.active().config_file is None  # --live olmadan ne istek ne ayar yüklemesi

    run = cli("doctor", "--live", "--only", "budget,live", "--json", "--config", config)
    assert run.exit_code == 0 and calls == [str(config)]
    assert [check["id"] for check in run.data["checks"]] == ["budget", "python"]
    assert os.environ["REQUEST_RATE_LIMIT"] == "2"  # ortamı okuyan istek bütçesi de dosyadaki değeri görür


def test_doctor_help_lists_the_extra_check(cli):
    run = cli("doctor", "--help")
    assert run.exit_code == 0 and "budget" in run.stdout and "--strict" in run.stdout and "--live" in run.stdout


# === config ======================================================================================


def test_config_show_prints_every_value_with_its_source(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://user:hunter2@proxy.example.com:8080")
    monkeypatch.delenv("REQUEST_RATE_LIMIT")  # süreç ortamı yapılandırma dosyasının önündedir
    config = write_config(tmp_path / "sofascore.toml", "schema = 1\n[client]\nrate = 2\n[[follow]]\ntournament = 17\n")
    run = cli("config", "show", "--json", "--config", config, "--data-dir", tmp_path / "data")
    data = run.data
    assert (run.exit_code, run.json["command"], data["config_file"]) == (0, "config show", str(config))
    rows = {row["key"]: row for row in data["values"]}
    assert rows["client.rate"] == {"key": "client.rate", "value": 2.0, "source": "file", "from": str(config), "locked": True}
    assert rows["storage.data_dir"]["value"] == str(tmp_path / "data") and rows["storage.data_dir"]["source"] == "flag"
    assert rows["client.proxy"]["value"] == "***" and "hunter2" not in run.stdout  # gizli değerler maskeli
    assert rows["follows"]["value"][0]["id"] == 17 and rows["follows"]["source"] == "file"
    assert [row["key"] for row in data["values"]] == [row["key"] for row in loader.active().describe()]

    text = cli("config", "show", "--config", config)
    lines = text.stdout.splitlines()
    assert lines[0] == f"Config file: {config}" and lines[1] == ""
    assert any(re.fullmatch(rf"client\.rate\s+= 2\.0  \[file: {re.escape(str(config))}\]", line) for line in lines)
    assert any(re.fullmatch(r"client\.proxy\s+= \"\*\*\*\"  \[env: PROXY_URL\]", line) for line in lines)
    assert "hunter2" not in text.stdout + text.stderr


def test_config_show_without_a_file_and_with_flags(cli):
    assert cli("config", "show").stdout.startswith("Config file: none (built-in defaults")
    run = cli("--rate", "off", "--ignore-breaker", "--lang", "tr", "--no-color", "config", "show", "--json", "--quiet")
    rows = {row["key"]: row for row in run.data["values"]}
    assert run.data["config_file"] is None
    assert rows["client.rate"]["value"] == 0.0 and rows["client.rate"]["source"] == "flag"
    assert rows["breaker.ignore"]["value"] is True
    assert rows["log.level"]["value"] == "ERROR" and rows["log.debug"]["value"] is False
    assert rows["display.language"]["value"] == "tr" and rows["display.use_color"]["value"] is False
    assert os.environ["NO_COLOR"] == "1"
    # Bayraklar ayarların en güçlü katmanıdır: ortamı hâlâ doğrudan okuyan modüller de aynı değeri görür
    assert (os.environ["REQUEST_RATE_LIMIT"], os.environ["IGNORE_RATE_LIMIT"], os.environ["LOG_LEVEL"]) == ("0", "true", "ERROR")
    assert logging.getLogger().level == logging.ERROR


@pytest.mark.parametrize("flags,level", [(["--verbose"], "DEBUG"), (["--log-level", "warning"], "WARNING")])
def test_log_level_flags_beat_the_debug_switch(cli, monkeypatch, flags, level):
    monkeypatch.setenv("DEBUG", "true")  # DEBUG=true seviyeyi DEBUG'a zorlar; açıkça istenen seviye önündedir
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    rows = {row["key"]: row for row in cli(*flags, "config", "show", "--json").data["values"]}
    assert (rows["log.level"]["value"], rows["log.level"]["source"], rows["log.debug"]["value"]) == (level, "flag", False)
    assert logging.getLogger().level == getattr(logging, level)


def test_config_show_reports_unreadable_legacy_values(cli, monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT", "many")
    run = cli("config", "show", "--json")
    assert run.json["warnings"] == [{
        "code": "legacy_value_ignored",
        "message": "MAX_CONCURRENT='many' is not valid and is ignored; client.max_concurrent is 10.",
    }]
    assert "Warning: MAX_CONCURRENT='many'" in cli("config", "show").stderr


def test_config_validate(cli, tmp_path):
    none = cli("config", "validate", "--json")
    assert none.exit_code == 0
    assert none.data == {
        "valid": True, "config_file": None, "overrides_file": None, "follows": 0, "sinks": 0, "schedule_tasks": 0,
    }
    assert cli("config", "validate").stdout.startswith("The configuration is valid (no config file")

    config = write_config(
        tmp_path / "sofascore.toml",
        'schema = 1\n[live]\nsource = "direct"\n[[follow]]\ntournament = 17\n[[follow]]\nevent = 5\n',
    )
    valid = cli("config", "validate", "--json", "--config", config)
    assert valid.exit_code == 0
    assert (valid.data["config_file"], valid.data["follows"]) == (str(config), 2)
    assert "live_direct_source" in [warning["code"] for warning in valid.json["warnings"]]
    text = cli("config", "validate", "--config", config)
    assert text.stdout == f"The configuration is valid: {config}\n"
    assert 'Warning: live.source is "direct"' in text.stderr  # uyarı metin kipinde stderr'e yazılır


def test_config_validate_does_not_touch_the_process(cli, tmp_path):
    config = write_config(tmp_path / "sofascore.toml", "schema = 1\n[client]\nrate = 2\n")
    before = dict(os.environ)
    assert cli("config", "validate", "--config", config).exit_code == 0
    assert dict(os.environ) == before
    assert loader.active().config_file is None


def test_config_path(cli, tmp_path, monkeypatch):
    disabled = cli("config", "path", "--json")
    assert disabled.exit_code == 0
    assert (disabled.data["config_file"], disabled.data["search_disabled"]) == (None, True)
    assert disabled.data["searched"] == [
        str(REPO / "sofascore.toml"), os.path.join(os.environ["SOFASCORE_CONFIG_DIR"], "sofascore.toml"),
    ]
    text = cli("config", "path")
    assert (text.stdout, text.stderr) == ("", "The config file search is turned off (SOFASCORE_CONFIG=none).\n")

    # Dosyanın içeriği okunmaz: bozuk bir dosyanın yolu da bulunur
    config = write_config(tmp_path / "sofascore.toml", BROKEN_CONFIG)
    monkeypatch.setenv("SOFASCORE_CONFIG", str(config))
    found = cli("config", "path")
    assert (found.exit_code, found.stdout, found.stderr) == (0, f"{config}\n", "")
    assert cli("config", "path", "--json").data["config_file"] == str(config)

    monkeypatch.setenv("SOFASCORE_CONFIG", "")
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "empty"))
    if not (REPO / "sofascore.toml").exists():
        missing = cli("config", "path")
        assert missing.stdout == "" and missing.stderr.startswith("No config file found. Searched: ")


def test_config_path_of_a_named_file_that_does_not_exist_is_a_config_error(cli, tmp_path):
    run = cli("config", "path", "--config", tmp_path / "nope.toml", "--json")
    assert run.exit_code == 2 and run.error["code"] == "config_invalid"
    assert "config file not found" in run.error["message"]


def _load_text(text: str, tmp_path: Path, environ: Optional[Mapping[str, str]] = None) -> Any:
    path = write_config(tmp_path / "generated.toml", text)
    return loader.load_settings(config_file=path, environ=dict(environ or {}), dotenv_values={}, overrides_file=None)


def test_config_init_prints_a_valid_file_with_every_default(cli, tmp_path):
    run = cli("config", "init")
    assert run.exit_code == 0
    assert run.stderr == (
        "Nothing was written. To use it, save the output as sofascore.toml in the project folder, "
        "for example: ssc config init > sofascore.toml\n"
    )
    text = run.stdout
    assert text.startswith(f"# sofascore.toml: configuration of SofaScore Scraper {__version__}.")
    assert cli("config", "init", "--quiet").stderr == ""
    as_json = cli("config", "init", "--json")
    assert as_json.data == {"from_legacy": False, "follows": None, "toml": text}

    defaults = loader.load_settings(config_file=None, environ={}, dotenv_values={}, overrides_file=None).settings
    # Olduğu gibi: her satır yorumdur, sonuç varsayılanlardır
    assert _load_text(text, tmp_path).settings == defaults

    # Her ayar satırının yorumu kaldırılınca dosya yine geçerlidir ve yine varsayılanları verir; yani yazılan
    # her değer o anahtarın gerçek varsayılanıdır ve dosyada yazılabilir
    from src.config import settings as model

    setting_line = re.compile(r"^# ([a-z_]+ = .+)$")
    uncommented, section, seen = [], None, set()
    for line in text.splitlines():
        header = re.fullmatch(r"\[([a-z]+)\]", line)
        if header:
            section = header.group(1)
        match = setting_line.match(line)
        if section and match and match.group(1).split(" = ")[0] in model.SETTING_KEYS[section]:
            seen.add(f"{section}.{match.group(1).split(' = ')[0]}")
            line = match.group(1)
        uncommented.append(line)
    assert seen == {key for key, f in model.iter_settings() if f.metadata["in_file"]}
    loaded = _load_text("\n".join(uncommented), tmp_path)
    # Dosyadaki göreli yol dosyanın dizinine göre çözülür; başka hiçbir değer varsayılandan ayrışmaz
    in_file_dir = dataclasses.replace(
        defaults, storage=dataclasses.replace(defaults.storage, data_dir=str(tmp_path / "data")),
    )
    assert loaded.settings == in_file_dir
    assert {loaded.source(key).layer for key in seen} == {"file"}

    # Örnek tablolar ([[follow]], [[sink]], [[schedule.task]], [slices.*]) da geçerlidir: bütün yorum
    # işaretleri kaldırılınca (açıklama satırları hariç) dosya yüklenir
    everything = []
    for line in text.splitlines():
        body = line[2:] if line.startswith("# ") else line
        is_toml = bool(re.match(r"^(\[|[a-z_]+ = )", body))
        everything.append(body if is_toml else line)
    with_examples = _load_text("\n".join(everything), tmp_path).settings
    assert [(follow.kind, follow.entity_id, follow.sport) for follow in with_examples.follows] == [("tournament", 17, "football")]
    assert [(sink.name, sink.type) for sink in with_examples.sinks] == [("feed", "file")]
    assert [(task.run, task.every) for task in with_examples.schedule.tasks] == [("sync", "6h")]
    assert list(with_examples.slices) == ["football"]


def test_toml_values():
    assert meta.toml_value(True) == "true" and meta.toml_value(False) == "false"
    assert meta.toml_value(3) == "3" and meta.toml_value(0.5) == "0.5" and meta.toml_value(5.0) == "5.0"
    assert meta.toml_value('a "b" \\ c\n\x7f') == '"a \\"b\\" \\\\ c\\n\\u007f"'
    assert meta.toml_value(("a", "b")) == '["a", "b"]' and meta.toml_value([]) == "[]"
    assert meta.toml_value("İstanbul") == '"İstanbul"'
    with pytest.raises(TypeError):
        meta.toml_value(None)


LEGACY_ENV = {
    "DATA_DIR": "legacy-data",
    "REQUEST_RATE_LIMIT": "off",
    "MAX_CONCURRENT": "7",
    "USE_PROXY": "true",
    "PROXY_URL": "http://user:hunter2@proxy.example.com:8080",
    "API_BASE_URL": "",
    "FETCH_ONLY_FINISHED": "false",
    "SOFASCORE_ALLOWED_HOSTS": "localhost,example.org",
    "LOG_LEVEL": "debug",
    "APP_LANGUAGE": "tr",
    "SOFASCORE_API_TOKEN": "sekret-token",
}


def test_config_init_from_legacy_is_the_equivalent_of_todays_sources(tmp_path):
    legacy = loader.load_settings(config_file=None, environ=LEGACY_ENV, dotenv_values=LEGACY_ENV, overrides_file=None)
    leagues = {17: "Premier League", 8: "LaLiga", 9: "LaLiga", 132: 'NBA "A"'}
    text = meta.legacy_config_text(legacy, leagues, {17: "football", 132: "basketball"}, "/cfg/leagues.txt")

    # Gizli değerler dosyaya girmez: proxy için değişkenin adı yazılır
    assert "hunter2" not in text and "sekret-token" not in text
    assert 'proxy_env = "PROXY_URL"' in text
    assert f"data_dir = {meta.toml_value(os.path.abspath('legacy-data'))}  # DATA_DIR" in text
    assert "# base_url: the value of API_BASE_URL cannot be written here" in text  # boş adres dosyada geçersiz

    # Dosya tek başına (bugünkü adlar olmadan; yalnızca adı verilen gizli değişkenler ortamda) aynı ayarları verir
    secrets = {"PROXY_URL": LEGACY_ENV["PROXY_URL"], "SOFASCORE_API_TOKEN": LEGACY_ENV["SOFASCORE_API_TOKEN"]}
    loaded = _load_text(text, tmp_path, secrets).settings
    expected = dataclasses.replace(
        legacy.settings,
        storage=dataclasses.replace(legacy.settings.storage, data_dir=os.path.abspath("legacy-data")),
        client=dataclasses.replace(legacy.settings.client, proxy_env="PROXY_URL", base_url=loaded.client.base_url),
        follows=loaded.follows,
    )
    assert loaded == expected
    assert loaded.client.effective_base_url == legacy.settings.client.effective_base_url
    assert (loaded.client.rate, loaded.client.use_proxy, loaded.client.proxy) == (0.0, True, LEGACY_ENV["PROXY_URL"])
    assert (loaded.display.language, loaded.log.level, loaded.fetch.only_finished) == ("tr", "DEBUG", False)
    assert [(f.kind, f.entity_id, f.name, f.sport, f.seasons) for f in loaded.follows] == [
        ("tournament", 17, "Premier League", "football", "all"),
        ("tournament", 8, "LaLiga (8)", None, "all"),   # aynı adlı iki lig: adlar ayrışır
        ("tournament", 9, "LaLiga (9)", None, "all"),
        ("tournament", 132, 'NBA "A"', "basketball", "all"),
    ]


def test_config_init_from_legacy_keeps_the_proxy_off_when_it_is_off_today(tmp_path):
    env = {"PROXY_URL": "http://proxy.example.com:8080"}
    legacy = loader.load_settings(config_file=None, environ=env, dotenv_values=env, overrides_file=None)
    assert legacy.settings.client.use_proxy is False
    text = meta.legacy_config_text(legacy, {}, {}, "/cfg/leagues.txt")
    assert "use_proxy = false" in text
    assert _load_text(text, tmp_path, env).settings.client.use_proxy is False


def test_config_init_from_legacy_command(cli, tmp_path, monkeypatch):
    """Komut bugünkü dosyaları okur: .env, ortam, leagues.txt ve league_sports.json (test kurulumundaki lig)."""
    from src.config_manager import ConfigManager
    from src.web import league_sports

    manager = ConfigManager()
    leagues = manager.get_leagues()
    sport_of = league_sports.load(manager.league_config_path)
    assert conftest.LEAGUE_ID in leagues and sport_of.get(conftest.LEAGUE_ID) == "football"

    run = cli("config", "init", "--from-legacy", "--json")
    assert run.exit_code == 0
    assert (run.data["from_legacy"], run.data["follows"]) == (True, len(leagues))
    text = run.data["toml"]
    assert f"one [[follow]] per league of {os.path.abspath(manager.league_config_path)}" in text
    assert text.count("\n[[follow]]\n") == len(leagues)
    name = json.dumps(leagues[conftest.LEAGUE_ID], ensure_ascii=False)
    assert f'[[follow]]\nname = {name}\nsport = "football"\ntournament = {conftest.LEAGUE_ID}\nseasons = "all"' in text
    assert f"data_dir = {meta.toml_value(conftest.DATA_DIR)}  # DATA_DIR" in text
    assert "max_concurrent = 5  # MAX_CONCURRENT" in text  # .env'deki değer

    # Çıktı geçerli bir yapılandırma dosyasıdır ve takipleri aynı ligleri verir
    loaded = _load_text(text, tmp_path).settings
    assert {follow.entity_id for follow in loaded.follows} == set(leagues)
    assert loaded.storage.data_dir == conftest.DATA_DIR

    note = cli("config", "init", "--from-legacy").stderr
    assert "ssc config init --from-legacy > sofascore.toml" in note

    # Bozuk bir yapılandırma dosyası bugünkü kaynakların okunmasını engellemez: dosya hesaba katılmaz
    broken = write_config(tmp_path / "broken.toml", BROKEN_CONFIG)
    assert cli("config", "init", "--from-legacy", "--config", broken, "--json").data["toml"] == text


# === diagnostics =================================================================================


def test_diagnostics_writes_the_bundle_relative_to_where_the_command_was_run(cli, tmp_path):
    run = cli("diagnostics", "--out", "out/bundle.zip", "--json", cwd=tmp_path)
    target = tmp_path / "out" / "bundle.zip"
    assert run.exit_code == 0 and list(run.data) == ["path"] and same_path(run.data["path"], target)
    with zipfile.ZipFile(target) as bundle:
        assert sorted(bundle.namelist()) == ["README.txt", "diagnostics.json", "log_tail.txt"]
        assert json.loads(bundle.read("diagnostics.json"))["source"] == "cli"
    text = cli("diagnostics", "--out", tmp_path / "second.zip")
    assert (text.exit_code, text.stdout) == (0, f"Diagnostics bundle written: {tmp_path / 'second.zip'}\n")


def test_diagnostics_that_cannot_be_written_is_a_storage_error(cli, tmp_path, monkeypatch):
    from src import diagnostics

    def refuse(path, source="cli"):
        raise OSError(28, "No space left on device", path)

    monkeypatch.setattr(diagnostics, "write_bundle", refuse)
    run = cli("diagnostics", "--out", tmp_path / "x.zip", "--json")
    assert run.exit_code == 5
    assert run.error["code"] == "storage_error" and run.error["details"]["errno"] == 28
    text = cli("diagnostics", "--out", tmp_path / "x.zip")
    assert text.stdout == "" and text.stderr.startswith("Storage error: storage error: No space left on device")


# === bozuk yapılandırma: iz dökümü değil, config_invalid ve çıkış kodu 2 ===========================


@pytest.mark.parametrize("command", [
    ("config", "show"), ("config", "validate"), ("diagnostics",), ("doctor", "--live", "--only", "budget"),
])
def test_a_broken_config_file_is_a_config_error_not_a_traceback(cli, tmp_path, command):
    config = write_config(tmp_path / "sofascore.toml", BROKEN_CONFIG)
    as_json = cli(*command, "--json", "--config", config)
    assert as_json.exit_code == 2
    assert as_json.error == {"code": "config_invalid", "message": f"{config}: {BROKEN_MESSAGE}", "details": None}
    assert as_json.json["command"] == " ".join(part for part in command if not part.startswith("-") and part != "budget")
    assert as_json.stderr == ""

    text = cli(*command, "--config", config)
    assert (text.exit_code, text.stdout) == (2, "")
    assert text.stderr.splitlines() == [
        f"Configuration error: {config}: {BROKEN_MESSAGE}",
        "Check the configuration with: ssc config validate",
    ]


def test_a_broken_config_found_by_the_search_is_reported_the_same_way(cli, tmp_path, monkeypatch):
    config = write_config(tmp_path / "sofascore.toml", "schema = 1\n[nope]\nx = 1\n")
    monkeypatch.setenv("SOFASCORE_CONFIG", str(config))
    run = cli("config", "show", "--json")
    assert run.exit_code == 2 and run.error["code"] == "config_invalid" and "nope" in run.error["message"]
    assert cli("version").exit_code == 0  # ayar yüklemeyen komutlar çalışır


def test_secrets_in_an_error_message_are_masked(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://user:hunter2@proxy.example.com:8080")

    @registry.command("zz-leak", help="ssc_help_cmd_version")
    def leak(inv: registry.Invocation) -> CommandResult:
        raise LegacyConfigError("cannot use http://user:hunter2@proxy.example.com:8080")

    try:
        run = cli("zz-leak", "--json")
    finally:
        registry._commands.pop(("zz-leak",))
    assert run.exit_code == 2 and "hunter2" not in run.stdout + run.stderr
    assert run.error["message"] == "cannot use http://***@proxy.example.com:8080"


# === kullanım hataları ===========================================================================


def test_no_command_prints_help_on_stderr_and_exits_2(cli):
    run = cli()
    assert (run.exit_code, run.stdout) == (2, "")
    lines = run.stderr.splitlines()
    assert lines[0] == "Usage error: a command is required"
    assert lines[1] == "usage: ssc [global options] [--version] COMMAND ..."
    assert "  config " in run.stderr and "  doctor " in run.stderr and "football, basketball, tennis" in run.stderr
    assert lines[-1] == "Run 'ssc --help' for the commands and options."

    group = cli("config")
    assert group.exit_code == 2 and "usage: ssc config [global options] COMMAND ..." in group.stderr
    assert all(name in group.stderr for name in ("show", "validate", "init", "path"))

    as_json = cli("config", "--json")
    assert as_json.exit_code == 2
    assert (as_json.json["command"], as_json.error["code"], as_json.error["message"]) == (
        "config", "invalid_request", "a command is required",
    )


@pytest.mark.parametrize("argv,command,message", [
    (["frobnicate"], None, "invalid choice: 'frobnicate'"),
    (["doctor", "--bogus"], "doctor", "unrecognized arguments: --bogus"),
    (["config", "show", "extra"], "config show", "unrecognized arguments: extra"),
    (["--rate", "fast", "version"], None, "argument --rate: expected requests per second"),
    (["--rate", "-1", "version"], None, "argument --rate"),
    (["version", "--output", "yaml"], "version", "argument --output: invalid choice"),
    (["version", "--log-level", "loud"], "version", "argument --log-level: invalid choice"),
    (["--quiet", "version", "--verbose"], None, "give only one of --quiet, --verbose"),
    (["--verbose", "--log-level", "info", "version"], None, "give only one of --verbose, --log-level"),
    (["describe", "everything"], "describe", "argument TOPIC: invalid choice"),
    (["doctor", "--str"], "doctor", "unrecognized arguments: --str"),  # kısaltma kabul edilmez
])
def test_usage_errors_exit_2_with_invalid_request(cli, argv, command, message):
    as_json = cli(*argv, "--json")
    assert as_json.exit_code == 2
    assert as_json.error["code"] == "invalid_request" and message in as_json.error["message"]
    assert as_json.json["command"] == command
    assert as_json.stderr == ""

    text = cli(*argv)
    assert (text.exit_code, text.stdout) == (2, "")
    assert text.stderr.startswith("Usage error: ") and message in text.stderr.splitlines()[0]
    if command and "give only one" not in message:
        assert f"usage: ssc {command} [global options]" in text.stderr


def test_an_unexpected_error_is_internal_with_the_traceback_on_stderr(cli):
    @registry.command("zz-boom", help="ssc_help_cmd_version")
    def boom(inv: registry.Invocation) -> CommandResult:
        raise RuntimeError("boom")

    @registry.command("zz-missing", help="ssc_help_cmd_version")
    def missing(inv: registry.Invocation) -> CommandResult:
        raise ModuleNotFoundError("No module named 'pandas'", name="pandas")

    @registry.command("zz-interrupt", help="ssc_help_cmd_version")
    def interrupt(inv: registry.Invocation) -> CommandResult:
        raise KeyboardInterrupt

    try:
        run = cli("zz-boom", "--json")
        assert run.exit_code == 1
        assert run.error == {"code": "internal", "message": "RuntimeError: boom", "details": None}
        assert "Traceback (most recent call last)" in run.stderr and "RuntimeError: boom" in run.stderr
        text = cli("zz-boom")
        assert text.stdout == "" and text.stderr.splitlines()[-1] == "Unexpected error: RuntimeError: boom"

        package = cli("zz-missing", "--json")
        assert package.exit_code == 1 and package.stderr == ""  # eksik paket bir programlama hatası değildir
        assert "a required package is not installed" in package.error["message"]
        assert "run `ssc doctor`" in package.error["message"]

        cancelled = cli("zz-interrupt", "--json")
        assert (cancelled.exit_code, cancelled.error["code"]) == (130, "cancelled")
    finally:
        for name in ("zz-boom", "zz-missing", "zz-interrupt"):
            registry._commands.pop((name,))


# === genel bayraklar =============================================================================


def test_global_flags_work_before_and_after_the_command(cli):
    before = cli("--json", "version")
    after = cli("version", "--json")
    assert before.json == after.json
    assert cli("--output", "json", "version").json == after.json
    assert cli("version", "--output=json").json == after.json
    # Komuttan sonra verilen değer öndekini ezer
    assert cli("--json", "version", "--output", "text").stdout.startswith(VERSION_TEXT)
    line = cli("version", "--output", "ndjson").stdout
    assert line.count("\n") == 1 and json.loads(line) == after.json


def test_prescan_finds_the_output_mode_and_language():
    assert cli_main._prescan([]) == ("text", None)
    assert cli_main._prescan(["--json", "x"]) == ("json", None)
    assert cli_main._prescan(["x", "--output", "ndjson", "--lang=tr"]) == ("ndjson", "tr")
    assert cli_main._prescan(["--output=json", "--lang", "en"]) == ("json", "en")
    assert cli_main._prescan(["--output", "yaml", "--lang", "de"]) == ("text", None)
    assert cli_main._prescan(["--", "--json"]) == ("text", None)


def test_lang_flag_translates_text_and_help_but_never_json(cli):
    english = cli("version", "--lang", "en")
    turkish = cli("version", "--lang", "tr")
    assert "CLI output schema" in english.stdout and "CLI çıktı şeması" in turkish.stdout
    assert cli("--lang", "tr", "--help").stdout.count("genel seçenekler") == 2
    help_tr = cli("config", "--help", "--lang=tr").stdout
    assert "usage: ssc config [genel seçenekler] COMMAND ..." in help_tr and "yapılandırmayı göster" in help_tr
    assert "ssc_" not in help_tr  # çevrilmemiş anahtar görünmüyor
    error_tr = cli("--lang", "tr", "config")
    assert error_tr.stderr.startswith("Kullanım hatası: a command is required")
    assert cli("version", "--json", "--lang", "tr").json == cli("version", "--json", "--lang", "en").json


def test_language_follows_the_application_setting(cli, monkeypatch):
    monkeypatch.setenv("APP_LANGUAGE", "tr")
    assert "CLI çıktı şeması" in cli("version").stdout
    assert "CLI output schema" in cli("version", "--lang", "en").stdout


def test_language_of_the_config_file_applies_to_the_text_of_commands_that_load_it(cli, tmp_path):
    config = write_config(tmp_path / "sofascore.toml", 'schema = 1\n[display]\nlanguage = "tr"\n')
    assert cli("config", "show", "--config", config).stdout.startswith(f"Yapılandırma dosyası: {config}\n")
    assert cli("config", "show", "--config", config, "--lang", "en").stdout.startswith(f"Config file: {config}\n")


def test_help_names_every_sport_and_not_only_football(cli):
    run = cli("--help")
    assert run.exit_code == 0 and run.stderr == ""
    assert f"match data ({', '.join(sport_slugs())})" in run.stdout  # src/sports.py, SP-1: 11 spor
    assert "football, basketball, tennis, american-football" in run.stdout
    assert "usage: ssc [global options] [--version] COMMAND ..." in run.stdout
    for name in ("config", "describe", "diagnostics", "doctor", "version"):
        assert re.search(rf"^    {name}\s", run.stdout, re.MULTILINE), name
    assert "ssc doctor --json" in run.stdout  # epilog'daki %(prog)s


def test_relative_paths_are_resolved_against_the_invoking_directory(cli, tmp_path, monkeypatch):
    monkeypatch.delenv("REQUEST_RATE_LIMIT")
    write_config(tmp_path / "mine.toml", "schema = 1\n[client]\nrate = 3\n")
    run = cli("config", "show", "--json", "--config", "mine.toml", "--data-dir", "d", cwd=tmp_path)
    rows = {row["key"]: row["value"] for row in run.data["values"]}
    assert same_path(run.data["config_file"], tmp_path / "mine.toml")
    assert rows["client.rate"] == 3.0 and same_path(rows["storage.data_dir"], tmp_path / "d")
    assert same_path(os.getcwd(), REPO)  # eski giriş noktası gibi proje köküne geçilir


def test_default_prog_is_the_module_form_unless_run_as_ssc(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/venv/bin/ssc"])
    assert cli_main._default_prog() == "ssc"
    monkeypatch.setattr(sys, "argv", ["C:\\venv\\Scripts\\ssc.exe"])
    assert cli_main._default_prog() in ("ssc", "python -m src.cli.main")  # Windows yolu yalnızca Windows'ta ayrışır
    monkeypatch.setattr(sys, "argv", ["/repo/src/cli/main.py"])
    assert cli_main._default_prog() == "python -m src.cli.main"


# === kayıt =======================================================================================


def test_commands_of_this_item_are_registered():
    """Alt küme olarak sınanır: sonraki işler komut ekler (bir dosya), bu testi düzenlemeleri gerekmez."""
    by_name = {command.name: command for command in registry.commands()}
    assert {
        "config init", "config path", "config show", "config validate", "describe", "diagnostics", "doctor", "version",
    } <= set(by_name)
    assert list(by_name) == sorted(by_name)
    assert "config" in [group.name for group in registry.groups()]
    assert registry.missing_groups() == []
    assert by_name["config show"].settings and by_name["diagnostics"].settings
    assert not any(by_name[name].settings for name in ("version", "doctor", "describe", "config path", "config validate"))
    assert by_name["describe"].always_json and not by_name["version"].always_json
    assert registry.find(("config", "show")) is by_name["config show"] and registry.find(("nope",)) is None


def test_a_command_is_added_by_registering_it_and_nothing_else(cli):
    def arguments(parser, t):
        parser.add_argument("--times", type=int, default=1)

    @registry.command("zz-echo", help="ssc_help_cmd_version", configure=arguments)
    def echo(inv: registry.Invocation) -> CommandResult:
        return CommandResult(data={"times": inv.args.times, "lang": inv.lang}, text="echo " * inv.args.times)

    try:
        assert cli("zz-echo", "--times", "2", "--json").data == {"times": 2, "lang": "en"}
        assert cli("zz-echo").stdout == "echo \n"
        assert "zz-echo" in cli("--help").stdout
    finally:
        registry._commands.pop(("zz-echo",))
    assert cli("zz-echo").exit_code == 2


def test_registry_rejects_bad_registrations():
    with pytest.raises(ValueError, match="already registered"):
        registry.command("version", help="x")(lambda inv: CommandResult())
    with pytest.raises(ValueError, match="already registered"):
        registry.group("config", help="x")
    with pytest.raises(ValueError, match="already registered"):
        registry.group("version", help="x")
    with pytest.raises(ValueError, match="parent of this command is itself a command"):
        registry.command("version more", help="x")(lambda inv: CommandResult())
    for bad in ("", "Version", "a_b", "a/b"):
        with pytest.raises(ValueError, match="invalid command name"):
            registry.command(bad, help="x")(lambda inv: CommandResult())


def test_a_subcommand_needs_a_declared_group():
    registry.command("zz-group leaf", help="ssc_help_cmd_version")(lambda inv: CommandResult())
    try:
        assert registry.missing_groups() == ["zz-group"]
        with pytest.raises(RuntimeError, match="zz-group"):
            cli_main.build_tree(lambda key, **kw: key)
    finally:
        registry._commands.pop(("zz-group", "leaf"))


def test_command_modules_register_themselves_without_a_shared_list():
    """Bu paketin altındaki her modül yüklenir: komut ekleyen iş bir dosya ekler, ortak bir dosyayı düzenlemez."""
    shared = [REPO / "src" / "cli" / "main.py", REPO / "src" / "cli" / "commands" / "__init__.py"]
    for path in shared:
        imports = re.findall(r"^\s*(?:from|import) .*$", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert imports and not any("meta" in line for line in imports), path
    assert sys.modules["src.cli.commands.meta"] is meta
    modules = {path.stem for path in (REPO / "src" / "cli" / "commands").glob("*.py") if not path.stem.startswith("_")}
    assert {f"src.cli.commands.{name}" for name in modules} <= set(sys.modules)


# === log: akış seçimi (src/logger.py) ============================================================


@pytest.fixture
def console_stream() -> Iterator[None]:
    try:
        yield
    finally:
        app_logger.set_console_stream("stdout")


def test_console_stream_is_stdout_unless_selected(console_stream, monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(app_logger.sys, "stdout", out)
    monkeypatch.setattr(app_logger.sys, "stderr", err)
    assert app_logger.console_stream() == "stdout"
    assert app_logger._build_console_handler().stream is out  # bugünkü davranış: web sunucusu ve main.py

    app_logger.set_console_stream("stderr")
    assert app_logger.console_stream() == "stderr"
    handler = app_logger._build_console_handler()
    assert handler.stream is err
    log = logging.getLogger("test_cli_skeleton.stream")
    log.propagate = False
    log.addHandler(handler)
    try:
        log.warning("to the error stream")
    finally:
        log.removeHandler(handler)
    assert out.getvalue() == "" and app_logger.LINE_RE.match(err.getvalue().rstrip("\n"))

    with pytest.raises(ValueError, match="unknown console stream"):
        app_logger.set_console_stream("file")
    assert app_logger.console_stream() == "stderr"


def test_console_stream_on_a_terminal_keeps_rich_on_the_selected_stream(console_stream, monkeypatch):
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    out, err = io.StringIO(), Tty()
    monkeypatch.setattr(app_logger.sys, "stdout", out)
    monkeypatch.setattr(app_logger.sys, "stderr", err)
    app_logger.set_console_stream("stderr")
    handler = app_logger._build_console_handler()
    assert isinstance(handler, app_logger.RedactingRichHandler) and handler.console.stderr is True
    # stdout terminal olsa da seçilen akış belirler
    monkeypatch.setattr(app_logger.sys, "stdout", Tty())
    monkeypatch.setattr(app_logger.sys, "stderr", io.StringIO())
    assert not isinstance(app_logger._build_console_handler(), app_logger.RedactingRichHandler)


def test_selecting_the_stream_survives_a_forced_setup(console_stream):
    app_logger.set_console_stream("stderr")
    app_logger.setup_logger(force=True)  # ayar yükleyici LOG_* değişince bunu çağırır
    assert app_logger.console_stream() == "stderr"
    assert app_logger.log_file_path() is not None  # dosya logu yerinde


def test_logger_warnings_are_english(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    monkeypatch.delenv("DEBUG", raising=False)
    assert app_logger.resolve_level() == logging.INFO
    assert capsys.readouterr().err == "Warning: invalid LOG_LEVEL 'LOUD'; using INFO.\n"


# === alt süreç: akışlar, çalışma dizini, paketsiz çalışma ========================================


def test_logs_go_to_stderr_and_stdout_stays_parseable(box: Sandbox):
    config = write_config(box.root / "sofascore.toml", 'schema = 1\n[live]\nsource = "direct"\n')
    run = box.run("config", "show", "--json", "--config", str(config))
    assert run.exit_code == 0
    assert run.json["command"] == "config show"  # stdout yalnızca zarf
    assert not LOG_LINE.search(run.stdout)
    lines = [line for line in run.stderr.splitlines() if LOG_LINE.match(line)]
    assert any("Config: Config file loaded:" in line for line in lines)
    assert any("WARNING" in line and 'live.source is "direct"' in line for line in lines)
    assert run.json["warnings"][0]["code"] == "live_direct_source"

    quiet = box.run("config", "show", "--json", "--config", str(config), "--quiet")
    assert quiet.exit_code == 0 and quiet.stderr == ""  # --quiet: yalnızca hatalar loglanır

    verbose = box.run("config", "show", "--config", str(config), "--verbose")
    assert verbose.stdout.startswith(f"Config file: {config}\n")
    assert "log.level" in verbose.stdout and re.search(r'log\.level\s+= "DEBUG"  \[flag\]', verbose.stdout)


def test_module_entry_point_runs_from_any_directory(box: Sandbox):
    run = box.run("diagnostics", "--out", "bundle.zip")
    assert run.exit_code == 0, run.stderr
    prefix = "Diagnostics bundle written: "
    assert run.stdout.startswith(prefix) and same_path(run.stdout[len(prefix):].strip(), box.cwd / "bundle.zip")
    assert (box.cwd / "bundle.zip").is_file()
    assert not LOG_LINE.search(run.stdout)
    usage = box.run("--help")
    assert "usage: python -m src.cli.main [global options] [--version] COMMAND ..." in usage.stdout


def test_a_broken_config_in_a_real_process_has_no_traceback(box: Sandbox):
    config = write_config(box.root / "sofascore.toml", BROKEN_CONFIG)
    for command in (("config", "show"), ("diagnostics",)):
        run = box.run(*command, SOFASCORE_CONFIG=str(config))
        assert run.exit_code == 2, run.stderr
        assert run.stdout == "" and "Traceback" not in run.stderr
        assert run.stderr.splitlines()[0] == f"Configuration error: {config}: {BROKEN_MESSAGE}"


def test_doctor_and_version_create_no_files(box: Sandbox):
    before = box.files()
    version = box.run("version", "--json")
    report = box.run("doctor", "--json", "--only", PORTABLE_CHECKS)
    assert (version.exit_code, report.exit_code, report.stderr) == (0, 0, "")
    assert [check["id"] for check in report.data["checks"]] == PORTABLE_CHECKS.split(",")
    assert report.data["checks"][-1]["code"] == "budget_off"  # alt süreç bütçe kapalı koşar
    assert box.files() == before


@pytest.mark.skipif(os.name == "nt", reason="kapanan boru POSIX'te EPIPE'dir; Windows'ta hata türü farklıdır")
def test_a_reader_that_goes_away_ends_the_command_quietly(box: Sandbox):
    """`ssc describe | head` gibi: okuyan süreç kapanınca iz dökümü ya da "Exception ignored" yazılmaz."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.cli.main", "describe"], cwd=box.cwd, env=box.environ(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.close()  # çocuk yazmaya başlamadan okuyan uç kapanır
    stderr = proc.stderr.read().decode("utf-8")
    proc.stderr.close()
    assert proc.wait(timeout=120) == 1
    assert stderr == ""


# Üçüncü taraf paketlerin hiçbiri içe aktarılamıyorken (yeni kurulum, paketler henüz kurulmamış)
_WITHOUT_PACKAGES = """
import sys

BLOCKED = {blocked!r}


class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError("No module named %r" % name, name=name)
        return None


sys.meta_path.insert(0, Block())
from src.cli.main import main

code = main(sys.argv[1:])
loaded = sorted(name for name in sys.modules if name.split(".")[0] in BLOCKED)
assert not loaded, loaded
sys.exit(code)
"""


def test_version_and_doctor_work_before_the_packages_are_installed(box: Sandbox):
    blocked = sorted({module for module, _dist in doctor.REQUIRED_MODULES} - set(sys.stdlib_module_names))
    assert {"dotenv", "rich", "pandas", "curl_cffi"} <= set(blocked)
    code = _WITHOUT_PACKAGES.format(blocked=set(blocked))

    assert box.run("--version", code=code).stdout == VERSION_TEXT + "\n"
    version = box.run("version", "--json", code=code)
    assert version.exit_code == 0 and version.data["schemas"] == {"cli": "sofascore.cli/1", "config": None, "data": 1}
    assert box.run("--help", code=code).exit_code == 0

    report = box.run("doctor", "--json", "--only", "python,packages,env,budget", code=code)
    assert report.exit_code == 1, report.stderr  # eksik paketler: doctor'ın bulması gereken sorun
    checks = {check["id"]: check for check in report.data["checks"]}
    assert checks["packages"]["code"] == "packages_missing" and checks["budget"]["code"] == "budget_off"

    show = box.run("config", "show", "--json", code=code)
    assert show.exit_code == 1 and show.error["code"] == "internal"
    assert "a required package is not installed" in show.error["message"] and "Traceback" not in show.stderr


def test_importing_the_cli_loads_no_heavy_module(box: Sandbox):
    code = (
        "import sys, json; import src.cli.main; from src.cli import commands; commands.load();"
        "print(json.dumps(sorted(name for name in sys.modules if name == 'src' or name.startswith('src.'))))"
    )
    run = box.run(code=code)
    assert run.exit_code == 0, run.stderr
    loaded = json.loads(run.stdout)
    assert {"src.cli.main", "src.cli.commands.meta", "src.errors"} <= set(loaded)
    # CLI'nin kendi modülleri dışında yalnızca saf, standart kütüphaneyle yetinen modüller: log (rich), ayar
    # yükleyici (dotenv), istek katmanı ve Store komut çalışırken yüklenir. Yeni bir komut modülü de buna uyar.
    light = {"src", "src.errors", "src.exceptions", "src.language", "src.sports", "src.version"}
    assert [name for name in loaded if not name.startswith("src.cli") and name not in light] == []
