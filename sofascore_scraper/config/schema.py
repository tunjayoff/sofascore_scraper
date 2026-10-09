"""
Yapılandırma dosyasının (sofascore.toml) JSON Schema'sı: `describe config` ve `config validate` için.

Şema, ayar modelinin alan tablosundan (sofascore_scraper/config/settings.py) üretilir; elle yazılmış ikinci bir anahtar
listesi yoktur, bu yüzden modelden sapamaz. Dosyayı gerçekte denetleyen kod yükleyicidir
(sofascore_scraper/config/loader.py); şema aynı kuralların makinece okunur anlatımıdır.

Her sayıl ayarın yanında `x-env` durur: onu ezen SOFASCORE_<BÖLÜM>__<ANAHTAR> değişkeni. 3.0'ın `x-legacy-env`
alanı (2.x'in adı) eski adlarla birlikte 3.1'de kalktı (plan maddesi P30).
"""
from __future__ import annotations

from dataclasses import Field
from typing import Any, Dict, List

from sofascore_scraper.config import settings as model
from sofascore_scraper.config.loader import (
    ENV_FOLLOWS,
    ENV_SCHEDULE_TASKS,
    ENV_SINKS,
    ENV_SLICES,
    LANGUAGE_KEY,
    env_name,
)
from sofascore_scraper.sports import sport_slugs

SCHEMA_ID = "sofascore.config/1"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_STRING_LIST: Dict[str, Any] = {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}
_SEASONS: Dict[str, Any] = {
    "anyOf": [
        {"type": "string", "pattern": "^(current|all|last:[1-9][0-9]*)$"},
        {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1},
    ]
}
_SLICE_OVERRIDE: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"enable": _STRING_LIST, "disable": _STRING_LIST},
}


def _json_default(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _setting_schema(key: str, f: "Field[Any]") -> Dict[str, Any]:
    meta = f.metadata
    kind = meta["kind"]
    out: Dict[str, Any]
    if kind in (model.KIND_STR, model.KIND_PATH):
        out = {"type": "string"}
    elif kind == model.KIND_URL:
        out = {"type": "string", "pattern": "^[Hh][Tt][Tt][Pp][Ss]?://"}
    elif kind == model.KIND_BOOL:
        out = {"type": "boolean"}
    elif kind == model.KIND_INT:
        out = {"type": "integer"}
    elif kind == model.KIND_FLOAT:
        out = {"type": "number"}
    elif kind == model.KIND_RATE:
        out = {"anyOf": [{"type": "number", "minimum": 0}, {"const": "off"}]}
    elif kind == model.KIND_ENUM:
        out = {"type": "string", "enum": list(meta["choices"])}
    elif kind == model.KIND_STR_LIST:
        items: Dict[str, Any] = {"type": "string", "minLength": 1}
        if meta["choices"] is not None:
            items["enum"] = list(meta["choices"])
        out = {"type": "array", "items": items, "uniqueItems": True}
    elif kind == model.KIND_SEASONS:
        out = dict(_SEASONS)
    else:  # pragma: no cover - model yeni bir tür eklerse
        raise AssertionError(kind)
    if kind in (model.KIND_INT, model.KIND_FLOAT):
        if meta["minimum"] is not None:
            out["exclusiveMinimum" if meta["exclusive_minimum"] else "minimum"] = meta["minimum"]
        if meta["maximum"] is not None:
            out["maximum"] = meta["maximum"]
    out["description"] = meta["doc"]
    # Dilin varsayılanı sistemden çıkarılır; şemaya sabit bir değer yazılmaz
    if key != LANGUAGE_KEY:
        out["default"] = _json_default(model.default_of(f))
    if meta["secret"]:
        out["x-secret"] = True
    out["x-env"] = env_name(key)
    return out


def _follow_schema() -> Dict[str, Any]:
    entity = {"type": "integer", "minimum": 1}
    return {
        "type": "array",
        "description": (
            "Followed tournaments, teams, players or events; exactly one of tournament, team, player, event per entry."
        ),
        "x-env": ENV_FOLLOWS,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **{kind: entity for kind in model.FOLLOW_KINDS},
                "name": {"type": "string"},
                "sport": {"type": "string", "enum": list(sport_slugs())},
                "seasons": _SEASONS,
                "slices": {"anyOf": [_STRING_LIST, _SLICE_OVERRIDE]},
                "live": {"type": "boolean", "default": False},
                "enabled": {"type": "boolean", "default": True},
            },
            "oneOf": [{"required": [kind]} for kind in model.FOLLOW_KINDS],
        },
    }


def _sink_schema() -> Dict[str, Any]:
    return {
        "type": "array",
        "description": "Output sinks of the event streams. Keys the schema does not name are passed to the sink as options.",
        "x-env": ENV_SINKS,
        "items": {
            "type": "object",
            "required": ["name", "type"],
            "additionalProperties": True,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "type": {"type": "string", "enum": list(model.SINK_TYPES)},
                "events": {**_STRING_LIST, "default": ["*"]},
                "url": {"type": "string"},
                "secret_env": {"type": "string"},
                "allow_unsigned": {"type": "boolean", "default": False},
                "path": {"type": "string"},
            },
        },
    }


def _task_schema() -> Dict[str, Any]:
    return {
        "type": "array",
        "description": "Scheduled tasks; exactly one of every, cron per entry.",
        "x-env": ENV_SCHEDULE_TASKS,
        "items": {
            "type": "object",
            "required": ["run"],
            "additionalProperties": True,
            "properties": {
                "run": {"type": "string", "minLength": 1},
                "every": {"type": "string", "pattern": "^[0-9]+(\\.[0-9]+)?\\s*[smhdSMHD]$"},
                "cron": {"type": "string"},
            },
            "oneOf": [{"required": ["every"]}, {"required": ["cron"]}],
        },
    }


def config_schema() -> Dict[str, Any]:
    """Yapılandırma dosyasının JSON Schema'sı (draft 2020-12). Yalnızca dosyada yazılabilen anahtarları içerir."""
    properties: Dict[str, Any] = {
        "schema": {"const": model.SCHEMA_VERSION, "description": "Version of the config file format."},
    }
    for section in model.SECTIONS:
        keys = {
            name: _setting_schema(f"{section}.{name}", f)
            for name, f in model.SETTING_KEYS[section].items()
            if f.metadata["in_file"]
        }
        if section == "schedule":
            keys["task"] = _task_schema()
        properties[section] = {"type": "object", "additionalProperties": False, "properties": keys}
    properties["slices"] = {
        "type": "object",
        "description": "Per-sport changes to the default slice selection.",
        "x-env": ENV_SLICES,
        "additionalProperties": False,
        "properties": {sport: _SLICE_OVERRIDE for sport in sport_slugs()},
    }
    properties["follow"] = _follow_schema()
    properties["sink"] = _sink_schema()
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "sofascore.toml",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }


def environment_only_keys() -> List[str]:
    """Dosyaya yazılamayan, yalnızca ortamdan okunan ayarlar ('client.captcha_token', 'server.token')."""
    return [key for key, f in model.iter_settings() if not f.metadata["in_file"]]


__all__ = ["JSON_SCHEMA_DIALECT", "SCHEMA_ID", "config_schema", "environment_only_keys"]
