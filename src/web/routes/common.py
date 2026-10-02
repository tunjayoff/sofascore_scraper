"""Paylaşılan durum: yapılandırma, iş deposu ve senkron worker hataları."""
from __future__ import annotations

from typing import Any, Dict


from src.config_manager import ConfigManager
from src.logger import get_logger
from src.web.jobs import get_job_store

logger = get_logger("WebAPI")
config_manager = ConfigManager()


class _SyncHttpError(Exception):
    """Senkron worker içinde fırlatılır; async route HTTPException'a çevrilir."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


_job_store = get_job_store(config_manager.get_data_dir())


SCRAPER_STATE = _job_store.snapshot()


def _refresh_scraper_state() -> Dict[str, Any]:
    """Keep module-level SCRAPER_STATE dict in sync with JobStore mirror."""
    global SCRAPER_STATE
    snap = _job_store.snapshot()
    SCRAPER_STATE.clear()
    SCRAPER_STATE.update(snap)
    return SCRAPER_STATE


def _persist_scraper_fields(**kwargs: Any) -> None:
    _job_store.update(**kwargs)
    _refresh_scraper_state()
