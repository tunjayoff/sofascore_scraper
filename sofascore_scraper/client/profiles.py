"""
İstek katmanının tarayıcı profilleri (curl_cffi `impersonate` adları) ve reddedilenlerin kenara alınması.

SofaScore'un kapısı (yanıt: 403 `{"error": {"code": 403, "reason": "challenge"}}`, `server: Varnish`) isteği
istemcinin kimliğine bakarak reddeder. 2026-11-14 ölçümü (tek IP, hücre başına 1 ila 3 istek):

  * User-Agent'ı eski bir Chrome sürümü bildiren istek reddedilir: `Chrome/147` ve altı 403, 148 ve üstü 200.
    Safari, Firefox ve Chrome olmayan başka adlar geçer. Eşik Chrome sürümleriyle ilerler.
  * Bazı parmak izleri User-Agent ne olursa olsun reddedilir (curl_cffi'nin chrome124, chrome131 ve Windows
    chrome151 profilleri).

Eski listenin dokuz profilinden altısı (chrome124 … chrome145) bu yüzden her istekte 403 alıyor, her 403 tarayıcı
köprüsünü açıyor ve süreci on dakikalığına "önce tarayıcı" moduna sokuyordu. İki önlem:

  1. `WANTED`: ölçümde kendi kimliğiyle geçen profiller. Kimlik ile parmak izi birbirini tutar (User-Agent
     ezilmez).
  2. Liste kendini düzeltir: bir profil challenge alırsa `REFUSED_SECONDS` boyunca kenara alınır ve istek hemen
     başka bir profille yinelenir (sofascore_scraper/client/transport.py). Eşik ilerleyip bir profili eskittiğinde ya
     da curl_cffi yeni bir sürümle geldiğinde kod değişmeden yalnızca geçen profiller kullanılır. Hiçbir profil
     kalmadıysa (kapı hepsini reddediyor: gerçek bir engel) seçim eskisi gibi bütün listeden yapılır ve istek
     tarayıcı köprüsüne düşer.

Dışarıya soru sorulmaz (güncel Chrome sürümü bir servisten okunmaz): neyin geçtiğini SofaScore'un kendi yanıtı
söyler ve bunun için fazladan istek gerekmez.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ölçümde olduğu gibi (kendi User-Agent'ıyla) geçenler. chrome152_macos ölçülmedi: alınmadı.
WANTED: Tuple[str, ...] = (
    "chrome150",
    "chrome151_macos",
    "chrome152",
    # Safari ve Firefox: Chrome sürüm kuralına takılmazlar, parmak izi çeşitliliği sağlarlar
    "safari17_0",
    "safari18_0",
    "safari260",
    "firefox133",
    "firefox155",
)
# curl_cffi'nin eski bir sürümü WANTED'ın hiçbirini tanımıyorsa (requirements.txt aralığı geniştir): eski liste.
# Chrome olanları kapıda reddedilir, ama tanınmayan bir profil adı isteği hiç göndermez.
LEGACY: Tuple[str, ...] = (
    "chrome124", "chrome131", "chrome133a", "chrome136", "chrome142", "chrome145",
    "safari17_0", "safari18_0", "firefox133",
)
REFUSED_SECONDS = 6 * 3600.0
MAX_SWAPS = 2  # bir istek, challenge alan profilin yerine en çok bu kadar başka profil dener


def _supported(names: Iterable[str]) -> List[str]:
    """curl_cffi'nin kurulu sürümünün tanıdığı adlar (sıra korunur). Sürüm okunamazsa hepsi."""
    try:
        from curl_cffi.requests import BrowserType

        known = {b.value for b in BrowserType}
    except Exception:  # pragma: no cover - curl_cffi'nin beklenmeyen bir sürümü
        return list(names)
    return [n for n in names if n in known]


def _initial() -> List[str]:
    wanted = _supported(WANTED)
    # En az üç Chrome profili olmadan liste eski curl_cffi'ye göre kurulur (tests/test_api_bypass_improvements.py)
    if len([n for n in wanted if n.startswith("chrome")]) >= 3:
        return wanted
    return _supported(LEGACY) or list(LEGACY)


PROFILES: List[str] = _initial()

_lock = threading.Lock()
_refused_until: Dict[str, float] = {}


def usable(exclude: Iterable[str] = ()) -> List[str]:
    """Kenara alınmamış profiller (`exclude` dışında)."""
    now = time.monotonic()
    skip = set(exclude)
    with _lock:
        return [p for p in PROFILES if p not in skip and _refused_until.get(p, 0.0) <= now]


def pick() -> str:
    """Bir profil: kenara alınmamışlardan rastgele; hepsi kenardaysa bütün listeden (eski davranış)."""
    return random.choice(usable() or PROFILES)


def another(tried: Iterable[str]) -> Optional[str]:
    """Challenge alan isteğin yineleneceği profil: kenara alınmamış ve bu istekte denenmemiş biri; yoksa None."""
    left = usable(tried)
    return random.choice(left) if left else None


def refuse(profile: Optional[str]) -> None:
    """`profile` challenge aldı: REFUSED_SECONDS boyunca seçilmez. Süre dolunca yeniden denenir."""
    if not profile or profile not in PROFILES:
        return
    now = time.monotonic()
    with _lock:
        fresh = _refused_until.get(profile, 0.0) <= now
        _refused_until[profile] = now + REFUSED_SECONDS
        left = len([p for p in PROFILES if _refused_until.get(p, 0.0) <= now])
    if fresh:
        logger.info("SofaScore challenged the %s profile; it is set aside for %d h (%d of %d profiles left)",
                    profile, int(REFUSED_SECONDS // 3600), left, len(PROFILES))


def refused() -> List[str]:
    """Şu an kenarda olan profiller (tanılama için)."""
    now = time.monotonic()
    with _lock:
        return [p for p in PROFILES if _refused_until.get(p, 0.0) > now]


def reset() -> None:
    """Kenara alınanları unutur (testler)."""
    with _lock:
        _refused_until.clear()
