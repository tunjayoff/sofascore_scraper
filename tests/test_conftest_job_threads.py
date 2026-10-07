"""
`conftest.join_job_threads`: iş deposunu kapatan fixture'ların iş thread'lerini bekleme yardımcısı.

`threading.enumerate()` başlatılmakta olan thread'i de döndürür (`start()` çağrılmış, thread henüz koşmuyor). macOS
CI'da bir iş thread'inin başlattığı `job-ticker-…` tam bu andayken yardımcı ona `join()` dedi ve test RuntimeError
ile söküldü ("cannot join thread before it is started"). Buradaki test o anı belirlenimli kurar.
"""
from __future__ import annotations

import threading
import time

import conftest


class _SlowStart(threading.Thread):
    """Koşmaya başlamadan önce `gate`i bekleyen thread: `start()` çağrıldıktan sonra bir süre başlamamış kalır."""

    def __init__(self, gate: threading.Event) -> None:
        super().__init__(name="job-ticker-test", daemon=True)
        self._gate = gate

    def _bootstrap_inner(self) -> None:  # type: ignore[override]
        self._gate.wait(10)
        super()._bootstrap_inner()  # type: ignore[misc]


def test_a_job_thread_that_is_still_starting_is_waited_for_not_joined_too_early() -> None:
    gate = threading.Event()
    child = _SlowStart(gate)
    # Başlatan thread `start()` içinde, çocuk başlayana kadar bekler (iş thread'i ile saatinin durumu). Başlatan
    # `before`a girer: yardımcı önce onu bekleyip çocuğun başlamasını dolaylı yoldan sağlayamasın
    starter = threading.Thread(target=child.start, name="job-starter-test", daemon=True)
    starter.start()
    deadline = time.monotonic() + 10
    while child not in conftest.job_threads():
        assert time.monotonic() < deadline, "the child thread was never started"
        time.sleep(0.001)
    before = conftest.job_threads() - {child}
    assert not child.is_alive()  # listede, ama başlamamış: join() burada RuntimeError verirdi
    opener = threading.Timer(0.2, gate.set)
    opener.start()
    try:
        conftest.join_job_threads(before, timeout=10)
        assert not child.is_alive()
    finally:
        gate.set()
        opener.cancel()
        starter.join(10)
        child.join(10)
