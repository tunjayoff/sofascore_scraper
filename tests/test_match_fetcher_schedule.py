"""Unit tests for schedule URL/strategy helpers and paginated fallback (mocked HTTP)."""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from src.exceptions import ResourceNotFoundError
from src.match_fetcher import MatchFetcher


def _finished_event(eid: int) -> Dict[str, Any]:
    return {
        "id": eid,
        "homeTeam": {"name": f"Home{eid}", "id": eid},
        "awayTeam": {"name": f"Away{eid}", "id": eid + 1000},
        "homeScore": {"current": 1},
        "awayScore": {"current": 0},
        "status": {"description": "Ended", "type": "finished"},
        "startTimestamp": 1_700_000_000,
    }


class TestScheduleHelpers(unittest.TestCase):
    def test_build_round_events_url_plain(self):
        url = MatchFetcher.build_round_events_url(17, 100, 3)
        self.assertEqual(url, "/unique-tournament/17/season/100/events/round/3")

    def test_build_round_events_url_with_slug(self):
        url = MatchFetcher.build_round_events_url(
            242, 70158, 227, slug="western-conference-semifinals"
        )
        self.assertEqual(
            url,
            "/unique-tournament/242/season/70158/events/round/227/slug/western-conference-semifinals",
        )

    def test_week_based_pl_rounds(self):
        rounds = [{"round": n} for n in range(1, 39)]
        self.assertTrue(MatchFetcher.is_week_based_rounds(rounds, max_round=50))

    def test_mls_playoff_rounds_not_week_based(self):
        rounds = [
            {
                "round": 227,
                "name": "Western conference semifinals",
                "slug": "western-conference-semifinals",
            },
            {
                "round": 195,
                "name": "Eastern conference semifinals",
                "slug": "eastern-conference-semifinals",
            },
        ]
        self.assertFalse(MatchFetcher.is_week_based_rounds(rounds, max_round=50))

    def test_empty_rounds_not_week_based(self):
        self.assertFalse(MatchFetcher.is_week_based_rounds([], max_round=50))


class TestFetchStrategyMocked(unittest.IsolatedAsyncioTestCase):
    def _make_fetcher(self, tmp: str) -> MatchFetcher:
        config = MagicMock()
        config.get_leagues.return_value = {242: "MLS", 17: "Premier League"}
        config.get_max_concurrent.return_value = 2
        seasons = MagicMock()
        seasons.get_season_name.return_value = "MLS 2025"
        return MatchFetcher(config, seasons, data_dir=tmp)

    async def test_paginated_fallback_when_rounds_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = self._make_fetcher(tmp)
            out = os.path.join(tmp, "matches_out")
            os.makedirs(out, exist_ok=True)

            fetcher._fetch_rounds_metadata = AsyncMock(return_value=[])  # type: ignore
            fetcher._fetch_and_save_event_pages = AsyncMock(  # type: ignore
                return_value=[{"events": [_finished_event(1)], "round": "last_0"}]
            )
            fetcher._fetch_and_save_round = AsyncMock(return_value=None)  # type: ignore

            with patch(
                "src.utils.create_session_async",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=MagicMock()),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ):
                results = await fetcher.fetch_all_rounds_async(242, 70158, out, max_round=50)

            self.assertEqual(len(results), 1)
            fetcher._fetch_and_save_event_pages.assert_awaited()  # type: ignore

    async def test_week_based_uses_round_urls_not_event_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = self._make_fetcher(tmp)
            out = os.path.join(tmp, "matches_out")
            os.makedirs(out, exist_ok=True)

            fetcher._fetch_rounds_metadata = AsyncMock(  # type: ignore
                return_value=[{"round": 1}, {"round": 2}]
            )

            async def save_round(*args, **kwargs):
                rn = args[4]
                return {"events": [_finished_event(100 + rn)], "round": rn}

            fetcher._fetch_and_save_round = AsyncMock(side_effect=save_round)  # type: ignore
            fetcher._fetch_and_save_event_pages = AsyncMock(return_value=[])  # type: ignore

            with patch(
                "src.utils.create_session_async",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=MagicMock()),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ):
                results = await fetcher.fetch_all_rounds_async(17, 96668, out, max_round=50)

            self.assertEqual(len(results), 2)
            fetcher._fetch_and_save_event_pages.assert_not_awaited()  # type: ignore
            calls = fetcher._fetch_and_save_round.await_args_list  # type: ignore
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(c.kwargs.get("slug") is None for c in calls))

    async def test_cup_slug_passed_for_week_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = self._make_fetcher(tmp)
            out = os.path.join(tmp, "out")
            os.makedirs(out, exist_ok=True)

            fetcher._fetch_rounds_metadata = AsyncMock(  # type: ignore
                return_value=[{"round": 1, "slug": "week-1"}]
            )
            seen_slugs: List[Optional[str]] = []

            async def save_round(*args, **kwargs):
                seen_slugs.append(kwargs.get("slug"))
                return {"events": [_finished_event(9)], "round": 1}

            fetcher._fetch_and_save_round = AsyncMock(side_effect=save_round)  # type: ignore
            fetcher._fetch_and_save_event_pages = AsyncMock(return_value=[])  # type: ignore

            with patch(
                "src.utils.create_session_async",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=MagicMock()),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ):
                results = await fetcher.fetch_all_rounds_async(17, 1, out, max_round=50)

            self.assertEqual(len(results), 1)
            self.assertEqual(seen_slugs, ["week-1"])

    async def test_event_pages_dedupe_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = self._make_fetcher(tmp)
            out = os.path.join(tmp, "out")
            os.makedirs(out, exist_ok=True)
            session = MagicMock()

            pages = {
                "/unique-tournament/242/season/1/events/last/0": {
                    "events": [_finished_event(1), _finished_event(2)],
                    "hasNextPage": True,
                },
                "/unique-tournament/242/season/1/events/last/1": {
                    "events": [_finished_event(2), _finished_event(3)],
                    "hasNextPage": False,
                },
            }

            async def fake_api(session, url, max_retries=None):
                if "/events/next/" in url:
                    raise ResourceNotFoundError("404")
                val = pages.get(url)
                if val is None:
                    raise ResourceNotFoundError(url)
                return val

            with patch("src.utils.make_api_request_async", new=fake_api), patch(
                "src.utils.FETCH_ONLY_FINISHED", True
            ):
                results = await fetcher._fetch_and_save_event_pages(session, 242, 1, out)

            ids = []
            for chunk in results:
                ids.extend(e["id"] for e in chunk["events"])
            self.assertEqual(sorted(ids), [1, 2, 3])
            self.assertTrue(os.path.exists(os.path.join(out, "events_last_0.json")))


class TestFinishedAndSeasonYear(unittest.TestCase):
    def test_finished_by_type_without_ended_description(self):
        fetcher = MatchFetcher.__new__(MatchFetcher)
        ev = {"status": {"type": "finished", "description": "AET", "code": 110}}
        self.assertTrue(MatchFetcher._is_finished_event(ev))
        filtered, total, finished = fetcher._filter_finished_matches(
            {
                "events": [
                    ev,
                    {"status": {"type": "notstarted", "description": "Not started"}},
                ]
            }
        )
        self.assertEqual(total, 2)
        self.assertEqual(finished, 1)
        self.assertEqual(len(filtered["events"]), 1)

    def test_parse_short_season_year(self):
        self.assertEqual(MatchFetcher._parse_season_start_year("Premier League 26/27"), 2026)
        self.assertEqual(MatchFetcher._parse_season_start_year("", {"year": "25/26"}), 2025)
        self.assertEqual(MatchFetcher._parse_season_start_year("Premier League 2024/25"), 2024)

    def test_empty_finished_falls_back_to_previous_season(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = MagicMock()
            config.get_league_by_id.return_value = "Premier League"
            seasons = MagicMock()
            seasons.get_season_name.side_effect = lambda lid, sid: {
                96668: "Premier League 26/27",
                76986: "Premier League 25/26",
            }[sid]
            seasons.get_season_info.return_value = {
                "id": 96668,
                "year": "26/27",
                "name": "Premier League 26/27",
            }
            seasons.get_seasons_for_league.return_value = [
                {"id": 96668, "year": "26/27", "name": "Premier League 26/27"},
                {"id": 76986, "year": "25/26", "name": "Premier League 25/26"},
            ]
            seasons._get_sortable_year_value.side_effect = (
                lambda y: float(str(y).split("/")[0]) if y else 0
            )
            fetcher = MatchFetcher(config, seasons, data_dir=tmp)
            fetcher._save_season_summary = MagicMock()  # type: ignore

            def rounds_for(league_id, season_id, max_round=50):
                if season_id == 76986:
                    return [{"events": [_finished_event(1)], "round": 1}]
                return []

            fetcher.fetch_all_rounds_for_season = MagicMock(side_effect=rounds_for)  # type: ignore
            ok = fetcher.fetch_all_matches_for_season(17, 96668, 50, 0)
            self.assertTrue(ok)
            self.assertEqual(
                [c.args[1] for c in fetcher.fetch_all_rounds_for_season.call_args_list],
                [96668, 76986],
            )


if __name__ == "__main__":
    unittest.main()
