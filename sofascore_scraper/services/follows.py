"""
Takipler servisi (docs/design/02-services.md 2.7 ve 4.3; docs/design/01-storage.md 2.3 "Follows"; plan maddesi
P21): neyin indirileceği ve izleneceği. Okuma `follows` tablosundandır (Store.follows); yazma satırın kaynağına
göre yapılır:

  legacy  `config/leagues.txt` ve `config/league_sports.json` (2.x'ten kalan lig listesi). Doğruluk kaynağı
          dosyalardır: tablo onların aynasıdır. Dosya yalnızca ad ve spor tutar; bu satırlarda sezon seçimi, veri
          seçimi, canlı izleme ve kapatma değiştirilemez (`invalid_request`). Satır `origin: "api"` ile takip
          tablosuna alınabilir (`update`): önce satır `api` olur, sonra dosyadan çıkarılır; her alanı yazılabilir
          olur. Taşıma açıktır: hiçbir kural satırları kendiliğinden taşımaz (plan maddesi FX-19).
  config  yapılandırma dosyasının `[[follow]]` girdileri: API'den değiştirilemez (`follow_managed`, 409).
  api     `state.db`'nin kendisi: API'den, web arayüzünden ve `ssc follows add` ile eklenen her takip.

Yeni bir takip her zaman `api` satırıdır (plan maddesi FX-19; önceden yapılandırma dosyası yokken bir turnuva
takibi leagues.txt'e yazılırdı ve sonra kilitli görünürdü). İndirmeler (`sync`) takipleri bu tablodan okur:
turnuvalar `sync_tournaments` (plan maddesi FX-13), takım, oyuncu ve maç takipleri `sync_others` (FX-19); her
kaynağın etkin takibi indirilir. `live = true` takipler ayrıca canlı servisin kapsamına girer.

Yapılandırma dosyasının devraldığı bir `api` satırı, dosyadan sonra çıkarıldığında geri gelmez (ST-17'nin
kuralı, karar P21): dosya kazanır; takip istenirse API'den yeniden eklenir.

SofaScore'da arama (`search`) tek istek atar (istemci, ortak bütçe); engelleme ve ağ hatası tipli hatadır. Yanıt
süreç içinde 10 dakika saklanır (plan maddesi FX-20, yazarken öneri): aynı metin (büyük-küçük harf ve boşluklar
önemsiz) bu sürede yeniden sorulursa SofaScore'a istek gitmez.

Bu modül web katmanını içe aktarmaz: leagues.txt ve spor dosyasının yazıcısı (`LegacyLeagues`) çağırandan gelir.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union

from sofascore_scraper.errors import ConflictError, NotFoundError, UpstreamBlockedError, UpstreamError, UsageError
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.logger import get_logger
from sofascore_scraper.sports import follow_slices, normalize_sport

if TYPE_CHECKING:
    from sofascore_scraper.store import Follow, Store

logger = get_logger("FollowsService")

KINDS: Tuple[str, ...] = ("tournament", "team", "player", "event")
TOURNAMENT = "tournament"
ORIGIN_LEGACY = "legacy"
ORIGIN_CONFIG = "config"
ORIGIN_API = "api"
# Bir kaynağın satırında değiştirilebilen alanlar. `origin`: leagues.txt'in satırını takip tablosuna almak için
# (yalnızca "api" değeri; FX-19)
FIELDS: Tuple[str, ...] = ("name", "sport", "seasons", "slices", "live", "enabled")
ORIGIN_FIELD = "origin"
WRITABLE: Mapping[str, Tuple[str, ...]] = {ORIGIN_LEGACY: ("sport", ORIGIN_FIELD), ORIGIN_CONFIG: (),
                                           ORIGIN_API: FIELDS}
SEARCH_LIMIT = 20
# SofaScore aramasının yanıtı bu kadar saniye saklanır (yazarken öneri, FX-20); en çok bu kadar metin
SEARCH_CACHE_SECONDS = 600.0
SEARCH_CACHE_SIZE = 256
# Aranabilen takip türleri (maç adla aranmaz; maç sayfasından ya da kimliğiyle takip edilir)
SEARCH_KINDS: Tuple[str, ...] = (TOURNAMENT, "team", "player")
_FOLLOW_ID = re.compile(r"^(tournament|team|player|event):([1-9][0-9]{0,18})$")
_LAST_N = re.compile(r"^last:[1-9][0-9]{0,3}$")
_NAME_FORBIDDEN = re.compile(r"[\r\n:/\\\x00]")
MAX_NAME = 80

Seasons = Union[str, Sequence[int]]


class LegacyLeagues(Protocol):
    """leagues.txt ve league_sports.json'ın yazıcısı (ConfigManager ve sofascore_scraper/web/league_sports.py üzerinde)."""

    def leagues(self) -> Mapping[int, str]: ...

    def add(self, name: str, tournament_id: int) -> bool: ...

    def remove(self, tournament_id: int) -> bool: ...

    def set_sport(self, tournament_id: int, sport: Optional[str]) -> None: ...


class ConfigLeagues:
    """
    `LegacyLeagues`in ConfigManager üzerindeki hali (komut satırı ve indirmeler için; web'in kendi yazıcısı
    sofascore_scraper/web/deps.py'dedir). Sporların yazıcısı çağırandan gelir (`set_sport`): bu modül web katmanını içe
    aktarmaz. Verilmezse spor değişikliği yazılmaz.

    manager: ConfigManager ya da onun gibi `get_leagues`, `add_league`, `remove_league` sunan bir nesne; None
    ise ilk yazmada kurulur (ConfigManager kurulurken leagues.txt yoksa örnekten oluşturur: salt okuyan bir
    komut onu yaratmasın diye `leagues()` o durumda tabloya dokunmaz).
    """

    def __init__(self, manager: Any = None, *, set_sport: Any = None) -> None:
        self._manager = manager
        self._set_sport = set_sport

    def _config(self) -> Any:
        if self._manager is None:
            from sofascore_scraper.config_manager import ConfigManager

            self._manager = ConfigManager()
        return self._manager

    def leagues(self) -> Mapping[int, str]:
        # Ayna (leagues.txt → tablo) yalnızca bir ConfigManager verildiyse yenilenir; yoksa tablo olduğu gibi okunur
        return self._manager.get_leagues() if self._manager is not None else {}

    def add(self, name: str, tournament_id: int) -> bool:
        return bool(self._config().add_league(name, tournament_id))

    def remove(self, tournament_id: int) -> bool:
        return bool(self._config().remove_league(tournament_id))

    def set_sport(self, tournament_id: int, sport: Optional[str]) -> None:
        if self._set_sport is not None:
            self._set_sport(self._config().league_config_path, tournament_id, sport)


@dataclass(frozen=True)
class NewFollow:
    """Eklenecek takip. seasons: "all", "current", "last:N" ya da sezon kimlikleri."""

    kind: str
    entity_id: int
    name: str
    sport: Optional[str] = None
    seasons: Seasons = "all"
    live: bool = False
    enabled: bool = True
    # Veri seçimi (plan maddesi P27): None = varsayılanlar; {"include": [...]} ya da {"enable": [...], "disable": [...]}
    slices: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class SearchHit:
    """
    SofaScore aramasındaki bir sonuç (plan maddesi FX-19): turnuva, takım ya da oyuncu (`kind`).

    category_*   turnuvanın kategorisi (takım ve oyuncuda boş)
    country_*    ülke: turnuvada kategorinin `alpha2`'si, takımda ve oyuncuda varlığın `country`'si
    team_*       oyuncunun takımı
    followed     aynı türden bir takip bu varlığı zaten adlandırıyor
    """

    id: int
    name: str
    slug: Optional[str]
    sport: Optional[str]
    category_id: Optional[int]
    category_name: Optional[str]
    category_slug: Optional[str]
    country_code: Optional[str]
    followed: bool
    kind: str = TOURNAMENT
    country_name: Optional[str] = None
    team_id: Optional[int] = None
    team_name: Optional[str] = None


# Eski ad (FX-13'e kadar yalnızca turnuva araması vardı)
TournamentHit = SearchHit


def follow_id(kind: str, entity_id: int) -> str:
    """API'deki kimlik: `tournament:17`."""
    return f"{kind}:{entity_id}"


def parse_follow_id(text: str) -> Optional[Tuple[str, int]]:
    """`tournament:17` → ("tournament", 17); biçim yanlışsa None."""
    m = _FOLLOW_ID.match(text or "")
    return (m.group(1), int(m.group(2))) if m else None


def writable_fields(origin: str) -> Tuple[str, ...]:
    return WRITABLE.get(origin, ())


def check_name(name: str) -> str:
    """leagues.txt satırı `Ad: ID`'dir ve ad dizin adına dönüşür: yeni satır, `:` ve yol ayırıcı olamaz."""
    text = (name or "").strip()
    if not text or text.strip(".") == "" or len(text) > MAX_NAME or _NAME_FORBIDDEN.search(text):
        raise UsageError("The follow name is not valid: 1 to 80 characters, no line break, ':', '/' or '\\'.",
                         {"field": "name"})
    return text


def check_sport(sport: Optional[str]) -> Optional[str]:
    if sport is None:
        return None
    slug = normalize_sport(sport)
    if slug is None:
        raise UsageError("Unknown sport.", {"field": "sport", "sport": sport})
    return slug


def check_seasons(seasons: Seasons) -> Seasons:
    if isinstance(seasons, str):
        if seasons in ("all", "current") or _LAST_N.match(seasons):
            return seasons
        raise UsageError("seasons must be all, current, last:N or a list of season ids.", {"field": "seasons"})
    ids = list(seasons)
    if not ids or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in ids):
        raise UsageError("seasons must be all, current, last:N or a list of season ids.", {"field": "seasons"})
    return tuple(dict.fromkeys(ids))


def check_slices(slices: Any) -> Optional[Dict[str, List[str]]]:
    """
    Takibin veri seçimi (sofascore_scraper/sports.py follow_slices): None, {"include": [...]} ya da {"enable": [...],
    "disable": [...]}; adlar dilim anahtarı ya da grup adıdır. Uymuyorsa `invalid_request`.
    """
    try:
        resolved = follow_slices(slices)
    except ValueError as e:
        raise UsageError(f"The data selection is not valid: {e}.", {"field": "slices"}) from None
    return {key: list(names) for key, names in resolved.items()} if resolved is not None else None


class FollowsService:
    """
    Takipler: liste, ekleme, değiştirme, kaldırma ve SofaScore'da arama.

    store: veri dizininin deposu. legacy: leagues.txt yazıcısı (ayna, kaldırma, spor ve takip tablosuna alma).
    config_file: bir yapılandırma dosyası etkin mi; FX-19'dan beri yeni takibin nereye yazılacağını belirlemez
    (her yeni takip `api` satırıdır), çağıranlar için kalır.
    """

    def __init__(self, store: "Store", legacy: LegacyLeagues, *, config_file: bool = True) -> None:
        self._store = store
        self._legacy = legacy
        self._config_file = bool(config_file)

    # -- okuma ----------------------------------------------------------------------------------------

    def list(self, *, kind: Optional[str] = None, origin: Optional[str] = None, enabled: Optional[bool] = None,
             text: Optional[str] = None, sport: Optional[str] = None) -> List["Follow"]:
        """
        Takipler, konum sırasıyla. Önce lig dosyası okunur: başka bir süreç ya da editör değiştirdiyse tablo ona
        eşitlenir (ConfigManager'ın aynası). text: adda geçen metin, büyük-küçük harf ayrımı yok. sport: yalnızca
        bu sporun takipleri (`sport_of`: kaydedilen, yoksa turnuvanın katalogdaki sporu); bilinmeyen spor
        `invalid_request`.
        """
        wanted_sport = check_sport(sport)
        self._legacy.leagues()
        rows = self._store.follows.list(kind=kind, enabled=enabled, origin=origin)
        if text:
            needle = text.casefold()
            rows = [row for row in rows if needle in row.name.casefold()]
        if wanted_sport is not None:
            rows = [row for row in rows if normalize_sport(self.sport_of(row)) == wanted_sport]
        return rows

    def sync_tournaments(self) -> List["Follow"]:
        """
        Bir eşitlemenin indirdiği turnuvalar: etkin turnuva takipleri, her kaynaktan (leagues.txt, yapılandırma
        dosyası, API ve `ssc follows`), konum sırasıyla (plan maddesi FX-13). Takım, oyuncu ve maç takipleri
        `sync_others`'tır.
        """
        return self.list(kind=TOURNAMENT, enabled=True)

    def sync_others(self) -> List["Follow"]:
        """
        Bir eşitlemenin maçlarını indirdiği takım, oyuncu ve maç takipleri: etkin olanlar, konum sırasıyla (plan
        maddesi FX-19; sofascore_scraper/services/follow_sync.py).
        """
        return [row for row in self._store.follows.list(enabled=True) if row.kind != TOURNAMENT]

    def get(self, kind: str, entity_id: int) -> Optional["Follow"]:
        self._legacy.leagues()
        return self._store.follows.get(kind, entity_id)

    def sport_of(self, follow: "Follow") -> Optional[str]:
        """Takibin sporu: kaydedilen, yoksa (turnuvada) katalogdaki maçlarından bilinen."""
        if follow.sport or follow.kind != TOURNAMENT:
            return follow.sport
        try:
            return self._store.entities.sport_of_tournament(follow.entity_id)
        except ValueError:
            return None

    # -- yazma ----------------------------------------------------------------------------------------

    def add(self, new: NewFollow) -> "Follow":
        """
        Yeni bir takip, her zaman takip tablosunun `api` satırı (yapılandırma dosyası olsun olmasın; FX-19): her
        alanı sonra da değiştirilebilir. Varlık ya da turnuva adı zaten takipteyse (leagues.txt'in satırı da)
        `follow_exists` (409); leagues.txt'te duran bir lig `origin: "api"` ile takip tablosuna alınır (`update`).
        """
        from sofascore_scraper.store import FollowSpec

        if new.kind not in KINDS:
            raise UsageError("Unknown follow kind.", {"field": "kind", "kind": new.kind})
        name = check_name(new.name)
        sport = check_sport(new.sport)
        seasons = check_seasons(new.seasons)
        slices = check_slices(new.slices)
        self._legacy.leagues()  # leagues.txt'in aynası güncel olsun: dosyadaki lig `follow_exists` alır
        spec = FollowSpec(kind=new.kind, entity_id=new.entity_id, name=name, sport=sport, seasons=seasons,
                          slices=slices, live=new.live, enabled=new.enabled)
        return self._store.follows.add(spec, origin=ORIGIN_API)

    def update(self, kind: str, entity_id: int, changes: Mapping[str, Any]) -> "Follow":
        """
        Bir takibin alanlarını değiştirir. Bilinmeyen takip `not_found`; yapılandırma dosyasının takibi
        `follow_managed` (409); leagues.txt'in takibinde yalnızca `sport` değişir (öteki alanlar `invalid_request`),
        ya da `origin: "api"` ile satır takip tablosuna alınır ve aynı çağrıdaki öteki alanlar da yazılır (`adopt`).
        """
        row = self.get(kind, entity_id)
        if row is None:
            raise NotFoundError("No follow has this id.", {"id": follow_id(kind, entity_id)})
        unknown = sorted(set(changes) - set(FIELDS) - {ORIGIN_FIELD})
        if unknown:
            raise UsageError("These fields cannot be changed.", {"fields": unknown})
        if row.origin == ORIGIN_CONFIG:
            raise ConflictError("The follow comes from the config file; change it there.",
                                {"id": follow_id(kind, entity_id), "origin": ORIGIN_CONFIG}, code="follow_managed")
        if ORIGIN_FIELD in changes:
            if changes[ORIGIN_FIELD] != ORIGIN_API:
                raise UsageError("A follow can only be moved into the follows table (origin api).",
                                 {"field": ORIGIN_FIELD})
            if row.origin == ORIGIN_LEGACY:
                row = self.adopt(kind, entity_id)
            changes = {field: value for field, value in changes.items() if field != ORIGIN_FIELD}
        values: Dict[str, Any] = {}
        for field, value in changes.items():
            if field == "name":
                values[field] = check_name(value)
            elif field == "sport":
                values[field] = check_sport(value)
            elif field == "seasons":
                values[field] = check_seasons(value)
            elif field == "slices":
                values[field] = check_slices(value)
            else:
                values[field] = bool(value)
        if row.origin == ORIGIN_LEGACY:
            unsupported = sorted(field for field in values if field not in WRITABLE[ORIGIN_LEGACY]
                                 and values[field] != _value_of(row, field))
            if unsupported:
                raise UsageError(
                    "This follow is kept in config/leagues.txt, which stores the name and the sport only.",
                    {"unsupported": unsupported, "origin": ORIGIN_LEGACY},
                )
            if "sport" in values and values["sport"] != row.sport:
                self._legacy.set_sport(entity_id, values["sport"])
            found = self.get(kind, entity_id)
            if found is None:
                raise _mirror_failed(entity_id)
            return found
        if not values:
            return row
        return self._store.follows.update(kind, entity_id, **values)

    def adopt(self, kind: str, entity_id: int) -> "Follow":
        """
        leagues.txt'in bir takibini takip tablosuna alır (plan maddesi FX-19): önce satır `api` olur (alanları ve
        konumu aynı), sonra lig dosyadan ve spor dosyasından çıkarılır; bir sonraki ayna onu geri getirmez. Dosya
        yazılamazsa satır yine `api` kalır (ayna isteği uygulanmaz) ve uyarı yazılır. `api` satırında hiçbir şey
        yapmaz; yapılandırma dosyasının takibi `follow_managed`.
        """
        row = self.get(kind, entity_id)
        if row is None:
            raise NotFoundError("No follow has this id.", {"id": follow_id(kind, entity_id)})
        if row.origin == ORIGIN_CONFIG:
            raise ConflictError("The follow comes from the config file; change it there.",
                                {"id": follow_id(kind, entity_id), "origin": ORIGIN_CONFIG}, code="follow_managed")
        if row.origin != ORIGIN_LEGACY:
            return row
        adopted = self._store.follows.adopt(kind, entity_id, origin=ORIGIN_API)
        try:
            self._legacy.remove(entity_id)
            self._legacy.set_sport(entity_id, None)
        except (OSError, StorageError) as e:
            logger.warning("Follow %s moved into the follows table, but config/leagues.txt could not be updated: %s",
                           follow_id(kind, entity_id), e)
        logger.info("Follow %s moved from config/leagues.txt into the follows table", follow_id(kind, entity_id))
        return adopted

    def remove(self, kind: str, entity_id: int) -> "Follow":
        """
        Takibi kaldırır ve kaldırılan satırı döndürür. Saklanan veri silinmez. Yapılandırma dosyasının takibi
        `follow_managed`; leagues.txt'in takibi dosyadan (ve spor dosyasından) çıkarılır.
        """
        row = self.get(kind, entity_id)
        if row is None:
            raise NotFoundError("No follow has this id.", {"id": follow_id(kind, entity_id)})
        if row.origin == ORIGIN_CONFIG:
            raise ConflictError("The follow comes from the config file; remove it there.",
                                {"id": follow_id(kind, entity_id), "origin": ORIGIN_CONFIG}, code="follow_managed")
        if row.origin == ORIGIN_LEGACY:
            if not self._legacy.remove(entity_id):
                raise NotFoundError("No follow has this id.", {"id": follow_id(kind, entity_id)})
            self._legacy.set_sport(entity_id, None)
            return row
        self._store.follows.remove(kind, entity_id)
        return row

    # -- SofaScore'da arama -----------------------------------------------------------------------------

    def search_tournaments(self, query: str, *, sport: Optional[str] = None) -> List[SearchHit]:
        """Yalnızca turnuvalar (`search(..., kinds=("tournament",))`)."""
        return self.search(query, sport=sport, kinds=(TOURNAMENT,))

    def search(self, query: str, *, sport: Optional[str] = None,
               kinds: Sequence[str] = (TOURNAMENT,)) -> List[SearchHit]:
        """
        SofaScore'da ada göre arama (tek istek, istemcinin API kökü, ortak bütçe), en çok 20 sonuç, SofaScore'un
        sırasıyla (plan maddesi FX-19). kinds: istenen türler ("tournament", "team", "player").

        Yalnızca turnuva istendiğinde `/search/unique-tournaments/{q}` (2.x'ten beri kullanılan uç nokta); takım ya
        da oyuncu istendiğinde `/search/all?q=...&page=0` (docs/all-sports/endpoints.csv, örneği
        research/all_sports/samples/football/search-all__1.json): sonuçların `type`'ı `team`, `player` ya da
        `uniqueTournament`tır. Bu uç noktada turnuva sonucunun biçimi örnekte yoktur: turnuva aramasınınkiyle
        aynı varsayılır (deneysel; canlı doğrulamada denetlenecek).

        Boş liste: SofaScore yanıt verdi, bir şey bulamadı (404 de "bulunamadı" sayılır). Engelleme `blocked` /
        `rate_limited` (503), ağ hatası ve beklenmeyen yanıt `upstream_error` (502); `details.reason`
        sofascore_scraper/web/upstream.py'deki nedendir.

        Yanıt (404 dahil) `SEARCH_CACHE_SECONDS` boyunca saklanır (FX-20): aynı uç noktaya aynı metin (büyük-küçük
        harf ve boşluk farkı önemsiz) yeniden sorulursa istek gitmez; "zaten takipte" yine takip tablosundan
        okunur. Hatalı yanıt saklanmaz. Metnin içindeki boşluklar teke indirilerek gönderilir.
        """
        from sofascore_scraper.client import endpoints

        text = (query or "").strip()
        if len(text) < 2:
            raise UsageError("The search text needs at least 2 characters.", {"field": "q"})
        wanted_sport = check_sport(sport)
        wanted = tuple(dict.fromkeys(kinds))
        unknown = [kind for kind in wanted if kind not in SEARCH_KINDS]
        if not wanted or unknown:
            raise UsageError("Unknown search kind.", {"field": "kinds", "kinds": unknown})
        only_tournaments = wanted == (TOURNAMENT,)
        text = " ".join(text.split())
        cache_key = (only_tournaments, text.casefold())
        found, results = _search_cache.get(cache_key)
        if found:
            logger.debug("Search answered from the cache")
        else:
            path = endpoints.search_unique_tournaments(text) if only_tournaments else endpoints.search_all(text)
            results = _results(self._ask(path), only_tournaments)
            _search_cache.put(cache_key, results)
        followed = {(row.kind, row.entity_id) for row in self._store.follows.list()}
        hits: List[SearchHit] = []
        for item in results or ():
            hit = _search_hit(item, followed, typed=not only_tournaments)
            if hit is None or hit.kind not in wanted or (wanted_sport is not None and hit.sport != wanted_sport):
                continue
            hits.append(hit)
            if len(hits) >= SEARCH_LIMIT:
                break
        return hits

    def _ask(self, path: str) -> Any:
        """Aramanın tek isteği; 404 None (bulunamadı), öteki hatalar tipli hata."""
        from sofascore_scraper import bridge_health, throttle
        from sofascore_scraper.client import api_url
        from sofascore_scraper.exceptions import APIError, NetworkError, RateLimitError, ResourceNotFoundError, SofaScoreScraperError
        from sofascore_scraper.utils import make_api_request

        before = bridge_health.snapshot()
        try:
            # Etkileşimli arama: 403 bekleme döngüsüyle bir sunucu işçisini dakikalarca tutma. Kullanıcı bekliyor:
            # ortak bütçede indirmenin bekleyen isteklerinin önüne geçer (öncelik şeridi, FX-23, F16)
            with throttle.interactive():
                return make_api_request(api_url(path), max_retries=1, timeout=10, raise_errors=True)
        except SofaScoreScraperError as e:
            if isinstance(e, ResourceNotFoundError):
                return None
            if isinstance(e, RateLimitError):
                raise UpstreamBlockedError("SofaScore is rate limiting us.", {"reason": "rate_limited"},
                                           code="rate_limited") from None
            if isinstance(e, APIError) and e.status_code == 403:
                reason = "browser" if _browser_failed(before, bridge_health.snapshot()) else "blocked"
                raise UpstreamBlockedError("SofaScore refused the request.", {"reason": reason},
                                           code="blocked") from None
            reason = "network" if isinstance(e, NetworkError) else "upstream"
            logger.error("Search failed (%s): %s", reason, type(e).__name__)
            raise UpstreamError("SofaScore could not be searched.", {"reason": reason}) from None


def _results(data: Any, only_tournaments: bool) -> Optional[List[Any]]:
    """Aramanın yanıtındaki sonuç listesi; 404 (None) için None. Beklenmeyen biçim `upstream_error`."""
    if data is None:
        return None
    key = "uniqueTournaments" if only_tournaments else "results"
    results = data.get(key, data.get("results")) if isinstance(data, dict) else None
    if not isinstance(results, list):
        logger.error("Search: the answer has no result list")
        raise UpstreamError("SofaScore answered in an unexpected form.", {"reason": "upstream"})
    return results


class _SearchCache:
    """
    SofaScore aramasının yanıtları, süreç içinde (plan maddesi FX-20): anahtar (yalnızca turnuva mı, katlanmış metin)
    → sonuç listesi (404: None). Süresi dolan girdi okunmaz; dolunca en eski kullanılan çıkar. Thread güvenlidir.
    """

    def __init__(self, seconds: float, size: int, clock: Any = time.monotonic) -> None:
        self._seconds, self._size, self._clock = seconds, size, clock
        self._items: "OrderedDict[Tuple[bool, str], Tuple[float, Optional[List[Any]]]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Tuple[bool, str]) -> Tuple[bool, Optional[List[Any]]]:
        """(bulundu mu, sonuçlar)."""
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return False, None
            if self._clock() - entry[0] >= self._seconds:
                del self._items[key]
                return False, None
            self._items.move_to_end(key)
            return True, entry[1]

    def put(self, key: Tuple[bool, str], results: Optional[List[Any]]) -> None:
        with self._lock:
            self._items[key] = (self._clock(), results)
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_search_cache = _SearchCache(SEARCH_CACHE_SECONDS, SEARCH_CACHE_SIZE)


def clear_search_cache() -> None:
    """Saklanan arama yanıtlarını unutur (testler ve gerekirse çağıranlar)."""
    _search_cache.clear()


def _value_of(row: "Follow", field: str) -> Any:
    value = getattr(row, field)
    if field == "slices":
        return check_slices(value) if value is not None else None
    return tuple(value) if isinstance(value, list) else value


def _mirror_failed(entity_id: int) -> Exception:
    return StorageError("The follows table could not be updated from config/leagues.txt.",
                        detail=follow_id(TOURNAMENT, entity_id))


def _browser_failed(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Challenge'ı çözecek gömülü tarayıcı bu istek sırasında başlatılamadı mı (köprünün son hatası)."""
    last = after.get("last_error") or {}
    return bool(last.get("kind") == "browser" and after.get("last_failure_at") != before.get("last_failure_at"))


# `/search/all` sonuçlarının `type`'ı → takip türü
_SEARCH_TYPES: Mapping[str, str] = {"uniqueTournament": TOURNAMENT, "team": "team", "player": "player"}


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sport_of(*holders: Mapping[str, Any]) -> Optional[str]:
    for holder in holders:
        sport = _mapping(holder.get("sport"))
        found = normalize_sport(sport.get("slug") or sport.get("name"))
        if found is not None:
            return found
    return None


def _search_hit(item: Any, followed: Set[Tuple[str, int]], *, typed: bool) -> Optional[SearchHit]:
    """
    Bir arama sonucu → SearchHit; tanınmayan biçim ya da tür None. typed: `/search/all`'ın sonucu (`type` alanı
    türü söyler); değilse turnuva aramasının sonucu.
    """
    if not isinstance(item, Mapping):
        return None
    kind = _SEARCH_TYPES.get(str(item.get("type"))) if typed else TOURNAMENT
    entity = item.get("entity", item)
    if kind is None or not isinstance(entity, Mapping):
        return None
    entity_id, name = _number(entity.get("id")), _text(entity.get("name"))
    if entity_id is None or name is None:
        return None
    country = _mapping(entity.get("country"))
    if kind == TOURNAMENT:
        category = _mapping(entity.get("category"))
        return SearchHit(
            id=entity_id, name=name, slug=_text(entity.get("slug")), sport=_sport_of(category, entity),
            category_id=_number(category.get("id")), category_name=_text(category.get("name")),
            category_slug=_text(category.get("slug")),
            country_code=_text(category.get("alpha2")) or _text(_mapping(category.get("country")).get("alpha2")),
            followed=(TOURNAMENT, entity_id) in followed, kind=TOURNAMENT,
            country_name=_text(_mapping(category.get("country")).get("name")),
        )
    team = _mapping(entity.get("team")) if kind == "player" else {}
    return SearchHit(
        id=entity_id, name=name, slug=_text(entity.get("slug")), sport=_sport_of(entity, team),
        category_id=None, category_name=None, category_slug=None, country_code=_text(country.get("alpha2")),
        followed=(kind, entity_id) in followed, kind=kind, country_name=_text(country.get("name")),
        team_id=_number(team.get("id")), team_name=_text(team.get("name")),
    )


__all__ = [
    "ConfigLeagues",
    "FIELDS",
    "FollowsService",
    "KINDS",
    "LegacyLeagues",
    "NewFollow",
    "SEARCH_CACHE_SECONDS",
    "SEARCH_KINDS",
    "SEARCH_LIMIT",
    "SearchHit",
    "TournamentHit",
    "check_name",
    "check_seasons",
    "clear_search_cache",
    "check_sport",
    "follow_id",
    "parse_follow_id",
    "writable_fields",
]
