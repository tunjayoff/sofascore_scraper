"""
Ayar yükleyici: katmanları birleştirip bir `Settings` üretir ve her değerin nereden geldiğini söyler
(docs/design/02-services.md bölüm 4.3; plan maddeleri P09 ve P30).

Katmanlar, zayıftan güçlüye:

    default    koddaki varsayılanlar
    overrides  CONFIG_DIR/overrides.json (Ayarlar sayfasının, `PATCH /api/v1/settings`in yazdığı dosya;
               yazarı sofascore_scraper/config/overrides.py)
    file       sofascore.toml
    env        süreç ortamı: SOFASCORE_<BÖLÜM>__<ANAHTAR> ve JSON listeleri (SOFASCORE_FOLLOWS...)
    flag       komut satırı (çağıran `flags` ile verir)

`.env` bir katman değildir: uygulama onu başlangıçta python-dotenv ile süreç ortamına yükler (var olan değeri
ezmeden); oradaki SOFASCORE_*__* satırları ortam katmanıdır. 2.x'in ortam adları (DATA_DIR, MAX_CONCURRENT,
APP_LANGUAGE...) 3.0'da bir sürüm daha okunuyordu; 3.1'de okunmaz (plan maddesi P30). Ortamda ya da `.env`'de
duran eski ad bir uyarıdır (`legacy_name`): ileti yeni adı söyler (LEGACY_NAMES). `ssc doctor` ve
`ssc config init --from-legacy` aynı tabloyu kullanır. İstisna: korumayı ya da gizliliği açan dört ad
(SOFASCORE_API_TOKEN, SOFASCORE_ALLOWED_HOSTS, USE_PROXY, PROXY_URL; DEPRECATED_NAMES) 3.1'de okunmaya devam
eder, kullanımdan kalktıklarını söyleyen bir uyarıyla; 3.2'de kalkarlar (plan maddesi FX-35, sahibin kararı
2026-10-09).

Ayarları okuyan modüller (throttle, refresh, logger, köprü sağlığı, devre kesici, istek katmanı...) değeri
`active_settings()`ten alır; ortamdan doğrudan okumazlar. 3.0'ın geçiş köprüsü (etkin değeri eski adıyla
ortama yazan `projection`) 3.1'de kalktı.

Yapılandırma dosyası süreç başına bir kez okunur; yeniden okumak açık bir çağrıdır (`reload`,
ConfigManager.reload_config). Ortam değişkenleri ise her okumada güncel görülür. `.env` yeniden okunmaz:
değişikliği süreç yeniden başlayınca geçerli olur.

Hata iletileri (ConfigError), uyarılar ve log satırları İngilizcedir: `config_invalid` hatasının `message`
alanına ve loglara girerler; ikisi de yerelleştirilmez (02-services.md 4.4, plan kuralı 8).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from dataclasses import Field, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import dotenv

from sofascore_scraper import language, redact
from sofascore_scraper.config import settings as model
from sofascore_scraper.config.settings import (
    FollowSpec,
    ScheduleSettings,
    ScheduleTask,
    Settings,
    SinkSpec,
    SliceOverride,
)
from sofascore_scraper.exceptions import ConfigError
from sofascore_scraper.redact import MASK, mask_webhook_url
from sofascore_scraper.sports import UnknownSliceName, check_slice_names, sport_slugs

logger = logging.getLogger("Config")

CONFIG_FILE_NAME = "sofascore.toml"
OVERRIDES_FILE_NAME = "overrides.json"
CONFIG_ENV = "SOFASCORE_CONFIG"
CONFIG_DISABLED = "none"   # SOFASCORE_CONFIG=none: yapılandırma dosyası aranmaz
ENV_PREFIX = "SOFASCORE_"
ENV_SEPARATOR = "__"
# Liste ayarları ortamdan JSON olarak verilir
ENV_FOLLOWS = "SOFASCORE_FOLLOWS"
ENV_SINKS = "SOFASCORE_SINKS"
ENV_SLICES = "SOFASCORE_SLICES"
ENV_SCHEDULE_TASKS = "SOFASCORE_SCHEDULE__TASKS"

LAYER_DEFAULT = "default"
LAYER_OVERRIDES = "overrides"
LAYER_FILE = "file"
LAYER_ENV = "env"
LAYER_FLAG = "flag"
LAYERS: Tuple[str, ...] = (LAYER_DEFAULT, LAYER_OVERRIDES, LAYER_FILE, LAYER_ENV, LAYER_FLAG)
# Dosya, ortam ya da bayrakla sabitlenen değer arayüzde kilitli görünür (karar D4)
LOCKED_LAYERS = frozenset({LAYER_FILE, LAYER_ENV, LAYER_FLAG})

# Erişim belirtecinin ortamdaki adı, `[server] token_env` başka bir değişken adı vermediyse
TOKEN_ENV = "SOFASCORE_SERVER__TOKEN"

_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")
_SEASON_WORDS = ("current", "all")
_LAST_N = re.compile(r"^last:([1-9]\d*)$")
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd])$")
_DURATION_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_CRON_FIELD = re.compile(r"^[\d*/,\-A-Za-z]+$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _Auto:
    def __repr__(self) -> str:
        return "AUTO"


AUTO = _Auto()


class _Reject(ValueError):
    """Bir değer anahtarının kuralına uymuyor; ileti çağıran tarafından yer bilgisiyle sarılır."""


@dataclass(frozen=True)
class Source:
    """
    Bir değerin geldiği yer: katman ve kaynağın adı (dosya yolu, ortam değişkeni, bayrak). `deprecated`: değer
    kullanımdan kalkmış bir 2.x adından okundu (DEPRECATED_NAMES; katmanı ortamdır).
    """

    layer: str
    name: str = ""
    deprecated: bool = False

    @property
    def locked(self) -> bool:
        return self.layer in LOCKED_LAYERS


_DEFAULT_SOURCE = Source(LAYER_DEFAULT)


@dataclass(frozen=True)
class ConfigWarning:
    """
    Yükleme sırasında fark edilen, hata olmayan bir durum. `message` İngilizcedir: log satırı ve CLI'nin JSON
    çıktısındaki `warnings` olur; ikisi de yerelleştirilmez (plan kuralı 8, 02-services.md 4.4).
    """

    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _rank(source: Source) -> int:
    return LAYERS.index(source.layer)


# === değer ayrıştırma (dosya, SOFASCORE_*__*, bayrak) ==============================================


def _describe_kind(f: "Field[Any]") -> str:
    meta = f.metadata
    kind = meta["kind"]
    if kind == model.KIND_ENUM:
        return "one of " + ", ".join(meta["choices"])
    return {
        model.KIND_STR: "a string", model.KIND_PATH: "a path", model.KIND_URL: "an http:// or https:// address",
        model.KIND_INT: "a whole number",
        model.KIND_FLOAT: "a number", model.KIND_BOOL: "true or false", model.KIND_STR_LIST: "a list of strings",
        model.KIND_RATE: "a number or \"off\"",
        model.KIND_SEASONS: "\"current\", \"all\", \"last:N\" or a list of season ids",
    }[kind]


def _check_range(f: "Field[Any]", value: float) -> None:
    meta = f.metadata
    low, high = meta["minimum"], meta["maximum"]
    if low is not None and (value <= low if meta["exclusive_minimum"] else value < low):
        raise _Reject(f"must be {'greater than' if meta['exclusive_minimum'] else 'at least'} {low:g}, got {value!r}")
    if high is not None and value > high:
        raise _Reject(f"must be at most {high:g}, got {value!r}")


def _string_list(value: Any, *, text: bool, what: str = "a list of strings") -> Tuple[str, ...]:
    if text:
        raw = str(value).strip()
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except ValueError:
                raise _Reject(f"expected a JSON list, got {raw!r}") from None
        else:
            value = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and item.strip() for item in value):
        raise _Reject(f"expected {what}, got {value!r}")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _seasons(value: Any, *, text: bool) -> model.Seasons:
    if text and str(value).strip().startswith("["):
        try:
            value = json.loads(str(value))
        except ValueError:
            raise _Reject(f"expected a JSON list, got {value!r}") from None
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _SEASON_WORDS or _LAST_N.match(word):
            return word
    elif isinstance(value, (list, tuple)) and value and all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in value
    ):
        return tuple(dict.fromkeys(value))
    raise _Reject(f"expected \"current\", \"all\", \"last:N\" or a list of season ids, got {value!r}")


def coerce(f: "Field[Any]", value: Any, *, text: bool = False) -> Any:
    """
    Değeri alanın türüne çevirir ve kurallarını denetler. `text=True`: değer bir dizgeden geliyor (ortam
    değişkeni, bayrak) ve sayıya / mantıksala çevrilir; dosyadan gelen değerin türü zaten doğru olmalıdır.
    """
    meta = f.metadata
    kind = meta["kind"]
    expected = _Reject(f"expected {_describe_kind(f)}, got {value!r}")

    if kind in (model.KIND_STR, model.KIND_PATH):
        if not isinstance(value, str):
            raise expected
        return value
    if kind == model.KIND_URL:
        if not isinstance(value, str) or not value.strip().lower().startswith(("http://", "https://")):
            raise expected
        return value.strip().rstrip("/")
    if kind == model.KIND_BOOL:
        if text:
            word = str(value).strip().lower()
            if word in _TRUE_WORDS:
                return True
            if word in _FALSE_WORDS:
                return False
            raise expected
        if not isinstance(value, bool):
            raise expected
        return value
    if kind == model.KIND_INT:
        if text:
            try:
                value = int(str(value).strip())
            except ValueError:
                raise expected from None
        if isinstance(value, bool) or not isinstance(value, int):
            raise expected
        _check_range(f, value)
        return value
    if kind in (model.KIND_FLOAT, model.KIND_RATE):
        if kind == model.KIND_RATE and isinstance(value, str) and value.strip().lower() == "off":
            return 0.0
        if text:
            try:
                value = float(str(value).strip())
            except ValueError:
                raise expected from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise expected
        _check_range(f, float(value))
        return float(value)
    if kind == model.KIND_ENUM:
        if not isinstance(value, str):
            raise expected
        word = value.strip()
        word = word.upper() if meta["case"] == "upper" else word.lower() if meta["case"] == "lower" else word
        if word not in meta["choices"]:
            raise expected
        return word
    if kind == model.KIND_STR_LIST:
        items = _string_list(value, text=text)
        choices = meta["choices"]
        if choices is not None:
            unknown = [item for item in items if item not in choices]
            if unknown:
                raise _Reject(f"unknown value {unknown[0]!r}; expected any of {', '.join(choices)}")
        return items
    if kind == model.KIND_SEASONS:
        return _seasons(value, text=text)
    raise AssertionError(kind)  # pragma: no cover - model yeni bir tür eklerse


def _resolve_path(value: str, base: Optional[Path]) -> str:
    """Göreli yol `base`e (yapılandırma dosyasının dizini) göre; çalışma dizinine göre asla. Boş değer boş kalır."""
    if not value or base is None:
        return value
    path = Path(value).expanduser()
    return os.path.normpath(str(path if path.is_absolute() else base / path))


# === 2.x'in ortam adları ===========================================================================
# 3.0'a kadar okunan adlar, 3.1'de okunmaz (plan maddesi P30; dördü dışında, bkz. DEPRECATED_NAMES). Tablo
# yalnızca kullanıcıya yeni adı söylemek içindir: yükleyicinin uyarısı (`legacy_name`), `ssc doctor` ve
# `ssc config init --from-legacy`. Eski ad -> ayar anahtarı; anahtar None ise ayar kalktı (yeni adı yok).

LANGUAGE_KEY = "display.language"

LEGACY_NAMES: Mapping[str, Optional[str]] = MappingProxyType({
    "DATA_DIR": "storage.data_dir",
    "STORE_DURABILITY": "storage.durability",
    "STORE_OPEN_RECONCILE_SECONDS": "storage.open_reconcile_seconds",
    "API_BASE_URL": "client.base_url",
    "REQUEST_RATE_LIMIT": "client.rate",
    "MAX_CONCURRENT": "client.max_concurrent",
    "REQUEST_TIMEOUT": "client.timeout_seconds",
    "MAX_RETRIES": "client.retries",
    "WAIT_TIME_MIN": "client.wait_time_min",
    "WAIT_TIME_MAX": "client.wait_time_max",
    "USE_PROXY": "client.use_proxy",
    "PROXY_URL": "client.proxy",
    "SOFA_CAPTCHA_TOKEN": "client.captcha_token",
    "SOFASCORE_BROWSER_PROFILE": "client.browser_profile",
    "SOFASCORE_BROWSER_HEADED": "client.browser_headed",
    "SOFASCORE_THROTTLE_DIR": "client.throttle_dir",
    "RATE_LIMIT_THRESHOLD_CONSECUTIVE": "breaker.rate_limit_consecutive",
    "RATE_LIMIT_THRESHOLD_RATIO": "breaker.rate_limit_ratio",
    "SERVER_ERROR_THRESHOLD_CONSECUTIVE": "breaker.server_error_consecutive",
    "IGNORE_RATE_LIMIT": "breaker.ignore",
    "BRIDGE_DEGRADED_AFTER": "bridge.degraded_after",
    "BRIDGE_BLOCKED_AFTER": "bridge.blocked_after",
    "BRIDGE_BLOCKED_MIN_SECONDS": "bridge.blocked_min_seconds",
    "FETCH_ONLY_FINISHED": "fetch.only_finished",
    "SAVE_EMPTY_ROUNDS": None,
    "REFRESH_WINDOW_HOURS": "refresh.window_hours",
    "REFRESH_MIN_INTERVAL_HOURS": "refresh.min_interval_hours",
    "REFRESH_LEGACY": "refresh.include_legacy",
    "WATCH_MAX_EVENT_POLLS": "live.max_event_polls",
    "SOFASCORE_ALLOWED_HOSTS": "server.allowed_hosts",
    "SOFASCORE_API_TOKEN": "server.token",
    "LOG_LEVEL": "log.level",
    "DEBUG": "log.debug",
    "LOG_DIR": "log.dir",
    "LOG_TO_FILE": "log.to_file",
    "LOG_MAX_MB": "log.max_mb",
    "LOG_BACKUP_COUNT": "log.backup_count",
    "USE_COLOR": "display.use_color",
    "DATE_FORMAT": "display.date_format",
    "APP_LANGUAGE": LANGUAGE_KEY,
})
LEGACY_ENV_NAMES: Tuple[str, ...] = tuple(LEGACY_NAMES)

# Kullanımdan kalkmış ama 3.1'de hâlâ okunan 2.x adları (plan maddesi FX-35, sahibin kararı 2026-10-09). 3.0'ın
# belgeleri sunucuyu bunlarla korumayı söylüyordu: sessizce okunmasalar erişim belirteci ve Host izin listesi
# kapanır, SofaScore istekleri yapılandırılmış proxy yerine kullanıcının kendi adresinden gider. 3.2'de kalkarlar.
#
# Kural: eski ad, yeni adının ortam değişkeni gibi okunur (aynı ayrıştırma, aynı gizli değer işlemi) ve ortam
# katmanındadır: yapılandırma dosyasını ve overrides.json'ı, 3.0'daki gibi, ezer. Ayar yeni ortam adıyla ya da bir
# bayrakla verildiyse eski ad okunmaz; belirteç için `token_env`, proxy adresi için `proxy_env` bir değişken adı
# verdiyse de. Okunmayan eski ad da bir uyarıdır (yok sayıldığını söyler). 3.0'daki gibi PROXY_URL tek başına
# proxy'yi açmaz (karar D19 yalnızca yeni adlar içindir): USE_PROXY=true gerekir.
DEPRECATED_NAMES: Mapping[str, str] = MappingProxyType({
    "SOFASCORE_API_TOKEN": "server.token",
    "SOFASCORE_ALLOWED_HOSTS": "server.allowed_hosts",
    "USE_PROXY": "client.use_proxy",
    "PROXY_URL": "client.proxy",
})
DEPRECATED_LAST_READ_IN = "3.1"
DEPRECATED_REMOVED_IN = "3.2"

# 3.1'de kalkan ayarlar: dosyada, overrides.json'da ya da SOFASCORE_*__* adıyla verilirse hata değil uyarıdır
# (3.0'ın Ayarlar sayfası onu overrides.json'a yazmış olabilir). Anahtar -> neden.
RETIRED_SETTINGS: Mapping[str, str] = MappingProxyType({
    "fetch.save_empty_rounds": "a round without a match is never stored",
})

# Değeri değişince ayarların yeniden kurulması gereken, önekle bulunamayan adlar: sistem dili ve hâlâ okunan
# eski adlar (USE_PROXY, PROXY_URL)
_WATCHED_ENV: Tuple[str, ...] = tuple(language.LOCALE_KEYS) + tuple(DEPRECATED_NAMES)


def env_name(key: str) -> str:
    """'client.rate' -> 'SOFASCORE_CLIENT__RATE'."""
    section, _, name = key.partition(".")
    return f"{ENV_PREFIX}{section.upper()}{ENV_SEPARATOR}{name.upper()}"


def legacy_replacement(name: str) -> str:
    """
    Eski bir ortam adının yerine geçen ad, iletide yazıldığı gibi: "SOFASCORE_STORAGE__DATA_DIR (or storage.data_dir
    in the config file)"; yalnızca ortamdan okunan ayar için yalnız değişkenin adı; kalkan ayar için "nothing".
    """
    key = LEGACY_NAMES.get(name)
    if key is None:
        return "nothing (the setting was removed)"
    section, _, field_name = key.partition(".")
    f = model.SETTING_KEYS[section][field_name]
    if not f.metadata["in_file"]:
        return env_name(key)
    return f"{env_name(key)} (or {key} in the config file)"


def legacy_names_in(environ: Mapping[str, str], dotenv_values: Mapping[str, str] = MappingProxyType({}),
                    named: Sequence[str] = ()) -> List[str]:
    """
    Ortamda ya da `.env`'de verilmiş (boş olmayan) eski adlar, tablo sırasıyla. `named`: ayarların adını verdiği
    değişkenler (`proxy_env`, `token_env`; named_variables): onlar okunur, eski ad sayılmaz (`ssc config init
    --from-legacy` proxy için `proxy_env = "PROXY_URL"` yazar).
    """
    return [name for name in LEGACY_NAMES if name not in named
            and ((environ.get(name) or "").strip() or (dotenv_values.get(name) or "").strip())]


def named_variables(settings: Settings) -> Tuple[str, ...]:
    """Ayarların değerini başka bir değişkenden okuttuğu adlar: `client.proxy_env`, `server.token_env`."""
    return tuple(name for name in (settings.client.proxy_env.strip(), settings.server.token_env.strip()) if name)


LEGACY_REMOVED = "removed"    # 3.1 okumaz (P30)
LEGACY_READ = "read"          # kullanımdan kalktı, 3.1'de okunuyor (DEPRECATED_NAMES)
LEGACY_IGNORED = "ignored"    # kullanımdan kalktı; yeni adı da verildiği için okunmadı


def legacy_state(name: str, sources: Optional[Mapping[str, Source]] = None) -> Tuple[str, str]:
    """
    Ortamda duran eski bir adın durumu ve (yok sayıldıysa) yerine okunan kaynağın adı: (LEGACY_*, ad). `sources`:
    yükleyicinin kaynakları; verilmezse kullanımdan kalkan ad okunuyor sayılır.
    """
    key = DEPRECATED_NAMES.get(name)
    if key is None:
        return LEGACY_REMOVED, ""
    if sources is None:
        return LEGACY_READ, ""
    source = sources.get(key, _DEFAULT_SOURCE)
    if source.deprecated and source.name == name:
        return LEGACY_READ, ""
    return LEGACY_IGNORED, "a command-line flag" if source.layer == LAYER_FLAG else (source.name or key)


def legacy_origin(name: str, environ: Mapping[str, str], dotenv_values: Mapping[str, str]) -> str:
    """Adın durduğu yer, iletide yazıldığı gibi: `.env`'de dolu ve ortamda yok ya da aynı değerle ise ".env"."""
    in_file = (dotenv_values.get(name) or "").strip()
    return ".env" if in_file and environ.get(name) in (None, dotenv_values.get(name)) else "the environment"


def legacy_warnings(environ: Mapping[str, str], dotenv_values: Mapping[str, str] = MappingProxyType({}),
                    named: Sequence[str] = (), sources: Optional[Mapping[str, Source]] = None) -> List["ConfigWarning"]:
    """
    Her eski ad için bir uyarı (`legacy_name`): okunmadığını ve yerine geçen adı söyler. Kullanımdan kalkan ad
    (DEPRECATED_NAMES) için: 3.1'de hâlâ okunduğunu ve 3.2'de kalkacağını, ya da yeni adı da verildiği için yok
    sayıldığını söyler.
    """
    out: List[ConfigWarning] = []
    for name in legacy_names_in(environ, dotenv_values, named):
        where = legacy_origin(name, environ, dotenv_values)
        state, winner = legacy_state(name, sources)
        if state == LEGACY_READ:
            message = (f"{name} (set in {where}) is deprecated; still read in {DEPRECATED_LAST_READ_IN}, removed in "
                       f"{DEPRECATED_REMOVED_IN}; use {legacy_replacement(name)}.")
        elif state == LEGACY_IGNORED:
            message = (f"{name} (set in {where}) is ignored because {winner} is also set; {name} is deprecated "
                       f"and removed in {DEPRECATED_REMOVED_IN}: remove it.")
        else:
            message = f"{name} (set in {where}) is no longer read since 3.1; use {legacy_replacement(name)}."
        out.append(ConfigWarning("legacy_name", message))
    return out


def _deprecated_value(name: str, key: str, raw: str, base: Optional[Path]) -> Any:
    """Kullanımdan kalkan adın değeri, yeni adının ortam değişkeni gibi ayrıştırılmış (_new_env_layer)."""
    section, _, field_name = key.partition(".")
    f = model.SETTING_KEYS[section][field_name]
    try:
        value = coerce(f, raw, text=True)
    except _Reject as e:
        raise ConfigError(f"{name}: {e}") from None
    if f.metadata["kind"] == model.KIND_PATH:
        value = _resolve_path(value, base)
    if f.metadata["secret"]:
        value = value.strip()
    return value


# === dosyalar ======================================================================================


def find_config_file(explicit: Union[str, os.PathLike, None] = None,
                     environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """
    Yapılandırma dosyası, şu sırayla: açıkça verilen yol (`--config`), SOFASCORE_CONFIG, ./sofascore.toml,
    CONFIG_DIR/sofascore.toml. Açıkça adı verilen dosya yoksa ConfigError; aranan yerlerde yoksa None.
    SOFASCORE_CONFIG=none aramayı kapatır (testler ve sorun giderme için): hiçbir dosya okunmaz.
    """
    env = os.environ if environ is None else environ
    from_env = (env.get(CONFIG_ENV) or "").strip()
    if not explicit and from_env.lower() == CONFIG_DISABLED:
        return None
    for label, named in (("--config", explicit), (CONFIG_ENV, from_env)):
        if named:
            path = Path(named).expanduser()
            if not path.is_file():
                raise ConfigError(f"{label}: config file not found: {path}")
            return path.absolute()
    for candidate in (Path(CONFIG_FILE_NAME), Path(env.get("SOFASCORE_CONFIG_DIR") or "config") / CONFIG_FILE_NAME):
        if candidate.is_file():
            return candidate.absolute()
    return None


def _toml_module() -> Any:
    """
    TOML okuyucu: Python 3.11+ standart kütüphanedeki tomllib, 3.10'da tomli (requirements.txt). Yalnızca
    bir yapılandırma dosyası okunacağı zaman içe aktarılır: dosyası olmayan, paketi henüz kurmamış bir 3.10
    kurulumu güncellemeden sonra da açılır.
    """
    try:
        import tomllib

        return tomllib
    except ImportError:
        pass
    try:
        import tomli

        return tomli
    except ImportError:
        raise ConfigError(
            "reading sofascore.toml on Python 3.10 needs the tomli package: "
            "pip install -r requirements.txt -c constraints.txt"
        ) from None


def _read_toml(path: Path) -> Dict[str, Any]:
    toml = _toml_module()
    try:
        with open(path, "rb") as f:
            return toml.load(f)
    except OSError as e:
        raise ConfigError(f"{path}: cannot read the config file: {e}") from e
    except toml.TOMLDecodeError as e:
        raise ConfigError(f"{path}: not valid TOML: {e}") from e


def _read_overrides(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        raise ConfigError(f"{path}: cannot read the overrides file: {e}") from e
    except ValueError as e:
        raise ConfigError(f"{path}: not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ConfigError(f"{path}: expected a JSON object")
    return doc


def _read_dotenv(path: Path) -> Dict[str, str]:
    """`.env`'deki değerler (python-dotenv'in ortama yüklediği biçimde); okunamıyorsa boş."""
    if not path.is_file():
        return {}
    try:
        return {k: v for k, v in dotenv.dotenv_values(path).items() if v is not None}
    except Exception:
        return {}


# === bir belgeyi (TOML / overrides.json) katmana çevirme ===========================================


# Hata iletisinde değerin yerine yazılan tür adları (bool, int'ten önce: True bir int'tir)
_SHAPES: Tuple[Tuple[type, str], ...] = (
    (bool, "a boolean"), (int, "a whole number"), (float, "a number"), (str, "a string"),
    (dict, "a table"), (list, "a list"), (tuple, "a list"),
)


@dataclass
class _Layer:
    """Bir kaynaktan gelen değerler. Listeler None ise o kaynak listeyi vermemiştir (alttaki geçerli kalır)."""

    values: Dict[str, Any]
    sources: Dict[str, Source]
    slices: Optional[Dict[str, SliceOverride]] = None
    follows: Optional[List[Mapping[str, Any]]] = None
    sinks: Optional[List[Mapping[str, Any]]] = None
    tasks: Optional[List[Mapping[str, Any]]] = None
    # 'slices' / 'follows' / 'sinks' / 'schedule.tasks' -> listenin bu katmandaki kaynağı
    list_sources: Dict[str, Source] = field(default_factory=dict)
    # Kalkmış ayarlar (RETIRED_SETTINGS): (verildiği yer, anahtar), uyarı için
    retired: List[Tuple[str, str]] = field(default_factory=list)


def _table(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a table, got {value!r}")
    return value


def _shape(value: Any) -> str:
    """
    Bir değerin türünün adı ("a string", "a list"). Değeri webhook adresi ya da gizli bir değer olabilen yerlerin
    hata iletisinde değerin kendisi yerine bu yazılır: ileti komutun çıktısına, log'a ve hata zarfına girer.
    """
    if value is None:
        return "null"
    for kind, name in _SHAPES:
        if isinstance(value, kind):
            return name
    return f"a {type(value).__name__} value"  # TOML tarih / saat


def _item_shape(value: Any) -> str:
    """Dizge listesi beklenen yerde verilen değerin tarifi: liste ise uymayan ilk öğenin türü ve sırası."""
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value, start=1):
            if not isinstance(item, str):
                return f"{_shape(item)} at #{index}"
            if not item.strip():
                return f"an empty string at #{index}"
    return _shape(value)


def _table_list(value: Any, where: str) -> List[Mapping[str, Any]]:
    # Uymayan öğenin kendisi iletiye yazılmaz, türü yazılır: tablo yerine dizge listesi olarak verilmiş bir
    # SOFASCORE_SINKS'in öğeleri webhook adresidir
    if not isinstance(value, list):
        raise ConfigError(f"{where}: expected a list of tables, got {_shape(value)}")
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"{where}: expected a list of tables, got {_shape(item)} at #{index}")
    return list(value)


def _parse_slices(value: Any, where: str) -> Dict[str, SliceOverride]:
    known = sport_slugs()
    out: Dict[str, SliceOverride] = {}
    for sport, table in _table(value, where).items():
        at = f"{where}.{sport}"
        if sport not in known:
            raise ConfigError(f"{at}: unknown sport; expected one of {', '.join(known)}")
        table = _table(table, at)
        unknown = sorted(set(table) - {"enable", "disable"})
        if unknown:
            raise ConfigError(f"{at}: unknown key {unknown[0]!r}; expected enable, disable")
        try:
            out[sport] = SliceOverride(
                enable=_string_list(table.get("enable", []), text=False),
                disable=_string_list(table.get("disable", []), text=False),
            )
            check_slice_names((*out[sport].enable, *out[sport].disable))
        except (_Reject, UnknownSliceName) as e:
            raise ConfigError(f"{at}: {e}") from None
    return out


def _document_layer(doc: Mapping[str, Any], *, layer: str, name: str, base: Optional[Path], lists: bool) -> _Layer:
    """
    Bölümlü bir belgeyi (yapılandırma dosyası ya da overrides.json) katmana çevirir. Bilinmeyen bölüm ve
    anahtar hatadır: yanlış yazılmış bir ayar sessizce yok sayılmaz. `lists=False`: [[follow]], [[sink]] ve
    [[schedule.task]] bu belgede verilemez (overrides.json; karar D11).
    """
    source = Source(layer, name)
    out = _Layer({}, {})
    for section, body in doc.items():
        where = f"{name}: [{section}]"
        if section == "schema":
            if isinstance(body, bool) or body != model.SCHEMA_VERSION:
                raise ConfigError(
                    f"{name}: schema = {body!r} is not supported; this version reads schema = {model.SCHEMA_VERSION}"
                )
            continue
        if section == "slices":
            out.slices = _parse_slices(body, where)
            out.list_sources["slices"] = source
            continue
        if section in ("follow", "sink"):
            if not lists:
                raise ConfigError(f"{name}: [[{section}]] can only be given in the config file or the environment")
            items = _table_list(body, f"{name}: [[{section}]]")
            if section == "follow":
                out.follows = items
            else:
                out.sinks = items
            out.list_sources["follows" if section == "follow" else "sinks"] = source
            continue
        if section not in model.SECTIONS:
            raise ConfigError(f"{name}: unknown section [{section}]")
        keys = model.SETTING_KEYS[section]
        for key, value in _table(body, where).items():
            if section == "schedule" and key == "task":
                if not lists:
                    raise ConfigError(f"{name}: [[schedule.task]] can only be given in the config file or the environment")
                out.tasks = _table_list(value, f"{name}: [[schedule.task]]")
                out.list_sources["schedule.tasks"] = source
                continue
            f = keys.get(key)
            if f is None and f"{section}.{key}" in RETIRED_SETTINGS:
                out.retired.append((f"{where} {key}", f"{section}.{key}"))
                continue
            if f is None:
                raise ConfigError(f"{where}: unknown key {key!r}")
            if not f.metadata["in_file"]:
                variable = env_name(f"{section}.{key}")
                raise ConfigError(f"{where} {key}: this value is read from the environment only ({variable})")
            try:
                value = coerce(f, value)
            except _Reject as e:
                raise ConfigError(f"{where} {key}: {e}") from None
            if f.metadata["kind"] == model.KIND_PATH:
                value = _resolve_path(value, base)
            out.values[f"{section}.{key}"] = value
            out.sources[f"{section}.{key}"] = source
    return out


# === listeler: takipler, hedefler, zamanlama =======================================================


_TYPE_NAMES = {int: "a whole number", str: "a string", bool: "true or false"}
# Bir [[sink]] tablosunun modellenen anahtarları (SinkSpec'in alanları); kalanı türe özgü seçenektir (`options`)
SINK_KEYS: Tuple[str, ...] = ("name", "type", "events", "url", "secret_env", "allow_unsigned", "path")


def _take(table: Mapping[str, Any], key: str, kind: type, where: str, default: Any = None, *, quote: bool = True) -> Any:
    """`quote=False`: yanlış türdeki değer iletiye yazılmaz, türü yazılır ([[sink]] satırları; bkz. parse_sinks)."""
    if key not in table:
        return default
    value = table[key]
    if isinstance(value, bool) and kind is not bool or not isinstance(value, kind):
        raise ConfigError(f"{where} {key}: expected {_TYPE_NAMES[kind]}, got {repr(value) if quote else _shape(value)}")
    return value


def _follow_slices(value: Any, where: str) -> Optional[Mapping[str, Any]]:
    try:
        if isinstance(value, dict):
            unknown = sorted(set(value) - {"enable", "disable"})
            if unknown:
                raise ConfigError(f"{where} slices: unknown key {unknown[0]!r}; expected enable, disable")
            parsed = {
                "enable": list(_string_list(value.get("enable", []), text=False)),
                "disable": list(_string_list(value.get("disable", []), text=False)),
            }
        else:
            parsed = {"include": list(_string_list(value, text=False))}
        check_slice_names(name for names in parsed.values() for name in names)
        return MappingProxyType(parsed)
    except UnknownSliceName as e:
        raise ConfigError(f"{where} slices: {e}") from None
    except _Reject:
        raise ConfigError(
            f"{where} slices: expected a list of slice names or a table with enable / disable, got {value!r}"
        ) from None


def parse_follows(items: Sequence[Mapping[str, Any]], default_seasons: model.Seasons, name: str) -> Tuple[FollowSpec, ...]:
    """[[follow]] tabloları -> FollowSpec. Aynı varlık ya da aynı ad iki kez verilemez (bugünkü leagues.txt kuralı)."""
    known_sports = sport_slugs()
    allowed = set(model.FOLLOW_KINDS) | {"name", "sport", "seasons", "slices", "live", "enabled"}
    specs: List[FollowSpec] = []
    seen_ids: Dict[Tuple[str, int], int] = {}
    seen_names: Dict[str, int] = {}
    for index, table in enumerate(items, start=1):
        where = f"{name}: [[follow]] #{index}"
        unknown = sorted(set(table) - allowed)
        if unknown:
            raise ConfigError(f"{where}: unknown key {unknown[0]!r}")
        kinds = [kind for kind in model.FOLLOW_KINDS if kind in table]
        if len(kinds) != 1:
            raise ConfigError(f"{where}: give exactly one of {', '.join(model.FOLLOW_KINDS)}")
        kind = kinds[0]
        entity_id = _take(table, kind, int, where)
        if entity_id <= 0:
            raise ConfigError(f"{where} {kind}: expected a positive id, got {entity_id!r}")
        sport = _take(table, "sport", str, where)
        if sport is not None and sport not in known_sports:
            raise ConfigError(f"{where} sport: unknown sport {sport!r}; expected one of {', '.join(known_sports)}")
        seasons = default_seasons
        if "seasons" in table:
            try:
                seasons = _seasons(table["seasons"], text=False)
            except _Reject as e:
                raise ConfigError(f"{where} seasons: {e}") from None
        label = (_take(table, "name", str, where) or "").strip() or f"{kind}-{entity_id}"
        if any(c in label for c in "\r\n\x00"):
            raise ConfigError(f"{where} name: control characters are not allowed")
        if (kind, entity_id) in seen_ids:
            raise ConfigError(f"{where}: {kind} {entity_id} is already followed by #{seen_ids[(kind, entity_id)]}")
        if label in seen_names:
            raise ConfigError(f"{where}: the name {label!r} is already used by #{seen_names[label]}")
        seen_ids[(kind, entity_id)] = index
        seen_names[label] = index
        specs.append(FollowSpec(
            kind=kind,
            entity_id=entity_id,
            name=label,
            sport=sport,
            seasons=seasons,
            slices=_follow_slices(table["slices"], where) if "slices" in table else None,
            live=_take(table, "live", bool, where, False),
            enabled=_take(table, "enabled", bool, where, True),
        ))
    return tuple(specs)


def parse_sinks(items: Sequence[Mapping[str, Any]], name: str, base: Optional[Path]) -> Tuple[SinkSpec, ...]:
    """
    [[sink]] tabloları -> SinkSpec. Modelin bilmediği anahtarlar `options`a geçer (türe özgü; P22 denetler).

    Buradaki hata iletileri reddedilen değeri yazmaz, türünü yazar: bir sink satırının alanı webhook adresi
    (yolu ya da sorgusu belirteç olabilir) ya da `secret_env`e adı yerine yazılmış imza anahtarı olabilir ve
    ileti komutun çıktısına, log'a ve hata zarfına girer. Tek istisna sink'in adıdır (`config show` da gösterir).
    """
    specs: List[SinkSpec] = []
    seen: Dict[str, int] = {}
    for index, table in enumerate(items, start=1):
        where = f"{name}: [[sink]] #{index}"
        label = (_take(table, "name", str, where, quote=False) or "").strip()
        if not label:
            raise ConfigError(f"{where}: name is required")
        if label in seen:
            raise ConfigError(f"{where}: the name {label!r} is already used by #{seen[label]}")
        seen[label] = index
        kind = _take(table, "type", str, where, quote=False)
        if kind not in model.SINK_TYPES:
            raise ConfigError(f"{where} type: expected one of {', '.join(model.SINK_TYPES)}")
        try:
            events = _string_list(table.get("events", ["*"]), text=False)
        except _Reject:
            raise ConfigError(
                f"{where} events: expected a list of strings, got {_item_shape(table['events'])}"
            ) from None
        url = _take(table, "url", str, where, "", quote=False).strip()
        secret_env = _take(table, "secret_env", str, where, "", quote=False).strip()
        allow_unsigned = _take(table, "allow_unsigned", bool, where, False, quote=False)
        path = _take(table, "path", str, where, "", quote=False).strip()
        if secret_env and not _ENV_NAME.match(secret_env):
            # İleti "secret_env: ..." diye yazılmaz: komut çıktısındaki maskeleme (redact_text) bunu bir
            # `anahtar: değer` çifti sayar ve iki noktadan sonraki sözcüğü `***` yapar
            raise ConfigError(
                f"{where}: secret_env must be the name of an environment variable (letters, digits and _), "
                f"not the secret itself"
            )
        if kind == "webhook":
            if not url.lower().startswith(("http://", "https://")):
                raise ConfigError(f"{where} url: a webhook needs an http:// or https:// address")
            if not secret_env and not allow_unsigned:
                # Karar D12: imzasız gönderim ancak açıkça istenirse
                raise ConfigError(f"{where}: a webhook needs secret_env (or allow_unsigned = true)")
        elif url:
            raise ConfigError(f"{where} url: only a webhook sink takes a url")
        if kind == "file":
            if not path:
                raise ConfigError(f"{where} path: a file sink needs a path")
        elif path:
            raise ConfigError(f"{where} path: only a file sink takes a path")
        specs.append(SinkSpec(
            name=label, type=kind, events=events, url=url, secret_env=secret_env, allow_unsigned=allow_unsigned,
            path=_resolve_path(path, base),
            options=MappingProxyType({k: v for k, v in table.items() if k not in SINK_KEYS}),
        ))
    return tuple(specs)


def mask_sink_options(kind: Any, options: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Bir sink'in seçeneklerinin gösterilecek hali (`config show`, tanılama paketi): sink'lerin tanımladığı
    anahtarların (sofascore_scraper.sinks: her türün ortak ve türe özgü anahtarları) değeri kalır, başka her anahtarın değeri
    `***` olur. Tanımlı anahtarlar gizli değer taşımaz; kullanıcının uydurduğu ya da yanlış yazdığı bir anahtar
    (`webhook_url`, `auth`...) taşıyabilir. Öyle bir anahtar sink kurulurken reddedilir, ama `config show` ve
    paket onu daha önce gösterir. Anahtarın adı kalır: hangisinin yanlış olduğu görünür.
    """
    from sofascore_scraper.sinks import COMMON_OPTIONS, TYPE_OPTIONS  # geç içe aktarma: sofascore_scraper.sinks ayarları (SinkSpec) kullanır

    known = set(COMMON_OPTIONS) | set(TYPE_OPTIONS.get(kind, ()) if isinstance(kind, str) else ())
    return {key: value if key in known else MASK for key, value in options.items()}


def mask_task_options(run: Any, options: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Bir zamanlayıcı görevinin seçeneklerinin gösterilecek hali (`config show`, tanılama paketi; FX-34), sink'lerin
    kuralıyla (mask_sink_options): görevin `run`ının tanımladığı seçeneklerin (sofascore_scraper/jobs/scheduler.py
    `TASK_RUNS`: `league_id`, `scope`, `include_env`, `older_than`) değeri kalır, başka her anahtarın değeri `***`
    olur. Öyle bir anahtar zamanlayıcı kurulurken reddedilir (`check_tasks`), ama `config show` ve paket onu daha
    önce gösterir. Bilinmeyen `run`ın her seçeneği maskelenir; anahtarın adı kalır.
    """
    from sofascore_scraper.jobs.scheduler import TASK_RUNS  # geç içe aktarma: zamanlayıcı yapılandırmayı kullanır

    found = TASK_RUNS.get(run) if isinstance(run, str) else None
    known = found.options if found is not None else frozenset()
    return {key: value if key in known else MASK for key, value in options.items()}


def mask_sink_table(table: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Aynı kural, ayrıştırılmamış bir [[sink]] tablosu için (SOFASCORE_SINKS'in bir öğesi): modelin anahtarları
    (SINK_KEYS) olduğu gibi kalır, seçenekler mask_sink_options'tan geçer. `url` burada maskelenmez
    (redact.mask_webhook_url çağıranın işidir).
    """
    shown = mask_sink_options(table.get("type"), {k: v for k, v in table.items() if k not in SINK_KEYS})
    return {key: shown.get(key, value) for key, value in table.items()}


def parse_duration(text: str) -> float:
    """'90s', '15m', '6h', '1d' -> saniye."""
    match = _DURATION.match(text.strip().lower())
    if not match or float(match.group(1)) <= 0:
        raise _Reject(f"expected a duration such as \"30m\" or \"6h\", got {text!r}")
    return float(match.group(1)) * _DURATION_UNITS[match.group(2)]


def parse_tasks(items: Sequence[Mapping[str, Any]], name: str) -> Tuple[ScheduleTask, ...]:
    typed = {"run", "every", "cron"}
    tasks: List[ScheduleTask] = []
    for index, table in enumerate(items, start=1):
        where = f"{name}: [[schedule.task]] #{index}"
        run = (_take(table, "run", str, where) or "").strip()
        if not run:
            raise ConfigError(f"{where}: run is required")
        every = _take(table, "every", str, where)
        cron = _take(table, "cron", str, where)
        if (every is None) == (cron is None):
            raise ConfigError(f"{where}: give exactly one of every, cron")
        seconds: Optional[float] = None
        if every is not None:
            try:
                seconds = parse_duration(every)
            except _Reject as e:
                raise ConfigError(f"{where} every: {e}") from None
        elif len(cron.split()) != 5 or not all(_CRON_FIELD.match(part) for part in cron.split()):
            raise ConfigError(f"{where} cron: expected five fields such as \"15 */6 * * *\", got {cron!r}")
        tasks.append(ScheduleTask(
            run=run, every=every, every_seconds=seconds, cron=cron,
            options=MappingProxyType({k: v for k, v in table.items() if k not in typed}),
        ))
    return tuple(tasks)


# === ortam =========================================================================================


def _json_env(environ: Mapping[str, str], name: str, kind: type) -> Any:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError as e:
        raise ConfigError(f"{name}: not valid JSON: {e}") from None
    if not isinstance(value, kind):
        raise ConfigError(f"{name}: expected a JSON {'list' if kind is list else 'object'}")
    return value


def _new_env_layer(environ: Mapping[str, str], base: Optional[Path]) -> _Layer:
    """
    SOFASCORE_<BÖLÜM>__<ANAHTAR> değişkenleri ve JSON listeleri (SOFASCORE_FOLLOWS, SOFASCORE_SINKS...). Boş bırakılmış
    değişken "verilmemiş"tir (`.env`'deki `SOFASCORE_CLIENT__RATE=` satırı varsayılanı bırakır); boş bir gizli
    değer de bir belirteç sayılmaz.
    """
    out = _Layer({}, {})
    for name in sorted(environ):
        if not name.startswith(ENV_PREFIX) or ENV_SEPARATOR not in name or name == ENV_SCHEDULE_TASKS:
            continue
        if not (environ[name] or "").strip():
            continue
        section, _, key = name[len(ENV_PREFIX):].lower().partition(ENV_SEPARATOR)
        f = model.SETTING_KEYS.get(section, {}).get(key)
        if f is None and f"{section}.{key}" in RETIRED_SETTINGS:
            out.retired.append((name, f"{section}.{key}"))
            continue
        if f is None:
            raise ConfigError(f"{name}: unknown setting (no [{section}] {key})")
        try:
            value = coerce(f, environ[name], text=True)
        except _Reject as e:
            raise ConfigError(f"{name}: {e}") from None
        if f.metadata["kind"] == model.KIND_PATH:
            value = _resolve_path(value, base)
        if f.metadata["secret"]:
            value = value.strip()
        out.values[f"{section}.{key}"] = value
        out.sources[f"{section}.{key}"] = Source(LAYER_ENV, name)

    slices = _json_env(environ, ENV_SLICES, dict)
    if slices is not None:
        out.slices = _parse_slices(slices, ENV_SLICES)
        out.list_sources["slices"] = Source(LAYER_ENV, ENV_SLICES)
    for label, name in (("follows", ENV_FOLLOWS), ("sinks", ENV_SINKS), ("schedule.tasks", ENV_SCHEDULE_TASKS)):
        items = _json_env(environ, name, list)
        if items is None:
            continue
        items = _table_list(items, name)
        if label == "follows":
            out.follows = items
        elif label == "sinks":
            out.sinks = items
        else:
            out.tasks = items
        out.list_sources[label] = Source(LAYER_ENV, name)
    return out


def _flag_layer(flags: Optional[Mapping[str, Any]]) -> _Layer:
    out = _Layer({}, {})
    for key, value in (flags or {}).items():
        section, _, name = key.partition(".")
        f = model.SETTING_KEYS.get(section, {}).get(name)
        if f is None:
            raise ConfigError(f"flag {key}: unknown setting")
        try:
            out.values[key] = coerce(f, value, text=isinstance(value, str))
        except _Reject as e:
            raise ConfigError(f"flag {key}: {e}") from None
        out.sources[key] = Source(LAYER_FLAG, key)
    return out


# === sonuç =========================================================================================


@dataclass(frozen=True)
class LoadedSettings:
    """Geçerli ayarlar ve her birinin kaynağı (`config show` bunu yazar)."""

    settings: Settings
    sources: Mapping[str, Source]
    config_file: Optional[str] = None
    overrides_file: Optional[str] = None
    warnings: Tuple[ConfigWarning, ...] = ()

    def source(self, key: str) -> Source:
        return self.sources.get(key, _DEFAULT_SOURCE)

    def replaced_by(self, key: str) -> Optional[str]:
        """Değer kullanımdan kalkan bir 2.x adından okunduysa (DEPRECATED_NAMES) yerine kullanılacak ad; yoksa None."""
        return env_name(key) if self.source(key).deprecated else None

    def describe(self, *, mask_secrets: bool = True) -> List[Dict[str, Any]]:
        """
        Her sayıl ayar için bir satır: anahtar, değer (gizliler maskeli), kaynak, kilitli mi, ve değer kullanımdan
        kalkan bir 2.x adından okunduysa yerine kullanılacak ad (`replaced_by`); sonra listeler.
        """
        rows: List[Dict[str, Any]] = []
        for key, f in model.iter_settings():
            value = self.settings.get(key)
            if mask_secrets and f.metadata["secret"] and value:
                value = MASK
            source = self.source(key)
            rows.append({
                "key": key, "value": list(value) if isinstance(value, tuple) else value,
                "source": source.layer, "from": source.name, "locked": source.locked,
                "replaced_by": self.replaced_by(key),
            })
        for key, value in (
            ("slices", {sport: _override_row(override) for sport, override in self.settings.slices.items()}),
            ("follows", [_follow_row(spec) for spec in self.settings.follows]),
            ("sinks", [_sink_row(spec, mask_secrets) for spec in self.settings.sinks]),
            ("schedule.tasks", [_task_row(task, mask_secrets) for task in self.settings.schedule.tasks]),
        ):
            source = self.source(key)
            rows.append({"key": key, "value": value, "source": source.layer, "from": source.name, "locked": source.locked,
                         "replaced_by": None})
        return rows


def _override_row(override: SliceOverride) -> Dict[str, Any]:
    return {"enable": list(override.enable), "disable": list(override.disable)}


def _follow_row(spec: FollowSpec) -> Dict[str, Any]:
    return {
        "kind": spec.kind, "id": spec.entity_id, "name": spec.name, "sport": spec.sport,
        "seasons": list(spec.seasons) if isinstance(spec.seasons, tuple) else spec.seasons,
        "slices": dict(spec.slices) if spec.slices is not None else None, "live": spec.live, "enabled": spec.enabled,
    }


def _sink_row(spec: SinkSpec, mask_secrets: bool) -> Dict[str, Any]:
    return {
        "name": spec.name, "type": spec.type, "events": list(spec.events),
        # Webhook adresinin yolu ve sorgusu da belirteç taşıyabilir: yalnızca şema ve host gösterilir
        "url": mask_webhook_url(spec.url) if mask_secrets else spec.url, "secret_env": spec.secret_env,
        "allow_unsigned": spec.allow_unsigned, "path": spec.path,
        # Sink'lerin tanımadığı bir anahtarın değeri gösterilmez (bkz. mask_sink_options)
        "options": mask_sink_options(spec.type, spec.options) if mask_secrets else dict(spec.options),
    }


def _task_row(task: ScheduleTask, mask_secrets: bool) -> Dict[str, Any]:
    # Görevin tanımadığı bir seçeneğin değeri gösterilmez (bkz. mask_task_options)
    options = mask_task_options(task.run, task.options) if mask_secrets else dict(task.options)
    return {"run": task.run, "every": task.every, "cron": task.cron, "options": options}


def _named_secret(environ: Mapping[str, str], variable: str, where: str) -> str:
    """Adı ayarda verilen ortam değişkeninin değeri. Boşsa hata: koruma sessizce kapanmasın."""
    if not _ENV_NAME.match(variable):
        raise ConfigError(f"{where}: expected the name of an environment variable, got {variable!r}")
    value = (environ.get(variable) or "").strip()
    if not value:
        raise ConfigError(f"{where}: the environment variable {variable} is not set")
    return value


def load_settings(
    *,
    config_file: Union[str, os.PathLike, None, _Auto] = AUTO,
    environ: Optional[Mapping[str, str]] = None,
    dotenv_values: Union[Mapping[str, str], None, _Auto] = AUTO,
    overrides_file: Union[str, os.PathLike, None, _Auto] = AUTO,
    flags: Optional[Mapping[str, Any]] = None,
) -> LoadedSettings:
    """
    Dosyaları okur ve katmanları birleştirir. Önbellek tutmaz, ortama yazmaz.

    config_file     AUTO = ara (find_config_file); None = dosya yok say; yol = o dosya (`--config`)
    environ         varsayılan os.environ
    dotenv_values   `.env`'in içeriği, yalnızca eski adların uyarısı için (değerler ortamdan okunur: uygulama
                    `.env`'i başlangıçta ortama yükler); AUTO = SOFASCORE_ENV_FILE / .env okunur; None = yok say
    overrides_file  AUTO = CONFIG_DIR/overrides.json (varsa); None = yok say
    flags           {'storage.data_dir': '/veri', 'client.rate': 2}: komut satırından gelenler
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    if isinstance(config_file, _Auto):
        path = find_config_file(None, env)
    elif config_file is None:
        path = None
    else:
        path = find_config_file(config_file, env)
    if isinstance(dotenv_values, _Auto):
        dotenv_values = _read_dotenv(Path(env.get("SOFASCORE_ENV_FILE") or ".env"))
    if isinstance(overrides_file, _Auto):
        overrides_file = Path(env.get("SOFASCORE_CONFIG_DIR") or "config") / OVERRIDES_FILE_NAME
    overrides_path = Path(overrides_file).absolute() if overrides_file is not None else None
    overrides_doc = _read_overrides(overrides_path) if overrides_path is not None else None
    return _build(
        env, dotenv_values or {},
        path, _read_toml(path) if path is not None else None,
        overrides_path if overrides_doc is not None else None, overrides_doc,
        flags,
    )


def _build(
    env: Mapping[str, str],
    dotenv_values: Mapping[str, str],
    path: Optional[Path],
    config_doc: Optional[Mapping[str, Any]],
    overrides_path: Optional[Path],
    overrides_doc: Optional[Mapping[str, Any]],
    flags: Optional[Mapping[str, Any]],
) -> LoadedSettings:
    """Okunmuş belgelerden ve ortamdan ayarları kurar (dosya okumaz)."""
    base = path.parent if path is not None else None
    file_name = str(path) if path is not None else ""
    layers: List[_Layer] = []
    if overrides_doc is not None:
        layers.append(
            _document_layer(overrides_doc, layer=LAYER_OVERRIDES, name=str(overrides_path), base=base, lists=False)
        )
    if config_doc is not None:
        layers.append(_document_layer(config_doc, layer=LAYER_FILE, name=file_name, base=base, lists=True))
    layers.append(_new_env_layer(env, base))
    layers.append(_flag_layer(flags))

    values: Dict[str, Any] = {key: model.default_of(f) for key, f in model.iter_settings()}
    sources: Dict[str, Source] = {}
    slices: Dict[str, SliceOverride] = {}
    follows: Optional[List[Mapping[str, Any]]] = None
    sinks: Optional[List[Mapping[str, Any]]] = None
    tasks: Optional[List[Mapping[str, Any]]] = None
    for layer in layers:
        values.update(layer.values)
        sources.update(layer.sources)
        sources.update(layer.list_sources)
        # Dilim seçimi spor spor birleşir; diğer listeler bütün olarak yer değiştirir (en güçlü katman kazanır)
        slices.update(layer.slices or {})
        for sport in layer.slices or {}:
            # Spor spor kaynak: ayarlar API'si hangi sporun seçiminin kilitli olduğunu buradan okur
            sources[f"slices.{sport}"] = layer.list_sources["slices"]
        follows = layer.follows if layer.follows is not None else follows
        sinks = layer.sinks if layer.sinks is not None else sinks
        tasks = layer.tasks if layer.tasks is not None else tasks

    warnings: List[ConfigWarning] = []

    # Dil verilmemişse sistem dili (sofascore_scraper/language.py: açık ayar > sistem dili > İngilizce)
    if LANGUAGE_KEY not in sources:
        values[LANGUAGE_KEY] = language.detected_language(env) or language.DEFAULT_LANGUAGE

    proxy_env = values["client.proxy_env"].strip()
    token_env = values["server.token_env"].strip()
    # proxy_env / token_env'in adını verdiği değişken okunur: eski bir ad olsa da uyarı verilmez
    named = tuple(name for name in (proxy_env, token_env) if name)

    # Kullanımdan kalkan 2.x adları (DEPRECATED_NAMES; plan maddesi FX-35): ayar yeni ortam adıyla ya da bayrakla
    # verilmediyse (belirteç ve proxy adresi için token_env / proxy_env de bir değişken adı vermediyse) okunur
    for name, key in DEPRECATED_NAMES.items():
        raw = env.get(name) or ""
        if not raw.strip() or name in named:
            continue
        if _rank(sources.get(key, _DEFAULT_SOURCE)) >= _rank(Source(LAYER_ENV)):
            continue
        if (key == "client.proxy" and proxy_env) or (key == "server.token" and token_env):
            continue
        values[key] = _deprecated_value(name, key, raw, base)
        sources[key] = Source(LAYER_ENV, name, deprecated=True)

    # Proxy: proxy_env değişkenin adını verir. Aynı ya da daha güçlü katmandan geliyorsa `proxy`nin yerini alır.
    proxy_source = sources.get("client.proxy", _DEFAULT_SOURCE)
    proxy_env_source = sources.get("client.proxy_env", _DEFAULT_SOURCE)
    if proxy_env:
        if values["client.proxy"] and proxy_source.layer == proxy_env_source.layer:
            raise ConfigError(f"{proxy_env_source.name or 'config'}: give [client] proxy or proxy_env, not both")
        if _rank(proxy_env_source) >= _rank(proxy_source):
            values["client.proxy"] = _named_secret(env, proxy_env, f"{proxy_env_source.name}: [client] proxy_env")
            proxy_source = sources["client.proxy"] = Source(proxy_env_source.layer, proxy_env)
    # Bir proxy verildiyse ve daha güçlü bir yerde use_proxy söylenmediyse proxy kullanılır (karar D19). Eski
    # PROXY_URL ise 3.0'daki gibi tek başına açmaz: USE_PROXY=true gerekir
    use_source = sources.get("client.use_proxy", _DEFAULT_SOURCE)
    if values["client.proxy"] and proxy_source.layer != LAYER_DEFAULT and not proxy_source.deprecated \
            and _rank(use_source) < _rank(proxy_source):
        values["client.use_proxy"] = True
        sources["client.use_proxy"] = proxy_source

    # Erişim belirteci: token_env bir değişken adı verdiyse o değişkenden; vermediyse SOFASCORE_SERVER__TOKEN
    token_env_source = sources.get("server.token_env", _DEFAULT_SOURCE)
    if token_env and _rank(token_env_source) >= _rank(sources.get("server.token", _DEFAULT_SOURCE)):
        values["server.token"] = _named_secret(env, token_env, f"{token_env_source.name}: [server] token_env")
        sources["server.token"] = Source(token_env_source.layer, token_env)

    # sofascore_scraper/bridge_health.thresholds: "engelli" eşiği "bozulmuş" eşiğinin altında olamaz
    values["bridge.blocked_after"] = max(values["bridge.blocked_after"], values["bridge.degraded_after"])

    if values["live.source"] == "direct":
        warnings.append(ConfigWarning(
            "live_direct_source", f"live.source is \"direct\": {model.LIVE_DIRECT_WARNING}.",
        ))
    for layer in layers:
        for where, key in layer.retired:
            warnings.append(ConfigWarning(
                "retired_setting", f"{where} is no longer read since 3.1: {RETIRED_SETTINGS[key]}.",
            ))
    # 2.x'in ortam adları okunmaz: her biri için yeni adı söyleyen bir uyarı (plan maddesi P30); kullanımdan
    # kalkan dördü için okunduğunu ya da yok sayıldığını söyleyen bir uyarı (FX-35)
    warnings.extend(legacy_warnings(env, dotenv_values, named, sources))

    sections = {
        section: cls(**{name: values[f"{section}.{name}"] for name in model.SETTING_KEYS[section]})
        for section, cls in model.SECTIONS.items() if section != "schedule"
    }

    def origin(label: str) -> str:
        return sources[label].name if label in sources else file_name

    try:
        check_slice_names(values["defaults.slices"])
    except UnknownSliceName as e:
        raise ConfigError(f"{origin('defaults.slices') or 'config'}: [defaults] slices: {e}") from None

    settings = Settings(
        schema=model.SCHEMA_VERSION,
        slices=MappingProxyType(dict(slices)),
        follows=parse_follows(follows or (), values["defaults.seasons"], origin("follows")),
        sinks=parse_sinks(sinks or (), origin("sinks"), base),
        schedule=ScheduleSettings(
            enabled=values["schedule.enabled"], tasks=parse_tasks(tasks or (), origin("schedule.tasks")),
        ),
        **sections,
    )
    return LoadedSettings(
        settings=settings,
        sources=MappingProxyType(sources),
        config_file=file_name or None,
        overrides_file=str(overrides_path) if overrides_doc is not None else None,
        warnings=tuple(warnings),
    )


# === etkin ayarlar (süreç genelinde) ===============================================================
# ConfigManager ve servisler buradan okur. Dosyalar bir kez okunur; ortam her çağrıda yoklanır (testler ve
# ayarları ortamdan alan modüller buna güvenir).


@dataclass
class _Files:
    """Diskten okunanlar: `reload` çağrılana kadar yeniden okunmaz."""

    config_file: Optional[Path]
    config_doc: Optional[Dict[str, Any]]
    overrides_file: Optional[Path]
    overrides_doc: Optional[Dict[str, Any]]
    # `.env`'in içeriği: yalnızca eski adların uyarısı için (iletinin "set in .env" bilgisi)
    dotenv_values: Dict[str, str]


_lock = threading.RLock()
_explicit_config: Optional[str] = None
_flags: Mapping[str, Any] = MappingProxyType({})
_files: Optional[_Files] = None
_fingerprint: Optional[Tuple[Any, ...]] = None
_loaded: Optional[LoadedSettings] = None
# Ayarda adı geçen değişkenler (proxy_env, token_env): değerleri değişince de yeniden kurulur
_named_env: Tuple[str, ...] = ()


def _env_fingerprint(env: Mapping[str, str]) -> Tuple[Any, ...]:
    return (
        tuple(env.get(name) for name in _WATCHED_ENV),
        tuple(sorted((name, value) for name, value in env.items() if name.startswith(ENV_PREFIX))),
        tuple(env.get(name) for name in _named_env),
    )


def _read_files(env: Mapping[str, str]) -> _Files:
    overrides = (Path(env.get("SOFASCORE_CONFIG_DIR") or "config") / OVERRIDES_FILE_NAME).absolute()
    overrides_doc = _read_overrides(overrides)
    config_file = find_config_file(_explicit_config, env)
    return _Files(
        config_file=config_file,
        config_doc=_read_toml(config_file) if config_file is not None else None,
        overrides_file=overrides if overrides_doc is not None else None,
        overrides_doc=overrides_doc,
        dotenv_values=_read_dotenv(Path(env.get("SOFASCORE_ENV_FILE") or ".env")),
    )


def _after_build(loaded: LoadedSettings) -> None:
    """
    Yeni ayarlar, onları başlangıçta okumuş modüllere uygulanır: gizli değer maskesi ve log kurulumu (günlükçü
    yüklenmişse; log ya da renk ayarı son kurulumdakinden farklıysa yenilenir: logger.follow_settings).
    """
    redact.refresh()
    import sys

    app_logger = sys.modules.get("sofascore_scraper.logger")
    if app_logger is not None:
        app_logger.follow_settings(loaded.settings)


def active() -> LoadedSettings:
    """
    Sürecin geçerli ayarları. İlk çağrıda dosyalar okunur; sonraki çağrılar yalnızca ortamın değişip
    değişmediğine bakar ve değişmediyse aynı nesneyi döndürür.
    """
    global _files, _fingerprint, _loaded, _named_env
    with _lock:
        env = dict(os.environ)
        fingerprint = _env_fingerprint(env)
        if _loaded is not None and _files is not None and fingerprint == _fingerprint:
            return _loaded
        if _files is None:
            _files = _read_files(env)
        previous = _loaded
        loaded = _build(
            env, _files.dotenv_values, _files.config_file, _files.config_doc, _files.overrides_file,
            _files.overrides_doc, _flags,
        )
        _named_env = tuple(
            name for name in (loaded.settings.client.proxy_env, loaded.settings.server.token_env) if name
        )
        # Önce kaydedilir: _after_build'in çağırdıkları (günlükçü) ayarları yeniden okuyabilir
        _loaded, _fingerprint = loaded, _env_fingerprint(env)
        if previous is None:
            if loaded.config_file:
                logger.info(f"Config file loaded: {loaded.config_file}")
            for warning in loaded.warnings:
                logger.warning(warning.message)
        _after_build(loaded)
        return loaded


def active_settings() -> Settings:
    return active().settings


def current() -> Optional[LoadedSettings]:
    """Kurulmuş etkin ayarlar; henüz kurulmadıysa None. Kurmaz (log maskesi gibi kurulumu tetiklememesi gerekenler için)."""
    return _loaded


def reload() -> LoadedSettings:
    """
    overrides.json'ı ve yapılandırma dosyasını yeniden okur. Dosyalardan biri geçersizse ConfigError atılır ve
    önceki ayarlar yürürlükte kalır. `.env` yeniden okunmaz: süreç ortamına başlangıçta yüklenmiştir.
    """
    global _files, _fingerprint, _loaded
    with _lock:
        previous = (_files, _fingerprint, _loaded)
        _files = None
        try:
            return _rebuild()
        except Exception:
            _files, _fingerprint, _loaded = previous
            raise


def _rebuild() -> LoadedSettings:
    """
    Dosyaları okuyup ayarları baştan kurar (active() onları modüllere uygular ve, ilk kurulum gibi, dosyanın
    adını ve uyarıları log'a yazar).
    """
    global _loaded
    with _lock:
        _loaded = None
        return active()


def activate(config_file: Union[str, os.PathLike, None] = None,
             flags: Optional[Mapping[str, Any]] = None) -> LoadedSettings:
    """Giriş noktası için: `--config` ile verilen dosya ve komut satırı bayraklarıyla yeniden yükler."""
    global _explicit_config, _flags
    with _lock:
        _explicit_config = os.fspath(config_file) if config_file is not None else None
        _flags = MappingProxyType(dict(flags or {}))
        return reload()


def reset() -> None:
    """Etkin ayarları unutur (testler için): sonraki çağrı dosyaları ve ortamı baştan okur."""
    global _explicit_config, _flags, _files, _fingerprint, _loaded, _named_env
    with _lock:
        previous = _loaded
        _explicit_config = None
        _flags = MappingProxyType({})
        _files = None
        _fingerprint = None
        _loaded = None
        _named_env = ()
        if previous is not None:
            redact.refresh()


__all__ = [
    "AUTO",
    "CONFIG_ENV",
    "ConfigWarning",
    "CONFIG_FILE_NAME",
    "LAYERS",
    "LAYER_DEFAULT",
    "LAYER_ENV",
    "LAYER_FILE",
    "LAYER_FLAG",
    "LAYER_OVERRIDES",
    "DEPRECATED_LAST_READ_IN",
    "DEPRECATED_NAMES",
    "DEPRECATED_REMOVED_IN",
    "LEGACY_ENV_NAMES",
    "LEGACY_IGNORED",
    "LEGACY_NAMES",
    "LEGACY_READ",
    "LEGACY_REMOVED",
    "RETIRED_SETTINGS",
    "TOKEN_ENV",
    "OVERRIDES_FILE_NAME",
    "LoadedSettings",
    "SINK_KEYS",
    "Source",
    "activate",
    "active",
    "active_settings",
    "coerce",
    "current",
    "env_name",
    "find_config_file",
    "legacy_names_in",
    "legacy_origin",
    "legacy_state",
    "named_variables",
    "legacy_replacement",
    "legacy_warnings",
    "load_settings",
    "mask_sink_options",
    "mask_sink_table",
    "mask_task_options",
    "parse_duration",
    "parse_follows",
    "parse_sinks",
    "parse_tasks",
    "reload",
    "reset",
]
