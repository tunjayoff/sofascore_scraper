"""
HTTP API'nin yol adları (docs/design/02-services.md bölüm 6).

  /api/v1/...   sürümlü API: zarf (`{"data": ...}`), hata modeli (`{"error": ...}`), kodla çevrilen iletiler.
                Rotaları `sofascore_scraper/web/api/v1/` altındadır.

2.x'in `/api/...` yolları (v1 dışındakiler) 3.0'da kullanımdan kalktı ve 3.1'de silindi (plan maddesi P30); öyle
bir yol artık 404 döner. Bu modül yalnızca önekleri bilir.
"""
from __future__ import annotations

API_PREFIX = "/api"
V1_PREFIX = "/api/v1"


def is_v1(path: str) -> bool:
    """Yol sürümlü API'ye mi ait (`/api/v1` ve altı)?"""
    return path == V1_PREFIX or path.startswith(V1_PREFIX + "/")


def is_api(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


__all__ = [
    "API_PREFIX",
    "V1_PREFIX",
    "is_api",
    "is_v1",
]
