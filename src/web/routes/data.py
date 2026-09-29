"""Dashboard, istatistikler, yedekleme, veri temizleme ve CSV dışa aktarma."""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import traceback
from typing import Any, Dict, Literal, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.paths import env_file_path
from src.web import league_sports
from src.web.routes.common import (
    _SyncHttpError,
    _find_league_seasons_json,
    config_manager,
    logger,
)

router = APIRouter(prefix="/api", tags=["api"])


def _build_dashboard_sync(data_dir: str, leagues: Dict[int, str]) -> dict:
    seasons_dir = os.path.join(data_dir, "seasons")
    matches_dir = os.path.join(data_dir, "matches")
    details_dir = os.path.join(data_dir, "match_details")

    league_cards = []
    for lid, lname in leagues.items():
        card = {"id": lid, "name": lname, "seasons": 0, "matches": 0, "details": 0, "coverage": 0, "last_update": None}
        sf = _find_league_seasons_json(data_dir, lid)
        if sf:
            try:
                with open(sf, "r") as f:
                    d = json.load(f)
                s = d.get("seasons", d) if isinstance(d, dict) else d
                card["seasons"] = len(s) if isinstance(s, list) else 0
            except Exception:
                pass

        match_pattern = os.path.join(matches_dir, f"{lid}_*", "*.csv")
        for mf in glob.glob(match_pattern):
            try:
                with open(mf, "r") as f:
                    rows = sum(1 for _ in f) - 1
                if rows > 0:
                    card["matches"] += rows
            except Exception:
                pass

        detail_files = glob.glob(os.path.join(details_dir, f"{lid}_*", "season_*", "*", "basic.json"))
        if not detail_files:
            safe_name = lname.replace(" ", "_").replace("/", "_")
            detail_files = glob.glob(os.path.join(details_dir, safe_name, "season_*", "*", "basic.json"))
        card["details"] = len(detail_files)
        card["coverage"] = round(card["details"] / card["matches"] * 100, 1) if card["matches"] > 0 else 0

        if detail_files:
            latest_mtime = max(os.path.getmtime(f) for f in detail_files)
            import datetime as _dt

            card["last_update"] = _dt.datetime.fromtimestamp(latest_mtime).isoformat()

        league_cards.append(card)

    def get_dir_size(path: str) -> int:
        total = 0
        if os.path.exists(path):
            for dp, _, fns in os.walk(path):
                for fn in fns:
                    try:
                        total += os.path.getsize(os.path.join(dp, fn))
                    except Exception:
                        pass
        return total

    s_size = get_dir_size(seasons_dir)
    m_size = get_dir_size(matches_dir)
    d_size = get_dir_size(details_dir)
    total = s_size + m_size + d_size

    size_fmt = total
    formatted = "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_fmt < 1024:
            formatted = f"{size_fmt:.1f} {unit}"
            break
        size_fmt /= 1024
    else:
        formatted = f"{size_fmt:.1f} TB"

    return {
        "leagues": league_cards,
        "disk_usage": {
            "seasons": s_size,
            "matches": m_size,
            "details": d_size,
            "total": total,
            "formatted_total": formatted,
        },
        "totals": {
            "leagues": len(leagues),
            "matches": sum(c["matches"] for c in league_cards),
            "details": sum(c["details"] for c in league_cards),
        },
    }


def _compute_system_stats_sync(data_dir: str, leagues: Dict[int, str]) -> Dict[str, Any]:
    logger.info(f"Generating stats from data_dir: {data_dir}")
    league_stats: Dict[int, Dict[str, Any]] = {
        league_id: {"name": league_name, "matches": 0, "details": 0}
        for league_id, league_name in leagues.items()
    }

    stats: Dict[str, Any] = {
        "leagues": len(leagues),
        "seasons": 0,
        "matches": 0,
        "details": 0,
        "league_breakdown": [],
        "disk_usage": {
            "seasons": 0,
            "matches": 0,
            "details": 0,
            "total": 0,
            "formatted_total": "0 B",
        },
    }

    seasons_dir = os.path.join(data_dir, "seasons")
    if os.path.exists(seasons_dir):
        files = [f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")]
        for f in files:
            try:
                with open(os.path.join(seasons_dir, f), "r") as fp:
                    data = json.load(fp)
                    if isinstance(data, dict) and "seasons" in data:
                        stats["seasons"] += len(data["seasons"])
                    elif isinstance(data, list):
                        stats["seasons"] += len(data)
            except Exception:
                pass

    matches_dir = os.path.join(data_dir, "matches")
    if os.path.exists(matches_dir):
        for root, dirs, files in os.walk(matches_dir):
            parent_dir = os.path.basename(root)
            current_l_id = None
            if "_" in parent_dir:
                try:
                    current_l_id = int(parent_dir.split("_")[0])
                except Exception:
                    pass

            for file in files:
                if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                    try:
                        with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                            row_count = sum(1 for line in f) - 1
                            if row_count > 0:
                                stats["matches"] += row_count
                                if current_l_id in league_stats:
                                    league_stats[current_l_id]["matches"] += row_count
                    except Exception:
                        pass

    match_details_dir = os.path.join(data_dir, "match_details")
    if os.path.exists(match_details_dir):
        league_name_to_id = {}
        for lid, lname in leagues.items():
            league_name_to_id[lname.replace(" ", "_").replace("/", "_").lower()] = lid

        basic_files = glob.glob(os.path.join(match_details_dir, "*", "season_*", "*", "basic.json"))
        for bf in basic_files:
            stats["details"] += 1
            rel = os.path.relpath(bf, match_details_dir)
            lg_folder = rel.split(os.sep)[0]
            current_l_id = None
            try:
                current_l_id = int(lg_folder.split("_")[0])
            except (ValueError, IndexError):
                clean = lg_folder.lower()
                current_l_id = league_name_to_id.get(clean)
                if current_l_id is None:
                    for key, lid in league_name_to_id.items():
                        if key in clean or clean in key:
                            current_l_id = lid
                            break
            if current_l_id in league_stats:
                league_stats[current_l_id]["details"] += 1

    for l_id, data in league_stats.items():
        if data["matches"] > 0 or data["details"] > 0:
            stats["league_breakdown"].append(
                {
                    "id": l_id,
                    "name": data["name"],
                    "matches": data["matches"],
                    "details": data["details"],
                    "coverage": round((data["details"] / data["matches"] * 100), 1)
                    if data["matches"] > 0
                    else 0,
                }
            )

    stats["league_breakdown"].sort(key=lambda x: x["matches"], reverse=True)

    def get_dir_size(path: str) -> int:
        total = 0
        if os.path.exists(path):
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        if os.path.exists(fp):
                            total += os.path.getsize(fp)
                    except Exception:
                        pass
        return total

    stats["disk_usage"]["seasons"] = get_dir_size(seasons_dir)
    stats["disk_usage"]["matches"] = get_dir_size(matches_dir)
    stats["disk_usage"]["details"] = get_dir_size(match_details_dir)
    stats["disk_usage"]["total"] = sum([stats["disk_usage"][k] for k in ["seasons", "matches", "details"]])

    size = stats["disk_usage"]["total"]
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            stats["disk_usage"]["formatted_total"] = f"{size:.2f} {unit}"
            break
        size /= 1024

    if "formatted_total" not in stats["disk_usage"]:
        stats["disk_usage"]["formatted_total"] = f"{size:.2f} TB"

    return stats


@router.get("/dashboard")
async def get_dashboard():
    """Dashboard overview with league cards, disk usage, and quick stats."""
    data_dir = config_manager.get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)
    leagues = dict(config_manager.get_leagues())
    return await asyncio.to_thread(_build_dashboard_sync, data_dir, leagues)


# Stats API
@router.get("/stats/system")
async def get_system_stats():
    """Get detailed system statistics (ported from StatsMenuHandler)."""
    try:
        data_dir = config_manager.get_data_dir()
        if not os.path.isabs(data_dir):
            data_dir = os.path.abspath(data_dir)

        leagues = dict(config_manager.get_leagues())
        return await asyncio.to_thread(_compute_system_stats_sync, data_dir, leagues)

    except Exception as e:
        logger.error(f"Error generating stats: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to generate statistics")


# --- Data Management Endpoints (W10) ---

BackupScope = Literal["all", "config", "seasons", "matches", "match_details"]


DataScope = Literal["all", "seasons", "matches", "match_details"]


_BACKUP_NAME_RE = re.compile(r"^backup_[a-z_]+_\d{8}_\d{6}\.zip$")


def _backups_dir() -> str:
    """Yedekler veri dizininde tutulur — web sunucusunun statik olarak servis ettiği bir yerde değil."""
    return os.path.join(os.path.abspath(config_manager.get_data_dir()), "backups")


def _create_backup_sync(scope: str, include_env: bool = False) -> dict:
    import zipfile
    import datetime as _dt

    data_dir = os.path.abspath(config_manager.get_data_dir())

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{scope}_{timestamp}.zip"

    backups_dir = _backups_dir()
    os.makedirs(backups_dir, exist_ok=True)
    zip_path = os.path.join(backups_dir, backup_filename)

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            dirs_to_backup = []
            if scope in ("all", "config"):
                config_path = config_manager.league_config_path
                if os.path.exists(config_path):
                    zf.write(config_path, os.path.basename(config_path))
                sports_path = league_sports.sidecar_path(config_path)
                if os.path.exists(sports_path):
                    zf.write(sports_path, os.path.basename(sports_path))
                # .env proxy kimlik bilgisi ve captcha token taşıyabilir; yalnızca açıkça istenirse
                if include_env and os.path.exists(env_file_path()):
                    zf.write(env_file_path(), ".env")
            if scope in ("all", "seasons"):
                dirs_to_backup.append(os.path.join(data_dir, "seasons"))
            if scope in ("all", "matches"):
                dirs_to_backup.append(os.path.join(data_dir, "matches"))
            if scope in ("all", "match_details"):
                dirs_to_backup.append(os.path.join(data_dir, "match_details"))

            for dir_path in dirs_to_backup:
                if os.path.exists(dir_path):
                    for root, _, files in os.walk(dir_path):
                        for f in files:
                            fp = os.path.join(root, f)
                            arcname = os.path.relpath(fp, os.path.dirname(data_dir))
                            zf.write(fp, arcname)

        return {"download_url": f"/api/data/backups/{backup_filename}", "filename": backup_filename}
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        raise _SyncHttpError(500, "Backup failed") from e


def _clear_data_sync(scope: str) -> dict:
    import shutil

    data_dir = config_manager.get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)

    cleared = []
    try:
        if scope in ("all", "match_details"):
            path = os.path.join(data_dir, "match_details")
            if os.path.exists(path):
                shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
                cleared.append("match_details")
        if scope in ("all", "matches"):
            path = os.path.join(data_dir, "matches")
            if os.path.exists(path):
                shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
                cleared.append("matches")
        if scope in ("all", "seasons"):
            path = os.path.join(data_dir, "seasons")
            if os.path.exists(path):
                shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
                cleared.append("seasons")
        return {"status": "success", "cleared": cleared}
    except Exception as e:
        logger.error(f"Clear data failed: {e}")
        raise _SyncHttpError(500, "Clear data failed") from e


def _export_csv_sync(league_id: Optional[int], data_dir: str):
    from fastapi.responses import FileResponse, Response

    csv_dir = os.path.join(data_dir, "match_details", "processed")
    pattern = os.path.join(csv_dir, "all_matches_*.csv")
    files = glob.glob(pattern)

    if not files:
        from src.SofaScoreUi import SimpleSofaScoreUI

        try:
            ui = SimpleSofaScoreUI(config_manager=config_manager)
            ui.export_all_to_csv()
            files = glob.glob(pattern)
        except Exception as e:
            logger.error(f"CSV export failed: {e}")
            raise _SyncHttpError(500, "CSV generation failed") from e

    if not files:
        raise _SyncHttpError(404, "No CSV data available. Run a fetch first.")

    latest = max(files, key=os.path.getctime)
    if not league_id:
        return FileResponse(latest, filename=os.path.basename(latest), media_type="text/csv")

    # Tek lig: birleşik dosyadan satırları lig klasörüne (`{id}_{ad}`) göre süz
    df = pd.read_csv(latest, low_memory=False)
    if "league_folder" in df.columns:
        df = df[df["league_folder"].astype(str).str.startswith(f"{league_id}_")]
    if df.empty:
        raise _SyncHttpError(404, f"No exported matches for league {league_id}.")
    filename = f"league_{league_id}_{os.path.basename(latest)}"
    return Response(
        content=df.to_csv(index=False),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/data/backup")
async def create_backup(scope: BackupScope = "all", include_env: bool = False):
    """Veri yedeği oluşturur ve indirilebilir zip dosyası döndürür."""
    try:
        return await asyncio.to_thread(_create_backup_sync, scope, include_env)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/data/backups/{name}")
async def download_backup(name: str):
    from fastapi.responses import FileResponse

    if not _BACKUP_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="Not Found")
    path = os.path.join(_backups_dir(), name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(path, filename=name, media_type="application/zip")


class ClearRequest(BaseModel):
    scope: DataScope = "all"


@router.post("/data/clear")
async def clear_data(req: ClearRequest):
    """Veriyi temizler."""
    try:
        return await asyncio.to_thread(_clear_data_sync, req.scope)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


# --- CSV Export Endpoint (W11) ---


@router.get("/export/csv")
async def export_csv(league_id: Optional[int] = None):
    """CSV export. Mevcut processed CSV'yi döndürür veya yeni oluşturur."""
    data_dir = config_manager.get_data_dir()
    try:
        return await asyncio.to_thread(_export_csv_sync, league_id, data_dir)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
