"""
Sahte SofaScore taşıyıcısının (tests/fakes/sofascore.py) kendi testleri: hazır yanıtlar, istek kaydı,
hata enjeksiyonu ve beklemelerin atlanması. İstek katmanı gerçektir; ağ yok.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Iterator, List

import curl_cffi.requests as cffi_requests
import pytest

import src.utils as utils
from characterization import WORLD, pin_default_settings
from fakes.sofascore import REQUEST_LAYER, FakeSofaScore
from src import breaker as request_breaker
from src.exceptions import APIError, NetworkError, RateLimitError, ResourceNotFoundError

LEAGUE = 17
SEASON_WEEKS = 61627  # haftalık turlar
SEASON_PAGES = 52186  # events/last + events/next sayfaları


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


# --- sahte taşıyıcının kendisi ---------------------------------------------------------------

def test_fake_serves_canned_payloads_and_records_every_url_in_order(fake: FakeSofaScore) -> None:
    async def fetch_async() -> Any:
        async with utils.create_session_async() as session:
            return await utils.make_api_request_async(session, f"/unique-tournament/{LEAGUE}/seasons")

    absolute = utils.make_api_request("https://www.sofascore.com/api/v1/event/9100001")
    relative = utils.make_api_request("/event/9100001/h2h")
    seasons = asyncio.run(fetch_async())

    assert absolute["event"]["id"] == 9100001
    assert relative["teamDuel"]["homeWins"] == 3
    assert [s["id"] for s in seasons["seasons"]] == [SEASON_WEEKS, SEASON_PAGES]
    assert [r.label for r in fake.requests] == [
        "sync /event/9100001 200",
        "sync /event/9100001/h2h 200",
        "async https://www.sofascore.com/ 200",  # oturum ısınması
        "async /unique-tournament/17/seasons 200",
    ]
    assert fake.canonical_log() == [
        "sync /event/9100001 200",
        "sync /event/9100001/h2h 200",
        {"concurrent": ["async /unique-tournament/17/seasons 200", "async https://www.sofascore.com/ 200"]},
    ]


def test_fake_answers_404_for_an_unknown_path(fake: FakeSofaScore) -> None:
    assert utils.make_api_request("/event/1") is None
    with pytest.raises(ResourceNotFoundError):
        utils.make_api_request("/event/1", raise_on_failure=True)
    assert fake.paths() == ["/event/1", "/event/1"]  # 404 yeniden denenmez


@pytest.mark.parametrize(
    ("status", "error", "backoff"),
    [
        (403, APIError, [10.0, 20.0]),
        (429, RateLimitError, [5.0, 10.0]),
        (500, APIError, [3.0, 6.0]),
    ],
)
def test_fake_injects_http_errors_through_the_real_request_layer(
    fake: FakeSofaScore, status: int, error: type, backoff: List[float]
) -> None:
    fake.fail("/event/9100001", status)
    started = time.monotonic()

    with pytest.raises(error) as raised:
        utils.make_api_request("/event/9100001", raise_on_failure=True)

    assert raised.value.status_code == status
    assert [r.outcome for r in fake.requests] == [str(status)] * 3  # MAX_RETRIES varsayılanı
    assert fake.slept(REQUEST_LAYER) == backoff  # geri çekilmeler kaydedilir ama beklenmez
    assert time.monotonic() - started < 2


def test_fake_injects_a_fault_a_limited_number_of_times(fake: FakeSofaScore) -> None:
    fake.fail("/event/*/statistics", 502, times=1)

    data = utils.make_api_request("/event/9100001/statistics")

    assert data is not None and "statistics" in data
    assert [r.outcome for r in fake.requests] == ["502", "200"]


def test_fake_injects_timeouts_and_connection_errors(fake: FakeSofaScore) -> None:
    fake.timeout("/event/9100001")
    fake.disconnect("/event/9100002")

    with pytest.raises(NetworkError) as timed_out:
        utils.make_api_request("/event/9100001", raise_on_failure=True)
    with pytest.raises(NetworkError) as disconnected:
        utils.make_api_request("/event/9100002", raise_on_failure=True)

    assert request_breaker.failure_kind(timed_out.value) == "timeout"
    assert request_breaker.failure_kind(disconnected.value) == "network"
    assert [r.outcome for r in fake.requests] == ["timeout"] * 3 + ["network"] * 3


def test_fake_skips_only_the_application_sleeps(fake: FakeSofaScore) -> None:
    """Uygulama kodu (src.*) dışındaki beklemelere dokunulmaz: test ve kütüphane kodu gerçekten bekler."""
    started = time.monotonic()

    time.sleep(0.02)
    asyncio.run(asyncio.sleep(0.02))

    assert time.monotonic() - started >= 0.03
    assert fake.sleeps == []


def test_fake_world_and_log_round_trip_through_json(tmp_path: Path) -> None:
    """Dünya ve hatalar JSON'dan kurulabilir, kayıt JSON'a yazılabilir: alt süreçte çalışan testler için."""
    source = FakeSofaScore.from_file(WORLD)
    source.fail("/event/9100001", 429, times=1, headers={"Retry-After": "7"})
    clone = FakeSofaScore.from_dict(json.loads(json.dumps(source.to_dict())))

    with clone:
        data = utils.make_api_request("/event/9100001")
    clone.save_log(tmp_path / "log.json")
    saved = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))

    assert data is not None and data["event"]["id"] == 9100001
    assert saved["canonical"] == ["sync /event/9100001 429", "sync /event/9100001 200"]
    assert [r["outcome"] for r in saved["requests"]] == ["429", "200"]
    assert saved["sleeps"][0] == {"source": REQUEST_LAYER, "seconds": 7.0, "kind": "sync"}  # Retry-After


def test_fake_restores_the_transport_on_uninstall() -> None:
    original_get, original_session = cffi_requests.get, utils.AsyncSession
    original_sleeps = (utils._sleep, utils._asleep, time.sleep, asyncio.sleep)

    with FakeSofaScore.from_file(WORLD):
        assert cffi_requests.get is not original_get

    assert (cffi_requests.get, utils.AsyncSession) == (original_get, original_session)
    assert (utils._sleep, utils._asleep, time.sleep, asyncio.sleep) == original_sleeps
