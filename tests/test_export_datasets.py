"""
Normalleştirilmiş veri kümelerinin dışa aktarması (plan maddesi SC-2; docs/design/02-services.md 2.7,
docs/design/04-schema-v1.md karar 19, 22 ve 23). Tümü çevrimdışı, `tests/store_fixtures.py` veri dizinleri üzerinde.

  * sütunlar modellerden çıkar: her yaprak alan bir sütun, adı yolun `_` ile birleşimi; listeler JSON metni;
  * her veri kümesi (events, slices, changes) ve her biçim (JSONL, CSV, Parquet, SQLite) geri okunur ve
    şema katmanının kayıtlarına eşittir; Parquet `pyarrow` yoksa atlanır, `pyarrow` olmadan istek `not_supported`;
  * süzgeçli dışa aktarma API v1'in aynı süzgeçli listesine eşittir (`GET /events`, `/events/{id}/slices`,
    `/changes`);
  * hiç taşınmamış bir 2.x dizininin dışa aktarması, taşımadan sonrakine eşittir;
  * ham dışa aktarma (`schema="raw"`) servisten geçer; `ssc export` ve API'nin dışa aktarma işi veri kümelerini yazar.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

import conftest
import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from src.errors import NotFoundError, NotSupportedError, UsageError
from src.schema import SCHEMA_VERSION
from src.services import export as export_service
from src.services.export import (DatasetFilter, DatasetSpec, ExportService, check_dataset, dataset_columns,
                                 flatten_record, leaf_paths, parse_moment, record_model)
from src.store import JobStore, Store, StoreError, default_db_path, open_store
from src.store import export as store_export
from src.web import deps
from src.web.app import app

client = TestClient(app)

DATASETS = ("events", "slices", "changes")
TABLE_FORMATS = ("csv", "sqlite", "parquet")
FORMATS = ("jsonl", *TABLE_FORMATS)


@pytest.fixture
def canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return open_store(fixture.data_dir)


def records(store: Store, dataset: str, flt: Optional[DatasetFilter] = None) -> List[Dict[str, Any]]:
    return [record.to_dict() for record in ExportService(store).records(dataset, flt)]


def flat(store: Store, dataset: str, flt: Optional[DatasetFilter] = None) -> List[Dict[str, Any]]:
    paths = export_service._column_paths(dataset)
    return [flatten_record(record, paths) for record in records(store, dataset, flt)]


def needs_pyarrow(fmt: str) -> None:
    if fmt == "parquet":
        pytest.importorskip("pyarrow")


def read_back(path: Path, fmt: str, dataset: str) -> List[Dict[str, Any]]:
    """Yazılan dosyanın satırları, yazıcının hücre kuralıyla (CSV metin, SQLite / Parquet değer)."""
    if fmt == "jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if fmt == "csv":
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    if fmt == "sqlite":
        conn = sqlite3.connect(path)
        try:
            cursor = conn.execute(f'SELECT * FROM "{dataset}"')
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        finally:
            conn.close()
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()


def expected_cells(rows: Sequence[Mapping[str, Any]], fmt: str) -> List[Dict[str, Any]]:
    """Kayıtların düz satırları, biçimin hücre kuralıyla (src/store/export.py)."""
    if fmt == "csv":
        return [{k: store_export._text(v) for k, v in row.items()} for row in rows]
    if fmt == "sqlite":
        return [{k: store_export._sqlite_value(v) for k, v in row.items()} for row in rows]
    out = []
    for row in rows:  # parquet: liste ve sözlük JSON metni, sayılar sütunun türüyle
        out.append({k: store_export._text(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    return out


def same_parquet(got: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> None:
    assert len(got) == len(expected)
    for g, e in zip(got, expected, strict=True):
        assert list(g) == list(e)
        for key in e:
            if isinstance(e[key], (int, float)) and not isinstance(e[key], bool) and isinstance(g[key], (int, float)):
                assert float(g[key]) == float(e[key]), key
            elif isinstance(g[key], str) and not isinstance(e[key], str) and e[key] is not None:
                assert g[key] == store_export._text(e[key]), key  # ilk satır grubunda boş: metin sütunu
            else:
                assert g[key] == e[key], key


# --- sütunlar -------------------------------------------------------------------------------------------


def test_columns_are_the_leaf_paths_of_the_models_joined_with_an_underscore() -> None:
    events = dataset_columns("events", "csv")
    for name in ("id", "status_class", "status_type", "score_home", "score_half_time_home", "score_periods",
                 "score_sets", "participants_home_name", "quality_observed_at_utc", "aggregate_winner"):
        assert name in events
    assert "status" not in events and "score" not in events  # nesneler açılır
    assert dataset_columns("slices", "csv") == ("owner_kind", "owner_id", "key", "sub", "state", "has_payload",
                                                "fetched_at_utc", "checked_at_utc", "error_reason",
                                                "error_http_status", "error_at_utc", "error_count", "payload")
    assert dataset_columns("changes", "csv")[-1] == "fields"  # liste: tek bir JSON sütunu
    for dataset in DATASETS:
        columns = dataset_columns(dataset, "csv")
        assert len(set(columns)) == len(columns)
        assert len({c.casefold() for c in columns}) == len(columns)  # SQLite büyük-küçük harf ayırmaz
        assert columns == tuple("_".join(path) for path in leaf_paths(record_model(dataset)))


def test_jsonl_columns_are_the_fields_of_the_record(canonical: Store) -> None:
    for dataset in DATASETS:
        found = records(canonical, dataset)
        assert found, dataset
        assert all(tuple(record) == dataset_columns(dataset, "jsonl") for record in found)


def test_every_score_family_is_covered_by_the_columns() -> None:
    from src.schema import models

    events = set(dataset_columns("events", "csv"))
    for family in (models.FootballScore, models.PeriodsScore, models.SetsScore, models.InningsScore,
                   models.CricketScore, models.FightScore, models.PlainScore):
        for path in leaf_paths(family):
            assert "_".join(("score", *path)) in events, (family.__name__, path)


def test_a_flat_row_has_every_column_and_nulls_under_a_null_object(canonical: Store) -> None:
    rows = flat(canonical, "events")
    columns = dataset_columns("events", "csv")
    assert all(tuple(row) == columns for row in rows)
    football = next(row for row in rows if row["score_family"] == "football")
    assert football["score_sets"] is None and football["score_periods"] is None  # başka ailenin alanı
    no_aggregate = next(row for row in rows if row["aggregate_home"] is None)
    assert no_aggregate["aggregate_winner"] is None
    tennis = next(row for row in rows if row["score_family"] == "sets" and row["score_sets"])
    assert isinstance(tennis["score_sets"], list)


# --- geri okuma -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("fmt", FORMATS)
def test_each_dataset_and_format_round_trips(canonical: Store, tmp_path: Path, dataset: str, fmt: str) -> None:
    needs_pyarrow(fmt)
    target = tmp_path / f"{dataset}.{fmt}"
    result = ExportService(canonical).export(DatasetSpec(dataset=dataset, format=fmt), target)

    assert result.path == str(target) and result.schema_version == SCHEMA_VERSION
    assert result.bytes == target.stat().st_size and result.columns == dataset_columns(dataset, fmt)
    got = read_back(target, fmt, dataset)
    if fmt == "jsonl":
        assert got == records(canonical, dataset)  # sözleşmenin JSON'u, iç içe, null'lar dahil
    elif fmt == "parquet":
        same_parquet(got, expected_cells(flat(canonical, dataset), fmt))
    else:
        assert got == expected_cells(flat(canonical, dataset), fmt)
    assert result.rows == len(got) > 0


def test_csv_cells_follow_the_writer_rules(canonical: Store, tmp_path: Path) -> None:
    target = tmp_path / "events.csv"
    ExportService(canonical).export(DatasetSpec(format="csv"), target)
    rows = read_back(target, "csv", "events")
    assert {row["quality_provisional"] for row in rows} <= {"true", "false"}
    tennis = next(row for row in rows if row["score_family"] == "sets" and row["score_sets"])
    assert isinstance(json.loads(tennis["score_sets"]), list)
    assert any(row["aggregate_home"] == "" for row in rows)  # null: boş hücre


def test_the_sqlite_export_is_a_table_named_after_the_dataset(canonical: Store, tmp_path: Path) -> None:
    for dataset in DATASETS:
        target = tmp_path / f"{dataset}.db"
        ExportService(canonical).export(DatasetSpec(dataset=dataset, format="sqlite"), target)
        conn = sqlite3.connect(target)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{dataset}")')]
        finally:
            conn.close()
        assert tables == [dataset] and tuple(columns) == dataset_columns(dataset, "sqlite")


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_stream_gets_the_bytes_of_the_file(canonical: Store, tmp_path: Path, fmt: str) -> None:
    needs_pyarrow(fmt)
    spec = DatasetSpec(dataset="slices", format=fmt)
    target = tmp_path / f"slices.{fmt}"
    service = ExportService(canonical)
    to_file = service.export(spec, target)
    stream = io.BytesIO()
    to_stream = service.export(spec, stream)
    assert to_stream.path is None and to_stream.rows == to_file.rows and to_stream.bytes == len(stream.getvalue())
    if fmt in ("jsonl", "csv"):
        assert stream.getvalue() == target.read_bytes()
    else:
        copy = tmp_path / f"copy.{fmt}"
        copy.write_bytes(stream.getvalue())
        assert read_back(copy, fmt, "slices") == read_back(target, fmt, "slices")


# --- içerik ---------------------------------------------------------------------------------------------


def test_slices_follow_the_events_and_carry_no_payload(canonical: Store) -> None:
    events = [event["id"] for event in records(canonical, "events")]
    slices = records(canonical, "slices")
    owners = list(dict.fromkeys(s["owner_id"] for s in slices))
    assert owners == [event_id for event_id in events if event_id in set(owners)]  # maçların sırasıyla
    assert all(s["owner_kind"] == "event" and s["payload"] is None for s in slices)
    for owner in owners:
        keys = [(s["key"], s["sub"] or "") for s in slices if s["owner_id"] == owner]
        assert keys == sorted(keys)
    assert {s["state"] for s in slices} >= {"ok", "empty"}


def test_changes_are_in_log_order_and_filtered_by_their_own_columns(canonical: Store) -> None:
    changes = records(canonical, "changes")
    assert [c["seq"] for c in changes] == sorted(c["seq"] for c in changes) and len(changes) == 2
    by_tournament = records(canonical, "changes", DatasetFilter(tournament_ids=(sf.NBA.id,)))
    assert [c["tournament_id"] for c in by_tournament] == [sf.NBA.id]
    assert records(canonical, "changes", DatasetFilter(sport="football"))[0]["sport"] == "football"
    one = changes[0]
    assert records(canonical, "changes", DatasetFilter(event_ids=(one["event_id"],))) == [one]
    moment = parse_moment(one["recorded_at_utc"], end=False)
    assert records(canonical, "changes", DatasetFilter(start_from=moment, start_to=moment)) == [one]


def test_filters_are_combined(canonical: Store) -> None:
    every = records(canonical, "events")
    football = records(canonical, "events", DatasetFilter(sport="football", status_classes=("completed",)))
    assert football and all(e["sport"] == "football" and e["status"]["class"] == "completed" for e in football)
    assert len(football) < len(every)
    season = every[0]["season_id"]
    assert all(e["season_id"] == season for e in records(canonical, "events", DatasetFilter(season_ids=(season,))))


# --- API ile aynı liste -------------------------------------------------------------------------------


def api_list(path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    response = client.get(path, params={**params, "limit": 200})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"]["next_cursor"] is None
    return body["data"]


@pytest.mark.parametrize("params,flt", [
    ({}, DatasetFilter()),
    ({"tournament": [sf.PL.id, sf.NBA.id]}, DatasetFilter(tournament_ids=(sf.PL.id, sf.NBA.id))),
    ({"sport": "football", "status": ["completed", "void"]},
     DatasetFilter(sport="football", status_classes=("completed", "void"))),
    ({"from": "2026-09-01", "to": "2026-09-20"},
     DatasetFilter(start_from=parse_moment("2026-09-01", end=False), start_to=parse_moment("2026-09-20", end=True))),
])
def test_a_filtered_export_equals_the_filtered_list_of_the_api(canonical: Store, tmp_path: Path,
                                                               params: Dict[str, Any], flt: DatasetFilter) -> None:
    listed = api_list("/api/v1/events", {**params, "sort": "start_utc"})
    for item in listed:
        assert item.pop("slices_summary") is None  # yalnızca `include=slices_summary` ile dolar
    target = tmp_path / "events.jsonl"
    ExportService(canonical).export(DatasetSpec(filter=flt), target)
    assert read_back(target, "jsonl", "events") == listed

    target = tmp_path / "slices.jsonl"
    ExportService(canonical).export(DatasetSpec(dataset="slices", filter=flt), target)
    exported = read_back(target, "jsonl", "slices")
    from_api = [s for item in listed for s in api_list(f"/api/v1/events/{item['id']}/slices", {})
                if s["state"] != "not_requested"]  # API sporun seçiminde olup satırı olmayanları da gösterir
    assert exported == from_api


def test_a_filtered_changes_export_equals_the_changes_list_of_the_api(canonical: Store, tmp_path: Path) -> None:
    for params, flt in (({}, DatasetFilter()),
                        ({"tournament": [sf.PL.id]}, DatasetFilter(tournament_ids=(sf.PL.id,))),
                        ({"from": "2026-09-20"}, DatasetFilter(start_from=parse_moment("2026-09-20", end=False)))):
        target = tmp_path / "changes.jsonl"
        ExportService(canonical).export(DatasetSpec(dataset="changes", filter=flt), target, overwrite=True)
        assert read_back(target, "jsonl", "changes") == api_list("/api/v1/changes", params)


# --- taşımadan önce ve sonra ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["canonical", "legacy"])
def test_an_export_of_a_never_migrated_directory_equals_the_export_after_migration(name: str, tmp_path: Path) -> None:
    fixture = sf.build_fixture(name, tmp_path / "data")
    store = open_store(fixture.data_dir)
    assert store.info(sizes=False).events_by_layout.get("v3", 0) == 0

    def exports(label: str) -> Dict[str, bytes]:
        out = {}
        for dataset in DATASETS:
            for fmt in ("jsonl", "csv"):
                target = tmp_path / label / f"{dataset}.{fmt}"
                ExportService(store).export(DatasetSpec(dataset=dataset, format=fmt), target)
                out[f"{dataset}.{fmt}"] = target.read_bytes()
        target = tmp_path / label / "raw.jsonl"
        ExportService(store).export(DatasetSpec(dataset="slices", format="jsonl", schema="raw"), target)
        out["raw.jsonl"] = target.read_bytes()
        return out

    before = exports("before")
    report = store.migrate.run()
    assert report.ok and report.events > 0
    assert store.info(sizes=False).events_by_layout.get("legacy", 0) == 0
    after = exports("after")
    assert before.keys() == after.keys()
    for key in before:
        assert before[key] == after[key], key
    assert before["events.jsonl"]


# --- boş seçim, hatalar ---------------------------------------------------------------------------------


def test_an_empty_selection(canonical: Store, tmp_path: Path) -> None:
    tmp_path = tmp_path / "out"
    tmp_path.mkdir()
    service = ExportService(canonical)
    nothing = DatasetFilter(tournament_ids=(999999,))
    with pytest.raises(NotFoundError):
        service.export(DatasetSpec(format="csv", filter=nothing), tmp_path / "none.csv", allow_empty=False)
    with pytest.raises(NotFoundError):
        service.export(DatasetSpec(schema="raw", filter=nothing), tmp_path / "none.jsonl", allow_empty=False)
    assert not list(tmp_path.iterdir())

    empty = service.export(DatasetSpec(format="csv", filter=nothing), tmp_path / "empty.csv")
    assert empty.rows == 0
    assert (tmp_path / "empty.csv").read_text(encoding="utf-8") == ",".join(dataset_columns("events", "csv")) + "\n"


def test_an_existing_target_needs_overwrite(canonical: Store, tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    service = ExportService(canonical)
    service.export(DatasetSpec(), target)
    with pytest.raises(StoreError):
        service.export(DatasetSpec(), target)
    assert service.export(DatasetSpec(format="jsonl"), target, overwrite=True).rows > 0


def test_parquet_without_pyarrow_is_not_supported_and_writes_nothing(canonical: Store, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path = tmp_path / "out"
    tmp_path.mkdir()
    monkeypatch.setattr(export_service, "parquet_available", lambda: False)
    with pytest.raises(NotSupportedError) as raised:
        ExportService(canonical).export(DatasetSpec(format="parquet"), tmp_path / "e.parquet")
    assert raised.value.details == {"dataset": "events", "format": "parquet", "schema": "normalized"}

    # pyarrow görünür ama içe aktarılamaz: Store'un hatası da `not_supported` olur
    monkeypatch.setattr(export_service, "parquet_available", lambda: True)

    def missing() -> Any:
        raise StoreError("no pyarrow", detail=store_export.PARQUET_PACKAGE)

    monkeypatch.setattr(store_export, "_pyarrow", missing)
    with pytest.raises(NotSupportedError):
        ExportService(canonical).export(DatasetSpec(format="parquet"), tmp_path / "e.parquet")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("spec", [
    DatasetSpec(dataset="odds"),
    DatasetSpec(schema="pretty"),
    DatasetSpec(format="tree"),
    DatasetSpec(format="json"),
    DatasetSpec(dataset="changes", schema="raw"),
    DatasetSpec(schema="raw", format="csv"),
    DatasetSpec(dataset="changes", filter=DatasetFilter(season_ids=(1,))),
    DatasetSpec(dataset="changes", filter=DatasetFilter(status_classes=("live",))),
    DatasetSpec(filter=DatasetFilter(status_classes=("finished",))),
    DatasetSpec(filter=DatasetFilter(start_from=2.0, start_to=1.0)),
])
def test_combinations_that_cannot_be_made_are_usage_errors(spec: DatasetSpec) -> None:
    with pytest.raises(UsageError):
        check_dataset(spec)


def test_the_raw_schema_writes_the_stored_payloads(canonical: Store, tmp_path: Path) -> None:
    service = ExportService(canonical)
    every = service.export(DatasetSpec(dataset="slices", schema="raw"), tmp_path / "all.jsonl")
    events_only = service.export(DatasetSpec(dataset="events", schema="raw"), tmp_path / "events.jsonl")
    tree = service.export(DatasetSpec(dataset="events", schema="raw", format="tree"), tmp_path / "tree")
    keys = {json.loads(line)["key"] for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()}
    assert keys == {"event"} and every.rows > events_only.rows == tree.rows and every.schema_version is None
    assert every.events == events_only.events > 0 and every.columns == ()
    with pytest.raises(UsageError):
        service.export(DatasetSpec(schema="raw"), io.BytesIO())


@pytest.mark.parametrize("text,end,expected", [
    ("2026-09-15", False, "2026-09-15T00:00:00+00:00"),
    ("2026-09-15", True, "2026-09-15T23:59:59+00:00"),
    ("2026-09-15T18:30:00Z", False, "2026-09-15T18:30:00+00:00"),
    ("2026-09-15T18:30", True, "2026-09-15T18:30:00+00:00"),
    ("2026-09-15T20:30:00+02:00", False, "2026-09-15T18:30:00+00:00"),
])
def test_moments_are_read_as_the_api_reads_them(text: str, end: bool, expected: str) -> None:
    import datetime as dt

    assert dt.datetime.fromtimestamp(parse_moment(text, end=end), dt.timezone.utc).isoformat() == expected


def test_a_bad_moment_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        parse_moment("yesterday", end=False)


# --- API v1: dışa aktarma işi ---------------------------------------------------------------------------


@pytest.fixture
def jobs(canonical: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    before = conftest.job_threads()
    job_store = JobStore(default_db_path(str(canonical.data_dir)))
    monkeypatch.setattr(deps, "job_store", lambda: job_store)
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


def finished(job_id: str) -> Dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
        if job["finished_at"]:
            return job
        assert time.monotonic() < deadline, "the export job did not end"
        time.sleep(0.01)


@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("fmt", FORMATS)
def test_the_export_job_writes_a_dataset(jobs: JobStore, canonical: Store, tmp_path: Path, dataset: str,
                                         fmt: str) -> None:
    needs_pyarrow(fmt)
    spec = {"dataset": dataset, "format": fmt, "filter": {"sport": "football"}}
    response = client.post("/api/v1/jobs", json={"kind": "export", "spec": spec})
    assert response.status_code == 202, response.text
    job = finished(response.json()["data"]["id"])
    assert job["state"] == "succeeded", job
    record = next(r for r in client.get("/api/v1/exports").json()["data"] if r["id"] == job["id"])
    assert (record["dataset"], record["format"], record["schema"], record["schema_version"], record["available"]) == (
        dataset, fmt, "normalized", SCHEMA_VERSION, True)
    assert record["filter"]["sport"] == "football"

    download = client.get(f"/api/v1/exports/{job['id']}/download")
    assert download.status_code == 200 and download.headers["content-type"].startswith(record["media_type"])
    copy = tmp_path / f"download.{fmt}"
    copy.write_bytes(download.content)
    expected = flat(canonical, dataset, DatasetFilter(sport="football"))
    got = read_back(copy, fmt, dataset)
    if fmt == "jsonl":
        assert got == records(canonical, dataset, DatasetFilter(sport="football"))
    elif fmt == "parquet":
        same_parquet(got, expected_cells(expected, fmt))
    else:
        assert got == expected_cells(expected, fmt)
    assert record["rows"] == len(got)


def test_the_export_job_takes_the_time_and_status_filters(jobs: JobStore, canonical: Store, tmp_path: Path) -> None:
    spec = {"dataset": "events", "format": "jsonl",
            "filter": {"status_classes": ["completed"], "from": "2026-09-01", "to": "2026-09-30T23:59:59Z"}}
    job = finished(client.post("/api/v1/jobs", json={"kind": "export", "spec": spec}).json()["data"]["id"])
    assert job["state"] == "succeeded", job
    assert job["spec"]["filter"]["from"] == "2026-09-01"
    lines = client.get(f"/api/v1/exports/{job['id']}/download").text.splitlines()
    flt = DatasetFilter(status_classes=("completed",), start_from=parse_moment("2026-09-01", end=False),
                        start_to=parse_moment("2026-09-30T23:59:59Z", end=True))
    assert [json.loads(line) for line in lines] == records(canonical, "events", flt)


@pytest.mark.parametrize("spec,status,code", [
    ({"dataset": "changes", "filter": {"season_ids": [1]}}, 400, "invalid_request"),
    ({"dataset": "events", "filter": {"from": "soon"}}, 400, "invalid_request"),
    ({"profile": "legacy-wide-csv", "filter": {"status_classes": ["live"]}}, 400, "invalid_request"),
    ({"dataset": "events", "filter": {"status_classes": ["finished"]}}, 422, "invalid_request"),
    ({"dataset": "events", "format": "parquet"}, 501, "not_supported"),
])
def test_export_jobs_that_cannot_be_made_start_no_job(jobs: JobStore, monkeypatch: pytest.MonkeyPatch,
                                                      spec: Dict[str, Any], status: int, code: str) -> None:
    monkeypatch.setattr(export_service, "parquet_available", lambda: False)
    response = client.post("/api/v1/jobs", json={"kind": "export", "spec": spec})
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert client.get("/api/v1/jobs").json()["data"] == []
