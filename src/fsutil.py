"""
Eski içe aktarma yolu: atomik yazma ve config dosyalarını koruyan süreçler arası kilit.

Gerçek kod src/store/files.py'ye taşındı (docs/design/01-storage.md, bölüm 2.2); bu modül 2.x
çağıranları ve testleri için adları yeniden dışa açar ve Store dışındaki kod Store'a geçince silinir.
Davranış aynıdır; tek fark Windows'ta yerine koymanın, hedef başka bir süreçte açıkken kısa
aralıklarla yeniden denenmesidir.
"""

import contextlib
import os  # noqa: F401  (testler os.replace'i `fsutil.os` üzerinden yamalar)
from typing import Iterator

from src.store import files as _files
from src.store.files import atomic_write_json, atomic_write_text, fcntl

__all__ = ["atomic_write_json", "atomic_write_text", "fcntl", "file_lock"]


@contextlib.contextmanager
def file_lock(path: str) -> Iterator[None]:
    """
    Süreçler arası danışma kilidi (CLI ve web aynı config dosyasını düzenlerken).
    Kilit, hedefin yanındaki `<path>.lock` dosyasında tutulur; Windows'ta kilitlenmez.
    `fcntl` bu modülün adı üzerinden okunur: testler kilitsiz dalı `fsutil.fcntl = None` ile dener.
    """
    if fcntl is None:
        yield
        return
    with _files.file_lock(path):
        yield
