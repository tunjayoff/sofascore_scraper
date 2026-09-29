"""
Canlı sağlık testi: gerçek SofaScore'a karşı BrowserBridge (Scrapling). Varsayılan çalıştırmada
atlanır; Scrapling veya Playwright sürümünü yükseltmeden önce çalıştırın:

    python -m pytest -m "live and browser" tests/test_live_bridge.py
"""
from __future__ import annotations

import asyncio
import tempfile

import pytest

import src.challenge_solver as cs

pytestmark = [pytest.mark.live, pytest.mark.browser]

ENDPOINTS = [
    "/unique-tournament/17/seasons",
    "/unique-tournament/17/season/76986/rounds",
    "/unique-tournament/17/season/76986/events/round/1",
    "/unique-tournament/8/season/77559/events/round/2",
    "/event/16363633",
    "/event/16363633/statistics",
    "/event/16363633/lineups",
    "/event/16363633/incidents",
    "/event/16363633/h2h",
    "/event/16363634",
    "/event/16363634/statistics",
    "/sport/football/events/live",
]


def test_bridge_fetches_api_headless(monkeypatch):
    monkeypatch.delenv("SOFASCORE_BROWSER_HEADED", raising=False)
    bridge = cs.BrowserBridge(profile_dir=tempfile.mkdtemp(prefix="live-bridge-"))
    monkeypatch.setattr(cs.BrowserBridge, "_instance", bridge)

    async def run():
        ok = 0
        for path in ENDPOINTS:
            data = await cs.fetch_api_via_browser(path)
            ok += data is not None and not (isinstance(data, dict) and data.get("__404__"))
        await cs._run_on_background_loop(bridge.close(), 30)
        return ok

    ok = asyncio.run(run())
    # İlk istekler challenge çözümüne denk gelebilir; biri kaçabilir
    assert ok >= len(ENDPOINTS) - 1, f"only {ok}/{len(ENDPOINTS)} endpoints answered"
