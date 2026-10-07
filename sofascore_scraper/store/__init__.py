"""
Store: DATA_DIR altındaki her şeye dokunan tek paket (docs/design/01-storage.md, bölüm 2).

Paketin dışındaki kod yalnızca bu kökten içe aktarır (`from sofascore_scraper.store import ...`), alt modüllerden
değil. Dışa açık olanlar: hata sınıfları, cephe (`open_store`, `Store`, `StoreInfo`), takipler (`FollowStore`,
`apply_follows`), kilitler (`Lease`, `LeaseInfo`), iş deposu, olay akışları ve kataloğun okuma API'leri
(`EventStore`, `EntityStore`, `ChangeLog` ile sorgu ve sonuç türleri: `Ref`, `Scope`, `EventQuery`, `Page`,
`EventRow`, `SliceInfo`, ...) ve kataloğun yönetimi (`CatalogAdmin` ve raporları). Eski düzen yazıcılarının gölge
kip kancaları (`shadow_*`) plan maddesi FX-15'te kalktı. Maçların yazma API'si `EventStore.put` / `observe` / `reset_empty_markers` /
`delete` (sonucu `PutResult`) ve `ChangeLog.append`'tir; maç dışı varlıklarınki `EntityStore.put`.

Hata sınıfları dışındaki adlar ilk kullanımda yüklenir: kök, cepheyi (SQLite, katalog, türetme) içe
aktarmadan da alınabilmelidir: hata sınıflarını uygulamanın en alt katmanları kullanır ve bir alt modülü
(`sofascore_scraper.store.files`) içe aktarmak önce bu dosyayı çalıştırır.
"""
import importlib as _importlib
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any

from sofascore_scraper.store.errors import (
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
    from sofascore_scraper.store.api import Store, StoreInfo, open_store
    from sofascore_scraper.store.changes import ChangeLog, ChangeRow
    from sofascore_scraper.store.entities import EntityStore, ParticipantRow, SeasonRow, TournamentRow
    from sofascore_scraper.store.events import (
        EventQuery,
        EventRow,
        EventState,
        EventStore,
        MissingRow,
        Page,
        PutResult,
        Ref,
        Scope,
        SliceError,
        SliceInfo,
        TournamentSummary,
    )
    from sofascore_scraper.store.history import HistoryStore, Snapshot, SnapshotInfo
    from sofascore_scraper.store.follows import ApplyResult, Follow, FollowConflict, FollowSpec, FollowStore, apply_follows
    from sofascore_scraper.store.jobs import (
        DataOperationRunningError,
        JobRunningError,
        JobStore,
        JobStoreConflict,
        default_db_path,
        get_job_store,
    )
    from sofascore_scraper.store.lease import Lease, LeaseInfo
    from sofascore_scraper.store.streams import StreamBatch, StreamEvent, StreamHead, StreamLog, StreamRecord
    from sofascore_scraper.store.watch import WatchStateStore
    from sofascore_scraper.store.indexer import CatalogAdmin, IndexProblem, RebuildReport, ReconcileReport, SupersededDir
    from sofascore_scraper.store.verify import VerifyIssue, VerifyReport
    from sofascore_scraper.store.api import ClearReport
    from sofascore_scraper.store.backup import BackupInfo, BackupManager
    from sofascore_scraper.store.entities import CategoryRow, SportRow
    from sofascore_scraper.store.export import Exporter, ExportFile, ExportReport, ExportSkip
    from sofascore_scraper.store.backup import BackupCheck, BackupInvalid, BackupNotFound, RestoreRefused, RestoreReport
    from sofascore_scraper.store.streams import DEFAULT_PRUNE_MAX_AGE_SECONDS, DEFAULT_PRUNE_MAX_ROWS, SinkCursor
    from sofascore_scraper.store.migrate import MigrationIssue, MigrationPlan, MigrationProgress, MigrationReport, Migrator
    from sofascore_scraper.store.api import LAYOUT_VERSION
    from sofascore_scraper.store.catalog import CATALOG_SCHEMA
    from sofascore_scraper.store.state import load_migrations
    from sofascore_scraper.store.legacy import league_dir_name
    from sofascore_scraper.store.purge import Purger, TournamentClearReport

_LAZY = {
    "open_store": "sofascore_scraper.store.api",
    "Store": "sofascore_scraper.store.api",
    "StoreInfo": "sofascore_scraper.store.api",
    "EventStore": "sofascore_scraper.store.events",
    "EventQuery": "sofascore_scraper.store.events",
    "EventRow": "sofascore_scraper.store.events",
    "EventState": "sofascore_scraper.store.events",
    "MissingRow": "sofascore_scraper.store.events",
    "Page": "sofascore_scraper.store.events",
    "PutResult": "sofascore_scraper.store.events",
    "Ref": "sofascore_scraper.store.events",
    "Scope": "sofascore_scraper.store.events",
    "SliceError": "sofascore_scraper.store.events",
    "SliceInfo": "sofascore_scraper.store.events",
    "TournamentSummary": "sofascore_scraper.store.events",
    "EntityStore": "sofascore_scraper.store.entities",
    "TournamentRow": "sofascore_scraper.store.entities",
    "SeasonRow": "sofascore_scraper.store.entities",
    "ParticipantRow": "sofascore_scraper.store.entities",
    "ChangeLog": "sofascore_scraper.store.changes",
    "ChangeRow": "sofascore_scraper.store.changes",
    "HistoryStore": "sofascore_scraper.store.history",
    "Snapshot": "sofascore_scraper.store.history",
    "SnapshotInfo": "sofascore_scraper.store.history",
    "FollowStore": "sofascore_scraper.store.follows",
    "Follow": "sofascore_scraper.store.follows",
    "FollowSpec": "sofascore_scraper.store.follows",
    "FollowConflict": "sofascore_scraper.store.follows",
    "ApplyResult": "sofascore_scraper.store.follows",
    "apply_follows": "sofascore_scraper.store.follows",
    "Lease": "sofascore_scraper.store.lease",
    "LeaseInfo": "sofascore_scraper.store.lease",
    "JobStore": "sofascore_scraper.store.jobs",
    "JobStoreConflict": "sofascore_scraper.store.jobs",
    "JobRunningError": "sofascore_scraper.store.jobs",
    "DataOperationRunningError": "sofascore_scraper.store.jobs",
    "default_db_path": "sofascore_scraper.store.jobs",
    "get_job_store": "sofascore_scraper.store.jobs",
    "StreamLog": "sofascore_scraper.store.streams",
    "StreamEvent": "sofascore_scraper.store.streams",
    "StreamRecord": "sofascore_scraper.store.streams",
    "StreamBatch": "sofascore_scraper.store.streams",
    "StreamHead": "sofascore_scraper.store.streams",
    "WatchStateStore": "sofascore_scraper.store.watch",
    "CatalogAdmin": "sofascore_scraper.store.indexer",
    "RebuildReport": "sofascore_scraper.store.indexer",
    "ReconcileReport": "sofascore_scraper.store.indexer",
    "IndexProblem": "sofascore_scraper.store.indexer",
    "SupersededDir": "sofascore_scraper.store.indexer",
    "VerifyReport": "sofascore_scraper.store.verify",
    "VerifyIssue": "sofascore_scraper.store.verify",
    "ClearReport": "sofascore_scraper.store.api",
    "BackupManager": "sofascore_scraper.store.backup",
    "BackupInfo": "sofascore_scraper.store.backup",
    "CategoryRow": "sofascore_scraper.store.entities",
    "SportRow": "sofascore_scraper.store.entities",
    "ExportFile": "sofascore_scraper.store.export",
    "Exporter": "sofascore_scraper.store.export",
    "ExportReport": "sofascore_scraper.store.export",
    "ExportSkip": "sofascore_scraper.store.export",
    "BackupCheck": "sofascore_scraper.store.backup",
    "BackupInvalid": "sofascore_scraper.store.backup",
    "BackupNotFound": "sofascore_scraper.store.backup",
    "RestoreRefused": "sofascore_scraper.store.backup",
    "RestoreReport": "sofascore_scraper.store.backup",
    "SinkCursor": "sofascore_scraper.store.streams",
    "DEFAULT_PRUNE_MAX_AGE_SECONDS": "sofascore_scraper.store.streams",
    "DEFAULT_PRUNE_MAX_ROWS": "sofascore_scraper.store.streams",
    "Migrator": "sofascore_scraper.store.migrate",
    "MigrationPlan": "sofascore_scraper.store.migrate",
    "MigrationReport": "sofascore_scraper.store.migrate",
    "MigrationProgress": "sofascore_scraper.store.migrate",
    "MigrationIssue": "sofascore_scraper.store.migrate",
    "LAYOUT_VERSION": "sofascore_scraper.store.api",
    "CATALOG_SCHEMA": "sofascore_scraper.store.catalog",
    "load_migrations": "sofascore_scraper.store.state",
    "league_dir_name": "sofascore_scraper.store.legacy",
    "Purger": "sofascore_scraper.store.purge",
    "TournamentClearReport": "sofascore_scraper.store.purge",
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
    "PutResult",
    "Ref",
    "Scope",
    "SliceError",
    "SliceInfo",
    "TournamentSummary",
    "EntityStore",
    "TournamentRow",
    "SeasonRow",
    "ParticipantRow",
    "ChangeLog",
    "ChangeRow",
    "HistoryStore",
    "Snapshot",
    "SnapshotInfo",
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
    "CatalogAdmin",
    "RebuildReport",
    "ReconcileReport",
    "IndexProblem",
    "SupersededDir",
    "VerifyReport",
    "VerifyIssue",
    "ClearReport",
    "BackupManager",
    "BackupInfo",
    "CategoryRow",
    "SportRow",
    "ExportFile",
    "Exporter",
    "ExportReport",
    "ExportSkip",
    "BackupCheck",
    "BackupInvalid",
    "BackupNotFound",
    "RestoreRefused",
    "RestoreReport",
    "SinkCursor",
    "DEFAULT_PRUNE_MAX_AGE_SECONDS",
    "DEFAULT_PRUNE_MAX_ROWS",
    "Migrator",
    "MigrationPlan",
    "MigrationReport",
    "MigrationProgress",
    "MigrationIssue",
    "LAYOUT_VERSION",
    "CATALOG_SCHEMA",
    "load_migrations",
    "league_dir_name",
    "Purger",
    "TournamentClearReport",
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
