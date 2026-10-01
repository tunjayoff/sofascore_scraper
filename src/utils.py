"""
Sofascore Scraper için yardımcı fonksiyonlar ve araçlar.

İstek katmanı src/client/ altına taşındı (transport.py: istek gövdeleri, oturum, başlıklar; context.py: iptal
kontrolü ve bekleme bildirimi). Bu modül o adları eskisi gibi dışa aktarır: `from src.utils import
make_api_request` ve `utils.make_api_request_async(...)` çalışmaya devam eder. Yeni kod src.client'i kullanır.

Geçiş süresince bu modüle yapılan atamalar (`utils._sleep = sahte`, `patch.object(utils, "_get_proxy_config")`)
taşınan gövdenin modülüne de uygulanır; yoksa yama yalnızca buradaki kopyayı değiştirir ve istek katmanı
gerçeğini kullanmaya devam ederdi (bkz. _UtilsModule).
"""

import os
import sys
import time as time
import types
from typing import Any, Dict, Tuple, TypeVar, Union
from pathlib import Path
import dotenv

from src import breaker as breaker, throttle as throttle
from src.logger import get_logger
from src.paths import env_file_path

# .env dosyasını yükle
dotenv.load_dotenv(env_file_path())

# Logger'ı alın
logger = get_logger("Utils")

from src.config_manager import ConfigManager

# Filtreleme ayarları
FETCH_ONLY_FINISHED: bool = os.getenv("FETCH_ONLY_FINISHED", "true").lower() == "true"
SAVE_EMPTY_ROUNDS: bool = os.getenv("SAVE_EMPTY_ROUNDS", "false").lower() == "true"

# Proxy ayarları
_cm = ConfigManager()

T = TypeVar('T')

# ---- İstek katmanı: src/client'ten yeniden dışa aktarılanlar ----
# `X as X` biçimi: bu adlar buradan taşındı, eski import'lar için duruyor.
from src.client import context as _context, transport as _transport
from src.client.context import (
    FetchCancelled as FetchCancelled,
    _cancel_check as _cancel_check,
    _notify_wait as _notify_wait,
    _wait_notifier as _wait_notifier,
    raise_if_cancelled as raise_if_cancelled,
    set_cancel_check as set_cancel_check,
    set_wait_notifier as set_wait_notifier,
)
from src.client.transport import (
    _ACCEPT_LANGUAGES as _ACCEPT_LANGUAGES,
    API_BASE_URL as API_BASE_URL,
    BROWSER_FIRST_SECONDS as BROWSER_FIRST_SECONDS,
    IMPERSONATE_PROFILES as IMPERSONATE_PROFILES,
    AsyncSession as AsyncSession,
    JsonResponse as JsonResponse,
    WarmableAsyncSession as WarmableAsyncSession,
    _asleep as _asleep,
    _athrottle as _athrottle,
    _browser_first as _browser_first,
    _browser_result as _browser_result,
    _full_url as _full_url,
    _get_proxy_config as _get_proxy_config,
    _get_runtime_request_config as _get_runtime_request_config,
    _is_transient_status as _is_transient_status,
    _mark_browser_first as _mark_browser_first,
    _parse_retry_after_seconds as _parse_retry_after_seconds,
    _request_async as _request_async,
    _request_semaphore as _request_semaphore,
    _request_semaphores as _request_semaphores,
    _request_sync as _request_sync,
    _retry_wait as _retry_wait,
    _sleep as _sleep,
    _throttle as _throttle,
    _warmup_session as _warmup_session,
    cffi_requests as cffi_requests,
    create_session_async as create_session_async,
    get_request_headers as get_request_headers,
    get_sofa_captcha_token as get_sofa_captcha_token,
    get_sofascore_hash as get_sofascore_hash,
    make_api_request as make_api_request,
    make_api_request_async as make_api_request_async,
)


def ensure_directory(directory_path: Union[str, Path]) -> bool:
    """
    Belirtilen dizinin var olduğundan emin olur.
    """
    try:
        path = Path(directory_path)
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Dizin oluşturma hatası ({directory_path}): {str(e)}")
        return False


# ---- Atamaları taşınan gövdeye iletme ----

# İstek katmanının çalışırken yeniden bağladığı modül değişkenleri: burada kopyası tutulmaz, okuma da yazma da
# doğrudan sahibine gider (kopya, sahibi değeri değiştirdiğinde eskirdi).
_LIVE_STATE: Dict[str, types.ModuleType] = {"_browser_first_until": _transport}


def _forwarding_table() -> Dict[str, Tuple[types.ModuleType, ...]]:
    """Bu modülde ve taşınan modüllerde aynı adla aynı nesneyi gösteren her ad → atamanın iletileceği modüller."""
    table: Dict[str, Tuple[types.ModuleType, ...]] = {}
    missing = object()
    for name, value in list(globals().items()):
        if name.startswith("__"):
            continue
        owners = tuple(m for m in (_transport, _context) if vars(m).get(name, missing) is value)
        if owners:
            table[name] = owners
    return table


_FORWARDED = _forwarding_table()


class _UtilsModule(types.ModuleType):
    """
    src.utils'in modül sınıfı: istek katmanına ait bir ada burada yapılan atama, gövdenin gerçekten okuduğu
    modüle (src.client.transport / context) de yapılır. Geçiş kodudur; src.utils'i yamalayan testler
    src.client'e taşındığında kalkar.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        owner = _LIVE_STATE.get(name)
        if owner is not None:
            setattr(owner, name, value)
            return
        super().__setattr__(name, value)
        for module in _FORWARDED.get(name, ()):
            setattr(module, name, value)

    def __getattr__(self, name: str) -> Any:
        # Yalnızca ad bu modülün sözlüğünde yoksa çağrılır
        owner = _LIVE_STATE.get(name)
        if owner is not None:
            return getattr(owner, name)
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")


sys.modules[__name__].__class__ = _UtilsModule
