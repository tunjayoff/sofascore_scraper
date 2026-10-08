"""
Karakterizasyon: tests/fixtures/status altındaki her gerçek yanıt için extract_scores ve izleyici kararları.

Beklenen değerler tests/fixtures/status_golden.json'da durur; spor kayıt defterine (sofascore_scraper/sports.py) geçmeden
önceki kodla üretildi. Yeni spor eklemek ya da kayıt defterini değiştirmek mevcut üç sporun çıktısını
değiştirmemeli. Davranış bilerek değiştirildiyse dosya şöyle yeniden üretilir:

    REGEN_STATUS_GOLDEN=1 python -m pytest tests/test_status_golden.py

Fixture başına kayıt:
  - scores: extract_scores sonucu (sınıf adı + alanlar)
  - observed: yanıtın alındığı anda canlı indirgeyicinin durumu (sınıf, bitti mi, bitişe yakın mı, takılı mı, oyun
    başlangıcı)
  - stuck_5h: başlangıçtan 5 saat sonra takılı sayılır mı (4 sa ve 6 sa eşiklerini ayırır)
"""
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path

import pytest

from sofascore_scraper.sports import sport_slugs
from sofascore_scraper.services.live.reducer import Observation, reduce
from sofascore_scraper.status import extract_scores

FIXTURES = Path(__file__).parent / "fixtures" / "status"
GOLDEN = Path(__file__).parent / "fixtures" / "status_golden.json"
PATHS = sorted(FIXTURES.glob("*/*.json"))


def _key(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def _observe_at(event: dict, sport: str, now: float, data_dir: str) -> dict:
    """Canlı indirgeyicinin ilk gözlemden sonraki durumu (2.x izleyicisinin `_observe`u bu indirgeyiciydi; P30)."""
    state, _events = reduce(None, Observation(event=event, via="live", at=now), sport)
    return dict(state)


def _snapshot(path: Path, data_dir: str) -> dict:
    sport = path.parent.name
    event = json.loads(path.read_text(encoding="utf-8"))
    event["id"] = event["event_id"]
    sheet = extract_scores(event, sport)
    scores = dataclasses.asdict(sheet)
    scores["status_class"] = sheet.status_class.value
    fetched_at = dt.datetime.fromisoformat(event["fetched_at_utc"]).timestamp()
    state = _observe_at(event, sport, fetched_at, data_dir)
    snap = {
        "scores": {"class": type(sheet).__name__, "settleable": sheet.settleable, **scores},
        "observed": {k: state.get(k) for k in ("class", "done", "near_end", "stuck", "play_start")},
    }
    start = event.get("startTimestamp")
    if isinstance(start, (int, float)):
        snap["stuck_5h"] = _observe_at(event, sport, start + 5 * 3600, data_dir)["stuck"]
    return json.loads(json.dumps(snap))  # Pair → liste, sayısal anahtar → metin: dosyadakiyle aynı biçim


@pytest.fixture(scope="module")
def golden(tmp_path_factory) -> dict:
    if os.getenv("REGEN_STATUS_GOLDEN") == "1":
        data_dir = str(tmp_path_factory.mktemp("regen"))
        data = {_key(p): _snapshot(p, data_dir) for p in PATHS}
        # Fixture başına bir satır: fark okunur kalsın
        lines = [f" {json.dumps(k)}: {json.dumps(data[k], ensure_ascii=False, sort_keys=True)}" for k in sorted(data)]
        GOLDEN.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_covers_every_fixture(golden):
    assert sorted(golden) == sorted(_key(p) for p in PATHS)
    assert {k.split("/")[0] for k in golden} == set(sport_slugs())


@pytest.mark.parametrize("path", PATHS, ids=_key)
def test_fixture_matches_golden(path, golden, tmp_path):
    assert _snapshot(path, str(tmp_path)) == golden[_key(path)]
