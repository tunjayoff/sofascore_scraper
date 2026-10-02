"""
Sorgu servisi: yüzlerin (web, CLI, kitaplık) okuma tarafı (docs/design/02-services.md 2.3 ve 2.7).

Okuyucular dosya ağacını gezmez; her soru deponun okuma API'sine sorulur (`Store.events`, `Store.entities`,
`Store.changes`: docs/design/01-storage.md 2.3). Katalog, depo açılırken dosyalarla eşitlenir ve eski düzen
yazıcılarının her yazmasından sonra güncellenir (gölge kip, 01-storage.md 3.5); iki düzen de (eski
`match_details/` ağacı ve v3) aynı çağrılarla okunur.

Bu adımda (plan maddesi RD-1) tek iş maç detayıdır: `GET /api/matches/{id}` yanıtının bugünkü sözlüğü.
Maç listeleri (RD-2) ve eksik detay / ihtiyaç sorguları (RD-3) kendi plan maddeleriyle eklenir; eski `/api`
yanıt biçimlerini üreten işlevler burada durur ve eski uç noktalarla birlikte kaldırılır (P30).

Eski okuyucudan (dizinden dosya dosya okuyan `routes/matches._get_match_details_sync`) farklar; hepsi yalnızca
eski biçimli kayıtlarda görünür, bugünkü kodun yazdığı dizinlerde yanıt aynıdır (01-storage.md 5.1, 5.2):

  * Yalnızca birleşik dosyası (`<id>/<id>.json`) olan dizin de bir maçtır; eskiden 404 dönüyordu.
  * Bir dilim önce kendi dosyasından, dosya yoksa birleşik dosyadan okunur. Eskiden birleşik dosya varsa
    yalnızca o dönüyordu (içindeki tanınmayan anahtarlarla birlikte).
  * Okunamayan (yarıda kesilmiş) dilim dosyası yalnızca o dilimi düşürür; eskiden bütün maç 500 dönüyordu.
  * Aynı maç iki dizinde duruyorsa olay yükü en yeni olan kopya okunur.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional, Tuple

from src.logger import get_logger
from src.sports import DETAIL_SLICES
from src.store import PayloadCorrupt, PayloadMissing

if TYPE_CHECKING:
    from src.store import Store

logger = get_logger("QueryService")

EVENT_KEY = "event"  # `/event/{id}` yükünün depodaki dilim anahtarı
LEGACY_EVENT_KEY = "basic"  # aynı yükün eski yanıtlardaki (ve basic.json'daki) adı
# SQLite'ın saklayabildiği kimlik aralığı: dışındaki bir sayı hiçbir maçın kimliği olamaz
_ID_RANGE = (-(2 ** 63), 2 ** 63 - 1)


def legacy_detail_keys() -> Tuple[str, ...]:
    """
    Eski maç detayı yanıtındaki dilimler, yanıttaki sırayla: dilim tablosunun `required` satırları
    (src/sports.py, DETAIL_SLICES). İsteğe bağlı dilimler (ör. tenisin `point_by_point`'i) eski yanıtta yoktur.
    """
    return tuple(detail.key for detail in DETAIL_SLICES if detail.required)


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


__all__ = ["EVENT_KEY", "LEGACY_EVENT_KEY", "QueryService", "legacy_detail_keys"]
