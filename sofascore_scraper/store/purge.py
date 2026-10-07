"""
Bir turnuvanın saklanan verisinin silinmesi (plan maddesi FX-19; ilk kullanıcı incelemesinin "bir ligin verisini
sil" eksiği). Açık bir depoda `store.purge` olarak durur.

`Store.clear` (docs/design/01-storage.md 9.3) bütün veriyi kapsama göre siler; `Purger.tournament` yalnızca bir
turnuvanınkini (ya da bir sezonununkini):

  maçlar           katalogun o turnuvaya (sezona) bağladığı her maçın v3 dizini (`v3/events/...`, geçmiş dosyaları
                   dahil) ve eski düzendeki bütün kopyaları (`match_details` altında adı maç kimliği olan dizinler)
  programlar       v3'te sezon dizini (`v3/tournaments/<ut>/seasons/<sid>/`: program sayfaları ve sezonun maç dışı
                   dilimleri), eski düzende `matches/<lig>/<sezon>/` ve lig dizinindeki o sezonun özet dosyaları
  sezon listesi    yalnızca turnuvanın tamamı silinirken: v3 turnuva dizini (`v3/tournaments/<ut>/`), eski düzende
                   `matches/<lig>/` ve `seasons/` altındaki JSON sezon listesi

Takipler, değişiklik günlüğü (`changes/`, `score_changes.jsonl`), iş geçmişi, yedekler ve dışa aktarmalar kalır;
takımların ve oyuncuların dizinleri (başka turnuvaların maçları da onlara bağlıdır) kalır. Silme `maintenance`
kilidiyle yapılır (bu süreç tutuyorsa onun altında, tutmuyorsa işlem süresince alınır: `Store.clear` gibi) ve
ardından, silme yarıda kalsa da, katalog aynı kilit altında kalan dosyalardan yerinde yeniden kurulur.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Set

from sofascore_scraper.store import files, layout
from sofascore_scraper.store.errors import StoreError
from sofascore_scraper.store.events import EventQuery, Scope
from sofascore_scraper.store.lease import MAINTENANCE, Lease
from sofascore_scraper.store.legacy import DETAILS_DIR, LegacyProblem, LegacyReader

if TYPE_CHECKING:
    from sofascore_scraper.store.api import Store

logger = logging.getLogger("Store")

_PURPOSE = "op:clear"
_IN_PLACE = "in_place"  # sofascore_scraper/store/indexer.py MODE_IN_PLACE


@dataclass(frozen=True)
class TournamentClearReport:
    """
    `Purger.tournament` sonucu.

    events           silinen maç sayısı (v3 dizini ya da eski düzen kopyası olan)
    event_dirs       silinen maç dizini sayısı (bir maçın birden çok eski kopyası olabilir)
    listings         silinen program ve sezon listesi yolu sayısı (dizin ya da dosya)
    catalog_rebuilt  katalog kalan dosyalardan yeniden kuruldu; False: kurulamadı (uyarı yazıldı)
    """

    tournament_id: int
    season_id: Optional[int]
    events: int
    event_dirs: int
    listings: int
    catalog_rebuilt: bool


def _check_id(value: Optional[int], what: str, *, optional: bool = False) -> Optional[int]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{what}: expected a positive integer, got {value!r}")
    return value


def _remove_empty(paths: Set[str], *, keep: str) -> None:
    """Boş kalan eski düzen kap dizinleri (lig ve sezon dizini), en derinden başlayarak; `keep` ve dışı kalır."""
    root = os.path.normcase(os.path.abspath(keep))
    for path in sorted(paths, key=len, reverse=True):
        full = os.path.normcase(os.path.abspath(path))
        if full == root or not full.startswith(root + os.sep):
            continue
        try:
            os.rmdir(path)  # boş değilse OSError: dizin yerinde kalır
        except OSError:
            pass


class Purger:
    """Turnuva başına silme. `store.purge` olarak durur."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    def tournament(self, tournament_id: int, *, season_id: Optional[int] = None) -> TournamentClearReport:
        """
        Turnuvanın (season_id verildiyse yalnızca o sezonun) saklanan verisini siler (modül belgesi). Kimlik pozitif
        tamsayı değilse ValueError; salt okunur depo ya da dosya sistemi hatası StoreError; başka bir süreç dizini
        kullanıyorsa LeaseHeld.
        """
        store = self._store
        tid = _check_id(tournament_id, "tournament_id")
        sid = _check_id(season_id, "season_id", optional=True)
        assert tid is not None
        store._require_open()
        if store.readonly:
            raise StoreError(f"A store opened read-only cannot be cleared: {store.data_dir}", path=str(store.data_dir))
        lease: Optional[Lease] = None
        if not store._leases.held_here(MAINTENANCE):
            lease = store._leases.acquire(MAINTENANCE, purpose=_PURPOSE)
        events = event_dirs = listings = 0
        rebuilt = False
        try:
            try:
                events, event_dirs = self._events(tid, sid)
                listings = self._listings(tid, sid)
            finally:
                rebuilt = self._rebuild()
        finally:
            if lease is not None:
                lease.release()
        logger.info("Tournament data cleared in %s: tournament=%s season=%s events=%d dirs=%d listings=%d "
                    "catalog_rebuilt=%s", store.data_dir, tid, sid if sid is not None else "-", events, event_dirs,
                    listings, rebuilt)
        return TournamentClearReport(tournament_id=tid, season_id=sid, events=events, event_dirs=event_dirs,
                                     listings=listings, catalog_rebuilt=rebuilt)

    # -- adımlar --

    def _event_ids(self, tournament_id: int, season_id: Optional[int]) -> Set[int]:
        scope = Scope(tournament_ids=(tournament_id,), season_ids=(season_id,) if season_id is not None else ())
        return {row.id for row in self._store.events.iter(EventQuery(scope=scope))}

    def _events(self, tournament_id: int, season_id: Optional[int]) -> "tuple[int, int]":
        root = os.fspath(self._store.data_dir)
        ids = self._event_ids(tournament_id, season_id)
        removed: Set[int] = set()
        dirs = 0
        for event_id in sorted(ids):
            if files.remove_tree(layout.resolve(root, layout.event_dir(event_id))):
                removed.add(event_id)
                dirs += 1
        reader = LegacyReader(root)
        containers: Set[str] = set()
        for found in reader.event_dirs():
            if found.name.isdigit() and int(found.name) in ids:
                if files.remove_tree(reader.resolve(found.path)):
                    removed.add(int(found.name))
                    dirs += 1
                    parent = os.path.dirname(reader.resolve(found.path))
                    containers.update((parent, os.path.dirname(parent)))
        _remove_empty(containers, keep=reader.resolve(DETAILS_DIR))
        return len(removed), dirs

    def _listings(self, tournament_id: int, season_id: Optional[int]) -> int:
        root = os.fspath(self._store.data_dir)
        paths: List[str] = []
        if season_id is not None:
            paths.append(layout.resolve(root, layout.season_dir(tournament_id, season_id)))
        else:
            paths.append(layout.resolve(root, layout.tournament_dir(tournament_id)))
        reader = LegacyReader(root)
        problems: List[LegacyProblem] = []
        leagues: List[str] = []
        for tid, league_rel, sid, season_rel in reader._season_dirs(problems):
            if tid != tournament_id:
                continue
            if season_id is None:
                if league_rel not in leagues:
                    leagues.append(league_rel)
            elif sid == season_id:
                paths.append(reader.resolve(season_rel))
        if season_id is not None:
            # Lig dizinindeki sezon özetleri de liste satırı kaynağıdır (katalog onları yeniden okurdu)
            paths.extend(reader.resolve(summary.path) for summary in reader.summary_files()
                         if summary.tournament_id == tournament_id and summary.season_id == season_id)
        paths.extend(reader.resolve(rel) for rel in leagues)
        if season_id is None:
            names = self._store.follows.leagues()
            for listed in reader.season_lists(names):
                if listed.tournament_id == tournament_id and listed.kind == "json":
                    paths.append(reader.resolve(listed.path))
        return sum(1 for path in paths if files.remove_tree(path))

    def _rebuild(self) -> bool:
        """Silmeden sonra katalog yerinde yeniden kurulur (uzlaştırma varlık satırlarını silmez; `Store.clear` gibi)."""
        try:
            self._store.catalog.rebuild(mode=_IN_PLACE)
        except (StoreError, sqlite3.Error, OSError) as e:
            logger.warning("The catalog of %s could not be rebuilt after a tournament clear; the next open "
                           "reconciles it: %s", self._store.data_dir, e)
            return False
        return True


__all__ = ["Purger", "TournamentClearReport"]
