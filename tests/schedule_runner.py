"""
Testlerin sezon programı çekimi: src/services/listing.py `ScheduleLister`'ı, sahte bir istek işleviyle çalıştırır.

Terminal menüsünün eski yüzü (`MatchFetcher.fetch_all_matches_for_season` ve `fetch_all_rounds_async`; istek
katmanının async işlevi `make_api_request_async(session, yol, max_retries)`) plan maddesi FX-15'te kalktı. Onu
kullanan testlerin sahte istek işlevleri aynı imzayla burada çalışır: yanıt (sözlük) ya da SofaScoreScraperError.
Sayfalar çekildikçe Store'a yazılır (`EntityStore.put`), kurallar listing'indir.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

from src.exceptions import CircuitOpenError, SofaScoreScraperError
from src.services import listing
from src.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from src.store import open_store

# Eski istek işlevinin imzası: (oturum, göreli yol, deneme sayısı) → yanıt
LegacyApi = Callable[[Any, str, Optional[int]], Awaitable[Any]]


def _answer(data: Any) -> Outcome:
    """İstek katmanının döndürdüğü veri → sonuç (src.client.Client ile aynı kural)."""
    if data is None:
        return Outcome(SLICE_FAILED, reason="other")
    if not data:
        return Outcome(SLICE_EMPTY, data=data, reason="empty")
    return Outcome(SLICE_OK, data=data)


def legacy_get(api: LegacyApi) -> listing.Get:
    """Sahte eski istek işlevi → listing'in istek işlevi (`Get`)."""

    async def get(path: str, retries: Optional[int] = None) -> Outcome:
        try:
            data = await api(None, path, retries)
        except CircuitOpenError:
            return Outcome(SLICE_SKIPPED, reason="breaker")
        except SofaScoreScraperError as exc:
            return Outcome.from_error(exc)
        return _answer(data)

    return get


async def inline(fn: Callable[[], Any]) -> Any:
    """Depoya erişim: çağıranın döngüsünde, doğrudan (eski yüz gibi)."""
    return fn()


async def list_schedule_async(data_dir: Union[str, Path], league_id: int, season_id: int, api: LegacyApi, *,
                              only_finished: bool, concurrency: int = 2, max_round: int = listing.MAX_ROUND
                              ) -> listing.ListingResult:
    """Sezonun programı: sayfalar çekilir çekilmez veri dizininin deposuna yazılır."""
    lister = listing.ScheduleLister(open_store(data_dir), only_finished=only_finished, concurrency=concurrency,
                                    max_round=max_round)
    return await lister.list(league_id, season_id, legacy_get(api), inline)


def list_schedule(data_dir: Union[str, Path], league_id: int, season_id: int, api: LegacyApi, *,
                  only_finished: bool, concurrency: int = 2) -> listing.ListingResult:
    """`list_schedule_async`'in senkron yüzü."""
    return asyncio.run(list_schedule_async(data_dir, league_id, season_id, api, only_finished=only_finished,
                                           concurrency=concurrency))
