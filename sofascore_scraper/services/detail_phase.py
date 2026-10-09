"""
Maç detaylarının aşaması: hangi maçların detayı indirilecek (katalogdan), indirme ve yenileme (boru hattıyla).

Eşitleme servisi (sofascore_scraper/services/sync.py) detay aşamasını, seçilen maçları ve yalnızca yenileme kipini
bununla çalıştırır; bakım servisi "yok" işaretlerini bununla geri alır. Kurallar başka modüllerdedir: bir maçın
ihtiyacı ve iş birimleri sofascore_scraper/services/planning.py, indirme sofascore_scraper/services/pipeline.py
(FetchPipeline), adaylar QueryService.detail_candidates. Bu sınıf yalnızca onları bir işin ömrü boyunca
birleştirir: ihtiyaç önbelleği (iş boyunca bir maçın kararı bir kez hesaplanır), devre kesicinin sonucu ve
yenileme dinleyicisi (iş kartının "yenilendi" sayacı).

2.x'te aynı işi sofascore_scraper/match_data_fetcher.py'deki MatchDataFetcher'ın giriş noktaları
(`collect_detail_match_ids`, `pending_detail_ids`, `fetch_detail_ids`, `fetch_matches_batch`, `refresh_due_ids`,
`refresh_matches`, `reset_unavailable_markers`) yapıyordu; P15'ten beri boru hattına ve planlamaya varan ince yüzlerdi ve
3.1'de kalktılar (plan maddesi P30). Davranış aynıdır: istekler, yazılan kayıtlar, sıra ve sayılar.

Maç kimlikleri metin olarak taşınır (iş kartındaki başarısız maçlar ve sayaçlar bugünkü gibi metin kimlik tutar);
kurallı bir kimlik olmayan metin ("007", "abc") hiçbir maçın kimliği değildir.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from sofascore_scraper import breaker as request_breaker
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.logger import get_logger
from sofascore_scraper.services import pipeline, planning
from sofascore_scraper.services.planning import WorkItem
from sofascore_scraper.services.query import DEFAULT_EMPTY_THRESHOLD, QueryService, RefreshPolicy
from sofascore_scraper.services.status import only_finished_setting
from sofascore_scraper.store import Ref, Scope, league_dir_name

if TYPE_CHECKING:
    from sofascore_scraper.config_manager import ConfigManager
    from sofascore_scraper.store import EventRow, Store

logger = get_logger("Details")

# Bitmiş bir maçta bu kadar KESİN yanıtta da boş gelen dilim o maç için yok sayılır (ör. tenis maçlarında kadro/olay
# yok). Kesin yanıt: HTTP 404 ya da içinde veri olmayan 200; başarısız istek sayılmaz.
UNAVAILABLE_AFTER_ATTEMPTS = DEFAULT_EMPTY_THRESHOLD
# Eski düzenin adları (yalnızca yenilemenin sırası için; dosyalara Store dokunur)
MATCH_DETAILS_DIR = "match_details"
NO_TOURNAMENT_DIR = "_no_tournament"
_LEGACY_LAYOUT = "legacy"
_MAX_EVENT_ID = 2 ** 63 - 1  # kataloğun saklayabildiği en büyük kimlik

Progress = Callable[[int, int, str], None]


def canonical_id(text: Any) -> Optional[int]:
    """Kurallı maç kimliği metni ("123") → sayı; "007", "abc", "-1" ve kataloğun sınırını aşan sayı None."""
    text = str(text)
    if not (text.isascii() and text.isdigit()) or str(int(text)) != text or int(text) > _MAX_EVENT_ID:
        return None
    return int(text)


class DetailPhase:
    """
    Bir işin detay aşaması. Durum: ihtiyaç önbelleği, son indirmenin devre kesici sonucu (`breaker_tripped`,
    `status_counts`) ve yenileme dinleyicisi. Bir iş için kurulur, işle biter.

    store   veri dizininin deposu
    config  yapılandırma: eşzamanlı istek sayısı, devre kesicinin eşikleri (iş kurmadıysa), liglerin adları
    """

    def __init__(self, store: "Store", config: "ConfigManager") -> None:
        self.store = store
        self.config = config
        self.breaker_tripped = False
        self.status_counts: Dict[str, int] = {}
        # Her yenilemede (maç kimliği, değişti mi) ile çağrılır (iş kartının sayacı)
        self.refresh_listener: Optional[Callable[[str, bool], None]] = None
        # Her iş biriminin sonucu (işin tahmini süresi maç başına isteği buradan ölçer; B2)
        self.result_listener: Optional[Callable[[pipeline.ItemResult], None]] = None
        self._needs: Dict[str, str] = {}

    # --- plan -----------------------------------------------------------------------------------------

    def candidates(self, league_id: Optional[int] = None, *,
                   only_season_ids: Optional[Sequence[int]] = None) -> Optional[List[str]]:
        """
        Detayı indirilecek maçların adayları: programlarda ve sezon özetlerinde geçen benzersiz maç kimlikleri,
        katalogdan (QueryService.detail_candidates). Lig başına sezon kimliği büyükten küçüğe, sezon içinde
        başlangıç zamanı sırasıyla; ligler kimlik sırasıyla. "Yalnızca bitmiş maçlar" ayarı okurken uygulanır.
        İstenen ligin listelerde hiç maçı yoksa None.
        """
        service = QueryService(self.store)
        service.require_current()
        candidates = service.detail_candidates(league_id, only_finished=only_finished_setting(),
                                               only_season_ids=only_season_ids)
        if league_id is not None:
            logger.info("Fetching match details for league %s", league_id)
            if league_id not in candidates:
                logger.warning("League %s has no listed matches", league_id)
                return None
        else:
            logger.info("Fetching match details for all leagues")
        match_ids: List[str] = []
        for tid, event_ids in candidates.items():
            logger.info("League %s: %d match ids", league_dir_name(tid, self._league_name(tid)), len(event_ids))
            match_ids.extend(str(event_id) for event_id in event_ids)
        return list(dict.fromkeys(match_ids))

    def _league_name(self, league_id: int) -> Optional[str]:
        """Ligin dizin adındaki adı (yazıcılarla aynı kaynak): yapılandırmadaki ad, yoksa katalogdaki turnuva adı."""
        try:
            name = self.config.get_league_by_id(league_id)
        except Exception:
            name = None
        if isinstance(name, str) and name:
            return name
        found = self.store.entities.tournament(league_id)
        return found.name if found is not None and found.name else None

    def needs(self, match_ids: Sequence[Any]) -> Dict[str, str]:
        """
        Maçların ihtiyacı (`full` / `refill` / `refresh` / `none`), işin önbelleğinden; önbellekte olmayanlar tek
        seferde katalogdan hesaplanır (`planning.event_needs`). Katalog dosyalarla eşit değilse CatalogNotCurrent.
        """
        mids = [str(mid) for mid in match_ids]
        wanted = [mid for mid in dict.fromkeys(mids) if mid not in self._needs]
        if wanted:
            ids = {mid: canonical_id(mid) for mid in wanted}
            QueryService(self.store).require_current()
            found = planning.event_needs(self.store, [event_id for event_id in ids.values() if event_id is not None],
                                         RefreshPolicy.current(), threshold=UNAVAILABLE_AFTER_ATTEMPTS)
            self._needs.update({mid: found.get(event_id, "full") if event_id is not None else "full"
                                for mid, event_id in ids.items()})
        return {mid: self._needs[mid] for mid in mids}

    def pending(self, match_ids: Sequence[Any]) -> List[str]:
        """Detay dilimleri eksik ya da kısmi olan maçlar, ardından yenilenecek (geçici) maçlar (`order_by_need`)."""
        mids = [str(mid) for mid in match_ids]
        return planning.order_by_need(mids, self.needs(mids))[0]

    # --- indirme --------------------------------------------------------------------------------------

    def _pipeline(self) -> pipeline.FetchPipeline:
        return pipeline.FetchPipeline(self.store, concurrency=self.config.get_max_concurrent())

    def _after_result(self, result: pipeline.ItemResult) -> None:
        """Her sonuçtan sonra: işin önbelleği, yenileme dinleyicisi (iş kartı sayacı)."""
        if result.put is not None:
            self._needs.pop(str(result.event_id), None)
        if result.item.need == "refresh" and result.ok and self.refresh_listener is not None:
            self.refresh_listener(str(result.event_id), bool(result.changed))
        if self.result_listener is not None:
            self.result_listener(result)

    def _items(self, match_ids: Sequence[Any]) -> List[WorkItem]:
        """Maçların iş birimleri (işin önbelleğindeki kararlarla); tamam olanlar düşer."""
        mids = [str(mid) for mid in match_ids]
        decided = self.needs(mids)
        needs = {event_id: decided[mid] for mid in mids if (event_id := canonical_id(mid)) is not None}
        items = planning.plan_items(self.store, list(needs), RefreshPolicy.current(),
                                    threshold=UNAVAILABLE_AFTER_ATTEMPTS, needs=needs)
        skipped = len(dict.fromkeys(mids)) - len(items)
        refresh = sum(1 for item in items if item.need == "refresh")
        if skipped:
            logger.info(f"{skipped} matches are complete; skipped")
        if refresh:
            logger.info(f"Refreshing {refresh} provisional records")
        return items

    def _run(self, items: List[WorkItem], *, progress: Optional[Progress],
             cancelled: Optional[Callable[[], bool]], failed: Optional[Callable[[str], None]]) -> int:
        """
        İş birimlerini boru hattıyla çalıştırır; yazılan maç sayısını döndürür. progress(done, planned): her sonuçta.
        Kesici, çağıran kurduysa işin kesicisidir (`request_breaker.scope`).
        """
        self.breaker_tripped = False
        planned = len(items)
        stored = 0
        done = 0
        if progress and planned:
            progress(0, planned, f"Starting {planned} match detail requests…")

        def on_result(result: pipeline.ItemResult) -> None:
            nonlocal done, stored
            done += 1
            self._after_result(result)
            breaker = request_breaker.current()
            if result.ok:
                stored += 1
            elif result.failed and failed is not None:
                # Devre kesildiyse istek hatası "başarısız maç" sayılmaz; depolama hatası sayılır
                if result.reason == pipeline.FAIL_STORAGE or breaker is None or not breaker.tripped:
                    failed(str(result.event_id))
            if progress and planned:
                progress(min(done, planned), planned, f"Match details {min(done, planned)}/{planned}")

        with request_breaker.scope(self.config) as breaker:
            try:
                self._pipeline().run_sync(items, cancelled=cancelled, on_result=on_result)
            finally:
                self.breaker_tripped = breaker.tripped
                self.status_counts = breaker.counts()
        return stored

    def fetch(self, match_ids: Sequence[Any], *, progress: Optional[Progress] = None,
              cancelled: Optional[Callable[[], bool]] = None,
              failed: Optional[Callable[[str], None]] = None) -> int:
        """
        Lig planının maçları (`pending` sırasıyla), boru hattıyla. Döndürür: yazılan maç sayısı. progress(done,
        total): total verilen maç sayısıdır. Tamam olan maç atlanır; bitmemiş maç olduğu haliyle saklanır, başarısız
        sayılmaz.

        failed(match_id): /event'i alınamayan, SofaScore'da olmayan ya da yazılamayan her maç için çağrılır;
        devre kesildikten sonra denenmeyen ve başarısız istekler sayılmaz (depolama hatası sayılır). Devre kesilince
        kalan maçlar denenmez ve `breaker_tripped` doğru olur. Kalıcı depolama hatasında StorageError.
        """
        total = len(match_ids)
        logger.info("Details will be fetched for %d matches", total)
        if progress:
            progress(0, total, f"Match details 0/{total}")

        def shown(done: int, _planned: int, _msg: str) -> None:
            if progress:
                progress(min(done, total), total, f"Match details {min(done, total)}/{total}")

        stored = self._run(self._items(match_ids), progress=shown, cancelled=cancelled, failed=failed)
        if self.breaker_tripped:
            logger.warning("Too many failed requests (rate limit or IP block); %d matches were stored. Wait 30 "
                           "minutes to 2 hours, keep the request rate at its default or use another IP, then "
                           "run again", stored)
        rate = (stored / total) * 100 if total > 0 else 0
        logger.info("Done: %d/%d matches (%.1f%%) processed successfully", stored, total, rate)
        return stored

    def fetch_selected(self, match_ids: Sequence[Any], *, progress: Optional[Progress] = None,
                       cancelled: Optional[Callable[[], bool]] = None,
                       failed: Optional[Callable[[str], None]] = None) -> int:
        """
        Kimliğiyle seçilen maçlar (lig planlarıyla aynı yol). `fetch` gibi; progress(done, planned): planned
        indirilecek maç sayısıdır (tamam olanlar düşmüş).
        """
        return self._run(self._items(match_ids), progress=progress, cancelled=cancelled, failed=failed)

    # --- yenileme -------------------------------------------------------------------------------------

    def refresh_due(self, league_id: Optional[Union[int, str]] = None) -> List[str]:
        """
        Kayıtlı maçlardan yenilenmesi gerekenler (eksik dilimli maçlar dahil değil), katalogdan
        (`planning.refresh_due_events`). league_id: maçın turnuvası (kurallı bir kimlik değilse hiçbir maç). Sıra,
        eski düzen yazıcısının dizinlerinin yol sırasıdır (lig dizini, sezon dizini, maç kimliği metin olarak):
        istekler bugünkü sırayla gider.
        """
        tournament_ids: Tuple[int, ...] = ()
        if league_id is not None:
            tournament = canonical_id(league_id)
            if tournament is None:
                return []
            tournament_ids = (tournament,)
        QueryService(self.store).require_current()
        rows = planning.refresh_due_events(self.store, RefreshPolicy.current(), tournament_ids=tournament_ids,
                                           threshold=UNAVAILABLE_AFTER_ATTEMPTS)
        ids = [str(row.id) for row in sorted(rows, key=self._legacy_order)]
        self._needs.update((mid, "refresh") for mid in ids)
        return ids

    def _legacy_order(self, row: "EventRow") -> List[str]:
        """Kaydın eski düzen yolu, parça parça (`refresh_due`'nun sırası)."""
        from sofascore_scraper.services.export import legacy_folders

        if row.layout == _LEGACY_LAYOUT:
            return str(row.path).strip("/").split("/")
        if row.legacy_path:
            return str(row.legacy_path).strip("/").split("/")
        try:
            basic = self.store.events.payload(row.id)
        except StorageError:
            basic = None
        league, season = legacy_folders(row, basic)
        if league is None:
            return [MATCH_DETAILS_DIR, str(row.id)]
        season_dir = season if league == NO_TOURNAMENT_DIR else f"season_{season}"
        return [MATCH_DETAILS_DIR, league, str(season_dir), str(row.id)]

    def refresh(self, match_ids: Sequence[Any], *, progress: Optional[Progress] = None,
                cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        """
        Yalnızca yenileme: her maç için boru hattının `refresh` birimi (yalnızca /event). Devre kesilirse kalan maçlar
        denenmez; sonuçta `breaker` (neden) ve `skipped` (denenmeyen maç) bulunur. Kalıcı depolama hatasında
        StorageError.
        """
        stats: Dict[str, Any] = {"refreshed": 0, "changed": 0, "failed": 0}
        n = len(match_ids)
        if n:
            logger.info(f"Refreshing {n} matches")
        items: List[WorkItem] = []
        for mid in match_ids:
            event_id = canonical_id(mid)
            if event_id is None:
                stats["failed"] += 1
                continue
            items.append(WorkItem(Ref.event(event_id), "refresh", (), None, "refresh"))
        done = n - len(items)
        skipped = 0

        def on_result(result: pipeline.ItemResult) -> None:
            nonlocal done, skipped
            done += 1
            self._after_result(result)
            if result.ok:
                stats["refreshed"] += 1
                stats["changed"] += int(bool(result.changed))
            elif result.failed:
                stats["failed"] += 1
            else:
                skipped += 1
            if progress:
                progress(done, n, f"Refresh {done}/{n}")

        with request_breaker.scope(self.config) as breaker:
            self._pipeline().run_sync(items, cancelled=cancelled, on_result=on_result)
            if breaker.tripped:
                stats["breaker"] = breaker.reason()
                stats["skipped"] = skipped
                logger.warning(f"Too many failed requests; the refresh stopped, {skipped} matches were not tried")
        self.breaker_tripped = bool(stats.get("breaker"))
        return stats

    # --- "yok" işaretleri -----------------------------------------------------------------------------

    def reset_markers(self, league_id: Optional[Union[int, str]] = None, *,
                      include_confirmed: bool = False) -> Dict[str, int]:
        """
        "Bu dilim bu maçta yok" işaretlerini yeniden denetime açar; ağ isteği yapmaz (`Store.events.reset_empty_markers`).
        Varsayılan olarak yalnızca kesin yanıtla doğrulanmamış sayımlar geri alınır; include_confirmed=True
        doğrulanmışları da siler. league_id: yalnızca bu turnuvanın maçları.

        Returns: {"matches": işareti değişen maç, "slices": yeniden istenecek dilim, "scanned": sayacı olan maç}
        """
        scope = None
        if league_id is not None:
            tournament = canonical_id(league_id)
            if tournament is None:
                return {"matches": 0, "slices": 0, "scanned": 0}
            scope = Scope(tournament_ids=(tournament,))
        result = dict(pipeline.put_retrying(
            self.store, 0, "the marker reset",
            lambda: self.store.events.reset_empty_markers(scope, include_confirmed=include_confirmed,
                                                          threshold=UNAVAILABLE_AFTER_ATTEMPTS),
            log=logger))
        if result.get("matches"):
            self._needs.clear()  # yeniden beklenen dilimi olan maçların ihtiyacı değişti
        return result


__all__ = ["DetailPhase", "UNAVAILABLE_AFTER_ATTEMPTS", "canonical_id"]
