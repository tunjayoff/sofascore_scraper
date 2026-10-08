"""
İndirilen bir dilimin gövdesi beklenen biçimde değilse (plan maddesi FX-5): başarısız istektir, "boş" yanıt
değil.

Kural (PR #25): yalnızca kesin bir "veri yok" yanıtı (HTTP 404 ya da içinde veri olmayan 200) dilimin o
maçta "yok" sayılmasına doğru sayılır; gerisi başarısızlıktır, not edilir ve sonraki çalıştırmada yeniden
istenir. sofascore_scraper/slices.py'deki kurallar toplam olmadan önce okunamayan gövdede hata fırlatırdı: async hatta
bu hata başarısız dilime dönüşüyordu, sync hatta ise maçın tamamını (toplu indirmede işin tamamını)
düşürüyordu. Kurallar artık üç yanıt verir (veri var, veri yok, okunamadı) ve çekici okunamayan gövdeyi
iki hatta da başarısız / "parse" olarak bildirir. P13'ten beri iki giriş noktası (lig planı ve kimliğiyle seçilen
maçlar) aynı boru hattıdır (sofascore_scraper/services/pipeline.py); testler ikisini de sınamaya devam eder.

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı ve çekici gerçektir.
Veriyi çekici Store'a yazar (plan maddesi ST-21), testler yalnızca okur: kaydın hali eski düzenin dosyaları
biçiminde sorulur (tests/detail_records.py `legacy_view`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Sequence, Tuple

import pytest

import detail_records
from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sofascore_scraper import breaker as request_breaker
from sofascore_scraper import bridge_health
from detail_fetch import Details
from sofascore_scraper.services.detail_phase import UNAVAILABLE_AFTER_ATTEMPTS, DetailPhase
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SliceOutcome
from sofascore_scraper.store import open_store

# tests/characterization/fixtures/fetch/world.json
FINISHED = 9100001  # futbol, bitti, altı `required` dilimi de dolu
TENNIS = 9200001  # bitti; statistics, h2h ve point-by-point var
REQUIRED = ["statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents"]
FETCHER_PAUSES = "sofascore_scraper.services.detail_phase"  # detay aşamasının maç denemeleri arasındaki beklemeleri

# (dilim, yolun son parçası, okunamayan gövde): sofascore_scraper.slices.slice_body_state bunlara BODY_MALFORMED der
MALFORMED: List[Tuple[str, str, Any]] = [
    ("statistics", "statistics", "abc"),  # nesne beklenen yerde metin
    ("statistics", "statistics", {"statistics": {"period": "ALL"}}),  # liste beklenen yerde nesne
    ("h2h", "h2h", {"teamDuel": ["x"]}),
    ("team_streaks", "team-streaks", ["x"]),
]
MALFORMED_IDS = ["statistics-text", "statistics-object", "h2h-list", "team_streaks-list"]

Run = Callable[[DetailPhase, Sequence[int]], List[str]]


def _run_async(md: DetailPhase, ids: Sequence[int]) -> List[str]:
    """Lig/sezon planları: DetailPhase.fetch. Başarısız maçları döndürür."""
    failed: List[str] = []
    md.fetch([str(i) for i in ids], failed=failed.append)
    return failed


def _run_sync(md: DetailPhase, ids: Sequence[int]) -> List[str]:
    """Kimliğiyle seçilen maçlar: DetailPhase.fetch_selected. Başarısız maçları döndürür."""
    failed: List[str] = []
    md.fetch_selected(list(ids), failed=failed.append)
    return failed


# İki giriş noktası da boru hattının ısıtılmış oturumuyla ister (`async`); sonraki çalıştırmalarda kayıt "refill"
# ihtiyacındadır ve yalnızca /event ile eksik dilim istenir.
PATHS = [pytest.param(_run_async, "async", id="async"), pytest.param(_run_sync, "async", id="sync")]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    bridge_health.reset()
    yield
    bridge_health.reset()


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
def breaker() -> Iterator[request_breaker.CircuitBreaker]:
    """İşin devre kesicisi: çekici bağlamdakini kullanır, test sayaçlarını okur."""
    own = request_breaker.CircuitBreaker()
    token = request_breaker.activate(own)
    try:
        yield own
    finally:
        request_breaker.deactivate(token)


def _fetcher(data_dir: Path) -> DetailPhase:
    from sofascore_scraper.web.deps import config_manager as _web_config

    return DetailPhase(open_store(str(data_dir)), _web_config())


def _stored(md: DetailPhase, event_id: int) -> Dict[str, Any]:
    """Kaydın hali, eski düzen dizininin dosyaları biçiminde (Store'dan): ad → içerik; kayıt yoksa {}."""
    return detail_records.legacy_view(md.store.data_dir, event_id)


def _need(md: DetailPhase, match_id: str) -> str:
    """Maçın şimdiki ihtiyacı, katalogdan (işin önbelleğine bakmadan)."""
    return Details(store=md.store).need(match_id)


def _expected(md: DetailPhase, event_id: int) -> List[str]:
    """Bu maçta hâlâ beklenen `required` dilimler ("yok" sayılanlar hariç)."""
    details = Details(store=md.store)
    assert details.location(str(event_id)) is not None
    return details.expected_keys(event_id, "football")


def _saved_outcomes(md: DetailPhase, monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, SliceOutcome]]:
    """Boru hattının her kayıtta Store.events.put'a verdiği dilim sonuçları, `event` hariç (kayıt yine gerçek)."""
    seen: List[Dict[str, SliceOutcome]] = []
    events = md.store.events
    put = events.put

    def spy(event_id: int, outcomes: Any, **kwargs: Any) -> Any:
        seen.append({key: outcome for key, outcome in dict(outcomes).items() if key != "event"})
        return put(event_id, outcomes, **kwargs)

    monkeypatch.setattr(events, "put", spy)
    return seen


def _slice_path(event_id: int, segment: str) -> str:
    return f"/event/{event_id}/{segment}"


# --- okunamayan gövde: başarısız istek --------------------------------------------------------


@pytest.mark.parametrize("run,via", PATHS)
@pytest.mark.parametrize("key,segment,body", MALFORMED, ids=MALFORMED_IDS)
def test_malformed_body_is_a_failed_slice(
    fake: FakeSofaScore, data_dir: Path, monkeypatch: pytest.MonkeyPatch,
    breaker: request_breaker.CircuitBreaker, run: Run, via: str, key: str, segment: str, body: Any,
) -> None:
    """
    Bitmiş bir maçın `required` dilimi okunamayan bir gövdeyle yanıtlanırsa: sonuç başarısız / "parse"tır,
    gövde diske yazılmaz, "yok" sayımı ve işareti oluşmaz, hata not edilir, maçın diğer dilimleri kaydedilir
    ve kayıt eksik (refill) kalır. Devre kesiciye başarısızlık bildirilmez.
    """
    md = _fetcher(data_dir)
    outcomes = _saved_outcomes(md, monkeypatch)
    path = _slice_path(FINISHED, segment)
    fake.add(path, body)

    assert run(md, [FINISHED]) == []  # maç başarısız sayılmaz: yalnızca bir dilimi eksik

    assert {r.via for r in fake.requests if r.path != SITE_ROOT} == {via}
    assert fake.count(path) == 1  # yanıt geldi: istek katmanı yeniden denemez
    assert len(outcomes) == 1 and sorted(outcomes[0]) == sorted(REQUIRED)
    outcome = outcomes[0][key]
    assert (outcome.status, outcome.reason, outcome.http_status, outcome.data) == (SLICE_FAILED, "parse", None, None)
    assert all(o.status == SLICE_OK for k, o in outcomes[0].items() if k != key)

    stored = _stored(md, FINISHED)
    others = [f"{k}.json" for k in REQUIRED if k != key]
    assert sorted(stored) == sorted(["basic.json", "observation.json", "_slice_status.json"] + others)
    status = stored["_slice_status.json"]
    assert list(status) == [key] and list(status[key]) == ["error"]  # "empty" sayımı yok
    error = status[key]["error"]
    assert (error["reason"], error["status"], error["count"]) == ("parse", None, 1)

    assert key in _expected(md, FINISHED)
    assert _need(md, str(FINISHED)) == "refill"
    # İstek yanıt aldı; okunamayan gövde engellenme belirtisi değildir
    assert (breaker.failures, breaker.counts(), breaker.tripped) == (0, {}, False)


@pytest.mark.parametrize("run,via", PATHS)
def test_repeated_malformed_body_never_becomes_unavailable(
    fake: FakeSofaScore, data_dir: Path, breaker: request_breaker.CircuitBreaker, run: Run, via: str
) -> None:
    """
    Aynı okunamayan gövde kaç çalıştırma gelirse gelsin dilim "yok" sayılmaz: her çalıştırmada yeniden
    istenir (bir kez), _unavailable.json oluşmaz, yalnızca hata sayacı artar. Düzgün yanıt geldiğinde dilim
    kaydedilir ve hata kaydı silinir.
    """
    md = _fetcher(data_dir)
    path = _slice_path(FINISHED, "statistics")
    fake.add(path, "abc")
    runs = UNAVAILABLE_AFTER_ATTEMPTS + 2

    for attempt in range(1, runs + 1):
        fake.reset_log()
        assert run(md, [FINISHED]) == []
        assert fake.count(path) == 1, attempt  # yeniden istendi; maç düzeyinde yeniden deneme yok
        assert fake.slept(FETCHER_PAUSES) == []
        if attempt > 1:
            # Kayıt "refill": yalnızca /event ve eksik dilim istenir
            assert sorted(r.label for r in fake.requests if r.path != SITE_ROOT) == [
                f"async /event/{FINISHED} 200", f"async {path} 200",
            ]
        stored = _stored(md, FINISHED)
        assert "_unavailable.json" not in stored and "statistics.json" not in stored
        entry = stored["_slice_status.json"]["statistics"]
        assert list(entry) == ["error"] and (entry["error"]["reason"], entry["error"]["count"]) == ("parse", attempt)
        assert "statistics" in _expected(md, FINISHED)
        assert _need(md, str(FINISHED)) == "refill"

    assert (breaker.failures, breaker.counts(), breaker.tripped) == (0, {}, False)

    world = FakeSofaScore.from_file(WORLD)
    fake.add(path, world.routes[path])
    fake.reset_log()
    assert run(md, [FINISHED]) == []
    stored = _stored(md, FINISHED)
    assert stored["statistics.json"] == world.routes[path]
    assert "_slice_status.json" not in stored and "_unavailable.json" not in stored
    assert _need(md, str(FINISHED)) != "refill"


@pytest.mark.parametrize("run,via", PATHS)
def test_malformed_body_does_not_advance_an_existing_marker(
    fake: FakeSofaScore, data_dir: Path, run: Run, via: str
) -> None:
    """Bir kesin "boş" yanıttan sonra gelen okunamayan gövdeler sayımı ilerletmez: dilim beklenmeye devam eder."""
    md = _fetcher(data_dir)
    path = _slice_path(FINISHED, "statistics")
    fake.add(path, {"statistics": []})  # 200, içinde veri yok: kesin yanıt
    assert run(md, [FINISHED]) == []
    assert _stored(md, FINISHED)["_unavailable.json"] == {"statistics": 1}

    fake.add(path, {"statistics": "abc"})
    for attempt in range(1, UNAVAILABLE_AFTER_ATTEMPTS + 2):
        assert run(md, [FINISHED]) == []
        stored = _stored(md, FINISHED)
        assert stored["_unavailable.json"] == {"statistics": 1}
        entry = stored["_slice_status.json"]["statistics"]
        assert entry["empty"]["count"] == 1
        assert (entry["error"]["reason"], entry["error"]["count"]) == ("parse", attempt)
        assert stored["statistics.json"] == {"statistics": []}  # önceki kesin yanıtın gövdesi; bozuk gövde yazılmadı
        assert "statistics" in _expected(md, FINISHED)
        assert _need(md, str(FINISHED)) == "refill"


# --- kesin "boş" yanıt: eskisi gibi sayılır ----------------------------------------------------


@pytest.mark.parametrize("run,via", PATHS)
def test_real_empty_answer_still_counts_towards_unavailable(
    fake: FakeSofaScore, data_dir: Path, monkeypatch: pytest.MonkeyPatch, run: Run, via: str
) -> None:
    """
    İçinde veri olmayan 200 ve 404 kesin yanıttır: "empty" sonuç, _unavailable.json'da ve
    _slice_status.json'da sayılır; UNAVAILABLE_AFTER_ATTEMPTS yanıttan sonra dilim beklenmez ve istenmez.
    """
    md = _fetcher(data_dir)
    outcomes = _saved_outcomes(md, monkeypatch)
    statistics, lineups = _slice_path(FINISHED, "statistics"), _slice_path(FINISHED, "lineups")
    fake.add(statistics, {"statistics": []})
    fake.remove(lineups)  # 404

    assert run(md, [FINISHED]) == []
    first = outcomes[0]
    assert (first["statistics"].status, first["statistics"].reason) == (SLICE_EMPTY, "empty")
    assert first["statistics"].data == {"statistics": []}
    # 404'ün nedeni her yolda "404"tür (P13'ten önce sync hat "empty" yazıyordu)
    assert (first["lineups"].status, first["lineups"].reason, first["lineups"].data) == (SLICE_EMPTY, "404", None)
    stored = _stored(md, FINISHED)
    assert stored["_unavailable.json"] == {"statistics": 1, "lineups": 1}
    assert {k: sorted(v) for k, v in stored["_slice_status.json"].items()} == {
        "statistics": ["empty"], "lineups": ["empty"],
    }
    assert [stored["_slice_status.json"][k]["empty"]["count"] for k in ("statistics", "lineups")] == [1, 1]
    assert stored["statistics.json"] == {"statistics": []} and "lineups.json" not in stored
    assert _need(md, str(FINISHED)) == "refill"

    assert run(md, [FINISHED]) == []
    stored = _stored(md, FINISHED)
    assert stored["_unavailable.json"] == {"statistics": UNAVAILABLE_AFTER_ATTEMPTS, "lineups": UNAVAILABLE_AFTER_ATTEMPTS}
    assert [stored["_slice_status.json"][k]["empty"]["count"] for k in ("statistics", "lineups")] == [2, 2]
    expected = _expected(md, FINISHED)
    assert "statistics" not in expected and "lineups" not in expected
    assert _need(md, str(FINISHED)) != "refill"

    fake.reset_log()
    assert run(md, [FINISHED]) == []
    assert fake.count(statistics) == 0 and fake.count(lineups) == 0  # artık istenmez


# --- point_by_point: boş liste "veri yok"tur ---------------------------------------------------


def test_empty_point_by_point_is_no_data_on_the_async_path(
    fake: FakeSofaScore, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    {"pointByPoint": []} kesin "veri yok" yanıtıdır (eskiden veri sayılırdı); gövde yine sonuçta durur ve
    diske yazılır. P13'ten beri isteğe bağlı dilimin de "yok" sayımı tutulur (tamlık hesabına yine girmez).
    """
    md = _fetcher(data_dir)
    outcomes = _saved_outcomes(md, monkeypatch)
    fake.add(_slice_path(TENNIS, "point-by-point"), {"pointByPoint": []})

    assert _run_async(md, [TENNIS]) == []

    outcome = outcomes[0]["point_by_point"]
    assert (outcome.status, outcome.reason, outcome.data) == (SLICE_EMPTY, "empty", {"pointByPoint": []})
    stored = _stored(md, TENNIS)
    assert stored["point_by_point.json"] == {"pointByPoint": []}
    assert stored["_unavailable.json"]["point_by_point"] == 1
    assert list(stored["_slice_status.json"]["point_by_point"]) == ["empty"]


def test_malformed_point_by_point_is_failed_on_the_async_path(
    fake: FakeSofaScore, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Okunamayan point_by_point gövdesi başarısızdır ve yazılmaz; dolu liste veridir."""
    md = _fetcher(data_dir)
    outcomes = _saved_outcomes(md, monkeypatch)
    path = _slice_path(TENNIS, "point-by-point")
    points = fake.routes[path]
    fake.add(path, {"pointByPoint": "abc"})

    assert _run_async(md, [TENNIS]) == []
    outcome = outcomes[0]["point_by_point"]
    assert (outcome.status, outcome.reason, outcome.data) == (SLICE_FAILED, "parse", None)
    assert "point_by_point.json" not in _stored(md, TENNIS)

    other = _fetcher(data_dir.parent / "other")
    seen = _saved_outcomes(other, monkeypatch)
    fake.add(path, points)
    assert _run_async(other, [TENNIS]) == []
    assert seen[0]["point_by_point"].status == SLICE_OK
    assert _stored(other, TENNIS)["point_by_point.json"] == points


def test_point_by_point_answers_for_picked_matches(fake: FakeSofaScore, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Kimliğiyle seçilen maç da isteğe bağlı dilimi ister (P13'ten önce sync hat istemezdi); dilim isteği aynı üç
    yanıtı verir.
    """
    path = _slice_path(TENNIS, "point-by-point")
    points = fake.routes[path]
    answers = []
    for name, body in (("full", points), ("empty", {"pointByPoint": []}), ("malformed", {"pointByPoint": "abc"})):
        md = _fetcher(tmp_path / name)
        seen = _saved_outcomes(md, monkeypatch)
        fake.add(path, body)
        assert _run_sync(md, [TENNIS]) == []
        answers.append(seen[0]["point_by_point"])

    full, empty, malformed = answers
    assert (full.status, full.data) == (SLICE_OK, points)
    assert (empty.status, empty.reason, empty.data) == (SLICE_EMPTY, "empty", {"pointByPoint": []})
    assert (malformed.status, malformed.reason, malformed.data) == (SLICE_FAILED, "parse", None)
    assert {r.via for r in fake.requests} == {"async"}


# --- "falsy" gövde de kurala sorulur ----------------------------------------------------------------


@pytest.mark.parametrize("body", [0, False, ""], ids=["zero", "false", "empty-text"])
@pytest.mark.parametrize("run,via", PATHS)
def test_falsy_malformed_body_is_a_failed_slice_on_every_path(
    fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Run, via: str, body: Any
) -> None:
    """
    0 / false / "" gibi okunamayan bir statistics gövdesi her yolda kurala sorulur: başarısız / "parse", "yok"
    sayılmaz. P13'ten önce async hat "falsy" her gövdeyi (`if data:`) kurala sormadan kesin "boş" sayıyordu.
    """
    fake.add(_slice_path(FINISHED, "statistics"), body)

    md = _fetcher(tmp_path / "data")
    outcomes = _saved_outcomes(md, monkeypatch)
    assert run(md, [FINISHED]) == []
    outcome = outcomes[0]["statistics"]
    assert (outcome.status, outcome.reason, outcome.data) == (SLICE_FAILED, "parse", None)
    assert "_unavailable.json" not in _stored(md, FINISHED)
