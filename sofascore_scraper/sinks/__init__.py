"""
Çıktı sink'leri (docs/design/02-services.md bölüm 5): olay günlüğünü stdout'a, bir dosyaya ya da bir
webhook'a teslim eder. Canlı verinin süreçten çıktığı tek yol budur; HTTP uç noktası yoktur.

Sink'ler yalnızca yapılandırma dosyasından ve ortamdan gelir (karar D11): `[[sink]]` tabloları ya da
`SOFASCORE_SINKS`. HTTP API'si sink kaydetmez. Yapılandırma `SinkSpec` olarak modellenir (sofascore_scraper/config);
türe özgü anahtarlar (`options`) burada denetlenir:

    her tür    sports = ["football"]      yalnızca bu sporların olayları
               tournaments = [17, 8]      yalnızca bu turnuvaların olayları
    file       rotate_daily, rotate_size, keep            (sofascore_scraper/sinks/file.py)
    webhook    batch_size (1-100), flush_seconds, max_age ("24h"), timeout_seconds   (sofascore_scraper/sinks/webhook.py)

Bilinmeyen bir anahtar hatadır (`config_invalid`): yanlış yazılmış bir ayar sessizce yok sayılmaz.

Webhook imza anahtarı: `secret_env` bir ortam değişkeninin adıdır; değeri burada, sink kurulurken okunur.
Değişkenin adı gizli bir değere benzemelidir (SECRET, TOKEN, KEY, PASSWORD...): tanılama paketi ve log
maskelemesi değerleri adlarından tanır (sofascore_scraper/redact.py), başka bir ad anahtarı pakete açık yazdırırdı.

Kullanım (sink'leri barındıran süreçler):

    from sofascore_scraper import sinks

    dispatcher = sinks.dispatcher_for(store, settings.sinks)      # sink yoksa None
    dispatcher.run(stop)                                          # `watch`, `serve`: `sinks` kilidiyle, durana kadar

    dispatcher.register()                                         # tek seferlik komut, işe başlamadan önce
    ...
    report = sinks.drain_at_exit(dispatcher)                      # çıkışta: en çok 10 saniye, hata fırlatmaz

    sinks.Dispatcher(store, [sinks.StdoutSink()]).follow(stop)    # `watch --stdout`: kilitsiz, "şimdi"den

Bu paket içe aktarıldığında yalnızca hafif modüller yüklenir; dağıtıcı ve sink sınıfları ilk kullanımda.
"""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sofascore_scraper.exceptions import ConfigError
from sofascore_scraper.sinks.base import (
    EVENT_SCHEMA,
    SINK_DROPPED,
    STREAMS,
    BaseSink,
    Clock,
    Envelope,
    EventFilter,
    FatalSinkError,
    RetryableSinkError,
    Sink,
    SinkError,
    StopToken,
    SystemClock,
)

if TYPE_CHECKING:
    from sofascore_scraper.config import SinkSpec
    from sofascore_scraper.sinks.dispatcher import Dispatcher, DrainReport
    from sofascore_scraper.store import Store

COMMON_OPTIONS: Tuple[str, ...] = ("sports", "tournaments")
TYPE_OPTIONS: Mapping[str, Tuple[str, ...]] = {
    "stdout": (),
    "file": ("rotate_daily", "rotate_size", "keep"),
    "webhook": ("batch_size", "flush_seconds", "max_age", "timeout_seconds"),
}

MIN_ROTATE_BYTES = 1024
MAX_FLUSH_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 120.0

_SIZE = re.compile(r"^(\d+)\s*(b|kb|mb|gb)?$")
_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}


# --- yapılandırmanın denetimi --------------------------------------------------------------------


def _where(spec: "SinkSpec", key: str = "") -> str:
    return f"[[sink]] {spec.name!r}" + (f" {key}" if key else "")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _filter(spec: "SinkSpec") -> EventFilter:
    options = spec.options
    sports = options.get("sports", ())
    if not isinstance(sports, (list, tuple)) or any(not isinstance(item, str) or not item for item in sports):
        raise ConfigError(f"{_where(spec, 'sports')}: expected a list of sport names")
    tournaments = options.get("tournaments", ())
    if not isinstance(tournaments, (list, tuple)) or any(not _is_int(item) for item in tournaments):
        raise ConfigError(f"{_where(spec, 'tournaments')}: expected a list of tournament ids")
    try:
        return EventFilter(spec.events, sports=sports, tournament_ids=tournaments)
    except ValueError as e:
        raise ConfigError(f"{_where(spec)} {e}") from None


def _size(spec: "SinkSpec", value: Any) -> int:
    if _is_int(value):
        size = int(value)
    else:
        match = _SIZE.match(value.strip().lower()) if isinstance(value, str) else None
        if match is None:
            raise ConfigError(f"{_where(spec, 'rotate_size')}: expected a size such as \"50MB\" or a number of bytes")
        size = int(match.group(1)) * _SIZE_UNITS[match.group(2) or "b"]
    if size < MIN_ROTATE_BYTES:
        raise ConfigError(f"{_where(spec, 'rotate_size')}: expected at least {MIN_ROTATE_BYTES} bytes")
    return size


def _seconds(spec: "SinkSpec", key: str, value: Any) -> float:
    """Süre: saniye sayısı ya da "90s" / "15m" / "24h" / "7d"."""
    if _is_number(value) and value > 0:
        return float(value)
    if isinstance(value, str):
        from sofascore_scraper.config.loader import parse_duration

        try:
            return parse_duration(value)
        except ValueError:
            pass
    raise ConfigError(f"{_where(spec, key)}: expected a duration such as \"24h\" or a number of seconds")


def _inside(path: str, directory: str) -> bool:
    path, directory = (os.path.normcase(os.path.abspath(item)) for item in (path, directory))
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def _secret(spec: "SinkSpec", environ: Mapping[str, str]) -> Optional[str]:
    """Webhook imza anahtarı. Adı verilmiş ama boş bir değişken hatadır: imza sessizce kapanmaz."""
    if not spec.secret_env:
        if not spec.allow_unsigned:  # yükleyici de reddeder; SinkSpec'i kendisi kuran çağıran için
            raise ConfigError(f"{_where(spec)}: a webhook needs secret_env (or allow_unsigned = true)")
        return None
    from sofascore_scraper.redact import is_secret_key

    if not is_secret_key(spec.secret_env):
        raise ConfigError(
            f"{_where(spec, 'secret_env')}: the variable name must look like a secret (contain SECRET, TOKEN, KEY "
            f"or PASSWORD), so that its value is masked in logs and in the diagnostics bundle; got {spec.secret_env!r}"
        )
    value = (environ.get(spec.secret_env) or "").strip()
    if not value:
        raise ConfigError(f"{_where(spec, 'secret_env')}: the environment variable {spec.secret_env} is not set")
    return value


def build_sink(spec: "SinkSpec", *, environ: Optional[Mapping[str, str]] = None, data_dir: Optional[str] = None,
               clock: Optional[Clock] = None) -> Sink:
    """
    Bir `SinkSpec`'ten sink kurar; türe özgü anahtarları denetler. Geçersiz ya da bilinmeyen anahtar, ayarlı
    olmayan imza anahtarı ve veri dizininin içine yazan dosya sink'i ConfigError'dır (`config_invalid`).

    environ   imza anahtarının okunacağı ortam (varsayılan os.environ)
    data_dir  veri dizini: dosya sink'i onun içine yazamaz (oraya yalnızca Store dokunur)
    """
    env = os.environ if environ is None else environ
    known = COMMON_OPTIONS + TYPE_OPTIONS.get(spec.type, ())
    unknown = sorted(key for key in spec.options if key not in known)
    if unknown:
        raise ConfigError(
            f"{_where(spec, unknown[0])}: unknown key for a {spec.type} sink (known: "
            f"{', '.join(('name', 'type', 'events', *known))})"
        )
    events = _filter(spec)
    options = spec.options

    if spec.type == "stdout":
        from sofascore_scraper.sinks.stdout import StdoutSink

        return StdoutSink(spec.name, events)

    if spec.type == "file":
        from sofascore_scraper.sinks.file import FileSink

        if not spec.path:
            raise ConfigError(f"{_where(spec, 'path')}: a file sink needs a path")
        if data_dir and _inside(spec.path, data_dir):
            raise ConfigError(
                f"{_where(spec, 'path')}: a file sink cannot write inside the data directory ({data_dir}); "
                f"backup, restore and clear own everything in it"
            )
        rotate_daily = options.get("rotate_daily", False)
        if not isinstance(rotate_daily, bool):
            raise ConfigError(f"{_where(spec, 'rotate_daily')}: expected true or false")
        keep = options.get("keep", 0)
        if not _is_int(keep) or keep < 0:
            raise ConfigError(f"{_where(spec, 'keep')}: expected a number of files, 0 or more")
        rotate_bytes = _size(spec, options["rotate_size"]) if "rotate_size" in options else 0
        return FileSink(spec.name, spec.path, events, rotate_bytes=rotate_bytes, rotate_daily=rotate_daily,
                        keep=keep, clock=clock)

    if spec.type == "webhook":
        from sofascore_scraper.sinks import webhook

        batch_size = options.get("batch_size", webhook.DEFAULT_WEBHOOK_BATCH)
        if not _is_int(batch_size) or not webhook.MIN_BATCH_SIZE <= batch_size <= webhook.MAX_BATCH_SIZE:
            raise ConfigError(
                f"{_where(spec, 'batch_size')}: expected a number between {webhook.MIN_BATCH_SIZE} and "
                f"{webhook.MAX_BATCH_SIZE}"
            )
        flush = options.get("flush_seconds", webhook.DEFAULT_FLUSH_SECONDS)
        if not _is_number(flush) or not 0 <= flush <= MAX_FLUSH_SECONDS:
            raise ConfigError(f"{_where(spec, 'flush_seconds')}: expected seconds between 0 and {MAX_FLUSH_SECONDS:g}")
        timeout = options.get("timeout_seconds", webhook.DEFAULT_TIMEOUT_SECONDS)
        if not _is_number(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
            raise ConfigError(
                f"{_where(spec, 'timeout_seconds')}: expected seconds above 0 and at most {MAX_TIMEOUT_SECONDS:g}"
            )
        max_age = (_seconds(spec, "max_age", options["max_age"]) if "max_age" in options
                   else webhook.DEFAULT_MAX_AGE_SECONDS)
        try:
            return webhook.WebhookSink(
                spec.name, spec.url, events, secret=_secret(spec, env), batch_size=batch_size,
                linger_seconds=float(flush), max_age_seconds=max_age, timeout_seconds=float(timeout), clock=clock,
            )
        except ValueError as e:
            raise ConfigError(f"{_where(spec)}: {e}") from None

    raise ConfigError(f"{_where(spec, 'type')}: unknown sink type {spec.type!r}")


def build_sinks(specs: Sequence["SinkSpec"], *, environ: Optional[Mapping[str, str]] = None,
                data_dir: Optional[str] = None, clock: Optional[Clock] = None) -> List[Sink]:
    """Yapılandırmadaki bütün sink'ler, verildikleri sırayla. Adlar ayrı olmalıdır."""
    seen: Dict[str, int] = {}
    for index, spec in enumerate(specs, start=1):
        if spec.name in seen:
            raise ConfigError(f"{_where(spec)}: the name is already used by sink #{seen[spec.name]}")
        seen[spec.name] = index
    return [build_sink(spec, environ=environ, data_dir=data_dir, clock=clock) for spec in specs]


def dispatcher_for(store: "Store", specs: Sequence["SinkSpec"], *, environ: Optional[Mapping[str, str]] = None,
                   clock: Optional[Clock] = None) -> Optional["Dispatcher"]:
    """
    Yapılandırılmış sink'ler için bir dağıtıcı; hiç sink yoksa None (hiçbir şey teslim edilmez, kilit alınmaz).
    Uzun çalışan süreç `run(stop)`, tek seferlik komut başta `register()` ve çıkışta `drain(timeout)` çağırır.
    """
    if not specs:
        return None
    from sofascore_scraper.sinks.dispatcher import Dispatcher

    built = build_sinks(specs, environ=environ, data_dir=os.fspath(store.data_dir), clock=clock)
    return Dispatcher(store, built, clock=clock)


def drain_at_exit(dispatcher: Optional["Dispatcher"], timeout: Optional[float] = None) -> Optional["DrainReport"]:
    """
    Tek seferlik komutların çıkışı: birikmiş olayları en çok `timeout` (varsayılan 10) saniye boyunca teslim
    eder ve sink'leri kapatır. Hiçbir koşulda hata fırlatmaz: sink'ler komutun sonucunu bozmaz. Dağıtıcı
    None ise (sink yok) hiçbir şey yapmaz.
    """
    if dispatcher is None:
        return None
    import logging

    from sofascore_scraper.sinks.dispatcher import DRAIN_TIMEOUT_SECONDS

    try:
        return dispatcher.drain(DRAIN_TIMEOUT_SECONDS if timeout is None else timeout)
    except Exception as e:
        logging.getLogger("Sinks").warning("The backlog could not be delivered at exit (%s)", type(e).__name__)
        return None
    finally:
        dispatcher.close()


_LAZY = {
    "Dispatcher": "sofascore_scraper.sinks.dispatcher",
    "DrainReport": "sofascore_scraper.sinks.dispatcher",
    "SinkStatus": "sofascore_scraper.sinks.dispatcher",
    "StdoutSink": "sofascore_scraper.sinks.stdout",
    "FileSink": "sofascore_scraper.sinks.file",
    "WebhookSink": "sofascore_scraper.sinks.webhook",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


__all__ = [
    "COMMON_OPTIONS",
    "EVENT_SCHEMA",
    "SINK_DROPPED",
    "STREAMS",
    "TYPE_OPTIONS",
    "BaseSink",
    "Clock",
    "Dispatcher",
    "DrainReport",
    "Envelope",
    "EventFilter",
    "FatalSinkError",
    "FileSink",
    "RetryableSinkError",
    "Sink",
    "SinkError",
    "SinkStatus",
    "StdoutSink",
    "StopToken",
    "SystemClock",
    "WebhookSink",
    "build_sink",
    "build_sinks",
    "dispatcher_for",
    "drain_at_exit",
]
