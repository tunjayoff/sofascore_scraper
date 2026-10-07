"""
Durum ve ayar uçlarının küçük kusurları (plan maddesi FX-2). Tümü çevrimdışı.

  * GET /api/status uygulamanın sürümünü verir (koda yazılmış "1.0.0" değil).
  * DATA_DIR, Store'un açamadığı bir dizine değiştirilmek istenirse yanıt 500 değil 400
    `data_dir_unusable` olur ve hiçbir şey değişmez.

Maskeli proxy parolasının geri konma kuralı tests/test_settings_proxy.py'dedir.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

import conftest
from sofascore_scraper.store import LeaseHeld, SchemaTooNew, StoreBusy, StoreError
from sofascore_scraper.version import __version__, read_version
from sofascore_scraper.web import deps
from sofascore_scraper.web.api import legacy as fetch_job
from sofascore_scraper.web.app import app
from sofascore_scraper.web.jobs import JobStore, default_db_path
from sofascore_scraper.web.api import legacy as scrape_mod
from sofascore_scraper.web.api import legacy as settings_mod

client = TestClient(app)
store = deps.job_store()


# --- GET /api/status ---------------------------------------------------------------------------


def test_status_reports_the_application_version():
    body = client.get("/api/status").json()
    assert body["version"] == __version__ == read_version()
    # /health ve OpenAPI belgesiyle aynı değer
    assert body["version"] == client.get("/health").json()["version"] == app.version
    assert list(body) == ["version", "leagues_count", "language"]


def test_status_version_is_not_a_constant_of_the_route(monkeypatch):
    monkeypatch.setattr(scrape_mod, "__version__", "9.8.7-test")
    assert client.get("/api/status").json()["version"] == "9.8.7-test"


# --- POST /api/settings: Store'un açamadığı veri dizini -----------------------------------------


def _finish_job() -> None:
    store.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)


def _read_env() -> str:
    with open(conftest.ENV_FILE, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def data_dir_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Doğrulayıcı tmp_path altını kabul eder; test bitince ortam değişkeni, .env ve iş deposu conftest'in
    dizinine geri döner. Bir koruma bozulursa bile gerçek indirme işi başlamaz.
    """
    monkeypatch.setattr(settings_mod, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(fetch_job, "run_fetch_job", lambda job_id, payload: None)
    env_before = _read_env()
    if store.snapshot().get("is_running"):
        _finish_job()
    yield tmp_path
    if store.snapshot().get("is_running"):
        _finish_job()
    os.environ["DATA_DIR"] = conftest.DATA_DIR
    with open(conftest.ENV_FILE, "w", encoding="utf-8") as f:
        f.write(env_before)
    store.rebind(default_db_path(conftest.DATA_DIR))


def _user_version(db_path: str) -> int:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _newer_state_db(data_dir: Path) -> str:
    """Bu sürümün kurduğu bir state.db; şema sürümü sonradan koddakinden ileriye alınır."""
    db_path = default_db_path(str(data_dir))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    JobStore(db_path).close()
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {_user_version(db_path) + 100}")
    return db_path


def _foreign_sqlite_db(data_dir: Path) -> str:
    """Geçerli bir SQLite dosyası, ama state.db değil (application_id yok, kendi tablosu var)."""
    db_path = default_db_path(str(data_dir))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES ('not ours')")
        conn.commit()
    return db_path


def _garbage_file(data_dir: Path) -> str:
    """SQLite bile olmayan bir dosya: bu durum FX-2'den önce de 400 idi (sqlite3.Error)."""
    db_path = default_db_path(str(data_dir))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with open(db_path, "wb") as f:
        f.write(b"this is not sqlite" * 100)
    return db_path


def _assert_nothing_changed(response, env_before: str, db_before: str) -> None:
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "data_dir_unusable"
    assert isinstance(detail["message"], str) and detail["message"]
    # Ne DATA_DIR ne de aynı istekteki diğer ayar yazıldı
    assert os.environ["DATA_DIR"] == conftest.DATA_DIR
    assert _read_env() == env_before
    settings = client.get("/api/settings").json()
    assert settings["data_dir"] == conftest.DATA_DIR and settings["max_retries"] != 9
    # İş deposu eski dizinde ve çalışıyor: yuva bırakıldı, yeni iş eski veritabanına yazılır
    assert store.db_path == db_before
    started = client.post("/api/fetch", json={"mode": "full", "league_id": 17})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    _finish_job()
    assert job_id in [job["id"] for job in client.get("/api/jobs").json()["jobs"]]
    assert store.db_path == db_before


@pytest.mark.parametrize(
    "prepare, raised",
    [
        (_newer_state_db, SchemaTooNew),
        (_foreign_sqlite_db, StoreError),
        (_garbage_file, sqlite3.Error),
    ],
    ids=["state_db_newer_than_code", "not_a_state_db", "not_sqlite"],
)
def test_data_dir_the_store_cannot_open_answers_400(
    data_dir_sandbox: Path, prepare: Callable[[Path], str], raised: type
):
    new_dir = data_dir_sandbox / "other"
    db_path = prepare(new_dir)
    with open(db_path, "rb") as f:
        file_before = f.read()
    # Senaryo gerçekten beklenen hatayı üretiyor (yanıtın 400 olması başka bir nedenden değil)
    with pytest.raises(raised):
        JobStore(db_path)
    env_before, db_before = _read_env(), store.db_path

    r = client.post("/api/settings", json={"data_dir": str(new_dir), "max_retries": 9})

    _assert_nothing_changed(r, env_before, db_before)
    # Açılamayan dosyaya dokunulmadı
    with open(db_path, "rb") as f:
        assert f.read() == file_before


@pytest.mark.parametrize(
    "error",
    [
        StoreBusy("state.db meşgul: yazma kilidi 5000 ms içinde alınamadı"),
        LeaseHeld(name="maintenance", pid=4242, host="elsewhere", purpose="migrate"),
        StoreError("state.db geçişi başarısız, geri alındı: 0002_job_events.sql"),
    ],
    ids=["store_busy", "lease_held", "migration_failed"],
)
def test_data_dir_change_answers_400_for_every_store_error(
    data_dir_sandbox: Path, monkeypatch: pytest.MonkeyPatch, error: StoreError
):
    """
    Kilit zaman aşımı, başka süreçteki bakım kilidi ve başarısız geçiş gerçek dosyalarla kurulmaz
    (beş saniyelik bekleme, ikinci süreç); depo açılışının bu hataları fırlattığı durum taklit edilir.
    """
    new_dir = data_dir_sandbox / "other"
    real_open = JobStore._open

    def failing_open(db_path: str):
        if os.path.abspath(db_path) == os.path.abspath(default_db_path(str(new_dir))):
            raise error
        return real_open(db_path)

    monkeypatch.setattr(JobStore, "_open", staticmethod(failing_open))
    env_before, db_before = _read_env(), store.db_path

    r = client.post("/api/settings", json={"data_dir": str(new_dir), "max_retries": 9})

    _assert_nothing_changed(r, env_before, db_before)


def test_usable_data_dir_still_changes(data_dir_sandbox: Path):
    """Karşı örnek: aynı istek, Store'un açabildiği bir dizinle 200 döner ve depo taşınır."""
    new_dir = data_dir_sandbox / "other"
    r = client.post("/api/settings", json={"data_dir": str(new_dir)})
    assert r.status_code == 200, r.text
    assert r.json()["data_dir_changed"] is True
    assert store.db_path == default_db_path(str(new_dir))
