"""
HTTP API'nin sürümleri (docs/design/02-services.md bölüm 6).

  /api/v1/...   sürümlü API: zarf (`{"data": ...}`), hata modeli (`{"error": ...}`), kodla çevrilen iletiler.
                Rotaları `sofascore_scraper/web/api/v1/` altındadır.
  /api/...      eski yollar (`sofascore_scraper/web/api/legacy.py`): bir sürüm daha aynı yanıt biçimleriyle durur, OpenAPI
                belgesinde `deprecated` işaretlidir ve her yanıtı `Deprecation: true` ile `Link:
                <halef>; rel="successor-version"` başlıklarını taşır (bölüm 6.1).

Bu modül yalnızca yol adlarını bilir: önekler ve eski her yolun v1'deki halefi. Halef tablosu bölüm 6.1'deki
tablonun aynısıdır ve her halef vardır (P21).

`Deprecation` başlığı taslağın biçimindedir (`true`), RFC 9745'in tarihi (`@<unix zamanı>`) değil: kullanımdan
kaldırma bir tarihe değil 3.0.0 sürümüne bağlıdır ve o tarih henüz belli değil (karar P21; plan bölüm 14).
"""
from __future__ import annotations

import re
from typing import List, Mapping, Optional, Pattern, Tuple
from urllib.parse import quote

API_PREFIX = "/api"
V1_PREFIX = "/api/v1"

DEPRECATION_HEADER = "Deprecation"
LINK_HEADER = "Link"
SUCCESSOR_REL = "successor-version"

# Eski yol → v1'deki halefi (V1_PREFIX'e göre): (yöntem, yol şablonu, halef). Yöntem "*" ise o yolun her
# yöntemi aynı halefe gider. Şablondaki `{ad}` parçaları halefte aynı adla kullanılabilir. Halefi bir iş olan
# yollar (indirme, yedek, silme, dışa aktarma) `/jobs`'a gider.
ANY_METHOD = "*"
LEGACY_SUCCESSORS: Tuple[Tuple[str, str, str], ...] = (
    (ANY_METHOD, "/api/leagues", "/follows"),
    (ANY_METHOD, "/api/leagues/search", "/follows"),
    (ANY_METHOD, "/api/leagues/search-remote", "/tournaments/search"),
    (ANY_METHOD, "/api/leagues/{league_id}", "/follows"),
    (ANY_METHOD, "/api/leagues/{league_id}/seasons", "/tournaments/{league_id}/seasons"),
    (ANY_METHOD, "/api/leagues/{league_id}/seasons/refresh", "/jobs"),
    (ANY_METHOD, "/api/leagues/{league_id}/missing-details", "/events"),
    (ANY_METHOD, "/api/seasons/{season_id}/matches", "/events"),
    (ANY_METHOD, "/api/matches", "/events"),
    (ANY_METHOD, "/api/matches/{match_id}", "/events/{match_id}"),
    (ANY_METHOD, "/api/matches/{match_id}/fetch", "/jobs"),
    (ANY_METHOD, "/api/fetch", "/jobs"),
    (ANY_METHOD, "/api/scrape/status", "/jobs"),
    (ANY_METHOD, "/api/scrape/stream", "/jobs"),
    (ANY_METHOD, "/api/scrape/cancel", "/jobs"),
    (ANY_METHOD, "/api/jobs", "/jobs"),
    (ANY_METHOD, "/api/jobs/{job_id}", "/jobs/{job_id}"),
    (ANY_METHOD, "/api/status", "/status"),
    (ANY_METHOD, "/api/bypass/status", "/status"),
    (ANY_METHOD, "/api/bypass/test", "/status/check"),
    (ANY_METHOD, "/api/settings", "/settings"),
    (ANY_METHOD, "/api/dashboard", "/status"),
    (ANY_METHOD, "/api/stats/system", "/status"),
    (ANY_METHOD, "/api/data/backup", "/jobs"),
    (ANY_METHOD, "/api/data/backups/{name}", "/backups/{name}"),
    (ANY_METHOD, "/api/data/clear", "/jobs"),
    # Dışa aktarma: üretmek bir iştir (POST), üretilmişi indirmek bir kaynaktır (GET)
    ("POST", "/api/export/csv", "/jobs"),
    (ANY_METHOD, "/api/export/csv", "/exports"),
    (ANY_METHOD, "/api/sports", "/sports"),
    (ANY_METHOD, "/api/logs", "/logs"),
    (ANY_METHOD, "/api/diagnostics", "/diagnostics"),
    (ANY_METHOD, "/api/diagnostics/bundle", "/diagnostics/bundle"),
    (ANY_METHOD, "/api/auth", "/auth"),
    (ANY_METHOD, "/api/auth/login", "/auth/login"),
    (ANY_METHOD, "/api/auth/logout", "/auth/logout"),
)

_PARAMETER = re.compile(r"\{([a-z_]+)\}")


def _compile(template: str) -> Pattern[str]:
    """`/api/jobs/{job_id}` → o yolla tam eşleşen, parametreleri adlandırılmış gruplara alan düzenli ifade."""
    parts = _PARAMETER.split(template)
    pattern = "".join(re.escape(part) if i % 2 == 0 else f"(?P<{part}>[^/]+)" for i, part in enumerate(parts))
    return re.compile(pattern)


# Parametresiz yollar önce denenir (ör. `/api/leagues/search`, `/api/leagues/{league_id}`den önce); aynı
# türden kurallar tablodaki sırayla (yönteme özel kural, o yolun genel kuralından önce yazılır)
_RULES: Tuple[Tuple[str, Pattern[str], str], ...] = tuple(
    (method, _compile(template), successor)
    for method, template, successor in sorted(LEGACY_SUCCESSORS, key=lambda rule: "{" in rule[1])
)


def is_v1(path: str) -> bool:
    """Yol sürümlü API'ye mi ait (`/api/v1` ve altı)?"""
    return path == V1_PREFIX or path.startswith(V1_PREFIX + "/")


def is_api(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def successor_of(path: str, method: str = "GET") -> Optional[str]:
    """
    Eski bir yolun v1'deki halefi (tam yol, ör. `/api/v1/jobs/abc`); yol eski bir rota değilse None.

    Yol parametreleri halefe yüzde kodlanarak yazılır: yol çözülmüş (decode edilmiş) gelir ve değeri bir
    yanıt başlığına gider.
    """
    if is_v1(path) or not is_api(path):
        return None
    wanted = method.upper()
    for rule_method, pattern, target in _RULES:
        if rule_method not in (ANY_METHOD, wanted):
            continue
        match = pattern.fullmatch(path)
        if match is not None:
            return V1_PREFIX + _filled(target, match.groupdict())
    return None


def _filled(target: str, parameters: Mapping[str, str]) -> str:
    """Halef şablonundaki `{ad}` parçalarını yol parametreleriyle (yüzde kodlanmış) doldurur."""
    values = {name: quote(value, safe="") for name, value in parameters.items()}
    return _PARAMETER.sub(lambda m: values.get(m.group(1), m.group(0)), target)


def deprecation_headers(path: str, method: str = "GET") -> List[Tuple[str, str]]:
    """Eski bir rotanın yanıtına eklenen başlıklar; diğer yollar için boş."""
    successor = successor_of(path, method)
    if successor is None:
        return []
    return [(DEPRECATION_HEADER, "true"), (LINK_HEADER, f'<{successor}>; rel="{SUCCESSOR_REL}"')]


__all__ = [
    "ANY_METHOD",
    "API_PREFIX",
    "DEPRECATION_HEADER",
    "LEGACY_SUCCESSORS",
    "LINK_HEADER",
    "SUCCESSOR_REL",
    "V1_PREFIX",
    "deprecation_headers",
    "is_api",
    "is_v1",
    "successor_of",
]
