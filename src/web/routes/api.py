from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel

from src.config_manager import ConfigManager
from src.logger import get_logger

router = APIRouter(prefix="/api", tags=["api"])
logger = get_logger("WebAPI")
config_manager = ConfigManager()


class _SyncHttpError(Exception):
    """Senkron worker içinde fırlatılır; async route HTTPException'a çevrilir."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _is_empty_schedule_val(v: Any) -> bool:
    if v is None or v == "":
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return False


def _filter_matches_df_by_league(df: pd.DataFrame, league_id: Optional[str]) -> pd.DataFrame:
    if df.empty or not league_id:
        return df
    try:
        lid = int(league_id)
    except (TypeError, ValueError):
        return df
    if "league_id" in df.columns:
        try:
            return df[df["league_id"].astype(int) == lid]
        except (ValueError, TypeError):
            return df[df["league_id"] == lid]
    if "league_folder" in df.columns:
        col = df["league_folder"].astype(str)
        return df[col.str.startswith(f"{lid}_")]
    if "tournament_id" in df.columns:
        try:
            return df[df["tournament_id"].astype(int) == lid]
        except (ValueError, TypeError):
            return df[df["tournament_id"] == lid]
    return df


def _normalize_schedule_match_row(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Schedule UI expects summary CSV fields; processed all_matches uses *_name/*_ft and Unix seconds for match_date."""
    r: Dict[str, Any] = {}
    for k, v in rec.items():
        r[k] = "" if (isinstance(v, float) and pd.isna(v)) else v
    if not r.get("home_team") and not _is_empty_schedule_val(r.get("home_team_name")):
        r["home_team"] = r.get("home_team_name", "")
    if not r.get("away_team") and not _is_empty_schedule_val(r.get("away_team_name")):
        r["away_team"] = r.get("away_team_name", "")
    if _is_empty_schedule_val(r.get("home_score")) and not _is_empty_schedule_val(r.get("home_score_ft")):
        r["home_score"] = r.get("home_score_ft")
    if _is_empty_schedule_val(r.get("away_score")) and not _is_empty_schedule_val(r.get("away_score_ft")):
        r["away_score"] = r.get("away_score_ft")
    if not r.get("tournament") and not _is_empty_schedule_val(r.get("tournament_name")):
        r["tournament"] = r.get("tournament_name", "")
    md = r.get("match_date")
    if not _is_empty_schedule_val(md):
        ts: Optional[float] = None
        if isinstance(md, (int, float)):
            ts = float(md)
        elif isinstance(md, str):
            s = md.strip()
            if s.isdigit():
                ts = float(s)
            else:
                try:
                    f = float(s)
                    if f == int(f) and all(c.isdigit() or c == "." for c in s):
                        ts = f
                except ValueError:
                    ts = None
        if ts is not None and ts > 0:
            if ts > 1e12:
                ts = ts / 1000.0
            try:
                r["match_date"] = datetime.fromtimestamp(ts).isoformat(sep=" ", timespec="seconds")
            except (OSError, ValueError, OverflowError):
                pass
    return r


def _find_league_seasons_json(data_dir: str, league_id: int) -> Optional[str]:
    """SeasonFetcher kaydı: {id}_{safe_name}_seasons.json; eski format: {id}_seasons.json."""
    seasons_dir = os.path.join(data_dir, "seasons")
    if not os.path.isdir(seasons_dir):
        return None
    legacy = os.path.join(seasons_dir, f"{league_id}_seasons.json")
    if os.path.isfile(legacy):
        return legacy
    pattern = os.path.join(seasons_dir, f"{league_id}_*_seasons.json")
    matches = glob.glob(pattern)
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


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
    max_concurrent: Optional[int] = None
    wait_time_min: Optional[float] = None
    wait_time_max: Optional[float] = None
    request_timeout: Optional[int] = None
    max_retries: Optional[int] = None
    rate_limit_threshold_consecutive: Optional[int] = None
    rate_limit_threshold_ratio: Optional[float] = None
    server_error_threshold_consecutive: Optional[int] = None
    fetch_only_finished: Optional[bool] = None
    save_empty_rounds: Optional[bool] = None
    log_level: Optional[str] = None
    debug: Optional[bool] = None


class FetchSelection(BaseModel):
    league_id: int
    season_ids: Optional[List[int]] = None
    match_ids: Optional[List[int]] = None


class FetchRequest(BaseModel):
    league_id: Optional[int] = None
    mode: str = "full"
    selections: Optional[List[FetchSelection]] = None


class MatchListResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int
    sort: str


def _schedule_match_date_sort_key(val: Any) -> float:
    """Tarih sıralaması için ms cinsinden anahtar (büyük = daha yeni)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, (int, float)):
        v = float(val)
        if pd.isna(v):
            return 0.0
        if 0 < v < 1e12:
            return v * 1000.0
        return v
    s = str(val).strip()
    if s.isdigit():
        v = float(s)
        return v * 1000.0 if v < 1e12 else v
    try:
        dt = pd.to_datetime(s, errors="coerce")
        if pd.notna(dt):
            return float(dt.value) / 1e6
    except Exception:
        pass
    return 0.0


def _build_schedule_matches_dataframe(
    data_dir: str,
    league_id: Optional[str],
    date: Optional[str],
    season_id: Optional[int],
) -> pd.DataFrame:
    csv_dir = os.path.join(data_dir, "match_details", "processed")
    all_files = glob.glob(os.path.join(csv_dir, "all_matches_*.csv"))
    if all_files:
        latest_file = max(all_files, key=os.path.getctime)
        try:
            df = pd.read_csv(latest_file)
            df = _filter_matches_df_by_league(df, league_id)
            if date and "match_date" in df.columns:
                df = df[df["match_date"].astype(str).str.contains(date, na=False)]
            if season_id is not None:
                sid = int(season_id)
                if "season_id" in df.columns:
                    df = df[pd.to_numeric(df["season_id"], errors="coerce") == sid]
                elif "season_folder" in df.columns:
                    sf = df["season_folder"].astype(str)
                    df = df[sf.str.startswith(f"{sid}_")]
                else:
                    return pd.DataFrame()
            return df
        except Exception as e:
            logger.error(f"Error reading export CSV: {e}")

    summary_pattern = os.path.join(data_dir, "matches", "**", "*_summary.csv")
    summary_files = glob.glob(summary_pattern, recursive=True)
    all_summary_data: List[pd.DataFrame] = []
    for file in summary_files:
        try:
            parent_dir = os.path.basename(os.path.dirname(file))
            if league_id and not parent_dir.startswith(f"{league_id}_"):
                continue
            fname = os.path.basename(file)
            m = re.match(r"^(\d+)_", fname)
            file_sid = int(m.group(1)) if m else None
            if season_id is not None and file_sid is not None and file_sid != int(season_id):
                continue
            df_part = pd.read_csv(file)
            if df_part.empty:
                continue
            df_part["_file_season_id"] = file_sid
            all_summary_data.append(df_part)
        except Exception as e:
            logger.error(f"Error reading summary CSV {file}: {e}")

    if not all_summary_data:
        return pd.DataFrame()
    df_total = pd.concat(all_summary_data, ignore_index=True)
    if date and "match_date" in df_total.columns:
        df_total = df_total[df_total["match_date"].astype(str).str.contains(date, na=False)]
    if season_id is not None and "_file_season_id" in df_total.columns:
        df_total = df_total[df_total["_file_season_id"] == int(season_id)]
    return df_total


def _get_matches_sync(
    data_dir: str,
    limit: int,
    offset: int,
    sort: str,
    league_id: Optional[str],
    date: Optional[str],
    season_id: Optional[int],
) -> MatchListResponse:
    df = _build_schedule_matches_dataframe(data_dir, league_id, date, season_id)
    if df.empty:
        return MatchListResponse(items=[], total=0, limit=limit, offset=offset, sort=sort)

    df = df.copy()
    if "match_date" in df.columns:
        df["_sort_ts"] = df["match_date"].map(_schedule_match_date_sort_key)
    else:
        df["_sort_ts"] = 0.0
    ascending = sort == "asc"
    df = df.sort_values(by="_sort_ts", ascending=ascending, kind="mergesort")
    total = int(len(df))
    df_page = df.iloc[offset : offset + limit]
    drop_cols = [c for c in ("_sort_ts", "_file_season_id") if c in df_page.columns]
    if drop_cols:
        df_page = df_page.drop(columns=drop_cols)
    raw = df_page.to_dict(orient="records")
    items = [_normalize_schedule_match_row(row) for row in raw]
    return MatchListResponse(items=items, total=total, limit=limit, offset=offset, sort=sort)


def _search_remote_leagues_sync(q: str) -> List[RemoteLeagueResult]:
    from src.utils import make_api_request

    url = f"https://www.sofascore.com/api/v1/search/unique-tournaments/{q}"
    try:
        data = make_api_request(url)
    except Exception as e:
        logger.error(f"Remote league search failed: {e}")
        return []

    if not data:
        return []

    leagues = []
    results = data.get("uniqueTournaments", data.get("results", []))
    for item in results:
        entity = item.get("entity", item) if isinstance(item, dict) else item
        if not isinstance(entity, dict):
            continue
        lid = entity.get("id")
        name = entity.get("name")
        if lid and name:
            category = entity.get("category", {})
            leagues.append(
                RemoteLeagueResult(
                    id=lid,
                    name=name,
                    country=category.get("name", "Unknown") if isinstance(category, dict) else "Unknown",
                    slug=entity.get("slug"),
                )
            )
    return leagues[:20]


def _refresh_league_seasons_sync(league_id: int) -> dict:
    from src.SofaScoreUi import SimpleSofaScoreUI

    ui = SimpleSofaScoreUI(config_manager=config_manager)
    ui.season_fetcher.fetch_seasons_for_league(league_id)
    data_dir = config_manager.get_data_dir()
    seasons_file = _find_league_seasons_json(data_dir, league_id)
    if seasons_file:
        with open(seasons_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        seasons = data.get("seasons", data) if isinstance(data, dict) else data
        return {"status": "success", "seasons": seasons if isinstance(seasons, list) else []}
    return {"status": "success", "seasons": []}


def _get_season_matches_sync(season_id: int, league_id: int, data_dir: str) -> dict:
    matches_dir = os.path.join(data_dir, "matches")
    matches = []
    if not os.path.exists(matches_dir):
        return {"matches": []}

    pattern = os.path.join(matches_dir, f"{league_id}_*", f"{season_id}_*_summary.csv")
    summary_files = glob.glob(pattern)
    if not summary_files:
        pattern = os.path.join(matches_dir, f"{league_id}_*", f"*{season_id}*.csv")
        summary_files = glob.glob(pattern)

    for f in summary_files:
        try:
            df = pd.read_csv(f)
            matches.extend(df.fillna("").to_dict(orient="records"))
        except Exception as e:
            logger.error(f"Error reading match file {f}: {e}")

    return {"matches": matches}


def _get_missing_details_sync(league_id: int, season_id: Optional[int], data_dir: str) -> dict:
    matches_dir = os.path.join(data_dir, "matches")
    match_details_dir = os.path.join(data_dir, "match_details")

    all_match_ids = set()
    match_info = {}

    if os.path.exists(matches_dir):
        pattern = os.path.join(matches_dir, f"{league_id}_*", "*.csv")
        for csv_file in glob.glob(pattern):
            try:
                df = pd.read_csv(csv_file)
                if "match_id" in df.columns:
                    for _, row in df.iterrows():
                        mid = row.get("match_id")
                        if pd.notna(mid):
                            mid = int(mid)
                            all_match_ids.add(mid)
                            match_info[mid] = {
                                "match_id": mid,
                                "home": row.get("home_team", ""),
                                "away": row.get("away_team", ""),
                                "match_date": str(row.get("match_date", "")),
                                "season_name": str(row.get("season_name", row.get("season", ""))),
                            }
            except Exception as e:
                logger.error(f"Error reading CSV for missing details: {e}")

    fetched_ids = set()
    if os.path.exists(match_details_dir):
        basic_files = glob.glob(os.path.join(match_details_dir, "**", "basic.json"), recursive=True)
        for bf in basic_files:
            parent = os.path.basename(os.path.dirname(bf))
            try:
                fetched_ids.add(int(parent))
            except ValueError:
                pass

    missing_ids = all_match_ids - fetched_ids
    missing = [match_info[mid] for mid in sorted(missing_ids) if mid in match_info][:500]

    return {
        "total_matches": len(all_match_ids),
        "missing_count": len(missing_ids),
        "missing": missing,
    }


def _get_match_details_sync(match_id: str) -> Dict[str, Any]:
    from src.match_data_fetcher import MatchDataFetcher, REQUIRED_FILES

    fetcher = MatchDataFetcher(config_manager=config_manager, data_dir=config_manager.get_data_dir())
    match_path_info = fetcher._find_match_path(match_id)
    if not match_path_info:
        raise _SyncHttpError(404, "Match details not found.")

    _, _, match_path = match_path_info
    if not os.path.exists(os.path.join(match_path, "basic.json")):
        raise _SyncHttpError(404, "Match details not found.")

    result: Dict[str, Any] = {}
    try:
        full_json_path = os.path.join(match_path, f"{match_id}.json")
        if os.path.exists(full_json_path):
            with open(full_json_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        else:
            for fname in REQUIRED_FILES:
                if not fname.endswith(".json"):
                    fname = f"{fname}.json"
                component = fname[:-5]
                c_path = os.path.join(match_path, fname)
                if os.path.exists(c_path):
                    with open(c_path, "r", encoding="utf-8") as f:
                        result[component] = json.load(f)

    except Exception as e:
        logger.error(f"Error loading match {match_id}: {e}")
        raise _SyncHttpError(500, "Error parsing match data.")

    return result


def _fetch_single_match_sync(match_id: str) -> dict:
    from src.match_data_fetcher import MatchDataFetcher

    fetcher = MatchDataFetcher(config_manager=config_manager, data_dir=config_manager.get_data_dir())
    try:
        result = None
        if fetcher._find_match_path(match_id):
            result = fetcher.refill_missing_match_slices(match_id)
        if result is None:
            result = fetcher.fetch_match_data(match_id)
        if result is None:
            raise _SyncHttpError(
                404,
                "Match data could not be fetched (may be unfinished or unavailable).",
            )
        return {"status": "success", "match_id": match_id}
    except _SyncHttpError:
        raise
    except Exception as e:
        logger.error(f"Single match fetch failed for {match_id}: {e}")
        if "403" in str(e) or "rate" in str(e).lower():
            raise _SyncHttpError(429, str(e))
        raise _SyncHttpError(500, str(e))


def _build_dashboard_sync(data_dir: str, leagues: Dict[int, str]) -> dict:
    seasons_dir = os.path.join(data_dir, "seasons")
    matches_dir = os.path.join(data_dir, "matches")
    details_dir = os.path.join(data_dir, "match_details")

    league_cards = []
    for lid, lname in leagues.items():
        card = {"id": lid, "name": lname, "seasons": 0, "matches": 0, "details": 0, "coverage": 0, "last_update": None}
        sf = os.path.join(seasons_dir, f"{lid}_seasons.json")
        if os.path.exists(sf):
            try:
                with open(sf, "r") as f:
                    d = json.load(f)
                s = d.get("seasons", d) if isinstance(d, dict) else d
                card["seasons"] = len(s) if isinstance(s, list) else 0
            except Exception:
                pass

        match_pattern = os.path.join(matches_dir, f"{lid}_*", "*.csv")
        for mf in glob.glob(match_pattern):
            try:
                with open(mf, "r") as f:
                    rows = sum(1 for _ in f) - 1
                if rows > 0:
                    card["matches"] += rows
            except Exception:
                pass

        detail_files = glob.glob(os.path.join(details_dir, f"{lid}_*", "season_*", "*", "basic.json"))
        if not detail_files:
            safe_name = lname.replace(" ", "_").replace("/", "_")
            detail_files = glob.glob(os.path.join(details_dir, safe_name, "season_*", "*", "basic.json"))
        card["details"] = len(detail_files)
        card["coverage"] = round(card["details"] / card["matches"] * 100, 1) if card["matches"] > 0 else 0

        if detail_files:
            latest_mtime = max(os.path.getmtime(f) for f in detail_files)
            import datetime as _dt

            card["last_update"] = _dt.datetime.fromtimestamp(latest_mtime).isoformat()

        league_cards.append(card)

    def get_dir_size(path: str) -> int:
        total = 0
        if os.path.exists(path):
            for dp, _, fns in os.walk(path):
                for fn in fns:
                    try:
                        total += os.path.getsize(os.path.join(dp, fn))
                    except Exception:
                        pass
        return total

    s_size = get_dir_size(seasons_dir)
    m_size = get_dir_size(matches_dir)
    d_size = get_dir_size(details_dir)
    total = s_size + m_size + d_size

    size_fmt = total
    formatted = "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_fmt < 1024:
            formatted = f"{size_fmt:.1f} {unit}"
            break
        size_fmt /= 1024
    else:
        formatted = f"{size_fmt:.1f} TB"

    return {
        "leagues": league_cards,
        "disk_usage": {
            "seasons": s_size,
            "matches": m_size,
            "details": d_size,
            "total": total,
            "formatted_total": formatted,
        },
        "totals": {
            "leagues": len(leagues),
            "matches": sum(c["matches"] for c in league_cards),
            "details": sum(c["details"] for c in league_cards),
        },
    }


def _compute_system_stats_sync(data_dir: str, leagues: Dict[int, str]) -> Dict[str, Any]:
    logger.info(f"Generating stats from data_dir: {data_dir}")
    league_stats: Dict[int, Dict[str, Any]] = {
        league_id: {"name": league_name, "matches": 0, "details": 0}
        for league_id, league_name in leagues.items()
    }

    stats: Dict[str, Any] = {
        "leagues": len(leagues),
        "seasons": 0,
        "matches": 0,
        "details": 0,
        "league_breakdown": [],
        "disk_usage": {
            "seasons": 0,
            "matches": 0,
            "details": 0,
            "total": 0,
            "formatted_total": "0 B",
        },
    }

    seasons_dir = os.path.join(data_dir, "seasons")
    if os.path.exists(seasons_dir):
        files = [f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")]
        for f in files:
            try:
                with open(os.path.join(seasons_dir, f), "r") as fp:
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
                        with open(os.path.join(root, file), "r", encoding="utf-8") as f:
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
            league_name_to_id[lname.replace(" ", "_").replace("/", "_").lower()] = lid

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

    for l_id, data in league_stats.items():
        if data["matches"] > 0 or data["details"] > 0:
            stats["league_breakdown"].append(
                {
                    "id": l_id,
                    "name": data["name"],
                    "matches": data["matches"],
                    "details": data["details"],
                    "coverage": round((data["details"] / data["matches"] * 100), 1)
                    if data["matches"] > 0
                    else 0,
                }
            )

    stats["league_breakdown"].sort(key=lambda x: x["matches"], reverse=True)

    def get_dir_size(path: str) -> int:
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
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            stats["disk_usage"]["formatted_total"] = f"{size:.2f} {unit}"
            break
        size /= 1024

    if "formatted_total" not in stats["disk_usage"]:
        stats["disk_usage"]["formatted_total"] = f"{size:.2f} TB"

    return stats


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


class RemoteLeagueResult(BaseModel):
    id: int
    name: str
    country: str
    slug: Optional[str] = None


@router.get("/leagues/search-remote", response_model=List[RemoteLeagueResult])
async def search_remote_leagues(q: str = Query(..., min_length=2)):
    """SofaScore'dan lig ara. Kullanıcının yeni eklemek istediği ligleri bulması için."""
    return await asyncio.to_thread(_search_remote_leagues_sync, q)


@router.get("/leagues/{league_id}/seasons")
async def get_league_seasons(league_id: int):
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
    """Bir ligin sezon listesini SofaScore'dan yeniler."""
    try:
        return await asyncio.to_thread(_refresh_league_seasons_sync, league_id)
    except Exception as e:
        logger.error(f"Failed to refresh seasons for league {league_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
) -> MatchListResponse:
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
    )

# Global Scraper State
SCRAPER_STATE = {
    "is_running": False,
    "status": "Idle",
    "progress": 0,
    "current_task": "",
    "current_batch": "",
    "matches_total": 0,
    "matches_done": 0,
    "matches_failed": 0,
    "circuit_breaker_triggered": False,
    "circuit_breaker_reason": None,
    "eta_seconds": None,
    "started_at": None,
    "cancel_requested": False,
    "log": []
}

@router.get("/matches/{match_id}")
async def get_match_details(match_id: str):
    """Retrieve detailed JSON data for a specific match."""
    try:
        return await asyncio.to_thread(_get_match_details_sync, match_id)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

@router.post("/matches/{match_id}/fetch")
async def fetch_single_match(match_id: str):
    """Tek bir maç için detayları senkron olarak çeker."""
    try:
        return await asyncio.to_thread(_fetch_single_match_sync, match_id)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/scrape/status")
async def get_scrape_status():
    """Get the current status of the background scraper."""
    return SCRAPER_STATE


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
            current_json = json.dumps(SCRAPER_STATE, default=str)
            if current_json != last_state_json:
                yield {"event": "update", "data": current_json}
                last_state_json = current_json
                if not SCRAPER_STATE["is_running"] and SCRAPER_STATE["status"] != "Running":
                    yield {"event": "done", "data": current_json}
                    break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())

@router.post("/scrape/cancel")
async def cancel_scrape():
    """Çalışan background fetch işlemini iptal eder."""
    if not SCRAPER_STATE["is_running"]:
        raise HTTPException(status_code=400, detail="No scraping process is running.")
    SCRAPER_STATE["cancel_requested"] = True
    SCRAPER_STATE["current_task"] = "Cancellation requested..."
    return {"status": "cancelling", "message": "Cancel signal sent."}


@router.post("/fetch")
async def trigger_fetch(background_tasks: BackgroundTasks, payload: FetchRequest):
    """Trigger a background fetch operation. Mode 'full' or 'details'."""
    
    if SCRAPER_STATE["is_running"]:
        raise HTTPException(status_code=409, detail="Scraping process is already running.")

    from src.SofaScoreUi import SimpleSofaScoreUI
    import traceback
    
    import datetime as _dt

    def update_state(status: str, progress: int, task: str):
        SCRAPER_STATE["status"] = status
        SCRAPER_STATE["progress"] = progress
        SCRAPER_STATE["current_task"] = task
        if len(SCRAPER_STATE["log"]) > 50:
             SCRAPER_STATE["log"].pop(0)
        SCRAPER_STATE["log"].append(f"[{status}] {task}")

    def run_update():
        SCRAPER_STATE["is_running"] = True
        SCRAPER_STATE["cancel_requested"] = False
        SCRAPER_STATE["log"] = []
        SCRAPER_STATE["matches_done"] = 0
        SCRAPER_STATE["matches_failed"] = 0
        SCRAPER_STATE["matches_total"] = 0
        SCRAPER_STATE["circuit_breaker_triggered"] = False
        SCRAPER_STATE["circuit_breaker_reason"] = None
        SCRAPER_STATE["eta_seconds"] = None
        SCRAPER_STATE["started_at"] = _dt.datetime.now().isoformat()

        def web_detail_progress(lo: int, hi: int):
            """Terminal tqdm ile uyumlu ara adımlar: done/total → [lo, hi]."""

            def cb(done: int, total: int, task: str) -> None:
                hi_clamped = max(lo, min(99, hi))
                if total <= 0:
                    pct = hi_clamped
                else:
                    pct = lo + int((done / total) * (hi_clamped - lo))
                pct = max(lo, min(hi_clamped, pct))
                SCRAPER_STATE["matches_done"] = done
                SCRAPER_STATE["matches_total"] = total
                update_state("Running", pct, task)

            return cb

        summary = (
            f"{len(payload.selections)} targeted selection(s)"
            if payload.selections
            else (str(payload.league_id) if payload.league_id else "All Leagues")
        )
        update_state("Running", 0, f"Starting fetch for {summary}")
        logger.info(f"Background fetch started. Target: {summary}")
        
        try:
            ui = SimpleSofaScoreUI(config_manager=config_manager)
            
            if payload.selections:
                if payload.mode == "details":
                    mid_set: Set[int] = set()
                    for s in payload.selections:
                        if s.match_ids:
                            mid_set.update(s.match_ids)
                    all_mids = list(mid_set)
                    update_state(
                        "Running",
                        10,
                        f"Fetching details for {len(all_mids)} selected matches...",
                    )
                    if all_mids:
                        ui.match_data_fetcher.fetch_matches_batch(
                            all_mids,
                            progress_callback=web_detail_progress(10, 85),
                        )
                else:
                    logger.info(
                        "Wizard/targeted fetch: %s row(s)",
                        len(payload.selections),
                    )
                    unique_leagues = sorted({s.league_id for s in payload.selections})
                    for league_id in unique_leagues:
                        if SCRAPER_STATE.get("cancel_requested"):
                            break
                        update_state(
                            "Running",
                            5,
                            f"Refreshing season list for league {league_id}...",
                        )
                        ui.season_fetcher.fetch_seasons_for_league(league_id)
                    
                    pairs: List[Tuple[int, int]] = []
                    seen_pairs: Set[Tuple[int, int]] = set()
                    for s in payload.selections:
                        for sid in s.season_ids or []:
                            key = (s.league_id, sid)
                            if key not in seen_pairs:
                                seen_pairs.add(key)
                                pairs.append(key)
                    
                    total_ops = len(pairs) or 1
                    for idx, (lid, sid) in enumerate(pairs):
                        if SCRAPER_STATE.get("cancel_requested"):
                            break
                        pct = 10 + int((idx / total_ops) * 45)
                        update_state(
                            "Running",
                            pct,
                            f"Fetching matches: league {lid}, season {sid}",
                        )
                        ui.match_fetcher.fetch_matches_for_season(lid, sid)
                    
                    league_to_seasons: Dict[int, List[int]] = {}
                    for s in payload.selections:
                        league_to_seasons.setdefault(s.league_id, [])
                        for sid in s.season_ids or []:
                            if sid not in league_to_seasons[s.league_id]:
                                league_to_seasons[s.league_id].append(sid)
                    
                    n_leagues = len(league_to_seasons) or 1
                    detail_span = 30
                    for i, (lid, only_sids) in enumerate(league_to_seasons.items()):
                        if SCRAPER_STATE.get("cancel_requested"):
                            break
                        if not only_sids:
                            continue
                        lo = 55 + int((i / n_leagues) * detail_span)
                        hi = 55 + int(((i + 1) / n_leagues) * detail_span)
                        if hi <= lo:
                            hi = lo + 1
                        update_state(
                            "Running",
                            lo,
                            f"Fetching match details: league {lid} ({len(only_sids)} season(s))…",
                        )
                        ui.match_data_fetcher.fetch_all_match_details(
                            league_id=str(lid),
                            max_seasons=0,
                            only_season_ids=only_sids,
                            progress_callback=web_detail_progress(lo, hi),
                        )
            
            elif payload.league_id:
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
                
                update_state("Running", 60, f"Fetching match details for League {payload.league_id}…")
                ui.match_data_fetcher.fetch_all_match_details(
                    league_id=str(payload.league_id),
                    max_seasons=0,
                    progress_callback=web_detail_progress(60, 89),
                )
                
            else:
                if payload.mode == "details":
                    update_state("Running", 10, "Fetching details for ALL existing matches…")
                    ui.match_data_fetcher.fetch_all_match_details(
                        max_seasons=0,
                        progress_callback=web_detail_progress(10, 89),
                    )
                else:
                    update_state("Running", 10, "Updating ALL leagues (Seasons, Matches, Details)…")
                    print("--> Updating ALL leagues...")
                    ui.update_all_leagues(progress_factory=web_detail_progress)
            
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


@router.get("/dashboard")
async def get_dashboard():
    """Dashboard overview with league cards, disk usage, and quick stats."""
    data_dir = config_manager.get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)
    leagues = dict(config_manager.get_leagues())
    return await asyncio.to_thread(_build_dashboard_sync, data_dir, leagues)


@router.get("/settings")
async def get_all_settings():
    """Get all current settings."""
    return {
        "language": config_manager.get_language(),
        "api_base_url": config_manager.get_api_base_url(),
        "use_proxy": config_manager.get_use_proxy(),
        "proxy_url": config_manager.get_proxy_url(),
        "data_dir": config_manager.get_data_dir(),
        "use_color": config_manager.get_use_color(),
        "date_format": config_manager.get_date_format(),
        "max_concurrent": config_manager.get_max_concurrent(),
        "wait_time_min": config_manager.get_wait_time_min(),
        "wait_time_max": config_manager.get_wait_time_max(),
        "request_timeout": config_manager.get_request_timeout(),
        "max_retries": config_manager.get_max_retries(),
        "rate_limit_threshold_consecutive": config_manager.get_rate_limit_threshold_consecutive(),
        "rate_limit_threshold_ratio": config_manager.get_rate_limit_threshold_ratio(),
        "server_error_threshold_consecutive": config_manager.get_server_error_threshold_consecutive(),
        "fetch_only_finished": os.getenv("FETCH_ONLY_FINISHED", "true").lower() == "true",
        "save_empty_rounds": os.getenv("SAVE_EMPTY_ROUNDS", "false").lower() == "true",
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "debug": os.getenv("DEBUG", "false").lower() == "true",
    }


@router.post("/settings")
async def update_settings(settings: SettingsUpdate):
    """Update application settings."""
    try:
        updated = False
        env_map = {
            "language": ("LANGUAGE", lambda v: v),
            "api_base_url": ("API_BASE_URL", lambda v: v),
            "use_proxy": ("USE_PROXY", lambda v: str(v).lower()),
            "proxy_url": ("PROXY_URL", lambda v: v),
            "data_dir": ("DATA_DIR", lambda v: v),
            "use_color": ("USE_COLOR", lambda v: str(v).lower()),
            "date_format": ("DATE_FORMAT", lambda v: v),
            "max_concurrent": ("MAX_CONCURRENT", lambda v: str(v)),
            "wait_time_min": ("WAIT_TIME_MIN", lambda v: str(v)),
            "wait_time_max": ("WAIT_TIME_MAX", lambda v: str(v)),
            "request_timeout": ("REQUEST_TIMEOUT", lambda v: str(v)),
            "max_retries": ("MAX_RETRIES", lambda v: str(v)),
            "rate_limit_threshold_consecutive": ("RATE_LIMIT_THRESHOLD_CONSECUTIVE", lambda v: str(v)),
            "rate_limit_threshold_ratio": ("RATE_LIMIT_THRESHOLD_RATIO", lambda v: str(v)),
            "server_error_threshold_consecutive": ("SERVER_ERROR_THRESHOLD_CONSECUTIVE", lambda v: str(v)),
            "fetch_only_finished": ("FETCH_ONLY_FINISHED", lambda v: str(v).lower()),
            "save_empty_rounds": ("SAVE_EMPTY_ROUNDS", lambda v: str(v).lower()),
            "log_level": ("LOG_LEVEL", lambda v: v),
            "debug": ("DEBUG", lambda v: str(v).lower()),
        }

        settings_dict = settings.model_dump(exclude_none=True)
        for field, value in settings_dict.items():
            if field in env_map:
                env_key, converter = env_map[field]
                if config_manager.update_env_variable(env_key, converter(value)):
                    updated = True

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

        leagues = dict(config_manager.get_leagues())
        return await asyncio.to_thread(_compute_system_stats_sync, data_dir, leagues)

    except Exception as e:
        logger.error(f"Error generating stats: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to generate statistics")


# --- Data Management Endpoints (W10) ---

def _create_backup_sync(scope: str) -> dict:
    import zipfile
    import datetime as _dt

    data_dir = config_manager.get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{scope}_{timestamp}.zip"

    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "backups")
    os.makedirs(static_dir, exist_ok=True)
    zip_path = os.path.join(static_dir, backup_filename)

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            dirs_to_backup = []
            if scope in ("all", "config"):
                config_path = config_manager.league_config_path
                if os.path.exists(config_path):
                    zf.write(config_path, os.path.basename(config_path))
                if os.path.exists(".env"):
                    zf.write(".env", ".env")
            if scope in ("all", "seasons"):
                dirs_to_backup.append(os.path.join(data_dir, "seasons"))
            if scope in ("all", "matches"):
                dirs_to_backup.append(os.path.join(data_dir, "matches"))
            if scope in ("all", "match_details"):
                dirs_to_backup.append(os.path.join(data_dir, "match_details"))

            for dir_path in dirs_to_backup:
                if os.path.exists(dir_path):
                    for root, _, files in os.walk(dir_path):
                        for f in files:
                            fp = os.path.join(root, f)
                            arcname = os.path.relpath(fp, os.path.dirname(data_dir))
                            zf.write(fp, arcname)

        return {"download_url": f"/static/backups/{backup_filename}", "filename": backup_filename}
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        raise _SyncHttpError(500, str(e)) from e


def _clear_data_sync(scope: str) -> dict:
    import shutil

    data_dir = config_manager.get_data_dir()
    if not os.path.isabs(data_dir):
        data_dir = os.path.abspath(data_dir)

    cleared = []
    try:
        if scope in ("all", "match_details"):
            path = os.path.join(data_dir, "match_details")
            if os.path.exists(path):
                shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
                cleared.append("match_details")
        if scope in ("all", "matches"):
            path = os.path.join(data_dir, "matches")
            if os.path.exists(path):
                shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
                cleared.append("matches")
        if scope in ("all", "seasons"):
            path = os.path.join(data_dir, "seasons")
            if os.path.exists(path):
                shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
                cleared.append("seasons")
        return {"status": "success", "cleared": cleared}
    except Exception as e:
        logger.error(f"Clear data failed: {e}")
        raise _SyncHttpError(500, str(e)) from e


def _export_csv_sync(league_id: Optional[int], data_dir: str):
    from fastapi.responses import FileResponse

    csv_dir = os.path.join(data_dir, "match_details", "processed")

    if league_id:
        pattern = os.path.join(csv_dir, f"*league_{league_id}*.csv")
        files = glob.glob(pattern)
        if not files:
            pattern = os.path.join(csv_dir, "all_matches_*.csv")
            files = glob.glob(pattern)
    else:
        files = glob.glob(os.path.join(csv_dir, "all_matches_*.csv"))

    if not files:
        from src.SofaScoreUi import SimpleSofaScoreUI

        try:
            ui = SimpleSofaScoreUI(config_manager=config_manager)
            ui.export_all_to_csv()
            files = glob.glob(os.path.join(csv_dir, "all_matches_*.csv"))
        except Exception as e:
            logger.error(f"CSV export failed: {e}")
            raise _SyncHttpError(500, "CSV generation failed") from e

    if not files:
        raise _SyncHttpError(404, "No CSV data available. Run a fetch first.")

    latest = max(files, key=os.path.getctime)
    filename = os.path.basename(latest)
    return FileResponse(latest, filename=filename, media_type="text/csv")


@router.post("/data/backup")
async def create_backup(scope: str = "all"):
    """Veri yedeği oluşturur ve indirilebilir zip dosyası döndürür."""
    try:
        return await asyncio.to_thread(_create_backup_sync, scope)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


class ClearRequest(BaseModel):
    scope: str = "all"


@router.post("/data/clear")
async def clear_data(req: ClearRequest):
    """Veriyi temizler."""
    try:
        return await asyncio.to_thread(_clear_data_sync, req.scope)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


# --- CSV Export Endpoint (W11) ---


@router.get("/export/csv")
async def export_csv(league_id: Optional[int] = None):
    """CSV export. Mevcut processed CSV'yi döndürür veya yeni oluşturur."""
    data_dir = config_manager.get_data_dir()
    try:
        return await asyncio.to_thread(_export_csv_sync, league_id, data_dir)
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
