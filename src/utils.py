"""
Sofascore Scraper için yardımcı fonksiyonlar ve araçlar.
"""

import os
import time
import random
import asyncio
import contextvars
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Callable, Dict, Any, Optional, Union, TypeVar, cast, Tuple
from pathlib import Path
import dotenv

from src.logger import get_logger
from src.paths import env_file_path
from src.exceptions import (
    APIError, RateLimitError, NetworkError,
    DataParsingError, ResourceNotFoundError
)

# .env dosyasını yükle
dotenv.load_dotenv(env_file_path())

# Logger'ı alın
logger = get_logger("Utils")

# API ayarları için çevre değişkenleri
API_BASE_URL: str = os.getenv("API_BASE_URL", "https://www.sofascore.com/api/v1")

from src.config_manager import ConfigManager

# Filtreleme ayarları
FETCH_ONLY_FINISHED: bool = os.getenv("FETCH_ONLY_FINISHED", "true").lower() == "true"
SAVE_EMPTY_ROUNDS: bool = os.getenv("SAVE_EMPTY_ROUNDS", "false").lower() == "true"

# Proxy ayarları
_cm = ConfigManager()

# Tip tanımı
JsonResponse = Dict[str, Any]
T = TypeVar('T')


# ---- İptal (web arka plan işi) ----

class FetchCancelled(BaseException):
    """
    Çalışan iş iptal edildiğinde istek katmanında fırlatılır.
    asyncio.CancelledError gibi bilerek BaseException: fetcher'lardaki geniş
    `except Exception` blokları bunu yutup bir sonraki istekle devam etmesin.
    """


# İş başına ayarlanır (web/routes/api.py). ContextVar olduğu için yalnızca o işin
# thread'ini ve onun asyncio.run / asyncio.to_thread çağrılarını etkiler; aynı anda
# gelen diğer web istekleri (lig arama, tek maç çekme) etkilenmez.
_cancel_check: "contextvars.ContextVar[Optional[Callable[[], bool]]]" = contextvars.ContextVar(
    "fetch_cancel_check", default=None
)


def set_cancel_check(fn: Optional[Callable[[], bool]]) -> "contextvars.Token":
    """Bu bağlamdaki istekler için iptal kontrolünü ayarlar."""
    return _cancel_check.set(fn)


def raise_if_cancelled() -> None:
    """İş iptal edildiyse FetchCancelled fırlatır."""
    fn = _cancel_check.get()
    if fn is not None and fn():
        raise FetchCancelled()


def _sleep(seconds: float) -> None:
    """time.sleep gibi, ama iptal istenirse beklemeyi hemen keser."""
    end = time.monotonic() + max(0.0, float(seconds))
    while True:
        raise_if_cancelled()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.25, left))


async def _asleep(seconds: float) -> None:
    """asyncio.sleep gibi, ama iptal istenirse beklemeyi hemen keser."""
    end = time.monotonic() + max(0.0, float(seconds))
    while True:
        raise_if_cancelled()
        left = end - time.monotonic()
        if left <= 0:
            return
        await asyncio.sleep(min(0.25, left))


# curl-cffi ile istekler (Cloudflare bypass için)
from curl_cffi import requests as cffi_requests
from curl_cffi.requests import AsyncSession

IMPERSONATE_PROFILES = [
    "chrome124",
    "chrome131",
    "chrome133a",
    "chrome136",
    "chrome142",
    "chrome145",
    # Safari profilleri — TLS parmak izi çeşitliliği sağlar
    "safari17_0",
    "safari18_0",
    # Firefox — ek çeşitlilik
    "firefox133",
]

import hashlib
import weakref

# Accept-Language header havuzu — her istekte rastgele seçilir
_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,tr;q=0.8",
    "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "tr-TR,tr;q=0.9,en;q=0.8",
]


def get_sofascore_hash() -> str:
    """
    Sofascore'un dinamik X-Requested-With başlığını hesaplar.
    Her 30 dakikalık UNIX zaman aralığının SHA-256 hash'inin ilk 6 karakteri.
    """
    bucket = str(int(time.time()) // 1800)
    return hashlib.sha256(bucket.encode("utf-8")).hexdigest()[:6]


def get_sofa_captcha_token() -> Optional[str]:
    """
    Geçerli sofa_captcha JWT tokenini döndürür.
    Önce .env (SOFA_CAPTCHA_TOKEN), sonra önbellekten bakar.
    """
    env_token = os.getenv("SOFA_CAPTCHA_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        from src.challenge_solver import get_cached_token
        return get_cached_token()
    except Exception:
        return None


def get_request_headers() -> Dict[str, str]:
    """
    API istekleri için kullanılacak HTTP başlıklarını döndürür.
    Dinamik 30 dakikalık X-Requested-With hash'i ve sofa_captcha tokeni otomatik eklenir.
    """
    headers = {
        "Accept": "*/*",
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
        # Sofascore dinamik 30-dakikalık hash
        "X-Requested-With": get_sofascore_hash(),
        # Sec-Fetch-* — modern tarayıcılar bu header'ları gönderir
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        # User-Agent is handled by impersonate="chrome"
    }

    # sofa_captcha / X-Captcha token mevcutsa ekle
    token = get_sofa_captcha_token()
    if token:
        headers["X-Captcha"] = token
        headers["Cookie"] = f"sofa_captcha={token}"

    # Opsiyonel header'lar — gerçek tarayıcılarda bazen var bazen yok
    if random.random() > 0.5:
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"

    return headers


def _get_runtime_request_config() -> Dict[str, Union[int, float]]:
    """Runtime'da güncel request ayarlarını döndürür."""
    return {
        "request_timeout": _cm.get_request_timeout(),
        "max_retries": _cm.get_max_retries(),
        "wait_time_min": _cm.get_wait_time_min(),
        "wait_time_max": _cm.get_wait_time_max(),
    }


def _get_proxy_config() -> Tuple[bool, str]:
    """Runtime'da güncel proxy ayarlarını döndürür."""
    use_proxy = _cm.get_use_proxy()
    proxy_url = _cm.get_proxy_url().strip()
    return use_proxy, proxy_url


def _parse_retry_after_seconds(retry_after: Optional[str], default_wait: int) -> int:
    """Retry-After header değerini saniye cinsinden parse eder."""
    if not retry_after:
        return default_wait
    try:
        return max(1, int(retry_after))
    except ValueError:
        try:
            dt = parsedate_to_datetime(retry_after)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = int((dt - datetime.now(timezone.utc)).total_seconds())
            return max(1, min(120, delta))
        except (TypeError, ValueError):
            return default_wait


# ---- Önce tarayıcı modu ----
# curl isteği challenge'a takılıp tarayıcı köprüsü aynı isteği alabildiyse, bir süre boyunca
# istekler doğrudan tarayıcıdan yapılır: her istekte önce reddedilecek bir curl isteği ve
# ardından gelen bekleme olmasın. Süre dolunca curl yeniden denenir (engel kalkmış olabilir).
BROWSER_FIRST_SECONDS = 600.0
_browser_first_until = 0.0


def _browser_first() -> bool:
    return time.monotonic() < _browser_first_until


def _mark_browser_first() -> None:
    global _browser_first_until
    if not _browser_first():
        logger.info(f"curl engelleniyor; istekler {int(BROWSER_FIRST_SECONDS)} sn tarayıcıdan yapılacak")
    _browser_first_until = time.monotonic() + BROWSER_FIRST_SECONDS


def _browser_result(data: Any, url: str) -> Optional[JsonResponse]:
    """Köprü sonucunu yorumla: veri, 404 (None) ya da başarısız (hata)."""
    if isinstance(data, dict) and data.get("__404__"):
        return None
    return cast(JsonResponse, data)


# 403/404/429 dışındaki 4xx yanıtlar kalıcıdır: yeniden denemek yalnızca zaman kaybettirir
def _is_transient_status(status_code: int) -> bool:
    return status_code >= 500 or status_code in (403, 408, 429)


def _full_url(url: str) -> str:
    return url if url.startswith("http") else f"{API_BASE_URL}{url}"


def _retry_wait(attempt: int) -> float:
    return 3 * (2 ** attempt)


def make_api_request(
    url: str,
    max_retries: Optional[int] = None,
    timeout: Optional[int] = None
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye API isteği yapar (curl_cffi kullanarak).

    404 ve tüm denemeler tükendiğinde None döner (senkron çağıranlar None bekler).
    Son denemeden sonra beklenmez; kalıcı 4xx hataları yeniden denenmez.
    """
    runtime_config = _get_runtime_request_config()
    max_retries = max(1, max_retries if max_retries is not None else int(runtime_config["max_retries"]))
    timeout = timeout if timeout is not None else int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    full_url = _full_url(url)

    if _browser_first():
        raise_if_cancelled()
        from src.challenge_solver import fetch_api_via_browser_sync
        data = fetch_api_via_browser_sync(url)
        if data is not None:
            _sleep(wait_time_min + random.uniform(0, wait_time_max))
            return _browser_result(data, url)
        # Köprü başarısız: aşağıda curl ile normal yoldan dene

    for attempt in range(max_retries):
        raise_if_cancelled()
        last_attempt = attempt == max_retries - 1
        try:
            logger.debug(f"API İsteği ({attempt+1}/{max_retries}): {url}")

            kwargs: Dict[str, Any] = {
                # Her istekte yeniden: 30 dk'lık hash ve yeni çözülen captcha token güncel kalsın
                "headers": get_request_headers(),
                "timeout": timeout,
                "impersonate": random.choice(IMPERSONATE_PROFILES),
            }
            if use_proxy and proxy_url:
                kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

            response = cffi_requests.get(full_url, **kwargs)

            if response.status_code in (429, 503):
                if last_attempt:
                    logger.error(f"Rate limit/Sunucu meşgul, denemeler tükendi: {url}")
                    return None
                default_wait = min(60, 5 * (2 ** attempt))
                wait_time = _parse_retry_after_seconds(response.headers.get("Retry-After"), default_wait)
                logger.warning(f"Rate limit/Sunucu meşgul ({response.status_code}). {wait_time} saniye bekleniyor...")
                _sleep(wait_time)
                continue

            if response.status_code == 403:
                logger.warning(f"403 Forbidden (deneme {attempt+1}/{max_retries}): {url}")
                logger.debug(f"cf-ray: {response.headers.get('cf-ray', 'yok')}, cf-mitigated: {response.headers.get('cf-mitigated', 'yok')}")
                raise_if_cancelled()
                if "challenge" in response.text:
                    logger.info("Turnstile challenge tespit edildi. BrowserBridge üzerinden veri alınıyor...")
                    try:
                        from src.challenge_solver import fetch_api_via_browser_sync
                        browser_data = fetch_api_via_browser_sync(url)
                        if browser_data is not None:
                            _mark_browser_first()
                            logger.debug("Veri BrowserBridge üzerinden alındı")
                            return _browser_result(browser_data, url)
                    except Exception as te:
                        logger.debug(f"BrowserBridge hatası: {te}")
                if last_attempt:
                    logger.error(f"403 Forbidden, denemeler tükendi: {url}")
                    return None
                _sleep(min(120, 10 * (2 ** attempt)))
                continue

            # 404 = missing resource (e.g. pregame-form). Never retry — burns cancel latency.
            if response.status_code == 404:
                logger.debug(f"Kaynak bulunamadı (404): {url}")
                return None

            if response.status_code >= 400:
                if last_attempt or not _is_transient_status(response.status_code):
                    logger.error(f"HTTP hata: {response.status_code} {response.reason} — {url}")
                    return None
                logger.warning(f"HTTP {response.status_code}, {_retry_wait(attempt)} sn sonra yeniden denenecek: {url}")
                _sleep(_retry_wait(attempt))
                continue

            try:
                data = response.json()
            except ValueError as e:
                raise DataParsingError(f"JSON ayrıştırma hatası: {str(e)}") from e

            # İnsan davranışını simüle etmek için kısa bekleme
            wait_time = wait_time_min + random.uniform(0, wait_time_max)
            try:
                _sleep(wait_time)
            except FetchCancelled:
                # Veri zaten elimizde: bu yanıtı döndür, iptal bir sonraki istekte işlensin.
                pass

            return cast(JsonResponse, data)

        except Exception as e:
            if "curl: (7)" in str(e) or "Failed to connect" in str(e):
                logger.error(f"Proxy/Bağlantı hatası: {str(e)} - proxy: {'açık' if use_proxy else 'yok'}")
            else:
                logger.error(f"İstek hatası: {str(e)}")
            if last_attempt:
                logger.error(f"Tüm denemeler başarısız oldu: {url}")
                return None
            logger.info(f"{_retry_wait(attempt)} saniye içinde yeniden deneniyor... ({attempt+1}/{max_retries})")
            _sleep(_retry_wait(attempt))

    return None


# Döngü başına istek sınırı: maç/tur sayısı değil, aynı anda uçuşan HTTP isteği sayısı.
# asyncio.Semaphore bir döngüye bağlanır; her asyncio.run yeni bir döngü açtığı için döngüye göre tutulur.
_request_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _request_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _request_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, _cm.get_max_concurrent()))
        _request_semaphores[loop] = sem
    return sem


async def make_api_request_async(
    session: AsyncSession,
    url: str,
    max_retries: Optional[int] = None
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye asenkron API isteği yapar (curl_cffi AsyncSession ile).

    Denemeler tükendiğinde tipli hata fırlatır — çağıranın devre kesicisi 403/429/5xx
    serisini görebilsin: 404 → ResourceNotFoundError, 429/503 → RateLimitError,
    diğer HTTP hataları → APIError(status_code), bağlantı hataları → NetworkError.
    Son denemeden sonra beklenmez; kalıcı 4xx hataları yeniden denenmez.
    """
    runtime_config = _get_runtime_request_config()
    max_retries = max(1, max_retries if max_retries is not None else int(runtime_config["max_retries"]))
    request_timeout = int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    full_url = _full_url(url)
    semaphore = _request_semaphore()

    if _browser_first():
        raise_if_cancelled()
        browser_data = None
        try:
            from src.challenge_solver import fetch_api_via_browser
            async with semaphore:
                browser_data = await fetch_api_via_browser(url)
        except Exception as e:
            logger.debug(f"BrowserBridge hatası: {e!r}")
        if isinstance(browser_data, dict) and browser_data.get("__404__"):
            raise ResourceNotFoundError(f"Kaynak bulunamadı: {url}")
        if browser_data is not None:
            await _asleep(wait_time_min + random.uniform(0, wait_time_max))
            return cast(JsonResponse, browser_data)
        # Köprü başarısız: aşağıda curl ile normal yoldan dene

    for attempt in range(max_retries):
        raise_if_cancelled()
        last_attempt = attempt == max_retries - 1
        try:
            logger.debug(f"Asenkron API İsteği ({attempt+1}/{max_retries}): {url}")

            # Her istekte yeniden: 30 dk'lık hash ve yeni çözülen captcha token güncel kalsın
            kwargs: Dict[str, Any] = {"timeout": request_timeout, "headers": get_request_headers()}
            if use_proxy and proxy_url:
                kwargs["proxy"] = proxy_url

            async with semaphore:
                response = await session.get(full_url, **kwargs)
        except Exception as e:
            if "curl: (7)" in str(e) or "Failed to connect" in str(e):
                logger.error(f"Proxy/Bağlantı hatası: {str(e)} - proxy: {'açık' if use_proxy else 'yok'}")
            else:
                logger.error(f"Asenkron istek hatası: {str(e)}")
            if last_attempt:
                raise NetworkError(f"İstek başarısız: {url}: {e}") from e
            await _asleep(_retry_wait(attempt))
            continue

        status = response.status_code

        if status in (429, 503):
            if last_attempt:
                raise RateLimitError(status_code=status, url=url)
            default_wait = min(60, 5 * (2 ** attempt))
            wait_time = _parse_retry_after_seconds(response.headers.get("Retry-After"), default_wait)
            logger.warning(f"Rate limit/Sunucu meşgul ({status}). {wait_time} saniye bekleniyor...")
            await _asleep(wait_time)
            continue

        if status == 403:
            logger.warning(f"403 Forbidden (deneme {attempt+1}/{max_retries}): {url}")
            logger.debug(f"cf-ray: {response.headers.get('cf-ray', 'yok')}, cf-mitigated: {response.headers.get('cf-mitigated', 'yok')}")
            raise_if_cancelled()
            if "challenge" in response.text:
                logger.info("Turnstile challenge tespit edildi. BrowserBridge üzerinden veri alınıyor...")
                browser_data = None
                try:
                    from src.challenge_solver import fetch_api_via_browser
                    browser_data = await fetch_api_via_browser(url)
                except Exception as te:
                    logger.debug(f"BrowserBridge hatası: {te!r}")
                if isinstance(browser_data, dict) and browser_data.get("__404__"):
                    raise ResourceNotFoundError(f"Kaynak bulunamadı: {url}")
                if browser_data is not None:
                    _mark_browser_first()
                    logger.debug("Veri BrowserBridge üzerinden alındı")
                    return cast(JsonResponse, browser_data)
            if last_attempt:
                raise APIError(f"HTTP 403 Forbidden: {url}", status_code=403)
            await _asleep(min(120, 10 * (2 ** attempt)))
            continue

        if status == 404:
            logger.debug(f"Kaynak bulunamadı (404): {url}")
            raise ResourceNotFoundError(f"Kaynak bulunamadı: {url}")

        if status >= 400:
            if last_attempt or not _is_transient_status(status):
                raise APIError(f"HTTP {status} {response.reason}: {url}", status_code=status)
            logger.warning(f"HTTP {status}, {_retry_wait(attempt)} sn sonra yeniden denenecek: {url}")
            await _asleep(_retry_wait(attempt))
            continue

        try:
            data = response.json()
        except ValueError as e:
            try:
                response_text = response.text
            except Exception:
                response_text = "<okunamadi>"
            raise DataParsingError(
                f"JSON ayrıştırma hatası: {str(e)} | Status: {status} | Content: {response_text[:500]}"
            ) from e

        wait_time = wait_time_min + random.uniform(0, wait_time_max)
        try:
            await _asleep(wait_time)
        except FetchCancelled:
            # Veri zaten elimizde: bu yanıtı döndür, iptal bir sonraki istekte işlensin.
            pass
        return cast(JsonResponse, data)

    return None


def ensure_directory(directory_path: Union[str, Path]) -> bool:
    """
    Belirtilen dizinin var olduğundan emin olur.
    """
    try:
        path = Path(directory_path)
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Dizin oluşturma hatası ({directory_path}): {str(e)}")
        return False



async def _warmup_session(session: AsyncSession) -> None:
    """
    Ana sayfaya GET yaparak Cloudflare cookie'lerini toplar.
    Session içindeki cookie jar'a otomatik eklenir.
    Başarısız olursa sessizce devam eder.
    """
    warmup_url = "https://www.sofascore.com/"
    try:
        logger.debug("Session warm-up başlatılıyor...")
        kwargs: Dict[str, Any] = {"timeout": 15}
        use_proxy, proxy_url = _get_proxy_config()
        if use_proxy and proxy_url:
            kwargs["proxy"] = proxy_url  # warm-up da gerçek IP'yi göstermesin
        resp = await session.get(warmup_url, **kwargs)
        logger.debug(
            f"Warm-up tamamlandı: status={resp.status_code}, "
            f"cookies={len(session.cookies) if hasattr(session, 'cookies') else '?'}"
        )
        # Cloudflare challenge geçişi için kısa bekleme
        await asyncio.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        logger.debug(f"Warm-up başarısız (devam ediliyor): {e}")


class WarmableAsyncSession:
    """
    Warm-up destekli AsyncSession context manager.
    İlk kullanımda ana sayfaya gidip Cloudflare cookie'lerini toplar.
    """

    def __init__(self) -> None:
        runtime_config = _get_runtime_request_config()
        profile = random.choice(IMPERSONATE_PROFILES)
        logger.debug(f"Async session oluşturuldu, impersonate profili: {profile}")
        self._session = AsyncSession(
            headers=get_request_headers(),
            timeout=int(runtime_config["request_timeout"]),
            impersonate=profile,
        )

    async def __aenter__(self) -> AsyncSession:
        await self._session.__aenter__()
        await _warmup_session(self._session)
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[override]
        await self._session.__aexit__(exc_type, exc_val, exc_tb)


def create_session_async() -> WarmableAsyncSession:
    """
    Asenkron API istekleri için warm-up destekli session oluşturur.
    Kullanım: async with create_session_async() as session: ...
    """
    return WarmableAsyncSession()
