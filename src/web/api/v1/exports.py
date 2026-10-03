"""
API v1: dışa aktarmalar (docs/design/02-services.md bölüm 6; docs/design/05-web-ui.md 6.10 ve 7.3 G13; plan maddeleri
P21 ve SC-2).

    GET /api/v1/exports                    dışa aktarma işleri, en yeni önce (imleçle sayfalama)
    GET /api/v1/exports/{export_id}/download   dosyanın kendisi

Dışa aktarma bir iştir (`POST /jobs {"kind": "export", "spec": {...}}`); kaydı iş kaydıdır. Kaynağın kimliği işin
kimliğidir, alanları işin belirtiminden ve sonucundan gelir: veri kümesi, biçim, şema, profil, süzgeç, satır ve
bayt sayısı, dosya adı; normalleştirilmiş bir veri kümesinde kayıtların şema sürümü (`schema_version`: kayıt
sürümü taşımaz, onu taşıyan kap taşır; docs/design/04-schema-v1.md karar 19). Dosya
`DATA_DIR/exports/<iş kimliği>.<uzantı>`dadır; yalnızca başarıyla bitmiş işin dosyası indirilir.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from src.errors import NotFoundError, UsageError
from src.jobs.model import Job as JobSnapshot
from src.jobs.model import JobKind, JobState
from src.web import deps
from src.web.api.v1 import PageInfo
from src.web.api.v1.downloads import Download, download_responses
from src.web.api.v1.jobs import ExportFilter, export_request
from src.web.errors import error_responses

router = APIRouter(tags=["exports"])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
HISTORY_LIMIT = 500


class ExportRecord(BaseModel):
    """An export: the job that writes it and, once it has succeeded, its file."""

    id: str = Field(description="Id of the export, the id of its job.")
    job_id: str
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


def _record(job: JobSnapshot) -> ExportRecord:
    spec = dict(job.spec)
    result: Dict[str, Any] = dict((job.result or {}).get("export") or {})
    flt = spec.get("filter") or {}
    return ExportRecord(
        id=job.id, job_id=job.id, state=job.state, dataset=str(spec.get("dataset") or "events"),
        format=str(spec.get("format") or "csv"), schema=str(spec.get("schema") or "normalized"),
        profile=spec.get("profile"),
        filter=ExportFilter(sport=flt.get("sport"), tournament_ids=list(flt.get("tournament_ids") or ()),
                            season_ids=list(flt.get("season_ids") or ()), event_ids=list(flt.get("event_ids") or ()),
                            status_classes=list(flt.get("status_classes") or ()), from_=flt.get("from"),
                            to=flt.get("to")),
        created_at=job.created_at, finished_at=job.finished_at, rows=result.get("rows"), events=result.get("events"),
        bytes=result.get("bytes"), skipped=result.get("skipped"), file=result.get("file"),
        media_type=result.get("media_type"), schema_version=result.get("schema_version"),
        available=job.state == JobState.SUCCEEDED and bool(result),
    )


@router.get("/exports", response_model=ExportListResponse, operation_id="listExports", summary="List exports",
            responses=error_responses("invalid_request"))
def list_exports(
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None, max_length=64, description="`page.next_cursor` of the previous page."),
) -> ExportListResponse:
    """Export jobs of the data directory, newest first, running and failed ones included."""
    jobs = deps.job_manager().list(limit=HISTORY_LIMIT, kinds=[JobKind.EXPORT])
    start = 0
    if cursor:
        position = next((i for i, job in enumerate(jobs) if job.id == cursor), None)
        if position is None:
            raise UsageError("The cursor does not name an export of this list.", {"cursor": cursor})
        start = position + 1
    page = jobs[start:start + limit]
    more = start + limit < len(jobs)
    return ExportListResponse(data=[_record(job) for job in page],
                              page=PageInfo(limit=limit, next_cursor=page[-1].id if more and page else None))


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
def download_export(export_id: str = Path(max_length=64)) -> Download:
    """The file of a succeeded export. 404 for an unknown id, an export that has not succeeded or a deleted file."""
    from src.services.data_jobs import export_extension, export_path

    job = deps.job_manager().get(export_id)
    if job is None or job.kind != JobKind.EXPORT:
        raise NotFoundError("No export has this id.", {"export_id": export_id})
    record = _record(job)
    if not record.available:
        raise NotFoundError("The export has no file (it has not succeeded).",
                            {"export_id": export_id, "state": job.state.value})
    request = export_request(job.spec)
    path = export_path(str(deps.store().data_dir), job.id, request)
    return Download(path, filename=f"sofascore-export-{job.id}.{export_extension(request)}",
                    media_type=record.media_type or "application/octet-stream", details={"export_id": export_id})


__all__ = ["router"]
