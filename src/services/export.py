"""
Dışa aktarma servisi (docs/design/02-services.md 2.7): saklanan maçlardan veri kümesi üretir.

Bugün tek profil vardır: `legacy-wide-csv`, eski `match_details/processed/all_matches_*.csv` dosyalarının
geniş CSV'si (maç başına bir satır: temel bilgiler, "ALL" dönemi istatistikleri, seriler, form, H2H, kadro
sayıları). Düzleştirme src/match_data_fetcher.py'den buraya taşındı (plan maddesi EX-1); maçlar dosya ağacı
gezilerek değil, deponun okuma API'siyle bulunur (`Store.events`), yani iki düzen de (eski `match_details/`
ağacı ve v3) aynı çağrılarla okunur.

Eski dışa aktarmadan farklar (yalnızca eski biçimli kayıtlarda görünür):

  * Bir maç bir kez yazılır. Eski kod düz dizindeki (`match_details/<id>`) maçı iki kez, ayrıca bir lig
    dizininde de duran maçı üç kez yazıyordu; şimdi kataloğun seçtiği kopya (olay yükü en yeni olan) okunur.
  * Satırlar başlangıç zamanı sırasıyladır (eşitlikte kimlik); eskiden dizin listeleme sırasıydı.
  * Yalnızca birleşik dosyası (`<id>/<id>.json`) olan dizin de bir maçtır; eskiden atlanıyordu.
  * Okunamayan bir dilim yalnızca o dilimi, işlenemeyen bir maç yalnızca o maçı düşürür; eskiden bozuk bir
    olay yükü bütün dışa aktarmayı boşaltabiliyordu.

İki kusur bilerek olduğu gibi taşındı (plan maddesi FX-7 düzeltir): `home_formation` / `away_formation` hep
boştur (kod nesne bekler, SofaScore metin gönderir) ve lig süzgeçli indirme dosyayı pandas'tan geçirdiği için
boşluklu tamsayı sütunları `1.0` biçiminde çıkar.

Servis dosya yazmaz; yalnızca istenen akışa ya da yola yazar. Web'in GET'i çıktıyı istekte üretip akıtır
(karar D16); terminal menüsü ve `--headless --csv-export` dosyayı `match_details/processed/` altına yazar
(`export_all_csv`, `write_legacy_csv_files`). Dışa aktarma kilit almaz: katalogdan okur.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
from dataclasses import dataclass
from typing import (TYPE_CHECKING, Any, Callable, Dict, IO, Iterable, Iterator, List, Mapping, Optional, Sequence,
                    Tuple, Union)

from src.errors import NotSupportedError
from src.logger import get_logger
from src.sports import event_sport_slug
from src.store import EventQuery, PayloadCorrupt, PayloadMissing, Scope

if TYPE_CHECKING:
    from src.services.context import ServiceContext
    from src.store import EventRow, Store

logger = get_logger("ExportService")

LEGACY_WIDE_CSV = "legacy-wide-csv"
EVENT_KEY = "event"  # `/event/{id}` yükünün depodaki dilim anahtarı (eski adı `basic`)
# Profilin okuduğu dilimler (olay yükünden sonra)
LEGACY_SLICE_KEYS: Tuple[str, ...] = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups")
# Önce gelen sütunlar (varsa, bu sırayla); kalanlar alfabetik
PRIORITY_COLUMNS: Tuple[str, ...] = ("match_id", "league_folder", "season_folder", "tournament_name", "season_name",
                                     "round", "home_team_name", "away_team_name", "home_score_ft", "away_score_ft",
                                     "match_date")
NO_TOURNAMENT_DIR = "_no_tournament"  # src/match_data_fetcher.py NO_TOURNAMENT_DIR ile aynı
_LEGACY_LAYOUT = "legacy"
_MATCH_DETAILS_DIR = "match_details"
_SORT = "start_asc"
_UNSAFE_NAME = re.compile(r"[^\w]")  # lig başına dosyanın adında "_" olan karakterler


@dataclass(frozen=True)
class ExportSpec:
    """
    Ne dışa aktarılacak.

    dataset / format / profile: bugün yalnızca `events` / `csv` / `legacy-wide-csv` desteklenir; başkası
        NotSupportedError.
    tournament_ids, event_ids: boş = süzgeç yok; dolu alanlar birlikte (VE) uygulanır. Yalnızca detayı
        (olay yükü) saklanan maçlar dışa aktarılır.
    league_id: eski lig süzgeçli indirme (`GET /api/export/csv?league_id=`): birleşik tablonun `league_folder`'ı
        `<lig id>_` ile başlayan satırları; tablo bugünkü gibi pandas'tan geçer (sütunlar birleşik tablonunkiler).
    """

    dataset: str = "events"
    format: str = "csv"
    profile: Optional[str] = LEGACY_WIDE_CSV
    tournament_ids: Tuple[int, ...] = ()
    event_ids: Tuple[int, ...] = ()
    league_id: Optional[int] = None


@dataclass(frozen=True)
class ExportResult:
    """Yazılan dışa aktarma: satır ve sütunlar, UTF-8 bayt sayısı, dosyaya yazıldıysa yolu."""

    rows: int
    columns: Tuple[str, ...]
    bytes: int = 0
    path: Optional[str] = None


@dataclass(frozen=True)
class LegacyTable:
    """
    `legacy-wide-csv` tablosu: sütunlar (öncelikliler önce, kalanlar alfabetik) ve maç başına bir satır.
    Satırda olmayan sütun CSV'de boş kalır.
    """

    columns: Tuple[str, ...]
    rows: Tuple[Dict[str, Any], ...]

    def chunks(self, batch: int = 500) -> Iterator[str]:
        """CSV metni parça parça: başlık, sonra `batch` satırlık parçalar (satır sonu `\\r\\n`)."""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(self.columns))
        writer.writeheader()
        for n, row in enumerate(self.rows, start=1):
            writer.writerow(row)
            if n % batch == 0:
                yield _drain(buffer)
        yield _drain(buffer)


@dataclass(frozen=True)
class PreparedExport:
    """
    Üretilmiş, henüz yazılmamış dışa aktarma. available: süzgeçten (league_id) önceki satır sayısı; rows ile
    birlikte "hiç veri yok" ile "bu lig için veri yok"u ayırır.
    """

    columns: Tuple[str, ...]
    rows: int
    available: int
    _chunks: Callable[[], Iterator[str]]

    def chunks(self) -> Iterator[str]:
        """CSV metni parça parça (bir kez tüketilir gibi düşünülmeli; her çağrı baştan üretir)."""
        return self._chunks()


def _drain(buffer: io.StringIO) -> str:
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return text


class ExportService:
    """Deponun okuma API'si üzerinde dışa aktarma. Durum tutmaz; kilit almaz. Hatalar çağırana çıkar."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    # -- profil ---------------------------------------------------------------------------------------

    def legacy_table(self, spec: Optional[ExportSpec] = None) -> LegacyTable:
        """
        `legacy-wide-csv` tablosu: detayı saklanan her maç için bir satır, başlangıç zamanı sırasıyla. `league_id`
        burada uygulanmaz (`prepare`). Depolama hatası (G/Ç, izin) StoreError olarak çıkar.
        """
        spec = spec or ExportSpec()
        _check(spec)
        rows: List[Dict[str, Any]] = []
        query = EventQuery(scope=Scope(tournament_ids=tuple(spec.tournament_ids), event_ids=tuple(spec.event_ids)),
                           has_details=True, sort=_SORT)
        for event in self._store.events.iter(query):
            found = self._payloads(event.id)
            if EVENT_KEY not in found:  # yük okunamadı (uyarı loglandı) ya da bu arada silindi
                continue
            match_data: Dict[str, Any] = {"basic": found.pop(EVENT_KEY), **found}
            league_folder, season_folder = legacy_folders(event, match_data["basic"])
            try:
                rows.append(legacy_wide_row(str(event.id), match_data, league_folder, season_folder))
            except Exception as e:  # beklenmeyen biçimde bir yük: o maç düşer, dışa aktarma sürer
                logger.warning("Event %s is left out of the CSV export, its payload could not be read: %s",
                               event.id, e)
        return LegacyTable(legacy_columns(rows), tuple(rows))

    def prepare(self, spec: Optional[ExportSpec] = None) -> PreparedExport:
        """Dışa aktarmayı üretir (yazmadan): sütunlar, satır sayısı ve CSV metninin parçaları."""
        spec = spec or ExportSpec()
        table = self.legacy_table(spec)
        if spec.league_id is None:
            return PreparedExport(table.columns, len(table.rows), len(table.rows), table.chunks)
        if not table.rows:
            return PreparedExport((), 0, 0, lambda: iter(()))
        columns, count, text = _league_csv(table, spec.league_id)
        return PreparedExport(columns, count, len(table.rows), lambda: iter((text,)))

    def export(self, spec: ExportSpec, dest: Union[str, "os.PathLike[str]", IO[str]]) -> ExportResult:
        """
        Dışa aktarmayı `dest`'e yazar: bir metin akışı ya da dosya yolu (UTF-8, satır sonu çevirisi yok). Yol
        verilirse dosya satır olmasa da yazılır; boş seçim için dosya istemeyen çağıran önce `prepare`'e bakar.
        """
        return self._write(self.prepare(spec), dest)

    def _write(self, prepared: PreparedExport, dest: Union[str, "os.PathLike[str]", IO[str]]) -> ExportResult:
        if isinstance(dest, (str, os.PathLike)):
            path = os.fspath(dest)
            size = _write_file(path, prepared.chunks())
            return ExportResult(prepared.rows, prepared.columns, size, path)
        size = 0
        for chunk in prepared.chunks():
            dest.write(chunk)
            size += len(chunk.encode("utf-8"))
        return ExportResult(prepared.rows, prepared.columns, size, None)

    # -- dosyaya yazan eski girişler (terminal menüsü, --headless --csv-export) ---------------------------

    def write_legacy_csv(self, directory: str, spec: Optional[ExportSpec] = None, *,
                         now: Optional[float] = None) -> Optional[ExportResult]:
        """
        Birleşik dosya: `<directory>/all_matches_<epoch>.csv`. Dışa aktarılacak maç yoksa dosya yazılmaz ve None
        döner (bugünkü gibi).
        """
        table = self.legacy_table(spec)
        if not table.rows:
            logger.warning("No downloaded match to export to CSV")
            return None
        path = os.path.join(directory, f"all_matches_{_stamp(now)}.csv")
        result = self._write(PreparedExport(table.columns, len(table.rows), len(table.rows), table.chunks), path)
        logger.info("CSV export of %s matches written: %s", result.rows, path)
        return result

    def write_legacy_csv_by_league(self, directory: str, spec: Optional[ExportSpec] = None, *,
                                   now: Optional[float] = None) -> List[ExportResult]:
        """
        Lig başına bir dosya: `<directory>/<lig>_<epoch>.csv`. Lig, satırın `league_folder`'ı, yoksa turnuva adıdır
        (bugünkü gibi); her dosyanın sütunları yalnızca kendi satırlarından çıkar.
        """
        table = self.legacy_table(spec)
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in table.rows:
            key = row.get("league_folder", row.get("tournament_name", "Unknown"))
            groups.setdefault(str(key if key is not None else "Unknown"), []).append(row)
        stamp = _stamp(now)
        results: List[ExportResult] = []
        for league, rows in groups.items():
            part = LegacyTable(legacy_columns(rows), tuple(rows))
            path = os.path.join(directory, f"{_UNSAFE_NAME.sub('_', league)}_{stamp}.csv")
            results.append(self._write(PreparedExport(part.columns, len(rows), len(rows), part.chunks), path))
            logger.info("CSV export of league %s written: %s", league, path)
        if not results:
            logger.warning("No downloaded match to export to CSV")
        return results

    def _payloads(self, event_id: int) -> Dict[str, Any]:
        """
        Maçın profilde kullanılan dilimlerinden yükü olanlar. Bir dilim okunamıyorsa (katalog güncellendikten sonra
        silinmiş ya da bozulmuş) dilimler tek tek okunur ve okunamayan dilim yok sayılır.
        """
        keys = (EVENT_KEY, *LEGACY_SLICE_KEYS)
        try:
            return self._store.events.payloads(event_id, keys)
        except (PayloadMissing, PayloadCorrupt):
            pass
        found: Dict[str, Any] = {}
        for key in keys:
            try:
                found.update(self._store.events.payloads(event_id, (key,)))
            except (PayloadMissing, PayloadCorrupt) as e:
                logger.warning("Event %s: the stored %s payload is unreadable and is left out of the export: %s",
                               event_id, key, e)
        return found


def _check(spec: ExportSpec) -> None:
    if spec.dataset != "events" or spec.format != "csv" or spec.profile != LEGACY_WIDE_CSV:
        raise NotSupportedError(
            f"Export of dataset {spec.dataset!r} as {spec.format!r} with profile {spec.profile!r} is not supported",
            {"dataset": spec.dataset, "format": spec.format, "profile": spec.profile})


def _stamp(now: Optional[float]) -> int:
    return int(time.time() if now is None else now)


def _write_file(path: str, chunks: Iterable[str]) -> int:
    """Dosyayı yazar (UTF-8, satır sonu çevirisi yok); yazılan bayt sayısını döndürür."""
    size = 0
    with open(path, "w", encoding="utf-8", newline="") as f:
        for chunk in chunks:
            f.write(chunk)
            size += len(chunk.encode("utf-8"))
    return size


def _league_csv(table: LegacyTable, league_id: int) -> Tuple[Tuple[str, ...], int, str]:
    """
    Eski lig süzgeci: birleşik CSV pandas'tan geçer, `league_folder`'ı `<lig id>_` ile başlayan satırlar kalır.
    pandas tür çıkarımı yaptığı için boşluklu tamsayı sütunları `1.0` olur (FX-7 düzeltir).
    """
    import pandas as pd

    df = pd.read_csv(io.StringIO("".join(table.chunks())), low_memory=False)
    if "league_folder" in df.columns:
        df = df[df["league_folder"].astype(str).str.startswith(f"{league_id}_")]
    if df.empty:
        return tuple(str(c) for c in df.columns), 0, ""
    return tuple(str(c) for c in df.columns), len(df), df.to_csv(index=False)


# -- satır kuralı ---------------------------------------------------------------------------------------

def legacy_columns(rows: Sequence[Mapping[str, Any]]) -> Tuple[str, ...]:
    """Satırlarda geçen sütunlar: `PRIORITY_COLUMNS`'tan olanlar o sırayla, kalanlar alfabetik."""
    present = set()
    for row in rows:
        present.update(row.keys())
    head = [c for c in PRIORITY_COLUMNS if c in present]
    return tuple(head + sorted(present.difference(head)))


def legacy_folders(event: "EventRow", basic: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Satırın `league_folder` ve `season_folder` değerleri. Eski düzende lig/sezon/maç derinliğindeki kayıt için
    dizin adları (`season_` önekleri atılmış); düz kayıtta (`match_details/<id>`) ikisi de yok. Eski düzende
    olmayan kayıtta, eski yazıcının o maç için seçeceği adlar (src/match_data_fetcher.py `_match_storage_dir`).
    """
    if event.layout == _LEGACY_LAYOUT:
        parts = (event.path or "").split("/")
        if len(parts) == 4 and parts[0] == _MATCH_DETAILS_DIR:
            return parts[1], parts[2].replace("season_", "")
        return None, None
    basic = basic if isinstance(basic, dict) else {}
    tournament = (basic.get("tournament") or {}).get("uniqueTournament") or {}
    tournament_id = tournament.get("id")
    if not tournament_id:
        return NO_TOURNAMENT_DIR, _safe_part(event_sport_slug(basic) or "unknown")
    league = f"{tournament_id}_{_dir_name(tournament.get('name') or 'Unknown_League')}"
    season = basic.get("season") or {}
    name, year = season.get("name", "Unknown_Season"), season.get("year", "Unknown_Year")
    if name and name != "Unknown_Season":
        folder = f"season_{_dir_name(name)}"
    elif year and year != "Unknown_Year":
        folder = f"season_{str(year).replace('/', '_')}"
    else:
        folder = f"season_{season.get('id')}"
    return league, folder.replace("season_", "")


def _dir_name(name: Any) -> str:
    return str(name).replace(" ", "_").replace("/", "_")


def _safe_part(name: str) -> str:
    """src/match_data_fetcher.py `_path_part` ile aynı kural: dizin adında güvenli tek parça."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name)).strip(".") or "unknown"


def legacy_wide_row(match_id: str, match_data: Mapping[str, Any], league_folder: Optional[str] = None,
                    season_folder: Optional[str] = None) -> Dict[str, Any]:
    """
    Bir maçın `legacy-wide-csv` satırı; `match_data` dilim adı → yük (`basic`, `statistics`, `team_streaks`,
    `pregame_form`, `h2h`, `lineups`). src/match_data_fetcher.py `process_match_for_csv`'nin kuralı, değişmeden.
    """
    processed: Dict[str, Any] = {
        # Basic match info
        "match_id": match_id,
        "tournament_id": None,
        "tournament_name": None,
        "season_id": None,
        "season_name": None,
        "season_year": None,
        "round": None,
        "home_team_id": None,
        "home_team_name": None,
        "away_team_id": None,
        "away_team_name": None,
        "home_score_ht": None,
        "away_score_ht": None,
        "home_score_ft": None,
        "away_score_ft": None,
        "match_date": None,
        "venue": None,
        "referee": None,
        "status": None,
    }

    if league_folder:
        processed["league_folder"] = league_folder
    if season_folder:
        processed["season_folder"] = season_folder

    if "basic" in match_data and match_data["basic"]:
        basic = match_data["basic"]
        processed.update({
            "tournament_id": basic.get("tournament", {}).get("uniqueTournament", {}).get("id"),
            "tournament_name": basic.get("tournament", {}).get("uniqueTournament", {}).get("name"),
            "season_id": basic.get("season", {}).get("id"),
            "season_name": basic.get("season", {}).get("name"),
            "season_year": basic.get("season", {}).get("year"),
            "round": basic.get("roundInfo", {}).get("round"),
            "home_team_id": basic.get("homeTeam", {}).get("id"),
            "home_team_name": basic.get("homeTeam", {}).get("name"),
            "away_team_id": basic.get("awayTeam", {}).get("id"),
            "away_team_name": basic.get("awayTeam", {}).get("name"),
            "home_score_ht": basic.get("homeScore", {}).get("period1"),
            "away_score_ht": basic.get("awayScore", {}).get("period1"),
            "home_score_ft": basic.get("homeScore", {}).get("normaltime"),
            "away_score_ft": basic.get("awayScore", {}).get("normaltime"),
            "match_date": basic.get("startTimestamp"),
            "venue": basic.get("venue", {}).get("name"),
            "referee": basic.get("referee", {}).get("name"),
            "status": basic.get("status", {}).get("description"),
        })

    # "ALL" döneminin istatistikleri
    if "statistics" in match_data and match_data["statistics"]:
        for period in match_data["statistics"].get("statistics", []):
            if period.get("period") == "ALL":
                for group in period.get("groups", []):
                    for item in group.get("statisticsItems", []):
                        key = item.get("key")
                        if key:
                            processed[f"home_{key}"] = item.get("homeValue")
                            processed[f"away_{key}"] = item.get("awayValue")

    # Takım serileri
    if "team_streaks" in match_data and match_data["team_streaks"]:
        for streak in match_data["team_streaks"].get("general", []):
            team = streak.get("team", "")
            if team in ["home", "away"]:
                name = f"{team}_streak_{streak.get('name', '').lower().replace(' ', '_')}"
                processed[name] = streak.get("value")
                processed[f"{name}_continued"] = streak.get("continued", False)

    # Maç öncesi form
    if "pregame_form" in match_data and match_data["pregame_form"]:
        home_form = match_data["pregame_form"].get("homeTeam", {})
        away_form = match_data["pregame_form"].get("awayTeam", {})
        processed.update({
            "home_position": home_form.get("position"),
            "away_position": away_form.get("position"),
            "home_points": home_form.get("value"),
            "away_points": away_form.get("value"),
            "home_rating": home_form.get("avgRating"),
            "away_rating": away_form.get("avgRating"),
            "home_form": "_".join(home_form.get("form", [])) if home_form.get("form") else None,
            "away_form": "_".join(away_form.get("form", [])) if away_form.get("form") else None,
        })

    # Aralarındaki maçlar
    if "h2h" in match_data and match_data["h2h"]:
        h2h = match_data["h2h"].get("teamDuel", {})
        processed.update({
            "h2h_home_wins": h2h.get("homeWins"),
            "h2h_away_wins": h2h.get("awayWins"),
            "h2h_draws": h2h.get("draws"),
        })

    # Kadrolar: onay, ilk on bir ve yedek sayıları, diziliş (diziliş sütunu bugün hep boş kalır: FX-7)
    if "lineups" in match_data and match_data["lineups"]:
        lineups = match_data["lineups"]
        processed["lineups_confirmed"] = lineups.get("confirmed", False) if isinstance(lineups, dict) else False
        for side in ("home", "away"):
            if isinstance(lineups, dict) and side in lineups and isinstance(lineups[side], dict):
                lineup = lineups[side]
                if "players" in lineup and isinstance(lineup["players"], list):
                    players = lineup["players"]
                    processed[f"{side}_starting_xi_count"] = sum(1 for p in players if p.get("substitute") is False)
                    processed[f"{side}_substitutes_count"] = sum(1 for p in players if p.get("substitute") is True)
                if "formation" in lineup and isinstance(lineup["formation"], dict):
                    processed[f"{side}_formation"] = lineup["formation"].get("name")
                else:
                    processed[f"{side}_formation"] = None

    return processed


# -- eski girişler ----------------------------------------------------------------------------------------

def export_all_csv(ctx: "ServiceContext") -> Optional[str]:
    """
    İndirilmiş bütün maçları tek CSV dosyasına yazar (`match_details/processed/all_matches_<epoch>.csv`); dosyanın
    yolunu döndürür. `--headless --csv-export`in adımıdır.

    None: dosya üretilmedi (maç yok ya da hata). Hata yutulur ve loglanır (P08'in sözleşmesi; çıkış kodu P19'un
    işidir). FetchCancelled BaseException olduğu için buradan geçer.
    """
    try:
        result = ctx.match_data_fetcher.convert_all_matches_to_csv()
    except Exception as exc:
        logger.error("CSV export failed: %s", exc)
        return None
    # `separate_by_league` verilmediği için sonuç tek bir yoldur; boş metin "üretilemedi" demektir
    return result if isinstance(result, str) and result else None


__all__ = ["ExportResult", "ExportService", "ExportSpec", "LEGACY_WIDE_CSV", "LegacyTable", "PreparedExport",
           "export_all_csv", "legacy_columns", "legacy_folders", "legacy_wide_row"]
