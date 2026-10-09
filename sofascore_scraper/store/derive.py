"""
Yük → katalog satırı (docs/design/01-storage.md, bölüm 3.3 "Notes on the event row"). Saf işlevler:
diske, veritabanına ve saate dokunmaz; aynı girdi her zaman aynı satırı verir.

Bir yükten skorun okunduğu tek yer burasıdır. `status_class` ve `scores_json` sofascore_scraper.status'tan
(classify_status, extract_scores) gelir; `home_score` / `away_score` normalleştirilmiş skordur
(`display`, yoksa `current`). 2.x liste ve CSV biçimlerinin `homeScore.current` sütunları 3.1'de kalktı (P30).

İşlevlerin döndürdüğü sözlüklerin anahtarları sütun adlarıdır. Bir sözlükte olmayan sütun o satırın bu
kaynaktan türetilmediğini söyler (ör. `event_row` depolama sütunlarını, olaydan türetilen sezon satırı
`listed` / `position` sütunlarını içermez); sofascore_scraper/store/catalog.py'deki `upsert` yalnızca verilen sütunları
yazar.

DERIVE_VERSION: buradaki herhangi bir işlevin çıktısı değişirse (yeni sütun, yeni spor için skor
çizelgesi, ad katlama kuralı) artırılır; katalog bunu görünce dosyalardan yeniden kurulur (bölüm 7.2).
tests/golden/derive/event_rows.json değiştiyse sürüm de artmalıdır. Aynı dosyalardan başka katalog
satırları çıkaran her kural değişikliği de sürümü artırır: dilim durumunu belirleyen kurallar
(sofascore_scraper/slices.py) ve eski düzenin ad kuralları (sofascore_scraper/store/legacy.py) bu modülün dışında durur ama katalog
onlardan da türer.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from sofascore_scraper.sports import event_sport_slug, score_family
from sofascore_scraper.status import ScoreSheet, StatusClass, classify_status, extract_scores
from sofascore_scraper.store.errors import PayloadCorrupt

logger = logging.getLogger(__name__)

# 1: ilk sürüm
# 2: point_by_point'in kendi "veri var mı" kuralı var ({"pointByPoint": []} artık `empty`); slug'ında büyük
#    harf olan eski tur dosyası yine program sayfası (alt anahtar küçük harfe katlanır)
# 3: sekiz periyot sporu kayıt defterinde (SP-1): Amerikan futbolu, Aussie kuralları, buz hokeyi, hentbol,
#    ragbi, futsal, mini futbol ve florbol maçlarının satırı skor çizelgesi (scores_json) alır
# 4: beş set sporu kayıt defterinde (SP-2): voleybol, badminton, masa tenisi, padel ve snooker maçlarının satırı
#    skor çizelgesi (scores_json) alır
# 5: beş B sınıfı spor kayıt defterinde (SP-3): beyzbol, kriket, e-spor, dart ve MMA maçlarının satırı skor
#    çizelgesi alır; kriketin `willcontinue` durumu (gün sonu) UNKNOWN değil LIVE sınıfındadır
# 6: futbolda kod 120 (AP) maçın uzatma skoru (`aet`) yalnızca SofaScore uzatma anahtarı (overtime / extra1 /
#    extra2) yolladıysa dolar; uzatmasız doğrudan penaltıya giden maç uzatma oynanmış görünmez (FX-23, F10).
#    Sürüm değiştiği için eski kataloglar ilk açılışta dosyalardan yeniden kurulur ve düzelir.
# 7: 2.x liste ve CSV biçimlerinin `home_score_current` / `away_score_current` sütunları kalktı (3.1, P30;
#    katalog şeması da 2'ye çıktı)
DERIVE_VERSION = 7

Row = Dict[str, Any]
Timestamp = Union[datetime, int, float, None]

ROW_SOURCES: Tuple[str, ...] = ("event", "listing")

# `event_row`'un doldurduğu sütunlar, DDL sırasıyla. Geri kalanlar (status_regressed, stale, listed_in,
# layout, path, legacy_path, sig, first_seen_at, updated_at) yükten türetilemez: manifestten, dosya
# konumundan ya da listelerden gelir ve onları dizinleyici yazar.
EVENT_DERIVED_COLUMNS: Tuple[str, ...] = (
    "id", "sport", "category_id", "tournament_id", "stage_id", "stage_name", "season_id",
    "round", "round_name", "round_slug", "start_ts",
    "status_type", "status_code", "status_description", "status_class",
    "home_id", "away_id", "home_name", "away_name",
    "home_score", "away_score",
    "winner_code", "scores_json", "slug", "custom_id",
    "observed_at", "change_ts", "observed_gap", "tier_hint",
    "row_source", "has_event_payload",
)

SIDE_HOME = 1
SIDE_AWAY = 2

_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1
# Skor çizelgesinin ayrı sütunlarda zaten duran alanları (sport, status_class, winner_code, change_ts);
# scores_json'a yalnızca spor ailesine özgü alanlar girer
_SHEET_COMMON = frozenset(f.name for f in dataclasses.fields(ScoreSheet))
# NFKD ile ayrışmayan, aramada yalın harfe inmesi beklenen Latin harfleri (küçük harf katlamasından sonra)
_FOLD_EXTRA = str.maketrans({
    "ı": "i", "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ħ": "h",
})


# --- yardımcılar ------------------------------------------------------------------------------------

def _obj(node: Any, key: str) -> Mapping[str, Any]:
    """`node[key]` bir nesneyse o, değilse boş nesne (eksik ya da beklenmeyen tipte alan satırı düşürmez)."""
    value = node.get(key) if isinstance(node, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _as_dict(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """sofascore_scraper.status ve sofascore_scraper.sports işlevleri `dict` bekler; başka bir Mapping gelirse sığ kopya alınır."""
    return payload if isinstance(payload, dict) else dict(payload)


def _int(value: Any) -> Optional[int]:
    """INTEGER sütunu için değer: tam sayı, tam sayıya inen ondalık ya da rakam metni; aksi halde None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        value = math.floor(value)
    elif isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return None
    if not isinstance(value, int):
        return None
    return value if _INT64_MIN <= value <= _INT64_MAX else None


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _flag(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, bool) else None


def epoch_seconds(value: Timestamp) -> Optional[int]:
    """
    Katalogdaki zaman sütunları için epoch saniye (tam sayı, aşağı yuvarlanır). datetime (saat dilimi
    yoksa UTC sayılır) ya da epoch saniye alır; None None kalır.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return math.floor(aware.timestamp())
    return _int(value)


def fold_name(value: object) -> str:
    """
    Arama için katlanmış ad: küçük harf katlaması (casefold), aksanlar atılır, boşluklar teke iner.
    "Beşiktaş" → "besiktas", "İstanbul" → "istanbul", "Kasımpaşa" → "kasimpasa", "Bodø/Glimt" → "bodo/glimt".
    Arayan taraf sorgu metnini de bu işlevden geçirir.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.translate(_FOLD_EXTRA).split())


def _folded(name: Optional[str]) -> Optional[str]:
    return fold_name(name) if name is not None else None


def season_sort_key(year: object) -> float:
    """
    Sezon yılı metni → sıralanabilir sayı (büyük = yeni). SeasonFetcher._get_sortable_year_value ile aynı
    kural: "24/25" → 2024, "99/00" → 2000, "98/99" → 1998, "2024/2025" → 2024, "2024" → 2024, boş / "0" → 0.

    Tek fark: o işlevin hata fırlattığı ya da sıralanamayan değer döndürdüğü girdiler ("ab/cd", "nan",
    metin olmayan değer) burada 0.0 olur; katalog satırı bozuk bir yıl metni yüzünden düşmez.
    """
    text = "" if year is None else str(year)
    if not text or text == "0":
        return 0.0
    try:
        if "/" in text:
            parts = text.split("/")
            start, end = parts[0].strip(), parts[1].strip()
            if len(start) == 2 and len(end) == 2:
                first, second = int(start), int(end)
                if first > second:  # yüzyıl geçişi: 99/00 → 2000
                    value = 2000.0 + second
                elif first < 50:
                    value = 2000.0 + first
                else:
                    value = 1900.0 + first
            else:
                value = float(start)
        else:
            value = float(text)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) else 0.0


# --- olay satırı ------------------------------------------------------------------------------------

def _status_class(payload: Mapping[str, Any]) -> str:
    try:
        return classify_status(_as_dict(payload)).value
    except TypeError:  # ör. status.code bir liste: sınıflandırılamaz
        return StatusClass.UNKNOWN.value


def scores_json(payload: Mapping[str, Any], sport: Optional[str]) -> Optional[str]:
    """
    Normalleştirilmiş skor çizelgesi (sofascore_scraper.status.extract_scores) JSON metni olarak: {"family": ...} ve
    o ailenin alanları; çiftler [ev, deplasman] listesidir. Ayrı sütunlarda duran ortak alanlar
    (sport, status_class, winner_code, change_ts) yazılmaz. Skor ailesi bilinmeyen sporda None.
    """
    family = score_family(sport)
    if family is None:
        return None
    try:
        sheet = extract_scores(_as_dict(payload), sport)
    except (AttributeError, TypeError, ValueError) as exc:
        # homeScore / awayScore / changes beklenmeyen tipte: satır yine yazılır, çizelge boş kalır
        logger.warning(f"The score sheet of event {payload.get('id')} could not be extracted: "
                       f"{type(exc).__name__}: {exc}")
        return None
    body = {k: v for k, v in dataclasses.asdict(sheet).items() if k not in _SHEET_COMMON}
    return json.dumps({"family": family, **body}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def tier_hint(payload: Mapping[str, Any]) -> Optional[int]:
    """
    Oyuncu istatistiği bayrağı (sofascore_scraper/refresh.py `_tier_hint` ile aynı kural): turnuvada ya da olayda
    `hasEventPlayerStatistics` doğruysa 1, ikisi de yoksa None, aksi halde 0.
    """
    flags = (
        _obj(_obj(payload, "tournament"), "uniqueTournament").get("hasEventPlayerStatistics"),
        payload.get("hasEventPlayerStatistics"),
    )
    if any(flags):
        return 1
    return None if all(f is None for f in flags) else 0


def event_row(payload: Mapping[str, Any], source: str = "event", observed_at: Timestamp = None, *,
              sport: Optional[str] = None) -> Row:
    """
    Bir olay nesnesinden (`/event/{id}` yanıtındaki olay ya da bir listedeki olay) `events` satırının
    yükten türeyen sütunları (EVENT_DERIVED_COLUMNS).

    source: "event" (maç sayfası yükü; `has_event_payload` = 1) ya da "listing" (tur / sayfa listesi).
    observed_at: yükün okunduğu an; None = bilinmiyor (gözlem kaydı olmayan eski kayıt).
    sport: yük sporunu söylemiyorsa kullanılacak slug (ör. listenin turnuvasından); yükteki spor önceliklidir.

    Kimliği olmayan ya da nesne olmayan yük PayloadCorrupt verir.
    """
    if source not in ROW_SOURCES:
        raise ValueError(f"Geçersiz satır kaynağı: {source!r}")
    if not isinstance(payload, Mapping):
        raise PayloadCorrupt(f"The event payload is not an object: {type(payload).__name__}")
    event_id = _int(payload.get("id"))
    if event_id is None:
        raise PayloadCorrupt(f"The event payload has no valid id: {payload.get('id')!r}")

    tournament = _obj(payload, "tournament")
    status = _obj(payload, "status")
    round_info = _obj(payload, "roundInfo")
    home, away = _obj(payload, "homeTeam"), _obj(payload, "awayTeam")
    home_score, away_score = _obj(payload, "homeScore"), _obj(payload, "awayScore")

    payload = _as_dict(payload)
    slug = event_sport_slug(payload) or (str(sport).lower() if sport else "")
    start_ts = _int(payload.get("startTimestamp"))
    observed = epoch_seconds(observed_at)

    def normalised(score: Mapping[str, Any]) -> Optional[int]:
        display = _int(score.get("display"))
        return display if display is not None else _int(score.get("current"))

    return {
        "id": event_id,
        "sport": slug,
        "category_id": _int(_obj(tournament, "category").get("id")),
        "tournament_id": _int(_obj(tournament, "uniqueTournament").get("id")),
        "stage_id": _int(tournament.get("id")),
        "stage_name": _text(tournament.get("name")),
        "season_id": _int(_obj(payload, "season").get("id")),
        "round": _int(round_info.get("round")),
        "round_name": _text(round_info.get("name")),
        "round_slug": _text(round_info.get("slug")),
        "start_ts": start_ts,
        "status_type": _text(status.get("type")),
        "status_code": _int(status.get("code")),
        "status_description": _text(status.get("description")),
        "status_class": _status_class(payload),
        "home_id": _int(home.get("id")),
        "away_id": _int(away.get("id")),
        "home_name": _text(home.get("name")),
        "away_name": _text(away.get("name")),
        "home_score": normalised(home_score),
        "away_score": normalised(away_score),
        "winner_code": _int(payload.get("winnerCode")),
        "scores_json": scores_json(payload, slug or None),
        "slug": _text(payload.get("slug")),
        "custom_id": _text(payload.get("customId")),
        "observed_at": observed,
        "change_ts": _int(_obj(payload, "changes").get("changeTimestamp")),
        "observed_gap": observed - start_ts if observed is not None and start_ts is not None else None,
        "tier_hint": tier_hint(payload),
        "row_source": source,
        "has_event_payload": 1 if source == "event" else 0,
    }


def event_participant_rows(event: Mapping[str, Any]) -> List[Row]:
    """
    Bir `events` satırından `event_participants` satırları (ev sahibi 1, deplasman 2). Başlangıç zamanı
    bilinmiyorsa 0 yazılır (sütun NOT NULL, birincil anahtarın parçası). Kimliği olmayan taraf atlanır.
    """
    rows: List[Row] = []
    seen = set()
    for side, key in ((SIDE_HOME, "home_id"), (SIDE_AWAY, "away_id")):
        participant_id = event.get(key)
        if participant_id is None or participant_id in seen:
            continue
        seen.add(participant_id)
        rows.append({
            "participant_id": participant_id,
            "start_ts": event.get("start_ts") or 0,
            "event_id": event["id"],
            "side": side,
        })
    return rows


# --- varlık satırları -------------------------------------------------------------------------------

def sport_row(sport: Any) -> Optional[Row]:
    """`tournament.category.sport` nesnesinden `sports` satırı; slug yoksa None."""
    if not isinstance(sport, Mapping):
        return None
    slug = _text(sport.get("slug"))
    if not slug:
        return None
    return {"slug": slug.lower(), "id": _int(sport.get("id")), "name": _text(sport.get("name"))}


def category_row(category: Any, *, sport: Optional[str] = None) -> Optional[Row]:
    """Kategori (ülke / tur) nesnesinden `categories` satırı; kimlik yoksa None. Spor bilinmiyorsa ''."""
    if not isinstance(category, Mapping):
        return None
    category_id = _int(category.get("id"))
    if category_id is None:
        return None
    own = _text(_obj(category, "sport").get("slug"))
    return {
        "id": category_id,
        "sport": (own or sport or "").lower(),
        "name": _text(category.get("name")),
        "slug": _text(category.get("slug")),
        "alpha2": _text(category.get("alpha2")),
    }


def tournament_row(unique_tournament: Any, *, updated_at: Timestamp, category: Any = None,
                   sport: Optional[str] = None) -> Optional[Row]:
    """
    `uniqueTournament` nesnesinden `tournaments` satırı; kimlik yoksa None.
    category: nesnenin kendi `category` alanı yoksa kullanılacak kategori (olayın `tournament.category`si).
    """
    if not isinstance(unique_tournament, Mapping):
        return None
    tournament_id = _int(unique_tournament.get("id"))
    if tournament_id is None:
        return None
    own_category = _obj(unique_tournament, "category") or (category if isinstance(category, Mapping) else {})
    own_sport = _text(_obj(own_category, "sport").get("slug"))
    name = _text(unique_tournament.get("name"))
    return {
        "id": tournament_id,
        "sport": (own_sport or sport or "").lower() or None,
        "category_id": _int(own_category.get("id")),
        "name": name,
        "name_folded": _folded(name),
        "slug": _text(unique_tournament.get("slug")),
        "updated_at": epoch_seconds(updated_at),
    }


def season_row(season: Any, *, tournament_id: Optional[int], updated_at: Timestamp) -> Optional[Row]:
    """
    Sezon nesnesinden (olaydaki `season` ya da sezon listesinin bir öğesi) `seasons` satırı. Sezon ya da
    turnuva kimliği yoksa None (sütun NOT NULL: turnuvasız olayın sezonu kataloğa girmez).
    `listed` / `position` burada yoktur; onları yalnızca `season_list_rows` yazar.
    """
    if not isinstance(season, Mapping) or tournament_id is None:
        return None
    season_id = _int(season.get("id"))
    if season_id is None:
        return None
    year = _text(season.get("year"))
    return {
        "id": season_id,
        "tournament_id": tournament_id,
        "name": _text(season.get("name")),
        "year": year,
        "sort_key": season_sort_key(year),
        "updated_at": epoch_seconds(updated_at),
    }


def season_list_rows(payload: Any, *, tournament_id: int, updated_at: Timestamp) -> List[Row]:
    """
    Sezon listesi yükünden ({"seasons": [...]}) `seasons` satırları: `listed` = 1, `position` listedeki
    sıra (0 = ilk). Kimliği olmayan öğe atlanır ama sırayı tüketir; aynı kimlik ikinci kez gelirse ilki kalır.
    """
    items = payload.get("seasons") if isinstance(payload, Mapping) else None
    rows: List[Row] = []
    seen = set()
    for position, item in enumerate(items if isinstance(items, list) else []):
        row = season_row(item, tournament_id=tournament_id, updated_at=updated_at)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        row["listed"] = 1
        row["position"] = position
        rows.append(row)
    return rows


def participant_row(team: Any, *, updated_at: Timestamp, sport: Optional[str] = None) -> Optional[Row]:
    """
    Yarışmacı nesnesinden (`homeTeam` / `awayTeam`: takım, tek oyuncu ya da çift) `participants` satırı;
    kimlik yoksa None. sport: nesnenin kendi `sport.slug` alanı yoksa kullanılacak slug.
    """
    if not isinstance(team, Mapping):
        return None
    participant_id = _int(team.get("id"))
    if participant_id is None:
        return None
    own_sport = _text(_obj(team, "sport").get("slug"))
    name = _text(team.get("name"))
    return {
        "id": participant_id,
        "sport": (own_sport or sport or "").lower() or None,
        "name": name,
        "name_folded": _folded(name),
        "short_name": _text(team.get("shortName")),
        "slug": _text(team.get("slug")),
        "name_code": _text(team.get("nameCode")),
        "country": _text(_obj(team, "country").get("alpha2")),
        "gender": _text(team.get("gender")),
        "type": _int(team.get("type")),
        "national": _flag(team.get("national")),
        "updated_at": epoch_seconds(updated_at),
    }


@dataclasses.dataclass(frozen=True)
class EntityRows:
    """Bir olay yükünün içinde geçen varlıkların satırları (olmayan varlık None / boş liste)."""

    sport: Optional[Row]
    category: Optional[Row]
    tournament: Optional[Row]
    season: Optional[Row]
    participants: List[Row]


def event_entity_rows(payload: Mapping[str, Any], *, updated_at: Timestamp,
                      sport: Optional[str] = None) -> EntityRows:
    """
    Olay yükündeki spor, kategori, turnuva, sezon ve iki yarışmacının satırları. `sport`, `event_row`'daki
    ile aynı yedek değerdir. Var olan satırın üzerine yazılıp yazılmayacağına çağıran karar verir
    (dizinleyici turnuva ve sezon satırını yalnızca yoksa ekler; bölüm 3.4, adım 4).
    """
    if not isinstance(payload, Mapping):
        raise PayloadCorrupt(f"The event payload is not an object: {type(payload).__name__}")
    tournament = _obj(payload, "tournament")
    category = _obj(tournament, "category")
    slug = event_sport_slug(_as_dict(payload)) or (str(sport).lower() if sport else None)
    tournament_out = tournament_row(tournament.get("uniqueTournament"), updated_at=updated_at,
                                    category=category, sport=slug)
    participants: List[Row] = []
    for key in ("homeTeam", "awayTeam"):
        row = participant_row(payload.get(key), updated_at=updated_at, sport=slug)
        if row is not None and all(row["id"] != other["id"] for other in participants):
            participants.append(row)
    return EntityRows(
        sport=sport_row(category.get("sport")),
        category=category_row(category, sport=slug),
        tournament=tournament_out,
        season=season_row(payload.get("season"), updated_at=updated_at,
                          tournament_id=tournament_out["id"] if tournament_out else None),
        participants=participants,
    )


__all__ = [
    "DERIVE_VERSION",
    "EVENT_DERIVED_COLUMNS",
    "ROW_SOURCES",
    "SIDE_HOME",
    "SIDE_AWAY",
    "EntityRows",
    "epoch_seconds",
    "fold_name",
    "season_sort_key",
    "scores_json",
    "tier_hint",
    "event_row",
    "event_participant_rows",
    "sport_row",
    "category_row",
    "tournament_row",
    "season_row",
    "season_list_rows",
    "participant_row",
    "event_entity_rows",
]
