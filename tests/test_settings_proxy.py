"""
Proxy ayarları web API'sinde (`/api/v1/settings`): parola asla tam dönmez, log'a yazılmaz; maskeli adres geri
gönderildiğinde saklanan parola korunur, başka bir uca gönderilecekse yeniden yazılması istenir
(sofascore_scraper/web/api/proxy.py). 2.x'in `/api/settings` yolu aynı kuralı uyguluyordu; 3.1'de kalktı (P30).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sofascore_scraper.config import active_settings
from sofascore_scraper.web.app import app
from sofascore_scraper.web.api.proxy import PROXY_PASSWORD_MASK, mask_proxy_url, restore_proxy_password

client = TestClient(app)

SECRET = "s3cr3t-Pa55"
STORED = f"http://scraper:{SECRET}@proxy.example:8080"
MASKED = f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:8080"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch, settings_overrides):
    """Proxy ortamdan verilmez (ortamın değeri ayarı kilitlerdi); test sonunda overrides.json'dan silinir."""
    for name in ("PROXY_URL", "USE_PROXY"):
        monkeypatch.delenv(name, raising=False)
    yield
    assert _patch({"client.proxy": None, "client.use_proxy": None}).status_code == 200


def _patch(values: Dict[str, Any]) -> Any:
    return client.patch("/api/v1/settings", json={"values": values})


def _rows() -> Dict[str, Any]:
    return {row["key"]: row for row in client.get("/api/v1/settings").json()["data"]["settings"]}


def _stored(url: str) -> None:
    assert _patch({"client.proxy": url, "client.use_proxy": True}).status_code == 200


def _refused(response: Any) -> None:
    assert response.status_code == 422, response.text
    errors = response.json()["error"]["details"]["errors"]
    assert [e["type"] for e in errors] == ["proxy_password_required"]


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


def test_settings_never_return_the_proxy_password():
    _stored(STORED)
    r = client.get("/api/v1/settings")
    assert r.status_code == 200 and SECRET not in r.text
    rows = _rows()
    assert rows["client.use_proxy"]["value"] is True
    assert rows["client.proxy"]["value"] == MASKED


def test_saving_a_proxy_stores_it_and_answers_without_the_password(caplog):
    with caplog.at_level(logging.DEBUG):
        r = _patch({"client.use_proxy": True, "client.proxy": STORED})
    assert r.status_code == 200 and SECRET not in r.text
    assert active_settings().client.proxy == STORED and active_settings().client.use_proxy is True
    assert _rows()["client.proxy"]["value"] == MASKED
    # Parola log'a yazılmaz
    assert all(SECRET not in rec.getMessage() for rec in caplog.records)


def test_sending_the_masked_url_back_keeps_the_stored_password():
    _stored(STORED)
    assert _patch({"client.proxy": MASKED}).status_code == 200
    assert active_settings().client.proxy == STORED


def test_a_masked_url_for_another_proxy_is_refused_and_nothing_changes():
    _stored(STORED)
    r = _patch({"client.proxy": f"http://scraper:{PROXY_PASSWORD_MASK}@other.example:8080"})
    _refused(r)
    assert SECRET not in r.text and active_settings().client.proxy == STORED


def test_masked_url_without_a_stored_password_asks_for_the_password():
    _refused(_patch({"client.proxy": MASKED}))
    assert active_settings().client.proxy == ""


def test_proxy_can_be_cleared():
    _stored(STORED)
    assert _patch({"client.use_proxy": False, "client.proxy": ""}).status_code == 200
    rows = _rows()
    assert rows["client.use_proxy"]["value"] is False and rows["client.proxy"]["value"] == ""


# --- kuralın kendisi (restore_proxy_password) ----------------------------------------------------


def _required(submitted: str, stored: str) -> None:
    with pytest.raises(HTTPException) as caught:
        restore_proxy_password(submitted, stored)
    assert caught.value.status_code == 422 and caught.value.detail["reason"] == "proxy_password_required"
    assert SECRET not in str(caught.value.detail)


def test_masked_password_survives_letter_case_of_scheme_and_host():
    """Şema ve sunucu adı büyük/küçük harfe duyarsızdır: aynı uç, parola geri konur."""
    submitted = f"HTTP://scraper:{PROXY_PASSWORD_MASK}@PROXY.example:8080"
    assert restore_proxy_password(submitted, STORED) == f"HTTP://scraper:{SECRET}@PROXY.example:8080"


@pytest.mark.parametrize(
    "stored",
    [
        f"https://scraper:{SECRET}@proxy.example",  # port yazılmamış
        f"socks5h://scraper:{SECRET}@10.0.0.1:1080/",
        f"http://scraper:{SECRET}@[2001:db8::1]:8080",
    ],
)
def test_masked_password_is_kept_for_the_same_scheme_host_and_port(stored):
    assert restore_proxy_password(mask_proxy_url(stored), stored) == stored


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
def test_stored_password_is_not_sent_to_a_different_proxy(submitted):
    _required(submitted, STORED)


def test_scheme_downgrade_on_the_same_host_does_not_reveal_the_password():
    """
    Ayarları yazabilen biri aynı sunucuda https'i http'ye çevirip parolayı ağda açık taşıtamaz:
    şema değişince parola yeniden yazılmalıdır.
    """
    _required(f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:8443", f"https://scraper:{SECRET}@proxy.example:8443")


def test_explicit_port_is_not_equal_to_a_missing_port():
    """Yazılmamış port hiçbir porta eşit sayılmaz: varsayılan port istemciye göre değişir."""
    _required(f"http://scraper:{PROXY_PASSWORD_MASK}@proxy.example:80", f"http://scraper:{SECRET}@proxy.example")


def test_a_typed_password_is_accepted_when_the_proxy_address_changes():
    """Parola yeniden yazıldıysa şema, sunucu ve port serbestçe değişir."""
    new = f"socks5://scraper:{SECRET}@proxy.example:1080"
    assert restore_proxy_password(new, STORED) == new


def test_a_new_password_replaces_the_stored_one():
    new = STORED.replace(SECRET, "changed")  # yeni parola; düz yazılmış kimlik bilgili URL değil
    assert restore_proxy_password(new, STORED) == new
    _required(MASKED, "")
