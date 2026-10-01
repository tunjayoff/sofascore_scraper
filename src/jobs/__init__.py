"""İşler (jobs): web ve CLI'nin ortak iş modeli ve ilerleme takibi.

Bu paket SQL ve dosya erişimi içermez; kalıcılık depo (store) katmanının işidir.
"""
from __future__ import annotations

from src.jobs.progress import MAX_FAILED_LISTED, PHASE_WEIGHTS, JobProgress

__all__ = [
    "MAX_FAILED_LISTED",
    "PHASE_WEIGHTS",
    "JobProgress",
]
