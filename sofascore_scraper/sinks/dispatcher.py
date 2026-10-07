"""
Sink dağıtıcısı (docs/design/02-services.md bölüm 5.2): olay günlüğünü okur ve her sink'e sırayla teslim eder.

  * Sink başına bir konum (`store.streams.cursor(sink)`): konumdan sonrası okunur, sırayla teslim edilir ve
    konum yalnızca başarıdan sonra ilerler. Teslim en az bir kezdir, sıralıdır ve yeniden başlamada kalınan
    yerden sürer; alıcı yinelenenleri `seq` ile ayıklar.
  * Aynı anda tek süreç dağıtır (`sinks` kilidi). Uzun çalışan süreç (`run`) kilit başkasındaysa bekler ve
    kilit boşalınca devralır; tek seferlik komut (`drain`) kilidi alamazsa birikmiş olayları sahibine bırakır.
  * Hata: `RetryableSinkError` artan aralıklarla (1 sn → 5 dk, serpiştirmeli) yeniden denenir ve baştaki toplu
    gönderim o sink'i bekletir, ileri atlanmaz. `FatalSinkError` sink'i süreç yeniden başlayana kadar kapatır.
    `max_age_seconds`'ı olan bir sink'te (webhook: 24 saat) o yaştan eski, teslim edilemeyen olaylar bırakılır
    ve aralık `system.sink_dropped` olayıyla kaydedilir.
  * Yeni bir sink "şimdi"den başlar: yapılandırmaya eklenen bir webhook günlükte birikmiş eski olayları
    almaz. Sink'in bilindiği state.db'ye yazılır (`store.runtime`, anahtar "sink:<ad>"); bilinen sink
    konumundan sürer. state.db yeniden yaratılırsa kayıt da konum da gider ve sink yeni günlüğün başından sürer.
  * Okunmadan budanmış olaylar (`StreamBatch.gap`) kayıp sayılır: `system.sink_dropped` (reason "pruned").
  * Dağıtıcı `sinks` kilidini tutarken günlüğü saatte bir budar (01-storage.md 9.3: 7 gün, 1.000.000 satır).
  * Konum tutmayan sink'ler (stdout) kilitsiz de izlenebilir (`follow`): `watch --stdout` böyle çalışır.
  * Durdurma ve süre sınırı her teslimden sonra sorulur: durdurulan `run` ya da süresi dolan `drain` en çok
    süren tek bir teslimi (webhook: isteğin zaman aşımı) bekler, sıradaki toplu gönderimlere başlamaz.
    Teslimler bu thread'de sırayla yapılır: `deliver`'ı hiç dönmeyen bir sink (okuyanı duran bir boru, yanıt
    vermeyen bir ağ dosya sistemi) diğer sink'leri ve kapanışı da bekletir; webhook kendi zaman aşımıyla döner.
  * state.db okunamaz ya da yazılamazsa (meşgul, G/Ç hatası) dağıtıcı ölmez: bekler ve yeniden dener.

Zaman, verilen saatten okunur (`Clock`); testler yeniden deneme aralıklarını gerçekte beklemeden sınar.
Gizli değerler: buradan loglanan ve günlüğe yazılan her metin sink'in adını ve hata sınıfını taşır; adres ve
imza anahtarı bu modüle hiç ulaşmaz.

Bir Dispatcher tek thread'den kullanılır.
"""
from __future__ import annotations

import logging
import math
import random
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sofascore_scraper.redact import redact_text
from sofascore_scraper.sinks.base import (
    DEFAULT_BATCH_SIZE,
    SINK_DROPPED,
    SYSTEM_SOURCE,
    SYSTEM_STREAM,
    Clock,
    Envelope,
    FatalSinkError,
    Sink,
    SinkError,
    StopToken,
    SystemClock,
)
from sofascore_scraper.store import (
    DEFAULT_PRUNE_MAX_AGE_SECONDS,
    DEFAULT_PRUNE_MAX_ROWS,
    LeaseHeld,
    StoreError,
    StreamEvent,
)

if TYPE_CHECKING:
    from sofascore_scraper.store import Store

logger = logging.getLogger("Sinks")

# state.db'nin okunamadığı ya da yazılamadığı durumlar: Store kilit zaman aşımını StoreBusy'ye çevirir, diğer
# SQLite hataları (WAL'siz kipte okurken kilit, G/Ç hatası) `sqlite3.Error` olarak çıkar (sofascore_scraper/store/state.py)
STORE_ERRORS: Tuple[type, ...] = (StoreError, sqlite3.Error)

LEASE_NAME = "sinks"
RUNTIME_PREFIX = "sink:"  # store.runtime anahtarı: "sink:<ad>" -> {"since_seq": N}; sink'in bilindiğini söyler

READ_LIMIT = 500
MAX_BATCHES_PER_STEP = 20  # bir sink bir turda en çok bu kadar toplu gönderim yapar: diğer sink'ler beklemez
MAX_READS_PER_BATCH = 20  # istenmeyen olayların üzerinden geçerken bir turda en çok bu kadar okuma yapılır
BACKOFF_FIRST_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 300.0
IDLE_WAIT_SECONDS = 0.5  # `run` durdurma isteğine en geç bu kadar sonra bakar
LEASE_RETRY_SECONDS = 5.0
ERROR_RETRY_SECONDS = 5.0  # `run`: beklenmeyen bir hatadan sonra döngü bu kadar bekler ve sürer
DRAIN_TIMEOUT_SECONDS = 10.0

# Günlük saatte bir budanır; saklama süresi `StreamLog.prune`'un varsayılanlarıdır (01-storage.md 9.3: 7 gün,
# 1.000.000 satır; sofascore_scraper/store/streams.py)
PRUNE_INTERVAL_SECONDS = 3600.0

REASON_MAX_AGE = "max_age"
REASON_PRUNED = "pruned"


@dataclass(frozen=True)
class SinkStatus:
    """Bir sink'in dağıtıcıdaki durumu (bu süreçte)."""

    name: str
    cursor: int
    delivered: int  # bu dağıtıcının teslim ettiği olay sayısı
    dropped: int  # yaş sınırı yüzünden bırakılan olay sayısı
    caught_up: bool  # son turda konumdan sonra teslim edilecek olay kalmadı
    disabled: bool
    error: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "cursor": self.cursor, "delivered": self.delivered, "dropped": self.dropped,
                "caught_up": self.caught_up, "disabled": self.disabled, "error": self.error}


@dataclass(frozen=True)
class DrainReport:
    """
    `Dispatcher.drain` sonucu.

    complete     her sink günlüğün sonuna ulaştı
    timed_out    süre doldu; kalan olaylar kilidin bir sonraki sahibine kaldı
    lease_held   `sinks` kilidi başka bir süreçte: hiçbir şey teslim edilmedi, birikenler ona kaldı
    holder_pid   o sürecin kimliği (biliniyorsa)
    """

    sinks: Tuple[SinkStatus, ...] = ()
    complete: bool = False
    timed_out: bool = False
    lease_held: bool = False
    holder_pid: Optional[int] = None

    @property
    def delivered(self) -> int:
        return sum(status.delivered for status in self.sinks)

    def to_dict(self) -> Dict[str, Any]:
        return {"complete": self.complete, "timed_out": self.timed_out, "lease_held": self.lease_held,
                "holder_pid": self.holder_pid, "delivered": self.delivered,
                "sinks": [status.to_dict() for status in self.sinks]}


@dataclass
class _State:
    sink: Sink
    name: str
    uses_cursor: bool
    batch_size: int
    linger: float
    max_age: Optional[float]
    cursor: int = 0
    head: Optional[List[Envelope]] = None  # teslim edilemeyen ve aynen yeniden denenecek toplu gönderim
    head_covers: int = 0  # o gönderim başarılı olunca konumun geleceği sıra numarası
    attempts: int = 0
    retry_at: float = 0.0  # saatin monotonic değeri
    linger_since: Optional[float] = None
    disabled: bool = False
    error: Optional[str] = None
    stored_error: Optional[str] = None  # state.db'ye en son yazılan hata metni
    dirty: bool = False  # konum bellekte ilerledi, henüz yazılmadı
    gap_reported: bool = False
    caught_up: bool = False
    delivered: int = 0
    dropped: int = 0
    known: bool = field(default=False)


class _LogReplaced(Exception):
    """Okuma başka bir günlükten geldi (`stream_id` değişti): tur bırakılır, konumlar yeniden okunur."""

    def __init__(self, stream_id: str) -> None:
        super().__init__(stream_id)
        self.stream_id = stream_id


def _equal_jitter() -> float:
    """Yeniden deneme aralığının çarpanı: [0.5, 1.0]. Aynı anda düşen alıcılara aynı anda yüklenilmez."""
    return 0.5 + random.random() / 2


class Dispatcher:
    """
    store       açık Store
    sinks       teslim edilecek sink'ler; adları ayrı olmalı
    clock       saat (varsayılan sistem saati)
    jitter      yeniden deneme aralığının çarpanını veren işlev (varsayılan [0.5, 1.0] arasında rastgele)
    read_limit  günlükten bir okumada alınan en çok satır
    """

    def __init__(self, store: "Store", sinks: Sequence[Sink], *, clock: Optional[Clock] = None,
                 jitter: Optional[Callable[[], float]] = None, read_limit: int = READ_LIMIT) -> None:
        names = [sink.name for sink in sinks]
        if len(set(names)) != len(names):
            raise ValueError("sink names must be unique")
        self._store = store
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._jitter = jitter if jitter is not None else _equal_jitter
        self._read_limit = max(1, int(read_limit))
        self._states = [
            _State(
                sink=sink,
                name=sink.name,
                uses_cursor=bool(getattr(sink, "uses_cursor", True)),
                batch_size=max(1, int(getattr(sink, "batch_size", DEFAULT_BATCH_SIZE))),
                linger=max(0.0, float(getattr(sink, "linger_seconds", 0.0))),
                max_age=getattr(sink, "max_age_seconds", None),
            )
            for sink in sinks
        ]
        self._started = False
        self._stream_id: Optional[str] = None  # konumların ait olduğu günlük
        self._last_prune: Optional[float] = None

    # --- başlangıç: konumlar ------------------------------------------------------------------

    def register(self) -> None:
        """
        Yeni sink'leri kaydeder: konumları günlüğün o anki sonuna yazılır ve bilindikleri state.db'ye işlenir.
        Kilit gerektirmez ve yinelenebilir; bilinen bir sink'e dokunmaz. Tek seferlik bir komut bunu işe
        başlamadan önce çağırır: böylece kendi ürettiği olaylar çıkışta (`drain`) teslim edilir.
        """
        head = self._store.streams.head()
        for st in self._states:
            if not st.uses_cursor or st.known:
                continue
            key = RUNTIME_PREFIX + st.name
            if self._store.runtime.get(key) is None:
                self._store.streams.set_cursor(st.name, head.last_seq)
                self._store.runtime.set(key, {"since_seq": head.last_seq})
                logger.info("Sink %s: new sink, delivery starts after sequence %d", st.name, head.last_seq)
            st.known = True

    def _start(self) -> None:
        """Kilit alındıktan sonra: yeni sink'leri kaydeder ve her sink'in konumunu okur."""
        self.register()
        head = self._store.streams.head()
        for st in self._states:
            if not st.uses_cursor:
                if not self._started:
                    st.cursor = head.last_seq  # konumsuz sink (stdout): "şimdi"den
                continue
            st.cursor = self._store.streams.cursor(st.name)
            st.head = None  # kilidi bu arada başka bir süreç tutmuş olabilir: toplu gönderim konumdan yeniden kurulur
            st.dirty = False
            self._recover(st)
        self._stream_id = head.stream_id
        self._started = True

    def _restart(self, stream_id: str) -> None:
        """
        Günlük değişti (state.db yeniden yaratılmış): eldeki sıra numaraları başka bir günlüğündür ve yeni
        günlüğünkilerle karşılaştırılamaz. Bellekteki konumlar ve bekleyen toplu gönderimler bırakılır; bir sonraki
        tur baştan başlar: yeni state.db'de kayıtlı olmayan sink yeni günlüğün o anki ucundan, kayıtlı olan
        (yedekten geri yüklenmiş state.db) orada saklı konumundan sürer.
        """
        logger.warning("The event log was replaced (stream id %s, was %s); every sink starts again in the new log",
                       stream_id, self._stream_id)
        for st in self._states:
            st.known = False
            st.head = None
            st.dirty = False
            st.attempts = 0
            st.retry_at = 0.0
            st.linger_since = None
            st.gap_reported = False
            st.caught_up = False
            st.stored_error = None
        self._started = False
        self._stream_id = None

    def _recover(self, st: _State) -> None:
        """
        Dosya sink'i: süreç satırı yazıp konumu kaydedemeden öldüyse konum dosyanın son satırından kurtarılır.
        Yalnızca o satır günlükte aynen duruyorsa: dosya başka bir günlükten (yeniden yaratılmış state.db)
        kalmış olabilir ve o zaman sıra numaraları karşılaştırılamaz.
        """
        last_delivered = getattr(st.sink, "last_delivered", None)
        if not callable(last_delivered):
            return
        try:
            document = last_delivered()
        except Exception as e:
            logger.warning("Sink %s: its last delivered line could not be read (%s)", st.name, type(e).__name__)
            return
        seq = document.get("seq") if isinstance(document, Mapping) else None
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= st.cursor:
            return
        batch = self._store.streams.read(after=seq - 1, limit=1)
        if not batch.events or batch.events[0].seq != seq:
            return
        if Envelope.from_record(batch.events[0]).to_dict() != dict(document):
            return
        logger.info("Sink %s: position recovered from its output (sequence %d, stored %d)", st.name, seq, st.cursor)
        st.cursor = seq
        st.dirty = True
        self._persist(st)

    # --- bir tur ------------------------------------------------------------------------------

    def step(self, *, flush: bool = False, until: Optional[Callable[[], bool]] = None) -> float:
        """
        Her sink için teslim edilebilecek her şeyi teslim eder ve bir sonraki işe kadar geçecek süreyi
        (saniye) döndürür: yeniden deneme ya da toplu gönderim beklemesi; bekleyen iş yoksa `math.inf`.
        flush=True: toplu gönderimin dolması beklenmez (kapanış, `drain`).
        until: durdurma isteği ya da süre sınırı. Her sink'ten önce ve her teslimden sonra sorulur; True
        dönerse tur yarıda kesilir ve 0 döner (kalan iş bir sonraki tura kalır).
        """
        if not self._started:
            self._start()
        delay = math.inf
        for st in self._states:
            if st.disabled:
                continue
            if until is not None and until():
                return 0.0
            try:
                delay = min(delay, self._advance(st, flush, until))
            except _LogReplaced as e:
                self._restart(e.stream_id)
                return 0.0
            except STORE_ERRORS as e:
                # state.db meşgul ya da okunamıyor: sink bu turda bekler, dağıtıcı ölmez
                logger.warning("Sink %s: the event log could not be read or written (%s); trying again",
                               st.name, type(e).__name__)
                delay = min(delay, BACKOFF_FIRST_SECONDS)
        return delay

    def _advance(self, st: _State, flush: bool, until: Optional[Callable[[], bool]] = None) -> float:
        for _round in range(MAX_BATCHES_PER_STEP):
            if _round and until is not None and until():
                self._persist(st)
                return 0.0
            now = self._clock.monotonic()
            if now < st.retry_at:
                self._persist(st)
                return st.retry_at - now
            if st.head is not None:
                batch, covers = st.head, st.head_covers
            else:
                batch, covers, wait = self._next_batch(st, flush, now)
                if not batch:
                    self._persist(st)
                    return wait
            try:
                st.sink.deliver(batch)
            except FatalSinkError as e:
                self._disable(st, str(e))
                return math.inf
            except Exception as e:
                return self._failed(st, batch, covers, e)
            st.delivered += len(batch)
            st.cursor = max(st.cursor, covers)
            st.head = None
            st.attempts = 0
            st.retry_at = 0.0
            st.linger_since = None
            if st.error is not None:
                logger.info("Sink %s: delivery works again", st.name)
            st.error = None
            st.dirty = True  # yazılamazsa bir sonraki turda yeniden denenir
            self._persist(st)
        return 0.0  # daha teslim edilecek olay var: sıra diğer sink'lere geçer, sonraki tur hemen sürer

    def _next_batch(self, st: _State, flush: bool, now: float) -> Tuple[List[Envelope], int, float]:
        """
        Konumdan sonraki, sink'in kabul ettiği ilk toplu gönderim: (olaylar, başarıda konumun geleceği numara,
        bekleme). Olay listesi boşsa teslim edilecek bir şey yoktur: bekleme `inf` (günlüğün sonu) ya da toplu
        gönderimin dolması için kalan süredir; 0 ise okunacak daha çok satır vardır (sıra diğer sink'lere geçer).
        """
        for _read in range(MAX_READS_PER_BATCH):
            read = self._store.streams.read(after=st.cursor, limit=self._read_limit)
            if self._stream_id is not None and read.stream_id != self._stream_id:
                raise _LogReplaced(read.stream_id)
            self._note_gap(st, read.gap, read.events[0].seq - 1 if read.events else read.last_seq)
            exhausted = len(read.events) < self._read_limit
            accepted = [env for env in (Envelope.from_record(record, read.stream_id) for record in read.events)
                        if st.sink.accepts(env)]
            if not accepted:
                if read.last_seq != st.cursor:
                    st.cursor = read.last_seq
                    st.dirty = True
                if exhausted:
                    st.caught_up = True
                    st.linger_since = None
                    return [], st.cursor, math.inf
                continue
            st.caught_up = False
            batch = accepted[:st.batch_size]
            full = len(batch) == st.batch_size
            if not full and exhausted and st.linger > 0 and not flush:
                if st.linger_since is None:
                    st.linger_since = now
                remaining = st.linger - (now - st.linger_since)
                if remaining > 0:
                    return [], st.cursor, remaining
            # Toplu gönderim okunan kabul edilmiş olayların tümüyse konum okumanın sonuna gelir: aradaki ve
            # sondaki, bu sink'in istemediği olaylar da geçilmiş olur
            covers = read.last_seq if len(accepted) <= st.batch_size else batch[-1].seq
            return batch, covers, 0.0
        st.caught_up = False
        return [], st.cursor, 0.0

    # --- hata, yeniden deneme, bırakma --------------------------------------------------------

    def _failed(self, st: _State, batch: List[Envelope], covers: int, exc: Exception) -> float:
        if isinstance(exc, SinkError):
            text = str(exc) or type(exc).__name__
            retry_after = getattr(exc, "retry_after", None)
        else:  # sink'in beklenmeyen hatası: dağıtıcıyı durdurmaz, geçici hata gibi yeniden denenir
            text = redact_text(f"internal error ({type(exc).__name__}: {exc})")[:200]
            retry_after = None
        st.attempts += 1
        delay = min(BACKOFF_MAX_SECONDS, BACKOFF_FIRST_SECONDS * (2 ** min(st.attempts - 1, 20))) * self._jitter()
        if retry_after is not None:
            delay = max(delay, min(float(retry_after), BACKOFF_MAX_SECONDS))
        st.retry_at = self._clock.monotonic() + delay
        st.head, st.head_covers = batch, covers
        st.linger_since = None
        st.caught_up = False
        log = logger.warning if st.attempts == 1 or st.error != text else logger.debug
        log("Sink %s: delivery of sequences %d-%d failed (%s); attempt %d, next in %.0f s",
            st.name, batch[0].seq, batch[-1].seq, text, st.attempts, delay)
        st.error = text
        if st.max_age is not None and self._clock.time() - batch[0].ts > st.max_age:
            self._drop_expired(st)
        self._persist(st)
        return delay

    def _drop_expired(self, st: _State) -> None:
        """
        Baştaki toplu gönderim yaş sınırını aştı: sink'in kabul ettiği, sınırdan eski olayların hepsi bir kerede
        bırakılır (her biri için ayrı bir başarısız istek atılmaz), aralık kaydedilir ve teslim, bir sonraki
        yeniden deneme anında, kalan olaylarla sürer.
        """
        assert st.max_age is not None
        now = self._clock.time()
        first: Optional[int] = None
        last = st.cursor
        count = 0
        own_only = True  # yalnızca bu sink'in kendi "bırakıldı" kayıtları bırakılıyorsa yenisi yazılmaz
        position = st.cursor
        fresh_found = False
        while not fresh_found:
            read = self._store.streams.read(after=position, limit=self._read_limit)
            for record in read.events:
                env = Envelope.from_record(record, read.stream_id)
                if not st.sink.accepts(env):
                    continue
                if now - env.ts <= st.max_age:
                    fresh_found = True
                    break
                first = env.seq if first is None else first
                last = env.seq
                count += 1
                if not (env.type == SINK_DROPPED and env.data.get("sink") == st.name):
                    own_only = False
            if len(read.events) < self._read_limit:
                break
            position = read.last_seq
        if not count or first is None:
            return
        st.cursor = max(st.cursor, last)
        st.head = None  # kalan olaylar konumdan yeniden okunur
        st.dropped += count
        st.dirty = True
        logger.warning("Sink %s: dropped %d undelivered event(s) older than %.0f s (sequences %d-%d)",
                       st.name, count, st.max_age, first, last)
        if not own_only:
            self._record_drop(st, REASON_MAX_AGE, first, last, count, max_age_seconds=st.max_age)

    def _note_gap(self, st: _State, gap: bool, last_missing: int) -> None:
        """Konumdan sonraki olayların bir kısmı okunmadan budanmış: bir kez loglanır ve kaydedilir."""
        if not gap:
            st.gap_reported = False
            return
        if st.gap_reported:
            return
        st.gap_reported = True
        logger.warning("Sink %s: events after sequence %d were pruned before they were delivered",
                       st.name, st.cursor)
        self._record_drop(st, REASON_PRUNED, st.cursor + 1, max(last_missing, st.cursor + 1), None)

    def _record_drop(self, st: _State, reason: str, first: int, last: int, count: Optional[int],
                     **extra: Any) -> None:
        data = {"sink": st.name, "reason": reason, "first_seq": first, "last_seq": last, "count": count, **extra}
        try:
            self._store.streams.append(SYSTEM_STREAM, [
                StreamEvent(type=SINK_DROPPED, data=data, source=SYSTEM_SOURCE, ts=self._clock.time()),
            ])
        except STORE_ERRORS as e:
            logger.warning("Sink %s: the dropped range could not be recorded (%s)", st.name, type(e).__name__)

    def _disable(self, st: _State, text: str) -> None:
        st.disabled = True
        st.caught_up = False
        st.error = f"disabled until restart: {text}"
        logger.error("Sink %s is disabled until restart: %s", st.name, text)
        self._persist(st)

    def _persist(self, st: _State) -> None:
        """
        Konumu ve son hatayı state.db'ye yazar; değişmediyse yazmaz. Yazılamazsa teslim sürer ve yazma bir
        sonraki turda yeniden denenir (`dirty` kalır); süreç ondan önce ölürse aynı olaylar yeniden başlamada
        bir kez daha teslim edilir (en az bir kez).
        """
        if not st.uses_cursor:
            st.dirty = False
            return
        if not (st.dirty or st.error != st.stored_error):
            return
        try:
            self._store.streams.set_cursor(st.name, st.cursor, error=st.error)
        except STORE_ERRORS as e:
            logger.warning("Sink %s: its position could not be stored (%s)", st.name, type(e).__name__)
            return
        st.dirty = False
        st.stored_error = st.error

    # --- uzun çalışan süreç -------------------------------------------------------------------

    def run(self, stop: StopToken, *, flush_timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
        """
        `stop` verilene kadar dağıtır. `sinks` kilidi başka bir süreçteyse bekler ve boşalınca devralır
        (`serve` ile `watch` aynı veri dizininde birlikte çalışabilir; biri dağıtır). Dururken birikmiş
        olayları en çok `flush_timeout` saniye boyunca teslim etmeyi dener, sonra kilidi bırakır.
        """
        lease = None
        waiting_logged = False
        try:
            while not stop.is_set():
                if lease is None:
                    try:
                        lease = self._store.lease(LEASE_NAME, purpose="dispatcher")
                    except LeaseHeld as held:
                        if not waiting_logged:
                            logger.info("Process %s already dispatches on this data directory; "
                                        "waiting to take over", held.pid if held.pid is not None else "?")
                            waiting_logged = True
                        stop.wait(LEASE_RETRY_SECONDS)
                        continue
                    try:
                        self._start()
                    except STORE_ERRORS as e:  # state.db meşgul: kilit bırakılır ve biraz sonra yeniden denenir
                        logger.warning("The sink positions could not be read (%s); trying again",
                                       type(e).__name__)
                        lease.release()
                        lease = None
                        stop.wait(LEASE_RETRY_SECONDS)
                        continue
                    logger.info("Dispatching to %s", ", ".join(st.name for st in self._states) or "no sink")
                try:
                    # Günlüğün ucu turdan önce okunur: tur sırasında eklenen olay beklemeyi hemen bitirir, yeniden
                    # deneme bekleyen (okumayan) bir sink ise döngüyü boşa döndürmez
                    seen = self._store.streams.head().last_seq
                    delay = self.step(until=stop.is_set)
                    self._prune_if_due()
                    if stop.is_set():
                        break
                    self._store.streams.wait(after=seen, timeout=max(0.0, min(delay, IDLE_WAIT_SECONDS)))
                except STORE_ERRORS as e:
                    logger.warning("The event log could not be read (%s); trying again", type(e).__name__)
                    stop.wait(BACKOFF_FIRST_SECONDS)
                except Exception as e:
                    # Uzun çalışan servis beklenmeyen bir hatayla sessizce durmaz: yazar, bekler, sürer. İletide
                    # yalnızca hatanın sınıfı vardır (sink'lerin teslim hataları buraya gelmez: `_advance` tutar)
                    logger.error("The dispatcher failed unexpectedly (%s); trying again in %.0f s",
                                 type(e).__name__, ERROR_RETRY_SECONDS, exc_info=True)
                    stop.wait(ERROR_RETRY_SECONDS)
            if lease is not None and flush_timeout > 0:
                try:
                    self._drain(flush_timeout)
                except STORE_ERRORS as e:
                    logger.warning("The backlog could not be delivered while stopping (%s)", type(e).__name__)
        finally:
            if lease is not None:
                lease.release()

    def follow(self, stop: StopToken) -> None:
        """
        `stop` verilene kadar, kilit almadan teslim eder: yalnızca konum tutmayan sink'ler (stdout) için.
        `watch --stdout` gibi, olayları kendi çıktısına yazan bir süreç bunu `sinks` kilidinin sahibinden
        bağımsız çalıştırır; günlüğü budamaz, state.db'ye yazmaz. Başlangıç "şimdi"dir.
        """
        if any(st.uses_cursor for st in self._states):
            raise ValueError("follow() is for sinks without a cursor; sinks with a cursor need run()")
        while not stop.is_set():
            try:
                seen = self._store.streams.head().last_seq
                delay = self.step(until=stop.is_set)
                if stop.is_set():
                    break
                self._store.streams.wait(after=seen, timeout=max(0.0, min(delay, IDLE_WAIT_SECONDS)))
            except STORE_ERRORS as e:
                logger.warning("The event log could not be read (%s); trying again", type(e).__name__)
                stop.wait(BACKOFF_FIRST_SECONDS)
        self.step(flush=True)

    def _prune_if_due(self) -> None:
        now = self._clock.monotonic()
        if self._last_prune is not None and now - self._last_prune < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        try:
            removed = self._store.streams.prune(max_age_s=DEFAULT_PRUNE_MAX_AGE_SECONDS,
                                                max_rows=DEFAULT_PRUNE_MAX_ROWS)
        except STORE_ERRORS as e:
            logger.warning("The event log could not be pruned (%s)", type(e).__name__)
            return
        if removed:
            logger.info("Pruned %d old event(s) from the event log", removed)

    # --- tek seferlik -------------------------------------------------------------------------

    def drain(self, timeout: float = DRAIN_TIMEOUT_SECONDS) -> DrainReport:
        """
        Tek seferlik komutlar için, çıkıştan önce: `sinks` kilidini dener, birikmiş olayları en çok `timeout`
        saniye boyunca teslim eder ve kilidi bırakır. Kilit başka bir süreçteyse hiçbir şey yapmaz: birikenler
        o sürece kalır. Süre her teslimden sonra denetlenir: süren tek bir teslim (webhook: isteğin zaman aşımı,
        varsayılan 10 sn) kadar aşılabilir, ondan sonra yeni bir teslime başlanmaz. `timeout` 0 ise hiçbir şey
        teslim edilmez.
        """
        try:
            lease = self._store.lease(LEASE_NAME, purpose="drain")
        except LeaseHeld as held:
            return DrainReport(lease_held=True, holder_pid=held.pid)
        try:
            self._start()
            complete = self._drain(timeout)
            return DrainReport(sinks=self.status(), complete=complete, timed_out=not complete and self._pending())
        finally:
            lease.release()

    def _drain(self, timeout: float) -> bool:
        """Her sink günlüğün sonuna ulaşana ya da süre dolana kadar teslim eder; hepsi ulaştıysa True."""
        deadline = self._clock.monotonic() + max(0.0, float(timeout))

        def expired() -> bool:
            return self._clock.monotonic() >= deadline

        while True:
            delay = self.step(flush=True, until=expired)
            if math.isinf(delay):
                return all(st.caught_up for st in self._states)
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0 or delay > remaining:
                return False
            self._clock.sleep(delay)

    def _pending(self) -> bool:
        return any(not st.caught_up and not st.disabled for st in self._states)

    # --- durum ve kapanış ---------------------------------------------------------------------

    def status(self) -> Tuple[SinkStatus, ...]:
        return tuple(
            SinkStatus(name=st.name, cursor=st.cursor, delivered=st.delivered, dropped=st.dropped,
                       caught_up=st.caught_up, disabled=st.disabled, error=st.error)
            for st in self._states
        )

    def close(self) -> None:
        """Sink'leri kapatır (açık dosyalar). Konumlar zaten yazılmıştır."""
        for st in self._states:
            try:
                st.sink.close()
            except Exception as e:
                logger.debug("Sink %s: close failed (%s)", st.name, type(e).__name__)


__all__ = [
    "BACKOFF_FIRST_SECONDS",
    "BACKOFF_MAX_SECONDS",
    "DRAIN_TIMEOUT_SECONDS",
    "LEASE_NAME",
    "PRUNE_INTERVAL_SECONDS",
    "READ_LIMIT",
    "REASON_MAX_AGE",
    "REASON_PRUNED",
    "RUNTIME_PREFIX",
    "STORE_ERRORS",
    "DrainReport",
    "Dispatcher",
    "SinkStatus",
]
