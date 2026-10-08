"""
Web işinin gövdesini (`POST /api/v1/jobs`un `sync` / `fetch` işi, sofascore_scraper/web/api/v1/jobs.py `_run_sync`)
thread açmadan çalıştıran test yardımcısı.

3.0'a kadar testler aynı servisi 2.x'in `/api/fetch` işiyle (`web/api/legacy.py` `run_fetch_job`) çalıştırıyordu; o
yol 3.1'de kalktı (P30). İşin gövdesi o işin biçimindedir (`mode`, `league_id`, `selections`): testlerin
senaryoları değişmeden kalır.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from sofascore_scraper.jobs.manager import JobManager, local_origin
from sofascore_scraper.jobs.model import JobKind
from sofascore_scraper.services.sync import SyncSelection, SyncSpec
from sofascore_scraper.store import JobStore


def sync_spec(mode: str = "full", league_id: Optional[int] = None, selections: Any = None) -> SyncSpec:
    """İşin gövdesi → servis belirtimi; boş ve verilmemiş listeler aynıdır."""
    return SyncSpec(mode=mode, league_id=league_id, selections=tuple(  # type: ignore[arg-type]
        SyncSelection(league_id=s["league_id"], season_ids=tuple(s.get("season_ids") or ()),
                      match_ids=tuple(s.get("match_ids") or ()))
        for s in selections or ()
    ))


def run_sync_job(store: JobStore, payload: Mapping[str, Any], *,
                 body: Optional[Callable[[Any, SyncSpec], Any]] = None) -> Dict[str, Any]:
    """
    İşi çağıranın thread'inde çalıştırır ve iş deposunun son görüntüsünü döndürür. `body`: işin gövdesi (varsayılan
    v1'in `_run_sync`u; bağlamı testin kurduğu bağlamla değiştirmek için `services.context.build_context` yamalanır).
    """
    from sofascore_scraper.web.api.v1 import jobs as jobs_v1

    spec = sync_spec(**payload)
    run = body or jobs_v1._run_sync
    kind = JobKind.FETCH if spec.mode == "details" else JobKind.SYNC
    from sofascore_scraper.web import deps

    # `POST /api/v1/jobs` gibi: iş her ilerlediğinde iş deposunun görüntüsü yenilenir (testler o çağrıyı sayar);
    # gövdenin hatası iş kaydına yazılır ve, arka plan thread'inde olduğu gibi, çağırana çıkmaz
    try:
        JobManager(store).submit(kind, dict(payload), lambda handle: run(handle, spec), origin=local_origin("api"),
                                 background=False, phases=spec.job_phases, on_change=lambda: deps.refresh_job_mirror())
    except Exception:
        pass
    return store.snapshot()


__all__ = ["run_sync_job", "sync_spec"]
