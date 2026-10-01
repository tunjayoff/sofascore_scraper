"""Süreçler arası ortak istek bütçesi (src/throttle.py, issue #16). Ağ yok; saat çoğunlukla sahte."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
import textwrap
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.challenge_solver as cs
import src.utils as utils
from src import throttle
from src.throttle import RequestThrottle, advance
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

def test_settings_expose_and_update_rate_limit(monkeypatch):
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


def test_settings_show_the_default_when_unset_and_accept_off(monkeypatch):
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
