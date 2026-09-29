"""
SofaScore `status` sınıflandırması ve spora göre skor çıkarımı.

Kurallar araştırma verisine dayanır: docs/status-matrix/README.md ("Durum evreni", "Edge case tablosu").
Sonuçlandırma bu repoda yapılmaz; kurallar için docs/settlement-notes.md.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class StatusClass(str, Enum):
    NOT_STARTED = "not_started"  # type notstarted (code 0)
    LIVE = "live"  # type inprogress (6, 7, 8, 9, 10, 13-16, 20, 30, 31)
    COMPLETED = "completed"  # type finished, code 100/110/120 (oynandı ve bitti)
    DECIDED_WITHOUT_PLAY = "decided_without_play"  # type finished, code 91 Walkover / 92 Retired
    VOID = "void"  # type postponed/canceled/interrupted/suspended (60, 70, 80, 81, 90)
    UNKNOWN = "unknown"  # hiçbirine uymayan; loglanır, asla sessizce COMPLETED sayılmaz


_COMPLETED_CODES = frozenset({100, 110, 120})
_WITHOUT_PLAY_CODES = frozenset({91, 92})
_LIVE_CODES = frozenset({6, 7, 8, 9, 10, 13, 14, 15, 16, 20, 30, 31})
_VOID_CODES = frozenset({60, 70, 80, 81, 90})

_VOID_TYPES = frozenset({"postponed", "canceled", "interrupted", "suspended"})

# Yalnızca type ve code yoksa kullanılır
_BY_DESCRIPTION = {
    "not started": StatusClass.NOT_STARTED,
    "ended": StatusClass.COMPLETED,
    "aet": StatusClass.COMPLETED,
    "after extra time": StatusClass.COMPLETED,
    "ap": StatusClass.COMPLETED,
    "penalties": StatusClass.COMPLETED,
    "walkover": StatusClass.DECIDED_WITHOUT_PLAY,
    "retired": StatusClass.DECIDED_WITHOUT_PLAY,
    "postponed": StatusClass.VOID,
    "canceled": StatusClass.VOID,
    "abandoned": StatusClass.VOID,
    "interrupted": StatusClass.VOID,
    "suspended": StatusClass.VOID,
}


def _by_code(code: int) -> StatusClass:
    if code == 0:
        return StatusClass.NOT_STARTED
    if code in _LIVE_CODES:
        return StatusClass.LIVE
    if code in _COMPLETED_CODES:
        return StatusClass.COMPLETED
    if code in _WITHOUT_PLAY_CODES:
        return StatusClass.DECIDED_WITHOUT_PLAY
    if code in _VOID_CODES:
        return StatusClass.VOID
    return StatusClass.UNKNOWN


def classify_status(event: Optional[Dict[str, Any]]) -> StatusClass:
    """Karar sırası: status.type → status.code → description (yalnızca type ve code yoksa)."""
    status = (event or {}).get("status") or {}
    if not isinstance(status, dict) or not status:
        return StatusClass.UNKNOWN
    stype = status.get("type")
    code = status.get("code")

    if stype:
        if stype == "notstarted":
            return StatusClass.NOT_STARTED
        if stype == "inprogress":
            return StatusClass.LIVE
        if stype in _VOID_TYPES:
            return StatusClass.VOID
        if stype == "finished":
            if code in _COMPLETED_CODES:
                return StatusClass.COMPLETED
            if code in _WITHOUT_PLAY_CODES:
                return StatusClass.DECIDED_WITHOUT_PLAY
            if code is None:  # kod yoksa description yedek; finished türüyle çelişen sonuç kabul edilmez
                by_desc = _BY_DESCRIPTION.get(str(status.get("description") or "").strip().lower())
                if by_desc in (StatusClass.COMPLETED, StatusClass.DECIDED_WITHOUT_PLAY):
                    return by_desc
            logger.warning(f"Bilinmeyen finished kodu: {status} (event {(event or {}).get('id')})")
            return StatusClass.UNKNOWN
        logger.warning(f"Bilinmeyen status type: {status} (event {(event or {}).get('id')})")
        return StatusClass.UNKNOWN

    if code is not None:
        result = _by_code(code)
    else:
        result = _BY_DESCRIPTION.get(str(status.get("description") or "").strip().lower(), StatusClass.UNKNOWN)
    if result is StatusClass.UNKNOWN:
        logger.warning(f"Sınıflandırılamayan status: {status} (event {(event or {}).get('id')})")
    return result


def is_played(event: Optional[Dict[str, Any]]) -> bool:
    """Maç oynandı ve bitti (hükmen / çekilme / iptal değil)."""
    return classify_status(event) is StatusClass.COMPLETED
