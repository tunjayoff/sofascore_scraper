"""
`serve`: HTTP API'yi ve web arayüzünü ön planda çalıştırır (docs/design/02-services.md bölüm 4.1 ve 4.6; plan
maddesi P25). 2.x'in `main.py --web`inin (3.1'de kalktı) ve doğrudan uvicorn başlatmalarının yerini alır; başlatıcılar
(scripts/start_web.py) ve Docker giriş noktası bu komutu çalıştırır.

    ssc serve                                       127.0.0.1:8000 ya da [server] host / port
    ssc serve --port 9000
    ssc serve --host 0.0.0.0 --allowed-hosts localhost,127.0.0.1,sunucum.lan
    ssc serve --dev                                 kod değişince yeniden başlar (geliştirme)

Host izin listesi (DNS rebinding; kurallar PR #43'ündür, sofascore_scraper/web/security.allowed_hosts_for_bind):

  * Kullanıcının verdiği liste (`--allowed-hosts`, `[server] allowed_hosts`, SOFASCORE_SERVER__ALLOWED_HOSTS,
    `.env`) yazıldığı gibi kullanılır; hiçbir zaman üzerine yazılmaz.
  * Yerel adres (127.0.0.1, localhost, ::1): yalnızca yerel adlar.
  * Belirli bir yerel olmayan adres (192.168.1.5): yerel adlar ve o adres.
  * Her arayüz (0.0.0.0, ::): liste verilmeden başlamaz (çıkış kodu 2); `--allow-any-host` açık ve güvensiz
    seçenektir ("*").
  * Yerel olmayan bir adreste erişim belirteci yoksa (SOFASCORE_SERVER__TOKEN ya da `[server] token_env`) bir
    uyarı yazılır. Uygulama, adresin dışarıya nasıl yayımlandığını göremez (Docker `-p 127.0.0.1:...`): uyarı
    o durumda da yazılır; dağıtım belgesi (docs/deploy/) ne zaman yok sayılabileceğini söyler (karar D17).

Erişim belirteci komut satırından verilmez (süreç listesinde görünürdü): ayarlardan okunur.

Canlı izleme bu komutta yoktur (sahip kararı, 2026-10-01): `ssc watch` ayrı bir süreçtir. Yapılandırılmış
sink'ler (`[[sink]]`, `SOFASCORE_SINKS`) ise bu süreçte, ayrı bir thread'de dağıtılır (`watch` gibi; ikisi aynı
veri dizininde çalışırsa `sinks` kilidini hangisi alırsa o dağıtır).

Uygulama içi zamanlayıcı (plan maddesi P29; sofascore_scraper/jobs/scheduler.py) isteğe bağlıdır ve varsayılan olarak
kapalıdır: `--scheduler` ya da `[schedule] enabled = true` açar, `--no-scheduler` ayarı bu çalıştırma için
kapatır. Görevler `[[schedule.task]]`tandır; bilinmeyen bir görev ya da seçenek sunucu başlamadan reddedilir
(config_invalid, çıkış kodu 2). Görev yoksa zamanlayıcı başlamaz (uyarı). Zamanlanmış çalışmalar web
sürecinin iş yöneticisiyle başlatılan sıradan işlerdir (`origin=scheduler`); sonraki çalışmalar
`/api/v1/status`'ta görünür. `--dev` ile zamanlayıcı çalışmaz: yeniden yükleyen sunucu uygulamayı bir alt
süreçte çalıştırır ve zamanlayıcıyı göremezdi (`--scheduler --dev` kullanım hatasıdır; ayardan açıksa uyarı).

Durdurma: Ctrl+C ya da SIGTERM sunucuyu düzgünce kapatır (uvicorn), sink'lerin birikenleri en çok 10 sn teslim
edilir, zamanlayıcının başlattığı ve süren iş iptal edilir (en çok 30 sn beklenir); çıkış kodu 0 (uzun çalışan
servis, bölüm 4.5). Sunucu başlayamazsa (port kullanımda) çıkış kodu 1.

Ağır içe aktarmalar (uvicorn, ayarlar, Store, sink'ler) işlevin içindedir (sofascore_scraper/cli/commands/__init__.py).
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from sofascore_scraper.cli.commands import CliWarning, CommandResult, Invocation, command
from sofascore_scraper.cli.output import Translator
from sofascore_scraper.errors import UsageError

logger = logging.getLogger(__name__)

# Web uygulamasının içe aktarma yolu (uvicorn `--reload` ile yalnızca bu biçimi kabul eder)
APP = "sofascore_scraper.web.app:app"
# `--dev`: yalnızca uygulama kodu izlenir; data/ altındaki yazımlar sunucuyu indirme ortasında yeniden başlatmasın
RELOAD_DIRS = ("sofascore_scraper", "locales")
SINK_JOIN_SECONDS = 15.0  # dağıtıcı dururken birikenleri en çok 10 sn teslim eder; üstüne pay
EXPOSED_WITHOUT_TOKEN = "exposed_without_token"
SERVER_FAILED = "server_failed"
SCHEDULER_NO_TASKS = "scheduler_no_tasks"
SCHEDULER_NOT_IN_DEV = "scheduler_not_in_dev"
# Her arayüz adresleri (sofascore_scraper/web/security.py ile aynı)
WILDCARD_BINDS = ("", "0.0.0.0", "::", "[::]")

# İzin listesinin nereden geldiği (Bind.origin)
FROM_FLAG = "flag"          # --allowed-hosts
FROM_SETTINGS = "settings"  # yapılandırma dosyası, ortam, .env, overrides.json
FROM_BIND = "bind"          # adresten türetildi (belirli adres: yerel adlar + o adres; --allow-any-host: "*")
FROM_DEFAULT = "default"    # yerel adres: yalnızca yerel adlar


def _port(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = -1
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError(f"expected a port number from 0 to 65535, got {value!r}")
    return number


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--host", metavar="ADDRESS", help=t("ssc_help_serve_host"))
    parser.add_argument("--port", type=_port, metavar="PORT", help=t("ssc_help_serve_port"))
    parser.add_argument("--allowed-hosts", dest="allowed_hosts", metavar="NAMES",
                        help=t("ssc_help_serve_allowed_hosts"))
    parser.add_argument("--allow-any-host", dest="allow_any_host", action="store_true",
                        help=t("ssc_help_serve_allow_any_host"))
    parser.add_argument("--dev", action="store_true", help=t("ssc_help_serve_dev"))
    scheduler = parser.add_mutually_exclusive_group()
    scheduler.add_argument("--scheduler", dest="scheduler", action="store_true",
                           help=t("ssc_help_serve_scheduler"))
    scheduler.add_argument("--no-scheduler", dest="no_scheduler", action="store_true",
                           help=t("ssc_help_serve_no_scheduler"))


@dataclass(frozen=True)
class Bind:
    """Sunucunun dinleyeceği adres ve yanıt vereceği Host adları."""

    host: str
    port: int
    allowed_hosts: Tuple[str, ...]
    origin: str
    loopback: bool

    @property
    def explicit(self) -> bool:
        """Listeyi kullanıcı mı verdi (bayrak ya da ayar)?"""
        return self.origin in (FROM_FLAG, FROM_SETTINGS)

    @property
    def url(self) -> str:
        """Kullanıcının tarayıcıda açacağı adres. Her arayüzde dinleyen sunucuya bu bilgisayardan ulaşılır."""
        name = self.host.strip()
        if name in WILDCARD_BINDS:
            name = "localhost"
        elif ":" in name and not name.startswith("["):
            name = f"[{name}]"  # IPv6 adresi URL'de köşeli ayraçla yazılır
        return f"http://{name}:{self.port}"


def resolve_bind(host: str, port: int, explicit: Optional[str], allow_any: bool, *, from_flag: bool = False) -> Bind:
    """
    Adres ve izin listesi (PR #43'ün kuralları, sofascore_scraper/web/security.allowed_hosts_for_bind). `explicit`: kullanıcının
    verdiği liste (virgülle ayrılmış) ya da None; `from_flag`: o liste `--allowed-hosts` ile mi verildi. Her
    arayüzde dinlenecek ve liste yoksa UsageError (çıkış kodu 2).
    """
    from sofascore_scraper.web import security

    try:
        derived = security.allowed_hosts_for_bind(host, explicit, allow_any=allow_any)
    except security.AllowedHostsRequired:
        raise UsageError(
            f"--host {host} listens on every network interface, but no allowed host names are set: give "
            f"--allowed-hosts, [server] allowed_hosts or {security.ALLOWED_HOSTS_ENV}, or --allow-any-host "
            f"(insecure: it turns off the protection against DNS rebinding)",
            {"host": host, "setting": "server.allowed_hosts"},
        ) from None
    given = security.parse_hosts(explicit)
    if given:
        hosts, origin = tuple(given), FROM_FLAG if from_flag else FROM_SETTINGS
    elif derived is not None:
        hosts, origin = tuple(security.parse_hosts(derived)), FROM_BIND
    else:
        hosts, origin = tuple(security.LOOPBACK_HOSTS), FROM_DEFAULT
    return Bind(host=host, port=port, allowed_hosts=hosts, origin=origin, loopback=security.is_loopback_bind(host))


def _explicit_hosts(inv: Invocation, loaded: Any) -> Optional[str]:
    """Kullanıcının verdiği izin listesi: `--allowed-hosts`, yoksa varsayılandan farklı bir kaynaktaki ayar."""
    from sofascore_scraper.config import loader

    if inv.args.allowed_hosts is not None:
        return str(inv.args.allowed_hosts)
    if loaded.source("server.allowed_hosts").layer == loader.LAYER_DEFAULT:
        return None
    return ",".join(loaded.settings.server.allowed_hosts)


def _apply_allowed_hosts(inv: Invocation, bind: Bind) -> None:
    """
    Türetilen ya da `--allowed-hosts` ile verilen izin listesini ayarlara verir: web uygulaması onu
    `server.allowed_hosts`ten okur (sofascore_scraper/web/security.py). `--allowed-hosts` ayarların en güçlü katmanına
    (bayrak) girer; adresten türetilen liste ve bayrak ayrıca ortama da yazılır (SOFASCORE_SERVER__ALLOWED_HOSTS):
    `--dev`in yeniden yükleyen alt süreci uygulamayı yeniden kurar ve listeyi ortamdan alır. Ayarlardan gelen
    listeye ve yerel adrese (varsayılan: yerel adlar) dokunulmaz.
    """
    from sofascore_scraper.config import loader
    from sofascore_scraper.web import security

    value = ",".join(bind.allowed_hosts)
    if bind.origin == FROM_FLAG:
        inv.flags = {**dict(inv.flags), "server.allowed_hosts": value}
        loader.activate(config_file=inv.config_file, flags=dict(inv.flags))
    if bind.origin in (FROM_FLAG, FROM_BIND) and os.environ.get(security.ALLOWED_HOSTS_ENV) != value:
        os.environ[security.ALLOWED_HOSTS_ENV] = value  # yükleyici ortamın değişimini görür


def _token_warning(inv: Invocation, bind: Bind, token: str) -> List[CliWarning]:
    """Yerel olmayan adres, belirteç yok: tek ve açık uyarı (log seviyesi kapatmışsa yine de stderr'e)."""
    if bind.loopback or token:
        return []
    message = (
        f"the web app is listening on {bind.host}:{bind.port} without an access token: anyone who can reach this "
        f"port can read and delete the data and change the settings. Set SOFASCORE_SERVER__TOKEN (or [server] "
        f"token_env), or keep the port behind a firewall or a reverse proxy you control"
    )
    logged = logger.isEnabledFor(logging.WARNING)
    if logged:
        logger.warning(message)
    else:
        inv.out.info(inv.t("ssc_serve_exposed_without_token", host=bind.host, port=bind.port))
    return [CliWarning(EXPOSED_WITHOUT_TOKEN, message, logged=True)]


def _uvicorn_options(bind: Bind, dev: bool) -> Dict[str, Any]:
    from sofascore_scraper.cli.main import PROJECT_ROOT

    options: Dict[str, Any] = {"host": bind.host, "port": bind.port}
    if dev:
        options["reload"] = True
        options["reload_dirs"] = [str(PROJECT_ROOT / name) for name in RELOAD_DIRS]
    return options


def log_config() -> Dict[str, Any]:
    """
    uvicorn'un log ayarı, erişim satırları stderr'e: CLI'de stdout yalnızca sonucu taşır (02-services.md 4.4).
    uvicorn varsayılan olarak erişim satırlarını stdout'a yazar.
    """
    import copy

    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    for handler in config.get("handlers", {}).values():
        if handler.get("stream") == "ext://sys.stdout":
            handler["stream"] = "ext://sys.stderr"
    return config


def run_server(options: Dict[str, Any]) -> None:
    """Sunucuyu çalıştırır ve durunca döner (testler bunu değiştirir; sunucu başlatmazlar)."""
    import uvicorn

    uvicorn.run(APP, log_config=log_config(), **options)


def _start_thread(target: Callable[[], None], name: str) -> threading.Thread:
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread


def _sink_host(settings: Any, data_dir: str) -> Optional[Any]:
    """Yapılandırılmış sink'lerin dağıtıcısı; sink yoksa None (Store açılmaz). Bozuk sink ayarı: config_invalid."""
    if not settings.sinks:
        return None
    from sofascore_scraper import sinks
    from sofascore_scraper.store import open_store

    return sinks.dispatcher_for(open_store(data_dir), settings.sinks)


def _stop_sinks(dispatcher: Any, thread: Optional[threading.Thread], stop: threading.Event) -> None:
    """Dağıtıcıyı durdurur: thread süre sınırıyla beklenir; asılı kaldıysa sink'ler yalnızca kapatılır."""
    from sofascore_scraper import sinks

    stop.set()
    if thread is not None:
        thread.join(SINK_JOIN_SECONDS)
        if thread.is_alive():
            logger.warning("The %s thread did not stop in time; leaving it behind", thread.name)
            dispatcher.close()
            return
    # Durdurma, dağıtıcı `sinks` kilidini almadan gelmiş olabilir: birikenler (kilit boşsa) en çok 10 sn teslim
    # edilir; kilit başka bir süreçteyse (watch) onlar orada kalır
    sinks.drain_at_exit(dispatcher)


def build_scheduler(tasks: Any) -> Any:
    """
    Web sürecinin iş yöneticisiyle çalışan zamanlayıcı (sofascore_scraper/web/deps): web'den başlatılan işlerle aynı iş deposu
    ve aynı `writer` kilidi; eski arayüzün iş yansısı da tazelenir. Görevler geçersizse ConfigError.
    """
    from sofascore_scraper.jobs.scheduler import Scheduler
    from sofascore_scraper.services.context import build_context
    from sofascore_scraper.web import deps

    return Scheduler(tasks, jobs=deps.job_manager, context=lambda: build_context(deps.config_manager()),
                     on_change=deps.refresh_job_mirror)


def _scheduler_host(inv: Invocation, settings: Any) -> Tuple[Optional[Any], List[CliWarning]]:
    """
    Zamanlayıcı açık mı (bayrak, yoksa `[schedule] enabled`) ve kurulabilir mi. (zamanlayıcı ya da None,
    uyarılar). `--scheduler --dev`: UsageError; geçersiz görev: ConfigError (sunucu başlamadan, çıkış 2).
    """
    # Bayrak yoksa None: ayar karar verir
    flag: Optional[bool] = None
    if getattr(inv.args, "scheduler", False):
        flag = True
    elif getattr(inv.args, "no_scheduler", False):
        flag = False
    enabled = bool(settings.schedule.enabled) if flag is None else bool(flag)
    if not enabled:
        return None, []
    if getattr(inv.args, "dev", False):
        if flag:
            raise UsageError("--scheduler cannot be combined with --dev: the reloading server runs the app in a "
                             "child process, where the scheduler would not be seen", {"option": "--scheduler"})
        message = "[schedule] enabled is set, but the scheduler does not run with --dev"
        logger.warning(message)
        return None, [CliWarning(SCHEDULER_NOT_IN_DEV, message, logged=True)]
    tasks = tuple(settings.schedule.tasks)
    if not tasks:
        message = "the scheduler is on, but no [[schedule.task]] is configured; it does not start"
        logger.warning(message)
        return None, [CliWarning(SCHEDULER_NO_TASKS, message, logged=True)]
    return build_scheduler(tasks), []


@command("serve", help="ssc_help_cmd_serve", configure=_arguments, settings=True)
def serve(inv: Invocation) -> CommandResult:
    args = inv.args
    from sofascore_scraper.config import loader

    loaded = loader.active()
    host = args.host if args.host is not None else loaded.settings.server.host
    port = args.port if args.port is not None else loaded.settings.server.port
    try:
        bind = resolve_bind(host, port, _explicit_hosts(inv, loaded), bool(args.allow_any_host),
                            from_flag=args.allowed_hosts is not None)
    except UsageError:
        if not inv.out.machine:
            inv.out.info(inv.t("ssc_serve_hosts_required", host=host, prog=inv.out.prog))
        raise
    _apply_allowed_hosts(inv, bind)
    settings = loader.active_settings()
    # "*" listesinin uyarısını web uygulaması başlarken kendisi yazar (sofascore_scraper/web/app.py)
    warnings = _token_warning(inv, bind, settings.server.token)

    data_dir = os.path.abspath(settings.storage.data_dir)
    # Geçersiz görev: sunucu başlamadan config_invalid (çıkış 2)
    scheduler, scheduler_warnings = _scheduler_host(inv, settings)
    warnings = [*warnings, *scheduler_warnings]
    dispatcher = _sink_host(settings, data_dir)  # bozuk sink ayarı: sunucu başlamadan config_invalid (çıkış 2)
    stop = threading.Event()
    thread: Optional[threading.Thread] = None
    if dispatcher is not None:
        dispatcher.register()  # yeni sink'in konumu "şimdi": sunucunun ilk olayından önce
        thread = _start_thread(lambda: dispatcher.run(stop), "serve-sinks")
        if not inv.out.quiet:
            inv.out.info(inv.t("ssc_serve_sinks", count=len(settings.sinks)))
    if scheduler is not None:
        scheduler.start()
        if not inv.out.quiet:
            inv.out.info(inv.t("ssc_serve_scheduler", count=len(scheduler.states())))

    if not inv.out.quiet:
        inv.out.info(inv.t("ssc_serve_starting", url=bind.url))
    logger.info("Starting the web server on %s:%s (allowed hosts: %s)", bind.host, bind.port,
                ",".join(bind.allowed_hosts))
    server_exit = 0
    try:
        run_server(_uvicorn_options(bind, bool(args.dev)))
    except KeyboardInterrupt:
        # Ctrl+C ya da SIGTERM. uvicorn düzgünce kapandıktan sonra yakaladığı sinyali yeniden yükseltir; sunucu
        # başlamadan gelen sinyal de buraya düşer. Uzun çalışan servis her iki durumda 0 ile biter.
        pass
    except SystemExit as exit_request:
        # uvicorn başlayamadığında (port kullanımda) nedeni loglar ve sys.exit(1) çağırır
        code = exit_request.code
        server_exit = code if isinstance(code, int) else (0 if code is None else 1)
    finally:
        if scheduler is not None:
            scheduler.stop()
        if dispatcher is not None:
            _stop_sinks(dispatcher, thread, stop)

    data: Dict[str, Any] = {
        "host": bind.host,
        "port": bind.port,
        "url": bind.url,
        "allowed_hosts": list(bind.allowed_hosts),
        "allowed_hosts_from": bind.origin,
        "token": bool(settings.server.token),
        "dev": bool(args.dev),
        "sinks": len(settings.sinks) if dispatcher is not None else 0,
        "data_dir": data_dir,
    }
    if scheduler is not None:
        data["scheduler_tasks"] = len(scheduler.states())
    if server_exit:
        # Hata satırı stderr'e (metin kipinde) ve zarfın uyarılarına; sonuç stdout'a yazılmaz
        data["server_exit_code"] = server_exit
        failed = CliWarning(SERVER_FAILED, f"the web server could not start or stopped with an error "
                                           f"(code {server_exit}); see the log lines", logged=True)
        return CommandResult(data=data, warnings=[*warnings, failed], exit_code=1,
                             notes=[inv.t("ssc_serve_failed", code=server_exit)])
    return CommandResult(data=data, text=inv.t("ssc_serve_stopped"), warnings=warnings)


__all__ = ["APP", "Bind", "FROM_BIND", "FROM_DEFAULT", "FROM_FLAG", "FROM_SETTINGS", "resolve_bind", "run_server"]
