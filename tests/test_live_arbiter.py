"""
Kaynak hakemi (src/services/live/arbiter.py; plan maddesi P24): sahte saatle durum makinesi.

  * push açılınca `page` önde; yoklama yavaş güvenlik aralığına iner
  * sessizlik (PING/PONG ve kare yok) ya da kopma → `poll` önde, `poll_interval`, hemen bir yoklama turu
  * her (yeniden) bağlanmadan sonra bir yoklama turu
  * push'u olmayan spor (`--source poll`) hiç değişiklik bildirmez
"""
from __future__ import annotations

import pytest

from src.services.live import arbiter as arb
from src.services.live.arbiter import SportArbiter

T0 = 1_790_000_000.0


def make(push: bool = True, poll_interval: float = 30.0) -> SportArbiter:
    return SportArbiter("football", poll_interval=poll_interval, push=push)


def test_it_starts_on_polling_and_polls_at_once() -> None:
    a = make()
    assert a.leader == "poll" and a.update(T0) is None
    assert a.poll_due(T0) and a.next_poll_in(T0) == 0.0
    a.polled(T0)
    assert not a.poll_due(T0 + 29) and a.poll_due(T0 + 30)
    assert a.next_poll_in(T0 + 10) == pytest.approx(20.0)


def test_an_open_connection_leads_and_slows_polling_down() -> None:
    a = make()
    a.polled(T0)
    a.opened(T0 + 5)
    switch = a.update(T0 + 5)
    assert switch is not None and switch.to_data() == {"sport": "football", "from": "poll", "to": "page",
                                                       "reason": "push_connected"}
    assert a.leader == "page" and a.last_switch == switch
    assert a.poll_due(T0 + 5)  # bağlanmadan sonra bir tur (tohumlama)
    a.polled(T0 + 6)
    assert a.interval() == arb.SAFETY_POLL_SECONDS
    assert not a.poll_due(T0 + 6 + 60) and a.poll_due(T0 + 6 + arb.SAFETY_POLL_SECONDS)
    assert a.update(T0 + 7) is None  # değişiklik yok: olay yok


def test_pings_keep_a_quiet_sport_healthy_and_silence_falls_back() -> None:
    a = make()
    a.opened(T0)
    a.update(T0)
    a.polled(T0)
    for k in range(1, 4):  # sitenin istemcisi 120 sn'de bir PING atar; maç yok, kare yok
        a.ping(T0 + 120 * k)
        assert a.update(T0 + 120 * k + 1) is None and a.leader == "page"
    last = T0 + 360
    assert a.healthy(last + arb.SILENCE_SECONDS)
    switch = a.update(last + arb.SILENCE_SECONDS + 1)
    assert switch is not None and (switch.to_source, switch.reason) == ("poll", "push_silent")
    assert a.interval() == 30.0 and a.poll_due(last + arb.SILENCE_SECONDS + 1)


def test_frames_count_as_signs_of_life() -> None:
    a = make()
    a.opened(T0)
    a.update(T0)
    a.frame(T0 + 170)
    assert a.update(T0 + 300) is None and a.leader == "page"


def test_a_dropped_connection_falls_back_and_a_reconnect_polls_once() -> None:
    a = make()
    a.opened(T0)
    a.update(T0)
    a.polled(T0)
    a.closed(T0 + 1800)  # yaklaşık 30 dakikada bir kopar
    switch = a.update(T0 + 1800)
    assert switch is not None and (switch.from_source, switch.to_source, switch.reason) == ("page", "poll",
                                                                                          "push_disconnected")
    assert a.poll_due(T0 + 1800)  # kopukken kaçanlar için hemen
    a.polled(T0 + 1800)
    assert not a.poll_due(T0 + 1810) and a.poll_due(T0 + 1830)  # kopukken poll_interval
    a.opened(T0 + 1805)
    assert a.connections == 2 and a.poll_due(T0 + 1805)  # yeniden bağlanma: bir tur
    switch = a.update(T0 + 1805)
    assert switch is not None and switch.to_source == "page"


def test_a_page_that_cannot_open_reports_unavailable() -> None:
    a = make()
    a.opened(T0)
    a.update(T0)
    a.failed("page crashed")
    switch = a.update(T0 + 1)
    assert switch is not None and switch.reason == "push_unavailable"
    a.opened(T0 + 10)
    assert a.unavailable is None and a.update(T0 + 10) is not None


def test_a_gap_asks_for_a_poll_round() -> None:
    a = make()
    a.polled(T0)
    assert not a.poll_due(T0 + 1)
    a.gap()
    assert a.poll_due(T0 + 1)


def test_without_push_polling_leads_and_nothing_is_reported() -> None:
    a = make(push=False)
    a.opened(T0)  # olmaz, ama olsa da
    assert a.update(T0) is None and a.leader == "poll" and not a.healthy(T0)
    a.polled(T0)
    assert a.interval() == 30.0


def test_the_safety_interval_is_never_shorter_than_the_poll_interval() -> None:
    a = make(poll_interval=600.0)
    a.opened(T0)
    a.update(T0)
    assert a.interval() == 600.0


def test_to_dict_reports_the_leader_and_the_last_switch() -> None:
    a = make()
    a.opened(T0)
    a.update(T0)
    data = a.to_dict()
    assert data["leader"] == "page" and data["connections"] == 1
    assert data["last_switch"] == {"sport": "football", "from": "poll", "to": "page", "reason": "push_connected",
                                   "at": T0}
