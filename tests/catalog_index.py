"""
Testin elle yazdığı eski düzen dosyalarını kataloğa alan yardımcılar.

Eski düzen yazıcılarının Store kancaları (`shadow_event`, `shadow_schedules`, `shadow_season_lists`,
`shadow_changes`, `shadow_cleared`; plan maddesi ST-11) ürün kodunda çağıranı kalmadığı için plan maddesi FX-15'te
kalktı. Eski düzende bir kayıt kuran testler (tests/legacy_writer.py, tests/detail_records.py ve birkaç test)
kancanın işini burada, deponun kendi dizinleyicisiyle yapar (`Catalog.index_event`, `sync_listings`): katalog,
testin yazdığını bir sonraki açılışı beklemeden görür.

Kanca gibi: katalog o an kullanılamıyorsa (açılışta eşitlenemedi) baştan eşitlenir; depolama hatası (dolu disk,
izin, meşgul veritabanı) yutulur, çünkü bu yardımcılar bir yazmanın `finally`'sinde de çağrılır ve testin asıl
hatasını örtmemelidir.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Union

from sofascore_scraper.store import StoreError, open_store
from sofascore_scraper.store.indexer import LISTING_CHANGES, LISTING_SCHEDULES, LISTING_SEASON_LISTS, canonical_id

PathLike = Union[str, "os.PathLike[str]"]
logger = logging.getLogger(__name__)

__all__ = ["LISTING_CHANGES", "LISTING_SCHEDULES", "LISTING_SEASON_LISTS", "index_event", "index_listings"]


def index_event(data_dir: PathLike, event_id: Union[int, str], directory: Optional[PathLike] = None) -> None:
    """Bir maçın eski düzen dizini yazıldı ya da değişti: maç diskten yeniden dizinlenir."""
    number = canonical_id(str(event_id))
    if number is None:
        return  # kurallı bir maç kimliği değil: yeniden kurma da böyle bir dizini maç saymaz
    try:
        store = open_store(data_dir)
        if not store._catalog_ready:
            store._sync_catalog()
            return
        paths: List[str] = []
        if directory is not None:
            try:
                paths.append(Path(os.path.abspath(os.fspath(directory))).relative_to(store.data_dir).as_posix())
            except ValueError:
                pass  # veri dizininin dışında: katalogdaki yola güvenilir
        store.catalog.index_event(number, paths=paths)
    except (StoreError, sqlite3.Error, OSError) as e:
        logger.debug("Event %s was not indexed: %s", number, e)


def index_listings(data_dir: PathLike, kinds: Iterable[str]) -> None:
    """Eski düzen listeleri (program sayfaları, sezon listeleri, değişiklik günlüğü) yeniden dizinlenir."""
    try:
        store = open_store(data_dir)
        if not store._catalog_ready:
            store._sync_catalog()
            return
        store.catalog.sync_listings(kinds)
    except (StoreError, sqlite3.Error, OSError) as e:
        logger.debug("Listings %s were not indexed: %s", list(kinds), e)
