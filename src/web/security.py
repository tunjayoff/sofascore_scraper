"""
Web uygulamasının güvenlik sınırları. Kullanıcı hesabı yoktur; uygulama varsayılan olarak yalnızca
bu bilgisayarı dinler. Ağa açmak yöneticinin kararıdır (güvenlik duvarı, ters vekil); burada
duranlar o kararı zayıflatmayan ve kullanıcının kendi tarayıcısı üzerinden gelen saldırıları
(hiçbir güvenlik duvarının durduramadığı) kesen parçalardır:

  Host izin listesi   DNS rebinding: yalnızca bilinen adlarla gelen isteklere yanıt verilir.
  Kaynak denetimi     CSRF: başka bir sitenin tetiklediği durum değiştiren istek reddedilir.
  Erişim belirteci    İsteğe bağlı (SOFASCORE_API_TOKEN): ayarlıysa her /api isteği onu taşır.
  Güvenlik başlıkları nosniff, çerçeveleme yasağı, Referrer-Policy, Content-Security-Policy.

Bu modül yalnızca standart kütüphaneye bağlıdır: main.py, sunucuyu başlatmadan önce Host izin
listesini buradan hesaplar.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
from pathlib import Path
from typing import List, Mapping, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --- Host izin listesi ----------------------------------------------------------------------

ALLOWED_HOSTS_ENV = "SOFASCORE_ALLOWED_HOSTS"
# Tarayıcının bu bilgisayara ulaşırken gönderdiği Host adları (IPv6 köşeli ayraçla gelir)
LOOPBACK_HOSTS: Tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")
# "Her arayüz" adresleri: bu adreslerle açılan sunucuya hangi adla ulaşılacağı bilinemez
_WILDCARD_BINDS = ("", "0.0.0.0", "::", "[::]")


class AllowedHostsRequired(Exception):
    """Sunucu her arayüzde dinleyecek ama hangi Host adlarına yanıt vereceği söylenmedi."""


def parse_hosts(raw: Optional[str]) -> List[str]:
    return [h.strip() for h in (raw or "").split(",") if h.strip()]


def is_loopback_bind(host: str) -> bool:
    """Sunucu yalnızca bu bilgisayarı mı dinliyor (localhost, 127.0.0.0/8, ::1)?"""
    name = (host or "").strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def allowed_hosts() -> List[str]:
    """
    Yanıt verilen Host adları: SOFASCORE_ALLOWED_HOSTS, yoksa (ya da boşsa) yalnızca yerel adlar.
    Kullanıcının yazdığı değer olduğu gibi kullanılır ("*" = hepsi; güvensiz).
    """
    return parse_hosts(os.environ.get(ALLOWED_HOSTS_ENV)) or list(LOOPBACK_HOSTS)


def allowed_hosts_for_bind(bind_host: str, explicit: Optional[str], allow_any: bool = False) -> Optional[str]:
    """
    main.py --web --host için SOFASCORE_ALLOWED_HOSTS değeri; None = ortam olduğu gibi kalır.

      - Kullanıcı SOFASCORE_ALLOWED_HOSTS yazdıysa her zaman o geçerlidir (üzerine yazılmaz).
      - Yerel adres: varsayılan (yalnızca yerel adlar).
      - --allow-any-host: "*" (güvensiz; DNS rebinding koruması kapanır).
      - Belirli bir adres (ör. 192.168.1.5): yerel adlar + o adres. IP ile yazılmış bir Host
        başlığını DNS rebinding üretemez, bu yüzden bu liste korumayı zayıflatmaz.
      - Her arayüz (0.0.0.0, ::): hangi adla ulaşılacağı bilinemez → AllowedHostsRequired.
    """
    if parse_hosts(explicit) or is_loopback_bind(bind_host):
        return None
    if allow_any:
        return "*"
    host = (bind_host or "").strip()
    if host in _WILDCARD_BINDS:
        raise AllowedHostsRequired(host)
    bare = host.strip("[]")
    # IPv6 adresi Host başlığında köşeli ayraçla gelir
    literal = f"[{bare}]" if ":" in bare else bare
    return ",".join(LOOPBACK_HOSTS + (literal,))


# --- Kaynak (origin) denetimi ---------------------------------------------------------------

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_cross_origin_write(method: str, headers: Mapping[str, str]) -> bool:
    """
    Durum değiştiren istek başka bir siteden mi tetiklendi?

    Tarayıcılar bunu iki başlıkla söyler; sayfadaki betik ikisini de değiştiremez:
      - Sec-Fetch-Site: yalnızca "same-origin" (uygulamanın kendi sayfası) ve "none" (kullanıcının
        kendisi) kabul edilir. Varsa o belirler: ters vekil Host'u yeniden yazsa da doğru kalır.
      - Origin: Sec-Fetch-Site göndermeyen tarayıcılar (düz HTTP ile yerel olmayan adres) için;
        isteğin Host'u ile eşleşmelidir. "null" (sandbox, yönlendirme) eşleşmez.
    İkisi de yoksa istek tarayıcıdan gelmiyordur (curl, betik): CSRF söz konusu değildir.
    """
    if method.upper() not in UNSAFE_METHODS:
        return False
    site = (headers.get("sec-fetch-site") or "").strip().lower()
    if site:
        return site not in ("same-origin", "none")
    origin = (headers.get("origin") or "").strip()
    if origin:
        return urlparse(origin).netloc.lower() != (headers.get("host") or "").strip().lower()
    return False


# --- Erişim belirteci -----------------------------------------------------------------------

TOKEN_ENV = "SOFASCORE_API_TOKEN"
SESSION_COOKIE = "sofascore_session"
SESSION_MAX_AGE = 30 * 24 * 3600
# Bundan kısa bir belirteç tahmin edilebilir; başlangıçta uyarılır
MIN_TOKEN_LENGTH = 16
# Belirteç olmadan da yanıt veren /api yolları: oturum durumu, giriş ve çıkış
AUTH_OPEN_PATHS = frozenset({"/api/auth", "/api/auth/login", "/api/auth/logout"})


# Süreç başlarken okunan belirteç (None = henüz okunmadı)
_startup_token: Optional[str] = None


def api_token() -> str:
    """
    Ayarlı erişim belirteci ("" = kapalı). İlk çağrıda (uygulama başlarken, .env yüklendikten
    sonra) okunur ve süreç boyunca sabit kalır; değiştirmek için uygulama yeniden başlatılır.

    Her istekte ortamdan okunmamasının nedeni: ayar kaydı .env'i ortamın üzerine yeniden yükler
    (ConfigManager.reload_config, override=True). .env'deki boş bir `SOFASCORE_API_TOKEN=` satırı,
    ortamdan (kabuk, Docker -e) verilen belirteci çalışırken silip korumayı sessizce kapatırdı.
    """
    global _startup_token
    if _startup_token is None:
        _startup_token = os.environ.get(TOKEN_ENV, "").strip()
    return _startup_token


def _equal(a: str, b: str) -> bool:
    # Sabit süreli karşılaştırma: yanıt süresi belirtecin kaç karakterinin tuttuğunu söylemez
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def token_matches(candidate: Optional[str]) -> bool:
    token = api_token()
    return bool(token) and bool(candidate) and _equal(str(candidate), token)


def session_value(token: str) -> str:
    """
    Oturum cookie'sinin değeri: belirteçten türetilir (HMAC), belirtecin kendisi değildir. Sunucuda
    oturum kaydı tutulmaz; belirteç değişince eski cookie'ler kendiliğinden geçersiz olur.
    """
    return hmac.new(token.encode("utf-8"), b"sofascore-scraper web session v1", hashlib.sha256).hexdigest()


def is_authenticated(headers: Mapping[str, str], cookies: Mapping[str, str]) -> bool:
    """Belirteç ayarlı değilse herkes; ayarlıysa `Authorization: Bearer` ya da oturum cookie'si."""
    token = api_token()
    if not token:
        return True
    scheme, _, value = (headers.get("authorization") or "").strip().partition(" ")
    if scheme.lower() == "bearer" and _equal(value.strip(), token):
        return True
    cookie = cookies.get(SESSION_COOKIE) or ""
    return bool(cookie) and _equal(cookie, session_value(token))


def requires_token(path: str) -> bool:
    return (path == "/api" or path.startswith("/api/")) and path not in AUTH_OPEN_PATHS


# --- Güvenlik başlıkları --------------------------------------------------------------------

# Derleme, betiklerinin eval gerektirmediğini frontend/index.html'deki bu etiketle bildirir
# (vite.config.ts: __INTLIFY_JIT_COMPILATION__). Etiketi taşımayan eski bir derleme vue-i18n
# iletilerini `Function(...)` ile derler; onu bozmamak için politika 'unsafe-eval' ile gevşer.
CSP_MARKER = '<meta name="sofascore-csp" content="no-eval"'

_CSP = (
    ("default-src", "'self'"),
    ("script-src", "'self'"),
    # Vue şablonlarındaki style="..." öznitelikleri; betik için 'unsafe-inline' yoktur
    ("style-src", "'self' 'unsafe-inline'"),
    ("img-src", "'self' data:"),
    # Küçük yazı tipleri derlemede CSS'e data: olarak gömülür
    ("font-src", "'self' data:"),
    # fetch ve SSE (/api/scrape/stream) yalnızca uygulamanın kendisine
    ("connect-src", "'self'"),
    ("object-src", "'none'"),
    ("base-uri", "'self'"),
    ("form-action", "'self'"),
    ("frame-ancestors", "'none'"),
)

# FastAPI'nin /docs (Swagger UI) ve /redoc sayfaları betiklerini CDN'den yükler ve satır içi betik
# kullanır; yalnızca o iki sayfa için geçerli, onların ihtiyaç duyduğu kadar gevşek politika.
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})
_DOCS_CSP = (
    ("default-src", "'self'"),
    ("script-src", "'self' 'unsafe-inline' https://cdn.jsdelivr.net"),
    ("style-src", "'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com"),
    ("img-src", "'self' data: https://fastapi.tiangolo.com"),
    ("font-src", "'self' data: https://fonts.gstatic.com"),
    ("worker-src", "blob:"),
    ("connect-src", "'self'"),
    ("object-src", "'none'"),
    ("base-uri", "'self'"),
    ("frame-ancestors", "'none'"),
)

# index.html'in son okunan hali: (yol, mtime_ns, boyut) → eval gerekiyor mu
_legacy_cache: Tuple[Optional[Tuple[str, int, int]], bool] = (None, False)


def _render(policy: Tuple[Tuple[str, str], ...]) -> str:
    return "; ".join(f"{name} {value}" for name, value in policy)


def spa_needs_eval(dist: Path) -> bool:
    """Sunulan derleme CSP etiketini taşımayan eski bir derleme mi? (index.html değişince yeniden okunur)"""
    global _legacy_cache
    index = dist / "index.html"
    try:
        st = index.stat()
        signature = (str(index), st.st_mtime_ns, st.st_size)
        if _legacy_cache[0] != signature:
            legacy = CSP_MARKER not in index.read_text(encoding="utf-8", errors="replace")
            _legacy_cache = (signature, legacy)
            if legacy:
                logger.warning(
                    "Web arayüzü eski bir derleme: Content-Security-Policy 'unsafe-eval' ile gevşetildi. "
                    "Yeniden derleyin: cd frontend && npm install && npm run build"
                )
        return _legacy_cache[1]
    except OSError:
        return False  # derleme yok: yardım sayfasında betik yoktur


def content_security_policy(path: str, dist: Path) -> str:
    if path in DOCS_PATHS:
        return _render(_DOCS_CSP)
    if spa_needs_eval(dist):
        return _render(tuple((n, v + " 'unsafe-eval'" if n == "script-src" else v) for n, v in _CSP))
    return _render(_CSP)


def security_headers(path: str, dist: Path) -> List[Tuple[str, str]]:
    return [
        # Yanıt, bildirilen türünden başka bir şey (betik, stil) olarak yorumlanmaz
        ("X-Content-Type-Options", "nosniff"),
        # Clickjacking: uygulama başka bir sayfanın içinde çerçevelenemez (CSP frame-ancestors ile aynı)
        ("X-Frame-Options", "DENY"),
        # Uygulamanın adresi (iç ağ adı, yol) dış bağlantılara sızmaz
        ("Referrer-Policy", "no-referrer"),
        # Başka bir site yanıtları <script>/<img> ile kendi sayfasına gömemez
        ("Cross-Origin-Resource-Policy", "same-origin"),
        ("Content-Security-Policy", content_security_policy(path, dist)),
    ]
