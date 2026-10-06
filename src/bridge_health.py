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

Durumu başka bir sürecin de görebilmesi için (ör. `status` komutu) bu modül hiçbir şey yazmaz:
her geçişte `on_health_change` geri çağrıları görüntüyle (snapshot) çağrılır; görüntüyü nereye
saklayacağına çağıran karar verir (docs/design/02-services.md 2.4: istemci DATA_DIR'e yazmaz).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.redact import redact_text

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
# snapshot()'ın döndürdüğü JSON'a hazır görüntü
BridgeHealthSnapshot = Dict[str, Any]
# Her durum geçişinde bir kez, yeni görüntüyle çağrılır
HealthChangeCallback = Callable[[Mapping[str, Any]], None]


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
        on_health_change: Optional[HealthChangeCallback] = None,
    ) -> None:
        self._clock = clock
        self._thresholds = thresholds_fn
        self._lock = threading.Lock()
        self._listeners: List[Listener] = []
        self._change_callbacks: List[HealthChangeCallback] = [on_health_change] if on_health_change else []
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
        # Ayrıntı /health ve /api/bypass/status ile dışarı verilir: tarayıcı hata metni proxy
        # adresini (parolasıyla) taşıyabilir, bu yüzden log satırları gibi maskelenir
        detail = redact_text(str(detail))[:200]
        with self._lock:
            now = self._clock()
            self.last_failure_at = now
            self.consecutive_failures += 1
            if self.failing_since is None:
                self.failing_since = now
            self.last_error = {"kind": kind, "detail": detail, "at": now}
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
        """Durum değişiminde bir kez: log + dinleyiciler + on_health_change geri çağrıları (kilit dışında)."""
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
                "Tarayıcı kurulumunu denetleyin: `python main.py --doctor`."
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
        for callback in list(self._change_callbacks):
            try:
                callback(snap)
            except Exception:  # görüntüyü saklayamayan çağıran isteği bozmamalı
                logger.debug("bridge health change callback failed", exc_info=True)

    # -- okuma --

    def snapshot(self) -> BridgeHealthSnapshot:
        """JSON'a hazır görüntü (/health, /api/bypass/status). Zamanlar ISO-8601 UTC."""
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> BridgeHealthSnapshot:
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

    def add_on_health_change(self, fn: HealthChangeCallback) -> None:
        """fn(snapshot): durum her değiştiğinde bir kez (ok → degraded → blocked ve geri dönüş)."""
        if fn not in self._change_callbacks:
            self._change_callbacks.append(fn)

    def remove_on_health_change(self, fn: HealthChangeCallback) -> None:
        if fn in self._change_callbacks:
            self._change_callbacks.remove(fn)


# --- bağlantı: istek katmanının son sonucu (plan maddesi FX-19) --------------------------------------------

# Bağlantı durumları: hiç istek bitmedi / son istek yanıt aldı / son istek yanıt alamadı
CONNECTION_NEVER = "never_tried"
CONNECTION_OK = "ok"
CONNECTION_FAILED = "failed"


class ConnectionState:
    """
    Bu sürecin SofaScore'a son isteğinin sonucu (köprü durumundan ayrı: köprü yalnızca reddedilen istekleri sayar
    ve hiç istek yokken de "ok" der). İstek katmanı her isteğin son halini bildirir (src/breaker.py
    `report_ok` / `report_exception`): yanıt (200, 404) başarı; 403, 429, 5xx, zaman aşımı, ağ ve okunamayan yanıt
    başarısızlık. Devre kesicinin göndermediği istek sayılmaz. Arayüz böylece hiçbir istek başarmadan "bağlı"
    demez.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.last_success_at: Optional[float] = None
        self.last_failure_at: Optional[float] = None
        self.last_failure_reason: Optional[str] = None
        self.last_failure_status: Optional[int] = None

    def record_answer(self) -> None:
        with self._lock:
            self.last_success_at = self._clock()

    def record_unanswered(self, reason: str, status: Optional[int] = None) -> None:
        with self._lock:
            self.last_failure_at = self._clock()
            self.last_failure_reason = str(reason)
            self.last_failure_status = status if isinstance(status, int) and not isinstance(status, bool) else None

    def snapshot(self) -> Dict[str, Any]:
        """JSON'a hazır görüntü; `state`: never_tried, ok ya da failed (son sonuç). Zamanlar ISO-8601 UTC."""
        with self._lock:
            success, failure = self.last_success_at, self.last_failure_at
            if success is None and failure is None:
                state = CONNECTION_NEVER
            elif failure is None or (success is not None and success >= failure):
                state = CONNECTION_OK
            else:
                state = CONNECTION_FAILED
            return {
                "state": state,
                "last_success_at": _iso(success),
                "last_failure_at": _iso(failure),
                "last_failure_reason": self.last_failure_reason,
                "last_failure_status": self.last_failure_status,
            }


# Süreç başına tek köprü (BrowserBridge.get_instance) → tek sağlık durumu
_health = BridgeHealth()
_connection = ConnectionState()


def record_answer() -> None:
    """SofaScore bir isteğe yanıt verdi (istek katmanından; FX-19)."""
    _connection.record_answer()


def record_unanswered(reason: str, status: Optional[int] = None) -> None:
    """Bir istek yanıt alamadı: reason src/breaker.py `failure_kind`'ın türüdür (FX-19)."""
    _connection.record_unanswered(reason, status)


def connection() -> Dict[str, Any]:
    """Bağlantının görüntüsü (`ConnectionState.snapshot`)."""
    return _connection.snapshot()


def record_success() -> None:
    _health.record_success()


def record_failure(kind: str, detail: str = "") -> None:
    _health.record_failure(kind, detail)


def snapshot() -> BridgeHealthSnapshot:
    return _health.snapshot()


def add_listener(fn: Listener) -> None:
    _health.add_listener(fn)


def remove_listener(fn: Listener) -> None:
    _health.remove_listener(fn)


def add_on_health_change(fn: HealthChangeCallback) -> None:
    _health.add_on_health_change(fn)


def remove_on_health_change(fn: HealthChangeCallback) -> None:
    _health.remove_on_health_change(fn)


def reset() -> None:
    """Durumu sıfırlar (testler): köprü ve bağlantı."""
    global _health, _connection
    _health = BridgeHealth()
    _connection = ConnectionState()


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
