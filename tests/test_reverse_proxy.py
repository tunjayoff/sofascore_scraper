"""
Ters vekil arkasında (B2; canlı doğrulamanın denetleyemediği madde, docs/design/03-implementation-plan.md bölüm 17):

  * kök yol (`root_path`, uvicorn `--root-path`): ASGI'ye göre `path` kökü de içerir; güvenlik katmanı ve hata
    işleyicileri yönlendiricinin eşleştirdiği yolu okur (`route_path`). Önceden "/<kök>/api/v1/..." erişim
    belirteci istenmeden yanıtlanıyordu;
  * vekilin başlıklarıyla (X-Forwarded-For, -Proto, -Host) gelen istekte iş durdurma ve ilerleme akışı çalışır;
    akış vekilin tamponlamasını kapatan başlıkları taşır (`X-Accel-Buffering: no`, `Cache-Control: no-store`).

Ağ yok: istekler uygulamaya doğrudan (TestClient, ASGI çağrısı) gider.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.jobs.manager import JobManager, JobOutcome, local_origin
from sofascore_scraper.jobs.model import JobKind, JobState
from sofascore_scraper.web import security
from sofascore_scraper.web.api import route_path
from sofascore_scraper.web.app import app
from test_api_v1_jobs import events_of, manager, store, wait_for  # noqa: F401  (fikstürler)

TOKEN = "proxy-token-0123456789abcdef0123456789"
PROXY_HEADERS = {
    "X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "scraper.example.org",
    "X-Real-IP": "203.0.113.7",
}


@pytest.mark.parametrize("path, root, expected", [
    ("/api/v1/jobs", "", "/api/v1/jobs"),
    ("/sofa/api/v1/jobs", "/sofa", "/api/v1/jobs"),
    ("/api/v1/jobs", "/sofa", "/api/v1/jobs"),  # kökü içermeyen eski biçim
    ("/sofa", "/sofa", ""),
    ("/sofascore/api/v1/jobs", "/sofa", "/sofascore/api/v1/jobs"),  # kök tam bir yol parçası değil
])
def test_the_route_path_is_the_routers(path: str, root: str, expected: str) -> None:
    scope = {"type": "http", "path": path, "root_path": root}
    assert route_path(scope) == expected
    from starlette._utils import get_route_path  # aynı kural (Starlette'in iç işlevi; sürümle değişirse test söyler)

    assert route_path(scope) == get_route_path(scope)  # type: ignore[arg-type]


def test_a_root_path_does_not_skip_the_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(security.TOKEN_ENV, TOKEN)
    behind = TestClient(app, root_path="/sofa")
    refused = behind.get("/sofa/api/v1/jobs")
    assert refused.status_code == 401 and refused.json()["error"]["code"] == "unauthorized"
    assert refused.headers["x-request-id"]  # v1 yolu olarak tanındı: v1 hata modeli ve istek kimliği
    allowed = behind.get("/sofa/api/v1/jobs", headers={"Authorization": f"Bearer {TOKEN}"})
    assert allowed.status_code == 200, allowed.text
    # v1'in kendi hataları da v1 modeliyle (çatının 404'ü, doğrulama hatası)
    unknown = behind.get("/sofa/api/v1/nope", headers={"Authorization": f"Bearer {TOKEN}"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"
    invalid = behind.get("/sofa/api/v1/jobs", params={"limit": 0}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "invalid_request"


@pytest.fixture
def waiting_job(manager: JobManager) -> Iterator[Tuple[str, threading.Event]]:  # noqa: F811
    """İptal sorulana kadar bekleyen, başka bir thread'de çalışan iş."""
    stopped = threading.Event()

    def run(handle: Any) -> JobOutcome:
        wait_for(handle.cancelled, what="the cancel request")
        stopped.set()
        return JobOutcome(state=JobState.CANCELLED)

    thread = threading.Thread(target=lambda: manager.submit(JobKind.SYNC, {}, run, origin=local_origin("cli"),
                                                             background=False), daemon=True)
    thread.start()
    job_id = wait_for(lambda: getattr(manager.active(), "id", None), what="the job to start")
    yield job_id, stopped
    thread.join(20)


@pytest.mark.parametrize("root", ["", "/sofa"])
def test_cancel_and_the_progress_stream_through_a_proxy(
        root: str, waiting_job: Tuple[str, threading.Event], monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Vekil gibi bir istemci (vekilin başlıkları, isteğe göre kök yol, erişim belirteci): durdurma işi bitirir,
    ilerleme akışı vekil tamponlamasın diye başlıklarını taşır ve iş bitince kapanır.
    """
    monkeypatch.setenv(security.TOKEN_ENV, TOKEN)
    job_id, stopped = waiting_job
    proxy = TestClient(app, root_path=root, headers={**PROXY_HEADERS, "Authorization": f"Bearer {TOKEN}"})

    cancelled = proxy.post(f"{root}/api/v1/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["cancel_requested"] is True
    assert stopped.wait(20)

    response = proxy.get(f"{root}/api/v1/jobs/{job_id}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"  # nginx: bu yanıtı tamponlama
    assert response.headers["cache-control"] == "no-store"
    events = events_of(response)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "started" and "cancel_requested" in kinds and kinds[-1] == "finished"
    finished: Dict[str, Any] = events[-1]["data"]["data"]
    assert finished["state"] == "cancelled"
