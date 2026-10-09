"""
API v1: ayarlar (docs/design/02-services.md bölüm 4.3 ve 6; karar D4).

    GET   /api/v1/settings    her ayar: değeri, hangi katmandan geldiği, kilitli mi, buradan değiştirilebilir mi
    PATCH /api/v1/settings    {"values": {"client.rate": 3, "display.language": null}}

Değerler ayar modelinin değerleridir (sofascore_scraper/config/settings.py), anahtarlar `bölüm.anahtar` biçimindedir. Gizli
değerler maskelenir: belirteçler `***` olarak, proxy adresi parolası maskelenmiş olarak döner.

Katmanlar ve kilit. Bir değer yapılandırma dosyasından, süreç ortamından ya da bir bayraktan geliyorsa
kilitlidir (`locked`): arayüzden yazılan değer onun altında kalır ve etkisi olmaz. PATCH kilitli bir anahtarı
reddeder (400 `invalid_request`, `details.locked`) ve hiçbir şey yazmaz. `.env`'deki SOFASCORE_*__* satırları süreç
ortamıdır, onlar da kilitlidir (2.x'in `.env` katmanı ve ortam adları 3.1'de kalktı, plan maddesi P30).

Yazma. PATCH, `CONFIG_DIR/overrides.json`'a yazar (sofascore_scraper/config/overrides.py): varsayılanların üstünde,
yapılandırma dosyasının altında duran, makinenin yazdığı katman. `null` anahtarı oradan siler (alttaki katmanın değeri
geçerli olur). İstek ya bütünüyle uygulanır ya da hiç uygulanmaz.

Buradan değiştirilebilen anahtarlar WRITABLE tablosundadır: bugünkü Ayarlar sayfasının düzenlediği ayarlar.
Diğerleri (sunucunun bağlandığı adres ve Host listesi, belirteç, dizin yolları, canlı kaynak ...) yalnızca
okunur; yapılandırma dosyasından ya da ortamdan verilir. Tablodaki kurallar yükleyicinin kurallarına ek
güvenlik sınırlarıdır: API adresi yalnızca SofaScore'un https adresleri olabilir, veri dizini proje ya da ev
dizininin içinde kalır.

Dilim seçimi (plan maddesi P27). `defaults.slices` ([defaults] slices) yazılabilir bir ayardır. Sporların
farkı ([slices.<spor>]) belgenin `slices` listesindedir ve PATCH'e `slices.<spor>` anahtarıyla verilir:
`{"values": {"slices.football": {"enable": ["odds"], "disable": []}}}`; `null` farkı siler. Adlar kayıt
defterinin dilim anahtarları ya da grup adlarıdır (sofascore_scraper/sports.py `check_slice_names`); bilinmeyen ad 400.

Her ayarın üst verisi (05-web-ui.md G4): türü, alt / üst sınırı, seçenekleri, bölümü ve değişikliğin yeniden
başlatma isteyip istemediği (`restart_needed`).

Veri dizini değişimi çalışan iş varken reddedilir (409 `job_running`). Yeni dizin önce iş deposuyla açılır;
açılamıyorsa hiçbir şey yazılmaz ve neden hata kodundan okunur: başka bir sürecin kilidi 409 (sahibi
`details.holder`da), deponun açamadığı bir dizin 507 `storage_error` (`details.class`: SchemaTooNew,
StoreBusy, ...; işletim sistemi hatasında `details.reason`). Değişimden sonra eski dizinin deposu bu süreçte
kapatılır (`close_data_dir`; plan maddesi FX-13): eski dizin uygulama çalışırken taşınabilir ya da silinebilir.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from sofascore_scraper import sports
from sofascore_scraper.config import settings as model
from sofascore_scraper.errors import PlatformError, UsageError
from sofascore_scraper.exceptions import ConfigError
from sofascore_scraper.redact import MASK
from sofascore_scraper.store import StoreError
from sofascore_scraper.web import deps
from sofascore_scraper.web.errors import STORAGE_ERROR, ValidationFailed, error_responses

logger = logging.getLogger("WebAPI")
router = APIRouter(tags=["settings"])

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR_KEY = "storage.data_dir"
PROXY_KEY = "client.proxy"
# API kökü yalnızca SofaScore'un iki sunucusundan biri olabilir (2.x'in ayarlar rotasındaki kural)
ALLOWED_API_HOSTS = frozenset({"www.sofascore.com", "api.sofascore.com"})
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
DEFAULTS_SLICES_KEY = "defaults.slices"
SLICES_PREFIX = "slices."
# Değişikliği ancak süreç yeniden başlayınca etkili olan ayarlar: sunucunun bağlandığı adres ve Host listesi,
# log dosyasının yeri ve biçimi, canlı servisin ve zamanlayıcının ayarları (`ssc serve` / `ssc watch` başlarken
# okunur), depolama dayanıklılığı. Bölümün tamamı: bölüm adı; tek anahtar: `bölüm.ad`.
RESTART_NEEDED = frozenset({
    "server", "live", "schedule", "storage.durability", "log.dir", "log.to_file", "log.format", "log.max_mb",
    "log.backup_count",
})
_CONTROL_CHARACTERS = "\r\n\x00"


# --- yazılabilir anahtarlar ------------------------------------------------------------------------


def _base_url(value: Any) -> Any:
    url = urlparse(str(value))
    if url.scheme != "https" or url.hostname not in ALLOWED_API_HOSTS:
        raise ValueError(f"must be an https address on {', '.join(sorted(ALLOWED_API_HOSTS))}")
    return value


def _slice_names(value: Any) -> Any:
    """Her ad bir dilim anahtarı ya da grup adı olmalı (sofascore_scraper/sports.py); değilse ValueError."""
    try:
        sports.check_slice_names(value)
    except sports.UnknownSliceName as e:
        raise ValueError(str(e)) from None
    return value


def _proxy(value: Any) -> Any:
    if value:
        url = urlparse(str(value))
        if url.scheme not in PROXY_SCHEMES or not url.hostname:
            raise ValueError("must be http(s)://host:port or socks5://host:port")
    return value


def _data_dir(value: Any) -> Any:
    """Mutlak yola çevirir; proje ya da ev dizininin içinde (ama kendisi değil) olmalıdır."""
    if not str(value).strip():
        raise ValueError("must not be empty")
    absolute = os.path.abspath(os.path.expanduser(str(value)))
    if absolute == os.path.abspath(deps.config_manager().get_data_dir()):
        return absolute
    resolved = Path(absolute).resolve()
    home, root = Path.home().resolve(), REPO_ROOT.resolve()
    if resolved in (home, root) or not (resolved.is_relative_to(root) or resolved.is_relative_to(home)):
        raise ValueError("must be a folder inside the project or your home directory")
    return absolute


@dataclass(frozen=True)
class Rule:
    """
    Yazılabilir bir anahtarın ek kuralı (türü, alt sınırı ve seçenekleri ayar modelindedir).

    maximum     üst sınır (sayılar)
    max_length  en uzun metin
    check       değeri denetleyen ve yazılacak biçimini döndüren işlev; uymuyorsa ValueError
    removable   `null` ile silinebilir mi (alttaki katmanın değerine dönülür)
    """

    maximum: Optional[float] = None
    max_length: Optional[int] = None
    check: Optional[Callable[[Any], Any]] = None
    removable: bool = True


WRITABLE: Mapping[str, Rule] = {
    "display.language": Rule(),
    "display.use_color": Rule(),
    "display.date_format": Rule(max_length=50),
    "client.base_url": Rule(max_length=500, check=_base_url),
    "client.use_proxy": Rule(),
    PROXY_KEY: Rule(max_length=500, check=_proxy),
    "client.max_concurrent": Rule(maximum=50),
    "client.rate": Rule(maximum=1000),
    "client.wait_time_min": Rule(maximum=60),
    "client.wait_time_max": Rule(maximum=60),
    "client.timeout_seconds": Rule(maximum=300),
    "client.retries": Rule(maximum=10),
    "breaker.rate_limit_consecutive": Rule(maximum=1000),
    "breaker.rate_limit_ratio": Rule(),
    "breaker.server_error_consecutive": Rule(maximum=1000),
    "fetch.only_finished": Rule(),
    # Seçilecek dilimler: anahtarlar ya da grup adları (boş liste: yalnızca maç sayfası)
    DEFAULTS_SLICES_KEY: Rule(check=_slice_names),
    "refresh.window_hours": Rule(maximum=720),
    "log.level": Rule(),
    "log.debug": Rule(),
    # Dizin değişimi iş deposunu da taşır: hedef bilinmeli, bu yüzden `null` ile silinemez
    DATA_DIR_KEY: Rule(max_length=500, check=_data_dir, removable=False),
}


# --- modeller ------------------------------------------------------------------------------------


SettingType = Literal["string", "path", "url", "integer", "number", "boolean", "choice", "string_list", "rate",
                      "seasons"]
_TYPES: Mapping[str, str] = {
    model.KIND_STR: "string", model.KIND_PATH: "path", model.KIND_URL: "url", model.KIND_INT: "integer",
    model.KIND_FLOAT: "number", model.KIND_BOOL: "boolean", model.KIND_ENUM: "choice",
    model.KIND_STR_LIST: "string_list", model.KIND_RATE: "rate", model.KIND_SEASONS: "seasons",
}


class SettingMetadata(BaseModel):
    """How a setting can be changed (05-web-ui.md G4). One row per setting, in the order of `settings`."""

    key: str = Field(description="`section.key`, as in the config file.")
    section: str = Field(description="The config file section (`[client]`, `[defaults]` ...).")
    type: SettingType = Field(
        description="`rate`: requests per second, 0 or \"off\" for no limit; `seasons`: current, all, last:N or a "
                    "list of season ids; `string_list`: a list of strings.",
    )
    description: str = Field(description="What the setting does, in English.")
    minimum: Optional[float] = Field(default=None, description="Lowest accepted value, when there is one.")
    exclusive_minimum: bool = Field(default=False, description="The value must be above `minimum`, not equal to it.")
    maximum: Optional[float] = Field(default=None, description="Highest accepted value, when there is one.")
    choices: Optional[List[str]] = Field(default=None, description="Accepted values of a `choice` setting.")
    max_length: Optional[int] = Field(default=None, description="Longest accepted text when changed through the API.")
    restart_needed: bool = Field(
        description="A change takes effect only after `ssc serve` or `ssc watch` is started again.",
    )


class Setting(BaseModel):
    key: str = Field(description="`section.key`, as in the config file.")
    value: Any = Field(description="The value in force. Secrets are masked.")
    source: Literal["default", "overrides", "file", "env", "flag"] = Field(
        description="The layer the value comes from, weakest to strongest in this order.",
    )
    source_name: str = Field(description="The file or the environment variable, when there is one.")
    locked: bool = Field(description="Pinned by the config file, the environment or a flag: a change made here would have no effect.")
    writable: bool = Field(description="Whether PATCH accepts this key right now.")
    secret: bool
    replaced_by: Optional[str] = Field(
        default=None,
        description="When the value comes from a deprecated 2.x environment variable (`source_name`; still read "
                    "in 3.1, removed in 3.2), the variable to use instead; otherwise null.",
    )


class SportSliceSelection(BaseModel):
    sport: str = Field(description="Registered sport slug.")
    enable: List[str] = Field(description="Slice keys or groups added to `defaults.slices` for this sport.")
    disable: List[str] = Field(description="Slice keys or groups removed for this sport (applied after `enable`).")
    source: Literal["default", "overrides", "file", "env", "flag"] = Field(
        description="The layer the sport's selection comes from; `default` when none is set.",
    )
    source_name: str
    locked: bool = Field(description="Pinned by the config file or the environment ([slices.<sport>]).")
    writable: bool = Field(description="Whether PATCH accepts `slices.<sport>` right now.")


class SettingsDocument(BaseModel):
    config_file: Optional[str] = Field(default=None, description="Path of the config file in use, if any.")
    overrides_file: Optional[str] = Field(default=None, description="Path of the file PATCH has written, if any.")
    settings: List[Setting]
    metadata: List[SettingMetadata] = Field(
        description="Type, limits, choices, section and restart need of each setting, in the order of `settings`.",
    )
    slices: List[SportSliceSelection] = Field(
        description="Per-sport changes to the default slice selection ([slices.<sport>]), one row per registered "
                    "sport in registry order. Change one with PATCH `{\"values\": {\"slices.<sport>\": {\"enable\": "
                    "[...], \"disable\": [...]}}}`; null removes it.",
    )


class SettingsResponse(BaseModel):
    data: SettingsDocument


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: Dict[str, Any] = Field(
        description="`section.key` to the new value; null removes the value written here earlier. "
                    "`slices.<sport>` takes `{\"enable\": [...], \"disable\": [...]}`.",
    )


def _shown(key: str, value: Any, secret: bool) -> Any:
    if key == PROXY_KEY:
        # Kullanıcı adı ve sunucu görünür kalır; parola asla dönmez
        from sofascore_scraper.web.api.proxy import mask_proxy_url

        return mask_proxy_url(str(value or ""))
    if secret:
        return MASK if value else ""
    return list(value) if isinstance(value, tuple) else value


def _document() -> SettingsDocument:
    loaded = deps.loaded_settings()
    rows: List[Setting] = []
    metadata: List[SettingMetadata] = []
    for key, f in model.iter_settings():
        source = loaded.source(key)
        secret = bool(f.metadata["secret"])
        meta = f.metadata
        section, _, _name = key.partition(".")
        rule = WRITABLE.get(key)
        metadata.append(SettingMetadata(
            key=key,
            section=section,
            type=_TYPES[meta["kind"]],  # type: ignore[arg-type]
            description=meta["doc"],
            minimum=meta["minimum"],
            exclusive_minimum=bool(meta["exclusive_minimum"]),
            maximum=_maximum(meta["maximum"], rule),
            choices=list(meta["choices"]) if meta["choices"] is not None else None,
            max_length=rule.max_length if rule is not None else None,
            restart_needed=section in RESTART_NEEDED or key in RESTART_NEEDED,
        ))
        rows.append(Setting(
            key=key,
            value=_shown(key, loaded.settings.get(key), secret),
            source=source.layer,  # type: ignore[arg-type]
            source_name=source.name,
            locked=source.locked,
            writable=key in WRITABLE and not source.locked,
            secret=secret,
            replaced_by=loaded.replaced_by(key),
        ))
    return SettingsDocument(config_file=loaded.config_file, overrides_file=loaded.overrides_file, settings=rows,
                            metadata=metadata, slices=_sport_selections(loaded))


def _maximum(model_maximum: Optional[float], rule: Optional[Rule]) -> Optional[float]:
    """Modelin ve (yazılabilirse) API kuralının üst sınırlarından küçüğü."""
    limits = [value for value in (model_maximum, rule.maximum if rule is not None else None) if value is not None]
    return min(limits) if limits else None


def _sport_selections(loaded: Any) -> List[SportSliceSelection]:
    """Her kayıtlı spor için [slices.<spor>] farkı ve kaynağı."""
    rows: List[SportSliceSelection] = []
    for sport in sports.sport_slugs():
        override = loaded.settings.slices.get(sport)
        source = loaded.source(SLICES_PREFIX + sport)
        rows.append(SportSliceSelection(
            sport=sport,
            enable=list(override.enable) if override is not None else [],
            disable=list(override.disable) if override is not None else [],
            source=source.layer,  # type: ignore[arg-type]
            source_name=source.name,
            locked=source.locked,
            writable=not source.locked,
        ))
    return rows


# --- okuma ---------------------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsResponse, operation_id="getSettings", summary="Get the settings")
def get_settings() -> SettingsResponse:
    """Every setting with its value, the layer it comes from and whether it can be changed here."""
    return SettingsResponse(data=_document())


# --- yazma ---------------------------------------------------------------------------------------


def _issue(key: str, message: str, kind: str = "value_error") -> Dict[str, Any]:
    return {"loc": ["body", "values", key], "message": message, "type": kind}


def _checked(key: str, value: Any) -> Any:
    """Değeri yükleyicinin ve tablonun kurallarıyla denetler; yazılacak biçimini döndürür (ValueError: uymuyor)."""
    from sofascore_scraper.config import overrides

    rule = WRITABLE[key]
    if value is None:
        if not rule.removable:
            raise ValueError("cannot be removed; give a value")
        return None
    if isinstance(value, str):
        if any(c in value for c in _CONTROL_CHARACTERS):
            raise ValueError("control characters are not allowed")
        if rule.max_length is not None and len(value) > rule.max_length:
            raise ValueError(f"must be at most {rule.max_length} characters")
    try:
        value = overrides.checked_value(key, value)
    except ConfigError as e:
        if model.SETTING_KEYS[key.partition(".")[0]][key.partition(".")[2]].metadata["secret"]:
            # Yükleyicinin iletisi gönderilen değeri de yazar; gizli bir değer hata gövdesine girmez
            raise ValueError("not a valid value") from None
        # "bölüm.anahtar: neden" → neden
        raise ValueError(str(e).partition(": ")[2] or str(e)) from None
    if rule.maximum is not None and isinstance(value, (int, float)) and not isinstance(value, bool) \
            and value > rule.maximum:
        raise ValueError(f"must be at most {rule.maximum:g}")
    return rule.check(value) if rule.check is not None else value


def _checked_slices(key: str, value: Any) -> Any:
    """`slices.<spor>`: None ya da {"enable": [...], "disable": [...]}; yükleyicinin kuralıyla (ValueError)."""
    from sofascore_scraper.config import overrides

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('expected {"enable": [...], "disable": [...]} or null')
    try:
        return overrides.checked_slice_override(key, value)
    except ConfigError as e:
        # "slices.<spor>: neden" → neden
        raise ValueError(str(e).partition(": ")[2] or str(e)) from None


def _restored_proxy(submitted: str) -> str:
    """
    Maskeli adres geri gönderildiyse (parola = `***`) saklanan parolayı yerine koyar; adres ya da kullanıcı
    değiştiyse parolanın yeniden yazılmasını ister (eski rotanın kuralı, tests/test_settings_proxy.py).
    """
    from fastapi import HTTPException

    from sofascore_scraper.web.api.proxy import restore_proxy_password as _restore_proxy_password

    try:
        return _restore_proxy_password(submitted.strip(), deps.config_manager().get_proxy_url())
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {}
        raise ValidationFailed(
            "The request is not valid.",
            {"errors": [_issue(PROXY_KEY, str(detail.get("message") or e.detail),
                               str(detail.get("reason") or "value_error"))]},
        ) from None


def _validated(values: Mapping[str, Any]) -> Dict[str, Any]:
    """İstenen değişiklikleri denetler; yazılacak değerleri döndürür. Hiçbir şey yazmaz."""
    known = dict(model.iter_settings())
    known_sports = sports.sport_slugs()
    unknown = sorted(key for key in values if key not in known
                     and not (key.startswith(SLICES_PREFIX) and key[len(SLICES_PREFIX):] in known_sports))
    if unknown:
        raise ValidationFailed(
            "The request is not valid.", {"errors": [_issue(key, "unknown setting", "unknown_key") for key in unknown]},
        )
    read_only = sorted(key for key in values if key not in WRITABLE and not key.startswith(SLICES_PREFIX))
    if read_only:
        raise UsageError(
            "These settings cannot be changed through the API; set them in the config file or the environment.",
            {"read_only": read_only},
        )
    loaded = deps.loaded_settings()
    locked = [
        {"key": key, "source": loaded.source(key).layer, "source_name": loaded.source(key).name}
        for key in sorted(values) if values[key] is not None and loaded.source(key).locked
    ]
    if locked:
        raise UsageError(
            "These settings are pinned by the config file, the environment or a flag; a change made here would "
            "have no effect.",
            {"locked": locked},
        )
    changes: Dict[str, Any] = {}
    issues: List[Dict[str, Any]] = []
    for key, value in values.items():
        try:
            changes[key] = _checked_slices(key, value) if key.startswith(SLICES_PREFIX) else _checked(key, value)
        except ValueError as e:
            issues.append(_issue(key, str(e)))
    if issues:
        raise ValidationFailed("The request is not valid.", {"errors": issues})
    if changes.get(PROXY_KEY):
        changes[PROXY_KEY] = _restored_proxy(str(changes[PROXY_KEY]))
    return changes


def _storage_error(exc: BaseException, path: str) -> PlatformError:
    """İşletim sisteminin ya da SQLite'ın reddettiği dizin: `storage_error`, nedeni ve yolu `details`te."""
    reason = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
    return PlatformError(STORAGE_ERROR, f"storage error: {reason} ({path})", {
        "class": type(exc).__name__, "path": path, "errno": getattr(exc, "errno", None), "reason": reason,
    })


def _write(changes: Mapping[str, Any]) -> None:
    """Değişiklikleri overrides.json'a yazar ve ayarları yeniden yükler."""
    from sofascore_scraper import redact
    from sofascore_scraper.config import overrides

    try:
        overrides.write_overrides(changes)
    except ConfigError as e:
        raise ValidationFailed("The request is not valid.", {"errors": [_issue("", str(e), "config_invalid")]}) from None
    except OSError as e:
        raise _storage_error(e, str(overrides.overrides_path())) from e
    redact.refresh()  # yeni proxy parolası loglarda hemen maskelensin


def _move_data_dir(changes: Mapping[str, Any], target: str) -> None:
    """
    Veri dizinini değiştirir: önce iş deposu yeni dizinde açılır (açılamıyorsa hiçbir şey yazılmaz), sonra
    ayar yazılır. Ayar yazılamazsa depo geçerli dizine geri döner. Çalışan iş ya da süren bir veri işlemi
    varken reddedilir (JobStoreConflict → 409).
    """
    from sofascore_scraper.store import default_db_path

    store = deps.job_store()
    previous = os.path.abspath(deps.config_manager().get_data_dir())
    with store.exclusive("data_dir_change"):
        try:
            store.rebind(default_db_path(target))
        except StoreError:
            raise  # LeaseHeld, SchemaTooNew, StoreBusy ...: kodu ve ayrıntısı hata tablosundan
        except (OSError, sqlite3.Error) as e:
            raise _storage_error(e, target) from e
        try:
            _write(changes)
        finally:
            # Ayar yazılamadıysa depo geçerli veri dizinine geri döner; yazıldıysa bu çağrı bir şey yapmaz
            current = os.path.abspath(deps.config_manager().get_data_dir())
            store.rebind(default_db_path(current))
            deps.refresh_job_mirror()
    # İş deposunun `maintenance` kilidi (eski dizinde) bırakıldıktan sonra
    if os.path.normcase(os.path.realpath(current)) != os.path.normcase(os.path.realpath(previous)):
        close_data_dir(previous)


def close_data_dir(data_dir: str) -> bool:
    """
    Eski veri dizininin bu süreçteki depolarını (yazılabilir ve salt okunur; `open_store`un kayıt defteri) kapatır:
    Windows'ta açık bir veri dizini taşınamaz ve silinemez (#120, plan maddesi FX-13). Depo açık değilse
    kurulmadan açılır (`create=False`, katalog eşitlenmeden) ve kapatılır; dizin bir depo değilse bir şey yapılmaz.
    Bu süreç o dizinin bir kilidini tutuyorsa (ör. `serve`in sink dağıtıcısı `sinks`i) depo açık kalır: onu
    kullanan thread'in altından kapatılmaz; uyarı yazılır. Kapattıysa True.
    """
    from sofascore_scraper.store import open_store

    names = ("writer", "live", "sinks", "export", "maintenance", *(f"watcher:{spec.slug}" for spec in sports.SPORTS))
    try:
        found = open_store(data_dir, create=False, sync_catalog=False)
        held = [name for name in names
                if (info := found.lease_holder(name)) is not None and info.pid == os.getpid()]
    except (StoreError, OSError, sqlite3.Error):
        return False  # bir depo değil (ya da açılamıyor): kapatılacak bir şey yok
    if held:
        logger.warning("The previous data directory stays open: this process holds its %s lease", ", ".join(held))
        return False
    found.close()
    try:
        open_store(data_dir, create=False, readonly=True, sync_catalog=False).close()
    except (StoreError, OSError, sqlite3.Error):
        pass
    return True


@router.patch(
    "/settings",
    response_model=SettingsResponse,
    operation_id="updateSettings",
    summary="Change settings",
    responses=error_responses(
        "invalid_request", "forbidden_origin", "job_running", "data_operation_running", "instance_running",
        "storage_error",
    ),
)
def update_settings(body: SettingsPatch) -> SettingsResponse:
    """
    Change settings and return all of them. The request is applied as a whole or not at all. A key that is
    pinned by the config file, the environment or a flag is refused (`details.locked`), as is a key that
    cannot be changed through the API (`details.read_only`). Changing `storage.data_dir` is refused while a
    job runs; downloads and the job history then use the new folder, and files are not moved.
    """
    changes = _validated(body.values)
    if not changes:
        return SettingsResponse(data=_document())
    target = changes.get(DATA_DIR_KEY)
    current = os.path.abspath(deps.config_manager().get_data_dir())
    if target is not None and target != current:
        _move_data_dir(changes, target)
    else:
        _write(changes)
    return SettingsResponse(data=_document())


__all__ = ["RESTART_NEEDED", "WRITABLE", "Rule", "close_data_dir", "router"]
