"""
classify_status: tests/fixtures/status altındaki gerçek SofaScore yanıtları.

Beklenen sınıf dosya adından değil, docs/status-matrix/README.md "Durum evreni" tablosundaki
(type, code) → sınıf eşlemesinden gelir.
"""
import copy
import json
from pathlib import Path

import pytest

from sofascore_scraper.match_fetcher import MatchFetcher
from sofascore_scraper.status import StatusClass, classify_status, is_played

FIXTURES = Path(__file__).parent / "fixtures" / "status"
ALL = sorted(FIXTURES.glob("*/*.json"))

# README "Durum evreni": görülen her (type, code) üçlüsü
EXPECTED = {
    ("notstarted", 0): StatusClass.NOT_STARTED,
    ("inprogress", 6): StatusClass.LIVE,  # 1st half
    ("inprogress", 7): StatusClass.LIVE,  # 2nd half
    ("inprogress", 8): StatusClass.LIVE,  # 1st set
    ("inprogress", 9): StatusClass.LIVE,  # 2nd set
    ("inprogress", 10): StatusClass.LIVE,  # 3rd set
    ("inprogress", 11): StatusClass.LIVE,  # 4th set (masa tenisi; docs/all-sports/README.md, SP-2)
    ("inprogress", 12): StatusClass.LIVE,  # 5th set
    ("inprogress", 13): StatusClass.LIVE,  # 1st quarter
    ("inprogress", 14): StatusClass.LIVE,
    ("inprogress", 15): StatusClass.LIVE,
    ("inprogress", 16): StatusClass.LIVE,  # 4th quarter
    ("inprogress", 20): StatusClass.LIVE,  # Started
    ("inprogress", 21): StatusClass.LIVE,  # 1st Inning (kriket; docs/all-sports/README.md, SP-3)
    ("inprogress", 28): StatusClass.LIVE,  # 8th Inning (beyzbol)
    ("inprogress", 29): StatusClass.LIVE,  # 9th Inning (beyzbol)
    ("inprogress", 30): StatusClass.LIVE,  # Pause
    ("inprogress", 31): StatusClass.LIVE,  # Halftime
    ("inprogress", 1001): StatusClass.LIVE,  # First game (e-spor)
    ("inprogress", 1002): StatusClass.LIVE,  # Second game (e-spor)
    ("willcontinue", 141): StatusClass.LIVE,  # End of day 1 (kriket; SP-3 kararı: gün arası canlıdır)
    ("finished", 100): StatusClass.COMPLETED,  # Ended
    ("finished", 110): StatusClass.COMPLETED,  # AET
    ("finished", 120): StatusClass.COMPLETED,  # AP
    ("finished", 91): StatusClass.DECIDED_WITHOUT_PLAY,  # Walkover
    ("finished", 92): StatusClass.DECIDED_WITHOUT_PLAY,  # Retired
    ("postponed", 60): StatusClass.VOID,
    ("canceled", 70): StatusClass.VOID,
    ("interrupted", 80): StatusClass.VOID,
    ("suspended", 81): StatusClass.VOID,
    ("canceled", 90): StatusClass.VOID,  # Abandoned
}


def _load(rel: str) -> dict:
    return json.loads((FIXTURES / f"{rel}.json").read_text(encoding="utf-8"))


def _legacy_is_finished(event: dict) -> bool:
    """_is_finished_event'in bu PR'dan önceki hâli (geriye uyumluluk karşılaştırması için)."""
    status = event.get("status") or {}
    if status.get("type") == "finished":
        return True
    if status.get("code") == 100:
        return True
    desc = str(status.get("description") or "").lower()
    return desc in ("ended", "aet", "after extra time", "ap", "penalties")


def test_fixture_set_is_not_empty():
    assert len(ALL) >= 231  # 203 + SP-3'ün 28 örneği


@pytest.mark.parametrize("path", ALL, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_fixture_matches_status_universe(path):
    event = json.loads(path.read_text(encoding="utf-8"))
    key = (event["status"]["type"], event["status"]["code"])
    assert key in EXPECTED, f"README'de olmayan üçlü: {event['status']}"
    got = classify_status(event)
    assert got is EXPECTED[key]
    assert got is not StatusClass.UNKNOWN


@pytest.mark.parametrize("path", ALL, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_is_finished_event_unchanged(path):
    event = json.loads(path.read_text(encoding="utf-8"))
    assert MatchFetcher._is_finished_event(event) == _legacy_is_finished(event)


@pytest.mark.parametrize("rel,expected", [
    ("football/F1_aet__17148332", StatusClass.COMPLETED),
    ("football/F1_aet__17155990", StatusClass.COMPLETED),
    ("football/F2_penalties__16950622", StatusClass.COMPLETED),
    ("football/F2_penalties__17090707", StatusClass.COMPLETED),
    ("basketball/K1_overtime_finished__16346148", StatusClass.COMPLETED),
    ("basketball/K1_overtime_finished__17066086", StatusClass.COMPLETED),
    ("basketball/B11_walkover_awarded__17102381", StatusClass.DECIDED_WITHOUT_PLAY),
    ("tennis/B11_walkover_awarded__17058663", StatusClass.DECIDED_WITHOUT_PLAY),
    ("tennis/T2_retired__17081861", StatusClass.DECIDED_WITHOUT_PLAY),
    ("tennis/T2_retired__17083330", StatusClass.DECIDED_WITHOUT_PLAY),
    ("tennis/T3_walkover__17058663", StatusClass.DECIDED_WITHOUT_PLAY),
    ("basketball/B7_postponed__16623552", StatusClass.VOID),
    ("football/B7_postponed__16539815", StatusClass.VOID),
    ("basketball/B8_canceled__17100305", StatusClass.VOID),
    ("tennis/B8_canceled__17081854", StatusClass.VOID),
    ("football/B9_interrupted__17148292", StatusClass.VOID),
    ("tennis/B9_suspended__17208583", StatusClass.VOID),
    ("football/A_inprogress-20-started__16982821", StatusClass.LIVE),
    ("tennis/A_inprogress-20-started__17207832", StatusClass.LIVE),
])
def test_named_edge_cases(rel, expected):
    assert classify_status(_load(rel)) is expected


def test_abandoned_with_full_score_is_void():
    """Basketbol Abandoned: canceled/90, skor 59-57 dolu; yine de sonuçlanmaz."""
    event = _load("basketball/B9_abandoned__17060394")
    assert event["homeScore"]["current"] == 59 and event["awayScore"]["current"] == 57
    assert classify_status(event) is StatusClass.VOID
    assert not is_played(event)
    assert not MatchFetcher._is_finished_event(event)


def test_walkover_and_retired_are_finished_but_not_played():
    for rel in ("tennis/T2_retired__17081861", "tennis/T3_walkover__17058663"):
        event = _load(rel)
        assert MatchFetcher._is_finished_event(event)  # FETCH_ONLY_FINISHED anlamı değişmedi
        assert not is_played(event)


def test_is_played_only_for_completed():
    assert is_played(_load("football/F2_penalties__16950622"))
    assert not is_played(_load("football/A_inprogress-20-started__16982821"))


@pytest.mark.parametrize("rel", [
    "football/F1_aet__17148332",
    "tennis/T2_retired__17081861",
    "basketball/B9_abandoned__17060394",
    "basketball/A_inprogress-30-pause__17157547",
])
def test_code_alone_when_type_missing(rel):
    event = copy.deepcopy(_load(rel))
    expected = classify_status(event)
    del event["status"]["type"]
    assert classify_status(event) is expected


@pytest.mark.parametrize("rel", [
    "football/A_finished-120-ap__16950622",
    "tennis/A_finished-91-walkover__17058663",
    "tennis/A_suspended-81-suspended__17208583",
    "football/A_notstarted-0-not-started__17184998",
])
def test_description_only_when_type_and_code_missing(rel):
    event = copy.deepcopy(_load(rel))
    expected = classify_status(event)
    del event["status"]["type"], event["status"]["code"]
    assert classify_status(event) is expected


# --- kriketin gün sonu ve B sınıfı sporların kodları (plan maddesi SP-3) ------------------------

def test_cricket_end_of_day_is_live_and_not_finished():
    """
    willcontinue / 141 "End of day 1": çok günlü maç ertesi gün sürer. Kapalı `status.class` sayımına yeni değer
    eklemek yerine LIVE (devre arası 31 gibi); maç bitmiş sayılmaz, FETCH_ONLY_FINISHED anlamı değişmez.
    """
    event = _load("cricket/A_willcontinue-141-end-of-day-1__16586046")
    assert classify_status(event) is StatusClass.LIVE
    assert not is_played(event)
    assert not MatchFetcher._is_finished_event(event)
    del event["status"]["type"]  # kod tek başına da canlı
    assert classify_status(event) is StatusClass.LIVE


@pytest.mark.parametrize("code", [21, 28, 29, 141, 1001, 1002])
def test_new_live_codes_are_live_without_a_type(code, caplog):
    with caplog.at_level("WARNING", logger="sofascore_scraper.status"):
        assert classify_status({"status": {"code": code}}) is StatusClass.LIVE
    assert caplog.text == ""


LIVE_2026_10_08 = json.loads((Path(__file__).parent / "fixtures" / "live_validation" / "live-statuses-2026-10-08.json")
                             .read_text(encoding="utf-8"))["statuses"]


def test_the_live_validation_saw_every_live_sport():
    """FX-27 V4: canlı doğrulamada (2026-10-08) canlı listelerde görülen 13 spor ve 34 durum."""
    assert len({row["sport"] for row in LIVE_2026_10_08}) == 13 and len(LIVE_2026_10_08) == 34


@pytest.mark.parametrize("row", LIVE_2026_10_08, ids=lambda r: f"{r['sport']}-{r['code']}")
def test_every_live_status_seen_on_2026_10_08_is_live_with_and_without_its_type(row, caplog):
    """
    FX-27 V4: görülen her (spor, kod, açıklama, tür) canlıdır; tür düşmüş yükte de (push karesi yalnızca kodu
    taşıyabilir) kod tablosundan canlı çıkar ve "Status could not be classified" uyarısı yazılmaz.
    """
    status = {"code": row["code"], "description": row["description"], "type": row["type"]}
    with caplog.at_level("WARNING", logger="sofascore_scraper.status"):
        assert classify_status({"status": status}) is StatusClass.LIVE
        assert classify_status({"status": {"code": row["code"], "description": row["description"]}}) \
            is StatusClass.LIVE
        assert classify_status({"status": {"code": row["code"]}}) is StatusClass.LIVE
    assert caplog.text == ""


@pytest.mark.parametrize("code", [1, 2, 3, 22, 42, 58, 1003, 1004, 1005])
def test_codes_added_after_the_live_validation_are_live_without_a_type(code):
    assert classify_status({"status": {"code": code}}) is StatusClass.LIVE


def test_a_push_probe_of_an_unknown_code_logs_nothing(caplog):
    """`classify_code` push karesinin kodunu yoklar: bilinmeyen kod UNKNOWN döner ama uyarı yazılmaz."""
    from sofascore_scraper.status import classify_code

    with caplog.at_level("WARNING", logger="sofascore_scraper.status"):
        assert classify_code(555) is StatusClass.UNKNOWN
        assert classify_code(None) is StatusClass.UNKNOWN and classify_code(True) is StatusClass.UNKNOWN
        assert classify_code(2) is StatusClass.LIVE and classify_code(100) is StatusClass.COMPLETED
    assert caplog.text == ""


def test_unseen_inning_codes_stay_unknown_without_a_type():
    """22-27 (2.-7. inning) görülmedi: tipi olmayan yükte bilinmez kalır; tipiyle gelen yük zaten LIVE'dır."""
    assert classify_status({"status": {"code": 25}}) is StatusClass.UNKNOWN
    assert classify_status({"status": {"type": "inprogress", "code": 25}}) is StatusClass.LIVE


# --- UNKNOWN (tek elle yazılmış girdiler) ------------------------------------------

@pytest.mark.parametrize("event", [
    {"status": {"type": "finished", "code": 999}},
    {"status": {"type": "somethingnew", "code": 100}},
    {"status": {"code": 555}},
    {"status": {"description": "Whatever"}},
    {"status": None},
    {"status": {}},
    {},
    None,
])
def test_unknown(event, caplog):
    assert classify_status(event) is StatusClass.UNKNOWN
    assert not is_played(event)


def test_unknown_finished_code_is_logged(caplog):
    with caplog.at_level("WARNING", logger="sofascore_scraper.status"):
        classify_status({"id": 1, "status": {"type": "finished", "code": 999}})
    assert "999" in caplog.text
