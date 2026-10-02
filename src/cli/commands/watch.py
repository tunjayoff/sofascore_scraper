"""
`watch`: canlı servisi ön planda çalıştırır (docs/design/02-services.md bölüm 8.4). systemd ya da bir konteyner
için tek süreçtir; Ctrl+C ya da SIGTERM ile durur.

    ssc watch                                      takiplerden (live = true), bütün sporlar
    ssc watch --sport football                     yalnızca futbol takipleri
    ssc watch --sport football --tournament 17     takipler yerine bu turnuvanın canlı maçları
    ssc watch --sport tennis --event 12345678      takipler yerine bu maç; bitince komut da biter
    ssc watch --stdout                             yeni olayları stdout'a da yaz (NDJSON, `sofascore.event/1`)

  * Aynı veri dizininde tek canlı servis çalışır: ikincisi ve eski `--watch` (`watcher:<spor>`) varken
    `instance_running` ile çıkar (çıkış kodu 6).
  * Kaynak `--source` ya da `[live] source`'tur. Bu sürümde yalnızca yoklama (`poll`) vardır; `page` (varsayılan)
    ve `direct` ona düşer ve bir uyarı yazılır. `direct` hiçbir zaman kendiliğinden seçilmez (8.3).
  * Yapılandırılmış sink'ler (`[[sink]]`, `SOFASCORE_SINKS`) bu süreçte, ayrı bir thread'de dağıtılır
    (`sinks` kilidi başka bir süreçteyse, örneğin `serve`, o dağıtır). `--stdout` kendi thread'inde, kilitsiz ve
    "şimdi"den yazar. İkisi ayrı thread'lerdir: stdout'u okuyan durursa yapılandırılmış sink'ler beklemez.
    Yapılandırılmış sink'ler ise tek thread'i paylaşır: `deliver`'ı hiç dönmeyen bir sink (asılı bir ağ dosya
    sistemi) diğerlerini bekletir. Kapanış bunu beklemez: thread'ler arka plandadır ve süre sınırıyla beklenir.
  * Canlı verinin HTTP uç noktası yoktur (sahip kararı, 2026-10-01): olaylar günlüğe ve sink'lere gider,
    sonradan `ssc events` ile okunur.

Ağır içe aktarmalar (ayarlar, Store, istek katmanı, sink'ler) işlevin içindedir (src/cli/commands/__init__.py).
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from typing import Any, Dict, List, Optional

from src.cli.commands import CliWarning, CommandResult, Invocation, command
from src.cli.output import JSON, TEXT, Translator
from src.errors import UsageError

logger = logging.getLogger(__name__)

SOURCES = ("page", "direct", "poll")
SINK_JOIN_SECONDS = 15.0  # dağıtıcı dururken birikenleri en çok 10 sn teslim eder; üstüne pay
STDOUT_JOIN_SECONDS = 5.0
# Testlerin LiveService'e verdiği ek seçenekler (sahte istek işlevi, saat); uygulama boş bırakır
SERVICE_OPTIONS: Dict[str, Any] = {}


def _hours(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        number = 0.0
    if not number > 0:
        raise argparse.ArgumentTypeError(f"expected a number of hours above 0, got {value!r}")
    return number


def _arguments(parser: argparse.ArgumentParser, t: Translator) -> None:
    parser.add_argument("--sport", dest="sports", action="append", metavar="SPORT", help=t("ssc_help_watch_sport"))
    parser.add_argument("--event", dest="event_ids", action="append", type=int, metavar="ID",
                        help=t("ssc_help_watch_event"))
    parser.add_argument("--tournament", dest="tournament_ids", action="append", type=int, metavar="ID",
                        help=t("ssc_help_watch_tournament"))
    parser.add_argument("--source", choices=SOURCES, help=t("ssc_help_watch_source"))
    parser.add_argument("--stdout", action="store_true", help=t("ssc_help_watch_stdout"))
    parser.add_argument("--hours", type=_hours, metavar="H", help=t("ssc_help_watch_hours"))


def _source_warnings(requested: str, from_flag: bool) -> List[CliWarning]:
    from src.config.settings import LIVE_DIRECT_WARNING
    from src.services.live.supervisor import AVAILABLE_SOURCES

    warnings: List[CliWarning] = []
    if requested == "direct" and from_flag:  # yapılandırmadan gelen seçimi yükleyici zaten uyarır
        message = f"live.source is \"direct\": {LIVE_DIRECT_WARNING}."
        logger.warning(message)
        warnings.append(CliWarning("live_direct_source", message, logged=True))
    if requested not in AVAILABLE_SOURCES:
        message = (f"the live source {requested!r} is not available in this version; "
                   f"polling is used (the fallback of every source)")
        logger.warning(message)
        warnings.append(CliWarning("live_source_unavailable", message, logged=True))
    return warnings


def _scope(args: argparse.Namespace, store: Any) -> Any:
    from src.services.live.supervisor import LiveScope, explicit_scope, scope_from_follows

    sports = [s.strip().lower() for s in args.sports or () if s.strip()]
    if args.event_ids or args.tournament_ids:
        if not sports:
            raise UsageError("--event and --tournament need --sport")
        return explicit_scope(sports, event_ids=args.event_ids or (), tournament_ids=args.tournament_ids or ())
    scope = scope_from_follows(store.follows.list(enabled=True))
    if sports:
        scope = LiveScope(sports=tuple(s for s in scope.sports if s.sport in sports), from_follows=True,
                          skipped=scope.skipped)
    return scope


def _start_thread(target: Any, name: str) -> threading.Thread:
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread


@command("watch", help="ssc_help_cmd_watch", configure=_arguments, settings=True)
def watch(inv: Invocation) -> CommandResult:
    args = inv.args
    if args.stdout and inv.out.mode == JSON:
        raise UsageError("--stdout writes a stream of lines; use it without --json (or with --output ndjson)")

    from src import sinks
    from src.config import loader
    from src.services.live.supervisor import LiveService
    from src.store import open_store

    settings = loader.active_settings()
    data_dir = os.path.abspath(settings.storage.data_dir)
    requested = args.source or settings.live.source
    warnings = _source_warnings(requested, from_flag=args.source is not None)

    store = open_store(data_dir)
    scope = _scope(args, store)
    if scope.skipped:
        message = f"these follows cannot be watched live and are skipped: {', '.join(scope.skipped)}"
        logger.warning(message)
        warnings.append(CliWarning("live_follow_skipped", message, logged=True))
    if scope.empty:
        raise UsageError("nothing to watch: follow something with live = true, or give --sport with --event or "
                         "--tournament")

    dispatcher = sinks.dispatcher_for(store, settings.sinks)  # bozuk sink ayarı: config_invalid (çıkış kodu 2)
    service = LiveService(
        store, scope, poll_interval=settings.live.poll_interval_seconds,
        max_event_polls=settings.live.max_event_polls, requested_source=requested, **SERVICE_OPTIONS,
    )

    stop = threading.Event()
    threads: List[threading.Thread] = []
    follower = None
    if args.stdout:
        inv.out.mode = TEXT  # stdout olay satırlarınındır; özet stderr'e gider
        follower = sinks.Dispatcher(store, [sinks.StdoutSink("stdout", write=inv.out.write)])
        follower.step()  # konum "şimdi": servisin ilk olayından önce
        threads.append(_start_thread(lambda: follower.follow(stop), "watch-stdout"))
    if dispatcher is not None:
        dispatcher.register()  # yeni sink'in konumu "şimdi": servisin ilk olayından önce
        threads.append(_start_thread(lambda: dispatcher.run(stop), "watch-sinks"))

    previous = _install_sigterm(stop)
    interrupted = False
    try:
        service.run(stop, until_seconds=args.hours * 3600 if args.hours else None)
        interrupted = stop.is_set()  # SIGTERM
    except KeyboardInterrupt:
        interrupted = True
    finally:
        stop.set()
        _restore_sigterm(previous)
        stuck = set()
        for thread in threads:
            thread.join(SINK_JOIN_SECONDS if thread.name == "watch-sinks" else STDOUT_JOIN_SECONDS)
            if thread.is_alive():
                stuck.add(thread.name)
                logger.warning("The %s thread did not stop in time; leaving it behind", thread.name)
        if dispatcher is not None:
            if "watch-sinks" in stuck:
                dispatcher.close()
            else:
                # Durdurma, dağıtıcı `sinks` kilidini almadan gelmiş olabilir: birikenler (kilit boşsa) en çok
                # 10 sn teslim edilir; kilit başka bir süreçteyse (serve) onlar orada kalır
                sinks.drain_at_exit(dispatcher)
        if follower is not None and "watch-stdout" not in stuck:
            follower.close()  # `follow` dururken son olayları da yazmıştır (servis durdurmadan önce bitti)

    report = service.report
    data = {**report.to_dict(), "requested_source": requested, "data_dir": data_dir, "interrupted": interrupted}
    text = inv.t("ssc_watch_summary", rounds=report.rounds, requests=report.requests, events=report.events,
                 confirmed=report.confirmed)
    if args.stdout:
        return CommandResult(warnings=warnings, notes=[text])
    return CommandResult(data=data, text=text, warnings=warnings)


def _install_sigterm(stop: threading.Event) -> Optional[Any]:
    """SIGTERM (systemd, docker stop) servisi Ctrl+C gibi durdurur. Yalnızca ana thread'de kurulabilir."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGTERM"):
        return None
    try:
        return signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    except (ValueError, OSError):
        return None


def _restore_sigterm(previous: Optional[Any]) -> None:
    if previous is None:
        return
    try:
        signal.signal(signal.SIGTERM, previous)
    except (ValueError, OSError):
        pass


__all__ = ["SOURCES"]
