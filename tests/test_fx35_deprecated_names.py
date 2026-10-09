"""
Kullanımdan kalkan ama 3.1'de hâlâ okunan 2.x adları (plan maddesi FX-35; sahibin kararı 2026-10-09):
SOFASCORE_API_TOKEN, SOFASCORE_ALLOWED_HOSTS, USE_PROXY, PROXY_URL (`loader.DEPRECATED_NAMES`).

3.0'ın belgeleri sunucuyu SOFASCORE_API_TOKEN ve SOFASCORE_ALLOWED_HOSTS ile korumayı söylüyordu. P30 (#186) 2.x
adlarını okumayı bıraktı: adları değiştirmeden 3.1'e geçen kurulumda erişim belirteci sessizce kapanırdı (API
belirteçsiz yanıt verir) ve Host izin listesi düşerdi; USE_PROXY / PROXY_URL düşünce SofaScore istekleri
yapılandırılmış proxy yerine kullanıcının kendi adresinden giderdi. Burada denetlenenler, her ad için:

  * yalnızca eski ad: değer kullanılır, uyarı okunduğunu ve 3.2'de kalkacağını söyler;
  * yalnızca yeni ad: uyarı yok;
  * ikisi de: yeni ad kazanır, uyarı eski adın yok sayıldığını söyler;
  * eski addan gelen belirteç /api/v1'i (kök yol altında da) korur; eski addan gelen izin listesiyle
    `serve --host 0.0.0.0` başlar; gizli değerler her yerde maskelidir ve dosyaya yazılmaz.

Diğer 2.x adlarının okunmadığını söyleyen P30 testleri yerindedir (test_config_loader, test_doctor...).
Ağ yok: istekler uygulamaya doğrudan (TestClient) gider; sunucu sahtedir.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

import pytest
from fastapi.testclient import TestClient

import test_cli_skeleton as skeleton
from sofascore_scraper import diagnostics, doctor, redact
from sofascore_scraper.config import loader, overrides
from sofascore_scraper.web import security
from sofascore_scraper.web.app import app
from test_cli_serve import FakeServer, server, serve  # noqa: F401  (fikstürler)
from test_doctor import make_ctx  # noqa: F401  (fikstür)

cli = skeleton.cli

TOKEN = "deprecated-token-0123456789abcdef0123"
NEW_TOKEN = "new-token-abcdef0123456789abcdef01234"
PROXY_SECRET = "pr0xy-Pa55-word"
PROXY = f"http://scraper:{PROXY_SECRET}@proxy.example.com:8080"
NEW_PROXY = "http://new.example.com:3128"

# Eski ad -> (ham değer, beklenen değer), yeni adın ham değeri ve beklenen değeri
CASES: Dict[str, Any] = {
    "SOFASCORE_API_TOKEN": ("server.token", (f"  {TOKEN} ", TOKEN), (NEW_TOKEN, NEW_TOKEN)),
    "SOFASCORE_ALLOWED_HOSTS": ("server.allowed_hosts", ("box.lan, 127.0.0.1", ("box.lan", "127.0.0.1")),
                                ("new.lan", ("new.lan",))),
    "USE_PROXY": ("client.use_proxy", ("true", True), ("false", False)),
    "PROXY_URL": ("client.proxy", (PROXY, PROXY), (NEW_PROXY, NEW_PROXY)),
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


def test_the_deprecated_names_are_exactly_the_four_and_all_are_legacy_names() -> None:
    assert dict(loader.DEPRECATED_NAMES) == {name: key for name, (key, _old, _new) in CASES.items()}
    for name, key in loader.DEPRECATED_NAMES.items():
        assert loader.LEGACY_NAMES[name] == key
    assert (loader.DEPRECATED_LAST_READ_IN, loader.DEPRECATED_REMOVED_IN) == ("3.1", "3.2")


# --- yükleyici: her ad için üç durum ---------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CASES))
def test_old_name_only_is_read_with_a_deprecation_warning(name: str) -> None:
    key, (raw, expected), _new = CASES[name]
    loaded = _load({name: raw})
    assert loaded.settings.get(key) == expected
    assert loaded.source(key) == loader.Source(loader.LAYER_ENV, name, deprecated=True)
    assert loaded.source(key).locked  # Ayarlar sayfasında kilitli
    assert loaded.replaced_by(key) == loader.env_name(key)
    replacement = loader.legacy_replacement(name)
    assert _messages(loaded) == [
        f"{name} (set in the environment) is deprecated; still read in 3.1, removed in 3.2; use {replacement}.",
    ]
    # `.env`'de duran ad (uygulama onu ortama yükler) ".env" diye söylenir
    in_file = _load({name: raw}, dotenv={name: raw})
    assert in_file.settings.get(key) == expected
    assert _messages(in_file) == [
        f"{name} (set in .env) is deprecated; still read in 3.1, removed in 3.2; use {replacement}.",
    ]


@pytest.mark.parametrize("name", sorted(CASES))
def test_new_name_only_gives_no_warning(name: str) -> None:
    key, _old, (raw, expected) = CASES[name]
    loaded = _load({loader.env_name(key): raw})
    assert loaded.settings.get(key) == expected
    assert loaded.source(key) == loader.Source(loader.LAYER_ENV, loader.env_name(key))
    assert loaded.replaced_by(key) is None
    assert loaded.warnings == ()


@pytest.mark.parametrize("name", sorted(CASES))
def test_both_names_the_new_one_wins_and_the_old_one_is_reported_as_ignored(name: str) -> None:
    key, (old_raw, _old_expected), (new_raw, expected) = CASES[name]
    new = loader.env_name(key)
    loaded = _load({name: old_raw, new: new_raw})
    assert loaded.settings.get(key) == expected
    assert loaded.source(key) == loader.Source(loader.LAYER_ENV, new)
    assert _messages(loaded) == [
        f"{name} (set in the environment) is ignored because {new} is also set; {name} is deprecated and removed "
        f"in 3.2: remove it.",
    ]


def test_the_old_name_ranks_like_the_environment_above_the_file_and_below_a_flag(tmp_path: Path) -> None:
    """Ortam katmanındadır (3.0'daki gibi): dosyayı ezer; bayrak onu ezer (eski ad yok sayılır)."""
    toml = 'schema = 1\n[server]\nallowed_hosts = ["file.lan"]\n'
    loaded = _load({"SOFASCORE_ALLOWED_HOSTS": "old.lan"}, toml=toml, tmp_path=tmp_path)
    assert loaded.settings.server.allowed_hosts == ("old.lan",)
    flagged = _load({"SOFASCORE_ALLOWED_HOSTS": "old.lan"}, flags={"server.allowed_hosts": "flag.lan"})
    assert flagged.settings.server.allowed_hosts == ("flag.lan",)
    assert _messages(flagged) == [
        "SOFASCORE_ALLOWED_HOSTS (set in the environment) is ignored because a command-line flag is also set; "
        "SOFASCORE_ALLOWED_HOSTS is deprecated and removed in 3.2: remove it.",
    ]


def test_token_env_and_proxy_env_win_over_the_old_names(tmp_path: Path) -> None:
    """`token_env` / `proxy_env` yeni yapılandırmadır: başka bir değişkeni adlandırıyorsa eski ad okunmaz."""
    toml = 'schema = 1\n[client]\nproxy_env = "MY_PROXY"\n[server]\ntoken_env = "MY_TOKEN"\n'
    env = {"SOFASCORE_API_TOKEN": TOKEN, "PROXY_URL": PROXY, "MY_TOKEN": NEW_TOKEN, "MY_PROXY": NEW_PROXY}
    loaded = _load(env, toml=toml, tmp_path=tmp_path)
    assert (loaded.settings.server.token, loaded.settings.client.proxy) == (NEW_TOKEN, NEW_PROXY)
    messages = _messages(loaded)
    assert any(m.startswith("SOFASCORE_API_TOKEN (set in the environment) is ignored because MY_TOKEN") for m in messages)
    assert any(m.startswith("PROXY_URL (set in the environment) is ignored because MY_PROXY") for m in messages)
    # `ssc config init --from-legacy` eski adı adlandırır: o zaman okunur ve uyarı yoktur
    named = 'schema = 1\n[client]\nproxy_env = "PROXY_URL"\nuse_proxy = true\n[server]\ntoken_env = "SOFASCORE_API_TOKEN"\n'
    loaded = _load({"SOFASCORE_API_TOKEN": TOKEN, "PROXY_URL": PROXY}, toml=named, tmp_path=tmp_path)
    assert (loaded.settings.server.token, loaded.settings.client.proxy) == (TOKEN, PROXY)
    assert not loaded.source("server.token").deprecated and loaded.warnings == ()


def test_proxy_url_alone_does_not_switch_the_proxy_on_as_in_3_0() -> None:
    """3.0'da (2.x'te de) PROXY_URL proxy'yi tek başına açmazdı: USE_PROXY=true gerekirdi (karar D19 yeni adlar için)."""
    alone = _load({"PROXY_URL": PROXY}).settings.client
    assert (alone.proxy, alone.use_proxy) == (PROXY, False)
    on = _load({"PROXY_URL": PROXY, "USE_PROXY": "True"}).settings.client
    assert (on.proxy, on.use_proxy) == (PROXY, True)
    # Yeni adla verilen proxy kendiliğinden açılır; eski USE_PROXY=false onu kapatır (3.0'daki gibi)
    assert _load({"SOFASCORE_CLIENT__PROXY": NEW_PROXY}).settings.client.use_proxy is True
    assert _load({"SOFASCORE_CLIENT__PROXY": NEW_PROXY, "USE_PROXY": "false"}).settings.client.use_proxy is False


def test_an_invalid_old_value_is_a_config_error_naming_the_old_name() -> None:
    with pytest.raises(loader.ConfigError, match="^USE_PROXY: expected true or false"):
        _load({"USE_PROXY": "maybe"})
    # Boş bırakılmış eski ad okunmaz ve sessizdir
    assert _load({"SOFASCORE_API_TOKEN": " ", "USE_PROXY": ""}).warnings == ()


def test_other_legacy_names_are_still_not_read() -> None:
    """P30 değişmedi: kullanımdan kalkan dördü dışındaki 2.x adları okunmaz."""
    loaded = _load({"DATA_DIR": "/elsewhere", "SOFA_CAPTCHA_TOKEN": "abcdef", "API_BASE_URL": "https://x.example/api"})
    assert loaded.settings == _load({}).settings
    assert all("no longer read since 3.1" in message for message in _messages(loaded)) and len(_messages(loaded)) == 3


# --- belirteç: /api/v1 korunur ----------------------------------------------------------------------


@pytest.fixture
def old_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    monkeypatch.delenv("SOFASCORE_SERVER__TOKEN_ENV", raising=False)
    monkeypatch.setenv("SOFASCORE_API_TOKEN", TOKEN)
    redact.refresh()
    yield TOKEN
    monkeypatch.undo()
    redact.refresh()


@pytest.mark.parametrize("root", ["", "/sofa"])
def test_a_token_from_the_old_name_protects_the_api(old_token: str, root: str) -> None:
    client = TestClient(app, root_path=root)
    refused = client.get(f"{root}/api/v1/jobs")
    assert refused.status_code == 401 and refused.json()["error"]["code"] == "unauthorized"
    wrong = client.get(f"{root}/api/v1/jobs", headers={"Authorization": "Bearer wrong-token-0123456789abcdef"})
    assert wrong.status_code == 401
    allowed = client.get(f"{root}/api/v1/jobs", headers={"Authorization": f"Bearer {old_token}"})
    assert allowed.status_code == 200, allowed.text
    assert security.token_variable() == "SOFASCORE_API_TOKEN"


def test_the_new_token_name_wins_over_the_old_one(old_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(security.TOKEN_ENV, NEW_TOKEN)
    client = TestClient(app)
    assert client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {NEW_TOKEN}"}).status_code == 200


def test_a_token_from_the_old_name_is_masked_locked_and_never_written(old_token: str) -> None:
    client = TestClient(app, headers={"Authorization": f"Bearer {old_token}"})
    path = overrides.overrides_path()
    assert not path.exists()
    try:
        body = client.get("/api/v1/settings")
        assert body.status_code == 200, body.text
        assert old_token not in body.text
        row = {r["key"]: r for r in body.json()["data"]["settings"]}["server.token"]
        assert row == {
            "key": "server.token", "value": "***", "source": "env", "source_name": "SOFASCORE_API_TOKEN",
            "locked": True, "writable": False, "secret": True, "replaced_by": "SOFASCORE_SERVER__TOKEN",
        }
        # Belirteç API'den yazılamaz; başka bir ayarın yazılması da belirteci dosyaya taşımaz
        refused = client.patch("/api/v1/settings", json={"values": {"server.token": "other-token-0123456789"}})
        assert refused.status_code == 400 and not path.exists()
        written = client.patch("/api/v1/settings", json={"values": {"client.retries": 4}})
        assert written.status_code == 200, written.text
        assert old_token not in written.text and old_token not in path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)
        Path(f"{path}.lock").unlink(missing_ok=True)
        loader.reload()
    # Log satırlarında da maskelenir
    assert old_token not in redact.redact_text(f"token={old_token} and {old_token}")


def test_a_proxy_from_the_old_name_is_masked_and_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_URL", PROXY)
    monkeypatch.setenv("USE_PROXY", "true")
    redact.refresh()
    try:
        client = TestClient(app)
        body = client.get("/api/v1/settings")
        assert body.status_code == 200 and PROXY_SECRET not in body.text
        rows = {r["key"]: r for r in body.json()["data"]["settings"]}
        assert rows["client.proxy"]["value"] == "http://scraper:***@proxy.example.com:8080"
        assert (rows["client.proxy"]["source_name"], rows["client.proxy"]["locked"]) == ("PROXY_URL", True)
        assert rows["client.proxy"]["replaced_by"] == "SOFASCORE_CLIENT__PROXY"
        assert (rows["client.use_proxy"]["value"], rows["client.use_proxy"]["source_name"]) == (True, "USE_PROXY")
        refused = client.patch("/api/v1/settings", json={"values": {"client.proxy": NEW_PROXY}})
        assert refused.status_code == 400 and PROXY_SECRET not in refused.text
        assert not overrides.overrides_path().exists()
        assert PROXY_SECRET not in redact.redact_text(f"connecting through {PROXY}")
    finally:
        monkeypatch.undo()
        redact.refresh()


# --- serve --host 0.0.0.0 ----------------------------------------------------------------------------


def test_serve_on_every_interface_starts_with_the_old_allowed_hosts_name(
        cli: skeleton.CliRunner, server: FakeServer, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
        caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("SOFASCORE_ALLOWED_HOSTS", "localhost,my-server.lan")
    with caplog.at_level(logging.WARNING):
        run = serve(cli, "--host", "0.0.0.0", "--port", "9000", "--json")
    assert run.exit_code == 0, run.stderr
    assert server.calls == [{"host": "0.0.0.0", "port": 9000}]
    # Liste ayarlardan gelir (ortama yeni adla yazılmaz) ve uygulama onu görür
    assert server.hosts_env == [None] and server.hosts_seen == [["localhost", "my-server.lan"]]
    assert run.data["allowed_hosts"] == ["localhost", "my-server.lan"] and run.data["allowed_hosts_from"] == "settings"
    assert any("SOFASCORE_ALLOWED_HOSTS (set in the environment) is deprecated" in record.getMessage()
               for record in caplog.records)
    # Belirteç de eski adla verilmişse açık adres uyarısı yoktur
    monkeypatch.setenv("SOFASCORE_API_TOKEN", TOKEN)
    run = serve(cli, "--host", "0.0.0.0", "--json")
    assert run.exit_code == 0 and run.data["token"] is True
    assert "exposed_without_token" not in [warning["code"] for warning in run.json["warnings"]]


def test_serve_on_every_interface_still_refuses_without_any_allowed_hosts(cli: skeleton.CliRunner,
                                                                         server: FakeServer) -> None:  # noqa: F811
    run = serve(cli, "--host", "0.0.0.0", "--json")
    assert run.exit_code == 2 and server.calls == []


# --- config show, config validate, doctor, tanılama -------------------------------------------------


def test_config_show_and_validate_say_the_value_comes_from_the_deprecated_name(cli: skeleton.CliRunner,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    monkeypatch.setenv("SOFASCORE_API_TOKEN", TOKEN)
    monkeypatch.setenv("PROXY_URL", PROXY)
    shown = cli("config", "show", "--json")
    rows = {row["key"]: row for row in shown.data["values"]}
    assert rows["server.token"] == {"key": "server.token", "value": "***", "source": "env", "from": "SOFASCORE_API_TOKEN",
                                    "locked": True, "replaced_by": "SOFASCORE_SERVER__TOKEN"}
    assert TOKEN not in shown.stdout and PROXY_SECRET not in shown.stdout
    codes = [(w["code"], w["message"]) for w in shown.json["warnings"]]
    assert ("legacy_name", "SOFASCORE_API_TOKEN (set in the environment) is deprecated; still read in 3.1, removed "
                           "in 3.2; use SOFASCORE_SERVER__TOKEN.") in codes
    text = cli("config", "show").stdout
    assert any(line.startswith("server.token") and line.endswith(
        '"***"  [env: SOFASCORE_API_TOKEN, deprecated, use SOFASCORE_SERVER__TOKEN]') for line in text.splitlines())
    assert TOKEN not in text and PROXY_SECRET not in text
    validated = cli("config", "validate", "--json")
    assert validated.exit_code == 0
    assert any(w["code"] == "legacy_name" and w["message"].startswith("PROXY_URL (set in the environment) is deprecated")
               for w in validated.json["warnings"])
    assert PROXY_SECRET not in validated.stdout


@pytest.mark.parametrize("name", sorted(CASES))
def test_doctor_says_the_deprecated_name_is_still_read_and_where_it_is(make_ctx, name: str) -> None:  # noqa: F811
    key, (raw, _expected), (new_raw, _new_expected) = CASES[name]
    replacement = loader.legacy_replacement(name)
    for ctx, where, origin in (
        (make_ctx(env_text=f"{name}={raw}\n"), ".env", "env_file"),
        (make_ctx(env_text="", environ={name: raw}), "the environment", "environment"),
    ):
        res = doctor.check_env(ctx)
        assert res.status == doctor.WARN
        assert res.summary == f"{name} (set in {where}) is deprecated; still read in 3.1, removed in 3.2: use {replacement}"
        assert [(p["key"], p["origin"]) for p in res.detail["problems"]] == [(name, origin)]
        assert TOKEN not in json.dumps(res.to_dict()) and PROXY_SECRET not in json.dumps(res.to_dict())
    both = doctor.check_env(make_ctx(env_text=f"{name}={raw}\n{loader.env_name(key)}={new_raw}\n"))
    assert both.summary == (f"{name} (set in .env) is ignored because {loader.env_name(key)} is also set; {name} is "
                            f"deprecated and removed in 3.2: remove it")
    tr = doctor.check_env(make_ctx(env_text="", environ={name: raw}, lang="tr"))
    assert f"{name} (ortamda tanımlı) kullanımdan kalktı; 3.1'de hâlâ okunuyor, 3.2'de kaldırılacak" in tr.summary


def test_diagnostics_mask_the_values_and_name_the_deprecated_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    for name, value in (("SOFASCORE_API_TOKEN", TOKEN), ("PROXY_URL", PROXY), ("USE_PROXY", "true"),
                        ("SOFASCORE_ALLOWED_HOSTS", "box.lan")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SOFASCORE_SERVER__ALLOWED_HOSTS")
    redact.refresh()
    try:
        doc = diagnostics.collect()
        settings = doc["settings"]
        assert settings["values"]["server.token"] == "***"
        assert settings["values"]["client.proxy"] == "http://***@proxy.example.com:8080"
        # (Bir belirteç anahtarının her değeri, kaynağı da, paketin maskesinde `***` olur)
        assert settings["sources"]["client.proxy"] == "env: PROXY_URL, deprecated, use SOFASCORE_CLIENT__PROXY"
        assert settings["sources"]["server.allowed_hosts"] == (
            "env: SOFASCORE_ALLOWED_HOSTS, deprecated, use SOFASCORE_SERVER__ALLOWED_HOSTS")
        assert settings["values"]["server.allowed_hosts"] == ["box.lan"]
        assert set(settings["legacy_names"]) == set(CASES)
        assert any(m.startswith("SOFASCORE_API_TOKEN (set in the environment) is deprecated") for m in settings["warnings"])
        flat = json.dumps(doc, ensure_ascii=False)
        assert TOKEN not in flat and PROXY_SECRET not in flat
    finally:
        monkeypatch.undo()
        redact.refresh()
