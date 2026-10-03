"""
POST /api/matches/{id}/fetch: SofaScore isteği reddettiğinde (plan maddesi FX-1) ve veri dizinine başka
biri yazarken (ST-10'un bu uç noktadaki parçası).

Eskiden reddedilen bir /event isteği 404 "Match data could not be fetched (may be unfinished or unavailable)",
dilimlerin hepsinin reddedilmesi de hiçbir dilim kaydedilmeden 200 "success" oluyordu: MatchDataFetcher'ın tek
maç yolu "maç yok" ile "istek başarısız"ı aynı None'a indirgiyordu. Artık yol her isteğin sonucunu bir rapora
yazar (SingleFetchReport) ve uç nokta lig araması ile sezon yenilemenin kullandığı tipli hatayı verir
(src/web/upstream.py: {"detail": {"reason", "message"}}).

Veri dizinine yazan biri varken (bu sürecin işi ya da `writer` kilidini tutan başka bir süreç) çekim
409 `job_running` ile reddedilir; eskiden yalnızca bu sürecin işi görülüyordu.

P13'ten beri uç nokta boru hattıyla (src/services/pipeline.py) ve kendi devre kesicisiyle çeker; rapor boru
hattının sonucudur (ItemResult.upstream_failure, aynı kural). Oturum ısınması (ana sayfa) istek sayılarına girmez.

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı gerçektir. Adım adım
istek sırası ve yazılan dosyalar tests/characterization/fixtures/fetch/single_match_route.golden.json'da durur.
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

import detail_records
from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from src import bridge_health
from src.exceptions import (
    APIError,
    CircuitOpenError,
    DataParsingError,
    NetworkError,
    RateLimitError,
    SofaScoreScraperError,
    StorageError,
)
from src.match_data_fetcher import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, MatchDataFetcher, SingleFetchReport, SliceOutcome
from src.web import upstream
from src.web.jobs import JobStore, default_db_path

ROOT = Path(__file__).resolve().parents[1]

# tests/characterization/fixtures/fetch/world.json: bitmiş ve bütün dilimleri dolu iki futbol maçı,
# pregame-form'u 404 ve kadrosu boş bir maç, başlamamış bir maç
COMPLETE = 9100001
COMPLETE_2 = 9100003
SPARSE = 9100002
NOT_STARTED = 9100004
UNKNOWN = 1
SLICE_KEYS = ["statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents"]
NOT_FETCHED = "Match data could not be fetched (may be unfinished or unavailable)."


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    bridge_health.reset()
    yield
    bridge_health.reset()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Boş bir veri dizini; ConfigManager.get_data_dir() onu döndürür."""
    path = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(path))
    return path


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def client(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Uygulama, çalışan işi olmayan geçici bir iş deposuyla."""
    yield from _client(JobStore(str(tmp_path / "jobs.db")), monkeypatch)


def _client(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import src.web.routes.matches as matches_routes
    from src.web.app import app

    monkeypatch.setattr(matches_routes, "_job_store", store)
    try:
        yield TestClient(app)
    finally:
        store.close()


def _fetch(client: TestClient, event_id: int) -> Any:
    return client.post(f"/api/matches/{event_id}/fetch")


def _stored(data_dir: Path, event_id: int) -> List[str]:
    """Saklanan yükler (Store; `event` maçın sayfası), ada göre; kayıt yoksa boş liste."""
    return detail_records.stored_slices(data_dir, event_id)


def _paths(fake: FakeSofaScore) -> List[str]:
    """SofaScore API'sine giden istekler, sırasıyla (oturum ısınması hariç)."""
    return [path for path in fake.paths() if path != SITE_ROOT]


def _typed(reason: str) -> Dict[str, Any]:
    return {"detail": upstream.detail(reason)}


# --- /event isteği reddedildi ----------------------------------------------------------------

@pytest.mark.parametrize(
    "status, http, reason",
    [
        (403, 502, upstream.BLOCKED),
        (429, 503, upstream.RATE_LIMITED),
        (503, 503, upstream.RATE_LIMITED),
        (500, 502, upstream.UPSTREAM),
    ],
)
def test_refused_event_request_answers_the_typed_reason(
    fake: FakeSofaScore, client: TestClient, data_dir: Path, status: int, http: int, reason: str
) -> None:
    fake.fail(f"/event/{COMPLETE}", status)

    response = _fetch(client, COMPLETE)

    assert response.status_code == http
    assert response.json() == _typed(reason)
    assert set(_paths(fake)) == {f"/event/{COMPLETE}"}  # dilim istenmedi
    assert _stored(data_dir, COMPLETE) == []  # hiçbir şey yazılmadı


def test_timeout_and_connection_failure_are_network(fake: FakeSofaScore, client: TestClient) -> None:
    fake.timeout(f"/event/{COMPLETE}")
    response = _fetch(client, COMPLETE)
    assert (response.status_code, response.json()) == (502, _typed(upstream.NETWORK))

    fake.clear_faults()
    fake.disconnect(f"/event/{COMPLETE}")
    response = _fetch(client, COMPLETE)
    assert (response.status_code, response.json()) == (502, _typed(upstream.NETWORK))


def test_unparseable_event_answer_is_upstream(fake: FakeSofaScore, client: TestClient) -> None:
    fake.fail(f"/event/{COMPLETE}", 200, body="<html>not json</html>")

    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (502, _typed(upstream.UPSTREAM))


def test_forbidden_because_the_browser_could_not_start_is_browser(fake: FakeSofaScore, client: TestClient) -> None:
    """403'ün nedeni challenge'ı çözecek tarayıcının açılamamasıysa lig aramasındaki gibi `browser` denir."""
    fake.fail(f"/event/{COMPLETE}", 403)
    fake.probe = lambda: bridge_health.record_failure(bridge_health.KIND_BROWSER, "chromium missing")

    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (502, _typed(upstream.BROWSER))


def test_stored_match_with_refused_event_is_not_requested_twice(
    fake: FakeSofaScore, client: TestClient, data_dir: Path
) -> None:
    """
    Diskteki maçta refill'in /event isteği reddedilirse tam çekim aynı isteği baştan atmaz. (P13'ten beri bitmemiş
    maçta da atmaz: goldende `stored_match_now_live` adımı.)
    """
    assert _fetch(client, COMPLETE).status_code == 200
    detail_records.drop_slices(data_dir, COMPLETE, "h2h")
    before = _stored(data_dir, COMPLETE)
    fake.reset_log()
    fake.fail(f"/event/{COMPLETE}", 403)

    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (502, _typed(upstream.BLOCKED))
    assert _paths(fake) == [f"/event/{COMPLETE}"] * 3  # istek katmanının üç denemesi, bir kez
    assert _stored(data_dir, COMPLETE) == before


# --- dilim istekleri reddedildi --------------------------------------------------------------

def test_every_slice_refused_answers_blocked_and_a_later_fetch_completes_the_match(
    fake: FakeSofaScore, client: TestClient, data_dir: Path
) -> None:
    fake.fail(f"/event/{COMPLETE}/*", 403)

    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (502, _typed(upstream.BLOCKED))
    # /event yanıt verdi: maçın kendisi yazılır, başarısız dilimler sayılmadan not edilir (bugünkü gibi)
    assert _stored(data_dir, COMPLETE) == ["event"]
    marks = detail_records.slice_marks(data_dir, COMPLETE)
    assert {key: mark["error"] for key, mark in marks.items()} == dict.fromkeys(SLICE_KEYS, "403")
    assert not any(mark["empty"] or mark["unverified"] for mark in marks.values())

    fake.clear_faults()
    fake.reset_log()
    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (200, {"status": "success", "match_id": str(COMPLETE)})
    assert len(_paths(fake)) == 1 + len(SLICE_KEYS)
    assert _stored(data_dir, COMPLETE) == sorted(["event", *SLICE_KEYS])


def test_refill_with_every_missing_slice_refused_answers_the_reason(
    fake: FakeSofaScore, client: TestClient, data_dir: Path
) -> None:
    assert _fetch(client, COMPLETE).status_code == 200
    detail_records.drop_slices(data_dir, COMPLETE, "h2h", "lineups")
    fake.reset_log()
    fake.fail(f"/event/{COMPLETE}/*", 429)

    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (503, _typed(upstream.RATE_LIMITED))
    assert set(_paths(fake)) == {f"/event/{COMPLETE}", f"/event/{COMPLETE}/h2h", f"/event/{COMPLETE}/lineups"}


def test_one_answered_slice_is_enough_for_success(fake: FakeSofaScore, client: TestClient, data_dir: Path) -> None:
    """Bir dilim bile saklandıysa yanıt bugünkü gibi "success"; reddedilenler _slice_status.json'da kalır."""
    for path in ("statistics", "team-streaks", "pregame-form", "lineups", "incidents"):
        fake.fail(f"/event/{COMPLETE}/{path}", 403)

    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (200, {"status": "success", "match_id": str(COMPLETE)})
    assert "h2h" in _stored(data_dir, COMPLETE) and "statistics" not in _stored(data_dir, COMPLETE)


def test_a_definitive_empty_answer_is_an_answer(fake: FakeSofaScore, client: TestClient) -> None:
    """404 (dilim yok) SofaScore'un yanıtıdır: geri kalan dilimler reddedilse de "hepsi reddedildi" değildir."""
    for path in ("statistics", "team-streaks", "h2h", "lineups", "incidents"):
        fake.fail(f"/event/{SPARSE}/{path}", 403)  # pregame-form bu maçta 404

    response = _fetch(client, SPARSE)

    assert (response.status_code, response.json()) == (200, {"status": "success", "match_id": str(SPARSE)})


# --- değişmeyenler ---------------------------------------------------------------------------

def test_unknown_matches_still_answer_404_and_unfinished_ones_are_stored(fake: FakeSofaScore,
                                                                        client: TestClient) -> None:
    response = _fetch(client, UNKNOWN)
    assert (response.status_code, response.json()) == (404, {"detail": NOT_FETCHED})
    # ST-27: bitmemiş maç da olduğu haliyle saklanır
    response = _fetch(client, NOT_STARTED)
    assert (response.status_code, response.json()) == (200, {"status": "success", "match_id": str(NOT_STARTED)})

    fake.add(f"/event/{COMPLETE}", {"error": "no event in this answer"})  # 200, ama içinde olay yok
    response = _fetch(client, COMPLETE)
    assert (response.status_code, response.json()) == (404, {"detail": NOT_FETCHED})


def test_a_storage_error_is_not_reported_as_an_upstream_block(
    fake: FakeSofaScore, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eskiden metninde "403" ya da "rate" geçen her hata 429 "SofaScore rate limit or block" oluyordu."""
    import src.services.pipeline as pipeline

    def fail_to_save(*args: Any, **kwargs: Any) -> None:
        raise StorageError("disk full: /data/17_Emirates_Cup/9100403")

    monkeypatch.setattr(pipeline, "put_retrying", fail_to_save)

    response = _fetch(client, COMPLETE)

    assert (response.status_code, response.json()) == (500, {"detail": "Match fetch failed"})


# --- neden eşlemesi: sonuç tipi ile hata sınıfı aynı nedeni verir ---------------------------------

@pytest.mark.parametrize(
    "error",
    [
        APIError("HTTP 403 Forbidden", status_code=403),
        RateLimitError(status_code=429, url="/event/1"),
        RateLimitError(status_code=503, url="/event/1"),
        APIError("HTTP 500 Internal Server Error", status_code=500),
        APIError("HTTP 400 Bad Request", status_code=400),
        NetworkError("İstek başarısız: /event/1: curl: (28) Operation timed out"),
        NetworkError("İstek başarısız: /event/1: curl: (7) Failed to connect"),
        DataParsingError("JSON ayrıştırma hatası"),
        CircuitOpenError("403"),
    ],
    ids=lambda error: f"{type(error).__name__}-{getattr(error, 'status_code', None)}",
)
def test_reason_of_an_outcome_matches_the_reason_of_its_error(error: SofaScoreScraperError) -> None:
    from src.web.routes.matches import _single_fetch_reason

    outcome = SliceOutcome.from_error(error)

    assert outcome.failed
    assert _single_fetch_reason(outcome, None) == upstream.reason_for(error, None)


# --- SingleFetchReport -----------------------------------------------------------------------

def _failed(reason: str, status: Optional[int] = None) -> SliceOutcome:
    return SliceOutcome(SLICE_FAILED, reason=reason, http_status=status)


def test_report_failure_rules() -> None:
    ok, empty = SliceOutcome(SLICE_OK, data={"x": 1}), SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
    blocked, limited = _failed("403", 403), _failed("429", 429)

    assert SingleFetchReport().upstream_failure() is None  # istek gönderilmedi
    assert SingleFetchReport(event=ok).upstream_failure() is None  # eksik dilim yoktu
    assert SingleFetchReport(event=empty).upstream_failure() is None  # maç yok: 404, engelleme değil
    assert SingleFetchReport(event=blocked).upstream_failure() is blocked
    assert SingleFetchReport(event=ok, slices={"a": blocked, "b": blocked}).upstream_failure() is blocked
    assert SingleFetchReport(event=ok, slices={"a": blocked, "b": ok}).upstream_failure() is None
    assert SingleFetchReport(event=ok, slices={"a": blocked, "b": empty}).upstream_failure() is None
    # Karışık nedenler: en sık görülen; eşitlikte ilk istenen
    assert SingleFetchReport(event=ok, slices={"a": limited, "b": blocked, "c": blocked}).upstream_failure() is blocked
    assert SingleFetchReport(event=ok, slices={"a": limited, "b": blocked}).upstream_failure() is limited


# --- çekici: rapor yalnızca istenirse tutulur ----------------------------------------------------

def _fetcher(data_dir: Path) -> MatchDataFetcher:
    from src.web.routes.common import config_manager

    return MatchDataFetcher(config_manager, data_dir=str(data_dir))


def test_fetcher_records_event_and_slice_outcomes(fake: FakeSofaScore, data_dir: Path) -> None:
    fetcher = _fetcher(data_dir)
    fake.fail(f"/event/{SPARSE}/statistics", 500)

    report = SingleFetchReport()
    data = fetcher.fetch_match_data(SPARSE, report=report)

    assert data is not None and report.event is not None
    assert report.event.status == SLICE_OK and report.event.data["id"] == SPARSE
    assert list(report.slices) == SLICE_KEYS  # tablo sırasıyla
    assert {key: (o.status, o.reason, o.http_status) for key, o in report.slices.items()} == {
        "statistics": (SLICE_FAILED, "5xx", 500),
        "team_streaks": (SLICE_OK, None, 200),
        "pregame_form": (SLICE_EMPTY, "404", 404),  # kesin "yok"; nedeni her yolda "404" (P13)
        "h2h": (SLICE_OK, None, 200),
        "lineups": (SLICE_EMPTY, "empty", 200),  # 200, ama içinde oyuncu yok
        "incidents": (SLICE_OK, None, 200),
    }

    fake.clear_faults()
    report = SingleFetchReport()
    assert fetcher.refill_missing_match_slices(SPARSE, report=report) is not None
    assert report.event is not None and report.event.status == SLICE_OK
    assert list(report.slices) == ["statistics", "pregame_form", "lineups"]  # yalnızca eksikler istendi

    report = SingleFetchReport()
    assert fetcher.fetch_match_data(UNKNOWN, report=report) is None
    assert report.event is not None and (report.event.status, report.event.reason) == (SLICE_EMPTY, "404")
    assert report.slices == {} and report.upstream_failure() is None
    # ST-27: bitmemiş maç saklanır; ön maç evresinde olmayan dilimler 404 döner ve sayılmaz
    report = SingleFetchReport()
    assert fetcher.fetch_match_data(NOT_STARTED, report=report) is not None
    assert report.event is not None and (report.event.status, report.event.reason) == (SLICE_OK, None)
    assert report.upstream_failure() is None


def test_without_a_report_the_same_pipeline_runs(fake: FakeSofaScore, data_dir: Path) -> None:
    """Rapor istenmese de aynı yol çalışır; reddedilen /event None'a iner."""
    fetcher = _fetcher(data_dir)

    assert fetcher.fetch_match_data(COMPLETE_2) is not None
    detail_records.drop_slices(data_dir, COMPLETE_2, "h2h")
    fake.reset_log()
    assert fetcher.refill_missing_match_slices(COMPLETE_2) is not None
    assert sorted(_paths(fake)) == [f"/event/{COMPLETE_2}", f"/event/{COMPLETE_2}/h2h"]

    fake.fail(f"/event/{COMPLETE}", 403)
    assert fetcher.fetch_match_data(COMPLETE) is None


# --- veri dizinine başka biri yazarken -----------------------------------------------------------

JOB_RUNNING = {
    "detail": {
        "code": "job_running",
        "message": "A download job is running; stop it or wait until it finishes, then try again.",
    }
}

# Başka bir süreç: veri dizininin bir kilidini cephe üzerinden alır (CLI indirmesinin yapacağı gibi), "ready"
# yazar ve stdin'den bir satır gelene kadar tutar
HOLDER = """
import sys
from src.store import open_store
lease = open_store(sys.argv[1]).lease(sys.argv[2], purpose=sys.argv[3])
print("ready", flush=True)
sys.stdin.readline()
lease.release()
"""


@contextlib.contextmanager
def _other_process(data_dir: Path, lease: str, purpose: str) -> Iterator[subprocess.Popen]:
    """Kilidi tutan ayrı bir süreç; blok bitince (öldürülmediyse) kilidi bırakıp temiz kapanır."""
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(data_dir), lease, purpose],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout is not None
        if proc.stdout.readline().strip() != "ready":
            proc.kill()
            pytest.fail(f"helper process did not start: {proc.communicate()[1]}")
        yield proc
    finally:
        if proc.poll() is None:
            _out, err = proc.communicate("\n", timeout=60)
            assert proc.returncode == 0, err
        else:
            proc.communicate()


@pytest.fixture
def job_store(data_dir: Path) -> JobStore:
    """Veri dizininin kendi iş deposu (üretimdeki gibi `.meta/state.db`): kilitleri o dizinin kilitleridir."""
    return JobStore(default_db_path(str(data_dir)))


@pytest.fixture
def shared_client(job_store: JobStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(job_store, monkeypatch)


def test_fetch_is_refused_while_another_process_holds_the_writer_lease(
    fake: FakeSofaScore, shared_client: TestClient, data_dir: Path
) -> None:
    with _other_process(data_dir, "writer", "headless"):
        response = _fetch(shared_client, COMPLETE)

        assert (response.status_code, response.json()) == (409, JOB_RUNNING)
        assert fake.requests == []  # SofaScore'a istek gitmedi
        assert _stored(data_dir, COMPLETE) == []

    response = _fetch(shared_client, COMPLETE)  # kilit bırakıldı

    assert (response.status_code, response.json()) == (200, {"status": "success", "match_id": str(COMPLETE)})


def test_fetch_proceeds_after_the_writing_process_is_killed(
    fake: FakeSofaScore, shared_client: TestClient, job_store: JobStore, data_dir: Path
) -> None:
    with _other_process(data_dir, "writer", "headless") as proc:
        assert _fetch(shared_client, COMPLETE).status_code == 409
        proc.kill()
        proc.wait(timeout=60)
        deadline = time.monotonic() + 20  # Windows kilidi hemen bırakmayabilir
        while job_store.writer_busy():
            assert time.monotonic() < deadline, "the writer lease of a killed process was not released"
            time.sleep(0.05)

        assert _fetch(shared_client, COMPLETE).status_code == 200


def test_fetch_is_refused_with_the_same_answer_while_a_job_runs_in_this_process(
    fake: FakeSofaScore, shared_client: TestClient, job_store: JobStore
) -> None:
    job_store.create_running({"mode": "full"})
    try:
        response = _fetch(shared_client, COMPLETE)
    finally:
        job_store.update(status="Completed", finished=True)

    assert (response.status_code, response.json()) == (409, JOB_RUNNING)
    assert fake.requests == []
    assert _fetch(shared_client, COMPLETE).status_code == 200


def test_a_watcher_in_another_process_does_not_block_the_fetch(
    fake: FakeSofaScore, shared_client: TestClient, data_dir: Path
) -> None:
    """İzleyici yazar değildir (`watcher:<spor>` ayrı bir kilittir): tek maç çekimi onunla birlikte çalışır."""
    with _other_process(data_dir, "watcher:football", "watch"):
        assert _fetch(shared_client, COMPLETE).status_code == 200
