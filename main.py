#!/usr/bin/env python3
"""
SofaScore Scraper uygulaması ana giriş noktası.
"""

import json
import sys
import traceback
import os
import argparse
from pathlib import Path

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

from src.SofaScoreUi import SimpleSofaScoreUI
from src.exceptions import StorageError
from src.logger import get_logger, log_file_path
from src.i18n import get_i18n
from src.sports import sport_slugs

# Logger'ı al
logger = get_logger("Main")


def parse_arguments() -> argparse.Namespace:
    """
    Komut satırı argümanlarını ayrıştırır.

    Returns:
        argparse.Namespace: Ayrıştırılan argümanlar
    """
    parser = argparse.ArgumentParser(
        description="SofaScore'dan futbol maçı verilerini çeken ve analiz eden uygulama.",
        epilog=(
            "Headless / CI örnekleri:\n"
            "  %(prog)s --headless --update-all\n"
            "  %(prog)s --headless --update-all --fetch-mode details --league-id 52\n"
            "  %(prog)s --headless --csv-export --data-dir ./data\n"
            "  %(prog)s --doctor --json   (ortam denetimi; hata varsa çıkış kodu 1)\n"
            "Not: --web modu kendi ConfigManager örneğini kullanır; CLI --config/--data-dir yalnızca "
            "TUI ve headless için geçerlidir (.env / DATA_DIR ile web hizalanabilir)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=VERSION_TEXT,
        help="Sürümü yazdırır ve çıkar",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Kullanıcı arayüzünü göstermeden toplu veri çekme işlemi yapar"
    )

    parser.add_argument(
        "--update-all",
        action="store_true",
        help="Tüm (veya --league-id ile tek) lig verisini headless günceller; --fetch-mode ile kapsam",
    )

    parser.add_argument(
        "--fetch-mode",
        choices=["full", "details"],
        default="full",
        help="--update-all ile: full=sezon+maç+detay, details=sadece maç detayları (web ile aynı)",
    )

    parser.add_argument(
        "--league-id",
        type=int,
        default=None,
        metavar="ID",
        help="--update-all ile yalnız bu SofaScore lig ID'si (config'de kayıtlı olmalı)",
    )

    parser.add_argument(
        "--config",
        default=None,
        help="Lig listesi dosyası (varsayılan: config/leagues.txt)",
    )

    parser.add_argument(
        "--data-dir",
        default=None,
        dest="data_dir",
        help="Veri kök dizini (varsayılan: .env'deki DATA_DIR, o da yoksa data)",
    )

    parser.add_argument(
        "--csv-export",
        action="store_true",
        help="Verileri CSV formatında dışa aktarır"
    )

    parser.add_argument(
        "--web",
        action="store_true",
        help="Web arayüzünü başlatır"
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Web sunucusunun dinleyeceği adres (varsayılan: 127.0.0.1). "
        "0.0.0.0 arayüzü kimlik doğrulaması olmadan tüm ağa açar.",
    )

    parser.add_argument("--port", type=int, default=8000, help="Web sunucusu portu (varsayılan: 8000)")

    parser.add_argument(
        "--dev",
        action="store_true",
        help="Web sunucusunu kod değişikliğinde yeniden başlatır (geliştirme)",
    )

    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Yalnızca geçici kayıtları yeniler (REFRESH_WINDOW_HOURS içindeki maçların /event'i); "
        "günlük cron için. --league-id ile tek lig",
    )

    parser.add_argument(
        "--refresh-legacy",
        action="store_true",
        help="observation.json'ı olmayan eski kayıtları da bir kez yeniler (varsayılan: kesin sayılır)",
    )

    parser.add_argument(
        "--recheck-unavailable",
        nargs="?",
        const="legacy",
        default=None,
        choices=["legacy", "all"],
        metavar="legacy|all",
        help="\"Bu dilim bu maçta yok\" işaretlerini yeniden denetime açar (ağ isteği yapmaz; dilimler sonraki "
        "indirmede yeniden istenir). legacy (varsayılan): yalnızca kesin yanıtla (404 / boş yanıt) doğrulanmamış, "
        "eski sürümlerden kalan işaretler; all: hepsi. --league-id ile tek lig; --headless --update-all ile "
        "birlikte verilirse önce işaretler açılır, sonra indirme yapılır",
    )

    parser.add_argument(
        "--watch",
        action="store_true",
        help="Canlı izleyici: --sport ve --league-ids ya da --event-ids ile; olaylar data/watch_events.jsonl",
    )
    parser.add_argument("--sport", choices=list(sport_slugs()), help="--watch ile spor")
    parser.add_argument("--league-ids", default=None, help="--watch: virgülle SofaScore unique-tournament id'leri")
    parser.add_argument("--event-ids", default=None, help="--watch: virgülle maç id'leri")
    parser.add_argument(
        "--watch-hours",
        type=float,
        default=None,
        help="--watch: en fazla kaç saat (varsayılan: Ctrl+C'ye ya da --event-ids'teki maçlar bitene kadar)",
    )

    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Ortam denetimi: Python, paketler, tarayıcı, dizinler, web arayüzü derlemesi, .env "
        "(SofaScore'a bağlanmaz; hata varsa çıkış kodu 1). Seçenekler: --doctor --help "
        "(--json, --strict, --only, --skip, --lang, --live)",
    )

    parser.add_argument(
        "--diagnostics",
        nargs="?",
        const="",
        default=None,
        metavar="YOL",
        help="Tanılama paketini (zip: sürümler, gizli değerleri maskelenmiş ayarlar, son iş, log sonu) "
        "yazar ve çıkar. YOL verilmezse log dizinine yazılır",
    )

    parser.add_argument(
        "--ignore-rate-limit",
        action="store_true",
        help="Rate-limit circuit breaker mekanizmasını devre dışı bırakır"
    )

    return parser.parse_args()


def _run_watch(args: argparse.Namespace) -> int:
    """Canlı izleyici (src/watcher.py): olay üretir, sonuçlandırmaz."""
    from src.config_manager import ConfigManager
    from src.watcher import MatchWatcher

    def ids(raw):
        return [int(x) for x in str(raw).split(",") if x.strip()] if raw else []

    if not args.sport or not (args.league_ids or args.event_ids):
        print("Örnek: python main.py --watch --sport football --league-ids 17,8", file=sys.stderr)
        return 2
    data_dir = args.data_dir or ConfigManager().get_data_dir()
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
        print(f"İzleyici durdu: {watcher.requests} istek; olaylar {watcher.events_path}", file=sys.stderr)
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


def main() -> int:
    """
    Uygulamanın ana giriş noktası.

    Returns:
        int: Çıkış kodu (0: başarılı, 1: hata)
    """
    try:
        # Komut satırı argümanlarını ayrıştır
        args = parse_arguments()

        if args.diagnostics is not None:
            return _run_diagnostics(args.diagnostics)

        if args.ignore_rate_limit:
            os.environ["IGNORE_RATE_LIMIT"] = "true"
            logger.warning("Rate-limit circuit breaker --ignore-rate-limit ile devre dışı bırakıldı.")

        if args.web:
            try:
                import uvicorn
                if args.host not in ("127.0.0.1", "localhost", "::1"):
                    logger.warning(
                        f"Web arayüzü {args.host} adresinde dinliyor: ağdaki herkes kimlik doğrulaması "
                        "olmadan erişebilir (veri silme, ayarlar dahil)."
                    )
                    extra = "*" if args.host in ("0.0.0.0", "::") else args.host
                    os.environ["SOFASCORE_ALLOWED_HOSTS"] = f"localhost,127.0.0.1,[::1],{extra}"
                logger.info(f"Web arayüzü başlatılıyor: http://localhost:{args.port}")
                i18n = get_i18n()
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

        ui = SimpleSofaScoreUI(config_path=args.config, data_dir=args.data_dir)

        if args.recheck_unavailable:
            # Ağ isteği yok: yalnızca işaretler geri alınır; dilimler sonraki indirmede yeniden istenir
            reset = ui.match_data_fetcher.reset_unavailable_markers(
                league_id=args.league_id, include_confirmed=args.recheck_unavailable == "all"
            )
            logger.info(f"Yeniden denetim: {reset}")
            print(get_i18n().t("recheck_unavailable_done", **reset))
            if not (args.headless or args.refresh_only):
                return 0

        if args.refresh_only:
            md = ui.match_data_fetcher
            md.begin_job_cache()
            try:
                ids = md.refresh_due_ids(league_id=args.league_id)
                logger.info(f"Yenileme: {len(ids)} geçici kayıt")
                stats = md.refresh_matches(ids)
            finally:
                md.end_job_cache()
            print(
                f"Yenileme: {stats['refreshed']} maç yenilendi, {stats['changed']} değişti, "
                f"{stats['failed']} başarısız (değişiklikler: data/score_changes.jsonl)"
            )
            if stats.get("breaker"):
                # Devre kesildi: kalan maçlar denenmedi; cron bunu sıfırdan farklı çıkış koduyla görsün
                print(
                    get_i18n().t("refresh_stopped_by_breaker", reason=stats["breaker"], skipped=stats.get("skipped", 0)),
                    file=sys.stderr,
                )
                return 2
            return 1 if stats["failed"] and not stats["refreshed"] else 0

        if args.headless:
            logger.info("Headless modda çalışılıyor")
            ran = False

            if args.update_all:
                logger.info(
                    "Headless güncelleme: league_id=%s mode=%s",
                    args.league_id,
                    args.fetch_mode,
                )
                # Tüm aşamalar (sezon, maç programı, detay, yenileme) tek devre kesiciyi paylaşır: açıldığında
                # istek katmanı bu çalıştırma için SofaScore'a yeni istek göndermez.
                from src import breaker as request_breaker

                with request_breaker.scope(ui.config_manager) as job_breaker:
                    ui.run_headless_fetch(league_id=args.league_id, mode=args.fetch_mode)
                if job_breaker.tripped:
                    print(get_i18n().t("fetch_stopped_by_breaker", reason=job_breaker.reason()), file=sys.stderr)
                    os.environ["APP_EXIT_CODE"] = "2"
                # Tüm ligler yolunda menü katmanı hatayı yakalayıp yalnızca "hata" yazar: nedeni burada söyle
                storage_error = getattr(ui.match_data_fetcher, "last_storage_error", None)
                if storage_error is not None:
                    raise storage_error
                ran = True

            if args.csv_export:
                logger.info("CSV dışa aktarma işlemi başlatılıyor")
                ui.export_all_to_csv()
                ran = True

            if not ran:
                logger.error(
                    "Headless için en az biri gerekli: --update-all ve/veya --csv-export"
                )
                print(
                    "Örnek: python main.py --headless --update-all\n"
                    "        python main.py --headless --update-all --fetch-mode details --league-id 52\n"
                    "        python main.py --headless --csv-export --data-dir ./data",
                    file=sys.stderr,
                )
                return 2
        else:
            # Normal interaktif mod
            logger.info("İnteraktif mod başlatılıyor")
            ui.run()

        forced_exit_code = os.getenv("APP_EXIT_CODE")
        if forced_exit_code and forced_exit_code.isdigit():
            return int(forced_exit_code)
        return 0  # Başarılı çıkış

    except KeyboardInterrupt:
        i18n = get_i18n()
        print(i18n.t('prog_terminated_by_user'))
        return 0

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
