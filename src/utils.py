"""
Sofascore Scraper için yardımcı fonksiyonlar ve araçlar.
"""

import os
import time
import random
import asyncio
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Union, TypeVar, cast, Tuple
from pathlib import Path
import dotenv

from src.logger import get_logger
from src.exceptions import (
    APIError, RateLimitError, NetworkError, 
    DataParsingError, SofaScoreScraperError, ResourceNotFoundError
)

# .env dosyasını yükle
dotenv.load_dotenv()

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


def make_api_request(
    url: str, 
    max_retries: Optional[int] = None,
    timeout: Optional[int] = None
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye API isteği yapar (curl_cffi kullanarak).
    """
    runtime_config = _get_runtime_request_config()
    max_retries = max_retries if max_retries is not None else int(runtime_config["max_retries"])
    timeout = timeout if timeout is not None else int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    
    headers = get_request_headers()
    
    # URL tamamlama
    if not url.startswith("http"):
        full_url = f"{API_BASE_URL}{url}"
    else:
        full_url = url

    for attempt in range(max_retries):
        try:
            logger.info(f"API İsteği ({attempt+1}/{max_retries}): {url}")
            
            kwargs: Dict[str, Any] = {
                "headers": headers,
                "timeout": timeout,
                "impersonate": random.choice(IMPERSONATE_PROFILES),
            }
            if use_proxy and proxy_url:
                kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

            # IMPERSONATE CHROME to bypass Cloudflare
            response = cffi_requests.get(
                full_url, 
                **kwargs
            )
            
            # Rate limiting kontrolü
            if response.status_code in (429, 503):
                default_wait = min(60, 5 * (2 ** attempt))
                retry_after = response.headers.get("Retry-After")
                wait_time = _parse_retry_after_seconds(retry_after, default_wait)
                logger.warning(f"Rate limit/Sunucu meşgul. {wait_time} saniye bekleniyor... (Retry-After: {retry_after})")
                time.sleep(wait_time)
                
                if attempt == max_retries - 1:
                    raise RateLimitError(wait_time)
                continue
            
            if response.status_code == 403:
                _adaptive_limiter.failure(403)
                wait_time = min(120, 10 * (2 ** attempt))
                cf_ray = response.headers.get("cf-ray", "yok")
                cf_status = response.headers.get("cf-mitigated", "yok")
                logger.warning(
                    f"403 Forbidden (deneme {attempt+1}/{max_retries}). "
                    f"Cloudflare/Turnstile koruması devrede. {wait_time}s bekleniyor..."
                )
                logger.debug(f"cf-ray: {cf_ray}, cf-mitigated: {cf_status}")

                # Turnstile challenge kontrolü ve otomatik çözme denemesi
                if "challenge" in response.text:
                    logger.info("Turnstile challenge tespit edildi. BrowserBridge üzerinden veri alınıyor...")
                    try:
                        from src.challenge_solver import fetch_api_via_browser_sync
                        browser_data = fetch_api_via_browser_sync(url)
                        if browser_data is not None:
                            logger.info("Veri BrowserBridge üzerinden başarıyla alındı!")
                            _adaptive_limiter.success()
                            return cast(JsonResponse, browser_data)
                    except Exception as te:
                        logger.debug(f"BrowserBridge hatası: {te}")

                time.sleep(wait_time)
                if attempt < max_retries - 1:
                    continue
            
            # 404 = missing resource (e.g. pregame-form). Never retry — burns cancel latency.
            if response.status_code == 404:
                logger.debug(f"Kaynak bulunamadı (404): {url}")
                return None

            # HTTP hatalarını kontrol et
            if response.status_code >= 400:
                raise APIError(f"HTTP hata: {response.status_code} {response.reason}", response.status_code)
            
            # Başarılı yanıt
            try:
                data = response.json()
            except ValueError as e:
                raise DataParsingError(f"JSON ayrıştırma hatası: {str(e)}") from e
            
            # İnsan davranışını simüle etmek için kısa bekleme
            _adaptive_limiter.success()
            wait_time = wait_time_min + random.uniform(0, wait_time_max)
            logger.info(f"Başarılı! Sonraki istek için {wait_time:.1f} saniye bekleniyor...")
            time.sleep(wait_time)
            
            return cast(JsonResponse, data)
            
        except Exception as e:
            if "curl: (7)" in str(e) or "Failed to connect" in str(e):
                logger.error(f"Proxy/Bağlantı hatası: {str(e)} - PROXY_URL: {proxy_url if use_proxy else 'Yok'}")
            else:
                logger.error(f"İstek hatası: {str(e)}")
            
            if attempt < max_retries - 1:
                wait_time = 3 * (2 ** attempt)
                logger.info(f"{wait_time} saniye içinde yeniden deneniyor... ({attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                logger.error(f"Tüm denemeler başarısız oldu: {url}")
                return None
    
    return None


async def make_api_request_async(
    session: AsyncSession,  # Changed type hint to curl_cffi AsyncSession
    url: str, 
    max_retries: Optional[int] = None
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye asenkron API isteği yapar (curl_cffi kullanarak).
    IMPORTANT: The session object MUST be a curl_cffi AsyncSession.
    """
    runtime_config = _get_runtime_request_config()
    max_retries = max_retries if max_retries is not None else int(runtime_config["max_retries"])
    request_timeout = int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    
    if not url.startswith("http"):
        full_url = f"{API_BASE_URL}{url}"
    else:
        full_url = url
    
    # Headers are usually set in the session, but we can merge extras if needed
    # headers = get_request_headers() 
    
    for attempt in range(max_retries):
        try:
            logger.debug(f"Asenkron API İsteği ({attempt+1}/{max_retries}): {url}")
            
            kwargs: Dict[str, Any] = {"timeout": request_timeout}
            if use_proxy and proxy_url:
                kwargs["proxy"] = proxy_url
                
            # The session is already configured with impersonate
            response = await session.get(full_url, **kwargs)
                
            if response.status_code in (429, 503):
                default_wait = min(60, 5 * (2 ** attempt))
                retry_after = response.headers.get("Retry-After")
                wait_time = _parse_retry_after_seconds(retry_after, default_wait)
                logger.warning(f"Rate limit/Sunucu meşgul. {wait_time} saniye bekleniyor... (Retry-After: {retry_after})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise RateLimitError(wait_time)
                continue

            if response.status_code == 403:
                _adaptive_limiter.failure(403)
                wait_time = min(120, 10 * (2 ** attempt))
                cf_ray = response.headers.get("cf-ray", "yok")
                cf_status = response.headers.get("cf-mitigated", "yok")
                logger.warning(
                    f"403 Forbidden (deneme {attempt+1}/{max_retries}). "
                    f"Cloudflare/Turnstile koruması devrede. {wait_time}s bekleniyor..."
                )
                logger.debug(f"cf-ray: {cf_ray}, cf-mitigated: {cf_status}")

                # Turnstile challenge kontrolü ve otomatik çözme denemesi
                if "challenge" in response.text:
                    logger.info("Turnstile challenge tespit edildi. BrowserBridge üzerinden veri alınıyor...")
                    try:
                        from src.challenge_solver import fetch_api_via_browser
                        browser_data = await fetch_api_via_browser(url)
                        if browser_data is not None:
                            logger.info("Veri BrowserBridge üzerinden başarıyla alındı!")
                            _adaptive_limiter.success()
                            return cast(JsonResponse, browser_data)
                    except Exception as te:
                        logger.debug(f"BrowserBridge hatası: {te}")

                await asyncio.sleep(wait_time)
                if attempt < max_retries - 1:
                    continue
            
            if response.status_code == 404:
                # 404 hatalarını normal akışta yönetebilmek için özel exception fırlat
                # Log seviyesini debug yapıyoruz ki konsolu kirletmesin
                logger.debug(f"Kaynak bulunamadı (404): {url}")
                raise ResourceNotFoundError(f"Kaynak bulunamadı: {url}")

            if response.status_code >= 400:
                error_msg = f"HTTP hata {response.status_code}: {response.reason}"
                logger.error(error_msg)
                raise APIError(error_msg, response.status_code)
            
            try:
                # curl_cffi responses have .json() method too
                data = response.json()
            except ValueError as e:
                # Hata durumunda içeriği logla
                try:
                    response_text = response.text
                except:
                    response_text = "<okunamadi>"
                
                error_msg = f"JSON ayrıştırma hatası: {str(e)} | Status: {response.status_code} | Content: {response_text[:500]}"
                logger.error(error_msg)
                raise DataParsingError(error_msg) from e
            
            _adaptive_limiter.success()
            wait_time = wait_time_min + random.uniform(0, wait_time_max)
            await asyncio.sleep(wait_time)
            
            return cast(JsonResponse, data)
                
        except ResourceNotFoundError:
            # 404 hatalarını retry etmeden doğrudan yukarı fırlat
            raise

        except Exception as e:
            if "curl: (7)" in str(e) or "Failed to connect" in str(e):
                logger.error(f"Proxy/Bağlantı hatası: {str(e)} - PROXY_URL: {proxy_url if use_proxy else 'Yok'}")
            else:
                logger.error(f"Asenkron hata: {str(e)}")
            
            if attempt < max_retries - 1:
                wait_time = 3 * (2 ** attempt)
                await asyncio.sleep(wait_time)
            else:
                return None
    
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



class AdaptiveRateLimiter:
    """Başarı/başarısızlık oranına göre dinamik bekleme süresi ayarlar."""

    def __init__(self, base_delay: float = 1.0, max_delay: float = 30.0):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.current_delay = base_delay
        self.consecutive_success = 0
        self.consecutive_fail = 0

    def success(self) -> None:
        self.consecutive_success += 1
        self.consecutive_fail = 0
        # 5 ardışık başarıdan sonra hafifçe hızlan
        if self.consecutive_success > 5:
            self.current_delay = max(self.base_delay, self.current_delay * 0.85)

    def failure(self, status_code: int) -> None:
        self.consecutive_fail += 1
        self.consecutive_success = 0
        if status_code == 429:
            self.current_delay = min(self.max_delay, self.current_delay * 3)
        elif status_code == 403:
            self.current_delay = min(self.max_delay, self.current_delay * 2)
        else:
            self.current_delay = min(self.max_delay, self.current_delay * 1.5)

    def get_delay(self) -> float:
        return self.current_delay + random.uniform(0, self.current_delay * 0.3)


# Modül düzeyinde paylaşılan adaptive rate limiter
_adaptive_limiter = AdaptiveRateLimiter()


def get_adaptive_limiter() -> AdaptiveRateLimiter:
    """Paylaşılan AdaptiveRateLimiter instance'ını döndürür."""
    return _adaptive_limiter


async def _warmup_session(session: AsyncSession) -> None:
    """
    Ana sayfaya GET yaparak Cloudflare cookie'lerini toplar.
    Session içindeki cookie jar'a otomatik eklenir.
    Başarısız olursa sessizce devam eder.
    """
    warmup_url = "https://www.sofascore.com/"
    try:
        logger.debug("Session warm-up başlatılıyor...")
        resp = await session.get(warmup_url, timeout=15)
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