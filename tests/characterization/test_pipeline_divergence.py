"""
Eski iki detay hattının ayrıştığı yerler: docs/design/02-services.md, bölüm 1.4'teki tablonun her satırı bir test.

  eski async hat: MatchDataFetcher.fetch_detail_ids → fetch_matches_batch_async (lig/sezon planları)
  eski sync hat:  MatchDataFetcher.fetch_matches_batch, fetch_match_data, refill_missing_match_slices
                  (kimliğiyle seçilen maçlar, tek maç uç noktası, async hattın içinden refill/refresh)

G-01 bu testlerde iki hattın farklı davranışını sabitlemişti. P13'ten beri iki giriş noktası da aynı boru hattına
(src/services/pipeline.py) gider; her test, eski farkın yerine iki yolun artık AYNI davrandığını ve bu davranışın
ne olduğunu sabitler (değişiklikler P13'ün PR metninde satır satır anılır). Satır 13 ve 14, FX-5'in bulduğu iki
farktır (boş sayılan "falsy" gövde, 404'ün iki nedeni).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı gerçektir.
"""
from __future__ import annotations

import asyncio
import json
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
SLICES = ["statistics", "team-streaks", "pregame-form", "h2h", "lineups", "incidents"]  # futbolun dilimleri, tablo sırasıyla
FETCHER_PAUSES = "src.match_data_fetcher"  # bu modülün time.sleep / asyncio.sleep beklemeleri
PIPELINE_PAUSES = "src.services.pipeline"  # boru hattının beklemeleri (yalnızca meşgul depoda)
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
    from src.web.deps import config_manager as _web_config
    config_manager = _web_config()

    return MatchDataFetcher(config_manager, data_dir=str(data_dir))


def _run_plan(md: MatchDataFetcher, ids: Sequence[int]) -> List[str]:
    """Eski async hattın giriş noktası (web işinin lig planı); başarısız sayılan maçları döndürür."""
    failed: List[str] = []
    md.fetch_detail_ids([str(i) for i in ids], failed_callback=failed.append)
    return failed


def _run_picked(md: MatchDataFetcher, ids: Sequence[int]) -> List[str]:
    """Eski sync hattın giriş noktası (web işinin seçili maçları); başarısız sayılan maçları döndürür."""
    failed: List[str] = []
    md.fetch_matches_batch(list(ids), failed_callback=failed.append)
    return failed


def _event_paths(event_id: int, slices: Sequence[str] = SLICES) -> List[str]:
    return [f"/event/{event_id}"] + [f"/event/{event_id}/{name}" for name in slices]


def _api_paths(fake: FakeSofaScore) -> List[str]:
    return [r.path for r in fake.requests if r.path != SITE_ROOT]


def _stored(md: MatchDataFetcher, event_id: int) -> Dict[str, Any]:
    """Kaydın hali, eski düzen dizininin dosyaları biçiminde (tests/detail_records.py `legacy_view`); kayıt yoksa {}."""
    return detail_records.legacy_view(md.data_dir, event_id)


def _manifest(md: MatchDataFetcher, event_id: int) -> Dict[str, Any]:
    path = detail_records.record_dir(md.data_dir, event_id) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _store_then_make_partial(fake: FakeSofaScore, md: MatchDataFetcher) -> None:
    """Diskte FINISHED'in bir dilimi eksik kaydı (ihtiyaç: refill), SofaScore'da ise maç artık "oynanıyor"."""
    assert _run_picked(md, [FINISHED]) == []
    detail_records.drop_slices(md.data_dir, FINISHED, "h2h")
    live = fake.event(FINISHED)
    live["status"] = {"code": 6, "description": "1st half", "type": "inprogress"}
    fake.add_event(live)
    fake.reset_log()


def test_row01_reached_from(fake: FakeSofaScore, data_dir: Path) -> None:
    """Her giriş noktası aynı boru hattına gider: ısıtılmış bir oturumla, eşzamanlı istekler (`async`)."""
    import src.web.api.legacy as matches_routes

    md = _fetcher(data_dir)

    md.fetch_detail_ids([str(FINISHED)])  # web işi, lig/sezon planı (fetch_job.py)
    assert {r.via for r in fake.requests} == {"async"} and len(fake.sessions) == 1

    # CLI ve web işi, takip edilen ligler: listelerden toplanan maçlar (SyncService'in detay aşaması)
    summary = data_dir / "matches" / "17_Premier_League" / "61627_Premier_League_24_25_summary.csv"
    summary.parent.mkdir(parents=True)
    summary.write_text(f"match_id\n{FINISHED_2}\n", encoding="utf-8")
    fake.reset_log()
    assert md.fetch_detail_ids(md.pending_detail_ids(md.collect_detail_match_ids("17") or [])) == 1
    assert {r.via for r in fake.requests} == {"async"}

    # Kimliğiyle seçilen maçlar, tek maç uç noktası, tek maçın eski yüzü: aynı yol, her biri kendi oturumuyla
    fake.reset_log()
    md.fetch_matches_batch([9100002])
    assert matches_routes._fetch_single_match_sync("9100010") == {"status": "success", "match_id": "9100010"}
    assert md.fetch_match_data(TENNIS) is not None
    assert {r.via for r in fake.requests} == {"async"} and len(fake.sessions) == 3

    # Eksik dilim (refill) de aynı oturumda tamamlanır: başka bir thread'e geçilmez
    detail_records.drop_slices(md.data_dir, FINISHED, "h2h")
    fake.reset_log()
    assert _run_plan(md, [FINISHED]) == []
    assert [r.label for r in fake.requests if r.path != SITE_ROOT] == [
        f"async /event/{FINISHED} 200",
        f"async /event/{FINISHED}/h2h 200",
    ]
    assert len(fake.sessions) == 1


def test_row02_slices_requested(fake: FakeSofaScore, tmp_path: Path) -> None:
    """İki giriş noktası da sporun bütün dilimlerini ister, isteğe bağlılar dahil (teniste point_by_point)."""
    in_plan, in_picked = _fetcher(tmp_path / "plan"), _fetcher(tmp_path / "picked")

    assert _run_plan(in_plan, [TENNIS]) == []
    plan_paths = _api_paths(fake)
    fake.reset_log()
    assert _run_picked(in_picked, [TENNIS]) == []

    expected = sorted(_event_paths(TENNIS, SLICES + ["point-by-point"]))
    assert sorted(plan_paths) == sorted(_api_paths(fake)) == expected
    assert "point_by_point.json" in _stored(in_plan, TENNIS)
    assert "point_by_point.json" in _stored(in_picked, TENNIS)


def test_row03_unfinished_events(fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ST-27: her durumdaki maç saklanır; FETCH_ONLY_FINISHED yalnızca okurken uygulanır. Ayar açık da olsa kapalı
    da olsa iki yol da oynanan maçı gövdesi gelen dilimleriyle kaydeder (P13'te açıkken atlanıyordu).
    """
    for only_finished in (True, False):
        monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", only_finished)
        in_plan = _fetcher(tmp_path / f"plan_{only_finished}")
        in_picked = _fetcher(tmp_path / f"picked_{only_finished}")
        for md in (in_plan, in_picked):
            fake.reset_log()
            assert (_run_plan if md is in_plan else _run_picked)(md, [LIVE]) == []
            assert sorted(_api_paths(fake)) == sorted(_event_paths(LIVE))
            assert sorted(_stored(md, LIVE)) == [
                "basic.json", "incidents.json", "lineups.json", "observation.json", "statistics.json",
            ]


def test_row04_event_failure(fake: FakeSofaScore, tmp_path: Path) -> None:
    """
    /event başarısız olursa iki yolda da yalnızca istek katmanı yeniden dener (MAX_RETRIES: 3 istek, aralarında
    3 ve 6 sn); maç başına ek deneme döngüsü ve bekleme yoktur. Maç başarısız sayılır.
    """
    in_plan, in_picked = _fetcher(tmp_path / "plan"), _fetcher(tmp_path / "picked")
    fake.fail(f"/event/{FINISHED}", 500)

    for md, run in ((in_plan, _run_plan), (in_picked, _run_picked)):
        fake.reset_log()
        assert run(md, [FINISHED]) == [str(FINISHED)]
        assert _api_paths(fake) == [f"/event/{FINISHED}"] * 3
        assert fake.slept(REQUEST_LAYER) == [3.0, 6.0]
        assert fake.slept(FETCHER_PAUSES) == [] and fake.slept(PIPELINE_PAUSES) == []
        assert md.last_status_counts.get("5xx") == 1


def test_row05_slice_retries(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Başarısız dilim de iki yolda istek katmanının varsayılanı kadar (3) istenir; sonuç ve hata kaydı aynıdır."""
    in_plan, in_picked = _fetcher(tmp_path / "plan"), _fetcher(tmp_path / "picked")
    statistics = f"/event/{FINISHED}/statistics"
    fake.fail(statistics, 500)

    assert _run_plan(in_plan, [FINISHED]) == []
    assert fake.count(statistics) == 3
    fake.reset_log()
    assert _run_picked(in_picked, [FINISHED]) == []
    assert fake.count(statistics) == 3

    # Maç kaydedilir, dilim "yok" sayılmaz, hata not edilir
    for md in (in_plan, in_picked):
        stored = _stored(md, FINISHED)
        assert "statistics.json" not in stored and "_unavailable.json" not in stored
        error = stored["_slice_status.json"]["statistics"]["error"]
        assert (error["reason"], error["status"], error["count"]) == ("5xx", 500, 1)


def test_row06_concurrency(fake: FakeSofaScore, tmp_path: Path) -> None:
    """İki yolda da maçlar ve bir maçın dilimleri eşzamanlıdır."""
    in_plan, in_picked = _fetcher(tmp_path / "plan"), _fetcher(tmp_path / "picked")

    for md, run in ((in_plan, _run_plan), (in_picked, _run_picked)):
        fake.reset_log()
        assert run(md, [FINISHED, FINISHED_2]) == []
        order = [r.path for r in fake.requests]
        # İkinci maç, birincinin dilimleri gelmeden başlar; bir maçın dilimleri birlikte uçuştadır
        assert order.index(f"/event/{FINISHED_2}") < order.index(f"/event/{FINISHED}/statistics")
        assert max(r.in_flight for r in fake.requests) > 1
        assert sorted(_api_paths(fake)) == sorted(_event_paths(FINISHED) + _event_paths(FINISHED_2))


def test_row07_pacing(fake: FakeSofaScore, tmp_path: Path, request_budget: Callable[[str], List[float]]) -> None:
    """
    Kendi sabit beklemesi olan yol yoktur (PR #33'te kalktı; 100'lük batch'ler de P13'te kalktı: bir çalıştırma
    tek oturumdur). İstekleri aralayan tek şey ortak istek bütçesidir (src/throttle.py): her istek, oturum
    ısınması dahil, bütçeden bir sıra alır ve orada bekler. Testlerde bütçe kapalıdır (tests/conftest.py); burada
    ikinci yarıda açılır. (--refresh-only'nin maç başına 1 sn'si de kalktı: test_fetch_flows.py::test_refresh_only.)
    """
    md = _fetcher(tmp_path / "data")
    unknown = [str(n) for n in range(1, 102)]  # 101 maç, hepsi 404

    # Bütçe kapalı: maçlar arasında bekleme yok
    assert throttle.configured_rate() == 0
    _run_picked(md, [FINISHED, FINISHED_2, NOT_STARTED])
    assert fake.slept(FETCHER_PAUSES) == [] and fake.slept(PIPELINE_PAUSES) == []

    fake.reset_log()
    md.fetch_detail_ids(unknown)
    assert fake.slept(FETCHER_PAUSES) == [] and fake.slept(PIPELINE_PAUSES) == []
    assert len(fake.sessions) == 1  # batch yok: çalıştırma başına bir oturum

    fake.reset_log()
    asyncio.run(md.fetch_matches_batch_async(unknown))
    assert fake.slept(FETCHER_PAUSES) == [] and fake.slept(PIPELINE_PAUSES) == []
    assert len(fake.sessions) == 1
    assert fake.slept(BUDGET_WARM_UP) == []  # kapalı bütçe kimseyi bekletmez

    # Bütçe açık (1 istek/sn): her istek bütçeden geçer, bekleme istek katmanındadır; iki yol aynı sayıda sıra alır
    budget_waits = request_budget("1")
    for md, run in ((_fetcher(tmp_path / "picked"), _run_picked), (_fetcher(tmp_path / "plan"), _run_plan)):
        fake.reset_log()
        budget_waits.clear()
        assert run(md, [FINISHED, FINISHED_2]) == []
        waited = [seconds for seconds in budget_waits if seconds > 0]
        assert len(budget_waits) == len(fake.requests) == 15  # 2 × 7 istek + oturum ısınması
        assert waited and all(seconds in fake.slept(REQUEST_LAYER) + fake.slept(BUDGET_WARM_UP) for seconds in waited)
        assert fake.slept(FETCHER_PAUSES) == [] and fake.slept(PIPELINE_PAUSES) == []


def test_row08_http_session(fake: FakeSofaScore, tmp_path: Path) -> None:
    """İki yol da ısıtılmış tek bir oturum (tek TLS profili) kullanır; ısınma API isteklerinden önce gelir."""
    for name, run in (("plan", _run_plan), ("picked", _run_picked)):
        fake.reset_log()
        assert run(_fetcher(tmp_path / name), [FINISHED]) == []
        sessions = fake.sessions
        assert len(sessions) == 1 and sessions[0].impersonate in utils.IMPERSONATE_PROFILES
        assert fake.requests[0].path == SITE_ROOT
        assert {r.session for r in fake.requests} == {sessions[0].number}
        assert {r.impersonate for r in fake.requests} == {None}  # profil oturumdan gelir


def test_row09_refill_of_a_match_that_is_no_longer_finished(fake: FakeSofaScore, tmp_path: Path) -> None:
    """
    ST-27: refill sırasında maç artık bitmiş görünmüyorsa da yeni hali saklanır ve eksik dilim bir kez istenir
    (P13'te refill vazgeçiyor, kayıt olduğu gibi kalıyordu). Yeni kayıt açıktır: planlayıcı ona bir şey
    planlamaz; liste maçı yeniden bitmiş gösterince bayatlar ve yenilenir.
    """
    in_plan, in_picked = _fetcher(tmp_path / "plan"), _fetcher(tmp_path / "picked")
    finished = fake.event(FINISHED)
    expected = [f"async /event/{FINISHED} 200", f"async /event/{FINISHED}/h2h 200"]

    _store_then_make_partial(fake, in_picked)
    assert _run_picked(in_picked, [FINISHED]) == []
    assert [r.label for r in fake.requests if r.path != SITE_ROOT] == expected

    fake.add_event(finished)  # ikinci yol için baştan: SofaScore'da yeniden "bitti"
    _store_then_make_partial(fake, in_plan)
    assert _run_plan(in_plan, [FINISHED]) == []
    assert [r.label for r in fake.requests if r.path != SITE_ROOT] == expected

    for md in (in_plan, in_picked):  # yeni hali saklanır; maç açık kayıttır
        stored = _stored(md, FINISHED)
        assert stored["basic.json"]["status"]["type"] == "inprogress" and "h2h.json" in stored
        assert md._needs_detail_fetch(str(FINISHED)) == "none"


def test_row10_breaker_scope(fake: FakeSofaScore, data_dir: Path) -> None:
    """Her yolun istekleri bir devre kesicinin altındadır, tek maç uç noktası dahil (kendi kesicisi)."""
    import src.web.api.legacy as matches_routes

    md = _fetcher(data_dir)
    fake.probe = lambda: request_breaker.current() is not None

    assert _run_plan(md, [FINISHED]) == []
    assert {r.probe for r in fake.requests} == {True}

    fake.reset_log()
    assert _run_picked(md, [FINISHED_2]) == []
    assert {r.probe for r in fake.requests} == {True}

    fake.reset_log()
    matches_routes._fetch_single_match_sync("9100010")
    assert {r.probe for r in fake.requests} == {True}


def test_row11_not_finished_outcome(fake: FakeSofaScore, tmp_path: Path) -> None:
    """
    ST-27: bitmemiş maç iki yolda da olduğu haliyle saklanır (P13'te atlanıyordu): başarısız sayılmaz, "yok"
    işaretleri tutulmaz.
    """
    in_plan, in_picked = _fetcher(tmp_path / "plan"), _fetcher(tmp_path / "picked")

    assert _run_plan(in_plan, [NOT_STARTED]) == []
    assert _run_picked(in_picked, [NOT_STARTED]) == []

    assert sorted(_api_paths(fake)) == sorted(_event_paths(NOT_STARTED) * 2)
    for md in (in_plan, in_picked):
        stored = _stored(md, NOT_STARTED)
        assert stored["basic.json"]["status"]["type"] == "notstarted"
        assert "_unavailable.json" not in stored and "_slice_status.json" not in stored
    # Sayımlar yapılan istekleri anlatır: ön maç evresinde olmayan dilimlerin 404'leri (iki yolda aynı)
    assert in_plan.last_status_counts == in_picked.last_status_counts == {"404": 6}


def test_row12_slice_markers(fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    "Yok" işaretleri bitmiş maçta istenen her dilim için tutulur, isteğe bağlılar dahil (point_by_point); bitmemiş
    maçta hiçbiri için tutulmaz.
    """
    in_plan, in_picked = _fetcher(tmp_path / "plan"), _fetcher(tmp_path / "picked")
    fake.add(f"/event/{TENNIS}/point-by-point", {"pointByPoint": []})  # isteğe bağlı dilim boş geliyor

    assert _run_plan(in_plan, [TENNIS]) == []
    assert _run_picked(in_picked, [TENNIS]) == []

    empty = {"incidents": 1, "lineups": 1, "pregame_form": 1, "team_streaks": 1, "point_by_point": 1}
    for md in (in_plan, in_picked):
        stored = _stored(md, TENNIS)
        assert stored["_unavailable.json"] == empty
        assert sorted(stored["_slice_status.json"]) == sorted(empty)
        assert stored["point_by_point.json"] == {"pointByPoint": []}

    # Bitmemiş maç ("yalnızca bitmiş maçlar" kapalı): boş gelen dilimler işaretlenmez
    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", False)
    for md, run in ((in_plan, _run_plan), (in_picked, _run_picked)):
        assert run(md, [LIVE]) == []
        stored = _stored(md, LIVE)
        assert "basic.json" in stored and "h2h.json" not in stored
        assert "_unavailable.json" not in stored and "_slice_status.json" not in stored


@pytest.mark.parametrize("body", [0, False, ""])
def test_row13_falsy_body_is_read_by_the_rule(fake: FakeSofaScore, tmp_path: Path, body: Any) -> None:
    """
    Yanlış türde "falsy" bir gövde (0, false, "") her yolda dilimin kuralıyla okunur: başarısız istek, neden
    `parse`; "yok" sayılmaz ve yazılmaz (FX-5'in bulduğu fark: eski async hat bunu boş sayıyordu).
    """
    fake.add(f"/event/{FINISHED}/statistics", body)
    for name, run in (("plan", _run_plan), ("picked", _run_picked)):
        md = _fetcher(tmp_path / name)
        assert run(md, [FINISHED]) == []
        stored = _stored(md, FINISHED)
        assert "statistics.json" not in stored and "statistics" not in stored.get("_unavailable.json", {})
        assert stored["_slice_status.json"]["statistics"]["error"]["reason"] == "parse"


def test_row14_a_404_has_one_reason(fake: FakeSofaScore, tmp_path: Path) -> None:
    """Bir dilimin 404'ü her yolda aynı nedenle ("404") sayılır (eski sync hat "empty" yazıyordu)."""
    reasons = {}
    for name, run in (("plan", _run_plan), ("picked", _run_picked)):
        md = _fetcher(tmp_path / name)
        assert run(md, [9100002]) == []  # pregame-form: 404
        reasons[name] = _manifest(md, 9100002)["slices"]["pregame_form"]["empty"]["reason"]
    assert reasons == {"plan": "404", "picked": "404"}
