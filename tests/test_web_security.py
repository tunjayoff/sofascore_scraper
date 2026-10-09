"""Web sunucusu güvenlik sınırları: dosya okuma, Host/Origin, yedek indirme, girdi doğrulama."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.web.app import FRONTEND_DIST, app

client = TestClient(app)


def _raw_get(raw_path: bytes):
    """TestClient '..' segmentlerini normalize eder; uvicorn etmez — ham ASGI isteği gönder."""
    msgs = []
    scope = {
        "type": "http", "method": "GET", "path": raw_path.decode(), "raw_path": raw_path,
        "query_string": b"", "headers": [(b"host", b"127.0.0.1:8000")], "http_version": "1.1",
        "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 1), "root_path": "",
    }

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(m):
        msgs.append(m)

    asyncio.run(app(scope, receive, send))
    body = b"".join(m.get("body", b"") for m in msgs[1:])
    return msgs[0]["status"], body


@pytest.mark.skipif(not FRONTEND_DIST.is_dir(), reason="frontend/dist yok")
@pytest.mark.parametrize("path", [b"/../../README.md", b"/../../../../../../etc/hostname", b"/assets/../../README.md"])
def test_spa_fallback_does_not_serve_files_outside_dist(path):
    status, body = _raw_get(path)
    # Fallback index.html döndürür; /assets kendi kontrolüyle 404 verir. Dosya içeriği asla dönmez.
    index = (FRONTEND_DIST / "index.html").read_bytes()
    assert (status == 200 and body == index) or status == 404


def test_unknown_host_rejected():
    r = client.get("/health", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_cross_origin_write_rejected():
    r = client.post("/api/v1/jobs", json={"kind": "backup", "spec": {}}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_write_allowed():
    r = client.post("/api/v1/jobs/x/cancel", headers={"origin": "http://testserver"})
    assert r.status_code != 403


def test_backup_download_rejects_other_names():
    assert client.get("/api/v1/backups/..%2F..%2F.env").status_code == 404
    assert client.get("/api/v1/backups/leagues.txt").status_code == 404


def test_event_id_must_be_numeric():
    assert client.get("/api/v1/events/..%2F..%2Fx").status_code in (404, 422)
    assert client.get("/api/v1/events/abc").status_code == 422
