"""
Dışa aktarmalar (plan maddesi FX-19):

  * dosya adı okunur: `<lig ya da veri kümesi>_<UTC tarih>_<iş kimliğinin son 8 harfi>.<uzantı>`; indirmenin adı da o;
  * `GET /api/v1/exports` `exports/` dizininde hiçbir işin yazmadığı dosyaları da listeler (`ssc export`):
    `source: "file"`, kimlik `file:<ad>`, indirilebilir; dizin dışına çıkılamaz;
  * Store: `store.export.files()` ve `file_path(ad)`.

Ağ yok; veri dizini store_fixtures'ın "canonical" ağacıdır.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import conftest
import store_fixtures as sf
import test_cli_skeleton as skeleton
from sofascore_scraper.services.data_jobs import (ExportRequest, export_label, export_name, local_export_name,
                                                  slug)
from sofascore_scraper.store import FollowSpec, JobStore, Store, default_db_path, open_store
from sofascore_scraper.web import deps
from sofascore_scraper.web.app import app

client = TestClient(app)
cli = skeleton.cli  # komut satırı fikstürü


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(fixture.data_dir))
    return open_store(fixture.data_dir)


@pytest.fixture
def jobs(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    before = conftest.job_threads()
    job_store = JobStore(default_db_path(str(store.data_dir)))
    monkeypatch.setattr(deps, "job_store", lambda: job_store)
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def finished(job_id: str) -> Dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        job = data(client.get(f"/api/v1/jobs/{job_id}"))
        if job["finished_at"]:
            return job
        assert time.monotonic() < deadline
        time.sleep(0.02)


# --- ad ------------------------------------------------------------------------------------------------------


def test_the_label_of_an_export() -> None:
    assert slug("Süper Lig") == "super-lig" and slug("Wimbledon, Men") == "wimbledon-men"
    assert slug("x" * 60) == "x" * 40 and slug("—") == ""
    assert export_label(ExportRequest(dataset="events"), "Premier League") == "premier-league"
    assert export_label(ExportRequest(dataset="slices")) == "slices"
    assert export_label(ExportRequest(dataset="slices", schema="raw", format="jsonl"), "NBA") == "nba-raw"
    assert export_label(ExportRequest(profile="legacy-wide-csv")) == "events-wide"
    assert export_label(ExportRequest(dataset="odds"), "—") == "odds"


def test_the_name_of_an_export_uses_the_follow_or_the_catalog(store: Store) -> None:
    moment = 1791331200.0  # 2026-10-07T00:00:00Z
    by_catalog = export_name(store, "01M4949EJJWC8MPBBNH826T4ED", ExportRequest(tournament_ids=(sf.NBA.id,)),
                             now=moment)
    assert by_catalog == "nba_2026-10-07_h826t4ed.csv"
    store.follows.add(FollowSpec(kind="tournament", entity_id=sf.NBA.id, name="NBA Regular Season"))
    by_follow = export_name(store, "01M4949EJJWC8MPBBNH826T4ED",
                            ExportRequest(dataset="events", format="parquet", tournament_ids=(sf.NBA.id,)), now=moment)
    assert by_follow == "nba-regular-season_2026-10-07_h826t4ed.parquet"
    two = ExportRequest(dataset="changes", format="jsonl", tournament_ids=(sf.NBA.id, sf.PL.id))
    assert export_name(store, "abc", two, now=moment) == "changes_2026-10-07_abc.jsonl"


def test_an_export_job_writes_and_serves_the_readable_name(store: Store, jobs: JobStore) -> None:
    job = data(client.post("/api/v1/jobs", json={"kind": "export", "spec": {
        "dataset": "events", "format": "jsonl", "filter": {"tournament_ids": [sf.NBA.id]}}}), 202)
    done = finished(job["id"])
    name = done["result"]["export"]["file"]
    assert name.startswith("nba_") and name.endswith(f"_{job['id'][-8:].lower()}.jsonl")
    assert (store.data_dir / "exports" / name).is_file()
    answer = client.get(f"/api/v1/exports/{job['id']}/download")
    assert answer.status_code == 200 and f'filename="{name}"' in answer.headers["content-disposition"]
    listed = data(client.get("/api/v1/exports"))
    assert [(r["id"], r["source"], r["file"]) for r in listed] == [(job["id"], "job", name)]


def test_the_name_of_an_ssc_export_file_is_the_jobs_with_the_time(store: Store) -> None:
    """FX-34: `ssc export`'un `--out` verilmeyen dosyası işinkiyle aynı etiketi ve tarihi taşır, epoch'u değil."""
    moment = 1791386130.0  # 2026-10-07T15:15:30Z
    nba = ExportRequest(dataset="events", format="jsonl", tournament_ids=(sf.NBA.id,))
    assert local_export_name(store, nba, now=moment) == "nba_2026-10-07_151530.jsonl"
    two = ExportRequest(dataset="changes", format="jsonl", tournament_ids=(sf.NBA.id, sf.PL.id))
    assert local_export_name(store, two, now=moment) == "changes_2026-10-07_151530.jsonl"
    wide = ExportRequest(profile="legacy-wide-csv")
    assert local_export_name(store, wide, now=moment) == "events-wide_2026-10-07_151530.csv"
    store.follows.add(FollowSpec(kind="player", entity_id=424242, name="Kylian Mbappé"))
    player = ExportRequest(dataset="slices", format="csv", player_ids=(424242,))
    assert local_export_name(store, player, now=moment) == "kylian-mbappe_2026-10-07_151530.csv"


def test_ssc_export_names_its_default_files_like_the_web(store: Store, cli: Any) -> None:
    league = cli("--data-dir", str(store.data_dir), "export", "--dataset", "events", "--tournament",
                 str(sf.NBA.id), "--json")
    assert league.exit_code == 0, league.stderr
    path = Path(league.data["path"])
    assert path.parent == store.data_dir / "exports"
    assert re.fullmatch(r"nba_\d{4}-\d{2}-\d{2}_\d{6}\.jsonl", path.name), path.name


# --- `ssc export`'un dosyaları ---------------------------------------------------------------------------------


def test_files_written_by_ssc_export_are_listed_and_served(store: Store, jobs: JobStore, cli: Any) -> None:
    written = cli("--data-dir", str(store.data_dir), "export", "--dataset", "events", "--format", "jsonl", "--json")
    assert written.exit_code == 0, written.stderr
    name = os.path.basename(written.data["path"])
    exports = store.data_dir / "exports"
    (exports / ".hidden").write_text("x", encoding="utf-8")
    (exports / "folder").mkdir()
    (exports / "mine.unknown").write_text("x", encoding="utf-8")
    os.utime(exports / "mine.unknown", (1, 1))

    listed = data(client.get("/api/v1/exports"))
    assert [(r["id"], r["source"], r["job_id"]) for r in listed] == [
        (f"file:{name}", "file", None), ("file:mine.unknown", "file", None)]
    record = listed[0]
    assert {k: record[k] for k in ("state", "dataset", "format", "schema", "file", "available", "media_type")} == {
        "state": "succeeded", "dataset": "events", "format": "jsonl", "schema": "normalized", "file": name,
        "available": True, "media_type": "application/x-ndjson"}
    assert record["bytes"] == (exports / name).stat().st_size and record["created_at"]
    assert (listed[1]["dataset"], listed[1]["format"]) == ("unknown", "unknown")

    answer = client.get(f"/api/v1/exports/file:{name}/download")
    assert answer.status_code == 200 and answer.content == (exports / name).read_bytes()
    assert f'filename="{name}"' in answer.headers["content-disposition"]
    for bad in ("file:.hidden", "file:folder", "file:..%2Fstate.db", "file:nope.csv"):
        assert client.get(f"/api/v1/exports/{bad}/download").status_code == 404, bad
    # Sayfalama işsiz dosyaları da kapsar
    first = client.get("/api/v1/exports", params={"limit": 1}).json()
    assert first["page"]["next_cursor"] == f"file:{name}"
    rest = data(client.get("/api/v1/exports", params={"cursor": first["page"]["next_cursor"]}))
    assert [r["id"] for r in rest] == ["file:mine.unknown"]


def test_the_store_lists_the_export_folder(store: Store) -> None:
    assert store.export.files() == []
    exports = store.data_dir / "exports"
    exports.mkdir(exist_ok=True)
    (exports / "a.csv").write_text("x", encoding="utf-8")
    (exports / "b c (1).jsonl").write_text("yy", encoding="utf-8")
    os.utime(exports / "a.csv", (5, 5))
    assert [(f.name, f.size) for f in store.export.files()] == [("b c (1).jsonl", 2), ("a.csv", 1)]
    assert store.export.file_path("a.csv") == str(exports / "a.csv")
    for bad in ("../state.db", "", ".a", "a/b.csv", "missing.csv"):
        assert store.export.file_path(bad) is None, bad
