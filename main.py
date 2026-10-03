#!/usr/bin/env python3
"""
SofaScore Scraper'ın eski giriş noktası; 3.0 boyunca bir geçiş kabuğudur (docs/design/02-services.md 4.7, plan
maddesi P19).

    python main.py <komut> [seçenekler]     yeni CLI'nin kendisi (`ssc <komut>` ile aynı): sync, status, ...
    python main.py <eski bayraklar>         yeni komutlara çevrilir (src/cli/legacy_flags.py); stderr'e tek bir
                                            kullanımdan kalkma satırı yazılır
    python main.py --web ...                `ssc serve` olarak çalışır (P25; kullanımdan kalkma satırıyla)
    python main.py                          kısa yardım: terminal menüsü 3.0'da kaldırıldı (P26); web arayüzü
                                            `ssc serve`, komutlar `ssc --help`; çıkış kodu 2
    python main.py --version                sürüm (yan etkisiz, yalnızca standart kütüphane)

Eski bayrakların çalıştırdığı komutlar yeni CLI'nin çıktı kurallarına ve çıkış kodlarına uyar (karar D5):
loglar stderr'de; çıkış kodları 0 başarı, 2 kullanım ya da yapılandırma hatası, 3 kısmi başarı, 4 SofaScore
engelledi (devre kesici), 5 depolama hatası, 6 veri dizini başka bir sürecin kilidinde, 130 / 143 Ctrl+C /
SIGTERM ile iptal.

Bu modül yüklenirken yalnızca standart kütüphaneyi ve src/version.py'yi içe aktarır: ağır modüller (ayarlar,
log, istek katmanı) yalnızca onları kullanan dalda yüklenir.
"""

import argparse
import contextlib
import logging
import os
import sys
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
        # Eylem bayrağı yok: eskiden terminal menüsü açılırdı (3.0'da kaldırıldı, P26)
        return print_no_menu_help(translation.warnings)

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
    (src/cli/exit_codes.py). Argümansız çalıştırma kısa bir yardım yazar ve 2 ile çıkar (terminal menüsü yok).
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and is_subcommand(arguments):
        return run_new_cli(arguments)
    return run_legacy(arguments)


# --- terminal menüsünün yerine ---------------------------------------------------------------------


def print_no_menu_help(warnings: Sequence[str] = ()) -> int:
    """
    Bayraksız (ya da yalnızca eylemsiz eski bayraklarla) çalıştırma: terminal menüsü 3.0'da kaldırıldı (plan
    maddesi P26). Web arayüzünü (`ssc serve`) ve komut listesini (`ssc --help`) gösteren kısa bir yardım stderr'e
    yazılır; çıkış kodu kullanım hatasıdır (2). Hiçbir dosya ya da dizin oluşturulmaz, istek atılmaz.
    """
    from src.cli import exit_codes
    from src.cli.main import translator

    t, _lang = translator(None)
    for warning in warnings:
        print(t("ssc_warning", message=warning), file=sys.stderr)
    commands = ", ".join(sorted(_command_names()))
    print(t("cli_no_menu", prog=NEW_PROG, legacy=PROG, commands=commands), file=sys.stderr)
    return exit_codes.USAGE_ERROR


if __name__ == "__main__":
    sys.exit(main())
