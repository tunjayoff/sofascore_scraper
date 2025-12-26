"""
Sofascore Scraper için yardımcı fonksiyonlar ve araçlar.
"""

import os
import time
import random
import asyncio
import aiohttp
import requests
from typing import Dict, Any, Optional, Union, List, TypeVar, cast
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

# API isteği için kullanılan User-Agent'lar
USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
]

# API ayarları için çevre değişkenleri
API_BASE_URL: str = os.getenv("API_BASE_URL", "https://www.sofascore.com/api/v1")
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "10"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
MAX_CONCURRENT: int = int(os.getenv("MAX_CONCURRENT", "25"))  # Paralel istek sayısı - arttırıldı
WAIT_TIME_MIN: float = float(os.getenv("WAIT_TIME_MIN", "0.2"))  # Minimum bekleme süresi
WAIT_TIME_MAX: float = float(os.getenv("WAIT_TIME_MAX", "0.5"))  # Maksimum ek bekleme süresi

# Filtreleme ayarları
FETCH_ONLY_FINISHED: bool = os.getenv("FETCH_ONLY_FINISHED", "true").lower() == "true"
SAVE_EMPTY_ROUNDS: bool = os.getenv("SAVE_EMPTY_ROUNDS", "false").lower() == "true"

# Tip tanımı
JsonResponse = Dict[str, Any]
T = TypeVar('T')


# curl-cffi ile istekler (Cloudflare bypass için)
from curl_cffi import requests as cffi_requests
from curl_cffi.requests import AsyncSession

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


def make_api_request(
    url: str, 
    max_retries: Optional[int] = None,
    timeout: Optional[int] = None
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye API isteği yapar (curl_cffi kullanarak).
    """
    # Varsayılan değerleri çevre değişkenlerinden al
    max_retries = max_retries if max_retries is not None else MAX_RETRIES
    timeout = timeout if timeout is not None else REQUEST_TIMEOUT
    
    headers = get_request_headers()
    
    # URL tamamlama
    if not url.startswith("http"):
        full_url = f"{API_BASE_URL}{url}"
    else:
        full_url = url

    for attempt in range(max_retries):
        try:
            logger.info(f"API İsteği ({attempt+1}/{max_retries}): {url}")
            
            # IMPERSONATE CHROME to bypass Cloudflare
            response = cffi_requests.get(
                full_url, 
                headers=headers, 
                timeout=timeout,
                impersonate="chrome"
            )
            
            # Rate limiting kontrolü
            if response.status_code == 429:  # Too Many Requests
                wait_time = min(60, 5 * (2 ** attempt))
                logger.warning(f"Rate limit aşıldı. {wait_time} saniye bekleniyor...")
                time.sleep(wait_time)
                
                if attempt == max_retries - 1:
                    raise RateLimitError(wait_time)
                continue
            
            if response.status_code == 403:
                logger.warning(f"403 Forbidden - Cloudflare bloklaması olabilir. (Deneme {attempt+1})")
            
            # HTTP hatalarını kontrol et
            if response.status_code >= 400:
                raise APIError(f"HTTP hata: {response.status_code} {response.reason}", response.status_code)
            
            # Başarılı yanıt
            try:
                data = response.json()
            except ValueError as e:
                raise DataParsingError(f"JSON ayrıştırma hatası: {str(e)}") from e
            
            # İnsan davranışını simüle etmek için kısa bekleme
            wait_time = WAIT_TIME_MIN + random.uniform(0, WAIT_TIME_MAX)
            logger.info(f"Başarılı! Sonraki istek için {wait_time:.1f} saniye bekleniyor...")
            time.sleep(wait_time)
            
            return cast(JsonResponse, data)
            
        except Exception as e:
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
    max_retries = max_retries if max_retries is not None else MAX_RETRIES
    
    if not url.startswith("http"):
        full_url = f"{API_BASE_URL}{url}"
    else:
        full_url = url
    
    # Headers are usually set in the session, but we can merge extras if needed
    # headers = get_request_headers() 
    
    for attempt in range(max_retries):
        try:
            logger.debug(f"Asenkron API İsteği ({attempt+1}/{max_retries}): {url}")
            
            # The session is already configured with impersonate
            response = await session.get(full_url, timeout=30)
                
            if response.status_code == 429:
                wait_time = min(60, 5 * (2 ** attempt))
                logger.warning(f"Rate limit aşıldı. {wait_time} saniye bekleniyor...")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise RateLimitError(wait_time)
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
            
            wait_time = WAIT_TIME_MIN * (random.random() * 0.5)
            await asyncio.sleep(wait_time)
            
            return cast(JsonResponse, data)
                
        except ResourceNotFoundError:
            # 404 hatalarını retry etmeden doğrudan yukarı fırlat
            raise

        except Exception as e:
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
    return AsyncSession(
        headers=get_request_headers(),
        timeout=REQUEST_TIMEOUT,
        impersonate="chrome"  # Magic is here
    )