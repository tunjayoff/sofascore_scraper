"""
Yük dosyalarının biçimi (docs/design/01-storage.md, bölüm 4.1).

  * Saklanan baytlar: json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + UTF-8.
    Anahtar sırası yanıttaki sıradır. Platformda "ham" (raw) denen şey bu baytlardır: ayrıştırılmış
    yanıtın yeniden yazılmış hali, ağdan gelen baytlar değil.
  * Sıkıştırma: gzip.compress(data, 6, mtime=0). mtime=0 çıktıyı belirlenimci yapar: aynı yük hep
    aynı dosyayı verir.
  * sha256 sıkıştırılmamış baytlar üzerinden alınır ve manifeste yazılır.
  * Dosya soneki kodlamayı söyler. Okuyucu sonekten seçer: `.json.gz`, `.json` (eski düzen) ve bir
    zstd modülü içe aktarılabiliyorsa `.json.zst`. Yazıcı 3.0'da yalnızca gzip yazar.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import zlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Optional, Tuple, Type, Union

from sofascore_scraper.store import files
from sofascore_scraper.store.errors import LayoutError, PayloadCorrupt, StoreError

PathLike = Union[str, "os.PathLike[str]"]

GZIP_LEVEL = 6
SUFFIX_GZIP = ".json.gz"
SUFFIX_ZSTD = ".json.zst"
SUFFIX_JSON = ".json"


def _load_zstd() -> Optional[ModuleType]:
    """Python 3.14+: compression.zstd; daha eskide isteğe bağlı backports.zstd. İkisi de yoksa None."""
    try:
        from compression import zstd
        return zstd
    except ImportError:
        pass
    try:
        from backports import zstd
        return zstd
    except ImportError:
        return None


_zstd = _load_zstd()
# Açma hataları: gzip.BadGzipFile (bir OSError), yarım dosyada EOFError, bozuk akışta zlib.error / ZstdError
_DECODE_ERRORS: Tuple[Type[BaseException], ...] = (OSError, EOFError, zlib.error) + (
    (_zstd.ZstdError,) if _zstd is not None else ()
)


def zstd_available() -> bool:
    return _zstd is not None


@dataclass(frozen=True)
class Encoded:
    """Bir yükün diske yazılmaya hazır hali."""

    raw: bytes  # kurallı JSON baytları (sıkıştırılmamış)
    stored: bytes  # dosyaya yazılan baytlar (gzip)
    sha256: str  # raw üzerinden, onaltılık

    @property
    def raw_bytes(self) -> int:
        return len(self.raw)

    @property
    def stored_bytes(self) -> int:
        return len(self.stored)


def canonical_bytes(payload: Any) -> bytes:
    """Yükün saklanan (kurallı) JSON baytları. JSON'a çevrilemeyen yük StoreError verir (fatal değildir)."""
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as e:  # UnicodeEncodeError bir ValueError'dır
        raise StoreError(f"The payload could not be converted to JSON ({e})", detail=str(e)) from e


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compress(data: bytes) -> bytes:
    """Belirlenimci gzip: aynı girdi aynı çıktıyı verir (başlıkta zaman damgası yok)."""
    return gzip.compress(data, GZIP_LEVEL, mtime=0)


def encode(payload: Any) -> Encoded:
    raw = canonical_bytes(payload)
    return Encoded(raw=raw, stored=compress(raw), sha256=sha256_hex(raw))


def codec_of(path: PathLike) -> str:
    """Dosya adındaki sonekten kodlama: "gzip" | "zstd" | "json". Tanınmayan sonekte LayoutError."""
    name = os.path.basename(os.fspath(path))
    if name.endswith(SUFFIX_GZIP):
        return "gzip"
    if name.endswith(SUFFIX_ZSTD):
        return "zstd"
    if name.endswith(SUFFIX_JSON):
        return "json"
    raise LayoutError(f"Unknown payload file suffix: {os.fspath(path)}", path=os.fspath(path))


def _corrupt(path: PathLike, exc: BaseException) -> PayloadCorrupt:
    detail = str(exc) or type(exc).__name__
    return PayloadCorrupt(f"Payload file is corrupt ({detail}): {os.fspath(path)}", path=os.fspath(path), detail=detail)


def decode(stored: bytes, path: PathLike) -> bytes:
    """Dosyadan okunan baytları açar (kodlama `path`in sonekinden). Bozuk ya da yarım veri PayloadCorrupt verir."""
    kind = codec_of(path)
    if kind == "zstd" and _zstd is None:
        raise StoreError(
            "This file is compressed with zstd but the zstd module is missing "
            f"(Python 3.14+ or the `backports.zstd` package is needed): {os.fspath(path)}",
            path=os.fspath(path),
        )
    try:
        if kind == "gzip":
            raw = gzip.decompress(stored)
        elif kind == "zstd":
            raw = _zstd.decompress(stored)
        else:
            raw = stored
    except _DECODE_ERRORS as e:
        raise _corrupt(path, e) from e
    if not raw or raw.isspace():
        # Elektrik kesintisinden sonra boş kalmış dosya: gzip.decompress(b"") hata vermeden b"" döndürür
        raise _corrupt(path, ValueError("empty file"))
    return raw


def read_raw(path: PathLike) -> bytes:
    """
    Saklanan JSON baytları (açılmış, ayrıştırılmamış). Dosya yoksa PayloadMissing, açılamıyorsa
    PayloadCorrupt. JSON olarak geçerli olup olmadığına bakılmaz; onu `read_payload` yapar.
    """
    return decode(files.read_bytes(path), path)


def read_payload(path: PathLike) -> Any:
    """Yükü okur ve ayrıştırır. Dosya yoksa PayloadMissing; yarım, bozuk ya da geçersiz JSON ise PayloadCorrupt."""
    raw = read_raw(path)
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as e:  # JSONDecodeError ve UnicodeDecodeError birer ValueError'dır
        raise _corrupt(path, e) from e


def write_payload(path: PathLike, payload: Any, *, durable: Optional[bool] = None) -> Encoded:
    """
    Yükü kurallı baytlara çevirir, gzip ile sıkıştırır ve atomik yazar. Yol `.json.gz` ile bitmelidir.
    Dönen Encoded, manifeste yazılacak sha256 ve boyutları taşır.
    """
    if codec_of(path) != "gzip":
        raise LayoutError(f"Payload files are written as {SUFFIX_GZIP} only: {os.fspath(path)}",
                          path=os.fspath(path))
    encoded = encode(payload)
    files.write_bytes(path, encoded.stored, durable=durable)
    return encoded
