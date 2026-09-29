"""
Which sport each configured league belongs to.

config/leagues.txt stays `name: id` (the CLI reads it), so the sport lives beside it in
config/league_sports.json as {"<league id>": "football" | "basketball" | "tennis"}.

A league added through the web UI gets its sport from the remote search result. A league
added any other way has none until either someone picks it in the UI, or a downloaded
match's basic.json (tournament.category.sport) tells us; that lookup reads local files only.
"""
from __future__ import annotations

import glob
import json
import os
import threading
from typing import Dict, Optional

from src.fsutil import atomic_write_json, file_lock
from src.logger import get_logger

logger = get_logger("LeagueSports")

SPORTS = ("football", "basketball", "tennis")

_lock = threading.Lock()


def normalize_sport(raw: object) -> Optional[str]:
    """Sofascore says "Football"/"football"/"Basketball"/"Tennis"; anything else is unknown."""
    s = str(raw or "").strip().lower()
    if "basket" in s:
        return "basketball"
    if "tennis" in s:
        return "tennis"
    if "football" in s or "soccer" in s:
        return "football"
    return None


def sidecar_path(league_config_path: str) -> str:
    return os.path.join(os.path.dirname(league_config_path) or ".", "league_sports.json")


def load(league_config_path: str) -> Dict[int, str]:
    p = sidecar_path(league_config_path)
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning(f"{p} okunamadı: {e}")
        return {}
    out: Dict[int, str] = {}
    for k, v in (raw or {}).items():
        sport = normalize_sport(v)
        try:
            if sport:
                out[int(k)] = sport
        except (TypeError, ValueError):
            continue
    return out


def _save(league_config_path: str, data: Dict[int, str]) -> None:
    atomic_write_json(sidecar_path(league_config_path), {str(k): v for k, v in sorted(data.items())})


def set_sport(league_config_path: str, league_id: int, sport: Optional[str]) -> None:
    """Store (or with sport=None, forget) a league's sport."""
    with _lock, file_lock(sidecar_path(league_config_path)):
        data = load(league_config_path)
        if sport:
            data[int(league_id)] = sport
        else:
            data.pop(int(league_id), None)
        _save(league_config_path, data)


def infer_from_data(data_dir: str, league_id: int) -> Optional[str]:
    """Read the sport from any downloaded match of this league (no network)."""
    pattern = os.path.join(data_dir, "match_details", f"{int(league_id)}_*", "*", "*", "basic.json")
    for path in glob.iglob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                basic = json.load(f)
        except (OSError, ValueError):
            continue
        sport = (((basic.get("tournament") or {}).get("category") or {}).get("sport") or {})
        found = normalize_sport(sport.get("slug") or sport.get("name"))
        if found:
            return found
    return None


def resolve_all(league_config_path: str, data_dir: str, league_ids) -> Dict[int, Optional[str]]:
    """Sport for each id: stored, else inferred from local data (and then stored), else None."""
    stored = load(league_config_path)
    out: Dict[int, Optional[str]] = {}
    learned: Dict[int, str] = {}
    for lid in league_ids:
        lid = int(lid)
        sport = stored.get(lid)
        if not sport:
            sport = infer_from_data(data_dir, lid)
            if sport:
                learned[lid] = sport
        out[lid] = sport
    if learned:
        with _lock, file_lock(sidecar_path(league_config_path)):
            data = load(league_config_path)
            data.update(learned)
            _save(league_config_path, data)
    return out
