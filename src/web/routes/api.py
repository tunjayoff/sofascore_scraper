import glob
import json
import os
import traceback
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from src.config_manager import ConfigManager
from src.logger import get_logger

router = APIRouter(prefix="/api", tags=["api"])
logger = get_logger("WebAPI")
config_manager = ConfigManager()


class LeagueModel(BaseModel):
    id: int
    name: str


class LeagueCreate(BaseModel):
    id: int
    name: str


class SettingsUpdate(BaseModel):
    api_base_url: Optional[str] = None
    use_proxy: Optional[bool] = None
    proxy_url: Optional[str] = None
    data_dir: Optional[str] = None
    use_color: Optional[bool] = None
    date_format: Optional[str] = None
    language: Optional[str] = None


class FetchRequest(BaseModel):
    league_id: Optional[int] = None
    mode: str = "full"


@router.get("/leagues", response_model=List[LeagueModel])
async def get_leagues() -> List[LeagueModel]:
    leagues = config_manager.get_leagues()
    return [LeagueModel(id=k, name=v) for k, v in leagues.items()]


@router.post("/leagues", response_model=LeagueModel)
async def add_league(league: LeagueCreate) -> LeagueModel:
    success = config_manager.add_league(league.name, league.id)
    if not success:
        raise HTTPException(status_code=400, detail="League ID or Name already exists.")
    return league


@router.delete("/leagues/{league_id}")
async def delete_league(league_id: int) -> Dict[str, str]:
    success = config_manager.remove_league(league_id)
    if not success:
        raise HTTPException(status_code=404, detail="League not found.")
    return {"status": "success", "message": f"League {league_id} deleted."}


@router.get("/leagues/search", response_model=List[LeagueModel])
async def search_leagues(q: str = Query(..., min_length=2)) -> List[LeagueModel]:
    leagues = config_manager.get_leagues()
    return [LeagueModel(id=lid, name=name) for lid, name in leagues.items() if q.lower() in name.lower()]


@router.get("/matches", response_model=List[dict])
async def get_matches(
    limit: int = 100,
    league_id: Optional[str] = None,
    date: Optional[str] = None,
) -> List[dict]:
    data_dir = config_manager.get_data_dir()
    csv_dir = os.path.join(data_dir, "match_details", "processed")
    matches: List[dict] = []

    all_files = glob.glob(os.path.join(csv_dir, "all_matches_*.csv"))
    if all_files:
        latest_file = max(all_files, key=os.path.getctime)
        try:
            df = pd.read_csv(latest_file)
            if league_id and "league_id" in df.columns:
                df = df[df["league_id"] == int(league_id)]
            if date and "match_date" in df.columns:
                df = df[df["match_date"].astype(str).str.contains(date, na=False)]
            matches = df.head(limit).fillna("").to_dict(orient="records")
        except Exception as e:
            logger.error(f"Error reading export CSV: {e}")

    if not matches:
        summary_pattern = os.path.join(data_dir, "matches", "**", "*_summary.csv")
        summary_files = glob.glob(summary_pattern, recursive=True)
        all_summary_data: List[pd.DataFrame] = []
        for file in summary_files:
            try:
                parent_dir = os.path.basename(os.path.dirname(file))
                if league_id and not parent_dir.startswith(f"{league_id}_"):
                    continue
                all_summary_data.append(pd.read_csv(file))
            except Exception as e:
                logger.error(f"Error reading summary CSV {file}: {e}")

        if all_summary_data:
            df_total = pd.concat(all_summary_data)
            if date and "match_date" in df_total.columns:
                df_total = df_total[df_total["match_date"].astype(str).str.contains(date, na=False)]
            if "match_date" in df_total.columns:
                df_total = df_total.sort_values(by="match_date", ascending=False)
            matches = df_total.head(limit).fillna("").to_dict(orient="records")

    return matches

# Global Scraper State
SCRAPER_STATE = {
    "is_running": False,
    "status": "Idle",
    "progress": 0,
    "current_task": "",
    "log": []
}

@router.get("/matches/{match_id}")
async def get_match_details(match_id: str):
    """Retrieve detailed JSON data for a specific match."""
    from src.match_data_fetcher import MatchDataFetcher
    fetcher = MatchDataFetcher(config_manager=config_manager, data_dir=config_manager.get_data_dir())
    
    match_path_info = fetcher._find_match_path(match_id)
    if not match_path_info:
        raise HTTPException(status_code=404, detail="Match details not found.")

    _, _, match_path = match_path_info
    if not os.path.exists(os.path.join(match_path, "basic.json")):
        raise HTTPException(status_code=404, detail="Match details not found.")
    
    result = {}
    try:
        # Load all components (basic, statistics, lineups etc)
        # In this structure, they are all in one file or multiple?
        # SofaScoreScraper tends to save one big JSON or multiple parts.
        # MatchDataFetcher.fetch_match_details saves match_id.json
        full_json_path = os.path.join(match_path, f"{match_id}.json")
        if os.path.exists(full_json_path):
             with open(full_json_path, 'r', encoding='utf-8') as f:
                 result = json.load(f)
        else:
             # Try loading parts if single file doesn't exist
             for component in ["basic", "statistics", "lineups", "incidents"]:
                 c_path = os.path.join(match_path, f"{component}.json")
                 if os.path.exists(c_path):
                     with open(c_path, 'r', encoding='utf-8') as f:
                         result[component] = json.load(f)
                         
    except Exception as e:
        logger.error(f"Error loading match {match_id}: {e}")
        raise HTTPException(status_code=500, detail="Error parsing match data.")
        
    return result

@router.post("/matches/{match_id}/fetch")
async def fetch_single_match(match_id: str):
    """Tek bir maç için detayları senkron olarak çeker."""
    from src.match_data_fetcher import MatchDataFetcher
    fetcher = MatchDataFetcher(config_manager=config_manager, data_dir=config_manager.get_data_dir())
    try:
        result = fetcher.fetch_match_data(match_id)
        if result is None:
            raise HTTPException(status_code=404,
                                detail="Match data could not be fetched (may be unfinished or unavailable).")
        return {"status": "success", "match_id": match_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Single match fetch failed for {match_id}: {e}")
        if "403" in str(e) or "rate" in str(e).lower():
            raise HTTPException(status_code=429, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scrape/status")
async def get_scrape_status():
    """Get the current status of the background scraper."""
    return SCRAPER_STATE

@router.post("/fetch")
async def trigger_fetch(background_tasks: BackgroundTasks, payload: FetchRequest):
    """Trigger a background fetch operation. Mode 'full' or 'details'."""
    
    if SCRAPER_STATE["is_running"]:
        raise HTTPException(status_code=409, detail="Scraping process is already running.")

    from src.SofaScoreUi import SimpleSofaScoreUI
    import traceback
    
    def update_state(status: str, progress: int, task: str):
        SCRAPER_STATE["status"] = status
        SCRAPER_STATE["progress"] = progress
        SCRAPER_STATE["current_task"] = task
        # Keep log size manageable
        if len(SCRAPER_STATE["log"]) > 50:
             SCRAPER_STATE["log"].pop(0)
        SCRAPER_STATE["log"].append(f"[{status}] {task}")

    def run_update():
        SCRAPER_STATE["is_running"] = True
        SCRAPER_STATE["log"] = [] # Clear log on start
        update_state("Running", 0, f"Starting fetch for {payload.league_id if payload.league_id else 'All Leagues'}")
        
        logger.info(f"Background fetch started. League ID: {payload.league_id if payload.league_id else 'All'}")
        
        try:
            ui = SimpleSofaScoreUI(config_manager=config_manager)
            
            if payload.league_id:
                print(f"--> Updating specific league: {payload.league_id} (Mode: {payload.mode})")
                
                if payload.mode == "full":
                    update_state("Running", 10, f"Fetching seasons for League {payload.league_id}...")
                    ui.season_fetcher.fetch_seasons_for_league(payload.league_id)
                    
                    update_state("Running", 30, f"Fetching matches for League {payload.league_id}...")
                    seasons = ui.season_fetcher.get_seasons_for_league(payload.league_id)
                    total_seasons = len(seasons)
                    for i, season in enumerate(seasons):
                        processing_season_name = season.get('name', season.get('year', 'Unknown'))
                        update_state("Running", 30 + int((i/total_seasons)*30), f"Fetching matches: {processing_season_name}")
                        ui.match_fetcher.fetch_matches_for_season(payload.league_id, season["id"])
                
                update_state("Running", 60, f"Fetching match details for League {payload.league_id}...")
                ui.match_data_fetcher.fetch_all_match_details(league_id=str(payload.league_id), max_seasons=0)
                
            else:
                if payload.mode == "details":
                    update_state("Running", 10, "Fetching details for ALL existing matches...")
                    ui.match_data_fetcher.fetch_all_match_details(max_seasons=0)
                else:
                    update_state("Running", 10, "Updating ALL leagues (Seasons, Matches, Details)...")
                    print("--> Updating ALL leagues...")
                    ui.update_all_leagues()
            
            # Export
            update_state("Running", 90, "Exporting data to CSV...")
            print("--> Exporting to CSV...")
            ui.export_all_to_csv()
            
            update_state("Completed", 100, "Background Task Completed Successfully.")
            print("--> Background Task Completed Successfully.")
            logger.info("Background update and export completed.")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Background update failed: {e}")
            logger.error(traceback.format_exc())
            print(f"--> Background Task FAILED: {e}")
            update_state("Failed", 0, f"Error: {error_msg}")
        finally:
            SCRAPER_STATE["is_running"] = False

    background_tasks.add_task(run_update)
    return {"status": "started", "message": "Update started."}

@router.get("/status")
async def system_status():
    """Get system status info."""
    return {
        "version": "1.0.0", 
        "leagues_count": len(config_manager.get_leagues()),
        "language": config_manager.get_language()
    }

@router.post("/settings")
async def update_settings(settings: SettingsUpdate):
    """Update application settings."""
    try:
        updated = False
        
        if settings.language:
            if config_manager.set_language(settings.language):
                updated = True
        
        if settings.api_base_url is not None:
            if config_manager.update_env_variable("API_BASE_URL", settings.api_base_url):
                updated = True
                
        if settings.use_proxy is not None:
            if config_manager.update_env_variable("USE_PROXY", str(settings.use_proxy).lower()):
                updated = True

        if settings.proxy_url is not None:
            if config_manager.update_env_variable("PROXY_URL", settings.proxy_url):
                updated = True
                
        if settings.data_dir is not None:
             if config_manager.update_env_variable("DATA_DIR", settings.data_dir):
                updated = True
                
        if settings.use_color is not None:
             if config_manager.update_env_variable("USE_COLOR", str(settings.use_color).lower()):
                updated = True
                
        # Force reload to apply changes in memory
        if updated:
            config_manager.reload_config()
            return {"status": "success", "message": "Settings updated successfully."}
        else:
            return {"status": "no_change", "message": "No settings were changed."}
            
    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Stats API
@router.get("/stats/system")
async def get_system_stats():
    """Get detailed system statistics (ported from StatsMenuHandler)."""
    try:
        data_dir = config_manager.get_data_dir()
        if not os.path.isabs(data_dir):
            data_dir = os.path.abspath(data_dir)
            
        logger.info(f"Generating stats from data_dir: {data_dir}")
        leagues = config_manager.get_leagues()
        league_stats: Dict[int, Dict[str, Any]] = {
            league_id: {"name": league_name, "matches": 0, "details": 0}
            for league_id, league_name in leagues.items()
        }

        stats: Dict[str, Any] = {
            "leagues": len(config_manager.get_leagues()),
            "seasons": 0,
            "matches": 0,
            "details": 0,
            "league_breakdown": [],
            "disk_usage": {
                "seasons": 0, "matches": 0, "details": 0, "total": 0, "formatted_total": "0 B"
            }
        }
        
        seasons_dir = os.path.join(data_dir, "seasons")
        if os.path.exists(seasons_dir):
            files = [f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")]
            for f in files:
                try:
                    with open(os.path.join(seasons_dir, f), 'r') as fp:
                        data = json.load(fp)
                        if isinstance(data, dict) and "seasons" in data:
                            stats["seasons"] += len(data["seasons"])
                        elif isinstance(data, list):
                            stats["seasons"] += len(data)
                except Exception:
                    pass

        matches_dir = os.path.join(data_dir, "matches")
        if os.path.exists(matches_dir):
            for root, dirs, files in os.walk(matches_dir):
                parent_dir = os.path.basename(root)
                current_l_id = None
                if "_" in parent_dir:
                    try:
                        current_l_id = int(parent_dir.split("_")[0])
                    except Exception:
                        pass
                
                for file in files:
                    if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                row_count = sum(1 for line in f) - 1
                                if row_count > 0:
                                    stats["matches"] += row_count
                                    if current_l_id in league_stats:
                                        league_stats[current_l_id]["matches"] += row_count
                        except Exception:
                            pass

        match_details_dir = os.path.join(data_dir, "match_details")
        if os.path.exists(match_details_dir):
            league_name_to_id = {}
            for lid, lname in leagues.items():
                league_name_to_id[lname.replace(' ', '_').replace('/', '_').lower()] = lid

            basic_files = glob.glob(os.path.join(match_details_dir, "*", "season_*", "*", "basic.json"))
            for bf in basic_files:
                stats["details"] += 1
                rel = os.path.relpath(bf, match_details_dir)
                lg_folder = rel.split(os.sep)[0]
                current_l_id = None
                try:
                    current_l_id = int(lg_folder.split("_")[0])
                except (ValueError, IndexError):
                    clean = lg_folder.lower()
                    current_l_id = league_name_to_id.get(clean)
                    if current_l_id is None:
                        for key, lid in league_name_to_id.items():
                            if key in clean or clean in key:
                                current_l_id = lid
                                break
                if current_l_id in league_stats:
                    league_stats[current_l_id]["details"] += 1
        
        # Format breakdown
        for l_id, data in league_stats.items():
            if data["matches"] > 0 or data["details"] > 0:
                stats["league_breakdown"].append({
                    "id": l_id,
                    "name": data["name"],
                    "matches": data["matches"],
                    "details": data["details"],
                    "coverage": round((data["details"] / data["matches"] * 100), 1) if data["matches"] > 0 else 0
                })
        
        stats["league_breakdown"].sort(key=lambda x: x["matches"], reverse=True)

        def get_dir_size(path):
            total = 0
            if os.path.exists(path):
                for dirpath, _, filenames in os.walk(path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        try:
                            if os.path.exists(fp):
                                total += os.path.getsize(fp)
                        except Exception:
                            pass
            return total
            
        stats["disk_usage"]["seasons"] = get_dir_size(seasons_dir)
        stats["disk_usage"]["matches"] = get_dir_size(matches_dir)
        stats["disk_usage"]["details"] = get_dir_size(match_details_dir)
        stats["disk_usage"]["total"] = sum([stats["disk_usage"][k] for k in ["seasons", "matches", "details"]])
        
        size = stats["disk_usage"]["total"]
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                stats["disk_usage"]["formatted_total"] = f"{size:.2f} {unit}"
                break
            size /= 1024

        if "formatted_total" not in stats["disk_usage"]:
            stats["disk_usage"]["formatted_total"] = f"{size:.2f} TB"
            
        return stats
        
    except Exception as e:
        logger.error(f"Error generating stats: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to generate statistics")
