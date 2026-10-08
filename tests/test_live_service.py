"""
Canlı servis (sofascore_scraper/services/live/supervisor.py) ve `ssc watch` (sofascore_scraper/cli/commands/watch.py): sahte API, sahte saat;
gerçek ağ ve gerçek bekleme yok.

  * olaylar `live` akışına yinelenme anahtarıyla gider; bitiş tek bir /event isteğiyle onaylanır ve saklanır
  * yeniden başlatma olayı yinelemez (durum kaydedilmeden çöken servis dahil)
  * ikinci kopya başlamaz (LeaseHeld; CLI'de çıkış kodu 6)
  * gözetici çöken kaynağı yeniden kurar; engellenmede bekler; meşgul depoda eklemeyi yeniden dener
  * kapsam takiplerden; günlük saatte bir budanır; durum bilgisi (`live_status`)
  * `events --after` kalınan yerden sürer ve budanmış aralığı bildirir
  * sink zarfı şemanın zarfıyla aynıdır
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

import conftest
import test_cli_skeleton as skeleton
from sofascore_scraper.cli.commands import watch as watch_command
from sofascore_scraper.services.live import supervisor
from sofascore_scraper.services.live.supervisor import (
    Blocked,
    LiveScope,
    LiveService,
    SportScope,
    append_retrying,
    explicit_scope,
    live_status,
    scope_from_follows,
)
from sofascore_scraper.store import LeaseHeld, Store, StoreBusy, StreamEvent, open_store
from sofascore_scraper.store.follows import FollowSpec
from test_cli_skeleton import CliRunner

cli = skeleton.cli

FIXTURES = Path(__file__).parent / "fixtures" / "status"
FB_LIVE = "football/A_inprogress-7-2nd-half__17018572"
FB_DONE = "football/A_finished-100-ended__17099711"
TN_LIVE = "tennis/A_inprogress-9-2nd-set__17208186"


def fx(rel: str, eid: int, tournament_id: Optional[int] = None) -> Dict[str, Any]:
    event = json.loads((FIXTURES / f"{rel}.json").read_text(encoding="utf-8"))
    event["id"] = eid
    if tournament_id is not None:
        event["tournament"] = {"uniqueTournament": {"id": tournament_id}}
    return event


def fetched(rel: str) -> float:
    return dt.datetime.fromisoformat(fx(rel, 0)["fetched_at_utc"]).timestamp()


class FakeApi:
    """Spor başına canlı liste ve maç sayfaları; testten değiştirilebilir. `blocked`: sıradaki istek engellenir."""

    def __init__(self, live: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 events: Optional[Dict[int, Dict[str, Any]]] = None) -> None:
        self.live = live or {}
        self.events = events or {}
        self.calls: List[str] = []
        self.blocked = 0

    def __call__(self, path: str) -> Optional[Dict[str, Any]]:
        self.calls.append(path)
        if self.blocked:
            self.blocked -= 1
            raise Blocked("RateLimitError")
        if path.endswith("/events/live"):
            return {"events": copy.deepcopy(self.live.get(path.split("/")[2], []))}
        event = self.events.get(int(path.rsplit("/", 1)[-1]))
        return {"event": copy.deepcopy(event)} if event else None


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Stop:
    """Sahte durdurma belirteci: `wait` saati ilerletir ve her beklemede `on_wait` çağrılır; `rounds` beklemeden sonra durur."""

    def __init__(self, clock: Clock, rounds: int = 1, on_wait: Optional[Callable[[int], None]] = None) -> None:
        self.clock = clock
        self.rounds = rounds
        self.on_wait = on_wait
        self.waits: List[float] = []
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout: Optional[float] = None) -> bool:
        self.waits.append(float(timeout or 0))
        self.clock.now += float(timeout or 0)
        if self.on_wait is not None:
            self.on_wait(len(self.waits))
        if len(self.waits) >= self.rounds:
            self._set = True
        return self._set


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(path))  # çalışma zamanı sınır denetimi bu dizine erişimleri izler
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "48")
    return path


@pytest.fixture
def store(data_dir: Path) -> Store:
    return open_store(data_dir)


def service(store: Store, api: FakeApi, clock: Clock, scope: Optional[LiveScope] = None, **kwargs: Any) -> LiveService:
    return LiveService(store, scope, fetch=api, clock=clock, sleep=clock.sleep, **kwargs)


def live_events(store: Store, stream: str = "live") -> List[Any]:
    return list(store.streams.read(streams=[stream]).events)


def store_finished_before(store: Store, done: Dict[str, Any]) -> None:
    """Maçın daha önce saklanmış, sonuçlanmış ama skoru sonradan düzeltilen bir yükü (bir saat önceki gözlem)."""
    earlier = copy.deepcopy(done)
    earlier["homeScore"]["display"] = earlier["homeScore"]["current"] = done["homeScore"]["current"] + 1
    store.events.observe(500, earlier, observed_at=dt.datetime.fromtimestamp(fetched(FB_LIVE) - 3600, dt.timezone.utc))


def finish_scenario() -> tuple:
    live = fx(FB_LIVE, 500, tournament_id=17)
    done = fx(FB_DONE, 500, tournament_id=17)
    done["startTimestamp"] = live["startTimestamp"]
    return live, done


# --- olaylar ve bitiş onayı ---------------------------------------------------------------------------


def test_a_finished_match_is_one_event_and_its_payload_is_stored(store: Store) -> None:
    live, done = finish_scenario()
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))

    def finish(n: int) -> None:
        if n == 1:  # ikinci turdan önce: listeden düştü, sayfası bitti
            api.live["football"] = []
            api.events[500] = done

    report = service(store, api, clock, explicit_scope(["football"], tournament_ids=[17])).run(
        Stop(clock, rounds=2, on_wait=finish))

    events = live_events(store)
    assert [e.type for e in events] == ["live.status_changed"]
    status = events[0]
    assert (status.event_id, status.sport, status.tournament_id, status.source) == (500, "football", 17, "poll")
    assert status.dedup_key and status.data["to"] == "completed" and status.data["provisional"] is True
    # Tur 1: liste + bitişe yakın maçın sayfası; tur 2: liste + listeden düşen maçın sayfası. Bitiş maç sayfasından
    # görüldüğü için onay isteği yapılmadı; yük saklandı
    assert api.calls == ["/sport/football/events/live", "/event/500"] * 2
    assert store.events.payload(500)["id"] == 500
    assert report.confirmed == 1 and report.events == 1 and report.rounds == 2
    assert live_events(store, "change") == []  # ilk saklanan yük: değişiklik satırı yok
    assert store.watch.load("football")["500"]["done"] is True


def test_a_confirmation_that_changes_a_stored_event_announces_the_change(store: Store) -> None:
    live, done = finish_scenario()
    store_finished_before(store, done)
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))

    def finish(n: int) -> None:
        api.live["football"] = []
        api.events[500] = done

    service(store, api, clock, explicit_scope(["football"], tournament_ids=[17])).run(
        Stop(clock, rounds=2, on_wait=finish))
    changes = live_events(store, "change")
    assert [(e.type, e.event_id, e.sport) for e in changes] == [("change.recorded", 500, "football")]
    assert isinstance(changes[0].data["change_seq"], int) and changes[0].dedup_key


def test_a_completion_seen_in_the_live_list_is_confirmed_with_one_event_request(store: Store) -> None:
    live, done = finish_scenario()
    listed_done = copy.deepcopy(done)
    api = FakeApi({"football": [live]}, {500: done})
    clock = Clock(fetched(FB_LIVE))

    def finish(n: int) -> None:
        api.live["football"] = [listed_done]

    service(store, api, clock, explicit_scope(["football"], tournament_ids=[17])).run(
        Stop(clock, rounds=2, on_wait=finish))

    assert [e.data["to"] for e in live_events(store)] == ["completed"]
    assert api.calls.count("/event/500") == 1  # onay isteği
    assert store.events.payload(500)["status"]["type"] == "finished"


def test_without_confirmation_nothing_is_stored_but_the_event(store: Store) -> None:
    live, done = finish_scenario()
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))

    def finish(n: int) -> None:
        api.live["football"] = []
        api.events[500] = done

    service(store, api, clock, explicit_scope(["football"], event_ids=[500]), confirm=False).run(
        Stop(clock, rounds=5, on_wait=finish))
    assert len(live_events(store)) == 1 and store.events.get(500) is None


def test_an_event_ids_scope_ends_when_every_match_has_finished(store: Store) -> None:
    live, done = finish_scenario()
    api = FakeApi({"football": []}, {500: done})
    clock = Clock(fetched(FB_LIVE))
    stop = Stop(clock, rounds=100)
    report = service(store, api, clock, explicit_scope(["football"], event_ids=[500])).run(stop)
    assert report.finished and stop.waits == [] and report.rounds == 1
    assert live_events(store) == []  # önceki sınıf bilinmiyordu: geçiş olayı yok


# --- yeniden başlatma ----------------------------------------------------------------------------------


def test_a_restart_does_not_emit_again(store: Store) -> None:
    live, done = finish_scenario()
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))
    scope = explicit_scope(["football"], tournament_ids=[17])
    service(store, api, clock, scope).run(Stop(clock, rounds=1))
    api.live["football"] = []
    api.events[500] = done
    service(store, api, clock, scope).run(Stop(clock, rounds=1))
    service(store, api, clock, scope).run(Stop(clock, rounds=1))
    assert [e.type for e in live_events(store)] == ["live.status_changed"]


def test_a_crash_before_the_state_was_saved_does_not_store_the_transition_twice(store: Store) -> None:
    live, done = finish_scenario()
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))
    scope = explicit_scope(["football"], tournament_ids=[17])
    service(store, api, clock, scope).run(Stop(clock, rounds=1))
    before = store.watch.load("football")
    api.live["football"] = []
    api.events[500] = done
    service(store, api, clock, scope, confirm=False).run(Stop(clock, rounds=1))
    store.watch.save("football", before)  # olay eklendi ama durum kaydedilmeden çöktü
    report = service(store, api, clock, scope, confirm=False).run(Stop(clock, rounds=1))
    assert [e.type for e in live_events(store)] == ["live.status_changed"]  # yinelenme anahtarı tuttu
    assert report.events == 0


def test_the_service_continues_where_the_legacy_watcher_stopped(store: Store, data_dir: Path) -> None:
    from sofascore_scraper.watcher import MatchWatcher

    live, done = finish_scenario()
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))
    legacy = MatchWatcher("football", league_ids=[17], data_dir=str(data_dir), fetch_json=api, clock=clock,
                          sleep=clock.sleep)
    legacy.tick()
    api.live["football"] = []
    api.events[500] = done
    service(store, api, clock, explicit_scope(["football"], tournament_ids=[17])).run(Stop(clock, rounds=1))
    assert [(e.data["from"], e.data["to"]) for e in live_events(store)] == [("live", "completed")]


def test_a_2x_state_file_is_imported_once(store: Store, data_dir: Path) -> None:
    live, done = finish_scenario()
    old = {"500": {"class": "live", "done": False, "start_ts": live["startTimestamp"], "tournament_id": 17,
                   "score": [1, 0], "near_end": True, "stuck": False}}
    (data_dir / "watch_state_football.json").write_text(json.dumps(old), encoding="utf-8")
    api = FakeApi({"football": []}, {500: done})
    clock = Clock(fetched(FB_LIVE))
    service(store, api, clock, explicit_scope(["football"], tournament_ids=[17])).run(Stop(clock, rounds=1))
    assert [(e.data["from"], e.data["to"]) for e in live_events(store)] == [("live", "completed")]
    assert store.watch.import_legacy("football") is None


# --- tek kopya ------------------------------------------------------------------------------------------


def test_a_second_instance_is_refused(store: Store) -> None:
    api = FakeApi({"football": []})
    clock = Clock(fetched(FB_LIVE))
    with store.lease("live", purpose="watch"):
        with pytest.raises(LeaseHeld) as refused:
            service(store, api, clock, explicit_scope(["football"], event_ids=[1])).run(Stop(clock))
    assert refused.value.name == "live" and api.calls == []


def test_the_legacy_watcher_and_the_service_exclude_each_other(store: Store) -> None:
    api = FakeApi({"football": []})
    clock = Clock(fetched(FB_LIVE))
    with store.lease("watcher:tennis", purpose="watch"):
        with pytest.raises(LeaseHeld):
            service(store, api, clock, explicit_scope(["football"], event_ids=[1])).run(Stop(clock))

    def try_watcher(n: int) -> None:
        with pytest.raises(LeaseHeld):
            store.lease("watcher:football", purpose="watch")

    service(store, api, clock, explicit_scope(["football"], tournament_ids=[1])).run(
        Stop(clock, rounds=1, on_wait=try_watcher))


@pytest.mark.parametrize("holder", ["live", "watcher:football"])
def test_a_data_operation_blocked_by_a_live_watcher_is_instance_running(store: Store, holder: str) -> None:
    from sofascore_scraper.errors import to_platform_error
    from sofascore_scraper.store.jobs import DataOperationRunningError, InstanceRunningConflict

    with store.lease(holder, purpose="watch"):
        with pytest.raises(InstanceRunningConflict) as refused:
            with store.jobs.exclusive("clear"):
                pass
    assert isinstance(refused.value, DataOperationRunningError)  # eski çağıranların yakaladığı sınıf
    assert refused.value.code == "instance_running" and holder in str(refused.value)
    assert to_platform_error(refused.value).code == "instance_running"


def test_the_legacy_alias_still_takes_league_ids(data_dir: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """
    P19: `main.py --watch` `ssc watch --source poll --stdout`tur (karar D18); --league-ids turnuva kapsamı,
    --watch-hours süre olur. Servis sahtedir: kapsamı, kaynağı ve süreyi kaydeder.
    """
    from types import SimpleNamespace

    import main

    seen: List[Dict[str, Any]] = []

    class Recorder:
        def __init__(self, store: Any, scope: Any, **options: Any) -> None:
            seen.append({"sports": [(s.sport, sorted(s.tournament_ids), sorted(s.event_ids)) for s in scope.sports],
                         "source": options.get("requested_source"), "data_dir": str(store.data_dir)})
            self.report = SimpleNamespace(rounds=0, requests=0, events=0, confirmed=0, to_dict=lambda: {})

        def run(self, stop: Any, until_seconds: Optional[float] = None) -> None:
            seen[-1]["until"] = until_seconds

    monkeypatch.setattr("sofascore_scraper.services.live.supervisor.LiveService", Recorder)
    assert main.main(["--watch", "--sport", "football", "--league-ids", "17,8", "--watch-hours", "2",
                      "--data-dir", str(data_dir)]) == 0
    assert seen == [{"sports": [("football", [8, 17], [])], "source": "poll", "data_dir": str(data_dir),
                     "until": 7200.0}]


# --- gözetim --------------------------------------------------------------------------------------------


class CrashingSource:
    """İlk `crashes` turda çöker, sonra hiçbir şey yapmaz. Kurulan her kopya sayılır."""

    made: List["CrashingSource"] = []

    def __init__(self, crashes: List[int]) -> None:
        self.crashes = crashes
        self.ticks = 0
        CrashingSource.made.append(self)

    def start(self, tracker: Any, event_ids: Any) -> None:
        pass

    def tick(self, tracker: Any) -> None:
        self.ticks += 1
        if self.crashes and self.crashes[0] > 0:
            self.crashes[0] -= 1
            raise RuntimeError("source failed")


def test_the_supervisor_restarts_a_crashing_source_with_back_off(store: Store) -> None:
    CrashingSource.made = []
    remaining = [2]
    clock = Clock(1_790_000_000.0)
    svc = service(store, FakeApi(), clock, explicit_scope(["football"], tournament_ids=[17]),
                  source_factory=lambda sport, get, clk: CrashingSource(remaining))
    stop = Stop(clock, rounds=6)
    report = svc.run(stop)
    assert report.source_restarts == 2 and len(CrashingSource.made) == 3
    assert stop.waits[:2] == [supervisor.SOURCE_RESTART_FIRST_SECONDS, supervisor.SOURCE_RESTART_FIRST_SECONDS * 2]
    assert CrashingSource.made[-1].ticks >= 1 and report.rounds == 6


def test_a_blocked_source_pauses_and_reports_blocked_and_recovered(store: Store) -> None:
    live = fx(FB_LIVE, 500, tournament_id=17)
    api = FakeApi({"football": [live]}, {500: live})
    api.blocked = 2
    clock = Clock(fetched(FB_LIVE))
    stop = Stop(clock, rounds=3)
    report = service(store, api, clock, explicit_scope(["football"], tournament_ids=[17])).run(stop)
    system = [(e.type, e.source) for e in live_events(store, "system")]
    assert system == [("system.blocked", "system"), ("system.recovered", "system")]
    assert stop.waits[:2] == [supervisor.BLOCKED_FIRST_SECONDS, supervisor.BLOCKED_FIRST_SECONDS * 2]
    assert not report.blocked


# --- meşgul depo ----------------------------------------------------------------------------------------


def busy_then(store: Store, monkeypatch: pytest.MonkeyPatch, failures: int) -> List[int]:
    real = store.streams.append
    calls: List[int] = []

    def append(stream: str, events: Any) -> Any:
        calls.append(1)
        if len(calls) <= failures:
            raise StoreBusy("busy")
        return real(stream, events)

    monkeypatch.setattr(store.streams, "append", append)
    return calls


def test_a_busy_append_is_retried_with_back_off(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = busy_then(store, monkeypatch, 3)
    waits: List[float] = []
    seqs = append_retrying(store, "live", [StreamEvent(type="live.stuck", event_id=1)], sleep=waits.append)
    assert seqs[0] is not None and len(calls) == 4
    assert waits == [0.5, 1.0, 2.0]


def test_a_busy_append_gives_up_only_while_stopping(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    busy_then(store, monkeypatch, 100)
    clock = Clock(0.0)
    stop = Stop(clock, rounds=1000)
    stop.set()
    with pytest.raises(StoreBusy):
        append_retrying(store, "live", [StreamEvent(type="live.stuck", event_id=1)], stop=stop)
    assert len(stop.waits) == supervisor.BUSY_ATTEMPTS_WHILE_STOPPING - 1


def test_the_service_does_not_end_on_a_busy_store(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    live, done = finish_scenario()
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))

    def finish(n: int) -> None:
        if n == 1:
            busy_then(store, monkeypatch, 2)
            api.live["football"] = []
            api.events[500] = done

    report = service(store, api, clock, explicit_scope(["football"], tournament_ids=[17]), confirm=False).run(
        Stop(clock, rounds=2, on_wait=finish))
    assert report.events == 1 and report.rounds == 2


def test_the_legacy_watcher_does_not_end_on_a_busy_store(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper.watcher import MatchWatcher

    not_started = fx("football/A_notstarted-0-not-started__17184998", 700)
    clock = Clock(not_started["startTimestamp"] + 5 * 3600)
    watcher = MatchWatcher("football", event_ids=[700], data_dir=str(data_dir),
                           fetch_json=FakeApi({"football": []}, {700: not_started}), clock=clock, sleep=clock.sleep)
    busy_then(watcher._store, monkeypatch, 2)
    watcher.start()  # stuck
    assert [e.type for e in live_events(watcher._store)] == ["live.stuck"]


# --- kapsam ---------------------------------------------------------------------------------------------


def test_scope_comes_from_live_follows(store: Store) -> None:
    store.follows.add(FollowSpec(kind="tournament", entity_id=17, name="Premier League", sport="football", live=True))
    store.follows.add(FollowSpec(kind="tournament", entity_id=8, name="LaLiga", sport="football", live=False))
    store.follows.add(FollowSpec(kind="event", entity_id=600, name="A match", sport="tennis", live=True))
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="A team", sport="basketball", live=True))
    store.follows.add(FollowSpec(kind="player", entity_id=7, name="A player", sport="tennis", live=True))
    store.follows.add(FollowSpec(kind="tournament", entity_id=99, name="Off", sport="football", live=True,
                                 enabled=False))
    scope = scope_from_follows(store.follows.list(enabled=True))
    assert [(s.sport, sorted(s.event_ids), sorted(s.tournament_ids), sorted(s.team_ids)) for s in scope.sports] == [
        ("basketball", [], [], [42]), ("football", [], [17], []), ("tennis", [600], [], [])]
    assert scope.from_follows and scope.skipped == ("player:7",)


def test_the_service_watches_the_follows_of_every_sport(store: Store) -> None:
    store.follows.add(FollowSpec(kind="tournament", entity_id=17, name="PL", sport="football", live=True))
    store.follows.add(FollowSpec(kind="event", entity_id=600, name="match", sport="tennis", live=True))
    football = fx(FB_LIVE, 500, tournament_id=17)
    other = fx(FB_LIVE, 501, tournament_id=8)
    tennis = fx(TN_LIVE, 600)
    api = FakeApi({"football": [football, other], "tennis": [tennis]}, {600: tennis})
    clock = Clock(fetched(FB_LIVE))
    report = service(store, api, clock).run(Stop(clock, rounds=1))
    assert report.sports == ("football", "tennis")
    assert set(store.watch.load("football")) == {"500"} and set(store.watch.load("tennis")) == {"600"}
    assert "/sport/football/events/live" in api.calls and "/sport/tennis/events/live" in api.calls


def test_a_new_live_follow_is_picked_up_while_running(store: Store) -> None:
    store.follows.add(FollowSpec(kind="tournament", entity_id=17, name="PL", sport="football", live=True))
    api = FakeApi({"football": [], "tennis": []})
    clock = Clock(1_790_000_000.0)

    def follow_tennis(n: int) -> None:
        if n == 1:
            store.follows.add(FollowSpec(kind="event", entity_id=600, name="match", sport="tennis", live=True))
            clock.now += supervisor.SCOPE_RELOAD_SECONDS

    report = service(store, api, clock).run(Stop(clock, rounds=3, on_wait=follow_tennis))
    assert report.sports == ("football", "tennis") and "/sport/tennis/events/live" in api.calls


def test_a_sport_filter_on_the_follows_holds_when_the_scope_is_read_again(store: Store) -> None:
    """V6: `--sport` süzgeci servisin ilk okumasında ve her yeniden okumada uygulanır; diğer sporlar izlenmez."""
    store.follows.add(FollowSpec(kind="tournament", entity_id=17, name="PL", sport="football", live=True))
    store.follows.add(FollowSpec(kind="event", entity_id=600, name="match", sport="tennis", live=True))
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="A team", sport="basketball", live=True))
    scope = scope_from_follows(store.follows.list(enabled=True), ["Football", "tennis"])
    assert [s.sport for s in scope.sports] == ["football", "tennis"] and scope.only_sports == ("football", "tennis")
    api = FakeApi({"football": [], "tennis": [], "basketball": [], "ice-hockey": []})
    clock = Clock(1_790_000_000.0)

    def follow_more(n: int) -> None:
        if n == 1:
            store.follows.add(FollowSpec(kind="tournament", entity_id=5, name="NHL", sport="ice-hockey", live=True))
            clock.now += supervisor.SCOPE_RELOAD_SECONDS

    report = service(store, api, clock, scope).run(Stop(clock, rounds=3, on_wait=follow_more))
    assert report.sports == ("football", "tennis")
    assert not any(c.startswith(("/sport/basketball", "/sport/ice-hockey")) for c in api.calls)


FB_NOT_STARTED = "football/A_notstarted-0-not-started__17184998"


def test_followed_events_far_from_kick_off_are_not_read_at_start(store: Store) -> None:
    """
    F35 (FX-23): takip edilen ve kaydı aylar sonra başlayacağını söyleyen maç izleme başında okunmaz; yakında
    başlayan ve kaydı olmayan maç eskisi gibi bir kez okunur. Başladığında canlı listede görülür.
    """
    now = 1_790_000_000.0
    far, soon, unknown = (fx(FB_NOT_STARTED, eid, tournament_id=17) for eid in (700, 701, 702))
    far["startTimestamp"] = int(now + 200 * 86400)
    soon["startTimestamp"] = int(now + 3600)
    unknown["startTimestamp"] = int(now + 200 * 86400)
    observed = dt.datetime.fromtimestamp(now - 60, dt.timezone.utc)
    store.events.observe(700, far, observed_at=observed)
    store.events.observe(701, soon, observed_at=observed)
    for eid in (700, 701, 702):
        store.follows.add(FollowSpec(kind="event", entity_id=eid, name=f"match {eid}", sport="football", live=True))
    api = FakeApi({"football": []}, {700: far, 701: soon, 702: unknown})
    clock = Clock(now)

    service(store, api, clock).run(Stop(clock, rounds=1))

    assert "/event/700" not in api.calls
    assert "/event/701" in api.calls and "/event/702" in api.calls
    assert set(store.watch.load("football")) == {"701", "702"}

    # Maç başladı: başlangıcı artık pencerenin içinde, izlenir (canlı listede de görünür)
    api.live["football"] = [fx(FB_LIVE, 700, tournament_id=17)]
    clock.now = float(far["startTimestamp"]) + 600
    service(store, api, clock).run(Stop(clock, rounds=1))
    assert store.watch.load("football")["700"]["class"] == "live"


def test_an_event_given_on_the_command_line_is_read_at_start_whatever_its_kick_off(store: Store) -> None:
    now = 1_790_000_000.0
    far = fx(FB_NOT_STARTED, 700, tournament_id=17)
    far["startTimestamp"] = int(now + 200 * 86400)
    store.events.observe(700, far, observed_at=dt.datetime.fromtimestamp(now - 60, dt.timezone.utc))
    api = FakeApi({"football": []}, {700: far})
    clock = Clock(now)

    service(store, api, clock, explicit_scope(["football"], event_ids=[700])).run(Stop(clock, rounds=1))

    assert "/event/700" in api.calls


def test_a_team_follow_watches_the_team_in_the_live_list() -> None:
    scope = SportScope("football", team_ids=frozenset({42}))
    assert scope.listed({"id": 1, "homeTeam": {"id": 42}})
    assert scope.listed({"id": 2, "awayTeam": {"id": 42}})
    assert not scope.listed({"id": 3, "homeTeam": {"id": 7}, "awayTeam": {"id": 8}})


# --- bakım ve durum -------------------------------------------------------------------------------------


def test_the_log_is_pruned_once_an_hour_while_the_live_lease_is_held(store: Store, monkeypatch: pytest.MonkeyPatch
                                                                     ) -> None:
    calls: List[Dict[str, Any]] = []
    monkeypatch.setattr(store.streams, "prune", lambda **kw: calls.append(kw) or 0)
    clock = Clock(1_790_000_000.0)
    svc = service(store, FakeApi({"football": []}), clock, explicit_scope(["football"], tournament_ids=[17]),
                  poll_interval=600)
    svc.run(Stop(clock, rounds=7))  # 7 tur x 10 dk
    assert len(calls) == 2
    assert calls[0] == {"max_age_s": 7 * 24 * 3600.0, "max_rows": 1_000_000}


def test_live_status_reports_the_holder_and_the_heartbeat(store: Store) -> None:
    clock = Clock(1_790_000_000.0)
    seen: List[Dict[str, Any]] = []
    assert live_status(store)["running"] is False and live_status(store)["last"] is None
    service(store, FakeApi({"football": []}), clock, explicit_scope(["football"], tournament_ids=[17])).run(
        Stop(clock, rounds=1, on_wait=lambda n: seen.append(live_status(store))))
    running = seen[0]
    assert running["running"] is True and running["pid"] == os.getpid() and running["source"] == "poll"
    assert running["sports"] == ["football"] and running["heartbeat_at"] == 1_790_000_000.0
    after = live_status(store)
    assert after["running"] is False and after["source"] is None and after["last"]["state"] == "stopped"


def test_every_source_runs_as_requested(store: Store) -> None:
    for requested, used in (("page", "page"), ("direct", "direct"), ("poll", "poll"), ("auto", "poll")):
        report = LiveService(store, explicit_scope(["football"], tournament_ids=[1]), fetch=FakeApi(),
                             requested_source=requested).report
        assert report.source == used


# --- olay günlüğü: kalınan yerden sürme ve boşluk --------------------------------------------------------


def test_events_after_a_sequence_resume_and_report_a_pruned_gap(cli: CliRunner, store: Store, data_dir: Path) -> None:
    for n in range(4):
        store.streams.append("live", [StreamEvent(type="live.stuck", event_id=n, sport="football")])
    seqs = [e.seq for e in live_events(store)]
    run = cli("events", "--data-dir", data_dir, "--after", seqs[1])
    lines = [json.loads(line) for line in run.stdout.splitlines()]
    assert [line["seq"] for line in lines[:-1]] == seqs[2:] and lines[-1]["gap"] is False
    store.streams.prune(max_rows=1)
    run = cli("events", "--data-dir", data_dir, "--after", seqs[1], "--json")
    assert run.data["gap"] is True and [e["seq"] for e in run.data["events"]] == seqs[3:]
    assert [w["code"] for w in run.json["warnings"]] == ["stream_gap"]


# --- zarf: sink'ler ve şema aynı belgeyi yazar ------------------------------------------------------------


def test_the_sink_envelope_is_the_schema_envelope(store: Store) -> None:
    from sofascore_scraper.schema import live_event_from_record
    from sofascore_scraper.sinks.base import Envelope

    live, done = finish_scenario()
    store_finished_before(store, done)
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE))

    def finish(n: int) -> None:
        api.live["football"] = []
        api.events[500] = done

    service(store, api, clock, explicit_scope(["football"], tournament_ids=[17])).run(
        Stop(clock, rounds=2, on_wait=finish))
    store.streams.append("job", [StreamEvent(type="job.started", data={"job_id": "01J"}, source="job")])
    records = store.streams.read().events
    assert {r.stream for r in records} >= {"live", "change", "job"}
    for record in records:
        assert Envelope.from_record(record).to_dict() == live_event_from_record(record).to_dict()


# --- ssc watch -------------------------------------------------------------------------------------------


class IdleOpener:
    """Sayfa açıcının sahtesi (`page` kaynağı): tarayıcı açmaz, push bağlantısı hiç kurulmaz."""

    def __init__(self) -> None:
        self.opened: List[str] = []

    def open(self, sport: str, feed: Any) -> Any:
        from types import SimpleNamespace

        self.opened.append(sport)
        return SimpleNamespace(ready=False, failed=None, close=lambda: None)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """`ssc watch`'ın servisine sahte API, saat ve sayfa açıcı verir; servis ilk beklemede durur."""
    live, done = finish_scenario()
    api = FakeApi({"football": []}, {500: live})
    clock = Clock(fetched(FB_LIVE))
    from sofascore_scraper.services.live.direct_source import DirectConnection

    idle_direct = DirectConnection(IdleOpener(), blocked=lambda: "no network in tests")  # `direct`: bağlanmaz
    monkeypatch.setattr(watch_command, "SERVICE_OPTIONS", {"fetch": api, "clock": clock, "sleep": clock.sleep,
                                                           "page_opener": IdleOpener(),
                                                           "direct_connection": idle_direct})

    real_run = LiveService.run

    def run_once(self: LiveService, stop: Any, *, until_seconds: Optional[float] = None) -> Any:
        fake = Stop(clock, rounds=2, on_wait=lambda n: api.events.__setitem__(500, done))
        report = real_run(self, fake, until_seconds=until_seconds)
        return report

    monkeypatch.setattr(LiveService, "run", run_once)
    return api


def test_watch_runs_the_service_and_reports_a_summary(cli: CliRunner, data_dir: Path, fake_service: FakeApi) -> None:
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", 500, "--source", "poll", "--json")
    assert run.exit_code == 0, run.stderr
    data = run.data
    assert (data["source"], data["requested_source"], data["sports"]) == ("poll", "poll", ["football"])
    assert data["events"] == 1 and data["confirmed"] == 1 and data["finished"] is True
    assert run.json["warnings"] == []


def test_watch_stdout_prints_the_new_events_as_json_lines(cli: CliRunner, data_dir: Path, fake_service: FakeApi
                                                          ) -> None:
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", 500, "--source", "poll", "--stdout")
    assert run.exit_code == 0, run.stderr
    lines = [json.loads(line) for line in run.stdout.splitlines()]
    assert [line["type"] for line in lines if line["stream"] == "live"] == ["live.status_changed"]
    assert all(set(line) == {"stream", "seq", "type", "ts", "event_id", "sport", "tournament_id", "source", "data"}
               for line in lines)
    assert "Live watching stopped" in run.stderr


def test_watch_uses_the_page_source_by_default_and_warns_when_direct_is_chosen(cli: CliRunner, data_dir: Path,
                                                                                fake_service: FakeApi) -> None:
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", 500, "--json")  # varsayılan: page
    assert run.json["warnings"] == [] and run.data["source"] == run.data["requested_source"] == "page"
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", 500, "--source", "direct", "--json")
    codes = [w["code"] for w in run.json["warnings"]]
    assert codes == ["live_direct_source"] and run.data["source"] == "direct"
    assert "terms-of-use grey area" in run.json["warnings"][0]["message"]


def test_watch_opens_live_pages_only_for_the_chosen_sports(cli: CliRunner, data_dir: Path, fake_service: FakeApi,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """V6: `--sport football --sport tennis` takip edilen öteki sporlara ne sayfa açar ne liste ister."""
    store = open_store(data_dir)
    for kind, eid, sport in (("tournament", 17, "football"), ("event", 600, "tennis"), ("team", 42, "basketball"),
                             ("tournament", 5, "ice-hockey")):
        store.follows.add(FollowSpec(kind=kind, entity_id=eid, name=f"{kind} {eid}", sport=sport, live=True))
    opener = IdleOpener()
    monkeypatch.setitem(watch_command.SERVICE_OPTIONS, "page_opener", opener)
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--sport", "tennis", "--source", "page",
              "--json")
    assert run.exit_code == 0, run.stderr
    assert run.data["sports"] == ["football", "tennis"]
    assert sorted(opener.opened) == ["football", "tennis"]
    assert not any(c.startswith(("/sport/basketball", "/sport/ice-hockey")) for c in fake_service.calls)


def test_watch_refuses_a_scope_it_cannot_use(cli: CliRunner, data_dir: Path, fake_service: FakeApi) -> None:
    run = cli("watch", "--data-dir", data_dir, "--event", 500, "--json")
    assert run.exit_code == 2 and "--sport" in run.error["message"]
    run = cli("watch", "--data-dir", data_dir, "--json")
    assert run.exit_code == 2 and "nothing to watch" in run.error["message"]
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", 1, "--stdout", "--json")
    assert run.exit_code == 2
    assert fake_service.calls == []


def test_watch_exits_6_when_another_live_service_holds_the_lease(cli: CliRunner, data_dir: Path,
                                                                 fake_service: FakeApi) -> None:
    store = open_store(data_dir)
    with store.lease("live", purpose="watch"):
        run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", 500, "--json")
    assert run.exit_code == 6 and run.error["code"] == "instance_running"
    assert fake_service.calls == []


def test_watch_hosts_the_configured_sinks(cli: CliRunner, data_dir: Path, tmp_path: Path, fake_service: FakeApi,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "sink" / "events.jsonl"
    monkeypatch.setenv("SOFASCORE_SINKS", json.dumps([{"name": "file", "type": "file", "path": str(out),
                                                       "events": ["live.*"]}]))
    run = cli("watch", "--data-dir", data_dir, "--sport", "football", "--event", 500, "--source", "poll", "--json")
    assert run.exit_code == 0, run.stderr
    # Yeni sink "şimdi"den başlar ve dağıtıcı durmadan önce birikenleri teslim eder: servisin olayı dosyadadır
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [line["type"] for line in lines] == ["live.status_changed"]


def test_watch_help_says_which_score_changes_are_events(cli: CliRunner) -> None:
    """FX-27 V7: set sporlarında `live.score_changed` yalnızca kazanılan set değişince gelir; yardım bunu söyler."""
    text = " ".join(cli("watch", "--help").stdout.split())
    assert "live.score_changed follows the headline score" in text and "not an event" in text
    text_tr = " ".join(cli("watch", "--help", "--lang=tr").stdout.split())
    assert "live.score_changed ana skoru izler" in text_tr


def test_watch_and_the_watch_sources_are_described(cli: CliRunner) -> None:
    described = cli("describe", "config").data["config"]
    sources = {entry["name"]: entry for entry in described["live_sources"]}
    assert list(sources) == ["page", "direct", "poll"]
    assert sources["page"]["default"] is True and sources["poll"]["available"] is True
    assert sources["page"]["available"] is True
    assert sources["direct"]["available"] is True and sources["direct"]["opt_in"] is True
    assert sources["direct"]["default"] is False
    assert "terms-of-use grey area" in sources["direct"]["warning"]
    commands = {c["name"]: c for c in cli("describe", "commands").data["commands"]["commands"]}
    flags = [option["flags"][0] for option in commands["watch"]["options"]]
    assert flags == ["--sport", "--event", "--tournament", "--source", "--stdout", "--hours"]


def test_the_watch_module_loads_nothing_heavy() -> None:
    import subprocess
    import sys

    code = ("import sys; import sofascore_scraper.cli.commands.watch; "
            "print(any(m.startswith(('sofascore_scraper.store', 'sofascore_scraper.services', 'sofascore_scraper.client', 'sofascore_scraper.sinks')) for m in sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                            cwd=Path(__file__).resolve().parent.parent)
    assert result.stdout.strip() == "False"


assert conftest  # sınır denetimi ve kancalar conftest'te kurulur


def test_the_package_exports_the_live_sources_lazily() -> None:
    """P24 ve P31'in adları da paket kökünden alınır (FX-15); kök onları ilk erişimde yükler."""
    import sofascore_scraper.services.live as live
    from sofascore_scraper.services.live import arbiter, direct_source, push_source

    assert live.PageSource is push_source.PageSource and live.SportArbiter is arbiter.SportArbiter
    assert live.DirectSource is direct_source.DirectSource
    for name in live.__all__:
        assert getattr(live, name) is not None, name
