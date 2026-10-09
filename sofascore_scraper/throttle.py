"""
Süreçler arası ortak istek bütçesi (issue #16).

Her kod yolu kendi isteklerini kendi sınırlıyordu (izleyici: istekler arası ≥ 1 sn; toplu
indirme: `client.max_concurrent`; program çekme: kendi semaforu). Aynı anda çalışan süreçler (spor
başına bir `--watch`, web işi, cron'dan `--refresh-only`) birbirini görmediği için toplam
hız süreç sayısıyla çarpılıyordu. Buradaki sınırlayıcı, SofaScore'a giden her isteğin
geçtiği en alt noktalardan çağrılır (sofascore_scraper/client/transport.py: curl istekleri;
sofascore_scraper/client/bridge.py: tarayıcı köprüsünün fetch'i) ve durumunu bir dosyada tutar: aynı kullanıcının tüm süreçleri
tek bütçeyi paylaşır.

Algoritma (GCRA): dosyada "sıradaki boş an" (tat) durur. Her istek kilidi alır, kendi anını
ayırır, tat'ı bir aralık ileri iter ve kilidi HEMEN bırakır; bekleme kilit dışında yapılır.
Böylece kilit yalnızca birkaç mikrosaniye tutulur.

Geri verme: sırasını beklerken iptal edilen istek (durdurulan iş, zaman aşımı, Ctrl-C) hiç
gönderilmez; ayırdığı sıra kuyrukta kalırsa sonraki istek, hangi süreçten gelirse gelsin, o
boş sıraların arkasında bekler. `reserve()` bu yüzden bir `Reservation` döndürür: değeri
beklenecek saniyedir, `give_back()` ile sıra bütçeye iade edilir. Kuyruğun sonundaki sıra
iade edilince tat geri çekilir; aradaki bir sıra dosyadaki "free" listesine yazılır ve
sonraki isteğe verilir. Her sıra en çok bir isteğe verildiği için bütçenin sınırı (herhangi
bir T saniyelik pencerede en çok hız × T + patlama payı kadar istek) iadelerle de aşılmaz.
Yalnızca zamanı henüz gelmemiş sıralar iade edilir (bkz. RequestThrottle.give_back).

Öncelik şeridi (FX-23, bulgu F16): kullanıcının beklediği etkileşimli istekler (web arayüzünde arama ve
yazarken öneriler) `interactive()` bloğunda yapılır ve kuyruğun sonuna değil, bekleyen ilk arka plan sırasının
yerine girer. O sıra ve arkasındakiler birer aralık geri kayar, kuyruk bir aralık uzar: bütçe aşılmaz, yalnızca
sıra değişir. Kayma dosyaya "bumps" olarak yazılır (konum, sayaç); arka plan isteği uyanınca `settle` ile
kendisinden sonra yazılmış kaymalara bakar ve gerekirse bir aralık daha bekler. İndirme sürerken bir arama
böylece en çok bir aralık bekler (1 istek/sn'de ~1 sn; önceden kuyruktaki bütün istekler kadar, 4-12 sn).

Dayanıklılık:
  - Kilit işletim sistemi kilididir (POSIX flock / Windows msvcrt.locking): süreç çökerse
    çekirdek kilidi kendiliğinden bırakır, "bayat kilit" kalmaz.
  - Kilit yine de alınamazsa (takılmış süreç, yazılamayan dizin) istek engellenmez: süreç
    içi sayaçla devam edilir, bir kez uyarı loglanır ve bir süre sonra dosya yeniden denenir.
  - Dosya bozuksa ya da sistem saati geri alınmışsa durum sıfırlanır; hiçbir istek
    _MAX_WAIT_SECONDS'tan uzun bekletilmez.

Ayar: `client.rate` (sofascore.toml, Ayarlar sayfası ya da SOFASCORE_CLIENT__RATE) = tüm süreçlerin
toplamı için saniyede istek; varsayılan 5; 0 ya da "off" = kapalı. Varsayılanın üstü ve "kapalı",
SofaScore'un engelleme riskini bilerek üstlenmek demektir. Durum dosyalarının dizini `client.throttle_dir`.
Değerler ayar yükleyicisinden çağrı anında okunur (2.x'in REQUEST_RATE_LIMIT ve SOFASCORE_THROTTLE_DIR adları
3.1'de okunmaz).
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

# Varsayılan: tüm süreçlerin toplamı için saniyede 5 istek (boşta geçen süreden sonra bir
# saniyelik, yani 5 isteklik patlama payıyla). Bilinçli olarak toplu indirmenin kendi tavanının
# çok altında: scripts/bench_bulk_rate.py (ağ yok; taşıyıcı sahte) varsayılan ayarlarla
# (client.max_concurrent = 10, wait_time_min = 0.2, wait_time_max = 0.5; 100 futbol maçı = 700 istek)
# sınırlayıcı kapalıyken ortalama 19-62 istek/sn (11-37 sn) ölçüyor; varsayılan bütçeyle
# sahte gecikmeden bağımsız olarak 5,0 istek/sn (140 sn; en yoğun saniyede 9 istek: baştaki
# patlama payı). Toplu indirme artık `client.max_concurrent`ten bağımsız ~5 istek/sn ile ilerler:
# 380 maçlık bir futbol sezonu ≈ 2700 istek ≈ 9 dakika. Daha hızlısı için `client.rate`
# yükseltilir ya da 0/off ile kapatılır; ikisi de SofaScore'un engelleme riskini artırır.
DEFAULT_RATE_LIMIT = 5.0

_LOCK_TIMEOUT_SECONDS = 1.0
_LOCK_POLL_SECONDS = 0.001
# Kilit alınamadıysa dosya bu süre boyunca yeniden denenmez (her istek 1 sn beklemesin)
_SHARED_RETRY_AFTER_SECONDS = 30.0
# Durum dosyasındaki "yazıldığı an" şimdiden bu kadar ilerideyse sistem saati geri alınmıştır
_CLOCK_SKEW_TOLERANCE_SECONDS = 1.0
# Hiçbir istek bir aralık + bu süreden uzun bekletilmez (bozuk durum / saat sıçramasına karşı son emniyet)
_MAX_WAIT_SECONDS = 300.0

# İade edilen sıralar karşılaştırılırken hoş görülen fark (epoch büyüklüğündeki sayılarda kayan
# nokta çözünürlüğü ~2e-7 sn); çok yüksek hızlarda aralığın dörtte biriyle sınırlanır
_POSITION_EPSILON = 1e-6
# Dosyadaki "free" listesinin üst sınırı; dolunca yeni iadeler yok sayılır (sıra boşa gider, bütçe aşılmaz)
_MAX_FREE_POSITIONS = 1024
# Öncelik şeridinin kayma kaydı: en çok bu kadar kayma tutulur; konumu şimdiden bu kadar eski olan atılır
# (bir sıra en çok _MAX_WAIT_SECONDS bekler; iki katı, kaymalarla uzayan beklemeye pay bırakır)
_MAX_BUMPS = 256
_BUMP_KEEP_SECONDS = 2 * _MAX_WAIT_SECONDS

# {"tat": sıradaki boş an, "at": yazıldığı an, "free": iade edilmiş ara sıralar (yoksa anahtar da yok),
#  "seq": öncelikli isteklerin sayacı, "bumps": [[konum, sayaç], ...] öncelikli isteklerin kaydırdığı yerler,
#  "prio": son öncelikli isteğin konumu (öncelikliler kendi aralarında sırayla gider); hiç öncelikli istek
#  olmadıysa üçü de yok}
State = Dict[str, Any]

# Etkileşimli istek: sıra öncelik şeridinden alınır (bkz. modül belgesi, `interactive`)
_priority: "contextvars.ContextVar[bool]" = contextvars.ContextVar("throttle_priority", default=False)


@contextlib.contextmanager
def interactive() -> Iterator[None]:
    """
    Blok içindeki SofaScore istekleri kullanıcının beklediği isteklerdir (arama, öneri): ortak bütçede bekleyen
    arka plan isteklerinin önüne geçerler. Bağlam değişkenidir: köprünün arka plan döngüsüne de taşınır.
    """
    token = _priority.set(True)
    try:
        yield
    finally:
        _priority.reset(token)


def is_interactive() -> bool:
    """Bu bağlamdaki istek öncelik şeridinde mi."""
    return _priority.get()

def configured_rate() -> float:
    """`client.rate` (istek/sn); 0 = sınırlayıcı kapalı. Çağrı anında ayar yükleyicisinden okunur."""
    from sofascore_scraper.config import loader

    return max(0.0, float(loader.active_settings().client.rate))


DEFAULT_STATE_DIR = os.path.join("~", ".cache", "sofascore_scraper", "throttle")


def state_dir() -> str:
    """Durum dosyalarının dizini (`client.throttle_dir`, boşsa varsayılan): aynı kullanıcının tüm süreçleri için ortak."""
    from sofascore_scraper.config import loader

    return os.path.expanduser(loader.active_settings().client.throttle_dir.strip() or DEFAULT_STATE_DIR)


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

def _load(state: State, now: float) -> Tuple[float, List[float]]:
    """Durumu okur: (tat ≥ şimdi, hâlâ kullanılabilir iade edilmiş sıralar — küçükten büyüğe)."""
    tat = state.get("tat", 0.0)
    written_at = state.get("at", 0.0)
    free = state.get("free")
    if not _is_number(tat) or not _is_number(written_at):
        tat, written_at, free = 0.0, 0.0, None
    if written_at > now + _CLOCK_SKEW_TOLERANCE_SECONDS:
        tat, free = now, None  # durum "gelecekte" yazılmış: sistem saati geri alınmış
    tat = max(tat, now)
    return tat, _free_positions(free, now, tat)


def _free_positions(free: Any, now: float, tat: float) -> List[float]:
    """Geçerli iade listesi: sayı olan, zamanı geçmemiş (≥ şimdi) ve kuyruğun içinde kalan (< tat) sıralar."""
    if not isinstance(free, list):
        return []
    return sorted({float(p) for p in free if _is_number(p) and now <= p < tat})


class _Lane(NamedTuple):
    """Öncelik şeridinin durumu: sayaç, kayma kaydı (sayaca göre sıralı) ve son öncelikli isteğin konumu."""

    seq: int = 0
    bumps: Tuple[Tuple[float, int], ...] = ()
    prio: Optional[float] = None


_NO_LANE = _Lane()


def _state(tat: float, now: float, free: List[float], lane: Optional[_Lane] = None) -> State:
    lane = lane or _NO_LANE
    state: State = {"tat": tat, "at": now}
    if free:
        state["free"] = free
    if lane.seq:
        state["seq"] = lane.seq
    if lane.bumps:
        state["bumps"] = [[p, s] for p, s in lane.bumps]
    if lane.prio is not None and lane.prio >= now:
        state["prio"] = lane.prio
    return state


def _lane(state: State, now: float) -> _Lane:
    """Öncelik şeridinin durumu; bozuk girdiler, çok eski kaymalar ve zamanı geçmiş `prio` atılır."""
    seq = state.get("seq")
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else 0
    found: List[Tuple[float, int]] = []
    raw = state.get("bumps")
    for item in raw if isinstance(raw, list) else ():
        if (isinstance(item, list) and len(item) == 2 and _is_number(item[0]) and isinstance(item[1], int)
                and not isinstance(item[1], bool) and 0 < item[1] <= seq and item[0] >= now - _BUMP_KEEP_SECONDS):
            found.append((float(item[0]), int(item[1])))
    found.sort(key=lambda bump: bump[1])
    prio = state.get("prio")
    prio = float(prio) if _is_number(prio) and prio >= now else None
    return _Lane(seq, tuple(found[-_MAX_BUMPS:]), prio)


def take(state: State, now: float, interval: float, burst: int = 1) -> Tuple[float, float, float, State]:
    """
    Bir istek için sıra ayırır. (bekleme_sn, ayrılan_an, sıra, yeni_durum) döndürür.

    `interval` = 1 / hız. `burst`: boşta geçen süreden sonra art arda beklemeden geçebilecek
    istek sayısı (1 = istekler arası her zaman en az `interval`). `sıra`, isteğin kuyruktaki
    yeridir (GCRA'nın "teorik varış anı"); istek `sıra - (burst - 1) × interval` anından önce
    gönderilmez. İade edilmiş bir sıra varsa önce o verilir (`put_back`), yoksa kuyruğun sonu.
    Zamanı geçmiş iadeler kullanılmaz: sırasından sonra gönderilen istek komşusuna yaklaşırdı.
    """
    tat, free = _load(state, now)
    lane = _lane(state, now)
    tolerance = (max(1, burst) - 1) * interval
    if free:
        position, free, new_tat = free[0], free[1:], tat
    else:
        position, new_tat = tat, tat + interval
    slot = max(now, position - tolerance)
    if slot - now > _MAX_WAIT_SECONDS + interval:  # + interval: çok düşük hızlarda tek aralık meşrudur
        position = slot = now
        free, new_tat, lane = [], now + interval, _Lane(lane.seq)
    return slot - now, slot, position, _state(new_tat, now, free, lane)


def take_priority(state: State, now: float, interval: float, burst: int = 1) -> Tuple[float, float, float, State]:
    """
    Öncelik şeridi: `take` gibi, ama sıra kuyruğun sonundan değil, zamanı henüz gelmemiş ilk arka plan
    sırasından alınır; öncelikli istekler kendi aralarında geliş sırasıyla gider (`prio`: sonuncunun konumu,
    yenisi onun ardına girer). Alınan sıra ve arkasındaki her sıra (iade edilmiş boşluklar dahil) bir aralık
    geri kayar, tat bir aralık uzar ve kayma (konum, yeni sayaç) kaydedilir; arka plan istekleri uyanınca bunu
    görür (`apply_bumps`). Önüne geçilecek bekleyen sıra yoksa ya da önde iade edilmiş bir boşluk varsa sıra
    `take` ile alınır.
    """
    tat, free = _load(state, now)
    lane = _lane(state, now)
    tolerance = (max(1, burst) - 1) * interval
    epsilon = min(_POSITION_EPSILON, interval / 4)
    # Kuyrukta zamanı gelmemiş (slot > şimdi) sıra sayısı; sıralar tat'tan geriye aralık aralık dizilir
    waiting = math.ceil((tat - now - tolerance) / interval - epsilon) - 1 if interval > 0 else 0
    head = tat - waiting * interval
    if lane.prio is not None and lane.prio + interval > head - epsilon:
        head = lane.prio + interval
    if waiting < 1 or head >= tat - epsilon or (free and free[0] <= head + epsilon):
        delay, slot, position, new_state = take(state, now, interval, burst)
        prio = position if lane.prio is None else max(lane.prio, position)
        return delay, slot, position, _state(new_state["tat"], now, list(new_state.get("free") or []),
                                             _lane(new_state, now)._replace(prio=prio))
    free = sorted(p + interval if p >= head - epsilon else p for p in free)
    seq = lane.seq + 1
    bumps = (*lane.bumps, (head, seq))[-_MAX_BUMPS:]
    slot = max(now, head - tolerance)
    return slot - now, slot, head, _state(tat + interval, now, free, _Lane(seq, bumps, head))


def apply_bumps(state: State, position: float, seen: int, interval: float) -> Tuple[float, int]:
    """
    Bir sıranın, ayrıldığından (`seen`: o anki sayaç) bu yana öncelikli isteklerin kaydırdığı yeni konumu:
    (konum, şimdiki sayaç). Kaymalar yazılış sırasıyla uygulanır: konumu kaymanın konumunda ya da ardında olan
    sıra bir aralık geri kayar.
    """
    lane = _lane(state, -math.inf)
    epsilon = min(_POSITION_EPSILON, interval / 4) if interval > 0 else _POSITION_EPSILON
    for at, number in lane.bumps:
        if number > seen and at <= position + epsilon:
            position += interval
    return position, max(lane.seq, seen)


def advance(state: State, now: float, interval: float, burst: int = 1) -> Tuple[float, float, State]:
    """`take` ile aynı, sıra olmadan: (bekleme_sn, ayrılan_an, yeni_durum)."""
    delay, slot, _, new_state = take(state, now, interval, burst)
    return delay, slot, new_state


def put_back(state: State, now: float, position: float, interval: float) -> Tuple[bool, State]:
    """
    Kullanılmayan bir sırayı (`take`'in döndürdüğü `sıra`, ayrıldığı andaki `interval` ile) bütçeye
    geri verir: (geri alındı mı, yeni_durum).

    Kuyruğun sonundaki sıra geri gelince tat o sıraya çekilir ve hemen altındaki iade edilmiş
    sıralar da birlikte kapanır (iptal edilen bir işin sıraları hangi düzende dönerse dönsün
    kuyruk sonunda tamamen kısalır). Aradaki bir sıra "free" listesine girer. Durum bu arada
    sıfırlandıysa (bozuk dosya, geri alınan saat, emniyet sınırı) ya da sıra tanınmıyorsa
    hiçbir şey değişmez.
    """
    tat = state.get("tat")
    written_at = state.get("at")
    if not (_is_number(tat) and _is_number(written_at) and _is_number(position) and _is_number(interval)):
        return False, state
    if interval <= 0 or written_at > now + _CLOCK_SKEW_TOLERANCE_SECONDS:
        return False, state
    lane = _lane(state, now)
    free = _free_positions(state.get("free"), -math.inf, tat)
    epsilon = min(_POSITION_EPSILON, interval / 4)
    if abs(position + interval - tat) <= epsilon:
        # Kuyruğun sonu: zamanı geçmiş olsa da geri alınır (tat şimdinin gerisine düşerse sonraki
        # ayırma zaten şimdiden başlar)
        tat = position
        while free and abs(free[-1] + interval - tat) <= epsilon:
            tat = free.pop()
    elif (
        now <= position < tat
        and len(free) < _MAX_FREE_POSITIONS
        and all(abs(position - p) > epsilon for p in free)
    ):
        free = sorted([*free, float(position)])
    else:
        return False, state
    return True, _state(tat, now, [p for p in free if now <= p < tat], lane)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class Reservation(float):
    """
    Bütçeden ayrılmış bir sıra. Sayı olarak değeri, isteği göndermeden önce beklenmesi gereken
    saniyedir: `reserve()`'ün sonucunu düz sayı gibi kullanan kod (ve testlerdeki düz sayı
    döndüren sahteler) değişmeden çalışır. İstek gönderilmeden vazgeçilirse `give_back()` sırayı
    bütçeye iade eder; bir sıra yalnızca bir kez iade edilir.

    slot: isteğin gönderilebileceği an. position: kuyruktaki yeri (bkz. `take`). seq: ayrıldığı andaki
    öncelik sayacı; öncelikli istekler sırayı kaydırınca `settle` yeni konumu ve beklemeyi ondan bulur.
    """

    __slots__ = ("slot", "position", "seq", "_interval", "_tolerance", "_lane", "_in_file", "_returned")

    slot: float
    position: float
    seq: int

    def __new__(
        cls,
        delay: float,
        *,
        slot: float,
        position: float,
        interval: float = 0.0,
        lane: "Optional[RequestThrottle]" = None,
        in_file: bool = False,
        seq: int = 0,
        tolerance: float = 0.0,
    ) -> "Reservation":
        self = super().__new__(cls, delay)
        self.slot = slot
        self.position = position
        self.seq = seq
        self._tolerance = tolerance
        self._interval = interval
        self._lane = lane  # None: bütçe kapalıyken alınmış, iade edilecek bir şey yok
        self._in_file = in_file  # ortak dosyadan mı, süreç içi sayaçtan mı ayrıldı
        self._returned = False
        return self

    def give_back(self) -> bool:
        """Sırayı bütçeye geri verir; geri alındıysa True. Hata fırlatmaz."""
        return self._lane.give_back(self) if self._lane is not None else False

    def recheck(self) -> float:
        """Öncelikli istekler sırayı kaydırdıysa daha beklenecek saniye (yoksa 0). Hata fırlatmaz."""
        return self._lane.recheck(self) if self._lane is not None else 0.0


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

    def _update_shared(self, step: "Callable[[State], Tuple[Any, State]]") -> Any:
        """Ortak durumu kilit altında okur, `step` ile değiştirir ve yazar; `step`'in sonucunu döndürür."""
        lock_path, state_path = self._paths()
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with file_lock(lock_path, self._lock_timeout):
            result, state = step(self._read(state_path))
            self._write(state_path, state)
        if self._shared_failed_at is not None:
            logger.info(f"Shared request budget ({self.name}) is used again.")
        self._shared_failed_at, self.shared_error = None, None
        self._local = state  # dosya kaybolursa süreç içi sayaç kaldığı yerden sürsün
        return result

    def reserve(self, *, priority: Optional[bool] = None) -> Reservation:
        """
        Sıradaki anı ayırır. Dönen değer beklenmesi gereken saniyedir (çağıran bekler); istek
        gönderilmeden vazgeçilirse `give_back` ile iade edilir, bekleme bitince `settle` / `recheck`
        öncelikli isteklerin kaydırdığı sırayı bekler.

        priority  True: öncelik şeridi (`take_priority`); None: bağlamdan (`interactive()` bloğu)
        """
        rate = self.rate()
        if rate <= 0:
            now = self._clock()
            return Reservation(0.0, slot=now, position=now)
        interval, burst = 1.0 / rate, self.burst()
        claim = take_priority if (is_interactive() if priority is None else priority) else take

        def step(state: State) -> Tuple[Tuple[float, float, float, int], State]:
            delay, slot, position, new_state = claim(state, self._clock(), interval, burst)
            return (delay, slot, position, int(new_state.get("seq") or 0)), new_state

        with self._thread_lock:
            in_file = False
            if self._shared_usable():
                try:
                    delay, slot, position, seq = self._update_shared(step)
                    in_file = True
                except (OSError, TimeoutError) as e:
                    if self._shared_failed_at is None:
                        logger.warning(
                            f"Shared request budget ({self.name}) is unavailable: {e}. This process goes on "
                            f"with its own counter; retrying the file in {int(_SHARED_RETRY_AFTER_SECONDS)} s."
                        )
                    self._shared_failed_at, self.shared_error = time.monotonic(), str(e)
            if not in_file:
                (delay, slot, position, seq), self._local = step(self._local)
            return Reservation(delay, slot=slot, position=position, interval=interval, lane=self, in_file=in_file,
                               seq=seq, tolerance=(burst - 1) * interval)

    def recheck(self, reservation: Reservation) -> float:
        """
        Ayrılmış sıranın, öncelikli isteklerin kaydırmasından sonraki yeri: konumu ve `slot`u günceller, daha
        beklenecek saniyeyi döndürür (0: sıra geldi). Durum değişmez. Hata fırlatmaz: dosyaya ulaşılamazsa 0.
        """
        with self._thread_lock:
            if reservation._lane is not self or reservation._returned or reservation._interval <= 0:
                return 0.0

            def step(state: State) -> Tuple[Tuple[float, int], State]:
                return apply_bumps(state, reservation.position, reservation.seq, reservation._interval), state

            if reservation._in_file:
                if not self._shared_usable():
                    return 0.0
                try:
                    position, seq = self._update_shared(step)
                except (OSError, TimeoutError):
                    return 0.0
            else:
                (position, seq), _ = step(self._local)
            reservation.position, reservation.seq = position, seq
            reservation.slot = max(reservation.slot, position - reservation._tolerance)
            return max(0.0, reservation.slot - self._clock())

    def reserve_slot(self) -> Tuple[float, float]:
        """Sıradaki anı ayırır: (beklenmesi gereken sn, ayrılan an). Beklemez."""
        reservation = self.reserve()
        return float(reservation), reservation.slot

    def give_back(self, reservation: Reservation) -> bool:
        """
        Kullanılmayan bir sırayı bütçeye geri verir: isteği, sırasını beklerken iptal edilen çağıran
        çağırır. Geri alındıysa True. İptal yolunda çağrıldığı için hata fırlatmaz; ortak dosyaya
        ulaşılamazsa sıra iade edilmeden kalır (eski davranış: kuyruk kendi kendine erir).

        Zamanı gelmiş bir sıra (slot ≤ şimdi) iade edilmez, kullanılmış sayılır: geri verilseydi
        sıradaki çağıran onu hiç beklemeden alırdı, ve durdurulan bir işin istek katmanında bekleyen
        öteki istekleri (iptale yalnızca beklerken bakarlar) durdurmadan sonra gönderilirdi. Zamanı
        gelmemiş bir sırayı alan istek ise bekler ve beklerken iptal edilir.
        """
        with self._thread_lock:
            if reservation._lane is not self or reservation._returned:
                return False
            reservation._returned = True
            if reservation.slot <= self._clock() + _POSITION_EPSILON:
                return False

            def step(state: State) -> Tuple[bool, State]:
                # Sıra öncelikli isteklerce kaydırıldıysa iade edilen yer kaydırılmış yeridir
                position, _ = apply_bumps(state, reservation.position, reservation.seq, reservation._interval)
                return put_back(state, self._clock(), position, reservation._interval)

            if not reservation._in_file:
                returned, self._local = step(self._local)
                return returned
            if not self._shared_usable():
                return False
            try:
                return bool(self._update_shared(step))
            except (OSError, TimeoutError) as e:
                if self._shared_failed_at is None:
                    logger.warning(
                        f"Shared request budget ({self.name}) is unavailable: {e}. A cancelled reservation was "
                        f"not returned; retrying the file in {int(_SHARED_RETRY_AFTER_SECONDS)} s."
                    )
                self._shared_failed_at, self.shared_error = time.monotonic(), str(e)
                return False

    def wait(self) -> float:
        """Sıra gelene kadar bekler (engelleyici). Bekleme yarıda kesilirse sıra iade edilir."""
        delay = self.reserve()
        if delay > 0:
            with give_back_if_interrupted(delay):
                self._sleep(delay)
                settle(delay, self._sleep)
        return delay

    async def wait_async(self) -> float:
        """Sıra gelene kadar bekler (olay döngüsünü engellemeden). Bekleme iptal edilirse sıra iade edilir."""
        delay = self.reserve()
        if delay > 0:
            with give_back_if_interrupted(delay):
                await asyncio.sleep(delay)
                await settle_async(delay, asyncio.sleep)
        return delay


# --- modül düzeyi: tüm API istekleri için tek şerit ----------------------------------------

API_LANE = "api"
_api: Optional[RequestThrottle] = None
_api_lock = threading.Lock()


def api_throttle() -> RequestThrottle:
    """SofaScore'a giden her isteğin geçtiği ortak bütçe (`client.rate`)."""
    global _api
    with _api_lock:
        if _api is None:
            _api = RequestThrottle(API_LANE, configured_rate)
        return _api


def reserve() -> Reservation:
    """
    Ortak bütçeden sıradaki anı ayırır; beklenmesi gereken saniyeyi döndürür (çağıran bekler).
    Bekleme iptal edilebiliyorsa `give_back_if_interrupted` bloğunda yapılır.
    """
    return api_throttle().reserve()


def give_back(delay: float) -> bool:
    """
    `reserve()`'ün döndürdüğü sırayı, isteği gönderilmeden bütçeye geri verir; geri alındıysa True.
    Düz bir sayı (testlerde `reserve` yerine konan sahtelerin döndürdüğü) için hiçbir şey yapmaz.
    """
    return delay.give_back() if isinstance(delay, Reservation) else False


def settle(delay: float, sleep: Callable[[float], None]) -> float:
    """
    `reserve()`'ün sırasını bekledikten sonra çağrılır: öncelikli istekler sırayı bu arada kaydırdıysa yeni
    sıraya kadar `sleep` ile bekler (ve yeniden bakar). Toplam ek beklemeyi döndürür. Düz bir sayı (testlerin
    sahteleri) ve beklemesiz sıra için hiçbir şey yapmaz. `give_back_if_interrupted` bloğunun içinde çağrılır.
    """
    if not isinstance(delay, Reservation) or delay <= 0:
        return 0.0
    total = 0.0
    while (extra := delay.recheck()) > 0:
        sleep(extra)
        total += extra
    return total


async def settle_async(delay: float, sleep: Callable[[float], Any]) -> float:
    """`settle`in async karşılığı: `sleep` beklenebilir (awaitable) döndürür."""
    if not isinstance(delay, Reservation) or delay <= 0:
        return 0.0
    total = 0.0
    while (extra := delay.recheck()) > 0:
        await sleep(extra)
        total += extra
    return total


@contextlib.contextmanager
def give_back_if_interrupted(delay: float) -> Iterator[None]:
    """
    Sıra beklemesini saran blok: bekleme bir istisnayla kesilirse (iş iptali, asyncio iptali,
    zaman aşımı, Ctrl-C) istek gönderilmeyecektir; sıra bütçeye geri verilir ve istisna sürer.
    """
    try:
        yield
    except BaseException:
        give_back(delay)
        raise


def wait() -> float:
    delay = reserve()
    if delay > 0:
        with give_back_if_interrupted(delay):
            time.sleep(delay)
            settle(delay, time.sleep)
    return delay


async def wait_async() -> float:
    delay = reserve()
    if delay > 0:
        with give_back_if_interrupted(delay):
            await asyncio.sleep(delay)
            await settle_async(delay, asyncio.sleep)
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
    ortak bütçe açıkken süreçler arasıdır; `client.rate` 0 ile ortak bütçe kapatılırsa
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
    global _api
    with _api_lock:
        _api = None
