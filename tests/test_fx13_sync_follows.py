"""
Eşitleme takip tablosunu okur (plan maddesi FX-13; docs/design/02-services.md 4.1 ve 4.3):

  * `sync_targets`: her kaynağın etkin turnuva takipleri (leagues.txt'in aynası, `[[follow]]`, API, `ssc follows`);
    kapalı takip ve takım / oyuncu / maç takipleri indirilmez; deposu olmayan bağlam yapılandırmanın liglerini okur.
  * takibin sezon seçimi: all, current, last:N, kimlikler (`pick_seasons`);
  * `FollowsSyncSpec`: yalnızca adı verilen takipler; takip edilmeyen kimlik atlanır ve günlüğe yazılır;
  * `mode="seasons"`: yalnızca sezon listeleri, tazelik süresine bakılmadan; program ve detay istenmez;
  * iş günlüğü satırları kodlarıyla (05-web-ui.md G24) — kod alamayan eski tutamaca yalnızca metin;
  * `ssc follows add` ile eklenen turnuva bir sonraki `ssc sync`in planındadır; `ssc follows remove` leagues.txt'ten
    gelen takibi dosyadan çıkarır (bir sonraki yansıtmada geri gelmez).

İstek atılmaz: indiriciler sahtedir.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

import conftest
import test_cli_skeleton as skeleton
from src.jobs.progress import JobProgress
from src.services.follows import ConfigLeagues, FollowsService
from src.services.listing import ListingResult
from src.services.sync import (
    SEASONS_PHASES,
    FollowsSyncSpec,
    SyncService,
    SyncSpec,
    pick_seasons,
    sync_targets,
)
from src.store import FollowSpec, Store, open_store
from test_cli_skeleton import CliRunner

cli = skeleton.cli

LEAGUES_FILE = os.path.join(conftest.CONFIG_DIR, "leagues.txt")
SEASONS = {17: [175, 174, 173, 172], 8: [85, 84, 83], 23: [235, 234], 99: [995, 994]}


class FakeConfig:
    """Yapılandırmanın servisin kullandığı yüzü: ligler (yedek yol) ve devre kesicinin eşikleri."""

    def __init__(self, leagues: Optional[Dict[int, str]] = None) -> None:
        self.leagues = dict(leagues or {})

    def get_leagues(self) -> Dict[int, str]:
        return dict(self.leagues)

    def get_rate_limit_threshold_consecutive(self) -> int:
        return 50

    def get_rate_limit_threshold_ratio(self) -> float:
        return 0.9

    def get_server_error_threshold_consecutive(self) -> int:
        return 50


class FakeSeasons:
    def __init__(self) -> None:
        self.listed: List[Tuple[int, Optional[float]]] = []

    def list_seasons(self, league_id: int, *, max_age: Optional[float] = None) -> ListingResult:
        self.listed.append((league_id, max_age))
        return ListingResult("seasons", league_id, seasons=self.get_seasons_for_league(league_id))

    def get_seasons_for_league(self, league_id: int) -> List[Dict[str, Any]]:
        return [{"id": sid, "name": f"S{sid}"} for sid in SEASONS.get(league_id, [])]

    def resolve_season_id(self, league_id: int, season_id: int) -> int:
        return 840 if season_id == 84 else season_id  # 84 eskimiş: SofaScore 840 diyor


class FakeSchedule:
    def __init__(self) -> None:
        self.calls: List[Tuple[int, int]] = []

    def list_schedule(self, league_id: int, season_id: int, *, max_age: Optional[float] = None) -> ListingResult:
        self.calls.append((league_id, season_id))
        return ListingResult("schedule", league_id, season_id, chunks=[{"round": 1}])


class FakeDetails:
    def __init__(self) -> None:
        self.collected: List[Tuple[Optional[str], Optional[List[int]]]] = []
        self.rate_limit_breaker_triggered = False
        self.last_status_counts: Dict[str, int] = {}
        self.refresh_listener: Any = None

    def begin_job_cache(self) -> None:
        pass

    def end_job_cache(self) -> None:
        pass

    def collect_detail_match_ids(self, league_id: Optional[str] = None, max_seasons: int = 0,
                                 only_season_ids: Optional[List[int]] = None) -> List[str]:
        self.collected.append((league_id, only_season_ids))
        return []

    def pending_detail_ids(self, ids: List[str]) -> List[str]:
        return []


class Handle:
    """İş yöneticisinin tutamacının servisin gördüğü yüzü; `log(message, **fields)` imzasıyla."""

    id = "job-fx13"

    def __init__(self, spec: SyncSpec, *, coded: bool = True) -> None:
        self.progress = JobProgress(list(spec.job_phases), lambda fields: None)
        self.lines: List[Dict[str, Any]] = []
        self.coded = coded

    def cancelled(self) -> bool:
        return False

    def log(self, message: str, **fields: Any) -> None:
        self.lines.append({"message": message, **fields})

    def publish(self, fields: Any) -> None:
        pass


class OldHandle(Handle):
    """FX-13'ten önceki imza: `log(message)`; kod verilmez."""

    def log(self, message: str) -> None:  # type: ignore[override]
        self.lines.append({"message": message})


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("DATA_DIR", str(path))
    return open_store(path)


def context(store: Optional[Store], leagues: Optional[Dict[int, str]] = None) -> SimpleNamespace:
    ctx = SimpleNamespace(config=FakeConfig(leagues), data_dir=str(store.data_dir) if store else "unused",
                          season_fetcher=FakeSeasons(), match_fetcher=FakeSchedule(), match_data_fetcher=FakeDetails())
    if store is not None:
        ctx.store = store
    return ctx


def follow(store: Store, kind: str, entity_id: int, name: str, *, origin: str = "api", **fields: Any) -> None:
    spec = FollowSpec(kind=kind, entity_id=entity_id, name=name, **fields)
    if origin == "api":
        store.follows.add(spec, origin="api")
    else:
        store.follows.apply([spec], origin=origin, prune=False)


# --- sezon seçimi ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("choice, expected", [
    ("all", [5, 4, 3]), ("current", [5]), ("last:2", [5, 4]), ("last:9", [5, 4, 3]), ((4, 9, 4), [4, 9]),
    ("unknown", [5, 4, 3]),
])
def test_pick_seasons(choice: Any, expected: List[int]) -> None:
    assert pick_seasons([5, 4, 3], choice) == expected
    assert pick_seasons([], "current") == []


# --- hangi turnuvalar -----------------------------------------------------------------------------------


def test_the_sync_reads_every_enabled_tournament_follow(store: Store) -> None:
    follow(store, "tournament", 17, "Premier League", origin="legacy")
    follow(store, "tournament", 8, "LaLiga", origin="config", seasons="current")
    follow(store, "tournament", 23, "Serie A", seasons="last:2")  # API ya da `ssc follows add`
    follow(store, "tournament", 99, "Off", enabled=False)
    follow(store, "team", 42, "A team")
    follow(store, "event", 12345, "A match")

    targets = sync_targets(context(store, leagues={1: "only in the config"}))

    assert {tid: (t.name, t.seasons) for tid, t in targets.items()} == {
        17: ("Premier League", "all"), 8: ("LaLiga", "current"), 23: ("Serie A", "last:2")}


def test_a_context_without_a_store_reads_the_leagues_of_the_configuration() -> None:
    targets = sync_targets(context(None, leagues={17: "Premier League"}))
    assert {tid: (t.name, t.seasons) for tid, t in targets.items()} == {17: ("Premier League", "all")}


def test_a_sync_downloads_each_follow_with_its_season_choice(store: Store) -> None:
    follow(store, "tournament", 17, "Premier League", origin="legacy")
    follow(store, "tournament", 8, "LaLiga", seasons=(84, 83))
    follow(store, "tournament", 23, "Serie A", seasons="current")
    ctx = context(store)
    spec = SyncSpec(mode="full", export=False)
    handle = Handle(spec)

    result = SyncService(ctx).run(spec, handle=handle)  # type: ignore[arg-type]

    assert result.state == "succeeded"
    assert [lid for lid, _ in ctx.season_fetcher.listed] == [8, 17, 23]
    assert ctx.match_fetcher.calls == [(8, 840), (8, 83), (17, 175), (17, 174), (17, 173), (17, 172), (23, 235)]
    assert ctx.match_data_fetcher.collected == [(None, None)]  # detay aşaması bugünkü gibi bütün katalog


def test_a_single_tournament_keeps_every_season(store: Store) -> None:
    """`--tournament` ve `ssc fetch tournament`: takibin seçimi uygulanmaz (bugünkü gibi bütün sezonlar)."""
    follow(store, "tournament", 23, "Serie A", seasons="current")
    ctx = context(store)
    spec = SyncSpec(mode="full", league_id=23, export=False)
    SyncService(ctx).run(spec, handle=Handle(spec))  # type: ignore[arg-type]
    assert ctx.match_fetcher.calls == [(23, 235), (23, 234)]


def test_named_follows_only(store: Store) -> None:
    follow(store, "tournament", 17, "Premier League", origin="legacy")
    follow(store, "tournament", 23, "Serie A", seasons="last:1")
    ctx = context(store)
    spec = FollowsSyncSpec(mode="full", export=False, follows=("tournament:23", "tournament:5", "team:42"))
    handle = Handle(spec)

    SyncService(ctx).run(spec, handle=handle)  # type: ignore[arg-type]

    assert [lid for lid, _ in ctx.season_fetcher.listed] == [23]
    assert ctx.match_fetcher.calls == [(23, 235)]
    assert ctx.match_data_fetcher.collected == [("23", None)]
    skipped = [line for line in handle.lines if line.get("code") == "sync_follow_skipped"]
    assert [line["params"]["follow"] for line in skipped] == ["tournament:5", "team:42"]


def test_season_lists_only(store: Store) -> None:
    follow(store, "tournament", 17, "Premier League", origin="legacy")
    follow(store, "tournament", 8, "LaLiga")
    ctx = context(store)
    spec = SyncSpec(mode="seasons", export=False)
    assert spec.job_phases == SEASONS_PHASES
    handle = Handle(spec)

    result = SyncService(ctx).run(spec, handle=handle)  # type: ignore[arg-type]

    assert result.state == "succeeded"
    assert ctx.season_fetcher.listed == [(8, None), (17, None)]  # tazelik süresine bakılmaz
    assert ctx.match_fetcher.calls == [] and ctx.match_data_fetcher.collected == []
    assert [line["code"] for line in handle.lines] == ["sync_season_list", "sync_season_list"]
    assert handle.lines[0]["params"] == {"league_id": 8}


def test_log_lines_carry_codes_and_an_old_handle_gets_the_text_only(store: Store) -> None:
    follow(store, "tournament", 23, "Serie A", seasons="current")
    spec = SyncSpec(mode="full", export=False)
    coded, old = Handle(spec), OldHandle(spec)
    SyncService(context(store)).run(spec, handle=coded)  # type: ignore[arg-type]
    SyncService(context(store)).run(spec, handle=old)  # type: ignore[arg-type]

    assert [line["message"] for line in coded.lines] == [line["message"] for line in old.lines]
    assert all("code" not in line for line in old.lines)
    assert [line["code"] for line in coded.lines] == [
        "sync_season_list", "sync_schedule", "sync_details_checking"]
    assert coded.lines[1]["params"] == {"league_id": 23, "season_id": 235}


# --- komut satırı ---------------------------------------------------------------------------------------


@contextlib.contextmanager
def leagues_file(leagues: Dict[int, str]) -> Iterator[None]:
    """Testin config dizinindeki leagues.txt'i yazar; çıkışta eski içeriğe döner."""
    before = Path(LEAGUES_FILE).read_bytes() if os.path.exists(LEAGUES_FILE) else None
    lines = ["# League configuration file", ""] + [f"{name}: {lid}" for lid, name in leagues.items()]
    previous = os.stat(LEAGUES_FILE).st_mtime_ns if os.path.exists(LEAGUES_FILE) else 0
    Path(LEAGUES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    stamp = max(time.time_ns(), previous + 1_000_000_000)
    os.utime(LEAGUES_FILE, ns=(stamp, stamp))
    try:
        yield
    finally:
        if before is None:
            os.remove(LEAGUES_FILE)
        else:
            Path(LEAGUES_FILE).write_bytes(before)
            os.utime(LEAGUES_FILE, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))


def test_a_tournament_added_with_the_cli_is_in_the_next_sync_plan(cli: CliRunner, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with leagues_file({17: "Premier League"}):
        added = cli("--data-dir", str(data_dir), "follows", "add", "tournament", "8", "--name", "LaLiga", "--json")
        assert added.exit_code == 0, added.stderr
        assert added.data["follow"]["origin"] == "api"

        plan = cli("--data-dir", str(data_dir), "sync", "--dry-run", "--json")
        assert plan.exit_code == 0, plan.stderr
        assert plan.data["tournaments"] == [8, 17]

        seasons_only = cli("--data-dir", str(data_dir), "sync", "--only", "seasons", "--dry-run", "--json")
        assert seasons_only.data["listings"] == {"season_lists": 2, "schedules": 0, "fresh": 0}
        assert seasons_only.data["requests"] == 2 and seasons_only.data["events"]["full"] == 0


def test_removing_a_leagues_txt_follow_with_the_cli_removes_it_from_the_file(cli: CliRunner, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with leagues_file({17: "Premier League", 8: "LaLiga"}):
        listed = cli("--data-dir", str(data_dir), "sync", "--dry-run", "--json")  # bağlam tabloyu yansıtır
        assert listed.data["tournaments"] == [8, 17]

        removed = cli("--data-dir", str(data_dir), "follows", "remove", "tournament", "8", "--json")
        assert removed.exit_code == 0, removed.stderr
        assert removed.data["removed"] is True
        assert "LaLiga" not in Path(LEAGUES_FILE).read_text(encoding="utf-8")

        again = cli("--data-dir", str(data_dir), "sync", "--dry-run", "--json")
        assert again.data["tournaments"] == [17]
        follows = cli("--data-dir", str(data_dir), "follows", "list", "--json").data["follows"]
        assert [(f["entity_id"], f["origin"]) for f in follows] == [(17, "legacy")]


def test_the_cli_follows_commands_use_the_service_rules(cli: CliRunner, tmp_path: Path) -> None:
    """Ad kuralı servisinkidir (leagues.txt'e ve dizin adına dönüşebilen ad): `:` ve `/` olamaz."""
    data_dir = tmp_path / "data"
    bad = cli("--data-dir", str(data_dir), "follows", "add", "tournament", "8", "--name", "La/Liga", "--json")
    assert (bad.exit_code, bad.error["code"]) == (2, "invalid_request")
    store = open_store(data_dir)
    assert FollowsService(store, ConfigLeagues(), config_file=True).list() == []
