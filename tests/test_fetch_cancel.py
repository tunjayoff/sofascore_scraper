"""Cancel must stop match-detail loops mid-batch (stdlib assert)."""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

from src.match_data_fetcher import MatchDataFetcher


def test_fetch_matches_batch_honors_should_cancel():
    cfg = MagicMock()
    fetcher = MatchDataFetcher(config_manager=cfg, data_dir="/tmp/ss_cancel_test")
    calls: List[str] = []

    def fake_needs(mid: str) -> str:
        return "full"

    def fake_fetch(mid: str) -> Dict[str, Any]:
        calls.append(str(mid))
        return {"basic": {"id": int(mid)}}

    fetcher._needs_detail_fetch = fake_needs  # type: ignore[method-assign]
    fetcher.fetch_match_data = fake_fetch  # type: ignore[method-assign]
    fetcher.refill_missing_match_slices = MagicMock(return_value=None)  # type: ignore[method-assign]

    stop_after = 3

    def should_cancel() -> bool:
        return len(calls) >= stop_after

    ids = list(range(1, 21))
    results = fetcher.fetch_matches_batch(ids, progress_callback=None, should_cancel=should_cancel)
    assert len(calls) == stop_after, f"expected stop at {stop_after}, got {len(calls)}: {calls}"
    assert len(results) == stop_after


def test_fetch_all_outer_batch_honors_should_cancel():
    """Outer batch loop in fetch_all_match_details must not start next batch after cancel."""
    cfg = MagicMock()
    cfg.get_max_concurrent.return_value = 2
    fetcher = MatchDataFetcher(config_manager=cfg, data_dir="/tmp/ss_cancel_batches")
    started: List[int] = []
    state = {"n": 0}

    def should_cancel() -> bool:
        return state["n"] >= 1

    def tracking_parallel(match_ids, max_concurrent=10, progress_callback=None, should_cancel=None):
        state["n"] += 1
        started.append(len(match_ids))
        return {str(m): {"basic": {"id": m}} for m in list(match_ids)[:1]}

    fetcher.fetch_matches_batch_parallel = tracking_parallel  # type: ignore[method-assign]

    ids = [str(i) for i in range(250)]
    batch_size = 100
    ran = 0
    for i in range(0, len(ids), batch_size):
        if should_cancel():
            break
        batch = ids[i : i + batch_size]
        fetcher.fetch_matches_batch_parallel(batch, should_cancel=should_cancel)
        ran += 1
    assert ran == 1
    assert state["n"] == 1
    assert started == [100]


if __name__ == "__main__":
    test_fetch_matches_batch_honors_should_cancel()
    test_fetch_all_outer_batch_honors_should_cancel()
    print("cancel ok")
