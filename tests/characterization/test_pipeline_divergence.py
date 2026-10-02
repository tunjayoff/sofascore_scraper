"""
İki detay hattının ayrıştığı yerler: docs/design/02-services.md, bölüm 1.4'teki tablonun her satırı bir test.

  async hat: MatchDataFetcher.fetch_detail_ids → fetch_matches_batch_async (lig/sezon planları)
  sync hat:  MatchDataFetcher.fetch_matches_batch, fetch_match_data, refill_missing_match_slices
             (kimliğiyle seçilen maçlar, tek maç uç noktası, async hattın içinden refill/refresh)

Testler bugünkü davranışı olduğu gibi sabitler: aynı maçın iki hatta farklı sonuç vermesi burada
"beklenen"dir. Hatlar tek boru hattında birleştirilirken (plan: P13) her test, değişikliğin adı
PR metninde anılarak tek tek çevrilir.

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı gerçektir.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Sequence

import pytest

import detail_records
import src.utils as utils
from characterization import WORLD, pin_default_settings
from fakes.sofascore import REQUEST_LAYER, SITE_ROOT, FakeSofaScore
from src import breaker as request_breaker
from src import throttle
from src.match_data_fetcher import MatchDataFetcher

FINISHED = 9100001  # futbol, bitti, altı dilimi de var
FINISHED_2 = 9100003
NOT_STARTED = 9100004
LIVE = 9300001  # futbol, oynanıyor; statistics, lineups, incidents var
TENNIS = 9200001  # bitti; statistics, h2h, point-by-point var
SLICES = ["statistics", "team-streaks", "pregame-form", "h2h", "lineups", "incidents"]  # `required` dilimler, istek sırasıyla
FETCHER_PAUSES = "src.match_data_fetcher"  # bu modülün time.sleep / asyncio.sleep beklemeleri
BUDGET_WARM_UP = "src.throttle"  # oturum ısınmasının bütçe sırası (diğer isteklerinki istek katmanındadır)


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(path))
    return path


@pytest.fixture
def request_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], List[float]]]:
    """
    Ortak istek bütçesini (src/throttle.py) açan fonksiyon: `rate` istek/sn, durum dosyası bu teste özel.
    Döndürdüğü liste, bundan sonra bütçeden alınan her sıranın bekleme süresiyle (sn; 0 = hemen) dolar.
    """
    waits: List[float] = []
    reserve = throttle.reserve

    def spy() -> float:
        waits.append(reserve())
        return waits[-1]

    def enable(rate: str) -> List[float]:
        monkeypatch.setenv("REQUEST_RATE_LIMIT", rate)
        monkeypatch.setenv("SOFASCORE_THROTTLE_DIR", str(tmp_path / "throttle"))
        monkeypatch.setattr(throttle, "reserve", spy)
        throttle.reset_for_tests()
        return waits

    yield enable
    throttle.reset_for_tests()


def _fetcher(data_dir: Path) -> MatchDataFetcher:
    from src.web.routes.common import config_manager

    return MatchDataFetcher(config_manager, data_dir=str(data_dir))


def _run_async(md: MatchDataFetcher, ids: Sequence[int]) -> List[str]:
    """Async hat, web işinin lig planında çağırdığı gibi; başarısız sayılan maçları döndürür."""
    failed: List[str] = []
    md.fetch_detail_ids([str(i) for i in ids], failed_callback=failed.append)
    return failed


def _run_sync(md: MatchDataFetcher, ids: Sequence[int]) -> List[str]:
    """Sync hat, web işinin seçili maçlarda çağırdığı gibi; başarısız sayılan maçları döndürür."""
    failed: List[str] = []
    md.fetch_matches_batch(list(ids), failed_callback=failed.append)
    return failed


def _event_paths(event_id: int, slices: Sequence[str] = SLICES) -> List[str]:
    return [f"/event/{event_id}"] + [f"/event/{event_id}/{name}" for name in slices]


def _api_paths(fake: FakeSofaScore) -> List[str]:
    return [r.path for r in fake.requests if r.path != SITE_ROOT]


def _stored(md: MatchDataFetcher, event_id: int) -> Dict[str, Any]:
    """
    Kaydın hali, eski düzen dizininin dosyaları biçiminde: ad → içerik; kayıt yoksa {}. Kayıtlar ST-21'den beri
    Store'dadır (v3); hali Store'dan okunur (tests/detail_records.py `legacy_view`).
    """
    return detail_records.legacy_view(md.data_dir, event_id)


def _store_then_make_partial(fake: FakeSofaScore, md: MatchDataFetcher) -> None:
    """Diskte FINISHED'in bir dilimi eksik kaydı (ihtiyaç: refill), SofaScore'da ise maç artık "oynanıyor"."""
    assert _run_sync(md, [FINISHED]) == []
    detail_records.drop_slices(md.data_dir, FINISHED, "h2h")
    live = fake.event(FINISHED)
    live["status"] = {"code": 6, "description": "1st half", "type": "inprogress"}
    fake.add_event(live)
    fake.reset_log()


def test_row01_reached_from(fake: FakeSofaScore, data_dir: Path) -> None:
    """Hangi giriş noktası hangi hattı kullanır: toplu planlar async, tek tek seçilen maçlar sync."""
    import src.web.routes.matches as matches_routes

    md = _fetcher(data_dir)

    md.fetch_detail_ids([str(FINISHED)])  # web işi, lig/sezon planı (fetch_job.py)
    assert {r.via for r in fake.requests} == {"async"}

    # CLI headless ve terminal menüsü: özet CSV'lerden toplanan maçlar da async hatta
    summary = data_dir / "matches" / "17_Premier_League" / "61627_Premier_League_24_25_summary.csv"
    summary.parent.mkdir(parents=True)
    summary.write_text(f"match_id\n{FINISHED_2}\n", encoding="utf-8")
    fake.reset_log()
    assert md.fetch_all_match_details(league_id="17") is True
    assert {r.via for r in fake.requests} == {"async"}

    fake.reset_log()
    md.fetch_matches_batch([9100002])  # web işi, kimliğiyle seçilen maçlar
    assert matches_routes._fetch_single_match_sync("9100010") == {"status": "success", "match_id": "9100010"}
    assert md.fetch_match_details(TENNIS) is True  # terminal menüsü, tek maç
    assert {r.via for r in fake.requests} == {"sync"} and fake.sessions == []

    # Async hattın içinden: eksik dilim (refill) sync hatla, başka bir thread'de tamamlanır
    detail_records.drop_slices(md.data_dir, FINISHED, "h2h")
    fake.reset_log()
    assert _run_async(md, [FINISHED]) == []
    assert [r.label for r in fake.requests if r.path != SITE_ROOT] == [
        f"sync /event/{FINISHED} 200",
        f"sync /event/{FINISHED}/h2h 200",
    ]
    assert len(fake.sessions) == 1


def test_row02_slices_requested(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Async hat isteğe bağlı dilimleri de ister (teniste point_by_point); sync hat yalnızca `required` olanları."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")

    assert _run_async(in_async, [TENNIS]) == []
    async_paths = _api_paths(fake)
    fake.reset_log()
    assert _run_sync(in_sync, [TENNIS]) == []

    assert sorted(async_paths) == sorted(_event_paths(TENNIS, SLICES + ["point-by-point"]))
    assert fake.paths() == _event_paths(TENNIS)
    assert "point_by_point.json" in _stored(in_async, TENNIS)
    assert "point_by_point.json" not in _stored(in_sync, TENNIS)


def test_row03_unfinished_events(fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FETCH_ONLY_FINISHED yalnızca async hatta okunur: kapalıyken async hat oynanan maçı kaydeder, sync hat atlar."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")

    # Varsayılan (açık): iki hat da yalnızca /event ister ve maçı kaydetmez
    assert _run_async(in_async, [LIVE]) == [str(LIVE)]
    assert _api_paths(fake) == [f"/event/{LIVE}"]
    fake.reset_log()
    assert _run_sync(in_sync, [LIVE]) == [str(LIVE)]
    assert fake.paths() == [f"/event/{LIVE}"]
    assert _stored(in_async, LIVE) == {} and _stored(in_sync, LIVE) == {}

    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", False)
    fake.reset_log()
    assert _run_async(in_async, [LIVE]) == []
    assert sorted(_api_paths(fake)) == sorted(_event_paths(LIVE))
    assert sorted(_stored(in_async, LIVE)) == [
        "basic.json", "incidents.json", "lineups.json", "observation.json", "statistics.json",
    ]
    fake.reset_log()
    assert _run_sync(in_sync, [LIVE]) == [str(LIVE)]
    assert fake.paths() == [f"/event/{LIVE}"]
    assert _stored(in_sync, LIVE) == {}


def test_row04_event_failure(fake: FakeSofaScore, tmp_path: Path) -> None:
    """
    /event başarısız olursa: async hat maçı 3 kez dener, her denemede istek katmanı 2 istek atar (6 istek);
    sync hat hatayı yutar, maçı yeniden denemez, istek katmanı varsayılan sayıda (3) istek atar.
    """
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")
    fake.fail(f"/event/{FINISHED}", 500)

    assert _run_async(in_async, [FINISHED]) == [str(FINISHED)]
    assert _api_paths(fake) == [f"/event/{FINISHED}"] * 6
    assert fake.slept(REQUEST_LAYER) == [3.0, 3.0, 3.0]  # her maç denemesinde iki istek arası
    match_backoff = fake.slept(FETCHER_PAUSES)  # maç denemeleri arası: 1·2ⁿ sn + [0, 1) sn
    assert len(match_backoff) == 2 and 1.0 <= match_backoff[0] < 2.0 and 2.0 <= match_backoff[1] < 3.0
    assert in_async.last_status_counts.get("5xx") == 3

    fake.reset_log()
    assert _run_sync(in_sync, [FINISHED]) == [str(FINISHED)]
    assert fake.paths() == [f"/event/{FINISHED}"] * 3
    assert fake.slept(REQUEST_LAYER) == [3.0, 6.0]
    assert fake.slept(FETCHER_PAUSES) == []
    assert in_sync.last_status_counts.get("5xx") == 1


def test_row05_slice_retries(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Başarısız dilim: async hatta tek istek (max_retries=1), sync hatta istek katmanının varsayılanı (3)."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")
    statistics = f"/event/{FINISHED}/statistics"
    fake.fail(statistics, 500)

    assert _run_async(in_async, [FINISHED]) == []
    assert fake.count(statistics) == 1
    fake.reset_log()
    assert _run_sync(in_sync, [FINISHED]) == []
    assert fake.count(statistics) == 3

    # Sonuç iki hatta aynı: maç kaydedilir, dilim "yok" sayılmaz, hata not edilir
    for md in (in_async, in_sync):
        stored = _stored(md, FINISHED)
        assert "statistics.json" not in stored and "_unavailable.json" not in stored
        error = stored["_slice_status.json"]["statistics"]["error"]
        assert (error["reason"], error["status"], error["count"]) == ("5xx", 500, 1)


def test_row06_concurrency(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Async hatta maçlar ve dilimler eşzamanlı; sync hatta her istek bir öncekinin yanıtından sonra."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")

    assert _run_async(in_async, [FINISHED, FINISHED_2]) == []
    async_requests = fake.requests
    fake.reset_log()
    assert _run_sync(in_sync, [FINISHED, FINISHED_2]) == []

    order = [r.path for r in async_requests]
    # İkinci maç, birincinin dilimleri gelmeden başlar; bir maçın dilimleri birlikte uçuştadır
    assert order.index(f"/event/{FINISHED_2}") < order.index(f"/event/{FINISHED}/statistics")
    assert max(r.in_flight for r in async_requests) > 1
    assert fake.paths() == _event_paths(FINISHED) + _event_paths(FINISHED_2)
    assert {r.in_flight for r in fake.requests} == {1}


def test_row07_pacing(fake: FakeSofaScore, tmp_path: Path, request_budget: Callable[[str], List[float]]) -> None:
    """
    İki hat da artık kendi sabit beklemesini eklemez (PR #33'te kalktı): sync hatta maç başına 0,2 sn,
    async hatta 100'lük batch'ler arasındaki 1 sn (fetch_detail_ids'in dış döngüsü ve
    fetch_matches_batch_async'in iç döngüsü) yok. İstekleri aralayan tek şey ortak istek bütçesidir
    (src/throttle.py): her istek, iki hatta da, istek katmanında bütçeden sıra alır ve orada bekler.
    Testlerde bütçe kapalıdır (tests/conftest.py); burada ikinci yarıda açılır.
    (--refresh-only'nin maç başına 1 sn'si de kalktı: test_fetch_flows.py::test_refresh_only.)
    """
    md = _fetcher(tmp_path / "data")
    unknown = [str(n) for n in range(1, 102)]  # 101 maç, hepsi 404: iki batch

    # Bütçe kapalı: hiçbir hatta maçlar ya da batch'ler arasında bekleme yok
    assert throttle.configured_rate() == 0
    _run_sync(md, [FINISHED, FINISHED_2, NOT_STARTED])
    assert fake.slept(FETCHER_PAUSES) == []

    fake.reset_log()
    md.fetch_detail_ids(unknown)
    assert fake.slept(FETCHER_PAUSES) == []
    assert len(fake.sessions) == 2  # dıştaki 100'lük batch başına bir oturum

    fake.reset_log()
    asyncio.run(md.fetch_matches_batch_async(unknown))
    assert fake.slept(FETCHER_PAUSES) == []
    assert len(fake.sessions) == 1
    assert fake.slept(BUDGET_WARM_UP) == []  # kapalı bütçe kimseyi bekletmez

    # Bütçe açık (1 istek/sn): her istek bütçeden geçer, bekleme istek katmanındadır
    budget_waits = request_budget("1")
    fake.reset_log()
    _run_sync(_fetcher(tmp_path / "sync"), [FINISHED, FINISHED_2])
    waited = [seconds for seconds in budget_waits if seconds > 0]
    assert len(budget_waits) == len(fake.requests) == 14  # deneme başına bir sıra
    assert waited and all(seconds in fake.slept(REQUEST_LAYER) for seconds in waited)
    assert fake.slept(FETCHER_PAUSES) == []

    fake.reset_log()
    budget_waits.clear()
    assert _run_async(_fetcher(tmp_path / "async"), [FINISHED, FINISHED_2]) == []
    waited = [seconds for seconds in budget_waits if seconds > 0]
    assert len(budget_waits) == len(fake.requests) == 15  # oturum ısınması da bütçeden sıra alır
    assert waited and all(seconds in fake.slept(REQUEST_LAYER) + fake.slept(BUDGET_WARM_UP) for seconds in waited)
    assert fake.slept(FETCHER_PAUSES) == []


def test_row08_http_session(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Async hat ısıtılmış tek bir oturum (tek TLS profili) kullanır; sync hat oturumsuz, istek başına profil seçer."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")

    assert _run_async(in_async, [FINISHED]) == []
    async_requests, sessions = fake.requests, fake.sessions
    fake.reset_log()
    assert _run_sync(in_sync, [FINISHED]) == []

    assert len(sessions) == 1 and sessions[0].impersonate in utils.IMPERSONATE_PROFILES
    assert async_requests[0].path == SITE_ROOT  # ısınma: ana sayfa, API isteklerinden önce
    assert {r.session for r in async_requests} == {sessions[0].number}
    assert {r.impersonate for r in async_requests} == {None}  # profil oturumdan gelir

    assert fake.sessions == [] and SITE_ROOT not in fake.paths()
    assert all(r.impersonate in utils.IMPERSONATE_PROFILES for r in fake.requests)


def test_row09_refill_that_returns_none(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Refill vazgeçince (maç artık bitmiş görünmüyor) iki hat da tam çekime düşer ve /event'i ikinci kez ister."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")
    finished = fake.event(FINISHED)

    _store_then_make_partial(fake, in_sync)
    assert _run_sync(in_sync, [FINISHED]) == [str(FINISHED)]
    assert [r.label for r in fake.requests] == [f"sync /event/{FINISHED} 200"] * 2

    fake.add_event(finished)  # ikinci hat için baştan: SofaScore'da yeniden "bitti"
    _store_then_make_partial(fake, in_async)
    assert _run_async(in_async, [FINISHED]) == [str(FINISHED)]
    assert sorted(r.label for r in fake.requests if r.path != SITE_ROOT) == [
        f"async /event/{FINISHED} 200",  # tam çekim
        f"sync /event/{FINISHED} 200",  # refill
    ]

    for md in (in_async, in_sync):  # kayıt olduğu gibi kalır
        stored = _stored(md, FINISHED)
        assert stored["basic.json"]["status"]["type"] == "finished" and "h2h.json" not in stored


def test_row10_breaker_scope(fake: FakeSofaScore, data_dir: Path) -> None:
    """Toplu hatların (async ve sync) devre kesicisi vardır; tek maç uç noktasının istekleri kesicisizdir."""
    import src.web.routes.matches as matches_routes

    md = _fetcher(data_dir)
    fake.probe = lambda: request_breaker.current() is not None

    assert _run_async(md, [FINISHED]) == []
    assert {r.probe for r in fake.requests} == {True}

    fake.reset_log()
    assert _run_sync(md, [FINISHED_2]) == []
    assert {r.probe for r in fake.requests} == {True}

    fake.reset_log()
    matches_routes._fetch_single_match_sync("9100010")
    assert {r.probe for r in fake.requests} == {False}


def test_row11_not_finished_outcome(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Bitmemiş maç iki hatta da "başarısız maç" olarak bildirilir (hata yok, yalnızca indirilecek bir şey yok)."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")

    assert _run_async(in_async, [NOT_STARTED]) == [str(NOT_STARTED)]
    assert _run_sync(in_sync, [NOT_STARTED]) == [str(NOT_STARTED)]

    assert _api_paths(fake) == [f"/event/{NOT_STARTED}"] * 2
    assert _stored(in_async, NOT_STARTED) == {} and _stored(in_sync, NOT_STARTED) == {}
    assert in_async.last_status_counts == {"other": 1}  # async hat bunu "diğer hata" diye sayar
    assert in_sync.last_status_counts == {}


def test_row12_slice_markers(fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """"Yok" işaretleri iki hatta da yalnızca `required` dilimler için ve yalnızca bitmiş maçta tutulur."""
    in_async, in_sync = _fetcher(tmp_path / "async"), _fetcher(tmp_path / "sync")
    fake.add(f"/event/{TENNIS}/point-by-point", {"pointByPoint": []})  # isteğe bağlı dilim boş geliyor

    assert _run_async(in_async, [TENNIS]) == []
    assert _run_sync(in_sync, [TENNIS]) == []

    empty_required = {"incidents": 1, "lineups": 1, "pregame_form": 1, "team_streaks": 1}
    for md in (in_async, in_sync):
        stored = _stored(md, TENNIS)
        assert stored["_unavailable.json"] == empty_required  # point_by_point sayılmaz
        assert sorted(stored["_slice_status.json"]) == sorted(empty_required)
    assert _stored(in_async, TENNIS)["point_by_point.json"] == {"pointByPoint": []}

    # Bitmemiş maç (yalnızca async hat kaydedebilir, satır 3): boş gelen dilimler işaretlenmez
    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", False)
    assert _run_async(in_async, [LIVE]) == []
    stored = _stored(in_async, LIVE)
    assert "basic.json" in stored and "h2h.json" not in stored
    assert "_unavailable.json" not in stored and "_slice_status.json" not in stored
