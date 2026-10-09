"""
Dışa aktarmanın katılımcı süzgeci (B1, e2e F13: dışa aktarma yalnızca lig takibiyle süzülebiliyordu; bir takım ya da
oyuncu takibi seçilemiyordu):

  * servis: `DatasetFilter.team_ids` / `player_ids` ve `ExportSpec.team_ids` / `player_ids`; takım maçın iki
    tarafından biridir, oyuncu maçın saklanan kadrosundadır; ikisi tek süzgeçtir (biri yeter), öteki süzgeçlerle VE;
    uyan maç yoksa hiçbir şey dışa aktarılmaz (boş süzgeç "hepsi" demek değildir);
  * iş belirtimi ve API: `filter.team_ids` / `filter.player_ids` (`POST /api/v1/jobs`, `GET /api/v1/exports`);
  * komut satırı: `ssc export --team ID --player ID`.

Ağ yok: maçlar ve kadrolar sahte yüklerle depoya yazılır.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

import conftest
import store_fixtures as sf
import test_cli_skeleton as skeleton
from sofascore_scraper.errors import NotFoundError
from sofascore_scraper.services.data_jobs import ExportRequest, export_name, run_export
from sofascore_scraper.services.export import (DatasetFilter, DatasetSpec, ExportService, ExportSpec,
                                               lineup_players)
from sofascore_scraper.services.follow_sync import PLAYER, listed_key
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import FollowSpec, JobStore, Store, default_db_path, open_store
from sofascore_scraper.web import deps
from sofascore_scraper.web.api.v1.jobs import export_request
from sofascore_scraper.web.app import app

client = TestClient(app)
cli = skeleton.cli  # komut satırı fikstürü
CASE = "football/A_finished-100-ended__16837335"

# Takımlar (yarışmacı kimliği) ve maçlar. Kadrodaki oyuncu kimlikleri store_fixtures'ın kuralıyla: takım × 10 + 1
# (ilk on bir), takım × 10 + 2 (yedek)
A, B, C, D, E = 1001, 1002, 1003, 1004, 1005
E1, E2, E3, E4 = 9700001, 9700002, 9700003, 9700004
A_STARTER, A_SUB, D_STARTER = A * 10 + 1, A * 10 + 2, D * 10 + 1
MATCHES = (
    # (maç, lig, sezon, ev sahibi, deplasman, kadro saklı mı)
    (E1, sf.PL, sf.PL_2526, A, B, True),
    (E2, sf.PL, sf.PL_2526, A, C, False),
    (E3, sf.PL, sf.PL_2526, D, E, True),
    (E4, sf.LALIGA, sf.LALIGA_2526, A, D, True),
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(data_dir))
    found = open_store(data_dir)
    for eid, league, season, home, away, with_lineups in MATCHES:
        payload = sf.basic_payload(sf.Ev(CASE, league, season, "Home", "Away", eid=eid))
        payload["homeTeam"] = {"id": home, "name": f"Team {home}", "slug": f"team-{home}"}
        payload["awayTeam"] = {"id": away, "name": f"Team {away}", "slug": f"team-{away}"}
        outcomes = {"event": Outcome(SLICE_OK, data=payload)}
        if with_lineups:
            outcomes["lineups"] = Outcome(SLICE_OK, data=sf.slice_payload("lineups", payload))
        found.events.put(eid, outcomes)
        # her maçın bir değişiklik satırı (değişiklik kümesi için)
        found.changes.append({
            "ts_utc": "2026-09-30T12:00:00+00:00", "event_id": eid, "sport": "football",
            "tournament": {"id": league.id, "name": None}, "start_ts": 1789990000, "status_regressed": False,
            "changed": {"status.type": ["inprogress", "finished"]}, "status_class": ["live", "completed"],
        })
    return found


def ids(store: Store, **fields: Any) -> List[int]:
    records = ExportService(store).records("events", DatasetFilter(**fields))
    return [record.id for record in records]  # type: ignore[attr-defined]


# --- servis ----------------------------------------------------------------------------------------------------


def test_a_team_stands_for_the_events_it_played(store: Store) -> None:
    assert sorted(ids(store, team_ids=(A,))) == [E1, E2, E4]
    assert sorted(ids(store, team_ids=(B, E))) == [E1, E3]


def test_a_player_stands_for_the_events_whose_stored_lineups_name_him(store: Store) -> None:
    # E2'nin kadrosu saklı değil: A'nın oyuncusu orada bulunmaz
    assert sorted(ids(store, player_ids=(A_STARTER,))) == [E1, E4]
    assert sorted(ids(store, player_ids=(A_SUB,))) == [E1, E4]  # yedekler de sayılır
    assert ids(store, player_ids=(424242,)) == []


def test_a_player_follows_stored_match_list_comes_before_the_lineups(store: Store) -> None:
    """
    FX-34: oyuncu takibinin saklanan maç listesi (B2, `follow_events:player:<kimlik>`) önce okunur ve kadrolarla
    birleşir: kadrosu saklanmamış maç (E2) da bulunur; katalogda olmayan kimlik ve öteki süzgeçler dışı kalır.
    """
    def remember(player: int, events: List[int]) -> None:
        store.runtime.set(listed_key(PLAYER, player), {"events": events, "listed_at": 0, "complete": True})

    remember(A_STARTER, [E2, 9799999])
    assert sorted(ids(store, player_ids=(A_STARTER,))) == [E1, E2, E4]
    assert sorted(ids(store, player_ids=(A_STARTER,), tournament_ids=(sf.PL.id,))) == [E1, E2]
    assert ids(store, player_ids=(A_STARTER,), event_ids=(E2, E3)) == [E2]
    assert ids(store, player_ids=(A_STARTER,), sport="basketball") == []
    # kadroda adı geçmeyen oyuncu: yalnızca listesi
    remember(424242, [E3])
    assert ids(store, player_ids=(424242,)) == [E3]
    assert sorted(ids(store, player_ids=(424242, D_STARTER))) == [E3, E4]
    # okunamayan liste (bozuk biçim) yalnızca kadrolara düşer
    store.runtime.set(listed_key(PLAYER, A_SUB), {"events": "broken"})
    assert sorted(ids(store, player_ids=(A_SUB,))) == [E1, E4]


def test_teams_and_players_are_one_filter_and_the_others_still_apply(store: Store) -> None:
    # biri yeter: C'nin maçı (E2) ya da D'nin ilk on birindekinin maçları (E3, E4)
    assert sorted(ids(store, team_ids=(C,), player_ids=(D_STARTER,))) == [E2, E3, E4]
    # öteki süzgeçlerle VE: lig, maç numarası, spor
    assert sorted(ids(store, team_ids=(A,), tournament_ids=(sf.PL.id,))) == [E1, E2]
    assert ids(store, team_ids=(A,), event_ids=(E2, E3)) == [E2]
    assert ids(store, player_ids=(D_STARTER,), season_ids=(sf.LALIGA_2526.id,)) == [E4]
    assert ids(store, team_ids=(A,), sport="basketball") == []


def test_no_matching_event_exports_nothing(store: Store, tmp_path: Path) -> None:
    """Uyan maç yoksa boş maç süzgeci "hepsi" olmaz: dışa aktarma boştur ya da not_found."""
    assert ids(store, team_ids=(31337,)) == []
    spec = DatasetSpec(dataset="events", format="jsonl", filter=DatasetFilter(team_ids=(31337,)))
    with pytest.raises(NotFoundError):
        ExportService(store).export_dataset(spec, str(tmp_path / "none.jsonl"), allow_empty=False)
    raw = DatasetSpec(dataset="slices", format="jsonl", schema="raw", filter=DatasetFilter(player_ids=(31337,)))
    with pytest.raises(NotFoundError):
        ExportService(store).export_dataset(raw, str(tmp_path / "none-raw.jsonl"), allow_empty=False)
    assert list(ExportService(store).records("changes", DatasetFilter(team_ids=(31337,)))) == []
    assert ExportService(store).legacy_table(ExportSpec(team_ids=(31337,))).rows == ()


def test_every_dataset_takes_the_participant_filter(store: Store, tmp_path: Path) -> None:
    def rows(dataset: str, **fields: Any) -> List[Dict[str, Any]]:
        dest = tmp_path / f"{dataset}-{len(fields)}-{time.monotonic_ns()}.jsonl"
        run_export(store, ExportRequest(dataset=dataset, format="jsonl", **fields), str(dest))  # type: ignore[arg-type]
        return [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines()]

    slices = rows("slices", team_ids=(B,))
    assert {r["owner_id"] for r in slices} == {E1} and {r["key"] for r in slices} >= {"event", "lineups"}
    changes = rows("changes", team_ids=(D,))
    assert {r["event_id"] for r in changes} == {E3, E4}
    everything = rows("changes")
    assert len(changes) == len([r for r in everything if r["event_id"] in (E3, E4)])
    # ham: yalnızca katılımcının maçlarının yükleri
    raw = tmp_path / "raw.jsonl"
    result = run_export(store, ExportRequest(dataset="events", format="jsonl", schema="raw", player_ids=(A_STARTER,)),
                        str(raw))
    assert result["events"] == 2
    assert sorted(json.loads(line)["event_id"] for line in raw.read_text(encoding="utf-8").splitlines()) == [E1, E4]


def test_the_legacy_wide_csv_takes_teams_and_players(store: Store, tmp_path: Path) -> None:
    dest = tmp_path / "wide.csv"
    result = run_export(store, ExportRequest(profile="legacy-wide-csv", team_ids=(E,), player_ids=(A_SUB,)),
                        str(dest))
    with dest.open(encoding="utf-8", newline="") as handle:
        assert sorted(int(row["match_id"]) for row in csv.DictReader(handle)) == [E1, E3, E4]
    assert result["rows"] == 3


def test_the_lineup_reader() -> None:
    lineups = {
        "home": {"players": [{"player": {"id": 1}}, {"player": {"id": 2}, "substitute": True}],
                 "missingPlayers": [{"player": {"id": 3}}]},
        "away": {"players": [{"player": {"id": 4}}, {"player": {"id": True}}, {"player": "x"}, "y"]},
    }
    assert lineup_players(lineups) == frozenset({1, 2, 4})
    for bad in (None, [], {"home": None}, {"home": {"players": {"a": 1}}}):
        assert lineup_players(bad) == frozenset()


def test_the_file_is_named_after_a_single_team_or_player(store: Store) -> None:
    moment = 1791331200.0  # 2026-10-07T00:00:00Z
    job = "01M4949EJJWC8MPBBNH826T4ED"
    assert export_name(store, job, ExportRequest(team_ids=(A,)), now=moment) == "team-1001_2026-10-07_h826t4ed.csv"
    store.follows.add(FollowSpec(kind="team", entity_id=A, name="Göztepe"), origin="api")
    store.follows.add(FollowSpec(kind="player", entity_id=A_STARTER, name="Victor Osimhen"), origin="api")
    assert export_name(store, job, ExportRequest(team_ids=(A,)), now=moment) == "goztepe_2026-10-07_h826t4ed.csv"
    assert export_name(store, job, ExportRequest(dataset="odds", format="jsonl", player_ids=(A_STARTER,)),
                       now=moment) == "victor-osimhen_2026-10-07_h826t4ed.jsonl"
    # bilinmeyen oyuncu, iki katılımcı ya da bir lig: eski kural
    assert export_name(store, job, ExportRequest(player_ids=(77,)), now=moment) == "events_2026-10-07_h826t4ed.csv"
    assert export_name(store, job, ExportRequest(team_ids=(A, B)), now=moment) == "events_2026-10-07_h826t4ed.csv"
    assert export_name(store, job, ExportRequest(tournament_ids=(sf.PL.id,), team_ids=(A,)),
                       now=moment).startswith("premier-league_")


# --- API ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def jobs(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    before = conftest.job_threads()
    job_store = JobStore(default_db_path(str(store.data_dir)))
    monkeypatch.setattr(deps, "job_store", lambda: job_store)
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


def _data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def _finished(job_id: str) -> Dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        job = _data(client.get(f"/api/v1/jobs/{job_id}"))
        if job["finished_at"]:
            return job
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_the_job_spec_carries_the_participant_filter() -> None:
    req = export_request({"dataset": "events", "filter": {"team_ids": [A], "player_ids": [A_STARTER, 9]}})
    assert (req.team_ids, req.player_ids) == ((A,), (A_STARTER, 9))
    assert (export_request({}).team_ids, export_request({}).player_ids) == ((), ())


def test_an_export_job_filtered_by_a_team_and_a_player(store: Store, jobs: JobStore) -> None:
    job = _data(client.post("/api/v1/jobs", json={"kind": "export", "spec": {
        "dataset": "events", "format": "jsonl", "filter": {"team_ids": [C], "player_ids": [D_STARTER]}}}), 202)
    done = _finished(job["id"])
    assert done["state"] == "succeeded", done
    assert done["result"]["export"]["rows"] == 3
    path = store.data_dir / "exports" / done["result"]["export"]["file"]
    assert sorted(json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()) == [E2, E3, E4]
    listed = _data(client.get("/api/v1/exports"))
    assert listed[0]["filter"]["team_ids"] == [C] and listed[0]["filter"]["player_ids"] == [D_STARTER]


@pytest.mark.parametrize("bad", [{"team_ids": [0]}, {"player_ids": [-3]}, {"team_ids": ["x"]}, {"teams": [1]}])
def test_bad_participant_ids_are_refused(bad: Dict[str, Any], store: Store, jobs: JobStore) -> None:
    r = client.post("/api/v1/jobs", json={"kind": "export", "spec": {"dataset": "events", "filter": bad}})
    assert r.status_code == 422, r.text


# --- komut satırı ------------------------------------------------------------------------------------------------


def _cli_ids(run: Any) -> Optional[List[int]]:
    assert run.exit_code == 0, run.stderr
    lines = Path(run.data["path"]).read_text(encoding="utf-8").splitlines()
    return sorted(json.loads(line)["id"] for line in lines)


def test_ssc_export_takes_team_and_player(store: Store, cli: Any, tmp_path: Path) -> None:
    base = ("--data-dir", str(store.data_dir), "export", "--dataset", "events", "--format", "jsonl", "--json")
    by_team = cli(*base, "--team", str(A), "--out", str(tmp_path / "a.jsonl"))
    assert _cli_ids(by_team) == [E1, E2, E4]
    both = cli(*base, "--team", str(C), "--player", str(D_STARTER), "--tournament", str(sf.PL.id),
               "--out", str(tmp_path / "b.jsonl"))
    assert _cli_ids(both) == [E2, E3]
    nothing = cli(*base, "--player", "31337", "--out", str(tmp_path / "c.jsonl"))
    assert nothing.exit_code == 1 and not (tmp_path / "c.jsonl").exists()
    bad = cli(*base, "--team", "0")
    assert bad.exit_code == 2


def test_ssc_export_wide_csv_takes_team_and_player(store: Store, cli: Any, tmp_path: Path) -> None:
    out = tmp_path / "wide.csv"
    run = cli("--data-dir", str(store.data_dir), "export", "--team", str(B), "--player", str(D_STARTER),
              "--out", str(out), "--json")
    assert run.exit_code == 0, run.stderr
    with out.open(encoding="utf-8", newline="") as handle:
        assert sorted(int(row["match_id"]) for row in csv.DictReader(handle)) == [E1, E3, E4]
    refused = cli("--data-dir", str(store.data_dir), "export", "--team", str(B), "--sport", "football")
    assert refused.exit_code == 2 and "--team" in refused.stderr
    none = cli("--data-dir", str(store.data_dir), "export", "--team", "31337", "--out", str(tmp_path / "x.csv"),
               "--json")
    assert none.exit_code == 1
    assert json.loads(none.stdout)["error"]["details"]["filters"] == {
        "tournaments": [], "events": [], "teams": [31337], "players": []}
