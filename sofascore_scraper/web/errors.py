"""
API v1 hata modeli (docs/design/02-services.md bölüm 6 ve 2.6).

`/api/v1` altındaki her hata aynı gövdeyle döner:

    {"error": {"code": "job_running", "message": "...", "details": {...} | null, "request_id": "..."}}

  code        hata tablosundaki kod (sofascore_scraper/errors.py). İstemci metni bu koddan üretir.
  message     İngilizce, yerelleştirilmez (sunucunun dili ne olursa olsun aynıdır).
  details     koda özgü ek bilgi (kilidin sahibi, doğrulama hataları, deponun kendi iletisi ...).
  request_id  isteğin kimliği; yanıtın `X-Request-Id` başlığında da durur ve hata loglarına yazılır.

HTTP durumu da tablodan gelir (`ErrorSpec.http_status`): `invalid_request` gövde/parametre doğrulamasında
422, diğer durumlarda 400'dür.

Eski yollar (`/api/...`, v1 dışı) bu modelden etkilenmez: buradaki işleyiciler yola bakar ve v1 dışındaki
istekleri FastAPI'nin kendi işleyicisine (ya da uygulamanın eski işleyicisine) bırakır.

Rotaların fırlattığı hatalar (PlatformError, StorageError, LeaseHeld, iş deposunun çakışmaları, beklenmeyen
her şey) uygulamanın ara katmanında yakalanır ve `exception_response` ile yanıta çevrilir (sofascore_scraper/web/app.py);
çatının kendi hataları (bilinmeyen yol, doğrulama) `install` ile kurulan işleyicilerden geçer.
"""
from __future__ import annotations

import logging
import re
import secrets
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from sofascore_scraper.errors import ERRORS, INTERNAL, PlatformError, error_spec, to_platform_error
from sofascore_scraper.redact import redact_text
from sofascore_scraper.store import JobStoreConflict, LeaseHeld
from sofascore_scraper.web.api import is_v1

logger = logging.getLogger("WebAPI")

REQUEST_ID_HEADER = "X-Request-Id"
# İstemcinin gönderdiği kimlik yalnızca bu biçimdeyse kullanılır (log satırına ve yanıt başlığına gider)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

INVALID_REQUEST = "invalid_request"
STORAGE_ERROR = "storage_error"
# Deponun kendi iletisi Türkçedir (sofascore_scraper/store/errors.py); v1'in `message` alanı İngilizcedir. Hata bir işletim
# sistemi nedenini taşımıyorsa bu genel ileti kullanılır ve deponun metni `details.store_message`e gider.
STORAGE_MESSAGE = "The data directory could not be read or written."
INTERNAL_MESSAGE = "Internal error."

# HTTP durumu → kod: çatının kendi ürettiği hatalar için (bilinmeyen yol, izin verilmeyen yöntem ...).
# Burada olmayan 4xx `invalid_request`, 5xx `internal` olur; durum kodu çatınınki olarak kalır.
_STATUS_CODES: Mapping[int, str] = {401: "unauthorized", 404: "not_found", 501: "not_supported"}


class ValidationFailed(Exception):
    """
    Bir v1 rotası gövdedeki ya da sorgudaki bir değeri reddetti: 422 `invalid_request`.

    PlatformError değildir (hata tablosunun sınıf listesine girmez); taşıdığı hata `error` alanındadır.
    Aynı kodun 400'ü (istek biçimi doğru ama bu haliyle yapılamaz: kilitli ayar, bilinmeyen imleç) için
    rota doğrudan `UsageError` fırlatır.
    """

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        self.error = PlatformError(INVALID_REQUEST, message, details)
        super().__init__(message)


# --- OpenAPI modelleri ---------------------------------------------------------------------------


class ApiError(BaseModel):
    """The error object of every `/api/v1` error response."""

    code: str = Field(description="Machine-readable error code; clients translate by this code.")
    message: str = Field(description="English text for logs and developers; never localised.")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Code-specific details.")
    request_id: str = Field(description="Id of the request; also sent in the X-Request-Id header.")


class ApiErrorResponse(BaseModel):
    error: ApiError


def error_responses(*codes: str) -> Dict[Union[int, str], Dict[str, Any]]:
    """
    Bir rotanın OpenAPI `responses` alanı: verilen kodların tablodaki HTTP durumları, hata modeliyle.
    Aynı duruma düşen kodlar tek satırda birleşir (`409: job_running, data_operation_running`).
    """
    by_status: Dict[int, List[str]] = {}
    for code in codes:
        for status in ERRORS[code].http_status:
            by_status.setdefault(status, []).append(code)
    return {
        status: {"model": ApiErrorResponse, "description": ", ".join(names)}
        for status, names in sorted(by_status.items())
    }


def validation_response() -> Dict[Union[int, str], Dict[str, Any]]:
    """Yalnızca 422 (`invalid_request`, doğrulama): girdisi olan her v1 rotası verebilir."""
    return {422: {"model": ApiErrorResponse, "description": INVALID_REQUEST}}


# --- istek kimliği -------------------------------------------------------------------------------


def new_request_id() -> str:
    return secrets.token_hex(8)


def assign_request_id(request: Request) -> str:
    """
    İsteğe kimlik verir ve `request.state`e yazar. İstemci `X-Request-Id` gönderdiyse ve değer güvenli
    biçimdeyse o kullanılır (çağıranın kendi izleme kimliği); değilse yenisi üretilir.
    """
    supplied = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
    request_id = supplied if _REQUEST_ID.match(supplied) else new_request_id()
    request.state.request_id = request_id
    return request_id


def request_id_of(request: Request) -> str:
    """İsteğin kimliği; ara katman vermediyse (ör. doğrudan çağrılan bir işleyici) şimdi verilir."""
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) and request_id else assign_request_id(request)


# --- hatadan yanıta ------------------------------------------------------------------------------


def http_status(error: PlatformError, *, validation: bool = False) -> int:
    """Kodun tablodaki HTTP durumu. HTTP'de karşılığı olmayan bir kod (`cancelled`) 500'dür."""
    statuses = error_spec(error.code).http_status
    if not statuses:
        return 500
    if error.code == INVALID_REQUEST:
        return 422 if validation else 400
    return statuses[0]


def error_response(
    request: Request,
    error: PlatformError,
    *,
    validation: bool = False,
    status_code: Optional[int] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> JSONResponse:
    """`{"error": {...}}` gövdeli yanıt. `status_code` yalnızca çatının kendi durumunu korumak içindir (405)."""
    request_id = request_id_of(request)
    body = {"error": {"code": error.code, "message": error.message, "details": error.details, "request_id": request_id}}
    response = JSONResponse(
        body, status_code=status_code or http_status(error, validation=validation), headers=dict(headers or {})
    )
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def platform_error(exc: BaseException) -> PlatformError:
    """
    Herhangi bir istisnayı v1'in döndüreceği hataya çevirir.

      * iş deposunun çakışması bir kilitten geliyorsa (başka süreç) kilidin sahibi `details.holder`a girer;
      * depolama hatasının iletisi İngilizcedir: işletim sistemi nedeni varsa ondan kurulur, yoksa genel ileti
        kullanılır ve deponun metni `details.store_message`e yazılır;
      * beklenmeyen hatanın metni istemciye gitmez (yol ya da gizli değer taşıyabilir); loglanır.
    """
    if isinstance(exc, PlatformError):
        return exc
    if isinstance(exc, JobStoreConflict) and isinstance(exc.__cause__, LeaseHeld):
        return to_platform_error(exc.__cause__)
    error = to_platform_error(exc)
    if error.code == STORAGE_ERROR:
        details = dict(error.details or {})
        if not details.get("reason"):
            details["store_message"] = redact_text(error.message)
            return PlatformError(STORAGE_ERROR, STORAGE_MESSAGE, details)
        return PlatformError(STORAGE_ERROR, redact_text(error.message), details)
    if error.code == INTERNAL:
        return PlatformError(INTERNAL, INTERNAL_MESSAGE)
    return error


def exception_response(request: Request, exc: BaseException) -> JSONResponse:
    """Bir v1 rotasından çıkan istisnanın yanıtı. Beklenmeyen hata, istek kimliğiyle birlikte loglanır."""
    if isinstance(exc, ValidationFailed):
        return error_response(request, exc.error, validation=True)
    error = platform_error(exc)
    if error.code == INTERNAL and not isinstance(exc, PlatformError):
        logger.error(
            "Unhandled error in %s %s (request %s): %s: %s",
            request.method, request.scope.get("path"), request_id_of(request), type(exc).__name__,
            redact_text(str(exc)), exc_info=exc,
        )
    return error_response(request, error)


def validation_details(errors: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Doğrulama hataları: alanın yeri, ileti ve türü. Gönderilen değer geri yazılmaz: gövde bir parola
    taşıyabilir (proxy adresi).
    """
    return {
        "errors": [
            {"loc": [str(part) for part in item.get("loc", ())], "message": str(item.get("msg", "")),
             "type": str(item.get("type", ""))}
            for item in errors
        ]
    }


# --- çatının hataları ----------------------------------------------------------------------------


async def _http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if not is_v1(request.scope["path"]):
        return await http_exception_handler(request, exc)
    code = _STATUS_CODES.get(exc.status_code, INTERNAL if exc.status_code >= 500 else INVALID_REQUEST)
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return error_response(
        request, PlatformError(code, message), status_code=exc.status_code, headers=getattr(exc, "headers", None),
    )


async def _validation_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    if not is_v1(request.scope["path"]):
        return await request_validation_exception_handler(request, exc)
    error = PlatformError(INVALID_REQUEST, "The request is not valid.", validation_details(exc.errors()))
    return error_response(request, error, validation=True)


def install(app: FastAPI) -> None:
    """Çatının kendi hatalarını (HTTPException, doğrulama) v1 yollarında hata modeline çeviren işleyiciler."""
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)


__all__ = [
    "INTERNAL_MESSAGE",
    "INVALID_REQUEST",
    "REQUEST_ID_HEADER",
    "STORAGE_MESSAGE",
    "ApiError",
    "ApiErrorResponse",
    "ValidationFailed",
    "assign_request_id",
    "error_response",
    "error_responses",
    "exception_response",
    "http_status",
    "install",
    "new_request_id",
    "platform_error",
    "request_id_of",
    "validation_details",
    "validation_response",
]
