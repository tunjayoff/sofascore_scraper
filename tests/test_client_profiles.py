"""
Tarayıcı profilleri (sofascore_scraper/client/profiles.py): challenge alan profil kenara alınır, istek başka bir
profille yinelenir ve tarayıcı köprüsü ancak geçen profil kalmadığında açılır.
"""
from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sofascore_scraper.client import profiles, transport

CFG = {"request_timeout": 5, "max_retries": 3, "wait_time_min": 0.0, "wait_time_max": 0.0}
CHALLENGE = '{"error": {"code": 403, "reason": "challenge" }}'


class Resp:
    def __init__(self, code: int, body: Any = None, text: str = "") -> None:
        self.status_code, self.reason, self.headers, self._body, self.text = code, "x", {}, body, text

    def json(self) -> Any:
        return self._body


@pytest.fixture
def quiet():
    with patch.object(transport, "_get_runtime_request_config", return_value=CFG), \
            patch.object(transport, "_get_proxy_config", return_value=(False, "")), \
            patch.object(transport, "_asleep", new=AsyncMock()), patch.object(transport, "_sleep"):
        yield


def test_the_list_holds_profiles_the_installed_curl_cffi_knows() -> None:
    from curl_cffi.requests import BrowserType

    known = {b.value for b in BrowserType}
    assert profiles.PROFILES and set(profiles.PROFILES) <= known
    assert transport.IMPERSONATE_PROFILES is profiles.PROFILES
    # Ölçümde kapının reddettiği profiller listede yok
    assert not {"chrome124", "chrome131", "chrome133a", "chrome136", "chrome142", "chrome145", "chrome146",
                "chrome151"} & set(profiles.PROFILES)


def test_a_refused_profile_is_not_picked_until_its_time_is_over(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(profiles.time, "monotonic", lambda: now[0])
    first = profiles.PROFILES[0]
    profiles.refuse(first)
    assert profiles.refused() == [first]
    assert all(profiles.pick() != first for _ in range(200))
    assert profiles.another([p for p in profiles.PROFILES if p != first]) is None
    now[0] += profiles.REFUSED_SECONDS + 1
    assert profiles.refused() == [] and first in profiles.usable()


def test_when_every_profile_is_refused_the_whole_list_is_used_again() -> None:
    for p in profiles.PROFILES:
        profiles.refuse(p)
    assert profiles.usable() == [] and profiles.another([]) is None
    assert profiles.pick() in profiles.PROFILES
    profiles.refuse("not-a-profile")  # bilinmeyen ad yok sayılır
    profiles.refuse(None)


def test_sync_request_swaps_the_challenged_profile_and_never_opens_the_browser(quiet: None) -> None:
    used: List[str] = []

    def get(url: str, **kwargs: Any) -> Resp:
        used.append(kwargs["impersonate"])
        return Resp(403, text=CHALLENGE) if len(used) == 1 else Resp(200, body={"ok": 1})

    browser = MagicMock()
    with patch.object(transport.cffi_requests, "get", side_effect=get), \
            patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", browser):
        assert transport.make_api_request("/x") == {"ok": 1}
    assert len(used) == 2 and used[0] != used[1]
    assert profiles.refused() == [used[0]]
    assert not browser.called and not transport._browser_first()


def test_sync_request_falls_back_to_the_browser_when_every_try_is_challenged(quiet: None) -> None:
    used: List[str] = []

    def get(url: str, **kwargs: Any) -> Resp:
        used.append(kwargs["impersonate"])
        return Resp(403, text=CHALLENGE)

    with patch.object(transport.cffi_requests, "get", side_effect=get), \
            patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", return_value={"via": "browser"}):
        assert transport.make_api_request("/x") == {"via": "browser"}
    # İlk profil ve en çok MAX_SWAPS başka profil denendi, hepsi ayrı; sonra köprü
    assert len(used) == 1 + profiles.MAX_SWAPS and len(set(used)) == len(used)
    assert set(profiles.refused()) == set(used) and transport._browser_first()


def test_a_403_that_is_not_a_challenge_does_not_set_a_profile_aside(quiet: None) -> None:
    with patch.object(transport.cffi_requests, "get", return_value=Resp(403, text="no")) as get:
        assert transport.make_api_request("/x") is None
    assert get.call_count == 3 and profiles.refused() == []


def test_async_session_swaps_its_profile_and_keeps_the_new_one(quiet: None) -> None:
    session = MagicMock()
    session.ssc_profile = profiles.PROFILES[0]
    session.ssc_profile_override = None
    seen: List[Any] = []

    async def get(url: str, **kwargs: Any) -> Resp:
        seen.append(kwargs.get("impersonate"))
        return Resp(403, text=CHALLENGE) if len(seen) == 1 else Resp(200, body={"n": len(seen)})

    session.get = get
    browser = AsyncMock()
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", browser):
        assert asyncio.run(transport.make_api_request_async(session, "/a")) == {"n": 2}
        assert asyncio.run(transport.make_api_request_async(session, "/b")) == {"n": 3}
    # İlk istek oturumun profiliyle gitti (istekle profil verilmez), yinelenen ve sonraki istek yeni profille
    assert seen[0] is None and seen[1] == seen[2] and seen[1] not in (None, profiles.PROFILES[0])
    assert profiles.refused() == [profiles.PROFILES[0]] and session.ssc_profile_override == seen[1]
    assert not browser.await_count and not transport._browser_first()


def test_async_session_without_a_known_profile_behaves_as_before(quiet: None) -> None:
    session = MagicMock()  # profili bilinmeyen oturum (testlerin sahte oturumları): yineleme yok, köprü
    session.get = AsyncMock(side_effect=[Resp(403, text=CHALLENGE)])
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", AsyncMock(return_value={"ok": 1})):
        assert asyncio.run(transport.make_api_request_async(session, "/a")) == {"ok": 1}
    assert session.get.call_count == 1 and profiles.refused() == []
