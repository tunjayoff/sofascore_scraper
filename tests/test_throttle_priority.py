"""
Ortak istek bütçesinin öncelik şeridi (FX-23, bulgu F16): web arayüzündeki arama ve öneriler, bir indirmenin
bütçede bekleyen isteklerinin önüne geçer; bütçe aşılmaz. Ağ yok; saat sahte.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
from typing import List

import pytest

from sofascore_scraper import throttle
from sofascore_scraper.client import transport
from sofascore_scraper.throttle import RequestThrottle, Reservation, apply_bumps, take, take_priority


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t
        self.sleeps: List[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _sent_at(reservation: Reservation, clock: Clock) -> float:
    """İsteğin gerçekten gideceği an: sırasını bekler, sonra kaymaları `settle` ile bekler (saat geri alınır)."""
    start = clock.t
    clock.t = max(clock.t, reservation.slot)
    throttle.settle(reservation, clock.sleep) if reservation > 0 else None
    sent = clock.t
    clock.t = start
    return sent


# --- saf işlevler ------------------------------------------------------------------------------


def test_a_priority_request_takes_the_first_waiting_slot_and_the_queue_moves_back():
    now, state = 100.0, {}
    background = []
    for _ in range(5):
        _, slot, position, state = take(state, now, 1.0)
        background.append(position)
    assert background == [100.0, 101.0, 102.0, 103.0, 104.0]

    delay, slot, position, state = take_priority(state, now, 1.0)

    assert (delay, slot, position) == (1.0, 101.0, 101.0)  # 100'deki istek zaten gidiyor; 101'in yerine
    assert state["tat"] == 106.0 and state["seq"] == 1 and state["bumps"] == [[101.0, 1]]
    moved = [apply_bumps(state, p, 0, 1.0)[0] for p in background]
    assert moved == [100.0, 102.0, 103.0, 104.0, 105.0]
    assert sorted([*moved, position]) == [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]  # aralıklar korunur


def test_priority_requests_keep_their_own_order():
    now, state = 100.0, {}
    for _ in range(4):
        state = take(state, now, 1.0)[3]
    first = take_priority(state, now, 1.0)
    second = take_priority(first[3], now, 1.0)
    assert (first[2], second[2]) == (101.0, 102.0)
    # İlki ikincinin kaymasından etkilenmez (sayacı ikincininkinden küçük ama konumu önde)
    assert apply_bumps(second[3], first[2], 1, 1.0)[0] == 101.0
    # Arka planın 101'deki isteği iki kez kayar
    assert apply_bumps(second[3], 101.0, 0, 1.0)[0] == 103.0


def test_without_a_queue_a_priority_request_is_an_ordinary_one():
    delay, slot, position, state = take_priority({}, 100.0, 1.0)
    assert (delay, position) == (0.0, 100.0)
    assert state == {"tat": 101.0, "at": 100.0, "prio": 100.0}
    # Arka planın sonraki isteği onun arkasında bekler
    assert take(state, 100.0, 1.0)[:3] == (1.0, 101.0, 101.0)


def test_a_returned_hole_ahead_is_used_without_moving_the_queue():
    now, state = 100.0, {}
    for _ in range(4):
        state = take(state, now, 1.0)[3]
    returned, state = throttle.put_back(state, now, 101.0, 1.0)
    assert returned and state["free"] == [101.0]
    delay, slot, position, state = take_priority(state, now, 1.0)
    assert (position, state.get("bumps")) == (101.0, None) and "free" not in state


def test_a_returned_hole_behind_the_head_moves_with_the_queue():
    now, state = 100.0, {}
    for _ in range(5):
        state = take(state, now, 1.0)[3]
    state = throttle.put_back(state, now, 103.0, 1.0)[1]
    state = take_priority(state, now, 1.0)[3]
    assert state["free"] == [104.0]  # 103'teki boşluk, 103'teki isteğin kaydığı yere taşındı


def test_the_state_file_stays_in_its_old_form_without_priority_requests():
    state = take({}, 100.0, 1.0)[3]
    assert state == {"tat": 101.0, "at": 100.0}


# --- sınırlayıcı ---------------------------------------------------------------------------------


def test_an_interactive_search_waits_one_interval_while_a_download_queue_waits(tmp_path):
    """
    F16: 1 istek/sn'de 8 istek sıra bekliyorken arama önceden 8 sn bekliyordu; şimdi bir aralık. Arka planın
    istekleri uyanınca kaymayı görür ve bir aralık daha bekler: bütün istekler yine 1 sn arayla gider.
    """
    clock = Clock()
    download = RequestThrottle("api", 1.0, burst=1, clock=clock, directory=str(tmp_path))
    web = RequestThrottle("api", 1.0, burst=1, clock=clock, directory=str(tmp_path))  # başka bir süreç gibi
    queued = [download.reserve() for _ in range(8)]
    assert [float(r) for r in queued] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]

    with throttle.interactive():
        search = web.reserve()

    assert float(search) == 1.0
    sent = sorted([_sent_at(r, clock) for r in queued] + [_sent_at(search, clock)])
    assert [round(s - clock.t, 6) for s in sent] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    assert _sent_at(queued[1], clock) - clock.t == 2.0  # aramanın yerini verdiği istek bir aralık sonra
    assert float(download.reserve()) == 9.0  # kuyruk bir aralık uzadı


def test_settle_follows_more_than_one_move(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 2.0, burst=1, clock=clock, directory=str(tmp_path))
    queued = [t.reserve() for _ in range(4)]  # 0, 0.5, 1.0, 1.5
    with throttle.interactive():
        t.reserve()
        t.reserve()
    last = queued[-1]
    clock.t = last.slot
    assert throttle.settle(last, clock.sleep) == 1.0 and clock.sleeps == [1.0]
    assert last.slot == pytest.approx(clock.t)


def test_a_moved_reservation_is_given_back_at_its_new_place(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 1.0, burst=1, clock=clock, directory=str(tmp_path))
    queued = [t.reserve() for _ in range(4)]  # 0..3
    with throttle.interactive():
        t.reserve()  # 1; 1..3 → 2..4, tat 5
    assert queued[-1].give_back() is True  # artık kuyruğun sonu: 4
    state = json.loads((tmp_path / "api.json").read_text(encoding="utf-8"))
    assert state["tat"] == clock.t + 4
    assert float(t.reserve()) == 4.0


def test_the_lane_follows_the_context_into_another_task():
    """Köprü isteği arka plan döngüsünde, çağıranın bağlamının kopyasıyla çalışır: öncelik de taşınır."""
    with throttle.interactive():
        context = contextvars.copy_context()
    assert not throttle.is_interactive()
    assert context.run(throttle.is_interactive) is True


def test_transport_waits_for_a_moved_slot(monkeypatch, tmp_path):
    clock = Clock()
    lane = RequestThrottle("api", 1.0, burst=1, clock=clock, directory=str(tmp_path))
    monkeypatch.setattr(throttle, "_api", lane)
    monkeypatch.setattr(transport, "_sleep", clock.sleep)
    for _ in range(3):
        lane.reserve()  # 0, 1, 2 bekliyor: indirmenin öteki istekleri
    real_reserve = throttle.reserve

    def reserve_then_search() -> Reservation:
        mine = real_reserve()  # 3
        with throttle.interactive():
            lane.reserve()  # arama 1'in yerine girer; bu istek 4'e kayar
        return mine

    monkeypatch.setattr(throttle, "reserve", reserve_then_search)
    start = clock.t
    transport._throttle()
    assert clock.t - start == 4.0 and clock.sleeps == [3.0, 1.0]


def test_async_settle_waits_for_a_moved_slot(tmp_path):
    clock = Clock()
    t = RequestThrottle("api", 1.0, burst=1, clock=clock, directory=str(tmp_path))
    queued = [t.reserve() for _ in range(3)]
    with throttle.interactive():
        t.reserve()
    waited: List[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)
        clock.t += seconds

    clock.t = queued[1].slot
    assert asyncio.run(throttle.settle_async(queued[1], sleep)) == 1.0 and waited == [1.0]


def test_a_plain_number_settles_to_nothing():
    assert throttle.settle(2.0, lambda s: None) == 0.0
    assert asyncio.run(throttle.settle_async(2.0, asyncio.sleep)) == 0.0


def test_the_search_service_asks_through_the_priority_lane(monkeypatch):
    from sofascore_scraper import utils
    from sofascore_scraper.services import follows

    seen: List[bool] = []

    def fake_request(url: str, **kwargs: object) -> dict:
        seen.append(throttle.is_interactive())
        return {"uniqueTournaments": []}

    monkeypatch.setattr(utils, "make_api_request", fake_request)
    service = follows.FollowsService.__new__(follows.FollowsService)
    assert service._ask("/search/unique-tournaments/abc") == {"uniqueTournaments": []}
    assert seen == [True] and not throttle.is_interactive()
