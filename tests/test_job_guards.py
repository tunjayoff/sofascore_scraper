"""
Çalışan iş korumaları: veri silme, yedek, DATA_DIR değişimi ve lig silme iş sürerken 409 döner;
iş yokken eskisi gibi çalışır. Tümü çevrimdışı.
"""
from __future__ import annotations

import os
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

import conftest
from src.web import fetch_job
from src.web.app import app
from src.web.jobs import DataOperationRunningError, JobRunningError, JobStore
from src.web.routes import api as api_mod
from src.web.routes import data as data_mod
from src.web.routes import settings as settings_mod

client = TestClient(app)
store = api_mod._job_store


def _finish_job() -> None:
    store.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)


@pytest.fixture(autouse=True)
def _idle_store_and_no_real_jobs(monkeypatch):
    """Her test boş depoyla başlar; bir koruma bozulursa bile gerçek indirme işi başlamaz."""
    monkeypatch.setattr(fetch_job, "run_fetch_job", lambda job_id, payload: None)
    if store.snapshot().get("is_running"):
        _finish_job()
    yield
    if store.snapshot().get("is_running"):
        _finish_job()


@pytest.fixture
def running_job():
    job_id = store.create_running({"mode": "full"})
    yield job_id
    _finish_job()


@pytest.fixture
def scratch_data_dir(tmp_path, monkeypatch):
    """Silme/yedek testleri conftest'in ortak veri setine değil, kendi küçük dizinine dokunur."""
    root = tmp_path / "data"
    for sub in ("seasons", "matches", "match_details"):
        (root / sub / "17_Premier_League").mkdir(parents=True)
        (root / sub / "17_Premier_League" / "x.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(root))
    return root


@pytest.fixture
def data_dir_sandbox(tmp_path, monkeypatch):
    """
    DATA_DIR'i değiştirmeyi deneyen testler: doğrulayıcı tmp_path altını kabul eder; test bitince
    ortam değişkeni ve .env conftest'in dizinine geri döner.
    """
    monkeypatch.setattr(settings_mod, "_REPO_ROOT", tmp_path)
    with open(conftest.ENV_FILE, encoding="utf-8") as f:
        env_before = f.read()
    yield tmp_path
    if store.snapshot().get("is_running"):
        _finish_job()
    os.environ["DATA_DIR"] = conftest.DATA_DIR
    with open(conftest.ENV_FILE, "w", encoding="utf-8") as f:
        f.write(env_before)


def _assert_conflict(response, code: str) -> None:
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]


# --- POST /api/data/clear ---


def test_clear_is_refused_while_a_job_runs(scratch_data_dir, running_job):
    _assert_conflict(client.post("/api/data/clear", json={"scope": "all"}), "job_running")
    for sub in ("seasons", "matches", "match_details"):
        assert (scratch_data_dir / sub / "17_Premier_League" / "x.json").is_file()


def test_clear_works_when_no_job_runs(scratch_data_dir):
    r = client.post("/api/data/clear", json={"scope": "matches"})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "success", "cleared": ["matches"]}
    assert os.listdir(scratch_data_dir / "matches") == []
    assert (scratch_data_dir / "seasons" / "17_Premier_League" / "x.json").is_file()
    # Yuva bırakıldı: ardından iş başlatılabilir
    assert client.post("/api/fetch", json={"mode": "full", "league_id": 17}).status_code == 200


def test_job_cannot_start_while_a_clear_is_in_progress(scratch_data_dir, monkeypatch):
    """Silme ile iş başlatma arasındaki yarış: silme sürerken /api/fetch 409 alır, iş oluşmaz."""
    entered, release = threading.Event(), threading.Event()
    real_clear = data_mod._clear_data_sync

    def slow_clear(scope):
        entered.set()
        assert release.wait(10)
        return real_clear(scope)

    monkeypatch.setattr(data_mod, "_clear_data_sync", slow_clear)
    result = {}
    t = threading.Thread(
        target=lambda: result.update(r=TestClient(app).post("/api/data/clear", json={"scope": "all"}))
    )
    t.start()
    try:
        assert entered.wait(10)
        _assert_conflict(client.post("/api/fetch", json={"mode": "full", "league_id": 17}), "data_operation_running")
        assert store.snapshot()["is_running"] is False
        # Aynı anda ikinci bir veri işlemi de reddedilir (yedek, silinen dizini okurdu)
        _assert_conflict(client.post("/api/data/backup"), "data_operation_running")
    finally:
        release.set()
        t.join(10)
    assert result["r"].status_code == 200, result["r"].text
    assert sorted(result["r"].json()["cleared"]) == ["match_details", "matches", "seasons"]


# --- POST /api/data/backup ---


def test_data_backup_is_refused_while_a_job_runs(scratch_data_dir, running_job):
    for scope in ("all", "seasons", "matches", "match_details"):
        _assert_conflict(client.post(f"/api/data/backup?scope={scope}"), "job_running")
    assert not (scratch_data_dir / "backups").exists()


def test_config_backup_is_allowed_while_a_job_runs(scratch_data_dir, running_job):
    """İş ayar dosyalarına yazmaz: yalnızca ayar içeren yedek iş sürerken de alınabilir."""
    r = client.post("/api/data/backup?scope=config")
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(scratch_data_dir / "backups" / r.json()["filename"]) as zf:
        assert "leagues.txt" in zf.namelist()
        assert not any(n.startswith("data/") for n in zf.namelist())


def test_backup_works_when_no_job_runs(scratch_data_dir):
    r = client.post("/api/data/backup")
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(scratch_data_dir / "backups" / r.json()["filename"]) as zf:
        assert "data/matches/17_Premier_League/x.json" in zf.namelist()
    assert client.post("/api/fetch", json={"mode": "full", "league_id": 17}).status_code == 200


# --- DELETE /api/leagues/{id} ---


def test_league_delete_is_refused_while_a_job_runs(running_job):
    _assert_conflict(client.delete(f"/api/leagues/{conftest.LEAGUE_ID}"), "job_running")
    assert conftest.LEAGUE_ID in [lg["id"] for lg in client.get("/api/leagues").json()]


def test_league_delete_works_when_no_job_runs():
    assert client.post("/api/leagues", json={"id": 99901, "name": "Guard Test League", "sport": "football"}).status_code == 200
    try:
        r = client.delete("/api/leagues/99901")
        assert r.status_code == 200, r.text
        assert 99901 not in [lg["id"] for lg in client.get("/api/leagues").json()]
        # Olmayan lig hâlâ 404 ve yuvayı tutmaz
        assert client.delete("/api/leagues/99901").status_code == 404
        assert client.post("/api/fetch", json={"mode": "full", "league_id": 17}).status_code == 200
    finally:
        api_mod.config_manager.remove_league(99901)


# --- POST /api/settings (DATA_DIR) ---


def test_data_dir_change_is_refused_while_a_job_runs(data_dir_sandbox, running_job):
    new_dir = str(data_dir_sandbox / "other")
    with open(conftest.ENV_FILE, encoding="utf-8") as f:
        env_before = f.read()
    db_before = store.db_path

    _assert_conflict(client.post("/api/settings", json={"data_dir": new_dir, "max_retries": 7}), "job_running")

    # Hiçbir şey yazılmadı: ne DATA_DIR ne de aynı istekteki diğer ayar
    assert os.environ["DATA_DIR"] == conftest.DATA_DIR
    with open(conftest.ENV_FILE, encoding="utf-8") as f:
        assert f.read() == env_before
    assert client.get("/api/settings").json()["data_dir"] == conftest.DATA_DIR
    assert store.db_path == db_before
    assert not os.path.exists(new_dir)
    assert store.snapshot()["job_id"] == running_job


def test_other_settings_still_save_while_a_job_runs(data_dir_sandbox, running_job, monkeypatch):
    """Arayüz veri klasörünü değiştirmeden başka ayar kaydedebilmeli; aynı klasörü göndermek de serbest."""
    before = client.get("/api/settings").json()["max_retries"]
    monkeypatch.setenv("MAX_RETRIES", str(before))  # test bitince ortam değişkeni eski haline döner
    r = client.post("/api/settings", json={"data_dir": conftest.DATA_DIR, "max_retries": before + 1})
    assert r.status_code == 200, r.text
    assert "data_dir_changed" not in r.json()
    assert client.get("/api/settings").json()["max_retries"] == before + 1
    assert store.snapshot()["job_id"] == running_job


# --- JobStore ---


def test_exclusive_slot_rules(tmp_path):
    s = JobStore(str(tmp_path / "jobs.db"))
    with s.exclusive("clear"):
        with pytest.raises(DataOperationRunningError) as busy:
            s.create_running({})
        assert busy.value.code == "data_operation_running" and busy.value.operation == "clear"
        with pytest.raises(DataOperationRunningError):
            with s.exclusive("backup"):
                pass
    assert s.snapshot()["is_running"] is False

    # Gövde hata verse de yuva bırakılır
    with pytest.raises(ValueError):
        with s.exclusive("clear"):
            raise ValueError("boom")
    s.create_running({})
    with pytest.raises(JobRunningError) as running:
        with s.exclusive("clear"):
            pytest.fail("iş çalışırken yuva alınmamalı")
    assert running.value.code == "job_running"
    # Reddedilen deneme yuvayı kilitli bırakmaz
    s.update(status="Completed", finished=True)
    with s.exclusive("clear"):
        pass
