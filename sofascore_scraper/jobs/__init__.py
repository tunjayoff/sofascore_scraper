"""İşler (jobs): web ve CLI'nin ortak iş modeli ve ilerleme takibi.

Bu paket SQL ve dosya erişimi içermez; kalıcılık depo (store) katmanının işidir.
"""
from __future__ import annotations

from sofascore_scraper.jobs.model import (
    LEGACY_STATUS_TO_STATE,
    ErrorInfo,
    Job,
    JobKind,
    JobState,
    Origin,
    OriginFace,
    job_state_from_status,
)
from sofascore_scraper.jobs.progress import MAX_FAILED_LISTED, PHASE_WEIGHTS, JobProgress

__all__ = [
    "LEGACY_STATUS_TO_STATE",
    "MAX_FAILED_LISTED",
    "PHASE_WEIGHTS",
    "ErrorInfo",
    "Job",
    "JobKind",
    "JobProgress",
    "JobState",
    "Origin",
    "OriginFace",
    "job_state_from_status",
]
