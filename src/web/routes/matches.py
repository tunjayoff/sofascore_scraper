"""Maç listesi, maç detayı ve eksik detay uç noktaları."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src import breaker as request_breaker
from src import bridge_health
from src.services.query import QueryService
from src.services.status import only_finished_setting
from src.store import open_store
from src.web import upstream
from src.web.jobs import JobRunningError
from src.web.routes.common import (
    _SyncHttpError,
    _job_store,
    config_manager,
    logger,
)

if TYPE_CHECKING:
    from src.services.planning import WorkItem
    from src.slices import Outcome

router = APIRouter(prefix="/api", tags=["api"])


def _parse_league_ids(league_id: Optional[str]) -> Optional[Set[int]]:
    """`league_id` is one id ("17") or several, comma-separated ("17,8,132"). None = no filter."""
    if not league_id:
        return None
    ids: Set[int] = set()
    for part in str(league_id).split(","):
        try:
            ids.add(int(part.strip()))
        except ValueError:
            continue
    return ids or None


class MatchListResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int
    sort: str


def _get_matches_sync(
    data_dir: str,
    limit: int,
    offset: int,
    sort: str,
    league_id: Optional[str],
    date: Optional[str],
    season_id: Optional[int],
    details: Optional[str] = None,
) -> MatchListResponse:
    """
    Maç listesi, depodan okunur (QueryService.matches_legacy). "Yalnızca bitmiş maçlar" ayarı okurken
    uygulanır (panodaki sayımla aynı kural); dışa aktarma CSV'sine geri düşülmez (tasarım kararı S14).
    """
    ids = _parse_league_ids(league_id)
    page = QueryService(open_store(data_dir)).matches_legacy(
        tournament_ids=sorted(ids) if ids is not None else (),
        season_id=season_id,
        date=date,
        details={"present": True, "missing": False}.get(details or ""),
        only_finished=only_finished_setting(),
        sort=sort if sort in ("asc", "desc") else "desc",
        offset=offset,
        limit=limit,
        league_names=config_manager.get_leagues(),
    )
    return MatchListResponse(items=list(page.items), total=page.total, limit=limit, offset=offset, sort=sort)


def _get_season_matches_sync(season_id: int, league_id: int, data_dir: str) -> dict:
    """Bir turnuvanın bir sezondaki maçları, depodan (QueryService.season_matches_legacy)."""
    matches = QueryService(open_store(data_dir)).season_matches_legacy(
        season_id, league_id, only_finished=only_finished_setting())
    return {"matches": matches}


_MISSING_LIST_LIMIT = 500


def _get_missing_details_sync(league_id: int, season_id: Optional[int], data_dir: str) -> dict:
    """
    Bir ligin (verildiyse bir sezonun) programlarda ve özetlerde geçen maçlarından detayı indirilmemiş olanlar,
    depodan (QueryService.missing_details_legacy). "Yalnızca bitmiş maçlar" ayarı okurken uygulanır (maç
    listeleriyle aynı kural); liste en çok `_MISSING_LIST_LIMIT` satırdır.
    """
    return QueryService(open_store(data_dir)).missing_details_legacy(
        league_id, season_id, only_finished=only_finished_setting(), limit=_MISSING_LIST_LIMIT)


def _get_match_details_sync(match_id: str) -> Dict[str, Any]:
    """
    Maçın saklanan detayı (`basic` ve yükü olan dilimler), depodan okunur (QueryService.match_detail_legacy).
    Maç bilinmiyorsa ya da olay yükü yoksa 404; depo açılamıyor ya da okunamıyorsa 500.
    """
    try:
        detail = QueryService(open_store(config_manager.get_data_dir())).match_detail_legacy(int(match_id))
    except Exception as e:
        logger.error(f"Error loading match {match_id}: {e}")
        raise _SyncHttpError(500, "Error parsing match data.")
    if detail is None:
        raise _SyncHttpError(404, "Match details not found.")
    return detail


def _single_fetch_reason(outcome: Outcome, before: Optional[Dict[str, Any]]) -> str:
    """
    Başarısız bir isteğin sonucu → src/web/upstream.py'deki neden. upstream.reason_for'un hata sınıfları
    için yaptığı eşlemenin sonuç tipi üzerindeki karşılığıdır (tek maç yolu hatayı değil sonucu saklar):
    429/503 → rate_limited; 403 → blocked (challenge'ı çözecek tarayıcı açılamadıysa browser);
    zaman aşımı/bağlantı hatası → network; gerisi (diğer HTTP kodları, bozuk yanıt) → upstream.
    """
    if outcome.reason == request_breaker.RATE_LIMITED or outcome.http_status == 503:
        return upstream.RATE_LIMITED
    if outcome.reason == request_breaker.FORBIDDEN:
        return upstream.BROWSER if upstream.browser_failed_since(before) else upstream.BLOCKED
    if outcome.reason in (request_breaker.TIMEOUT, request_breaker.NETWORK):
        return upstream.NETWORK
    return upstream.UPSTREAM


def _single_item(store: Any, event_id: int) -> "WorkItem":
    """
    Tek maçın iş birimi: olay yükü saklanan maç için `refill` (sayfası yeniden okunur, eksik dilimleri istenir),
    diğerleri için `full`.
    """
    from src.services import planning
    from src.services.pipeline import UNAVAILABLE_AFTER_ATTEMPTS
    from src.store import Ref, Scope

    states = list(store.events.states(Scope(event_ids=(event_id,))))
    state = states[0] if states else None
    if state is not None and state.event.has_event_payload:
        item = planning.work_item(event_id, state, "refill", threshold=UNAVAILABLE_AFTER_ATTEMPTS)
        if item is not None:
            return item
    return planning.WorkItem(Ref.event(event_id), "full", (), None, "single match")


def _breaker_outcome(reason: str) -> "Outcome":
    """Devre kesicinin nedeni ("403" | "429" | "5xx" | "other") → `_single_fetch_reason`'ın okuyacağı başarısız sonuç."""
    from src.slices import SLICE_FAILED, Outcome

    return Outcome(SLICE_FAILED, reason=reason)


def _fetch_single_match_sync(match_id: str) -> dict:
    """
    Tek maçı boru hattıyla çeker (src/services/pipeline.py), toplu indirmelerle aynı kurallarla: sporun seçilen
    bütün dilimleri (isteğe bağlılar dahil), "yalnızca bitmiş maçlar" ayarı ve bu çekimin kendi devre kesicisi.

    SofaScore /event isteğini ya da istenen dilimlerin hepsini reddettiyse, ya da devre kesildiyse, tipli hatayı
    (src/web/upstream.py: {"detail": {"reason", "message"}}) fırlatır. Maç SofaScore'da yoksa ya da bitmemişse
    404; dilimlerden biri bile yanıt aldıysa "success". Kayıt yazılamadıysa 500.
    """
    from src import utils
    from src.client.context import request_context
    from src.services.pipeline import ITEM_SKIPPED, SKIP_BREAKER, FetchPipeline

    try:
        store = open_store(config_manager.get_data_dir())
        item = _single_item(store, int(match_id))
        breaker = request_breaker.CircuitBreaker.from_config(config_manager)
        before = bridge_health.snapshot()
        with request_context(breaker=breaker):
            summary = FetchPipeline(store, concurrency=1,
                                    only_finished=bool(utils.FETCH_ONLY_FINISHED)).run_sync([item])
        result = summary.results[0]
        if result.error is not None:
            raise result.error
        failure = result.upstream_failure()
        if failure is None and result.status == ITEM_SKIPPED and result.reason == SKIP_BREAKER:
            failure = _breaker_outcome(breaker.reason())
        if failure is not None:
            reason = _single_fetch_reason(failure, before)
            what = "the event request" if failure is result.event else (
                "every slice request" if result.slices else "the circuit breaker")
            logger.error(
                f"Single match fetch for {match_id} failed ({reason}): {what} failed "
                f"({failure.reason}, HTTP {failure.http_status})"
            )
            raise upstream.http_error(reason)
        if not result.ok:
            raise _SyncHttpError(
                404,
                "Match data could not be fetched (may be unfinished or unavailable).",
            )
        return {"status": "success", "match_id": match_id}
    except (_SyncHttpError, HTTPException):
        raise
    except Exception as e:
        # İstek hataları buraya ulaşmaz (sonuçtan okunur); kalanlar diske yazma ve benzeri hatalardır
        logger.error(f"Single match fetch failed for {match_id}: {e}")
        raise _SyncHttpError(500, "Match fetch failed")


@router.get("/seasons/{season_id}/matches")
async def get_season_matches(season_id: int, league_id: int = Query(...)):
    """Yerel kayıtlı maç listesini sezon bazında döndürür."""
    data_dir = config_manager.get_data_dir()
    return await asyncio.to_thread(_get_season_matches_sync, season_id, league_id, data_dir)


@router.get("/leagues/{league_id}/missing-details")
async def get_missing_details(league_id: int, season_id: Optional[int] = None):
    """Bir lig için detayı çekilmemiş maçları döndürür."""
    data_dir = config_manager.get_data_dir()
    return await asyncio.to_thread(_get_missing_details_sync, league_id, season_id, data_dir)


@router.get("/matches", response_model=MatchListResponse)
async def get_matches(
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0, le=50000),
    sort: str = Query("desc"),
    league_id: Optional[str] = None,
    date: Optional[str] = None,
    season_id: Optional[int] = None,
    details: Optional[str] = Query(None, pattern="^(present|missing)$"),
) -> MatchListResponse:
    """`league_id` may list several ids ("17,8"); `details` keeps only matches with/without details."""
    if sort not in ("asc", "desc"):
        sort = "desc"
    data_dir = config_manager.get_data_dir()
    return await asyncio.to_thread(
        _get_matches_sync,
        data_dir,
        limit,
        offset,
        sort,
        league_id,
        date,
        season_id,
        details,
    )


@router.get("/matches/{match_id}")
async def get_match_details(match_id: int):
    """Retrieve detailed JSON data for a specific match."""
    try:
        return await asyncio.to_thread(_get_match_details_sync, str(match_id))
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/matches/{match_id}/fetch")
async def fetch_single_match(match_id: int):
    """Tek bir maç için detayları senkron olarak çeker."""
    if _job_store.writer_busy():
        # Veri dizinine yazan biri var: bu sürecin işi ya da `writer` kilidini tutan başka bir süreç (ikinci
        # bir web sunucusunun işi, CLI indirmesi). Aynı dosyalara yazıyor ve istek hızını zaten kullanıyor:
        # diğer uç noktalar gibi 409 job_running (app.py, JobStoreConflict)
        raise JobRunningError()
    try:
        return await asyncio.to_thread(_fetch_single_match_sync, str(match_id))
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
