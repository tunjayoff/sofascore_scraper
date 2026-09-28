"""
Sofascore Turnstile challenge çözücü.

Sofascore, API isteklerini korumak için Cloudflare Turnstile CAPTCHA kullanır.
Bu modül, headless tarayıcı ile Turnstile challenge'ını çözerek geçerli bir
JWT token alır. Bu token `sofa_captcha` cookie'si olarak API isteklerinde kullanılır.

Akış:
  1. Playwright ile Sofascore challenge sayfasını aç
  2. Turnstile widget'ı otomatik çözülsün
  3. Sayfa JS'i çözümü POST /api/v1/token/captcha'ya gönderir → JWT döner
  4. JWT, sofa_captcha cookie'sine yazılır
  5. Cookie'yi curl_cffi session'larına aktarırız

Kullanım:
  token = await solve_turnstile_challenge()
  # veya
  token = solve_turnstile_challenge_sync()
"""

import asyncio
import json
import os
import time
from typing import Optional

from src.logger import get_logger

logger = get_logger("ChallengeSolver")

# Token önbelleği — çözülen token JWT olup exp süresi var
_cached_token: Optional[str] = None
_cached_at: float = 0
_TOKEN_TTL_SECONDS = 3500  # JWT genellikle 1 saat geçerli, 58 dk'da yenile


def _is_token_valid() -> bool:
    """Önbellekteki token hâlâ geçerli mi?"""
    if not _cached_token:
        return False
    if time.time() - _cached_at > _TOKEN_TTL_SECONDS:
        return False
    # JWT exp kontrolü
    try:
        import base64
        payload = _cached_token.split(".")[1]
        # Base64 padding düzelt
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("exp", 0) > time.time() + 60  # 1 dk margin
    except Exception:
        return False


def get_cached_token() -> Optional[str]:
    """Geçerli bir önbellek tokeni varsa döndürür."""
    if _is_token_valid():
        return _cached_token
    return None


def _get_chrome_executable() -> Optional[str]:
    """Sistemdeki Google Chrome çalıştırılabilir dosyasını bulur."""
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


async def solve_turnstile_challenge(timeout_ms: int = 35000, headless: Optional[bool] = None) -> Optional[str]:
    """
    Playwright ile Sofascore Turnstile challenge'ını çözer.
    Eğer ekranda masaüstü oturumu varsa (DISPLAY mevcutsa) görünür modda açılabilir.

    Returns:
        str: sofa_captcha JWT tokeni, veya çözülemezse None
    """
    global _cached_token, _cached_at

    # Önbellekten dön
    cached = get_cached_token()
    if cached:
        logger.debug("Turnstile token önbellekten kullanılıyor")
        return cached

    # .env içinde kayıtlı token var mı?
    env_token = os.getenv("SOFA_CAPTCHA_TOKEN", "").strip()
    if env_token:
        _cached_token = env_token
        _cached_at = time.time()
        return env_token

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright yüklü değil. Yüklemek için: pip install playwright")
        return None

    if headless is None:
        # DISPLAY varsa kullanıcı onay verebilsin diye headful çalıştır
        headless = not bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))

    logger.info(f"Turnstile challenge çözülüyor (headless={headless})...")

    chrome_path = _get_chrome_executable()

    try:
        async with async_playwright() as p:
            launch_kwargs = {
                "headless": headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            }
            if chrome_path:
                launch_kwargs["executable_path"] = chrome_path

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
                locale="tr-TR",
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            # Doğrudan Sofascore'un bağımsız doğrulama sayfası
            challenge_url = (
                "https://www.sofascore.com/captcha.html"
                "?redirectUrl=https%3A%2F%2Fwww.sofascore.com%2Ftr"
            )
            await page.goto(challenge_url, wait_until="domcontentloaded")
            logger.info("Doğrulama sayfası açıldı, Turnstile widget bekleniyor...")

            try:
                # 35 saniye boyunca doğrulamayı veya yönlendirmeyi bekle
                for _ in range(timeout_ms // 1000):
                    await asyncio.sleep(1)

                    # 1. Cookie kontrolü
                    cookies = await context.cookies("https://www.sofascore.com")
                    captcha_cookie = next(
                        (c for c in cookies if c["name"] == "sofa_captcha"),
                        None,
                    )
                    if captcha_cookie and captcha_cookie["value"]:
                        token = captcha_cookie["value"]
                        _cached_token = token
                        _cached_at = time.time()
                        logger.info("Turnstile challenge çözüldü! sofa_captcha çerezi alındı.")
                        await browser.close()
                        return token

                    # 2. localStorage kontrolü
                    token_from_storage = await page.evaluate(
                        "window.localStorage.getItem('sofa.captcha.token')"
                    )
                    if token_from_storage:
                        try:
                            token = json.loads(token_from_storage)
                        except (json.JSONDecodeError, TypeError):
                            token = token_from_storage

                        if token:
                            _cached_token = token
                            _cached_at = time.time()
                            logger.info("Turnstile token localStorage üzerinden alındı.")
                            await browser.close()
                            return token

                    # 3. Yönlendirme tamamlandıysa çerezlere tekrar bak
                    if "captcha.html" not in page.url:
                        logger.info("Doğrulama tamamlandı, sayfaya yönlendirildi.")
                        cookies = await context.cookies("https://www.sofascore.com")
                        for c in cookies:
                            if c["name"] == "sofa_captcha":
                                _cached_token = c["value"]
                                _cached_at = time.time()
                                await browser.close()
                                return _cached_token

            except Exception as e:
                logger.warning(f"Doğrulama bekleme hatası: {e}")

            await browser.close()

    except Exception as e:
        logger.error(f"Turnstile challenge çözme hatası: {e}")

    logger.warning("Turnstile challenge otomatik çözülemedi.")
    return None


def solve_turnstile_challenge_sync(timeout_ms: int = 30000) -> Optional[str]:
    """Senkron wrapper — solve_turnstile_challenge'ın senkron versiyonu."""
    cached = get_cached_token()
    if cached:
        return cached

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Zaten bir event loop çalışıyorsa yeni bir thread'de çalıştır
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(
                asyncio.run, solve_turnstile_challenge(timeout_ms)
            ).result(timeout=timeout_ms / 1000 + 10)
    else:
        return asyncio.run(solve_turnstile_challenge(timeout_ms))


def apply_token_to_headers(headers: dict, token: Optional[str] = None) -> dict:
    """Token'ı header'lara ekler (cookie olarak)."""
    token = token or get_cached_token()
    if not token:
        return headers

    existing_cookies = headers.get("Cookie", "")
    if "sofa_captcha=" in existing_cookies:
        return headers

    cookie_val = f"sofa_captcha={token}"
    if existing_cookies:
        headers["Cookie"] = f"{existing_cookies}; {cookie_val}"
    else:
        headers["Cookie"] = cookie_val

    return headers
