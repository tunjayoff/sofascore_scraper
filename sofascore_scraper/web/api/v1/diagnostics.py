"""
API v1: log ve tanılama (docs/design/02-services.md bölüm 6; docs/design/05-web-ui.md 6.14; plan maddesi P21).

    GET /api/v1/logs                 log dosyasının son kayıtları (eskiden yeniye), seviye süzgeciyle
    GET /api/v1/diagnostics          tanılama özeti (paketteki diagnostics.json ile aynı içerik)
    GET /api/v1/diagnostics/bundle   hata bildirimine eklenecek zip

Hiçbiri dosya yolu parametresi almaz: okunan tek dosya sofascore_scraper.logger'ın yazdığı log dosyasıdır (ve çevrilmiş
eskileri). Yanıtlar sofascore_scraper.redact'ten geçer (gizli değerler ve makine adları maskelenir); boyutları sınırlıdır.
Eski `/api/logs`, `/api/diagnostics` ve `/api/diagnostics/bundle`ın halefleridir.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from sofascore_scraper import diagnostics
from sofascore_scraper.web.errors import error_responses

router = APIRouter(tags=["diagnostics"])

ZIP = "application/zip"


class LogEntry(BaseModel):
    time: str
    level: str
    pid: int
    logger: str
    message: str = Field(description="With secrets masked; a traceback follows its line.")


class LogTail(BaseModel):
    """The newest entries of the log file, oldest first."""

    enabled: bool = Field(description="False when file logging is off; `entries` is then empty.")
    file: Optional[str] = None
    level: Optional[str] = Field(default=None, description="The level the server logs at.")
    min_level: Optional[str] = None
    count: int
    entries: List[LogEntry]


class LogTailResponse(BaseModel):
    data: LogTail


class DiagnosticsResponse(BaseModel):
    data: Dict[str, Any] = Field(description="The diagnostics summary; the same as diagnostics.json of the bundle.")


@router.get("/logs", response_model=LogTailResponse, operation_id="listLogs", summary="Read the log",
            responses=error_responses("invalid_request"))
def read_logs(
    limit: int = Query(200, ge=1, le=diagnostics.MAX_LOG_ENTRIES, description="Newest entries to return."),
    level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = Query(
        None, description="Only this level and above."),
) -> LogTailResponse:
    """The newest entries of the log file, oldest first. With file logging off, `enabled` is false."""
    return LogTailResponse(data=LogTail.model_validate(diagnostics.read_log_entries(limit=limit, min_level=level)))


@router.get("/diagnostics", response_model=DiagnosticsResponse, operation_id="getDiagnostics",
            summary="Diagnostics summary")
def get_diagnostics() -> DiagnosticsResponse:
    """Versions, runtime, settings (secrets masked), data directory, logging, bridge, request budget, checks, jobs."""
    return DiagnosticsResponse(data=diagnostics.collect(source="web"))


@router.get(
    "/diagnostics/bundle",
    operation_id="downloadDiagnosticsBundle",
    summary="Download the diagnostics bundle",
    response_class=Response,
    responses={200: {"description": "A zip: diagnostics.json, log_tail.txt, README.txt.",
                     "content": {ZIP: {"schema": {"type": "string", "format": "binary"}}}}},
)
def diagnostics_bundle(
    log_lines: int = Query(diagnostics.DEFAULT_TAIL_LINES, ge=0, le=diagnostics.MAX_TAIL_LINES,
                           description="Log lines in the bundle."),
) -> Response:
    """A zip to attach to a bug report; secrets and host names are masked."""
    data = diagnostics.build_bundle(source="web", log_lines=log_lines)
    return Response(content=data, media_type=ZIP, headers={
        "Content-Disposition": f'attachment; filename="{diagnostics.bundle_filename()}"',
        "Cache-Control": "no-store",
    })


__all__ = ["router"]
