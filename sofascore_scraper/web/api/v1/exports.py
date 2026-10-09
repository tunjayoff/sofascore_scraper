"""
API v1: dışa aktarmalar (docs/design/02-services.md bölüm 6; docs/design/05-web-ui.md 6.10 ve 7.3 G13; plan maddeleri
P21 ve SC-2).

    GET /api/v1/exports                    dışa aktarma işleri, en yeni önce (imleçle sayfalama)
    GET /api/v1/exports/{export_id}/download   dosyanın kendisi

Dışa aktarma bir iştir (`POST /jobs {"kind": "export", "spec": {...}}`); kaydı iş kaydıdır. Kaynağın kimliği işin
kimliğidir, alanları işin belirtiminden ve sonucundan gelir: veri kümesi, biçim, şema, profil, süzgeç, satır ve
bayt sayısı, dosya adı; normalleştirilmiş bir veri kümesinde kayıtların şema sürümü (`schema_version`: kayıt
sürümü taşımaz, onu taşıyan kap taşır; docs/design/04-schema-v1.md karar 19). Dosya `DATA_DIR/exports/`dadır,
adı işin sonucundaki `file` alanıdır (FX-19'dan beri okunur bir ad; önceki işlerde `<iş kimliği>.<uzantı>`);
yalnızca başarıyla bitmiş işin dosyası indirilir.

`exports/` dizininde hiçbir işin sahiplenmediği dosyalar (`ssc export`'un yazdıkları; FX-19) da listelenir:
kimliği `file:<ad>`dır, `source` "file", `job_id` null; veri kümesi ve biçim adından ve uzantısından okunur
(bilinmiyorsa "unknown"). Dosyalar Store'dan okunur (`store.export.files`, `file_path`).
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from sofascore_scraper.errors import NotFoundError, UsageError
from sofascore_scraper.jobs.model import Job as JobSnapshot
from sofascore_scraper.jobs.model import JobKind, JobState
from sofascore_scraper.web import deps
from sofascore_scraper.web.api.v1 import PageInfo
from sofascore_scraper.web.api.v1.downloads import Download, download_responses
from sofascore_scraper.web.api.v1.jobs import ExportFilter, export_request
from sofascore_scraper.web.errors import error_responses

router = APIRouter(tags=["exports"])

FILE_PREFIX = "file:"  # `exports/`taki işsiz dosyanın kimlik öneki (FX-19)

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
HISTORY_LIMIT = 500


class ExportRecord(BaseModel):
    """An export: the job that writes it and, once it has succeeded, its file; or a file of `exports/` no job
    wrote (`ssc export`)."""

    id: str = Field(description="Id of the export: the id of its job, or `file:<name>` for a file no job wrote.")
    source: Literal["job", "file"] = Field(
        default="job", description="job: an export job; file: a file in `exports/` that no job wrote (`ssc export`).",
    )
    job_id: Optional[str] = Field(default=None, description="Null for a file no job wrote.")
    state: JobState = Field(description="State of the job; the file can be downloaded when `succeeded`.")
    dataset: str
    format: str
    schema_: str = Field(alias="schema")
    profile: Optional[str] = None
    filter: ExportFilter
    created_at: Optional[str] = Field(default=None, description="ISO-8601, UTC.")
    finished_at: Optional[str] = Field(default=None, description="ISO-8601, UTC.")
    rows: Optional[int] = Field(default=None, description="Records (rows or lines) written; for a raw export the "
                                                          "payloads written.")
    events: Optional[int] = Field(default=None, description="Events with at least one exported payload.")
    bytes: Optional[int] = None
    skipped: Optional[int] = Field(default=None, description="Payloads that could not be read and were left out.")
    file: Optional[str] = Field(default=None, description="File name in the data directory's `exports/`.")
    media_type: Optional[str] = None
    schema_version: Optional[int] = Field(
        default=None, description="Version of the data schema of the records; null for a raw export and for the "
                                  "legacy-wide-csv profile.")
    available: bool = Field(description="The file can be downloaded.")

    model_config = {"populate_by_name": True}


class ExportListResponse(BaseModel):
    data: List[ExportRecord]
    page: PageInfo


_KNOWN_FORMATS = {"csv": "csv", "jsonl": "jsonl", "parquet": "parquet", "sqlite": "sqlite", "db": "sqlite"}


def _file_record(name: str, size: int, modified_at: float) -> ExportRecord:
    """`exports/`taki bir işin olmayan dosyası: veri kümesi adın ilk parçasından, biçim uzantıdan."""
    from sofascore_scraper.services.data_jobs import DATASETS, MEDIA_TYPES

    stem, _, ext = name.rpartition(".")
    fmt = _KNOWN_FORMATS.get(ext.lower(), "unknown")
    head = (stem or name).split("_", 1)[0].lower()
    dataset = head if head in DATASETS else "unknown"
    when = dt.datetime.fromtimestamp(modified_at, dt.timezone.utc).isoformat(timespec="seconds")
    return ExportRecord(
        id=f"{FILE_PREFIX}{name}", source="file", job_id=None, state=JobState.SUCCEEDED, dataset=dataset,
        format=fmt, schema="normalized" if dataset != "unknown" else "unknown", profile=None, filter=ExportFilter(),
        created_at=when, finished_at=when, bytes=size, file=name,
        media_type=MEDIA_TYPES.get(fmt, "application/octet-stream"), available=True,
    )


def _sort_time(text: Optional[str]) -> float:
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp() if text else 0.0
    except ValueError:
        return 0.0


def _claimed(jobs: List[JobSnapshot]) -> Set[str]:
    """İşlerin dosya adları: sonuçtaki `file`, yoksa FX-19'dan önceki `<iş kimliği>.<uzantı>`."""
    from sofascore_scraper.services.data_jobs import export_extension

    names: Set[str] = set()
    for job in jobs:
        result = dict((job.result or {}).get("export") or {})
        if result.get("file"):
            names.add(str(result["file"]))
        names.add(f"{job.id}.{export_extension(export_request(job.spec))}")
    return names


def _record(job: JobSnapshot) -> ExportRecord:
    spec = dict(job.spec)
    result: Dict[str, Any] = dict((job.result or {}).get("export") or {})
    flt = spec.get("filter") or {}
    return ExportRecord(
        id=job.id, source="job", job_id=job.id, state=job.state, dataset=str(spec.get("dataset") or "events"),
        format=str(spec.get("format") or "csv"), schema=str(spec.get("schema") or "normalized"),
        profile=spec.get("profile"),
        filter=ExportFilter(sport=flt.get("sport"), tournament_ids=list(flt.get("tournament_ids") or ()),
                            season_ids=list(flt.get("season_ids") or ()), event_ids=list(flt.get("event_ids") or ()),
                            status_classes=list(flt.get("status_classes") or ()), from_=flt.get("from"),
                            to=flt.get("to"), team_ids=list(flt.get("team_ids") or ()),
                            player_ids=list(flt.get("player_ids") or ())),
        created_at=job.created_at, finished_at=job.finished_at, rows=result.get("rows"), events=result.get("events"),
        bytes=result.get("bytes"), skipped=result.get("skipped"), file=result.get("file"),
        media_type=result.get("media_type"), schema_version=result.get("schema_version"),
        available=job.state == JobState.SUCCEEDED and bool(result),
    )


@router.get("/exports", response_model=ExportListResponse, operation_id="listExports", summary="List exports",
            responses=error_responses("invalid_request"))
def list_exports(
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None, max_length=256, description="`page.next_cursor` of the previous page."),
) -> ExportListResponse:
    """
    Export jobs of the data directory, running and failed ones included, and the files of `exports/` that no job
    wrote (`ssc export`; `source: "file"`), newest first.
    """
    jobs = deps.job_manager().list(limit=HISTORY_LIMIT, kinds=[JobKind.EXPORT])
    claimed = _claimed(jobs)
    entries: List[Tuple[float, ExportRecord]] = [(_sort_time(job.created_at), _record(job)) for job in jobs]
    entries += [(found.modified_at, _file_record(found.name, found.size, found.modified_at))
                for found in deps.store().export.files() if found.name not in claimed]
    records = [record for _when, record in sorted(entries, key=lambda pair: -pair[0])]
    start = 0
    if cursor:
        position = next((i for i, record in enumerate(records) if record.id == cursor), None)
        if position is None:
            raise UsageError("The cursor does not name an export of this list.", {"cursor": cursor})
        start = position + 1
    page = records[start:start + limit]
    more = start + limit < len(records)
    return ExportListResponse(data=page, page=PageInfo(limit=limit, next_cursor=page[-1].id if more and page else None))


@router.get(
    "/exports/{export_id}/download",
    operation_id="downloadExport",
    summary="Download an export",
    response_class=Download,
    status_code=200,
    responses={**download_responses("application/octet-stream",
                                         "The export file (CSV, JSONL, Parquet or SQLite)."),
               **error_responses("not_found")},
)
def download_export(export_id: str = Path(max_length=256)) -> Download:
    """
    The file of a succeeded export, or (`file:<name>`) a file of `exports/` that no job wrote. 404 for an unknown
    id, an export that has not succeeded or a deleted file.
    """
    from sofascore_scraper.services.data_jobs import export_path

    if export_id.startswith(FILE_PREFIX):
        name = export_id[len(FILE_PREFIX):]
        found = deps.store().export.file_path(name)
        if found is None:
            raise NotFoundError("No export has this id.", {"export_id": export_id})
        info = _file_record(name, 0, 0.0)
        return Download(found, filename=name, media_type=info.media_type or "application/octet-stream",
                        details={"export_id": export_id})
    job = deps.job_manager().get(export_id)
    if job is None or job.kind != JobKind.EXPORT:
        raise NotFoundError("No export has this id.", {"export_id": export_id})
    record = _record(job)
    if not record.available:
        raise NotFoundError("The export has no file (it has not succeeded).",
                            {"export_id": export_id, "state": job.state.value})
    request = export_request(job.spec)
    named = deps.store().export.file_path(record.file) if record.file else None
    path = named or export_path(str(deps.store().data_dir), job.id, request)
    return Download(path, filename=os.path.basename(path),
                    media_type=record.media_type or "application/octet-stream", details={"export_id": export_id})


__all__ = ["router"]
