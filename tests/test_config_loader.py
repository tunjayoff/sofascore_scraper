"""
Ayar modeli ve yükleyici (sofascore_scraper/config; plan maddesi P09, docs/design/02-services.md bölüm 4.3).

Dört grup:

  modüller           ayarı okuyan modüller etkin ayarlardan okur (3.1: ortamı doğrudan okumaz); 2.x'in ortam
                     adları okunmaz, her biri yerini alan adı söyleyen bir uyarı verir (plan maddesi P30)
  yapılandırma       sofascore.toml: bölümler, öncelik sırası, göreli yollar, hatalar, listeler
  etkin ayarlar      ConfigManager getter'ları, yeniden yükleme
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
import test_cli_skeleton as skeleton
from sofascore_scraper import breaker, bridge_health, language, paths, refresh, throttle
from sofascore_scraper import logger as app_logger
from sofascore_scraper.config import Settings, config_schema, loader
from sofascore_scraper.config import settings as model
from sofascore_scraper.config.schema import environment_only_keys
from sofascore_scraper.config_manager import ConfigManager
from sofascore_scraper.exceptions import ConfigError
from sofascore_scraper.store import files as store_files
from sofascore_scraper.web import security

ROOT = Path(__file__).resolve().parent.parent

# `main()`i bu süreçte çalıştıran ve süreçteki izlerini geri alan fixture (tests/test_cli_skeleton.py)
cli = skeleton.cli

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
    assert loader.TOKEN_ENV == security.TOKEN_ENV == loader.env_name("server.token")
    assert model.LANGUAGES == language.SUPPORTED_LANGUAGES
    assert s.display.language == language.DEFAULT_LANGUAGE
    assert language.EXPLICIT_KEYS == (loader.env_name(loader.LANGUAGE_KEY),)


def test_settings_are_frozen_and_keep_secrets_out_of_repr(tmp_path):
    loaded = _load(env={
        "SOFASCORE_CLIENT__PROXY": "http://user:hunter2@proxy.example.com:8080", "SOFASCORE_SERVER__TOKEN": "tok-en-123",
        "SOFASCORE_CLIENT__CAPTCHA_TOKEN": "captcha-456",
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
    """.env.example'daki her ad (yorum satırındakiler dahil) bir ayarın SOFASCORE_<BÖLÜM>__<ANAHTAR> adı; 2.x adı yok."""
    documented = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        text = line.lstrip("# ").strip()
        if text.startswith(loader.ENV_PREFIX) and "=" in text and " " not in text.split("=", 1)[0]:
            documented.add(text.split("=", 1)[0])
    assert documented
    known = {loader.env_name(key) for key, _ in model.iter_settings()}
    assert documented - known == set()
    assert documented & set(loader.LEGACY_NAMES) == set()
    # Eski adlar tablosu: her ad gerçek bir alanı gösterir (ya da kalkan ayarı: None), en çok bir kez
    keys = [key for key in loader.LEGACY_NAMES.values() if key is not None]
    assert len(keys) == len(set(keys))
    for key in keys:
        Settings().get(key)
    assert [name for name, key in loader.LEGACY_NAMES.items() if key is None] == ["SAVE_EMPTY_ROUNDS"]
    assert set(loader.RETIRED_SETTINGS) == {"fetch.save_empty_rounds"}


# === modüller ve 2.x adları ========================================================================


def test_modules_read_the_effective_settings(monkeypatch, tmp_path):
    """3.1: ayarı okuyan modüller ortamı doğrudan okumaz, etkin ayarlardan okur (yeni ad, dosya, bayrak aynı yoldan)."""
    profile, throttle_dir = str(tmp_path / "profile"), str(tmp_path / "throttle")
    cases = [
        ("SOFASCORE_CLIENT__RATE", "2.5", throttle.configured_rate, 2.5),
        ("SOFASCORE_CLIENT__RATE", "off", throttle.configured_rate, 0.0),
        ("SOFASCORE_CLIENT__THROTTLE_DIR", throttle_dir, throttle.state_dir, throttle_dir),
        ("SOFASCORE_REFRESH__WINDOW_HOURS", "12", refresh.refresh_window_hours, 12.0),
        ("SOFASCORE_REFRESH__MIN_INTERVAL_HOURS", "2", refresh.refresh_min_interval_hours, 2.0),
        ("SOFASCORE_REFRESH__INCLUDE_LEGACY", "true", refresh.refresh_legacy_enabled, True),
        ("SOFASCORE_BREAKER__IGNORE", "true", breaker._ignore_rate_limit, True),
        ("SOFASCORE_BRIDGE__DEGRADED_AFTER", "4", lambda: bridge_health.thresholds()["degraded_after"], 4),
        ("SOFASCORE_LOG__LEVEL", "warning", lambda: logging.getLevelName(app_logger.resolve_level()), "WARNING"),
        ("SOFASCORE_LOG__DEBUG", "true", lambda: logging.getLevelName(app_logger.resolve_level()), "DEBUG"),
        ("SOFASCORE_LOG__TO_FILE", "false", app_logger.log_to_file_enabled, False),
        ("SOFASCORE_LOG__MAX_MB", "2", app_logger.log_max_bytes, 2 * 1024 * 1024),
        ("SOFASCORE_LOG__BACKUP_COUNT", "4", app_logger.log_backup_count, 4),
        ("SOFASCORE_SERVER__ALLOWED_HOSTS", "a.example, b.example", security.allowed_hosts, ["a.example", "b.example"]),
        ("SOFASCORE_SERVER__TOKEN", " tok-123 ", security.api_token, "tok-123"),
        ("SOFASCORE_STORAGE__DURABILITY", "full", store_files.durability_full, True),
        ("SOFASCORE_CLIENT__BROWSER_PROFILE", profile, paths.browser_profile_dir, profile),
    ]
    for name, raw, read, expected in cases:
        with monkeypatch.context() as patch:
            patch.setenv(name, raw)
            assert read() == expected, name


def test_legacy_names_are_not_read_and_each_one_names_its_replacement(monkeypatch):
    """2.x'in adları 3.1'de okunmaz (plan maddesi P30): ayarları değiştirmez, her biri için bir uyarı."""
    env = {"MAX_CONCURRENT": "4", "REQUEST_RATE_LIMIT": "fast", "SAVE_EMPTY_ROUNDS": "true", "SOFASCORE_API_TOKEN": "t"}
    loaded = _load(env=env, dotenv={"MAX_CONCURRENT": "4"})
    assert loaded.settings == _load().settings
    assert [(w.code, w.message) for w in loaded.warnings] == [
        ("legacy_name", "REQUEST_RATE_LIMIT (set in the environment) is no longer read since 3.1; "
                        "use SOFASCORE_CLIENT__RATE (or client.rate in the config file)."),
        ("legacy_name", "MAX_CONCURRENT (set in .env) is no longer read since 3.1; "
                        "use SOFASCORE_CLIENT__MAX_CONCURRENT (or client.max_concurrent in the config file)."),
        ("legacy_name", "SAVE_EMPTY_ROUNDS (set in the environment) is no longer read since 3.1; "
                        "use nothing (the setting was removed)."),
        # Yalnızca ortamdan okunan ayar: yalnız değişkenin adı
        ("legacy_name", "SOFASCORE_API_TOKEN (set in the environment) is no longer read since 3.1; "
                        "use SOFASCORE_SERVER__TOKEN."),
    ]
    # Boş bırakılmış eski ad (2.x'in .env.example'ındaki `SOFA_CAPTCHA_TOKEN=` gibi) uyarı vermez
    assert _load(env={"SOFA_CAPTCHA_TOKEN": "", "PROXY_URL": " "}).warnings == ()
    # Süreçte: modüller eski adı görmez
    monkeypatch.delenv("SOFASCORE_CLIENT__RATE")
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "2.5")
    monkeypatch.setenv("IGNORE_RATE_LIMIT", "true")
    assert throttle.configured_rate() == throttle.DEFAULT_RATE_LIMIT
    assert breaker._ignore_rate_limit() is False
    assert "REQUEST_RATE_LIMIT" in loader.legacy_names_in(os.environ)


def test_a_retired_setting_is_a_warning_not_an_error(tmp_path):
    """fetch.save_empty_rounds 3.1'de kalktı: 3.0'ın Ayarlar sayfası overrides.json'a yazmış olabilir."""
    for loaded in (
        _load(tmp_path, toml="[fetch]\nsave_empty_rounds = true\n"),
        _load(tmp_path, overrides={"fetch": {"save_empty_rounds": False}}),
        _load(env={"SOFASCORE_FETCH__SAVE_EMPTY_ROUNDS": "true"}),
    ):
        assert [w.code for w in loaded.warnings] == ["retired_setting"]
        assert "a round without a match is never stored" in loaded.warnings[0].message
        assert loaded.settings == _load().settings


def test_effective_base_url_is_the_one_the_request_layer_applies(monkeypatch, tmp_path):
    """
    `client.base_url`i iki yer okur: ayarlar sayfası yazıldığı gibi gösterir (`base_url`), istek katmanı sondaki "/"
    işaretini atar (`effective_base_url`, sofascore_scraper/client/transport.py).
    """
    from sofascore_scraper import client
    from sofascore_scraper.client import endpoints, transport

    assert model.DEFAULT_API_BASE_URL == endpoints.DEFAULT_BASE_URL
    for raw in (None, "", "https://api.example.invalid/api/v1", "https://api.example.invalid/api/v1/"):
        if raw is None:
            monkeypatch.delenv("SOFASCORE_CLIENT__BASE_URL", raising=False)
        else:
            monkeypatch.setenv("SOFASCORE_CLIENT__BASE_URL", raw)
        settings = _from_process_env().client
        assert settings.effective_base_url == transport._configured_base_url(), repr(raw)
        assert settings.effective_base_url == (raw or endpoints.DEFAULT_BASE_URL).rstrip("/"), repr(raw)
        # İstemcinin kendi ayar sınıfı (P05) modelden kurulabilir: alan adları aynı
        built = client.ClientSettings(
            base_url=settings.effective_base_url, retries=settings.retries, timeout_seconds=settings.timeout_seconds,
        )
        assert built.base_url == settings.effective_base_url
    # Dosyadan gelen kök denetlenir ve sondaki "/" atılır
    from_file = _load(tmp_path, toml='[client]\nbase_url = "https://api.example.invalid/api/v1/"\n').settings.client
    assert from_file.base_url == from_file.effective_base_url == "https://api.example.invalid/api/v1"


def test_bridge_blocked_threshold_is_never_below_the_degraded_one(monkeypatch):
    monkeypatch.setenv("SOFASCORE_BRIDGE__DEGRADED_AFTER", "8")
    monkeypatch.setenv("SOFASCORE_BRIDGE__BLOCKED_AFTER", "5")
    assert dataclasses.asdict(_from_process_env().bridge) == bridge_health.thresholds()
    assert _from_process_env().bridge.blocked_after == 8


def test_browser_headed_matches_the_bridge(monkeypatch):
    from sofascore_scraper.client import bridge

    for raw in (None, "", "true", "false", "yes", "off"):
        if raw is None:
            monkeypatch.delenv("SOFASCORE_CLIENT__BROWSER_HEADED", raising=False)
        else:
            monkeypatch.setenv("SOFASCORE_CLIENT__BROWSER_HEADED", raw)
        assert _from_process_env().client.browser_headed is (not bridge._headless()), repr(raw)


@pytest.mark.parametrize(
    "env",
    [
        {}, {"SOFASCORE_DISPLAY__LANGUAGE": "tr"}, {"SOFASCORE_DISPLAY__LANGUAGE": "en", "LANG": "tr_TR.UTF-8"},
        # 2.x'in adları (APP_LANGUAGE, LANGUAGE) okunmaz: sistem dili belirler
        {"APP_LANGUAGE": "tr"}, {"LANGUAGE": "tr"}, {"APP_LANGUAGE": "en", "LANG": "tr_TR.UTF-8"},
        {"LC_ALL": "tr_TR.UTF-8"}, {"LC_MESSAGES": "tr_TR", "LANG": "en_US.UTF-8"}, {"LANG": "tr_TR.UTF-8"},
        {"LC_ALL": "C", "LANG": "tr_TR.UTF-8"}, {"SOFASCORE_DISPLAY__LANGUAGE": "en", "LC_ALL": "tr_TR.UTF-8"},
    ],
)
def test_language_follows_the_one_rule(env):
    """sofascore_scraper/language.resolve_language ile aynı sonuç; kaynağı da doğru (açık ayar mı, sistem dili mi)."""
    loaded = _load(env=env)
    assert loaded.settings.display.language == language.resolve_language(env)
    explicit = language.explicit_language(env) is not None
    assert (loaded.source("display.language").layer != loader.LAYER_DEFAULT) is explicit


def test_dotenv_is_not_a_layer_of_its_own():
    """
    3.1: `.env` uygulama açılırken ortama yüklenir (python-dotenv, ortamdaki değeri ezmez) ve ortam katmanı sayılır.
    Yükleyiciye verilen içeriği yalnızca eski adın uyarısında nerede yazıldığını söyler.
    """
    assert loader.LAYERS == ("default", "overrides", "file", "env", "flag")
    loaded = _load(
        env={"SOFASCORE_CLIENT__MAX_CONCURRENT": "4"},
        dotenv={"SOFASCORE_CLIENT__MAX_CONCURRENT": "4", "SOFASCORE_CLIENT__RETRIES": "9"},
    )
    assert loaded.source("client.max_concurrent") == loader.Source("env", "SOFASCORE_CLIENT__MAX_CONCURRENT")
    assert loaded.source("client.max_concurrent").locked is True
    # `.env`'de yazıp ortama yüklenmemiş bir değer sayılmaz
    assert loaded.source("client.retries").layer == "default"


def test_an_unknown_language_is_an_error():
    with pytest.raises(ConfigError, match="SOFASCORE_DISPLAY__LANGUAGE"):
        _load(env={"SOFASCORE_DISPLAY__LANGUAGE": "de"})


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
    """default < overrides.json < dosya < ortam (`.env` dahil) < bayrak."""
    key = "client.max_concurrent"

    def value(**sources: Any) -> Any:
        loaded = _load(tmp_path, **sources)
        return loaded.settings.client.max_concurrent, loaded.source(key).layer

    assert value() == (10, "default")
    overrides = {"client": {"max_concurrent": 3}}
    assert value(overrides=overrides) == (3, "overrides")
    toml = "[client]\nmax_concurrent = 4\n"
    assert value(overrides=overrides, toml=toml) == (4, "file")
    env = {"SOFASCORE_CLIENT__MAX_CONCURRENT": "6"}
    assert value(env=env, overrides=overrides, toml=toml) == (6, "env")
    assert value(env=env, overrides=overrides, toml=toml, flags={key: 7}) == (7, "flag")
    assert value(env=env, toml=toml, flags={key: "8"}) == (8, "flag")
    # 2.x adı ve boş bırakılmış yeni ad hiçbir katmanı ezmez
    assert value(env={"MAX_CONCURRENT": "5"}, toml=toml) == (4, "file")
    assert value(env={"SOFASCORE_CLIENT__MAX_CONCURRENT": " "}, toml=toml) == (4, "file")


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
    # Yapılandırma dosyası yokken göreli yol yazıldığı gibi kalır (çalışma dizinine göre)
    env = {"SOFASCORE_STORAGE__DATA_DIR": "relative/data"}
    assert _load(env=env).settings.storage.data_dir == "relative/data"
    assert _load(conf, toml=toml, env=env).settings.storage.data_dir == str(conf / "relative" / "data")


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
    # Adresin kullanıcı bilgisi de yolu da gösterilmez (webhook adresinde yol ya da sorgu belirteç olabilir)
    assert rows["sinks"][0]["url"] == "http://***@127.0.0.1:9000/***"
    assert "pw" not in repr(s)
    assert loader.parse_duration("30s") == 30.0 and loader.parse_duration("1d") == 86400.0


def test_proxy_and_token_come_from_named_environment_variables(tmp_path):
    env = {"MY_PROXY": " http://u:p@proxy.example.com:8080 ", "MY_TOKEN": "s3cret-token"}
    toml = '[client]\nproxy_env = "MY_PROXY"\n[server]\ntoken_env = "MY_TOKEN"\n'
    loaded = _load(tmp_path, toml=toml, env=env)
    s = loaded.settings
    assert (s.client.proxy, s.server.token) == ("http://u:p@proxy.example.com:8080", "s3cret-token")
    assert loaded.source("client.proxy") == loader.Source("file", "MY_PROXY")
    assert loaded.source("server.token") == loader.Source("file", "MY_TOKEN")
    # Dosyada proxy verilince kullanılır (overrides.json'daki "hayır", zayıf katman, onu kapatmaz) ...
    assert s.client.use_proxy is True
    assert _load(tmp_path, toml=toml, env=env, overrides={"client": {"use_proxy": False}}).settings.client.use_proxy
    # ... ama aynı dosyadaki ya da ortamdaki açık bir "hayır" kapatır
    off_env = {**env, "SOFASCORE_CLIENT__USE_PROXY": "false"}
    assert _load(tmp_path, toml=toml, env=off_env).settings.client.use_proxy is False
    off = '[client]\nproxy = "http://proxy.example.com:1"\nuse_proxy = false\n'
    assert _load(tmp_path, toml=off).settings.client.use_proxy is False
    assert _load(tmp_path, toml='[client]\nproxy = "http://proxy.example.com:1"\n').settings.client.use_proxy is True
    assert _load(env={"SOFASCORE_CLIENT__PROXY": "http://proxy.example.com:1"}).settings.client.use_proxy is True
    # 2.x adları okunmaz: PROXY_URL proxy, SOFASCORE_API_TOKEN belirteç vermez
    old = _load(env={"PROXY_URL": "http://proxy.example.com:1", "SOFASCORE_API_TOKEN": "abc"}).settings
    assert (old.client.proxy, old.client.use_proxy, old.server.token) == ("", False, "")
    # token_env boşsa SOFASCORE_SERVER__TOKEN
    plain = _load(tmp_path, toml='[server]\ntoken_env = ""\n', env={"SOFASCORE_SERVER__TOKEN": " abc "})
    assert plain.settings.server.token == "abc"
    assert plain.source("server.token") == loader.Source("env", "SOFASCORE_SERVER__TOKEN")


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
    # Boş bırakılan gizli değer verilmemiş sayılır: dosyadaki token_env'in gösterdiği belirteci silmez
    kept = _load(tmp_path, toml='[server]\ntoken_env = "MY_TOKEN"\n', env={"SOFASCORE_SERVER__TOKEN": " ", "MY_TOKEN": "t-1"})
    assert kept.settings.server.token == "t-1"
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


# --- sink satırlarının hataları: reddedilen değer iletiye yazılmaz ------------------------------------------


def fake(*words: str) -> str:
    """
    Gizli bir değerin yerini tutan sınama değeri: bilerek sahte ("fake-..."), çalışırken parçalardan kurulur.
    Depoda gizli değere benzeyen bir sabit durmaz; testler bu metinleri hata iletilerinde ve çıktılarda arar.
    """
    return "-".join(("fake", *words))


HOOK_PATH_PART, HOOK_QUERY_PART, SIGNING_VALUE = fake("path", "part"), fake("query", "part"), fake("signing", "value")
# Yolu ve sorgusu belirteç olan bir webhook adresi (sahte)
HOOK_ADDRESS = f"https://hooks.example.org/services/T000/B000/{HOOK_PATH_PART}?sig={HOOK_QUERY_PART}"
PRIVATE_PARTS = (HOOK_PATH_PART, HOOK_QUERY_PART, SIGNING_VALUE, "/services/", "hooks.example.org")
# İmza anahtarını taşıyan ortam değişkeninin adı (bir ad, değer değil)
HOOK_VARIABLE = "_".join(("HOOK", "SIGNING", "KEY"))


def _refused(env: Mapping[str, str]) -> str:
    """Yükleyicinin hata iletisi; içinde adresin ve gizli değerin hiçbir parçası olmamalı."""
    with pytest.raises(ConfigError) as error:
        _load(env=env)
    message = str(error.value)
    for private in PRIVATE_PARTS:
        assert private not in message, private
    return message


@pytest.mark.parametrize("variable", ["SOFASCORE_SINKS", "SOFASCORE_FOLLOWS", "SOFASCORE_SCHEDULE__TASKS"])
def test_a_list_of_strings_instead_of_tables_is_named_by_its_type(variable):
    """Tablo yerine dizge listesi: öğeler webhook adresi olabilir; ileti öğenin türünü ve sırasını söyler."""
    assert _refused({variable: json.dumps([HOOK_ADDRESS])}) == f"{variable}: expected a list of tables, got a string at #1"
    mixed = json.dumps([{"name": "out", "type": "stdout"}, [HOOK_ADDRESS], HOOK_ADDRESS])
    assert _refused({variable: mixed}) == f"{variable}: expected a list of tables, got a list at #2"


def test_a_list_of_strings_in_the_config_file_is_named_by_its_type(tmp_path):
    with pytest.raises(ConfigError) as error:
        _load(tmp_path, toml=f'sink = ["{HOOK_ADDRESS}"]\n')
    assert str(error.value).endswith("sofascore.toml: [[sink]]: expected a list of tables, got a string at #1")
    assert HOOK_PATH_PART not in str(error.value)
    with pytest.raises(ConfigError, match=r"\[\[sink\]\]: expected a list of tables, got a string$"):
        _load(tmp_path, toml=f'sink = "{HOOK_ADDRESS}"\n')


@pytest.mark.parametrize(
    "table,message",
    [
        # Adres yanlış alanda ya da yanlış türde
        ({"name": "ops", "type": "webhook", "url": [HOOK_ADDRESS]}, "url: expected a string, got a list"),
        ({"name": "ops", "type": "webhook", "url": {"address": HOOK_ADDRESS}}, "url: expected a string, got a table"),
        ({"name": "ops", "type": "webhook", "url": None}, "url: expected a string, got null"),
        ({"name": "ops", "type": HOOK_ADDRESS}, "type: expected one of stdout, file, webhook"),
        ({"name": "ops", "type": [HOOK_ADDRESS]}, "type: expected a string, got a list"),
        ({"name": [HOOK_ADDRESS], "type": "stdout"}, "name: expected a string, got a list"),
        ({"name": "ops", "type": "file", "path": [HOOK_ADDRESS]}, "path: expected a string, got a list"),
        ({"name": "ops", "type": "stdout", "events": HOOK_ADDRESS}, "events: expected a list of strings, got a string"),
        ({"name": "ops", "type": "stdout", "events": [7, HOOK_ADDRESS]},
         "events: expected a list of strings, got a whole number at #1"),
        ({"name": "ops", "type": "stdout", "events": [HOOK_ADDRESS, " "]},
         "events: expected a list of strings, got an empty string at #2"),
        ({"name": "ops", "type": "webhook", "url": HOOK_ADDRESS, "allow_unsigned": HOOK_ADDRESS},
         "allow_unsigned: expected true or false, got a string"),
        ({"name": "ops", "type": "webhook", "url": HOOK_ADDRESS, "allow_unsigned": 1.5},
         "allow_unsigned: expected true or false, got a number"),
        # İmza anahtarının adı yerine kendisi yazılmış
        ({"name": "ops", "type": "webhook", "url": HOOK_ADDRESS, "secret_env": SIGNING_VALUE},
         ": secret_env must be the name of an environment variable (letters, digits and _), not the secret itself"),
        ({"name": "ops", "type": "webhook", "url": HOOK_ADDRESS, "secret_env": [SIGNING_VALUE]},
         "secret_env: expected a string, got a list"),
        ({"name": True, "type": "stdout"}, "name: expected a string, got a boolean"),
    ],
)
def test_sink_errors_name_the_type_of_a_rejected_value_not_the_value(table, message):
    """Bir sink satırının alanı adres ya da gizli değer olabilir; ileti komutun çıktısına, log'a ve zarfa girer."""
    separator = "" if message.startswith(":") else " "
    assert _refused({"SOFASCORE_SINKS": json.dumps([table])}) == f"SOFASCORE_SINKS: [[sink]] #1{separator}{message}"


def test_sink_errors_of_the_config_file_do_not_quote_the_value_either(tmp_path):
    with pytest.raises(ConfigError) as error:
        _load(tmp_path, toml=f'[[sink]]\nname = "ops"\ntype = "webhook"\nurl = ["{HOOK_ADDRESS}"]\n')
    assert str(error.value).endswith("sofascore.toml: [[sink]] #1 url: expected a string, got a list")
    assert HOOK_PATH_PART not in str(error.value)
    with pytest.raises(ConfigError) as error:
        _load(tmp_path, toml='[[sink]]\nname = "ops"\ntype = "file"\npath = 1979-05-27\n')
    assert str(error.value).endswith("[[sink]] #1 path: expected a string, got a date value")


def test_other_rows_still_quote_a_rejected_value():
    """Takip ve zamanlama satırları adres taşımaz: iletileri değeri göstermeye devam eder."""
    with pytest.raises(ConfigError, match=r"\[\[follow\]\] #1 tournament: expected a whole number, got '17'"):
        _load(env={"SOFASCORE_FOLLOWS": '[{"tournament": "17"}]'})
    with pytest.raises(ConfigError, match=r"\[\[schedule.task\]\] #1 run: expected a string, got 5"):
        _load(env={"SOFASCORE_SCHEDULE__TASKS": '[{"run": 5, "every": "1h"}]'})


@pytest.mark.parametrize("value", [
    json.dumps([HOOK_ADDRESS]),
    json.dumps([{"name": "ops", "type": "webhook", "url": [HOOK_ADDRESS], "secret_env": HOOK_VARIABLE}]),
    json.dumps([{"name": "ops", "type": "webhook", "url": HOOK_ADDRESS, "secret_env": SIGNING_VALUE}]),
], ids=["list-of-strings", "url-as-a-list", "secret-instead-of-its-name"])
def test_a_command_refused_for_a_wrong_sink_value_prints_no_part_of_it(cli, tmp_path, monkeypatch, value):
    """Komutun hata zarfı (JSON), metin çıktısı ve log satırları: adresin ve gizli değerin hiçbir parçası yok."""
    monkeypatch.setenv("SOFASCORE_CONFIG", "none")
    monkeypatch.setenv("SOFASCORE_SINKS", value)
    for command in (("config", "validate"), ("config", "show")):
        as_json = cli(*command, "--json", "--data-dir", tmp_path / "data")
        as_text = cli(*command, "--data-dir", tmp_path / "data")
        assert as_json.exit_code == 2 and as_text.exit_code == 2
        assert as_json.error["code"] == "config_invalid" and as_json.error["message"].startswith("SOFASCORE_SINKS: ")
        assert as_text.stderr.startswith("Configuration error: SOFASCORE_SINKS: ")
        for private in PRIVATE_PARTS:
            for output in (as_json.stdout, as_json.stderr, as_text.stdout, as_text.stderr):
                assert private not in output, private
        if SIGNING_VALUE in value:
            # Komut çıktısındaki maskeleme iletiyi bozmaz ("secret_env: <sözcük>" bir anahtar-değer çifti sayılırdı)
            assert as_text.stderr.splitlines()[0] == (
                "Configuration error: SOFASCORE_SINKS: [[sink]] #1: secret_env must be the name of an environment "
                "variable (letters, digits and _), not the secret itself"
            )


# --- sink seçenekleri: sink'lerin tanımadığı anahtarın değeri gösterilmez ------------------------------------

# Bir webhook sink'i: tanımlı seçenekler, yanlış yazılmış / uydurulmuş anahtarlar ve başka türün bir anahtarı
SINK_WITH_UNKNOWN_OPTIONS = f'''
[[sink]]
name = "ops"
type = "webhook"
url = "https://hooks.example.org/in"
allow_unsigned = true
batch_size = 50
max_age = "12h"
sports = ["football"]
webhook_url = "{HOOK_ADDRESS}"
signing = "{SIGNING_VALUE}"
headers = {{ authorization = "{fake("header", "value")}" }}
retries = 3
rotate_size = "50MB"
[[sink]]
name = "feed"
type = "file"
path = "out/live.ndjson"
rotate_daily = true
keep = 7
fallback = ["{HOOK_ADDRESS}"]
'''
SHOWN_OPTIONS = [
    {
        "batch_size": 50, "max_age": "12h", "sports": ["football"],
        "webhook_url": "***", "signing": "***", "headers": "***", "retries": "***",
        "rotate_size": "***",  # dosya sink'inin anahtarı: webhook için bilinmiyor
    },
    {"rotate_daily": True, "keep": 7, "fallback": "***"},
]
OPTION_VALUES = (HOOK_PATH_PART, HOOK_QUERY_PART, SIGNING_VALUE, "/services/", fake("header", "value"))


def test_describe_masks_the_value_of_a_sink_option_the_sinks_do_not_define(tmp_path):
    loaded = _load(tmp_path, toml=SINK_WITH_UNKNOWN_OPTIONS)
    rows = {row["key"]: row["value"] for row in loaded.describe()}
    assert [sink["options"] for sink in rows["sinks"]] == SHOWN_OPTIONS
    for private in OPTION_VALUES:
        assert private not in json.dumps(rows), private
    # Ayarın kendisi değişmez (sink kurulurken bilinmeyen anahtar reddedilir); istenirse değerler gösterilir
    assert loaded.settings.sinks[0].options["webhook_url"] == HOOK_ADDRESS
    plain = {row["key"]: row["value"] for row in loaded.describe(mask_secrets=False)}
    assert plain["sinks"][0]["options"]["webhook_url"] == HOOK_ADDRESS
    assert plain["sinks"][1]["options"] == {"rotate_daily": True, "keep": 7, "fallback": [HOOK_ADDRESS]}


def test_config_show_never_prints_the_value_of_an_unknown_sink_option(cli, tmp_path):
    """`ssc config show`, JSON ve metin: anahtarın adı görünür (hangisinin yanlış olduğu), değeri görünmez."""
    config = tmp_path / "sofascore.toml"
    config.write_text("schema = 1\n" + SINK_WITH_UNKNOWN_OPTIONS, encoding="utf-8")
    as_json = cli("config", "show", "--json", "--config", config, "--data-dir", tmp_path / "data")
    as_text = cli("config", "show", "--config", config, "--data-dir", tmp_path / "data")
    assert as_json.exit_code == 0 and as_text.exit_code == 0
    rows = {row["key"]: row["value"] for row in as_json.data["values"]}
    assert [sink["options"] for sink in rows["sinks"]] == SHOWN_OPTIONS
    for private in OPTION_VALUES:
        for output in (as_json.stdout, as_json.stderr, as_text.stdout, as_text.stderr):
            assert private not in output, private
    for shown in ('"webhook_url": "***"', '"signing": "***"', '"headers": "***"', '"batch_size": 50', '"keep": 7'):
        assert shown in as_text.stdout, shown


def test_the_shown_sink_options_are_the_ones_the_sinks_define():
    from sofascore_scraper import sinks

    # Her sink türünün seçenek listesi vardır; modelin alanları seçenek sayılmaz
    assert set(sinks.TYPE_OPTIONS) == set(model.SINK_TYPES)
    assert set(loader.SINK_KEYS) == {f.name for f in dataclasses.fields(model.SinkSpec)} - {"options"}
    every = set(sinks.COMMON_OPTIONS) | {key for keys in sinks.TYPE_OPTIONS.values() for key in keys}
    for kind in model.SINK_TYPES:
        known = set(sinks.COMMON_OPTIONS) | set(sinks.TYPE_OPTIONS[kind])
        shown = loader.mask_sink_options(kind, {key: 1 for key in every | {"invented"}})
        assert {key for key, value in shown.items() if value == 1} == known
        assert shown["invented"] == "***"
    # Türü bilinmeyen ya da dizge olmayan (ayrıştırılmamış tablo) bir sink: yalnızca ortak anahtarlar
    for kind in ("kafka", None, ["webhook"]):
        assert loader.mask_sink_options(kind, {"sports": ["tennis"], "batch_size": 5}) == {
            "sports": ["tennis"], "batch_size": "***",
        }
    table = {"name": "ops", "type": "webhook", "uri": HOOK_ADDRESS, "url": HOOK_ADDRESS, "batch_size": 5, "keep": 2}
    assert loader.mask_sink_table(table) == {**table, "uri": "***", "keep": "***"}
    assert list(loader.mask_sink_table(table)) == list(table)  # anahtarların sırası korunur


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
    # 2.x adları için, yapılandırma dosyası olsun olmasın, yerini alan adı söyleyen uyarı (`.env`'dekiler dahil)
    env = {"MAX_CONCURRENT": "5", "DATA_DIR": "d"}
    expected = [
        ("legacy_name", "DATA_DIR (set in .env) is no longer read since 3.1; "
                        "use SOFASCORE_STORAGE__DATA_DIR (or storage.data_dir in the config file)."),
        ("legacy_name", "MAX_CONCURRENT (set in the environment) is no longer read since 3.1; "
                        "use SOFASCORE_CLIENT__MAX_CONCURRENT (or client.max_concurrent in the config file)."),
    ]
    with_file = _load(tmp_path, toml="", env=env, dotenv={"DATA_DIR": "d"})
    assert [(w.code, w.message) for w in with_file.warnings] == expected
    assert [(w.code, w.message) for w in _load(env=env, dotenv={"DATA_DIR": "d"}).warnings] == expected


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


# === etkin ayarlar: ConfigManager ve modüller =====================================================


@pytest.fixture
def active(monkeypatch):
    """
    Etkin ayarları sıfırdan kurdurur; testten sonra test yapılandırma dizinindeki dosyaları siler.
    `write(toml)` dosyayı yazıp ayarları yeniden yükler. conftest dosya aramayı
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
    assert dict(os.environ) == before
    # conftest: veri dizini ve istek bütçesi süreç ortamından (yeni adlarıyla) gelir; test `.env`'i boş
    assert loaded.source("storage.data_dir") == loader.Source("env", "SOFASCORE_STORAGE__DATA_DIR")
    assert loaded.source("client.max_concurrent").layer == "default"
    assert (cm.get_max_concurrent(), cm.get_data_dir(), cm.get_request_rate_limit()) == (10, conftest.DATA_DIR, 0.0)


GETTERS = {
    # getter -> (ayarın ortam adı, ham değer, sonuç)
    "get_data_dir": ("SOFASCORE_STORAGE__DATA_DIR", "relative/store", "relative/store"),
    "get_api_base_url": ("SOFASCORE_CLIENT__BASE_URL", "https://api.example.invalid/api/v1", "https://api.example.invalid/api/v1"),
    "get_use_proxy": ("SOFASCORE_CLIENT__USE_PROXY", "true", True),
    "get_proxy_url": ("SOFASCORE_CLIENT__PROXY", "http://proxy.example.com:1", "http://proxy.example.com:1"),
    "get_use_color": ("SOFASCORE_DISPLAY__USE_COLOR", "false", False),
    "get_date_format": ("SOFASCORE_DISPLAY__DATE_FORMAT", "%d.%m.%Y", "%d.%m.%Y"),
    "get_max_concurrent": ("SOFASCORE_CLIENT__MAX_CONCURRENT", "7", 7),
    "get_wait_time_min": ("SOFASCORE_CLIENT__WAIT_TIME_MIN", "0.05", 0.05),
    "get_wait_time_max": ("SOFASCORE_CLIENT__WAIT_TIME_MAX", "2.5", 2.5),
    "get_request_timeout": ("SOFASCORE_CLIENT__TIMEOUT_SECONDS", "30", 30),
    "get_max_retries": ("SOFASCORE_CLIENT__RETRIES", "5", 5),
    "get_rate_limit_threshold_consecutive": ("SOFASCORE_BREAKER__RATE_LIMIT_CONSECUTIVE", "12", 12),
    "get_rate_limit_threshold_ratio": ("SOFASCORE_BREAKER__RATE_LIMIT_RATIO", "0.5", 0.5),
    "get_server_error_threshold_consecutive": ("SOFASCORE_BREAKER__SERVER_ERROR_CONSECUTIVE", "9", 9),
}


@pytest.mark.parametrize("getter", sorted(GETTERS))
def test_config_manager_getter_reads_the_effective_settings(active, monkeypatch, getter):
    """Her getter etkin ayarı verir; ortam değişikliği bir sonraki çağrıda görülür. 2.x adı görülmez."""
    name, raw, expected = GETTERS[getter]
    key = name[len(loader.ENV_PREFIX):].lower().replace(loader.ENV_SEPARATOR, ".", 1)
    old = next(legacy for legacy, target in loader.LEGACY_NAMES.items() if target == key)
    read = getattr(ConfigManager(), getter)
    monkeypatch.delenv(name, raising=False)
    unset = read()
    assert not _same(unset, expected)
    monkeypatch.setenv(name, raw)
    assert _same(read(), expected)
    monkeypatch.delenv(name)
    monkeypatch.setenv(old, raw)
    assert _same(read(), unset)


def test_config_manager_rate_and_language_getters(active, monkeypatch):
    cm = ConfigManager()
    for raw, expected in (("2.5", 2.5), ("off", 0.0), ("0", 0.0)):
        monkeypatch.setenv("SOFASCORE_CLIENT__RATE", raw)
        assert cm.get_request_rate_limit() == throttle.configured_rate() == expected, raw
    monkeypatch.delenv("SOFASCORE_CLIENT__RATE")
    assert cm.get_request_rate_limit() == throttle.DEFAULT_RATE_LIMIT

    from sofascore_scraper.i18n import app_language

    for env, expected in (
        ({}, "en"), ({"SOFASCORE_DISPLAY__LANGUAGE": "tr"}, "tr"), ({"APP_LANGUAGE": "tr"}, "en"),
        ({"LC_MESSAGES": "tr_TR.UTF-8"}, "tr"),
    ):
        with monkeypatch.context() as patch:
            for key in language.ENV_KEYS:
                patch.delenv(key, raising=False)
            for key, value in env.items():
                patch.setenv(key, value)
            assert cm.get_language() == app_language() == expected, env
    assert cm.get_match_data_dir() == os.path.join(cm.get_data_dir(), "matches")


def test_config_file_is_honoured_by_getters_and_by_the_modules(active, monkeypatch):
    """Dosyadaki değer getter'lara ve ayarı okuyan modüllere yansır; ortama hiçbir şey yazılmaz."""
    from sofascore_scraper.i18n import app_language
    from sofascore_scraper.services.status import only_finished_setting

    for name in ("SOFASCORE_STORAGE__DATA_DIR", "SOFASCORE_SERVER__ALLOWED_HOSTS"):   # conftest verir; dosyayı ezerdi
        monkeypatch.delenv(name)
    before = dict(os.environ)
    config_dir = Path(conftest.CONFIG_DIR)
    loaded = active(
        '[storage]\ndata_dir = "store"\n'
        "[client]\nmax_concurrent = 4\nrate = 2\ntimeout_seconds = 33\n"
        "[refresh]\nwindow_hours = 12\n[bridge]\ndegraded_after = 6\n"
        '[server]\nallowed_hosts = ["localhost", "box.lan"]\n[display]\nlanguage = "tr"\n[fetch]\nonly_finished = false\n'
    )
    before[loader.CONFIG_ENV] = os.environ[loader.CONFIG_ENV]
    cm = ConfigManager()
    assert loaded.config_file == str(config_dir / "sofascore.toml")
    # Süreç ortamındaki SOFASCORE_CLIENT__RATE=0 (conftest) dosyanın üstünde
    assert (cm.get_max_concurrent(), cm.get_request_timeout(), cm.get_request_rate_limit()) == (4, 33, 0.0)
    assert cm.get_data_dir() == str(config_dir / "store")
    assert cm.get_language() == app_language() == "tr"
    assert loaded.source("client.rate") == loader.Source("env", "SOFASCORE_CLIENT__RATE")

    assert dict(os.environ) == before
    assert refresh.refresh_window_hours() == 12.0
    assert bridge_health.thresholds()["degraded_after"] == 6
    assert security.allowed_hosts() == ["localhost", "box.lan"]
    assert throttle.configured_rate() == 0.0
    assert only_finished_setting() is False
    assert loader.active() is loaded

    # Ortamdan verilen yeni ad dosyayı ezer ve hemen görülür; 2.x adı görülmez
    monkeypatch.setenv("MAX_CONCURRENT", "9")
    assert cm.get_max_concurrent() == 4
    monkeypatch.setenv("SOFASCORE_CLIENT__MAX_CONCURRENT", "11")
    assert cm.get_max_concurrent() == 11
    monkeypatch.delenv("SOFASCORE_CLIENT__MAX_CONCURRENT")
    monkeypatch.delenv("MAX_CONCURRENT")
    assert cm.get_max_concurrent() == 4

    # Dosya kalkıp yeniden yüklenince varsayılanlar
    (config_dir / "sofascore.toml").unlink()
    monkeypatch.setenv(loader.CONFIG_ENV, "none")
    loader.reload()
    assert cm.get_max_concurrent() == 10 and cm.get_data_dir() == "data"


def test_reload_does_not_reread_the_env_file(active, monkeypatch):
    """
    `.env` süreç başında ortama yüklenir (python-dotenv: ortamda verilmiş değeri ezmez). Yeniden yükleme
    yapılandırma dosyasını ve overrides.json'ı okur, `.env`'i ortama yeniden yüklemez: kabuktan ya da
    `docker -e` ile verilen değer (#43) her zaman geçerlidir.
    """
    env_file = Path(conftest.ENV_FILE)
    original = env_file.read_text(encoding="utf-8")
    cm = ConfigManager()
    monkeypatch.setenv("SOFASCORE_CLIENT__TIMEOUT_SECONDS", "30")
    try:
        env_file.write_text(
            original + "SOFASCORE_CLIENT__TIMEOUT_SECONDS=20\nSOFASCORE_CLIENT__RETRIES=7\n", encoding="utf-8",
        )
        assert cm.reload_config() is True
        assert (cm.get_request_timeout(), cm.get_max_retries()) == (30, 3)
        assert "SOFASCORE_CLIENT__RETRIES" not in os.environ
    finally:
        env_file.write_text(original, encoding="utf-8")


def test_config_file_is_reread_only_on_reload(active, monkeypatch):
    path = Path(conftest.CONFIG_DIR) / "sofascore.toml"
    active("[client]\nretries = 4\n")
    cm = ConfigManager()
    assert cm.get_max_retries() == 4
    path.write_text("[client]\nretries = 6\n", encoding="utf-8")
    monkeypatch.setenv("SOFASCORE_CLIENT__WAIT_TIME_MIN", "1.5")   # ortam değişti: ayarlar yeniden kurulur, dosya okunmaz
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
    monkeypatch.delenv("SOFASCORE_STORAGE__DATA_DIR")
    before = dict(os.environ)
    explicit = tmp_path / "other.toml"
    explicit.write_text('[storage]\ndata_dir = "from-file"\n[client]\nretries = 9\n', encoding="utf-8")
    loaded = loader.activate(config_file=explicit, flags={"storage.data_dir": str(tmp_path / "from-flag")})
    cm = ConfigManager()
    assert loaded.config_file == str(explicit)
    assert (cm.get_data_dir(), cm.get_max_retries()) == (str(tmp_path / "from-flag"), 9)
    assert dict(os.environ) == before                  # bayrak ortama yazılmaz
    loader.reset()
    assert cm.get_max_retries() == 3


def test_secrets_named_by_the_config_file_reach_their_readers_and_are_masked(active, monkeypatch):
    """
    token_env / proxy_env başka bir değişkeni gösterse de değer, belirteci ve proxy'yi okuyan koda
    (sofascore_scraper/web/security.py, sofascore_scraper/client/bridge.py) ayarlardan ulaşır ve loglarda maskelenir.
    """
    from sofascore_scraper import redact

    token, proxy = "tok-0123456789abcdef", "http://scraper:pr0xy-passw0rd@proxy.example.com:8080"
    monkeypatch.setenv("MY_TOKEN", token)
    monkeypatch.setenv("MY_PROXY", proxy)
    before = dict(os.environ)
    active('[server]\ntoken_env = "MY_TOKEN"\n[client]\nproxy_env = "MY_PROXY"\n')
    cm = ConfigManager()
    assert security.api_token() == token and security.token_variable() == "MY_TOKEN"
    assert (cm.get_use_proxy(), cm.get_proxy_url()) == (True, proxy)
    text = redact.redact_text(f"token {token} via pr0xy-passw0rd")
    assert token not in text and "pr0xy-passw0rd" not in text
    # Adı verilen değişken boşalırsa koruma sessizce kapanmaz: ayarlar kurulamaz
    monkeypatch.setenv("MY_TOKEN", "")
    with pytest.raises(ConfigError, match="token_env: the environment variable MY_TOKEN is not set"):
        cm.get_data_dir()
    monkeypatch.setenv("MY_TOKEN", token)
    assert cm.get_settings().server.token == token
    loader.reset()
    before[loader.CONFIG_ENV] = os.environ[loader.CONFIG_ENV]
    assert dict(os.environ) == before


def test_log_settings_from_the_config_file_reach_the_running_logger(active):
    root = logging.getLogger()
    level_before, handler_before = root.level, app_logger._file_handler
    assert handler_before is not None and handler_before.backupCount == app_logger.DEFAULT_BACKUP_COUNT
    active('[log]\nlevel = "error"\n')
    assert root.level == logging.ERROR and "SOFASCORE_LOG__LEVEL" not in os.environ
    assert app_logger._file_handler is handler_before            # yalnızca seviye: log kurulumu yenilenmez
    active('[log]\nlevel = "error"\nbackup_count = 2\n')
    assert app_logger._file_handler.backupCount == 2
    active("")                                                     # dosyada log ayarı kalmadı: varsayılanlar
    assert root.level == level_before
    assert app_logger._file_handler.backupCount == app_logger.DEFAULT_BACKUP_COUNT


def _run_python(code: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    """Kodu temiz bir süreçte çalıştırır: gerçek `.env`, config/ ve log dizinine dokunmaz."""
    clean = {k: v for k, v in os.environ.items() if k not in loader.LEGACY_ENV_NAMES and not k.startswith("SOFASCORE_")}
    clean.update({
        "SOFASCORE_ENV_FILE": str(tmp_path / ".env"), "SOFASCORE_CONFIG_DIR": str(tmp_path / "config"),
        "SOFASCORE_CLIENT__THROTTLE_DIR": str(tmp_path / "throttle"), "SOFASCORE_LOG__DIR": str(tmp_path / "logs"),
        "LC_MESSAGES": "C",
        # Log satırları (Türkçe harfler) borudan her platformda aynı kodlamayla geçsin
        "PYTHONIOENCODING": "utf-8",
        **env,
    })
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, encoding="utf-8", errors="replace",
        timeout=120,
    )


def test_at_start_up_the_config_file_reaches_the_modules(tmp_path):
    """
    Uçtan uca, ayrı bir süreçte, main.py'nin içe aktarma sırasıyla (önce config_manager): dosya içe aktarma
    sırasında okunur; bütçe ve yenileme modülleri, sofascore_scraper/client/transport.py'nin içe aktarılırken donan
    sabiti ve "yalnızca bitmiş maçlar" ayarı onu görür.
    """
    config = tmp_path / "my.toml"
    config.write_text(
        '[client]\nrate = 2\nbase_url = "https://api.example.invalid/api/v1"\n[storage]\ndata_dir = "d"\n'
        "[refresh]\nwindow_hours = 1.5\n[fetch]\nonly_finished = false\n",
        encoding="utf-8",
    )
    code = (
        "import json, os\n"
        "import sofascore_scraper.config_manager as cm\n"
        "from sofascore_scraper import refresh, throttle\n"
        "from sofascore_scraper.client import transport\n"
        "from sofascore_scraper.services.status import only_finished_setting\n"
        "print(json.dumps([throttle.configured_rate(), refresh.refresh_window_hours(), cm.ConfigManager().get_data_dir(),"
        " cm.ConfigManager().get_request_rate_limit(), transport.API_BASE_URL, only_finished_setting()]))\n"
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
        "import sofascore_scraper.config_manager as cm\n"
        "from sofascore_scraper import throttle\n"
        "from sofascore_scraper.client import transport\n"
        "from sofascore_scraper.services.status import only_finished_setting\n"
        "names = set(os.environ) - set(before)\n"
        f"added = sorted(n for n in names if n.startswith('SOFASCORE_') or n in {sorted(loader.LEGACY_ENV_NAMES)!r})\n"
        "print(json.dumps([throttle.configured_rate(), added, cm.ConfigManager().get_data_dir(),"
        " transport.API_BASE_URL, only_finished_setting()]))\n"
    )
    done = _run_python(code, tmp_path, SOFASCORE_CONFIG="none")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [
        5.0, [], "data", "https://www.sofascore.com/api/v1", True,
    ]


def test_a_broken_config_file_stops_the_application_at_start_up(tmp_path):
    config = tmp_path / "broken.toml"
    config.write_text("[client]\nrate = 'fast'\n", encoding="utf-8")
    done = _run_python("import sofascore_scraper.config_manager\n", tmp_path, SOFASCORE_CONFIG=str(config))
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
        "default": 5.0, "x-env": "SOFASCORE_CLIENT__RATE",
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
    assert properties["display"]["properties"]["language"]["x-env"] == "SOFASCORE_DISPLAY__LANGUAGE"
    assert "x-legacy-env" not in json.dumps(schema)
    assert properties["server"]["properties"]["allowed_hosts"]["default"] == ["localhost", "127.0.0.1", "[::1]"]
    assert set(properties["schedule"]["properties"]) == {"enabled", "task"}
    assert set(properties["slices"]["properties"]) == set(__import__("sofascore_scraper.sports", fromlist=["sport_slugs"]).sport_slugs())
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
