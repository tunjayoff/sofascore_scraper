"""
Dilim sonucu (Outcome) ve "bu yanıtta veri var mı" kuralları.

Saf modül: disk, ağ, yapılandırma ve günlük yoktur; import edildiğinde standart kitaplık ile src.exceptions
dışında hiçbir şey yüklenmez. Çekici (src/match_data_fetcher.py), istemci ve depo aynı sonuç tipini ve aynı
kuralları buradan alır (docs/design/01-storage.md 2.3, docs/design/02-services.md 2.4).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, Dict, Final, FrozenSet, List, Literal, Mapping, Optional

from src.exceptions import ResourceNotFoundError

OutcomeStatus = Literal["ok", "empty", "failed", "skipped"]
OutcomeVia = Literal["curl", "bridge"]

SLICE_OK: Final = "ok"  # yanıt geldi, veri var
SLICE_EMPTY: Final = "empty"  # kesin yanıt: kaynak yok (404) ya da içinde veri olmayan 200
SLICE_FAILED: Final = "failed"  # istek başarısız: dilimin var olup olmadığı bilinmiyor
SLICE_SKIPPED: Final = "skipped"  # istek hiç gönderilmedi (istemci, devre kesici açıkken bunu döndürür)


@dataclass(frozen=True)
class Outcome:
    """Bir isteğin tipli sonucu (async ve sync yollar aynısını üretir); kod tabanındaki tek sonuç tipi."""

    status: OutcomeStatus  # SLICE_OK | SLICE_EMPTY | SLICE_FAILED | SLICE_SKIPPED
    # Ayrıştırılmış JSON. SLICE_OK'te doludur; SLICE_EMPTY'de içinde veri olmayan 200 gövdesi olabilir
    data: Any = None
    # SLICE_FAILED: "403" | "429" | "5xx" | "timeout" | "network" | "parse" | "breaker" | "other"
    # SLICE_EMPTY: "404" ya da "empty"
    # SLICE_SKIPPED: "breaker" (istemci, src/client: devre kesici açık); "not_selected" | "not_applicable" |
    #   "not_due" | "cancelled" ileride. Çekiciler (from_error, MatchDataFetcher) açık devre kesiciyi hâlâ
    #   SLICE_FAILED / "breaker" olarak bildirir
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


# --- "bu yanıtta veri var mı" kuralları ------------------------------------------------------
#
# Kayıtlı her dilimin (src/sports.py DETAIL_SLICES) kendi kuralı vardır. Kural tek bir yanıt gövdesine bakar
# ve üç yanıttan birini verir:
#
#   BODY_DATA       gövdede dilimin verisi var
#   BODY_NO_DATA    gövde okunabiliyor ama içinde veri yok (None, boş nesne, boş liste, ...)
#   BODY_MALFORMED  kural gövdeyi okuyamadı: içine bakması gereken değer beklenen JSON türünde değil
#
# Kurallar toplamdır: hangi değer verilirse verilsin hata fırlatmazlar. Kayıt defterine yeni bir dilim
# eklendiğinde buraya da kuralı eklenir (tests/test_slices.py eksik kuralı yakalar).
#
# Altı eski kuralın BODY_DATA ve BODY_NO_DATA yanıtları MatchDataFetcher'dan taşınan yüklemlerin True ve
# False yanıtlarıyla aynıdır; BODY_MALFORMED tam olarak o yüklemlerin eskiden hata fırlattığı gövdelerdir
# (eski düzen okuyucusu bunları zaten bozuk sayıyordu). Eski yüklemin açıkça elediği yanlış türler (ör.
# `lineups` gövdesinin liste, `incidents` gövdesinin metin olması) eskisi gibi BODY_NO_DATA'dır.

BodyState = Literal["data", "no_data", "malformed"]

BODY_DATA: Final = "data"
BODY_NO_DATA: Final = "no_data"
BODY_MALFORMED: Final = "malformed"


def _filled_list(value: Any) -> BodyState:
    return BODY_DATA if isinstance(value, list) and len(value) > 0 else BODY_NO_DATA


def _statistics_state(body: Any) -> BodyState:
    """
    ALL periyodunda (yoksa ilk periyotta) en az bir dolu grup. Gövde {"statistics": [...]} ya da doğrudan
    periyot listesidir; yalnızca None "yok" sayılır, nesne ya da liste olmayan her gövde (0 dahil) okunamaz.
    """
    if body is None:
        return BODY_NO_DATA
    if isinstance(body, list):
        periods: Any = body
    elif isinstance(body, dict):
        periods = body.get("statistics") or []
    else:
        return BODY_MALFORMED
    if not isinstance(periods, list):
        return BODY_MALFORMED
    all_periods: List[Dict[str, Any]] = []
    for period in periods:
        if not period:
            continue
        if not isinstance(period, dict):
            return BODY_MALFORMED
        if period.get("period") == "ALL":
            all_periods.append(period)
    if not all_periods and periods:
        first = periods[0]
        if not isinstance(first, dict):
            return BODY_MALFORMED  # ALL yokken ilk periyoda bakılır; o da nesne değil
        all_periods = [first]
    for period in all_periods:
        groups = period.get("groups") or []
        if not isinstance(groups, list):
            return BODY_MALFORMED
        for group in groups:
            if not isinstance(group, dict):
                return BODY_MALFORMED
            if group.get("statisticsItems") or []:
                return BODY_DATA
    return BODY_NO_DATA


def _lineups_state(body: Any) -> BodyState:
    """İki taraftan birinde en az bir oyuncu."""
    if not body or not isinstance(body, dict):
        return BODY_NO_DATA
    for side in ("home", "away"):
        block = body.get(side)
        if isinstance(block, dict) and _filled_list(block.get("players")) == BODY_DATA:
            return BODY_DATA
    return BODY_NO_DATA


def _h2h_state(body: Any) -> BodyState:
    """teamDuel sayılarından biri (0 dahil) ya da bir maç listesi. Dolu ama nesne olmayan teamDuel okunamaz."""
    if not body or not isinstance(body, dict):
        return BODY_NO_DATA
    duel = body.get("teamDuel") or {}
    if not isinstance(duel, dict):
        return BODY_MALFORMED
    if any(duel.get(x) is not None for x in ("homeWins", "awayWins", "draws")):
        return BODY_DATA
    return _filled_list(body.get("matches") or body.get("events") or duel.get("matches"))


def _pregame_form_state(body: Any) -> BodyState:
    """Bir takımda form listesi ya da position / value / avgRating."""
    if not body or not isinstance(body, dict):
        return BODY_NO_DATA
    for side in ("homeTeam", "awayTeam"):
        team = body.get(side)
        if not team or not isinstance(team, dict):
            continue
        if _filled_list(team.get("form")) == BODY_DATA:
            return BODY_DATA
        if any(team.get(x) is not None for x in ("position", "value", "avgRating")):
            return BODY_DATA
    return BODY_NO_DATA


def _team_streaks_state(body: Any) -> BodyState:
    """Yalnızca "general" listesi sayılır. Dolu ama nesne olmayan gövde okunamaz."""
    if not body:
        return BODY_NO_DATA
    if not isinstance(body, dict):
        return BODY_MALFORMED
    return _filled_list(body.get("general"))


def _incidents_state(body: Any) -> BodyState:
    """{"incidents": [...]} ya da doğrudan liste."""
    if body and isinstance(body, dict):
        body = body.get("incidents")
    return _filled_list(body)


def _point_by_point_state(body: Any) -> BodyState:
    """
    {"pointByPoint": [...]}: liste doluysa veri var. None, boş nesne, boş liste, anahtarı olmayan nesne ve
    boş `pointByPoint` listesi "veri yok"tur; nesne olmayan başka her gövde (dolu liste, metin, sayı) ile
    liste olmayan `pointByPoint` okunamaz.
    """
    if body is None or (isinstance(body, (dict, list)) and not body):
        return BODY_NO_DATA
    if not isinstance(body, dict):
        return BODY_MALFORMED
    points = body.get("pointByPoint")
    if points is None:
        return BODY_NO_DATA
    if not isinstance(points, list):
        return BODY_MALFORMED
    return _filled_list(points)


def _esports_games_state(body: Any) -> BodyState:
    """
    {"games": [...]} (e-spor, SP-3): liste doluysa veri var. Kuralı point_by_point'inkiyle aynıdır: None, boş
    nesne, boş liste ve `games` anahtarı olmayan nesne "veri yok"; nesne olmayan başka gövde ile liste olmayan
    `games` okunamaz.
    """
    if body is None or (isinstance(body, (dict, list)) and not body):
        return BODY_NO_DATA
    if not isinstance(body, dict):
        return BODY_MALFORMED
    games = body.get("games")
    if games is None:
        return BODY_NO_DATA
    if not isinstance(games, list):
        return BODY_MALFORMED
    return _filled_list(games)


def _innings_state(body: Any) -> BodyState:
    """
    {"innings": [...]} (kriketin skor kartı): liste doluysa veri var. Kuralı esports_games'inkiyle aynıdır:
    None, boş nesne, boş liste ve `innings` anahtarı olmayan nesne "veri yok"; nesne olmayan başka gövde ile
    liste olmayan `innings` okunamaz.
    """
    if body is None or (isinstance(body, (dict, list)) and not body):
        return BODY_NO_DATA
    if not isinstance(body, dict):
        return BODY_MALFORMED
    innings = body.get("innings")
    if innings is None:
        return BODY_NO_DATA
    if not isinstance(innings, list):
        return BODY_MALFORMED
    return _filled_list(innings)


# Dilim anahtarı → kural. Kayıt defterindeki her dilim burada olmalıdır.
_BODY_RULES: Dict[str, Callable[[Any], BodyState]] = {
    "statistics": _statistics_state,
    "lineups": _lineups_state,
    "h2h": _h2h_state,
    "team_streaks": _team_streaks_state,
    "pregame_form": _pregame_form_state,
    "incidents": _incidents_state,
    "point_by_point": _point_by_point_state,
    "esports_games": _esports_games_state,
    "innings": _innings_state,
}

# Kendi kuralı olan dilim anahtarları
PRESENCE_RULE_KEYS: Final[FrozenSet[str]] = frozenset(_BODY_RULES)


def slice_body_state(key: str, body: Any) -> BodyState:
    """
    Tek bir yanıt gövdesi için üç yanıttan biri: BODY_DATA, BODY_NO_DATA ya da BODY_MALFORMED. Hata
    fırlatmaz.

    Kuralı olmayan anahtarda ("basic" ve kayıt defterinde olmayan her anahtar) değerin dolu olması yeter;
    yanıt o zaman hiçbir zaman BODY_MALFORMED olmaz.
    """
    rule = _BODY_RULES.get(key)
    if rule is not None:
        return rule(body)
    return BODY_DATA if body else BODY_NO_DATA


# --- yüklemler -------------------------------------------------------------------------------
#
# Hepsi maçın birleşik sözlüğünü ({"basic": ..., "statistics": ..., ...}) alır ve yalnızca kendi anahtarına
# bakar. Tek bir yanıt gövdesi için {anahtar: gövde} verilir. Okunamayan gövde de False verir; "veri yok"
# ile "okunamadı"yı ayırması gereken çağıran slice_body_state'i kullanır.


def statistics_has_data(d: Mapping[str, Any]) -> bool:
    return _statistics_state(d.get("statistics")) == BODY_DATA


def has_lineups_data_dict(d: Mapping[str, Any]) -> bool:
    return _lineups_state(d.get("lineups")) == BODY_DATA


def has_h2h_data_dict(d: Mapping[str, Any]) -> bool:
    return _h2h_state(d.get("h2h")) == BODY_DATA


def has_pregame_form_data_dict(d: Mapping[str, Any]) -> bool:
    return _pregame_form_state(d.get("pregame_form")) == BODY_DATA


def has_team_streaks_data_dict(d: Mapping[str, Any]) -> bool:
    return _team_streaks_state(d.get("team_streaks")) == BODY_DATA


def has_incidents_data_dict(d: Mapping[str, Any]) -> bool:
    return _incidents_state(d.get("incidents")) == BODY_DATA


def has_point_by_point_data_dict(d: Mapping[str, Any]) -> bool:
    return _point_by_point_state(d.get("point_by_point")) == BODY_DATA


def has_esports_games_data_dict(d: Mapping[str, Any]) -> bool:
    return _esports_games_state(d.get("esports_games")) == BODY_DATA


def has_innings_data_dict(d: Mapping[str, Any]) -> bool:
    return _innings_state(d.get("innings")) == BODY_DATA


def match_detail_slice_present(key: str, d: Mapping[str, Any]) -> bool:
    """
    Maçın birleşik sözlüğünde `key` diliminin verisi var mı. Hata fırlatmaz: okunamayan gövde False verir.
    Kuralı olmayan anahtarda ("basic" ve kayıt defterinde olmayan her anahtar) değerin dolu olması yeter.
    """
    return slice_body_state(key, d.get(key)) == BODY_DATA
