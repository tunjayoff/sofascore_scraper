"""
Yeni CLI'nin giriş noktası (docs/design/02-services.md bölüm 4; plan maddesi P18).

    ssc [genel bayraklar] <komut> [seçenekler]
    python -m sofascore_scraper.cli.main ...

Bu modül komutları bilmez: sofascore_scraper/cli/commands/ altındaki modüller kendini kaydeder, argparse ağacı kayıttan
kurulur. Burada duranlar: genel bayraklar (bölüm 4.2), ayrıştırma, ayarların yüklenmesi, sonucun ve hatanın
yazılması (bölüm 4.4) ve çıkış kodu (bölüm 4.5).

Genel bayraklar komuttan önce de sonra da verilebilir (`ssc --json doctor`, `ssc doctor --json`). `--wait`,
`--progress` ve `--log-format` P19 ile geldi: ilk ikisini iş çalıştıran komutlar okur (sofascore_scraper/cli/commands/sync.py),
üçüncüsü ayarlar yüklendikten sonra konsol log satırlarının biçimini seçer (sofascore_scraper/logger.py).

Sinyaller (sofascore_scraper/cli/signals.py): komut çalışırken SIGTERM, Ctrl+C gibi KeyboardInterrupt olur ve `cancelled`
(143) olarak yazılır; iş çalıştıran komutlar iş süresince kendi işleyicilerini kurar (iptal, sonuç, 130 / 143).

Çalışma dizini: eski giriş noktası (depo kökündeki main.py) gibi proje köküne geçilir; `.env`, `config/`,
`data/` ve `./sofascore.toml` iki giriş noktasında da aynı yerdir. Kullanıcının verdiği göreli yollar
(`--config`, `--data-dir`, `--out`) komutu çalıştırdığı dizine göre çözülür.

Yüklenirken yalnızca standart kütüphaneyi ve hafif modülleri içe aktarır: `--version` ve `doctor` paketler
kurulmadan da çalışır.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sofascore_scraper import language
from sofascore_scraper.cli import MODULE_PROG, PROG, VERSION_TEXT
from sofascore_scraper.cli import commands as registry
from sofascore_scraper.cli.exit_codes import GENERAL_ERROR, OK, exit_code_for
from sofascore_scraper.cli.output import JSON, OUTPUT_MODES, TEXT, CommandResult, Output, Translator
from sofascore_scraper.cli import removed_flags, signals
from sofascore_scraper.errors import INTERNAL, Cancelled, PlatformError, UsageError, to_platform_error
from sofascore_scraper.sports import sport_slugs

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# Genel bayrakların argparse `dest` adları: her komutun ayrıştırıcısında bulunurlar, komutun kendi seçeneği değildirler
GLOBAL_DESTS = (
    "config", "data_dir", "output", "quiet", "verbose", "log_level", "log_format", "no_color", "lang", "rate",
    "ignore_breaker", "wait", "progress",
)
LOG_FORMATS = ("text", "json")
# `--progress`: işin ilerlemesi stderr'e (none: yazılmaz; text: okunur satırlar; ndjson: JobEvent satırları)
PROGRESS_MODES = ("none", "text", "ndjson")


class _Parser(argparse.ArgumentParser):
    """Kullanım hatasında süreci sonlandırmak yerine UsageError fırlatan ayrıştırıcı (çıktı kipine göre yazılır)."""

    # Hatanın hangi komutta oluştuğu ("config show"); kökte None
    command_path: Optional[str] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if sys.version_info >= (3, 14):
            # Python 3.14 terminalde yardım ve kullanım metnini renklendirir. Kullanım satırı hata zarfının
            # `details.usage` alanına da girer; orada renk kodu olmamalı ve çıktı terminale göre değişmemeli.
            kwargs.setdefault("color", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> Any:  # argparse imzası NoReturn; biz istisna fırlatırız
        error = UsageError(message, {"usage": self.format_usage().strip()})
        error.command_path = self.command_path  # type: ignore[attr-defined]
        raise error


@dataclass
class ParserTree:
    """Kurulmuş argparse ağacı: kök, yol başına ayrıştırıcı ve genel bayrakların eylemleri."""

    root: argparse.ArgumentParser
    parsers: Dict[registry.CommandPath, argparse.ArgumentParser]
    global_actions: List[argparse.Action]


@dataclass(frozen=True)
class GlobalOptions:
    """Ayrıştırılmış genel bayraklar (bölüm 4.2)."""

    config: Optional[str] = None
    data_dir: Optional[str] = None
    output: str = TEXT
    quiet: bool = False
    log_level: Optional[str] = None
    no_color: bool = False
    lang: Optional[str] = None
    rate: Optional[float] = None
    ignore_breaker: bool = False
    log_format: Optional[str] = None
    wait: Optional[float] = None
    progress: Optional[str] = None


# --- çeviri ----------------------------------------------------------------------------------------


def translator(lang: Optional[str] = None) -> Tuple[Translator, str]:
    """
    (çeviri işlevi, dil). Dil kuralı uygulamanınkidir: `--lang` > APP_LANGUAGE (.env dahil) > sistem dili >
    İngilizce. locales/*.json'ı sofascore_scraper/doctor.py'nin bağlamı okur: yalnızca standart kütüphaneyi ister (paketler
    kurulmadan da çalışır) ve `.env`'deki dil ayarını hesaba katar.
    """
    from sofascore_scraper import doctor

    ctx = doctor.Context(lang=lang)
    return ctx.t, str(ctx.lang)


# --- argparse ağacı --------------------------------------------------------------------------------


def _rate(value: str) -> float:
    word = value.strip().lower()
    if word == "off":
        return 0.0
    try:
        rate = float(word)
    except ValueError:
        rate = math.nan
    if not math.isfinite(rate) or rate < 0:
        raise argparse.ArgumentTypeError(f"expected requests per second (0 or more) or 'off', got {value!r}")
    return rate


def _seconds(value: str) -> float:
    try:
        seconds = float(value.strip())
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError(f"expected a number of seconds (0 or more), got {value!r}")
    return seconds


def _global_options(t: Translator) -> argparse.ArgumentParser:
    """
    Genel bayrakları taşıyan üst ayrıştırıcı; köke ve her komuta `parents` ile eklenir. Varsayılanlar
    SUPPRESS'tir: bayrak verilmediyse ad alanında hiç bulunmaz, böylece komuttan önce verilen değer komutun
    ayrıştırıcısının varsayılanıyla ezilmez.
    """
    absent = argparse.SUPPRESS
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    group = parser.add_argument_group(t("ssc_global_options_title"))
    group.add_argument("--config", metavar="PATH", default=absent, help=t("ssc_help_config"))
    group.add_argument("--data-dir", dest="data_dir", metavar="PATH", default=absent, help=t("ssc_help_data_dir"))
    group.add_argument("--json", dest="output", action="store_const", const=JSON, default=absent, help=t("ssc_help_json"))
    group.add_argument("--output", choices=OUTPUT_MODES, default=absent, help=t("ssc_help_output"))
    group.add_argument("--quiet", action="store_true", default=absent, help=t("ssc_help_quiet"))
    group.add_argument("--verbose", action="store_true", default=absent, help=t("ssc_help_verbose"))
    group.add_argument(
        "--log-level", dest="log_level", type=str.upper, choices=LOG_LEVELS, metavar="LEVEL", default=absent,
        help=t("ssc_help_log_level"),
    )
    group.add_argument(
        "--log-format", dest="log_format", type=str.lower, choices=LOG_FORMATS, default=absent,
        help=t("ssc_help_log_format"),
    )
    group.add_argument("--no-color", dest="no_color", action="store_true", default=absent, help=t("ssc_help_no_color"))
    group.add_argument("--lang", choices=language.SUPPORTED_LANGUAGES, default=absent, help=t("ssc_help_lang"))
    group.add_argument("--rate", type=_rate, metavar="N|off", default=absent, help=t("ssc_help_rate"))
    group.add_argument(
        "--ignore-breaker", dest="ignore_breaker", action="store_true", default=absent, help=t("ssc_help_ignore_breaker"),
    )
    group.add_argument("--wait", type=_seconds, metavar="SECONDS", default=absent, help=t("ssc_help_wait"))
    group.add_argument("--progress", choices=PROGRESS_MODES, default=absent, help=t("ssc_help_progress"))
    return parser


def _add_help(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help=t("ssc_help_help"))


def build_tree(t: Translator, prog: str = PROG) -> ParserTree:
    """Kayıtlı komutlardan argparse ağacını kurar. Yardım metinleri `t` ile çevrilir."""
    registry.load()
    undeclared = registry.missing_groups()
    if undeclared:
        raise RuntimeError(f"command group not declared with group(): {', '.join(undeclared)}")

    common = _global_options(t)
    formatter = argparse.RawDescriptionHelpFormatter
    root = _Parser(
        prog=prog,
        description=t("ssc_description", sports=", ".join(sport_slugs())),
        epilog=t("ssc_epilog"),
        formatter_class=formatter,
        add_help=False,
        allow_abbrev=False,
        parents=[common],
    )
    _add_help(root, t)
    root.add_argument("--version", action="version", version=VERSION_TEXT, help=t("ssc_help_version"))
    root.set_defaults(_command=None, _group=())

    parsers: Dict[registry.CommandPath, argparse.ArgumentParser] = {(): root}
    children: Dict[registry.CommandPath, Any] = {}

    def subparsers_of(path: registry.CommandPath) -> Any:
        if path not in children:
            children[path] = parsers[path].add_subparsers(
                title=t("ssc_commands_title"), metavar="COMMAND", parser_class=_Parser,
            )
        return children[path]

    entries: List[Tuple[registry.CommandPath, Any]] = [(entry.path, entry) for entry in registry.groups()]
    entries += [(entry.path, entry) for entry in registry.commands()]
    for path, entry in sorted(entries, key=lambda item: item[0]):  # üst yol her zaman alt yoldan önce gelir
        text = t(entry.help)
        description = t(entry.description) if getattr(entry, "description", None) else text
        parser = subparsers_of(path[:-1]).add_parser(
            path[-1], help=text, description=description, formatter_class=formatter, add_help=False,
            allow_abbrev=False, parents=[common],
        )
        parser.command_path = " ".join(path)
        _add_help(parser, t)
        parsers[path] = parser
        if isinstance(entry, registry.Command):
            if entry.configure is not None:
                entry.configure(parser, t)
            parser.set_defaults(_command=path)
        else:
            parser.set_defaults(_command=None, _group=path)
    # Kullanım satırları en sonda yazılır: alt ayrıştırıcıların adı (prog) üst ayrıştırıcının kendiliğinden
    # üretilen kullanım satırından türetilir.
    for parser in parsers.values():
        parser.usage = _usage(parser, t)
    return ParserTree(root=root, parsers=parsers, global_actions=list(common._actions))


def _usage(parser: argparse.ArgumentParser, t: Translator) -> str:
    """
    Kısa kullanım satırı: genel bayraklar tek tek sayılmaz ("[global options]"), yalnızca komutun kendi
    seçenekleri yazılır. Tek satırdır; terminal genişliğine bağlı değildir.
    """
    own = [
        action for action in parser._actions
        if action.dest not in GLOBAL_DESTS and not isinstance(action, argparse._HelpAction)
    ]
    formatter = parser._get_formatter()
    formatter.add_usage(None, own, [], prefix="")
    generated = " ".join(formatter.format_help().split())
    rest = generated[len(parser.prog):].strip() if generated.startswith(parser.prog) else ""
    label = t("ssc_usage_global")
    parts = [parser.prog, f"[{label}]", rest]
    return " ".join(part for part in parts if part).replace("%", "%%")


# --- `describe commands`: ağacın makinece okunur özeti ---------------------------------------------


def _describe_action(action: argparse.Action) -> Dict[str, Any]:
    default = action.default
    return {
        "name": action.dest,
        "flags": list(action.option_strings),
        "help": action.help if action.help is not argparse.SUPPRESS else None,
        "takes_value": action.nargs != 0,
        "nargs": action.nargs,
        "metavar": action.metavar if isinstance(action.metavar, str) else None,
        "choices": list(action.choices) if action.choices is not None else None,
        "required": bool(action.required),
        "default": None if default is argparse.SUPPRESS else default,
    }


def _is_plumbing(action: argparse.Action) -> bool:
    """Yardım eylemi ve alt komut seçicisi: komutun seçeneği değildir."""
    return isinstance(action, (argparse._HelpAction, argparse._SubParsersAction))


def describe_commands(t: Translator) -> Dict[str, Any]:
    """
    Komutlar, seçenekleri ve genel bayraklar; argparse ağacından üretilir, bu yüzden uygulamadan sapamaz.
    `t` İngilizce çeviri işlevi olmalıdır: bu çıktı yerelleştirilmez.
    """
    tree = build_tree(t, PROG)
    version = [action for action in tree.root._actions if isinstance(action, argparse._VersionAction)]
    described = []
    for cmd in registry.commands():
        own = [
            action for action in tree.parsers[cmd.path]._actions
            if not _is_plumbing(action) and action.dest not in GLOBAL_DESTS
        ]
        described.append({
            "name": cmd.name,
            "help": t(cmd.help),
            "always_json": cmd.always_json,
            "loads_settings": cmd.settings,
            "arguments": [_describe_action(action) for action in own if not action.option_strings],
            "options": [_describe_action(action) for action in own if action.option_strings],
        })
    return {
        "prog": PROG,
        "usage": f"{PROG} [global options] COMMAND [options]",
        "global_options": [
            _describe_action(action) for action in tree.global_actions + version if not _is_plumbing(action)
        ],
        "groups": [{"name": entry.name, "help": t(entry.help)} for entry in registry.groups()],
        "commands": described,
    }


# --- genel bayraklar -------------------------------------------------------------------------------


def _prescan(argv: Sequence[str]) -> Tuple[str, Optional[str]]:
    """
    Ayrıştırmadan önce çıktı kipi ve dil: yardım metni ve kullanım hatası da doğru dilde ve doğru biçimde
    (`--json` ile zarf olarak) yazılabilsin.
    """
    mode, lang = TEXT, None
    for index, arg in enumerate(argv):
        following = argv[index + 1] if index + 1 < len(argv) else None
        if arg == "--":
            break
        if arg == "--json":
            mode = JSON
        elif arg == "--output" and following in OUTPUT_MODES:
            mode = str(following)
        elif arg.startswith("--output=") and arg.split("=", 1)[1] in OUTPUT_MODES:
            mode = arg.split("=", 1)[1]
        elif arg == "--lang" and following in language.SUPPORTED_LANGUAGES:
            lang = following
        elif arg.startswith("--lang=") and arg.split("=", 1)[1] in language.SUPPORTED_LANGUAGES:
            lang = arg.split("=", 1)[1]
    return mode, lang


def _options(namespace: argparse.Namespace) -> GlobalOptions:
    given = [name for name in ("quiet", "verbose", "log_level") if getattr(namespace, name, None)]
    if len(given) > 1:
        flags = ", ".join("--" + name.replace("_", "-") for name in given)
        raise UsageError(f"give only one of {flags}")
    level = getattr(namespace, "log_level", None)
    if getattr(namespace, "quiet", False):
        level = "ERROR"
    elif getattr(namespace, "verbose", False):
        level = "DEBUG"
    return GlobalOptions(
        config=getattr(namespace, "config", None),
        data_dir=getattr(namespace, "data_dir", None),
        output=getattr(namespace, "output", TEXT),
        quiet=bool(getattr(namespace, "quiet", False)),
        log_level=level,
        no_color=bool(getattr(namespace, "no_color", False)),
        lang=getattr(namespace, "lang", None),
        rate=getattr(namespace, "rate", None),
        ignore_breaker=bool(getattr(namespace, "ignore_breaker", False)),
        log_format=getattr(namespace, "log_format", None),
        wait=getattr(namespace, "wait", None),
        progress=getattr(namespace, "progress", None),
    )


def _setting_flags(options: GlobalOptions, data_dir: Optional[str]) -> Dict[str, Any]:
    """Genel bayrakların ayar karşılıkları: yükleyicinin en güçlü katmanı (sofascore_scraper/config/loader.py, `flags`)."""
    flags: Dict[str, Any] = {}
    if data_dir is not None:
        flags["storage.data_dir"] = data_dir
    if options.rate is not None:
        flags["client.rate"] = options.rate
    if options.ignore_breaker:
        flags["breaker.ignore"] = True
    if options.log_level is not None:
        # `log.debug` (DEBUG=true) seviyeyi DEBUG'a zorlar; açıkça istenen seviye onun da önündedir
        flags["log.level"] = options.log_level
        flags["log.debug"] = False
    if options.log_format is not None:
        flags["log.format"] = options.log_format
    if options.no_color:
        flags["display.use_color"] = False
    if options.lang is not None:
        flags["display.language"] = options.lang
    return flags


# --- hata yazımı -----------------------------------------------------------------------------------


def _redact(text: str) -> str:
    """Bilinen gizli değerleri (proxy parolası, belirteç) maskeler; maskeleme modülü yüklenemiyorsa metin aynen kalır."""
    try:
        from sofascore_scraper.redact import redact_text

        return redact_text(text)
    except Exception:
        return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, Mapping):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _report(out: Output, command: Optional[str], exc: BaseException) -> int:
    """İstisnayı hata tablosundaki koda bağlar, doğru akışa yazar ve çıkış kodunu döndürür."""
    if isinstance(exc, ModuleNotFoundError) and not (exc.name or "").startswith("sofascore_scraper"):
        error = PlatformError(
            INTERNAL, f"a required package is not installed ({exc}); run `{out.prog} doctor` to see what is missing",
        )
    elif isinstance(exc, signals.Terminated):
        error = Cancelled("cancelled by a termination signal (SIGTERM)", signal_number=signals.SIGTERM)
    else:
        error = to_platform_error(exc)
        if error.code == INTERNAL:
            # Beklenmeyen hata bir programlama hatasıdır: iz dökümü stderr'e (stdout'ta yalnızca zarf kalır)
            trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            out.info(_redact(trace).rstrip())
    code = exit_code_for(error)
    shown = PlatformError(error.code, _redact(error.message), _redact_value(error.details) if error.details else None)
    out.error(command, shown, code)
    return code


# --- giriş noktası ---------------------------------------------------------------------------------


def _default_prog() -> str:
    """Yardımda görünen komut adı: `ssc` olarak çağrıldıysa o, değilse modül biçimi."""
    name = os.path.splitext(os.path.basename(sys.argv[0] or ""))[0].lower()
    return PROG if name == PROG else MODULE_PROG


def _merge_warnings(inv: registry.Invocation, result: CommandResult) -> None:
    merged = {(warning.code, warning.message): warning for warning in [*inv.warnings, *result.warnings]}
    result.warnings = list(merged.values())


def main(argv: Optional[Sequence[str]] = None, *, prog: Optional[str] = None) -> int:
    """
    CLI'yi çalıştırır ve çıkış kodunu döndürür (sofascore_scraper/cli/exit_codes.py). `argv` verilmezse sys.argv okunur.
    Hiçbir zaman istisna sızdırmaz: her hata tablodaki bir kodla yazılır.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    invoked_cwd = os.getcwd()
    os.chdir(PROJECT_ROOT)

    mode, wanted_lang = _prescan(arguments)
    out = Output(mode=mode, prog=prog or _default_prog())
    command_name: Optional[str] = None
    try:
        t, lang = translator(wanted_lang)
        out.t = t
        tree = build_tree(t, out.prog)
        # 2.x'in `main.py` bayrakları (3.1'de kalktı): hiçbir şey çalışmaz, ileti yerine geçen komutu söyler
        removed = removed_flags.removed_in(
            arguments, (entry.path[0] for entry in [*registry.commands(), *registry.groups()]))
        if removed:
            raise UsageError(removed_flags.message(removed, PROG), {"removed": removed})
        namespace, unknown = tree.root.parse_known_args(arguments)
        if unknown:
            # Hatayı komutun kendi ayrıştırıcısı bildirir: kullanım satırı o komutunkidir
            reached = namespace._command if namespace._command is not None else namespace._group
            tree.parsers[reached].error(f"unrecognized arguments: {' '.join(unknown)}")
        options = _options(namespace)
        out.mode, out.quiet = options.output, options.quiet

        path = namespace._command
        if path is None:
            # Komut verilmedi: yardım metni stderr'e, çıkış kodu 2 (02-services.md 4.7)
            group = " ".join(namespace._group) or None
            command_name = group
            raise UsageError("a command is required", {"usage": tree.parsers[namespace._group].format_help().strip()})
        command = registry.find(path)
        assert command is not None  # ağaç kayıttan kuruldu
        command_name = command.name

        if options.no_color:
            os.environ["NO_COLOR"] = "1"
        inv = registry.Invocation(
            args=namespace,
            out=out,
            t=t,
            lang=lang,
            cwd=invoked_cwd,
            describe_commands=describe_commands,
            translator=translator,
        )
        inv.config_file = inv.resolve_path(options.config) if options.config else None
        inv.flags = _setting_flags(options, inv.resolve_path(options.data_dir) if options.data_dir else None)
        with signals.terminate_as_interrupt():
            if command.settings:
                registry.activate_settings(inv)
                _apply_log_format()
            result = command.run(inv)
        _merge_warnings(inv, result)
        out.result(command_name, result, always_json=command.always_json)
        return int(result.exit_code)
    except SystemExit as stop:
        # argparse: --help ve --version çıktıyı kendisi yazar ve 0 ile çıkar
        code = stop.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    except BrokenPipeError:
        return _closed_pipe(out)
    except (Exception, KeyboardInterrupt) as exc:
        command_name = getattr(exc, "command_path", None) or command_name
        try:
            return _report(out, command_name, exc)
        except BrokenPipeError:
            return _closed_pipe(out)


def _apply_log_format() -> None:
    """`--log-format` ya da `[log] format`: konsol log satırlarının biçimi (sofascore_scraper/logger.py)."""
    from sofascore_scraper import logger as app_logger
    from sofascore_scraper.config import loader

    app_logger.set_log_format(str(loader.active_settings().log.format or "text"))


def _closed_pipe(out: Output) -> int:
    """
    Çıktıyı okuyan süreç kapandı (ör. `ssc describe | head`): yazacak yer kalmadı. stdout kapatılır ki
    yorumlayıcı kapanırken kalan tamponu yazmayı deneyip ikinci bir hata basmasın.

    Akış komutunda (`ssc events | head -1`) okuyan yeterince satır almıştır: çıkış kodu 0. Tek seferlik
    komutun sonucu ise okunamadı: 1.
    """
    try:
        sys.stdout.close()
    except Exception:
        pass
    return OK if out.streaming else GENERAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
