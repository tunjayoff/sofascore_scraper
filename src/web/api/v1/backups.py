"""
API v1: yedekler (docs/design/02-services.md bölüm 6; docs/design/05-web-ui.md 6.11; plan maddesi P21).

    GET /api/v1/backups          veri dizininin `backups/` altındaki yedekler, en yeni önce
    GET /api/v1/backups/{name}   yedek dosyasının kendisi (zip)

Yedek almak bir iştir (`POST /jobs {"kind": "backup"}`), geri yüklemeyi denemek de (`{"kind": "restore",
"spec": {"name", "dry_run": true}}`). Liste ve dosya BackupService'ten ve Store'dan gelir (biçim 2'de
`backup.json`). `.env` taşıyan yedeğin adı bunu söyler (`with_env`): o dosya gizli değer taşıyabilir.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field

from src.errors import NotFoundError
from src.web import deps
from src.web.api.v1 import PageInfo
from src.web.api.v1.downloads import Download, download_responses
from src.web.errors import error_responses

router = APIRouter(tags=["backups"])

ZIP = "application/zip"


class BackupRecord(BaseModel):
    """A backup zip in the data directory's `backups/`."""

    name: str
    scope: str = Field(description="all, state, data, config, seasons, matches or match_details.")
    created_at_utc: Optional[str] = Field(default=None, description="The time in the file name, as UTC.")
    bytes: int
    format: Optional[int] = Field(default=None, description="2 (with backup.json), 1 (2.x), null: unreadable zip.")
    with_env: bool = Field(description="The zip holds `.env`, which can carry secrets.")


class BackupListResponse(BaseModel):
    data: List[BackupRecord]
    page: PageInfo


def _utc(local: str) -> Optional[str]:
    """Addaki yerel zaman (saat dilimi yok) → UTC metni."""
    try:
        moment = datetime.fromisoformat(local)
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/backups", response_model=BackupListResponse, operation_id="listBackups", summary="List backups")
def list_backups() -> BackupListResponse:
    """The backups of the data directory, newest first."""
    from src.services.backup import BackupService

    found = BackupService(deps.store()).list()
    return BackupListResponse(
        data=[BackupRecord(name=b.name, scope=b.scope, created_at_utc=_utc(b.created_at), bytes=b.size,
                           format=b.format, with_env=b.with_env) for b in found],
        page=PageInfo(limit=len(found), next_cursor=None),
    )


@router.get(
    "/backups/{name}",
    operation_id="downloadBackup",
    summary="Download a backup",
    response_class=Download,
    status_code=200,
    responses={**download_responses(ZIP, "The backup zip."), **error_responses("not_found")},
)
def download_backup(name: str = Path(max_length=200)) -> Download:
    """The backup zip; 404 for a name that is not a backup of this data directory."""
    from src.store import BackupNotFound

    try:
        path = deps.store().backup.path_of(name)
    except BackupNotFound:
        raise NotFoundError("No backup has this name.", {"name": name}) from None
    return Download(path, filename=name, media_type=ZIP, details={"name": name})


__all__ = ["router"]
