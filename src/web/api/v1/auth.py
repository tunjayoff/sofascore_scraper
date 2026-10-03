"""
API v1: oturum (docs/design/02-services.md bölüm 6 ve 6.1; plan maddesi P21).

    GET  /api/v1/auth           belirteç gerekiyor mu, bu çağıran onu taşıyor mu
    POST /api/v1/auth/login     {"token": "..."}: belirteç doğruysa oturum cookie'sini kurar
    POST /api/v1/auth/logout    oturum cookie'sini siler

Eski `/api/auth*` yollarının halefleridir; aynı cookie'yi kurar ve aynı kuralları uygular. Üçü de belirteç
olmadan yanıt verir (`security.AUTH_OPEN_PATHS`); kaynak denetimi ve Host denetimi onlara da uygulanır.

Programlar belirteci her istekte `Authorization: Bearer` ile gönderir. Web arayüzü onu bir kez buraya gönderir ve
karşılığında oturum cookie'si alır (HttpOnly: sayfadaki betik okuyamaz; SameSite=Strict: başka bir siteden gelen
istekle gönderilmez). Belirtecin kendisi cookie'ye ya da log'a yazılmaz.

Başarısız giriş sınırı (`deps.attempt_limiter`) eski giriş yoluyla ve yanlış `Authorization: Bearer`
başlıklarıyla ortaktır. Kilit sürerken gelen giriş değerlendirilmez: 401 `unauthorized`,
`details.reason = "too_many_attempts"` ve `Retry-After` (v1'in kilit yanıtı, bölüm 6). Yanlış belirteç 401
`unauthorized`, `details.reason = "invalid_token"`.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from src.errors import PlatformError
from src.web import deps, errors, security
from src.web.errors import error_responses

logger = logging.getLogger("WebAPI")
router = APIRouter(tags=["auth"])

INVALID_TOKEN = "invalid_token"
TOO_MANY_ATTEMPTS = "too_many_attempts"


class AuthState(BaseModel):
    """Whether the server asks for an access token, and whether this caller presents a valid one."""

    required: bool = Field(description="An access token is configured.")
    authenticated: bool = Field(description="This request carries the token or a valid session cookie.")


class AuthResponse(BaseModel):
    data: AuthState


class AuthLogin(BaseModel):
    """The access token, sent once by the web UI in exchange for a session cookie."""

    token: str = Field(max_length=4096)


# --- eski yollarla ortak kurallar ----------------------------------------------------------------


def is_https(request: Request) -> bool:
    """TLS'i sonlandıran ters vekilin arkasında uygulama düz HTTP görür; vekil bunu başlıkla bildirir."""
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def state_of(request: Request) -> AuthState:
    return AuthState(
        required=bool(security.api_token()),
        authenticated=security.is_authenticated(request.headers, request.cookies),
    )


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    """Oturum cookie'si: değeri belirteçten türetilir (security.session_value), belirtecin kendisi değildir."""
    response.set_cookie(
        security.SESSION_COOKIE,
        security.session_value(token),
        max_age=security.SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=is_https(request),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(security.SESSION_COOKIE, path="/", httponly=True, samesite="strict")


def after_logout() -> AuthState:
    required = bool(security.api_token())
    return AuthState(required=required, authenticated=not required)


# --- rotalar -------------------------------------------------------------------------------------


@router.get("/auth", response_model=AuthResponse, operation_id="getAuth", summary="Session state")
def auth_status(request: Request) -> AuthResponse:
    """Whether an access token is required and whether this caller presents it. Answers without a token."""
    return AuthResponse(data=state_of(request))


@router.post(
    "/auth/login",
    response_model=AuthResponse,
    operation_id="login",
    summary="Sign in with the access token",
    responses=error_responses("forbidden_origin"),
)
def login(body: AuthLogin, request: Request, response: Response) -> Any:
    """
    Exchange the access token for a session cookie (HttpOnly, SameSite=Strict). Without a configured token the
    call does nothing and reports `authenticated`. A wrong token is 401 `unauthorized` with
    `details.reason = "invalid_token"`; after repeated wrong tokens from one address the attempts are refused
    for a while (401 with `details.reason = "too_many_attempts"` and `Retry-After`), the right token included.
    """
    token = security.api_token()
    if not token:
        return AuthResponse(data=AuthState(required=False, authenticated=True))
    client = deps.client_key(request.client)
    limiter = deps.attempt_limiter
    wait = limiter.retry_after(client)
    if wait > 0:
        seconds = max(1, math.ceil(wait))
        return errors.error_response(
            request,
            PlatformError("unauthorized", f"Too many failed attempts. Try again in {seconds} seconds.",
                          {"reason": TOO_MANY_ATTEMPTS, "retry_after": seconds}),
            headers={"Retry-After": str(seconds), "WWW-Authenticate": "Bearer"},
        )
    if not security.token_matches(body.token.strip()):
        logger.warning("Access token refused (client: %s)", client)
        lock = limiter.failure(client)
        if lock > 0:
            logger.warning(
                "Too many failed access-token attempts from %s; new attempts are refused for %d s", client, lock,
            )
        return errors.error_response(
            request,
            PlatformError("unauthorized", "The access token is not correct.", {"reason": INVALID_TOKEN}),
            headers={"WWW-Authenticate": "Bearer"},
        )
    limiter.success(client)
    set_session_cookie(response, request, token)
    return AuthResponse(data=AuthState(required=True, authenticated=True))


@router.post(
    "/auth/logout",
    response_model=AuthResponse,
    operation_id="logout",
    summary="Sign out",
    responses=error_responses("forbidden_origin"),
)
def logout(response: Response) -> AuthResponse:
    """Delete the session cookie of this browser."""
    clear_session_cookie(response)
    return AuthResponse(data=after_logout())


__all__ = [
    "AuthResponse",
    "AuthState",
    "after_logout",
    "clear_session_cookie",
    "is_https",
    "router",
    "set_session_cookie",
    "state_of",
]
