from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
import dotenv

from src.logger import attach_file_handler, get_logger
from src.paths import env_file_path
from src.version import __version__
from src.web import security
from src.web.missing_ui import MISSING_UI_HTML

dotenv.load_dotenv(env_file_path())
logger = get_logger("WebApp")
# uvicorn kendi logger'ını köke iletmez: sunucu hataları log dosyasına da yazılsın (erişim logu hariç)
attach_file_handler("uvicorn")

app = FastAPI(
    title="SofaScore Scraper Web UI",
    description="Web interface for SofaScore Scraper",
    version=__version__,
)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# DNS rebinding: yalnızca bilinen Host adlarına yanıt verilir (varsayılan: yerel adlar). Liste
# açıktır: SOFASCORE_ALLOWED_HOSTS ne diyorsa odur; hiçbir başlatma yolu onu sessizce "*" yapmaz
# (bkz. security.allowed_hosts_for_bind).
ALLOWED_HOSTS = security.allowed_hosts()
if "*" in ALLOWED_HOSTS:
    logger.warning("Host izin listesi kapalı (*): her Host başlığına yanıt veriliyor, DNS rebinding koruması yok.")


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    """
    Her isteğin geçtiği tek güvenlik katmanı (kurallar: src/web/security.py).

      1. CSRF: başka bir sitenin tetiklediği durum değiştiren istek (veri silme, yedek, ayar yazma,
         iş başlatma) reddedilir. Tarayıcı dışı istemciler (Origin göndermeyen curl) etkilenmez.
      2. Güvenlik başlıkları: ret yanıtları dahil her yanıta eklenir.
    """
    path = request.url.path
    if security.is_cross_origin_write(request.method, request.headers):
        response = JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    else:
        response = await call_next(request)
    for name, value in security.security_headers(path, FRONTEND_DIST):
        response.headers[name] = value
    return response


# Eklenen son middleware en dışta çalışır: Host kontrolü diğer her şeyden önce
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

from src.web.jobs import JobStoreConflict  # noqa: E402
from src.web.routes import api  # noqa: E402

app.include_router(api.router)


@app.exception_handler(JobStoreConflict)
async def job_store_conflict(_request: Request, exc: JobStoreConflict):
    """
    Çalışan iş ya da süren veri işlemiyle çakışan istek: 409 ve makinece okunur `code`
    (job_running / data_operation_running). Ön yüz mesajı bu koda göre çevirir.
    """
    return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, status_code=409)


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
