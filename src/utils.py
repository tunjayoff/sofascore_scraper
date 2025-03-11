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
    DataParsingError, SofaScoreScraperError
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


def get_request_headers() -> Dict[str, str]:
    """
    API istekleri için kullanılacak HTTP başlıklarını döndürür.
    
    Returns:
        Dict[str, str]: HTTP başlıkları
    """
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sofascore.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive"
    }


def make_api_request(
    url: str, 
    max_retries: Optional[int] = None,
    timeout: Optional[int] = None
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye API isteği yapar.
    
    Args:
        url: API endpoint URL'si
        max_retries: Maksimum yeniden deneme sayısı (None ise çevre değişkeninden alınır)
        timeout: İstek zaman aşımı süresi (saniye) (None ise çevre değişkeninden alınır)
    
    Returns:
        Optional[JsonResponse]: JSON yanıtı veya hata durumunda None
    
    Raises:
        APIError: API hata durumlarında
        RateLimitError: Rate limiting durumunda 
        NetworkError: Ağ hatası durumunda
        DataParsingError: JSON ayrıştırma hatası durumunda
    """
    # Varsayılan değerleri çevre değişkenlerinden al
    max_retries = max_retries if max_retries is not None else MAX_RETRIES
    timeout = timeout if timeout is not None else REQUEST_TIMEOUT
    
    headers = get_request_headers()
    
    for attempt in range(max_retries):
        try:
            logger.info(f"API İsteği ({attempt+1}/{max_retries}): {url}")
            
            response = requests.get(url, headers=headers, timeout=timeout)
            
            # Rate limiting kontrolü
            if response.status_code == 429:  # Too Many Requests
                wait_time = min(60, 5 * (2 ** attempt))  # Exponential backoff: 5, a10, 20, ...
                logger.warning(f"Rate limit aşıldı. {wait_time} saniye bekleniyor...")
                time.sleep(wait_time)
                
                if attempt == max_retries - 1:
                    raise RateLimitError(wait_time)
                    
                continue
            
            # Diğer HTTP hatalarını kontrol et
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                raise APIError(f"HTTP hata: {str(e)}", response.status_code) from e
            
            # Başarılı yanıt
            try:
                data = response.json()
            except ValueError as e:
                raise DataParsingError(f"JSON ayrıştırma hatası: {str(e)}") from e
            
            # İnsan davranışını simüle etmek için kısa bekleme (optimize edilmiş)
            wait_time = WAIT_TIME_MIN + random.uniform(0, WAIT_TIME_MAX)
            logger.info(f"Başarılı! Sonraki istek için {wait_time:.1f} saniye bekleniyor...")
            time.sleep(wait_time)
            
            return cast(JsonResponse, data)
            
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            logger.error(f"Ağ hatası: {str(e)}")
            
            if attempt < max_retries - 1:
                wait_time = 3 * (2 ** attempt)  # Exponential backoff
                logger.info(f"{wait_time} saniye içinde yeniden deneniyor... ({attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise NetworkError(f"API isteği başarısız: {str(e)}") from e
        
        except (RateLimitError, APIError, DataParsingError):
            # Özel hataları yeniden yükselt
            raise
            
        except Exception as e:
            # Beklenmeyen hatalar için
            if attempt < max_retries - 1:
                wait_time = 3 * (2 ** attempt)
                logger.error(f"Beklenmeyen hata: {str(e)}. {wait_time} saniye içinde yeniden deneniyor...")
                time.sleep(wait_time)
            else:
                raise SofaScoreScraperError(f"API isteği sırasında beklenmeyen hata: {str(e)}") from e
    
    # Buraya ulaşılmamalı, ancak tip kontrolü için gerekli
    return None


async def make_api_request_async(
    session: aiohttp.ClientSession, 
    url: str, 
    max_retries: Optional[int] = None
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye asenkron API isteği yapar.
    
    Args:
        session: aiohttp.ClientSession nesnesi
        url: API endpoint URL'si
        max_retries: Maksimum yeniden deneme sayısı (None ise çevre değişkeninden alınır)
    
    Returns:
        Optional[JsonResponse]: JSON yanıtı veya hata durumunda None
        
    Raises:
        APIError: API hata durumlarında
        RateLimitError: Rate limiting durumunda 
        NetworkError: Ağ hatası durumunda
        DataParsingError: JSON ayrıştırma hatası durumunda
    """
    # Varsayılan değeri çevre değişkeninden al
    max_retries = max_retries if max_retries is not None else MAX_RETRIES
    
    # Tam URL oluştur
    if not url.startswith("http"):
        full_url = f"{API_BASE_URL}{url}"
    else:
        full_url = url
    
    headers = get_request_headers()
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Asenkron API İsteği ({attempt+1}/{max_retries}): {url}")
            
            async with session.get(full_url, headers=headers, ssl=False, timeout=aiohttp.ClientTimeout(total=30)) as response:
                # Rate limiting kontrolü
                if response.status == 429:  # Too Many Requests
                    wait_time = min(60, 5 * (2 ** attempt))  # Exponential backoff
                    logger.warning(f"Rate limit aşıldı. {wait_time} saniye bekleniyor...")
                    await asyncio.sleep(wait_time)
                    
                    if attempt == max_retries - 1:
                        raise RateLimitError(wait_time)
                        
                    continue
                
                # Diğer HTTP hatalarını kontrol et
                if response.status >= 400:
                    error_text = await response.text()
                    error_msg = f"HTTP hata {response.status}: {response.reason}. Yanıt: {error_text[:200]}"
                    logger.error(error_msg)
                    raise APIError(error_msg, response.status)
                
                # Başarılı yanıt
                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as e:
                    response_text = await response.text()
                    error_msg = f"JSON ayrıştırma hatası: {str(e)}. İlk 200 karakter: {response_text[:200]}"
                    logger.error(error_msg)
                    raise DataParsingError(error_msg) from e
                
                # Rate limiting için minimal bekleme
                wait_time = WAIT_TIME_MIN * (random.random() * 0.5)  # 0.0-0.1 sn arası (optimize edilmiş)
                await asyncio.sleep(wait_time)
                
                return cast(JsonResponse, data)
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Ağ hatası: {str(e)}")
            
            if attempt < max_retries - 1:
                wait_time = 3 * (2 ** attempt)  # Exponential backoff
                logger.info(f"{wait_time} saniye içinde yeniden deneniyor... ({attempt+1}/{max_retries})")
                await asyncio.sleep(wait_time)
            else:
                error_msg = f"Asenkron API isteği başarısız: {str(e)} - URL: {full_url}"
                logger.error(error_msg)
                raise NetworkError(error_msg) from e
                
        except (RateLimitError, APIError, DataParsingError):
            # Özel hataları yeniden yükselt
            raise
            
        except Exception as e:
            # Beklenmeyen hatalar için
            logger.error(f"Beklenmeyen hata türü: {type(e).__name__}")
            if attempt < max_retries - 1:
                wait_time = 3 * (2 ** attempt)
                logger.error(f"Beklenmeyen hata: {str(e)}. {wait_time} saniye içinde yeniden deneniyor...")
                await asyncio.sleep(wait_time)
            else:
                import traceback
                logger.error(f"Hata detayı: {traceback.format_exc()}")
                error_msg = f"Asenkron API isteği sırasında beklenmeyen hata: {str(e)} - URL: {full_url}"
                raise SofaScoreScraperError(error_msg) from e
    
    # Buraya ulaşılmamalı, ancak tip kontrolü için gerekli
    return None


def ensure_directory(directory_path: Union[str, Path]) -> bool:
    """
    Belirtilen dizinin var olduğundan emin olur.
    
    Args:
        directory_path: Oluşturulacak dizin yolu
    
    Returns:
        bool: Başarılı ise True, değilse False
    """
    try:
        # Path nesnesine dönüştür
        path = Path(directory_path)
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Dizin oluşturma hatası ({directory_path}): {str(e)}")
        return False


def create_session_async() -> aiohttp.ClientSession:
    """
    Asenkron API istekleri için bir aiohttp.ClientSession nesnesi oluşturur.
    
    Returns:
        aiohttp.ClientSession: Yapılandırılmış bir session nesnesi
    """
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    return aiohttp.ClientSession(
        headers=get_request_headers(),
        timeout=timeout,
        raise_for_status=False  # Hatayı kendimiz ele alacağız
    )