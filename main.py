#!/usr/bin/env python3
"""
SofaScore Scraper'ın eski giriş noktası; 3.0 boyunca bir geçiş kabuğudur (docs/design/02-services.md 4.7, plan
maddesi P19).

    python main.py <komut> [seçenekler]     yeni CLI'nin kendisi (`ssc <komut>` ile aynı): sync, status, ...
    python main.py <eski bayraklar>         yeni komutlara çevrilir (src/cli/legacy_flags.py); stderr'e tek bir
                                            kullanımdan kalkma satırı yazılır
    python main.py --web ...                `ssc serve` olarak çalışır (P25; kullanımdan kalkma satırıyla)
    python main.py                          terminal menüsü (eskisi gibi; P26 kaldırır)
    python main.py --version                sürüm (yan etkisiz, yalnızca standart kütüphane)

Eski bayrakların çalıştırdığı komutlar yeni CLI'nin çıktı kurallarına ve çıkış kodlarına uyar (karar D5):
loglar stderr'de; çıkış kodları 0 başarı, 2 kullanım ya da yapılandırma hatası, 3 kısmi başarı, 4 SofaScore
engelledi (devre kesici), 5 depolama hatası, 6 veri dizini başka bir sürecin kilidinde, 130 / 143 Ctrl+C /
SIGTERM ile iptal.

Bu modül yüklenirken yalnızca standart kütüphaneyi ve src/version.py'yi içe aktarır: ağır modüller (ayarlar,
log, istek katmanı, terminal arayüzü) yalnızca onları kullanan dalda yüklenir.
"""

import argparse
import contextlib
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Iterator, List, Optional, Sequence

from src.version import __version__

VERSION_TEXT = f"SofaScore Scraper {__version__}"
# Yeni CLI'nin hata ve yardım metinlerinde görünen komut adı
PROG = "python main.py"
# Kullanımdan kalkma satırının önerdiği komut adı (pyproject.toml [project.scripts])
NEW_PROG = "ssc"

# --version ağır modüller yüklenmeden yanıtlanır: sürüm sorgusu yan etkisiz ve yalnızca tek satır çıktı olmalı
# (bağımlılıklar kurulmadan da çalışır: yalnızca standart kütüphane).
if __name__ == "__main__" and "--version" in sys.argv[1:]:
    print(VERSION_TEXT)
    sys.exit(0)

# Proje kökü: terminal menüsü buraya geçer (yeni CLI kendisi geçer)
script_dir = Path(__file__).resolve().parent

# Terminal menüsü dalının logger'ı (src/logger.py ilk kullanımda kurar)
logger = logging.getLogger("Main")

# Değeri olan bayraklar: alt komut aranırken değerleri atlanır (eski bayraklar ve yeni CLI'nin genel bayrakları)
_VALUE_OPTIONS = frozenset({
    "--config", "--data-dir", "--output", "--log-level", "--log-format", "--lang", "--rate", "--wait", "--progress",
    "--fetch-mode", "--league-id", "--host", "--port", "--sport", "--league-ids", "--event-ids", "--watch-hours",
})


def get_i18n() -> Any:
    """Uygulamanın çeviri nesnesi (src/i18n.py); eski bayrakların yardım ve kullanım metinleri için."""
    from src.i18n import get_i18n as app_i18n

    return app_i18n()


def parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """
    Eski bayrakları ayrıştırır (`--help` bu listeyi yazar). Yeni komutlar `python main.py <komut> --help` ile
    görülür.
    """
    from src.sports import sport_slugs

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

    return parser.parse_args(None if argv is None else list(argv))


# --- yeni CLI ------------------------------------------------------------------------------------


def _command_names() -> frozenset:
    """Yeni CLI'nin üst düzey komut ve grup adları (komut modülleri kendini kaydeder; hafif)."""
    from src.cli import commands as registry

    return frozenset(entry.path[0] for entry in [*registry.commands(), *registry.groups()])


def is_subcommand(argv: Sequence[str]) -> bool:
    """argv yeni CLI'nin bir komutu mu (`sync`, `--json status`): ilk konumsal argüman bir komut adıysa."""
    names = _command_names()
    skip = False
    for index, arg in enumerate(argv):
        if skip:
            skip = False
            continue
        if arg == "--":
            return False
        if arg.startswith("-"):
            if "=" not in arg and arg in _VALUE_OPTIONS:
                skip = True
            elif arg == "--recheck-unavailable":
                following = argv[index + 1] if index + 1 < len(argv) else ""
                skip = following in ("legacy", "all")
            elif arg == "--diagnostics":
                following = argv[index + 1] if index + 1 < len(argv) else ""
                skip = bool(following) and not following.startswith("-")
            continue
        return arg in names
    return False


@contextlib.contextmanager
def _restored_process() -> Iterator[None]:
    """
    Bir CLI komutunun sürece bıraktıklarını (çalışma dizini, ortam, etkin ayarlar, log akışı, biçimi ve seviyesi)
    geri alır: eski bayraklar birden çok komut çalıştırabilir ve her biri temiz bir süreçte başlamalı.
    """
    cwd, environ = os.getcwd(), dict(os.environ)
    level = logging.getLogger().level
    log_module = sys.modules.get("src.logger")
    stream = log_module.console_stream() if log_module is not None else "stdout"
    log_format = log_module.log_format() if log_module is not None else "text"
    try:
        yield
    finally:
        loader = sys.modules.get("src.config.loader")
        if loader is not None:
            loader.reset()
        os.environ.clear()
        os.environ.update(environ)
        log_module = sys.modules.get("src.logger")
        if log_module is not None:
            log_module.set_console_stream(stream)
            log_module.set_log_format(log_format)
        logging.getLogger().setLevel(level)
        os.chdir(cwd)


def run_new_cli(argv: Sequence[str]) -> int:
    """Yeni CLI'yi bu süreçte çalıştırır (src/cli/main.py) ve çıkış kodunu döndürür."""
    from src.cli.main import main as cli_main

    with _restored_process():
        return int(cli_main(list(argv), prog=PROG))


def _say_deprecated(commands: Sequence[Sequence[str]], warnings: Sequence[str] = ()) -> None:
    """Kullanımdan kalkma satırı (ve çevirinin uyarıları) stderr'e; dil uygulamanınkidir."""
    from src.cli import legacy_flags
    from src.cli.main import translator

    t, _lang = translator(None)
    print(legacy_flags.deprecation_line(t, NEW_PROG, commands), file=sys.stderr)
    for warning in warnings:
        print(t("ssc_warning", message=warning), file=sys.stderr)


def run_legacy(argv: Sequence[str]) -> int:
    """
    Eski bayraklar: yeni komutlara çevrilir ve sırayla çalıştırılır. Bir komut bitmezse (kullanım hatası, kilit,
    depolama hatası, iptal) sonrakiler çalışmaz; çıkış kodu, sonucu belli olanların öncelikli olanıdır.
    """
    from src.cli import exit_codes, legacy_flags

    if "--doctor" in argv:
        # Seçenekleri `ssc doctor`un; doctor paketler kurulmadan da çalışır (ağır modül yüklenmez)
        command = legacy_flags.doctor_command(argv)
        _say_deprecated([command])
        return run_new_cli(command)

    args = parse_arguments(argv)
    translation = legacy_flags.translate(args, os.getcwd())
    if translation.error is not None:
        # Kullanım hatası: hiçbir şey çalışmadan, eski metniyle
        key = {legacy_flags.WATCH_USAGE: "cli_watch_usage", legacy_flags.NEEDS_ACTION: "cli_headless_usage"}
        print(get_i18n().t(key[translation.error]), file=sys.stderr)
        return exit_codes.USAGE_ERROR
    if translation.interactive:
        return _run_interactive(args)

    _say_deprecated(translation.commands, translation.warnings)
    codes: List[int] = []
    for command in translation.commands:
        code = run_new_cli(command)
        codes.append(code)
        if code not in (exit_codes.OK, exit_codes.PARTIAL, exit_codes.UPSTREAM):
            break  # iş bitmedi: sonraki adım (ör. CSV dışa aktarma) çalışmaz
    return exit_codes.combine(codes)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Giriş noktası. Bir alt komut yeni CLI'ye gider; eski bayraklar çevrilir. Çıkış kodu yeni CLI'nin tablosudur
    (src/cli/exit_codes.py); terminal menüsü eski kodlarını korur.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and is_subcommand(arguments):
        return run_new_cli(arguments)
    return run_legacy(arguments)


# --- karşılığı henüz olmayan eski kipler ------------------------------------------------------------


def _prepare_legacy_process(args: argparse.Namespace) -> None:
    """Terminal menüsünün eski başlangıcı: `.env`, yapılandırma, gizli dosyaların izinleri."""
    import dotenv

    from src.paths import env_file_path

    os.chdir(script_dir)
    dotenv.load_dotenv(env_file_path())
    # Yapılandırma her kipte burada, diğer modüllerden (ve Main logger'ından) önce yüklenir: sofascore.toml bu içe
    # aktarmada okunur ve bozuksa uygulama burada durur. Lig yapılandırması da kurulur (config/ dizini ve örnek lig
    # dosyası ilk çalıştırmada burada oluşur). Tekil nesne yolsuz kurulduğu için --config bu kiplerde etkisizdir.
    from src.config_manager import ConfigManager
    from src.logger import get_logger
    from src.private_files import harden_secret_paths

    ConfigManager()
    get_logger("Main")
    # .env ve tarayıcı profili yalnızca sahibince okunur (POSIX)
    harden_secret_paths()
    if args.ignore_rate_limit:
        os.environ["IGNORE_RATE_LIMIT"] = "true"
        logger.warning("Rate-limit circuit breaker disabled with --ignore-rate-limit.")


def _run_interactive(args: argparse.Namespace) -> int:
    """Bayraksız çalıştırma: terminal menüsü (P26 kaldırır). Terminal arayüzü yalnızca burada yüklenir."""
    from src.exceptions import StorageError

    try:
        _prepare_legacy_process(args)
        # Köprü durumu değişince ("SofaScore bizi engelliyor") kullanıcıya tek satır
        from src import bridge_health

        bridge_health.add_listener(bridge_health.print_cli_line)
        if args.data_dir:
            # Açıkça verilen --data-dir bu çalıştırma için DATA_DIR'i ezer (tüm modüller aynısını görsün)
            os.environ["DATA_DIR"] = args.data_dir
        if args.refresh_legacy:
            os.environ["REFRESH_LEGACY"] = "true"

        from src.SofaScoreUi import SimpleSofaScoreUI

        ui = SimpleSofaScoreUI(config_path=args.config, data_dir=args.data_dir)
        logger.info("İnteraktif mod başlatılıyor")
        ui.run()

        # Menüden başlatılan bir indirmede devre kesildiyse indirici bunu ortam değişkeniyle bildirir; menünün
        # türü belli bir sonucu yoktur.
        forced_exit_code = os.getenv("APP_EXIT_CODE")
        if forced_exit_code and forced_exit_code.isdigit():
            return int(forced_exit_code)
        return 0
    except KeyboardInterrupt:
        print(get_i18n().t('prog_terminated_by_user'))
        return 0
    except StorageError as e:
        # Kayıt diske yazılamadı (disk dolu, izin yok): iz dökümü yerine nedeni söyle
        logger.error("Storage error, stopped: %s", e)
        print(get_i18n().t("storage_error_abort", path=e.path or "?", reason=e.detail or str(e)), file=sys.stderr)
        return 1
    except Exception as e:
        return _unexpected(e)


def _unexpected(e: BaseException) -> int:
    from src.logger import log_file_path

    i18n = get_i18n()
    logger.exception("Unexpected error: %s", e)
    print(i18n.t('unexpected_error_occurred', error=str(e)))
    traceback.print_exc()
    log_path = log_file_path()
    if log_path:
        print(i18n.t('check_log_for_details', path=log_path))
    else:
        print(i18n.t('check_console_for_details'))
    return 1


if __name__ == "__main__":
    sys.exit(main())
