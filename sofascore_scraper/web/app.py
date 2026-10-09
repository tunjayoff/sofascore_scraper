import math
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
import dotenv

from sofascore_scraper.errors import PlatformError
from sofascore_scraper.private_files import harden_secret_paths
from sofascore_scraper.logger import attach_file_handler, get_logger
from sofascore_scraper.paths import env_file_path
from sofascore_scraper.version import __version__
from sofascore_scraper.web import deps, errors, security
from sofascore_scraper.web.api import is_v1, route_path
from sofascore_scraper.web.missing_ui import MISSING_UI_HTML

dotenv.load_dotenv(env_file_path())
logger = get_logger("WebApp")
# uvicorn kendi logger'ını köke iletmez: sunucu hataları log dosyasına da yazılsın (erişim logu hariç)
attach_file_handler("uvicorn")
# .env ve tarayıcı profili yalnızca sahibince okunur (POSIX); sunucu hangi yoldan başlatılırsa başlatılsın
harden_secret_paths()


# Erişim belirteci ayarlardan okunur (sofascore_scraper/web/security.py `api_token`). `[server] token_env`in adını verdiği
# değişken boşsa ConfigError fırlar ve sunucu başlamaz: koruma bir yazım hatasıyla sessizce kapanmaz.
if 0 < len(security.api_token()) < security.MIN_TOKEN_LENGTH:
    logger.warning(
        f"{security.token_variable()} is short and guessable: use at least {security.MIN_TOKEN_LENGTH} random "
        "characters."
    )

app = FastAPI(
    title="SofaScore Scraper Web UI",
    description="Web interface for SofaScore Scraper",
    version=__version__,
)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# DNS rebinding: yalnızca bilinen Host adlarına yanıt verilir (varsayılan: yerel adlar). Liste
# açıktır: `server.allowed_hosts` ne diyorsa odur; hiçbir başlatma yolu onu sessizce "*" yapmaz
# (bkz. security.allowed_hosts_for_bind).
ALLOWED_HOSTS = security.allowed_hosts()
if "*" in ALLOWED_HOSTS:
    logger.warning("The Host allow list is off (*): every Host header is answered, no DNS rebinding protection.")


TOO_MANY_ATTEMPTS = "too_many_attempts"


def _refused_origin(request: Request, v1: bool) -> Response:
    if v1:
        return errors.error_response(request, PlatformError("forbidden_origin", "Cross-origin request rejected."))
    return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)


def _unauthorized(request: Request, v1: bool) -> Response:
    headers = {"WWW-Authenticate": "Bearer"}
    if v1:
        return errors.error_response(
            request, PlatformError("unauthorized", "An access token is required."), headers=headers,
        )
    return JSONResponse(
        {"detail": {"code": "auth_required", "message": "An access token is required."}},
        status_code=401,
        headers=headers,
    )


def _too_many_attempts(request: Request, v1: bool, wait: float) -> Response:
    """Kilit sürerken gelen deneme: belirteç değerlendirilmez. v1'de kod `unauthorized` kalır (hata tablosu)."""
    seconds = max(1, math.ceil(wait))
    message = f"Too many failed attempts. Try again in {seconds} seconds."
    headers = {"Retry-After": str(seconds)}
    if v1:
        details = {"reason": TOO_MANY_ATTEMPTS, "retry_after": seconds}
        return errors.error_response(
            request, PlatformError("unauthorized", message, details), headers={**headers, "WWW-Authenticate": "Bearer"},
        )
    return JSONResponse(
        {"detail": {"code": TOO_MANY_ATTEMPTS, "message": message, "retry_after": seconds}},
        status_code=429,
        headers=headers,
    )


def _failed_attempt(client: str) -> None:
    lock = deps.attempt_limiter.failure(client)
    if lock > 0:
        logger.warning("Too many failed access-token attempts from %s; new attempts are refused for %d s", client, lock)


async def _guarded(request: Request, call_next: RequestResponseEndpoint, path: str, v1: bool) -> Response:
    """Kaynak denetimi, erişim belirteci ve başarısız deneme sınırı; sonra rota. v1 rotasının hatası yanıta çevrilir."""
    if security.is_cross_origin_write(request.method, request.headers):
        return _refused_origin(request, v1)

    token_set = bool(security.api_token())
    client = deps.client_key(request.client)
    limiter = deps.attempt_limiter
    if token_set and security.requires_token(path):
        # Yanlış `Authorization: Bearer` bir tahmindir ve giriş denemeleriyle aynı sayaca yazılır. Kimlik
        # bilgisi taşımayan istek (arayüzün giriş öncesi istekleri) ve oturum cookie'si sayılmaz.
        bearer = deps.presents_bearer(request.headers)
        if bearer:
            wait = limiter.retry_after(client)
            if wait > 0:
                return _too_many_attempts(request, v1, wait)
        if not security.is_authenticated(request.headers, request.cookies):
            if bearer:
                _failed_attempt(client)
            return _unauthorized(request, v1)
        if bearer:
            limiter.success(client)

    try:
        return await call_next(request)
    except Exception as exc:
        if not v1:
            raise
        # v1 rotasından çıkan her hata (PlatformError, StorageError, beklenmeyen ...) hata modeliyle döner
        return errors.exception_response(request, exc)


@app.middleware("http")
async def security_boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """
    Her isteğin geçtiği tek güvenlik katmanı (kurallar: sofascore_scraper/web/security.py).

      1. CSRF: başka bir sitenin tetiklediği durum değiştiren istek (veri silme, yedek, ayar yazma,
         iş başlatma) reddedilir. Tarayıcı dışı istemciler (Origin göndermeyen curl) etkilenmez.
      2. Erişim belirteci (ayarlıysa): /api istekleri `Authorization: Bearer` ya da oturum cookie'si
         taşımalıdır (SSE başlık gönderemez; cookie ile çalışır). Aynı istemciden art arda gelen yanlış
         belirteçler bir süre reddedilir (sofascore_scraper/web/deps.py: AttemptLimiter).
      3. Güvenlik başlıkları: ret yanıtları dahil her yanıta eklenir.

    `/api/v1` yanıtları ayrıca istek kimliğini (`X-Request-Id`) taşır ve retler v1 hata modeliyle döner
    (`unauthorized`, `forbidden_origin`). v1 dışındaki yolların (`/health`, arayüzün dosyaları, var olmayan bir
    `/api` yolu) ret gövdeleri 2.x'in biçimindedir.
    """
    # Yönlendiricinin eşleştirdiği yolun kendisi. request.url, Host başlığıyla birleştirilerek kurulur:
    # "*" izin listesinde "x/y?" gibi bir Host, oradan okunan yolu değiştirip belirteç denetimini
    # atlatabilirdi. Kök yolla çalışırken (`--root-path`) `path` kökü de içerir; yönlendirici onu atar,
    # denetim de atmalı: yoksa "/<kök>/api/v1/..." belirteçsiz geçiyordu (B2, `route_path`).
    path = route_path(request.scope)
    v1 = is_v1(path)
    if v1:
        errors.assign_request_id(request)
    response = await _guarded(request, call_next, path, v1)
    for name, value in security.security_headers(path):
        response.headers[name] = value
    if v1:
        response.headers[errors.REQUEST_ID_HEADER] = errors.request_id_of(request)
    return response


# Eklenen son middleware en dışta çalışır: Host kontrolü diğer her şeyden önce
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

from sofascore_scraper.web.api import v1  # noqa: E402
from sofascore_scraper.store import JobStoreConflict  # noqa: E402

# 2.x'in `/api` yolları 3.1'de kalktı (P30): HTTP API yalnızca `/api/v1`dir
app.include_router(v1.router)
errors.install(app)


@app.exception_handler(JobStoreConflict)
async def job_store_conflict(request: Request, exc: JobStoreConflict):
    """
    Çalışan iş ya da süren veri işlemiyle çakışan istek: 409 ve makinece okunur `code`
    (job_running / data_operation_running). Ön yüz mesajı bu koda göre çevirir. v1 yollarında aynı hata
    v1 hata modeliyle döner; kilit başka bir süreçteyse sahibi `details.holder`dadır.
    """
    if is_v1(route_path(request.scope)):
        return errors.exception_response(request, exc)
    return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, status_code=409)


@app.get("/health")
async def health_check(request: Request):
    # status/version/ui: sunucunun kendisi (başlatıcı ve arayüz bunlara bakar). bridge: SofaScore'a
    # erişimin durumu (ok / degraded / blocked); throttle: süreçler arası ortak istek bütçesi.
    from sofascore_scraper import bridge_health, throttle

    if not security.is_authenticated(request.headers, request.cookies):
        # Belirteç ayarlı ve çağıran onu taşımıyor: yalnızca "sunucu ayakta" (sağlık denetimleri
        # çalışmaya devam eder); sürüm, köprü hatası ve istek bütçesi ayrıntıları verilmez
        return {"status": "ok"}

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
        "The web UI is not built (no frontend/dist): / shows a help page. "
        "To build it: cd frontend && npm install && npm run build"
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
