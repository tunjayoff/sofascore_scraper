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
    "chrome",
    "chrome110",
    "chrome120",
    "chrome124",
    "safari17_0",
    "edge101",
]

# ... imports ...


def get_request_headers() -> Dict[str, str]:
    """
    API istekleri için kullanılacak HTTP başlıklarını döndürür.
    curl_cffi already handles user-agent impersonation, so we just add extra headers if needed.
    """
    return {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sofascore.com/",
        # User-Agent is handled by impersonate="chrome"
    }


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
                wait_time = min(120, 10 * (2 ** attempt))
                cf_ray = response.headers.get("cf-ray", "yok")
                cf_status = response.headers.get("cf-mitigated", "yok")
                logger.warning(
                    f"403 Forbidden (deneme {attempt+1}/{max_retries}). "
                    f"Cloudflare koruması tetiklenmiş olabilir. {wait_time}s bekleniyor..."
                )
                logger.debug(f"cf-ray: {cf_ray}, cf-mitigated: {cf_status}")
                time.sleep(wait_time)
                if attempt < max_retries - 1:
                    continue
            
            # HTTP hatalarını kontrol et
            if response.status_code >= 400:
                raise APIError(f"HTTP hata: {response.status_code} {response.reason}", response.status_code)
            
            # Başarılı yanıt
            try:
                data = response.json()
            except ValueError as e:
                raise DataParsingError(f"JSON ayrıştırma hatası: {str(e)}") from e
            
            # İnsan davranışını simüle etmek için kısa bekleme
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
                wait_time = min(120, 10 * (2 ** attempt))
                cf_ray = response.headers.get("cf-ray", "yok")
                cf_status = response.headers.get("cf-mitigated", "yok")
                logger.warning(
                    f"403 Forbidden (deneme {attempt+1}/{max_retries}). "
                    f"Cloudflare koruması tetiklenmiş olabilir. {wait_time}s bekleniyor..."
                )
                logger.debug(f"cf-ray: {cf_ray}, cf-mitigated: {cf_status}")
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


def create_session_async() -> AsyncSession:
    """
    Asenkron API istekleri için curl_cffi.requests.AsyncSession oluşturur.
    """
    runtime_config = _get_runtime_request_config()
    profile = random.choice(IMPERSONATE_PROFILES)
    logger.debug(f"Async session oluşturuldu, impersonate profili: {profile}")
    return AsyncSession(
        headers=get_request_headers(),
        timeout=int(runtime_config["request_timeout"]),
        impersonate=profile,
    )