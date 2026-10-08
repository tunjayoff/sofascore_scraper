"""
Eşitleme servisinin sınırlarını sahte bağlama bağlar (sofascore_scraper/services/sync.py).

Servis listeleri ve detay aşamasını modül düzeyindeki işlevlerle çalıştırır (`list_seasons`, `stored_seasons`,
`resolve_season_id`, `list_schedule`, `detail_phase`). Sahte bağlamla koşan testler (`SimpleNamespace`) bunları
bağlamın sahtelerine yönlendirir:

  ctx.seasons    list_seasons(lid, max_age=...), get_seasons_for_league(lid), resolve_season_id(lid, sid)
  ctx.schedule   list_schedule(lid, sid, max_age=...)
  ctx.details    detay aşamasının yüzü (DetailPhase): candidates, pending, fetch, fetch_selected,
                 refresh_due, refresh, breaker_tripped, status_counts, refresh_listener

2.x'te bu yüzler bağlamın `season_fetcher`, `match_fetcher` ve `match_data_fetcher` özellikleriydi (P30'da kalktı).
Gerçek bir ServiceContext (bu adlar olmadan) her işlevin gerçek yoluna gider.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sınırları bağlamın sahtelerine bağlar; bağlamda sahte yoksa gerçek işlev çağrılır."""
    from sofascore_scraper.services import sync

    real = {name: getattr(sync, name) for name in
            ("detail_phase", "list_seasons", "stored_seasons", "resolve_season_id", "list_schedule")}

    def detail_phase(ctx: Any) -> Any:
        return ctx.details if hasattr(ctx, "details") else real["detail_phase"](ctx)

    def list_seasons(ctx: Any, league_id: int, *, max_age: Optional[float]) -> Any:
        if hasattr(ctx, "seasons"):
            return ctx.seasons.list_seasons(league_id, max_age=max_age)
        return real["list_seasons"](ctx, league_id, max_age=max_age)

    def stored_seasons(ctx: Any, league_id: int) -> Any:
        if hasattr(ctx, "seasons"):
            return ctx.seasons.get_seasons_for_league(league_id)
        return real["stored_seasons"](ctx, league_id)

    def resolve_season_id(ctx: Any, league_id: int, season_id: int) -> Any:
        if hasattr(ctx, "seasons"):
            return ctx.seasons.resolve_season_id(league_id, season_id)
        return real["resolve_season_id"](ctx, league_id, season_id)

    def list_schedule(ctx: Any, league_id: int, season_id: int, *, max_age: Optional[float]) -> Any:
        if hasattr(ctx, "schedule"):
            return ctx.schedule.list_schedule(league_id, season_id, max_age=max_age)
        return real["list_schedule"](ctx, league_id, season_id, max_age=max_age)

    monkeypatch.setattr(sync, "detail_phase", detail_phase)
    monkeypatch.setattr(sync, "list_seasons", list_seasons)
    monkeypatch.setattr(sync, "stored_seasons", stored_seasons)
    monkeypatch.setattr(sync, "resolve_season_id", resolve_season_id)
    monkeypatch.setattr(sync, "list_schedule", list_schedule)


__all__ = ["install"]
