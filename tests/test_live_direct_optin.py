"""
`direct` canlı kaynağı açık seçimdir (docs/design/02-services.md 8.3; plan maddesi P31, kabul ölçütleri).

  * Yalnızca yapılandırma kelimesi kelimesine `direct` dediğinde (bayrak, yapılandırma dosyası, ortam) push
    istemcisi kurulur. Başka her yapılandırmada, `page` kaynağı açılamasa ya da çökse de, push sunucusuna
    bağlantı denenmez (soket katmanı sahtedir) ve kimlik bilgisi okuyucusu hiç çağrılmaz. "auto" diye bir değer
    yoktur.
  * Uyarılar: `--help`, `describe`, `config validate` ve `config show`, komutun çıktısı ve her başlangıçta log.
  * Sahte bir kimlik bilgisiyle yapılan bir çalışmadan sonra veri dizininde, log dosyasında, olay akışlarında
    ve tanılama paketinde o kimlik bilgisi bulunmaz.

Ağ ve tarayıcı yok: push sunucusu tests/test_live_direct_source.py'deki süreç içi sahte sunucudur.
"""
from __future__ import annotations

import io
import json
import logging
import os
import socket
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import conftest
import test_cli_skeleton as skeleton
from sofascore_scraper.cli.commands import watch as watch_command
from sofascore_scraper.config import settings as model
from sofascore_scraper.services.live import direct_source as ds
from sofascore_scraper.services.live.supervisor import AVAILABLE_SOURCES, LiveService, explicit_scope
from sofascore_scraper.store import Store, open_store
from test_cli_skeleton import CliRunner
from test_live_direct_source import FakeNatsServer, FakeReader, fake_credential, fake_value, fast
from test_live_push_source import FakeOpener, finish_frame, scenario, wait_for
from test_live_service import Stop

cli = skeleton.cli


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data"
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(path))
    monkeypatch.setenv("SOFASCORE_REFRESH__WINDOW_HOURS", "48")
    return path


@pytest.fixture
def store(data_dir: Path) -> Store:
    return open_store(data_dir)


@pytest.fixture
def guard(monkeypatch: pytest.MonkeyPatch) -> Dict[str, List[Any]]:
    """
    Push istemcisinin bütün girişleri kaydedilir ve hiçbiri dışarı çıkamaz: soket katmanı (modülün ve
    standart kütüphanenin), kimlik bilgisi okuyucusu, bağlantının ve kaynağın kurulması.
    """
    calls: Dict[str, List[Any]] = {"socket": [], "read": [], "connection": [], "source": []}

    def no_socket(*args: Any, **kwargs: Any) -> Any:
        calls["socket"].append(args[:1])
        raise OSError("no network in tests")

    def no_read(self: Any, sport: str) -> Any:
        calls["read"].append(sport)
        raise ds.CredentialUnavailable("no browser in tests")

    def recorded(cls: Any, key: str) -> None:
        real = cls.__init__

        def init(self: Any, *args: Any, **kwargs: Any) -> None:
            calls[key].append(1)
            real(self, *args, **kwargs)

        monkeypatch.setattr(cls, "__init__", init)

    monkeypatch.setattr(ds, "open_socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setattr(ds.BrowserCredentialReader, "read", no_read)
    recorded(ds.DirectConnection, "connection")
    recorded(ds.DirectSource, "source")
    return calls


def nothing_reached(calls: Dict[str, List[Any]]) -> bool:
    return not any(calls.values())


# --- servis -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("requested", ["page", "poll", "auto", "DIRECT", "Direct", " direct", "direct ", "",
                                       "page,direct", "direct-client"])
def test_no_other_requested_source_builds_the_direct_client(store: Store, guard: Dict[str, List[Any]],
                                                            requested: str) -> None:
    live, done, api, clock = scenario()
    report = LiveService(store, explicit_scope(["football"], tournament_ids=[17]), fetch=api, clock=clock,
                         sleep=clock.sleep, requested_source=requested, page_opener=FakeOpener(fail=True)
                         ).run(Stop(clock, rounds=40))
    assert report.source in ("page", "poll") and report.rounds >= 1
    assert nothing_reached(guard)


def test_a_failing_page_source_falls_back_to_polling_and_never_to_direct(store: Store,
                                                                        guard: Dict[str, List[Any]]) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()

    def script(n: int) -> None:
        page = opener.pages.get("football")
        if page is not None and n in (1, 8, 30):  # açılır, bağlanır ve çöker; yeniden açılır ve yine çöker
            page.connect()
            page.emit("crash", page)

    report = LiveService(store, explicit_scope(["football"], tournament_ids=[17]), fetch=api, clock=clock,
                         sleep=clock.sleep, requested_source="page", page_opener=opener
                         ).run(Stop(clock, rounds=90, on_wait=script))
    assert report.source == "page" and report.leaders == {"football": "poll"} and len(opener.opened) >= 2
    assert {e.data["to"] for e in store.streams.read(streams=["system"]).events} <= {"page", "poll"}
    assert nothing_reached(guard)


def test_an_injected_connection_is_ignored_unless_direct_is_requested(store: Store,
                                                                      guard: Dict[str, List[Any]]) -> None:
    live, done, api, clock = scenario()
    reader = FakeReader(fake_credential("ws://127.0.0.1:9/"))
    connection = ds.DirectConnection(reader, connect=lambda c: guard["socket"].append(c))
    guard["connection"].clear()
    for requested in ("page", "poll"):
        service = LiveService(store, explicit_scope(["football"], tournament_ids=[17]), fetch=api, clock=clock,
                              sleep=clock.sleep, requested_source=requested, page_opener=FakeOpener(fail=True),
                              direct_connection=connection)
        service.run(Stop(clock, rounds=3))
        with pytest.raises(RuntimeError):
            service._direct()
    assert reader.reads == [] and not connection.running and nothing_reached(guard)


def test_direct_is_listed_but_is_never_the_default() -> None:
    assert model.LiveSettings().source == "page" and "direct" in AVAILABLE_SOURCES
    assert "auto" not in model.LIVE_SOURCES and "auto" not in watch_command.SOURCES


# --- ssc watch: bayrak, yapılandırma dosyası, ortam ----------------------------------------------------


@pytest.fixture
def watch_run(monkeypatch: pytest.MonkeyPatch, guard: Dict[str, List[Any]]) -> Dict[str, List[Any]]:
    """`ssc watch`'ın servisine sahte API, saat ve açılamayan sayfa açıcı verir; servis birkaç turda durur."""
    live, done, api, clock = scenario()
    monkeypatch.setattr(watch_command, "SERVICE_OPTIONS", {"fetch": api, "clock": clock, "sleep": clock.sleep,
                                                           "page_opener": FakeOpener(fail=True)})
    real_run = LiveService.run

    def run_briefly(self: LiveService, stop: Any, *, until_seconds: Optional[float] = None) -> Any:
        def script(n: int) -> None:
            if n == 1 and self.report.source == "direct":  # okuyucu bağlantının thread'inde çağrılır
                wait_for(lambda: bool(guard["read"]))

        return real_run(self, Stop(clock, rounds=3, on_wait=script), until_seconds=until_seconds)

    monkeypatch.setattr(LiveService, "run", run_briefly)
    return guard


def config_file(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "conf" / "sofascore.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"schema = {model.SCHEMA_VERSION}\n\n[live]\nsource = \"{source}\"\n", encoding="utf-8")
    return path


WATCH = ("watch", "--sport", "football", "--event", "500", "--json")


@pytest.mark.parametrize("how", ["default", "flag page", "flag poll", "env page", "env poll", "file page",
                                 "file poll"])
def test_watch_builds_no_direct_client_unless_told_to(cli: CliRunner, data_dir: Path, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch,
                                                      watch_run: Dict[str, List[Any]], how: str) -> None:
    args: List[Any] = ["--data-dir", data_dir]
    where, _, value = how.partition(" ")
    if where == "flag":
        args += ["--source", value]
    elif where == "env":
        monkeypatch.setenv("SOFASCORE_LIVE__SOURCE", value)
    elif where == "file":
        args = ["--config", config_file(tmp_path, value), *args]
    run = cli(*WATCH[:1], *args, *WATCH[1:])
    assert run.exit_code == 0, run.stderr
    assert run.data["source"] == (value or "page")
    assert "live_direct_source" not in [w["code"] for w in run.json["warnings"]]
    assert nothing_reached(watch_run)


@pytest.mark.parametrize("how", ["flag", "env", "file"])
def test_watch_builds_the_direct_client_when_told_to_and_says_so(cli: CliRunner, data_dir: Path, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch,
                                                                 watch_run: Dict[str, List[Any]], how: str,
                                                                 caplog: pytest.LogCaptureFixture) -> None:
    args: List[Any] = ["--data-dir", data_dir]
    if how == "flag":
        args += ["--source", "direct"]
    elif how == "env":
        monkeypatch.setenv("SOFASCORE_LIVE__SOURCE", "direct")
    else:
        args = ["--config", config_file(tmp_path, "direct"), *args]
    run = cli(*WATCH[:1], *args, *WATCH[1:])
    assert run.exit_code == 0, run.stderr
    assert run.data["source"] == "direct" and run.data["requested_source"] == "direct"
    assert len(watch_run["connection"]) == 1 and watch_run["read"] == ["football"]
    assert watch_run["socket"] == []  # kimlik bilgisi okunamadı: bağlanılmadı, yoklama sürdü
    direct = [w for w in run.json["warnings"] if w["code"] == "live_direct_source"]
    assert len(direct) == 1 and "terms-of-use grey area" in direct[0]["message"]
    assert "live_source_unavailable" not in [w["code"] for w in run.json["warnings"]]
    started = [r.getMessage() for r in caplog.records if "chosen explicitly" in r.getMessage()]
    assert len(started) == 1 and "IP address blocked" in started[0]  # her başlangıçta log


def test_there_is_no_auto_value(cli: CliRunner, data_dir: Path, monkeypatch: pytest.MonkeyPatch,
                                watch_run: Dict[str, List[Any]]) -> None:
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", "500", "--source", "auto")
    assert run.exit_code == 2
    monkeypatch.setenv("SOFASCORE_LIVE__SOURCE", "auto")
    run = cli(*WATCH[:1], "--data-dir", data_dir, *WATCH[1:])
    assert run.exit_code == 2 and run.error["code"] == "config_invalid"
    assert nothing_reached(watch_run)


# --- uyarılar ---------------------------------------------------------------------------------------------


WARNING_PARTS = ("credential outside the site's client", "may break without notice", "IP address blocked",
                 "terms-of-use grey area")


def test_the_four_warnings_are_in_help_describe_config_validate_and_show(cli: CliRunner, tmp_path: Path,
                                                                         caplog: pytest.LogCaptureFixture) -> None:
    def has_all(text: str) -> bool:
        flat = " ".join(text.split())
        return all(part in flat for part in WARNING_PARTS)

    assert has_all(cli("watch", "--help").stdout)
    sources = {s["name"]: s for s in cli("describe", "config").data["config"]["live_sources"]}
    assert sources["direct"]["available"] is True and has_all(sources["direct"]["warning"])
    assert has_all(json.dumps(cli("describe", "config").data["config"]["schema"]))  # config init'in yorumu da bu
    path = config_file(tmp_path, "direct")
    validated = cli("config", "validate", "--config", path, "--json")
    direct = [w for w in validated.json["warnings"] if w["code"] == "live_direct_source"]
    assert len(direct) == 1 and has_all(direct[0]["message"])
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        shown = cli("config", "show", "--config", path)
    assert shown.exit_code == 0 and "live.source" in shown.stdout
    # `config show` yükleyicinin uyarılarını ayarlar etkinleşirken log satırı olarak stderr'e yazar
    assert any(has_all(r.getMessage()) for r in caplog.records)
    from sofascore_scraper.cli.commands.meta import default_config_text  # `config init`'in yazdığı örnek dosya

    assert has_all(" ".join(line.lstrip("# ") for line in default_config_text().splitlines()))


def test_the_readmes_carry_the_four_warnings() -> None:
    root = Path(__file__).resolve().parent.parent
    english = (root / "README.md").read_text(encoding="utf-8")
    assert all(part in english for part in WARNING_PARTS)
    turkish = (root / "README.tr.md").read_text(encoding="utf-8")
    assert all(word in turkish for word in ("kimlik bilgisi", "haber vermeden", "IP adres", "gri alan"))
    assert "--source direct" in english and "--source direct" in turkish


# --- kimlik bilgisi hiçbir yere yazılmaz ------------------------------------------------------------------


@pytest.fixture
def log_files(tmp_path: Path) -> Any:
    from sofascore_scraper import logger as app_logger

    keys = ("SOFASCORE_LOG__DIR", "SOFASCORE_LOG__TO_FILE", "SOFASCORE_LOG__LEVEL", "SOFASCORE_LOG__DEBUG")
    saved = {k: os.environ.get(k) for k in keys}
    level = logging.getLogger().level
    os.environ["SOFASCORE_LOG__DIR"] = str(tmp_path / "logs")
    os.environ["SOFASCORE_LOG__LEVEL"] = "DEBUG"
    os.environ.pop("SOFASCORE_LOG__DEBUG", None)
    app_logger.setup_logger(force=True)
    yield tmp_path / "logs"
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    app_logger.setup_logger(force=True)
    logging.getLogger().setLevel(level)


def test_a_run_with_a_fake_credential_leaves_it_nowhere(store: Store, data_dir: Path, log_files: Path) -> None:
    from sofascore_scraper import diagnostics

    live, done, api, clock = scenario()
    server = FakeNatsServer(accept=fake_value("leak"))
    credential = fake_credential(server.url, "leak")
    needles = [fake_value("leak"), fake_value("leakp"), server.url]
    connection = fast(FakeReader(credential))

    def script(n: int) -> None:
        if n == 1:
            wait_for(lambda: connection.opens == 1)
            # Bir yerde yanlışlıkla log'a yazılsa da (ör. bir hata iletisinde) maskelenir
            logging.getLogger("test.leak").warning("connect options %s via %s", dict(credential.options),
                                                   credential.url)
        if n == 2:
            api.events[500] = done
            server.msg("sport.football", finish_frame(500, live["startTimestamp"]))
            wait_for(lambda: connection.messages == 1)

    try:
        LiveService(store, explicit_scope(["football"], tournament_ids=[17]), fetch=api, clock=clock,
                    sleep=clock.sleep, requested_source="direct", direct_connection=connection
                    ).run(Stop(clock, rounds=4, on_wait=script))
    finally:
        server.close()
    events = list(store.streams.read().events)
    assert [e.source for e in events if e.stream == "live"] == ["direct"]
    for event in events:
        text = json.dumps({"type": event.type, "data": event.data, "source": event.source}, default=str)
        assert not any(needle in text for needle in needles)
    bundle = zipfile.ZipFile(io.BytesIO(diagnostics.build_bundle(source="cli")))
    store.close()
    for handler in logging.getLogger().handlers:
        handler.flush()
    log_text = "".join(p.read_text(encoding="utf-8") for p in log_files.rglob("*.log*"))
    assert "connect options" in log_text and "terms-of-use grey area" in log_text
    for needle in needles:
        assert needle not in log_text
        for name in bundle.namelist():
            assert needle.encode() not in bundle.read(name), name
        for path in data_dir.rglob("*"):
            if path.is_file():
                assert needle.encode() not in path.read_bytes(), path


assert conftest  # sınır denetimi ve kancalar conftest'te kurulur
