"""
Şema v1 kayıtlarının (sofascore_scraper/schema) API v1'deki yanıt modelleri (docs/design/04-schema-v1.md; plan maddesi P21).

Kayıtların sözleşmesi `sofascore_scraper/schema/models.py`'deki dataclass'lardır. FastAPI onlardan doğrudan yanıt modeli
kuramaz: `Status.class_` alanının JSON adı `class`tır (Python anahtar sözcüğü) ve dataclass'tan kurulan model onu
yanlış adlandırırdı (SC-1'in notu). Burada her dataclass'ın pydantic aynası alanlarından kurulur: aynı alanlar,
aynı sıra, aynı tipler, JSON adı (`alias`) ve açıklama alanın `metadata`sından. Ayna elle yazılmadığı için modelle
ayrışamaz; `tests/test_api_v1_resources.py` her kaydın JSON Schema'sını bu aynanınkiyle karşılaştırır.

Rotalar kaydı `to_dict()` ile sözlüğe çevirip döndürür; FastAPI onu bu modellerle doğrular ve `class` adıyla
yazar. Model adları şemadakilerdir; yalnızca API'nin kendi modelleriyle çakışan `Status` burada `EventStatus`
adını alır (`/status` yanıtının modeli `Status`tır).
"""
from __future__ import annotations

import dataclasses
import typing
from typing import Any, Dict, List, Literal, Mapping, Tuple, Type, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from sofascore_scraper.schema import models as schema_models

# API'nin kendi modelleriyle çakışan adlar (OpenAPI belgesinde tek bir bileşen adı alanı vardır)
RENAMED: Mapping[str, str] = {"Status": "EventStatus"}

_cache: Dict[type, Type[BaseModel]] = {}


def _translate(tp: Any) -> Any:
    """Dataclass alanının tipi → pydantic alanının tipi (iç içe kayıtlar aynalarıyla)."""
    if isinstance(tp, type) and dataclasses.is_dataclass(tp) and issubclass(tp, schema_models.Model):
        return mirror(tp)
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is Union:
        return Union[tuple(_translate(arg) for arg in args)]  # type: ignore[return-value]
    if origin in (tuple, Tuple):
        if len(args) == 2 and args[1] is Ellipsis:
            return List[_translate(args[0])]  # type: ignore[misc]
        raise TypeError(f"unsupported tuple type in the schema: {tp!r}")
    if origin in (dict, Mapping, typing.Mapping) or tp in (dict, Mapping):
        return Dict[str, Any]
    if origin is Literal or tp is Any or tp is type(None) or tp in (int, float, str, bool):
        return tp
    raise TypeError(f"unsupported type in the schema: {tp!r}")


def mirror(record: type) -> Type[BaseModel]:
    """Bir şema dataclass'ının pydantic aynası (önbellekte; iç içe kayıtlar da aynalanır)."""
    found = _cache.get(record)
    if found is not None:
        return found
    hints = typing.get_type_hints(record, globalns=vars(schema_models))
    fields: Dict[str, Any] = {}
    for item in dataclasses.fields(record):
        meta = item.metadata
        name = schema_models.json_name(item)
        alias = name if name != item.name else None
        fields[item.name] = (
            _translate(hints[item.name]),
            Field(..., alias=alias, description=str(meta.get("doc") or "") or None),
        )
    model = create_model(  # type: ignore[call-overload]
        RENAMED.get(record.__name__, record.__name__),
        __config__=ConfigDict(populate_by_name=True),
        __doc__=str(getattr(record, "SUMMARY", "") or "") or None,
        __module__=__name__,
        **fields,
    )
    _cache[record] = model
    return model


# Yanıtlarda kullanılan kayıtlar
Tournament = mirror(schema_models.Tournament)
Category = mirror(schema_models.Category)
Season = mirror(schema_models.Season)
Event = mirror(schema_models.Event)
Slice = mirror(schema_models.Slice)
Change = mirror(schema_models.Change)
Participant = mirror(schema_models.Participant)


def as_json(record: schema_models.Model) -> Dict[str, Any]:
    """Kaydın JSON hali (`to_dict`): yanıt modeli onu doğrular ve aynı adlarla yazar."""
    return record.to_dict()


__all__ = ["Category", "Change", "Event", "Participant", "RENAMED", "Season", "Slice", "Tournament", "as_json", "mirror"]
