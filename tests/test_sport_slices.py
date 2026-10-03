"""
Her spor SofaScore'un o sporda sunduğu detay dilimlerini ister (src/sports.py, DETAIL_SLICES).

Kanıt: sitenin maç sayfalarının istekleri ve aldığı yanıtlar (research/all_sports, PR #17), spor ve uç nokta
başına tests/fixtures/sport_slices/evidence.json'da (tests/sport_evidence.py türetir). Kurallar
(tests/sport_evidence.py `verdict`, src/sports.py DETAIL_SLICES'ın üstündeki not):

  required   bitmiş maçta veriyle yanıtlandı, bitmiş maçta 404 almadı     → istenir, tamlık hesabına girer
  optional   başlamış maçta 404 aldı, bitmiş maçta hep veriyle gelmedi    → istenir, tamlık hesabına girmez
  open_data  yalnızca bitmemiş maçta veriyle yanıtlandı                   → ortak dilim değişmez; spora özel
                                                                            dilim istenir, tamlığa girmez
  absent     sayfa açılınca istenen dilim, eksiksiz yüklenmiş canlı ya da → istenmez
             bitmiş sayfada hiç istenmedi
  unknown    kanıt yok ya da yetersiz                                     → ortak dilim değişmez; spora özel
                                                                            dilim istenmez

Futbol, basketbol ve tenis değişmez (goldenları bugünkü davranışı sabitler): kanıtın onlar için söylediği
PROPOSALS'ta durur ve kanıt değişirse test bunu görür. Ağ yok.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

import sport_evidence as evidence_mod
from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sport_evidence import ABSENT, OPEN_DATA, OPTIONAL, REQUIRED, UNKNOWN
from src import slices, sports
from src.services import planning
from src.services.pipeline import FetchPipeline
from src.services.query import RefreshPolicy, required_detail_keys
from src.slices import BODY_DATA, BODY_NO_DATA, SLICE_OK, Outcome
from src.store import Ref, open_store

FIXTURES = Path(__file__).parent / "fixtures" / "sport_slices"
EVIDENCE = evidence_mod.load()
COMMON_KEYS = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")

# Kanıtın futbol, basketbol ve tenis için önerdiği ama bu değişikliğin uygulamadığı yerler: (spor, dilim) →
# kanıtın yargısı. Bu sporların davranışı goldenlarla sabit; değişiklik ayrı bir karar ister.
PROPOSALS: Dict[Tuple[str, str], str] = {
    ("football", "pregame_form"): OPTIONAL,  # bitmiş iki maçta ve canlı maçta 404
    ("basketball", "pregame_form"): OPTIONAL,  # bitmiş maçta 404
    ("tennis", "pregame_form"): OPTIONAL,  # bitmiş ve canlı maçta 404
    ("tennis", "lineups"): ABSENT,  # iki eksiksiz sayfa (bitmiş, canlı) istemedi
    ("tennis", "incidents"): ABSENT,
    ("tennis", "point_by_point"): REQUIRED,  # bitmiş ve canlı maçta veriyle; bugün isteğe bağlı
}

# Kanıttaki, kayıt defterinde dilimi olmayan maç uç noktaları ve neden (tablonun tamamı hesaba katılsın diye)
OTHER_ENDPOINTS: Dict[str, str] = {
    "/": "the event itself (/event/{id}), not a slice",
    "/votes": "fan votes, not match data",
    "/at-bats": "baseball only (200 on one not-started match, 404 in every other sport): proposal",
    "/umpires": "baseball only, 404 on a not-started match: no data seen",
    "/weather": "baseball only, 404 on a not-started match: no data seen",
    "/comments": "baseball only, 404 on a not-started match: no data seen",
    "/managers": "every team sport (football, basketball, baseball, handball): proposal, not sport-specific",
    "/best-players": "basketball player ratings: proposal, not sport-specific",
    "/best-players/summary": "football player ratings: proposal, not sport-specific",
    "/player-of-the-match": "football fan poll: proposal",
    "/average-positions": "football player positions: proposal",
    "/heatmap/{id}": "football player heatmap (one request per player): proposal",
    "/shotmap": "football shot map: proposal",
    "/graph": "football and basketball momentum graph: proposal",
    "/graph/win-probability": "404 in every sport",
    "/live-match-tracker": "live widget",
    "/highlights": "media (video links)",
    "/official-tweets": "media",
    "/media/summary/country/{cc}": "media, per country",
    "/sport-video-highlights/country/{cc}/extended": "media, per country",
    "/ai-insights/{lang}": "generated text, per language",
    "/ai-insights-postmatch/{lang}": "generated text, per language",
}


def _endpoint(spec: sports.SliceSpec) -> str:
    return spec.path.replace("/event/{event_id}", "")


def _cells() -> List[Tuple[str, sports.SliceSpec, str]]:
    return [(sport, spec, evidence_mod.verdict(EVIDENCE, sport, _endpoint(spec)))
            for sport in sports.sport_slugs() for spec in sports.DETAIL_SLICES]


# --- kanıt tablosu -----------------------------------------------------------------------------------------


@pytest.mark.skipif(not (evidence_mod.RESEARCH / "requests.jsonl").exists(), reason="research data not present")
def test_the_evidence_table_is_derived_from_the_research_data():
    assert json.loads(json.dumps(evidence_mod.derive())) == EVIDENCE


def test_every_endpoint_in_the_evidence_is_a_slice_or_explained():
    paths = {_endpoint(spec) for spec in sports.DETAIL_SLICES}
    seen = {endpoint for by_endpoint in EVIDENCE["answers"].values() for endpoint in by_endpoint}
    assert seen - paths - set(OTHER_ENDPOINTS) == set()
    assert set(OTHER_ENDPOINTS) <= seen  # açıklamalar kanıtta geçen uç noktalar


def test_the_evidence_covers_the_recorded_pages():
    pages = EVIDENCE["pages"]
    # Eksiksiz yüklenmiş canlı ya da bitmiş maç sayfası: yokluk yalnızca bunlarda kanıttır
    complete = sorted(sport for sport, rows in pages.items()
                      if any(row["complete"] and row["state"] in ("finished", "live") for row in rows))
    assert complete == ["basketball", "cricket", "darts", "esports", "football", "futsal", "ice-hockey", "mma",
                        "padel", "snooker", "tennis"]
    # Sayfası 403 aldı, açılmadı ya da yarım yüklendi: kanıt yok
    for sport in ("american-football", "aussie-rules", "badminton"):
        assert sport not in pages
    for sport in ("minifootball", "rugby", "table-tennis"):
        assert [row["complete"] for row in pages[sport]] == [False]


# --- kayıt defteri kanıta uyar -------------------------------------------------------------------------------


@pytest.mark.parametrize("sport,spec,found", _cells(), ids=lambda v: getattr(v, "key", v))
def test_the_registry_follows_the_evidence(sport: str, spec: sports.SliceSpec, found: str):
    applies, counts = spec.applies_to(sport), spec.counts_in(sport)
    if (sport, spec.key) in PROPOSALS:
        assert found == PROPOSALS[(sport, spec.key)]
        # bugünkü davranış: ortak dilim istenir ve tamlığa girer; tenisin point_by_point'i isteğe bağlı
        assert applies and counts is (spec.key != "point_by_point")
        return
    if found == REQUIRED:
        assert applies and counts
    elif found == OPTIONAL:
        assert applies and not counts
    elif found == ABSENT:
        assert not applies
    elif spec.sports is None:  # ortak dilim, kanıt yetersiz ya da yalnızca bitmemiş maçta veri: değişmez
        assert applies and counts is spec.required
    elif found == OPEN_DATA:
        assert applies and not counts
    else:
        assert found == UNKNOWN and not applies


def test_no_sport_requests_a_slice_its_match_page_never_asks_for():
    for sport, spec, found in _cells():
        if found == ABSENT and (sport, spec.key) not in PROPOSALS:
            assert spec.key not in {s.key for s in sports.slices_for(sport)}, (sport, spec.key)
            assert spec.key not in planning.expected_slice_keys(sport, phase="post"), (sport, spec.key)


@pytest.mark.parametrize("sport,expected,counted", [
    ("football", COMMON_KEYS, COMMON_KEYS),
    ("basketball", COMMON_KEYS, COMMON_KEYS),
    ("tennis", COMMON_KEYS + ("point_by_point",), COMMON_KEYS),
    ("ice-hockey", COMMON_KEYS, ("statistics", "team_streaks", "h2h", "lineups", "incidents")),
    ("futsal", COMMON_KEYS, ("team_streaks", "h2h", "incidents")),
    ("minifootball", COMMON_KEYS, ("statistics", "team_streaks", "pregame_form", "h2h", "incidents")),
    ("padel", ("statistics", "team_streaks", "pregame_form", "h2h"), ("team_streaks", "h2h")),
    ("snooker", ("statistics", "team_streaks", "pregame_form", "h2h"), ("team_streaks", "h2h")),
    ("cricket", COMMON_KEYS + ("innings",), ("team_streaks", "h2h", "lineups", "incidents", "innings")),
    ("esports", ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "esports_games"),
     ("team_streaks", "h2h", "lineups")),
    ("darts", ("statistics", "team_streaks", "pregame_form", "h2h", "point_by_point"),
     ("statistics", "team_streaks", "h2h", "point_by_point")),
    ("mma", ("statistics", "team_streaks", "pregame_form", "h2h"), ("statistics", "team_streaks", "pregame_form", "h2h")),
    # kanıt yok ya da yetersiz: bugünkü altı dilim
    ("american-football", COMMON_KEYS, COMMON_KEYS),
    ("aussie-rules", COMMON_KEYS, COMMON_KEYS),
    ("handball", COMMON_KEYS, COMMON_KEYS),
    ("rugby", COMMON_KEYS, COMMON_KEYS),
    ("floorball", COMMON_KEYS, COMMON_KEYS),
    ("volleyball", COMMON_KEYS, COMMON_KEYS),
    ("badminton", COMMON_KEYS, COMMON_KEYS),
    ("table-tennis", COMMON_KEYS, COMMON_KEYS),
    ("baseball", COMMON_KEYS, COMMON_KEYS),
    # kayıtlı olmayan ya da bilinmeyen spor: bugünkü altı dilim
    ("waterpolo", COMMON_KEYS, COMMON_KEYS),
    ("", COMMON_KEYS, COMMON_KEYS),
    (None, COMMON_KEYS, COMMON_KEYS),
])
def test_slices_and_completeness_per_sport(sport, expected, counted):
    assert tuple(s.key for s in sports.slices_for(sport)) == expected
    assert tuple(s.key for s in sports.slices_for(sport, required_only=True)) == counted
    assert planning.expected_slice_keys(sport, phase="post") == counted


def test_innings_exist_once_the_match_started():
    assert "innings" not in planning.expected_slice_keys("cricket", phase="pre")
    assert "innings" not in {s.key for s in sports.select_slices("event", "cricket", phase="pre")}
    assert "innings" in planning.expected_slice_keys("cricket", phase="live")


def test_slice_spec_checks_the_per_sport_sets():
    with pytest.raises(ValueError):
        sports.SliceSpec("x", "/event/{event_id}/x", not_in=frozenset({"padel"}), optional_in=frozenset({"padel"}))
    with pytest.raises(ValueError):
        sports.SliceSpec("x", "/event/{event_id}/x", required=False, optional_in=frozenset({"padel"}))
    with pytest.raises(ValueError):
        sports.SliceSpec("x", "/event/{event_id}/x", sports=frozenset({"tennis"}), not_in=frozenset({"padel"}))
    spec = sports.SliceSpec("x", "/event/{event_id}/x", not_in=frozenset({"padel"}),
                            optional_in=frozenset({"futsal"}))
    assert (spec.applies_to("padel"), spec.counts_in("padel")) == (False, False)
    assert (spec.applies_to("futsal"), spec.counts_in("futsal")) == (True, False)
    assert (spec.applies_to(None), spec.counts_in(None)) == (True, True)


# --- yeni dilimlerin "veri var mı" kuralı, kayıtlı yanıtlarla --------------------------------------------------


def _recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["body"]


def test_cricket_innings_recorded_answer_has_data():
    body = _recorded("cricket_innings__16526539.json")
    assert slices.slice_body_state("innings", body) == BODY_DATA
    assert slices.has_innings_data_dict({"innings": body})
    assert slices.slice_body_state("innings", {"error": {"code": 404, "message": "Not Found"}}) == BODY_NO_DATA


def test_darts_point_by_point_recorded_answer_has_data():
    body = _recorded("darts_point_by_point__17099318.json")
    assert [leg_set["set"] for leg_set in body["pointByPoint"]] and "legs" in body["pointByPoint"][0]
    assert slices.slice_body_state("point_by_point", body) == BODY_DATA
    assert slices.slice_body_state("point_by_point", {"pointByPoint": []}) == BODY_NO_DATA


# --- boru hattı her sporda tam olarak kayıtlı dilimleri ister ------------------------------------------------


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


def _finished_event(event_id: int, sport: str) -> Dict[str, Any]:
    return {
        "id": event_id,
        "tournament": {"name": "Cup", "uniqueTournament": {"id": 77, "name": "Cup"},
                       "category": {"id": 1, "name": "World", "sport": {"name": sport, "slug": sport}}},
        "season": {"id": 5, "name": "Cup 26", "year": "26"},
        "homeTeam": {"id": 1, "name": "Home"}, "awayTeam": {"id": 2, "name": "Away"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "startTimestamp": 1790000000,
    }


# Veri taşıyan en küçük gövdeler (tests/test_slices.py PRESENT ile aynı biçim)
_BODIES: Dict[str, Any] = {
    "statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "shots"}]}]}]},
    "team_streaks": {"general": [{"name": "Wins", "value": "3"}]},
    "pregame_form": {"homeTeam": {"form": ["W"]}, "awayTeam": {"form": ["L"]}},
    "h2h": {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}},
    "lineups": {"home": {"players": [{"player": {"id": 1}}]}, "away": {"players": []}},
    "incidents": {"incidents": [{"incidentType": "goal"}]},
    "point_by_point": {"pointByPoint": [{"games": []}]},
    "esports_games": {"games": [{"id": 1, "status": {"code": 100, "type": "finished"}, "winnerCode": 1}]},
    "innings": {"innings": [{"number": 1, "score": 120, "wickets": 3, "overs": 20}]},
}


def _run(store: Any, event_ids: List[int]) -> Any:
    items = [planning.WorkItem(Ref.event(event_id), "full", (), None, "test") for event_id in event_ids]
    return FetchPipeline(store, concurrency=5, only_finished=True).run_sync(items)


@pytest.mark.parametrize("sport", sports.sport_slugs())
def test_the_pipeline_requests_exactly_the_registered_slices(sport: str, fake: FakeSofaScore, tmp_path: Path):
    event_id = 9700000 + sports.sport_slugs().index(sport)
    fake.add(f"/event/{event_id}", {"event": _finished_event(event_id, sport)})
    # Her kayıtlı uç nokta veriyle yanıtlar: istenmeyen bir dilim isteği yine de kayda düşer
    for spec in sports.DETAIL_SLICES:
        fake.add(spec.path.format(event_id=event_id), _BODIES[spec.key])
    store = open_store(tmp_path / "data")

    [result] = _run(store, [event_id]).results

    assert result.ok
    requested = sorted(r.path for r in fake.requests if r.path not in (SITE_ROOT, f"/event/{event_id}"))
    assert requested == sorted(spec.path.format(event_id=event_id) for spec in sports.slices_for(sport))
    assert list(result.slices) == [spec.key for spec in sports.slices_for(sport)]


@pytest.mark.parametrize("sport", sports.sport_slugs())
def test_a_finished_match_without_its_optional_slices_is_complete_after_one_fetch(sport: str, fake: FakeSofaScore,
                                                                                 tmp_path: Path):
    """Tamlığa girmeyen dilimler 404 alınca maç yeniden doldurulmaz: bitmiş maç başına istek 1 + dilim sayısı."""
    event_id = 9800000 + sports.sport_slugs().index(sport)
    fake.add(f"/event/{event_id}", {"event": _finished_event(event_id, sport)})
    for spec in sports.slices_for(sport, required_only=True):
        fake.add(spec.path.format(event_id=event_id), _BODIES[spec.key])
    store = open_store(tmp_path / "data")

    _run(store, [event_id])

    sent = [r for r in fake.requests if r.path != SITE_ROOT]
    assert len(sent) == 1 + len(sports.slices_for(sport))
    policy = RefreshPolicy(now=1790000000 + 86400 * 30, window_s=0, min_interval_s=0)
    assert planning.plan_items(store, [event_id], policy) == []


# --- Store: her spor kendi beklediği dilimlere bakılarak eksik sayılır ---------------------------------------


def _put(store: Any, event_id: int, sport: str, keys: Tuple[str, ...]) -> None:
    outcomes = {"event": Outcome(SLICE_OK, _finished_event(event_id, sport))}
    outcomes.update({key: Outcome(SLICE_OK, _BODIES[key]) for key in keys})
    store.events.put(event_id, outcomes)


def test_missing_uses_each_sports_own_required_slices(tmp_path: Path):
    store = open_store(tmp_path / "data")
    _put(store, 1, "padel", ("team_streaks", "h2h"))  # padel: kadro ve olaylar yok, öteki ikisi isteğe bağlı
    _put(store, 2, "football", ("statistics", "team_streaks", "pregame_form", "h2h", "incidents"))
    _put(store, 3, "cricket", ("team_streaks", "h2h", "lineups", "incidents"))  # innings eksik
    _put(store, 4, "waterpolo", ("team_streaks", "h2h"))  # kayıtlı değil: ortak altı dilim

    rows = {row.event_id: row.missing_keys
            for row in store.events.missing(None, required_detail_keys(), exclusive=True)}

    assert rows == {2: ("lineups",), 3: ("innings",), 4: ("statistics", "pregame_form", "lineups", "incidents")}
    # exclusive olmadan "" her spora eklenir (eski anlam): padel de ortak dilimleri bekler
    union = {row.event_id for row in store.events.missing(None, {"": COMMON_KEYS})}
    assert union == {1, 2, 3, 4}


def test_required_detail_keys_lists_every_registered_sport():
    required = required_detail_keys()
    assert required[""] == COMMON_KEYS
    assert set(required) == {""} | set(sports.sport_slugs())
    for sport in sports.sport_slugs():
        assert required[sport] == tuple(s.key for s in sports.slices_for(sport, required_only=True))
