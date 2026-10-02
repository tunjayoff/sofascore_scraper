"""
SofaScore `status` sınıflandırması ve spora göre skor çıkarımı.

Kurallar araştırma verisine dayanır: docs/status-matrix/README.md ("Durum evreni", "Edge case tablosu").
Sonuçlandırma bu repoda yapılmaz; kurallar için docs/settlement-notes.md.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, NamedTuple, Optional

from src.sports import event_sport_slug, period_format, score_family

logger = logging.getLogger(__name__)


class StatusClass(str, Enum):
    NOT_STARTED = "not_started"  # type notstarted (code 0)
    LIVE = "live"  # type inprogress (6, 7, 8-12, 13-16, 20, 30, 31)
    COMPLETED = "completed"  # type finished, code 100/110/120 (oynandı ve bitti)
    DECIDED_WITHOUT_PLAY = "decided_without_play"  # type finished, code 91 Walkover / 92 Retired
    VOID = "void"  # type postponed/canceled/interrupted/suspended (60, 70, 80, 81, 90)
    UNKNOWN = "unknown"  # hiçbirine uymayan; loglanır, asla sessizce COMPLETED sayılmaz


_COMPLETED_CODES = frozenset({100, 110, 120})
_WITHOUT_PLAY_CODES = frozenset({91, 92})
# 11 / 12: 4. ve 5. set (masa tenisi, voleybol; SP-2). Tip inprogress olduğundan zaten LIVE sayılıyorlardı;
# burada yalnızca tipi olmayan yükte fark eder.
_LIVE_CODES = frozenset({6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 30, 31})
_VOID_CODES = frozenset({60, 70, 80, 81, 90})

_VOID_TYPES = frozenset({"postponed", "canceled", "interrupted", "suspended"})

# Yalnızca type ve code yoksa kullanılır
_BY_DESCRIPTION = {
    "not started": StatusClass.NOT_STARTED,
    "ended": StatusClass.COMPLETED,
    "aet": StatusClass.COMPLETED,
    "after extra time": StatusClass.COMPLETED,
    "ap": StatusClass.COMPLETED,
    "penalties": StatusClass.COMPLETED,
    "walkover": StatusClass.DECIDED_WITHOUT_PLAY,
    "retired": StatusClass.DECIDED_WITHOUT_PLAY,
    "postponed": StatusClass.VOID,
    "canceled": StatusClass.VOID,
    "abandoned": StatusClass.VOID,
    "interrupted": StatusClass.VOID,
    "suspended": StatusClass.VOID,
}


def _by_code(code: int) -> StatusClass:
    if code == 0:
        return StatusClass.NOT_STARTED
    if code in _LIVE_CODES:
        return StatusClass.LIVE
    if code in _COMPLETED_CODES:
        return StatusClass.COMPLETED
    if code in _WITHOUT_PLAY_CODES:
        return StatusClass.DECIDED_WITHOUT_PLAY
    if code in _VOID_CODES:
        return StatusClass.VOID
    return StatusClass.UNKNOWN


def classify_status(event: Optional[Dict[str, Any]]) -> StatusClass:
    """Karar sırası: status.type → status.code → description (yalnızca type ve code yoksa)."""
    status = (event or {}).get("status") or {}
    if not isinstance(status, dict) or not status:
        return StatusClass.UNKNOWN
    stype = status.get("type")
    code = status.get("code")

    if stype:
        if stype == "notstarted":
            return StatusClass.NOT_STARTED
        if stype == "inprogress":
            return StatusClass.LIVE
        if stype in _VOID_TYPES:
            return StatusClass.VOID
        if stype == "finished":
            if code in _COMPLETED_CODES:
                return StatusClass.COMPLETED
            if code in _WITHOUT_PLAY_CODES:
                return StatusClass.DECIDED_WITHOUT_PLAY
            if code is None:  # kod yoksa description yedek; finished türüyle çelişen sonuç kabul edilmez
                by_desc = _BY_DESCRIPTION.get(str(status.get("description") or "").strip().lower())
                if by_desc in (StatusClass.COMPLETED, StatusClass.DECIDED_WITHOUT_PLAY):
                    return by_desc
            logger.warning(f"Bilinmeyen finished kodu: {status} (event {(event or {}).get('id')})")
            return StatusClass.UNKNOWN
        logger.warning(f"Bilinmeyen status type: {status} (event {(event or {}).get('id')})")
        return StatusClass.UNKNOWN

    if code is not None:
        result = _by_code(code)
    else:
        result = _BY_DESCRIPTION.get(str(status.get("description") or "").strip().lower(), StatusClass.UNKNOWN)
    if result is StatusClass.UNKNOWN:
        logger.warning(f"Sınıflandırılamayan status: {status} (event {(event or {}).get('id')})")
    return result


def is_played(event: Optional[Dict[str, Any]]) -> bool:
    """Maç oynandı ve bitti (hükmen / çekilme / iptal değil)."""
    return classify_status(event) is StatusClass.COMPLETED


# --- Skor çıkarımı ---------------------------------------------------------------------


class Pair(NamedTuple):
    home: Optional[Any]
    away: Optional[Any]


def _pair(home: Dict[str, Any], away: Dict[str, Any], key: str) -> Optional[Pair]:
    h, a = home.get(key), away.get(key)
    return None if h is None and a is None else Pair(h, a)


@dataclass
class ScoreSheet:
    sport: Optional[str]
    status_class: StatusClass
    winner_code: Optional[int]  # 1 ev, 2 deplasman, 3 berabere
    raw_change_ts: Optional[int]  # changes.changeTimestamp
    raw_changed_fields: List[str]  # changes.changes (yalnızca son güncelleme)

    @property
    def settleable(self) -> bool:
        """Yalnızca oynanıp biten maç; hükmen/çekilme ve iptal elle ya da kurala göre işlenir."""
        return self.status_class is StatusClass.COMPLETED


@dataclass
class FootballScores(ScoreSheet):
    """`current` kullanılmaz: AP'de penaltıları içerir (10-9), `display` içermez (3-3)."""
    ht: Optional[Pair] = None  # period1
    ft90: Optional[Pair] = None  # normaltime
    aet: Optional[Pair] = None  # display; yalnızca code 110/120
    penalties: Optional[Pair] = None
    aggregated: Optional[Pair] = None
    aggregated_winner_code: Optional[int] = None


@dataclass
class BasketballScores(ScoreSheet):
    # "quarters" | "halves" | "thirds" | None (periyot skoru yok). Basketbolda sezilir: iki yarı yalnızca
    # period2/period4'te gelir. Öteki sporlarda kayıt defterindeki sabit bölünüş (src/sports.py period_format).
    format: Optional[str] = None
    periods: Dict[str, Pair] = field(default_factory=dict)
    regulation: Optional[Pair] = None  # normaltime
    overtime: Optional[Pair] = None
    final: Optional[Pair] = None  # current


@dataclass
class PeriodsScores(BasketballScores):
    """
    Periyot ailesi, basketbol dışındaki sporlar (plan maddesi SP-1). Basketbolun alanlarına ek olarak
    penaltı atışları ve iki maçlı eşleşmenin toplamı okunur (hentbolda görüldü: kod 120 AP ile `penalties`,
    rövanşta `aggregated`). Basketbol çizelgesi bu alanları taşımaz; çıktısı değişmesin diye ayrı sınıf.
    Hentbolun tek AP örneğinde `current` ve `display` penaltıları içerir (31 = 22 + 5 + 4): `final` de içerir.
    """
    penalties: Optional[Pair] = None
    aggregated: Optional[Pair] = None
    aggregated_winner_code: Optional[int] = None


@dataclass
class TennisScores(ScoreSheet):
    """`normaltime` kullanılmaz: Retired maçlarda yok."""
    sets_won: Optional[Pair] = None  # current
    games: List[Pair] = field(default_factory=list)  # period1..period5 (match tie-break setinde puan)
    tiebreaks: Dict[int, Pair] = field(default_factory=dict)  # set no → periodNTieBreak
    retired: bool = False  # code 92
    walkover: bool = False  # code 91
    match_tiebreak: bool = False  # sezgisel, bkz. _is_match_tiebreak


def _is_match_tiebreak(games: List[Pair]) -> bool:
    """
    Sezgisel: 3. ya da 5. set (son oynanan set) ≥ 10 ise normal set olamaz (normal set en çok 7),
    10 puanlık match tie-break sayılır. Tie-break'siz uzun set formatında (ör. 10-8) yanlış sonuç verir.
    """
    if len(games) not in (3, 5):
        return False
    last = games[-1]
    return max(last.home or 0, last.away or 0) >= 10


def extract_scores(event: Dict[str, Any], sport: Optional[str] = None) -> ScoreSheet:
    """
    Spor parametre ile verilirse o kullanılır; yoksa event.tournament.category.sport.slug.
    Hangi skor sınıfının döneceğini sporun kayıt defterindeki skor ailesi belirler (src/sports.py).
    """
    sport = (sport or event_sport_slug(event) or "").lower() or None
    family = score_family(sport)
    status = event.get("status") or {}
    code = status.get("code")
    home = event.get("homeScore") or {}
    away = event.get("awayScore") or {}
    changes = event.get("changes") or {}
    common = dict(
        sport=sport,
        status_class=classify_status(event),
        winner_code=event.get("winnerCode"),
        raw_change_ts=changes.get("changeTimestamp"),
        raw_changed_fields=list(changes.get("changes") or []),
    )

    if family == "football":
        ft90 = _pair(home, away, "normaltime")
        display = _pair(home, away, "display")
        if code == 100 and display is not None and ft90 is not None and display != ft90:
            logger.warning(f"Futbol {event.get('id')}: code 100 ama display {display} != normaltime {ft90}")
        return FootballScores(
            **common,
            ht=_pair(home, away, "period1"),
            ft90=ft90,
            aet=display if code in (110, 120) else None,
            penalties=_pair(home, away, "penalties"),
            aggregated=_pair(home, away, "aggregated"),
            aggregated_winner_code=event.get("aggregatedWinnerCode"),
        )

    if family == "periods":
        periods = {k: p for k in ("period1", "period2", "period3", "period4") if (p := _pair(home, away, k))}
        fixed = period_format(sport)
        if not periods:
            fmt: Optional[str] = None
        elif fixed is not None:
            fmt = fixed
        # Basketbol: iki yarı formatında period1/period3 hiç gelmez; biri varsa çeyrek (maç sürüyor ya da
        # yarıda kalmış olsa da)
        elif {"period1", "period3"} & set(periods):
            fmt = "quarters"
        else:
            fmt = "halves"
        sheet = dict(
            common,
            format=fmt,
            periods=periods,
            regulation=_pair(home, away, "normaltime"),
            overtime=_pair(home, away, "overtime"),
            final=_pair(home, away, "current"),
        )
        if fixed is None:
            return BasketballScores(**sheet)
        return PeriodsScores(
            **sheet,
            penalties=_pair(home, away, "penalties"),
            aggregated=_pair(home, away, "aggregated"),
            aggregated_winner_code=event.get("aggregatedWinnerCode"),
        )

    if family == "sets":
        games = []
        for n in range(1, 6):
            p = _pair(home, away, f"period{n}")
            if p is None:
                break
            games.append(p)
        tiebreaks = {n: p for n in range(1, 6) if (p := _pair(home, away, f"period{n}TieBreak"))}
        return TennisScores(
            **common,
            sets_won=_pair(home, away, "current"),
            games=games,
            tiebreaks=tiebreaks,
            retired=code == 92,
            walkover=code == 91,
            match_tiebreak=_is_match_tiebreak(games),
        )

    logger.warning(f"extract_scores: desteklenmeyen spor {sport!r} (event {event.get('id')})")
    return ScoreSheet(**common)


# --- Gözlem kaydı (kesinlik takibi) ------------------------------------------------------

OBSERVATION_KEY = "observation"  # match_details/.../{id}/observation.json, basic.json'ın yanında


def observation_record(event: Dict[str, Any], observed_at: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Bizim /event yanıtını aldığımız an ve SofaScore'un son değişiklik zamanı (changes.changeTimestamp)."""
    when = observed_at or dt.datetime.now(dt.timezone.utc)
    return {
        "observed_at_utc": when.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "change_ts": ((event or {}).get("changes") or {}).get("changeTimestamp"),
    }


def read_observation(match_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Eski kayıtlarda alan yok: eksik olan None döner."""
    stored = (match_data or {}).get(OBSERVATION_KEY)
    stored = stored if isinstance(stored, dict) else {}
    return {"observed_at_utc": stored.get("observed_at_utc"), "change_ts": stored.get("change_ts")}
