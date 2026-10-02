"""
Durdurma, maç detayı indirmesini maçların arasında keser (P13'ten beri tek yol: src/services/pipeline.py).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı ve Store gerçektir.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, List
from unittest.mock import MagicMock

import pytest

from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from src.match_data_fetcher import MatchDataFetcher

IDS = [9100001, 9100002, 9100003, 9100010, 9200001]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


def _fetcher(tmp_path: Path) -> MatchDataFetcher:
    cfg = MagicMock()
    cfg.get_max_concurrent.return_value = 1  # maçlar sırayla: durdurmanın yeri belirli
    cfg.get_rate_limit_threshold_consecutive.return_value = 100
    cfg.get_rate_limit_threshold_ratio.return_value = 2.0
    cfg.get_server_error_threshold_consecutive.return_value = 100
    return MatchDataFetcher(config_manager=cfg, data_dir=str(tmp_path / "data"))


def _events(fake: FakeSofaScore) -> List[str]:
    return [r.path for r in fake.requests if r.path != SITE_ROOT and r.path.count("/") == 2]


def test_fetch_matches_batch_honors_should_cancel(fake: FakeSofaScore, tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path)
    done: List[str] = []
    stop_after = 3

    results = fetcher.fetch_matches_batch(
        IDS, progress_callback=lambda n, _t, _m: n and done.append(str(n)), should_cancel=lambda: len(done) >= stop_after)

    assert len(results) == stop_after
    assert _events(fake) == [f"/event/{mid}" for mid in IDS[:stop_after]]  # sonraki maçlar için istek yok


def test_fetch_detail_ids_honors_should_cancel_in_one_session(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Eski 100'lük dış batch döngüsü yok: bir çalıştırma tek oturumdur ve durdurma sonraki maçı başlatmaz."""
    fetcher = _fetcher(tmp_path)
    failed: List[str] = []

    ok = fetcher.fetch_detail_ids([str(mid) for mid in IDS], should_cancel=lambda: bool(_events(fake)),
                                  failed_callback=failed.append)

    assert ok == 1 and failed == []
    assert _events(fake) == [f"/event/{IDS[0]}"] and len(fake.sessions) == 1
