"""
Detay yazıcısının karakterizasyonu: indirme yollarının bıraktığı mantıksal döküm (plan maddesi ST-21).

Dört yol sahte SofaScore ile sırayla çalıştırılır ve her adımdan sonra veri dizininin mantıksal dökümü
(tests/store_dump.py: maç başına gözlem, dilim durumları, sayaçlar, hata kayıtları, yük özetleri ve değişiklik
günlüğü) alınır:

  1. paralel toplu indirme (`fetch_detail_ids`, asyncio yolu): yeni maçlar, başarısız ve boş dilimler, tenis
     (zorunlu olmayan `point_by_point` dilimi), turnuvası olmayan maç, "yalnızca bitmiş maçlar" kapalıyken
     oynanan maç;
  2. tek maç (`fetch_match_data`, sıralı yol);
  3. eksik dilimlerin tamamlanması (`fetch_matches_batch` ve paralel yol, `refill`);
  4. yenileme (`refresh_due_ids` + `refresh_matches`): skoru düzeltilen, iptal edilen (status_regressed) ve
     değişmeyen maç;
  5. "yok" işaretlerinin yeniden denetimi (`reset_unavailable_markers`).

Golden (fixtures/fetch/detail_writer.golden.json) eski düzen yazıcısıyla kaydedildi; Store'a geçen yazıcı aynı
dökümü vermelidir (v3'teki kopya eski düzendekinin önündedir: `store_dump.dump`). Dosya yolları, dosya zamanları
ve gözlem anı dökümde yoktur; değişiklik satırının yazılma anı (`ts_utc`) maskelenir. İstekler de sabitlenir:
ihtiyaç hesabı (hangi maçın tam, kısmi ya da yenilenecek sayıldığı) aynı kalmalıdır.

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider. Yeniden üretmek: UPDATE_GOLDENS=1.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest

import src.utils as utils
import store_dump
from characterization import WORLD, assert_golden, pin_default_settings
from fakes.sofascore import FakeSofaScore
from src.match_data_fetcher import MatchDataFetcher

LEAGUE = 17
NO_TOURNAMENT = 9400001  # turnuvası (uniqueTournament) olmayan maç: dünyada yok, test ekler
LIVE = 9300001  # oynanıyor
NOT_STARTED = 9100004
TENNIS = 9200001
VOLATILE = "<volatile>"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


def _fetcher(data_dir: Path) -> MatchDataFetcher:
    from src.web.routes.common import config_manager

    return MatchDataFetcher(config_manager, data_dir=str(data_dir))


def _normal(dump: Dict[str, Any]) -> Dict[str, Any]:
    """Döküm, çalıştırma anına bağlı alanları maskelenmiş olarak: gözlem anı ve değişiklik satırının zamanı."""
    out = copy.deepcopy(dump)
    for event in out["events"].values():
        observation = event.get("observation")
        if observation is not None and observation.get("observed_at_utc") is not None:
            observation["observed_at_utc"] = VOLATILE
    for change in out["changes"]:
        if "ts_utc" in change["row"]:
            change["row"]["ts_utc"] = VOLATILE
    return out


def _add_no_tournament_event(fake: FakeSofaScore) -> None:
    """Bitmiş bir futbol maçı, turnuvasının `uniqueTournament`'ı yok (eski yazıcı `_no_tournament/` altına yazar)."""
    event = fake.event(9100001)
    event["id"] = NO_TOURNAMENT
    event["slug"] = "friendly-match"
    event["tournament"] = {"name": "Club Friendlies", "slug": "club-friendlies", "id": 4444}
    event.pop("season", None)
    slices = {key: fake.routes[f"/event/9100001/{path}"] for key, path in (
        ("statistics", "statistics"), ("team_streaks", "team-streaks"), ("h2h", "h2h"),
        ("lineups", "lineups"), ("incidents", "incidents"))}
    fake.add_event(event, slices)


def _change_score(fake: FakeSofaScore, event_id: int, home: int) -> None:
    event = fake.event(event_id)
    event["homeScore"].update(current=home, display=home, normaltime=home)
    event["changes"]["changeTimestamp"] += 3600
    fake.add_event(event)


def _cancel(fake: FakeSofaScore, event_id: int) -> None:
    event = fake.event(event_id)
    event["status"] = {"code": 70, "description": "Canceled", "type": "canceled"}
    event["changes"]["changeTimestamp"] += 7200
    fake.add_event(event)


def test_detail_writers_leave_the_recorded_logical_dump(
        fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    md = _fetcher(data_dir)
    _add_no_tournament_event(fake)
    steps: Dict[str, Any] = {}

    def record(name: str, result: Any) -> None:
        steps[name] = {"result": result, "requests": fake.canonical_log(),
                       "dump": _normal(store_dump.dump(data_dir))}
        fake.reset_log()

    # 1. paralel yol: 9100003'ün istatistiği ve 9100010'un kadrosu başarısız (500, 429), 9100002'de iki dilim
    #    boş (pregame-form 404, kadro boş gövde), tenis maçında zorunlu olmayan dilim de istenir
    fake.fail("/event/9100003/statistics", 500)
    fake.fail("/event/9100010/lineups", 429)
    done = md.fetch_detail_ids([9100001, 9100002, 9100003, NOT_STARTED, 9100010, TENNIS, NO_TOURNAMENT])
    record("async_batch", {"success": done})

    # 1b. "yalnızca bitmiş maçlar" kapalı: oynanan maç da yazılır (işaretsiz)
    fake.clear_faults()
    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", False)
    done = md.fetch_detail_ids([LIVE])
    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", True)
    record("async_unfinished", {"success": done})

    # 2. sıralı tek maç: kayıtlı bir maçın üzerine ve oynanan maç (yazılmaz)
    fetched = {str(event_id): md.fetch_match_data(event_id) is not None for event_id in (9100001, LIVE)}
    record("single_fetch", fetched)

    # 3. eksik dilimler: sıralı yol (9100003 istatistik, 9100010 kadro, 9100002 ikinci kez boş), sonra paralel
    #    yol 9100002 için üçüncü kez (iki boş yanıttan sonra beklenmez: istek yok)
    results = md.fetch_matches_batch([9100003, 9100010, 9100002])
    record("refill_sequential", sorted(results))
    done = md.fetch_detail_ids([9100002, 9100003])
    record("refill_async", {"success": done})

    # 4. yenileme: pencere açık, alt sınır kapalı; 9100010 skor düzeltmesi, 9100003 iptal, 9100001 aynı
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "10000000")
    monkeypatch.setenv("REFRESH_MIN_INTERVAL_HOURS", "0")
    _change_score(fake, 9100010, home=3)
    _cancel(fake, 9100003)
    md.begin_job_cache()
    try:
        due = md.refresh_due_ids(league_id=LEAGUE)
        stats = md.refresh_matches([mid for mid in due if mid in ("9100001", "9100003", "9100010")])
    finally:
        md.end_job_cache()
    record("refresh", {"due": due, "stats": stats})
    monkeypatch.delenv("REFRESH_WINDOW_HOURS")
    monkeypatch.delenv("REFRESH_MIN_INTERVAL_HOURS")

    # 5. işaretlerin yeniden denetimi: varsayılan (doğrulanmış sayımlar kalır), sonra hepsi
    record("reset_default", md.reset_unavailable_markers(league_id=LEAGUE))
    record("reset_all", md.reset_unavailable_markers(league_id=LEAGUE, include_confirmed=True))

    assert_golden("detail_writer", {"steps": steps})
