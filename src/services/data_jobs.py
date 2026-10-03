"""
Dışa aktarma işi (docs/design/02-services.md 2.7 ve 2.8; docs/design/05-web-ui.md 6.10; plan maddesi P21): API'nin
`export` işinin gövdesi. Yedek, temizleme ve katalog işleri doğrudan BackupService ve MaintenanceService'i
çağırır; bu modül yalnızca dışa aktarmanın isteğini, denetimini ve dosyasını tanımlar.

`run_export` bir iş yöneticisi işinin (`JobManager.submit`) içinde çalışır ve sonucunu iş kaydına yazılacak bir
sözlük olarak döndürür (`JobOutcome.result`).

Dışa aktarma bugün iki biçim üretir:

  legacy-wide-csv  2.x'in geniş CSV'si (`ExportService.legacy_table`; maç başına bir satır), Store'un satır
                   yazıcısıyla (UTF-8, `\\n` satır sonu, boş hücre null)
  raw              saklanan SofaScore yükleri, sıkıştırmasız JSONL (`Store.export.raw`; dilim başına bir satır:
                   `{"event_id", "key", "sub", "fetched_at", "payload"}`)

Normalleştirilmiş veri kümeleri (şema v1 satırları; JSONL, CSV, Parquet, SQLite) plan maddesi SC-2'nindir:
istenirse `not_supported`. Dışa aktarma dosyası `DATA_DIR/exports/<iş kimliği>.<uzantı>`ya yazılır; aynı işin
dosyası yeniden yazılabilir (`overwrite`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, Tuple

from src.errors import NotSupportedError, UsageError
from src.logger import get_logger

if TYPE_CHECKING:
    from src.store import Store

logger = get_logger("DataJobs")

EXPORTS_DIR = "exports"  # src/store/layout.py EXPORTS_DIR
LEGACY_WIDE_CSV = "legacy-wide-csv"
DATASETS: Tuple[str, ...] = ("events", "slices")
FORMATS: Tuple[str, ...] = ("csv", "jsonl", "parquet", "sqlite")
SCHEMAS: Tuple[str, ...] = ("normalized", "raw")
MEDIA_TYPES: Dict[str, str] = {
    "csv": "text/csv", "jsonl": "application/x-ndjson", "parquet": "application/vnd.apache.parquet",
    "sqlite": "application/vnd.sqlite3",
}
EVENT_KEY = "event"


@dataclass(frozen=True)
class ExportRequest:
    """
    Ne dışa aktarılacak. profile `legacy-wide-csv`: 2.x'in geniş CSV'si (dataset `events`, format `csv`).
    schema `raw`: saklanan yükler (format `jsonl`; dataset `events` yalnızca olay yükünü, `slices` her dilimi
    verir). Süzgeçler birlikte (VE) uygulanır; geniş CSV yalnızca turnuva ve maç süzgeçlerini bilir.
    """

    dataset: str = "events"
    format: str = "csv"
    schema: str = "normalized"
    profile: Optional[str] = None
    sport: Optional[str] = None
    tournament_ids: Tuple[int, ...] = ()
    season_ids: Tuple[int, ...] = ()
    event_ids: Tuple[int, ...] = ()


def check_export(req: ExportRequest) -> None:
    """İsteğin bugün üretilebilir olup olmadığı: biçim dışı birleşim `invalid_request`, SC-2'ninki `not_supported`."""
    if req.dataset not in DATASETS or req.format not in FORMATS or req.schema not in SCHEMAS:
        raise UsageError("Unknown dataset, format or schema.",
                         {"dataset": req.dataset, "format": req.format, "schema": req.schema})
    if req.profile is not None:
        if req.profile != LEGACY_WIDE_CSV:
            raise UsageError("Unknown export profile.", {"profile": req.profile})
        if (req.dataset, req.format) != ("events", "csv"):
            raise UsageError("The legacy-wide-csv profile is the events dataset as CSV.",
                             {"dataset": req.dataset, "format": req.format})
        if req.sport is not None or req.season_ids:
            raise UsageError("The legacy-wide-csv profile filters by tournament and event only.",
                             {"filter": ["sport", "season_ids"]})
        return
    if req.schema == "raw":
        if req.format != "jsonl":
            raise UsageError("A raw export is written as JSONL.", {"format": req.format})
        return
    raise NotSupportedError(
        "Normalized datasets cannot be exported yet; use the legacy-wide-csv profile or the raw schema.",
        {"dataset": req.dataset, "format": req.format, "schema": req.schema},
    )


def export_extension(req: ExportRequest) -> str:
    return "jsonl" if req.schema == "raw" and req.profile is None else req.format


def export_path(data_dir: str, job_id: str, req: ExportRequest) -> str:
    """İşin dışa aktarma dosyası: `DATA_DIR/exports/<iş kimliği>.<uzantı>`."""
    return os.path.join(os.path.abspath(data_dir), EXPORTS_DIR, f"{job_id}.{export_extension(req)}")


def run_export(store: "Store", req: ExportRequest, dest: str) -> Dict[str, Any]:
    """Dışa aktarmayı `dest`'e yazar ve sonucunu döndürür (satır, maç, bayt, atlanan dilim sayısı)."""
    from src.services.export import ExportService, ExportSpec
    from src.store import EventQuery, Scope

    check_export(req)
    if req.profile == LEGACY_WIDE_CSV:
        table = ExportService(store).legacy_table(
            ExportSpec(tournament_ids=tuple(req.tournament_ids), event_ids=tuple(req.event_ids)))
        report = store.export.rows(table.rows, table.columns, dest, "csv", overwrite=True)
        result = {"rows": report.items, "events": report.items, "bytes": report.bytes, "skipped": 0}
    else:
        query = EventQuery(scope=Scope(sport=req.sport, tournament_ids=tuple(req.tournament_ids),
                                       season_ids=tuple(req.season_ids), event_ids=tuple(req.event_ids)),
                           has_details=True, sort="start_asc")
        keys: Optional[Sequence[str]] = (EVENT_KEY,) if req.dataset == "events" else None
        report = store.export.raw(query, dest, keys=keys, fmt="jsonl", overwrite=True)
        result = {"rows": report.items, "events": report.events, "bytes": report.bytes,
                  "skipped": len(report.skipped)}
    logger.info("Export written: %s (%d rows, %d bytes)", dest, result["rows"], result["bytes"])
    return {**result, "path": dest, "file": os.path.basename(dest), "media_type": MEDIA_TYPES[export_extension(req)]}


__all__ = [
    "DATASETS",
    "EXPORTS_DIR",
    "ExportRequest",
    "FORMATS",
    "LEGACY_WIDE_CSV",
    "MEDIA_TYPES",
    "SCHEMAS",
    "check_export",
    "export_extension",
    "export_path",
    "run_export",
]
