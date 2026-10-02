"""
Sink'lerin ortak parçaları (docs/design/02-services.md bölüm 5): zarf, süzgeç, hata sınıfları, protokoller.

Üreticiler bir sink'i hiç çağırmaz: olayı `store.streams.append` ile kalıcı günlüğe ekler. Sink'ler aynı
günlüğü sıra numarasıyla okuyan tüketicilerdir; canlı verinin süreçten çıktığı tek yol budur (HTTP uç
noktası yoktur).

Zarf (`sofascore.event/1`), her sink'in yazdığı tek biçim:

    {"stream": "live", "seq": 1842, "type": "live.status_changed", "ts": "2026-10-01T18:52:04.000Z",
     "event_id": 17124861, "sport": "football", "tournament_id": 17, "source": "poll", "data": {...}}

  * `seq` bir veri dizininin bütün akışları için tek sıradır; kesin artar ama ardışık değildir. Tüketici
    `n`'den sonra `n + 1` geleceğini varsaymaz ve yinelenenleri `seq` ile ayıklar (teslim en az bir kezdir).
  * `ts` olayın kaydedildiği andır: ISO-8601, UTC, milisaniye çözünürlüğünde ve `Z` ile.
  * Olmayan alan `null`dır. Yinelenme anahtarı (`dedup_key`) günlüğün iç işidir, zarfa girmez.

Bu modül yalnızca standart kütüphaneyi ister; Store'un sınıfları tür olarak anılır, çalışırken içe aktarılmaz.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

if TYPE_CHECKING:
    from src.store import StreamRecord

EVENT_SCHEMA = "sofascore.event/1"

# Günlüğün akışları (src/store/streams.py ile aynı; tests/test_sinks.py eşitliği sınar)
STREAMS: Tuple[str, ...] = ("live", "change", "job", "system")
SYSTEM_STREAM = "system"
SYSTEM_SOURCE = "system"
# Bir sink'e teslim edilemeden bırakılan olayların kaydı: {sink, reason, first_seq, last_seq, count, ...}
SINK_DROPPED = "system.sink_dropped"

DEFAULT_BATCH_SIZE = 500  # bir `deliver` çağrısındaki en çok olay (stdout, dosya); webhook kendi sınırını verir


# --- hatalar -------------------------------------------------------------------------------------


class SinkError(Exception):
    """Teslim hatası. İleti loglara, sink'in `last_error` alanına ve akış olaylarına girer: gizli değer taşımaz."""


class RetryableSinkError(SinkError):
    """
    Geçici hata (ağ, 5xx, dolu disk): aynı toplu gönderim artan aralıklarla yeniden denenir.
    `retry_after`: alıcının istediği bekleme (saniye; `Retry-After` başlığı), varsa.
    """

    def __init__(self, message: str, *, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class FatalSinkError(SinkError):
    """Kalıcı hata (alıcı 410 döndü): sink süreç yeniden başlayana kadar kapatılır, konumu yerinde kalır."""


# --- zarf ----------------------------------------------------------------------------------------


def iso_utc(ts: float) -> str:
    """Epoch saniye → "2026-10-01T18:52:04.123Z" (her zaman milisaniyeli: ayrıştıran tek biçim görür)."""
    ms = int(round(float(ts) * 1000))
    moment = dt.datetime.fromtimestamp(ms // 1000, tz=dt.timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{ms % 1000:03d}Z"


def encode(document: Any) -> str:
    """
    Tek satırlık JSON metni. ASCII'dir: hangi konsol kod sayfasına, dosya kodlamasına ya da HTTP gövdesine
    yazılırsa yazılsın aynı baytlar çıkar (webhook imzası gövdenin baytları üzerindedir).
    """
    return json.dumps(document, ensure_ascii=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class Envelope:
    """
    Günlükten okunmuş bir olay, sink'lerin gördüğü haliyle. `stream_id` zarfın JSON'una girmez: sıra
    numaralarının hangi günlüğe ait olduğunu söyler (webhook'un `Idempotency-Key` başlığı onu taşır).
    """

    stream: str
    seq: int
    type: str
    ts: float  # epoch saniye (UTC)
    data: Mapping[str, Any] = field(default_factory=dict)
    event_id: Optional[int] = None
    sport: Optional[str] = None
    tournament_id: Optional[int] = None
    source: Optional[str] = None
    stream_id: str = ""

    @classmethod
    def from_record(cls, record: "StreamRecord", stream_id: str = "") -> "Envelope":
        return cls(
            stream=record.stream, seq=record.seq, type=record.type, ts=record.ts, data=record.data,
            event_id=record.event_id, sport=record.sport, tournament_id=record.tournament_id,
            source=record.source, stream_id=stream_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        """`sofascore.event/1` zarfı; alan sırası tasarımdaki örneğin sırasıdır."""
        return {
            "stream": self.stream,
            "seq": self.seq,
            "type": self.type,
            "ts": iso_utc(self.ts),
            "event_id": self.event_id,
            "sport": self.sport,
            "tournament_id": self.tournament_id,
            "source": self.source,
            "data": dict(self.data),
        }

    def to_json(self) -> str:
        return encode(self.to_dict())


# --- süzgeç --------------------------------------------------------------------------------------

_GLOB_CHARS = frozenset("*?[")


class EventFilter:
    """
    Bir sink'in istediği olaylar: tür kalıpları (`live.*`, `job.finished`, `*`) ve isteğe bağlı spor, turnuva,
    maç süzgeçleri; hepsi birlikte uygulanır (VE). Kalıplar büyük/küçük harfe duyarlı kabuk kalıplarıdır
    (`*`, `?`, `[...]`); Store'un okuması tam ad eşleştirdiği için kalıp eşleştirmek sink'in işidir.
    """

    def __init__(self, patterns: Iterable[str] = ("*",), *, sports: Iterable[str] = (),
                 tournament_ids: Iterable[int] = (), event_ids: Iterable[int] = ()) -> None:
        self.patterns: Tuple[str, ...] = tuple(dict.fromkeys(patterns))
        if not self.patterns or any(not isinstance(p, str) or not p.strip() for p in self.patterns):
            raise ValueError("events: expected at least one event type or pattern such as \"live.*\"")
        self._match_all = "*" in self.patterns
        self._exact = frozenset(p for p in self.patterns if not _GLOB_CHARS & set(p))
        self._globs = tuple(p for p in self.patterns if _GLOB_CHARS & set(p))
        self.sports = frozenset(sports)
        self.tournament_ids = frozenset(tournament_ids)
        self.event_ids = frozenset(event_ids)

    @property
    def exact_types(self) -> Tuple[str, ...]:
        """Kalıp içermeyen bir süzgecin tür adları (Store'a `types=` olarak verilebilir); kalıp varsa boş."""
        return () if self._globs else tuple(sorted(self._exact))

    def matches_type(self, type_: str) -> bool:
        if self._match_all or type_ in self._exact:
            return True
        return any(fnmatch.fnmatchcase(type_, pattern) for pattern in self._globs)

    def matches(self, env: Envelope) -> bool:
        if self.sports and env.sport not in self.sports:
            return False
        if self.tournament_ids and env.tournament_id not in self.tournament_ids:
            return False
        if self.event_ids and env.event_id not in self.event_ids:
            return False
        return self.matches_type(env.type)


# --- saat ve durdurma ----------------------------------------------------------------------------


class Clock(Protocol):
    """Dağıtıcının ve sink'lerin saati; testler sahtesini verir (yeniden deneme aralıkları gerçekte beklenmez)."""

    def time(self) -> float: ...  # epoch saniye (UTC)

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class StopToken(Protocol):
    """Uzun çalışan bir döngüyü durdurma isteği; `threading.Event` bu protokole uyar."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: Optional[float] = None) -> bool: ...


# --- sink ----------------------------------------------------------------------------------------


@runtime_checkable
class Sink(Protocol):
    """
    Bir çıktı hedefi. `deliver` toplu gönderimi ya tümüyle teslim eder ya da hata fırlatır
    (RetryableSinkError / FatalSinkError); dağıtıcı konumu yalnızca başarıdan sonra ilerletir.
    """

    name: str

    def accepts(self, env: Envelope) -> bool: ...

    def deliver(self, batch: Sequence[Envelope]) -> None: ...

    def close(self) -> None: ...


class BaseSink:
    """
    Sink'lerin ortak tabanı: ad, süzgeç ve dağıtıcının okuduğu ayarlar. Dağıtıcı bu alanları `getattr` ile ve
    aşağıdaki varsayılanlarla okur; yalnızca `Sink` protokolüne uyan bir nesne de teslim alabilir.

    uses_cursor      konum state.db'de tutulur ve yeniden başlamada kalınan yerden sürülür. False (stdout):
                     konum yalnızca bellekte, her başlangıç "şimdi"den
    batch_size       bir `deliver` çağrısına verilen en çok olay
    linger_seconds   toplu gönderim dolmadıysa en çok bu kadar beklenir (webhook: 1 sn); 0: hemen
    max_age_seconds  teslim edilemeyen olay bu yaştan sonra bırakılır ve `system.sink_dropped` yazılır;
                     None: hiçbir zaman bırakılmaz (sink baştaki toplu gönderimde bekler)
    """

    uses_cursor: bool = True
    batch_size: int = DEFAULT_BATCH_SIZE
    linger_seconds: float = 0.0
    max_age_seconds: Optional[float] = None

    def __init__(self, name: str, events: Optional[EventFilter] = None) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("a sink needs a name")
        self.name = name
        self.filter = events if events is not None else EventFilter()

    def accepts(self, env: Envelope) -> bool:
        return self.filter.matches(env)

    def deliver(self, batch: Sequence[Envelope]) -> None:  # pragma: no cover - alt sınıflar uygular
        raise NotImplementedError

    def close(self) -> None:
        """Açık dosya ya da bağlantı varsa kapatır; yinelenen çağrı zararsızdır."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "EVENT_SCHEMA",
    "SINK_DROPPED",
    "STREAMS",
    "SYSTEM_SOURCE",
    "SYSTEM_STREAM",
    "BaseSink",
    "Clock",
    "Envelope",
    "EventFilter",
    "FatalSinkError",
    "RetryableSinkError",
    "Sink",
    "SinkError",
    "StopToken",
    "SystemClock",
    "encode",
    "iso_utc",
]
