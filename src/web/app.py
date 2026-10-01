import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
import dotenv

from src.logger import get_logger
from src.paths import env_file_path
from src.version import __version__
from src.web.missing_ui import MISSING_UI_HTML

dotenv.load_dotenv(env_file_path())
logger = get_logger("WebApp")

app = FastAPI(
    title="SofaScore Scraper Web UI",
    description="Web interface for SofaScore Scraper",
    version=__version__,
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
        "version": __version__,
        "ui": "vue-spa" if FRONTEND_DIST.is_dir() else "missing-dist",
        "bridge": bridge_health.snapshot(),
        "throttle": throttle.status(),
    }


_assets_dir = FRONTEND_DIST / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="spa_assets")
# Derleme başlangıçta yoksa /assets bağlanmaz: sunucu çalışırken yapılan derlemenin dosyalarını da
# aşağıdaki genel rota sunar (StaticFiles, var olmayan klasörle her istekte 500 verirdi).
if not (FRONTEND_DIST / "index.html").is_file():
    logger.warning(
        "Web arayüzü derlenmemiş (frontend/dist yok): / adresinde yardım sayfası gösterilecek. "
        "Derlemek için: cd frontend && npm install && npm run build"
    )


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    # Derlemeye her istekte bakılır: sunucu çalışırken yapılan derleme yeniden başlatmadan görünür
    if full_path:
        # uvicorn '..' segmentlerini ve %2e%2e'yi normalize etmez: yol dist içinde kalmalı
        candidate = (FRONTEND_DIST / full_path).resolve()
        if candidate.is_relative_to(FRONTEND_DIST.resolve()) and candidate.is_file():
            return FileResponse(candidate)
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        # Arayüz derlenmemiş: ham JSON yerine iki dilli, kendi kendine yeten yardım sayfası.
        # 503: istenen arayüz şu an sunulamıyor (API ve /health etkilenmez).
        return HTMLResponse(MISSING_UI_HTML, status_code=503, headers={"Cache-Control": "no-store"})
    return FileResponse(index)
