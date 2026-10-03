"""
API v1: `/api/v1` altındaki rotalar (docs/design/02-services.md bölüm 6).

Kurallar (her rota için):

  * yanıt modeli bildirilir; belge `docs/api/openapi-v1.json`'da durur ve bir test onu `app.openapi()` ile
    karşılaştırır (`python -m src.web.openapi --write` yeniden üretir);
  * tek kaynak `{"data": {...}}`, liste `{"data": [...], "page": {"limit", "next_cursor"}}` zarfıyla döner;
  * hatalar `{"error": {"code", "message", "details", "request_id"}}` biçimindedir (src/web/errors.py);
    rota bir PlatformError (ya da `ValidationFailed`) fırlatır, yanıta uygulamanın ara katmanı çevirir;
  * metinler İngilizcedir ve yerelleştirilmez: istemci `code` ile çevirir;
  * durum değiştiren istek GET olamaz.

Bu paketteki rotalar: sağlık, durum, sporlar ve sink'ler (meta.py), oturum (auth.py), takipler (follows.py),
turnuvalar, sezonlar ve turnuva araması (tournaments.py), maçlar, dilimleri, ham yükler ve değişiklikler (events.py), işler (jobs.py), ayarlar
(settings.py). Şema v1 kayıtlarının yanıt modelleri records.py'dedir. Canlı veri için rota yoktur.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.web.api import V1_PREFIX
from src.web.errors import error_responses, validation_response


class PageInfo(BaseModel):
    """Cursor pagination of a collection response."""

    limit: int = Field(description="Maximum number of items in this page.")
    next_cursor: Optional[str] = Field(
        default=None, description="Pass as `cursor` to get the next page; null on the last page.",
    )


# Rota modülleri PageInfo'yu buradan alır: içe aktarma, model tanımlandıktan sonra yapılır
from src.web.api.v1 import auth, events, follows, jobs, meta, settings, tournaments  # noqa: E402

# Her rotanın verebildiği hatalar: belirteç (401), doğrulama (422; FastAPI'nin kendi 422 modelinin yerine) ve
# beklenmeyen hata (500). Rotalar kendi kodlarını ekler.
router = APIRouter(
    prefix=V1_PREFIX,
    responses={**error_responses("unauthorized", "internal"), **validation_response()},
)
for _module in (meta, auth, follows, tournaments, events, jobs, settings):
    router.include_router(_module.router)

__all__ = ["PageInfo", "router"]
