"""
İstek katmanı: SofaScore'a giden her HTTP isteğinin gövdesi (curl_cffi taşıyıcısı ve tarayıcı köprüsüne düşüş).

Buradaki her şey 2.x'in sofascore_scraper/utils.py'sinden taşındı (plan maddesi P05; docs/design/02-services.md 2.2 ve
2.4): yeniden deneme, geri çekilme, tipli hatalar, ortak istek bütçesi, "önce tarayıcı" modu, oturum ısınması. Adları
yeniden dışa aktaran utils 3.1'de kalktı (plan maddesi P30).

İptal kontrolü ve bekleme bildirimi sofascore_scraper/client/context.py'dedir. Bu modül DATA_DIR altına hiçbir şey yazmaz.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import random
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Final, Optional, Tuple, Union, cast

# curl-cffi ile istekler (Cloudflare bypass için)
from curl_cffi import requests as cffi_requests
from curl_cffi.requests import AsyncSession

from sofascore_scraper import breaker, bridge_health, throttle
from sofascore_scraper.client.context import FetchCancelled, _notify_wait, notify_request, raise_if_cancelled
from sofascore_scraper.client.endpoints import DEFAULT_BASE_URL

# .env bu import sırasında ortama yüklenir (sofascore_scraper/config_manager.py); ayarlar ondan sonra okunur
from sofascore_scraper.config_manager import ConfigManager
from sofascore_scraper.exceptions import (
    APIError,
    CircuitOpenError,
    DataParsingError,
    NetworkError,
    RateLimitError,
    ResourceNotFoundError,
    SofaScoreScraperError,
)
from sofascore_scraper.logger import get_logger
from sofascore_scraper.slices import OutcomeVia

# Günlükçü adı taşınmadan önceki gibi: log satırlarındaki ad değişmesin
logger = get_logger("Utils")

def _configured_base_url() -> str:
    """
    `client.base_url` ayarı (`effective_base_url`): boş bırakılan ayar varsayılan köktür; sondaki "/" atılır
    (yollar "/" ile başlar). Ayarlar kurulamıyorsa (geçersiz yapılandırma) varsayılan kök.
    """
    from sofascore_scraper.config import loader

    try:
        return loader.active_settings().client.effective_base_url or DEFAULT_BASE_URL
    except Exception:
        return DEFAULT_BASE_URL


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


# ---- Ortak istek bütçesi (sofascore_scraper/throttle.py, issue #16) ----
# SofaScore'a giden her curl isteğinin hemen öncesinde çağrılır; tarayıcı köprüsü kendi
# isteklerinde aynı bütçeyi kullanır (BrowserBridge._api_fetch). Bekleme iptal edilebilir; iptal
# edilen istek gönderilmediği için sırası bütçeye geri verilir (sonraki istek onun arkasında beklemez).
# Sırası gelen istek işin bağlamına bildirilir, beklediği süreyle (`notify_request`; işin istek sayacı, B2).

def _throttle() -> None:
    delay = throttle.reserve()
    waited = 0.0
    if delay > 0:
        with throttle.give_back_if_interrupted(delay):
            _sleep(delay)
            # etkileşimli bir istek önüne geçtiyse (FX-23, F16)
            waited = float(delay) + (throttle.settle(delay, _sleep) or 0.0)
    notify_request(waited)


async def _athrottle() -> None:
    delay = throttle.reserve()
    waited = 0.0
    if delay > 0:
        with throttle.give_back_if_interrupted(delay):
            await _asleep(delay)
            waited = float(delay) + (await throttle.settle_async(delay, _asleep) or 0.0)
    notify_request(waited)


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
    Geçerli sofa_captcha JWT tokenini döndürür: önce elle verilen `client.captcha_token`
    (SOFASCORE_CLIENT__CAPTCHA_TOKEN), sonra köprünün önbelleği.
    """
    from sofascore_scraper.config import loader

    env_token = (loader.active_settings().client.captcha_token or "").strip()
    if env_token:
        return env_token
    try:
        from sofascore_scraper.client.bridge import get_cached_token
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


# Retry-After'ın iki biçiminin de üst sınırı (saniye)
RETRY_AFTER_CAP_SECONDS = 120


def _parse_retry_after_seconds(retry_after: Optional[str], default_wait: int) -> int:
    """
    Retry-After başlığı saniye olarak: tamsayı ya da HTTP tarihi, 1..RETRY_AFTER_CAP_SECONDS aralığında; yoksa ya da
    okunamıyorsa `default_wait`. Eskiden yalnızca tarih biçimi sınırlıydı: "Retry-After: 3600" diyen bir yanıt işi
    (ve iptal edilene kadar çağıranı) bir saat bekletirdi (c projesinin üretim düzeltmesi, B11).
    """
    if not retry_after:
        return default_wait
    try:
        return max(1, min(RETRY_AFTER_CAP_SECONDS, int(retry_after)))
    except ValueError:
        try:
            dt = parsedate_to_datetime(retry_after)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = int((dt - datetime.now(timezone.utc)).total_seconds())
            return max(1, min(RETRY_AFTER_CAP_SECONDS, delta))
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
        logger.info(f"curl is blocked; requests go through the browser for {int(BROWSER_FIRST_SECONDS)} s")
    _browser_first_until = time.monotonic() + BROWSER_FIRST_SECONDS


def _browser_result(data: Any, url: str) -> Optional[JsonResponse]:
    """Köprü sonucunu yorumla: veri ya da 404 (ResourceNotFoundError)."""
    if isinstance(data, dict) and data.get("__404__"):
        raise ResourceNotFoundError(f"Not found: {url}")
    return cast(JsonResponse, data)


def _needs_solve(status_code: int, text: str) -> bool:
    """
    Bir Turnstile çözümünün yanıtlayacağı 403: challenge ya da captcha token'ının reddi (`jwt:*`; köprünün
    needs_solve'u). c'nin 4. koşusu: curl'ün taşıdığı süresi dolmuş token `jwt:time-out-of-bounds` ile reddedildi ve
    istek köprüye hiç gitmedi.
    """
    from sofascore_scraper.client.bridge import needs_solve

    return needs_solve(status_code, text)


# 403/404/429 dışındaki 4xx yanıtlar kalıcıdır: yeniden denemek yalnızca zaman kaybettirir
def _is_transient_status(status_code: int) -> bool:
    return status_code >= 500 or status_code in (403, 408, 429)


def _active_bridge() -> Any:
    """Süreçte açılmış indirme köprüsü (BrowserBridge.get_instance); yoksa None. Köprü burada kurulmaz."""
    from sofascore_scraper.client.bridge import BrowserBridge

    return BrowserBridge._instance


# ---- Kapı: yanıtlanamayacak hiçbir şey gönderilmez (S1-B4; c'nin rest.Client._gate'i) ----
#
# Köprünün başarısız çözümü soğurken (BrowserBridge.solve_cooldown_left; 180, 180, 360, sonra 600 sn) her istek
# challenge'a takılır ve onu yanıtlayacak bir çözüm başlayamaz. c'nin üretim benzeri 3. koşusu iki beklemede
# reddedileceği belli yaklaşık 262 istek gönderdi (okuma başına 7'ye kadar: curl, tarayıcı, yine curl). Artık beklemede
# istek gönderilmez: hemen 403 olarak biter (APIError, gönderilmedi; işin devre kesicisine ve köprü sağlığına sayılır),
# süren bir istek bir sonraki denemesini yapmaz. Art arda CHALLENGE_FAILURES_ALERT istek challenge'da yanıtsız bittiyse
# kapı tutulur: bir yanıt (200 ya da 404) gelene kadar aynı anda tek istek gider (yoklama: tek deneme, tarayıcının),
# ötekiler gönderilmeden 403'le biter. Hiçbir çözümün yanıtlamadığı bir 403'e takılan yoklamanın ardından bir sonraki
# UNSOLVABLE_PROBE_PAUSE_S sonra gider, art arda her birinde iki katı, en çok UNSOLVABLE_PROBE_PAUSE_MAX_S.

CHALLENGE_FAILURES_ALERT = 3
UNSOLVABLE_PROBE_PAUSE_S = 30.0
UNSOLVABLE_PROBE_PAUSE_MAX_S = 300.0
# Art arda hiç yanıt almadan biten istekler (her denemesi taşıma hatası: ad yok, bağlantı yok, zaman aşımı), "SofaScore'a
# ulaşılamıyor" ERROR'ından önce (S1-A4; bir koşuda bir kez, sonraki her durumdaki yanıtta INFO "answers again")
TRANSPORT_FAILURES_ALERT = 3


@dataclass
class _Request:
    """Bir isteğin karşılaştıkları (bittiğinde sayaçlar ve kapı için)."""

    challenge_failed: bool = False  # tarayıcının yanıtlamadığı bir challenge'a takıldı ya da kapı tuttu
    browser_failed: bool = False  # tarayıcı bu isteğe hiç yanıt vermedi
    probe: bool = False  # kapının bıraktığı tek istek
    single: bool = False  # kapının yoklaması: tek deneme, tarayıcının
    unsolvable: bool = False  # hiçbir çözümün yanıtlamadığı bir 403'e takıldı
    # Son denemenin taşıma hatası (ya da ağın kapısı tuttu), o zamandan beri yanıt yok (S1-A4)
    transport_error: Optional[str] = None
    browser_unreached: bool = False  # tarayıcı durum 0 verdi: SofaScore'a hiç ulaşamadı


class _GateState:
    """Kapının süreç genelindeki durumu ve sayaçları (her iş ve thread aynı kapıdan geçer)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.held = False
        self.probe_out = False
        self.unsolvable_probes = 0
        self.probe_not_before = -math.inf
        self.skip_logged = False
        self.skipped_in_hold = 0
        self.challenges = 0  # curl'ün karşılaştığı 403 challenge'lar
        self.challenge_failures = 0  # onlardan tarayıcının yanıtlamadıkları
        # Challenge'da yanıtsız biten (ya da kapının tuttuğu) istekler; art arda olanlar son 200/404'ten beri
        self.challenge_failed_requests = 0
        self.consecutive_challenge_failed_requests = 0
        self.challenge_skipped = 0  # gönderilmeden 403'le biten istekler
        # Hiç yanıt almadan biten istekler (S1-A4); art arda olanlar herhangi bir durumdaki son yanıttan beri
        self.transport_failed_requests = 0
        self.consecutive_transport_failed_requests = 0
        self.transport_skipped = 0  # köprünün çözümü siteye ulaşamadığı için gönderilmeden biten istekler


_gate = _GateState()


def gate_status() -> Dict[str, int]:
    """Kapının sayaçları (bridge_health.requests(), tanılama paketi)."""
    g = _gate
    return {
        "challenges": g.challenges,
        "challenge_failures": g.challenge_failures,
        "challenge_failed_requests": g.challenge_failed_requests,
        "consecutive_challenge_failed_requests": g.consecutive_challenge_failed_requests,
        "challenge_skipped": g.challenge_skipped,
        "transport_failed_requests": g.transport_failed_requests,
        "consecutive_transport_failed_requests": g.consecutive_transport_failed_requests,
        "transport_skipped": g.transport_skipped,
    }


def _publish_gate() -> None:
    try:
        bridge_health.update_requests(gate_status())
    except Exception:  # durum yazımı isteği bozmamalı
        logger.debug("publishing the gate counters failed", exc_info=True)


def reset_gate() -> None:
    """Kapıyı ve sayaçlarını sıfırlar (testler; süreç başına bir kez başlar)."""
    _gate.reset()


def _solve_cooldown_left() -> float:
    """Köprünün başarısız çözümünün kalan beklemesi; köprü yoksa 0."""
    left = getattr(_active_bridge(), "solve_cooldown_left", None)
    if left is None:
        return 0.0
    try:
        return float(left())
    except Exception:
        return 0.0


def _bridge_unreached() -> bool:
    """Köprünün son çözümünün yüklemeleri siteye ulaşmadı (BrowserBridge.solve_unreached; S1-A4)."""
    return getattr(_active_bridge(), "solve_unreached", False) is True


def _probe_paused_s() -> float:
    """Çözümün yanıtlamadığı 403'lere takılan yoklamalardan sonra bir sonraki yoklamaya kalan saniye."""
    return max(0.0, _gate.probe_not_before - time.monotonic())


def _admit(req: _Request) -> Optional[str]:
    """
    Bu istek gönderilmeden mi biter ("challenge"), yoksa gider mi (None)? Kapı tutuluyorsa ve yoklama dışarıda değilse
    bu istek yoklamadır (tek deneme, tarayıcının).
    """
    cooling = _solve_cooldown_left() > 0
    if cooling and _bridge_unreached():
        # Köprünün çözümü siteye ulaşamadığı için soğuyor: ağın kesintisi, öyle sayılır (S1-A4)
        return "transport"
    with _gate.lock:
        if cooling or _gate.consecutive_challenge_failed_requests >= CHALLENGE_FAILURES_ALERT:
            _gate.held = True
        if cooling or (_gate.held and (_gate.probe_out or _probe_paused_s() > 0)):
            return "challenge"
        if _gate.held:
            req.probe = req.single = _gate.probe_out = True
            first = _gate.skip_logged
            _gate.skip_logged = False  # sonucu haberdir: yeni bir bekleme loglanır
        else:
            first = False
    if first:
        logger.info("One request goes first while the Turnstile challenge is not solved")
    return None


def _kept(req: _Request) -> Optional[str]:
    """
    Süren bir istek bir deneme daha yapmasın mı: çözüm soğuyor, ya da kapı tutuluyor ve bu onun yoklaması değil.
    c'de 2026-10-02'ye kadar (4. koşu) yalnızca bekleme durduruyordu: token reddedilmiş ve çözüm yokken uyarıdan sonraki
    istekler curl ve tarayıcı denemelerini sürdürdü, istek başına 7.
    """
    cooling = _solve_cooldown_left() > 0
    if cooling or (_gate.held and not req.probe):
        # Tarayıcısı hiçbir şeye ulaşamayan bir istek (durum 0: duvarda ağ kesildi, çözümünün yüklemeleri de) ağın
        # başarısızlığı olarak biter, challenge'ın değil (S1-A4: kesinti yalnızca challenge'ın ERROR'ı gibi görünürdü)
        if req.browser_unreached or (cooling and _bridge_unreached()):
            return "transport"
        return "challenge"
    return None


def _skip(url: str, why: str, req: _Request, *, record: bool = True) -> SofaScoreScraperError:
    """
    Kapının tuttuğu (ya da bir sonraki denemesinden önce bitirdiği) isteğin hatası: gönderilmemiş bir 403. İstek o
    yüzden yanıtsızdır, gönderilmiş gibi sayılır (işin devre kesicisi; `record`: köprü sağlığı, köprü kendisi yazmadıysa).
    """
    if why == "transport":
        req.transport_error = req.transport_error or "not sent (the browser could not reach SofaScore)"
        with _gate.lock:
            _gate.transport_skipped += 1
        logger.debug(f"Not sent (SofaScore cannot be reached): {url}")
        _publish_gate()
        return NetworkError(f"Request not sent: the browser could not reach SofaScore: {url}")
    req.challenge_failed = True
    with _gate.lock:
        _gate.challenge_skipped += 1
        _gate.skipped_in_hold += 1
        first = not _gate.skip_logged
        _gate.skip_logged = True
    if first:
        cooling = max(_solve_cooldown_left(), _probe_paused_s())
        logger.warning(
            "The Turnstile challenge is not solved; requests are answered 403 without being sent "
            + (f"for {cooling:.0f} s, then one goes first" if cooling > 0 else "while one goes first")
        )
    logger.debug(f"Not sent (the challenge's gate): {url}")
    if record:
        bridge_health.record_failure(bridge_health.KIND_CHALLENGE,
                                     "HTTP 403: not sent (the Turnstile challenge is not solved)")
    _publish_gate()
    return APIError(f"HTTP 403 Forbidden (not sent: the Turnstile challenge is not solved): {url}", status_code=403)


def _challenge_request_failed(url: str) -> None:
    """İstek challenge'da yanıtsız bitti (tarayıcının yanıtlamadığı bir challenge ya da kapı tuttu)."""
    with _gate.lock:
        _gate.challenge_failed_requests += 1
        _gate.consecutive_challenge_failed_requests += 1
        count = _gate.consecutive_challenge_failed_requests
        if count >= CHALLENGE_FAILURES_ALERT:
            _gate.held = True  # kapı buradan tutulur, süren istekler bir deneme daha yapmaz (_kept)
    if count == CHALLENGE_FAILURES_ALERT:
        bridge = _active_bridge()
        logger.error(
            f"The Turnstile challenge keeps failing ({count} request(s) in a row unanswered, the last on {url}; "
            f"{_gate.challenge_failures} challenge(s) and {getattr(bridge, 'solve_failures', 0)} solve(s) failed in "
            "all); nothing is being read"
        )


def _transport_request_failed(url: str, error: str) -> None:
    """İstek hiç yanıt almadan bitti: her denemesi taşıma hatası (S1-A4)."""
    with _gate.lock:
        _gate.transport_failed_requests += 1
        _gate.consecutive_transport_failed_requests += 1
        count = _gate.consecutive_transport_failed_requests
    if count == TRANSPORT_FAILURES_ALERT:
        logger.error(
            f"SofaScore cannot be reached ({count} request(s) in a row got no answer, the last on {url}: {error}); "
            "nothing is being read"
        )


def _reached() -> None:
    """SofaScore (ya da önündeki Cloudflare) yanıt verdi, durum ne olursa olsun (S1-A4)."""
    with _gate.lock:
        count = _gate.consecutive_transport_failed_requests
        _gate.consecutive_transport_failed_requests = 0
    if count >= TRANSPORT_FAILURES_ALERT:
        logger.info(f"SofaScore answers again after {count} request(s) got no answer")
    if count:
        _publish_gate()


def _probe_ended(req: _Request) -> None:
    """
    Kapının yoklaması yanıtsız bitti: hiçbir çözümün yanıtlamadığı bir 403'e takıldıysa ya da tarayıcı yanıt
    vermediyse sonraki UNSOLVABLE_PROBE_PAUSE_S sonra gider, art arda iki katı (onları bir çözümün beklemesi aralamaz).
    """
    if not (req.unsolvable or req.browser_failed):
        return
    with _gate.lock:
        _gate.unsolvable_probes += 1
        pause = min(UNSOLVABLE_PROBE_PAUSE_MAX_S, UNSOLVABLE_PROBE_PAUSE_S * 2 ** (_gate.unsolvable_probes - 1))
        _gate.probe_not_before = time.monotonic() + pause
        probes = _gate.unsolvable_probes
    logger.warning(
        f"{'A 403 no solve answers' if req.unsolvable else 'The browser gave no answer'} ({probes} probe(s) in a row); "
        f"the next request goes in {int(pause)} s"
    )


def _answered() -> None:
    """
    SofaScore isteğe yanıt verdi (200 ya da 404; curl'ün ya da köprünün): kapı açılır ve köprünün başarısız
    çözümleri artık art arda değildir (BrowserBridge.reset_solve_streak, c'nin B2'si).
    """
    with _gate.lock:
        count, skipped = _gate.consecutive_challenge_failed_requests, _gate.skipped_in_hold
        _gate.consecutive_challenge_failed_requests = 0
        _gate.held = False
        _gate.unsolvable_probes = 0
        _gate.probe_not_before = -math.inf
        _gate.skip_logged = False
        _gate.skipped_in_hold = 0
    if count >= CHALLENGE_FAILURES_ALERT:
        logger.info(
            f"SofaScore answers again after {count} request(s) failed on the challenge"
            + (f" ({skipped} of them not sent, or not retried)" if skipped else "")
        )
    if count:
        _publish_gate()
    bridge = _active_bridge()
    reset = getattr(bridge, "reset_solve_streak", None)
    if reset is not None:
        try:
            reset()
        except Exception:  # durum yazımı isteği bozmamalı
            logger.debug("resetting the solve streak failed", exc_info=True)


def _request_done(url: str, req: _Request, exc: Optional[BaseException]) -> None:
    """İsteğin son hali kapıya ve sayaçlara: yanıt (200, 404) ya da yanıtsız bitiş."""
    if req.probe:
        _gate.probe_out = False
    if exc is None or isinstance(exc, ResourceNotFoundError):
        _answered()
        return
    if isinstance(exc, CircuitOpenError):
        return  # devre kesildi: istek gönderilmedi, kapı hakkında bir şey söylemez
    if req.challenge_failed:
        _challenge_request_failed(url)
        _publish_gate()
    if req.transport_error is not None:
        _transport_request_failed(url, req.transport_error)
        _publish_gate()
    if req.single and isinstance(exc, SofaScoreScraperError):
        _probe_ended(req)


def _count(field: str) -> None:
    with _gate.lock:
        setattr(_gate, field, getattr(_gate, field) + 1)


def _browser_sync(full_url: str, req: _Request) -> "Tuple[Any, Any]":
    """Köprüden tek deneme (sync); sonucu ve nasıl bittiği (bridge.BrowserOutcome)."""
    from sofascore_scraper.client import bridge as cs

    with cs.observe(hold=lambda: _kept(req)) as seen:
        data = cs.fetch_api_via_browser_sync(full_url)
    return data, seen


async def _browser_async(full_url: str, req: _Request) -> "Tuple[Any, Any]":
    """Köprüden tek deneme (async); sonucu ve nasıl bittiği. Köprünün hatası yükselir."""
    from sofascore_scraper.client import bridge as cs

    with cs.observe(hold=lambda: _kept(req)) as seen:
        data = await cs.fetch_api_via_browser(full_url)
    return data, seen


def _browser_answered(seen: Any) -> None:
    """Tarayıcının sayfası SofaScore'a ulaştı (durum 0 değil): taşıma serisi biter (S1-A4)."""
    if getattr(seen, "status", None):
        _reached()


def _browser_unanswered(req: _Request, seen: Any) -> None:
    """Tarayıcı bu isteğe 200/404 vermedi: 403'le yanıtladıysa challenge'da yanıtsızdır, hiç yanıt yoksa başarısızdır."""
    status = getattr(seen, "status", None)
    if status == 0:
        req.browser_unreached = True  # sayfa da SofaScore'a ulaşamadı
    if status:
        req.transport_error = None  # SofaScore sayfaya yanıt verdi
    if status == 403:
        _count("challenge_failures")
        req.challenge_failed = True
        if not _needs_solve(403, getattr(seen, "text", "") or ""):
            req.unsolvable = True
    else:
        req.browser_failed = True


def _challenge_unanswered(req: _Request, seen: Any) -> None:
    """
    curl'ün challenge'ını tarayıcı yanıtlamadı (hiç yanıt vermedi ya da 403'le döndü): istek challenge'da yanıtsızdır.
    """
    _count("challenge_failures")
    req.challenge_failed = True
    status = getattr(seen, "status", None)
    if status == 0:
        req.browser_unreached = True
    if status is None or status == 0:
        req.browser_failed = True
    elif status == 403 and not _needs_solve(403, getattr(seen, "text", "") or ""):
        req.unsolvable = True


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
    ayırabilsin. Her iki durumda isteğin son hali işin devre kesicisine bildirilir (sofascore_scraper/breaker.py).
    Son denemeden sonra beklenmez; kalıcı 4xx hataları yeniden denenmez.

    raise_errors, raise_on_failure ile aynı anahtardır (ikisinden biri True ise hata fırlatılır):
    nedeni kullanıcıya göstermesi gereken çağıranlar bu adı kullanır (web: lig arama, sezon
    yenileme; sofascore_scraper/web/upstream.py hatayı `reason`a çevirir).

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
    breaker.check(url)
    req = _Request()
    why = _admit(req)
    if why is not None:
        skipped = _skip(url, why, req)
        _request_done(url, req, skipped)
        raise skipped
    try:
        data = _request_sync_body(url, max_retries, timeout, trace, req)
    except BaseException as e:
        _request_done(url, req, e)
        raise
    _request_done(url, req, None)
    return data


def _request_sync_body(
    url: str, max_retries: Optional[int], timeout: Optional[float], trace: RequestTrace, req: _Request
) -> Optional[JsonResponse]:
    """Kapıdan geçen isteğin denemeleri (_request_sync)."""
    runtime_config = _get_runtime_request_config()
    max_retries = max(1, max_retries if max_retries is not None else int(runtime_config["max_retries"]))
    timeout = timeout if timeout is not None else int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    full_url = _full_url(url)

    if _browser_first() or req.single:
        # Kapının yoklaması sayfası çözen tarayıcıdan gider: tek istek (c'de 2026-10-02'ye kadar 7'ye kadar)
        raise_if_cancelled()
        data, seen = _browser_sync(full_url, req)
        _browser_answered(seen)
        if seen.status == 403 and not seen.sent:
            # Köprü göndermedi: çözüm soğuyor; sağlığa kendisi yazdı. Gönderilmemiş bir durum 0 (tarayıcı siteye
            # ulaşamadı, S1-A4) tarayıcının başarısızlığıdır: aşağıda curl (kapı onu da ağın sayar)
            raise _skip(url, "challenge", req, record=False)
        if data is not None:
            trace.bridge()
            req.challenge_failed = False
            try:
                _sleep(wait_time_min + random.uniform(0, wait_time_max))
            except FetchCancelled:
                # Yanıt elimizde: curl yolundaki gibi döndür, iptal bir sonraki istekte işlensin (FX-18)
                pass
            return _browser_result(data, url)
        _browser_unanswered(req, seen)
        # Köprü başarısız: aşağıda curl ile normal yoldan dene (yoklama: tek deneme)

    attempts = 1 if req.single else max_retries
    # İsteği bitiren hata: döngüden `break` ile çıkılır ve sonda fırlatılır (try içinde fırlatılsa
    # aşağıdaki geniş except onu bağlantı hatası sanıp yeniden denerdi)
    failure: Exception = NetworkError(f"Request failed: {url}")
    for attempt in range(attempts):
        raise_if_cancelled()
        if attempt and (why := _kept(req)) is not None:
            # Bu arada bir çözüm başarısız oldu (bu isteğin ya da başkasının): kalan her deneme challenge'a takılırdı
            failure = _skip(url, why, req)
            break
        breaker.check(url)  # devre açıksa istek gönderilmez, ortak bütçeden sıra ayrılmaz
        trace.attempt()
        last_attempt = attempt == attempts - 1
        try:
            logger.debug(f"API request ({attempt+1}/{max_retries}): {url}")

            kwargs: Dict[str, Any] = {
                # Her istekte yeniden: 30 dk'lık hash ve yeni çözülen captcha token güncel kalsın
                "headers": get_request_headers(),
                "timeout": timeout,
                "impersonate": random.choice(IMPERSONATE_PROFILES),
            }
            if use_proxy and proxy_url:
                kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

            _throttle()
            if (why := _kept(req)) is not None:
                # Sıra beklenirken geldi (bir işin istekleri birbirinin arkasında bekler)
                failure = _skip(url, why, req)
                break
            response = cffi_requests.get(full_url, **kwargs)
            trace.curl(response.status_code)
            _reached()
            req.transport_error = None

            if response.status_code in (429, 503):
                if last_attempt:
                    logger.error(f"Rate limited or server busy, no attempts left: {url}")
                    failure = RateLimitError(status_code=response.status_code, url=url)
                    break
                default_wait = min(60, 5 * (2 ** attempt))
                wait_time = _parse_retry_after_seconds(response.headers.get("Retry-After"), default_wait)
                logger.warning(f"Rate limited or server busy ({response.status_code}); waiting {wait_time} s")
                _notify_wait("rate_limit", wait_time)
                _sleep(wait_time)
                continue

            if response.status_code == 403:
                logger.warning(f"403 Forbidden (attempt {attempt+1}/{max_retries}): {url}")
                logger.debug(f"cf-ray: {response.headers.get('cf-ray', 'none')}, cf-mitigated: {response.headers.get('cf-mitigated', 'none')}")
                raise_if_cancelled()
                if _needs_solve(403, response.text) and not (req.single and req.browser_failed):
                    _count("challenges")
                    logger.info("Turnstile challenge detected; fetching through the browser bridge")
                    seen: Any = None
                    try:
                        browser_data, seen = _browser_sync(full_url, req)
                        _browser_answered(seen)
                        if browser_data is not None:
                            trace.bridge()
                            _mark_browser_first()
                            logger.debug("Fetched through the browser bridge")
                    except Exception as te:
                        logger.debug(f"Browser bridge error: {te}")
                        browser_data = None
                    if seen is not None and seen.status == 403 and not seen.sent:
                        # Başka bir isteğin çözümü bu istek sırasını beklerken başarısız oldu: gönderilmedi
                        _count("challenge_failures")
                        failure = _skip(url, "challenge", req, record=False)
                        break
                    if isinstance(browser_data, dict) and browser_data.get("__404__"):
                        failure = ResourceNotFoundError(f"Not found: {url}")
                        break
                    if browser_data is not None:
                        req.challenge_failed = False
                        return cast(JsonResponse, browser_data)
                    _challenge_unanswered(req, seen)
                elif not _needs_solve(403, response.text):
                    req.unsolvable = True
                if last_attempt:
                    logger.error(f"403 Forbidden, no attempts left: {url}")
                    failure = APIError(f"HTTP 403 Forbidden: {url}", status_code=403)
                    break
                if _solve_cooldown_left() > 0:
                    # Çözüm başarısız oldu: hiçbir yeniden deneme geçmez (siteye ulaşamadıysa ağın, S1-A4)
                    failure = _skip(url, _kept(req) or "challenge", req)
                    break
                _notify_wait("forbidden", min(120, 10 * (2 ** attempt)))
                _sleep(min(120, 10 * (2 ** attempt)))
                continue

            # 404 = missing resource (e.g. pregame-form). Never retry — burns cancel latency.
            if response.status_code == 404:
                logger.debug(f"Not found (404): {url}")
                failure = ResourceNotFoundError(f"Not found: {url}")
                break

            if response.status_code >= 400:
                if last_attempt or not _is_transient_status(response.status_code):
                    logger.error(f"HTTP error: {response.status_code} {response.reason}: {url}")
                    failure = APIError(
                        f"HTTP {response.status_code} {response.reason}: {url}", status_code=response.status_code
                    )
                    break
                logger.warning(f"HTTP {response.status_code}, retrying in {_retry_wait(attempt)} s: {url}")
                _sleep(_retry_wait(attempt))
                continue

            try:
                data = response.json()
            except ValueError as e:
                raise DataParsingError(f"JSON parse error: {str(e)}") from e

            # İnsan davranışını simüle etmek için kısa bekleme
            wait_time = wait_time_min + random.uniform(0, wait_time_max)
            try:
                _sleep(wait_time)
            except FetchCancelled:
                # Veri zaten elimizde: bu yanıtı döndür, iptal bir sonraki istekte işlensin.
                pass

            return cast(JsonResponse, data)

        except Exception as e:
            if not isinstance(e, DataParsingError):
                req.transport_error = f"{type(e).__name__}: {e}"[:200]
            if "curl: (7)" in str(e) or "Failed to connect" in str(e):
                logger.error(f"Proxy or connection error: {str(e)} (proxy {'on' if use_proxy else 'off'})")
            else:
                logger.error(f"Request error: {str(e)}")
            if last_attempt:
                logger.error(f"All attempts failed: {url}")
                if isinstance(e, DataParsingError):
                    failure = e
                else:
                    failure = NetworkError(f"Request failed: {url}: {e}")
                    failure.__cause__ = e
                break
            logger.info(f"Retrying in {_retry_wait(attempt)} s ({attempt+1}/{max_retries})")
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
    İsteğin son hali (başarı, 404 ya da hata) işin devre kesicisine bildirilir (sofascore_scraper/breaker.py);
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
    breaker.check(url)
    req = _Request()
    why = _admit(req)
    if why is not None:
        skipped = _skip(url, why, req)
        _request_done(url, req, skipped)
        raise skipped
    try:
        data = await _request_async_body(session, url, max_retries, timeout, trace, req)
    except BaseException as e:
        _request_done(url, req, e)
        raise
    _request_done(url, req, None)
    return data


async def _request_async_body(
    session: AsyncSession,
    url: str,
    max_retries: Optional[int],
    timeout: Optional[float],
    trace: RequestTrace,
    req: _Request,
) -> Optional[JsonResponse]:
    """Kapıdan geçen isteğin denemeleri (_request_async)."""
    runtime_config = _get_runtime_request_config()
    max_retries = max(1, max_retries if max_retries is not None else int(runtime_config["max_retries"]))
    request_timeout = timeout if timeout is not None else int(runtime_config["request_timeout"])
    wait_time_min = float(runtime_config["wait_time_min"])
    wait_time_max = float(runtime_config["wait_time_max"])
    use_proxy, proxy_url = _get_proxy_config()
    full_url = _full_url(url)
    semaphore = _request_semaphore()

    if _browser_first() or req.single:
        # Kapının yoklaması sayfası çözen tarayıcıdan gider: tek istek
        raise_if_cancelled()
        browser_data = None
        seen: Any = None
        try:
            async with semaphore:
                # Semafor beklenirken iş durdurulmuş olabilir (aşağıdaki curl yoluyla aynı kural)
                raise_if_cancelled()
                browser_data, seen = await _browser_async(full_url, req)
        except Exception as e:
            logger.debug(f"Browser bridge error: {e!r}")
        _browser_answered(seen)
        if seen is not None and seen.status == 403 and not seen.sent:
            # Köprü göndermedi: çözüm soğuyor; sağlığa kendisi yazdı. Gönderilmemiş bir durum 0 (tarayıcı siteye
            # ulaşamadı, S1-A4) tarayıcının başarısızlığıdır: aşağıda curl (kapı onu da ağın sayar)
            raise _skip(url, "challenge", req, record=False)
        if browser_data is not None:
            trace.bridge()
            req.challenge_failed = False
        if isinstance(browser_data, dict) and browser_data.get("__404__"):
            raise ResourceNotFoundError(f"Not found: {url}")
        if browser_data is not None:
            try:
                await _asleep(wait_time_min + random.uniform(0, wait_time_max))
            except FetchCancelled:
                # Yanıt elimizde: curl yolundaki gibi döndür, iptal bir sonraki istekte işlensin
                # (FX-18, plan bölüm 15 satır 91)
                pass
            return cast(JsonResponse, browser_data)
        _browser_unanswered(req, seen)
        # Köprü başarısız: aşağıda curl ile normal yoldan dene (yoklama: tek deneme)

    attempts = 1 if req.single else max_retries
    for attempt in range(attempts):
        raise_if_cancelled()
        if attempt and (why := _kept(req)) is not None:
            # Bu arada bir çözüm başarısız oldu: kalan her deneme challenge'a takılırdı
            raise _skip(url, why, req)
        trace.attempt()
        last_attempt = attempt == attempts - 1
        try:
            logger.debug(f"Async API request ({attempt+1}/{max_retries}): {url}")

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
                kept = _kept(req)
                if kept is None:
                    response = await session.get(full_url, **kwargs)
        except CircuitOpenError:
            raise
        except Exception as e:
            req.transport_error = f"{type(e).__name__}: {e}"[:200]
            if "curl: (7)" in str(e) or "Failed to connect" in str(e):
                logger.error(f"Proxy or connection error: {str(e)} (proxy {'on' if use_proxy else 'off'})")
            else:
                logger.error(f"Async request error: {str(e)}")
            if last_attempt:
                raise NetworkError(f"Request failed: {url}: {e}") from e
            await _asleep(_retry_wait(attempt))
            continue

        if kept is not None:
            # Sıra beklenirken geldi (bir işin istekleri birbirinin arkasında bekler): gönderilmedi
            raise _skip(url, kept, req)
        status = response.status_code
        trace.curl(status)
        _reached()
        req.transport_error = None

        if status in (429, 503):
            if last_attempt:
                raise RateLimitError(status_code=status, url=url)
            default_wait = min(60, 5 * (2 ** attempt))
            wait_time = _parse_retry_after_seconds(response.headers.get("Retry-After"), default_wait)
            logger.warning(f"Rate limited or server busy ({status}); waiting {wait_time} s")
            _notify_wait("rate_limit", wait_time)
            await _asleep(wait_time)
            continue

        if status == 403:
            logger.warning(f"403 Forbidden (attempt {attempt+1}/{max_retries}): {url}")
            logger.debug(f"cf-ray: {response.headers.get('cf-ray', 'none')}, cf-mitigated: {response.headers.get('cf-mitigated', 'none')}")
            raise_if_cancelled()
            if _needs_solve(403, response.text) and not (req.single and req.browser_failed):
                _count("challenges")
                logger.info("Turnstile challenge detected; fetching through the browser bridge")
                browser_data = None
                seen = None
                try:
                    browser_data, seen = await _browser_async(full_url, req)
                except Exception as te:
                    logger.debug(f"Browser bridge error: {te!r}")
                _browser_answered(seen)
                if seen is not None and seen.status == 403 and not seen.sent:
                    # Başka bir isteğin çözümü bu istek sırasını beklerken başarısız oldu: gönderilmedi
                    _count("challenge_failures")
                    raise _skip(url, "challenge", req, record=False)
                if browser_data is not None:
                    trace.bridge()
                    req.challenge_failed = False
                if isinstance(browser_data, dict) and browser_data.get("__404__"):
                    raise ResourceNotFoundError(f"Not found: {url}")
                if browser_data is not None:
                    _mark_browser_first()
                    logger.debug("Fetched through the browser bridge")
                    return cast(JsonResponse, browser_data)
                _challenge_unanswered(req, seen)
            elif not _needs_solve(403, response.text):
                req.unsolvable = True
            if last_attempt:
                raise APIError(f"HTTP 403 Forbidden: {url}", status_code=403)
            if _solve_cooldown_left() > 0:
                # Çözüm başarısız oldu: hiçbir yeniden deneme geçmez (siteye ulaşamadıysa ağın, S1-A4)
                raise _skip(url, _kept(req) or "challenge", req)
            _notify_wait("forbidden", min(120, 10 * (2 ** attempt)))
            await _asleep(min(120, 10 * (2 ** attempt)))
            continue

        if status == 404:
            logger.debug(f"Not found (404): {url}")
            raise ResourceNotFoundError(f"Not found: {url}")

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
                f"JSON parse error: {str(e)} | Status: {status} | Content: {response_text[:500]}"
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
        logger.debug("Session warm-up started")
        kwargs: Dict[str, Any] = {"timeout": 15}
        use_proxy, proxy_url = _get_proxy_config()
        if use_proxy and proxy_url:
            kwargs["proxy"] = proxy_url  # warm-up da gerçek IP'yi göstermesin
        notify_request(await throttle.wait_async())  # warm-up da SofaScore'a giden bir istek: ortak bütçeden
        resp = await session.get(warmup_url, **kwargs)
        logger.debug(
            f"Warm-up done: status={resp.status_code}, "
            f"cookies={len(session.cookies) if hasattr(session, 'cookies') else '?'}"
        )
        # Cloudflare challenge geçişi için kısa bekleme
        await asyncio.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        logger.debug(f"Warm-up failed (continuing): {e}")


class WarmableAsyncSession:
    """
    Warm-up destekli AsyncSession context manager.
    İlk kullanımda ana sayfaya gidip Cloudflare cookie'lerini toplar.
    """

    def __init__(self) -> None:
        runtime_config = _get_runtime_request_config()
        profile = random.choice(IMPERSONATE_PROFILES)
        logger.debug(f"Async session created, impersonate profile: {profile}")
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
