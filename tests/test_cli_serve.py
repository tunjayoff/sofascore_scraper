"""
`ssc serve` (sofascore_scraper/cli/commands/serve.py; plan maddesi P25): bağımsız değişkenler, Host izin listesi kuralları
(PR #43'ünkiler), belirteçsiz açık adres uyarısı, sink dağıtıcısının barındırılması, durdurma ve çıkış kodları,
`main.py --web`in çevirisi. Başlatıcı tests/test_start_web.py'de, Docker giriş noktası tests/test_packaging.py'dedir.

Süreç içi testler sunucu başlatmaz: `serve.run_server` sahtesiyle değiştirilir ve web uygulamasının o an
göreceği izin listesini (SOFASCORE_ALLOWED_HOSTS) kaydeder. Gerçek bir sunucuyu yalnızca bir test başlatır
(127.0.0.1'de boş bir port, ağ yok) ve SIGTERM ile durdurur.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import main as legacy_main
import test_cli_skeleton as skeleton
from sofascore_scraper.cli import legacy_flags, signals
from sofascore_scraper.cli.commands import serve as serve_command
from sofascore_scraper.web import security
from test_cli_skeleton import CliRunner, Run, Sandbox

cli = skeleton.cli
box = skeleton.box

REPO = Path(__file__).resolve().parents[1]
LOOPBACK = ["localhost", "127.0.0.1", "[::1]"]
TOKEN = "serve-test-token-0123456789abcdef"


class FakeServer:
    """`run_server` yerine: çağrıları ve o anki izin listesini kaydeder; istenirse bir istisna fırlatır."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.hosts_env: List[Optional[str]] = []
        self.raises: Optional[BaseException] = None
        self.during: Optional[Any] = None

    def __call__(self, options: Dict[str, Any]) -> None:
        self.calls.append(dict(options))
        self.hosts_env.append(os.environ.get(security.ALLOWED_HOSTS_ENV))
        if self.during is not None:
            self.during()
        if self.raises is not None:
            raise self.raises


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeServer:
    """Sahte sunucu; izin listesi ve belirteç ortamda yok, veri dizini geçici."""
    fake = FakeServer()
    monkeypatch.setattr(serve_command, "run_server", fake)
    for name in (security.ALLOWED_HOSTS_ENV, "SOFASCORE_SERVER__ALLOWED_HOSTS", security.TOKEN_ENV,
                 "SOFASCORE_SERVER__TOKEN", "SOFASCORE_SINKS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(security, "_startup_token", None)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return fake


def serve(cli: CliRunner, *argv: Any) -> Run:
    return cli("serve", *argv)


def warnings_of(caplog: pytest.LogCaptureFixture) -> List[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


# --- adres ve port -------------------------------------------------------------------------------


def test_default_is_this_computer_on_port_8000_with_the_loopback_names(cli: CliRunner, server: FakeServer,
                                                                      caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        run = serve(cli, "--json")
    assert run.exit_code == 0, run.stderr
    assert server.calls == [{"host": "127.0.0.1", "port": 8000}]
    # Yerel adreste izin listesine dokunulmaz: web uygulaması varsayılanı (yerel adlar) kullanır
    assert server.hosts_env == [None]
    data = run.data
    assert data["allowed_hosts"] == LOOPBACK and data["allowed_hosts_from"] == "default"
    assert (data["url"], data["token"], data["sinks"], data["dev"]) == ("http://127.0.0.1:8000", False, 0, False)
    assert run.json["warnings"] == [] and warnings_of(caplog) == []


def test_text_output_says_where_the_app_is_on_stderr_and_stdout_has_only_the_result(cli: CliRunner,
                                                                                    server: FakeServer) -> None:
    run = serve(cli, "--port", "9123")
    assert run.exit_code == 0
    assert "Web app: http://127.0.0.1:9123" in run.stderr
    assert run.stdout.strip() == "Web server stopped."


def test_address_and_port_come_from_the_config_file(cli: CliRunner, server: FakeServer, tmp_path: Path) -> None:
    config = skeleton.write_config(tmp_path / "sofascore.toml", 'schema = 1\n[server]\nhost = "localhost"\nport = 9001\n')
    run = serve(cli, "--config", config, "--json")
    assert run.exit_code == 0, run.stderr
    assert server.calls == [{"host": "localhost", "port": 9001}]
    # Bayrak dosyanın önündedir
    run = serve(cli, "--config", config, "--port", "9002", "--host", "127.0.0.1", "--json")
    assert server.calls[-1] == {"host": "127.0.0.1", "port": 9002}


@pytest.mark.parametrize("port", ["70000", "-1", "http"])
def test_a_bad_port_is_a_usage_error(cli: CliRunner, server: FakeServer, port: str) -> None:
    run = serve(cli, "--port", port, "--json")
    assert (run.exit_code, run.error["code"]) == (2, "invalid_request")
    assert server.calls == []


def test_dev_restarts_on_code_changes_only(cli: CliRunner, server: FakeServer) -> None:
    run = serve(cli, "--dev", "--json")
    assert run.exit_code == 0
    options = server.calls[0]
    assert options["reload"] is True
    assert [Path(path) for path in options["reload_dirs"]] == [REPO / "sofascore_scraper", REPO / "locales"]
    assert run.data["dev"] is True


# --- Host izin listesi (PR #43'ün kuralları) --------------------------------------------------------


@pytest.mark.parametrize("bind", ["0.0.0.0", "::"])
def test_every_interface_without_allowed_hosts_refuses_to_start(cli: CliRunner, server: FakeServer, bind: str) -> None:
    run = serve(cli, "--host", bind, "--json")
    assert (run.exit_code, run.error["code"]) == (2, "invalid_request")
    assert "--allowed-hosts" in run.error["message"] and "--allow-any-host" in run.error["message"]
    assert run.error["details"] == {"host": bind, "setting": "server.allowed_hosts"}
    assert server.calls == []
    assert os.environ.get(security.ALLOWED_HOSTS_ENV) is None  # sessizce "*" yapılmadı


@pytest.mark.parametrize("lang,expected", [("en", "insecure"), ("tr", "güvensiz")])
def test_the_refusal_explains_itself_in_the_app_language(cli: CliRunner, server: FakeServer, lang: str,
                                                         expected: str) -> None:
    run = serve(cli, "--host", "0.0.0.0", "--lang", lang)
    assert run.exit_code == 2 and run.stdout == ""
    assert expected in run.stderr and "--allow-any-host" in run.stderr
    assert "ssc serve --host 0.0.0.0 --allowed-hosts" in run.stderr


def test_allowed_hosts_the_user_set_are_used_as_written_and_never_overwritten(
        cli: CliRunner, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(security.ALLOWED_HOSTS_ENV, "localhost,my-server.lan")
    run = serve(cli, "--host", "0.0.0.0", "--port", "9000", "--json")
    assert run.exit_code == 0, run.stderr
    assert server.calls == [{"host": "0.0.0.0", "port": 9000}]
    assert server.hosts_env == ["localhost,my-server.lan"]
    assert run.data["allowed_hosts"] == ["localhost", "my-server.lan"]
    assert run.data["allowed_hosts_from"] == "settings"
    # --allow-any-host da kullanıcının değerini ezmez
    run = serve(cli, "--host", "0.0.0.0", "--allow-any-host", "--json")
    assert run.exit_code == 0 and server.hosts_env[-1] == "localhost,my-server.lan"


def test_allowed_hosts_from_the_config_file_and_the_new_variable(cli: CliRunner, server: FakeServer,
                                                                 monkeypatch: pytest.MonkeyPatch,
                                                                 tmp_path: Path) -> None:
    config = skeleton.write_config(tmp_path / "sofascore.toml",
                                   'schema = 1\n[server]\nallowed_hosts = ["box.lan", "127.0.0.1"]\n')
    run = serve(cli, "--config", config, "--host", "0.0.0.0", "--json")
    assert run.exit_code == 0, run.stderr
    assert server.hosts_env == ["box.lan,127.0.0.1"]
    monkeypatch.setenv("SOFASCORE_SERVER__ALLOWED_HOSTS", "other.lan")
    run = serve(cli, "--host", "0.0.0.0", "--json")
    assert run.exit_code == 0 and server.hosts_env[-1] == "other.lan"


def test_the_allowed_hosts_flag_wins_over_the_config_file(cli: CliRunner, server: FakeServer, tmp_path: Path) -> None:
    config = skeleton.write_config(tmp_path / "sofascore.toml", 'schema = 1\n[server]\nallowed_hosts = ["box.lan"]\n')
    run = serve(cli, "--config", config, "--host", "0.0.0.0", "--allowed-hosts", "flag.lan, 127.0.0.1", "--json")
    assert run.exit_code == 0, run.stderr
    assert server.hosts_env == ["flag.lan,127.0.0.1"]
    assert run.data["allowed_hosts_from"] == "flag"


def test_allow_any_host_is_the_explicit_insecure_opt_out(cli: CliRunner, server: FakeServer,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        run = serve(cli, "--host", "0.0.0.0", "--allow-any-host", "--json")
    assert run.exit_code == 0
    assert server.hosts_env == ["*"] and run.data["allowed_hosts"] == ["*"]
    # Yerel olmayan adres, belirteç yok: belirteç uyarısı. "*" uyarısını web uygulaması başlarken yazar
    assert any("without an access token" in message for message in warnings_of(caplog))


@pytest.mark.parametrize("bind,expected", [
    ("192.168.1.5", "localhost,127.0.0.1,[::1],192.168.1.5"),
    ("fe80::1", "localhost,127.0.0.1,[::1],[fe80::1]"),
])
def test_one_address_allows_the_loopback_names_and_that_address(cli: CliRunner, server: FakeServer, bind: str,
                                                                expected: str) -> None:
    run = serve(cli, "--host", bind, "--json")
    assert run.exit_code == 0
    assert server.hosts_env == [expected] and run.data["allowed_hosts_from"] == "bind"


def test_the_url_of_each_kind_of_address() -> None:
    def url(host: str) -> str:
        return serve_command.Bind(host, 8000, (), serve_command.FROM_DEFAULT, False).url

    assert url("0.0.0.0") == url("::") == "http://localhost:8000"
    assert url("192.168.1.5") == "http://192.168.1.5:8000"
    assert url("fe80::1") == url("[fe80::1]") == "http://[fe80::1]:8000"


# --- belirteçsiz açık adres uyarısı ----------------------------------------------------------------


def test_one_warning_when_exposed_without_a_token(cli: CliRunner, server: FakeServer,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        run = serve(cli, "--host", "192.168.1.5", "--port", "8123", "--json")
    assert run.exit_code == 0
    warnings = warnings_of(caplog)
    assert len(warnings) == 1
    assert "192.168.1.5:8123" in warnings[0] and "without an access token" in warnings[0]
    assert security.TOKEN_ENV in warnings[0]
    assert [warning["code"] for warning in run.json["warnings"]] == ["exposed_without_token"]


def test_every_interface_warns_too_even_when_only_published_locally(cli: CliRunner, server: FakeServer,
                                                                    monkeypatch: pytest.MonkeyPatch,
                                                                    caplog: pytest.LogCaptureFixture) -> None:
    # Docker: uygulama `-p 127.0.0.1:...` yayımını göremez; uyarı yazılır (karar D17, belgede açıklanır)
    monkeypatch.setenv(security.ALLOWED_HOSTS_ENV, ",".join(LOOPBACK))
    with caplog.at_level(logging.WARNING):
        assert serve(cli, "--host", "0.0.0.0").exit_code == 0
    assert len([m for m in warnings_of(caplog) if "without an access token" in m]) == 1


def test_no_warning_with_a_token_or_on_loopback(cli: CliRunner, server: FakeServer, monkeypatch: pytest.MonkeyPatch,
                                                caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert serve(cli, "--host", "127.0.0.1").exit_code == 0
        monkeypatch.setenv(security.TOKEN_ENV, TOKEN)
        run = serve(cli, "--host", "192.168.1.5", "--json")
    assert run.exit_code == 0 and run.data["token"] is True
    assert warnings_of(caplog) == []


def test_a_token_in_the_variable_named_by_token_env_counts(cli: CliRunner, server: FakeServer,
                                                           monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    config = skeleton.write_config(tmp_path / "sofascore.toml", 'schema = 1\n[server]\ntoken_env = "MY_SERVE_TOKEN"\n')
    monkeypatch.setenv("MY_SERVE_TOKEN", TOKEN)
    with caplog.at_level(logging.WARNING):
        run = serve(cli, "--config", config, "--host", "192.168.1.5", "--json")
    assert run.exit_code == 0 and run.data["token"] is True
    # Yapılandırma dosyası varken testin ortamındaki eski adlar ayrıca uyarılır; belirteç uyarısı yok
    assert not [message for message in warnings_of(caplog) if "access token" in message]


@pytest.mark.parametrize("lang,expected", [
    ("en", "Anyone who can reach this port can read and delete your data"),
    ("tr", "Bu porta ulaşabilen herkes verilerinizi okuyabilir ve silebilir"),
])
def test_the_warning_is_printed_even_when_the_log_level_hides_it(cli: CliRunner, server: FakeServer, lang: str,
                                                                 expected: str) -> None:
    run = serve(cli, "--host", "192.168.1.5", "--quiet", "--lang", lang)
    assert run.exit_code == 0
    assert expected in run.stderr and "192.168.1.5" in run.stderr


# --- durdurma ve çıkış kodları -----------------------------------------------------------------------


@pytest.mark.parametrize("stop", [KeyboardInterrupt(), signals.Terminated()])
def test_ctrl_c_and_sigterm_stop_the_service_with_0(cli: CliRunner, server: FakeServer, stop: BaseException) -> None:
    server.raises = stop
    run = serve(cli, "--json")
    assert run.exit_code == 0 and run.json["ok"] is True


def test_a_server_that_cannot_start_exits_with_1_and_says_so_on_stderr(cli: CliRunner, server: FakeServer) -> None:
    server.raises = SystemExit(3)  # uvicorn: port kullanımda
    run = serve(cli)
    assert run.exit_code == 1
    assert run.stdout == ""
    assert "could not start" in run.stderr
    run = serve(cli, "--json")
    assert run.exit_code == 1 and run.data["server_exit_code"] == 3
    assert "server_failed" in [warning["code"] for warning in run.json["warnings"]]


def test_uvicorn_access_lines_go_to_stderr() -> None:
    config = serve_command.log_config()
    streams = {name: handler.get("stream") for name, handler in config["handlers"].items()}
    assert streams and set(streams.values()) == {"ext://sys.stderr"}
    from uvicorn.config import LOGGING_CONFIG

    assert LOGGING_CONFIG["handlers"]["access"]["stream"] == "ext://sys.stdout"  # kopya değişti, asıl değil


# --- sink'ler ------------------------------------------------------------------------------------


def test_serve_hosts_the_configured_sinks(cli: CliRunner, server: FakeServer, monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
    from sofascore_scraper.store import StreamEvent, open_store

    out = tmp_path / "sink" / "events.jsonl"
    monkeypatch.setenv("SOFASCORE_SINKS", json.dumps([{"name": "file", "type": "file", "path": str(out),
                                                       "events": ["job.*"]}]))

    def emit() -> None:
        # Sunucu çalışırken bir iş biter: dağıtıcı onu sink'e teslim eder (en geç çıkıştaki boşaltmada)
        store = open_store(os.environ["DATA_DIR"])
        store.streams.append("job", [StreamEvent(type="job.finished", data={"state": "succeeded"}, source="job")])

    server.during = emit
    run = serve(cli, "--json")
    assert run.exit_code == 0, run.stderr
    assert run.data["sinks"] == 1
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [line["type"] for line in lines] == ["job.finished"]


def test_a_broken_sink_stops_the_start_before_the_server(cli: CliRunner, server: FakeServer,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    inside = Path(os.environ["DATA_DIR"]) / "inside.jsonl"
    monkeypatch.setenv("SOFASCORE_SINKS", json.dumps([{"name": "file", "type": "file", "path": str(inside)}]))
    run = serve(cli, "--json")
    assert (run.exit_code, run.error["code"]) == (2, "config_invalid")
    assert server.calls == []


def test_without_sinks_no_store_is_opened(cli: CliRunner, server: FakeServer) -> None:
    assert serve(cli, "--json").exit_code == 0
    assert not (Path(os.environ["DATA_DIR"]) / ".meta").exists()


# --- komut kaydı ---------------------------------------------------------------------------------


def test_serve_is_described_with_its_options(cli: CliRunner) -> None:
    commands = {c["name"]: c for c in cli("describe", "commands").data["commands"]["commands"]}
    flags = [option["flags"][0] for option in commands["serve"]["options"]]
    assert flags == ["--host", "--port", "--allowed-hosts", "--allow-any-host", "--dev", "--scheduler", "--no-scheduler"]
    assert commands["serve"]["loads_settings"] is True


def test_there_is_no_token_option_and_no_live_option(cli: CliRunner) -> None:
    run = cli("serve", "--help")
    assert run.exit_code == 0
    assert "--token" not in run.stdout and "--live" not in run.stdout


# --- main.py --web ---------------------------------------------------------------------------------


@pytest.mark.parametrize("argv,expected", [
    (["--web"], ["serve", "--host", "127.0.0.1", "--port", "8000"]),
    (["--web", "--host", "0.0.0.0", "--port", "9", "--allow-any-host", "--dev"],
     ["serve", "--host", "0.0.0.0", "--port", "9", "--allow-any-host", "--dev"]),
    (["--web", "--data-dir", "veri", "--ignore-rate-limit"],
     ["--data-dir", os.path.abspath("veri"), "--ignore-breaker", "serve", "--host", "127.0.0.1", "--port", "8000"]),
])
def test_legacy_web_is_translated_to_serve(argv: List[str], expected: List[str]) -> None:
    translation = legacy_flags.translate(legacy_main.parse_arguments(argv), os.getcwd())
    assert translation.commands == (tuple(expected),) and translation.error is None
    assert translation.interactive is False


def test_main_py_web_runs_serve_with_its_deprecation_line(server: FakeServer, capsys: pytest.CaptureFixture[str],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LANGUAGE", "en")
    assert legacy_main.main(["--web", "--port", "9010"]) == 0
    err = capsys.readouterr().err
    assert "this run is: ssc serve --host 127.0.0.1 --port 9010" in err
    assert server.calls == [{"host": "127.0.0.1", "port": 9010}]
    # Her arayüz, izin listesi yok: başlamaz (kod 2), sunucu çağrılmaz
    assert legacy_main.main(["--web", "--host", "0.0.0.0"]) == 2
    assert len(server.calls) == 1 and "--allow-any-host" in capsys.readouterr().err


# --- gerçek bir sunucu --------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM is POSIX")
def test_a_real_server_answers_and_stops_on_sigterm_with_0(box: Sandbox) -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "sofascore_scraper.cli.main", "--json", "serve", "--port", str(port)],
        cwd=box.cwd, env=box.environ(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        url = f"http://127.0.0.1:{port}/health"
        deadline = time.monotonic() + 60
        health = None
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    health = json.loads(response.read())
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        assert health is not None and health["status"] == "ok", proc.stderr.read() if proc.poll() is not None else ""
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(urllib.request.Request(url, headers={"Host": "evil.example"}), timeout=5)
        assert refused.value.code == 400
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0, err.decode()
    # stdout yalnızca sonuç: uvicorn'un erişim satırları stderr'dedir
    document = json.loads(out.decode())
    assert document["ok"] is True and document["data"]["port"] == port
    assert b'"GET /health HTTP/1.1" 200' in err
