#!/usr/bin/env python3
"""
SofaScore Scraper uygulaması ana giriş noktası.
"""

import contextlib
import dataclasses
import json
import logging
import sys
import time
import traceback
import os
import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple

from src.version import __version__

VERSION_TEXT = f"SofaScore Scraper {__version__}"

# --version ağır modüller yüklenmeden yanıtlanır: aşağıdaki import'lar config/ dosyalarını
# oluşturur ve log yazar; sürüm sorgusu yan etkisiz ve yalnızca tek satır çıktı olmalı
# (bağımlılıklar kurulmadan da çalışır: yalnızca standart kütüphane).
if __name__ == "__main__" and "--version" in sys.argv[1:]:
    print(VERSION_TEXT)
    sys.exit(0)

# Çalışma dizinini modülün dizinine ayarla
script_dir = Path(__file__).resolve().parent
# Kullanıcının komutu çalıştırdığı dizin: --diagnostics YOL göreli yolu buna göre çözülür
invoked_cwd = Path.cwd()
os.chdir(script_dir)

# --doctor aşağıdaki import'lardan önce yanıtlanır: eksik paket (dotenv dahil) tam da onun bulması
# gereken sorundur; src/doctor.py yalnızca standart kütüphaneyi ister. Seçenekleri: --doctor --help
if __name__ == "__main__" and "--doctor" in sys.argv[1:]:
    from src.doctor import main as doctor_main

    sys.exit(doctor_main([a for a in sys.argv[1:] if a != "--doctor"]))

import dotenv

# Çevre değişkenlerini yükle
from src.paths import env_file_path  # noqa: E402

dotenv.load_dotenv(env_file_path())

# Yapılandırma her kipte burada, diğer modüllerden (ve Main logger'ından) önce yüklenir: sofascore.toml bu içe
# aktarmada okunur ve bozuksa uygulama burada durur. Terminal arayüzü (src/SofaScoreUi.py) yalnızca etkileşimli
# dalda, servisler ve istek katmanı yalnızca kendi dallarında içe aktarılır.
from src.config_manager import ConfigManager
from src.exceptions import StorageError
from src.store import LeaseHeld
from src.private_files import harden_secret_paths
from src.logger import get_logger, log_file_path
from src.i18n import get_i18n
from src.sports import sport_slugs

if TYPE_CHECKING:
    from src.jobs.manager import JobHandle, JobOutcome
    from src.services.context import ServiceContext
    from src.services.sync import SyncResult, SyncSpec

# Lig yapılandırması her kipte başlangıçta kurulur: config/ dizini ve örnek lig dosyası ilk çalıştırmada burada
# oluşur. Eskiden bunu terminal arayüzünün içe aktarılması yan etki olarak yapıyordu. Tekil nesne yolsuz
# kurulduğu için --config bugünkü gibi etkisizdir (docs/design/02-services.md 1.7).
ConfigManager()

# Logger'ı al
logger = get_logger("Main")

# Veri dizini başka bir sürecin kilidinde (docs/design/02-services.md 4.5'teki kod). Diğer çıkış kodları
# bugünkü gibidir: 0 başarı, 1 hata, 2 kullanım hatası ya da devre kesici.
EXIT_LEASE_HELD = 6


def parse_arguments() -> argparse.Namespace:
    """
    Komut satırı argümanlarını ayrıştırır.

    Returns:
        argparse.Namespace: Ayrıştırılan argümanlar
    """
    # Yardım metinleri locales/*.json'dan gelir (dil kuralı: src/language.py). argparse bunları
    # %-biçimlendirir: çevirilerde yalın "%" olmamalı (tests/test_language.py denetler).
    t = get_i18n().t
    parser = argparse.ArgumentParser(
        description=t("cli_description"),
        epilog=t("cli_epilog"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    parser.add_argument("-h", "--help", action="help", help=t("cli_help_help"))

    parser.add_argument("--version", action="version", version=VERSION_TEXT, help=t("cli_help_version"))

    parser.add_argument("--headless", action="store_true", help=t("cli_help_headless"))

    parser.add_argument("--update-all", action="store_true", help=t("cli_help_update_all"))

    parser.add_argument("--fetch-mode", choices=["full", "details"], default="full", help=t("cli_help_fetch_mode"))

    parser.add_argument("--league-id", type=int, default=None, metavar="ID", help=t("cli_help_league_id"))

    parser.add_argument("--config", default=None, help=t("cli_help_config"))

    parser.add_argument("--data-dir", default=None, dest="data_dir", help=t("cli_help_data_dir"))

    parser.add_argument("--csv-export", action="store_true", help=t("cli_help_csv_export"))

    parser.add_argument("--web", action="store_true", help=t("cli_help_web"))

    parser.add_argument("--host", default="127.0.0.1", help=t("cli_help_host"))

    parser.add_argument("--allow-any-host", action="store_true", help=t("cli_help_allow_any_host"))

    parser.add_argument("--port", type=int, default=8000, help=t("cli_help_port"))

    parser.add_argument("--dev", action="store_true", help=t("cli_help_dev"))

    parser.add_argument("--refresh-only", action="store_true", help=t("cli_help_refresh_only"))

    parser.add_argument("--refresh-legacy", action="store_true", help=t("cli_help_refresh_legacy"))

    parser.add_argument(
        "--recheck-unavailable",
        nargs="?",
        const="legacy",
        default=None,
        choices=["legacy", "all"],
        metavar="legacy|all",
        help=t("cli_help_recheck_unavailable"),
    )

    parser.add_argument("--watch", action="store_true", help=t("cli_help_watch"))
    parser.add_argument("--sport", choices=list(sport_slugs()), help=t("cli_help_sport"))
    parser.add_argument("--league-ids", default=None, help=t("cli_help_league_ids"))
    parser.add_argument("--event-ids", default=None, help=t("cli_help_event_ids"))
    parser.add_argument("--watch-hours", type=float, default=None, help=t("cli_help_watch_hours"))

    parser.add_argument("--doctor", action="store_true", help=t("cli_help_doctor"))

    parser.add_argument(
        "--diagnostics",
        nargs="?",
        const="",
        default=None,
        metavar=t("cli_metavar_path"),
        help=t("cli_help_diagnostics"),
    )

    parser.add_argument("--ignore-rate-limit", action="store_true", help=t("cli_help_ignore_rate_limit"))

    return parser.parse_args()


@contextlib.contextmanager
def _data_dir_lease(data_dir: str, name: str, purpose: str) -> Iterator[None]:
    """
    Blok boyunca veri dizininin `name` kilidini tutar (docs/design/01-storage.md 6.1): canlı izleyici (aynı
    dizinde aynı sporu izleyen ikinci bir süreç reddedilir) ve yalnızca yeniden denetim (`--recheck-unavailable`)
    için. Kilit başkasındaysa LeaseHeld fırlar; main() onu sahibin bilgisiyle kullanıcıya söyler. Depo bu süreçte
    kilit için açılır ve blok bitince kapatılır.

    İndirme ve yenileme bu işlevi kullanmaz: onların yazar kilidini iş yöneticisi alır (bkz. `_run_services`).
    """
    from src.store import open_store

    store = open_store(data_dir)
    try:
        with store.lease(name, purpose=purpose):
            yield
    finally:
        store.close()


def _lease_held_text(held: LeaseHeld) -> str:
    """Reddedilen kilidin sahibi, kullanıcının dilinde; bilinmeyen alanlar "?" olarak yazılır."""
    since = "?"
    if held.started_at is not None:
        since = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(held.started_at))
    return get_i18n().t(
        "cli_lease_held",
        lease=held.name or "?",
        pid=held.pid if held.pid is not None else "?",
        host=held.host or "?",
        purpose=held.purpose or "?",
        since=since,
    )


def _run_watch(args: argparse.Namespace) -> int:
    """Canlı izleyici (src/watcher.py): olay üretir, sonuçlandırmaz."""
    from src.watcher import MatchWatcher

    def ids(raw):
        return [int(x) for x in str(raw).split(",") if x.strip()] if raw else []

    if not args.sport or not (args.league_ids or args.event_ids):
        print(get_i18n().t("cli_watch_usage"), file=sys.stderr)
        return 2
    data_dir = args.data_dir or ConfigManager().get_data_dir()
    # Aynı dizinde aynı sporu izleyen ikinci bir süreç aynı durum ve olay dosyalarına yazardı: reddedilir
    with _data_dir_lease(data_dir, f"watcher:{args.sport}", "watch"):
        watcher = MatchWatcher(
            args.sport,
            event_ids=ids(args.event_ids),
            league_ids=ids(args.league_ids),
            on_event=lambda ev: print(json.dumps(ev, ensure_ascii=False)),
            data_dir=data_dir,
        )
        try:
            watcher.run(until_seconds=args.watch_hours * 3600 if args.watch_hours else None)
        except KeyboardInterrupt:
            pass
        finally:
            print(
                get_i18n().t("cli_watch_stopped", requests=watcher.requests, path=watcher.events_path),
                file=sys.stderr,
            )
    return 0


def _run_diagnostics(target: str) -> int:
    """Tanılama paketini yazar (src/diagnostics.py); web arayüzündeki indirmeyle aynı içerik."""
    from src import diagnostics

    path = str(invoked_cwd / Path(target).expanduser()) if target else None
    try:
        written = diagnostics.write_bundle(path, source="cli")
    except OSError as e:
        print(get_i18n().t("diagnostics_failed", error=str(e)), file=sys.stderr)
        return 1
    print(get_i18n().t("diagnostics_written", path=written))
    return 0


def _report_sync(result: "SyncResult") -> int:
    """
    Bir indirmenin sonucunu kullanıcıya söyler ve çıkış kodunu döndürür (0; devre kesildiyse 2).

    Maç listesi alınamayan sezonlar stderr'e yazılır. Devre kesildiyse yalnızca neden söylenir: yarıda kalan
    bir çalışmada indirici, denenmeyen maçları başarısız diye bildirmez ve sayılar yanıltıcı olurdu.
    """
    t = get_i18n().t
    if result.state == "cancelled":
        # İş başka bir süreçten iptal edildi (ör. web arayüzündeki Durdur): Ctrl+C'deki gibi, özet yazılmaz
        print(t("prog_terminated_by_user"))
        return 0
    if result.schedule_empty_seasons:
        print(t("cli_sync_empty_schedules", count=result.schedule_empty_seasons), file=sys.stderr)
    if result.breaker:
        print(t("fetch_stopped_by_breaker", reason=result.breaker), file=sys.stderr)
        return 2
    progress = result.progress
    total = int(progress.get("details_total", 0))
    failed = int(progress.get("failed_count", 0))
    print(
        t(
            "cli_sync_summary",
            total=total,
            ok=max(total - failed, 0),
            failed=failed,
            refreshed=progress.get("refreshed", 0),
            changed=progress.get("refresh_changed", 0),
        )
    )
    return 0


def _run_refresh_only(spec: "SyncSpec", ctx: "ServiceContext", job: "JobHandle") -> Tuple[int, "SyncResult"]:
    """--refresh-only. 0: en az bir kayıt yenilendi ya da iş yok; 1: hepsi başarısız; 2: devre kesildi."""
    from src.services.sync import RefreshCounts, SyncService

    result = SyncService(ctx).run(spec, handle=job)
    counts = result.refresh or RefreshCounts()
    t = get_i18n().t
    if result.state == "cancelled":
        print(t("prog_terminated_by_user"))  # başka bir süreçten iptal edildi; Ctrl+C'deki gibi
        return 0, result
    print(t("cli_refresh_summary", refreshed=counts.refreshed, changed=counts.changed, failed=counts.failed))
    if result.breaker:
        # Devre kesildi: kalan maçlar denenmedi; cron bunu sıfırdan farklı çıkış koduyla görsün
        print(t("refresh_stopped_by_breaker", reason=result.breaker, skipped=counts.skipped), file=sys.stderr)
        return 2, result
    return (1 if counts.failed and not counts.refreshed else 0), result


def _run_headless(
    args: argparse.Namespace, ctx: "ServiceContext", spec: Optional["SyncSpec"] = None,
    job: Optional["JobHandle"] = None,
) -> Tuple[int, Optional["SyncResult"]]:
    """
    --headless: --update-all (indirme) ve/veya --csv-export. 2: eylem verilmedi ya da devre kesildi.

    İndirme bir iştir: `spec` ve iş yöneticisinin tutamacı (`job`) onunla birlikte gelir. Yalnızca CSV dışa
    aktarma iş değildir (kilit almaz, iş geçmişine girmez).
    """
    from src.services.export import export_all_csv
    from src.services.sync import SyncService

    t = get_i18n().t
    logger.info("Running in headless mode")
    exit_code = 0
    ran = False
    result: Optional["SyncResult"] = None

    if args.update_all and spec is not None:
        logger.info("Headless update: league_id=%s mode=%s", args.league_id, args.fetch_mode)
        # Web işiyle aynı akış (sezon listeleri → maç listeleri → detaylar), tek devre kesiciyle. CSV aşaması
        # istenmez: komut satırında o, --csv-export'un ayrı adımıdır.
        result = SyncService(ctx).run(spec, handle=job)
        exit_code = _report_sync(result)
        ran = True

    if args.csv_export:
        logger.info("Starting CSV export")
        csv_path = export_all_csv(ctx)
        print(f"{t('csv_created_success')} {csv_path}" if csv_path else t("csv_created_error"))
        ran = True

    if not ran:
        logger.error("Headless needs at least one of --update-all and --csv-export")
        print(t("cli_headless_usage"), file=sys.stderr)
        return 2, result
    return exit_code, result


def _recheck_unavailable(args: argparse.Namespace, ctx: "ServiceContext") -> None:
    """--recheck-unavailable: ağ isteği yok; yalnızca işaretler geri alınır, dilimler sonraki indirmede istenir."""
    from src.services.maintenance import MaintenanceService

    reset = MaintenanceService(ctx).recheck_unavailable(
        args.league_id, include_confirmed=args.recheck_unavailable == "all"
    )
    print(get_i18n().t("recheck_unavailable_done", matches=reset.matches, slices=reset.slices, scanned=reset.scanned))


def _job_outcome(result: Optional["SyncResult"]) -> "JobOutcome":
    """Servisin sonucu → işin bitişi: durum, sonuç özeti ve devre kesildiyse nedeni söyleyen kart metni."""
    from src.jobs.manager import JobOutcome
    from src.jobs.model import JobState

    if result is None:
        return JobOutcome()
    if result.state == "cancelled":
        return JobOutcome(state=JobState.CANCELLED)
    summary: Dict[str, Any] = {"schedule_empty_seasons": result.schedule_empty_seasons, **result.progress}
    if result.refresh is not None:
        summary["refresh"] = dataclasses.asdict(result.refresh)
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


def _run_services(args: argparse.Namespace) -> int:
    """
    Terminal arayüzü olmadan çalışan kipler: --recheck-unavailable, --refresh-only, --headless.

    Hepsi aynı servis bağlamını kurar (src/services/context.py). İndirme (`--headless --update-all`) ve yenileme
    (`--refresh-only`) birer iştir: iş yöneticisi (src/jobs/manager.py) veri dizininin yazar kilidini alır, işi
    geçmişe kaydeder (web arayüzünün iş geçmişinde görünür, oradan iptal edilebilir) ve bu süreçte çalıştırır.
    Kilit başka bir süreçteyse iş başlamaz ve LeaseHeld main()'e çıkar (çıkış kodu 6). Yalnızca yeniden denetim
    iş değildir ama yazar kilidini tutar; yalnızca CSV dışa aktarma kilit almaz.
    """
    from src.jobs.manager import local_origin
    from src.jobs.model import JobKind
    from src.services.context import build_context
    from src.services.sync import SyncSpec
    from src.store import JobStoreConflict

    # Tekil yapılandırma nesnesi (yukarıda kuruldu); --config bugünkü gibi okunmaz
    ctx = build_context(ConfigManager(), data_dir=args.data_dir)

    downloads = bool(args.headless and args.update_all)
    if not (args.refresh_only or downloads):
        if not args.recheck_unavailable:
            return _run_headless(args, ctx)[0]
        with _data_dir_lease(ctx.data_dir, "writer", "recheck-unavailable"):
            _recheck_unavailable(args, ctx)
            return _run_headless(args, ctx)[0] if args.headless else 0

    if args.refresh_only:
        kind, purpose, spec = JobKind.REFRESH, "refresh", SyncSpec(mode="refresh", league_id=args.league_id)
    else:
        kind, purpose = JobKind.SYNC, "headless"
        spec = SyncSpec(mode=args.fetch_mode, league_id=args.league_id, export=False)
    exit_codes: List[int] = []

    def body(job: "JobHandle") -> "JobOutcome":
        if args.recheck_unavailable:
            _recheck_unavailable(args, ctx)
        if args.refresh_only:
            exit_code, result = _run_refresh_only(spec, ctx, job)
        else:
            exit_code, result = _run_headless(args, ctx, spec, job)
        exit_codes.append(exit_code)
        return _job_outcome(result)

    # İş günlüğü satırları bugünkü gibi SyncService log satırları olarak da yazılır (konsol ve log dosyası)
    service_log = get_logger("SyncService")
    try:
        ctx.jobs.submit(
            kind,
            dataclasses.asdict(spec),
            body,
            origin=local_origin("cli"),
            background=False,
            phases=spec.job_phases,
            # Web arayüzünün iş kartı başlığı istek gövdesinden üretilir: aynı biçim
            payload={"league_id": spec.league_id, "mode": spec.mode, "selections": None},
            # Kilidin amacı: aynı dizini isteyen başka bir süreç kullanıcıya bunu söyler
            lease_purpose=purpose,
            on_log=lambda message: service_log.info("%s", message),
        )
    except JobStoreConflict as conflict:
        # Yazar kilidi başka bir süreçte: sahibini söyleyen LeaseHeld main()'in bilinen dalına gider
        if isinstance(conflict.__cause__, LeaseHeld):
            raise conflict.__cause__ from None
        raise
    return exit_codes[0] if exit_codes else 1


def main() -> int:
    """
    Uygulamanın ana giriş noktası.

    Returns:
        int: Çıkış kodu (0: başarılı, 1: hata, 2: kullanım hatası ya da devre kesici, 6: veri dizini
        başka bir sürecin kilidinde)
    """
    try:
        # Komut satırı argümanlarını ayrıştır
        args = parse_arguments()

        # .env ve tarayıcı profili yalnızca sahibince okunur (POSIX); her çalışma kipinde denetlenir
        harden_secret_paths()

        if args.diagnostics is not None:
            return _run_diagnostics(args.diagnostics)

        if args.ignore_rate_limit:
            os.environ["IGNORE_RATE_LIMIT"] = "true"
            logger.warning("Rate-limit circuit breaker --ignore-rate-limit ile devre dışı bırakıldı.")

        if args.web:
            try:
                import uvicorn
                from src.web import security

                i18n = get_i18n()
                # Host izin listesi açıktır: kullanıcının SOFASCORE_ALLOWED_HOSTS değeri her zaman
                # geçerlidir; yerel olmayan bir --host onu hiçbir zaman sessizce "*" yapmaz.
                try:
                    hosts = security.allowed_hosts_for_bind(
                        args.host, os.environ.get(security.ALLOWED_HOSTS_ENV), allow_any=args.allow_any_host
                    )
                except security.AllowedHostsRequired:
                    print(i18n.t("web_allowed_hosts_required", host=args.host), file=sys.stderr)
                    return 2
                if hosts is not None:
                    os.environ[security.ALLOWED_HOSTS_ENV] = hosts
                if not security.is_loopback_bind(args.host) and not security.api_token():
                    # Tek ve açık uyarı: konsola ve log dosyasına (log seviyesi kapatmışsa yine de konsola)
                    exposed = i18n.t("web_exposed_without_token", host=args.host, port=args.port)
                    logger.warning(exposed)
                    if not logger.isEnabledFor(logging.WARNING):
                        print(exposed, file=sys.stderr)
                logger.info(f"Web arayüzü başlatılıyor: http://localhost:{args.port}")
                print(i18n.t('web_server_starting'))
                print(i18n.t('go_to_address'))
                print(i18n.t('press_ctrl_c'))

                reload_opts = {}
                if args.dev:
                    # Only watch app code — data/ match writes must not restart the server mid-scrape
                    reload_opts = {
                        "reload": True,
                        "reload_dirs": [str(script_dir / "src"), str(script_dir / "locales")],
                    }
                uvicorn.run("src.web.app:app", host=args.host, port=args.port, **reload_opts)
                # ponytail: SPA assets live in frontend/dist — rebuild with `cd frontend && npm run build` after UI changes
            except ImportError:
                i18n = get_i18n()
                print(i18n.t('err_web_packages_not_installed'))
                print(i18n.t('run_pip_install'))
                return 1
            return 0

        # Terminal modları (etkileşimli, headless, --watch, --refresh-only): köprü durumu değişince
        # ("SofaScore bizi engelliyor") kullanıcıya tek satır. Web modunda aynı bilgi arayüzdeki
        # afişte ve /health'te.
        from src import bridge_health

        bridge_health.add_listener(bridge_health.print_cli_line)

        if args.data_dir:
            # Açıkça verilen --data-dir bu çalıştırma için DATA_DIR'i ezer (tüm modüller aynısını görsün)
            os.environ["DATA_DIR"] = args.data_dir
        if args.watch:
            return _run_watch(args)

        if args.refresh_legacy:
            os.environ["REFRESH_LEGACY"] = "true"

        if args.recheck_unavailable or args.refresh_only or args.headless:
            return _run_services(args)

        # Normal interaktif mod: terminal arayüzü yalnızca burada yüklenir
        from src.SofaScoreUi import SimpleSofaScoreUI

        ui = SimpleSofaScoreUI(config_path=args.config, data_dir=args.data_dir)
        logger.info("İnteraktif mod başlatılıyor")
        ui.run()

        # Menüden başlatılan bir indirmede devre kesildiyse indirici bunu ortam değişkeniyle bildirir; menünün
        # türü belli bir sonucu yoktur. Servis kipleri bu değişkeni okumaz: onların sonucu SyncResult'tır.
        forced_exit_code = os.getenv("APP_EXIT_CODE")
        if forced_exit_code and forced_exit_code.isdigit():
            return int(forced_exit_code)
        return 0  # Başarılı çıkış

    except KeyboardInterrupt:
        i18n = get_i18n()
        print(i18n.t('prog_terminated_by_user'))
        return 0

    except LeaseHeld as e:
        # Veri dizini başka bir sürecin elinde. LeaseHeld bir StorageError'dır: bu dal ondan önce gelmeli.
        logger.error(
            "Data directory is in use by another process: lease=%s pid=%s host=%s purpose=%s",
            e.name, e.pid, e.host, e.purpose,
        )
        print(_lease_held_text(e), file=sys.stderr)
        return EXIT_LEASE_HELD

    except StorageError as e:
        # Kayıt diske yazılamadı (disk dolu, izin yok): iz dökümü yerine nedeni söyle
        logger.error(f"Depolama hatası, işlem durduruldu: {e}")
        print(get_i18n().t("storage_error_abort", path=e.path or "?", reason=e.detail or str(e)), file=sys.stderr)
        return 1

    except Exception as e:
        i18n = get_i18n()
        logger.exception(f"Beklenmeyen hata: {str(e)}")
        print(i18n.t('unexpected_error_occurred', error=str(e)))
        traceback.print_exc()
        log_path = log_file_path()
        if log_path:
            print(i18n.t('check_log_for_details', path=log_path))
        else:
            print(i18n.t('check_console_for_details'))
        return 1  # Hata çıkışı


if __name__ == "__main__":
    sys.exit(main())
