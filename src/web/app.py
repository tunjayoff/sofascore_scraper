import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
import dotenv

from src.logger import get_logger
from src.paths import env_file_path

dotenv.load_dotenv(env_file_path())
logger = get_logger("WebApp")

app = FastAPI(
    title="SofaScore Scraper Web UI",
    description="Web interface for SofaScore Scraper",
    version="2.0.0",
)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# DNS rebinding: yalnızca yerel host adlarına yanıt ver. main.py --host ile LAN'a açıldığında
# SOFASCORE_ALLOWED_HOSTS genişletilir ("*" = hepsi).
_DEFAULT_HOSTS = "localhost,127.0.0.1,[::1]"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("SOFASCORE_ALLOWED_HOSTS", _DEFAULT_HOSTS).split(",") if h.strip()]

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def reject_cross_origin_writes(request: Request, call_next):
    """
    CSRF: tarayıcılar cross-origin POST'larda Origin gönderir. Origin, isteğin Host'u ile
    eşleşmiyorsa başka bir sitenin tetiklediği istektir — veri silme, yedek, ayar yazma engellenir.
    """
    if request.method in _UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    return await call_next(request)


# Eklenen son middleware en dışta çalışır: Host kontrolü Origin kontrolünden önce
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

from src.web.routes import api  # noqa: E402

app.include_router(api.router)


@app.get("/health")
async def health_check():
    # status/version/ui: sunucunun kendisi (başlatıcı ve arayüz bunlara bakar). bridge: SofaScore'a
    # erişimin durumu (ok / degraded / blocked); throttle: süreçler arası ortak istek bütçesi.
    from src import bridge_health, throttle

    return {
        "status": "ok",
        "version": "2.0.0",
        "ui": "vue-spa" if FRONTEND_DIST.is_dir() else "missing-dist",
        "bridge": bridge_health.snapshot(),
        "throttle": throttle.status(),
    }


if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="spa_assets")

    _DIST_ROOT = FRONTEND_DIST.resolve()

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            # uvicorn '..' segmentlerini ve %2e%2e'yi normalize etmez: yol dist içinde kalmalı
            candidate = (FRONTEND_DIST / full_path).resolve()
            if candidate.is_relative_to(_DIST_ROOT) and candidate.is_file():
                return FileResponse(candidate)
        index = FRONTEND_DIST / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=500, detail="SPA index missing")
        return FileResponse(index)
else:
    logger.warning("frontend/dist missing — run: cd frontend && npm run build")

    @app.get("/")
    async def missing_frontend():
        return {
            "error": "SPA not built",
            "hint": "cd frontend && npm install && npm run build",
            "api": "/api",
            "health": "/health",
        }
