"""
API v1: veri işleri, dışa aktarmalar, yedekler, log ve tanılama (plan maddesi P21; docs/design/02-services.md 2.8
ve 6; docs/design/05-web-ui.md 6.10, 6.11, 6.14 ve 7.3: G2, G13). Tümü çevrimdışı.

  * `POST /jobs` türleri: export (2.x'in geniş CSV'si, ham JSONL), backup, clear (`confirm` ister), rebuild ve
    restore (deneme; FX-13'ten beri gerçek geri yükleme de, `maintenance` kilidiyle); reddedilen istek iş kaydı
    bırakmaz;
  * temizleme ve katalog işleri `maintenance` kilidiyle çalışır: sürerken başka iş ve veri işlemi başlamaz, başka
    bir sürecin depo kopyası onların satırını bayat saymaz (iş deposunun kilit seçimi);
  * `/exports` ve dosyası, `/backups` ve dosyası (silinmiş dosya v1 modeliyle 404);
  * `/logs`, `/diagnostics`, `/diagnostics/bundle`.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List

import conftest
import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from sofascore_scraper.services.backup import BackupService
from sofascore_scraper.services.export import ExportService
from sofascore_scraper.store import EventQuery, JobStore, Store, default_db_path, open_store
from sofascore_scraper.web import deps
from sofascore_scraper.web.app import app

client = TestClient(app)


def wait_for(condition: Any, timeout: float = 30.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} did not happen within {timeout} s"
        time.sleep(0.01)


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return open_store(fixture.data_dir)


@pytest.fixture
def jobs(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    """Web'in iş deposu, veri dizininin state.db'sinde (üretimdeki gibi ayrı bir bağlantı)."""
    before = conftest.job_threads()
    job_store = JobStore(default_db_path(str(store.data_dir)))
    monkeypatch.setattr(deps, "job_store", lambda: job_store)
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


def start(body: Dict[str, Any]) -> Dict[str, Any]:
    response = client.post("/api/v1/jobs", json=body)
    job = data(response, 202)
    assert response.headers["location"] == f"/api/v1/jobs/{job['id']}"
    return job


def ended(job_id: str) -> Dict[str, Any]:
    job = wait_for(lambda: (lambda j: j if j["finished_at"] else None)(data(client.get(f"/api/v1/jobs/{job_id}"))),
                   what="the job to end")
    return job


# --- dışa aktarma -------------------------------------------------------------------------------------


def test_the_wide_csv_export_is_written_and_downloaded(jobs: JobStore, store: Store) -> None:
    job = start({"kind": "export", "spec": {"profile": "legacy-wide-csv"}})
    assert (job["kind"], job["spec"]["profile"], job["spec"]["schema"]) == ("export", "legacy-wide-csv", "normalized")
    done = ended(job["id"])
    assert done["state"] == "succeeded", done
    table = ExportService(store).legacy_table()
    result = done["result"]["export"]
    # FX-19: okunur ad: <lig ya da veri kümesi>_<UTC tarih>_<iş kimliğinin son 8 harfi>.<uzantı>
    name = str(result["file"])
    assert re.fullmatch(r"events-wide_\d{4}-\d{2}-\d{2}_" + job["id"][-8:].lower() + r"\.csv", name), name
    assert (result["rows"], result["file"], result["media_type"]) == (len(table.rows), name, "text/csv")
    assert "path" not in result

    record = data(client.get("/api/v1/exports"))[0]
    assert {k: record[k] for k in ("id", "job_id", "state", "dataset", "format", "schema", "profile", "rows",
                                   "available", "file")} == {
        "id": job["id"], "job_id": job["id"], "state": "succeeded", "dataset": "events", "format": "csv",
        "schema": "normalized", "profile": "legacy-wide-csv", "rows": len(table.rows), "available": True,
        "file": name,
    }
    assert record["filter"] == {"sport": None, "tournament_ids": [], "season_ids": [], "event_ids": [],
                                "status_classes": [], "from": None, "to": None}  # SC-2: üç süzgeç daha

    r = client.get(f"/api/v1/exports/{job['id']}/download")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f'filename="{name}"' in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[0].split(",") == list(table.columns) and len(lines) == len(table.rows) + 1
    assert r.content == (store.data_dir / "exports" / name).read_bytes()


def test_a_raw_export_of_one_tournament(jobs: JobStore, store: Store) -> None:
    job = start({"kind": "export", "spec": {"dataset": "slices", "schema": "raw", "format": "jsonl",
                                            "filter": {"tournament_ids": [sf.NBA.id]}}})
    assert ended(job["id"])["state"] == "succeeded"
    lines = [json.loads(line) for line in client.get(f"/api/v1/exports/{job['id']}/download").text.splitlines()]
    nba = {row.id for row in store.events.list(EventQuery(limit=500, has_details=True)).items
           if row.tournament_id == sf.NBA.id}
    assert lines and {line["event_id"] for line in lines} == nba
    assert set(lines[0]) == {"event_id", "key", "sub", "fetched_at", "payload"}
    assert {line["key"] for line in lines} > {"event"}

    events_only = start({"kind": "export", "spec": {"dataset": "events", "schema": "raw", "format": "jsonl",
                                                    "filter": {"tournament_ids": [sf.NBA.id]}}})
    assert ended(events_only["id"])["state"] == "succeeded"
    keys = {json.loads(line)["key"] for line in client.get(f"/api/v1/exports/{events_only['id']}/download").text.splitlines()}
    assert keys == {"event"}


@pytest.mark.parametrize("spec,status,code", [
    ({"schema": "raw", "format": "csv"}, 400, "invalid_request"),
    ({"profile": "legacy-wide-csv", "format": "jsonl"}, 400, "invalid_request"),
    ({"profile": "legacy-wide-csv", "filter": {"season_ids": [1]}}, 400, "invalid_request"),
    ({"dataset": "changes", "schema": "raw", "format": "jsonl"}, 400, "invalid_request"),  # SC-2: changes'in hamı yok
    ({"dataset": "lineups"}, 422, "invalid_request"),  # P28'den beri `odds` bir veri kümesidir
    ({"profile": "pretty"}, 422, "invalid_request"),
])
def test_exports_that_cannot_be_made_start_no_job(jobs: JobStore, spec: Dict[str, Any], status: int,
                                                  code: str) -> None:
    error(client.post("/api/v1/jobs", json={"kind": "export", "spec": spec}), status, code)
    assert data(client.get("/api/v1/jobs")) == []


def test_downloads_of_exports_without_a_file(jobs: JobStore, store: Store) -> None:
    error(client.get("/api/v1/exports/nope/download"), 404, "not_found")
    job = start({"kind": "export", "spec": {"profile": "legacy-wide-csv"}})
    (store.data_dir / "exports" / ended(job["id"])["result"]["export"]["file"]).unlink()
    gone = client.get(f"/api/v1/exports/{job['id']}/download")
    body = error(gone, 404, "not_found")
    assert body["details"] == {"export_id": job["id"]} and gone.headers["x-request-id"] == body["request_id"]


def test_a_failed_export_has_no_download(jobs: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("broken")

    monkeypatch.setattr("sofascore_scraper.services.data_jobs.run_export", broken)
    job = start({"kind": "export", "spec": {"profile": "legacy-wide-csv"}})
    assert ended(job["id"])["state"] == "failed"
    record = data(client.get("/api/v1/exports"))[0]
    assert (record["state"], record["available"], record["rows"]) == ("failed", False, None)
    assert error(client.get(f"/api/v1/exports/{job['id']}/download"), 404, "not_found")["details"]["state"] == "failed"


def test_the_export_list_pages(jobs: JobStore) -> None:
    ids = []
    for _ in range(3):
        job = start({"kind": "export", "spec": {"profile": "legacy-wide-csv"}})
        ended(job["id"])
        ids.append(job["id"])
    first = client.get("/api/v1/exports", params={"limit": 2}).json()
    assert [e["id"] for e in first["data"]] == ids[::-1][:2] and first["page"]["next_cursor"] == ids[1]
    second = client.get("/api/v1/exports", params={"limit": 2, "cursor": first["page"]["next_cursor"]}).json()
    assert [e["id"] for e in second["data"]] == [ids[0]] and second["page"]["next_cursor"] is None
    error(client.get("/api/v1/exports", params={"cursor": "nope"}), 400, "invalid_request")


# --- yedek ve geri yükleme denemesi --------------------------------------------------------------------


def test_a_backup_job_and_the_backup_list(jobs: JobStore, store: Store) -> None:
    job = start({"kind": "backup", "spec": {"scope": "data"}})
    done = ended(job["id"])
    assert done["state"] == "succeeded", done
    made = done["result"]["backup"]
    assert made["scope"] == "data" and made["with_env"] is False and made["format"] == 2
    listed = data(client.get("/api/v1/backups"))
    assert [b["name"] for b in listed] == [made["name"]]
    assert listed[0]["bytes"] == made["bytes"] and listed[0]["created_at_utc"].endswith("Z")
    r = client.get(f"/api/v1/backups/{made['name']}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.content == (store.data_dir / "backups" / made["name"]).read_bytes()
    assert "backup.json" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    error(client.get("/api/v1/backups/backup_all_20200101_000000.zip"), 404, "not_found")
    error(client.get("/api/v1/backups/..%2Fstate.db"), 404, "not_found")


def test_an_export_runs_while_a_download_runs(jobs: JobStore, store: Store) -> None:
    """F14 (FX-23): dışa aktarma veriyi yalnızca okur; web'in indirmesi `writer`ı tutarken de başlar ve biter."""
    sync_id = jobs.create_running({"kind": "sync"}, kind="sync", replace_running=False)
    try:
        job = start({"kind": "export", "spec": {"dataset": "events", "format": "jsonl"}})
        done = ended(job["id"])
        assert done["state"] == "succeeded", done
        assert done["result"]["export"]["rows"] > 0
        listed = {j["id"]: j["state"] for j in data(client.get("/api/v1/jobs"))}
        assert listed == {sync_id: "running", job["id"]: "succeeded"}
    finally:
        jobs.update(status="Completed", finished=True)


def test_an_export_runs_while_another_process_downloads(jobs: JobStore, store: Store) -> None:
    with store.lease("writer", purpose="job"):
        done = ended(start({"kind": "export", "spec": {"dataset": "events", "format": "csv"}})["id"])
    assert done["state"] == "succeeded", done


def test_an_export_waits_for_maintenance_and_for_another_export(jobs: JobStore, store: Store) -> None:
    body = {"kind": "export", "spec": {"dataset": "events", "format": "csv"}}
    with store.lease("maintenance", purpose="op:clear"):
        refused = error(client.post("/api/v1/jobs", json=body), 409, "data_operation_running")
    assert refused["details"]["holder"]["lease"] == "maintenance"
    with store.lease("export", purpose="export"):
        error(client.post("/api/v1/jobs", json=body), 409, "data_operation_running")
    assert data(client.get("/api/v1/jobs")) == []  # reddedilen istek iş kaydı bırakmaz


@pytest.mark.parametrize("following", [
    {"kind": "export", "spec": {"profile": "legacy-wide-csv"}},
    {"kind": "rebuild", "spec": {"mode": "recreate"}},
], ids=["export", "maintenance"])
def test_a_finished_export_does_not_refuse_the_next_job(jobs: JobStore, monkeypatch: pytest.MonkeyPatch,
                                                        following: Dict[str, Any]) -> None:
    """
    Bir dışa aktarmanın satırı bitmiş görünür (`finished_at`), ama `export` kilidini (ve paylaşımlı `maintenance`ı)
    bitiş bloğunun sonunda bırakır (`JobStore._job_finishing`: `job.finished` akış olayı ve geri okuma). Yükteki
    CI'da bu arada başlatılan dışa aktarma 409 `data_operation_running` alıyordu (test_the_export_list_pages).
    İşin bittiğini gören istemci 409 almaz: sonraki dışa aktarma ya da bakım işi o kilidin bırakılmasını bekler.
    """
    from sofascore_scraper.jobs import manager as manager_module

    in_finish, release = threading.Event(), threading.Event()
    announce = manager_module.JobManager._announce

    def slow_finish(self: Any, type: str, payload: Any) -> None:
        if type == manager_module.STREAM_JOB_FINISHED and not in_finish.is_set():
            in_finish.set()
            release.wait(30)  # bitiş bloğunu yükteki gibi uzatır; kilit bu sürede tutulur
        announce(self, type, payload)

    monkeypatch.setattr(manager_module.JobManager, "_announce", slow_finish)
    first = start({"kind": "export", "spec": {"profile": "legacy-wide-csv"}})
    assert in_finish.wait(30)
    assert data(client.get(f"/api/v1/jobs/{first['id']}"))["finished_at"]  # istemci işi bitmiş görür
    opener = threading.Timer(0.5, release.set)
    opener.start()
    try:
        second = start(following)  # FX-23'ten bu düzeltmeye kadar: 409 data_operation_running
        assert release.is_set()  # ikinci iş, ilkinin bitiş bloğu bitene kadar bekledi
    finally:
        opener.cancel()
        release.set()
    assert ended(first["id"])["state"] == "succeeded"
    assert ended(second["id"])["state"] == "succeeded"


def test_a_backup_job_records_itself_as_finished_in_the_archive(jobs: JobStore, store: Store,
                                                                 tmp_path: Path) -> None:
    """F30 (FX-23): yedekteki state.db'de yedeği alan iş `completed`; geri yüklemeden sonra yarıda kalmış görünmez."""
    import sqlite3

    job = start({"kind": "backup", "spec": {"scope": "all"}})
    done = ended(job["id"])
    assert done["state"] == "succeeded", done
    archived = tmp_path / "archived_state.db"
    with zipfile.ZipFile(store.data_dir / "backups" / done["result"]["backup"]["name"]) as zf:
        archived.write_bytes(zf.read(".meta/state.db"))
    conn = sqlite3.connect(archived)
    try:
        row = conn.execute("SELECT status, finished_at FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    finally:
        conn.close()
    assert row[0] == "completed" and row[1]


def test_a_restore_is_checked_not_done(jobs: JobStore, store: Store) -> None:
    name = BackupService(store).create("data").name
    job = start({"kind": "restore", "spec": {"name": name}})
    done = ended(job["id"])
    assert done["state"] == "succeeded", done
    report = done["result"]["restore"]
    assert report["dry_run"] is True and report["name"] == name and report["occupied"]
    error(client.post("/api/v1/jobs", json={"kind": "restore", "spec": {"name": "nope.zip"}}), 404, "not_found")
    # FX-13: gerçek geri yükleme boş olmayan bir hedefe `force` olmadan başlamaz; iş kaydı bırakmaz
    refused = error(client.post("/api/v1/jobs", json={"kind": "restore", "spec": {"name": name, "dry_run": False}}),
                    400, "confirmation_required")
    assert refused["details"]["occupied"] == report["occupied"]
    assert len(data(client.get("/api/v1/jobs"))) == 1


def test_a_restore_replaces_the_data_and_leaves_a_finished_job(jobs: JobStore, store: Store) -> None:
    """
    FX-13 (G2): `dry_run: false` geri yükler. Geri yüklenen state.db'nin iş geçmişinde işin kendi satırı korunur:
    iş `succeeded` biter ve `RestoreReport`u taşır; yedekten sonra yazılan veri çöpe taşınmıştır.
    """
    name = BackupService(store).create("all").name
    before = store.events.count(EventQuery(has_details=True))
    cleared = start({"kind": "clear", "spec": {"scope": "events", "confirm": True}})
    assert ended(cleared["id"])["state"] == "succeeded"
    assert store.events.count(EventQuery(has_details=True)) == 0

    job = start({"kind": "restore", "spec": {"name": name, "dry_run": False, "force": True}})
    done = ended(job["id"])

    assert done["state"] == "succeeded", done
    report = done["result"]["restore"]
    assert report["dry_run"] is False and report["force"] is True and report["name"] == name
    assert report["catalog_rebuilt"] is True and report["verify_ok"] in (True, None)
    assert set(report) >= {"occupied", "replaced", "restored", "skipped", "counts"}
    assert open_store(store.data_dir).events.count(EventQuery(has_details=True)) == before
    # İşin satırı ve olayları geri yüklenen geçmişte durur; bitişini kendi satırına yazdı
    assert data(client.get(f"/api/v1/jobs/{job['id']}"))["state"] == "succeeded"
    events = jobs.read_events(job["id"])
    assert events[0]["type"] == "started" and events[-1]["type"] == "finished"
    # Yedeğin iş geçmişi geri gelir (kapsam `all` state.db'yi taşır); temizleme işi yedekten sonraydı
    assert cleared["id"] not in {j["id"] for j in data(client.get("/api/v1/jobs"))}


def test_a_restore_holds_the_maintenance_lease(jobs: JobStore, store: Store) -> None:
    name = BackupService(store).create("data").name
    jobs.create_running({"kind": "sync"}, kind="sync", purpose="sync")
    try:
        error(client.post("/api/v1/jobs", json={"kind": "restore", "spec": {"name": name, "dry_run": False,
                                                                          "force": True}}), 409, "job_running")
    finally:
        jobs.update(status="Cancelled", finished=True)


# --- temizleme ve katalog: `maintenance` kilidi ---------------------------------------------------------


def test_clearing_needs_confirmation(jobs: JobStore, store: Store) -> None:
    body = error(client.post("/api/v1/jobs", json={"kind": "clear", "spec": {"scope": "events"}}), 400,
                 "confirmation_required")
    assert body["details"] == {"scope": "events"}
    assert data(client.get("/api/v1/jobs")) == [] and store.events.count(EventQuery(has_details=True)) > 0


def test_a_clear_job_deletes_the_scope(jobs: JobStore, store: Store) -> None:
    job = start({"kind": "clear", "spec": {"scope": "events", "confirm": True}})
    done = ended(job["id"])
    assert done["state"] == "succeeded", done
    assert done["result"]["clear"]["scopes"] == ["events"] and done["result"]["clear"]["catalog_rebuilt"] is True
    assert store.events.count(EventQuery(has_details=True)) == 0


def test_a_rebuild_job(jobs: JobStore, store: Store) -> None:
    job = start({"kind": "rebuild", "spec": {"mode": "recreate"}})
    done = ended(job["id"])
    assert done["state"] == "succeeded", done
    assert done["result"]["rebuild"]["mode"] == "recreate" and done["result"]["rebuild"]["events"] > 0


def test_a_running_clear_excludes_every_other_job(jobs: JobStore, store: Store) -> None:
    """Kilit seçiminin kendisi tests/test_job_leases.py'dedir; burada API'nin gördüğü."""
    jobs.create_running({"kind": "clear"}, kind="clear", purpose="clear", lease="maintenance",
                        replace_running=False)
    try:
        error(client.post("/api/v1/jobs", json={"kind": "sync"}), 409, "job_running")
        error(client.post("/api/v1/jobs", json={"kind": "rebuild"}), 409, "job_running")
    finally:
        jobs.update(status="Completed", finished=True)


def test_a_clear_is_refused_while_another_process_writes(jobs: JobStore, store: Store) -> None:
    with store.lease("writer", purpose="job"):
        body = error(client.post("/api/v1/jobs", json={"kind": "clear", "spec": {"confirm": True}}), 409,
                     "job_running")
    assert body["details"]["holder"]["lease"] == "writer"


# --- log ve tanılama --------------------------------------------------------------------------------------


def test_the_log_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper import diagnostics

    entries = [{"time": "2026-10-01 12:00:00,000", "level": "INFO", "pid": 1, "logger": "X", "message": "hello"}]
    seen: List[Any] = []

    def fake(limit: int = 200, min_level: Any = None) -> Dict[str, Any]:
        seen.append((limit, min_level))
        return {"enabled": True, "file": "/x/app.log", "level": "INFO", "min_level": min_level, "count": 1,
                "entries": entries}

    monkeypatch.setattr(diagnostics, "read_log_entries", fake)
    assert data(client.get("/api/v1/logs", params={"limit": 5, "level": "WARNING"})) == {
        "enabled": True, "file": "/x/app.log", "level": "INFO", "min_level": "WARNING", "count": 1, "entries": entries,
    }
    assert seen == [(5, "WARNING")]
    error(client.get("/api/v1/logs", params={"level": "LOUD"}), 422, "invalid_request")
    error(client.get("/api/v1/logs", params={"limit": 0}), 422, "invalid_request")


def test_the_real_log_tail_reads() -> None:
    body = data(client.get("/api/v1/logs", params={"limit": 3}))
    assert set(body) == {"enabled", "file", "level", "min_level", "count", "entries"} and body["count"] <= 3


def test_diagnostics_and_the_bundle() -> None:
    summary = data(client.get("/api/v1/diagnostics"))
    assert summary["source"] == "web" and summary["app"]["name"] == "sofascore-scraper"
    r = client.get("/api/v1/diagnostics/bundle", params={"log_lines": 5})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "no-store" and "sofascore-diagnostics-" in r.headers["content-disposition"]
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == ["README.txt", "diagnostics.json", "log_tail.txt"]
    error(client.get("/api/v1/diagnostics/bundle", params={"log_lines": -1}), 422, "invalid_request")


def test_no_secret_value_reaches_the_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper import redact

    proxy = "http://" + "user:" + "-".join(("fake", "proxy", "word")) + "@proxy.example:8080"
    monkeypatch.setenv("PROXY_URL", proxy)
    redact.refresh()
    try:
        text = client.get("/api/v1/diagnostics").text
        assert "fake-proxy-word" not in text
    finally:
        monkeypatch.delenv("PROXY_URL")
        redact.refresh()
