"""
Sunucunun tahmini süresi (`progress.detail.eta_seconds`) işin ölçülen hızına dayanır (FX-26, canlı doğrulama M14).

Celtics indirmesi 128 maçın 31'indeyken "69 sn kaldı" demişti; 2 istek/sn'de maç başına ~9 istekle kalan 97 maç ~7
dakika sürdü. İlk maçlar çoğunlukla yalnızca olayı okunan gelecek fikstürlerdi (1 istek): aşamanın ortalama hızı
kalan maçlar için iyimserdi. Burada:

  * istek hesabı: sınıflara ayrılmış planlı maçlar × sınıfın ölçülen maç başına isteği / ölçülen istek hızı;
  * son dakikaların hızı: planı olmayan aşamada (lig indirmesi) yavaşlayan iş;
  * eski kural (ortalama hız) bir alt sınır olarak kalır;
  * takım takibinin eşitlemesi maçlarını sınıflandırır ve biten her maçın isteğini bildirir.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

from sofascore_scraper.jobs.progress import JobProgress
from sofascore_scraper.services import follow_sync
from sofascore_scraper.services import sync as sync_module
from sofascore_scraper.services.sync import FollowsSyncSpec
from sofascore_scraper.slices import Outcome
from sofascore_scraper.store import Store

import test_fx19_follow_sync as fx19

# FX-19'un sahte dünyası ve eşitleme düzeneği (takım 42'nin dört maçı)
TEAM, follow, run = fx19.TEAM, fx19.follow, fx19.run
_settings, fake, store = fx19._settings, fx19.fake, fx19.store


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(total: int) -> Tuple[JobProgress, Clock]:
    clock = Clock()
    progress = JobProgress(["details"], lambda fields: None, clock=clock, wall_clock=clock)
    progress.start_phase("details", total)
    return progress, clock


def test_the_celtics_case_counts_the_requests_still_to_send() -> None:
    progress, clock = make(128)
    progress.plan_costs({"event_only": 30, "full": 98})
    done = 0
    for _ in range(25):  # gelecek fikstürler: 1 istek, 2 istek/sn
        clock.t += 0.5
        done += 1
        progress.note_cost("event_only", 1)
        progress.advance(done)
    for _ in range(6):  # bitmiş maçlar: 9 istek
        clock.t += 4.5
        done += 1
        progress.note_cost("full", 9)
        progress.advance(done)
    eta = progress.eta_seconds()
    assert eta is not None
    average = (clock.t - 1000.0) / done * (128 - done)  # eski kural: ~124 sn
    assert average < 130
    # kalan: 5 fikstür × 1 + 92 maç × 9 = 833 istek, ölçülen hız 79 istek / 39.5 sn = 2 istek/sn
    assert eta == pytest.approx(833 / (79 / 39.5), abs=0.5)


def test_a_class_without_a_finished_match_counts_as_the_dearest() -> None:
    progress, clock = make(20)
    progress.plan_costs({"event_only": 10, "full": 10})
    for i in range(10):
        clock.t += 1.0
        progress.note_cost("event_only", 2)
        progress.advance(i + 1)
    # 2 istek/sn; tam maçların hiçbiri bitmedi: onlar da maç başına 2 istek sayılır (bilinen en pahalı sınıf)
    assert progress.eta_seconds() == pytest.approx(10 * 2 / 2.0, abs=0.1)


def test_without_a_plan_the_pace_of_the_last_minutes_counts() -> None:
    progress, clock = make(400)
    for i in range(100):  # saklanmış maçlar hızla geçer
        clock.t += 0.1
        progress.advance(i + 1)
    for i in range(30):  # sonra maç başına 10 sn
        clock.t += 10.0
        progress.advance(101 + i)
    eta = progress.eta_seconds()
    average = (clock.t - 1000.0) / 130 * 270
    assert eta is not None and eta > average * 3
    assert eta == pytest.approx(10.0 * 270, rel=0.02)


def test_the_old_rules_still_hold() -> None:
    progress, clock = make(100)
    progress.advance(10)
    assert progress.eta_seconds() is None  # çok erken
    clock.t += 20
    assert progress.eta_seconds() == 180.0  # ortalama: maç başına 2 sn, 90 maç
    progress.advance(100)
    assert progress.eta_seconds() is None


def test_a_new_phase_forgets_the_measurements() -> None:
    progress, clock = make(10)
    progress.plan_costs({"full": 10})
    clock.t += 10
    progress.note_cost("full", 50)
    progress.advance(5)
    progress.start_phase("details", 10)
    clock.t += 10
    progress.advance(5)
    assert progress.eta_seconds() == 10.0  # yalnızca ortalama: yeni aşamada plan ve ölçü yok


def test_the_cost_class_and_the_requests_of_a_result() -> None:
    event_only = SimpleNamespace(need="refill", reason=follow_sync.EVENT_ONLY_REASON)
    assert sync_module._cost_class(event_only) == "event_only"
    assert sync_module._cost_class(SimpleNamespace(need="full", reason="x")) == "full"
    ok, skipped = Outcome("ok", data={}), Outcome("skipped", reason="breaker")
    result = SimpleNamespace(event=ok, slices={"statistics": ok, "lineups": Outcome("empty"), "h2h": skipped})
    assert sync_module._requests_of(result) == 3
    assert sync_module._requests_of(SimpleNamespace(event=None, slices={})) == 0


def test_a_team_sync_plans_its_matches_by_class(store: Store, fake: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    planned: List[Dict[str, int]] = []
    noted: List[Tuple[str, int]] = []
    real_plan, real_note = JobProgress.plan_costs, JobProgress.note_cost

    def plan(self: JobProgress, counts: Any) -> None:
        planned.append(dict(counts))
        real_plan(self, counts)

    def note(self: JobProgress, kind: str, requests: int) -> None:
        noted.append((kind, requests))
        real_note(self, kind, requests)

    monkeypatch.setattr(JobProgress, "plan_costs", plan)
    monkeypatch.setattr(JobProgress, "note_cost", note)
    follow(store, "team", TEAM, "Team 42", sport="football")
    result, _ = run(store, FollowsSyncSpec(mode="full", follows=(f"team:{TEAM}",)))
    assert result.state == "succeeded"
    # dünyada: iki bitmiş maç (tam), bir gelecek ve bir oynanan maç (yalnızca olay)
    assert planned == [{"full": 2, "event_only": 2}]
    assert sorted(kind for kind, _ in noted) == ["event_only", "event_only", "full", "full"]
    assert all(requests == 1 for kind, requests in noted if kind == "event_only")
    assert all(requests > 1 for kind, requests in noted if kind == "full")
