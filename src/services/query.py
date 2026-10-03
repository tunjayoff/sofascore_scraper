"""
Sorgu servisi: yüzlerin (web, CLI, kitaplık) okuma tarafı (docs/design/02-services.md 2.3 ve 2.7).

Okuyucular dosya ağacını gezmez; her soru deponun okuma API'sine sorulur (`Store.events`, `Store.entities`,
`Store.changes`: docs/design/01-storage.md 2.3). Katalog, depo açılırken dosyalarla eşitlenir ve eski düzen
yazıcılarının her yazmasından sonra güncellenir (gölge kip, 01-storage.md 3.5); iki düzen de (eski
`match_details/` ağacı ve v3) aynı çağrılarla okunur.

Üç iş var: maç detayı (plan maddesi RD-1: `GET /api/matches/{id}` yanıtının bugünkü sözlüğü), maç listeleri
(RD-2: `GET /api/matches` ile `GET /api/seasons/{id}/matches` satırları) ve indirme planı (RD-3: hangi maçın
detayı eksik, hangisi yenilenmeli, bir ligin hangi maçları listelerde geçiyor; `GET /api/leagues/{id}/missing-
details` ve src/match_data_fetcher.py'deki planlayıcılar). Eski `/api` yanıt biçimlerini üreten işlevler burada
durur ve eski uç noktalarla birlikte kaldırılır (P30).

Maç detayında eski okuyucudan (dizinden dosya dosya okuyan `routes/matches._get_match_details_sync`) farklar;
hepsi yalnızca eski biçimli kayıtlarda görünür, bugünkü kodun yazdığı dizinlerde yanıt aynıdır (01-storage.md
5.1, 5.2):

  * Yalnızca birleşik dosyası (`<id>/<id>.json`) olan dizin de bir maçtır; eskiden 404 dönüyordu.
  * Bir dilim önce kendi dosyasından, dosya yoksa birleşik dosyadan okunur. Eskiden birleşik dosya varsa
    yalnızca o dönüyordu (içindeki tanınmayan anahtarlarla birlikte).
  * Okunamayan (yarıda kesilmiş) dilim dosyası yalnızca o dilimi düşürür; eskiden bütün maç 500 dönüyordu.
  * Aynı maç iki dizinde duruyorsa olay yükü en yeni olan kopya okunur.

Maç listelerinin satırları eskiden sezon özeti CSV'lerinden (`matches/<lig>/<sezon>_summary.csv`; on sütun,
src/match_fetcher.py `_save_season_summary`) okunuyordu; şimdi kataloğun maç satırlarından aynı sütunlarla
kurulur (`LEGACY_LIST_COLUMNS`). Farklar:

  * Liste, kataloğun bildiği her maçtır: yalnızca `_matches.csv`'si olan sezonun maçları ve hiçbir programda
    geçmeyen ama detayı indirilmiş maçlar da (panodaki sayımla aynı kural, plan maddesi RD-4).
  * "Yalnızca bitmiş maçlar" ayarı (FETCH_ONLY_FINISHED) okurken uygulanır: açıkken bitmiş maçlar ve, bitmemiş
    olduğu halde detayı indirilmiş maçlar listelenir. Eskiden özet yazılırken süzülüyordu; ayar kapalıyken
    yazılmış bir sezonun bitmemiş maçları ayar açıkken de listede kalıyordu.
  * Detayı indirilmiş maçın satırı saklanan olay yükündendir (skor ve durum programdakinden yeni olabilir).
  * `has_details` lig süzgecinden bağımsızdır; düz ya da kimliksiz lig dizinindeki detayları da görür.
  * Bir maç sezonun listesinde bir kez geçer (iki özet dosyası olan sezonda eskiden iki kez geçiyordu); sezon
    listesinin sırası başlangıç zamanıdır (eskiden özet dosyalarının dizin ve satır sırası).
  * Dışa aktarma CSV'sine (`match_details/processed/all_matches_*.csv`) geri düşülmez (tasarım kararı S14):
    özeti ve detayı olmayan bir veri dizininin listesi boştur.

İndirme planı (RD-3) eskiden dosyalardan çıkarılıyordu: maçın ihtiyacı (`full` / `refill` / `refresh` / `none`)
bütün dilim dosyaları ve işaret dosyaları (`_unavailable.json`) okunarak, yenilenecek maçlar
`match_details/<lig>/<sezon>/<id>` ağacı gezilerek, bir ligin maçları ve eksik detaylar sezon özeti CSV'lerinden.
Şimdi hepsi kataloğa sorulur (`Store.events.missing`, `refresh_candidates` ve maç listesi); hiçbir yük okunmaz.
Bugünkü kodun yazdığı dizinlerde kümeler ve kararlar aynıdır; sıra yalnızca bir ligin maç adaylarında
değişir. Farklar:

  * Yenilenecek maçlar ağacın her yerinden gelir: düz (`match_details/<id>`) ve `_no_tournament/` altındaki
    kayıtlar da; lig süzgeci dizin adının `<lig id>_` önekine değil, maçın turnuvasına bakar (kimliksiz lig
    dizinindeki kayıt da bulunur). Sıra eski gezintininkidir (kayıt dizinlerinin yolu).
  * Bir ligin maçları (`detail_candidates`) ve eksik detayları (`missing_details_legacy`) programlarda ve
    özetlerde geçen maçlardır. "Yalnızca bitmiş maçlar" ayarı okurken uygulanır: bitmiş, detayı indirilmiş
    (maç listeleriyle aynı kural) ya da durumu bilinmeyen maçlar (durum sütunu olmayan özet satırı). Eskiden
    özetler yazılırken süzülüyordu; ayar kapalıyken yazılmış bir sezonun bitmemiş maçları ayar açıkken artık
    plana girmez. Sezonlar yine büyükten küçüğe, bir sezonun içinde sıra başlangıç zamanıdır (eskiden özet
    dosyasının satır sırası); lig verilmezse ligler kimlik sırasıyla (eskiden dizin listeleme sırası).
  * Eksik detaylarda "detayı var", kataloğun "olay yükü var" bilgisidir: yalnızca birleşik dosyası olan dizin
    de detaydır (eskiden yalnızca `basic.json` sayılıyordu). İlk sürümün sezon dizinindeki tur özetleri
    (`<sezon>/round_<n>_matches.csv`) de okunur (katalog onları da dizinler).
"""
from __future__ import annotations

import heapq
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import (TYPE_CHECKING, Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set,
                    Tuple)

from src import refresh
from src.logger import get_logger
from src.paths import league_dir_name
from src.sports import DETAIL_SLICES, slices_for, sport_slugs
from src.status import StatusClass
from src.store import EventQuery, PayloadCorrupt, PayloadMissing, Scope, StoreError

if TYPE_CHECKING:
    from src import schema
    from src.store import EventRow, Store

logger = get_logger("QueryService")

EVENT_KEY = "event"  # `/event/{id}` yükünün depodaki dilim anahtarı
LEGACY_EVENT_KEY = "basic"  # aynı yükün eski yanıtlardaki (ve basic.json'daki) adı
# SQLite'ın saklayabildiği kimlik aralığı: dışındaki bir sayı hiçbir maçın kimliği olamaz
_ID_RANGE = (-(2 ** 63), 2 ** 63 - 1)

# Sezon özeti CSV'sinin on sütunu, sırasıyla (src/match_fetcher.py `_save_season_summary`)
LEGACY_LIST_COLUMNS: Tuple[str, ...] = ("round", "match_id", "home_team", "away_team", "home_score", "away_score",
                                        "match_date", "status", "tournament", "season")
SORT_ASC = "asc"
SORT_DESC = "desc"
FINISHED_CLASSES: Tuple[str, ...] = (StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value)
_NOT_FINISHED: Tuple[str, ...] = tuple(m.value for m in StatusClass if m.value not in FINISHED_CLASSES)
_STORE_SORT = {SORT_ASC: "start_asc", SORT_DESC: "start_desc"}
_LEGACY_LAYOUT = "legacy"
_LISTING_SOURCE = "listing"  # `events.row_source`: maç yalnızca bir listeden biliniyor
_MATCH_DETAILS_DIR = "match_details"
_ROUND_SUB_RE = re.compile(r"round_(\d+)(?:_.*)?")
# Tarih süzgecinin ISO önekleri: "2026", "2026-09", "2026-09-15", "2026-09-15T13", "…T13:30", "…T13:30:00".
# `match_date` metninde dört basamaklı tek sayı yıldır; böyle bir metin yalnızca başta geçebilir, yani bir
# başlangıç zamanı aralığıdır.
_DATE_PREFIX_RE = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2})(?:T(\d{2})(?::(\d{2})(?::(\d{2}))?)?)?)?)?")
_DATE_SLACK = 86400  # saniye: yerel saatin yaz saati geçişlerine karşı aralığın iki yanındaki pay


def legacy_detail_keys() -> Tuple[str, ...]:
    """
    Eski maç detayı yanıtındaki dilimler, yanıttaki sırayla: dilim tablosunun spora bağlı olmayan `required`
    satırları (src/sports.py, DETAIL_SLICES). İsteğe bağlı dilimler (ör. tenisin `point_by_point`'i) ve spora
    özel dilimler (ör. kriketin `innings`'i) eski yanıtta yoktur.
    """
    return tuple(detail.key for detail in DETAIL_SLICES if detail.required and detail.sports is None)


# -- indirme planı (RD-3) -------------------------------------------------------------------------------

# Bir maçın ihtiyacı (src/match_data_fetcher.py `_needs_detail_fetch`'in dönüş değerleri)
NEED_FULL = "full"  # kayıt yok ya da olay yükü yok: baştan indirilir
NEED_REFILL = "refill"  # olay yükü var, beklenen dilimlerden en az biri eksik
NEED_REFRESH = "refresh"  # dilimler tam ama kayıt geçici ve yenileme zamanı geldi (src/refresh.py)
NEED_NONE = "none"  # tamam
# Bu kadar kesin "veri yok" yanıtından sonra dilim o maçta artık beklenmez (src/match_data_fetcher.py
# UNAVAILABLE_AFTER_ATTEMPTS ile aynı değer; çağıran kendi değerini verebilir)
DEFAULT_EMPTY_THRESHOLD = 2
_ID_CHUNK = 500  # bir sorgunun kapsamına yazılan en çok kimlik


def required_detail_keys() -> Dict[str, Tuple[str, ...]]:
    """
    `Store.events.missing`'in `required` argümanı (`exclusive=True` ile): `""` altında sporu kayıt defterinde
    olmayan ya da bilinmeyen maçın beklediği dilimler, kayıtlı her sporun altında o sporun bütün beklediği
    dilimler. Kural `slices_for(spor, required_only=True)`'dur (dilim tablosu, src/sports.py). Bir spor ortak
    bir dilimi beklemeyebildiği (`not_in`, `optional_in`) için sporlar `""`'nin üstüne eklenmez, onun yerine geçer.
    """
    required: Dict[str, Tuple[str, ...]] = {"": tuple(detail.key for detail in slices_for(None, required_only=True))}
    for sport in sport_slugs():
        required[sport] = tuple(detail.key for detail in slices_for(sport, required_only=True))
    return required


@dataclass(frozen=True)
class RefreshPolicy:
    """
    Yenileme politikası (src/refresh.py) saniye cinsinden, bir an için. window_s <= 0: politika kapalı.
    include_unobserved: gözlemi olmayan (eski) kayıtlar da yenilenir (--refresh-legacy).
    """

    now: float
    window_s: float
    min_interval_s: float
    include_unobserved: bool = False

    @classmethod
    def current(cls, now: Optional[float] = None) -> "RefreshPolicy":
        """Ayarların o anki değerleri (REFRESH_WINDOW_HOURS, REFRESH_MIN_INTERVAL_HOURS, REFRESH_LEGACY)."""
        return cls(
            now=time.time() if now is None else now,
            window_s=refresh.refresh_window_hours() * 3600,
            min_interval_s=refresh.refresh_min_interval_hours() * 3600,
            include_unobserved=refresh.refresh_legacy_enabled(),
        )


class CatalogNotCurrent(StoreError):
    """
    Katalog dosyalarla eşit değil (açılışta eşitlenemedi ya da bir yazma kancası onu güncelleyemedi): indirme
    planı ondan çıkarılmaz. Eksik bir katalog her maçı "eksik" gösterir ve gereksiz istek yaptırırdı.
    """

    default_message = "The data folder's index (.meta/catalog.db) is not up to date; no download is planned from it"


def _event_ids(values: Iterable[Any]) -> List[int]:
    """Kimlik listesi: tam sayı olarak yazılabilenler, tekrarsız ve sıralı; geri kalanı hiçbir maçın kimliği değildir."""
    found = set()
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            number = value
        else:
            text = str(value)
            if not (text.isascii() and text.isdigit()) or str(int(text)) != text:
                continue
            number = int(text)
        if _ID_RANGE[0] <= number <= _ID_RANGE[1]:
            found.add(number)
    return sorted(found)


def _chunks(values: Sequence[int], size: int = _ID_CHUNK) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


@dataclass(frozen=True)
class LegacyMatchPage:
    """`GET /api/matches` için bir sayfa: satırlar (eski sözlük biçiminde) ve süzgeçlere uyan bütün maçların sayısı."""

    items: Tuple[Dict[str, Any], ...]
    total: int


# -- API v1: şema v1 kayıtlarının türleri (plan maddesi P21) --------------------------------------------------------

V1_SORTS: Tuple[str, ...] = ("start_desc", "start_asc")
CHANGE_ORDERS: Tuple[str, ...] = ("asc", "desc")
_CHANGE_SCAN = 500  # süzgeçli değişiklik sayfasında bir okumanın en çok satırı


@dataclass(frozen=True)
class EventFilter:
    """
    `GET /api/v1/events` süzgeçleri; boş alan süzmez, dolu alanlar birlikte (VE) uygulanır.

    start_from / start_to: başlangıç zamanı aralığı (epoch saniye), iki uç dahil. has_details: True = olay yükü
    saklananlar, False = yalnızca bir listeden bilinenler. text: yarışmacı adında geçen metin. followed: yalnızca
    etkin turnuva takiplerinin maçları (`Scope.followed`).
    """

    sport: Optional[str] = None
    tournament_ids: Tuple[int, ...] = ()
    season_ids: Tuple[int, ...] = ()
    participant_ids: Tuple[int, ...] = ()
    status_classes: Tuple[str, ...] = ()
    start_from: Optional[float] = None
    start_to: Optional[float] = None
    has_details: Optional[bool] = None
    text: Optional[str] = None
    followed: bool = False


@dataclass(frozen=True)
class SliceSummary:
    """
    Bir maçın dilim özeti: seçilen dilimler (sporun ve evresinin kayıt defteri varsayılanı) ve onlardan kaçının
    durumu `ok`, `empty`, `error`. İstenmemiş dilim (`not_requested`) üç sayıya da girmez.
    """

    selected: int
    ok: int
    empty: int
    error: int


@dataclass(frozen=True)
class EventPage:
    """Bir maç sayfası: kayıtlar, sonraki sayfanın konumu ve istendiyse maç başına dilim özeti."""

    items: Tuple["schema.Event", ...]
    next_cursor: Optional[str]
    slices: Optional[Mapping[int, SliceSummary]] = None


@dataclass(frozen=True)
class RawPayload:
    """
    Saklanan bir SofaScore yükü, olduğu gibi (sıkıştırması açılmış JSON baytları). sha256: baytların özeti
    (HTTP `ETag`); fetched_at: yükün alındığı an (UTC, ISO 8601), bilinmiyorsa None.
    """

    data: bytes
    sha256: str
    fetched_at: Optional[str]


@dataclass(frozen=True)
class TournamentEntry:
    """Bir turnuva kaydı, kategorisi (katalogda yoksa None) ve takip edilip edilmediği (her kaynaktan bir takip)."""

    tournament: "schema.Tournament"
    category: Optional["schema.Category"]
    followed: bool = False


@dataclass(frozen=True)
class ChangePage:
    """Değişiklik günlüğünün bir sayfası; next_cursor: devam için verilecek sıra numarası (metin), yoksa None."""

    items: Tuple["schema.Change", ...]
    next_cursor: Optional[str]


def refresh_window_seconds() -> float:
    """Yenileme penceresi, saniye (ayar `REFRESH_WINDOW_HOURS`): kayıtların `quality.settlement` hesabı için."""
    return refresh.refresh_window_hours() * 3600


def _valid_id(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and _ID_RANGE[0] <= value <= _ID_RANGE[1]



class QueryService:
    """
    Deponun okuma API'si üzerinde, yüzlerin kullandığı sorgular. Durum tutmaz; kilit almaz.

    API v1'in okumaları (`events`, `event`, `event_slices`, `event_slice`, `raw`, `tournaments`, `tournament`,
    `seasons`, `season`, `season_slice`, `changes`) şema v1 kayıtları döndürür (src/schema). Bilinmeyen kimlik
    için None dönerler (yüz onu `not_found`a çevirir); depolama hatası StoreError olarak çıkar.
    """

    def __init__(self, store: "Store") -> None:
        self._store = store

    def match_detail_legacy(self, event_id: int) -> Optional[Dict[str, Any]]:
        """
        Bir maçın saklanan detayı, `GET /api/matches/{id}` yanıtının biçiminde: önce `basic` (`/event/{id}`
        yükü), ardından yükü olan her `required` dilim, dilim tablosunun sırasıyla. İçeriği JSON `null` olan
        dilim dosyası anahtarıyla ve None değeriyle yer alır (bugünkü gibi).

        Maç bilinmiyorsa ya da olay yükü yoksa (yalnızca bir program sayfasından bilinen maç) None döner.
        Depolama hatası (G/Ç, izin) StoreError olarak çağırana çıkar.
        """
        if isinstance(event_id, bool) or not isinstance(event_id, int):
            raise ValueError(f"event_id: expected an integer, got {event_id!r}")
        if not _ID_RANGE[0] <= event_id <= _ID_RANGE[1]:
            return None
        keys = legacy_detail_keys()
        found = self._payloads(event_id, (EVENT_KEY, *keys))
        if EVENT_KEY not in found:
            return None
        detail: Dict[str, Any] = {LEGACY_EVENT_KEY: found[EVENT_KEY]}
        for key in keys:
            if key in found:
                detail[key] = found[key]
        return detail

    # -- maç listeleri (eski `/api/matches` ve `/api/seasons/{id}/matches`) -----------------------------

    def matches_legacy(self, *, tournament_ids: Sequence[int] = (), season_id: Optional[int] = None,
                       date: Optional[str] = None, details: Optional[bool] = None, only_finished: bool = True,
                       sort: str = SORT_DESC, offset: int = 0, limit: int = 25,
                       league_names: Optional[Mapping[int, str]] = None) -> LegacyMatchPage:
        """
        `GET /api/matches` satırları: `LEGACY_LIST_COLUMNS`, ardından `league_folder` ve `has_details`.

        tournament_ids: boş = süzgeç yok. date: `match_date` metninde geçen metin (eskisi gibi alt dize).
        details: True = detayı olanlar, False = olmayanlar. only_finished: "yalnızca bitmiş maçlar" ayarı (modül
        belgesi). Sıra başlangıç zamanıdır (eşitlikte kimlik); başlangıcı bilinmeyen maçlar `asc`'de başta,
        `desc`'te sondadır. league_names: turnuva kimliği → yapılandırmadaki lig adı (`league_folder` için).
        """
        if sort not in _STORE_SORT:
            raise ValueError(f"sort: expected 'asc' or 'desc', got {sort!r}")
        for value, what, minimum in ((offset, "offset", 0), (limit, "limit", 1)):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{what}: expected an integer of at least {minimum}, got {value!r}")
        scope = Scope(tournament_ids=tuple(tournament_ids), season_ids=() if season_id is None else (season_id,))
        start_from, start_to = _date_range(date)
        queries = _list_queries(scope, details, only_finished, _STORE_SORT[sort], start_from, start_to)
        rows: List["EventRow"] = []
        if len(queries) == 1 and not date:
            page = self._store.events.list(replace(queries[0], offset=offset, limit=limit), with_total=True)
            rows, total = list(page.items), int(page.total or 0)
        else:
            total = 0
            for row in _merged(self._store, queries, descending=sort == SORT_DESC):
                if date and date not in _match_date(row):
                    continue
                if offset <= total < offset + limit:
                    rows.append(row)
                total += 1
        names = _Names(self._store, league_names)
        items = []
        for row in rows:
            item = _legacy_row(row, names)
            item["league_folder"] = names.league_folder(row)
            item["has_details"] = bool(row.has_event_payload)
            items.append(item)
        return LegacyMatchPage(tuple(items), total)

    def season_matches_legacy(self, season_id: int, tournament_id: int, *,
                              only_finished: bool = True) -> List[Dict[str, Any]]:
        """
        `GET /api/seasons/{id}/matches` satırları (`LEGACY_LIST_COLUMNS`): turnuvanın o sezondaki maçları,
        başlangıç zamanı sırasıyla (eşitlikte kimlik). only_finished: `matches_legacy` ile aynı kural.
        """
        scope = Scope(tournament_ids=(tournament_id,), season_ids=(season_id,))
        queries = _list_queries(scope, None, only_finished, _STORE_SORT[SORT_ASC], None, None)
        names = _Names(self._store, None)
        return [_legacy_row(row, names) for row in _merged(self._store, queries, descending=False)]

    # -- indirme planı (RD-3) ------------------------------------------------------------------------

    def require_current(self) -> None:
        """Katalog dosyalarla eşit değilse CatalogNotCurrent (`Store.catalog_current`): plan ondan çıkarılmaz."""
        if not self._store.catalog_current:
            raise CatalogNotCurrent(path=str(self._store.data_dir))

    def detail_needs(self, event_ids: Iterable[Any], policy: RefreshPolicy, *,
                     threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None) -> Dict[int, str]:
        """
        Her maçın ihtiyacı (`NEED_*`), kimlik → ihtiyaç; kimlik olamayan değerler sonuçta yer almaz. Kural
        planlayıcınındır (src/services/planning.py `event_needs` ve `compute_need`): bu yüz ona devreder, böylece
        canlı, bayat, açık ve yalnızca listeden bilinen bitmemiş maçların kuralları (plan maddeleri P13, ST-27)
        burada da geçerlidir.

        layout verilirse yalnızca o düzende saklanan olay yükü kayıt sayılır (eski düzen indiricisi `legacy`
        verir: yalnızca o dizinleri tamamlayabilir ve yenileyebilir). Hiçbir yük okunmaz.
        """
        from src.services import planning

        return planning.event_needs(self._store, event_ids, policy, threshold=threshold, layout=layout)

    def refresh_due(self, policy: RefreshPolicy, *, tournament_ids: Sequence[int] = (),
                    threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None) -> List["EventRow"]:
        """
        Yenilenecek kayıtlar (ihtiyacı `refresh` olanlar): önce bayat kayıtlar, sonra kapanmış durumdaki geçici
        kayıtlardan zamanı gelenler, iki grup da kimlik sırasıyla (src/services/planning.py `refresh_due_events`'e
        devreder). tournament_ids: boş = süzgeç yok; maçın turnuvasına bakılır. layout: `detail_needs` ile aynı.
        """
        from src.services import planning

        return planning.refresh_due_events(self._store, policy, tournament_ids=tournament_ids, threshold=threshold,
                                           layout=layout)

    def listed_events(self, *, tournament_ids: Sequence[int] = (), season_ids: Sequence[int] = (),
                      only_finished: bool = True) -> Iterator["EventRow"]:
        """
        Bir programda ya da özette geçen maçlar (liste satırı olanlar ve olay yükü olup bir listenin bağladığı
        maçlar), başlangıç zamanı sırasıyla (eşitlikte kimlik; başlangıcı bilinmeyenler başta).

        only_finished: "yalnızca bitmiş maçlar" ayarı (`_planned`): bitmiş, detayı indirilmiş ya da durumu
        bilinmeyen maçlar.
        """
        scope = Scope(tournament_ids=tuple(tournament_ids), season_ids=tuple(season_ids))
        return (row for row in self._store.events.iter(EventQuery(scope=scope, sort=_STORE_SORT[SORT_ASC]))
                if (row.row_source == _LISTING_SOURCE or row.listed_in is not None)
                and (not only_finished or _planned(row)))

    def detail_candidates(self, tournament_id: Optional[int] = None, *, only_finished: bool = True,
                          max_seasons: int = 0, only_season_ids: Optional[Sequence[int]] = None
                          ) -> Dict[int, List[int]]:
        """
        Detayı indirilecek maçların adayları, turnuva başına (`collect_detail_match_ids`): listelerde geçen
        maçlar (`listed_events`), sezon kimliği büyükten küçüğe, sezon içinde başlangıç zamanı sırasıyla.
        Listede maçı olan her turnuva sonuçta yer alır (süzgeçler bütün maçlarını eleyince boş listeyle);
        turnuvalar kimlik sırasıyla. only_season_ids: yalnızca bu sezonlar; max_seasons > 0: en yeni N sezon.
        """
        by_tournament: Dict[int, Dict[int, List[int]]] = {}
        for row in self.listed_events(tournament_ids=() if tournament_id is None else (tournament_id,),
                                      only_finished=False):
            if row.tournament_id is None or row.season_id is None:
                continue
            seasons = by_tournament.setdefault(row.tournament_id, {})
            if only_finished and not _planned(row):  # süzülen maçın turnuvası yine de sonuçta yer alır
                continue
            seasons.setdefault(row.season_id, []).append(row.id)
        allowed = None if only_season_ids is None else {int(season_id) for season_id in only_season_ids}
        out: Dict[int, List[int]] = {}
        for tid in sorted(by_tournament):
            seasons = sorted(by_tournament[tid], reverse=True)
            if allowed is not None:
                seasons = [season_id for season_id in seasons if season_id in allowed]
            if max_seasons > 0:
                seasons = seasons[:max_seasons]
            out[tid] = list(dict.fromkeys(event_id for season_id in seasons for event_id in by_tournament[tid][season_id]))
        return out

    def missing_details_legacy(self, tournament_id: int, season_id: Optional[int] = None, *,
                               only_finished: bool = True, limit: int = 500) -> Dict[str, Any]:
        """
        `GET /api/leagues/{id}/missing-details` yanıtı: turnuvanın (verildiyse o sezonun) listelerde geçen
        maçları (`listed_events`) ve onlardan olay yükü olmayanlar, kimlik sırasıyla en çok `limit` tanesi.
        Satırlar özet CSV'sinin değerleriyle: takım adları, yerel saatle `match_date`, sezonun adı.
        """
        rows = list(self.listed_events(tournament_ids=(tournament_id,),
                                       season_ids=() if season_id is None else (season_id,),
                                       only_finished=only_finished))
        missing = sorted((row for row in rows if not row.has_event_payload), key=lambda row: row.id)
        names = _Names(self._store, None)
        shown = [{
            "match_id": row.id,
            "home": row.home_name or "",
            "away": row.away_name or "",
            "match_date": _match_date(row),
            "season_name": names.season(row.season_id),
        } for row in missing[:limit]]
        return {
            "total_matches": len({row.id for row in rows}),
            "missing_count": len(missing),
            "missing": shown,
            "truncated": len(missing) > len(shown),
        }

    # -- API v1 ------------------------------------------------------------------------------------------

    # -- maçlar (v1) ---------------------------------------------------------------------------------------

    def events(self, flt: Optional[EventFilter] = None, *, sort: str = "start_desc", limit: int = 50,
               cursor: Optional[str] = None, slices_summary: bool = False) -> EventPage:
        """
        Süzgece uyan maçlar, başlangıç zamanı sırasıyla (eşitlikte kimlik). Konumlu sayfalama: `next_cursor`
        bir sonraki çağrıya `cursor` olarak verilir; konum sıralamaya bağlıdır (başka sıralamayla ValueError).
        slices_summary: maç başına dilim özeti de (sayfa başına iki sorgu daha).
        """
        from src import schema

        if sort not in V1_SORTS:
            raise ValueError(f"sort: expected one of {', '.join(V1_SORTS)}, got {sort!r}")
        flt = flt or EventFilter()
        query = EventQuery(
            scope=Scope(sport=flt.sport, tournament_ids=tuple(flt.tournament_ids), season_ids=tuple(flt.season_ids),
                        participant_ids=tuple(flt.participant_ids), followed=flt.followed),
            status_classes=tuple(flt.status_classes),
            start_from=flt.start_from,
            start_to=flt.start_to,
            text=flt.text,
            has_details=flt.has_details,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
        page = self._store.events.list(query)
        window = refresh_window_seconds()
        items = tuple(schema.event_from_row(row, refresh_window_s=window) for row in page.items)
        summaries = self._slice_summaries([row.id for row in page.items]) if slices_summary else None
        return EventPage(items, page.next_cursor, summaries)

    def _slice_summaries(self, event_ids: Sequence[int]) -> Dict[int, SliceSummary]:
        from src.services import planning
        from src.sports import select_slices

        out: Dict[int, SliceSummary] = {}
        if not event_ids:
            return out
        for state in self._store.events.states(Scope(event_ids=tuple(event_ids))):
            row = state.event
            specs = select_slices("event", row.sport or None, None, phase=planning.phase_of(row.status_class))
            states = [state.slice(spec.key).state for spec in specs]
            out[row.id] = SliceSummary(selected=len(specs), ok=states.count("ok"), empty=states.count("empty"),
                                       error=states.count("error"))
        return out

    def event(self, event_id: int) -> Optional["schema.Event"]:
        """Maçın kaydı; bilinmiyorsa None."""
        from src import schema

        if not _valid_id(event_id):
            return None
        row = self._store.events.get(event_id)
        return None if row is None else schema.event_from_row(row, refresh_window_s=refresh_window_seconds())

    def event_slices(self, event_id: int) -> Optional[List["schema.Slice"]]:
        """
        Maçın dilimleri (yük olmadan): katalogdaki her dilim satırı ve sporunun varsayılan seçiminde olup satırı
        olmayan dilimler (`not_requested`), (anahtar, alt anahtar) sırasıyla. Maç bilinmiyorsa None.
        """
        from src import schema
        from src.sports import select_slices
        from src.store import Ref

        if not _valid_id(event_id):
            return None
        row = self._store.events.get(event_id)
        if row is None:
            return None
        infos = {(info.key, info.sub): info for info in self._store.events.slices(event_id)}
        for spec in select_slices("event", row.sport or None, None):
            if (spec.key, "") not in infos:
                infos[(spec.key, "")] = _not_requested(Ref.event(event_id), spec.key)
        return [schema.slice_from_info(infos[key]) for key in sorted(infos)]

    def event_slice(self, event_id: int, key: str, sub: str = "") -> Optional["schema.Slice"]:
        """
        Maçın bir dilimi, saklanan yüküyle (`payload`; yük yoksa null). Maç bilinmiyorsa ya da dilimin ne satırı
        var ne de sporunun seçiminde geçiyorsa None. Depo düzenine uymayan dilim adı ValueError.
        """
        from src import schema
        from src.sports import select_slices

        if not _valid_id(event_id):
            return None
        row = self._store.events.get(event_id)
        if row is None:
            return None
        info = _slice_info(lambda: self._store.events.slice(event_id, key, sub))
        if info.state == "not_requested" and (sub or key not in {
                spec.key for spec in select_slices("event", row.sport or None, None)}):
            return None
        payload = self._payload(event_id, key, sub) if info.has_payload else None
        return schema.slice_from_info(info, payload=payload)

    def _payload(self, event_id: int, key: str, sub: str) -> Any:
        try:
            return self._store.events.payload(event_id, key, sub)
        except (PayloadMissing, PayloadCorrupt) as e:
            logger.warning("Event %s: the stored %s payload is unreadable: %s", event_id, key, e)
            return None

    def raw(self, event_id: int, key: str = EVENT_KEY, sub: str = "") -> Optional[RawPayload]:
        """
        Maçın bir diliminin saklanan yükü, olduğu gibi (docs/design/04-schema-v1.md bölüm 7). Yük saklanmıyorsa
        (dilim boş, istenmemiş ya da hatalı ve önceki yükü yok) ya da okunamıyorsa None: ham istek "yok" der, boş
        bir yük uydurmaz. Geçersiz anahtar ValueError.
        """
        import hashlib

        if not _valid_id(event_id):
            return None
        info = _slice_info(lambda: self._store.events.slice(event_id, key, sub))
        if not info.has_payload:
            return None
        try:
            data = self._store.events.payload(event_id, key, sub, raw=True)
        except (PayloadMissing, PayloadCorrupt) as e:
            logger.warning("Event %s: the stored %s payload is unreadable: %s", event_id, key, e)
            return None
        if data is None:
            return None
        body = bytes(data)
        from src import schema

        return RawPayload(body, hashlib.sha256(body).hexdigest(), schema.utc_text(info.fetched_at))

    # -- turnuvalar ve sezonlar --------------------------------------------------------------------------

    def tournaments(self, *, sport: Optional[str] = None, text: Optional[str] = None,
                    followed: Optional[bool] = None, limit: int = 50, offset: int = 0
                    ) -> Tuple[List[TournamentEntry], bool]:
        """
        Katalogdaki turnuvalar, ada göre sıralı (eşitlikte kimlik): sayfanın kayıtları ve ardından başka sayfa var
        mı. followed: True yalnızca takip edilenler, False yalnızca ötekiler (takip: her kaynaktan bir satır).
        """
        if isinstance(offset, bool) or offset < 0 or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be positive and offset not negative")
        following = self._followed_tournaments()
        if followed is True:
            rows = [r for r in (self._store.entities.tournament(tid) for tid in sorted(following)) if r is not None]
            if sport is not None:
                rows = [r for r in rows if r.sport == sport]
            if text:
                folded = _fold(text)
                rows = [r for r in rows if folded in _fold(r.name or "")]
            rows.sort(key=lambda r: (_fold(r.name or ""), r.id))
        else:
            extra = len(following) if followed is False else 0
            rows = self._store.entities.tournaments(sport=sport, text=text, limit=offset + limit + 1 + extra)
            if followed is False:
                rows = [r for r in rows if r.id not in following]
        page = rows[offset:offset + limit]
        return [self._entry(r, following) for r in page], len(rows) > offset + limit

    def tournament(self, tournament_id: int) -> Optional[TournamentEntry]:
        """Turnuvanın kaydı ve kategorisi; katalogda yoksa None."""
        if not _valid_id(tournament_id):
            return None
        row = self._store.entities.tournament(tournament_id)
        return None if row is None else self._entry(row, self._followed_tournaments())

    def _followed_tournaments(self) -> Set[int]:
        return {follow.entity_id for follow in self._store.follows.list(kind="tournament")}

    def _entry(self, row: Any, followed: Set[int]) -> TournamentEntry:
        from src import schema

        tournament = schema.tournament_from_row(row)
        assert tournament is not None
        category = None
        if row.category_id is not None:
            found = self._store.entities.category(row.category_id)
            category = schema.category_from_row(found) if found is not None else None
        return TournamentEntry(tournament, category, row.id in followed)

    def seasons(self, tournament_id: int) -> Optional[List["schema.Season"]]:
        """
        Turnuvanın katalogdaki sezonları, en yeni önce. Turnuva da sezonu da bilinmiyorsa None (boş liste: turnuva
        bilinir, sezonu yok).
        """
        from src import schema

        if not _valid_id(tournament_id):
            return None
        rows = self._store.entities.seasons(tournament_id)
        if not rows and self._store.entities.tournament(tournament_id) is None:
            return None
        return [s for s in (schema.season_from_row(r) for r in rows) if s is not None]

    def season(self, season_id: int) -> Optional["schema.Season"]:
        from src import schema

        if not _valid_id(season_id):
            return None
        row = self._store.entities.season(season_id)
        return None if row is None else schema.season_from_row(row)

    def season_slice(self, season_id: int, key: str, sub: str = "") -> Optional["schema.Slice"]:
        """Sezonun bir dilimi (ör. puan durumu), saklanan yüküyle; sezon ya da dilim bilinmiyorsa None."""
        from src import schema
        from src.store import Ref

        if not _valid_id(season_id):
            return None
        row = self._store.entities.season(season_id)
        if row is None:
            return None
        ref = Ref.season(row.tournament_id, season_id)
        info = _slice_info(lambda: self._store.entities.slice(ref, key, sub))
        if info.state == "not_requested":
            return None
        payload = None
        if info.has_payload:
            try:
                payload = self._store.entities.payload(ref, key, sub)
            except (PayloadMissing, PayloadCorrupt) as e:
                logger.warning("Season %s: the stored %s payload is unreadable: %s", season_id, key, e)
        return schema.slice_from_info(info, payload=payload)

    # -- değişiklikler ----------------------------------------------------------------------------------

    def changes(self, *, after: int = 0, before: Optional[int] = None, event_id: Optional[int] = None,
                tournament_ids: Sequence[int] = (), since: Optional[float] = None, until: Optional[float] = None,
                order: str = "asc", limit: int = 50) -> ChangePage:
        """
        Değişiklik günlüğü (`Store.changes`), kendi sıra numarasıyla: her iki sırada da `after`dan büyük numaralar.
        asc: artan; desc: `before`dan küçük numaralar (verilmezse en yeniden), azalan. since / until: kaydın zamanı (epoch
        saniye) aralığı, iki uç dahil. Sayfa dolduysa `next_cursor` son satırın numarasıdır: asc'de `after`,
        desc'te `before` olarak verilir.
        """
        from src import schema

        if order not in CHANGE_ORDERS:
            raise ValueError(f"order: expected one of {', '.join(CHANGE_ORDERS)}, got {order!r}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit: expected a positive integer, got {limit!r}")
        wanted = set(tournament_ids)

        def keep(row: Any) -> bool:
            if wanted and row.tournament_id not in wanted:
                return False
            return until is None or row.ts <= until

        found: List[Any] = []
        if order == "asc":
            position = after
            while len(found) <= limit:
                batch = self._store.changes.list(after_seq=position, event_id=event_id, since=since,
                                                 limit=_CHANGE_SCAN)
                found.extend(row for row in batch if keep(row))
                if len(batch) < _CHANGE_SCAN:
                    break
                position = batch[-1].seq
        else:
            top = self._store.changes.last_seq() + 1 if before is None else before
            while len(found) <= limit and top > after + 1:
                low = max(after, top - 1 - _CHANGE_SCAN)
                batch = [row for row in self._store.changes.list(after_seq=low, event_id=event_id, since=since,
                                                                   limit=_CHANGE_SCAN) if row.seq < top]
                found.extend(row for row in reversed(batch) if keep(row))
                top = low + 1
        page = found[:limit]
        more = len(found) > limit
        return ChangePage(tuple(schema.change_from_row(row) for row in page),
                          str(page[-1].seq) if more and page else None)

    def _payloads(self, event_id: int, keys: Iterable[str]) -> Dict[str, Any]:
        """
        Maçın istenen dilimlerinden yükü olanlar (anahtar → yük). Katalog "yük var" derken dosya okunamıyorsa
        (katalog güncellendikten sonra silinmiş ya da bozulmuş: bir sonraki açılış uzlaştırır) dilimler tek
        tek okunur ve okunamayan dilim yok sayılır: bir dilimin dosyası bütün maçı okunmaz yapmaz.
        """
        wanted = tuple(keys)
        try:
            return self._store.events.payloads(event_id, wanted)
        except (PayloadMissing, PayloadCorrupt):
            pass
        found: Dict[str, Any] = {}
        for key in wanted:
            try:
                found.update(self._store.events.payloads(event_id, (key,)))
            except (PayloadMissing, PayloadCorrupt) as e:
                logger.warning("Event %s: the stored %s payload is unreadable and is left out: %s", event_id, key, e)
        return found


def _fold(text: str) -> str:
    """Ad karşılaştırması: büyük-küçük harf ve aksan ayrımı yok (kataloğun `name_folded` kuralına yakın)."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _slice_info(read: Callable[[], Any]) -> Any:
    """Bir dilimin durumu; adı depo düzeninin kurallarına uymuyorsa (LayoutError) ValueError."""
    from src.store import LayoutError

    try:
        return read()
    except LayoutError as e:
        raise ValueError(f"not a valid slice name: {e}") from None


def _not_requested(ref: Any, key: str) -> Any:
    from src.store import SliceInfo

    return SliceInfo(ref=ref, key=key, sub="", state="not_requested", has_payload=False, fetched_at=None,
                     checked_at=None, empty_count=0, unverified_empty_count=0, error=None, stored_bytes=None,
                     raw_bytes=None, history_count=0)


# -- maç listelerinin yardımcıları ---------------------------------------------------------------------

def _list_queries(scope: Scope, details: Optional[bool], only_finished: bool, sort: str,
                  start_from: Optional[float], start_to: Optional[float]) -> List[EventQuery]:
    """
    Listenin ayrık sorguları. Ayar açıkken liste "bitmiş YA DA detayı olan" maçlardır; katalog sorgusu koşulları
    VE ile bağladığından bu, iki ayrık sorgunun birleşimidir: bitmiş maçlar ve detayı olan bitmemiş maçlar.
    """
    base = EventQuery(scope=scope, sort=sort, start_from=start_from, start_to=start_to)
    if not only_finished or details is True:
        return [replace(base, has_details=details)]
    if details is False:
        return [replace(base, has_details=False, status_classes=FINISHED_CLASSES)]
    return [replace(base, status_classes=FINISHED_CLASSES),
            replace(base, status_classes=_NOT_FINISHED, has_details=True)]


def _planned(row: "EventRow") -> bool:
    """
    "Yalnızca bitmiş maçlar" ayarı açıkken indirme planına giren liste satırı: bitmiş, detayı indirilmiş (maç
    listeleriyle aynı kural) ya da durumu bilinmeyen maç. Sonuncusu, durum sütunu olmayan bir özet CSV'sinin
    satırıdır: özetin satırları eskiden süzülmeden okunurdu (özet yazılırken süzülmüştür).
    """
    return (row.status_class in FINISHED_CLASSES or row.has_event_payload
            or row.status_class == StatusClass.UNKNOWN.value)


def _sort_key(row: "EventRow") -> Tuple[bool, int, int]:
    """Kataloğun sırası: başlangıcı bilinmeyen satırlar artan sırada başta (SQLite NULL'ı en küçük sayar)."""
    return (row.start_ts is not None, row.start_ts or 0, row.id)


def _merged(store: "Store", queries: Sequence[EventQuery], *, descending: bool) -> Iterator["EventRow"]:
    """Ayrık sorguların satırları, kataloğun sırasıyla tek akışta."""
    if len(queries) == 1:
        return store.events.iter(queries[0])
    return heapq.merge(*(store.events.iter(q) for q in queries), key=_sort_key, reverse=descending)


def _date_range(date: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """
    Tarih süzgecinin başlangıç zamanı aralığı (epoch saniye), metin bir ISO önekiyse; değilse (None, None) ve
    süzgeç yalnızca metin olarak uygulanır. `match_date` yerel saattir: aralık yaz saati geçişlerine karşı iki
    yandan bir gün geniş tutulur; kesin karar her durumda metin karşılaştırmasınındır.
    """
    m = _DATE_PREFIX_RE.fullmatch(date) if date else None
    if m is None:
        return None, None
    year, month, day, hour, minute, second = (int(p) if p is not None else None for p in m.groups())
    try:
        start = datetime(year or 1, month or 1, day or 1, hour or 0, minute or 0, second or 0)
        if month is None:
            end = start.replace(year=start.year + 1)
        elif day is None:
            end = start.replace(year=start.year + start.month // 12, month=start.month % 12 + 1)
        elif hour is None:
            end = start + timedelta(days=1)
        elif minute is None:
            end = start + timedelta(hours=1)
        elif second is None:
            end = start + timedelta(minutes=1)
        else:
            end = start + timedelta(seconds=1)
        return start.timestamp() - _DATE_SLACK, end.timestamp() + _DATE_SLACK
    except (ValueError, OverflowError, OSError):
        # Takvimde olmayan tarih (2026-13, 0000) ya da sınır dışı yıl: aralık yok, metin karşılaştırması karar verir
        return None, None


def _match_date(row: "EventRow") -> str:
    """Özetteki `match_date`: başlangıç zamanı yerel saatle, saniye çözünürlüğünde ISO metni; yoksa boş."""
    if row.start_ts is None:
        return ""
    try:
        return datetime.fromtimestamp(row.start_ts).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _round(row: "EventRow") -> Any:
    """
    Özetteki `round`: maçı listeleyen sayfa bir tur dosyasıysa istenen tur numarası (`round_28_semifinals` → 28),
    bir olay sayfasıysa sayfanın adı (`last_0`). Hiçbir sayfada geçmeyen maçta olay yükündeki tur; o da yoksa boş.
    """
    if row.listed_in:
        m = _ROUND_SUB_RE.fullmatch(row.listed_in)
        return int(m.group(1)) if m else row.listed_in
    return row.round if row.round is not None else ""


class _Names:
    """Sezon adları ve lig dizini adları; bir çağrı boyunca önbellekte."""

    def __init__(self, store: "Store", league_names: Optional[Mapping[int, str]]) -> None:
        self._store = store
        self._league_names = dict(league_names or {})
        self._seasons: Dict[int, str] = {}
        self._tournaments: Dict[int, Optional[str]] = {}

    def season(self, season_id: Optional[int]) -> str:
        if season_id is None:
            return ""
        if season_id not in self._seasons:
            found = self._store.entities.season(season_id)
            self._seasons[season_id] = (found.name if found is not None else None) or ""
        return self._seasons[season_id]

    def league_folder(self, row: "EventRow") -> str:
        """
        Maçın lig dizininin adı (`17_Premier_League`); eskiden özet CSV'sinin bulunduğu `matches/` alt dizininin
        adıydı. Turnuvası bilinen maçta: detay dizini o turnuvanın kimliğiyle başlayan bir lig dizinindeyse onun
        adı, değilse yazıcıların kullandığı ad (`src/paths.league_dir_name`: yapılandırmadaki lig adı, yoksa
        katalogdaki turnuva adı). Turnuvası olmayan maçta detay dizininin lig dizini (`_no_tournament`); o da
        yoksa boş.
        """
        parts = (row.path or "").split("/") if row.layout == _LEGACY_LAYOUT else []
        directory = parts[1] if len(parts) > 2 and parts[0] == _MATCH_DETAILS_DIR else ""
        tid = row.tournament_id
        if tid is None:
            return directory
        if directory.split("_", 1)[0] == str(tid):
            return directory
        name = self._league_names.get(tid)
        if name is None:
            if tid not in self._tournaments:
                found = self._store.entities.tournament(tid)
                self._tournaments[tid] = found.name if found is not None else None
            name = self._tournaments[tid]
        return league_dir_name(tid, name)


def _legacy_row(row: "EventRow", names: _Names) -> Dict[str, Any]:
    """Maçın satırı, özet CSV'sinin sütunlarıyla (skorlar `current`, yoksa 0; turnuva aşamanın adı)."""
    return {
        "round": _round(row),
        "match_id": row.id,
        "home_team": row.home_name or "",
        "away_team": row.away_name or "",
        "home_score": row.home_score_current if row.home_score_current is not None else 0,
        "away_score": row.away_score_current if row.away_score_current is not None else 0,
        "match_date": _match_date(row),
        "status": row.status_description or "",
        "tournament": row.stage_name or "",
        "season": names.season(row.season_id),
    }


__all__ = ["CHANGE_ORDERS", "CatalogNotCurrent", "ChangePage", "DEFAULT_EMPTY_THRESHOLD", "EVENT_KEY", "EventFilter",
           "EventPage", "LEGACY_EVENT_KEY", "LEGACY_LIST_COLUMNS", "LegacyMatchPage", "NEED_FULL", "NEED_NONE",
           "NEED_REFILL", "NEED_REFRESH", "QueryService", "RawPayload", "RefreshPolicy", "SliceSummary",
           "TournamentEntry", "V1_SORTS", "legacy_detail_keys", "refresh_window_seconds", "required_detail_keys"]
