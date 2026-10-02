"""
Değişiklik günlüğü: dosyaları, katalogdaki dizini ve `Store.changes` API'si (docs/design/01-storage.md,
bölüm 3.4 adım 6, 5.2 ve 8.5).

Günlüğün kendisi dosyadır; `changes` tablosu o dosyaların dizinidir ve dosyalardan yeniden kurulur. İki
kaynak vardır:

  * Eski düzendeki `score_changes.jsonl`: satırın sıra numarası (`seq`) dosyadaki satır numarasıdır (1'den
    başlar). Boş ve okunamayan satırlar da numara harcar, böylece bir satır bozulduğunda ötekilerin numarası
    kaymaz ve yeniden kurma aynı numaraları verir.
  * v3'ün aylık parçaları `changes/<yyyy>-<aa>.jsonl` (satırın `ts_utc` ayı): satır başına bir JSON nesnesi,
    bayt olarak ve LF satır sonuyla yazılır. `seq` satırın içindedir; yazan onu yazma kilidi altında
    `max(seq) + 1` olarak verir (`append_row`), yeniden kurma satırdakini okur.
  * Eski dosyanın taşınmış kopyası `changes/0000-legacy.jsonl` (`migrate`, docs/design/01-storage.md 5.4;
    `copy_legacy`): eski dosyanın baytlarının aynısıdır, bu yüzden sıra numarası yine satır numarasıdır. Kopya
    varsa eski dosyanın yerini alır: eski dosya artık dizinlenmez (aynı satırlar iki kez sayılmasın) ve
    `migrate --delete-legacy` onu kopyası doğrulandıktan sonra siler.

Dizinleme sırası: önce eski dosya (ya da kopyası), sonra parçalar ada göre, her dosyada satır sırasıyla. Aynı `seq` iki
satırda geçerse bu sırada önce gelen geçerlidir, öteki `duplicate_seq` sorunu olarak bildirilir ve dizine
girmez. Bu yalnızca v3 satırları varken eski dosyaya satır eklenirse olur (2.x ve 3.x yazarları aynı dizinde;
desteklenmez) ve doğrulamanın I7 kuralı bunu yakalar.

Dizin artımlıdır (`sync`): her dosyanın dizinlenmiş uzunluğu `meta` tablosunda durur (`changes_indexed`);
dosya uzadıysa yalnızca kuyruğu okunur. Satır eklenip katalog işlemi tamamlanmadan süreç ölürse bir sonraki
açılış ya da ekleme kuyruğu dizinler, böylece aynı `seq` iki kez verilmez. Satır sonu olmayan son satır
yarım kalmış bir yazmadır: atlanır ve bildirilir; bir sonraki ekleme önce o satırı kapatır ve yeni satıra
başlar. Dosya uzamak dışında bir biçimde değiştiyse (kısaldı, baştan yazıldı, silindi) bütün günlük baştan
dizinlenir.

`ChangeLog` (`Store.changes`) günlüğün API'sidir (bölüm 2.3): `list` ve `last_seq` dizinden okur, `append`
bir satır ekler (olağan yol `EventStore.put(on_event_change=...)`'tir).

Dizine giremeyen satır (tarihi ya da maç kimliği olmayan, v3'te `seq`'i olmayan) atlanır ve bildirilir.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from src.store import files, layout, legacy
from src.store.catalog import Catalog
from src.store.entities import storable
from src.store.errors import StoreError
from src.store.events import check_int
from src.store.legacy import LegacyProblem, LegacyReader

if TYPE_CHECKING:
    from src.store.api import Store

PathLike = Union[str, "os.PathLike[str]"]
Row = Dict[str, Any]

LEGACY_SEGMENT = legacy.CHANGES_FILE  # `changes.segment`: satırın geldiği dosya, DATA_DIR'e göre
LEGACY_COPY = f"{layout.CHANGES_DIR}/0000-legacy.jsonl"  # eski dosyanın `migrate` ile taşınmış kopyası
# Sıra numarası satır numarası olan dosyalar (satırın içinde `seq` yok): eski dosya ve kopyası
NUMBERED_SEGMENTS: Tuple[str, ...] = (LEGACY_SEGMENT, LEGACY_COPY)
META_INDEXED = "changes_indexed"  # meta: dosya → dizinlenmiş uzunluğu (JSON; bkz. `_Mark`)
PROBLEM_SEQ = "duplicate_seq"  # aynı sıra numarası daha önce gelen bir satırda

_SEGMENT_RE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\.jsonl")
_TAIL_BYTES = 64  # dizinlenmiş kısmın sonundan özeti tutulan bayt sayısı ("dosya yalnızca uzadı mı")
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1


def _plain_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if _INT64_MIN <= value <= _INT64_MAX else None


def _epoch(value: Any) -> Optional[int]:
    """`ts_utc` (ISO 8601; saat dilimi yoksa UTC) → epoch saniye; ayrıştırılamıyorsa None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return math.floor(parsed.timestamp())
    except (OverflowError, OSError):
        return None


def change_row(seq: int, row: Mapping[str, Any], line: str, segment: str) -> Optional[Row]:
    """
    Günlüğün bir satırından (`src/refresh.change_row` biçimi) `changes` tablosu satırı. `ts_utc` ya da
    `event_id` kullanılamıyorsa None (iki sütun da NOT NULL). `fields` değişen alan adlarıdır, satırdaki
    sırayla ve virgülle ayrılmış; `row_json` satırın dosyada yazıldığı halidir.
    """
    ts = _epoch(row.get("ts_utc"))
    event_id = _plain_int(row.get("event_id"))
    if ts is None or event_id is None:
        return None
    tournament = row.get("tournament")
    changed = row.get("changed")
    sport = row.get("sport")
    return storable({
        "seq": seq,
        "ts": ts,
        "event_id": event_id,
        "sport": sport if isinstance(sport, str) else None,
        "tournament_id": _plain_int(tournament.get("id")) if isinstance(tournament, Mapping) else None,
        "status_regressed": int(bool(row.get("status_regressed"))),
        "fields": ",".join(str(name) for name in changed) if isinstance(changed, Mapping) else "",
        "row_json": line,
        "segment": segment,
    })


# --- dosyalar -----------------------------------------------------------------------------------------

def is_segment(name: str) -> bool:
    """Dosya adı bir v3 günlük parçasının adı mı (`<yyyy>-<aa>.jsonl`)."""
    return _SEGMENT_RE.fullmatch(name) is not None


def is_numbered(segment: str) -> bool:
    """Dosyanın satırlarının sıra numarası satır numarası mı (eski dosya ve taşınmış kopyası)."""
    return segment in NUMBERED_SEGMENTS


def segments(data_dir: PathLike, problems: Optional[List[LegacyProblem]] = None) -> List[str]:
    """
    Diskteki günlük dosyaları (DATA_DIR'e göre), dizinleme sırasıyla: eski düzen dosyası (varsa), sonra
    `changes/` altındaki aylık parçalar ada göre. Eski dosyanın taşınmış kopyası (`LEGACY_COPY`) varsa eski
    dosyanın yerine o gelir; eski dosya listelenmez. `changes/` altında adı kurala uymayan girdi bildirilir.
    """
    found: List[str] = []
    directory = layout.resolve(data_dir, layout.CHANGES_DIR)
    copy_name = LEGACY_COPY.rsplit("/", 1)[-1]
    try:
        names = sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        names = []
    except OSError as exc:
        raise StoreError.from_exception(exc, directory, reading=True) from exc
    if copy_name in names and os.path.isfile(os.path.join(directory, copy_name)):
        found.append(LEGACY_COPY)
    elif os.path.isfile(layout.resolve(data_dir, LEGACY_SEGMENT)):
        found.append(LEGACY_SEGMENT)
    for name in names:
        if name.startswith(".") or name == copy_name:
            continue  # işletim sisteminin gizli dosyaları; kopya yukarıda
        if is_segment(name) and os.path.isfile(os.path.join(directory, name)):
            found.append(f"{layout.CHANGES_DIR}/{name}")
        elif problems is not None:
            problems.append(LegacyProblem(f"{layout.CHANGES_DIR}/{name}", legacy.PROBLEM_NAME,
                                          "değişiklik günlüğü parçası olarak tanınmadı"))
    return found


def _order(segment: str) -> Tuple[int, str]:
    """Dizinleme sırası: eski dosya (ya da kopyası) önce, sonra parçalar ada göre."""
    return (0, "") if is_numbered(segment) else (1, segment)


@dataclass
class _Mark:
    """Bir günlük dosyasının dizinlenmiş kısmı (`meta.changes_indexed` içinde durur)."""

    offset: int  # dizinlenmiş son tam satırın sonu (bayt)
    lines: int  # o noktaya kadar tüketilen satır sayısı (eski dosyada sıra numarası buradan sürer)
    size: int  # dizinlendiği andaki dosya boyutu ve zamanı: ikisi de aynıysa dosya değişmemiştir
    mtime_ns: int
    tail: str  # `offset`ten önceki son baytların özeti


def _load_marks(cat: Catalog) -> Dict[str, _Mark]:
    raw = cat.get_meta(META_INDEXED)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(segment): _Mark(int(v[0]), int(v[1]), int(v[2]), int(v[3]), str(v[4]))
                for segment, v in data.items()}
    except (ValueError, TypeError, IndexError, AttributeError, KeyError):
        return {}  # okunamayan kayıt: günlük baştan dizinlenir


def _save_marks(cat: Catalog, marks: Mapping[str, _Mark]) -> None:
    data = {segment: [m.offset, m.lines, m.size, m.mtime_ns, m.tail] for segment, m in marks.items()}
    cat.set_meta(META_INDEXED, json.dumps(data, sort_keys=True, separators=(",", ":")))


def _stat(path: str) -> Optional[os.stat_result]:
    try:
        return os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise StoreError.from_exception(exc, path, reading=True) from exc


def _read(path: str, start: int, end: Optional[int] = None) -> Optional[Tuple[bytes, os.stat_result]]:
    """Dosyanın [start, end) aralığı (end=None: sonuna kadar) ve o andaki durumu; dosya yoksa None."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            stop = st.st_size if end is None else min(end, st.st_size)
            f.seek(min(start, stop))
            return f.read(max(0, stop - start)), st
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise StoreError.from_exception(exc, path, reading=True) from exc


def _tail_digest(path: str, offset: int) -> str:
    found = _read(path, max(0, offset - _TAIL_BYTES), offset)
    return hashlib.sha256(found[0] if found else b"").hexdigest()


@dataclass(frozen=True)
class SegmentLine:
    """Bir günlük dosyasının dizine girebilen bir satırı."""

    number: int  # dosyadaki satır numarası (1'den başlar)
    seq: int
    line: str  # satır, satır sonu olmadan
    row: Row  # `changes` tablosu satırı


def parse_lines(data: bytes, segment: str, *, first_line: int = 1,
                problems: Optional[List[LegacyProblem]] = None) -> Tuple[List[SegmentLine], int, int]:
    """
    Bir günlük dosyasının `first_line` numaralı satırdan başlayan baytlarını ayrıştırır. Döndürdükleri:
    dizine girebilen satırlar, tüketilen bayt sayısı (son tam satırın sonu) ve tüketilen satır sayısı.

    Eski dosyada (ve kopyasında) `seq` satır numarasıdır; v3 parçasında satırın içindeki `seq` alanıdır (pozitif tam sayı
    olmalı). Boş satır atlanır; ayrıştırılamayan, nesne olmayan ya da dizine giremeyen satır bildirilir.
    Satır sonu olmayan son parça yarım kalmış bir yazmadır: tüketilmez ve bildirilir (bölüm 8.5).
    """
    numbered = is_numbered(segment)
    parts = data.split(b"\n")
    torn = parts.pop()  # son "\n"den sonrası: tam dosyada boş
    notes: List[LegacyProblem] = problems if problems is not None else []
    if torn.strip():
        notes.append(LegacyProblem(segment, legacy.PROBLEM_TORN, f"satır {first_line + len(parts)}"))
    out: List[SegmentLine] = []
    for number, raw in enumerate(parts, start=first_line):
        if not raw.strip():
            continue
        try:
            line = raw.decode("utf-8").rstrip("\r")
            parsed = json.loads(line)
        except (ValueError, RecursionError) as exc:  # JSONDecodeError ve UnicodeDecodeError birer ValueError'dır
            notes.append(LegacyProblem(segment, legacy.PROBLEM_CORRUPT, f"satır {number}: {exc}"))
            continue
        if not isinstance(parsed, dict):
            notes.append(LegacyProblem(segment, legacy.PROBLEM_MALFORMED, f"satır {number}: JSON nesnesi değil"))
            continue
        seq = number if numbered else _plain_int(parsed.get("seq"))
        if seq is None or seq < 1:
            notes.append(LegacyProblem(segment, legacy.PROBLEM_MALFORMED, f"satır {number}: seq yok"))
            continue
        row = change_row(seq, parsed, line, segment)
        if row is None:
            notes.append(LegacyProblem(segment, legacy.PROBLEM_MALFORMED,
                                       f"satır {number}: ts_utc ya da event_id yok"))
            continue
        out.append(SegmentLine(number, seq, line, row))
    return out, len(data) - len(torn), len(parts)


def read_segment(data_dir: PathLike, segment: str,
                 problems: Optional[List[LegacyProblem]] = None) -> List[SegmentLine]:
    """Bir günlük dosyasının dizine girebilen bütün satırları (doğrulama ve döküm için); dosya yoksa boş liste."""
    found = _read(layout.resolve(data_dir, segment), 0)
    return parse_lines(found[0], segment, problems=problems)[0] if found is not None else []


# --- dizin --------------------------------------------------------------------------------------------

def _chunks(values: List[int], size: int = 500) -> Iterator[List[int]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _insert(cat: Catalog, segment: str, lines: List[SegmentLine],
            problems: Optional[List[LegacyProblem]]) -> None:
    """
    Satırları dizine yazar. Sıra numarası başka bir satırda duruyorsa dizinleme sırasında önce gelen
    geçerlidir (modül açıklaması): öteki bildirilir. Böylece kuyruğu dizinlemek ile baştan dizinlemek aynı
    satırları verir.
    """
    if not lines:
        return
    conn = cat.connection()
    holders: Dict[int, str] = {}
    for chunk in _chunks(sorted({line.seq for line in lines})):
        marks = ", ".join("?" for _ in chunk)
        holders.update((int(row[0]), str(row[1])) for row in conn.execute(
            f"SELECT seq, segment FROM changes WHERE seq IN ({marks})", chunk))
    accepted: Dict[int, Row] = {}
    for line in lines:
        holder = holders.get(line.seq)
        if holder is not None and _order(holder) <= _order(segment):
            if problems is not None:
                problems.append(LegacyProblem(segment, PROBLEM_SEQ,
                                              f"satır {line.number}: seq {line.seq} zaten {holder} içinde"))
            continue
        if holder is not None and problems is not None:
            problems.append(LegacyProblem(holder, PROBLEM_SEQ, f"seq {line.seq} zaten {segment} içinde"))
        holders[line.seq] = segment
        accepted[line.seq] = line.row
    cat.upsert("changes", accepted.values())


def _count(cat: Catalog) -> int:
    return int(cat.connection().execute("SELECT count(*) FROM changes").fetchone()[0])


def sync(cat: Catalog, data_dir: PathLike, problems: Optional[List[LegacyProblem]] = None, *,
         full: bool = False) -> Optional[int]:
    """
    Günlük dosyalarını dizinle eşitler; `Catalog.write()` bloğunun içinde çağrılır. Bir şey değiştiyse
    dizindeki toplam satır sayısını, değişmediyse None döndürür.

    Her dosyanın boyutu ve zamanı `meta`daki kayıtla karşılaştırılır; ikisi de aynıysa dosya okunmaz. Uzamış
    dosyanın yalnızca kuyruğu okunur (eski dosyada satır numaraları kaldığı yerden sürer). Yeni bir dosya
    baştan okunur. Bir dosya başka biçimde değiştiyse (kısaldı, baştan yazıldı, silindi) ya da dizinde satırı
    olan bir dosyanın kaydı yoksa dizin boşaltılır ve bütün dosyalar baştan dizinlenir.

    full=True: kayıtlara bakılmaz, her şey baştan dizinlenir (yeniden kurma, derin uzlaştırma). Diskte dosya
    da dizinde satır da yoksa None döner.
    """
    conn = cat.connection()
    found = segments(data_dir, problems)
    marks: Dict[str, _Mark] = {} if full else _load_marks(cat)
    plan: List[Tuple[str, int, int]] = []  # dosya, başlangıç baytı, ilk satırın numarası
    restart = full or any(segment not in found for segment in marks)
    if not restart:
        for segment in found:
            path = layout.resolve(data_dir, segment)
            mark, st = marks.get(segment), _stat(path)
            if st is None:  # listelendikten sonra silindi
                if mark is None:
                    continue
                restart = True
            elif mark is None:
                # Kaydı olmayan dosya yenidir. Dizinde yine de satırı varsa (bu kaydı tutmayan bir sürümün kurduğu
                # katalog) neresine kadar dizinlendiği bilinmez: baştan başlanır
                restart = conn.execute("SELECT 1 FROM changes WHERE segment = ? LIMIT 1", (segment,)).fetchone() is not None
                if not restart:
                    plan.append((segment, 0, 1))
            elif (st.st_size, st.st_mtime_ns) == (mark.size, mark.mtime_ns):
                continue
            elif st.st_size >= mark.offset and _tail_digest(path, mark.offset) == mark.tail:
                plan.append((segment, mark.offset, mark.lines + 1))
            else:
                restart = True
            if restart:
                break
    if restart:
        removed = conn.execute("DELETE FROM changes").rowcount
        had_marks, marks = bool(marks), {}
        plan = [(segment, 0, 1) for segment in found]
        if not plan and not removed and not had_marks:
            return None
    elif not plan:
        return None
    for segment, start, first_line in plan:
        path = layout.resolve(data_dir, segment)
        read = _read(path, start)
        if read is None:
            marks.pop(segment, None)
            continue
        data, st = read
        lines, consumed, count = parse_lines(data, segment, first_line=first_line, problems=problems)
        _insert(cat, segment, lines, problems)
        offset = start + consumed
        marks[segment] = _Mark(offset, first_line - 1 + count, st.st_size, st.st_mtime_ns,
                               _tail_digest(path, offset))
    _save_marks(cat, marks)
    return _count(cat)


def index_all(cat: Catalog, reader: LegacyReader, problems: Optional[List[LegacyProblem]] = None) -> int:
    """
    Bütün günlük dosyalarını baştan dizinler (bölüm 3.4, adım 6): önce eski düzen dosyası, sonra v3 parçaları
    ada göre. `Catalog.write()` bloğunun içinde çağrılır; dizindeki satır sayısını döndürür.
    """
    return sync(cat, reader.data_dir, problems, full=True) or 0


# --- eski dosyanın taşınması (migrate, bölüm 5.4) -----------------------------------------------------

COPY_NONE = "none"  # eski dosya yok
COPY_CREATED = "created"  # kopya yazıldı
COPY_EXTENDED = "extended"  # eski dosya kopyadan sonra uzamıştı (2.x satır ekledi): kopya yeniden yazıldı
COPY_UNCHANGED = "unchanged"  # kopya eski dosyayla aynı
COPY_CONFLICT = "conflict"  # kopya eski dosyanın başı değil: hiçbir şey yazılmadı


def legacy_copy_state(data_dir: PathLike) -> str:
    """
    Eski dosya ile taşınmış kopyası arasındaki durum, hiçbir şey yazmadan: `copy_legacy`'nin yapacağı iş
    (`COPY_CREATED` yazılacak, `COPY_EXTENDED` yeniden yazılacak, `COPY_UNCHANGED`, `COPY_CONFLICT`,
    `COPY_NONE`).
    """
    source = _read(layout.resolve(data_dir, LEGACY_SEGMENT), 0)
    if source is None:
        return COPY_NONE
    copied = _read(layout.resolve(data_dir, LEGACY_COPY), 0)
    if copied is None:
        return COPY_CREATED
    if copied[0] == source[0]:
        return COPY_UNCHANGED
    return COPY_EXTENDED if source[0].startswith(copied[0]) else COPY_CONFLICT


def copy_legacy(cat: Catalog, data_dir: PathLike, problems: Optional[List[LegacyProblem]] = None) -> str:
    """
    Eski dosyayı (`score_changes.jsonl`) baytı baytına `changes/0000-legacy.jsonl`e kopyalar ve günlüğü
    yeniden dizinler; `Catalog.write()` bloğunun içinde çağrılır. Satır numaraları, dolayısıyla sıra numaraları
    değişmez. Kopya atomik yazılır ve geri okunarak doğrulanır (uyuşmazlıkta silinir, StoreError). Eski dosya
    yerinde kalır; kopya varken dizinlenmez (`segments`). Durumu döndürür (`legacy_copy_state`): kopya eski
    dosyanın başı değilse hiçbir şey yazılmaz (`COPY_CONFLICT`).
    """
    state = legacy_copy_state(data_dir)
    if state in (COPY_CREATED, COPY_EXTENDED):
        source = _read(layout.resolve(data_dir, LEGACY_SEGMENT), 0)
        assert source is not None
        target = layout.resolve(data_dir, LEGACY_COPY)
        files.write_bytes(target, source[0])
        if files.read_bytes(target) != source[0]:
            files.remove(target)
            raise StoreError(f"Change log copy could not be verified: {target}", path=target,
                             detail="read-back mismatch")
    sync(cat, data_dir, problems)
    return state


def legacy_copy_verified(data_dir: PathLike) -> bool:
    """Taşınmış kopya var ve eski dosyanın baytlarının aynısı mı (eski dosya silinebilir mi)."""
    return legacy_copy_state(data_dir) == COPY_UNCHANGED


# --- ekleme -------------------------------------------------------------------------------------------

def _ends_torn(path: str) -> bool:
    """Dosya var, boş değil ve son baytı satır sonu değil: yarım kalmış bir satırla bitiyor."""
    st = _stat(path)
    if st is None or st.st_size == 0:
        return False
    found = _read(path, st.st_size - 1)
    return found is not None and found[0] not in (b"", b"\n")


def _append_bytes(path: str, data: bytes) -> None:
    """Dosyanın sonuna ekler ve tamponu boşaltır. Dosya `open(..., "ab")` ile açılır: izni umask'e uyar (karar S12)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "ab") as f:
            f.write(data)
            f.flush()
            if files.durability_full():
                os.fsync(f.fileno())
    except OSError as exc:
        raise StoreError.from_exception(exc, path) from exc


def append_row(cat: Catalog, data_dir: PathLike, row: Mapping[str, Any]) -> int:
    """
    Günlüğe bir satır ekler ve sıra numarasını döndürür; `Catalog.write()` bloğunun içinde çağrılır (yazma
    kilidi sıra numarasının süreçler arası kilididir).

    row: `src/refresh.change_row` biçiminde bir nesne; `ts_utc` (ISO 8601) ve `event_id` (tam sayı) zorunludur
    (ValueError). Satır, `ts_utc`'nin ayına ait parçaya `seq` alanı eklenerek yazılır: önce dosyaya (tampon
    boşaltılır), sonra dizine. Arada süreç ölürse bir sonraki açılış ya da ekleme kuyruğu dizinler; bu yüzden
    numara verilmeden önce dizin dosyalarla eşitlenir.
    """
    if not isinstance(row, Mapping):
        raise ValueError(f"row: expected a mapping, got {type(row).__name__}")
    ts = _epoch(row.get("ts_utc"))
    if ts is None or _plain_int(row.get("event_id")) is None:
        raise ValueError("row: expected ts_utc (ISO 8601) and event_id (integer)")
    try:
        moment = datetime.fromtimestamp(ts, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"row: ts_utc out of range: {row.get('ts_utc')!r}") from exc
    segment = layout.change_segment(moment.year, moment.month)
    path = layout.resolve(data_dir, segment)

    if _ends_torn(path):
        _append_bytes(path, b"\n")  # yarım satır kapanır; yeni satır kendi satırında başlar
    sync(cat, data_dir)
    conn = cat.connection()
    seq = int(conn.execute("SELECT coalesce(max(seq), 0) FROM changes").fetchone()[0]) + 1

    body = {**row, "seq": seq}
    try:
        line = json.dumps(body, ensure_ascii=False)
        data = line.encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:  # UnicodeEncodeError bir ValueError'dır
        raise StoreError(f"Değişiklik satırı JSON'a çevrilemedi ({exc})", detail=str(exc)) from exc
    indexed = change_row(seq, body, line, segment)
    assert indexed is not None  # ts_utc ve event_id yukarıda denetlendi

    _append_bytes(path, data + b"\n")
    cat.upsert("changes", [indexed])
    marks = _load_marks(cat)
    st = _stat(path)
    if st is not None:
        previous = marks.get(segment)
        marks[segment] = _Mark(st.st_size, (previous.lines if previous else 0) + 1, st.st_size, st.st_mtime_ns,
                               _tail_digest(path, st.st_size))
        _save_marks(cat, marks)
    return seq


# --- API ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChangeRow:
    """
    Değişiklik günlüğünün bir satırı. `row` günlükteki satırın kendisidir (`src/refresh.change_row` biçimi:
    `changed` alan → [eski, yeni]; v3 parçalarından gelen satırda `seq` alanı da vardır); öteki alanlar
    dizinin sütunlarıdır. `ts` epoch saniyedir (UTC).
    """

    seq: int
    ts: int
    event_id: int
    sport: Optional[str]
    tournament_id: Optional[int]
    status_regressed: bool
    fields: Tuple[str, ...]  # değişen alan adları, satırdaki sırayla
    row: Mapping[str, Any]
    segment: str  # satırın geldiği dosya, DATA_DIR'e göre


_CHANGE_SELECT = ("SELECT seq, ts, event_id, sport, tournament_id, status_regressed, fields, row_json, segment "
                  "FROM changes")


def _change(row: Any) -> ChangeRow:
    try:
        parsed = json.loads(row[7])
    except ValueError:
        parsed = None
    return ChangeRow(
        seq=int(row[0]), ts=int(row[1]), event_id=int(row[2]), sport=row[3], tournament_id=row[4],
        status_regressed=bool(row[5]), fields=tuple(name for name in str(row[6]).split(",") if name),
        row=parsed if isinstance(parsed, dict) else {}, segment=str(row[8]),
    )


class ChangeLog:
    """Değişiklik günlüğünün API'si (`Store.changes`); okunan satırlar katalogdaki dizinden gelir."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._catalog = store._catalog

    def append(self, row: Mapping[str, Any]) -> int:
        """
        Günlüğe bir satır ekler ve sıra numarasını (`seq`) döndürür (`append_row`). Olağan yol
        `EventStore.put(on_event_change=...)`'tir: karşılaştırma ve ekleme aynı kritik bölümde yapılır. Kendi
        yazma işlemini açar; açık bir `Catalog.write()` bloğunun içinde çağrılırsa ona katılır. Salt okunur
        depoda StoreError.
        """
        self._store._require_open()
        if self._store.readonly:
            raise StoreError(f"Depo salt okunur açılmış: {self._store.data_dir}", path=str(self._store.data_dir))
        assert self._catalog is not None
        with self._catalog.write():
            return append_row(self._catalog, self._store.data_dir, row)

    def list(self, *, after_seq: int = 0, event_id: Optional[int] = None, since: Optional[float] = None,
             limit: int = 1000) -> List[ChangeRow]:
        """
        Sıra numarası `after_seq`'ten büyük satırlar, sıra numarasıyla; en çok `limit` tane. Tüketici son
        satırın `seq`'ini bir sonraki çağrıya `after_seq` olarak verir. event_id: yalnızca o maçın satırları;
        since: zamanı (epoch saniye) bundan önce olmayan satırlar.
        """
        conditions = ["seq > ?"]
        params: List[Any] = [check_int(after_seq, "after_seq", minimum=0)]
        if event_id is not None:
            conditions.append("event_id = ?")
            params.append(check_int(event_id, "event_id"))
        if since is not None:
            if isinstance(since, bool) or not isinstance(since, (int, float)) or since != since:
                raise ValueError(f"since: expected a number, got {since!r}")
            conditions.append("ts >= ?")
            params.append(since)
        params.append(check_int(limit, "limit", minimum=1))
        self._store._require_open()
        assert self._catalog is not None
        with self._catalog.read() as conn:
            found = conn.execute(
                f"{_CHANGE_SELECT} WHERE {' AND '.join(conditions)} ORDER BY seq LIMIT ?", params).fetchall()
        return [_change(row) for row in found]

    def last_seq(self) -> int:
        """Dizindeki en büyük sıra numarası; günlük boşsa 0."""
        self._store._require_open()
        assert self._catalog is not None
        with self._catalog.read() as conn:
            row = conn.execute("SELECT max(seq) FROM changes").fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0


__all__ = [
    "ChangeRow",
    "ChangeLog",
    "SegmentLine",
    "LEGACY_SEGMENT",
    "LEGACY_COPY",
    "NUMBERED_SEGMENTS",
    "COPY_NONE",
    "COPY_CREATED",
    "COPY_EXTENDED",
    "COPY_UNCHANGED",
    "COPY_CONFLICT",
    "META_INDEXED",
    "PROBLEM_SEQ",
    "change_row",
    "is_segment",
    "is_numbered",
    "legacy_copy_state",
    "copy_legacy",
    "legacy_copy_verified",
    "segments",
    "parse_lines",
    "read_segment",
    "sync",
    "index_all",
    "append_row",
]
