"""
Bağlantı durumu (plan maddesi FX-19; sofascore_scraper/bridge_health.py `ConnectionState`): `/api/v1/status`, `/health` ve
`/status/check` "hiç denenmedi", "yanıt aldı" ve "yanıt alamadı"yı ayırır; son başarı ve son hatanın zamanı ve nedeni
istek katmanından gelir (sofascore_scraper/breaker.py `report_ok` / `report_exception`). İlk kullanıcı incelemesinde arayüz hiçbir
istek başarmadan "Bağlı" diyordu.

Ağ yok: istekler tests/fakes/sofascore.py'nin sahte taşıyıcısına gider. Saat dondurulur (FX-30): zamanlar saniye
çözünürlüğünde gösterilir; gerçek saatle iki ayrı anın karşılaştırılması bir saniye sınırına denk gelince kırılıyordu.
"""
from __future__ import annotations

from typing import Any, Iterator, List

import pytest
from fastapi.testclient import TestClient

from characterization import WORLD, pin_default_settings
from fakes.sofascore import FakeSofaScore
from sofascore_scraper import breaker, bridge_health
from sofascore_scraper.client import Client
from sofascore_scraper.config import loader
from sofascore_scraper.exceptions import CircuitOpenError
from sofascore_scraper.web.app import app

client = TestClient(app)

# Dondurulmuş saatin başlangıcı (köprü ve bağlantı durumu bu saati okur)
START = 1_791_500_000.0  # 2026-10-08T22:53:20+00:00


@pytest.fixture(autouse=True)
def now(monkeypatch: pytest.MonkeyPatch) -> Iterator[List[float]]:
    """Durumun saati: testler `now[0]`ı ilerletir; her kayıt o anı okur."""
    pin_default_settings(monkeypatch)
    loader.reset()
    moment = [START]
    bridge_health.reset(clock=lambda: moment[0])
    yield moment
    bridge_health.reset()
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


def connection() -> Any:
    response = client.get("/api/v1/status")
    assert response.status_code == 200, response.text
    return response.json()["data"]["connection"]


def test_the_state_follows_the_last_outcome() -> None:
    now = [1000.0]
    state = bridge_health.ConnectionState(clock=lambda: now[0])
    assert state.snapshot() == {"state": "never_tried", "last_success_at": None, "last_failure_at": None,
                                "last_failure_reason": None, "last_failure_status": None, "last_check": None}
    state.record_unanswered("timeout")
    assert state.snapshot()["state"] == "failed"
    now[0] = 1001.0
    state.record_answer()
    snap = state.snapshot()
    assert (snap["state"], snap["last_success_at"], snap["last_failure_reason"]) == (
        "ok", "1970-01-01T00:16:41+00:00", "timeout")
    now[0] = 1002.0
    state.record_unanswered("403", 403)
    assert (state.snapshot()["state"], state.snapshot()["last_failure_status"]) == ("failed", 403)


def test_nothing_tried_is_not_connected() -> None:
    assert connection()["state"] == "never_tried"
    health = client.get("/api/v1/health").json()["data"]
    assert health["connection"]["state"] == "never_tried" and health["bridge"]["state"] == "ok"


def test_an_answer_and_a_refusal_from_the_request_layer(fake: FakeSofaScore, now: List[float]) -> None:
    assert Client().get_sync("/event/9100001").status == "ok"
    answered = connection()
    assert (answered["state"], answered["last_success_at"], answered["last_failure_at"]) == (
        "ok", "2026-10-08T22:53:20+00:00", None)
    now[0] = START + 1
    assert Client().get_sync("/event/1").status == "empty"  # 404: SofaScore yanıt verdi; son yanıt bu an
    assert (connection()["state"], connection()["last_success_at"]) == ("ok", "2026-10-08T22:53:21+00:00")
    now[0] = START + 2
    fake.fail("/event/9100002", 403)
    assert Client().get_sync("/event/9100002", retries=1).status == "failed"
    refused = connection()
    assert (refused["state"], refused["last_failure_reason"], refused["last_failure_status"]) == ("failed", "403",
                                                                                                  403)
    # Ret son yanıtın zamanına dokunmaz
    assert (refused["last_success_at"], refused["last_failure_at"]) == ("2026-10-08T22:53:21+00:00",
                                                                        "2026-10-08T22:53:22+00:00")


def test_the_bridge_times_cover_every_transport(fake: FakeSofaScore, now: List[float]) -> None:
    """Curl yolunun yanıtı köprüye uğramaz; `/status.bridge`'in zamanları yine de her taşıyıcının sonucudur."""
    before = client.get("/api/v1/status").json()["data"]["bridge"]
    assert (before["state"], before["last_success_at"], before["last_failure_at"]) == ("ok", None, None)
    assert Client().get_sync("/event/9100001").status == "ok"
    bridge = client.get("/api/v1/status").json()["data"]["bridge"]
    assert bridge["state"] == "ok"
    assert bridge["last_success_at"] == connection()["last_success_at"] == "2026-10-08T22:53:20+00:00"
    assert bridge_health.snapshot()["last_success_at"] is None  # köprünün kendi kaydı (devre kesici, upstream)
    now[0] = START + 3
    fake.disconnect("/event/9100002")
    Client().get_sync("/event/9100002", retries=1)
    bridge = client.get("/api/v1/health").json()["data"]["bridge"]
    assert bridge["last_failure_at"] == connection()["last_failure_at"] == "2026-10-08T22:53:23+00:00"
    assert bridge["consecutive_failures"] == 0


def test_a_request_the_breaker_held_back_says_nothing() -> None:
    breaker.report_exception(CircuitOpenError("/event/1"))
    assert connection()["state"] == "never_tried"


def test_the_connection_check_reports_it(fake: FakeSofaScore, now: List[float]) -> None:
    fake.add("/sport/football/events/live", {"events": [{"id": 1}]})
    assert connection()["last_check"] is None
    checked = client.post("/api/v1/status/check", json={"target": "sofascore"}).json()["data"]
    assert checked["ok"] is True and checked["connection"]["state"] == "ok"
    last = connection()["last_check"]
    assert (last["ok"], last["reason"], last["at"]) == (True, None, "2026-10-08T22:53:20+00:00")
    # Yanıttaki an, `/status`taki son denetimin anıdır: tek saat okuması (FX-30)
    assert checked["checked_at_utc"] == "2026-10-08T22:53:20Z"
    now[0] = START + 1
    fake.disconnect("/sport/football/events/live")
    failed = client.post("/api/v1/status/check", json={"target": "sofascore"}).json()["data"]
    assert failed["ok"] is False
    assert (failed["connection"]["state"], failed["connection"]["last_failure_reason"]) == ("failed", "network")
    # Son denetim `/status`ta: arayüz onu sekme başına hatırlamak zorunda değil
    assert connection()["last_check"] == {"at": "2026-10-08T22:53:21+00:00", "ok": False, "reason": "network"}
    assert failed["checked_at_utc"] == "2026-10-08T22:53:21Z"
