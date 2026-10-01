"""
Dizinleyici: yük dosyalarından katalog satırları (docs/design/01-storage.md, bölüm 3.4 ve 5.2).

Bu modül maçları ve dilimlerini dizinler: `events`, `event_slices`, `event_participants`,
`participants` ve olay yükünde geçen `sports` / `categories` / `tournaments` / `seasons` satırları.
Program sayfaları, sezon listeleri, değişiklik günlüğü ve uzlaştırma (reconcile) sonraki adımda eklenir.

Kaynaklar ve öncelik:

  * v3: `v3/events/.../<id>/manifest.json` + `event.json.gz`. Geçerli bir v3 maçı, manifesti okunabilen,
    `event` dilimi `ok` olan ve olay yükü okunabilen dizindir. Dilim satırları manifestten gelir; öteki
    yük dosyaları okunmaz.
  * Eski düzen: `match_details` altındaki bütün biçimler (src/store/legacy.py). Dilim satırları dosyalardan
    ve işaret dosyalarından gelir; `fetched_at` dosyanın mtime'ıdır.
  * Aynı maç iki düzende de varsa v3 geçerlidir; eski dizin `legacy_path` sütununa yazılır (bölüm 3.4,
    adım 5). v3 kopyası okunamıyorsa sorun bildirilir ve eski kopya dizinlenir.
  * Aynı maçın birden çok eski dizini varsa olay yükü en yeni olan geçerlidir (LegacyReader.iter_events
    ile aynı kural); ötekiler raporda `superseded` olarak durur.

Satırların tamamı dosyalardan türer, saate bakılmaz: aynı ağacın iki kurulumu aynı satırları verir.
Okunamayan ya da ayrıştırılamayan dosya kurulumu durdurmaz; raporda listelenir.

Varlık satırlarının kuralı: `sports`, `categories`, `tournaments` ve `seasons` satırı yalnızca yoksa
eklenir (tarama sırasında ilk gören kazanır; listeler ve sezon dosyaları bu satırların asıl kaynağıdır).
`participants` satırında en yeni olay yükü kazanır (`updated_at` karşılaştırılır).

Yeniden kurma (`CatalogAdmin.rebuild`) iki kiptedir (bölüm 3.4):

  * yerinde: tek `BEGIN IMMEDIATE` işlemi bütün satırları siler ve yeniden yazar. Başka bağlantılar
    commit'e kadar eski, tutarlı kataloğu görür; hata olursa eski katalog kalır.
  * yeniden yaratma: `catalog.db.build` kurulur, sonra `catalog.db`'nin yerine konur.

Yeniden kurma `maintenance` kilidini (bölüm 6.1) gerektirir; kilitler Store cephesiyle gelir, burada
alınmaz. O zamana kadar çağıran, aynı anda başka yazar olmadığından emin olmalıdır.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from src.store import catalog as catalog_mod
from src.store import codec, derive, files, layout, legacy
from src.store import manifest as manifest_mod
from src.store.catalog import Catalog
from src.store.errors import (
    CatalogCorrupt,
    LayoutError,
    PayloadCorrupt,
    PayloadMissing,
    SchemaTooNew,
    StoreError,
)
from src.store.legacy import LegacyEvent, LegacyEventDir, LegacyProblem, LegacyReader, LegacySlice
from src.store.manifest import Manifest, SliceEntry
from src.version import __version__ as APP_VERSION

if TYPE_CHECKING:
    from src.store.verify import VerifyReport

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]
Row = Dict[str, Any]
Progress = Callable[[str, int, int], None]

LAYOUT_V3 = "v3"
LAYOUT_LEGACY = "legacy"

MODE_AUTO = "auto"
MODE_IN_PLACE = "in_place"
MODE_RECREATE = "recreate"
BUILD_SUFFIX = ".build"

EVENT_KEY = legacy.EVENT_KEY

# rebuild(progress=...) aşama adları
STAGE_SCAN = "scan"
STAGE_V3_EVENTS = "v3_events"
STAGE_LEGACY_EVENTS = "legacy_events"
STAGE_FINISH = "finish"

# meta tablosundaki anahtarlar (derive_version'ı catalog.py yazar)
META_BUILT_AT = "built_at"  # epoch saniye
META_BUILT_BY = "built_by"  # uygulama sürümü
META_BUILD_MODE = "build_mode"
META_COUNTS = "counts"  # JSON: tablo → satır sayısı

# IndexProblem.kind: eski düzen okuyucusunun değerleri + v3'e özgü olanlar
PROBLEM_CORRUPT = legacy.PROBLEM_CORRUPT
PROBLEM_MALFORMED = legacy.PROBLEM_MALFORMED
PROBLEM_ID_MISMATCH = legacy.PROBLEM_ID_MISMATCH
PROBLEM_NO_EVENT = legacy.PROBLEM_NO_EVENT
PROBLEM_UNREADABLE = legacy.PROBLEM_UNREADABLE
PROBLEM_NAME = legacy.PROBLEM_NAME
PROBLEM_TOO_NEW = "schema_too_new"  # manifest bu sürümün bildiğinden yeni bir biçimde

SUPERSEDED_BY_V3 = "v3"
SUPERSEDED_BY_LEGACY = "legacy"

# `events` satırının dizinleyicinin doldurduğu (yükten türemeyen) sütunları
EVENT_STORAGE_COLUMNS: Tuple[str, ...] = (
    "status_regressed", "layout", "path", "legacy_path", "sig", "first_seen_at", "updated_at",
)
SLICE_COLUMNS: Tuple[str, ...] = (
    "event_id", "key", "sub", "state", "has_payload", "fetched_at", "checked_at",
    "empty_count", "unverified_empty_count", "error_reason", "error_status", "error_at", "error_count",
    "stored_bytes", "raw_bytes", "history_count", "meta_json",
)
_PARTICIPANT_COLUMNS: Tuple[str, ...] = (
    "id", "sport", "name", "name_folded", "short_name", "slug", "name_code", "country", "gender", "type",
    "national", "updated_at",
)

_BATCH_EVENTS = 500  # bu kadar maçta bir satırlar veritabanına yazılır
_PROGRESS_EVERY = 100
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1
# Beklenmeyen biçimdeki bir yük satır türetilirken bu hataları verebilir; maç atlanır, kurulum sürer
_DERIVE_ERRORS = (StoreError, ArithmeticError, AttributeError, KeyError, TypeError, ValueError)


# --- kayıtlar -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class IndexProblem:
    """Dizinlemeyi durdurmayan bir sorun (okunamayan dosya, adı kurala uymayan dizin...)."""

    layout: str  # "v3" | "legacy"
    path: str  # DATA_DIR'e göre
    kind: str  # PROBLEM_* değerlerinden biri
    detail: str = ""


@dataclass(frozen=True)
class SupersededDir:
    """Aynı maçın daha geçerli bir kopyası olduğu için dizinlenmeyen eski düzen dizini."""

    event_id: int
    path: str
    winner: str  # geçerli kopyanın dizini (DATA_DIR'e göre)
    by: str  # "v3": v3 kopyası var | "legacy": daha yeni bir eski düzen dizini var


@dataclass(frozen=True)
class V3Event:
    """Okunmuş bir v3 maç dizini: manifest ve olay yükü."""

    event_id: int
    path: str  # v3 dizini (DATA_DIR'e göre)
    manifest: Manifest
    event: Dict[str, Any]
    sig: str  # manifest dosyasının imzası: "<mtime_ns>:<boyut>" (bölüm 3.5)


@dataclass(frozen=True)
class EventRecord:
    """Bir maçın katalogda durması gereken satırları (dosyalardan türetilmiş)."""

    event_id: int
    layout: str
    event: Row  # `events` satırı: EVENT_DERIVED_COLUMNS + EVENT_STORAGE_COLUMNS
    slices: Tuple[Row, ...]  # `event_slices` satırları (SLICE_COLUMNS)
    links: Tuple[Row, ...]  # `event_participants` satırları
    entities: derive.EntityRows


@dataclass
class RebuildReport:
    mode: str  # "in_place" | "recreate"
    reason: Optional[str]  # kataloğun bulunduğu hal (CatalogState.rebuild_reason); None: kullanılabilirdi
    completed: bool = False  # False: should_stop durdurdu, eski katalog olduğu gibi kaldı
    events: int = 0
    events_v3: int = 0
    events_legacy: int = 0
    slices: int = 0
    counts: Dict[str, int] = field(default_factory=dict)  # tablo → satır sayısı
    problems: List[IndexProblem] = field(default_factory=list)
    superseded: List[SupersededDir] = field(default_factory=list)
    seconds: float = 0.0


class _Stopped(Exception):
    """should_stop True döndü: işlem geri alınır."""


# --- yardımcılar --------------------------------------------------------------------------------------

def _i64(value: Optional[int]) -> Optional[int]:
    """SQLite INTEGER sınırına sığdırır (işaret dosyasındaki dev bir sayı kurulumu düşürmesin)."""
    if value is None:
        return None
    return min(max(int(value), _INT64_MIN), _INT64_MAX)


def _clean(value: str) -> str:
    """UTF-8'e çevrilemeyen metin (JSON'daki eşsiz vekil kod noktası) SQLite'a yazılamaz: '?' ile değiştirilir."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return value.encode("utf-8", "replace").decode("utf-8")
    return value


def _storable(row: Row) -> Row:
    """Satırı yazılabilir hale getirir (yerinde): yalnızca ASCII olmayan metinlere bakılır."""
    for name, value in row.items():
        if type(value) is str and not value.isascii():
            row[name] = _clean(value)
    return row


def _detail(exc: BaseException) -> str:
    return (getattr(exc, "detail", "") or str(exc)) or type(exc).__name__


def problem_of(exc: BaseException, rel: str, layout_name: str) -> IndexProblem:
    """Okuma / türetme hatası → IndexProblem (eski düzen okuyucusundaki eşlemeyle aynı türler)."""
    if isinstance(exc, SchemaTooNew):
        kind = PROBLEM_TOO_NEW
    elif isinstance(exc, PayloadCorrupt):
        kind = PROBLEM_CORRUPT
    elif isinstance(exc, LayoutError):
        kind = PROBLEM_ID_MISMATCH
    elif isinstance(exc, PayloadMissing):
        kind = PROBLEM_NO_EVENT
    elif isinstance(exc, StoreError):
        kind = PROBLEM_UNREADABLE
    else:
        kind = PROBLEM_MALFORMED
    return IndexProblem(layout_name, rel, kind, _detail(exc))


def _from_legacy(problems: Iterable[LegacyProblem]) -> List[IndexProblem]:
    return [IndexProblem(LAYOUT_LEGACY, p.path, p.kind, p.detail) for p in problems]


def canonical_id(name: str) -> Optional[int]:
    """Dizin adı bir maç kimliğinin kurallı yazımıysa ("123"; "0123" değil) o kimlik, değilse None."""
    if name.isascii() and name.isdigit() and str(int(name)) == name:
        return int(name)
    return None


def file_signature(path: PathLike) -> Optional[str]:
    """Dosyanın değişiklik imzası: "<mtime_ns>:<boyut>"; dosya yoksa None."""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise StoreError.from_exception(exc, os.fspath(path), reading=True) from exc
    return f"{st.st_mtime_ns}:{st.st_size}"


# --- keşif --------------------------------------------------------------------------------------------

def _numbered_dirs(path: str, rel: str, problems: List[IndexProblem]) -> List[Tuple[int, str]]:
    """Dizindeki, adı yalnızca rakamlardan oluşan alt dizinler: (sayı, ad), sayıya göre sıralı."""
    try:
        with os.scandir(path) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        problems.append(problem_of(StoreError.from_exception(exc, path, reading=True), rel, LAYOUT_V3))
        return []
    found: List[Tuple[int, str]] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue  # geçici dosyalar ve işletim sisteminin gizli dosyaları
        try:
            is_dir = entry.is_dir()
        except OSError:
            is_dir = False
        if is_dir and entry.name.isascii() and entry.name.isdigit():
            found.append((int(entry.name), entry.name))
        else:
            problems.append(IndexProblem(LAYOUT_V3, f"{rel}/{entry.name}", PROBLEM_NAME,
                                         "v3 maç ağacında tanınmayan girdi"))
    return sorted(found)


def scan_v3_events(data_dir: PathLike, problems: Optional[List[IndexProblem]] = None) -> List[Tuple[int, str]]:
    """
    `v3/events` altındaki maç dizinleri: (maç kimliği, DATA_DIR'e göre yol), kimliğe göre sıralı. Yalnızca
    dizin listeler; hiçbir dosyayı okumaz. Kurallı yerinde durmayan dizin (ör. yanlış kovaya konmuş)
    maç sayılmaz ve bildirilir.
    """
    notes: List[IndexProblem] = problems if problems is not None else []
    found: List[Tuple[int, str]] = []
    root_rel = layout.EVENTS_DIR
    root = layout.resolve(data_dir, root_rel)
    for _, million in _numbered_dirs(root, root_rel, notes):
        million_rel = f"{root_rel}/{million}"
        for _, thousand in _numbered_dirs(os.path.join(root, million), million_rel, notes):
            bucket_rel = f"{million_rel}/{thousand}"
            for event_id, name in _numbered_dirs(os.path.join(root, million, thousand), bucket_rel, notes):
                rel = f"{bucket_rel}/{name}"
                if layout.event_id_from_dir(rel) != event_id:
                    notes.append(IndexProblem(LAYOUT_V3, rel, PROBLEM_NAME, "maç dizini kurallı yerinde değil"))
                    continue
                found.append((event_id, rel))
    return sorted(found)


def legacy_order(candidate: LegacyEventDir) -> Tuple[int, int, str]:
    """
    Aynı maçın eski düzen dizinleri arasındaki öncelik: olay yükü en yeni olan, eşitlikte lig/sezon
    dizini, sonra yol. LegacyReader.iter_events'in kuralıdır (test eşitliği denetler).
    """
    return (-candidate.mtime_ns, legacy._FORM_RANK[candidate.form], candidate.path)


def legacy_candidates(reader: LegacyReader,
                      problems: Optional[List[IndexProblem]] = None) -> Dict[str, List[LegacyEventDir]]:
    """
    Eski düzendeki maç dizini adayları, dizin adına göre gruplanmış ve her grup öncelik sırasında.
    Yalnızca dizin listeler ve `stat` çağırır. Anahtarlar: kurallı kimlikler sayıya göre, sonra ötekiler.
    """
    report = legacy.LegacyReport()
    groups: Dict[str, List[LegacyEventDir]] = {}
    for candidate in reader.event_dirs(report):
        groups.setdefault(candidate.name, []).append(candidate)
    if problems is not None:
        problems.extend(_from_legacy(report.problems))

    def name_order(name: str) -> Tuple[int, int, str]:
        event_id = canonical_id(name)
        return (0, event_id, name) if event_id is not None else (1, 0, name)

    return {name: sorted(groups[name], key=legacy_order) for name in sorted(groups, key=name_order)}


# --- dosyalardan kayıt --------------------------------------------------------------------------------

def read_v3_event(data_dir: PathLike, event_id: int) -> V3Event:
    """
    Bir v3 maç dizinini okur (manifest + olay yükü). Manifest yoksa PayloadMissing, okunamıyorsa
    PayloadCorrupt, biçimi yeniyse SchemaTooNew; manifest başka bir varlığınsa ya da olay yükündeki kimlik
    tutmuyorsa LayoutError; `event` dilimi `ok` değilse ya da olay yükü yoksa PayloadMissing.
    """
    rel = layout.event_dir(event_id)
    manifest_file = layout.resolve(data_dir, layout.manifest_path(rel))
    sig = file_signature(manifest_file)  # okumadan önce: araya giren bir yazma imzayı eski bırakır, yeni değil
    if sig is None:
        raise PayloadMissing(f"Manifest bulunamadı: {manifest_file}", path=manifest_file)
    found = manifest_mod.read_manifest(manifest_file)
    if found.kind != "event" or found.id != event_id:
        raise LayoutError(
            f"Manifest bu maç dizinine ait değil ({found.kind} {found.id!r}): {manifest_file}",
            path=manifest_file, detail=f"{found.kind} {found.id!r}, dizin {event_id}")
    entry = found.slices.get(EVENT_KEY)
    if entry is None or entry.state != "ok" or not entry.has_payload:
        raise PayloadMissing(f"v3 maç dizininde geçerli olay yükü yok: {manifest_file}", path=manifest_file,
                             detail="manifestte event dilimi ok değil")
    payload_file = layout.resolve(data_dir, layout.slice_path(rel, EVENT_KEY))
    payload = codec.read_payload(payload_file)
    if not isinstance(payload, dict) or isinstance(payload.get("id"), bool) or payload.get("id") != event_id:
        got = payload.get("id") if isinstance(payload, dict) else type(payload).__name__
        raise LayoutError(f"Olay yükündeki id ({got!r}) dizine ({event_id}) eşit değil: {payload_file}",
                          path=payload_file, detail=f"id {got!r}, dizin {event_id}")
    return V3Event(event_id=event_id, path=rel, manifest=found, event=payload, sig=sig)


def _meta_json(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if meta is None:
        return None
    return json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _v3_slice_row(event_id: int, name: str, entry: SliceEntry) -> Row:
    key, sub = layout.split_slice_name(name)
    empty, error, history = entry.empty, entry.error, entry.history
    return {
        "event_id": event_id,
        "key": key,
        "sub": sub,
        "state": entry.state,
        "has_payload": int(entry.has_payload),
        "fetched_at": derive.epoch_seconds(entry.fetched_at),
        "checked_at": derive.epoch_seconds(entry.checked_at),
        "empty_count": _i64(empty.count) if empty else 0,
        "unverified_empty_count": _i64(empty.unverified) if empty else 0,
        "error_reason": error.reason if error else None,
        "error_status": _i64(error.status) if error else None,
        "error_at": derive.epoch_seconds(error.at) if error else None,
        "error_count": _i64(error.count) if error else 0,
        "stored_bytes": _i64(entry.stored_bytes),
        "raw_bytes": _i64(entry.raw_bytes),
        "history_count": _i64(history.count) if history else 0,
        "meta_json": _meta_json(entry.meta),
    }


def _legacy_slice_row(event_id: int, entry: LegacySlice) -> Row:
    """
    Eski düzendeki bir dilimin satırı. `fetched_at` yük dosyasının mtime'ı; `checked_at` bilinen son
    denemedir (yükün, "veri yok" işaretinin ve hata işaretinin zamanlarından en yenisi). Sıkıştırılmamış
    boyut dosya okunmadan bilinemez: `raw_bytes` NULL, `stored_bytes` dosyanın boyutudur.
    """
    error = entry.error
    fetched_at = derive.epoch_seconds(entry.fetched_at) if entry.has_payload else None
    error_at = derive.epoch_seconds(error.at) if error else None
    seen = [t for t in (fetched_at, derive.epoch_seconds(entry.empty_at), error_at) if t is not None]
    return {
        "event_id": event_id,
        "key": entry.key,
        "sub": "",
        "state": entry.state,
        "has_payload": int(entry.has_payload),
        "fetched_at": fetched_at,
        "checked_at": max(seen) if seen else None,
        "empty_count": _i64(entry.empty_count),
        "unverified_empty_count": _i64(entry.unverified_empty_count),
        "error_reason": error.reason if error else None,
        "error_status": _i64(error.status) if error else None,
        "error_at": error_at,
        "error_count": _i64(error.count) if error else 0,
        "stored_bytes": _i64(entry.size),
        "raw_bytes": None,
        "history_count": 0,
        "meta_json": None,
    }


def _record(event_id: int, layout_name: str, payload: Dict[str, Any], *, observed_at: derive.Timestamp,
            storage: Row, slices: Iterable[Row], payload_at: Optional[int]) -> EventRecord:
    row = derive.event_row(payload, "event", observed_at)
    if row["id"] != event_id:
        raise LayoutError(f"Olay yükündeki id ({row['id']!r}) dizine ({event_id}) eşit değil",
                          detail=f"id {row['id']!r}, dizin {event_id}")
    row.update(storage)
    entities = derive.event_entity_rows(payload, updated_at=payload_at)
    for found in (entities.sport, entities.category, entities.tournament, entities.season, *entities.participants):
        if found is not None:
            _storable(found)
    return EventRecord(
        event_id=event_id,
        layout=layout_name,
        event=_storable(row),
        slices=tuple(_storable(s) for s in slices),
        links=tuple(derive.event_participant_rows(row)),
        entities=entities,
    )


def v3_event_record(event: V3Event, *, legacy_path: Optional[str] = None) -> EventRecord:
    """
    v3 maçının katalog satırları. `first_seen_at` / `updated_at` manifestten (`created_at`, `updated_at`),
    gözlem manifestin `observation` alanından gelir. `legacy_path`: aynı maçın hâlâ diskte duran eski dizini.
    """
    found = event.manifest
    observation = found.observation
    updated_at = derive.epoch_seconds(found.updated_at)
    payload_at = derive.epoch_seconds(found.slices[EVENT_KEY].fetched_at)
    storage = {
        "status_regressed": int(bool(observation and observation.status_regressed)),
        "layout": LAYOUT_V3,
        "path": None,
        "legacy_path": legacy_path,
        "sig": event.sig,
        "first_seen_at": derive.epoch_seconds(found.created_at),
        "updated_at": updated_at,
    }
    return _record(
        event.event_id, LAYOUT_V3, event.event,
        observed_at=observation.observed_at if observation else None,
        storage=storage,
        slices=[_v3_slice_row(event.event_id, name, entry) for name, entry in found.slices.items()],
        payload_at=payload_at if payload_at is not None else updated_at,
    )


def legacy_event_record(event: LegacyEvent) -> EventRecord:
    """
    Eski düzendeki bir maçın katalog satırları (bölüm 5.2). `observed_at` observation.json'dan gelir, dosya
    yoksa NULL'dır. Manifest olmadığı için `first_seen_at` / `updated_at` yük dosyalarının en eski / en
    yeni mtime'ıdır; `sig` dizin imzasıdır.
    """
    observation = event.observation
    slices = [_legacy_slice_row(event.event_id, entry) for entry in event.slices]
    times = [row["fetched_at"] for row in slices if row["fetched_at"] is not None]
    storage = {
        "status_regressed": int(bool(observation and observation.status_regressed)),
        "layout": LAYOUT_LEGACY,
        "path": event.dir.path,
        "legacy_path": None,
        "sig": event.dir.sig,
        "first_seen_at": min(times) if times else 0,
        "updated_at": max(times) if times else 0,
    }
    return _record(
        event.event_id, LAYOUT_LEGACY, dict(event.event),
        observed_at=observation.observed_at if observation else None,
        storage=storage,
        slices=slices,
        payload_at=slices[0]["fetched_at"] if slices[0]["fetched_at"] is not None else storage["updated_at"],
    )


def read_legacy_winner(reader: LegacyReader, candidates: Sequence[LegacyEventDir], *,
                       problems: Optional[List[IndexProblem]] = None,
                       superseded: Optional[List[SupersededDir]] = None) -> Optional[LegacyEvent]:
    """
    Öncelik sırasındaki adaylardan okunabilen ilki (LegacyReader.iter_events'in bir maç için yaptığı).
    Okunamayan aday sorun olarak, kazananın ardındaki adaylar `superseded` olarak bildirilir.
    """
    for position, candidate in enumerate(candidates):
        try:
            event = reader.read_event(candidate, payloads=False)
        except StoreError as exc:
            if problems is not None:
                problems.append(problem_of(exc, candidate.path, LAYOUT_LEGACY))
            continue
        if problems is not None:
            problems.extend(_from_legacy(event.problems))
        if superseded is not None:
            superseded.extend(SupersededDir(event.event_id, other.path, candidate.path, SUPERSEDED_BY_LEGACY)
                              for other in candidates[position + 1:])
        return event
    return None


def v3_record(data_dir: PathLike, event_id: int, *, candidates: Sequence[LegacyEventDir] = (),
              problems: Optional[List[IndexProblem]] = None,
              superseded: Optional[List[SupersededDir]] = None) -> Optional[EventRecord]:
    """
    Maçın v3 dizininden kaydı; dizin geçerli bir v3 maçı değilse sorun bildirilir ve None döner.
    candidates: aynı maçın eski düzen dizinleri, öncelik sırasında; ilki `legacy_path` olur, hepsi
    `superseded` olarak bildirilir (içerikleri okunmaz).
    """
    rel = layout.event_dir(event_id)
    try:
        record = v3_event_record(read_v3_event(data_dir, event_id),
                                 legacy_path=candidates[0].path if candidates else None)
    except _DERIVE_ERRORS as exc:
        if problems is not None:
            problems.append(problem_of(exc, rel, LAYOUT_V3))
        return None
    if superseded is not None:
        superseded.extend(SupersededDir(event_id, c.path, rel, SUPERSEDED_BY_V3) for c in candidates)
    return record


def legacy_record(reader: LegacyReader, candidates: Sequence[LegacyEventDir], *,
                  problems: Optional[List[IndexProblem]] = None,
                  superseded: Optional[List[SupersededDir]] = None) -> Optional[EventRecord]:
    """Maçın eski düzendeki kaydı: öncelik sırasındaki adaylardan okunabilen ilki; hiçbiri okunamıyorsa None."""
    replaced: List[SupersededDir] = []
    winner = read_legacy_winner(reader, candidates, problems=problems, superseded=replaced)
    if winner is None:
        return None
    try:
        record = legacy_event_record(winner)
    except _DERIVE_ERRORS as exc:
        if problems is not None:
            problems.append(problem_of(exc, winner.path, LAYOUT_LEGACY))
        return None
    if superseded is not None:
        superseded.extend(replaced)
    return record


def event_record(data_dir: PathLike, reader: LegacyReader, event_id: int, *, has_v3: bool,
                 candidates: Sequence[LegacyEventDir] = (),
                 problems: Optional[List[IndexProblem]] = None,
                 superseded: Optional[List[SupersededDir]] = None) -> Optional[EventRecord]:
    """
    Bir maçın, dosyalara göre katalogda durması gereken satırları: önce v3 dizini, o yoksa ya da
    okunamıyorsa öncelik sırasındaki eski düzen adayları. Hiçbiri okunamıyorsa None.
    """
    if has_v3:
        record = v3_record(data_dir, event_id, candidates=candidates, problems=problems, superseded=superseded)
        if record is not None:
            return record
    return legacy_record(reader, candidates, problems=problems, superseded=superseded)


# --- kataloğa yazma -----------------------------------------------------------------------------------

_PARTICIPANT_UPSERT = (
    "INSERT INTO participants ({columns}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates} "
    "WHERE excluded.updated_at >= participants.updated_at"
).format(
    columns=", ".join(_PARTICIPANT_COLUMNS),
    marks=", ".join("?" for _ in _PARTICIPANT_COLUMNS),
    updates=", ".join(f"{name} = excluded.{name}" for name in _PARTICIPANT_COLUMNS if name != "id"),
)


class RowWriter:
    """
    Kayıtları kataloğa yazar; `Catalog.write()` bloğunun içinde kullanılır. Satırlar toplu yazılır
    (`flush`). `fresh=True`: tablolar boş (yeniden kurma), maçın eski satırları silinmez.
    """

    def __init__(self, cat: Catalog, *, fresh: bool = False, batch: int = _BATCH_EVENTS) -> None:
        self._cat = cat
        self._fresh = fresh
        self._batch = batch
        self._pending: List[EventRecord] = []
        self.events = 0
        self.slices = 0

    def add(self, record: EventRecord) -> None:
        self._pending.append(record)
        self.events += 1
        self.slices += len(record.slices)
        if len(self._pending) >= self._batch:
            self.flush()

    def flush(self) -> None:
        records, self._pending = self._pending, []
        if not records:
            return
        cat = self._cat
        conn = cat.connection()
        if not self._fresh:
            ids = [(record.event_id,) for record in records]
            conn.executemany("DELETE FROM event_slices WHERE event_id = ?", ids)
            conn.executemany("DELETE FROM event_participants WHERE event_id = ?", ids)
        cat.upsert("events", (r.event for r in records))
        cat.upsert("event_slices", (s for r in records for s in r.slices))
        cat.upsert("event_participants", (link for r in records for link in r.links))
        for table, pick in (("sports", lambda e: e.sport), ("categories", lambda e: e.category),
                            ("tournaments", lambda e: e.tournament), ("seasons", lambda e: e.season)):
            rows = [row for row in (pick(r.entities) for r in records) if row is not None]
            cat.upsert(table, rows, on_conflict="ignore")
        conn.executemany(
            _PARTICIPANT_UPSERT,
            [tuple(row[name] for name in _PARTICIPANT_COLUMNS) for r in records for row in r.entities.participants],
        )


def delete_event(cat: Catalog, event_id: int) -> bool:
    """
    Maç dizini kalmayan maçın `events`, `event_slices` ve `event_participants` satırlarını siler; `write()`
    bloğunun içinde çağrılır. Yalnızca listeden gelen satıra (maç dizini hiç olmamış) dokunmaz.
    """
    conn = cat.connection()
    conn.execute("DELETE FROM event_slices WHERE event_id = ?", (event_id,))
    removed = conn.execute(
        "DELETE FROM events WHERE id = ? AND (has_event_payload = 1 OR layout IS NOT NULL)", (event_id,)).rowcount
    if removed:
        conn.execute("DELETE FROM event_participants WHERE event_id = ?", (event_id,))
    return removed > 0


# --- yönetim ------------------------------------------------------------------------------------------

class CatalogAdmin:
    """
    Kataloğun yönetimi (bölüm 2.3): yeniden kurma, tek maçı yeniden dizinleme, doğrulama, sayımlar.

    catalog: paylaşılan Catalog nesnesi (Store cephesi verir); verilmezse DATA_DIR/.meta/catalog.db için
    yenisi açılır ve `close()` onu kapatır. clock: `built_at` ve onarım işaretleri için saat.
    """

    def __init__(self, data_dir: PathLike, catalog: Optional[Catalog] = None, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.data_dir = os.path.abspath(os.fspath(data_dir))
        self._owns_catalog = catalog is None
        self.catalog = catalog if catalog is not None else Catalog(catalog_mod.catalog_path(self.data_dir))
        self.reader = LegacyReader(self.data_dir)
        self._clock = clock

    def __enter__(self) -> "CatalogAdmin":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_catalog:
            self.catalog.close()

    def now(self) -> float:
        return float(self._clock())

    # -- yeniden kurma -----------------------------------------------------------------------------

    def ensure(self, *, progress: Optional[Progress] = None) -> Optional[RebuildReport]:
        """
        Açılışta çağrılır: katalog yoksa, şema ya da türetme sürümü farklıysa ya da dosya okunamıyorsa
        yeniden kurar ve raporu döndürür; katalog kullanılabilir durumdaysa hiçbir şey yapmaz (None).
        """
        if self.catalog.inspect().usable:
            return None
        return self.rebuild(progress=progress)

    def rebuild(self, *, progress: Optional[Progress] = None,
                should_stop: Optional[Callable[[], bool]] = None, mode: str = MODE_AUTO) -> RebuildReport:
        """
        Kataloğu dosyalardan yeniden kurar (bölüm 3.4).

        mode="auto": katalog açılıyor ve şeması uyuyorsa yerinde, aksi halde (dosya yok, bozuk, başka
        şema sürümü) yeniden yaratarak. "in_place" / "recreate" kipi zorlar; şeması uymayan dosya yerinde
        kurulamaz (StoreError).
        progress(aşama, biten, toplam): "scan", "v3_events", "legacy_events", "finish".
        should_stop(): True dönerse kurulum bırakılır, eski katalog olduğu gibi kalır (`completed=False`).
        """
        if mode not in (MODE_AUTO, MODE_IN_PLACE, MODE_RECREATE):
            raise ValueError(f"Geçersiz yeniden kurma kipi: {mode!r}")
        started = time.monotonic()
        state = self.catalog.inspect()
        in_place_possible = state.exists and state.schema_ok
        automatic = mode == MODE_AUTO
        if automatic:
            mode = MODE_IN_PLACE if in_place_possible else MODE_RECREATE
        elif mode == MODE_IN_PLACE and not in_place_possible:
            if state.rebuild_reason != catalog_mod.REBUILD_MISSING:
                raise StoreError(
                    f"Katalog yerinde yeniden kurulamaz ({state.rebuild_reason}); yeniden yaratılmalı: "
                    f"{self.catalog.path}", path=self.catalog.path, detail=state.detail)
            self.catalog.prepare()
        report = RebuildReport(mode=mode, reason=state.rebuild_reason)
        try:
            if mode == MODE_IN_PLACE:
                try:
                    with self.catalog.write():
                        self.catalog.clear()
                        self._fill(self.catalog, report, progress, should_stop)
                except CatalogCorrupt as exc:
                    if not automatic:
                        raise
                    # Başlık sağlam görünüyordu ama sayfalar bozuk: dosya baştan yaratılır
                    logger.warning(f"Katalog yerinde kurulamadı ({_detail(exc)}); yeniden yaratılıyor: "
                                   f"{self.catalog.path}")
                    report = RebuildReport(mode=MODE_RECREATE, reason=catalog_mod.REBUILD_CORRUPT)
                    self._recreate(report, progress, should_stop)
            else:
                self._recreate(report, progress, should_stop)
        except _Stopped:
            report.completed = False
            logger.info(f"Katalog kurulumu durduruldu; eski katalog olduğu gibi kaldı: {self.catalog.path}")
        else:
            report.completed = True
        report.seconds = time.monotonic() - started
        if report.completed:
            logger.info(
                f"Katalog yeniden kuruldu ({report.mode}): {report.events} maç ({report.events_v3} v3, "
                f"{report.events_legacy} eski düzen), {report.slices} dilim, {len(report.problems)} sorun, "
                f"{report.seconds:.2f} sn"
            )
        return report

    def _recreate(self, report: RebuildReport, progress: Optional[Progress],
                  should_stop: Optional[Callable[[], bool]]) -> None:
        main = self.catalog
        build_path = main.path + BUILD_SUFFIX
        self._remove_database(build_path)  # yarıda kalmış bir önceki kurulum
        build = Catalog(build_path, busy_timeout_ms=main.busy_timeout_ms)
        try:
            build.prepare()
            with build.write():
                self._fill(build, report, progress, should_stop)
            # WAL'daki her şey ana dosyaya: yerine konacak olan tek dosyadır
            build.connection().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except BaseException:
            build.close()
            self._remove_database(build_path)
            raise
        build.close()
        main.close()  # eski dosya açıkken Windows'ta yerine konamaz
        try:
            # Eski WAL, yeni dosyanın yanında kalırsa SQLite onu yeni veritabanına uygular: önce silinir
            for sidecar in catalog_mod.sidecar_paths(main.path):
                files.remove(sidecar)
            files.replace(build_path, main.path)
        except StoreError as exc:
            self._remove_database(build_path)
            raise StoreError(
                f"Katalog yeniden yaratılamadı; catalog.db başka bir süreçte açık olabilir "
                f"({_detail(exc)}): {main.path}", path=main.path, errno_code=exc.errno,
                detail=_detail(exc)) from exc

    @staticmethod
    def _remove_database(path: str) -> None:
        for name in (path, *catalog_mod.sidecar_paths(path)):
            files.remove(name)

    def _fill(self, cat: Catalog, report: RebuildReport, progress: Optional[Progress],
              should_stop: Optional[Callable[[], bool]]) -> None:
        """Boş tabloları doldurur (bölüm 3.4, adım 4, 5 ve 8); `cat.write()` bloğunun içinde çağrılır."""

        def tick(stage: str, done: int, total: int, *, force: bool = False) -> None:
            if should_stop is not None and should_stop():
                raise _Stopped()
            if progress is not None and (force or done == total or done % _PROGRESS_EVERY == 0):
                progress(stage, done, total)

        tick(STAGE_SCAN, 0, 1, force=True)
        v3_dirs = scan_v3_events(self.data_dir, report.problems)
        groups = legacy_candidates(self.reader, report.problems)
        tick(STAGE_SCAN, 1, 1, force=True)

        writer = RowWriter(cat, fresh=True)
        indexed_v3 = set()
        for done, (event_id, _rel) in enumerate(v3_dirs, start=1):
            record = v3_record(self.data_dir, event_id, candidates=groups.get(str(event_id), ()),
                               problems=report.problems, superseded=report.superseded)
            if record is not None:
                writer.add(record)
                indexed_v3.add(event_id)
                report.events_v3 += 1
            tick(STAGE_V3_EVENTS, done, len(v3_dirs))

        for done, (name, candidates) in enumerate(groups.items(), start=1):
            # v3 kopyası dizinlenen maçın eski dizini okunmaz. Kimliği kurallı olmayan dizin de okunur:
            # okuyucu uyuşmazlığı sorun olarak bildirir.
            if canonical_id(name) not in indexed_v3:
                record = legacy_record(self.reader, candidates, problems=report.problems,
                                       superseded=report.superseded)
                if record is not None:
                    writer.add(record)
                    report.events_legacy += 1
            tick(STAGE_LEGACY_EVENTS, done, len(groups))
        writer.flush()

        tick(STAGE_FINISH, 0, 1, force=True)
        report.events = writer.events
        report.slices = writer.slices
        conn = cat.connection()
        conn.execute("ANALYZE main")  # yalnızca katalog: ATTACH edilmiş state.db'nin istatistikleri değişmesin
        report.counts = self._counts(cat)
        cat.set_meta(META_BUILT_AT, str(int(self.now())))
        cat.set_meta(META_BUILT_BY, APP_VERSION)
        cat.set_meta(META_BUILD_MODE, report.mode)
        cat.set_meta(META_COUNTS, json.dumps(report.counts, sort_keys=True))
        cat.stamp_derive_version()
        tick(STAGE_FINISH, 1, 1, force=True)

    @staticmethod
    def _counts(cat: Catalog) -> Dict[str, int]:
        conn = cat.connection()
        return {table: int(conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
                for table in cat.tables() if table != "meta"}

    # -- tek maç -----------------------------------------------------------------------------------

    def index_event(self, event_id: int, *, paths: Sequence[str] = (),
                    candidates: Optional[Sequence[LegacyEventDir]] = None,
                    problems: Optional[List[IndexProblem]] = None) -> Optional[str]:
        """
        Bir maçı dosyalardan yeniden dizinler ve düzenini döndürür ("v3" | "legacy"); maçın geçerli bir
        dizini kalmadıysa satırlarını siler ve None döner. Kendi yazma işlemini açar (açık bir `write()`
        bloğunun içinde çağrılırsa ona katılır).

        Maçın eski düzen dizinleri bütün ağaç taranmadan bulunur: katalog satırındaki `path` ve
        `legacy_path` ile çağıranın bildiği dizinler (`paths`, DATA_DIR'e göre; ör. yazıcının az önce yazdığı
        dizin). Başka bir yerdeki yeni kopyayı bulmak tam taramanın işidir. candidates: tam tarama yapmış
        çağıranın verdiği, öncelik sırasındaki adaylar; verilirse yalnızca onlar kullanılır.
        """
        cat = self.catalog
        with cat.write():
            if candidates is None:
                row = cat.connection().execute(
                    "SELECT path, legacy_path FROM events WHERE id = ?", (event_id,)).fetchone()
                known = list(paths) + ([row["path"], row["legacy_path"]] if row is not None else [])
                found = [self.reader.event_dir_at(path) for path in dict.fromkeys(p.strip("/") for p in known if p)]
                candidates = sorted((c for c in found if c is not None and c.name == str(event_id)),
                                    key=legacy_order)
            manifest_file = layout.resolve(self.data_dir, layout.manifest_path(layout.event_dir(event_id)))
            has_v3 = file_signature(manifest_file) is not None
            record = event_record(self.data_dir, self.reader, event_id, has_v3=has_v3, candidates=candidates,
                                  problems=problems)
            if record is None:
                delete_event(cat, event_id)
                return None
            writer = RowWriter(cat)
            writer.add(record)
            writer.flush()
            return record.layout

    # -- doğrulama ve sayımlar ---------------------------------------------------------------------

    def verify(self, *, deep: bool = False, repair: bool = False) -> "VerifyReport":
        """Tutarlılık denetimi (bölüm 3.6); ayrıntı src/store/verify.py'de. VerifyReport döndürür."""
        from src.store import verify as verify_mod  # döngüsel içe aktarma: verify bu modülü kullanır

        return verify_mod.verify(self, deep=deep, repair=repair)

    def stats(self) -> Dict[str, Any]:
        """
        Kataloğun hali ve sayımları (elle bakmak ve `Store.info` için). Hiçbir şey yazmaz, dosya yoksa
        yaratmaz; katalog bu kodun şemasında değilse yalnızca dosyanın hali döner.
        """
        cat = self.catalog
        state = cat.inspect()
        out: Dict[str, Any] = {
            "path": cat.path,
            "exists": state.exists,
            "usable": state.usable,
            "rebuild_reason": state.rebuild_reason,
            "schema_version": state.schema_version,
            "derive_version": state.derive_version,
            "size_bytes": None,
        }
        if not state.exists:
            return out
        try:
            out["size_bytes"] = os.stat(cat.path).st_size
        except OSError:
            pass
        if not state.schema_ok:
            return out
        with cat.read() as conn:
            def grouped(sql: str) -> Dict[str, int]:
                return {str(key): int(count) for key, count in conn.execute(sql).fetchall()}

            out["journal_mode"] = cat.journal_mode
            out["meta"] = {str(key): str(value) for key, value in conn.execute("SELECT key, value FROM meta")}
            out["tables"] = self._counts(cat)
            out["events"] = {
                "by_layout": grouped("SELECT coalesce(layout, 'listing'), count(*) FROM events GROUP BY 1 ORDER BY 1"),
                "by_status_class": grouped("SELECT status_class, count(*) FROM events GROUP BY 1 ORDER BY 1"),
                "by_sport": grouped("SELECT sport, count(*) FROM events GROUP BY 1 ORDER BY 1"),
                "with_event_payload": int(conn.execute(
                    "SELECT count(*) FROM events WHERE has_event_payload = 1").fetchone()[0]),
                "observed": int(conn.execute(
                    "SELECT count(*) FROM events WHERE has_event_payload = 1 AND observed_at IS NOT NULL"
                ).fetchone()[0]),
                "superseded_legacy_dirs": int(conn.execute(
                    "SELECT count(*) FROM events WHERE legacy_path IS NOT NULL").fetchone()[0]),
            }
            out["slices"] = {
                "by_state": grouped("SELECT state, count(*) FROM event_slices GROUP BY 1 ORDER BY 1"),
                "with_payload": int(conn.execute(
                    "SELECT count(*) FROM event_slices WHERE has_payload = 1").fetchone()[0]),
            }
        return out


__all__ = [
    "LAYOUT_V3",
    "LAYOUT_LEGACY",
    "MODE_AUTO",
    "MODE_IN_PLACE",
    "MODE_RECREATE",
    "BUILD_SUFFIX",
    "EVENT_STORAGE_COLUMNS",
    "SLICE_COLUMNS",
    "IndexProblem",
    "SupersededDir",
    "V3Event",
    "EventRecord",
    "RebuildReport",
    "RowWriter",
    "CatalogAdmin",
    "problem_of",
    "canonical_id",
    "file_signature",
    "scan_v3_events",
    "legacy_order",
    "legacy_candidates",
    "read_v3_event",
    "v3_event_record",
    "legacy_event_record",
    "read_legacy_winner",
    "v3_record",
    "legacy_record",
    "event_record",
    "delete_event",
]
