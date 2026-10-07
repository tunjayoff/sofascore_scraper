"""
Which sport each configured league belongs to.

config/leagues.txt stays `name: id` (the CLI reads it), so the sport lives beside it in
config/league_sports.json as {"<league id>": "<sport slug>"} (the sports registered in sofascore_scraper/sports.py:
"football" | "basketball" | "tennis").

A league added through the web UI gets its sport from the remote search result. A league
added any other way has none until either someone picks it in the UI, or the downloaded data
tells us: the catalog of the data directory knows the sport of every tournament it has a match
of (sofascore_scraper/services/tournaments.sport_of; no network, no file scan). `sports_for` answers from both
and writes nothing; a sport read from the data is not copied into the sidecar.

The sidecar stays the source of truth. After every write the sports are also mirrored into the
`follows` table of the data directory (origin "legacy", together with the league names; see
ConfigManager.mirror_follows and sofascore_scraper/store/follows.py). The file is never rewritten from the table.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Dict, Iterable, Optional

from sofascore_scraper import sports
from sofascore_scraper import store as store_api
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.config_files import atomic_write_json, file_lock
from sofascore_scraper.logger import get_logger
from sofascore_scraper.services import tournaments

logger = get_logger("LeagueSports")

# The supported sports come from the registry in sofascore_scraper/sports.py; this name stays for existing imports
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
    config_module = sys.modules.get("sofascore_scraper.config_manager")
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
    """
    The sport of this league's downloaded matches, from the catalog of the data directory (no network).

    The tournament of a match is the one its payload names, whatever directory the match is stored in.
    None when nothing is known, the sport is not a registered one, or the catalog cannot be read (the
    league list is configuration and must not fail because an index is unavailable).
    """
    try:
        return tournaments.sport_of(store_api.open_store(data_dir), int(league_id))
    except (StorageError, ValueError) as e:  # ValueError: an id outside the range the catalog can hold
        logger.warning("Sport of league %s could not be read from the catalog of %s: %s", league_id, data_dir, e)
        return None


def sports_for(league_config_path: str, data_dir: str, league_ids: Iterable[int]) -> Dict[int, Optional[str]]:
    """Sport for each id: stored, else read from the downloaded data, else None. Nothing is written."""
    stored = load(league_config_path)
    out: Dict[int, Optional[str]] = {}
    for lid in league_ids:
        lid = int(lid)
        out[lid] = stored.get(lid) or infer_from_data(data_dir, lid)
    return out
