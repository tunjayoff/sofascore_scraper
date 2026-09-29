"""
Atomik dosya yazma: önce aynı dizinde benzersiz bir geçici dosyaya yazılır, sonra
os.replace ile yerine konur. Yarıda kesilen bir yazma hedef dosyayı bozuk bırakmaz ve
aynı dosyaya aynı anda yazan iki süreç birbirinin geçici dosyasını ezmez.
"""

import contextlib
import json
import os
import tempfile
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def atomic_write_json(path: str, data: Any, **dump_kwargs: Any) -> None:
    dump_kwargs.setdefault("ensure_ascii", False)
    dump_kwargs.setdefault("indent", 2)
    atomic_write_text(path, json.dumps(data, **dump_kwargs))


@contextlib.contextmanager
def file_lock(path: str) -> Iterator[None]:
    """
    Süreçler arası danışma kilidi (CLI ve web aynı config dosyasını düzenlerken).
    Kilit, hedefin yanındaki `<path>.lock` dosyasında tutulur; Windows'ta kilitlenmez.
    """
    if fcntl is None:
        yield
        return
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    with open(lock_path, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
