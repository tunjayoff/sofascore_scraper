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
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from src import refresh
from src.logger import get_logger
from src.paths import league_dir_name
from src.sports import DETAIL_SLICES, slices_for, sport_slugs
from src.status import StatusClass
from src.store import EventQuery, PayloadCorrupt, PayloadMissing, Scope, StoreError

if TYPE_CHECKING:
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


class QueryService:
    """Deponun okuma API'si üzerinde, yüzlerin kullandığı sorgular. Durum tutmaz; kilit almaz."""

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
        Her maçın ihtiyacı (`NEED_*`), kimlik → ihtiyaç; kimlik olamayan değerler sonuçta yer almaz.

          full     kayıt yok: maç bilinmiyor ya da olay yükü yok (yalnızca bir listeden biliniyor)
          refill   olay yükü var, beklenen bir dilim eksik: satırı yok ya da `ok` değil ve kesin "veri yok"
                   sayısı `threshold`un altında (`Store.events.missing`, `required_detail_keys()`)
          refresh  dilimler tam, kayıt geçici ve yenileme zamanı gelmiş (`Store.events.refresh_candidates`)
          none     tamam

        layout verilirse yalnızca o düzende saklanan olay yükü kayıt sayılır (eski düzen indiricisi `legacy`
        verir: yalnızca o dizinleri tamamlayabilir ve yenileyebilir). Kimlikler 500'lük parçalarla sorulur;
        parça başına üç ya da dört sorgu çalışır ve hiçbir yük okunmaz.
        """
        required = required_detail_keys()
        needs: Dict[int, str] = {}
        for chunk in _chunks(_event_ids(event_ids)):
            scope = Scope(event_ids=tuple(chunk))
            records = {row.id for row in self._store.events.iter(EventQuery(scope=scope, has_details=True))
                       if layout is None or (row.layout == layout and row.path)}
            refill = {row.event_id for row in self._store.events.missing(
                scope, required, status_classes=(), threshold=threshold, exclusive=True) if row.has_event_payload}
            due = set(self._store.events.refresh_candidates(
                now=policy.now, window_s=policy.window_s, min_interval_s=policy.min_interval_s, scope=scope,
                include_unobserved=policy.include_unobserved)) if records else set()
            for event_id in chunk:
                if event_id not in records:
                    needs[event_id] = NEED_FULL
                elif event_id in refill:
                    needs[event_id] = NEED_REFILL
                elif event_id in due:
                    needs[event_id] = NEED_REFRESH
                else:
                    needs[event_id] = NEED_NONE
        return needs

    def refresh_due(self, policy: RefreshPolicy, *, tournament_ids: Sequence[int] = (),
                    threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None) -> List["EventRow"]:
        """
        Yenilenecek kayıtlar (ihtiyacı `refresh` olanlar), kimlik sırasıyla: yenileme adayları, eksik dilimi
        olanlar hariç (onlar `refill`dir). tournament_ids: boş = süzgeç yok; maçın turnuvasına bakılır.
        layout: `detail_needs` ile aynı.
        """
        scope = Scope(tournament_ids=tuple(tournament_ids))
        candidates = self._store.events.refresh_candidates(
            now=policy.now, window_s=policy.window_s, min_interval_s=policy.min_interval_s, scope=scope,
            include_unobserved=policy.include_unobserved)
        required = required_detail_keys()
        rows: List["EventRow"] = []
        for chunk in _chunks(candidates):
            chunk_scope = Scope(event_ids=tuple(chunk))
            refill = {row.event_id for row in self._store.events.missing(
                chunk_scope, required, status_classes=(), threshold=threshold, exclusive=True)}
            rows.extend(row for row in self._store.events.iter(EventQuery(scope=chunk_scope, has_details=True))
                        if row.id not in refill and (layout is None or (row.layout == layout and row.path)))
        return sorted(rows, key=lambda row: row.id)

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


__all__ = ["CatalogNotCurrent", "DEFAULT_EMPTY_THRESHOLD", "EVENT_KEY", "LEGACY_EVENT_KEY", "LEGACY_LIST_COLUMNS",
           "LegacyMatchPage", "NEED_FULL", "NEED_NONE", "NEED_REFILL", "NEED_REFRESH", "QueryService", "RefreshPolicy",
           "legacy_detail_keys", "required_detail_keys"]
