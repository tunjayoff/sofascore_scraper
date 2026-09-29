"""Bir indirme işinin yapılandırılmış ilerlemesi: aşama, iş genelinde sayaç, hatalar, tahmini süre.

Web kartı düz `current_task` metni yerine bunu (`detail`) okur; metinler ön yüzde çevrilir.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

# Her aşamanın genel yüzdedeki payı. İş planında olmayan aşamaların payı diğerlerine dağılır.
PHASE_WEIGHTS: Dict[str, int] = {"seasons": 5, "matches": 25, "details": 65, "export": 5}

MAX_FAILED_LISTED = 50
# Bu kadar ilerleme ve süre birikmeden tahmini süre verme: ilk birkaç maçın hızı yanıltıcı
_ETA_MIN_DONE = 3
_ETA_MIN_ELAPSED = 5.0


class JobProgress:
    """Aşamaları sırayla yürütür ve her değişikliği `publish` ile iş deposuna yazar.

    `publish(fields)` JobStore.update'e verilecek alanları alır. Aynı iş içinde birden çok
    thread'den (asyncio döngüsü, to_thread) çağrılabilir; bu yüzden kilitli.
    """

    def __init__(
        self,
        phases: List[str],
        publish: Callable[[Dict[str, Any]], None],
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        unknown = [p for p in phases if p not in PHASE_WEIGHTS]
        if unknown:
            raise ValueError(f"unknown phase(s): {unknown}")
        self.phases = list(phases)
        self._publish = publish
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.RLock()
        self._index = -1
        self._done = 0
        self._total = 0
        self._phase_started = 0.0
        self._context: Dict[str, Any] = {}
        self._failed: List[Dict[str, Any]] = []
        self._failed_count = 0
        self._details_done = 0
        self._details_total = 0
        self._wait: Optional[Dict[str, Any]] = None
        self._breaker: Optional[str] = None
        weight_sum = sum(PHASE_WEIGHTS[p] for p in self.phases) or 1
        self._weights = [PHASE_WEIGHTS[p] * 100.0 / weight_sum for p in self.phases]

    # --- aşamalar -------------------------------------------------------------------

    @property
    def phase(self) -> Optional[str]:
        return self.phases[self._index] if 0 <= self._index < len(self.phases) else None

    def start_phase(self, name: str, total: int = 0) -> None:
        with self._lock:
            idx = self.phases.index(name)
            self._index = idx
            self._done = 0
            self._total = max(0, int(total))
            self._phase_started = self._clock()
            self._context = {}
            self._wait = None
            if name == "details":
                self._details_done = 0
                self._details_total = self._total
            self._emit()

    def set_total(self, total: int) -> None:
        with self._lock:
            self._total = max(0, int(total))
            if self.phase == "details":
                self._details_total = self._total
            self._emit()

    def set_context(
        self,
        *,
        league_id: Optional[int] = None,
        league_name: Optional[str] = None,
        season_name: Optional[str] = None,
    ) -> None:
        """Şu an üzerinde çalışılan lig/sezon (kartta "LaLiga · 24/25")."""
        with self._lock:
            self._context = {
                k: v
                for k, v in (("league_id", league_id), ("league_name", league_name), ("season_name", season_name))
                if v is not None
            }
            self._emit()

    def advance(self, done: int) -> None:
        """Aşama içindeki tamamlanan iş sayısını (mutlak) ayarlar."""
        with self._lock:
            self._done = max(0, min(int(done), self._total)) if self._total else max(0, int(done))
            if self.phase == "details":
                self._details_done = self._done
            # İlerleme geldiyse bekleme bitmiştir
            if self._wait and self._wait["until"] <= self._wall():
                self._wait = None
            self._emit()

    # --- olaylar --------------------------------------------------------------------

    def add_failed(self, match_id: str, league_id: Optional[int] = None) -> None:
        with self._lock:
            self._failed_count += 1
            if len(self._failed) < MAX_FAILED_LISTED:
                entry: Dict[str, Any] = {"match_id": str(match_id)}
                if league_id is not None:
                    entry["league_id"] = league_id
                self._failed.append(entry)
            self._emit()

    def wait(self, reason: str, seconds: float) -> None:
        """SofaScore geri çekilmesi: kart "{n} sn bekleniyor" gösterir. Daha uzun olan bekleme kazanır."""
        with self._lock:
            until = self._wall() + max(0.0, float(seconds))
            if self._wait and self._wait["until"] >= until:
                return
            self._wait = {"reason": reason, "until": round(until, 1)}
            self._emit()

    def breaker(self, reason: str) -> None:
        """Devre kesildi (çok fazla 403/429/5xx): indirme erken durdu."""
        with self._lock:
            self._breaker = reason
            self._emit(circuit_breaker_triggered=True, circuit_breaker_reason=reason)

    # --- hesaplar -------------------------------------------------------------------

    def percent(self) -> int:
        with self._lock:
            if self._index < 0:
                return 0
            base = sum(self._weights[: self._index])
            frac = (self._done / self._total) if self._total else 0.0
            return int(min(99, base + self._weights[self._index] * frac))

    def eta_seconds(self) -> Optional[float]:
        """Mevcut aşamanın kalan süresi, aşamadaki ortalama hıza göre."""
        with self._lock:
            if self._total <= 0 or self._done < _ETA_MIN_DONE or self._done >= self._total:
                return None
            elapsed = self._clock() - self._phase_started
            if elapsed < _ETA_MIN_ELAPSED:
                return None
            return round(elapsed / self._done * (self._total - self._done), 1)

    def detail(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "phases": list(self.phases),
                "phase": self.phase,
                "phase_index": self._index + 1,
                "phase_count": len(self.phases),
                "done": self._done,
                "total": self._total,
                **self._context,
                "eta_seconds": self.eta_seconds(),
                "wait": dict(self._wait) if self._wait else None,
                "failed_count": self._failed_count,
                "failed": list(self._failed),
                "breaker": self._breaker,
            }

    def result(self) -> Dict[str, Any]:
        """İş bitince `result`'a yazılacak özet."""
        with self._lock:
            return {
                "details_done": self._details_done,
                "details_total": self._details_total,
                "failed_count": self._failed_count,
                "failed": list(self._failed),
                "breaker": self._breaker,
            }

    def _emit(self, **extra: Any) -> None:
        fields: Dict[str, Any] = {
            "progress": self.percent(),
            "detail": self.detail(),
            "matches_done": self._details_done,
            "matches_total": self._details_total,
            "matches_failed": self._failed_count,
        }
        fields.update(extra)
        self._publish(fields)
