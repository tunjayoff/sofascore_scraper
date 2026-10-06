"""
Kalıcı olay akışları: sıra numaralı tek bir günlük (docs/design/01-storage.md, bölüm 2.3 "Event streams").

Üreticiler bir akışa (`live`, `change`, `job`, `system`) olay ekler; her tüketici (`ssc events`,
`watch --stdout`, sink dağıtıcısı) aynı günlüğü sıra numarasıyla okur. Satırlar state.db'nin
`stream_events` tablosundadır, sink'lerin konumları `sink_cursors` tablosunda.

Sıra numaraları:
  * Bütün akışlar için **tek sıra** vardır (`INTEGER PRIMARY KEY AUTOINCREMENT`): numaralar kesin artar ve
    budamadan sonra yeniden kullanılmaz. Bir akışın içinde artar ama ardışık değildir: başka akışların
    satırları araya girer ve yinelendiği için saklanmayan bir olay da numara harcar. Tüketici `n`'den sonra
    `n + 1` geleceğini varsaymamalıdır.
  * Her ekleme `BEGIN IMMEDIATE` işlemindedir, yani numaralar kayıt (commit) sırasıyla verilir: okuyan,
    daha küçük numaralı bir satır henüz kaydedilmemişken daha büyüğünü görmez. `read` bu yüzden "okuduğum
    son numara"yı güvenle ilerletebilir.
  * `stream_id` state.db'de duran bir UUID'dir; state.db yeniden yaratılırsa ya da bir yedekten geri yüklenirse
    (yedeğin state.db'si kendi `stream_id`'siyle gelir) değişir ve tüketiciye sakladığı konumun artık anlamsız
    olduğunu söyler.

`wait`, aynı süreçteki eklemeler için süreç içi bir koşul değişkeni kullanır; başka süreçlerin
eklemelerini `PRAGMA data_version`'ı WAIT_POLL_SECONDS aralıkla yoklayarak görür (SQLite'ın süreçler arası
bildirimi yoktur).

state.db üzerinde ANALYZE çalıştırılmaz: istatistik varken planlayıcı tek akışlı okumayı
`stream_events_stream` dizini yerine rowid aralığıyla yapar (bölüm 3.7; tests/test_store_query_plans.py).
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from src.store.errors import StoreError

if TYPE_CHECKING:
    from src.store.api import Store

logger = logging.getLogger("Store")

LIVE_STREAM = "live"  # canlı servis: live.status_changed, live.score_changed, live.stuck ...
CHANGE_STREAM = "change"  # değişiklik günlüğünün her satırı için bir bildirim: change.recorded
JOB_STREAM = "job"  # job.started, job.finished
SYSTEM_STREAM = "system"  # system.blocked, system.recovered, system.live_source_changed, system.sink_dropped
STREAMS: Tuple[str, ...] = (LIVE_STREAM, CHANGE_STREAM, JOB_STREAM, SYSTEM_STREAM)

DEFAULT_READ_LIMIT = 500
WAIT_POLL_SECONDS = 0.2  # başka süreçlerin eklemeleri bu aralıkla yoklanır

# Günlüğün saklama süresi (docs/design/01-storage.md 9.3): `prune`'un varsayılanları. Budamayı `live` ya da
# `sinks` kilidini tutan süreç saatte bir çalıştırır (src/sinks/dispatcher.py, src/services/live/supervisor.py).
DEFAULT_PRUNE_MAX_AGE_SECONDS = 7 * 24 * 3600.0
DEFAULT_PRUNE_MAX_ROWS = 1_000_000

META_STREAM_ID = "stream_id"
META_PRUNED = "stream_pruned"  # JSON: akış adı → o akışta budanan en büyük sıra numarası

_STREAM_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_TABLE = "stream_events"
_COLUMNS = "seq, stream, ts_ms, type, event_id, sport, tournament_id, source, dedup_key, payload_json"
_INSERT = (
    "INSERT INTO stream_events (stream, ts_ms, type, event_id, sport, tournament_id, source, dedup_key, payload_json) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (stream, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING"
)


@dataclass(frozen=True)
class StreamEvent:
    """
    Bir üreticinin akışa eklediği olay. `data` JSON'a çevrilebilir olmalıdır.

    dedup_key: aynı akışta bu anahtarla bir satır zaten varsa olay yeniden saklanmaz (ör. aynı geçişi hem
    push hem yoklama gördü). None: yinelenme denetimi yok.
    ts: olayın kaydedildiği an (epoch saniye, UTC); None ise ekleme anı.
    """

    type: str  # "live.status_changed", "job.finished" ...
    data: Mapping[str, Any] = field(default_factory=dict)
    event_id: Optional[int] = None
    sport: Optional[str] = None
    tournament_id: Optional[int] = None
    source: Optional[str] = None  # push | poll | job | system
    dedup_key: Optional[str] = None
    ts: Optional[float] = None


@dataclass(frozen=True)
class StreamRecord:
    """Günlükte saklanan bir olay: `StreamEvent` alanları ile sıra numarası, akış adı ve kayıt anı."""

    seq: int
    stream: str
    ts: float  # epoch saniye (UTC); milisaniye çözünürlüğünde
    type: str
    data: Mapping[str, Any]
    event_id: Optional[int] = None
    sport: Optional[str] = None
    tournament_id: Optional[int] = None
    source: Optional[str] = None
    dedup_key: Optional[str] = None


@dataclass(frozen=True)
class StreamBatch:
    """
    `StreamLog.read` sonucu.

    last_seq: bu okumanın kapsadığı son sıra numarası; bir sonraki okumaya `after` olarak verilir. Süzgeçle
    okunurken eşleşmeyen satırların üzerinden de ilerler, `after`'dan küçük olmaz.
    gap: True ise `after`'dan sonraki satırların bir kısmı okunmadan budanmış (tüketici geride kalmış).
    """

    stream_id: str
    events: Tuple[StreamRecord, ...]
    last_seq: int
    gap: bool


@dataclass(frozen=True)
class StreamHead:
    """Günlüğün uçları. Günlük boşsa first_seq 0'dır; last_seq o zaman da verilmiş son numarada kalır."""

    stream_id: str
    first_seq: int
    last_seq: int


@dataclass(frozen=True)
class SinkCursor:
    """
    Bir sink'in kayıtlı konumu (`sink_cursors` satırı). Kaldırılmış sink'lerin satırları da kalır.

    seq         sink'e en son teslim edilen sıra numarası
    updated_at  satırın son yazıldığı an (epoch saniye)
    last_error  son teslim denemesinin hatası; None: hata yok
    """

    sink: str
    seq: int
    updated_at: int
    last_error: Optional[str] = None


def _check_stream(stream: Any) -> str:
    if not isinstance(stream, str) or not _STREAM_RE.fullmatch(stream):
        raise StoreError(f"Geçersiz akış adı: {stream!r}", detail="stream")
    return stream


def _optional_int(value: Any, what: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreError(f"Akış olayında {what} tam sayı olmalı: {value!r}", detail=what)
    return value


def _optional_text(value: Any, what: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StoreError(f"Akış olayında {what} metin olmalı: {value!r}", detail=what)
    return value


def _placeholders(values: Sequence[Any]) -> str:
    return ", ".join("?" for _ in values)


class StreamLog:
    """`Store.streams`: akışlara ekleme, sıra numarasıyla okuma, bekleme, budama ve sink konumları."""

    def __init__(self, store: "Store") -> None:
        self._state = store._state
        self._appended = threading.Condition()
        self._stream_id: Optional[str] = None

    # --- ekleme -----------------------------------------------------------------------------

    def append(self, stream: str, events: Sequence[StreamEvent]) -> List[Optional[int]]:
        """
        Olayları tek işlemde ekler ve sıra numaralarını (verilen sırayla) döndürür. `dedup_key`'i akışta
        zaten bulunan olay saklanmaz ve yerine None döner; aynı çağrıdaki ikinci kopya için de böyledir.

        Çağıran zaten bir `StateDb.write()` işlemindeyse o işleme katılır: satırlar ancak dıştaki işlem
        kaydedildiğinde görünür olur.
        """
        _check_stream(stream)
        rows = [self._row(stream, event) for event in events]
        if not rows:
            return []
        seqs: List[Optional[int]] = []
        with self._state.write() as conn:
            for row in rows:
                cursor = conn.execute(_INSERT, row)
                seqs.append(int(cursor.lastrowid) if cursor.rowcount == 1 and cursor.lastrowid is not None else None)
        with self._appended:
            self._appended.notify_all()
        return seqs

    @staticmethod
    def _row(stream: str, event: StreamEvent) -> Tuple[Any, ...]:
        if not isinstance(event.type, str) or not event.type:
            raise StoreError(f"Akış olayının türü boş olamaz: {event.type!r}", detail="type")
        try:
            payload = json.dumps(dict(event.data), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as e:
            raise StoreError(f"Akış olayı JSON'a çevrilemedi: {event.type} ({e})", detail=str(e)) from e
        try:
            ts_ms = int(round((time.time() if event.ts is None else float(event.ts)) * 1000))
        except (TypeError, ValueError, OverflowError) as e:
            raise StoreError(f"Akış olayının zamanı geçersiz: {event.ts!r}", detail="ts") from e
        return (
            stream,
            ts_ms,
            event.type,
            _optional_int(event.event_id, "event_id"),
            _optional_text(event.sport, "sport"),
            _optional_int(event.tournament_id, "tournament_id"),
            _optional_text(event.source, "source"),
            _optional_text(event.dedup_key, "dedup_key"),
            payload,
        )

    # --- okuma ------------------------------------------------------------------------------

    def read(self, *, after: int = 0, limit: int = DEFAULT_READ_LIMIT, streams: Sequence[str] = (),
             types: Sequence[str] = (), event_ids: Sequence[int] = (), sport: Optional[str] = None,
             tournament_ids: Sequence[int] = ()) -> StreamBatch:
        """
        Sıra numarası `after`'dan büyük olayları, numara sırasıyla, en çok `limit` tane döndürür.
        Süzgeçler birlikte uygulanır (VE); boş süzgeç "hepsi" demektir. `types` tam ad eşleşmesidir.

        Devam etmek için sonucun `last_seq` değeri `after` olarak verilir: aynı olay iki kez gelmez ve
        hiçbiri atlanmaz. `gap=True`, istenen akışlarda `after`'dan sonraki satırların budanmış olduğunu
        söyler (yalnızca akışa göre bilinir; öteki süzgeçler hesaba katılmaz).
        """
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise StoreError(f"after sıfır ya da pozitif bir sıra numarası olmalı: {after!r}", detail="after")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise StoreError(f"limit en az 1 olmalı: {limit!r}", detail="limit")
        names = [_check_stream(name) for name in dict.fromkeys(streams)]
        where: List[str] = []
        params: List[Any] = []
        if len(names) == 1:
            where.append("stream = ?")  # stream_events_stream dizini (bölüm 3.7)
            params.append(names[0])
        where.append("seq > ?")
        params.append(after)
        if len(names) > 1:
            # Birden çok akış: dizin akış başına ayrı aralık verir ve sıralama ister; rowid sırasıyla
            # yürüyüp süzmek LIMIT'e erken ulaşır. Tekli artı, dizinin seçilmesini engeller.
            where.append(f"+stream IN ({_placeholders(names)})")
            params.extend(names)
        for column, values in (("type", list(types)), ("event_id", list(event_ids)),
                               ("tournament_id", list(tournament_ids))):
            if values:
                where.append(f"{column} IN ({_placeholders(values)})")
                params.extend(values)
        if sport is not None:
            where.append("sport = ?")
            params.append(sport)
        stream_id = self._ensure_stream_id()
        # Satırlar, uç ve budama izleri aynı anlık görüntüden okunur: `last_seq` okunmamış bir satırın
        # üzerinden atlamaz ve `gap` bu okumanın gördüğü durumu anlatır.
        with self._snapshot() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM {_TABLE} WHERE {' AND '.join(where)} ORDER BY seq LIMIT ?", (*params, limit)
            ).fetchall()
            head = self._last_seq(conn)
            pruned = self._pruned(conn)
        events = tuple(self._record(row) for row in rows)
        # Sınır dolduysa uca kadar başka eşleşen satır olabilir: ancak son dönen satıra kadar ilerlenir
        last_seq = events[-1].seq if len(events) == limit else max(after, head)
        marks = [pruned.get(name, 0) for name in names] if names else list(pruned.values())
        return StreamBatch(stream_id=stream_id, events=events, last_seq=last_seq,
                           gap=any(after < mark for mark in marks))

    @contextlib.contextmanager
    def _snapshot(self) -> Iterator[sqlite3.Connection]:
        """Birden çok okumayı tek anlık görüntüden yapar. Çağıran zaten bir işlemdeyse onun içinde okur."""
        conn = self._state.connection()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            if conn.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("COMMIT")

    @staticmethod
    def _record(row: sqlite3.Row) -> StreamRecord:
        try:
            data = json.loads(row["payload_json"])
        except ValueError:
            data = None
        if not isinstance(data, dict):
            # Kendi yazdığımız bir satır okunamıyor (dosya hasarı): tüketici bu satırda takılıp kalmasın
            logger.warning("Stream event %s has an unreadable payload; delivered with empty data", row["seq"])
            data = {}
        return StreamRecord(
            seq=int(row["seq"]), stream=str(row["stream"]), ts=int(row["ts_ms"]) / 1000, type=str(row["type"]),
            data=data, event_id=row["event_id"], sport=row["sport"], tournament_id=row["tournament_id"],
            source=row["source"], dedup_key=row["dedup_key"],
        )

    @staticmethod
    def _last_seq(conn: sqlite3.Connection) -> int:
        """Saklanan son satırın numarası; günlük boşsa (hiç yazılmamış ya da tümü budanmış) verilmiş son numara."""
        row = conn.execute(f"SELECT max(seq) FROM {_TABLE}").fetchone()
        if row[0] is not None:
            return int(row[0])
        row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (_TABLE,)).fetchone()
        return int(row[0]) if row is not None else 0

    def head(self) -> StreamHead:
        """Günlüğün kimliği ile ilk ve son sıra numarası (bütün akışlar)."""
        stream_id = self._ensure_stream_id()
        with self._snapshot() as conn:
            first = conn.execute(f"SELECT min(seq) FROM {_TABLE}").fetchone()[0]
            last = self._last_seq(conn)
        return StreamHead(stream_id=stream_id, first_seq=int(first or 0), last_seq=last)

    def _ensure_stream_id(self) -> str:
        if self._stream_id is not None:
            return self._stream_id
        value = self._state.meta_get(META_STREAM_ID)
        if value is None:
            outer = self._state.connection().in_transaction
            with self._state.write() as conn:  # iki süreç aynı anda üretirse ilki kalır
                conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                             (META_STREAM_ID, uuid.uuid4().hex))
                value = str(conn.execute("SELECT value FROM meta WHERE key = ?", (META_STREAM_ID,)).fetchone()[0])
            if outer:
                return value  # çağıranın işlemi geri alınabilir: kimlik kaydedilene kadar akılda tutulmaz
        self._stream_id = value
        return value

    # --- bekleme ----------------------------------------------------------------------------

    def wait(self, *, after: int, timeout: float) -> bool:
        """
        Sıra numarası `after`'dan büyük bir satır var olur olmaz True; `timeout` saniye içinde olmazsa False.
        Aynı süreçteki ekleme hemen, başka bir süreçteki ekleme en geç WAIT_POLL_SECONDS sonra uyandırır.
        """
        conn = self._state.connection()
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._appended:
            # Sürüm yoklamadan önce okunur: yoklama ile bekleme arasındaki bir kayıt gözden kaçmaz
            version = self._data_version(conn)
            while True:
                if conn.execute(f"SELECT 1 FROM {_TABLE} WHERE seq > ? LIMIT 1", (after,)).fetchone() is not None:
                    return True
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    notified = self._appended.wait(min(remaining, WAIT_POLL_SECONDS))
                    current = self._data_version(conn)
                    if notified or current != version:
                        version = current
                        break

    @staticmethod
    def _data_version(conn: sqlite3.Connection) -> int:
        """Dosyayı başka bir bağlantı (bu süreçte ya da başka süreçte) her değiştirdiğinde değişen sayaç."""
        return int(conn.execute("PRAGMA data_version").fetchone()[0])

    # --- budama -----------------------------------------------------------------------------

    def prune(self, *, max_age_s: Optional[float] = DEFAULT_PRUNE_MAX_AGE_SECONDS,
              max_rows: Optional[int] = DEFAULT_PRUNE_MAX_ROWS) -> int:
        """
        `max_age_s` saniyeden eski satırları ve en yeni `max_rows` satırın dışında kalanları siler; silinen
        satır sayısını döndürür. Varsayılanlar bölüm 9.3'ünkiler (7 gün, 1.000.000 satır); None o ölçütü
        kapatır, ikisi de None ise hiçbir şey silinmez. Silinenlerin gerisinde kalmış bir tüketici bir sonraki
        `read`'de `gap=True` görür.
        """
        if max_age_s is not None and max_age_s < 0:
            raise StoreError(f"max_age_s negatif olamaz: {max_age_s!r}", detail="max_age_s")
        if max_rows is not None and (isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 0):
            raise StoreError(f"max_rows sıfır ya da pozitif bir tam sayı olmalı: {max_rows!r}", detail="max_rows")
        if max_age_s is None and max_rows is None:
            return 0
        removed = 0
        with self._state.write() as conn:
            marks = self._pruned(conn)
            if max_age_s is not None:
                cutoff = int(round((time.time() - max_age_s) * 1000))
                removed += self._delete(conn, "ts_ms < ?", cutoff, marks)
            if max_rows is not None:
                row = conn.execute(f"SELECT seq FROM {_TABLE} ORDER BY seq DESC LIMIT 1 OFFSET ?",
                                   (max_rows,)).fetchone()
                if row is not None:
                    removed += self._delete(conn, "seq <= ?", int(row[0]), marks)
            if removed:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (META_PRUNED, json.dumps(marks, sort_keys=True, separators=(",", ":"))),
                )
        return removed

    @staticmethod
    def _delete(conn: sqlite3.Connection, condition: str, value: int, marks: Dict[str, int]) -> int:
        """Koşula uyan satırları siler; akış başına silinen en büyük numarayı `marks`'a işler."""
        for stream, seq in conn.execute(f"SELECT stream, max(seq) FROM {_TABLE} WHERE {condition} GROUP BY stream",
                                        (value,)):
            marks[str(stream)] = max(marks.get(str(stream), 0), int(seq))
        return int(conn.execute(f"DELETE FROM {_TABLE} WHERE {condition}", (value,)).rowcount)

    @staticmethod
    def _pruned(conn: sqlite3.Connection) -> Dict[str, int]:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_PRUNED,)).fetchone()
        if row is None:
            return {}
        try:
            data = json.loads(row[0])
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): int(v) for k, v in data.items() if isinstance(v, int) and not isinstance(v, bool)}

    # --- sink konumları ---------------------------------------------------------------------

    def cursor(self, sink: str) -> int:
        """Sink'e en son teslim edilen sıra numarası; kayıt yoksa 0."""
        row = self._state.connection().execute("SELECT seq FROM sink_cursors WHERE sink = ?", (sink,)).fetchone()
        return int(row[0]) if row is not None else 0

    def cursors(self) -> List[SinkCursor]:
        """
        Kayıtlı bütün sink konumları, ada göre sıralı (`ssc status` sink başına konum, gecikme ve son hata için
        okur). Hiç teslim yapmamış bir sink'in satırı yoktur; kaldırılmış sink'lerin satırları kalır.
        """
        rows = self._state.connection().execute(
            "SELECT sink, seq, updated_at, last_error FROM sink_cursors ORDER BY sink").fetchall()
        return [SinkCursor(sink=str(row[0]), seq=int(row[1]), updated_at=int(row[2]),
                           last_error=None if row[3] is None else str(row[3])) for row in rows]

    def set_cursor(self, sink: str, seq: int, *, error: Optional[str] = None) -> None:
        """
        Sink'in konumunu yazar. `error`: son teslim denemesinin hatası (konum ilerlemeden de yazılabilir);
        None önceki hatayı siler.
        """
        if not isinstance(sink, str) or not sink:
            raise StoreError(f"Sink adı boş olamaz: {sink!r}", detail="sink")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise StoreError(f"Sink konumu sıfır ya da pozitif bir sıra numarası olmalı: {seq!r}", detail="seq")
        with self._state.write() as conn:
            conn.execute(
                """
                INSERT INTO sink_cursors (sink, seq, updated_at, last_error) VALUES (?, ?, ?, ?)
                ON CONFLICT(sink) DO UPDATE SET
                    seq = excluded.seq, updated_at = excluded.updated_at, last_error = excluded.last_error
                """,
                (sink, seq, int(time.time()), error),
            )


__all__ = [
    "CHANGE_STREAM",
    "DEFAULT_PRUNE_MAX_AGE_SECONDS",
    "DEFAULT_PRUNE_MAX_ROWS",
    "DEFAULT_READ_LIMIT",
    "JOB_STREAM",
    "LIVE_STREAM",
    "STREAMS",
    "SYSTEM_STREAM",
    "WAIT_POLL_SECONDS",
    "SinkCursor",
    "StreamBatch",
    "StreamEvent",
    "StreamHead",
    "StreamLog",
    "StreamRecord",
]
