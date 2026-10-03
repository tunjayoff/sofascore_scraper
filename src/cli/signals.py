"""
CLI'nin sinyalleri (docs/design/02-services.md bölüm 4.6; plan maddesi P19).

İki kural vardır:

  * Tek seferlik komut (sync, fetch, refresh, data ...): SIGINT (Ctrl+C) ya da SIGTERM işi iptal eder. İş
    sürüyorsa iptal istenir (`CancelRequest`): istekler bir sonraki iptal denetiminde durur, yazıcı o anki maçı
    bütün olarak yazar ya da hiç yazmaz, iş satırı `cancelled` olur, kilit bırakılır ve sonuç (`--json` ile
    zarf) yine yazılır. Çıkış kodu 130 (SIGINT) ya da 143 (SIGTERM) olur. İkinci sinyal süreci hemen bitirir
    (aynı kodla); yarım kalan satırı sonradan veri dizinini açan ilk süreç `interrupted` yapar.
  * İş dışındaki her şey (okuma komutları, ayarların yüklenmesi): SIGINT Python'un KeyboardInterrupt'ıdır;
    SIGTERM de aynı yoldan gider (`terminate_as_interrupt`): `Terminated` (bir KeyboardInterrupt) fırlar,
    komutun temizliği çalışır, çıkış kodu 143'tür. Akış komutları (`events --follow`, `jobs tail --follow`)
    ve canlı servis (`watch`) durdurulmayı başarı sayar ve 0 ile çıkar.

Windows: Ctrl+C SIGINT'tir; Ctrl+Break (SIGBREAK) SIGINT gibi işlenir. SIGHUP yoktur; yeniden yüklemeyi
uzun çalışan servisler yapar (bu modül SIGHUP'a dokunmaz).

Sinyal işleyicileri yalnızca ana thread'de kurulabilir. Başka bir thread'den çağrılan komut (testler, gömülü
kullanım) sinyallere dokunmaz; iptal yine iş satırından gelebilir.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
from types import FrameType
from typing import Any, Callable, Dict, Iterator, List, Optional
import contextlib

SIGINT = int(signal.SIGINT)
SIGTERM = int(signal.SIGTERM)
# Windows'ta Ctrl+Break; SIGINT gibi işlenir
SIGBREAK: Optional[int] = int(signal.SIGBREAK) if hasattr(signal, "SIGBREAK") else None  # type: ignore[attr-defined]


def exit_code_of(signal_number: int) -> int:
    """Sinyalle iptal edilen komutun çıkış kodu: 128 + sinyal (SIGINT 130, SIGTERM 143)."""
    return 128 + int(signal_number)


def _normal(signum: int) -> int:
    """Ctrl+Break, Ctrl+C sayılır."""
    return SIGINT if SIGBREAK is not None and signum == SIGBREAK else int(signum)


def _handled() -> List[int]:
    handled = [SIGINT, SIGTERM]
    if SIGBREAK is not None:
        handled.append(SIGBREAK)
    return handled


def _main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


class Terminated(KeyboardInterrupt):
    """SIGTERM, iş dışındaki bir anda geldi: Ctrl+C gibi temizlenir, çıkış kodu 143."""

    signal_number = SIGTERM


def _raise_terminated(signum: int, frame: Optional[FrameType]) -> None:
    raise Terminated()


@contextlib.contextmanager
def terminate_as_interrupt() -> Iterator[None]:
    """Blok boyunca SIGTERM, KeyboardInterrupt gibi (`Terminated`) fırlar. Ana thread dışında hiçbir şey yapmaz."""
    if not _main_thread():
        yield
        return
    try:
        previous = signal.signal(signal.SIGTERM, _raise_terminated)
    except (ValueError, OSError):  # gömülü yorumlayıcı ya da sinyali olmayan platform
        yield
        return
    try:
        yield
    finally:
        try:
            signal.signal(signal.SIGTERM, previous)
        except (ValueError, OSError):
            pass


class CancelRequest:
    """
    Bir işin sinyalle gelen iptal isteği. `install()` ile SIGINT ve SIGTERM'ü (Windows'ta SIGBREAK'i de) alır:

      * ilk sinyal yalnızca bayrağı kaldırır (`requested`, `signal_number`) ve `on_first`'ü çağırır; işin iptal
        denetimleri (`cancelled()`) bunu görür;
      * ikinci sinyal `exit(128 + ilk sinyal)` çağırır: süreç beklemeden biter (varsayılan `os._exit`; tamponlar
        boşaltılır ama temizlik çalışmaz).

    İşleyici süreci kilitlemez ve depoya yazmaz: sinyal ana thread'in herhangi bir anında (bir SQLite işleminin
    ortasında da) gelebilir. İptali iş satırına yazmak işin kendi akışındadır.
    """

    def __init__(self, *, on_first: Optional[Callable[[int], None]] = None,
                 exit: Optional[Callable[[int], Any]] = None) -> None:
        self.signal_number: Optional[int] = None
        self._on_first = on_first
        self._exit = exit if exit is not None else _hard_exit
        self._previous: Dict[int, Any] = {}

    @property
    def requested(self) -> bool:
        return self.signal_number is not None

    @property
    def exit_code(self) -> Optional[int]:
        return exit_code_of(self.signal_number) if self.signal_number is not None else None

    def cancelled(self) -> bool:
        return self.signal_number is not None

    def handle(self, signum: int, frame: Optional[FrameType] = None) -> None:
        """Sinyal işleyicisi (testler doğrudan çağırabilir)."""
        if self.signal_number is not None:
            self._exit(exit_code_of(self.signal_number))
            return
        self.signal_number = _normal(signum)
        if self._on_first is not None:
            try:
                self._on_first(self.signal_number)
            except Exception:  # bilgi satırı yazılamadı (ör. stderr kapalı): iptal yine geçerlidir
                pass

    def install(self) -> bool:
        """İşleyicileri kurar; ana thread dışında ya da kurulamıyorsa False (sinyaller dokunulmadan kalır)."""
        if not _main_thread():
            return False
        for signum in _handled():
            try:
                self._previous[signum] = signal.signal(signum, self.handle)
            except (ValueError, OSError):
                continue
        return bool(self._previous)

    def restore(self) -> None:
        for signum, previous in self._previous.items():
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError, TypeError):
                pass
        self._previous.clear()

    def __enter__(self) -> "CancelRequest":
        self.install()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.restore()


def _hard_exit(code: int) -> None:
    """İkinci sinyal: beklemeden çık. Yazılmış ama boşaltılmamış çıktı kaybolmasın."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


__all__ = [
    "SIGINT",
    "SIGTERM",
    "CancelRequest",
    "Terminated",
    "exit_code_of",
    "terminate_as_interrupt",
]
