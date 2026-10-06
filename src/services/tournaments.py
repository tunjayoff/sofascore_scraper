"""
Turnuva okuma servisi: saklanan sezon listeleri, bir sezonun program sayfaları ve turnuvanın sporu
(docs/design/02-services.md 2.2; plan maddesi RD-5).

Bütün okumalar deponun kataloğundan yapılır (`Store.entities`, `Store.events`); dosya adlarına ve dizin
adlarına bakılmaz. Böylece bir ligin sezon listesi için her okuyucu aynı dosyayı seçer.

Sezon listesi kuralı (docs/design/01-storage.md 5.1): bir turnuvanın birden çok sezon listesi dosyası varsa
adı ne olursa olsun **en yenisi** geçerlidir (`<id>_seasons.json`, `<id>_<ad>_seasons.json`,
`<ad>_seasons.json`); `league_seasons.csv` yalnızca hiç JSON listesi olmayan turnuva için kullanılır.
Okunamayan dosya (yarım kalmış JSON, UTF-8 olmayan içerik, nesne yerine dizi ya da metin) sezon listesi
sayılmaz: turnuvanın okunabilen en yeni dosyası geçerli olur, o da yoksa turnuvanın listesi yoktur. Başında
UTF-8 BOM olan dosya okunur.

Adında kimlik olmayan dosya (`<ad>_seasons.json`) turnuvasına ligin adıyla bağlanır. Katalog adları takip
tablosundan alır (`Store.follows.leagues()`); yapılandırma dosyası (sofascore.toml) aynı turnuvaya başka bir
ad veriyorsa ya da tablo henüz dolmadıysa o ad, diskteki dosya ve dizin adlarının kurulduğu ad (leagues.txt'teki)
değildir ve katalog o dosyayı turnuvasına bağlayamaz. Bu yüzden okuyucu ligin adını `name` ile verir: ad
kataloğun kullandığından farklıysa eski düzen dosyaları aynı kuralla bu adla bir kez daha çözülür ve kazanan,
adında kimlik olmayan bir dosyaysa (kataloğun göremediği tek durum) o geçerli olur; değilse katalog konuşur.

Servis yazmaz, istek atmaz ve depoyu açmaz: açık bir `Store` alır. Depolama hataları (StoreError) çağırana
çıkar.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Set

from src import sports
# Paket kökü üzerinden: Store'un cephesi ve sorgu türleri ilk çağrıda yüklenir (bu modülü içe aktarmak hafif kalır)
from src import store as store_api

if TYPE_CHECKING:
    from src.store import Store

SEASONS_KEY = "seasons"  # turnuvanın sezon listesi dilimi
SCHEDULE_KEY = "schedule"  # sezonun program sayfaları (tur dosyaları, olay sayfaları)
ROW_SOURCE_LISTING = "listing"
LAYOUT_V3 = "v3"  # katalogda v3 düzeninde duran dilimin `layout` değeri
SEASONS_SUFFIX = "_seasons.json"
# `<id>_<ad>_seasons.json` ve `<id>_seasons.json`: turnuvası adında yazılı dosya (soneksiz ada uygulanır;
# eski düzen okuyucusunun kuralıyla aynı)
_ID_NAMED = re.compile(r"[0-9]+(?:_.*)?")
_ALL = 1_000_000  # `EntityStore.tournaments` için "hepsi": sınırı olmayan bir çağrısı yok


def _listed(payload: Any) -> List[Any]:
    """Sezon listesi yükünün `seasons` listesi, saklandığı gibi; liste değilse boş liste."""
    seasons = payload.get("seasons") if isinstance(payload, Mapping) else None
    return list(seasons) if isinstance(seasons, list) else []


def _named_only_lists(lists: Iterable[Any], names: Mapping[int, str]) -> Dict[int, Mapping[str, Any]]:
    """
    `names`teki turnuvalardan, geçerli sezon listesi adında kimlik olmayan bir dosya (`<ad>_seasons.json`) olanlar:
    {turnuva kimliği: yük}. lists: eski düzen dosyalarının `names` ile çözülmüş taraması (`_legacy_lists`).
    Geçerli listesi `<id>_...` dosyası ya da CSV olan turnuva sonuçta yer almaz: onu katalog da aynı biçimde görür.
    """
    found: Dict[int, Mapping[str, Any]] = {}
    for item in lists:
        if item.tournament_id not in names or item.superseded_by is not None or item.kind != "json":
            continue
        stem = item.path.rsplit("/", 1)[-1][: -len(SEASONS_SUFFIX)]
        if not _ID_NAMED.fullmatch(stem):
            found[item.tournament_id] = item.payload
    return found


def _legacy_lists(store: "Store", names: Mapping[int, str]) -> List[Any]:
    """
    Eski düzendeki sezon listesi dosyalarının taraması (`seasons/`, `league_seasons.csv`), `<ad>_seasons.json`
    dosyaları `names` ile çözülerek. Seçim kuralı kataloğunkiyle aynı koddur (en yeni dosya kazanır).
    """
    return store.catalog.reader.season_lists(names)


def _unknown_to_catalog(store: "Store", names: Mapping[int, Optional[str]]) -> Dict[int, str]:
    """`names`ten, adı kataloğun kullandığı addan (takip tablosundaki) farklı olan ligler: {kimlik: ad}."""
    known = store.follows.leagues()
    return {int(league_id): name for league_id, name in names.items()
            if isinstance(name, str) and name and known.get(int(league_id)) != name}


def _with_v3_list(store: "Store") -> Set[int]:
    """Sezon listesi v3 düzeninde saklanan turnuvalar: onların listesi adında kimlik olmayan eski dosyaya yenilmez."""
    return set(store.entities.tournaments_with_slice(SEASONS_KEY, layout_name=LAYOUT_V3))


def _from_catalog(store: "Store", tournament_id: int) -> Optional[Mapping[str, Any]]:
    payload = store.entities.payload(store_api.Ref.tournament(tournament_id), SEASONS_KEY)
    return payload if isinstance(payload, Mapping) else None


def seasons_of(store: "Store", tournament_id: int, *, name: Optional[str] = None) -> Optional[List[Any]]:
    """
    Turnuvanın saklanan sezon listesi: SofaScore'un verdiği sezon nesneleri, verdiği sırayla. Turnuvanın
    sezon listesi yoksa None; listesi olan ama `seasons` anahtarı liste olmayan yük boş liste verir.

    name: ligin, diskteki dosya ve dizin adlarının kurulduğu adı (ConfigManager'daki). Kataloğun bildiği addan
    farklıysa adında kimlik olmayan dosyalar bu adla çözülür (modül belgesi); verilmezse yalnızca katalog konuşur.
    """
    tournament_id = int(tournament_id)
    differing = _unknown_to_catalog(store, {tournament_id: name})
    if differing and tournament_id in _with_v3_list(store):
        differing = {}
    payload = _named_only_lists(_legacy_lists(store, differing), differing).get(tournament_id) if differing else None
    if payload is None:
        payload = _from_catalog(store, tournament_id)
    return None if payload is None else _listed(payload)


def season_lists(store: "Store", names: Mapping[int, Optional[str]]) -> Dict[int, List[Any]]:
    """
    Saklanan bütün sezon listeleri: {turnuva kimliği: sezonlar}. Listesi olmayan turnuva sonuçta yer almaz.

    names: adlarıyla birlikte yapılandırılmış ligler (bkz. `seasons_of`). Hangi turnuvalara bakılacağı:
    yapılandırılmış ligler, takipler, katalogda satırı olan (en az bir maçı bilinen) turnuvalar ve eski düzende
    sezon listesi dosyası olan turnuvalar, ayrıca katalogda sezon listesi saklanan her turnuva
    (`EntityStore.tournaments_with_slice`; maçı bilinmeyen, yapılandırılmamış ve takip edilmeyen bir turnuvanın
    yalnızca v3'te duran listesi de böyle bulunur). v3 düzeninde saklanan bir liste, adında kimlik olmayan eski
    bir dosyaya (`<ad>_seasons.json`) yenilmez.
    """
    configured: Dict[int, Optional[str]] = {int(league_id): name for league_id, name in names.items()}
    differing = _unknown_to_catalog(store, configured)
    legacy = _legacy_lists(store, differing)
    v3_lists = _with_v3_list(store) if differing else set()
    named_only = {tid: payload for tid, payload in _named_only_lists(legacy, differing).items() if tid not in v3_lists}

    wanted: Dict[int, None] = dict.fromkeys(configured)  # sıralı küme
    wanted.update(dict.fromkeys(store.follows.leagues()))
    wanted.update(dict.fromkeys(row.id for row in store.entities.tournaments(limit=_ALL)))
    # Sezon listesi olan ama maçı, takibi ve satırı olmayan turnuva (yalnızca v3'te duran liste dahil)
    wanted.update(dict.fromkeys(store.entities.tournaments_with_slice(SEASONS_KEY)))
    wanted.update(dict.fromkeys(item.tournament_id for item in legacy if item.tournament_id is not None))

    out: Dict[int, List[Any]] = {}
    for tournament_id in wanted:
        payload = named_only.get(tournament_id)
        if payload is None:
            payload = _from_catalog(store, tournament_id)
        if payload is not None:
            out[tournament_id] = _listed(payload)
    return out


def schedule_pages(store: "Store", tournament_id: int, season_id: int) -> int:
    """
    Sezonun saklanan program sayfası sayısı (tur dosyaları ve olay sayfaları). Aynı sayfanın iki dizindeki
    kopyası bir kez sayılır; okunamayan sayfa da sayılır (dosyası vardır).
    """
    ref = store_api.Ref.season(int(tournament_id), int(season_id))
    return sum(1 for info in store.entities.slices(ref) if info.key == SCHEDULE_KEY)


def has_matches(store: "Store", tournament_id: int, season_id: int) -> bool:
    """
    Sezonun maç listesi indirilmiş mi: saklanan bir program sayfası var ya da (yalnızca özet dosyası kalmış
    eski sezonlarda) bir listeden bilinen maçı var. Yalnızca kendi detayı indirilmiş bir maç sezonu "listesi
    indirilmiş" yapmaz.
    """
    if schedule_pages(store, tournament_id, season_id):
        return True
    scope = store_api.Scope(tournament_ids=(int(tournament_id),), season_ids=(int(season_id),))
    return any(row.row_source == ROW_SOURCE_LISTING or row.listed_in is not None
               for row in store.events.iter(store_api.EventQuery(scope=scope)))


def sport_of(store: "Store", tournament_id: int) -> Optional[str]:
    """
    Turnuvanın sporu (src/sports.py'deki kayıtlı kısa ad), indirilmiş veriden: turnuvanın katalog satırı,
    orada yoksa maçlarında en çok geçen spor. Bilinmiyorsa ya da kayıtlı bir spor değilse None.
    """
    return sports.normalize_sport(store.entities.sport_of_tournament(int(tournament_id)))


__all__ = ["seasons_of", "season_lists", "schedule_pages", "has_matches", "sport_of"]
