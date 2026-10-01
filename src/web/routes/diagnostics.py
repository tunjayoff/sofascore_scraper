"""
Log ve tanılama uç noktaları (salt okunur).

Hiçbiri dosya yolu parametresi almaz: okunan tek dosya src.logger'ın yazdığı log dosyasıdır
(ve çevrilmiş eskileri). Yanıtlar src.redact'ten geçer; boyutları sınırlıdır.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Query, Response

from src import diagnostics

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/logs")
def recent_logs(
    limit: int = Query(200, ge=1, le=diagnostics.MAX_LOG_ENTRIES, description="En fazla kaç kayıt (en yeniler)"),
    level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = Query(
        None, description="Yalnızca bu seviye ve üstü"
    ),
):
    """Log dosyasının son kayıtları, eskiden yeniye. Dosya logu kapalıysa `enabled: false` ve boş liste."""
    return diagnostics.read_log_entries(limit=limit, min_level=level)


@router.get("/diagnostics")
def diagnostics_summary():
    """Tanılama özeti (paketteki diagnostics.json ile aynı içerik)."""
    return diagnostics.collect(source="web")


@router.get("/diagnostics/bundle")
def diagnostics_bundle(
    log_lines: int = Query(
        diagnostics.DEFAULT_TAIL_LINES, ge=0, le=diagnostics.MAX_TAIL_LINES, description="Pakete giren log satırı"
    ),
):
    """Hata bildirimine eklenecek zip: diagnostics.json + log_tail.txt + README.txt."""
    data = diagnostics.build_bundle(source="web", log_lines=log_lines)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{diagnostics.bundle_filename()}"',
            "Cache-Control": "no-store",
        },
    )
