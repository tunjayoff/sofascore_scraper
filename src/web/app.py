from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import dotenv

from src.logger import get_logger

dotenv.load_dotenv()
logger = get_logger("WebApp")

app = FastAPI(
    title="SofaScore Scraper Web UI",
    description="Web interface for SofaScore Scraper",
    version="2.0.0",
)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
LEGACY_STATIC = BASE_DIR / "static"

from src.web.routes import api

app.include_router(api.router)

if LEGACY_STATIC.is_dir():
    app.mount("/legacy-static", StaticFiles(directory=str(LEGACY_STATIC)), name="legacy_static")


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0", "ui": "vue-spa" if FRONTEND_DIST.is_dir() else "missing-dist"}


if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="spa_assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
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
