"""
İstek katmanı: SofaScore'a giden her HTTP isteğinin gövdesi (curl_cffi taşıyıcısı ve tarayıcı köprüsüne düşüş).

Buradaki her şey src/utils.py'den taşındı (plan maddesi P05; docs/design/02-services.md 2.2 ve 2.4): yeniden
deneme, geri çekilme, tipli hatalar, ortak istek bütçesi, "önce tarayıcı" modu, oturum ısınması. src/utils.py
aynı adları yeniden dışa aktarır ve kendisine yapılan atamaları buraya iletir; eski import'lar ve
`utils._sleep = ...` biçimindeki yamalar çalışmaya devam eder.

İptal kontrolü ve bekleme bildirimi src/client/context.py'dedir. Bu modül DATA_DIR altına hiçbir şey yazmaz.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Final, Optional, Tuple, Union, cast

# curl-cffi ile istekler (Cloudflare bypass için)
from curl_cffi import requests as cffi_requests
from curl_cffi.requests import AsyncSession

from src import breaker, throttle
from src.client.context import FetchCancelled, _notify_wait, raise_if_cancelled
from src.client.endpoints import DEFAULT_BASE_URL

# .env bu import sırasında yüklenir (src/config_manager.py); aşağıdaki os.getenv ondan sonra okunmalı
from src.config_manager import ConfigManager
from src.exceptions import (
    APIError,
    CircuitOpenError,
    DataParsingError,
    NetworkError,
    RateLimitError,
    ResourceNotFoundError,
    SofaScoreScraperError,
)
from src.logger import get_logger
from src.slices import OutcomeVia

# Günlükçü adı taşınmadan önceki gibi: log satırlarındaki ad değişmesin
logger = get_logger("Utils")

def _configured_base_url() -> str:
    """API_BASE_URL ayarı. Boş bırakılan ayar varsayılan köktür; sondaki "/" atılır (yollar "/" ile başlar)."""
    return (os.getenv("API_BASE_URL") or "").strip().rstrip("/") or DEFAULT_BASE_URL


# API kökü: süreç başlarken bir kez okunur ve her isteğe uygulanır (base_url / api_url)
API_BASE_URL: str = _configured_base_url()

# İstek ve proxy ayarları her istekte buradan okunur (çalışırken değişen ayar bir sonraki isteğe uygulanır)
_cm = ConfigManager()

# Tip tanımı
JsonResponse = Dict[str, Any]

VIA_CURL: Final = "curl"
VIA_BRIDGE: Final = "bridge"


@dataclass
class RequestTrace:
    """
    Bir isteğin nasıl sonuçlandığının izi; gövde doldurur, Client okur (Outcome.via / http_status).

    via          yanıtı getiren yol: "curl" ya da "bridge"; hiç yanıt alınmadıysa (bağlantı hatası, açık devre) None
    http_status  curl'den gelen son yanıtın durum kodu; yanıt köprüden geldiyse None (köprü kodu bildirmez)
    """

    via: Optional[OutcomeVia] = None
    http_status: Optional[int] = None

    def attempt(self) -> None:
        """Yeni bir deneme başlıyor: önceki denemenin izi son hali anlatmaz."""
        self.via, self.http_status = None, None

    def curl(self, status_code: int) -> None:
        self.via, self.http_status = VIA_CURL, status_code

    def bridge(self) -> None:
        self.via, self.http_status = VIA_BRIDGE, None


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


# ---- Ortak istek bütçesi (src/throttle.py, issue #16) ----
# SofaScore'a giden her curl isteğinin hemen öncesinde çağrılır; tarayıcı köprüsü kendi
# isteklerinde aynı bütçeyi kullanır (BrowserBridge._api_fetch). Bekleme iptal edilebilir; iptal
# edilen istek gönderilmediği için sırası bütçeye geri verilir (sonraki istek onun arkasında beklemez).

def _throttle() -> None:
    delay = throttle.reserve()
    if delay > 0:
        with throttle.give_back_if_interrupted(delay):
            _sleep(delay)


async def _athrottle() -> None:
    delay = throttle.reserve()
    if delay > 0:
        with throttle.give_back_if_interrupted(delay):
            await _asleep(delay)


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
    """Köprü sonucunu yorumla: veri ya da 404 (ResourceNotFoundError)."""
    if isinstance(data, dict) and data.get("__404__"):
        raise ResourceNotFoundError(f"Kaynak bulunamadı: {url}")
    return cast(JsonResponse, data)


# 403/404/429 dışındaki 4xx yanıtlar kalıcıdır: yeniden denemek yalnızca zaman kaybettirir
def _is_transient_status(status_code: int) -> bool:
    return status_code >= 500 or status_code in (403, 408, 429)


def base_url() -> str:
    """API kökü: her isteğin adresinin başı (süreç başlarken okunan API_BASE_URL). Kök yalnızca buradan alınır."""
    return API_BASE_URL


def api_url(path: str) -> str:
    """API köküne göre yol ("/event/1") → tam adres."""
    return f"{API_BASE_URL}{path}"


def _full_url(url: str) -> str:
    return url if url.startswith("http") else api_url(url)


def _retry_wait(attempt: int) -> float:
    return 3 * (2 ** attempt)


def make_api_request(
    url: str,
    max_retries: Optional[int] = None,
    timeout: Optional[float] = None,
    raise_on_failure: bool = False,
    raise_errors: bool = False,
    trace: Optional[RequestTrace] = None,
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye API isteği yapar (curl_cffi kullanarak).

    404 ve tüm denemeler tükendiğinde None döner (senkron çağıranlar None bekler).
    raise_on_failure=True ise None yerine asenkron sürümle aynı tipli hatalar fırlatılır
    (404 → ResourceNotFoundError, 429/503 → RateLimitError, diğer HTTP → APIError, bağlantı →
    NetworkError, bozuk yanıt → DataParsingError): çağıran "kaynak yok" ile "istek başarısız"ı
    ayırabilsin. Her iki durumda isteğin son hali işin devre kesicisine bildirilir (src/breaker.py).
    Son denemeden sonra beklenmez; kalıcı 4xx hataları yeniden denenmez.

    raise_errors, raise_on_failure ile aynı anahtardır (ikisinden biri True ise hata fırlatılır):
    nedeni kullanıcıya göstermesi gereken çağıranlar bu adı kullanır (web: lig arama, sezon
    yenileme; src/web/upstream.py hatayı `reason`a çevirir).

    trace verilirse yanıtı getiren yol ve HTTP durum kodu ona yazılır (RequestTrace).
    """
    try:
        data = _request_sync(url, max_retries, timeout, trace)
    except SofaScoreScraperError as e:
        breaker.report_exception(e)
        if raise_on_failure or raise_errors:
            raise
        return None
    breaker.report_ok()
    return data


def _request_sync(
    url: str, max_retries: Optional[int], timeout: Optional[float], trace: Optional[RequestTrace] = None
) -> Optional[JsonResponse]:
    """make_api_request'in gövdesi: veri döndürür ya da isteği bitiren tipli hatayı fırlatır."""
    trace = trace if trace is not None else RequestTrace()
    runtime_config = _get_runtime_request_config()
    max_retries = max(1, max_retries if max_retries is not None else int(runtime_config["max_retries"]))
    timeout = timeout if timeout is not None else int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    full_url = _full_url(url)

    breaker.check(url)
    if _browser_first():
        raise_if_cancelled()
        from src.challenge_solver import fetch_api_via_browser_sync
        data = fetch_api_via_browser_sync(full_url)
        if data is not None:
            trace.bridge()
            try:
                _sleep(wait_time_min + random.uniform(0, wait_time_max))
            except FetchCancelled:
                # Yanıt elimizde: curl yolundaki gibi döndür, iptal bir sonraki istekte işlensin (FX-18)
                pass
            return _browser_result(data, url)
        # Köprü başarısız: aşağıda curl ile normal yoldan dene

    # İsteği bitiren hata: döngüden `break` ile çıkılır ve sonda fırlatılır (try içinde fırlatılsa
    # aşağıdaki geniş except onu bağlantı hatası sanıp yeniden denerdi)
    failure: Exception = NetworkError(f"İstek başarısız: {url}")
    for attempt in range(max_retries):
        raise_if_cancelled()
        breaker.check(url)  # devre açıksa istek gönderilmez, ortak bütçeden sıra ayrılmaz
        trace.attempt()
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

            _throttle()
            response = cffi_requests.get(full_url, **kwargs)
            trace.curl(response.status_code)

            if response.status_code in (429, 503):
                if last_attempt:
                    logger.error(f"Rate limit/Sunucu meşgul, denemeler tükendi: {url}")
                    failure = RateLimitError(status_code=response.status_code, url=url)
                    break
                default_wait = min(60, 5 * (2 ** attempt))
                wait_time = _parse_retry_after_seconds(response.headers.get("Retry-After"), default_wait)
                logger.warning(f"Rate limit/Sunucu meşgul ({response.status_code}). {wait_time} saniye bekleniyor...")
                _notify_wait("rate_limit", wait_time)
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
                        browser_data = fetch_api_via_browser_sync(full_url)
                        if browser_data is not None:
                            trace.bridge()
                            _mark_browser_first()
                            logger.debug("Veri BrowserBridge üzerinden alındı")
                    except Exception as te:
                        logger.debug(f"BrowserBridge hatası: {te}")
                        browser_data = None
                    if isinstance(browser_data, dict) and browser_data.get("__404__"):
                        failure = ResourceNotFoundError(f"Kaynak bulunamadı: {url}")
                        break
                    if browser_data is not None:
                        return cast(JsonResponse, browser_data)
                if last_attempt:
                    logger.error(f"403 Forbidden, denemeler tükendi: {url}")
                    failure = APIError(f"HTTP 403 Forbidden: {url}", status_code=403)
                    break
                _notify_wait("forbidden", min(120, 10 * (2 ** attempt)))
                _sleep(min(120, 10 * (2 ** attempt)))
                continue

            # 404 = missing resource (e.g. pregame-form). Never retry — burns cancel latency.
            if response.status_code == 404:
                logger.debug(f"Kaynak bulunamadı (404): {url}")
                failure = ResourceNotFoundError(f"Kaynak bulunamadı: {url}")
                break

            if response.status_code >= 400:
                if last_attempt or not _is_transient_status(response.status_code):
                    logger.error(f"HTTP hata: {response.status_code} {response.reason} — {url}")
                    failure = APIError(
                        f"HTTP {response.status_code} {response.reason}: {url}", status_code=response.status_code
                    )
                    break
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
                if isinstance(e, DataParsingError):
                    failure = e
                else:
                    failure = NetworkError(f"İstek başarısız: {url}: {e}")
                    failure.__cause__ = e
                break
            logger.info(f"{_retry_wait(attempt)} saniye içinde yeniden deneniyor... ({attempt+1}/{max_retries})")
            _sleep(_retry_wait(attempt))

    raise failure


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
    max_retries: Optional[int] = None,
    *,
    timeout: Optional[float] = None,
    trace: Optional[RequestTrace] = None,
) -> Optional[JsonResponse]:
    """
    Belirtilen URL'ye asenkron API isteği yapar (curl_cffi AsyncSession ile).

    Denemeler tükendiğinde tipli hata fırlatır: 404 → ResourceNotFoundError, 429/503 →
    RateLimitError, diğer HTTP hataları → APIError(status_code), bağlantı hataları → NetworkError.
    İsteğin son hali (başarı, 404 ya da hata) işin devre kesicisine bildirilir (src/breaker.py);
    kesici açıksa istek gönderilmez (CircuitOpenError).
    Son denemeden sonra beklenmez; kalıcı 4xx hataları yeniden denenmez.

    timeout verilmezse REQUEST_TIMEOUT ayarı kullanılır. trace verilirse yanıtı getiren yol ve HTTP durum
    kodu ona yazılır (RequestTrace).
    """
    try:
        data = await _request_async(session, url, max_retries, timeout, trace)
    except SofaScoreScraperError as e:
        breaker.report_exception(e)
        raise
    breaker.report_ok()
    return data


async def _request_async(
    session: AsyncSession,
    url: str,
    max_retries: Optional[int] = None,
    timeout: Optional[float] = None,
    trace: Optional[RequestTrace] = None,
) -> Optional[JsonResponse]:
    """make_api_request_async'in gövdesi."""
    trace = trace if trace is not None else RequestTrace()
    runtime_config = _get_runtime_request_config()
    max_retries = max(1, max_retries if max_retries is not None else int(runtime_config["max_retries"]))
    request_timeout = timeout if timeout is not None else int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    full_url = _full_url(url)
    semaphore = _request_semaphore()

    breaker.check(url)
    if _browser_first():
        raise_if_cancelled()
        browser_data = None
        try:
            from src.challenge_solver import fetch_api_via_browser
            async with semaphore:
                # Semafor beklenirken iş durdurulmuş olabilir (aşağıdaki curl yoluyla aynı kural)
                raise_if_cancelled()
                browser_data = await fetch_api_via_browser(full_url)
        except Exception as e:
            logger.debug(f"BrowserBridge hatası: {e!r}")
        if browser_data is not None:
            trace.bridge()
        if isinstance(browser_data, dict) and browser_data.get("__404__"):
            raise ResourceNotFoundError(f"Kaynak bulunamadı: {url}")
        if browser_data is not None:
            try:
                await _asleep(wait_time_min + random.uniform(0, wait_time_max))
            except FetchCancelled:
                # Yanıt elimizde: curl yolundaki gibi döndür, iptal bir sonraki istekte işlensin
                # (FX-18, plan bölüm 15 satır 91)
                pass
            return cast(JsonResponse, browser_data)
        # Köprü başarısız: aşağıda curl ile normal yoldan dene

    for attempt in range(max_retries):
        raise_if_cancelled()
        trace.attempt()
        last_attempt = attempt == max_retries - 1
        try:
            logger.debug(f"Asenkron API İsteği ({attempt+1}/{max_retries}): {url}")

            # Her istekte yeniden: 30 dk'lık hash ve yeni çözülen captcha token güncel kalsın
            kwargs: Dict[str, Any] = {"timeout": request_timeout, "headers": get_request_headers()}
            if use_proxy and proxy_url:
                kwargs["proxy"] = proxy_url

            async with semaphore:
                # Semafor beklenirken iş durdurulmuş olabilir. Kontrol bütçeden sıra ayırmadan önce yapılır:
                # zamanı gelmiş bir sıra beklenmez ve geri verilmez, yani ayrıldıktan sonra istek gider.
                # Deneme başındaki kontrol bunu yakalamaz: o, semafor beklenmeden önce yapılır.
                raise_if_cancelled()
                # Sıra beklerken devre kesilmiş olabilir: istek gönderilmez, ortak bütçeden sıra ayrılmaz
                breaker.check(url)
                await _athrottle()
                response = await session.get(full_url, **kwargs)
        except CircuitOpenError:
            raise
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
        trace.curl(status)

        if status in (429, 503):
            if last_attempt:
                raise RateLimitError(status_code=status, url=url)
            default_wait = min(60, 5 * (2 ** attempt))
            wait_time = _parse_retry_after_seconds(response.headers.get("Retry-After"), default_wait)
            logger.warning(f"Rate limit/Sunucu meşgul ({status}). {wait_time} saniye bekleniyor...")
            _notify_wait("rate_limit", wait_time)
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
                    browser_data = await fetch_api_via_browser(full_url)
                except Exception as te:
                    logger.debug(f"BrowserBridge hatası: {te!r}")
                if browser_data is not None:
                    trace.bridge()
                if isinstance(browser_data, dict) and browser_data.get("__404__"):
                    raise ResourceNotFoundError(f"Kaynak bulunamadı: {url}")
                if browser_data is not None:
                    _mark_browser_first()
                    logger.debug("Veri BrowserBridge üzerinden alındı")
                    return cast(JsonResponse, browser_data)
            if last_attempt:
                raise APIError(f"HTTP 403 Forbidden: {url}", status_code=403)
            _notify_wait("forbidden", min(120, 10 * (2 ** attempt)))
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
        await throttle.wait_async()  # warm-up da SofaScore'a giden bir istek: ortak bütçeden
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
