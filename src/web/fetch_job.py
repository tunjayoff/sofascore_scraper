"""Arka plan veri çekme işi: /api/fetch isteğinin seçtiği ligleri, sezonları ve maçları indirir.

Her istek aynı aşamalardan geçer (sezon listeleri → maç listeleri → maç detayları → CSV) ve
ilerleme `JobProgress` ile yapılandırılmış olarak yayınlanır; kart metinleri ön yüzde çevrilir.
"""
from __future__ import annotations

import traceback
from collections import Counter
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.SofaScoreUi import SimpleSofaScoreUI
from src.utils import FetchCancelled
from src.web.progress import JobProgress
from src.web.routes.common import _job_store, _refresh_scraper_state, config_manager, logger

if TYPE_CHECKING:
    from src.web.routes.scrape import FetchRequest


def update_state(status: str, progress: int, task: str):
    finished = status in ("Completed", "Failed", "Cancelled")
    _job_store.update(
        status=status,
        progress=progress,
        current_task=task,
        append_log=f"[{status}] {task}",
        finished=finished,
    )
    _refresh_scraper_state()


def _note(task: str) -> None:
    """Ham görev metni: iş günlüğü ve eski istemciler için; yüzdeye dokunmaz."""
    _job_store.update(current_task=task, append_log=f"[Running] {task}")
    _refresh_scraper_state()


def _publish(fields: Dict[str, Any]) -> None:
    _job_store.update(**fields)
    _refresh_scraper_state()


def _breaker_reason(status_counts: Dict[str, int]) -> str:
    """Devreyi kesen hatanın türü: en sık görülen 403 / 429 / 5xx."""
    relevant = Counter({k: v for k, v in (status_counts or {}).items() if k in ("403", "429", "5xx")})
    return relevant.most_common(1)[0][0] if relevant else "other"


# (lig, sezon, sezon adı) — maç listesi aşamasının bir adımı
SeasonStep = Tuple[int, int, Optional[str]]


def run_fetch_job(job_id: str, payload: "FetchRequest") -> None:
    """/api/fetch işini çalıştırır (kendi thread'inde; bkz. routes/scrape.trigger_fetch)."""
    from src.utils import set_cancel_check, set_wait_notifier

    # Every request this job makes (and its waits between retries) now checks the cancel
    # flag, so "Stop" takes effect within a fraction of a second instead of after the
    # current season or a 2-minute 403 back-off.
    set_cancel_check(_job_store.cancel_requested)

    cancelled = _job_store.cancel_requested
    full = payload.mode != "details"
    phases = ["seasons", "matches", "details", "export"] if full else ["details", "export"]
    tracker = JobProgress(phases, _publish)
    # 429/403 geri çekilmeleri kartta geri sayım olarak görünsün
    set_wait_notifier(tracker.wait)

    league_names = config_manager.get_leagues()

    def lname(lid: Optional[int]) -> Optional[str]:
        return league_names.get(int(lid)) if lid is not None else None

    summary = (
        f"{len(payload.selections)} targeted selection(s)"
        if payload.selections
        else (str(payload.league_id) if payload.league_id else "All Leagues")
    )
    update_state("Running", 0, f"Starting fetch for {summary}")
    logger.info("Background fetch started. job_id=%s Target: %s", job_id, summary)

    try:
        ui = SimpleSofaScoreUI(config_manager=config_manager)
        empty_schedule = 0

        # Detay aşamasının planı: lig → yalnızca bu sezonlar (None: tüm sezonlar).
        # Anahtar None ise maç dizinindeki tüm ligler taranır.
        detail_plan: Dict[Optional[int], Optional[List[int]]] = {}
        explicit_match_ids: List[int] = []

        if payload.selections:
            unique_leagues = sorted({s.league_id for s in payload.selections})
        elif payload.league_id:
            unique_leagues = [payload.league_id]
        else:
            unique_leagues = sorted(league_names)

        if full:
            # 1. Sezon listeleri
            tracker.start_phase("seasons", len(unique_leagues))
            for i, lid in enumerate(unique_leagues):
                if cancelled():
                    break
                tracker.set_context(league_id=lid, league_name=lname(lid))
                _note(f"Refreshing season list for league {lid}...")
                try:
                    ui.season_fetcher.fetch_seasons_for_league(lid)
                except Exception as e:
                    logger.error("Season list failed for league %s: %s", lid, e)
                tracker.advance(i + 1)

            # 2. Maç listeleri: hangi (lig, sezon) çiftleri
            steps: List[SeasonStep] = []
            seen: set = set()

            def season_names(lid: int) -> Dict[int, Optional[str]]:
                return {
                    int(s["id"]): s.get("name") or s.get("year")
                    for s in ui.season_fetcher.get_seasons_for_league(lid)
                    if s.get("id") is not None
                }

            if payload.selections:
                for s in payload.selections:
                    names = season_names(s.league_id)
                    for sid in s.season_ids or []:
                        resolved = ui.season_fetcher.resolve_season_id(s.league_id, sid)
                        if resolved != sid:
                            _note(f"Season {sid} outdated → using {resolved} for league {s.league_id}")
                        if (s.league_id, resolved) not in seen:
                            seen.add((s.league_id, resolved))
                            steps.append((s.league_id, resolved, names.get(resolved)))
                for lid, sid, _ in steps:
                    detail_plan.setdefault(lid, []).append(sid)  # type: ignore[union-attr]
            else:
                for lid in unique_leagues:
                    for sid, name in season_names(lid).items():
                        steps.append((lid, sid, name))
                if payload.league_id:
                    detail_plan[payload.league_id] = None
                else:
                    detail_plan[None] = None

            if not cancelled():
                tracker.start_phase("matches", len(steps))
            for idx, (lid, sid, sname) in enumerate(steps):
                if cancelled():
                    break
                tracker.set_context(league_id=lid, league_name=lname(lid), season_name=sname)
                _note(f"Fetching matches: league {lid}, season {sid}")
                try:
                    ok = ui.match_fetcher.fetch_matches_for_season(lid, sid)
                except Exception as e:
                    logger.error("Match list failed for league %s season %s: %s", lid, sid, e)
                    ok = False
                if not ok:
                    empty_schedule += 1
                tracker.advance(idx + 1)
            _job_store.update(schedule_empty_seasons=empty_schedule)
            _refresh_scraper_state()
            if steps and empty_schedule >= len(steps):
                from src.i18n import get_i18n
                _note(get_i18n().t("fetch_zero_matches"))
        elif payload.selections:
            for s in payload.selections:
                explicit_match_ids.extend(s.match_ids or [])
            explicit_match_ids = list(dict.fromkeys(explicit_match_ids))
        elif payload.league_id:
            detail_plan[payload.league_id] = None
        else:
            detail_plan[None] = None

        # 3. Maç detayları
        if not cancelled():
            # Geçici kayıtların yenilenmesi kartta ayrı sayılır (JobProgress.detail()["refreshed"])
            ui.match_data_fetcher.refresh_listener = tracker.add_refreshed
            try:
                _run_details(ui, tracker, detail_plan, explicit_match_ids, payload, lname)
            finally:
                ui.match_data_fetcher.refresh_listener = None

        if cancelled():
            update_state("Cancelled", tracker.percent(), "Cancelled")
        else:
            # 4. CSV
            tracker.start_phase("export", 1)
            _note("Exporting data to CSV...")
            print("--> Exporting to CSV...")
            ui.export_all_to_csv()
            tracker.advance(1)

            from src.i18n import get_i18n
            empty_n = int(_job_store.snapshot().get("schedule_empty_seasons") or 0)
            _job_store.update(result={"schedule_empty_seasons": empty_n, **tracker.result()})
            if empty_n > 0:
                update_state("Completed", 100, get_i18n().t("fetch_completed_with_warning"))
            else:
                update_state("Completed", 100, "Background Task Completed Successfully.")
            print("--> Background Task Completed Successfully.")
            logger.info("Background update and export completed.")

    except FetchCancelled:
        logger.info("Background fetch cancelled. job_id=%s", job_id)
        update_state("Cancelled", tracker.percent(), "Cancelled")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Background update failed: {e}")
        logger.error(traceback.format_exc())
        print(f"--> Background Task FAILED: {e}")
        update_state("Failed", tracker.percent(), f"Error: {error_msg}")
    finally:
        set_wait_notifier(None)
        snap = _job_store.snapshot()
        if snap.get("is_running"):
            # Ensure job is closed if worker exited without terminal status
            if _job_store.cancel_requested():
                update_state("Cancelled", int(snap.get("progress") or 0), "Cancelled")
            else:
                update_state("Failed", int(snap.get("progress") or 0), "Interrupted")
        _refresh_scraper_state()


def _run_details(
    ui: SimpleSofaScoreUI,
    tracker: JobProgress,
    detail_plan: Dict[Optional[int], Optional[List[int]]],
    explicit_match_ids: List[int],
    payload: "FetchRequest",
    lname,
) -> None:
    """Detay aşaması: önce tüm liglerde eksik maçları sayar, sonra tek bir sayaçla indirir."""
    md = ui.match_data_fetcher
    cancelled = _job_store.cancel_requested
    tracker.start_phase("details", 0)

    if explicit_match_ids:
        league_of = {int(m): s.league_id for s in payload.selections or [] for m in s.match_ids or []}
        leagues = set(league_of.values())
        lid = next(iter(leagues)) if len(leagues) == 1 else None
        if lid is not None:
            tracker.set_context(league_id=lid, league_name=lname(lid))
        _note(f"Fetching details for {len(explicit_match_ids)} selected matches...")

        def cb(done: int, total: int, _msg: str) -> None:
            if total != tracker.detail()["total"]:
                tracker.set_total(total)
            tracker.advance(done)

        md.fetch_matches_batch(
            explicit_match_ids,
            progress_callback=cb,
            should_cancel=cancelled,
            failed_callback=lambda mid: tracker.add_failed(mid, league_of.get(int(mid))),
        )
        return

    md.begin_job_cache()
    try:
        _note("Checking which matches need details...")
        work: List[Tuple[Optional[int], List[str]]] = []
        for lid, only_sids in detail_plan.items():
            if cancelled():
                return
            ids = md.collect_detail_match_ids(
                league_id=str(lid) if lid is not None else None,
                max_seasons=0,
                only_season_ids=only_sids,
            ) or []
            pending = md.pending_detail_ids(ids)
            if pending:
                work.append((lid, pending))
        tracker.set_total(sum(len(p) for _, p in work))

        offset = 0
        for lid, pending in work:
            if cancelled():
                break
            tracker.set_context(league_id=lid, league_name=lname(lid))
            _note(f"Fetching match details: league {lid if lid is not None else 'all'} ({len(pending)} matches)…")
            md.fetch_detail_ids(
                pending,
                progress_callback=lambda done, _t, _m, _o=offset: tracker.advance(_o + done),
                should_cancel=cancelled,
                failed_callback=lambda mid, _l=lid: tracker.add_failed(mid, _l),
            )
            offset += len(pending)
            if md.rate_limit_breaker_triggered:
                tracker.breaker(_breaker_reason(md.last_status_counts))
                _note("Too many failed requests; stopped fetching details.")
                break
    finally:
        md.end_job_cache()
