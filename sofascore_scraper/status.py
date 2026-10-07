"""
SofaScore `status` sınıflandırması ve spora göre skor çıkarımı.

Kurallar araştırma verisine dayanır: docs/status-matrix/README.md ("Durum evreni", "Edge case tablosu").
Sonuçlandırma bu repoda yapılmaz; kurallar için docs/settlement-notes.md.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, NamedTuple, Optional

from sofascore_scraper.sports import event_sport_slug, period_format, score_family, set_format

logger = logging.getLogger(__name__)


class StatusClass(str, Enum):
    NOT_STARTED = "not_started"  # type notstarted (code 0)
    # type inprogress (6, 7, 8-12, 13-16, 20, 21, 28, 29, 30, 31, 1001, 1002) ve kriketin gün sonu: willcontinue (141)
    LIVE = "live"
    COMPLETED = "completed"  # type finished, code 100/110/120 (oynandı ve bitti)
    DECIDED_WITHOUT_PLAY = "decided_without_play"  # type finished, code 91 Walkover / 92 Retired
    VOID = "void"  # type postponed/canceled/interrupted/suspended (60, 70, 80, 81, 90)
    UNKNOWN = "unknown"  # hiçbirine uymayan; loglanır, asla sessizce COMPLETED sayılmaz


_COMPLETED_CODES = frozenset({100, 110, 120})
_WITHOUT_PLAY_CODES = frozenset({91, 92})
# 11 / 12: 4. ve 5. set (masa tenisi, voleybol; SP-2). Tip inprogress olduğundan zaten LIVE sayılıyorlardı;
# burada yalnızca tipi olmayan yükte fark eder. SP-3'te görülenler de öyle: 21 (kriket 1. innings), 28 / 29
# (beyzbol 8. / 9. inning), 1001 / 1002 (e-spor 1. / 2. oyun) ve 141 (kriket "End of day 1"). Görülmeyen ara
# kodlar (22-27 gibi) eklenmedi: tipi olmayan yükte UNKNOWN kalırlar.
_LIVE_CODES = frozenset({6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 21, 28, 29, 30, 31, 141, 1001, 1002})
_VOID_CODES = frozenset({60, 70, 80, 81, 90})

# Çok günlü kriket maçında gün sonu (kod 141 "End of day 1"): maç sürüyor, yalnızca ertesi güne kadar ara
# veriliyor. Futbolun devre arası (31) gibi LIVE sayılır; böylece kapalı `status.class` sayımına yeni değer
# gerekmez (docs/design/04-schema-v1.md bölüm 2, madde 8). Plan maddesi SP-3.
_LIVE_TYPES = frozenset({"inprogress", "willcontinue"})
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
        if stype in _LIVE_TYPES:
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
    aet: Optional[Pair] = None  # display; code 110, ya da code 120 ve uzatma oynandıysa (bkz. _football_extra_time)
    penalties: Optional[Pair] = None
    aggregated: Optional[Pair] = None
    aggregated_winner_code: Optional[int] = None


@dataclass
class BasketballScores(ScoreSheet):
    # "quarters" | "halves" | "thirds" | None (periyot skoru yok). Basketbolda sezilir: iki yarı yalnızca
    # period2/period4'te gelir. Öteki sporlarda kayıt defterindeki sabit bölünüş (sofascore_scraper/sports.py period_format).
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


@dataclass
class SetsScores(ScoreSheet):
    """
    Set ailesi, tenis dışındaki sporlar (plan maddesi SP-2): voleybol, badminton, masa tenisi, padel, snooker.
    Tenisin çizelgesinden farkları:
      - `sets` set numarasıyla tutulur (periodN → N), sıra boşluklu olabilir: masa tenisinin canlı yükü yalnızca
        o anki seti taşıyabiliyor (yalnız period4). En çok 7 set okunur (masa tenisinde 7 setlik maçlar var;
        kayıtlı yüklerde en çok period5 görüldü).
      - retired / walkover bayrakları yok: durum söyler (04-schema-v1.md karar 10).
      - match tie-break sezgisi yalnızca oyunla sayılan sette (padel) uygulanır; sayıyla sayılan sporlarda bir
        set her zaman 10'u geçer.
      - snooker (frames): `current` kazanılan frame'dir; period1 `current`'ı tekrarladığı için set sayılmaz.
    """
    # "games" | "points" | "frames" | "legs" | "legs_won" | "games_won" (sofascore_scraper/sports.py SetFormat)
    format: Optional[str] = None
    sets_won: Optional[Pair] = None  # current
    sets: Dict[int, Pair] = field(default_factory=dict)  # set no → periodN
    tiebreaks: Dict[int, Pair] = field(default_factory=dict)  # set no → periodNTieBreak
    match_tiebreak: bool = False


@dataclass
class InningsScores(ScoreSheet):
    """
    Beyzbol (plan maddesi SP-3). İki takım her inning'de sırayla vurur; `innings` inning numarası → o inning'in
    sayıları (run). Kaynak `innings.inningN.run`; o inning yoksa `periodN` (NPB, KBO ve MLB hazırlık maçları
    ikisini de yollar, değerler aynıdır; bazı yüklerde `innings` kırpık ya da eksik gelebilir).
    `regulation` normaltime, `extra_innings` overtime (uzatma inning'lerinin toplamı; kod 110 "AET"),
    `hits` / `errors` maçın toplamı (`inningsBaseball`). Başlık skoru (display / current) toplam sayıdır.
    """
    innings: Dict[int, Pair] = field(default_factory=dict)
    regulation: Optional[Pair] = None
    extra_innings: Optional[Pair] = None
    hits: Optional[Pair] = None
    errors: Optional[Pair] = None


@dataclass
class CricketScores(ScoreSheet):
    """
    Kriket (plan maddesi SP-3). İki tarafın innings'leri birbirinden bağımsız numaralanır: `homeScore.innings`
    ev sahibinin 1. ve 2. innings'i, `awayScore.innings` konuğunkiler. Hangi tarafın önce vurduğu yükte yok.
    Her innings: runs (score), wickets, overs (SofaScore'un yazımı: 68.1 = 68 over 1 top). Başlık skoru
    (display / current) tarafın innings'lerinin toplam sayısıdır.
    """
    home_innings: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    away_innings: Dict[int, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class FightScores(ScoreSheet):
    """
    MMA (plan maddesi SP-3): skor yok (homeScore / awayScore boş gelir). Sonuç: kazanan `winnerCode`
    (ScoreSheet.winner_code), yöntem `winType` (UD, SD, TKO, SUB görüldü; olduğu gibi), bittiği raunt `finalRound`.
    """
    method: Optional[str] = None
    final_round: Optional[int] = None


_MAX_SETS = 7
# Bir yükten okunan en çok inning / innings (beyzbolda uzatma inning'leri 9'dan sonra sürer)
_MAX_INNINGS = 30
_INNING_KEY = re.compile(r"inning(\d+)")


def _numbered(node: Any) -> Dict[int, Any]:
    """`{"inning1": ..., "inning2": ...}` → {1: ..., 2: ...}; başka anahtarlar (kırpma işareti gibi) atlanır."""
    if not isinstance(node, dict):
        return {}
    found = {}
    for key, value in node.items():
        match = _INNING_KEY.fullmatch(str(key))
        if match and 0 < int(match.group(1)) <= _MAX_INNINGS:
            found[int(match.group(1))] = value
    return found


def _baseball_innings(home: Dict[str, Any], away: Dict[str, Any]) -> Dict[int, Pair]:
    """Inning başına sayı: `innings.inningN.run`, o inning'de yoksa `periodN`."""
    by_side = []
    for side in (home, away):
        runs = {n: v.get("run") for n, v in _numbered(side.get("innings")).items() if isinstance(v, dict)}
        for n in range(1, _MAX_INNINGS + 1):
            if runs.get(n) is None and side.get(f"period{n}") is not None:
                runs[n] = side.get(f"period{n}")
        by_side.append(runs)
    numbers = sorted(n for n in set(by_side[0]) | set(by_side[1])
                     if by_side[0].get(n) is not None or by_side[1].get(n) is not None)
    return {n: Pair(by_side[0].get(n), by_side[1].get(n)) for n in numbers}


def _cricket_innings(side: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Bir tarafın innings'leri: numara → {"runs", "wickets", "overs"} (SofaScore'un score / wickets / overs)."""
    return {
        n: {"runs": v.get("score"), "wickets": v.get("wickets"), "overs": v.get("overs")}
        for n, v in sorted(_numbered(side.get("innings")).items()) if isinstance(v, dict)
    }


def _set_unit(event: Dict[str, Any], sport: Optional[str]) -> Optional[str]:
    """
    Set ailesinde setin birimi (sofascore_scraper/sports.py set_format). Dart'ta maç olaya göre değişir: bestOfSets varsa set
    usulüdür (`legs`: periodN o setteki leg'ler), yoksa yalnızca leg sayılır (`legs_won`).
    """
    unit = set_format(sport)
    if unit == "legs":
        best_of_sets = event.get("bestOfSets")
        if not (isinstance(best_of_sets, int) and not isinstance(best_of_sets, bool) and best_of_sets > 0):
            return "legs_won"
    return unit


# Set listesi olmayan birimler: başlık skoru sayının kendisidir, periodN bir set skoru değildir
_COUNT_ONLY_UNITS = frozenset({"frames", "legs_won", "games_won"})


def _is_match_tiebreak(games: List[Pair]) -> bool:
    """
    Sezgisel: 3. ya da 5. set (son oynanan set) ≥ 10 ise normal set olamaz (normal set en çok 7),
    10 puanlık match tie-break sayılır. Tie-break'siz uzun set formatında (ör. 10-8) yanlış sonuç verir.
    """
    if len(games) not in (3, 5):
        return False
    last = games[-1]
    return max(last.home or 0, last.away or 0) >= 10


# Futbolda uzatmanın oynandığını gösteren anahtarlar: iki uzatma devresi ve toplamları (kod 110 ve uzatmadan
# sonra penaltıya giden kod 120 yüklerinde görüldü; 0-0 biten uzatmada da gelir)
_FOOTBALL_EXTRA_TIME_KEYS = ("overtime", "extra1", "extra2")


def _football_extra_time(code: Any, home: Dict[str, Any], away: Dict[str, Any]) -> bool:
    """
    Uzatma oynandı mı. Kod 110 (AET) her zaman evet. Kod 120 (AP) tek başına yetmez: uzatmasız doğrudan
    penaltıya giden maçlar var (UEFA Süper Kupası 2025, event 13960989: normaltime 2-2, penaltılar 4-3, uzatma
    anahtarı yok). Kod 120'de yalnızca SofaScore uzatma anahtarlarından birini yolladıysa evet.
    """
    if code == 110:
        return True
    if code != 120:
        return False
    return any(_pair(home, away, key) is not None for key in _FOOTBALL_EXTRA_TIME_KEYS)


def extract_scores(event: Dict[str, Any], sport: Optional[str] = None) -> ScoreSheet:
    """
    Spor parametre ile verilirse o kullanılır; yoksa event.tournament.category.sport.slug.
    Hangi skor sınıfının döneceğini sporun kayıt defterindeki skor ailesi belirler (sofascore_scraper/sports.py).
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
            aet=display if _football_extra_time(code, home, away) else None,
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

    if family == "sets" and (unit := _set_unit(event, sport)) is not None:
        by_set = {} if unit in _COUNT_ONLY_UNITS else {
            n: p for n in range(1, _MAX_SETS + 1) if (p := _pair(home, away, f"period{n}"))}
        set_tiebreaks = {} if unit != "games" else {
            n: p for n in range(1, _MAX_SETS + 1) if (p := _pair(home, away, f"period{n}TieBreak"))}
        return SetsScores(
            **common,
            format=unit,
            sets_won=_pair(home, away, "current"),
            sets=by_set,
            tiebreaks=set_tiebreaks,
            match_tiebreak=unit == "games" and _is_match_tiebreak([by_set[n] for n in sorted(by_set)]),
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

    if family == "innings":
        totals_home, totals_away = home.get("inningsBaseball") or {}, away.get("inningsBaseball") or {}
        totals_home = totals_home if isinstance(totals_home, dict) else {}
        totals_away = totals_away if isinstance(totals_away, dict) else {}
        return InningsScores(
            **common,
            innings=_baseball_innings(home, away),
            regulation=_pair(home, away, "normaltime"),
            extra_innings=_pair(home, away, "overtime"),
            hits=_pair(totals_home, totals_away, "hits"),
            errors=_pair(totals_home, totals_away, "errors"),
        )

    if family == "cricket":
        return CricketScores(**common, home_innings=_cricket_innings(home), away_innings=_cricket_innings(away))

    if family == "fight":
        final_round = event.get("finalRound")
        return FightScores(
            **common,
            method=event.get("winType") if isinstance(event.get("winType"), str) else None,
            final_round=final_round if isinstance(final_round, int) and not isinstance(final_round, bool) else None,
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
