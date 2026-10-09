"""
API v1'in OpenAPI kaydı (plan maddesi P20; docs/design/02-services.md bölüm 6 ve 6.1).

`docs/api/openapi-v1.json` sözleşmenin kaydıdır: bu test onu uygulamanın ürettiği belgeyle karşılaştırır ve
bildirilmemiş her değişiklikte düşer. Sözleşme bilerek değiştirildiyse dosya şöyle yeniden üretilir:

    python -m sofascore_scraper.web.openapi --write

Karşılaştırma ayrıştırılmış JSON üzerindedir (nesnelerde anahtar sırası sayılmaz, listelerde sayılır). Belgeyi
FastAPI ve pydantic üretir; kayıt `constraints.txt`'deki sürümlerle alınmıştır.

2.x'in `/api` yolları 3.0'da kullanımdan kalktı ve 3.1'de silindi (P30): uygulamanın `/api` altındaki her yolu
v1'dedir ve hiçbir yanıt `Deprecation` başlığı taşımaz.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.web import api as api_paths
from sofascore_scraper.web import openapi
from sofascore_scraper.web.app import app

MAX_SHOWN = 40
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
ERROR_REF = {"$ref": "#/components/schemas/ApiErrorResponse"}

client = TestClient(app)


@pytest.fixture(scope="module")
def full_document() -> Dict[str, Any]:
    """Uygulamanın tam belgesi, taze üretilmiş (FastAPI'nin önbelleği atlanır ve yerine konur)."""
    cached = app.openapi_schema
    app.openapi_schema = None
    try:
        return copy.deepcopy(app.openapi())
    finally:
        app.openapi_schema = cached


@pytest.fixture(scope="module")
def document() -> Dict[str, Any]:
    return openapi.v1_document()


def operations(doc: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    for path, item in doc["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                yield method.upper(), path, item[method]


# --- kayıt -------------------------------------------------------------------------------------------


def test_the_committed_document_matches_the_application(document: Dict[str, Any]) -> None:
    recorded = openapi.read_snapshot()
    assert recorded is not None, "docs/api/openapi-v1.json is missing; run: python -m sofascore_scraper.web.openapi --write"

    found = openapi.differences(recorded, document)

    if found:
        shown = "\n  ".join(found[:MAX_SHOWN])
        more = f"\n  ... and {len(found) - MAX_SHOWN} more" if len(found) > MAX_SHOWN else ""
        pytest.fail(
            f"docs/api/openapi-v1.json: {len(found)} difference(s) (recorded -> current)\n  {shown}{more}\n"
            "If the contract was changed on purpose: python -m sofascore_scraper.web.openapi --write",
            pytrace=False,
        )


def committed_bytes() -> bytes:
    """Depodaki kayıt, LF satır sonlarıyla (Windows'ta git çalışma kopyasına CRLF yazar: `* text=auto`)."""
    return openapi.SNAPSHOT_PATH.read_bytes().replace(b"\r\n", b"\n")


def test_the_committed_file_is_what_write_produces() -> None:
    text = committed_bytes()
    assert text.endswith(b"}\n") and b"\r" not in text
    rendered = openapi.render(json.loads(text)).encode("utf-8")
    assert rendered == text and b"\r" not in rendered


def test_the_document_is_the_v1_view(document: Dict[str, Any], full_document: Dict[str, Any]) -> None:
    assert document["info"] == {"title": openapi.TITLE, "description": openapi.DESCRIPTION, "version": "1"}
    assert document["openapi"] == full_document["openapi"]
    assert list(document["paths"]) == [path for path in full_document["paths"] if path.startswith("/api/v1/")]
    assert [f"{method} {path}" for method, path, _op in operations(document)] == [
        "GET /api/v1/health",
        "GET /api/v1/status",
        "POST /api/v1/status/check",
        "GET /api/v1/sports",
        "GET /api/v1/sports/{slug}",
        "GET /api/v1/sinks",
        "GET /api/v1/auth",
        "POST /api/v1/auth/login",
        "POST /api/v1/auth/logout",
        "GET /api/v1/follows",
        "POST /api/v1/follows",
        "GET /api/v1/follows/{follow_id}",
        "PATCH /api/v1/follows/{follow_id}",
        "DELETE /api/v1/follows/{follow_id}",
        "GET /api/v1/tournaments",
        "POST /api/v1/tournaments/search",
        "GET /api/v1/catalog/suggest",  # FX-20
        "GET /api/v1/tournaments/{tournament_id}",
        "GET /api/v1/teams/{team_id}",  # B1
        "GET /api/v1/tournaments/{tournament_id}/seasons",
        "GET /api/v1/seasons/{season_id}",
        "GET /api/v1/seasons/{season_id}/slices",  # P28
        "GET /api/v1/seasons/{season_id}/standings",  # P28
        "GET /api/v1/seasons/{season_id}/slices/{key}",
        "GET /api/v1/events",
        "GET /api/v1/events/{event_id}",
        "GET /api/v1/events/{event_id}/extra",
        "GET /api/v1/events/{event_id}/slices",
        "GET /api/v1/events/{event_id}/slices/{key}",
        "GET /api/v1/events/{event_id}/raw",
        "GET /api/v1/events/{event_id}/slices/{key}/raw",
        "GET /api/v1/events/{event_id}/odds",
        "GET /api/v1/events/{event_id}/odds/{key}",  # P28
        "GET /api/v1/changes",
        "GET /api/v1/jobs",
        "POST /api/v1/jobs",
        "GET /api/v1/jobs/{job_id}",
        "POST /api/v1/jobs/{job_id}/cancel",
        "GET /api/v1/jobs/{job_id}/events",
        "GET /api/v1/exports",
        "GET /api/v1/exports/{export_id}/download",
        "GET /api/v1/backups",
        "GET /api/v1/backups/{name}",
        "GET /api/v1/logs",
        "GET /api/v1/diagnostics",
        "GET /api/v1/diagnostics/bundle",
        "GET /api/v1/settings",
        "PATCH /api/v1/settings",
    ]
    # Belge kendi içinde kapalıdır: andığı her şema içindedir, anmadığı yoktur
    referenced = set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(document)))
    assert referenced == set(document["components"]["schemas"])


def test_there_is_no_live_resource(document: Dict[str, Any], full_document: Dict[str, Any]) -> None:
    """Canlı verinin HTTP ucu yoktur (sahibin kararı): ne v1'de ne eski yollarda bir canlı rota ya da akış."""
    assert not [path for path in full_document["paths"] if "live" in path or "watch" in path]
    streams = [path for _m, path, op in operations(document) if "text/event-stream" in json.dumps(op)]
    assert streams == ["/api/v1/jobs/{job_id}/events"]


def test_every_api_path_is_v1(document: Dict[str, Any], full_document: Dict[str, Any]) -> None:
    """2.x'in yolları 3.1'de kalktı: `/api` altında yalnızca v1 vardır ve belgenin her modeli v1'indir."""
    assert [path for path in full_document["paths"] if api_paths.is_api(path) and not api_paths.is_v1(path)] == []
    # FastAPI'nin kendi doğrulama modelleri, v1 dışındaki tek yolun (arayüzün `/{full_path}`) yanıtında
    assert set(full_document["components"]["schemas"]) - set(document["components"]["schemas"]) <= {
        "HTTPValidationError", "ValidationError"}
    assert not [op for _m, _p, op in operations(full_document) if op.get("deprecated")]


# --- her rota modelini bildirir ----------------------------------------------------------------------


def test_every_v1_operation_has_a_stable_id_and_a_summary(document: Dict[str, Any]) -> None:
    ids = [op["operationId"] for _m, _p, op in operations(document)]
    assert ids == [
        "getHealth", "getStatus", "checkConnection", "listSports", "getSport", "listSinks", "getAuth", "login",
        "logout", "listFollows", "addFollow", "getFollow", "updateFollow", "removeFollow", "listTournaments",
        "searchTournaments", "suggestCatalog", "getTournament", "getTeam", "listTournamentSeasons", "getSeason",
        "listSeasonSlices",
        "getSeasonStandings", "getSeasonSlice", "listEvents", "getEvent", "getEventExtra", "listEventSlices", "getEventSlice",
        "getEventRaw", "getEventSliceRaw", "listEventOdds", "listEventOddsSnapshots", "listChanges", "listJobs", "startJob", "getJob", "cancelJob", "streamJobEvents", "listExports",
        "downloadExport", "listBackups", "downloadBackup", "listLogs", "getDiagnostics", "downloadDiagnosticsBundle",
        "getSettings", "updateSettings",
    ]
    assert len(set(ids)) == len(ids)
    for method, path, op in operations(document):
        assert op.get("summary") and op.get("tags"), (method, path)
        assert not op.get("deprecated"), (method, path)


# Gövdesi zarf olmayan işlemler: işin olay akışı (SSE) ve saklanan SofaScore yükünün kendisi (ham yük, P21)
STREAM_OPERATIONS = {"/api/v1/jobs/{job_id}/events"}
RAW_OPERATIONS = {"/api/v1/events/{event_id}/raw", "/api/v1/events/{event_id}/slices/{key}/raw"}
# Dosya indirmeleri (P21): dışa aktarma, yedek ve tanılama paketi
DOWNLOAD_OPERATIONS = {
    "/api/v1/exports/{export_id}/download": "application/octet-stream",
    "/api/v1/backups/{name}": "application/zip",
    "/api/v1/diagnostics/bundle": "application/zip",
}


def test_every_v1_operation_declares_its_response_model(document: Dict[str, Any]) -> None:
    for method, path, op in operations(document):
        success = [status for status in op["responses"] if status.startswith("2")]
        assert len(success) == 1, (method, path)
        content = op["responses"][success[0]]["content"]
        if path in STREAM_OPERATIONS:
            assert list(content) == ["text/event-stream"]
            continue
        if path in RAW_OPERATIONS:
            assert list(content) == ["application/json"] and "$ref" not in content["application/json"]["schema"]
            assert {"ETag", "X-Sofascore-Fetched-At"} <= set(op["responses"][success[0]]["headers"])
            continue
        if path in DOWNLOAD_OPERATIONS:
            assert list(content) == [DOWNLOAD_OPERATIONS[path]]
            assert content[DOWNLOAD_OPERATIONS[path]]["schema"] == {"type": "string", "format": "binary"}
            continue
        schema = content["application/json"]["schema"]
        assert schema["$ref"].endswith("Response"), (method, path, schema)
        model = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
        # Zarf: tek kaynak {"data"}, liste {"data", "page"}
        assert list(model["properties"]) in (["data"], ["data", "page"]), (method, path)


def test_every_v1_operation_documents_its_errors_with_the_error_model(document: Dict[str, Any]) -> None:
    for method, path, op in operations(document):
        # 304: ham yük rotalarının `If-None-Match` yanıtı, gövdesiz
        errors = {status: body for status, body in op["responses"].items() if status[0] not in "23"}
        assert {"401", "422", "500"} <= set(errors), (method, path)
        for status, body in errors.items():
            assert body["content"]["application/json"]["schema"] == ERROR_REF, (method, path, status)
        if method != "GET":
            assert "403" in errors, (method, path)  # durum değiştiren istek: kaynak denetimi
    start = document["paths"]["/api/v1/jobs"]["post"]["responses"]
    assert start["409"]["description"] == "job_running, data_operation_running, instance_running"
    assert set(start) == {"202", "400", "401", "403", "404", "409", "422", "500", "501", "507"}


def test_state_changing_requests_are_never_get(document: Dict[str, Any]) -> None:
    writes = {f"{method} {path}" for method, path, _op in operations(document) if method != "GET"}
    assert writes == {
        "POST /api/v1/jobs", "POST /api/v1/jobs/{job_id}/cancel", "PATCH /api/v1/settings", "POST /api/v1/status/check",
        "POST /api/v1/auth/login", "POST /api/v1/auth/logout", "POST /api/v1/follows", "PATCH /api/v1/follows/{follow_id}",
        "DELETE /api/v1/follows/{follow_id}", "POST /api/v1/tournaments/search",
    }


# --- komut satırı ------------------------------------------------------------------------------------


def test_check_passes_on_the_committed_file_and_fails_on_a_stale_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    assert openapi.main(["--check"]) == 0

    stale = json.loads(openapi.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    del stale["paths"]["/api/v1/health"]
    stale["info"]["version"] = "0"
    target = tmp_path / "docs" / "api" / "openapi-v1.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(openapi, "SNAPSHOT_PATH", target)
    monkeypatch.setattr(openapi, "ROOT", tmp_path)
    capsys.readouterr()

    assert openapi.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "$ > paths: added '/api/v1/health'" in err and "$ > info > version: '0' -> '1'" in err
    assert "2 difference(s); run: python -m sofascore_scraper.web.openapi --write" in err


def test_write_regenerates_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    target = tmp_path / "docs" / "api" / "openapi-v1.json"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr(openapi, "SNAPSHOT_PATH", target)
    monkeypatch.setattr(openapi, "ROOT", tmp_path)

    assert openapi.main(["--check"]) == 1 and "is missing" in capsys.readouterr().err
    assert openapi.main(["--write"]) == 0

    committed = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi-v1.json"
    assert openapi.differences(json.loads(committed.read_text(encoding="utf-8")), json.loads(target.read_text(encoding="utf-8"))) == []
    assert openapi.main(["--check"]) == 0


def test_without_a_flag_the_document_goes_to_stdout(capsys: pytest.CaptureFixture, document: Dict[str, Any]) -> None:
    capsys.readouterr()
    assert openapi.main([]) == 0
    assert json.loads(capsys.readouterr().out) == document


def test_the_program_prints_only_the_document_and_leaves_nothing_behind(tmp_path: Path) -> None:
    """
    `python -m sofascore_scraper.web.openapi`, bir geliştiricinin kabuğundaki gibi (testlerin yalıtım değişkenleri olmadan):
    standart çıktı kaydın kendisidir (log satırları stderr'e gider), çalışma dizininde dosya oluşmaz ve yerel
    yapılandırma (sunucunun başlamasını durduracak bir dosya, bir erişim belirteci) sonucu etkilemez.
    """
    logs = openapi.ROOT / "logs"
    had_logs = logs.exists()
    (tmp_path / "sofascore.toml").write_text('[server]\ntoken_env = "A_VARIABLE_NOBODY_SETS"\n', encoding="utf-8")
    env = {
        name: value for name, value in os.environ.items()
        if name not in openapi._ISOLATED_PATHS and name not in ("SOFASCORE_CONFIG", "SOFASCORE_SERVER__ALLOWED_HOSTS")
    }
    env.update(PYTHONPATH=str(openapi.ROOT), SOFASCORE_SERVER__TOKEN="short", PYTHONIOENCODING="utf-8")

    done = subprocess.run(
        [sys.executable, "-m", "sofascore_scraper.web.openapi"], cwd=str(tmp_path), env=env, capture_output=True, timeout=120,
        check=False,
    )

    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.replace(b"\r\n", b"\n") == committed_bytes()
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["sofascore.toml"]
    assert logs.exists() == had_logs


def test_differences_ignore_key_order_but_not_types_or_list_order() -> None:
    assert not openapi.differences({"a": 1, "b": [1, 2]}, {"b": [1, 2], "a": 1})
    assert openapi.differences({"a": [1, 2]}, {"a": [2, 1]}) == ["$ > a[0]: 1 -> 2", "$ > a[1]: 2 -> 1"]
    assert openapi.differences({"a": 1}, {"a": True}) == ["$ > a: 1 (int) -> True (bool)"]
    assert openapi.differences({"a": 1, "b": 2}, {"b": 2, "c": 3}) == ["$: removed 'a'", "$: added 'c'"]
    assert openapi.differences([1], [1, 2]) == ["$: length 1 -> 2"]


def test_v1_view_keeps_only_v1_paths_and_their_schemas() -> None:
    def ref(name: str) -> Dict[str, str]:
        return {"$ref": openapi.SCHEMA_REF + name}

    doc = {
        "info": {"title": "x", "version": "9.9.9"},
        "paths": {
            "/api/leagues": {"get": {"responses": {"200": {"schema": ref("League")}}}},
            "/api/v1/jobs": {"get": {"responses": {"200": {"schema": ref("Job")}, "422": {"schema": ref("Err")}}}},
            "/api/v10": {"get": {"responses": {"200": {"schema": ref("Other")}}}},
        },
        "components": {"schemas": {
            "League": {"type": "object"}, "Other": {"type": "object"},
            "Job": {"properties": {"error": {"anyOf": [ref("JobError"), {"type": "null"}]}}},
            "JobError": {"type": "object"}, "Err": {"properties": {"cause": ref("Err")}},
        }},
    }
    before = copy.deepcopy(doc)

    view = openapi.v1_view(doc)

    assert doc == before
    assert list(view["paths"]) == ["/api/v1/jobs"]
    assert list(view["components"]["schemas"]) == ["Err", "Job", "JobError"]
    assert view["info"]["version"] == "1"


# --- eski yollar: deprecated ve başlıklar ------------------------------------------------------------


def test_no_response_carries_deprecation_headers() -> None:
    for path in ("/api/v1/health", "/api/v1/jobs", "/api/v1/nope", "/health", "/", "/api/nope", "/openapi.json",
                 "/api/status", "/api/jobs"):
        headers = client.get(path).headers
        assert "deprecation" not in headers and "link" not in headers, path
