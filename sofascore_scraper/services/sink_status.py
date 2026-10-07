"""
Sink'lerin durumu (docs/design/05-web-ui.md 6.13 ve 7.3 G1; plan maddesi P21): yapılandırmadaki her sink'in olay
günlüğünde nereye kadar teslim ettiği, ne kadar geride kaldığı, son hatası ve bıraktığı olaylar.

Yalnızca okur: konumlar `sink_cursors` satırlarından (`Store.streams.cursors`), gecikme günlüğün başından ve
teslim edilmemiş ilk olayın zamanından, bırakılan olaylar günlükte kalan `system.sink_dropped` olaylarından
sayılır. Sink'ler yalnızca yapılandırma dosyasından ve ortamdan gelir (karar D11); bu modül sink kurmaz ve
teslim etmez. Adreslerin kimlik bilgisi `redact.mask_webhook_url` ile maskelenir.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from sofascore_scraper.redact import mask_webhook_url

if TYPE_CHECKING:
    from sofascore_scraper.config import SinkSpec
    from sofascore_scraper.store import Store

SINKS_LEASE = "sinks"  # sofascore_scraper/sinks/dispatcher.py LEASE_NAME
DROPPED_TYPE = "system.sink_dropped"  # sofascore_scraper/sinks/base.py SINK_DROPPED
STATE_OK = "ok"
STATE_ERROR = "error"
STATE_PENDING = "pending"
_READ_LIMIT = 1000


@dataclass(frozen=True)
class SinkState:
    """
    Bir sink'in durumu. cursor: sink'e teslim edilen son olayın sıra numarası (hiç teslim yoksa 0); lag_events:
    günlükte ondan sonraki olaylar (sink'in süzgecinden önce sayılır); lag_seconds: teslim edilmemiş en eski
    olayın yaşı, yoksa None; delivered_at: konumun son yazıldığı an (epoch saniye); served: bir süreç `sinks`
    kilidini tutuyor, yani teslim ediyor.
    """

    name: str
    type: str
    target: Optional[str]
    events: Tuple[str, ...]
    state: str
    served: bool
    cursor: int
    head_seq: int
    lag_events: int
    lag_seconds: Optional[float]
    delivered_at: Optional[int]
    last_error: Optional[str]
    dropped: int


def target_of(spec: "SinkSpec") -> Optional[str]:
    """Gösterilecek hedef: dosya yolu ya da kimlik bilgisi maskelenmiş webhook adresi; stdout için None."""
    if spec.type == "webhook":
        return mask_webhook_url(spec.url) if spec.url else None
    if spec.type == "file":
        return spec.path or None
    return None


def dropped_counts(store: "Store") -> Dict[str, int]:
    """Günlükte kalan `system.sink_dropped` olaylarından sink başına bırakılan olay sayısı."""
    counts: Dict[str, int] = {}
    after = 0
    while True:
        batch = store.streams.read(after=after, limit=_READ_LIMIT, types=(DROPPED_TYPE,))
        for record in batch.events:
            name = record.data.get("sink")
            if isinstance(name, str):
                counts[name] = counts.get(name, 0) + int(record.data.get("count") or 0)
        if len(batch.events) < _READ_LIMIT or batch.last_seq <= after:
            return counts
        after = batch.last_seq


def _lag_seconds(store: "Store", cursor: int, head: int, now: float) -> Optional[float]:
    if head <= cursor:
        return None
    batch = store.streams.read(after=cursor, limit=1)
    if not batch.events:
        return None
    return round(max(0.0, now - batch.events[0].ts), 3)


def sink_states(store: "Store", specs: Sequence["SinkSpec"], *, now: Optional[float] = None) -> List[SinkState]:
    """Yapılandırmadaki sink'lerin durumu, yapılandırmadaki sırayla. Sink yoksa depoya bakılmaz."""
    if not specs:
        return []
    moment = time.time() if now is None else now
    head = store.streams.head().last_seq
    cursors = {c.sink: c for c in store.streams.cursors()}
    served = store.lease_holder(SINKS_LEASE) is not None
    dropped = dropped_counts(store)
    out: List[SinkState] = []
    for spec in specs:
        found = cursors.get(spec.name)
        cursor = found.seq if found is not None else 0
        state = STATE_PENDING if found is None else (STATE_ERROR if found.last_error else STATE_OK)
        out.append(SinkState(
            name=spec.name,
            type=spec.type,
            target=target_of(spec),
            events=tuple(spec.events),
            state=state,
            served=served,
            cursor=cursor,
            head_seq=head,
            lag_events=max(0, head - cursor),
            lag_seconds=_lag_seconds(store, cursor, head, moment),
            delivered_at=found.updated_at if found is not None else None,
            last_error=found.last_error if found is not None else None,
            dropped=dropped.get(spec.name, 0),
        ))
    return out


__all__ = ["DROPPED_TYPE", "SINKS_LEASE", "SinkState", "dropped_counts", "sink_states", "target_of"]
