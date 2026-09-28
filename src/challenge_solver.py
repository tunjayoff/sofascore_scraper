"""
Sofascore Turnstile Challenge Çözücü ve Tarayıcı Köprüsü (Browser Bridge).

Sofascore API korumasını (Cloudflare Turnstile + Varnish TLS/JA4 parmak izi)
aşmak için headless/headed gerçek Chrome oturumunu yönetir.

Proje mimarisi:
- Project 'c' (ps3838_site) kanıtlanmış Chrome başlatma bayrakları
  (--disable-blink-features=AutomationControlled, ignore_default_args, no_viewport=True)
- Sayfa içinde Turnstile challenge'ını otomatik çözme (turnstile.execute)
- /api/v1/token/captcha üzerinden taze JWT sofa_captcha token değişimi
- Chrome'un kendi HTTP/2 TLS oturumu üzerinden yüksek hızlı (2-10ms) JSON veri çekimi
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, Optional, Union

from src.logger import get_logger

logger = get_logger("ChallengeSolver")

# Token önbelleği
_cached_token: Optional[str] = None
_cached_at: float = 0
_TOKEN_TTL_SECONDS = 3500

DEFAULT_PROFILE_DIR = os.path.expanduser("~/.cache/sofascore_scraper/chrome_profile")


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


def _get_chrome_executable() -> Optional[str]:
    """Sistemdeki Google Chrome / Chromium çalıştırılabilir dosyasını bulur."""
    candidates = [
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for p in candidates:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None


class BrowserBridge:
    """
    Playwright ve gerçek Google Chrome üzerinden Sofascore API köprüsü.
    Turnstile challenge'larını arka planda otomatik çözer ve API isteklerini
    tarayıcının geçerli TLS oturumu üzerinden yürütür.
    """

    _instance: Optional["BrowserBridge"] = None
    _lock = asyncio.Lock()

    def __init__(self, profile_dir: str = DEFAULT_PROFILE_DIR):
        self.profile_dir = profile_dir
        self.pw = None
        self.context = None
        self.page = None
        self.token: Optional[str] = None
        self._init_lock = asyncio.Lock()
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

            from playwright.async_api import async_playwright

            self.pw = await async_playwright().start()

            args = [
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ]

            headless = not bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
            chrome_path = _get_chrome_executable()

            launch_kwargs: Dict[str, Any] = {
                "user_data_dir": self.profile_dir,
                "headless": headless,
                "args": args,
                "ignore_default_args": ["--enable-automation"],
                "no_viewport": True,
            }
            if chrome_path:
                launch_kwargs["executable_path"] = chrome_path
            else:
                launch_kwargs["channel"] = "chrome"

            logger.info(f"Chrome oturumu başlatılıyor (headless={headless})...")
            self.context = await self.pw.chromium.launch_persistent_context(**launch_kwargs)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

            try:
                await self.page.goto("https://www.sofascore.com/tr", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.warning(f"Sofascore anasayfa açılış uyarısı: {e}")

            # Önceki oturumdan kalan token varsa yükle
            try:
                t_raw = await self.page.evaluate("() => localStorage.getItem('sofa.captcha.token')")
                if t_raw:
                    try:
                        self.token = json.loads(t_raw)
                    except Exception:
                        self.token = t_raw
            except Exception:
                pass

    async def solve_challenge(self) -> Optional[str]:
        """
        Sayfa içinde Turnstile challenge'ını çözer ve geçerli JWT tokenı alır.
        """
        await self.ensure_ready()
        logger.info("Cloudflare Turnstile challenge çözülüyor...")

        try:
            # 1. Turnstile execute
            t_token = await self.page.evaluate("""() => {
                return new Promise((resolve, reject) => {
                    const timeout = setTimeout(() => reject("turnstile_timeout"), 15000);
                    if (typeof turnstile !== "undefined") {
                        turnstile.execute(undefined, {
                            callback: (token) => { clearTimeout(timeout); resolve(token); },
                            "error-callback": (err) => { clearTimeout(timeout); reject(err); }
                        });
                    } else {
                        reject("turnstile_undefined");
                    }
                });
            }""")

            # 2. Token captcha endpoint'inde takas et
            jwt = await self.page.evaluate("""async (turnstileToken) => {
                const resp = await fetch("https://www.sofascore.com/api/v1/token/captcha", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ response: turnstileToken, provider: "turnstile" })
                });
                const data = await resp.json();
                if (data && data.token) {
                    localStorage.setItem("sofa.captcha.token", JSON.stringify(data.token));
                    document.cookie = "sofa_captcha=" + data.token + "; Path=/; Secure; SameSite=Lax";
                    return data.token;
                }
                return null;
            }""", t_token)

            if jwt:
                self.token = jwt
                global _cached_token, _cached_at
                _cached_token = jwt
                _cached_at = time.time()
                logger.info("Turnstile challenge başarıyla çözüldü, yeni JWT token alındı.")
                return jwt

        except Exception as e:
            logger.warning(f"Turnstile execute başarısız, captcha.html fallback deneniyor: {e}")
            # Fallback: captcha.html sayfasına git
            try:
                challenge_url = "https://www.sofascore.com/captcha.html?redirectUrl=https%3A%2F%2Fwww.sofascore.com%2Ftr"
                await self.page.goto(challenge_url, wait_until="domcontentloaded", timeout=20000)
                for _ in range(10):
                    await asyncio.sleep(1)
                    if "/captcha.html" not in self.page.url:
                        t_raw = await self.page.evaluate("() => localStorage.getItem('sofa.captcha.token')")
                        if t_raw:
                            token = json.loads(t_raw) if t_raw.startswith('"') else t_raw
                            self.token = token
                            _cached_token = token
                            _cached_at = time.time()
                            logger.info("captcha.html üzerinden token başarıyla alındı.")
                            return token
            except Exception as fe:
                logger.error(f"captcha.html fallback hatası: {fe}")

        return None

    async def fetch_json(self, path_or_url: str) -> Optional[Any]:
        """
        Sofascore API isteğini tarayıcı oturumu içinden fetch() ile yapar.
        403 challenge durumunda Turnstile'ı otomatik çözüp isteği yineler.
        """
        await self.ensure_ready()

        url = path_or_url if path_or_url.startswith("http") else "https://www.sofascore.com/api/v1" + path_or_url
        x_req = hashlib.sha256(str(int(time.time()) // 1800).encode("utf-8")).hexdigest()[:6]

        res = await self.page.evaluate("""async ([targetUrl, xReq, xCap]) => {
            const headers = { "x-requested-with": xReq };
            if (xCap) headers["x-captcha"] = xCap;
            try {
                const resp = await fetch(targetUrl, { headers });
                return {
                    status: resp.status,
                    ok: resp.ok,
                    data: resp.ok ? await resp.json() : null,
                    text: !resp.ok ? await resp.text() : null
                };
            } catch (err) {
                return { status: 0, ok: false, data: null, text: String(err) };
            }
        }""", [url, x_req, self.token])

        # 403 Challenge alındıysa otomatik çöz ve tekrar dene
        if res.get("status") == 403 and "challenge" in (res.get("text") or ""):
            logger.info("API 403 challenge döndürdü, Turnstile otomatik çözülüyor...")
            new_token = await self.solve_challenge()
            if new_token:
                res = await self.page.evaluate("""async ([targetUrl, xReq, xCap]) => {
                    const headers = { "x-requested-with": xReq };
                    if (xCap) headers["x-captcha"] = xCap;
                    try {
                        const resp = await fetch(targetUrl, { headers });
                        return {
                            status: resp.status,
                            ok: resp.ok,
                            data: resp.ok ? await resp.json() : null,
                            text: !resp.ok ? await resp.text() : null
                        };
                    } catch (err) {
                        return { status: 0, ok: false, data: null, text: String(err) };
                    }
                }""", [url, x_req, self.token])

        if res.get("ok"):
            return res.get("data")

        logger.warning(f"Tarayıcı fetch başarısız (status {res.get('status')}): {res.get('text', '')[:100]}")
        return None

    async def close(self) -> None:
        """Tarayıcı oturumunu kapatır."""
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
            self.page = None
        if self.pw:
            try:
                await self.pw.stop()
            except Exception:
                pass
            self.pw = None


# Senkron / Genel Kullanım Fonksiyonları

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()


def _get_background_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None or not _loop.is_running():
            _loop = asyncio.new_event_loop()

            def _run():
                asyncio.set_event_loop(_loop)
                _loop.run_forever()

            _loop_thread = threading.Thread(target=_run, daemon=True, name="BrowserBridgeLoop")
            _loop_thread.start()
        return _loop


def fetch_api_via_browser_sync(path_or_url: str, timeout: float = 35.0) -> Optional[Any]:
    """
    Senkron API istek köprüsü.
    Arka planda BrowserBridge üzerinden Sofascore API'sine istek yapar.
    """
    loop = _get_background_loop()
    bridge = BrowserBridge.get_instance()
    fut = asyncio.run_coroutine_threadsafe(bridge.fetch_json(path_or_url), loop)
    try:
        return fut.result(timeout=timeout)
    except Exception as e:
        logger.error(f"fetch_api_via_browser_sync hatası: {e}")
        return None


async def fetch_api_via_browser(path_or_url: str) -> Optional[Any]:
    """Asenkron API istek köprüsü."""
    bridge = BrowserBridge.get_instance()
    return await bridge.fetch_json(path_or_url)


async def solve_turnstile_challenge(timeout_ms: int = 35000, headless: Optional[bool] = None) -> Optional[str]:
    """Turnstile challenge çözücü."""
    bridge = BrowserBridge.get_instance()
    return await bridge.solve_challenge()


def solve_turnstile_challenge_sync(timeout_ms: int = 30000) -> Optional[str]:
    """Senkron Turnstile challenge çözücü."""
    cached = get_cached_token()
    if cached:
        return cached

    loop = _get_background_loop()
    bridge = BrowserBridge.get_instance()
    fut = asyncio.run_coroutine_threadsafe(bridge.solve_challenge(), loop)
    try:
        return fut.result(timeout=timeout_ms / 1000 + 10)
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
            fut.result(timeout=3.0)
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
