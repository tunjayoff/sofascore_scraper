"""
Dilim sonucu (Outcome) ve "bu yanıtta veri var mı" yüklemleri.

Saf modül: disk, ağ, yapılandırma ve günlük yoktur; import edildiğinde standart kitaplık ile src.exceptions
dışında hiçbir şey yüklenmez. Çekici (src/match_data_fetcher.py), istemci ve depo aynı sonuç tipini ve aynı
yüklemleri buradan alır (docs/design/01-storage.md 2.3, docs/design/02-services.md 2.4).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, Dict, Final, Literal, Mapping, Optional

from src.exceptions import ResourceNotFoundError

OutcomeStatus = Literal["ok", "empty", "failed", "skipped"]
OutcomeVia = Literal["curl", "bridge"]

SLICE_OK: Final = "ok"  # yanıt geldi, veri var
SLICE_EMPTY: Final = "empty"  # kesin yanıt: kaynak yok (404) ya da içinde veri olmayan 200
SLICE_FAILED: Final = "failed"  # istek başarısız: dilimin var olup olmadığı bilinmiyor
SLICE_SKIPPED: Final = "skipped"  # istek hiç gönderilmedi; bu durumu henüz hiçbir yol üretmiyor


@dataclass(frozen=True)
class Outcome:
    """Bir isteğin tipli sonucu (async ve sync yollar aynısını üretir); kod tabanındaki tek sonuç tipi."""

    status: OutcomeStatus  # SLICE_OK | SLICE_EMPTY | SLICE_FAILED | SLICE_SKIPPED
    # Ayrıştırılmış JSON. SLICE_OK'te doludur; SLICE_EMPTY'de içinde veri olmayan 200 gövdesi olabilir
    data: Any = None
    # SLICE_FAILED: "403" | "429" | "5xx" | "timeout" | "network" | "parse" | "breaker" | "other"
    # SLICE_EMPTY: "404" ya da "empty"
    # SLICE_SKIPPED: "breaker" | "not_selected" | "not_applicable" | "not_due" | "cancelled" (ileride; açık devre
    #   kesici bugün SLICE_FAILED / "breaker" olarak bildirilir)
    reason: Optional[str] = None
    http_status: Optional[int] = None
    # Yanıtın alındığı an. None: kaydeden "şimdi" sayar
    fetched_at: Optional[dt.datetime] = None
    # Yanıtı getiren yol; bilinmiyorsa None
    via: Optional[OutcomeVia] = None
    # Durumun yanında saklanacak küçük JSON (ör. bir tur sayfası için {"complete": true})
    meta: Optional[Mapping[str, Any]] = None

    @property
    def failed(self) -> bool:
        return self.status == SLICE_FAILED

    @classmethod
    def from_error(cls, exc: BaseException) -> "Outcome":
        """İsteği bitiren hata → sonuç: 404 kesin "yok"tur, gerisi başarısızlık."""
        # Hata türü eşlemesi src.breaker'da durur; o modül günlükçüyü kurduğu için yalnızca burada yüklenir
        # (modülün kendisi import edildiğinde saf kalsın diye).
        from src import breaker as request_breaker

        if isinstance(exc, ResourceNotFoundError):
            return cls(SLICE_EMPTY, reason=request_breaker.NOT_FOUND, http_status=404)
        return cls(
            SLICE_FAILED,
            reason=request_breaker.failure_kind(exc),
            http_status=request_breaker.http_status(exc),
        )


# Eski ad: detay dilimi isteğinin sonucu. Yeni kod Outcome'u kullanır.
SliceOutcome = Outcome


# --- "bu yanıtta veri var mı" yüklemleri -----------------------------------------------------
#
# Hepsi maçın birleşik sözlüğünü ({"basic": ..., "statistics": ..., ...}) alır ve yalnızca kendi anahtarına
# bakar. Tek bir yanıt gövdesi için {anahtar: gövde} verilir. Beklenmeyen biçimdeki gövdede (ör. sözlük yerine
# metin) bazıları AttributeError ile düşer; bu davranış MatchDataFetcher'daki eski metotlardan aynen taşındı.


def statistics_has_data(d: Mapping[str, Any]) -> bool:
    s = d.get("statistics")
    if s is None:
        return False
    periods = s if isinstance(s, list) else (s.get("statistics") or [])
    all_periods = [p for p in periods if p and p.get("period") == "ALL"]
    if not all_periods and periods:
        all_periods = [periods[0]]
    for p in all_periods:
        for g in p.get("groups") or []:
            if (g.get("statisticsItems") or []):
                return True
    return False


def has_lineups_data_dict(d: Mapping[str, Any]) -> bool:
    L = d.get("lineups")
    if not L or not isinstance(L, dict):
        return False
    for side in ("home", "away"):
        block = L.get(side)
        if not isinstance(block, dict):
            continue
        players = block.get("players")
        if isinstance(players, list) and len(players) > 0:
            return True
    return False


def has_h2h_data_dict(d: Mapping[str, Any]) -> bool:
    h = d.get("h2h")
    if not h or not isinstance(h, dict):
        return False
    td = h.get("teamDuel") or {}
    if td and any(td.get(x) is not None for x in ("homeWins", "awayWins", "draws")):
        return True
    raw = h.get("matches") or h.get("events") or td.get("matches")
    return isinstance(raw, list) and len(raw) > 0


def has_pregame_form_data_dict(d: Mapping[str, Any]) -> bool:
    p = d.get("pregame_form")
    if not p or not isinstance(p, dict):
        return False

    def chk(t: Any) -> bool:
        if not t or not isinstance(t, dict):
            return False
        form = t.get("form")
        if isinstance(form, list) and len(form) > 0:
            return True
        return any(t.get(x) is not None for x in ("position", "value", "avgRating"))

    return chk(p.get("homeTeam")) or chk(p.get("awayTeam"))


def has_team_streaks_data_dict(d: Mapping[str, Any]) -> bool:
    g = (d.get("team_streaks") or {}).get("general")
    return isinstance(g, list) and len(g) > 0


def has_incidents_data_dict(d: Mapping[str, Any]) -> bool:
    raw = d.get("incidents")
    if raw and isinstance(raw, dict) and not isinstance(raw, list):
        raw = raw.get("incidents")
    return isinstance(raw, list) and len(raw) > 0


# Kendi yüklemi olan dilimler. Burada olmayan anahtar ("basic" dahil) için değerin dolu olması yeter.
_PRESENCE_PREDICATES: Dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "statistics": statistics_has_data,
    "lineups": has_lineups_data_dict,
    "h2h": has_h2h_data_dict,
    "team_streaks": has_team_streaks_data_dict,
    "pregame_form": has_pregame_form_data_dict,
    "incidents": has_incidents_data_dict,
}


def match_detail_slice_present(key: str, d: Mapping[str, Any]) -> bool:
    """
    Maçın birleşik sözlüğünde `key` diliminin verisi var mı. Kendi yüklemi olmayan anahtarda ("basic" ve
    tabloda olmayan her dilim) değerin dolu olması yeter.
    """
    predicate = _PRESENCE_PREDICATES.get(key)
    if predicate is not None:
        return predicate(d)
    return bool(d.get(key))
