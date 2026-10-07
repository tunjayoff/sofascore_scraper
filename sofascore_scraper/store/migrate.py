"""
Eski (2.x) düzenden v3 düzenine taşıma: `Store.migrate` (docs/design/01-storage.md, bölüm 5.4).

Taşıma açık bir komuttur (`ssc migrate`); açılışta kendiliğinden çalışmaz. Varsayılan davranış: **dönüştür ve
doğrula, eski kopyayı bırak**. `delete_legacy=True` her eski kopyayı v3 kopyası yeniden doğrulandıktan sonra
siler; iki adım ayrı zamanlarda da çalışabilir: sonraki bir `delete_legacy` çalışması yalnızca dönüştürülmüş
maçların eski kopyalarını siler.

İş listesi katalogdan gelir; ayrı bir günlük yoktur, taşımanın durumu dosyaların durumudur:

  * maçlar: `layout = 'legacy'` satırları (dönüştürülecek) ve `legacy_path`'i dolu v3 satırları (eski kopyası
    diskte duran, dönüştürülmüş ya da yükseltilmiş maçlar; yalnızca `delete_legacy` ile);
  * program sayfaları: v3 kopyası olmayan eski düzen sayfaları (`entity_slices`, `layout = 'legacy'`), sezon
    başına tek `EntityStore.put` ile; sayfa katalog satırının yolundan okunur, yeniden kurulmuş bir addan değil
    (iki eski dosya aynı alt anahtara katlanabilir; geçersiz olan geçerlinin üzerine yazılmaz, bölüm 5.1);
  * sezon listeleri: v3 listesi olmayan turnuvanın geçerli (en yeni) eski listesi;
  * değişiklik günlüğü: `score_changes.jsonl` baytı baytına `changes/0000-legacy.jsonl`e kopyalanır
    (sofascore_scraper/store/changes.py, `copy_legacy`); satır, dolayısıyla sıra numaraları değişmez.

Bir maçın adımları (her adım, bir sonraki çalışmanın sürebileceği bir durum bırakır):

  1. Eski dizinin bütün dosyaları okunur. Dilim, `observation.json`, `_unavailable.json` ya da
     `_slice_status.json` olmayan dosyalar (ve okunamayan dilim dosyaları, birleşik dosyanın tanınmayan
     anahtarları varsa birleşik dosya) yeni dizinin `_extra/` alt dizinine olduğu gibi kopyalanır ve raporda
     listelenir. Yarıda kalmış geçici dosyalar (`.<ad>.<rastgele>.tmp`) kopyalanmaz.
  2. v3 dizini `.meta/tmp/writer.<rastgele>` altında kurulur (karar S16: hazırlık girdisi sahibinin adını
     taşır; taşıma `writer` kilidini tutar): dilim başına bir `.json.gz` ve `migrated_from` taşıyan manifest.
  3. Hazırlanan dizin doğrulanır: her dosya diskten geri okunur, açılır, ayrıştırılır ve eski nesneyle
     karşılaştırılır; sha256 yeniden hesaplanıp manifesttekiyle karşılaştırılır; `_extra/` dosyaları bayt
     olarak karşılaştırılır. Uyuşmazlıkta hazırlık silinir, eski dizine dokunulmaz, maç başarısız sayılır ve
     çalışma sürer.
  4. Tek yeniden adlandırmayla yerine konur. v3 dizini zaten varsa (önceden yükseltilmiş ya da önceki çalışma
     bu adımdan sonra durmuş) hiçbir şeyin üzerine yazılmaz: eski dilimler v3 dilimleriyle karşılaştırılır ve
     bir dilim yalnızca v3'teki daha yeniyse farklı olabilir (değilse çakışma olarak bildirilir).
  5. Katalog: maç dosyalarından yeniden dizinlenir (`layout = 'v3'`, `legacy_path` = eski dizin).
  6. Yalnızca `delete_legacy` ile: yayımlanmış dosyalar manifestlerine göre bir kez daha doğrulanır, eski
     kopyanın her dilimi v3'te aynıdır ya da v3'teki daha yenidir, `_extra/` eksiksizdir (yükseltilmiş maçta
     eksikse şimdi kopyalanır); sonra eski dizin `.meta/trash/` altına taşınır, orada silinir ve maç yeniden
     dizinlenir (`legacy_path` boşalır ya da aynı maçın kalan eski kopyasını gösterir). Önce taşımak, eski
     ağaçta yarım silinmiş bir dizin bırakmaz. Eski düzen ağaçlarındaki boş sezon ve lig dizinleri çalışmanın
     sonunda silinir (köklerin kendisi ve `match_details/processed/` kalır).

Bir maçın 1-6 adımları maça yazmanın çerçevesinde çalışır (`EventStore._entity_write`, bölüm 6.2): yarım yazma
işareti, kataloğun yazma kilidi, iş, işaretin silinmesi, commit. Arada ölen süreçten sonra bir sonraki açılış
(ya da bir sonraki taşıma) maçı dosyalarından toparlar; hazırlık girdileri `writer` kilidi alınınca, çöp
`writer` kilidi alınınca boşaltılır (sofascore_scraper/store/lease.py).

Türetilmiş dosyalar (sezon özetleri `*_summary.{json,csv}`, `*_matches.csv`, `match_details/processed/*`)
taşınmaz; kuru çalıştırmada "türetilmiş" olarak listelenir ve yalnızca `purge_derived=True` ile silinir. Tur
ya da sayfa dosyası olmayan, yalnızca özeti olan sezon dönüştürülemez: yerinde kalır, okunmaya devam eder ve
bildirilir; onun özet dosyaları `purge_derived` ile de silinmez. İzleyici dosyaları (`watch_events.jsonl`,
`watch_state_*.json`) taşınmaz (bölüm 5.4).

`plan()` diske hiçbir şey yazmaz (kuru çalıştırma): sayımlar, önceki bayt, sonraki baytın tahmini (200 maçlık
bir örnek bellekte sıkıştırılıp genellenir; `exact=True` hepsini sıkıştırır), tanınmayan dosyalar, çakışmalar,
dönüştürülemeyen sezonlar. Aynı ağaçta `plan()` ile ardından gelen `run()` aynı sayıları verir.

`run()` `writer` kilidini, sonra `live` kilidini alır (bölüm 6.1): bir indirme, canlı servis ya da izleyici
çalışırken LeaseHeld ile reddedilir. Önce katalog dosyalarla uzlaştırılır (yarım yazmalar ve değişiklik
satırı niyetleri kapanır). Her çalışma `state.db`'nin `migration_runs` tablosunda bir satır bırakır.
`limit` ve `should_stop` çalışmayı iki maç arasında durdurur; yeniden çalıştırmak kaldığı yerden sürer, çünkü
iş listesi katalogdan yeniden hesaplanır. Bitmiş bir maç iki kez dönüştürülmez.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import changes as changes_mod
from sofascore_scraper.store import codec, files, indexer, layout, legacy
from sofascore_scraper.store import entities as entities_mod
from sofascore_scraper.store import manifest as manifest_mod
from sofascore_scraper.store.entities import KEY_SCHEDULE, KEY_SEASONS
from sofascore_scraper.store.errors import LayoutError, StoreError
from sofascore_scraper.store.events import Ref, manifest_from_legacy
from sofascore_scraper.store.legacy import LegacyEvent, LegacyEventDir, LegacyReader, LegacyReport, LegacySchedulePage
from sofascore_scraper.store.lease import LIVE, WRITER, Lease
from sofascore_scraper.store.manifest import Manifest

if TYPE_CHECKING:
    from sofascore_scraper.store.api import Store

logger = logging.getLogger(__name__)

MIGRATE_PURPOSE = "op:migrate"  # `writer` ve `live` kilitlerinin amacı (reddedilen süreç bunu görür)
SAMPLE_EVENTS = 200  # kuru çalıştırmanın boyut tahmini için bellekte dönüştürülen maç sayısı

# Bir maçın adımları (`Migrator._checkpoint`): testler süreci burada "öldürür"
STEP_READ = "read"  # 1: eski dizin okundu
STEP_STAGED = "staged"  # 2: v3 dizini hazırlık alanında kuruldu
STEP_VERIFIED = "verified"  # 3: hazırlık geri okunarak doğrulandı
STEP_PUBLISHED = "published"  # 4: yerine kondu
STEP_INDEXED = "indexed"  # 5: katalog satırları yazıldı (commit'ten önce)
STEP_TRASHED = "trashed"  # 6: eski dizin çöpe taşındı
STEP_DELETED = "deleted"  # 6: çöpteki dizin silindi
EVENT_STEPS: Tuple[str, ...] = (STEP_READ, STEP_STAGED, STEP_VERIFIED, STEP_PUBLISHED, STEP_INDEXED,
                                STEP_TRASHED, STEP_DELETED)

# Aşamalar (`MigrationProgress.stage`)
STAGE_EVENTS = "events"
STAGE_SCHEDULES = "schedules"
STAGE_SEASON_LISTS = "season_lists"
STAGE_CHANGE_LOG = "change_log"
STAGE_DERIVED = "derived"

# `MigrationIssue.kind`
ISSUE_FAILED = "failed"  # dönüştürülemedi (okunamadı, doğrulanamadı, yazılamadı); eski kopya yerinde
ISSUE_CONFLICT = "conflict"  # v3 kopyası eskisiyle uyuşmuyor ve daha yeni değil; hiçbir şeyin üzerine yazılmadı
ISSUE_KEPT = "kept"  # eski kopya silinmedi (doğrulanamadı); `delete_legacy`
ISSUE_UNCONVERTIBLE = "unconvertible"  # yalnızca özet dosyası olan sezon
ISSUE_UNRESOLVED = "unresolved"  # turnuvası bulunamayan sezon listesi dosyası
ISSUE_UNRECOGNISED = "unrecognised"  # eski ağaçta tanınmayan ya da okunamayan girdi (olay yükü olmayan dizin...)

Progress = Callable[["MigrationProgress"], None]


class _Rejected(StoreError):
    """Bir maçın ya da sayfanın taşınması (ya da eski kopyasının silinmesi) doğrulama yüzünden reddedildi."""


# --- sonuç türleri ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MigrationProgress:
    """İlerleme: aşama, biten iş ve toplam."""

    stage: str
    done: int
    total: int


@dataclass(frozen=True)
class MigrationIssue:
    """Raporun bir satırı: türü (`ISSUE_*`), neyle ilgili olduğu (maç kimliği, "<turnuva>/<sezon>"...), yol, açıklama."""

    kind: str
    key: str
    path: str = ""
    detail: str = ""


@dataclass
class MigrationPlan:
    """
    Kuru çalıştırmanın sonucu (`Migrator.plan`): bir `run()`ın aynı ağaçta yapacağı iş.

    events               dönüştürülecek maç (eski düzende)
    legacy_copies        eski kopyası silinecek maç (`delete_legacy`; dönüştürülecekler dahil)
    schedule_pages       v3'e yazılacak program sayfası; `schedule_seasons` onların sezonu
    season_lists         v3'e yazılacak sezon listesi
    change_log           değişiklik günlüğü kopyasının durumu (`changes.COPY_*`); `change_log_lines` satır sayısı
    extra_files          `_extra/`e kopyalanacak dosyalar
    derived_files        türetilmiş dosyalar (yalnızca `purge_derived` ile silinir)
    unconvertible        yalnızca özeti olan sezonlar ve turnuvası bulunamayan listeler
    conflicts            v3 kopyası eskisinden farklı ve daha yeni olmayan girdiler
    bytes_before         taşınacak eski dosyaların boyutu
    bytes_after          v3'te tutacakları yerin tahmini (`exact`: hepsi sıkıştırıldı; değilse `sampled` maçtan)
    stopped              iş listesi `limit` ile kesildi: maçlardan sonraki aşamalar bu çalışmada yapılmaz
    """

    tournaments: Tuple[int, ...] = ()
    limit: Optional[int] = None
    delete_legacy: bool = False
    purge_derived: bool = False
    exact: bool = False
    events: int = 0
    legacy_copies: int = 0
    schedule_pages: int = 0
    schedule_seasons: int = 0
    season_lists: int = 0
    change_log: str = changes_mod.COPY_NONE
    change_log_lines: int = 0
    extra_files: List[str] = field(default_factory=list)
    derived_files: List[str] = field(default_factory=list)
    unconvertible: List[MigrationIssue] = field(default_factory=list)
    conflicts: List[MigrationIssue] = field(default_factory=list)
    bytes_before: int = 0
    bytes_after: int = 0
    sampled: int = 0
    stopped: bool = False
    seconds: float = 0.0


@dataclass
class MigrationReport:
    """
    Bir taşıma çalışmasının sonucu (`Migrator.run`); sayımlar `MigrationPlan` ile aynı adları taşır.

    events                 v3'e dönüştürülen maç; `events_existing`: v3 kopyası zaten vardı (yalnızca dizinlendi)
    legacy_copies          silinen eski maç dizini; `legacy_kept`: silinmeyenler ve nedeni
    schedule_files_deleted, season_list_files_deleted, change_log_deleted: silinen eski liste dosyaları
    failed                 dönüştürülemeyen girdiler (eski kopya yerinde)
    derived_removed        silinen türetilmiş dosyalar (`purge_derived`)
    stopped                `limit` ya da `should_stop` çalışmayı iki maç arasında durdurdu
    """

    run_id: Optional[int] = None
    tournaments: Tuple[int, ...] = ()
    limit: Optional[int] = None
    delete_legacy: bool = False
    purge_derived: bool = False
    events: int = 0
    events_existing: int = 0
    legacy_copies: int = 0
    schedule_pages: int = 0
    schedule_seasons: int = 0
    season_lists: int = 0
    change_log: str = changes_mod.COPY_NONE
    change_log_lines: int = 0
    schedule_files_deleted: int = 0
    season_list_files_deleted: int = 0
    change_log_deleted: bool = False
    extra_files: List[str] = field(default_factory=list)
    derived_removed: List[str] = field(default_factory=list)
    unconvertible: List[MigrationIssue] = field(default_factory=list)
    conflicts: List[MigrationIssue] = field(default_factory=list)
    failed: List[MigrationIssue] = field(default_factory=list)
    legacy_kept: List[MigrationIssue] = field(default_factory=list)
    bytes_before: int = 0
    bytes_after: int = 0
    stopped: bool = False
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Hiçbir girdi başarısız olmadı ve çakışma yok (silinmeyen eski kopyalar da yok)."""
        return not (self.failed or self.conflicts or self.legacy_kept)


# --- yardımcılar ------------------------------------------------------------------------------------------

def _whole_second(epoch: Optional[float]) -> Optional[datetime]:
    """Dosya zamanı → UTC zaman, tam saniyeye indirilmiş (katalogdaki eski düzen değeriyle aynı)."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(math.floor(epoch), timezone.utc)


def _is_leftover(name: str) -> bool:
    """Atomik yazmanın yarıda kalmış geçici dosyası (`.<ad>.<rastgele>.tmp`): taşınmaz."""
    return name.startswith(".") and name.endswith(".tmp")


def _tree_size(path: str) -> int:
    """Dosyanın ya da dizinin (içindekilerle) bayt boyutu; yoksa 0."""
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for base, _dirs, names in os.walk(path):
        for name in names:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(base, name))
    return total


def _same_payload(stored: Any, original: Any) -> bool:
    """Geri okunan nesne eskisiyle aynı mı: derin eşitlik, NaN gibi kendine eşit olmayan değerler için kurallı baytlar."""
    if stored == original:
        return True
    try:
        return codec.canonical_bytes(stored) == codec.canonical_bytes(original)
    except StoreError:
        return False


@dataclass(frozen=True)
class _Extra:
    """`_extra/`e kopyalanacak bir dosya: eski dizine göre adı (`_extra/` altındaki adı da budur) ve gerçek yolu."""

    name: str
    source: str


def extra_files_of(reader: LegacyReader, event: LegacyEvent) -> List[_Extra]:
    """
    Maçın `_extra/`e kopyalanacak dosyaları (adım 1): tanınmayan dosyalar (alt dizinlerin dosyaları dahil),
    okunamayan dilim dosyaları ve tanınmayan anahtarları olan birleşik dosya. Yarıda kalmış geçici dosyalar ve
    sembolik bağlar atlanır.
    """
    base = reader.resolve(event.path)
    names: Set[str] = set()
    for entry in event.extra_files:
        name = entry.rstrip("/")
        path = os.path.join(base, name)
        if os.path.islink(path):
            continue
        if entry.endswith("/") or os.path.isdir(path):
            for root, dirs, inner in os.walk(path):
                dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
                for file_name in inner:
                    full = os.path.join(root, file_name)
                    if _is_leftover(file_name) or os.path.islink(full):
                        continue
                    names.add(os.path.relpath(full, base).replace(os.sep, "/"))
        elif not _is_leftover(name):
            names.add(name)
    for entry in event.slices:
        if not entry.has_payload and entry.path and not entry.in_combined:
            names.add(entry.path.rsplit("/", 1)[-1])  # okunamayan dilim dosyası: olduğu gibi saklanır
    if event.combined_extra_keys and event.dir.combined:
        names.add(f"{event.dir.name}.json")
    return [_Extra(name, os.path.join(base, *name.split("/"))) for name in sorted(names)]


def _extra_target(root: str, name: str) -> str:
    return os.path.join(root, layout.EXTRA_DIR_NAME, *name.split("/"))


def compare_with_v3(event: LegacyEvent, found: Manifest) -> List[str]:
    """
    Eski kopyanın verisi v3 kopyasında var mı (adım 4 ve 6): yükü olan her eski dilimin v3'te yükü olmalı ve
    özeti aynı olmalı ya da v3'teki daha yeni olmalıdır. Uyuşmayan dilimlerin açıklamaları; boşsa kopya
    doğrulanmıştır.
    """
    problems: List[str] = []
    payloads = event.payloads or {}
    for entry in event.slices:
        if not entry.has_payload:
            continue
        mine = found.slices.get(entry.key)
        if mine is None or not mine.has_payload:
            problems.append(f"{entry.key}: no payload in the v3 copy")
            continue
        try:
            digest = codec.sha256_hex(codec.canonical_bytes(payloads[entry.key]))
        except StoreError as exc:
            problems.append(f"{entry.key}: {exc}")
            continue
        if digest == mine.sha256:
            continue
        legacy_at = _whole_second(entry.fetched_at)
        if mine.fetched_at is not None and legacy_at is not None and mine.fetched_at >= legacy_at:
            continue  # v3'teki yük eski kopyadan sonra yazıldı
        problems.append(f"{entry.key}: differs from the v3 copy, which is not newer")
    return problems


def _bytes_in(found: Manifest, extras: Sequence[_Extra]) -> int:
    """Bir maçın v3 dizininin tahmini boyutu: yük dosyaları, manifest ve `_extra/`."""
    total = sum(entry.stored_bytes or 0 for entry in found.slices.values() if entry.has_payload)
    total += len(json.dumps(manifest_mod.to_dict(found), ensure_ascii=False, indent=2).encode("utf-8")) + 1
    total += sum(_tree_size(extra.source) for extra in extras)
    return total


# --- taşıyıcı ---------------------------------------------------------------------------------------------

@dataclass
class _Work:
    """İş listesi (katalogdan; bkz. modül açıklaması)."""

    convert: List[int] = field(default_factory=list)  # eski düzendeki maçlar
    copies: List[int] = field(default_factory=list)  # `legacy_path`'i dolu v3 maçları (delete_legacy)
    pages: Dict[Tuple[int, int], List[LegacySchedulePage]] = field(default_factory=dict)
    lists: List[legacy.LegacySeasonList] = field(default_factory=list)
    unconvertible: List[MigrationIssue] = field(default_factory=list)
    derived: List[str] = field(default_factory=list)
    candidates: Dict[str, List[LegacyEventDir]] = field(default_factory=dict)  # eski maç dizinleri, ada göre
    stopped: bool = False  # `limit` maçları kesti

    @property
    def events(self) -> List[Tuple[int, bool]]:
        """(maç kimliği, dönüştürülecek mi) çiftleri, kimliğe göre sıralı."""
        return sorted([(event_id, True) for event_id in self.convert] + [(event_id, False) for event_id in self.copies])


class Migrator:
    """Eski düzenden v3'e taşıma (`Store.migrate`; bölüm 5.4). Bkz. modül açıklaması."""

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._data_dir = str(store.data_dir)
        self._reader = LegacyReader(self._data_dir)
        self._candidates: Optional[Dict[str, List[LegacyEventDir]]] = None

    # -- son çalıştırma ------------------------------------------------------------------------------------

    def last_run(self) -> Optional[Dict[str, Any]]:
        """
        `migration_runs` tablosunun en son satırı (durum ekranları için), hiç taşıma çalışmadıysa None: {"id",
        "started_at", "finished_at" (epoch saniye; yarıda kalan çalışmada None), "delete_legacy", "events_done",
        "events_failed", "bytes_before", "bytes_after"}. Raporun kendisi (`report_json`) verilmez.
        """
        self._store._require_open()
        row = self._store._state.connection().execute(
            "SELECT id, started_at, finished_at, delete_legacy, events_done, events_failed, bytes_before, "
            "bytes_after FROM migration_runs WHERE dry_run = 0 ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"id": int(row[0]), "started_at": int(row[1]),
                "finished_at": int(row[2]) if row[2] is not None else None, "delete_legacy": bool(row[3]),
                "events_done": int(row[4]), "events_failed": int(row[5]), "bytes_before": int(row[6]),
                "bytes_after": int(row[7])}

    # -- kuru çalıştırma -----------------------------------------------------------------------------------

    def plan(self, *, tournaments: Iterable[int] = (), limit: Optional[int] = None, exact: bool = False,
             delete_legacy: bool = False, purge_derived: bool = False) -> MigrationPlan:
        """
        Kuru çalıştırma: aynı seçeneklerle bir `run()`ın yapacağı iş. Diske hiçbir şey yazmaz ve kilit almaz
        (katalog açılışta güncellenmiş haliyle okunur). tournaments: yalnızca bu turnuvalar (maçlar, sayfalar,
        listeler, özetler; değişiklik günlüğü ve `processed/` yalnızca seçimsiz çalışmada). limit: en çok bu
        kadar maç; liste kesilirse maçlardan sonraki aşamalar bu çalışmanın işi değildir. exact=True: sonraki
        boyut bütün maçlar bellekte sıkıştırılarak hesaplanır (yavaş); yoksa en çok `SAMPLE_EVENTS` maçlık
        örnekten genellenir.
        """
        started = time.monotonic()
        self._store._require_open()
        scope = _scope(tournaments)
        _check_limit(limit)
        self._candidates = None
        work = self._work(scope, limit, delete_legacy, purge_derived)
        plan = MigrationPlan(tournaments=scope, limit=limit, delete_legacy=delete_legacy,
                             purge_derived=purge_derived, exact=exact, stopped=work.stopped,
                             unconvertible=list(work.unconvertible), derived_files=list(work.derived))
        plan.events = len(work.convert)
        if delete_legacy:
            plan.legacy_copies = sum(self._readable_copies(work, event_id) for event_id, _ in work.events)

        sample = set(work.convert) if exact else set(_spread(work.convert, SAMPLE_EVENTS))
        estimated = 0
        for event_id in work.convert:
            event = self._legacy_event(event_id, payloads=event_id in sample)
            if event is None:
                continue
            extras = extra_files_of(self._reader, event)
            plan.extra_files.extend(f"{event.path}/{extra.name}" for extra in extras)
            plan.bytes_before += _tree_size(self._reader.resolve(event.path))
            if event_id in sample:
                try:
                    found, _encoded = manifest_from_legacy(event)
                except StoreError:
                    continue
                estimated += _bytes_in(found, extras)
                plan.sampled += 1
        if plan.sampled:
            plan.bytes_after += round(estimated * len(work.convert) / plan.sampled)

        if not work.stopped:
            for key, pages in sorted(work.pages.items()):
                plan.schedule_seasons += 1
                for page in pages:
                    plan.schedule_pages += 1
                    plan.bytes_before += page.size
                    with contextlib.suppress(StoreError):
                        plan.bytes_after += codec.encode(self._reader.read_schedule(page).payload).stored_bytes
            for item in work.lists:
                plan.season_lists += 1
                plan.bytes_before += _tree_size(self._reader.resolve(item.path)) if item.kind == "json" else 0
                with contextlib.suppress(StoreError):
                    plan.bytes_after += codec.encode(item.payload).stored_bytes
            if not scope:
                plan.change_log = changes_mod.legacy_copy_state(self._data_dir)
                if plan.change_log in (changes_mod.COPY_CREATED, changes_mod.COPY_EXTENDED):
                    size = _tree_size(layout.resolve(self._data_dir, changes_mod.LEGACY_SEGMENT))
                    plan.bytes_before += size
                    plan.bytes_after += size
                if plan.change_log == changes_mod.COPY_CONFLICT:
                    plan.conflicts.append(MigrationIssue(ISSUE_CONFLICT, "change_log", changes_mod.LEGACY_COPY,
                                                         "the copy is not a prefix of score_changes.jsonl"))
                plan.change_log_lines = len(self._reader.change_log())
        plan.conflicts.extend(self._event_conflicts(work.convert))
        plan.seconds = time.monotonic() - started
        return plan

    # -- çalıştırma ----------------------------------------------------------------------------------------

    def run(self, *, tournaments: Iterable[int] = (), limit: Optional[int] = None, delete_legacy: bool = False,
            purge_derived: bool = False, should_stop: Optional[Callable[[], bool]] = None,
            progress: Optional[Progress] = None) -> MigrationReport:
        """
        Taşır (modül açıklaması). `writer` ve `live` kilitlerini alır (bu süreç tutuyorsa onların altında
        çalışır); başka bir süreç tutuyorsa LeaseHeld. Katalog kullanılabilir değilse StoreError (önce
        `catalog rebuild`). Bir girdinin başarısızlığı çalışmayı durdurmaz: raporda listelenir.
        """
        started = time.monotonic()
        store = self._store
        store._require_open()
        if store.readonly:
            raise StoreError(f"A store opened read-only cannot be migrated: {self._data_dir}", path=self._data_dir)
        scope = _scope(tournaments)
        _check_limit(limit)
        report = MigrationReport(tournaments=scope, limit=limit, delete_legacy=delete_legacy,
                                 purge_derived=purge_derived)
        leases: List[Lease] = []
        try:
            for name in (WRITER, LIVE):
                if not store._leases.held_here(name):
                    leases.append(store.lease(name, purpose=MIGRATE_PURPOSE))
            # Yarım yazmalar ve değişiklik satırı niyetleri kapanır; iş listesi güncel kataloğa göre kurulur
            store.catalog.reconcile(quiet=True)
            report.run_id = self._record_start(delete_legacy)
            work = self._work(scope, limit, delete_legacy, purge_derived)
            self._candidates = work.candidates if delete_legacy else None
            report.unconvertible = list(work.unconvertible)
            report.stopped = work.stopped
            emptied: Set[str] = set()
            try:
                self._run_events(work, report, delete_legacy, emptied, should_stop, progress)
                if not report.stopped:
                    self._run_schedules(work, report, delete_legacy, emptied, progress)
                    self._run_season_lists(work, report, delete_legacy, scope, emptied, progress)
                    if not scope:
                        self._run_change_log(report, delete_legacy, progress)
                    if purge_derived:
                        self._purge_derived(work, report, emptied, progress)
            except Exception:
                with contextlib.suppress(StoreError):
                    self._remove_empty(emptied)  # yarıda kalan çalışmanın boşalttığı dizinler de gider
                raise
            self._remove_empty(emptied, sweep=delete_legacy or purge_derived)
        finally:
            for lease in reversed(leases):
                lease.release()
            self._candidates = None
        report.seconds = time.monotonic() - started
        self._record_finish(report)
        logger.info(
            "Migration finished in %s: %d events converted (%d already in v3), %d failed, %d legacy copies removed, "
            "%d schedule pages, %d season lists, change log %s, %d derived files removed, stopped=%s, %.2f s",
            self._data_dir, report.events, report.events_existing, len(report.failed), report.legacy_copies,
            report.schedule_pages, report.season_lists, report.change_log, len(report.derived_removed),
            report.stopped, report.seconds)
        return report

    def _checkpoint(self, step: str) -> None:
        """Bir maçın adımları arasında çağrılır (`STEP_*`). Hiçbir şey yapmaz; testler süreci burada öldürür."""

    # -- iş listesi ----------------------------------------------------------------------------------------

    def _work(self, scope: Tuple[int, ...], limit: Optional[int], delete_legacy: bool,
              purge_derived: bool) -> _Work:
        cat = self._store._catalog
        assert cat is not None
        where = ""
        params: List[Any] = []
        if scope:
            where = f" AND tournament_id IN ({', '.join('?' for _ in scope)})"
            params = list(scope)
        work = _Work()
        with cat.read() as conn:
            work.convert = [int(row[0]) for row in conn.execute(
                f"SELECT id FROM events WHERE layout = 'legacy'{where} ORDER BY id", params)]
            if delete_legacy:
                work.copies = [int(row[0]) for row in conn.execute(
                    f"SELECT id FROM events WHERE layout = 'v3' AND legacy_path IS NOT NULL{where} ORDER BY id",
                    params)]
            page_rows = {(int(row[0]), str(row[1])): (str(row[2]), str(row[3])) for row in conn.execute(
                "SELECT entity_id, sub, layout, path FROM entity_slices WHERE kind = 'season' AND key = ?",
                (KEY_SCHEDULE,))}
            list_rows = {int(row[0]): (str(row[1]), str(row[2])) for row in conn.execute(
                "SELECT entity_id, layout, path FROM entity_slices WHERE kind = 'tournament' AND key = ?",
                (KEY_SEASONS,))}
        if limit is not None:
            items = work.events
            if len(items) > limit:
                kept = items[:limit]
                work.convert = [event_id for event_id, convert in kept if convert]
                work.copies = [event_id for event_id, convert in kept if not convert]
                work.stopped = True

        scanned = LegacyReport()  # eski ağaçta tanınmayan girdiler: taşınmaz, yerinde kalır ve bildirilir
        walked: List[indexer.IndexProblem] = []
        work.candidates = indexer.legacy_candidates(self._reader, walked)
        if not scope:
            self._unreadable_events(work.candidates, walked, scanned)
        paged: Set[Tuple[int, int]] = set()
        for page in self._reader.schedule_pages(scanned):
            paged.add((page.tournament_id, page.season_id))
            if page.superseded_by is not None or (scope and page.tournament_id not in scope):
                continue
            row = page_rows.get((page.season_id, page.sub))
            if row is not None and row == (indexer.LAYOUT_LEGACY, page.path):
                work.pages.setdefault((page.tournament_id, page.season_id), []).append(page)
        paged.update(entities_mod.v3_schedule_seasons(self._data_dir))

        names = self._store._league_names()
        for item in self._reader.season_lists(names, scanned):
            if item.superseded_by is not None:
                continue
            if item.tournament_id is None:
                work.unconvertible.append(MigrationIssue(ISSUE_UNRESOLVED, item.label, item.path,
                                                         "the tournament of this season list is not known"))
                continue
            if scope and item.tournament_id not in scope:
                continue
            row = list_rows.get(item.tournament_id)
            if row is not None and row[0] == indexer.LAYOUT_LEGACY:
                work.lists.append(item)

        summaries = [s for s in self._reader.summary_files() if not scope or s.tournament_id in scope]
        for summary in summaries:
            key = (summary.tournament_id, summary.season_id) if summary.season_id is not None else None
            if key is None or key not in paged:
                where_ = f"{summary.tournament_id}/{summary.season_id if summary.season_id is not None else '?'}"
                work.unconvertible.append(MigrationIssue(
                    ISSUE_UNCONVERTIBLE, where_, summary.path,
                    "a season with summary files only (no round or page file): it stays in place and stays readable"))
            elif purge_derived:
                work.derived.append(summary.path)
        if purge_derived and not scope:
            processed = layout.resolve(self._data_dir, f"{legacy.DETAILS_DIR}/{legacy.PROCESSED_DIR}")
            for base, _dirs, inner in os.walk(processed):
                for name in inner:
                    full = os.path.join(base, name)
                    work.derived.append(os.path.relpath(full, self._data_dir).replace(os.sep, "/"))
        if not scope:
            seen: Set[Tuple[str, str]] = set()
            for problem in scanned.problems:
                if problem.kind == legacy.PROBLEM_UNRESOLVED or (problem.path, problem.kind) in seen:
                    continue  # turnuvası bulunamayan liste yukarıda; aynı girdi bir kez
                seen.add((problem.path, problem.kind))
                work.unconvertible.append(MigrationIssue(ISSUE_UNRECOGNISED, problem.kind, problem.path,
                                                         problem.detail))
        work.derived.sort()
        work.unconvertible.sort(key=lambda issue: (issue.kind, issue.path))
        return work

    def _unreadable_events(self, candidates: Mapping[str, List[LegacyEventDir]],
                           walked: Sequence[indexer.IndexProblem], scanned: LegacyReport) -> None:
        """
        Eski maç ağacında taşınamayan girdiler `scanned`a: olay yükü olmayan dizinler (taramanın sorunları) ve
        katalogda maç olarak durmayan, okunamayan ya da kimliği tutmayan dizinler. Taşınmazlar, yerinde kalırlar.
        """
        scanned.problems.extend(legacy.LegacyProblem(p.path, p.kind, p.detail) for p in walked)
        cat = self._store._catalog
        assert cat is not None
        with cat.read() as conn:
            known = {int(row[0]) for row in conn.execute("SELECT id FROM events WHERE layout IS NOT NULL")}
        for name, group in candidates.items():
            event_id = indexer.canonical_id(name)
            if event_id is not None and event_id in known:
                continue
            for candidate in group:
                try:
                    self._reader.read_event(candidate, payloads=False)
                except StoreError as exc:
                    found = indexer.problem_of(exc, candidate.path, indexer.LAYOUT_LEGACY)
                    scanned.problems.append(legacy.LegacyProblem(found.path, found.kind, found.detail))

    def _readable_copies(self, work: _Work, event_id: int) -> int:
        """Maçın, kendisinin kopyası olarak okunabilen eski dizinleri (silinmeye aday olanlar)."""
        count = 0
        for candidate in work.candidates.get(str(event_id), []):
            with contextlib.suppress(StoreError):
                self._reader.read_event(candidate, payloads=False)
                count += 1
        return count

    def _legacy_event(self, event_id: int, *, payloads: bool) -> Optional[LegacyEvent]:
        """Maçın geçerli eski kopyası (katalog satırının yolundan); okunamıyorsa None."""
        for candidate in self._event_candidates(event_id):
            try:
                return self._reader.read_event(candidate, payloads=payloads)
            except StoreError:
                continue
        return None

    def _event_candidates(self, event_id: int) -> List[LegacyEventDir]:
        """
        Maçın eski düzen dizinleri, öncelik sırasıyla. Silme kipinde çalışmanın başında bir kez taranan bütün
        kopyalar; değilse katalog satırının `path` ve `legacy_path`'i (dönüştürme yalnızca geçerli kopyayı okur).
        """
        if self._candidates is not None:
            return [c for c in self._candidates.get(str(event_id), [])
                    if os.path.isdir(self._reader.resolve(c.path))]
        cat = self._store._catalog
        assert cat is not None
        with cat.read() as conn:
            row = conn.execute("SELECT path, legacy_path FROM events WHERE id = ?", (event_id,)).fetchone()
        known = [path for path in ((row[0], row[1]) if row is not None else ()) if path]
        found = (self._reader.event_dir_at(path) for path in dict.fromkeys(known))
        return sorted((c for c in found if c is not None and c.name == str(event_id)), key=indexer.legacy_order)

    def _event_conflicts(self, event_ids: Sequence[int]) -> List[MigrationIssue]:
        """Dönüştürülecek ama v3 dizini zaten olan maçların çakışmaları (adım 4'ün kuralı), yazmadan."""
        out: List[MigrationIssue] = []
        for event_id in event_ids:
            manifest_file = layout.resolve(self._data_dir, layout.manifest_path(layout.event_dir(event_id)))
            if not os.path.isfile(manifest_file):
                continue
            event = self._legacy_event(event_id, payloads=True)
            if event is None:
                continue
            try:
                found = manifest_mod.read_manifest(manifest_file)
            except StoreError as exc:
                out.append(MigrationIssue(ISSUE_CONFLICT, str(event_id), layout.event_dir(event_id), str(exc)))
                continue
            problems = compare_with_v3(event, found)
            if problems:
                out.append(MigrationIssue(ISSUE_CONFLICT, str(event_id), event.path, "; ".join(problems)))
        return out

    # -- maçlar --------------------------------------------------------------------------------------------

    def _run_events(self, work: _Work, report: MigrationReport, delete_legacy: bool, emptied: Set[str],
                    should_stop: Optional[Callable[[], bool]], progress: Optional[Progress]) -> None:
        items = work.events
        for position, (event_id, convert) in enumerate(items):
            if should_stop is not None and should_stop():
                report.stopped = True
                break
            converted = True
            if convert:
                try:
                    self._convert(event_id, report)
                except StoreError as exc:  # _Rejected dahil: eski kopya yerinde, çalışma sürer
                    if not isinstance(exc, _Rejected):
                        logger.warning("Event %s could not be migrated: %s", event_id, exc)
                    report.failed.append(MigrationIssue(ISSUE_FAILED, str(event_id), exc.path or "", str(exc)))
                    converted = False
            if delete_legacy and converted:
                try:
                    self._delete_copies(event_id, report, emptied)
                except StoreError as exc:
                    logger.warning("The legacy copies of event %s could not be removed: %s", event_id, exc)
                    report.legacy_kept.append(MigrationIssue(ISSUE_KEPT, str(event_id), exc.path or "", str(exc)))
            if progress is not None:
                progress(MigrationProgress(STAGE_EVENTS, position + 1, len(items)))

    def _convert(self, event_id: int, report: MigrationReport) -> None:
        """Adım 1-5: maçı yazmanın çerçevesinde (`EventStore._entity_write`) dönüştürür."""
        events = self._store.events

        def body(write: Any) -> None:
            if write.recover:
                indexer.heal_v3_event(self._data_dir, event_id)
            candidates = self._event_candidates(event_id)
            if not candidates:
                raise _Rejected(f"Event {event_id}: no legacy directory found", path=self._data_dir)
            event = self._reader.read_event(candidates[0], payloads=True)
            extras = extra_files_of(self._reader, event)
            self._checkpoint(STEP_READ)
            rel = layout.event_dir(event_id)
            target = layout.resolve(self._data_dir, rel)
            before = _tree_size(self._reader.resolve(event.path))
            if os.path.lexists(target):
                # Adım 4: v3 dizini zaten var; hiçbir şeyin üzerine yazılmaz
                try:
                    found = manifest_mod.read_manifest(layout.resolve(self._data_dir, layout.manifest_path(rel)))
                except StoreError as exc:
                    raise _Rejected(f"Event {event_id}: a v3 directory exists and its manifest cannot be read "
                                    f"({exc})", path=target) from exc
                problems = compare_with_v3(event, found)
                if problems:
                    report.conflicts.append(MigrationIssue(ISSUE_CONFLICT, str(event_id), event.path,
                                                           "; ".join(problems)))
                self._index(event_id)
                report.events_existing += 1
                return
            found, encoded = manifest_from_legacy(event)
            staged = files.new_staging_dir(self._data_dir, events._staging_label())
            try:
                for key, ready in encoded.items():
                    files.write_bytes(os.path.join(staged, *layout.slice_path("", key).strip("/").split("/")),
                                      ready.stored)
                for extra in extras:
                    files.write_bytes(_extra_target(staged, extra.name), files.read_bytes(extra.source))
                manifest_mod.write_manifest(os.path.join(staged, layout.MANIFEST_NAME), found)
                self._checkpoint(STEP_STAGED)
                self._verify_staged(event, found, staged, extras)
                self._checkpoint(STEP_VERIFIED)
                try:
                    files.publish_dir(staged, target)
                finally:
                    write.touched = write.touched or os.path.isdir(target)
            except Exception:
                with contextlib.suppress(StoreError):
                    files.remove_tree(staged)
                raise
            self._checkpoint(STEP_PUBLISHED)
            self._index(event_id)
            report.events += 1
            report.extra_files.extend(f"{event.path}/{extra.name}" for extra in extras)
            report.bytes_before += before
            report.bytes_after += _tree_size(target)

        events._entity_write(event_id, body)

    def _verify_staged(self, event: LegacyEvent, found: Manifest, staged: str, extras: Sequence[_Extra]) -> None:
        """
        Adım 3: hazırlanan dizinin her dosyası geri okunur ve eskisiyle karşılaştırılır (ayrıştırılmış nesne ve
        açılmış baytların sha256'sı; `_extra/` bayt olarak). Uyuşmazlıkta _Rejected.
        """
        payloads = event.payloads or {}
        for key, entry in found.slices.items():
            if not entry.has_payload:
                continue
            path = os.path.join(staged, *layout.slice_path("", key).strip("/").split("/"))
            try:
                raw = _read_back(path)
                same = _same_payload(json.loads(raw), payloads[key]) and codec.sha256_hex(raw) == entry.sha256
            except (StoreError, ValueError, RecursionError, KeyError):
                same = False
            if not same:
                raise _Rejected(f"Event {event.event_id}: the staged {key} slice does not read back as the legacy "
                                f"payload ({event.path})", path=path, detail=f"{key}: read-back mismatch")
        for extra in extras:
            try:
                same = files.read_bytes(_extra_target(staged, extra.name)) == files.read_bytes(extra.source)
            except StoreError:
                same = False
            if not same:
                raise _Rejected(f"Event {event.event_id}: the copy of {extra.name} does not read back as the "
                                f"original ({event.path})", path=extra.source, detail="read-back mismatch")
        manifest_mod.read_manifest(os.path.join(staged, layout.MANIFEST_NAME))

    def _index(self, event_id: int) -> None:
        """Adım 5: maçın katalog satırları dosyalardan (açık yazma işleminin içinde)."""
        candidates = self._event_candidates(event_id) if self._candidates is not None else None
        self._store.catalog.index_event(event_id, candidates=candidates)
        self._checkpoint(STEP_INDEXED)

    def _delete_copies(self, event_id: int, report: MigrationReport, emptied: Set[str]) -> None:
        """Adım 6: maçın doğrulanan eski kopyalarını çöp üzerinden siler (`EventStore._entity_write` içinde)."""

        def body(write: Any) -> None:
            if write.recover:
                indexer.heal_v3_event(self._data_dir, event_id)
            rel = layout.event_dir(event_id)
            root = layout.resolve(self._data_dir, rel)
            try:
                found = manifest_mod.read_manifest(layout.resolve(self._data_dir, layout.manifest_path(rel)))
            except StoreError as exc:
                raise _Rejected(f"Event {event_id}: the v3 copy cannot be read ({exc})", path=root) from exc
            from sofascore_scraper.store import verify as verify_mod  # döngüsel içe aktarma: verify dizinleyiciyi kullanır

            for name, entry in found.slices.items():
                if entry.has_payload:
                    fault = verify_mod.payload_fault(self._data_dir, rel, name, entry)
                    if fault is not None:
                        raise _Rejected(f"Event {event_id}: the v3 copy of {name} does not match its manifest "
                                        f"({fault})", path=root, detail=fault)
            candidates = self._event_candidates(event_id)
            removed: List[str] = []
            for candidate in candidates:
                try:
                    event = self._reader.read_event(candidate, payloads=True)
                except LayoutError:
                    report.legacy_kept.append(MigrationIssue(
                        ISSUE_KEPT, str(event_id), candidate.path,
                        "the directory holds another event's payload; it is not a copy of this event"))
                    continue
                except StoreError as exc:
                    report.legacy_kept.append(MigrationIssue(ISSUE_KEPT, str(event_id), candidate.path,
                                                             f"cannot be read: {exc}"))
                    continue
                problems = compare_with_v3(event, found)
                problems += self._ensure_extras(event, root, write)
                if problems:
                    report.legacy_kept.append(MigrationIssue(ISSUE_KEPT, str(event_id), candidate.path,
                                                             "; ".join(problems)))
                    continue
                source = self._reader.resolve(candidate.path)
                trashed = files.move_to_trash(self._data_dir, source)
                write.touched = True
                self._checkpoint(STEP_TRASHED)
                files.remove_tree(trashed)
                self._checkpoint(STEP_DELETED)
                removed.append(candidate.path)
                emptied.add(os.path.dirname(source))
            if removed:
                assert self._candidates is not None
                self._candidates[str(event_id)] = [c for c in self._candidates.get(str(event_id), [])
                                                   if c.path not in removed]
                report.legacy_copies += len(removed)
            self._index(event_id)

        self._store.events._entity_write(event_id, body)

    def _ensure_extras(self, event: LegacyEvent, root: str, write: Any) -> List[str]:
        """
        Eski kopyanın `_extra/` dosyaları v3 dizininde var mı; eksik olan (maç `put` ile yükseltilmişse) şimdi
        kopyalanır ve geri okunur. Aynı adla farklı içerik çakışmadır. Sorunların açıklamaları.
        """
        problems: List[str] = []
        for extra in extra_files_of(self._reader, event):
            target = _extra_target(root, extra.name)
            try:
                wanted = files.read_bytes(extra.source)
            except StoreError as exc:
                problems.append(f"{extra.name}: cannot be read ({exc})")
                continue
            try:
                have: Optional[bytes] = files.read_bytes(target)
            except StoreError:
                have = None
            if have is None:
                write.touched = True
                files.write_bytes(target, wanted)
                have = files.read_bytes(target)
            if have != wanted:
                problems.append(f"{extra.name}: differs from {layout.EXTRA_DIR_NAME}/{extra.name} of the v3 copy")
        return problems

    # -- program sayfaları ---------------------------------------------------------------------------------

    def _run_schedules(self, work: _Work, report: MigrationReport, delete_legacy: bool, emptied: Set[str],
                       progress: Optional[Progress]) -> None:
        seasons = sorted(work.pages.items())
        for position, ((tournament_id, season_id), pages) in enumerate(seasons):
            outcomes: Dict[Tuple[str, str], Outcome] = {}
            for page in pages:
                try:
                    schedule = self._reader.read_schedule(page)
                except StoreError as exc:
                    report.failed.append(MigrationIssue(ISSUE_FAILED, f"{tournament_id}/{season_id}/{page.sub}",
                                                        page.path, str(exc)))
                    continue
                outcomes[(KEY_SCHEDULE, page.sub)] = Outcome(
                    SLICE_OK, dict(schedule.payload), fetched_at=_whole_second(page.fetched_at),
                    meta=dict(schedule.meta))
                report.bytes_before += page.size
                report.bytes_after += codec.encode(schedule.payload).stored_bytes
            if outcomes:
                try:
                    self._store.entities.put(Ref.season(tournament_id, season_id), outcomes)
                except (StoreError, ValueError) as exc:
                    report.failed.append(MigrationIssue(ISSUE_FAILED, f"{tournament_id}/{season_id}",
                                                        pages[0].path.rsplit("/", 1)[0], str(exc)))
                else:
                    report.schedule_pages += len(outcomes)
                    report.schedule_seasons += 1
            if progress is not None:
                progress(MigrationProgress(STAGE_SCHEDULES, position + 1, len(seasons)))
        if delete_legacy:
            self._delete_schedule_files(report, emptied, work)

    def _delete_schedule_files(self, report: MigrationReport, emptied: Set[str], work: _Work) -> None:
        """
        Eski program dosyalarını siler: alt anahtarının v3 sayfası doğrulanan (dosyası manifestine uyan ve eski
        yükle aynı ya da ondan yeni) her eski dosya, geçerli olmayan kopyalar dahil.
        """
        from sofascore_scraper.store import verify as verify_mod

        scope = set(report.tournaments)
        verified: Dict[Tuple[int, int], Optional[Manifest]] = {}
        removed = 0
        for page in self._reader.schedule_pages():
            if scope and page.tournament_id not in scope:
                continue
            key = (page.tournament_id, page.season_id)
            if key not in verified:
                directory = layout.season_dir(*key)
                try:
                    verified[key] = manifest_mod.read_manifest(
                        layout.resolve(self._data_dir, layout.manifest_path(directory)))
                except StoreError:
                    verified[key] = None
            found = verified[key]
            name = layout.slice_name(KEY_SCHEDULE, page.sub)
            entry = found.slices.get(name) if found is not None else None
            if entry is None or not entry.has_payload:
                continue  # v3 sayfası yok: eski dosya geçerli kopyadır
            problem = verify_mod.payload_fault(self._data_dir, layout.season_dir(*key), name, entry)
            if problem is None and page.superseded_by is None:
                try:
                    digest = codec.sha256_hex(codec.canonical_bytes(self._reader.read_schedule(page).payload))
                except StoreError as exc:
                    problem = str(exc)
                else:
                    legacy_at = _whole_second(page.fetched_at)
                    newer = entry.fetched_at is not None and legacy_at is not None and entry.fetched_at >= legacy_at
                    if digest != entry.sha256 and not newer:
                        problem = "differs from the v3 page, which is not newer"
            if problem is not None:
                report.legacy_kept.append(MigrationIssue(ISSUE_KEPT, f"{key[0]}/{key[1]}/{page.sub}", page.path,
                                                         problem))
                continue
            source = self._reader.resolve(page.path)
            files.remove_tree(files.move_to_trash(self._data_dir, source))
            emptied.add(os.path.dirname(source))
            removed += 1
        report.schedule_files_deleted += removed
        if removed:
            self._store.catalog.sync_listings(indexer.LISTING_SCHEDULES)

    # -- sezon listeleri -----------------------------------------------------------------------------------

    def _run_season_lists(self, work: _Work, report: MigrationReport, delete_legacy: bool,
                          scope: Tuple[int, ...], emptied: Set[str], progress: Optional[Progress]) -> None:
        for position, item in enumerate(work.lists):
            assert item.tournament_id is not None
            outcome = Outcome(SLICE_OK, dict(item.payload), fetched_at=_whole_second(item.fetched_at))
            try:
                self._store.entities.put(Ref.tournament(item.tournament_id), {KEY_SEASONS: outcome})
            except (StoreError, ValueError) as exc:
                report.failed.append(MigrationIssue(ISSUE_FAILED, str(item.tournament_id), item.path, str(exc)))
            else:
                report.season_lists += 1
                report.bytes_before += _tree_size(self._reader.resolve(item.path)) if item.kind == "json" else 0
                report.bytes_after += codec.encode(item.payload).stored_bytes
            if progress is not None:
                progress(MigrationProgress(STAGE_SEASON_LISTS, position + 1, len(work.lists)))
        if delete_legacy:
            self._delete_season_list_files(report, scope, emptied)

    def _delete_season_list_files(self, report: MigrationReport, scope: Tuple[int, ...], emptied: Set[str]) -> None:
        """
        Eski sezon listesi dosyalarını siler: v3 listesi doğrulanan (dosyası manifestine uyan ve geçerli eski
        listeyle aynı ya da ondan yeni) turnuvanın bütün JSON dosyaları. `league_seasons.csv` birden çok turnuvayı
        taşır: yalnızca hepsi doğrulandıysa ve seçimsiz çalışmada silinir.
        """
        from sofascore_scraper.store import verify as verify_mod

        items = self._reader.season_lists(self._store._league_names())
        winners = {item.tournament_id: item for item in items
                   if item.superseded_by is None and item.tournament_id is not None}
        verdict: Dict[int, Optional[str]] = {}
        for tournament_id, winner in winners.items():
            directory = layout.tournament_dir(tournament_id)
            try:
                found = manifest_mod.read_manifest(layout.resolve(self._data_dir, layout.manifest_path(directory)))
            except StoreError:
                continue  # v3 listesi yok
            entry = found.slices.get(KEY_SEASONS)
            if entry is None or not entry.has_payload:
                continue
            problem = verify_mod.payload_fault(self._data_dir, directory, KEY_SEASONS, entry)
            if problem is None:
                legacy_at = _whole_second(winner.fetched_at)
                newer = entry.fetched_at is not None and legacy_at is not None and entry.fetched_at >= legacy_at
                if codec.sha256_hex(codec.canonical_bytes(winner.payload)) != entry.sha256 and not newer:
                    problem = "differs from the v3 season list, which is not newer"
            verdict[tournament_id] = problem
        removed = 0
        csv_tournaments: Set[Optional[int]] = set()
        for item in items:
            if item.kind != "json":
                csv_tournaments.add(item.tournament_id)
                continue
            if item.tournament_id is None or item.tournament_id not in verdict:
                continue
            if scope and item.tournament_id not in scope:
                continue
            problem = verdict[item.tournament_id]
            if problem is not None:
                report.legacy_kept.append(MigrationIssue(ISSUE_KEPT, str(item.tournament_id), item.path, problem))
                continue
            source = self._reader.resolve(item.path)
            files.remove_tree(files.move_to_trash(self._data_dir, source))
            emptied.add(os.path.dirname(source))
            removed += 1
        if csv_tournaments and not scope:
            if all(t is not None and verdict.get(t, "missing") is None for t in csv_tournaments):
                source = layout.resolve(self._data_dir, legacy.SEASONS_CSV)
                files.remove_tree(files.move_to_trash(self._data_dir, source))
                removed += 1
            else:
                report.legacy_kept.append(MigrationIssue(
                    ISSUE_KEPT, "league_seasons", legacy.SEASONS_CSV,
                    "not every tournament of this file has a verified v3 season list"))
        report.season_list_files_deleted += removed
        if removed:
            self._store.catalog.sync_listings(indexer.LISTING_SEASON_LISTS)

    # -- değişiklik günlüğü --------------------------------------------------------------------------------

    def _run_change_log(self, report: MigrationReport, delete_legacy: bool, progress: Optional[Progress]) -> None:
        cat = self._store._catalog
        assert cat is not None
        before = _tree_size(layout.resolve(self._data_dir, changes_mod.LEGACY_SEGMENT))
        with cat.write():
            report.change_log = changes_mod.copy_legacy(cat, self._data_dir)
        if report.change_log in (changes_mod.COPY_CREATED, changes_mod.COPY_EXTENDED):
            report.bytes_before += before
            report.bytes_after += before
        if report.change_log != changes_mod.COPY_NONE:
            report.change_log_lines = len(self._reader.change_log())
        if report.change_log == changes_mod.COPY_CONFLICT:
            report.conflicts.append(MigrationIssue(ISSUE_CONFLICT, "change_log", changes_mod.LEGACY_COPY,
                                                   "the copy is not a prefix of score_changes.jsonl"))
        if delete_legacy and changes_mod.legacy_copy_verified(self._data_dir):
            files.remove(layout.resolve(self._data_dir, changes_mod.LEGACY_SEGMENT))
            report.change_log_deleted = True
            self._store.catalog.sync_listings(indexer.LISTING_CHANGES)
        if progress is not None:
            progress(MigrationProgress(STAGE_CHANGE_LOG, 1, 1))

    # -- türetilmiş dosyalar ve boş dizinler ---------------------------------------------------------------

    def _purge_derived(self, work: _Work, report: MigrationReport, emptied: Set[str],
                       progress: Optional[Progress]) -> None:
        for position, rel in enumerate(work.derived):
            path = layout.resolve(self._data_dir, rel)
            if files.remove(path):
                report.derived_removed.append(rel)
                emptied.add(os.path.dirname(path))
            if progress is not None:
                progress(MigrationProgress(STAGE_DERIVED, position + 1, len(work.derived)))
        if report.derived_removed:
            self._store.catalog.sync_listings(indexer.LISTING_SCHEDULES)

    def _remove_empty(self, emptied: Iterable[str], *, sweep: bool = False) -> None:
        """
        İçi boşalan dizinleri (sezon, lig) yukarı doğru siler; eski düzen ağaçlarının kökleri ve `processed/`
        kalır. sweep=True: eski düzen ağaçlarındaki bütün boş dizinlere bakılır (yarıda kalmış bir önceki
        çalışmanın boşalttıkları da gider; silme kipinde çalışma her seferinde aynı sonuca varır).
        """
        names = (legacy.DETAILS_DIR, legacy.MATCHES_DIR, legacy.SEASONS_DIR)
        roots = {os.path.normcase(layout.resolve(self._data_dir, name)) for name in names}
        roots.add(os.path.normcase(os.path.abspath(self._data_dir)))
        processed = os.path.normcase(layout.resolve(self._data_dir, f"{legacy.DETAILS_DIR}/{legacy.PROCESSED_DIR}"))
        candidates = set(emptied)
        if sweep:
            for name in names:
                for base, _dirs, _files in os.walk(layout.resolve(self._data_dir, name)):
                    candidates.add(base)
        changed = False
        for directory in sorted(candidates, key=len, reverse=True):
            current = os.path.abspath(directory)
            while (os.path.normcase(current) not in roots and os.path.normcase(current) != processed
                   and current.startswith(os.path.abspath(self._data_dir) + os.sep)):
                try:
                    os.rmdir(current)
                except OSError:
                    break  # boş değil ya da yok
                changed = True
                current = os.path.dirname(current)
        if changed:
            self._store.catalog.sync_listings(indexer.LISTING_SCHEDULES)

    # -- migration_runs ------------------------------------------------------------------------------------

    def _record_start(self, delete_legacy: bool) -> int:
        with self._store._state.write() as conn:
            cursor = conn.execute(
                "INSERT INTO migration_runs (started_at, dry_run, delete_legacy) VALUES (?, 0, ?)",
                (int(time.time()), int(delete_legacy)))
            return int(cursor.lastrowid or 0)

    def _record_finish(self, report: MigrationReport) -> None:
        if report.run_id is None:
            return
        try:
            with self._store._state.write() as conn:
                conn.execute(
                    "UPDATE migration_runs SET finished_at = ?, events_done = ?, events_failed = ?, bytes_before = ?, "
                    "bytes_after = ?, report_json = ? WHERE id = ?",
                    (int(time.time()), report.events + report.events_existing, len(report.failed),
                     report.bytes_before, report.bytes_after, json.dumps(report_summary(report), sort_keys=True),
                     report.run_id))
        except Exception as exc:  # kayıt bilgidir: yazılamaması taşımayı düşürmez
            logger.warning("The migration run could not be recorded in state.db: %s", exc)


def report_summary(report: MigrationReport) -> Dict[str, Any]:
    """Raporun `migration_runs.report_json`'a yazılan özeti: sayımlar ve sorunların ilk 100'ü."""
    data = dataclasses.asdict(report)
    for name in ("extra_files", "derived_removed", "unconvertible", "conflicts", "failed", "legacy_kept"):
        values = data[name]
        data[name] = values[:100]
        data[f"{name}_count"] = len(values)
    data["tournaments"] = list(report.tournaments)
    return data


def _read_back(path: str) -> bytes:
    """Hazırlanan bir yük dosyasının açılmış baytları (doğrulama; testler burada bozulmuş okuma enjekte eder)."""
    return codec.read_raw(path)


def _scope(tournaments: Iterable[int]) -> Tuple[int, ...]:
    out: List[int] = []
    for value in tournaments:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"tournaments: expected tournament ids, got {value!r}")
        out.append(value)
    return tuple(sorted(set(out)))


def _check_limit(limit: Optional[int]) -> None:
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError(f"limit: expected a positive number, got {limit!r}")


def _spread(values: Sequence[int], count: int) -> List[int]:
    """Listeden eşit aralıklı en çok `count` öğe (örnek: baştaki maçlar listenin tamamını temsil etmeyebilir)."""
    if len(values) <= count:
        return list(values)
    step = len(values) / count
    return [values[int(index * step)] for index in range(count)]


__all__ = [
    "EVENT_STEPS",
    "ISSUE_CONFLICT",
    "ISSUE_FAILED",
    "ISSUE_KEPT",
    "ISSUE_UNCONVERTIBLE",
    "ISSUE_UNRECOGNISED",
    "ISSUE_UNRESOLVED",
    "MIGRATE_PURPOSE",
    "MigrationIssue",
    "MigrationPlan",
    "MigrationProgress",
    "MigrationReport",
    "Migrator",
    "SAMPLE_EVENTS",
    "compare_with_v3",
    "extra_files_of",
    "report_summary",
]
