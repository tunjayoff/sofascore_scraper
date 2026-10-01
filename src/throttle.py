"""
Süreçler arası ortak istek bütçesi (issue #16).

Her kod yolu kendi isteklerini kendi sınırlıyordu (izleyici: istekler arası ≥ 1 sn; toplu
indirme: MAX_CONCURRENT; program çekme: kendi semaforu). Aynı anda çalışan süreçler (spor
başına bir `--watch`, web işi, cron'dan `--refresh-only`) birbirini görmediği için toplam
hız süreç sayısıyla çarpılıyordu. Buradaki sınırlayıcı, SofaScore'a giden her isteğin
geçtiği en alt noktalardan çağrılır (src/utils.py: curl istekleri; src/challenge_solver.py:
tarayıcı köprüsünün fetch'i) ve durumunu bir dosyada tutar: aynı kullanıcının tüm süreçleri
tek bütçeyi paylaşır.

Algoritma (GCRA): dosyada tek sayı durur — "sıradaki boş an" (tat). Her istek kilidi alır,
kendi anını ayırır, tat'ı bir aralık ileri iter ve kilidi HEMEN bırakır; bekleme kilit
dışında yapılır. Böylece kilit yalnızca birkaç mikrosaniye tutulur.

Dayanıklılık:
  - Kilit işletim sistemi kilididir (POSIX flock / Windows msvcrt.locking): süreç çökerse
    çekirdek kilidi kendiliğinden bırakır, "bayat kilit" kalmaz.
  - Kilit yine de alınamazsa (takılmış süreç, yazılamayan dizin) istek engellenmez: süreç
    içi sayaçla devam edilir, bir kez uyarı loglanır ve bir süre sonra dosya yeniden denenir.
  - Dosya bozuksa ya da sistem saati geri alınmışsa durum sıfırlanır; hiçbir istek
    _MAX_WAIT_SECONDS'tan uzun bekletilmez.

Ayar (.env): REQUEST_RATE_LIMIT = tüm süreçlerin toplamı için saniyede istek; boş = 5
(varsayılan); 0 ya da "off" = kapalı. Varsayılanın üstü ve "kapalı", SofaScore'un engelleme
riskini bilerek üstlenmek demektir.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_RATE = "REQUEST_RATE_LIMIT"
ENV_DIR = "SOFASCORE_THROTTLE_DIR"

# Varsayılan: tüm süreçlerin toplamı için saniyede 5 istek (boşta geçen süreden sonra bir
# saniyelik, yani 5 isteklik patlama payıyla). Bilinçli olarak toplu indirmenin kendi tavanının
# çok altında: scripts/bench_bulk_rate.py (ağ yok; taşıyıcı sahte) varsayılan ayarlarla
# (MAX_CONCURRENT=10, WAIT_TIME_MIN=0.2, WAIT_TIME_MAX=0.5; 100 futbol maçı = 700 istek)
# sınırlayıcı kapalıyken ortalama 19-62 istek/sn (11-37 sn) ölçüyor; varsayılan bütçeyle
# sahte gecikmeden bağımsız olarak 5,0 istek/sn (140 sn; en yoğun saniyede 9 istek: baştaki
# patlama payı). Toplu indirme artık MAX_CONCURRENT'ten bağımsız ~5 istek/sn ile ilerler:
# 380 maçlık bir futbol sezonu ≈ 2700 istek ≈ 9 dakika. Daha hızlısı için REQUEST_RATE_LIMIT
# yükseltilir ya da 0/off ile kapatılır; ikisi de SofaScore'un engelleme riskini artırır.
DEFAULT_RATE_LIMIT = 5.0

_OFF_WORDS = ("off", "false", "no", "none", "disabled")
_LOCK_TIMEOUT_SECONDS = 1.0
_LOCK_POLL_SECONDS = 0.001
# Kilit alınamadıysa dosya bu süre boyunca yeniden denenmez (her istek 1 sn beklemesin)
_SHARED_RETRY_AFTER_SECONDS = 30.0
# Durum dosyasındaki "yazıldığı an" şimdiden bu kadar ilerideyse sistem saati geri alınmıştır
_CLOCK_SKEW_TOLERANCE_SECONDS = 1.0
# Hiçbir istek bir aralık + bu süreden uzun bekletilmez (bozuk durum / saat sıçramasına karşı son emniyet)
_MAX_WAIT_SECONDS = 300.0

State = Dict[str, float]

_warned_invalid_rate: Optional[str] = None


def configured_rate() -> float:
    """REQUEST_RATE_LIMIT (istek/sn). 0 ya da "off" = sınırlayıcı kapalı; boş/geçersiz = varsayılan."""
    global _warned_invalid_rate
    raw = os.getenv(ENV_RATE, "").strip().lower()
    if not raw:
        return DEFAULT_RATE_LIMIT
    if raw in _OFF_WORDS:
        return 0.0
    try:
        rate = float(raw)
    except ValueError:
        rate = math.nan
    if not math.isfinite(rate):
        if _warned_invalid_rate != raw:
            _warned_invalid_rate = raw
            logger.warning(f"{ENV_RATE} geçersiz ({raw!r}), varsayılan {DEFAULT_RATE_LIMIT:g} kullanılacak.")
        return DEFAULT_RATE_LIMIT
    return max(0.0, rate)


def state_dir() -> str:
    """Durum dosyalarının dizini: aynı kullanıcının tüm süreçleri için ortak."""
    return os.getenv(ENV_DIR, "").strip() or os.path.join(
        os.path.expanduser("~"), ".cache", "sofascore_scraper", "throttle"
    )


# --- işletim sistemi kilidi ---------------------------------------------------------------

if os.name == "nt":  # pragma: no cover - yalnızca Windows
    import msvcrt

    def _try_lock(f: Any) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # kilitliyse OSError

    def _unlock(f: Any) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(f: Any) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # kilitliyse BlockingIOError

    def _unlock(f: Any) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: str, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """
    Süreçler arası dışlayıcı kilit. Sahibi ölürse işletim sistemi bırakır; `timeout` içinde
    alınamazsa TimeoutError. Her çağrı dosyayı yeniden açar: fork sonrası paylaşılan bir
    tanıtıcı, üst ve alt süreci aynı kilidin sahibi yapardı.
    """
    f = open(path, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(f)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"kilit {timeout:g} sn içinde alınamadı: {path}") from None
                time.sleep(_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            try:
                _unlock(f)
            except OSError:
                pass
    finally:
        f.close()


# --- GCRA -----------------------------------------------------------------------------------

def advance(state: State, now: float, interval: float, burst: int = 1) -> Tuple[float, float, State]:
    """
    Bir istek için sıradaki anı ayırır. (bekleme_sn, ayrılan_an, yeni_durum) döndürür.

    `interval` = 1 / hız. `burst`: boşta geçen süreden sonra art arda beklemeden geçebilecek
    istek sayısı (1 = istekler arası her zaman en az `interval`).
    """
    tat = state.get("tat", 0.0)
    written_at = state.get("at", 0.0)
    if not _is_number(tat) or not _is_number(written_at):
        tat, written_at = 0.0, 0.0
    if written_at > now + _CLOCK_SKEW_TOLERANCE_SECONDS:
        tat = now  # durum "gelecekte" yazılmış: sistem saati geri alınmış
    tat = max(tat, now)
    slot = max(now, tat - (max(1, burst) - 1) * interval)
    if slot - now > _MAX_WAIT_SECONDS + interval:  # + interval: çok düşük hızlarda tek aralık meşrudur
        tat = slot = now
    return slot - now, slot, {"tat": tat + interval, "at": now}


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class RequestThrottle:
    """
    Adlandırılmış bir bütçe ("şerit"). Aynı adlı şeritler, aynı durum dizinini kullanan tüm
    süreçlerde tek bütçeyi paylaşır.

    rate: istek/sn (sayı ya da her çağrıda okunan fonksiyon; ≤ 0 = kapalı).
    burst: None → bir saniyelik bütçe (en az 1). Hız ≤ 1 iken istekler eşit aralıklıdır.
    shared: False → yalnızca süreç içi (dosya yok). Fonksiyon da olabilir.
    """

    def __init__(
        self,
        name: str,
        rate: "float | Callable[[], float]",
        burst: Optional[int] = None,
        *,
        shared: "bool | Callable[[], bool]" = True,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        directory: Optional[str] = None,
        lock_timeout: float = _LOCK_TIMEOUT_SECONDS,
    ) -> None:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"geçersiz şerit adı: {name!r}")
        self.name = name
        self._rate = rate
        self._burst = burst
        self._shared = shared
        self._clock = clock
        self._sleep = sleep
        self._directory = directory
        self._lock_timeout = lock_timeout
        self._local: State = {}
        self._thread_lock = threading.Lock()
        self._shared_failed_at: Optional[float] = None
        self.shared_error: Optional[str] = None

    # -- ayarlar --

    def rate(self) -> float:
        rate = self._rate() if callable(self._rate) else self._rate
        return rate if _is_number(rate) and rate > 0 else 0.0

    def burst(self) -> int:
        if self._burst is not None:
            return max(1, int(self._burst))
        return max(1, int(self.rate()))

    def is_shared(self) -> bool:
        return bool(self._shared() if callable(self._shared) else self._shared)

    def _paths(self) -> Tuple[str, str]:
        base = os.path.join(self._directory or state_dir(), self.name)
        return base + ".lock", base + ".json"

    # -- durum dosyası (yalnızca kilit altında) --

    @staticmethod
    def _read(path: str) -> State:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}  # yok ya da yarım yazılmış: sıfırdan başla

    @staticmethod
    def _write(path: str, state: State) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def _shared_usable(self) -> bool:
        if not self.is_shared():
            return False
        if self._shared_failed_at is None:
            return True
        return time.monotonic() - self._shared_failed_at >= _SHARED_RETRY_AFTER_SECONDS

    # -- ayırma --

    def reserve_slot(self) -> Tuple[float, float]:
        """Sıradaki anı ayırır: (beklenmesi gereken sn, ayrılan an). Beklemez."""
        rate = self.rate()
        if rate <= 0:
            return 0.0, self._clock()
        interval, burst = 1.0 / rate, self.burst()
        with self._thread_lock:
            if self._shared_usable():
                lock_path, state_path = self._paths()
                try:
                    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
                    with file_lock(lock_path, self._lock_timeout):
                        delay, slot, state = advance(self._read(state_path), self._clock(), interval, burst)
                        self._write(state_path, state)
                    if self._shared_failed_at is not None:
                        logger.info(f"Ortak istek bütçesi ({self.name}) yeniden kullanılıyor.")
                    self._shared_failed_at, self.shared_error = None, None
                    self._local = state  # dosya kaybolursa süreç içi sayaç kaldığı yerden sürsün
                    return delay, slot
                except (OSError, TimeoutError) as e:
                    if self._shared_failed_at is None:
                        logger.warning(
                            f"Ortak istek bütçesi ({self.name}) kullanılamıyor: {e}. Bu süreç kendi "
                            f"sayacıyla devam ediyor; {int(_SHARED_RETRY_AFTER_SECONDS)} sn sonra yeniden denenecek."
                        )
                    self._shared_failed_at, self.shared_error = time.monotonic(), str(e)
            delay, slot, self._local = advance(self._local, self._clock(), interval, burst)
            return delay, slot

    def reserve(self) -> float:
        """Sıradaki anı ayırır ve beklenmesi gereken saniyeyi döndürür (çağıran bekler)."""
        return self.reserve_slot()[0]

    def wait(self) -> float:
        """Sıra gelene kadar bekler (engelleyici)."""
        delay = self.reserve()
        if delay > 0:
            self._sleep(delay)
        return delay

    async def wait_async(self) -> float:
        """Sıra gelene kadar bekler (olay döngüsünü engellemeden)."""
        delay = self.reserve()
        if delay > 0:
            await asyncio.sleep(delay)
        return delay


# --- modül düzeyi: tüm API istekleri için tek şerit ----------------------------------------

API_LANE = "api"
_api: Optional[RequestThrottle] = None
_api_lock = threading.Lock()


def api_throttle() -> RequestThrottle:
    """SofaScore'a giden her isteğin geçtiği ortak bütçe (REQUEST_RATE_LIMIT)."""
    global _api
    with _api_lock:
        if _api is None:
            _api = RequestThrottle(API_LANE, configured_rate)
        return _api


def reserve() -> float:
    """Ortak bütçeden sıradaki anı ayırır; beklenmesi gereken saniyeyi döndürür (çağıran bekler)."""
    return api_throttle().reserve()


def wait() -> float:
    delay = reserve()
    if delay > 0:
        time.sleep(delay)
    return delay


async def wait_async() -> float:
    delay = reserve()
    if delay > 0:
        await asyncio.sleep(delay)
    return delay


def lane(
    name: str,
    min_interval: float,
    *,
    shared: bool = True,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> RequestThrottle:
    """
    Kendi alt bütçesi olan çağıranlar için (ör. izleyici: istekler arası ≥ 1 sn). Şerit,
    ortak bütçe açıkken süreçler arasıdır; REQUEST_RATE_LIMIT=0 ile ortak bütçe kapatılırsa
    aralık yine uygulanır ama yalnızca süreç içinde (eski davranış).
    """
    return RequestThrottle(
        name,
        1.0 / min_interval,
        burst=1,
        shared=(lambda: configured_rate() > 0) if shared else False,
        clock=clock,
        sleep=sleep,
    )


def status() -> Dict[str, Any]:
    """Tanılama (/health): ortak bütçenin ayarı ve dosyanın kullanılabilirliği."""
    t = api_throttle()
    rate = t.rate()
    return {
        "enabled": rate > 0,
        "requests_per_second": rate,
        "shared": rate > 0 and t.shared_error is None,
        "error": t.shared_error,
    }


def reset_for_tests() -> None:
    global _api, _warned_invalid_rate
    with _api_lock:
        _api = None
    _warned_invalid_rate = None
