"""
Servis bağlamı: 2.x'in indiricileri (P15'ten beri bağlamın eski adlı yüzleri) 3.1'de kalktı (plan maddesi P30).

Bağlam yalnızca yapılandırmayı, veri dizinini ve istemciyi taşır; detay aşaması her iş için ayrı kurulur
(sofascore_scraper/services/sync.py `detail_phase`). Ağ yok. Diğer bağlam testleri tests/test_sync_service.py'dedir
(veri dizinleri, dondurulmuşluk, takipler: tests/test_store_follows.py).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sofascore_scraper.config_manager import ConfigManager
from sofascore_scraper.services.context import ServiceContext, build_context

ROOT = Path(__file__).resolve().parent.parent
REMOVED_MODULES = ("sofascore_scraper.match_data_fetcher", "sofascore_scraper.match_fetcher",
                   "sofascore_scraper.season_fetcher")


@pytest.fixture
def ctx(tmp_path: Path) -> ServiceContext:
    return build_context(ConfigManager(), data_dir=str(tmp_path / "data"))


def test_the_context_carries_no_fetcher(ctx: ServiceContext, tmp_path: Path) -> None:
    for name in ("season_fetcher", "match_fetcher", "match_data_fetcher"):
        assert not hasattr(ctx, name), name
    # 2.x'in detay indiricisinin kurucusu processed/ dizinini kuruyordu: bugün hiçbir şey kurmaz
    assert not (tmp_path / "data" / "match_details" / "processed").exists()


def test_each_run_has_its_own_detail_phase(ctx: ServiceContext) -> None:
    from sofascore_scraper.services import sync

    first, second = sync.detail_phase(ctx), sync.detail_phase(ctx)
    assert first is not second and first.store is second.store is ctx.store
    assert first.config is ctx.config


def test_the_context_stays_frozen(ctx: ServiceContext) -> None:
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.data_dir = "elsewhere"  # type: ignore[misc]
    assert [f.name for f in dataclasses.fields(ctx)] == ["config", "data_dir", "client"]


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_the_fetcher_modules_are_gone(module: str) -> None:
    code = f"import importlib.util, sys\nsys.exit(0 if importlib.util.find_spec({module!r}) is None else 1)\n"
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0
