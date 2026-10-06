"""
Dışa aktarma: ham yükler ve satır yazıcıları (docs/design/01-storage.md, bölüm 4.5; plan maddesi ST-25).

`Exporter.raw` saklanan yükleri sıkıştırmasız dışa verir:

  * `fmt="tree"`: `<dest>/events/<id>/<anahtar>.json` (alt anahtarlı dilimde `<anahtar>/<alt>.json`). Her
    dosya saklanan baytların açılmış halidir; v3 dosyası `gzip.open` ile parça parça kopyalanır
    (`shutil.copyfileobj`), bellekte bir yükün tamamı bile tutulmaz. `pretty=True` yükü ayrıştırıp girintili
    yazar.
  * `fmt="jsonl"`: dilim başına bir satır,
    `{"event_id":...,"key":...,"sub":...,"fetched_at":...,"payload":...}`. v3 yükünün baytları satıra
    ayrıştırılmadan eklenir.

Eski düzendeki maç eski düzen okuyucusundan aynı biçimde çıkar. Eski dosyalar girintili yazılmıştır
(`indent=2`); dışa aktarmada yük ayrıştırılıp kurallı baytlara çevrilir (src/store/codec.py), v3'ün sakladığı
baytlar da bunlardır. Bu yüzden bir maçın eski düzen ve v3 kopyası aynı ağacı ve aynı JSONL satırlarını verir.
`fetched_at` katalogdaki değerdir (UTC, ISO 8601); yükseltme onu değiştirmez.

İş listesi katalogdan gelir ve tek bir okuma işlemi içinde okunur, dışa aktarma bu yüzden tutarlı bir anlık
görüntüdür: maçlar sorgunun sırasıyla konumlu sayfalarla (`events_sport_start` ya da
`events_tournament_season` dizini), her sayfanın dilimleri birincil anahtar aramalarıyla. WAL kipinde uzun bir
okuma işlemi yazarları durdurmaz. Görüntü alındıktan sonra silinen ya da bozulan bir yük atlanır ve raporda
(`ExportReport.skipped`) adıyla bildirilir; dışa aktarma durmaz.

`Exporter.rows` şema katmanından gelen satırları (eşlemeler) JSONL, CSV, SQLite ya da Parquet olarak yazar;
satırları bir kez, akış halinde okur. Parquet için `pyarrow` gerekir ve isteğe bağlı bir ektir
(`pip install -e ".[parquet]"`); yoksa çağrı paketi adıyla anan bir StoreError verir (servisler bunu
`not_supported`'a çevirir). SQLite dışa aktarması sözleşmenin tablosuyla yeni bir dosya yazar, `catalog.db`'nin
kopyası değildir.

Yazma kuralları:

  * Hedef bir yolsa çıktı önce hedefin yanında gizli bir geçici adla kurulur ve bittiğinde tek yeniden
    adlandırmayla yerine konur: yarım bir dışa aktarma hedefte görünmez. Hedef zaten varsa `overwrite=True`
    olmadıkça StoreError (boş bir dizin hedef sayılmaz).
  * Hedef bir ikili akışsa (`BinaryIO`, ör. bir HTTP yanıtı) JSONL ve CSV doğrudan akışa yazılır; SQLite ve
    Parquet önce `.meta/tmp/export.<rastgele>` altında kurulur (karar S16'nın `export` etiketi), sonra akışa
    kopyalanır ve hazırlık silinir. Akış kapatılmaz.
  * Dışa aktarma kilit almaz (`02-services.md` 2.8).
"""
from __future__ import annotations

import csv
import datetime as _dt
import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
import zlib
from dataclasses import dataclass
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from src.store import codec, files, layout
from src.store.errors import PayloadCorrupt, PayloadMissing, StoreError
from src.store.events import (
    LAYOUT_LEGACY,
    LAYOUT_V3,
    EventQuery,
    _check_sort,
    _keyset_page,
    _moment,
    _query_sql,
    decode_cursor,
)
from src.store.legacy import LegacyReader

if TYPE_CHECKING:
    from src.store.api import Store

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]

RAW_FORMATS: Tuple[str, ...] = ("tree", "jsonl")
ROW_FORMATS: Tuple[str, ...] = ("jsonl", "csv", "parquet", "sqlite")
EVENTS_SUBDIR = "events"  # ağaç kipinde maç dizinlerinin üst dizini
STAGING_LABEL = "export"  # .meta/tmp'deki hazırlık girdisinin sahibi (karar S16)
SKIP_MISSING = "missing"  # katalog yük var diyor, dosya yok (görüntüden sonra silinmiş ya da taşınmış)
SKIP_CORRUPT = "corrupt"  # dosya açılamıyor ya da JSON değil
PARQUET_PACKAGE = "pyarrow"

EVENT_BATCH = 500  # bir okuma sayfasındaki maç sayısı
ROW_BATCH = 1000  # SQLite'a bir seferde eklenen satır
PARQUET_BATCH = 10_000  # Parquet satır grubu; sütun türleri ilk grubun değerlerinden çıkarılır
_COPY_CHUNK = 1 << 16
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1


# --- sonuç türleri ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExportSkip:
    """Dışa aktarılamayan bir dilim: `reason` "missing" (dosya yok) ya da "corrupt" (okunamıyor)."""

    event_id: int
    key: str
    sub: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ExportFile:
    """`exports/` dizinindeki bir dosya (FX-19: API'nin ve `ssc export`'un yazdıkları). modified_at epoch saniye."""

    name: str
    size: int
    modified_at: float


# `exports/` dizininde listelenen ve indirilebilen dosya adı: yol ayırıcısız, gizli değil
_EXPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._,()+-]{0,199}$")


@dataclass(frozen=True)
class ExportReport:
    """
    Bir dışa aktarmanın sonucu.

    fmt      istenen biçim
    dest     yazılan yol (mutlak); hedef bir akışsa boş
    events   en az bir dilimi yazılan maç sayısı (`raw`); `rows` için 0
    items    ağaçta dosya, JSONL'de satır, `rows`'ta satır sayısı
    bytes    hedefe yazılan bayt sayısı
    skipped  atlanan dilimler, görüldükleri sırayla
    """

    fmt: str
    dest: str
    events: int
    items: int
    bytes: int
    skipped: Tuple[ExportSkip, ...] = ()


# --- yardımcılar --------------------------------------------------------------------------------------

def _iso(epoch: Any) -> Optional[str]:
    moment = _moment(epoch)
    return moment.isoformat() if moment is not None else None


def _plain(value: Any) -> Any:
    """Satır değerinin JSON'a uygun hali: tarih ISO metni olur, gerisi olduğu gibi kalır."""
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    return value


def _json_default(value: Any) -> Any:
    plain = _plain(value)
    if plain is value:
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
    return plain


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _text(value: Any) -> str:
    """CSV hücresi ve metin sütunu: None boş, bool `true`/`false`, sözlük ve liste kurallı JSON."""
    value = _plain(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return _dumps(value)


def _check_columns(columns: Sequence[str], *, fold_case: bool = False) -> Tuple[str, ...]:
    if isinstance(columns, (str, bytes)) or not isinstance(columns, Sequence):
        raise ValueError(f"columns: expected a sequence of names, got {columns!r}")
    out = tuple(columns)
    if not out:
        raise ValueError("columns: expected at least one column")
    seen: set = set()
    for name in out:
        if not isinstance(name, str) or not name or "\x00" in name:
            raise ValueError(f"columns: invalid column name {name!r}")
        folded = name.casefold() if fold_case else name
        if folded in seen:
            raise ValueError(f"columns: duplicate column name {name!r}")
        seen.add(folded)
    return out


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row_values(row: Mapping[str, Any], columns: Sequence[str]) -> List[Any]:
    if not isinstance(row, Mapping):
        raise ValueError(f"rows: expected mappings, got {type(row).__name__}")
    return [row.get(name) for name in columns]


def _is_stream(dest: Any) -> bool:
    return hasattr(dest, "write") and not isinstance(dest, (str, bytes, os.PathLike))


class _Counter:
    """İkili akışa yazar ve yazılan baytları sayar; akışı kapatmaz."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self.count = 0

    def write(self, data: bytes) -> int:
        self._stream.write(data)
        self.count += len(data)
        return len(data)


class _TextOut:
    """csv.writer için metin yazarı: UTF-8'e çevirip ikili hedefe verir."""

    def __init__(self, out: Callable[[bytes], Any]) -> None:
        self._out = out

    def write(self, text: str) -> int:
        self._out(text.encode("utf-8"))
        return len(text)


# --- hedef yolları ------------------------------------------------------------------------------------

def _target(dest: PathLike) -> str:
    try:
        path = os.path.abspath(os.fspath(dest))
    except TypeError as e:
        raise ValueError(f"dest: expected a path or a binary stream, got {dest!r}") from e
    if os.path.basename(path) in ("", ".", ".."):
        raise ValueError(f"dest: not a usable output name: {path!r}")
    return path


def _occupied(path: str) -> bool:
    """Hedef dolu mu: var olan dosya ya da içi boş olmayan dizin."""
    if not os.path.lexists(path):
        return False
    if os.path.isdir(path) and not os.path.islink(path):
        try:
            return bool(os.listdir(path))
        except OSError as e:
            raise StoreError.from_exception(e, path, reading=True) from e
    return True


def _sibling(path: str, tag: str) -> str:
    """Hedefin yanında, aynı dosya sisteminde gizli bir geçici ad."""
    return os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.{tag}-{uuid.uuid4().hex}")


def _prepare(path: str, overwrite: bool) -> None:
    if _occupied(path) and not overwrite:
        raise StoreError(f"Export target already exists: {path}", path=path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as e:
        raise StoreError.from_exception(e, os.path.dirname(path)) from e


def _publish(staged: str, path: str) -> None:
    """Kurulan çıktıyı hedefe koyar; var olan hedef (dosya ya da dizin) önce kenara alınır, sonra silinir."""
    aside: Optional[str] = None
    if os.path.lexists(path):
        aside = _sibling(path, "old")
        files.replace(path, aside)
    try:
        files.replace(staged, path)
    except StoreError:
        if aside is not None:
            files.replace(aside, path)
        raise
    if aside is not None:
        files.remove_tree(aside)


def _discard(path: Optional[str]) -> None:
    if path is None:
        return
    try:
        files.remove_tree(path)
    except StoreError as e:  # asıl hatayı örtmesin
        logger.warning("Could not remove the unfinished export %s: %s", path, e)


# --- dışa aktarıcı ------------------------------------------------------------------------------------

class _RawOut:
    """`raw`'ın yazdığı yer: ağaçta bir dizin, JSONL'de tek bir dosya."""

    def __init__(self, fmt: str, root: str, pretty: bool) -> None:
        self.fmt = fmt
        self.root = root
        self.pretty = pretty
        self.items = 0
        self.bytes = 0
        self._lines: Optional[BinaryIO] = None
        self._made: set = set()

    def open(self) -> None:
        try:
            if self.fmt == "jsonl":
                self._lines = open(self.root, "wb")
            else:
                os.makedirs(self.root)
        except OSError as e:
            raise StoreError.from_exception(e, self.root) from e

    def close(self) -> None:
        if self._lines is not None:
            try:
                self._lines.close()
            except OSError as e:
                raise StoreError.from_exception(e, self.root) from e
            self._lines = None

    def file_path(self, event_id: int, key: str, sub: str) -> str:
        parts = [self.root, EVENTS_SUBDIR, str(event_id)] + ([key, sub] if sub else [key])
        parts[-1] += ".json"
        path = os.path.join(*parts)
        parent = os.path.dirname(path)
        if parent not in self._made:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                raise StoreError.from_exception(e, parent) from e
            self._made.add(parent)
        return path

    def write_file(self, path: str, data: bytes) -> None:
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            raise StoreError.from_exception(e, path) from e
        self.items += 1
        self.bytes += len(data)

    def stream_file(self, path: str, source: str) -> None:
        """v3 dosyasını açarak parça parça kopyalar. Bozuk ya da kayıp kaynakta yarım dosya silinir."""
        try:
            with open(path, "wb") as dst:
                copied = _copy_gzip(source, dst.write, path)
        except BaseException:
            _drop(path)
            raise
        self.items += 1
        self.bytes += copied

    def write_line(self, event_id: int, key: str, sub: str, fetched_at: Optional[str], *,
                   data: Optional[bytes] = None, source: Optional[str] = None) -> None:
        """
        Bir JSONL satırı: önek, yük, kapanış. Yük ya baytlarıyla (`data`) ya da v3 dosyasından parça parça
        (`source`) yazılır; ikincisinde yükün tamamı hiç bellekte durmaz. Kaynak bozuksa satır geri alınır.
        """
        head = _dumps({"event_id": event_id, "key": key, "sub": sub, "fetched_at": fetched_at})
        prefix = (head[:-1] + ',"payload":').encode("utf-8")
        lines = self._lines
        assert lines is not None
        start = lines.tell()
        try:
            lines.write(prefix)
            if source is not None:
                size = _copy_gzip(source, lines.write, self.root)
            else:
                assert data is not None
                lines.write(data)
                size = len(data)
            lines.write(b"}\n")
        except (PayloadMissing, PayloadCorrupt):
            try:
                lines.seek(start)
                lines.truncate()
            except OSError as e:
                raise StoreError.from_exception(e, self.root) from e
            raise
        except OSError as e:
            raise StoreError.from_exception(e, self.root) from e
        self.items += 1
        self.bytes += len(prefix) + size + 2


def _copy_gzip(source: str, write: Callable[[bytes], Any], target: str) -> int:
    """
    gzip dosyasını açarak parça parça `write`'a verir; kopyalanan bayt sayısını döndürür. Kaynak yoksa
    PayloadMissing; yarım, bozuk ya da boş akışta PayloadCorrupt; hedefe yazılamazsa StoreError.
    """
    copied = 0
    try:
        src = gzip.open(source, "rb")
    except FileNotFoundError as e:
        raise PayloadMissing(f"Payload file not found: {source}", path=source) from e
    except OSError as e:
        raise StoreError.from_exception(e, source, reading=True) from e
    with src:
        while True:
            try:
                chunk = src.read(_COPY_CHUNK)
            except FileNotFoundError as e:
                raise PayloadMissing(f"Payload file not found: {source}", path=source) from e
            except (EOFError, zlib.error, gzip.BadGzipFile) as e:
                raise PayloadCorrupt(f"Payload file is corrupt ({e or type(e).__name__}): {source}", path=source,
                                     detail=str(e) or type(e).__name__) from e
            except OSError as e:
                raise StoreError.from_exception(e, source, reading=True) from e
            if not chunk:
                break
            try:
                write(chunk)
            except OSError as e:
                raise StoreError.from_exception(e, target) from e
            copied += len(chunk)
    if copied == 0 or (copied < _COPY_CHUNK and not _has_content(source)):
        raise PayloadCorrupt(f"Payload file is corrupt (empty payload): {source}", path=source,
                             detail="empty payload")
    return copied


def _has_content(source: str) -> bool:
    """Kısa yükün yalnızca boşluktan oluşmadığı (codec.decode'un kuralı)."""
    try:
        return not codec.read_raw(source).isspace()
    except (PayloadMissing, PayloadCorrupt):
        return False


def _drop(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


class Exporter:
    """Ham yüklerin ve satırların dışa aktarılması (`Store.export`)."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._data_dir = str(store.data_dir)
        self._reader = LegacyReader(self._data_dir)

    # -- `exports/` dizini (FX-19) ---------------------------------------------------------------------

    def files(self) -> List[ExportFile]:
        """
        Veri dizininin `exports/` dizinindeki dosyalar, en yeni önce (API'nin dışa aktarma işlerinin ve
        `ssc export`'un yazdıkları). Dizin yoksa boş liste; alt dizinler ve adı uymayan girdiler atlanır.
        """
        root = layout.resolve(self._data_dir, layout.EXPORTS_DIR)
        found: List[ExportFile] = []
        try:
            with os.scandir(root) as scan:
                for entry in scan:
                    if not _EXPORT_NAME.match(entry.name) or not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                    found.append(ExportFile(entry.name, int(st.st_size), float(st.st_mtime)))
        except FileNotFoundError:
            return []
        except OSError as e:
            raise StoreError.from_exception(e, root, reading=True) from e
        return sorted(found, key=lambda f: (-f.modified_at, f.name))

    def file_path(self, name: str) -> Optional[str]:
        """`exports/` altındaki dosyanın yolu; ad uymuyorsa ya da dosya yoksa None (dizin dışına çıkılamaz)."""
        if not isinstance(name, str) or not _EXPORT_NAME.match(name):
            return None
        path = layout.resolve(self._data_dir, f"{layout.EXPORTS_DIR}/{name}")
        return path if os.path.isfile(path) and not os.path.islink(path) else None

    # -- ham yükler ------------------------------------------------------------------------------------

    def raw(self, q: EventQuery, dest: PathLike, *, keys: Optional[Sequence[str]] = None,
            fmt: Literal["tree", "jsonl"] = "tree", pretty: bool = False,
            overwrite: bool = False) -> ExportReport:
        """
        Sorguya uyan maçların saklanan yüklerini sıkıştırmasız yazar (bölüm 4.5). Sıra sorgunun sırasıdır
        (`q.sort`); `q.cursor` verilirse oradan sürer, `q.limit` ve `q.offset` yok sayılır. keys: yalnızca bu
        dilim anahtarları (bütün alt anahtarlarıyla); None hepsi.

        fmt "tree": `dest` bir dizin olur. fmt "jsonl": `dest` tek bir dosya olur; `pretty` yalnızca ağaçta
        geçerlidir. Hedef doluysa ve `overwrite` verilmediyse StoreError. Görüntüden sonra kaybolan ya da
        bozuk yük atlanır (`ExportReport.skipped`).
        """
        if fmt not in RAW_FORMATS:
            raise ValueError(f"fmt: expected one of {', '.join(RAW_FORMATS)}, got {fmt!r}")
        if pretty and fmt != "tree":
            raise ValueError("pretty: only the tree format can be indented")
        if not isinstance(q, EventQuery):
            raise ValueError(f"q: expected an EventQuery, got {type(q).__name__}")
        wanted = None
        if keys is not None:
            if isinstance(keys, str):
                raise ValueError(f"keys: expected a sequence of slice keys, got {keys!r}")
            wanted = frozenset(layout.validate_key(key) for key in keys)
        sort = _check_sort(q.sort)
        conditions, params = _query_sql(q)
        after = decode_cursor(q.cursor, sort) if q.cursor is not None else None

        path = _target(dest)
        self._store._require_open()
        _prepare(path, overwrite)
        out = _RawOut(fmt, _sibling(path, "export"), pretty)
        skipped: List[ExportSkip] = []
        events = 0
        catalog = self._store._catalog
        assert catalog is not None
        try:
            out.open()
            with catalog.read() as conn:  # tek okuma işlemi: bütün dışa aktarma aynı anlık görüntü
                while True:
                    rows = _keyset_page(conn, conditions, params, sort, after, EVENT_BATCH)
                    if not rows:
                        break
                    by_event: Dict[int, List[Tuple[str, str, Any]]] = {}
                    ids = ", ".join(str(row.id) for row in rows)
                    for event_id, key, sub, fetched_at in conn.execute(
                            "SELECT event_id, key, sub, fetched_at FROM event_slices "
                            f"WHERE event_id IN ({ids}) AND has_payload = 1 ORDER BY event_id, key, sub"):
                        if wanted is None or key in wanted:
                            by_event.setdefault(int(event_id), []).append((str(key), str(sub), fetched_at))
                    for row in rows:
                        written = 0
                        for key, sub, fetched_at in by_event.get(row.id, ()):
                            try:
                                self._export_slice(out, row.id, row.layout, row.path, key, sub, fetched_at)
                                written += 1
                            except (PayloadMissing, PayloadCorrupt) as e:
                                reason = SKIP_MISSING if isinstance(e, PayloadMissing) else SKIP_CORRUPT
                                skipped.append(ExportSkip(row.id, key, sub, reason, e.detail or str(e)))
                                logger.warning("Export skipped event %s slice %s (%s): %s", row.id,
                                               layout.slice_name(key, sub), reason, e)
                        events += 1 if written else 0
                    if len(rows) < EVENT_BATCH:
                        break
                    after = (rows[-1].start_ts, rows[-1].id)
            out.close()
            _publish(out.root, path)
        except BaseException:
            try:
                out.close()
            except StoreError:
                pass
            _discard(out.root)
            raise
        logger.info("Raw export written to %s: fmt=%s events=%d items=%d bytes=%d skipped=%d", path, fmt, events,
                    out.items, out.bytes, len(skipped))
        return ExportReport(fmt=fmt, dest=path, events=events, items=out.items, bytes=out.bytes,
                            skipped=tuple(skipped))

    def _export_slice(self, out: _RawOut, event_id: int, where: Optional[str], directory: Optional[str], key: str,
                      sub: str, fetched_at: Any) -> None:
        source: Optional[str] = None
        if where == LAYOUT_V3:
            source = layout.resolve(self._data_dir, layout.slice_path(layout.event_dir(event_id), key, sub))
            if out.fmt == "jsonl":
                out.write_line(event_id, key, sub, _iso(fetched_at), source=source)
                return
            if not out.pretty:
                out.stream_file(out.file_path(event_id, key, sub), source)
                return
            data = codec.read_raw(source)
        elif where == LAYOUT_LEGACY and directory and not sub:
            data = self._legacy_bytes(directory, key)
        else:
            raise PayloadMissing(f"No payload file for event {event_id} slice {layout.slice_name(key, sub)}",
                                 path=directory)
        if out.fmt == "jsonl":
            out.write_line(event_id, key, sub, _iso(fetched_at), data=data)
            return
        if out.pretty:
            data = _pretty(data, source or directory or "")
        out.write_file(out.file_path(event_id, key, sub), data)

    def _legacy_bytes(self, directory: str, key: str) -> bytes:
        """Eski düzendeki yükün kurallı baytları (v3'ün saklayacağı baytlar)."""
        value = self._reader.read_payload(directory, key)
        if value is None and self._reader.read_payload(directory, key, raw=True) is None:
            where = self._reader.resolve(directory)
            raise PayloadMissing(f"Payload file not found: {where} ({key})", path=where)
        try:
            return codec.canonical_bytes(value)
        except StoreError as e:
            raise PayloadCorrupt(str(e), path=self._reader.resolve(directory), detail=e.detail) from e

    # -- satırlar --------------------------------------------------------------------------------------

    def rows(self, rows: Iterable[Mapping[str, Any]], columns: Sequence[str], dest: Union[PathLike, BinaryIO],
             fmt: Literal["jsonl", "csv", "parquet", "sqlite"], *, table: str = "rows",
             overwrite: bool = False, types: Optional[Mapping[str, str]] = None) -> ExportReport:
        """
        Satırları `columns` sırasıyla yazar. Satırda olmayan sütun boş (None) değerdir, `columns`'ta olmayan
        anahtar yazılmaz. Satırlar bir kez, akış halinde okunur.

          jsonl    satır başına bir JSON nesnesi (`columns` sırasıyla)
          csv      başlık satırı ve satırlar; UTF-8, `\\n` satır sonu; None boş hücre, bool `true`/`false`,
                   sözlük ve liste kurallı JSON
          sqlite   yeni bir dosyada `table` tablosu; bool 0/1, sözlük ve liste JSON metni
          parquet  `pyarrow` gerekir; sütun türleri ilk 10 000 satırın değerlerinden çıkarılır (bool, int64,
                   float64, metin; sözlük ve liste JSON metni) ve sonraki satırlar o türe uymalıdır. `types`
                   bir sütunun türünü verir (`bool`, `int64`, `float64`, `string`): o sütunda çıkarım yapılmaz,
                   ilk satır grubunda hep boş olan sütun da o türdedir. Öteki biçimler `types`'ı okumaz.

        Tarih ve saat değerleri ISO metni olarak yazılır. `dest` bir yol ya da ikili akış olabilir.
        """
        if fmt not in ROW_FORMATS:
            raise ValueError(f"fmt: expected one of {', '.join(ROW_FORMATS)}, got {fmt!r}")
        names = _check_columns(columns, fold_case=fmt == "sqlite")
        if fmt == "sqlite" and (not isinstance(table, str) or not table or "\x00" in table
                                or table.lower().startswith("sqlite_")):
            raise ValueError(f"table: invalid table name {table!r}")
        kinds = _check_types(types, names)
        writer = _ROW_WRITERS[fmt]
        if fmt == "parquet":
            _pyarrow()  # paket yoksa hiçbir şey yazılmadan StoreError

        if _is_stream(dest):
            stream = _Counter(dest)  # type: ignore[arg-type]
            if fmt in ("jsonl", "csv"):
                count = writer(rows, names, stream.write, table)
                return ExportReport(fmt=fmt, dest="", events=0, items=count, bytes=stream.count)
            staging = files.new_staging_dir(self._data_dir, STAGING_LABEL)
            try:
                staged = os.path.join(staging, f"rows.{fmt}")
                count = self._to_file(writer, rows, names, staged, table, kinds)
                try:
                    with open(staged, "rb") as f:
                        shutil.copyfileobj(f, stream)  # type: ignore[misc]
                except OSError as e:
                    raise StoreError.from_exception(e, staged, reading=True) from e
            finally:
                _discard(staging)
            return ExportReport(fmt=fmt, dest="", events=0, items=count, bytes=stream.count)

        path = _target(dest)  # type: ignore[arg-type]
        _prepare(path, overwrite)
        staged = _sibling(path, "export")
        try:
            count = self._to_file(writer, rows, names, staged, table, kinds)
            size = os.path.getsize(staged)
            _publish(staged, path)
        except BaseException:
            _discard(staged)
            raise
        logger.info("Rows exported to %s: fmt=%s rows=%d bytes=%d", path, fmt, count, size)
        return ExportReport(fmt=fmt, dest=path, events=0, items=count, bytes=size)

    @staticmethod
    def _to_file(writer: "_RowWriter", rows: Iterable[Mapping[str, Any]], names: Sequence[str], path: str,
                 table: str, kinds: Optional[Mapping[str, str]] = None) -> int:
        if writer is _write_parquet:
            return writer(rows, names, path, table, kinds)
        if writer is _write_sqlite:
            return writer(rows, names, path, table)
        try:
            with open(path, "wb") as f:
                return writer(rows, names, f.write, table)
        except OSError as e:
            raise StoreError.from_exception(e, path) from e


# --- satır yazıcıları ---------------------------------------------------------------------------------

_RowWriter = Callable[..., int]


def _write_jsonl(rows: Iterable[Mapping[str, Any]], names: Sequence[str], out: Callable[[bytes], Any],
                 table: str) -> int:
    count = 0
    for row in rows:
        values = _row_values(row, names)
        try:
            line = _dumps(dict(zip(names, values, strict=True)))
        except (TypeError, ValueError) as e:
            raise StoreError(f"Row {count + 1} cannot be written as JSON ({e})", detail=str(e)) from e
        out(line.encode("utf-8") + b"\n")
        count += 1
    return count


def _write_csv(rows: Iterable[Mapping[str, Any]], names: Sequence[str], out: Callable[[bytes], Any],
               table: str) -> int:
    writer = csv.writer(_TextOut(out), lineterminator="\n")
    writer.writerow(names)
    count = 0
    for row in rows:
        try:
            writer.writerow([_text(value) for value in _row_values(row, names)])
        except (TypeError, ValueError) as e:
            raise StoreError(f"Row {count + 1} cannot be written as CSV ({e})", detail=str(e)) from e
        count += 1
    return count


def _sqlite_value(value: Any) -> Any:
    value = _plain(value)
    if value is None or isinstance(value, (str, float, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value if _INT64_MIN <= value <= _INT64_MAX else str(value)
    return _dumps(value)


def _write_sqlite(rows: Iterable[Mapping[str, Any]], names: Sequence[str], path: str, table: str) -> int:
    count = 0
    try:
        conn = sqlite3.connect(path, isolation_level=None)
    except sqlite3.Error as e:
        raise StoreError(f"Cannot create the SQLite export ({e}): {path}", path=path, detail=str(e)) from e
    try:
        conn.execute("PRAGMA journal_mode = OFF")  # hazırlık dosyası: bitince yerine konur
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("BEGIN")
        conn.execute(f"CREATE TABLE {_quote(table)} ({', '.join(_quote(name) for name in names)})")
        sql = (f"INSERT INTO {_quote(table)} ({', '.join(_quote(name) for name in names)}) "
               f"VALUES ({', '.join('?' for _ in names)})")
        batch: List[List[Any]] = []
        for row in rows:
            try:
                batch.append([_sqlite_value(value) for value in _row_values(row, names)])
            except (TypeError, ValueError) as e:
                raise StoreError(f"Row {count + len(batch) + 1} cannot be written to SQLite ({e})",
                                 detail=str(e)) from e
            if len(batch) >= ROW_BATCH:
                conn.executemany(sql, batch)
                count += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            count += len(batch)
        conn.execute("COMMIT")
    except sqlite3.Error as e:
        raise StoreError(f"Cannot write the SQLite export ({e}): {path}", path=path, detail=str(e)) from e
    finally:
        conn.close()
    return count


def _pyarrow() -> Any:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError as e:
        raise StoreError(
            f"Parquet export needs the '{PARQUET_PACKAGE}' package, which is not installed "
            f"(pip install {PARQUET_PACKAGE}, or pip install -e \".[parquet]\")",
            detail=PARQUET_PACKAGE) from e
    return pyarrow


# Parquet sütun türleri (ilk satır grubundan çıkarılır)
_BOOL, _INT, _FLOAT, _STR = "bool", "int64", "float64", "string"
_KINDS: Tuple[str, ...] = (_BOOL, _INT, _FLOAT, _STR)


def _infer(values: Iterable[Any]) -> str:
    kinds = set()
    for value in values:
        value = _plain(value)
        if value is None:
            continue
        if isinstance(value, bool):
            kinds.add(_BOOL)
        elif isinstance(value, int) and _INT64_MIN <= value <= _INT64_MAX:
            kinds.add(_INT)
        elif isinstance(value, float):
            kinds.add(_FLOAT)
        else:
            kinds.add(_STR)
    if len(kinds) == 1:
        return kinds.pop()
    if kinds == {_INT, _FLOAT}:
        return _FLOAT
    return _STR


def _convert(value: Any, kind: str, column: str) -> Any:
    value = _plain(value)
    if value is None:
        return None
    if kind == _STR:
        return _text(value)
    if kind == _FLOAT and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if kind == _INT and isinstance(value, int) and not isinstance(value, bool) and _INT64_MIN <= value <= _INT64_MAX:
        return value
    if kind == _BOOL and isinstance(value, bool):
        return value
    raise StoreError(f"Parquet column {column!r}: a value of type {type(value).__name__} does not fit the type "
                     f"{kind} taken from the first {PARQUET_BATCH} rows", detail=column)


def _batches(rows: Iterable[Mapping[str, Any]], names: Sequence[str], size: int) -> Iterator[List[List[Any]]]:
    batch: List[List[Any]] = []
    for row in rows:
        batch.append(_row_values(row, names))
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _check_types(types: Optional[Mapping[str, str]], names: Sequence[str]) -> Dict[str, str]:
    """`rows`'un `types`'ı: bilinen sütunlar ve bilinen türler; yoksa boş."""
    if types is None:
        return {}
    if not isinstance(types, Mapping):
        raise ValueError(f"types: expected a mapping of column to type, got {types!r}")
    out: Dict[str, str] = {}
    for name, kind in types.items():
        if name not in names:
            raise ValueError(f"types: {name!r} is not one of the columns")
        if kind not in _KINDS:
            raise ValueError(f"types: expected one of {', '.join(_KINDS)} for {name!r}, got {kind!r}")
        out[name] = kind
    return out


def _write_parquet(rows: Iterable[Mapping[str, Any]], names: Sequence[str], path: str, table: str,
                   given: Optional[Mapping[str, str]] = None) -> int:
    given = given or {}
    pa = _pyarrow()
    pq = pa.parquet
    types = {_BOOL: pa.bool_(), _INT: pa.int64(), _FLOAT: pa.float64(), _STR: pa.string()}
    kinds: Optional[List[str]] = None
    schema = None
    writer = None
    count = 0
    try:
        for batch in _batches(rows, names, PARQUET_BATCH):
            if kinds is None:
                kinds = [given.get(names[i]) or _infer(row[i] for row in batch) for i in range(len(names))]
                schema = pa.schema([(name, types[kind]) for name, kind in zip(names, kinds, strict=True)])
                writer = pq.ParquetWriter(path, schema)
            arrays = [pa.array([_convert(row[i], kinds[i], names[i]) for row in batch], type=types[kinds[i]])
                      for i in range(len(names))]
            writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
            count += len(batch)
        if writer is None:  # satır yok: yalnızca şema (türü verilmeyen sütunlar metin)
            schema = pa.schema([(name, types[given.get(name, _STR)]) for name in names])
            writer = pq.ParquetWriter(path, schema)
        writer.close()
    except StoreError:
        raise
    except OSError as e:
        raise StoreError.from_exception(e, path) from e
    except (pa.ArrowException, ValueError, TypeError) as e:
        raise StoreError(f"Cannot write the Parquet export ({e}): {path}", path=path, detail=str(e)) from e
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 - ikinci kapatma ya da yarım dosya; asıl hata yukarıda
                pass
    return count


_ROW_WRITERS: Dict[str, _RowWriter] = {
    "jsonl": _write_jsonl,
    "csv": _write_csv,
    "sqlite": _write_sqlite,
    "parquet": _write_parquet,
}


def _pretty(data: bytes, where: str) -> bytes:
    try:
        value = json.loads(data)
    except (ValueError, RecursionError) as e:
        raise PayloadCorrupt(f"Payload is not valid JSON ({e}): {where}", path=where or None, detail=str(e)) from e
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


__all__ = [
    "Exporter",
    "ExportFile",
    "ExportReport",
    "ExportSkip",
    "RAW_FORMATS",
    "ROW_FORMATS",
    "SKIP_MISSING",
    "SKIP_CORRUPT",
    "PARQUET_PACKAGE",
]
