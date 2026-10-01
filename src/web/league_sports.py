"""
Which sport each configured league belongs to.

config/leagues.txt stays `name: id` (the CLI reads it), so the sport lives beside it in
config/league_sports.json as {"<league id>": "<sport slug>"} (the sports registered in src/sports.py:
"football" | "basketball" | "tennis").

A league added through the web UI gets its sport from the remote search result. A league
added any other way has none until either someone picks it in the UI, or a downloaded
match's basic.json (tournament.category.sport) tells us; that lookup reads local files only.

The sidecar stays the source of truth. After every write the sports are also mirrored into the
`follows` table of the data directory (origin "legacy", together with the league names; see
ConfigManager.mirror_follows and src/store/follows.py). The file is never rewritten from the table.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import threading
from typing import Dict, Optional

from src import sports
from src.fsutil import atomic_write_json, file_lock
from src.logger import get_logger

logger = get_logger("LeagueSports")

# The supported sports come from the registry in src/sports.py; this name stays for existing imports
SPORTS = sports.sport_slugs()

_lock = threading.Lock()


def normalize_sport(raw: object) -> Optional[str]:
    """Sofascore says "Football"/"football"/"Basketball"/"Tennis"; anything else is unknown."""
    return sports.normalize_sport(raw)


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


def _mirror_follows(league_config_path: str) -> None:
    """
    The sidecar was written: bring the follows table in line (called after the file lock is released).

    The league names live in ConfigManager, so the mirror goes through it. If the config module was never
    loaded there is no ConfigManager in this process and nothing to do: the sports reach the table the next
    time a ConfigManager mirrors that league file.
    """
    config_module = sys.modules.get("src.config_manager")
    if config_module is not None:
        config_module.mirror_league_follows(league_config_path)


def set_sport(league_config_path: str, league_id: int, sport: Optional[str]) -> None:
    """Store (or with sport=None, forget) a league's sport."""
    with _lock, file_lock(sidecar_path(league_config_path)):
        data = load(league_config_path)
        if sport:
            data[int(league_id)] = sport
        else:
            data.pop(int(league_id), None)
        _save(league_config_path, data)
    _mirror_follows(league_config_path)


def infer_from_data(data_dir: str, league_id: int) -> Optional[str]:
    """Read the sport from any downloaded match of this league (no network)."""
    pattern = os.path.join(data_dir, "match_details", f"{int(league_id)}_*", "*", "*", "basic.json")
    for path in glob.iglob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                basic = json.load(f)
        except (OSError, ValueError):
            continue
        found = normalize_sport(sports.event_sport_slug(basic))
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
        _mirror_follows(league_config_path)
    return out
