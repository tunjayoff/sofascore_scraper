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

import logging
from pathlib import Path
from typing import List, Mapping, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

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
