"""
Tutarlılık denetimi: katalog, dosyaların söylediğini söylüyor mu (docs/design/01-storage.md, bölüm 3.6).

Denetlenen kurallar (maçlar ve dilimleri için):

  I1  `has_event_payload = 1` olan her `events` satırının bir maç dizini var; v3'te manifestin `event`
      dilimi `ok`.
  I2  `has_payload = 1` olan her dilim satırının dosyası var, açılıyor ve ayrıştırılıyor; v3'te sha256 ve
      boyutlar manifesttekilere eşit.                                                    (yalnızca deep)
  I3  Her maç dizininin katalogda satırı var ve dilimleri, sayaçları dosyalardakiyle aynı.
  I4  `row_source = 'event'` olan satır `derive(olay yükü)`ne eşit.
  I5  Katalog `layout = 'v3'` derken diskte yalnızca eski dizin (ya da tersi) yok.
  I6  `pending_writes` boş (çalışan bir yazar yokken).
  I7  Değişiklik günlüğünün v3 parçalarında (`changes/<yyyy>-<aa>.jsonl`) `changes.seq` boşluksuzdur ve her
      satırın içindeki `seq`'e eşittir. Hızlı kip dizine ve dosya boyutlarına bakar: v3 satırlarının
      numaraları ardışık mı, her parçanın tamamı dizinlenmiş mi. `deep=True` parçaları okur ve her satırı
      dizindeki satırıyla karşılaştırır. Eski dosyanın (`score_changes.jsonl`) numaraları satır numarasıdır
      ve boş ya da bozuk satırlar numara harcadığı için meşru boşlukları olur: ona bu kural uygulanmaz.
  I8  Her `slice_history` satırı okunabilen bir gzip üyesini gösterir ve üyenin özeti satırınkine eşittir.
      Hızlı kip dosya okumaz: her dilimin satırları 1'den ardışık mı, geçmiş dosyası var mı ve son satırın
      bittiği yere kadar uzanıyor mu (`stat`). `deep=True` her üyeyi kendi bayt aralığından açar, satırını
      ayrıştırır ve özeti karşılaştırır. Yeniden okunan maçta (hızlı kipte imzası tutmayan, derin kipte her maç)
      katalogdaki satırlar geçmiş dosyalarından türetilenlerle de karşılaştırılır.
  I9  v3 maç dizininde manifestin adını vermediği dosya yok; yarım kalmış geçici dosyalar ayrıca
      bildirilir.                                                                        (yalnızca deep)

ve iki veritabanında `PRAGMA quick_check`.

Hızlı kip (`deep=False`) imzaya bakar: katalogdaki `sig`, v3'te manifest dosyasının, eski düzende maç
dizininin imzasına eşitse ve geçerli dizin değişmediyse maç değişmemiş sayılır ve dosyaları okunmaz. İmzası
tutmayan maçın satırları dosyalardan yeniden türetilip katalogdakilerle karşılaştırılır. `deep=True` her
maçı yeniden türetir ve v3 yüklerinin hepsini okur.

`repair=True`: tutmayan maçları (geçmiş satırları dahil, I8) dosyalardan yeniden dizinler, v3 dizinlerindeki yarım geçici dosyaları
siler, okunamayan v3 yüklerini manifestte `error` / `corrupt` olarak işaretler (deep) ve değişiklik günlüğünü
dosyalarından baştan dizinler (I7; dosyaların kendisindeki bir boşluk onarılamaz). Hiçbir yük dosyasını
silmez; eski düzen dosyalarına dokunmaz. Bozuk veritabanı onarılmaz: katalog yeniden kurulur.

Okunamayan dosyalar (`problems`) ve kullanılmayan kopyalar (`superseded`) tutarsızlık değildir; raporda
bilgi olarak durur. Denetim, çalıştığı sırada başka bir yazar olmadığını varsayar.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import os
import pathlib
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sofascore_scraper.store import changes as changes_mod
from sofascore_scraper.store import codec, files, indexer, layout
from sofascore_scraper.store import history as history_mod
from sofascore_scraper.store import manifest as manifest_mod
from sofascore_scraper.store.errors import PayloadCorrupt, PayloadMissing, StoreError
from sofascore_scraper.store.indexer import CatalogAdmin, EventRecord, IndexProblem, SupersededDir
from sofascore_scraper.store.legacy import LegacyEventDir
from sofascore_scraper.store.manifest import ErrorMark, SliceEntry

logger = logging.getLogger(__name__)

QUICK_CHECKS: Tuple[str, ...] = ("quick_check", "I1", "I3", "I5", "I6", "I7", "I8")
DEEP_CHECKS: Tuple[str, ...] = QUICK_CHECKS + ("I2", "I4", "I9")

INVARIANT_CATALOG = "catalog"  # katalog dosyasının kendisi: kullanılamıyor ya da quick_check geçmiyor
INVARIANT_STATE = "state"  # state.db quick_check

KIND_QUICK_CHECK = "quick_check"
KIND_NO_DIRECTORY = "no_event_directory"  # I1
KIND_PAYLOAD = "payload"  # I2
KIND_UNINDEXED = "unindexed"  # I3: dizin var, satır yok
KIND_SLICES = "slices"  # I3 (ya da I2): dilim satırları dosyalardakinden farklı
KIND_EVENT_ROW = "event_row"  # I4
KIND_PARTICIPANTS = "event_participants"  # I4
KIND_LAYOUT = "layout"  # I5
KIND_PENDING = "pending_write"  # I6
KIND_SEQ_GAP = "seq_gap"  # I7: v3 satırlarının numaraları ardışık değil
KIND_SEQ_UNINDEXED = "seq_unindexed"  # I7: parçanın tamamı dizinlenmemiş (ya da parça dizinlendikten sonra değişmiş)
KIND_SEQ_MISMATCH = "seq_mismatch"  # I7 (deep): dosyadaki satır ile dizindeki satır farklı
KIND_HISTORY_ROWS = "history_rows"  # I8: katalogdaki satırlar geçmiş dosyalarından türetilenlerden farklı
KIND_HISTORY_FILE = "history_file"  # I8: satırların gösterdiği geçmiş dosyası yok ya da kısa; numaralar ardışık değil
KIND_HISTORY_MEMBER = "history_member"  # I8 (deep): satırın aralığında okunabilen, aynı özetli bir üye yok
KIND_UNKNOWN_FILE = "unknown_file"  # I9

REASON_CORRUPT = "corrupt"


@dataclass(frozen=True)
class VerifyIssue:
    """Bir tutarsızlık. `repaired`: bu çalıştırmada giderildi."""

    invariant: str  # "I1" ... "I9" | "catalog" | "state"
    kind: str
    detail: str = ""
    event_id: Optional[int] = None
    path: Optional[str] = None  # DATA_DIR'e göre (veritabanı sorunlarında dosyanın yolu)
    repaired: bool = False


@dataclass
class VerifyReport:
    deep: bool
    repair: bool
    checked: Tuple[str, ...] = ()  # bakılan kurallar; katalog kullanılamıyorsa yalnızca "quick_check"
    events: int = 0  # diskte ya da katalogda görülen maç
    events_read: int = 0  # dosyaları yeniden okunan maç (hızlı kipte yalnızca imzası tutmayanlar)
    issues: List[VerifyIssue] = field(default_factory=list)
    problems: List[IndexProblem] = field(default_factory=list)
    superseded: List[SupersededDir] = field(default_factory=list)
    leftovers: List[str] = field(default_factory=list)  # v3 dizinlerinde kalmış geçici dosyalar
    leftovers_removed: int = 0
    seconds: float = 0.0

    @property
    def open_issues(self) -> List[VerifyIssue]:
        return [issue for issue in self.issues if not issue.repaired]

    @property
    def ok(self) -> bool:
        """Giderilmemiş tutarsızlık yok."""
        return not self.open_issues


# --- veritabanı dosyaları -----------------------------------------------------------------------------

def quick_check_file(path: str) -> Optional[List[str]]:
    """
    Bir SQLite dosyasında `PRAGMA quick_check` (salt okunur bağlantıyla; şema yaratmaz, göç çalıştırmaz).
    Dönen değer bulunan sorunlardır; boş liste = sağlam, None = dosya yok.
    """
    if not os.path.isfile(path):
        return None
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as exc:
        return [str(exc) or type(exc).__name__]
    finally:
        if conn is not None:
            conn.close()
    return [str(row[0]) for row in rows if str(row[0]).lower() != "ok"]


# --- v3 dosyaları (deep) ------------------------------------------------------------------------------

def payload_fault(data_dir: str, directory: str, name: str, entry: SliceEntry) -> Optional[str]:
    """
    I2: manifestin yükü var dediği dilimin dosyası yerinde, açılıyor, ayrıştırılıyor ve sha256 ile
    boyutları manifesttekilere eşit mi. Sorun yoksa None, varsa kısa açıklaması.
    """
    key, sub = layout.split_slice_name(name)
    path = layout.resolve(data_dir, layout.slice_path(directory, key, sub))
    try:
        stored = files.read_bytes(path)
        raw = codec.decode(stored, path)
    except PayloadMissing:
        return "file missing"
    except PayloadCorrupt as exc:
        return f"cannot be opened ({exc.detail or exc})"
    try:
        json.loads(raw)
    except (ValueError, RecursionError) as exc:
        return f"not JSON ({exc})"
    if codec.sha256_hex(raw) != entry.sha256:
        return "sha256 differs from the manifest"
    if len(raw) != entry.raw_bytes or len(stored) != entry.stored_bytes:
        return (f"size differs from the manifest (file {len(stored)}/{len(raw)}, "
                f"manifest {entry.stored_bytes}/{entry.raw_bytes})")
    return None


def directory_files(data_dir: str, directory: str) -> List[str]:
    """
    Varlık dizinindeki dosyalar, dizine göre "/" ayırıcılı ve sıralı; `_history` ve `_extra` alt ağaçları
    hariç. `_extra/`, taşınan eski dizinin tanınmayan dosyalarının olduğu gibi kopyasıdır (sofascore_scraper/store/migrate.py).
    """
    root = layout.resolve(data_dir, directory)
    found: List[str] = []
    for base, dirs, names in os.walk(root):
        if base == root:
            for skipped in (layout.HISTORY_DIR_NAME, layout.EXTRA_DIR_NAME):
                if skipped in dirs:
                    dirs.remove(skipped)  # geçmiş dosyaları I8'in konusu; `_extra` manifestin dışındadır
        prefix = os.path.relpath(base, root).replace(os.sep, "/")
        for name in names:
            found.append(name if prefix == "." else f"{prefix}/{name}")
    return sorted(found)


def is_leftover(name: str) -> bool:
    """Atomik yazmanın geçici dosyası (`.<ad>.<rastgele>.tmp`, sofascore_scraper/store/files.py): yarıda kalmış bir yazma."""
    base = name.rsplit("/", 1)[-1]
    return base.startswith(".") and base.endswith(".tmp")


# --- karşılaştırma ------------------------------------------------------------------------------------

_ABSENT: Any = object()
_Brief = Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]  # layout, path, legacy_path, sig


def _differing(expected: Dict[str, Any], stored: Dict[str, Any], skip: Sequence[str] = ()) -> List[str]:
    """Beklenen satırın, katalogdaki satırda farklı duran (ya da hiç olmayan) sütunları."""
    return [name for name, value in expected.items() if name not in skip and stored.get(name, _ABSENT) != value]


class _Run:
    """Tek bir doğrulama çalıştırması: okuma aşamasında sorunları toplar, sonra (istenirse) onarır."""

    def __init__(self, admin: CatalogAdmin, report: VerifyReport) -> None:
        self.admin = admin
        self.report = report
        self.data_dir = admin.data_dir
        self.deep = report.deep
        self.candidates: Dict[int, List[LegacyEventDir]] = {}
        self.reindex: Dict[int, List[int]] = {}  # maç → o maçın `issues` içindeki sorunlarının sıraları
        self.mark: Dict[int, List[str]] = {}  # v3 maçı → manifestte bozuk işaretlenecek dilim adları
        self.resign: List[int] = []  # satırları tutan ama imzası eskimiş maçlar
        self.pending: List[Tuple[str, int, int]] = []  # (tür, kimlik, sorunun sırası)
        self.changes: List[int] = []  # I7 sorunlarının sıraları: onarım günlüğü baştan dizinler

    def issue(self, invariant: str, kind: str, detail: str = "", *, event_id: Optional[int] = None,
              path: Optional[str] = None, fix: bool = False) -> None:
        self.report.issues.append(VerifyIssue(invariant, kind, detail, event_id=event_id, path=path))
        if fix and event_id is not None:
            self.reindex.setdefault(event_id, []).append(len(self.report.issues) - 1)

    # -- okuma aşaması -----------------------------------------------------------------------------

    def scan(self, conn: sqlite3.Connection) -> None:
        report = self.report
        v3_dirs = dict(indexer.scan_v3_events(self.data_dir, report.problems))
        for name, candidates in indexer.legacy_candidates(self.admin.reader, report.problems).items():
            event_id = indexer.canonical_id(name)
            if event_id is not None:
                self.candidates[event_id] = candidates
            else:  # kimliği kurallı olmayan dizin: okuyucu neden maç sayılmadığını bildirir
                indexer.legacy_record(self.admin.reader, candidates, problems=report.problems)
        # Hızlı kipin baktığı dört sütun bellekte tutulur; satırın tamamı yalnızca yeniden okunan maçta çekilir
        known: Dict[int, _Brief] = {int(row[0]): (row[1], row[2], row[3], row[4]) for row in conn.execute(
            "SELECT id, layout, path, legacy_path, sig FROM events WHERE has_event_payload = 1 OR layout IS NOT NULL")}
        ids = sorted(set(v3_dirs) | set(self.candidates) | set(known))
        report.events = len(ids)
        for event_id in ids:
            self.check_event(conn, event_id, event_id in v3_dirs, self.candidates.get(event_id, []),
                             known.get(event_id))
        for row in conn.execute("SELECT kind, entity_id FROM pending_writes ORDER BY kind, entity_id"):
            kind, entity_id = str(row[0]), int(row[1])
            self.issue("I6", KIND_PENDING, f"{kind} {entity_id}: marker of an unfinished write",
                       event_id=entity_id if kind == "event" else None)
            self.pending.append((kind, entity_id, len(report.issues) - 1))
        for kind, detail, path in self.change_log_faults(conn):
            self.issue("I7", kind, detail, path=path)
            self.changes.append(len(report.issues) - 1)
        for kind, detail, owner, entity_id, path in self.history_faults(conn):
            self.issue("I8", kind, detail, event_id=entity_id if owner == indexer.KIND_EVENT else None, path=path,
                       fix=True)

    def history_faults(self, conn: sqlite3.Connection) -> List[Tuple[str, str, str, int, Optional[str]]]:
        """I8: `slice_history` satırları ile geçmiş dosyaları arasındaki tutarsızlıklar: (tür, açıklama, varlık türü,
        kimlik, yol)."""
        found: List[Tuple[str, str, str, int, Optional[str]]] = []
        groups: Dict[Tuple[str, int, str, str], List[Tuple[int, str, int, int]]] = {}
        for row in conn.execute("SELECT kind, entity_id, key, sub, n, sha256, offset, length FROM slice_history "
                                "ORDER BY kind, entity_id, key, sub, n"):
            groups.setdefault((str(row[0]), int(row[1]), str(row[2]), str(row[3])), []).append(
                (int(row[4]), str(row[5]), int(row[6]), int(row[7])))
        for (kind, entity_id, key, sub), rows in groups.items():
            directory = history_mod.entity_directory(conn, kind, entity_id)
            label = f"{kind} {entity_id} {layout.slice_name(key, sub)}"
            if directory is None:
                found.append((KIND_HISTORY_FILE, f"{label}: the directory of the entity is not known", kind, entity_id, None))
                continue
            rel = layout.history_path(directory, key, sub)
            if [n for n, *_ in rows] != list(range(1, len(rows) + 1)):
                found.append((KIND_HISTORY_FILE, f"{label}: the numbers do not run from 1 without a gap", kind, entity_id, rel))
                continue
            path = layout.resolve(self.data_dir, rel)
            end = max(offset + length for _n, _sha, offset, length in rows)
            try:
                size: Optional[int] = os.stat(path).st_size
            except OSError:
                size = None
            if size is None or size < end:
                detail = "file missing" if size is None else f"file of {size} bytes, rows up to {end} bytes"
                found.append((KIND_HISTORY_FILE, f"{label}: {detail}", kind, entity_id, rel))
                continue
            if not self.deep:
                continue
            try:
                data = files.read_bytes(path)
            except StoreError as exc:
                found.append((KIND_HISTORY_FILE, f"{label}: cannot be read ({exc.detail or exc})", kind, entity_id, rel))
                continue
            bad: List[int] = []
            for n, digest, offset, length in rows:
                try:
                    _at, member_digest, _raw = history_mod.decode_member(data[offset:offset + length], path)
                except StoreError:
                    bad.append(n)
                    continue
                if member_digest != digest:
                    bad.append(n)
            if bad:
                found.append((KIND_HISTORY_MEMBER, f"{label}: members unreadable or with another digest: "
                              f"{', '.join(map(str, bad[:10]))}", kind, entity_id, rel))
        return found

    def change_log_faults(self, conn: sqlite3.Connection) -> List[Tuple[str, str, Optional[str]]]:
        """I7: değişiklik günlüğünün v3 parçaları ile dizinleri arasındaki tutarsızlıklar: (tür, açıklama, yol)."""
        found: List[Tuple[str, str, Optional[str]]] = []
        numbered = changes_mod.NUMBERED_SEGMENTS  # eski dosya ve kopyası: numara satır numarasıdır
        marks_of = ", ".join("?" for _ in numbered)
        total, low, high = conn.execute(
            f"SELECT count(*), min(seq), max(seq) FROM changes WHERE segment NOT IN ({marks_of})",
            numbered).fetchone()
        if total and high - low + 1 != total:
            found.append((KIND_SEQ_GAP, f"the numbers of the v3 rows have gaps: {total} rows "
                          f"in {low}-{high}", None))
        marks = changes_mod._load_marks(self.admin.catalog)
        on_disk = [segment for segment in changes_mod.segments(self.data_dir) if segment not in numbered]
        indexed = {str(row[0]) for row in conn.execute(
            f"SELECT DISTINCT segment FROM changes WHERE segment NOT IN ({marks_of})", numbered)}
        for segment in sorted(set(on_disk) | indexed | {name for name in marks if name not in numbered}):
            if segment not in on_disk:
                found.append((KIND_SEQ_UNINDEXED, "indexed rows but no file", segment))
                continue
            mark = marks.get(segment)
            try:
                size = os.stat(layout.resolve(self.data_dir, segment)).st_size
            except OSError:
                size = -1
            if mark is None or mark.size != size:
                found.append((KIND_SEQ_UNINDEXED, "the segment is not fully indexed" if mark is None else
                              f"file of {size} bytes, {mark.size} bytes indexed", segment))
                continue
            if not self.deep:
                continue
            expected = {line.seq: line.line for line in changes_mod.read_segment(self.data_dir, segment)}
            stored = {int(row[0]): str(row[1]) for row in conn.execute(
                "SELECT seq, row_json FROM changes WHERE segment = ?", (segment,))}
            missing = sorted(seq for seq in expected if seq not in stored)
            extra = sorted(seq for seq in stored if seq not in expected)
            differing = sorted(seq for seq in expected if seq in stored and stored[seq] != expected[seq])
            if missing or extra or differing:
                notes = [f"{label}: {', '.join(map(str, numbers[:10]))}" for label, numbers in (
                    ("not indexed", missing), ("not in the file", extra), ("rows differ", differing)) if numbers]
                found.append((KIND_SEQ_MISMATCH, "; ".join(notes), segment))
        return found

    def _unchanged(self, event_id: int, has_v3: bool, candidates: Sequence[LegacyEventDir],
                   brief: _Brief) -> bool:
        """Hızlı kip: geçerli dizin ve imzası katalogdakiyle aynı mı (dosyalar okunmadan)."""
        row_layout, row_path, row_legacy_path, row_sig = brief
        first = candidates[0] if candidates else None
        if has_v3:
            if row_layout != indexer.LAYOUT_V3 or row_path is not None:
                return False
            if row_legacy_path != (first.path if first else None):
                return False
            manifest_file = layout.resolve(self.data_dir, layout.manifest_path(layout.event_dir(event_id)))
            if indexer.file_signature(manifest_file) != row_sig:
                return False
            winner = layout.event_dir(event_id)
            self.report.superseded.extend(
                SupersededDir(event_id, c.path, winner, indexer.SUPERSEDED_BY_V3) for c in candidates)
            return True
        if first is None or row_layout != indexer.LAYOUT_LEGACY:
            return False
        if row_path != first.path or row_sig != first.sig or row_legacy_path is not None:
            return False
        self.report.superseded.extend(
            SupersededDir(event_id, c.path, first.path, indexer.SUPERSEDED_BY_LEGACY) for c in candidates[1:])
        return True

    def check_event(self, conn: sqlite3.Connection, event_id: int, has_v3: bool,
                    candidates: Sequence[LegacyEventDir], brief: Optional[_Brief]) -> None:
        report = self.report
        if brief is not None and not self.deep and self._unchanged(event_id, has_v3, candidates, brief):
            return
        report.events_read += 1
        row: Optional[Dict[str, Any]] = None
        if brief is not None:
            row = dict(conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
        problems: List[IndexProblem] = []
        record = indexer.event_record(self.data_dir, self.admin.reader, event_id, has_v3=has_v3,
                                      candidates=candidates, problems=problems, superseded=report.superseded)
        report.problems.extend(problems)
        if self.deep and has_v3:
            self.check_v3_files(event_id)
        if record is None:
            if row is not None:
                why = f"{problems[0].kind}: {problems[0].detail}" if problems else "no directory"
                self.issue("I1", KIND_NO_DIRECTORY, f"a catalog row but no valid event directory ({why})",
                           event_id=event_id, path=row["path"] or layout.event_dir(event_id), fix=True)
            return  # satırı da yok: okunamayan dizin yalnızca sorun olarak bildirilir
        where = record.event["path"] or layout.event_dir(event_id)
        if row is None:
            self.issue("I3", KIND_UNINDEXED, "an event directory but no catalog row", event_id=event_id,
                       path=where, fix=True)
            return
        if row["layout"] != record.layout:
            self.issue("I5", KIND_LAYOUT, f"the catalog says {row['layout']!r}, the valid one on disk is {record.layout!r}",
                       event_id=event_id, path=where, fix=True)
            return
        clean = True
        columns = _differing(record.event, row, skip=("sig",))
        if columns:
            clean = False
            self.issue("I4", KIND_EVENT_ROW, "columns differ: " + ", ".join(columns), event_id=event_id,
                       path=where, fix=True)
        clean = self.check_slices(conn, record, where) and clean
        links = [dict(r) for r in conn.execute(
            "SELECT participant_id, start_ts, event_id, side FROM event_participants WHERE event_id = ? "
            "ORDER BY side", (event_id,))]
        if links != sorted(record.links, key=lambda link: link["side"]):
            clean = False
            self.issue("I4", KIND_PARTICIPANTS, "event_participants rows do not match the event payload",
                       event_id=event_id, path=where, fix=True)
        history = [dict(r) for r in conn.execute(
            "SELECT * FROM slice_history WHERE kind = ? AND entity_id = ? ORDER BY key, sub, n",
            (indexer.KIND_EVENT, event_id))]
        if history != sorted(record.history, key=lambda h: (h["key"], h["sub"], h["n"])):
            clean = False
            self.issue("I8", KIND_HISTORY_ROWS, "slice_history rows do not match the history files",
                       event_id=event_id, path=where, fix=True)
        if clean and row["sig"] != record.event["sig"]:
            self.resign.append(event_id)  # içerik aynı, yalnızca imza eskimiş: tutarsızlık değil

    def check_slices(self, conn: sqlite3.Connection, record: EventRecord, where: str) -> bool:
        stored = {(r["key"], r["sub"]): dict(r) for r in conn.execute(
            "SELECT * FROM event_slices WHERE event_id = ?", (record.event_id,))}
        expected = {(s["key"], s["sub"]): s for s in record.slices}
        notes: List[str] = []
        lost_payload = False
        for name in sorted(set(stored) | set(expected)):
            label = "/".join(part for part in name if part)
            if name not in expected:
                notes.append(f"{label}: not in the files")
                lost_payload = lost_payload or bool(stored[name]["has_payload"])
            elif name not in stored:
                notes.append(f"{label}: not in the catalog")
            else:
                columns = _differing(expected[name], stored[name])
                if columns:
                    notes.append(f"{label}: " + ", ".join(columns))
                    lost_payload = lost_payload or (bool(stored[name]["has_payload"])
                                                    and not expected[name]["has_payload"])
        if notes:
            # Katalog "yükü var" derken dosya yoksa ya da okunamıyorsa I2, öteki farklar I3
            self.issue("I2" if lost_payload else "I3", KIND_SLICES, "; ".join(notes), event_id=record.event_id,
                       path=where, fix=True)
        return not notes

    def check_v3_files(self, event_id: int) -> None:
        """I2 ve I9: v3 maç dizinindeki yük dosyaları manifestle karşılaştırılır (deep)."""
        directory = layout.event_dir(event_id)
        try:
            found = manifest_mod.read_manifest(layout.resolve(self.data_dir, layout.manifest_path(directory)))
        except StoreError:
            return  # okunamayan manifest zaten sorun olarak bildirildi
        named = {layout.MANIFEST_NAME}
        broken: List[str] = []
        for name, entry in found.slices.items():
            key, sub = layout.split_slice_name(name)
            if not entry.has_payload:
                if entry.error is not None and entry.error.reason == REASON_CORRUPT:
                    # Bozuk diye işaretlenmiş yükün dosyası silinmez: yeniden çekilene kadar yerinde durur
                    named.add(layout.slice_path("", key, sub).lstrip("/"))
                continue
            named.add(layout.slice_path("", key, sub).lstrip("/"))
            fault = payload_fault(self.data_dir, directory, name, entry)
            if fault is not None:
                broken.append(name)
                self.issue("I2", KIND_PAYLOAD, f"{name}: {fault}", event_id=event_id,
                           path=layout.slice_path(directory, key, sub), fix=True)
        if broken:
            self.mark[event_id] = broken
        for name in directory_files(self.data_dir, directory):
            if name in named:
                continue
            if is_leftover(name):
                self.report.leftovers.append(f"{directory}/{name}")
            else:
                self.issue("I9", KIND_UNKNOWN_FILE, "a file the manifest does not name", event_id=event_id,
                           path=f"{directory}/{name}")

    # -- onarım ------------------------------------------------------------------------------------

    def repair(self) -> None:
        admin, report = self.admin, self.report
        cat = admin.catalog
        fixed: List[int] = []
        events = sorted(set(self.reindex) | set(self.mark) | set(self.resign))
        # Yapılacak iş yoksa yazma kilidi de alınmaz (başka bir yazar varken boşuna beklenmesin)
        with cat.write() if events or self.pending or self.changes else contextlib.nullcontext():
            for event_id, names in self.mark.items():
                mark_corrupt(admin, event_id, names)
            for event_id in events:
                admin.index_event(event_id, candidates=self.candidates.get(event_id, []))
                fixed.extend(self.reindex.get(event_id, []))
            for kind, entity_id, position in self.pending:
                if kind != "event":
                    continue  # öteki varlıkların dizinleyicisi sonraki adımda gelir
                admin.recover_event(entity_id, candidates=self.candidates.get(entity_id, []))
                cat.connection().execute(
                    "DELETE FROM pending_writes WHERE kind = ? AND entity_id = ?", (kind, entity_id))
                fixed.append(position)
            if self.changes:
                # Günlük dosyalarından baştan dizinlenir; hâlâ duran sorun (dosyalardaki boşluk) açık kalır
                changes_mod.sync(cat, self.data_dir, full=True)
                left = {(kind, path) for kind, _detail, path in self.change_log_faults(cat.connection())}
                fixed.extend(position for position in self.changes
                             if (report.issues[position].kind, report.issues[position].path) not in left)
        for position in fixed:
            report.issues[position] = dataclasses.replace(report.issues[position], repaired=True)
        for rel in report.leftovers:
            report.leftovers_removed += int(files.remove(layout.resolve(self.data_dir, rel)))


def mark_corrupt(admin: CatalogAdmin, event_id: int, names: Sequence[str]) -> List[str]:
    """
    Okunamayan v3 yüklerini manifestte `error` / `corrupt` olarak işaretler (dilim yeniden çekilecekler
    arasına girer). Dosya silinmez. Manifest, katalogun yazma kilidi altında okunup yazılır (bölüm 6.2);
    kilit alındıktan sonra yük yeniden denetlenir, o arada düzelmiş dilime dokunulmaz. İşaretlenen
    dilimlerin adlarını döndürür; katalog satırlarını çağıran yeniden dizinler.
    """
    directory = layout.event_dir(event_id)
    manifest_file = layout.resolve(admin.data_dir, layout.manifest_path(directory))
    marked: List[str] = []
    with admin.catalog.write():
        found = manifest_mod.read_manifest(manifest_file)
        at = datetime.fromtimestamp(int(admin.now()), timezone.utc)
        for name in names:
            entry = found.slices.get(name)
            if entry is None or not entry.has_payload or payload_fault(admin.data_dir, directory, name, entry) is None:
                continue
            count = entry.error.count + 1 if entry.error is not None else 1
            entry.state = "error"
            entry.error = ErrorMark(reason=REASON_CORRUPT, at=at, count=count)
            entry.sha256 = entry.stored_bytes = entry.raw_bytes = None  # artık geçerli bir yükü yok
            entry.fetched_at = None
            entry.checked_at = at
            marked.append(name)
        if marked:
            found.updated_at = max(found.updated_at, at)
            manifest_mod.write_manifest(manifest_file, found)
            logger.warning(f"Event {event_id}: unreadable payloads were marked as corrupt in the manifest: "
                           f"{', '.join(marked)}")
    return marked


# --- giriş --------------------------------------------------------------------------------------------

def verify(admin: CatalogAdmin, *, deep: bool = False, repair: bool = False) -> VerifyReport:
    """
    Kataloğu dosyalarla karşılaştırır (modül açıklamasındaki kurallar) ve raporu döndürür. Hata fırlatmaz
    diye bir söz yoktur: disk ya da kilit hatası StoreError olarak çıkar; tutarsızlıklar rapordadır.
    """
    started = time.monotonic()
    report = VerifyReport(deep=deep, repair=repair, checked=QUICK_CHECKS[:1])
    cat = admin.catalog

    state_path = layout.resolve(admin.data_dir, layout.STATE_DB)
    for line in quick_check_file(state_path) or []:
        report.issues.append(VerifyIssue(INVARIANT_STATE, KIND_QUICK_CHECK, line, path=layout.STATE_DB))

    state = cat.inspect()
    if not state.usable:
        report.issues.append(VerifyIssue(
            INVARIANT_CATALOG, str(state.rebuild_reason),
            "the catalog is unusable and must be rebuilt" + (f" ({state.detail})" if state.detail else ""),
            path=layout.CATALOG_DB))
    else:
        faults = cat.quick_check()
        for line in faults:
            report.issues.append(VerifyIssue(INVARIANT_CATALOG, KIND_QUICK_CHECK, line, path=layout.CATALOG_DB))
        if not faults:
            run = _Run(admin, report)
            with cat.read() as conn:
                run.scan(conn)
            report.checked = DEEP_CHECKS if deep else QUICK_CHECKS
            if repair:
                run.repair()

    report.seconds = time.monotonic() - started
    if report.open_issues:
        logger.warning(f"Catalog verification: {len(report.open_issues)} unresolved inconsistencies "
                       f"({report.events} events, {report.events_read} of them read again)")
    return report


__all__ = [
    "QUICK_CHECKS",
    "DEEP_CHECKS",
    "VerifyIssue",
    "VerifyReport",
    "verify",
    "quick_check_file",
    "payload_fault",
    "directory_files",
    "is_leftover",
    "mark_corrupt",
]
