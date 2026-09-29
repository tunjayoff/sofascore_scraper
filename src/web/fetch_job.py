"""Arka plan veri çekme işi: /api/fetch isteğinin seçtiği ligleri, sezonları ve maçları indirir."""
from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Dict, List, Set, Tuple

from src.SofaScoreUi import SimpleSofaScoreUI
from src.utils import FetchCancelled
from src.web.routes.common import _job_store, _refresh_scraper_state, config_manager, logger

if TYPE_CHECKING:
    from src.web.routes.scrape import FetchRequest


def update_state(status: str, progress: int, task: str):
    finished = status in ("Completed", "Failed", "Cancelled")
    db_status = None
    if status == "Completed":
        db_status = "completed"
    elif status == "Failed":
        db_status = "failed"
    elif status == "Cancelled":
        db_status = "cancelled"
    _job_store.update(
        status=status if not finished else status,
        progress=progress,
        current_task=task,
        append_log=f"[{status}] {task}",
        finished=finished,
    )
    # map human status for mirror when finished
    if finished and db_status:
        # update() already mapped; refresh
        pass
    _refresh_scraper_state()


def run_fetch_job(job_id: str, payload: "FetchRequest") -> None:
    """/api/fetch işini çalıştırır (kendi thread'inde; bkz. routes/scrape.trigger_fetch)."""
    from src.utils import set_cancel_check

    # Every request this job makes (and its waits between retries) now checks the cancel
    # flag, so "Stop" takes effect within a fraction of a second instead of after the
    # current season or a 2-minute 403 back-off.
    set_cancel_check(_job_store.cancel_requested)

    def web_detail_progress(lo: int, hi: int):
        """Terminal tqdm ile uyumlu ara adımlar: done/total → [lo, hi]."""

        def cb(done: int, total: int, task: str) -> None:
            hi_clamped = max(lo, min(99, hi))
            if total <= 0:
                pct = hi_clamped
            else:
                pct = lo + int((done / total) * (hi_clamped - lo))
            pct = max(lo, min(hi_clamped, pct))
            _job_store.update(matches_done=done, matches_total=total)
            update_state("Running", pct, task)

        return cb

    summary = (
        f"{len(payload.selections)} targeted selection(s)"
        if payload.selections
        else (str(payload.league_id) if payload.league_id else "All Leagues")
    )
    update_state("Running", 0, f"Starting fetch for {summary}")
    logger.info("Background fetch started. job_id=%s Target: %s", job_id, summary)

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
                        should_cancel=_job_store.cancel_requested,
                    )
            else:
                logger.info(
                    "Wizard/targeted fetch: %s row(s)",
                    len(payload.selections),
                )
                unique_leagues = sorted({s.league_id for s in payload.selections})
                for league_id in unique_leagues:
                    if _job_store.cancel_requested():
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
                        resolved = ui.season_fetcher.resolve_season_id(s.league_id, sid)
                        if resolved != sid:
                            update_state(
                                "Running",
                                8,
                                f"Season {sid} outdated → using {resolved} for league {s.league_id}",
                            )
                        key = (s.league_id, resolved)
                        if key not in seen_pairs:
                            seen_pairs.add(key)
                            pairs.append(key)

                # Keep detail fetch aligned with resolved season ids
                resolved_by_league: Dict[int, List[int]] = {}
                for lid, sid in pairs:
                    resolved_by_league.setdefault(lid, []).append(sid)

                total_ops = len(pairs) or 1
                empty_schedule = 0
                for idx, (lid, sid) in enumerate(pairs):
                    if _job_store.cancel_requested():
                        break
                    pct = 10 + int((idx / total_ops) * 45)
                    update_state(
                        "Running",
                        pct,
                        f"Fetching matches: league {lid}, season {sid}",
                    )
                    ok = ui.match_fetcher.fetch_matches_for_season(lid, sid)
                    if not ok:
                        empty_schedule += 1
                _job_store.update(schedule_empty_seasons=empty_schedule)
                _refresh_scraper_state()
                if empty_schedule and empty_schedule >= len(pairs):
                    from src.i18n import get_i18n
                    update_state(
                        "Running",
                        54,
                        get_i18n().t("fetch_zero_matches"),
                    )

                league_to_seasons: Dict[int, List[int]] = resolved_by_league
                n_leagues = len(league_to_seasons) or 1
                detail_span = 30
                for i, (lid, only_sids) in enumerate(league_to_seasons.items()):
                    if _job_store.cancel_requested():
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
                        should_cancel=_job_store.cancel_requested,
                    )

        elif payload.league_id:
            print(f"--> Updating specific league: {payload.league_id} (Mode: {payload.mode})")

            if payload.mode == "full":
                update_state("Running", 10, f"Fetching seasons for League {payload.league_id}...")
                ui.season_fetcher.fetch_seasons_for_league(payload.league_id)

                update_state("Running", 30, f"Fetching matches for League {payload.league_id}...")
                seasons = ui.season_fetcher.get_seasons_for_league(payload.league_id)
                total_seasons = len(seasons)
                empty_schedule = 0
                for i, season in enumerate(seasons):
                    if _job_store.cancel_requested():
                        break
                    processing_season_name = season.get('name', season.get('year', 'Unknown'))
                    update_state("Running", 30 + int((i/total_seasons)*30) if total_seasons else 30, f"Fetching matches: {processing_season_name}")
                    ok = ui.match_fetcher.fetch_matches_for_season(payload.league_id, season["id"])
                    if not ok:
                        empty_schedule += 1
                _job_store.update(schedule_empty_seasons=empty_schedule)
                _refresh_scraper_state()
                if empty_schedule and total_seasons and empty_schedule >= total_seasons:
                    from src.i18n import get_i18n
                    update_state("Running", 55, get_i18n().t("fetch_zero_matches"))

            update_state("Running", 60, f"Fetching match details for League {payload.league_id}…")
            ui.match_data_fetcher.fetch_all_match_details(
                league_id=str(payload.league_id),
                max_seasons=0,
                progress_callback=web_detail_progress(60, 89),
                should_cancel=_job_store.cancel_requested,
            )

        else:
            if payload.mode == "details":
                update_state("Running", 10, "Fetching details for ALL existing matches…")
                ui.match_data_fetcher.fetch_all_match_details(
                    max_seasons=0,
                    progress_callback=web_detail_progress(10, 89),
                    should_cancel=_job_store.cancel_requested,
                )
            else:
                update_state("Running", 10, "Updating ALL leagues (Seasons, Matches, Details)…")
                print("--> Updating ALL leagues...")
                ui.update_all_leagues(progress_factory=web_detail_progress)

        if _job_store.cancel_requested():
            update_state("Cancelled", int(_job_store.snapshot().get("progress") or 0), "Cancelled")
        else:
            # Export
            update_state("Running", 90, "Exporting data to CSV...")
            print("--> Exporting to CSV...")
            ui.export_all_to_csv()

            from src.i18n import get_i18n
            empty_n = int(_job_store.snapshot().get("schedule_empty_seasons") or 0)
            result = {"schedule_empty_seasons": empty_n}
            _job_store.update(result=result)
            if empty_n > 0:
                update_state("Completed", 100, get_i18n().t("fetch_completed_with_warning"))
            else:
                update_state("Completed", 100, "Background Task Completed Successfully.")
            print("--> Background Task Completed Successfully.")
            logger.info("Background update and export completed.")

    except FetchCancelled:
        logger.info("Background fetch cancelled. job_id=%s", job_id)
        update_state("Cancelled", int(_job_store.snapshot().get("progress") or 0), "Cancelled")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Background update failed: {e}")
        logger.error(traceback.format_exc())
        print(f"--> Background Task FAILED: {e}")
        update_state("Failed", 0, f"Error: {error_msg}")
    finally:
        snap = _job_store.snapshot()
        if snap.get("is_running"):
            # Ensure job is closed if worker exited without terminal status
            if _job_store.cancel_requested():
                update_state("Cancelled", int(snap.get("progress") or 0), "Cancelled")
            else:
                update_state("Failed", int(snap.get("progress") or 0), "Interrupted")
        _refresh_scraper_state()
