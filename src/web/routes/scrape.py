"""Arka plan işleri: başlatma, durum, iptal, iş geçmişi ve BrowserBridge testleri."""
from __future__ import annotations

import asyncio
import json
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from src.web.routes.common import (
    _job_store,
    _refresh_scraper_state,
    config_manager,
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
        "version": "1.0.0",
        "leagues_count": len(config_manager.get_leagues()),
        "language": config_manager.get_language()
    }


@router.get("/bypass/status")
async def bypass_status():
    """Get Cloudflare Turnstile & BrowserBridge status."""
    from src.challenge_solver import get_cached_token, _is_token_valid
    token = get_cached_token()
    return {
        "status": "ready",
        "has_token": bool(token),
        "is_valid": _is_token_valid(),
        "mechanism": "BrowserBridge (Chrome persistent context + Turnstile auto-solve)",
    }


@router.post("/bypass/test")
async def bypass_test():
    """Test live connectivity and challenge solving through BrowserBridge."""
    from src.challenge_solver import fetch_api_via_browser
    data = await fetch_api_via_browser("/sport/football/events/live")
    if data and "events" in data:
        return {
            "success": True,
            "events_count": len(data.get("events", [])),
            "message": "BrowserBridge connection verified successfully.",
        }
    return {
        "success": False,
        "message": "BrowserBridge test failed to retrieve live data.",
    }
