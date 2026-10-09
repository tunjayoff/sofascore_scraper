"""
API v1: ayarlar ve overrides.json'ın yazarı (plan maddesi P20; docs/design/02-services.md 4.3 ve 6, karar D4).

  * GET her ayarı değeri, katmanı, kilidi ve yazılabilirliğiyle verir; gizli değerler maskelidir;
  * PATCH `CONFIG_DIR/overrides.json`'a yazar: değer hemen geçerlidir, `null` siler, istek bütündür;
  * yapılandırma dosyasının, ortamın ya da bir bayrağın sabitlediği (kilitli) anahtar reddedilir ve hiçbir şey
    yazılmaz (eski rota böyle bir değeri `.env`'e yazıp başarı bildiriyordu: plan bölüm 15, satır 54);
  * API'den değiştirilemeyen anahtarlar, bilinmeyen anahtarlar ve kuralına uymayan değerler reddedilir;
  * proxy parolası hiçbir yanıtta dönmez; maskeli adres geri gönderilince saklanan parola korunur;
  * veri dizini değişimi iş deposunu taşır; çalışan iş, kullanılamayan dizin ve başka sürecin kilidi ayrı
    kodlarla reddedilir.

Testler conftest'in yapılandırma dizinine yazar ve arkalarında overrides.json bırakmaz.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import conftest
from sofascore_scraper import redact
from sofascore_scraper.config import loader, overrides
from sofascore_scraper.config import settings as model
from sofascore_scraper.exceptions import ConfigError
from sofascore_scraper.store import LeaseHeld, SchemaTooNew, open_store
from sofascore_scraper.web import deps
from sofascore_scraper.web.api.v1 import settings as settings_v1
from sofascore_scraper.web.app import app
from sofascore_scraper.store import default_db_path

client = TestClient(app)
store = deps.job_store()
URL = "/api/v1/settings"
PROXY_SECRET = "s3cr3t-Pa55"
PROXY_URL = f"http://scraper:{PROXY_SECRET}@proxy.example:8080"


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """overrides.json'ın yolu. Test bitince dosya, `.env` ve etkin ayarlar eski haline döner."""
    path = overrides.overrides_path()
    assert path == Path(conftest.CONFIG_DIR) / "overrides.json" and not path.exists()
    env_before = Path(conftest.ENV_FILE).read_bytes()
    environ_before = dict(os.environ)
    yield path
    if store.snapshot().get("is_running"):
        store.update(status="Cancelled", finished=True)
    monkeypatch.undo()
    for leftover in (path, Path(f"{path}.lock")):
        leftover.unlink(missing_ok=True)
    Path(conftest.ENV_FILE).write_bytes(env_before)
    # Ortam da eski haline döner: eski ayar rotası yazdığı değeri ortamda bırakır, köprü de kendi yazdıklarını
    for name in set(os.environ) - set(environ_before):
        del os.environ[name]
    os.environ.update(environ_before)
    loader.reload()
    redact.refresh()
    store.rebind(default_db_path(conftest.DATA_DIR))
    assert os.environ["SOFASCORE_STORAGE__DATA_DIR"] == conftest.DATA_DIR


def rows() -> Dict[str, Dict[str, Any]]:
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return {row["key"]: row for row in response.json()["data"]["settings"]}


def patch(values: Dict[str, Any]) -> Any:
    return client.patch(URL, json={"values": values})


def error(response: Any, status: int) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == ("invalid_request" if status in (400, 422) else body["code"])
    return body


def issues(response: Any) -> Dict[str, str]:
    """422 yanıtındaki hatalar: anahtar → ileti."""
    return {item["loc"][-1]: item["message"] for item in error(response, 422)["details"]["errors"]}


# --- okuma -------------------------------------------------------------------------------------------


def test_get_lists_every_setting_of_the_model_with_its_source(sandbox: Path) -> None:
    body = client.get(URL).json()["data"]

    assert list(body) == ["config_file", "overrides_file", "settings", "metadata", "slices"]
    assert (body["config_file"], body["overrides_file"]) == (None, None)
    listed = body["settings"]
    assert [row["key"] for row in listed] == [key for key, _f in model.iter_settings()]
    assert all(list(row) == ["key", "value", "source", "source_name", "locked", "writable", "secret", "replaced_by"] for row in listed)
    found = {row["key"]: row for row in listed}
    # conftest: client.rate süreç ortamında (SOFASCORE_CLIENT__RATE), client.max_concurrent hiçbir yerde
    assert found["client.rate"] == {
        "key": "client.rate", "value": 0.0, "source": "env", "source_name": "SOFASCORE_CLIENT__RATE", "locked": True,
        "writable": False, "secret": False, "replaced_by": None,
    }
    assert found["client.max_concurrent"] == {
        "key": "client.max_concurrent", "value": 10, "source": "default", "source_name": "",
        "locked": False, "writable": True, "secret": False, "replaced_by": None,
    }
    assert found["client.retries"] == {
        "key": "client.retries", "value": 3, "source": "default", "source_name": "", "locked": False,
        "writable": True, "secret": False, "replaced_by": None,
    }
    assert found["server.allowed_hosts"]["value"] == ["localhost", "127.0.0.1", "testserver"]


def test_writable_is_the_table_minus_the_locked_keys(sandbox: Path) -> None:
    found = rows()
    writable = {key for key, row in found.items() if row["writable"]}
    locked = {key for key, row in found.items() if row["locked"]}
    assert writable == set(settings_v1.WRITABLE) - locked
    assert set(settings_v1.WRITABLE) <= set(found)
    # Sunucunun dinlediği adres, Host listesi, belirteç ve dizin yolları API'den değiştirilemez
    for key in ("server.host", "server.port", "server.allowed_hosts", "server.token_env", "server.token", "log.dir",
                "client.browser_profile", "client.proxy_env", "client.captcha_token", "live.source"):
        assert key not in settings_v1.WRITABLE, key
    # Dosyada yazılamayan (yalnızca ortamdan okunan) hiçbir anahtar tabloda değil
    assert all(f.metadata["in_file"] for key, f in model.iter_settings() if key in settings_v1.WRITABLE)


def test_secrets_are_masked(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOFASCORE_CLIENT__CAPTCHA_TOKEN", "captcha-value-123")
    monkeypatch.setenv("SOFASCORE_CLIENT__PROXY", PROXY_URL)

    found = rows()

    assert found["client.captcha_token"]["value"] == "***" and found["client.captcha_token"]["secret"] is True
    assert found["client.proxy"]["value"] == "http://scraper:***@proxy.example:8080"
    assert found["server.token"]["value"] == ""  # ayarlı değil
    text = client.get(URL).text
    assert "captcha-value-123" not in text and PROXY_SECRET not in text


# --- yazma -------------------------------------------------------------------------------------------


def test_patch_writes_the_overrides_file_and_the_value_is_in_force_at_once(sandbox: Path) -> None:
    response = patch({"client.retries": 7, "display.language": "tr", "client.rate": None})

    assert response.status_code == 200, response.text
    document = response.json()["data"]
    assert document["overrides_file"] == str(sandbox)
    found = {row["key"]: row for row in document["settings"]}
    assert found["client.retries"] == {
        "key": "client.retries", "value": 7, "source": "overrides", "source_name": str(sandbox), "locked": False,
        "writable": True, "secret": False, "replaced_by": None,
    }
    assert found["display.language"]["value"] == "tr"
    assert json.loads(sandbox.read_text(encoding="utf-8")) == {"client": {"retries": 7}, "display": {"language": "tr"}}
    assert sandbox.read_text(encoding="utf-8").endswith("}\n")
    # Uygulamanın geri kalanı da yeni değeri görür (etkin ayarlardan); ortama bir şey yazılmaz (3.1)
    assert deps.config_manager().get_max_retries() == 7
    assert "SOFASCORE_CLIENT__RETRIES" not in os.environ
    assert rows() == found


@pytest.mark.skipif(os.name != "posix", reason="POSIX dosya izinleri")
def test_the_overrides_file_is_private(sandbox: Path) -> None:
    assert patch({"client.retries": 7}).status_code == 200
    assert stat.S_IMODE(os.stat(sandbox).st_mode) == 0o600


def test_a_value_written_here_beats_the_default_and_null_gives_it_back(sandbox: Path) -> None:
    """3.1: `.env`'deki değer ortamdır ve ayarı kilitler; yalnızca varsayılan ve overrides.json yazılabilir katmandır."""
    assert rows()["client.max_concurrent"]["source"] == "default"

    assert patch({"client.max_concurrent": 9}).status_code == 200
    assert rows()["client.max_concurrent"]["value"] == 9 and deps.config_manager().get_max_concurrent() == 9

    assert patch({"client.max_concurrent": None}).status_code == 200
    row = rows()["client.max_concurrent"]
    assert (row["value"], row["source"]) == (10, "default")
    assert json.loads(sandbox.read_text(encoding="utf-8")) == {}
    assert "SOFASCORE_CLIENT__MAX_CONCURRENT" not in os.environ


def test_an_empty_patch_changes_nothing(sandbox: Path) -> None:
    before = rows()
    response = patch({})
    assert response.status_code == 200 and not sandbox.exists()
    assert {row["key"]: row for row in response.json()["data"]["settings"]} == before


def test_values_are_normalised_as_the_config_file_would(sandbox: Path) -> None:
    assert patch({"log.level": "debug", "client.rate": None, "refresh.window_hours": 24,
                  "client.base_url": "https://api.sofascore.com/api/v1/"}).status_code == 200
    assert json.loads(sandbox.read_text(encoding="utf-8")) == {
        "client": {"base_url": "https://api.sofascore.com/api/v1"},
        "log": {"level": "DEBUG"},
        "refresh": {"window_hours": 24.0},
    }
    assert rows()["log.level"]["value"] == "DEBUG"


# --- kilitli, salt okunur, bilinmeyen ------------------------------------------------------------------


def test_a_key_pinned_by_the_environment_is_refused_and_nothing_is_written(sandbox: Path) -> None:
    refused = error(patch({"client.rate": 3, "client.retries": 7}), 400)

    assert refused["details"] == {"locked": [{"key": "client.rate", "source": "env", "source_name": "SOFASCORE_CLIENT__RATE"}]}
    assert "pinned" in refused["message"]
    assert not sandbox.exists() and rows()["client.retries"]["value"] == 3


def test_a_key_pinned_by_the_config_file_is_refused(sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan bölüm 15, satır 54: dosyanın sabitlediği değer için eski rota başarı bildiriyordu ve etkisi yoktu."""
    config = tmp_path / "sofascore.toml"
    config.write_text("[client]\nretries = 9\n", encoding="utf-8")
    monkeypatch.setenv("SOFASCORE_CONFIG", str(config))
    loader.reload()

    row = rows()["client.retries"]
    assert row == {"key": "client.retries", "value": 9, "source": "file", "source_name": str(config), "locked": True,
                   "writable": False, "secret": False, "replaced_by": None}
    assert client.get(URL).json()["data"]["config_file"] == str(config)
    refused = error(patch({"client.retries": 2}), 400)
    assert refused["details"]["locked"] == [{"key": "client.retries", "source": "file", "source_name": str(config)}]
    assert not sandbox.exists() and deps.config_manager().get_max_retries() == 9

    # Dosyanın sabitlemediği anahtarlar yazılabilir
    assert patch({"client.timeout_seconds": 30}).status_code == 200
    assert rows()["client.timeout_seconds"]["value"] == 30


def test_an_override_of_a_locked_key_can_still_be_removed(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert patch({"client.retries": 7}).status_code == 200
    monkeypatch.setenv("SOFASCORE_CLIENT__RETRIES", "4")  # sonradan ortamda sabitlendi
    assert rows()["client.retries"] == {
        "key": "client.retries", "value": 4, "source": "env", "source_name": "SOFASCORE_CLIENT__RETRIES", "locked": True,
        "writable": False, "secret": False, "replaced_by": None,
    }
    error(patch({"client.retries": 8}), 400)

    assert patch({"client.retries": None}).status_code == 200
    assert json.loads(sandbox.read_text(encoding="utf-8")) == {}


def test_keys_the_api_may_not_change_are_refused(sandbox: Path) -> None:
    refused = error(patch({"server.allowed_hosts": ["*"], "live.source": "direct", "client.retries": 7}), 400)
    assert refused["details"] == {"read_only": ["live.source", "server.allowed_hosts"]}
    assert not sandbox.exists()
    # Yalnızca ortamdan okunan gizli değerler de öyle
    assert error(patch({"server.token": "x" * 20}), 400)["details"] == {"read_only": ["server.token"]}


def test_unknown_keys_are_rejected(sandbox: Path) -> None:
    assert issues(patch({"client.nope": 1, "nope": 2, "client.retries": 7})) == {
        "client.nope": "unknown setting", "nope": "unknown setting",
    }
    assert not sandbox.exists()
    error(client.patch(URL, json={"client.retries": 7}), 422)        # zarf: {"values": {...}}
    error(client.patch(URL, json={"values": {}, "extra": 1}), 422)


@pytest.mark.parametrize("key,value,expected", [
    ("client.retries", "3", "expected a whole number"),
    ("client.retries", -1, "at least 0"),
    ("client.retries", 11, "must be at most 10"),
    ("client.retries", True, "expected a whole number"),
    ("client.max_concurrent", 0, "at least 1"),
    ("client.max_concurrent", 51, "must be at most 50"),
    ("client.timeout_seconds", 301, "must be at most 300"),
    ("client.rate", 1001, "must be at most 1000"),
    ("client.wait_time_max", 61, "must be at most 60"),
    ("breaker.rate_limit_ratio", 1.5, "at most 1"),
    ("breaker.rate_limit_consecutive", 1001, "must be at most 1000"),
    ("refresh.window_hours", 721, "must be at most 720"),
    ("fetch.only_finished", "yes", "expected true or false"),
    ("log.level", "LOUD", "expected one of"),
    ("display.language", "de", "expected one of"),
    ("display.date_format", "%Y\nFOO=bar", "control characters"),
    ("display.date_format", "x" * 51, "at most 50 characters"),
    ("client.base_url", "https://attacker.example/api/v1", "must be an https address on"),
    ("client.base_url", "http://www.sofascore.com/api/v1", "must be an https address on"),
    ("client.base_url", "ftp://www.sofascore.com", "expected an http:// or https:// address"),
    ("client.proxy", "ftp://proxy.example:21", "must be http(s)://host:port"),
    ("client.proxy", "proxy.example:8080", "must be http(s)://host:port"),
    ("client.proxy", 8080, "not a valid value"),
    ("storage.data_dir", "", "must not be empty"),
    ("storage.data_dir", None, "cannot be removed"),
])
def test_values_that_break_a_rule_are_rejected(sandbox: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: Any,
                                               expected: str) -> None:
    # Bu testte hiçbir anahtar ortamdan sabitlenmiş olmasın (kilit, değer denetiminden önce gelir). İstek
    # reddedilir: varsayılan veri dizinine (./data) hiçbir şey yazılmaz
    for name in ("SOFASCORE_CLIENT__RATE", "SOFASCORE_STORAGE__DATA_DIR"):
        monkeypatch.delenv(name)
    loader.reload()

    found = issues(patch({key: value, "client.wait_time_min": 0.3}))

    assert list(found) == [key] and expected in found[key], found
    assert not sandbox.exists()  # geçerli olan diğer değer de yazılmadı: istek bütündür


def test_every_broken_value_is_reported_at_once(sandbox: Path) -> None:
    found = issues(patch({"client.retries": "x", "client.max_concurrent": 500, "log.level": "INFO"}))
    assert set(found) == {"client.retries", "client.max_concurrent"}


# --- proxy -------------------------------------------------------------------------------------------


def test_the_proxy_password_is_stored_but_never_returned(sandbox: Path) -> None:
    response = patch({"client.proxy": PROXY_URL})

    assert response.status_code == 200 and PROXY_SECRET not in response.text
    assert json.loads(sandbox.read_text(encoding="utf-8")) == {"client": {"proxy": PROXY_URL}}
    row = rows()["client.proxy"]
    assert (row["value"], row["source"], row["secret"]) == ("http://scraper:***@proxy.example:8080", "overrides", True)
    assert deps.config_manager().get_proxy_url() == PROXY_URL
    # Yeni bir kaynaktan verilen proxy kullanılır (yükleyicinin kuralı): use_proxy açılır
    assert rows()["client.use_proxy"]["value"] is True
    assert PROXY_SECRET in redact.secret_values()  # loglarda da maskelenir


def test_the_masked_proxy_keeps_the_stored_password_only_for_the_same_endpoint(sandbox: Path) -> None:
    assert patch({"client.proxy": PROXY_URL}).status_code == 200

    # Arayüz maskeli adresi olduğu gibi geri gönderir: saklanan parola korunur
    assert patch({"client.proxy": "http://scraper:***@proxy.example:8080", "client.retries": 4}).status_code == 200
    assert deps.config_manager().get_proxy_url() == PROXY_URL

    # Sunucu değişti: saklanan parola yeni adrese gönderilmez
    rejected = patch({"client.proxy": "http://scraper:***@other.example:8080"})
    body = error(rejected, 422)
    assert body["details"]["errors"] == [{
        "loc": ["body", "values", "client.proxy"],
        "message": "Re-enter the proxy password: the proxy address or user changed.",
        "type": "proxy_password_required",
    }]
    assert deps.config_manager().get_proxy_url() == PROXY_URL and PROXY_SECRET not in rejected.text

    assert patch({"client.proxy": None, "client.use_proxy": None}).status_code == 200
    assert rows()["client.proxy"]["value"] == "" and deps.config_manager().get_proxy_url() == ""


# --- veri dizini -------------------------------------------------------------------------------------


@pytest.fixture
def data_dir_sandbox(sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Veri dizinini gerçekten değiştiren testler. Veri dizini overrides.json'dan gelir (ortamdan ya da `.env`'den
    gelseydi kilitli olurdu) ve doğrulayıcı tmp_path altını kabul eder. Geri alma `sandbox` fixture'ındadır.
    """
    sandbox.write_text(json.dumps(DATA_DIR_OVERRIDES), encoding="utf-8")
    monkeypatch.delenv("SOFASCORE_STORAGE__DATA_DIR")
    loader.reload()
    monkeypatch.setattr(settings_v1, "REPO_ROOT", tmp_path)
    if store.snapshot().get("is_running"):
        store.update(status="Cancelled", finished=True)
    assert rows()["storage.data_dir"]["source"] == "overrides"
    return tmp_path


DATA_DIR_OVERRIDES = {"storage": {"data_dir": conftest.DATA_DIR}}


def _overrides_untouched() -> bool:
    """data_dir_sandbox'ın yazdığı overrides.json değişmedi: reddedilen istek hiçbir şey yazmadı."""
    return json.loads(overrides.overrides_path().read_text(encoding="utf-8")) == DATA_DIR_OVERRIDES


def test_a_data_dir_pinned_by_the_environment_cannot_be_changed(sandbox: Path, tmp_path: Path) -> None:
    refused = error(patch({"storage.data_dir": str(tmp_path / "other")}), 400)
    assert refused["details"]["locked"][0] == {"key": "storage.data_dir", "source": "env", "source_name": "SOFASCORE_STORAGE__DATA_DIR"}


def test_a_data_dir_change_moves_the_job_store(data_dir_sandbox: Path) -> None:
    new_dir = str(data_dir_sandbox / "second")

    response = patch({"storage.data_dir": new_dir})

    assert response.status_code == 200, response.text
    row = rows()["storage.data_dir"]
    assert (row["value"], row["source"]) == (new_dir, "overrides")
    assert store.db_path == default_db_path(new_dir) and os.path.isfile(store.db_path)
    assert deps.config_manager().get_data_dir() == new_dir
    assert client.get("/api/v1/jobs").json()["data"] == []
    # Aynı dizini yeniden yazmak bir taşıma değildir
    assert patch({"storage.data_dir": new_dir}).status_code == 200 and store.db_path == default_db_path(new_dir)

    # Yeni iş yeni dizinin veritabanına yazılır; başka bir dizine geçince o dizinin geçmişi görünür
    job_id = store.create_running({"mode": "full"})
    store.update(status="Cancelled", finished=True)
    assert [job["id"] for job in client.get("/api/v1/jobs").json()["data"]] == [job_id]
    third = str(data_dir_sandbox / "third")
    assert patch({"storage.data_dir": third}).status_code == 200 and store.db_path == default_db_path(third)
    assert client.get("/api/v1/jobs").json()["data"] == []
    back = patch({"storage.data_dir": new_dir})
    assert back.status_code == 200 and store.db_path == default_db_path(new_dir)
    assert [job["id"] for job in client.get("/api/v1/jobs").json()["data"]] == [job_id]
    # Eski veri dizini (conftest'inki) artık izin verilen alanın dışında: geri dönüş de aynı kurala tabidir.
    # (Windows'ta geçici dizin ev dizininin altındadır ve kural onu kabul eder.)
    if not Path(conftest.DATA_DIR).resolve().is_relative_to(Path.home().resolve()):
        assert "inside the project" in issues(patch({"storage.data_dir": conftest.DATA_DIR}))["storage.data_dir"]


def _open_stores_of(data_dir: str) -> list:
    from sofascore_scraper.store import api

    key = os.path.normcase(os.path.realpath(data_dir))
    return [found for (path, _readonly), found in api._registry.items() if path == key and not found.closed]


def test_a_data_dir_change_closes_the_store_of_the_old_folder(data_dir_sandbox: Path) -> None:
    """
    FX-13 (#120): değişimden sonra eski dizinin deposu bu süreçte açık kalmaz; Windows'ta eski dizin uygulama
    çalışırken taşınabilir ya da silinebilir.
    """
    import shutil

    first = str(data_dir_sandbox / "first")
    assert patch({"storage.data_dir": first}).status_code == 200
    assert client.get("/api/v1/status").json()["data"]["storage_error"] is None  # depo açılır
    readonly = open_store(first, readonly=True, create=False)
    assert _open_stores_of(first)

    assert patch({"storage.data_dir": str(data_dir_sandbox / "second")}).status_code == 200

    assert _open_stores_of(first) == [] and readonly.closed
    shutil.rmtree(first)  # açık dosya yok (Windows'ta açık catalog.db silinemezdi)
    assert client.get("/api/v1/status").json()["data"]["summary"]["data_dir"] == str(data_dir_sandbox / "second")


def test_the_old_store_stays_open_while_this_process_holds_one_of_its_leases(data_dir_sandbox: Path) -> None:
    """`serve`in sink dağıtıcısı eski dizinin `sinks` kilidini tutuyorsa depo onun altından kapatılmaz."""
    first = str(data_dir_sandbox / "first")
    assert patch({"storage.data_dir": first}).status_code == 200
    old = open_store(first)
    with old.lease("sinks", purpose="dispatcher"):
        assert patch({"storage.data_dir": str(data_dir_sandbox / "second")}).status_code == 200
        assert not old.closed and _open_stores_of(first) == [old]
    assert settings_v1.close_data_dir(first) is True and old.closed
    assert settings_v1.close_data_dir(str(data_dir_sandbox / "not-a-store")) is False


def test_a_relative_data_dir_is_stored_as_an_absolute_path(data_dir_sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(data_dir_sandbox)
    assert patch({"storage.data_dir": "relative/data"}).status_code == 200
    assert rows()["storage.data_dir"]["value"] == str(data_dir_sandbox / "relative" / "data")


def test_a_data_dir_outside_the_project_and_the_home_folder_is_rejected(data_dir_sandbox: Path) -> None:
    for bad in ("/etc", str(Path.home()), str(data_dir_sandbox)):
        found = issues(patch({"storage.data_dir": bad}))
        assert "inside the project or your home directory" in found["storage.data_dir"], bad
    assert _overrides_untouched()


def test_a_data_dir_change_is_refused_while_a_job_runs(data_dir_sandbox: Path) -> None:
    store.create_running({"mode": "full"})
    try:
        response = patch({"storage.data_dir": str(data_dir_sandbox / "other"), "client.retries": 7})
        assert response.status_code == 409 and response.json()["error"]["code"] == "job_running"
        assert _overrides_untouched() and rows()["client.retries"]["value"] == 3
        assert store.db_path == default_db_path(conftest.DATA_DIR)
        # Diğer ayarlar iş çalışırken de kaydedilir
        assert patch({"client.retries": 7}).status_code == 200
    finally:
        store.update(status="Cancelled", finished=True)


def test_an_unusable_data_dir_is_a_storage_error_and_nothing_changes(data_dir_sandbox: Path) -> None:
    blocker = data_dir_sandbox / "a_file"
    blocker.write_text("not a folder", encoding="utf-8")
    target = str(blocker / "data")

    response = patch({"storage.data_dir": target, "client.retries": 9})

    assert response.status_code == 507, response.text
    body = response.json()["error"]
    assert body["code"] == "storage_error" and body["message"].startswith("storage error: ")
    assert body["details"]["path"] == target and body["details"]["reason"]
    assert _overrides_untouched() and rows()["client.retries"]["value"] == 3
    assert store.db_path == default_db_path(conftest.DATA_DIR)
    # Yuva bırakıldı: sonraki istek çalışır
    assert patch({"client.retries": 9}).status_code == 200


def test_the_cause_of_an_unusable_data_dir_is_in_the_code_and_the_details(
    data_dir_sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eski rota her neden için aynı 400 `data_dir_unusable` iletisini verir; v1 nedeni ayırır."""
    target = str(data_dir_sandbox / "other")
    real = store.rebind

    def refuse_with(exc: BaseException) -> None:
        def rebind(db_path: str) -> bool:
            if db_path == default_db_path(target):
                raise exc
            return real(db_path)

        monkeypatch.setattr(store, "rebind", rebind)

    refuse_with(SchemaTooNew("state.db bu koddan yeni", path=default_db_path(target)))
    newer = patch({"storage.data_dir": target})
    assert newer.status_code == 507
    assert newer.json()["error"]["details"]["class"] == "SchemaTooNew"
    assert newer.json()["error"]["details"]["store_message"] == "state.db bu koddan yeni"

    refuse_with(LeaseHeld(name="writer", pid=99, host="other", purpose="headless", started_at=0.0))
    held = patch({"storage.data_dir": target})
    assert held.status_code == 409 and held.json()["error"]["code"] == "job_running"
    assert held.json()["error"]["details"]["holder"]["pid"] == 99

    assert _overrides_untouched() and store.db_path == default_db_path(conftest.DATA_DIR)


def test_the_job_store_returns_when_the_setting_cannot_be_written(
    data_dir_sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full_disk(changes: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(overrides, "write_overrides", full_disk)

    response = patch({"storage.data_dir": str(data_dir_sandbox / "other")})

    assert response.status_code == 507 and response.json()["error"]["details"]["reason"] == "No space left on device"
    assert store.db_path == default_db_path(conftest.DATA_DIR)
    assert rows()["storage.data_dir"]["value"] == conftest.DATA_DIR


# --- overrides.json'ın yazarı ------------------------------------------------------------------------


def test_merged_applies_changes_without_touching_the_document() -> None:
    document = {"client": {"retries": 4, "rate": 2.0}, "log": {"level": "INFO"}}

    result = overrides.merged(document, {"client.retries": None, "client.max_concurrent": 8, "log.level": None,
                                         "defaults.slices": ("core", "odds")})

    assert result == {"client": {"rate": 2.0, "max_concurrent": 8}, "defaults": {"slices": ["core", "odds"]}}
    assert document == {"client": {"retries": 4, "rate": 2.0}, "log": {"level": "INFO"}}


def test_the_writer_rejects_what_the_loader_would_reject() -> None:
    for key, value, expected in [
        ("client.nope", 1, "unknown setting"),
        ("nope", 1, "unknown setting"),
        ("server.token", "x", "read from the environment only (SOFASCORE_SERVER__TOKEN)"),
        ("client.retries", "x", "expected a whole number"),
        ("live.source", "push", "expected one of page, direct, poll"),
    ]:
        with pytest.raises(ConfigError, match=expected.replace("(", r"\(").replace(")", r"\)")):
            overrides.merged({}, {key: value})
    assert overrides.checked_value("client.rate", "off") == 0.0
    assert overrides.split_key("client.rate") == ("client", "rate")


def test_a_reload_that_fails_puts_the_file_back(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert patch({"client.retries": 7}).status_code == 200
    before = sandbox.read_bytes()
    real, calls = loader.reload, []

    def failing_once() -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise ConfigError("sofascore.toml: [client] proxy: give proxy or proxy_env, not both")
        return real()

    monkeypatch.setattr(loader, "reload", failing_once)

    response = patch({"client.retries": 8})

    body = error(response, 422)
    assert "give proxy or proxy_env" in body["details"]["errors"][0]["message"]
    assert sandbox.read_bytes() == before and len(calls) == 2
    assert rows()["client.retries"]["value"] == 7


def test_a_failed_first_write_leaves_no_file(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing() -> Any:
        raise ConfigError("broken")

    monkeypatch.setattr(loader, "reload", failing)
    with pytest.raises(ConfigError):
        overrides.write_overrides({"client.retries": 8})
    assert not sandbox.exists()


def test_a_broken_overrides_file_is_reported_not_overwritten(sandbox: Path) -> None:
    sandbox.write_text("{not json", encoding="utf-8")

    response = patch({"client.retries": 8})

    assert "not valid JSON" in error(response, 422)["details"]["errors"][0]["message"]
    assert sandbox.read_text(encoding="utf-8") == "{not json"
    sandbox.unlink()
