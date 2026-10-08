"""
Sorgu servisi: yüzlerin (web, CLI, kitaplık) okuma tarafı (docs/design/02-services.md 2.3 ve 2.7).

Okuyucular dosya ağacını gezmez; her soru deponun okuma API'sine sorulur (`Store.events`, `Store.entities`,
`Store.changes`: docs/design/01-storage.md 2.3). Katalog, depo açılırken dosyalarla eşitlenir ve Store'un her
yazmasıyla aynı kritik bölümde güncellenir (01-storage.md 6.2); iki düzen de (eski `match_details/` ağacı ve v3)
aynı çağrılarla okunur.

İki iş var: API v1'in okumaları (maçlar, dilimler, turnuvalar, sezonlar, değişiklikler; şema v1 kayıtları) ve
indirme planı (RD-3: hangi maçın detayı eksik, hangisi yenilenmeli, bir ligin hangi maçları listelerde geçiyor).
2.x'in `/api` yanıt biçimlerini (maç listeleri, eksik detaylar) üreten işlevler eski uç noktalarla birlikte
3.1'de kalktı (P30).

İndirme planı (RD-3) eskiden dosyalardan çıkarılıyordu: maçın ihtiyacı (`full` / `refill` / `refresh` / `none`)
bütün dilim dosyaları ve işaret dosyaları (`_unavailable.json`) okunarak, yenilenecek maçlar
`match_details/<lig>/<sezon>/<id>` ağacı gezilerek, bir ligin maçları sezon özeti CSV'lerinden. Şimdi hepsi
kataloğa sorulur (`Store.events.missing`, `refresh_candidates` ve maç listesi); hiçbir yük okunmaz. Bugünkü
kodun yazdığı dizinlerde kümeler ve kararlar aynıdır; sıra yalnızca bir ligin maç adaylarında değişir. Farklar:

  * Yenilenecek maçlar ağacın her yerinden gelir: düz (`match_details/<id>`) ve `_no_tournament/` altındaki
    kayıtlar da; lig süzgeci dizin adının `<lig id>_` önekine değil, maçın turnuvasına bakar (kimliksiz lig
    dizinindeki kayıt da bulunur). Sıra eski gezintininkidir (kayıt dizinlerinin yolu).
  * Bir ligin maçları (`detail_candidates`) programlarda ve özetlerde geçen maçlardır. "Yalnızca bitmiş maçlar"
    ayarı okurken uygulanır: bitmiş, detayı indirilmiş ya da durumu bilinmeyen maçlar (durum sütunu olmayan
    özet satırı). Eskiden özetler yazılırken süzülüyordu; ayar kapalıyken yazılmış bir sezonun bitmemiş maçları
    ayar açıkken artık plana girmez. Sezonlar yine büyükten küçüğe, bir sezonun içinde sıra başlangıç zamanıdır
    (eskiden özet dosyasının satır sırası); lig verilmezse ligler kimlik sırasıyla (eskiden dizin listeleme
    sırası). İlk sürümün sezon dizinindeki tur özetleri (`<sezon>/round_<n>_matches.csv`) de okunur (katalog
    onları da dizinler).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import (TYPE_CHECKING, Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set,
                    Tuple)

from sofascore_scraper import refresh
from sofascore_scraper.logger import get_logger
from sofascore_scraper.sports import slices_for, sport_slugs
from sofascore_scraper.status import StatusClass
from sofascore_scraper.store import EventQuery, PayloadCorrupt, PayloadMissing, Scope, StoreError

if TYPE_CHECKING:
    from sofascore_scraper import schema
    from sofascore_scraper.store import EventRow, Store

logger = get_logger("QueryService")

EVENT_KEY = "event"  # `/event/{id}` yükünün depodaki dilim anahtarı
# SQLite'ın saklayabildiği kimlik aralığı: dışındaki bir sayı hiçbir maçın kimliği olamaz
_ID_RANGE = (-(2 ** 63), 2 ** 63 - 1)

FINISHED_CLASSES: Tuple[str, ...] = (StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value)
_LISTING_SOURCE = "listing"  # `events.row_source`: maç yalnızca bir listeden biliniyor


# -- indirme planı (RD-3) -------------------------------------------------------------------------------

# Bir maçın ihtiyacı (2.x'te MatchDataFetcher `_needs_detail_fetch`'in dönüş değerleri)
NEED_FULL = "full"  # kayıt yok ya da olay yükü yok: baştan indirilir
NEED_REFILL = "refill"  # olay yükü var, beklenen dilimlerden en az biri eksik
NEED_REFRESH = "refresh"  # dilimler tam ama kayıt geçici ve yenileme zamanı geldi (sofascore_scraper/refresh.py)
NEED_NONE = "none"  # tamam
# Bu kadar kesin "veri yok" yanıtından sonra dilim o maçta artık beklenmez (services/detail_phase.py
# UNAVAILABLE_AFTER_ATTEMPTS ile aynı değer; çağıran kendi değerini verebilir)
DEFAULT_EMPTY_THRESHOLD = 2
_ID_CHUNK = 500  # bir sorgunun kapsamına yazılan en çok kimlik


def required_detail_keys() -> Dict[str, Tuple[str, ...]]:
    """
    `Store.events.missing`'in `required` argümanı (`exclusive=True` ile): `""` altında sporu kayıt defterinde
    olmayan ya da bilinmeyen maçın beklediği dilimler, kayıtlı her sporun altında o sporun bütün beklediği
    dilimler. Kural `slices_for(spor, required_only=True)`'dur (dilim tablosu, sofascore_scraper/sports.py). Bir spor ortak
    bir dilimi beklemeyebildiği (`not_in`, `optional_in`) için sporlar `""`'nin üstüne eklenmez, onun yerine geçer.
    """
    required: Dict[str, Tuple[str, ...]] = {"": tuple(detail.key for detail in slices_for(None, required_only=True))}
    for sport in sport_slugs():
        required[sport] = tuple(detail.key for detail in slices_for(sport, required_only=True))
    return required


@dataclass(frozen=True)
class RefreshPolicy:
    """
    Yenileme politikası (sofascore_scraper/refresh.py) saniye cinsinden, bir an için. window_s <= 0: politika kapalı.
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
class EventExtra:
    """
    Şema v1 kaydında olmayan, maç sayfasının başlığında gösterilen bilgiler; saklanan olay yükünden (FX-26).

    note     SofaScore'un sonuç notu, verdiği gibi (İngilizce): "India beat West Indies by 8 wickets" (kriket)
    series   maçın ait olduğu "best of" serisinde iki tarafın o ana kadar kazandığı maçlar (beyzbol play-off'u,
             `homeScore.series` / `awayScore.series`); bu maçın ev sahibi önce. Seri yoksa None.
    venue    sahanın adı (`venue.name`, yoksa `venue.stadium.name`)
    referee  hakemin adı (`referee.name`)
    """

    note: Optional[str] = None
    series: Optional[Tuple[int, int]] = None
    venue: Optional[str] = None
    referee: Optional[str] = None


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
class Suggestion:
    """
    Katalogda adıyla bulunan bir turnuva ya da yarışmacı (plan maddesi FX-20, yazarken öneri). kind: takip türü
    ("tournament" ya da "team"; tek oyunculu sporların oyuncuları da yarışmacıdır, SofaScore'da takım kimliği
    taşırlar). country_code: turnuvada kategorinin, yarışmacıda kendi ülke kodu; category_*: turnuvanın
    kategorisi. followed: aynı türden bir takip bu kimliği adlandırıyor.
    """

    kind: str
    id: int
    name: str
    slug: Optional[str]
    sport: Optional[str]
    country_code: Optional[str]
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    category_slug: Optional[str] = None
    followed: bool = False


# Önerilerde sıralama için katalogdan okunan en çok aday (tür başına): ad başında eşleşenler sonda kalmasın
_SUGGEST_POOL = 200


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
    `seasons`, `season`, `season_slice`, `changes`) şema v1 kayıtları döndürür (sofascore_scraper/schema). Bilinmeyen kimlik
    için None dönerler (yüz onu `not_found`a çevirir); depolama hatası StoreError olarak çıkar.
    """

    def __init__(self, store: "Store") -> None:
        self._store = store

    # -- indirme planı (RD-3) ------------------------------------------------------------------------

    def require_current(self) -> None:
        """Katalog dosyalarla eşit değilse CatalogNotCurrent (`Store.catalog_current`): plan ondan çıkarılmaz."""
        if not self._store.catalog_current:
            raise CatalogNotCurrent(path=str(self._store.data_dir))

    def listed_events(self, *, tournament_ids: Sequence[int] = (), season_ids: Sequence[int] = (),
                      only_finished: bool = True) -> Iterator["EventRow"]:
        """
        Bir programda ya da özette geçen maçlar (liste satırı olanlar ve olay yükü olup bir listenin bağladığı
        maçlar), başlangıç zamanı sırasıyla (eşitlikte kimlik; başlangıcı bilinmeyenler başta).

        only_finished: "yalnızca bitmiş maçlar" ayarı (`_planned`): bitmiş, detayı indirilmiş ya da durumu
        bilinmeyen maçlar.
        """
        scope = Scope(tournament_ids=tuple(tournament_ids), season_ids=tuple(season_ids))
        return (row for row in self._store.events.iter(EventQuery(scope=scope, sort="start_asc"))
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

    # -- API v1 ------------------------------------------------------------------------------------------

    # -- maçlar (v1) ---------------------------------------------------------------------------------------

    def events(self, flt: Optional[EventFilter] = None, *, sort: str = "start_desc", limit: int = 50,
               cursor: Optional[str] = None, slices_summary: bool = False) -> EventPage:
        """
        Süzgece uyan maçlar, başlangıç zamanı sırasıyla (eşitlikte kimlik). Konumlu sayfalama: `next_cursor`
        bir sonraki çağrıya `cursor` olarak verilir; konum sıralamaya bağlıdır (başka sıralamayla ValueError).
        slices_summary: maç başına dilim özeti de (sayfa başına iki sorgu daha).
        """
        from sofascore_scraper import schema

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
        from sofascore_scraper.services import planning
        from sofascore_scraper.sports import select_slices

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
        from sofascore_scraper import schema

        if not _valid_id(event_id):
            return None
        row = self._store.events.get(event_id)
        return None if row is None else schema.event_from_row(row, refresh_window_s=refresh_window_seconds())

    def event_extra(self, event_id: int) -> Optional[EventExtra]:
        """
        Maçın şema v1 dışındaki başlık bilgileri (`EventExtra`), saklanan olay yükünden. Yük yoksa, okunamıyorsa
        ya da hiçbiri yoksa None.
        """
        if not _valid_id(event_id):
            return None
        try:
            payload = self._store.events.payload(event_id, EVENT_KEY)
        except (PayloadMissing, PayloadCorrupt, StoreError) as e:
            logger.warning("Event %s: the stored event payload is unreadable: %s", event_id, e)
            return None
        return event_extra_of(payload)

    def event_slices(self, event_id: int) -> Optional[List["schema.Slice"]]:
        """
        Maçın dilimleri (yük olmadan): katalogdaki her dilim satırı ve sporunun varsayılan seçiminde olup satırı
        olmayan dilimler (`not_requested`), (anahtar, alt anahtar) sırasıyla. Maç bilinmiyorsa None.
        """
        from sofascore_scraper import schema
        from sofascore_scraper.sports import select_slices
        from sofascore_scraper.store import Ref

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
        from sofascore_scraper import schema
        from sofascore_scraper.sports import select_slices

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
        from sofascore_scraper import schema

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

    def suggest(self, text: str, *, sport: Optional[str] = None, limit: int = 8) -> List[Suggestion]:
        """
        Yazarken öneri (plan maddesi FX-20): adında `text` geçen turnuvalar ve yarışmacılar (takımlar), yalnızca
        katalogdan; SofaScore'a istek atılmaz. Sıra: adı metinle başlayanlar, sonra bir sözcüğü metinle başlayanlar
        (boşluk, tire, eğik çizgi gibi bir işaretten sonra), sonra adında geçenler; her birinin içinde takip
        edilenler önce, sonra ad (eşitlikte tür ve kimlik). Adı ya da bir sözcüğü metinle başlayan bir öneri
        varsa metnin yalnızca bir sözcüğün ortasında geçtiği adlar hiç gösterilmez (FX-28, M23: "la" için Alanyaspor,
        Atalanta…). Büyük-küçük harf ve aksan ayrımı yoktur. En çok `limit` öneri.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        wanted = _suggest_key(text or "")
        if not wanted:
            return []
        follows = self._store.follows.list()
        followed = {(f.kind, f.entity_id) for f in follows}
        categories: Dict[int, Any] = {}

        def pool(word_start: bool) -> List[Suggestion]:
            # Katalogdan aday adlar, türüne göre en çok _SUGGEST_POOL; word_start ile yalnızca sözcük başında geçenler
            # (ada göre sıralı havuz, sözcük ortasında geçen çok adla dolup sözcük başı olanları dışarıda bırakmasın)
            found: List[Suggestion] = []
            for row in self._store.entities.tournaments(sport=sport, text=wanted, limit=_SUGGEST_POOL,
                                                        word_start=word_start):
                if not row.name:
                    continue
                category = None
                if row.category_id is not None:
                    if row.category_id not in categories:
                        categories[row.category_id] = self._store.entities.category(row.category_id)
                    category = categories[row.category_id]
                found.append(Suggestion(
                    kind="tournament", id=row.id, name=row.name, slug=row.slug, sport=row.sport,
                    country_code=category.alpha2 if category is not None else None,
                    category_id=row.category_id, category_name=category.name if category is not None else None,
                    category_slug=category.slug if category is not None else None,
                    followed=("tournament", row.id) in followed,
                ))
            for row in self._store.entities.participants(text=wanted, sport=sport, limit=_SUGGEST_POOL,
                                                         word_start=word_start):
                if not row.name:
                    continue
                found.append(Suggestion(kind="team", id=row.id, name=row.name, slug=row.slug, sport=row.sport,
                                        country_code=row.country, followed=("team", row.id) in followed))
            return found

        def rank(item: Suggestion) -> Tuple[int, int, str, str, int]:
            name = _suggest_key(item.name)
            return _match_place(name, wanted), 0 if item.followed else 1, name, item.kind, item.id

        ranked = sorted(pool(True), key=rank)
        if not ranked or rank(ranked[0])[0] == 2:
            ranked = sorted(pool(False), key=rank)
        if ranked and rank(ranked[0])[0] < 2:
            ranked = [item for item in ranked if rank(item)[0] < 2]
        return ranked[:limit]

    def tournament(self, tournament_id: int) -> Optional[TournamentEntry]:
        """Turnuvanın kaydı ve kategorisi; katalogda yoksa None."""
        if not _valid_id(tournament_id):
            return None
        row = self._store.entities.tournament(tournament_id)
        return None if row is None else self._entry(row, self._followed_tournaments())

    def _followed_tournaments(self) -> Set[int]:
        return {follow.entity_id for follow in self._store.follows.list(kind="tournament")}

    def _entry(self, row: Any, followed: Set[int]) -> TournamentEntry:
        from sofascore_scraper import schema

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
        from sofascore_scraper import schema

        if not _valid_id(tournament_id):
            return None
        rows = self._store.entities.seasons(tournament_id)
        if not rows and self._store.entities.tournament(tournament_id) is None:
            return None
        return [s for s in (schema.season_from_row(r) for r in rows) if s is not None]

    def season(self, season_id: int) -> Optional["schema.Season"]:
        from sofascore_scraper import schema

        if not _valid_id(season_id):
            return None
        row = self._store.entities.season(season_id)
        return None if row is None else schema.season_from_row(row)

    def season_slice(self, season_id: int, key: str, sub: str = "") -> Optional["schema.Slice"]:
        """Sezonun bir dilimi (ör. puan durumu), saklanan yüküyle; sezon ya da dilim bilinmiyorsa None."""
        from sofascore_scraper import schema
        from sofascore_scraper.store import Ref

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
                order: str = "asc", limit: int = 50, sport: Optional[str] = None,
                regressed: Optional[bool] = None) -> ChangePage:
        """
        Değişiklik günlüğü (`Store.changes`), kendi sıra numarasıyla: her iki sırada da `after`dan büyük numaralar.
        asc: artan; desc: `before`dan küçük numaralar (verilmezse en yeniden), azalan. since / until: kaydın zamanı (epoch
        saniye) aralığı, iki uç dahil. Sayfa dolduysa `next_cursor` son satırın numarasıdır: asc'de `after`,
        desc'te `before` olarak verilir. sport: yalnızca bu sporun (slug) satırları; regressed: True yalnızca
        bitmişten geçersize dönenler, False yalnızca ötekiler (plan maddesi FX-13).
        """
        from sofascore_scraper import schema

        if order not in CHANGE_ORDERS:
            raise ValueError(f"order: expected one of {', '.join(CHANGE_ORDERS)}, got {order!r}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit: expected a positive integer, got {limit!r}")
        wanted = set(tournament_ids)

        def keep(row: Any) -> bool:
            if wanted and row.tournament_id not in wanted:
                return False
            if sport is not None and row.sport != sport:
                return False
            if regressed is not None and bool(row.status_regressed) != regressed:
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


def _fold(text: str) -> str:
    """Ad karşılaştırması: büyük-küçük harf ve aksan ayrımı yok (kataloğun `name_folded` kuralına yakın)."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# Kataloğun `name_folded` kuralındaki gibi NFKD ile ayrışmayan harfler (sofascore_scraper/store/derive.py `fold_name`)
_SUGGEST_FOLD = str.maketrans({"ı": "i", "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe",
                               "ħ": "h"})


def _suggest_key(text: str) -> str:
    """Önerilerin sıralamasında ad: katlanmış, boşluklar teke inmiş (katalogdaki aramayla aynı karşılaştırma)."""
    return " ".join(_fold(text).translate(_SUGGEST_FOLD).split())


def _match_place(name: str, wanted: str) -> int:
    """
    Önerinin yeri (ikisi de `_suggest_key`ten geçmiş): 0 ad metinle başlıyor, 1 bir sözcüğü metinle başlıyor (harf
    ya da rakam olmayan bir işaretten sonra), 2 metin yalnızca bir sözcüğün ortasında geçiyor (ya da hiç geçmiyor).
    """
    if name.startswith(wanted):
        return 0
    at = name.find(wanted, 1)
    while at > 0:
        if not name[at - 1].isalnum():
            return 1
        at = name.find(wanted, at + 1)
    return 2


def _slice_info(read: Callable[[], Any]) -> Any:
    """Bir dilimin durumu; adı depo düzeninin kurallarına uymuyorsa (LayoutError) ValueError."""
    from sofascore_scraper.store import LayoutError

    try:
        return read()
    except LayoutError as e:
        raise ValueError(f"not a valid slice name: {e}") from None


def _not_requested(ref: Any, key: str) -> Any:
    from sofascore_scraper.store import SliceInfo

    return SliceInfo(ref=ref, key=key, sub="", state="not_requested", has_payload=False, fetched_at=None,
                     checked_at=None, empty_count=0, unverified_empty_count=0, error=None, stored_bytes=None,
                     raw_bytes=None, history_count=0)


def _planned(row: "EventRow") -> bool:
    """
    "Yalnızca bitmiş maçlar" ayarı açıkken indirme planına giren liste satırı: bitmiş, detayı indirilmiş (maç
    listeleriyle aynı kural) ya da durumu bilinmeyen maç. Sonuncusu, durum sütunu olmayan bir özet CSV'sinin
    satırıdır: özetin satırları eskiden süzülmeden okunurdu (özet yazılırken süzülmüştür).
    """
    return (row.status_class in FINISHED_CLASSES or row.has_event_payload
            or row.status_class == StatusClass.UNKNOWN.value)


def event_extra_of(payload: Any) -> Optional[EventExtra]:
    """
    Olay yükünden (`/event/{id}` yanıtının `event` nesnesi ya da yanıtın kendisi) `EventExtra`; hiçbir bilgi
    yoksa None. Not boş olmayan bir metindir; seri iki tarafta da tam sayı olmalıdır.
    """
    event = payload.get("event", payload) if isinstance(payload, Mapping) else None
    if not isinstance(event, Mapping):
        return None
    note = _name(event.get("note"))
    home, away = event.get("homeScore"), event.get("awayScore")
    pair = (home.get("series") if isinstance(home, Mapping) else None,
            away.get("series") if isinstance(away, Mapping) else None)
    series = (int(pair[0]), int(pair[1])) if all(
        isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in pair) else None
    venue_of = event.get("venue") if isinstance(event.get("venue"), Mapping) else {}
    stadium = venue_of.get("stadium") if isinstance(venue_of.get("stadium"), Mapping) else {}
    venue = _name(venue_of.get("name")) or _name(stadium.get("name"))
    referee_of = event.get("referee") if isinstance(event.get("referee"), Mapping) else {}
    referee = _name(referee_of.get("name"))
    if note is None and series is None and venue is None and referee is None:
        return None
    return EventExtra(note=note, series=series, venue=venue, referee=referee)


def _name(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["CHANGE_ORDERS", "CatalogNotCurrent", "ChangePage", "DEFAULT_EMPTY_THRESHOLD", "EVENT_KEY", "EventFilter",
           "EventExtra", "EventPage", "NEED_FULL", "NEED_NONE",
           "NEED_REFILL", "NEED_REFRESH", "QueryService", "RawPayload", "RefreshPolicy", "SliceSummary",
           "Suggestion", "TournamentEntry", "V1_SORTS", "event_extra_of", "refresh_window_seconds",
           "required_detail_keys"]
