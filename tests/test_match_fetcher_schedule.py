"""Sezon programı sarmalayıcısı (src/match_fetcher.py): tur yolu, strateji, sayfalı liste, önceki sezona geçiş (sahte HTTP)."""

from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from src.exceptions import APIError, ResourceNotFoundError
from src.match_fetcher import MatchFetcher
from src.store import Ref, open_store


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


@contextlib.contextmanager
def _data_dir() -> Iterator[str]:
    """
    Geçici veri dizini; silinmeden önce sarmalayıcının bu dizin için açtığı depo kapatılır. Depo kayıt defterinde
    açık kalır ve catalog.db açıkken Windows dizini silemez (WinError 32).
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            yield tmp
        finally:
            from src.store import api as store_api

            root = os.path.abspath(tmp)
            for store in list(store_api._registry.values()):
                if os.fspath(store.data_dir) == root:
                    store.close()


def _no_session() -> Any:
    return AsyncMock(__aenter__=AsyncMock(return_value=MagicMock()), __aexit__=AsyncMock(return_value=False))


def _api(pages: Dict[str, Any], calls: List[str]) -> Any:
    """Eski istek yolunun sahte hali (src.utils.make_api_request_async; göreli yol): bilinmeyen yol 404."""

    async def fake_api(session: Any, url: str, max_retries: Optional[int] = None) -> Any:
        calls.append(url)
        if url not in pages:
            raise ResourceNotFoundError(url)
        return pages[url]

    return fake_api


class TestFetchStrategyMocked(unittest.IsolatedAsyncioTestCase):
    """Uyumluluk yolu (fetch_all_rounds_async): kurallar src/services/listing.py'den, istekler eski async işlevden."""

    def _make_fetcher(self, tmp: str) -> MatchFetcher:
        config = MagicMock()
        config.get_leagues.return_value = {242: "MLS", 17: "Premier League"}
        config.get_max_concurrent.return_value = 2
        seasons = MagicMock()
        seasons.get_season_name.return_value = "MLS 2025"
        return MatchFetcher(config, seasons, data_dir=tmp)

    async def _rounds(self, fetcher: MatchFetcher, league: int, season: int, pages: Dict[str, Any],
                      calls: List[str]) -> List[Dict[str, Any]]:
        with patch("src.utils.make_api_request_async", new=_api(pages, calls)), \
                patch("src.utils.create_session_async", return_value=_no_session()), \
                patch("src.utils.FETCH_ONLY_FINISHED", True):
            return await fetcher.fetch_all_rounds_async(league, season, max_round=50)

    async def test_paginated_fallback_when_rounds_missing(self):
        with _data_dir() as tmp:
            fetcher = self._make_fetcher(tmp)
            base = "/unique-tournament/242/season/70158"
            calls: List[str] = []
            pages = {f"{base}/rounds": {"rounds": []},
                     f"{base}/events/last/0": {"events": [_finished_event(1)], "hasNextPage": False}}

            results = await self._rounds(fetcher, 242, 70158, pages, calls)

            self.assertEqual([r["round"] for r in results], ["last_0"])
            self.assertEqual(calls, [f"{base}/rounds", f"{base}/events/last/0", f"{base}/events/next/0"])

    async def test_week_based_uses_round_urls_not_event_list(self):
        with _data_dir() as tmp:
            fetcher = self._make_fetcher(tmp)
            base = "/unique-tournament/17/season/96668"
            calls: List[str] = []
            pages = {f"{base}/rounds": {"rounds": [{"round": 1}, {"round": 2}]},
                     f"{base}/events/round/1": {"events": [_finished_event(101)]},
                     f"{base}/events/round/2": {"events": [_finished_event(102)]}}

            results = await self._rounds(fetcher, 17, 96668, pages, calls)

            self.assertEqual(len(results), 2)
            self.assertEqual(sorted(calls), [f"{base}/events/round/1", f"{base}/events/round/2", f"{base}/rounds"])

    async def test_cup_slug_passed_for_week_entry(self):
        with _data_dir() as tmp:
            fetcher = self._make_fetcher(tmp)
            base = "/unique-tournament/17/season/1"
            calls: List[str] = []
            pages = {f"{base}/rounds": {"rounds": [{"round": 1, "slug": "week-1"}]},
                     f"{base}/events/round/1/slug/week-1": {"events": [_finished_event(9)]}}

            results = await self._rounds(fetcher, 17, 1, pages, calls)

            self.assertEqual(len(results), 1)
            self.assertEqual(calls, [f"{base}/rounds", f"{base}/events/round/1/slug/week-1"])

    async def test_event_pages_dedupe_and_save(self):
        with _data_dir() as tmp:
            fetcher = self._make_fetcher(tmp)
            base = "/unique-tournament/242/season/1"
            calls: List[str] = []
            pages = {
                f"{base}/events/last/0": {"events": [_finished_event(1), _finished_event(2)], "hasNextPage": True},
                f"{base}/events/last/1": {"events": [_finished_event(2), _finished_event(3)], "hasNextPage": False},
            }

            results = await self._rounds(fetcher, 242, 1, pages, calls)

            ids = []
            for chunk in results:
                ids.extend(e["id"] for e in chunk["events"])
            self.assertEqual(sorted(ids), [1, 2, 3])
            # Sayfalar Store'a yazılır (ST-22): sezonun schedule/last_0 ve last_1 dilimleri
            store = open_store(tmp)
            try:
                ref = Ref.season(242, 1)
                self.assertEqual([info.sub for info in store.entities.slices(ref)], ["last_0", "last_1"])
                first = store.entities.payload(ref, "schedule", "last_0")
                self.assertEqual([e["id"] for e in first["events"]], [1, 2])
                self.assertEqual(store.entities.slice(ref, "schedule", "last_1").meta, {"filtered": True})
                self.assertFalse(os.path.exists(os.path.join(tmp, "matches", "242_MLS")))
            finally:
                store.close()

    async def test_a_failed_round_does_not_stop_the_other_rounds(self):
        """Eski yüz başarısızlığı sonuçta göstermez (sayfa listesi); tipli sonuç list_schedule'dadır."""
        with _data_dir() as tmp:
            fetcher = self._make_fetcher(tmp)
            base = "/unique-tournament/17/season/5"
            calls: List[str] = []
            pages = {f"{base}/rounds": {"rounds": [{"round": 1}, {"round": 2}]},
                     f"{base}/events/round/1": {"events": [_finished_event(1)]}}

            async def failing(session: Any, url: str, max_retries: Optional[int] = None) -> Any:
                if url.endswith("/round/2"):
                    raise APIError("HTTP 500", status_code=500)
                return await _api(pages, calls)(session, url, max_retries)

            with patch("src.utils.make_api_request_async", new=failing), \
                    patch("src.utils.create_session_async", return_value=_no_session()), \
                    patch("src.utils.FETCH_ONLY_FINISHED", True):
                results = await fetcher.fetch_all_rounds_async(17, 5)

            self.assertEqual([r["round"] for r in results], [1])


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
            fetcher._report_season = MagicMock()  # type: ignore

            def rounds_for(league_id, season_id, max_round=50):
                if season_id == 76986:
                    return [{"events": [_finished_event(1)], "round": 1}]
                return []

            fetcher.fetch_all_rounds_for_season = MagicMock(side_effect=rounds_for)  # type: ignore
            ok = fetcher.fetch_all_matches_for_season(17, 96668, 50, 0, allow_fallback=True)
            self.assertTrue(ok)
            self.assertEqual(
                [c.args[1] for c in fetcher.fetch_all_rounds_for_season.call_args_list],
                [96668, 76986],
            )

            # Açık seçimde (varsayılan) başka sezona geçilmez ve başarısız sayılır
            fetcher.fetch_all_rounds_for_season.reset_mock()
            self.assertFalse(fetcher.fetch_all_matches_for_season(17, 96668, 50, 0))
            self.assertEqual(
                [c.args[1] for c in fetcher.fetch_all_rounds_for_season.call_args_list],
                [96668],
            )


if __name__ == "__main__":
    unittest.main()
