"""
Kütüphane logger'ları (FX-23, bulgu F3): Scrapling içe aktarılırken kendi "[zaman] INFO: ..." handler'ını kurar ve
satırları kök logger'a da iletir; her satır iki kez görünüyordu. Köprü onu uygulamanın handler'larına bağlar.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

from sofascore_scraper import logger as logger_mod
from sofascore_scraper.client import bridge as cs


@pytest.fixture
def scrapling_logger():
    target = logging.getLogger("scrapling")
    saved = (list(target.handlers), target.propagate)
    yield target
    target.handlers[:] = saved[0]
    target.propagate = saved[1]


def test_a_library_logger_writes_through_the_application_handlers(scrapling_logger):
    scrapling_logger.addHandler(logging.StreamHandler())
    scrapling_logger.propagate = False

    assert logger_mod.adopt_library_logger("scrapling") == 1

    assert scrapling_logger.handlers == [] and scrapling_logger.propagate is True
    assert logger_mod.adopt_library_logger("scrapling") == 0


def test_the_bridge_adopts_the_scrapling_logger_when_it_loads_scrapling(scrapling_logger, monkeypatch):
    scrapling_logger.addHandler(logging.StreamHandler())  # Scrapling'in içe aktarılırken kurduğu handler

    class Session:
        pass

    fetchers = types.ModuleType("scrapling.fetchers")
    fetchers.AsyncStealthySession = Session  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fetchers)

    assert cs._session_class() is Session
    assert scrapling_logger.handlers == [] and scrapling_logger.propagate is True
