"""Dashboard, istatistikler, yedekleme, veri temizleme ve CSV dışa aktarma."""
from __future__ import annotations

import asyncio
import glob
import os
import re
import traceback
from typing import Any, Dict, Literal, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.paths import env_file_path
from src.web import league_sports
from src.services import stats as stats_service
from src.web.routes.common import (
    _SyncHttpError,
    config_manager,
    logger,
)

router = APIRouter(prefix="/api", tags=["api"])


def _build_dashboard_sync(data_dir: str, leagues: Dict[int, str]) -> dict:
    cards = []
    for lid, name in leagues.items():
        st = stats_service.league_stats(data_dir, lid, name)
        cards.append({k: st[k] for k in ("id", "name", "seasons", "matches", "details", "coverage", "last_update")})
    disk = stats_service.system_stats(data_dir, {})["disk_usage"]
    return {
        "leagues": cards,
        "disk_usage": {k: disk[k] for k in ("seasons", "matches", "details", "total", "formatted_total")},
        "totals": {
            "leagues": len(leagues),
            "matches": sum(c["matches"] for c in cards),
            "details": sum(c["details"] for c in cards),
        },
    }


def _compute_system_stats_sync(data_dir: str, leagues: Dict[int, str]) -> Dict[str, Any]:
    stats = stats_service.system_stats(data_dir, leagues)
    stats["league_breakdown"] = sorted(
        (
            {k: b[k] for k in ("id", "name", "matches", "details", "coverage")}
            for b in stats["league_breakdown"]
            if b["matches"] or b["details"]
        ),
        key=lambda b: b["matches"],
        reverse=True,
    )
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
def download_backup(name: str):
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
