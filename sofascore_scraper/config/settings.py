"""
Ayar modeli: uygulamanın bütün ayarları, dondurulmuş (frozen) veri sınıfları olarak.

Model iki şeyi birlikte anlatır (docs/design/02-services.md bölüm 4.3):

  * bugünkü ayarlar: `.env` ve ortam değişkenleriyle verilen her anahtar (`.env.example`), bugünkü
    varsayılanlarıyla;
  * yapılandırma dosyasının (`sofascore.toml`) bölümleri: tüketicileri sonraki PR'larda gelenler de
    ([defaults], [slices.*], [[follow]], [live], [[sink]], [schedule], [server]) şimdiden burada durur.

Her alanın `metadata`sı ayrıştırma kuralını taşır (tür, alt sınır, seçenekler, gizli mi, dosyada
yazılabilir mi, açıklama). Yükleyici (sofascore_scraper/config/loader.py) ve JSON Schema üretici
(sofascore_scraper/config/schema.py) aynı tabloyu okur; ikinci bir anahtar listesi yoktur.

Bu modül saftır: dosya okumaz, ortam değişkenine bakmaz, `sofascore_scraper` içinden hiçbir şeyi içe aktarmaz.
Açıklama metinleri (`doc`) İngilizcedir: `describe config` çıktısına ve JSON Schema'ya girer, o çıktı
yerelleştirilmez (02-services.md 4.4).
"""
from __future__ import annotations

from dataclasses import MISSING, Field, dataclass, field, fields
from types import MappingProxyType
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence, Tuple, Type, Union

SCHEMA_VERSION = 1

# Alan türleri (metadata["kind"])
KIND_STR = "str"
KIND_PATH = "path"            # dosyadan geliyorsa göreli yol dosyanın dizinine göre çözülür
KIND_URL = "url"              # http(s) adresi; sondaki "/" atılır
KIND_INT = "int"
KIND_FLOAT = "float"
KIND_BOOL = "bool"
KIND_ENUM = "enum"
KIND_STR_LIST = "str_list"
KIND_RATE = "rate"            # sayı (istek/sn) ya da "off"; 0 = sınır yok
KIND_SEASONS = "seasons"      # "current" | "all" | "last:N" | [sezon id'leri]

DEFAULT_API_BASE_URL = "https://www.sofascore.com/api/v1"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOOPBACK_HOSTS: Tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")
LOG_LEVELS: Tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LANGUAGES: Tuple[str, ...] = ("en", "tr")
# Canlı kaynak (docs/design/02-services.md bölüm 8): "page" sayfanın kendi bağlantısını dinler (varsayılan),
# "direct" uygulamanın kendi bağlantısıdır (yalnızca açıkça seçilirse; yükleyici uyarı üretir), "poll" yalnızca
# yoklamadır. Yoklama, page ve direct için her zaman yedektir.
LIVE_SOURCES: Tuple[str, ...] = ("page", "direct", "poll")
# "direct" kaynağının her anıldığı yerde söylenen dört uyarı (02-services.md 8.3)
LIVE_DIRECT_WARNING = (
    "it uses SofaScore's own client credential outside the site's client; it may break without notice when the "
    "credential or the server changes; it may get your IP address blocked; it is a terms-of-use grey area that "
    "you choose knowingly"
)
FOLLOW_KINDS: Tuple[str, ...] = ("tournament", "team", "player", "event")
SINK_TYPES: Tuple[str, ...] = ("stdout", "file", "webhook")

Seasons = Union[str, Tuple[int, ...]]


def setting(
    default: Any,
    kind: str,
    doc: str,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    exclusive_minimum: bool = False,
    choices: Optional[Sequence[str]] = None,
    case: Optional[str] = None,
    secret: bool = False,
    in_file: bool = True,
) -> Any:
    """
    Bir ayar alanı. `in_file=False`: yalnızca ortamdan gelir (gizli değerler dosyaya yazılmaz; dosya
    değişkenin adını verir). `case`: "upper" / "lower" — seçenek karşılaştırması ve saklanan biçim.
    """
    metadata = {
        "kind": kind, "doc": doc, "minimum": minimum, "maximum": maximum, "exclusive_minimum": exclusive_minimum,
        "choices": tuple(choices) if choices is not None else None, "case": case, "secret": secret,
        "in_file": in_file,
    }
    # Gizli değer repr'e girmez: Settings nesnesi yanlışlıkla loglansa da parola görünmez
    return field(default=default, metadata=metadata, repr=not secret)


# --- bölümler -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class StorageSettings:
    data_dir: str = setting("data", KIND_PATH, "Data directory (downloaded listings, events, state).")
    durability: str = setting(
        "normal", KIND_ENUM, "\"full\" fsyncs every file and directory the Store writes.", choices=("normal", "full"),
        case="lower",
    )
    open_reconcile_seconds: float = setting(
        60.0, KIND_FLOAT, "An open of the data directory skips the scan of the old folders when the last scan "
        "ended less than this many seconds ago; 0 scans on every open.", minimum=0,
    )


@dataclass(frozen=True)
class ClientSettings:
    base_url: str = setting(DEFAULT_API_BASE_URL, KIND_URL, "Base URL of the SofaScore API.")
    rate: float = setting(
        5.0, KIND_RATE, "Requests per second across all processes; 0 or \"off\" removes the limit.", minimum=0,
    )
    max_concurrent: int = setting(10, KIND_INT, "Maximum number of requests in flight.", minimum=1)
    timeout_seconds: int = setting(10, KIND_INT, "Request timeout in seconds.", minimum=1)
    retries: int = setting(3, KIND_INT, "Retries per request.", minimum=0)
    wait_time_min: float = setting(0.2, KIND_FLOAT, "Minimum pause between requests of one worker (s).", minimum=0)
    wait_time_max: float = setting(0.5, KIND_FLOAT, "Maximum extra pause between requests of one worker (s).", minimum=0)
    use_proxy: bool = setting(
        False, KIND_BOOL, "Send requests through the proxy. In the config file it defaults to true when a proxy is given.",
    )
    proxy: str = setting("", KIND_STR, "Proxy URL; may carry credentials.", secret=True)
    proxy_env: str = setting("", KIND_STR, "Name of the environment variable that holds the proxy URL.")
    odds_provider: int = setting(1, KIND_INT, "Odds provider id used by the odds slices.", minimum=1)
    odds_country: str = setting(
        "", KIND_STR, "Country recorded with every odds read (the country SofaScore answers for, e.g. \"TR\"); "
        "empty = not recorded. It is never derived from this machine.",
    )
    captcha_token: str = setting(
        "", KIND_STR, "A sofa_captcha token entered by hand; empty = the browser bridge solves the challenge.",
        secret=True, in_file=False,
    )
    browser_profile: str = setting("", KIND_PATH, "Browser profile directory of the bridge; empty = the default.")
    browser_headed: bool = setting(False, KIND_BOOL, "Show the bridge browser window (debugging).")
    throttle_dir: str = setting("", KIND_PATH, "Directory of the shared request-budget files; empty = the default.")

    @property
    def effective_base_url(self) -> str:
        """
        İsteklere uygulanan kök (sofascore_scraper/client/transport.py bunu kullanır): boş bırakılan ayar varsayılan
        köktür, sondaki "/" atılır. `base_url` ise ayarın yazıldığı halidir (Ayarlar sayfası onu gösterir).
        """
        return self.base_url.strip().rstrip("/") or DEFAULT_API_BASE_URL


@dataclass(frozen=True)
class BreakerSettings:
    rate_limit_consecutive: int = setting(20, KIND_INT, "Consecutive rate-limit errors that stop a job.", minimum=1)
    rate_limit_ratio: float = setting(
        0.9, KIND_FLOAT, "Share of rate-limit errors in a batch that stops a job.", minimum=0, maximum=1,
    )
    server_error_consecutive: int = setting(50, KIND_INT, "Consecutive 5xx responses that stop a job.", minimum=1)
    ignore: bool = setting(False, KIND_BOOL, "Do not stop jobs when the breaker would trip.")


@dataclass(frozen=True)
class BridgeSettings:
    degraded_after: int = setting(3, KIND_INT, "Rejected requests in a row before the state is degraded.", minimum=1)
    blocked_after: int = setting(
        10, KIND_INT, "Rejected requests in a row before the state is blocked (never below degraded_after).", minimum=1,
    )
    blocked_min_seconds: float = setting(
        200.0, KIND_FLOAT, "The streak must last this long before the state is blocked.", minimum=0,
    )


@dataclass(frozen=True)
class FetchSettings:
    only_finished: bool = setting(
        True, KIND_BOOL, "A league download fetches the details of finished events only, and the Overview counts and "
        "status counts finished events (or events with details) only. Team, player and event follows and the v1 "
        "event list are not affected.",
    )


@dataclass(frozen=True)
class DefaultsSettings:
    slices: Tuple[str, ...] = setting(("core",), KIND_STR_LIST, "Slice groups or slice keys fetched by default.")
    seasons: Seasons = setting("current", KIND_SEASONS, "Seasons of a follow: current, all, last:N or a list of ids.")


@dataclass(frozen=True)
class RefreshSettings:
    window_hours: float = setting(
        72.0, KIND_FLOAT, "Hours after kick-off during which a finished event is re-read; 0 = off.", minimum=0,
    )
    min_interval_hours: float = setting(6.0, KIND_FLOAT, "Minimum hours between two refreshes of an event.", minimum=0)
    include_legacy: bool = setting(False, KIND_BOOL, "Also refresh old records that have no observation file.")


@dataclass(frozen=True)
class LiveSettings:
    source: str = setting(
        "page", KIND_ENUM,
        "Live source: page (listen to the connection SofaScore's own page opens; the default), direct (the "
        "application's own connection; never chosen for you: " + LIVE_DIRECT_WARNING + "), poll (polling only). "
        "Polling is always the fallback of page and direct.",
        choices=LIVE_SOURCES, case="lower",
    )
    poll_interval_seconds: float = setting(
        30.0, KIND_FLOAT, "Polling interval of the live list.", minimum=0, exclusive_minimum=True,
    )
    detail_slices: Tuple[str, ...] = setting((), KIND_STR_LIST, "Slices fetched while an event is live.")
    detail_interval_seconds: float = setting(
        20.0, KIND_FLOAT, "Interval of the live detail slices.", minimum=0, exclusive_minimum=True,
    )
    max_event_polls: int = setting(20, KIND_INT, "Maximum event pages requested per polling round.", minimum=1)


@dataclass(frozen=True)
class ServerSettings:
    host: str = setting("127.0.0.1", KIND_STR, "Bind address of the HTTP server.")
    port: int = setting(8000, KIND_INT, "Port of the HTTP server.", minimum=1, maximum=65535)
    allowed_hosts: Tuple[str, ...] = setting(LOOPBACK_HOSTS, KIND_STR_LIST, "Host names the server answers to.")
    token_env: str = setting(
        "", KIND_STR, "Name of the environment variable that holds the optional access token; empty = "
        "SOFASCORE_SERVER__TOKEN.",
    )
    token: str = setting("", KIND_STR, "Access token; empty = no token.", secret=True, in_file=False)


@dataclass(frozen=True)
class LogSettings:
    level: str = setting("INFO", KIND_ENUM, "Log level.", choices=LOG_LEVELS, case="upper")
    debug: bool = setting(False, KIND_BOOL, "Force the DEBUG level.")
    format: str = setting("text", KIND_ENUM, "Log line format.", choices=("text", "json"), case="lower")
    dir: str = setting("", KIND_PATH, "Log directory; empty = logs/ in the project folder.")
    to_file: bool = setting(True, KIND_BOOL, "Write the log file (the console is always on).")
    max_mb: float = setting(5.0, KIND_FLOAT, "Size at which the log file is rotated (MB).", minimum=0.001)
    backup_count: int = setting(5, KIND_INT, "Rotated log files kept.", minimum=1)

    @property
    def effective_level(self) -> str:
        """Uygulanan seviye: `debug` açıksa DEBUG (sofascore_scraper/logger.resolve_level ile aynı kural)."""
        return "DEBUG" if self.debug else self.level


@dataclass(frozen=True)
class DisplaySettings:
    language: str = setting(
        "en", KIND_ENUM, "Interface language; not set = the system language (Turkish) or English.", choices=LANGUAGES,
        case="lower",
    )
    use_color: bool = setting(True, KIND_BOOL, "Coloured console output.")
    date_format: str = setting(DEFAULT_DATE_FORMAT, KIND_STR, "strftime format of displayed dates.")


# --- listeler: dilim seçimi, takipler, hedefler, zamanlama --------------------------------------


@dataclass(frozen=True)
class SliceOverride:
    """[slices.<spor>]: o spor için varsayılan seçime eklenen ve çıkarılan dilimler."""

    enable: Tuple[str, ...] = ()
    disable: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FollowSpec:
    """
    Bir takip. Alanlar Store'un FollowSpec'iyle aynıdır (docs/design/01-storage.md bölüm 2.3); Store bu
    paketi içe aktaramadığı için (katman kuralı) tür burada da tanımlıdır. Hiçbir yerde uygulanmaz:
    tabloya yazmak ST-17'nin işidir.

    `slices`: None = varsayılan seçim; {"include": [...]} = yalnızca bunlar; {"enable": [...],
    "disable": [...]} = varsayılana göre fark. JSON'a çevrilebilir (follows.slices_json).
    """

    kind: str
    entity_id: int
    name: str
    sport: Optional[str] = None
    seasons: Seasons = "all"
    slices: Optional[Mapping[str, Any]] = None
    live: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class SinkSpec:
    """
    [[sink]]: bir çıktı hedefi (02-services.md bölüm 5). `secret_env` değişkenin adıdır; değeri burada
    tutulmaz. `options`: türe özgü, modelin adını bilmediği anahtarlar (toplu gönderim boyutu, dosya
    çevirme...), olduğu gibi; anlamlarını hedefin kendi kodu (P22) denetler.
    """

    name: str
    type: str
    events: Tuple[str, ...] = ("*",)
    url: str = field(default="", repr=False)   # adres kimlik bilgisi taşıyabilir
    secret_env: str = ""
    allow_unsigned: bool = False
    path: str = ""
    options: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ScheduleTask:
    """[[schedule.task]]: `every` ("6h") ya da `cron` ("15 */6 * * *"); ikisinden tam olarak biri."""

    run: str
    every: Optional[str] = None
    every_seconds: Optional[float] = None
    cron: Optional[str] = None
    options: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ScheduleSettings:
    enabled: bool = setting(False, KIND_BOOL, "Run the in-app scheduler inside `serve`.")
    tasks: Tuple[ScheduleTask, ...] = ()


# --- kök ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """Geçerli ayarların tamamı. Değişmez: yeni bir yükleme yeni bir nesne üretir."""

    schema: int = SCHEMA_VERSION
    storage: StorageSettings = field(default_factory=StorageSettings)
    client: ClientSettings = field(default_factory=ClientSettings)
    breaker: BreakerSettings = field(default_factory=BreakerSettings)
    bridge: BridgeSettings = field(default_factory=BridgeSettings)
    fetch: FetchSettings = field(default_factory=FetchSettings)
    defaults: DefaultsSettings = field(default_factory=DefaultsSettings)
    slices: Mapping[str, SliceOverride] = field(default_factory=lambda: MappingProxyType({}))
    follows: Tuple[FollowSpec, ...] = ()
    refresh: RefreshSettings = field(default_factory=RefreshSettings)
    live: LiveSettings = field(default_factory=LiveSettings)
    sinks: Tuple[SinkSpec, ...] = ()
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    log: LogSettings = field(default_factory=LogSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)

    def get(self, key: str) -> Any:
        """'client.rate' gibi noktalı bir anahtarın değeri; bilinmeyen anahtar KeyError."""
        section, _, name = key.partition(".")
        if section not in SECTIONS or name not in SETTING_KEYS.get(section, {}):
            raise KeyError(key)
        return getattr(getattr(self, section), name)


# Sayıl (tek değerli) ayar taşıyan bölümler, dosyadaki sırayla. Listeler ([[follow]], [[sink]],
# [[schedule.task]], [slices.*]) ayrı ayrıştırılır.
SECTIONS: Mapping[str, Type[Any]] = MappingProxyType({
    "storage": StorageSettings,
    "client": ClientSettings,
    "breaker": BreakerSettings,
    "bridge": BridgeSettings,
    "fetch": FetchSettings,
    "defaults": DefaultsSettings,
    "refresh": RefreshSettings,
    "live": LiveSettings,
    "schedule": ScheduleSettings,
    "server": ServerSettings,
    "log": LogSettings,
    "display": DisplaySettings,
})


def _setting_fields(cls: Type[Any]) -> Dict[str, "Field[Any]"]:
    return {f.name: f for f in fields(cls) if "kind" in f.metadata}


# bölüm -> alan adı -> dataclass alanı (yalnızca `setting(...)` ile tanımlananlar)
SETTING_KEYS: Mapping[str, Mapping[str, "Field[Any]"]] = MappingProxyType(
    {section: MappingProxyType(_setting_fields(cls)) for section, cls in SECTIONS.items()}
)


def iter_settings() -> Iterator[Tuple[str, "Field[Any]"]]:
    """Her sayıl ayar için ('bölüm.ad', alan), bölüm ve alan sırasıyla."""
    for section, keys in SETTING_KEYS.items():
        for name, f in keys.items():
            yield f"{section}.{name}", f


def default_of(f: "Field[Any]") -> Any:
    return f.default if f.default is not MISSING else f.default_factory()  # type: ignore[misc]


__all__ = [
    "SCHEMA_VERSION",
    "SECTIONS",
    "SETTING_KEYS",
    "BreakerSettings",
    "BridgeSettings",
    "ClientSettings",
    "DefaultsSettings",
    "DisplaySettings",
    "FetchSettings",
    "FollowSpec",
    "LiveSettings",
    "LogSettings",
    "RefreshSettings",
    "ScheduleSettings",
    "ScheduleTask",
    "ServerSettings",
    "Settings",
    "SinkSpec",
    "SliceOverride",
    "StorageSettings",
    "default_of",
    "iter_settings",
    "setting",
]
