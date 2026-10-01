"""
Ayar modeli ve yükleyici (src/config; plan maddesi P09, docs/design/02-services.md bölüm 4.3).

Dört grup:

  bugünkü davranış   yapılandırma dosyası yokken her ayar bugünkü okuyucusuyla aynı değeri verir (tuhaf
                     değerler dahil); G-04 goldenı (tests/snapshots/api/settings.json) ve ortamı hâlâ
                     doğrudan okuyan modüller ölçüttür
  yapılandırma       sofascore.toml: bölümler, öncelik sırası, göreli yollar, hatalar, listeler
  etkin ayarlar      ConfigManager getter'ları, ortam köprüsü, yeniden yükleme
  şema               JSON Schema modelden üretilir
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pytest

import conftest
from src import breaker, bridge_health, language, paths, refresh, throttle, watcher
from src import logger as app_logger
from src.config import Settings, config_schema, loader
from src.config import settings as model
from src.config.schema import environment_only_keys
from src.config_manager import ConfigManager
from src.exceptions import ConfigError
from src.store import files as store_files
from src.web import security

ROOT = Path(__file__).resolve().parent.parent

# docs/design/02-services.md bölüm 4.3'teki örnek dosya, olduğu gibi
DESIGN_SAMPLE = '''
schema = 1

[storage]
data_dir = "data"

[client]
rate = 5                  # requests per second across all processes; 0 or "off" removes the limit
max_concurrent = 10
timeout_seconds = 20
retries = 3
proxy = ""                # or proxy_env = "PROXY_URL"
odds_provider = 1

[defaults]
slices = ["core"]         # group names or slice keys; see `ssc describe slices`
seasons = "current"       # current | all | last:N | [ids]

[slices.tennis]
enable = ["point_by_point"]
disable = ["lineups", "incidents"]

[[follow]]
name = "premier-league"
sport = "football"
tournament = 17
seasons = "last:2"
slices = ["core", "odds", "standings"]
live = true

[[follow]]
event = 17124861

[refresh]
window_hours = 72
min_interval_hours = 6

[live]
source = "page"           # page | direct | poll. Polling is always the fallback of page and direct.
                          # "direct" uses SofaScore's own client credential outside its client, may break
                          # without notice, may get the IP address blocked and is a terms-of-use grey area:
                          # read section 8.3 before choosing it. It is never the default.
poll_interval_seconds = 30
detail_slices = []        # e.g. ["incidents", "statistics"] while an event is live
detail_interval_seconds = 20

[[sink]]
name = "ops"
type = "webhook"
url = "https://example.org/hooks/sofascore"
secret_env = "SOFASCORE_HOOK_SECRET"
events = ["live.*", "job.finished"]

[[sink]]
name = "feed"
type = "file"
path = "out/live.ndjson"
events = ["live.*"]

[schedule]
enabled = false
[[schedule.task]]
run = "sync"
every = "6h"              # or cron = "15 */6 * * *"

[server]
host = "127.0.0.1"
port = 8000
allowed_hosts = ["localhost", "127.0.0.1"]
token_env = ""            # name of the env var holding the optional access token (PR #43: SOFASCORE_API_TOKEN)

[log]
level = "info"
format = "text"
'''


def _load(
    tmp_path: Optional[Path] = None,
    *,
    toml: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    dotenv: Optional[Mapping[str, str]] = None,
    overrides: Optional[Dict[str, Any]] = None,
    flags: Optional[Mapping[str, Any]] = None,
) -> loader.LoadedSettings:
    """Verilen kaynaklarla, sürecin ortamına ve gerçek dosyalara bakmadan yükler."""
    config_file = overrides_file = None
    if toml is not None:
        config_file = tmp_path / "sofascore.toml"
        config_file.write_text(toml, encoding="utf-8")
    if overrides is not None:
        overrides_file = tmp_path / "overrides.json"
        overrides_file.write_text(json.dumps(overrides), encoding="utf-8")
    return loader.load_settings(
        config_file=config_file, environ=env or {}, dotenv_values=dotenv or {}, overrides_file=overrides_file,
        flags=flags,
    )


def _from_process_env() -> Settings:
    """Sürecin ortamından (monkeypatch ile kurulan), dosyalara bakmadan."""
    return loader.load_settings(config_file=None, dotenv_values={}, overrides_file=None).settings


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b and type(a) is type(b)


# === model =========================================================================================


def test_defaults_are_the_defaults_of_the_modules_that_read_the_environment_today():
    """Modeldeki varsayılanlar ikinci bir kopyadır; ortamı okuyan modül değişirse bu test düşer."""
    s = Settings()
    assert s.client.rate == throttle.DEFAULT_RATE_LIMIT
    assert s.refresh.window_hours == refresh.DEFAULT_REFRESH_WINDOW_HOURS
    assert s.refresh.min_interval_hours == refresh.DEFAULT_REFRESH_MIN_INTERVAL_HOURS
    assert s.bridge.degraded_after == bridge_health.DEFAULT_DEGRADED_AFTER
    assert s.bridge.blocked_after == bridge_health.DEFAULT_BLOCKED_AFTER
    assert s.bridge.blocked_min_seconds == bridge_health.DEFAULT_BLOCKED_MIN_SECONDS
    assert s.log.max_mb == app_logger.DEFAULT_MAX_MB
    assert s.log.backup_count == app_logger.DEFAULT_BACKUP_COUNT
    assert model.LOG_LEVELS == app_logger.LEVEL_NAMES
    assert s.server.allowed_hosts == security.LOOPBACK_HOSTS == model.LOOPBACK_HOSTS
    assert loader.DEFAULT_TOKEN_ENV == security.TOKEN_ENV
    assert model.LANGUAGES == language.SUPPORTED_LANGUAGES
    assert s.display.language == language.DEFAULT_LANGUAGE
    assert loader._OFF_WORDS == throttle._OFF_WORDS


def test_settings_are_frozen_and_keep_secrets_out_of_repr(tmp_path):
    loaded = _load(env={
        "PROXY_URL": "http://user:hunter2@proxy.example.com:8080", "SOFASCORE_API_TOKEN": "tok-en-123",
        "SOFA_CAPTCHA_TOKEN": "captcha-456",
    })
    s = loaded.settings
    assert (s.client.proxy, s.server.token, s.client.captcha_token) == (
        "http://user:hunter2@proxy.example.com:8080", "tok-en-123", "captcha-456",
    )
    for secret in ("hunter2", "tok-en-123", "captcha-456"):
        assert secret not in repr(s)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.client.rate = 1.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        s.slices["football"] = None  # type: ignore[index]

    rows = {row["key"]: row for row in loaded.describe()}
    assert rows["client.proxy"]["value"] == rows["server.token"]["value"] == rows["client.captcha_token"]["value"] == "***"
    assert loaded.describe(mask_secrets=False)[0]["key"] == "storage.data_dir"


def test_get_by_dotted_key():
    s = Settings()
    assert s.get("client.max_concurrent") == 10
    for key in ("client.nope", "nope.rate", "follows", "schedule.tasks"):
        with pytest.raises(KeyError):
            s.get(key)


def test_every_documented_environment_key_is_modelled():
    """.env.example'daki ve tanılama paketindeki her anahtarın modelde bir karşılığı var."""
    from src.diagnostics import SETTING_KEYS

    documented = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            documented.add(line.split("=", 1)[0].strip())
    known = set(loader.LEGACY_ENV_NAMES)
    assert documented - known == set()
    assert set(SETTING_KEYS) - known == set()
    # Tabloda her ayar en çok bir kez geçer ve gerçek bir alanı gösterir
    keys = [var.key for var in loader.LEGACY]
    assert len(keys) == len(set(keys))
    for key in keys + [loader.LANGUAGE_KEY]:
        Settings().get(key)


# === bugünkü davranış: yapılandırma dosyası yokken ==================================================

# GET /api/settings yanıtındaki alan -> ayar. log_level ve debug yanıtta ham olarak yankılanır
# (routes/settings.py); model log modülünün kuralını izler ve aşağıda src/logger ile karşılaştırılır.
GOLDEN_FIELDS = {
    "language": "display.language",
    "api_base_url": "client.base_url",
    "use_proxy": "client.use_proxy",
    "data_dir": "storage.data_dir",
    "use_color": "display.use_color",
    "date_format": "display.date_format",
    "max_concurrent": "client.max_concurrent",
    "request_rate_limit": "client.rate",
    "wait_time_min": "client.wait_time_min",
    "wait_time_max": "client.wait_time_max",
    "request_timeout": "client.timeout_seconds",
    "max_retries": "client.retries",
    "rate_limit_threshold_consecutive": "breaker.rate_limit_consecutive",
    "rate_limit_threshold_ratio": "breaker.rate_limit_ratio",
    "server_error_threshold_consecutive": "breaker.server_error_consecutive",
    "fetch_only_finished": "fetch.only_finished",
    "save_empty_rounds": "fetch.save_empty_rounds",
    "refresh_window_hours": "refresh.window_hours",
}


def _golden_scenarios() -> Dict[str, Any]:
    return json.loads((ROOT / "tests" / "snapshots" / "api" / "settings.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", sorted(_golden_scenarios()))
def test_legacy_environment_resolves_as_the_settings_golden_pins(scenario):
    """G-04'ün sabitlediği beş senaryo (ayrıştırılamayan değerler dahil) modelde de aynı sonucu verir."""
    golden = _golden_scenarios()[scenario]
    env = {"LC_MESSAGES": "C", **golden["env"]}
    body = golden["response"]["body"]
    for layer in ("env", "dotenv"):
        # Değerler süreç ortamından da `.env`'den de gelse sonuç aynıdır
        loaded = _load(env=env, dotenv=env if layer == "dotenv" else {})
        for field, key in GOLDEN_FIELDS.items():
            assert _same(loaded.settings.get(key), body[field]), (layer, field)
        assert loaded.settings.client.proxy == golden["env"].get("PROXY_URL", "")
        assert {source.layer for source in loaded.sources.values()} <= {layer}
        assert loaded.config_file is None and loaded.warnings == ()


RAW_VALUES = (
    "", " ", "0", "1", "3", " 7 ", "3.7", "-1", "0.0005", "abc", "1e2", "nan", "off", "OFF", "none", "disabled",
    "true", "TRUE", " true ", "false", "yes", "y", "on", "full", " FULL ", "debug", "Warning", "verbose",
    "a.example, b.example ,,", ",", "~/somewhere", "/abs/path",
)


def _log_dir(s: Settings) -> str:
    return s.log.dir


# değişken -> (bugünkü okuyucu, aynı şeyi modelden veren işlev)
TODAYS_READERS = {
    "REQUEST_RATE_LIMIT": (throttle.configured_rate, lambda s: s.client.rate),
    "REFRESH_WINDOW_HOURS": (refresh.refresh_window_hours, lambda s: s.refresh.window_hours),
    "REFRESH_MIN_INTERVAL_HOURS": (refresh.refresh_min_interval_hours, lambda s: s.refresh.min_interval_hours),
    "REFRESH_LEGACY": (refresh.refresh_legacy_enabled, lambda s: s.refresh.include_legacy),
    "IGNORE_RATE_LIMIT": (breaker._ignore_rate_limit, lambda s: s.breaker.ignore),
    "BRIDGE_DEGRADED_AFTER": (bridge_health.thresholds, lambda s: dataclasses.asdict(s.bridge)),
    "BRIDGE_BLOCKED_AFTER": (bridge_health.thresholds, lambda s: dataclasses.asdict(s.bridge)),
    "BRIDGE_BLOCKED_MIN_SECONDS": (bridge_health.thresholds, lambda s: dataclasses.asdict(s.bridge)),
    "LOG_LEVEL": (lambda: logging.getLevelName(app_logger.resolve_level()), lambda s: s.log.effective_level),
    "DEBUG": (lambda: logging.getLevelName(app_logger.resolve_level()), lambda s: s.log.effective_level),
    "LOG_TO_FILE": (app_logger.log_to_file_enabled, lambda s: s.log.to_file),
    "LOG_MAX_MB": (app_logger.log_max_bytes, lambda s: int(s.log.max_mb * 1024 * 1024)),
    "LOG_BACKUP_COUNT": (app_logger.log_backup_count, lambda s: s.log.backup_count),
    "LOG_DIR": (lambda: os.getenv("LOG_DIR", "").strip(), _log_dir),
    "WATCH_MAX_EVENT_POLLS": (watcher.max_event_polls, lambda s: s.live.max_event_polls),
    "SOFASCORE_ALLOWED_HOSTS": (security.allowed_hosts, lambda s: list(s.server.allowed_hosts)),
    "SOFASCORE_API_TOKEN": (lambda: os.environ.get("SOFASCORE_API_TOKEN", "").strip(), lambda s: s.server.token),
    "STORE_DURABILITY": (store_files.durability_full, lambda s: s.storage.durability == "full"),
    "SOFASCORE_BROWSER_PROFILE": (
        paths.browser_profile_dir,
        lambda s: os.path.expanduser(s.client.browser_profile or paths.DEFAULT_BROWSER_PROFILE_DIR),
    ),
    "SOFASCORE_THROTTLE_DIR": (
        throttle.state_dir,
        lambda s: s.client.throttle_dir or os.path.join(os.path.expanduser("~"), ".cache", "sofascore_scraper", "throttle"),
    ),
    "SOFA_CAPTCHA_TOKEN": (lambda: os.getenv("SOFA_CAPTCHA_TOKEN", "").strip(), lambda s: s.client.captcha_token),
}


@pytest.mark.parametrize("name", sorted(TODAYS_READERS))
def test_legacy_variable_is_parsed_like_the_module_that_reads_it_today(monkeypatch, name):
    """Ortamı hâlâ doğrudan okuyan her modül için: aynı ham değer, aynı sonuç (ayarlanmamış hali dahil)."""
    today, modelled = TODAYS_READERS[name]
    monkeypatch.delenv(name, raising=False)
    for other in ("BRIDGE_DEGRADED_AFTER", "BRIDGE_BLOCKED_AFTER", "LOG_LEVEL", "DEBUG"):
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setattr(throttle, "_warned_invalid_rate", None)
    assert _same(modelled(_from_process_env()), today()), "unset"
    for raw in RAW_VALUES:
        monkeypatch.setenv(name, raw)
        assert _same(modelled(_from_process_env()), today()), repr(raw)


def test_effective_base_url_is_the_one_the_request_layer_applies(monkeypatch, tmp_path):
    """
    API_BASE_URL'yi iki yer okur: ayarlar sayfası yazıldığı gibi gösterir (`base_url`), istek katmanı boş değeri
    varsayılana çevirip sondaki "/" işaretini atar (`effective_base_url`, src/client/transport.py).
    """
    from src import client
    from src.client import endpoints, transport

    assert model.DEFAULT_API_BASE_URL == endpoints.DEFAULT_BASE_URL
    for raw in (None, "", "  ", "https://api.example.invalid/api/v1", " https://api.example.invalid/api/v1/ "):
        if raw is None:
            monkeypatch.delenv("API_BASE_URL", raising=False)
        else:
            monkeypatch.setenv("API_BASE_URL", raw)
        settings = _from_process_env().client
        assert settings.effective_base_url == transport._configured_base_url(), repr(raw)
        assert settings.base_url == os.getenv("API_BASE_URL", endpoints.DEFAULT_BASE_URL), repr(raw)
        # İstemcinin kendi ayar sınıfı (P05) modelden kurulabilir: alan adları aynı
        built = client.ClientSettings(
            base_url=settings.effective_base_url, retries=settings.retries, timeout_seconds=settings.timeout_seconds,
        )
        assert built.base_url == settings.effective_base_url
    # Dosyadan gelen kök denetlenir ve sondaki "/" atılır
    from_file = _load(tmp_path, toml='[client]\nbase_url = "https://api.example.invalid/api/v1/"\n').settings.client
    assert from_file.base_url == from_file.effective_base_url == "https://api.example.invalid/api/v1"


def test_bridge_blocked_threshold_is_never_below_the_degraded_one(monkeypatch):
    monkeypatch.setenv("BRIDGE_DEGRADED_AFTER", "8")
    monkeypatch.setenv("BRIDGE_BLOCKED_AFTER", "5")
    assert dataclasses.asdict(_from_process_env().bridge) == bridge_health.thresholds()
    assert _from_process_env().bridge.blocked_after == 8


def test_browser_headed_matches_the_bridge(monkeypatch):
    from src import challenge_solver

    for raw in (None,) + RAW_VALUES:
        if raw is None:
            monkeypatch.delenv("SOFASCORE_BROWSER_HEADED", raising=False)
        else:
            monkeypatch.setenv("SOFASCORE_BROWSER_HEADED", raw)
        assert _from_process_env().client.browser_headed is (not challenge_solver._headless()), repr(raw)


@pytest.mark.parametrize(
    "env",
    [
        {}, {"APP_LANGUAGE": "tr"}, {"APP_LANGUAGE": " EN "}, {"APP_LANGUAGE": "de"}, {"LANGUAGE": "tr"},
        {"LANGUAGE": "tr_TR:tr"}, {"APP_LANGUAGE": "de", "LANGUAGE": "tr"}, {"APP_LANGUAGE": "en", "LANGUAGE": "tr"},
        {"LC_ALL": "tr_TR.UTF-8"}, {"LC_MESSAGES": "tr_TR", "LANG": "en_US.UTF-8"}, {"LANG": "tr_TR.UTF-8"},
        {"LC_ALL": "C", "LANG": "tr_TR.UTF-8"}, {"APP_LANGUAGE": "en", "LC_ALL": "tr_TR.UTF-8"},
    ],
)
def test_language_follows_the_one_rule(env):
    """src/language.resolve_language ile aynı sonuç; kaynağı da doğru (açık ayar mı, sistem dili mi)."""
    loaded = _load(env=env)
    assert loaded.settings.display.language == language.resolve_language(env)
    explicit = language.explicit_language(env) is not None
    assert (loaded.source("display.language").layer != loader.LAYER_DEFAULT) is explicit


def test_dotenv_and_process_environment_are_told_apart():
    """Ortamdaki değer `.env`'dekiyle aynıysa `.env`'den gelmiştir; farklıysa (ya da orada yoksa) süreç ortamından."""
    loaded = _load(
        env={"MAX_CONCURRENT": "4", "REQUEST_TIMEOUT": "30", "MAX_RETRIES": "7"},
        dotenv={"MAX_CONCURRENT": "4", "REQUEST_TIMEOUT": "20", "WAIT_TIME_MIN": "9"},
    )
    assert loaded.source("client.max_concurrent") == loader.Source("dotenv", "MAX_CONCURRENT", legacy=True)
    assert loaded.source("client.timeout_seconds") == loader.Source("env", "REQUEST_TIMEOUT", legacy=True)
    assert loaded.source("client.retries") == loader.Source("env", "MAX_RETRIES", legacy=True)
    # `.env`'de yazıp ortama yüklenmemiş bir değer sayılmaz (python-dotenv yüklemeden önceki durum)
    assert loaded.source("client.wait_time_min").layer == "default"
    assert (loaded.settings.client.max_concurrent, loaded.settings.client.timeout_seconds) == (4, 30)
    assert [loaded.source(key).locked for key in ("client.max_concurrent", "client.timeout_seconds")] == [False, True]


def test_invalid_legacy_values_are_reported_not_raised():
    loaded = _load(env={"MAX_CONCURRENT": "many", "REQUEST_RATE_LIMIT": "fast", "LOG_LEVEL": "verbose"})
    assert dict(loaded.invalid_legacy) == {
        "client.max_concurrent": "many", "client.rate": "fast", "log.level": "verbose",
    }
    assert loaded.settings.client.max_concurrent == 10
    assert loaded.warnings == ()


# === yapılandırma dosyası ===========================================================================


def test_design_sample_loads(tmp_path):
    loaded = _load(tmp_path, toml=DESIGN_SAMPLE)
    s = loaded.settings
    file_name = str(tmp_path / "sofascore.toml")
    assert loaded.config_file == file_name
    assert s.storage.data_dir == str(tmp_path / "data")
    assert (s.client.rate, s.client.max_concurrent, s.client.timeout_seconds, s.client.retries) == (5.0, 10, 20, 3)
    assert (s.client.proxy, s.client.use_proxy, s.client.odds_provider) == ("", False, 1)
    assert (s.defaults.slices, s.defaults.seasons) == (("core",), "current")
    assert dict(s.slices) == {"tennis": model.SliceOverride(("point_by_point",), ("lineups", "incidents"))}
    assert s.follows == (
        model.FollowSpec(
            kind="tournament", entity_id=17, name="premier-league", sport="football", seasons="last:2",
            slices={"include": ["core", "odds", "standings"]}, live=True,
        ),
        # Adı ve sezon seçimi verilmeyen takip: ad türden ve id'den, sezonlar [defaults]'tan
        model.FollowSpec(kind="event", entity_id=17124861, name="event-17124861", seasons="current"),
    )
    assert (s.refresh.window_hours, s.refresh.min_interval_hours) == (72.0, 6.0)
    assert (s.live.source, s.live.poll_interval_seconds, s.live.detail_slices, s.live.detail_interval_seconds) == (
        "page", 30.0, (), 20.0,
    )
    assert s.sinks == (
        model.SinkSpec(
            name="ops", type="webhook", events=("live.*", "job.finished"), url="https://example.org/hooks/sofascore",
            secret_env="SOFASCORE_HOOK_SECRET",
        ),
        model.SinkSpec(name="feed", type="file", events=("live.*",), path=str(tmp_path / "out" / "live.ndjson")),
    )
    assert s.schedule == model.ScheduleSettings(
        enabled=False, tasks=(model.ScheduleTask(run="sync", every="6h", every_seconds=21600.0),),
    )
    assert (s.server.host, s.server.port, s.server.allowed_hosts) == ("127.0.0.1", 8000, ("localhost", "127.0.0.1"))
    assert (s.log.level, s.log.format) == ("INFO", "text")
    assert loaded.warnings == ()
    for key in ("client.rate", "follows", "sinks", "slices", "schedule.tasks", "log.level"):
        assert loaded.source(key) == loader.Source("file", file_name)
    assert loaded.source("client.wait_time_min").layer == "default"
    json.dumps(loaded.describe())  # `config show --json` için: JSON'a çevrilebilir


def test_empty_config_file_changes_nothing(tmp_path):
    assert _load(tmp_path, toml="").settings == Settings()
    assert _load(tmp_path, toml="schema = 1\n").settings == Settings()


def test_precedence_from_defaults_to_flags(tmp_path):
    """default < dotenv < overrides.json < dosya < süreç ortamı (bugünkü ad < yeni ad) < bayrak."""
    key = "client.max_concurrent"

    def value(**sources: Any) -> Any:
        loaded = _load(tmp_path, **sources)
        return loaded.settings.client.max_concurrent, loaded.source(key).layer

    dotenv = {"MAX_CONCURRENT": "2"}
    assert value() == (10, "default")
    assert value(env=dotenv, dotenv=dotenv) == (2, "dotenv")
    overrides = {"client": {"max_concurrent": 3}}
    assert value(env=dotenv, dotenv=dotenv, overrides=overrides) == (3, "overrides")
    toml = "[client]\nmax_concurrent = 4\n"
    assert value(env=dotenv, dotenv=dotenv, overrides=overrides, toml=toml) == (4, "file")
    legacy = {"MAX_CONCURRENT": "5"}   # `.env`'dekinden farklı: süreç ortamından verilmiş
    assert value(env=legacy, dotenv=dotenv, overrides=overrides, toml=toml) == (5, "env")
    both = {**legacy, "SOFASCORE_CLIENT__MAX_CONCURRENT": "6"}
    assert value(env=both, dotenv=dotenv, overrides=overrides, toml=toml) == (6, "env")
    assert value(env=both, dotenv=dotenv, overrides=overrides, toml=toml, flags={key: 7}) == (7, "flag")
    assert value(env=both, toml=toml, flags={key: "8"}) == (8, "flag")
    # Ortamdaki okunamayan bugünkü ad daha zayıf katmanı ezmez
    assert value(env={"MAX_CONCURRENT": "many"}, toml=toml) == (4, "file")


def test_relative_paths_resolve_against_the_config_file_not_the_working_directory(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    conf = tmp_path / "conf"
    conf.mkdir()
    absolute = tmp_path / "abs" / "throttle"
    toml = (
        # Mutlak yol TOML'un tek tırnaklı (kaçışsız) dizgesiyle: Windows yollarındaki ters bölü olduğu gibi kalır
        f"[storage]\ndata_dir = \"../store\"\n[client]\nbrowser_profile = \"profile\"\nthrottle_dir = '{absolute}'\n"
        '[log]\ndir = "logs"\n[[sink]]\nname = "f"\ntype = "file"\npath = "out/x.ndjson"\n'
    )
    loaded = _load(conf, toml=toml, env={"SOFASCORE_LOG__DIR": "envlogs"}, overrides={"client": {"browser_profile": "ui"}})
    s = loaded.settings
    assert s.storage.data_dir == str(tmp_path / "store")
    assert s.client.browser_profile == str(conf / "profile")
    assert s.client.throttle_dir == str(absolute)
    assert s.log.dir == str(conf / "envlogs")          # SOFASCORE_*__* değeri de dosyanın dizinine göre
    assert s.sinks[0].path == str(conf / "out" / "x.ndjson")
    # Yapılandırma dosyası yokken bugünkü adlar yazıldığı gibi kalır (bugünkü davranış: çalışma dizinine göre)
    assert _load(env={"DATA_DIR": "relative/data"}).settings.storage.data_dir == "relative/data"
    # ... dosya varken de: bugünkü ad bugünkü anlamını korur
    assert _load(conf, toml=toml, env={"DATA_DIR": "relative/data"}).settings.storage.data_dir == "relative/data"


def test_config_file_is_searched_in_the_documented_order(tmp_path, monkeypatch):
    cwd, config_dir = tmp_path / "cwd", tmp_path / "config"
    for d in (cwd, config_dir):
        d.mkdir()
    monkeypatch.chdir(cwd)
    env = {"SOFASCORE_CONFIG_DIR": str(config_dir)}
    assert loader.find_config_file(None, env) is None

    in_config_dir = config_dir / "sofascore.toml"
    in_config_dir.write_text("", encoding="utf-8")
    assert loader.find_config_file(None, env) == in_config_dir
    in_cwd = cwd / "sofascore.toml"
    in_cwd.write_text("", encoding="utf-8")
    assert loader.find_config_file(None, env) == in_cwd
    named = tmp_path / "named.toml"
    named.write_text("", encoding="utf-8")
    assert loader.find_config_file(None, {**env, "SOFASCORE_CONFIG": str(named)}) == named
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("", encoding="utf-8")
    assert loader.find_config_file(explicit, {**env, "SOFASCORE_CONFIG": str(named)}) == explicit
    assert loader.find_config_file(None, {**env, "SOFASCORE_CONFIG": "  "}) == in_cwd   # boş = verilmemiş
    # "none" aramayı kapatır (testler böyle yalıtılır); açıkça verilen dosya yine okunur
    assert loader.find_config_file(None, {**env, "SOFASCORE_CONFIG": "None"}) is None
    assert loader.find_config_file(explicit, {**env, "SOFASCORE_CONFIG": "none"}) == explicit

    # Adı açıkça verilen dosya yoksa sessizce başka bir dosyaya düşülmez
    with pytest.raises(ConfigError, match="--config: config file not found"):
        loader.find_config_file(tmp_path / "missing.toml", env)
    with pytest.raises(ConfigError, match="SOFASCORE_CONFIG: config file not found"):
        loader.find_config_file(None, {**env, "SOFASCORE_CONFIG": str(tmp_path / "missing.toml")})


@pytest.mark.parametrize(
    "toml,message",
    [
        ("[client\n", "not valid TOML"),
        ("schema = 2\n", "schema = 2 is not supported"),
        ("schema = true\n", "is not supported"),
        ("[clinet]\nrate = 1\n", r"unknown section \[clinet\]"),
        ("[client]\nratee = 1\n", r"\[client\]: unknown key 'ratee'"),
        ("client = 5\n", r"\[client\]: expected a table"),
        ('[client]\nrate = "fast"\n', r'\[client\] rate: expected a number or "off"'),
        ("[client]\nrate = -1\n", r"\[client\] rate: must be at least 0"),
        ("[client]\nrate = true\n", r"\[client\] rate: expected a number"),
        ('[client]\nmax_concurrent = "4"\n', r"\[client\] max_concurrent: expected a whole number, got '4'"),
        ("[client]\nmax_concurrent = 0\n", "must be at least 1"),
        ("[client]\nmax_concurrent = 2.5\n", "expected a whole number"),
        ("[client]\nuse_proxy = 1\n", "expected true or false"),
        ('[client]\nbase_url = "ftp://example.invalid"\n', r"\[client\] base_url: expected an http:// or https:// address"),
        ('[client]\nbase_url = ""\n', "base_url: expected an http:// or https:// address"),
        ('[client]\ncaptcha_token = "x"\n', "read from the environment only .SOFASCORE_CLIENT__CAPTCHA_TOKEN."),
        ('[server]\ntoken = "x"\n', "read from the environment only"),
        ("[server]\nport = 70000\n", "must be at most 65535"),
        ('[server]\nallowed_hosts = "localhost"\n', "expected a list of strings"),
        ("[live]\npoll_interval_seconds = 0\n", "must be greater than 0"),
        ('[live]\nsource = "carrier-pigeon"\n', r"\[live\] source: expected one of page, direct, poll, got 'carrier-pigeon'"),
        ('[live]\nsources = ["page", "poll"]\n', r"\[live\]: unknown key 'sources'"),
        ("[live]\nenabled = true\n", r"\[live\]: unknown key 'enabled'"),
        ('[live]\ndetail_slices = ["incidents", 5]\n', "detail_slices: expected a list of strings"),
        ('[log]\nlevel = "loud"\n', "expected one of DEBUG, INFO, WARNING, ERROR, CRITICAL"),
        ('[defaults]\nseasons = "last:0"\n', r'expected "current", "all", "last:N" or a list of season ids'),
        ("[defaults]\nseasons = []\n", "a list of season ids"),
        ("[slices.curling]\nenable = []\n", r"\[slices\].curling: unknown sport"),
        ('[slices.tennis]\nonly = ["x"]\n', "unknown key 'only'; expected enable, disable"),
        ('[client]\nproxy = "http://p:1"\nproxy_env = "PROXY_URL"\n', "give .client. proxy or proxy_env, not both"),
        ('[client]\nproxy_env = "MY_PROXY"\n', "proxy_env: the environment variable MY_PROXY is not set"),
        ('[client]\nproxy_env = "not a name"\n', "expected the name of an environment variable"),
        ('[server]\ntoken_env = "MY_TOKEN"\n', "token_env: the environment variable MY_TOKEN is not set"),
        # takipler
        ("[[follow]]\nname = 'x'\n", r"\[\[follow\]\] #1: give exactly one of tournament, team, player, event"),
        ("[[follow]]\ntournament = 17\nevent = 5\n", "give exactly one of"),
        ("[[follow]]\ntournament = 0\n", "expected a positive id"),
        ("[[follow]]\ntournament = '17'\n", "tournament: expected a whole number, got '17'"),
        ("[[follow]]\ntournament = 17\nsport = 'curling'\n", "unknown sport 'curling'"),
        ("[[follow]]\ntournament = 17\nseasons = 'next'\n", r"\[\[follow\]\] #1 seasons: expected"),
        ("[[follow]]\ntournament = 17\nslices = 5\n", "slices: expected a list of slice names or a table"),
        ("[[follow]]\ntournament = 17\nslices = {only = ['x']}\n", "slices: unknown key 'only'"),
        ("[[follow]]\ntournament = 17\ncolour = 'red'\n", "unknown key 'colour'"),
        ("[[follow]]\ntournament = 17\n[[follow]]\ntournament = 17\n", "#2: tournament 17 is already followed by #1"),
        ("[[follow]]\ntournament = 17\nname = 'a'\n[[follow]]\ntournament = 8\nname = 'a'\n", "the name 'a' is already used by #1"),
        ("follow = 5\n", r"\[\[follow\]\]: expected a list of tables"),
        # hedefler
        ("[[sink]]\ntype = 'stdout'\n", r"\[\[sink\]\] #1: name is required"),
        ("[[sink]]\nname = 'a'\ntype = 'kafka'\n", "type: expected one of stdout, file, webhook"),
        ("[[sink]]\nname = 'a'\ntype = 'webhook'\nsecret_env = 'S'\n", "a webhook needs an http:// or https:// address"),
        ("[[sink]]\nname = 'a'\ntype = 'webhook'\nurl = 'https://x.example'\n", "needs secret_env .or allow_unsigned = true."),
        ("[[sink]]\nname = 'a'\ntype = 'file'\n", "a file sink needs a path"),
        ("[[sink]]\nname = 'a'\ntype = 'stdout'\npath = 'x'\n", "only a file sink takes a path"),
        ("[[sink]]\nname = 'a'\ntype = 'stdout'\nurl = 'https://x.example'\n", "only a webhook sink takes a url"),
        ("[[sink]]\nname = 'a'\ntype = 'stdout'\nevents = 'live.*'\n", "events: expected a list of strings"),
        ("[[sink]]\nname = 'a'\ntype = 'stdout'\n[[sink]]\nname = 'a'\ntype = 'stdout'\n", "the name 'a' is already used by #1"),
        # zamanlama
        ("[[schedule.task]]\nevery = '6h'\n", r"\[\[schedule.task\]\] #1: run is required"),
        ("[[schedule.task]]\nrun = 'sync'\n", "give exactly one of every, cron"),
        ("[[schedule.task]]\nrun = 'sync'\nevery = '6h'\ncron = '* * * * *'\n", "give exactly one of every, cron"),
        ("[[schedule.task]]\nrun = 'sync'\nevery = 'often'\n", "every: expected a duration"),
        ("[[schedule.task]]\nrun = 'sync'\nevery = '0h'\n", "every: expected a duration"),
        ("[[schedule.task]]\nrun = 'sync'\ncron = 'hourly'\n", "cron: expected five fields"),
    ],
)
def test_config_file_errors_name_the_place(tmp_path, toml, message):
    """Yanlış yazılmış ya da yanlış türde bir ayar sessizce yok sayılmaz; hata dosyayı ve anahtarı söyler."""
    with pytest.raises(ConfigError, match=message) as error:
        _load(tmp_path, toml=toml)
    assert "sofascore.toml" in str(error.value)


def test_follows_sinks_and_tasks_in_detail(tmp_path):
    toml = '''
[defaults]
seasons = "all"
[[follow]]
team = 42
sport = "basketball"
seasons = [61627, 52186, 61627]
slices = { enable = ["odds"], disable = ["lineups"] }
enabled = false
[[follow]]
player = 7
name = "  A Player  "
[[sink]]
name = "hook"
type = "webhook"
url = "http://user:pw@127.0.0.1:9000/in"
allow_unsigned = true
batch_size = 50
[[sink]]
name = "out"
type = "stdout"
[[schedule.task]]
run = "sync"
cron = "15 */6 * * *"
follow = ["premier-league"]
[[schedule.task]]
run = "refresh"
every = "90 M"
'''
    loaded = _load(tmp_path, toml=toml)
    s = loaded.settings
    assert s.follows == (
        model.FollowSpec(
            kind="team", entity_id=42, name="team-42", sport="basketball", seasons=(61627, 52186),
            slices={"enable": ["odds"], "disable": ["lineups"]}, enabled=False,
        ),
        model.FollowSpec(kind="player", entity_id=7, name="A Player", seasons="all"),
    )
    hook, out = s.sinks
    assert (hook.allow_unsigned, hook.secret_env, dict(hook.options)) == (True, "", {"batch_size": 50})
    assert (out.type, out.events, dict(out.options)) == ("stdout", ("*",), {})
    assert s.schedule.tasks == (
        model.ScheduleTask(run="sync", cron="15 */6 * * *", options={"follow": ["premier-league"]}),
        model.ScheduleTask(run="refresh", every="90 M", every_seconds=5400.0),
    )
    rows = {row["key"]: row["value"] for row in loaded.describe()}
    assert rows["sinks"][0]["url"] == "http://***@127.0.0.1:9000/in"    # adresteki kimlik bilgisi gösterilmez
    assert "pw" not in repr(s)
    assert loader.parse_duration("30s") == 30.0 and loader.parse_duration("1d") == 86400.0


def test_proxy_and_token_come_from_named_environment_variables(tmp_path):
    env = {"MY_PROXY": " http://u:p@proxy.example.com:8080 ", "MY_TOKEN": "s3cret-token", "USE_PROXY": "false"}
    toml = '[client]\nproxy_env = "MY_PROXY"\n[server]\ntoken_env = "MY_TOKEN"\n'
    loaded = _load(tmp_path, toml=toml, env=env, dotenv={"USE_PROXY": "false"})
    s = loaded.settings
    assert (s.client.proxy, s.server.token) == ("http://u:p@proxy.example.com:8080", "s3cret-token")
    assert loaded.source("client.proxy") == loader.Source("file", "MY_PROXY")
    assert loaded.source("server.token") == loader.Source("file", "MY_TOKEN")
    # Dosyada proxy verilince kullanılır: `.env`'deki USE_PROXY=false (zayıf katman) onu kapatmaz ...
    assert s.client.use_proxy is True
    # ... ama aynı dosyadaki ya da süreç ortamındaki açık bir "hayır" kapatır
    assert _load(tmp_path, toml=toml, env=env).settings.client.use_proxy is False
    off = '[client]\nproxy = "http://proxy.example.com:1"\nuse_proxy = false\n'
    assert _load(tmp_path, toml=off).settings.client.use_proxy is False
    assert _load(tmp_path, toml='[client]\nproxy = "http://proxy.example.com:1"\n').settings.client.use_proxy is True
    assert _load(env={"SOFASCORE_CLIENT__PROXY": "http://proxy.example.com:1"}).settings.client.use_proxy is True
    # Bugünkü adlarla kural değişmez: PROXY_URL tek başına proxy'yi açmaz
    assert _load(env={"PROXY_URL": "http://proxy.example.com:1"}).settings.client.use_proxy is False
    # token_env boşsa bugünkü ad geçerlidir
    legacy = _load(tmp_path, toml='[server]\ntoken_env = ""\n', env={"SOFASCORE_API_TOKEN": " abc "})
    assert legacy.settings.server.token == "abc"
    assert legacy.source("server.token") == loader.Source("env", "SOFASCORE_API_TOKEN", legacy=True)


def test_new_style_environment_variables(tmp_path):
    data_dir = str(tmp_path / "data-from-env")
    env = {
        "SOFASCORE_CLIENT__RATE": "off",
        "SOFASCORE_CLIENT__USE_PROXY": "yes",
        "SOFASCORE_SERVER__PORT": "9000",
        "SOFASCORE_SERVER__ALLOWED_HOSTS": "localhost, my-server.lan",
        "SOFASCORE_LIVE__SOURCE": "POLL",
        "SOFASCORE_LIVE__DETAIL_SLICES": '["incidents", "statistics"]',
        "SOFASCORE_DEFAULTS__SEASONS": "[1, 2]",
        "SOFASCORE_STORAGE__DATA_DIR": data_dir,
        "SOFASCORE_LOG__LEVEL": "debug",
        "SOFASCORE_SERVER__TOKEN": "from-env",
        "SOFASCORE_FOLLOWS": '[{"sport": "football", "tournament": 17}]',
        "SOFASCORE_SINKS": '[{"name": "out", "type": "stdout"}]',
        "SOFASCORE_SLICES": '{"football": {"disable": ["h2h"]}}',
        "SOFASCORE_SCHEDULE__TASKS": '[{"run": "sync", "every": "1h"}]',
        # Çift alt çizgisi olmayan SOFASCORE_ adları ayar değildir (bugünkü adlar, gizli değer taşıyanlar)
        "SOFASCORE_HOOK_SECRET": "x", "SOFASCORE_CONFIG_DIR": "config",
    }
    toml = "[[follow]]\ntournament = 8\n[slices.tennis]\ndisable = ['lineups']\n[slices.football]\nenable = ['odds']\n"
    loaded = _load(tmp_path, toml=toml, env=env)
    s = loaded.settings
    assert (s.client.rate, s.client.use_proxy, s.server.port) == (0.0, True, 9000)
    assert s.server.allowed_hosts == ("localhost", "my-server.lan")
    assert (s.live.source, s.defaults.seasons, s.storage.data_dir, s.log.level) == ("poll", (1, 2), data_dir, "DEBUG")
    assert s.live.detail_slices == ("incidents", "statistics")
    assert s.server.token == "from-env"
    # Listeler bütün olarak yer değiştirir; dilim seçimi spor spor birleşir
    assert [(f.kind, f.entity_id, f.seasons) for f in s.follows] == [("tournament", 17, (1, 2))]
    assert [sink.name for sink in s.sinks] == ["out"]
    assert dict(s.slices) == {
        "tennis": model.SliceOverride(disable=("lineups",)), "football": model.SliceOverride(disable=("h2h",)),
    }
    assert s.schedule.tasks[0].every_seconds == 3600.0
    # Boş bırakılan gizli değer verilmemiş sayılır: bugünkü adla verilen belirteci silmez
    kept = _load(env={"SOFASCORE_SERVER__TOKEN": " ", "SOFASCORE_API_TOKEN": "legacy-token"})
    assert kept.settings.server.token == "legacy-token"
    assert loaded.source("follows") == loader.Source("env", "SOFASCORE_FOLLOWS")
    assert loaded.source("client.rate") == loader.Source("env", "SOFASCORE_CLIENT__RATE")
    assert loader.env_name("client.rate") == "SOFASCORE_CLIENT__RATE"


@pytest.mark.parametrize(
    "env,message",
    [
        ({"SOFASCORE_CLIENT__RATEE": "1"}, r"SOFASCORE_CLIENT__RATEE: unknown setting \(no \[client\] ratee\)"),
        ({"SOFASCORE_CLINET__RATE": "1"}, "SOFASCORE_CLINET__RATE: unknown setting"),
        ({"SOFASCORE_CLIENT__RATE": "fast"}, "SOFASCORE_CLIENT__RATE: expected a number"),
        ({"SOFASCORE_CLIENT__USE_PROXY": "maybe"}, "SOFASCORE_CLIENT__USE_PROXY: expected true or false"),
        ({"SOFASCORE_LIVE__DETAIL_SLICES": "[1"}, "SOFASCORE_LIVE__DETAIL_SLICES: expected a JSON list"),
        ({"SOFASCORE_LIVE__SOURCE": "push"}, "SOFASCORE_LIVE__SOURCE: expected one of page, direct, poll"),
        ({"SOFASCORE_FOLLOWS": "{}"}, "SOFASCORE_FOLLOWS: expected a JSON list"),
        ({"SOFASCORE_FOLLOWS": "[1]"}, "SOFASCORE_FOLLOWS: expected a list of tables"),
        ({"SOFASCORE_FOLLOWS": '[{"tournament": 17, "event": 3}]'}, r"SOFASCORE_FOLLOWS: \[\[follow\]\] #1: give exactly one"),
        ({"SOFASCORE_SINKS": "nope"}, "SOFASCORE_SINKS: not valid JSON"),
        ({"SOFASCORE_SLICES": "[]"}, "SOFASCORE_SLICES: expected a JSON object"),
    ],
)
def test_new_style_environment_errors(env, message):
    with pytest.raises(ConfigError, match=message):
        _load(env=env)


def test_flags(tmp_path):
    loaded = _load(flags={"storage.data_dir": "/flag/data", "client.rate": "off", "log.debug": True})
    assert (loaded.settings.storage.data_dir, loaded.settings.client.rate, loaded.settings.log.debug) == (
        "/flag/data", 0.0, True,
    )
    assert loaded.source("client.rate") == loader.Source("flag", "client.rate")
    with pytest.raises(ConfigError, match="flag client.nope: unknown setting"):
        _load(flags={"client.nope": 1})
    with pytest.raises(ConfigError, match="flag client.rate: must be at least 0"):
        _load(flags={"client.rate": -2})


def test_overrides_file(tmp_path):
    loaded = _load(tmp_path, overrides={"client": {"rate": 2}, "slices": {"tennis": {"disable": ["lineups"]}}})
    assert loaded.settings.client.rate == 2.0
    assert loaded.source("client.rate") == loader.Source("overrides", str(tmp_path / "overrides.json"))
    assert loaded.source("client.rate").locked is False     # arayüzün yazdığı değer kilitli değildir
    assert loaded.overrides_file == str(tmp_path / "overrides.json")
    assert dict(loaded.settings.slices) == {"tennis": model.SliceOverride(disable=("lineups",))}
    # Hedefler ve takipler arayüzün dosyasından verilemez (karar D11)
    for doc, what in (
        ({"sink": [{"name": "a", "type": "stdout"}]}, "sink"), ({"follow": [{"tournament": 17}]}, "follow"),
        ({"schedule": {"task": [{"run": "sync", "every": "1h"}]}}, "schedule.task"),
    ):
        with pytest.raises(ConfigError, match=rf"\[\[{what}\]\] can only be given in the config file or the environment"):
            _load(tmp_path, overrides=doc)
    (tmp_path / "overrides.json").write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="overrides.json: not valid JSON"):
        loader.load_settings(config_file=None, environ={}, dotenv_values={}, overrides_file=tmp_path / "overrides.json")
    (tmp_path / "overrides.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a JSON object"):
        loader.load_settings(config_file=None, environ={}, dotenv_values={}, overrides_file=tmp_path / "overrides.json")


def test_warnings(tmp_path):
    """
    `direct` canlı kaynağı yalnızca açıkça yazılırsa seçilir (02-services.md 8.3) ve seçildiği her yerde dört
    uyarısı söylenir: dosyada, yeni ortam değişkeninde, bayrakta.
    """
    assert Settings().live.source == "page" and _load().warnings == ()
    for chosen in (
        _load(tmp_path, toml='[live]\nsource = "direct"\n'),
        _load(env={"SOFASCORE_LIVE__SOURCE": "direct"}),
        _load(flags={"live.source": "direct"}),
    ):
        assert chosen.settings.live.source == "direct"
        assert [w.code for w in chosen.warnings] == ["live_direct_source"]
        text = str(chosen.warnings[0])
        for point in ("own client credential", "break without notice", "IP address blocked", "terms-of-use grey area"):
            assert point in text
    assert _load(tmp_path, toml='[live]\nsource = "poll"\n').warnings == ()
    # Yapılandırma dosyası varken süreç ortamındaki bugünkü adlar için uyarı; `.env`'dekiler ve dosyasız durum sessiz
    env = {"MAX_CONCURRENT": "5", "DATA_DIR": "d"}
    with_file = _load(tmp_path, toml="", env=env, dotenv={"DATA_DIR": "d"})
    assert [(w.code, w.message) for w in with_file.warnings] == [(
        "legacy_name",
        "MAX_CONCURRENT is a legacy name; use SOFASCORE_CLIENT__MAX_CONCURRENT or the config file (client.max_concurrent).",
    )]
    assert _load(env=env).warnings == ()


def test_missing_toml_reader_is_a_config_error(tmp_path, monkeypatch):
    """Python 3.10'da tomli kurulu değilse: dosya yokken sorun yok, dosya varsa ne yapılacağını söyleyen hata."""
    import builtins

    real_import = builtins.__import__

    def without_toml(name, *args, **kwargs):
        if name in ("tomllib", "tomli"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_toml)
    assert _load().settings == Settings()
    with pytest.raises(ConfigError, match="needs the tomli package"):
        _load(tmp_path, toml="")


# === etkin ayarlar: ConfigManager ve ortam köprüsü =================================================


@pytest.fixture
def active(monkeypatch):
    """
    Etkin ayarları sıfırdan kurdurur; testten sonra test yapılandırma dizinindeki dosyaları siler ve köprünün
    ortama yazdıklarını geri alır. `write(toml)` dosyayı yazıp ayarları yeniden yükler. conftest dosya aramayı
    kapatır (SOFASCORE_CONFIG=none); burada değişken yazılan dosyaya çevrilir.
    """
    config_dir = Path(conftest.CONFIG_DIR)
    created = [config_dir / loader.CONFIG_FILE_NAME, config_dir / loader.OVERRIDES_FILE_NAME]
    loader.reset()

    def write(toml: str) -> loader.LoadedSettings:
        created[0].write_text(toml, encoding="utf-8")
        monkeypatch.setenv(loader.CONFIG_ENV, str(created[0]))
        return loader.reload()

    yield write
    for path in created:
        path.unlink(missing_ok=True)
    loader.reset()


def test_without_a_config_file_nothing_is_written_to_the_environment(active):
    before = dict(os.environ)
    loaded = loader.active()
    cm = ConfigManager()
    assert cm.get_settings() is loaded.settings is loader.active_settings()   # ortam değişmedikçe aynı nesne
    assert loaded.config_file is None and loaded.overrides_file is None
    assert loader.projection(loaded) == {}
    assert dict(os.environ) == before
    # conftest: MAX_CONCURRENT test `.env`'inden, DATA_DIR ve REQUEST_RATE_LIMIT süreç ortamından gelir
    assert loaded.source("client.max_concurrent") == loader.Source("dotenv", "MAX_CONCURRENT", legacy=True)
    assert loaded.source("storage.data_dir") == loader.Source("env", "DATA_DIR", legacy=True)
    assert (cm.get_max_concurrent(), cm.get_data_dir(), cm.get_request_rate_limit()) == (5, conftest.DATA_DIR, 0.0)


GETTERS = {
    # getter -> (değişken, bugünkü kural: ham değer -> sonuç, varsayılan)
    "get_data_dir": ("DATA_DIR", lambda raw: raw, "data"),
    "get_api_base_url": ("API_BASE_URL", lambda raw: raw, "https://www.sofascore.com/api/v1"),
    "get_use_proxy": ("USE_PROXY", lambda raw: raw.lower() == "true", False),
    "get_proxy_url": ("PROXY_URL", lambda raw: raw, ""),
    "get_use_color": ("USE_COLOR", lambda raw: raw.lower() == "true", True),
    "get_date_format": ("DATE_FORMAT", lambda raw: raw, "%Y-%m-%d %H:%M:%S"),
    "get_max_concurrent": ("MAX_CONCURRENT", int, 10),
    "get_wait_time_min": ("WAIT_TIME_MIN", float, 0.2),
    "get_wait_time_max": ("WAIT_TIME_MAX", float, 0.5),
    "get_request_timeout": ("REQUEST_TIMEOUT", int, 10),
    "get_max_retries": ("MAX_RETRIES", int, 3),
    "get_rate_limit_threshold_consecutive": ("RATE_LIMIT_THRESHOLD_CONSECUTIVE", int, 20),
    "get_rate_limit_threshold_ratio": ("RATE_LIMIT_THRESHOLD_RATIO", float, 0.9),
    "get_server_error_threshold_consecutive": ("SERVER_ERROR_THRESHOLD_CONSECUTIVE", int, 50),
}


@pytest.mark.parametrize("getter", sorted(GETTERS))
def test_config_manager_getter_resolves_as_before(active, monkeypatch, caplog, getter):
    """
    Her getter, P09'dan önceki gövdesinin kuralıyla aynı sonucu verir: `os.getenv(AD, varsayılan)`, sayılarda
    int()/float(), okunamazsa uyarı ve varsayılan. Ortam değişikliği bir sonraki çağrıda görülür.
    """
    name, rule, default = GETTERS[getter]
    read = getattr(ConfigManager(), getter)
    monkeypatch.delenv(name, raising=False)
    assert _same(read(), default)
    for raw in RAW_VALUES + ("inf", "10.5", "-", "90%", "010"):
        monkeypatch.setenv(name, raw)
        caplog.clear()
        try:
            expected, warned = rule(raw), False
        except ValueError:
            expected, warned = default, True
        assert _same(read(), expected), repr(raw)
        warnings = [r.getMessage() for r in caplog.records if r.name == "ConfigManager" and r.levelno == logging.WARNING]
        assert warnings == ([f"{name} is not valid; using the default {default}."] if warned else []), repr(raw)


def test_config_manager_rate_and_language_getters(active, monkeypatch):
    cm = ConfigManager()
    monkeypatch.setattr(throttle, "_warned_invalid_rate", None)
    for raw in RAW_VALUES + ("inf", "2.5"):
        monkeypatch.setenv("REQUEST_RATE_LIMIT", raw)
        assert _same(cm.get_request_rate_limit(), throttle.configured_rate()), repr(raw)
    monkeypatch.delenv("REQUEST_RATE_LIMIT")
    assert cm.get_request_rate_limit() == throttle.DEFAULT_RATE_LIMIT

    from src.i18n import app_language

    for env in ({}, {"APP_LANGUAGE": "tr"}, {"LANGUAGE": "tr"}, {"APP_LANGUAGE": "de"}, {"LC_MESSAGES": "tr_TR.UTF-8"}):
        with monkeypatch.context() as patch:
            for key in language.ENV_KEYS:
                patch.delenv(key, raising=False)
            for key, value in env.items():
                patch.setenv(key, value)
            assert cm.get_language() == app_language(), env
    assert cm.get_match_data_dir() == os.path.join(cm.get_data_dir(), "matches")


def test_config_file_is_honoured_by_getters_and_by_modules_that_read_the_environment(active, monkeypatch):
    """
    Dosyadaki değer getter'lara yansır ve köprüyle, ayarı hâlâ ortamdan okuyan modüllere de. Dosya kalkınca
    ortam eski haline döner.
    """
    for name in ("DATA_DIR", "SOFASCORE_ALLOWED_HOSTS"):   # conftest süreç ortamından verir; öyle kalsa dosyayı ezerdi
        monkeypatch.delenv(name)
    before = dict(os.environ)
    config_dir = Path(conftest.CONFIG_DIR)
    loaded = active(
        '[storage]\ndata_dir = "store"\n'
        "[client]\nmax_concurrent = 4\nrate = 2\ntimeout_seconds = 33\n"
        "[refresh]\nwindow_hours = 12\n[bridge]\ndegraded_after = 6\n"
        '[server]\nallowed_hosts = ["localhost", "box.lan"]\n[display]\nlanguage = "tr"\n[fetch]\nonly_finished = false\n'
    )
    cm = ConfigManager()
    assert loaded.config_file == str(config_dir / "sofascore.toml")
    # `.env`'deki MAX_CONCURRENT=5 dosyanın altında kalır; süreç ortamındaki REQUEST_RATE_LIMIT=0 üstünde
    assert (cm.get_max_concurrent(), cm.get_request_timeout(), cm.get_request_rate_limit()) == (4, 33, 0.0)
    assert cm.get_data_dir() == str(config_dir / "store")
    assert cm.get_language() == "tr"
    assert loaded.source("client.rate") == loader.Source("env", "REQUEST_RATE_LIMIT", legacy=True)
    assert any("REQUEST_RATE_LIMIT is a legacy name" in w.message for w in loaded.warnings)

    # Köprü: ortamı doğrudan okuyanlar da dosyayı görür
    assert os.environ["DATA_DIR"] == str(config_dir / "store")
    assert os.environ["MAX_CONCURRENT"] == "4"
    assert os.environ["FETCH_ONLY_FINISHED"] == "false"
    assert refresh.refresh_window_hours() == 12.0
    assert bridge_health.thresholds()["degraded_after"] == 6
    assert security.allowed_hosts() == ["localhost", "box.lan"]
    assert language.resolve_language() == "tr"
    assert throttle.configured_rate() == 0.0                       # süreç ortamındaki değere dokunulmadı
    assert os.environ["REQUEST_RATE_LIMIT"] == "0"
    assert loader.active() is loaded                               # köprünün yazdıkları yeni bir yüklemeye yol açmaz

    # Süreç ortamından verilen değer dosyayı ezer ve hemen görülür
    monkeypatch.setenv("MAX_CONCURRENT", "9")
    assert cm.get_max_concurrent() == 9
    monkeypatch.setenv("SOFASCORE_CLIENT__MAX_CONCURRENT", "11")
    assert cm.get_max_concurrent() == 11 and os.environ["MAX_CONCURRENT"] == "11"
    monkeypatch.delenv("SOFASCORE_CLIENT__MAX_CONCURRENT")
    monkeypatch.delenv("MAX_CONCURRENT")
    assert cm.get_max_concurrent() == 4

    # Dosya kalkıp yeniden yüklenince ortam, köprüden önceki haline döner
    (config_dir / "sofascore.toml").unlink()
    monkeypatch.setenv(loader.CONFIG_ENV, "none")
    loader.reload()
    monkeypatch.setenv("MAX_CONCURRENT", "5")                      # test `.env`'inin yüklediği değer
    assert dict(os.environ) == before
    assert cm.get_max_concurrent() == 5 and cm.get_data_dir() == "data"


def test_settings_page_write_does_not_override_the_config_file(active):
    """
    Ayarlar sayfası `.env`'e yazar (update_env_variable). Dosyanın sabitlediği ayar kilitlidir: `.env` değeri
    dosyanın altında kalır. Dosyada olmayan ayar eskisi gibi değişir.
    """
    active("[client]\nmax_concurrent = 4\n")
    cm = ConfigManager()
    env_file = Path(conftest.ENV_FILE)
    original = env_file.read_text(encoding="utf-8")
    try:
        assert cm.update_env_variable("MAX_CONCURRENT", "8") is True
        assert cm.update_env_variable("MAX_RETRIES", "6") is True
        assert (cm.get_max_concurrent(), cm.get_max_retries()) == (4, 6)
        loaded = loader.active()
        assert loaded.source("client.max_concurrent").locked is True
        assert loaded.source("client.retries") == loader.Source("dotenv", "MAX_RETRIES", legacy=True)
        assert os.environ["MAX_CONCURRENT"] == "4"
        assert "MAX_CONCURRENT='8'" in env_file.read_text(encoding="utf-8")
    finally:
        env_file.write_text(original, encoding="utf-8")
        os.environ.pop("MAX_RETRIES", None)
        os.environ["MAX_CONCURRENT"] = "5"


def test_reload_keeps_the_process_environment_above_dotenv(active, monkeypatch):
    """
    #43'te bulunan hata: yeniden yükleme `.env`'i ortamın üzerine yazıyordu, kabuktan ya da `docker -e` ile
    verilen değer `.env`'deki (boş olabilen) satıra yeniliyordu. Süreç ortamı `.env`'in önündedir; `.env`'den
    gelen değerler ise eskisi gibi yenilenir.
    """
    env_file = Path(conftest.ENV_FILE)
    original = env_file.read_text(encoding="utf-8")
    cm = ConfigManager()
    monkeypatch.setenv("REQUEST_TIMEOUT", "30")                      # süreç ortamından
    monkeypatch.setenv("SOFASCORE_ALLOWED_HOSTS", "my-server.lan")
    monkeypatch.setenv("MAX_RETRIES", "3")                           # aşağıda .env'den gelecek; test sonunda geri alınır
    monkeypatch.delenv("MAX_RETRIES")
    assert loader.active().source("client.timeout_seconds").layer == "env"
    try:
        env_file.write_text(
            original + "REQUEST_TIMEOUT=20\nSOFASCORE_ALLOWED_HOSTS=\nMAX_RETRIES=7\nMAX_CONCURRENT=6\n", encoding="utf-8",
        )
        assert cm.reload_config() is True
        assert os.environ["REQUEST_TIMEOUT"] == "30" and cm.get_request_timeout() == 30
        assert os.environ["SOFASCORE_ALLOWED_HOSTS"] == "my-server.lan"       # boş satır değeri silmedi
        assert security.allowed_hosts() == ["my-server.lan"]
        # `.env`'den gelmiş (MAX_CONCURRENT) ve oraya yeni eklenmiş (MAX_RETRIES) değerler eskisi gibi yenilenir
        assert (cm.get_max_concurrent(), cm.get_max_retries()) == (6, 7)
        loaded = loader.active()
        assert loaded.source("client.timeout_seconds") == loader.Source("env", "REQUEST_TIMEOUT", legacy=True)
        assert loaded.source("client.retries") == loader.Source("dotenv", "MAX_RETRIES", legacy=True)
        # Ayarlar sayfasının yazdığı değer eskisi gibi hemen geçerlidir ve yeniden yüklemede de kalır
        assert cm.update_env_variable("REQUEST_TIMEOUT", "45") is True
        assert cm.reload_config() is True
        assert cm.get_request_timeout() == 45
        assert loader.active().source("client.timeout_seconds").layer == "dotenv"
    finally:
        env_file.write_text(original, encoding="utf-8")
        os.environ["MAX_CONCURRENT"] = "5"


def test_config_file_is_reread_only_on_reload(active, monkeypatch):
    path = Path(conftest.CONFIG_DIR) / "sofascore.toml"
    active("[client]\nretries = 4\n")
    cm = ConfigManager()
    assert cm.get_max_retries() == 4
    path.write_text("[client]\nretries = 6\n", encoding="utf-8")
    monkeypatch.setenv("WAIT_TIME_MIN", "1.5")          # ortam değişti: ayarlar yeniden kurulur, dosya okunmaz
    assert (cm.get_wait_time_min(), cm.get_max_retries()) == (1.5, 4)
    assert cm.reload_config() is True
    assert cm.get_max_retries() == 6

    # Bozuk dosya: yeniden yükleme başarısız, önceki ayarlar yürürlükte
    path.write_text("[client]\nretries = 'many'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="retries: expected a whole number"):
        loader.reload()
    assert cm.get_max_retries() == 6
    assert cm.reload_config() is False
    assert cm.get_max_retries() == 6


def test_activate_takes_an_explicit_file_and_flags(active, tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR")
    explicit = tmp_path / "other.toml"
    explicit.write_text('[storage]\ndata_dir = "from-file"\n[client]\nretries = 9\n', encoding="utf-8")
    loaded = loader.activate(config_file=explicit, flags={"storage.data_dir": str(tmp_path / "from-flag")})
    cm = ConfigManager()
    assert loaded.config_file == str(explicit)
    assert (cm.get_data_dir(), cm.get_max_retries()) == (str(tmp_path / "from-flag"), 9)
    assert os.environ["DATA_DIR"] == str(tmp_path / "from-flag")
    loader.reset()
    assert "DATA_DIR" not in os.environ
    assert cm.get_max_retries() == 3


def test_secrets_named_by_the_config_file_reach_today_s_readers_and_are_masked(active, monkeypatch):
    """
    token_env / proxy_env başka bir değişkeni gösterse de değer, belirteci ve proxy'yi bugün ortamdan okuyan
    koda (src/web/security.py, src/challenge_solver.py) bugünkü adıyla ulaşır ve loglarda maskelenir.
    """
    from src import redact

    token, proxy = "tok-0123456789abcdef", "http://scraper:pr0xy-passw0rd@proxy.example.com:8080"
    monkeypatch.setenv("MY_TOKEN", token)
    monkeypatch.setenv("MY_PROXY", proxy)
    active('[server]\ntoken_env = "MY_TOKEN"\n[client]\nproxy_env = "MY_PROXY"\n')
    cm = ConfigManager()
    assert os.environ["SOFASCORE_API_TOKEN"] == token
    assert (cm.get_use_proxy(), cm.get_proxy_url()) == (True, proxy)
    assert (os.environ["USE_PROXY"], os.environ["PROXY_URL"]) == ("true", proxy)
    text = redact.redact_text(f"token {token} via pr0xy-passw0rd")
    assert token not in text and "pr0xy-passw0rd" not in text
    # Adı verilen değişken boşalırsa koruma sessizce kapanmaz: ayarlar kurulamaz
    monkeypatch.setenv("MY_TOKEN", "")
    with pytest.raises(ConfigError, match="token_env: the environment variable MY_TOKEN is not set"):
        cm.get_data_dir()
    monkeypatch.setenv("MY_TOKEN", token)
    assert cm.get_settings().server.token == token
    loader.reset()
    assert not {"SOFASCORE_API_TOKEN", "USE_PROXY", "PROXY_URL"} & set(os.environ)


def test_log_settings_from_the_config_file_reach_the_running_logger(active):
    root = logging.getLogger()
    level_before, handler_before = root.level, app_logger._file_handler
    assert handler_before is not None and handler_before.backupCount == app_logger.DEFAULT_BACKUP_COUNT
    active('[log]\nlevel = "error"\n')
    assert root.level == logging.ERROR and os.environ["LOG_LEVEL"] == "ERROR"
    assert app_logger._file_handler is handler_before            # yalnızca seviye: log kurulumu yenilenmez
    active('[log]\nlevel = "error"\nbackup_count = 2\n')
    assert app_logger._file_handler.backupCount == 2
    loader.reset()
    assert root.level == level_before and "LOG_LEVEL" not in os.environ
    assert app_logger._file_handler.backupCount == app_logger.DEFAULT_BACKUP_COUNT


def _run_python(code: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    """Kodu temiz bir süreçte çalıştırır: gerçek `.env`, config/ ve log dizinine dokunmaz."""
    clean = {k: v for k, v in os.environ.items() if k not in loader.LEGACY_ENV_NAMES and not k.startswith("SOFASCORE_")}
    clean.update({
        "SOFASCORE_ENV_FILE": str(tmp_path / ".env"), "SOFASCORE_CONFIG_DIR": str(tmp_path / "config"),
        "SOFASCORE_THROTTLE_DIR": str(tmp_path / "throttle"), "LOG_DIR": str(tmp_path / "logs"), "LC_MESSAGES": "C",
        # Log satırları (Türkçe harfler) borudan her platformda aynı kodlamayla geçsin
        "PYTHONIOENCODING": "utf-8",
        **env,
    })
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, encoding="utf-8", errors="replace",
        timeout=120,
    )


def test_at_start_up_the_config_file_reaches_modules_that_read_the_environment(tmp_path):
    """
    Uçtan uca, ayrı bir süreçte, main.py'nin içe aktarma sırasıyla (önce config_manager): dosya içe aktarma
    sırasında okunur; bütçe ve yenileme modülleri ile src/utils.py'nin içe aktarılırken donan sabitleri onu görür.
    """
    config = tmp_path / "my.toml"
    config.write_text(
        '[client]\nrate = 2\nbase_url = "https://api.example.invalid/api/v1"\n[storage]\ndata_dir = "d"\n'
        "[refresh]\nwindow_hours = 1.5\n[fetch]\nonly_finished = false\n",
        encoding="utf-8",
    )
    code = (
        "import json, os\n"
        "import src.config_manager as cm\n"
        "from src import refresh, throttle, utils\n"
        "print(json.dumps([throttle.configured_rate(), refresh.refresh_window_hours(), os.environ['DATA_DIR'],"
        " cm.ConfigManager().get_request_rate_limit(), utils.API_BASE_URL, utils.FETCH_ONLY_FINISHED]))\n"
    )
    done = _run_python(code, tmp_path, SOFASCORE_CONFIG=str(config))
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [
        2.0, 1.5, str(tmp_path / "d"), 2.0, "https://api.example.invalid/api/v1", False,
    ]
    # Dosya yokken aynı süreç bugünkü varsayılanları verir ve ortama hiçbir ayar yazılmaz
    code = (
        "import json, os\n"
        "before = dict(os.environ)\n"
        "import src.config_manager as cm\n"
        "from src import throttle, utils\n"
        "names = set(os.environ) - set(before)\n"
        f"added = sorted(n for n in names if n.startswith('SOFASCORE_') or n in {sorted(loader.LEGACY_ENV_NAMES)!r})\n"
        "print(json.dumps([throttle.configured_rate(), added, cm.ConfigManager().get_data_dir(),"
        " utils.API_BASE_URL, utils.FETCH_ONLY_FINISHED]))\n"
    )
    done = _run_python(code, tmp_path, SOFASCORE_CONFIG="none")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [
        5.0, [], "data", "https://www.sofascore.com/api/v1", True,
    ]


def test_a_broken_config_file_stops_the_application_at_start_up(tmp_path):
    config = tmp_path / "broken.toml"
    config.write_text("[client]\nrate = 'fast'\n", encoding="utf-8")
    done = _run_python("import src.config_manager\n", tmp_path, SOFASCORE_CONFIG=str(config))
    assert done.returncode != 0
    assert "ConfigError" in done.stderr and 'broken.toml: [client] rate: expected a number or "off"' in done.stderr


# === şema ==========================================================================================


def test_schema_is_generated_from_the_model():
    schema = config_schema()
    json.dumps(schema)
    assert schema["$id"] == "sofascore.config/1" and schema["additionalProperties"] is False
    properties = schema["properties"]
    assert set(properties) == {"schema", "slices", "follow", "sink"} | set(model.SECTIONS)
    # Her sayıl ayar şemada; yalnızca ortamdan okunanlar (gizli değerler) hariç
    for key, f in model.iter_settings():
        section, _, name = key.partition(".")
        entry = properties[section]["properties"].get(name)
        if not f.metadata["in_file"]:
            assert entry is None
            continue
        assert entry["description"] and entry["x-env"] == loader.env_name(key), key
    assert environment_only_keys() == ["client.captcha_token", "server.token"]
    client = properties["client"]["properties"]
    assert client["rate"] == {
        "anyOf": [{"type": "number", "minimum": 0}, {"const": "off"}],
        "description": 'Requests per second across all processes; 0 or "off" removes the limit.',
        "default": 5.0, "x-env": "SOFASCORE_CLIENT__RATE", "x-legacy-env": "REQUEST_RATE_LIMIT",
    }
    assert client["max_concurrent"]["minimum"] == 1 and client["max_concurrent"]["type"] == "integer"
    assert client["proxy"]["x-secret"] is True
    assert properties["live"]["properties"]["poll_interval_seconds"]["exclusiveMinimum"] == 0
    live = properties["live"]["properties"]
    assert set(live) == {"source", "poll_interval_seconds", "detail_slices", "detail_interval_seconds", "max_event_polls"}
    assert live["source"]["enum"] == ["page", "direct", "poll"] and live["source"]["default"] == "page"
    assert "IP address blocked" in live["source"]["description"]    # uyarı, kaynağın belgelendiği yerde de durur
    assert properties["log"]["properties"]["level"]["enum"] == list(model.LOG_LEVELS)
    assert "default" not in properties["display"]["properties"]["language"]
    assert properties["display"]["properties"]["language"]["x-legacy-env"] == "APP_LANGUAGE"
    assert properties["server"]["properties"]["allowed_hosts"]["default"] == ["localhost", "127.0.0.1", "[::1]"]
    assert set(properties["schedule"]["properties"]) == {"enabled", "task"}
    assert set(properties["slices"]["properties"]) == set(__import__("src.sports", fromlist=["sport_slugs"]).sport_slugs())
    assert [list(option["required"]) for option in properties["follow"]["items"]["oneOf"]] == [
        ["tournament"], ["team"], ["player"], ["event"],
    ]
    assert properties["sink"]["items"]["required"] == ["name", "type"]


def test_schema_accepts_what_the_loader_accepts(tmp_path):
    """Tasarımdaki örnek dosyanın her bölümü ve anahtarı şemada tanımlı (şema ile yükleyici ayrışmasın)."""
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib

    doc = tomllib.loads(DESIGN_SAMPLE)
    properties = config_schema()["properties"]
    for section, body in doc.items():
        assert section in properties, section
        if section in model.SECTIONS:
            assert set(body) <= set(properties[section]["properties"]), section
    for table in doc["follow"]:
        assert set(table) <= set(properties["follow"]["items"]["properties"])
    for table in doc["sink"]:
        assert set(table) <= set(properties["sink"]["items"]["properties"])
