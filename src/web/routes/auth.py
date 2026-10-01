"""
İsteğe bağlı erişim belirteci (SOFASCORE_API_TOKEN) için oturum uç noktaları.

Programlar belirteci her istekte `Authorization: Bearer` ile gönderir. Web arayüzü onu bir kez
buraya gönderir ve karşılığında oturum cookie'si alır (HttpOnly: sayfadaki betik okuyamaz;
SameSite=Strict: başka bir siteden gelen istekle gönderilmez). Bu üç yol belirteç olmadan da
yanıt verir (src/web/security.py: AUTH_OPEN_PATHS); kaynak denetimi onlara da uygulanır.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.web import security
from src.web.routes.common import logger

router = APIRouter(prefix="/api", tags=["api"])


class LoginRequest(BaseModel):
    token: str = Field(max_length=4096)


def _is_https(request: Request) -> bool:
    # TLS'i sonlandıran ters vekilin arkasında uygulama düz HTTP görür; vekil bunu başlıkla bildirir
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


@router.get("/auth")
def auth_status(request: Request):
    """Belirteç gerekiyor mu ve bu çağıran onu taşıyor mu? (Arayüz giriş kutusunu buna göre gösterir.)"""
    return {
        "required": bool(security.api_token()),
        "authenticated": security.is_authenticated(request.headers, request.cookies),
    }


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    """Belirteç doğruysa oturum cookie'sini kurar. Belirtecin kendisi cookie'ye ya da log'a yazılmaz."""
    token = security.api_token()
    if not token:
        return {"required": False, "authenticated": True}
    if not security.token_matches(body.token.strip()):
        client = request.client.host if request.client else "?"
        logger.warning(f"Erişim belirteci reddedildi (istemci: {client})")
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_token", "message": "The access token is not correct."},
        )
    response.set_cookie(
        security.SESSION_COOKIE,
        security.session_value(token),
        max_age=security.SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=_is_https(request),
    )
    return {"required": True, "authenticated": True}


@router.post("/auth/logout")
def logout(response: Response):
    """Oturum cookie'sini siler (bu tarayıcı için)."""
    response.delete_cookie(security.SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    required = bool(security.api_token())
    return {"required": required, "authenticated": not required}
