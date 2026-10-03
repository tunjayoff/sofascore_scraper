"""Web sunucusu güvenlik sınırları: dosya okuma, Host/Origin, yedekler, girdi doğrulama."""
from __future__ import annotations

import asyncio
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from src.paths import env_file_path
from src.web import deps
from src.web.app import FRONTEND_DIST, app
from src.web.api import legacy as api_mod

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
    r = client.post("/api/data/backup", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_write_allowed():
    r = client.post("/api/scrape/cancel", headers={"origin": "http://testserver"})
    assert r.status_code != 403


def test_backup_is_outside_static_and_excludes_env():
    assert os.path.exists(env_file_path())
    r = client.post("/api/data/backup")
    assert r.status_code == 200
    body = r.json()
    assert body["download_url"] == f"/api/data/backups/{body['filename']}"
    path = os.path.join(api_mod._backups_dir(), body["filename"])
    assert "static" not in path
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert "config/.env" not in names  # biçim 2 (ST-24): ayarlar config/ altında
    assert "config/leagues.txt" in names

    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"


def test_backup_download_rejects_other_names():
    assert client.get("/api/data/backups/..%2F..%2F.env").status_code == 404
    assert client.get("/api/data/backups/leagues.txt").status_code == 404


def test_backup_rejects_unknown_scope():
    assert client.post("/api/data/backup?scope=../../x").status_code == 422


def test_clear_rejects_unknown_scope():
    assert client.post("/api/data/clear", json={"scope": "everything"}).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"proxy_url": "http://x:1\nDATA_DIR=/etc"},
        {"date_format": "%Y\nFOO=bar"},
        {"api_base_url": "https://attacker.example/api/v1"},
        {"api_base_url": "http://www.sofascore.com/api/v1"},
        {"data_dir": "/etc"},
        {"max_concurrent": -1},
        {"log_level": "LOUD"},
    ],
)
def test_settings_rejects_bad_values(payload):
    before = open(env_file_path(), encoding="utf-8").read()
    r = client.post("/api/settings", json=payload)
    assert r.status_code == 422
    assert open(env_file_path(), encoding="utf-8").read() == before


def test_env_writer_rejects_newlines():
    assert deps.config_manager().update_env_variable("PROXY_URL", "a\nB=c") is False


@pytest.mark.parametrize("name", ["Evil\nInjected", "Serie A: Italy", "../../escape", "..", "a/b"])
def test_add_league_rejects_unsafe_names(name):
    r = client.post("/api/leagues", json={"id": 424242, "name": name})
    assert r.status_code == 422
    assert 424242 not in deps.config_manager().get_leagues()


def test_match_id_must_be_numeric():
    assert client.get("/api/matches/..%2F..%2Fx").status_code in (404, 422)
    assert client.get("/api/matches/abc").status_code == 422
