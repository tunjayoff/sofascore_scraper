"""
Dışa aktarma işi (docs/design/02-services.md 2.7 ve 2.8; docs/design/05-web-ui.md 6.10; plan maddesi P21): API'nin
`export` işinin gövdesi. Yedek, temizleme ve katalog işleri doğrudan BackupService ve MaintenanceService'i
çağırır; bu modül yalnızca dışa aktarmanın isteğini, denetimini ve dosyasını tanımlar.

`run_export` bir iş yöneticisi işinin (`JobManager.submit`) içinde çalışır ve sonucunu iş kaydına yazılacak bir
sözlük olarak döndürür (`JobOutcome.result`).

Dışa aktarma üç biçim üretir:

  normalized       veri kümeleri `events`, `slices`, `changes`, şema v1 kayıtları olarak JSONL, CSV, Parquet ya da
                   SQLite (`ExportService.export_dataset`; plan maddesi SC-2). Parquet için `pyarrow` gerekir;
                   yoksa istek iş başlamadan `not_supported` ile reddedilir.
  legacy-wide-csv  2.x'in geniş CSV'si (`ExportService.legacy_table`; maç başına bir satır), Store'un satır
                   yazıcısıyla (UTF-8, `\\n` satır sonu, boş hücre null)
  raw              saklanan SofaScore yükleri, sıkıştırmasız JSONL (`Store.export.raw`; dilim başına bir satır:
                   `{"event_id", "key", "sub", "fetched_at", "payload"}`)

Dışa aktarma dosyası `DATA_DIR/exports/`a okunur bir adla yazılır (plan maddesi FX-19; `export_name`):
`<lig ya da veri kümesi>_<UTC tarih>_<iş kimliğinin son 8 harfi>.<uzantı>`, ör.
`premier-league_2026-10-06_x7k2m9qa.csv`; adı işin sonucundaki `file` alanı taşır. FX-19'dan önceki işlerin
dosyası `<iş kimliği>.<uzantı>`dır. Aynı işin dosyası yeniden yazılabilir (`overwrite`). `ssc export`'un
`--out` verilmeyen dosyası da aynı etiketi ve UTC tarihi taşır, iş kimliği yerine UTC saatle (FX-34;
`local_export_name`): `premier-league_2026-10-06_142530.jsonl`.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from sofascore_scraper.errors import UsageError
from sofascore_scraper.logger import get_logger

if TYPE_CHECKING:
    from sofascore_scraper.services.export import DatasetSpec
    from sofascore_scraper.store import Store

logger = get_logger("DataJobs")

EXPORTS_DIR = "exports"  # sofascore_scraper/store/layout.py EXPORTS_DIR
LEGACY_WIDE_CSV = "legacy-wide-csv"
DATASETS: Tuple[str, ...] = ("events", "slices", "changes", "odds", "standings")
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
    verir). schema `normalized`: şema v1 kayıtları (`events`, `slices`, `changes`; JSONL, CSV, Parquet, SQLite).
    Süzgeçler birlikte (VE) uygulanır; geniş CSV yalnızca turnuva ve maç süzgeçlerini bilir.

    status_classes: durum sınıfları. start_from / start_to: ISO 8601 tarih ya da tarih-saat (UTC; yalnızca
    tarih: günün başı / sonu); `changes` için kaydın zamanı, ötekiler için maçın başlangıcı.
    team_ids / player_ids: katılımcı süzgeci (B1; sofascore_scraper/services/export.py): bu takımlardan ya da
    oyunculardan birinin maçları. Geniş CSV de bilir.
    """

    dataset: str = "events"
    format: str = "csv"
    schema: str = "normalized"
    profile: Optional[str] = None
    sport: Optional[str] = None
    tournament_ids: Tuple[int, ...] = ()
    season_ids: Tuple[int, ...] = ()
    event_ids: Tuple[int, ...] = ()
    status_classes: Tuple[str, ...] = ()
    start_from: Optional[str] = None
    start_to: Optional[str] = None
    team_ids: Tuple[int, ...] = ()
    player_ids: Tuple[int, ...] = ()


def check_export(req: ExportRequest) -> None:
    """
    İsteğin üretilebilir olup olmadığı, hiçbir şey okumadan: biçim dışı birleşim `invalid_request`, kurulumda
    yapılamayan (Parquet, `pyarrow` yok) `not_supported`.
    """
    if req.dataset not in DATASETS or req.format not in FORMATS or req.schema not in SCHEMAS:
        raise UsageError("Unknown dataset, format or schema.",
                         {"dataset": req.dataset, "format": req.format, "schema": req.schema})
    if req.profile is not None:
        if req.profile != LEGACY_WIDE_CSV:
            raise UsageError("Unknown export profile.", {"profile": req.profile})
        if (req.dataset, req.format) != ("events", "csv"):
            raise UsageError("The legacy-wide-csv profile is the events dataset as CSV.",
                             {"dataset": req.dataset, "format": req.format})
        if (req.sport is not None or req.season_ids or req.status_classes or req.start_from is not None
                or req.start_to is not None):
            raise UsageError("The legacy-wide-csv profile filters by tournament, event, team and player only.",
                             {"filter": ["sport", "season_ids", "status_classes", "from", "to"]})
        return
    if req.schema == "raw" and req.format != "jsonl":
        raise UsageError("A raw export is written as JSONL.", {"format": req.format})
    from sofascore_scraper.services.export import check_dataset

    check_dataset(dataset_spec(req))


def dataset_spec(req: ExportRequest) -> "DatasetSpec":
    """İstek → servisin veri kümesi belirtimi. Okunamayan zaman metni `invalid_request`."""
    from sofascore_scraper.services.export import DatasetFilter, DatasetSpec, parse_moment

    moments: Dict[str, Optional[float]] = {}
    for name, text, end in (("from", req.start_from, False), ("to", req.start_to, True)):
        try:
            moments[name] = None if text is None else parse_moment(text, end=end)
        except ValueError:
            raise UsageError("Expected an ISO 8601 date or date-time.", {"filter": name, "value": text}) from None
    return DatasetSpec(
        dataset=req.dataset, format=req.format, schema=req.schema,
        filter=DatasetFilter(sport=req.sport, tournament_ids=tuple(req.tournament_ids),
                             season_ids=tuple(req.season_ids), event_ids=tuple(req.event_ids),
                             status_classes=tuple(req.status_classes), start_from=moments["from"],
                             start_to=moments["to"], team_ids=tuple(req.team_ids),
                             player_ids=tuple(req.player_ids)))


def export_extension(req: ExportRequest) -> str:
    return "jsonl" if req.schema == "raw" and req.profile is None else req.format


def export_path(data_dir: str, job_id: str, req: ExportRequest, name: Optional[str] = None) -> str:
    """
    İşin dışa aktarma dosyası: `DATA_DIR/exports/<name>`; name verilmezse FX-19'dan önceki ad
    `<iş kimliği>.<uzantı>` (eski işlerin dosyası).
    """
    return os.path.join(os.path.abspath(data_dir), EXPORTS_DIR, name or f"{job_id}.{export_extension(req)}")


_SLUG_DROP = re.compile(r"[^a-z0-9]+")
LABEL_MAX = 40


def slug(text: str) -> str:
    """Dosya adına giren parça: aksanlar çıkarılır, küçük harf, harf ve rakam dışı `-`, en çok 40 karakter."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return _SLUG_DROP.sub("-", plain).strip("-")[:LABEL_MAX].strip("-")


def export_label(req: ExportRequest, tournament_name: Optional[str] = None) -> str:
    """
    Dosya adının baş parçası: tek bir turnuva (ya da turnuvasız tek bir takım veya oyuncu) süzülmüşse onun adı
    (biliniyorsa), yoksa veri kümesi; ham dışa aktarmada `-raw`, 2.x'in geniş CSV'sinde `-wide` eklenir.
    """
    base = slug(tournament_name or "") if tournament_name else ""
    base = base or req.dataset
    if req.profile == LEGACY_WIDE_CSV:
        return f"{base}-wide"
    return f"{base}-raw" if req.schema == "raw" else base


def export_subject(store: "Store", req: ExportRequest) -> Optional[str]:
    """
    Dosya adının konusu (`export_label`'a verilen ad): tek bir turnuva süzülmüşse onun adı, takip tablosundan,
    yoksa katalogdan. Turnuva süzülmemiş, tek bir takım ya da tek bir oyuncu süzülmüşse (B1) onun takibinin adı,
    takımın takibi yoksa katalogdaki adı. Bilinmiyorsa None (etiket veri kümesidir).
    """
    name: Optional[str] = None
    if len(req.tournament_ids) == 1:
        tid = int(req.tournament_ids[0])
        followed = store.follows.get("tournament", tid)
        if followed is not None:
            name = followed.name
        else:
            row = store.entities.tournament(tid)
            name = row.name if row is not None else None
    elif not req.tournament_ids and len(req.team_ids) + len(req.player_ids) == 1:
        kind, entity_id = ("team", int(req.team_ids[0])) if req.team_ids else ("player", int(req.player_ids[0]))
        follow = store.follows.get(kind, entity_id)
        if follow is not None:
            name = follow.name
        elif kind == "team":
            rows = store.entities.participants(ids=(entity_id,), limit=1)
            name = rows[0].name if rows else None
    return name


def _utc_moment(now: Optional[float]) -> dt.datetime:
    return dt.datetime.fromtimestamp(now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp(),
                                     dt.timezone.utc)


def export_name(store: "Store", job_id: str, req: ExportRequest, *, now: Optional[float] = None) -> str:
    """
    Okunur dosya adı (FX-19): `<etiket>_<UTC tarih>_<iş kimliğinin son 8 harfi>.<uzantı>`; etiketin adı
    `export_subject`'tendir.
    """
    short = "".join(ch for ch in job_id.lower() if ch.isalnum())[-8:] or "export"
    label = export_label(req, export_subject(store, req))
    return f"{label}_{_utc_moment(now):%Y-%m-%d}_{short}.{export_extension(req)}"


def local_export_name(store: "Store", req: ExportRequest, *, now: Optional[float] = None) -> str:
    """
    `ssc export`'un `--out` verilmeyen dosyasının adı (FX-34): işinkiyle aynı etiket ve UTC tarih, iş kimliği
    yerine UTC saat: `<etiket>_<UTC tarih>_<UTC saat, %H%M%S>.<uzantı>`, ör. `premier-league_2026-10-06_142530.jsonl`.
    """
    label = export_label(req, export_subject(store, req))
    return f"{label}_{_utc_moment(now):%Y-%m-%d_%H%M%S}.{export_extension(req)}"


def run_export(store: "Store", req: ExportRequest, dest: str) -> Dict[str, Any]:
    """
    Dışa aktarmayı `dest`'e yazar ve sonucunu döndürür (satır, maç, bayt, atlanan dilim sayısı; normalleştirilmiş
    veri kümesinde şema sürümü).
    """
    from sofascore_scraper.services.export import ExportService, ExportSpec

    check_export(req)
    if req.profile == LEGACY_WIDE_CSV:
        table = ExportService(store).legacy_table(
            ExportSpec(tournament_ids=tuple(req.tournament_ids), event_ids=tuple(req.event_ids),
                       team_ids=tuple(req.team_ids), player_ids=tuple(req.player_ids)))
        report = store.export.rows(table.rows, table.columns, dest, "csv", overwrite=True)
        result: Dict[str, Any] = {"rows": report.items, "events": report.items, "bytes": report.bytes, "skipped": 0}
    else:
        written = ExportService(store).export_dataset(dataset_spec(req), dest, overwrite=True)
        result = {"rows": written.rows, "events": written.events, "bytes": written.bytes,
                  "skipped": len(written.skipped)}
        if written.schema_version is not None:
            result["schema_version"] = written.schema_version
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
    "dataset_spec",
    "export_extension",
    "export_label",
    "export_name",
    "export_path",
    "export_subject",
    "local_export_name",
    "run_export",
]
