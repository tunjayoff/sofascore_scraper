"""
src/slices.py: sonuç tipi (Outcome) ve "bu yanıtta veri var mı" kuralları.

Beklenen değerler tabloya elle yazıldı ve yüklemler src/slices.py'ye taşınmadan ÖNCEKİ kodla
(MatchDataFetcher metotları, src/match_data_fetcher.py'deki SliceOutcome) doğrulandı. Aynı tablolar
IMPLEMENTATIONS'taki her uygulamaya uygulanır (src.slices'taki işlevler, tek tek ve dağıtıcı üzerinden).
MatchDataFetcher'ın vekil metotları P15'te kalktı; onların iki satırı tablodan çıktı.

Kurallar toplamdır ve üç yanıt verir (slice_body_state: veri var, veri yok, okunamadı). Altı eski kuralın
yanıtı, taşınan yüklemlerin bu dosyada saklanan kopyasıyla (`_REFERENCE`) üretilmiş gövdeler üzerinde
karşılaştırılır: True → veri var, False → veri yok, hata → okunamadı.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from src.exceptions import (
    APIError,
    CircuitOpenError,
    DataParsingError,
    NetworkError,
    RateLimitError,
    ResourceNotFoundError,
)
from src import match_data_fetcher, slices, sports
from src.match_data_fetcher import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SliceOutcome
from src.services import pipeline
from src.slices import BODY_DATA, BODY_MALFORMED, BODY_NO_DATA, SLICE_SKIPPED, Outcome

FIXTURES = Path(__file__).parent / "fixtures" / "status"

# src.slices'ta kendi işlevi olan dilimler: anahtar → işlev adı (ilk altısı eskiden MatchDataFetcher'ın
# metotlarıydı, adları baştaki alt çizgiyle)
TYPED_FUNCTIONS = {
    "statistics": "statistics_has_data",
    "lineups": "has_lineups_data_dict",
    "h2h": "has_h2h_data_dict",
    "pregame_form": "has_pregame_form_data_dict",
    "team_streaks": "has_team_streaks_data_dict",
    "incidents": "has_incidents_data_dict",
    "point_by_point": "has_point_by_point_data_dict",
    "esports_games": "has_esports_games_data_dict",
    "innings": "has_innings_data_dict",
}
# Kendi kuralı olan dilimler: kayıt defterindeki her dilim
RULE_KEYS = tuple(TYPED_FUNCTIONS)
# Kendi kuralı olmayan anahtarlar (kayıt defterinde olmayanlar): değer "truthy" ise veri var sayılır
GENERIC_KEYS = ("basic", "graph")
ALL_KEYS = RULE_KEYS + GENERIC_KEYS

Predicate = Callable[[str, Dict[str, Any]], bool]


def _answered(key: str, body: Any) -> SliceOutcome:
    """Yanıt gelen dilimin sonucu: boru hattının kuralı (eskiden MatchDataFetcher._answered_outcome vekili)."""
    return pipeline.answered_outcome(key, SliceOutcome(SLICE_OK, data=body))


def _function_direct(key: str, d: Dict[str, Any]) -> bool:
    name = TYPED_FUNCTIONS.get(key)
    return getattr(slices, name)(d) if name else slices.match_detail_slice_present(key, d)


IMPLEMENTATIONS: List[Tuple[str, Predicate]] = [
    ("function-dispatch", slices.match_detail_slice_present),
    ("function-direct", _function_direct),
]

# İçinde veri olan gerçekçi yanıtlar (tests/test_data_integrity.py'deki PRESENT ile aynı biçimler)
PRESENT: Dict[str, Any] = {
    "basic": {"id": 4242, "status": {"code": 100, "type": "finished"}},
    "statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "shots"}]}]}]},
    "team_streaks": {"general": [{"name": "Wins", "value": "3"}]},
    "pregame_form": {"homeTeam": {"form": ["W"]}, "awayTeam": {"form": ["L"]}},
    "h2h": {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}},
    "lineups": {"home": {"players": [{"player": {"id": 1}}]}, "away": {"players": []}},
    "incidents": {"incidents": [{"incidentType": "goal"}]},
    "point_by_point": {"pointByPoint": [{"games": []}]},
    # research/all_sports/samples/esports/event-id-esports-games__1.json biçiminde, kısaltılmış
    "esports_games": {"games": [{"id": 588243, "status": {"code": 100, "type": "finished"}, "winnerCode": 1}]},
    # tests/fixtures/sport_slices/cricket_innings__16526539.json biçiminde, kısaltılmış
    "innings": {"innings": [{"number": 1, "score": 285, "wickets": 4, "overs": 50}]},
    "graph": {"graphPoints": [{"minute": 1, "value": 3}]},
}

_STAT_ITEMS = [{"statisticsItems": [{"key": "shots"}]}]

# (anahtar, yanıt gövdesi, beklenen). Yüklem {anahtar: gövde} sözlüğüyle çağrılır.
BODY_CASES: List[Tuple[str, Any, bool]] = [
    # --- statistics: ALL periyodunda (yoksa ilk periyotta) en az bir dolu grup
    ("statistics", {}, False),
    ("statistics", [], False),
    ("statistics", {"statistics": []}, False),
    ("statistics", {"statistics": None}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": []}]}, False),
    ("statistics", {"statistics": [{"period": "ALL"}]}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": []}]}]}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": [{"groupName": "x"}]}]}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": _STAT_ITEMS}]}, True),
    ("statistics", [{"period": "ALL", "groups": _STAT_ITEMS}], True),  # sarmalayıcısız liste
    # ALL yoksa ilk periyot sayılır, sonrakiler sayılmaz
    ("statistics", [{"period": "1ST", "groups": _STAT_ITEMS}, {"period": "2ND"}], True),
    ("statistics", [{"period": "1ST"}, {"period": "2ND", "groups": _STAT_ITEMS}], False),
    # ALL varsa yalnızca ona bakılır
    ("statistics", [{"period": "1ST"}, {"period": "ALL", "groups": _STAT_ITEMS}], True),
    ("statistics", [{"period": "1ST", "groups": _STAT_ITEMS}, {"period": "ALL", "groups": []}], False),
    # --- lineups: iki taraftan birinde en az bir oyuncu
    ("lineups", {}, False),
    ("lineups", [], False),
    ("lineups", ["x"], False),
    ("lineups", {"confirmed": True}, False),
    ("lineups", {"home": {"players": []}, "away": {"players": []}}, False),
    ("lineups", {"home": {"players": "abc"}}, False),
    ("lineups", {"home": {"formation": "4-4-2"}, "away": None}, False),
    ("lineups", {"home": [1], "away": {"players": [1]}}, True),
    ("lineups", {"home": {"players": [{"player": {"id": 1}}]}}, True),
    # --- h2h: teamDuel sayılarından biri (0 dahil) ya da bir maç listesi
    ("h2h", {}, False),
    ("h2h", ["x"], False),
    ("h2h", {"teamDuel": None, "managerDuel": None}, False),
    ("h2h", {"teamDuel": {"homeWins": None, "awayWins": None, "draws": None}}, False),
    ("h2h", {"managerDuel": {"homeWins": 1}}, False),
    ("h2h", {"teamDuel": {"homeWins": 0}}, True),
    ("h2h", {"teamDuel": {"draws": 2}}, True),
    ("h2h", {"matches": [1]}, True),
    ("h2h", {"teamDuel": None, "events": [1]}, True),
    ("h2h", {"teamDuel": {"matches": [1]}}, True),
    ("h2h", {"matches": [], "events": []}, False),
    # --- pregame_form: bir takımda form listesi ya da position / value / avgRating
    ("pregame_form", {}, False),
    ("pregame_form", ["x"], False),
    ("pregame_form", {"label": "x"}, False),
    ("pregame_form", {"homeTeam": {"form": []}, "awayTeam": {"form": []}}, False),
    ("pregame_form", {"homeTeam": {"form": ["W"]}}, True),
    ("pregame_form", {"homeTeam": {"position": 0}}, True),
    ("pregame_form", {"awayTeam": {"value": "12"}}, True),
    ("pregame_form", {"homeTeam": "x", "awayTeam": {"avgRating": "6.9"}}, True),
    # --- team_streaks: yalnızca "general" listesi sayılır
    ("team_streaks", {}, False),
    ("team_streaks", {"general": [], "head2head": []}, False),
    ("team_streaks", {"head2head": [{"name": "Wins"}]}, False),
    ("team_streaks", {"general": {"name": "Wins"}}, False),
    ("team_streaks", {"general": [{"name": "Wins"}]}, True),
    # --- incidents: {"incidents": [...]} ya da doğrudan liste
    ("incidents", {}, False),
    ("incidents", [], False),
    ("incidents", {"incidents": []}, False),
    ("incidents", {"incidents": {"a": 1}}, False),
    ("incidents", "abc", False),
    ("incidents", {"incidents": [{"incidentType": "goal"}]}, True),
    ("incidents", [{"incidentType": "goal"}], True),
    # --- point_by_point: {"pointByPoint": [...]} listesi dolu olmalı
    ("point_by_point", {}, False),
    ("point_by_point", [], False),
    ("point_by_point", {"pointByPoint": []}, False),  # içi boş liste: veri yok (eskiden dolu sözlük = veri)
    ("point_by_point", {"pointByPoint": None}, False),
    ("point_by_point", {"error": {"code": 404, "message": "Not Found"}}, False),
    ("point_by_point", {"pointByPoint": [{"games": []}]}, True),
    ("point_by_point", {"pointByPoint": [{}]}, True),
    # --- esports_games: {"games": [...]} listesi dolu olmalı (SP-3)
    ("esports_games", {}, False),
    ("esports_games", [], False),
    ("esports_games", {"games": []}, False),
    ("esports_games", {"games": None}, False),
    ("esports_games", {"error": {"code": 404, "message": "Not Found"}}, False),
    ("esports_games", {"games": [{"id": 588244, "status": {"code": 20, "type": "inprogress"}}]}, True),
    # --- innings: {"innings": [...]} listesi dolu olmalı (kriket)
    ("innings", {}, False),
    ("innings", [], False),
    ("innings", {"innings": []}, False),
    ("innings", {"innings": None}, False),
    ("innings", {"error": {"code": 404, "message": "Not Found"}}, False),
    ("innings", {"innings": [{"number": 1, "score": 0, "wickets": 0, "overs": 0}]}, True),
    # --- kendi kuralı olmayan anahtarlar: bool(değer)
    ("basic", {}, False),
    ("basic", {"id": 1}, True),
    ("graph", 0, False),
    ("graph", [1], True),
    ("graph", {"graphPoints": []}, True),  # kuralı olmayan anahtarda dolu sözlük yeter
    ("graph", "abc", True),
]

# Beklenmeyen biçimde gövde: kural okuyamaz. Yüklem False verir (eskiden AttributeError, TypeError ya da
# KeyError ile düşerdi); slice_body_state "okunamadı" der.
MALFORMED_CASES: List[Tuple[str, Any]] = [
    ("statistics", "abc"),
    ("statistics", 0),  # yalnızca None "yok" sayılır; 0 okunamayan gövdedir
    ("statistics", True),
    ("statistics", ["x"]),
    ("statistics", [None, {"period": "1ST", "groups": _STAT_ITEMS}]),  # ALL yokken ilk öğe None
    ("statistics", [{"period": "ALL", "groups": _STAT_ITEMS}, "x"]),  # veri olsa da: periyotlar önce okunur
    ("statistics", {"statistics": [{"period": "ALL", "groups": ["x"]}]}),
    ("statistics", {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": []}, None]}]}),
    ("statistics", {"statistics": [{"period": "ALL", "groups": "abc"}]}),
    ("statistics", {"statistics": [{"period": "ALL", "groups": 5}]}),
    ("statistics", {"statistics": [{"period": "ALL", "groups": {"a": 1}}]}),
    ("statistics", {"statistics": "abc"}),
    ("statistics", {"statistics": 5}),
    ("statistics", {"statistics": {"period": "ALL"}}),
    ("statistics", {"statistics": {"": 1}}),
    ("h2h", {"teamDuel": ["x"]}),
    ("h2h", {"teamDuel": "abc"}),
    ("h2h", {"teamDuel": 5, "matches": [1]}),  # maç listesi olsa da: teamDuel önce okunur
    ("team_streaks", ["x"]),
    ("team_streaks", "abc"),
    ("team_streaks", 5),
    ("team_streaks", True),
    ("point_by_point", "abc"),
    ("point_by_point", 0),
    ("point_by_point", False),
    ("point_by_point", [{"games": []}]),  # sarmalayıcısız liste bilinen bir biçim değil
    ("point_by_point", {"pointByPoint": {"games": []}}),
    ("point_by_point", {"pointByPoint": "abc"}),
    ("point_by_point", {"pointByPoint": 0}),
    ("esports_games", "abc"),
    ("esports_games", 0),
    ("esports_games", [{"id": 588243}]),  # sarmalayıcısız liste bilinen bir biçim değil
    ("esports_games", {"games": {"id": 588243}}),
    ("esports_games", {"games": "abc"}),
    ("innings", "abc"),
    ("innings", 0),
    ("innings", [{"number": 1}]),  # sarmalayıcısız liste bilinen bir biçim değil
    ("innings", {"innings": {"number": 1}}),
    ("innings", {"innings": "abc"}),
]


def _impl_params(cases: List[tuple]) -> List[Any]:
    return [
        pytest.param(impl, *case, id=f"{name}:{case[0]}:{index}")
        for name, impl in IMPLEMENTATIONS
        for index, case in enumerate(cases)
    ]


@pytest.mark.parametrize("impl,key,body,expected", _impl_params(BODY_CASES))
def test_presence_of_hand_made_bodies(impl: Predicate, key: str, body: Any, expected: bool):
    assert impl(key, {key: body}) is expected


@pytest.mark.parametrize("impl,key,body", _impl_params(MALFORMED_CASES))
def test_malformed_body_is_absent_and_does_not_raise(impl: Predicate, key: str, body: Any):
    assert impl(key, {key: body}) is False


# --- üç yanıt: slice_body_state --------------------------------------------------------------


@pytest.mark.parametrize("key,body,expected", BODY_CASES, ids=[f"{c[0]}:{i}" for i, c in enumerate(BODY_CASES)])
def test_body_state_of_hand_made_bodies(key: str, body: Any, expected: bool):
    """Elle yazılmış tablodaki hiçbir gövde "okunamadı" değildir: True → veri var, False → veri yok."""
    assert slices.slice_body_state(key, body) == (BODY_DATA if expected else BODY_NO_DATA)


@pytest.mark.parametrize("key,body", MALFORMED_CASES, ids=[f"{c[0]}:{i}" for i, c in enumerate(MALFORMED_CASES)])
def test_body_state_of_malformed_bodies(key: str, body: Any):
    assert slices.slice_body_state(key, body) == BODY_MALFORMED


def test_body_state_values_and_null():
    assert (BODY_DATA, BODY_NO_DATA, BODY_MALFORMED) == ("data", "no_data", "malformed")
    for key in ALL_KEYS + ("unknown_slice",):
        assert slices.slice_body_state(key, None) == BODY_NO_DATA
        assert slices.slice_body_state(key, PRESENT.get(key, {"x": 1})) == BODY_DATA


def test_keys_without_a_rule_are_never_malformed():
    """Kayıt defterinde olmayan anahtarın kuralı yoktur: dolu değer veri, boş değer "veri yok"."""
    for key in GENERIC_KEYS + ("odds", ""):
        assert key not in slices.PRESENCE_RULE_KEYS
        for body in _ATOMS:
            assert slices.slice_body_state(key, body) == (BODY_DATA if body else BODY_NO_DATA)
            assert slices.match_detail_slice_present(key, {key: body}) is bool(body)


def test_every_registered_slice_has_a_rule_of_its_own():
    """
    Kayıt defterine (src/sports.py DETAIL_SLICES) dilim ekleyen, src/slices.py'ye kuralını da ekler: kuralı
    olmayan dilim dolu her gövdeyi (`{"pointByPoint": []}` gibi) veri sayardı.
    """
    registered = {detail.key for detail in sports.DETAIL_SLICES}
    assert registered - slices.PRESENCE_RULE_KEYS == set(), "src/slices.py'de kuralı olmayan kayıtlı dilim"
    assert slices.PRESENCE_RULE_KEYS == registered == set(RULE_KEYS)
    for spec in sports.SPORTS:
        assert set(spec.detail_slices) <= slices.PRESENCE_RULE_KEYS


# --- taşınan yüklemlerin kopyası (karşılaştırma için) ---------------------------------------
#
# Altı yüklemin kurallar toplam yapılmadan önceki hali, aynen. Yalnızca bu dosyada, yeni kuralların eski
# yanıtları koruduğunu göstermek için durur: True → veri var, False → veri yok, hata → okunamadı.


def _ref_statistics(d: Dict[str, Any]) -> bool:
    s = d.get("statistics")
    if s is None:
        return False
    periods = s if isinstance(s, list) else (s.get("statistics") or [])
    all_periods = [p for p in periods if p and p.get("period") == "ALL"]
    if not all_periods and periods:
        all_periods = [periods[0]]
    for p in all_periods:
        for g in p.get("groups") or []:
            if (g.get("statisticsItems") or []):
                return True
    return False


def _ref_lineups(d: Dict[str, Any]) -> bool:
    L = d.get("lineups")
    if not L or not isinstance(L, dict):
        return False
    for side in ("home", "away"):
        block = L.get(side)
        if not isinstance(block, dict):
            continue
        players = block.get("players")
        if isinstance(players, list) and len(players) > 0:
            return True
    return False


def _ref_h2h(d: Dict[str, Any]) -> bool:
    h = d.get("h2h")
    if not h or not isinstance(h, dict):
        return False
    td = h.get("teamDuel") or {}
    if td and any(td.get(x) is not None for x in ("homeWins", "awayWins", "draws")):
        return True
    raw = h.get("matches") or h.get("events") or td.get("matches")
    return isinstance(raw, list) and len(raw) > 0


def _ref_pregame_form(d: Dict[str, Any]) -> bool:
    p = d.get("pregame_form")
    if not p or not isinstance(p, dict):
        return False

    def chk(t: Any) -> bool:
        if not t or not isinstance(t, dict):
            return False
        form = t.get("form")
        if isinstance(form, list) and len(form) > 0:
            return True
        return any(t.get(x) is not None for x in ("position", "value", "avgRating"))

    return chk(p.get("homeTeam")) or chk(p.get("awayTeam"))


def _ref_team_streaks(d: Dict[str, Any]) -> bool:
    g = (d.get("team_streaks") or {}).get("general")
    return isinstance(g, list) and len(g) > 0


def _ref_incidents(d: Dict[str, Any]) -> bool:
    raw = d.get("incidents")
    if raw and isinstance(raw, dict) and not isinstance(raw, list):
        raw = raw.get("incidents")
    return isinstance(raw, list) and len(raw) > 0


_REFERENCE: Dict[str, Callable[[Dict[str, Any]], bool]] = {
    "statistics": _ref_statistics,
    "lineups": _ref_lineups,
    "h2h": _ref_h2h,
    "pregame_form": _ref_pregame_form,
    "team_streaks": _ref_team_streaks,
    "incidents": _ref_incidents,
}


def _reference_state(key: str, body: Any) -> str:
    try:
        return BODY_DATA if _REFERENCE[key]({key: body}) else BODY_NO_DATA
    except (AttributeError, TypeError, KeyError, IndexError, ValueError):  # eski okuyucunun yakaladıkları
        return BODY_MALFORMED


# JSON'un verebileceği her türden değer: boş ve dolu
_ATOMS: List[Any] = [
    None, True, False, 0, 1, 2.5, "", "abc", [], ["x"], [None], [[]], [{}], [1, 2], {}, {"a": 1}, {"": 1},
    {"period": "ALL"}, {"statisticsItems": [1]}, {"players": [1]}, {"form": ["W"]}, {"homeWins": 0},
]
_GROUPS = [{"statisticsItems": [{"key": "shots"}]}]

# Dilim → gövde kalıpları; her kalıp, kuralın okuduğu bir yere bir değer koyar
_TEMPLATES: Dict[str, List[Callable[[Any], Any]]] = {
    "statistics": [
        lambda a: a,
        lambda a: {"statistics": a},
        lambda a: [a],
        lambda a: {"statistics": [a]},
        lambda a: [a, {"period": "ALL", "groups": _GROUPS}],
        lambda a: [{"period": "ALL", "groups": _GROUPS}, a],
        lambda a: [a, {"period": "1ST", "groups": _GROUPS}],
        lambda a: [{"period": "1ST", "groups": _GROUPS}, a],
        lambda a: [{"period": a, "groups": _GROUPS}, {"period": "2ND"}],
        lambda a: [{"period": "ALL", "groups": a}],
        lambda a: [{"period": "1ST", "groups": a}],
        lambda a: [{"period": "ALL", "groups": [a]}],
        lambda a: [{"period": "ALL", "groups": [a, {"statisticsItems": [1]}]}],
        lambda a: [{"period": "ALL", "groups": [{"statisticsItems": [1]}, a]}],
        lambda a: [{"period": "ALL", "groups": [{"statisticsItems": a}]}],
        lambda a: [{"period": "ALL", "groups": []}, {"period": "ALL", "groups": a}],
        lambda a: [{"period": "ALL", "groups": a}, {"period": "ALL", "groups": _GROUPS}],
    ],
    "lineups": [
        lambda a: a,
        lambda a: {"home": a},
        lambda a: {"away": a},
        lambda a: {"home": {"players": a}},
        lambda a: {"home": a, "away": {"players": [1]}},
        lambda a: {"home": {"players": a}, "away": {"players": a}},
    ],
    "h2h": [
        lambda a: a,
        lambda a: {"teamDuel": a},
        lambda a: {"teamDuel": a, "matches": [1]},
        lambda a: {"teamDuel": {"homeWins": a}},
        lambda a: {"teamDuel": {"draws": a, "matches": [1]}},
        lambda a: {"teamDuel": {"matches": a}},
        lambda a: {"matches": a},
        lambda a: {"events": a},
        lambda a: {"matches": a, "events": [1]},
        lambda a: {"managerDuel": a},
    ],
    "pregame_form": [
        lambda a: a,
        lambda a: {"homeTeam": a},
        lambda a: {"awayTeam": a},
        lambda a: {"homeTeam": {"form": a}},
        lambda a: {"awayTeam": {"position": a}},
        lambda a: {"homeTeam": {"form": a, "value": a}},
        lambda a: {"homeTeam": a, "awayTeam": {"form": ["W"]}},
    ],
    "team_streaks": [
        lambda a: a,
        lambda a: {"general": a},
        lambda a: {"head2head": a},
        lambda a: {"general": a, "head2head": [1]},
    ],
    "incidents": [
        lambda a: a,
        lambda a: {"incidents": a},
        lambda a: [a],
        lambda a: {"incidents": [a]},
        lambda a: {"other": a},
    ],
}


def _generated_bodies(key: str) -> Iterator[Any]:
    for template in _TEMPLATES[key]:
        for atom in _ATOMS:
            yield template(atom)


@pytest.mark.parametrize("key", sorted(_REFERENCE))
def test_rules_answer_as_the_moved_predicates_did(key: str):
    """
    Altı eski kural, üretilmiş her gövdede taşınan yüklemle aynı yanıtı verir: eskiden True ise "veri var",
    False ise "veri yok", hata ise "okunamadı". Yüklemler artık hata fırlatmaz ve yalnızca "veri var"da True'dur.
    """
    seen = set()
    count = 0
    for body in _generated_bodies(key):
        count += 1
        expected = _reference_state(key, body)
        assert slices.slice_body_state(key, body) == expected, body
        for name, impl in IMPLEMENTATIONS:
            assert impl(key, {key: body}) is (expected == BODY_DATA), (name, body)
        seen.add(expected)
    assert count == len(_TEMPLATES[key]) * len(_ATOMS)
    # Eski yüklemi hiç hata fırlatmayan üç dilim hiçbir gövdeyi "okunamadı" saymaz
    never_malformed = key in ("lineups", "pregame_form", "incidents")
    assert seen == ({BODY_DATA, BODY_NO_DATA} if never_malformed else {BODY_DATA, BODY_NO_DATA, BODY_MALFORMED})


def test_reference_covers_the_hand_made_tables():
    """Kopya gerçekten eski koddur: elle yazılmış tabloların eski yüklemi olan satırlarıyla aynı yanıtı verir."""
    for key, body, expected in BODY_CASES:
        if key in _REFERENCE:
            assert _reference_state(key, body) == (BODY_DATA if expected else BODY_NO_DATA), (key, body)
    for key, body in MALFORMED_CASES:
        if key in _REFERENCE:
            assert _reference_state(key, body) == BODY_MALFORMED, (key, body)


@pytest.mark.parametrize("key", ALL_KEYS + ("unknown_slice",))
def test_rules_are_total(key: str):
    """Hangi değer verilirse verilsin hata yok; yüklem yalnızca "veri var" yanıtında True."""
    bodies = [body for templates in _TEMPLATES.values() for template in templates for body in map(template, _ATOMS)]
    bodies += [{"pointByPoint": atom} for atom in _ATOMS]
    bodies += [{"games": atom} for atom in _ATOMS]
    bodies += [("a", "tuple"), {1, 2}, b"bytes", object(), float("nan"), {1: 2}, {None: [1]}]  # JSON'da olmayanlar
    for body in bodies:
        state = slices.slice_body_state(key, body)
        assert state in (BODY_DATA, BODY_NO_DATA, BODY_MALFORMED)
        for name, impl in IMPLEMENTATIONS:
            assert impl(key, {key: body}) is (state == BODY_DATA), (name, body)


def test_fetcher_maps_the_three_answers_to_outcomes():
    """
    Çekici, yanıt gelen dilimde üç yanıtı sonuca çevirir: veri var → "ok"; veri yok → "empty" / "empty"
    (gövde sonuçta durur ve diske yazılır); okunamadı → "failed" / "parse" (gövde sonuca konmaz). Okunamayan
    gövde kesin bir "yok" yanıtı değildir: sayılmaz ve yeniden istenir (uçtan uca:
    tests/test_malformed_slice_body.py).
    """
    for key, body in MALFORMED_CASES:
        outcome = _answered(key, body)
        assert (outcome.status, outcome.reason, outcome.http_status, outcome.data) == (
            SLICE_FAILED, "parse", None, None,
        ), (key, body)
        assert outcome.failed
    for key, body, expected in BODY_CASES:
        outcome = _answered(key, body)
        if expected:
            assert (outcome.status, outcome.reason, outcome.data) == (SLICE_OK, None, body), (key, body)
        else:
            assert (outcome.status, outcome.reason, outcome.data) == (SLICE_EMPTY, "empty", body), (key, body)
    empty_points = _answered("point_by_point", {"pointByPoint": []})
    assert (empty_points.status, empty_points.reason) == (SLICE_EMPTY, "empty")  # eskiden "ok"
    assert empty_points.data == {"pointByPoint": []}  # gövde yine sonuçta durur ve diske yazılır
    assert _answered("point_by_point", PRESENT["point_by_point"]).status == SLICE_OK
    # Kuralı olmayan anahtar hiçbir zaman "okunamadı" değildir
    assert _answered("graph", "abc").status == SLICE_OK
    assert _answered("graph", 0).status == SLICE_EMPTY


@pytest.mark.parametrize("name,impl", IMPLEMENTATIONS, ids=[name for name, _ in IMPLEMENTATIONS])
@pytest.mark.parametrize("key", ALL_KEYS)
def test_missing_or_null_slice_is_absent(name: str, impl: Predicate, key: str):
    assert impl(key, {}) is False
    assert impl(key, {key: None}) is False
    # Yalnızca kendi anahtarına bakar: başka dilimlerin verisi sonucu değiştirmez
    others = {k: v for k, v in PRESENT.items() if k != key}
    assert impl(key, others) is False
    assert impl(key, dict(others, **{key: None})) is False


@pytest.mark.parametrize("name,impl", IMPLEMENTATIONS, ids=[name for name, _ in IMPLEMENTATIONS])
@pytest.mark.parametrize("key", ALL_KEYS)
def test_realistic_body_is_present(name: str, impl: Predicate, key: str):
    assert impl(key, {key: PRESENT[key]}) is True
    assert impl(key, PRESENT) is True


def _status_fixtures() -> List[Path]:
    return sorted(FIXTURES.glob("*/*.json"))


def test_status_fixtures_exist():
    assert len(_status_fixtures()) > 100


@pytest.mark.parametrize("path", _status_fixtures(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_status_fixture_bodies(path: Path):
    """
    Gerçek /event yanıtı her dilimin gövdesi olarak verilir: "basic" (ve yüklemi olmayan anahtarlar) için veri
    vardır; kendi yüklemi olan dilimler, o dilimin biçiminde olmayan dolu bir gövdeyi "yok" sayar.
    """
    event = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(event, dict) and event
    for name, impl in IMPLEMENTATIONS:
        for key in ALL_KEYS:
            expected = key in GENERIC_KEYS
            assert impl(key, {key: event}) is expected, (name, key)
            # Maçın kendisi yalnızca "basic"te durduğunda diğer dilimler yok
            assert impl(key, {"basic": event}) is (key == "basic"), (name, key)


# --- tipli sonuç ---------------------------------------------------------------------------

# (hata, durum, neden, HTTP kodu)
ERROR_CASES: List[Tuple[BaseException, str, str, Optional[int]]] = [
    (ResourceNotFoundError("x"), SLICE_EMPTY, "404", 404),
    (RateLimitError(status_code=429, url="/x"), SLICE_FAILED, "429", 429),
    (RateLimitError(status_code=503, url="/x"), SLICE_FAILED, "5xx", 503),
    (APIError("HTTP 403 Forbidden", status_code=403), SLICE_FAILED, "403", 403),
    (APIError("HTTP 500", status_code=500), SLICE_FAILED, "5xx", 500),
    # ResourceNotFoundError olmayan 404 kesin "yok" sayılmaz
    (APIError("HTTP 404", status_code=404), SLICE_FAILED, "404", 404),
    (APIError("durum kodu yok"), SLICE_FAILED, "other", None),
    (NetworkError("İstek başarısız: curl: (28) Operation timed out"), SLICE_FAILED, "timeout", None),
    (NetworkError("İstek başarısız: curl: (7) Failed to connect"), SLICE_FAILED, "network", None),
    (DataParsingError("JSON ayrıştırma hatası"), SLICE_FAILED, "parse", None),
    # Açık devre kesici bugün "başarısız" sonuçtur (neden: breaker)
    (CircuitOpenError("/x"), SLICE_FAILED, "breaker", None),
    (ValueError("boom"), SLICE_FAILED, "other", None),
    (RuntimeError("read timed out"), SLICE_FAILED, "timeout", None),
]


@pytest.mark.parametrize(
    "exc,status,reason,http_status", ERROR_CASES, ids=[f"{type(c[0]).__name__}-{c[2]}" for c in ERROR_CASES]
)
def test_outcome_from_error(exc: BaseException, status: str, reason: str, http_status: Optional[int]):
    outcome = SliceOutcome.from_error(exc)
    assert isinstance(outcome, SliceOutcome)
    assert (outcome.status, outcome.reason, outcome.http_status) == (status, reason, http_status)
    assert outcome.data is None
    assert outcome.failed is (status == SLICE_FAILED)


def test_outcome_status_values():
    assert (SLICE_OK, SLICE_EMPTY, SLICE_FAILED) == ("ok", "empty", "failed")


def test_outcome_defaults_and_positional_order():
    bare = SliceOutcome(SLICE_OK)
    assert (bare.status, bare.data, bare.reason, bare.http_status, bare.failed) == ("ok", None, None, None, False)
    full = SliceOutcome(SLICE_FAILED, {"a": 1}, "429", 429)
    assert (full.status, full.data, full.reason, full.http_status, full.failed) == ("failed", {"a": 1}, "429", 429, True)
    assert SliceOutcome(SLICE_EMPTY, reason="empty").failed is False


def test_outcome_is_frozen_and_compares_by_value():
    outcome = SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
    assert outcome == SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
    assert outcome != SliceOutcome(SLICE_EMPTY, reason="empty")
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.status = SLICE_OK  # type: ignore[misc]


# --- src/slices.py'ye taşıma ---------------------------------------------------------------

def test_moved_names_stay_importable_from_match_data_fetcher():
    assert slices.SliceOutcome is Outcome
    assert match_data_fetcher.SliceOutcome is Outcome
    for name in ("SLICE_OK", "SLICE_EMPTY", "SLICE_FAILED"):
        assert getattr(match_data_fetcher, name) is getattr(slices, name)
    for name in list(TYPED_FUNCTIONS.values()) + ["match_detail_slice_present"]:
        assert callable(getattr(slices, name))


def test_outcome_fields_and_new_defaults():
    names = [f.name for f in dataclasses.fields(Outcome)]
    # İlk dört alan eski SliceOutcome'ın sırasıdır (konumla kurulan sonuçlar bozulmasın)
    assert names == ["status", "data", "reason", "http_status", "fetched_at", "via", "meta"]
    bare = Outcome(SLICE_OK)
    assert (bare.fetched_at, bare.via, bare.meta) == (None, None, None)
    # Yeni alanlar verilmediğinde eşitlik eskisi gibidir
    assert Outcome(SLICE_EMPTY, reason="empty") == SliceOutcome(SLICE_EMPTY, None, "empty", None)


def test_outcome_carries_new_fields():
    at = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
    outcome = Outcome(SLICE_OK, data={"events": []}, fetched_at=at, via="bridge", meta={"complete": True})
    assert (outcome.fetched_at, outcome.via, outcome.meta) == (at, "bridge", {"complete": True})
    assert outcome != Outcome(SLICE_OK, data={"events": []})
    assert outcome == dataclasses.replace(
        Outcome(SLICE_OK, data={"events": []}, via="bridge"), fetched_at=at, meta={"complete": True}
    )


def test_skipped_is_a_status_of_its_own():
    assert SLICE_SKIPPED == "skipped"
    skipped = Outcome(SLICE_SKIPPED, reason="not_selected")
    assert skipped.failed is False
    assert skipped.status not in (SLICE_OK, SLICE_EMPTY, SLICE_FAILED)


def test_from_error_does_not_set_new_fields():
    for exc in (ResourceNotFoundError("x"), RateLimitError(status_code=429, url="/x")):
        outcome = Outcome.from_error(exc)
        assert (outcome.fetched_at, outcome.via, outcome.meta) == (None, None, None)


_PURITY_PROBE = """
import sys
import src.slices
print(",".join(sorted(m for m in sys.modules if m == "src" or m.startswith("src."))))
print(",".join(sorted(m for m in ("pandas", "tqdm", "rich", "dotenv", "curl_cffi") if m in sys.modules)))
"""


def test_slices_module_is_pure():
    """
    src.slices import edildiğinde çekici, istek katmanı, günlükçü ve ağır kitaplıklar yüklenmez: depo ve
    servisler yüklemleri bunlara bağlanmadan çağırabilir. Temiz bir yorumlayıcıda bakılır (bu süreçte
    diğer testler hepsini zaten yüklemiş olur).
    """
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", _PURITY_PROBE], cwd=root, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].split(",") == ["src", "src.exceptions", "src.slices"]
    assert lines[1:] in ([], [""])
