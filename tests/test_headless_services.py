"""
Komut satırının çağırdığı servisler (plan maddesi P10; docs/design/02-services.md 2.7):

  * SyncSpec.export: okunmaz; işin CSV aşaması yok (EX-1: dışa aktarma istendiğinde üretilir);
  * SyncService'in yalnızca yenileme kipi (`mode="refresh"`): `main.py --refresh-only`nin satır içi kodundan taşınan
    çağrı sırası, sayılar, devre kesici, iptal ve depolama hatası;
  * MaintenanceService.recheck_unavailable: "yok" işaretlerinin yeniden denetimi.

`main.py`nin bu servisleri nasıl çağırdığı tests/test_cli_services.py'de, uçtan uca davranış (istekler, dosyalar,
çıktı) tests/characterization/test_cli_goldens.py'dedir. Gerçek ağ yok: indiriciler sahtedir.
"""
from __future__ import annotations

import dataclasses

import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from src import breaker as request_breaker
from src.client import context as request_ctx
from src.config_manager import ConfigManager
from src.exceptions import StorageError
from src.jobs.progress import JobProgress
from src.services.context import build_context
from src.services.maintenance import MaintenanceService, ResetCounts
from src.services.sync import (
    DETAILS_PHASES,
    FULL_PHASES,
    REFRESH_PHASES,
    RefreshCounts,
    SyncResult,
    SyncService,
    SyncSpec,
)


@pytest.fixture(autouse=True)
def _clean_request_context() -> Iterator[None]:
    """Her test boş bir istek bağlamıyla başlar; servis çıkışta kendi kurduklarını geri almış olmalıdır."""
    with request_ctx.request_context():
        yield
        assert request_breaker.current() is None


class Untouched:
    """Bu kipte hiç çağrılmaması gereken indirici: herhangi bir özniteliğe erişim testi düşürür."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"{self._name}.{attr} must not be used in this mode")


class FakeRefresher:
    """MatchDataFetcher'ın yenileme ve detay yüzü: çağrıları sırayla kaydeder."""

    def __init__(self, due: Optional[List[str]] = None, stats: Optional[Dict[str, Any]] = None) -> None:
        self.due = list(due or [])
        self.stats: Dict[str, Any] = dict(stats or {"refreshed": 0, "changed": 0, "failed": 0})
        self.calls: List[Any] = []
        self.rate_limit_breaker_triggered = False
        self.last_status_counts: Dict[str, int] = {}
        self.refresh_listener: Optional[Callable[[str, bool], None]] = None
        self.listener_during_refresh: Any = "not called"
        self.cancel_check_during_refresh: Any = "not called"
        self.error: Optional[BaseException] = None
        self.changed_ids: Tuple[str, ...] = ()

    # --- yenileme ---
    def begin_job_cache(self) -> None:
        self.calls.append("begin_job_cache")

    def end_job_cache(self) -> None:
        self.calls.append("end_job_cache")

    def refresh_due_ids(self, league_id: Any = None) -> List[str]:
        self.calls.append(("refresh_due_ids", league_id))
        return list(self.due)

    def refresh_matches(
        self, match_ids: List[str], progress_callback: Any = None, should_cancel: Any = None
    ) -> Dict[str, Any]:
        self.calls.append(("refresh_matches", list(match_ids)))
        self.listener_during_refresh = self.refresh_listener
        self.cancel_check_during_refresh = should_cancel
        if self.error is not None:
            raise self.error
        for n, mid in enumerate(match_ids[: self.stats.get("refreshed", 0)], start=1):
            if self.refresh_listener:
                self.refresh_listener(mid, mid in self.changed_ids)
            if progress_callback:
                progress_callback(n, len(match_ids), "")
        return dict(self.stats)

    # --- detaylar (CSV aşaması testleri için) ---
    def collect_detail_match_ids(self, league_id: Any = None, max_seasons: int = 0, only_season_ids: Any = None) -> List[str]:
        self.calls.append(("collect_detail_match_ids", league_id))
        return []

    def pending_detail_ids(self, ids: List[str]) -> List[str]:
        return list(ids)


class RecordingHandle:
    """Servisin gördüğü iş: günlük satırlarını ve ilerlemenin geçtiği aşamaları kaydeder."""

    id = "job-1"

    def __init__(self, spec: SyncSpec, cancel_on: Optional[str] = None) -> None:
        self.lines: List[str] = []
        self.published: List[Dict[str, Any]] = []
        self.progress = JobProgress(list(spec.job_phases), self.published.append)
        self._cancel_on = cancel_on
        self._cancelled = False

    def cancelled(self) -> bool:
        return self._cancelled

    def log(self, message: str) -> None:
        self.lines.append(message)
        if self._cancel_on and self._cancel_on in message:
            self._cancelled = True

    def publish(self, fields: Any) -> None:
        pass

    @property
    def phases(self) -> List[str]:
        seen: List[str] = []
        for fields in self.published:
            phase = fields["detail"]["phase"]
            if phase and phase not in seen:
                seen.append(phase)
        return seen


def make_ctx(md: Any, *, seasons: Any = None, schedule: Any = None) -> Any:
    """ServiceContext'in yerini tutar: gerçek yapılandırma (kesici eşikleri), sahte indiriciler."""
    return SimpleNamespace(
        config=ConfigManager(),
        data_dir="unused",
        season_fetcher=seasons or Untouched("season_fetcher"),
        match_fetcher=schedule or Untouched("match_fetcher"),
        match_data_fetcher=md,
    )


# --- SyncSpec: aşamalar ------------------------------------------------------------------------------


def test_the_job_has_no_export_phase() -> None:
    """CSV aşaması kalktı (EX-1, karar D9); okunmayan `export` alanı da (FX-15)."""
    assert not hasattr(SyncSpec(), "export")
    assert SyncSpec().job_phases == FULL_PHASES == ("seasons", "matches", "details")
    assert SyncSpec(mode="details").job_phases == DETAILS_PHASES == ("details",)
    assert SyncSpec().job_phases == ("seasons", "matches", "details")
    assert SyncSpec(mode="details").job_phases == ("details",)


def test_refresh_mode_has_one_phase_whatever_the_export_switch_says() -> None:
    assert SyncSpec(mode="refresh").job_phases == REFRESH_PHASES == ("details",)
    assert SyncSpec(mode="refresh").job_phases == ("details",)


# --- CSV aşaması yok ---------------------------------------------------------------------------------


def test_no_csv_is_written_and_the_spec_has_no_export_field(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "export" not in {f.name for f in dataclasses.fields(SyncSpec)}  # FX-15: okunmayan alan kalktı
    ctx = make_ctx(FakeRefresher())
    spec = SyncSpec(mode="details", league_id=17)
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert "Exporting data to CSV..." not in handle.lines
    assert "export" not in handle.phases
    assert (result.state, result.refresh) == ("succeeded", None)


def test_a_run_without_a_handle_and_without_export_never_starts_the_export_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tutamaçsız çalıştırmanın ilerleme nesnesi `job_phases` ile kurulur: "export" aşaması onda yoktur."""
    result = SyncService(make_ctx(FakeRefresher())).run(SyncSpec(mode="details"))

    assert result == SyncResult(
        state="succeeded", schedule_empty_seasons=0, breaker=None, progress=result.progress, refresh=None
    )


# --- yalnızca yenileme -------------------------------------------------------------------------------


def test_refresh_mode_reproduces_the_call_order_of_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """begin_job_cache → refresh_due_ids → refresh_matches → end_job_cache; başka hiçbir indirici çağrılmaz."""
    md = FakeRefresher(due=["1", "2", "3"], stats={"refreshed": 2, "changed": 1, "failed": 1})
    md.changed_ids = ("2",)

    result = SyncService(make_ctx(md)).run(SyncSpec(mode="refresh"))

    assert md.calls == [
        "begin_job_cache", ("refresh_due_ids", None), ("refresh_matches", ["1", "2", "3"]), "end_job_cache",
    ]
    assert result.refresh == RefreshCounts(due=3, refreshed=2, changed=1, failed=1, skipped=0)
    assert (result.state, result.breaker, result.schedule_empty_seasons) == ("partial", None, 0)
    # Yenilenen her kayıt ilerlemeye de sayılır (web kartının okuduğu alanlar)
    assert (result.progress["refreshed"], result.progress["refresh_changed"]) == (2, 1)
    assert callable(md.listener_during_refresh) and md.refresh_listener is None


def test_refresh_mode_passes_the_league_and_succeeds_when_nothing_failed() -> None:
    md = FakeRefresher(due=["7"], stats={"refreshed": 1, "changed": 0, "failed": 0})

    result = SyncService(make_ctx(md)).run(SyncSpec(mode="refresh", league_id=17))

    assert ("refresh_due_ids", 17) in md.calls
    assert result.state == "succeeded"
    assert result.refresh == RefreshCounts(due=1, refreshed=1)


def test_refresh_mode_with_nothing_due_still_asks_the_fetcher_and_succeeds() -> None:
    md = FakeRefresher()

    result = SyncService(make_ctx(md)).run(SyncSpec(mode="refresh"))

    assert ("refresh_matches", []) in md.calls
    assert (result.state, result.refresh) == ("succeeded", RefreshCounts())


def test_refresh_mode_reports_a_breaker_stop_with_the_untried_records() -> None:
    md = FakeRefresher(
        due=[str(n) for n in range(10)],
        stats={"refreshed": 0, "changed": 0, "failed": 3, "breaker": "403", "skipped": 7},
    )
    spec = SyncSpec(mode="refresh")
    handle = RecordingHandle(spec)

    result = SyncService(make_ctx(md)).run(spec, handle=handle)

    assert (result.state, result.breaker) == ("partial", "403")
    assert result.refresh == RefreshCounts(due=10, refreshed=0, changed=0, failed=3, skipped=7)
    assert result.progress["breaker"] == "403"
    assert handle.lines == [
        "Refreshing 10 provisional records...",
        "Too many failed requests (403); stopped fetching provisional records.",
    ]


def test_refresh_mode_reports_progress_in_the_details_phase_and_can_be_cancelled() -> None:
    md = FakeRefresher(due=["1", "2"], stats={"refreshed": 2, "changed": 0, "failed": 0})
    spec = SyncSpec(mode="refresh")
    handle = RecordingHandle(spec, cancel_on="Refreshing")

    result = SyncService(make_ctx(md)).run(spec, handle=handle)

    assert handle.phases == ["details"]
    assert result.progress["details_total"] == 2 and result.progress["details_done"] == 2
    # İptal sorusu indiriciye verilir; iptal edilmiş iş "cancelled" biter ve o ana kadarki sayıları taşır
    assert md.cancel_check_during_refresh == handle.cancelled
    assert (result.state, result.refresh) == ("cancelled", RefreshCounts(due=2, refreshed=2))


def test_refresh_mode_lets_a_storage_error_through_and_cleans_up() -> None:
    md = FakeRefresher(due=["1"])
    md.error = StorageError.from_exception(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "/data/match_details/x")

    with pytest.raises(StorageError):
        SyncService(make_ctx(md)).run(SyncSpec(mode="refresh"))

    assert md.calls[-1] == "end_job_cache"
    assert md.refresh_listener is None


def test_refresh_mode_shares_one_breaker_with_the_request_layer() -> None:
    """Yenileme de işin tek kesicisiyle çalışır: indirici onu istek bağlamından bulur (request_breaker.scope)."""
    seen: List[Any] = []

    class Probe(FakeRefresher):
        def refresh_matches(self, match_ids: List[str], progress_callback: Any = None, should_cancel: Any = None) -> Dict[str, Any]:
            seen.append(request_breaker.current())
            return super().refresh_matches(match_ids, progress_callback, should_cancel)

    SyncService(make_ctx(Probe(due=["1"]))).run(SyncSpec(mode="refresh"))

    assert len(seen) == 1 and isinstance(seen[0], request_breaker.CircuitBreaker)


# --- MaintenanceService ------------------------------------------------------------------------------


class FakeMarkers:
    def __init__(self, result: Dict[str, int]) -> None:
        self.result = result
        self.calls: List[Tuple[Any, bool]] = []

    def reset_unavailable_markers(self, league_id: Any = None, include_confirmed: bool = False) -> Dict[str, int]:
        self.calls.append((league_id, include_confirmed))
        return dict(self.result)


@pytest.mark.parametrize("league_id, include_confirmed", [(None, False), (17, False), (None, True), (8, True)])
def test_recheck_unavailable_passes_the_scope_and_returns_typed_counts(
    league_id: Optional[int], include_confirmed: bool
) -> None:
    md = FakeMarkers({"matches": 2, "slices": 5, "scanned": 9})

    counts = MaintenanceService(make_ctx(md)).recheck_unavailable(league_id, include_confirmed=include_confirmed)

    assert md.calls == [(league_id, include_confirmed)]
    assert counts == ResetCounts(matches=2, slices=5, scanned=9)


def test_recheck_unavailable_defaults_to_every_league_and_unconfirmed_markers_only() -> None:
    md = FakeMarkers({"matches": 0, "slices": 0, "scanned": 0})

    assert MaintenanceService(make_ctx(md)).recheck_unavailable() == ResetCounts()
    assert md.calls == [(None, False)]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_recheck_unavailable_reopens_the_markers_on_disk(tmp_path: Path) -> None:
    """
    Gerçek indiriciyle: doğrulanmamış işaret geri alınır, doğrulanmış olan yalnızca include_confirmed ile. Kayıt
    eski düzende (önceki bir sürümün yazdığı); işaretler Store'da sıfırlanır (kayıt v3'e yükseltilir, eski dizine
    dokunulmaz; plan maddesi ST-21). Lig süzgeci maçın turnuvasına bakar.
    """
    match_dir = tmp_path / "match_details" / "17_Premier_League" / "season_24_25" / "4242"
    _write_json(match_dir / "basic.json", {"id": 4242, "tournament": {"uniqueTournament": {"id": 17}},
                                           "status": {"type": "finished", "code": 100}})
    # lineups: eski sürümden kalma, doğrulanmamış sayım; incidents: iki kesin yanıtla doğrulanmış
    _write_json(match_dir / "_unavailable.json", {"lineups": 2, "incidents": 2})
    _write_json(match_dir / "_slice_status.json", {"incidents": {"empty": {"count": 2}}})
    ctx = build_context(ConfigManager(), data_dir=str(tmp_path))
    service = MaintenanceService(ctx)

    def counts() -> Dict[str, Any]:
        return {i.key: (i.empty_count, i.unverified_empty_count) for i in ctx.store.events.slices(4242)
                if i.empty_count or i.unverified_empty_count}

    assert service.recheck_unavailable(8) == ResetCounts(matches=0, slices=0, scanned=0)  # başka lig
    assert service.recheck_unavailable(17) == ResetCounts(matches=1, slices=1, scanned=1)
    assert counts() == {"incidents": (2, 0)}
    assert service.recheck_unavailable(17) == ResetCounts(matches=0, slices=0, scanned=1)  # tekrarlanabilir

    assert service.recheck_unavailable(include_confirmed=True) == ResetCounts(matches=1, slices=1, scanned=1)
    assert counts() == {}
    assert json.loads((match_dir / "_unavailable.json").read_text(encoding="utf-8")) == {"lineups": 2, "incidents": 2}
