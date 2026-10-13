"""
Sofascore Turnstile challenge çözücü ve tarayıcı köprüsü (BrowserBridge).

Sofascore API'si curl isteklerini (TLS/JA4 parmak izi) 403 + challenge ile reddeder;
istekler gerçek bir tarayıcının içinden fetch() ile yapılmalıdır.

Tarayıcıyı Scrapling'in StealthySession'ı açar (patchright + gizlilik ayarları): her
ortamda headless çalışır — masaüstünde ve ekranı olmayan sunucuda aynı yol. Challenge,
sofascore.com/captcha.html sayfasındaki gömülü Turnstile'ı Scrapling'in solve_cloudflare
özelliğiyle geçerek çözülür; sonuç sofa_captcha cookie'si ve JWT token'dır. Standart
Playwright ile headless modda Turnstile hiç geçilemiyordu (ölçüm: 0/12 istek).

Modül P24 ile sofascore_scraper/challenge_solver.py'den buraya taşındı; eski ad FX-32 ile kalktı (araştırma betikleri de
bu modülü içe aktarır).
API kökü sofascore_scraper/client/transport.py'deki ayardan (`api_url`) gelir; köprü kendi kökünü tutmaz.

İptal (docs/design/02-services.md 2.4; plan bölüm 15, satır 61 ve 92): köprünün sonucunu bekleyen çağıran kendi
iptal kontrolüne de bakar, sync yolda (_run_sync, P24) ve her indirmenin geçtiği async yolda
(_run_on_background_loop, FX-18). Ortak challenge çözümünü ya da sayfanın fetch()'ini bekleyen bir çağıran iş
durdurulunca beklemeyi bırakır; ortak çözüm (asyncio.shield) diğer bekleyenler için sürer. Köprüden çıkan bir
istek, bütçeden sıra ayırmadan önce de iptale bakar (_wait_for_slot): durdurmadan sonra hiçbir istek gönderilmez.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import collections
import concurrent.futures
import contextlib
import contextvars
import hashlib
import json
import math
import re
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from sofascore_scraper import bridge_health, throttle
from sofascore_scraper.client import profile_lock
from sofascore_scraper.client.procinfo import browser_processes, memory_mib
from sofascore_scraper.client.context import FetchCancelled, notify_request, raise_if_cancelled
from sofascore_scraper.private_files import make_private_dir
from sofascore_scraper.logger import get_logger
from sofascore_scraper.paths import browser_profile_dir

logger = get_logger("ChallengeSolver")

# Token önbelleği
_cached_token: Optional[str] = None
_cached_at: float = 0
_TOKEN_TTL_SECONDS = 3500

DEFAULT_PROFILE_DIR = browser_profile_dir()
SITE_ORIGIN = "https://www.sofascore.com"
HOME_URL = "https://www.sofascore.com/tr"
CAPTCHA_URL = "https://www.sofascore.com/captcha.html?redirectUrl=https%3A%2F%2Fwww.sofascore.com%2Ftr"
# Çözümün API'yi gerçekten açtığını doğrulamak için küçük bir uç nokta (API köküne göre yol). _PROBE_URL
# verilirse o tam adres kullanılır (tests/test_bridge_offline_browser.py yerel sunucuya çevirir).
_PROBE_PATH = "/unique-tournament/17/seasons"
_PROBE_URL: Optional[str] = None

# Zaman aşımları (saniye). Tarayıcı başlatma (ilk challenge çözümü dahil), istek başına
# zaman aşımının dışında tutulur: soğuk bir başlatmayı yarıda kesmek profili kilitli bırakır.
# Ortak istek bütçesinde sıra beklenen süre de bu zaman aşımlarından sayılmaz (bkz. _SlotWait).
STARTUP_TIMEOUT = 150.0
REQUEST_TIMEOUT = 120.0
SOLVE_TIMEOUT = 90.0
# Çıkışta bir köprünün kapanmasına tanınan süre (_cleanup)
CLOSE_TIMEOUT = 10.0
_JS_FETCH_TIMEOUT_MS = 20000
# Başlatma başarısız olduysa her 403'te yeniden denenmez
_LAUNCH_RETRY_AFTER = 300.0
# Yeni çözülmüş bir token, hemen ardından gelen 403 dalgası için yeniden kullanılır
_FRESH_TOKEN_SECONDS = 30.0
# captcha.html'nin Scrapling'in çözücüsünden sonra siteye (yönlendirmesi: /tr) geçmesi için tanınan süre. c'nin
# 5. koşusunda geçen çözümler yüklemeden 3,4-4,5 sn sonra yönlendi.
REDIRECT_WAIT_S = 15.0
_POLL_S = 0.5
# captcha.html'nin bir saatte en çok yüklenme sayısı: c'ye taşınan kodun 403'teki çözümlerinin bir saatte yaptığı
# (beklemeleri 180, 180, 360, sonra 600 sn: 0, 180, 360, 720, 1320, 1920, 2520 ve 3120. sn'lerde yüklemeler). Hiçbir
# yol (403'ün çözümü, açılış) kayan bir saatte bundan fazla yüklemez (BrowserBridge._load_wait).
PORT_LOADS_PER_HOUR = 8
# Bir sayfa isteğinin beklediği çözümün bütçesi (75 sn): captcha.html yüklemeleri ve yoklamaları birlikte; isteğin ilk
# denemesi, çözüm ve yinelemesi REQUEST_TIMEOUT (120 sn) içinde kalır.
_PROBE_S = _JS_FETCH_TIMEOUT_MS / 1000
_HOME_S = 10.0  # geçen bir çözümden sonra ana sayfanın yüklemesine kalan (sayfa belgesi gelince kullanılabilir)
SOLVE_BUDGET_S = REQUEST_TIMEOUT - 2 * _PROBE_S - 5.0
# Açılışın çözümü (120 sn): STARTUP_TIMEOUT (150 sn) eksi Chromium'un açılışı
LAUNCH_SOLVE_BUDGET_S = STARTUP_TIMEOUT - 30.0
# Bir captcha.html yüklemesi ancak çözümün bütçesinden bu kadarı kaldıysa başlar (çözücü ve yönlendirme beklemesi);
# her yükleme kalanı alır, en çok SOLVE_TIMEOUT
MIN_LOAD_S = 20.0
# Token, JWT `exp`'inden bu kadar önce yeniden çözülmeye başlanır (BrowserBridge._refresh_due; S1-A6): arka planda,
# deneme başına tek captcha.html yüklemesi, başarısız bir denemenin ardından sonraki başarısız çözümlerin beklemeleri
# kadar arayla (180, 180, 360 sn: _load_wait), token başına en çok REFRESH_MAX_LOADS, token'ın REFRESH_MIN_LEFT_S'den
# fazlası kalmışken. Token bir saat yaşar ve süresi dolunca API `jwt:time-out-of-bounds` 403'ü verir (c'nin 4. koşusu:
# çözümden 3599,4 sn sonra). c'nin soak'ında (2026-10-02) yenileme, o zaman exp'ten 120 sn önce tek yükleme, 3 kez
# başarısız oldu; bir kez exp'ten sonra art arda beş yükleme başarısız oldu ve 24 dakika hiçbir şey okunmadı.
REFRESH_BEFORE_EXP_S = 900.0
# Token'ın bu kadarı kaldıysa yenileme yok: API'nin jwt 403'ü her çözüm gibi çözer. En az _SOLVE_RETRY_AFTER: exp'ten
# sonraki 403'ün çözümü son denemeden bir bekleme sonrasından önce gelmez (_load_not_before)
REFRESH_MIN_LEFT_S = 180.0
# Bir token'ın ömründe en çok yenileme yüklemesi: başarısız çözümler gibi aralanınca (180, 180, 360 sn) üçü
# REFRESH_BEFORE_EXP_S ile REFRESH_MIN_LEFT_S arasındaki 720 sn'ye sığar
REFRESH_MAX_LOADS = 3
# Tarayıcının geri dönüşümü (S1-A3, BrowserBridge._recycle): yalnızca token'ın bu kadarı kalmışken (yeniden açılış
# hiç çözmez, ondan sonraki okumalar çözüme ihtiyaç duymamalı)
RECYCLE_TOKEN_LEFT_S = 600.0
# Bir sayfa isteği süren bir geri dönüşümü en çok bu kadar bekler; sonra başarısız olur (curl okumayı alır ya da bir
# sonraki denemesi yeni tarayıcıyı)
RECYCLE_HOLD_S = 30.0
# Bellek için ancak tarayıcı bu kadar çalıştıktan sonra: hemen sınırın üstünde açılan bir tarayıcı (sürecin kendi
# belleği ya da çok düşük bir sınır) her denetimde yeniden açılmasın
RECYCLE_MIN_AGE_S = 1800.0
# Bellek ve yaş bu aralıkla denetlenir (/proc dakikada bir okunur, her istekte değil)
RECYCLE_CHECK_S = 60.0
# Bundan genç bir token yenilenmez (_refresh_due): çözülür çözülmez REFRESH_BEFORE_EXP_S ya da daha azı kalan bir token
# (SofaScore kısa kesti ya da sunucunun saati bir saate kadar ileri) yeniden ve yeniden yenilenirdi: c'nin bir
# incelemesinin benzetiminde 13-22 sn'de bir çözüm
REFRESH_MIN_AGE_S = 600.0
# Başarısız bir çözümden sonra bu süre yeniden denenmez (her istek 10-90 sn yakmasın); üçüncü art arda başarısız
# çözümden sonra iki katına çıkar, en çok _SOLVE_RETRY_AFTER_MAX (solve_retry_after)
_SOLVE_RETRY_AFTER = 180.0
_SOLVE_RETRY_AFTER_MAX = 600.0


def solve_retry_after(failures: int) -> float:
    """
    Art arda `failures`'ıncı başarısız çözümden sonraki bekleme: birinci ve ikinciden sonra 180 sn, sonra iki katı, en
    çok 600 sn (180, 180, 360, 600, 600, ...). "Art arda": aralarında yanıt yok; her 200 ya da 404 (curl'ün ya da
    sayfanın) seriyi bitirir (BrowserBridge.reset_solve_streak), geçen bir çözüm gibi.

    c projesinin üretim benzeri 3. koşusu (2026-10-02) üç çözüm istedi, üçüncüsü fırtınanın 7,2. dakikasında, iki
    180 sn'lik beklemeden sonra; ilk başarısızlıktan itibaren ikiye katlamak 3 dakika daha hiçbir şey okutmazdı. İki
    çözümü aşan bir duvar geçici değil, adresin engellenmesine benzer: orada her çözüm captcha sayfasını iki kez ve
    iki yoklamayı harcar ve Cloudflare her 3 dakikada challenge'ını geçemeyen bir istemci görür; üst sınır kalkan bir
    engeli 10 dakika içinde bulur (önce tarayıcı penceresi, transport.BROWSER_FIRST_SECONDS): bir engelin ilk
    saatinde 17-20 yerine 8 çözüm.
    """
    return min(_SOLVE_RETRY_AFTER_MAX, _SOLVE_RETRY_AFTER * 2 ** max(0, failures - 2))

# Tarayıcının HTTP önbelleği: durum taşıyan yanıtlar (maç, canlı liste, sezon listeleri) max-age boyunca
# (sezon listelerinde 60 sn) önbellekten dönerse biten maç o süre "devam ediyor" görünür (issue #6).
# Yalnızca maçın durumundan bağımsız statik veri önbelleği kullanır.
_STATIC_PATH_RE = re.compile(r"/(seasons|rounds)/?$")


def token_left(token: Optional[str], now: float) -> Optional[float]:
    """JWT'nin kalan saniyesi (`exp` - `now`), `exp` yoksa None: her çözümde loglanır, token'ın kendisi asla."""
    exp = token_exp(token)
    return None if exp is None else exp - now


_UNSENT_CHALLENGE = '{"error": {"code": 403, "reason": "challenge"}}'
"""
Çerez yokken ve hiçbir çözüm geçmezken köprünün isteği göndermeden verdiği yanıt: sayfanın karşılaşacağı challenge
(istek katmanı onu bir challenge olarak alır).
"""


# Ağın yanıtladığını gösteren adresler ve tarayıcının kendi ağ hatası (S1-A4)
_CHALLENGES_ORIGIN = "https://challenges.cloudflare.com/"
_NAVIGATION_ERROR_RE = re.compile(r"net::ERR_(?!ABORTED)|NS_ERROR_")
_NETWORK_CHECK_S = 5.0
# Yüklemesi siteye ulaşmayan bir çözümden sonraki bekleme (`solve_unreached`: ağ yok): o yükleme ne SofaScore'a ne
# Cloudflare'e ulaştı; kısa bir bekleme okumaları ağ dönünce hemen geri getirir (başarısız çözüm serisinde değildir)
UNREACHED_COOLDOWN_S = 30.0


def _reached(response: Any, page: Any) -> bool:
    """
    Sitenin yanıtladığını gösteren bir yanıt: ana çerçevenin captcha.html gezinmesine ya da Cloudflare'in
    challenge'larından; bir service worker'ın verdiği asla (önbellekteki bir sayfa hiçbir şeye ulaşmaz).
    """
    if getattr(response, "from_service_worker", False) is True:
        return False
    url = str(getattr(response, "url", "") or "")
    if url.startswith(_CHALLENGES_ORIGIN):
        return True
    if not url.startswith(SITE_ORIGIN + "/captcha.html"):
        return False
    try:
        # patchright'ın Request.frame'i isteğin henüz çerçevesi ya da sayfası yokken hata verir; bir dinleyicinin
        # hatası Playwright'ın sonraki protokol çağrısını bozardı
        frame = getattr(getattr(response, "request", None), "frame", None)
        main = getattr(page, "main_frame", None)
    except Exception:  # dinleyici Playwright'a asla hata fırlatmaz
        return False
    main_frame = frame is None or main is None or frame == main
    if main_frame:
        _log_captcha_cache(response)
    return main_frame


_cache_logged = False


def _log_captcha_cache(response: Any) -> None:
    """
    Süreçte bir kez, INFO: captcha.html'nin ana çerçeve yanıtının taşıdığı `Cache-Control` (kendi isteği yok); canlı
    bir koşu önbellekteki bir sayfanın ulaşılmış bir site sayılıp sayılamayacağını söyler.
    """
    global _cache_logged
    if _cache_logged:
        return
    _cache_logged = True
    try:
        headers = getattr(response, "headers", None) or {}
        value = headers.get("cache-control") if isinstance(headers, dict) else None
    except Exception:  # dinleyici Playwright'a asla hata fırlatmaz
        value = None
    logger.info("captcha.html answered with Cache-Control: %s", value or "(none)")


async def _from_network(response: Any) -> bool:
    """
    Bu yanıt ağdan mı geldi, disk önbelleğinden değil? captcha.html önbelleğe alınabilir (`Cache-Control: public,
    max-age=3600`, c'nin soak'ı): kesintide önbellek onu verebilir. Ağdan geçen, isteğin yanıt başlıkları ve gövdesidir
    (`Request.sizes()`, Chromium'un kodlanmış veri uzunluğu): önbelleğin verdiği sayfa için 0 (patchright 1.63'ün
    Chromium'uyla yerel sunucuya karşı ölçüldü), sunucunun gönderdiği için (304 dahil) fazlası.
    """
    try:
        sizes = await asyncio.wait_for(response.request.sizes(), _NETWORK_CHECK_S)
        body = sizes.get("responseBodySize") or 0
        headers = sizes.get("responseHeadersSize") or 0
    except Exception:  # bilinmiyor: ağın taşıdığının kanıtı değil
        return False
    return body + headers > 0


def _network_failure(request: Any) -> Optional[str]:
    """
    Sitenin ya da Cloudflare challenge'larının, tarayıcının kendi ağ hatasıyla (`requestfailed`, `net::ERR_*` ama
    `ERR_ABORTED` değil) düşen bir isteği; değilse None. Asla hata fırlatmaz (dinleyici).
    """
    try:
        url = str(getattr(request, "url", "") or "")
        failure = getattr(request, "failure", None)
        if callable(failure):
            failure = None  # eski bir API'nin metodu: dinleyicide beklenmez
        text = str(failure or "")
    except Exception:  # dinleyici Playwright'a asla hata fırlatmaz
        return None
    if not (_on_origin(url) or url.startswith(_CHALLENGES_ORIGIN)):
        return None
    return text if _NAVIGATION_ERROR_RE.search(text) else None


def _navigation_error(e: BaseException) -> bool:
    """Tarayıcının kendi ağ hatası (Chromium'un `net::ERR_*`'ı: ad yok, bağlantı yok, proxy düştü), yüklenen bir
    sayfanınki değil (`ERR_ABORTED`: yarıda kesilen gezinme)."""
    return _NAVIGATION_ERROR_RE.search(str(e)) is not None


def recycle_reason(mem_mib: Optional[float], age_s: float, *, limit_mib: float, every_s: float) -> Optional[str]:
    """
    Tarayıcı şimdi neden geri dönüştürülmeli, ya da None: belleği `limit_mib` ya da üstünde (0: bellek için asla) ve
    RECYCLE_MIN_AGE_S çalıştı, ya da `every_s` çalıştı (0: yaş için asla).
    """
    over = limit_mib > 0 and mem_mib is not None and mem_mib >= limit_mib
    if over and age_s >= RECYCLE_MIN_AGE_S:
        return f"memory {mem_mib:.0f} MiB, the limit {limit_mib:.0f} MiB"
    if every_s > 0 and age_s >= every_s:
        return f"running {age_s / 3600:.1f} h, recycled every {every_s / 3600:g} h"
    return None


def _recycle_settings() -> Tuple[float, float]:
    """(`client.browser_recycle_mib`, `client.browser_recycle_hours`); ayarlar kurulamıyorsa varsayılanlar."""
    from sofascore_scraper.config import loader

    try:
        client = loader.active_settings().client
        return float(client.browser_recycle_mib), float(client.browser_recycle_hours)
    except Exception:
        return 2048.0, 6.0


def _page_url(page: Any) -> Optional[str]:
    """Sayfanın adresi; sayfa yoksa ya da adresi okunamıyorsa (testlerin sahte sayfaları) None."""
    url = getattr(page, "url", None) if page is not None else None
    return url if isinstance(url, str) else None


def _on_origin(url: str) -> bool:
    """Sitenin bir sayfası, captcha.html dahil: `fetch()`'i sitenin olan bir sayfa."""
    return url.startswith(SITE_ORIGIN + "/")


def _on_site(url: str) -> bool:
    """captcha.html dışında sitenin bir sayfası: geçen bir captcha.html'nin gittiği yer."""
    return url.startswith(SITE_ORIGIN + "/") and not url.startswith(SITE_ORIGIN + "/captcha.html")


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


def token_exp(token: Optional[str]) -> Optional[float]:
    """JWT'nin `exp`'i (UNIX sn); okunabilir, `exp`'li bir JWT değilse None."""
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:  # okunamayanın okunacak bir süresi de yoktur
        return None
    return float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None


def _is_token_valid() -> bool:
    """Önbellekteki token hâlâ geçerli mi (TTL'imiz ve JWT'nin kendi `exp`'i, bir dakika önceden)?"""
    if not _cached_token:
        return False
    if time.time() - _cached_at > _TOKEN_TTL_SECONDS:
        return False
    return (token_exp(_cached_token) or 0) > time.time() + 60


# SofaScore'un captcha token'ını reddettiği 403: token'ın saati dolunca `{"error": {"code": 403, "reason":
# "jwt:time-out-of-bounds"}}` (c'nin üretim benzeri 4. koşusu, 2026-10-02) ve başka her `jwt:` nedeni (bozuk imza,
# bozuk token)
_JWT_REFUSED_RE = re.compile(r'"reason"\s*:\s*"jwt:')


def token_refused(text: str) -> bool:
    """Bu 403 gövdesi SofaScore'un captcha token'ını reddetmesi mi (`jwt:*`)?"""
    return _JWT_REFUSED_RE.search(text or "") is not None


def needs_solve(status: Any, text: str) -> bool:
    """
    Bir Turnstile çözümünün yanıtlayacağı 403: challenge ya da token'ın reddi (`token_refused`). Başka bir 403 captcha'nın
    değildir: hiçbir çözüm onu açmaz. c'nin 4. koşusu: token'ın saati dolunca API her sayfa isteğine `jwt:` 403'ü verdi;
    bu "challenge" demediği için köprü bir daha çözmedi ve koşu durana kadar hiçbir şey okunmadı.
    """
    return status == 403 and ("challenge" in (text or "") or token_refused(text))


def get_cached_token() -> Optional[str]:
    """Geçerli bir önbellek tokeni varsa döndürür."""
    if _is_token_valid():
        return _cached_token
    return None


def _api_url(path: str) -> str:
    """API köküne göre yol → tam adres (API_BASE_URL ayarı; sofascore_scraper/client/transport.py)."""
    # İşlev içinde: transport curl_cffi'yi yükler; köprü modülü yalnız yüklenince ona gerek yok
    from sofascore_scraper.client.transport import api_url

    return api_url(path)


def _client_settings() -> Any:
    from sofascore_scraper.config import loader

    return loader.active_settings().client


def _headless() -> bool:
    # Her ortamda aynı yol: headless. Hata ayıklamak için pencereyi görmek isterseniz `client.browser_headed` açılır.
    return not _client_settings().browser_headed


def _sandbox_setting() -> bool:
    """`client.browser_sandbox` (c projesinin K18'i): Chromium kendi sandbox'ıyla başlar; varsayılan kapalı."""
    return bool(getattr(_client_settings(), "browser_sandbox", False))


class _SlotWait:
    """
    Bir köprü çağrısının ortak istek bütçesinde (sofascore_scraper/throttle.py) sıra beklediği toplam süre.

    Bekleme köprünün içinde (arka plan döngüsünde) yapılır, ama çağrının zaman aşımından
    sayılmaz: düşük bir REQUEST_RATE_LIMIT ya da kalabalık bir kuyruk, sırası gelmemiş isteği
    REQUEST_TIMEOUT ile düşürmesin. Süre, beklemeye başlamadan önce (ayırma anında) yazılır;
    çağıran taraf son tarihi buna göre uzatır (_run_sync / _run_on_background_loop).
    `joined`: bu çağrının beklediği ortak challenge çözümünün kendi sıra beklemeleri.
    `shared`: sayaç ortak challenge çözümünündür; o çözümün sıra beklemesi tek bir çağıranın iptaliyle
    kesilmez (bkz. _wait_for_slot).
    """

    __slots__ = ("own", "joined", "shared")

    def __init__(self, shared: bool = False) -> None:
        self.own = 0.0
        self.joined: List["_SlotWait"] = []
        self.shared = shared

    @property
    def seconds(self) -> float:
        return self.own + sum(w.seconds for w in self.joined)


_slot_wait: "contextvars.ContextVar[Optional[_SlotWait]]" = contextvars.ContextVar("bridge_slot_wait", default=None)


@dataclass
class BrowserOutcome:
    """
    Köprünün bir sayfa isteğini nasıl bitirdiği (istek katmanı okur; `observe`). Köprünün sonucu veri ya da None'dır;
    None'ın nedenini (çözülemeyen 403, gönderilmemiş istek, hiç yanıt yok) istek katmanı buradan öğrenir.

    status  son durum kodu; None: bilinen bir yanıt yok (köprü hata verdi ya da hiç çağrılmadı)
    text    son yanıtın gövdesi (başarısızlıkta; 200'de boş)
    sent    False: istek gönderilmeden yanıtlandı (çözüm beklemesi, istek katmanının kapısı, cookie yok)
    hold    istek katmanının kapısı: bu sayfa isteğinin neden gönderilmeyeceği ya da None (sıra geldiğinde sorulur)
    """

    status: Optional[int] = None
    text: str = ""
    sent: bool = True
    hold: Optional[Callable[[], Optional[str]]] = None


_outcome: "contextvars.ContextVar[Optional[BrowserOutcome]]" = contextvars.ContextVar("bridge_outcome", default=None)
# Bir sayfa isteğinin kendisi gönderiliyor (çözümün yoklaması değil): sıra geldiğinde kapıya bakılır (_held_back)
_page_fetching: "contextvars.ContextVar[bool]" = contextvars.ContextVar("bridge_page_fetching", default=False)


@contextlib.contextmanager
def observe(hold: Optional[Callable[[], Optional[str]]] = None) -> Iterator[BrowserOutcome]:
    """
    Blok içindeki köprü çağrısının (fetch_api_via_browser ve _sync) sonucu: dönen BrowserOutcome'a yazılır. Çağrı arka
    plan döngüsüne çağıranın bağlamıyla gider (_submit), sonuç aynı nesneye yazılır. `hold`: istek katmanının kapısı
    (S1-B4), sıra geldiğinde sorulur.
    """
    out = BrowserOutcome(hold=hold)
    token = _outcome.set(out)
    try:
        yield out
    finally:
        _outcome.reset(token)


# Sıra beklerken çağıranın iptal kontrolüne bu aralıkla bakılır (istek katmanındaki _sleep/_asleep ile aynı)
_CANCEL_CHECK_SECONDS = 0.25


async def _cancellable_sleep(seconds: float) -> None:
    """
    asyncio.sleep gibi, ama çağıranın iptal kontrolü (sofascore_scraper/client/context.py) iptal derse
    FetchCancelled ile kesilir. Kontrol bir ContextVar'dır: köprü görevi, çağıranın bağlamını
    kopyaladığı için (_submit) arka plan döngüsünde de çağıranın işine bakar.
    """
    end = time.monotonic() + seconds
    while True:
        raise_if_cancelled()
        left = end - time.monotonic()
        if left <= 0:
            return
        await asyncio.sleep(min(_CANCEL_CHECK_SECONDS, left))


async def _wait_for_slot() -> None:
    """
    Ortak bütçeden sıra alır ve bekler; beklenen süre çağrının zaman aşımına eklenir.

    İş iptal edildiyse sıra hiç ayrılmaz (FX-18, plan bölüm 15 satır 92): istek semaforunu durdurmadan önce
    almış, köprünün içinde (ensure_ready, ortak çözüm) bekleyen bir istek durdurmadan sonra gönderilmez.
    Ayrılmış ve zamanı gelmiş bir sıra ise geri verilmez, istek gider (FX-6'nın kuralı; kontrol bu yüzden
    ayırmadan önce yapılır).

    Bekleme, çağıranın işi iptal edilince en geç _CANCEL_CHECK_SECONDS içinde kesilir (sync yolda
    çağıran thread köprünün sonucunu beklerken kendi iptaline bakamaz). Bekleme nasıl kesilirse
    kesilsin (iş iptali, zaman aşımının görevi iptal etmesi) istek gönderilmemiştir: sıra bütçeye
    geri verilir. Ortak challenge çözümünün doğrulama isteği iptale bakmaz: çözümü başlatan işin
    iptali, aynı çözümü bekleyen başka çağıranları düşürmesin.
    """
    wait = _slot_wait.get()
    shared = wait is not None and wait.shared
    if not shared:
        raise_if_cancelled()
    delay = throttle.reserve()
    waited = 0.0
    if delay > 0:
        if wait is not None:
            wait.own += delay
        with throttle.give_back_if_interrupted(delay):
            sleep = asyncio.sleep if shared else _cancellable_sleep
            await sleep(delay)
            # Etkileşimli bir istek önüne geçtiyse (FX-23, F16) kaydırılan sırayı bekler
            extra = await throttle.settle_async(delay, sleep)
            if extra and wait is not None:
                wait.own += extra
            waited = float(delay) + (extra or 0.0)
    # Sırası gelen istek çağıranın işine bildirilir (işin istek sayacı, B2): görev çağıranın bağlamını taşır
    notify_request(waited)


def _session_class() -> Any:
    """
    Scrapling'in tarayıcı oturumu sınıfı. Scrapling içe aktarılırken "scrapling" logger'ına kendi konsol handler'ını
    kurar; satırlar hem onunla hem kök logger'la iki kez yazılıyordu. Logger uygulamanın handler'larına bağlanır:
    her satır bir kez, uygulamanın biçiminde (FX-23, bulgu F3).
    """
    from scrapling.fetchers import AsyncStealthySession

    from sofascore_scraper.logger import adopt_library_logger

    adopt_library_logger("scrapling")
    return AsyncStealthySession


# Kurulan her köprü (zayıf başvuru): çıkış kancası hepsinin tarayıcısını kapatır (_cleanup); öksüz bir Chrome
# profili kilitli tutar
_bridges: "weakref.WeakSet[BrowserBridge]" = weakref.WeakSet()


class BrowserBridge:
    """
    Scrapling StealthySession üzerinden Sofascore API köprüsü.
    Tek bir sayfayı açık tutar, API isteklerini o sayfadan fetch() ile yapar ve 403
    challenge geldiğinde tek bir çözümü tüm bekleyen isteklerle paylaşır.
    """

    _instance: Optional["BrowserBridge"] = None

    # Sınıf düzeyindeki varsayılanlar: `__new__` ile kurulan (testlerin) köprüleri de her alanı bulur
    home_url: Optional[str] = None
    report_health = True
    sandbox: Optional[bool] = None
    session: Any = None
    context: Any = None
    page: Any = None
    # Turnstile çözümleri (açılışta ve 403'te), durum için (c'nin A1'i): toplam, başarısız ve art arda başarısız
    solves = 0
    solve_failures = 0
    consecutive_solve_failures = 0
    # Zaman (epoch sn): testler kendi saatini verir (`clock`)
    _clock: Callable[[], float] = staticmethod(lambda: time.time())
    _launch_failed_at = 0.0
    _solve_failed_at = 0.0
    # Son başarısız çözümün beklemesi (solve_retry_after)
    _solve_cooldown_s = _SOLVE_RETRY_AFTER
    # captcha.html'nin her yüklemesinin uyduğu tek saat (_load_wait): başarısız bir yüklemeden sonra bundan önce yok
    # (onun beklemesi); saat monotonic'tir, testler kendi saatini verir
    _mono: Callable[[], float] = staticmethod(lambda: time.monotonic())
    _load_not_before = -math.inf
    # Bir açılış (onun çözümü) sürüyor: istekler onu bekler, yanında ikinci bir çözüm başlamaz (c'nin B8'i)
    _launching = False
    # API'nin kabul ettiği son token (_api_unlocked; ya da yoklanacak sayfa yoktu)
    _checked_token: Optional[str] = None
    # Son çözümün yüklemeleri siteye hiç ulaşmadı (ağ yok; S1-A4)
    solve_unreached = False
    # Yenilemenin denemeleri (S1-A6, _refresh_due): hangi token için, kaç yükleme, kaçı başarısız; bir açılışın ilk
    # _SOLVE_RETRY_AFTER'ında yenileme yok (bir çökme döngüsü her açılışta captcha.html yüklemesin)
    _refresh_token: Optional[str] = None
    _refresh_loads = 0
    _refresh_failures = 0
    _refresh_not_before = -math.inf
    _launched_at: Optional[float] = None
    # Geri dönüşüm (S1-A3): süren sayfa istekleri, süren bir geri dönüşümün kapısı (yeni istekler onu bekler), sayısı
    _inflight = 0
    _recycling: Optional[asyncio.Event] = None
    recycles = 0
    _recycle_task: Optional["asyncio.Future[Any]"] = None
    _recycle_checked_at = -math.inf
    _relaunch_solves = True  # açılış çerez yoksa çözer; geri dönüşümün yeniden açılışı asla
    _launch_dir: Optional[str] = None
    token: Optional[str] = None
    _token_at = 0.0
    _solve_task: Optional["asyncio.Future[Optional[str]]"] = None
    _solve_wait: Optional[_SlotWait] = None

    def __init__(self, profile_dir: str = DEFAULT_PROFILE_DIR, home_url: Optional[str] = None, *,
                 report_health: bool = True, sandbox: Optional[bool] = None,
                 clock: Callable[[], float] = time.time):
        """
        home_url       köprü sekmesinin açıldığı ve sayfa SofaScore dışına çıkınca döndüğü adres; None: HOME_URL.
                       Canlı sayfaların ayrı köprüsü (sofascore_scraper/services/live/push_source.py) API çağırmayan bir sayfa verir.
        report_health  başlatma hataları ve isteklerin sonucu süreç genelindeki köprü sağlığına (sofascore_scraper/bridge_health.py)
                       yazılır. Canlı kaynakların köprüleri False verir: onların tarayıcısı başlamazsa izleme sürecinin
                       köprü sağlığı "bozuk" görünmesin, indirmelerin köprüsünün durumu o değildir (FX-15).
        sandbox        Chromium kendi sandbox'ıyla başlar (patchright'ın `chromium_sandbox`'ı, Scrapling'in
                       `additional_args`'ıyla); None: `client.browser_sandbox` ayarı, açılışta okunur. Varsayılan
                       kapalıdır (patchright'ın `--no-sandbox`'ı, bugünkü gibi); sunucunun buna izin vermesi gerekir.
        clock          beklemelerin saati (epoch sn); testler sahtesini verir.
        """
        self._clock = clock
        self._mono = time.monotonic if clock is time.time else clock
        self.profile_dir = profile_dir
        self.home_url = home_url
        self.report_health = report_health
        self.sandbox = sandbox
        self.session: Any = None
        self.context: Any = None
        self.page: Any = None
        self.token: Optional[str] = None
        self._token_at: float = 0.0
        self._init_lock = asyncio.Lock()
        self._solve_task: Optional["asyncio.Future[Optional[str]]"] = None
        self._solve_wait: Optional[_SlotWait] = None
        self._launch_failed_at: float = 0.0
        self._solve_failed_at: float = 0.0
        # Profil başka bir süreçteyse (ör. `ssc serve`) bu sürecin geçici profili; köprü kapanınca silinir
        self._launch_dir: Optional[str] = None
        # Profil SofaScore cookie'lerini (çözülmüş challenge) taşır: yalnızca sahibine açık (0700)
        make_private_dir(self.profile_dir)
        # Çıkışta kapatılacaklar (_cleanup): süreçteki her köprü, canlı kaynaklarınki de
        _bridges.add(self)

    @classmethod
    def get_instance(cls, profile_dir: str = DEFAULT_PROFILE_DIR) -> "BrowserBridge":
        if cls._instance is None:
            cls._instance = cls(profile_dir=profile_dir)
        return cls._instance

    def _home(self) -> str:
        return getattr(self, "home_url", None) or HOME_URL

    async def ensure_ready(self) -> None:
        """
        Tarayıcı oturumunun hazır ve açık olduğundan emin olur. Bir açılış (ve çözümü) sürerken onu bekler: açılışın
        çözümünün yanında bir isteğin çözümü başlamaz (c'nin soak'ında, 2026-10-02: aynı anda iki captcha.html
        yüklemesi, iki token, sayfanın token'ı ve cookie'si ayrı, 35 sn sonra üçüncü bir çözüm).
        """
        if self.page and not self.page.is_closed() and not self._launching:
            return
        # Bir geri dönüşüm sürerken onu en çok RECYCLE_HOLD_S bekler, yeniden açılışın tuttuğu kilidi değil (c'nin
        # ef782b4 incelemesi: bir istek bütün yeniden açılışı bekliyordu, STARTUP_TIMEOUT'a kadar)
        await self._hold_for_recycle()

        async with self._init_lock:
            if self.page and not self.page.is_closed() and not self._launching:
                return
            if self._launch_failed_at and self._clock() - self._launch_failed_at < _LAUNCH_RETRY_AFTER:
                if self.report_health:
                    bridge_health.record_failure(bridge_health.KIND_BROWSER,
                                                 "the browser could not start (waiting before the next try)")
                raise RuntimeError(
                    "The browser bridge could not start (tried recently). "
                    "Run `ssc doctor` to see why "
                    "(browser install: `python -m patchright install chromium --no-shell`)"
                )
            await self._relaunch()

    async def _relaunch(self, *, solve: bool = True) -> None:
        """
        Kalan oturumu kapatıp yenisini açar; `_init_lock` tutulur. `solve`: çerezi olmayan bir açılış (bekleme dışında)
        çözer; geri dönüşümün yeniden açılışı çözmez.
        """
        # Sayfası kapanmış eski oturum: yeniden başlatmadan önce kapat (profil kilidi)
        await self.close()
        self._launching = True
        self._relaunch_solves = solve
        try:
            await self._launch_in_free_profile()
        except BaseException as e:
            # Yarım kalan başlatma (hata veya iptal) tarayıcı sürecini ve profil kilidini bırakmasın. Yalnızca hata
            # sonraki açılışı geciktirir (_LAUNCH_RETRY_AFTER): iptal (çağıranın zaman aşımı, durdurma) tarayıcı
            # hakkında bir şey söylemez (c'nin B8'i)
            if not isinstance(e, asyncio.CancelledError):
                self._launch_failed_at = self._clock()
            await self.close()
            if isinstance(e, Exception) and self.report_health:  # iptal bir sağlık sinyali değil
                bridge_health.record_failure(bridge_health.KIND_BROWSER, f"{e.__class__.__name__}: {e}")
            raise
        finally:
            self._launching = False
            self._relaunch_solves = True
        self._launch_failed_at = 0.0

    def _idle_event(self) -> asyncio.Event:
        """Süren sayfa isteği yokken kurulu olay (geri dönüşüm bekler); `__new__` ile kurulan köprüde de vardır."""
        idle = self.__dict__.get("_idle_ev")
        if idle is None:
            idle = self.__dict__["_idle_ev"] = asyncio.Event()
            if not self._inflight:
                idle.set()
        return idle

    async def _hold_for_recycle(self) -> None:
        """
        Bir geri dönüşüm sürerken toplam RECYCLE_HOLD_S bekler, sonra hata verir: istek her köprü hatası gibi başarısız
        olur ve istek katmanı sürer (curl, sonraki deneme).
        """
        end = time.monotonic() + RECYCLE_HOLD_S
        while self._recycling is not None:
            left = end - time.monotonic()
            try:
                if left <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait_for(self._recycling.wait(), left)
            except asyncio.TimeoutError:
                raise RuntimeError("The browser is being recycled") from None

    def running(self) -> bool:
        """Bir tarayıcı oturumu açık."""
        return self.session is not None

    def age_s(self) -> float:
        """Tarayıcının açılışından beri saniye; tarayıcı yoksa 0."""
        at = self._launched_at
        return 0.0 if at is None or self.session is None else max(0.0, self._clock() - at)

    def status(self) -> Dict[str, Any]:
        """Bellek (memory_mib, MiB), tarayıcının süreçleri ve şimdiye kadarki geri dönüşümler; /proc'u okur."""
        mem, source = memory_mib()
        return {
            "memory_mib": None if mem is None else round(mem),
            "memory_from": source,
            "browser_processes": browser_processes(),
            "browser_recycles": self.recycles,
        }

    def _maybe_recycle(self) -> None:
        """
        Sayfa isteklerinin arasında (RECYCLE_CHECK_S'de bir): tarayıcının belleği ya da yaşı öyle diyorsa (recycle_reason,
        `client.browser_recycle_mib` ve `browser_recycle_hours`) geri dönüşümü arka planda başlatır. Hiçbir isteği
        tutmaz: süren istekler biter, yeniler en çok RECYCLE_HOLD_S bekler; challenge başarısızken ya da token yakında
        bitiyorsa beklenir (_recycle). Bellek ve süreç sayısı köprü sağlığına yazılır (tanılama paketi).
        """
        if self.session is None or self._recycling is not None:
            return
        task = self._recycle_task
        if task is not None and not task.done():
            return
        now = time.monotonic()
        if now - self._recycle_checked_at < RECYCLE_CHECK_S:
            return
        self._recycle_checked_at = now
        limit_mib, every_h = _recycle_settings()
        mem, source = memory_mib()
        if self.report_health:
            bridge_health.update_solver({
                "memory_mib": None if mem is None else round(mem), "memory_from": source,
                "browser_processes": browser_processes(), "browser_recycles": self.recycles,
            })
        reason = recycle_reason(mem, self.age_s(), limit_mib=limit_mib, every_s=every_h * 3600.0)
        if reason is None:
            return
        self._recycle_task = asyncio.ensure_future(self._recycle_quietly(reason, mem))

    async def _recycle_quietly(self, reason: str, mem: Optional[float]) -> None:
        try:
            await self._recycle(reason, mem)
        except asyncio.CancelledError:
            raise
        except Exception:  # eski tarayıcı sürer ya da sonraki istek bir tane açar
            logger.exception("Browser recycle failed")

    async def _recycle(self, reason: str, before_mib: Optional[float]) -> bool:
        """
        Tarayıcı kapanır ve aynı profille yeniden açılır (S1-A3, c'nin 999e19d/ba73c6c/36319ac'si): renderer'larının
        belleği gider, profil ve onunla `sofa_captcha` cookie'si kalır; geçerli bir token yeni bir Turnstile istemez ve
        yeniden açılış çözmez. Sonraya kalır (bir sonraki denetim): bir çözüm sürerken, başarısızken ya da soğurken,
        token'ın RECYCLE_TOKEN_LEFT_S'den azı kaldıysa ya da token yoksa, ya da köprü geçici bir profilde açıksa
        (kapanınca silinir, cookie'si de). Yeni istekler onu en çok RECYCLE_HOLD_S bekler; süren istekler önce biter, en
        çok REQUEST_TIMEOUT, yoksa sonraya kalır. c'nin 5. koşusu: birimin anonim belleği 2 saatte 637'den 1254 MiB'a
        çıktı (tepesi 2,44 GB).
        """
        if self.session is None or self._recycling is not None:
            return False
        why = self._recycle_blocked()
        if why:
            logger.info(f"Browser recycle ({reason}) postponed: {why}")
            return False
        gate = self._recycling = asyncio.Event()
        try:
            # Süren istekler önce biter; bu arada birinin başlattığı bir çözüm (bir yenileme, ör.: cookie'si o bitene
            # kadar yok) beklemeyi hemen bitirir
            idle = self._idle_event()
            end = time.monotonic() + REQUEST_TIMEOUT
            while not idle.is_set():
                if self._recycle_blocked() or time.monotonic() >= end:
                    break
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(idle.wait(), min(1.0, max(0.0, end - time.monotonic())))
            if not idle.is_set():
                logger.warning(
                    f"Browser recycle ({reason}) postponed: {self._recycle_blocked() or 'fetches still under way'}"
                )
                return False
            why = self._recycle_blocked()
            if why:
                logger.info(f"Browser recycle ({reason}) postponed: {why}")
                return False
            logger.info(f"Recycling the browser ({reason})")
            async with self._init_lock:
                await self._relaunch(solve=False)
        finally:
            self._recycling = None
            gate.set()
        self.recycles += 1
        after, _ = memory_mib()
        logger.info(
            "Browser recycled (%s): memory %s MiB before, %s MiB after, %d browser process(es)",
            reason, "?" if before_mib is None else f"{before_mib:.0f}", "?" if after is None else f"{after:.0f}",
            browser_processes(),
        )
        self._publish_solver()
        return True

    def _recycle_blocked(self) -> str:
        """Geri dönüşüm neden şimdi beklemeli, ya da "" (_recycle)."""
        if self._solve_task is not None and not self._solve_task.done():
            return "a solve is under way"
        if self.solve_cooldown_left() > 0 or self.consecutive_solve_failures > 0:
            return "the challenge is failing"
        if self._launch_dir is not None:
            return "the browser runs on a temporary profile (its cookie would be lost)"
        exp = token_exp(self.token)
        if exp is None or exp - self._clock() < RECYCLE_TOKEN_LEFT_S:
            return "the token ends soon or is not there"
        return ""

    def _use_secondary_profile(self, owner: profile_lock.ProfileOwner) -> None:
        """Profil başka bir süreçte: bu süreç kardeş geçici profille açar (sofascore_scraper/client/profile_lock.py)."""
        try:
            self._launch_dir = profile_lock.secondary_profile(self.profile_dir)
        except OSError as e:
            raise RuntimeError(f"The browser profile {self.profile_dir} is in use by {owner.describe()} and no "
                               f"temporary profile could be created ({e})") from e
        logger.info("Browser profile %s is in use by %s; this process uses a temporary profile: %s",
                    self.profile_dir, owner.describe(), self._launch_dir)

    async def _launch_in_free_profile(self) -> None:
        """
        Tarayıcıyı boş bir profille açar. Profil başka bir canlı süreçteyse (`ssc serve` ile yan yana bir CLI
        komutu) geçici kardeş profil kullanılır; kilit denetimiyle başlatma arasında başka süreç profili alırsa
        Chromium'un kilit hatası görülür ve bir kez geçici profille yeniden denenir.
        """
        owner = profile_lock.profile_owner(self.profile_dir)
        if owner is not None:
            self._use_secondary_profile(owner)
        try:
            await self._launch()
        except Exception as e:
            if self._launch_dir is not None or not profile_lock.is_lock_error(e):
                raise
            await self.close()
            self._use_secondary_profile(profile_lock.profile_owner(self.profile_dir) or profile_lock.ProfileOwner())
            await self._launch()

    def _sandboxed(self) -> bool:
        sandbox = getattr(self, "sandbox", None)
        return _sandbox_setting() if sandbox is None else bool(sandbox)

    async def _launch(self) -> None:
        AsyncStealthySession = _session_class()
        headless = _headless()
        sandbox = self._sandboxed()
        logger.info(
            f"Starting the browser session (Scrapling StealthySession, headless={headless}, sandbox={sandbox})"
        )
        # Bugünkü argümanlar aynen; `sandbox` ile bir açılış seçeneği daha (c'nin K18'i: bir dağıtım gereği,
        # challenge yöntemi değil)
        extra: Dict[str, Any] = {"additional_args": {"chromium_sandbox": True}} if sandbox else {}
        self.session = AsyncStealthySession(
            headless=headless,
            solve_cloudflare=True,
            user_data_dir=self._launch_dir or self.profile_dir,
            proxy=_proxy_settings(),
            block_webrtc=True,
            timeout=60000,
            max_pages=2,
            # Yükleme başına tek gezinme: Scrapling başarısız bir fetch'i varsayılan olarak 3 kez dener (`retries`,
            # 1 sn arayla); her biri yük saatinin dışında bir captcha.html yüklemesiydi (c'nin B9'u)
            retries=1,
            **extra,
        )
        await self.session.start()
        # Scrapling'in oturum bağlamı: kendi sayfamızı bunun içinde açıp tekrar tekrar kullanırız
        self.context = getattr(self.session, "context", None)
        if self.context is None:
            raise RuntimeError("The Scrapling session opened no browser context (version mismatch?)")
        self._launched_at = self._clock()
        # Profilden gelen token uzun önce çözülmüş sayılır (`_token_at` 0): açılışın ilk _SOLVE_RETRY_AFTER'ında
        # yenileme yok, bir çökme döngüsü hemen yenileyemez
        self._refresh_not_before = self._launched_at + _SOLVE_RETRY_AFTER

        # Köprü sayfası site cookie'si olana kadar about:blank'te bekler (c'nin B8'i): cookie yokken /tr
        # captcha.html'ye yönlenir ve onun Turnstile widget'ı köprü sayfasında, çözümünkünün yanında, koşunun geri
        # kalanında çalışırdı (c'nin 5. koşusu)
        self.page = await self.context.new_page()
        solved_now = None
        if await self._token_from_context():
            await self._open_home()
        elif self._relaunch_solves and self.solve_cooldown_left() <= 0 and self._load_wait() <= 0:
            # Profilde çözüm yok: API isteklerinden önce çöz, token'ı her çözüm gibi API'ye karşı yokla (eskiden
            # açılışınki yoklanmazdı). Bekleme ve yük saati sürerken açılış çözmez.
            had = self._checked_token
            try:
                solved_now = await self._solve_checked(time.monotonic() + LAUNCH_SOLVE_BUDGET_S, again=False)
            except asyncio.CancelledError:
                # Yarıda kesildi (çağıranın STARTUP_TIMEOUT'u): API token'ını kabul etmediyse yine başarısız bir
                # çözümdür, beklemesiyle; yoksa sonraki istek captcha.html'yi hemen yeniden yüklerdi
                passed = self._checked_token is not None and self._checked_token != had
                self._count_solve(passed)
                if not passed:
                    self._start_cooldown("at launch (cut short)")
                raise
            self._count_solve(solved_now is not None)  # açılışın çözümü de bir çözümdür
            if solved_now is None:
                # c'ye taşınan kod gibi: kendi beklemesi yok; ilk sayfa isteği (cookie yoksa isteğin kendisi) yeniden
                # çözer. Siteye ulaşmayan bir yükleme UNREACHED_COOLDOWN_S bekler.
                logger.warning("The Turnstile challenge at launch left no sofa_captcha cookie")
                if self.solve_unreached:
                    self._start_cooldown("at launch")
        # Yoksa cookie yok ve şimdi çözüm yok: sayfa about:blank'te kalır, ilk istek çözer (fetch_json)
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
            cookies = await self.context.cookies(SITE_ORIGIN)
        except Exception:
            return None
        return next((c["value"] for c in cookies if c.get("name") == "sofa_captcha" and c.get("value")), None)

    @property
    def _load_starts(self) -> "collections.deque[float]":
        """Son saatin captcha.html yüklemelerinin başladığı anlar (_mono); `__new__` ile kurulan köprüde de vardır."""
        starts = self.__dict__.get("_load_starts_q")
        if starts is None:
            starts = self.__dict__["_load_starts_q"] = collections.deque()
        return starts

    async def _solve_on_captcha_page(self, loads: int = 1, deadline: float = 0.0) -> Optional[str]:
        """
        captcha.html'deki gömülü Turnstile'ı Scrapling'e çözdürür; sayfa geçtiyse token'ı, geçmediyse None döndürür.
        En çok `loads` yükleme, hiçbiri `deadline`'a (time.monotonic(); 0: şimdiden SOLVE_BUDGET_S) MIN_LOAD_S'den az
        kalmışken başlamaz; her yükleme yük saatine yazılır (_load_wait).

        Geçti: captcha.html, Scrapling'in çözücüsünden sonra REDIRECT_WAIT_S içinde siteye geçti (yönlendirmesi: /tr)
        ve `sofa_captcha` cookie'si orada, ya da yeni bir cookie geldi (c'nin B7'si). Scrapling 0.4.15 bunu ayırt
        edemez: `embedded` bir Turnstile için `_challenge_cleared` hep True'dur, tıklasa da tıklamasa da "Cloudflare
        captcha is solved" yazar ve bir daha tıklamaz (c'nin 5. koşusunda iki başarısız yüklemede de, biri sayfa
        yüklendikten 0,2 sn sonra). Eskiden çözüm, cookie'yi 10 sn sonra bulduğu gibi alırdı: hiç yönlenmeyen bir
        sayfa, eski bir cookie dururken yine "çözüldü" sayılırdı.
        """
        before = await self._token_from_context()
        deadline = deadline or time.monotonic() + SOLVE_BUDGET_S
        self.solve_unreached = False
        # Hiçbir yükleme siteye ulaşmadıysa bunu ağ söyler (S1-A4): sayfa captcha.html gezinmesine ya da Cloudflare'in
        # challenge'larından bir yanıt gördüyse (_reached; gezinmeden önce kurulan bir dinleyici) ya da siteye
        # geçtiyse ulaştı; dinleyici yerindeyken böyle bir yanıt görmeyen ve tarayıcının kendi ağ hatasını gören
        # ulaşmadı (ad yok, proxy düştü, paketler düştü). Dinleyici kurulamadıysa hata metni karar verir
        # (_navigation_error). Hiçbir şey yüklemeyen bir çözüm (zaman kalmadı) iki yönde de bir şey söylemez.
        attempted = reached = False
        for attempt in range(1, loads + 1):
            left = deadline - time.monotonic()
            if left < MIN_LOAD_S:
                break
            attempted = True
            started = self._mono()
            landed: List[str] = []
            answered: List[str] = []
            setup_ran: List[bool] = []
            checks: List["asyncio.Future[None]"] = []
            net_errors: List[str] = []

            async def check(response: Any, answered: List[str] = answered) -> None:
                if await _from_network(response):
                    answered.append(str(response.url))

            async def setup(page: Any, setup_ran: List[bool] = setup_ran, checks: List[Any] = checks,
                            net_errors: List[str] = net_errors) -> None:
                def on_response(response: Any) -> None:
                    if _reached(response, page):
                        checks.append(asyncio.ensure_future(check(response)))

                def on_failed(request: Any) -> None:
                    why = _network_failure(request)
                    if why is not None:
                        net_errors.append(why)

                page.on("response", on_response)
                page.on("requestfailed", on_failed)
                setup_ran.append(True)

            async def watch(page: Any, landed: List[str] = landed) -> None:
                landed.append(await self._await_redirect(page))

            try:
                await asyncio.wait_for(
                    # network_idle kapalı: captcha sayfası ana sayfaya yönlenirse reklam/websocket trafiği
                    # ağı hiç boşaltmaz ve çözüm Scrapling'in tüm zaman aşımını bekler (ölçüm: 65 sn).
                    # Çözücü kendi içinde kısa bir networkidle beklemesi yapıyor. `page_action` yönlendirmeyi bekler,
                    # `page_setup` dinleyicileri gezinmeden önce kurar.
                    self.session.fetch(CAPTCHA_URL, solve_cloudflare=True, network_idle=False, google_search=False,
                                       page_setup=setup, page_action=watch),
                    min(SOLVE_TIMEOUT, left),
                )
                navigation_failed = False
            except asyncio.CancelledError:
                # Yarıda kesildi: bu yüklemenin yanıtları her yüklemeninki gibi sayılır (bekleyen ağ denetimlerine bir
                # saniye); dinleyici yoksa bayrak önceki yüklemelerin bıraktığı gibi kalır
                if checks:
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(asyncio.wait(checks, timeout=1.0))
                if answered or (landed and _on_site(landed[0])) or not net_errors:
                    reached = True
                    self._load_starts.append(started)
                if setup_ran:
                    self.solve_unreached = not reached
                raise
            except Exception as e:  # yönlendirme ve cookie karar verir
                navigation_failed = _navigation_error(e)
                logger.warning(f"Solving on captcha.html failed: {e.__class__.__name__}: {e}")
            finally:
                await self._park_solver_pages()
            if checks:
                # Bu yanıtlar ağdan mı geldi (_from_network)
                await asyncio.wait(checks, timeout=_NETWORK_CHECK_S)
            # captcha.html'de durmak kanıt değildir (bir service worker ya da önbellek vermiş olabilir); siteye geçmek
            # kanıttır. Ulaşılmamak kanıt ister: tarayıcının kendi ağ hatası ve onlardan tek bayt yok; ne bayt ne
            # böyle bir hata gören yükleme ulaşmış sayılır (c'ye taşınan kodun beklemeleri)
            if answered or (landed and _on_site(landed[0])) or not (navigation_failed or net_errors):
                reached = True
                # PORT_LOADS_PER_HOUR'a sayılır: hiçbir şeye ulaşmayan bir yükleme Cloudflare'e de bir şey göstermedi
                self._load_starts.append(started)
            self.solve_unreached = attempted and not reached
            redirected = bool(landed) and _on_site(landed[0])
            token = await self._token_from_context()
            if redirected and not token:
                # Cookie sayfa ayrılırken yazılır: onun için biraz daha
                for _ in range(10):
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(_POLL_S)
                    token = await self._token_from_context()
                    if token:
                        break
            if token and (redirected or token != before):
                return token
            logger.info(
                "captcha.html did not pass (load %d of %d): %s", attempt, loads,
                "redirected, but no sofa_captcha cookie" if redirected
                else f"no redirect to the site within {REDIRECT_WAIT_S:g} s",
            )
        return None

    async def _park_solver_pages(self) -> None:
        """
        Scrapling'in çözüm sayfasını yüklemesi bitince kapatır (c'nin A2'si). Havuzu sayfayı bir fetch'ten sonra da
        tutar, ne yüklediyse onda: geçen bir çözümden sonra /tr'deki web uygulaması (websocket üzerinden canlı skorlar,
        reklamlar), c'nin 5. koşusunun tamamı boyunca çalıştı ve kendini 31 dakikada bir yeniden yükledi; bu yalnızca
        daha az gönderir. Havuz önce sayfayı unutur, bir sonraki çözüm yeni sayfa açar; havuzun bu API'si yoksa sayfa
        about:blank'e gider. Köprünün kendi sayfasına ve meşgul (yükleme süren) sayfalara dokunulmaz.
        """
        pool = getattr(self.session, "page_pool", None)
        pages = getattr(pool, "pages", None)
        if not isinstance(pages, list):
            return
        for info in list(pages):
            page = getattr(info, "page", None)
            if page is None or page is self.page or getattr(info, "state", "") == "busy":
                continue
            try:
                remove = getattr(pool, "remove_page", None)
                if callable(remove):
                    remove(info)
                    await page.close()
                else:
                    await page.goto("about:blank")
            except Exception as e:  # sonraki çözüm bir sayfa açar ya da bunu kullanır
                logger.debug(f"Closing Scrapling's solve page: {e}")

    async def _blank_bridge_page(self) -> None:
        """Köprü sayfası about:blank'e (istek yok); sayfa sitede değilse dokunulmaz."""
        page = self.page
        url = _page_url(page)
        if page is None or page.is_closed() or url is None or not _on_origin(url):
            return
        try:
            await page.goto("about:blank")
        except Exception as e:  # çözüm sürer; sayfa web uygulamasınındır
            logger.debug(f"The bridge page to about:blank: {e}")

    async def _open_home(self, deadline: float = 0.0) -> None:
        """
        Köprü sayfası ana sayfaya, cookie olduğunda (c'nin B8'i). Sayfa captcha.html dışında sitenin bir sayfasındaysa
        yeniden açılmaz (web uygulaması kendi başına gezinir). En çok 30 sn, `deadline` (time.monotonic()) varsa ondan
        önce (bir çözümün: _HOME_S onun için ayrılır).
        """
        page = self.page
        if page is None or page.is_closed() or _on_site(_page_url(page) or ""):
            return
        timeout_s = 30.0
        if deadline:
            timeout_s = max(1.0, min(timeout_s, deadline - time.monotonic()))
        try:
            await page.goto(self._home(), wait_until="domcontentloaded", timeout=timeout_s * 1000)
        except Exception as e:  # yükleme zaman aşımına uğrasa da sayfa kullanılabilir
            logger.warning(f"Opening the SofaScore home page: {e}")

    async def _await_redirect(self, page: Any) -> str:
        """Scrapling'in `page_action`'ı: sayfa captcha.html'den siteye geçince adresi, ya da REDIRECT_WAIT_S sonraki hali."""
        waited = 0.0
        while True:
            url = str(getattr(page, "url", "") or "")
            if _on_site(url) or waited >= REDIRECT_WAIT_S:
                return url
            await asyncio.sleep(_POLL_S)
            waited += _POLL_S

    def _set_token(self, token: str) -> None:
        global _cached_token, _cached_at
        self.token = token
        self._token_at = self._clock()
        # curl_cffi istekleri de bu tokeni kullansın (get_request_headers → get_cached_token)
        _cached_token = token
        _cached_at = time.time()

    async def solve_challenge(self, *, drop_token: bool = False) -> Optional[str]:
        """
        Turnstile challenge'ını çözer ve geçerli token'ı döndürür.

        `drop_token`: API token'ın kendisini reddetti (`token_refused`); captcha sayfası açılmadan önce
        `sofa_captcha` cookie'si silinir (cookie dururken sayfa challenge göstermeden yönlenir, yeni cookie üretmez).

        Aynı anda gelen 403'lerin hepsi aynı çözümü bekler. Az önce çözülmüş bir token
        doğrudan döndürülür; başarısız bir çözümden sonra bir süre yeniden denenmez ve API'nin reddettiği
        token'ı da döndürülmez: önce bekleme gelir (c'nin B3'ü; eskiden beklemenin ilk _FRESH_TOKEN_SECONDS'ında
        gelen bir sayfa isteği o token'la yeniden gönderilirdi: reddedileceği belli iki istek).
        """
        await self.ensure_ready()
        if self.solve_cooldown_left() > 0:
            return None
        if self.token and self._clock() - self._token_at < _FRESH_TOKEN_SECONDS:
            return self.token
        running = self._solve_task is not None and not self._solve_task.done()
        wait = self._load_wait()
        if not running and wait > 0:
            # En çok bir bekleme önce başarısız bir yükleme ya da saatin yüklemeleri dolu: zamanı gelmeden yükleme
            # yok; okumalar kalan süre boyunca her başarısızlıktan sonraki gibi bekler
            self._solve_cooldown_s = wait
            self._solve_failed_at = self._clock()
            self._publish_solver()
            logger.info(f"A captcha.html load failed lately; no new attempt for {int(wait)} s")
            return None
        if self._solve_task is None or self._solve_task.done():
            # Çözümün (doğrulama isteğinin) sıra beklemesi kendi sayacına yazılır: görev, bağlamı
            # oluşturulduğu anda kopyalar
            solve_wait = _SlotWait(shared=True)
            token = _slot_wait.set(solve_wait)
            try:
                solve = self._solve_challenge(drop_token=True) if drop_token else self._solve_challenge()
                self._solve_task = asyncio.ensure_future(solve)
            finally:
                _slot_wait.reset(token)
            self._solve_wait = solve_wait
        # Ortak çözümü bekleyen her çağrı, çözümün sıra beklemesi kadar ek süre alır
        wait = _slot_wait.get()
        if wait is not None and self._solve_wait is not None and self._solve_wait not in wait.joined:
            wait.joined.append(self._solve_wait)
        # shield: bir bekleyenin iptali diğerlerinin beklediği çözümü iptal etmesin
        return await asyncio.shield(self._solve_task)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        """
        Köprü sayfasında JS çalıştırır. Sayfa kendi kendine yönlenirse (SofaScore ana sayfası
        zaman zaman yeniden yüklenir) süren çağrı "Execution context was destroyed" ile düşer:
        sayfanın yüklenmesi beklenip bir kez daha denenir; sayfa sofascore.com dışına çıktıysa
        ana sayfaya yalnızca cookie varken ve süren bir çözüm yokken dönülür (c'nin B10'u): yoksa /tr captcha.html'ye
        yönlenirdi (kendi yüklemesi ve canlı bir widget); istek, her köprü hatası gibi başarısız olur.
        """
        for attempt in range(3):
            try:
                return await self.page.evaluate(script, arg)
            except Exception as e:
                msg = str(e)
                if attempt == 2 or not any(k in msg for k in ("Execution context was destroyed", "navigation", "Target page, context or browser has been closed")):
                    raise
                logger.info(f"The bridge page navigated away; retrying ({attempt + 1}/2)")
                if self.page.is_closed():
                    self.page = None
                    if self._launching:
                        # Açılış, ensure_ready'nin bekleyeceği kilidi STARTUP_TIMEOUT'una kadar tutar: onun
                        # yoklaması şimdi başarısız olur
                        raise RuntimeError("The bridge page closed during the launch") from None
                    if self._recycling is not None:
                        # Bir geri dönüşüm tam da bu isteği bekliyor: burada onu beklemek yok
                        raise RuntimeError("The browser is being recycled") from None
                    await self.ensure_ready()
                    continue
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception as le:  # yeniden deneme söyler
                    logger.warning(f"The bridge page could not recover: {le}")
                if not str(self.page.url).startswith(SITE_ORIGIN):
                    # Site dışında (bir çözüm onu about:blank'e götürdü, ör.): ana sayfaya yalnızca cookie varken ve
                    # çözüm sürmüyorken, _open_home ile
                    solving = self._solve_task is not None and not self._solve_task.done()
                    if solving or not await self._token_from_context():
                        raise RuntimeError("The bridge page is off the site") from None
                    await self._open_home()

    async def _api_fetch(self, url: str, cache_mode: str) -> Dict[str, Any]:
        """
        Köprüden çıkan tek API isteği. Tarayıcıdan giden her istek buradan geçer: önce süreçler
        arası ortak bütçeden sıra alınır (sofascore_scraper/throttle.py), sonra sayfada fetch() çalışır.
        Sıra bekleme, çağrının zaman aşımından sayılmaz (_SlotWait).
        """
        await _wait_for_slot()
        if self._held_back():
            # Sıra beklenirken bir çözüm başarısız oldu (ya da istek katmanının kapısı kapandı): istek gönderilmez
            # (c'nin B4'ü; eskiden böyle istekler sayfadan gönderilirdi, bazıları reddedilen token'la iki kez)
            return self._unsent()
        x_req = hashlib.sha256(str(int(time.time()) // 1800).encode("utf-8")).hexdigest()[:6]
        return await self.evaluate(_FETCH_JS, [url, x_req, self.token, _JS_FETCH_TIMEOUT_MS, cache_mode])

    def _unsent(self) -> Dict[str, Any]:
        """
        Gönderilmeden verilen yanıt: challenge'ın 403'ü, ya da son çözümün yüklemeleri siteye ulaşmadıysa durum 0 (S1-A4:
        tarayıcı SofaScore'a ulaşamadı; istek katmanı onu ağın başarısızlığı sayar, challenge'ın değil).
        """
        if self.solve_unreached:
            return {"status": 0, "ok": False, "data": None, "sent": False,
                    "text": "the browser could not reach SofaScore (no load of captcha.html did)"}
        return {"status": 403, "ok": False, "data": None, "text": _UNSENT_CHALLENGE, "sent": False}

    def _held_back(self) -> bool:
        """Sırası gelen sayfa isteği gönderilmesin mi: çözüm beklemesi ya da istek katmanının kapısı (S1-B4)."""
        if not _page_fetching.get():
            return False  # çözümün yoklaması: çözüm sürerken bekleme yoktur
        if self.solve_cooldown_left() > 0:
            return True
        out = _outcome.get()
        hold = getattr(out, "hold", None)
        try:
            return bool(hold is not None and hold())
        except Exception:  # kapı isteği bozmamalı
            return False

    async def _page_fetch(self, url: str) -> Dict[str, Any]:
        """Sayfa isteğinin kendisi (_api_fetch); sırası geldiğinde kapıya bakılır (_held_back)."""
        token = _page_fetching.set(True)
        try:
            return await self._api_fetch(url, cache_mode_for(url))
        finally:
            _page_fetching.reset(token)

    async def _api_unlocked(self) -> Optional[bool]:
        """
        Çözüm API'yi gerçekten açtı mı? Yoklanamıyorsa None (sitenin sayfası yok: ana sayfa yüklenmedi): token
        yoklanmadan tutulur.
        """
        page = self.page
        if page is None or page.is_closed():
            return None
        url = _page_url(page)
        if url is not None and not _on_origin(url):
            return None
        res = await self._api_fetch(_PROBE_URL or _api_url(_PROBE_PATH), "no-store")
        return res.get("status") != 403

    async def _solve_challenge(self, *, drop_token: bool = False, refresh: bool = False) -> Optional[str]:
        """
        Çözümün kendisi. `drop_token`: önce `sofa_captcha` cookie'si silinir (API token'ı reddetti). `refresh`: aynısı,
        token'ın `exp`'inden önce (_start_refresh); başarısız olan cookie'yi geri koyar ve bekleme başlatmaz, çünkü
        token `exp`'ine kadar yanıt alır.
        """
        saved = await self._captcha_cookie() if refresh else None
        kept = self.token, self._token_at
        if drop_token or refresh:
            # Cookie dururken captcha.html challenge göstermeden yönlenir ve yeni cookie üretmez (aşağıdaki
            # sil-ve-yeniden-çöz yolunun bir captcha sayfası önce yapılanı)
            try:
                await self.context.clear_cookies(name="sofa_captcha")
            except Exception as e:  # aşağıdaki çözüm yine dener
                logger.debug(f"Deleting the sofa_captcha cookie: {e}")
        logger.info("Solving the Cloudflare Turnstile challenge (captcha.html)")
        if drop_token and not refresh:
            # /tr'deki web uygulaması cookie'si gidince kendini (yaklaşık 31 dakikada bir) captcha.html'ye yeniden
            # yükler ve duvar boyunca orada canlı bir widget tutar (c'nin soak'ı): sayfa about:blank'te bekler, cookie
            # olunca ana sayfaya döner (_open_home)
            await self._blank_bridge_page()
        # Yenileme: tek yükleme (reddedilen token'ı başarısız bir yenilemedir, yeniden yüklenmez)
        token = await self._solve_checked(again=not refresh)
        if refresh and not token:
            # Sayılır ama başarısız çözüm serisinde değildir: kullanılan token hâlâ yanıt alır. Başarısızlıkları
            # sonraki denemeyi c'ye taşınan kodun başarısız çözüm serisi gibi aralar (180, 180, 360 sn); exp'ten sonraki
            # çözümler kendi serisini tutar. Siteye ulaşmayan bir deneme UNREACHED_COOLDOWN_S bekler, sayılmaz.
            self.solves += 1
            self.solve_failures += 1
            if self.solve_unreached:
                wait = UNREACHED_COOLDOWN_S
            else:
                self._refresh_failures += 1
                wait = solve_retry_after(self._refresh_failures)
            self._load_not_before = self._mono() + wait
            self._publish_solver()
        else:
            self._count_solve(bool(token))
        if token:
            self._load_not_before = -math.inf
            self._set_token(token)
            self._solve_failed_at = 0.0
            return token
        if refresh:
            # Önceki token sayfanın kalır (API'nin reddettiği o değil)
            global _cached_token, _cached_at
            self.token, self._token_at = kept
            if kept[0] is not None:
                _cached_token, _cached_at = kept[0], time.time()
            if saved is not None:
                try:
                    await self.context.add_cookies([saved])
                except Exception as e:  # exp yine de çözer
                    logger.debug(f"Putting the sofa_captcha cookie back: {e}")
            logger.warning(
                "Refreshing the captcha token failed; the current one is kept until it expires, then a refused "
                "request solves again"
            )
            return None
        self._start_cooldown()
        return None

    def _refresh_due(self) -> Optional[float]:
        """
        Token'ın `exp`'inden önce yeniden çözme zamanı geldiyse kalan saniyesi, yoksa None (S1-A6): `exp`'ten
        REFRESH_BEFORE_EXP_S önceden itibaren, REFRESH_MIN_LEFT_S'den fazlası kalmışken; son başarısız yüklemenin
        beklemesi bittiğinde bir deneme (_load_wait: 180, 180, 360 sn ve saatte en çok 8 yükleme), token başına en çok
        REFRESH_MAX_LOADS, çözüm sürerken ya da soğurken hiç. Deneme başına tek captcha.html yüklemesi; başarısız bir
        yenileme hiçbir okumayı tutmaz. c'nin 4. koşusu açılışta bir kez çözdü ve exp'ten sonra kördü ("challenge"
        demeyen bir 403); soak'ta yenileme exp'ten 120 sn önce bir kez denendi ve 3'te 3 başarısız oldu.
        """
        token = self.token
        if not token:
            return None
        if self._clock() - self._token_at < REFRESH_MIN_AGE_S:
            # Çözülür çözülmez bu kadar az kalan bir token jwt 403'üne bırakılır: gerçekten bittiğinde çözülür
            return None
        exp = token_exp(token)
        if exp is None:
            return None
        left = exp - self._clock()
        if not REFRESH_MIN_LEFT_S < left <= REFRESH_BEFORE_EXP_S:
            return None
        if token == self._refresh_token and self._refresh_loads >= REFRESH_MAX_LOADS:
            return None
        if self._load_wait() > 0 or self._clock() < self._refresh_not_before:
            return None
        if self.solve_cooldown_left() > 0:
            return None
        if self._solve_task is not None and not self._solve_task.done():
            return None
        return left

    def _start_refresh(self) -> None:
        """
        Zamanı geldiyse (_refresh_due) bir yenileme denemesini arka planda başlatır: sayfanın istekleri bu arada hâlâ
        geçerli olan token'la sürer; cookie yokken reddedilen bir istek her çözümü beklediği gibi bunu bekler ve
        yinelenir. Yeni bir token (geçen bir yenileme, her çözüm) kendi denemelerini başlatır.
        """
        left = self._refresh_due()
        if left is None:
            return
        if self.token != self._refresh_token:
            self._refresh_token, self._refresh_loads, self._refresh_failures = self.token, 0, 0
        self._refresh_loads += 1
        logger.info(
            f"The captcha token expires in {int(left)} s; solving the Turnstile again "
            f"(try {self._refresh_loads} of {REFRESH_MAX_LOADS})"
        )
        # Çözümün (yoklamasının) sıra beklemesi kendi sayacına yazılır, isteklerin iptali onu kesmez
        solve_wait = _SlotWait(shared=True)
        token = _slot_wait.set(solve_wait)
        try:
            self._solve_task = asyncio.ensure_future(self._refresh())
        finally:
            _slot_wait.reset(token)
        self._solve_wait = solve_wait

    async def _refresh(self) -> Optional[str]:
        try:
            return await self._solve_challenge(refresh=True)
        except Exception as e:  # en iyi çaba; exp yine de çözer
            logger.warning(f"Refreshing the captcha token: {e.__class__.__name__}: {e}")
            return None

    async def _captcha_cookie(self) -> Optional[Dict[str, Any]]:
        """`sofa_captcha` cookie'si bağlamın tuttuğu haliyle (geri koymak için), varsa."""
        try:
            cookies = await self.context.cookies(SITE_ORIGIN)
        except Exception:  # bağlam yok ya da tarayıcı kapanıyor
            return None
        return next((c for c in cookies if c.get("name") == "sofa_captcha"), None)

    async def _solve_checked(self, deadline: float = 0.0, loads: int = 0, *, again: bool = True) -> Optional[str]:
        """
        captcha.html'de bir çözüm, token'ı köprü sayfasından API'ye karşı yoklanır; API'nin hâlâ reddettiği token
        (profilde süresi geçmiş cookie: captcha.html challenge göstermeden yönlenir ve yeni cookie üretilmez)
        silinir ve bir kez daha, tek yüklemeyle çözülür (c'nin B9'u: çözüm başına tek yükleme; eskiden aynı çözüm
        Scrapling'in yeniden denemeleriyle üç yüklemeye kadar çıkabiliyordu). Hepsi `deadline`'dan (0: şimdiden
        SOLVE_BUDGET_S) önce, her yoklamaya _PROBE_S kalarak. `again=False`: ikinci yükleme yok (açılışın çözümü).
        Saatin yüklemeleri dolduysa (PORT_LOADS_PER_HOUR) ikinci yükleme yapılmaz: başarısız bir çözüm.
        """
        deadline = deadline or time.monotonic() + SOLVE_BUDGET_S
        # Her captcha.html çözümü ana sayfanın yüklemesine ve bir yoklamaya zaman bırakır; ikincisi (reddedilen
        # yoklama) ancak yüklemesi hâlâ sığıyorsa (MIN_LOAD_S)
        rest_s = _HOME_S + _PROBE_S
        token = await self._solve_on_captcha_page(loads or 1, deadline - rest_s)
        if token:
            self._set_token(token)
            await self._open_home(deadline - _PROBE_S)
        unlocked = await self._api_unlocked() if token else None
        if token and unlocked:
            self._checked_token = token
        elif token and unlocked is None:
            pass  # yoklanmadı (ana sayfa yüklenmedi): tutulur, _checked_token değil
        elif token and not again:
            # Açılışınki: c'ye taşınan kodun açılışı gibi tek yükleme; reddedilen token başarısız bir açılış çözümüdür,
            # ilk isteğin çözümü onu siler ve yeniden yükler
            token = None
        elif token and self._load_wait() > 0:
            token = None
        elif token:
            # Profildeki süresi geçmiş cookie: cookie silinip challenge baştan çözülür
            logger.info("The sofa_captcha cookie is still refused; deleting it and solving the challenge again")
            await self.context.clear_cookies(name="sofa_captcha")
            token = await self._solve_on_captcha_page(1, deadline - rest_s)
            if token:
                self._set_token(token)
                await self._open_home(deadline - _PROBE_S)
            unlocked = await self._api_unlocked() if token else None
            if token and unlocked is False:
                token = None
            elif token and unlocked:
                self._checked_token = token
        if token:
            left = token_left(token, self._clock())
            logger.info(
                "The Turnstile challenge is solved (the token has %s left)",
                "an unknown time" if left is None else f"{left:.0f} s",
            )
        return token

    def _load_wait(self) -> float:
        """
        Bir captcha.html yüklemesinin (403'ün çözümü, açılışınki) başlamasına kalan saniye: son başarısız yüklemenin
        beklemesi ve kayan bir saatte en çok PORT_LOADS_PER_HOUR yükleme (c'nin B9'u: tek yük saati).
        """
        now = self._mono()
        starts = self._load_starts
        while starts and starts[0] <= now - 3600.0:
            starts.popleft()
        wait = self._load_not_before - now
        if len(starts) >= PORT_LOADS_PER_HOUR:
            wait = max(wait, starts[0] + 3600.0 - now)
        return max(0.0, wait)

    def _start_cooldown(self, where: str = "") -> None:
        """
        Başarısız çözümün beklemesi: solve_retry_after(art arda başarısız çözüm sayısı), ya da yüklemesi siteye
        ulaşmadıysa UNREACHED_COOLDOWN_S (S1-A4: istek katmanı bunun tuttuklarını challenge'ın değil ağın sayar);
        yük saati de ona uyar.
        """
        if self.solve_unreached:
            self._solve_cooldown_s = UNREACHED_COOLDOWN_S
            where = (where + " " if where else "") + "(the browser could not reach SofaScore)"
        else:
            self._solve_cooldown_s = solve_retry_after(self.consecutive_solve_failures)
        self._solve_failed_at = self._clock()
        self._load_not_before = max(self._load_not_before, self._mono() + self._solve_cooldown_s)
        self._publish_solver()
        logger.warning(
            f"The Turnstile challenge could not be solved{' ' + where if where else ''} "
            f"({self.consecutive_solve_failures} in a row); not trying again for {int(self._solve_cooldown_s)} s."
        )

    def solve_cooldown_left(self) -> float:
        """
        Başarısız bir çözümden sonra yeni çözüme kalan saniye (solve_retry_after); başarısız çözüm yoksa ya da bekleme
        bittiyse 0. İstek katmanı bunu başka thread'den okur.
        """
        if not self._solve_failed_at:
            return 0.0
        return max(0.0, self._solve_failed_at + self._solve_cooldown_s - self._clock())

    def reset_solve_streak(self) -> None:
        """
        SofaScore yanıt verdi (200 ya da 404; curl'ün ya da sayfanın): başarısız çözümler artık art arda değildir, bir
        sonraki başarısızlık yeniden 180 sn bekler. c'de 2026-10-02'ye kadar seriyi yalnızca geçen bir çözüm
        bitiriyordu: Cloudflare'in kendiliğinden bitirdiği bir fırtına seriyi 2-5'te bırakır, saatler sonraki fırtına
        360 ya da 600 sn'lik beklemeyle başlardı. Süren bir bekleme kısalmaz: çözüm başarısız olmadan önce gönderilmiş
        bir isteğin yanıtı duvar hakkında bir şey söylemez.
        """
        if self.consecutive_solve_failures:
            self.consecutive_solve_failures = 0
            self._publish_solver()

    def _count_solve(self, solved: bool) -> None:
        """
        Bir çözümü sayar, açılışınkini de (c'nin A1'i; yalnızca gözlem: çözümün kendisi değişmez). Üretim benzeri
        koşuda (c, 2026-10-01) her çözüm dakikalarca başarısız oldu ve bunu gösteren bir sayı yoktu. Hiçbir yüklemesi
        siteye ulaşmayan çözüm (S1-A4) ağın başarısızlığıdır, challenge'ın değil: başarısız çözüm serisine girmez
        (beklemeleri 600 sn'ye büyür ve okumaları ağ döndükten sonra da o kadar tutardı).
        """
        self.solves += 1
        if solved:
            self.consecutive_solve_failures = 0
        else:
            self.solve_failures += 1
            if not self.solve_unreached:
                self.consecutive_solve_failures += 1
        self._publish_solver()

    def solver_status(self) -> Dict[str, Any]:
        """Çözüm sayaçları ve süren bekleme (bridge_health.solver(), tanılama paketi)."""
        left = self.solve_cooldown_left()
        return {
            "solves": self.solves,
            "solve_failures": self.solve_failures,
            "consecutive_solve_failures": self.consecutive_solve_failures,
            "solve_unreached": self.solve_unreached,
            "browser_recycles": self.recycles,
            # Beklemenin bittiği an (epoch sn); bridge_health.solver() kalan süreyi okuma anında hesaplar
            "solve_cooldown_until": time.time() + left if left > 0 else None,
        }

    def _publish_solver(self) -> None:
        """Sayaçlar süreç genelindeki köprü sağlığına yazılır (yalnızca indirmelerin köprüsü, `report_health`)."""
        if self.report_health:
            bridge_health.update_solver(self.solver_status())

    async def fetch_json(self, path_or_url: str) -> Optional[Any]:
        """
        Sofascore API isteğini tarayıcı oturumu içinden fetch() ile yapar.
        403 challenge durumunda Turnstile'ı otomatik çözüp isteği yineler.
        """
        # Tarayıcı geri dönüştürülürken yenisini en çok RECYCLE_HOLD_S bekler; süren bir istek geri dönüşümü
        # bitene kadar tutar (S1-A3)
        await self._hold_for_recycle()
        self._inflight += 1
        self._idle_event().clear()
        try:
            await self.ensure_ready()

            url = path_or_url if path_or_url.startswith("http") else _api_url(path_or_url)
            res = await self._fetch_now(url)
        finally:
            self._inflight -= 1
            if self._inflight == 0:
                self._idle_event().set()
        out = _outcome.get()
        if out is not None:
            out.status = int(res.get("status") or 0)
            out.text = "" if res.get("ok") else str(res.get("text") or "")
            out.sent = bool(res.get("sent", True))
        answer = self._answer(res, url)
        self._maybe_recycle()
        return answer

    async def _fetch_now(self, url: str) -> Dict[str, Any]:
        """
        Sayfadan istek; bir çözümün yanıtlayacağı 403'te (needs_solve: challenge ya da token'ın reddi) çöz ve bir kez
        yinele. Sayfa sitede değilse (about:blank: açılışta cookie yoktu ya da çözüm yapılmadı) istek oradan
        gönderilmez (c'nin B8'i): önce cookie (varsa ana sayfa), yoksa bir çözüm (beklemesine uyarak), o da yoksa
        challenge'ın yanıtı, gönderilmeden (`sent` False).
        """
        if self.solve_cooldown_left() > 0:
            # Başarısız bir çözüm soğuyor: her istek challenge'a takılır ve onu yanıtlayacak bir çözüm başlayamaz;
            # istek gönderilmeden challenge'ın yanıtı (c'nin B4'ü), çözüm siteye ulaşamadıysa ağın (A4)
            return self._unsent()
        page_url = _page_url(self.page)
        if page_url is not None and not _on_origin(page_url):
            solved = False
            if await self._token_from_context():
                await self._open_home()
            elif not await self.solve_challenge():
                return self._unsent()
            else:
                solved = True
            if solved and not _on_origin(_page_url(self.page) or ""):
                # Bir kez daha, isteğin ilk denemesinin (burada yok) bıraktığı sürede: geçen bir çözümden sonra /tr
                # çözümün _HOME_S'inden uzun sürebilir
                await self._open_home(time.monotonic() + _PROBE_S)
            if not _on_origin(_page_url(self.page) or ""):
                return {"status": 0, "ok": False, "data": None, "sent": False,
                        "text": f"the bridge page could not open the site ({_page_url(self.page)})"}

        # Token exp'ine yaklaştıysa arka planda yeniden çözülür (S1-A6); bu istek şimdiki token'la sürer
        self._start_refresh()
        sent = self.token
        res = await self._page_fetch(url)
        if not res.get("sent", True):
            return res
        if int(res.get("status") or 0):
            # Sayfa SofaScore'a ulaştı: ağ geri geldi (istek katmanı beklemeyi artık ağınki saymaz)
            self.solve_unreached = False

        # Bir çözümün yanıtlayacağı 403 (challenge ya da token'ın reddi, needs_solve): çöz ve bir kez yinele
        text = res.get("text") or ""
        if needs_solve(res.get("status"), text):
            refused = token_refused(text)
            if refused and self.token and self.token != sent:
                # Bu istek yoldayken bir çözüm token'ı değiştirdi: yenisiyle yinelenir, çözüm gerekmez
                new_token: Optional[str] = self.token
            else:
                logger.info(
                    "The API answered 403 %s; solving Turnstile",
                    "rejecting the captcha token" if refused else "with a challenge",
                )
                new_token = await (self.solve_challenge(drop_token=True) if refused else self.solve_challenge())
            if new_token:
                res = await self._page_fetch(url)
        return res

    def _answer(self, res: Dict[str, Any], url: str) -> Optional[Any]:
        """Sayfanın yanıtı → veri, {"__404__": True} ya da None; sağlık sinyaliyle."""
        # Sağlık sinyali (sofascore_scraper/bridge_health.py): isteğin SON hali sayılır — çözülüp yinelenen 403 başarıdır
        if res.get("ok"):
            bridge_health.record_success()
            self.reset_solve_streak()
            return res.get("data")

        if res.get("status") == 404:
            bridge_health.record_success()  # API yanıt verdi; kaynak yok
            self.reset_solve_streak()
            logger.debug(f"Not found (404): {url}")
            return {"__404__": True}

        if res.get("status") == 403:
            text = res.get("text") or ""
            kind = bridge_health.KIND_CHALLENGE if needs_solve(403, text) else bridge_health.KIND_FORBIDDEN
            bridge_health.record_failure(kind, f"HTTP 403: {text[:100]}")

        logger.warning(f"Browser fetch failed (status {res.get('status')}): {(res.get('text') or '')[:100]}")
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
        if getattr(self, "_launch_dir", None) is not None:
            profile_lock.remove_secondary(self._launch_dir)
            self._launch_dir = None


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
    """curl_cffi ile aynı proxy (`client.use_proxy`, `client.proxy`): tarayıcı da gerçek IP'yi göstermesin."""
    client = _client_settings()
    if not client.use_proxy:
        return None
    url = (client.proxy or "").strip()
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


def _submit(coro) -> "Tuple[concurrent.futures.Future[Any], _SlotWait]":
    """
    Coroutine'i arka plan döngüsüne verir. Döngüdeki görev, çağıranın o anki bağlamını kopyalar
    (call_soon_threadsafe); böylece köprünün içindeki sıra beklemeleri bu çağrının sayacına yazılır.
    """
    wait = _SlotWait()
    token = _slot_wait.set(wait)
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, _get_background_loop())
    finally:
        _slot_wait.reset(token)
    return fut, wait


def _caller_cancelled() -> bool:
    """Çağıranın işi iptal edildi mi (sofascore_scraper/client/context.py'deki iptal kontrolü)."""
    try:
        raise_if_cancelled()
    except FetchCancelled:
        return True
    return False


def _run_sync(coro, timeout: float, *, cancellable: bool = False) -> Any:
    """
    `timeout`: ortak bütçede sıra beklenen süre HARİÇ en uzun çalışma süresi.

    `cancellable`: çağıran thread beklerken kendi iptal kontrolüne de _CANCEL_CHECK_SECONDS'ta bir bakar.
    İptalde önce coroutine'e kısa bir süre tanınır: sıra beklemesindeyse kendisi FetchCancelled ile biter ve
    sırayı bütçeye geri verir (_wait_for_slot). Bitmezse (ortak challenge çözümünü ya da sayfanın fetch()'ini
    bekliyordur) çağıran beklemeyi bırakır ve FetchCancelled fırlatır; coroutine iptal edilir, ortak çözüm
    (asyncio.shield) öteki bekleyenler için sürer. Tarayıcının açılışı bu yolla kesilmez (yarım kalan açılış
    profili kilitli bırakır): ensure_ready çağrıları `cancellable` vermez.
    """
    fut, wait = _submit(coro)
    started = time.monotonic()
    try:
        while True:
            left = started + timeout + wait.seconds - time.monotonic()
            step = min(left, _CANCEL_CHECK_SECONDS) if cancellable else left
            try:
                return fut.result(timeout=max(0.0, step))
            except concurrent.futures.TimeoutError:
                if fut.done():
                    return fut.result()  # coroutine'in kendi zaman aşımı (ya da tam o anda biten sonuç)
                if cancellable and _caller_cancelled():
                    try:
                        return fut.result(timeout=2 * _CANCEL_CHECK_SECONDS)
                    except concurrent.futures.TimeoutError:
                        raise FetchCancelled() from None
                if started + timeout + wait.seconds - time.monotonic() <= 0:
                    raise
                # bu arada sıra beklendi: son tarih uzadı
    except BaseException:
        fut.cancel()
        raise


async def _run_on_background_loop(coro, timeout: float = REQUEST_TIMEOUT, *, cancellable: bool = False) -> Any:
    """
    Coroutine'i BrowserBridge'in arka plan döngüsünde çalıştırır ve sonucu bekler.
    Playwright nesneleri oluşturuldukları döngüye bağlıdır; çağıranın döngüsü
    (örn. asyncio.run ile açılıp kapanan geçici döngü) kullanılırsa sonraki çağrılar askıda kalır.
    `timeout`: ortak bütçede sıra beklenen süre HARİÇ en uzun çalışma süresi.

    `cancellable`: _run_sync'teki gibi (FX-18, plan bölüm 15 satır 61). Çağıran beklerken kendi iptal kontrolüne
    _CANCEL_CHECK_SECONDS'ta bir bakar; iptalde coroutine'e kısa bir süre tanınır (sıra beklemesindeyse kendisi
    biter ve sırayı geri verir; o arada gelen yanıt döndürülür), bitmezse çağıran FetchCancelled fırlatır ve
    coroutine iptal edilir. Ortak challenge çözümü (asyncio.shield) öteki bekleyenler için sürer. Tarayıcının
    açılışı bu yolla kesilmez: ensure_ready çağrıları `cancellable` vermez.
    """
    fut, wait = _submit(coro)
    wrapped = asyncio.wrap_future(fut)
    started = time.monotonic()
    try:
        while True:
            left = started + timeout + wait.seconds - time.monotonic()
            if left <= 0:
                raise asyncio.TimeoutError()
            step = min(left, _CANCEL_CHECK_SECONDS) if cancellable else left
            done, _ = await asyncio.wait({wrapped}, timeout=step)
            if done:
                return wrapped.result()
            if cancellable and _caller_cancelled():
                done, _ = await asyncio.wait({wrapped}, timeout=2 * _CANCEL_CHECK_SECONDS)
                if done:
                    return wrapped.result()
                raise FetchCancelled()
            # bu arada sıra beklenmiş olabilir: son tarih uzadı
    except BaseException:
        wrapped.cancel()  # asyncio.wait_for'un yaptığı gibi: iptal arka plandaki göreve de ulaşır
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
        return _run_sync(bridge.fetch_json(path_or_url), timeout, cancellable=True)
    except Exception as e:
        logger.error(f"fetch_api_via_browser_sync failed: {e!r}")
        return None


async def fetch_api_via_browser(path_or_url: str) -> Optional[Any]:
    """Asenkron API istek köprüsü."""
    bridge = BrowserBridge.get_instance()
    await _run_on_background_loop(bridge.ensure_ready(), STARTUP_TIMEOUT)
    return await _run_on_background_loop(bridge.fetch_json(path_or_url), REQUEST_TIMEOUT, cancellable=True)


async def solve_turnstile_challenge(timeout_ms: int = 35000, headless: Optional[bool] = None) -> Optional[str]:
    """Turnstile challenge çözücü."""
    bridge = BrowserBridge.get_instance()
    await _run_on_background_loop(bridge.ensure_ready(), STARTUP_TIMEOUT)
    return await _run_on_background_loop(bridge.solve_challenge(), timeout_ms / 1000 + 10, cancellable=True)


def solve_turnstile_challenge_sync(timeout_ms: int = 30000) -> Optional[str]:
    """Senkron Turnstile challenge çözücü."""
    cached = get_cached_token()
    if cached:
        return cached

    bridge = BrowserBridge.get_instance()
    try:
        _run_sync(bridge.ensure_ready(), STARTUP_TIMEOUT)
        return _run_sync(bridge.solve_challenge(), timeout_ms / 1000 + 10, cancellable=True)
    except Exception as e:
        logger.error(f"solve_turnstile_challenge_sync failed: {e}")
        return None


def _cleanup() -> None:
    """
    Çıkışta süreçteki HER köprünün tarayıcısını kapatır (c projesinin düzeltmesi, A5). Eskiden yalnızca indirmelerin
    köprüsü (`get_instance`) kapanırdı, o da yoksa çıkış onu kurardı; canlı kaynakların köprüleri
    (sofascore_scraper/services/live/push_source.py, direct_source.py) ve araştırma betiklerinin köprüleri açık kalır,
    öksüz Chrome profili kilitli tutardı. Kapanış en iyi çabadır: köprü başına CLOSE_TIMEOUT.
    """
    loop = _loop
    if loop is None or not loop.is_running():
        return
    for bridge in list(_bridges):
        if getattr(bridge, "session", None) is None and getattr(bridge, "page", None) is None:
            continue
        try:
            # Chrome'un kapanmasına zaman tanı; yoksa süreç öksüz kalır
            asyncio.run_coroutine_threadsafe(bridge.close(), loop).result(timeout=CLOSE_TIMEOUT)
        except Exception:
            pass
    try:
        loop.call_soon_threadsafe(loop.stop)
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
