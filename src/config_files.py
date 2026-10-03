"""
Veri dizini dışındaki yapılandırma dosyalarının yazımı: atomik yazma ve süreçler arası kilit.

Kullananlar: leagues.txt (src/config_manager.py), league_sports.json (src/web/league_sports.py),
overrides.json (src/config/overrides.py) ve log dosyasının çevrilmesi (src/logger.py, yalnızca kilit).
DATA_DIR'e yazmaz: veri dizinine yalnızca Store dokunur (docs/design/01-storage.md bölüm 2.1); Store'un kendi
dosya ilkelleri src/store/files.py'dedir.

Atomik yazma: içerik önce aynı dizinde `.<ad>.<rastgele>.tmp` adlı geçici dosyaya yazılır, sonra os.replace
ile yerine konur. Okuyan taraf ya eski ya yeni dosyayı görür, yarım dosyayı görmez; aynı dosyaya aynı anda
yazan iki süreç birbirinin geçici dosyasını ezmez. Hata olduğu gibi (OSError) çıkar, hedef eski haliyle
kalır; hiçbir şey fsync edilmez. Dosya izni 0600'dür (tempfile.mkstemp öyle açar): bu dosyalar proxy adresi
gibi gizli değerler taşıyabilir.

Windows: hedef başka bir süreçte açıkken os.replace PermissionError verir. Yerine koyma en çok
REPLACE_RETRIES kez, REPLACE_RETRY_PAUSE aralıkla yeniden denenir; denemeler tükenirse son PermissionError
çıkar.

Bu kod 2.x'te src/fsutil.py'deydi, ST-03 ile src/store/files.py'ye taşınmıştı; ST-28 onu Store'un dışına,
yapılandırma dosyalarının yanına aldı.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

__all__ = ["atomic_write_json", "atomic_write_text", "fcntl", "file_lock"]

REPLACE_RETRIES = 10
REPLACE_RETRY_PAUSE = 0.02  # saniye

_WINDOWS = os.name == "nt"


def _replace(src: str, dst: str) -> None:
    """os.replace; Windows'ta PermissionError (hedef açık) alınırsa kısa aralıklarla yeniden dener."""
    retries = REPLACE_RETRIES if _WINDOWS else 0
    for attempt in range(retries + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == retries:
                raise
            time.sleep(REPLACE_RETRY_PAUSE)


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Metni bayt bayt aynen yazar (satır sonu çevirisi yok); üst dizinleri oluşturur."""
    data = text.encode(encoding)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def atomic_write_json(path: str, data: Any, **dump_kwargs: Any) -> None:
    """JSON olarak atomik yazar: varsayılan olarak okunur UTF-8 ve girinti 2."""
    dump_kwargs.setdefault("ensure_ascii", False)
    dump_kwargs.setdefault("indent", 2)
    atomic_write_text(path, json.dumps(data, **dump_kwargs))


@contextlib.contextmanager
def file_lock(path: str) -> Iterator[None]:
    """
    Süreçler arası danışma kilidi (CLI ve web aynı config dosyasını düzenlerken).
    Kilit, hedefin yanındaki `<path>.lock` dosyasında tutulur; Windows'ta kilitlenmez.
    `fcntl` bu modülün adı üzerinden okunur: testler kilitsiz dalı `config_files.fcntl = None` ile dener.
    Store'un kendi kilitleri (lease) bu değildir: onlar src/store/lease.py'dedir.
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
