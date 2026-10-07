"""
API v1'in dosya indirmeleri: dışa aktarma ve yedek dosyası (docs/design/05-web-ui.md 6.10 ve 6.11; plan maddesi P21).

Dosya veri dizinindedir ve yolunu Store verir (`backups/`) ya da iş kimliğinden kurulur (`exports/`); web katmanı
dosya sistemine dokunmaz (Store sınırı, docs/design/01-storage.md 2.4). Dosya, yanıt gönderilirken açılır
(Starlette FileResponse, işçi thread'inde). O ana kadar silinmişse (elle, ya da bir temizleme) yanıt v1 hata
modeliyle 404 `not_found` olur; Starlette'in kendi hatası (düz metin 500) yerine.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi.responses import FileResponse
from starlette.types import Receive, Scope, Send

from sofascore_scraper.errors import PlatformError
from sofascore_scraper.web import errors


class Download(FileResponse):
    """İndirilecek dosya; gönderilirken bulunamazsa v1 hata modeliyle 404."""

    def __init__(self, path: str, *, filename: str, media_type: str, details: Dict[str, Any]) -> None:
        super().__init__(path, filename=filename, media_type=media_type, headers={"Cache-Control": "no-store"})
        self._details = details

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except RuntimeError:
            # Starlette: "File at path ... does not exist." (henüz hiçbir şey gönderilmeden)
            from starlette.requests import Request

            response = errors.error_response(
                Request(scope), PlatformError("not_found", "The file is no longer there.", self._details),
            )
            await response(scope, receive, send)


def download_responses(media_type: str, description: str) -> Dict[Any, Dict[str, Any]]:
    """Bir indirme rotasının OpenAPI 200 yanıtı: zarfsız dosya, verilen türde."""
    return {
        200: {
            "description": description,
            "content": {media_type: {"schema": {"type": "string", "format": "binary"}}},
        },
    }


__all__ = ["Download", "download_responses"]
