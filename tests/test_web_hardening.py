"""
Web uygulamasının güvenlik sınırları (src/web/security.py, src/web/app.py):

  - kaynak (origin) denetimi: aynı kaynaktan tarayıcı, başka siteden tarayıcı, Origin göndermeyen program
  - GET ile durum değiştiren uç nokta kalmadı
  - Host izin listesi: yerel varsayılan, yerel olmayan adres (izin listesiyle / listesiz), açık değer
  - isteğe bağlı erişim belirteci: REST, SSE, cookie akışı, /health, log ve tanılama paketi
  - güvenlik başlıkları ve derlenmiş arayüzün CSP ile uyumu
  - .env / tarayıcı profili / .env içeren yedek dosya izinleri (POSIX)
  - proxy parolasının kalan okuma yolları (iş hatası metni, köprü hata ayrıntısı)

Tümü çevrimdışıdır: SofaScore'a istek atılmaz, tarayıcı başlatılmaz.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import stat
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import conftest
import src.challenge_solver as cs
import src.utils as utils
from src import bridge_health, diagnostics, private_files, redact
from src.i18n import I18nManager
from src.paths import env_file_path
from src.web import fetch_job, security
from src.web.app import FRONTEND_DIST, app
from src.web.missing_ui import MISSING_UI_HTML
from src.web.routes import api as api_mod
from src.web.routes import data as data_mod
from src.web.routes.scrape import FetchRequest

REPO = Path(__file__).resolve().parents[1]
client = TestClient(app)

# Uydurma sınama değerleri (gerçek bir kimlik bilgisi değildir)
TOKEN = "t0ken-Zx9-correct-horse-battery-staple"
PROXY_SECRET = "s3cr3t-Pa55"
PROXY_URL = f"http://scraper:{PROXY_SECRET}@proxy.example:8080"

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX dosya izinleri")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _api_routes():
    """(yöntem, örnek yol) çiftleri: /api altındaki her uç nokta, yol parametreleri doldurulmuş."""
    out = []
    # OpenAPI şeması: FastAPI sürümünden bağımsız olarak kayıtlı her yol ve yöntemi verir
    for template, operations in app.openapi()["paths"].items():
        if not template.startswith("/api"):
            continue
        path = re.sub(r"\{name\}", "backup_all_20260101_000000.zip", template)
        path = re.sub(r"\{[a-z_]+\}", "1", path)
        out += [(method.upper(), path) for method in operations]
    assert len(out) > 30
    return sorted(out)


API_ROUTES = _api_routes()
UNSAFE_ROUTES = [(m, p) for m, p in API_ROUTES if m in security.UNSAFE_METHODS]
GET_ROUTES = [p for m, p in API_ROUTES if m == "GET"]


def _start_with_token(monkeypatch, value: str) -> None:
    """Uygulama bu belirteçle ("" = belirteçsiz) başlamış gibi: ortam ve başlangıçta okunan değer."""
    if value:
        monkeypatch.setenv(security.TOKEN_ENV, value)
    else:
        monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    monkeypatch.setattr(security, "_startup_token", value)


@pytest.fixture
def token(monkeypatch):
    """Erişim belirteci ayarlı (yalnızca ortamda: Docker -e gibi)."""
    _start_with_token(monkeypatch, TOKEN)
    redact.refresh()
    yield TOKEN
    monkeypatch.undo()
    redact.refresh()


@pytest.fixture
def env_backup():
    """Test .env dosyasını (içerik ve izinler) eski haline döndürür."""
    path = env_file_path()
    with open(path, encoding="utf-8") as f:
        before = f.read()
    yield path
    with open(path, "w", encoding="utf-8") as f:
        f.write(before)
    redact.refresh()


# --- 1a. Kaynak (origin) denetimi -----------------------------------------------------------

# Yan etkisi olmayan bir yazma uç noktası: boştayken 400 döner; 403 yalnızca denetimden gelir
WRITE = "/api/scrape/cancel"

ALLOWED_ORIGINS = {
    "program without Origin (curl)": {},
    "same-origin browser": {"sec-fetch-site": "same-origin", "origin": "http://testserver"},
    "same-origin browser without Sec-Fetch-Site": {"origin": "http://testserver"},
    "user-initiated (address bar, extension)": {"sec-fetch-site": "none"},
    # Ters vekil Host'u yeniden yazmış: tarayıcının kendi beyanı (Sec-Fetch-Site) belirler
    "same-origin browser behind a Host-rewriting proxy": {"sec-fetch-site": "same-origin", "origin": "https://scraper.example"},
}
REJECTED_ORIGINS = {
    "cross-site browser": {"sec-fetch-site": "cross-site", "origin": "https://evil.example"},
    "cross-site browser claiming our origin": {"sec-fetch-site": "cross-site", "origin": "http://testserver"},
    "another app on the same site (other port / subdomain)": {"sec-fetch-site": "same-site", "origin": "http://testserver:3000"},
    "cross-site browser without Sec-Fetch-Site": {"origin": "https://evil.example"},
    "same host, other port": {"origin": "http://testserver:3000"},
    "sandboxed frame / redirect": {"origin": "null"},
}


@pytest.mark.parametrize("headers", ALLOWED_ORIGINS.values(), ids=ALLOWED_ORIGINS.keys())
def test_origin_check_lets_the_app_and_programs_through(headers):
    r = client.post(WRITE, headers=headers)
    assert r.status_code == 400 and "No scraping process" in r.text


@pytest.mark.parametrize("headers", REJECTED_ORIGINS.values(), ids=REJECTED_ORIGINS.keys())
def test_origin_check_rejects_requests_triggered_by_another_site(headers):
    r = client.post(WRITE, headers=headers)
    assert r.status_code == 403
    assert r.json() == {"detail": "Cross-origin request rejected"}


@pytest.mark.parametrize("method,path", UNSAFE_ROUTES, ids=[f"{m} {p}" for m, p in UNSAFE_ROUTES])
def test_every_state_changing_route_is_behind_the_origin_check(method, path, monkeypatch):
    """Veri silme, yedek, ayar, iş başlatma, SofaScore'a istek: hiçbiri başka siteden tetiklenemez."""
    monkeypatch.setattr(utils.cffi_requests, "get", lambda *a, **k: pytest.fail("a request was sent"))
    before = _tree_digest()
    for headers in ({"sec-fetch-site": "cross-site"}, {"origin": "https://evil.example"}):
        assert client.request(method, path, headers=headers).status_code == 403
    assert _tree_digest() == before


def test_cross_site_reads_are_not_refused():
    """Denetim yazmalar içindir: salt okunur GET (ör. başka sayfadan açılan indirme bağlantısı) çalışır."""
    r = client.get("/api/leagues", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 200


# --- 1b. GET ile durum değiştiren uç nokta yok ----------------------------------------------

# Salt okunur olduğu elle doğrulanmış GET uç noktaları. Yeni bir GET eklendiğinde bu liste bilinçli
# olarak güncellenir: disk yazan, iş başlatan ya da SofaScore'a istek atan bir uç nokta GET olamaz.
READ_ONLY_GETS = {
    "/api/auth",
    "/api/bypass/status",
    "/api/dashboard",
    "/api/data/backups/backup_all_20260101_000000.zip",
    "/api/diagnostics",
    "/api/diagnostics/bundle",
    "/api/export/csv",
    "/api/jobs",
    "/api/jobs/1",
    "/api/leagues",
    "/api/leagues/1/missing-details",
    "/api/leagues/1/seasons",
    "/api/leagues/search",
    "/api/logs",
    "/api/matches",
    "/api/matches/1",
    "/api/scrape/status",
    "/api/scrape/stream",
    "/api/seasons/1/matches",
    "/api/settings",
    "/api/sports",
    "/api/stats/system",
    "/api/status",
}


def _tree_digest() -> dict:
    """Veri ve yapılandırma dosyalarının içerik özeti (iş deposu ve kilit dosyaları hariç)."""
    out = {}
    for root in (conftest.DATA_DIR, conftest.CONFIG_DIR):
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if name.startswith("jobs.db") or name.endswith(".lock"):
                    continue
                path = os.path.join(folder, name)
                with open(path, "rb") as f:
                    out[path] = hashlib.sha256(f.read()).hexdigest()
    with open(conftest.ENV_FILE, "rb") as f:
        out[conftest.ENV_FILE] = hashlib.sha256(f.read()).hexdigest()
    return out


def test_get_routes_are_the_reviewed_read_only_set():
    assert set(GET_ROUTES) == READ_ONLY_GETS


def test_no_get_route_writes_files_or_sends_requests(monkeypatch):
    monkeypatch.setattr(utils.cffi_requests, "get", lambda *a, **k: pytest.fail("a GET route sent a request"))
    from src.SofaScoreUi import SimpleSofaScoreUI

    monkeypatch.setattr(SimpleSofaScoreUI, "export_all_to_csv", lambda self: pytest.fail("a GET route wrote an export"))
    before = _tree_digest()
    for path in GET_ROUTES:
        query = {"/api/leagues/search": "?q=prem", "/api/seasons/1/matches": "?league_id=17"}.get(path, "")
        r = client.get(path + query)
        assert r.status_code in (200, 404), (path, r.status_code)
    assert _tree_digest() == before


def test_remote_league_search_is_post_only(monkeypatch):
    """Her arama SofaScore'a canlı istek atar: GET olsaydı başka bir sitedeki <img> tetikleyebilirdi."""
    calls = []
    monkeypatch.setattr(utils, "_sleep", lambda *a, **k: None)
    monkeypatch.setattr(
        utils.cffi_requests, "get", lambda url, **k: calls.append(url) or (_ for _ in ()).throw(ConnectionError("offline"))
    )
    r = client.get("/api/leagues/search-remote", params={"q": "premier"})
    assert r.status_code in (404, 405) and calls == []
    r = client.post("/api/leagues/search-remote", params={"q": "premier"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and calls == []
    # Uygulamanın kendi sayfasından: istek gider (burada çevrimdışı olduğu için 502 network)
    r = client.post("/api/leagues/search-remote", params={"q": "premier"}, headers={"sec-fetch-site": "same-origin"})
    assert r.status_code == 502 and r.json()["detail"]["reason"] == "network"
    assert calls and all("search/unique-tournaments/premier" in url for url in calls)


def test_csv_export_get_only_downloads_and_post_creates(tmp_path, monkeypatch):
    from src.SofaScoreUi import SimpleSofaScoreUI

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    processed = tmp_path / "match_details" / "processed"
    generated = []

    def fake_export(self):
        generated.append(1)
        processed.mkdir(parents=True, exist_ok=True)
        (processed / "all_matches_20260101.csv").write_text("match_id,league_folder\n1,17_Premier_League\n", encoding="utf-8")

    monkeypatch.setattr(SimpleSofaScoreUI, "export_all_to_csv", fake_export)

    # GET: dışa aktarım yokken hiçbir şey üretmez
    r = client.get("/api/export/csv")
    assert r.status_code == 404 and "POST /api/export/csv" in r.json()["detail"]
    assert generated == [] and not processed.exists()
    # Başka siteden POST: reddedilir, üretilmez
    assert client.post("/api/export/csv", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert generated == []
    # POST: üretir ve dosyayı döndürür; sonraki GET var olanı indirir
    r = client.post("/api/export/csv")
    assert r.status_code == 200 and r.text.startswith("match_id,league_folder") and generated == [1]
    r = client.get("/api/export/csv?league_id=17")
    assert r.status_code == 200 and r.text.strip().splitlines() == ["match_id,league_folder", "1,17_Premier_League"]
    assert generated == [1]


# --- 2. Host izin listesi -------------------------------------------------------------------

LOOPBACK = list(security.LOOPBACK_HOSTS)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2", "LOCALHOST"])
def test_loopback_binds(host):
    assert security.is_loopback_bind(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "my-server.lan", "", "10.0.0.1", "fe80::1"])
def test_non_loopback_binds(host):
    assert not security.is_loopback_bind(host)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, LOOPBACK),
        ("", LOOPBACK),  # .env'deki boş satır "hiçbir ada yanıt verme" demek değildir
        (" , ", LOOPBACK),
        ("my-server.lan", ["my-server.lan"]),  # açık değer olduğu gibi: yerel adlar eklenmez
        ("localhost, 192.168.1.5 ,my-server.lan", ["localhost", "192.168.1.5", "my-server.lan"]),
        ("*", ["*"]),
    ],
)
def test_allowed_hosts_come_from_the_setting_or_default_to_loopback(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(security.ALLOWED_HOSTS_ENV, raising=False)
    else:
        monkeypatch.setenv(security.ALLOWED_HOSTS_ENV, raw)
    assert security.allowed_hosts() == expected


@pytest.mark.parametrize(
    "bind,explicit,allow_any,expected",
    [
        # yerel adres: ortam olduğu gibi kalır (varsayılan: yerel adlar)
        ("127.0.0.1", None, False, None),
        ("localhost", "", False, None),
        ("127.0.0.1", None, True, None),
        # kullanıcının değeri her zaman geçerlidir; --allow-any-host onu ezmez
        ("0.0.0.0", "my-server.lan", False, None),
        ("0.0.0.0", "my-server.lan", True, None),
        ("192.168.1.5", "my-server.lan", False, None),
        ("0.0.0.0", "*", False, None),
        # belirli bir adres: yerel adlar + o adres (IP ile yazılmış Host'u DNS rebinding üretemez)
        ("192.168.1.5", None, False, "localhost,127.0.0.1,[::1],192.168.1.5"),
        ("fe80::1", "", False, "localhost,127.0.0.1,[::1],[fe80::1]"),
        # açıkça istenen güvensiz seçenek
        ("0.0.0.0", None, True, "*"),
        ("::", "", True, "*"),
    ],
)
def test_allowed_hosts_for_a_bind_address(bind, explicit, allow_any, expected):
    assert security.allowed_hosts_for_bind(bind, explicit, allow_any=allow_any) == expected


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "[::]", ""])
def test_listening_on_every_interface_needs_an_explicit_allow_list(bind):
    with pytest.raises(security.AllowedHostsRequired):
        security.allowed_hosts_for_bind(bind, None)
    with pytest.raises(security.AllowedHostsRequired):
        security.allowed_hosts_for_bind(bind, " ")


def _host_check(monkeypatch, setting):
    """Uygulamanın kurduğu gibi bir Host denetimi (izin listesi SOFASCORE_ALLOWED_HOSTS'tan)."""
    if setting is None:
        monkeypatch.delenv(security.ALLOWED_HOSTS_ENV, raising=False)
    else:
        monkeypatch.setenv(security.ALLOWED_HOSTS_ENV, setting)
    inner = Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])
    inner.add_middleware(TrustedHostMiddleware, allowed_hosts=security.allowed_hosts())
    probe = TestClient(inner)
    return lambda host: probe.get("/", headers={"host": host}).status_code


def test_host_header_matrix(monkeypatch):
    # Varsayılan: yalnızca yerel adlar
    status = _host_check(monkeypatch, None)
    assert [status(h) for h in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000")] == [200, 200, 200]
    assert [status(h) for h in ("evil.example", "192.168.1.5:8000", "my-server.lan")] == [400, 400, 400]
    # Açık değer: yazıldığı gibi
    status = _host_check(monkeypatch, "localhost,my-server.lan,192.168.1.5")
    assert [status(h) for h in ("my-server.lan:8000", "192.168.1.5:8000", "localhost")] == [200, 200, 200]
    assert [status(h) for h in ("evil.example", "127.0.0.1:8000", "sub.my-server.lan")] == [400, 400, 400]
    # Açıkça istenen "*": her ad
    status = _host_check(monkeypatch, "*")
    assert status("evil.example") == 200


def test_the_app_rejects_unknown_hosts_before_anything_else(token):
    # Belirteç doğru olsa da bilinmeyen Host reddedilir (DNS rebinding)
    r = client.get("/api/leagues", headers={"host": "evil.example", "authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 400


def _run_web(monkeypatch, argv, lang="en"):
    """main.py --web'i sunucuyu başlatmadan çalıştırır: (çıkış kodu, uvicorn.run çağrıları)."""
    import main as cli

    calls = []
    i18n = I18nManager()
    i18n.set_language(lang)
    monkeypatch.setattr(cli, "get_i18n", lambda: i18n)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr("sys.argv", ["main.py", "--web", *argv])
    return cli.main(), calls


@pytest.fixture
def hosts_env(monkeypatch):
    """SOFASCORE_ALLOWED_HOSTS ayarsız başlar; main.py'nin yazdığı değer test sonunda geri alınır."""
    monkeypatch.delenv(security.ALLOWED_HOSTS_ENV, raising=False)
    _start_with_token(monkeypatch, "")
    return lambda: os.environ.get(security.ALLOWED_HOSTS_ENV)


def test_web_on_loopback_keeps_the_default_allow_list(monkeypatch, hosts_env, caplog):
    with caplog.at_level(logging.WARNING):
        code, calls = _run_web(monkeypatch, [])
    assert code == 0 and len(calls) == 1
    assert calls[0][1]["host"] == "127.0.0.1"
    assert hosts_env() is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("lang,expected", [("en", "SOFASCORE_ALLOWED_HOSTS"), ("tr", "SOFASCORE_ALLOWED_HOSTS")])
def test_web_on_every_interface_refuses_to_start_without_an_allow_list(monkeypatch, hosts_env, capsys, lang, expected):
    code, calls = _run_web(monkeypatch, ["--host", "0.0.0.0"], lang)
    err = capsys.readouterr().err
    assert code == 2 and calls == []
    assert hosts_env() is None  # sessizce "*" yapılmadı
    assert expected in err and "--allow-any-host" in err and "0.0.0.0" in err
    assert ("insecure" in err) if lang == "en" else ("güvensiz" in err)


def test_web_on_every_interface_honours_the_users_allow_list(monkeypatch, hosts_env):
    monkeypatch.setenv(security.ALLOWED_HOSTS_ENV, "localhost,my-server.lan")
    code, calls = _run_web(monkeypatch, ["--host", "0.0.0.0", "--port", "9000"])
    assert code == 0 and calls[0][1] == {"host": "0.0.0.0", "port": 9000}
    assert hosts_env() == "localhost,my-server.lan"  # üzerine yazılmadı
    # --allow-any-host da kullanıcının değerini ezmez
    code, _ = _run_web(monkeypatch, ["--host", "0.0.0.0", "--allow-any-host"])
    assert code == 0 and hosts_env() == "localhost,my-server.lan"


def test_web_allow_any_host_is_an_explicit_opt_out(monkeypatch, hosts_env):
    code, calls = _run_web(monkeypatch, ["--host", "0.0.0.0", "--allow-any-host"])
    assert code == 0 and len(calls) == 1
    assert hosts_env() == "*"


def test_web_on_one_address_allows_that_address_only(monkeypatch, hosts_env):
    code, calls = _run_web(monkeypatch, ["--host", "192.168.1.5"])
    assert code == 0 and len(calls) == 1
    assert hosts_env() == "localhost,127.0.0.1,[::1],192.168.1.5"


# --- 4. Başlangıç uyarısı -------------------------------------------------------------------

@pytest.mark.parametrize(
    "lang,expected",
    [
        ("en", "Anyone who can reach this port can read and delete your data and change the settings"),
        ("tr", "Bu porta ulaşabilen herkes verilerinizi okuyabilir ve silebilir, ayarları değiştirebilir"),
    ],
)
def test_one_warning_when_exposed_without_a_token(monkeypatch, hosts_env, caplog, lang, expected):
    with caplog.at_level(logging.WARNING):
        code, calls = _run_web(monkeypatch, ["--host", "192.168.1.5", "--port", "8123"], lang)
    assert code == 0 and len(calls) == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert expected in warnings[0] and "192.168.1.5:8123" in warnings[0] and security.TOKEN_ENV in warnings[0]


def test_the_warning_is_printed_even_when_the_log_level_hides_it(monkeypatch, hosts_env, capsys):
    import main as cli

    monkeypatch.setattr(cli.logger, "isEnabledFor", lambda level: False)
    code, _ = _run_web(monkeypatch, ["--host", "192.168.1.5"])
    assert code == 0
    assert "without an access token" in capsys.readouterr().err


def test_no_warning_with_a_token_or_on_loopback(monkeypatch, hosts_env, caplog):
    with caplog.at_level(logging.WARNING):
        _start_with_token(monkeypatch, TOKEN)
        assert _run_web(monkeypatch, ["--host", "192.168.1.5"])[0] == 0
        _start_with_token(monkeypatch, "")
        assert _run_web(monkeypatch, ["--host", "127.0.0.1"])[0] == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- 3. Erişim belirteci --------------------------------------------------------------------

def test_without_a_token_the_api_is_open(monkeypatch):
    _start_with_token(monkeypatch, "")
    assert security.api_token() == ""
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/auth").json() == {"required": False, "authenticated": True}
    # Giriş uç noktası zararsızdır: cookie kurmaz
    r = client.post("/api/auth/login", json={"token": "anything"})
    assert r.status_code == 200 and r.json() == {"required": False, "authenticated": True}
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize("method,path", API_ROUTES, ids=[f"{m} {p}" for m, p in API_ROUTES])
def test_with_a_token_every_api_route_needs_it(token, method, path):
    r = TestClient(app).request(method, path)
    if path in security.AUTH_OPEN_PATHS:
        assert r.status_code != 401 or r.json()["detail"]["code"] == "invalid_token"
        return
    assert r.status_code == 401
    assert r.json() == {"detail": {"code": "auth_required", "message": "An access token is required."}}
    assert r.headers["www-authenticate"] == "Bearer"


def test_unknown_api_paths_also_need_the_token(token):
    assert TestClient(app).get("/api/nope").status_code == 401
    assert TestClient(app).get("/api").status_code == 401


def test_token_check_uses_the_routed_path_not_the_url_built_from_the_host_header(token):
    """
    Host izin listesi "*" iken Host başlığı serbesttir. Denetim yolu URL'den okusaydı, "x/y?" gibi
    bir Host ile kurulan adresin yolu "/y" olur ve /api isteği belirteçsiz geçerdi.
    """
    import asyncio

    from starlette.requests import Request

    from src.web.app import security_boundary

    reached = []

    async def call_next(request):
        reached.append(request.scope["path"])
        return PlainTextResponse("handler")

    for host in (b"evil.example/y?", b"evil.example/y#", b"evil.example"):
        scope = {
            "type": "http", "method": "GET", "path": "/api/leagues", "raw_path": b"/api/leagues", "query_string": b"",
            "headers": [(b"host", host)], "scheme": "http", "server": ("127.0.0.1", 8000), "root_path": "",
        }
        response = asyncio.run(security_boundary(Request(scope), call_next))
        assert response.status_code == 401, host
    assert reached == []


def test_bearer_token_for_programs(token):
    c = TestClient(app)
    assert c.get("/api/leagues", headers={"authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.get("/api/leagues", headers={"authorization": f"bearer   {TOKEN}"}).status_code == 200
    # Yazma da: Origin göndermeyen program, belirteçle
    r = c.post(WRITE, headers={"authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 400 and "No scraping process" in r.text
    for bad in (f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", "Bearer ", f"Basic {TOKEN}", TOKEN, f"Token {TOKEN}"):
        assert c.get("/api/leagues", headers={"authorization": bad}).status_code == 401, bad
    # Belirteç adres satırında kabul edilmez (log'lara ve geçmişe düşerdi)
    assert c.get(f"/api/leagues?token={TOKEN}&access_token={TOKEN}").status_code == 401
    # Başka siteden gelen yazma, belirteç doğru olsa da reddedilir
    r = c.post(WRITE, headers={"authorization": f"Bearer {TOKEN}", "sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_cookie_session_for_the_web_ui(token):
    c = TestClient(app)
    assert c.get("/api/auth").json() == {"required": True, "authenticated": False}

    wrong = c.post("/api/auth/login", json={"token": "not-the-token"})
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "invalid_token"
    assert "set-cookie" not in wrong.headers
    assert c.get("/api/leagues").status_code == 401

    ok = c.post("/api/auth/login", json={"token": f"  {TOKEN} "})
    assert ok.status_code == 200 and ok.json() == {"required": True, "authenticated": True}
    cookie = ok.headers["set-cookie"]
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "samesite=strict", "path=/", f"max-age={security.SESSION_MAX_AGE}"} <= attributes
    assert "secure" not in attributes  # düz HTTP: Secure cookie tarayıcıdan geri gelmezdi
    # Cookie belirtecin kendisini taşımaz
    assert cookie.startswith(f"{security.SESSION_COOKIE}=") and TOKEN not in cookie and TOKEN not in ok.text

    # Oturum: REST ve (başlık gönderemeyen) SSE
    assert c.get("/api/auth").json() == {"required": True, "authenticated": True}
    assert c.get("/api/leagues").status_code == 200
    assert c.post(WRITE).status_code == 400

    out = c.post("/api/auth/logout")
    assert out.status_code == 200 and out.json() == {"required": True, "authenticated": False}
    assert f"{security.SESSION_COOKIE}=" in out.headers["set-cookie"] and "max-age=0" in out.headers["set-cookie"].lower()
    c.cookies.clear()
    assert c.get("/api/leagues").status_code == 401


def test_session_cookie_is_secure_behind_tls(token):
    r = TestClient(app).post("/api/auth/login", json={"token": TOKEN}, headers={"x-forwarded-proto": "https"})
    assert "secure" in {part.strip().lower() for part in r.headers["set-cookie"].split(";")}


def test_login_cannot_be_triggered_by_another_site(token):
    r = TestClient(app).post("/api/auth/login", json={"token": TOKEN}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_changing_the_token_ends_existing_sessions(token, monkeypatch):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert c.get("/api/leagues").status_code == 200
    _start_with_token(monkeypatch, TOKEN + "-rotated")  # yeni belirteçle yeniden başlatıldı
    assert c.get("/api/leagues").status_code == 401
    # Uydurma cookie değerleri de geçmez
    for forged in (TOKEN, "0" * 64, ""):
        assert TestClient(app, cookies={security.SESSION_COOKIE: forged}).get("/api/leagues").status_code == 401


def test_token_is_read_at_startup_and_a_settings_save_cannot_switch_it_off(monkeypatch, env_backup):
    """
    Belirteç ortamdan verilmiş (kabuk, Docker -e), .env'de ise boş bir satır var (.env.example'dan
    kopyalanmış). Ayar kaydı .env'i ortamın üzerine yeniden yükler; koruma yine de açık kalmalı.
    """
    monkeypatch.setenv(security.TOKEN_ENV, TOKEN)
    monkeypatch.setattr(security, "_startup_token", None)  # süreç yeni başlıyor
    monkeypatch.setenv("MAX_RETRIES", os.environ.get("MAX_RETRIES", "3"))  # ayar kaydı ortamı da yazar
    with open(env_backup, "a", encoding="utf-8") as f:
        f.write(f"\n{security.TOKEN_ENV}=\n")
    auth = {"authorization": f"Bearer {TOKEN}"}
    c = TestClient(app)
    assert c.get("/api/leagues").status_code == 401

    saved = c.post("/api/settings", json={"max_retries": 4}, headers=auth)
    assert saved.status_code == 200 and saved.json()["status"] == "success"
    assert os.environ[security.TOKEN_ENV] == ""  # yeniden yükleme ortamdaki değeri sildi
    assert security.api_token() == TOKEN
    assert c.get("/api/leagues").status_code == 401
    assert c.get("/api/leagues", headers=auth).status_code == 200


def test_status_stream_works_with_the_cookie_and_the_bearer_token(token):
    """EventSource başlık gönderemez: SSE akışı oturum cookie'siyle açılır."""
    anonymous = TestClient(app).get("/api/scrape/stream")
    assert anonymous.status_code == 401 and "event:" not in anonymous.text

    c = TestClient(app)
    assert c.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    for stream in (c.get("/api/scrape/stream"), TestClient(app).get("/api/scrape/stream", headers={"authorization": f"Bearer {TOKEN}"})):
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: update" in stream.text and '"is_running": false' in stream.text


def test_status_stream_without_a_token_is_open():
    stream = client.get("/api/scrape/stream")
    assert stream.status_code == 200 and "event: update" in stream.text


def test_health_hides_details_from_callers_without_the_token(token):
    bridge_health.record_failure(bridge_health.KIND_BROWSER, "RuntimeError: chromium missing at /home/someone/.cache")
    anonymous = TestClient(app).get("/health")
    # Sağlık denetimleri (Docker HEALTHCHECK, başlatıcı) çalışmaya devam eder; ayrıntı yok
    assert anonymous.status_code == 200 and anonymous.json() == {"status": "ok"}

    full = TestClient(app).get("/health", headers={"authorization": f"Bearer {TOKEN}"}).json()
    assert {"status", "version", "ui", "bridge", "throttle"} <= set(full)
    assert full["bridge"]["last_error"]["detail"].startswith("RuntimeError: chromium missing")


def test_health_is_complete_when_no_token_is_set():
    assert {"status", "version", "ui", "bridge", "throttle"} <= set(client.get("/health").json())


def test_the_web_ui_itself_loads_without_the_token(token):
    """Belirteç kutusunu gösterecek sayfa ve dosyaları belirteç istemez; veri yalnızca /api'dedir."""
    c = TestClient(app)
    assert c.get("/").status_code in (200, 503)
    assert c.get("/settings").status_code in (200, 503)
    assert c.get("/favicon.svg").status_code in (200, 503)


def test_token_comparison_is_constant_time(token, monkeypatch):
    seen = []
    real = security.hmac.compare_digest
    monkeypatch.setattr(security.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    c = TestClient(app)
    c.get("/api/leagues", headers={"authorization": "Bearer guess"})
    c.post("/api/auth/login", json={"token": "guess"})
    c.cookies.set(security.SESSION_COOKIE, "guess")
    c.get("/api/leagues")
    assert len(seen) == 3
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen)
    # Kaynakta == ile karşılaştırma yok
    source = (REPO / "src" / "web" / "security.py").read_text(encoding="utf-8")
    assert "== token" not in source and "token ==" not in source


def test_token_is_never_returned_logged_or_bundled(token, env_backup, tmp_path, caplog, monkeypatch):
    """Belirteç ortamda ve .env'de: hiçbir API yanıtında, log satırında ya da tanılama paketinde geçmez."""
    monkeypatch.setenv("MAX_RETRIES", os.environ.get("MAX_RETRIES", "3"))  # ayar kaydı ortamı da yazar
    with open(env_backup, "a", encoding="utf-8") as f:
        f.write(f"\n{security.TOKEN_ENV}={TOKEN}\n")
    redact.refresh()
    auth = {"authorization": f"Bearer {TOKEN}"}
    c = TestClient(app)
    log = logging.getLogger("WebAPI")

    with caplog.at_level(logging.DEBUG):
        attempt = "wrong-guess-123456"
        assert c.post("/api/auth/login", json={"token": attempt}).status_code == 401
        assert c.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
        assert c.post("/api/settings", json={"max_retries": 3}, headers=auth).status_code == 200
        # Bir hata metni belirteci taşısa bile (ör. yanlışlıkla loglanan başlık) log'a maskeli düşer
        log.error(f"request failed: Authorization: Bearer {TOKEN} / token {TOKEN}")
    messages = [r.getMessage() for r in caplog.records]
    assert any("Erişim belirteci reddedildi" in m for m in messages)
    assert not any(attempt in m for m in messages), "a rejected attempt must not be logged"
    assert all(TOKEN not in m for m in messages if "request failed" not in m)

    outputs = {
        "redacted log line": redact.redact_text(f"request failed: Authorization: Bearer {TOKEN} / token {TOKEN}"),
        "GET /api/settings": c.get("/api/settings", headers=auth).text,
        "GET /api/auth": c.get("/api/auth", headers=auth).text,
        "GET /health": c.get("/health", headers=auth).text,
        "GET /api/logs": c.get("/api/logs", params={"limit": 2000}, headers=auth).text,
        "GET /api/diagnostics": c.get("/api/diagnostics", headers=auth).text,
        "collect()": json.dumps(diagnostics.collect(source="cli")),
    }
    web_bundle = c.get("/api/diagnostics/bundle", headers=auth)
    assert web_bundle.status_code == 200
    with open(diagnostics.write_bundle(str(tmp_path / "bundle.zip"), source="cli"), "rb") as f:
        cli_bundle = f.read()
    for label, data in (("web bundle", web_bundle.content), ("cli bundle", cli_bundle)):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                outputs[f"{label} {name}"] = zf.read(name).decode("utf-8")
    session = security.session_value(TOKEN)
    for where, text in outputs.items():
        assert TOKEN not in text, f"token found in {where}"
        assert session not in text, f"session value found in {where}"
    # Sınama boş değil: ilgili satır log'da, maskelenmiş haliyle
    assert "request failed" in outputs["web bundle log_tail.txt"]


def test_token_is_a_masked_setting(monkeypatch):
    assert security.TOKEN_ENV in redact.KNOWN_SECRET_KEYS
    assert redact.is_secret_key(security.TOKEN_ENV)
    assert redact.mask_value(security.TOKEN_ENV, TOKEN) == redact.MASK
    assert redact.redact_obj({security.TOKEN_ENV: TOKEN}) == {security.TOKEN_ENV: redact.MASK}
    # Yalnızca ortamda ayarlı (Docker -e): .env'de olmasa da bilinen gizli değerdir
    _start_with_token(monkeypatch, TOKEN)
    redact.refresh()
    try:
        assert TOKEN in redact.secret_values()
        assert redact.redact_text(f"cookie jar had {TOKEN} in it") == f"cookie jar had {redact.MASK} in it"
    finally:
        monkeypatch.undo()
        redact.refresh()
    # Ayarlar API'si belirteci ne okur ne yazar
    assert "token" not in json.dumps(client.get("/api/settings").json()).lower()
    from src.web.routes.settings import SettingsUpdate

    assert not [name for name in SettingsUpdate.model_fields if "token" in name]


# --- 1c. Güvenlik başlıkları ve CSP ---------------------------------------------------------

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "same-origin",
}


def _csp(header: str) -> dict:
    out = {}
    for part in header.split(";"):
        name, _, sources = part.strip().partition(" ")
        out[name] = sources.split()
    return out


def _allows(policy: dict, directive: str, url: str) -> bool:
    """Tarayıcının kaynak eşleştirmesinin burada gereken kadarı: 'self', data: ve tam kaynak adresleri."""
    sources = policy.get(directive, policy["default-src"])
    parsed = urlparse(url)
    if parsed.scheme == "data":
        return "data:" in sources
    if parsed.scheme == "blob":
        return "blob:" in sources
    if not parsed.netloc:
        return "'self'" in sources  # göreli adres: uygulamanın kendisi
    return f"{parsed.scheme}://{parsed.netloc}" in sources


class _Page(HTMLParser):
    """Sayfanın yüklediği kaynaklar ve satır içi betik/stil kullanımı."""

    def __init__(self, html: str):
        super().__init__()
        self.resources = []  # (CSP yönergesi, adres)
        self.inline = {"script": 0, "style": 0, "style-attr": 0, "handler": 0}
        self._open = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if any(name.startswith("on") for name in a):
            self.inline["handler"] += 1
        if "style" in a:
            self.inline["style-attr"] += 1
        if tag == "script":
            if a.get("src"):
                self.resources.append(("script-src", a["src"]))
            else:
                self._open = "script"
        elif tag == "style":
            self._open = "style"
        elif tag == "link" and a.get("href"):
            rel = (a.get("rel") or "").lower()
            directive = {"stylesheet": "style-src", "modulepreload": "script-src", "icon": "img-src", "shortcut icon": "img-src"}.get(rel)
            if directive:
                self.resources.append((directive, a["href"]))
        elif tag == "img" and a.get("src"):
            self.resources.append(("img-src", a["src"]))

    def handle_data(self, data):
        if self._open and data.strip():
            self.inline[self._open] += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._open = None


def _assert_page_runs_under(html: str, policy: dict):
    page = _Page(html)
    for directive, url in page.resources:
        assert _allows(policy, directive, url), f"{directive} blocks {url}"
    if page.inline["script"] or page.inline["handler"]:
        assert "'unsafe-inline'" in policy["script-src"]
    if page.inline["style"] or page.inline["style-attr"]:
        assert "'unsafe-inline'" in policy["style-src"]
    return page


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("GET", "/", None),
        ("GET", "/settings", None),
        ("GET", "/health", 200),
        ("GET", "/api/leagues", 200),
        ("GET", "/api/nope", 404),
        ("GET", "/api/diagnostics/bundle", 200),
        ("POST", "/api/data/clear", 422),
        ("PUT", "/api/leagues", 405),
    ],
)
def test_security_headers_on_every_response(method, path, status):
    r = client.request(method, path)
    if status is not None:
        assert r.status_code == status
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers[name] == value, (path, name)
    policy = _csp(r.headers["content-security-policy"])
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["default-src"] == ["'self'"] and policy["object-src"] == ["'none'"]
    assert policy["base-uri"] == ["'self'"] and policy["form-action"] == ["'self'"]
    assert "'unsafe-inline'" not in policy["script-src"]


def test_security_headers_on_refusals(token):
    cross_site = client.post(WRITE, headers={"sec-fetch-site": "cross-site"})
    unauthenticated = TestClient(app).get("/api/leagues")
    assert (cross_site.status_code, unauthenticated.status_code) == (403, 401)
    for r in (cross_site, unauthenticated):
        for name, value in EXPECTED_HEADERS.items():
            assert r.headers[name] == value
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_strict_policy_allows_what_the_app_does():
    policy = _csp(security.content_security_policy("/", Path("/nonexistent/dist")))
    assert policy["script-src"] == ["'self'"]  # satır içi betik, eval ve dış betik yok
    assert policy["connect-src"] == ["'self'"]  # fetch ve SSE yalnızca uygulamanın kendisine
    assert _allows(policy, "connect-src", "/api/scrape/stream")
    assert not _allows(policy, "connect-src", "https://evil.example/collect")
    assert not _allows(policy, "script-src", "https://cdn.jsdelivr.net/x.js")
    assert _allows(policy, "font-src", "/assets/geist-latin-400-normal.woff2")
    assert _allows(policy, "font-src", "data:font/woff2;base64,AAAA")
    assert _allows(policy, "img-src", "data:image/svg+xml,%3Csvg%3E")


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").is_file(), reason="frontend/dist yok")
def test_built_spa_runs_under_the_policy_it_is_served_with():
    """frontend/dist'teki gerçek derleme: index.html, CSS'teki yazı tipleri ve betikler politikaya uyar."""
    r = client.get("/")
    assert r.status_code == 200 and '<div id="app">' in r.text
    policy = _csp(r.headers["content-security-policy"])
    page = _assert_page_runs_under(r.text, policy)
    assert page.inline["script"] == 0 and page.inline["handler"] == 0  # satır içi betik yok
    assert [d for d, _ in page.resources].count("script-src") >= 1

    assets = FRONTEND_DIST / "assets"
    # CSS: yazı tipleri ve görseller (gömülü data: ya da /assets altında)
    urls = []
    for css in assets.glob("*.css"):
        urls += re.findall(r"url\(\s*['\"]?([^'\")]+)", css.read_text(encoding="utf-8"))
    assert urls, "the build bundles its fonts"
    for url in urls:
        directive = "font-src" if re.search(r"font|\.woff2?", url[:60]) else "img-src"
        assert _allows(policy, directive, url), f"{directive} blocks {url[:80]}"

    # Betikler: kodu metinden üreten (eval / new Function) bir derleme 'unsafe-eval' ister
    scripts = "\n".join(js.read_text(encoding="utf-8") for js in assets.glob("*.js"))
    uses_eval = bool(re.search(r"\bFunction\(|\beval\(", scripts))
    declares_no_eval = security.CSP_MARKER in r.text
    if declares_no_eval:
        assert not uses_eval, "the build says it needs no eval but its scripts use it"
        assert "'unsafe-eval'" not in policy["script-src"]
    else:
        # Eski derleme (CSP etiketi yok): bozulmaması için politika gevşetilir
        assert "'unsafe-eval'" in policy["script-src"]
    # Dış kaynak yok: betiklerdeki her mutlak adres yalnızca metindir (XML ad alanı, belge bağlantısı)
    assert "'unsafe-inline'" not in policy["script-src"]


def test_policy_follows_the_build(tmp_path):
    current = tmp_path / "current"
    current.mkdir()
    (current / "index.html").write_text(
        '<!doctype html><html><head><meta name="sofascore-csp" content="no-eval" /></head><body></body></html>', encoding="utf-8"
    )
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "index.html").write_text("<!doctype html><html><head></head><body></body></html>", encoding="utf-8")

    assert not security.spa_needs_eval(current)
    assert security.spa_needs_eval(legacy)
    assert not security.spa_needs_eval(tmp_path / "missing")
    assert "'unsafe-eval'" not in security.content_security_policy("/", current)
    assert _csp(security.content_security_policy("/", legacy))["script-src"] == ["'self'", "'unsafe-eval'"]
    # Yeniden derleme sunucu yeniden başlatılmadan görülür
    (legacy / "index.html").write_text((current / "index.html").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not security.spa_needs_eval(legacy)


def test_frontend_source_declares_what_the_policy_relies_on():
    index = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    config = (REPO / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert security.CSP_MARKER in index
    assert re.search(r"__INTLIFY_JIT_COMPILATION__:\s*true", config)
    page = _Page(index)
    assert page.inline == {"script": 0, "style": 0, "style-attr": 0, "handler": 0}


def test_missing_ui_page_runs_under_the_policy(tmp_path):
    policy = _csp(security.content_security_policy("/", tmp_path))
    page = _assert_page_runs_under(MISSING_UI_HTML, policy)
    assert page.inline["script"] == 0 and page.inline["style"] == 1


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_api_docs_pages_get_the_policy_they_need(path):
    """FastAPI'nin belge sayfaları betiklerini CDN'den yükler: yalnızca o sayfalara özel, daha gevşek politika."""
    r = client.get(path)
    assert r.status_code == 200
    policy = _csp(r.headers["content-security-policy"])
    page = _assert_page_runs_under(r.text, policy)
    assert any(url.startswith("https://cdn.jsdelivr.net/") for _d, url in page.resources)
    assert policy["frame-ancestors"] == ["'none'"]
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers[name] == value
    # Gevşeklik başka hiçbir sayfaya taşmaz
    assert "cdn.jsdelivr.net" not in client.get("/").headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in client.get("/openapi.json").headers["content-security-policy"]


# --- 5. Dosya izinleri ----------------------------------------------------------------------

@posix_only
def test_env_file_is_created_private(tmp_path, monkeypatch):
    env = tmp_path / "conf" / ".env"
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setenv("DATE_FORMAT", os.environ.get("DATE_FORMAT", ""))  # update_env_variable ortamı da yazar
    old_umask = os.umask(0o022)
    try:
        assert api_mod.config_manager.update_env_variable("DATE_FORMAT", "%Y-%m-%d")
    finally:
        os.umask(old_umask)
        redact.refresh()
    assert env.read_text(encoding="utf-8").strip() == "DATE_FORMAT='%Y-%m-%d'"
    assert _mode(env) == 0o600


@posix_only
def test_writing_a_setting_tightens_an_existing_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MAX_RETRIES=3\n", encoding="utf-8")
    os.chmod(env, 0o644)
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setenv("DATE_FORMAT", os.environ.get("DATE_FORMAT", ""))
    try:
        assert api_mod.config_manager.update_env_variable("DATE_FORMAT", "%d.%m.%Y")
    finally:
        redact.refresh()
    assert "MAX_RETRIES=3" in env.read_text(encoding="utf-8")
    assert _mode(env) == 0o600


@posix_only
def test_startup_tightens_existing_env_and_browser_profile(tmp_path, monkeypatch, caplog):
    env = tmp_path / ".env"
    env.write_text("PROXY_URL=http://u:p@h:1\n", encoding="utf-8")
    profile = tmp_path / "profile"
    (profile / "Default").mkdir(parents=True)
    os.chmod(env, 0o664)
    os.chmod(profile, 0o755)
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setenv("SOFASCORE_BROWSER_PROFILE", str(profile))

    with caplog.at_level(logging.INFO):
        assert private_files.harden_secret_paths() == [str(env), str(profile)]
    assert _mode(env) == 0o600 and _mode(profile) == 0o700
    assert len([r for r in caplog.records if "İzinler daraltıldı" in r.getMessage()]) == 2
    # İkinci çağrı: değişecek bir şey yok, log da yok
    caplog.clear()
    assert private_files.harden_secret_paths() == []
    assert not caplog.records
    # Zaten daha dar olan izinler genişletilmez
    os.chmod(env, 0o400)
    assert private_files.harden_secret_paths() == []
    assert _mode(env) == 0o400
    os.chmod(env, 0o600)


@posix_only
def test_startup_with_nothing_to_tighten_is_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setenv("SOFASCORE_BROWSER_PROFILE", str(tmp_path / "no-profile"))
    assert private_files.harden_secret_paths() == []
    assert not (tmp_path / "no.env").exists() and not (tmp_path / "no-profile").exists()


def test_permission_tightening_is_a_no_op_on_windows(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_text("X=1\n", encoding="utf-8")
    before = _mode(target)
    monkeypatch.setattr(private_files.os, "name", "nt")
    try:
        changed = private_files.restrict_permissions(str(target), private_files.PRIVATE_FILE_MODE)
    finally:
        monkeypatch.undo()
    assert changed is False and _mode(target) == before


@posix_only
def test_browser_profile_directory_is_created_private(tmp_path):
    old_umask = os.umask(0o022)
    try:
        profile = tmp_path / "cache" / "chrome_profile"
        cs.BrowserBridge(profile_dir=str(profile))
        assert _mode(profile) == 0o700
        # Var olan, herkese açık bir profil de daraltılır
        loose = tmp_path / "loose"
        loose.mkdir(mode=0o755)
        cs.BrowserBridge(profile_dir=str(loose))
        assert _mode(loose) == 0o700
    finally:
        os.umask(old_umask)


@posix_only
def test_the_app_and_the_cli_tighten_permissions_at_startup():
    for name in ("src/web/app.py", "main.py"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert re.search(r"^\s*harden_secret_paths\(\)$", text, flags=re.M), name
    install = (REPO / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert re.search(r"cp \.env\.example \.env\n(\s*#.*\n)*\s*chmod 600 \.env", install)


def test_backup_with_env_says_so_in_its_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert os.path.exists(env_file_path())

    plain = client.post("/api/data/backup?scope=config").json()
    assert "with_env" not in plain["filename"]
    with zipfile.ZipFile(os.path.join(data_mod._backups_dir(), plain["filename"])) as zf:
        assert ".env" not in zf.namelist()

    with_env = client.post("/api/data/backup?scope=config&include_env=true").json()
    assert re.fullmatch(r"backup_config_with_env_\d{8}_\d{6}\.zip", with_env["filename"])
    path = os.path.join(data_mod._backups_dir(), with_env["filename"])
    with zipfile.ZipFile(path) as zf:
        assert ".env" in zf.namelist() and "leagues.txt" in zf.namelist()
    if os.name == "posix":
        assert _mode(path) == 0o600
    # İndirme bağlantısı yeni adla da çalışır
    assert with_env["download_url"] == f"/api/data/backups/{with_env['filename']}"
    assert client.get(with_env["download_url"]).status_code == 200

    # .env'in girmediği kapsamda ad da onu söylemez
    seasons = client.post("/api/data/backup?scope=seasons&include_env=true").json()
    assert "with_env" not in seasons["filename"]
    with zipfile.ZipFile(os.path.join(data_mod._backups_dir(), seasons["filename"])) as zf:
        assert ".env" not in zf.namelist()


# --- 6. Proxy parolasının kalan okuma yolları -----------------------------------------------

@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setenv("USE_PROXY", "true")
    monkeypatch.setenv("PROXY_URL", PROXY_URL)
    redact.refresh()
    yield PROXY_URL
    monkeypatch.undo()
    redact.refresh()


def test_failed_job_does_not_store_or_return_the_proxy_password(proxy, monkeypatch, capsys):
    """İş beklenmedik bir hatayla biterse hata metni iş kaydına yazılır ve API'den okunur."""

    def boom(*args, **kwargs):
        raise RuntimeError(f"curl: (56) CONNECT tunnel failed, response 407 via {PROXY_URL}")

    monkeypatch.setattr(fetch_job, "SimpleSofaScoreUI", boom)
    store = api_mod._job_store
    job_id = store.create_running({"mode": "details", "league_id": conftest.LEAGUE_ID})
    fetch_job.run_fetch_job(job_id, FetchRequest(mode="details", league_id=conftest.LEAGUE_ID))

    status = client.get("/api/scrape/status")
    assert status.json()["status"] == "Failed"
    assert "***@proxy.example:8080" in status.json()["current_task"]
    printed = capsys.readouterr().out
    assert "Background Task FAILED" in printed
    outputs = {
        "GET /api/scrape/status": status.text,
        "GET /api/jobs": client.get("/api/jobs").text,
        f"GET /api/jobs/{job_id}": client.get(f"/api/jobs/{job_id}").text,
        "GET /api/scrape/stream": client.get("/api/scrape/stream").text,
        "stdout": printed,
    }
    for where, text in outputs.items():
        assert PROXY_SECRET not in text, f"proxy password found in {where}"


def test_bridge_error_detail_does_not_expose_the_proxy_password(proxy):
    """/health belirteçsiz de okunur: tarayıcı hata metnindeki proxy adresi maskelenir."""
    bridge_health.record_failure(
        bridge_health.KIND_BROWSER, f"Error: browserType.launch: proxy {PROXY_URL} refused (scraper:{PROXY_SECRET})"
    )
    health = client.get("/health")
    status = client.get("/api/bypass/status")
    assert "***@proxy.example:8080" in health.json()["bridge"]["last_error"]["detail"]
    for r in (health, status):
        assert r.status_code == 200 and PROXY_SECRET not in r.text
