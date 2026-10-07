"""
Bahis oranlarının ve maç dışı dilimlerin okuması (plan maddesi P28): API v1'in oran ve sezon kaynakları ile dışa
aktarmanın `odds` ve `standings` veri kümeleri buradan okur.

    OwnerDataService(store).odds_slices(event_id)      maçın saklanan oran dilimleri (Slice, yüksüz)
    OwnerDataService(store).odds(event_id, key, ...)   bir oran diliminin anlık görüntüleri (Odds), eskiden yeniye
    OwnerDataService(store).season_slices(season_id)   sezonun saklanan dilimleri (puan durumu, sezon bilgisi ...)
    OwnerDataService(store).standings(season_id, ...)  sezonun puan durumu satırları (StandingsRow)

Tek bir okuma bir anlık görüntüdür: oranlar maç bitene kadar değişir ve geçmişi ancak yeniden okunduysa vardır
(dilimin geçmişi, `keep_history`; sofascore_scraper/store/history.py). Geçmişi olmayan dilim (eski bir kayıt) yalnızca saklanan
yüküyle tek bir görüntü verir. Servis yazmaz, SofaScore'a istek atmaz.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Iterator, List, Optional

from sofascore_scraper.logger import get_logger
from sofascore_scraper.schema import mappers, models
from sofascore_scraper.sports import get_slice
from sofascore_scraper.store import PayloadCorrupt, PayloadMissing, Ref, StoreError

if TYPE_CHECKING:
    from sofascore_scraper.schema import Slice
    from sofascore_scraper.store import SliceInfo, Store

logger = get_logger("OwnerDataService")

ODDS_GROUP = "odds"
NORMALIZED_ODDS_KEYS = mappers.ODDS_KEYS  # şema v1 Odds kaydıyla verilen oran dilimleri
STANDINGS_KEY = "standings"


def is_odds_slice(key: str) -> bool:
    """Maçın bir oran dilimi mi (kayıt defterinde grubu `odds`, sahibi maç)."""
    spec = get_slice(key)
    return spec is not None and spec.owner == "event" and spec.group == ODDS_GROUP


def _provider_id(info: "SliceInfo") -> Optional[int]:
    found = info.meta.get("provider_id") if info.meta else None
    if isinstance(found, int) and not isinstance(found, bool):
        return found
    return int(info.sub) if info.sub.isdigit() else None


class OwnerDataService:
    """Oranların ve maç dışı dilimlerin okuma yüzü; her yöntem kataloğa ve yük dosyalarına bakar."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    # -- bahis oranları ----------------------------------------------------------------------------------

    def odds_slices(self, event_id: int) -> Optional[List["Slice"]]:
        """Maçın saklanan (satırı olan) oran dilimleri, anahtar sırasıyla; maç bilinmiyorsa None."""
        if self._store.events.get(event_id) is None:
            return None
        return [mappers.slice_from_info(info) for info in self._store.events.slices(event_id)
                if is_odds_slice(info.key) and info.state != "not_requested"]

    def odds(self, event_id: int, key: str, sub: Optional[str] = None, *,
             history: bool = True) -> Optional[List[models.Odds]]:
        """
        Bir oran diliminin kayıtları (`odds_all` ya da `odds_featured`), eskiden yeniye: geçmişi varsa her anlık
        görüntü (history=False: yalnızca saklanan son yük). sub: sağlayıcı kimliği; verilmezse saklanan bütün
        sağlayıcılar. Maç bilinmiyorsa None; anahtar bu şemayla verilmiyorsa ValueError.
        """
        if key not in mappers.ODDS_KEYS:
            raise ValueError(f"key: expected one of {', '.join(mappers.ODDS_KEYS)}, got {key!r}")
        if self._store.events.get(event_id) is None:
            return None
        infos = [info for info in self._store.events.slices(event_id)
                 if info.key == key and (sub is None or info.sub == sub) and info.has_payload]
        out: List[models.Odds] = []
        for info in infos:
            out.extend(self._odds_of(Ref.event(event_id), info, history=history))
        return out

    def _odds_of(self, ref: Ref, info: "SliceInfo", *, history: bool) -> Iterator[models.Odds]:
        provider = _provider_id(info)
        if history and info.history_count:
            try:
                for snap in self._store.history.snapshots(ref, info.key, info.sub):
                    found = mappers.odds_from_payload(ref.id, info.key, snap.payload, provider_id=provider,
                                                      fetched_at=snap.fetched_at)
                    if found is not None:
                        yield found
                return
            except (PayloadCorrupt, PayloadMissing, StoreError) as e:
                logger.warning("Event %s: the history of %s/%s is unreadable (%s); the stored payload is used",
                               ref.id, info.key, info.sub, e)
        payload = self._payload(lambda: self._store.events.payload(ref.id, info.key, info.sub), ref, info.key)
        found = mappers.odds_from_payload(ref.id, info.key, payload, provider_id=provider, fetched_at=info.fetched_at)
        if found is not None:
            yield found

    def odds_lines(self, event_ids: Iterable[int]) -> Iterator[models.OddsLine]:
        """Maçların oran satırları (dışa aktarmanın `odds` veri kümesi): maç, dilim, anlık görüntü sırasıyla."""
        for event_id in event_ids:
            for info in self._store.events.slices(event_id):
                if info.key in mappers.ODDS_KEYS and info.has_payload:
                    for odds in self._odds_of(Ref.event(event_id), info, history=True):
                        yield from mappers.odds_lines(odds)

    # -- sezon dilimleri ---------------------------------------------------------------------------------

    def _season_ref(self, season_id: int) -> Optional[Ref]:
        row = self._store.entities.season(season_id)
        if row is None or row.tournament_id is None:
            return None
        return Ref.season(int(row.tournament_id), int(season_id))

    def season_slices(self, season_id: int) -> Optional[List["Slice"]]:
        """Sezonun saklanan dilimleri (sezon listesi ve programlar dahil), (anahtar, alt anahtar) sırasıyla."""
        ref = self._season_ref(season_id)
        if ref is None:
            return None
        return [mappers.slice_from_info(info) for info in self._store.entities.slices(ref)
                if info.state != "not_requested"]

    def standings(self, season_id: int, table: Optional[str] = None) -> Optional[List[models.StandingsRow]]:
        """Sezonun puan durumu satırları; table: `total` ya da `home` (verilmezse ikisi). Sezon bilinmiyorsa None."""
        ref = self._season_ref(season_id)
        if ref is None:
            return None
        return list(self._standings_of(ref, table))

    def _standings_of(self, ref: Ref, table: Optional[str]) -> Iterator[models.StandingsRow]:
        for info in self._store.entities.slices(ref):
            if info.key != STANDINGS_KEY or not info.has_payload or (table is not None and info.sub != table):
                continue
            payload = self._payload(lambda info=info: self._store.entities.payload(ref, info.key, info.sub), ref,
                                    info.key)
            yield from mappers.standings_rows(int(ref.tournament_id or 0), ref.id, info.sub, payload,
                                              fetched_at=info.fetched_at)

    def standings_rows(self, seasons: Iterable[Ref]) -> Iterator[models.StandingsRow]:
        """Sezonların puan durumu satırları (dışa aktarmanın `standings` veri kümesi)."""
        for ref in seasons:
            yield from self._standings_of(ref, None)

    @staticmethod
    def _payload(read: Any, ref: Ref, key: str) -> Any:
        try:
            return read()
        except (PayloadMissing, PayloadCorrupt) as e:
            logger.warning("%s %s: the stored %s payload is unreadable: %s", ref.kind.capitalize(), ref.id, key, e)
            return None


__all__ = ["NORMALIZED_ODDS_KEYS", "ODDS_GROUP", "OwnerDataService", "STANDINGS_KEY", "is_odds_slice"]
