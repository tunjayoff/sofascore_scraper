"""
Eski (2.x) disk düzeninin salt okunur okuyucusu (docs/design/01-storage.md, bölüm 5.1 ve 5.2).

Eski yol kurallarını bilen tek modül burasıdır. Yalnızca okur: hiçbir işlevi dosya yazmaz, silmez,
taşımaz ya da dizin oluşturmaz. Dönen kayıtlar düzenden bağımsızdır; katalog (indexer), v3'e yükseltme
ve `migrate` aynı kayıtlardan beslenir.

Tanınan biçimler (bölüm 5.1):

  L1        match_details/<lig id>_<ad>/season_<ad>/<maç id>/
  L2        match_details/<ad>/season_<ad>/<maç id>/                (ID'siz lig dizini)
  L3        match_details/<maç id>/                                 (düz)
  L4        <maç dizini>/<maç id>.json                              (bütün dilimler tek dosyada; L1-L3, L5 ile birlikte)
  L5        match_details/_no_tournament/<spor>/<maç id>/
  program   matches/<lig id>_<ad>/<sezon id>_<ad>/round_*.json, events_*.json
  özetler   matches/<lig id>_<ad>/*_summary.{json,csv}, *_matches.csv (lig ya da sezon dizininde)
  sezonlar  seasons/<lig id>_*_seasons.json, <lig id>_seasons.json, <ad>_seasons.json, league_seasons.csv
  günlük    score_changes.jsonl
  izleyici  watch_events.jsonl, watch_state_<spor>.json

Kurallar:

  * Maç dizini: `basic.json`'ı (ya da L4'te birleşik dosyanın `basic` anahtarı) olan ve o yükteki `id`
    dizin adına eşit olan dizin. `match_details/processed/` atlanır.
  * Aynı maç birden çok yerde duruyorsa olay yükü (basic.json, yoksa birleşik dosya) en yeni olan dizin
    geçerlidir; ötekiler `LegacyEvent.duplicates` ve `LegacyReport.superseded` içinde bildirilir.
  * Bir dilim önce kendi dosyasından (`<anahtar>.json`), dosya yoksa birleşik dosyadan okunur
    (bölüm 5.2). Kendi dosyası olan dilim hiçbir zaman birleşik dosyadaki kopyadan eski değildir: bugünkü
    yazıcıların hepsi ayrı dosyayı yazar, birleşik dosyayı yalnızca yenileme günceller.
  * Dilim durumu ve sayaçlar bölüm 2.3'teki eşlemeyle çıkar: dosya var ve dilimin kuralı
    (sofascore_scraper.slices.slice_body_state) "veri var" diyorsa `ok`, "veri yok" diyorsa yüküyle birlikte `empty`;
    `_unavailable.json[k] = c` ve `_slice_status.json[k].empty.count = n` için `empty_count = min(c, n)`,
    `unverified_empty_count = c - min(c, n)`; `_slice_status.json[k].error` hata alanlarına kopyalanır.
    Okunamayan dosya ve kuralın okuyamadığı gövde (beklenmeyen biçim) `error` / `corrupt` olur ve taramayı
    durdurmaz.
  * Bir turnuvanın birden çok sezon listesi dosyası varsa adı ne olursa olsun en yenisi geçerlidir;
    `league_seasons.csv` yalnızca JSON dosyası olmayan turnuva için kullanılır.
  * `score_changes.jsonl` satırlarının sıra numarası (seq) satır numarasıdır (1'den başlar).

Bütün yollar DATA_DIR'e göre görelidir ve "/" ile ayrılır (katalogdaki biçim); gerçek yol
`layout.resolve(data_dir, rel)` ile bulunur.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Set, Tuple, Union

from sofascore_scraper.slices import BODY_DATA, BODY_MALFORMED, slice_body_state
from sofascore_scraper.sports import DETAIL_SLICES
from sofascore_scraper.store import codec, files, layout
from sofascore_scraper.store.errors import LayoutError, PayloadCorrupt, PayloadMissing, StoreError

PathLike = Union[str, "os.PathLike[str]"]

# --- eski düzenin adları (yazıcılardaki sabitlerle aynı; tests/test_store_legacy.py eşitliği denetler) ---

SEASONS_DIR = "seasons"
MATCHES_DIR = "matches"
DETAILS_DIR = "match_details"
PROCESSED_DIR = "processed"  # match_details/processed: türetilmiş CSV'ler, maç dizini değil
NO_TOURNAMENT_DIR = "_no_tournament"
BASIC_FILE = "basic.json"
OBSERVATION_FILE = "observation.json"
UNAVAILABLE_FILE = "_unavailable.json"
SLICE_STATUS_FILE = "_slice_status.json"
SEASONS_SUFFIX = "_seasons.json"
SEASONS_CSV = "league_seasons.csv"
CHANGES_FILE = "score_changes.jsonl"
WATCH_EVENTS_FILE = "watch_events.jsonl"
SUMMARY_JSON_SUFFIX = "_summary.json"
SUMMARY_CSV_SUFFIX = "_summary.csv"
MATCHES_CSV_SUFFIX = "_matches.csv"

EVENT_KEY = "event"  # /event/{id} yükünün v3 anahtarı
BASIC_KEY = "basic"  # aynı yükün eski adı (basic.json, birleşik dosyadaki anahtar)
OBSERVATION_KEY = "observation"

FORM_L1 = "L1"
FORM_L2 = "L2"
FORM_L3 = "L3"
FORM_L5 = "L5"
# Aynı maçın iki kopyasının olay yükü aynı anda yazılmışsa hangisinin geçerli olduğu: lig/sezon dizini önce
_FORM_RANK = {FORM_L1: 0, FORM_L2: 1, FORM_L5: 2, FORM_L3: 3}

STATE_OK = "ok"
STATE_EMPTY = "empty"
STATE_ERROR = "error"
REASON_CORRUPT = "corrupt"

# LegacyProblem.kind değerleri
PROBLEM_CORRUPT = "corrupt"  # dosya okunamıyor ya da JSON olarak ayrıştırılamıyor
PROBLEM_MALFORMED = "malformed"  # JSON geçerli ama beklenen biçimde değil
PROBLEM_ID_MISMATCH = "id_mismatch"  # olay yükündeki id dizin adına eşit değil
PROBLEM_NO_EVENT = "no_event_payload"  # maç dizini yerinde ama olay yükü yok (yarım kalmış yazma)
PROBLEM_UNREADABLE = "unreadable"  # dosya sistemi hatası
PROBLEM_NAME = "unknown_name"  # adı kurala uymayan dizin ya da dosya
PROBLEM_UNRESOLVED = "unresolved_tournament"  # dosya adında turnuva id'si yok ve addan da bulunamadı
PROBLEM_TORN = "torn_line"  # satır sonu olmayan son satır (yarım yazma)

_PREFIX_RE = re.compile(r"([0-9]+)_")
_ROUND_RE = re.compile(r"round_([0-9]+)(?:_(.+))?")
_PAGE_RE = re.compile(r"events_(last|next)_([0-9]+)")
_WATCH_STATE_RE = re.compile(r"watch_state_(.+)\.json")
_MISSING: Any = object()


# --- kayıtlar -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyProblem:
    """Taramayı durdurmayan bir sorun: `verify` ve taşıma raporu bunları listeler."""

    path: str
    kind: str  # PROBLEM_* değerlerinden biri
    detail: str = ""


@dataclass(frozen=True)
class LegacySuperseded:
    """Aynı kimliğin daha yeni bir kopyası olduğu için kullanılmayan dizin ya da dosya."""

    kind: str  # "event" | "schedule" | "season_list"
    key: str  # maç id'si, "<turnuva>/<sezon>/<sayfa>" ya da turnuva id'si
    path: str
    winner: str


@dataclass
class LegacyReport:
    """Bir taramanın yan çıktısı; okuyuculara verilirse sorunlar ve geçersiz kopyalar burada toplanır."""

    problems: List[LegacyProblem] = field(default_factory=list)
    superseded: List[LegacySuperseded] = field(default_factory=list)


@dataclass(frozen=True)
class LegacyEventDir:
    """`match_details` altında bulunan bir maç dizini adayı (içeriği henüz okunmadı)."""

    name: str  # dizin adı; tanınan bir maçta maç kimliği
    path: str
    form: str  # L1 | L2 | L3 | L5
    league_dir: Optional[str]  # L3'te None
    season_dir: Optional[str]  # L5'te spor dizini; L3'te None
    has_basic: bool  # basic.json var
    combined: bool  # L4: <ad>.json var
    mtime_ns: int  # olay yükünü taşıyan dosyanın mtime'ı (basic.json, yoksa birleşik dosya)
    sig: str  # dizin imzası: "<mtime_ns>:<girdi sayısı>" (bölüm 3.5)
    entries: Tuple[str, ...]  # dizindeki adlar, sıralı; alt dizinlerin sonunda "/"

    @property
    def tournament_id(self) -> Optional[int]:
        """Lig dizini adındaki id (yalnızca L1); kesin değer olay yükündedir."""
        match = _PREFIX_RE.match(self.league_dir or "") if self.form == FORM_L1 else None
        return int(match.group(1)) if match else None


@dataclass(frozen=True)
class LegacySliceError:
    """Dilimin son başarısız isteği (`_slice_status.json[k].error`) ya da okunamayan dosya (`corrupt`)."""

    reason: str
    status: Optional[int] = None
    at: Optional[datetime] = None
    count: int = 1


@dataclass(frozen=True)
class LegacySlice:
    """Bir maçın bir dilimi: durum, sayaçlar ve yükün nerede durduğu."""

    key: str  # v3 anahtarı; /event/{id} yükü için "event"
    state: str  # "ok" | "empty" | "error"
    has_payload: bool  # ayrıştırılabilen bir yük var
    path: Optional[str] = None  # yükün (ya da bozuk dosyanın) yolu
    in_combined: bool = False  # yük birleşik dosyadan (L4) geliyor
    fetched_at: Optional[float] = None  # dosyanın mtime'ı (epoch saniye)
    size: Optional[int] = None  # dosyanın boyutu; birleşik dosyadan gelen dilimde None
    empty_count: int = 0  # kesin yanıtla doğrulanmış "veri yok" sayısı
    unverified_empty_count: int = 0  # eski sürümden kalan, kesin yanıtın desteklemediği sayım
    empty_at: Optional[datetime] = None
    error: Optional[LegacySliceError] = None

    def settled_empty(self, threshold: int = 2) -> bool:
        """Yeterince denendi ve hep boş geldi: dilim bu maçta artık beklenmez."""
        return self.empty_count + self.unverified_empty_count >= threshold


@dataclass(frozen=True)
class LegacyObservation:
    """observation.json: yükün gözlendiği an, SofaScore'un değişiklik zamanı, yapışkan bayrak."""

    observed_at: Optional[datetime]  # ayrıştırılamıyorsa None (kayıt gözlemsiz sayılır)
    change_ts: Optional[int]
    status_regressed: bool
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class LegacyEvent:
    """Okunmuş bir maç: olay yükü, gözlem, dilim durumları ve (istenirse) bütün yükler."""

    event_id: int
    dir: LegacyEventDir
    event: Mapping[str, Any]  # /event/{id} yükü
    observation: Optional[LegacyObservation]
    slices: Tuple[LegacySlice, ...]  # ilki "event", gerisi dilim tablosu sırasıyla
    payloads: Optional[Mapping[str, Any]]  # anahtar → yük ("event" dahil); payloads=False ile okunduysa None
    extra_files: Tuple[str, ...] = ()  # dilim, gözlem ya da işaret dosyası olmayan girdiler
    combined_extra_keys: Tuple[str, ...] = ()  # birleşik dosyadaki tanınmayan anahtarlar
    problems: Tuple[LegacyProblem, ...] = ()
    duplicates: Tuple[str, ...] = ()  # aynı maçın kullanılmayan (daha eski) dizinleri

    @property
    def path(self) -> str:
        return self.dir.path

    def slice(self, key: str) -> Optional[LegacySlice]:
        return next((s for s in self.slices if s.key == key), None)


@dataclass(frozen=True)
class LegacySchedulePage:
    """`matches/<lig>/<sezon>/` altındaki bir tur dosyası ya da olay sayfası (içeriği okunmadı)."""

    tournament_id: int
    season_id: int
    sub: str  # v3 alt anahtarı: round_12, round_3_final, last_0, next_2
    kind: str  # "round" | "page"
    path: str
    mtime_ns: int
    size: int
    superseded_by: Optional[str] = None  # aynı sezonun aynı sayfasının daha yeni kopyası

    @property
    def fetched_at(self) -> float:
        return self.mtime_ns / 1_000_000_000


@dataclass(frozen=True)
class LegacySchedule:
    """Okunmuş bir program sayfası. `payload`, dosyadaki nesnenin `_complete` anahtarı çıkarılmış halidir."""

    page: LegacySchedulePage
    payload: Mapping[str, Any]
    meta: Mapping[str, Any]  # {"complete": bool} ya da {"filtered": True}

    @property
    def events(self) -> List[Any]:
        listed = self.payload.get("events")
        return listed if isinstance(listed, list) else []


@dataclass(frozen=True)
class LegacySummaryFile:
    """Türetilmiş sezon özeti. Yalnızca tur / sayfa dosyası olmayan sezonlarda kaynak olarak kullanılır."""

    tournament_id: int
    season_id: Optional[int]  # dosya (ya da sezon dizini) adındaki önek; yoksa None
    kind: str  # "summary_csv" | "summary_json" | "matches_csv"
    path: str
    mtime_ns: int
    nested: bool  # sezon dizininin içinde (ilk sürümün round_<n>_matches.csv dosyaları)


@dataclass(frozen=True)
class LegacySeasonList:
    """Bir turnuvanın sezon listesi (dosyanın tamamı okunmuş halde)."""

    tournament_id: Optional[int]  # dosya adından ya da `league_names`ten; bulunamadıysa None
    label: str  # dosya adının ad kısmı ("Premier_League"); yoksa ""
    kind: str  # "json" | "csv"
    path: str
    mtime_ns: int
    payload: Mapping[str, Any]  # {"seasons": [...]}
    superseded_by: Optional[str] = None

    @property
    def seasons(self) -> List[Any]:
        listed = self.payload.get("seasons")
        return listed if isinstance(listed, list) else []

    @property
    def fetched_at(self) -> float:
        return self.mtime_ns / 1_000_000_000


@dataclass(frozen=True)
class LegacyLine:
    """Bir JSON Lines dosyasının bir satırı."""

    seq: int  # satır numarası, 1'den başlar (değişiklik günlüğünde sıra numarası)
    row: Mapping[str, Any]
    line: str  # dosyadaki satır, satır sonu olmadan


@dataclass(frozen=True)
class LegacyWatchState:
    """watch_state_<spor>.json: izleyicinin son bildiği durum (maç id'si → durum)."""

    sport: str
    path: str
    mtime_ns: int
    state: Mapping[str, Any]


# --- yardımcılar --------------------------------------------------------------------------------


def _is_id(name: str) -> bool:
    return name.isascii() and name.isdigit()


def _id_order(name: str) -> Tuple[int, int, str]:
    return (0, int(name), name) if _is_id(name) else (1, 0, name)


def _is_dir(entry: "os.DirEntry[str]") -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _league_form(league_dir: str) -> str:
    """Lig düzeyindeki dizinin biçimi: `_no_tournament` → L5, `<id>_<ad>` → L1, gerisi (ID'siz ad) → L2."""
    if league_dir == NO_TOURNAMENT_DIR:
        return FORM_L5
    return FORM_L1 if _PREFIX_RE.match(league_dir) else FORM_L2


def safe_name(name: str) -> str:
    """2.x yazıcılarının dizin / dosya adı kuralı: boşluk ve yol ayırıcıları '_' olur."""
    return str(name).replace(" ", "_").replace("/", "_").replace("\\", "_")


def league_dir_name(league_id: int, league_name: Optional[str]) -> str:
    """
    2.x düzeninde bir ligin dizin adı (`17_Premier_League`): kimlik ve yapılandırmadaki lig adı; ad yoksa
    sabit bir yer tutucu (`League_17`). Eski yanıtlar (dosya raporu, maç listelerinin lig sütunu) ligleri bu
    adla anar; 3.0 bu adla dizin açmaz.
    """
    return f"{league_id}_{safe_name(league_name or f'League_{league_id}')}"


def _parse_ts(value: Any) -> Optional[datetime]:
    """ISO 8601 metni → saat dilimli datetime; saat dilimi yoksa UTC sayılır. Ayrıştırılamıyorsa None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _plain_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _confirmed_empty(entry: Any) -> int:
    """`_slice_status.json[k].empty.count`: pozitif tam sayı değilse 0 (2.x yazıcısının kuralı)."""
    empty = entry.get("empty") if isinstance(entry, dict) else None
    count = _plain_int(empty.get("count")) if isinstance(empty, dict) else None
    return count if count is not None and count > 0 else 0


def _error_mark(entry: Any) -> Optional[LegacySliceError]:
    error = entry.get("error") if isinstance(entry, dict) else None
    if not isinstance(error, dict):
        return None
    reason = error.get("reason")
    count = _plain_int(error.get("count"))
    return LegacySliceError(
        reason=reason if isinstance(reason, str) and reason else "other",
        status=_plain_int(error.get("status")),
        at=_parse_ts(error.get("at")),
        count=count if count is not None and count > 0 else 1,
    )


def _detail(exc: StoreError) -> str:
    return exc.detail or str(exc)


def _problem_of(exc: StoreError, rel: str) -> LegacyProblem:
    if isinstance(exc, PayloadCorrupt):
        kind = PROBLEM_CORRUPT
    elif isinstance(exc, LayoutError):
        kind = PROBLEM_ID_MISMATCH
    elif isinstance(exc, PayloadMissing):
        kind = PROBLEM_NO_EVENT
    else:
        kind = PROBLEM_UNREADABLE
    return LegacyProblem(rel, kind, _detail(exc))


def schedule_sub(file_name: str) -> Optional[Tuple[str, str]]:
    """
    Program dosyasının adından (tür, v3 alt anahtarı): `round_12.json` → ("round", "round_12"),
    `round_3_final.json` → ("round", "round_3_final"), `events_last_0.json` → ("page", "last_0").
    Program dosyası değilse ya da alt anahtar v3 kuralına uymuyorsa None.

    v3 alt anahtarları küçük harftir (layout.validate_sub büyük harfi reddeder), eski yazıcı ise tur
    dosyasının adına SofaScore'un slug'ını olduğu gibi koyar. Slug'ında büyük harf olan tur dosyası
    (`round_1_Final.json`) yine program sayfasıdır: alt anahtar küçük harfe katlanır ("round_1_final"),
    dosyanın diskteki adı değişmez. `round_` ve `events_` önekleri yazıcının yazdığı gibi küçük harf olmalıdır.
    """
    if not file_name.endswith(".json"):
        return None
    stem = file_name[: -len(".json")]
    page = _PAGE_RE.fullmatch(stem)
    if page:
        return "page", f"{page.group(1)}_{page.group(2)}"
    if not _ROUND_RE.fullmatch(stem):
        return None
    try:
        return "round", layout.validate_sub(stem.lower())
    except LayoutError:
        return None


# --- okuyucu ------------------------------------------------------------------------------------


class LegacyReader:
    """Bir veri dizinindeki eski düzen ağaçlarının okuyucusu. Durum tutmaz; her çağrı diski yeniden okur."""

    def __init__(self, data_dir: PathLike, *, known_slices: Optional[Iterable[str]] = None) -> None:
        self.data_dir = os.fspath(data_dir)
        # Maç dizinindeki hangi dosyaların dilim olduğu: spor kayıt defterindeki tablo (sofascore_scraper/sports.py)
        self.known_slices: Tuple[str, ...] = (
            tuple(known_slices) if known_slices is not None else tuple(s.key for s in DETAIL_SLICES)
        )

    # -- dosya sistemi (yalnızca okuma) --

    def resolve(self, rel: str) -> str:
        return layout.resolve(self.data_dir, rel)

    def _entries(self, rel: str, problems: Optional[List[LegacyProblem]] = None) -> List["os.DirEntry[str]"]:
        """
        Dizinin girdileri, ada göre sıralı (listeleme sırası sonucu etkilemesin). Dizin yoksa boş liste.
        Dizin listelenemiyorsa (izin yok, G/Ç hatası): `problems` verildiyse sorun not edilir ve boş liste
        döner (tarama sürer), verilmediyse StoreError.
        """
        try:
            with os.scandir(self.resolve(rel)) as scan:
                return sorted(scan, key=lambda entry: entry.name)
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError as e:
            error = StoreError.from_exception(e, self.resolve(rel), reading=True)
            if problems is None:
                raise error from e
            problems.append(LegacyProblem(rel, PROBLEM_UNREADABLE, _detail(error)))
            return []

    def _stat(self, rel: str) -> Optional[os.stat_result]:
        try:
            return os.stat(self.resolve(rel))
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as e:
            raise StoreError.from_exception(e, self.resolve(rel), reading=True) from e

    def _load(self, rel: str) -> Any:
        """JSON dosyasını okur: yoksa PayloadMissing, ayrıştırılamıyorsa PayloadCorrupt."""
        return codec.read_payload(self.resolve(rel))

    def _load_object(self, rel: str) -> Dict[str, Any]:
        """JSON nesnesi tutması gereken dosya; içeriği nesne değilse de PayloadCorrupt."""
        data = self._load(rel)
        if not isinstance(data, dict):
            raise PayloadCorrupt(f"The file is not a JSON object: {self.resolve(rel)}", path=self.resolve(rel),
                                 detail="not a JSON object")
        return data

    def _load_dict(self, rel: str, problems: List[LegacyProblem]) -> Optional[Dict[str, Any]]:
        """İsteğe bağlı bir yan dosya: yoksa ya da bozuksa None (bozuksa sorun olarak not edilir)."""
        try:
            return self._load_object(rel)
        except PayloadMissing:
            return None
        except StoreError as e:
            problems.append(_problem_of(e, rel))
            return None

    def signature(self, rel: str) -> Optional[str]:
        """
        Değişiklik imzası (bölüm 3.5): dizin için "<mtime_ns>:<girdi sayısı>", dosya için "<mtime_ns>:<boyut>".
        Yol yoksa None. Bir dizindeki dosya os.replace ile değişince dizinin mtime'ı da değişir.
        """
        st = self._stat(rel)
        if st is None:
            return None
        if os.path.isdir(self.resolve(rel)):
            return f"{st.st_mtime_ns}:{len(self._entries(rel))}"
        return f"{st.st_mtime_ns}:{st.st_size}"

    def has_data(self) -> bool:
        """Veri dizininde eski düzenden herhangi bir şey var mı."""
        return any(
            self._stat(rel) is not None
            for rel in (DETAILS_DIR, MATCHES_DIR, SEASONS_DIR, SEASONS_CSV, CHANGES_FILE)
        )

    # -- maç dizinleri: keşif --

    def _candidate(
        self,
        rel: str,
        name: str,
        children: List["os.DirEntry[str]"],
        form: str,
        league_dir: Optional[str],
        season_dir: Optional[str],
    ) -> Optional[LegacyEventDir]:
        names = {child.name for child in children if not _is_dir(child)}
        has_basic = BASIC_FILE in names
        combined = f"{name}.json" in names
        if not has_basic and not (combined and _is_id(name)):
            return None
        source = self._stat(f"{rel}/{BASIC_FILE if has_basic else f'{name}.json'}")
        own = self._stat(rel)
        if source is None or own is None:  # tarama sırasında silindi
            return None
        return LegacyEventDir(
            name=name,
            path=rel,
            form=form,
            league_dir=league_dir,
            season_dir=season_dir,
            has_basic=has_basic,
            combined=combined,
            mtime_ns=source.st_mtime_ns,
            sig=f"{own.st_mtime_ns}:{len(children)}",
            entries=tuple(child.name + ("/" if _is_dir(child) else "") for child in children),
        )

    def _walk_details(self) -> Tuple[List[LegacyEventDir], List[LegacyProblem]]:
        """
        `match_details` ağacı. Kural 2.x yazıcısının ağaç gezintisinin kuralıdır: birinci düzeyde olay yükü olan
        dizin düz kayıttır (L3); öteki birinci düzey dizinler lig dizinidir ve maçlar üçüncü düzeydedir. Fark: yalnızca birleşik dosyası olan dizin de adaydır (bölüm 5.1).
        """
        found: List[LegacyEventDir] = []
        problems: List[LegacyProblem] = []
        for top in self._entries(DETAILS_DIR, problems):
            if top.name == PROCESSED_DIR or not _is_dir(top):
                continue
            top_rel = f"{DETAILS_DIR}/{top.name}"
            children = self._entries(top_rel, problems)
            flat = self._candidate(top_rel, top.name, children, FORM_L3, None, None)
            if flat is not None:
                found.append(flat)
                continue
            season_dirs = [child for child in children if _is_dir(child)]
            if _is_id(top.name) and children and not season_dirs:
                problems.append(LegacyProblem(top_rel, PROBLEM_NO_EVENT, "no event payload in the flat event directory"))
                continue
            form = _league_form(top.name)
            for season in season_dirs:
                season_rel = f"{top_rel}/{season.name}"
                for match in self._entries(season_rel, problems):
                    if not _is_dir(match):
                        continue
                    match_rel = f"{season_rel}/{match.name}"
                    unreadable = len(problems)
                    nested = self._candidate(
                        match_rel, match.name, self._entries(match_rel, problems), form, top.name, season.name
                    )
                    if nested is not None:
                        found.append(nested)
                    elif len(problems) == unreadable:  # listelenemeyen dizin zaten bildirildi
                        problems.append(LegacyProblem(match_rel, PROBLEM_NO_EVENT, "no event payload in the event directory"))
        return found, problems

    def event_dirs(self, report: Optional[LegacyReport] = None) -> List[LegacyEventDir]:
        """
        Bütün maç dizini adayları, yola göre sıralı. Yalnızca dizin listeler ve `stat` çağırır, hiçbir
        dosyanın içeriğini okumaz: aynı maçın kopyaları ayıklanmaz, yükteki id denetlenmez.
        Olay yükü olmayan maç dizinleri `report.problems`a yazılır.
        """
        found, problems = self._walk_details()
        if report is not None:
            report.problems.extend(problems)
        return sorted(found, key=lambda d: d.path)

    def event_dir_at(self, rel: str) -> Optional[LegacyEventDir]:
        """Yolu bilinen (katalogdaki `path`) bir maç dizini; dizin ya da olay yükü yoksa None."""
        parts = rel.strip("/").split("/")
        if len(parts) == 2 and parts[0] == DETAILS_DIR:
            form, league, season = FORM_L3, None, None
        elif len(parts) == 4 and parts[0] == DETAILS_DIR:
            league, season = parts[1], parts[2]
            form = _league_form(league)
        else:
            return None
        path = "/".join(parts)
        return self._candidate(path, parts[-1], self._entries(path), form, league, season)

    # -- maç dizinleri: okuma --

    def _marker_counts(self, base: str, problems: List[LegacyProblem]) -> Dict[str, int]:
        """_unavailable.json: {dilim: sayı}. Okunamayan dosya boş sayılır (2.x yazıcısının kuralı)."""
        data = self._load_dict(f"{base}/{UNAVAILABLE_FILE}", problems)
        if data is None:
            return {}
        try:
            return {str(key): int(value) for key, value in data.items()}
        except (ValueError, TypeError, OverflowError):
            problems.append(LegacyProblem(f"{base}/{UNAVAILABLE_FILE}", PROBLEM_MALFORMED, "a value that is not a number"))
            return {}

    def read_event(self, event_dir: LegacyEventDir, *, payloads: bool = True) -> LegacyEvent:
        """
        Bir maç dizinini okur. Olay yükü yoksa PayloadMissing, okunamıyorsa PayloadCorrupt, yükteki `id`
        dizin adına eşit değilse LayoutError. Dilim, gözlem ve işaret dosyalarındaki bozukluklar hata
        fırlatmaz: dilim `error` / `corrupt` olur, sorun `problems` içinde bildirilir.
        """
        base, name = event_dir.path, event_dir.name
        entries = set(event_dir.entries)
        problems: List[LegacyProblem] = []

        combined_rel = f"{base}/{name}.json"
        combined: Optional[Dict[str, Any]] = None
        if event_dir.combined and event_dir.has_basic:
            combined = self._load_dict(combined_rel, problems)  # bozuksa bildirilir; ayrı dosyalar okunur
        elif event_dir.combined:
            combined = self._load_object(combined_rel)  # olay yükünün tek kaynağı: okunamıyorsa hata çağırana
        combined_stat = self._stat(combined_rel) if combined is not None else None
        combined_event = combined.get(BASIC_KEY) if combined is not None else None

        event: Any = None
        event_rel, in_combined = f"{base}/{BASIC_FILE}", False
        if event_dir.has_basic:
            try:
                event = self._load(event_rel)
            except PayloadCorrupt as e:
                if not isinstance(combined_event, dict):
                    raise
                problems.append(_problem_of(e, event_rel))  # basic.json bozuk: birleşik dosyadaki kopya kullanılır
        if event is None:
            if combined_event is None:
                raise PayloadMissing(f"The match directory has no event payload: {self.resolve(base)}", path=self.resolve(base))
            event, event_rel, in_combined = combined_event, combined_rel, True
        if not isinstance(event, dict) or _plain_int(event.get("id")) is None or str(event["id"]) != name:
            found = event.get("id") if isinstance(event, dict) else type(event).__name__
            raise LayoutError(
                f"The id in the event payload ({found!r}) is not the directory name ({name!r}): {self.resolve(base)}",
                path=self.resolve(base),
                detail=f"id {found!r}, directory {name!r}",
            )

        event_stat = combined_stat if in_combined else self._stat(event_rel)
        loaded: Dict[str, Any] = {EVENT_KEY: event}
        slices: List[LegacySlice] = [LegacySlice(
            key=EVENT_KEY, state=STATE_OK, has_payload=True, path=event_rel, in_combined=in_combined,
            fetched_at=event_stat.st_mtime if event_stat else None,
            size=event_stat.st_size if event_stat and not in_combined else None,
        )]

        unavailable = self._marker_counts(base, problems) if UNAVAILABLE_FILE in entries else {}
        status: Dict[str, Any] = {}
        if SLICE_STATUS_FILE in entries:
            status = self._load_dict(f"{base}/{SLICE_STATUS_FILE}", problems) or {}
        for key in sorted((set(unavailable) | set(map(str, status))) - set(self.known_slices)):
            problems.append(LegacyProblem(base, PROBLEM_NAME, f"a slice the marker file does not know: {key}"))

        for key in self.known_slices:
            entry = self._read_slice(base, key, entries, combined, combined_rel, combined_stat,
                                     unavailable.get(key, 0), status.get(key), loaded, problems)
            if entry is not None:
                slices.append(entry)

        observation = None
        raw_observation = (
            self._load_dict(f"{base}/{OBSERVATION_FILE}", problems) if OBSERVATION_FILE in entries else None
        )
        if raw_observation is None and combined is not None and isinstance(combined.get(OBSERVATION_KEY), dict):
            raw_observation = combined[OBSERVATION_KEY]
        if raw_observation is not None:
            observation = LegacyObservation(
                observed_at=_parse_ts(raw_observation.get("observed_at_utc")),
                change_ts=_plain_int(raw_observation.get("change_ts")),
                status_regressed=bool(raw_observation.get("status_regressed")),
                raw=raw_observation,
            )

        known_files = {BASIC_FILE, OBSERVATION_FILE, UNAVAILABLE_FILE, SLICE_STATUS_FILE}
        known_files.update(f"{key}.json" for key in self.known_slices)
        if event_dir.combined:
            known_files.add(f"{name}.json")
        known_keys = {BASIC_KEY, OBSERVATION_KEY, *self.known_slices}
        return LegacyEvent(
            event_id=int(name),
            dir=event_dir,
            event=event,
            observation=observation,
            slices=tuple(slices),
            payloads=loaded if payloads else None,
            extra_files=tuple(sorted(entries - known_files)),
            combined_extra_keys=tuple(sorted(str(k) for k in combined if k not in known_keys)) if combined else (),
            problems=tuple(problems),
        )

    def _read_slice(
        self,
        base: str,
        key: str,
        entries: Set[str],
        combined: Optional[Dict[str, Any]],
        combined_rel: str,
        combined_stat: Optional[os.stat_result],
        unavailable: int,
        status: Any,
        loaded: Dict[str, Any],
        problems: List[LegacyProblem],
    ) -> Optional[LegacySlice]:
        """Bir dilimin durumu (bölüm 2.3'teki eşleme). Dosyası, birleşik kopyası ve işareti yoksa None."""
        payload: Any = _MISSING
        path: Optional[str] = None
        in_combined = False
        stat: Optional[os.stat_result] = None
        unreadable: Optional[str] = None

        if f"{key}.json" in entries:
            path = f"{base}/{key}.json"
            try:
                payload = self._load(path)
                stat = self._stat(path)
            except PayloadMissing:
                path = None  # tarama sırasında silindi
            except StoreError as e:
                unreadable = _detail(e)
                problems.append(_problem_of(e, path))
        if payload is _MISSING and combined is not None and combined.get(key) is not None:
            payload, path, in_combined, stat, unreadable = combined[key], combined_rel, True, combined_stat, None

        count = max(unavailable, 0)
        empty_count = min(count, _confirmed_empty(status))
        marks = {
            "empty_count": empty_count,
            "unverified_empty_count": count - empty_count,
            "empty_at": _parse_ts(status["empty"].get("at")) if _confirmed_empty(status) else None,
        }
        error = _error_mark(status)
        corrupt = LegacySliceError(REASON_CORRUPT)

        if payload is not _MISSING:
            loaded[key] = payload
            body = slice_body_state(key, payload)  # "bu yanıtta veri var mı": üç yanıt (sofascore_scraper/slices.py)
            if body == BODY_MALFORMED:
                problems.append(LegacyProblem(path or base, PROBLEM_MALFORMED, f"{key}: unexpected shape"))
                state, error = STATE_ERROR, corrupt
            elif body == BODY_DATA:
                state = STATE_OK
            else:
                state = STATE_ERROR if error is not None else STATE_EMPTY
            return LegacySlice(
                key=key, state=state, has_payload=True, path=path, in_combined=in_combined,
                fetched_at=stat.st_mtime if stat else None,
                size=stat.st_size if stat and not in_combined else None,
                error=error, **marks,
            )
        if unreadable is not None:
            return LegacySlice(key=key, state=STATE_ERROR, has_payload=False, path=path, error=corrupt, **marks)
        if error is not None:
            return LegacySlice(key=key, state=STATE_ERROR, has_payload=False, error=error, **marks)
        if count > 0:
            return LegacySlice(key=key, state=STATE_EMPTY, has_payload=False, **marks)
        return None

    def iter_events(self, *, payloads: bool = True, report: Optional[LegacyReport] = None) -> Iterator[LegacyEvent]:
        """
        Tanınan bütün maçlar, kimliğe göre sıralı, her maç bir kez. Aynı maçın birden çok dizini varsa olay
        yükü en yeni olan okunur; ötekiler `duplicates` ve `report.superseded` içinde bildirilir. Okunamayan
        ya da id'si tutmayan dizin atlanır ve `report.problems`a yazılır; tarama durmaz.
        """
        found, orphans = self._walk_details()
        if report is not None:
            report.problems.extend(orphans)
        groups: Dict[str, List[LegacyEventDir]] = {}
        for candidate in found:
            groups.setdefault(candidate.name, []).append(candidate)
        for name in sorted(groups, key=_id_order):
            ordered = sorted(groups[name], key=lambda d: (-d.mtime_ns, _FORM_RANK[d.form], d.path))
            for position, candidate in enumerate(ordered):
                try:
                    event = self.read_event(candidate, payloads=payloads)
                except StoreError as e:
                    if report is not None:
                        report.problems.append(_problem_of(e, candidate.path))
                    continue
                rest = tuple(d.path for d in ordered[position + 1:])
                if report is not None:
                    report.problems.extend(event.problems)
                    report.superseded.extend(LegacySuperseded("event", name, p, candidate.path) for p in rest)
                yield replace(event, duplicates=rest) if rest else event
                break

    def read_payload(self, event_dir: Union[str, LegacyEventDir], key: str = EVENT_KEY, *,
                     raw: bool = False) -> Any:
        """
        Eski düzendeki bir maçın tek bir yükü (bölüm 5.2): `<dizin>/<anahtar>.json` (`event` → basic.json),
        dosya yoksa birleşik dosyadaki anahtar. Yük yoksa None. raw=True ayrıştırmadan bayt döndürür:
        dosyadaki baytlar, birleşik dosyadan gelen dilimde kurallı (sıkıştırılmamış) JSON baytları.
        """
        base = event_dir if isinstance(event_dir, str) else event_dir.path
        base = base.strip("/")
        file_key = BASIC_KEY if key == EVENT_KEY else key
        path = self.resolve(f"{base}/{file_key}.json")
        try:
            return codec.read_raw(path) if raw else codec.read_payload(path)
        except PayloadMissing:
            pass
        try:
            combined = self._load(f"{base}/{base.rsplit('/', 1)[-1]}.json")
        except PayloadMissing:
            return None
        value = combined.get(file_key) if isinstance(combined, dict) else None
        if value is None:
            return None
        return codec.canonical_bytes(value) if raw else value

    # -- program: tur dosyaları ve olay sayfaları --

    def _season_dirs(self, problems: List[LegacyProblem]) -> Iterator[Tuple[int, str, int, str]]:
        """(turnuva id, lig dizini yolu, sezon id, sezon dizini yolu); adı id ile başlamayan dizin bildirilir."""
        for league in self._entries(MATCHES_DIR, problems):
            if not _is_dir(league):
                continue
            league_rel = f"{MATCHES_DIR}/{league.name}"
            league_match = _PREFIX_RE.match(league.name)
            if not league_match:
                problems.append(LegacyProblem(league_rel, PROBLEM_NAME, "the league directory name does not start with an id"))
                continue
            for season in self._entries(league_rel, problems):
                if not _is_dir(season):
                    continue
                season_rel = f"{league_rel}/{season.name}"
                season_match = _PREFIX_RE.match(season.name)
                if not season_match:
                    problems.append(LegacyProblem(season_rel, PROBLEM_NAME, "the season directory name does not start with an id"))
                    continue
                yield int(league_match.group(1)), league_rel, int(season_match.group(1)), season_rel

    def schedule_pages(self, report: Optional[LegacyReport] = None) -> List[LegacySchedulePage]:
        """
        Bütün tur dosyaları ve olay sayfaları, mtime sırasıyla (eşitlikte yola göre): katalog liste
        satırlarını bu sırayla işler (bölüm 3.4). İçerik okunmaz. Bir sezonun aynı sayfası iki dizinde
        duruyorsa (ya da aynı dizinde adları yalnızca büyük/küçük harfle ayrılan iki tur dosyası varsa) en
        yenisi geçerlidir (eşitlikte yolu küçük olan); ötekilerin `superseded_by` alanı doludur.
        """
        problems: List[LegacyProblem] = []
        pages: List[LegacySchedulePage] = []
        for tournament_id, _league_rel, season_id, season_rel in self._season_dirs(problems):
            for entry in self._entries(season_rel, problems):
                if _is_dir(entry) or not entry.name.endswith(".json"):
                    continue
                rel = f"{season_rel}/{entry.name}"
                parsed = schedule_sub(entry.name)
                if parsed is None:
                    problems.append(LegacyProblem(rel, PROBLEM_NAME, "not recognised as a schedule file"))
                    continue
                st = self._stat(rel)
                if st is None:
                    continue
                pages.append(LegacySchedulePage(
                    tournament_id=tournament_id, season_id=season_id, sub=parsed[1], kind=parsed[0], path=rel,
                    mtime_ns=st.st_mtime_ns, size=st.st_size,
                ))
        winners: Dict[Tuple[int, int, str], LegacySchedulePage] = {}
        for page in sorted(pages, key=lambda p: (-p.mtime_ns, p.path)):
            winners.setdefault((page.tournament_id, page.season_id, page.sub), page)
        out: List[LegacySchedulePage] = []
        for page in sorted(pages, key=lambda p: (p.mtime_ns, p.path)):
            winner = winners[(page.tournament_id, page.season_id, page.sub)]
            if winner is not page:
                page = replace(page, superseded_by=winner.path)
                if report is not None:
                    key = f"{page.tournament_id}/{page.season_id}/{page.sub}"
                    report.superseded.append(LegacySuperseded("schedule", key, page.path, winner.path))
            out.append(page)
        if report is not None:
            report.problems.extend(problems)
        return out

    def read_schedule(self, page: LegacySchedulePage) -> LegacySchedule:
        """
        Program sayfasını okur. `_complete` anahtarı yükten çıkar ve `meta`ya taşınır: {"complete": bool}.
        Süzülerek yazılmış sayfa (`_complete`'siz tur dosyası ya da olay sayfası) {"filtered": True} alır
        (bölüm 5.2). Dosya yoksa PayloadMissing, okunamıyorsa ya da nesne değilse PayloadCorrupt.
        """
        data = self._load_object(page.path)
        if page.kind == "round" and "_complete" in data:
            meta: Dict[str, Any] = {"complete": bool(data["_complete"])}
        else:
            meta = {"filtered": True}
        return LegacySchedule(page=page, payload={k: v for k, v in data.items() if k != "_complete"}, meta=meta)

    # -- sezon özetleri (türetilmiş) --

    def summary_files(self, report: Optional[LegacyReport] = None) -> List[LegacySummaryFile]:
        """
        Sezon özetleri, yola göre sıralı: lig dizinindeki `<sezon id>_..._summary.json|csv` ve eski
        `_matches.csv`, sezon dizinindeki `*_summary.csv|_matches.csv` (ilk sürümün tur başına dosyaları).
        Kural, 2.x'te özetleri arayan kodun kuralıdır (sonuncusu `MatchDataFetcher._season_summary_files`; P15 ile
        kalktı).
        """
        problems: List[LegacyProblem] = []
        out: List[LegacySummaryFile] = []

        def add(tournament_id: int, rel: str, name: str, season_id: Optional[int], nested: bool) -> None:
            if name.endswith(SUMMARY_CSV_SUFFIX):
                kind = "summary_csv"
            elif name.endswith(MATCHES_CSV_SUFFIX):
                kind = "matches_csv"
            elif name.endswith(SUMMARY_JSON_SUFFIX) and not nested:
                kind = "summary_json"
            else:
                return
            st = self._stat(rel)
            if st is not None:
                out.append(LegacySummaryFile(tournament_id, season_id, kind, rel, st.st_mtime_ns, nested))

        for league in self._entries(MATCHES_DIR, problems):
            league_match = _PREFIX_RE.match(league.name)
            if not _is_dir(league) or not league_match:
                continue  # adı uymayan lig dizinini schedule_pages bildirir
            tournament_id = int(league_match.group(1))
            league_rel = f"{MATCHES_DIR}/{league.name}"
            for entry in self._entries(league_rel, problems):
                prefix = _PREFIX_RE.match(entry.name)
                season_id = int(prefix.group(1)) if prefix else None
                if not _is_dir(entry):
                    add(tournament_id, f"{league_rel}/{entry.name}", entry.name, season_id, False)
                    continue
                for inner in self._entries(f"{league_rel}/{entry.name}", problems):
                    if not _is_dir(inner):
                        add(tournament_id, f"{league_rel}/{entry.name}/{inner.name}", inner.name, season_id, True)
        if report is not None:
            report.problems.extend(problems)
        return sorted(out, key=lambda s: s.path)

    def read_summary_rows(self, summary: LegacySummaryFile) -> List[Dict[str, str]]:
        """Özet CSV'sinin satırları (başlık → metin). JSON özeti için `read_summary_json` kullanılır."""
        if not summary.path.endswith(".csv"):
            raise LayoutError(f"Not a CSV summary: {self.resolve(summary.path)}", path=self.resolve(summary.path))
        text = self._read_text(summary.path)
        return [dict(row) for row in csv.DictReader(io.StringIO(text, newline=""))]

    def read_summary_json(self, summary: LegacySummaryFile) -> Any:
        """`<sezon>_summary.json`: özetin kurulduğu sayfa nesnelerinin listesi."""
        return self._load(summary.path)

    def _read_text(self, rel: str) -> str:
        path = self.resolve(rel)
        try:
            return files.read_bytes(path).decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise PayloadCorrupt(f"The file is not UTF-8 ({e}): {path}", path=path, detail=str(e)) from e

    # -- sezon listeleri --

    def season_lists(self, league_names: Optional[Mapping[int, str]] = None,
                     report: Optional[LegacyReport] = None) -> List[LegacySeasonList]:
        """
        Bütün sezon listeleri, yola göre sıralı. Dosya adları: `<id>_<ad>_seasons.json`, `<id>_seasons.json`,
        `<ad>_seasons.json` ve tek dosyalık `league_seasons.csv` (turnuva başına bir kayıt verir).

        Bir turnuvanın birden çok JSON dosyası varsa adı ne olursa olsun en yenisi geçerlidir (eşitlikte
        yolu küçük olan); ötekilerin `superseded_by` alanı doludur. CSV yalnızca JSON dosyası olmayan turnuva
        için geçerlidir. `<ad>_seasons.json`'ın turnuvası addan bulunur: `league_names` (id → ad; boşluklu
        ya da `safe_name` biçimi) verilmediyse ya da ad orada yoksa `tournament_id` None kalır.
        """
        problems: List[LegacyProblem] = []
        by_label: Dict[str, int] = {}
        for league_id, league_name in (league_names or {}).items():
            by_label.setdefault(str(league_name), int(league_id))
            by_label.setdefault(safe_name(league_name), int(league_id))

        found: List[LegacySeasonList] = []
        for entry in self._entries(SEASONS_DIR, problems):
            if _is_dir(entry) or not entry.name.endswith(SEASONS_SUFFIX):
                continue
            rel = f"{SEASONS_DIR}/{entry.name}"
            payload = self._load_dict(rel, problems)
            st = self._stat(rel)
            if payload is None or st is None:
                continue
            stem = entry.name[: -len(SEASONS_SUFFIX)]
            prefix = re.fullmatch(r"([0-9]+)(?:_(.*))?", stem)
            if prefix:
                tournament_id: Optional[int] = int(prefix.group(1))
                label = prefix.group(2) or ""
            else:
                tournament_id, label = by_label.get(stem), stem
                if tournament_id is None:
                    problems.append(LegacyProblem(rel, PROBLEM_UNRESOLVED, f"no tournament id in the file name: {stem}"))
            found.append(LegacySeasonList(tournament_id, label, "json", rel, st.st_mtime_ns, payload))
        found.extend(self._seasons_from_csv(problems))

        winners: Dict[int, LegacySeasonList] = {}
        for item in sorted(found, key=lambda s: (s.kind != "json", -s.mtime_ns, s.path)):
            if item.tournament_id is not None:
                winners.setdefault(item.tournament_id, item)
        out: List[LegacySeasonList] = []
        for item in sorted(found, key=lambda s: (s.path, s.tournament_id or 0)):
            winner = winners.get(item.tournament_id) if item.tournament_id is not None else None
            if winner is not None and winner is not item:
                item = replace(item, superseded_by=winner.path)
                if report is not None:
                    report.superseded.append(
                        LegacySuperseded("season_list", str(item.tournament_id), item.path, winner.path)
                    )
            out.append(item)
        if report is not None:
            report.problems.extend(problems)
        return out

    def _seasons_from_csv(self, problems: List[LegacyProblem]) -> List[LegacySeasonList]:
        """league_seasons.csv (ilk sürüm): `Lig ID`, `Sezon ID`, `Sezon Adı`, `Sezon Yılı` sütunları."""
        st = self._stat(SEASONS_CSV)
        if st is None:
            return []
        try:
            rows = list(csv.DictReader(io.StringIO(self._read_text(SEASONS_CSV), newline="")))
        except (StoreError, csv.Error) as e:
            problems.append(LegacyProblem(SEASONS_CSV, PROBLEM_CORRUPT, str(e)))
            return []
        seasons: Dict[int, List[Dict[str, Any]]] = {}
        labels: Dict[int, str] = {}
        for number, row in enumerate(rows, start=2):
            try:
                league_id, season_id = int(row["Lig ID"]), int(row["Sezon ID"])
                season = {"id": season_id, "name": row["Sezon Adı"], "year": row["Sezon Yılı"]}
            except (KeyError, TypeError, ValueError):
                problems.append(LegacyProblem(SEASONS_CSV, PROBLEM_MALFORMED, f"line {number}"))
                continue
            seasons.setdefault(league_id, []).append(season)
            labels.setdefault(league_id, safe_name(row.get("Liga Adı") or ""))
        return [
            LegacySeasonList(league_id, labels[league_id], "csv", SEASONS_CSV, st.st_mtime_ns, {"seasons": listed})
            for league_id, listed in sorted(seasons.items())
        ]

    # -- değişiklik günlüğü ve izleyici dosyaları --

    def _read_lines(self, rel: str, report: Optional[LegacyReport]) -> List[LegacyLine]:
        """
        JSON Lines dosyası; dosya yoksa boş liste. Sıra numarası satır numarasıdır, okunamayan ya da boş
        satırların numarası da harcanır (numaralar sonradan kaymasın). Satır sonu olmayan son satır yarım
        kalmış bir yazmadır: atlanır ve bildirilir (bölüm 8.5).
        """
        try:
            data = files.read_bytes(self.resolve(rel))
        except PayloadMissing:
            return []
        problems: List[LegacyProblem] = []
        parts = data.split(b"\n")
        torn = parts.pop()  # son "\n"den sonrası: tam dosyada boş
        if torn.strip():
            problems.append(LegacyProblem(rel, PROBLEM_TORN, f"line {len(parts) + 1}"))
        out: List[LegacyLine] = []
        for number, raw in enumerate(parts, start=1):
            if not raw.strip():
                continue
            try:
                line = raw.decode("utf-8").rstrip("\r")
                row = json.loads(line)
            except (ValueError, RecursionError) as e:  # JSONDecodeError ve UnicodeDecodeError birer ValueError'dır
                problems.append(LegacyProblem(rel, PROBLEM_CORRUPT, f"line {number}: {e}"))
                continue
            if not isinstance(row, dict):
                problems.append(LegacyProblem(rel, PROBLEM_MALFORMED, f"line {number}: not a JSON object"))
                continue
            out.append(LegacyLine(seq=number, row=row, line=line))
        if report is not None:
            report.problems.extend(problems)
        return out

    def change_log(self, report: Optional[LegacyReport] = None) -> List[LegacyLine]:
        """score_changes.jsonl satırları; `seq` satır numarasıdır (bölüm 5.2, 8.5)."""
        return self._read_lines(CHANGES_FILE, report)

    def watch_events(self, report: Optional[LegacyReport] = None) -> List[LegacyLine]:
        """watch_events.jsonl: izleyicinin olay geçmişi (hiçbir kod okumaz; taşınmaz, yalnızca dökülebilir)."""
        return self._read_lines(WATCH_EVENTS_FILE, report)

    def watch_states(self, report: Optional[LegacyReport] = None) -> List[LegacyWatchState]:
        """watch_state_<spor>.json dosyaları, spora göre sıralı. Bozuk dosya atlanır ve bildirilir."""
        problems: List[LegacyProblem] = []
        out: List[LegacyWatchState] = []
        for entry in self._entries("", problems):
            match = _WATCH_STATE_RE.fullmatch(entry.name)
            if not match or _is_dir(entry):
                continue
            state = self._load_dict(entry.name, problems)
            st = self._stat(entry.name)
            if state is not None and st is not None:
                out.append(LegacyWatchState(match.group(1), entry.name, st.st_mtime_ns, state))
        if report is not None:
            report.problems.extend(problems)
        return sorted(out, key=lambda s: s.sport)


__all__ = [
    "LegacyReader",
    "LegacyReport",
    "LegacyProblem",
    "LegacySuperseded",
    "LegacyEventDir",
    "LegacyEvent",
    "LegacySlice",
    "LegacySliceError",
    "LegacyObservation",
    "LegacySchedulePage",
    "LegacySchedule",
    "LegacySummaryFile",
    "LegacySeasonList",
    "LegacyLine",
    "LegacyWatchState",
    "schedule_sub",
    "safe_name",
    "league_dir_name",
]
