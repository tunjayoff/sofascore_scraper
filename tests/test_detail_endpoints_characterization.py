"""
Karakterizasyon: bir maç için istenen detay uç noktaları.

Beklenen listeler bu dosyada elle yazılıdır; kayıt defterinden (sofascore_scraper/sports.py) türetilmez. Böylece
tablo değişirse hangi isteğin eklendiği ya da düştüğü burada görünür. Gerçek ağ yok: istekler sahte taşıyıcıya
gider (tests/fakes/sofascore.py), istek katmanı gerçektir.

P13'ten beri bütün yollar tek boru hattıdır (sofascore_scraper/services/pipeline.py); üç giriş noktası yine ayrı ayrı sabitlenir:
  - toplu indirme: sporun bütün dilimleri, teniste point-by-point dahil
  - tek maç indirme: aynısı (eskiden teniste de point-by-point yoktu)
  - eksik dilim tamamlama (refill): yalnızca eksik ve "yok" sayılmayan dilimler, isteğe bağlılar dahil
Dilimler eşzamanlı istendiği için sıra karşılaştırılmaz.
"""
from typing import Iterator
from unittest.mock import MagicMock

import pytest

import detail_records
from characterization import pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sofascore_scraper.match_data_fetcher import DETAIL_SLICE_KEYS, REQUIRED_FILES, SLICE_EMPTY, MatchDataFetcher, SliceOutcome

MID = "4242"
EVENT = f"/event/{MID}"
COMMON = [
    f"{EVENT}/statistics",
    f"{EVENT}/team-streaks",
    f"{EVENT}/pregame-form",
    f"{EVENT}/h2h",
    f"{EVENT}/lineups",
    f"{EVENT}/incidents",
]
POINT_BY_POINT = f"{EVENT}/point-by-point"
COMMON_KEYS = ["statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents"]

# (etiket, tournament.category.sport nesnesi, toplu indirmede point-by-point istenir mi)
SPORT_CASES = [
    ("football", {"slug": "football", "name": "Football"}, False),
    ("basketball", {"slug": "basketball", "name": "Basketball"}, False),
    ("tennis", {"slug": "tennis", "name": "Tennis"}, True),
    ("tennis-name-only", {"name": "Tennis"}, True),
    ("unregistered-sport", {"slug": "handball", "name": "Handball"}, False),
    ("no-sport", None, False),
]
_IDS = [c[0] for c in SPORT_CASES]


def _basic(sport_obj) -> dict:
    category = {"name": "X"}
    if sport_obj is not None:
        category["sport"] = sport_obj
    return {
        "id": int(MID),
        "tournament": {"name": "Cup", "uniqueTournament": {"id": 77, "name": "Cup"}, "category": category},
        "season": {"id": 5, "name": "Cup 26", "year": "26"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": 1, "name": "A"},
        "awayTeam": {"id": 2, "name": "B"},
        "homeScore": {"current": 1},
        "awayScore": {"current": 0},
        "startTimestamp": 1790000000,
    }


def _fetcher(tmp_path) -> MatchDataFetcher:
    return MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    """/event/{MID} testin maçını, her dilim boş bir nesne döndürür."""
    world = FakeSofaScore()
    for path in COMMON + [POINT_BY_POINT]:
        world.add(path, {})
    with world:
        yield world


def _serve(fake: FakeSofaScore, basic: dict) -> None:
    fake.add(EVENT, {"event": basic})
    fake.reset_log()


def _calls(fake: FakeSofaScore) -> list:
    return sorted(r.path for r in fake.requests if r.path != SITE_ROOT)


def test_slice_constants_are_unchanged():
    assert DETAIL_SLICE_KEYS == tuple(COMMON_KEYS)
    assert REQUIRED_FILES == ["basic.json"] + [f"{k}.json" for k in COMMON_KEYS]


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_batch_download_requests_exactly_these_endpoints(tmp_path, fake, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    _serve(fake, _basic(sport_obj))
    data = f.fetch_matches_batch([MID])[MID]

    expected = [EVENT] + COMMON + ([POINT_BY_POINT] if point_by_point else [])
    assert _calls(fake) == sorted(expected)
    expected_keys = ["basic", "observation"] + COMMON_KEYS + (["point_by_point"] if point_by_point else [])
    assert list(data) == expected_keys  # tablo sırası


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_batch_download_counts_every_requested_slice_as_unavailable(tmp_path, fake, label, sport_obj,
                                                                     point_by_point):
    """Boş gelen her istenen dilim "yok" sayılır, point_by_point dahil; tamlık yalnızca altı ortak dilime bakar."""
    f = _fetcher(tmp_path)
    _serve(fake, _basic(sport_obj))
    f.fetch_matches_batch([MID])

    unavailable = detail_records.legacy_view(f.data_dir, int(MID))["_unavailable.json"]
    assert unavailable == {k: 1 for k in COMMON_KEYS + (["point_by_point"] if point_by_point else [])}
    assert f._expected_slice_keys(int(MID), None) == COMMON_KEYS
    assert f._compute_detail_need(MID) == "refill"


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_single_match_download_requests_exactly_these_endpoints(tmp_path, fake, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    _serve(fake, _basic(sport_obj))
    data = f.fetch_match_data(MID)

    extra = [POINT_BY_POINT] if point_by_point else []
    assert _calls(fake) == sorted([EVENT] + COMMON + extra)  # toplu indirmeyle aynı dilimler
    assert list(data) == ["basic", "observation"] + COMMON_KEYS + (["point_by_point"] if point_by_point else [])


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_refill_requests_every_missing_slice(tmp_path, fake, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    basic = _basic(sport_obj)
    f._save_match_data(MID, {"basic": basic})
    _serve(fake, basic)
    f.refill_missing_match_slices(MID)

    assert _calls(fake) == sorted([EVENT] + COMMON + ([POINT_BY_POINT] if point_by_point else []))


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_refill_skips_present_and_unavailable_slices(tmp_path, fake, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    basic = _basic(sport_obj)
    gone = SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
    # lineups ve incidents iki kez (artık beklenmez), statistics bir kez 404 almış
    f._save_match_data(MID, {"basic": basic, "h2h": {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}},
                             "lineups": None, "incidents": None, "statistics": None},
                       {"lineups": gone, "incidents": gone, "statistics": gone})
    f._save_match_data(MID, {"basic": basic, "lineups": None, "incidents": None},
                       {"lineups": gone, "incidents": gone})
    _serve(fake, basic)
    f.refill_missing_match_slices(MID)

    expected = [EVENT, f"{EVENT}/statistics", f"{EVENT}/team-streaks", f"{EVENT}/pregame-form"]
    assert _calls(fake) == sorted(expected + ([POINT_BY_POINT] if point_by_point else []))
