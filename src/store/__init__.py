"""
Store: DATA_DIR altındaki her şeye dokunan tek paket (docs/design/01-storage.md, bölüm 2).

Paketin dışındaki kod yalnızca bu kökten içe aktarır (`from src.store import ...`), alt modüllerden
değil. Şimdilik yalnızca hata sınıfları dışa açık; Store cephesi ve veri tipleri sonraki adımlarda eklenir.
"""
from src.store.errors import (
    CatalogCorrupt,
    FollowExists,
    FollowManaged,
    LayoutError,
    LeaseHeld,
    PayloadCorrupt,
    PayloadMissing,
    SchemaTooNew,
    StoreBusy,
    StoreError,
    UnknownEvent,
)

__all__ = [
    "StoreError",
    "LeaseHeld",
    "StoreBusy",
    "UnknownEvent",
    "PayloadMissing",
    "PayloadCorrupt",
    "CatalogCorrupt",
    "SchemaTooNew",
    "LayoutError",
    "FollowExists",
    "FollowManaged",
]
