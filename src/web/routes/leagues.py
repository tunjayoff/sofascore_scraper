"""Lig yapılandırması, lig arama ve sezon listesi uç noktaları."""
from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from src import bridge_health
from src.exceptions import SofaScoreScraperError
from src.web import league_sports, upstream
from src.web.routes.common import (
    _find_league_seasons_json,
    _job_store,
    config_manager,
    logger,
)

router = APIRouter(prefix="/api", tags=["api"])


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


class _UpstreamFailure(Exception):
    """Senkron worker içinde fırlatılır; async route tipli HTTP hatasına çevirir (src/web/upstream.py)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _search_remote_leagues_sync(q: str) -> List[RemoteLeagueResult]:
    """
    SofaScore'da lig arar. Boş liste yalnızca istek başarılı olup hiçbir şey bulunamadığında
    döner; engelleme, ağ hatası ve beklenmeyen yanıt _UpstreamFailure(reason) olarak fırlatılır
    (eskiden hepsi boş listeye dönüşüyor, arayüz "Sonuç yok. Yazımı değiştirin." diyordu).
    """
    from src.utils import make_api_request

    url = f"https://www.sofascore.com/api/v1/search/unique-tournaments/{quote(q, safe='')}"
    before = bridge_health.snapshot()
    try:
        # Etkileşimli arama: 403 bekleme döngüsüyle bir sunucu işçisini dakikalarca tutma
        data = make_api_request(url, max_retries=1, timeout=10, raise_errors=True)
    except SofaScoreScraperError as e:
        reason = upstream.reason_for(e, before)
        # Arama uç noktası sonuç yokken boş liste döndürür: 404 "sonuç yok" değil, beklenmeyen yanıt
        if reason == upstream.NOT_FOUND:
            reason = upstream.UPSTREAM
        logger.error(f"Uzak lig araması başarısız ({reason}): {e}")
        raise _UpstreamFailure(reason) from e

    results = None
    if isinstance(data, dict):
        results = data.get("uniqueTournaments", data.get("results"))
    if not isinstance(results, list):
        logger.error("Uzak lig araması: yanıt beklenen biçimde değil (sonuç listesi yok)")
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
    Sezon listesini SofaScore'dan yeniler. "success" yalnızca SofaScore gerçekten yanıt verdiğinde
    döner (liste boş olabilir: ligin sezonu yok). Çekilemediyse _UpstreamFailure(reason) fırlatılır;
    eskiden diskteki eski liste (ya da boş liste) "success" olarak dönüyordu.
    """
    from src.services.context import build_context

    ctx = build_context(config_manager)
    before = bridge_health.snapshot()
    try:
        seasons = ctx.season_fetcher.fetch_seasons_checked(league_id, max_retries=_REFRESH_MAX_RETRIES)
    except SofaScoreScraperError as e:
        reason = upstream.reason_for(e, before)
        logger.error(f"Lig {league_id} için sezon yenileme başarısız ({reason}): {e}")
        raise _UpstreamFailure(reason) from e
    return {"status": "success", "seasons": seasons}


def _leagues_with_sport_sync() -> List[LeagueModel]:
    leagues = config_manager.get_leagues()
    sports = league_sports.resolve_all(
        config_manager.league_config_path, config_manager.get_data_dir(), leagues.keys()
    )
    return [LeagueModel(id=k, name=v, sport=sports.get(k)) for k, v in leagues.items()]


@router.get("/leagues", response_model=List[LeagueModel])
async def get_leagues() -> List[LeagueModel]:
    return await asyncio.to_thread(_leagues_with_sport_sync)


@router.post("/leagues", response_model=LeagueModel)
def add_league(league: LeagueCreate) -> LeagueModel:
    success = config_manager.add_league(league.name, league.id)
    if not success:
        raise HTTPException(status_code=400, detail="League ID or Name already exists.")
    sport = league_sports.normalize_sport(league.sport)
    if sport:
        league_sports.set_sport(config_manager.league_config_path, league.id, sport)
    return LeagueModel(id=league.id, name=league.name, sport=sport)


@router.patch("/leagues/{league_id}", response_model=LeagueModel)
def update_league(league_id: int, body: LeagueUpdate) -> LeagueModel:
    """Set (or clear, with sport=null) the sport of a configured league."""
    leagues = config_manager.get_leagues()
    if league_id not in leagues:
        raise HTTPException(status_code=404, detail="League not found.")
    sport = league_sports.normalize_sport(body.sport)
    if body.sport and not sport:
        raise HTTPException(status_code=422, detail=f"Unknown sport. Use one of: {', '.join(league_sports.SPORTS)}.")
    league_sports.set_sport(config_manager.league_config_path, league_id, sport)
    return LeagueModel(id=league_id, name=leagues[league_id], sport=sport)


@router.delete("/leagues/{league_id}")
def delete_league(league_id: int) -> Dict[str, str]:
    # Çalışan iş lig adını (dizin adı) ve sporunu yapılandırmadan okur: iş sürerken lig silinmez
    # (409 job_running, bkz. app.py).
    with _job_store.exclusive("league_delete"):
        success = config_manager.remove_league(league_id)
        if not success:
            raise HTTPException(status_code=404, detail="League not found.")
        league_sports.set_sport(config_manager.league_config_path, league_id, None)
    return {"status": "success", "message": f"League {league_id} deleted."}


@router.get("/leagues/search", response_model=List[LeagueModel])
def search_leagues(q: str = Query(..., min_length=2)) -> List[LeagueModel]:
    leagues = config_manager.get_leagues()
    return [LeagueModel(id=lid, name=name) for lid, name in leagues.items() if q.lower() in name.lower()]


class RemoteLeagueResult(BaseModel):
    id: int
    name: str
    country: str
    slug: Optional[str] = None
    sport: Optional[str] = "Football"


@router.post("/leagues/search-remote", response_model=List[RemoteLeagueResult])
async def search_remote_leagues(q: str = Query(..., min_length=2)):
    """
    SofaScore'dan lig ara. Kullanıcının yeni eklemek istediği ligleri bulması için.
    Başarısızlıkta {"detail": {"reason", "message"}}: blocked / browser / rate_limited / network / upstream.

    POST: her çağrı SofaScore'a canlı istek atar (istek bütçesini harcar, tarayıcıyı başlatabilir).
    GET olsaydı başka bir sitedeki <img> bunu kullanıcının adına, kaynak denetimine takılmadan
    tetikleyebilirdi.
    """
    try:
        return await asyncio.to_thread(_search_remote_leagues_sync, q)
    except _UpstreamFailure as e:
        raise upstream.http_error(e.reason) from e


@router.get("/leagues/{league_id}/seasons")
def get_league_seasons(league_id: int):
    """Bir ligin yerel olarak kayıtlı sezon listesini döndürür."""
    data_dir = config_manager.get_data_dir()
    seasons_file = _find_league_seasons_json(data_dir, league_id)
    if not seasons_file:
        return {"seasons": [], "fetched": False}
    try:
        with open(seasons_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        seasons = data.get("seasons", data) if isinstance(data, dict) else data
        return {"seasons": seasons if isinstance(seasons, list) else [], "fetched": True}
    except Exception as e:
        logger.error(f"Error reading seasons for league {league_id}: {e}")
        return {"seasons": [], "fetched": False}


@router.post("/leagues/{league_id}/seasons/refresh")
async def refresh_league_seasons(league_id: int):
    """
    Bir ligin sezon listesini SofaScore'dan yeniler.
    Başarısızlıkta {"detail": {"reason", "message"}}: blocked / browser / rate_limited / network /
    not_found (SofaScore'da bu ID'de lig yok) / upstream.
    """
    try:
        return await asyncio.to_thread(_refresh_league_seasons_sync, league_id)
    except _UpstreamFailure as e:
        raise upstream.http_error(e.reason) from e
    except Exception as e:
        logger.error(f"Failed to refresh seasons for league {league_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh seasons")
