"""
Durdurma, maç detayı indirmesini maçların arasında keser (P13'ten beri tek yol: sofascore_scraper/services/pipeline.py).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı ve Store gerçektir.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, List
from unittest.mock import MagicMock

import pytest

from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sofascore_scraper.services.detail_phase import DetailPhase
from sofascore_scraper.store import open_store

IDS = [9100001, 9100002, 9100003, 9100010, 9200001]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


def _details(tmp_path: Path) -> DetailPhase:
    cfg = MagicMock()
    cfg.get_max_concurrent.return_value = 1  # maçlar sırayla: durdurmanın yeri belirli
    cfg.get_rate_limit_threshold_consecutive.return_value = 100
    cfg.get_rate_limit_threshold_ratio.return_value = 2.0
    cfg.get_server_error_threshold_consecutive.return_value = 100
    return DetailPhase(open_store(str(tmp_path / "data")), cfg)


def _events(fake: FakeSofaScore) -> List[str]:
    return [r.path for r in fake.requests if r.path != SITE_ROOT and r.path.count("/") == 2]


def test_the_selected_matches_honor_cancel(fake: FakeSofaScore, tmp_path: Path) -> None:
    details = _details(tmp_path)
    done: List[str] = []
    stop_after = 3

    stored = details.fetch_selected(
        IDS, progress=lambda n, _t, _m: n and done.append(str(n)), cancelled=lambda: len(done) >= stop_after)

    assert stored == stop_after
    assert _events(fake) == [f"/event/{mid}" for mid in IDS[:stop_after]]  # sonraki maçlar için istek yok


def test_a_league_plan_honors_cancel_in_one_session(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Eski 100'lük dış batch döngüsü yok: bir çalıştırma tek oturumdur ve durdurma sonraki maçı başlatmaz."""
    details = _details(tmp_path)
    failed: List[str] = []

    ok = details.fetch([str(mid) for mid in IDS], cancelled=lambda: bool(_events(fake)), failed=failed.append)

    assert ok == 1 and failed == []
    assert _events(fake) == [f"/event/{IDS[0]}"] and len(fake.sessions) == 1
