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


def test_a_resume_right_after_a_cancel_does_not_ask_the_stored_matches_again(
        fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bulgu F29: durdurup hemen sürdürmek, saniyeler önce saklanan maçları yeniden istiyordu. Sebep, "veri yok"
    yanıtı alan dilimlerin (9100002: iki dilim) doğrulanmak için hemen yeniden istenmesiydi. Varsayılan bekleme
    (`fetch.confirm_empty_after_seconds`, 60 sn) içinde sürdürülen iş yalnızca kalan maçları ister; bekleme
    geçince (burada 0) doğrulama yapılır ve dilimler artık beklenmez.
    """
    monkeypatch.delenv("SOFASCORE_FETCH__CONFIRM_EMPTY_AFTER_SECONDS", raising=False)  # varsayılan bekleme
    done: List[int] = []
    first = _details(tmp_path)
    first.fetch([str(mid) for mid in IDS], progress=lambda n, _t, _m: done.append(n),
                cancelled=lambda: bool(done) and done[-1] >= 3)
    stored = [mid for mid in IDS if f"/event/{mid}" in _events(fake)]
    assert stored == IDS[:3]

    fake.reset_log()
    resumed = _details(tmp_path)
    assert resumed.pending([str(mid) for mid in IDS]) == [str(mid) for mid in IDS[3:]]
    resumed.fetch(resumed.pending([str(mid) for mid in IDS]))
    asked = {path.split("/")[2] for path in fake.paths() if path.startswith("/event/")}
    assert asked == {str(mid) for mid in IDS[3:]}

    # Bekleme geçti: 9100002'nin "veri yok" yanıtları bir kez doğrulanır, sonra hiçbir şey istenmez
    monkeypatch.setenv("SOFASCORE_FETCH__CONFIRM_EMPTY_AFTER_SECONDS", "0")
    later = _details(tmp_path)
    assert "9100002" in later.pending([str(mid) for mid in IDS])
    later.fetch(later.pending([str(mid) for mid in IDS]))
    assert _details(tmp_path).pending([str(mid) for mid in IDS]) == []
