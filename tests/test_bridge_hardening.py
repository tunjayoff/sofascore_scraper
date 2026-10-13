"""
Tarayıcı köprüsünün (sofascore_scraper/client/bridge.py) c projesinden geri taşınan üretim düzeltmeleri (S1).

c projesi (sofascore_site/bridge.py) köprüyü buradan taşıdı ve üretim benzeri koşularda (1-5) düzeltti; testleri
(tests/test_sofascore_bridge.py) buraya köprünün kendi arayüzüyle taşındı. Tarayıcı yok: Scrapling oturumu, bağlamı
ve sayfa sahtedir; SofaScore'a hiçbir istek gitmez.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from sofascore_scraper.client import bridge as cs

# conftest `browser` işaretsiz testlerde `_launch`'ı gerçek tarayıcı açmasın diye değiştirir; burada tarayıcı
# sahte olduğu için gerçek `_launch` kodu çalışır (toplama anında alınan asıl işlev)
_REAL_LAUNCH = cs.BrowserBridge._launch

CHALLENGE = '{"error": {"code": 403, "reason": "challenge"}}'


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    monkeypatch.setattr(cs, "_cached_token", None)
    monkeypatch.setattr(cs, "_cached_at", 0.0)
    monkeypatch.setattr(cs.BrowserBridge, "_instance", None)


@pytest.fixture
def real_launch(monkeypatch):
    monkeypatch.setattr(cs.BrowserBridge, "_launch", _REAL_LAUNCH)


def _jwt(exp: float) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64({'exp': int(exp)})}.sig"


def _page(*answers: Any, url: str = cs.HOME_URL) -> MagicMock:
    page = MagicMock()
    page.is_closed.return_value = False
    page.url = url
    page.evaluate = AsyncMock(side_effect=list(answers))
    page.wait_for_load_state = AsyncMock()

    def goto(to, **_):
        page.url = to

    page.goto = AsyncMock(side_effect=goto)
    page.close = AsyncMock()
    return page


def _fake_session(monkeypatch, cookies_seq: List[Any], page: Optional[MagicMock] = None):
    """
    Scrapling'in AsyncStealthySession'ının sahtesi: argümanlarını kaydeder; köprü sayfası about:blank'te açılır ve
    fetch'leri 200 döner. `cookies_seq`: bağlamın sırayla verdiği cookie listeleri.
    """
    made: List[dict] = []
    page = page if page is not None else _page(url="about:blank")
    if not isinstance(page.evaluate.side_effect, list) or not page.evaluate.side_effect:
        page.evaluate = AsyncMock(return_value={"status": 200, "ok": True, "data": {}, "text": None})
    context = MagicMock()
    context.cookies = AsyncMock(side_effect=list(cookies_seq))
    context.new_page = AsyncMock(return_value=page)
    context.clear_cookies = AsyncMock()

    class Session:
        def __init__(self, **kw: Any) -> None:
            made.append(kw)
            self.context = None
            self.fetch = AsyncMock()
            self.close = AsyncMock()

        async def start(self) -> None:
            self.context = context

    monkeypatch.setattr(cs, "_session_class", lambda: Session)
    return made, page


def _bridge(tmp_path, **kw: Any) -> cs.BrowserBridge:
    return cs.BrowserBridge(profile_dir=str(tmp_path / "profile"), **kw)


def _run(coro):
    return asyncio.run(coro)


# --- A7: Chromium'un kendi sandbox'ı (c'nin K18'i, 15bb37f) -------------------------------------------


def _profile_cookie() -> List[dict]:
    return [{"name": "sofa_captcha", "value": _jwt(time.time() + 3600)}]


def test_the_launch_arguments_are_unchanged_without_the_sandbox(tmp_path, monkeypatch, real_launch):
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    b = _bridge(tmp_path)
    _run(b.ensure_ready())
    (kw,) = made
    assert "additional_args" not in kw
    assert kw == {
        "headless": True, "solve_cloudflare": True, "user_data_dir": b.profile_dir, "proxy": None,
        "block_webrtc": True, "timeout": 60000, "max_pages": 2,
        "retries": 1,  # B9: yükleme başına tek gezinme
    }


def test_the_sandbox_is_one_launch_option_more_and_nothing_else(tmp_path, monkeypatch, real_launch):
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    b = _bridge(tmp_path, sandbox=True)
    _run(b.ensure_ready())
    (kw,) = made
    assert kw.pop("additional_args") == {"chromium_sandbox": True}
    assert kw == {
        "headless": True, "solve_cloudflare": True, "user_data_dir": b.profile_dir, "proxy": None,
        "block_webrtc": True, "timeout": 60000, "max_pages": 2,
        "retries": 1,  # B9: yükleme başına tek gezinme
    }


def test_the_sandbox_setting_is_read_at_launch_and_off_by_default(tmp_path, monkeypatch, real_launch):
    from sofascore_scraper.config import loader

    assert loader.active_settings().client.browser_sandbox is False
    monkeypatch.setenv("SOFASCORE_CLIENT__BROWSER_SANDBOX", "true")
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    _run(_bridge(tmp_path).ensure_ready())
    assert made[0]["additional_args"] == {"chromium_sandbox": True}
    # Açıkça verilen değer ayarın önüne geçer
    made, _ = _fake_session(monkeypatch, [_profile_cookie()] * 4)
    _run(_bridge(tmp_path, sandbox=False).ensure_ready())
    assert "additional_args" not in made[0]


# --- A5: çıkış her köprüyü kapatır -------------------------------------------------------------------


def test_exit_closes_every_bridge_with_a_browser_and_creates_none(tmp_path, monkeypatch):
    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(cs, "_loop", loop)
    try:
        downloads = _bridge(tmp_path)
        live = cs.BrowserBridge(profile_dir=str(tmp_path / "live"), home_url="https://x/robots.txt",
                                report_health=False)
        idle = cs.BrowserBridge(profile_dir=str(tmp_path / "idle"))  # tarayıcısı hiç açılmadı
        sessions = []
        for b in (downloads, live):
            b.session, b.page = MagicMock(), _page()
            b.session.close = AsyncMock()
            sessions.append(b.session)
        cs._cleanup()
        thread.join(5)
        assert not thread.is_alive()  # döngü durdu
        assert all(s.close.await_count == 1 for s in sessions)
        assert downloads.session is None and live.session is None and idle.session is None
        assert cs.BrowserBridge._instance is None  # çıkış köprü kurmaz
    finally:
        if not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(5)
            loop.close()


def test_exit_without_a_running_loop_does_nothing(monkeypatch):
    monkeypatch.setattr(cs, "_loop", None)
    cs._cleanup()


# --- A1: çözüm sayaçları (c: 71c3fad, e8be49b, 19991ad) ------------------------------------------------


def _solver(tmp_path, **kw: Any) -> cs.BrowserBridge:
    b = _bridge(tmp_path, **kw)
    b.ensure_ready = AsyncMock()
    return b


def test_solves_and_their_failures_are_counted_and_published(tmp_path, caplog):
    from sofascore_scraper import bridge_health, diagnostics

    b = _solver(tmp_path)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    with caplog.at_level("WARNING"):
        assert _run(b._solve_challenge()) is None
        assert _run(b._solve_challenge()) is None
    assert (b.solves, b.solve_failures, b.consecutive_solve_failures) == (2, 2, 2)
    assert "(2 in a row)" in caplog.text
    assert bridge_health.solver()["solves"] == 2
    assert bridge_health.solver()["consecutive_solve_failures"] == 2
    b._solve_on_captcha_page = AsyncMock(return_value=_jwt(time.time() + 3600))  # sayfa yok: yoklanmaz
    assert _run(b._solve_challenge()) is not None
    assert (b.solves, b.solve_failures, b.consecutive_solve_failures) == (3, 2, 0)
    solver = diagnostics._bridge()["solver"]
    assert (solver["solves"], solver["solve_failures"], solver["consecutive_solve_failures"]) == (3, 2, 0)
    # Durum görüntüsünün alanları değişmez (/health, /api/v1/status)
    assert "solver" not in bridge_health.snapshot() and "solves" not in bridge_health.public_snapshot()


def test_a_live_sources_bridge_does_not_publish_its_solves(tmp_path):
    from sofascore_scraper import bridge_health

    b = _solver(tmp_path, report_health=False)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    assert _run(b._solve_challenge()) is None
    assert b.solves == 1 and bridge_health.solver()["solves"] == 0


def test_a_launch_solve_is_counted_as_any(tmp_path, monkeypatch, real_launch):
    _fake_session(monkeypatch, [[], [], [], [], []])
    b = _bridge(tmp_path)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    _run(b.ensure_ready())
    assert (b.solves, b.solve_failures, b.consecutive_solve_failures) == (1, 1, 1)


def test_a_bridge_made_without_init_has_the_counters():
    b = cs.BrowserBridge.__new__(cs.BrowserBridge)
    status = b.solver_status()
    assert (status["solves"], status["solve_failures"], status["consecutive_solve_failures"]) == (0, 0, 0)


# --- B2: artan çözüm beklemesi, her yanıt seriyi bitirir (c: 8b0e05d, 5702da6) ------------------------


def _clocked(tmp_path, clock: Clock, **kw: Any) -> cs.BrowserBridge:
    b = cs.BrowserBridge(profile_dir=str(tmp_path / "profile"), clock=clock, **kw)
    b.ensure_ready = AsyncMock()
    return b


def test_the_cooldown_doubles_from_the_third_failed_solve_up_to_600_s(tmp_path, caplog):
    assert [cs.solve_retry_after(n) for n in range(1, 7)] == [180, 180, 360, 600, 600, 600]
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    assert b.solve_cooldown_left() == 0.0

    async def go() -> List[float]:
        waited = []
        for _ in range(4):
            assert await b.solve_challenge() is None
            waited.append(b.solve_cooldown_left())
            clock.t += waited[-1] - 1
            assert await b.solve_challenge() is None  # hâlâ bekleniyor: çözüm yok
            clock.t += 1
            assert b.solve_cooldown_left() == 0.0
        return waited

    with caplog.at_level("WARNING"):
        assert _run(go()) == [180, 180, 360, 600]
    assert b._solve_on_captcha_page.await_count == 4
    assert "(3 in a row); not trying again for 360 s" in caplog.text
    tok = _jwt(time.time() + 3600)
    b._solve_on_captcha_page = AsyncMock(return_value=tok)
    assert _run(b.solve_challenge()) == tok
    assert b.solve_cooldown_left() == 0.0 and b.consecutive_solve_failures == 0
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    clock.t += 31  # taze token'ın 30 sn'si geçti
    assert _run(b.solve_challenge()) is None
    assert b.solve_cooldown_left() == 180  # yeni bir seri 180 sn'den başlar


def test_an_answer_ends_the_run_of_failed_solves_but_not_the_cooldown(tmp_path):
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)

    async def fail() -> float:
        assert await b.solve_challenge() is None
        left = b.solve_cooldown_left()
        clock.t += left
        return left

    assert [_run(fail()) for _ in range(3)] == [180, 180, 360]
    assert _run(b.solve_challenge()) is None and b.consecutive_solve_failures == 4
    b.reset_solve_streak()  # SofaScore yanıt verdi
    assert b.consecutive_solve_failures == 0
    assert b.solve_cooldown_left() == 600  # süren bekleme uzunluğunu korur
    clock.t += 600
    assert _run(fail()) == 180  # sonraki başarısızlık yeni bir seri başlatır
    assert (b.solves, b.solve_failures) == (5, 5)  # toplamlar kalır


def test_a_page_answer_ends_the_streak(tmp_path):
    b = _clocked(tmp_path, Clock())
    b.consecutive_solve_failures = 2
    b.page = _page({"status": 200, "ok": True, "data": {"a": 1}, "text": None},
                   {"status": 404, "ok": False, "data": None, "text": ""})
    assert _run(b.fetch_json("/event/1")) == {"a": 1}
    assert b.consecutive_solve_failures == 0
    b.consecutive_solve_failures = 2
    assert _run(b.fetch_json("/event/2")) == {"__404__": True}
    assert b.consecutive_solve_failures == 0


def test_the_cooldown_left_is_published_for_the_diagnostics(tmp_path):
    from sofascore_scraper import bridge_health

    clock = Clock(time.time())
    b = _clocked(tmp_path, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    assert _run(b.solve_challenge()) is None
    left = bridge_health.solver()["solve_cooldown_left"]
    assert 175 <= left <= 180
    assert bridge_health.solver()["consecutive_solve_failures"] == 1


# --- B3: taze token kısayolundan önce bekleme (c: 68cfba4) ----------------------------------------------


def test_a_token_the_api_refused_is_not_reused_in_the_cooldown(tmp_path):
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b.context = MagicMock()
    b.context.clear_cookies = AsyncMock()
    b._solve_on_captcha_page = AsyncMock(return_value="refused")
    b._api_unlocked = AsyncMock(return_value=False)

    async def go():
        assert await b.solve_challenge() is None
        assert b.token == "refused" and b.solve_cooldown_left() == 180
        clock.t += 5
        assert await b.solve_challenge() is None  # "refused" değil
        assert b._solve_on_captcha_page.await_count == 2  # başarısız çözümün iki denemesi

    _run(go())


def test_a_fresh_token_is_still_reused_outside_a_cooldown(tmp_path):
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b.token, b._token_at = "jwt", clock.t
    b._solve_challenge = AsyncMock(return_value="new")
    clock.t += 29
    assert _run(b.solve_challenge()) == "jwt"
    clock.t += 2
    assert _run(b.solve_challenge()) == "new"
    assert b._solve_challenge.await_count == 1


# --- B1: token'ın reddi (`jwt:` 403) cookie silinerek çözülür (c: c9030fe, 51c23a1) ----------------------

JWT_REFUSED = '{"error": {"code": 403, "reason": "jwt:time-out-of-bounds" }}'
"""c'nin üretim benzeri 4. koşusunda çözümden 3599,4 sn sonra her sayfa isteğinin aldığı gövde, bayt bayt."""


def test_a_rejected_token_needs_a_solve_and_another_403_does_not():
    assert cs.token_refused(JWT_REFUSED)
    assert cs.token_refused('{"error":{"code":403,"reason":"jwt:bad-signature"}}')
    assert not cs.token_refused(CHALLENGE)
    assert not cs.token_refused("forbidden: jwt: none")  # SofaScore'un nedeni değil
    assert cs.needs_solve(403, JWT_REFUSED) and cs.needs_solve(403, CHALLENGE)
    assert not cs.needs_solve(403, "forbidden")
    assert not cs.needs_solve(200, JWT_REFUSED)
    assert cs.token_exp(_jwt(1234)) == 1234.0
    assert cs.token_exp("not-a-jwt") is None and cs.token_exp(None) is None


class TokenPage:
    """Sayfa: süresi dolmamış token'la (köprünün saatine göre) 200, dolmuşla SofaScore'un `jwt:` 403'ü, token'sız challenge."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.sent: List[Any] = []  # (url, token)
        self.page = _page()
        self.page.evaluate = AsyncMock(side_effect=self.evaluate)

    async def evaluate(self, script, arg):
        url, token = arg[0], arg[2]
        self.sent.append((url, token))
        await asyncio.sleep(0)
        if token is None:
            return {"status": 403, "ok": False, "data": None, "text": CHALLENGE}
        if (cs.token_exp(token) or 0) <= self.clock.t:
            return {"status": 403, "ok": False, "data": None, "text": JWT_REFUSED}
        return {"status": 200, "ok": True, "data": {"event": {}}, "text": None}


def _token_bridge(tmp_path, clock: Clock, *, solved=None):
    """TokenPage üzerinde bir köprü; captcha sayfası bir saat geçerli yeni bir token çözer (ya da `solved`'dakileri)."""
    b = _clocked(tmp_path, clock)
    page = TokenPage(clock)
    b.page = page.page
    b.context = MagicMock()
    b.context.clear_cookies = AsyncMock()
    b.context.add_cookies = AsyncMock()
    b.context.cookies = AsyncMock(return_value=[{"name": "sofa_captcha", "value": "x"}])
    tokens = iter(solved) if solved is not None else None

    async def solve_on_captcha_page(*_a, **_k):
        await asyncio.sleep(0)
        if tokens is not None:
            return next(tokens)
        return _jwt(clock.t + 3600)

    b._solve_on_captcha_page = AsyncMock(side_effect=solve_on_captcha_page)
    b._api_unlocked = AsyncMock(return_value=True)
    return b, page


def test_an_expired_token_is_solved_once_and_the_reads_answer_again(tmp_path, caplog):
    clock = Clock()
    b, _ = _token_bridge(tmp_path, clock)
    b.token = _jwt(clock.t - 1)  # süresi dolmuş

    async def go():
        return await asyncio.gather(*(b.fetch_json(f"/event/{i}") for i in range(5)))

    with caplog.at_level("INFO"):
        answers = _run(go())
    assert answers == [{"event": {}}] * 5
    assert b._solve_on_captcha_page.await_count == 1 and (b.solves, b.solve_failures) == (1, 0)
    b.context.clear_cookies.assert_any_await(name="sofa_captcha")
    assert b.solve_cooldown_left() == 0 and cs._cached_token == b.token
    assert "rejecting the captcha token" in caplog.text


def test_a_rejected_token_whose_solve_fails_enters_the_cooldown(tmp_path):
    from sofascore_scraper import bridge_health

    clock = Clock()
    b, _ = _token_bridge(tmp_path, clock, solved=[None, None, None])
    b.token = _jwt(clock.t - 1)
    assert _run(b.fetch_json("/event/1")) is None
    assert b.solve_cooldown_left() == 180 and b.consecutive_solve_failures == 1
    assert _run(b.fetch_json("/event/2")) is None  # beklemede yeni çözüm yok
    assert b._solve_on_captcha_page.await_count == 1
    # Token'ın reddi de challenge'ın türüdür: bir çözüm yanıtlardı
    assert bridge_health.snapshot()["last_error"]["kind"] == bridge_health.KIND_CHALLENGE


def test_a_fetch_whose_token_a_solve_replaced_meanwhile_is_sent_again_without_a_solve(tmp_path):
    clock = Clock()
    b, page = _token_bridge(tmp_path, clock)
    old, new = _jwt(clock.t - 1), _jwt(clock.t + 3600)
    b.token = old
    real = page.evaluate

    async def evaluate(script, arg):
        res = await real(script, arg)
        if arg[2] == old:
            b._set_token(new)  # eski token'ın isteği yoldayken bir çözüm bitti
            b._token_at = clock.t - 60  # taze değil: kısayol token'ın değişmesidir
        return res

    page.page.evaluate = AsyncMock(side_effect=evaluate)
    assert _run(b.fetch_json("/event/1")) == {"event": {}}
    assert [t for _, t in page.sent] == [old, new]
    b._solve_on_captcha_page.assert_not_awaited()


# --- B7: geçen bir çözüm, siteye yönlenme ve cookie'dir (c: ec3d614) ----------------------------------


class SolvePage:
    """Scrapling'in havuz sayfası: captcha.html'nin her yüklemesinin vardığı adres (`lands`) ve bıraktığı cookie."""

    def __init__(self, lands, cookies) -> None:
        self.lands, self.cookies = list(lands), list(cookies)
        self.page = MagicMock()
        self.page.close = AsyncMock()
        self.page.goto = AsyncMock()
        self.page.url = "about:blank"
        self.info = MagicMock(page=self.page, state="ready", url="")
        self.removed = 0
        self.cookie: Optional[str] = None


def _solving_bridge(tmp_path, monkeypatch, lands, cookies, *, cookie=None):
    """
    Scrapling oturumu captcha.html'yi sayfa açısından Scrapling'in kendi akışıyla yükler: `page_action` sayfayı vardığı
    yerde görür, havuz sayfayı sonra da tutar, bağlamın cookie'si son yüklemenin bıraktığıdır.
    """
    monkeypatch.setattr(cs, "_POLL_S", 0.0)
    sp = SolvePage(lands, cookies)
    sp.cookie = cookie
    b = cs.BrowserBridge(profile_dir=str(tmp_path / "profile"), clock=Clock())
    pool = MagicMock()
    pool.pages = []

    def remove_page(info):
        pool.pages.remove(info)
        sp.removed += 1

    pool.remove_page = remove_page

    async def fetch(url, **kw):
        assert url == cs.CAPTCHA_URL
        if sp.info not in pool.pages:
            pool.pages.append(sp.info)
        sp.page.url = sp.lands.pop(0)
        new = sp.cookies.pop(0)
        if new is not None:
            sp.cookie = new
        if "page_action" in kw:
            await kw["page_action"](sp.page)
        return MagicMock()

    b.session = MagicMock()
    b.session.fetch = AsyncMock(side_effect=fetch)
    b.session.page_pool = pool
    b.context = MagicMock()

    async def cookies_of(_origin):
        return [{"name": "sofa_captcha", "value": sp.cookie}] if sp.cookie else []

    b.context.cookies = AsyncMock(side_effect=cookies_of)
    b.context.clear_cookies = AsyncMock()
    return b, sp


def test_a_load_that_never_leaves_captcha_html_fails(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cs, "REDIRECT_WAIT_S", 0.0)
    b, sp = _solving_bridge(tmp_path, monkeypatch, [cs.CAPTCHA_URL], [None])
    with caplog.at_level("INFO"):
        assert _run(b._solve_on_captcha_page()) is None
    assert "captcha.html did not pass" in caplog.text and "no redirect to the site" in caplog.text


def test_a_load_that_redirects_with_a_cookie_passes(tmp_path, monkeypatch):
    tok = _jwt(time.time() + 3600)
    b, _ = _solving_bridge(tmp_path, monkeypatch, [cs.HOME_URL], [tok])
    assert _run(b._solve_on_captcha_page()) == tok


def test_an_old_cookie_without_a_redirect_is_not_a_pass(tmp_path, monkeypatch):
    # Eskiden 10 sn sonra bulunan cookie, sayfa hiç yönlenmese de çözümün token'ıydı
    old = _jwt(time.time() + 3600)
    monkeypatch.setattr(cs, "REDIRECT_WAIT_S", 0.0)
    b, _ = _solving_bridge(tmp_path, monkeypatch, [cs.CAPTCHA_URL], [None], cookie=old)
    assert _run(b._solve_on_captcha_page()) is None
    # Aynı cookie yönlenmeyle: site onu kabul etti, bir geçiş (yoklama karar verir)
    b, _ = _solving_bridge(tmp_path, monkeypatch, [cs.HOME_URL], [None], cookie=old)
    assert _run(b._solve_on_captcha_page()) == old


def test_a_new_cookie_without_a_redirect_is_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "REDIRECT_WAIT_S", 0.0)
    tok = _jwt(time.time() + 3600)
    b, _ = _solving_bridge(tmp_path, monkeypatch, [cs.CAPTCHA_URL], [tok])
    assert _run(b._solve_on_captcha_page()) == tok


def test_a_redirect_without_a_cookie_waits_a_moment_for_it(tmp_path, monkeypatch):
    b, sp = _solving_bridge(tmp_path, monkeypatch, [cs.HOME_URL], [None])
    tok = _jwt(time.time() + 3600)
    calls = []

    async def cookies_of(_origin):
        calls.append(1)
        if len(calls) >= 4:  # sayfa ayrılırken yazılır
            return [{"name": "sofa_captcha", "value": tok}]
        return []

    b.context.cookies = AsyncMock(side_effect=cookies_of)
    assert _run(b._solve_on_captcha_page()) == tok


def test_the_redirect_is_waited_for_after_scraplings_solver(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_POLL_S", 0.0)
    page = MagicMock(url=cs.CAPTCHA_URL)
    seen = []

    async def go():
        b = _bridge(tmp_path)

        async def later():
            for _ in range(5):
                await asyncio.sleep(0)
            page.url = cs.HOME_URL

        task = asyncio.ensure_future(later())
        seen.append(await b._await_redirect(page))
        await task

    _run(go())
    assert seen == [cs.HOME_URL]
    assert cs._on_site(cs.HOME_URL) and not cs._on_site(cs.CAPTCHA_URL)
    assert not cs._on_site("about:blank") and cs._on_origin(cs.CAPTCHA_URL)


# --- A2: Scrapling'in çözüm sayfası her yüklemeden sonra kapanır (c: ec3d614) -------------------------


def test_scraplings_solve_page_is_closed_and_the_bridge_page_kept(tmp_path):
    b = _bridge(tmp_path)
    b.page = _page()
    solve_page, busy_page = _page(url=cs.HOME_URL), _page(url=cs.CAPTCHA_URL)
    infos = [
        MagicMock(page=solve_page, state="ready"),
        MagicMock(page=busy_page, state="busy"),
        MagicMock(page=b.page, state="ready"),
    ]
    pool = MagicMock()
    pool.pages = list(infos)
    pool.remove_page = lambda info: pool.pages.remove(info)
    b.session = MagicMock(page_pool=pool)
    _run(b._park_solver_pages())
    solve_page.close.assert_awaited_once()
    busy_page.close.assert_not_awaited()
    b.page.close.assert_not_awaited()
    assert pool.pages == infos[1:]
    # Havuzun API'si yoksa sayfa about:blank'e gider
    bare = MagicMock(spec=["pages"])
    bare.pages = [infos[0]]
    b.session = MagicMock(page_pool=bare)
    _run(b._park_solver_pages())
    solve_page.goto.assert_awaited_once_with("about:blank")


def test_every_load_parks_the_solve_page_passed_or_not(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "REDIRECT_WAIT_S", 0.0)
    b, sp = _solving_bridge(tmp_path, monkeypatch, [cs.CAPTCHA_URL], [None])
    assert _run(b._solve_on_captcha_page()) is None
    assert sp.removed == 1 and sp.page.close.await_count == 1
    tok = _jwt(time.time() + 3600)
    b, sp = _solving_bridge(tmp_path, monkeypatch, [cs.HOME_URL], [tok])
    assert _run(b._solve_on_captcha_page()) == tok
    assert sp.removed == 1 and sp.page.close.await_count == 1


def test_a_load_that_raises_still_parks_the_page(tmp_path, monkeypatch):
    b, sp = _solving_bridge(tmp_path, monkeypatch, [cs.CAPTCHA_URL], [None])
    pool = b.session.page_pool

    async def raises(url, **kw):
        pool.pages.append(sp.info)
        raise RuntimeError("iframe.bounding_box")

    b.session.fetch = AsyncMock(side_effect=raises)
    monkeypatch.setattr(cs, "REDIRECT_WAIT_S", 0.0)
    assert _run(b._solve_on_captcha_page()) is None
    assert sp.removed == 1


# --- B9: çözüm başına tek captcha.html yüklemesi, tek yük saati (saatte 8), Scrapling retries=1 -------------
# (c: 43f8edd, b5005bf, cfed796)


def test_a_failed_solve_loads_captcha_html_once(tmp_path):
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    assert _run(b.solve_challenge()) is None
    assert [c.args[0] if c.args else 1 for c in b._solve_on_captcha_page.await_args_list] == [1]


def test_a_load_that_never_leaves_captcha_html_is_counted_once_per_load(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cs, "REDIRECT_WAIT_S", 0.0)
    b, sp = _solving_bridge(tmp_path, monkeypatch, [cs.CAPTCHA_URL] * 3, [None] * 3)
    with caplog.at_level("INFO"):
        assert _run(b._solve_on_captcha_page(3)) is None
    assert b.session.fetch.await_count == 3 and len(b._load_starts) == 3
    fails = [r.getMessage() for r in caplog.records if "captcha.html did not pass" in r.getMessage()]
    assert [m.split("(")[1].split(")")[0] for m in fails] == ["load 1 of 3", "load 2 of 3", "load 3 of 3"]


def test_no_path_loads_more_in_an_hour_than_the_cooldowns_allow(tmp_path):
    # PORT_LOADS_PER_HOUR (8): saatte o kadar yükleme varken bir 403'ün çözümü en eskisinin çıkmasını bekler,
    # okumalar bir beklemedeki gibi tutulur
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    b._load_starts.extend(clock.t - 3000 + 100 * i for i in range(8))
    assert b._load_wait() == 600
    assert _run(b.solve_challenge()) is None
    assert b._solve_on_captcha_page.await_count == 0 and b.solve_cooldown_left() == 600
    clock.t += 600
    assert b._load_wait() == 0


def test_the_hourly_cap_holds_before_a_solves_second_load(tmp_path):
    # Reddedilen token'ın sil-ve-yeniden-yükle'si ikinci bir yüklemedir: saat dolmuşken (dokuzuncu) yapılmaz
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b.context = MagicMock()
    b.context.clear_cookies = AsyncMock()
    tok = _jwt(time.time() + 3600)

    async def load(*_a, **_k):
        b._load_starts.append(clock.t)
        return tok

    b._solve_on_captcha_page = AsyncMock(side_effect=load)
    b._api_unlocked = AsyncMock(return_value=False)
    b._load_starts.extend(clock.t - 3000 + 100 * i for i in range(7))
    assert _run(b._solve_checked()) is None
    assert b._solve_on_captcha_page.await_count == 1 and len(b._load_starts) == 8
    b.context.clear_cookies.assert_not_awaited()


def test_a_failed_solves_cooldown_also_holds_the_next_load(tmp_path):
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    assert _run(b.solve_challenge()) is None
    assert b._load_wait() == 180
    clock.t += 180
    assert b._load_wait() == 0
    b._solve_on_captcha_page = AsyncMock(return_value=_jwt(time.time() + 3600))
    assert _run(b.solve_challenge()) is not None
    assert b._load_not_before == -float("inf")  # geçen çözüm saati açar


def test_the_solve_budgets_fit_the_timeouts_that_wait_for_them(tmp_path, monkeypatch):
    # Bir istek: ilk denemesi, yoklamalarıyla çözüm, yinelemesi REQUEST_TIMEOUT içinde; açılış: Chromium'un açılışı ve
    # çözüm STARTUP_TIMEOUT içinde
    probe = cs._JS_FETCH_TIMEOUT_MS / 1000
    assert 2 * probe + cs.SOLVE_BUDGET_S <= cs.REQUEST_TIMEOUT
    assert cs.LAUNCH_SOLVE_BUDGET_S + 30 <= cs.STARTUP_TIMEOUT
    assert cs.SOLVE_BUDGET_S - cs._HOME_S - cs._PROBE_S >= cs.MIN_LOAD_S  # yüklemesi sığar
    monkeypatch.setattr(cs, "MIN_LOAD_S", 0.05)
    monkeypatch.setattr(cs, "_PROBE_S", 0.0)
    monkeypatch.setattr(cs, "_HOME_S", 0.0)
    b, _sp = _solving_bridge(tmp_path, monkeypatch, [cs.CAPTCHA_URL] * 9, [None] * 9)

    async def hang(url, **kw):
        await asyncio.sleep(10)

    b.session.fetch = AsyncMock(side_effect=hang)

    async def go():
        start = time.monotonic()
        token = await b._solve_checked(time.monotonic() + 0.3, 3)
        return token, time.monotonic() - start

    token, took = _run(go())
    assert token is None and took < 0.5
    assert b.session.fetch.await_count <= 3


def test_a_passed_solve_logs_the_time_its_token_has_left(tmp_path, caplog):
    clock = Clock()
    b = _clocked(tmp_path, clock)
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(clock.t) + 3630}).encode())
    tok = "e30." + payload.decode().rstrip("=") + ".sig"
    assert cs.token_left(tok, clock.t) == 3630 and cs.token_left("opaque", clock.t) is None
    b._solve_on_captcha_page = AsyncMock(return_value=tok)
    with caplog.at_level("INFO"):
        assert _run(b.solve_challenge()) == tok
    assert "The Turnstile challenge is solved (the token has 3630 s left)" in caplog.text
    assert tok not in caplog.text and payload.decode()[:20] not in caplog.text


# --- B8: açılış (c: ec3d614, 1f27d68, 43f8edd, ce94eb3) -----------------------------------------------


def _launching_bridge(tmp_path, monkeypatch, cookies_seq, clock: Optional[Clock] = None):
    made, page = _fake_session(monkeypatch, cookies_seq)
    b = cs.BrowserBridge(profile_dir=str(tmp_path / "profile"), clock=clock or Clock())
    return b, made, page


def test_launch_without_a_token_solves_checks_and_then_opens_the_home_page(tmp_path, monkeypatch, real_launch):
    tok = _jwt(time.time() + 3600)
    cookie = [{"name": "sofa_captcha", "value": tok}]
    clock = Clock()
    b, _, page = _launching_bridge(tmp_path, monkeypatch, [[], cookie, cookie, cookie], clock)
    b._solve_on_captcha_page = AsyncMock(return_value=tok)
    _run(b.ensure_ready())
    assert b.token == tok and b._token_at == clock.t  # şimdi çözüldü: 30 sn yeniden kullanılır
    # Açılışın çözümü de köprü sayfasından API'ye karşı yoklanır, cookie gelince ana sayfada
    probe = page.evaluate.await_args.args[1]
    assert probe[0] == cs._api_url(cs._PROBE_PATH) and page.url == cs.HOME_URL
    assert b._checked_token == tok


def test_a_launch_solve_that_leaves_no_cookie_keeps_the_page_on_blank(tmp_path, monkeypatch, real_launch, caplog):
    b, _, page = _launching_bridge(tmp_path, monkeypatch, [[]] * 4)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    with caplog.at_level("WARNING"):
        _run(b.ensure_ready())
    assert b.page is not None and b.token is None  # açılış sürer, eskisi gibi
    assert (b.solves, b.solve_failures, b.consecutive_solve_failures) == (1, 1, 1)
    assert b.solve_cooldown_left() == 0  # kendi beklemesi yok: ilk istek yeniden çözer
    # Cookie yokken /tr'ye değil (c'nin 5. koşusu: captcha.html'ye yönlendi, koşu boyunca bir Turnstile widget'ı)
    assert page.url == "about:blank" and page.goto.await_count == 0
    assert "at launch left no sofa_captcha cookie" in caplog.text


def test_a_fetch_without_the_cookie_solves_first_and_never_fetches_from_blank(tmp_path, monkeypatch, real_launch):
    from sofascore_scraper import bridge_health

    clock = Clock()
    b, _, page = _launching_bridge(tmp_path, monkeypatch, [[]] * 6, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    _run(b.ensure_ready())
    assert b.solve_cooldown_left() == 0 and page.url == "about:blank"
    assert _run(b.fetch_json("/event/1")) is None
    assert b._solve_on_captcha_page.await_count == 2 and b.solve_cooldown_left() == 180
    assert bridge_health.snapshot()["last_error"]["kind"] == bridge_health.KIND_CHALLENGE
    assert _run(b.fetch_json("/event/2")) is None
    assert b._solve_on_captcha_page.await_count == 2  # bekleme: yükleme yok
    page.evaluate.assert_not_awaited()  # about:blank'ten hiçbir istek gitmedi
    clock.t += 180
    b._load_not_before = -float("inf")
    tok = _jwt(time.time() + 3600)

    async def passes(*_a, **_k):
        b.context.cookies = AsyncMock(return_value=[{"name": "sofa_captcha", "value": tok}])
        return tok

    b._solve_on_captcha_page = AsyncMock(side_effect=passes)
    assert _run(b.fetch_json("/event/1")) == {}
    assert page.url == cs.HOME_URL  # cookie gelince açıldı
    assert page.evaluate.await_args.args[1][0] == cs._api_url("/event/1")


def test_a_launch_solves_only_outside_a_cooldown_one_load_each(tmp_path, monkeypatch, real_launch):
    clock = Clock()
    b, _, page = _launching_bridge(tmp_path, monkeypatch, [[]] * 20, clock)
    b._solve_on_captcha_page = AsyncMock(return_value=None)
    _run(b.ensure_ready())
    assert (b.consecutive_solve_failures, b.solve_cooldown_left()) == (1, 0)
    b._solve_failed_at, b._solve_cooldown_s = clock.t, 180.0  # bir isteğin çözümü başarısız oldu
    page.is_closed.return_value = True  # beklemede yeniden açılış: çözüm yok
    _run(b.ensure_ready())
    assert b._solve_on_captcha_page.await_count == 1
    clock.t += 180
    page.is_closed.return_value = True
    _run(b.ensure_ready())
    assert b._solve_on_captcha_page.await_count == 2 and b.consecutive_solve_failures == 2
    assert all(c.args[0] == 1 for c in b._solve_on_captcha_page.await_args_list if c.args)


def test_the_launch_loads_once_even_when_the_api_refuses_its_token(tmp_path, monkeypatch, real_launch):
    tok = _jwt(time.time() + 3600)
    b, _, page = _launching_bridge(tmp_path, monkeypatch, [[]] + [[{"name": "sofa_captcha", "value": tok}]] * 9)
    page.evaluate = AsyncMock(return_value={"status": 403, "ok": False, "data": None, "text": CHALLENGE})
    b._solve_on_captcha_page = AsyncMock(return_value=tok)
    _run(b.ensure_ready())
    assert b._solve_on_captcha_page.await_count == 1
    assert (b.solves, b.solve_failures) == (1, 1)
    assert not b.context.clear_cookies.called  # açılışta sil-ve-yeniden-yükle yok


def test_a_fetch_during_the_launchs_solve_waits_for_it(tmp_path, monkeypatch, real_launch):
    tok = _jwt(time.time() + 3600)
    cookie = [{"name": "sofa_captcha", "value": tok}]
    b, _, _ = _launching_bridge(tmp_path, monkeypatch, [[], []] + [cookie] * 10)

    async def slow_pass(*_a, **_k):
        await asyncio.sleep(0.1)
        return tok

    b._solve_on_captcha_page = AsyncMock(side_effect=slow_pass)

    async def go():
        launch = asyncio.ensure_future(b.ensure_ready())
        await asyncio.sleep(0.02)  # açılışın çözümü sürüyor
        answer = await b.fetch_json("/event/1")
        await launch
        return answer

    assert _run(go()) == {}
    assert b._solve_on_captcha_page.await_count == 1 and b.solves == 1


def test_a_launch_cut_short_in_its_solve_counts_it_and_cools_down(tmp_path, monkeypatch, real_launch):
    b, _, _ = _launching_bridge(tmp_path, monkeypatch, [[]] * 10)
    b._solve_checked = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        _run(b.ensure_ready())
    assert (b.solves, b.solve_failures) == (1, 1) and b.solve_cooldown_left() == 180
    assert b._launch_failed_at == 0.0  # iptal bozuk bir tarayıcı değildir


def test_a_launch_cut_after_its_load_passed_is_no_failed_solve(tmp_path, monkeypatch, real_launch):
    b, _, _ = _launching_bridge(tmp_path, monkeypatch, [[]] * 10)
    tok = _jwt(time.time() + 3600)

    async def passes_then_cut(*_a, **_k):
        b._set_token(tok)  # yükleme geçti ve API kabul etti; sonra kesildi
        b._checked_token = tok
        raise asyncio.CancelledError

    b._solve_checked = AsyncMock(side_effect=passes_then_cut)
    with pytest.raises(asyncio.CancelledError):
        _run(b.ensure_ready())
    assert (b.solves, b.solve_failures) == (1, 0) and b.solve_cooldown_left() == 0


def test_a_cancelled_launch_does_not_hold_the_next_back(tmp_path):
    b = _bridge(tmp_path)
    b._launch = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        _run(b.ensure_ready())
    assert b._launch_failed_at == 0.0
    b._launch = AsyncMock(side_effect=RuntimeError("no chrome"))
    with pytest.raises(RuntimeError):
        _run(b.ensure_ready())
    assert b._launch_failed_at > 0


def test_a_page_that_closes_in_the_launchs_probe_fails_it_at_once(tmp_path):
    b = _bridge(tmp_path)
    b.page = _page(Exception("Target page, context or browser has been closed"))
    b.page.is_closed.return_value = True
    b._launching = True
    b.ensure_ready = AsyncMock()
    t = time.monotonic()
    with pytest.raises(RuntimeError, match="during the launch"):
        _run(b.evaluate("js"))
    assert time.monotonic() - t < 1 and b.ensure_ready.await_count == 0


def test_a_drop_token_solve_keeps_the_bridge_page_on_about_blank(tmp_path):
    # c'nin soak'ı: cookie'si gidince web uygulamasının kendi /tr yenilemesi captcha.html'ye gitti ve duvar boyunca
    # canlı bir widget tuttu. Token'ın reddindeki çözüm sayfayı önce about:blank'e götürür.
    clock = Clock()
    b, page = _token_bridge(tmp_path, clock, solved=[None])
    page.page.url = cs.HOME_URL
    b.token = _jwt(clock.t - 1)
    assert _run(b.fetch_json("/event/1")) is None
    page.page.goto.assert_any_await("about:blank")
    assert page.page.url == "about:blank" and b.solve_cooldown_left() == 180


def test_a_home_page_slow_after_a_passed_solve_is_tried_once_more(tmp_path):
    tok = _jwt(time.time() + 3600)
    b = _clocked(tmp_path, Clock())
    b.page = _page(url="about:blank")
    b.context = MagicMock()
    b.context.cookies = AsyncMock(return_value=[])
    loads = []

    async def goto(url, **kw):
        loads.append(kw["timeout"])
        if len(loads) > 1:
            b.page.url = url

    b.page.goto = AsyncMock(side_effect=goto)
    b.page.evaluate = AsyncMock(return_value={"status": 200, "ok": True, "data": {"a": 1}, "text": None})

    async def passes(*_a, **_k):
        b.context.cookies = AsyncMock(return_value=[{"name": "sofa_captcha", "value": tok}])
        return tok

    b._solve_on_captcha_page = AsyncMock(side_effect=passes)
    assert _run(b.fetch_json("/event/1")) == {"a": 1}
    assert len(loads) == 2


def test_with_the_cookie_a_page_on_blank_opens_the_home_page_and_fetches(tmp_path):
    b = _clocked(tmp_path, Clock())
    b.page = _page({"status": 200, "ok": True, "data": {"a": 1}, "text": None}, url="about:blank")
    b.context = MagicMock()
    b.context.cookies = AsyncMock(return_value=[{"name": "sofa_captcha", "value": "x"}])
    b.solve_challenge = AsyncMock()
    assert _run(b.fetch_json("/event/1")) == {"a": 1}
    assert b.page.url == cs.HOME_URL
    b.solve_challenge.assert_not_awaited()


# --- B10: evaluate() site dışından yalnızca cookie varken ve çözüm sürmüyorken döner (c: b5005bf) ------------


def test_evaluate_goes_back_home_off_the_site_only_with_a_cookie_and_no_solve(tmp_path):
    b = _bridge(tmp_path)
    b.page = _page(
        Exception("Page.evaluate: Execution context was destroyed, most likely a navigation"),
        {"ok": 1},
        url="https://accounts.google.com/",
    )
    b.context = MagicMock()
    b.context.cookies = AsyncMock(return_value=[{"name": "sofa_captcha", "value": "x"}])
    assert _run(b.evaluate("js", [1])) == {"ok": 1}
    assert b.page.evaluate.await_count == 2
    b.page.wait_for_load_state.assert_awaited_once()
    b.page.goto.assert_awaited_once_with(cs.HOME_URL, wait_until="domcontentloaded", timeout=30000)
    # Cookie yokken (token'ın reddindeki çözüm sildi): /tr captcha.html'ye yönlenirdi, istek başarısız olur
    b.page = _page(Exception("Execution context was destroyed"), url="about:blank")
    b.context.cookies = AsyncMock(return_value=[])
    with pytest.raises(RuntimeError, match="off the site"):
        _run(b.evaluate("js", [1]))
    b.page.goto.assert_not_awaited()
    # Cookie var ama çözüm sürüyor (sayfası bilerek about:blank'te): gitme yok
    b.page = _page(Exception("Execution context was destroyed"), url="about:blank")
    b.context.cookies = AsyncMock(return_value=[{"name": "sofa_captcha", "value": "x"}])

    async def solving():
        b._solve_task = asyncio.get_running_loop().create_future()
        try:
            await b.evaluate("js", [1])
        finally:
            b._solve_task = None

    with pytest.raises(RuntimeError, match="off the site"):
        _run(solving())
    b.page.goto.assert_not_awaited()


def test_a_live_bridge_goes_back_to_its_own_quiet_page(tmp_path):
    quiet = "https://www.sofascore.com/robots.txt"
    b = cs.BrowserBridge(profile_dir=str(tmp_path / "live"), home_url=quiet, report_health=False)
    b.page = _page(Exception("Execution context was destroyed"), {"ok": 1}, url="https://accounts.google.com/")
    b.context = MagicMock()
    b.context.cookies = AsyncMock(return_value=[{"name": "sofa_captcha", "value": "x"}])
    assert _run(b.evaluate("js")) == {"ok": 1}
    b.page.goto.assert_awaited_once_with(quiet, wait_until="domcontentloaded", timeout=30000)


# --- B4: köprü, çözüm soğurken ya da kapı tutarken sayfa isteğini göndermez (c: 8b0e05d, 68cfba4) ------------


def test_in_a_cooldown_the_page_fetch_is_answered_unsent(tmp_path):
    from sofascore_scraper import bridge_health

    clock = Clock()
    b = _clocked(tmp_path, clock)
    b.page = _page({"status": 200, "ok": True, "data": {"a": 1}, "text": None})
    b._solve_failed_at, b._solve_cooldown_s = clock.t, 180.0
    with cs.observe() as seen:
        assert _run(b.fetch_json("/event/1")) is None
    assert (seen.status, seen.sent) == (403, False)
    b.page.evaluate.assert_not_awaited()
    assert bridge_health.snapshot()["last_error"]["kind"] == bridge_health.KIND_CHALLENGE  # sayılır
    clock.t += 180
    with cs.observe() as seen:
        assert _run(b.fetch_json("/event/1")) == {"a": 1}
    assert (seen.status, seen.sent) == (200, True)


def test_a_fetch_whose_turn_comes_in_a_cooldown_is_not_sent(tmp_path, monkeypatch):
    clock = Clock()
    b = _clocked(tmp_path, clock)
    b.page = _page({"status": 200, "ok": True, "data": {"a": 1}, "text": None})

    async def slot():  # sıra beklenirken başka bir isteğin çözümü başarısız oldu
        b._solve_failed_at, b._solve_cooldown_s = clock.t, 180.0

    monkeypatch.setattr(cs, "_wait_for_slot", slot)
    with cs.observe() as seen:
        assert _run(b.fetch_json("/event/1")) is None
    assert seen.sent is False
    b.page.evaluate.assert_not_awaited()


def test_the_request_layers_gate_is_asked_when_the_turn_comes(tmp_path):
    b = _clocked(tmp_path, Clock())
    b.page = _page({"status": 200, "ok": True, "data": {"a": 1}, "text": None})
    with cs.observe(hold=lambda: "challenge") as seen:
        assert _run(b.fetch_json("/event/1")) is None
    assert seen.sent is False
    b.page.evaluate.assert_not_awaited()
    with cs.observe(hold=lambda: None) as seen:
        assert _run(b.fetch_json("/event/1")) == {"a": 1}


def test_the_solves_own_probe_is_never_held_back(tmp_path):
    b = _clocked(tmp_path, Clock())
    b.page = _page({"status": 200, "ok": True, "data": {}, "text": None})

    async def go():
        with cs.observe(hold=lambda: "challenge"):
            return await b._api_unlocked()

    assert _run(go()) is True
    b.page.evaluate.assert_awaited_once()


def test_the_outcome_reaches_the_caller_through_the_background_loop(tmp_path, monkeypatch):
    b = _clocked(tmp_path, Clock())
    b.page = _page({"status": 403, "ok": False, "data": None, "text": "<html>blocked</html>"})
    monkeypatch.setattr(cs.BrowserBridge, "get_instance", classmethod(lambda cls, *a, **k: b))
    with cs.observe() as seen:
        assert cs.fetch_api_via_browser_sync("/event/1") is None
    assert (seen.status, seen.text, seen.sent) == (403, "<html>blocked</html>", True)
