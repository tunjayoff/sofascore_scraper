"""
Tek getirme boru hattı (sofascore_scraper/services/pipeline.py, plan maddesi P13; docs/design/02-services.md 3.3).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı ve Store gerçektir. Akışların
goldenları tests/characterization/test_fetch_flows.py'de, eski iki hattın farkları test_pipeline_divergence.py'dedir;
burada boru hattının kendi sözleşmesi sınanır: birimler, sonuçlar, yazıcı, devre kesici, iptal ve depolama hataları.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import errno
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest

import detail_records
from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sofascore_scraper import breaker as request_breaker
from sofascore_scraper.client.context import FetchCancelled, request_context
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.services import planning, pipeline
from sofascore_scraper.services.pipeline import FetchPipeline, ItemResult
from sofascore_scraper.services.query import RefreshPolicy
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from sofascore_scraper.store import Ref, Scope, StoreBusy, open_store

FINISHED = 9100001
EMPTY_SLICES = 9100002  # pregame-form 404, lineups boş
FINISHED_2 = 9100003
NOT_STARTED = 9100004
LIVE = 9300001
TENNIS = 9200001
FOOTBALL_KEYS = ["statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents"]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def store(tmp_path: Path) -> Any:
    return open_store(tmp_path / "data")


def _full(event_id: int) -> planning.WorkItem:
    return planning.WorkItem(Ref.event(event_id), "full", (), None, "test")


def _refill(event_id: int, *keys: str) -> planning.WorkItem:
    return planning.WorkItem(Ref.event(event_id), "refill", tuple((key, "") for key in keys), None, "test")


def _refresh(event_id: int) -> planning.WorkItem:
    return planning.WorkItem(Ref.event(event_id), "refresh", (), None, "test")


def _run(store: Any, items: List[planning.WorkItem], **kwargs: Any) -> pipeline.PipelineSummary:
    options: Dict[str, Any] = {"concurrency": 5}
    run_options = {key: kwargs.pop(key) for key in ("cancelled", "on_result") if key in kwargs}
    options.update(kwargs)
    return FetchPipeline(store, **options).run_sync(items, **run_options)


def _api(fake: FakeSofaScore) -> List[str]:
    return sorted(r.path for r in fake.requests if r.path != SITE_ROOT)


# --- birimler -------------------------------------------------------------------------------------------


def test_a_full_item_stores_the_event_and_every_selected_slice(fake: FakeSofaScore, store: Any) -> None:
    summary = _run(store, [_full(TENNIS)])

    [result] = summary.results
    assert result.ok and result.reason is None and result.payload["id"] == TENNIS
    # tablo sırasıyla; FX-16: teniste kadro ve olaylar istenmez
    assert list(result.slices) == [key for key in FOOTBALL_KEYS if key not in ("lineups", "incidents")] + [
        "point_by_point"]
    assert result.put is not None and result.put.created
    assert (summary.total, summary.ok, summary.failed) == (1, 1, 0)
    assert len(fake.sessions) == 1 and fake.requests[0].path == SITE_ROOT  # çalıştırma başına ısıtılmış bir oturum
    assert "point_by_point" in detail_records.stored_slices(store.data_dir, TENNIS)


def test_a_refill_item_requests_only_its_slices_and_rewrites_the_event(fake: FakeSofaScore, store: Any) -> None:
    _run(store, [_full(FINISHED)])
    detail_records.drop_slices(store.data_dir, FINISHED, "h2h")
    fake.reset_log()

    [result] = _run(store, [_refill(FINISHED, "h2h", "unknown_slice")]).results

    assert result.ok and list(result.slices) == ["h2h"]  # sporun seçiminde olmayan anahtar istenmez
    assert _api(fake) == [f"/event/{FINISHED}", f"/event/{FINISHED}/h2h"]
    assert "h2h" in detail_records.stored_slices(store.data_dir, FINISHED)


def test_a_refill_without_slices_only_reads_the_event_again(fake: FakeSofaScore, store: Any) -> None:
    _run(store, [_full(FINISHED)])
    fake.reset_log()

    [result] = _run(store, [_refill(FINISHED)]).results

    assert result.ok and result.slices == {} and _api(fake) == [f"/event/{FINISHED}"]


def test_a_refresh_records_the_change_and_announces_it_on_the_change_stream(fake: FakeSofaScore,
                                                                            store: Any) -> None:
    _run(store, [_full(FINISHED)])
    event = fake.event(FINISHED)
    event["homeScore"].update(current=5, display=5, normaltime=5)
    event["changes"]["changeTimestamp"] += 3600
    fake.add_event(event)
    fake.reset_log()

    summary = _run(store, [_refresh(FINISHED)])

    [result] = summary.results
    assert result.ok and result.slices == {} and _api(fake) == [f"/event/{FINISHED}"]
    assert result.changed["homeScore.current"][1] == 5
    assert (summary.refreshed, summary.changed) == (1, 1)
    [row] = store.changes.list(event_id=FINISHED)
    assert result.change_seq == row.seq
    events = store.streams.read(streams=["change"]).events
    assert [(e.type, e.event_id, dict(e.data)) for e in events] == [
        ("change.recorded", FINISHED, {"change_seq": row.seq})]
    assert events[0].tournament_id == 17 and events[0].sport == "football"


def test_an_unchanged_refresh_records_nothing(fake: FakeSofaScore, store: Any) -> None:
    _run(store, [_full(FINISHED)])

    summary = _run(store, [_refresh(FINISHED)])

    assert summary.results[0].ok and summary.results[0].changed == {} and summary.changed == 0
    assert store.changes.list(event_id=FINISHED) == []
    assert store.streams.read(streams=["change"]).events == ()


def test_a_refresh_of_an_unstored_event_sends_no_request(fake: FakeSofaScore, store: Any) -> None:
    [result] = _run(store, [_refresh(FINISHED)]).results

    assert result.failed and result.reason == pipeline.FAIL_NOT_STORED and fake.requests == []


def test_a_listing_only_or_unknown_event_is_failed_not_found(fake: FakeSofaScore, store: Any) -> None:
    [result] = _run(store, [_full(1)]).results

    assert result.failed and result.reason == pipeline.FAIL_NOT_FOUND
    assert result.event is not None and (result.event.status, result.event.reason) == (SLICE_EMPTY, "404")
    assert result.upstream_failure() is None  # "maç yok" bir yanıttır, engellenme değil


def test_an_answer_without_an_event_is_no_such_match(fake: FakeSofaScore, store: Any) -> None:
    fake.add("/event/42", {"error": {"code": 404}})

    [result] = _run(store, [_full(42)]).results

    assert result.failed and result.reason == pipeline.FAIL_NOT_FOUND
    assert (result.event.status, result.event.reason) == (SLICE_EMPTY, "empty")


@pytest.mark.parametrize("event_id", [NOT_STARTED, LIVE])
def test_an_unfinished_event_is_stored_as_it_is_now(fake: FakeSofaScore, store: Any, event_id: int) -> None:
    """ST-27: her durumdaki maç saklanır (planlayıcı karar verir; boru hattının `only_finished`ı 3.1'de kalktı)."""
    summary = _run(store, [_full(event_id)])

    [result] = summary.results
    assert result.ok and result.payload is not None
    assert summary.skipped == {} and summary.failed == 0
    stored = store.events.get(event_id)
    assert stored is not None and stored.has_event_payload
    assert stored.status_class == ("not_started" if event_id == NOT_STARTED else "live")
    assert detail_records.slice_marks(store.data_dir, event_id) == {}  # 404'ler sayılmaz


def test_an_unfinished_event_is_stored_with_the_slices_it_has(fake: FakeSofaScore, store: Any) -> None:
    [result] = _run(store, [_full(LIVE)]).results

    assert result.ok
    assert detail_records.stored_slices(store.data_dir, LIVE) == sorted(["event", "incidents", "lineups",
                                                                        "statistics"])
    assert detail_records.slice_marks(store.data_dir, LIVE) == {}  # 404'ler sayılmaz


def test_a_no_data_answer_of_an_unfinished_event_is_recorded_but_not_counted(fake: FakeSofaScore, store: Any
                                                                             ) -> None:
    """
    FX-27 V5: canlı maçta SofaScore'un "veri yok" dediği dilim "istenmedi" görünmüyordu (katalogda satırı yoktu).
    Artık sayılmayan bir kayıt açar (durum `empty`, sayaç 0): planlayıcı onu yine eksik sayar ve yeniden ister.
    """
    [result] = _run(store, [_full(LIVE)]).results
    no_data = sorted(name for name, o in result.slices.items() if o.status == SLICE_EMPTY and o.data is None)
    assert no_data  # sahte dünyada canlı maçın en az bir dilimi 404 verir
    for key in no_data:
        info = store.events.slice(LIVE, key)
        assert (info.state, info.empty_count, info.unverified_empty_count, info.has_payload) == ("empty", 0, 0, False)
    (state,) = store.events.states(Scope(event_ids=(LIVE,)))
    assert set(no_data) <= set(planning.wanted_slice_keys(state))


def test_empty_answers_are_counted_on_a_finished_event(fake: FakeSofaScore, store: Any) -> None:
    [result] = _run(store, [_full(EMPTY_SLICES)]).results

    assert result.slices["pregame_form"] == Outcome(SLICE_EMPTY, reason="404", http_status=404,
                                                    fetched_at=result.slices["pregame_form"].fetched_at,
                                                    via=result.slices["pregame_form"].via)
    assert result.slices["lineups"].status == SLICE_EMPTY and result.slices["lineups"].reason == "empty"
    marks = detail_records.slice_marks(store.data_dir, EMPTY_SLICES)
    assert {key: mark["empty"] for key, mark in marks.items()} == {"lineups": 1, "pregame_form": 1}


# --- gövde kuralı ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("body", "status", "reason"), [
    ({"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"name": "x"}]}]}]}, SLICE_OK, None),
    ({"statistics": []}, SLICE_EMPTY, "empty"),
    ({}, SLICE_EMPTY, "empty"),
    (0, SLICE_FAILED, "parse"),
    (False, SLICE_FAILED, "parse"),
    ("", SLICE_FAILED, "parse"),
    ({"statistics": "x"}, SLICE_FAILED, "parse"),
])
def test_an_answered_body_is_read_by_the_rule_of_its_slice(body: Any, status: str, reason: Optional[str]) -> None:
    for client_status in (SLICE_OK, SLICE_EMPTY):
        outcome = pipeline.answered_outcome("statistics", Outcome(client_status, data=body, http_status=200))
        assert (outcome.status, outcome.reason) == (status, reason)
        assert outcome.http_status == (None if status == SLICE_FAILED else 200)
        assert outcome.data == (None if status == SLICE_FAILED else body)


def test_a_malformed_slice_body_is_a_failed_request_that_is_not_stored(fake: FakeSofaScore, store: Any) -> None:
    fake.add(f"/event/{FINISHED}/statistics", {"statistics": "x"})

    [result] = _run(store, [_full(FINISHED)]).results

    outcome = result.slices["statistics"]
    assert result.ok and (outcome.status, outcome.reason) == (SLICE_FAILED, "parse")
    assert detail_records.slice_marks(store.data_dir, FINISHED)["statistics"] == {"empty": 0, "unverified": 0,
                                                                                 "error": "parse"}
    assert "statistics" not in detail_records.stored_slices(store.data_dir, FINISHED)


# --- devre kesici ve iptal --------------------------------------------------------------------------------


def _open_breaker() -> request_breaker.CircuitBreaker:
    breaker = request_breaker.CircuitBreaker(consecutive=lambda: 1, ratio=lambda: 1.0)
    breaker.record("403")
    assert breaker.tripped
    return breaker


def test_an_open_breaker_skips_every_item_without_a_request(fake: FakeSofaScore, store: Any) -> None:
    with request_context(breaker=_open_breaker()):
        summary = _run(store, [_full(FINISHED), _refresh(FINISHED_2)])

    assert [(r.status, r.reason) for r in summary.results] == [("skipped", "breaker")] * 2
    assert summary.breaker == "403" and fake.requests == [] and fake.sessions == []


def test_items_after_the_breaker_trips_are_skipped(fake: FakeSofaScore, store: Any) -> None:
    fake.fail("/event/*", 403)
    breaker = request_breaker.CircuitBreaker(consecutive=lambda: 1, ratio=lambda: 1.0)

    with request_context(breaker=breaker):
        summary = _run(store, [_full(FINISHED), _full(FINISHED_2), _full(EMPTY_SLICES)], concurrency=1)

    assert [(r.status, r.reason) for r in summary.results] == [
        ("failed", "403"), ("skipped", "breaker"), ("skipped", "breaker")]
    assert summary.breaker == "403"
    assert {r.path for r in fake.requests if r.path != SITE_ROOT} == {f"/event/{FINISHED}"}


def test_a_request_refused_by_an_open_breaker_is_a_skipped_slice(fake: FakeSofaScore, store: Any) -> None:
    """Dilimler uçuştayken devre kesilirse kalanlar `skipped` / `breaker` olur ve Store onları yok sayar."""
    breaker = request_breaker.CircuitBreaker(consecutive=lambda: 1, ratio=lambda: 1.0)
    fake.fail(f"/event/{FINISHED}/statistics", 403)

    with request_context(breaker=breaker):
        [result] = _run(store, [_full(FINISHED)]).results

    assert result.ok and breaker.tripped
    skipped = [key for key, outcome in result.slices.items() if outcome.status == SLICE_SKIPPED]
    assert all(result.slices[key].reason == "breaker" for key in skipped)
    marks = detail_records.slice_marks(store.data_dir, FINISHED)
    assert all(key not in marks for key in skipped)


def test_a_cancel_stops_before_the_next_item(fake: FakeSofaScore, store: Any) -> None:
    seen: List[ItemResult] = []
    summary = _run(store, [_full(FINISHED), _full(FINISHED_2)], concurrency=1,
                   cancelled=lambda: bool(seen), on_result=seen.append)

    assert summary.cancelled and [r.event_id for r in seen] == [FINISHED]
    assert f"/event/{FINISHED_2}" not in _api(fake)


def test_a_cancel_seen_by_the_request_layer_reaches_the_caller(fake: FakeSofaScore, store: Any) -> None:
    stop = threading.Event()
    fake.probe = lambda: stop.set()  # ilk istekten sonra iş durdurulur

    with request_context(cancel=stop.is_set), pytest.raises(FetchCancelled):
        _run(store, [_full(FINISHED), _full(FINISHED_2)], concurrency=1)

    assert len(fake.sessions) == 1  # oturum kapatıldı (sahte taşıyıcı açılışları kaydeder)


# --- yazıcı ---------------------------------------------------------------------------------------------


def test_writes_happen_on_one_writer_thread_off_the_event_loop(fake: FakeSofaScore, store: Any,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    threads: List[str] = []
    put = store.events.put

    def recording_put(*args: Any, **kwargs: Any) -> Any:
        threads.append(threading.current_thread().name)
        return put(*args, **kwargs)

    monkeypatch.setattr(store.events, "put", recording_put)
    _run(store, [_full(FINISHED), _full(FINISHED_2), _full(TENNIS)])

    assert len(threads) == 3 and len(set(threads)) == 1 and threads[0].startswith("pipeline-writer")


def test_the_writer_queue_is_bounded(fake: FakeSofaScore, store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yazıcı yavaşken en çok `writer_queue` yazma kuyruktadır: öteki işçiler sıra bekler, bellek büyümez."""
    counts = {"writing": 0, "queued": 0, "most_writing": 0, "most_queued": 0}
    lock = threading.Lock()
    release = threading.Event()
    put = store.events.put

    def slow_put(*args: Any, **kwargs: Any) -> Any:
        release.wait(5)
        return put(*args, **kwargs)

    def counter(name: str, original: Any) -> Any:
        async def counting(self: Any, *args: Any, **kwargs: Any) -> Any:
            with lock:
                counts[name] += 1
                counts["most_" + name] = max(counts["most_" + name], counts[name])
            try:
                return await original(self, *args, **kwargs)
            finally:
                with lock:
                    counts[name] -= 1
        return counting

    monkeypatch.setattr(store.events, "put", slow_put)
    monkeypatch.setattr(pipeline.FetchPipeline, "_write", counter("writing", pipeline.FetchPipeline._write))
    monkeypatch.setattr(pipeline.FetchPipeline, "_in_writer", counter("queued", pipeline.FetchPipeline._in_writer))
    timer = threading.Timer(0.3, release.set)
    timer.start()
    try:
        summary = _run(store, [_full(FINISHED), _full(FINISHED_2), _full(TENNIS), _full(EMPTY_SLICES)],
                       writer_queue=1)
    finally:
        timer.cancel()
        release.set()
    assert summary.ok == 4
    assert counts["most_writing"] >= 2 and counts["most_queued"] == 1


def test_a_busy_store_is_retried_with_growing_waits(fake: FakeSofaScore, store: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    put = store.events.put

    def busy_twice(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise StoreBusy("busy")
        return put(*args, **kwargs)

    monkeypatch.setattr(store.events, "put", busy_twice)
    [result] = _run(store, [_full(FINISHED)]).results

    assert result.ok and calls["n"] == 3
    assert fake.slept("sofascore_scraper.services.pipeline") == [pipeline.STORE_BUSY_FIRST_WAIT, 2 * pipeline.STORE_BUSY_FIRST_WAIT]


def test_a_store_that_stays_busy_fails_only_that_match(fake: FakeSofaScore, store: Any,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    put = store.events.put

    def busy_for_one(event_id: int, *args: Any, **kwargs: Any) -> Any:
        if event_id == FINISHED:
            raise StoreBusy("busy")
        return put(event_id, *args, **kwargs)

    monkeypatch.setattr(store.events, "put", busy_for_one)
    summary = _run(store, [_full(FINISHED), _full(FINISHED_2)], concurrency=1)

    assert [(r.status, r.reason) for r in summary.results] == [("failed", "storage"), ("ok", None)]
    assert isinstance(summary.results[0].error, StoreBusy) and not summary.results[0].error.fatal


def test_a_fatal_storage_error_stops_the_run_and_reaches_the_caller(fake: FakeSofaScore, store: Any,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    def full_disk(*args: Any, **kwargs: Any) -> Any:
        raise StorageError("disk full", path=str(store.data_dir), errno_code=errno.ENOSPC, detail="disk full")

    monkeypatch.setattr(store.events, "put", full_disk)
    seen: List[ItemResult] = []
    with pytest.raises(StorageError) as raised:
        _run(store, [_full(FINISHED), _full(FINISHED_2)], concurrency=1, on_result=seen.append)

    assert raised.value.fatal and [(r.event_id, r.reason) for r in seen] == [(FINISHED, "storage")]
    assert f"/event/{FINISHED_2}" not in _api(fake)


def test_an_unexpected_write_error_becomes_a_storage_error_of_that_match(fake: FakeSofaScore, store: Any,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    def rejects(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("rejected payload")

    monkeypatch.setattr(store.events, "put", rejects)
    [result] = _run(store, [_full(FINISHED)]).results

    assert result.failed and result.reason == "storage" and not result.error.fatal


def test_the_pipeline_fetches_events_only(fake: FakeSofaScore, store: Any) -> None:
    item = planning.WorkItem(Ref.season(17, 61627), "listing", (), None, "test")
    with pytest.raises(ValueError):
        _run(store, [item])


def test_an_empty_plan_opens_no_session(fake: FakeSofaScore, store: Any) -> None:
    summary = _run(store, [])
    assert summary.total == 0 and fake.requests == [] and fake.sessions == []


def test_run_works_inside_a_running_loop(fake: FakeSofaScore, store: Any) -> None:
    summary = asyncio.run(FetchPipeline(store, concurrency=2).run([_full(FINISHED)]))
    assert summary.ok == 1


# --- SofaScore engellediğinde -----------------------------------------------------------------------------


def _outcome(status: str, reason: Optional[str] = None) -> Outcome:
    return Outcome(status, reason=reason)


def test_upstream_failure_rule() -> None:
    event_failed = _outcome(SLICE_FAILED, "403")
    assert pipeline.upstream_failure(event_failed, {}) is event_failed
    assert pipeline.upstream_failure(_outcome(SLICE_OK), {}) is None
    both = {"a": _outcome(SLICE_FAILED, "429"), "b": _outcome(SLICE_FAILED, "403"), "c": _outcome(SLICE_FAILED, "403")}
    assert pipeline.upstream_failure(_outcome(SLICE_OK), both) is both["b"]
    tie = {"a": _outcome(SLICE_FAILED, "429"), "b": _outcome(SLICE_FAILED, "403")}
    assert pipeline.upstream_failure(_outcome(SLICE_OK), tie) is tie["a"]
    answered = {"a": _outcome(SLICE_FAILED, "403"), "b": _outcome(SLICE_EMPTY, "404")}
    assert pipeline.upstream_failure(_outcome(SLICE_OK), answered) is None
    with_breaker = {"a": _outcome(SLICE_FAILED, "403"), "b": _outcome(SLICE_SKIPPED, "breaker")}
    assert pipeline.upstream_failure(_outcome(SLICE_OK), with_breaker) is with_breaker["a"]
    only_skipped = {"a": _outcome(SLICE_SKIPPED, "breaker")}
    assert pipeline.upstream_failure(_outcome(SLICE_OK), only_skipped) is None


# --- planlama: iş birimleri -------------------------------------------------------------------------------


def _policy() -> RefreshPolicy:
    return RefreshPolicy(now=int(dt.datetime.now(dt.timezone.utc).timestamp()), window_s=0, min_interval_s=0,
                         include_unobserved=False)


def test_plan_items_orders_full_and_refill_before_refresh_and_drops_complete_matches(
        fake: FakeSofaScore, store: Any) -> None:
    _run(store, [_full(FINISHED), _full(FINISHED_2)])
    detail_records.drop_slices(store.data_dir, FINISHED_2, "h2h")
    needs = {FINISHED: "refresh"}  # işin önbelleğindeki karar kazanır

    items = planning.plan_items(store, [FINISHED, "007", 1, FINISHED_2, FINISHED_2], _policy(), needs=needs)

    assert [(item.owner.id, item.need, item.slices) for item in items] == [
        (1, "full", ()),
        (FINISHED_2, "refill", (("h2h", ""),)),
        (FINISHED, "refresh", ()),
    ]


def test_a_refill_item_also_asks_for_missing_optional_slices(fake: FakeSofaScore, store: Any) -> None:
    _run(store, [_full(TENNIS)])
    _run(store, [_full(TENNIS)])  # teniste olmayan dört dilim ikinci "yok" yanıtıyla artık beklenmez
    detail_records.drop_slices(store.data_dir, TENNIS, "h2h", "point_by_point")

    [item] = planning.plan_items(store, [TENNIS], _policy())

    assert (item.need, item.slices) == ("refill", (("h2h", ""), ("point_by_point", "")))


def test_a_cached_refill_of_a_vanished_record_becomes_a_full_fetch(store: Any) -> None:
    [item] = planning.plan_items(store, [FINISHED], _policy(), needs={FINISHED: "refill"})
    assert item.need == "full"


def test_a_noop_on_result_callback_still_counts(fake: FakeSofaScore, store: Any) -> None:
    calls: Dict[str, int] = {"n": 0}
    summary = _run(store, [_full(FINISHED)], on_result=lambda result: calls.__setitem__("n", calls["n"] + 1))
    assert calls["n"] == 1 and summary.ok == 1
