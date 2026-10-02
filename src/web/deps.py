"""
v1 rotalarının ve uygulamanın ara katmanının paylaştığı nesneler (docs/design/02-services.md bölüm 6).

  * servis bağlamı: web sürecinin iş yöneticisi (süreç genelindeki iş deposunun üzerinde), yapılandırma
    yöneticisi ve geçerli ayarlar. Bugün bunların sahibi eski rotaların ortak modülüdür
    (src/web/routes/common.py); v1 aynı nesneleri kullanır, böylece v1'den başlatılan bir iş eski
    arayüzde de görünür ve iki yüz aynı "tek iş" kuralına uyar. Nesneler çağrı anında okunur (içe aktarma
    anında değil): testler ve DATA_DIR değişimi onları değiştirebilir.
  * erişim belirteci: Settings'ten (`[server] token_env`, varsayılan ad SOFASCORE_API_TOKEN) okunur.
  * başarısız giriş sınırı: aynı istemciden art arda gelen yanlış belirteçler bir süre reddedilir.

Bu modül hafiftir: istek katmanını, indiricileri ve FastAPI'yi içe aktarmaz.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional

if TYPE_CHECKING:
    from src.config import LoadedSettings
    from src.config_manager import ConfigManager
    from src.jobs.manager import JobManager
    from src.store import JobStore


# --- servis bağlamı ------------------------------------------------------------------------------


def job_store() -> "JobStore":
    """Web sürecinin iş deposu (çalışan işin yansısı ve `writer` kilidi ondadır)."""
    from src.web.routes import common

    return common._job_store


def job_manager() -> "JobManager":
    """Web sürecinin iş yöneticisi: işleri başlatır, okur ve iptal eder (hangi süreçte çalışırlarsa çalışsınlar)."""
    from src.jobs.manager import JobManager

    return JobManager(job_store())


def refresh_job_mirror() -> None:
    """Eski arayüzün okuduğu canlı iş görüntüsünü (SCRAPER_STATE) iş deposuyla eşitler."""
    from src.web.routes import common

    common._refresh_scraper_state()


def config_manager() -> "ConfigManager":
    from src.web.routes import common

    return common.config_manager


def loaded_settings() -> "LoadedSettings":
    """Geçerli ayarlar ve her birinin kaynağı (src/config/loader.py)."""
    from src.config import active

    return active()


def server_token() -> str:
    """
    Erişim belirteci, Settings'ten ("" = kapalı): `[server] token_env` bir değişken adı veriyorsa onun
    değeri, vermiyorsa SOFASCORE_API_TOKEN. Adı verilen değişken boşsa ConfigError fırlar (koruma bir yazım
    hatasıyla sessizce kapanmaz).
    """
    return loaded_settings().settings.server.token


# --- başarısız giriş sınırı ----------------------------------------------------------------------


_MAX_DOUBLINGS = 32


@dataclass
class _Attempts:
    failures: int = 0
    last_failure: float = 0.0
    locked_until: float = 0.0


class AttemptLimiter:
    """
    Yanlış erişim belirteci denemelerini istemci başına sınırlar (bellekte; süreç yeniden başlayınca sıfırlanır).

    Bir istemcinin art arda `free_attempts` yanlış denemesinden sonra yeni denemeleri bir süre reddedilir;
    süre dolduktan sonraki her yanlış deneme süreyi ikiye katlar (`base_lock` saniyeden `max_lock`a kadar).
    Doğru belirteç sayacı sıfırlar; `forget_after` saniye boyunca yanlış deneme gelmezse sayaç unutulur.
    Kilit sürerken gelen denemeler değerlendirilmez ve sayılmaz (kilidi uzatmaz).

    "İstemci" bağlantının karşı ucundaki adrestir. Ters vekilin arkasında bütün kullanıcılar aynı adresten
    görünebilir: o durumda kilit hepsini etkiler (oturum cookie'si taşıyan istekler etkilenmez).
    """

    def __init__(
        self,
        *,
        free_attempts: int = 5,
        base_lock: float = 30.0,
        max_lock: float = 3600.0,
        forget_after: float = 900.0,
        max_clients: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.free_attempts = max(1, int(free_attempts))
        self.base_lock = float(base_lock)
        self.max_lock = float(max_lock)
        self.forget_after = float(forget_after)
        self.max_clients = max(1, int(max_clients))
        self._clock = clock
        self._lock = threading.Lock()
        self._clients: Dict[str, _Attempts] = {}

    def _entry(self, client: str, now: float) -> Optional[_Attempts]:
        """İstemcinin kaydı; süresi dolmuşsa silinir ve None döner."""
        entry = self._clients.get(client)
        if entry is None:
            return None
        if now >= max(entry.locked_until, entry.last_failure + self.forget_after):
            del self._clients[client]
            return None
        return entry

    def retry_after(self, client: str) -> float:
        """İstemci kilitliyse kalan saniye, değilse 0."""
        with self._lock:
            now = self._clock()
            entry = self._entry(client, now)
            return max(0.0, entry.locked_until - now) if entry is not None else 0.0

    def failure(self, client: str) -> float:
        """Yanlış bir denemeyi kaydeder; bu denemeyle bir kilit başladıysa süresini (saniye), başlamadıysa 0 döndürür."""
        with self._lock:
            now = self._clock()
            entry = self._entry(client, now)
            if entry is None:
                if len(self._clients) >= self.max_clients:
                    self._evict(now)
                entry = self._clients[client] = _Attempts()
            entry.failures += 1
            entry.last_failure = now
            if entry.failures < self.free_attempts:
                return 0.0
            # Üs sınırlıdır: sayaç haftalarca büyüse de çarpım taşmaz (max_lock zaten çok daha önce geçerlidir)
            doublings = min(entry.failures - self.free_attempts, _MAX_DOUBLINGS)
            lock = min(self.base_lock * (2 ** doublings), self.max_lock)
            entry.locked_until = now + lock
            return lock

    def success(self, client: str) -> None:
        """Doğru belirteç: istemcinin sayacı sıfırlanır."""
        with self._lock:
            self._clients.pop(client, None)

    def _evict(self, now: float) -> None:
        """Yer açar: önce süresi dolmuş kayıtlar, yetmezse en eski yanlış denemenin sahibi."""
        for client in list(self._clients):
            self._entry(client, now)  # süresi dolmuşsa siler
        if len(self._clients) >= self.max_clients:
            oldest = min(self._clients, key=lambda c: self._clients[c].last_failure)
            del self._clients[oldest]

    def reset(self) -> None:
        """Bütün kayıtları siler (testler)."""
        with self._lock:
            self._clients.clear()


# Süreç genelinde tek sınırlayıcı: giriş uç noktası ve `Authorization: Bearer` denemeleri aynı sayacı besler
attempt_limiter = AttemptLimiter()


def client_key(client: Optional[Any]) -> str:
    """İsteğin karşı ucu (`request.client`): adresi; bilinmiyorsa (ör. Unix soketi) hepsi tek istemci sayılır."""
    host = getattr(client, "host", None)
    return str(host) if host else "unknown"


def presents_bearer(headers: Mapping[str, str]) -> bool:
    """İstek bir `Authorization: Bearer <değer>` taşıyor mu? (Değeri değerlendirmez.)"""
    scheme, _, value = (headers.get("authorization") or "").strip().partition(" ")
    return scheme.lower() == "bearer" and bool(value.strip())


__all__ = [
    "AttemptLimiter",
    "attempt_limiter",
    "client_key",
    "config_manager",
    "job_manager",
    "job_store",
    "loaded_settings",
    "presents_bearer",
    "refresh_job_mirror",
    "server_token",
]
