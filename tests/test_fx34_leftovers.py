"""
FX-34: belge güncellemesi 10'un (#193) bulduğu küçük artıklar. Ağ yok.

  * canlı servisin belgeleri 3.1'de kalkan `--watch` bayrağını ve `watcher:<spor>` izleyicisini anmaz.
"""
from __future__ import annotations

import re

import pytest

from sofascore_scraper.cli.commands import watch as watch_command
from sofascore_scraper.services.live import supervisor


@pytest.mark.parametrize("module", [watch_command, supervisor], ids=["cli.watch", "live.supervisor"])
def test_the_live_service_docstrings_name_no_removed_watcher(module: object) -> None:
    doc = module.__doc__ or ""
    assert not re.search(r"--watch\b", doc)
    assert "watcher:" not in doc
