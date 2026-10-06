"""Süreçler arası ortak istek bütçesi (src/throttle.py, issue #16). Ağ yok; saat çoğunlukla sahte."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import random
import subprocess
import sys
import textwrap
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.challenge_solver as cs
import src.utils as utils
from src import throttle
from src.client import request_context, transport
from src.throttle import RequestThrottle, Reservation, advance, put_back, take
from src.watcher import MatchWatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def shared_dir(tmp_path, monkeypatch):
    """Ortak bütçe açık, durum dosyaları bu teste özel."""
    d = tmp_path / "throttle"
    monkeypatch.setenv("SOFASCORE_THROTTLE_DIR", str(d))
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "10")
    throttle.reset_for_tests()
    yield d
    throttle.reset_for_tests()


# --- GCRA (saf fonksiyon) --------------------------------------------------------------------

def test_advance_spaces_requests_by_interval():
    state, now, slots = {}, 100.0, []
    for _ in range(4):  # dört istek aynı anda gelir
        delay, slot, state = advance(state, now, interval=0.5)
        slots.append(slot)
        assert delay == slot - now
    assert slots == [100.0, 100.5, 101.0, 101.5]


def test_advance_idle_time_is_not_saved_up():
    _, _, state = advance({}, 100.0, 1.0)
    delay, slot, state = advance(state, 500.0, 1.0)  # uzun boşluktan sonra
    assert (delay, slot) == (0.0, 500.0)
    assert advance(state, 500.0, 1.0)[0] == 1.0  # ama biriken "kredi" yok: ikinci istek bekler


def test_advance_burst_lets_first_requests_through_then_spaces():
    state, delays = {}, []
    for _ in range(5):
        delay, _, state = advance(state, 100.0, interval=0.1, burst=3)
        delays.append(round(delay, 6))
    assert delays == [0.0, 0.0, 0.0, 0.1, 0.2]


@pytest.mark.parametrize("bad", [
    {"tat": "x", "at": 1.0},
    {"tat": float("nan"), "at": 1.0},
    {"tat": float("inf"), "at": 1.0},
    {"tat": True, "at": 1.0},
    {"tat": None},
])
def test_advance_ignores_corrupt_state(bad):
    assert advance(bad, 100.0, 1.0)[:2] == (0.0, 100.0)


def test_advance_resets_when_clock_went_backwards():
    # Durum saat 10:00'da yazılmış, şimdi 09:00: bir saat bekletmek yerine sıfırla
    stale = {"tat": 5000.0, "at": 4999.0}
    assert advance(stale, 1400.0, 1.0)[:2] == (0.0, 1400.0)


def test_advance_never_waits_longer_than_the_cap():
    far = {"tat": 100.0 + throttle._MAX_WAIT_SECONDS + 50, "at": 100.0}
    assert advance(far, 100.0, 1.0)[0] == 0.0
    near = {"tat": 100.0 + throttle._MAX_WAIT_SECONDS - 50, "at": 100.0}
    assert advance(near, 100.0, 1.0)[0] == throttle._MAX_WAIT_SECONDS - 50  # meşru kuyruk korunur


def test_very_low_rates_still_space_requests():
    """Aralık emniyet sınırından uzun olsa da (saatte 1 istek) ikinci istek bir aralık bekler."""
    delay, _, state = advance({}, 100.0, interval=3600.0)
    assert delay == 0.0
    assert advance(state, 100.0, interval=3600.0)[0] == 3600.0


# --- ayar -------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, throttle.DEFAULT_RATE_LIMIT),
    ("", throttle.DEFAULT_RATE_LIMIT),
    ("5", 5.0),
    ("0.5", 0.5),
    ("0", 0.0),
    ("off", 0.0),
    ("OFF", 0.0),
    ("-3", 0.0),
    ("abc", throttle.DEFAULT_RATE_LIMIT),
    ("nan", throttle.DEFAULT_RATE_LIMIT),
    ("inf", throttle.DEFAULT_RATE_LIMIT),
])
def test_configured_rate(monkeypatch, raw, expected):
    monkeypatch.delenv("MAX_CONCURRENT", raising=False)
    if raw is None:
        monkeypatch.delenv("REQUEST_RATE_LIMIT", raising=False)
    else:
        monkeypatch.setenv("REQUEST_RATE_LIMIT", raw)
    assert throttle.configured_rate() == expected


def test_default_is_five_requests_per_second():
    assert throttle.DEFAULT_RATE_LIMIT == 5.0


@pytest.mark.parametrize("max_concurrent", [None, "1", "10", "30", "50", "0", "abc"])
def test_default_rate_does_not_depend_on_max_concurrent(monkeypatch, max_concurrent):
    """Varsayılan sabittir (5 istek/sn): MAX_CONCURRENT'i yükseltmek toplam hızı artırmaz."""
    monkeypatch.delenv("REQUEST_RATE_LIMIT", raising=False)
    if max_concurrent is None:
        monkeypatch.delenv("MAX_CONCURRENT", raising=False)
    else:
        monkeypatch.setenv("MAX_CONCURRENT", max_concurrent)
    assert throttle.configured_rate() == 5.0
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "7")  # açıkça verilen değer aynen kullanılır
    assert throttle.configured_rate() == 7.0
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "100")  # eski varsayılanı elle yazmış kullanıcı etkilenmez
    assert throttle.configured_rate() == 100.0


def test_unset_limit_paces_requests_at_five_per_second(tmp_path, monkeypatch):
    """Ayar yokken: bir saniyelik pay (5 istek) beklemeden geçer, sonrası 0,2 sn aralıklıdır."""
    monkeypatch.delenv("REQUEST_RATE_LIMIT", raising=False)
    clock = Clock()
    t = RequestThrottle("api", throttle.configured_rate, clock=clock, directory=str(tmp_path))
    delays = [t.reserve() for _ in range(10)]  # on istek aynı anda gelir
    assert delays[:5] == [0.0] * 5
    assert delays[5:] == pytest.approx([0.2, 0.4, 0.6, 0.8, 1.0])
    clock.t += 60.0
    slots = [t.reserve_slot()[1] for _ in range(605)]  # sürekli yük: 600 istek tam 120 sn sürer
    assert slots[-1] - slots[4] == pytest.approx(120.0)


@pytest.mark.parametrize("raw", ["0", "off", "OFF", "false", "none", "disabled"])
def test_off_switch_removes_the_limit(tmp_path, monkeypatch, raw):
    """0 / off: hiçbir istek bekletilmez, durum dosyası da yazılmaz."""
    monkeypatch.setenv("REQUEST_RATE_LIMIT", raw)
    monkeypatch.setenv("SOFASCORE_THROTTLE_DIR", str(tmp_path / "t"))
    throttle.reset_for_tests()
    try:
        assert [throttle.reserve() for _ in range(200)] == [0.0] * 200
        assert throttle.status() == {"enabled": False, "requests_per_second": 0.0, "shared": False, "error": None}
        assert not (tmp_path / "t").exists()
    finally:
        throttle.reset_for_tests()


def test_default_shows_in_status(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUEST_RATE_LIMIT", raising=False)
    monkeypatch.setenv("SOFASCORE_THROTTLE_DIR", str(tmp_path / "t"))
    throttle.reset_for_tests()
    try:
        assert throttle.status() == {"enabled": True, "requests_per_second": 5.0, "shared": True, "error": None}
    finally:
        throttle.reset_for_tests()


def test_settings_page_knows_the_same_default():
    """Ayarlar sayfası uyarıyı bu sayıya göre gösterir; iki sabit ayrı düşmesin."""
    import re

    path = os.path.join(ROOT, "frontend", "src", "views", "SettingsView.vue")
    with open(path, encoding="utf-8") as f:
        found = re.search(r"^const DEFAULT_RATE_LIMIT = ([\d.]+)$", f.read(), re.M)
    assert found and float(found.group(1)) == throttle.DEFAULT_RATE_LIMIT


def test_disabled_throttle_never_waits_and_writes_nothing(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 0.0, clock=clock, sleep=clock.sleep, directory=str(tmp_path / "t"))
    assert [t.wait() for _ in range(50)] == [0.0] * 50
    assert clock.sleeps == [] and not (tmp_path / "t").exists()


def test_rate_is_read_on_every_call(shared_dir, monkeypatch):
    """Ayarlar sayfası .env'i değiştirince çalışan süreç yeniden başlatılmadan uyar."""
    clock = Clock()
    t = RequestThrottle("api", throttle.configured_rate, burst=1, clock=clock)
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "2")
    assert [t.reserve(), t.reserve()] == [0.0, 0.5]
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "0")
    assert t.reserve() == 0.0


def test_default_burst_is_one_second_of_budget(tmp_path):
    d = str(tmp_path)
    assert RequestThrottle("a", 100.0, directory=d).burst() == 100
    assert RequestThrottle("b", 1.0, directory=d).burst() == 1
    assert RequestThrottle("c", 0.2, directory=d).burst() == 1  # ≤ 1 istek/sn: eşit aralık


# --- dosya durumu: aynı dizini kullanan örnekler tek bütçeyi paylaşır ---------------------------

def test_two_throttles_share_one_budget(tmp_path):
    clock = Clock()
    a = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    b = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    slots = [a.reserve_slot()[1], b.reserve_slot()[1], a.reserve_slot()[1], b.reserve_slot()[1]]
    assert slots == [clock.t, clock.t + 1, clock.t + 2, clock.t + 3]


def test_lanes_are_independent(tmp_path):
    clock = Clock()
    api = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    watch = RequestThrottle("watch", 1.0, clock=clock, directory=str(tmp_path))
    assert api.reserve() == 0.0 and watch.reserve() == 0.0
    assert api.reserve() == 1.0 and watch.reserve() == 1.0


def test_corrupt_state_file_is_reset(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    t.reserve()
    (tmp_path / "api.json").write_text("{yarım yazılmış", encoding="utf-8")
    assert RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path)).reserve() == 0.0
    assert json.loads((tmp_path / "api.json").read_text(encoding="utf-8"))["tat"] == clock.t + 1


def test_wait_sleeps_for_the_reserved_delay(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 4.0, burst=1, clock=clock, sleep=clock.sleep, directory=str(tmp_path))
    for _ in range(3):
        t.wait()
    assert clock.sleeps == [0.25, 0.25]


def test_wait_async_does_not_block_the_loop(tmp_path):
    t = RequestThrottle("api", 50.0, burst=1, directory=str(tmp_path))

    async def run():
        t0 = time.monotonic()
        await asyncio.gather(*[t.wait_async() for _ in range(5)])  # 0, 20, 40, 60, 80 ms
        return time.monotonic() - t0

    assert asyncio.run(run()) >= 0.075


def test_invalid_lane_name_rejected():
    with pytest.raises(ValueError):
        RequestThrottle("../x", 1.0)


# --- dayanıklılık -----------------------------------------------------------------------------

def test_unusable_directory_falls_back_to_in_process_pacing(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # dizin olması gereken yerde dosya: makedirs başarısız olur
    clock = Clock()
    t = RequestThrottle("api", 1.0, clock=clock, directory=str(blocker / "sub"))
    with caplog.at_level("WARNING"):
        assert [t.reserve(), t.reserve(), t.reserve()] == [0.0, 1.0, 2.0]  # süreç içi aralık korunur
    assert t.shared_error
    assert caplog.text.count("kullanılamıyor") == 1  # her istekte değil, bir kez


def test_held_lock_does_not_deadlock(tmp_path, monkeypatch):
    """Kilidi tutan süreç takılı kalsa bile istek engellenmez: zaman aşımı → süreç içi sayaç."""
    clock = Clock()
    t = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path), lock_timeout=0.05)
    assert t.reserve() == 0.0

    real_lock, attempts = throttle.file_lock, []

    def counting_lock(path, timeout=1.0):
        attempts.append(timeout)
        return real_lock(path, timeout)

    with real_lock(str(tmp_path / "api.lock")):  # başka biri kilidi tutuyor ve bırakmıyor
        monkeypatch.setattr(throttle, "file_lock", counting_lock)
        assert t.reserve() == 1.0  # zaman aşımı; kaldığı yerden: paylaşılan durumun son kopyasıyla
        assert attempts == [0.05] and t.shared_error
        assert t.reserve() == 2.0
        assert attempts == [0.05]  # bir süre dosya yeniden denenmez: her istek zaman aşımı beklemez
    # Kilit bırakıldı ve yeniden deneme süresi doldu: dosyaya dönülür
    t._shared_failed_at = time.monotonic() - throttle._SHARED_RETRY_AFTER_SECONDS - 1
    t.reserve()
    assert len(attempts) == 2 and t.shared_error is None


_HOLD_LOCK = """
import sys, time
sys.path.insert(0, {root!r})
from src import throttle
with throttle.file_lock({lock!r}):
    open({ready!r}, "w").close()
    time.sleep(120)
"""


def _wait_for(path, proc, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if proc.poll() is not None:
            raise AssertionError(f"yardımcı süreç erken çıktı: {proc.stderr.read() if proc.stderr else ''}")
        if time.monotonic() > deadline:
            raise AssertionError("yardımcı süreç hazır olmadı")
        time.sleep(0.01)


def test_crashed_lock_holder_leaves_no_stale_lock(tmp_path):
    """Kilidi tutarken ölen süreç: işletim sistemi kilidi bırakır, sonraki istek dosyayı kullanır."""
    lock, ready = str(tmp_path / "api.lock"), str(tmp_path / "ready")
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK.format(root=ROOT, lock=lock, ready=ready)],
        stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for(ready, proc)
        with pytest.raises(TimeoutError):  # süreç yaşarken kilit gerçekten onda
            with throttle.file_lock(lock, timeout=0.05):
                pass
        proc.kill()
        proc.wait(timeout=30)
        t = RequestThrottle("api", 1.0, directory=str(tmp_path), lock_timeout=10.0)
        assert t.reserve() == 0.0
        assert t.shared_error is None
        assert os.path.exists(tmp_path / "api.json")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
        if proc.stderr:
            proc.stderr.close()


_WORKER = """
import json, os, sys, time
sys.path.insert(0, {root!r})
from src.throttle import RequestThrottle
t = RequestThrottle("api", {rate!r}, burst=1, directory={directory!r}, lock_timeout=60.0)
open({ready!r}, "w").close()
while not os.path.exists({go!r}):
    time.sleep(0.002)
slots = [t.reserve_slot()[1] for _ in range({count})]
assert t.shared_error is None, t.shared_error
print(json.dumps(slots))
"""


def test_two_processes_share_the_budget(tmp_path):
    """
    İki ayrı süreç aynı anda, aynı bütçeden sıra ayırır; ayrılan anların birleşimi tek bütçeye
    uymalı. Süreçler uyumaz, yalnızca ayırır: sonuç zamanlamaya değil kilit altında yazılan
    duruma bağlıdır, yavaş bir CI makinesinde de aynıdır. Bütçe paylaşılmasaydı iki süreç
    aynı 10 saniyelik aralığa ayrı ayrı 200'er an yerleştirir, aralıklar bozulurdu.
    """
    rate, count = 20.0, 200
    interval = 1.0 / rate
    go = str(tmp_path / "go")
    procs = []
    try:
        for i in range(2):
            ready = str(tmp_path / f"ready{i}")
            code = _WORKER.format(root=ROOT, rate=rate, directory=str(tmp_path / "t"), ready=ready, go=go, count=count)
            p = subprocess.Popen(
                [sys.executable, "-c", textwrap.dedent(code)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            procs.append((p, ready))
        for p, ready in procs:
            _wait_for(ready, p)
        open(go, "w").close()  # ikisi birlikte başlasın
        per_process = []
        for p, _ in procs:
            out, err = p.communicate(timeout=120)
            assert p.returncode == 0, err
            per_process.append(json.loads(out))
    finally:
        for p, _ in procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=30)
            for stream in (p.stdout, p.stderr):
                if stream:
                    stream.close()

    assert [len(s) for s in per_process] == [count, count]
    merged = sorted(s for slots in per_process for s in slots)
    gaps = [b - a for a, b in zip(merged, merged[1:], strict=False)]
    eps = 1e-3  # epoch büyüklüğündeki sayılarda kayan nokta yuvarlaması (aralık 50 ms)
    assert min(gaps) >= interval - eps  # toplamda: hiçbir iki istek aralıktan sık değil
    assert merged[-1] - merged[0] >= (2 * count - 1) * interval - eps  # 400 istek, tek sıra


# --- GCRA: kullanılmayan sıranın geri verilmesi (FX-6) -----------------------------------------
# İptal edilen isteğin sırası kuyrukta kalırsa sonraki istek onun arkasında bekler (PR #33'ün
# açık bıraktığı durum). put_back sırayı geri alır; hiçbir sıra iki isteğe verilmez.

def _queue(count, now=100.0, interval=0.5, burst=1):
    """Aynı anda gelen `count` istek: ([(bekleme, an, sıra), ...], durum)."""
    state, taken = {}, []
    for _ in range(count):
        delay, slot, position, state = take(state, now, interval, burst)
        taken.append((delay, slot, position))
    return taken, state


def test_take_is_advance_plus_the_position_in_the_queue():
    state_a, state_t = {}, {}
    for now in (100.0, 100.0, 100.0, 100.1, 107.0):
        delay, slot, state_a = advance(state_a, now, 0.5, burst=2)
        t_delay, t_slot, position, state_t = take(state_t, now, 0.5, burst=2)
        assert (t_delay, t_slot, state_t) == (delay, slot, state_a)
        assert position == state_t["tat"] - 0.5 and position - 0.5 <= slot <= position
    assert "free" not in state_t  # iade yokken dosyanın biçimi eskisiyle aynı


def test_giving_back_the_last_slot_shortens_the_queue():
    taken, state = _queue(3)
    returned, state = put_back(state, 100.0, taken[2][2], 0.5)
    assert returned and state == {"tat": 101.0, "at": 100.0}
    assert take(state, 100.0, 0.5)[:2] == (1.0, 101.0)  # sonraki istek iade edilen anı alır


def test_a_slot_given_back_from_the_middle_goes_to_the_next_request_once():
    taken, state = _queue(4)  # anlar: 100, 100.5, 101, 101.5
    returned, state = put_back(state, 100.0, taken[1][2], 0.5)
    assert returned and state == {"tat": 102.0, "at": 100.0, "free": [100.5]}
    delay, slot, position, state = take(state, 100.0, 0.5)
    assert (delay, slot, position) == (0.5, 100.5, 100.5) and "free" not in state
    assert take(state, 100.0, 0.5)[:2] == (2.0, 102.0)  # ikinci istek kuyruğun sonuna


def test_slots_given_back_in_any_order_free_the_whole_queue():
    """Durdurulan iş: varsayılan ayarla 70 istek kuyrukta (5'i gitti); 65 sıra karışık düzende geri gelir."""
    taken, state = _queue(70, interval=0.2, burst=5)
    assert take(state, 100.0, 0.2, burst=5)[0] == pytest.approx(13.2)  # iade olmasaydı sonraki istek
    waiting = [t for t in taken if t[0] > 1e-9]  # beşinci isteğin beklemesi kayan nokta artığı (1e-14)
    assert len(waiting) == 65
    random.Random(16).shuffle(waiting)
    for _, _, position in waiting:
        returned, state = put_back(state, 100.0, position, 0.2)
        assert returned
    assert state == {"tat": pytest.approx(101.0), "at": 100.0}
    assert take(state, 100.0, 0.2, burst=5)[0] == pytest.approx(0.2)  # yalnızca giden 5 isteğin arkasında


def test_a_given_back_slot_whose_time_has_passed_is_not_reused():
    """Geç kalan istek iade edilmiş eski sırayı alsaydı bir sonraki sıranın sahibine aralıktan fazla yaklaşırdı."""
    taken, state = _queue(4)
    _, state = put_back(state, 100.0, taken[1][2], 0.5)  # 100.5 boşta
    delay, slot, position, state = take(state, 100.7, 0.5)
    assert (slot, position) == (102.0, 102.0) and "free" not in state
    # Zamanı geçmiş bir sıra aradan geri de verilemez
    taken, state = _queue(4)
    assert put_back(state, 100.7, taken[1][2], 0.5) == (False, state)


def test_giving_back_a_stale_last_slot_still_shortens_the_queue():
    taken, state = _queue(2)  # 100, 100.5; tat 101
    returned, state = put_back(state, 100.8, taken[1][2], 0.5)
    assert returned and take(state, 100.8, 0.5)[:2] == (0.0, 100.8)


def test_burst_slots_keep_their_send_time_when_reused():
    taken, state = _queue(8, interval=0.1, burst=3)  # bekleme: 0, 0, 0, 0.1, ..., 0.5
    _, state = put_back(state, 100.0, taken[4][2], 0.1)
    delay, slot, position, _ = take(state, 100.05, 0.1, burst=3)
    assert position == taken[4][2] and slot == pytest.approx(taken[4][1]) and delay == pytest.approx(0.15)


@pytest.mark.parametrize("state", [
    {},
    {"tat": "x", "at": 1.0},
    {"tat": 103.0, "at": 5000.0},  # sistem saati geri alınmış: sonraki ayırma durumu sıfırlar
    {"tat": 100.0, "at": 100.0},  # durum sıfırlanmış: sıra artık kuyruğun dışında
])
def test_put_back_leaves_an_unknown_state_alone(state):
    assert put_back(state, 100.0, 101.0, 0.5) == (False, state)


def test_put_back_ignores_bad_arguments_and_repeats():
    taken, state = _queue(4)
    assert put_back(state, 100.0, float("nan"), 0.5) == (False, state)
    assert put_back(state, 100.0, taken[1][2], 0.0) == (False, state)
    _, state = put_back(state, 100.0, taken[1][2], 0.5)
    assert put_back(state, 100.0, taken[1][2], 0.5) == (False, state)  # aynı sıra listeye iki kez girmez


def test_corrupt_free_list_is_ignored():
    for free in ("x", [None, "y", float("nan"), True], [99.0, 500.0]):  # sayı değil / zamanı geçmiş / kuyruk dışı
        delay, slot, position, state = take({"tat": 102.0, "at": 100.0, "free": free}, 100.0, 0.5)
        assert (slot, position) == (102.0, 102.0) and "free" not in state


def test_free_list_is_bounded(monkeypatch):
    monkeypatch.setattr(throttle, "_MAX_FREE_POSITIONS", 3)
    taken, state = _queue(10)
    for _, _, position in taken[1:4]:
        returned, state = put_back(state, 100.0, position, 0.5)
        assert returned
    assert put_back(state, 100.0, taken[5][2], 0.5) == (False, state)  # liste dolu: sıra boşa gider
    returned, state = put_back(state, 100.0, taken[9][2], 0.5)  # kuyruğun sonu listeye girmez
    assert returned and len(state["free"]) == 3


def test_cap_reset_drops_the_free_list():
    cap = throttle._MAX_WAIT_SECONDS
    far = {"tat": 100.0 + cap + 50, "at": 100.0, "free": [100.0 + cap + 20]}
    assert take(far, 100.0, 1.0) == (0.0, 100.0, 100.0, {"tat": 101.0, "at": 100.0})


@pytest.mark.parametrize("burst", [1, 5])
def test_given_back_slots_never_let_the_budget_be_exceeded(burst):
    """
    Rastgele ayırma ve iade: gönderilen istekler (iade edilmeyen sıralar, ayrılan anlarında) bütçeye
    uyar — art arda gelen her n + 1 istek en az (n - burst + 1) aralığa yayılır (iade olmadan GCRA'nın
    verdiği sınır). Bir sıra iki isteğe verilseydi ya da zamanı geçmiş bir sıra kullanılsaydı bozulurdu.
    """
    rng = random.Random(6)
    interval, now, state = 0.2, 1_000_000.0, {}
    waiting, sent, reused, given_back = [], [], 0, 0
    for _ in range(4000):
        now += rng.choice([0.0, 0.0, 0.0, 0.01, 0.07, 0.2, 0.9])
        sent += [slot for slot, _ in waiting if slot <= now]  # sırası gelen istek gider
        waiting = [w for w in waiting if w[0] > now]
        if rng.random() < 0.6 or not waiting:
            had_free = bool(state.get("free"))
            _, slot, position, state = take(state, now, interval, burst)
            reused += had_free
            waiting.append((slot, position))
        else:
            slot, position = waiting.pop(rng.randrange(len(waiting)))  # sırasını beklerken iptal edildi
            returned, state = put_back(state, now, position, interval)
            given_back += returned
    sent.sort()
    assert given_back > 300 and reused > 100 and len(sent) > 1000
    eps = 1e-6
    for n in range(1, 40):
        spans = [b - a for a, b in zip(sent, sent[n:], strict=False)]
        assert min(spans) >= (n - burst + 1) * interval - eps, n


# --- sınırlayıcı: iade ------------------------------------------------------------------------

def test_reservation_is_the_delay_for_callers_that_only_wait(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 2.0, burst=1, clock=clock, directory=str(tmp_path))
    first, second = t.reserve(), t.reserve()
    assert isinstance(second, float) and second == 0.5 and second + 1 == 1.5
    assert json.loads(json.dumps([first, second])) == [0.0, 0.5]
    assert (second.slot, second.position) == (clock.t + 0.5, clock.t + 0.5)


def test_given_back_reservation_is_free_for_the_next_caller(tmp_path):
    clock = Clock()
    job = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    other = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))  # başka bir süreç gibi
    queued = [job.reserve() for _ in range(5)]
    assert [float(r) for r in queued] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert [r.give_back() for r in queued[1:]] == [True] * 4
    assert other.reserve() == 1.0  # iade olmasaydı 5.0
    assert json.loads((tmp_path / "api.json").read_text(encoding="utf-8")) == {"tat": clock.t + 2, "at": clock.t}


def test_a_reservation_is_given_back_only_once(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    t.reserve()
    queued = t.reserve()
    assert queued.give_back() is True
    again = t.reserve()  # aynı an artık bu isteğin
    assert again == 1.0 and queued.give_back() is False
    assert t.reserve() == 2.0
    stranger = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    assert stranger.give_back(again) is False  # başka bir örneğin verdiği sıra


def test_a_slot_whose_time_has_come_is_not_given_back(tmp_path):
    """Zamanı gelmiş sıra kullanılmış sayılır: geri verilseydi sıradaki çağıran hiç beklemeden alırdı."""
    clock = Clock()
    t = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path))
    now = t.reserve()
    assert now == 0.0 and now.give_back() is False  # beklemeden geçen istek: sırası zaten geldi
    queued = t.reserve()
    clock.t += 1.0
    assert queued.give_back() is False
    assert t.reserve() == 1.0


def test_disabled_throttle_has_nothing_to_give_back(tmp_path):
    t = RequestThrottle("api", 0.0, directory=str(tmp_path / "t"))
    reservation = t.reserve()
    assert reservation == 0.0 and reservation.give_back() is False and not (tmp_path / "t").exists()


def test_give_back_works_without_the_shared_file(tmp_path):
    clock = Clock()
    t = RequestThrottle("watch", 1.0, burst=1, shared=False, clock=clock, directory=str(tmp_path / "t"))
    queued = [t.reserve() for _ in range(3)]
    assert queued[1].give_back() is True
    assert [t.reserve(), t.reserve()] == [1.0, 3.0]
    assert not (tmp_path / "t").exists()


def test_give_back_never_raises_when_the_file_is_unusable(tmp_path, caplog):
    clock = Clock()
    t = RequestThrottle("api", 1.0, clock=clock, directory=str(tmp_path), lock_timeout=0.05)
    t.reserve()
    queued, last = t.reserve(), t.reserve()
    with caplog.at_level("WARNING", logger="src.throttle"), throttle.file_lock(str(tmp_path / "api.lock")):
        assert queued.give_back() is False  # kilit başkasında: sıra iade edilemez, hata da yok
        assert last.give_back() is False  # dosya bir süre yeniden denenmez: her iade zaman aşımı beklemez
    assert t.shared_error and caplog.text.count("was not returned") == 1
    assert t.reserve() == 3.0  # süreç içi sayaç, paylaşılan durumun son kopyasıyla sürer


def test_interrupted_wait_gives_the_slot_back(tmp_path):
    clock = Clock()

    def interrupted(seconds):
        raise KeyboardInterrupt

    t = RequestThrottle("api", 1.0, clock=clock, sleep=interrupted, directory=str(tmp_path))
    assert t.wait() == 0.0
    for _ in range(3):
        with pytest.raises(KeyboardInterrupt):
            t.wait()
    assert t.reserve() == 1.0  # üç yarım kalan bekleme kuyrukta yer tutmuyor


def test_cancelled_wait_async_gives_the_slot_back(tmp_path):
    t = RequestThrottle("api", 0.1, directory=str(tmp_path))  # 10 sn'de bir istek

    async def run():
        assert await t.wait_async() == 0.0
        waiting = [asyncio.ensure_future(t.wait_async()) for _ in range(3)]
        await asyncio.sleep(0.01)
        for task in waiting:
            task.cancel()
        return await asyncio.gather(*waiting, return_exceptions=True)

    results = asyncio.run(run())
    assert all(isinstance(r, asyncio.CancelledError) for r in results)
    assert 9.0 < t.reserve() <= 10.0  # iade olmasaydı ~40 sn


def test_module_level_give_back_ignores_plain_numbers(shared_dir):
    """Testlerde throttle.reserve yerine konan sahteler düz sayı döndürür; istek katmanı onlarla da çalışır."""
    assert throttle.give_back(1.5) is False
    with pytest.raises(ValueError), throttle.give_back_if_interrupted(1.5):
        raise ValueError
    assert isinstance(throttle.reserve(), Reservation)


def test_interrupted_block_gives_the_slot_back_and_a_finished_one_keeps_it(shared_dir, monkeypatch):
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "0.1")
    assert throttle.reserve() == 0.0
    kept = throttle.reserve()
    with throttle.give_back_if_interrupted(kept):
        pass  # bekleme bitti: istek gönderilecek, sıra kullanıldı
    dropped = throttle.reserve()
    assert 19.0 < dropped <= 20.0
    with pytest.raises(utils.FetchCancelled), throttle.give_back_if_interrupted(dropped):
        raise utils.FetchCancelled()
    assert 19.0 < throttle.reserve() <= 20.0  # aynı an yeniden verildi
    assert kept.give_back() is True  # hâlâ beklenebilirdi: blok onu iade etmedi


_CANCELLED_JOB = """
import json, os, random, sys, time
sys.path.insert(0, {root!r})
from src.throttle import RequestThrottle
t = RequestThrottle("api", 5.0, clock=lambda: {now!r}, directory={directory!r}, lock_timeout=60.0)
queued = [t.reserve() for _ in range(70)]
open({ready!r}, "w").close()
while not os.path.exists({go!r}):
    time.sleep(0.002)
waiting = [r for r in queued if r > 0]
random.Random(7).shuffle(waiting)
print(json.dumps([r.give_back() for r in waiting]))
assert t.shared_error is None, t.shared_error
"""


def test_cancelled_job_in_another_process_frees_its_queue(tmp_path):
    """
    Bir süreçte toplu iş 70 isteği kuyruğa koyar (varsayılan MAX_CONCURRENT × 7 dilim, 5 istek/sn) ve
    durdurulur; başka bir sürecin sonraki isteği o 65 boş sıranın arkasında 13 sn beklemez. Saat iki
    süreçte de sabittir: sonuç zamanlamaya değil dosyaya yazılan duruma bağlıdır.
    """
    now, directory = 1_000_000.0, tmp_path / "t"
    ready, go = str(tmp_path / "ready"), str(tmp_path / "go")
    code = _CANCELLED_JOB.format(root=ROOT, now=now, directory=str(directory), ready=ready, go=go)
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(code)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        _wait_for(ready, proc)
        mine = RequestThrottle("api", 5.0, clock=lambda: now, directory=str(directory), lock_timeout=60.0)
        behind = mine.reserve()
        assert behind == pytest.approx(70 / 5 - 0.8)  # iş sürerken: kuyruğun sonu
        open(go, "w").close()  # iş durduruldu
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
        assert json.loads(out) == [True] * 65
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()

    assert mine.reserve() == pytest.approx(0.2)  # giden 5 isteğin hemen arkası; iade olmasaydı 13.4
    assert behind.give_back() is True  # kuyruğun sonu da dönünce aradaki boş sıralar birlikte kapanır
    state = json.loads((directory / "api.json").read_text(encoding="utf-8"))
    assert state == {"tat": pytest.approx(now + 1.2), "at": now}
    assert mine.reserve() == pytest.approx(0.4)


# --- isteklerin geçtiği noktalar --------------------------------------------------------------

class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self.reason, self.headers, self._body, self.text = code, "x", {}, body, text

    def json(self):
        return self._body


CFG = {"max_retries": 2, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}


def _reserve_counter(monkeypatch, delay=0.0):
    calls = []

    def fake_reserve():
        calls.append(1)
        return delay

    monkeypatch.setattr(throttle, "reserve", fake_reserve)
    return calls


def test_sync_curl_request_takes_a_slot_per_attempt(monkeypatch):
    calls = _reserve_counter(monkeypatch, delay=0.3)
    sleeps = []
    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils, "_sleep", side_effect=sleeps.append), \
            patch.object(utils.cffi_requests, "get", side_effect=[Resp(500), Resp(200, {"ok": 1})]) as get:
        assert utils.make_api_request("/x") == {"ok": 1}
    assert get.call_count == 2 and len(calls) == 2  # yeniden deneme de bir istektir
    assert sleeps.count(0.3) == 2


def test_async_curl_request_takes_a_slot(monkeypatch):
    calls = _reserve_counter(monkeypatch)
    session = MagicMock()
    session.get = AsyncMock(return_value=Resp(200, {"ok": 1}))
    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")):
        assert asyncio.run(utils.make_api_request_async(session, "/x")) == {"ok": 1}
    assert len(calls) == 1


def test_throttle_wait_is_cancellable(monkeypatch):
    """Uzun bir bütçe beklemesi iptal edilen işi tutmaz ve istek hiç atılmaz."""
    _reserve_counter(monkeypatch, delay=60.0)
    token = utils.set_cancel_check(lambda: True)
    try:
        with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
                patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
                patch.object(utils, "raise_if_cancelled", side_effect=[None, utils.FetchCancelled()]), \
                patch.object(utils.cffi_requests, "get") as get, \
                pytest.raises(utils.FetchCancelled):
            utils.make_api_request("/x")
        assert get.call_count == 0
    finally:
        utils._cancel_check.reset(token)


def _bridge(evaluate):
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token = "tok"
    bridge.ensure_ready = AsyncMock()
    bridge.evaluate = evaluate
    return bridge


def test_browser_fetch_takes_a_slot_per_request(monkeypatch):
    calls = _reserve_counter(monkeypatch)
    results = [
        {"status": 403, "ok": False, "data": None, "text": '{"error":{"reason":"challenge"}}'},
        {"status": 200, "ok": True, "data": {"a": 1}, "text": None},
    ]
    bridge = _bridge(AsyncMock(side_effect=results))
    bridge.solve_challenge = AsyncMock(return_value="new")
    assert asyncio.run(bridge.fetch_json("/event/1")) == {"a": 1}
    assert len(calls) == 2  # ilk istek + challenge sonrası yineleme


def test_browser_first_request_passes_the_limiter_once(monkeypatch):
    """Önce-tarayıcı modunda istek curl'e uğramaz: bütçeden tek sıra alınır (köprünün içinde)."""
    calls = _reserve_counter(monkeypatch)
    bridge = _bridge(AsyncMock(return_value={"status": 200, "ok": True, "data": {"a": 1}, "text": None}))
    monkeypatch.setattr(utils, "_browser_first_until", time.monotonic() + 60)
    session = MagicMock()
    session.get = AsyncMock()
    with patch.object(cs.BrowserBridge, "get_instance", return_value=bridge), \
            patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")):
        assert asyncio.run(utils.make_api_request_async(session, "/event/1")) == {"a": 1}
    assert session.get.await_count == 0 and len(calls) == 1


def test_real_limiter_spaces_bridge_requests_across_callers(shared_dir, monkeypatch):
    """Gerçek sınırlayıcıyla uçtan uca: köprü ve curl aynı şeridi kullanır."""
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "1")
    stamps = []

    async def evaluate(script, arg=None):
        stamps.append("browser")
        return {"status": 200, "ok": True, "data": {}, "text": None}

    bridge = _bridge(evaluate)
    delays = []
    real_reserve = throttle.api_throttle().reserve

    def spy():
        delays.append(real_reserve())
        return 0.0  # ayırma gerçek, uyku yok

    monkeypatch.setattr(throttle, "reserve", spy)
    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils.cffi_requests, "get", return_value=Resp(200, {})):
        asyncio.run(bridge.fetch_json("/event/1"))
        utils.make_api_request("/event/2")
        asyncio.run(bridge.fetch_json("/event/3"))
    assert delays[0] == 0.0
    assert 0.9 < delays[1] <= 1.0 and 1.9 < delays[2] <= 2.0  # 1 istek/sn: sıra sıra
    assert os.path.exists(shared_dir / "api.json")


# --- köprü: ortak bütçede sıra bekleme, istek zaman aşımından sayılmaz -------------------------
# PR #19'un açık bıraktığı durum: düşük bir bütçede (ya da kalabalık kuyrukta) istek, sırasını
# köprünün içinde beklerken REQUEST_TIMEOUT (120 sn) doluyordu. Testler süreleri küçültür:
# zaman aşımı 0,5 sn, sıra beklemesi 0,8 sn.

_OK = {"status": 200, "ok": True, "data": {"a": 1}, "text": None}


def _slot_delays(monkeypatch, *delays):
    """throttle.reserve sahtesi: sırayla verilen beklemeleri döndürür, bitince 0."""
    left = list(delays)
    monkeypatch.setattr(throttle, "reserve", lambda: left.pop(0) if left else 0.0)


def test_slot_wait_does_not_count_towards_async_bridge_timeout(monkeypatch):
    _slot_delays(monkeypatch, 0.8)
    bridge = _bridge(AsyncMock(return_value=_OK))
    monkeypatch.setattr(cs, "REQUEST_TIMEOUT", 0.5)
    with patch.object(cs.BrowserBridge, "get_instance", return_value=bridge):
        start = time.monotonic()
        assert asyncio.run(cs.fetch_api_via_browser("/event/1")) == {"a": 1}
    assert time.monotonic() - start >= 0.75  # sıra gerçekten beklendi, istek düşmedi


def test_slot_wait_does_not_count_towards_sync_bridge_timeout(monkeypatch):
    _slot_delays(monkeypatch, 0.8)
    bridge = _bridge(AsyncMock(return_value=_OK))
    with patch.object(cs.BrowserBridge, "get_instance", return_value=bridge):
        start = time.monotonic()
        assert cs.fetch_api_via_browser_sync("/event/1", timeout=0.5) == {"a": 1}
    assert time.monotonic() - start >= 0.75


def test_retry_after_challenge_also_extends_the_timeout(monkeypatch):
    """Challenge sonrası yineleme kuyruğun sonundan yeni sıra alır: o bekleme de sayılmaz."""
    _slot_delays(monkeypatch, 0.4, 0.4)
    challenge = {"status": 403, "ok": False, "data": None, "text": '{"error":{"reason":"challenge"}}'}
    bridge = _bridge(AsyncMock(side_effect=[challenge, _OK]))
    bridge.solve_challenge = AsyncMock(return_value="new")
    with patch.object(cs.BrowserBridge, "get_instance", return_value=bridge):
        assert cs.fetch_api_via_browser_sync("/event/1", timeout=0.5) == {"a": 1}


@pytest.mark.parametrize("sync", [True, False])
def test_bridge_timeout_still_limits_the_request_itself(monkeypatch, sync):
    """Uzayan yalnızca sıra beklemesidir: sırası gelmiş ama yanıt vermeyen istek yine düşer."""
    _slot_delays(monkeypatch, 0.3)

    async def stuck():
        await cs._wait_for_slot()
        await asyncio.sleep(30)

    start = time.monotonic()
    if sync:
        with pytest.raises(concurrent.futures.TimeoutError):
            cs._run_sync(stuck(), 0.3)
    else:
        async def run():
            with pytest.raises(asyncio.TimeoutError):
                await cs._run_on_background_loop(stuck(), 0.3)

        asyncio.run(run())
    assert 0.5 <= time.monotonic() - start < 5  # 0,3 sn sıra + 0,3 sn zaman aşımı


def test_callers_waiting_for_a_shared_solve_get_its_slot_wait_too(monkeypatch):
    """Ortak çözümün doğrulama isteği sıra beklerken, çözümü bekleyen diğer istekler de düşmez."""
    _slot_delays(monkeypatch, 0.8)
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token, bridge._token_at = None, 0.0
    bridge._solve_task, bridge._solve_wait, bridge._solve_failed_at = None, None, 0.0
    bridge.ensure_ready = AsyncMock()
    solves = []

    async def solve():
        solves.append(1)
        await cs._wait_for_slot()  # _api_unlocked'ın doğrulama isteği
        return "jwt"

    bridge._solve_challenge = solve

    async def run():
        return await asyncio.gather(*[cs._run_on_background_loop(bridge.solve_challenge(), 0.5) for _ in range(3)])

    assert asyncio.run(run()) == ["jwt"] * 3
    assert len(solves) == 1


def test_calls_do_not_share_their_slot_wait(monkeypatch):
    """Bir çağrının sıra beklemesi, aynı anda çalışan başka bir çağrının zaman aşımını uzatmaz."""
    _slot_delays(monkeypatch, 0.8)

    async def queued():
        await cs._wait_for_slot()
        return "ok"

    async def stuck():
        await asyncio.sleep(30)

    async def run():
        start = time.monotonic()
        first = asyncio.ensure_future(cs._run_on_background_loop(queued(), 0.3))
        with pytest.raises(asyncio.TimeoutError):
            await cs._run_on_background_loop(stuck(), 0.3)
        assert time.monotonic() - start < 1.0  # paylaşılsaydı 0,8 + 0,3 sn sürerdi
        assert await first == "ok"

    asyncio.run(run())


# --- iptal: istek katmanı ve köprü sırayı geri verir, köprünün beklemesi kesilir (FX-6) ---------

def _queued_seconds():
    """Ortak durum dosyasına göre kuyruğun sonu şimdiden kaç saniye ileride."""
    with open(os.path.join(throttle.state_dir(), "api.json"), encoding="utf-8") as f:
        return json.load(f)["tat"] - time.time()


def test_cancelled_sync_request_gives_its_slot_back(shared_dir, monkeypatch):
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "0.1")  # 10 sn'de bir istek
    assert throttle.reserve() == 0.0
    answers = iter([False])  # deneme başındaki kontrol geçer, sıra beklenirken iptal gelir

    with request_context(cancel=lambda: next(answers, True)), \
            patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils.cffi_requests, "get") as get, \
            pytest.raises(utils.FetchCancelled):
        utils.make_api_request("/x")
    assert get.call_count == 0
    assert 9.0 < throttle.reserve() <= 10.0  # iade olmasaydı ~20 sn


def test_stopped_bulk_job_leaves_no_queue_behind(shared_dir, monkeypatch):
    """
    Uçtan uca: 70 eşzamanlı istek (10 maç × 7 dilim) başlar, ilki gidince iş durdurulur. O anda
    MAX_CONCURRENT kadarı sırasını bekliyor, gerisi istek semaforunda; iptal edilince hepsi sırayla
    bütçeden geçer. Eskiden her biri kuyrukta bir sıra bırakırdı: sonraki istek, hangi süreçten gelirse
    gelsin, burada (1 istek/sn) 69 sn, varsayılan 5 istek/sn ile 13 sn beklerdi. Şimdi hiçbiri gönderilmez
    ve kuyrukta sıra kalmaz.
    """
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "1")
    session = MagicMock()
    session.get = AsyncMock(return_value=Resp(200, {"ok": 1}))
    stopped = []

    async def stop():  # bütün istekler yola çıktıktan sonra: "Durdur"
        stopped.append(True)

    async def job():
        return await asyncio.gather(
            *[utils.make_api_request_async(session, f"/event/{n}") for n in range(70)], stop(),
            return_exceptions=True,
        )

    with request_context(cancel=lambda: bool(stopped)), \
            patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")):
        results = asyncio.run(job())

    assert session.get.await_count == 1  # durdurmadan sonra hiçbir istek gitmedi
    assert sum(isinstance(r, utils.FetchCancelled) for r in results) == 69
    assert _queued_seconds() <= 1.0 + 1e-3  # yalnızca giden isteğin aralığı
    assert throttle.reserve() <= 1.0


# --- iptal: istek semaforunu durdurmadan sonra alan istek gönderilmez (FX-9) --------------------
# _request_async iptale yalnızca semaforu beklemeden önce bakıyordu. Semaforu durdurmadan sonra alan
# istek bütçeden sıra ayırıyordu; sıranın zamanı gelmişse (bütçe kapalı ya da yüksekken hep, bütçe
# sınırken kuyruğun iptali bir aralıktan uzun sürdüğünde) istek gidiyordu. Yukarıdaki test bu yüzden
# yavaş makinede 1 yerine 2 istek görüyordu. Aşağıdaki testler saate bağlı değildir: gönderilen istek
# ve ayrılan sıra sayısı, durdurma anında semaforu tutan istek sayısıyla belirlenir.

REQUEST_SLOTS = 10  # istek semaforunun genişliği (MAX_CONCURRENT)


@pytest.fixture
def request_slots(monkeypatch):
    """İstek semaforu REQUEST_SLOTS genişliğinde: ortamın MAX_CONCURRENT değeri testi etkilemez."""
    monkeypatch.setattr(transport._cm, "get_max_concurrent", lambda: REQUEST_SLOTS)
    return REQUEST_SLOTS


def _stopped_job(request, count=70):
    """
    `count` isteği birlikte başlatır ve hepsi yola çıkınca işi durdurur; isteklerin sonuçlarını döndürür.

    Durdurma, görevlerin ilk adımlarıyla aynı döngü turunda çalışır: o anda semaforu almış istekler ilk
    beklemelerindedir (bütçe sırası, uçuştaki istek, köprü), gerisi semaforu bekler. Hangi isteğin hangi
    tarafta olduğu saate değil yalnızca semaforun genişliğine bağlıdır.
    """
    stopped = []

    async def stop():
        stopped.append(True)

    async def job():
        return await asyncio.gather(*[request(n) for n in range(count)], stop(), return_exceptions=True)

    with request_context(cancel=lambda: bool(stopped)), \
            patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")):
        return asyncio.run(job())[:count]


@pytest.mark.parametrize("seconds", [0.0, 0.02])
def test_stopped_bulk_job_sends_nothing_however_slow_the_reservations_are(
        shared_dir, monkeypatch, request_slots, seconds):
    """
    Yukarıdaki testin yavaş makinedeki hali: her sıra ayırma `seconds` sürer. Kontrol yokken semaforda
    bekleyen 59 istek durdurmadan sonra birer sıra ayırıp geri veriyordu (70 ayırma); 20 ms'lik
    ayırmalarla bu 1 sn'yi (bir aralığı) aşıyor, sırası gelmiş bulunan istek de gidiyordu. Kontrol
    varken yalnızca durdurmadan önce semaforu almış olanlar sıra ayırır.
    """
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "1")
    session = MagicMock()
    session.get = AsyncMock(return_value=Resp(200, {"ok": 1}))
    real_reserve = throttle.api_throttle().reserve
    reservations = []

    def slow_reserve():
        time.sleep(seconds)
        reservations.append(1)
        return real_reserve()

    monkeypatch.setattr(throttle, "reserve", slow_reserve)
    results = _stopped_job(lambda n: utils.make_api_request_async(session, f"/event/{n}"))

    assert session.get.await_count == 1
    # giden istek + durdurma anında semaforu tutup bütçedeki sırasını bekleyen REQUEST_SLOTS istek
    assert len(reservations) == 1 + request_slots
    assert sum(isinstance(r, utils.FetchCancelled) for r in results) == 69


def test_stop_reaches_requests_waiting_for_a_request_slot(monkeypatch, request_slots):
    """
    Bütçe sınır değilken (kapalı ya da yüksek): durdurma anında uçuşta olan istekler tamamlanır, semaforda
    bekleyenler gönderilmez ve bütçeden sıra ayırmaz. Eskiden 70 isteğin 70'i de giderdi.
    """
    reservations = _reserve_counter(monkeypatch)  # her sıra hemen gelir
    sent = []

    async def get(url, **kwargs):
        sent.append(url)
        await asyncio.sleep(0)  # istek uçuşta: döngü sıradaki isteğe geçer
        return Resp(200, {"ok": 1})

    session = MagicMock()
    session.get = get
    with patch.object(utils.breaker, "report_ok") as answered, \
            patch.object(utils.breaker, "report_exception") as failed:
        results = _stopped_job(lambda n: utils.make_api_request_async(session, f"/event/{n}"))

    assert len(sent) == request_slots and len(reservations) == request_slots
    assert results[:request_slots] == [{"ok": 1}] * request_slots  # yanıtı gelmiş istek atılmaz
    assert all(isinstance(r, utils.FetchCancelled) for r in results[request_slots:])
    # durdurulan istek bir sonuç değildir: ağ hatasına çevrilmez, devre kesiciye bildirilmez
    assert answered.call_count == request_slots and failed.call_count == 0


def test_stop_reaches_browser_first_requests_waiting_for_a_request_slot(monkeypatch, request_slots):
    """
    Önce-tarayıcı modunda istek curl'e uğramadan köprüden gider ve aynı semaforu bekler: semaforu
    durdurmadan sonra alan istek köprüye verilmez. Eskiden bekleyenlerin hepsi köprüden giderdi.

    Durdurma anında semaforu tutan istekler köprünün içindedir (ensure_ready). FX-18'den beri onlar da
    gitmez: köprü bütçeden sıra ayırmadan önce iptale bakar (src/client/bridge.py, _wait_for_slot).
    Eskiden köprü iptale yalnızca sıra beklerken bakıyordu ve zamanı gelmiş sıradaki REQUEST_SLOTS istek
    durdurmadan sonra gidiyordu; şimdi hiçbiri gitmez ve hiçbiri sıra ayırmaz.
    """
    reservations = _reserve_counter(monkeypatch)
    evaluated = []

    async def evaluate(script, arg=None):
        evaluated.append(arg)
        return {"status": 200, "ok": True, "data": {"a": 1}, "text": None}

    bridge = _bridge(evaluate)
    monkeypatch.setattr(utils, "_browser_first_until", time.monotonic() + 60)
    session = MagicMock()
    session.get = AsyncMock()
    with patch.object(cs.BrowserBridge, "get_instance", return_value=bridge):
        results = _stopped_job(lambda n: utils.make_api_request_async(session, f"/event/{n}"), count=30)

    assert len(evaluated) == 0 and len(reservations) == 0  # FX-18: eskiden request_slots
    assert session.get.await_count == 0
    assert all(isinstance(r, utils.FetchCancelled) for r in results)


@pytest.mark.parametrize("sync", [True, False])
def test_bridge_slot_wait_stops_when_the_job_is_cancelled(shared_dir, monkeypatch, sync):
    """
    Köprünün içindeki sıra beklemesi çağıranın iptal kontrolüne bakar: iş durdurulunca bir kontrol
    aralığı içinde kesilir, istek gönderilmez ve sıra geri verilir. Eskiden sync yolda çağıran thread
    bekleme bitene kadar (burada 10 sn) köprüde kalırdı.
    """
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "0.1")
    assert throttle.reserve() == 0.0
    bridge = _bridge(AsyncMock(return_value=_OK))
    stop_at = time.monotonic() + 0.1

    def call():
        if sync:
            return cs.fetch_api_via_browser_sync("/event/1")
        return asyncio.run(cs.fetch_api_via_browser("/event/1"))

    with request_context(cancel=lambda: time.monotonic() >= stop_at), \
            patch.object(cs.BrowserBridge, "get_instance", return_value=bridge):
        start = time.monotonic()
        with pytest.raises(utils.FetchCancelled):
            call()
        elapsed = time.monotonic() - start
    assert elapsed < 0.1 + cs._CANCEL_CHECK_SECONDS + 2.0  # + yavaş makine payı; 10 sn değil
    assert bridge.evaluate.await_count == 0
    assert 9.0 - elapsed < throttle.reserve() <= 10.0  # iade olmasaydı ~20 sn


def test_bridge_slot_wait_without_a_cancel_check_is_unchanged(shared_dir, monkeypatch):
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "4")
    monkeypatch.setattr(throttle.api_throttle(), "_burst", 1)
    bridge = _bridge(AsyncMock(return_value=_OK))
    with request_context(), patch.object(cs.BrowserBridge, "get_instance", return_value=bridge):
        start = time.monotonic()
        assert [cs.fetch_api_via_browser_sync("/event/1") for _ in range(3)] == [{"a": 1}] * 3
    assert time.monotonic() - start >= 0.45  # iki bekleme, 0,25'er sn


def test_cancelled_bridge_call_gives_its_slot_back(shared_dir, monkeypatch):
    """Sıra beklerken asyncio iptali (zaman aşımı, kapanan çağıran) da sırayı geri verir."""
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "0.1")
    assert throttle.reserve() == 0.0

    async def run():
        waiting = asyncio.ensure_future(cs._wait_for_slot())
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    with request_context():
        asyncio.run(run())
    assert 9.0 < throttle.reserve() <= 10.0


def test_one_cancelled_job_does_not_stop_a_shared_solve(monkeypatch):
    """
    Ortak challenge çözümünü başlatan işin iptali, çözümün doğrulama isteğini sıra beklerken kesmez:
    aynı çözümü başka çağıranlar da bekliyor olabilir (çözüm görevi, başlatanın bağlamını kopyalar).
    """
    _slot_delays(monkeypatch, 0.3, 0.3)
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token, bridge._token_at = None, 0.0
    bridge._solve_task, bridge._solve_wait, bridge._solve_failed_at = None, None, 0.0
    bridge.ensure_ready = AsyncMock()

    async def solve():
        await cs._wait_for_slot()  # _api_unlocked'ın doğrulama isteği
        return "jwt"

    bridge._solve_challenge = solve
    with request_context(cancel=lambda: True):
        assert cs._run_sync(bridge.solve_challenge(), 5.0) == "jwt"
        with pytest.raises(utils.FetchCancelled):  # işin kendi isteği ise kesilir
            cs._run_sync(cs._wait_for_slot(), 5.0)


# --- izleyici: 1 sn aralık ortak bütçenin "watch" şeridinden gelir -----------------------------

def _fake_api(path):
    return {"events": []} if path.endswith("/events/live") else None


def test_watcher_with_injected_fetch_keeps_its_lane_in_process(shared_dir, tmp_path):
    clock = Clock()
    w = MatchWatcher("football", league_ids=[17], data_dir=str(tmp_path / "d"), fetch_json=_fake_api,
                     clock=clock, sleep=clock.sleep)
    for _ in range(3):
        w._get("/sport/football/events/live")
    assert clock.sleeps == [1.0, 1.0]
    assert not os.path.exists(shared_dir / "watch.json")  # sahte fetch: ortak dosyaya dokunmaz


def test_watchers_in_separate_processes_share_one_second_spacing(shared_dir, tmp_path):
    """
    Spor başına bir --watch süreci (issue #16): gerçek fetch kullanan izleyiciler aynı şeridi
    paylaşır, toplamda istekler arası ≥ 1 sn kalır. İki izleyici iki süreci temsil eder.
    """
    clock = Clock()
    stamps = []

    def fetch(path):
        stamps.append(clock())
        return _fake_api(path)

    watchers = []
    for sport in ("football", "tennis"):
        with patch.object(MatchWatcher, "_default_fetch", staticmethod(fetch)):
            watchers.append(MatchWatcher(sport, league_ids=[17], data_dir=str(tmp_path / "d"),
                                         clock=clock, sleep=clock.sleep))
    for _ in range(3):
        for w in watchers:
            w._get(f"/sport/{w.sport}/events/live")
    assert len(stamps) == 6
    assert all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:], strict=False))
    assert os.path.exists(shared_dir / "watch.json")


def test_watchers_keep_one_second_spacing_with_the_default_budget(shared_dir, tmp_path, monkeypatch):
    """REQUEST_RATE_LIMIT verilmemişken (5 istek/sn) izleyici şeridi değişmez: toplamda ≥ 1 sn, ortak dosya."""
    monkeypatch.delenv("REQUEST_RATE_LIMIT", raising=False)
    clock = Clock()
    stamps = []

    def fetch(path):
        stamps.append(clock())
        return _fake_api(path)

    watchers = []
    for sport in ("football", "tennis"):
        with patch.object(MatchWatcher, "_default_fetch", staticmethod(fetch)):
            watchers.append(MatchWatcher(sport, league_ids=[17], data_dir=str(tmp_path / "d"),
                                         clock=clock, sleep=clock.sleep))
    for _ in range(3):
        for w in watchers:
            w._get(f"/sport/{w.sport}/events/live")
    assert [b - a for a, b in zip(stamps, stamps[1:], strict=False)] == [1.0] * 5
    assert os.path.exists(shared_dir / "watch.json")


def test_watcher_keeps_private_spacing_when_shared_budget_is_off(shared_dir, tmp_path, monkeypatch):
    """REQUEST_RATE_LIMIT=0: ortak dosya yok, ama izleyicinin 1 sn aralığı (eski davranış) sürer."""
    monkeypatch.setenv("REQUEST_RATE_LIMIT", "0")
    clock = Clock()
    with patch.object(MatchWatcher, "_default_fetch", staticmethod(_fake_api)):
        w = MatchWatcher("football", league_ids=[17], data_dir=str(tmp_path / "d"), clock=clock, sleep=clock.sleep)
    for _ in range(3):
        w._get("/sport/football/events/live")
    assert clock.sleeps == [1.0, 1.0]
    assert not os.path.exists(shared_dir / "watch.json")


def test_watcher_default_fetch_also_passes_the_api_lane(shared_dir, tmp_path, monkeypatch):
    """İzleyicinin gerçek isteği make_api_request'ten geçer: genel bütçeden de sıra alır."""
    calls = _reserve_counter(monkeypatch)
    clock = Clock()
    w = MatchWatcher("football", league_ids=[17], data_dir=str(tmp_path / "d"), clock=clock, sleep=clock.sleep)
    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils.cffi_requests, "get", return_value=Resp(200, {"events": []})):
        assert w._get("/sport/football/events/live") == {"events": []}
    assert len(calls) == 1


# --- ayarlar uç noktası -----------------------------------------------------------------------

@pytest.fixture
def env_file_restored():
    """
    Ayarlar uç noktası paylaşılan test `.env`'ine yazar (REQUEST_RATE_LIMIT) ve yükleyiciye o değerin `.env`'den
    geldiğini bildirir. Dosya ve yükleyicinin durumu testten sonra eski haline döner: yoksa sonraki bir test
    süreç ortamındaki REQUEST_RATE_LIMIT=0'ı `.env`'in değeri sayar ve yapılandırma dosyası onu ezer
    (tests/test_config_loader.py, bu dosyadan sonra çalışınca; FX-15).
    """
    import conftest
    from src.config import loader

    try:
        with open(conftest.ENV_FILE, "rb") as f:
            saved = f.read()
    except FileNotFoundError:
        saved = None
    yield
    if saved is None:
        try:
            os.remove(conftest.ENV_FILE)
        except FileNotFoundError:
            pass
    else:
        with open(conftest.ENV_FILE, "wb") as f:
            f.write(saved)
    loader.reset()


def test_settings_expose_and_update_rate_limit(monkeypatch, env_file_restored):
    from fastapi.testclient import TestClient

    from src.web.app import app

    client = TestClient(app)
    before = os.environ.get("REQUEST_RATE_LIMIT")
    try:
        assert client.get("/api/settings").json()["request_rate_limit"] == 0.0  # conftest: kapalı
        assert client.post("/api/settings", json={"request_rate_limit": 2.5}).status_code == 200
        assert os.environ["REQUEST_RATE_LIMIT"] == "2.5"
        assert client.get("/api/settings").json()["request_rate_limit"] == 2.5
        assert client.post("/api/settings", json={"request_rate_limit": -1}).status_code == 422
        assert client.post("/api/settings", json={"request_rate_limit": 5000}).status_code == 422
    finally:
        client.post("/api/settings", json={"request_rate_limit": float(before or 0)})
        os.environ["REQUEST_RATE_LIMIT"] = before or "0"


def test_settings_show_the_default_when_unset_and_accept_off(monkeypatch, env_file_restored):
    from fastapi.testclient import TestClient

    from src.web.app import app

    client = TestClient(app)
    before = os.environ.get("REQUEST_RATE_LIMIT")
    try:
        monkeypatch.delenv("REQUEST_RATE_LIMIT", raising=False)
        assert client.get("/api/settings").json()["request_rate_limit"] == 5.0
        assert client.post("/api/settings", json={"request_rate_limit": 0}).status_code == 200  # kapalı
        assert os.environ["REQUEST_RATE_LIMIT"] == "0"
        assert client.get("/api/settings").json()["request_rate_limit"] == 0.0
        assert client.post("/api/settings", json={"request_rate_limit": 40}).status_code == 200  # varsayılanın üstü
        assert client.get("/api/settings").json()["request_rate_limit"] == 40.0
    finally:
        client.post("/api/settings", json={"request_rate_limit": float(before or 0)})
        os.environ["REQUEST_RATE_LIMIT"] = before or "0"
