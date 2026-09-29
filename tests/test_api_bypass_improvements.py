"""
API bypass iyileştirmelerinin birim testleri.
- Profil havuzu güncellemesi
- Header çeşitlendirme (Sec-Fetch-*, Accept-Language randomization)
- AdaptiveRateLimiter davranışı
- WarmableAsyncSession context manager uyumu
"""

import asyncio
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------- IMPERSONATE_PROFILES ----------

def test_modern_chrome_in_profiles():
    """Modern Chrome profilleri havuzda bulunmalıdır."""
    from src.utils import IMPERSONATE_PROFILES
    chrome_profiles = [p for p in IMPERSONATE_PROFILES if p.startswith("chrome")]
    assert len(chrome_profiles) >= 3


def test_profiles_have_safari():
    """TLS çeşitliliği için en az bir Safari profili olmalı."""
    from src.utils import IMPERSONATE_PROFILES
    safari = [p for p in IMPERSONATE_PROFILES if p.startswith("safari")]
    assert len(safari) >= 1, f"Safari profili bulunamadı: {IMPERSONATE_PROFILES}"


def test_profiles_minimum_count():
    """Yeterli çeşitlilik için en az 4 profil olmalı."""
    from src.utils import IMPERSONATE_PROFILES
    assert len(IMPERSONATE_PROFILES) >= 4


# ---------- get_request_headers ----------

def test_headers_contain_sec_fetch():
    """Sec-Fetch-* header'ları modern tarayıcı davranışını taklit eder."""
    from src.utils import get_request_headers
    headers = get_request_headers()
    assert "Sec-Fetch-Dest" in headers
    assert headers["Sec-Fetch-Dest"] == "empty"
    assert "Sec-Fetch-Mode" in headers
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert "Sec-Fetch-Site" in headers
    assert headers["Sec-Fetch-Site"] == "same-origin"


def test_headers_always_have_dynamic_xhr():
    """X-Requested-With dinamik 6 karakterlik hex hash olmalıdır."""
    from src.utils import get_request_headers, get_sofascore_hash
    h = get_sofascore_hash()
    assert len(h) == 6
    assert all(c in "0123456789abcdef" for c in h)
    headers = get_request_headers()
    assert headers.get("X-Requested-With") == h


def test_token_injected_into_headers():
    """SOFA_CAPTCHA_TOKEN ayarlandığında X-Captcha ve Cookie eklenmeli."""
    from unittest.mock import patch
    with patch.dict("os.environ", {"SOFA_CAPTCHA_TOKEN": "mock_jwt_token_123"}):
        from src.utils import get_request_headers
        headers = get_request_headers()
        assert headers.get("X-Captcha") == "mock_jwt_token_123"
        assert "sofa_captcha=mock_jwt_token_123" in headers.get("Cookie", "")


def test_accept_language_randomized():
    """Accept-Language header'ı her çağrıda sabit olmamalı (rastgeleleştirilmiş)."""
    from src.utils import get_request_headers
    # 20 header seti oluştur, en az 2 farklı Accept-Language değeri görmemiz lazım
    random.seed(42)
    langs = {get_request_headers()["Accept-Language"] for _ in range(20)}
    assert len(langs) >= 2, f"Accept-Language çeşitlenmiyor: {langs}"


def test_cache_control_sometimes_present():
    """Cache-Control opsiyonel olarak eklenmeli (%50 olasılık)."""
    from src.utils import get_request_headers
    random.seed(42)
    results = [("Cache-Control" in get_request_headers()) for _ in range(30)]
    # En az bir True ve en az bir False olmalı
    assert True in results, "Cache-Control hiç eklenmiyor"
    assert False in results, "Cache-Control her zaman ekleniyor (rastgelelik yok)"


# ---------- AdaptiveRateLimiter ----------

def test_adaptive_limiter_success_reduces_delay():
    """Ardışık başarılar delay'i azaltmalı."""
    from src.utils import AdaptiveRateLimiter
    limiter = AdaptiveRateLimiter(base_delay=1.0)
    # Önce delay'i yükselt, sonra başarılarla düşür
    limiter.failure(403)  # 1.0 → 2.0
    limiter.failure(403)  # 2.0 → 4.0
    elevated = limiter.current_delay
    # 10 ardışık başarı
    for _ in range(10):
        limiter.success()
    assert limiter.current_delay < elevated, "Başarılar delay'i düşürmedi"
    assert limiter.current_delay >= limiter.base_delay, "Delay base'in altına indi"


def test_adaptive_limiter_429_triples_delay():
    """429 hatası delay'i 3 katına çıkarmalı."""
    from src.utils import AdaptiveRateLimiter
    limiter = AdaptiveRateLimiter(base_delay=1.0, max_delay=30.0)
    initial = limiter.current_delay
    limiter.failure(429)
    assert limiter.current_delay == initial * 3


def test_adaptive_limiter_403_doubles_delay():
    """403 hatası delay'i 2 katına çıkarmalı."""
    from src.utils import AdaptiveRateLimiter
    limiter = AdaptiveRateLimiter(base_delay=1.0, max_delay=30.0)
    initial = limiter.current_delay
    limiter.failure(403)
    assert limiter.current_delay == initial * 2


def test_adaptive_limiter_respects_max_delay():
    """Delay hiçbir zaman max_delay'i aşmamalı."""
    from src.utils import AdaptiveRateLimiter
    limiter = AdaptiveRateLimiter(base_delay=1.0, max_delay=10.0)
    for _ in range(20):
        limiter.failure(429)
    assert limiter.current_delay <= limiter.max_delay


def test_adaptive_limiter_get_delay_includes_jitter():
    """get_delay(), current_delay'den büyük olmalı (jitter eklenir)."""
    from src.utils import AdaptiveRateLimiter
    random.seed(123)
    limiter = AdaptiveRateLimiter(base_delay=5.0)
    delay = limiter.get_delay()
    assert delay >= limiter.current_delay, "Jitter eklenmemiş"


def test_adaptive_limiter_consecutive_counters():
    """success/failure çağrıları consecutive sayaçları düzgün sıfırlamalı."""
    from src.utils import AdaptiveRateLimiter
    limiter = AdaptiveRateLimiter()
    limiter.success()
    limiter.success()
    assert limiter.consecutive_success == 2
    assert limiter.consecutive_fail == 0
    limiter.failure(500)
    assert limiter.consecutive_success == 0
    assert limiter.consecutive_fail == 1


# ---------- WarmableAsyncSession ----------

def test_warmable_session_is_context_manager():
    """create_session_async() sonucu async context manager olmalı."""
    from src.utils import create_session_async
    session_cm = create_session_async()
    assert hasattr(session_cm, "__aenter__"), "async context manager değil"
    assert hasattr(session_cm, "__aexit__"), "async context manager değil"


@pytest.mark.asyncio
async def test_warmable_session_warmup_called():
    """Warm-up fonksiyonu session açılışında çağrılmalı."""
    with patch("src.utils._warmup_session", new_callable=AsyncMock) as mock_warmup:
        from src.utils import create_session_async
        async with create_session_async() as session:
            assert session is not None
        mock_warmup.assert_called_once()


@pytest.mark.asyncio
async def test_warmable_session_returns_async_session():
    """Context manager'dan dönen obje curl_cffi AsyncSession olmalı."""
    from curl_cffi.requests import AsyncSession
    with patch("src.utils._warmup_session", new_callable=AsyncMock):
        from src.utils import create_session_async
        async with create_session_async() as session:
            assert isinstance(session, AsyncSession)


# ---------- Modül düzeyinde paylaşılan limiter ----------

def test_shared_limiter_singleton():
    """get_adaptive_limiter() her çağrıda aynı instance'ı döndürmeli."""
    from src.utils import get_adaptive_limiter
    limiter_a = get_adaptive_limiter()
    limiter_b = get_adaptive_limiter()
    assert limiter_a is limiter_b
