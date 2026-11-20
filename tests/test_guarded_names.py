"""
3.2'de kalkan korumalı 2.x adları: SOFASCORE_API_TOKEN, SOFASCORE_ALLOWED_HOSTS, USE_PROXY, PROXY_URL
(`loader.GUARDED_NAMES`). 3.1 onları bir uyarıyla hâlâ okuyordu (plan maddesi FX-35); 3.2'de hiç okunmazlar ve
kapalı kalarak düşerler (sahibin kararı): ikisi sunucuyu korur (erişim belirteci, Host izin listesi), ikisi istekleri
proxy'den geçirir. Sessizce okunmamaları yükseltmede sunucuyu açık bırakır ya da istekleri kullanıcının kendi
adresinden gönderirdi (3.1.0'ın sürüm denetiminde yakalanan açık kalma). Burada denetlenenler, her ad için:

  * yalnızca eski ad: ayarlar yüklenmez (GuardedNameError, bir ConfigError: CLI'de `config_invalid` ve çıkış kodu
    2, sunucu başlamaz, `ssc doctor` FAIL); ileti eski adı, yeni ortam adını ve 3.2'de kalktığını söyler, değeri
    yazmaz;
  * yalnızca yeni ad: uyarı yok;
  * ikisi de: yeni ad geçerlidir, eski ad yok sayılır ve bir uyarıdır;
  * eski addan hiçbir değer okunmaz; dosyadaki değer yeni adın yerini tutmaz (eski ad onu ezerdi);
  * Docker giriş noktası eski izin listesi adını liste saymaz ve yerel adları da yazmaz: konteyner başlamaz.

Diğer 2.x adlarının okunmadığını söyleyen P30 testleri yerindedir (test_config_loader, test_doctor...).
Ağ yok: istekler uygulamaya doğrudan (TestClient) gider; sunucu sahtedir.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pytest

import test_cli_skeleton as skeleton
from sofascore_scraper import diagnostics, doctor, redact
from sofascore_scraper.config import loader
from sofascore_scraper.errors import to_platform_error
from sofascore_scraper.web import security
from test_cli_serve import FakeServer, server, serve  # noqa: F401  (fikstürler)
from test_doctor import make_ctx  # noqa: F401  (fikstür)

cli = skeleton.cli
REPO = Path(__file__).resolve().parents[1]

TOKEN = "removed-token-0123456789abcdef0123"
NEW_TOKEN = "new-token-abcdef0123456789abcdef01234"
PROXY_SECRET = "pr0xy-Pa55-word"
PROXY = f"http://scraper:{PROXY_SECRET}@proxy.example.com:8080"
NEW_PROXY = "http://new.example.com:3128"

# Eski ad -> (ayar, eski adın ham değeri, yeni adın ham değeri ve beklenen değer)
CASES: Dict[str, Any] = {
    "SOFASCORE_API_TOKEN": ("server.token", f"  {TOKEN} ", (NEW_TOKEN, NEW_TOKEN)),
    "SOFASCORE_ALLOWED_HOSTS": ("server.allowed_hosts", "box.lan, 127.0.0.1", ("new.lan", ("new.lan",))),
    "USE_PROXY": ("client.use_proxy", "true", ("false", False)),
    "PROXY_URL": ("client.proxy", PROXY, (NEW_PROXY, NEW_PROXY)),
}
NEW_NAMES = {
    "SOFASCORE_API_TOKEN": "SOFASCORE_SERVER__TOKEN",
    "SOFASCORE_ALLOWED_HOSTS": "SOFASCORE_SERVER__ALLOWED_HOSTS",
    "USE_PROXY": "SOFASCORE_CLIENT__USE_PROXY",
    "PROXY_URL": "SOFASCORE_CLIENT__PROXY",
}
PROTECTS = {
    "SOFASCORE_API_TOKEN": "the access token",
    "SOFASCORE_ALLOWED_HOSTS": "the Host allow-list",
    "USE_PROXY": "the proxy",
    "PROXY_URL": "the proxy",
}


def _load(env: Mapping[str, str], dotenv: Optional[Mapping[str, str]] = None, toml: Optional[str] = None,
          tmp_path: Optional[Path] = None, flags: Optional[Mapping[str, Any]] = None) -> loader.LoadedSettings:
    config_file = None
    if toml is not None:
        assert tmp_path is not None
        config_file = tmp_path / "sofascore.toml"
        config_file.write_text(toml, encoding="utf-8")
    return loader.load_settings(config_file=config_file, environ=dict(env), dotenv_values=dict(dotenv or {}),
                                overrides_file=None, flags=flags)


def _messages(loaded: loader.LoadedSettings) -> list:
    return [warning.message for warning in loaded.warnings if warning.code == "legacy_name"]


def _refusal(name: str, where: str = "the environment") -> str:
    return (f"{name} (set in {where}) was removed in 3.2 and is no longer read: set {NEW_NAMES[name]} instead, or "
            f"remove {name}. The settings are not loaded, so that {PROTECTS[name]} is not dropped without a word.")


def test_the_guarded_names_are_exactly_the_four_and_all_are_legacy_names() -> None:
    assert dict(loader.GUARDED_NAMES) == {name: key for name, (key, _old, _new) in CASES.items()}
    for name, key in loader.GUARDED_NAMES.items():
        assert loader.LEGACY_NAMES[name] == key
        assert loader.env_name(key) == NEW_NAMES[name]
    assert loader.GUARDED_REMOVED_IN == "3.2"
    # "Hâlâ okunur" yolu kalktı: değerin eski addan geldiğini söyleyen bir kaynak ya da satır alanı yok
    assert not hasattr(loader, "DEPRECATED_NAMES") and not hasattr(loader, "LEGACY_READ")
    assert not hasattr(loader.LoadedSettings, "replaced_by")
    assert "deprecated" not in loader.Source.__dataclass_fields__
    assert all("replaced_by" not in row for row in _load({}).describe())


# --- yükleyici: her ad için üç durum ---------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CASES))
def test_old_name_only_refuses_to_load_and_says_what_to_set_without_the_value(name: str) -> None:
    _key, raw, _new = CASES[name]
    with pytest.raises(loader.GuardedNameError) as refused:
        _load({name: raw})
    error = refused.value
    assert isinstance(error, loader.ConfigError)
    assert str(error) == _refusal(name)
    assert error.names == (name,)
    assert raw.strip() not in str(error) and PROXY_SECRET not in str(error)
    # CLI ve API için `config_invalid` (çıkış kodu 2)
    assert to_platform_error(error).code == "config_invalid"
    # `.env`'de duran ad (uygulama onu ortama yükler) ".env" diye söylenir; ortama yüklenmemiş olsa da sayılır
    for env in ({name: raw}, {}):
        with pytest.raises(loader.GuardedNameError) as in_file:
            _load(env, dotenv={name: raw})
        assert str(in_file.value) == _refusal(name, ".env")


@pytest.mark.parametrize("name", sorted(CASES))
def test_new_name_only_gives_no_warning(name: str) -> None:
    key, _old, (raw, expected) = CASES[name]
    loaded = _load({loader.env_name(key): raw})
    assert loaded.settings.get(key) == expected
    assert loaded.source(key) == loader.Source(loader.LAYER_ENV, loader.env_name(key))
    assert loaded.warnings == ()


@pytest.mark.parametrize("name", sorted(CASES))
def test_both_names_the_new_one_is_used_and_the_old_one_is_reported_as_ignored(name: str) -> None:
    key, old_raw, (new_raw, expected) = CASES[name]
    new = loader.env_name(key)
    loaded = _load({name: old_raw, new: new_raw})
    assert loaded.settings.get(key) == expected
    assert loaded.source(key) == loader.Source(loader.LAYER_ENV, new)
    assert _messages(loaded) == [
        f"{name} (set in the environment) is ignored because {new} is also set; {name} was removed in 3.2: remove it.",
    ]


def test_every_blocking_name_is_named_in_one_error() -> None:
    env = {name: raw for name, (_key, raw, _new) in CASES.items()}
    with pytest.raises(loader.GuardedNameError) as refused:
        _load(env)
    message = str(refused.value)
    assert set(refused.value.names) == set(CASES)
    for name in CASES:
        assert f"{name} (set in the environment) was removed in 3.2" in message and NEW_NAMES[name] in message
    assert message.endswith("so that the proxy, the Host allow-list and the access token are not dropped without a word.")
    assert TOKEN not in message and PROXY_SECRET not in message
    # Biri yeni adıyla verildiyse yalnızca diğerleri engeller
    with pytest.raises(loader.GuardedNameError) as rest:
        _load({**env, "SOFASCORE_SERVER__TOKEN": NEW_TOKEN})
    assert "SOFASCORE_API_TOKEN" not in rest.value.names and len(rest.value.names) == 3


def test_a_value_in_the_config_file_does_not_stand_in_for_the_new_name(tmp_path: Path) -> None:
    """
    Eski ad ortam katmanındaydı ve dosyayı ezerdi: dosyaya düşmek 3.1'in kullandığı değeri sessizce değiştirirdi
    (dosyadaki `use_proxy = false` proxy'yi kapatırdı). Bayrak ise yeni addır (eski adı 3.1'de de ezerdi).
    """
    toml = 'schema = 1\n[server]\nallowed_hosts = ["file.lan"]\n[client]\nuse_proxy = false\n'
    with pytest.raises(loader.GuardedNameError) as refused:
        _load({"SOFASCORE_ALLOWED_HOSTS": "old.lan", "USE_PROXY": "true"}, toml=toml, tmp_path=tmp_path)
    assert refused.value.names == ("USE_PROXY", "SOFASCORE_ALLOWED_HOSTS")
    flagged = _load({"SOFASCORE_ALLOWED_HOSTS": "old.lan"}, flags={"server.allowed_hosts": "flag.lan"})
    assert flagged.settings.server.allowed_hosts == ("flag.lan",)
    assert _messages(flagged) == [
        "SOFASCORE_ALLOWED_HOSTS (set in the environment) is ignored because a command-line flag is also set; "
        "SOFASCORE_ALLOWED_HOSTS was removed in 3.2: remove it.",
    ]


def test_token_env_and_proxy_env_stand_in_for_the_new_names(tmp_path: Path) -> None:
    """`token_env` / `proxy_env` yeni yapılandırmadır: başka bir değişkeni adlandırıyorsa eski ad yok sayılır."""
    toml = 'schema = 1\n[client]\nproxy_env = "MY_PROXY"\n[server]\ntoken_env = "MY_TOKEN"\n'
    env = {"SOFASCORE_API_TOKEN": TOKEN, "PROXY_URL": PROXY, "USE_PROXY": "false", "MY_TOKEN": NEW_TOKEN,
           "MY_PROXY": NEW_PROXY}
    loaded = _load(env, toml=toml, tmp_path=tmp_path)
    assert (loaded.settings.server.token, loaded.settings.client.proxy) == (NEW_TOKEN, NEW_PROXY)
    # Adı verilen değişkenden gelen proxy kendiliğinden açılır (karar D19): eski USE_PROXY=false onu kapatmaz
    assert loaded.settings.client.use_proxy is True
    messages = _messages(loaded)
    assert any(m.startswith("SOFASCORE_API_TOKEN (set in the environment) is ignored because MY_TOKEN") for m in messages)
    assert any(m.startswith("PROXY_URL (set in the environment) is ignored because MY_PROXY") for m in messages)
    assert any(m.startswith("USE_PROXY (set in the environment) is ignored because MY_PROXY") for m in messages)
    # `ssc config init --from-legacy` eski adı adlandırır: o zaman okunan değişkendir, uyarı ve hata yoktur
    named = 'schema = 1\n[client]\nproxy_env = "PROXY_URL"\nuse_proxy = true\n[server]\ntoken_env = "SOFASCORE_API_TOKEN"\n'
    loaded = _load({"SOFASCORE_API_TOKEN": TOKEN, "PROXY_URL": PROXY}, toml=named, tmp_path=tmp_path)
    assert (loaded.settings.server.token, loaded.settings.client.proxy) == (TOKEN, PROXY)
    assert loaded.warnings == ()


def test_a_new_proxy_switches_the_proxy_on_and_the_old_use_proxy_is_ignored() -> None:
    """Yeni adla verilen proxy kendiliğinden açılır (D19); eski USE_PROXY artık onu açıp kapatmaz."""
    for raw in ("true", "false"):
        loaded = _load({"SOFASCORE_CLIENT__PROXY": NEW_PROXY, "USE_PROXY": raw})
        assert (loaded.settings.client.proxy, loaded.settings.client.use_proxy) == (NEW_PROXY, True)
        assert _messages(loaded) == [
            "USE_PROXY (set in the environment) is ignored because SOFASCORE_CLIENT__PROXY is also set; USE_PROXY "
            "was removed in 3.2: remove it.",
        ]


def test_no_value_is_ever_taken_from_an_old_name() -> None:
    """Geçersiz bir eski değer de ayrıştırılmaz: eski ad yalnızca var olup olmadığıyla sayılır."""
    with pytest.raises(loader.GuardedNameError) as refused:
        _load({"USE_PROXY": "maybe"})
    assert "expected true or false" not in str(refused.value)
    ignored = _load({"USE_PROXY": "maybe", "SOFASCORE_CLIENT__USE_PROXY": "false"})
    assert ignored.settings.client.use_proxy is False
    # Boş bırakılmış eski ad yok sayılır ve sessizdir
    assert _load({"SOFASCORE_API_TOKEN": " ", "USE_PROXY": ""}).warnings == ()


def test_other_legacy_names_are_still_only_warnings() -> None:
    """P30 değişmedi: korumalı dördü dışındaki 2.x adları okunmaz ve yalnızca uyarıdır."""
    loaded = _load({"DATA_DIR": "/elsewhere", "SOFA_CAPTCHA_TOKEN": "abcdef", "API_BASE_URL": "https://x.example/api",
                    "SOFASCORE_THROTTLE_DIR": "/tmp/t", "REQUEST_RATE_LIMIT": "9", "MAX_CONCURRENT": "40"})
    assert loaded.settings == _load({}).settings
    assert all("no longer read since 3.1" in message for message in _messages(loaded)) and len(_messages(loaded)) == 6


# --- süreç: sunucu, CLI, doctor, tanılama -------------------------------------------------------------


def test_the_web_app_refuses_to_start_with_the_old_token_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    monkeypatch.delenv("SOFASCORE_SERVER__TOKEN_ENV", raising=False)
    monkeypatch.setenv("SOFASCORE_API_TOKEN", TOKEN)
    with pytest.raises(loader.GuardedNameError):
        security.api_token()
    with pytest.raises(loader.GuardedNameError):
        security.allowed_hosts()
    # Yeni adla: yeni belirteç geçerli, eskisi hiçbir yerde kullanılmaz
    monkeypatch.setenv(security.TOKEN_ENV, NEW_TOKEN)
    assert security.api_token() == NEW_TOKEN and security.token_variable() == "SOFASCORE_SERVER__TOKEN"


def test_serve_refuses_to_start_with_the_old_allowed_hosts_name(
        cli: skeleton.CliRunner, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("SOFASCORE_ALLOWED_HOSTS", "localhost,my-server.lan")
    for argv in (("--host", "0.0.0.0", "--json"), ("--json",)):
        run = serve(cli, *argv)
        assert run.exit_code == 2 and server.calls == []
        assert run.json["error"]["code"] == "config_invalid"
        assert run.json["error"]["message"] == _refusal("SOFASCORE_ALLOWED_HOSTS")
    # Yeni adla başlar; eski ad yok sayıldığını söyleyen bir uyarıdır
    monkeypatch.setenv("SOFASCORE_SERVER__ALLOWED_HOSTS", "localhost,my-server.lan")
    run = serve(cli, "--host", "0.0.0.0", "--json")
    assert run.exit_code == 0, run.stderr
    assert run.data["allowed_hosts"] == ["localhost", "my-server.lan"]
    assert any(w["code"] == "legacy_name" and w["message"].startswith("SOFASCORE_ALLOWED_HOSTS (set in the environment) "
                                                                       "is ignored") for w in run.json["warnings"])


def test_every_command_that_loads_the_settings_stops_with_exit_code_2(cli: skeleton.CliRunner,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    monkeypatch.setenv("SOFASCORE_API_TOKEN", TOKEN)
    monkeypatch.setenv("PROXY_URL", PROXY)
    for argv in (("config", "show", "--json"), ("config", "validate", "--json"), ("status", "--json")):
        run = cli(*argv)
        assert run.exit_code == 2, argv
        assert run.json["error"]["code"] == "config_invalid"
        message = run.json["error"]["message"]
        assert "SOFASCORE_API_TOKEN (set in the environment) was removed in 3.2" in message
        assert "set SOFASCORE_CLIENT__PROXY instead" in message
        assert TOKEN not in run.stdout + run.stderr and PROXY_SECRET not in run.stdout + run.stderr
    text = cli("config", "show")
    assert text.exit_code == 2 and "SOFASCORE_SERVER__TOKEN" in text.stderr and TOKEN not in text.stderr


@pytest.mark.parametrize("name", sorted(CASES))
def test_doctor_fails_on_the_old_name_and_says_where_it_is(make_ctx, name: str) -> None:  # noqa: F811
    key, raw, (new_raw, _expected) = CASES[name]
    new = loader.env_name(key)
    for ctx, where, origin in (
        (make_ctx(env_text=f"{name}={raw}\n"), ".env", "env_file"),
        (make_ctx(env_text="", environ={name: raw}), "the environment", "environment"),
    ):
        env = doctor.check_env(ctx)
        assert env.status == doctor.FAIL
        assert env.summary == (f"{name} (set in {where}) was removed in 3.2 and is no longer read; the settings do not "
                               f"load until it is replaced: set {new} instead, or remove {name}")
        assert [(p["key"], p["origin"], p["status"]) for p in env.detail["problems"]] == [(name, origin, "fail")]
        config = doctor.check_config(ctx)
        assert (config.status, config.code) == (doctor.FAIL, "config_removed_name")
        assert config.detail["removed_names"] == [name]
        assert config.summary == (f"The settings do not load: a 2.x name removed in 3.2 is set without its new name "
                                  f"({name}); it guards the access token, the Host allow-list or the proxy, so nothing "
                                  f"starts without it")
        assert f"{name} -> {new}" in config.fix
        flat = json.dumps(env.to_dict()) + json.dumps(config.to_dict())
        assert TOKEN not in flat and PROXY_SECRET not in flat
    both = make_ctx(env_text=f"{name}={raw}\n{new}={new_raw}\n")
    ignored = doctor.check_env(both)
    assert ignored.status == doctor.WARN
    assert ignored.summary == (f"{name} (set in .env) is ignored because {new} is also set; {name} was removed in "
                               f"3.2: remove it")
    assert doctor.check_config(both).code != "config_removed_name"
    tr = make_ctx(env_text="", environ={name: raw}, lang="tr")
    assert f"{name} (ortamda tanımlı) 3.2'de kaldırıldı ve artık okunmuyor" in doctor.check_env(tr).summary
    assert doctor.check_config(tr).summary.startswith("Ayarlar yüklenmiyor: 3.2'de kaldırılan bir 2.x adı")


def test_doctor_does_not_blame_a_covered_old_name_when_the_config_file_is_broken(make_ctx, tmp_path) -> None:  # noqa: F811
    """Ayarlar başka bir nedenle kurulamıyorsa yeni adın ortamda olup olmadığına bakılır."""
    ctx = make_ctx(env_text="SOFASCORE_API_TOKEN=abcdefghijkl\nSOFASCORE_SERVER__TOKEN=mnopqrstuvwx\n",
                   environ={"SOFASCORE_CONFIG": str(tmp_path / "missing.toml")})
    assert doctor.check_config(ctx).code == "config_invalid"
    assert doctor.check_env(ctx).status == doctor.WARN


def test_diagnostics_report_the_refusal_without_the_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    monkeypatch.delenv("SOFASCORE_SERVER__ALLOWED_HOSTS")
    for name, value in (("SOFASCORE_API_TOKEN", TOKEN), ("PROXY_URL", PROXY), ("USE_PROXY", "true"),
                        ("SOFASCORE_ALLOWED_HOSTS", "box.lan")):
        monkeypatch.setenv(name, value)
    redact.refresh()
    try:
        doc = diagnostics.collect()
        settings = doc["settings"]
        assert settings["error"].startswith("GuardedNameError: USE_PROXY (set in the environment) was removed in 3.2")
        assert set(settings["legacy_names"]) == set(CASES)
        flat = json.dumps(doc, ensure_ascii=False)
        assert TOKEN not in flat and PROXY_SECRET not in flat
    finally:
        monkeypatch.undo()
        redact.refresh()


# --- Docker giriş noktası ------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("sh") is None or os.name == "nt", reason="needs a POSIX sh")
@pytest.mark.parametrize("where", ["environment", ".env"])
def test_the_container_does_not_start_with_the_old_allowed_hosts_name(tmp_path: Path, where: str) -> None:
    """
    Giriş noktası eski adı liste saymaz, yerel adları da yazmaz (yazsaydı eski ad "yeni adı da verilmiş" sayılıp
    yok sayılır, konteyner kullanıcının listesi olmadan başlardı): gerçek `serve` yükleyicinin iletisiyle, çıkış
    kodu 2 ile durur. Ağa çıkılmaz: ayarlar yüklenemediği için sunucu kurulmaz.
    """
    app = tmp_path / "app"
    (app / "config").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    env_file = app / "config" / ".env"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(tmp_path), "APP_HOME": str(app),
        "SOFASCORE_ENV_FILE": str(env_file), "SOFASCORE_CONFIG": "none", "SOFASCORE_CONFIG_DIR": str(app / "config"),
        "SOFASCORE_STORAGE__DATA_DIR": str(tmp_path / "data"), "SOFASCORE_LOG__DIR": str(tmp_path / "logs"),
        "SOFASCORE_LOG__TO_FILE": "false", "SOFASCORE_DISPLAY__LANGUAGE": "en", "PORT": "1",
        "PYTHONPATH": str(REPO),
    }
    if where == ".env":
        env_file.write_text("SOFASCORE_ALLOWED_HOSTS=box.lan\n", encoding="utf-8")
    else:
        env["SOFASCORE_ALLOWED_HOSTS"] = "box.lan"
    proc = subprocess.run(["sh", str(REPO / "docker" / "entrypoint.sh")], env=env, capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    origin = ".env" if where == ".env" else "the environment"
    assert (f"SOFASCORE_ALLOWED_HOSTS (set in {origin}) was removed in 3.2 and is no longer read: set "
            f"SOFASCORE_SERVER__ALLOWED_HOSTS instead") in proc.stderr
    assert "box.lan" not in proc.stderr


def test_the_old_names_never_reach_the_log(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Hata iletisinde ve günlükte değerler yoktur; maskeleme 2.x adlarının değerlerini de kapsar."""
    monkeypatch.setenv("PROXY_URL", PROXY)
    redact.refresh()
    try:
        with caplog.at_level(logging.WARNING), pytest.raises(loader.GuardedNameError):
            loader.reset()
            loader.active()
        assert PROXY_SECRET not in caplog.text
        assert PROXY_SECRET not in redact.redact_text(f"connecting through {PROXY}")
    finally:
        monkeypatch.undo()
        loader.reset()
        redact.refresh()
