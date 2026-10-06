"""
SofaScore API'sinin uç noktaları: her URL şablonu tek yerde (docs/design/02-services.md 2.4).

Buradaki her işlev API köküne göre bir YOL döndürür ("/event/123"); kök adres (`API_BASE_URL`) yola yalnızca
istek katmanında eklenir (src/client/transport.py: base_url / _full_url). Saf modül: istek atmaz, ayar okumaz.

Maç detay dilimlerinin yolları (istatistik, kadro, ...) src/sports.py'deki dilim tablosundadır; `event_slice`
onu kullanır, böylece dilim yolları iki yerde tutulmaz.
"""
from __future__ import annotations

import re
from typing import Optional, Union
from urllib.parse import quote

from src.sports import SliceSpec, get_slice

# Ayar verilmediğinde (ya da boş verildiğinde) kullanılan API kökü
DEFAULT_BASE_URL = "https://www.sofascore.com/api/v1"

Id = Union[int, str]

# --- şablonlar ---------------------------------------------------------------------------------------

EVENT = "/event/{event_id}"
LIVE_EVENTS = "/sport/{sport}/events/live"
SEASONS = "/unique-tournament/{tournament_id}/seasons"
ROUNDS = "/unique-tournament/{tournament_id}/season/{season_id}/rounds"
ROUND_EVENTS = "/unique-tournament/{tournament_id}/season/{season_id}/events/round/{round_number}"
ROUND_EVENTS_SLUG = ROUND_EVENTS + "/slug/{slug}"
SEASON_EVENTS_PAGE = "/unique-tournament/{tournament_id}/season/{season_id}/events/{kind}/{page}"
SEARCH_UNIQUE_TOURNAMENTS = "/search/unique-tournaments/{query}"
SEARCH_ALL = "/search/all?q={query}&page={page}"

# Alt anahtar yolun bir parçasıdır: Store'un alt anahtar biçimi (küçük harf, rakam, _ . -)
_SUB = re.compile(r"^[a-z0-9_.-]{1,80}$")

# events/{kind}/{page}: oynanmış maçlar sondan başa, gelecek maçlar baştan sona sayfalanır
SEASON_EVENT_KINDS = ("last", "next")


# --- yollar ------------------------------------------------------------------------------------------

def event(event_id: Id) -> str:
    """Maçın kendisi (durum, skor, takımlar)."""
    return EVENT.format(event_id=event_id)


def event_slice(key: str, event_id: Id, sub: str = "") -> str:
    """
    Maçın bir detay dilimi; `key` src/sports.py'deki dilim anahtarıdır ("statistics", "lineups", "odds_all", ...).
    sub: alt anahtarı olan dilimde (bahis oranları: sağlayıcı kimliği) zorunludur, olmayanda boş kalır.
    """
    spec = get_slice(key)
    if spec is None or spec.owner != "event":
        raise KeyError(f"unknown event slice: {key!r}")
    return _slice_path(spec, sub, event_id=event_id)


def owner_slice(key: str, sub: str = "", **ids: Id) -> str:
    """
    Maç dışı bir varlığın dilimi (plan maddesi P28): sezon (`tournament_id`, `season_id`), takım (`team_id`),
    oyuncu (`player_id`) ya da spor. `key` src/sports.py'deki dilim anahtarıdır ("standings", "team_rankings", ...).
    Bilinmeyen anahtar ya da eksik kimlik KeyError.
    """
    spec = get_slice(key)
    if spec is None or spec.owner == "event":
        raise KeyError(f"unknown owner slice: {key!r}")
    return _slice_path(spec, sub, **ids)


def _slice_path(spec: SliceSpec, sub: str, **ids: Id) -> str:
    if (spec.subs is None) != (sub == ""):
        raise ValueError(f"slice {spec.key!r}: " + ("expects no sub-key" if spec.subs is None else "needs a sub-key"))
    if sub and not _SUB.match(sub):
        raise ValueError(f"slice {spec.key!r}: invalid sub-key {sub!r}")
    return spec.format_path(sub, **ids)


def live_events(sport: str) -> str:
    """Bir sporun şu an oynanan maçları."""
    return LIVE_EVENTS.format(sport=sport)


def seasons(tournament_id: Id) -> str:
    """Bir ligin sezon listesi."""
    return SEASONS.format(tournament_id=tournament_id)


def rounds(tournament_id: Id, season_id: Id) -> str:
    """Bir sezonun tur listesi."""
    return ROUNDS.format(tournament_id=tournament_id, season_id=season_id)


def round_events(tournament_id: Id, season_id: Id, round_number: Id, slug: Optional[str] = None) -> str:
    """Bir turun maçları; kupa turlarında `slug` eklenir."""
    template = ROUND_EVENTS_SLUG if slug else ROUND_EVENTS
    return template.format(tournament_id=tournament_id, season_id=season_id, round_number=round_number, slug=slug)


def season_events_page(tournament_id: Id, season_id: Id, kind: str, page: int) -> str:
    """Turları olmayan sezonların maç sayfaları; `kind`: "last" ya da "next"."""
    if kind not in SEASON_EVENT_KINDS:
        raise ValueError(f"kind must be one of {SEASON_EVENT_KINDS}: {kind!r}")
    return SEASON_EVENTS_PAGE.format(tournament_id=tournament_id, season_id=season_id, kind=kind, page=page)


def search_unique_tournaments(query: str) -> str:
    """Ada göre lig araması; sorgu yolun parçasıdır ve tümüyle kodlanır ("/" dahil)."""
    return SEARCH_UNIQUE_TOURNAMENTS.format(query=quote(query, safe=""))


def search_all(query: str, page: int = 0) -> str:
    """
    Ada göre genel arama (takım, oyuncu, turnuva, ...; plan maddesi FX-19): `/search/all?q=...&page=N`
    (docs/all-sports/endpoints.csv; örnek research/all_sports/samples/football/search-all__1.json). Sorgu tümüyle
    kodlanır.
    """
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise ValueError(f"page must be a non-negative integer: {page!r}")
    return SEARCH_ALL.format(query=quote(query, safe=""), page=page)
