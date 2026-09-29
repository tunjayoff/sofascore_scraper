"""
Veri dizini istatistikleri — web (dashboard, /stats/system) ve CLI (istatistik menüsü,
raporlar) aynı sayımları buradan alır.

Sayım kuralları:
- maç: lig dizinindeki sezon özet CSV'lerinin (`*_summary.csv`, eski `*_matches.csv`) satırları
- maç detayı: `basic.json` içeren maç dizini (dilim JSON'ları değil)
- lig eşlemesi: dizin adının `{lig_id}_` öneki; detay dizinleri için eski ID'siz ad da kabul edilir
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
from typing import Any, Dict, List, Optional

from src.paths import safe_name

SUMMARY_SUFFIXES = ("_summary.csv", "_matches.csv")


def dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _csv_rows(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def _season_list_len(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    if isinstance(data, dict):
        data = data.get("seasons", [])
    return len(data) if isinstance(data, list) else 0


def _league_dirs(parent: str, league_id: int, league_name: Optional[str] = None) -> List[str]:
    """`parent` altında bu lige ait dizinler: `{id}_*`, yoksa eski ID'siz ad."""
    if not os.path.isdir(parent):
        return []
    found = [os.path.join(parent, d) for d in os.listdir(parent) if d.startswith(f"{league_id}_")]
    if not found and league_name:
        legacy = os.path.join(parent, safe_name(league_name))
        if os.path.isdir(legacy):
            found.append(legacy)
    return [d for d in found if os.path.isdir(d)]


def _summary_files(league_dir: str) -> List[str]:
    out = []
    for root, _, files in os.walk(league_dir):
        out.extend(os.path.join(root, f) for f in files if f.endswith(SUMMARY_SUFFIXES))
    return out


def _detail_basics(league_dir: str) -> List[str]:
    return glob.glob(os.path.join(league_dir, "season_*", "*", "basic.json"))


def league_stats(data_dir: str, league_id: int, league_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Bir lig için: seasons (SofaScore'daki sezon listesi), seasons_fetched (programı indirilmiş
    sezon), matches, details, coverage (%), last_update (son detay yazımı, ISO) ve disk boyutları.
    """
    seasons_files = glob.glob(os.path.join(data_dir, "seasons", f"{league_id}_*_seasons.json"))
    match_dirs = _league_dirs(os.path.join(data_dir, "matches"), league_id)
    detail_dirs = _league_dirs(os.path.join(data_dir, "match_details"), league_id, league_name)

    summaries = [f for d in match_dirs for f in _summary_files(d)]
    basics = [b for d in detail_dirs for b in _detail_basics(d)]
    matches = sum(_csv_rows(f) for f in summaries)
    last_update = None
    if basics:
        latest = max(os.path.getmtime(b) for b in basics)
        last_update = _dt.datetime.fromtimestamp(latest).isoformat()

    sizes = {
        "seasons": sum(os.path.getsize(f) for f in seasons_files),
        "matches": sum(dir_size(d) for d in match_dirs),
        "details": sum(dir_size(d) for d in detail_dirs),
    }
    sizes["total"] = sum(sizes.values())
    return {
        "id": league_id,
        "name": league_name,
        "seasons": max((_season_list_len(f) for f in seasons_files), default=0),
        "seasons_fetched": len({os.path.basename(f).split("_", 1)[0] for f in summaries}),
        "matches": matches,
        "details": len(basics),
        "coverage": round(len(basics) / matches * 100, 1) if matches else 0,
        "last_update": last_update,
        "disk": sizes,
    }


def system_stats(data_dir: str, leagues: Dict[int, str]) -> Dict[str, Any]:
    """Tüm veri dizini: toplamlar, yapılandırılmış ligler için döküm ve disk kullanımı."""
    seasons_dir = os.path.join(data_dir, "seasons")
    matches_dir = os.path.join(data_dir, "matches")
    details_dir = os.path.join(data_dir, "match_details")

    season_count = 0
    if os.path.isdir(seasons_dir):
        season_count = sum(
            _season_list_len(os.path.join(seasons_dir, f)) for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")
        )
    match_count = sum(_csv_rows(f) for f in _summary_files(matches_dir)) if os.path.isdir(matches_dir) else 0
    detail_count = len(glob.glob(os.path.join(details_dir, "*", "season_*", "*", "basic.json")))

    breakdown = [league_stats(data_dir, lid, name) for lid, name in leagues.items()]
    disk = {
        "seasons": dir_size(seasons_dir),
        "matches": dir_size(matches_dir),
        "details": dir_size(details_dir),
        "datasets": dir_size(os.path.join(data_dir, "datasets")),
    }
    disk["total"] = sum(disk.values())
    disk["formatted_total"] = format_size(disk["total"])
    return {
        "leagues": len(leagues),
        "seasons": season_count,
        "matches": match_count,
        "details": detail_count,
        "league_breakdown": breakdown,
        "disk_usage": disk,
    }
