"""
Proxy ayarları web API'sinde: parola asla tam dönmez, log'a yazılmaz; maskeli adres geri
gönderildiğinde saklanan parola korunur.
"""
from __future__ import annotations

import logging
import os

import pytest
from fastapi.testclient import TestClient

from src.paths import env_file_path
from src.web.app import app
from src.web.routes.settings import PROXY_PASSWORD_MASK, mask_proxy_url

client = TestClient(app)

SECRET = "s3cr3t-Pa55"
STORED = f"http://scraper:{SECRET}@proxy.example:8080"
MASKED = f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:8080"


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    """Test .env dosyasını ve proxy ortam değişkenlerini eski haline döndürür."""
    path = env_file_path()
    with open(path, encoding="utf-8") as f:
        before = f.read()
    # monkeypatch'e kaydet: update_env_variable'ın os.environ'a yazdığı değer test sonunda geri alınsın
    monkeypatch.setenv("PROXY_URL", "")
    monkeypatch.setenv("USE_PROXY", "false")
    yield
    with open(path, "w", encoding="utf-8") as f:
        f.write(before)


def _env_file() -> str:
    with open(env_file_path(), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize(
    "url, masked",
    [
        ("", ""),
        ("http://proxy.example:8080", "http://proxy.example:8080"),
        ("http://user@proxy.example:8080", "http://user@proxy.example:8080"),
        ("http://user:pw@proxy.example:8080", "http://user:***@proxy.example:8080"),
        ("socks5h://user:pw@10.0.0.1:1080/", "socks5h://user:***@10.0.0.1:1080/"),
        # Parolada ':' ve '@' olabilir; son '@' sunucuyu ayırır
        ("http://user:p:w@x@proxy.example:8080", "http://user:***@proxy.example:8080"),
        ("https://user:pw@proxy.example/path?x=1@2", "https://user:***@proxy.example/path?x=1@2"),
        # .env'e elle, şemasız yazılmış değer de sızmamalı
        ("user:pw@proxy.example:8080", "user:***@proxy.example:8080"),
    ],
)
def test_mask_proxy_url(url, masked):
    assert mask_proxy_url(url) == masked


def test_settings_never_return_the_proxy_password(monkeypatch):
    monkeypatch.setenv("USE_PROXY", "true")
    monkeypatch.setenv("PROXY_URL", STORED)
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["use_proxy"] is True
    assert r.json()["proxy_url"] == MASKED
    assert SECRET not in r.text


def test_saving_a_proxy_stores_it_and_answers_without_the_password(caplog):
    with caplog.at_level(logging.DEBUG):
        r = client.post("/api/settings", json={"use_proxy": True, "proxy_url": STORED})
    assert r.status_code == 200 and r.json()["status"] == "success"
    assert SECRET not in r.text
    assert os.environ["PROXY_URL"] == STORED and os.environ["USE_PROXY"] == "true"
    assert SECRET in _env_file()  # tam değer yalnızca .env'de
    assert client.get("/api/settings").json()["proxy_url"] == MASKED
    # Parola log'a yazılmaz
    assert caplog.records, "the settings update is logged"
    assert all(SECRET not in rec.getMessage() for rec in caplog.records)


def test_sending_the_masked_url_back_keeps_the_stored_password(monkeypatch):
    monkeypatch.setenv("PROXY_URL", STORED)
    r = client.post("/api/settings", json={"proxy_url": MASKED})
    assert r.status_code == 200
    assert os.environ["PROXY_URL"] == STORED
    assert PROXY_PASSWORD_MASK not in os.environ["PROXY_URL"]


def test_masked_password_survives_letter_case_of_scheme_and_host(monkeypatch):
    """Şema ve sunucu adı büyük/küçük harfe duyarsızdır: aynı uç, parola geri konur."""
    monkeypatch.setenv("PROXY_URL", STORED)
    r = client.post("/api/settings", json={"proxy_url": f"HTTP://scraper:{PROXY_PASSWORD_MASK}@PROXY.example:8080"})
    assert r.status_code == 200
    assert os.environ["PROXY_URL"] == f"HTTP://scraper:{SECRET}@PROXY.example:8080"


@pytest.mark.parametrize(
    "stored",
    [
        f"https://scraper:{SECRET}@proxy.example",  # port yazılmamış
        f"socks5h://scraper:{SECRET}@10.0.0.1:1080/",
        f"http://scraper:{SECRET}@[2001:db8::1]:8080",
    ],
)
def test_masked_password_is_kept_for_the_same_scheme_host_and_port(monkeypatch, stored):
    monkeypatch.setenv("PROXY_URL", stored)
    r = client.post("/api/settings", json={"proxy_url": mask_proxy_url(stored)})
    assert r.status_code == 200
    assert os.environ["PROXY_URL"] == stored


@pytest.mark.parametrize(
    "submitted",
    [
        f"http://scraper:{PROXY_PASSWORD_MASK}@other.example:8080",  # başka sunucu
        f"http://someone:{PROXY_PASSWORD_MASK}@proxy.example:8080",  # başka kullanıcı
        f"socks5://scraper:{PROXY_PASSWORD_MASK}@proxy.example:8080",  # başka şema
        f"https://scraper:{PROXY_PASSWORD_MASK}@proxy.example:8080",  # başka şema (yükseltme de sayılır)
        f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:1080",  # başka port
        f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example",  # port silinmiş
        f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:80a",  # okunamayan port
        f"socks5://scraper:{PROXY_PASSWORD_MASK}@PROXY.example:1080",  # FX-2'den önce parolayı koruyan istek (#23)
    ],
)
def test_stored_password_is_not_sent_to_a_different_proxy(monkeypatch, submitted):
    monkeypatch.setenv("PROXY_URL", STORED)
    before = _env_file()
    r = client.post("/api/settings", json={"proxy_url": submitted})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "proxy_password_required"
    assert os.environ["PROXY_URL"] == STORED and _env_file() == before
    assert SECRET not in r.text


def test_scheme_downgrade_on_the_same_host_does_not_reveal_the_password(monkeypatch):
    """
    Ayarları yazabilen biri aynı sunucuda https'i http'ye çevirip parolayı ağda açık taşıtamaz:
    şema değişince parola yeniden yazılmalıdır.
    """
    stored = f"https://scraper:{SECRET}@proxy.example:8443"
    monkeypatch.setenv("PROXY_URL", stored)
    before = _env_file()
    r = client.post("/api/settings", json={"proxy_url": f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:8443"})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "proxy_password_required"
    assert os.environ["PROXY_URL"] == stored and _env_file() == before
    assert SECRET not in r.text


def test_explicit_port_is_not_equal_to_a_missing_port(monkeypatch):
    """Yazılmamış port hiçbir porta eşit sayılmaz: varsayılan port istemciye göre değişir."""
    stored = f"http://scraper:{SECRET}@proxy.example"
    monkeypatch.setenv("PROXY_URL", stored)
    r = client.post("/api/settings", json={"proxy_url": f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:80"})
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "proxy_password_required"
    assert os.environ["PROXY_URL"] == stored


def test_a_typed_password_is_accepted_when_the_proxy_address_changes(monkeypatch):
    """Parola yeniden yazıldıysa şema, sunucu ve port serbestçe değişir."""
    monkeypatch.setenv("PROXY_URL", STORED)
    new = f"socks5://scraper:{SECRET}@proxy.example:1080"
    r = client.post("/api/settings", json={"proxy_url": new})
    assert r.status_code == 200 and SECRET not in r.text
    assert os.environ["PROXY_URL"] == new


def test_masked_url_without_a_stored_password_asks_for_the_password():
    r = client.post("/api/settings", json={"proxy_url": MASKED})
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "proxy_password_required"
    assert os.environ["PROXY_URL"] == ""


def test_a_new_password_replaces_the_stored_one(monkeypatch):
    monkeypatch.setenv("PROXY_URL", STORED)
    new = STORED.replace(SECRET, "changed")  # yeni parola; düz yazılmış kimlik bilgili URL değil
    assert client.post("/api/settings", json={"proxy_url": new}).status_code == 200
    assert os.environ["PROXY_URL"] == new


def test_proxy_can_be_cleared(monkeypatch):
    monkeypatch.setenv("PROXY_URL", STORED)
    monkeypatch.setenv("USE_PROXY", "true")
    r = client.post("/api/settings", json={"use_proxy": False, "proxy_url": ""})
    assert r.status_code == 200
    assert os.environ["PROXY_URL"] == "" and os.environ["USE_PROXY"] == "false"
    body = client.get("/api/settings").json()
    assert body["use_proxy"] is False and body["proxy_url"] == ""
