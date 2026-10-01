"""Maç listesi, maç detayı ve eksik detay uç noktaları."""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src import breaker as request_breaker
from src import bridge_health
from src.web import upstream
from src.web.routes.common import (
    _SyncHttpError,
    _job_store,
    config_manager,
    logger,
)

if TYPE_CHECKING:
    from src.slices import Outcome

router = APIRouter(prefix="/api", tags=["api"])


def _is_empty_schedule_val(v: Any) -> bool:
    if v is None or v == "":
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return False


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


def _filter_matches_df_by_league(df: pd.DataFrame, league_id: Optional[str]) -> pd.DataFrame:
    ids = _parse_league_ids(league_id)
    if df.empty or ids is None:
        return df
    if "league_id" in df.columns:
        return df[pd.to_numeric(df["league_id"], errors="coerce").isin(ids)]
    if "league_folder" in df.columns:
        prefix = df["league_folder"].astype(str).str.split("_").str[0]
        return df[pd.to_numeric(prefix, errors="coerce").isin(ids)]
    if "tournament_id" in df.columns:
        return df[pd.to_numeric(df["tournament_id"], errors="coerce").isin(ids)]
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


def _read_processed_matches(
    data_dir: str,
    league_id: Optional[str],
    date: Optional[str],
    season_id: Optional[int],
) -> pd.DataFrame:
    """The export CSV (match_details/processed/all_matches_*.csv), filtered like the summaries."""
    csv_dir = os.path.join(data_dir, "match_details", "processed")
    all_files = glob.glob(os.path.join(csv_dir, "all_matches_*.csv"))
    if not all_files:
        return pd.DataFrame()
    latest_file = max(all_files, key=os.path.getctime)
    try:
        df = pd.read_csv(latest_file)
    except Exception as e:
        logger.error(f"Error reading export CSV: {e}")
        return pd.DataFrame()
    df = _filter_matches_df_by_league(df, league_id)
    if date and "match_date" in df.columns:
        df = df[df["match_date"].astype(str).str.contains(date, na=False)]
    if season_id is not None:
        sid = int(season_id)
        if "season_id" in df.columns:
            df = df[pd.to_numeric(df["season_id"], errors="coerce") == sid]
        elif "season_folder" in df.columns:
            df = df[df["season_folder"].astype(str).str.startswith(f"{sid}_")]
        else:
            return pd.DataFrame()
    return df


def _build_schedule_matches_dataframe(
    data_dir: str,
    league_id: Optional[str],
    date: Optional[str],
    season_id: Optional[int],
) -> pd.DataFrame:
    """
    Every downloaded match: the per-season summaries under data/matches are the source.

    The export CSV used to win whenever it existed, but it is only rewritten when a job
    finishes (a stopped job skips it) and only holds matches that have details, so a league
    could show "380 matches" on the Leagues page and none in the list. It is now only the
    fallback for data folders that have no summaries at all.
    """
    summary_pattern = os.path.join(data_dir, "matches", "**", "*_summary.csv")
    summary_files = glob.glob(summary_pattern, recursive=True)
    if not summary_files:
        return _read_processed_matches(data_dir, league_id, date, season_id)

    league_ids = _parse_league_ids(league_id)
    league_prefixes = {str(i) for i in league_ids} if league_ids is not None else None
    all_summary_data: List[pd.DataFrame] = []
    for file in summary_files:
        try:
            parent_dir = os.path.basename(os.path.dirname(file))
            league_prefix = parent_dir.split("_")[0]
            if league_prefixes is not None and league_prefix not in league_prefixes:
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
            df_part["league_folder"] = parent_dir
            all_summary_data.append(df_part)
        except Exception as e:
            logger.error(f"Error reading summary CSV {file}: {e}")

    if not all_summary_data:
        return pd.DataFrame()
    df_total = pd.concat(all_summary_data, ignore_index=True)
    if "match_id" in df_total.columns:
        df_total = df_total.drop_duplicates(subset=["match_id"], keep="last")
    if date and "match_date" in df_total.columns:
        df_total = df_total[df_total["match_date"].astype(str).str.contains(date, na=False)]
    if season_id is not None and "_file_season_id" in df_total.columns:
        df_total = df_total[df_total["_file_season_id"] == int(season_id)]
    return df_total


def _detail_match_ids(data_dir: str, league_id: Optional[str]) -> Set[str]:
    """Ids of matches whose details are on disk: match_details/<league>/<season>/<id>/basic.json."""
    base = os.path.join(data_dir, "match_details")
    if not os.path.isdir(base):
        return set()
    ids = _parse_league_ids(league_id)
    prefixes = {str(i) for i in ids} if ids is not None else None
    found: Set[str] = set()
    for league_dir in os.listdir(base):
        if league_dir == "processed":
            continue
        if prefixes is not None and league_dir.split("_")[0] not in prefixes:
            continue
        league_path = os.path.join(base, league_dir)
        if not os.path.isdir(league_path):
            continue
        for season_dir in os.listdir(league_path):
            season_path = os.path.join(league_path, season_dir)
            if not os.path.isdir(season_path):
                continue
            for mid in os.listdir(season_path):
                if os.path.exists(os.path.join(season_path, mid, "basic.json")):
                    found.add(mid)
    return found


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
    df = _build_schedule_matches_dataframe(data_dir, league_id, date, season_id)
    if df.empty:
        return MatchListResponse(items=[], total=0, limit=limit, offset=offset, sort=sort)

    df = df.copy()
    if "match_id" in df.columns:
        have = _detail_match_ids(data_dir, league_id)
        mid = pd.to_numeric(df["match_id"], errors="coerce").astype("Int64").astype(str)
        df["has_details"] = mid.isin(have)
        if details == "present":
            df = df[df["has_details"]]
        elif details == "missing":
            df = df[~df["has_details"]]
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


_MISSING_LIST_LIMIT = 500


def _get_missing_details_sync(league_id: int, season_id: Optional[int], data_dir: str) -> dict:
    matches_dir = os.path.join(data_dir, "matches")
    match_details_dir = os.path.join(data_dir, "match_details")

    all_match_ids = set()
    match_info = {}

    if os.path.exists(matches_dir):
        # Sezon özetleri `{season_id}_{ad}_summary.csv` adıyla lig dizininde durur
        csv_glob = f"{season_id}_*.csv" if season_id else "*.csv"
        pattern = os.path.join(matches_dir, f"{league_id}_*", csv_glob)
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
    missing_all = [match_info[mid] for mid in sorted(missing_ids) if mid in match_info]
    missing = missing_all[:_MISSING_LIST_LIMIT]

    return {
        "total_matches": len(all_match_ids),
        "missing_count": len(missing_ids),
        "missing": missing,
        "truncated": len(missing_all) > len(missing),
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


def _fetch_single_match_sync(match_id: str) -> dict:
    """
    Tek maçı çeker. SofaScore /event isteğini ya da istenen dilimlerin hepsini reddettiyse tipli hatayı
    (src/web/upstream.py: {"detail": {"reason", "message"}}) fırlatır; eskiden ilki 404 "Match data could
    not be fetched", ikincisi hiçbir dilim kaydedilmeden 200 "success" oluyordu. Maç SofaScore'da yoksa ya
    da bitmemişse 404; dilimlerden biri bile yanıt aldıysa "success".
    """
    from src.match_data_fetcher import MatchDataFetcher, SingleFetchReport

    fetcher = MatchDataFetcher(config_manager=config_manager, data_dir=config_manager.get_data_dir())
    report = SingleFetchReport()
    before = bridge_health.snapshot()
    try:
        result = None
        if fetcher._find_match_path(match_id):
            result = fetcher.refill_missing_match_slices(match_id, report=report)
        # Reddedilen /event, kayıt yokmuş gibi baştan istenmez: neden belli, aynı istek yine reddedilir
        if result is None and report.upstream_failure() is None:
            result = fetcher.fetch_match_data(match_id, report=report)
        failure = report.upstream_failure()
        if failure is not None:
            reason = _single_fetch_reason(failure, before)
            logger.error(
                f"Single match fetch for {match_id} failed ({reason}): "
                f"{'the event request' if failure is report.event else 'every slice request'} failed "
                f"({failure.reason}, HTTP {failure.http_status})"
            )
            raise upstream.http_error(reason)
        if result is None:
            raise _SyncHttpError(
                404,
                "Match data could not be fetched (may be unfinished or unavailable).",
            )
        return {"status": "success", "match_id": match_id}
    except (_SyncHttpError, HTTPException):
        raise
    except Exception as e:
        # İstek hataları buraya ulaşmaz (yukarıda rapordan okunur); kalanlar diske yazma ve benzeri hatalardır
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
    if _job_store.snapshot().get("is_running"):
        # Çalışan iş aynı dosyalara yazıyor ve istek hızını zaten kullanıyor
        raise HTTPException(status_code=409, detail="A fetch job is running; try again when it finishes.")
    try:
        return await asyncio.to_thread(_fetch_single_match_sync, str(match_id))
    except _SyncHttpError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
