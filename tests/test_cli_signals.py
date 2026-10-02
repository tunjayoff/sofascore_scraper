"""
CLI'nin sinyalleri (plan maddesi P19; docs/design/02-services.md bölüm 4.6, karar D6).

  * Bir iş komutu (sync, fetch, refresh) SIGINT ya da SIGTERM alınca işi iptal eder: servis bir sonraki iptal
    denetiminde durur, iş satırı `cancelled` olur, kilit bırakılır, sonuç yine yazılır; çıkış kodu 130 / 143.
  * İkinci sinyal süreci hemen bitirir; yarım kalan iş satırını sonra okuyan `interrupted` yapar.
  * İş dışındaki bir komut SIGTERM'de Ctrl+C gibi temizlenir: 143. Akış komutu (`events --follow`) durdurulmayı
    başarı sayar: 0.

Sinyal testleri gerçek bir alt süreçte koşar: süreç, servisi sahtesiyle değiştiren küçük bir betikle başlar (ağ
yok). Windows'ta sinyal gönderimi farklıdır (CTRL_C_EVENT konsol grubuna gider): orada yalnızca birim testleri koşar.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from src.cli import signals
from src.jobs.manager import JobManager
from src.jobs.model import JobState
from src.store import JobStore, open_store
from test_cli_skeleton import Sandbox

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX sinyalleri gerekir")

REPO = Path(__file__).resolve().parents[1]
READY = "fake-service-ready"

# Servisi sahtesiyle değiştirip CLI'yi çalıştıran betik. MODE: "cooperative" iptali görünce durur; "stubborn"
# iptale bakmaz (ikinci sinyal testleri için); "status" durum komutunu yavaşlatır (iş dışındaki komut).
_SCRIPT = """
import sys, time
MODE = sys.argv[1]
# Servis modülleri burada, CLI'den önce yüklenir: log satırları CLI'deki gibi baştan stderr'e gitsin
from src import logger
logger.set_console_stream("stderr")

def wait(cancelled):
    sys.stderr.write("%(ready)s\\n"); sys.stderr.flush()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if cancelled is not None and cancelled():
            return
        time.sleep(0.02)

if MODE == "status":
    from src.services.status import StatusService
    real = StatusService.summary
    def summary(self, **kwargs):
        wait(None)
        return real(self, **kwargs)
    StatusService.summary = summary
else:
    from src.services.sync import SyncResult, SyncService
    def run(self, spec, *, handle=None):
        handle.progress.start_phase("details", 1)
        wait(handle.cancelled if MODE == "cooperative" else None)
        return SyncResult(state="cancelled", schedule_empty_seasons=0, breaker=None,
                          progress=handle.progress.result())
    SyncService.run = run

from src.cli.main import main
sys.exit(main(sys.argv[2:]))
""" % {"ready": READY}


def start(box: Sandbox, mode: str, *argv: str) -> "subprocess.Popen[bytes]":
    proc = subprocess.Popen(
        [sys.executable, "-c", _SCRIPT, mode, "--data-dir", str(box.root / "data"), *argv],
        cwd=box.cwd, env=box.environ(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stderr is not None
    seen: List[str] = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = proc.stderr.readline().decode("utf-8", "replace")
        if not line:
            break
        seen.append(line)
        if line.strip() == READY:
            return proc
    proc.kill()
    out, err = proc.communicate()
    pytest.fail(f"the command did not reach the service: {''.join(seen)}{err.decode()}")


def finish(proc: "subprocess.Popen[bytes]") -> Tuple[int, str, str]:
    out, err = proc.communicate(timeout=60)
    return proc.returncode, out.decode("utf-8"), err.decode("utf-8")


def jobs(box: Sandbox) -> List[Any]:
    store = open_store(box.root / "data")
    try:
        return JobManager(JobStore.for_store(store)).list()
    finally:
        store.close()


@pytest.fixture
def box(tmp_path: Path) -> Sandbox:
    return Sandbox.create(tmp_path / "box")


# --- iş komutu: iptal, sonuç, çıkış kodu ---------------------------------------------------------


@posix_only
@pytest.mark.parametrize("signum, code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_a_signal_cancels_the_job_prints_the_result_and_releases_the_lease(box: Sandbox, signum: int,
                                                                           code: int) -> None:
    proc = start(box, "cooperative", "sync", "--json")
    proc.send_signal(signum)
    exit_code, out, err = finish(proc)

    assert exit_code == code, err
    document = json.loads(out)  # sonuç yine yazıldı
    assert document["ok"] is True and document["data"]["state"] == "cancelled"
    assert document["data"]["cancelled_by_signal"] == int(signum)
    assert "Stopping after the current step" in err and "Traceback" not in err
    (job,) = jobs(box)
    assert job.state is JobState.CANCELLED and job.cancel_requested is True
    store = open_store(box.root / "data")
    try:
        assert store.lease_holder("writer") is None  # kilit bırakıldı: sıradaki komut başlayabilir
    finally:
        store.close()


@posix_only
def test_text_mode_says_the_job_was_cancelled(box: Sandbox) -> None:
    proc = start(box, "cooperative", "refresh")
    proc.send_signal(signal.SIGINT)
    exit_code, out, err = finish(proc)
    assert exit_code == 130 and out == ""
    assert "Cancelled: job " in err and "stopped after its current step" in err


@posix_only
def test_a_second_signal_exits_at_once_and_the_row_is_reaped_later(box: Sandbox) -> None:
    proc = start(box, "stubborn", "sync", "--json")
    proc.send_signal(signal.SIGINT)
    time.sleep(0.3)
    proc.send_signal(signal.SIGINT)
    exit_code, out, _err = finish(proc)

    assert exit_code == 130 and out == ""  # sonuç yazılmadan çıkıldı
    (job,) = jobs(box)  # okuyan, sahibi ölmüş satırı süpürür
    assert job.state is JobState.INTERRUPTED


# --- iş dışındaki komutlar -----------------------------------------------------------------------


@posix_only
def test_sigterm_on_a_one_shot_command_exits_143(box: Sandbox) -> None:
    open_store(box.root / "data").close()
    proc = start(box, "status", "status", "--json")
    proc.send_signal(signal.SIGTERM)
    exit_code, out, err = finish(proc)
    assert exit_code == 143, err
    document = json.loads(out)
    assert document["ok"] is False and document["error"]["code"] == "cancelled" and document["exit_code"] == 143
    assert "SIGTERM" in document["error"]["message"] and "Traceback" not in err


@posix_only
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_a_following_stream_stops_with_0(box: Sandbox, signum: int) -> None:
    open_store(box.root / "data").close()
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.cli.main", "--data-dir", str(box.root / "data"), "events", "--follow"],
        cwd=box.cwd, env=box.environ(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(2.0)  # komut yüklendi ve izlemeye başladı
    proc.send_signal(signum)
    exit_code, out, err = finish(proc)
    assert exit_code == 0, err
    assert out == "" and "Traceback" not in err


# --- birim: CancelRequest ------------------------------------------------------------------------


def test_cancel_request_first_signal_flags_second_exits() -> None:
    exits: List[int] = []
    notes: List[int] = []
    cancel = signals.CancelRequest(on_first=notes.append, exit=exits.append)
    assert (cancel.requested, cancel.exit_code, cancel.cancelled()) == (False, None, False)

    cancel.handle(int(signal.SIGTERM))
    assert (cancel.requested, cancel.signal_number, cancel.exit_code, notes, exits) == (True, 15, 143, [15], [])
    cancel.handle(int(signal.SIGINT))
    assert exits == [143]  # ikinci sinyal: ilk sinyalin koduyla çıkış


def test_cancel_request_survives_a_failing_note() -> None:
    def broken(_signum: int) -> None:
        raise OSError("stderr closed")

    cancel = signals.CancelRequest(on_first=broken, exit=lambda code: None)
    cancel.handle(int(signal.SIGINT))
    assert cancel.exit_code == 130


def test_cancel_request_installs_and_restores_the_handlers() -> None:
    before = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    cancel = signals.CancelRequest(exit=lambda code: None)
    with cancel:
        assert signal.getsignal(signal.SIGINT) == cancel.handle
        assert signal.getsignal(signal.SIGTERM) == cancel.handle
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before


def test_handlers_are_left_alone_outside_the_main_thread() -> None:
    import threading

    installed: List[bool] = []
    cancel = signals.CancelRequest(exit=lambda code: None)
    thread = threading.Thread(target=lambda: installed.append(cancel.install()))
    thread.start()
    thread.join()
    assert installed == [False]
    with signals.terminate_as_interrupt():  # ana thread: SIGTERM bir KeyboardInterrupt olur
        assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM) if os.name != "nt" else signals._raise_terminated(15, None)
            time.sleep(1)


def test_exit_codes_of_signals() -> None:
    assert (signals.exit_code_of(signal.SIGINT), signals.exit_code_of(signal.SIGTERM)) == (130, 143)
    assert issubclass(signals.Terminated, KeyboardInterrupt)
