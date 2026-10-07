"""
BrowserBridge'in gerçek tarayıcı yolu — SofaScore'a hiç dokunmadan.

Gerçek olan: Scrapling `AsyncStealthySession` + patchright + Chromium başlatılır, köprü kendi
`_launch` / `_solve_on_captcha_page` / `fetch_json` / `evaluate` kodunu çalıştırır ve API istekleri
gerçek bir sayfanın içinden `fetch()` ile yapılır (başlıklar, önbellek modu, zaman aşımı, 403 →
çözüm → yineleme, sayfa yönlenmesi, kapanan sayfa).

Sahte olan: SofaScore'un kendisi. Köprünün üç adres sabiti (ana sayfa, captcha.html, yoklama uç
noktası) 127.0.0.1'deki küçük bir HTTP sunucusuna çevrilir; "challenge çözümü" bu sunucunun
captcha.html'de verdiği cookie'dir. Gerçek Cloudflare Turnstile burada DENENMEZ — onun testi
canlıdır: `pytest -m "live and browser" tests/test_live_bridge.py`.

Emniyet: tarayıcının proxy'si de aynı sahte sunucudur. Başka bir adrese giden her istek (bir
sabit yanlış çevrilmiş olsa bile) sunucuda biter, kaydedilir ve dışarı çıkmaz; modül sonunda
hiçbir isteğin sofascore.com'a yönelmediği doğrulanır.

Çalıştırma (Chromium gerekir: `python -m playwright install chromium`):

    python -m pytest -m "browser and not live" tests/test_bridge_offline_browser.py
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

import sofascore_scraper.challenge_solver as cs
from sofascore_scraper import bridge_health

pytestmark = pytest.mark.browser

CHALLENGE_BODY = {"error": {"code": 403, "reason": "challenge"}}


class FakeSite:
    """SofaScore'un yerine geçen yerel sunucunun durumu ve gördüğü istekler."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.origin = ""
        self.netloc = ""
        self.hits: dict[str, int] = {}
        self.last_headers: dict[str, dict[str, str]] = {}
        self.blocked: list[str] = []  # sahte sunucudan başka bir yere gitmek isteyen istekler
        self.issued = 0
        self.accepted: set[str] = set()
        self.captcha_effective = True
        self.slow_started = threading.Event()
        self.slow_release = threading.Event()
        self.hang_release = threading.Event()

    def url(self, path: str) -> str:
        return self.origin + path

    def count(self, path: str) -> int:
        with self.lock:
            return self.hits.get(path, 0)

    def revoke_tokens(self) -> None:
        """Sunucu artık verdiği hiçbir cookie'yi kabul etmiyor (süresi dolmuş çözüm)."""
        with self.lock:
            self.accepted.clear()

    def reset(self) -> None:
        with self.lock:
            self.captcha_effective = True
        self.slow_started.clear()
        self.slow_release.clear()
        self.hang_release.clear()


def _cookie(headers: dict[str, str], name: str) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    site: FakeSite

    def log_message(self, *args) -> None:  # test çıktısını kirletmesin
        pass

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:  # tarayıcı bağlantıyı kapattı (iptal edilen fetch)
            pass

    def _json(self, status: int, data, extra: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json", extra)

    def _html(self, title: str, extra: dict[str, str] | None = None) -> None:
        page = f"<!doctype html><html><head><title>{title}</title></head><body><p>{title}</p></body></html>"
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", extra)

    def do_CONNECT(self) -> None:
        # Tarayıcı proxy üzerinden https bir adrese gitmek istiyor: asla tünel açılmaz
        with self.site.lock:
            self.site.blocked.append(self.path)
        self._send(403, b"offline test: no outbound connections", "text/plain", {"Connection": "close"})
        self.close_connection = True

    def do_GET(self) -> None:
        site = self.site
        target = self.path
        if "://" in target:  # proxy biçimi: GET http://host:port/path
            parts = urlsplit(target)
            if parts.netloc != site.netloc:
                with site.lock:
                    site.blocked.append(target)
                self._send(502, b"offline test: no outbound connections", "text/plain")
                return
            target = parts.path + (f"?{parts.query}" if parts.query else "")
        path = target.split("?", 1)[0]
        headers = {k.lower(): v for k, v in self.headers.items()}
        with site.lock:
            site.hits[path] = hit = site.hits.get(path, 0) + 1
            site.last_headers[path] = headers
            token_ok = _cookie(headers, "sofa_captcha") in site.accepted

        if path == "/tr":
            self._html("fake home")
        elif path == "/captcha.html":
            with site.lock:
                site.issued += 1
                token = f"token-{site.issued}"
                if site.captcha_effective:
                    site.accepted.add(token)
            self._html("fake captcha", {"Set-Cookie": f"sofa_captcha={token}; Path=/; Max-Age=3600"})
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        elif path == "/api/v1/echo":
            self._json(200, {"hit": hit, "headers": headers})
        elif path in ("/api/v1/unique-tournament/1/seasons", "/api/v1/event/1"):
            # Sunucu ikisine de "60 sn önbellekte tut" der; köprü yalnızca statik olanı önbellekten okumalı
            self._json(200, {"hit": hit}, {"Cache-Control": "public, max-age=60"})
        elif path == "/api/v1/missing":
            self._json(404, {"error": {"code": 404, "message": "Not Found"}})
        elif path == "/api/v1/forbidden":
            self._json(403, {"error": {"code": 403, "reason": "Forbidden"}})
        elif path in ("/api/v1/guarded", "/api/v1/probe"):
            if token_ok:
                self._json(200, {"hit": hit, "guarded": True})
            else:
                self._json(403, CHALLENGE_BODY)
        elif path == "/api/v1/not-json":
            self._send(200, b"<html>maintenance</html>", "text/html")
        elif path == "/api/v1/hang":
            site.hang_release.wait(20)
            self._json(200, {"late": True})
        elif path == "/api/v1/slow-once":
            if hit == 1:
                site.slow_started.set()
                site.slow_release.wait(20)
            self._json(200, {"hit": hit})
        else:
            self._json(404, {"error": {"code": 404, "message": "unknown test route"}})


class _LocalCookieJar:
    """`context.cookies("https://www.sofascore.com")` çağrısını yerel sunucunun adresine çevirir."""

    def __init__(self, context, origin: str) -> None:
        self._context = context
        self._origin = origin

    async def cookies(self, _url):
        return await self._context.cookies(self._origin)


class LocalBridge(cs.BrowserBridge):
    """
    Uygulama kodundan tek sapma: `_token_from_context` cookie'yi sabit yazılmış
    "https://www.sofascore.com" adresi için sorar; burada aynı kod yerel adres için çalışır.
    """

    cookie_origin = ""

    async def _token_from_context(self):
        real = self.context
        self.context = _LocalCookieJar(real, self.cookie_origin)
        try:
            return await super()._token_from_context()
        finally:
            self.context = real


def run(coro, timeout: float = 90.0):
    """Köprünün kendi arka plan döngüsünde çalıştırır (uygulamadaki senkron yol ile aynı)."""
    return cs._run_sync(coro, timeout)


@pytest.fixture(scope="module")
def site():
    state = FakeSite()
    handler = type("Handler", (_Handler,), {"site": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    state.netloc = f"127.0.0.1:{server.server_address[1]}"
    state.origin = f"http://{state.netloc}"
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="FakeSofaScore")
    thread.start()
    try:
        yield state
    finally:
        state.slow_release.set()
        state.hang_release.set()
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def bridge(site, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(cs, "HOME_URL", site.url("/tr"))
    mp.setattr(cs, "CAPTCHA_URL", site.url("/captcha.html"))
    mp.setattr(cs, "_PROBE_URL", site.url("/api/v1/probe"))
    # Tarayıcının tüm trafiği sahte sunucudan geçer (bkz. modül açıklaması: emniyet)
    mp.setenv("USE_PROXY", "true")
    mp.setenv("PROXY_URL", site.origin)
    mp.delenv("SOFASCORE_BROWSER_HEADED", raising=False)
    # Köprü çözdüğü token'ı modül düzeyinde de saklar; diğer testlere sızmasın
    mp.setattr(cs, "_cached_token", None)
    mp.setattr(cs, "_cached_at", 0)

    LocalBridge.cookie_origin = site.origin
    instance = LocalBridge(profile_dir=str(tmp_path_factory.mktemp("bridge-profile")))
    mp.setattr(cs.BrowserBridge, "_instance", instance)
    try:
        run(instance.ensure_ready(), cs.STARTUP_TIMEOUT)
        yield instance
    finally:
        try:
            run(instance.close(), 30)
        finally:
            mp.undo()
    reached = [target for target in site.blocked if "sofascore" in target.lower()]
    assert reached == [], f"test sırasında SofaScore'a istek denendi: {reached}"


@pytest.fixture(autouse=True)
def _fresh_state(bridge, site):
    """Her senaryo aynı noktadan başlar: çözüm 'taze' sayılmaz, başarısız çözüm beklemesi yoktur."""
    site.reset()
    bridge._token_at = 0.0
    bridge._solve_failed_at = 0.0
    yield
    site.slow_release.set()
    site.hang_release.set()


# --- başlatma ----------------------------------------------------------------------------------

def test_launch_solves_on_the_captcha_page_then_opens_the_home_page(bridge, site):
    """Boş profil: önce captcha.html (cookie buradan gelir), sonra API isteklerinin yapılacağı ana sayfa."""
    assert site.count("/captcha.html") == 1
    assert site.count("/tr") >= 1
    assert bridge.token == "token-1"
    assert bridge.context is not None
    assert bridge.page.url == site.url("/tr")
    assert not bridge.page.is_closed()


# --- istek yolu ------------------------------------------------------------------------------

def test_fetch_json_runs_inside_the_page_with_the_expected_headers(bridge, site):
    data = run(bridge.fetch_json(site.url("/api/v1/echo")))

    seen = data["headers"]
    assert re.fullmatch(r"[0-9a-f]{6}", seen["x-requested-with"])
    assert seen["x-captcha"] == bridge.token
    assert _cookie(seen, "sofa_captcha") == bridge.token  # sayfa içi fetch cookie'yi kendisi gönderir
    assert seen["referer"].startswith(site.origin)  # istek ana sayfanın bağlamından çıktı
    assert "HeadlessChrome" not in seen["user-agent"]
    snap = bridge_health.snapshot()
    assert snap["state"] == bridge_health.OK
    assert snap["last_success_at"] is not None


def test_sync_and_async_entry_points_reach_the_same_bridge(bridge, site):
    before = site.count("/api/v1/echo")

    sync_data = cs.fetch_api_via_browser_sync(site.url("/api/v1/echo"))
    async_data = asyncio.run(cs.fetch_api_via_browser(site.url("/api/v1/echo")))

    assert sync_data["hit"] == before + 1
    assert async_data["hit"] == before + 2
    assert site.count("/captcha.html") == 1  # ikinci bir tarayıcı / çözüm başlamadı


def test_stateful_endpoints_bypass_the_browser_http_cache(bridge, site):
    """issue #6: maç yanıtı max-age boyunca önbellekten dönerse biten maç 'devam ediyor' görünür."""
    first = run(bridge.fetch_json(site.url("/api/v1/event/1")))
    second = run(bridge.fetch_json(site.url("/api/v1/event/1")))

    assert (first["hit"], second["hit"]) == (1, 2)
    assert site.count("/api/v1/event/1") == 2


def test_static_endpoints_may_use_the_browser_http_cache(bridge, site):
    first = run(bridge.fetch_json(site.url("/api/v1/unique-tournament/1/seasons")))
    second = run(bridge.fetch_json(site.url("/api/v1/unique-tournament/1/seasons")))

    assert (first["hit"], second["hit"]) == (1, 1)
    assert site.count("/api/v1/unique-tournament/1/seasons") == 1


def test_404_is_reported_as_missing_resource_not_as_failure(bridge, site):
    assert run(bridge.fetch_json(site.url("/api/v1/missing"))) == {"__404__": True}
    assert bridge_health.snapshot()["consecutive_failures"] == 0


def test_plain_403_is_a_failure_without_a_solve_attempt(bridge, site):
    assert run(bridge.fetch_json(site.url("/api/v1/forbidden"))) is None

    assert site.count("/captcha.html") == 1
    snap = bridge_health.snapshot()
    assert snap["consecutive_failures"] == 1
    assert snap["last_error"]["kind"] == bridge_health.KIND_FORBIDDEN


def test_non_json_200_is_a_failed_fetch_not_a_crash(bridge, site):
    assert run(bridge.fetch_json(site.url("/api/v1/not-json"))) is None


def test_hanging_request_is_aborted_by_the_in_page_timeout(bridge, site, monkeypatch):
    monkeypatch.setattr(cs, "_JS_FETCH_TIMEOUT_MS", 400)

    started = time.monotonic()
    result = run(bridge.fetch_json(site.url("/api/v1/hang")))
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 10, f"sayfa içi zaman aşımı çalışmadı ({elapsed:.1f} sn)"
    # Köprü hâlâ kullanılabilir
    site.hang_release.set()
    assert run(bridge.fetch_json(site.url("/api/v1/echo")))["hit"] >= 1


# --- challenge ---------------------------------------------------------------------------------

def test_challenge_is_solved_once_and_the_request_retried(bridge, site):
    site.revoke_tokens()
    old_token = bridge.token
    solves = site.count("/captcha.html")

    data = run(bridge.fetch_json(site.url("/api/v1/guarded")))

    assert data == {"hit": 2, "guarded": True}  # 1: 403 challenge, 2: çözümden sonra
    assert site.count("/captcha.html") == solves + 1
    assert bridge.token != old_token
    assert site.last_headers["/api/v1/guarded"]["x-captcha"] == bridge.token
    assert site.count("/api/v1/probe") >= 1  # çözümün API'yi gerçekten açtığı yoklandı
    assert bridge_health.snapshot()["consecutive_failures"] == 0


def test_fresh_token_is_reused_instead_of_solving_again(bridge, site):
    """Az önce çözülmüş token, hemen ardından gelen 403'ler için yeniden kullanılır (yeni çözüm yok)."""
    site.revoke_tokens()
    bridge._token_at = time.time()
    solves = site.count("/captcha.html")

    assert run(bridge.fetch_json(site.url("/api/v1/guarded"))) is None

    assert site.count("/captcha.html") == solves
    assert bridge_health.snapshot()["last_error"]["kind"] == bridge_health.KIND_CHALLENGE


def test_unsolvable_challenge_fails_and_is_not_retried_for_a_while(bridge, site):
    """Çözüm cookie üretiyor ama sunucu hâlâ reddediyor: cookie silinip bir kez daha denenir, sonra beklenir."""
    site.revoke_tokens()
    site.captcha_effective = False
    solves = site.count("/captcha.html")

    assert run(bridge.fetch_json(site.url("/api/v1/guarded")), 120) is None

    assert site.count("/captcha.html") == solves + 2
    assert bridge._solve_failed_at > 0
    assert bridge_health.snapshot()["last_error"]["kind"] == bridge_health.KIND_CHALLENGE

    # Bekleme süresi içinde yeni çözüm denenmez: istek hemen başarısız döner
    started = time.monotonic()
    assert run(bridge.fetch_json(site.url("/api/v1/guarded"))) is None
    assert site.count("/captcha.html") == solves + 2
    assert time.monotonic() - started < 10


# --- sayfa toparlanması ------------------------------------------------------------------------

def test_fetch_survives_a_page_navigation_mid_request(bridge, site):
    """SofaScore ana sayfası kendi kendine yeniden yüklenebilir: süren fetch düşer, köprü yineleyip sonucu alır."""

    async def scenario():
        task = asyncio.ensure_future(bridge.fetch_json(site.url("/api/v1/slow-once")))
        for _ in range(200):
            if site.slow_started.is_set():
                break
            await asyncio.sleep(0.05)
        assert site.slow_started.is_set(), "istek sunucuya ulaşmadı"
        await bridge.page.reload(wait_until="domcontentloaded")
        site.slow_release.set()
        return await task

    assert run(scenario()) == {"hit": 2}
    assert bridge.page.url == site.url("/tr")


def test_closed_page_relaunches_with_the_solution_kept_in_the_profile(bridge, site):
    site.revoke_tokens()
    run(bridge.fetch_json(site.url("/api/v1/guarded")))  # geçerli bir çözüm profilde olsun
    token = bridge.token
    solves = site.count("/captcha.html")
    run(bridge.page.close())

    data = run(bridge.fetch_json(site.url("/api/v1/guarded")), 180)

    assert data["guarded"] is True
    assert not bridge.page.is_closed()
    assert bridge.token == token  # cookie kalıcı profilden geldi
    assert site.count("/captcha.html") == solves  # yeniden başlatma yeni çözüm gerektirmedi
    assert bridge._token_at == 0.0  # profilden gelen token "az önce çözüldü" sayılmaz
