import math
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
import dotenv

from src.errors import PlatformError
from src.private_files import harden_secret_paths
from src.logger import attach_file_handler, get_logger
from src.paths import env_file_path
from src.version import __version__
from src.web import deps, errors, security
from src.web.api import deprecation_headers, is_v1
from src.web.missing_ui import MISSING_UI_HTML

dotenv.load_dotenv(env_file_path())
logger = get_logger("WebApp")
# uvicorn kendi logger'ını köke iletmez: sunucu hataları log dosyasına da yazılsın (erişim logu hariç)
attach_file_handler("uvicorn")
# .env ve tarayıcı profili yalnızca sahibince okunur (POSIX); sunucu hangi yoldan başlatılırsa başlatılsın
harden_secret_paths()


def _token_from_settings() -> str:
    """
    Erişim belirtecini Settings'ten okur (`[server] token_env` bir değişken adı verebilir; varsayılan ad
    SOFASCORE_API_TOKEN) ve güvenlik modülünün süreç boyunca kullandığı değer yapar. Güvenlik modülü belirteci
    ortamdan kendisi okur (P30'a kadar); adı ayarda verilen bir değişkendeki belirteci tek başına göremez.
    Adı verilen değişken boşsa ConfigError fırlar ve sunucu başlamaz: koruma sessizce kapanmaz.
    """
    token = deps.server_token()
    if security.api_token() != token:
        security._startup_token = token
    return token


if 0 < len(_token_from_settings()) < security.MIN_TOKEN_LENGTH:
    logger.warning(
        f"{security.TOKEN_ENV} çok kısa, tahmin edilebilir: en az {security.MIN_TOKEN_LENGTH} rastgele karakter kullanın."
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
# açıktır: SOFASCORE_ALLOWED_HOSTS ne diyorsa odur; hiçbir başlatma yolu onu sessizce "*" yapmaz
# (bkz. security.allowed_hosts_for_bind).
ALLOWED_HOSTS = security.allowed_hosts()
if "*" in ALLOWED_HOSTS:
    logger.warning("Host izin listesi kapalı (*): her Host başlığına yanıt veriliyor, DNS rebinding koruması yok.")


LOGIN_PATH = "/api/auth/login"
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

    login = token_set and request.method == "POST" and path == LOGIN_PATH
    if login:
        wait = limiter.retry_after(client)
        if wait > 0:
            return _too_many_attempts(request, False, wait)
    try:
        response = await call_next(request)
    except Exception as exc:
        if not v1:
            raise
        # v1 rotasından çıkan her hata (PlatformError, StorageError, beklenmeyen ...) hata modeliyle döner
        return errors.exception_response(request, exc)
    if login:
        if response.status_code == 401:
            _failed_attempt(client)
        elif response.status_code == 200:
            limiter.success(client)
    return response


@app.middleware("http")
async def security_boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """
    Her isteğin geçtiği tek güvenlik katmanı (kurallar: src/web/security.py).

      1. CSRF: başka bir sitenin tetiklediği durum değiştiren istek (veri silme, yedek, ayar yazma,
         iş başlatma) reddedilir. Tarayıcı dışı istemciler (Origin göndermeyen curl) etkilenmez.
      2. Erişim belirteci (ayarlıysa): /api istekleri `Authorization: Bearer` ya da oturum cookie'si
         taşımalıdır (SSE başlık gönderemez; cookie ile çalışır). Aynı istemciden art arda gelen yanlış
         belirteçler bir süre reddedilir (src/web/deps.py: AttemptLimiter).
      3. Güvenlik başlıkları: ret yanıtları dahil her yanıta eklenir.

    `/api/v1` yanıtları ayrıca istek kimliğini (`X-Request-Id`) taşır ve retler v1 hata modeliyle döner
    (`unauthorized`, `forbidden_origin`); eski yolların ret gövdeleri değişmez. Eski her rotanın yanıtına
    `Deprecation` ve halefini gösteren `Link` başlıkları eklenir (src/web/api/__init__.py).
    """
    # Yönlendiricinin eşleştirdiği yolun kendisi. request.url, Host başlığıyla birleştirilerek kurulur:
    # "*" izin listesinde "x/y?" gibi bir Host, oradan okunan yolu değiştirip belirteç denetimini
    # atlatabilirdi.
    path = request.scope["path"]
    v1 = is_v1(path)
    if v1:
        errors.assign_request_id(request)
    response = await _guarded(request, call_next, path, v1)
    for name, value in security.security_headers(path, FRONTEND_DIST):
        response.headers[name] = value
    if v1:
        response.headers[errors.REQUEST_ID_HEADER] = errors.request_id_of(request)
    else:
        for name, value in deprecation_headers(path, request.method):
            response.headers[name] = value
    return response


# Eklenen son middleware en dışta çalışır: Host kontrolü diğer her şeyden önce
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

from src.web.api import v1  # noqa: E402
from src.web.jobs import JobStoreConflict  # noqa: E402
from src.web.routes import api  # noqa: E402

# Eski yollar bir sürüm daha durur: belgede `deprecated`, yanıtlarında Deprecation ve Link başlıkları
app.include_router(api.router, deprecated=True)
app.include_router(v1.router)
errors.install(app)


@app.exception_handler(JobStoreConflict)
async def job_store_conflict(request: Request, exc: JobStoreConflict):
    """
    Çalışan iş ya da süren veri işlemiyle çakışan istek: 409 ve makinece okunur `code`
    (job_running / data_operation_running). Ön yüz mesajı bu koda göre çevirir. v1 yollarında aynı hata
    v1 hata modeliyle döner; kilit başka bir süreçteyse sahibi `details.holder`dadır.
    """
    if is_v1(request.scope["path"]):
        return errors.exception_response(request, exc)
    return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, status_code=409)


@app.get("/health")
async def health_check(request: Request):
    # status/version/ui: sunucunun kendisi (başlatıcı ve arayüz bunlara bakar). bridge: SofaScore'a
    # erişimin durumu (ok / degraded / blocked); throttle: süreçler arası ortak istek bütçesi.
    from src import bridge_health, throttle

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
