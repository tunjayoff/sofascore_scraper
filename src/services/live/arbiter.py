"""
Kaynak hakemi (docs/design/02-services.md bölüm 8.1, "Arbitration and fallback"; plan maddesi P24).

Spor başına push bağlantısının sağlığını izler ve hangi kaynağın önde olduğunu, yoklamanın ne zaman
yapılacağını söyler. Saf bir durum makinesidir: saati çağıran verir, ağa ve depoya dokunmaz.

  * **Sağlık.** Bağlantı açık (sayfanın NATS bağlantısı `INFO` gönderdi) ve son işaretten (açılış, kare,
    PING/PONG) bu yana SILENCE_SECONDS geçmedi. Sitenin istemcisi 120 sn'de bir PING atar (ölçüm:
    docs/push-channel/README.md); eşik bu aralığın bir buçuk katıdır, maçı olmayan sessiz bir sporda da
    bağlantı PING/PONG ile sağlıklı görünür.
  * **Önde olan.** Push sağlıklıysa push kaynağı (`page` ya da `direct`), değilse `poll`. Her değişiklik bir `Switch` döndürür; servis onu
    `system.live_source_changed` olarak yazar.
  * **Yoklama aralığı.** Push sağlıklıyken yavaş güvenlik aralığı (SAFETY_POLL_SECONDS): push'un hiç anmadığı
    kapsamdaki maçları ve kaçan kareleri yoklama yakalar. Push sessiz ya da kopuksa `poll_interval`.
  * **Yeniden bağlanma.** Her açılıştan (ilk açılış dahil) sonra bir yoklama turu hemen yapılır: bağlantı
    kopukken olan geçiş push'ta tekrarlanmaz (ölçümde kopma pencerelerindeki 36 geçişin 22'si) ve son bilinen
    durum yeni turla tohumlanır.

`direct` kaynağında (P31) bağlantı uygulamanın kendisinindir; hakem aynıdır, yalnızca önde olanın adı `direct`'tir.
Push hiç yoksa (`push=False`, `--source poll`) önde olan hep `poll`'dur ve hakem değişiklik bildirmez.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

LEADER_PAGE = "page"
LEADER_DIRECT = "direct"
LEADER_POLL = "poll"

PING_INTERVAL_SECONDS = 120.0  # sitenin istemcisinin PING aralığı (ölçüm)
SILENCE_SECONDS = 1.5 * PING_INTERVAL_SECONDS
SAFETY_POLL_SECONDS = 120.0  # push sağlıklıyken yoklama aralığı

REASON_CONNECTED = "push_connected"
REASON_DISCONNECTED = "push_disconnected"
REASON_SILENT = "push_silent"
REASON_UNAVAILABLE = "push_unavailable"


@dataclass(frozen=True)
class Switch:
    """Önde olan kaynağın değişmesi: `system.live_source_changed` olayının verisi."""

    sport: str
    from_source: str
    to_source: str
    reason: str
    at: float

    def to_data(self) -> Dict[str, Any]:
        return {"sport": self.sport, "from": self.from_source, "to": self.to_source, "reason": self.reason}


class SportArbiter:
    """
    sport            spor adı
    poll_interval    push sağlıksızken yoklama aralığı ([live] poll_interval_seconds)
    push             bu sporda push kaynağı var mı (`page` ya da `direct`); yoksa hep yoklama
    push_name        push kaynağının adı (önde olan push iken bu ad yazılır): "page" ya da "direct"
    safety_interval  push sağlıklıyken yoklama aralığı
    silence          bu kadar sn işaret gelmezse push sessiz sayılır
    """

    def __init__(self, sport: str, *, poll_interval: float, push: bool,
                 safety_interval: float = SAFETY_POLL_SECONDS, silence: float = SILENCE_SECONDS,
                 push_name: str = LEADER_PAGE) -> None:
        self.sport = sport
        self.push_name = push_name
        self.poll_interval = max(1.0, float(poll_interval))
        self.push = push
        self.safety_interval = max(self.poll_interval, float(safety_interval))
        self.silence = float(silence)
        self.leader = LEADER_POLL
        self.connected = False
        self.connections = 0  # açılan bağlantı sayısı (ilk açılış + yeniden bağlanmalar)
        self.opened_at: Optional[float] = None
        self.last_frame: Optional[float] = None
        self.last_ping: Optional[float] = None
        self.last_poll: Optional[float] = None
        self.poll_pending = True  # ilk tur hemen
        self.unavailable: Optional[str] = None  # kaynak açılamadı (neden); sağlıksız sayılır
        self.last_switch: Optional[Switch] = None

    # --- girdiler -------------------------------------------------------------------------------

    def opened(self, at: float) -> None:
        """Push bağlantısı (yeniden) kuruldu: yoklama turu hemen gerekir."""
        self.connected = True
        self.connections += 1
        self.opened_at = at
        self.unavailable = None
        self.poll_pending = True

    def closed(self, at: float) -> None:
        self.connected = False

    def frame(self, at: float) -> None:
        self.last_frame = at

    def ping(self, at: float) -> None:
        self.last_ping = at

    def failed(self, reason: str) -> None:
        """Kaynak açılamadı ya da sayfa kapandı/çöktü: push sağlıksız."""
        self.connected = False
        self.unavailable = reason

    def gap(self) -> None:
        """Kare kaybedildi (kuyruk taştı): bir yoklama turu hemen gerekir."""
        self.poll_pending = True

    def polled(self, at: float) -> None:
        self.last_poll = at
        self.poll_pending = False

    # --- kararlar -------------------------------------------------------------------------------

    def last_sign(self) -> Optional[float]:
        signs = [t for t in (self.opened_at, self.last_frame, self.last_ping) if t is not None]
        return max(signs) if signs else None

    def healthy(self, now: float) -> bool:
        if not self.push or not self.connected:
            return False
        sign = self.last_sign()
        return sign is not None and now - sign <= self.silence

    def _reason(self, now: float) -> str:
        if self.unavailable is not None:
            return REASON_UNAVAILABLE
        if not self.connected:
            return REASON_DISCONNECTED
        return REASON_SILENT

    def update(self, now: float) -> Optional[Switch]:
        """Önde olanı yeniden değerlendirir; değiştiyse Switch (değişmediyse None)."""
        if not self.push:
            return None
        healthy = self.healthy(now)
        wanted = self.push_name if healthy else LEADER_POLL
        if wanted == self.leader:
            return None
        switch = Switch(self.sport, self.leader, wanted, REASON_CONNECTED if healthy else self._reason(now), now)
        self.leader = wanted
        self.last_switch = switch
        if not healthy:
            # Push'tan yoklamaya geçiş: bağlantı kopukken kaçanlar için tur hemen
            self.poll_pending = True
        return switch

    def interval(self) -> float:
        return self.poll_interval if self.leader == LEADER_POLL else self.safety_interval

    def poll_due(self, now: float) -> bool:
        if self.poll_pending or self.last_poll is None:
            return True
        return now - self.last_poll >= self.interval()

    def next_poll_in(self, now: float) -> float:
        if self.poll_due(now):
            return 0.0
        assert self.last_poll is not None
        return max(0.0, self.last_poll + self.interval() - now)

    def to_dict(self) -> Dict[str, Any]:
        sw = self.last_switch
        return {
            "leader": self.leader,
            "push": self.push,
            "connected": self.connected,
            "connections": self.connections,
            "last_frame_at": self.last_frame,
            "last_ping_at": self.last_ping,
            "last_switch": None if sw is None else {**sw.to_data(), "at": sw.at},
        }


__all__ = [
    "LEADER_DIRECT",
    "LEADER_PAGE",
    "LEADER_POLL",
    "PING_INTERVAL_SECONDS",
    "REASON_CONNECTED",
    "REASON_DISCONNECTED",
    "REASON_SILENT",
    "REASON_UNAVAILABLE",
    "SAFETY_POLL_SECONDS",
    "SILENCE_SECONDS",
    "SportArbiter",
    "Switch",
]
