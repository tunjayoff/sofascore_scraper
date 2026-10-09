"""
Normalleştirilmiş şema v1'in modelleri (docs/design/04-schema-v1.md; docs/design/00-platform.md bölüm 3).

Dışarıya verilen sözleşme budur: API v1, dışa aktarma, `ssc events` ve sink'ler bu kayıtları yazar. Ham
SofaScore yükü ayrı durur ve istenince olduğu gibi verilir.

Kurallar (belgenin 2. bölümü):
  * Her alan her kayıtta vardır; değeri bilinmiyorsa `null` olur, boş metin olmaz. Listeler `null` olmaz.
  * Kimlikler SofaScore'un tam sayılarıdır. `_utc` ile biten alanlar ISO 8601, UTC, `Z` sonekli metindir.
    `change_ts` SofaScore'un verdiği epoch saniyedir ve olduğu gibi taşınır.
  * Sayım değerleri küçük harf ve alt çizgiyle yazılır.

Modeller değişmezdir (frozen dataclass) ve yalnızca JSON'a çevrilebilir değerler tutar; `to_dict()` alan
sırasıyla düz sözlük verir. Alanların açıklaması, birimi ve SofaScore yükündeki kaynağı alanın `metadata`sında
durur: JSON Schema (sofascore_scraper/schema/jsonschema.py) ve belgedeki alan tabloları oradan üretilir, böylece üçü
birbirinden ayrılamaz (tests/test_schema_v1.py).

Açıklama metinleri sözleşmenin parçasıdır ve İngilizcedir (JSON Schema'ya `description` olarak girer).

Bu modül saftır: yalnızca standart kitaplığı ve sofascore_scraper.sports'u içe aktarır.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Literal, Mapping, Optional, Tuple, Union

from sofascore_scraper.sports import PeriodFormat, ScoreFamily, SetFormat

# Sürüm: alan eklemek serbesttir; alan silmek, yeniden adlandırmak, tipini, birimini ya da anlamını
# değiştirmek ve kapalı bir sayıma değer eklemek sürümü artırır (belge, bölüm 3).
SCHEMA_VERSION = 1
SCHEMA_ID = f"sofascore.data/{SCHEMA_VERSION}"
# Akış olaylarının zarfı (docs/design/02-services.md 5.1); `LiveEvent` bu zarfın modelidir.
EVENT_ENVELOPE_ID = "sofascore.event/1"

StatusClassName = Literal["not_started", "live", "completed", "decided_without_play", "void", "unknown"]
Side = Literal["home", "away", "draw"]
ParticipantType = Literal["team", "player", "pair", "other"]
PeriodsFormat = PeriodFormat  # sofascore_scraper/sports.py: "quarters", "halves", "thirds"
SetsFormat = SetFormat  # sofascore_scraper/sports.py: "games", "points", "frames", "legs", "legs_won", "games_won"
Settlement = Literal["open", "provisional", "final"]
RecordSource = Literal["event", "listing"]
SliceState = Literal["ok", "empty", "error", "not_requested"]


def spec(doc: str, *, source: str = "", unit: str = "", fmt: str = "", name: str = "", open_enum: bool = False,
         known: Tuple[str, ...] = ()) -> Any:
    """
    Bir model alanının sözleşme bilgisi.

    doc: alanın anlamı (İngilizce; JSON Schema `description`).
    source: değerin SofaScore yükündeki yolu ya da nasıl türetildiği.
    unit: birim (ör. "epoch seconds", "seconds").
    fmt: JSON Schema `format` (ör. "date-time").
    name: JSON'daki ad, Python adından farklıysa (`class` bir anahtar sözcük olduğu için).
    open_enum: sayım açık: yeni değerler sürüm artırmadan eklenebilir, tüketici bilmediği değeri hoş görmelidir.
    known: tipi düz metin olan açık bir sayımın bugün bilinen değerleri.
    """
    return field(metadata={"doc": doc, "source": source, "unit": unit, "format": fmt, "name": name,
                           "open": open_enum, "known": tuple(known)})


def json_name(item: "dataclasses.Field[Any]") -> str:
    """Alanın JSON'daki adı."""
    return str(item.metadata.get("name") or item.name)


def _plain(value: Any) -> Any:
    if isinstance(value, Model):
        return value.to_dict()
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class Model:
    """Bütün modellerin tabanı. SUMMARY: modelin sözleşmedeki tek cümlelik tanımı (İngilizce)."""

    SUMMARY: ClassVar[str] = ""

    def to_dict(self) -> Dict[str, Any]:
        """Kaydın JSON'a yazılacak hali: alan sırasıyla, iç içe modeller sözlük, demetler liste olarak."""
        return {json_name(item): _plain(getattr(self, item.name)) for item in dataclasses.fields(self)}


# --- varlıklar ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sport(Model):
    """Spor."""

    SUMMARY: ClassVar[str] = "A sport."

    slug: str = spec("SofaScore's slug of the sport, lower case. The key of a sport everywhere in the schema.",
                     source="`tournament.category.sport.slug`")
    name: Optional[str] = spec("English name of the sport.", source="`tournament.category.sport.name`")
    id: Optional[int] = spec("SofaScore's numeric id of the sport; null when no stored payload has shown it.",
                             source="`tournament.category.sport.id`")
    score_family: Optional[ScoreFamily] = spec(
        "Which score structure events of this sport carry (see Score). Null for a sport the platform has no "
        "score mapping for.", source="sport registry (`sofascore_scraper/sports.py`)", open_enum=True)


@dataclass(frozen=True)
class Category(Model):
    """Kategori: ülke, bölge ya da tur."""

    SUMMARY: ClassVar[str] = "A country or region, or a tour such as ATP, that groups tournaments."

    id: int = spec("SofaScore's category id.", source="`tournament.category.id`")
    sport: Optional[str] = spec("Slug of the sport the category belongs to.",
                                source="`tournament.category.sport.slug`")
    name: Optional[str] = spec("Name of the category, in English.", source="`tournament.category.name`")
    slug: Optional[str] = spec("SofaScore's slug of the category.", source="`tournament.category.slug`")
    country_code: Optional[str] = spec(
        "SofaScore's two-letter code of the category's country, as given (mostly ISO 3166-1 alpha-2; SofaScore "
        "uses `EN` for England). Null for a category that is not a country, such as ATP.",
        source="`tournament.category.alpha2`")


@dataclass(frozen=True)
class Tournament(Model):
    """Turnuva (benzersiz turnuva)."""

    SUMMARY: ClassVar[str] = "A competition: SofaScore's unique tournament."

    id: int = spec("SofaScore's unique-tournament id.", source="`tournament.uniqueTournament.id`")
    sport: Optional[str] = spec(
        "Slug of the sport.",
        source="`tournament.uniqueTournament.category.sport.slug`, else `tournament.category.sport.slug`")
    category_id: Optional[int] = spec(
        "Id of the tournament's Category.",
        source="`tournament.uniqueTournament.category.id`, else `tournament.category.id`")
    name: Optional[str] = spec("Name of the tournament, in English.", source="`tournament.uniqueTournament.name`")
    slug: Optional[str] = spec("SofaScore's slug of the tournament.", source="`tournament.uniqueTournament.slug`")


@dataclass(frozen=True)
class Season(Model):
    """Sezon."""

    SUMMARY: ClassVar[str] = "One edition of a tournament."

    id: int = spec("SofaScore's season id.", source="`season.id`; `seasons[].id` of the season list")
    tournament_id: int = spec(
        "Id of the Tournament the season belongs to.",
        source="`tournament.uniqueTournament.id` of the event that names the season, or the tournament whose "
               "season list holds it")
    name: Optional[str] = spec("Name of the season, for example `Premier League 26/27`.",
                               source="`season.name`; `seasons[].name`")
    year: Optional[str] = spec("The season's year text as SofaScore writes it: `26/27`, `2025`.",
                               source="`season.year`; `seasons[].year`")


@dataclass(frozen=True)
class Participant(Model):
    """Yarışmacı: takım, tek oyuncu ya da çift."""

    SUMMARY: ClassVar[str] = "A competitor of an event: a team, a single player or a pair."

    id: int = spec(
        "SofaScore's id of the competitor (the id space of `homeTeam` / `awayTeam`: teams, single players and "
        "pairs share it; it is not the person id of a squad player).", source="`homeTeam.id`, `awayTeam.id`")
    sport: Optional[str] = spec("Slug of the sport.",
                                source="`homeTeam.sport.slug`, else the sport of the event")
    type: Optional[ParticipantType] = spec(
        "What the competitor is: `team`, `player` (one person, as in tennis singles), `pair` (two persons, as in "
        "tennis doubles) or `other` (a type code the platform does not know). Null when SofaScore gave no type.",
        source="`homeTeam.type`: 0 is `team`, 1 is `player`, 2 is `pair`")
    name: Optional[str] = spec("Name, in English.", source="`homeTeam.name`")
    short_name: Optional[str] = spec("Short name.", source="`homeTeam.shortName`")
    slug: Optional[str] = spec("SofaScore's slug.", source="`homeTeam.slug`")
    name_code: Optional[str] = spec("Three-letter code, for example `ARS`.", source="`homeTeam.nameCode`")
    country_code: Optional[str] = spec("SofaScore's two-letter country code, as given.",
                                       source="`homeTeam.country.alpha2`")
    gender: Optional[str] = spec("Gender as SofaScore gives it.", source="`homeTeam.gender`",
                                 open_enum=True, known=("M", "F"))
    national: Optional[bool] = spec("True for a national team.", source="`homeTeam.national`")


# --- maç (Event) --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Status(Model):
    """Maçın durumu."""

    SUMMARY: ClassVar[str] = "The status of an event: SofaScore's triple and the platform's class."

    type: Optional[str] = spec(
        "SofaScore's status type.", source="`status.type`", open_enum=True,
        known=("notstarted", "inprogress", "finished", "postponed", "canceled", "interrupted", "suspended",
               "willcontinue"))
    code: Optional[int] = spec("SofaScore's status code, for example 100 (ended), 110 (after extra time), "
                               "120 (after penalties), 91 (walkover), 92 (retired).", source="`status.code`")
    description: Optional[str] = spec("SofaScore's status text, in English, for example `Ended`, `2nd half`.",
                                      source="`status.description`")
    class_: StatusClassName = spec(
        "The platform's class of the status. `not_started`: not begun. `live`: in progress, breaks included (half "
        "time, the night between two days of a cricket match: type `willcontinue`). `completed`: played "
        "and finished. `decided_without_play`: finished by walkover or retirement. `void`: postponed, cancelled, "
        "interrupted, suspended or abandoned. `unknown`: none of these; never silently treated as completed.",
        source="derived from `status.type`, then `status.code`, then `status.description` (`sofascore_scraper/status.py`)",
        name="class")


@dataclass(frozen=True)
class ScorePair(Model):
    """İki tarafın skoru."""

    SUMMARY: ClassVar[str] = "A score of both sides."

    home: Optional[int] = spec("Value of the home side.", source="`homeScore.<key>`")
    away: Optional[int] = spec("Value of the away side.", source="`awayScore.<key>`")


@dataclass(frozen=True)
class PlainScore(Model):
    """Skor ailesi olmayan sporun skoru: yalnızca başlık skoru."""

    SUMMARY: ClassVar[str] = "The score of an event whose sport has no score family: only the headline score."

    family: None = spec("Always null: the sport has no score mapping.", source="sport registry")
    home: Optional[int] = spec("Headline score of the home side, as SofaScore displays it.",
                               source="`homeScore.display`, else `homeScore.current`")
    away: Optional[int] = spec("Headline score of the away side, as SofaScore displays it.",
                               source="`awayScore.display`, else `awayScore.current`")


@dataclass(frozen=True)
class FootballScore(Model):
    """Futbol ailesi skoru."""

    SUMMARY: ClassVar[str] = "Score family `football`: goals by stage of the match."

    family: Literal["football"] = spec("Always `football`.", source="sport registry")
    home: Optional[int] = spec(
        "Headline score of the home side: goals including extra time, without the penalty shoot-out.",
        unit="goals", source="`homeScore.display`, else `homeScore.current`")
    away: Optional[int] = spec(
        "Headline score of the away side: goals including extra time, without the penalty shoot-out.",
        unit="goals", source="`awayScore.display`, else `awayScore.current`")
    half_time: Optional[ScorePair] = spec("Goals in the first half.", unit="goals", source="`period1`")
    regulation: Optional[ScorePair] = spec("Goals after 90 minutes.", unit="goals", source="`normaltime`")
    after_extra_time: Optional[ScorePair] = spec(
        "Goals after extra time (cumulative, without the shoot-out). Null unless the match went to extra time.",
        unit="goals",
        source="`display`, only when `status.code` is 110, or 120 when SofaScore sends `overtime`, `extra1` or "
               "`extra2`")
    penalties: Optional[ScorePair] = spec("Goals of the penalty shoot-out alone.", unit="goals",
                                          source="`penalties`")


@dataclass(frozen=True)
class PeriodScore(Model):
    """Bir periyodun skoru."""

    SUMMARY: ClassVar[str] = "Points of one period."

    number: int = spec(
        "Position of the period within the format, starting at 1: quarter 1 to 4, half 1 to 2, or period 1 to 3.",
        source="`periodN`: N, except for basketball `halves`, where `period2` is 1 and `period4` is 2")
    home: Optional[int] = spec("Points of the home side in the period (goals in the goal sports).", unit="points",
                               source="`homeScore.periodN`")
    away: Optional[int] = spec("Points of the away side in the period (goals in the goal sports).", unit="points",
                               source="`awayScore.periodN`")


@dataclass(frozen=True)
class PeriodsScore(Model):
    """
    Periyot ailesi skoru: basketbol, Amerikan futbolu, Avustralya futbolu, ragbi (sayı) ile buz hokeyi, hentbol,
    futsal, minifutbol ve floorball (gol). Alan tabloları belgeyle bağlı olduğundan birim `points` kalır (SP-1);
    gol sporlarında sayılanın gol olduğunu metinler söyler (FX-21).
    """

    SUMMARY: ClassVar[str] = ("Score family `periods`: points by period (basketball, American football, Aussie rules, "
                              "ice hockey, handball, rugby, futsal, minifootball, floorball).")

    family: Literal["periods"] = spec("Always `periods`.", source="sport registry")
    home: Optional[int] = spec(
        "Headline score of the home side: points including overtime; goals in the goal sports (ice hockey, "
        "handball, futsal, minifootball, floorball).", unit="points",
        source="`homeScore.display`, else `homeScore.current`")
    away: Optional[int] = spec(
        "Headline score of the away side: points including overtime; goals in the goal sports (ice hockey, "
        "handball, futsal, minifootball, floorball).", unit="points",
        source="`awayScore.display`, else `awayScore.current`")
    format: Optional[PeriodsFormat] = spec(
        "How regulation time is divided: `quarters` (basketball, American football, Aussie rules), `halves` "
        "(basketball, handball, rugby, futsal, minifootball) or `thirds` (ice hockey, floorball). Null while no "
        "period score exists.",
        source="sport registry (`sofascore_scraper/sports.py`); basketball: `quarters` when `period1` or `period3` is present, "
               "`halves` when only `period2` / `period4` are",
        open_enum=True)
    periods: Tuple[PeriodScore, ...] = spec(
        "Points (goals in the goal sports) of each period of regulation time that has a score, in order.",
        source="`period1` to `period4`")
    regulation: Optional[ScorePair] = spec("Points (goals in the goal sports) at the end of regulation time.",
                                           unit="points", source="`normaltime`")
    overtime: Optional[ScorePair] = spec(
        "Points (goals in the goal sports) scored in overtime alone. Null without overtime.",
        unit="points", source="`overtime`")
    final: Optional[ScorePair] = spec("Final points (goals in the goal sports) including overtime.", unit="points",
                                      source="`current`")
    penalties: Optional[ScorePair] = spec(
        "Goals of the penalty shoot-out alone. Null without a shoot-out, and always null for basketball. In the "
        "one recorded handball shoot-out, `final` and the headline score include these goals.",
        unit="goals", source="`penalties`")


@dataclass(frozen=True)
class SetScore(Model):
    """Bir set."""

    SUMMARY: ClassVar[str] = "One set."

    number: int = spec("Number of the set, starting at 1.", source="`periodN`")
    home: Optional[int] = spec(
        "What the home side won in the set, as `SetsScore.format` says: games in tennis and padel (points when "
        "the set is a match tie-break), points in volleyball, badminton and table tennis, legs in darts played "
        "in sets. In e-sports (`games_won`) the set is a game (map) and this is its score as SofaScore gives it: "
        "rounds in titles played in rounds (CS2), else 1 for the side that won the game and 0 for the other.",
        unit="games", source="`homeScore.periodN`")
    away: Optional[int] = spec(
        "What the away side won in the set, as `SetsScore.format` says: games in tennis and padel (points when "
        "the set is a match tie-break), points in volleyball, badminton and table tennis, legs in darts played "
        "in sets. In e-sports (`games_won`) the set is a game (map) and this is its score as SofaScore gives it: "
        "rounds in titles played in rounds (CS2), else 1 for the side that won the game and 0 for the other.",
        unit="games", source="`awayScore.periodN`")
    tiebreak: Optional[ScorePair] = spec(
        "Points of the set's tie-break. Null when the set had none, and always null outside tennis and padel.",
        unit="points", source="`periodNTieBreak`")


@dataclass(frozen=True)
class SetsScore(Model):
    """
    Set ailesi skoru: tenis, padel (oyun), voleybol, badminton, masa tenisi (sayı), dart (set usulünde leg),
    yalnızca bir sayı veren snooker (frame) ve setsiz ya da tek setlik dart (leg); bu ikisinde set listesi boştur.
    E-sporda (oyun) her set bir oyundur (harita), skoru SofaScore'un verdiği gibi (raunt ya da kazanana 1).
    Alan tabloları belgeyle bağlı olduğundan birimler tenisin birimleri kalır (SP-2); neyin sayıldığını `format`
    söyler (SP-3).
    """

    SUMMARY: ClassVar[str] = (
        "Score family `sets`: sets won and the score of each set. Tennis and padel count games per set; volleyball, "
        "badminton and table tennis count points; darts played in sets counts legs per set; snooker and darts "
        "played in legs only give only the frames or legs won and no sets; e-sports give the games (maps) won "
        "and the score of each game.")

    family: Literal["sets"] = spec("Always `sets`.", source="sport registry")
    home: Optional[int] = spec(
        "Headline score of the home side: sets won; the frames, legs or games won with the formats `frames`, "
        "`legs_won` and `games_won`.", unit="sets", source="`homeScore.display`, else `homeScore.current`")
    away: Optional[int] = spec(
        "Headline score of the away side: sets won; the frames, legs or games won with the formats `frames`, "
        "`legs_won` and `games_won`.", unit="sets", source="`awayScore.display`, else `awayScore.current`")
    format: Optional[SetsFormat] = spec(
        "What the score counts. `games`, `points`, `legs`: sets won, and each set counts games (tennis, padel), "
        "points (volleyball, badminton, table tennis) or legs (darts played in sets). `frames`, `legs_won`: no "
        "sets; `sets_won` and the headline score are the frames (snooker) or legs (darts played in legs only or "
        "in a single set) won. `games_won`: `sets_won` and the headline score are the games (maps) won in "
        "e-sports, and each set is a game. Null when the record has no score sheet.",
        source="sport registry (`sofascore_scraper/sports.py`); darts: `legs` when the event's `bestOfSets` is more "
               "than 1, else `legs_won`",
        open_enum=True)
    sets_won: Optional[ScorePair] = spec(
        "Sets won by each side; the frames, legs or games won with the formats `frames`, `legs_won` and "
        "`games_won`.", unit="sets", source="`current`")
    sets: Tuple[SetScore, ...] = spec(
        "The sets that have a score, in order. Empty with the formats `frames` and `legs_won`. With `games_won` "
        "the games (maps) that have a score; a game SofaScore gives as 0-0 (not played, or the one being played "
        "while live) is left out.",
        source="`period1` to `period7` (tennis: to `period5`); tie-breaks from `period1TieBreak` to "
               "`period7TieBreak` (tennis: to `period5TieBreak`), in tennis and padel only")
    match_tiebreak: bool = spec(
        "True when the deciding set was a match tie-break (first to 10 points) and not a normal set. A "
        "heuristic: the last of three or five sets has a side with 10 or more. Always false outside tennis "
        "and padel.",
        source="derived from the set scores (`sofascore_scraper/status.py`)")


@dataclass(frozen=True)
class InningScore(Model):
    """Beyzbolda bir inning."""

    SUMMARY: ClassVar[str] = "Runs of one inning."

    number: int = spec("Number of the inning, starting at 1; extra innings go on after 9.",
                       source="`innings.inningN`, else `periodN`: N")
    home: Optional[int] = spec("Runs of the home side in the inning.", unit="runs",
                               source="`homeScore.innings.inningN.run`, else `homeScore.periodN`")
    away: Optional[int] = spec("Runs of the away side in the inning.", unit="runs",
                               source="`awayScore.innings.inningN.run`, else `awayScore.periodN`")


@dataclass(frozen=True)
class InningsScore(Model):
    """İnning ailesi skoru (beyzbol)."""

    SUMMARY: ClassVar[str] = "Score family `innings`: runs by inning, with hits and errors (baseball)."

    family: Literal["innings"] = spec("Always `innings`.", source="sport registry")
    home: Optional[int] = spec("Headline score of the home side: runs, extra innings included.", unit="runs",
                               source="`homeScore.display`, else `homeScore.current`")
    away: Optional[int] = spec("Headline score of the away side: runs, extra innings included.", unit="runs",
                               source="`awayScore.display`, else `awayScore.current`")
    innings: Tuple[InningScore, ...] = spec(
        "Runs of each inning that has a score, in order. SofaScore gives the innings in `innings`; some leagues "
        "also give them as `period1` to `period9`, with the same values. An inning missing from `innings` is "
        "read from `periodN`.", source="`innings.inningN.run`, else `periodN`")
    regulation: Optional[ScorePair] = spec("Runs after the scheduled innings. Null when SofaScore does not give it.",
                                           unit="runs", source="`normaltime`")
    extra_innings: Optional[ScorePair] = spec("Runs scored in extra innings alone. Null without extra innings.",
                                              unit="runs", source="`overtime`")
    hits: Optional[ScorePair] = spec("Hits of each side in the whole game.", unit="hits",
                                     source="`inningsBaseball.hits`")
    errors: Optional[ScorePair] = spec("Errors of each side in the whole game.", unit="errors",
                                       source="`inningsBaseball.errors`")


@dataclass(frozen=True)
class CricketInnings(Model):
    """Kriket: bir tarafın bir innings'i."""

    SUMMARY: ClassVar[str] = "One innings of one side in cricket."

    side: Literal["home", "away"] = spec("The side that batted.",
                                         source="`homeScore.innings` or `awayScore.innings`")
    number: int = spec("Number of the innings of this side, starting at 1.", source="`inningN`: N")
    runs: Optional[int] = spec("Runs scored.", unit="runs", source="`inningN.score`")
    wickets: Optional[int] = spec("Wickets lost.", unit="wickets", source="`inningN.wickets`")
    overs: Optional[float] = spec(
        "Overs bowled, in SofaScore's notation: the digit after the point counts balls, so 68.1 is 68 overs and one "
        "ball.", unit="overs", source="`inningN.overs`")


@dataclass(frozen=True)
class CricketScore(Model):
    """Kriket ailesi skoru."""

    SUMMARY: ClassVar[str] = "Score family `cricket`: the innings of both sides with runs, wickets and overs."

    family: Literal["cricket"] = spec("Always `cricket`.", source="sport registry")
    home: Optional[int] = spec("Headline score of the home side: runs of all its innings.", unit="runs",
                               source="`homeScore.display`, else `homeScore.current`")
    away: Optional[int] = spec("Headline score of the away side: runs of all its innings.", unit="runs",
                               source="`awayScore.display`, else `awayScore.current`")
    innings: Tuple[CricketInnings, ...] = spec(
        "The innings of both sides, the home side's first, each side's in its own order. SofaScore numbers each "
        "side's innings separately and does not say which side batted first.",
        source="`homeScore.innings`, `awayScore.innings`")


@dataclass(frozen=True)
class FightScore(Model):
    """Dövüş ailesi skoru (MMA): skor yok, sonuç yöntemi ve son raunt."""

    SUMMARY: ClassVar[str] = ("Score family `fight`: no score; how the fight was decided and in which round (MMA). "
                              "The winner is the event's `winner`.")

    family: Literal["fight"] = spec("Always `fight`.", source="sport registry")
    home: Optional[int] = spec("Headline score of the home side; SofaScore gives none for a fight, so null.",
                               source="`homeScore.display`, else `homeScore.current`")
    away: Optional[int] = spec("Headline score of the away side; SofaScore gives none for a fight, so null.",
                               source="`awayScore.display`, else `awayScore.current`")
    method: Optional[str] = spec(
        "How the fight was decided, as SofaScore abbreviates it, for example `UD` (unanimous decision), `SD` "
        "(split decision), `TKO`, `SUB` (submission); text, not an enumeration of the platform. Null while "
        "undecided.", source="`winType`")
    final_round: Optional[int] = spec("The round in which the fight ended. Null while undecided.",
                                      source="`finalRound`")


Score = Union[FootballScore, PeriodsScore, SetsScore, InningsScore, CricketScore, FightScore, PlainScore]


@dataclass(frozen=True)
class EventParticipant(Model):
    """Maçın bir tarafı."""

    SUMMARY: ClassVar[str] = "A side of an event."

    id: Optional[int] = spec("Id of the Participant.", source="`homeTeam.id` / `awayTeam.id`")
    name: Optional[str] = spec("Name of the participant at the time the event was read.",
                               source="`homeTeam.name` / `awayTeam.name`")


@dataclass(frozen=True)
class EventParticipants(Model):
    """Maçın iki tarafı."""

    SUMMARY: ClassVar[str] = "Both sides of an event."

    home: Optional[EventParticipant] = spec("The home side (the first-named side). Null when neither its id "
                                            "nor its name is known.", source="`homeTeam`")
    away: Optional[EventParticipant] = spec("The away side (the second-named side). Null when neither its id "
                                            "nor its name is known.", source="`awayTeam`")


@dataclass(frozen=True)
class Stage(Model):
    """Maçın turnuva içindeki bölümü (SofaScore'un benzersiz olmayan `tournament` nesnesi)."""

    SUMMARY: ClassVar[str] = "The part of a tournament an event belongs to: SofaScore's (non-unique) tournament object."

    id: Optional[int] = spec("SofaScore's id of the stage.", source="`tournament.id`")
    name: Optional[str] = spec("Name of the stage, for example `UEFA Champions League, Group A` or "
                               "`Wimbledon, London, GB, Qualifying, 1st - 2nd Round`.", source="`tournament.name`")


@dataclass(frozen=True)
class Round(Model):
    """Tur."""

    SUMMARY: ClassVar[str] = "The round of an event."

    number: Optional[int] = spec("Number of the round.", source="`roundInfo.round`")
    name: Optional[str] = spec("Name of the round, for example `Quarterfinals`. League rounds have none.",
                               source="`roundInfo.name`")
    slug: Optional[str] = spec("SofaScore's slug of the round.", source="`roundInfo.slug`")


@dataclass(frozen=True)
class Aggregate(Model):
    """İki maçlı eşleşmenin toplam skoru."""

    SUMMARY: ClassVar[str] = "The aggregate score of a two-legged tie, as shown with this leg."

    home: Optional[int] = spec("Aggregate score of this event's home side.", source="`homeScore.aggregated`")
    away: Optional[int] = spec("Aggregate score of this event's away side.", source="`awayScore.aggregated`")
    winner: Optional[Side] = spec("Who won the tie.",
                                  source="`aggregatedWinnerCode`: 1 is `home`, 2 is `away`, 3 is `draw`")


@dataclass(frozen=True)
class Quality(Model):
    """Kaydın güvenilirliği ve kaynağı."""

    SUMMARY: ClassVar[str] = "How much the record can be trusted, and where it comes from."

    source: RecordSource = spec(
        "`event`: the record derives from the stored `/event/{id}` payload. `listing`: the event is known only "
        "from a schedule page (a fixture, or a match whose details were never fetched); slices do not exist.",
        source="which stored payload the record derives from")
    observed_at_utc: Optional[str] = spec(
        "When the platform read the `/event/{id}` payload the record derives from. Null for a `listing` record "
        "and for a record stored by a version that did not note the time.",
        unit="ISO 8601 UTC", fmt="date-time", source="time of the platform's request")
    change_ts: Optional[int] = spec(
        "SofaScore's own time of its last change to the event. Carried as given; compare it for equality or "
        "order to detect a change.", unit="epoch seconds", source="`changes.changeTimestamp`")
    settlement: Settlement = spec(
        "`open`: the event has not reached a terminal status, or has no `/event/{id}` payload. `provisional`: "
        "terminal status, but it was last read before start time plus the refresh window, so the result may "
        "still be corrected. `final`: it was read after the window closed (or the time of the read or the start "
        "time is unknown, or the refresh policy is off); the platform will not read it again by itself.",
        source="derived: status class, `observed_at_utc`, `start_utc` and the refresh window setting")
    provisional: bool = spec("True exactly when `settlement` is `provisional`.", source="derived")
    tier_hint: Optional[bool] = spec(
        "True when SofaScore offers player statistics for the event or its tournament, which marks the better "
        "covered competitions (their results are corrected sooner). False when both flags are false, null when "
        "neither is given.",
        source="`tournament.uniqueTournament.hasEventPlayerStatistics`, `hasEventPlayerStatistics`")
    stale: bool = spec("True when a schedule page read later than the event payload disagrees with it in "
                       "status, winner, start time or a score field. The next refresh reads the event again.",
                       source="derived by comparing stored payloads")
    status_regressed: bool = spec(
        "True when the event was stored as completed and a later read showed it as void.",
        source="derived from the change log")


@dataclass(frozen=True)
class Event(Model):
    """Maç."""

    SUMMARY: ClassVar[str] = "A match."

    id: int = spec("SofaScore's event id.", source="`id`")
    sport: Optional[str] = spec("Slug of the sport. Null when no stored payload says it.",
                                source="`tournament.category.sport.slug`")
    category_id: Optional[int] = spec("Id of the Category.", source="`tournament.category.id`")
    tournament_id: Optional[int] = spec("Id of the Tournament. Null for an event without a unique tournament.",
                                        source="`tournament.uniqueTournament.id`")
    season_id: Optional[int] = spec("Id of the Season.", source="`season.id`")
    stage: Optional[Stage] = spec("The part of the tournament. Null when neither id nor name is known.",
                                  source="`tournament`")
    round: Optional[Round] = spec("The round. Null when the event has no round information.",
                                  source="`roundInfo`")
    start_utc: Optional[str] = spec("Scheduled start. For tennis this is the planned time, not the first point.",
                                    unit="ISO 8601 UTC", fmt="date-time", source="`startTimestamp`")
    status: Status = spec("Status: SofaScore's triple and the platform's class.", source="`status`")
    participants: EventParticipants = spec("The two sides.", source="`homeTeam`, `awayTeam`")
    score: Score = spec("The score, in the structure of the sport's score family.",
                        source="`homeScore`, `awayScore`")
    winner: Optional[Side] = spec("Who won. Null while undecided and when SofaScore names no winner.",
                                  source="`winnerCode`: 1 is `home`, 2 is `away`, 3 is `draw`")
    aggregate: Optional[Aggregate] = spec("Aggregate of a two-legged tie. Null for every other event.",
                                          source="`homeScore.aggregated`, `awayScore.aggregated`, "
                                                 "`aggregatedWinnerCode`")
    slug: Optional[str] = spec("SofaScore's slug of the event.", source="`slug`")
    custom_id: Optional[str] = spec("SofaScore's short id of the pairing, used in its page addresses.",
                                    source="`customId`")
    quality: Quality = spec("Provenance and reliability of the record.", source="derived")


# --- dilim (Slice) ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SliceError(Model):
    """Dilimin son başarısız isteği."""

    SUMMARY: ClassVar[str] = "The last failed attempt to fetch a slice."

    reason: str = spec("Why it failed.", source="the platform's request", open_enum=True,
                       known=("403", "429", "5xx", "timeout", "network", "parse", "breaker", "corrupt", "other"))
    http_status: Optional[int] = spec("HTTP status of the failed response, when there was one.",
                                      source="the platform's request")
    at_utc: Optional[str] = spec("When the attempt failed.", unit="ISO 8601 UTC", fmt="date-time",
                                 source="time of the platform's request")
    count: int = spec("How many attempts in a row have failed.", source="the platform's bookkeeping")


@dataclass(frozen=True)
class Slice(Model):
    """Dilim: bir varlık hakkında saklanan bir SofaScore yanıtı ve durumu."""

    SUMMARY: ClassVar[str] = "One stored response of SofaScore about an event or another entity, and its state."

    owner_kind: str = spec("What the slice belongs to.", source="the request that fetched it", open_enum=True,
                           known=("event", "tournament", "season", "team", "player", "sport"))
    owner_id: int = spec("Id of the owner; for `event` the event id.", source="the request that fetched it")
    key: str = spec("Name of the slice, for example `event`, `statistics`, `lineups`, `incidents`.",
                    source="slice registry (`sofascore_scraper/sports.py`)", open_enum=True,
                    known=("event", "statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents",
                           "point_by_point", "esports_games", "innings", "odds_featured", "odds_all",
                           "odds_changes", "winning_odds", "seasons", "schedule", "standings", "season_info",
                           "cuptrees", "top_players", "top_teams", "season_odds", "team_rankings",
                           "player_statistics", "rankings"))
    sub: Optional[str] = spec("Sub-key for a slice that has several payloads per owner, for example the round "
                              "of a schedule page. Null when the slice has one payload.",
                              source="slice registry")
    state: SliceState = spec(
        "`ok`: a payload with data is stored. `empty`: SofaScore answered that it has no such data (404, or a "
        "response without content). `error`: the last attempt failed and it is unknown whether data exists. "
        "`not_requested`: the platform has not asked for it.", source="the platform's bookkeeping")
    has_payload: bool = spec("True when a payload is stored. A slice in state `error` can still hold the "
                             "payload of an earlier successful read.", source="the platform's bookkeeping")
    fetched_at_utc: Optional[str] = spec("When the stored payload was read.", unit="ISO 8601 UTC",
                                         fmt="date-time", source="time of the platform's request")
    checked_at_utc: Optional[str] = spec("When the slice was last asked for, whatever the outcome.",
                                         unit="ISO 8601 UTC", fmt="date-time",
                                         source="time of the platform's request")
    error: Optional[SliceError] = spec("The last failure. Null unless the state is `error`.",
                                       source="the platform's bookkeeping")
    payload: Any = spec("The stored SofaScore response, unchanged (see Raw on request). Present only when "
                        "asked for; null otherwise.", source="the whole response")


# --- değişiklik (Change) ------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChangedField(Model):
    """İki okuma arasında değişen bir alan."""

    SUMMARY: ClassVar[str] = "One field that changed between two reads of an event."

    path: str = spec(
        "Path of the field in SofaScore's event object: `status.type`, `status.code`, `status.description`, "
        "`winnerCode`, `startTimestamp`, `homeScore.<key>`, `awayScore.<key>`.", source="the path itself")
    old: Any = spec("Value before, as SofaScore gave it. Null when the field did not exist.",
                    source="the stored payload")
    new: Any = spec("Value after, as SofaScore gave it. Null when the field no longer exists.",
                    source="the new payload")


@dataclass(frozen=True)
class Change(Model):
    """Saklanmış bir maçta sonradan görülen değişiklik."""

    SUMMARY: ClassVar[str] = "A change of an already stored event that a later read found."

    seq: int = spec("Sequence number of the change log. Increases by one per change; pass the last one seen "
                    "to read the next changes.", source="the platform's change log")
    recorded_at_utc: str = spec("When the platform found the change.", unit="ISO 8601 UTC", fmt="date-time",
                                source="time of the platform's request")
    event_id: int = spec("Id of the Event.", source="`id`")
    sport: Optional[str] = spec("Slug of the sport.", source="`tournament.category.sport.slug`")
    tournament_id: Optional[int] = spec("Id of the Tournament.", source="`tournament.uniqueTournament.id`")
    start_utc: Optional[str] = spec("Scheduled start of the event after the change.", unit="ISO 8601 UTC",
                                    fmt="date-time", source="`startTimestamp`")
    seconds_after_start: Optional[int] = spec(
        "How long after the scheduled start SofaScore made the change: its change time, or the time the "
        "platform found the change when SofaScore gave none, minus the start.", unit="seconds",
        source="`changes.changeTimestamp` - `startTimestamp`")
    old_status_class: Optional[StatusClassName] = spec("Status class before.", source="derived from `status`")
    new_status_class: Optional[StatusClassName] = spec("Status class after.", source="derived from `status`")
    old_change_ts: Optional[int] = spec("SofaScore's change time before.", unit="epoch seconds",
                                        source="`changes.changeTimestamp` of the stored payload")
    new_change_ts: Optional[int] = spec("SofaScore's change time after.", unit="epoch seconds",
                                        source="`changes.changeTimestamp` of the new payload")
    status_regressed: bool = spec("True when the event went from completed to void.", source="derived")
    tier_hint: Optional[bool] = spec("As `Event.quality.tier_hint`, at the time of the change.",
                                     source="`tournament.uniqueTournament.hasEventPlayerStatistics`, "
                                            "`hasEventPlayerStatistics`")
    fields: Tuple[ChangedField, ...] = spec("The fields that changed, ordered by path. Compared are the status "
                                            "triple, the winner code, the start time and every score field.",
                                            source="difference of the two payloads")


# --- akış olayı (LiveEvent) ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveEvent(Model):
    """Akış olayı: `sofascore.event/1` zarfı."""

    SUMMARY: ClassVar[str] = "An event of a stream: the envelope `sofascore.event/1` that sinks and `ssc events` write."

    stream: str = spec("The stream the event belongs to.", source="the producer", open_enum=True,
                       known=("live", "change", "job", "system"))
    seq: int = spec("Sequence number, one sequence for all streams of a data directory. Strictly increasing, "
                    "not consecutive. De-duplicate on it.", source="the platform's stream log")
    type: str = spec("Type of the event, prefixed with its stream.", source="the producer", open_enum=True,
                     known=("live.status_changed", "live.score_changed", "live.stuck", "change.recorded",
                            "job.started", "job.finished", "system.blocked", "system.recovered",
                            "system.live_source_changed", "system.sink_dropped"))
    ts: str = spec("When the platform recorded the event, with milliseconds.", unit="ISO 8601 UTC",
                   fmt="date-time", source="the platform's clock")
    event_id: Optional[int] = spec("Id of the Event it is about. Null for events about no match.",
                                   source="`id`")
    sport: Optional[str] = spec("Slug of the sport.", source="the producer")
    tournament_id: Optional[int] = spec("Id of the Tournament.", source="`tournament.uniqueTournament.id`")
    source: Optional[str] = spec("How the platform learned of it.", source="the producer", open_enum=True,
                                 known=("push", "poll", "job", "system"))
    data: Mapping[str, Any] = spec("Type-specific content (see LiveEvent data).", source="the producer")


# --- bahis oranları ve puan durumu (plan maddesi P28) ---------------------------------------------------
#
# Oranlar ve puan durumu, şemanın ham vermediği iki maç dışı yük biçimidir. Öteki P28 dilimleri (sezon bilgisi,
# kupa ağacı, en iyiler listeleri, sıralamalar, oyuncu istatistikleri) Slice kaydıyla ham verilir.

@dataclass(frozen=True)
class OddsChoice(Model):
    """Bir pazarın bir seçeneği ve oranı."""

    SUMMARY: ClassVar[str] = "One outcome of a betting market and its price."

    name: str = spec("Name of the outcome as SofaScore gives it, for example `1`, `X`, `2`, `Over`.",
                     source="`choices[].name`")
    fractional: Optional[str] = spec("Current price as a fraction, for example `11/5`.",
                                     source="`choices[].fractionalValue`")
    decimal: Optional[float] = spec("Current price as a decimal (1 + the fraction), rounded to three places.",
                                    source="derived from `choices[].fractionalValue`")
    initial_fractional: Optional[str] = spec("Opening price as a fraction.",
                                             source="`choices[].initialFractionalValue`")
    initial_decimal: Optional[float] = spec("Opening price as a decimal, rounded to three places.",
                                            source="derived from `choices[].initialFractionalValue`")
    change: Optional[int] = spec("Direction of the last change of the price: 1 up, -1 down, 0 none.",
                                 source="`choices[].change`")
    winning: Optional[bool] = spec("True for the outcome that won once the event is settled; null while open "
                                   "or when SofaScore does not say.", source="`choices[].winning`")


@dataclass(frozen=True)
class OddsMarket(Model):
    """Bir bahis pazarı (maç sonucu, alt/üst, handikap ...)."""

    SUMMARY: ClassVar[str] = "One betting market of an event with its outcomes."

    market_id: Optional[int] = spec("SofaScore's id of the market type (1 is the match result).",
                                    source="`marketId`")
    name: Optional[str] = spec("Name of the market, for example `Full time`.", source="`marketName`")
    group: Optional[str] = spec("Group of the market, for example `1X2` or `Home/Away`.", source="`marketGroup`")
    period: Optional[str] = spec("Part of the event the market covers, for example `Full-time`.",
                                 source="`marketPeriod`")
    choice_group: Optional[str] = spec("Line of a market with several lines, for example `2.5` for over/under; "
                                       "null for a market with one line.", source="`choiceGroup`")
    label: Optional[str] = spec("Name under which the featured odds list the market (`default`, `fullTime`, "
                                "`asian`); null in the full list.", source="key of `featured`")
    is_live: Optional[bool] = spec("True when the prices were offered during play.", source="`isLive`")
    suspended: Optional[bool] = spec("True when the market was closed for bets at the time of the read.",
                                     source="`suspended`")
    choices: Tuple[OddsChoice, ...] = spec("The outcomes of the market, in SofaScore's order.",
                                           source="`choices`")


@dataclass(frozen=True)
class Odds(Model):
    """Bir maçın bir oran dilimi: tek okuma, bir anlık görüntü."""

    SUMMARY: ClassVar[str] = "The odds of an event from one provider as read at one moment."

    event_id: int = spec("Id of the Event.", source="the request that fetched it")
    key: str = spec("Odds slice the record comes from: `odds_all` or `odds_featured`.",
                    source="slice registry (`sofascore_scraper/sports.py`)", open_enum=True,
                    known=("odds_all", "odds_featured"))
    provider_id: Optional[int] = spec("SofaScore's id of the bookmaker the odds come from. Which bookmakers "
                                      "SofaScore offers depends on the country it sees the request from; the "
                                      "platform stores no address or location of the machine.",
                                      source="the request that fetched it")
    fetched_at_utc: Optional[str] = spec("When the odds were read. A read is a snapshot: odds change until the "
                                         "event ends, and only a later read shows a later price.",
                                         unit="ISO 8601 UTC", fmt="date-time",
                                         source="time of the platform's request")
    markets: Tuple[OddsMarket, ...] = spec("The markets, in SofaScore's order.",
                                           source="`markets`, or the values of `featured`")


@dataclass(frozen=True)
class OddsLine(Model):
    """Dışa aktarmanın düz oran satırı: bir anlık görüntünün bir pazarının bir seçeneği."""

    SUMMARY: ClassVar[str] = "One outcome of one market of one odds snapshot, as a flat row."

    event_id: int = spec("Id of the Event.", source="the request that fetched it")
    key: str = spec("Odds slice the row comes from.", source="slice registry (`sofascore_scraper/sports.py`)", open_enum=True,
                    known=("odds_all", "odds_featured"))
    provider_id: Optional[int] = spec("As `Odds.provider_id`.", source="the request that fetched it")
    fetched_at_utc: Optional[str] = spec("When the snapshot was read.", unit="ISO 8601 UTC", fmt="date-time",
                                         source="time of the platform's request")
    market_id: Optional[int] = spec("As `OddsMarket.market_id`.", source="`marketId`")
    market_name: Optional[str] = spec("As `OddsMarket.name`.", source="`marketName`")
    market_group: Optional[str] = spec("As `OddsMarket.group`.", source="`marketGroup`")
    market_period: Optional[str] = spec("As `OddsMarket.period`.", source="`marketPeriod`")
    choice_group: Optional[str] = spec("As `OddsMarket.choice_group`.", source="`choiceGroup`")
    label: Optional[str] = spec("As `OddsMarket.label`.", source="key of `featured`")
    is_live: Optional[bool] = spec("As `OddsMarket.is_live`.", source="`isLive`")
    suspended: Optional[bool] = spec("As `OddsMarket.suspended`.", source="`suspended`")
    choice: str = spec("As `OddsChoice.name`.", source="`choices[].name`")
    fractional: Optional[str] = spec("As `OddsChoice.fractional`.", source="`choices[].fractionalValue`")
    decimal: Optional[float] = spec("As `OddsChoice.decimal`.", source="derived from `choices[].fractionalValue`")
    initial_fractional: Optional[str] = spec("As `OddsChoice.initial_fractional`.",
                                             source="`choices[].initialFractionalValue`")
    initial_decimal: Optional[float] = spec("As `OddsChoice.initial_decimal`.",
                                            source="derived from `choices[].initialFractionalValue`")
    change: Optional[int] = spec("As `OddsChoice.change`.", source="`choices[].change`")
    winning: Optional[bool] = spec("As `OddsChoice.winning`.", source="`choices[].winning`")


@dataclass(frozen=True)
class StandingsRow(Model):
    """Puan durumunun bir satırı."""

    SUMMARY: ClassVar[str] = "One row of a standings table of a season."

    tournament_id: int = spec("Id of the Tournament.", source="the request that fetched it")
    season_id: int = spec("Id of the Season.", source="the request that fetched it")
    table: str = spec("Which table: `total` (all matches) or `home` (home matches only).",
                      source="the request that fetched it", open_enum=True, known=("total", "home"))
    group_name: Optional[str] = spec("Name of the table or group, for example `Premier League 26/27` or "
                                     "`Group A`.", source="`standings[].name`")
    position: Optional[int] = spec("Rank in the table, 1 for the first.", source="`rows[].position`")
    participant_id: Optional[int] = spec("Id of the Participant.", source="`rows[].team.id`")
    participant_name: Optional[str] = spec("Name of the Participant.", source="`rows[].team.name`")
    matches: Optional[int] = spec("Matches played.", source="`rows[].matches`")
    wins: Optional[int] = spec("Matches won.", source="`rows[].wins`")
    draws: Optional[int] = spec("Matches drawn; null in sports without draws.", source="`rows[].draws`")
    losses: Optional[int] = spec("Matches lost.", source="`rows[].losses`")
    scores_for: Optional[int] = spec("Goals or points scored.", source="`rows[].scoresFor`")
    scores_against: Optional[int] = spec("Goals or points conceded.", source="`rows[].scoresAgainst`")
    points: Optional[float] = spec("Table points (a fraction in a few sports).", source="`rows[].points`")
    fetched_at_utc: Optional[str] = spec("When the table was read.", unit="ISO 8601 UTC", fmt="date-time",
                                         source="time of the platform's request")


MODELS: Tuple[type, ...] = (
    Sport, Category, Tournament, Season, Participant,
    Event, Status, EventParticipants, EventParticipant, Stage, Round, Aggregate, Quality,
    ScorePair, PlainScore, FootballScore, PeriodsScore, PeriodScore, SetsScore, SetScore,
    InningsScore, InningScore, CricketScore, CricketInnings, FightScore,
    Slice, SliceError,
    Change, ChangedField,
    LiveEvent,
    Odds, OddsMarket, OddsChoice, OddsLine, StandingsRow,
)
# Kendi başına verilen kayıtlar (API kaynağı, dışa aktarma veri kümesi, akış satırı); gerisi bunların parçası.
# P28'in oran ve puan durumu kayıtları FX-21'den beri buradadır: `Odds` (API v1'in oran anlık görüntüsü),
# `OddsLine` (dışa aktarmanın `odds` satırı), `StandingsRow` (puan durumu ve `standings` veri kümesinin satırı).
RECORDS: Tuple[type, ...] = (Sport, Category, Tournament, Season, Participant, Event, Slice, Change, LiveEvent,
                             Odds, OddsLine, StandingsRow)

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_ID",
    "EVENT_ENVELOPE_ID",
    "MODELS",
    "RECORDS",
    "Model",
    "Odds",
    "OddsChoice",
    "OddsLine",
    "OddsMarket",
    "StandingsRow",
    "Sport",
    "Category",
    "Tournament",
    "Season",
    "Participant",
    "Event",
    "Status",
    "EventParticipants",
    "EventParticipant",
    "Stage",
    "Round",
    "Aggregate",
    "Quality",
    "Score",
    "ScorePair",
    "PlainScore",
    "FootballScore",
    "PeriodsScore",
    "PeriodScore",
    "SetsScore",
    "SetScore",
    "InningsScore",
    "InningScore",
    "CricketScore",
    "CricketInnings",
    "FightScore",
    "Slice",
    "SliceError",
    "Change",
    "ChangedField",
    "LiveEvent",
    "StatusClassName",
    "Side",
    "ParticipantType",
    "PeriodsFormat",
    "SetsFormat",
    "Settlement",
    "RecordSource",
    "SliceState",
    "spec",
    "json_name",
]
