"""
src/store/layout.py: v3 düzeninin yol kuralları (docs/design/01-storage.md, bölüm 4.2).

İşlevler saftır: diske dokunmaz, yalnızca DATA_DIR'e göre göreli ("/" ayırıcılı) yol üretir.
"""
from __future__ import annotations

import os

import pytest

from src.store import LayoutError, layout

# --- maç dizinleri --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "event_id, expected",
    [
        (16416346, "v3/events/16/416/16416346"),  # tasarım belgesindeki örnek
        (0, "v3/events/0/000/0"),
        (7, "v3/events/0/000/7"),
        (999, "v3/events/0/000/999"),
        (1000, "v3/events/0/001/1000"),
        (999999, "v3/events/0/999/999999"),
        (1000000, "v3/events/1/000/1000000"),
        (12345678, "v3/events/12/345/12345678"),  # 8 hane
        (99999999, "v3/events/99/999/99999999"),
        (1234567890, "v3/events/1234/567/1234567890"),  # 10 hane
        (9999999999, "v3/events/9999/999/9999999999"),
    ],
)
def test_event_dir_depends_only_on_the_id(event_id, expected):
    assert layout.event_dir(event_id) == expected
    assert layout.event_id_from_dir(expected) == event_id
    assert layout.event_id_from_dir(expected + "/") == event_id


def test_no_event_directory_level_holds_more_than_a_thousand_entries():
    """İkinci düzey 3 haneli (000-999); aynı binlik dilimdeki 1000 kimlik aynı dizine düşer, 1001. düşmez."""
    parents = {layout.event_dir(i).rsplit("/", 1)[0] for i in range(16416000, 16417000)}
    assert parents == {"v3/events/16/416"}
    assert layout.event_dir(16417000).rsplit("/", 1)[0] == "v3/events/16/417"

    for event_id in (5, 4321, 16416346, 1234567890):
        million, thousand, name = layout.event_dir(event_id).split("/")[2:]
        assert (int(million), int(thousand), int(name)) == (event_id // 1_000_000, (event_id // 1000) % 1000, event_id)
        assert len(thousand) == 3


@pytest.mark.parametrize("bad", [-1, True, False, "16416346", 1.0, None])
def test_ids_must_be_non_negative_integers(bad):
    for build in (layout.event_dir, layout.tournament_dir, layout.team_dir, layout.player_dir, layout.sport_dir):
        with pytest.raises(LayoutError):
            build(bad)
    with pytest.raises(LayoutError):
        layout.season_dir(17, bad)
    with pytest.raises(LayoutError):
        layout.season_dir(bad, 76986)


@pytest.mark.parametrize(
    "rel",
    [
        "v3/events/16/416/16416347x",
        "v3/events/16/417/16416346",  # yanlış binlik dizini
        "v3/events/17/416/16416346",  # yanlış milyonluk dizini
        "v3/events/0/000/007",  # baştaki sıfırlar kurallı ad değil
        "match_details/8_LaLiga/season_LaLiga_26_27/16416346",  # eski düzen
        "v3/events/16/416",
        "",
    ],
)
def test_event_id_from_dir_rejects_everything_but_the_canonical_path(rel):
    assert layout.event_id_from_dir(rel) is None


# --- diğer varlıklar ------------------------------------------------------------------------------

def test_entity_directories():
    assert layout.tournament_dir(17) == "v3/tournaments/17"
    assert layout.season_dir(17, 76986) == "v3/tournaments/17/seasons/76986"
    assert layout.team_dir(42) == "v3/teams/0/42"
    assert layout.team_dir(2817) == "v3/teams/2/2817"
    assert layout.player_dir(1234567) == "v3/players/1234/1234567"
    assert layout.sport_dir(1) == "v3/sports/1"


def test_entity_dir_dispatches_on_the_kind():
    assert layout.entity_dir("event", 16416346) == layout.event_dir(16416346)
    assert layout.entity_dir("tournament", 17) == layout.tournament_dir(17)
    assert layout.entity_dir("season", 76986, tournament_id=17) == layout.season_dir(17, 76986)
    assert layout.entity_dir("team", 2817) == layout.team_dir(2817)
    assert layout.entity_dir("player", 1234567) == layout.player_dir(1234567)
    assert layout.entity_dir("sport", 1) == layout.sport_dir(1)
    assert set(layout.KINDS) == {"event", "tournament", "season", "team", "player", "sport"}

    with pytest.raises(LayoutError):
        layout.entity_dir("season", 76986)  # sezonun dizini turnuvasının altındadır
    with pytest.raises(LayoutError):
        layout.entity_dir("league", 17)


# --- varlık dizininin içi -------------------------------------------------------------------------

def test_files_inside_an_entity_directory():
    directory = layout.event_dir(16416346)

    assert layout.manifest_path(directory) == "v3/events/16/416/16416346/manifest.json"
    assert layout.slice_path(directory, "event") == "v3/events/16/416/16416346/event.json.gz"
    assert layout.slice_path(directory, "odds_all", "1") == "v3/events/16/416/16416346/odds_all/1.json.gz"
    assert layout.history_path(directory, "odds_all", "1") == "v3/events/16/416/16416346/_history/odds_all/1.jsonl.gz"
    assert layout.history_path(directory, "winning_odds") == "v3/events/16/416/16416346/_history/winning_odds/_.jsonl.gz"

    season = layout.season_dir(17, 76986)
    assert layout.slice_path(season, "schedule", "round_12") == "v3/tournaments/17/seasons/76986/schedule/round_12.json.gz"
    assert layout.slice_path(season, "schedule", "round_3_final") == (
        "v3/tournaments/17/seasons/76986/schedule/round_3_final.json.gz"
    )
    assert layout.slice_path(layout.tournament_dir(17), "seasons") == "v3/tournaments/17/seasons.json.gz"
    assert layout.slice_path(layout.player_dir(1234567), "season_statistics", "17-76986") == (
        "v3/players/1234/1234567/season_statistics/17-76986.json.gz"
    )


@pytest.mark.parametrize(
    "key, sub, name",
    [("statistics", "", "statistics"), ("odds_all", "1", "odds_all/1"), ("schedule", "last_0", "schedule/last_0")],
)
def test_slice_name_round_trip(key, sub, name):
    assert layout.slice_name(key, sub) == name
    assert layout.split_slice_name(name) == (key, sub)


@pytest.mark.parametrize("key", ["event", "a", "odds_all", "h2h", "a" * 40])
def test_valid_keys(key):
    assert layout.validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    ["", "Event", "1st", "_history", "odds-all", "odds all", "a" * 41, "a/b", "../x", "é", "nul", "con", "com1", None, 3],
)
def test_invalid_keys(key):
    with pytest.raises(LayoutError):
        layout.validate_key(key)
    with pytest.raises(LayoutError):
        layout.slice_path("v3/events/0/000/1", key)


@pytest.mark.parametrize("sub", ["", "1", "round_12", "total", "17-76986", "A.b-c_d", "x" * 80])
def test_valid_subs(sub):
    assert layout.validate_sub(sub) == sub


@pytest.mark.parametrize(
    "sub",
    ["a/b", "..\\x", "a b", "x" * 81, "é", "nul", "NUL", "aux.1", "LPT9", "_", None, 1],
)
def test_invalid_subs(sub):
    """`_` alt anahtarı yasak: geçmiş dosyasında alt anahtarsız dilimin adıdır, ikisi çakışırdı."""
    with pytest.raises(LayoutError):
        layout.validate_sub(sub)
    with pytest.raises(LayoutError):
        layout.slice_path("v3/events/0/000/1", "odds_all", sub)
    with pytest.raises(LayoutError):
        layout.history_path("v3/events/0/000/1", "odds_all", sub)


@pytest.mark.parametrize("name", ["", "/1", "odds_all/", "odds_all/1/2", "Odds/1", 5, None])
def test_split_slice_name_rejects_malformed_names(name):
    with pytest.raises(LayoutError):
        layout.split_slice_name(name)


# --- dizin düzeyindeki dosyalar -------------------------------------------------------------------

def test_meta_paths():
    assert layout.SCHEMA_FILE == ".meta/schema.json"
    assert layout.CATALOG_DB == ".meta/catalog.db"
    assert layout.STATE_DB == ".meta/state.db"
    assert layout.LEGACY_JOBS_DB == ".meta/jobs.db"
    assert layout.TMP_DIR == ".meta/tmp"
    assert layout.TRASH_DIR == ".meta/trash"
    assert layout.UNCLEAN_MARKER == ".meta/locks/unclean"


def test_lock_paths():
    assert layout.lock_path("writer") == ".meta/locks/writer.lock"
    assert layout.lock_path("maintenance") == ".meta/locks/maintenance.lock"
    assert layout.lock_path("watcher:tennis") == ".meta/locks/watcher-tennis.lock"
    assert layout.lock_path("watcher:american-football") == ".meta/locks/watcher-american-football.lock"

    for bad in ("", "Writer", "watcher:", ":tennis", "watcher:a:b", "../writer", "a/b", None):
        with pytest.raises(LayoutError):
            layout.lock_path(bad)


def test_change_segments_are_named_by_month():
    assert layout.change_segment(2026, 10) == "changes/2026-10.jsonl"
    assert layout.change_segment(2027, 1) == "changes/2027-01.jsonl"

    for year, month in ((2026, 0), (2026, 13), (0, 5), ("2026", 5)):
        with pytest.raises(LayoutError):
            layout.change_segment(year, month)


def test_resolve_joins_with_the_platform_separator(tmp_path):
    rel = layout.slice_path(layout.event_dir(16416346), "odds_all", "1")

    assert layout.resolve(tmp_path, rel) == os.path.join(
        str(tmp_path), "v3", "events", "16", "416", "16416346", "odds_all", "1.json.gz"
    )
    assert layout.resolve(str(tmp_path), layout.META_DIR) == os.path.join(str(tmp_path), ".meta")
    assert not (tmp_path / "v3").exists()  # saf işlev: hiçbir şey oluşturmaz


def test_every_name_is_ascii_and_valid_on_windows():
    """Yollar yalnızca rakam, sabit sözcük ve doğrulanmış anahtarlardan oluşur: ':' '\\' '*' '?' vb. yok."""
    paths = [
        layout.event_dir(1234567890),
        layout.season_dir(17, 76986),
        layout.team_dir(2817),
        layout.player_dir(1234567),
        layout.sport_dir(1),
        layout.slice_path(layout.event_dir(16416346), "odds_all", "A.b-c_d"),
        layout.history_path(layout.event_dir(16416346), "odds_all"),
        layout.lock_path("watcher:table-tennis"),
        layout.change_segment(2026, 10),
        layout.SCHEMA_FILE, layout.CATALOG_DB, layout.STATE_DB, layout.UNCLEAN_MARKER, layout.TMP_DIR, layout.TRASH_DIR,
    ]
    forbidden = set('\\:*?"<>|')
    for path in paths:
        assert path.isascii()
        assert not forbidden & set(path), path
        assert not path.startswith("/") and "//" not in path and ".." not in path.split("/")
        assert all(part and not part.endswith((" ", ".")) for part in path.split("/")), path
