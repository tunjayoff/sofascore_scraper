"""Bir indirme işinin yapılandırılmış ilerlemesi: aşama, iş genelinde sayaç, hatalar, tahmini süre.

Web kartı düz `current_task` metni yerine bunu (`detail`) okur; metinler ön yüzde çevrilir.

Tahmini süre (`eta_seconds`, FX-26, canlı doğrulama M14) üç ölçümün en uzunudur:

  * aşamanın ortalama hızı: başından beri maç başına geçen süre × kalan maç;
  * son dakikaların hızı (`ETA_WINDOW` saniye): aşamanın ilk maçları çoğu zaman hızlı geçer (saklanmış maç
    atlanır, istek bütçesi dolu başlar), ortalama bu yüzden iyimserdir;
  * istek hesabı: iş, maçlarını sınıflara ayırıp planladıysa (`plan_costs`; takım, oyuncu ve maç takiplerinde
    "yalnızca olay" okunan gelecek fikstür ile tam okunan maç) ve biten her maçın gönderdiği isteği bildiriyorsa
    (`note_cost`): kalan maçların sınıflarının şimdiye dek ölçülen maç başına isteğiyle beklenen istek sayısı,
    bu işin ölçülen istek hızına bölünür. Celtics indirmesi (31/128 maç) "69 sn" demişti: ilk maçlar tek istekli
    fikstürlerdi, kalan 97 maçın çoğu ~9 istekliydi; 2 istek/sn'de ~7 dakika sürdü.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple

# Her aşamanın genel yüzdedeki payı. İş planında olmayan aşamaların payı diğerlerine dağılır.
PHASE_WEIGHTS: Dict[str, int] = {"seasons": 5, "matches": 25, "details": 65}

MAX_FAILED_LISTED = 50
# Bu kadar ilerleme ve süre birikmeden tahmini süre verme: ilk birkaç maçın hızı yanıltıcı
_ETA_MIN_DONE = 3
_ETA_MIN_ELAPSED = 5.0
# Son dakikaların hızı bu kadar saniyeden ölçülür; en az bu kadar süre ve adım gerekir (ön yüzün kuralı da bu:
# frontend/src/app/eta.ts)
ETA_WINDOW = 180.0
_ETA_WINDOW_MIN_SPAN = 30.0
_ETA_WINDOW_MIN_STEPS = 2


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
        self._refreshed = 0  # yenilenen geçici kayıt (sofascore_scraper/refresh.py)
        self._refresh_changed = 0  # bunlardan SofaScore'da değişmiş olan
        self._details_done = 0
        self._details_total = 0
        self._wait: Optional[Dict[str, Any]] = None
        self._breaker: Optional[str] = None
        # Tahmini süre: aşamanın (an, biten) örnekleri; sınıf → planlanan maç; sınıf → [biten maç, istek]
        self._samples: Deque[Tuple[float, int]] = deque()
        self._planned: Dict[str, int] = {}
        self._costs: Dict[str, List[int]] = {}
        self._cost_started: Optional[float] = None
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
            self._samples = deque([(self._phase_started, 0)])
            self._planned, self._costs, self._cost_started = {}, {}, None
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
            self._sample()
            if self.phase == "details":
                self._details_done = self._done
            # İlerleme geldiyse bekleme bitmiştir
            if self._wait and self._wait["until"] <= self._wall():
                self._wait = None
            self._emit()

    def plan_costs(self, counts: Mapping[str, int]) -> None:
        """
        Aşamaya eklenen maçların sınıfları (sınıf → maç sayısı; ör. "full", "event_only"): istek hesabının
        kalan maçları. `note_cost` ile birlikte kullanılır; çağrılmazsa tahmin hıza göredir.
        """
        with self._lock:
            for kind, number in counts.items():
                if number > 0:
                    self._planned[kind] = self._planned.get(kind, 0) + int(number)
            if self._cost_started is None and self._planned:
                self._cost_started = self._clock()

    def note_cost(self, kind: str, requests: int) -> None:
        """Planlanan sınıftan bir maç bitti ve SofaScore'a `requests` istek gönderdi."""
        with self._lock:
            cost = self._costs.setdefault(kind, [0, 0])
            cost[0] += 1
            cost[1] += max(0, int(requests))

    def _sample(self) -> None:
        now = self._clock()
        if self._samples and self._samples[-1][1] > self._done:
            self._samples.clear()  # geri giden sayaç: yeni bir ölçüm
        self._samples.append((now, self._done))
        while len(self._samples) > 2 and self._samples[1][0] <= now - ETA_WINDOW:
            self._samples.popleft()

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

    def add_refreshed(self, match_id: str, changed: bool) -> None:
        """Geçici bir kayıt yeniden okundu; changed: basic değişti (score_changes.jsonl'a yazıldı)."""
        with self._lock:
            self._refreshed += 1
            self._refresh_changed += int(bool(changed))
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
        """Mevcut aşamanın kalan süresi: ortalama hız, son dakikaların hızı ve istek hesabının en uzunu (modül belgesi)."""
        with self._lock:
            if self._total <= 0 or self._done < _ETA_MIN_DONE or self._done >= self._total:
                return None
            now = self._clock()
            elapsed = now - self._phase_started
            if elapsed < _ETA_MIN_ELAPSED:
                return None
            left = self._total - self._done
            estimates = [elapsed / self._done * left]
            if len(self._samples) >= 2:
                (first_at, first_done), (last_at, last_done) = self._samples[0], self._samples[-1]
                span, steps = last_at - first_at, last_done - first_done
                if span >= _ETA_WINDOW_MIN_SPAN and steps >= _ETA_WINDOW_MIN_STEPS:
                    estimates.append(span / steps * left)
            by_requests = self._eta_by_requests(now)
            if by_requests is not None:
                estimates.append(by_requests)
            return round(max(estimates), 1)

    def _eta_by_requests(self, now: float) -> Optional[float]:
        """Kalan planlı maçların beklenen isteği / ölçülen istek hızı; ölçü yoksa None."""
        if self._cost_started is None or not self._planned:
            return None
        sent = sum(cost[1] for cost in self._costs.values())
        spent = now - self._cost_started
        if sent <= 0 or spent < _ETA_MIN_ELAPSED:
            return None
        per_match = {kind: cost[1] / cost[0] for kind, cost in self._costs.items() if cost[0]}
        # Henüz biteni olmayan sınıf için ölçülen en pahalı sınıf: tahmin iyimser olmasın
        unknown = max(per_match.values())
        expected = sum(max(0, number - self._costs.get(kind, [0, 0])[0]) * per_match.get(kind, unknown)
                       for kind, number in self._planned.items())
        return expected / (sent / spent) if expected > 0 else None

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
                "refreshed": self._refreshed,
                "refresh_changed": self._refresh_changed,
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
                "refreshed": self._refreshed,
                "refresh_changed": self._refresh_changed,
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
