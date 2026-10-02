"""
SyncService, servis bağlamı ve CSV dışa aktarma servisi (plan maddesi P08; docs/design/02-services.md 2.3, 2.7).

Web işinin akışı src/web/fetch_job.py'den src/services/sync.py'ye taşındı; iş kaydına yazılanlar G-01
goldenlarıyla (tests/characterization/test_fetch_flows.py) ve tests/test_job_progress.py, test_breaker_phases.py,
test_storage_errors.py ile sabitlidir. Burada servis tek başına, bir iş deposu olmadan sınanır:

  * katman: src/web terminal arayüzünü, src/services hiçbir yüzü içe aktarmaz;
  * build_context: veri dizinleri ve üç indirici;
  * SyncService.run: aşamalar, detay planı, iptal, devre kesici, sonuç, istek bağlamının geri alınması;
  * export_all_csv: menü metni yazdırmaz, hatayı yutar; işin CSV aşaması yoktur (EX-1);
  * web bağdaştırıcısı: istek → SyncSpec, konsol satırları, iptal kontrolünün iş bitince geri alınması;
  * lig araması API kökünü istemciden alır.

Gerçek ağ yok: indiriciler sahtedir; lig aramasında curl taşıyıcısı sahtedir.
"""
from __future__ import annotations

import ast
import dataclasses
import errno
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

import src.utils as utils
from src import breaker as request_breaker
from src.client import context as request_ctx
from src.client import transport
from src.config_manager import ConfigManager
from src.exceptions import StorageError
from src.jobs.progress import JobProgress
from src.match_data_fetcher import MatchDataFetcher
from src.match_fetcher import MatchFetcher
from src.season_fetcher import SeasonFetcher
from src.services import export as export_service
from src.services.context import DATA_SUBDIRECTORIES, ServiceContext, build_context
from src.services.export import export_all_csv
from src.services.listing import ListingResult
from src.services.sync import (
    DETAILS_PHASES,
    FULL_PHASES,
    DetachedHandle,
    FailedListing,
    SyncResult,
    SyncSelection,
    SyncService,
    SyncSpec,
)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

LEAGUES = {17: "Premier League", 8: "LaLiga"}


# --- katman ------------------------------------------------------------------------------------------


def _imports(path: Path) -> List[str]:
    """Dosyadaki her içe aktarmanın tam adı (işlev içindekiler dahil); `from a import b` → 'a' ve 'a.b'."""
    found: List[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
            found.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _is_or_under(module: str, names: Sequence[str]) -> bool:
    return any(module == name or module.startswith(name + ".") for name in names)


def _violations(directory: Path, forbidden: Sequence[str]) -> List[str]:
    files = sorted(directory.rglob("*.py"))
    assert files, f"{directory} is empty"
    return [
        f"{path.relative_to(ROOT).as_posix()}: {module}"
        for path in files
        for module in _imports(path)
        if _is_or_under(module, forbidden)
    ]


def test_web_imports_nothing_from_the_terminal_ui() -> None:
    """Web, indiricilere servis bağlamı üzerinden ulaşır; menü arayüzü (P26'da silinir) ona gerekmez."""
    assert _violations(SRC / "web", ("src.ui", "src.SofaScoreUi")) == []


def test_services_import_no_face_module() -> None:
    assert _violations(SRC / "services", ("src.web", "src.ui", "src.cli", "src.SofaScoreUi")) == []


def test_the_import_scan_sees_function_level_and_from_imports(tmp_path: Path) -> None:
    source = "import os\ndef f():\n    from src.SofaScoreUi import SimpleSofaScoreUI\n    from src import ui\n"
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")

    found = _imports(path)

    assert "src.SofaScoreUi" in found and "src.ui" in found
    assert [m for m in found if _is_or_under(m, ("src.ui", "src.SofaScoreUi"))] == [
        "src.SofaScoreUi", "src.SofaScoreUi.SimpleSofaScoreUI", "src.ui",
    ]


def test_loading_the_web_job_does_not_load_the_terminal_ui(tmp_path: Path) -> None:
    """Ayrı süreçte: rotalar, iş modülü ve servisler yüklendiğinde menü modülleri yüklenmiş olmamalı."""
    code = (
        "import json, sys\n"
        "import src.web.routes, src.web.fetch_job, src.services.sync, src.services.export\n"
        "print(json.dumps(sorted(m for m in sys.modules"
        " if m == 'src.SofaScoreUi' or m == 'src.ui' or m.startswith('src.ui.'))))\n"
    )
    # Kendi veri dizini: rotalar yüklenirken açılan iş deposu testlerin ortak dizinine dokunmasın
    env = {**os.environ, "DATA_DIR": str(tmp_path / "data")}
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []


# --- build_context -----------------------------------------------------------------------------------


@pytest.fixture
def config() -> ConfigManager:
    return ConfigManager()


def test_build_context_creates_the_data_directories_and_the_three_fetchers(
    tmp_path: Path, config: ConfigManager
) -> None:
    data_dir = tmp_path / "new" / "data"

    ctx = build_context(config, data_dir=str(data_dir))

    assert isinstance(ctx, ServiceContext)
    assert ctx.config is config and ctx.data_dir == str(data_dir)
    assert DATA_SUBDIRECTORIES == ("seasons", "matches", "match_details", "datasets")
    for name in DATA_SUBDIRECTORIES:
        assert (data_dir / name).is_dir(), name
    assert isinstance(ctx.season_fetcher, SeasonFetcher) and ctx.season_fetcher.data_dir == str(data_dir)
    assert isinstance(ctx.match_fetcher, MatchFetcher) and ctx.match_fetcher.data_dir == str(data_dir)
    assert ctx.match_fetcher.season_fetcher is ctx.season_fetcher
    assert isinstance(ctx.match_data_fetcher, MatchDataFetcher) and ctx.match_data_fetcher.data_dir == str(data_dir)


def test_build_context_uses_the_configured_data_dir(
    tmp_path: Path, config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "configured"))

    ctx = build_context(config)

    assert ctx.data_dir == str(tmp_path / "configured")
    assert (tmp_path / "configured" / "match_details").is_dir()


def test_build_context_builds_new_fetchers_every_time(tmp_path: Path, config: ConfigManager) -> None:
    """İndiriciler iş durumunu taşır (iş önbelleği, son sayımlar): bağlam paylaşılmaz."""
    first = build_context(config, data_dir=str(tmp_path))
    second = build_context(config, data_dir=str(tmp_path))

    assert first.match_data_fetcher is not second.match_data_fetcher
    assert first.season_fetcher is not second.season_fetcher


def test_build_context_raises_when_a_directory_cannot_be_created(tmp_path: Path, config: ConfigManager) -> None:
    """Terminal arayüzünün kurucusu gibi: dizin oluşturulamıyorsa iş başlamadan hata çıkar."""
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OSError):
        build_context(config, data_dir=str(blocker / "data"))


def test_build_context_is_frozen(tmp_path: Path, config: ConfigManager) -> None:
    ctx = build_context(config, data_dir=str(tmp_path))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.data_dir = "elsewhere"  # type: ignore[misc]


@pytest.mark.parametrize("use_color", ["false", "true"])
def test_build_context_leaves_the_colour_switch_to_the_logger(
    tmp_path: Path, config: ConfigManager, monkeypatch: pytest.MonkeyPatch, use_color: str
) -> None:
    """
    P15: bağlam NO_COLOR'a dokunmaz. Süreç başlarken günlükçü kurar (src/logger.py); eskiden bağlam da her işte
    kuruyordu, ilerleme çubuğu (P14'te kalktı) renksiz yazsın diye.
    """
    monkeypatch.setenv("USE_COLOR", use_color)
    monkeypatch.setenv("NO_COLOR", "untouched")

    build_context(config, data_dir=str(tmp_path))

    assert os.environ["NO_COLOR"] == "untouched"


# --- sahte indiriciler ve tutamaç --------------------------------------------------------------------


class FakeSeasons:
    def __init__(
        self,
        seasons: Optional[Dict[int, List[Dict[str, Any]]]] = None,
        resolve: Optional[Dict[Tuple[int, int], int]] = None,
        failing: Sequence[int] = (),
    ) -> None:
        self.seasons = seasons or {}
        self.resolve = resolve or {}
        self.failing = set(failing)
        self.fetched: List[int] = []

    def fetch_seasons_for_league(self, league_id: int) -> List[Dict[str, Any]]:
        self.fetched.append(league_id)
        if league_id in self.failing:
            raise RuntimeError("season list unavailable")
        return self.seasons.get(league_id, [])

    def list_seasons(self, league_id: int, *, max_age: Optional[float] = None) -> ListingResult:
        """Servisin tipli yüzü (P14): eski adlı sahte yöntemin sonucu, liste sonucu olarak."""
        return ListingResult("seasons", league_id, seasons=self.fetch_seasons_for_league(league_id))

    def get_seasons_for_league(self, league_id: int) -> List[Dict[str, Any]]:
        return self.seasons.get(league_id, [])

    def resolve_season_id(self, league_id: int, season_id: int) -> int:
        return self.resolve.get((league_id, season_id), season_id)


class FakeSchedule:
    def __init__(self, empty: Sequence[Tuple[int, int]] = (), raising: Sequence[Tuple[int, int]] = ()) -> None:
        self.empty = set(empty)
        self.raising = set(raising)
        self.calls: List[Tuple[int, int]] = []

    def fetch_matches_for_season(self, league_id: int, season_id: int) -> bool:
        self.calls.append((league_id, season_id))
        if (league_id, season_id) in self.raising:
            raise RuntimeError("schedule unavailable")
        return (league_id, season_id) not in self.empty

    def list_schedule(self, league_id: int, season_id: int, *, max_age: Optional[float] = None) -> ListingResult:
        """Servisin tipli yüzü (P14): True → maç listelendi, False → boş program; hata fırlatılır."""
        listed = self.fetch_matches_for_season(league_id, season_id)
        return ListingResult("schedule", league_id, season_id, chunks=[{"round": 1}] if listed else [])


class FakeDetails:
    """MatchDataFetcher'ın servisin kullandığı yüzü; çağrıları kaydeder."""

    def __init__(self, pending: Optional[Dict[Optional[str], List[str]]] = None, failing: Sequence[str] = ()) -> None:
        self.pending = pending or {}
        self.failing = set(failing)
        self.rate_limit_breaker_triggered = False
        self.last_status_counts: Dict[str, int] = {}
        self.refresh_listener: Optional[Callable[[str, bool], None]] = None
        self.collected: List[Tuple[Optional[str], Optional[List[int]]]] = []
        self.fetched: List[List[str]] = []
        self.batches: List[List[int]] = []
        self.cache_events: List[str] = []
        self.exports = 0
        self.export_result: Any = "/data/match_details/processed/all_matches_x.csv"
        self.during_fetch: Callable[[], None] = lambda: None
        self.listener_during_fetch: Any = "not called"

    def begin_job_cache(self) -> None:
        self.cache_events.append("begin")

    def end_job_cache(self) -> None:
        self.cache_events.append("end")

    def collect_detail_match_ids(
        self, league_id: Optional[str] = None, max_seasons: int = 0, only_season_ids: Optional[List[int]] = None
    ) -> List[str]:
        self.collected.append((league_id, only_season_ids))
        return list(self.pending.get(league_id, [])) + [f"done-{league_id}"]

    def pending_detail_ids(self, ids: List[str]) -> List[str]:
        return [i for i in ids if not i.startswith("done-")]

    def fetch_detail_ids(
        self, ids: List[str], progress_callback: Any = None, should_cancel: Any = None, failed_callback: Any = None
    ) -> int:
        self.fetched.append(list(ids))
        self.listener_during_fetch = self.refresh_listener
        self.during_fetch()
        for n, mid in enumerate(ids, start=1):
            if mid in self.failing:
                failed_callback(mid)
            progress_callback(n, len(ids), "")
        return len(ids)

    def fetch_matches_batch(
        self, ids: List[int], progress_callback: Any = None, should_cancel: Any = None, failed_callback: Any = None
    ) -> None:
        self.batches.append(list(ids))
        self.during_fetch()
        for n, mid in enumerate(ids, start=1):
            if str(mid) in self.failing:
                failed_callback(mid)
            progress_callback(n, len(ids), "")

    def convert_all_matches_to_csv(self) -> Any:
        self.exports += 1
        if isinstance(self.export_result, BaseException):
            raise self.export_result
        return self.export_result


class RecordingHandle:
    """Servisin gördüğü iş: her çağrıyı sırayla kaydeder; `cancel_on` geçen günlük satırından sonra iptal ister."""

    id = "job-1"

    def __init__(self, spec: SyncSpec, cancel_on: Optional[str] = None) -> None:
        self.events: List[Tuple[str, Any]] = []
        self.progress = JobProgress(list(spec.job_phases), lambda fields: self.events.append(("progress", fields)))
        self._cancel_on = cancel_on
        self._cancelled = False

    def cancelled(self) -> bool:
        return self._cancelled

    def log(self, message: str) -> None:
        self.events.append(("log", message))
        if self._cancel_on and self._cancel_on in message:
            self._cancelled = True

    def publish(self, fields: Any) -> None:
        self.events.append(("publish", dict(fields)))

    @property
    def lines(self) -> List[str]:
        return [value for kind, value in self.events if kind == "log"]

    @property
    def phases(self) -> List[str]:
        """İlerlemenin geçtiği aşamalar, sırayla ve yinelenmeden."""
        seen: List[str] = []
        for kind, value in self.events:
            if kind == "progress" and value["detail"]["phase"] and value["detail"]["phase"] not in seen:
                seen.append(value["detail"]["phase"])
        return seen


def make_ctx(
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seasons: Optional[FakeSeasons] = None,
    schedule: Optional[FakeSchedule] = None,
    details: Optional[FakeDetails] = None,
    leagues: Optional[Dict[int, str]] = None,
) -> Any:
    monkeypatch.setattr(config, "get_leagues", lambda: dict(LEAGUES if leagues is None else leagues))
    return SimpleNamespace(
        config=config,
        data_dir="unused",
        season_fetcher=seasons or FakeSeasons(),
        match_fetcher=schedule or FakeSchedule(),
        match_data_fetcher=details or FakeDetails(),
    )


@pytest.fixture(autouse=True)
def _clean_request_context() -> Iterator[None]:
    """Her test boş bir istek bağlamıyla başlar ve başka testlere bir şey bırakmadığı denetlenir."""
    with request_ctx.request_context():
        yield


def _request_context_state() -> Tuple[Any, Any, Any]:
    return request_ctx._cancel_check.get(), request_ctx._wait_notifier.get(), request_breaker.current()


# --- SyncSpec ----------------------------------------------------------------------------------------


def test_spec_defaults_and_phases() -> None:
    spec = SyncSpec()

    assert (spec.mode, spec.league_id, spec.selections) == ("full", None, ())
    assert spec.job_phases == FULL_PHASES == ("seasons", "matches", "details")
    assert SyncSpec(mode="details").job_phases == DETAILS_PHASES == ("details",)
    assert SyncSelection(league_id=17) == SyncSelection(league_id=17, season_ids=(), match_ids=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.mode = "details"  # type: ignore[misc]


# --- SyncService.run: tam kip ------------------------------------------------------------------------


def test_full_run_of_one_league_goes_through_the_three_phases(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seasons = FakeSeasons({17: [{"id": 1, "name": "PL 24/25"}, {"id": 2, "year": "23/24"}, {"name": "no id"}]})
    schedule = FakeSchedule()
    details = FakeDetails({"17": ["a", "b"]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule, details=details)
    spec = SyncSpec(mode="full", league_id=17)
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert seasons.fetched == [17]
    assert schedule.calls == [(17, 1), (17, 2)]
    assert details.collected == [("17", None)]  # tek lig, tüm sezonlar
    assert details.fetched == [["a", "b"]] and details.cache_events == ["begin", "end"]
    assert details.exports == 0  # CSV aşaması yok (EX-1)
    assert handle.lines == [
        "Refreshing season list for league 17...",
        "Fetching matches: league 17, season 1",
        "Fetching matches: league 17, season 2",
        "Checking which matches need details...",
        "Fetching match details: league 17 (2 matches)…",
    ]
    assert handle.phases == ["seasons", "matches", "details"]
    assert ("publish", {"schedule_empty_seasons": 0}) in handle.events
    assert result == SyncResult(
        state="succeeded", schedule_empty_seasons=0, breaker=None, progress=handle.progress.result()
    )
    assert result.progress["details_done"] == 2 and result.progress["details_total"] == 2
    # Servis yazdırmaz: konsol metni onu çağıran yüzün işidir
    assert "-->" not in capsys.readouterr().out


def test_the_matches_phase_names_the_league_and_the_season(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    seasons = FakeSeasons({17: [{"id": 1, "name": "PL 24/25"}, {"id": 2, "year": "23/24"}]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons)
    spec = SyncSpec(league_id=17)
    handle = RecordingHandle(spec)

    SyncService(ctx).run(spec, handle=handle)

    contexts = [
        (d.get("league_id"), d.get("league_name"), d.get("season_name"))
        for kind, value in handle.events
        if kind == "progress" and (d := value["detail"])["phase"] == "matches" and "league_id" in d
    ]
    assert (17, "Premier League", "PL 24/25") in contexts and (17, "Premier League", "23/24") in contexts


def test_without_a_league_every_configured_league_is_synced_in_id_order(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    seasons = FakeSeasons({17: [{"id": 1}], 8: [{"id": 2}]})
    schedule = FakeSchedule()
    details = FakeDetails({None: ["x"]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule, details=details)

    result = SyncService(ctx).run(SyncSpec())

    assert seasons.fetched == [8, 17]
    assert schedule.calls == [(8, 2), (17, 1)]
    # Anahtar None: maç dizinindeki bütün ligler taranır
    assert details.collected == [(None, None)] and details.fetched == [["x"]]
    assert result.state == "succeeded"


def test_season_selections_resolve_retired_ids_and_limit_the_detail_plan(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    seasons = FakeSeasons(
        {17: [{"id": 11, "name": "PL new"}], 8: [{"id": 2, "name": "LaLiga 24/25"}]},
        resolve={(17, 10): 11},
    )
    schedule = FakeSchedule()
    details = FakeDetails({"17": ["a"], "8": ["b"]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule, details=details)
    spec = SyncSpec(
        selections=(
            # 10 emekli, 11'e çözülür; 11 ikinci kez eklenmez. Tam kipte match_ids okunmaz.
            SyncSelection(league_id=17, season_ids=(10, 11), match_ids=(999,)),
            SyncSelection(league_id=8, season_ids=(2,)),
        )
    )
    handle = RecordingHandle(spec)

    SyncService(ctx).run(spec, handle=handle)

    assert seasons.fetched == [8, 17]  # sezon listeleri lig ID'si sırasıyla
    assert schedule.calls == [(17, 11), (8, 2)]  # maç listeleri seçim sırasıyla
    assert "Season 10 outdated → using 11 for league 17" in handle.lines
    assert details.collected == [("17", [11]), ("8", [2])]
    assert details.batches == []


def test_a_selection_without_seasons_fetches_no_schedule_and_no_details(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule = FakeSchedule()
    details = FakeDetails({"17": ["a"]})
    ctx = make_ctx(config, monkeypatch, seasons=FakeSeasons({17: [{"id": 1}]}), schedule=schedule, details=details)
    spec = SyncSpec(selections=(SyncSelection(league_id=17),))
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert schedule.calls == [] and details.collected == [] and details.fetched == []
    assert handle.lines == [
        "Refreshing season list for league 17...",
        "Checking which matches need details...",
    ]
    assert result.state == "succeeded" and result.schedule_empty_seasons == 0


def test_a_failing_season_list_does_not_stop_the_run(config: ConfigManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """P14: çekilemeyen sezon listesi başarısız bir iş birimidir (iş `partial` biter); kayıtlı liste kullanılır."""
    seasons = FakeSeasons({17: [{"id": 1}]}, failing=[17])
    schedule = FakeSchedule()
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule)
    spec = SyncSpec(league_id=17)
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert schedule.calls == [(17, 1)] and result.state == "partial"
    assert result.failed_listings == (FailedListing("seasons", 17, None, "other"),)
    assert "The season list of league 17 could not be fetched (other)." in handle.lines


def test_empty_and_failing_schedules_are_counted_and_published(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    seasons = FakeSeasons({17: [{"id": 1}, {"id": 2}, {"id": 3}]})
    schedule = FakeSchedule(empty=[(17, 1)], raising=[(17, 3)])
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule)
    spec = SyncSpec(league_id=17)
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    # P14: yalnızca boş program "maç yok" sayılır; çekilemeyen program başarısız bir iş birimidir
    assert result.schedule_empty_seasons == 1
    assert ("publish", {"schedule_empty_seasons": 1}) in handle.events
    assert result.failed_listings == (FailedListing("schedule", 17, 3, "other"),) and result.state == "partial"
    # Bir sezon maç döndürdü: "hiç maç yok" satırı yazılmaz
    assert not any("0 matches" in line or "no matches" in line.lower() for line in handle.lines)


def test_when_every_schedule_is_empty_the_job_log_says_so(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.i18n import get_i18n

    seasons = FakeSeasons({17: [{"id": 1}, {"id": 2}]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=FakeSchedule(empty=[(17, 1), (17, 2)]))
    spec = SyncSpec(league_id=17)
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert result.schedule_empty_seasons == 2
    assert get_i18n().t("fetch_zero_matches") in handle.lines


# --- SyncService.run: detay kipi ---------------------------------------------------------------------


def test_details_mode_skips_the_listing_phases(config: ConfigManager, monkeypatch: pytest.MonkeyPatch) -> None:
    seasons, schedule = FakeSeasons({17: [{"id": 1}]}), FakeSchedule()
    details = FakeDetails({"17": ["a"]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule, details=details)
    spec = SyncSpec(mode="details", league_id=17)
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert seasons.fetched == [] and schedule.calls == []
    assert handle.phases == ["details"]
    assert details.collected == [("17", None)] and details.fetched == [["a"]]
    assert not any(kind == "publish" for kind, _ in handle.events)
    assert result.state == "succeeded" and result.schedule_empty_seasons == 0


def test_details_mode_without_a_league_scans_every_league(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = FakeDetails({None: ["a", "b"]})
    ctx = make_ctx(config, monkeypatch, details=details)

    SyncService(ctx).run(SyncSpec(mode="details"))

    assert details.collected == [(None, None)] and details.fetched == [["a", "b"]]


def test_selected_matches_are_fetched_once_each_and_failures_name_their_league(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = FakeDetails(failing=["3"])
    ctx = make_ctx(config, monkeypatch, details=details)
    spec = SyncSpec(
        mode="details",
        selections=(
            # Detay kipinde season_ids okunmaz
            SyncSelection(league_id=17, season_ids=(1,), match_ids=(1, 2, 1)),
            SyncSelection(league_id=8, match_ids=(3, 2)),
        ),
    )
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert details.batches == [[1, 2, 3]]
    assert details.collected == [] and details.cache_events == []
    assert handle.lines == ["Fetching details for 3 selected matches..."]
    assert result.progress["failed"] == [{"match_id": "3", "league_id": 8}]
    assert result.state == "partial" and result.breaker is None
    assert result.progress["details_done"] == 3 and result.progress["details_total"] == 3


def test_selected_matches_of_one_league_put_that_league_on_the_card(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(config, monkeypatch)
    spec = SyncSpec(mode="details", selections=(SyncSelection(league_id=8, match_ids=(5, 6)),))
    handle = RecordingHandle(spec)

    SyncService(ctx).run(spec, handle=handle)

    named = [v["detail"] for k, v in handle.events if k == "progress" and v["detail"].get("league_id") == 8]
    assert named and named[0]["league_name"] == "LaLiga"


def test_selections_without_matches_do_nothing_in_details_mode(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = FakeDetails({"17": ["a"]})
    ctx = make_ctx(config, monkeypatch, details=details)
    spec = SyncSpec(mode="details", selections=(SyncSelection(league_id=17, season_ids=(1,)),))

    result = SyncService(ctx).run(spec)

    assert details.batches == [] and details.collected == [] and details.fetched == []
    assert details.exports == 0 and result.state == "succeeded"


def test_run_without_a_handle_works_and_reports_the_result(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = FakeDetails({"17": ["a", "b"]}, failing=["b"])
    ctx = make_ctx(config, monkeypatch, details=details)

    result = SyncService(ctx).run(SyncSpec(mode="details", league_id=17))

    assert result.state == "partial"
    assert result.progress["failed_count"] == 1 and result.progress["failed"] == [{"match_id": "b", "league_id": 17}]


def test_detached_handle_cannot_be_cancelled_and_keeps_progress_to_itself() -> None:
    handle = DetachedHandle(SyncSpec(mode="details"))

    handle.progress.start_phase("details", 2)
    handle.publish({"schedule_empty_seasons": 1})
    handle.log("a line")

    assert handle.cancelled() is False and handle.id == ""
    assert handle.progress.phases == ["details"]


# --- yenileme sayacı, istek bağlamı ------------------------------------------------------------------


def test_the_job_installs_its_request_context_and_takes_it_back(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """İptal sorusu, bekleme bildirimi ve tek bir devre kesici yalnızca iş sürerken kuruludur."""
    details = FakeDetails({"17": ["a"]})
    seen: List[Tuple[Any, Any, Any]] = []
    details.during_fetch = lambda: seen.append(_request_context_state())
    ctx = make_ctx(config, monkeypatch, details=details)
    spec = SyncSpec(mode="details", league_id=17)
    handle = RecordingHandle(spec)

    SyncService(ctx).run(spec, handle=handle)

    (cancel, on_wait, breaker), = seen
    assert cancel == handle.cancelled and on_wait == handle.progress.wait
    assert isinstance(breaker, request_breaker.CircuitBreaker)
    assert _request_context_state() == (None, None, None)


def test_refreshed_records_are_counted_only_during_the_detail_phase(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = FakeDetails({"17": ["a"]})
    ctx = make_ctx(config, monkeypatch, details=details)
    spec = SyncSpec(mode="details", league_id=17)
    handle = RecordingHandle(spec)
    details.during_fetch = lambda: details.refresh_listener("a", True)  # type: ignore[misc]

    result = SyncService(ctx).run(spec, handle=handle)

    assert details.listener_during_fetch == handle.progress.add_refreshed
    assert details.refresh_listener is None
    assert result.progress["refreshed"] == 1 and result.progress["refresh_changed"] == 1


# --- devre kesici ------------------------------------------------------------------------------------


def test_a_breaker_reported_by_the_detail_fetcher_stops_the_remaining_leagues(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = FakeDetails({"17": ["a"], "8": ["b"]})

    def trip() -> None:
        details.rate_limit_breaker_triggered = True
        details.last_status_counts = {"403": 2, "404": 20, "429": 9}

    details.during_fetch = trip
    ctx = make_ctx(config, monkeypatch, seasons=FakeSeasons({17: [{"id": 1}], 8: [{"id": 2}]}), details=details)
    spec = SyncSpec(selections=(SyncSelection(8, (2,)), SyncSelection(17, (1,))))
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert details.fetched == [["b"]]  # lig 17 hiç başlamadı
    # İşin kesicisi açılmadı: neden indiricinin sayımından gelir (en sık görülen 403 / 429 / 5xx)
    assert result.breaker == "429" and result.state == "partial"
    assert handle.lines.count("Too many failed requests (429); stopped fetching match details.") == 1
    assert details.exports == 0  # CSV aşaması yok (EX-1)
    assert details.cache_events == ["begin", "end"]


def _trip(breaker: request_breaker.CircuitBreaker) -> None:
    for _ in range(50):
        breaker.record(request_breaker.FORBIDDEN)
        if breaker.tripped:
            return
    raise AssertionError("the breaker did not trip")


def test_an_open_breaker_stops_every_later_phase_and_is_reported_once(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_THRESHOLD_CONSECUTIVE", "3")
    seasons = FakeSeasons({17: [{"id": 1}, {"id": 2}], 8: [{"id": 3}]})
    schedule = FakeSchedule()
    details = FakeDetails({"17": ["a"]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule, details=details)
    original = seasons.fetch_seasons_for_league

    def blocked_season_list(league_id: int) -> List[Dict[str, Any]]:
        breaker = request_breaker.current()
        assert breaker is not None
        _trip(breaker)
        return original(league_id)

    monkeypatch.setattr(seasons, "fetch_seasons_for_league", blocked_season_list)
    spec = SyncSpec()
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert seasons.fetched == [8]  # ikinci ligin sezon listesi istenmedi
    assert schedule.calls == [] and details.collected == [] and details.fetched == []
    assert [line for line in handle.lines if line.startswith("Too many failed requests")] == [
        "Too many failed requests (403); stopped fetching season lists."
    ]
    assert result.breaker == "403" and result.state == "partial"
    # Hiçbir maç listesi istenmedi: "boş sezon" sayılmaz, "hiç maç yok" satırı yazılmaz
    assert result.schedule_empty_seasons == 0
    assert details.exports == 0


# --- iptal -------------------------------------------------------------------------------------------


def test_a_cancel_between_phases_ends_the_run_without_an_export(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    seasons = FakeSeasons({17: [{"id": 1}, {"id": 2}]})
    schedule = FakeSchedule()
    details = FakeDetails({"17": ["a"]})
    ctx = make_ctx(config, monkeypatch, seasons=seasons, schedule=schedule, details=details)
    spec = SyncSpec(league_id=17)
    handle = RecordingHandle(spec, cancel_on="Fetching matches: league 17, season 1")

    result = SyncService(ctx).run(spec, handle=handle)

    assert schedule.calls == [(17, 1)]  # ikinci sezon istenmedi
    assert details.collected == [] and details.exports == 0
    assert result.state == "cancelled"
    assert "export" not in handle.phases and "details" not in handle.phases


def test_a_cancel_seen_by_the_request_layer_is_a_result_not_an_error(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """İstek katmanı iptali bir isteğin ortasında FetchCancelled ile bildirir; servis onu sonuca çevirir."""
    details = FakeDetails({"17": ["a", "b"]})

    def cancelled_mid_request() -> None:
        raise request_ctx.FetchCancelled()

    details.during_fetch = cancelled_mid_request
    ctx = make_ctx(config, monkeypatch, details=details)
    spec = SyncSpec(mode="details", league_id=17)
    handle = RecordingHandle(spec)

    result = SyncService(ctx).run(spec, handle=handle)

    assert result.state == "cancelled" and details.exports == 0
    assert details.cache_events == ["begin", "end"] and details.refresh_listener is None
    assert _request_context_state() == (None, None, None)


# --- depolama hatası ---------------------------------------------------------------------------------


def test_a_storage_error_leaves_the_service_and_the_context_is_taken_back(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = FakeDetails({"17": ["a"]})
    error = StorageError.from_exception(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "/data/match_details/x")

    def full_disk() -> None:
        raise error

    details.during_fetch = full_disk
    ctx = make_ctx(config, monkeypatch, details=details)

    with pytest.raises(StorageError) as raised:
        SyncService(ctx).run(SyncSpec(mode="details", league_id=17))

    assert raised.value is error
    assert details.exports == 0 and details.cache_events == ["begin", "end"] and details.refresh_listener is None
    assert _request_context_state() == (None, None, None)


# --- export_all_csv ----------------------------------------------------------------------------------


def test_export_returns_the_path_of_the_file(config: ConfigManager, monkeypatch: pytest.MonkeyPatch) -> None:
    details = FakeDetails()
    ctx = make_ctx(config, monkeypatch, details=details)

    assert export_all_csv(ctx) == "/data/match_details/processed/all_matches_x.csv"
    assert details.exports == 1


@pytest.mark.parametrize("nothing", ["", None, []])
def test_export_returns_none_when_no_file_was_written(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch, nothing: Any
) -> None:
    details = FakeDetails()
    details.export_result = nothing

    assert export_all_csv(make_ctx(config, monkeypatch, details=details)) is None


def test_export_swallows_and_logs_an_error_like_the_menu_step_did(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Menü adımı her hatayı yutuyordu: dışa aktarma hatası işi düşürmez, iş "Completed" biter."""
    logged: List[str] = []
    monkeypatch.setattr(export_service.logger, "error", lambda message, *args: logged.append(message % args))
    details = FakeDetails({"17": ["a"]})
    details.export_result = StorageError.from_exception(OSError(errno.ENOSPC, "No space left on device"), "/data/x.csv")
    ctx = make_ctx(config, monkeypatch, details=details)

    assert export_all_csv(ctx) is None
    assert len(logged) == 1 and logged[0].startswith("CSV export failed: ")

    result = SyncService(ctx).run(SyncSpec(mode="details", league_id=17))
    assert result.state == "succeeded" and details.exports == 1  # işin CSV aşaması yok (EX-1)


def test_export_lets_a_cancel_through(config: ConfigManager, monkeypatch: pytest.MonkeyPatch) -> None:
    details = FakeDetails()
    details.export_result = request_ctx.FetchCancelled()

    with pytest.raises(request_ctx.FetchCancelled):
        export_all_csv(make_ctx(config, monkeypatch, details=details))


def test_export_of_a_real_data_dir_prints_no_menu_text(
    tmp_path: Path, config: ConfigManager, capsys: pytest.CaptureFixture[str]
) -> None:
    """Terminal menüsünün CSV adımı başlık ve sonuç satırı yazdırıyordu; servis yazdırmaz."""
    from src.i18n import get_i18n

    ctx = build_context(config, data_dir=str(tmp_path))

    assert export_all_csv(ctx) is None  # indirilmiş maç yok

    out = capsys.readouterr().out
    i18n = get_i18n()
    for key in ("headless_exporting_csv", "title_csv_conversion", "csv_created_success", "csv_created_error"):
        assert i18n.t(key) not in out, key


# --- web bağdaştırıcısı ------------------------------------------------------------------------------


@pytest.fixture
def web_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Web işini kendi thread'i olmadan, geçici bir iş deposu ve sahte bir servis bağlamıyla çalıştırır."""
    import src.web.fetch_job as fj
    from src.web.jobs import JobStore
    from src.web.routes.scrape import FetchRequest

    store = JobStore(str(tmp_path / "jobs.db"))
    monkeypatch.setattr(fj, "_job_store", store)
    monkeypatch.setattr(fj, "_refresh_scraper_state", lambda: store.snapshot())

    def run(details: FakeDetails, **payload: Any) -> Dict[str, Any]:
        ctx = make_ctx(fj.config_manager, monkeypatch, details=details)
        monkeypatch.setattr(fj, "build_context", lambda config_manager: ctx)
        request = FetchRequest(**payload)
        fj.run_fetch_job(store.create_running(request.model_dump()), request)
        return store.snapshot()

    return run


def test_payload_becomes_a_spec() -> None:
    import src.web.fetch_job as fj
    from src.web.routes.scrape import FetchRequest

    assert fj._spec_from_payload(FetchRequest()) == SyncSpec(mode="full", league_id=None, selections=())
    assert fj._spec_from_payload(FetchRequest(mode="details", league_id=17, selections=[])) == SyncSpec(
        mode="details", league_id=17
    )
    request = FetchRequest(
        selections=[
            {"league_id": 17, "season_ids": [2, 1, 2]},
            {"league_id": 8, "match_ids": [5]},
            {"league_id": 9, "season_ids": [], "match_ids": None},
        ]
    )
    assert fj._spec_from_payload(request).selections == (
        SyncSelection(league_id=17, season_ids=(2, 1, 2)),
        SyncSelection(league_id=8, match_ids=(5,)),
        SyncSelection(league_id=9),
    )


def test_the_first_job_log_line_names_the_target() -> None:
    import src.web.fetch_job as fj
    from src.web.routes.scrape import FetchRequest

    assert fj._summary(FetchRequest()) == "All Leagues"
    assert fj._summary(FetchRequest(league_id=17)) == "17"
    assert fj._summary(FetchRequest(league_id=17, selections=[{"league_id": 8}, {"league_id": 9}])) == (
        "2 targeted selection(s)"
    )


def test_web_job_prints_its_console_line_and_no_menu_text(
    web_job: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.i18n import get_i18n

    final = web_job(FakeDetails({"17": ["a"]}), mode="details", league_id=17)

    assert final["status"] == "Completed" and final["progress"] == 100
    assert final["log"][0] == "[Running] Starting fetch for 17"
    assert final["log"][-1] == "[Completed] Background Task Completed Successfully."
    assert not any("CSV" in line for line in final["log"])
    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if line.startswith("-->")] == [
        "--> Background Task Completed Successfully.",
    ]
    i18n = get_i18n()
    for key in ("headless_exporting_csv", "title_csv_conversion", "csv_created_success", "csv_created_error"):
        assert i18n.t(key) not in out, key


def test_web_job_does_not_leave_its_cancel_check_behind(web_job: Any) -> None:
    """Eskiden iptal kontrolü kurulup geri alınmıyordu; işi ana thread'de koşturan testlerde sonraki isteklere sızıyordu."""
    final = web_job(FakeDetails({"17": ["a"]}), mode="details", league_id=17)

    assert final["status"] == "Completed"
    assert _request_context_state() == (None, None, None)


def test_web_job_ends_cancelled_when_the_request_layer_sees_the_cancel(web_job: Any) -> None:
    details = FakeDetails({"17": ["a", "b", "c", "d"]})

    def cancelled_mid_request() -> None:
        raise request_ctx.FetchCancelled()

    details.during_fetch = cancelled_mid_request

    final = web_job(details, mode="details", league_id=17)

    assert final["status"] == "Cancelled" and final["is_running"] is False
    assert final["log"][-1] == "[Cancelled] Cancelled"
    assert details.exports == 0
    assert _request_context_state() == (None, None, None)


def test_web_job_writes_the_result_of_the_service(web_job: Any) -> None:
    details = FakeDetails({"17": ["a", "b"]}, failing=["b"])

    final = web_job(details, mode="details", league_id=17)

    assert final["status"] == "Completed"
    assert final["result"]["schedule_empty_seasons"] == 0
    assert final["result"]["failed"] == [{"match_id": "b", "league_id": 17}]
    assert final["result"]["details_done"] == 2 and final["result"]["breaker"] is None


# --- lig araması: API kökü ---------------------------------------------------------------------------

DEFAULT_BASE = "https://www.sofascore.com/api/v1"
OTHER_BASE = "https://api.sofascore.com/api/v1"


class _Response:
    def __init__(self, body: Any) -> None:
        self.status_code = 200
        self.reason = "OK"
        self.headers: Dict[str, str] = {}
        self.text = json.dumps(body)
        self._body = body

    def json(self) -> Any:
        return self._body


@pytest.fixture
def sent_urls(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """curl'e giden tam adresleri toplar; her isteğe tek sonuçlu bir arama yanıtı döner."""
    urls: List[str] = []
    body = {
        "results": [
            {"entity": {"id": 17, "name": "Premier League", "slug": "premier-league",
                        "category": {"name": "England", "sport": {"name": "Football"}}}}
        ]
    }

    def sync_get(url: str, **kwargs: Any) -> _Response:
        urls.append(url)
        return _Response(body)

    monkeypatch.setattr(transport.cffi_requests, "get", sync_get)
    monkeypatch.setattr(utils, "_sleep", lambda seconds: None)
    return urls


@pytest.mark.parametrize("base", [DEFAULT_BASE, OTHER_BASE])
def test_league_search_uses_the_api_base_of_the_client(
    sent_urls: List[str], monkeypatch: pytest.MonkeyPatch, base: str
) -> None:
    """Eskiden arama, API_BASE_URL ne olursa olsun varsayılan adrese gidiyordu (PR #48'in bıraktığı tek istek)."""
    from src.web.routes import leagues

    monkeypatch.setattr(utils, "API_BASE_URL", base)

    found = leagues._search_remote_leagues_sync("premier league/1")

    # Sorgu yolun parçasıdır ve tümüyle kodlanır ("/" dahil), önceki gibi
    assert sent_urls == [f"{base}/search/unique-tournaments/premier%20league%2F1"]
    assert [(league.id, league.name, league.country, league.sport) for league in found] == [
        (17, "Premier League", "England", "Football")
    ]


def test_the_league_route_module_no_longer_hard_codes_the_api_base() -> None:
    source = (SRC / "web" / "routes" / "leagues.py").read_text(encoding="utf-8")
    assert "sofascore.com" not in source
