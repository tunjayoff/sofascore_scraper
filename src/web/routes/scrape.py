"""Arka plan işleri: başlatma, durum, iptal, iş geçmişi ve BrowserBridge testleri."""
from __future__ import annotations

import asyncio
import json
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from src.version import __version__
from src.web.routes.common import (
    _job_store,
    _refresh_scraper_state,
    config_manager,
    logger,
)

router = APIRouter(prefix="/api", tags=["api"])


class FetchSelection(BaseModel):
    league_id: int
    season_ids: Optional[List[int]] = None
    match_ids: Optional[List[int]] = None


class FetchRequest(BaseModel):
    league_id: Optional[int] = None
    mode: Literal["full", "details"] = "full"
    selections: Optional[List[FetchSelection]] = None


@router.get("/scrape/status")
async def get_scrape_status():
    """Get the current status of the background scraper."""
    return _refresh_scraper_state()


@router.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)):
    """Recent scrape jobs (newest first)."""
    return {"jobs": _job_store.list_jobs(limit=limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/scrape/stream")
async def scrape_status_stream(request: Request):
    """SSE endpoint for real-time scraper status updates."""
    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError:
        raise HTTPException(status_code=501, detail="SSE not available, use polling instead")

    async def event_generator():
        last_state_json = None
        while True:
            if await request.is_disconnected():
                break
            state = _refresh_scraper_state()
            current_json = json.dumps(state, default=str)
            if current_json != last_state_json:
                yield {"event": "update", "data": current_json}
                last_state_json = current_json
                if not state.get("is_running") and state.get("status") != "Running":
                    yield {"event": "done", "data": current_json}
                    break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.post("/scrape/cancel")
def cancel_scrape():
    """Çalışan background fetch işlemini iptal eder."""
    if not _job_store.request_cancel():
        raise HTTPException(status_code=400, detail="No scraping process is running.")
    _refresh_scraper_state()
    return {"status": "cancelling", "message": "Cancel signal sent."}


@router.post("/fetch")
async def trigger_fetch(payload: FetchRequest):
    """Trigger a background fetch operation. Mode 'full' or 'details'."""
    # async kalmalı: "çalışıyor mu" kontrolü ile create_running arasında await yok, bu yüzden
    # olay döngüsünde atomik. Threadpool'da iki eşzamanlı istek iki iş başlatabilirdi.
    import threading

    from src.web.fetch_job import run_fetch_job

    if _refresh_scraper_state().get("is_running"):
        raise HTTPException(status_code=409, detail="Scraping process is already running.")
    job_id = _job_store.create_running(payload.model_dump())
    _refresh_scraper_state()

    # Dedicated thread: long scrapes must not occupy Starlette's shared threadpool
    # (otherwise /dashboard, /stats, page navigations starve while fetch runs).
    threading.Thread(target=run_fetch_job, args=(job_id, payload), name="sofascore-fetch", daemon=True).start()
    return {"status": "started", "job_id": job_id, "message": "Update started."}


@router.get("/status")
async def system_status():
    """Get system status info."""
    return {
        # Tek kaynak: pyproject.toml (src/version.py); /health ve OpenAPI belgesi de aynı değeri verir
        "version": __version__,
        "leagues_count": len(config_manager.get_leagues()),
        "language": config_manager.get_language()
    }


@router.get("/bypass/status")
async def bypass_status():
    """Get Cloudflare Turnstile & BrowserBridge status."""
    from src import bridge_health
    from src.challenge_solver import get_cached_token, _is_token_valid
    token = get_cached_token()
    return {
        "status": "ready",
        "has_token": bool(token),
        "is_valid": _is_token_valid(),
        "mechanism": "BrowserBridge (Chrome persistent context + Turnstile auto-solve)",
        # ok / degraded / blocked + son başarılı istek, ardışık başarısızlık, son hata (web arayüzü afişi)
        "health": bridge_health.snapshot(),
    }


def _browser_ready() -> bool:
    """Köprünün açık bir tarayıcı sayfası var mı? (Yalnızca okur; tarayıcı başlatmaz.)"""
    from src.challenge_solver import BrowserBridge

    try:
        page = BrowserBridge.get_instance().page
        return bool(page) and not page.is_closed()
    except Exception:
        return False


@router.post("/bypass/test")
async def bypass_test():
    """
    Test live connectivity and challenge solving through BrowserBridge.

    SofaScore'a TEK bir canlı istek atar; yalnızca kullanıcı istediğinde çağrılır (Ayarlar →
    Bağlantı testi), hiçbir şey bunu kendiliğinden ya da zamanlayıcıyla çağırmaz. Sonuç her
    zaman 200 ile döner: `success`, başarısızsa `reason` (src/web/upstream.py: blocked / browser /
    network / upstream), tarayıcı ve challenge durumu ve köprü sağlık görüntüsü.
    """
    from src import bridge_health
    from src.challenge_solver import _is_token_valid, fetch_api_via_browser, get_cached_token
    from src.web import upstream

    before = bridge_health.snapshot()
    reason: Optional[str] = None
    events_count = 0
    try:
        data = await fetch_api_via_browser("/sport/football/events/live")
    except Exception as e:
        # Tarayıcı başlatılamadı (köprü bunu sağlık durumuna yazar) ya da istek zaman aşımına uğradı
        logger.warning(f"Bağlantı testi başarısız: {e.__class__.__name__}: {e}")
        reason = upstream.BROWSER if upstream.browser_failed_since(before) else upstream.NETWORK
    else:
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            events_count = len(data["events"])
        elif data is None:
            # Köprü yalnızca 403'ü sağlık durumuna yazar: yeni bir kayıt varsa SofaScore reddetti.
            # Yoksa yanıt alınamadı (ağ hatası, zaman aşımı; nadiren 429/5xx): köprü ayrıntı vermez.
            after = bridge_health.snapshot()
            refused = (
                after["consecutive_failures"] > before["consecutive_failures"]
                or after["last_failure_at"] != before["last_failure_at"]
            )
            reason = upstream.BLOCKED if refused else upstream.NETWORK
        else:
            reason = upstream.UPSTREAM  # yanıt geldi ama canlı maç listesi değil

    result = {
        "success": reason is None,
        "reason": reason,
        "message": "BrowserBridge connection verified successfully." if reason is None else upstream.detail(reason)["message"],
        "browser_ready": _browser_ready(),
        "has_token": bool(get_cached_token()),
        "is_valid": _is_token_valid(),
        "health": bridge_health.snapshot(),
    }
    if reason is None:
        result["events_count"] = events_count
    return result
