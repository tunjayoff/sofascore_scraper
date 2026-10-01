"""
Sofascore Turnstile challenge çözücü ve tarayıcı köprüsü (BrowserBridge).

Sofascore API'si curl isteklerini (TLS/JA4 parmak izi) 403 + challenge ile reddeder;
istekler gerçek bir tarayıcının içinden fetch() ile yapılmalıdır.

Tarayıcıyı Scrapling'in StealthySession'ı açar (patchright + gizlilik ayarları): her
ortamda headless çalışır — masaüstünde ve ekranı olmayan sunucuda aynı yol. Challenge,
sofascore.com/captcha.html sayfasındaki gömülü Turnstile'ı Scrapling'in solve_cloudflare
özelliğiyle geçerek çözülür; sonuç sofa_captcha cookie'si ve JWT token'dır. Standart
Playwright ile headless modda Turnstile hiç geçilemiyordu (ölçüm: 0/12 istek).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Dict, Optional

from src import bridge_health, throttle
from src.logger import get_logger

logger = get_logger("ChallengeSolver")

# Token önbelleği
_cached_token: Optional[str] = None
_cached_at: float = 0
_TOKEN_TTL_SECONDS = 3500

DEFAULT_PROFILE_DIR = os.getenv(
    "SOFASCORE_BROWSER_PROFILE", os.path.expanduser("~/.cache/sofascore_scraper/chrome_profile")
)
HOME_URL = "https://www.sofascore.com/tr"
CAPTCHA_URL = "https://www.sofascore.com/captcha.html?redirectUrl=https%3A%2F%2Fwww.sofascore.com%2Ftr"
# Çözümün API'yi gerçekten açtığını doğrulamak için küçük bir uç nokta
_PROBE_URL = "https://www.sofascore.com/api/v1/unique-tournament/17/seasons"

# Zaman aşımları (saniye). Tarayıcı başlatma (ilk challenge çözümü dahil), istek başına
# zaman aşımının dışında tutulur: soğuk bir başlatmayı yarıda kesmek profili kilitli bırakır.
STARTUP_TIMEOUT = 150.0
REQUEST_TIMEOUT = 120.0
SOLVE_TIMEOUT = 90.0
_JS_FETCH_TIMEOUT_MS = 20000
# Başlatma başarısız olduysa her 403'te yeniden denenmez
_LAUNCH_RETRY_AFTER = 300.0
# Yeni çözülmüş bir token, hemen ardından gelen 403 dalgası için yeniden kullanılır
_FRESH_TOKEN_SECONDS = 30.0
# Başarısız bir çözümden sonra bu süre yeniden denenmez (her istek 10-90 sn yakmasın)
_SOLVE_RETRY_AFTER = 180.0

# Tarayıcının HTTP önbelleği: durum taşıyan yanıtlar (maç, canlı liste, sezon listeleri) max-age boyunca
# (sezon listelerinde 60 sn) önbellekten dönerse biten maç o süre "devam ediyor" görünür (issue #6).
# Yalnızca maçın durumundan bağımsız statik veri önbelleği kullanır.
_STATIC_PATH_RE = re.compile(r"/(seasons|rounds)/?$")


def cache_mode_for(url: str) -> str:
    """fetch() cache seçeneği: statik veri için tarayıcı varsayılanı, diğer her şey için no-store."""
    path = url.split("?", 1)[0]
    return "default" if _STATIC_PATH_RE.search(path) else "no-store"


_FETCH_JS = """async ([targetUrl, xReq, xCap, timeoutMs, cacheMode]) => {
    const headers = { "x-requested-with": xReq };
    if (xCap) headers["x-captcha"] = xCap;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        const resp = await fetch(targetUrl, { headers, signal: ctrl.signal, cache: cacheMode || "no-store" });
        return {
            status: resp.status,
            ok: resp.ok,
            data: resp.ok ? await resp.json() : null,
            text: !resp.ok ? await resp.text() : null
        };
    } catch (err) {
        return { status: 0, ok: false, data: null, text: String(err) };
    } finally {
        clearTimeout(timer);
    }
}"""


def _is_token_valid() -> bool:
    """Önbellekteki token hâlâ geçerli mi?"""
    if not _cached_token:
        return False
    if time.time() - _cached_at > _TOKEN_TTL_SECONDS:
        return False
    try:
        import base64
        payload = _cached_token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("exp", 0) > time.time() + 60
    except Exception:
        return False


def get_cached_token() -> Optional[str]:
    """Geçerli bir önbellek tokeni varsa döndürür."""
    if _is_token_valid():
        return _cached_token
    return None


def _headless() -> bool:
    # Her ortamda aynı yol: headless. Hata ayıklamak için pencereyi görmek isterseniz 1 yapın.
    return os.getenv("SOFASCORE_BROWSER_HEADED", "").lower() not in ("1", "true", "yes")


class BrowserBridge:
    """
    Scrapling StealthySession üzerinden Sofascore API köprüsü.
    Tek bir sayfayı açık tutar, API isteklerini o sayfadan fetch() ile yapar ve 403
    challenge geldiğinde tek bir çözümü tüm bekleyen isteklerle paylaşır.
    """

    _instance: Optional["BrowserBridge"] = None

    def __init__(self, profile_dir: str = DEFAULT_PROFILE_DIR):
        self.profile_dir = profile_dir
        self.session: Any = None
        self.context: Any = None
        self.page: Any = None
        self.token: Optional[str] = None
        self._token_at: float = 0.0
        self._init_lock = asyncio.Lock()
        self._solve_task: Optional["asyncio.Future[Optional[str]]"] = None
        self._launch_failed_at: float = 0.0
        self._solve_failed_at: float = 0.0
        os.makedirs(self.profile_dir, exist_ok=True)

    @classmethod
    def get_instance(cls, profile_dir: str = DEFAULT_PROFILE_DIR) -> "BrowserBridge":
        if cls._instance is None:
            cls._instance = cls(profile_dir=profile_dir)
        return cls._instance

    async def ensure_ready(self) -> None:
        """Tarayıcı oturumunun hazır ve açık olduğundan emin olur."""
        if self.page and not self.page.is_closed():
            return

        async with self._init_lock:
            if self.page and not self.page.is_closed():
                return
            if self._launch_failed_at and time.time() - self._launch_failed_at < _LAUNCH_RETRY_AFTER:
                bridge_health.record_failure(bridge_health.KIND_BROWSER, "tarayıcı başlatılamadı (yeniden deneme bekleniyor)")
                raise RuntimeError(
                    "BrowserBridge başlatılamadı (yakın zamanda denendi). "
                    "Tarayıcı kurulu mu? `python -m playwright install chromium`"
                )
            # Sayfası kapanmış eski oturum: yeniden başlatmadan önce kapat (profil kilidi)
            await self.close()
            try:
                await self._launch()
            except BaseException as e:
                # Yarım kalan başlatma (hata veya iptal) tarayıcı sürecini ve profil kilidini bırakmasın
                self._launch_failed_at = time.time()
                await self.close()
                if isinstance(e, Exception):  # iptal bir sağlık sinyali değil
                    bridge_health.record_failure(bridge_health.KIND_BROWSER, f"{e.__class__.__name__}: {e}")
                raise
            self._launch_failed_at = 0.0

    async def _launch(self) -> None:
        from scrapling.fetchers import AsyncStealthySession

        headless = _headless()
        logger.info(f"Tarayıcı oturumu başlatılıyor (Scrapling StealthySession, headless={headless})...")
        self.session = AsyncStealthySession(
            headless=headless,
            solve_cloudflare=True,
            user_data_dir=self.profile_dir,
            proxy=_proxy_settings(),
            block_webrtc=True,
            timeout=60000,
            max_pages=2,
        )
        await self.session.start()
        # Scrapling'in oturum bağlamı: kendi sayfamızı bunun içinde açıp tekrar tekrar kullanırız
        self.context = getattr(self.session, "context", None)
        if self.context is None:
            raise RuntimeError("Scrapling oturumu tarayıcı bağlamı açmadı (sürüm uyumsuzluğu?)")

        # Profilde çözüm yoksa challenge'ı API isteklerinden önce çöz
        solved_now = None
        if not await self._token_from_context():
            solved_now = await self._solve_on_captcha_page()

        self.page = await self.context.new_page()
        try:
            await self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"Sofascore anasayfa açılış uyarısı: {e}")
        token = await self._token_from_context()
        if token:
            self._set_token(token)
            # Profilden gelen token süresi dolmuş olabilir: "az önce çözüldü" sayılmasın,
            # yoksa ilk 403'te _FRESH_TOKEN_SECONDS boyunca yeni çözüm yapılmaz
            if token != solved_now:
                self._token_at = 0.0

    async def _token_from_context(self) -> Optional[str]:
        """Çözümün sonucu: sofa_captcha cookie'si (sayfa içi fetch'ler onu kendiliğinden gönderir)."""
        try:
            cookies = await self.context.cookies("https://www.sofascore.com")
        except Exception:
            return None
        return next((c["value"] for c in cookies if c.get("name") == "sofa_captcha" and c.get("value")), None)

    async def _solve_on_captcha_page(self) -> Optional[str]:
        """captcha.html'deki gömülü Turnstile'ı Scrapling'e çözdürür; token'ı (varsa) döndürür."""
        before = await self._token_from_context()
        try:
            await asyncio.wait_for(
                # network_idle kapalı: captcha sayfası ana sayfaya yönlenirse reklam/websocket trafiği
                # ağı hiç boşaltmaz ve çözüm Scrapling'in tüm zaman aşımını bekler (ölçüm: 65 sn).
                # Çözücü kendi içinde kısa bir networkidle beklemesi yapıyor.
                self.session.fetch(CAPTCHA_URL, solve_cloudflare=True, network_idle=False, google_search=False),
                SOLVE_TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"captcha.html çözümü hata verdi: {e.__class__.__name__}: {e}")
        # Cookie, widget geçtikten kısa süre sonra yazılır
        for _ in range(20):
            token = await self._token_from_context()
            if token and token != before:
                return token
            await asyncio.sleep(0.5)
        # Değer değişmemiş olabilir (sunucu aynı cookie'yi geçerli saydı): başarısız sayma — asıl karar
        # isteğin yeniden denenmesinde. Aksi halde _SOLVE_RETRY_AFTER boyunca hiç çözüm yapılmazdı.
        return await self._token_from_context()

    def _set_token(self, token: str) -> None:
        global _cached_token, _cached_at
        self.token = token
        self._token_at = time.time()
        # curl_cffi istekleri de bu tokeni kullansın (get_request_headers → get_cached_token)
        _cached_token = token
        _cached_at = self._token_at

    async def solve_challenge(self) -> Optional[str]:
        """
        Turnstile challenge'ını çözer ve geçerli token'ı döndürür.

        Aynı anda gelen 403'lerin hepsi aynı çözümü bekler. Az önce çözülmüş bir token
        doğrudan döndürülür; başarısız bir çözümden sonra bir süre yeniden denenmez.
        """
        await self.ensure_ready()
        if self.token and time.time() - self._token_at < _FRESH_TOKEN_SECONDS:
            return self.token
        if self._solve_failed_at and time.time() - self._solve_failed_at < _SOLVE_RETRY_AFTER:
            return None
        if self._solve_task is None or self._solve_task.done():
            self._solve_task = asyncio.ensure_future(self._solve_challenge())
        # shield: bir bekleyenin iptali diğerlerinin beklediği çözümü iptal etmesin
        return await asyncio.shield(self._solve_task)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        """
        Köprü sayfasında JS çalıştırır. Sayfa kendi kendine yönlenirse (SofaScore ana sayfası
        zaman zaman yeniden yüklenir) süren çağrı "Execution context was destroyed" ile düşer:
        sayfanın yüklenmesi beklenip bir kez daha denenir; sayfa sofascore.com dışına çıktıysa
        ana sayfaya geri dönülür.
        """
        for attempt in range(3):
            try:
                return await self.page.evaluate(script, arg)
            except Exception as e:
                msg = str(e)
                if attempt == 2 or not any(k in msg for k in ("Execution context was destroyed", "navigation", "Target page, context or browser has been closed")):
                    raise
                logger.info(f"Köprü sayfası yönlendi; yeniden deneniyor ({attempt + 1}/2)")
                if self.page.is_closed():
                    self.page = None
                    await self.ensure_ready()
                    continue
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                    if not self.page.url.startswith("https://www.sofascore.com"):
                        await self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception as le:
                    logger.warning(f"Köprü sayfası toparlanamadı: {le}")

    async def _api_fetch(self, url: str, cache_mode: str) -> Dict[str, Any]:
        """
        Köprüden çıkan tek API isteği. Tarayıcıdan giden her istek buradan geçer: önce süreçler
        arası ortak bütçeden sıra alınır (src/throttle.py), sonra sayfada fetch() çalışır.
        """
        await throttle.wait_async()
        x_req = hashlib.sha256(str(int(time.time()) // 1800).encode("utf-8")).hexdigest()[:6]
        return await self.evaluate(_FETCH_JS, [url, x_req, self.token, _JS_FETCH_TIMEOUT_MS, cache_mode])

    async def _api_unlocked(self) -> bool:
        """Çözümden sonra API gerçekten açıldı mı? (Sayfa henüz yoksa doğrulanamaz: evet say.)"""
        if self.page is None or self.page.is_closed():
            return True
        res = await self._api_fetch(_PROBE_URL, "no-store")
        return res.get("status") != 403

    async def _solve_challenge(self) -> Optional[str]:
        logger.info("Cloudflare Turnstile challenge çözülüyor (captcha.html)...")
        token = await self._solve_on_captcha_page()
        if token:
            self._set_token(token)
        if token and not await self._api_unlocked():
            # Profildeki süresi geçmiş cookie: captcha.html challenge göstermeden yönlenir ve yeni
            # cookie üretilmez. Cookie silinip challenge baştan çözülür.
            logger.info("sofa_captcha cookie'si hâlâ reddediliyor; silinip challenge yeniden çözülüyor")
            await self.context.clear_cookies(name="sofa_captcha")
            token = await self._solve_on_captcha_page()
            if token:
                self._set_token(token)
            if token and not await self._api_unlocked():
                token = None
        if token:
            self._set_token(token)
            self._solve_failed_at = 0.0
            logger.info("Turnstile challenge çözüldü.")
            return token
        self._solve_failed_at = time.time()
        logger.warning(
            f"Turnstile challenge çözülemedi; {int(_SOLVE_RETRY_AFTER)} sn yeniden denenmeyecek."
        )
        return None

    async def fetch_json(self, path_or_url: str) -> Optional[Any]:
        """
        Sofascore API isteğini tarayıcı oturumu içinden fetch() ile yapar.
        403 challenge durumunda Turnstile'ı otomatik çözüp isteği yineler.
        """
        await self.ensure_ready()

        url = path_or_url if path_or_url.startswith("http") else "https://www.sofascore.com/api/v1" + path_or_url

        res = await self._api_fetch(url, cache_mode_for(url))

        # 403 Challenge alındıysa otomatik çöz ve tekrar dene
        if res.get("status") == 403 and "challenge" in (res.get("text") or ""):
            logger.info("API 403 challenge döndürdü, Turnstile otomatik çözülüyor...")
            new_token = await self.solve_challenge()
            if new_token:
                res = await self._api_fetch(url, cache_mode_for(url))

        # Sağlık sinyali (src/bridge_health.py): isteğin SON hali sayılır — çözülüp yinelenen 403 başarıdır
        if res.get("ok"):
            bridge_health.record_success()
            return res.get("data")

        if res.get("status") == 404:
            bridge_health.record_success()  # API yanıt verdi; kaynak yok
            logger.debug(f"Kaynak bulunamadı (404): {url}")
            return {"__404__": True}

        if res.get("status") == 403:
            text = res.get("text") or ""
            kind = bridge_health.KIND_CHALLENGE if "challenge" in text else bridge_health.KIND_FORBIDDEN
            bridge_health.record_failure(kind, f"HTTP 403: {text[:100]}")

        logger.warning(f"Tarayıcı fetch başarısız (status {res.get('status')}): {(res.get('text') or '')[:100]}")
        return None

    async def close(self) -> None:
        """Tarayıcı oturumunu kapatır."""
        if self.page is not None:
            try:
                await self.page.close()
            except Exception:
                pass
            self.page = None
        if self.session is not None:
            try:
                await self.session.close()
            except Exception:
                pass
        self.session = None
        self.context = None


# Senkron / Genel Kullanım Fonksiyonları

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()


def _get_background_loop() -> asyncio.AbstractEventLoop:
    """
    BrowserBridge'in tek arka plan döngüsü. Döngü çalışmaya başlayana kadar kilit
    bırakılmaz: aksi halde eşzamanlı ilk çağrılar ikinci bir döngü açar ve Playwright
    nesneleri iki döngüden kullanılır.
    """
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None or _loop.is_closed() or _loop_thread is None or not _loop_thread.is_alive():
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run():
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            thread = threading.Thread(target=_run, daemon=True, name="BrowserBridgeLoop")
            thread.start()
            ready.wait()
            _loop, _loop_thread = loop, thread
        return _loop


def _proxy_settings() -> Optional[Dict[str, str]]:
    """curl_cffi ile aynı proxy: tarayıcı da gerçek IP'yi göstermesin."""
    if os.getenv("USE_PROXY", "false").lower() != "true":
        return None
    url = os.getenv("PROXY_URL", "").strip()
    if not url:
        return None
    from urllib.parse import urlparse

    u = urlparse(url)
    settings = {"server": f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"}
    if u.username:
        settings["username"] = u.username
    if u.password:
        settings["password"] = u.password
    return settings


def _run_sync(coro, timeout: float) -> Any:
    fut = asyncio.run_coroutine_threadsafe(coro, _get_background_loop())
    try:
        return fut.result(timeout=timeout)
    except BaseException:
        fut.cancel()
        raise


async def _run_on_background_loop(coro, timeout: float = REQUEST_TIMEOUT) -> Any:
    """
    Coroutine'i BrowserBridge'in arka plan döngüsünde çalıştırır ve sonucu bekler.
    Playwright nesneleri oluşturuldukları döngüye bağlıdır; çağıranın döngüsü
    (örn. asyncio.run ile açılıp kapanan geçici döngü) kullanılırsa sonraki çağrılar askıda kalır.
    """
    fut = asyncio.run_coroutine_threadsafe(coro, _get_background_loop())
    try:
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
    except BaseException:
        fut.cancel()
        raise


def fetch_api_via_browser_sync(path_or_url: str, timeout: float = REQUEST_TIMEOUT) -> Optional[Any]:
    """
    Senkron API istek köprüsü.
    Arka planda BrowserBridge üzerinden Sofascore API'sine istek yapar.
    """
    bridge = BrowserBridge.get_instance()
    try:
        _run_sync(bridge.ensure_ready(), STARTUP_TIMEOUT)
        return _run_sync(bridge.fetch_json(path_or_url), timeout)
    except Exception as e:
        logger.error(f"fetch_api_via_browser_sync hatası: {e!r}")
        return None


async def fetch_api_via_browser(path_or_url: str) -> Optional[Any]:
    """Asenkron API istek köprüsü."""
    bridge = BrowserBridge.get_instance()
    await _run_on_background_loop(bridge.ensure_ready(), STARTUP_TIMEOUT)
    return await _run_on_background_loop(bridge.fetch_json(path_or_url), REQUEST_TIMEOUT)


async def solve_turnstile_challenge(timeout_ms: int = 35000, headless: Optional[bool] = None) -> Optional[str]:
    """Turnstile challenge çözücü."""
    bridge = BrowserBridge.get_instance()
    await _run_on_background_loop(bridge.ensure_ready(), STARTUP_TIMEOUT)
    return await _run_on_background_loop(bridge.solve_challenge(), timeout_ms / 1000 + 10)


def solve_turnstile_challenge_sync(timeout_ms: int = 30000) -> Optional[str]:
    """Senkron Turnstile challenge çözücü."""
    cached = get_cached_token()
    if cached:
        return cached

    bridge = BrowserBridge.get_instance()
    try:
        _run_sync(bridge.ensure_ready(), STARTUP_TIMEOUT)
        return _run_sync(bridge.solve_challenge(), timeout_ms / 1000 + 10)
    except Exception as e:
        logger.error(f"solve_turnstile_challenge_sync hatası: {e}")
        return None


import atexit


def _cleanup():
    global _loop
    if _loop and _loop.is_running():
        try:
            bridge = BrowserBridge.get_instance()
            fut = asyncio.run_coroutine_threadsafe(bridge.close(), _loop)
            # Chrome'un kapanmasına zaman tanı; yoksa süreç öksüz kalır
            fut.result(timeout=10.0)
        except Exception:
            pass
        try:
            _loop.call_soon_threadsafe(_loop.stop)
        except Exception:
            pass


atexit.register(_cleanup)


def apply_token_to_headers(headers: dict, token: Optional[str] = None) -> dict:
    """Token'ı header'lara ekler."""
    token = token or get_cached_token()
    if not token:
        return headers

    existing_cookies = headers.get("Cookie", "")
    if "sofa_captcha=" not in existing_cookies:
        cookie_val = f"sofa_captcha={token}"
        headers["Cookie"] = f"{existing_cookies}; {cookie_val}" if existing_cookies else cookie_val

    if "X-Captcha" not in headers:
        headers["X-Captcha"] = token

    return headers
