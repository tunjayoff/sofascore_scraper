"""Arka plan veri çekme işi: /api/fetch isteğinin seçtiği ligleri, sezonları ve maçları indirir.

Akışın kendisi (sezon listeleri → maç listeleri → maç detayları → CSV, devre kesici, detay planı)
src/services/sync.py'dedir; işi yürüten iş yöneticisidir (src/jobs/manager.py: tutamaç, iptal bayrağının
satırdan okunması, kalp atışı, olay günlüğü, bitiş durumu). Bu modül web yüzünün bağdaştırıcısıdır: isteği
SyncSpec'e çevirir, işi sürecin iş deposu üzerinde yürütür ve servisin sonucunu işin bitişine ve kart metnine
çevirir.

İlerleme `JobProgress` ile yapılandırılmış olarak yayınlanır; kart metinleri ön yüzde çevrilir.
SofaScore engellediğinde servis kalan lig/sezon/maç için istek atmayı bırakır; iş `partial` olarak kaydedilir
(eski API "Completed" gösterir) ve nedeni karta yazılır. Kayıt diske yazılamıyorsa (disk dolu, izin yok) iş
"Failed" olarak, nedeni söyleyerek biter. Bitiş metinlerinin çeviri anahtarı işin `finished` olayındadır.
"""
from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any, Mapping

from src.errors import to_platform_error
from src.exceptions import StorageError
from src.jobs.manager import JobHandle, JobManager, JobNotActive, JobOutcome
from src.jobs.model import ErrorInfo, JobState
from src.redact import redact_text
from src.services.context import build_context
from src.services.sync import SyncSelection, SyncService, SyncSpec
from src.web.routes.common import _job_store, _refresh_scraper_state, config_manager, logger

if TYPE_CHECKING:
    from src.jobs.progress import JobProgress
    from src.web.routes.scrape import FetchRequest


def job_manager() -> JobManager:
    """Web sürecinin iş yöneticisi: yolların da koruma için kullandığı, süreç genelindeki iş deposunun üzerinde."""
    return JobManager(_job_store)


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


class _ConsoleHandle:
    """Servisin gördüğü iş (src.services.sync.JobHandle): iş yöneticisinin tutamacı ve sunucu konsolundaki satır."""

    def __init__(self, handle: JobHandle) -> None:
        self._handle = handle
        self.id = handle.id

    @property
    def progress(self) -> "JobProgress":
        return self._handle.progress

    def cancelled(self) -> bool:
        return self._handle.cancelled()

    def log(self, message: str) -> None:
        self._handle.log(message)
        if self.progress.phase == "export":
            # Sunucu konsolundaki satır (CSV aşaması başlarken); servis yazdırmadığı için burada
            print("--> Exporting to CSV...")

    def publish(self, fields: Mapping[str, Any]) -> None:
        self._handle.publish(fields)


def _error_info(exc: BaseException) -> ErrorInfo:
    """İşi durduran hata, hata tablosundaki koduyla; metin log satırları gibi maskelenir."""
    error = to_platform_error(exc)
    return ErrorInfo(code=error.code, message=redact_text(error.message), details=error.details)


def _fetch(handle: JobHandle, payload: "FetchRequest", spec: SyncSpec) -> JobOutcome:
    """İşin gövdesi: servisi çalıştırır ve sonucunu işin bitişine çevirir. Hata fırlatmaz."""
    from src.i18n import get_i18n

    tracker = handle.progress
    summary = _summary(payload)
    handle.log(f"Starting fetch for {summary}")
    logger.info("Background fetch started. job_id=%s Target: %s", handle.id, summary)

    try:
        # Servis tutamacın iptal sorusunu istek bağlamına kurar: işin her isteği (ve yeniden denemeler
        # arasındaki beklemeler) ona bakar, böylece "Durdur" geçerli sezonun ya da 2 dakikalık bir 403 geri
        # çekilmesinin sonunda değil, saniyenin kesri içinde etkili olur. 429/403 geri çekilmeleri de kartta
        # geri sayım olur.
        result = SyncService(build_context(config_manager)).run(spec, handle=_ConsoleHandle(handle))
    except StorageError as e:
        # Kalıcı depolama hatası (disk dolu, izin yok): kalan maçlar da yazılamaz, iş durur
        params = {"path": e.path or "?", "reason": e.detail or str(e)}
        message = get_i18n().t("storage_error_abort", **params)
        logger.error(f"Background fetch aborted, data could not be written: {e}")
        print(f"--> Background Task FAILED: {message}")
        return JobOutcome(
            state=JobState.FAILED,
            result={"error": "storage", "error_path": e.path, **tracker.result()},
            error=_error_info(e),
            message=message,
            code="storage_error_abort",
            params=params,
        )
    except Exception as e:
        # Hata metni iş kaydına yazılır ve API'den (durum, iş geçmişi, SSE) okunur: bir istek hatası
        # proxy adresini parolasıyla taşıyabilir, bu yüzden log satırları gibi maskelenir
        error_msg = redact_text(str(e))
        logger.error(f"Background update failed: {e}")
        logger.error(traceback.format_exc())
        print(f"--> Background Task FAILED: {error_msg}")
        return JobOutcome(state=JobState.FAILED, error=_error_info(e), message=f"Error: {error_msg}")

    if result.state == "cancelled":
        return JobOutcome(state=JobState.CANCELLED, message="Cancelled")

    empty_n = result.schedule_empty_seasons
    code = None
    params: Mapping[str, Any] = {}
    if result.breaker:
        code, params = "fetch_stopped_by_breaker", {"reason": result.breaker}
        message = get_i18n().t(code, **params)
    elif empty_n > 0:
        code = "fetch_completed_with_warning"
        message = get_i18n().t(code)
    else:
        message = "Background Task Completed Successfully."
    print("--> Background Task Completed Successfully.")
    logger.info("Background update and export completed.")
    # Bitiş durumu servisin sonucudur: devre kesildiyse ya da bir maç indirilemediyse `partial`
    return JobOutcome(
        state=JobState(result.state),
        result={"schedule_empty_seasons": empty_n, **result.progress},
        message=message,
        code=code,
        params=params,
    )


def run_fetch_job(job_id: str, payload: "FetchRequest") -> None:
    """
    Başlatılmış /api/fetch işini çalıştırır (kendi thread'inde; bkz. routes/scrape.trigger_fetch).

    İş yöneticisi işin bitişini her durumda yazar (gövde hata fırlatsa da) ve `writer` kilidini bırakır.
    """
    spec = _spec_from_payload(payload)
    try:
        job_manager().run(
            job_id,
            lambda handle: _fetch(handle, payload, spec),
            phases=spec.job_phases,
            on_change=_refresh_scraper_state,
        )
    except JobNotActive:
        # İş, thread'i başlamadan bitirilmiş (ör. depo başka bir dizine taşınmış): yapılacak bir şey yok
        logger.warning("Fetch job %s is no longer the running job of this process; nothing was fetched.", job_id)
    finally:
        _refresh_scraper_state()
