"""
Normalleştirilmiş şema v1'in JSON Schema belgesi (`ssc describe schemas` için; docs/design/04-schema-v1.md).

Belge modellerden üretilir (sofascore_scraper/schema/models.py): alan tipleri tip ipuçlarından, açıklama, birim ve kaynak
alanın `metadata`sından gelir. Elle yazılmış ikinci bir şema yoktur, bu yüzden modelden ayrılamaz.

Üretilen belge JSON Schema 2020-12'dir. Sözleşmenin kuralları belgeye şöyle yansır:
  * Her alan `required`dır; bilinmeyen değer `null`dır.
  * `additionalProperties` yazılmaz: alan eklemek sürümü artırmaz, tüketici bilmediği alanı yok saymalıdır.
  * Kapalı sayımlar `enum` ile yazılır. Açık sayımlar (`open_enum`) `enum` taşımaz; bilinen değerleri
    `examples`tadır, çünkü yeni bir değer eski şemayla doğrulanan bir kaydı geçersiz kılmamalıdır.
  * `x-unit` ve `x-source` alanın birimini ve SofaScore yükündeki kaynağını taşır (doğrulamayı etkilemez).

Bu modül saftır: yalnızca standart kitaplığı ve sofascore_scraper.schema.models'i içe aktarır.
"""
from __future__ import annotations

import collections.abc
import dataclasses
import typing
from typing import Any, Dict, List, Mapping, Tuple, Union

from sofascore_scraper.schema.models import EVENT_ENVELOPE_ID, MODELS, RECORDS, SCHEMA_ID, SCHEMA_VERSION, Model, json_name

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_PRIMITIVES: Dict[Any, str] = {int: "integer", str: "string", bool: "boolean", float: "number"}
_NONE = type(None)


def _ref(model: type) -> Dict[str, Any]:
    return {"$ref": f"#/$defs/{model.__name__}"}


def _nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Şemanın `null` da kabul eden hali."""
    if not schema:  # herhangi bir JSON değeri: null zaten içinde
        return schema
    if "enum" in schema:
        return {**schema, "type": [schema["type"], "null"], "enum": [*schema["enum"], None]}
    if isinstance(schema.get("type"), str):
        return {**schema, "type": [schema["type"], "null"]}
    return {"anyOf": [schema, {"type": "null"}]}


def _literal(values: Tuple[Any, ...], meta: Mapping[str, Any]) -> Dict[str, Any]:
    if len(values) == 1 and not meta.get("open"):
        return {"const": values[0]}
    if meta.get("open"):
        return {"type": "string", "examples": list(values)}
    return {"type": "string", "enum": list(values)}


def type_schema(annotation: Any, meta: Mapping[str, Any]) -> Dict[str, Any]:
    """Bir tip ipucunun JSON Schema karşılığı. Desteklenmeyen tip TypeError verir (model yanlış yazılmış)."""
    if annotation is Any:
        return {}
    if annotation is None or annotation is _NONE:
        return {"type": "null"}
    if annotation in _PRIMITIVES:
        schema: Dict[str, Any] = {"type": _PRIMITIVES[annotation]}
        if meta.get("format"):
            schema["format"] = meta["format"]
        if meta.get("known"):
            schema["examples"] = list(meta["known"])
        return schema
    if isinstance(annotation, type) and issubclass(annotation, Model):
        return _ref(annotation)

    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Literal:
        return _literal(args, meta)
    if origin is Union:
        members = [arg for arg in args if arg is not _NONE]
        inner = type_schema(members[0], meta) if len(members) == 1 else {
            "oneOf": [type_schema(member, meta) for member in members]}
        return _nullable(inner) if len(members) != len(args) else inner
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        return {"type": "array", "items": type_schema(args[0], {})}
    if origin is not None and isinstance(origin, type) and issubclass(origin, collections.abc.Mapping):
        return {"type": "object"}
    raise TypeError(f"unsupported annotation in a schema model: {annotation!r}")


def field_schema(annotation: Any, meta: Mapping[str, Any]) -> Dict[str, Any]:
    """Bir alanın şeması: tipi, açıklaması, birimi ve kaynağı."""
    schema = dict(type_schema(annotation, meta))
    if meta.get("doc"):
        schema["description"] = meta["doc"]
    if meta.get("unit"):
        schema["x-unit"] = meta["unit"]
    if meta.get("source"):
        schema["x-source"] = meta["source"]
    return schema


def model_fields(model: type) -> List[Tuple[str, Any, Mapping[str, Any]]]:
    """Modelin alanları: (JSON adı, tip ipucu, metadata), alan sırasıyla."""
    hints = typing.get_type_hints(model)
    return [(json_name(item), hints[item.name], item.metadata) for item in dataclasses.fields(model)]


def model_schema(model: type) -> Dict[str, Any]:
    """Bir modelin nesne şeması (`$defs` altındaki girdisi)."""
    fields = model_fields(model)
    return {
        "type": "object",
        "title": model.__name__,
        "description": getattr(model, "SUMMARY", ""),
        "required": [name for name, _annotation, _meta in fields],
        "properties": {name: field_schema(annotation, meta) for name, annotation, meta in fields},
    }


def json_schema() -> Dict[str, Any]:
    """Bütün modellerin şeması tek belgede; modeller `$defs` altındadır."""
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "Normalized schema",
        "description": (
            "The records the platform gives out: API v1 resources, export datasets, stream events. Every field "
            "is always present; an unknown value is null. Fields may be added without a new version, so ignore "
            "fields you do not know."
        ),
        "x-schema-version": SCHEMA_VERSION,
        "x-records": [model.__name__ for model in RECORDS],
        "$defs": {model.__name__: model_schema(model) for model in MODELS},
    }


def record_schema(name: str) -> Dict[str, Any]:
    """Tek bir kayıt türünü doğrulayan belge: `record_schema("Event")`. Bilinmeyen ad KeyError verir."""
    document = json_schema()
    if name not in document["$defs"]:
        raise KeyError(name)
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"{SCHEMA_ID}/{name}",
        "$ref": f"#/$defs/{name}",
        "$defs": document["$defs"],
    }


def describe() -> Dict[str, Any]:
    """`ssc describe schemas` için: sürüm, kayıt türleri ve şema belgesi."""
    return {
        "id": SCHEMA_ID,
        "version": SCHEMA_VERSION,
        "records": [model.__name__ for model in RECORDS],
        "event_envelope": EVENT_ENVELOPE_ID,
        "schema": json_schema(),
    }


__all__ = [
    "JSON_SCHEMA_DIALECT",
    "type_schema",
    "field_schema",
    "model_fields",
    "model_schema",
    "json_schema",
    "record_schema",
    "describe",
]
