"""
Proxy adresinin parolası (PR #43; tests/test_settings_proxy.py): API'den asla tam dönmez.

  * `mask_proxy_url`: `http://user:secret@host:8080` → `http://user:***@host:8080` (parola yoksa aynen);
  * `restore_proxy_password`: form maskeli adresi geri gönderdiyse saklanan parolayı yerine koyar, ama yalnızca
    şema, sunucu, port ve kullanıcı aynıysa; biri değiştiyse parolanın yeniden yazılması istenir (422,
    `proxy_password_required`). Şema da sayılır: https'ten http'ye geçmek parolayı ağda açık taşırdı; başka bir
    port başka bir dinleyicidir.

Eski `GET/POST /api/settings` ve `GET/PATCH /api/v1/settings` aynı kuralı kullanır (eskiden
src/web/routes/settings.py'deydi).
"""
from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import urlparse

from fastapi import HTTPException

PROXY_PASSWORD_MASK = "***"
PASSWORD_REQUIRED = "proxy_password_required"


def _split_proxy_userinfo(url: str) -> Optional[Tuple[str, str, str, str]]:
    """
    "scheme://user:pass@host:port/x" → ("scheme://", "user", "pass", "@host:port/x"); parola yoksa None.
    urlparse'a güvenmez: şemasız yazılmış ("user:pass@host:8080") bir değer de maskelenmeli.
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        scheme, rest = "", url
    cut = min((i for i in (rest.find(c) for c in "/?#") if i >= 0), default=len(rest))
    userinfo, at, hostport = rest[:cut].rpartition("@")
    user, colon, password = userinfo.partition(":")
    if not at or not colon or not password:
        return None
    return scheme + sep, user, password, at + hostport + rest[cut:]


def mask_proxy_url(url: str) -> str:
    """http://user:secret@host:8080 → http://user:***@host:8080 (parola yoksa değer aynen döner)."""
    parts = _split_proxy_userinfo(url or "")
    if parts is None:
        return url or ""
    head, user, _password, tail = parts
    return f"{head}{user}:{PROXY_PASSWORD_MASK}{tail}"


def _proxy_endpoint(url: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """
    Parolanın gönderildiği uç: (şema, sunucu, port). Şema ve sunucu küçük harfe çevrilir; yazılmamış port
    None'dır ve yazılmış hiçbir porta eşit sayılmaz (varsayılan port istemciye göre değişir). Sunucusu
    okunamayan ya da portu geçersiz bir adres için None.
    """
    try:
        parsed = urlparse(url)
        scheme, hostname, port = parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port
    except ValueError:  # .env'e elle yazılmış bozuk bir değer, ya da sayı olmayan bir port
        return None
    if not hostname:
        return None
    return scheme, hostname, port


def restore_proxy_password(submitted: str, stored: str) -> str:
    """
    Form maskeli adresi geri gönderdiyse (parola = yer tutucu) saklanan parolayı yerine koyar. Kullanıcı adı,
    şema, sunucu ya da port değiştiyse saklanan parola yeni adrese gönderilmez: HTTPException 422,
    `{"reason": "proxy_password_required", "message": ...}`.
    """
    new = _split_proxy_userinfo(submitted)
    if new is None or new[2] != PROXY_PASSWORD_MASK:
        return submitted

    old = _split_proxy_userinfo(stored)
    stored_endpoint = _proxy_endpoint(stored)
    same_target = (
        old is not None
        and old[1] == new[1]
        and stored_endpoint is not None
        and stored_endpoint == _proxy_endpoint(submitted)
    )
    if not same_target:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": PASSWORD_REQUIRED,
                "message": "Re-enter the proxy password: the proxy address or user changed.",
            },
        )
    head, user, _mask, tail = new
    return f"{head}{user}:{old[2]}{tail}"


# Eski ad (src/web/routes/settings.py `_restore_proxy_password`)
_restore_proxy_password = restore_proxy_password

__all__ = ["PASSWORD_REQUIRED", "PROXY_PASSWORD_MASK", "mask_proxy_url", "restore_proxy_password"]
