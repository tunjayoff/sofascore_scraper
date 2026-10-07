"""
stdout sink'i: satır başına bir zarf (NDJSON). `ssc events` ve `watch --stdout` bunu kullanır.

Konum tutmaz (`uses_cursor = False`): her başlangıç "şimdi"den ya da çağıranın verdiği sıra numarasından
sürer; kaçırılan olaylar `ssc events --after SEQ` ile günlükten okunur.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional, Sequence

from sofascore_scraper.sinks.base import BaseSink, Envelope, EventFilter, FatalSinkError

LineWriter = Callable[[str], None]


class StdoutSink(BaseSink):
    """
    write: bir satırı (satır sonu olmadan) yazan işlev. Verilmezse `sys.stdout`'a yazılır ve her toplu
    gönderimden sonra akış boşaltılır; okuyan süreç kapandıysa sink kapatılır (FatalSinkError). Kendi
    yazıcısını veren çağıran (CLI) hatalarını da kendisi ele alır: yazıcının hatası olduğu gibi çıkar.
    """

    uses_cursor = False

    def __init__(self, name: str = "stdout", events: Optional[EventFilter] = None, *,
                 write: Optional[LineWriter] = None) -> None:
        super().__init__(name, events)
        self._write = write

    def deliver(self, batch: Sequence[Envelope]) -> None:
        if self._write is not None:
            for env in batch:
                self._write(env.to_json())
            return
        try:
            stream = sys.stdout
            stream.write("".join(env.to_json() + "\n" for env in batch))
            stream.flush()
        except (OSError, ValueError) as e:  # kapalı boru ya da kapatılmış akış: yazacak yer kalmadı
            raise FatalSinkError(f"stdout is closed ({type(e).__name__})") from e


__all__ = ["LineWriter", "StdoutSink"]
