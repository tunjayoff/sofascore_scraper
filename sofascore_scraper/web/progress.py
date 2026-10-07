"""Geriye dönük uyumluluk: iş ilerlemesi `sofascore_scraper/jobs/progress.py` içine taşındı.

Bu modül yalnızca eski içe aktarma yolunu (`sofascore_scraper.web.progress`) ayakta tutar; yeni kod
`sofascore_scraper.jobs.progress` yolunu kullanmalı.
"""
from __future__ import annotations

from sofascore_scraper.jobs.progress import (
    _ETA_MIN_DONE,
    _ETA_MIN_ELAPSED,
    MAX_FAILED_LISTED,
    PHASE_WEIGHTS,
    JobProgress,
)

__all__ = [
    "MAX_FAILED_LISTED",
    "PHASE_WEIGHTS",
    "JobProgress",
    "_ETA_MIN_DONE",
    "_ETA_MIN_ELAPSED",
]
