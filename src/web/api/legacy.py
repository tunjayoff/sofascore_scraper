"""
Eski `/api` yolları (docs/design/02-services.md bölüm 6.1; plan maddesi P21): v1'in yanında bir sürüm daha duran
bağdaştırıcılar. Yollar, yanıt biçimleri ve durum kodları aynıdır (G-02 ve G-04 altınları,
tests/snapshots/openapi-legacy.json); her yanıt `Deprecation` ve halefini gösteren `Link` başlığını taşır
(src/web/api/__init__.py). Plan maddesi P30 bu modülü siler.

Bağdaştırıcılar servisleri çağırır: maç listeleri ve detayı QueryService, pano ve istatistikler StatusService,
sezon listeleri services.tournaments, tek maç FetchPipeline, indirme işi SyncService (iş yöneticisinin altında),
yedek BackupService, temizleme MaintenanceService, CSV ExportService. Paylaşılan nesneler (yapılandırma
yöneticisi, iş deposu, eski arayüzün iş görüntüsü) src/web/deps.py'dedir; bu modül durum tutmaz.

Eskiden src/web/routes/ (leagues, matches, scrape, settings, data, sports, diagnostics, auth, common) ve
src/web/fetch_job.py'deydiler. Farklar:

  * Belge metinleri (OpenAPI `description`) ve log satırları İngilizcedir.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Mapping, Optional, Set

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from src import breaker as request_breaker
from src import bridge_health, diagnostics, sports
from src import store as store_api
from src.errors import to_platform_error
from src.exceptions import SofaScoreScraperError, StorageError
from src.language import explicit_language
from src.logger import get_logger
from src.redact import redact_text
from src.refresh import refresh_window_hours
from src.services import stats as stats_service
from src.services import tournaments
from src.services.backup import BackupService
from src.services.maintenance import MaintenanceService
from src.services.query import QueryService
from src.services.status import DataSummary, StatusService, only_finished_setting
from src.store import JobRunningError, StoreError, default_db_path, open_store
from src.version import __version__
from src.web import deps, league_sports, security, upstream
from src.web.api.proxy import PROXY_PASSWORD_MASK, mask_proxy_url, restore_proxy_password
from src.web.api.v1 import auth as v1_auth

if TYPE_CHECKING:
    from src.jobs.manager import JobHandle, JobManager, JobOutcome
    from src.jobs.model import ErrorInfo
    from src.services.planning import WorkItem
    from src.services.sync import SyncSpec
    from src.slices import Outcome

logger = get_logger("WebAPI")

router = APIRouter(prefix="/api", tags=["api"])


class _SyncHttpError(Exception):
    """Senkron worker içinde fırlatılır; async rota HTTPException'a çevirir."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class _UpstreamFailure(Exception):
    """Senkron worker içinde fırlatılır; async rota tipli HTTP hatasına çevirir (src/web/upstream.py)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _cm() -> Any:
    """Web sürecinin yapılandırma yöneticisi (src/web/deps.py)."""
    return deps.config_manager()


# =================================================================================================
# Ligler, lig arama ve sezon listeleri (eski src/web/routes/leagues.py)
# =================================================================================================


class LeagueModel(BaseModel):
    id: int
    name: str
    sport: Optional[str] = None


# leagues.txt satır biçimi "Ad: ID"; ad dizin adına da dönüşür (yeni satır, ':' ve yol ayırıcı yok)
LeagueName = Field(min_length=1, max_length=80, pattern=r"^[^\r\n:/\\\x00]+$")


class LeagueCreate(BaseModel):
    id: int = Field(gt=0)
    name: str = LeagueName
    sport: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name_not_dots(cls, v: str) -> str:
        v = v.strip()
        if not v or v.strip(".") == "":
            raise ValueError("invalid league name")
        return v


class LeagueUpdate(BaseModel):
    sport: Optional[str] = None


class RemoteLeagueResult(BaseModel):
    id: int
    name: str
    country: str
    slug: Optional[str] = None
    sport: Optional[str] = "Football"


def _search_remote_leagues_sync(q: str) -> List[RemoteLeagueResult]:
    """
    SofaScore'da lig arar. Boş liste yalnızca istek başarılı olup hiçbir şey bulunamadığında döner; engelleme,
    ağ hatası ve beklenmeyen yanıt _UpstreamFailure(reason) olarak fırlatılır.
    """
    from src.client import api_url, endpoints
    from src.utils import make_api_request

    # API kökü istemcinindir (API_BASE_URL): diğer bütün istekler gibi
    url = api_url(endpoints.search_unique_tournaments(q))
    before = bridge_health.snapshot()
    try:
        # Etkileşimli arama: 403 bekleme döngüsüyle bir sunucu işçisini dakikalarca tutma
        data = make_api_request(url, max_retries=1, timeout=10, raise_errors=True)
    except SofaScoreScraperError as e:
        reason = upstream.reason_for(e, before)
        # Arama uç noktası sonuç yokken boş liste döndürür: 404 "sonuç yok" değil, beklenmeyen yanıt
        if reason == upstream.NOT_FOUND:
            reason = upstream.UPSTREAM
        logger.error(f"Remote league search failed ({reason}): {e}")
        raise _UpstreamFailure(reason) from e

    results = None
    if isinstance(data, dict):
        results = data.get("uniqueTournaments", data.get("results"))
    if not isinstance(results, list):
        logger.error("Remote league search: the answer has no result list")
        raise _UpstreamFailure(upstream.UPSTREAM)

    leagues = []
    for item in results:
        entity = item.get("entity", item) if isinstance(item, dict) else item
        if not isinstance(entity, dict):
            continue
        lid = entity.get("id")
        name = entity.get("name")
        if lid and name:
            category = entity.get("category", {})
            sport_obj = category.get("sport", {}) if isinstance(category, dict) else {}
            sport_name = sport_obj.get("name", "Football") if isinstance(sport_obj, dict) else "Football"
            leagues.append(
                RemoteLeagueResult(
                    id=lid,
                    name=name,
                    country=category.get("name", "Unknown") if isinstance(category, dict) else "Unknown",
                    slug=entity.get("slug"),
                    sport=sport_name,
                )
            )
    return leagues[:20]


# Etkileşimli yenileme: kullanıcı beklerken reddedilen bir isteği dakikalarca yeniden deneme
_REFRESH_MAX_RETRIES = 2


def _refresh_league_seasons_sync(league_id: int) -> dict:
    """
    Sezon listesini SofaScore'dan yeniler. "success" yalnızca SofaScore gerçekten yanıt verdiğinde döner (liste
    boş olabilir: ligin sezonu yok). Çekilemediyse _UpstreamFailure(reason) fırlatılır.
    """
    context = build_context(_cm())
    before = bridge_health.snapshot()
    try:
        seasons = context.season_fetcher.fetch_seasons_checked(league_id, max_retries=_REFRESH_MAX_RETRIES)
    except SofaScoreScraperError as e:
        reason = upstream.reason_for(e, before)
        logger.error(f"Season list refresh for league {league_id} failed ({reason}): {e}")
        raise _UpstreamFailure(reason) from e
    return {"status": "success", "seasons": seasons}


def _with_sport(leagues: Dict[int, str]) -> List[LeagueModel]:
    """
    Liglerin sporlarıyla listesi. Spor: kullanıcının kaydettiği (config/league_sports.json), yoksa indirilmiş
    veriden bilinen (katalog). Hiçbir dosya yazılmaz.
    """
    manager = _cm()
    found = league_sports.sports_for(manager.league_config_path, manager.get_data_dir(), leagues.keys())
    return [LeagueModel(id=k, name=v, sport=found.get(k)) for k, v in leagues.items()]


def _leagues_with_sport_sync() -> List[LeagueModel]:
    return _with_sport(_cm().get_leagues())


@router.get("/leagues", response_model=List[LeagueModel])
async def get_leagues() -> List[LeagueModel]:
    return await asyncio.to_thread(_leagues_with_sport_sync)


@router.post("/leagues", response_model=LeagueModel)
def add_league(league: LeagueCreate) -> LeagueModel:
    manager = _cm()
    success = manager.add_league(league.name, league.id)
    if not success:
        raise HTTPException(status_code=400, detail="League ID or Name already exists.")
    sport = league_sports.normalize_sport(league.sport)
    if sport:
        league_sports.set_sport(manager.league_config_path, league.id, sport)
    return LeagueModel(id=league.id, name=league.name, sport=sport)


@router.patch("/leagues/{league_id}", response_model=LeagueModel)
def update_league(league_id: int, body: LeagueUpdate) -> LeagueModel:
    """Set (or clear, with sport=null) the sport of a configured league."""
    manager = _cm()
    leagues = manager.get_leagues()
    if league_id not in leagues:
        raise HTTPException(status_code=404, detail="League not found.")
    sport = league_sports.normalize_sport(body.sport)
    if body.sport and not sport:
        raise HTTPException(status_code=422, detail=f"Unknown sport. Use one of: {', '.join(league_sports.SPORTS)}.")
    league_sports.set_sport(manager.league_config_path, league_id, sport)
    return LeagueModel(id=league_id, name=leagues[league_id], sport=sport)


@router.delete("/leagues/{league_id}")
def delete_league(league_id: int) -> Dict[str, str]:
    # Çalışan iş lig adını (dizin adı) ve sporunu yapılandırmadan okur: iş sürerken lig silinmez (409 job_running)
    manager = _cm()
    with deps.job_store().exclusive("league_delete"):
        success = manager.remove_league(league_id)
        if not success:
            raise HTTPException(status_code=404, detail="League not found.")
        league_sports.set_sport(manager.league_config_path, league_id, None)
    return {"status": "success", "message": f"League {league_id} deleted."}


@router.get("/leagues/search", response_model=List[LeagueModel])
def search_leagues(q: str = Query(..., min_length=2)) -> List[LeagueModel]:
    leagues = _cm().get_leagues()
    return _with_sport({lid: name for lid, name in leagues.items() if q.lower() in name.lower()})


@router.post("/leagues/search-remote", response_model=List[RemoteLeagueResult])
async def search_remote_leagues(q: str = Query(..., min_length=2)):
    """
    Search SofaScore for leagues the user wants to add. On failure: {"detail": {"reason", "message"}} with reason
    blocked, browser, rate_limited, network or upstream.

    POST: every call sends a live request to SofaScore (it uses the request budget and may start the browser);
    as a GET another site could trigger it on the user's behalf.
    """
    try:
        return await asyncio.to_thread(_search_remote_leagues_sync, q)
    except _UpstreamFailure as e:
        raise upstream.http_error(e.reason) from e


def _stored_seasons(league_id: int) -> Optional[List[Any]]:
    """
    Ligin saklanan sezon listesi, katalogdan; listesi yoksa None. Ligin birden çok dosyası varsa en yenisi
    geçerlidir (kural: src/services/tournaments.py). Lig yapılandırılmışsa adı da verilir.
    """
    manager = _cm()
    store = store_api.open_store(manager.get_data_dir())
    return tournaments.seasons_of(store, league_id, name=manager.get_league_by_id(league_id))


@router.get("/leagues/{league_id}/seasons")
def get_league_seasons(league_id: int):
    """The season list of a league as stored locally."""
    try:
        seasons = _stored_seasons(league_id)
    except Exception as e:
        logger.error(f"Error reading seasons for league {league_id}: {e}")
        return {"seasons": [], "fetched": False}
    return {"seasons": seasons or [], "fetched": seasons is not None}


@router.post("/leagues/{league_id}/seasons/refresh")
async def refresh_league_seasons(league_id: int):
    """
    Refresh the season list of a league from SofaScore. On failure: {"detail": {"reason", "message"}} with
    reason blocked, browser, rate_limited, network, not_found (SofaScore has no league with this id) or upstream.
    """
    try:
        return await asyncio.to_thread(_refresh_league_seasons_sync, league_id)
    except _UpstreamFailure as e:
        raise upstream.http_error(e.reason) from e
    except Exception as e:
        logger.error(f"Failed to refresh seasons for league {league_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh seasons")


# =================================================================================================
# Maç listesi, maç detayı ve eksik detaylar (eski src/web/routes/matches.py)
# =================================================================================================


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
    Maç listesi, depodan (QueryService.matches_legacy). "Yalnızca bitmiş maçlar" ayarı okurken uygulanır
    (panodaki sayımla aynı kural); dışa aktarma CSV'sine geri düşülmez (tasarım kararı S14).
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
        league_names=_cm().get_leagues(),
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
    Bir ligin (verildiyse bir sezonun) listelerde geçen maçlarından detayı indirilmemiş olanlar, depodan
    (QueryService.missing_details_legacy); en çok `_MISSING_LIST_LIMIT` satır.
    """
    return QueryService(open_store(data_dir)).missing_details_legacy(
        league_id, season_id, only_finished=only_finished_setting(), limit=_MISSING_LIST_LIMIT)


def _get_match_details_sync(match_id: str) -> Dict[str, Any]:
    """
    Maçın saklanan detayı (`basic` ve yükü olan dilimler), depodan (QueryService.match_detail_legacy). Maç
    bilinmiyorsa ya da olay yükü yoksa 404; depo açılamıyor ya da okunamıyorsa 500.
    """
    try:
        detail = QueryService(open_store(_cm().get_data_dir())).match_detail_legacy(int(match_id))
    except Exception as e:
        logger.error(f"Error loading match {match_id}: {e}")
        raise _SyncHttpError(500, "Error parsing match data.")
    if detail is None:
        raise _SyncHttpError(404, "Match details not found.")
    return detail


def _single_fetch_reason(outcome: "Outcome", before: Optional[Dict[str, Any]]) -> str:
    """
    Başarısız bir isteğin sonucu → src/web/upstream.py'deki neden: 429/503 → rate_limited; 403 → blocked
    (challenge'ı çözecek tarayıcı açılamadıysa browser); zaman aşımı/bağlantı hatası → network; gerisi → upstream.
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
    bütün dilimleri, "yalnızca bitmiş maçlar" ayarı ve bu çekimin kendi devre kesicisi.

    SofaScore /event isteğini ya da istenen dilimlerin hepsini reddettiyse, ya da devre kesildiyse, tipli hatayı
    (src/web/upstream.py) fırlatır. Maç SofaScore'da yoksa ya da bitmemişse 404; dilimlerden biri bile yanıt
    aldıysa "success". Kayıt yazılamadıysa 500.
    """
    from src import utils
    from src.client.context import request_context
    from src.services.pipeline import ITEM_SKIPPED, SKIP_BREAKER, FetchPipeline

    manager = _cm()
    try:
        store = open_store(manager.get_data_dir())
        item = _single_item(store, int(match_id))
        breaker = request_breaker.CircuitBreaker.from_config(manager)
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
    """The stored matches of one season of a league."""
    data_dir = _cm().get_data_dir()
    return await asyncio.to_thread(_get_season_matches_sync, season_id, league_id, data_dir)


@router.get("/leagues/{league_id}/missing-details")
async def get_missing_details(league_id: int, season_id: Optional[int] = None):
    """The listed matches of a league whose details are not downloaded."""
    data_dir = _cm().get_data_dir()
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
    data_dir = _cm().get_data_dir()
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
    """Fetch the details of one match now and wait for the result."""
    if deps.job_store().writer_busy():
        # Veri dizinine yazan biri var: bu sürecin işi ya da `writer` kilidini tutan başka bir süreç. Aynı
        # dosyalara yazıyor ve istek hızını zaten kullanıyor: diğer uç noktalar gibi 409 job_running
        raise JobRunningError()
    try:
        return await asyncio.to_thread(_fetch_single_match_sync, str(match_id))
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


# =================================================================================================
# Arka plan işi: başlatma, durum, iptal, iş geçmişi, bağlantı testi (eski src/web/routes/scrape.py ve
# src/web/fetch_job.py)
# =================================================================================================


class FetchSelection(BaseModel):
    league_id: int
    season_ids: Optional[List[int]] = None
    match_ids: Optional[List[int]] = None


class FetchRequest(BaseModel):
    league_id: Optional[int] = None
    mode: Literal["full", "details"] = "full"
    selections: Optional[List[FetchSelection]] = None


def build_context(config_manager: Any) -> Any:
    """İşin servis bağlamı (src/services/context.build_context); testler bu adı değiştirir."""
    from src.services.context import build_context as build

    return build(config_manager)


def job_manager() -> "JobManager":
    """Web sürecinin iş yöneticisi: yolların da koruma için kullandığı, süreç genelindeki iş deposunun üzerinde."""
    return deps.job_manager()


def _spec_from_payload(payload: FetchRequest) -> "SyncSpec":
    """/api/fetch gövdesi → servis belirtimi. Boş ve verilmemiş listeler aynı anlama gelir."""
    from src.services.sync import SyncSelection, SyncSpec

    return SyncSpec(
        mode=payload.mode,
        league_id=payload.league_id,
        selections=tuple(
            SyncSelection(
                league_id=s.league_id,
                season_ids=tuple(s.season_ids or ()),
                match_ids=tuple(s.match_ids or ()),
            )
            for s in payload.selections or ()
        ),
    )


def _summary(payload: FetchRequest) -> str:
    """İşin ilk günlük satırındaki hedef: seçim sayısı, lig ID'si ya da "All Leagues"."""
    if payload.selections:
        return f"{len(payload.selections)} targeted selection(s)"
    return str(payload.league_id) if payload.league_id else "All Leagues"


def _open_store(ctx: Any) -> None:
    """
    İşin veri dizinini tam bir depo yapar: bağlamın deposuna ilk erişim `.meta/` altında eksik olanları kurar.
    Depo açılamazsa iş yine çalışır (bugünkü gibi dosyalara doğrudan yazar); neden uyarı olarak loglanır.
    """
    try:
        getattr(ctx, "store", None)
    except Exception as e:  # depo açılamadı (daha yeni düzen, meşgul ya da bozuk dosya): iş bunsuz da çalışır
        logger.warning("The store of the data directory could not be opened; the job runs without it: %s", e)


def _error_info(exc: BaseException) -> "ErrorInfo":
    """İşi durduran hata, hata tablosundaki koduyla; metin log satırları gibi maskelenir."""
    from src.jobs.model import ErrorInfo

    error = to_platform_error(exc)
    return ErrorInfo(code=error.code, message=redact_text(error.message), details=error.details)


def _fetch(handle: "JobHandle", payload: FetchRequest, spec: "SyncSpec") -> "JobOutcome":
    """İşin gövdesi: servisi çalıştırır ve sonucunu işin bitişine çevirir. Hata fırlatmaz."""
    from src.i18n import get_i18n
    from src.jobs.manager import JobOutcome
    from src.jobs.model import JobState
    from src.services.sync import SyncService

    tracker = handle.progress
    summary = _summary(payload)
    handle.log(f"Starting fetch for {summary}")
    logger.info("Background fetch started. job_id=%s Target: %s", handle.id, summary)

    try:
        # Servis tutamacın iptal sorusunu istek bağlamına kurar: işin her isteği ona bakar, böylece "Durdur"
        # saniyenin kesri içinde etkili olur. 429/403 geri çekilmeleri de kartta geri sayım olur.
        ctx = build_context(_cm())
        _open_store(ctx)
        result = SyncService(ctx).run(spec, handle=handle)
    except StorageError as e:
        # Kalıcı depolama hatası (disk dolu, izin yok): kalan maçlar da yazılamaz, iş durur
        params = {"path": e.path or "?", "reason": e.detail or str(e)}
        message = get_i18n().t("storage_error_abort", **params)
        logger.error(f"Background fetch aborted, data could not be written: {e}")
        print(f"--> Background Task FAILED: {message}")
        return JobOutcome(
            state=JobState.FAILED,
            result={"error": "storage", "error_path": e.path, **tracker.result()},
            error=_error_info(e),
            message=message,
            code="storage_error_abort",
            params=params,
        )
    except Exception as e:
        # Hata metni iş kaydına yazılır ve API'den okunur: bir istek hatası proxy adresini parolasıyla
        # taşıyabilir, bu yüzden log satırları gibi maskelenir
        error_msg = redact_text(str(e))
        logger.error(f"Background update failed: {e}")
        logger.error(traceback.format_exc())
        print(f"--> Background Task FAILED: {error_msg}")
        return JobOutcome(state=JobState.FAILED, error=_error_info(e), message=f"Error: {error_msg}")

    if result.state == "cancelled":
        return JobOutcome(state=JobState.CANCELLED, message="Cancelled")

    empty_n = result.schedule_empty_seasons
    code = None
    params: Mapping[str, Any] = {}
    if result.breaker:
        code, params = "fetch_stopped_by_breaker", {"reason": result.breaker}
        message = get_i18n().t(code, **params)
    elif empty_n > 0:
        code = "fetch_completed_with_warning"
        message = get_i18n().t(code)
    else:
        message = "Background Task Completed Successfully."
    print("--> Background Task Completed Successfully.")
    logger.info("Background update completed.")
    # Bitiş durumu servisin sonucudur: devre kesildiyse ya da bir maç indirilemediyse `partial`
    return JobOutcome(
        state=JobState(result.state),
        result={"schedule_empty_seasons": empty_n, **result.progress},
        message=message,
        code=code,
        params=params,
    )


def run_fetch_job(job_id: str, payload: FetchRequest) -> None:
    """
    Başlatılmış /api/fetch işini çalıştırır (kendi thread'inde; bkz. `trigger_fetch`). İş yöneticisi işin bitişini
    her durumda yazar (gövde hata fırlatsa da) ve `writer` kilidini bırakır.
    """
    from src.jobs.manager import JobNotActive

    spec = _spec_from_payload(payload)
    try:
        job_manager().run(
            job_id,
            lambda handle: _fetch(handle, payload, spec),
            phases=spec.job_phases,
            on_change=deps.refresh_job_mirror,
        )
    except JobNotActive:
        # İş, thread'i başlamadan bitirilmiş (ör. depo başka bir dizine taşınmış): yapılacak bir şey yok
        logger.warning("Fetch job %s is no longer the running job of this process; nothing was fetched.", job_id)
    finally:
        deps.refresh_job_mirror()


@router.get("/scrape/status")
async def get_scrape_status():
    """Get the current status of the background scraper."""
    return deps.refresh_job_mirror()


@router.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)):
    """Recent scrape jobs (newest first)."""
    return {"jobs": deps.job_store().list_jobs(limit=limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = deps.job_store().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/scrape/stream")
async def scrape_status_stream(request: Request):
    """SSE endpoint for real-time scraper status updates."""
    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError:
        raise HTTPException(status_code=501, detail="SSE not available, use polling instead")

    async def event_generator():
        last_state_json = None
        while True:
            if await request.is_disconnected():
                break
            state = deps.refresh_job_mirror()
            current_json = json.dumps(state, default=str)
            if current_json != last_state_json:
                yield {"event": "update", "data": current_json}
                last_state_json = current_json
                if not state.get("is_running") and state.get("status") != "Running":
                    yield {"event": "done", "data": current_json}
                    break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.post("/scrape/cancel")
def cancel_scrape():
    """Cancel the running background fetch (in this server, or a job of another process on the data folder)."""
    store = deps.job_store()
    if not store.request_cancel():
        # Bu süreçte çalışan iş yok. Veri dizininde başka bir sürecin işi çalışıyorsa (ör. komut satırından
        # başlatılmış bir indirme) iptal onun satırına yazılır; o süreç bayrağı bir saniye içinde okur.
        manager = deps.job_manager()
        other = manager.active()
        if other is None or not manager.cancel(other.id):
            raise HTTPException(status_code=400, detail="No scraping process is running.")
    deps.refresh_job_mirror()
    return {"status": "cancelling", "message": "Cancel signal sent."}


@router.post("/fetch")
async def trigger_fetch(payload: FetchRequest):
    """Trigger a background fetch operation. Mode 'full' or 'details'."""
    # async kalmalı: "çalışıyor mu" kontrolü ile işin başlatılması arasında await yok, bu yüzden olay döngüsünde
    # atomik. Threadpool'da iki eşzamanlı istek iki iş başlatabilirdi.
    import dataclasses
    import threading

    from src.jobs.manager import local_origin
    from src.jobs.model import JobKind

    if deps.refresh_job_mirror().get("is_running"):
        raise HTTPException(status_code=409, detail="Scraping process is already running.")
    # İş yöneticisi `writer` kilidini alır ve işi kaydeder: kilit başka bir süreçteyse ya da bir veri işlemi
    # sürüyorsa 409 (job_running / data_operation_running; app.py).
    job = job_manager().start(
        JobKind.FETCH,
        dataclasses.asdict(_spec_from_payload(payload)),
        origin=local_origin("api"),
        payload=payload.model_dump(),
    )
    job_id = job.id
    deps.refresh_job_mirror()

    # Ayrı thread: uzun indirmeler Starlette'in ortak thread havuzunu tutmasın
    threading.Thread(target=_run_fetch_job_thread, args=(job_id, payload), name="sofascore-fetch", daemon=True).start()
    return {"status": "started", "job_id": job_id, "message": "Update started."}


def _run_fetch_job_thread(job_id: str, payload: FetchRequest) -> None:
    """Thread gövdesi: `run_fetch_job` çağrı anında aranır (testler onu değiştirir)."""
    run_fetch_job(job_id, payload)


@router.get("/status")
async def system_status():
    """Get system status info."""
    manager = _cm()
    return {
        # Tek kaynak: pyproject.toml (src/version.py); /health ve OpenAPI belgesi de aynı değeri verir
        "version": __version__,
        "leagues_count": len(manager.get_leagues()),
        "language": manager.get_language()
    }


@router.get("/bypass/status")
async def bypass_status():
    """Get Cloudflare Turnstile & BrowserBridge status."""
    from src.challenge_solver import _is_token_valid, get_cached_token
    token = get_cached_token()
    return {
        "status": "ready",
        "has_token": bool(token),
        "is_valid": _is_token_valid(),
        "mechanism": "BrowserBridge (Chrome persistent context + Turnstile auto-solve)",
        # ok / degraded / blocked + son başarılı istek, ardışık başarısızlık, son hata (web arayüzü afişi)
        "health": bridge_health.snapshot(),
    }


def _browser_ready() -> bool:
    """Köprünün açık bir tarayıcı sayfası var mı? (Yalnızca okur; tarayıcı başlatmaz.)"""
    from src.challenge_solver import BrowserBridge

    try:
        page = BrowserBridge.get_instance().page
        return bool(page) and not page.is_closed()
    except Exception:
        return False


@router.post("/bypass/test")
async def bypass_test():
    """
    Test live connectivity and challenge solving through BrowserBridge.

    Sends ONE live request to SofaScore, only when the user asks (Settings → connection test). The answer is
    always 200: `success`, on failure `reason` (blocked, browser, network or upstream), the browser and
    challenge state and the bridge health snapshot.
    """
    from src.challenge_solver import _is_token_valid, fetch_api_via_browser, get_cached_token

    before = bridge_health.snapshot()
    reason: Optional[str] = None
    events_count = 0
    try:
        data = await fetch_api_via_browser("/sport/football/events/live")
    except Exception as e:
        # Tarayıcı başlatılamadı (köprü bunu sağlık durumuna yazar) ya da istek zaman aşımına uğradı
        logger.warning(f"Connection test failed: {e.__class__.__name__}: {e}")
        reason = upstream.BROWSER if upstream.browser_failed_since(before) else upstream.NETWORK
    else:
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            events_count = len(data["events"])
        elif data is None:
            # Köprü yalnızca 403'ü sağlık durumuna yazar: yeni bir kayıt varsa SofaScore reddetti. Yoksa yanıt
            # alınamadı (ağ hatası, zaman aşımı; nadiren 429/5xx): köprü ayrıntı vermez.
            after = bridge_health.snapshot()
            refused = (
                after["consecutive_failures"] > before["consecutive_failures"]
                or after["last_failure_at"] != before["last_failure_at"]
            )
            reason = upstream.BLOCKED if refused else upstream.NETWORK
        else:
            reason = upstream.UPSTREAM  # yanıt geldi ama canlı maç listesi değil

    result = {
        "success": reason is None,
        "reason": reason,
        "message": "BrowserBridge connection verified successfully." if reason is None else upstream.detail(reason)["message"],
        "browser_ready": _browser_ready(),
        "has_token": bool(get_cached_token()),
        "is_valid": _is_token_valid(),
        "health": bridge_health.snapshot(),
    }
    if reason is None:
        result["events_count"] = events_count
    return result


# =================================================================================================
# Ayarlar (eski src/web/routes/settings.py)
# =================================================================================================


_ALLOWED_API_HOSTS = {"www.sofascore.com", "api.sofascore.com"}
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _no_control_chars(v: Optional[str]) -> Optional[str]:
    # .env satır tabanlı: yeni satır başka bir değişken enjekte eder
    if v is not None and any(c in v for c in "\r\n\x00"):
        raise ValueError("control characters are not allowed")
    return v


class SettingsUpdate(BaseModel):
    api_base_url: Optional[str] = None
    use_proxy: Optional[bool] = None
    proxy_url: Optional[str] = Field(default=None, max_length=500)
    data_dir: Optional[str] = Field(default=None, max_length=500)
    use_color: Optional[bool] = None
    date_format: Optional[str] = Field(default=None, max_length=50)
    language: Optional[Literal["tr", "en"]] = None
    max_concurrent: Optional[int] = Field(default=None, ge=1, le=50)
    request_rate_limit: Optional[float] = Field(default=None, ge=0, le=1000)
    wait_time_min: Optional[float] = Field(default=None, ge=0, le=60)
    wait_time_max: Optional[float] = Field(default=None, ge=0, le=60)
    request_timeout: Optional[int] = Field(default=None, ge=1, le=300)
    max_retries: Optional[int] = Field(default=None, ge=0, le=10)
    rate_limit_threshold_consecutive: Optional[int] = Field(default=None, ge=1, le=1000)
    rate_limit_threshold_ratio: Optional[float] = Field(default=None, gt=0, le=1)
    server_error_threshold_consecutive: Optional[int] = Field(default=None, ge=1, le=1000)
    fetch_only_finished: Optional[bool] = None
    save_empty_rounds: Optional[bool] = None
    refresh_window_hours: Optional[float] = Field(default=None, ge=0, le=720)
    log_level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = None
    debug: Optional[bool] = None

    _strip_controls = field_validator("api_base_url", "proxy_url", "data_dir", "date_format")(
        classmethod(lambda cls, v: _no_control_chars(v))
    )

    @field_validator("api_base_url")
    @classmethod
    def _api_host_allowed(cls, v: Optional[str]) -> Optional[str]:
        from urllib.parse import urlparse

        if v is None:
            return v
        u = urlparse(v)
        if u.scheme != "https" or u.hostname not in _ALLOWED_API_HOSTS:
            raise ValueError(f"api_base_url must be https on {sorted(_ALLOWED_API_HOSTS)}")
        return v

    @field_validator("proxy_url")
    @classmethod
    def _proxy_scheme(cls, v: Optional[str]) -> Optional[str]:
        from urllib.parse import urlparse

        if v:
            u = urlparse(v)
            if u.scheme not in ("http", "https", "socks5", "socks5h") or not u.hostname:
                raise ValueError("proxy_url must be http(s):// or socks5://host:port")
        return v

    @field_validator("data_dir")
    @classmethod
    def _data_dir_contained(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == _cm().get_data_dir():
            return v
        resolved = Path(v).expanduser()
        if not resolved.is_absolute():
            resolved = _REPO_ROOT / resolved
        resolved = resolved.resolve()
        home = Path.home().resolve()
        if resolved in (home, _REPO_ROOT) or not (
            resolved.is_relative_to(_REPO_ROOT) or resolved.is_relative_to(home)
        ):
            raise ValueError("data_dir must be a folder inside the project or your home directory")
        return v


@router.get("/settings")
def get_all_settings():
    """Get all current settings."""
    manager = _cm()
    return {
        "language": manager.get_language(),
        # Dil açıkça ayarlanmış mı (APP_LANGUAGE)? Değilse `language` sunucunun sistem dilidir ve arayüz ilk
        # ziyarette onu değil, tarayıcının dilini izler.
        "language_explicit": explicit_language() is not None,
        "api_base_url": manager.get_api_base_url(),
        "use_proxy": manager.get_use_proxy(),
        # Parola maskeli döner (kullanıcı adı ve sunucu görünür kalır); tam değer yalnızca .env'de
        "proxy_url": mask_proxy_url(manager.get_proxy_url()),
        "data_dir": manager.get_data_dir(),
        "use_color": manager.get_use_color(),
        "date_format": manager.get_date_format(),
        "max_concurrent": manager.get_max_concurrent(),
        "request_rate_limit": manager.get_request_rate_limit(),
        "wait_time_min": manager.get_wait_time_min(),
        "wait_time_max": manager.get_wait_time_max(),
        "request_timeout": manager.get_request_timeout(),
        "max_retries": manager.get_max_retries(),
        "rate_limit_threshold_consecutive": manager.get_rate_limit_threshold_consecutive(),
        "rate_limit_threshold_ratio": manager.get_rate_limit_threshold_ratio(),
        "server_error_threshold_consecutive": manager.get_server_error_threshold_consecutive(),
        "fetch_only_finished": os.getenv("FETCH_ONLY_FINISHED", "true").lower() == "true",
        "save_empty_rounds": os.getenv("SAVE_EMPTY_ROUNDS", "false").lower() == "true",
        "refresh_window_hours": refresh_window_hours(),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "debug": os.getenv("DEBUG", "false").lower() == "true",
    }


def _abs_data_dir(value: str) -> str:
    # İş deposu ve veri uçları göreli DATA_DIR'i çalışma dizinine göre çözer (bkz. jobs.get_job_store)
    return os.path.abspath(value)


@router.post("/settings")
def update_settings(settings: SettingsUpdate):
    """
    Update application settings (written to `.env`).

    Changing the data folder is refused while a job runs (409 `job_running`; nothing
    is written); without a job the change takes effect at once and the job store moves to the new folder. A
    folder that cannot be used is 400 `data_dir_unusable`.
    """
    manager = _cm()
    if settings.proxy_url:
        # Her şeyden önce: 422 (parola yeniden yazılmalı) hiçbir ayar yazılmadan ve iş deposu taşınmadan döner
        settings.proxy_url = restore_proxy_password(settings.proxy_url.strip(), manager.get_proxy_url())

    new_dir = settings.data_dir
    if new_dir is None or _abs_data_dir(new_dir) == _abs_data_dir(manager.get_data_dir()):
        return _apply_settings(settings)

    job_store = deps.job_store()
    with job_store.exclusive("data_dir_change"):
        try:
            # Önce depo: dizin oluşturulamıyor, yazılamıyor ya da Store onu açamıyorsa .env'e hiç dokunulmaz.
            # StoreError: state.db koddan yeni, dosya bir state.db değil, geçiş başarısız, yazma kilidi alınamadı
            # ya da dizinde başka bir süreç yazarken geçiş gerekiyor. rebind bu durumların hepsinde depoyu eski
            # dizinde bırakır.
            job_store.rebind(default_db_path(_abs_data_dir(new_dir)))
        except (OSError, sqlite3.Error, StoreError) as e:
            logger.error(f"The new data folder cannot be used: {new_dir}: {e}")
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "data_dir_unusable",
                    "message": "The data folder cannot be created or written to; nothing was changed.",
                },
            )
        try:
            result = _apply_settings(settings)
        finally:
            # .env yazılamadıysa depo geçerli DATA_DIR'e geri döner; yazıldıysa bu çağrı bir şey yapmaz
            moved = not job_store.rebind(default_db_path(_abs_data_dir(manager.get_data_dir())))
            deps.refresh_job_mirror()
    if moved:
        result["data_dir_changed"] = True
        result["message"] = (
            "Settings updated successfully. The data folder changed: downloads and the job history "
            "now use the new folder; files in the old folder were not moved."
        )
    return result


# Eski alan → (.env değişkeni, yazılacak metin, ayar modelindeki anahtar)
_ENV_MAP: Dict[str, Any] = {
    "language": ("APP_LANGUAGE", lambda v: v, "display.language"),
    "api_base_url": ("API_BASE_URL", lambda v: v, "client.base_url"),
    "use_proxy": ("USE_PROXY", lambda v: str(v).lower(), "client.use_proxy"),
    "proxy_url": ("PROXY_URL", lambda v: v, "client.proxy"),
    "data_dir": ("DATA_DIR", lambda v: v, "storage.data_dir"),
    "use_color": ("USE_COLOR", lambda v: str(v).lower(), "display.use_color"),
    "date_format": ("DATE_FORMAT", lambda v: v, "display.date_format"),
    "max_concurrent": ("MAX_CONCURRENT", lambda v: str(v), "client.max_concurrent"),
    "request_rate_limit": ("REQUEST_RATE_LIMIT", lambda v: f"{float(v):g}", "client.rate"),
    "wait_time_min": ("WAIT_TIME_MIN", lambda v: str(v), "client.wait_time_min"),
    "wait_time_max": ("WAIT_TIME_MAX", lambda v: str(v), "client.wait_time_max"),
    "request_timeout": ("REQUEST_TIMEOUT", lambda v: str(v), "client.timeout_seconds"),
    "max_retries": ("MAX_RETRIES", lambda v: str(v), "client.retries"),
    "rate_limit_threshold_consecutive": ("RATE_LIMIT_THRESHOLD_CONSECUTIVE", lambda v: str(v),
                                         "breaker.rate_limit_consecutive"),
    "rate_limit_threshold_ratio": ("RATE_LIMIT_THRESHOLD_RATIO", lambda v: str(v), "breaker.rate_limit_ratio"),
    "server_error_threshold_consecutive": ("SERVER_ERROR_THRESHOLD_CONSECUTIVE", lambda v: str(v),
                                           "breaker.server_error_consecutive"),
    "fetch_only_finished": ("FETCH_ONLY_FINISHED", lambda v: str(v).lower(), "fetch.only_finished"),
    "save_empty_rounds": ("SAVE_EMPTY_ROUNDS", lambda v: str(v).lower(), "fetch.save_empty_rounds"),
    "refresh_window_hours": ("REFRESH_WINDOW_HOURS", lambda v: f"{float(v):g}", "refresh.window_hours"),
    "log_level": ("LOG_LEVEL", lambda v: v, "log.level"),
    "debug": ("DEBUG", lambda v: str(v).lower(), "log.debug"),
}


def _apply_settings(settings: SettingsUpdate) -> dict:
    manager = _cm()
    try:
        updated = False
        settings_dict = settings.model_dump(exclude_none=True)
        for field, value in settings_dict.items():
            if field in _ENV_MAP:
                env_key, converter, _key = _ENV_MAP[field]
                if manager.update_env_variable(env_key, converter(value)):
                    updated = True

        if updated:
            manager.reload_config()
            return {"status": "success", "message": "Settings updated successfully."}
        else:
            return {"status": "no_change", "message": "No settings were changed."}

    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")


# =================================================================================================
# Pano, istatistikler, yedek, temizleme, CSV (eski src/web/routes/data.py)
# =================================================================================================


def _data_summary(data_dir: str, leagues: Dict[int, str]) -> DataSummary:
    """Veri dizininin özeti (katalogdan); yapılandırılmış ligler maçları olmasa da dökümde yer alır."""
    return StatusService(store_api.open_store(data_dir)).summary(tournament_ids=tuple(leagues))


def _build_dashboard_sync(data_dir: str, leagues: Dict[int, str]) -> dict:
    summary = _data_summary(data_dir, leagues)
    cards = []
    for lid, name in leagues.items():
        st = stats_service.league_counts(summary, lid, name)
        cards.append({k: st[k] for k in ("id", "name", "seasons", "matches", "details", "coverage", "last_update")})
    disk = stats_service.disk_usage(summary)
    return {
        "leagues": cards,
        "disk_usage": {k: disk[k] for k in ("seasons", "matches", "details", "total", "formatted_total")},
        "totals": {
            "leagues": len(leagues),
            "matches": sum(c["matches"] for c in cards),
            "details": sum(c["details"] for c in cards),
        },
    }


def _compute_system_stats_sync(data_dir: str, leagues: Dict[int, str]) -> Dict[str, Any]:
    stats = stats_service.system_counts(_data_summary(data_dir, leagues), leagues)
    stats["league_breakdown"] = sorted(
        (
            {k: b[k] for k in ("id", "name", "matches", "details", "coverage")}
            for b in stats["league_breakdown"]
            if b["matches"] or b["details"]
        ),
        key=lambda b: b["matches"],
        reverse=True,
    )
    return stats


@router.get("/dashboard")
async def get_dashboard():
    """Dashboard overview with league cards, disk usage, and quick stats."""
    manager = _cm()
    data_dir = manager.get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)
    leagues = dict(manager.get_leagues())
    return await asyncio.to_thread(_build_dashboard_sync, data_dir, leagues)


@router.get("/stats/system")
async def get_system_stats():
    """Get detailed system statistics (counts per league, disk use)."""
    try:
        manager = _cm()
        data_dir = manager.get_data_dir()
        if not os.path.isabs(data_dir):
            data_dir = os.path.abspath(data_dir)

        leagues = dict(manager.get_leagues())
        return await asyncio.to_thread(_compute_system_stats_sync, data_dir, leagues)

    except Exception as e:
        logger.error(f"Error generating stats: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to generate statistics")


BackupScope = Literal["all", "config", "seasons", "matches", "match_details"]


DataScope = Literal["all", "seasons", "matches", "match_details"]


_BACKUP_NAME_RE = re.compile(r"^backup_[a-z_]+_\d{8}_\d{6}\.zip$")


def _backups_dir() -> str:
    """Yedekler veri dizininde tutulur: web sunucusunun statik olarak servis ettiği bir yerde değil."""
    return os.path.join(os.path.abspath(_cm().get_data_dir()), "backups")


def _create_backup_sync(scope: str, include_env: bool = False) -> dict:
    """
    Yedeği Store yazar (BackupService → `Store.backup`). Lig dosyası ve spor eşlemesi pakete girer; .env yalnızca
    açıkça istenirse girer ve dosya adı bunu söyler.
    """
    manager = _cm()
    data_dir = os.path.abspath(manager.get_data_dir())
    config_path = manager.league_config_path
    try:
        info = BackupService(store_api.open_store(data_dir)).create(
            scope,  # type: ignore[arg-type]  # yolun kapsamı Literal ile denetlendi
            config_files=(config_path, league_sports.sidecar_path(config_path)),
            include_secrets=include_env,
        )
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        raise _SyncHttpError(500, "Backup failed") from e
    return {"download_url": f"/api/data/backups/{info.name}", "filename": info.name}


def _clear_data_sync(scope: str) -> dict:
    """Temizlemeyi Store yapar (MaintenanceService → `Store.clear`); katalog aynı kilit altında yeniden kurulur."""
    data_dir = _cm().get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)
    try:
        report = MaintenanceService(store=store_api.open_store(data_dir)).clear(
            scope, confirm=True)  # type: ignore[arg-type]  # yolun kapsamı Literal ile denetlendi
    except Exception as e:
        logger.error(f"Clear data failed: {e}")
        raise _SyncHttpError(500, "Clear data failed") from e
    return {"status": "success", "cleared": list(report.cleared)}


def _export_csv_sync(league_id: Optional[int], data_dir: str):
    """
    CSV dışa aktarımını istekte üretir ve akıtır (ExportService, `legacy-wide-csv` profili); hiçbir dosya yazmaz
    (karar D16).
    """
    from fastapi.responses import StreamingResponse

    from src.services.export import ExportService, ExportSpec

    try:
        prepared = ExportService(store_api.open_store(data_dir)).prepare(ExportSpec(league_id=league_id))
    except Exception as e:
        logger.error(f"CSV export failed: {e}")
        raise _SyncHttpError(500, "CSV generation failed") from e
    if not prepared.available:
        raise _SyncHttpError(404, "No CSV data available. Run a fetch first.")
    if not prepared.rows:
        raise _SyncHttpError(404, f"No exported matches for league {league_id}.")
    filename = f"all_matches_{int(time.time())}.csv"
    if league_id:
        filename = f"league_{league_id}_{filename}"
    return StreamingResponse(
        prepared.chunks(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _run_exclusive(operation: str, fn: Callable[..., Any], *args: Any) -> Any:
    """
    `fn`'i iş deposunun veri işlemi yuvasında çalıştırır: iş çalışıyorsa ya da başka bir veri işlemi sürüyorsa
    JobStoreConflict (409), işlem sürerken de yeni iş başlatılamaz. Yuva worker thread içinde alınır: istemci
    bağlantıyı koparsa bile dosya işlemi bitene kadar tutulur.
    """
    with deps.job_store().exclusive(operation):
        return fn(*args)


@router.post("/data/backup")
async def create_backup(scope: BackupScope = "all", include_env: bool = False):
    """
    Create a backup and return where to download the zip.

    While a job runs a backup with data is refused (409 job_running): the zip would be an inconsistent copy of
    a download in progress. `scope=config` reads only the settings files, which a job does not write.
    """
    try:
        if scope == "config":
            return await asyncio.to_thread(_create_backup_sync, scope, include_env)
        return await asyncio.to_thread(_run_exclusive, "backup", _create_backup_sync, scope, include_env)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/data/backups/{name}")
def download_backup(name: str):
    from fastapi.responses import FileResponse

    from src.store import BackupNotFound

    if not _BACKUP_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        path = open_store(_cm().get_data_dir()).backup.path_of(name)
    except BackupNotFound:
        raise HTTPException(status_code=404, detail="Not Found") from None
    return FileResponse(path, filename=name, media_type="application/zip")


class ClearRequest(BaseModel):
    scope: DataScope = "all"


@router.post("/data/clear")
async def clear_data(req: ClearRequest):
    """
    Delete stored data. Refused while a job runs (409 job_running): deleting would race the job's writes.
    """
    try:
        return await asyncio.to_thread(_run_exclusive, "clear", _clear_data_sync, req.scope)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/export/csv")
async def export_csv(league_id: Optional[int] = None):
    """
    CSV export of the downloaded matches, made on request; read-only, nothing is written to disk. `league_id`:
    only the matches of that league's folder. 404 without downloaded matches.
    """
    data_dir = _cm().get_data_dir()
    try:
        return await asyncio.to_thread(_export_csv_sync, league_id, data_dir)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/export/csv")
async def create_csv_export(league_id: Optional[int] = None):
    """The same answer as GET /api/export/csv (an alias for one release); nothing is written to disk."""
    data_dir = _cm().get_data_dir()
    try:
        return await asyncio.to_thread(_export_csv_sync, league_id, data_dir)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


# =================================================================================================
# Sporlar (eski src/web/routes/sports.py)
# =================================================================================================


class DetailSliceModel(BaseModel):
    key: str
    path: str
    required: bool
    default_enabled: bool


class SportModel(BaseModel):
    slug: str
    name: str
    i18n_key: str
    score_family: str
    slices: List[DetailSliceModel]


def _sport_model(spec: sports.SportSpec) -> SportModel:
    return SportModel(
        slug=spec.slug,
        name=spec.name,
        i18n_key=spec.i18n_key,
        score_family=spec.score_family,
        slices=[
            DetailSliceModel(key=s.key, path=s.path, required=s.counts_in(spec.slug),
                             default_enabled=s.default_enabled)
            for s in sports.DETAIL_SLICES
            if s.applies_to(spec.slug)
        ],
    )


@router.get("/sports", response_model=List[SportModel])
def get_sports() -> List[SportModel]:
    """
    The registered sports, in registry order. `slices` is every slice that applies to the sport, disabled ones
    included (`default_enabled` says which are asked for).
    """
    return [_sport_model(spec) for spec in sports.SPORTS]


# =================================================================================================
# Log ve tanılama (eski src/web/routes/diagnostics.py)
# =================================================================================================


@router.get("/logs")
def recent_logs(
    limit: int = Query(200, ge=1, le=diagnostics.MAX_LOG_ENTRIES, description="Newest entries to return"),
    level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = Query(
        None, description="Only this level and above"
    ),
):
    """The newest entries of the log file, oldest first. With file logging off: `enabled: false`, an empty list."""
    return diagnostics.read_log_entries(limit=limit, min_level=level)


@router.get("/diagnostics")
def diagnostics_summary():
    """The diagnostics summary (the same content as diagnostics.json of the bundle)."""
    return diagnostics.collect(source="web")


@router.get("/diagnostics/bundle")
def diagnostics_bundle(
    log_lines: int = Query(
        diagnostics.DEFAULT_TAIL_LINES, ge=0, le=diagnostics.MAX_TAIL_LINES, description="Log lines in the bundle"
    ),
):
    """A zip to attach to a bug report: diagnostics.json, log_tail.txt and README.txt."""
    data = diagnostics.build_bundle(source="web", log_lines=log_lines)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{diagnostics.bundle_filename()}"',
            "Cache-Control": "no-store",
        },
    )


# =================================================================================================
# Oturum (eski src/web/routes/auth.py)
# =================================================================================================


class LoginRequest(BaseModel):
    token: str = Field(max_length=4096)


@router.get("/auth")
def auth_status(request: Request):
    """Whether an access token is required and whether this caller presents it (the UI shows the sign-in box)."""
    state = v1_auth.state_of(request)
    return {"required": state.required, "authenticated": state.authenticated}


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    """Set the session cookie when the token is right. The token itself is never written to the cookie or the log."""
    token = security.api_token()
    if not token:
        return {"required": False, "authenticated": True}
    if not security.token_matches(body.token.strip()):
        client = request.client.host if request.client else "?"
        logger.warning(f"Access token refused (client: {client})")
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_token", "message": "The access token is not correct."},
        )
    v1_auth.set_session_cookie(response, request, token)
    return {"required": True, "authenticated": True}


@router.post("/auth/logout")
def logout(response: Response):
    """Delete the session cookie (for this browser)."""
    v1_auth.clear_session_cookie(response)
    state = v1_auth.after_logout()
    return {"required": state.required, "authenticated": state.authenticated}


__all__ = [
    "FetchRequest",
    "PROXY_PASSWORD_MASK",
    "SettingsUpdate",
    "build_context",
    "job_manager",
    "mask_proxy_url",
    "router",
    "run_fetch_job",
]
