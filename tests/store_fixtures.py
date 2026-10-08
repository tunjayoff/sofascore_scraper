"""
Eski (legacy) veri dizini fabrikası: `docs/design/01-storage.md` bölüm 5.1'deki her disk biçimini
içeren `DATA_DIR` ağaçları kurar (plan maddesi G-02).

Olayların durum / skor / zaman alanları `tests/fixtures/status/` altındaki gerçek SofaScore
yanıtlarından gelir; turnuva, sezon ve takım zarfı burada eklenir. Detay dilimleri küçük ama
`sofascore_scraper/match_data_fetcher.py`'deki "veri var mı" denetimlerinden geçen sentetik yanıtlardır.

Bu modül `sofascore_scraper/`'den hiçbir şey içe aktarmaz: okuyucular ve yazıcılar yeniden yazılırken fabrika aynı
baytları üretmeye devam etmelidir. Bugünkü yazıcılarla aynı çıktıyı verdiği
`tests/characterization/test_reader_goldens.py` içindeki "fabrika sadakati" testleriyle doğrulanır.

Belirlenimlilik:
  - her dosya ikili yazılır (JSON: `atomic_write_json` biçimi, girinti 2, son satır yok; CSV: `\\r\\n`),
    yani bayt sayıları her platformda aynıdır;
  - her dosyanın mtime'ı sabittir (`BASE_MTIME`, bilerek eskitilenler hariç);
  - gözlem ve işaret dosyalarındaki zamanlar `FIXTURE_NOW`'a göre seçilmiştir: yenileme kararını
    sınayan testler saati `FIXTURE_NOW`'a sabitlemelidir;
  - özet CSV'lerindeki `match_date`, bugünkü yazıcı gibi sürecin yerel saatiyle yazılır
    (`datetime.fromtimestamp`); altın dosyalarla karşılaştıran testler saat dilimini UTC'ye sabitler.

Dizinler (`FIXTURE_NAMES`):
  canonical       bugünkü yazıcıların ürettiği biçimler: L1, tur dosyaları (`_complete` ile), olay
                  sayfaları, özet JSON/CSV, `observation.json` / `_unavailable.json` /
                  `_slice_status.json`'ın sekiz birleşimi, `score_changes.jsonl`, izleyici dosyaları.
                  Bir sezon FETCH_ONLY_FINISHED=false ile yazılmıştır (özette bitmemiş maçlar var).
                  Okuyucuların birbiriyle çeliştiği hiçbir durum yok.
  legacy          eski biçimler ve çelişen durumlar: L2-L5, tek dosyalı kayıt, `_complete`'siz tur
                  dosyası, eski `_matches.csv` (iki konumda), dört sezon listesi adı,
                  `league_seasons.csv`, iki özet dosyasında geçen maç, iki sezon listesi olan lig,
                  iki yerde duran maç, bozuk dilim dosyası, `basic.json`'sız dizin.
  processed_only  özet dosyası olmayan dizin: maç listesi `match_details/processed/all_matches_*.csv`'den okunur.
  empty           boş dizin.
"""
from __future__ import annotations

import csv
import datetime as dt
import functools
import io
import json
import os
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

STATUS_DIR = Path(__file__).resolve().parent / "fixtures" / "status"

FIXTURE_NOW = 1790856000  # 2026-10-01T12:00:00Z: testlerin sabitlediği "şimdi"
BASE_MTIME = FIXTURE_NOW - 86400  # her dosyanın mtime'ı (aksi belirtilmedikçe)
DAY = 86400

# Spordan bağımsız `required` detay dilimleri, istek sırasıyla (sofascore_scraper/sports.py DETAIL_SLICES). Bir sporda
# tamlığa girip girmedikleri `optional_in` / `not_in`'e bağlıdır (FX-16: futbol, basketbol ve teniste pregame_form
# tamlığa girmez; tenis lineups ve incidents istemez)
REQUIRED_SLICES: Tuple[str, ...] = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")
# Spora özel detay dilimleri (tenis ve dart); teniste point_by_point FX-16'dan beri tamlığa girer
SPORT_SPECIFIC_SLICES: Tuple[str, ...] = ("point_by_point",)

SUMMARY_COLUMNS = ["round", "match_id", "home_team", "away_team", "home_score", "away_score",
                   "match_date", "status", "tournament", "season"]
# İlk sürümün tur başına yazdığı `round_<n>_matches.csv` sütunları (e42deb0, sofascore_scraper/match_fetcher.py)
OLD_ROUND_CSV_COLUMNS = ["match_id", "home_team", "away_team", "home_team_id", "away_team_id", "home_score",
                         "away_score", "round", "status", "status_type", "start_timestamp", "start_time", "slug"]
# İlk sürümün `league_seasons.csv` başlığı (e42deb0, sofascore_scraper/season_fetcher.py)
SEASONS_CSV_COLUMNS = ["Liga Adı", "Lig ID", "Sezon ID", "Sezon Adı", "Sezon Yılı"]

_META_KEYS = ("case_id", "event_id", "fetched_at_utc", "source_file")
_SPORTS = {"football": ("Football", 1), "basketball": ("Basketball", 2), "tennis": ("Tennis", 5)}
MARKER_AT = "2026-09-29T18:00:00+00:00"


# --- tanımlar ----------------------------------------------------------------------------


@dataclass(frozen=True)
class League:
    id: Optional[int]  # None: uniqueTournament'i olmayan olay (L5)
    name: str  # uniqueTournament.name ve config/leagues.txt'deki ad
    sport: Optional[str]  # slug; None: spor bilgisi olmayan olay
    category: str
    stage: Optional[str] = None  # tournament.name (özet CSV'sindeki `tournament`); yoksa `name`


@dataclass(frozen=True)
class Season:
    id: int
    name: str
    year: str


@dataclass(frozen=True)
class Ev:
    """Bir olay: `case` tests/fixtures/status altındaki yanıt, gerisi eklenen zarf."""

    case: str
    league: League
    season: Season
    home: str
    away: str
    round: Optional[int] = None
    eid: Optional[int] = None  # None: yanıtın kendi event_id'si
    shift: int = 0  # saniye: startTimestamp ve diğer zaman damgaları bu kadar kaydırılır


@dataclass(frozen=True)
class Detail:
    """`match_details` altındaki bir maç dizini."""

    ev: Ev
    form: str = "L1"  # L1 | L2 | L3 | L5 (L4: `combined` verilir)
    slices: Tuple[str, ...] = REQUIRED_SLICES  # verisi olan dilim dosyaları
    empty: Tuple[str, ...] = ()  # yazılan ama içinde veri olmayan dilim dosyaları
    corrupt: Tuple[str, ...] = ()  # yarım kalmış (ayrıştırılamayan) dilim dosyaları
    basic: bool = True  # False: basic.json yok
    basic_case: Optional[str] = None  # basic.json başka bir yanıttan (bayat kopya, sonradan iptal)
    combined: Optional[Tuple[str, ...]] = None  # L4: `<id>.json` içindeki dilimler ("basic" hep var)
    observed: Optional[str] = None  # observation.json: observed_at_utc
    observation: Optional[Dict[str, Any]] = None  # observation.json içeriği doğrudan (bozuk biçimler)
    regressed: bool = False  # observation.json: status_regressed
    unavailable: Optional[Dict[str, int]] = None  # _unavailable.json
    slice_status: Optional[Dict[str, Dict[str, Any]]] = None  # _slice_status.json
    mtime: int = BASE_MTIME


@dataclass(frozen=True)
class Listing:
    """`matches/<lig>/<sezon>/` altındaki bir tur dosyası ya da olay sayfası."""

    kind: str  # "round" | "page"
    label: Union[int, str]  # tur numarası ya da "last/0"
    events: Tuple[Ev, ...]
    slug: Optional[str] = None  # kupa turu: round_<n>_<slug>.json
    complete: Optional[bool] = None  # tur: `_complete` değeri; None: anahtar yok (eski, süzülmüş dosya)
    has_next: bool = False
    filtered: bool = True  # False: FETCH_ONLY_FINISHED=false ile yazıldı (sayfada ve özette her durum var)


@dataclass(frozen=True)
class DetailRecord:
    """Yazılan bir maç dizininin özeti (testler ve sonraki plan maddeleri için)."""

    event_id: int
    form: str  # "L1" | "L2" | "L3" | "L5"
    path: str  # veri dizinine göre, '/' ile
    has_basic: bool
    combined: bool  # L4: `<id>.json` var
    files: Tuple[str, ...]  # dizindeki dosya adları, sıralı
    league_id: Optional[int]
    sport: Optional[str]


@dataclass
class LegacyFixture:
    name: str
    data_dir: Path
    leagues: Dict[int, str]  # config/leagues.txt'de olması beklenen ligler
    details: List[DetailRecord] = field(default_factory=list)
    listed: Dict[Tuple[int, int], List[int]] = field(default_factory=dict)  # (lig, sezon) → listelenen olaylar
    summary_files: List[str] = field(default_factory=list)  # yazılan özet CSV'leri (göreli yol)

    @property
    def detail_ids(self) -> List[int]:
        return sorted({d.event_id for d in self.details})

    @property
    def listed_ids(self) -> List[int]:
        return sorted({eid for ids in self.listed.values() for eid in ids})

    @property
    def event_ids(self) -> List[int]:
        return sorted(set(self.detail_ids) | set(self.listed_ids))


# --- yazma ------------------------------------------------------------------------------


def dump_json(obj: Any) -> bytes:
    """`sofascore_scraper/config_files.atomic_write_json` (2.x: sofascore_scraper/fsutil.py) ile aynı baytlar."""
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def dump_csv(columns: Sequence[str], rows: Sequence[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def dump_jsonl(rows: Sequence[Dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


class _Writer:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def write(self, rel: str, data: bytes, mtime: int = BASE_MTIME) -> None:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (mtime, mtime))


# --- olay ve dilim yanıtları ------------------------------------------------------------------


def safe_name(name: str) -> str:
    """`sofascore_scraper/store/legacy.safe_name` ile aynı kural (2.x yazıcılarının kuralı)."""
    return str(name).replace(" ", "_").replace("/", "_").replace("\\", "_")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _team(name: str) -> Dict[str, Any]:
    return {"name": name, "slug": _slug(name), "id": 1000 + zlib.crc32(name.encode("utf-8")) % 9000}


def event_id(ev: Ev) -> int:
    return ev.eid if ev.eid is not None else int(ev.case.rsplit("__", 1)[1])


@functools.lru_cache(maxsize=None)
def _status_text(case: str) -> str:
    return (STATUS_DIR / f"{case}.json").read_text(encoding="utf-8")


def event_payload(ev: Ev, case: Optional[str] = None) -> Dict[str, Any]:
    """Liste yanıtındaki (tur dosyası, olay sayfası) olay nesnesi."""
    raw = json.loads(_status_text(case or ev.case))
    body = {k: v for k, v in raw.items() if k not in _META_KEYS}
    if ev.shift:
        body["startTimestamp"] += ev.shift
        if isinstance((body.get("changes") or {}).get("changeTimestamp"), int):
            body["changes"]["changeTimestamp"] += ev.shift
        for key, value in (body.get("time") or {}).items():
            if key.endswith("Timestamp") and isinstance(value, int):
                body["time"][key] = value + ev.shift

    league = ev.league
    category: Dict[str, Any] = {"name": league.category, "slug": _slug(league.category)}
    if league.sport:
        sport_name, sport_id = _SPORTS[league.sport]
        category["sport"] = {"name": sport_name, "slug": league.sport, "id": sport_id}
    stage = league.stage or league.name
    tournament: Dict[str, Any] = {"name": stage, "slug": _slug(stage), "category": category}
    if league.id is not None:
        tournament["uniqueTournament"] = {
            "name": league.name,
            "slug": _slug(league.name),
            "category": category,
            "id": league.id,
            "hasEventPlayerStatistics": league.sport == "football",
        }
    event: Dict[str, Any] = {
        "tournament": tournament,
        "season": {"name": ev.season.name, "year": ev.season.year, "id": ev.season.id},
    }
    if ev.round is not None:
        event["roundInfo"] = {"round": ev.round}
    event.update({k: v for k, v in body.items() if k != "startTimestamp"})
    event["homeTeam"] = _team(ev.home)
    event["awayTeam"] = _team(ev.away)
    event["id"] = event_id(ev)
    event["slug"] = f"{_slug(ev.home)}-{_slug(ev.away)}"
    event["startTimestamp"] = body["startTimestamp"]
    return event


def basic_payload(ev: Ev, case: Optional[str] = None) -> Dict[str, Any]:
    """`/event/{id}` yanıtı (basic.json): liste nesnesi + yalnızca maç sayfasında gelen alanlar."""
    event = event_payload(ev, case)
    if ev.league.sport == "football":
        event["venue"] = {"name": f"{ev.home} Stadium", "id": 100 + event["id"] % 900}
        event["referee"] = {"name": f"Referee {event['id'] % 50}", "id": 200 + event["id"] % 50}
    return event


def is_finished(event: Dict[str, Any]) -> bool:
    """Özete giren maç: `status.type == "finished"` (oynanıp biten ve hükmen / çekilme)."""
    return (event.get("status") or {}).get("type") == "finished"


def slice_payload(key: str, event: Dict[str, Any], empty: bool = False) -> Any:
    """Bir detay diliminin yanıtı; `empty`: dosya var ama içinde veri yok."""
    n = int(event["id"])
    sport = (((event.get("tournament") or {}).get("category") or {}).get("sport") or {}).get("slug")
    home, away = event["homeTeam"], event["awayTeam"]
    if key == "statistics":
        if empty:
            return {"statistics": []}
        items = {
            "football": [("Ball possession", "ballPossession", 40 + n % 21),
                         ("Total shots", "totalShotsOnGoal", 5 + n % 13)],
            "basketball": [("Free throws", "freeThrowsScored", 10 + n % 17), ("Rebounds", "rebounds", 30 + n % 19)],
            "tennis": [("Aces", "aces", n % 15), ("Double faults", "doubleFaults", n % 6)],
        }.get(sport or "", [("Possession", "possession", 50)])

        def group(scale: int) -> List[Dict[str, Any]]:
            return [{
                "groupName": "Match overview",
                "statisticsItems": [
                    {"name": name, "home": str(value // scale), "away": str((value + 3) // scale), "compareCode": 2,
                     "statisticsType": "positive", "valueType": "event", "homeValue": value // scale,
                     "awayValue": (value + 3) // scale, "renderType": 1, "key": stat_key}
                    for name, stat_key, value in items
                ],
            }]

        return {"statistics": [{"period": "ALL", "groups": group(1)}, {"period": "1ST", "groups": group(2)}]}
    if key == "team_streaks":
        if empty:
            return {"general": [], "head2head": []}
        return {
            "general": [
                {"name": "Wins", "value": str(2 + n % 4), "team": "home", "continued": True},
                {"name": "No losses", "value": str(3 + n % 5), "team": "away", "continued": n % 2 == 0},
            ],
            "head2head": [{"name": "More than 2.5 goals", "value": "4/5", "team": "both", "continued": True}],
        }
    if key == "pregame_form":
        if empty:
            return {}
        return {
            "homeTeam": {"avgRating": f"6.{n % 90:02d}", "position": 1 + n % 18, "value": str(10 + n % 30),
                         "form": ["W", "D", "W", "L", "W"]},
            "awayTeam": {"avgRating": f"6.{(n + 7) % 90:02d}", "position": 2 + n % 17, "value": str(8 + n % 25),
                         "form": ["L", "W", "D", "D", "W"]},
            "label": "Pts",
        }
    if key == "h2h":
        if empty:
            return {"teamDuel": None, "managerDuel": None}
        return {"teamDuel": {"homeWins": n % 5, "awayWins": n % 3, "draws": n % 4}, "managerDuel": None}
    if key == "lineups":
        if empty:
            return {"confirmed": False, "home": {"players": []}, "away": {"players": []}}

        def side(team: Dict[str, Any], formation: str) -> Dict[str, Any]:
            return {
                "players": [
                    {"player": {"name": f"{team['name']} Starter", "id": team["id"] * 10 + 1}, "shirtNumber": 1,
                     "position": "G", "substitute": False},
                    {"player": {"name": f"{team['name']} Substitute", "id": team["id"] * 10 + 2}, "shirtNumber": 12,
                     "position": "F", "substitute": True},
                ],
                "formation": formation,
            }

        return {"confirmed": True, "home": side(home, "4-3-3"), "away": side(away, "4-4-2")}
    if key == "incidents":
        if empty:
            return {"incidents": []}
        hs, as_ = event.get("homeScore") or {}, event.get("awayScore") or {}
        return {"incidents": [
            {"text": "FT", "homeScore": hs.get("display", 0), "awayScore": as_.get("display", 0), "isLive": False,
             "time": 90, "incidentType": "period"},
            {"player": {"name": f"{home['name']} Starter", "id": home["id"] * 10 + 1}, "isHome": True,
             "time": 10 + n % 70, "incidentClass": "regular", "incidentType": "goal"},
        ]}
    if key == "point_by_point":
        if empty:
            return {"pointByPoint": []}
        return {"pointByPoint": [{"set": 1, "games": [{"game": 1, "points": [
            {"homePoint": "15", "awayPoint": "0", "pointDescription": 0, "homePointType": 1, "awayPointType": 5},
            {"homePoint": "30", "awayPoint": "0", "pointDescription": 0, "homePointType": 1, "awayPointType": 5},
        ], "score": {"homeScore": 1, "awayScore": 0, "serving": 1, "scoring": 1}}]}]}
    raise KeyError(key)


def observation_payload(event: Dict[str, Any], observed_at_utc: str, regressed: bool = False) -> Dict[str, Any]:
    """`sofascore_scraper/status.observation_record` ile aynı biçim."""
    obs: Dict[str, Any] = {
        "observed_at_utc": observed_at_utc,
        "change_ts": (event.get("changes") or {}).get("changeTimestamp"),
    }
    if regressed:
        obs["status_regressed"] = True
    return obs


def empty_marker(count: int) -> Dict[str, Any]:
    """_slice_status.json: bu sürümün kesin yanıtla saydığı "yok"."""
    return {"empty": {"count": count, "at": MARKER_AT}}


def error_marker(reason: str, status: Optional[int], count: int = 1) -> Dict[str, Any]:
    """_slice_status.json: dilimin son başarısız isteği."""
    return {"error": {"reason": reason, "status": status, "at": MARKER_AT, "count": count}}


def summary_row(round_label: Union[int, str], event: Dict[str, Any]) -> Dict[str, Any]:
    """`MatchFetcher._save_season_summary`'nin yazdığı on sütun."""
    start = event.get("startTimestamp")
    return {
        "round": round_label,
        "match_id": event.get("id", ""),
        "home_team": event["homeTeam"]["name"],
        "away_team": event["awayTeam"]["name"],
        "home_score": (event.get("homeScore") or {}).get("current", 0),
        "away_score": (event.get("awayScore") or {}).get("current", 0),
        "match_date": dt.datetime.fromtimestamp(start).isoformat() if start else "",
        "status": (event.get("status") or {}).get("description", ""),
        "tournament": (event.get("tournament") or {}).get("name", ""),
        "season": (event.get("season") or {}).get("name", ""),
    }


def old_round_row(event: Dict[str, Any]) -> Dict[str, Any]:
    """İlk sürümün `round_<n>_matches.csv` satırı."""
    start = event.get("startTimestamp")
    return {
        "match_id": event.get("id"),
        "home_team": event["homeTeam"]["name"],
        "away_team": event["awayTeam"]["name"],
        "home_team_id": event["homeTeam"]["id"],
        "away_team_id": event["awayTeam"]["id"],
        "home_score": (event.get("homeScore") or {}).get("current"),
        "away_score": (event.get("awayScore") or {}).get("current"),
        "round": (event.get("roundInfo") or {}).get("round"),
        "status": (event.get("status") or {}).get("description"),
        "status_type": (event.get("status") or {}).get("type"),
        "start_timestamp": start,
        "start_time": dt.datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M:%S") if start else None,
        "slug": event.get("slug"),
    }


# --- dizin düzeni -----------------------------------------------------------------------


def league_dir(league: League) -> str:
    """`matches/` ve `match_details/` altındaki lig dizini adı: `<id>_<ad>`."""
    return f"{league.id}_{safe_name(league.name)}"


def season_dir(season: Season, name: Optional[str] = None) -> str:
    """`matches/<lig>/` altındaki sezon dizini ve özet dosyası öneki: `<id>_<ad>`."""
    return f"{season.id}_{safe_name(name or season.name)}"


def detail_dir(detail: Detail) -> str:
    """Maç dizininin `match_details/` altındaki yeri (01-storage.md 5.1, L1-L5)."""
    ev = detail.ev
    eid = event_id(ev)
    season = f"season_{safe_name(ev.season.name)}"
    if detail.form == "L1":
        return f"match_details/{league_dir(ev.league)}/{season}/{eid}"
    if detail.form == "L2":
        return f"match_details/{safe_name(ev.league.name)}/{season}/{eid}"
    if detail.form == "L3":
        return f"match_details/{eid}"
    if detail.form == "L5":
        return f"match_details/_no_tournament/{ev.league.sport or 'unknown'}/{eid}"
    raise ValueError(detail.form)


class _Builder:
    def __init__(self, name: str, root: Path, leagues: Sequence[League]) -> None:
        self.w = _Writer(root)
        self.fixture = LegacyFixture(name=name, data_dir=root, leagues={lg.id: lg.name for lg in leagues if lg.id})

    # -- match_details --

    def detail(self, detail: Detail) -> None:
        ev = detail.ev
        base = detail_dir(detail)
        basic = basic_payload(ev, detail.basic_case)
        files: Dict[str, bytes] = {}
        if detail.basic:
            files["basic.json"] = dump_json(basic)
        for key in detail.slices:
            files[f"{key}.json"] = dump_json(slice_payload(key, basic))
        for key in detail.empty:
            files[f"{key}.json"] = dump_json(slice_payload(key, basic, empty=True))
        for key in detail.corrupt:
            files[f"{key}.json"] = dump_json(slice_payload(key, basic))[:40]  # yarıda kesilmiş yazma
        if detail.combined is not None:
            combined: Dict[str, Any] = {"basic": basic}
            combined.update({key: slice_payload(key, basic) for key in detail.combined})
            files[f"{event_id(ev)}.json"] = dump_json(combined)
        if detail.observation is not None:
            files["observation.json"] = dump_json(detail.observation)
        elif detail.observed is not None:
            files["observation.json"] = dump_json(observation_payload(basic, detail.observed, detail.regressed))
        if detail.unavailable is not None:
            files["_unavailable.json"] = dump_json(detail.unavailable)
        if detail.slice_status is not None:
            files["_slice_status.json"] = dump_json(detail.slice_status)
        for name, data in files.items():
            self.w.write(f"{base}/{name}", data, detail.mtime)
        self.fixture.details.append(DetailRecord(
            event_id=event_id(ev), form=detail.form, path=base, has_basic=detail.basic,
            combined=detail.combined is not None, files=tuple(sorted(files)), league_id=ev.league.id,
            sport=ev.league.sport,
        ))

    # -- matches --

    def listing(self, league: League, season: Season, listing: Listing, season_name: Optional[str] = None) -> None:
        """Tur dosyası ya da olay sayfası; `season_name`: sezon dizini başka bir adla yazılmışsa."""
        base = f"matches/{league_dir(league)}/{season_dir(season, season_name)}"
        events = [event_payload(ev) for ev in listing.events]
        if listing.kind == "round":
            suffix = f"_{listing.slug}" if listing.slug else ""
            payload: Dict[str, Any] = {"events": events, "hasNextPage": False}
            if listing.complete is None:
                payload["events"] = [e for e in events if is_finished(e)]  # eski sürüm süzüp yazıyordu
            else:
                payload["_complete"] = listing.complete
            self.w.write(f"{base}/round_{listing.label}{suffix}.json", dump_json(payload))
        else:
            kind, page = str(listing.label).split("/")
            payload = {
                "events": [e for e in events if is_finished(e) or not listing.filtered],
                "hasNextPage": listing.has_next,
                "source": listing.label,
            }
            self.w.write(f"{base}/events_{kind}_{page}.json", dump_json(payload))

    def summary(
        self,
        league: League,
        season: Season,
        listings: Sequence[Listing],
        season_name: Optional[str] = None,
        suffix: str = "_summary",
        with_json: bool = True,
    ) -> None:
        """Sezon özeti (lig dizininde): `<sid>_<ad>_summary.json` + `.csv`; `suffix="_matches"`: eski ad."""
        results, rows = summary_of(listings)
        stem = f"matches/{league_dir(league)}/{season_dir(season, season_name)}{suffix}"
        if with_json:
            self.w.write(f"{stem}.json", dump_json(results))
        self.w.write(f"{stem}.csv", dump_csv(SUMMARY_COLUMNS, rows))
        self.fixture.summary_files.append(f"{stem}.csv")
        listed = self.fixture.listed.setdefault((league.id or 0, season.id), [])
        listed.extend(row["match_id"] for row in rows if row["match_id"] not in listed)

    # -- seasons --

    def seasons(self, file_name: str, seasons: Sequence[Season], mtime: int = BASE_MTIME) -> None:
        payload = {"seasons": [{"name": s.name, "year": s.year, "editor": False, "id": s.id} for s in seasons]}
        self.w.write(f"seasons/{file_name}", dump_json(payload), mtime)


def summary_of(listings: Sequence[Listing]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Listelerden sezon özeti: (summary.json içeriği, summary.csv satırları). Yalnızca bitmiş maçlar girer;
    FETCH_ONLY_FINISHED=false ile yazılmış listelerde (`filtered=False`) hepsi.
    """
    results: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    for listing in listings:
        events = [event_payload(ev) for ev in listing.events]
        finished = [e for e in events if is_finished(e) or not listing.filtered]
        if not finished:
            continue
        if listing.kind == "round":
            label: Union[int, str] = listing.label
            results.append({"events": finished, "hasNextPage": False, "round": label})
        else:
            label = str(listing.label).replace("/", "_")
            results.append(
                {"events": finished, "hasNextPage": listing.has_next, "source": listing.label, "round": label}
            )
        rows.extend(summary_row(label, e) for e in finished)
    return results, rows


# --- veri ---------------------------------------------------------------------------------

PL = League(17, "Premier League", "football", "England")
FA_CUP = League(19, "FA Cup", "football", "England")
LALIGA = League(8, "LaLiga", "football", "Spain")
BUNDESLIGA = League(35, "Bundesliga", "football", "Germany")
NBA = League(132, "NBA", "basketball", "USA")
WIMBLEDON = League(2361, "Wimbledon, Men", "tennis", "ATP", stage="Wimbledon, London, Great Britain")
FRIENDLY = League(None, "Club Friendly Games", "football", "World")
EXHIBITION = League(None, "Exhibition", "tennis", "Exhibition")
NO_SPORT = League(None, "Unknown", None, "Unknown")

PL_2627 = Season(96668, "Premier League 26/27", "26/27")
PL_2526 = Season(76986, "Premier League 25/26", "25/26")
PL_2425 = Season(61627, "Premier League 24/25", "24/25")
FA_2627 = Season(97110, "FA Cup 26/27", "26/27")
FA_2526 = Season(77210, "FA Cup 25/26", "25/26")
LALIGA_2627 = Season(97532, "LaLiga 26/27", "26/27")
LALIGA_2526 = Season(77559, "LaLiga 25/26", "25/26")
NBA_2627 = Season(80229, "NBA 26/27", "26/27")
NBA_2526 = Season(65360, "NBA 25/26", "25/26")
WIM_2026 = Season(79116, "Wimbledon Men Singles 2026", "2026")
WIM_2025 = Season(63966, "Wimbledon Men Singles 2025", "2025")
NO_SEASON = Season(90001, "Friendly Games 2026", "2026")


def _pl(case: str, home: str, away: str, rnd: int, **kw: Any) -> Ev:
    return Ev(f"football/{case}", PL, PL_2627, home, away, round=rnd, **kw)


# Premier League 26/27: işaret dosyalarının sekiz birleşimi bu maçlarda (aşağıda CANONICAL_DETAILS)
PL_ARS = _pl("A_finished-100-ended__16837335", "Arsenal", "Chelsea", 1)
PL_LIV = _pl("F9_changed_after_finish__17099711", "Liverpool", "Everton", 1)
PL_LEE = _pl("B13_start_time_changed__16867839", "Leeds United", "Fulham", 1)
PL_BRE = _pl("B3_live_first_period__17018554", "Brentford", "Burnley", 1)
PL_AVL = _pl("B3_live_first_period__17185003", "Aston Villa", "Wolverhampton", 2, shift=600)
PL_NEW = _pl("B5_live_last_period__17018572", "Newcastle United", "Sunderland", 2)
PL_TOT = _pl("B5_live_last_period__17184988", "Tottenham Hotspur", "West Ham United", 2)
PL_BHA = _pl("F6_cup_draw_aggregate__16872361", "Brighton & Hove Albion", "Bournemouth", 2)
PL_CRY = _pl("F6_cup_draw_aggregate__16951514", "Crystal Palace", "Nottingham Forest", 2)
PL_NO_DETAIL = _pl("B6_finished_regular__16837335", "Manchester City", "Hull City", 2, eid=16837399, shift=7 * DAY)
PL_NOT_STARTED = _pl("A_notstarted-0-not-started__17184998", "Manchester United", "Coventry City", 2)
PL_POSTPONED = _pl("A_postponed-60-postponed__16539815", "Ipswich Town", "Middlesbrough", 2)
PL_FUTURE = _pl("A_notstarted-0-not-started__17211671", "Chelsea", "Liverpool", 3)

# Premier League 25/26: bir önceki sezon (yanıtlar 114 gün geri kaydırıldı)
PL_OLD_A = Ev("football/A_finished-100-ended__17099711", PL, PL_2526, "Southampton", "Leicester City", round=38,
              eid=14025001, shift=-114 * DAY)
PL_OLD_B = Ev("football/B6_finished_regular__17099711", PL, PL_2526, "Watford", "Norwich City", round=38,
              eid=14025002, shift=-114 * DAY + 7200)

# FA Cup: penaltılarla biten maç (homeScore.current 10, display 3) ve uzatmada biten maç
CUP_PEN = Ev("football/A_finished-120-ap__16950622", FA_CUP, FA_2627, "Manchester City", "Manchester United", round=28)
CUP_PEN_2 = Ev("football/F2_penalties__17090707", FA_CUP, FA_2627, "Arsenal", "Tottenham Hotspur", round=28)
CUP_AET = Ev("football/A_finished-110-aet__17148332", FA_CUP, FA_2627, "Manchester City", "Arsenal", round=29)

# NBA: sezon programı olay sayfalarından (events/last/<n>)
NBA_A = Ev("basketball/A_finished-100-ended__16484334", NBA, NBA_2627, "Boston Celtics", "Denver Nuggets")
NBA_B = Ev("basketball/A_finished-100-ended__17092269", NBA, NBA_2627, "Miami Heat", "Chicago Bulls")
NBA_OT = Ev("basketball/A_finished-110-aet__16346148", NBA, NBA_2627, "Phoenix Suns", "Utah Jazz")
NBA_WO = Ev("basketball/A_finished-91-walkover__17102381", NBA, NBA_2627, "Dallas Mavericks", "Orlando Magic")
NBA_RECENT = Ev("basketball/K7_whistle_to_finished__17203939", NBA, NBA_2627, "Golden State Warriors", "Atlanta Hawks")
# Listede bitmiş görünen, maç sayfası sonradan "Abandoned" olan maç (kayıt silinmez: status_regressed)
NBA_VOID = Ev("basketball/K1_overtime_finished__17066086", NBA, NBA_2627, "Toronto Raptors", "Brooklyn Nets",
              eid=17060394)
NBA_VOID_BASIC = "basketball/A_canceled-90-abandoned__17060394"

# Wimbledon: tenis (kadro ve olay dilimi istenmez, pregame_form tamlığa girmez, point_by_point girer; FX-16)
WIM_A = Ev("tennis/T1_finished__17204710", WIMBLEDON, WIM_2026, "Carlos Alcaraz", "Jannik Sinner")
WIM_B = Ev("tennis/T1_finished__17206241", WIMBLEDON, WIM_2026, "Novak Djokovic", "Taylor Fritz")
WIM_RET = Ev("tennis/A_finished-92-retired__17081861", WIMBLEDON, WIM_2026, "Alexander Zverev", "Ben Shelton")
WIM_WO = Ev("tennis/A_finished-91-walkover__17058663", WIMBLEDON, WIM_2026, "Daniil Medvedev", "Holger Rune")
WIM_TB = Ev("tennis/T8_match_tiebreak__17078471", WIMBLEDON, WIM_2026, "Casper Ruud", "Alex de Minaur")
WIM_DOUBLES = Ev("tennis/T7_doubles__17207542", WIMBLEDON, WIM_2026, "Granollers M / Zeballos H",
                 "Heliovaara H / Patten H", shift=1200)
# Canlı maç: yalnızca izleyici dosyalarında geçer
WIM_LIVE = Ev("tennis/A_inprogress-10-3rd-set__17202152", WIMBLEDON, WIM_2026, "Lorenzo Musetti", "Tommy Paul")

# LaLiga 26/27: FETCH_ONLY_FINISHED=false ile indirilmiş sezon (sayfalarda ve özette bitmemiş maçlar da var)
LIGA_FIN = Ev("football/B6_finished_regular__17099711", LALIGA, LALIGA_2627, "Real Madrid", "Barcelona",
              eid=16990001, shift=3 * DAY)
LIGA_POSTPONED = Ev("football/A_postponed-60-postponed__16599919", LALIGA, LALIGA_2627, "Villarreal", "Osasuna")
LIGA_INTERRUPTED = Ev("football/A_interrupted-80-interrupted__17148292", LALIGA, LALIGA_2627, "Girona", "Getafe")
LIGA_NEXT = Ev("football/B1_future_7d__17211681", LALIGA, LALIGA_2627, "Athletic Club", "Real Sociedad")
LIGA_CANCELED = Ev("football/A_canceled-70-canceled__16425949", LALIGA, LALIGA_2627, "Celta Vigo", "Mallorca",
                   shift=3600)

# LaLiga 25/26: ID'siz lig dizini (L2) ve ilk sürümün tur CSV'leri
LIGA_A = Ev("football/A_finished-100-ended__16837335", LALIGA, LALIGA_2526, "Atlético Madrid", "Sevilla", round=1,
            eid=15000001, shift=-120 * DAY)
LIGA_B = Ev("football/F6_cup_draw_aggregate__16951514", LALIGA, LALIGA_2526, "Real Betis", "Valencia", round=1,
            eid=15000002, shift=-120 * DAY)

# uniqueTournament'i olmayan olaylar (L5)
FRIENDLY_A = Ev("football/A_finished-100-ended__17099711", FRIENDLY, NO_SEASON, "Ajax", "Celtic", eid=15500001,
                shift=13 * DAY)
EXHIBITION_A = Ev("tennis/T9_challenger_itf__17208186", EXHIBITION, NO_SEASON, "Player One", "Player Two",
                  eid=15500002)
NO_SPORT_A = Ev("football/A_finished-110-aet__17155990", NO_SPORT, NO_SEASON, "Home Side", "Away Side", eid=15500003)

_TENNIS_BASE = ("statistics", "team_streaks", "h2h", "point_by_point")
_TENNIS_ABSENT = {"pregame_form": 2, "lineups": 2, "incidents": 2}


def _without(*missing: str) -> Tuple[str, ...]:
    return tuple(k for k in REQUIRED_SLICES if k not in missing)


# (observation.json, _unavailable.json, _slice_status.json) birleşimleri satır sonlarında: 0 yok, 1 var
CANONICAL_DETAILS: Tuple[Detail, ...] = (
    # 100 kesin kayıt: gözlem, başlangıç + 72 saatten sonra
    Detail(PL_ARS, observed="2026-09-19T06:00:00+00:00"),
    # 100 geçici kayıt: gözlem pencere içinde, 6 saatten eski → yenilenir
    Detail(PL_LIV, observed="2026-09-15T13:10:00+00:00"),
    # 000 gözlemi olmayan eski kayıt: kesin sayılır (REFRESH_LEGACY ile yenilenir)
    Detail(PL_LEE),
    # 010 eski sürümün saydığı "yok" (kesin yanıtla doğrulanmamış): dilimler beklenmez
    Detail(PL_BRE, slices=_without("lineups", "incidents"), unavailable={"lineups": 2, "incidents": 2}),
    # 110 eşiğin altında "yok" sayımı: dilim hâlâ beklenir; gözlem taze (yenileme zamanı gelmedi)
    Detail(PL_AVL, slices=_without("lineups"), observed="2026-10-01T09:00:00+00:00", unavailable={"lineups": 1}),
    # 001 başarısız istek: sayılmaz, dilim beklenir
    Detail(PL_NEW, slices=_without("statistics"), slice_status={"statistics": error_marker("403", 403, 2)}),
    # 101 başarısız istek + yenilenecek gözlem: eksik dilim önce gelir
    Detail(PL_TOT, slices=_without("h2h"), observed="2026-09-29T16:00:00+00:00",
           slice_status={"h2h": error_marker("timeout", None)}),
    # 011 kesin yanıtla doğrulanmış "yok": yeniden denetim (varsayılan) dokunmaz
    Detail(PL_BHA, slices=_without("lineups", "incidents"), unavailable={"lineups": 2, "incidents": 2},
           slice_status={"lineups": empty_marker(2), "incidents": empty_marker(2)}),
    # 111 karışık: lineups 2 sayımın 1'i doğrulanmış, incidents eşiğin altında + hata, pregame_form doğrulanmamış
    Detail(PL_CRY, slices=_without("lineups", "incidents", "pregame_form"), observed="2026-09-17T06:00:00+00:00",
           unavailable={"lineups": 2, "incidents": 1, "pregame_form": 3},
           slice_status={"lineups": empty_marker(1), "incidents": {**empty_marker(1), **error_marker("5xx", 503)}}),
    # observed_at_utc'siz gözlem dosyası: eski kayıt gibi işlenir
    Detail(PL_OLD_A, observation={"change_ts": 1779598000}),
    Detail(CUP_PEN, observed="2026-09-20T12:00:00+00:00"),
    Detail(CUP_AET),
    Detail(NBA_A, observed="2026-09-18T00:00:00+00:00"),
    # dilim dosyası var ama içinde veri yok: "yok" sayılmış ve doğrulanmış
    Detail(NBA_B, slices=_without("lineups", "incidents"), empty=("lineups", "incidents"),
           observed="2026-09-19T00:00:00+00:00", unavailable={"lineups": 2, "incidents": 2},
           slice_status={"lineups": empty_marker(2), "incidents": empty_marker(2)}),
    # hükmen: yalnızca basic, bütün dilimler "yok"
    Detail(NBA_WO, slices=(), unavailable={k: 2 for k in REQUIRED_SLICES}),
    # geçici ama taze gözlem: yenileme zamanı gelmedi
    Detail(NBA_RECENT, observed="2026-10-01T08:00:00+00:00"),
    # oynanmış sayılırken iptal edilen maç: kayıt durur, gözlemde status_regressed
    Detail(NBA_VOID, slices=("statistics",), basic_case=NBA_VOID_BASIC, observed="2026-09-21T00:00:00+00:00",
           regressed=True),
    # tenis: kadro / olay / form yok (doğrulanmış), geçici → yenilenir
    Detail(WIM_A, slices=_TENNIS_BASE, observed="2026-09-29T04:30:00+00:00", unavailable=dict(_TENNIS_ABSENT),
           slice_status={k: empty_marker(2) for k in _TENNIS_ABSENT}),
    Detail(WIM_B, slices=("statistics", "h2h", "point_by_point"), unavailable=dict(_TENNIS_ABSENT)),
    Detail(WIM_RET, slices=(), observed="2026-09-19T08:00:00+00:00", unavailable={k: 2 for k in REQUIRED_SLICES}),
    Detail(WIM_TB, slices=("statistics", "h2h")),
    Detail(LIGA_FIN, observed="2026-09-22T04:00:00+00:00"),
    # başlamamış maçın detayı (FETCH_ONLY_FINISHED=false): maç öncesi dilimler var, işaret dosyası yazılmaz
    Detail(LIGA_NEXT, slices=("team_streaks", "pregame_form", "h2h")),
)

PL_ROUNDS: Tuple[Listing, ...] = (
    Listing("round", 1, (PL_ARS, PL_LIV, PL_LEE, PL_BRE), complete=True),
    Listing("round", 2, (PL_AVL, PL_NEW, PL_TOT, PL_BHA, PL_CRY, PL_NO_DETAIL, PL_NOT_STARTED, PL_POSTPONED),
            complete=False),
    Listing("round", 3, (PL_FUTURE,), complete=False),
)
PL_OLD_ROUNDS: Tuple[Listing, ...] = (Listing("round", 38, (PL_OLD_A, PL_OLD_B), complete=True),)
CUP_ROUNDS: Tuple[Listing, ...] = (
    Listing("round", 28, (CUP_PEN, CUP_PEN_2), slug="semifinals", complete=True),
    Listing("round", 29, (CUP_AET,), slug="final", complete=True),
)
NBA_PAGES: Tuple[Listing, ...] = (
    Listing("page", "last/0", (NBA_A, NBA_B, NBA_OT), has_next=True),
    Listing("page", "last/1", (NBA_WO, NBA_RECENT, NBA_VOID)),
)
WIM_PAGES: Tuple[Listing, ...] = (
    Listing("page", "last/0", (WIM_A, WIM_B, WIM_RET, WIM_WO, WIM_TB, WIM_DOUBLES)),
)
LIGA_PAGES: Tuple[Listing, ...] = (
    Listing("page", "last/0", (LIGA_FIN, LIGA_POSTPONED, LIGA_INTERRUPTED), filtered=False),
    Listing("page", "next/0", (LIGA_NEXT, LIGA_CANCELED), filtered=False),
)

# `sofascore_scraper/refresh.change_row` biçiminde iki satır: bitiş sonrası skor düzeltmesi ve sonradan iptal edilen maç
SCORE_CHANGES: Tuple[Dict[str, Any], ...] = (
    {
        "ts_utc": "2026-09-15T13:10:00+00:00",
        "event_id": 17099711,
        "sport": "football",
        "tournament": {"id": 17, "name": "Premier League"},
        "tier_hint": True,
        "start_ts": 1789444800,
        "hours_after_start": 9.15,
        "changed": {
            "awayScore.current": [0, 1],
            "awayScore.display": [0, 1],
            "awayScore.normaltime": [0, 1],
            "awayScore.period1": [0, 1],
        },
        "old_change_ts": 1789474674,
        "new_change_ts": 1789477727,
        "status_class": ["completed", "completed"],
    },
    {
        "ts_utc": "2026-09-21T00:00:00+00:00",
        "event_id": 17060394,
        "sport": "basketball",
        "tournament": {"id": 132, "name": "NBA"},
        "tier_hint": False,
        "start_ts": 1789599600,
        "hours_after_start": 8.3,
        "changed": {
            "awayScore.current": [70, 57],
            "awayScore.display": [70, 57],
            "awayScore.normaltime": [64, 57],
            "awayScore.overtime": [6, None],
            "awayScore.period1": [20, 23],
            "awayScore.period2": [18, 14],
            "awayScore.period3": [21, 20],
            "awayScore.period4": [5, 0],
            "homeScore.current": [78, 59],
            "homeScore.display": [78, 59],
            "homeScore.normaltime": [64, 59],
            "homeScore.overtime": [14, None],
            "homeScore.period1": [16, 19],
            "homeScore.period2": [19, 24],
            "homeScore.period3": [11, 16],
            "homeScore.period4": [18, 0],
            "startTimestamp": [1789504200, 1789599600],
            "status.code": [100, 90],
            "status.description": ["Ended", "Abandoned"],
            "status.type": ["finished", "canceled"],
            "winnerCode": [1, None],
        },
        "old_change_ts": 1789512242,
        "new_change_ts": 1789629463,
        "status_class": ["completed", "void"],
        "status_regressed": True,
    },
)

# İzleyici dosyaları (`sofascore_scraper/watcher.py`): canlı → gol → bitti (futbol) ve süren bir tenis maçı
WATCH_EVENTS: Tuple[Dict[str, Any], ...] = (
    {
        "type": "score_changed",
        "event_id": 17018572,
        "at_utc": "2026-09-29T13:54:20+00:00",
        "from": [1, 0],
        "to": [2, 0],
        "change_ts": 1790687855,
        "source": "live",
    },
    {
        "type": "status_changed",
        "event_id": 17018572,
        "from": "live",
        "to": "completed",
        "at_utc": "2026-09-29T14:04:20+00:00",
        "change_ts": 1790690140,
        "source": "event",
        "scores": {
            "sport": "football",
            "status_class": "completed",
            "winner_code": 1,
            "raw_change_ts": 1790690140,
            "raw_changed_fields": ["status.code", "status.description", "status.type"],
            "ht": [0, 0],
            "ft90": [1, 0],
            "aet": None,
            "penalties": None,
            "aggregated": None,
            "aggregated_winner_code": None,
        },
        "provisional": True,
    },
)
WATCH_STATE: Dict[str, Dict[str, Dict[str, Any]]] = {
    "football": {
        "17018572": {
            "class": "completed",
            "done": True,
            "start_ts": 1790683200,
            "tournament_id": 17,
            "score": [1, 0],
            "near_end": False,
            "stuck": False,
            "completed_emitted": True,
        },
    },
    "tennis": {
        "17202152": {
            "class": "live",
            "done": False,
            "start_ts": 1790684100,
            "tournament_id": 2361,
            "score": [1, 1],
            "near_end": True,
            "play_start": 1790683285,
            "stuck": False,
        },
    },
}


# --- dizinler -----------------------------------------------------------------------------


def _build_canonical(root: Path) -> LegacyFixture:
    b = _Builder("canonical", root, (PL, FA_CUP, NBA, WIMBLEDON, LALIGA, BUNDESLIGA))  # Bundesliga: verisi yok
    for league, season, listings in (
        (PL, PL_2627, PL_ROUNDS),
        (PL, PL_2526, PL_OLD_ROUNDS),
        (FA_CUP, FA_2627, CUP_ROUNDS),
        (NBA, NBA_2627, NBA_PAGES),
        (WIMBLEDON, WIM_2026, WIM_PAGES),
        (LALIGA, LALIGA_2627, LIGA_PAGES),
    ):
        for listing in listings:
            b.listing(league, season, listing)
        b.summary(league, season, listings)
    for detail in CANONICAL_DETAILS:
        b.detail(detail)
    b.seasons("17_Premier_League_seasons.json", (PL_2627, PL_2526, PL_2425))
    b.seasons("19_FA_Cup_seasons.json", (FA_2627, FA_2526))
    b.seasons("132_NBA_seasons.json", (NBA_2627, NBA_2526))
    b.seasons("2361_Wimbledon,_Men_seasons.json", (WIM_2026, WIM_2025))
    b.seasons("8_LaLiga_seasons.json", (LALIGA_2627, LALIGA_2526))
    b.w.write("score_changes.jsonl", dump_jsonl(SCORE_CHANGES))
    b.w.write("watch_events.jsonl", dump_jsonl(WATCH_EVENTS))
    for sport, state in WATCH_STATE.items():
        b.w.write(f"watch_state_{sport}.json", dump_json(state))
    return b.fixture


def _build_legacy(root: Path) -> LegacyFixture:
    b = _Builder("legacy", root, (PL, LALIGA, WIMBLEDON))

    # Premier League 26/27: bir tur dosyası eski biçimde (`_complete` yok, yalnızca bitmiş maçlar)
    rounds = (
        Listing("round", 1, (PL_ARS, PL_LIV)),
        Listing("round", 2, (PL_LEE, PL_BRE, PL_AVL, PL_NEW), complete=True),
    )
    for listing in rounds:
        b.listing(PL, PL_2627, listing)
    b.summary(PL, PL_2627, rounds)
    # Aynı sezonun ikinci özeti: sezon listesi yokken `Season_<id>` adıyla yazılmış (aynı iki maç)
    stale = (Listing("round", 1, (PL_ARS, PL_LIV), complete=True),)
    b.listing(PL, PL_2627, stale[0], season_name="Season_96668")
    b.summary(PL, PL_2627, stale, season_name="Season_96668")
    # Eski adla sezon özeti: yalnızca `<sid>_<ad>_matches.csv`
    b.summary(PL, PL_2526, PL_OLD_ROUNDS, suffix="_matches", with_json=False)

    # LaLiga 25/26: ilk sürümün tur başına dosyaları + sonradan yazılmış özet (iki maç iki dosyada)
    liga_base = f"matches/{league_dir(LALIGA)}/{season_dir(LALIGA_2526)}"
    liga_events = [event_payload(LIGA_A), event_payload(LIGA_B)]
    b.w.write(f"{liga_base}/round_1_full.json", dump_json({"events": liga_events}))
    old_rows = [old_round_row(e) for e in liga_events]
    b.w.write(f"{liga_base}/round_1_matches.csv", dump_csv(OLD_ROUND_CSV_COLUMNS, old_rows))
    b.summary(LALIGA, LALIGA_2526, (Listing("round", 1, (LIGA_A, LIGA_B), complete=True),))

    for detail in (
        Detail(PL_ARS, observed="2026-09-19T06:00:00+00:00"),
        # aynı maçın düz dizinde kalmış bayat kopyası (iki yerde duran maç; basic.json'ı daha eski)
        Detail(PL_ARS, form="L3", slices=(), basic_case="football/A_inprogress-7-2nd-half__17018572",
               mtime=BASE_MTIME - 10 * DAY),
        # L4: basic.json'ın yanında bütün dilimleri tutan tek dosya; okuyucu yalnızca onu okur (gözlem dahil değil)
        Detail(PL_LIV, slices=(), combined=REQUIRED_SLICES, observed="2026-09-15T13:10:00+00:00"),
        # L3: düz dizin
        Detail(PL_LEE, form="L3"),
        # L3 + L4: yalnızca tek dosya, basic.json yok (bugünkü okuyucular bulamaz)
        Detail(PL_BRE, form="L3", slices=(), basic=False, combined=REQUIRED_SLICES),
        # yarıda kesilmiş dilim dosyası
        Detail(PL_AVL, slices=_without("statistics"), corrupt=("statistics",)),
        # basic.json'sız dizin (yarım kalmış yazma)
        Detail(PL_NEW, slices=("statistics",), basic=False),
        # L2: ID'siz lig dizini
        Detail(LIGA_A, form="L2", observed="2026-05-24T18:00:00+00:00"),
        # L5: uniqueTournament'siz olaylar
        Detail(FRIENDLY_A, form="L5", observed="2026-09-28T07:00:00+00:00"),
        Detail(EXHIBITION_A, form="L5", slices=_TENNIS_BASE, unavailable=dict(_TENNIS_ABSENT)),
        Detail(NO_SPORT_A, form="L5", slices=()),
    ):
        b.detail(detail)

    # Sezon listeleri: dört dosya adı; Premier League'in iki dosyası var (ID'li ad daha yeni ve daha uzun)
    b.seasons("17_seasons.json", (PL_2526, PL_2425), mtime=BASE_MTIME - 30 * DAY)
    b.seasons("17_Premier_League_seasons.json", (PL_2627, PL_2526, PL_2425))
    b.seasons("LaLiga_seasons.json", (LALIGA_2627, LALIGA_2526))
    b.seasons("2361_Wimbledon, Men_seasons.json", (WIM_2026,))
    b.w.write("league_seasons.csv", dump_csv(SEASONS_CSV_COLUMNS, [
        {"Liga Adı": lg.name, "Lig ID": lg.id, "Sezon ID": s.id, "Sezon Adı": s.name, "Sezon Yılı": s.year}
        for lg, seasons in ((PL, (PL_2526, PL_2425)), (LALIGA, (LALIGA_2526,)))
        for s in seasons
    ]))

    # Terminal arayüzünden kalanlar ve Store'un tanımadığı üst dizin
    b.w.write("datasets/all_matches.csv", b"match_id,home_team\n16837335,Arsenal\n")
    b.w.write("reports/system_report_20260901_120000.json", dump_json({"leagues": 2}))
    b.w.write("finish_lag/samples.jsonl", dump_jsonl([{"event_id": 16837335, "lag_seconds": 5400}]))
    return b.fixture


# `match_details/processed/all_matches_<ts>.csv`: dışa aktarma CSV'sinin sütunlarından bir alt küme
PROCESSED_COLUMNS = ["match_id", "league_folder", "season_folder", "tournament_name", "season_name", "round",
                     "home_team_name", "away_team_name", "home_score_ft", "away_score_ft", "match_date",
                     "away_score_ht", "home_score_ht", "season_id", "status", "tournament_id"]
PROCESSED_CSV_NAME = "all_matches_1790000000.csv"


def _processed_row(ev: Ev, with_folders: bool = True) -> Dict[str, Any]:
    event = event_payload(ev)
    hs, as_ = event.get("homeScore") or {}, event.get("awayScore") or {}
    row: Dict[str, Any] = {
        "match_id": event["id"],
        "tournament_name": ev.league.name,
        "season_name": ev.season.name,
        "round": ev.round,
        "home_team_name": ev.home,
        "away_team_name": ev.away,
        "home_score_ft": hs.get("normaltime"),
        "away_score_ft": as_.get("normaltime"),
        "match_date": event["startTimestamp"],  # Unix saniye
        "away_score_ht": as_.get("period1"),
        "home_score_ht": hs.get("period1"),
        "season_id": ev.season.id,
        "status": event["status"]["description"],
        "tournament_id": ev.league.id,
    }
    if with_folders:
        row["league_folder"] = league_dir(ev.league)
        row["season_folder"] = safe_name(ev.season.name)
    return row


def _build_processed_only(root: Path) -> LegacyFixture:
    b = _Builder("processed_only", root, (PL, LALIGA))
    b.detail(Detail(PL_ARS, observed="2026-09-19T06:00:00+00:00"))
    b.detail(Detail(PL_LIV, slices=()))
    rows = [_processed_row(PL_ARS), _processed_row(PL_LIV), _processed_row(PL_NO_DETAIL),
            _processed_row(LIGA_A, with_folders=False)]  # düz dizinden aktarılmış satır: klasör sütunları boş
    b.w.write(f"match_details/processed/{PROCESSED_CSV_NAME}", dump_csv(PROCESSED_COLUMNS, rows))
    for row in rows:  # özet dosyası yok: "listelenen" maçlar dışa aktarma CSV'sindekiler
        b.fixture.listed.setdefault((row["tournament_id"], row["season_id"]), []).append(row["match_id"])
    return b.fixture


def _build_empty(root: Path) -> LegacyFixture:
    return _Builder("empty", root, (PL,)).fixture


_BUILDERS: Dict[str, Callable[[Path], LegacyFixture]] = {
    "canonical": _build_canonical,
    "legacy": _build_legacy,
    "processed_only": _build_processed_only,
    "empty": _build_empty,
}
FIXTURE_NAMES: Tuple[str, ...] = tuple(_BUILDERS)


def build_fixture(name: str, root: Union[str, Path]) -> LegacyFixture:
    """`root` altına (yoksa oluşturulur, boş olmalı) adı verilen veri dizinini kurar."""
    return _BUILDERS[name](Path(root))
