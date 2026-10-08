"""Veri hattı doğruluğu: tur tazeliği, 'bitti' tanımı, eksik dilimler, sezon seçimi."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from catalog_index import LISTING_SCHEDULES, index_listings
from schedule_runner import inline, legacy_get
from detail_fetch import Details
from sofascore_scraper.services import listing
from sofascore_scraper.services.detail_phase import UNAVAILABLE_AFTER_ATTEMPTS
from sofascore_scraper.services.pipeline import is_finished
from sofascore_scraper.services.status import StatusService
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, Outcome, SliceOutcome
from sofascore_scraper.store import Ref, league_dir_name, open_store



def _event(mid, status_type="finished", desc="Ended", code=100):
    return {"id": mid, "status": {"type": status_type, "description": desc, "code": code}}


def _lister(tmp_path) -> listing.ScheduleLister:
    """Sezon programının kuralları (sofascore_scraper/services/listing.py); "yalnızca bitmiş maçlar" açık."""
    return listing.ScheduleLister(open_store(str(tmp_path)), only_finished=True, concurrency=2)


# --- tur önbelleği ---------------------------------------------------------------
# Tur sayfası Store'dadır (ST-22): önbellek kararı dilimin katalogdaki kaydından (meta.complete, fetched_at) gelir.

SEASON = 96668


def _store_round(tmp_path, data, *, complete, age_seconds=0):
    fetched = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    open_store(str(tmp_path)).entities.put(
        Ref.season(17, SEASON), {("schedule", "round_1"): Outcome(SLICE_OK, data, fetched_at=fetched,
                                                                  meta={"complete": complete})})


def _legacy_round_file(tmp_path, data):
    """Eski sürümün yazdığı tur dosyası (matches/<lig>/<sezon>/round_1.json)."""
    season_dir = tmp_path / "matches" / "17_Premier_League" / f"{SEASON}_Premier_League_26_27"
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / "round_1.json").write_text(json.dumps(data))
    index_listings(str(tmp_path), LISTING_SCHEDULES)  # testin kendi yazdığı eski dosya kataloğa girer


def test_complete_round_is_reused(tmp_path):
    f = _lister(tmp_path)
    _store_round(tmp_path, {"events": [_event(1)]}, complete=True, age_seconds=10 * 86400)
    assert f.cached_round(17, SEASON, "round_1") == {"events": [_event(1)]}


def test_incomplete_round_is_refetched_after_ttl(tmp_path):
    f = _lister(tmp_path)
    data = {"events": [_event(1), _event(2, "notstarted", "Not started", 0)]}
    _store_round(tmp_path, data, complete=False)
    assert f.cached_round(17, SEASON, "round_1") is not None
    _store_round(tmp_path, data, complete=False, age_seconds=listing.ROUND_CACHE_TTL_SECONDS + 60)
    assert f.cached_round(17, SEASON, "round_1") is None


def test_legacy_round_file_is_refetched_once(tmp_path):
    """Eski sürüm yalnız bitmiş maçları süzüp yazıyordu; _complete yok → yeniden çek."""
    f = _lister(tmp_path)
    _legacy_round_file(tmp_path, {"events": [_event(1)]})
    assert f.cached_round(17, SEASON, "round_1") is None


def test_complete_legacy_round_file_is_reused(tmp_path):
    """`_complete` taşıyan eski tur dosyası da önbellektir (yükü `_complete` anahtarı olmadan gelir)."""
    f = _lister(tmp_path)
    _legacy_round_file(tmp_path, {"events": [_event(1)], "_complete": True})
    assert f.cached_round(17, SEASON, "round_1") == {"events": [_event(1)]}


def test_round_saves_raw_payload_and_returns_only_finished(tmp_path):
    f = _lister(tmp_path)
    payload = {"events": [_event(1), _event(2, "inprogress", "1st half", 6)]}
    get = legacy_get(AsyncMock(return_value=payload))
    result = asyncio.run(f.round(asyncio.Semaphore(2), 17, SEASON, 1, None, get, inline, listing.ScheduleRun()))
    store = open_store(str(tmp_path))
    saved = store.entities.payload(Ref.season(17, SEASON), "schedule", "round_1")
    assert [e["id"] for e in saved["events"]] == [1, 2] and "_complete" not in saved
    assert store.entities.slice(Ref.season(17, SEASON), "schedule", "round_1").meta == {"complete": False}
    assert not (tmp_path / "matches" / "17_Premier_League").exists()
    assert [e["id"] for e in result["events"]] == [1]
    assert result["round"] == 1


@pytest.mark.parametrize("status", [
    {"type": "finished", "description": "AET", "code": 110},
    {"type": "finished", "description": "AP", "code": 120},
    {"type": "finished", "description": "Ended", "code": 100},
])
def test_finished_includes_extra_time_and_penalties(status):
    assert is_finished({"status": status})


# --- maç detayları ------------------------------------------------------------------

def _detail_fetcher(tmp_path) -> Details:
    return Details(tmp_path)


def _basic(mid=42, sport="tennis", desc="Ended"):
    return {
        "id": mid,
        "status": {"type": "finished", "description": desc, "code": 100},
        "tournament": {"uniqueTournament": {"id": 2361, "name": "Wimbledon, Men"},
                       "category": {"sport": {"slug": sport}}},
        "season": {"id": 1, "name": "Wimbledon Men Singles 2025"},
    }


# FX-16: teniste point_by_point tamlığa girer (kadro ve olaylar istenmez, pregame_form isteğe bağlı)
_EMPTY_SLICES = ("lineups", "incidents", "team_streaks", "pregame_form", "h2h", "point_by_point")


def _partial_match():
    return {"basic": _basic(), "statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"x": 1}]}]}]},
            **{key: None for key in _EMPTY_SLICES}}


def test_slice_confirmed_empty_twice_is_no_longer_expected(tmp_path):
    """İki kesin "yok" yanıtı (404 ya da içinde veri olmayan 200) dilimi o maç için beklenmez yapar."""
    f = _detail_fetcher(tmp_path)
    data = _partial_match()
    confirmed = {key: SliceOutcome(SLICE_EMPTY, reason="404", http_status=404) for key in _EMPTY_SLICES}
    f.save("42", data, confirmed)
    assert f.need("42") == "refill"
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS - 1):
        f.save("42", data, confirmed)
    assert f.need("42") == "none"


@pytest.mark.parametrize("outcomes", [
    None,  # sonucu bilinmeyen boş dilim: istenip istenmediği belli değil
    {key: SliceOutcome(SLICE_FAILED, reason="429", http_status=429) for key in _EMPTY_SLICES},
], ids=["unknown", "failed"])
def test_slice_missing_without_a_definitive_answer_stays_expected(tmp_path, outcomes):
    """Eski kural her boş dilimi sayıyordu; başarısız istek kaç kez olursa olsun "yok" sayılmaz."""
    f = _detail_fetcher(tmp_path)
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS + 3):
        f.save("42", _partial_match(), outcomes)
    assert f.need("42") == "refill"


def test_sync_fetch_accepts_aet(tmp_path):
    f = _detail_fetcher(tmp_path)
    from fakes.sofascore import FakeSofaScore

    fake = FakeSofaScore()  # P13: tek maç da boru hattından; dilimler 404
    fake.add_event(_basic(sport="football", desc="AET"))
    with fake:
        assert f.fetch(42) is not None


# --- kapsam raporu (P15: katalogdan, dosya yazılmaz) -------------------------------------------------

def _files(root) -> set:
    return {os.path.relpath(os.path.join(d, n), root) for d, dirs, names in os.walk(root)
            for n in names if ".meta" not in d.split(os.sep)}


def test_coverage_report_counts_the_slices_the_writer_marked_and_writes_nothing(tmp_path, capsys):
    """Terminal menüsünün dosya raporu (FX-15'te kalktı) yerine: kapsam raporu katalogdan gelir, dosya yazılmaz."""
    f = _detail_fetcher(tmp_path)
    f.save("42", _partial_match(), {key: SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
                                                 for key in _EMPTY_SLICES})
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS):  # istatistik yeterince kez kesin "yok": artık eksik sayılmaz
        f.save("43", {**_partial_match(), "basic": _basic(43), "statistics": None},
                           {"statistics": SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)})
    capsys.readouterr()
    before = _files(tmp_path)

    report = StatusService(open_store(str(tmp_path))).coverage()
    assert capsys.readouterr().out == ""
    assert _files(tmp_path) == before
    # 42: bitmiş maçta tek kesin "yok" tamlık için yeter (FX-23; planlayıcı yine bir kez sorar); 43: istatistik
    # beklenmez; teniste tamlığa giren öteki üç dilim eksik (FX-16)
    missing = {key: 1 for key in ("team_streaks", "h2h", "point_by_point")}
    assert (report.matches, report.complete, report.completion_rate) == (2, 1, 50.0)
    assert dict(report.missing) == missing
    (tournament,) = report.tournaments
    assert (tournament.tournament_id, tournament.matches, tournament.complete) == (2361, 2, 1)
    assert [season.season_id for season in tournament.seasons] == [1]
    assert dict(tournament.seasons[0].missing) == dict(tournament.missing) == missing


def test_path_helpers_match_existing_layout():
    """2.x düzeninin lig dizini adı (Store'un ad kuralı; eskiden sofascore_scraper/paths.py)."""
    assert league_dir_name(17, "Premier League") == "17_Premier_League"
    assert league_dir_name(2361, "Wimbledon, Men") == "2361_Wimbledon,_Men"
    # Config'de olmayan lig: kaydeden ve okuyan aynı adı kullanır
    assert league_dir_name(5, None) == "5_League_5"
