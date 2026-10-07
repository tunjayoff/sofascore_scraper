"""
SofaScore'a giden etkileşimli isteklerin (lig arama, sezon yenileme, bağlantı testi) NEDEN
başarısız olduğunu web arayüzüne taşıyan ortak sözlük.

İstek katmanının tipli hataları (sofascore_scraper/exceptions.py) ve köprü sağlık durumu (sofascore_scraper/bridge_health.py)
tek bir `reason` alanına indirgenir; arayüz her nedeni çevrilmiş bir mesaja ve bir sonraki adıma
eşler (frontend/src/lib/upstream.ts). "Sonuç yok" bir neden DEĞİLDİR: o, isteğin başarılı olup
boş dönmesidir ve 200 ile yanıtlanır.

  blocked       SofaScore isteği reddetti (403; challenge çözülemedi ya da challenge'sız 403)
  browser       SofaScore challenge istedi ama gömülü tarayıcı başlatılamadı
  rate_limited  429 / 503: çok hızlı gidiyoruz ya da SofaScore meşgul
  network       bağlantı kurulamadı / zaman aşımı / proxy hatası
  not_found     SofaScore'da böyle bir kaynak yok (404)
  upstream      SofaScore yanıt verdi ama beklenmeyen bir yanıt (5xx, bozuk JSON, tanınmayan biçim)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from sofascore_scraper import bridge_health
from sofascore_scraper.exceptions import APIError, NetworkError, RateLimitError, ResourceNotFoundError

BLOCKED = "blocked"
BROWSER = "browser"
RATE_LIMITED = "rate_limited"
NETWORK = "network"
NOT_FOUND = "not_found"
UPSTREAM = "upstream"

# Yanıtın HTTP durumu: arayüz `reason`a bakar; durum kodu API'yi doğrudan kullananlar içindir
_STATUS = {
    BLOCKED: 502,
    BROWSER: 502,
    RATE_LIMITED: 503,
    NETWORK: 502,
    NOT_FOUND: 404,
    UPSTREAM: 502,
}

# API'yi doğrudan kullananlar için kısa İngilizce açıklama (arayüz kendi çevirisini gösterir)
_MESSAGE = {
    BLOCKED: "SofaScore refused the request (HTTP 403).",
    BROWSER: "SofaScore asked for a browser check and the built-in browser could not start.",
    RATE_LIMITED: "SofaScore is rate limiting requests.",
    NETWORK: "SofaScore could not be reached.",
    NOT_FOUND: "SofaScore has no such resource.",
    UPSTREAM: "SofaScore returned an unexpected response.",
}


def browser_failed_since(before: Optional[Dict[str, Any]]) -> bool:
    """
    `before` (isteğe başlamadan alınan bridge_health.snapshot()) sonrasında köprü bir tarayıcı
    başlatma hatası kaydetti mi? Öyleyse 403'ün nedeni "SofaScore engelliyor" değil, challenge'ı
    çözecek tarayıcının açılamamasıdır.
    """
    now = bridge_health.snapshot()
    err = now.get("last_error") or {}
    if err.get("kind") != bridge_health.KIND_BROWSER:
        return False
    if before is None:
        return now.get("consecutive_failures", 0) > 0
    return (
        now.get("consecutive_failures", 0) > before.get("consecutive_failures", 0)
        or now.get("last_failure_at") != before.get("last_failure_at")
    )


def reason_for(exc: BaseException, before: Optional[Dict[str, Any]] = None) -> str:
    """İstek katmanı hatasını tek bir nedene indirger. `before`: istek öncesi köprü görüntüsü."""
    if isinstance(exc, ResourceNotFoundError):
        return NOT_FOUND
    if isinstance(exc, RateLimitError):
        return RATE_LIMITED
    if isinstance(exc, APIError):
        if exc.status_code == 403:
            return BROWSER if browser_failed_since(before) else BLOCKED
        return UPSTREAM
    if isinstance(exc, NetworkError):
        return NETWORK
    return UPSTREAM


def detail(reason: str) -> Dict[str, str]:
    return {"reason": reason, "message": _MESSAGE[reason]}


def http_error(reason: str) -> HTTPException:
    """Tipli hata yanıtı: {"detail": {"reason": ..., "message": ...}}."""
    return HTTPException(status_code=_STATUS[reason], detail=detail(reason))
