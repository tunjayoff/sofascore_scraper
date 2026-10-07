"""
Dilim geçmişi: sürümlenen dilimlerin (oranlar ve her türlü sürümlenen dilim) anlık görüntüleri
(docs/design/01-storage.md, bölüm 2.3, 3.3, 3.4 adım 7, 3.6 I8, 4.2 ve 9.3).

Geçmiş dosyası varlık dizinindedir: `_history/<key>/<sub ya da "_">.jsonl.gz`. Her anlık görüntü bir gzip
üyesidir ve tek bir satır taşır:

    {"fetched_at":"<ISO 8601, UTC>","sha256":"<yükün özeti>","payload":<kurallı yük baytları>}\\n

Üyeler dosyanın sonuna eklenir; dosyanın tamamı geçerli, çok üyeli bir gzip akışıdır (`zcat` JSON Lines
basar). `payload`, yük dosyasındakiyle aynı kurallı baytlardır (sofascore_scraper/store/codec.py) ve `sha256` onların
özetidir: aynı yükün geçmişteki ve dilim dosyasındaki özeti aynıdır.

Katalogdaki `slice_history` tablosu her üye için bir satır tutar: sıra (`n`, dosyadaki 1'den başlayan konum),
zaman (tam saniye), özet ve üyenin bayt aralığı (`offset`, `length`). Bir anlık görüntü yalnızca o aralık
açılarak okunur. Satırlar dosyalardan türer (yeniden kurma, `history_rows`); manifestteki
`history` alanı (`count`, `last_sha256`) dilimin geçmişinin özetidir.

Yarım üye (yazma sırasında ölen süreç): üye üye okuyan okuyucu, açılamayan ya da satırı ayrıştırılamayan ilk
üyede durur ve gerisini yok sayar. Bir sonraki ekleme önce dosyayı son sağlam üyenin sonuna kısaltır.

Yazma `EventStore.put(keep_history=...)` (sofascore_scraper/store/events.py) ve `HistoryStore.prune` ile olur; ikisi de
bölüm 6.2'deki yazma protokolünün içindedir (katalogun yazma kilidi altında). Maç olmayan varlıkların geçmişi
onların yazıcısıyla gelir (plan maddesi ST-22); okuma yöntemleri her `Ref` türünü kabul eder.
"""
from __future__ import annotations

import json
import logging
import os
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple, Union

from sofascore_scraper.store import codec, derive, files, layout
from sofascore_scraper.store import manifest as manifest_mod
from sofascore_scraper.store.errors import LayoutError, PayloadCorrupt, PayloadMissing, StoreError
from sofascore_scraper.store.events import STEP_MANIFEST, Ref, check_int
from sofascore_scraper.store.manifest import HistoryMark

if TYPE_CHECKING:
    import sqlite3

    from sofascore_scraper.store.api import Store

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]
Row = Dict[str, Any]

LEASE_WRITER = "writer"  # `prune` bu kilidi gerektirir (bölüm 6.1)
_CHUNK = 64 * 1024  # üye sınırı aranırken açıcıya bir seferde verilen bayt
_NO_SUB = "_"  # alt anahtarı olmayan dilimin geçmiş dosyasının adı (sofascore_scraper/store/layout.py)


# --- üyeler -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Member:
    """Geçmiş dosyasındaki sağlam bir üye."""

    n: int  # 1'den başlayan konum
    offset: int  # üyenin dosyadaki ilk baytı
    length: int  # üyenin bayt uzunluğu (sıkıştırılmış)
    fetched_at: datetime
    sha256: str
    raw: bytes  # yükün kurallı baytları (sıkıştırılmamış)


@dataclass(frozen=True)
class Scan:
    """Bir geçmiş dosyasının okunması: sağlam üyeler ve onların bittiği yer."""

    members: Tuple[Member, ...]
    good_end: int  # son sağlam üyenin bittiği bayt; dosya bundan uzunsa kuyruk yarım ya da bozuk
    size: int  # dosyanın boyutu

    @property
    def torn(self) -> bool:
        return self.size > self.good_end


def _prefix(fetched_at: str, sha256: str) -> bytes:
    return (b'{"fetched_at":' + json.dumps(fetched_at).encode("ascii") + b',"sha256":'
            + json.dumps(sha256).encode("ascii") + b',"payload":')


def timestamp(value: datetime) -> str:
    """Geçmiş satırındaki zaman: UTC, ISO 8601 (manifestteki biçim; saniyenin kesri korunur)."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat()


def encode_member(raw: bytes, sha256: str, fetched_at: datetime) -> bytes:
    """
    Bir anlık görüntünün gzip üyesi. `raw` yükün kurallı baytları (`codec.Encoded.raw`), `sha256` onların özeti.
    Satır elle kurulur ki içindeki yük baytları yük dosyasındakilerle aynı olsun.
    """
    line = _prefix(timestamp(fetched_at), sha256) + raw + b"}\n"
    return codec.compress(line)


def parse_line(line: bytes) -> Tuple[datetime, str, bytes]:
    """
    Bir üyenin açılmış satırı → (zaman, özet, yükün kurallı baytları). Satır geçerli bir anlık görüntü değilse
    (JSON değil, alan eksik, özet yüke uymuyor) ValueError.
    """
    found = json.loads(line)
    if not isinstance(found, dict) or "payload" not in found:
        raise ValueError("not a snapshot line")
    at, digest = found.get("fetched_at"), found.get("sha256")
    if not isinstance(at, str) or not isinstance(digest, str):
        raise ValueError("fetched_at or sha256 missing")
    moment = datetime.fromisoformat(at[:-1] + "+00:00" if at.endswith(("Z", "z")) else at)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    head = _prefix(at, digest)
    if line.startswith(head) and line.endswith(b"}\n"):
        raw = line[len(head):-2]
    else:  # başka bir araçla yazılmış satır: yük yeniden kurallı hale getirilir
        raw = codec.canonical_bytes(found["payload"])
    if codec.sha256_hex(raw) != digest:
        raise ValueError("sha256 does not match the payload")
    return moment.astimezone(timezone.utc), digest, raw


def _inflate(view: memoryview, start: int) -> Optional[Tuple[bytes, int]]:
    """`start`taki gzip üyesini açar: (açılmış baytlar, üyenin bittiği yer); üye yarım ya da bozuksa None."""
    size = len(view)
    inflater = zlib.decompressobj(31)  # 31: gzip başlığı ve CRC denetimi
    parts: List[bytes] = []
    fed = start
    try:
        while not inflater.eof and fed < size:
            chunk = view[fed:fed + _CHUNK]
            fed += len(chunk)
            parts.append(inflater.decompress(chunk))
    except zlib.error:
        return None
    if not inflater.eof:
        return None
    return b"".join(parts), fed - len(inflater.unused_data)


def scan_bytes(data: bytes) -> Scan:
    """Geçmiş dosyasının baytlarındaki sağlam üyeler: açılamayan ya da satırı geçersiz olan ilk üyede durulur."""
    view = memoryview(data)
    members: List[Member] = []
    pos = 0
    while pos < len(data):
        found = _inflate(view, pos)
        if found is None:
            break
        line, end = found
        try:
            moment, digest, raw = parse_line(line)
        except (ValueError, RecursionError, StoreError):
            break
        members.append(Member(n=len(members) + 1, offset=pos, length=end - pos, fetched_at=moment,
                              sha256=digest, raw=raw))
        pos = end
    return Scan(members=tuple(members), good_end=pos, size=len(data))


def scan_file(path: PathLike) -> Scan:
    """Geçmiş dosyasını okur (`scan_bytes`). Dosya yoksa boş bir tarama; okunamıyorsa StoreError."""
    try:
        data = files.read_bytes(path)
    except PayloadMissing:
        return Scan(members=(), good_end=0, size=0)
    return scan_bytes(data)


def _size(path: str) -> Optional[int]:
    try:
        return os.stat(path).st_size
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StoreError.from_exception(exc, path, reading=True) from exc


def append(path: PathLike, member: bytes, *, known: Optional[Tuple[int, int]] = None) -> Tuple[int, int, int]:
    """
    Üyeyi geçmiş dosyasının sonuna ekler ve (n, offset, length) döndürür. Katalogun yazma kilidi altında çağrılır.

    Dosya önce taranır; son sağlam üyeden sonra bayt varsa (yarım kalmış bir ekleme) dosya o noktaya
    kısaltılır. known=(üye sayısı, bittiği bayt): katalogun bildiği son hal. Dosyanın boyutu tam olarak
    oradaysa tarama atlanır. Dosya `open(..., "ab")` ile açılır: izni umask'e uyar (karar S12).
    """
    target = os.fspath(path)
    size = _size(target)
    if size is None:
        count, end = 0, 0
    elif known is not None and known[1] == size and known[0] >= 0:
        count, end = known
    else:
        scan = scan_file(target)
        count, end = len(scan.members), scan.good_end
        if scan.torn:
            logger.warning(f"History file {target}: {scan.size - scan.good_end} bytes after the last complete "
                           "snapshot are removed before the next append")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if size is not None and size != end:
            with open(target, "r+b") as f:
                f.truncate(end)
        with open(target, "ab") as f:
            f.write(member)
            f.flush()
            if files.durability_full():
                os.fsync(f.fileno())
    except OSError as exc:
        raise StoreError.from_exception(exc, target) from exc
    return count + 1, end, len(member)


def history_files(data_dir: PathLike, directory: str) -> List[Tuple[str, str, str]]:
    """
    Varlık dizinindeki geçmiş dosyaları: (key, sub, gerçek yol), sıralı. Adı geçerli bir dilim adına
    çevrilemeyen girdi atlanır. Dizin okunamıyorsa StoreError.
    """
    root = layout.resolve(data_dir, f"{directory}/{layout.HISTORY_DIR_NAME}")
    found: List[Tuple[str, str, str]] = []
    try:
        keys = sorted(os.listdir(root))
    except (FileNotFoundError, NotADirectoryError):
        return found
    except OSError as exc:
        raise StoreError.from_exception(exc, root, reading=True) from exc
    for key in keys:
        key_dir = os.path.join(root, key)
        try:
            layout.validate_key(key)
            names = sorted(os.listdir(key_dir))
        except (LayoutError, NotADirectoryError):
            continue
        except OSError as exc:
            raise StoreError.from_exception(exc, key_dir, reading=True) from exc
        for name in names:
            if not name.endswith(layout.HISTORY_SUFFIX):
                continue
            stem = name[: -len(layout.HISTORY_SUFFIX)]
            try:
                sub = "" if stem == _NO_SUB else layout.validate_sub(stem)
            except LayoutError:
                continue
            path = os.path.join(key_dir, name)
            if os.path.isfile(path):
                found.append((key, sub, path))
    return found


def history_rows(data_dir: PathLike, kind: str, entity_id: int, directory: str) -> List[Row]:
    """
    Varlığın geçmiş dosyalarından `slice_history` satırları (bölüm 3.4, adım 7): sağlam her üye için bir satır.
    Okunamayan dosya atlanır (satırı olmaz); yarım kuyruk yok sayılır.
    """
    rows: List[Row] = []
    try:
        found = history_files(data_dir, directory)
    except StoreError:
        return rows
    for key, sub, path in found:
        try:
            scan = scan_file(path)
        except StoreError:
            continue
        rows.extend(member_row(kind, entity_id, key, sub, member) for member in scan.members)
    return rows


def member_row(kind: str, entity_id: int, key: str, sub: str, member: Member) -> Row:
    return {"kind": kind, "entity_id": entity_id, "key": key, "sub": sub, "n": member.n,
            "fetched_at": derive.epoch_seconds(member.fetched_at), "sha256": member.sha256,
            "offset": member.offset, "length": member.length}


def mark_of(scan: Scan, previous: Optional[HistoryMark]) -> Optional[HistoryMark]:
    """Taramaya göre manifestin `history` alanı (bilinmeyen alanları korunur); sağlam üye yoksa None."""
    if not scan.members:
        return None
    return HistoryMark(count=len(scan.members), last_sha256=scan.members[-1].sha256,
                       extra=dict(previous.extra) if previous is not None else {})


def read_member(path: PathLike, offset: int, length: int) -> Tuple[datetime, str, bytes]:
    """
    `offset` / `length` aralığındaki üyeyi okur ve açar: (zaman, özet, yük baytları). Dosya yoksa
    PayloadMissing; aralık tam bir üye değilse ya da satır geçersizse PayloadCorrupt.
    """
    target = os.fspath(path)
    try:
        with open(target, "rb") as f:
            f.seek(offset)
            data = f.read(length)
    except FileNotFoundError as exc:
        raise PayloadMissing(f"Geçmiş dosyası bulunamadı: {target}", path=target) from exc
    except OSError as exc:
        raise StoreError.from_exception(exc, target, reading=True) from exc
    return decode_member(data, target)


def decode_member(data: bytes, path: str) -> Tuple[datetime, str, bytes]:
    """Tek bir üyenin baytları → (zaman, özet, yük baytları); tam bir üye ya da geçerli bir satır değilse PayloadCorrupt."""
    found = _inflate(memoryview(data), 0) if data else None
    if found is None or found[1] != len(data):
        raise PayloadCorrupt(f"Geçmiş dosyasında bu aralıkta tam bir üye yok: {path}", path=path,
                             detail="not a complete gzip member")
    try:
        return parse_line(found[0])
    except (ValueError, RecursionError, StoreError) as exc:
        raise PayloadCorrupt(f"Geçmiş satırı geçersiz ({exc}): {path}", path=path, detail=str(exc)) from exc


# --- API ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SnapshotInfo:
    """Bir anlık görüntünün katalogdaki kaydı (`HistoryStore.index`). `fetched_at` tam saniyedir (katalog)."""

    n: int
    fetched_at: datetime
    sha256: str


@dataclass(frozen=True)
class Snapshot:
    """Bir anlık görüntü. `fetched_at` geçmiş satırındaki zamandır; `payload` ayrıştırılmış yük ya da (raw=True) baytlar."""

    n: int
    fetched_at: datetime
    sha256: str
    payload: Any


def _check_ref(ref: Any) -> Ref:
    if not isinstance(ref, Ref):
        raise ValueError(f"ref: expected a Ref, got {type(ref).__name__}")
    return ref


def _check_slice(key: Any, sub: Any) -> Tuple[str, str]:
    layout.slice_name(key, sub)  # LayoutError
    return key, sub


def entity_directory(conn: "sqlite3.Connection", kind: str, entity_id: int,
                     tournament_id: Optional[int] = None) -> Optional[str]:
    """Varlık dizini (DATA_DIR'e göre). Turnuvası verilmeyen sezonunki katalogdan bulunur; bilinmiyorsa None."""
    if kind == "season" and tournament_id is None:
        row = conn.execute("SELECT tournament_id FROM seasons WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            return None
        tournament_id = int(row[0])
    return layout.entity_dir(kind, entity_id, tournament_id)


class HistoryStore:
    """
    Dilim geçmişinin API'si (`Store.history`): sürümlenen dilimlerin anlık görüntüleri. Okumalar katalogdaki
    `slice_history` satırlarından yer bulur ve yalnızca istenen üyeleri açar; `prune` dosyaları yeniden yazar.
    """

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._catalog = store._catalog
        self._data_dir = str(store.data_dir)

    def _rows(self, ref: Ref, key: str, sub: str, *, since: Optional[int] = None,
              n: Optional[int] = None) -> Tuple[Optional[str], List[Any]]:
        self._store._require_open()
        assert self._catalog is not None
        sql = ("SELECT n, fetched_at, sha256, offset, length FROM slice_history "
               "WHERE kind = ? AND entity_id = ? AND key = ? AND sub = ?")
        params: List[Any] = [ref.kind, ref.id, key, sub]
        if since is not None:
            sql += " AND fetched_at >= ?"
            params.append(since)
        if n is not None:
            sql += " AND n = ?"
            params.append(n)
        with self._catalog.read() as conn:
            rows = conn.execute(sql + " ORDER BY n", params).fetchall()
            directory = entity_directory(conn, ref.kind, ref.id, ref.tournament_id) if rows else None
        return directory, rows

    def _path(self, directory: str, key: str, sub: str) -> str:
        return layout.resolve(self._data_dir, layout.history_path(directory, key, sub))

    def index(self, ref: Ref, key: str, sub: str = "") -> List[SnapshotInfo]:
        """Dilimin anlık görüntüleri, sırayla (katalogdan; dosya okunmaz). Geçmişi yoksa boş liste."""
        _check_ref(ref)
        _check_slice(key, sub)
        _directory, rows = self._rows(ref, key, sub)
        return [SnapshotInfo(n=int(r[0]), fetched_at=datetime.fromtimestamp(int(r[1]), timezone.utc),
                             sha256=str(r[2])) for r in rows]

    def snapshots(self, ref: Ref, key: str, sub: str = "", *, since: Optional[float] = None,
                  raw: bool = False) -> Iterator[Snapshot]:
        """
        Dilimin anlık görüntüleri, sırayla. since: yalnızca bu andan (epoch saniye) sonra ya da o anda alınanlar.
        raw=True: yük ayrıştırılmaz, kurallı baytları döner. Dosya bir kez okunur. Katalog ile dosya uyuşmuyorsa
        (o sırada `prune` dosyayı yeniden yazmış olabilir) satırlar ve dosya bir kez daha okunur; yine
        uyuşmazsa PayloadCorrupt.
        """
        _check_ref(ref)
        _check_slice(key, sub)
        floor = None
        if since is not None:
            if isinstance(since, bool) or not isinstance(since, (int, float)):
                raise ValueError(f"since: expected epoch seconds, got {since!r}")
            floor = derive.epoch_seconds(since)
        found = self._read_all(ref, key, sub, floor, retry=True)
        for n, moment, digest, data in found:
            if since is not None and moment.timestamp() < since:
                continue
            yield Snapshot(n=n, fetched_at=moment, sha256=digest, payload=data if raw else json.loads(data))

    def _read_all(self, ref: Ref, key: str, sub: str, floor: Optional[int],
                  retry: bool) -> List[Tuple[int, datetime, str, bytes]]:
        directory, rows = self._rows(ref, key, sub, since=floor)
        if not rows or directory is None:
            return []
        path = self._path(directory, key, sub)
        try:
            data = files.read_bytes(path)
            out = []
            for n, _at, digest, offset, length in rows:
                moment, found, raw = decode_member(data[offset:offset + length], path)
                if found != digest:
                    raise PayloadCorrupt(f"Geçmiş üyesinin özeti katalogdakinden farklı: {path}", path=path,
                                         detail=f"n={n}")
                out.append((int(n), moment, found, raw))
            return out
        except (PayloadMissing, PayloadCorrupt):
            if not retry:
                raise
        return self._read_all(ref, key, sub, floor, retry=False)

    def snapshot(self, ref: Ref, key: str, sub: str, n: int, *, raw: bool = False) -> Optional[Snapshot]:
        """`n`. anlık görüntü (1'den başlar); katalogda yoksa None. Yalnızca o üyenin bayt aralığı okunur."""
        _check_ref(ref)
        _check_slice(key, sub)
        check_int(n, "n", minimum=1)
        for attempt in (1, 2):
            directory, rows = self._rows(ref, key, sub, n=n)
            if not rows or directory is None:
                return None
            _n, _at, digest, offset, length = rows[0]
            try:
                moment, found, data = read_member(self._path(directory, key, sub), int(offset), int(length))
                if found != digest:
                    raise PayloadCorrupt(f"Geçmiş üyesinin özeti katalogdakinden farklı (n={n})",
                                         detail=f"n={n}")
            except (PayloadMissing, PayloadCorrupt):
                if attempt == 2:
                    raise
                continue
            return Snapshot(n=n, fetched_at=moment, sha256=found, payload=data if raw else json.loads(data))
        return None  # pragma: no cover - döngü her yolda döner

    def prune(self, ref: Optional[Ref] = None, *, older_than: float) -> int:
        """
        `older_than`dan (epoch saniye) önce alınmış anlık görüntüleri siler ve silinen sayısını döndürür. Her
        dilimin en yeni anlık görüntüsü, ne kadar eski olursa olsun kalır. Dosyalar yeniden yazılır (üyeler
        olduğu gibi kopyalanır); kalanların `n`'i yeniden 1'den başlar. Manifest ve katalog aynı kritik bölümde
        güncellenir (bölüm 6.2). ref=None: bütün maçlar.

        Bu süreç `writer` kilidini tutmalıdır (bölüm 6.1); tutmuyorsa StoreError. Maç olmayan varlıkların
        geçmişi henüz yazılmadığından yalnızca maçlar budanır.
        """
        if ref is not None:
            _check_ref(ref)
        if isinstance(older_than, bool) or not isinstance(older_than, (int, float)):
            raise ValueError(f"older_than: expected epoch seconds, got {older_than!r}")
        events = self._store.events
        events._writable()
        if not self._store._leases.held_here(LEASE_WRITER):
            raise StoreError(f"Geçmişi budamak için `{LEASE_WRITER}` kilidi tutulmalı: {self._data_dir}",
                             path=self._data_dir, detail="writer lease not held")
        if ref is not None and ref.kind != "event":
            return 0
        assert self._catalog is not None
        sql = "SELECT DISTINCT entity_id FROM slice_history WHERE kind = 'event' AND fetched_at < ?"
        params: List[Any] = [older_than]
        if ref is not None:
            sql += " AND entity_id = ?"
            params.append(ref.id)
        with self._catalog.read() as conn:
            ids = [int(row[0]) for row in conn.execute(sql + " ORDER BY entity_id", params)]
        removed = 0
        for event_id in ids:
            removed += events._entity_write(
                event_id, lambda write, target=event_id: prune_event(events, write, target, float(older_than)))
        if removed:
            cutoff = timestamp(datetime.fromtimestamp(older_than, timezone.utc))
            logger.info(f"History pruned: {removed} snapshots older than {cutoff} removed")
        return removed


def prune_event(events: Any, write: Any, event_id: int, older_than: float) -> int:
    """
    `prune`un bir maç için gövdesi; `EventStore._entity_write` içinde, yazma kilidi altında çalışır. Silinen
    anlık görüntü sayısını döndürür.
    """
    from sofascore_scraper.store import indexer  # döngüsel içe aktarma: dizinleyici bu modülü kullanır

    data_dir = events._data_dir
    if write.recover:
        indexer.heal_v3_event(data_dir, event_id)
    directory = layout.event_dir(event_id)
    manifest_file = layout.resolve(data_dir, layout.manifest_path(directory))
    try:
        found = manifest_mod.read_manifest(manifest_file)
    except PayloadMissing:
        return 0
    removed = 0
    rewrote = False
    for key, sub, path in history_files(data_dir, directory):
        data = files.read_bytes(path)
        scan = scan_bytes(data)
        members = scan.members
        if not members:
            continue
        kept = [m for m in members[:-1] if m.fetched_at.timestamp() >= older_than] + [members[-1]]
        if len(kept) == len(members) and not scan.torn:
            continue
        write.touched = True
        files.write_bytes(path, b"".join(data[m.offset:m.offset + m.length] for m in kept))
        removed += len(members) - len(kept)
        rewrote = True
        entry = found.slices.get(layout.slice_name(key, sub))
        if entry is not None:
            entry.history = HistoryMark(count=len(kept), last_sha256=kept[-1].sha256,
                                        extra=dict(entry.history.extra) if entry.history is not None else {})
    if not rewrote:
        return 0
    manifest_mod.write_manifest(manifest_file, found)
    events._checkpoint(STEP_MANIFEST)
    events._index(event_id)
    return removed


__all__ = [
    "HistoryStore",
    "Snapshot",
    "SnapshotInfo",
    "Member",
    "Scan",
    "encode_member",
    "parse_line",
    "scan_bytes",
    "scan_file",
    "append",
    "history_files",
    "history_rows",
    "member_row",
    "mark_of",
    "read_member",
    "decode_member",
    "entity_directory",
    "prune_event",
]
