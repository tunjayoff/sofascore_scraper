"""
Toplu indirme yolunun (fetch_matches_batch_async) istek hızını ağ olmadan ölçer.

SofaScore'a hiç istek atılmaz: tarayıcı köprüsü, sabit gecikmeli sahte bir taşıyıcıyla
değiştirilir. Ölçülen şey, kodun kendi sınırlarının (MAX_CONCURRENT, istek sonrası
WAIT_TIME_MIN/MAX beklemesi) ve ortak istek bütçesinin izin verdiği istek hızıdır.
Varsayılan bütçe (5 istek/sn, bkz. sofascore_scraper/throttle.py) bu tavanın bilerek çok altındadır.

Kullanım:
    python scripts/bench_bulk_rate.py                       # sınırlayıcı kapalı (kodun kendi tavanı)
    python scripts/bench_bulk_rate.py --rate default        # varsayılan ortak bütçeyle (5 istek/sn)
    python scripts/bench_bulk_rate.py --rate 20 --latency 0.2
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import tempfile
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

os.environ.setdefault("LOG_LEVEL", "ERROR")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _event(mid: int, sport: str) -> Dict[str, Any]:
    return {
        "id": mid,
        "tournament": {
            "name": "Bench League",
            "category": {"sport": {"slug": sport}},
            "uniqueTournament": {"id": 1, "name": "Bench League"},
        },
        "season": {"id": 1, "name": "Bench 26/27", "year": "26/27"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": 1, "name": "A"},
        "awayTeam": {"id": 2, "name": "B"},
        "homeScore": {"current": 1},
        "awayScore": {"current": 0},
        "startTimestamp": 1_600_000_000,
    }


def run_once(matches: int, latency: float, sport: str, rate: str) -> Dict[str, float]:
    """Bir koşu: `matches` yeni maç, istek başına `latency` sn sahte gecikme."""
    with tempfile.TemporaryDirectory(prefix="sofascore-bench-") as tmp:
        os.environ["DATA_DIR"] = os.path.join(tmp, "data")
        os.environ["SOFASCORE_THROTTLE_DIR"] = os.path.join(tmp, "throttle")
        if rate == "default":
            os.environ.pop("REQUEST_RATE_LIMIT", None)
        else:
            os.environ["REQUEST_RATE_LIMIT"] = rate

        from sofascore_scraper.client import bridge as cs
        import sofascore_scraper.utils as utils
        from sofascore_scraper import throttle
        from sofascore_scraper.config_manager import ConfigManager
        from sofascore_scraper.match_data_fetcher import MatchDataFetcher

        throttle.reset_for_tests()
        stamps: List[float] = []

        async def fake_browser_fetch(path_or_url: str) -> Any:
            # Gerçek köprüyle aynı nokta: ortak bütçe, isteğin hemen öncesinde
            await throttle.wait_async()
            stamps.append(time.monotonic())
            if latency:
                await asyncio.sleep(latency)
            tail = path_or_url.rstrip("/").rsplit("/", 1)[-1]
            if tail.isdigit():
                return {"event": _event(int(tail), sport)}
            return {tail: []}

        @contextlib.asynccontextmanager
        async def fake_session():
            yield MagicMock()

        cm = ConfigManager()
        fetcher = MatchDataFetcher(cm, data_dir=os.environ["DATA_DIR"])
        utils._browser_first_until = time.monotonic() + 3600  # bugünkü gerçek yol: önce tarayıcı
        ids = list(range(1, matches + 1))
        with patch.object(cs, "fetch_api_via_browser", fake_browser_fetch), \
                patch.object(utils, "create_session_async", fake_session):
            t0 = time.monotonic()
            results = asyncio.run(fetcher.fetch_matches_batch_async(ids, max_concurrent=cm.get_max_concurrent()))
            elapsed = time.monotonic() - t0
        if len(results) != matches:
            raise SystemExit(f"beklenen {matches} maç, alınan {len(results)}")
        # En yoğun 1 sn'lik pencere
        peak, j = 0, 0
        for i, s in enumerate(stamps):
            while s - stamps[j] > 1.0:
                j += 1
            peak = max(peak, i - j + 1)
        return {"requests": len(stamps), "elapsed": elapsed, "avg": len(stamps) / elapsed, "peak": peak}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--matches", type=int, default=100)
    p.add_argument("--latency", type=float, nargs="*", default=[0.0, 0.1, 0.2, 0.3, 0.5],
                   help="istek başına sahte gecikme (sn); birden çok değer verilebilir")
    p.add_argument("--sport", default="football", choices=["football", "basketball", "tennis"])
    p.add_argument("--rate", default="0", help="REQUEST_RATE_LIMIT (istek/sn); 0 = kapalı, 'default' = varsayılan")
    args = p.parse_args()

    print(f"spor={args.sport} maç={args.matches} REQUEST_RATE_LIMIT={args.rate} "
          f"MAX_CONCURRENT={os.getenv('MAX_CONCURRENT', '10')}")
    print(f"{'gecikme':>8} {'istek':>6} {'süre':>7} {'ort. istek/sn':>14} {'tepe istek/sn':>14}")
    for lat in args.latency:
        r = run_once(args.matches, lat, args.sport, args.rate)
        print(f"{lat:>7.2f}s {r['requests']:>6.0f} {r['elapsed']:>6.2f}s {r['avg']:>14.1f} {r['peak']:>14.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
