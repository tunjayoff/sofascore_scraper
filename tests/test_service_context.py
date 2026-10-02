"""
Servis bağlamı (plan maddesi P15): `build_context` eski indiricileri kurmaz; ilk erişimde kurulurlar.

Ağ yok. Diğer bağlam testleri tests/test_sync_service.py'dedir (veri dizinleri, dondurulmuşluk, takipler:
tests/test_store_follows.py).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.config_manager import ConfigManager
from src.services.context import ServiceContext, build_context

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ctx(tmp_path: Path) -> ServiceContext:
    return build_context(ConfigManager(), data_dir=str(tmp_path / "data"))


def test_the_fetchers_are_built_on_first_use(ctx: ServiceContext, tmp_path: Path) -> None:
    assert not {"season_fetcher", "match_fetcher", "match_data_fetcher"} & set(vars(ctx))
    # MatchDataFetcher'ın kurucusu processed/ dizinini kurar: bağlam kurulurken kurulmaz
    assert not (tmp_path / "data" / "match_details" / "processed").exists()

    details = ctx.match_data_fetcher
    assert details is ctx.match_data_fetcher  # bağlamın ömrü boyunca aynı nesne
    assert details.data_dir == ctx.data_dir and details.config_manager is ctx.config
    assert (tmp_path / "data" / "match_details" / "processed").is_dir()

    schedule = ctx.match_fetcher
    assert schedule.season_fetcher is ctx.season_fetcher
    assert schedule is ctx.match_fetcher and ctx.season_fetcher.data_dir == ctx.data_dir


def test_each_context_has_its_own_fetchers(tmp_path: Path) -> None:
    config = ConfigManager()
    first = build_context(config, data_dir=str(tmp_path))
    second = build_context(config, data_dir=str(tmp_path))
    assert first.match_data_fetcher is not second.match_data_fetcher
    assert first.match_fetcher is not second.match_fetcher


def test_the_context_stays_frozen(ctx: ServiceContext) -> None:
    import dataclasses

    _ = ctx.season_fetcher
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.data_dir = "elsewhere"  # type: ignore[misc]
    assert [f.name for f in dataclasses.fields(ctx)] == ["config", "data_dir", "client"]


def test_importing_the_context_does_not_load_the_fetchers() -> None:
    code = (
        "import sys, src.services.context\n"
        "loaded = sorted(m for m in ('src.match_data_fetcher', 'src.match_fetcher', 'src.season_fetcher')"
        " if m in sys.modules)\n"
        "print('LOADED=' + ','.join(loaded))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "LOADED="
