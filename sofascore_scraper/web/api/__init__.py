"""
HTTP API'nin yol adları (docs/design/02-services.md bölüm 6).

  /api/v1/...   sürümlü API: zarf (`{"data": ...}`), hata modeli (`{"error": ...}`), kodla çevrilen iletiler.
                Rotaları `sofascore_scraper/web/api/v1/` altındadır.

2.x'in `/api/...` yolları (v1 dışındakiler) 3.0'da kullanımdan kalktı ve 3.1'de silindi (plan maddesi P30); öyle
bir yol artık 404 döner. Bu modül yalnızca önekleri bilir.

Kök yol (B2, ters vekil denetimi). Uygulama bir kök yolla çalıştırıldığında (uvicorn `--root-path /sofa`; ASGI
`root_path`) isteğin `path`'i kökü de içerir ("/sofa/api/v1/jobs") ve yönlendirici kökü atıp eşleştirir. Güvenlik
katmanı ve hata işleyicileri yolu `path`ten okuyordu: kök yolla "/sofa/api/v1/..." `/api` ile başlamadığı için
erişim belirteci istenmiyor, rota yine de çalışıyordu. Hepsi artık yönlendiricinin eşleştirdiği yolu okur
(`route_path`, Starlette'in `get_route_path` kuralı).
"""
from __future__ import annotations

from typing import Any, Mapping

API_PREFIX = "/api"
V1_PREFIX = "/api/v1"


def route_path(scope: Mapping[str, Any]) -> str:
    """
    Yönlendiricinin eşleştirdiği yol: `path`, kök yolla (`root_path`) başlıyorsa kökü atılmış hali (Starlette'in
    `get_route_path`ıyla aynı kural: kök yalnızca tam bir yol parçasıysa atılır).
    """
    path = str(scope.get("path") or "")
    root = str(scope.get("root_path") or "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root):] if path[len(root)] == "/" else path


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
    "route_path",
]
