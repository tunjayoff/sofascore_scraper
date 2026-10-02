"""
Sink'ler (plan maddesi P22; docs/design/02-services.md bölüm 5): zarf, süzgeç, stdout ve dosya sink'leri,
dağıtıcı (konumlar, yeniden deneme, yaş sınırı, kilit, tek seferlik boşaltma), yapılandırmadan kurulum ve
`ssc events` komutu. Tümü çevrimdışı; zamana bağlı her şey sahte saatle sınanır.

Webhook'un HTTP sözleşmesi (yerel bir sunucuya karşı) tests/test_webhook_contract.py'dedir.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import math
import os
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterator, List, Optional, Sequence

import pytest

import conftest
import test_cli_skeleton as skeleton
from src import sinks
from src.cli.commands import events as events_command
from src.config import SinkSpec, loader
from src.exceptions import ConfigError
from src.jobs.manager import JobManager, local_origin
from src.jobs.model import JobKind
from src.sinks import base, dispatcher as dispatcher_mod
from src.sinks.base import BaseSink, Envelope, EventFilter, FatalSinkError, RetryableSinkError, Sink
from src.sinks.dispatcher import Dispatcher, DrainReport
from src.sinks.file import FileSink
from src.sinks.stdout import StdoutSink
from src.sinks.webhook import WebhookSink
from src.store import JobStore, Store, StoreBusy, StreamEvent, open_store
from src.store import streams as store_streams
from test_cli_skeleton import CliRunner

# `main()`i bu süreçte çalıştıran ve süreçteki izlerini geri alan fixture (tests/test_cli_skeleton.py)
cli = skeleton.cli
box = skeleton.box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = 1_790_000_000.0  # sahte saatin başlangıcı (2026-09-21)
DAY = 86400.0


class FakeClock:
    """Duvar saati ve monotonic saat birlikte ilerler; `sleep` beklemez, saati ileri alır."""

    def __init__(self, now: float = T0) -> None:
        self.now = now
        self.slept: List[float] = []

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSink(BaseSink):
    """Teslim edilen toplu gönderimleri kaydeder; `failures` listesindeki hatalar sırayla fırlatılır."""

    def __init__(self, name: str = "probe", events: Sequence[str] = ("*",), **settings: Any) -> None:
        super().__init__(name, EventFilter(events))
        for key, value in settings.items():
            setattr(self, key, value)
        self.batches: List[List[Envelope]] = []
        self.attempts: List[List[int]] = []
        self.failures: List[Exception] = []
        self.fail_always: Optional[Exception] = None
        self.closed = False

    def deliver(self, batch: Sequence[Envelope]) -> None:
        self.attempts.append([env.seq for env in batch])
        if self.fail_always is not None:
            raise self.fail_always
        if self.failures:
            raise self.failures.pop(0)
        self.batches.append(list(batch))

    def close(self) -> None:
        self.closed = True

    @property
    def seqs(self) -> List[int]:
        return [env.seq for batch in self.batches for env in batch]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    # Çalışma zamanı sınır denetimi bu dizini de izler: sink'ler veri dizinine dokunmamalı
    conftest.STORE_BOUNDARY.add_data_dir(str(path))
    return path


@pytest.fixture
def store(data_dir: Path) -> Store:
    return open_store(data_dir)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def emit(store: Store, type_: str = "live.status_changed", *, stream: Optional[str] = None, ts: Optional[float] = None,
         **fields: Any) -> int:
    """Günlüğe bir olay ekler ve sıra numarasını döndürür. Akış verilmezse türün ön ekidir."""
    data = fields.pop("data", {"n": 1})
    event = StreamEvent(type=type_, data=data, ts=ts, source=fields.pop("source", "poll"), **fields)
    seq = store.streams.append(stream or type_.split(".")[0], [event])[0]
    assert seq is not None
    return seq


def make(store: Store, *probes: Sink, clock: Optional[FakeClock] = None, **kwargs: Any) -> Dispatcher:
    return Dispatcher(store, list(probes), clock=clock or FakeClock(), jitter=lambda: 1.0, **kwargs)


def cursor_row(store: Store, name: str) -> Optional[Dict[str, Any]]:
    row = store._state.connection().execute(
        "SELECT seq, last_error FROM sink_cursors WHERE sink = ?", (name,)).fetchone()
    return None if row is None else {"seq": row["seq"], "last_error": row["last_error"]}


def system_events(store: Store, type_: str = "system.sink_dropped") -> List[Dict[str, Any]]:
    return [dict(record.data) for record in store.streams.read(streams=["system"], types=[type_]).events]


# === zarf ve süzgeç ==============================================================================


def test_stream_names_are_the_ones_of_the_store():
    assert base.STREAMS == store_streams.STREAMS == events_command.STREAMS
    assert base.SYSTEM_STREAM == store_streams.SYSTEM_STREAM


def test_envelope_has_the_fields_of_the_design_in_order(store: Store):
    seq = emit(store, "live.status_changed", event_id=17124861, sport="football", tournament_id=17,
               ts=1790856421.25, data={"from": "live", "to": "completed", "provisional": True})
    batch = store.streams.read()
    env = Envelope.from_record(batch.events[0], batch.stream_id)
    assert env.stream_id == batch.stream_id
    document = env.to_dict()
    assert list(document) == ["stream", "seq", "type", "ts", "event_id", "sport", "tournament_id", "source", "data"]
    assert document == {
        "stream": "live", "seq": seq, "type": "live.status_changed", "ts": "2026-10-01T12:07:01.250Z",
        "event_id": 17124861, "sport": "football", "tournament_id": 17, "source": "poll",
        "data": {"from": "live", "to": "completed", "provisional": True},
    }
    assert json.loads(env.to_json()) == document and "\n" not in env.to_json()
    assert "stream_id" not in document and "dedup_key" not in document


def test_timestamps_are_utc_with_milliseconds_and_z():
    assert base.iso_utc(0) == "1970-01-01T00:00:00.000Z"
    assert base.iso_utc(1790856421) == "2026-10-01T12:07:01.000Z"
    assert base.iso_utc(1790856421.9996) == "2026-10-01T12:07:02.000Z"  # milisaniyeye yuvarlanır, taşma saniyeye geçer


def test_lines_are_ascii_whatever_the_data(store: Store):
    emit(store, "live.status_changed", data={"takım": "Beşiktaş", "note": "İ→ş"})
    env = Envelope.from_record(store.streams.read().events[0])
    line = env.to_json()
    assert line.isascii() and json.loads(line)["data"] == {"takım": "Beşiktaş", "note": "İ→ş"}


def test_absent_fields_are_null_not_missing(store: Store):
    emit(store, "job.started", source="job", data={})
    document = Envelope.from_record(store.streams.read().events[0]).to_dict()
    assert document["event_id"] is None and document["sport"] is None and document["tournament_id"] is None
    assert document["data"] == {}


def env_of(type_: str = "live.status_changed", **fields: Any) -> Envelope:
    return Envelope(stream=type_.split(".")[0], seq=fields.pop("seq", 1), type=type_, ts=T0, **fields)


def test_filter_patterns_are_shell_patterns_on_the_type():
    flt = EventFilter(["live.*", "job.finished"])
    assert flt.matches(env_of("live.status_changed")) and flt.matches(env_of("live.score_changed"))
    assert flt.matches(env_of("job.finished")) and not flt.matches(env_of("job.started"))
    assert not flt.matches(env_of("system.blocked")) and not flt.matches(env_of("alive.x"))
    assert not EventFilter(["Live.*"]).matches(env_of("live.status_changed"))  # büyük/küçük harfe duyarlı
    assert EventFilter().matches(env_of("anything")) and EventFilter(["*"]).patterns == ("*",)
    assert EventFilter(["live.s?ore_changed"]).matches(env_of("live.score_changed"))


def test_filter_exact_types_are_only_offered_without_patterns():
    assert EventFilter(["job.finished", "job.started"]).exact_types == ("job.finished", "job.started")
    assert EventFilter(["job.finished", "live.*"]).exact_types == ()
    assert EventFilter(["*"]).exact_types == ()


def test_filter_by_sport_tournament_and_event_is_combined_with_the_type():
    flt = EventFilter(["live.*"], sports=["football"], tournament_ids=[17], event_ids=[5])
    match = env_of(sport="football", tournament_id=17, event_id=5)
    assert flt.matches(match)
    assert not flt.matches(env_of(sport="tennis", tournament_id=17, event_id=5))
    assert not flt.matches(env_of(sport="football", tournament_id=8, event_id=5))
    assert not flt.matches(env_of(sport="football", tournament_id=17, event_id=6))
    assert not flt.matches(env_of("job.finished", sport="football", tournament_id=17, event_id=5))
    assert not flt.matches(env_of())  # alanı olmayan olay süzgece uymaz


@pytest.mark.parametrize("patterns", [[], [""], ["  "], [3]])
def test_filter_rejects_empty_patterns(patterns):
    with pytest.raises(ValueError, match="events"):
        EventFilter(patterns)


def test_base_sink_needs_a_name_and_satisfies_the_protocol():
    with pytest.raises(ValueError):
        BaseSink("")
    probe = RecordingSink("p")
    assert isinstance(probe, Sink) and repr(probe) == "<RecordingSink 'p'>"
    assert (probe.uses_cursor, probe.batch_size, probe.linger_seconds, probe.max_age_seconds) == (True, 500, 0.0, None)


# === stdout ======================================================================================


def test_stdout_sink_writes_one_line_per_event(capsys: pytest.CaptureFixture[str]):
    sink = StdoutSink()
    sink.deliver([env_of(seq=1), env_of("job.finished", seq=2)])
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [1, 2]
    assert sink.uses_cursor is False


def test_stdout_sink_with_a_writer_leaves_errors_to_the_caller():
    lines: List[str] = []
    StdoutSink(write=lines.append).deliver([env_of(seq=7)])
    assert [json.loads(line)["seq"] for line in lines] == [7]

    def broken(_line: str) -> None:
        raise BrokenPipeError()

    with pytest.raises(BrokenPipeError):
        StdoutSink(write=broken).deliver([env_of()])


def test_stdout_sink_is_disabled_when_stdout_is_gone(monkeypatch: pytest.MonkeyPatch):
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stdout", closed)
    with pytest.raises(FatalSinkError, match="stdout is closed"):
        StdoutSink().deliver([env_of()])


# === dosya =======================================================================================


def lines_of(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().decode("ascii").splitlines()]


def envs(*seqs: int, type_: str = "live.status_changed") -> List[Envelope]:
    return [env_of(type_, seq=seq, data={"n": seq}) for seq in seqs]


def test_file_sink_appends_ndjson_with_lf_and_creates_the_folder(tmp_path: Path):
    path = tmp_path / "out" / "deep" / "live.ndjson"
    sink = FileSink("feed", str(path))
    sink.deliver(envs(1, 2))
    sink.deliver(envs(3))
    sink.close()
    raw = path.read_bytes()
    assert raw.count(b"\n") == 3 and b"\r" not in raw and raw.endswith(b"\n")
    assert [doc["seq"] for doc in lines_of(path)] == [1, 2, 3]
    assert lines_of(path)[0] == envs(1)[0].to_dict()


def test_file_sink_appends_to_an_existing_file(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    first = FileSink("feed", str(path))
    first.deliver(envs(1))
    first.close()
    second = FileSink("feed", str(path))
    second.deliver(envs(2))
    second.close()
    assert [doc["seq"] for doc in lines_of(path)] == [1, 2]


def test_file_sink_does_not_repeat_lines_when_a_batch_is_retried(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    sink = FileSink("feed", str(path))
    sink.deliver(envs(1, 2))
    sink.deliver(envs(1, 2, 3))  # dağıtıcı konumu yazamadı ve aynı olayları yeniden verdi
    sink.close()
    assert [doc["seq"] for doc in lines_of(path)] == [1, 2, 3]


def test_file_sink_writes_low_sequence_numbers_of_another_log(tmp_path: Path):
    """Yazılan son numara bir günlüğe aittir: state.db yeniden yaratılırsa numaralar baştan başlar."""
    path = tmp_path / "live.ndjson"
    sink = FileSink("feed", str(path))
    sink.deliver([env_of(seq=7, stream_id="first-log")])
    sink.deliver([env_of(seq=7, stream_id="first-log")])  # aynı günlük, aynı numara: yinelenmez
    sink.deliver([env_of(seq=1, stream_id="second-log"), env_of(seq=2, stream_id="second-log")])
    sink.close()
    assert [doc["seq"] for doc in lines_of(path)] == [7, 1, 2]


def test_file_sink_rotates_by_size(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    clock = FakeClock()
    line = len(envs(1)[0].to_json()) + 1
    sink = FileSink("feed", str(path), rotate_bytes=line * 2, clock=clock)
    sink.deliver(envs(1, 2))
    clock.advance(60)
    sink.deliver(envs(3))  # sığmıyor: önce çevrilir
    clock.advance(60)
    sink.deliver(envs(4, 5, 6, 7))  # bir toplu gönderim birden çok dosyaya bölünür
    sink.close()
    # Çevrilen dosya son yazıldığı anın adını alır; aynı saniyedeki ikinci çevirme bir sayaç ekler
    stamp = [time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(T0 + offset)) for offset in (0, 120)]
    names = [f"live.{stamp[0]}.ndjson", f"live.{stamp[1]}.ndjson", f"live.{stamp[1]}-01.ndjson"]
    assert sink._rotated_names() == names  # eskiden yeniye
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(names + ["live.ndjson"])
    files = [lines_of(tmp_path / name) for name in names + ["live.ndjson"]]
    assert [[doc["seq"] for doc in docs] for docs in files] == [[1, 2], [3, 4], [5, 6], [7]]


def test_file_sink_rotations_in_the_same_second_get_a_counter(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    sink = FileSink("feed", str(path), rotate_bytes=len(envs(1)[0].to_json()) + 1, clock=FakeClock())
    sink.deliver(envs(1, 2, 3))
    sink.close()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(T0))
    assert sink._rotated_names() == [f"live.{stamp}.ndjson", f"live.{stamp}-01.ndjson"]
    assert [lines_of(tmp_path / name)[0]["seq"] for name in sink._rotated_names()] == [1, 2]
    assert [doc["seq"] for doc in lines_of(path)] == [3]
    path.unlink()
    assert FileSink("feed", str(path)).last_delivered()["seq"] == 2  # en yeni çevrilmiş dosya sayaçlı olandır


def test_file_sink_rotates_when_the_utc_day_changes(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    midnight = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc).timestamp()
    clock = FakeClock(midnight - 30)
    sink = FileSink("feed", str(path), rotate_daily=True, clock=clock)
    sink.deliver(envs(1))
    clock.advance(10)
    sink.deliver(envs(2))  # aynı gün: aynı dosya
    clock.advance(60)
    sink.deliver(envs(3))  # ertesi gün
    sink.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["live.20261001T235940Z.ndjson", "live.ndjson"]
    assert [doc["seq"] for doc in lines_of(tmp_path / "live.20261001T235940Z.ndjson")] == [1, 2]
    assert [doc["seq"] for doc in lines_of(path)] == [3]


def test_file_sink_takes_the_day_of_an_existing_file_from_its_modification_time(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    yesterday = dt.datetime(2026, 10, 1, 22, 0, tzinfo=dt.timezone.utc).timestamp()
    path.write_bytes((envs(1)[0].to_json() + "\n").encode("ascii"))
    os.utime(path, (yesterday, yesterday))
    sink = FileSink("feed", str(path), rotate_daily=True, clock=FakeClock(yesterday + 3 * 3600))
    sink.deliver(envs(2))
    sink.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["live.20261001T220000Z.ndjson", "live.ndjson"]


def test_file_sink_keeps_only_the_newest_rotated_files(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    clock = FakeClock()
    sink = FileSink("feed", str(path), rotate_bytes=len(envs(1)[0].to_json()) + 1, keep=2, clock=clock)
    for seq in range(1, 7):
        sink.deliver(envs(seq))
        clock.advance(1)
    sink.close()
    rotated = sorted(p.name for p in tmp_path.iterdir() if p.name != "live.ndjson")
    assert len(rotated) == 2
    assert [lines_of(tmp_path / name)[0]["seq"] for name in rotated] == [4, 5]
    (tmp_path / "live.other.ndjson").write_text("{}\n")  # kalıba uymayan dosyaya dokunulmaz
    assert "live.other.ndjson" not in FileSink("feed", str(path))._rotated_names()


def test_file_sink_removes_an_incomplete_last_line(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "live.ndjson"
    whole = (envs(1)[0].to_json() + "\n").encode("ascii")
    path.write_bytes(whole + b'{"stream":"live","seq":2,"ty')  # yazarken ölen süreç
    sink = FileSink("feed", str(path))
    assert sink.last_delivered() == envs(1)[0].to_dict()  # yarım satır sayılmaz
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        sink.deliver(envs(2))
    sink.close()
    assert [doc["seq"] for doc in lines_of(path)] == [1, 2]
    assert "removed an incomplete last line" in caplog.text


def test_file_sink_with_only_a_torn_line_starts_clean(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    path.write_bytes(b'{"stream":"li')
    sink = FileSink("feed", str(path))
    assert sink.last_delivered() is None
    sink.deliver(envs(1))
    sink.close()
    assert [doc["seq"] for doc in lines_of(path)] == [1]


def test_file_sink_last_delivered_reads_the_newest_file(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    assert FileSink("feed", str(path)).last_delivered() is None  # dosya yok
    clock = FakeClock()
    sink = FileSink("feed", str(path), rotate_bytes=len(envs(1)[0].to_json()) + 1, clock=clock)
    sink.deliver(envs(1))
    clock.advance(5)
    sink.deliver(envs(2))
    assert sink.last_delivered()["seq"] == 2
    sink.close()
    path.unlink()  # yazılan dosya yok: en yeni çevrilmiş dosyaya bakılır
    assert FileSink("feed", str(path)).last_delivered()["seq"] == 1
    path.write_bytes(b"not json\n")
    assert FileSink("feed", str(path)).last_delivered() is None


def test_file_sink_reads_a_last_line_longer_than_one_chunk(tmp_path: Path):
    path = tmp_path / "live.ndjson"
    big = env_of(seq=2, data={"blob": "x" * 200_000})
    sink = FileSink("feed", str(path))
    sink.deliver([env_of(seq=1), big])
    sink.close()
    assert FileSink("feed", str(path)).last_delivered() == big.to_dict()


def test_file_sink_reports_a_write_error_as_retryable_and_recovers(tmp_path: Path):
    blocker = tmp_path / "out"
    blocker.write_text("a file where the folder should be")
    sink = FileSink("feed", str(blocker / "live.ndjson"))
    with pytest.raises(RetryableSinkError, match="cannot write the file") as caught:
        sink.deliver(envs(1))
    assert str(tmp_path) not in str(caught.value)  # hata metni yol taşımaz
    blocker.unlink()
    sink.deliver(envs(1))
    sink.close()
    assert [doc["seq"] for doc in lines_of(blocker / "live.ndjson")] == [1]


def test_file_sink_rejects_bad_arguments(tmp_path: Path):
    with pytest.raises(ValueError):
        FileSink("feed", "")
    with pytest.raises(ValueError):
        FileSink("feed", str(tmp_path / "x"), rotate_bytes=-1)


# === dağıtıcı: konumlar ==========================================================================


def test_a_new_sink_starts_at_now_and_is_remembered(store: Store):
    old = emit(store)
    probe = RecordingSink()
    dispatcher = make(store, probe)
    assert dispatcher.step() == math.inf
    assert probe.batches == []  # günlükte birikmiş eski olaylar yeni sink'e gönderilmez
    assert cursor_row(store, "probe") == {"seq": old, "last_error": None}
    assert store.runtime.get("sink:probe").value == {"since_seq": old}
    new = emit(store)
    dispatcher.step()
    assert probe.seqs == [new] and cursor_row(store, "probe")["seq"] == new


def test_a_known_sink_resumes_after_its_cursor_also_from_zero(store: Store):
    probe = RecordingSink()
    make(store, probe).register()  # boş günlükte kaydedildi: konum 0
    assert cursor_row(store, "probe")["seq"] == 0
    seqs = [emit(store), emit(store)]
    again = RecordingSink()
    make(store, again).step()  # yeniden başlama: bilinen sink "şimdi"ye atlamaz
    assert again.seqs == seqs


def test_register_is_idempotent_and_needs_no_lease(store: Store):
    with store.lease("sinks", purpose="other"):
        dispatcher = make(store, RecordingSink())
        dispatcher.register()
        dispatcher.register()
    first = emit(store)
    make(store, RecordingSink()).register()  # ikinci bir süreç: bilinen sink'e dokunmaz
    assert cursor_row(store, "probe")["seq"] == 0 and first == 1


def test_delivery_is_ordered_filtered_and_the_cursor_passes_unwanted_events(store: Store):
    probe = RecordingSink(events=["live.*", "job.finished"])
    dispatcher = make(store, probe)
    dispatcher.register()
    a = emit(store, "live.status_changed")
    emit(store, "job.started", source="job")
    b = emit(store, "job.finished", source="job")
    c = emit(store, "live.score_changed")
    tail = emit(store, "system.blocked", source="system")
    dispatcher.step()
    assert probe.seqs == [a, b, c]
    assert [env.type for env in probe.batches[0]] == ["live.status_changed", "job.finished", "live.score_changed"]
    assert probe.batches[0][0].stream_id == store.streams.head().stream_id
    assert cursor_row(store, "probe")["seq"] == tail  # istenmeyen son olayın da ötesinde


def test_events_are_split_into_batches_of_the_sinks_size(store: Store):
    probe = RecordingSink(batch_size=2)
    dispatcher = make(store, probe, read_limit=3)
    dispatcher.register()
    seqs = [emit(store) for _ in range(7)]
    dispatcher.step()
    assert [[env.seq for env in batch] for batch in probe.batches] == [seqs[0:2], seqs[2:4], seqs[4:6], seqs[6:]]
    assert cursor_row(store, "probe")["seq"] == seqs[-1]


def test_each_sink_has_its_own_cursor(store: Store):
    fast, slow = RecordingSink("fast"), RecordingSink("slow")
    slow.fail_always = RetryableSinkError("down")
    dispatcher = make(store, fast, slow)
    dispatcher.register()
    seqs = [emit(store), emit(store)]
    dispatcher.step()
    assert fast.seqs == seqs and slow.seqs == []
    assert cursor_row(store, "fast")["seq"] == seqs[-1] and cursor_row(store, "slow")["seq"] == 0


def test_any_object_with_the_protocol_methods_is_a_sink(store: Store):
    """Dağıtıcı `BaseSink` istemez: protokolün dört üyesi yeter, ayarların varsayılanları kullanılır."""

    class Minimal:
        name = "minimal"

        def __init__(self) -> None:
            self.got: List[int] = []

        def accepts(self, env: Envelope) -> bool:
            return env.type != "job.started"

        def deliver(self, batch: Sequence[Envelope]) -> None:
            self.got.extend(env.seq for env in batch)

        def close(self) -> None:
            pass

    minimal = Minimal()
    assert isinstance(minimal, Sink)
    dispatcher = make(store, minimal)
    dispatcher.register()
    wanted = emit(store)
    emit(store, "job.started", source="job")
    dispatcher.step()
    assert minimal.got == [wanted] and cursor_row(store, "minimal")["seq"] == 2


def test_sink_names_must_be_unique(store: Store):
    with pytest.raises(ValueError, match="unique"):
        Dispatcher(store, [RecordingSink("a"), RecordingSink("a")])


def test_a_sink_without_cursor_starts_at_now_on_every_start_and_stores_nothing(store: Store):
    emit(store)
    probe = RecordingSink("out", uses_cursor=False)
    dispatcher = make(store, probe)
    dispatcher.step()
    seq = emit(store)
    dispatcher.step()
    assert probe.seqs == [seq]
    assert cursor_row(store, "out") is None and store.runtime.get("sink:out") is None
    emit(store)
    again = RecordingSink("out", uses_cursor=False)
    make(store, again).step()
    assert again.seqs == []  # yeni süreç: yine "şimdi"den


def test_many_batches_are_spread_over_steps(store: Store, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dispatcher_mod, "MAX_BATCHES_PER_STEP", 2)
    probe = RecordingSink(batch_size=1)
    dispatcher = make(store, probe)
    dispatcher.register()
    seqs = [emit(store) for _ in range(5)]
    assert dispatcher.step() == 0.0 and probe.seqs == seqs[:2]  # sıra diğer sink'lere geçer
    assert dispatcher.step() == 0.0 and dispatcher.step() == math.inf
    assert probe.seqs == seqs


def test_a_long_run_of_unwanted_events_is_passed_in_bounded_steps(store: Store, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dispatcher_mod, "MAX_READS_PER_BATCH", 2)
    rare, other = RecordingSink("rare", events=["job.finished"]), RecordingSink("other")
    dispatcher = make(store, rare, other, read_limit=2)
    dispatcher.register()
    seqs = [emit(store) for _ in range(9)]
    wanted = emit(store, "job.finished", source="job")
    assert dispatcher.step() == 0.0  # dört satır geçildi; sıra diğer sink'e geçti
    assert cursor_row(store, "rare")["seq"] == seqs[3] and not dispatcher.status()[0].caught_up
    assert other.seqs == seqs + [wanted]
    while dispatcher.step() == 0.0:
        pass
    assert rare.seqs == [wanted] and cursor_row(store, "rare")["seq"] == wanted
    assert make(store, rare, other).drain(10.0).complete


# === dağıtıcı: hata ve yeniden deneme ============================================================


def test_a_failed_batch_is_retried_with_backoff_and_blocks_the_sink(store: Store, clock: FakeClock):
    probe = RecordingSink(batch_size=2)
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    first = [emit(store), emit(store)]
    probe.failures = [RetryableSinkError("the receiver answered HTTP 503")] * 3
    assert dispatcher.step() == 1.0
    assert cursor_row(store, "probe") == {"seq": 0, "last_error": "the receiver answered HTTP 503"}
    later = emit(store)
    assert dispatcher.step() == 1.0 and len(probe.attempts) == 1  # süre dolmadan yeniden denenmez
    clock.advance(1.0)
    assert dispatcher.step() == 2.0
    clock.advance(2.0)
    assert dispatcher.step() == 4.0
    assert probe.attempts == [first, first, first]  # aynı toplu gönderim; sonraki olay öne geçmez
    clock.advance(4.0)
    assert dispatcher.step() == math.inf
    assert probe.seqs == first + [later]
    assert cursor_row(store, "probe") == {"seq": later, "last_error": None}


def test_backoff_doubles_up_to_five_minutes(store: Store, clock: FakeClock):
    probe = RecordingSink()
    probe.fail_always = RetryableSinkError("down")
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    emit(store, ts=clock.time())
    delays = []
    for _ in range(12):
        delay = dispatcher.step()
        delays.append(delay)
        clock.advance(delay)
    assert delays == [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 300]


def test_backoff_has_jitter_by_default(store: Store, clock: FakeClock, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dispatcher_mod.random, "random", lambda: 0.0)
    probe = RecordingSink()
    probe.fail_always = RetryableSinkError("down")
    dispatcher = Dispatcher(store, [probe], clock=clock)
    dispatcher.register()
    emit(store, ts=clock.time())
    assert dispatcher.step() == 0.5  # [0.5, 1.0] x 1 sn
    monkeypatch.setattr(dispatcher_mod.random, "random", lambda: 0.999)
    clock.advance(1)
    assert 1.9 < dispatcher.step() < 2.0


def test_retry_after_of_the_receiver_is_honoured_up_to_the_cap(store: Store, clock: FakeClock):
    probe = RecordingSink()
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    emit(store, ts=clock.time())
    probe.failures = [RetryableSinkError("HTTP 429", retry_after=30), RetryableSinkError("HTTP 429", retry_after=9999)]
    assert dispatcher.step() == 30
    clock.advance(30)
    assert dispatcher.step() == 300


def test_an_unexpected_error_of_a_sink_is_retried_and_does_not_stop_the_others(store: Store, clock: FakeClock,
                                                                              caplog: pytest.LogCaptureFixture):
    broken, fine = RecordingSink("broken"), RecordingSink("fine")
    broken.failures = [TypeError("boom")]
    dispatcher = make(store, broken, fine, clock=clock)
    dispatcher.register()
    seq = emit(store)
    with caplog.at_level(logging.INFO, logger="Sinks"):
        assert dispatcher.step() == 1.0
        assert fine.seqs == [seq]
        assert cursor_row(store, "broken")["last_error"] == "internal error (TypeError: boom)"
        clock.advance(1)
        dispatcher.step()
    assert broken.seqs == [seq] and cursor_row(store, "broken") == {"seq": seq, "last_error": None}
    assert "Sink broken: delivery works again" in caplog.text


def test_a_fatal_error_disables_the_sink_until_restart(store: Store, clock: FakeClock,
                                                       caplog: pytest.LogCaptureFixture):
    gone, fine = RecordingSink("gone"), RecordingSink("fine")
    gone.failures = [FatalSinkError("the receiver answered 410 Gone")]
    dispatcher = make(store, gone, fine, clock=clock)
    dispatcher.register()
    first = emit(store)
    with caplog.at_level(logging.ERROR, logger="Sinks"):
        assert dispatcher.step() == math.inf
    assert "Sink gone is disabled until restart" in caplog.text
    second = emit(store)
    clock.advance(3600)
    dispatcher.step()
    assert gone.attempts == [[first]] and fine.seqs == [first, second]  # kapalı sink'e bir daha gidilmez
    row = cursor_row(store, "gone")
    assert row == {"seq": 0, "last_error": "disabled until restart: the receiver answered 410 Gone"}
    status = {item.name: item for item in dispatcher.status()}
    assert status["gone"].disabled and not status["gone"].caught_up and status["fine"].caught_up
    restarted = RecordingSink("gone")
    make(store, restarted).step()  # yeniden başlama: konum yerinde, teslim yeniden denenir
    assert restarted.seqs == [first, second]


def test_position_survives_a_restart_and_nothing_is_sent_twice(store: Store):
    probe = RecordingSink()
    dispatcher = make(store, probe)
    dispatcher.register()
    first = [emit(store), emit(store)]
    dispatcher.step()
    dispatcher.close()
    assert probe.closed
    second = [emit(store)]
    after_restart = RecordingSink()
    make(store, after_restart).step()
    assert probe.seqs == first and after_restart.seqs == second


def test_a_crash_before_the_cursor_is_stored_replays_the_batch(store: Store, monkeypatch: pytest.MonkeyPatch,
                                                              caplog: pytest.LogCaptureFixture):
    probe = RecordingSink()
    dispatcher = make(store, probe)
    dispatcher.register()
    seqs = [emit(store), emit(store)]

    def busy(*_args: Any, **_kwargs: Any) -> None:
        raise StoreBusy("state.db is busy")

    with monkeypatch.context() as patch, caplog.at_level(logging.WARNING, logger="Sinks"):
        patch.setattr(store.streams, "set_cursor", busy)
        dispatcher.step()  # teslim edildi, konum yazılamadı; dağıtıcı ölmez
    assert probe.seqs == seqs and cursor_row(store, "probe")["seq"] == 0
    assert "its position could not be stored (StoreBusy)" in caplog.text
    replay = RecordingSink()
    make(store, replay).step()
    assert replay.seqs == seqs  # en az bir kez: alıcı `seq` ile ayıklar


def test_a_busy_event_log_delays_a_sink_instead_of_ending_the_dispatcher(store: Store, monkeypatch: pytest.MonkeyPatch):
    probe = RecordingSink()
    dispatcher = make(store, probe)
    dispatcher.register()
    seq = emit(store)
    real_read = store.streams.read

    def busy(**_kwargs: Any) -> Any:
        raise StoreBusy("state.db is busy")

    monkeypatch.setattr(store.streams, "read", busy)
    assert dispatcher.step() == 1.0
    monkeypatch.setattr(store.streams, "read", real_read)
    dispatcher.step()
    assert probe.seqs == [seq]


def test_a_cursor_that_could_not_be_stored_is_written_at_the_next_step(store: Store, monkeypatch: pytest.MonkeyPatch):
    """Konum yazılamadıysa bir sonraki turda yeniden denenir; yeni bir olay beklenmez."""
    probe = RecordingSink()
    dispatcher = make(store, probe)
    dispatcher.register()
    seqs = [emit(store), emit(store)]
    with monkeypatch.context() as patch:
        patch.setattr(store.streams, "set_cursor", lambda *a, **k: (_ for _ in ()).throw(StoreBusy("busy")))
        dispatcher.step()
        assert dispatcher.step() == math.inf  # yazılamadıkça her turda yeniden denenir, teslim yinelenmez
    assert probe.seqs == seqs and cursor_row(store, "probe")["seq"] == 0
    assert dispatcher.step() == math.inf
    assert cursor_row(store, "probe") == {"seq": seqs[-1], "last_error": None}
    replay = RecordingSink()
    make(store, replay).step()
    assert probe.seqs == seqs and replay.seqs == []  # yeniden başlama artık hiçbir şeyi yinelemez


@pytest.mark.parametrize("error", [
    sqlite3.OperationalError("database is locked"), sqlite3.DatabaseError("disk I/O error"),
])
def test_a_sqlite_error_of_the_event_log_delays_a_sink_like_a_busy_one(store: Store, monkeypatch: pytest.MonkeyPatch,
                                                                      caplog: pytest.LogCaptureFixture,
                                                                      error: Exception):
    """Store yalnızca kilit zaman aşımını StoreBusy'ye çevirir; diğer SQLite hataları olduğu gibi çıkar."""
    probe, other = RecordingSink(), RecordingSink("other")
    dispatcher = make(store, probe, other)
    dispatcher.register()
    seq = emit(store)
    real_read = store.streams.read
    calls: List[int] = []

    def flaky(**kwargs: Any) -> Any:
        calls.append(kwargs["after"])
        if len(calls) == 1:
            raise error
        return real_read(**kwargs)

    monkeypatch.setattr(store.streams, "read", flaky)
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        assert dispatcher.step() == 1.0
    assert probe.seqs == [] and other.seqs == [seq]  # hata alan sink bekler, diğeri sürer
    assert f"could not be read or written ({type(error).__name__})" in caplog.text
    dispatcher.step()
    assert probe.seqs == [seq]


def test_a_step_that_is_told_to_stop_starts_no_further_batch(store: Store):
    first, second = RecordingSink("first", batch_size=1), RecordingSink("second", batch_size=1)
    dispatcher = make(store, first, second)
    dispatcher.register()
    seqs = [emit(store) for _ in range(3)]
    assert dispatcher.step(until=lambda: bool(first.batches)) == 0.0
    # İlk sink'in ilk teslimi bitti; ne onun sıradaki toplu gönderimine ne de ikinci sink'e başlandı
    assert first.seqs == seqs[:1] and second.seqs == []
    assert cursor_row(store, "first")["seq"] == seqs[0]
    assert dispatcher.step(until=lambda: False) == math.inf
    assert first.seqs == seqs and second.seqs == seqs


def test_a_replaced_event_log_restarts_the_positions(store: Store, clock: FakeClock, caplog: pytest.LogCaptureFixture):
    """`stream_id` değiştiyse eldeki numaralar başka bir günlüğündür: bekleyen gönderim bırakılır, konumlar yeniden okunur."""
    stuck, other = RecordingSink("stuck"), RecordingSink("other")
    stuck.failures = [RetryableSinkError("down")]
    dispatcher = make(store, stuck, other, clock=clock)
    dispatcher.register()
    first = emit(store)
    assert dispatcher.step() == 1.0 and dispatcher._states[0].head is not None and other.seqs == [first]
    store.streams._stream_id = "another-log"  # state.db yeniden yaratılmış gibi: okumalar başka bir kimlik bildirir
    later = emit(store)
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        assert dispatcher.step() == 0.0  # `other` okudu ve başka bir günlük gördü: tur bırakıldı
    assert "The event log was replaced" in caplog.text and not dispatcher._started
    assert dispatcher._states[0].head is None  # eski günlükten kalan toplu gönderim yeniden denenmez
    assert dispatcher.step() == math.inf
    assert dispatcher._stream_id == "another-log"
    assert stuck.seqs == [first, later] and other.seqs == [first, later]  # konumlar state.db'den yeniden okundu
    assert {env.stream_id for env in stuck.batches[-1]} == {"another-log"}
    assert caplog.text.count("The event log was replaced") == 1


# === dağıtıcı: bekletme, yaş sınırı, budama ======================================================


def test_a_partial_batch_waits_for_the_flush_interval(store: Store, clock: FakeClock):
    probe = RecordingSink(batch_size=3, linger_seconds=1.0)
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    a = emit(store)
    assert dispatcher.step() == 1.0 and probe.batches == []
    clock.advance(0.4)
    b = emit(store)
    assert dispatcher.step() == pytest.approx(0.6) and probe.batches == []  # süre ilk olaydan sayılır
    clock.advance(0.6)
    assert dispatcher.step() == math.inf
    assert [[env.seq for env in batch] for batch in probe.batches] == [[a, b]]


def test_a_full_batch_is_sent_at_once_and_flush_does_not_wait(store: Store, clock: FakeClock):
    probe = RecordingSink(batch_size=2, linger_seconds=1.0)
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    seqs = [emit(store), emit(store), emit(store)]
    assert dispatcher.step() == 1.0
    assert probe.seqs == seqs[:2]  # dolu olan hemen, kalan bekler
    assert dispatcher.step(flush=True) == math.inf
    assert probe.seqs == seqs


def test_events_older_than_max_age_are_dropped_and_recorded(store: Store, clock: FakeClock,
                                                            caplog: pytest.LogCaptureFixture):
    probe = RecordingSink(events=["live.*", "system.*"], batch_size=2, max_age_seconds=DAY)
    probe.fail_always = RetryableSinkError("the receiver answered HTTP 500")
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    old = [emit(store, ts=clock.time() - DAY - 60) for _ in range(5)]
    emit(store, "job.started", source="job", ts=clock.time() - DAY - 60)  # sink'in istemediği olay sayılmaz
    fresh = [emit(store, ts=clock.time()) for _ in range(2)]
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        assert dispatcher.step() == 1.0
    # Baştaki toplu gönderim bir kez denendi; sınırdan eski beş olayın hepsi tek seferde bırakıldı
    assert probe.attempts == [old[:2]]
    assert system_events(store) == [{
        "sink": "probe", "reason": "max_age", "first_seq": old[0], "last_seq": old[-1], "count": 5,
        "max_age_seconds": DAY,
    }]
    assert cursor_row(store, "probe") == {"seq": old[-1], "last_error": "the receiver answered HTTP 500"}
    assert "dropped 5 undelivered event(s)" in caplog.text
    # Teslim, bir sonraki deneme anında, kalan (taze) olaylarla sürer; sink kendi "bırakıldı" kaydını da alır
    probe.fail_always = None
    clock.advance(1.0)
    dispatcher.step()
    assert probe.seqs[:2] == fresh
    assert probe.batches[-1][-1].type == "system.sink_dropped"
    assert dispatcher.status()[0].dropped == 5


def test_fresh_events_are_never_dropped_and_a_sink_without_max_age_keeps_everything(store: Store, clock: FakeClock):
    young, keeper = RecordingSink("young", max_age_seconds=DAY), RecordingSink("keeper")
    young.fail_always = keeper.fail_always = RetryableSinkError("down")
    dispatcher = make(store, young, keeper, clock=clock)
    dispatcher.register()
    emit(store, ts=clock.time() - DAY + 60)
    emit(store, ts=clock.time() - 30 * DAY)  # zaman damgası sırasız olabilir: baştaki olay taze, bırakılmaz
    dispatcher.step()
    assert system_events(store) == []
    assert cursor_row(store, "young")["seq"] == 0 and cursor_row(store, "keeper")["seq"] == 0


def test_dropping_only_its_own_drop_records_writes_no_new_record(store: Store, clock: FakeClock):
    probe = RecordingSink(events=["system.sink_dropped"], max_age_seconds=DAY)
    probe.fail_always = RetryableSinkError("down")
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    emit(store, "system.sink_dropped", source="system", ts=clock.time() - 2 * DAY,
         data={"sink": "probe", "reason": "max_age", "first_seq": 1, "last_seq": 2, "count": 2})
    dispatcher.step()
    assert len(system_events(store)) == 1  # yalnızca baştaki kayıt: kendi kendini besleyen bir döngü olmaz
    assert dispatcher.status()[0].dropped == 1


def test_events_pruned_before_delivery_are_recorded_once(store: Store, clock: FakeClock,
                                                         caplog: pytest.LogCaptureFixture):
    probe = RecordingSink(events=["live.*"])
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    old = [emit(store, ts=time.time() - 30 * DAY) for _ in range(3)]
    kept = emit(store)
    assert store.streams.prune(max_age_s=7 * DAY) == 3
    probe.failures = [RetryableSinkError("down")]
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        dispatcher.step()
        clock.advance(1.0)
        dispatcher.step()
    assert probe.seqs == [kept]
    assert system_events(store) == [{
        "sink": "probe", "reason": "pruned", "first_seq": old[0], "last_seq": old[-1], "count": None,
    }]
    assert caplog.text.count("were pruned before they were delivered") == 1


def test_the_dispatcher_prunes_the_log_once_per_hour(store: Store, clock: FakeClock, monkeypatch: pytest.MonkeyPatch):
    calls: List[Dict[str, Any]] = []
    monkeypatch.setattr(store.streams, "prune", lambda **kwargs: calls.append(kwargs) or 0)
    dispatcher = make(store, RecordingSink(), clock=clock)
    dispatcher._prune_if_due()
    dispatcher._prune_if_due()
    clock.advance(3599)
    dispatcher._prune_if_due()
    assert calls == [{"max_age_s": 7 * DAY, "max_rows": 1_000_000}]  # 01-storage.md 9.3
    clock.advance(1)
    dispatcher._prune_if_due()
    assert len(calls) == 2

    def busy(**_kwargs: Any) -> int:
        raise StoreBusy("busy")

    monkeypatch.setattr(store.streams, "prune", busy)
    clock.advance(3600)
    dispatcher._prune_if_due()  # budanamadıysa dağıtıcı sürer


# === dağıtıcı: dosya sink'inin konumunun kurtarılması ============================================


def test_file_sink_cursor_is_recovered_from_the_last_line(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture):
    path = tmp_path / "out" / "live.ndjson"
    sink = FileSink("feed", str(path), EventFilter(["live.*"]))
    dispatcher = make(store, sink)
    dispatcher.register()
    first = [emit(store), emit(store)]
    with monkeypatch.context() as patch:
        # Süreç satırları yazdı ve konumu kaydedemeden öldü
        patch.setattr(store.streams, "set_cursor", lambda *a, **k: (_ for _ in ()).throw(StoreBusy("busy")))
        dispatcher.step()
    dispatcher.close()
    assert cursor_row(store, "feed")["seq"] == 0 and [doc["seq"] for doc in lines_of(path)] == first
    later = emit(store)
    with caplog.at_level(logging.INFO, logger="Sinks"):
        restarted = make(store, FileSink("feed", str(path), EventFilter(["live.*"])))
        restarted.step()
        restarted.close()
    assert [doc["seq"] for doc in lines_of(path)] == first + [later]  # hiçbir satır iki kez yazılmadı
    assert cursor_row(store, "feed")["seq"] == later
    assert "position recovered from its output (sequence 2, stored 0)" in caplog.text


def test_file_sink_cursor_is_not_recovered_from_a_file_of_another_log(store: Store, tmp_path: Path):
    path = tmp_path / "live.ndjson"
    stale = env_of(seq=2, data={"from": "another log"})
    path.write_bytes((stale.to_json() + "\n").encode("ascii"))
    dispatcher = make(store, FileSink("feed", str(path)))
    dispatcher.register()
    seqs = [emit(store), emit(store), emit(store)]  # 2 numaralı olay var ama dosyadaki satırla aynı değil
    dispatcher.step()
    dispatcher.close()
    assert [doc["seq"] for doc in lines_of(path)] == [2] + seqs


def test_a_broken_last_line_reader_does_not_stop_the_start(store: Store, caplog: pytest.LogCaptureFixture):
    probe = RecordingSink()
    probe.last_delivered = lambda: (_ for _ in ()).throw(OSError("unreadable"))  # type: ignore[attr-defined]
    dispatcher = make(store, probe)
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        dispatcher.register()
        seq = emit(store)
        dispatcher.step()
    assert probe.seqs == [seq] and "could not be read (OSError)" in caplog.text


# === dağıtıcı: kilit, tek seferlik boşaltma, uzun çalışan döngü ==================================


def test_drain_delivers_the_backlog_and_reports(store: Store, clock: FakeClock):
    probe = RecordingSink(batch_size=2, linger_seconds=1.0)
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    seqs = [emit(store) for _ in range(3)]
    report = dispatcher.drain(10.0)
    assert isinstance(report, DrainReport)
    assert (report.complete, report.timed_out, report.lease_held, report.delivered) == (True, False, False, 3)
    assert probe.seqs == seqs and clock.slept == []  # toplu gönderimin dolması beklenmez
    assert report.to_dict() == {
        "complete": True, "timed_out": False, "lease_held": False, "holder_pid": None, "delivered": 3,
        "sinks": [{"name": "probe", "cursor": seqs[-1], "delivered": 3, "dropped": 0, "caught_up": True,
                   "disabled": False, "error": None}],
    }
    assert store.lease_holder("sinks") is None  # kilit bırakıldı


def test_drain_retries_within_its_time_and_then_gives_up(store: Store, clock: FakeClock):
    probe = RecordingSink()
    probe.fail_always = RetryableSinkError("the receiver answered HTTP 503")
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    seq = emit(store, ts=clock.time())
    report = dispatcher.drain(10.0)
    assert (report.complete, report.timed_out) == (False, True)
    assert clock.slept == [1.0, 2.0, 4.0]  # 8 sn'lik bir sonraki bekleme süreye sığmaz
    assert len(probe.attempts) == 4
    assert report.sinks[0].error == "the receiver answered HTTP 503" and report.sinks[0].cursor == 0
    probe.fail_always = None
    assert make(store, probe, clock=clock).drain(10.0).complete and probe.seqs == [seq]  # bir sonraki çalıştırma


class SlowSink(RecordingSink):
    """Her teslimi `seconds` süren bir alıcı (sahte saat o kadar ilerler)."""

    def __init__(self, clock: FakeClock, seconds: float, **settings: Any) -> None:
        super().__init__(**settings)
        self._slow_clock, self._seconds = clock, seconds

    def deliver(self, batch: Sequence[Envelope]) -> None:
        self._slow_clock.advance(self._seconds)
        super().deliver(batch)


def test_drain_starts_no_delivery_after_its_time_is_up(store: Store, clock: FakeClock):
    """Yavaş ama çalışan bir alıcı süreyi en çok bir teslim kadar aşar: sıradaki toplu gönderimlere başlanmaz."""
    probe = SlowSink(clock, 6.0, batch_size=1)
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    seqs = [emit(store) for _ in range(5)]
    started = clock.monotonic()
    report = dispatcher.drain(10.0)
    assert probe.seqs == seqs[:2] and clock.monotonic() - started == 12.0  # 6 sn + 6 sn; üçüncüye başlanmadı
    assert (report.complete, report.timed_out, report.delivered) == (False, True, 2)
    assert cursor_row(store, "probe")["seq"] == seqs[1] and store.lease_holder("sinks") is None
    assert make(store, SlowSink(clock, 0.0), clock=clock).drain(10.0).complete  # kalanlar bir sonraki çalıştırmaya


def test_drain_without_time_delivers_nothing(store: Store, clock: FakeClock):
    probe = RecordingSink()
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    emit(store)
    report = dispatcher.drain(0)
    assert probe.attempts == [] and (report.complete, report.timed_out) == (False, True)


def test_drain_leaves_the_backlog_to_the_holder_of_the_lease(store: Store):
    probe = RecordingSink()
    dispatcher = make(store, probe)
    dispatcher.register()
    emit(store)
    with store.lease("sinks", purpose="dispatcher"):
        report = dispatcher.drain(10.0)
    assert report.lease_held and report.holder_pid == os.getpid() and not report.complete and report.sinks == ()
    assert probe.attempts == []


# Başka bir süreç: depoyu açar, `sinks` kilidini alır, "ready" yazar ve stdin'den bir satır gelince bırakır.
LEASE_HOLDER = """
import os, sys
from src.store import open_store
lease = open_store(sys.argv[1]).lease("sinks", purpose="dispatcher")
print("ready", os.getpid(), flush=True)
sys.stdin.readline()
lease.release()
"""


def test_drain_leaves_the_backlog_to_another_process_that_dispatches(store: Store):
    probe = RecordingSink()
    dispatcher = make(store, probe)
    dispatcher.register()
    seq = emit(store)
    holder = subprocess.Popen([sys.executable, "-c", LEASE_HOLDER, str(store.data_dir)], cwd=ROOT,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        ready, pid = holder.stdout.readline().split()  # sürecin kendi bildirdiği kimlik (Windows'ta başlatıcı ayrı süreçtir)
        assert ready == "ready"
        report = dispatcher.drain(10.0)
        assert report.lease_held and report.holder_pid == int(pid) and probe.attempts == []
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.wait(timeout=30) == 0
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.stdout.close()
        holder.stdin.close()
    assert dispatcher.drain(10.0).complete and probe.seqs == [seq]  # kilit boşaldı: bir sonraki çalıştırma teslim eder


def test_drain_with_a_disabled_sink_is_not_complete_but_not_timed_out(store: Store, clock: FakeClock):
    probe = RecordingSink()
    probe.failures = [FatalSinkError("gone")]
    dispatcher = make(store, probe, clock=clock)
    dispatcher.register()
    emit(store)
    report = dispatcher.drain(10.0)
    assert (report.complete, report.timed_out, report.sinks[0].disabled) == (False, False, True)


def test_one_shot_run_delivers_the_events_of_its_own_job_at_exit(store: Store, tmp_path: Path):
    """Tek seferlik komutun akışı: başta kayıt, iş (JobManager `job` akışına yazar), çıkışta boşaltma."""
    path = tmp_path / "out" / "jobs.ndjson"
    spec = SinkSpec(name="jobs", type="file", events=("job.*",), path=str(path))
    dispatcher = sinks.dispatcher_for(store, [spec])
    assert dispatcher is not None
    dispatcher.register()

    manager = JobManager(JobStore.for_store(store), cancel_poll=0.02, heartbeat=0.05)
    job = manager.submit(JobKind.SYNC, {"mode": "details"}, lambda handle: None, origin=local_origin("cli"),
                         background=False)
    report = sinks.drain_at_exit(dispatcher)
    assert report is not None and report.complete and report.delivered == 2
    written = lines_of(path)
    assert [doc["type"] for doc in written] == ["job.started", "job.finished"]
    assert [doc["stream"] for doc in written] == ["job", "job"] and written[0]["source"] == "job"
    assert written[0]["data"]["job_id"] == job.id == written[1]["data"]["job_id"]
    assert written[1]["data"]["state"] == "succeeded" and written[1]["data"]["error_code"] is None
    assert written[0]["seq"] < written[1]["seq"]


def test_drain_at_exit_does_nothing_without_sinks_and_never_raises(store: Store, monkeypatch: pytest.MonkeyPatch,
                                                                   caplog: pytest.LogCaptureFixture):
    assert sinks.dispatcher_for(store, []) is None and sinks.drain_at_exit(None) is None
    probe = RecordingSink()
    dispatcher = make(store, probe)
    monkeypatch.setattr(dispatcher, "drain", lambda timeout: (_ for _ in ()).throw(StoreBusy("busy")))
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        assert sinks.drain_at_exit(dispatcher) is None
    assert probe.closed and "could not be delivered at exit (StoreBusy)" in caplog.text


def wait_until(condition: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


@pytest.fixture
def running() -> Iterator[List[Any]]:
    """`run` döngülerini thread'de çalıştıran testler: (stop, thread) çiftleri test bitince durdurulur."""
    started: List[Any] = []
    yield started
    for stop, thread in started:
        stop.set()
        thread.join(timeout=10)
        assert not thread.is_alive()


def start(dispatcher: Dispatcher, running: List[Any], **kwargs: Any) -> threading.Event:
    stop = threading.Event()
    thread = threading.Thread(target=dispatcher.run, args=(stop,), kwargs=kwargs, daemon=True)
    thread.start()
    running.append((stop, thread))
    return stop


def test_run_delivers_events_as_they_are_appended_and_holds_the_lease(store: Store, running: List[Any]):
    probe = RecordingSink()
    dispatcher = Dispatcher(store, [probe])
    stop = start(dispatcher, running)
    wait_until(lambda: cursor_row(store, "probe") is not None)  # kilit alındı, sink kaydedildi
    assert store.lease_holder("sinks").purpose == "dispatcher"
    first = emit(store)
    wait_until(lambda: probe.seqs == [first])
    second = emit(store)
    wait_until(lambda: probe.seqs == [first, second])
    stop.set()
    running[0][1].join(timeout=10)
    assert store.lease_holder("sinks") is None
    assert cursor_row(store, "probe")["seq"] == second


def test_run_flushes_a_lingering_batch_when_it_stops(store: Store, running: List[Any]):
    probe = RecordingSink(batch_size=50, linger_seconds=3600.0)
    dispatcher = Dispatcher(store, [probe])
    stop = start(dispatcher, running)
    wait_until(lambda: cursor_row(store, "probe") is not None)
    seq = emit(store)
    time.sleep(0.1)
    assert probe.seqs == []  # toplu gönderim dolmadı, süre de dolmadı
    stop.set()
    running[0][1].join(timeout=10)
    assert probe.seqs == [seq]


def test_run_waits_for_the_lease_and_takes_over(store: Store, running: List[Any], monkeypatch: pytest.MonkeyPatch,
                                                caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr(dispatcher_mod, "LEASE_RETRY_SECONDS", 0.05)
    probe = RecordingSink()
    dispatcher = Dispatcher(store, [probe])
    dispatcher.register()
    seq = emit(store)
    with caplog.at_level(logging.INFO, logger="Sinks"):
        with store.lease("sinks", purpose="dispatcher"):  # `serve` dağıtıyor
            start(dispatcher, running)
            time.sleep(0.3)
            assert probe.attempts == []
        wait_until(lambda: probe.seqs == [seq])
    assert caplog.text.count("already dispatches on this data directory") == 1


def test_run_does_not_spin_while_a_sink_waits_for_its_retry(store: Store, running: List[Any]):
    """Yeniden deneme bekleyen sink günlüğü okumaz; bekleyen olaylar döngüyü boşa döndürmemeli."""
    probe = RecordingSink()
    probe.fail_always = RetryableSinkError("down")
    dispatcher = Dispatcher(store, [probe], jitter=lambda: 1.0)
    dispatcher.register()
    emit(store)
    steps: List[float] = []
    real_step = dispatcher.step
    dispatcher.step = lambda **kwargs: steps.append(time.monotonic()) or real_step(**kwargs)  # type: ignore[method-assign]
    start(dispatcher, running, flush_timeout=0)
    wait_until(lambda: len(probe.attempts) >= 1)
    emit(store)
    time.sleep(0.6)
    assert len(steps) < 15, len(steps)  # en çok IDLE_WAIT_SECONDS aralıklarla ve yeni olayda


def test_run_stops_after_the_delivery_in_progress(store: Store):
    """Durdurma her teslimden sonra sorulur: bekleyen toplu gönderimler kapanıştaki süreye kalır."""
    stop = threading.Event()

    class StopsWhileDelivering(RecordingSink):
        def deliver(self, batch: Sequence[Envelope]) -> None:
            super().deliver(batch)
            stop.set()  # servis bu teslim sürerken durduruldu

    probe = StopsWhileDelivering(batch_size=1)
    dispatcher = Dispatcher(store, [probe])
    dispatcher.register()
    seqs = [emit(store) for _ in range(4)]
    dispatcher.run(stop, flush_timeout=0)
    assert probe.seqs == seqs[:1] and cursor_row(store, "probe")["seq"] == seqs[0]
    assert store.lease_holder("sinks") is None
    stop_now = threading.Event()

    class StopsAtOnce(RecordingSink):
        def deliver(self, batch: Sequence[Envelope]) -> None:
            super().deliver(batch)
            stop_now.set()

    again = StopsAtOnce(batch_size=1)
    Dispatcher(store, [again]).run(stop_now)  # varsayılan kapanış süresi: kalanlar durdurulduktan sonra teslim edilir
    assert again.seqs == seqs[1:]


def test_run_survives_errors_of_the_event_log_and_unexpected_errors(store: Store, running: List[Any],
                                                                    monkeypatch: pytest.MonkeyPatch,
                                                                    caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr(dispatcher_mod, "BACKOFF_FIRST_SECONDS", 0.02)
    monkeypatch.setattr(dispatcher_mod, "ERROR_RETRY_SECONDS", 0.02)
    probe = RecordingSink()
    dispatcher = Dispatcher(store, [probe])
    failures: List[Exception] = [sqlite3.OperationalError("database is locked"), StoreBusy("busy"),
                                 RuntimeError("unexpected")]
    real_step = dispatcher.step

    def step(**kwargs: Any) -> float:
        if failures:
            raise failures.pop(0)
        return real_step(**kwargs)

    dispatcher.step = step  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="Sinks"):
        start(dispatcher, running)
        wait_until(lambda: not failures and cursor_row(store, "probe") is not None)
        seq = emit(store)
        wait_until(lambda: probe.seqs == [seq])  # üç hatadan sonra döngü hâlâ dağıtıyor
    assert store.lease_holder("sinks") is not None and running[0][1].is_alive()
    assert "The event log could not be read (OperationalError)" in caplog.text
    assert "The event log could not be read (StoreBusy)" in caplog.text
    assert "The dispatcher failed unexpectedly (RuntimeError); trying again" in caplog.text


def test_follow_prints_new_events_without_the_lease_and_without_writing(store: Store):
    emit(store)
    lines: List[str] = []
    out = StdoutSink(events=EventFilter(["live.*"]), write=lines.append)
    dispatcher = Dispatcher(store, [out])
    stop = threading.Event()
    with store.lease("sinks", purpose="dispatcher"):  # kilit başkasında: izleme yine çalışır
        thread = threading.Thread(target=dispatcher.follow, args=(stop,), daemon=True)
        thread.start()
        wait_until(lambda: dispatcher._started)  # başlangıç konumu ("şimdi") alındı
        seq = emit(store)
        emit(store, "job.started", source="job")
        wait_until(lambda: len(lines) == 1)
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and [json.loads(line)["seq"] for line in lines] == [seq]  # "şimdi"den
    assert cursor_row(store, "stdout") is None and store.runtime.get("sink:stdout") is None
    with pytest.raises(ValueError, match="without a cursor"):
        Dispatcher(store, [RecordingSink()]).follow(stop)


def test_run_returns_at_once_when_already_stopped(store: Store):
    probe = RecordingSink()
    stop = threading.Event()
    stop.set()
    Dispatcher(store, [probe]).run(stop)
    assert store.lease_holder("sinks") is None and cursor_row(store, "probe") is None


# === gizli değerler ==============================================================================

def fake(*words: str) -> str:
    """
    Gizli bir değerin yerini tutan sınama değeri: bilerek sahte ve tahmin edilebilir ("fake-..."), çalışırken
    parçalardan kurulur. Depoda gizli değere benzeyen bir sabit durmaz; testler bu metinleri loglarda, hata
    metinlerinde ve çıktılarda arar ve hiçbirinde bulmamalıdır.
    """
    return "-".join(("fake", *words))


SECRET = fake("signing", "value")
HOOK_USER, HOOK_PASS = fake("user"), fake("pass", "word")
HOOK_PATH_PART, HOOK_QUERY_PART = fake("path", "part"), fake("query", "part")
# Kullanıcı adı ve parola taşıyan, yolu ve sorgusu belirteç olan bir webhook adresi (hepsi sahte)
HOOK_URL = urllib.parse.urlunsplit((
    "https", "@".join((":".join((HOOK_USER, HOOK_PASS)), "hooks.example.org")),
    f"/services/T000/B000/{HOOK_PATH_PART}", f"sig={HOOK_QUERY_PART}", "",
))
PRIVATE_PARTS = (SECRET, HOOK_USER, HOOK_PASS, HOOK_PATH_PART, HOOK_QUERY_PART, "/services/")


def test_webhook_failures_never_put_the_secret_or_the_address_anywhere(store: Store, clock: FakeClock,
                                                                      caplog: pytest.LogCaptureFixture):
    def refuse(url: str, body: bytes, headers: Any, timeout: float) -> Any:
        raise ConnectionRefusedError(111, f"Connection refused to {url} with {SECRET}")

    sink = WebhookSink("ops", HOOK_URL, secret=SECRET, max_age_seconds=DAY, linger_seconds=0, clock=clock,
                       transport=refuse)
    dispatcher = make(store, sink, clock=clock)
    with caplog.at_level(logging.DEBUG):
        dispatcher.register()
        emit(store, ts=clock.time() - 2 * DAY)
        emit(store, ts=clock.time())
        dispatcher.step()
        clock.advance(1)
        dispatcher.step()
    row = cursor_row(store, "ops")
    assert row["last_error"] == "network error (ConnectionRefusedError: Connection refused to *** with ***)"
    everything = json.dumps({
        "logs": caplog.text,
        "cursor": row,
        "events": [dict(record.data) for record in store.streams.read().events],
        "runtime": store.runtime.get("sink:ops").value,
        "status": [status.to_dict() for status in dispatcher.status()],
        "repr": [repr(sink), repr(dispatcher.status())],
    })
    for private in PRIVATE_PARTS:
        assert private not in everything, private
    assert len(system_events(store)) == 1 and "ops" in caplog.text  # sink adıyla anılır


def test_an_unexpected_webhook_error_is_recorded_by_its_class_only(store: Store, clock: FakeClock,
                                                                  caplog: pytest.LogCaptureFixture):
    """Ağ hatası olmayan bir istisnanın metni adresi taşıyabilir: hiçbir yere yazılmaz."""
    def broken(url: str, body: bytes, headers: Any, timeout: float) -> Any:
        raise ValueError(f"cannot send to {url} with {headers} and {SECRET}")

    sink = WebhookSink("ops", HOOK_URL, secret=SECRET, linger_seconds=0, clock=clock, transport=broken)
    dispatcher = make(store, sink, clock=clock)
    with caplog.at_level(logging.DEBUG):
        dispatcher.register()
        emit(store, ts=clock.time())
        assert dispatcher.step() == 1.0
    assert cursor_row(store, "ops")["last_error"] == "internal error (ValueError)"
    everything = json.dumps({"logs": caplog.text, "cursor": cursor_row(store, "ops"),
                             "status": [status.to_dict() for status in dispatcher.status()]})
    for private in PRIVATE_PARTS + ("Basic ", "hooks.example.org/"):
        assert private not in everything, private
    with pytest.raises(RetryableSinkError) as caught:
        sink.deliver([env_of(seq=9)])
    assert caught.value.__cause__ is None and caught.value.__suppress_context__  # asıl istisna zincire eklenmez


def test_every_part_of_a_webhook_address_is_scrubbed_from_error_texts(clock: FakeClock):
    """İşletim sisteminin iletisi adresin tek bir parçasını taşısa da (yol bölümü, sorgu değeri, parola) yazılmaz."""
    user, word = "us@er", "p:ss w0rd"  # yüzde kodlaması gereken karakterlerle, sahte değerler
    quoted = [urllib.parse.quote(part, safe="") for part in (user, word)]
    url = HOOK_URL.replace(HOOK_USER, quoted[0]).replace(HOOK_PASS, quoted[1])
    seen: List[str] = []

    def refuse(target: str, body: bytes, headers: Any, timeout: float) -> Any:
        seen.append(target)
        raise ConnectionRefusedError(111, " | ".join(leaks))

    sink = WebhookSink("ops", url, secret=SECRET, clock=clock, transport=refuse)
    leaks = [HOOK_PATH_PART, HOOK_QUERY_PART, *quoted, user, word, "T000", SECRET, url]
    with pytest.raises(RetryableSinkError) as caught:
        sink.deliver([env_of(seq=1)])
    assert str(caught.value) == "network error (ConnectionRefusedError: " + " | ".join(["***"] * len(leaks)) + ")"
    assert seen == [HOOK_URL.replace(f"{HOOK_USER}:{HOOK_PASS}@", "")]  # istek kullanıcı bilgisi olmadan gider
    assert all(part not in repr(sink) for part in leaks)


# === yapılandırmadan kurulum =====================================================================


def spec(**fields: Any) -> SinkSpec:
    options = fields.pop("options", {})
    return SinkSpec(options=MappingProxyType(dict(options)), **fields)


def test_sinks_are_built_from_the_config_file(tmp_path: Path, data_dir: Path):
    config = tmp_path / "sofascore.toml"
    config.write_text(
        'schema = 1\n'
        '[[sink]]\nname = "ops"\ntype = "webhook"\nurl = "https://example.org/hooks/sofascore"\n'
        'secret_env = "SOFASCORE_HOOK_SECRET"\nevents = ["live.*", "job.finished"]\nbatch_size = 50\n'
        'flush_seconds = 0.5\nmax_age = "6h"\ntimeout_seconds = 3\nsports = ["football"]\ntournaments = [17, 8]\n'
        '[[sink]]\nname = "feed"\ntype = "file"\npath = "out/live.ndjson"\nevents = ["live.*"]\n'
        'rotate_daily = true\nrotate_size = "50MB"\nkeep = 14\n'
        '[[sink]]\nname = "console"\ntype = "stdout"\n',
        encoding="utf-8",
    )
    loaded = loader.load_settings(config_file=str(config), environ={}, dotenv_values={})
    built = sinks.build_sinks(loaded.settings.sinks, environ={"SOFASCORE_HOOK_SECRET": SECRET}, data_dir=str(data_dir))
    ops, feed, console = built
    assert isinstance(ops, WebhookSink) and ops.signed and ops.host == "example.org"
    assert (ops.batch_size, ops.linger_seconds, ops.max_age_seconds, ops.timeout_seconds) == (50, 0.5, 6 * 3600.0, 3.0)
    assert ops.filter.patterns == ("live.*", "job.finished")
    assert ops.filter.sports == {"football"} and ops.filter.tournament_ids == {17, 8}
    # Göreli yol yapılandırma dosyasının dizinine göre çözülür (src/config/loader.py)
    assert isinstance(feed, FileSink) and skeleton.same_path(feed.path, tmp_path / "out" / "live.ndjson")
    assert (feed.rotate_daily, feed.rotate_bytes, feed.keep) == (True, 50 * 1024 ** 2, 14)
    assert isinstance(console, StdoutSink) and console.filter.patterns == ("*",)


def test_sink_defaults():
    hook = sinks.build_sink(spec(name="ops", type="webhook", url="http://127.0.0.1:9/hook", allow_unsigned=True))
    assert isinstance(hook, WebhookSink) and not hook.signed
    assert (hook.batch_size, hook.linger_seconds, hook.max_age_seconds, hook.timeout_seconds) == (20, 1.0, DAY, 10.0)
    feed = sinks.build_sink(spec(name="feed", type="file", path="/tmp/x/live.ndjson"))
    assert isinstance(feed, FileSink) and (feed.rotate_daily, feed.rotate_bytes, feed.keep) == (False, 0, 0)
    assert sinks.build_sink(spec(name="f", type="file", path="/x/y", options={"rotate_size": 2048})).rotate_bytes == 2048
    assert sinks.build_sink(
        spec(name="o", type="webhook", url="http://h/x", allow_unsigned=True, options={"max_age": 90})
    ).max_age_seconds == 90.0


HOOK = {"name": "ops", "type": "webhook", "url": "https://example.org/hook", "secret_env": "HOOK_SECRET"}
FEED = {"name": "feed", "type": "file", "path": "/var/out/live.ndjson"}


@pytest.mark.parametrize("fields, options, message", [
    (HOOK, {"batchsize": 5}, r"\[\[sink\]\] 'ops' batchsize: unknown key for a webhook sink \(known: name, type, events, "
                             r"sports, tournaments, batch_size, flush_seconds, max_age, timeout_seconds\)"),
    (HOOK, {"rotate_daily": True}, "rotate_daily: unknown key for a webhook sink"),
    (FEED, {"batch_size": 5}, "batch_size: unknown key for a file sink"),
    ({"name": "c", "type": "stdout"}, {"keep": 1}, "keep: unknown key for a stdout sink"),
    (HOOK, {"batch_size": 0}, "batch_size: expected a number between 1 and 100"),
    (HOOK, {"batch_size": 101}, "batch_size: expected a number between 1 and 100"),
    (HOOK, {"batch_size": True}, "batch_size: expected a number between 1 and 100"),
    (HOOK, {"flush_seconds": -1}, "flush_seconds: expected seconds between 0 and 60"),
    (HOOK, {"flush_seconds": "1s"}, "flush_seconds: expected seconds between 0 and 60"),
    (HOOK, {"timeout_seconds": 0}, "timeout_seconds: expected seconds above 0 and at most 120"),
    (HOOK, {"max_age": "soon"}, 'max_age: expected a duration such as "24h" or a number of seconds'),
    (HOOK, {"max_age": 0}, "max_age: expected a duration"),
    (HOOK, {"sports": "football"}, "sports: expected a list of sport names"),
    (HOOK, {"tournaments": ["17"]}, "tournaments: expected a list of tournament ids"),
    (FEED, {"rotate_daily": "yes"}, "rotate_daily: expected true or false"),
    (FEED, {"rotate_size": "big"}, 'rotate_size: expected a size such as "50MB" or a number of bytes'),
    (FEED, {"rotate_size": 100}, "rotate_size: expected at least 1024 bytes"),
    (FEED, {"keep": -1}, "keep: expected a number of files, 0 or more"),
])
def test_sink_options_are_checked(fields: Dict[str, Any], options: Dict[str, Any], message: str):
    with pytest.raises(ConfigError, match=message):
        sinks.build_sink(spec(options=options, **fields), environ={"HOOK_SECRET": SECRET})


def test_a_webhook_needs_its_secret_in_the_environment():
    with pytest.raises(ConfigError, match="secret_env: the environment variable HOOK_SECRET is not set"):
        sinks.build_sink(spec(**HOOK), environ={})
    with pytest.raises(ConfigError, match="the environment variable HOOK_SECRET is not set"):
        sinks.build_sink(spec(**HOOK, allow_unsigned=True), environ={"HOOK_SECRET": "  "})  # imza sessizce kapanmaz
    with pytest.raises(ConfigError, match=r"a webhook needs secret_env \(or allow_unsigned = true\)"):
        sinks.build_sink(spec(name="ops", type="webhook", url="https://example.org/hook"))
    with pytest.raises(ConfigError, match="a webhook needs an http:// or https:// address"):
        sinks.build_sink(spec(name="ops", type="webhook", url="file:///etc/passwd", allow_unsigned=True))


def test_the_secret_variable_must_have_a_name_that_is_masked(monkeypatch: pytest.MonkeyPatch):
    """Tanılama paketi ve log maskelemesi gizli değerleri değişkenin adından tanır (src/redact.py)."""
    from src import redact

    with pytest.raises(ConfigError, match="secret_env: the variable name must look like a secret"):
        sinks.build_sink(spec(**{**HOOK, "secret_env": "SOFASCORE_HOOK"}), environ={"SOFASCORE_HOOK": SECRET})
    for name in ("SOFASCORE_HOOK_SECRET", "HOOK_TOKEN", "WEBHOOK_KEY", "HOOK_PASSWORD"):
        sink = sinks.build_sink(spec(**{**HOOK, "secret_env": name}), environ={name: SECRET})
        assert redact.mask_value(name, SECRET) == "***" and SECRET not in repr(sink)


def test_the_diagnostics_bundle_masks_the_secret_of_an_accepted_variable_name(monkeypatch: pytest.MonkeyPatch):
    from src import diagnostics

    monkeypatch.setenv("SOFASCORE_HOOK_SECRET", SECRET)
    settings = json.dumps(diagnostics._settings())
    assert SECRET not in settings and '"SOFASCORE_HOOK_SECRET": "***"' in settings


def test_the_secret_is_read_from_the_process_environment_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOOK_SECRET", SECRET)
    assert sinks.build_sink(spec(**HOOK)).signed


def test_a_file_sink_cannot_write_inside_the_data_directory(store: Store, tmp_path: Path):
    inside = spec(name="feed", type="file", path=str(store.data_dir / "out" / "live.ndjson"))
    with pytest.raises(ConfigError, match="path: a file sink cannot write inside the data directory"):
        sinks.dispatcher_for(store, [inside])
    with pytest.raises(ConfigError, match="cannot write inside the data directory"):
        sinks.build_sink(spec(name="feed", type="file", path=str(store.data_dir)), data_dir=str(store.data_dir))
    beside = spec(name="feed", type="file", path=str(tmp_path / "data-out" / "live.ndjson"))
    assert sinks.dispatcher_for(store, [beside]) is not None  # adı benzeyen komşu dizin içeride sayılmaz
    with pytest.raises(ConfigError, match="a file sink needs a path"):
        sinks.build_sink(spec(name="feed", type="file"))


def test_sink_names_are_unique_and_types_known():
    with pytest.raises(ConfigError, match="'feed': the name is already used by sink #1"):
        sinks.build_sinks([spec(**FEED), spec(**FEED)])
    with pytest.raises(ConfigError, match="unknown sink type 'kafka'"):
        sinks.build_sink(spec(name="q", type="kafka"))
    with pytest.raises(ConfigError, match="'feed' events: expected at least one event type"):
        sinks.build_sink(spec(**FEED, events=()))


def test_the_package_root_is_light_and_loads_sinks_on_first_use(box: skeleton.Sandbox):
    code = (
        "import sys, src.sinks; "
        "heavy = [m for m in ('src.sinks.dispatcher', 'src.sinks.webhook', 'src.sinks.file', 'src.store.api', "
        "'src.redact', 'urllib.request') if m in sys.modules]; "
        "from src.sinks import Dispatcher, WebhookSink; print(heavy, 'src.sinks.webhook' in sys.modules)"
    )
    run = box.run(code=code)
    assert run.exit_code == 0, run.stderr
    assert run.stdout.strip() == "[] True"
    with pytest.raises(AttributeError):
        sinks.NoSuchName  # noqa: B018


# === `ssc events` ================================================================================


@pytest.fixture
def log_dir(data_dir: Path) -> Path:
    """Dört olaylı bir günlük: iki canlı olay (iki ayrı maç), bir iş olayı, bir sistem olayı."""
    opened = open_store(data_dir)
    emit(opened, "live.status_changed", event_id=101, sport="football", tournament_id=17, ts=1790856421.5,
         data={"from": "live", "to": "completed"})
    emit(opened, "job.started", source="job", data={"job_id": "01J"})
    emit(opened, "live.score_changed", event_id=102, sport="football", data={"scores": [1, 0]})
    emit(opened, "system.blocked", source="system", data={})
    return data_dir


def stream_lines(run: skeleton.Run) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in run.stdout.splitlines()]


def test_events_prints_the_log_as_json_lines_and_ends_with_an_end_line(cli: CliRunner, log_dir: Path):
    run = cli("events", "--data-dir", log_dir)
    assert (run.exit_code, run.stderr) == (0, "")
    documents = stream_lines(run)
    assert all("type" in document for document in documents)
    assert [document["type"] for document in documents] == [
        "live.status_changed", "job.started", "live.score_changed", "system.blocked", "end"]
    assert documents[0] == {
        "stream": "live", "seq": 1, "type": "live.status_changed", "ts": "2026-10-01T12:07:01.500Z", "event_id": 101,
        "sport": "football", "tournament_id": 17, "source": "poll", "data": {"from": "live", "to": "completed"},
    }
    stream_id = open_store(log_dir).streams.head().stream_id
    assert documents[-1] == {"type": "end", "stream_id": stream_id, "last_seq": 4, "count": 4, "gap": False}


def test_events_ndjson_mode_prints_no_envelope(cli: CliRunner, log_dir: Path):
    text, ndjson = cli("events", "--data-dir", log_dir), cli("events", "--data-dir", log_dir, "--output", "ndjson")
    assert ndjson.stdout == text.stdout and ndjson.stderr == ""
    assert not any("ok" in document for document in stream_lines(ndjson))


def test_events_json_mode_is_one_document(cli: CliRunner, log_dir: Path):
    run = cli("events", "--data-dir", log_dir, "--stream", "job", "--json")
    assert run.exit_code == 0
    data = run.data
    assert (data["count"], data["last_seq"], data["gap"]) == (1, 4, False)
    assert [event["type"] for event in data["events"]] == ["job.started"]
    assert data["events"][0] == stream_lines(cli("events", "--data-dir", log_dir, "--stream", "job"))[0]


def test_events_filters(cli: CliRunner, log_dir: Path):
    def types(*args: str) -> List[str]:
        return [document["type"] for document in stream_lines(cli("events", "--data-dir", log_dir, *args))[:-1]]

    assert types("--stream", "live") == ["live.status_changed", "live.score_changed"]
    assert types("--stream", "live", "--stream", "system") == ["live.status_changed", "live.score_changed",
                                                               "system.blocked"]
    assert types("--type", "live.*") == ["live.status_changed", "live.score_changed"]
    assert types("--type", "job.started", "--type", "system.blocked") == ["job.started", "system.blocked"]
    assert types("--type", "*.s*") == ["live.status_changed", "job.started", "live.score_changed"]
    assert types("--event", "102") == ["live.score_changed"]
    assert types("--event", "101", "--event", "102", "--type", "live.score_changed") == ["live.score_changed"]
    assert types("--type", "nothing.*") == []
    assert types("--after", "2") == ["live.score_changed", "system.blocked"]
    assert types("--after", "99") == []


def test_events_limit_stops_at_the_last_printed_event_so_that_after_resumes(cli: CliRunner, log_dir: Path):
    first = stream_lines(cli("events", "--data-dir", log_dir, "--type", "live.*", "--limit", "1"))
    assert [document["type"] for document in first] == ["live.status_changed", "end"]
    assert (first[-1]["last_seq"], first[-1]["count"]) == (1, 1)
    rest = stream_lines(cli("events", "--data-dir", log_dir, "--type", "live.*", "--after", first[-1]["last_seq"]))
    assert [document["seq"] for document in rest[:-1]] == [3] and rest[-1]["last_seq"] == 4


def test_events_reads_a_long_log_in_pages(cli: CliRunner, data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(events_command, "PAGE", 3)
    opened = open_store(data_dir)
    seqs = [emit(opened, "live.x" if n % 2 else "job.x") for n in range(10)]
    documents = stream_lines(cli("events", "--data-dir", data_dir, "--type", "live.*"))
    assert [document["seq"] for document in documents[:-1]] == seqs[1::2]
    assert documents[-1]["last_seq"] == seqs[-1] and documents[-1]["count"] == 5


def test_events_reports_a_gap_after_pruning(cli: CliRunner, data_dir: Path):
    opened = open_store(data_dir)
    for _ in range(3):
        emit(opened, ts=time.time() - 30 * DAY)
    kept = emit(opened)
    opened.streams.prune(max_age_s=7 * DAY)
    run = cli("events", "--data-dir", data_dir, "--after", "1")
    documents = stream_lines(run)
    assert [document["seq"] for document in documents[:-1]] == [kept] and documents[-1]["gap"] is True
    assert run.stderr == ("Warning: events after sequence 1 were pruned from the event log before they were read; "
                          "some are missing\n")
    as_json = cli("events", "--data-dir", data_dir, "--after", "1", "--json").json
    assert as_json["data"]["gap"] is True and [w["code"] for w in as_json["warnings"]] == ["stream_gap"]
    assert stream_lines(cli("events", "--data-dir", data_dir, "--after", kept))[-1]["gap"] is False


def test_events_on_a_folder_that_is_not_a_store_is_empty_and_creates_nothing(cli: CliRunner, tmp_path: Path):
    empty = tmp_path / "fresh"
    run = cli("events", "--data-dir", empty)
    assert run.exit_code == 0
    assert stream_lines(run) == [{"type": "end", "stream_id": None, "last_seq": 0, "count": 0, "gap": False}]
    assert run.stderr.startswith("The data folder has no event log yet: ") and str(empty) in run.stderr
    assert cli("events", "--data-dir", empty, "--after", "7", "--json").data == {
        "stream_id": None, "last_seq": 7, "count": 0, "gap": False, "events": []}
    assert not empty.exists()
    assert "Veri klasöründe henüz bir olay günlüğü yok" in cli("events", "--data-dir", empty, "--lang", "tr").stderr
    follow = cli("events", "--data-dir", empty, "--follow", "--output", "ndjson")
    assert follow.exit_code == 5 and json.loads(follow.stdout)["error"]["code"] == "storage_error"


def test_events_takes_no_lease_and_writes_no_cursor(cli: CliRunner, log_dir: Path):
    opened = open_store(log_dir)
    with opened.lease("sinks"), opened.lease("writer"):
        assert cli("events", "--data-dir", log_dir).exit_code == 0
    assert opened._state.connection().execute("SELECT count(*) FROM sink_cursors").fetchone()[0] == 0
    assert opened.info(sizes=False).leases == ()


@pytest.mark.parametrize("argv, message", [
    (["--follow", "--json"], "--follow writes a stream of lines"),
    (["--after", "-1"], "argument --after: expected a sequence number"),
    (["--after", "x"], "argument --after: expected a sequence number"),
    (["--limit", "0"], "argument --limit: expected a number of events"),
    (["--stream", "nope"], "argument --stream: invalid choice"),
    (["--event", "abc"], "argument --event: invalid int value"),
    (["--type", ""], "--type: expected an event type or a pattern"),
])
def test_events_usage_errors(cli: CliRunner, log_dir: Path, argv: List[str], message: str):
    run = cli("events", "--data-dir", log_dir, *argv, "--json")
    assert run.exit_code == 2 and run.error["code"] == "invalid_request" and message in run.error["message"]


def test_events_is_described_with_the_options_of_the_design(cli: CliRunner):
    described_commands = cli("describe", "commands").data["commands"]["commands"]
    commands = {command["name"]: command for command in described_commands}
    described = commands["events"]
    assert described["loads_settings"] and not described["always_json"]
    assert [option["flags"] for option in described["options"]] == [
        ["--stream"], ["--after"], ["--follow"], ["--type"], ["--event"], ["--limit"]]
    assert described["options"][0]["choices"] == ["live", "change", "job", "system"]
    assert "events" in cli("--help").stdout and "olay günlüğündeki" in cli("--help", "--lang", "tr").stdout


FOLLOWER = """
import signal, sys
from src.cli.main import main
signal.signal(signal.SIGINT, signal.default_int_handler)  # testi çalıştıran kabuk SIGINT'i yok saydırmış olabilir
sys.exit(main(["events", "--data-dir", sys.argv[1], "--follow", *sys.argv[2:]]))
"""


def follow(box: skeleton.Sandbox, data_dir: Path, *args: str) -> "subprocess.Popen[str]":
    return subprocess.Popen(
        [sys.executable, "-c", FOLLOWER, str(data_dir), *args], cwd=box.cwd, env=box.environ(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_events_follow_starts_at_now_and_prints_events_of_another_process(box: skeleton.Sandbox, log_dir: Path):
    opened = open_store(log_dir)
    proc = follow(box, log_dir, "--type", "live.*", "--limit", "2")
    try:
        # İzleyici "şimdi"den başlar: eski dört olayı yazmaz. Başladığını bilmenin yolu yok; olaylar, izleyici
        # ikisini de görene kadar aralıklarla eklenir.
        deadline = time.monotonic() + 60
        while proc.poll() is None and time.monotonic() < deadline:
            emit(opened, "job.started", source="job")
            emit(opened, "live.status_changed", event_id=7)
            time.sleep(0.2)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    documents = [json.loads(line) for line in out.splitlines()]
    assert [document["type"] for document in documents] == ["live.status_changed", "live.status_changed", "end"]
    assert documents[0]["seq"] > 4 and documents[-1]["count"] == 2
    assert documents[-1]["last_seq"] == documents[1]["seq"]


def test_events_follow_resumes_after_a_sequence_number(box: skeleton.Sandbox, log_dir: Path):
    proc = follow(box, log_dir, "--after", "2", "--limit", "2")
    try:
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    assert [json.loads(line).get("seq") for line in out.splitlines()] == [3, 4, None]


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT cannot be sent to a child process on Windows")
def test_events_follow_stops_cleanly_on_ctrl_c(box: skeleton.Sandbox, log_dir: Path):
    opened = open_store(log_dir)
    proc = follow(box, log_dir)
    try:
        first = None
        deadline = time.monotonic() + 60
        while first is None and time.monotonic() < deadline:
            emit(opened, "live.status_changed", event_id=9)
            ready = _readable(proc.stdout, 0.2)
            if ready:
                first = proc.stdout.readline()
        assert first and json.loads(first)["type"] == "live.status_changed"
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    assert all(json.loads(line)["type"] != "end" for line in out.splitlines())  # durdurulan izleme `end` yazmaz
    assert "Traceback" not in err


def _readable(stream: Any, timeout: float) -> bool:
    return bool(select.select([stream], [], [], timeout)[0])
