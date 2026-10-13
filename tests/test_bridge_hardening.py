"""
Tarayıcı köprüsünün (sofascore_scraper/client/bridge.py) c projesinden geri taşınan üretim düzeltmeleri (S1).

c projesi (sofascore_site/bridge.py) köprüyü buradan taşıdı ve üretim benzeri koşularda (1-5) düzeltti; testleri
(tests/test_sofascore_bridge.py) buraya köprünün kendi arayüzüyle taşındı. Tarayıcı yok: Scrapling oturumu, bağlamı
ve sayfa sahtedir; SofaScore'a hiçbir istek gitmez.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from sofascore_scraper.client import bridge as cs

# conftest `browser` işaretsiz testlerde `_launch`'ı gerçek tarayıcı açmasın diye değiştirir; burada tarayıcı
# sahte olduğu için gerçek `_launch` kodu çalışır (toplama anında alınan asıl işlev)
_REAL_LAUNCH = cs.BrowserBridge._launch

CHALLENGE = '{"error": {"code": 403, "reason": "challenge"}}'


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    monkeypatch.setattr(cs, "_cached_token", None)
    monkeypatch.setattr(cs, "_cached_at", 0.0)
    monkeypatch.setattr(cs.BrowserBridge, "_instance", None)


@pytest.fixture
def real_launch(monkeypatch):
    monkeypatch.setattr(cs.BrowserBridge, "_launch", _REAL_LAUNCH)


def _jwt(exp: float) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64({'exp': int(exp)})}.sig"


def _page(*answers: Any, url: str = cs.HOME_URL) -> MagicMock:
    page = MagicMock()
    page.is_closed.return_value = False
    page.url = url
    page.evaluate = AsyncMock(side_effect=list(answers))
    page.wait_for_load_state = AsyncMock()

    def goto(to, **_):
        page.url = to

    page.goto = AsyncMock(side_effect=goto)
    page.close = AsyncMock()
    return page


def _fake_session(monkeypatch, cookies_seq: List[Any], page: Optional[MagicMock] = None):
    """
    Scrapling'in AsyncStealthySession'ının sahtesi: argümanlarını kaydeder; köprü sayfası about:blank'te açılır ve
    fetch'leri 200 döner. `cookies_seq`: bağlamın sırayla verdiği cookie listeleri.
    """
    made: List[dict] = []
    page = page if page is not None else _page(url="about:blank")
    if not isinstance(page.evaluate.side_effect, list) or not page.evaluate.side_effect:
        page.evaluate = AsyncMock(return_value={"status": 200, "ok": True, "data": {}, "text": None})
    context = MagicMock()
    context.cookies = AsyncMock(side_effect=list(cookies_seq))
    context.new_page = AsyncMock(return_value=page)
    context.clear_cookies = AsyncMock()

    class Session:
        def __init__(self, **kw: Any) -> None:
            made.append(kw)
            self.context = None
            self.fetch = AsyncMock()
            self.close = AsyncMock()

        async def start(self) -> None:
            self.context = context

    monkeypatch.setattr(cs, "_session_class", lambda: Session)
    return made, page


def _bridge(tmp_path, **kw: Any) -> cs.BrowserBridge:
    return cs.BrowserBridge(profile_dir=str(tmp_path / "profile"), **kw)


def _run(coro):
    return asyncio.run(coro)


# --- A7: Chromium'un kendi sandbox'ı (c'nin K18'i, 15bb37f) -------------------------------------------


def _profile_cookie() -> List[dict]:
    return [{"name": "sofa_captcha", "value": _jwt(time.time() + 3600)}]


def test_the_launch_arguments_are_unchanged_without_the_sandbox(tmp_path, monkeypatch, real_launch):
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    b = _bridge(tmp_path)
    _run(b.ensure_ready())
    (kw,) = made
    assert "additional_args" not in kw
    assert kw == {
        "headless": True, "solve_cloudflare": True, "user_data_dir": b.profile_dir, "proxy": None,
        "block_webrtc": True, "timeout": 60000, "max_pages": 2,
    }


def test_the_sandbox_is_one_launch_option_more_and_nothing_else(tmp_path, monkeypatch, real_launch):
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    b = _bridge(tmp_path, sandbox=True)
    _run(b.ensure_ready())
    (kw,) = made
    assert kw.pop("additional_args") == {"chromium_sandbox": True}
    assert kw == {
        "headless": True, "solve_cloudflare": True, "user_data_dir": b.profile_dir, "proxy": None,
        "block_webrtc": True, "timeout": 60000, "max_pages": 2,
    }


def test_the_sandbox_setting_is_read_at_launch_and_off_by_default(tmp_path, monkeypatch, real_launch):
    from sofascore_scraper.config import loader

    assert loader.active_settings().client.browser_sandbox is False
    monkeypatch.setenv("SOFASCORE_CLIENT__BROWSER_SANDBOX", "true")
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    _run(_bridge(tmp_path).ensure_ready())
    assert made[0]["additional_args"] == {"chromium_sandbox": True}
    # Açıkça verilen değer ayarın önüne geçer
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    _run(_bridge(tmp_path, sandbox=False).ensure_ready())
    assert "additional_args" not in made[0]
