"""Talimat 05: gözlenen URL → pattern (explore_all_sports.py ve analyze_all_sports.py ortak)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Sitenin spor menüsündeki slug'lar (research/all_sports/pages.jsonl, ana sayfa bağlantıları)
SPORT_SLUGS = (
    "football", "basketball", "volleyball", "tennis", "motorsport", "mma", "american-football", "handball",
    "esports", "darts", "ice-hockey", "table-tennis", "baseball", "rugby", "badminton", "cricket", "futsal",
    "cycling", "snooker", "waterpolo", "aussie-rules", "beach-volley", "minifootball", "floorball", "bandy",
    "padel",
)
SOFA_HOST = re.compile(r"(^|\.)sofascore\.(com|app|net|io)$")
_SPORT_SEG = re.compile(r"/(" + "|".join(re.escape(s) for s in sorted(SPORT_SLUGS, key=len, reverse=True)) + r")(?=/|$)")


def api_pattern(url: str) -> str:
    """
    www.sofascore.com/api/v1/event/123/statistics → /event/{id}/statistics. Tarih → {date}, yıl-ay → {month}, sayı → {id},
    spor slug'ı → {sport}, iki büyük harfli ülke kodu → {cc}, maçın customId'si → {customId}. Başka SofaScore alan adları ve /api/v1 dışı
    yollar alan adıyla; üçüncü taraf yalnızca alan adı + ilk yol parçasıyla yazılır.
    """
    u = urlparse(url)
    host = u.hostname or ""
    if not SOFA_HOST.search(host):
        return f"{host}/{u.path.strip('/').split('/')[0]}"
    path = re.sub(r"^/_next/data/[^/]+/", "/_next/data/{build}/", u.path)
    path = re.sub(r"/\d{4}-\d{2}-\d{2}", "/{date}", path)
    path = re.sub(r"/\d{4}-\d{2}(?=/|$)", "/{month}", path)
    path = re.sub(r"/-?\d+(?=/|$)", "/{id}", path)
    path = re.sub(r"/[A-Z]{2}(?=/|$)", "/{cc}", path)
    # Maçın customId'si (ör. /event/WUbsuWb/h2h/events): 5–12 harf, büyük ve küçük karışık
    path = re.sub(r"/(?=[A-Za-z]*[A-Z])(?=[A-Za-z]*[a-z])[A-Za-z]{5,12}(?=/|$)", "/{customId}", path)
    path = _SPORT_SEG.sub("/{sport}", path)
    if host == "www.sofascore.com" and re.match(r"^/api/v1(/|$)", path):
        return path[len("/api/v1"):] or "/"
    return f"{host}{path}"
