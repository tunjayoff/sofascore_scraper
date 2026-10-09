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
import concurrent.futures
import contextvars
import hashlib
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from sofascore_scraper import bridge_health, throttle
from sofascore_scraper.client import profile_lock
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
_JS_FETCH_TIMEOUT_MS = 20000
# Başlatma başarısız olduysa her 403'te yeniden denenmez
_LAUNCH_RETRY_AFTER = 300.0
# Yeni çözülmüş bir token, hemen ardından gelen 403 dalgası için yeniden kullanılır
_FRESH_TOKEN_SECONDS = 30.0
# Başarısız bir çözümden sonra bu süre yeniden denenmez (her istek 10-90 sn yakmasın)
_SOLVE_RETRY_AFTER = 180.0

# Tarayıcının HTTP önbelleği: durum taşıyan yanıtlar (maç, canlı liste, sezon listeleri) max-age boyunca
# (sezon listelerinde 60 sn) önbellekten dönerse biten maç o süre "devam ediyor" görünür (issue #6).
# Yalnızca maçın durumundan bağımsız statik veri önbelleği kullanır.
_STATIC_PATH_RE = re.compile(r"/(seasons|rounds)/?$")


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


class BrowserBridge:
    """
    Scrapling StealthySession üzerinden Sofascore API köprüsü.
    Tek bir sayfayı açık tutar, API isteklerini o sayfadan fetch() ile yapar ve 403
    challenge geldiğinde tek bir çözümü tüm bekleyen isteklerle paylaşır.
    """

    _instance: Optional["BrowserBridge"] = None

    def __init__(self, profile_dir: str = DEFAULT_PROFILE_DIR, home_url: Optional[str] = None, *,
                 report_health: bool = True):
        """
        home_url       köprü sekmesinin açıldığı ve sayfa SofaScore dışına çıkınca döndüğü adres; None: HOME_URL.
                       Canlı sayfaların ayrı köprüsü (sofascore_scraper/services/live/push_source.py) API çağırmayan bir sayfa verir.
        report_health  başlatma hataları ve isteklerin sonucu süreç genelindeki köprü sağlığına (sofascore_scraper/bridge_health.py)
                       yazılır. Canlı kaynakların köprüleri False verir: onların tarayıcısı başlamazsa izleme sürecinin
                       köprü sağlığı "bozuk" görünmesin, indirmelerin köprüsünün durumu o değildir (FX-15).
        """
        self.profile_dir = profile_dir
        self.home_url = home_url
        self.report_health = report_health
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

    @classmethod
    def get_instance(cls, profile_dir: str = DEFAULT_PROFILE_DIR) -> "BrowserBridge":
        if cls._instance is None:
            cls._instance = cls(profile_dir=profile_dir)
        return cls._instance

    def _home(self) -> str:
        return getattr(self, "home_url", None) or HOME_URL

    async def ensure_ready(self) -> None:
        """Tarayıcı oturumunun hazır ve açık olduğundan emin olur."""
        if self.page and not self.page.is_closed():
            return

        async with self._init_lock:
            if self.page and not self.page.is_closed():
                return
            if self._launch_failed_at and time.time() - self._launch_failed_at < _LAUNCH_RETRY_AFTER:
                if self.report_health:
                    bridge_health.record_failure(bridge_health.KIND_BROWSER,
                                                 "the browser could not start (waiting before the next try)")
                raise RuntimeError(
                    "The browser bridge could not start (tried recently). "
                    "Run `ssc doctor` to see why "
                    "(browser install: `python -m patchright install chromium --no-shell`)"
                )
            # Sayfası kapanmış eski oturum: yeniden başlatmadan önce kapat (profil kilidi)
            await self.close()
            try:
                await self._launch_in_free_profile()
            except BaseException as e:
                # Yarım kalan başlatma (hata veya iptal) tarayıcı sürecini ve profil kilidini bırakmasın
                self._launch_failed_at = time.time()
                await self.close()
                if isinstance(e, Exception) and self.report_health:  # iptal bir sağlık sinyali değil
                    bridge_health.record_failure(bridge_health.KIND_BROWSER, f"{e.__class__.__name__}: {e}")
                raise
            self._launch_failed_at = 0.0

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

    async def _launch(self) -> None:
        AsyncStealthySession = _session_class()
        headless = _headless()
        logger.info(f"Starting the browser session (Scrapling StealthySession, headless={headless})")
        self.session = AsyncStealthySession(
            headless=headless,
            solve_cloudflare=True,
            user_data_dir=self._launch_dir or self.profile_dir,
            proxy=_proxy_settings(),
            block_webrtc=True,
            timeout=60000,
            max_pages=2,
        )
        await self.session.start()
        # Scrapling'in oturum bağlamı: kendi sayfamızı bunun içinde açıp tekrar tekrar kullanırız
        self.context = getattr(self.session, "context", None)
        if self.context is None:
            raise RuntimeError("The Scrapling session opened no browser context (version mismatch?)")

        # Profilde çözüm yoksa challenge'ı API isteklerinden önce çöz
        solved_now = None
        if not await self._token_from_context():
            solved_now = await self._solve_on_captcha_page()

        self.page = await self.context.new_page()
        try:
            await self.page.goto(self._home(), wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"Opening the SofaScore home page: {e}")
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
            cookies = await self.context.cookies("https://www.sofascore.com")
        except Exception:
            return None
        return next((c["value"] for c in cookies if c.get("name") == "sofa_captcha" and c.get("value")), None)

    async def _solve_on_captcha_page(self) -> Optional[str]:
        """captcha.html'deki gömülü Turnstile'ı Scrapling'e çözdürür; token'ı (varsa) döndürür."""
        before = await self._token_from_context()
        try:
            await asyncio.wait_for(
                # network_idle kapalı: captcha sayfası ana sayfaya yönlenirse reklam/websocket trafiği
                # ağı hiç boşaltmaz ve çözüm Scrapling'in tüm zaman aşımını bekler (ölçüm: 65 sn).
                # Çözücü kendi içinde kısa bir networkidle beklemesi yapıyor.
                self.session.fetch(CAPTCHA_URL, solve_cloudflare=True, network_idle=False, google_search=False),
                SOLVE_TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"Solving on captcha.html failed: {e.__class__.__name__}: {e}")
        # Cookie, widget geçtikten kısa süre sonra yazılır
        for _ in range(20):
            token = await self._token_from_context()
            if token and token != before:
                return token
            await asyncio.sleep(0.5)
        # Değer değişmemiş olabilir (sunucu aynı cookie'yi geçerli saydı): başarısız sayma — asıl karar
        # isteğin yeniden denenmesinde. Aksi halde _SOLVE_RETRY_AFTER boyunca hiç çözüm yapılmazdı.
        return await self._token_from_context()

    def _set_token(self, token: str) -> None:
        global _cached_token, _cached_at
        self.token = token
        self._token_at = time.time()
        # curl_cffi istekleri de bu tokeni kullansın (get_request_headers → get_cached_token)
        _cached_token = token
        _cached_at = self._token_at

    async def solve_challenge(self) -> Optional[str]:
        """
        Turnstile challenge'ını çözer ve geçerli token'ı döndürür.

        Aynı anda gelen 403'lerin hepsi aynı çözümü bekler. Az önce çözülmüş bir token
        doğrudan döndürülür; başarısız bir çözümden sonra bir süre yeniden denenmez.
        """
        await self.ensure_ready()
        if self.token and time.time() - self._token_at < _FRESH_TOKEN_SECONDS:
            return self.token
        if self._solve_failed_at and time.time() - self._solve_failed_at < _SOLVE_RETRY_AFTER:
            return None
        if self._solve_task is None or self._solve_task.done():
            # Çözümün (doğrulama isteğinin) sıra beklemesi kendi sayacına yazılır: görev, bağlamı
            # oluşturulduğu anda kopyalar
            solve_wait = _SlotWait(shared=True)
            token = _slot_wait.set(solve_wait)
            try:
                self._solve_task = asyncio.ensure_future(self._solve_challenge())
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
        ana sayfaya geri dönülür.
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
                    await self.ensure_ready()
                    continue
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                    if not self.page.url.startswith("https://www.sofascore.com"):
                        await self.page.goto(self._home(), wait_until="domcontentloaded", timeout=30000)
                except Exception as le:
                    logger.warning(f"The bridge page could not recover: {le}")

    async def _api_fetch(self, url: str, cache_mode: str) -> Dict[str, Any]:
        """
        Köprüden çıkan tek API isteği. Tarayıcıdan giden her istek buradan geçer: önce süreçler
        arası ortak bütçeden sıra alınır (sofascore_scraper/throttle.py), sonra sayfada fetch() çalışır.
        Sıra bekleme, çağrının zaman aşımından sayılmaz (_SlotWait).
        """
        await _wait_for_slot()
        x_req = hashlib.sha256(str(int(time.time()) // 1800).encode("utf-8")).hexdigest()[:6]
        return await self.evaluate(_FETCH_JS, [url, x_req, self.token, _JS_FETCH_TIMEOUT_MS, cache_mode])

    async def _api_unlocked(self) -> bool:
        """Çözümden sonra API gerçekten açıldı mı? (Sayfa henüz yoksa doğrulanamaz: evet say.)"""
        if self.page is None or self.page.is_closed():
            return True
        res = await self._api_fetch(_PROBE_URL or _api_url(_PROBE_PATH), "no-store")
        return res.get("status") != 403

    async def _solve_challenge(self) -> Optional[str]:
        logger.info("Solving the Cloudflare Turnstile challenge (captcha.html)")
        token = await self._solve_on_captcha_page()
        if token:
            self._set_token(token)
        if token and not await self._api_unlocked():
            # Profildeki süresi geçmiş cookie: captcha.html challenge göstermeden yönlenir ve yeni
            # cookie üretilmez. Cookie silinip challenge baştan çözülür.
            logger.info("The sofa_captcha cookie is still refused; deleting it and solving the challenge again")
            await self.context.clear_cookies(name="sofa_captcha")
            token = await self._solve_on_captcha_page()
            if token:
                self._set_token(token)
            if token and not await self._api_unlocked():
                token = None
        if token:
            self._set_token(token)
            self._solve_failed_at = 0.0
            logger.info("Turnstile challenge solved.")
            return token
        self._solve_failed_at = time.time()
        logger.warning(
            f"The Turnstile challenge could not be solved; not trying again for {int(_SOLVE_RETRY_AFTER)} s."
        )
        return None

    async def fetch_json(self, path_or_url: str) -> Optional[Any]:
        """
        Sofascore API isteğini tarayıcı oturumu içinden fetch() ile yapar.
        403 challenge durumunda Turnstile'ı otomatik çözüp isteği yineler.
        """
        await self.ensure_ready()

        url = path_or_url if path_or_url.startswith("http") else _api_url(path_or_url)

        res = await self._api_fetch(url, cache_mode_for(url))

        # 403 Challenge alındıysa otomatik çöz ve tekrar dene
        if res.get("status") == 403 and "challenge" in (res.get("text") or ""):
            logger.info("The API answered 403 with a challenge; solving Turnstile")
            new_token = await self.solve_challenge()
            if new_token:
                res = await self._api_fetch(url, cache_mode_for(url))

        # Sağlık sinyali (sofascore_scraper/bridge_health.py): isteğin SON hali sayılır — çözülüp yinelenen 403 başarıdır
        if res.get("ok"):
            bridge_health.record_success()
            return res.get("data")

        if res.get("status") == 404:
            bridge_health.record_success()  # API yanıt verdi; kaynak yok
            logger.debug(f"Not found (404): {url}")
            return {"__404__": True}

        if res.get("status") == 403:
            text = res.get("text") or ""
            kind = bridge_health.KIND_CHALLENGE if "challenge" in text else bridge_health.KIND_FORBIDDEN
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


import atexit


def _cleanup():
    global _loop
    if _loop and _loop.is_running():
        try:
            bridge = BrowserBridge.get_instance()
            fut = asyncio.run_coroutine_threadsafe(bridge.close(), _loop)
            # Chrome'un kapanmasına zaman tanı; yoksa süreç öksüz kalır
            fut.result(timeout=10.0)
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
