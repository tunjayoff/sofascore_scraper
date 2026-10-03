"""
Çalışan iş korumaları: veri silme, yedek, DATA_DIR değişimi ve lig silme iş sürerken 409 döner;
iş yokken eskisi gibi çalışır. İş deposu DATA_DIR değişimini izler. Tümü çevrimdışı.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

import conftest
from src.web import deps
from src.web.api import legacy as fetch_job
from src.web.app import app
from src.web.jobs import (
    DataOperationRunningError,
    JobRunningError,
    JobStore,
    default_db_path,
)
from src.web.api import legacy as data_mod
from src.web.api import legacy as settings_mod

client = TestClient(app)
store = deps.job_store()


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
    DATA_DIR'i gerçekten değiştiren testler: doğrulayıcı tmp_path altını kabul eder; test bitince
    ortam değişkeni, .env ve iş deposu conftest'in dizinine geri döner.
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
    store.rebind(default_db_path(conftest.DATA_DIR))


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
        assert "config/leagues.txt" in zf.namelist()  # biçim 2 (ST-24): ayar dosyaları config/ altında
        assert not any(n.startswith(("seasons/", "matches/", "match_details/", "v3/")) for n in zf.namelist())


def test_backup_works_when_no_job_runs(scratch_data_dir):
    r = client.post("/api/data/backup")
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(scratch_data_dir / "backups" / r.json()["filename"]) as zf:
        assert "matches/17_Premier_League/x.json" in zf.namelist()  # biçim 2: veri dizinine göre yol
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
        deps.config_manager().remove_league(99901)


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


def test_data_dir_change_moves_the_job_store(data_dir_sandbox):
    old_dir, new_dir = str(data_dir_sandbox / "first"), str(data_dir_sandbox / "second")
    assert client.post("/api/settings", json={"data_dir": old_dir}).status_code == 200
    old_job = store.create_running({"mode": "full", "where": "old"})
    _finish_job()

    r = client.post("/api/settings", json={"data_dir": new_dir})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["data_dir_changed"] is True
    assert "not moved" in body["message"]

    assert client.get("/api/settings").json()["data_dir"] == new_dir
    assert store.db_path == os.path.join(new_dir, ".meta", "state.db")
    # Yeni dizinin geçmişi boş; eski dizinin son işi "şu anki durum" olarak görünmez
    assert client.get("/api/jobs").json()["jobs"] == []
    status = client.get("/api/scrape/status").json()
    assert status["job_id"] is None and status["status"] == "Idle"

    # Yeni iş yeni dizinin veritabanına yazılır
    started = client.post("/api/fetch", json={"mode": "full", "league_id": 17})
    assert started.status_code == 200, started.text
    new_job = started.json()["job_id"]
    _finish_job()
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [new_job]
    conn = sqlite3.connect(default_db_path(new_dir))
    try:
        assert [row[0] for row in conn.execute("SELECT id FROM jobs")] == [new_job]
    finally:
        conn.close()  # `with sqlite3.connect(...)` bağlantıyı kapatmaz, yalnızca işlemi bitirir

    # Eski klasöre dönünce eski geçmiş yerinde
    r = client.post("/api/settings", json={"data_dir": old_dir})
    assert r.status_code == 200 and r.json()["data_dir_changed"] is True
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [old_job]


def test_unusable_data_dir_is_rejected_without_changing_anything(data_dir_sandbox):
    blocker = data_dir_sandbox / "a_file"
    blocker.write_text("not a folder", encoding="utf-8")
    db_before = store.db_path

    r = client.post("/api/settings", json={"data_dir": str(blocker / "data"), "max_retries": 9})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "data_dir_unusable"
    assert os.environ["DATA_DIR"] == conftest.DATA_DIR
    assert client.get("/api/settings").json()["max_retries"] != 9
    assert store.db_path == db_before
    # Yuva bırakıldı
    assert client.post("/api/fetch", json={"mode": "full", "league_id": 17}).status_code == 200


def test_job_store_returns_when_the_env_write_fails(data_dir_sandbox, monkeypatch):
    """.env yazılamazsa DATA_DIR eskisi gibi kalır; depo da eski dizinde kalmalı."""
    db_before = store.db_path
    monkeypatch.setattr(deps.config_manager(), "update_env_variable", lambda key, value: False)
    r = client.post("/api/settings", json={"data_dir": str(data_dir_sandbox / "other")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "no_change"
    assert "data_dir_changed" not in r.json()
    assert store.db_path == db_before


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


def test_rebind_switches_database_and_resets_the_mirror(tmp_path):
    a, b = str(tmp_path / "a" / "jobs.db"), str(tmp_path / "b" / "jobs.db")
    s = JobStore(a)
    first = s.create_running({"n": 1})
    with pytest.raises(JobRunningError):
        s.rebind(b)
    assert s.db_path == a
    s.update(status="Completed", finished=True)

    assert s.rebind(a) is False  # aynı yol: yansıya dokunulmaz
    assert s.snapshot()["job_id"] == first

    assert s.rebind(b) is True
    assert s.db_path == b and s.list_jobs() == []
    assert s.snapshot()["job_id"] is None and s.snapshot()["status"] == "Idle"
    second = s.create_running({"n": 2})
    s.update(status="Completed", finished=True)
    assert [j["id"] for j in s.list_jobs()] == [second]

    assert s.rebind(a) is True
    assert [j["id"] for j in s.list_jobs()] == [first]


def test_rebind_marks_stale_running_rows_in_the_target(tmp_path):
    """Hedef dizinde çökmüş bir sunucudan kalan "running" satırı, açılıştaki gibi interrupted olur."""
    b = str(tmp_path / "b" / "jobs.db")
    crashed = JobStore(b)
    stale = crashed.create_running({})
    crashed.close()  # süreç öldü: `writer` kilidi düştü, satır "running" kaldı
    s = JobStore(str(tmp_path / "a" / "jobs.db"))
    assert s.rebind(b) is True
    assert s.get_job(stale)["status"] == "interrupted"
    assert s.snapshot()["is_running"] is False


def test_rebind_keeps_the_old_database_when_the_new_one_fails(tmp_path):
    a = str(tmp_path / "a" / "jobs.db")
    s = JobStore(a)
    job = s.create_running({})
    s.update(status="Completed", finished=True)
    (tmp_path / "file").write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        s.rebind(str(tmp_path / "file" / "jobs.db"))
    not_a_db = tmp_path / "c" / "jobs.db"
    not_a_db.parent.mkdir()
    not_a_db.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(sqlite3.Error):
        s.rebind(str(not_a_db))
    assert s.db_path == a
    assert [j["id"] for j in s.list_jobs()] == [job]
