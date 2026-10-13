"""
Köprünün kullandığı Scrapling 0.4.15 iç ayrıntıları (S1): Scrapling değişirse bu testler açıkça düşer.

Köprü (sofascore_scraper/client/bridge.py) Scrapling'in belgelenmemiş davranışlarına dayanır: bir çözümün geçip
geçmediğini Scrapling'e soramaz (`_challenge_cleared`), çözüm sayfasını havuzdan kendisi kapatır (`page_pool`),
captcha.html'nin yüklemesini tek gezinmeyle sınırlar (`retries`). Bunlar değişirse köprünün davranışı sessizce
değişir; bu testler hangi varsayımın bozulduğunu söyler. Sürüm requirements.txt'te sabittir (scrapling[fetchers]==0.4.15).
Tarayıcı açılmaz.
"""
from __future__ import annotations

import inspect

import pytest

scrapling = pytest.importorskip("scrapling")


def test_the_pinned_scrapling_version():
    from importlib.metadata import version

    assert version("scrapling") == "0.4.15", (
        "the bridge's solve depends on Scrapling 0.4.15 internals; re-check them (this module) before a new version"
    )


def test_scrapling_cannot_tell_whether_an_embedded_turnstile_passed():
    """
    B7: `embedded` bir Turnstile için `_challenge_cleared` sayfa içeriğinden bağımsız olarak hep True'dur; köprü bu
    yüzden geçişi sayfanın siteye yönlenmesinden ve cookie'den okur. Scrapling bunu düzeltirse köprünün kuralı yine
    doğrudur ama bu test, kuralın gerekçesinin değiştiğini söyler.
    """
    from scrapling.fetchers import AsyncStealthySession

    cleared = AsyncStealthySession._challenge_cleared
    assert cleared("<html>still the widget</html>", "embedded") is True


def test_the_async_stealth_fetch_takes_a_page_action():
    """B7: yönlendirmeyi `page_action` bekler (çözücüden sonra, yanıt dönmeden önce çalışır)."""
    from scrapling.fetchers import AsyncStealthySession

    source = inspect.getsource(AsyncStealthySession.fetch)
    assert "params.page_action" in source
    assert source.index("_cloudflare_solver(page)") < source.index("params.page_action(page)")


def test_the_session_keeps_its_pages_in_a_pool_the_bridge_can_prune():
    """
    A2: köprü çözüm sayfasını `session.page_pool.pages`'ten bulur, `remove_page` ile havuzdan çıkarır ve kapatır;
    meşgul sayfaya (`state == "busy"`) dokunmaz.
    """
    from scrapling.engines._browsers._page import PageInfo, PagePool
    from scrapling.fetchers import AsyncStealthySession

    inits = "".join(
        inspect.getsource(k.__init__)
        for k in AsyncStealthySession.__mro__
        if "__init__" in vars(k) and inspect.isfunction(vars(k)["__init__"])
    )
    assert "self.page_pool = PagePool(" in inits
    pool = PagePool(max_pages=2)
    assert isinstance(pool.pages, list)
    info = PageInfo(object(), "ready", "")
    pool.pages.append(info)
    assert {"page", "state"} <= set(PageInfo.__slots__)
    pool.remove_page(info)
    assert pool.pages == []
    pool.remove_page(info)  # havuzda olmayan sayfa: hata yok


def test_the_session_takes_retries_and_additional_args():
    """
    B9: `retries=1` Scrapling'in bir fetch'i yeniden denemesini kapatır (her deneme bir captcha.html yüklemesiydi);
    A7: `additional_args` (Chromium'un kendi sandbox'ı, `chromium_sandbox`) bağlama/tarayıcıya geçer.
    """
    from scrapling.engines._browsers._validators import StealthConfig
    from scrapling.fetchers import AsyncStealthySession

    fields = getattr(StealthConfig, "__struct_fields__", None) or tuple(
        getattr(StealthConfig, "__annotations__", {})
    )
    assert "retries" in fields and "additional_args" in fields
    source = inspect.getsource(AsyncStealthySession.fetch)
    assert "for attempt in range(self._config.retries)" in source
