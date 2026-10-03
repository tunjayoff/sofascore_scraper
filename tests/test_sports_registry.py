"""Spor kayıt defteri (src/sports.py): arama, ad normalizasyonu, bilinmeyen spor, dilim tablosu."""
import re
from pathlib import Path

import pytest

from src import sports
from src.sports import WatcherParams

REPO = Path(__file__).resolve().parent.parent
ORIGINAL = ("football", "basketball", "tennis")
# A sınıfı, periyot tabanlı sekiz spor (plan maddesi SP-1)
PERIOD_SPORTS = ("american-football", "aussie-rules", "ice-hockey", "handball", "rugby", "futsal", "minifootball",
                 "floorball")
# A sınıfı, set tabanlı beş spor (plan maddesi SP-2)
SET_SPORTS = ("volleyball", "badminton", "table-tennis", "padel", "snooker")
# B sınıfı: kendi durum ya da skor mantığı olan beş spor (plan maddesi SP-3)
CLASS_B_SPORTS = ("baseball", "cricket", "esports", "darts", "mma")
REGISTERED = ORIGINAL + PERIOD_SPORTS + SET_SPORTS + CLASS_B_SPORTS
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

def test_registered_sports_in_order():
    assert sports.sport_slugs() == REGISTERED
    assert tuple(s.slug for s in sports.SPORTS) == REGISTERED


# SofaScore'un tournament.category.sport.name değerleri (research/all_sports/samples)
SOFASCORE_NAME = {
    "football": "Football", "basketball": "Basketball", "tennis": "Tennis",
    "american-football": "American football", "aussie-rules": "Aussie rules", "ice-hockey": "Hockey",
    "handball": "Handball", "rugby": "Rugby", "futsal": "Futsal", "minifootball": "Minifootball",
    "floorball": "Floorball", "volleyball": "Volleyball", "badminton": "Badminton", "table-tennis": "Table tennis",
    "padel": "Padel", "snooker": "Snooker", "baseball": "Baseball", "cricket": "Cricket", "esports": "E-sports",
    "darts": "Darts", "mma": "Mixed Martial Arts",
}


@pytest.mark.parametrize("slug", REGISTERED)
def test_get_sport_by_slug(slug):
    spec = sports.get_sport(slug)
    assert spec is not None and spec.slug == slug
    assert spec.i18n_key == f"sport.{slug}"
    assert spec.name == SOFASCORE_NAME[slug]


@pytest.mark.parametrize("raw", ["Football", "FOOTBALL", " football", "soccer", "Handball", "waterpolo", "", None, 7,
                                 ["football"]])
def test_get_sport_is_exact_and_unknown_is_none(raw):
    assert sports.get_sport(raw) is None


def test_score_families():
    assert [sports.score_family(s) for s in ORIGINAL] == ["football", "periods", "sets"]
    assert all(sports.score_family(s) == "periods" for s in PERIOD_SPORTS)
    assert all(sports.score_family(s) == "sets" for s in SET_SPORTS)
    assert [sports.score_family(s) for s in CLASS_B_SPORTS] == ["innings", "cricket", "sets", "sets", "fight"]
    assert sports.score_family("waterpolo") is None
    assert sports.score_family(None) is None


def test_period_formats():
    """Basketbolun bölünüşü skorlardan sezilir; öteki periyot sporlarınınki sabittir."""
    assert {s: sports.period_format(s) for s in REGISTERED} == {
        "football": None, "basketball": None, "tennis": None,
        "american-football": "quarters", "aussie-rules": "quarters", "ice-hockey": "thirds", "handball": "halves",
        "rugby": "halves", "futsal": "halves", "minifootball": "halves", "floorball": "thirds",
        **{s: None for s in SET_SPORTS + CLASS_B_SPORTS},
    }
    assert sports.period_format("waterpolo") is None and sports.period_format(None) is None
    assert all((s.period_format is None) == (s.slug == "basketball") for s in sports.SPORTS
               if s.score_family == "periods")


def test_set_formats():
    """Tenis kendi çizelgesini kullanır (None); öteki set sporlarında setin birimi sabittir."""
    assert {s: sports.set_format(s) for s in REGISTERED if sports.score_family(s) == "sets"} == {
        "tennis": None, "volleyball": "points", "badminton": "points", "table-tennis": "points", "padel": "games",
        "snooker": "frames", "esports": "games_won", "darts": "legs",
    }
    assert all(s.set_format is None for s in sports.SPORTS if s.score_family != "sets")
    assert sports.set_format("waterpolo") is None and sports.set_format(None) is None


# --- ad normalizasyonu ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Football", "football"), ("football", "football"), ("FOOTBALL", "football"), ("  Football ", "football"),
    ("Soccer", "football"),
    ("Basketball", "basketball"), ("basketball", "basketball"),
    ("Tennis", "tennis"), ("tennis", "tennis"),
    ("American football", "american-football"), ("american-football", "american-football"),
    ("Aussie rules", "aussie-rules"), ("aussie-rules", "aussie-rules"),
    ("Hockey", "ice-hockey"), ("Ice hockey", "ice-hockey"), ("ice-hockey", "ice-hockey"),
    ("Handball", "handball"), ("Rugby", "rugby"), ("Futsal", "futsal"),
    ("Minifootball", "minifootball"), ("Mini football", "minifootball"), ("Floorball", "floorball"),
    ("Volleyball", "volleyball"), ("Badminton", "badminton"), ("Table tennis", "table-tennis"),
    ("table-tennis", "table-tennis"), ("Padel", "padel"), ("Snooker", "snooker"),
    ("Baseball", "baseball"), ("Cricket", "cricket"), ("E-sports", "esports"), ("esports", "esports"),
    ("Darts", "darts"), ("Mixed Martial Arts", "mma"), ("MMA", "mma"),
])
def test_normalize_names_sofascore_uses(raw, expected):
    assert sports.normalize_sport(raw) == expected


@pytest.mark.parametrize("raw", ["waterpolo", "motorsport", "beach-volley", "", "   ", None, 0, 17, {}])
def test_normalize_unknown_is_none(raw):
    assert sports.normalize_sport(raw) is None


def _normalize_with_the_registry(raw):
    """Kayıt defterindeki sporların tam adı ve slug'ı kendi slug'ına; geri kalanı kayıt defterinden önceki gibi."""
    key = str(raw or "").strip().lower().replace(" ", "-")
    exact = {slug: slug for slug in REGISTERED} | {
        "soccer": "football", "hockey": "ice-hockey", "mini-football": "minifootball", "e-sports": "esports",
        "mixed-martial-arts": "mma"}
    return exact.get(key) or _normalize_before_registry(raw)


@pytest.mark.parametrize("raw", [
    *SOFASCORE_SLUGS, *SOFASCORE_NAMES, *(n.upper() for n in SOFASCORE_NAMES),
    "Soccer", "soccer (football)", "Basket", "basket-tennis", "tennis football", "foot ball", " Tennis\n", "x", "", None, 0,
])
def test_normalize_keeps_the_pre_registry_function_for_everything_else(raw):
    assert sports.normalize_sport(raw) == _normalize_with_the_registry(raw)


def test_unregistered_lookalikes_keep_the_legacy_mapping():
    """Kayıt defterinde olmayan benzer adlar eskisi gibi çözülür (bilinen sınır; spor eklenince düzelir)."""
    assert sports.normalize_sport("beach tennis") == "tennis"
    assert sports.normalize_sport("beach football") == "football"


def test_registered_lookalikes_resolve_to_their_own_sport():
    """SP-1'den önce "American football" ve "minifootball" futbol, SP-2'den önce "Table tennis" tenis sayılıyordu."""
    assert _normalize_before_registry("American football") == "football"
    assert sports.normalize_sport("American football") == "american-football"
    assert sports.normalize_sport("minifootball") == "minifootball"
    assert _normalize_before_registry("Table tennis") == "tennis"
    assert sports.normalize_sport("Table tennis") == "table-tennis"


def test_exact_name_wins_over_legacy_substring_once_a_sport_is_registered(monkeypatch):
    monkeypatch.setitem(sports._BY_EXACT_NAME, "beach-tennis", "beach-tennis")
    monkeypatch.setitem(sports._BY_SLUG, "beach-tennis", sports.get_sport("tennis"))
    assert sports.normalize_sport("beach-tennis") == "beach-tennis"
    assert sports.normalize_sport("Beach tennis") == "beach-tennis"
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
    assert sports.watcher_params("american-football") == WatcherParams("played_ratio", 5 * 3600, False)
    for sport in ("ice-hockey", "handball", "rugby", "minifootball"):
        assert sports.watcher_params(sport) == WatcherParams("played_ratio", 4 * 3600, False)
    for sport in ("aussie-rules", "futsal", "floorball"):  # kayıtlı yüklerde saat verisi yok
        assert sports.watcher_params(sport) == WatcherParams("never", 4 * 3600, False)
    assert sports.watcher_params("volleyball") == WatcherParams("last_set", 6 * 3600, False)
    for sport in ("badminton", "table-tennis"):
        assert sports.watcher_params(sport) == WatcherParams("last_set", 4 * 3600, False)
    assert sports.watcher_params("padel") == WatcherParams("last_set", 6 * 3600, True)
    assert sports.watcher_params("snooker") == WatcherParams("never", 6 * 3600, False)  # set kodu yok
    # B sınıfı (SP-3): bitişe yakınlık kuralı yok; kriketin çok günlü maçları için 6 gün
    assert sports.watcher_params("baseball") == WatcherParams("never", 6 * 3600, False)
    assert sports.watcher_params("cricket") == WatcherParams("never", 6 * 86400, False)
    assert sports.watcher_params("esports") == WatcherParams("never", 6 * 3600, False)
    for sport in ("darts", "mma"):
        assert sports.watcher_params(sport) == WatcherParams("never", 4 * 3600, False)


@pytest.mark.parametrize("sport", ["waterpolo", "Tennis", "", None])
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
        ("esports_games", "/event/{event_id}/esports-games"),
        ("innings", "/event/{event_id}/innings"),
    ]
    assert all(s.default_enabled for s in sports.DETAIL_SLICES)
    assert [s.key for s in sports.DETAIL_SLICES if not s.required] == ["esports_games"]
    # spora göre: tenisin point_by_point'i isteğe bağlı, dartınki tamlığa girer (tests/test_sport_slices.py)
    assert sports.get_slice("point_by_point").sports == frozenset({"tennis", "darts"})
    assert sports.get_slice("point_by_point").optional_in == frozenset({"tennis"})
    assert sports.get_slice("esports_games").sports == frozenset({"esports"})
    assert sports.get_slice("esports_games").phases == frozenset({"live", "post"})  # oyunlar maç başlayınca var
    assert sports.get_slice("innings").sports == frozenset({"cricket"})
    assert sports.get_slice("innings").phases == frozenset({"live", "post"})  # innings maç başlayınca var
    assert all(s.sports is None for s in sports.DETAIL_SLICES if s.key in COMMON_KEYS)


def test_slice_table_is_consistent():
    keys = [s.key for s in sports.DETAIL_SLICES]
    assert len(set(keys)) == len(keys)
    for s in sports.DETAIL_SLICES:
        assert s.sports is None or s.sports <= set(sports.sport_slugs())
        assert (s.not_in | s.optional_in) <= set(sports.sport_slugs())
        assert s.path.startswith("/event/{event_id}/")
        assert sports.get_slice(s.key) is s
    assert sports.get_slice("nope") is None


def test_slice_url():
    s = sports.get_slice("team_streaks")
    assert s.url("https://www.sofascore.com/api/v1", 42) == "https://www.sofascore.com/api/v1/event/42/team-streaks"


# Spor başına bütün dilimler ve tamlık: tests/test_sport_slices.py (kanıt tablosuna karşı)
@pytest.mark.parametrize("sport,expected", [
    ("football", COMMON_KEYS),
    ("basketball", COMMON_KEYS),
    ("tennis", COMMON_KEYS + ("point_by_point",)),
    ("handball", COMMON_KEYS),
    ("volleyball", COMMON_KEYS),
    ("table-tennis", COMMON_KEYS),  # point_by_point teniste ve dartta
    ("waterpolo", COMMON_KEYS),
    ("", COMMON_KEYS),
    (None, COMMON_KEYS),
])
def test_slices_for_sport(sport, expected):
    assert tuple(s.key for s in sports.slices_for(sport)) == expected
    assert tuple(s.key for s in sports.slices_for(sport, required_only=True)) == COMMON_KEYS


def test_sport_spec_lists_its_slices():
    assert sports.get_sport("football").detail_slices == COMMON_KEYS
    assert sports.get_sport("tennis").detail_slices == COMMON_KEYS + ("point_by_point",)
    assert sports.get_sport("esports").detail_slices == (
        "statistics", "team_streaks", "pregame_form", "h2h", "lineups", "esports_games")
    assert sports.get_sport("cricket").detail_slices == COMMON_KEYS + ("innings",)


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


# --- SliceSpec ve seçim (plan maddesi P12; docs/design/02-services.md 3.1) -------------------------------

def test_detail_slice_is_the_slice_spec_with_todays_defaults():
    assert sports.DetailSlice is sports.SliceSpec
    for s in sports.DETAIL_SLICES:
        # e-sporun oyunları (SP-3) maç başlamadan yoktur: yalnızca canlı ve bitmiş evrede
        phases = frozenset({"live", "post"}) if s.key in ("esports_games", "innings") else sports.ALL_PHASES
        assert (s.owner, s.subs, s.phases, s.group, s.keep_history, s.max_age) == (
            "event", None, phases, "core", False, None)
        assert s.counts_for_completeness is s.required
        assert all(s.counts_in(sport) is (s.required and sport not in s.optional_in)
                   for sport in sports.sport_slugs() if s.applies_to(sport))
    extra = sports.DetailSlice("innings", "/event/{event_id}/innings", sports=frozenset({"basketball"}))
    assert extra.required and extra.default_enabled and extra.owner == "event"


@pytest.mark.parametrize("bad", [
    {"owner": "league"}, {"phases": frozenset()}, {"phases": frozenset({"later"})}, {"group": "misc"},
    {"subs": ()}, {"subs": ("",)}, {"subs": "home"},
])
def test_slice_spec_rejects_invalid_fields(bad):
    with pytest.raises(ValueError):
        sports.SliceSpec("x", "/event/{event_id}/x", **bad)


def test_slice_spec_accepts_the_design_fields():
    import datetime as dt

    spec = sports.SliceSpec("standings", "/unique-tournament/{tournament_id}/season/{season_id}/standings/{sub}",
                            owner="season", subs=("total", "home", "away"), group="standings",
                            max_age=dt.timedelta(hours=6))
    odds = sports.SliceSpec("odds_all", "/event/{event_id}/odds/{sub}/all", subs="provider", group="odds",
                            keep_history=True)
    assert spec.owner == "season" and odds.subs == sports.PROVIDER_SUBS and odds.valid_in("pre")
    post_only = sports.SliceSpec("x", "/x", phases=frozenset({"post"}))
    assert not post_only.valid_in("live") and post_only.valid_in("post") and post_only.valid_in(None)


@pytest.mark.parametrize("sport", ["football", "basketball", "tennis", "handball", "", None])
def test_select_slices_without_a_selection_is_slices_for(sport):
    assert sports.select_slices("event", sport) == sports.slices_for(sport)
    assert sports.select_slices("event", sport, ["core"]) == sports.slices_for(sport)
    for phase in sports.PHASES:
        assert sports.select_slices("event", sport, phase=phase) == sports.slices_for(sport)
    assert sports.select_slices("season", sport) == ()


def _keys(found):
    return tuple(s.key for s in found)


def test_select_slices_with_a_selection():
    assert _keys(sports.select_slices("event", "football", ["h2h", "lineups"])) == ("h2h", "lineups")
    assert _keys(sports.select_slices("event", "football", ["point_by_point"])) == ()  # tenis dışında yok
    no_h2h = sports.SliceSelection(disable=("point_by_point", "h2h"))
    assert _keys(sports.select_slices("event", "tennis", no_h2h)) == tuple(k for k in COMMON_KEYS if k != "h2h")
    narrow = sports.SliceSelection(base=("h2h",), enable=("incidents",))
    assert _keys(sports.select_slices("event", "tennis", narrow)) == ("h2h", "incidents")
    # "statistics" hem dilim anahtarı hem (tasarımda) grup adı
    assert _keys(sports.select_slices("event", "football", ["statistics"])) == ("statistics",)
    # P28: `odds` grubu maçın dört oran dilimini seçer (varsayılan olarak kapalılar)
    assert _keys(sports.select_slices("event", "football", ["odds"])) == (
        "odds_featured", "odds_all", "odds_changes", "winning_odds")


def test_select_slices_turns_on_a_slice_that_is_off_by_default(monkeypatch):
    import dataclasses

    table = tuple(dataclasses.replace(s, default_enabled=s.key != "lineups") for s in sports.DETAIL_SLICES)
    monkeypatch.setattr(sports, "DETAIL_SLICES", table)
    assert "lineups" not in _keys(sports.select_slices("event", "football"))
    assert "lineups" in _keys(sports.select_slices("event", "football", ["core"]))
    assert "lineups" in _keys(sports.select_slices("event", "football", sports.SliceSelection(enable=("lineups",))))


@pytest.mark.parametrize("selection", [["nope"], sports.SliceSelection(enable=("Lineups",)),
                                       sports.SliceSelection(disable=("odds_everything",))])
def test_select_slices_rejects_unknown_names(selection):
    with pytest.raises(sports.UnknownSliceName) as info:
        sports.select_slices("event", "football", selection)
    assert "lineups" in info.value.known and "core" in info.value.known
    assert isinstance(info.value, ValueError)


def test_select_slices_rejects_bad_arguments():
    with pytest.raises(ValueError):
        sports.select_slices("league", "football")
    with pytest.raises(ValueError):
        sports.select_slices("event", "football", phase="halftime")
    with pytest.raises(ValueError):
        sports.select_slices("event", "football", "lineups")


def test_known_slice_names_list_each_name_once():
    names = sports.known_slice_names()
    assert len(names) == len(set(names))
    assert names[:len(sports.DETAIL_SLICES)] == tuple(s.key for s in sports.DETAIL_SLICES)
    assert set(sports.GROUPS) <= set(names)
    sports.check_slice_names(names)
