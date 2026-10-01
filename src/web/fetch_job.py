"""Arka plan veri çekme işi: /api/fetch isteğinin seçtiği ligleri, sezonları ve maçları indirir.

Akışın kendisi (sezon listeleri → maç listeleri → maç detayları → CSV, devre kesici, detay planı)
src/services/sync.py'dedir. Bu modül web yüzünün bağdaştırıcısıdır: isteği SyncSpec'e çevirir, servise
iş deposuna yazan bir tutamaç verir ve sonucu işin bitiş durumuna ve kart metnine çevirir.

İlerleme `JobProgress` ile yapılandırılmış olarak yayınlanır; kart metinleri ön yüzde çevrilir.
SofaScore engellediğinde servis kalan lig/sezon/maç için istek atmayı bırakır ve iş nedenini karta
yazarak "Completed" biter. Kayıt diske yazılamıyorsa (disk dolu, izin yok) iş "Failed" olarak, nedeni
söyleyerek biter.
"""
from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any, Dict, Mapping

from src.exceptions import StorageError
from src.jobs.progress import JobProgress
from src.redact import redact_text
from src.services.context import build_context
from src.services.sync import SyncSelection, SyncService, SyncSpec
from src.web.routes.common import _job_store, _refresh_scraper_state, config_manager, logger

if TYPE_CHECKING:
    from src.web.routes.scrape import FetchRequest


def update_state(status: str, progress: int, task: str):
    finished = status in ("Completed", "Failed", "Cancelled")
    _job_store.update(
        status=status,
        progress=progress,
        current_task=task,
        append_log=f"[{status}] {task}",
        finished=finished,
    )
    _refresh_scraper_state()


def _note(task: str) -> None:
    """Ham görev metni: iş günlüğü ve eski istemciler için; yüzdeye dokunmaz."""
    _job_store.update(current_task=task, append_log=f"[Running] {task}")
    _refresh_scraper_state()


def _publish(fields: Dict[str, Any]) -> None:
    _job_store.update(**fields)
    _refresh_scraper_state()


def _spec_from_payload(payload: "FetchRequest") -> SyncSpec:
    """/api/fetch gövdesi → servis belirtimi. Boş ve verilmemiş listeler aynı anlama gelir."""
    return SyncSpec(
        mode=payload.mode,
        league_id=payload.league_id,
        selections=tuple(
            SyncSelection(
                league_id=s.league_id,
                season_ids=tuple(s.season_ids or ()),
                match_ids=tuple(s.match_ids or ()),
            )
            for s in payload.selections or ()
        ),
    )


def _summary(payload: "FetchRequest") -> str:
    """İşin ilk günlük satırındaki hedef: seçim sayısı, lig ID'si ya da "All Leagues"."""
    if payload.selections:
        return f"{len(payload.selections)} targeted selection(s)"
    return str(payload.league_id) if payload.league_id else "All Leagues"


class _StoreJobHandle:
    """Servisin gördüğü iş (src.services.sync.JobHandle): her çağrı iş deposuna yazılır."""

    def __init__(self, job_id: str, progress: JobProgress) -> None:
        self.id = job_id
        self.progress = progress

    def cancelled(self) -> bool:
        return _job_store.cancel_requested()

    def log(self, message: str) -> None:
        _note(message)
        if self.progress.phase == "export":
            # Sunucu konsolundaki satır (CSV aşaması başlarken); servis yazdırmadığı için burada
            print("--> Exporting to CSV...")

    def publish(self, fields: Mapping[str, Any]) -> None:
        _publish(dict(fields))


def run_fetch_job(job_id: str, payload: "FetchRequest") -> None:
    """/api/fetch işini çalıştırır (kendi thread'inde; bkz. routes/scrape.trigger_fetch)."""
    spec = _spec_from_payload(payload)
    tracker = JobProgress(list(spec.job_phases), _publish)
    # Servis tutamacın iptal sorusunu istek bağlamına kurar: işin her isteği (ve yeniden denemeler arasındaki
    # beklemeler) ona bakar, böylece "Durdur" geçerli sezonun ya da 2 dakikalık bir 403 geri çekilmesinin
    # sonunda değil, saniyenin kesri içinde etkili olur. 429/403 geri çekilmeleri de kartta geri sayım olur.
    handle = _StoreJobHandle(job_id, tracker)

    summary = _summary(payload)
    update_state("Running", 0, f"Starting fetch for {summary}")
    logger.info("Background fetch started. job_id=%s Target: %s", job_id, summary)

    try:
        result = SyncService(build_context(config_manager)).run(spec, handle=handle)

        if result.state == "cancelled":
            update_state("Cancelled", tracker.percent(), "Cancelled")
        else:
            from src.i18n import get_i18n

            empty_n = result.schedule_empty_seasons
            _job_store.update(result={"schedule_empty_seasons": empty_n, **result.progress})
            if result.breaker:
                update_state("Completed", 100, get_i18n().t("fetch_stopped_by_breaker", reason=result.breaker))
            elif empty_n > 0:
                update_state("Completed", 100, get_i18n().t("fetch_completed_with_warning"))
            else:
                update_state("Completed", 100, "Background Task Completed Successfully.")
            print("--> Background Task Completed Successfully.")
            logger.info("Background update and export completed.")

    except StorageError as e:
        # Kalıcı depolama hatası (disk dolu, izin yok): kalan maçlar da yazılamaz, iş durur
        from src.i18n import get_i18n

        message = get_i18n().t("storage_error_abort", path=e.path or "?", reason=e.detail or str(e))
        logger.error(f"Background fetch aborted, data could not be written: {e}")
        print(f"--> Background Task FAILED: {message}")
        _job_store.update(result={"error": "storage", "error_path": e.path, **tracker.result()})
        update_state("Failed", tracker.percent(), message)
    except Exception as e:
        # Hata metni iş kaydına yazılır ve API'den (durum, iş geçmişi, SSE) okunur: bir istek hatası
        # proxy adresini parolasıyla taşıyabilir, bu yüzden log satırları gibi maskelenir
        error_msg = redact_text(str(e))
        logger.error(f"Background update failed: {e}")
        logger.error(traceback.format_exc())
        print(f"--> Background Task FAILED: {error_msg}")
        update_state("Failed", tracker.percent(), f"Error: {error_msg}")
    finally:
        snap = _job_store.snapshot()
        if snap.get("is_running"):
            # Ensure job is closed if worker exited without terminal status
            if _job_store.cancel_requested():
                update_state("Cancelled", int(snap.get("progress") or 0), "Cancelled")
            else:
                update_state("Failed", int(snap.get("progress") or 0), "Interrupted")
        _refresh_scraper_state()
