"""
Komut kaydı (docs/design/02-services.md bölüm 4.1): her komut bu paketin altındaki bir modüldür ve kendini
kaydeder. Yeni bir komut ekleyen iş bir dosya ekler; ortak bir dosyayı düzenlemez.

    from src.cli.commands import CommandResult, Invocation, command, group

    group("config", help="ssc_help_cmd_config")

    @command("config show", help="ssc_help_cmd_config_show", configure=_show_arguments, settings=True)
    def config_show(inv: Invocation) -> CommandResult:
        ...
        return CommandResult(data={...}, text="...")

  * Ad, boşlukla ayrılmış yoldur ("config show"). Üst düzeyi (`config`) `group` ile bildirilir.
  * `help` ve `description` locale anahtarlarıdır (locales/*.json); `describe commands` İngilizcesini yazar.
  * `configure(parser, t)` komutun kendi seçeneklerini ekler. Genel bayraklar (`--json`, `--config`...)
    her komuta ana modül tarafından eklenir.
  * `settings=True`: komut çalışmadan önce ayarlar yüklenir (yapılandırma dosyası, `.env`, ortam, bayraklar)
    ve loglar stderr'e yönlendirilir. Bozuk bir yapılandırma dosyası `config_invalid` (çıkış kodu 2) olur.
  * Komut sonucu döndürür, kendisi yazdırmaz; hatayı `PlatformError` (src/errors.py) olarak fırlatır.

Modüller yüklenirken hafif kalmalıdır: ağır içe aktarmalar (istek katmanı, Store, pandas) komutun işlevinin
içinde yapılır. `ssc --version` ve `ssc doctor` paketler kurulmadan da çalışır.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src.cli.output import CliWarning, CommandResult, Output, Translator

CommandPath = Tuple[str, ...]
Configure = Callable[[argparse.ArgumentParser, Translator], None]


@dataclass
class Invocation:
    """
    Bir komut çağrısı: ayrıştırılmış argümanlar ve komutun ihtiyaç duyabileceği ortam.

    args          ayrıştırılmış argümanlar (komutun kendi seçenekleri)
    out           çıktı (kip, akışlar); komutlar sonuç döndürür, buraya yalnızca ara satır gerekirse yazar
    t             çeviri işlevi (seçilen dil)
    lang          seçilen dil ("en" | "tr")
    cwd           kullanıcının komutu çalıştırdığı dizin: göreli yollar buna göre çözülür
    config_file   `--config` ile verilen dosya (mutlak yol) ya da None
    flags         ayar bayrakları, yükleyicinin beklediği biçimde: {"storage.data_dir": "/veri", "client.rate": 2}
    describe_commands  argparse ağacının makinece okunur özeti (`describe commands`); çeviri işlevi alır
    translator    bir dil için (çeviri işlevi, dil) veren işlev; İngilizce çıktı ya da dil değişimi için
    warnings      ayarlar yüklenirken oluşan uyarılar; sonuç zarfına eklenir
    """

    args: argparse.Namespace
    out: Output
    t: Translator
    lang: str
    cwd: str
    config_file: Optional[str] = None
    flags: Mapping[str, Any] = field(default_factory=dict)
    describe_commands: Optional[Callable[[Translator], Dict[str, Any]]] = None
    translator: Optional[Callable[[Optional[str]], Tuple[Translator, str]]] = None
    warnings: List[CliWarning] = field(default_factory=list)

    def resolve_path(self, path: str) -> str:
        """Kullanıcının verdiği yol, komutu çalıştırdığı dizine göre mutlak yol olarak (`~` açılır)."""
        return os.path.abspath(os.path.join(self.cwd, os.path.expanduser(path)))


Run = Callable[[Invocation], CommandResult]


@dataclass(frozen=True)
class Command:
    path: CommandPath
    help: str
    run: Run
    configure: Optional[Configure] = None
    description: Optional[str] = None
    settings: bool = False
    always_json: bool = False

    @property
    def name(self) -> str:
        return " ".join(self.path)


@dataclass(frozen=True)
class Group:
    path: CommandPath
    help: str

    @property
    def name(self) -> str:
        return " ".join(self.path)


_commands: Dict[CommandPath, Command] = {}
_groups: Dict[CommandPath, Group] = {}
_loaded = False


def _path(name: str) -> CommandPath:
    path = tuple(name.split())
    if not path or any(not part.replace("-", "").isalnum() or part != part.lower() for part in path):
        raise ValueError(f"invalid command name: {name!r}")
    return path


def group(name: str, *, help: str) -> Group:
    """Alt komutları olan bir üst komut (`config`). Aynı ad iki kez bildirilemez."""
    path = _path(name)
    if path in _groups or path in _commands:
        raise ValueError(f"command already registered: {name}")
    _groups[path] = Group(path, help)
    return _groups[path]


def register(cmd: Command) -> Command:
    if cmd.path in _commands or cmd.path in _groups:
        raise ValueError(f"command already registered: {cmd.name}")
    if any(cmd.path[:depth] in _commands for depth in range(1, len(cmd.path))):
        raise ValueError(f"{cmd.name}: a parent of this command is itself a command")
    _commands[cmd.path] = cmd
    return cmd


def command(
    name: str,
    *,
    help: str,
    configure: Optional[Configure] = None,
    description: Optional[str] = None,
    settings: bool = False,
    always_json: bool = False,
) -> Callable[[Run], Run]:
    """Bir işlevi komut olarak kaydeden dekoratör."""

    def decorate(run: Run) -> Run:
        register(Command(
            path=_path(name), help=help, run=run, configure=configure, description=description,
            settings=settings, always_json=always_json,
        ))
        return run

    return decorate


def logs_to_stderr() -> None:
    """
    Uygulamanın konsol log satırlarını stderr'e yönlendirir: yeni CLI'de stdout yalnızca sonucu taşır
    (02-services.md 4.4). Log modülünü (rich, dotenv) içe aktarır; bu yüzden yalnızca uygulama kodunu
    çalıştıran komutlar çağırır.
    """
    from src import logger as app_logger

    app_logger.set_console_stream("stderr")


def activate_settings(inv: Invocation) -> None:
    """
    Sürecin ayarlarını yükler: `--config` ile verilen (ya da aranan) yapılandırma dosyası, `.env`, ortam ve
    komut satırı bayrakları (src/config/loader.py). Bozuk bir dosya ConfigError'dır; çağıran onu
    `config_invalid` olarak yazar. Yükleme uyarıları (ör. live.source = "direct") çağrıya eklenir; aynı
    metinler log satırı olarak da yazıldığı için metin kipinde yinelenmez.
    """
    logs_to_stderr()
    from src.config import loader

    level = inv.flags.get("log.level")
    if level:
        # `--quiet`, `--verbose`, `--log-level`: seviye ilk log satırından önce geçerli olsun. Yükleyici aynı
        # değeri ortama yazdığında seviye zaten yerindedir; "seviye değişti" satırı yazılmaz.
        logging.getLogger().setLevel(getattr(logging, str(level)))
    loaded = loader.activate(config_file=inv.config_file, flags=dict(inv.flags))
    inv.warnings.extend(CliWarning(warning.code, warning.message, logged=True) for warning in loaded.warnings)
    language = loaded.settings.display.language
    if language != inv.lang and inv.translator is not None:
        # Dil yapılandırma dosyasında da verilebilir ([display] language); komutun metni o dilde yazılır.
        # Yardım metni ve ayarlar yüklenmeden oluşan hatalar ortamdaki dile (APP_LANGUAGE, sistem dili) uyar.
        inv.t, inv.lang = inv.translator(language)
        inv.out.t = inv.t


def read_settings(inv: Invocation, *, config_file: bool = True) -> Any:
    """
    Ayarları sürece dokunmadan okur ve `LoadedSettings` döndürür (src/config/loader.load_settings): ortama
    yazmaz, log kurmaz, etkin ayarları değiştirmez. `config validate`, `doctor` ve `config init` bunu kullanır.

    Yükleyici `.env`'in ortama yüklenmiş olmasını bekler (uygulama onu başlangıçta python-dotenv ile yükler);
    burada aynı görünüm ortamı değiştirmeden kurulur: süreç ortamı, altında `.env`.

    config_file=False: yapılandırma dosyası hesaba katılmaz (yalnızca bugünkü kaynaklar).
    """
    import dotenv

    from src.config import loader
    from src.paths import env_file_path

    try:
        file_values = {key: value for key, value in dotenv.dotenv_values(env_file_path()).items() if value is not None}
    except Exception:
        file_values = {}
    return loader.load_settings(
        config_file=(inv.config_file or loader.AUTO) if config_file else None,
        environ={**file_values, **os.environ},
        dotenv_values=file_values,
        flags=dict(inv.flags),
    )


def load() -> None:
    """Bu paketin altındaki her modülü içe aktarır (adı `_` ile başlayanlar hariç); modüller kendini kaydeder."""
    global _loaded
    if _loaded:
        return
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")
    _loaded = True


def commands() -> List[Command]:
    """Kayıtlı komutlar, ada göre sıralı."""
    load()
    return [_commands[path] for path in sorted(_commands)]


def groups() -> List[Group]:
    load()
    return [_groups[path] for path in sorted(_groups)]


def find(path: CommandPath) -> Optional[Command]:
    load()
    return _commands.get(tuple(path))


def missing_groups() -> List[str]:
    """Alt komutu olan ama `group` ile bildirilmemiş üst adlar (yardım metni olmazdı)."""
    load()
    needed = {cmd.path[:depth] for cmd in _commands.values() for depth in range(1, len(cmd.path))}
    return sorted(" ".join(path) for path in needed - set(_groups))


__all__ = [
    "CliWarning",
    "Command",
    "CommandResult",
    "Group",
    "Invocation",
    "activate_settings",
    "command",
    "commands",
    "find",
    "group",
    "groups",
    "load",
    "logs_to_stderr",
    "missing_groups",
    "read_settings",
    "register",
]
