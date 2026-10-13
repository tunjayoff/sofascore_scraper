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
