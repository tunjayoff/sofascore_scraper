"""
Dışa aktarma servisi (docs/design/02-services.md 2.7): saklanan maçlardan veri kümesi üretir.

İki tür dışa aktarma vardır:

  * Veri kümeleri (plan maddesi SC-2; `DatasetSpec`): `events`, `slices` ve `changes`, normalleştirilmiş
    şemanın (docs/design/04-schema-v1.md) kayıtları olarak JSONL, CSV, Parquet ya da SQLite; ya da saklanan
    ham yükler olarak (`schema="raw"`, yalnızca `events` ve `slices`). Aşağıda "Veri kümeleri" bölümü.
  * Tek profil: `legacy-wide-csv`, eski `match_details/processed/all_matches_*.csv` dosyalarının
geniş CSV'si (maç başına bir satır: temel bilgiler, "ALL" dönemi istatistikleri, seriler, form, H2H, kadro
sayıları). Düzleştirme 2.x'in match_data_fetcher.py modülünden buraya taşındı (plan maddesi EX-1); maçlar dosya ağacı
gezilerek değil, deponun okuma API'siyle bulunur (`Store.events`), yani iki düzen de (eski `match_details/`
ağacı ve v3) aynı çağrılarla okunur.

Eski dışa aktarmadan farklar (yalnızca eski biçimli kayıtlarda görünür):

  * Bir maç bir kez yazılır. Eski kod düz dizindeki (`match_details/<id>`) maçı iki kez, ayrıca bir lig
    dizininde de duran maçı üç kez yazıyordu; şimdi kataloğun seçtiği kopya (olay yükü en yeni olan) okunur.
  * Satırlar başlangıç zamanı sırasıyladır (eşitlikte kimlik); eskiden dizin listeleme sırasıydı.
  * Yalnızca birleşik dosyası (`<id>/<id>.json`) olan dizin de bir maçtır; eskiden atlanıyordu.
  * Okunamayan bir dilim yalnızca o dilimi, işlenemeyen bir maç yalnızca o maçı düşürür; eskiden bozuk bir
    olay yükü bütün dışa aktarmayı boşaltabiliyordu.

EX-1'in olduğu gibi taşıdığı iki kusur giderildi (plan maddesi FX-7): `home_formation` / `away_formation`
doludur (SofaScore dizilişi metin olarak gönderir, `"4-2-3-1"`; eski kod nesne beklediği için bu sütunlar hep
boştu) ve lig süzgeçli indirme artık pandas'tan geçmez: satırları birleşik dışa aktarmadakiyle aynıdır, boşluklu
tamsayı sütunları `1.0` biçiminde çıkmaz, satır sonu her yerde `\\r\\n`'dir.

Katılımcı süzgeci (B1, e2e F13: dışa aktarma yalnızca lig takibiyle süzülebiliyordu): `team_ids` takımların
(yarışmacı kimliği, `homeTeam.id`; bireysel sporlarda oyuncu ya da çift) maçlarını, `player_ids` oyuncuların (kişi
kimliği, kadrodaki `player.id`) maçlarını seçer. İkisi tek bir süzgeçtir: maç listelenen takımlardan ya da
oyunculardan birinindir; öteki süzgeçlerle birlikte (VE) uygulanır. Bir oyuncunun maçları iki kaynağın
birleşimidir: önce oyuncu takibinin saklanan maç listesi (B2; state.db'nin çalışma zamanı bilgisi
`follow_events:player:<kimlik>`, sofascore_scraper/services/follow_sync.py `listed_events`), sonra saklanan
kadrolar (`lineups` dilimi: ilk on bir ve yedekler; eksik oyuncular sayılmaz). Böylece takip edilen oyuncunun
kadrosu indirilmemiş maçı da bulunur; takip edilmeyen (ya da listesi henüz okunmamış) oyuncunun kadrosu
saklanmamış maçını bu süzgeç bulmaz. Süzgeç dışa aktarmadan önce maç kimliklerine çözülür
(`ExportService.participant_events`); bunun için öteki süzgeçlerin kapsamındaki (spor, turnuva, sezon, maç)
maçların kadroları okunur (listeden bulunan maçınki okunmaz).

Servis dosya yazmaz; yalnızca istenen akışa ya da yola yazar. Web'in GET'i çıktıyı istekte üretip akıtır
(karar D16); `ssc export --profile legacy-wide-csv` (2.x'te `--headless --csv-export`) dosyayı
`match_details/processed/` altına yazar (`write_legacy_csv`). Dışa aktarma kilit almaz: katalogdan okur.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime as _dt
import importlib.util
import io
import itertools
import os
import re
import time
import types
import typing
from dataclasses import dataclass, field
from typing import (TYPE_CHECKING, Any, BinaryIO, Callable, Dict, FrozenSet, IO, Iterable, Iterator, List, Mapping,
                    Optional, Sequence, Set, Tuple, Type, Union)

from sofascore_scraper.errors import NotFoundError, NotSupportedError, UsageError
from sofascore_scraper.logger import get_logger
from sofascore_scraper.sports import event_sport_slug
from sofascore_scraper.store import EventQuery, PayloadCorrupt, PayloadMissing, Scope, StoreError

if TYPE_CHECKING:
    from sofascore_scraper.schema.models import Model
    from sofascore_scraper.store import EventRow, Store

logger = get_logger("ExportService")

LEGACY_WIDE_CSV = "legacy-wide-csv"
EVENT_KEY = "event"  # `/event/{id}` yükünün depodaki dilim anahtarı (eski adı `basic`)
# Profilin okuduğu dilimler (olay yükünden sonra)
LEGACY_SLICE_KEYS: Tuple[str, ...] = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups")
# Önce gelen sütunlar (varsa, bu sırayla); kalanlar alfabetik
PRIORITY_COLUMNS: Tuple[str, ...] = ("match_id", "league_folder", "season_folder", "tournament_name", "season_name",
                                     "round", "home_team_name", "away_team_name", "home_score_ft", "away_score_ft",
                                     "match_date")
NO_TOURNAMENT_DIR = "_no_tournament"  # sofascore_scraper/store/legacy.py NO_TOURNAMENT_DIR ile aynı (2.x yazıcısının adı)
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
    team_ids, player_ids: katılımcı süzgeci (modül belgesi); ikisi birlikte tek süzgeçtir.
    league_id: 2.x'in lig süzgeçli indirmesi (3.1'de kalkan `GET /api/export/csv?league_id=`): birleşik tablonun `league_folder`'ı
        `<lig id>_` ile başlayan satırları, birleşik tablodaki değerleriyle (sütunlar birleşik tablonunkiler).
    """

    dataset: str = "events"
    format: str = "csv"
    profile: Optional[str] = LEGACY_WIDE_CSV
    tournament_ids: Tuple[int, ...] = ()
    event_ids: Tuple[int, ...] = ()
    league_id: Optional[int] = None
    team_ids: Tuple[int, ...] = ()
    player_ids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class ExportResult:
    """
    Yazılan dışa aktarma: satır ve sütunlar, UTF-8 bayt sayısı, dosyaya yazıldıysa yolu.

    Veri kümelerinde ayrıca: events (dosyanın kapsadığı farklı maç sayısı: ham dışa aktarmada en az bir yükü yazılan
    maç, normalleştirilmişte kayıtların maçı, yani `events` kümesinde satır sayısı, dilim, değişiklik ve oranlarda
    farklı maç; puan durumu maça bağlı değildir, 0; FX-26, canlı doğrulama M18), skipped (ham dışa aktarmada okunamayıp atlanan yükler, `ExportSkip`), schema_version (normalleştirilmiş
    kayıtların şema sürümü; ham ve geniş CSV için None). Ham dışa aktarmada `rows` yazılan yük sayısıdır ve
    `columns` boştur.
    """

    rows: int
    columns: Tuple[str, ...]
    bytes: int = 0
    path: Optional[str] = None
    events: int = 0
    skipped: Tuple[Any, ...] = ()
    schema_version: Optional[int] = None


def _counting_events(dataset: str, records: Iterable[Dict[str, Any]], seen: Set[int]) -> Iterator[Dict[str, Any]]:
    """
    Kayıtları olduğu gibi verir ve maçlarını `seen`'e ekler: `events` kümesinde `id`, dilimlerde sahibi maç olanın
    `owner_id`'si, değişiklik ve oranlarda `event_id`.
    """
    for record in records:
        if dataset == DATASET_EVENTS:
            value = record.get("id")
        elif dataset == DATASET_SLICES:
            value = record.get("owner_id") if record.get("owner_kind") == "event" else None
        else:
            value = record.get("event_id")
        if isinstance(value, int) and not isinstance(value, bool):
            seen.add(value)
        yield record


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
        event_ids = self._narrowed_events(Scope(tournament_ids=tuple(spec.tournament_ids),
                                                event_ids=tuple(spec.event_ids)), spec.team_ids, spec.player_ids)
        query = EventQuery(scope=Scope(tournament_ids=tuple(spec.tournament_ids), event_ids=event_ids),
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
        part = LegacyTable(table.columns, _league_rows(table, spec.league_id))
        if not part.rows:
            return PreparedExport(table.columns, 0, len(table.rows), lambda: iter(()))
        return PreparedExport(table.columns, len(part.rows), len(table.rows), part.chunks)

    def export(self, spec: Union[ExportSpec, "DatasetSpec"], dest: Union[str, "os.PathLike[str]", IO[str], BinaryIO],
               *, overwrite: bool = False, allow_empty: bool = True) -> ExportResult:
        """
        Dışa aktarmayı `dest`'e yazar.

        ExportSpec (`legacy-wide-csv`): `dest` bir metin akışı ya da dosya yolu (UTF-8, satır sonu çevirisi yok).
            Yol verilirse dosya satır olmasa da yazılır ve var olan dosyanın üzerine yazılır; boş seçim için dosya
            istemeyen çağıran önce `prepare`'e bakar. `overwrite` ve `allow_empty` okunmaz.
        DatasetSpec: `export_dataset`.
        """
        if isinstance(spec, DatasetSpec):
            return self.export_dataset(spec, dest, overwrite=overwrite, allow_empty=allow_empty)  # type: ignore[arg-type]
        return self._write(self.prepare(spec), dest)  # type: ignore[arg-type]

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

    # -- dosyaya yazan girişler (`ssc export --profile legacy-wide-csv`; 2.x'te --headless --csv-export) ------

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

    # -- katılımcı süzgeci (B1) -----------------------------------------------------------------------------

    def participant_events(self, team_ids: Iterable[int] = (), player_ids: Iterable[int] = (),
                           scope: Optional[Scope] = None) -> Set[int]:
        """
        Kapsamdaki maçlardan takımlardan ya da oyunculardan birinin oynadığı maçların kimlikleri (modül belgesi):
        takım katalogdaki iki tarafından biridir; oyuncu takibinin saklanan maç listesindedir ya da maçın saklanan
        kadrosundadır. Okunamayan bir kadro atlanır (uyarı loglanır).
        """
        teams, players = tuple(team_ids), frozenset(player_ids)
        base = scope or Scope()
        found: Set[int] = set()
        if teams:
            query = EventQuery(scope=dataclasses.replace(base, participant_ids=teams), sort=_SORT)
            found.update(row.id for row in self._store.events.iter(query, batch=_PAGE))
        if players:
            listed = self._listed_player_events(players, base)
            found.update(listed)
            read = 0
            for state in self._store.events.states(base, batch=_PAGE):
                if state.event.id in found or not state.slice(LINEUPS_KEY).has_payload:
                    continue
                read += 1
                try:
                    lineups = self._store.events.payload(state.event.id, LINEUPS_KEY)
                except (PayloadMissing, PayloadCorrupt) as e:
                    logger.warning("Event %s: the stored lineups are unreadable and the player filter skips it: %s",
                                   state.event.id, e)
                    continue
                if players & lineup_players(lineups):
                    found.add(state.event.id)
            logger.info("Player filter: %d stored lineups read, %d matches from the stored match lists of player "
                        "follows, %d matches of the players", read, len(listed), len(found))
        return found

    def _listed_player_events(self, players: Iterable[int], scope: Scope) -> Set[int]:
        """
        Oyuncu takiplerinin saklanan maç listelerindeki (B2, `follow_sync.listed_events`) maçlardan katalogda ve
        kapsamda olanlar. Listesi olmayan (takip edilmeyen ya da listesi henüz okunmamış) oyuncu bir şey katmaz.
        """
        from sofascore_scraper.services.follow_sync import PLAYER, listed_events

        wanted: Dict[int, None] = {}
        for player in sorted(players):
            for event_id in listed_events(self._store, PLAYER, player) or ():
                wanted.setdefault(int(event_id))
        ids = list(wanted)
        if scope.event_ids:
            allowed = {int(event_id) for event_id in scope.event_ids}
            ids = [event_id for event_id in ids if event_id in allowed]
        found: Set[int] = set()
        for start in range(0, len(ids), _PAGE):
            chunk = tuple(ids[start:start + _PAGE])
            query = EventQuery(scope=dataclasses.replace(scope, event_ids=chunk), sort=_SORT)
            found.update(row.id for row in self._store.events.iter(query, batch=_PAGE))
        return found

    def _narrowed_events(self, scope: Scope, team_ids: Iterable[int],
                         player_ids: Iterable[int]) -> Tuple[int, ...]:
        """
        Kapsamın maç süzgeci, katılımcı süzgeciyle daraltılmış: katılımcı yoksa kapsamınki olduğu gibi; varsa
        uyan maçlar (kapsamın maç süzgeciyle kesişimi zaten), hiçbiri uymuyorsa hiçbir maçı seçmeyen `(_NO_EVENT,)`.
        """
        teams, players = tuple(team_ids), tuple(player_ids)
        if not teams and not players:
            return tuple(scope.event_ids)
        found = self.participant_events(teams, players, scope)
        return tuple(sorted(found)) or (_NO_EVENT,)

    def _narrowed(self, flt: "DatasetFilter") -> "DatasetFilter":
        """Süzgecin katılımcıları maç kimliklerine çözülmüş hali (`team_ids` ve `player_ids` boşalır)."""
        if not flt.team_ids and not flt.player_ids:
            return flt
        scope = Scope(sport=flt.sport, tournament_ids=tuple(flt.tournament_ids), season_ids=tuple(flt.season_ids),
                      event_ids=tuple(flt.event_ids))
        events = self._narrowed_events(scope, flt.team_ids, flt.player_ids)
        return dataclasses.replace(flt, event_ids=events, team_ids=(), player_ids=())

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

    # -- veri kümeleri (SC-2) -----------------------------------------------------------------------------

    def export_dataset(self, spec: "DatasetSpec", dest: Union[str, "os.PathLike[str]", BinaryIO], *,
                       overwrite: bool = False, allow_empty: bool = True) -> ExportResult:
        """
        Bir veri kümesini `dest`'e yazar: bir yol ya da ikili akış (ör. HTTP yanıtı; kapatılmaz). Ham dışa
        aktarma yalnızca bir yola yazar.

        overwrite: hedef doluysa üzerine yaz; verilmezse StoreError (depolama hatası).
        allow_empty: False ise seçimde kayıt yoksa hiçbir şey yazılmaz ve NotFoundError.

        Geçersiz birleşim UsageError; Parquet için `pyarrow` yoksa NotSupportedError (hiçbir şey yazılmaz).
        """
        check_dataset(spec)
        if spec.schema == RAW:
            return self._raw_dataset(spec, dest, overwrite=overwrite, allow_empty=allow_empty)
        from sofascore_scraper.schema import SCHEMA_VERSION

        columns = dataset_columns(spec.dataset, spec.format)
        events: Set[int] = set()
        records = _counting_events(spec.dataset, (record.to_dict() for record in self.records(spec.dataset, spec.filter)),
                                   events)
        if spec.format == "jsonl":
            rows: Iterable[Mapping[str, Any]] = records  # kayıt olduğu gibi, iç içe (sözleşmenin JSON'u)
        else:
            paths = _column_paths(spec.dataset)
            rows = (flatten_record(record, paths) for record in records)
        rows = _first_or_raise(rows, spec, allow_empty)
        try:
            report = self._store.export.rows(rows, columns, dest, spec.format,  # type: ignore[arg-type]
                                             table=spec.dataset, overwrite=overwrite,
                                             types=column_types(spec.dataset) if spec.format == "parquet" else None)
        except StoreError as e:
            if e.detail == PARQUET_PACKAGE:
                raise _parquet_missing(spec) from e
            raise
        logger.info("Dataset %s exported as %s: %d rows, %d bytes", spec.dataset, spec.format, report.items,
                    report.bytes)
        return ExportResult(report.items, columns, report.bytes, report.dest or None, events=len(events),
                            schema_version=SCHEMA_VERSION)

    def records(self, dataset: str, flt: Optional["DatasetFilter"] = None) -> Iterator["Model"]:
        """
        Veri kümesinin kayıtları (şema v1), dışa aktarmanın sırasıyla:

          events   Event; başlangıç zamanı sırasıyla (eşitlikte kimlik; başlangıcı bilinmeyenler başta), listeden
                   bilinen maçlar da (`quality.source` "listing")
          slices   Slice; maçlar `events` sırasıyla, her maçın katalogdaki dilim satırları (anahtar, alt anahtar)
                   sırasıyla. Yük (`payload`) null'dır: yükler ham dışa aktarmadadır.
          changes  Change; günlüğün sıra numarasıyla (`seq`). Süzgeçten yalnızca spor, turnuva, maç ve zaman
                   aralığı uygulanır; zaman aralığı kaydın zamanıdır (`recorded_at_utc`), maçın başlangıcı değil.
          odds     OddsLine (P28); maçlar `events` sırasıyla, her maçın `odds_all` ve `odds_featured`
                   dilimlerinin her anlık görüntüsünün her pazar seçeneği bir satır (geçmişi olmayan dilimde
                   saklanan son yük). Oranlar yalnızca seçildilerse indirilir; yoksa veri kümesi boştur.
          standings
                   StandingsRow (P28); süzgece uyan maçların sezonları (turnuva, sezon; ilk görülme sırasıyla),
                   her sezonun saklanan puan durumu tabloları, sıra düzeniyle.

        Katılımcı süzgeci (`team_ids`, `player_ids`) önce maç kimliklerine çözülür (modül belgesi).
        """
        flt = self._narrowed(flt or DatasetFilter())
        if dataset == DATASET_EVENTS:
            return self._events(flt)
        if dataset == DATASET_SLICES:
            return self._slices(flt)
        if dataset == DATASET_CHANGES:
            return self._changes(flt)
        if dataset == DATASET_ODDS:
            return self._odds(flt)
        if dataset == DATASET_STANDINGS:
            return self._standings(flt)
        raise ValueError(f"dataset: expected one of {', '.join(DATASETS)}, got {dataset!r}")

    def _events(self, flt: "DatasetFilter") -> Iterator["Model"]:
        from sofascore_scraper import schema
        from sofascore_scraper.services.query import refresh_window_seconds

        window = refresh_window_seconds()
        for row in self._store.events.iter(_event_query(flt), batch=_PAGE):
            yield schema.event_from_row(row, refresh_window_s=window)

    def _slices(self, flt: "DatasetFilter") -> Iterator["Model"]:
        from sofascore_scraper import schema

        rows = self._store.events.iter(_event_query(flt), batch=_PAGE)
        while True:
            ids = [row.id for row in itertools.islice(rows, _PAGE)]
            if not ids:
                return
            found = {state.event.id: state.slices
                     for state in self._store.events.states(Scope(event_ids=tuple(ids)), batch=_PAGE)}
            for event_id in ids:
                for info in found.get(event_id, ()):
                    yield schema.slice_from_info(info)

    def _odds(self, flt: "DatasetFilter") -> Iterator["Model"]:
        from sofascore_scraper.services.owner_data import OwnerDataService

        data = OwnerDataService(self._store)
        rows = self._store.events.iter(_event_query(flt), batch=_PAGE)
        while True:
            ids = [row.id for row in itertools.islice(rows, _PAGE)]
            if not ids:
                return
            yield from data.odds_lines(ids)

    def _standings(self, flt: "DatasetFilter") -> Iterator["Model"]:
        from sofascore_scraper.services.owner_data import OwnerDataService
        from sofascore_scraper.store import Ref

        seasons: Dict[Tuple[int, int], None] = {}
        for row in self._store.events.iter(_event_query(flt), batch=_PAGE):
            if row.tournament_id is not None and row.season_id is not None:
                seasons.setdefault((int(row.tournament_id), int(row.season_id)), None)
        yield from OwnerDataService(self._store).standings_rows(Ref.season(t, s) for t, s in seasons)

    def _changes(self, flt: "DatasetFilter") -> Iterator["Model"]:
        from sofascore_scraper import schema

        tournaments, events = set(flt.tournament_ids), set(flt.event_ids)
        single = flt.event_ids[0] if len(events) == 1 else None
        after = 0
        while True:
            batch = self._store.changes.list(after_seq=after, event_id=single, since=flt.start_from, limit=_PAGE)
            for row in batch:
                if ((flt.sport is None or row.sport == flt.sport)
                        and (not tournaments or row.tournament_id in tournaments)
                        and (not events or row.event_id in events)
                        and (flt.start_to is None or row.ts <= flt.start_to)):
                    yield schema.change_from_row(row)
            if len(batch) < _PAGE:
                return
            after = batch[-1].seq

    def _raw_dataset(self, spec: "DatasetSpec", dest: Any, *, overwrite: bool, allow_empty: bool) -> ExportResult:
        if _is_stream(dest):
            raise UsageError("A raw export is written to a path, not to a stream.", {"schema": RAW})
        query = _event_query(self._narrowed(spec.filter), has_details=True)
        if not allow_empty and self._store.events.count(query) == 0:
            raise NotFoundError("there is no downloaded match to export", _not_found_details(spec))
        keys = (EVENT_KEY,) if spec.dataset == DATASET_EVENTS else None
        report = self._store.export.raw(query, dest, keys=keys, fmt=spec.format,  # type: ignore[arg-type]
                                        overwrite=overwrite)
        return ExportResult(report.items, (), report.bytes, report.dest or None, events=report.events,
                            skipped=tuple(report.skipped))


def _check(spec: ExportSpec) -> None:
    if spec.dataset != "events" or spec.format != "csv" or spec.profile != LEGACY_WIDE_CSV:
        raise NotSupportedError(
            f"Export of dataset {spec.dataset!r} as {spec.format!r} with profile {spec.profile!r} is not supported",
            {"dataset": spec.dataset, "format": spec.format, "profile": spec.profile})


# -- veri kümeleri (plan maddesi SC-2) ------------------------------------------------------------------
#
# Kayıtlar şema katmanından gelir (sofascore_scraper/schema; docs/design/04-schema-v1.md) ve Store'un satır yazıcısıyla
# (`Store.export.rows`) yazılır; ham dışa aktarma `Store.export.raw`'dır.
#
#   JSONL    satır başına bir kayıt, sözleşmenin JSON'u olduğu gibi (iç içe; `to_dict()`)
#   CSV, Parquet, SQLite
#            düzleştirilmiş: her yaprak alan bir sütundur, adı yolun `_` ile birleşimidir (`status_class`,
#            `score_home`, `quality_observed_at_utc`; şema belgesinin 23. kararı). Sütunlar modellerden çıkar,
#            veriden değil: her kayıt bütün sütunlara sahiptir ve değeri olmayan sütun boştur (null; karar 22).
#            Skor bir birleşimdir (spor ailesine göre yapı): bütün ailelerin alanları sütundur, kaydın ailesinde
#            olmayanlar boştur. Listeler (`score_periods`, `score_sets`, `fields`) JSON metnidir; SQLite'ta da
#            (alt tablo yok: bir dışa aktarma tek bir tablodur, adı veri kümesinin adıdır). Parquet sütunlarının
#            türü de modellerden gelir (`column_types`): tamsayı alan, değeri olmasa da int64'tür.
#
# Şema sürümü kayıtta değil, kaydı taşıyan kaptadır (karar 19): dışa aktarmanın sonucunda (`ExportResult`,
# iş kaydı, `ssc export --json`) `schema_version` olarak.

DATASET_EVENTS = "events"
DATASET_SLICES = "slices"
DATASET_CHANGES = "changes"
DATASET_ODDS = "odds"  # P28: bahis oranları, satır başına bir pazar seçeneği
DATASET_STANDINGS = "standings"  # P28: puan durumu satırları
DATASETS: Tuple[str, ...] = (DATASET_EVENTS, DATASET_SLICES, DATASET_CHANGES, DATASET_ODDS, DATASET_STANDINGS)
NORMALIZED = "normalized"
RAW = "raw"
SCHEMAS: Tuple[str, ...] = (NORMALIZED, RAW)
DATASET_FORMATS: Tuple[str, ...] = ("jsonl", "csv", "parquet", "sqlite")
TEXT_FORMATS: Tuple[str, ...] = ("jsonl", "csv")  # bir metin akışına (stdout) yazılabilenler
RAW_DATASETS: Tuple[str, ...] = (DATASET_EVENTS, DATASET_SLICES)
RAW_FORMATS: Tuple[str, ...] = ("jsonl", "tree")
PARQUET_PACKAGE = "pyarrow"  # sofascore_scraper/store/export.py PARQUET_PACKAGE
COLUMN_SEPARATOR = "_"
_STRING = "string"
_DATASET_SORT = "start_asc"
_PAGE = 500  # bir okumadaki maç ya da değişiklik satırı
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NOTHING = object()
LINEUPS_KEY = "lineups"  # oyuncu süzgecinin okuduğu dilim (kadro)
# Hiçbir maçın kimliği değil (SofaScore kimlikleri pozitiftir): katılımcı süzgecine uyan maç yoksa maç süzgeci bu
# tek kimlik olur, böylece boş süzgeç "hepsi" anlamına gelmez
_NO_EVENT = 0


@dataclass(frozen=True)
class DatasetFilter:
    """
    Hangi kayıtlar; boş alan süzmez, dolu alanlar birlikte (VE) uygulanır. API v1'in `GET /events` süzgeçleriyle
    aynı anlamdadır.

    status_classes: durum sınıfları (`not_started`, `live`, `completed`, `decided_without_play`, `void`,
        `unknown`). start_from / start_to: epoch saniye, iki uç dahil; `events` ve `slices` için maçın
        başlangıcı, `changes` için kaydın zamanı. `changes` sezon ve durum süzgeci almaz.
    team_ids / player_ids: katılımcı süzgeci (B1; modül belgesi): bu takımlardan ya da oyunculardan birinin
        maçları; ikisi birlikte tek süzgeçtir, öteki alanlarla VE.
    """

    sport: Optional[str] = None
    tournament_ids: Tuple[int, ...] = ()
    season_ids: Tuple[int, ...] = ()
    event_ids: Tuple[int, ...] = ()
    status_classes: Tuple[str, ...] = ()
    start_from: Optional[float] = None
    start_to: Optional[float] = None
    team_ids: Tuple[int, ...] = ()
    player_ids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class DatasetSpec:
    """
    Bir veri kümesinin dışa aktarması.

    dataset: `events`, `slices` ya da `changes`.
    format: normalleştirilmişte `jsonl`, `csv`, `parquet`, `sqlite`; hamda `jsonl` (dilim başına bir satır) ya da
        `tree` (maç başına bir dizin).
    schema: `normalized` (şema v1 kayıtları) ya da `raw` (saklanan SofaScore yükleri; yalnızca detayı saklanan
        maçlar). Ham `events` yalnızca olay yükünü, ham `slices` her dilimi verir; `changes`'in ham biçimi yoktur.
    """

    dataset: str = DATASET_EVENTS
    format: str = "jsonl"
    schema: str = NORMALIZED
    filter: DatasetFilter = field(default_factory=DatasetFilter)


def parquet_available() -> bool:
    """Parquet yazılabilir mi: isteğe bağlı `pyarrow` paketi kurulu mu (`pip install -e ".[parquet]"`)."""
    return importlib.util.find_spec(PARQUET_PACKAGE) is not None


def _parquet_missing(spec: DatasetSpec) -> NotSupportedError:
    return NotSupportedError(
        f"Parquet export needs the '{PARQUET_PACKAGE}' package, which is not installed "
        f"(pip install {PARQUET_PACKAGE}, or pip install -e \".[parquet]\").",
        {"dataset": spec.dataset, "format": spec.format, "schema": spec.schema})


def check_dataset(spec: DatasetSpec) -> None:
    """
    İstek yapılabilir mi: geçersiz birleşim UsageError (`invalid_request`), kurulumda yapılamayan (Parquet,
    `pyarrow` yok) NotSupportedError. Hiçbir şey okumaz.
    """
    from sofascore_scraper.status import StatusClass

    details = {"dataset": spec.dataset, "format": spec.format, "schema": spec.schema}
    if spec.dataset not in DATASETS or spec.schema not in SCHEMAS:
        raise UsageError("Unknown dataset or schema.", details)
    if spec.schema == RAW:
        if spec.dataset not in RAW_DATASETS:
            raise UsageError(f"The {spec.dataset} dataset has no raw form; export it normalized.", details)
        if spec.format not in RAW_FORMATS:
            raise UsageError("A raw export is written as JSONL or as a tree.", details)
    elif spec.format not in DATASET_FORMATS:
        raise UsageError("A normalized dataset is written as JSONL, CSV, Parquet or SQLite.", details)
    flt = spec.filter
    unknown = sorted(set(flt.status_classes).difference(m.value for m in StatusClass))
    if unknown:
        raise UsageError("Unknown status class.", {"status_classes": unknown})
    if spec.dataset == DATASET_CHANGES and (flt.season_ids or flt.status_classes):
        raise UsageError("The changes dataset filters by sport, tournament, event and time only.",
                         {"filter": ["season_ids", "status_classes"]})
    if flt.start_from is not None and flt.start_to is not None and flt.start_from > flt.start_to:
        raise UsageError("The start of the time range is after its end.", {"from": flt.start_from, "to": flt.start_to})
    if spec.format == "parquet" and not parquet_available():
        raise _parquet_missing(spec)


def parse_moment(text: str, *, end: bool) -> float:
    """
    ISO 8601 tarih ya da tarih-saat → epoch saniye (API v1'in `from` / `to` kuralı). Saat dilimi olmayan değer
    UTC'dir. Yalnızca tarih verilirse başlangıç o günün başı, bitiş o günün son saniyesidir (iki uç dahil).
    Okunamayan metin ValueError.
    """
    raw = str(text).strip()
    if _DATE.match(raw):
        day = _dt.datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
        return (day + _dt.timedelta(days=1)).timestamp() - 1 if end else day.timestamp()
    moment = _dt.datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.timestamp()


def record_model(dataset: str) -> "Type[Model]":
    """Veri kümesinin kayıt modeli (şema v1)."""
    from sofascore_scraper.schema import models

    found: Dict[str, Type[Model]] = {DATASET_EVENTS: models.Event, DATASET_SLICES: models.Slice,
                                     DATASET_CHANGES: models.Change, DATASET_ODDS: models.OddsLine,
                                     DATASET_STANDINGS: models.StandingsRow}
    if dataset not in found:
        raise ValueError(f"dataset: expected one of {', '.join(DATASETS)}, got {dataset!r}")
    return found[dataset]


def _model_types(hint: Any) -> Tuple[type, ...]:
    """Alan tipindeki modeller (Optional[Model] ya da modellerin birleşimi); model değilse boş."""
    from sofascore_scraper.schema.models import Model

    args = typing.get_args(hint) if typing.get_origin(hint) in (Union, types.UnionType) else (hint,)
    return tuple(arg for arg in args if isinstance(arg, type) and issubclass(arg, Model))


def _leaf_kind(hint: Any) -> str:
    """
    Yaprak alanın tablo türü (Parquet sütunu): `bool`, `int64`, `float64`; öteki her şey (metin, sayım, liste,
    serbest değer) `string`. Optional açılır.
    """
    if typing.get_origin(hint) in (Union, types.UnionType):
        args = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        kinds = {_leaf_kind(arg) for arg in args}
        return kinds.pop() if len(kinds) == 1 else _STRING
    if hint is bool:
        return "bool"
    if hint is int:
        return "int64"
    if hint is float:
        return "float64"
    return _STRING


def _leaves(model: type) -> List[Tuple[Tuple[str, ...], str]]:
    """
    Modelin yaprak alanları (JSON adlarıyla yol, tablo türü), alan sırasıyla. İç içe model (ya da modellerin
    birleşimi, ör. skor) açılır: birleşimin her üyesinin alanları sırayla, daha önce görülmemiş olanlar eklenir;
    aynı yolun üyelere göre türü değişirse tür `string`tir. Liste ve demet alanları açılmaz (tek bir yaprak).
    """
    from sofascore_scraper.schema.models import json_name

    hints = typing.get_type_hints(model)
    out: Dict[Tuple[str, ...], str] = {}
    for item in dataclasses.fields(model):
        name = json_name(item)
        nested = _model_types(hints[item.name])
        if not nested:
            out[(name,)] = _leaf_kind(hints[item.name])
            continue
        for member in nested:
            for path, kind in _leaves(member):
                key = (name, *path)
                out[key] = kind if out.get(key, kind) == kind else _STRING
    return list(out.items())


def leaf_paths(model: type) -> Tuple[Tuple[str, ...], ...]:
    """Modelin yaprak alanlarının yolları (JSON adlarıyla), alan sırasıyla (`_leaves`)."""
    return tuple(path for path, _kind in _leaves(model))


def column_types(dataset: str) -> Dict[str, str]:
    """
    Düzleştirilmiş veri kümesinin sütun türleri, modellerden (`bool`, `int64`, `float64`, `string`): Parquet
    sütunlarının türü veriye bakılmadan bunlardır, hep boş bir sütun da türünü korur.
    """
    return {COLUMN_SEPARATOR.join(path): kind for path, kind in _leaves(record_model(dataset))}


def _column_paths(dataset: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Düzleştirilmiş veri kümesinin (sütun adı, yol) çiftleri."""
    return tuple((COLUMN_SEPARATOR.join(path), path) for path in leaf_paths(record_model(dataset)))


def dataset_columns(dataset: str, fmt: str) -> Tuple[str, ...]:
    """
    Veri kümesinin sütunları: JSONL'de kaydın üst düzey alanları, CSV / Parquet / SQLite'ta düzleştirilmiş
    yaprak alanlar (modül açıklaması).
    """
    if fmt == "jsonl":
        from sofascore_scraper.schema.models import json_name

        return tuple(json_name(item) for item in dataclasses.fields(record_model(dataset)))
    return tuple(name for name, _path in _column_paths(dataset))


def flatten_record(record: Mapping[str, Any], paths: Sequence[Tuple[str, Tuple[str, ...]]]) -> Dict[str, Any]:
    """Kaydın (`to_dict()`) düz satırı: her sütuna yolundaki değer; yolda null bir nesne varsa null."""
    row: Dict[str, Any] = {}
    for name, path in paths:
        value: Any = record
        for part in path:
            value = value.get(part) if isinstance(value, Mapping) else None
        row[name] = value
    return row


def lineup_players(lineups: Any) -> FrozenSet[int]:
    """
    Saklanan bir kadronun (`/event/{id}/lineups`) oyuncu kimlikleri: iki tarafın `players` listesi (ilk on bir ve
    yedekler). `missingPlayers` (sakat, cezalı) sayılmaz. Beklenmeyen biçim boş küme.
    """
    found: Set[int] = set()
    if not isinstance(lineups, Mapping):
        return frozenset()
    for side in ("home", "away"):
        team = lineups.get(side)
        entries = team.get("players") if isinstance(team, Mapping) else None
        for entry in entries if isinstance(entries, list) else ():
            player = entry.get("player") if isinstance(entry, Mapping) else None
            value = player.get("id") if isinstance(player, Mapping) else None
            if isinstance(value, int) and not isinstance(value, bool):
                found.add(value)
    return frozenset(found)


def _event_query(flt: DatasetFilter, *, has_details: Optional[bool] = None) -> EventQuery:
    return EventQuery(
        scope=Scope(sport=flt.sport, tournament_ids=tuple(flt.tournament_ids), season_ids=tuple(flt.season_ids),
                    event_ids=tuple(flt.event_ids)),
        status_classes=tuple(flt.status_classes), start_from=flt.start_from, start_to=flt.start_to,
        has_details=has_details, sort=_DATASET_SORT)


def _not_found_details(spec: DatasetSpec) -> Dict[str, Any]:
    return {"dataset": spec.dataset, "schema": spec.schema, "filter": dataclasses.asdict(spec.filter)}


def _first_or_raise(rows: Iterable[Mapping[str, Any]], spec: DatasetSpec,
                    allow_empty: bool) -> Iterable[Mapping[str, Any]]:
    """Satır yoksa ve boş dışa aktarma istenmiyorsa, hiçbir şey yazılmadan NotFoundError."""
    if allow_empty:
        return rows
    iterator = iter(rows)
    first = next(iterator, _NOTHING)
    if first is _NOTHING:
        raise NotFoundError("there is nothing to export for this selection", _not_found_details(spec))
    return itertools.chain((first,), iterator)  # type: ignore[arg-type]


def _is_stream(dest: Any) -> bool:
    return hasattr(dest, "write") and not isinstance(dest, (str, bytes, os.PathLike))


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


def _league_rows(table: LegacyTable, league_id: int) -> Tuple[Dict[str, Any], ...]:
    """
    Eski lig süzgeci: `league_folder`'ı `<lig id>_` ile başlayan satırlar; sütunlar birleşik tablonunkiler kalır.
    Satırlar birleşik dışa aktarmadaki değerleriyle yazılır (FX-7: eskiden tablo pandas'tan geçiyor, boşluklu
    tamsayı sütunları `1.0` oluyordu). Tabloda `league_folder` sütunu hiç yoksa (yalnızca düz kayıtlar) süzgeç
    uygulanmaz ve bütün satırlar kalır: pandas'lı kodun davranışı, olduğu gibi korundu.
    """
    if "league_folder" not in table.columns:
        return table.rows
    prefix = f"{league_id}_"
    return tuple(row for row in table.rows if str(row.get("league_folder") or "").startswith(prefix))


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
    olmayan kayıtta, eski yazıcının o maç için seçeceği adlar (2.x'te `MatchDataFetcher._match_storage_dir`).
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
    """2.x yazıcısının `_path_part` kuralı (tests/legacy_writer.py'de de): dizin adında güvenli tek parça."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name)).strip(".") or "unknown"


def legacy_wide_row(match_id: str, match_data: Mapping[str, Any], league_folder: Optional[str] = None,
                    season_folder: Optional[str] = None) -> Dict[str, Any]:
    """
    Bir maçın `legacy-wide-csv` satırı; `match_data` dilim adı → yük (`basic`, `statistics`, `team_streaks`,
    `pregame_form`, `h2h`, `lineups`). 2.x'teki `MatchDataFetcher.process_match_for_csv`'nin kuralı, değişmeden.
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

    # Kadrolar: onay, ilk on bir ve yedek sayıları, diziliş
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
                processed[f"{side}_formation"] = _formation(lineup.get("formation"))

    return processed


def _formation(value: Any) -> Optional[str]:
    """
    Kadronun dizilişi. SofaScore metin gönderir (`"4-2-3-1"`); eski kodun beklediği `{"name": ...}` nesnesi de
    okunur. Başka her şey (yok, boş metin, sayı) boş hücredir.
    """
    if isinstance(value, dict):
        value = value.get("name")
    return value if isinstance(value, str) and value else None


__all__ = ["COLUMN_SEPARATOR", "DATASETS", "DATASET_CHANGES", "DATASET_EVENTS", "DATASET_FORMATS", "DATASET_SLICES",
           "DatasetFilter", "DatasetSpec", "ExportResult", "ExportService", "ExportSpec", "LEGACY_WIDE_CSV",
           "LegacyTable", "NORMALIZED", "PreparedExport", "RAW", "RAW_DATASETS", "RAW_FORMATS", "SCHEMAS",
           "TEXT_FORMATS", "check_dataset", "column_types", "dataset_columns", "flatten_record", "leaf_paths",
           "legacy_columns", "legacy_folders", "legacy_wide_row", "lineup_players", "parquet_available", "parse_moment",
           "record_model"]
