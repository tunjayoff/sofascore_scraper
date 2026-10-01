"""Geriye dönük uyumluluk: iş ilerlemesi `src/jobs/progress.py` içine taşındı.

Bu modül yalnızca eski içe aktarma yolunu (`src.web.progress`) ayakta tutar; yeni kod
`src.jobs.progress` yolunu kullanmalı.
"""
from __future__ import annotations

from src.jobs.progress import (
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
