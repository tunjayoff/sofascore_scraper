"""Dashboard, istatistikler, yedekleme, veri temizleme ve CSV dışa aktarma."""
from __future__ import annotations

import asyncio
import os
import re
import time
import traceback
from typing import Any, Callable, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Depo: özet katalogdan okunur (`open_store`); yedek ve temizleme Store'un işidir (servisler aracılığıyla)
from src import store as store_hooks
from src.web import league_sports
from src.services import stats as stats_service
from src.services.backup import BackupService
from src.services.maintenance import MaintenanceService
from src.services.status import DataSummary, StatusService
from src.web.routes.common import (
    _SyncHttpError,
    _job_store,
    config_manager,
    logger,
)

router = APIRouter(prefix="/api", tags=["api"])


def _data_summary(data_dir: str, leagues: Dict[int, str]) -> DataSummary:
    """Veri dizininin özeti (katalogdan); yapılandırılmış ligler maçları olmasa da dökümde yer alır."""
    return StatusService(store_hooks.open_store(data_dir)).summary(tournament_ids=tuple(leagues))


def _build_dashboard_sync(data_dir: str, leagues: Dict[int, str]) -> dict:
    summary = _data_summary(data_dir, leagues)
    cards = []
    for lid, name in leagues.items():
        st = stats_service.league_counts(summary, lid, name)
        cards.append({k: st[k] for k in ("id", "name", "seasons", "matches", "details", "coverage", "last_update")})
    disk = stats_service.disk_usage(summary)
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
    stats = stats_service.system_counts(_data_summary(data_dir, leagues), leagues)
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
    """
    Yedeği Store yazar (BackupService → `Store.backup`): bugünkü zip düzeni ve adı. Lig dosyası ve spor
    eşlemesi pakete girer; .env proxy kimlik bilgisi, captcha ve erişim belirteci taşıyabilir, yalnızca açıkça
    istenirse girer ve dosya adı bunu söyler (gizli değer taşıyan bir yedek veri yedeği sanılıp paylaşılmasın).
    """
    data_dir = os.path.abspath(config_manager.get_data_dir())
    config_path = config_manager.league_config_path
    try:
        info = BackupService(store_hooks.open_store(data_dir)).create(
            scope,  # type: ignore[arg-type]  # yolun kapsamı Literal ile denetlendi
            config_files=(config_path, league_sports.sidecar_path(config_path)),
            include_secrets=include_env,
        )
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        raise _SyncHttpError(500, "Backup failed") from e
    return {"download_url": f"/api/data/backups/{info.name}", "filename": info.name}


def _clear_data_sync(scope: str) -> dict:
    """Temizlemeyi Store yapar (MaintenanceService → `Store.clear`); katalog aynı kilit altında yeniden kurulur."""
    data_dir = config_manager.get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)
    try:
        report = MaintenanceService(store=store_hooks.open_store(data_dir)).clear(
            scope, confirm=True)  # type: ignore[arg-type]  # yolun kapsamı Literal ile denetlendi
    except Exception as e:
        logger.error(f"Clear data failed: {e}")
        raise _SyncHttpError(500, "Clear data failed") from e
    return {"status": "success", "cleared": list(report.cleared)}


def _export_csv_sync(league_id: Optional[int], data_dir: str):
    """
    CSV dışa aktarımını istekte üretir ve akıtır (ExportService, `legacy-wide-csv` profili); hiçbir dosya yazmaz
    (karar D16). Maçlar katalogdan bulunur, yükler depodan okunur: iki düzen de (eski ağaç ve v3) dahildir.
    """
    from fastapi.responses import StreamingResponse

    from src.services.export import ExportService, ExportSpec

    try:
        prepared = ExportService(store_hooks.open_store(data_dir)).prepare(ExportSpec(league_id=league_id))
    except Exception as e:
        logger.error(f"CSV export failed: {e}")
        raise _SyncHttpError(500, "CSV generation failed") from e
    if not prepared.available:
        raise _SyncHttpError(404, "No CSV data available. Run a fetch first.")
    if not prepared.rows:
        raise _SyncHttpError(404, f"No exported matches for league {league_id}.")
    filename = f"all_matches_{int(time.time())}.csv"
    if league_id:
        filename = f"league_{league_id}_{filename}"
    return StreamingResponse(
        prepared.chunks(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _run_exclusive(operation: str, fn: Callable[..., Any], *args: Any) -> Any:
    """
    `fn`'i iş deposunun veri işlemi yuvasında çalıştırır: iş çalışıyorsa ya da başka bir veri işlemi
    sürüyorsa JobStoreConflict (app.py bunu 409'a çevirir), işlem sürerken de yeni iş başlatılamaz.
    Yuva worker thread içinde alınır: istemci bağlantıyı koparsa bile dosya işlemi bitene kadar tutulur.
    """
    with _job_store.exclusive(operation):
        return fn(*args)


@router.post("/data/backup")
async def create_backup(scope: BackupScope = "all", include_env: bool = False):
    """
    Veri yedeği oluşturur ve indirilebilir zip dosyası döndürür.

    İş çalışırken veri içeren yedek reddedilir (409 job_running): iş dosya yazmayı sürdürdüğü için
    zip yarım bir indirmenin tutarsız kopyası olurdu, işin bitmesini beklemek ise isteği saatlerce
    açık tutardı. `scope=config` yalnızca işin yazmadığı ayar dosyalarını okur; her zaman alınabilir.
    """
    try:
        if scope == "config":
            return await asyncio.to_thread(_create_backup_sync, scope, include_env)
        return await asyncio.to_thread(_run_exclusive, "backup", _create_backup_sync, scope, include_env)
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
    """
    Veriyi temizler. İş çalışırken reddedilir (409 job_running): rmtree yazan işle yarışır ve iş,
    bellekteki dizinine göre silinen maçları "var" sayıp atlar.
    """
    try:
        return await asyncio.to_thread(_run_exclusive, "clear", _clear_data_sync, req.scope)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


# --- CSV Export Endpoint (W11) ---


@router.get("/export/csv")
async def export_csv(league_id: Optional[int] = None):
    """
    İndirilmiş maçların CSV dışa aktarımı, istekte üretilir; salt okunur, diske hiçbir şey yazmaz.
    league_id: yalnızca o ligin lig dizinindeki maçlar. İndirilmiş maç yoksa 404.
    """
    data_dir = config_manager.get_data_dir()
    try:
        return await asyncio.to_thread(_export_csv_sync, league_id, data_dir)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/export/csv")
async def create_csv_export(league_id: Optional[int] = None):
    """GET /api/export/csv ile aynı yanıt (bir sürüm boyunca takma ad); diske hiçbir şey yazmaz."""
    data_dir = config_manager.get_data_dir()
    try:
        return await asyncio.to_thread(_export_csv_sync, league_id, data_dir)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
