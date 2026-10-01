"""
Tarayıcı köprüsünün sağlık durumu: "SofaScore bizi engelliyor mu?"

Her şey headless tarayıcının SofaScore challenge'ını çözmesine bağlı (src/challenge_solver.py).
Bu bozulduğunda işler yalnızca yavaşça başarısız oluyordu; kullanıcıya nedenini söyleyen bir
şey yoktu. Burada köprüden geçen her isteğin SONUCU sayılır ve üç durumdan biri tutulur:

  ok        son istek başarılı (ya da henüz istek yok)
  degraded  art arda BRIDGE_DEGRADED_AFTER istek reddedildi
  blocked   art arda BRIDGE_BLOCKED_AFTER istek reddedildi VE seri en az
            BRIDGE_BLOCKED_MIN_SECONDS sürdü

Durum adları köprünün durumunu anlatır; nedeni son hatanın türü söyler (SofaScore reddediyor mu,
yoksa tarayıcı mı açılamıyor) ve mesajlar buna göre seçilir.

Sayılan başarısızlıklar: çözülemeyen challenge, challenge'sız 403, başlatılamayan tarayıcı.
Çözülüp yinelenen bir 403 başarıdır. Ağ hatası, 5xx ve 429 seriyi ne uzatır ne sıfırlar:
onlar engelleme değil, başka bir sorun (429/5xx için devre kesici ayrıca var).

Süre koşulunun nedeni: toplu indirmede 10 istek aynı anda uçar; tek bir başarısız çözüm
hepsini birden düşürür. Bu geçici olabilir (çözüm 180 sn sonra yeniden denenir), bu yüzden
"blocked" demek için serinin en az bir yeniden çözüm denemesini de kapsaması beklenir.

Durum, istek sonuçlarıyla değişir; kendi kendine (zaman geçtikçe) değişmez. Değişimde tek
bir log satırı yazılır ve dinleyiciler çağrılır (CLI'da tek satır mesaj). Durum süreç
başınadır: web uygulaması kendi köprüsünü, her CLI süreci kendininkini bildirir.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

OK = "ok"
DEGRADED = "degraded"
BLOCKED = "blocked"
_RANK = {OK: 0, DEGRADED: 1, BLOCKED: 2}

# Başarısızlık türleri (son hata; arayüz ve CLI mesajı buna göre neden gösterir)
KIND_CHALLENGE = "challenge"  # 403 challenge çözülemedi ya da çözümden sonra da reddedildi
KIND_FORBIDDEN = "forbidden"  # challenge sunulmayan 403
KIND_BROWSER = "browser"      # headless tarayıcı başlatılamadı

DEFAULT_DEGRADED_AFTER = 3
DEFAULT_BLOCKED_AFTER = 10
# Başarısız bir çözüm 180 sn yeniden denenmez (challenge_solver._SOLVE_RETRY_AFTER): 200 sn süren
# bir seri, en az bir yeniden denemenin de başarısız olduğunu gösterir.
DEFAULT_BLOCKED_MIN_SECONDS = 200.0

Listener = Callable[[Dict[str, Any], str], None]


def _env_number(key: str, default: float, minimum: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def thresholds() -> Dict[str, float]:
    """Eşikler (.env): BRIDGE_DEGRADED_AFTER, BRIDGE_BLOCKED_AFTER, BRIDGE_BLOCKED_MIN_SECONDS."""
    degraded = int(_env_number("BRIDGE_DEGRADED_AFTER", DEFAULT_DEGRADED_AFTER, 1))
    blocked = int(_env_number("BRIDGE_BLOCKED_AFTER", DEFAULT_BLOCKED_AFTER, 1))
    return {
        "degraded_after": degraded,
        "blocked_after": max(blocked, degraded),
        "blocked_min_seconds": _env_number("BRIDGE_BLOCKED_MIN_SECONDS", DEFAULT_BLOCKED_MIN_SECONDS, 0),
    }


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


class BridgeHealth:
    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        thresholds_fn: Callable[[], Dict[str, float]] = thresholds,
    ) -> None:
        self._clock = clock
        self._thresholds = thresholds_fn
        self._lock = threading.Lock()
        self._listeners: List[Listener] = []
        self.state = OK
        self.consecutive_failures = 0
        self.last_success_at: Optional[float] = None
        self.last_failure_at: Optional[float] = None
        self.failing_since: Optional[float] = None
        self.changed_at: Optional[float] = None
        self.last_error: Optional[Dict[str, Any]] = None

    # -- olaylar --

    def record_success(self) -> None:
        """Köprü bir isteğe yanıt aldı (200 ya da 404): seri biter."""
        with self._lock:
            now = self._clock()
            self.last_success_at = now
            self.consecutive_failures = 0
            self.failing_since = None
            previous = self._transition(OK, now)
            snap = self._snapshot() if previous else None
        self._announce(previous, snap)

    def record_failure(self, kind: str, detail: str = "") -> None:
        """Köprü bir isteği alamadı: reddedildi (403/challenge) ya da tarayıcı açılamadı."""
        with self._lock:
            now = self._clock()
            self.last_failure_at = now
            self.consecutive_failures += 1
            if self.failing_since is None:
                self.failing_since = now
            self.last_error = {"kind": kind, "detail": str(detail)[:200], "at": now}
            th = self._thresholds()
            target = OK
            if self.consecutive_failures >= th["degraded_after"]:
                target = DEGRADED
            if (
                self.consecutive_failures >= th["blocked_after"]
                and now - self.failing_since >= th["blocked_min_seconds"]
            ):
                target = BLOCKED
            # Seri sürerken durum yalnızca kötüleşir; iyileşme yalnızca başarılı istekle olur
            previous = self._transition(target, now) if _RANK[target] > _RANK[self.state] else None
            snap = self._snapshot() if previous else None
        self._announce(previous, snap)

    def _transition(self, new: str, now: float) -> Optional[str]:
        """Kilit altında. Durum değiştiyse önceki durumu döndürür."""
        if new == self.state:
            return None
        previous, self.state, self.changed_at = self.state, new, now
        return previous

    def _announce(self, previous: Optional[str], snap: Optional[Dict[str, Any]]) -> None:
        """Durum değişiminde bir kez: log + dinleyiciler (kilit dışında)."""
        if previous is None or snap is None:
            return
        err = snap["last_error"] or {}
        cause = f"son hata: {err.get('kind')}: {err.get('detail')}"
        browser = err.get("kind") == KIND_BROWSER
        if snap["state"] == OK:
            logger.info(f"SofaScore istekleri yeniden başarılı (köprü durumu: {previous} → ok).")
        elif snap["state"] == DEGRADED:
            subject = "Tarayıcı köprüsü çalışmıyor" if browser else "SofaScore istekleri reddediyor"
            logger.warning(
                f"{subject}: art arda {snap['consecutive_failures']} istek başarısız ({cause}). "
                f"Köprü durumu: {previous} → degraded."
            )
        else:
            subject = "Tarayıcı köprüsü çalışmıyor" if browser else "SofaScore bizi engelliyor"
            advice = (
                "Tarayıcı kurulumunu denetleyin (`python -m playwright install chromium`)."
                if browser
                else "Bir süre bekleyin; istek hızını düşürmek (REQUEST_RATE_LIMIT) yardımcı olabilir."
            )
            logger.warning(
                f"{subject}: art arda {snap['consecutive_failures']} istek başarısız, {snap['failing_since']} "
                f"tarihinden beri başarılı istek yok ({cause}). Köprü durumu: {previous} → blocked. {advice}"
            )
        for fn in list(self._listeners):
            try:
                fn(snap, previous)
            except Exception:  # bildirim isteği asla bozmamalı
                logger.debug("bridge health listener failed", exc_info=True)

    # -- okuma --

    def snapshot(self) -> Dict[str, Any]:
        """JSON'a hazır görüntü (/health, /api/bypass/status). Zamanlar ISO-8601 UTC."""
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> Dict[str, Any]:
        err = self.last_error
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": _iso(self.last_success_at),
            "last_failure_at": _iso(self.last_failure_at),
            "failing_since": _iso(self.failing_since),
            "changed_at": _iso(self.changed_at),
            "last_error": {"kind": err["kind"], "detail": err["detail"], "at": _iso(err["at"])} if err else None,
            "thresholds": self._thresholds(),
        }

    def add_listener(self, fn: Listener) -> None:
        """fn(snapshot, önceki_durum): durum her değiştiğinde bir kez."""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)


# Süreç başına tek köprü (BrowserBridge.get_instance) → tek sağlık durumu
_health = BridgeHealth()


def record_success() -> None:
    _health.record_success()


def record_failure(kind: str, detail: str = "") -> None:
    _health.record_failure(kind, detail)


def snapshot() -> Dict[str, Any]:
    return _health.snapshot()


def add_listener(fn: Listener) -> None:
    _health.add_listener(fn)


def remove_listener(fn: Listener) -> None:
    _health.remove_listener(fn)


def reset() -> None:
    """Durumu sıfırlar (testler)."""
    global _health
    _health = BridgeHealth()


# --- CLI ------------------------------------------------------------------------------------

def cli_message(snap: Dict[str, Any], t: Callable[..., str]) -> str:
    """
    Durum için tek satırlık, yerelleştirilmiş mesaj. `t`: I18nManager.t (locales/tr.json, en.json).
    """
    state = snap.get("state")
    if state == OK:
        return t("bridge_health_ok")
    err = snap.get("last_error") or {}
    reason = t(f"bridge_reason_{err.get('kind') or KIND_FORBIDDEN}")
    last = snap.get("last_success_at")
    if last:
        last = dt.datetime.fromisoformat(last).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return t(
        "bridge_health_blocked" if state == BLOCKED else "bridge_health_degraded",
        count=snap.get("consecutive_failures", 0),
        reason=reason,
        last_success=last or t("bridge_health_never"),
    )


def print_cli_line(snap: Dict[str, Any], previous: str) -> None:
    """
    Dinleyici (main.py terminal modlarında ekler): durum değişince uygulama dilinde tek satır.
    stderr'e yazılır; --watch'ta stdout olay akışıdır.
    """
    from src.i18n import get_i18n

    print(cli_message(snap, get_i18n().t), file=sys.stderr)
