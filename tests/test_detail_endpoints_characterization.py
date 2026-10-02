"""
Karakterizasyon: bir maç için istenen detay uç noktaları (spor kayıt defteri öncesi davranış).

Beklenen listeler bu dosyada elle yazılıdır; kayıt defterinden (src/sports.py) türetilmez. Böylece
tablo değişirse hangi isteğin eklendiği ya da düştüğü burada görünür. Gerçek ağ yok: istek katmanı sahte.

Üç yol ayrı ayrı sabitlenir:
  - toplu (async) indirme: altı ortak dilim; teniste ayrıca point-by-point
  - tek maç (sync) indirme: altı ortak dilim; teniste de point-by-point YOK
  - eksik dilim tamamlama (refill): yalnızca eksik ve "yok" sayılmayan ortak dilimler
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

import detail_records
from src.match_data_fetcher import DETAIL_SLICE_KEYS, REQUIRED_FILES, SLICE_EMPTY, MatchDataFetcher, SliceOutcome

MID = "4242"
EVENT = f"https://www.sofascore.com/api/v1/event/{MID}"
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


def _fetch_async(f: MatchDataFetcher, basic: dict):
    calls = []

    async def fake(session, url, max_retries=None, **kw):
        calls.append(url)
        return {"event": basic} if url == EVENT else {}

    with patch("src.utils.make_api_request_async", new=fake):
        data = asyncio.run(f._fetch_match_data_async(object(), MID))
    return calls, data


def _sync_api(calls: list, basic: dict):
    def fake(url, *a, **kw):
        calls.append(url)
        return {"event": basic} if url == EVENT else {}

    return fake


def test_slice_constants_are_unchanged():
    assert DETAIL_SLICE_KEYS == tuple(COMMON_KEYS)
    assert REQUIRED_FILES == ["basic.json"] + [f"{k}.json" for k in COMMON_KEYS]


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_batch_download_requests_exactly_these_endpoints(tmp_path, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    calls, data = _fetch_async(f, _basic(sport_obj))

    expected = [EVENT] + COMMON + ([POINT_BY_POINT] if point_by_point else [])
    assert calls == expected  # sıra da aynı: tablo sırası istek sırasıdır
    expected_keys = ["basic", "observation"] + COMMON_KEYS + (["point_by_point"] if point_by_point else [])
    assert list(data) == expected_keys


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_batch_download_counts_only_the_six_tracked_slices_as_unavailable(tmp_path, label, sport_obj, point_by_point):
    """Boş gelen point_by_point "yok" sayımına girmez; tamlık yalnızca altı ortak dilime bakar."""
    f = _fetcher(tmp_path)
    _fetch_async(f, _basic(sport_obj))

    unavailable = detail_records.legacy_view(f.data_dir, int(MID))["_unavailable.json"]
    assert unavailable == {k: 1 for k in COMMON_KEYS}
    assert f._expected_slice_keys(int(MID), None) == COMMON_KEYS
    assert f._compute_detail_need(MID) == "refill"


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_single_match_download_requests_exactly_these_endpoints(tmp_path, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    calls = []
    with patch("src.match_data_fetcher.make_api_request", new=_sync_api(calls, _basic(sport_obj))):
        data = f.fetch_match_data(MID)

    assert calls == [EVENT] + COMMON  # teniste de point-by-point yok
    assert list(data) == ["basic", "observation"] + COMMON_KEYS


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_refill_requests_every_missing_tracked_slice(tmp_path, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    basic = _basic(sport_obj)
    f._save_match_data(MID, {"basic": basic})
    calls = []
    with patch("src.match_data_fetcher.make_api_request", new=_sync_api(calls, basic)):
        f.refill_missing_match_slices(MID)

    assert calls == [EVENT] + COMMON


@pytest.mark.parametrize("label,sport_obj,point_by_point", SPORT_CASES, ids=_IDS)
def test_refill_skips_present_and_unavailable_slices(tmp_path, label, sport_obj, point_by_point):
    f = _fetcher(tmp_path)
    basic = _basic(sport_obj)
    gone = SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
    # lineups ve incidents iki kez (artık beklenmez), statistics bir kez 404 almış
    f._save_match_data(MID, {"basic": basic, "h2h": {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}},
                             "lineups": None, "incidents": None, "statistics": None},
                       {"lineups": gone, "incidents": gone, "statistics": gone})
    f._save_match_data(MID, {"basic": basic, "lineups": None, "incidents": None},
                       {"lineups": gone, "incidents": gone})
    calls = []
    with patch("src.match_data_fetcher.make_api_request", new=_sync_api(calls, basic)):
        f.refill_missing_match_slices(MID)

    assert calls == [EVENT, f"{EVENT}/statistics", f"{EVENT}/team-streaks", f"{EVENT}/pregame-form"]
