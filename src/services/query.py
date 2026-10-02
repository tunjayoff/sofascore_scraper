"""
Sorgu servisi: yüzlerin (web, CLI, kitaplık) okuma tarafı (docs/design/02-services.md 2.3 ve 2.7).

Okuyucular dosya ağacını gezmez; her soru deponun okuma API'sine sorulur (`Store.events`, `Store.entities`,
`Store.changes`: docs/design/01-storage.md 2.3). Katalog, depo açılırken dosyalarla eşitlenir ve eski düzen
yazıcılarının her yazmasından sonra güncellenir (gölge kip, 01-storage.md 3.5); iki düzen de (eski
`match_details/` ağacı ve v3) aynı çağrılarla okunur.

Şimdilik iki iş var: maç detayı (plan maddesi RD-1: `GET /api/matches/{id}` yanıtının bugünkü sözlüğü) ve maç
listeleri (RD-2: `GET /api/matches` ile `GET /api/seasons/{id}/matches` satırları). Eksik detay / ihtiyaç
sorguları (RD-3) kendi plan maddesiyle eklenir; eski `/api` yanıt biçimlerini üreten işlevler burada durur ve
eski uç noktalarla birlikte kaldırılır (P30).

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
"""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from src.logger import get_logger
from src.paths import league_dir_name
from src.sports import DETAIL_SLICES
from src.status import StatusClass
from src.store import EventQuery, PayloadCorrupt, PayloadMissing, Scope

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
_MATCH_DETAILS_DIR = "match_details"
_ROUND_SUB_RE = re.compile(r"round_(\d+)(?:_.*)?")
# Tarih süzgecinin ISO önekleri: "2026", "2026-09", "2026-09-15", "2026-09-15T13", "…T13:30", "…T13:30:00".
# `match_date` metninde dört basamaklı tek sayı yıldır; böyle bir metin yalnızca başta geçebilir, yani bir
# başlangıç zamanı aralığıdır.
_DATE_PREFIX_RE = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2})(?:T(\d{2})(?::(\d{2})(?::(\d{2}))?)?)?)?)?")
_DATE_SLACK = 86400  # saniye: yerel saatin yaz saati geçişlerine karşı aralığın iki yanındaki pay


def legacy_detail_keys() -> Tuple[str, ...]:
    """
    Eski maç detayı yanıtındaki dilimler, yanıttaki sırayla: dilim tablosunun `required` satırları
    (src/sports.py, DETAIL_SLICES). İsteğe bağlı dilimler (ör. tenisin `point_by_point`'i) eski yanıtta yoktur.
    """
    return tuple(detail.key for detail in DETAIL_SLICES if detail.required)


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


__all__ = ["EVENT_KEY", "LEGACY_EVENT_KEY", "LEGACY_LIST_COLUMNS", "LegacyMatchPage", "QueryService",
           "legacy_detail_keys"]
