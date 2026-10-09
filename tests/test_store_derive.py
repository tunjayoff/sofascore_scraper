"""
sofascore_scraper/store/derive.py: yük → katalog satırı (docs/design/01-storage.md, bölüm 3.3).

Altın dosya tests/golden/derive/event_rows.json, tests/fixtures/status altındaki her gerçek yanıt için
`event_row` çıktısını tutar. Çıktı bilerek değiştirildiyse DERIVE_VERSION artırılır (katalog yeniden
kurulsun diye) ve dosya şöyle yeniden üretilir:

    REGEN_DERIVE_GOLDEN=1 python -m pytest tests/test_store_derive.py

Ağ yok. Bugünkü kurallarla karşılaştırma: classify_status / extract_scores (sofascore_scraper/status.py),
`_tier_hint` (sofascore_scraper/refresh.py), listing.sortable_year (2.x'te SeasonFetcher._get_sortable_year_value) ve
özet CSV satırı (tests/store_fixtures.summary_row = 2.x'in MatchFetcher._save_season_summary).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import store_fixtures as sf
from sofascore_scraper.refresh import _tier_hint
from sofascore_scraper.services.listing import sortable_year
from sofascore_scraper.status import ScoreSheet, classify_status, extract_scores
from sofascore_scraper.store import PayloadCorrupt, derive

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "status"
GOLDEN = Path(__file__).parent / "golden" / "derive" / "event_rows.json"
PATHS = sorted(FIXTURES.glob("*/*.json"))

# tests/store_fixtures.py'deki bütün olaylar (üç spor, turnuvasız ve sporu bilinmeyen olaylar dahil)
EVENTS = {name: value for name, value in vars(sf).items() if isinstance(value, sf.Ev)}


def _key(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def _load(path: Path) -> tuple[dict, str, float]:
    """Yanıt (kimliği `event_id`'den), spor (dizin adı) ve yanıtın alındığı an."""
    event = json.loads(path.read_text(encoding="utf-8"))
    event["id"] = event["event_id"]
    return event, path.parent.name, dt.datetime.fromisoformat(event["fetched_at_utc"]).timestamp()


def _row(path: Path) -> dict:
    event, sport, fetched_at = _load(path)
    return derive.event_row(event, "event", fetched_at, sport=sport)


def _compact(row: dict) -> dict:
    """Altın dosyadaki biçim: NULL sütunlar yazılmaz (sütun kümesi ayrıca denetlenir)."""
    return {k: v for k, v in row.items() if v is not None}


def _sheet_json(sheet: ScoreSheet) -> dict:
    """extract_scores sonucu, JSON'dan geçmiş haliyle (Pair → liste, sayısal anahtar → metin)."""
    return json.loads(json.dumps(dataclasses.asdict(sheet)))


@pytest.fixture(scope="module")
def golden() -> dict:
    if os.getenv("REGEN_DERIVE_GOLDEN") == "1":
        rows = {_key(p): _compact(_row(p)) for p in PATHS}
        lines = [f'  {json.dumps(k)}: {json.dumps(rows[k], ensure_ascii=False)}' for k in sorted(rows)]
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            '{\n "derive_version": %d,\n "rows": {\n%s\n }\n}\n' % (derive.DERIVE_VERSION, ",\n".join(lines)),
            encoding="utf-8", newline="\n",
        )
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


# --- altın satırlar: tests/fixtures/status altındaki her yanıt -----------------------------------------

def test_golden_covers_every_status_fixture_and_names_the_derive_version(golden):
    assert sorted(golden["rows"]) == sorted(_key(p) for p in PATHS)
    # 154 + 25 örnek: SP-1'in sekiz periyot sporu; + 24: SP-2'nin beş set sporu; + 28: SP-3'ün beş B sınıfı sporu
    # + 2: tek setlik dart ve harita skorlu bitmiş e-spor maçı (B3)
    assert len(PATHS) == 233
    # Sürüm artınca altın dosya yeniden üretilir; altın dosya değişince sürüm artırılır
    assert golden["derive_version"] == derive.DERIVE_VERSION


@pytest.mark.parametrize("path", PATHS, ids=_key)
def test_event_row_matches_golden(path, golden):
    row = _row(path)

    assert tuple(row) == derive.EVENT_DERIVED_COLUMNS
    assert _compact(row) == golden["rows"][_key(path)]


@pytest.mark.parametrize("path", PATHS, ids=_key)
def test_status_class_and_scores_equal_the_status_module(path):
    event, sport, fetched_at = _load(path)
    row = derive.event_row(event, "event", fetched_at, sport=sport)
    sheet = extract_scores(event, sport)
    expected = _sheet_json(sheet)

    assert row["status_class"] == classify_status(event).value == sheet.status_class.value
    assert row["sport"] == sheet.sport == sport
    assert row["winner_code"] == sheet.winner_code
    assert row["change_ts"] == sheet.raw_change_ts
    # scores_json: çizelgenin ayrı sütunu olmayan bütün alanları + ailesi
    common = {f.name for f in dataclasses.fields(ScoreSheet)}
    stored = json.loads(row["scores_json"])
    family = {"FootballScores": "football", "BasketballScores": "periods", "PeriodsScores": "periods",
              "TennisScores": "sets", "SetsScores": "sets", "InningsScores": "innings",
              "CricketScores": "cricket", "FightScores": "fight"}[type(sheet).__name__]
    assert stored == {"family": family, **{k: v for k, v in expected.items() if k not in common}}
    assert common == {"sport", "status_class", "winner_code", "raw_change_ts", "raw_changed_fields"}


@pytest.mark.parametrize("path", PATHS, ids=_key)
def test_scores_and_status_columns_are_read_from_the_payload_as_documented(path):
    event, sport, fetched_at = _load(path)
    row = derive.event_row(event, "event", fetched_at, sport=sport)
    home, away = event["homeScore"], event["awayScore"]

    assert row["home_score"] == home.get("display", home.get("current"))
    assert row["away_score"] == away.get("display", away.get("current"))
    assert "home_score_current" not in row and "away_score_current" not in row  # 3.1'de kalktı (P30)
    assert (row["status_type"], row["status_code"], row["status_description"]) == (
        event["status"].get("type"), event["status"].get("code"), event["status"].get("description"))
    assert row["start_ts"] == event["startTimestamp"]
    assert row["observed_at"] == int(fetched_at)
    assert row["observed_gap"] == int(fetched_at) - event["startTimestamp"]
    assert row["tier_hint"] == _as_int(_tier_hint(event))


def _as_int(flag):
    return None if flag is None else int(flag)


def test_scores_json_is_compact_sorted_and_stable():
    event, sport, _ = _load(FIXTURES / "football" / "A_finished-120-ap__16950622.json")
    first = derive.scores_json(event, sport)

    assert first == derive.scores_json(json.loads(json.dumps(event)), sport)
    assert first == json.dumps(json.loads(first), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert json.loads(first) == {
        "family": "football", "ht": [2, 2], "ft90": [3, 3], "aet": None, "penalties": [7, 6],
        "aggregated": None, "aggregated_winner_code": None,
    }


# --- tam olay yükü: elle yazılmış beklenen satır -------------------------------------------------------

EVENT = {
    "tournament": {
        "name": "Premier League", "slug": "premier-league", "id": 1,
        "category": {"name": "England", "slug": "england", "id": 1, "alpha2": "EN", "flag": "england",
                     "sport": {"name": "Football", "slug": "football", "id": 1}},
        "uniqueTournament": {
            "name": "Premier League", "slug": "premier-league", "id": 17, "hasEventPlayerStatistics": True,
            "category": {"name": "England", "slug": "england", "id": 1, "alpha2": "EN",
                         "sport": {"name": "Football", "slug": "football", "id": 1}},
        },
    },
    "season": {"name": "Premier League 26/27", "year": "26/27", "editor": False, "id": 96668},
    "roundInfo": {"round": 29, "name": "Semifinals", "slug": "semifinals", "cupRoundType": 2},
    "customId": "FP",
    "status": {"code": 100, "description": "Ended", "type": "finished"},
    "winnerCode": 1,
    "homeTeam": {"name": "Brighton & Hove Albion", "slug": "brighton-and-hove-albion", "shortName": "Brighton",
                 "gender": "M", "sport": {"name": "Football", "slug": "football", "id": 1}, "nameCode": "BHA",
                 "national": False, "type": 0, "id": 30, "country": {"alpha2": "EN", "name": "England"}},
    "awayTeam": {"name": "Fenerbahçe İstanbul", "slug": "fenerbahce", "shortName": "Fenerbahçe", "gender": "M",
                 "nameCode": "FEN", "national": False, "type": 0, "id": 3052,
                 "country": {"alpha2": "TR", "name": "Türkiye"}},
    "homeScore": {"current": 4, "display": 4, "period1": 4, "period2": 0, "normaltime": 4},
    "awayScore": {"current": 0, "display": 0, "period1": 0, "period2": 0, "normaltime": 0},
    "time": {"injuryTime1": 5, "injuryTime2": 5},
    "changes": {"changes": ["status.code", "status.description"], "changeTimestamp": 1787500200},
    "hasGlobalHighlights": True,
    "id": 16416346,
    "slug": "brighton-and-hove-albion-fenerbahce",
    "startTimestamp": 1787493600,
    "finalResultOnly": False,
}

EXPECTED_ROW = {
    "id": 16416346,
    "sport": "football",
    "category_id": 1,
    "tournament_id": 17,
    "stage_id": 1,
    "stage_name": "Premier League",
    "season_id": 96668,
    "round": 29,
    "round_name": "Semifinals",
    "round_slug": "semifinals",
    "start_ts": 1787493600,
    "status_type": "finished",
    "status_code": 100,
    "status_description": "Ended",
    "status_class": "completed",
    "home_id": 30,
    "away_id": 3052,
    "home_name": "Brighton & Hove Albion",
    "away_name": "Fenerbahçe İstanbul",
    "home_score": 4,
    "away_score": 0,
    "winner_code": 1,
    "scores_json": '{"aet":null,"aggregated":null,"aggregated_winner_code":null,"family":"football",'
                   '"ft90":[4,0],"ht":[4,0],"penalties":null}',
    "slug": "brighton-and-hove-albion-fenerbahce",
    "custom_id": "FP",
    "observed_at": 1787760000,
    "change_ts": 1787500200,
    "observed_gap": 266400,
    "tier_hint": 1,
    "row_source": "event",
    "has_event_payload": 1,
}


def test_full_event_payload_gives_the_documented_row():
    assert derive.event_row(EVENT, "event", 1787760000) == EXPECTED_ROW


def test_event_row_does_not_change_the_payload_and_accepts_any_mapping():
    before = json.dumps(EVENT, sort_keys=True)

    assert derive.event_row(types.MappingProxyType(EVENT), "event", 1787760000) == EXPECTED_ROW
    assert json.dumps(EVENT, sort_keys=True) == before


def test_listing_row_has_no_event_payload():
    row = derive.event_row(EVENT, "listing")

    assert (row["row_source"], row["has_event_payload"]) == ("listing", 0)
    assert (row["observed_at"], row["observed_gap"]) == (None, None)
    assert {k: v for k, v in row.items() if k not in ("row_source", "has_event_payload", "observed_at", "observed_gap")} \
        == {k: v for k, v in EXPECTED_ROW.items() if k not in ("row_source", "has_event_payload", "observed_at", "observed_gap")}


def test_observed_at_accepts_datetime_float_and_none():
    aware = dt.datetime(2026, 8, 24, 16, 0, 0, 900000, tzinfo=dt.timezone.utc)
    stamp = int(aware.timestamp())

    assert derive.event_row(EVENT, "event", aware)["observed_at"] == stamp
    assert derive.event_row(EVENT, "event", aware.replace(tzinfo=None))["observed_at"] == stamp  # saat dilimsiz = UTC
    assert derive.event_row(EVENT, "event", aware.astimezone(dt.timezone(dt.timedelta(hours=3))))["observed_at"] == stamp
    assert derive.event_row(EVENT, "event", stamp + 0.9)["observed_at"] == stamp
    # Ertelenen maç: başlangıç ileri alındı, gözlem ondan önce → aralık negatif (bölüm 8.3)
    assert derive.event_row(EVENT, "event", 1787493600 - 3600)["observed_gap"] == -3600
    no_start = {k: v for k, v in EVENT.items() if k != "startTimestamp"}
    row = derive.event_row(no_start, "event", stamp)
    assert (row["start_ts"], row["observed_at"], row["observed_gap"]) == (None, stamp, None)


def test_epoch_seconds():
    assert derive.epoch_seconds(None) is None
    assert derive.epoch_seconds(1787760000) == 1787760000
    assert derive.epoch_seconds(1787760000.999) == 1787760000
    assert derive.epoch_seconds(dt.datetime(1970, 1, 1, 0, 0, 5, tzinfo=dt.timezone.utc)) == 5
    assert derive.epoch_seconds(float("nan")) is None


def test_sport_hint_is_used_only_when_the_payload_does_not_name_its_sport():
    bare = {"id": 1, "status": {"type": "finished", "code": 100}, "homeScore": {"current": 2}, "awayScore": {"current": 1}}

    assert derive.event_row(bare)["sport"] == ""
    assert derive.event_row(bare)["scores_json"] is None
    assert derive.event_row(bare, sport="Tennis")["sport"] == "tennis"
    assert json.loads(derive.event_row(bare, sport="tennis")["scores_json"])["family"] == "sets"
    assert derive.event_row(EVENT, sport="tennis")["sport"] == "football"  # yükteki spor önceliklidir
    # Kayıt defterinde olmayan spor: satır yazılır, çizelge yok
    waterpolo = dict(bare, tournament={"category": {"sport": {"slug": "waterpolo"}}})
    row = derive.event_row(waterpolo)
    assert (row["sport"], row["scores_json"], row["status_class"], row["home_score"]) == ("waterpolo", None, "completed", 2)


def test_invalid_input_is_rejected():
    with pytest.raises(ValueError):
        derive.event_row(EVENT, "summary")
    for payload in ({}, {"id": None}, {"id": "abc"}, {"id": True}, {"id": [1]}):
        with pytest.raises(PayloadCorrupt):
            derive.event_row(payload)
    for payload in (None, [], "x", 5):
        with pytest.raises(PayloadCorrupt):
            derive.event_row(payload)
        with pytest.raises(PayloadCorrupt):
            derive.event_entity_rows(payload, updated_at=0)


MALFORMED = [
    {"tournament": "x", "season": [], "roundInfo": 3, "status": "finished", "homeTeam": None, "awayTeam": 7},
    {"homeScore": [1], "awayScore": "2", "changes": [], "tournament": {"category": {"sport": {"slug": "football"}}}},
    {"status": {"type": ["finished"], "code": []}, "winnerCode": "x", "startTimestamp": "soon"},
    {"status": {"type": "finished", "code": 100}, "homeScore": {"current": "3", "display": None},
     "awayScore": {"current": 2.0}, "startTimestamp": "1787493600", "winnerCode": 2 ** 70,
     "tournament": {"uniqueTournament": {"id": "17"}, "id": 1.0, "name": 5}},
]


@pytest.mark.parametrize("extra", MALFORMED, ids=range(len(MALFORMED)))
def test_malformed_fields_never_raise_and_give_typed_columns(extra):
    row = derive.event_row({"id": 9, **extra}, "event", 100)
    entities = derive.event_entity_rows({"id": 9, **extra}, updated_at=100)

    assert tuple(row) == derive.EVENT_DERIVED_COLUMNS
    text_columns = {"sport", "stage_name", "round_name", "round_slug", "status_type", "status_description",
                    "status_class", "home_name", "away_name", "scores_json", "slug", "custom_id", "row_source"}
    for column, value in row.items():
        assert value is None or isinstance(value, str if column in text_columns else int), column
        assert not isinstance(value, bool)
    assert isinstance(entities, derive.EntityRows)


def test_malformed_values_are_coerced_or_dropped():
    row = derive.event_row({"id": 9, **MALFORMED[3]}, "event", 100)

    assert (row["home_score"], row["away_score"]) == (3, 2)
    assert (row["start_ts"], row["winner_code"]) == (1787493600, None)  # 64 bite sığmayan sayı yazılmaz
    assert (row["tournament_id"], row["stage_id"], row["stage_name"]) == (17, 1, "5")
    assert derive.event_row({"id": 9, **MALFORMED[2]})["status_class"] == "unknown"
    assert derive.event_row({"id": 9, **MALFORMED[1]})["scores_json"] is None


# --- bugünkü liste ve CSV biçimleri ---------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(EVENTS), ids=str)
def test_legacy_summary_columns_can_be_rebuilt_from_the_row(name):
    """
    Özet CSV'sinin sütunları (2.x'te sofascore_scraper/match_fetcher.py:547-575) satırdan geri kurulabilmeli (bölüm 3.3,
    8.2); skor sütunları hariç: CSV'nin `current` skorunun sütunları 3.1'de kalktı (P30), satırda normalleştirilmiş skor
    (`display`, yoksa `current`) durur.
    """
    ev = EVENTS[name]
    event = sf.basic_payload(ev)
    row = derive.event_row(event, "event")
    summary = sf.summary_row(ev.round if ev.round is not None else "", event)

    assert row["id"] == summary["match_id"] == sf.event_id(ev)
    assert (row["home_name"], row["away_name"]) == (summary["home_team"], summary["away_team"])
    assert (row["status_description"] or "") == summary["status"]
    assert (row["stage_name"] or "") == summary["tournament"]
    assert row["round"] == ev.round
    assert row["sport"] == (ev.league.sport or "")
    assert row["tournament_id"] == ev.league.id
    assert row["season_id"] == ev.season.id
    assert row["status_class"] == classify_status(event).value
    assert row["tier_hint"] == _as_int(_tier_hint(event))


def test_penalty_shootout_keeps_both_scores():
    """
    Penaltılarla biten maç: normalleştirilmiş skor 3-3; 2.x listelerinin yazdığı `current` (10-9) satırda yok (3.1),
    penaltılar skor çizelgesinde.
    """
    row = derive.event_row(sf.basic_payload(sf.CUP_PEN), "event")

    assert (row["home_score"], row["away_score"]) == (3, 3)
    assert json.loads(row["scores_json"])["penalties"] is not None
    assert row["status_description"] == "AP"


TIER_CASES = [
    ({}, None),
    ({"hasEventPlayerStatistics": True}, 1),
    ({"hasEventPlayerStatistics": False}, 0),
    ({"tournament": {"uniqueTournament": {"hasEventPlayerStatistics": True}}}, 1),
    ({"tournament": {"uniqueTournament": {"hasEventPlayerStatistics": False}}}, 0),
    ({"tournament": {"uniqueTournament": {"hasEventPlayerStatistics": False}}, "hasEventPlayerStatistics": True}, 1),
    ({"tournament": {"uniqueTournament": {}}, "hasEventPlayerStatistics": None}, None),
]


@pytest.mark.parametrize("event, expected", TIER_CASES, ids=range(len(TIER_CASES)))
def test_tier_hint_equals_the_change_log_rule(event, expected):
    assert derive.tier_hint(event) == expected == _as_int(_tier_hint(event))


# --- ad katlama -----------------------------------------------------------------------------------------

FOLD_CASES = [
    ("Beşiktaş", "besiktas"),
    ("İstanbul Başakşehir", "istanbul basaksehir"),
    ("ISPARTA", "isparta"),
    ("Kasımpaşa", "kasimpasa"),
    ("Fenerbahçe", "fenerbahce"),
    ("Göztepe", "goztepe"),
    ("Atlético Madrid", "atletico madrid"),
    ("Bodø/Glimt", "bodo/glimt"),
    ("Łódź", "lodz"),
    ("Đoković N.", "dokovic n."),
    ("Straße", "strasse"),
    ("São Paulo", "sao paulo"),
    ("  Real   Madrid \t", "real madrid"),
    ("Granollers M / Zeballos H", "granollers m / zeballos h"),
    ("ﬁnal", "final"),
    ("Ægir Þór", "aegir thor"),
    ("", ""),
    (None, ""),
    (17, "17"),
]


@pytest.mark.parametrize("raw, folded", FOLD_CASES, ids=[str(c[0]) for c in FOLD_CASES])
def test_fold_name(raw, folded):
    assert derive.fold_name(raw) == folded
    assert derive.fold_name(folded) == folded  # katlanmış ad yeniden katlanınca değişmez


def test_fold_name_matches_across_spellings():
    assert derive.fold_name("FENERBAHÇE") == derive.fold_name("fenerbahce") == derive.fold_name("Fenerbahçe")
    assert derive.fold_name("istanbul") == derive.fold_name("İSTANBUL") == derive.fold_name("ISTANBUL")


# --- sezon sıralama anahtarı ----------------------------------------------------------------------------

YEARS = ["24/25", "26/27", "99/00", "98/99", "00/01", "49/50", "50/51", "2024/2025", "2024/25", "2024", "1998",
         "0", "", "2026 ", " 24 / 25 ", "24/25/26", "abc", "x/y", "5/6", "24/", "/25", "2024.5", "-1", "1e3"]


@pytest.mark.parametrize("year", YEARS, ids=repr)
def test_season_sort_key_equals_todays_rule(year):
    assert derive.season_sort_key(year) == sortable_year(year)


def test_season_sort_key_values():
    assert [derive.season_sort_key(y) for y in ("26/27", "99/00", "98/99", "2024/2025", "2024", None)] == \
        [2026.0, 2000.0, 1998.0, 2024.0, 2024.0, 0.0]
    ordered = sorted(["24/25", "2026", "98/99", "99/00", "25/26"], key=derive.season_sort_key, reverse=True)
    assert ordered == ["2026", "25/26", "24/25", "99/00", "98/99"]


@pytest.mark.parametrize("year", ["ab/cd", "abcd/ef", "nan", "inf", "-inf", "nan/1", 2024, 24.5, ["24/25"]], ids=repr)
def test_season_sort_key_never_raises_where_todays_rule_fails(year):
    """Bugünkü işlev bu girdilerde hata fırlatır ya da NaN / sonsuz döndürür; katalog satırı 0.0 ya da sayı alır."""
    value = derive.season_sort_key(year)

    assert isinstance(value, float) and value == value and abs(value) != float("inf")
    if isinstance(year, str):
        assert value == 0.0
    assert derive.season_sort_key(2024) == 2024.0


# --- varlık satırları -----------------------------------------------------------------------------------

def test_entity_rows_of_a_full_event():
    rows = derive.event_entity_rows(EVENT, updated_at=dt.datetime(2026, 8, 24, 16, 0, tzinfo=dt.timezone.utc))
    at = 1787587200

    assert rows.sport == {"slug": "football", "id": 1, "name": "Football"}
    assert rows.category == {"id": 1, "sport": "football", "name": "England", "slug": "england", "alpha2": "EN"}
    assert rows.tournament == {
        "id": 17, "sport": "football", "category_id": 1, "name": "Premier League",
        "name_folded": "premier league", "slug": "premier-league", "updated_at": at,
    }
    assert rows.season == {
        "id": 96668, "tournament_id": 17, "name": "Premier League 26/27", "year": "26/27",
        "sort_key": 2026.0, "updated_at": at,
    }
    assert rows.participants == [
        {"id": 30, "sport": "football", "name": "Brighton & Hove Albion", "name_folded": "brighton & hove albion",
         "short_name": "Brighton", "slug": "brighton-and-hove-albion", "name_code": "BHA", "country": "EN",
         "gender": "M", "type": 0, "national": 0, "updated_at": at},
        # Takımın kendi `sport` alanı yok: olayın sporu kullanılır
        {"id": 3052, "sport": "football", "name": "Fenerbahçe İstanbul", "name_folded": "fenerbahce istanbul",
         "short_name": "Fenerbahçe", "slug": "fenerbahce", "name_code": "FEN", "country": "TR",
         "gender": "M", "type": 0, "national": 0, "updated_at": at},
    ]


def test_entity_rows_without_unique_tournament_or_sport():
    friendly = derive.event_entity_rows(sf.basic_payload(sf.FRIENDLY_A), updated_at=7)
    unknown = derive.event_entity_rows(sf.basic_payload(sf.NO_SPORT_A), updated_at=7)

    # uniqueTournament yok: turnuva ve (turnuvaya bağlı olduğu için) sezon satırı yok
    assert (friendly.tournament, friendly.season) == (None, None)
    assert friendly.sport == {"slug": "football", "id": 1, "name": "Football"}
    assert [p["sport"] for p in friendly.participants] == ["football", "football"]
    assert (unknown.sport, unknown.tournament, unknown.season) == (None, None, None)
    assert [p["sport"] for p in unknown.participants] == [None, None]
    assert [p["sport"] for p in derive.event_entity_rows(
        sf.basic_payload(sf.NO_SPORT_A), updated_at=7, sport="Football").participants] == ["football", "football"]


def test_single_entity_functions_reject_objects_without_id():
    assert derive.sport_row({"name": "Football"}) is None
    assert derive.sport_row(None) is None
    assert derive.category_row({"name": "England"}) is None
    assert derive.category_row({"id": 1}) == {"id": 1, "sport": "", "name": None, "slug": None, "alpha2": None}
    assert derive.category_row({"id": 1}, sport="Tennis")["sport"] == "tennis"
    assert derive.tournament_row({"name": "x"}, updated_at=1) is None
    assert derive.tournament_row("x", updated_at=1) is None
    assert derive.season_row({"id": 5}, tournament_id=None, updated_at=1) is None
    assert derive.season_row({"name": "x"}, tournament_id=17, updated_at=1) is None
    assert derive.participant_row({"name": "x"}, updated_at=1) is None
    assert derive.participant_row(None, updated_at=1) is None


def test_tournament_row_from_a_bare_unique_tournament_object():
    """`/unique-tournament/{id}` yanıtındaki nesne: kategori ve spor nesnenin içinden okunur."""
    row = derive.tournament_row(
        {"id": 2361, "name": "Wimbledon, Men", "slug": "wimbledon-men",
         "category": {"id": 3, "name": "ATP", "sport": {"slug": "Tennis"}}},
        updated_at=10, category={"id": 99, "sport": {"slug": "football"}}, sport="football")

    assert row == {"id": 2361, "sport": "tennis", "category_id": 3, "name": "Wimbledon, Men",
                   "name_folded": "wimbledon, men", "slug": "wimbledon-men", "updated_at": 10}
    bare = derive.tournament_row({"id": 5}, updated_at=10)
    assert bare == {"id": 5, "sport": None, "category_id": None, "name": None, "name_folded": None,
                    "slug": None, "updated_at": 10}


def test_season_list_rows_keep_the_payload_order():
    payload = {"seasons": [
        {"name": "Premier League 26/27", "year": "26/27", "editor": False, "id": 96668},
        {"name": "Premier League 25/26", "year": "25/26", "editor": False, "id": 76986},
        {"name": "no id"},
        {"name": "Premier League 99/00", "year": "99/00", "id": 6},
        {"name": "duplicate", "year": "26/27", "id": 96668},
    ]}

    rows = derive.season_list_rows(payload, tournament_id=17, updated_at=3)

    assert [(r["id"], r["position"], r["listed"], r["sort_key"]) for r in rows] == [
        (96668, 0, 1, 2026.0), (76986, 1, 1, 2025.0), (6, 3, 1, 2000.0)]
    assert rows[0] == {"id": 96668, "tournament_id": 17, "name": "Premier League 26/27", "year": "26/27",
                       "sort_key": 2026.0, "updated_at": 3, "listed": 1, "position": 0}
    for bad in (None, [], {"seasons": None}, {"seasons": {"id": 1}}, {}):
        assert derive.season_list_rows(bad, tournament_id=17, updated_at=3) == []
    # Olaydan türetilen sezon satırı `listed` / `position` sütunlarını taşımaz (upsert onlara dokunmaz)
    assert set(derive.season_row(payload["seasons"][0], tournament_id=17, updated_at=3)) == \
        {"id", "tournament_id", "name", "year", "sort_key", "updated_at"}


def test_event_participant_rows():
    row = derive.event_row(EVENT, "event")

    assert derive.event_participant_rows(row) == [
        {"participant_id": 30, "start_ts": 1787493600, "event_id": 16416346, "side": 1},
        {"participant_id": 3052, "start_ts": 1787493600, "event_id": 16416346, "side": 2},
    ]
    assert derive.event_participant_rows({**row, "start_ts": None, "home_id": None}) == [
        {"participant_id": 3052, "start_ts": 0, "event_id": 16416346, "side": 2}]
    # İki tarafta aynı kimlik: birincil anahtar çakışmasın diye tek satır
    assert derive.event_participant_rows({**row, "away_id": 30}) == [
        {"participant_id": 30, "start_ts": 1787493600, "event_id": 16416346, "side": 1}]


# --- saflık ---------------------------------------------------------------------------------------------

def test_module_is_pure_and_respects_the_store_layering():
    """Taze yorumlayıcıda sofascore_scraper.store.derive yalnızca Store'un izinli olduğu sofascore_scraper modüllerini yükler (bölüm 2.1)."""
    code = (
        "import sys, json; import sofascore_scraper.store.derive; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'sofascore_scraper' or m.startswith('sofascore_scraper.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    loaded = set(json.loads(out.stdout))

    assert loaded == {"sofascore_scraper", "sofascore_scraper.exceptions", "sofascore_scraper.sports", "sofascore_scraper.status", "sofascore_scraper.store", "sofascore_scraper.store.derive",
                      "sofascore_scraper.store.errors"}
