"""
src/store/streams.py ve src/store/watch.py: sıra numaralı olay akışları, sink konumları ve izleyici durumu
(docs/design/01-storage.md bölüm 2.3 ve 9.3; plan maddesi ST-18).

Süreçler arası testler gerçek ikinci süreçler başlatır (aynı state.db'ye ekleyen iki süreç, başka sürecin
eklemesiyle uyanan `wait`). Tümü çevrimdışı.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

import src.store
from src.store import (
    LayoutError,
    Store,
    StoreError,
    StreamBatch,
    StreamEvent,
    StreamHead,
    StreamLog,
    StreamRecord,
    WatchStateStore,
    open_store,
)
from src.store import api as api_mod
from src.store import legacy, streams, watch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = 86400

# Başka bir süreç: depoyu açar, "ready" yazar, stdin'den bir satır gelince `live` akışına N olay ekler ve
# aldığı sıra numaralarını JSON olarak yazar
APPENDER = """
import json, sys
from src.store import StreamEvent, open_store
store = open_store(sys.argv[1])
print("ready", flush=True)
sys.stdin.readline()
seqs = []
for n in range(int(sys.argv[3])):
    seqs += store.streams.append("live", [StreamEvent("live.test", {"who": sys.argv[2], "n": n})])
store.close()
print(json.dumps(seqs), flush=True)
"""


@pytest.fixture(autouse=True)
def _close_stores() -> Iterator[None]:
    """Testin açtığı depolar kayıt defterinde kalmasın."""
    yield
    for store in list(api_mod._registry.values()):
        store.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def store(data_dir: Path) -> Store:
    return open_store(data_dir)


@pytest.fixture
def log(store: Store) -> StreamLog:
    return store.streams


def start_process(script: str, *args: object) -> "subprocess.Popen[str]":
    """Betiği ayrı bir süreçte başlatır ve "ready" yazmasını bekler."""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, *map(str, args)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout is not None
    if proc.stdout.readline().strip() != "ready":
        proc.kill()
        pytest.fail(f"yardımcı süreç başlayamadı: {proc.communicate()[1]}")
    return proc


def finish_process(proc: "subprocess.Popen[str]", *, release: bool = True) -> str:
    """Sürece (istenirse) devam işaretini verir, bitmesini bekler ve çıktısını döndürür."""
    try:
        out, err = proc.communicate("\n" if release else None, timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    assert proc.returncode == 0, err
    return out


def live(n: int, **fields: Any) -> StreamEvent:
    return StreamEvent("live.test", {"n": n}, **fields)


def seqs_of(batch: StreamBatch) -> List[int]:
    return [event.seq for event in batch.events]


def rows(store: Store, sql: str, *params: Any) -> List[tuple]:
    return [tuple(row) for row in store._state.connection().execute(sql, params)]


# --- cephe ve açık adlar ----------------------------------------------------------------------------

def test_store_exposes_streams_and_watch(store: Store) -> None:
    assert isinstance(store.streams, StreamLog) and isinstance(store.watch, WatchStateStore)
    for name in ("StreamLog", "StreamEvent", "StreamRecord", "StreamBatch", "StreamHead", "WatchStateStore"):
        assert name in src.store.__all__
    assert streams.STREAMS == ("live", "change", "job", "system")


def test_legacy_file_names_equal_the_reader_constants() -> None:
    assert watch.LEGACY_EVENTS_FILE == legacy.WATCH_EVENTS_FILE
    assert legacy._WATCH_STATE_RE.fullmatch(watch.legacy_state_file("table-tennis")).group(1) == "table-tennis"


# --- ekleme ve okuma --------------------------------------------------------------------------------

def test_an_empty_log_has_a_stable_id_and_no_rows(data_dir: Path) -> None:
    store = open_store(data_dir)
    head = store.streams.head()
    assert isinstance(head, StreamHead) and (head.first_seq, head.last_seq) == (0, 0)
    assert len(head.stream_id) == 32 and int(head.stream_id, 16) >= 0
    batch = store.streams.read()
    assert batch == StreamBatch(stream_id=head.stream_id, events=(), last_seq=0, gap=False)
    store.close()
    assert open_store(data_dir).streams.head().stream_id == head.stream_id  # yalnızca state.db yenilenirse değişir


def test_append_returns_increasing_sequence_numbers_and_fields_round_trip(log: StreamLog) -> None:
    before = time.time()
    seqs = log.append("live", [
        StreamEvent("live.status_changed", {"from": "live", "to": "completed", "ad": "Beşiktaş"}, event_id=17,
                    sport="football", tournament_id=52, source="poll", dedup_key="17:status:1"),
        StreamEvent("live.stuck"),
    ])
    seqs += log.append("job", [StreamEvent("job.started", {"id": "j1"}, source="job", ts=1_790_000_000.25)])

    assert seqs == sorted(seqs) and len(set(seqs)) == 3 and all(isinstance(seq, int) for seq in seqs)
    first, second, third = log.read().events
    assert first == StreamRecord(
        seq=seqs[0], stream="live", ts=first.ts, type="live.status_changed",
        data={"from": "live", "to": "completed", "ad": "Beşiktaş"}, event_id=17, sport="football", tournament_id=52,
        source="poll", dedup_key="17:status:1")
    assert before - 0.01 <= first.ts <= time.time() + 0.01  # ts verilmezse ekleme anı
    assert (second.seq, second.stream, second.type, second.data) == (seqs[1], "live", "live.stuck", {})
    assert (second.event_id, second.sport, second.tournament_id, second.source, second.dedup_key) == (None,) * 5
    assert (third.stream, third.type, third.data, third.source, third.ts) == \
        ("job", "job.started", {"id": "j1"}, "job", 1_790_000_000.25)
    assert log.head() == StreamHead(stream_id=log.head().stream_id, first_seq=seqs[0], last_seq=seqs[2])


def test_append_of_nothing_writes_nothing(log: StreamLog) -> None:
    assert log.append("live", []) == []
    assert log.head().last_seq == 0


def test_a_duplicate_dedup_key_is_stored_once(log: StreamLog) -> None:
    first = log.append("live", [live(1, dedup_key="k"), live(2, dedup_key="k"), live(3)])
    second = log.append("live", [live(4, dedup_key="k"), live(5), live(6, dedup_key="other")])
    elsewhere = log.append("system", [live(7, dedup_key="k")])  # anahtar akış başınadır

    assert [seq is None for seq in first] == [False, True, False]
    assert [seq is None for seq in second] == [True, False, False]
    assert elsewhere[0] is not None
    stored = [(e.stream, e.data["n"]) for e in log.read().events]
    assert stored == [("live", 1), ("live", 3), ("live", 5), ("live", 6), ("system", 7)]
    kept = [seq for seq in first + second + elsewhere if seq is not None]
    assert kept == sorted(kept) == seqs_of(log.read())  # saklanmayan olay numara harcasa da sıra bozulmaz


def test_events_without_a_dedup_key_are_never_merged(log: StreamLog) -> None:
    assert None not in log.append("live", [live(1), live(1), live(1)])
    assert len(log.read().events) == 3


@pytest.mark.parametrize("stream", ["", "Live", "live events", "1live", None, 7, "a" * 41])
def test_invalid_stream_names_are_refused(log: StreamLog, stream: Any) -> None:
    with pytest.raises(StoreError):
        log.append(stream, [live(1)])
    with pytest.raises(StoreError):
        log.read(streams=[stream])
    assert log.head().last_seq == 0


@pytest.mark.parametrize("event", [
    StreamEvent(""),
    StreamEvent("live.x", {"when": object()}),
    StreamEvent("live.x", event_id="17"),  # type: ignore[arg-type]
    StreamEvent("live.x", event_id=True),
    StreamEvent("live.x", tournament_id=1.5),  # type: ignore[arg-type]
    StreamEvent("live.x", sport=5),  # type: ignore[arg-type]
    StreamEvent("live.x", dedup_key=5),  # type: ignore[arg-type]
    StreamEvent("live.x", ts=float("nan")),
    StreamEvent("live.x", ts="dün"),  # type: ignore[arg-type]
])
def test_an_invalid_event_fails_the_whole_append(log: StreamLog, event: StreamEvent) -> None:
    with pytest.raises(StoreError):
        log.append("live", [live(1), event, live(2)])
    assert log.read().events == () and log.head().last_seq == 0


def test_append_joins_an_outer_transaction(store: Store) -> None:
    with pytest.raises(RuntimeError):
        with store._state.write():
            assert store.streams.append("live", [live(1)])[0] is not None
            raise RuntimeError("dıştaki işlem geri alınır")
    assert store.streams.read().events == ()
    with store._state.write():
        store.streams.append("live", [live(2)])
        assert [e.data["n"] for e in store.streams.read().events] == [2]  # aynı işlemin içinden görünür
    assert [e.data["n"] for e in store.streams.read().events] == [2]


def test_stream_id_made_inside_a_rolled_back_transaction_is_not_remembered(data_dir: Path) -> None:
    store = open_store(data_dir)
    with pytest.raises(RuntimeError):
        with store._state.write():
            rolled_back = store.streams.head().stream_id
            raise RuntimeError("geri al")
    kept = store.streams.head().stream_id
    assert kept != rolled_back and store._state.meta_get("stream_id") == kept
    assert store.streams.read().stream_id == kept
    store.close()
    assert open_store(data_dir).streams.head().stream_id == kept


def test_read_after_resumes_exactly(log: StreamLog) -> None:
    expected: List[int] = []
    for n in range(57):
        expected += [seq for seq in log.append("live" if n % 3 else "job", [live(n)]) if seq is not None]

    for streams_filter, wanted in (((), expected), (("live",), [s for i, s in enumerate(expected) if i % 3]),
                                   (("live", "job"), expected)):
        seen: List[int] = []
        after = 0
        for _ in range(100):
            batch = log.read(after=after, limit=10, streams=streams_filter)
            assert batch.last_seq >= after and not batch.gap
            if not batch.events:
                break
            assert len(batch.events) <= 10 and all(seq > after for seq in seqs_of(batch))
            seen += seqs_of(batch)
            after = batch.last_seq
        assert seen == wanted  # hiçbiri atlanmadı, hiçbiri iki kez gelmedi
        assert after == expected[-1]
        assert log.read(after=after, streams=streams_filter).events == ()


def test_read_filters(log: StreamLog) -> None:
    log.append("live", [
        StreamEvent("live.status_changed", {"n": 1}, event_id=1, sport="football", tournament_id=17),
        StreamEvent("live.score_changed", {"n": 2}, event_id=1, sport="football", tournament_id=17),
        StreamEvent("live.score_changed", {"n": 3}, event_id=2, sport="tennis", tournament_id=2361),
        StreamEvent("live.stuck", {"n": 4}, event_id=3, sport="tennis"),
    ])
    log.append("job", [StreamEvent("job.started", {"n": 5}), StreamEvent("job.finished", {"n": 6})])
    log.append("system", [StreamEvent("system.blocked", {"n": 7}, sport="football")])

    def ns(**filters: Any) -> List[int]:
        return [event.data["n"] for event in log.read(**filters).events]

    assert ns() == [1, 2, 3, 4, 5, 6, 7]
    assert ns(streams=["live"]) == [1, 2, 3, 4]
    assert ns(streams=["job", "system"]) == [5, 6, 7]
    assert ns(streams=["job", "job"]) == [5, 6]
    assert ns(types=["live.score_changed", "job.finished"]) == [2, 3, 6]
    assert ns(types=["live.*"]) == []  # tam ad eşleşmesi; kalıpları sink'ler derler
    assert ns(event_ids=[1, 3]) == [1, 2, 4]
    assert ns(sport="football") == [1, 2, 7]
    assert ns(tournament_ids=[17, 2361]) == [1, 2, 3]
    assert ns(streams=["live"], types=["live.score_changed"], sport="tennis", event_ids=[2], tournament_ids=[2361]) == [3]
    assert ns(streams=["live"], sport="football", after=1) == [2]
    assert ns(streams=["system"], event_ids=[1]) == []


def test_last_seq_moves_past_rows_the_filter_skips(log: StreamLog) -> None:
    live_seq = log.append("live", [live(1)])[0]
    job_seqs = log.append("job", [live(n) for n in range(2, 6)])
    head = job_seqs[-1]

    batch = log.read(streams=["live"])
    assert seqs_of(batch) == [live_seq] and batch.last_seq == head  # kalan satırlar bu tüketicinin değil
    assert log.read(streams=["live"], after=batch.last_seq).last_seq == head
    assert log.read(streams=["job"], limit=2).last_seq == job_seqs[1]  # sınır doldu: son dönen satırda durur
    assert log.read(streams=["job"], limit=4).last_seq == job_seqs[3]
    assert log.read(after=head + 100).last_seq == head + 100  # `after`'dan geri gitmez


@pytest.mark.parametrize("arguments", [{"after": -1}, {"after": 1.5}, {"after": True}, {"limit": 0}, {"limit": -5},
                                       {"limit": None}])
def test_read_refuses_bad_positions(log: StreamLog, arguments: Dict[str, Any]) -> None:
    with pytest.raises(StoreError):
        log.read(**arguments)


def test_an_unreadable_payload_does_not_stop_the_consumer(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    first, second = store.streams.append("live", [live(1), live(2)])
    with store._state.write() as conn:
        conn.execute("UPDATE stream_events SET payload_json = '{yarım' WHERE seq = ?", (first,))
    with caplog.at_level("WARNING", logger="Store"):
        batch = store.streams.read()
    assert [(e.seq, e.data) for e in batch.events] == [(first, {}), (second, {"n": 2})]
    assert f"Stream event {first} has an unreadable payload" in caplog.text


def test_single_stream_read_uses_the_stream_index_and_state_db_has_no_statistics(store: Store) -> None:
    """Bölüm 3.7: tek akışlı okuma `stream_events_stream` dizinini kullanır; bu yalnızca ANALYZE çalıştırılmadıkça doğrudur."""
    conn = store._state.connection()
    for n in range(300):
        store.streams.append(("live", "job", "system")[n % 3], [live(n)])
    statements: List[str] = []
    conn.set_trace_callback(statements.append)
    try:
        store.streams.read(streams=["live"], after=10)
        store.streams.read(after=10)
        store.streams.read(streams=["live", "job"], after=10)
    finally:
        conn.set_trace_callback(None)
    selects = [s for s in statements if s.startswith("SELECT seq, stream")]
    assert len(selects) == 3

    def plan(sql: str) -> str:
        # İz, Python sürümüne göre değerleri yerine konmuş ya da "?" ile gelir: ikincisinde boş değer bağlanır
        return " | ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, [0] * sql.count("?")))

    assert "USING INDEX stream_events_stream (stream=? AND seq>?)" in plan(selects[0])
    for sql in selects[1:]:  # akış süzgeci yok ya da birden çok akış: rowid sırasıyla, sıralama adımı olmadan
        assert "USING INTEGER PRIMARY KEY (rowid>?)" in plan(sql) and "TEMP B-TREE" not in plan(sql)
    assert rows(store, "SELECT count(*) FROM sqlite_master WHERE name LIKE 'sqlite_stat%'") == [(0,)]


# --- süreçler arası sıra ----------------------------------------------------------------------------

def test_seq_is_strictly_increasing_across_two_appending_processes(store: Store, data_dir: Path) -> None:
    per_process = 40
    procs = [start_process(APPENDER, data_dir, who, per_process) for who in ("a", "b")]
    for proc in procs:  # ikisi de hazır: aynı anda başlasınlar
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.flush()

    # Bu süreç de ekler ve bir yandan günlüğü izler: izleyen, eklenen her satırı tam bir kez ve sırayla görmeli
    followed: List[int] = []
    own: List[int] = []
    after = 0
    deadline = time.monotonic() + 120
    while any(proc.poll() is None for proc in procs):
        assert time.monotonic() < deadline
        if len(own) < per_process:
            own += [seq for seq in store.streams.append("live", [live(len(own), source="main")]) if seq is not None]
        batch = store.streams.read(after=after, limit=7)
        followed += seqs_of(batch)
        after = batch.last_seq
    reported = [json.loads(finish_process(proc, release=False)) for proc in procs]
    while True:
        batch = store.streams.read(after=after, limit=7)
        if not batch.events:
            break
        followed += seqs_of(batch)
        after = batch.last_seq

    everything = store.streams.read(limit=10_000).events
    all_seqs = [event.seq for event in everything]
    assert len(all_seqs) == 2 * per_process + len(own)
    assert all(a < b for a, b in zip(all_seqs, all_seqs[1:], strict=False))  # kesin artan, yinelenen yok
    assert followed == all_seqs  # okuyan, daha küçük numaralı bir satır kaydedilmeden büyüğünü görmedi
    for who, seqs in zip(("a", "b"), reported, strict=True):
        assert len(seqs) == per_process and seqs == sorted(seqs)
        mine = [event for event in everything if event.data.get("who") == who]
        assert [event.seq for event in mine] == seqs and [event.data["n"] for event in mine] == list(range(per_process))
    assert sorted(reported[0] + reported[1] + own) == all_seqs
    assert set(reported[0]) & set(reported[1]) == set()


# --- wait -------------------------------------------------------------------------------------------

def test_wait_returns_at_once_when_a_newer_row_exists_and_times_out_otherwise(log: StreamLog) -> None:
    seq = log.append("live", [live(1)])[0]
    assert seq is not None
    assert log.wait(after=seq - 1, timeout=0) is True
    assert log.wait(after=0, timeout=30) is True
    started = time.monotonic()
    assert log.wait(after=seq, timeout=0.3) is False
    assert 0.25 <= time.monotonic() - started < 5
    assert log.wait(after=seq, timeout=0) is False
    assert log.wait(after=seq, timeout=-1) is False


def test_wait_wakes_for_an_event_appended_by_another_thread(store: Store) -> None:
    after = store.streams.append("live", [live(1)])[0]
    result: Dict[str, Any] = {}

    def waiter() -> None:
        started = time.monotonic()
        result["woke"] = store.streams.wait(after=after, timeout=60)
        result["took"] = time.monotonic() - started

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive()  # satır yokken bekliyor
    store.streams.append("job", [live(2)])  # hangi akışa eklendiği fark etmez: sıra tektir
    thread.join(60)
    assert not thread.is_alive() and result["woke"] is True and result["took"] < 30


def test_wait_wakes_for_an_event_appended_by_another_process(store: Store, data_dir: Path) -> None:
    after = store.streams.append("live", [live(1)])[0]
    proc = start_process(APPENDER, data_dir, "other", 1)
    result: Dict[str, Any] = {}

    def waiter() -> None:
        result["woke"] = store.streams.wait(after=after, timeout=120)

    thread = threading.Thread(target=waiter)
    thread.start()
    try:
        time.sleep(0.5)
        assert thread.is_alive()  # öteki süreç henüz eklemedi
        appended = json.loads(finish_process(proc))
    finally:
        thread.join(120)
    assert not thread.is_alive() and result["woke"] is True
    assert appended[0] > after and seqs_of(store.streams.read(after=after)) == appended


def test_wait_is_not_woken_by_other_writes_to_state_db(store: Store) -> None:
    after = store.streams.append("live", [live(1)])[0]
    result: Dict[str, Any] = {}
    thread = threading.Thread(target=lambda: result.setdefault("woke", store.streams.wait(after=after, timeout=1.0)))
    thread.start()
    for n in range(5):  # başka tablolara yazılanlar yoklamayı tetikler ama bekleyeni uyandırmaz
        store.runtime.set("bridge_health", {"n": n})
        time.sleep(0.05)
    thread.join(30)
    assert result["woke"] is False


# --- budama -----------------------------------------------------------------------------------------

def test_prune_by_age_reports_a_gap_to_a_consumer_behind_the_cut(log: StreamLog) -> None:
    now = time.time()
    old = log.append("live", [live(n, ts=now - 10 * DAY) for n in range(3)])
    fresh = log.append("live", [live(n, ts=now - 1 * DAY) for n in range(3, 6)])
    assert not log.read(after=0).gap

    assert log.prune(max_age_s=7 * DAY) == 3

    assert seqs_of(log.read()) == fresh
    assert log.read(after=0).gap is True  # hiç okumamış tüketici
    assert log.read(after=old[1]).gap is True  # kesimin gerisinde kalmış tüketici
    assert log.read(after=old[2]).gap is False  # silinen son satırı okumuş tüketici
    assert log.read(after=fresh[0]).gap is False
    assert log.head().first_seq == fresh[0] and log.head().last_seq == fresh[-1]
    assert log.prune(max_age_s=7 * DAY) == 0  # ikinci kez silinecek bir şey yok
    assert log.read(after=old[1]).gap is True  # iz kalıcıdır


def test_prune_by_row_count_keeps_the_newest_rows(log: StreamLog) -> None:
    seqs = log.append("live", [live(n) for n in range(10)])

    assert log.prune(max_rows=20) == 0 and not log.read().gap
    assert log.prune(max_rows=4) == 6
    assert seqs_of(log.read()) == seqs[6:]
    assert log.read(after=seqs[4]).gap is True and log.read(after=seqs[5]).gap is False
    assert log.prune(max_age_s=DAY, max_rows=4) == 0
    assert log.prune() == 0


def test_gap_is_tracked_per_stream(log: StreamLog) -> None:
    now = time.time()
    job_old = log.append("job", [live(1, ts=now - 30 * DAY)])[0]
    live_seq = log.append("live", [live(2)])[0]
    assert log.prune(max_age_s=7 * DAY) == 1

    assert log.read(after=0, streams=["live"]).gap is False  # `live` akışından hiçbir şey silinmedi
    assert log.read(after=0, streams=["job"]).gap is True
    assert log.read(after=0, streams=["live", "job"]).gap is True
    assert log.read(after=0).gap is True  # süzgeçsiz okuma bütün akışları izler
    assert log.read(after=job_old).gap is False and seqs_of(log.read(after=job_old)) == [live_seq]


def test_sequence_numbers_are_not_reused_after_pruning_everything(data_dir: Path) -> None:
    store = open_store(data_dir)
    seqs = store.streams.append("live", [live(n) for n in range(5)])
    assert store.streams.prune(max_rows=0) == 5
    head = store.streams.head()
    assert (head.first_seq, head.last_seq) == (0, seqs[-1])  # boş günlükte de son numara bilinir
    batch = store.streams.read(after=seqs[1])
    assert (batch.events, batch.last_seq, batch.gap) == ((), seqs[-1], True)
    assert store.streams.wait(after=seqs[-1], timeout=0) is False
    store.close()

    reopened = open_store(data_dir)
    assert reopened.streams.append("live", [live(9)])[0] > seqs[-1]
    assert reopened.streams.read(after=seqs[1]).gap is True  # budama izi state.db'dedir


@pytest.mark.parametrize("arguments", [{"max_age_s": -1}, {"max_rows": -1}, {"max_rows": 1.5}, {"max_rows": True}])
def test_prune_refuses_bad_limits(log: StreamLog, arguments: Dict[str, Any]) -> None:
    log.append("live", [live(1)])
    with pytest.raises(StoreError):
        log.prune(**arguments)
    assert len(log.read().events) == 1


# --- sink konumları ---------------------------------------------------------------------------------

def test_cursor_and_set_cursor_round_trip(data_dir: Path) -> None:
    store = open_store(data_dir)
    log = store.streams
    assert log.cursor("webhook:ops") == 0  # kayıt yok
    before = int(time.time())
    log.set_cursor("webhook:ops", 41)
    log.set_cursor("file", 7, error="disk dolu")
    assert (log.cursor("webhook:ops"), log.cursor("file"), log.cursor("stdout")) == (41, 7, 0)
    assert rows(store, "SELECT sink, seq, last_error FROM sink_cursors ORDER BY sink") == \
        [("file", 7, "disk dolu"), ("webhook:ops", 41, None)]
    assert all(before <= row[0] <= int(time.time()) for row in rows(store, "SELECT updated_at FROM sink_cursors"))

    log.set_cursor("file", 7)  # başarılı teslim önceki hatayı siler
    log.set_cursor("webhook:ops", 41, error="HTTP 503")  # konum ilerlemeden hata yazılabilir
    log.set_cursor("webhook:ops", 12, error=None)  # geri almak da serbesttir (yeniden gönderim)
    assert rows(store, "SELECT sink, seq, last_error FROM sink_cursors ORDER BY sink") == \
        [("file", 7, None), ("webhook:ops", 12, None)]
    store.close()
    assert open_store(data_dir).streams.cursor("webhook:ops") == 12


@pytest.mark.parametrize("sink,seq", [("", 1), (None, 1), ("file", -1), ("file", 1.5), ("file", True)])
def test_set_cursor_refuses_bad_values(log: StreamLog, sink: Any, seq: Any) -> None:
    with pytest.raises(StoreError):
        log.set_cursor(sink, seq)
    assert log.cursor("file") == 0


def test_a_consumer_resumes_from_its_cursor(log: StreamLog) -> None:
    seqs = log.append("live", [live(n) for n in range(6)])
    delivered: List[int] = []
    for _ in range(3):  # her turda en çok iki olay teslim edilir, konum ancak teslimden sonra ilerler
        batch = log.read(after=log.cursor("stdout"), limit=2)
        delivered += seqs_of(batch)
        log.set_cursor("stdout", batch.last_seq)
    assert delivered == seqs and log.cursor("stdout") == seqs[-1]
    assert log.read(after=log.cursor("stdout")).events == ()


# --- izleyici durumu --------------------------------------------------------------------------------

class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def updated_at(store: Store, watcher: str) -> Dict[int, int]:
    return dict(rows(store, "SELECT event_id, updated_at FROM watch_state WHERE watcher = ?", watcher))


def test_watch_state_round_trip_keeps_watchers_apart(data_dir: Path) -> None:
    store = open_store(data_dir)
    assert store.watch.load("football") == {}
    football = {"500": {"class": "live", "done": False, "score": [1, 0], "start_ts": 1790000000, "ad": "Göztepe"},
                "7": {"done": True, "class": "completed"}}
    store.watch.save("football", football)
    store.watch.save("tennis", {"600": {"class": "not_started", "done": False}})

    loaded = store.watch.load("football")
    assert loaded == football and list(loaded) == ["7", "500"]  # maç id'si sırasıyla
    assert list(loaded["500"]) == list(football["500"])  # satırın anahtar sırası korunur
    assert store.watch.load("tennis") == {"600": {"class": "not_started", "done": False}}
    assert store.watch.load("basketball") == {}
    store.close()
    assert open_store(data_dir).watch.load("football") == football


def test_full_save_replaces_the_state_and_rewrites_only_what_differs(store: Store) -> None:
    clock = FakeClock(1_000_000)
    watch_store = WatchStateStore(store, clock=clock)
    watch_store.save("football", {"1": {"class": "live"}, "2": {"class": "live"}, "3": {"class": "live"}})
    clock.now += 60
    watch_store.save("football", {"1": {"class": "live"}, "2": {"class": "completed", "done": True}, 4: {"class": "live"}})

    assert watch_store.load("football") == {"1": {"class": "live"}, "2": {"class": "completed", "done": True},
                                            "4": {"class": "live"}}
    assert updated_at(store, "football") == {1: 1_000_000, 2: 1_000_060, 4: 1_000_060}
    watch_store.save("football", {})
    assert watch_store.load("football") == {}


def test_save_with_changed_touches_only_those_rows(store: Store) -> None:
    clock = FakeClock(1_000_000)
    watch_store = WatchStateStore(store, clock=clock)
    watch_store.save("football", {"1": {"class": "live"}, "2": {"class": "live"}, "3": {"class": "live"}})
    watch_store.save("tennis", {"2": {"class": "live"}})
    clock.now += 60
    # 1 değişti, 3 artık durumda yok; 2 durumda yok ama `changed`te de değil: dokunulmaz
    watch_store.save("football", {"1": {"class": "void"}, "9": {"class": "live"}}, changed=["1", "3", "3"])

    assert watch_store.load("football") == {"1": {"class": "void"}, "2": {"class": "live"}}
    assert updated_at(store, "football") == {1: 1_000_060, 2: 1_000_000}
    assert watch_store.load("tennis") == {"2": {"class": "live"}}
    watch_store.save("football", {"1": {"class": "void"}}, changed=[])
    assert updated_at(store, "football") == {1: 1_000_060, 2: 1_000_000}


def test_finished_events_are_dropped_after_the_retention(store: Store) -> None:
    clock = FakeClock(1_000_000)
    watch_store = WatchStateStore(store, clock=clock)
    state = {"1": {"class": "completed", "done": True}, "2": {"class": "live", "done": False},
             "3": {"class": "void", "done": True}}
    watch_store.save("football", state)
    watch_store.save("tennis", {"1": {"class": "completed", "done": True}})

    clock.now += 7 * DAY  # tam yedi gün: henüz eski değil
    watch_store.save("football", state, changed=[])
    assert set(watch_store.load("football")) == {"1", "2", "3"}

    clock.now += 1
    state["3"] = {"class": "live", "done": False}  # 3 yeniden canlı: bu çağrıda yazılır
    watch_store.save("football", state, changed=["3"])
    assert watch_store.load("football") == {"2": {"class": "live", "done": False}, "3": {"class": "live", "done": False}}
    assert set(watch_store.load("tennis")) == {"1"}  # başka izleyicinin satırlarına dokunulmaz

    kept_forever = WatchStateStore(store, retention_s=None, clock=clock)
    clock.now += 365 * DAY
    kept_forever.save("tennis", {}, changed=[])
    assert set(kept_forever.load("tennis")) == {"1"}
    watch_store.save("tennis", {"1": {"class": "completed", "done": True}})  # tam kayıt da eski satırı bırakır
    assert watch_store.load("tennis") == {}
    assert watch.WATCH_STATE_RETENTION_SECONDS == 7 * DAY


@pytest.mark.parametrize("state", [
    {"abc": {"class": "live"}},
    {"1.5": {"class": "live"}},
    {"": {"class": "live"}},
    {True: {"class": "live"}},
    {"1": ["class", "live"]},
    {"1": {"when": object()}},
])
def test_an_invalid_state_writes_nothing(store: Store, state: Dict[Any, Any]) -> None:
    store.watch.save("football", {"5": {"class": "live"}})
    with pytest.raises(StoreError):
        store.watch.save("football", {"6": {"class": "live"}, **state})
    with pytest.raises(StoreError):
        store.watch.save("football", {"6": {"class": "live"}, **state}, changed=list(state) + ["6"])
    with pytest.raises(StoreError):
        store.watch.save("", {"6": {"class": "live"}})
    assert store.watch.load("football") == {"5": {"class": "live"}}


def test_an_unreadable_row_is_skipped_on_load(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    store.watch.save("football", {"1": {"class": "live"}, "2": {"class": "live"}})
    with store._state.write() as conn:
        conn.execute("UPDATE watch_state SET state_json = '[1' WHERE event_id = 1")
    with caplog.at_level("WARNING", logger="Store"):
        assert store.watch.load("football") == {"2": {"class": "live"}}
    assert "Watch state of event 1" in caplog.text
    store.watch.save("football", {"1": {"class": "void"}, "2": {"class": "live"}})  # üzerine yazılır
    assert store.watch.load("football")["1"] == {"class": "void"}


# --- 2.x dosyaları ----------------------------------------------------------------------------------

def legacy_state_path(data_dir: Path, sport: str) -> Path:
    return data_dir / f"watch_state_{sport}.json"


def import_record(store: Store, sport: str) -> Dict[str, Any]:
    return json.loads(store._state.meta_get(f"imported_watch_state:{sport}") or "null")


def test_legacy_state_file_is_imported_once(store: Store, data_dir: Path) -> None:
    old = {"500": {"class": "live", "done": False, "score": [1, 0]}, "7": {"class": "completed", "done": True}}
    legacy_state_path(data_dir, "football").write_text(json.dumps(old, indent=2), encoding="utf-8")

    assert store.watch.import_legacy("football") == 2
    assert store.watch.load("football") == old
    record = import_record(store, "football")
    assert {k: record[k] for k in ("file", "watcher", "found", "rows", "imported")} == \
        {"file": "watch_state_football.json", "watcher": "football", "found": True, "rows": 2, "imported": 2}

    # Dosya sonradan değişse de (ör. 2.x'e dönülüp yazıldı) bir daha alınmaz: yetkili olan tablodur
    legacy_state_path(data_dir, "football").write_text(json.dumps({"9": {"class": "live"}}), encoding="utf-8")
    store.watch.save("football", {"500": {"class": "completed", "done": True}}, changed=["500"])
    assert store.watch.import_legacy("football") is None
    assert store.watch.load("football") == {"7": old["7"], "500": {"class": "completed", "done": True}}
    assert legacy_state_path(data_dir, "football").exists()  # dosya silinmez


def test_legacy_import_keeps_rows_the_store_already_has(store: Store, data_dir: Path) -> None:
    legacy_state_path(data_dir, "tennis").write_text(
        json.dumps({"1": {"class": "live"}, "2": {"class": "live"}}), encoding="utf-8")
    store.watch.save("court", {"1": {"class": "completed", "done": True}})

    assert store.watch.import_legacy("tennis", watcher="court") == 1
    assert store.watch.load("court") == {"1": {"class": "completed", "done": True}, "2": {"class": "live"}}
    assert store.watch.load("tennis") == {}
    assert import_record(store, "tennis")["watcher"] == "court"


def test_a_missing_legacy_state_file_is_recorded_too(store: Store, data_dir: Path) -> None:
    assert store.watch.import_legacy("football") == 0
    assert import_record(store, "football")["found"] is False
    legacy_state_path(data_dir, "football").write_text(json.dumps({"1": {"class": "live"}}), encoding="utf-8")
    assert store.watch.import_legacy("football") is None  # sonradan beliren dosya alınmaz
    assert store.watch.load("football") == {}
    assert store.watch.import_legacy("tennis") == 0  # her sporun kaydı ayrıdır


@pytest.mark.parametrize("content,rows_seen,imported", [
    (b"{", 0, {}),  # yarım dosya: 2.x izleyicisi de boş durumla başlardı
    (b"[1, 2]", 0, {}),
    (b"\xff\xfe", 0, {}),
    (json.dumps({"5": {"class": "live"}, "x": {"class": "live"}, "6": [1], "7": {"done": True}}).encode(), 4,
     {"5": {"class": "live"}, "7": {"done": True}}),
])
def test_damaged_legacy_state_is_imported_as_far_as_it_is_usable(
        store: Store, data_dir: Path, content: bytes, rows_seen: int, imported: Dict[str, Any]) -> None:
    legacy_state_path(data_dir, "football").write_bytes(content)
    assert store.watch.import_legacy("football") == len(imported)
    assert store.watch.load("football") == imported
    record = import_record(store, "football")
    assert (record["found"], record["rows"], record["imported"]) == (True, rows_seen, len(imported))
    assert store.watch.import_legacy("football") is None


def test_an_unreadable_legacy_state_file_is_tried_again(store: Store, data_dir: Path) -> None:
    legacy_state_path(data_dir, "football").mkdir()  # okunamıyor (dizin): kayıt yazılmaz
    assert store.watch.import_legacy("football") == 0
    assert store._state.meta_get("imported_watch_state:football") is None
    legacy_state_path(data_dir, "football").rmdir()
    legacy_state_path(data_dir, "football").write_text(json.dumps({"1": {"class": "live"}}), encoding="utf-8")
    assert store.watch.import_legacy("football") == 1


def test_mirror_writes_the_state_file_in_the_2x_format(store: Store, data_dir: Path) -> None:
    state = {"500": {"class": "live", "done": False, "ad": "Fenerbahçe", "score": [1, None]}, "7": {"done": True}}
    store.watch.mirror_legacy_state("football", state)
    path = legacy_state_path(data_dir, "football")
    assert path.read_bytes() == json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    store.watch.mirror_legacy_state("football", {})
    assert path.read_bytes() == b"{}"
    assert sorted(p.name for p in data_dir.iterdir()) == [".meta", "watch_state_football.json"]  # geçici dosya kalmaz
    with pytest.raises(StoreError):
        store.watch.mirror_legacy_state("football", {"1": {"when": object()}})
    assert path.read_bytes() == b"{}"


@pytest.mark.parametrize("sport", ["", ".", "..", "../x", "a/b", "a\\b", "a\0b", None, 5])
def test_legacy_files_refuse_a_sport_that_is_not_a_file_name(store: Store, data_dir: Path, sport: Any) -> None:
    with pytest.raises(LayoutError):
        store.watch.mirror_legacy_state(sport, {})
    with pytest.raises(LayoutError):
        store.watch.import_legacy(sport)
    assert sorted(p.name for p in data_dir.iterdir()) == [".meta"]


def test_legacy_event_lines_are_appended_as_utf8_with_lf(store: Store, data_dir: Path) -> None:
    path = data_dir / "watch_events.jsonl"
    store.watch.append_legacy_events([])
    assert not path.exists()
    store.watch.append_legacy_events(['{"type": "stuck", "ad": "Gençlerbirliği"}'])
    store.watch.append_legacy_events(['{"n": 2}', '{"n": 3}'])
    assert path.read_bytes() == '{"type": "stuck", "ad": "Gençlerbirliği"}\n{"n": 2}\n{"n": 3}\n'.encode("utf-8")
    path.unlink()
    path.mkdir()
    with pytest.raises(StoreError):
        store.watch.append_legacy_events(["{}"])
