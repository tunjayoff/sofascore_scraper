"""
İndirme komutları (docs/design/02-services.md bölüm 4.1; plan maddesi P19): `sync`, `fetch event|tournament`,
`refresh`. Üçü de eşitleme servisini (sofascore_scraper/services/sync.py) bir iş olarak çalıştırır.

    ssc sync                                   takip edilen her turnuva: sezon listeleri, programlar, detaylar
    ssc sync --tournament 17 --only events     tek lig, yalnızca maç detayları (programlar indirilmez)
    ssc sync --only seasons                    yalnızca sezon listeleri, tazelik süresine bakılmadan
    ssc sync --recheck-unavailable all         önce "yok" işaretlerini aç, sonra indir
    ssc sync --dry-run                         istek atmadan planı ve istek tahminini göster
    ssc fetch event 12345678 12345679          yalnızca bu maçların detayları
    ssc fetch tournament 17 --season 61627     bir turnuvanın sezonu (ya da bütün sezonları)
    ssc refresh [--tournament 17] [--include-legacy]   yalnızca geçici kayıtların /event'i

Her iş (P11'in iş yöneticisi, sofascore_scraper/jobs/manager.py) veri dizininin `writer` kilidini alır, iş geçmişine yazılır
(web arayüzünde görünür, `ssc jobs` ile okunur, başka bir süreçten iptal edilebilir) ve bu süreçte çalışır.

  * Kilit başka bir süreçteyse iş başlamaz: sahibi `error.details.holder`dadır, çıkış kodu 6. `--wait N`
    kilidi N saniye bekler.
  * Çıkış kodları (bölüm 4.5): 0 başarı ya da yapılacak iş yok; 3 iş bitti ama bazı maçlar ya da listeler
    alınamadı (`partial`); 4 devre kesici SofaScore'un engellemesi yüzünden işi durdurdu; 5 depolama hatası;
    6 kilit başkasında; 130 / 143 Ctrl+C / SIGTERM ile iptal (ya da iş başka bir süreçten iptal edildi: 130).
    Sinyalde sonuç yine yazılır (sofascore_scraper/cli/signals.py); ikinci sinyal süreci hemen bitirir.
  * `--dry-run` istek atmaz ve kilit almaz: hangi listelerin bayat, hangi maçların neye ihtiyacı olduğunu ve en
    az kaç istek gerektiğini söyler. Programın tur sayfaları ve yeni listelerden çıkacak maçlar önceden
    bilinemez: tahmin bir alt sınırdır (`requests_is_minimum`).
  * `--progress text|ndjson` işin olaylarını stderr'e yazar (ndjson: `jobs tail` satırlarıyla aynı biçim).
  * Yapılandırılmış sink'ler (`[[sink]]`) işten önce kaydedilir ve işten sonra en çok 10 sn boşaltılır
    (`sinks.drain_at_exit`): işin `job.started` / `job.finished` olayları da onlara gider.

Turnuvalar takip tablosundan gelir (plan maddesi FX-13): leagues.txt'in, yapılandırma dosyasının `[[follow]]`
girdilerinin, API'nin ve `ssc follows add`in etkin turnuva takipleri, her biri kendi sezon seçimiyle
(sofascore_scraper/services/sync.py `sync_targets`). `--tournament` takip edilmeyen bir turnuvayı da indirir (bütün sezonları). Ağır içe aktarmalar (servisler, Store, istek katmanı) işlevlerin içindedir.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as _dt
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from sofascore_scraper.cli import exit_codes, signals
from sofascore_scraper.cli.commands import CommandResult, Invocation, command, group
from sofascore_scraper.cli.output import NDJSON, Output, Translator, dumps
from sofascore_scraper.errors import UsageError

if TYPE_CHECKING:
    from sofascore_scraper.jobs.manager import JobOutcome
    from sofascore_scraper.services.context import ServiceContext
    from sofascore_scraper.services.maintenance import ResetCounts
    from sofascore_scraper.services.sync import SyncResult, SyncSpec

logger = logging.getLogger("Cli")

RECHECK_MODES = ("legacy", "all")
PROGRESS_JOIN_SECONDS = 5.0  # iş bittikten sonra ilerleme satırlarının son okuması en çok bu kadar beklenir


# --- seçenekler ----------------------------------------------------------------------------------


def _positive_id(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive id, got {value!r}")
    return number


def _follow_id(value: str) -> str:
    from sofascore_scraper.services.follows import parse_follow_id

    if parse_follow_id(value) is None:
        raise argparse.ArgumentTypeError(f"expected a follow id such as team:42, got {value!r}")
    return value


def _sync_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--tournament", type=_positive_id, metavar="ID", help=t("ssc_help_sync_tournament"))
    parser.add_argument("--follow", dest="follows", action="append", type=_follow_id, metavar="KIND:ID",
                        help=t("ssc_help_sync_follow"))
    parser.add_argument("--only", choices=("events", "seasons"), help=t("ssc_help_sync_only_any"))
    parser.add_argument("--recheck-unavailable", dest="recheck", nargs="?", const="legacy", choices=RECHECK_MODES,
                        metavar="legacy|all", help=t("ssc_help_sync_recheck"))
    parser.add_argument("--include-legacy", dest="include_legacy", action="store_true",
                        help=t("ssc_help_refresh_include_legacy"))
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help=t("ssc_help_dry_run"))


def _refresh_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--tournament", type=_positive_id, metavar="ID", help=t("ssc_help_refresh_tournament"))
    parser.add_argument("--include-legacy", dest="include_legacy", action="store_true",
                        help=t("ssc_help_refresh_include_legacy"))
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help=t("ssc_help_dry_run"))


def _fetch_event_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("event_ids", nargs="+", type=_positive_id, metavar="ID", help=t("ssc_help_fetch_event_ids"))
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help=t("ssc_help_dry_run"))


def _fetch_tournament_arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("tournament", type=_positive_id, metavar="ID", help=t("ssc_help_fetch_tournament_id"))
    parser.add_argument("--season", dest="seasons", action="append", type=_positive_id, metavar="ID",
                        help=t("ssc_help_fetch_season"))
    parser.add_argument("--only", choices=("events",), help=t("ssc_help_sync_only"))
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help=t("ssc_help_dry_run"))


# --- ortak: ayarlar, bağlam, sink'ler --------------------------------------------------------------


def option(inv: Invocation, name: str, default: Any = None) -> Any:
    """Genel bayrağın değeri (`--wait`, `--progress`); verilmediyse `default` (bayraklar ad alanında SUPPRESS'tir)."""
    value = getattr(inv.args, name, None)
    return default if value is None else value


def data_dir_of_settings() -> str:
    """Etkin ayarların veri dizini (mutlak yol): `--data-dir` > yapılandırma dosyası > DATA_DIR > `data`."""
    from sofascore_scraper.config import loader

    return os.path.abspath(loader.active_settings().storage.data_dir)


def build_service_context(data_dir: str) -> "ServiceContext":
    """
    İndirme komutlarının servis bağlamı: eski giriş noktasının başlangıçta yaptıkları da burada (gizli
    dosyaların izinleri, yapılandırma nesnesi, köprü durumunun stderr satırı).
    """
    from sofascore_scraper import bridge_health
    from sofascore_scraper.config_manager import ConfigManager
    from sofascore_scraper.private_files import harden_secret_paths
    from sofascore_scraper.services.context import build_context

    harden_secret_paths()
    bridge_health.add_listener(bridge_health.print_cli_line)
    try:
        return build_context(ConfigManager(), data_dir=data_dir)
    except OSError as e:
        # Veri dizini kurulamadı (yerinde bir dosya var, izin yok, disk dolu): depolama hatası, çıkış kodu 5
        from sofascore_scraper.exceptions import StorageError

        raise StorageError.from_exception(e, getattr(e, "filename", None) or data_dir) from e


@contextlib.contextmanager
def results_only_on_stdout() -> Iterator[None]:
    """
    Blok boyunca `print()` satırları stderr'e gider: indiricilerin eski ilerleme satırları ("Fetching match
    details...", "Done: 4/4 matches") stdout'a, yani komutun sonucuna karışmaz (bölüm 4.4). Sonuç bloktan sonra
    yazılır.
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


def register_sinks(store: Any) -> Any:
    """
    Yapılandırılmış sink'lerin dağıtıcısı; işten önce kaydedilir (konumu "şimdi": işin kendi olaylarından önce).
    Sink yoksa None. Bozuk bir sink ayarı ConfigError'dır (`config_invalid`, çıkış kodu 2): iş başlamaz.
    """
    from sofascore_scraper import sinks
    from sofascore_scraper.config import loader

    dispatcher = sinks.dispatcher_for(store, loader.active_settings().sinks)
    if dispatcher is not None:
        dispatcher.register()
    return dispatcher


def drain_sinks(dispatcher: Any) -> None:
    """İşten sonra birikenler en çok 10 sn teslim edilir; sink'ler kapanır. Hata fırlatmaz."""
    if dispatcher is None:
        return
    from sofascore_scraper import sinks

    sinks.drain_at_exit(dispatcher)


# --- iş olaylarının satırları (`--progress`, `jobs tail`) ------------------------------------------


def iso_ms(ts_ms: Any) -> Optional[str]:
    """Epoch milisaniye → ISO-8601 UTC, milisaniyeli, `Z` (akış günlüğüyle aynı biçim)."""
    if ts_ms is None:
        return None
    moment = _dt.datetime.fromtimestamp(int(ts_ms) / 1000, tz=_dt.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def job_event_line(event: Any) -> Dict[str, Any]:
    """Bir JobEvent → akış satırı: `{"type": "job.<tür>", "job_id", "seq", "ts", "data"}`."""
    return {"type": f"job.{event.type}", "job_id": event.job_id, "seq": event.seq, "ts": iso_ms(event.ts_ms),
            "data": dict(event.data)}


def job_event_text(t: Translator, event: Any) -> Optional[str]:
    """Bir JobEvent'in okunur satırı (`--progress text`); ilerleme olayları yazılmaz (saniyede iki tane)."""
    data: Mapping[str, Any] = event.data or {}
    kind = str(event.type)
    if kind == "phase":
        return t("ssc_progress_phase", phase=data.get("phase") or "?", index=int(data.get("phase_index") or 0) + 1,
                 count=data.get("phase_count") or "?", total=data.get("total") if data.get("total") is not None else "?")
    if kind == "log":
        return str(data.get("message") or "")
    if kind == "failed":
        item = data.get("match_id") or data.get("item") or data.get("failed_count") or "?"
        return t("ssc_progress_failed", item=item)
    if kind == "breaker":
        return t("ssc_progress_breaker", reason=data.get("reason") or "?")
    if kind == "cancel_requested":
        return t("ssc_progress_cancel_requested")
    if kind == "finished":
        return t("ssc_progress_finished", state=data.get("state") or "?")
    return None


class _ProgressPrinter(threading.Thread):
    """İşin olaylarını (başka bir bağlantıyla, satırdan) okuyup stderr'e yazar; iş bitince kendiliğinden durur."""

    def __init__(self, jobs: Any, job_id: str, mode: str, out: Output, t: Translator) -> None:
        super().__init__(name="cli-progress", daemon=True)
        self._jobs, self._job_id, self._mode, self._out, self._t = jobs, job_id, mode, out, t

    def run(self) -> None:
        try:
            for event in self._jobs.events(self._job_id, follow=True):
                if self._mode == NDJSON:
                    self._out.info(dumps(job_event_line(event), NDJSON))
                else:
                    text = job_event_text(self._t, event)
                    if text:
                        self._out.info(text)
        except Exception as e:  # ilerleme satırları işi bozmaz
            logger.debug("Progress lines stopped: %s", e)


# --- iş -------------------------------------------------------------------------------------------


class _SignalAwareHandle:
    """İş tutamacı + sinyalle gelen iptal: servis ikisinden birini görünce durur (sofascore_scraper/services/sync.JobHandle)."""

    def __init__(self, handle: Any, cancel: signals.CancelRequest) -> None:
        self._handle = handle
        self._cancel = cancel

    @property
    def id(self) -> str:
        return str(self._handle.id)

    @property
    def progress(self) -> Any:
        return self._handle.progress

    def cancelled(self) -> bool:
        return self._cancel.requested or bool(self._handle.cancelled())

    def log(self, message: str, **fields: Any) -> None:
        self._handle.log(message, **fields)

    def publish(self, fields: Mapping[str, Any]) -> None:
        self._handle.publish(fields)


@dataclass
class JobRun:
    """Bir indirme işinin sonucu: kayıt, servisin sonucu, ön adımlar ve sinyal."""

    job_id: str
    kind: str
    state: str
    result: Optional["SyncResult"]
    recheck: Optional["ResetCounts"]
    seconds: float
    signal_number: Optional[int]


def job_outcome(result: Optional["SyncResult"]) -> "JobOutcome":
    """Servisin sonucu → işin bitişi: durum, sonuç özeti ve devre kesildiyse nedeni söyleyen kart metni."""
    from sofascore_scraper.i18n import get_i18n
    from sofascore_scraper.jobs.manager import JobOutcome
    from sofascore_scraper.jobs.model import JobState

    if result is None:
        return JobOutcome()
    if result.state == "cancelled":
        return JobOutcome(state=JobState.CANCELLED)
    summary: Dict[str, Any] = {"schedule_empty_seasons": result.schedule_empty_seasons, **result.progress}
    if result.refresh is not None:
        summary["refresh"] = dataclasses.asdict(result.refresh)
    if result.failed_listings:
        summary["failed_listings"] = [dataclasses.asdict(item) for item in result.failed_listings]
    if result.breaker:
        # Kart metninin çeviri anahtarı işin `finished` olayına da yazılır: istemci metni koddan üretir
        code, params = "fetch_stopped_by_breaker", {"reason": result.breaker}
        if result.refresh is not None:
            code, params = "refresh_stopped_by_breaker", {"reason": result.breaker, "skipped": result.refresh.skipped}
        return JobOutcome(
            state=JobState(result.state), result=summary, code=code, params=params,
            message=get_i18n().t(code, **params),
        )
    return JobOutcome(state=JobState(result.state), result=summary)


def run_sync_job(inv: Invocation, ctx: "ServiceContext", spec: "SyncSpec", *, kind: str, purpose: str,
                 before: Optional[Callable[["ServiceContext"], Any]] = None,
                 event_ids: Sequence[int] = ()) -> JobRun:
    """
    Eşitleme servisini bir iş olarak bu süreçte çalıştırır (`writer` kilidiyle). `before(ctx)` işin içinde,
    servisten önce çalışır (`--recheck-unavailable`); döndürdüğü `JobRun.recheck` olur. İşin belirtimi API'nin
    gövdesiyle aynı alanlarla kaydedilir (FX-20, sofascore_scraper/services/job_spec.py), hedeflerin adlarıyla; event_ids:
    kimliğiyle seçilen maçlar (`ssc fetch event`).

    Kilit alınamazsa LeaseHeld (sahibiyle); depolama hatası StorageError olarak çıkar (iş `failed` kaydedilir).
    """
    from sofascore_scraper.jobs.manager import local_origin
    from sofascore_scraper.jobs.model import JobKind
    from sofascore_scraper.services import job_spec
    from sofascore_scraper.services.sync import SyncService
    from sofascore_scraper.store import JobStoreConflict, LeaseHeld

    jobs = ctx.jobs
    holder: Dict[str, Any] = {}
    service_log = logging.getLogger("SyncService")
    cancel = signals.CancelRequest(on_first=lambda signum: inv.out.info(inv.t("ssc_cancel_requested")))
    started = time.monotonic()
    recorded = job_spec.record(kind, spec, event_ids=event_ids)
    names = job_spec.names(ctx.store, job_spec.targets(recorded))
    if names:
        recorded[job_spec.NAMES] = names
    try:
        job = jobs.start(
            JobKind(kind), recorded, origin=local_origin("cli"),
            wait_for_lease=float(option(inv, "wait", 0.0)),
            lease_purpose=purpose,
        )
    except JobStoreConflict as conflict:
        # Yazar kilidi başka bir süreçte: sahibini söyleyen LeaseHeld hata tablosundaki koduna gider
        if isinstance(conflict.__cause__, LeaseHeld):
            raise conflict.__cause__ from None
        raise

    def body(handle: Any) -> "JobOutcome":
        wrapped = _SignalAwareHandle(handle, cancel)
        if before is not None:
            holder["recheck"] = before(ctx)
        result = SyncService(ctx).run(spec, handle=wrapped)
        holder["result"] = result
        if cancel.requested:
            # Sinyal: iptal iş satırına da yazılır (başka bir süreçten istenmiş gibi görünür, olayı yazılır)
            jobs.cancel(handle.id)
        return job_outcome(result)

    # İş başladı: bundan sonra Ctrl+C ve SIGTERM işi iptal eder (iş satırı `cancelled` olur, kilit bırakılır).
    # Kilidi beklerken gelen Ctrl+C ise beklemeyi keser: iş hiç başlamamıştır.
    cancel.install()
    printer: Optional[_ProgressPrinter] = None
    try:
        progress_mode = str(option(inv, "progress", "none"))
        if progress_mode in ("text", NDJSON):
            printer = _ProgressPrinter(jobs, job.id, progress_mode, inv.out, inv.t)
            printer.start()
        finished = jobs.run(job.id, body, phases=spec.job_phases,
                            on_log=lambda message: service_log.info("%s", message))
    finally:
        cancel.restore()
        if printer is not None:
            printer.join(PROGRESS_JOIN_SECONDS)
    return JobRun(
        job_id=finished.id, kind=kind, state=str(finished.state.value), result=holder.get("result"),
        recheck=holder.get("recheck"), seconds=round(time.monotonic() - started, 3),
        signal_number=cancel.signal_number,
    )


def exit_code_of_run(run: JobRun) -> int:
    """İşin çıkış kodu (bölüm 4.5): sinyal 130/143; başka süreçten iptal 130; devre kesici 4; kısmi 3; yoksa 0."""
    if run.signal_number is not None:
        return signals.exit_code_of(run.signal_number)
    result = run.result
    if result is None:
        return exit_codes.OK
    if result.state == "cancelled":
        return exit_codes.CANCELLED_SIGINT
    if result.breaker:
        return exit_codes.UPSTREAM
    if result.state == "partial":
        return exit_codes.PARTIAL
    return exit_codes.OK


def run_data(run: JobRun) -> Dict[str, Any]:
    """İşin `--json` verisi (bölüm 4.4): kimlik, durum, sayılar, durdurulma nedeni, süre."""
    result = run.result
    progress: Mapping[str, Any] = result.progress if result is not None else {}
    total = int(progress.get("details_total") or 0)
    failed = int(progress.get("failed_count") or 0)
    data: Dict[str, Any] = {
        "job_id": run.job_id,
        "kind": run.kind,
        "state": run.state,
        "counts": {
            "events_needed": total,
            "events_stored": max(total - failed, 0) if result is not None and not result.breaker else None,
            "events_failed": failed,
            "refreshed": int(progress.get("refreshed") or 0),
            "refresh_changed": int(progress.get("refresh_changed") or 0),
            "schedule_empty_seasons": result.schedule_empty_seasons if result is not None else 0,
        },
        "stopped": result.breaker if result is not None else None,
        "failed_items": list(progress.get("failed") or []),
        "failed_listings": [dataclasses.asdict(item) for item in result.failed_listings] if result else [],
        "refresh": dataclasses.asdict(result.refresh) if result is not None and result.refresh is not None else None,
        "recheck": dataclasses.asdict(run.recheck) if run.recheck is not None else None,
        "cancelled_by_signal": run.signal_number,
        "duration_seconds": run.seconds,
    }
    return data


def _common_notes(t: Translator, run: JobRun) -> List[str]:
    notes: List[str] = []
    if run.recheck is not None:
        notes.append(t("recheck_unavailable_done", matches=run.recheck.matches, slices=run.recheck.slices,
                       scanned=run.recheck.scanned))
    return notes


def sync_result(inv: Invocation, run: JobRun) -> CommandResult:
    """Bir indirmenin (sync, fetch) sonucu: metin, notlar (stderr) ve çıkış kodu."""
    t, result = inv.t, run.result
    code = exit_code_of_run(run)
    notes = _common_notes(t, run)
    text: Optional[str] = None
    if result is None:
        text = None
    elif run.signal_number is not None or result.state == "cancelled":
        notes.append(t("ssc_job_cancelled", job_id=run.job_id))
    else:
        if result.schedule_empty_seasons:
            notes.append(t("ssc_sync_empty_schedules", count=result.schedule_empty_seasons))
        if result.failed_listings:
            notes.append(t("ssc_sync_failed_listings", count=len(result.failed_listings)))
        if result.breaker:
            # Yarıda kalan çalışmanın sayıları yanıltıcı olur: yalnızca neden söylenir
            notes.append(t("fetch_stopped_by_breaker", reason=result.breaker))
        else:
            counts = run_data(run)["counts"]
            text = t("ssc_sync_summary", total=counts["events_needed"], ok=counts["events_stored"],
                     failed=counts["events_failed"], refreshed=counts["refreshed"],
                     changed=counts["refresh_changed"])
    return CommandResult(data=run_data(run), text=text, exit_code=code, notes=notes)


def _include_legacy(enabled: bool) -> None:
    """`--include-legacy`: gözlemi olmayan eski kayıtlar bu çalıştırmada bir kez yenilenir (REFRESH_LEGACY)."""
    if enabled:
        os.environ["REFRESH_LEGACY"] = "true"


# --- kuru çalıştırma ------------------------------------------------------------------------------


def _event_requests(item: Any, selection: Any = None, row: Any = None) -> int:
    """
    Bir maç iş biriminin en az kaç istek tuttuğu: yenileme 1, eksik dilimler dilim başına 1, tam çekim 1 + seçilmiş
    ve tamlık hesabına giren dilimler (selection: yapılandırmanın seçimi, maçın satırına ya da sporuna göre).
    """
    from sofascore_scraper.services import planning

    if item.need == "refresh":
        return 1
    if item.need == "refill":
        return max(1, len(item.slices))
    chosen = planning.CONFIGURED if selection is None else selection
    return 1 + len(planning.expected_slice_keys(item.sport, chosen, row=row))


def _plan_events(ctx: "ServiceContext", ids: Sequence[Any]) -> Tuple[Dict[str, int], int]:
    from sofascore_scraper.services import planning
    from sofascore_scraper.services.query import RefreshPolicy
    from sofascore_scraper.store import Scope

    policy = planning.configured_policy(ctx.store)
    items = planning.plan_items(ctx.store, list(ids), RefreshPolicy.current(), selection=policy)
    full = [item.owner.id for item in items if item.need == "full"]
    rows: Dict[int, Any] = {}
    for start in range(0, len(full), 500):  # maç satırları (turnuva, takımlar: takibin seçimi için) parça parça
        chunk = tuple(full[start:start + 500])
        rows.update((state.event.id, state.event) for state in ctx.store.events.states(Scope(event_ids=chunk)))
    needs: Dict[str, int] = {"full": 0, "refill": 0, "refresh": 0}
    requests = 0
    for item in items:
        needs[item.need] = needs.get(item.need, 0) + 1
        requests += _event_requests(item, policy, rows.get(item.owner.id))
    return needs, requests


def _stored_event_ids(ctx: "ServiceContext", tournament_ids: Iterable[int], season_ids: Iterable[int] = ()) -> List[int]:
    """Katalogda bilinen maçlar (listelerden ya da detaylardan), turnuva ve sezonla süzülmüş."""
    from sofascore_scraper.store import EventQuery, Scope

    query = EventQuery(scope=Scope(tournament_ids=tuple(tournament_ids), season_ids=tuple(season_ids)))
    return [row.id for row in ctx.store.events.iter(query)]


def plan_sync(ctx: "ServiceContext", spec: "SyncSpec") -> Dict[str, Any]:
    """
    `--dry-run`: ne istenirdi. İstek atmaz, kilit almaz, iş kaydı yazmaz. Listelerin tazeliği eşitleme işinin
    kuralıdır (sofascore_scraper/services/listing.py); maçların ihtiyacı planlamanınkidir (sofascore_scraper/services/planning.py).
    """
    from sofascore_scraper.services import listing
    from sofascore_scraper.services.sync import pick_seasons, sync_targets

    store = ctx.store
    targets = sync_targets(ctx)
    named = tuple(getattr(spec, "follows", ()) or ())
    by_follow = not spec.selections and not spec.league_id
    leagues: List[int]
    if spec.selections:
        leagues = sorted({s.league_id for s in spec.selections})
    elif spec.league_id:
        leagues = [int(spec.league_id)]
    elif named:
        leagues = sorted({int(text.partition(":")[2]) for text in named
                          if text.startswith("tournament:") and int(text.partition(":")[2]) in targets})
    else:
        leagues = sorted(int(lid) for lid in targets)
    lists = {"season_lists": 0, "schedules": 0, "fresh": 0}
    list_requests = 0
    seasons_of: Dict[int, List[int]] = {}
    if spec.mode == "seasons":
        # Yalnızca sezon listeleri: tazelik süresine bakılmadan her biri bir istek
        lists["season_lists"] = len(leagues)
        list_requests = len(leagues)
    if spec.mode == "full":
        for lid in leagues:
            if listing.season_list_is_fresh(store, lid, listing.SEASON_LIST_TTL_SECONDS):
                lists["fresh"] += 1
            else:
                lists["season_lists"] += 1
                list_requests += 1
            wanted = [sid for s in spec.selections if s.league_id == lid for sid in s.season_ids]
            known = [int(s["id"]) for s in ctx.season_fetcher.get_seasons_for_league(lid) if s.get("id") is not None]
            if by_follow and lid in targets:
                known = pick_seasons(known, targets[lid].seasons)  # takibin sezon seçimi (sync'in kuralı)
            seasons_of[lid] = wanted or known
            for sid in seasons_of[lid]:
                if listing.schedule_is_fresh(store, lid, sid, listing.SCHEDULE_TTL_SECONDS):
                    lists["fresh"] += 1
                else:
                    lists["schedules"] += 1
                    list_requests += 1  # en az tur listesi; tur ve olay sayfaları önceden bilinmez
    explicit = [mid for s in spec.selections for mid in s.match_ids]
    if spec.mode == "seasons":
        ids: List[Any] = []
    elif explicit:
        ids = list(dict.fromkeys(explicit))
    else:
        ids = []
        for lid in leagues or [None]:  # type: ignore[list-item]
            seasons = seasons_of.get(lid, []) if lid is not None and spec.selections else []
            ids.extend(_stored_event_ids(ctx, [lid] if lid is not None else [], seasons))
    needs, event_requests = _plan_events(ctx, ids)
    follows, follow_requests = _plan_follows(ctx, spec, needs)
    return {
        "dry_run": True,
        "tournaments": leagues,
        "listings": lists,
        "events": needs,
        "follows": follows,
        "requests": list_requests + event_requests + follow_requests,
        "requests_is_minimum": spec.mode == "full",
    }


def _plan_follows(ctx: "ServiceContext", spec: "SyncSpec", needs: Dict[str, int]) -> Tuple[Dict[str, int], int]:
    """
    Takım, oyuncu ve maç takiplerinin payı (FX-19): takip sayıları ve en az istek. Takımın ve oyuncunun listesi
    istek atmadan bilinmez: takım en az iki sayfa (`next/0`, `last/0`), oyuncu bir sayfa; maçları listeye bağlıdır.
    Maç takipleri katalogdan planlanır ve `needs`'e eklenir.
    """
    from sofascore_scraper.services import follow_sync, planning
    from sofascore_scraper.services.follows import ConfigLeagues, FollowsService
    from sofascore_scraper.services.query import RefreshPolicy

    counts = {"team": 0, "player": 0, "event": 0}
    named = [text for text in (getattr(spec, "follows", ()) or ()) if not text.startswith("tournament:")]
    if spec.mode != "full" or (not named and (getattr(spec, "follows", ()) or spec.selections or spec.league_id)):
        return counts, 0
    rows = FollowsService(ctx.store, ConfigLeagues()).sync_others()
    if named:
        rows = [row for row in rows if f"{row.kind}:{row.entity_id}" in named]
    for row in rows:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    requests = 2 * counts["team"] + counts["player"]
    events = [row for row in rows if row.kind == "event"]
    if events:
        items, policy = follow_sync.plan_items(ctx.store, [], events, planning.configured_policy(ctx.store),
                                               RefreshPolicy.current())
        for item in items:
            needs[item.need] = needs.get(item.need, 0) + 1
            requests += _event_requests(item, policy, None)
    return counts, requests


def plan_refresh(ctx: "ServiceContext", league_id: Optional[int]) -> Dict[str, Any]:
    """`refresh --dry-run`: yenilenmesi gereken geçici kayıtlar; her biri tek istek (/event)."""
    from sofascore_scraper.services import planning
    from sofascore_scraper.services.query import RefreshPolicy

    rows = planning.refresh_due_events(ctx.store, RefreshPolicy.current(),
                                       tournament_ids=[league_id] if league_id else ())
    due = len(rows)
    return {"dry_run": True, "tournaments": [league_id] if league_id else [], "events": {"refresh": due},
            "requests": due, "requests_is_minimum": False}


def plan_text(t: Translator, plan: Mapping[str, Any]) -> str:
    lists = plan.get("listings") or {}
    events = plan.get("events") or {}
    lines = []
    if lists:
        lines.append(t("ssc_plan_listings", season_lists=lists.get("season_lists", 0),
                       schedules=lists.get("schedules", 0), fresh=lists.get("fresh", 0)))
    follows = plan.get("follows") or {}
    if any(follows.values()):
        lines.append(t("ssc_plan_follows", teams=follows.get("team", 0), players=follows.get("player", 0),
                       events=follows.get("event", 0)))
    lines.append(t("ssc_plan_events", full=events.get("full", 0), refill=events.get("refill", 0),
                   refresh=events.get("refresh", 0)))
    key = "ssc_plan_requests_minimum" if plan.get("requests_is_minimum") else "ssc_plan_requests"
    lines.append(t(key, requests=plan.get("requests", 0)))
    return "\n".join(lines)


# --- komutlar -------------------------------------------------------------------------------------


def _recheck(league_id: Optional[int], mode: Optional[str]) -> Optional[Callable[["ServiceContext"], Any]]:
    if not mode:
        return None

    def run(ctx: "ServiceContext") -> Any:
        from sofascore_scraper.services.maintenance import MaintenanceService

        return MaintenanceService(ctx).recheck_unavailable(league_id, include_confirmed=mode == "all")

    return run


def _download(inv: Invocation, spec: "SyncSpec", *, kind: str, purpose: str, dry_run: bool,
              before: Optional[Callable[["ServiceContext"], Any]] = None,
              plan: Optional[Callable[["ServiceContext"], Dict[str, Any]]] = None,
              describe: Callable[[Invocation, JobRun], CommandResult] = sync_result) -> CommandResult:
    with results_only_on_stdout():
        ctx = build_service_context(data_dir_of_settings())
        if dry_run:
            planned = (plan or (lambda c: plan_sync(c, spec)))(ctx)
        else:
            dispatcher = register_sinks(ctx.store)
            try:
                run = run_sync_job(inv, ctx, spec, kind=kind, purpose=purpose, before=before)
            finally:
                drain_sinks(dispatcher)
    if dry_run:
        return CommandResult(data=planned, text=plan_text(inv.t, planned))
    return describe(inv, run)


@command("sync", help="ssc_help_cmd_sync", configure=_sync_arguments, settings=True)
def sync(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.sync import SyncSpec

    args = inv.args
    if args.dry_run and args.recheck:
        raise UsageError("--recheck-unavailable changes the data folder; it cannot be part of --dry-run")
    if args.only == "seasons" and args.recheck:
        raise UsageError("--recheck-unavailable resets match details; it cannot be used with --only seasons")
    _include_legacy(args.include_legacy)
    mode = {"events": "details", "seasons": "seasons"}.get(args.only or "", "full")
    if args.follows and args.tournament:
        raise UsageError("Give only one of --tournament and --follow")
    if args.follows and mode == "details":
        raise UsageError("--follow downloads the follows' lists and matches; it cannot be used with --only events")
    spec: SyncSpec
    if args.follows:
        from sofascore_scraper.services.sync import FollowsSyncSpec

        spec = FollowsSyncSpec(mode=mode, follows=tuple(dict.fromkeys(args.follows)))  # type: ignore[arg-type]
    else:
        spec = SyncSpec(mode=mode, league_id=args.tournament)  # type: ignore[arg-type]
    return _download(inv, spec, kind="sync", purpose="sync", dry_run=args.dry_run,
                     before=_recheck(args.tournament, args.recheck))


group("fetch", help="ssc_help_cmd_fetch")


def _tournament_of(ctx: "ServiceContext", event_ids: Sequence[int]) -> Dict[int, int]:
    """Katalogdaki turnuvası (yoksa 0): seçimin `league_id`'si yalnızca işin kartında ve başarısız öğede görünür."""
    found: Dict[int, int] = {}
    for event_id in event_ids:
        row = ctx.store.events.get(int(event_id))
        found[int(event_id)] = int(row.tournament_id) if row is not None and row.tournament_id else 0
    return found


@command("fetch event", help="ssc_help_cmd_fetch_event", configure=_fetch_event_arguments, settings=True)
def fetch_event(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.sync import SyncSelection, SyncSpec

    ids = list(dict.fromkeys(int(e) for e in inv.args.event_ids))
    with results_only_on_stdout():
        ctx = build_service_context(data_dir_of_settings())
        by_tournament: Dict[int, List[int]] = {}
        for event_id, tournament in _tournament_of(ctx, ids).items():
            by_tournament.setdefault(tournament, []).append(event_id)
        spec = SyncSpec(mode="details", selections=tuple(
            SyncSelection(league_id=tid, match_ids=tuple(events)) for tid, events in sorted(by_tournament.items())))
        if inv.args.dry_run:
            needs, requests = _plan_events(ctx, ids)
        else:
            dispatcher = register_sinks(ctx.store)
            try:
                run = run_sync_job(inv, ctx, spec, kind="fetch", purpose="fetch", event_ids=ids)
            finally:
                drain_sinks(dispatcher)
    if inv.args.dry_run:
        planned = {"dry_run": True, "tournaments": sorted(t for t in by_tournament if t), "events": needs,
                   "requests": requests, "requests_is_minimum": False}
        return CommandResult(data=planned, text=plan_text(inv.t, planned))
    return sync_result(inv, run)


@command("fetch tournament", help="ssc_help_cmd_fetch_tournament", configure=_fetch_tournament_arguments,
         settings=True)
def fetch_tournament(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.sync import SyncSelection, SyncSpec

    args = inv.args
    mode = "details" if args.only == "events" else "full"
    seasons = tuple(dict.fromkeys(args.seasons or ()))
    if seasons and mode == "details":
        raise UsageError("--season selects schedules to fetch; it cannot be used with --only events")
    selections = (SyncSelection(league_id=args.tournament, season_ids=seasons),) if seasons else ()
    spec = SyncSpec(mode=mode, league_id=args.tournament, selections=selections)
    return _download(inv, spec, kind="fetch", purpose="fetch", dry_run=args.dry_run)


def refresh_result(inv: Invocation, run: JobRun) -> CommandResult:
    """Yalnızca yenilemenin sonucu: sayılar stdout'a, devre kesildiyse neden stderr'e."""
    from sofascore_scraper.services.sync import RefreshCounts

    t, result = inv.t, run.result
    code = exit_code_of_run(run)
    notes = _common_notes(t, run)
    text: Optional[str] = None
    if run.signal_number is not None or (result is not None and result.state == "cancelled"):
        notes.append(t("ssc_job_cancelled", job_id=run.job_id))
    elif result is not None:
        counts = result.refresh or RefreshCounts()
        text = t("ssc_refresh_summary", refreshed=counts.refreshed, changed=counts.changed, failed=counts.failed)
        if result.breaker:
            notes.append(t("refresh_stopped_by_breaker", reason=result.breaker, skipped=counts.skipped))
    return CommandResult(data=run_data(run), text=text, exit_code=code, notes=notes)


@command("refresh", help="ssc_help_cmd_refresh", configure=_refresh_arguments, settings=True)
def refresh(inv: Invocation) -> CommandResult:
    from sofascore_scraper.services.sync import SyncSpec

    args = inv.args
    _include_legacy(args.include_legacy)
    spec = SyncSpec(mode="refresh", league_id=args.tournament)
    return _download(inv, spec, kind="refresh", purpose="refresh", dry_run=args.dry_run,
                     plan=lambda ctx: plan_refresh(ctx, args.tournament), describe=refresh_result)


__all__ = [
    "JobRun",
    "build_service_context",
    "data_dir_of_settings",
    "drain_sinks",
    "exit_code_of_run",
    "iso_ms",
    "job_event_line",
    "job_event_text",
    "job_outcome",
    "option",
    "plan_sync",
    "register_sinks",
    "run_sync_job",
]
