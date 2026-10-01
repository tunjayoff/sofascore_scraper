"""
Store: DATA_DIR altındaki her şeye dokunan tek paket (docs/design/01-storage.md, bölüm 2).

Paketin dışındaki kod yalnızca bu kökten içe aktarır (`from src.store import ...`), alt modüllerden
değil. Dışa açık olanlar: hata sınıfları, cephe (`open_store`, `Store`, `StoreInfo`), takipler (`FollowStore`,
`apply_follows`), kilitler (`Lease`, `LeaseInfo`), iş deposu, olay akışları ve kataloğun okuma API'leri
(`EventStore` ile sorgu ve sonuç türleri: `Ref`, `Scope`, `EventQuery`, `Page`, `EventRow`, `SliceInfo`,
...). Öteki okuma API'leri ve yazma API'leri sonraki adımlarda eklenir.

Hata sınıfları dışındaki adlar ilk kullanımda yüklenir: kök, cepheyi (SQLite, katalog, türetme) içe
aktarmadan da alınabilmelidir, çünkü `src.store.files` gibi alt modülleri uygulamanın en alt katmanları
kullanır (src/fsutil.py) ve bir alt modülü içe aktarmak önce bu dosyayı çalıştırır.
"""
import importlib as _importlib
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any

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

if _TYPE_CHECKING:  # tür denetleyicileri ve API anlık görüntüsü adları buradan bulur
    from src.store.api import Store, StoreInfo, open_store
    from src.store.events import (
        EventQuery,
        EventRow,
        EventState,
        EventStore,
        MissingRow,
        Page,
        Ref,
        Scope,
        SliceError,
        SliceInfo,
        TournamentSummary,
    )
    from src.store.follows import ApplyResult, Follow, FollowConflict, FollowSpec, FollowStore, apply_follows
    from src.store.jobs import (
        DataOperationRunningError,
        JobRunningError,
        JobStore,
        JobStoreConflict,
        default_db_path,
        get_job_store,
    )
    from src.store.lease import Lease, LeaseInfo
    from src.store.streams import StreamBatch, StreamEvent, StreamHead, StreamLog, StreamRecord
    from src.store.watch import WatchStateStore

_LAZY = {
    "open_store": "src.store.api",
    "Store": "src.store.api",
    "StoreInfo": "src.store.api",
    "EventStore": "src.store.events",
    "EventQuery": "src.store.events",
    "EventRow": "src.store.events",
    "EventState": "src.store.events",
    "MissingRow": "src.store.events",
    "Page": "src.store.events",
    "Ref": "src.store.events",
    "Scope": "src.store.events",
    "SliceError": "src.store.events",
    "SliceInfo": "src.store.events",
    "TournamentSummary": "src.store.events",
    "FollowStore": "src.store.follows",
    "Follow": "src.store.follows",
    "FollowSpec": "src.store.follows",
    "FollowConflict": "src.store.follows",
    "ApplyResult": "src.store.follows",
    "apply_follows": "src.store.follows",
    "Lease": "src.store.lease",
    "LeaseInfo": "src.store.lease",
    "JobStore": "src.store.jobs",
    "JobStoreConflict": "src.store.jobs",
    "JobRunningError": "src.store.jobs",
    "DataOperationRunningError": "src.store.jobs",
    "default_db_path": "src.store.jobs",
    "get_job_store": "src.store.jobs",
    "StreamLog": "src.store.streams",
    "StreamEvent": "src.store.streams",
    "StreamRecord": "src.store.streams",
    "StreamBatch": "src.store.streams",
    "StreamHead": "src.store.streams",
    "WatchStateStore": "src.store.watch",
}


def __getattr__(name: str) -> _Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "open_store",
    "Store",
    "StoreInfo",
    "EventStore",
    "EventQuery",
    "EventRow",
    "EventState",
    "MissingRow",
    "Page",
    "Ref",
    "Scope",
    "SliceError",
    "SliceInfo",
    "TournamentSummary",
    "FollowStore",
    "Follow",
    "FollowSpec",
    "FollowConflict",
    "ApplyResult",
    "apply_follows",
    "Lease",
    "LeaseInfo",
    "JobStore",
    "JobStoreConflict",
    "JobRunningError",
    "DataOperationRunningError",
    "default_db_path",
    "get_job_store",
    "StreamLog",
    "StreamEvent",
    "StreamRecord",
    "StreamBatch",
    "StreamHead",
    "WatchStateStore",
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
