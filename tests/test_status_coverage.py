"""
Kapsam raporu katalogdan (plan maddesi P15): `StatusService.coverage()`.

Üç küme:

  * fikstür dizinleri (`tests/store_fixtures.py`): her dizinde rapor özetin sayılarıyla tutarlıdır; `canonical`
    dizininde her maçın eksik dilimleri dosyalardan elle hesaplanan beklentiye (dilim dosyası, `_unavailable.json`
    sayacı) eşittir;
  * Store API'siyle yazılmış maçlar: tam maç, eksik dilim, yeterince denenip boş gelen dilim, yalnızca listeden
    bilinen maç, turnuvasız maç, kapsam süzgeci ve sıralar;
  * rapor hiçbir dosya yazmaz.
Ağ yok.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

import conftest
import store_fixtures as sf
from sofascore_scraper.services import planning
from sofascore_scraper.services import status as status_module
from sofascore_scraper.services.status import CoverageReport, SeasonCoverage, StatusService, TournamentCoverage
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_OK, Outcome, match_detail_slice_present
from sofascore_scraper.sports import event_sport_slug, sport_slugs
from sofascore_scraper.status import classify_status
from sofascore_scraper.store import Ref, Scope, Store, open_store

REQUIRED = planning.expected_slice_keys("football")


@pytest.fixture(autouse=True)
def _default_setting(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SOFASCORE_FETCH__ONLY_FINISHED", raising=False)
    status_module.forget_sizes()
    yield
    status_module.forget_sizes()


def build(name: str, tmp_path: Path) -> sf.LegacyFixture:
    fixture = sf.build_fixture(name, tmp_path / "data")
    conftest.STORE_BOUNDARY.add_data_dir(str(fixture.data_dir))
    return fixture


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return build(request.param, tmp_path)


def tree(path: Path) -> Dict[str, Tuple[int, int]]:
    """Dizindeki dosyalar (`.meta` hariç): göreli yol → (boyut, mtime_ns)."""
    found: Dict[str, Tuple[int, int]] = {}
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".meta"]
        for name in files:
            full = os.path.join(root, name)
            stat = os.stat(full)
            found[os.path.relpath(full, path)] = (stat.st_size, stat.st_mtime_ns)
    return found


# --- fikstür dizinleri ------------------------------------------------------------------------------


def test_coverage_agrees_with_the_summary(fx: sf.LegacyFixture) -> None:
    """Rapor detayı saklanan her maçı bir kez sayar (özetin `details`'i); turnuva ve sezon toplamları tutarlı."""
    store = open_store(fx.data_dir)
    service = StatusService(store)
    report = service.coverage()
    summary = service.summary(tournament_ids=tuple(fx.leagues), sizes=False)

    assert report.catalog_rebuild_reason is None
    assert report.data_dir == str(store.data_dir)
    assert report.matches == summary.details
    assert {t.tournament_id: t.matches for t in report.tournaments} == {
        t.tournament_id: t.details for t in summary.tournaments if t.details}
    assert report.complete == sum(t.complete for t in report.tournaments)
    for tournament in report.tournaments:
        assert tournament.matches == sum(s.matches for s in tournament.seasons)
        assert tournament.complete == sum(s.complete for s in tournament.seasons)
        for key, count in tournament.missing.items():
            assert count == sum(s.missing.get(key, 0) for s in tournament.seasons)
            assert 0 < count <= tournament.matches - tournament.complete
    for key, count in report.missing.items():
        assert count == sum(t.missing.get(key, 0) for t in report.tournaments)
    # her eksik bir sporun beklediği bir dilimdir (FX-16: teniste point_by_point ortak kümede değil)
    expected = {key for sport in (None, *sport_slugs()) for key in planning.expected_slice_keys(sport)}
    assert all(key in expected for key in report.missing)


def test_complete_means_the_planner_has_nothing_to_fill_but_a_confirmation(fx: sf.LegacyFixture) -> None:
    """
    Tam sayılan maç, planlayıcının doldurmayı beklediği dilimi olmayan maçtır (aynı eşik, aynı beklenen dilimler);
    bitmiş maçta son yanıtı "veri yok" olan dilim, planlayıcı onu doğrulamak için bir kez daha istese de çözülmüş
    sayılır (FX-23: ilk indirmeden sonra tamlık "%0" görünmesin).
    """
    store = open_store(fx.data_dir)
    report = StatusService(store).coverage()
    incomplete = 0
    for state in store.events.states():
        if state.event.has_event_payload and planning.unresolved_slice_keys(state):
            incomplete += 1
        assert set(planning.unresolved_slice_keys(state)) <= set(planning.missing_slice_keys(state))
    assert report.matches - report.complete == incomplete


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def file_missing(directory: Path) -> Tuple[str, ...]:
    """
    Dosyalardan elle: veri dosyası olmayan ve `_unavailable.json` sayacı eşiğe varmamış beklenen dilimler. Bitmiş
    maçta sayacı olan ("veri yok" denmiş) dilim eksik sayılmaz (FX-23).
    """
    unavailable = _read(directory / "_unavailable.json")
    counts = {str(k): int(v) for k, v in unavailable.items()} if isinstance(unavailable, dict) else {}
    basic = _read(directory / "basic.json")
    event = basic.get("event", basic) if isinstance(basic, dict) else None
    finished = classify_status(event).value in ("completed", "decided_without_play")
    status = _read(directory / "_slice_status.json")
    failed_last = {str(k) for k, v in status.items() if isinstance(v, dict) and "error" in v} \
        if isinstance(status, dict) else set()
    missing: List[str] = []
    # Maçın sporunun ve evresinin beklediği dilimler (FX-16'dan beri futbolda pregame_form yok, teniste
    # point_by_point var; FX-31'den beri istatistik maç başlamadan beklenmez)
    phase = planning.phase_of(classify_status(event).value) if isinstance(event, dict) else None
    for key in planning.expected_slice_keys(event_sport_slug(event) if isinstance(event, dict) else None,
                                            phase=phase):
        body = _read(directory / f"{key}.json")
        if body is not None and match_detail_slice_present(key, {key: body}):
            continue
        if counts.get(key, 0) >= planning.DEFAULT_EMPTY_THRESHOLD:
            continue
        if finished and counts.get(key, 0) > 0 and key not in failed_last:
            continue  # son yanıt "veri yok" (son isteği başarısız olan dilim değil)
        missing.append(key)
    return tuple(missing)


def test_canonical_fixture_matches_the_files(tmp_path: Path) -> None:
    """`canonical` dizini: eksik dilimler, maç maç, dosyalardan elle hesaplanan beklentiyle aynı."""
    fixture = build("canonical", tmp_path)
    expected_missing: Dict[str, int] = {}
    by_tournament: Dict[Optional[int], Tuple[int, int]] = {}
    for record in fixture.details:
        assert record.has_basic and not record.combined
        missing = file_missing(fixture.data_dir / record.path)
        for key in missing:
            expected_missing[key] = expected_missing.get(key, 0) + 1
        matches, complete = by_tournament.get(record.league_id, (0, 0))
        by_tournament[record.league_id] = (matches + 1, complete + (not missing))

    report = StatusService(open_store(fixture.data_dir)).coverage()
    assert report.matches == len(fixture.details)
    assert dict(report.missing) == expected_missing
    assert {t.tournament_id: (t.matches, t.complete) for t in report.tournaments} == by_tournament
    assert report.complete < report.matches  # fikstürde eksik dilimli maç var: kural gerçekten sınanıyor
    assert report.completion_rate == round(report.complete / report.matches * 100, 2)


def test_the_report_writes_no_file(fx: sf.LegacyFixture) -> None:
    store = open_store(fx.data_dir)
    before = tree(fx.data_dir)
    StatusService(store).coverage()
    assert tree(fx.data_dir) == before


# --- Store API'siyle yazılmış maçlar ----------------------------------------------------------------

FINISHED = "football/A_finished-100-ended__16837335"
OTHER_FINISHED = "football/A_finished-110-aet__17148332"
NOT_STARTED = "football/A_notstarted-0-not-started__17184998"


def _payload(league: sf.League, season: sf.Season, eid: int, case: str = FINISHED) -> Dict[str, Any]:
    return sf.basic_payload(sf.Ev(case, league, season, f"Home {eid}", f"Away {eid}", eid=eid))


def put(store: Store, payload: Dict[str, Any], *, have: Tuple[str, ...] = REQUIRED,
        empty: Tuple[str, ...] = (), empties: int = 1) -> int:
    """Maçı yazar: `have` dilimleri verili, `empty` dilimleri `empties` kez kesin "veri yok" almış."""
    event_id = int(payload["id"])
    outcomes: Dict[str, Outcome] = {"event": Outcome(SLICE_OK, data=payload)}
    for key in have:
        outcomes[key] = Outcome(SLICE_OK, data=sf.slice_payload(key, payload))
    store.events.put(event_id, outcomes)
    for _ in range(empties if empty else 0):
        store.events.put(event_id, {key: Outcome(SLICE_EMPTY, data=None, reason="empty", http_status=404)
                                    for key in empty}, count_empties=empty)
    return event_id


@pytest.fixture
def store(tmp_path: Path) -> Store:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    return open_store(data_dir)


def test_written_events(store: Store) -> None:
    pl, cup = sf.PL, sf.FA_CUP
    full = put(store, _payload(pl, sf.PL_2627, 9300001))
    no_lineups = put(store, _payload(pl, sf.PL_2627, 9300002), have=tuple(k for k in REQUIRED if k != "lineups"))
    # İki kesin "veri yok": kadro artık beklenmez, maç tamdır
    settled = put(store, _payload(pl, sf.PL_2526, 9300003), have=tuple(k for k in REQUIRED if k != "lineups"),
                  empty=("lineups",), empties=2)
    # Tek "veri yok": bitmiş maçta tamlık için çözülmüş sayılır (FX-23); planlayıcı onu doğrulamak için bir kez
    # daha ister
    once = put(store, _payload(pl, sf.PL_2526, 9300004), have=tuple(k for k in REQUIRED if k != "h2h"),
               empty=("h2h",), empties=1)
    bare = put(store, _payload(cup, sf.FA_2627, 9300005, OTHER_FINISHED), have=())
    orphan = put(store, _payload(sf.FRIENDLY, sf.NO_SEASON, 9300006))
    assert len({full, no_lineups, settled, once, bare, orphan}) == 6

    report = StatusService(store).coverage()
    assert planning.missing_slice_keys(_state(store, once)) == ("h2h",)
    assert (report.matches, report.complete) == (6, 4)
    assert dict(report.missing) == {key: (2 if key == "lineups" else 1) for key in REQUIRED}
    assert list(report.missing) == list(REQUIRED)  # kayıt defterinin sırası
    assert [t.tournament_id for t in report.tournaments] == [None, pl.id, cup.id]

    premier = report.tournament(pl.id)
    assert (premier.matches, premier.complete) == (4, 3)
    assert dict(premier.missing) == {"lineups": 1}
    assert premier.seasons == (
        SeasonCoverage(sf.PL_2627.id, 2, 1, {"lineups": 1}),
        SeasonCoverage(sf.PL_2526.id, 2, 2, {}),
    )
    assert premier.completion_rate == 75.0
    assert premier.seasons[0].completion_rate == 50.0

    cup_row = report.tournament(cup.id)
    assert (cup_row.matches, cup_row.complete, dict(cup_row.missing)) == (1, 0, {key: 1 for key in REQUIRED})
    assert report.tournament(None).matches == 1 and report.tournament(None).complete == 1
    assert report.tournament(12345) == TournamentCoverage(12345)
    assert report.completion_rate == 66.67


def test_listing_only_events_are_not_counted(store: Store) -> None:
    put(store, _payload(sf.PL, sf.PL_2627, 9300011))
    page = {"events": [sf.event_payload(sf.Ev(FINISHED, sf.PL, sf.PL_2627, "A", "B", eid=9300012)),
                       sf.event_payload(sf.Ev(NOT_STARTED, sf.PL, sf.PL_2627, "C", "D", eid=9300013))]}
    store.entities.put(Ref.season(sf.PL.id, sf.PL_2627.id), {("schedule", "round_1"): Outcome(SLICE_OK, data=page)})
    assert store.events.get(9300012) is not None and not store.events.get(9300012).has_event_payload

    report = StatusService(store).coverage()
    assert (report.matches, report.complete) == (1, 1)


def test_scope(store: Store) -> None:
    put(store, _payload(sf.PL, sf.PL_2627, 9300021))
    put(store, _payload(sf.PL, sf.PL_2526, 9300022), have=())
    put(store, _payload(sf.FA_CUP, sf.FA_2627, 9300023, OTHER_FINISHED))
    service = StatusService(store)

    only_pl = service.coverage(Scope(tournament_ids=(sf.PL.id,)))
    assert [t.tournament_id for t in only_pl.tournaments] == [sf.PL.id]
    assert (only_pl.matches, only_pl.complete) == (2, 1)

    one_season = service.coverage(Scope(season_ids=(sf.PL_2526.id,)))
    assert (one_season.matches, one_season.complete) == (1, 0)
    assert service.coverage(Scope(event_ids=(9300021,))).complete == 1
    assert service.coverage(Scope(sport="tennis")) == CoverageReport(data_dir=str(store.data_dir))


def test_threshold(store: Store) -> None:
    """Eşik, son yanıtı "veri yok" olmayan dilimde sayılır: bir "veri yok", sonra başarısız bir istek."""
    event_id = put(store, _payload(sf.PL, sf.PL_2627, 9300031), have=tuple(k for k in REQUIRED if k != "lineups"),
                   empty=("lineups",), empties=1)
    store.events.put(event_id, {"lineups": Outcome("failed", reason="timeout")})
    assert _state(store, event_id).slice("lineups").state != "empty"
    service = StatusService(store)
    assert service.coverage().complete == 0
    assert service.coverage(threshold=1).complete == 1


def test_one_no_data_answer_is_resolved_only_for_a_finished_match(store: Store) -> None:
    """FX-23: bitmemiş maçta tek "veri yok" çözülmüş sayılmaz (maç sürerken veri daha gelebilir)."""
    finished = put(store, _payload(sf.PL, sf.PL_2627, 9300041), have=tuple(k for k in REQUIRED if k != "lineups"),
                   empty=("lineups",), empties=1)
    state = _state(store, finished)
    assert planning.missing_slice_keys(state) == ("lineups",) and planning.unresolved_slice_keys(state) == ()
    void = dataclasses.replace(state, event=dataclasses.replace(state.event, status_class="void"))
    assert planning.unresolved_slice_keys(void) == planning.missing_slice_keys(void)



def test_a_no_data_answer_taken_while_the_match_was_on_does_not_resolve_it_after_the_end(store: Store) -> None:
    """
    FX-27 V5: maç oynanırken alınan "veri yok" sayılmadan kaydedilir (durum `empty`, sayaç 0). Maç bitince o
    kayıt tamlığa girmez: bitişten sonraki ilk istek onu sayana kadar dilim çözülmemiştir ve yeniden istenir.
    """
    event_id = put(store, _payload(sf.PL, sf.PL_2627, 9300051), have=tuple(k for k in REQUIRED if k != "lineups"))
    store.events.put(event_id, {"lineups": Outcome(SLICE_EMPTY, data=None, reason="empty", http_status=404)},
                     count_empties=False)
    state = _state(store, event_id)
    assert (state.slice("lineups").state, state.slice("lineups").empty_count) == ("empty", 0)
    assert planning.missing_slice_keys(state) == ("lineups",) == planning.unresolved_slice_keys(state)


def _state(store: Store, event_id: int) -> Any:
    (found,) = store.events.states(Scope(event_ids=(event_id,)))
    return found


def test_empty_data_directory(store: Store) -> None:
    report = StatusService(store).coverage()
    assert report == CoverageReport(data_dir=str(store.data_dir))
    assert report.completion_rate == 0.0 and report.tournaments == ()
