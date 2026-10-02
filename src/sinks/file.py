"""
Dosya sink'i: zarfları bir dosyaya NDJSON olarak ekler (docs/design/02-services.md bölüm 5.2).

    [[sink]]
    name = "feed"
    type = "file"
    path = "out/live.ndjson"
    events = ["live.*"]
    rotate_daily = true        # gün (UTC) değişince dosyayı çevir
    rotate_size = "50MB"       # dosya bu boyutu aşacaksa çevir (bayt sayısı ya da KB / MB / GB)
    keep = 14                  # en yeni bu kadar çevrilmiş dosya kalır; 0 (varsayılan): hiçbiri silinmez

  * Yazılan dosya her zaman `path`'in kendisidir. Çevirme onu `<ad>.<son yazma anı><uzantı>` olarak yeniden
    adlandırır (`live.20261001T235958Z.ndjson`; aynı saniyedeki ikinci çevirme `...Z-01` ekini alır); ekler
    sıralandığında zaman sırası çıkar.
  * Satır sonu her platformda LF'dir; satırlar ASCII'dir. Her toplu gönderimden sonra dosya diske
    eşitlenir (fsync): dağıtıcı konumu ancak ondan sonra ilerletir.
  * Yarım kalmış son satır (yazarken ölen süreç) bir sonraki açılışta kesilir; o olay konum ilerlemediği için
    yeniden yazılır.
  * Konumun kurtarılması: süreç satırı yazıp konumu kaydedemeden öldüyse dağıtıcı en yeni dosyanın son
    satırına (`last_delivered`) bakar ve o olayı günlükte aynen bulursa oradan sürer; böylece yeniden
    başlama satırları ikinci kez yazmaz.

Bu modül Store sınırının bilinen ayrığıdır (01-storage.md 2.4): kendi çıktı yoluna doğrudan dokunur. Yol
veri dizininin içinde olamaz; bunu sink'i kuran denetler (src/sinks/__init__.py).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import os
import re
import time
from typing import IO, Any, Dict, List, Optional, Sequence

from src.sinks.base import BaseSink, Clock, Envelope, EventFilter, RetryableSinkError, SystemClock

logger = logging.getLogger("Sinks")

_TAIL_CHUNK = 64 * 1024
_SUFFIX_FORMAT = "%Y%m%dT%H%M%SZ"
_SUFFIX_RE = r"\d{8}T\d{6}Z(?:-\d{2,})?"


def _utc_day(epoch: float) -> dt.date:
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).date()


class FileSink(BaseSink):
    """
    path          yazılacak dosya (mutlak yol); üst dizini yoksa oluşturulur
    rotate_bytes  0: boyuta göre çevirme yok
    rotate_daily  gün (UTC) değişince çevir
    keep          çevrilmiş dosyalardan en yeni kaç tanesi kalır (0: hepsi)
    fsync         her toplu gönderimden sonra diske eşitle
    """

    def __init__(self, name: str, path: str, events: Optional[EventFilter] = None, *, rotate_bytes: int = 0,
                 rotate_daily: bool = False, keep: int = 0, fsync: bool = True,
                 clock: Optional[Clock] = None) -> None:
        super().__init__(name, events)
        if not path:
            raise ValueError("a file sink needs a path")
        if rotate_bytes < 0 or keep < 0:
            raise ValueError("rotate_bytes and keep cannot be negative")
        self.path = os.path.abspath(path)
        self.rotate_bytes = int(rotate_bytes)
        self.rotate_daily = bool(rotate_daily)
        self.keep = int(keep)
        self._fsync = bool(fsync)
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._file: Optional[IO[bytes]] = None
        self._size = 0
        self._day: Optional[dt.date] = None  # açık dosyaya en son yazılan gün
        self._last_write: Optional[float] = None
        self._written_seq = 0  # bu süreçte yazılan son sıra numarası: yeniden denenen gönderim satırı yinelemez
        self._written_log = ""  # o numaranın ait olduğu günlük (`stream_id`)
        directory, filename = os.path.split(self.path)
        self._directory = directory
        self._stem, self._ext = os.path.splitext(filename)
        self._rotated_re = re.compile(
            "^" + re.escape(self._stem) + r"\.(" + _SUFFIX_RE + ")" + re.escape(self._ext) + "$")

    # --- yazma --------------------------------------------------------------------------------

    def deliver(self, batch: Sequence[Envelope]) -> None:
        if batch and batch[0].stream_id != self._written_log:
            # Başka bir günlük (state.db yeniden yaratılmış): sıra numaraları baştan başlar, eski numara geçersiz
            self._written_seq, self._written_log = 0, batch[0].stream_id
        pending = [env for env in batch if env.seq > self._written_seq]
        if not pending:
            return
        try:
            self._write(pending)
        except OSError as e:
            self._close_quietly()
            reason = e.strerror or type(e).__name__
            raise RetryableSinkError(f"cannot write the file ({reason})") from e

    def _write(self, batch: Sequence[Envelope]) -> None:
        self._open()
        now = self._clock.time()
        today = _utc_day(now)
        if self.rotate_daily and self._size > 0 and self._day is not None and self._day != today:
            self._rotate()
        buffer: List[bytes] = []
        buffered = 0
        last_seq = self._written_seq
        for env in batch:
            line = (env.to_json() + "\n").encode("ascii")
            if self.rotate_bytes and self._size + buffered > 0 and self._size + buffered + len(line) > self.rotate_bytes:
                self._flush(buffer, last_seq, now, today)
                buffer, buffered = [], 0
                self._rotate()
            buffer.append(line)
            buffered += len(line)
            last_seq = env.seq
        self._flush(buffer, last_seq, now, today)

    def _flush(self, buffer: List[bytes], last_seq: int, now: float, today: dt.date) -> None:
        if not buffer:
            return
        handle = self._open()
        data = b"".join(buffer)
        handle.write(data)
        handle.flush()
        if self._fsync:
            os.fsync(handle.fileno())
        self._size += len(data)
        self._day = today
        self._last_write = now
        self._written_seq = last_seq

    def _open(self) -> IO[bytes]:
        if self._file is not None:
            return self._file
        if self._directory:
            os.makedirs(self._directory, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            size = handle.seek(0, os.SEEK_END)
            if size:
                kept = self._complete_length(handle, size)
                if kept != size:
                    logger.warning("Sink %s: removed an incomplete last line from its file (%d bytes)",
                                   self.name, size - kept)
                    handle.truncate(kept)
                    size = kept
                modified = os.fstat(handle.fileno()).st_mtime
                self._day = _utc_day(modified)
                self._last_write = modified
            else:
                self._day = None
                self._last_write = None
        except BaseException:
            handle.close()
            raise
        self._file = handle
        self._size = size
        return handle

    @staticmethod
    def _complete_length(handle: IO[bytes], size: int) -> int:
        """Dosyanın, son satır sonuna kadar olan uzunluğu (yarım kalmış son satır sayılmaz)."""
        position = size
        while position > 0:
            start = max(0, position - _TAIL_CHUNK)
            handle.seek(start)
            chunk = handle.read(position - start)
            cut = chunk.rfind(b"\n")
            if cut >= 0:
                return start + cut + 1
            position = start
        return 0

    # --- çevirme ------------------------------------------------------------------------------

    def _rotated_names(self) -> List[str]:
        """Çevrilmiş dosyaların adları, eskiden yeniye."""
        try:
            names = os.listdir(self._directory or ".")
        except OSError:
            return []
        # Ek'e göre sıralanır, dosya adına göre değil: "…Z-01.ndjson" adı "…Z.ndjson"dan önce sıralanırdı
        found = [(match.group(1), name) for name in names for match in [self._rotated_re.match(name)] if match]
        return [name for _suffix, name in sorted(found)]

    def _rotate(self) -> None:
        """Açık dosyayı son yazma anının adıyla kenara koyar; bir sonraki yazma yeni bir dosya açar."""
        self._close_quietly()
        moment = self._last_write if self._last_write is not None else self._clock.time()
        suffix = time.strftime(_SUFFIX_FORMAT, time.gmtime(moment))
        taken = set(self._rotated_names())
        name = f"{self._stem}.{suffix}{self._ext}"
        counter = 0
        while name in taken:
            counter += 1
            name = f"{self._stem}.{suffix}-{counter:02d}{self._ext}"
        os.replace(self.path, os.path.join(self._directory, name))
        self._size = 0
        self._day = None
        self._last_write = None
        if self.keep:
            for old in (self._rotated_names())[:-self.keep]:
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(self._directory, old))

    # --- konumun kurtarılması -----------------------------------------------------------------

    def last_delivered(self) -> Optional[Dict[str, Any]]:
        """
        En yeni dosyanın son tam satırı (ayrıştırılmış zarf) ya da None: dosya yok, boş ya da satır okunamıyor.
        Yazılan dosya boşsa en yeni çevrilmiş dosyaya bakılır.
        """
        candidates = [self.path] + [os.path.join(self._directory, name) for name in reversed(self._rotated_names())]
        for candidate in candidates[:2]:
            line = self._last_line(candidate)
            if line is None:
                continue
            try:
                document = json.loads(line)
            except ValueError:
                return None
            return document if isinstance(document, dict) else None
        return None

    def _last_line(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                end = self._complete_length(handle, size)
                if end == 0:
                    return None
                # Son tam satırın başı: sondaki satır sonundan önceki satır sonu
                start = self._complete_length(handle, end - 1)
                handle.seek(start)
                return handle.read(end - 1 - start)
        except OSError:
            return None

    # --- kapanış ------------------------------------------------------------------------------

    def _close_quietly(self) -> None:
        handle, self._file = self._file, None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()

    def close(self) -> None:
        self._close_quietly()


__all__ = ["FileSink"]
