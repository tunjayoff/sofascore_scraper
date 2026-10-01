"""Spor kayıt defteri (src/sports.py): arama, ad normalizasyonu, bilinmeyen spor, dilim tablosu."""
import re
from pathlib import Path

import pytest

from src import sports
from src.sports import WatcherParams

REPO = Path(__file__).resolve().parent.parent
REGISTERED = ("football", "basketball", "tennis")
COMMON_KEYS = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")

# SofaScore'un spor menüsündeki 26 slug (docs/all-sports/README.md) ve arama sonuçlarında gelen adlar
SOFASCORE_SLUGS = (
    "football", "basketball", "tennis", "american-football", "aussie-rules", "ice-hockey", "handball", "rugby",
    "futsal", "minifootball", "floorball", "volleyball", "badminton", "table-tennis", "padel", "snooker", "darts",
    "baseball", "cricket", "esports", "mma", "motorsport", "cycling", "bandy", "waterpolo", "beach-volley",
)
SOFASCORE_NAMES = (
    "Football", "Basketball", "Tennis", "American football", "Aussie rules", "Ice hockey", "Handball", "Rugby",
    "Futsal", "Minifootball", "Floorball", "Volleyball", "Badminton", "Table tennis", "Padel", "Snooker", "Darts",
    "Baseball", "Cricket", "Esports", "MMA", "Motorsport", "Cycling", "Bandy", "Waterpolo", "Beach volley",
)


def _normalize_before_registry(raw):
    """src/web/league_sports.normalize_sport'un kayıt defterinden önceki hali (karşılaştırma için kopya)."""
    s = str(raw or "").strip().lower()
    if "basket" in s:
        return "basketball"
    if "tennis" in s:
        return "tennis"
    if "football" in s or "soccer" in s:
        return "football"
    return None


# --- arama ------------------------------------------------------------------------------

def test_only_the_three_existing_sports_are_registered_in_order():
    assert sports.sport_slugs() == REGISTERED
    assert tuple(s.slug for s in sports.SPORTS) == REGISTERED


@pytest.mark.parametrize("slug", REGISTERED)
def test_get_sport_by_slug(slug):
    spec = sports.get_sport(slug)
    assert spec is not None and spec.slug == slug
    assert spec.i18n_key == f"sport.{slug}"
    assert spec.name.lower() == slug


@pytest.mark.parametrize("raw", ["Football", "FOOTBALL", " football", "soccer", "handball", "", None, 7, ["football"]])
def test_get_sport_is_exact_and_unknown_is_none(raw):
    assert sports.get_sport(raw) is None


def test_score_families():
    assert [sports.score_family(s) for s in REGISTERED] == ["football", "periods", "sets"]
    assert sports.score_family("handball") is None
    assert sports.score_family(None) is None


# --- ad normalizasyonu ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Football", "football"), ("football", "football"), ("FOOTBALL", "football"), ("  Football ", "football"),
    ("Soccer", "football"),
    ("Basketball", "basketball"), ("basketball", "basketball"),
    ("Tennis", "tennis"), ("tennis", "tennis"),
])
def test_normalize_names_sofascore_uses(raw, expected):
    assert sports.normalize_sport(raw) == expected


@pytest.mark.parametrize("raw", ["ice-hockey", "Ice hockey", "Handball", "volleyball", "esports", "", "   ", None, 0, 17, {}])
def test_normalize_unknown_is_none(raw):
    assert sports.normalize_sport(raw) is None


@pytest.mark.parametrize("raw", [
    *SOFASCORE_SLUGS, *SOFASCORE_NAMES, *(n.upper() for n in SOFASCORE_NAMES),
    "Soccer", "soccer (football)", "Basket", "basket-tennis", "tennis football", "foot ball", " Tennis\n", "x", "", None, 0,
])
def test_normalize_matches_the_pre_registry_function(raw):
    assert sports.normalize_sport(raw) == _normalize_before_registry(raw)


def test_unregistered_lookalikes_keep_the_legacy_mapping():
    """Kayıt defterinde olmayan benzer adlar eskisi gibi çözülür (bilinen sınır; spor eklenince düzelir)."""
    assert sports.normalize_sport("table-tennis") == "tennis"
    assert sports.normalize_sport("American football") == "football"
    assert sports.normalize_sport("minifootball") == "football"


def test_exact_name_wins_over_legacy_substring_once_a_sport_is_registered(monkeypatch):
    monkeypatch.setitem(sports._BY_EXACT_NAME, "table-tennis", "table-tennis")
    assert sports.normalize_sport("table-tennis") == "table-tennis"
    assert sports.normalize_sport("Table tennis") == "table-tennis"
    assert sports.normalize_sport("Tennis") == "tennis"


@pytest.mark.parametrize("event,expected", [
    ({"tournament": {"category": {"sport": {"slug": "tennis", "name": "Tennis"}}}}, "tennis"),
    ({"tournament": {"category": {"sport": {"name": "Basketball"}}}}, "basketball"),
    ({"tournament": {"category": {"sport": {"slug": "Ice-Hockey"}}}}, "ice-hockey"),
    ({"tournament": {"category": {"sport": {}}}}, None),
    ({"tournament": {"category": None}}, None),
    ({"tournament": "x"}, None),
    ({}, None),
    (None, None),
])
def test_event_sport_slug(event, expected):
    assert sports.event_sport_slug(event) == expected


# --- izleyici parametreleri -------------------------------------------------------------

def test_watcher_params_per_sport():
    assert sports.watcher_params("football") == WatcherParams("football_minute", 4 * 3600, False)
    assert sports.watcher_params("basketball") == WatcherParams("played_ratio", 4 * 3600, False)
    assert sports.watcher_params("tennis") == WatcherParams("last_set", 6 * 3600, True)


@pytest.mark.parametrize("sport", ["handball", "Tennis", "", None])
def test_watcher_params_for_unregistered_sport_are_the_defaults(sport):
    assert sports.watcher_params(sport) == WatcherParams("never", 4 * 3600, False)


# --- dilim tablosu ----------------------------------------------------------------------

def test_detail_slice_table():
    assert [(s.key, s.path) for s in sports.DETAIL_SLICES] == [
        ("statistics", "/event/{event_id}/statistics"),
        ("team_streaks", "/event/{event_id}/team-streaks"),
        ("pregame_form", "/event/{event_id}/pregame-form"),
        ("h2h", "/event/{event_id}/h2h"),
        ("lineups", "/event/{event_id}/lineups"),
        ("incidents", "/event/{event_id}/incidents"),
        ("point_by_point", "/event/{event_id}/point-by-point"),
    ]
    assert all(s.default_enabled for s in sports.DETAIL_SLICES)
    assert [s.key for s in sports.DETAIL_SLICES if not s.required] == ["point_by_point"]
    assert sports.get_slice("point_by_point").sports == frozenset({"tennis"})
    assert all(s.sports is None for s in sports.DETAIL_SLICES if s.key != "point_by_point")


def test_slice_table_is_consistent():
    keys = [s.key for s in sports.DETAIL_SLICES]
    assert len(set(keys)) == len(keys)
    for s in sports.DETAIL_SLICES:
        assert s.sports is None or s.sports <= set(sports.sport_slugs())
        assert s.path.startswith("/event/{event_id}/")
        assert sports.get_slice(s.key) is s
    assert sports.get_slice("nope") is None


def test_slice_url():
    s = sports.get_slice("team_streaks")
    assert s.url("https://www.sofascore.com/api/v1", 42) == "https://www.sofascore.com/api/v1/event/42/team-streaks"


@pytest.mark.parametrize("sport,expected", [
    ("football", COMMON_KEYS),
    ("basketball", COMMON_KEYS),
    ("tennis", COMMON_KEYS + ("point_by_point",)),
    ("handball", COMMON_KEYS),
    ("", COMMON_KEYS),
    (None, COMMON_KEYS),
])
def test_slices_for_sport(sport, expected):
    assert tuple(s.key for s in sports.slices_for(sport)) == expected
    assert tuple(s.key for s in sports.slices_for(sport, required_only=True)) == COMMON_KEYS


def test_sport_spec_lists_its_slices():
    assert sports.get_sport("football").detail_slices == COMMON_KEYS
    assert sports.get_sport("tennis").detail_slices == COMMON_KEYS + ("point_by_point",)


# --- web arayüzü listesi kayıt defteriyle aynı kalmalı (henüz /api/sports'tan okumuyor) ------

def test_frontend_sport_list_matches_the_registry():
    src = (REPO / "frontend" / "src" / "lib" / "sport.ts").read_text(encoding="utf-8")
    listed = re.search(r"export const SPORTS: SportKey\[\] = \[(.*?)\]", src, re.S)
    union = re.search(r"export type SportKey = (.*)", src)
    assert listed and union
    assert tuple(re.findall(r"'([^']+)'", listed.group(1))) == sports.sport_slugs()
    assert tuple(re.findall(r"'([^']+)'", union.group(1))) == sports.sport_slugs()


@pytest.mark.parametrize("locale", ["tr", "en"])
def test_frontend_locales_have_every_sport_label(locale):
    src = (REPO / "frontend" / "src" / "locales" / f"{locale}.ts").read_text(encoding="utf-8")
    block = re.search(r"^  sport: \{\n(.*?)^  \},", src, re.S | re.M)
    assert block
    keys = set(re.findall(r"^\s+'?([\w-]+)'?:", block.group(1), re.M))
    for spec in sports.SPORTS:
        section, _, key = spec.i18n_key.partition(".")
        assert section == "sport" and key in keys
