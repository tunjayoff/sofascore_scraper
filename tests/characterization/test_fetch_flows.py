"""
İndirme akışlarının goldenları: her akışın SofaScore'a attığı istekler (sırasıyla) ve yazdığı dosyalar.

Sabitlenen akışlar:
  - web işi (`POST /api/v1/jobs`un gövdesi, sofascore_scraper/web/api/v1/jobs.py `_run_sync`): tam güncelleme (lig,
    tüm ligler, sezon seçimi), yalnızca detay, kimliğiyle seçilen maçlar; ayrıca tam güncellemenin ikinci ve
    üçüncü çalıştırması. 3.0'a kadar aynı servisi 2.x'in `/api/fetch` işi çalıştırıyordu; yol 3.1'de kalktı (P30),
    goldenlar v1'in işiyle yeniden üretildi (iş günlüğünün ilk satırı ve sonucun `failed_listings` alanı)
  - yalnızca yenileme (main.py --refresh-only'nin çağırdığı sıra)
  - "yok" işaretlerinin yeniden denetimi (main.py --recheck-unavailable'ın çağırdığı fonksiyon)

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı gerçektir.
Beklenen çıktılar fixtures/fetch/*.golden.json'dadır (yeniden üretmek: UPDATE_GOLDENS=1). Bu dosya
bugünkü davranışı olduğu gibi kaydeder; doğru olduğunu söylemez. Bilinen tutarsızlıklar
test_pipeline_divergence.py'de satır satır sabitlenir.

Maç detayları ST-21'den beri Store'a, v3 düzenine yazılır (`v3/events/.../<id>/`: manifest ve `.json.gz` yükler);
kayıtların durumunu kuran adımlar (dilim silmek, gözlemi geriye almak) tests/detail_records.py ile yapılır.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List

import curl_cffi.requests as cffi_requests
import pytest

import detail_records
from catalog_index import index_event
import legacy_writer
import sofascore_scraper.utils as utils
from characterization import WORLD, assert_golden, pin_default_settings, snapshot_tree
from fakes.sofascore import REQUEST_LAYER, SITE_ROOT, FakeSofaScore
from web_job import run_sync_job
from sofascore_scraper.web import deps
from sofascore_scraper import breaker as request_breaker
from sofascore_scraper.exceptions import APIError, NetworkError, RateLimitError, ResourceNotFoundError
from sofascore_scraper.match_data_fetcher import MatchDataFetcher

LEAGUE = 17
SEASON_WEEKS = 61627  # haftalık turlar
SEASON_PAGES = 52186  # events/last + events/next sayfaları
# 24/25: 1. tur 9100001 + 9100002 (pregame-form 404, lineups boş), 2. tur 9100003 + 9100004 (başlamadı)
# 23/24: 9100010. Listelerde olmayanlar: 9200001 (tenis, bitti), 9300001 (futbol, oynanıyor)
NOT_STARTED = 9100004
TENNIS = 9200001

RunJob = Callable[..., Dict[str, Any]]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Boş bir veri dizini; ConfigManager.get_data_dir() onu döndürür."""
    path = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(path))
    return path


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def run_job(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunJob:
    """
    Web işini (`POST /api/v1/jobs`un gövdesi) kendi thread'i olmadan, geçici bir iş deposuyla çalıştırır; son iş
    durumunu döndürür.
    """
    from sofascore_scraper.store import JobStore

    store = JobStore(str(tmp_path / "jobs.db"))
    monkeypatch.setattr(deps, "job_store", lambda: store)
    monkeypatch.setattr(deps, "refresh_job_mirror", lambda: store.snapshot())
    return lambda **payload: run_sync_job(store, payload)


def _fetcher(data_dir: Path) -> MatchDataFetcher:
    from sofascore_scraper.web.deps import config_manager as _web_config
    config_manager = _web_config()

    return MatchDataFetcher(config_manager, data_dir=str(data_dir))


def _make_provisional(data_dir: Path, fake: FakeSofaScore, event_id: int) -> None:
    """Kaydı geçici yapar: başlangıçtan 2 sa sonra gözlenmiş gibi (yenileme penceresi açık, son gözlem eski)."""
    start = fake.event(event_id)["startTimestamp"]
    detail_records.set_observed_at(data_dir, event_id, dt.datetime.fromtimestamp(start + 2 * 3600, dt.timezone.utc))


def _change_score(fake: FakeSofaScore, event_id: int, home: int) -> None:
    """SofaScore maçın skorunu sonradan düzeltmiş gibi: /event/{id} yeni skoru döndürür."""
    event = fake.event(event_id)
    event["homeScore"].update(current=home, display=home, normaltime=home)
    event["changes"]["changeTimestamp"] += 3600
    fake.add_event(event)


def _job_summary(final: Dict[str, Any]) -> Dict[str, Any]:
    """İş durumunun kararlı kısmı (zamanlar, iş kimliği ve çevrilen kart metni hariç)."""
    keys = (
        "status", "progress", "matches_total", "matches_done", "matches_failed",
        "schedule_empty_seasons", "circuit_breaker_triggered", "log", "result",
    )
    summary = {key: final.get(key) for key in keys}
    summary["phases"] = (final.get("detail") or {}).get("phases")
    return summary


def _markers(data_dir: Path) -> Dict[str, Any]:
    """
    "Yok" sayaçları ve hata kayıtları (eski düzende `_unavailable.json` / `_slice_status.json`), Store'dan: maç →
    dilim → {kesin sayım, doğrulanmamış sayım, hata nedeni}; işareti olmayan maç yer almaz.
    """
    from sofascore_scraper.store import EventQuery, open_store

    store = open_store(data_dir)
    out: Dict[str, Any] = {}
    for row in store.events.iter(EventQuery(has_details=True)):
        marks = detail_records.slice_marks(data_dir, row.id)
        if marks:
            out[str(row.id)] = marks
    return out


def _delete_record(data_dir: Path, event_id: int) -> None:
    """Maçın kaydını siler (eski düzende dizinini silmenin karşılığı): Store'un silmesi."""
    from sofascore_scraper.store import open_store

    assert open_store(data_dir).events.delete(event_id)


def _as_legacy_record(data_dir: Path, event_id: int, *, drop: Any = (), unavailable: Any = None) -> Path:
    """
    Saklanan maçı önceki bir sürümün yazdığı eski düzen kaydına çevirir: yükleri Store'dan okunur, v3 kaydı silinir
    ve eski düzen yazıcısının dondurulmuş kopyasıyla (tests/legacy_writer.py) yazılır; `drop` dilimleri yazılmaz,
    `unavailable` eski sürümün `_unavailable.json`'ıdır (doğrulanmamış sayımlar). Maç dizinini döndürür.
    """
    from sofascore_scraper.store import open_store

    store = open_store(data_dir)
    payloads = store.events.payloads(event_id)
    match_data = {("basic" if key == "event" else key): payload for key, payload in payloads.items() if key not in drop}
    _delete_record(data_dir, event_id)
    match_dir = Path(legacy_writer.save_legacy(data_dir, event_id, match_data))
    if unavailable is not None:
        (match_dir / "_unavailable.json").write_text(json.dumps(unavailable), encoding="utf-8")
        index_event(data_dir, event_id, match_dir)
    return match_dir


# --- sahte taşıyıcının kendisi ---------------------------------------------------------------

def test_fake_serves_canned_payloads_and_records_every_url_in_order(fake: FakeSofaScore) -> None:
    async def fetch_async() -> Any:
        async with utils.create_session_async() as session:
            return await utils.make_api_request_async(session, f"/unique-tournament/{LEAGUE}/seasons")

    absolute = utils.make_api_request("https://www.sofascore.com/api/v1/event/9100001")
    relative = utils.make_api_request("/event/9100001/h2h")
    seasons = asyncio.run(fetch_async())

    assert absolute["event"]["id"] == 9100001
    assert relative["teamDuel"]["homeWins"] == 3
    assert [s["id"] for s in seasons["seasons"]] == [SEASON_WEEKS, SEASON_PAGES]
    assert [r.label for r in fake.requests] == [
        "sync /event/9100001 200",
        "sync /event/9100001/h2h 200",
        "async https://www.sofascore.com/ 200",  # oturum ısınması
        "async /unique-tournament/17/seasons 200",
    ]
    assert fake.canonical_log() == [
        "sync /event/9100001 200",
        "sync /event/9100001/h2h 200",
        {"concurrent": ["async /unique-tournament/17/seasons 200", "async https://www.sofascore.com/ 200"]},
    ]


def test_fake_answers_404_for_an_unknown_path(fake: FakeSofaScore) -> None:
    assert utils.make_api_request("/event/1") is None
    with pytest.raises(ResourceNotFoundError):
        utils.make_api_request("/event/1", raise_on_failure=True)
    assert fake.paths() == ["/event/1", "/event/1"]  # 404 yeniden denenmez


@pytest.mark.parametrize(
    ("status", "error", "backoff"),
    [
        (403, APIError, [10.0, 20.0]),
        (429, RateLimitError, [5.0, 10.0]),
        (500, APIError, [3.0, 6.0]),
    ],
)
def test_fake_injects_http_errors_through_the_real_request_layer(
    fake: FakeSofaScore, status: int, error: type, backoff: List[float]
) -> None:
    fake.fail("/event/9100001", status)
    started = time.monotonic()

    with pytest.raises(error) as raised:
        utils.make_api_request("/event/9100001", raise_on_failure=True)

    assert raised.value.status_code == status
    assert [r.outcome for r in fake.requests] == [str(status)] * 3  # MAX_RETRIES varsayılanı
    assert fake.slept(REQUEST_LAYER) == backoff  # geri çekilmeler kaydedilir ama beklenmez
    assert time.monotonic() - started < 2


def test_fake_injects_a_fault_a_limited_number_of_times(fake: FakeSofaScore) -> None:
    fake.fail("/event/*/statistics", 502, times=1)

    data = utils.make_api_request("/event/9100001/statistics")

    assert data is not None and "statistics" in data
    assert [r.outcome for r in fake.requests] == ["502", "200"]


def test_fake_injects_timeouts_and_connection_errors(fake: FakeSofaScore) -> None:
    fake.timeout("/event/9100001")
    fake.disconnect("/event/9100002")

    with pytest.raises(NetworkError) as timed_out:
        utils.make_api_request("/event/9100001", raise_on_failure=True)
    with pytest.raises(NetworkError) as disconnected:
        utils.make_api_request("/event/9100002", raise_on_failure=True)

    assert request_breaker.failure_kind(timed_out.value) == "timeout"
    assert request_breaker.failure_kind(disconnected.value) == "network"
    assert [r.outcome for r in fake.requests] == ["timeout"] * 3 + ["network"] * 3


def test_fake_skips_only_the_application_sleeps(fake: FakeSofaScore) -> None:
    """Uygulama kodu (sofascore_scraper.*) dışındaki beklemelere dokunulmaz: test ve kütüphane kodu gerçekten bekler."""
    started = time.monotonic()

    time.sleep(0.02)
    asyncio.run(asyncio.sleep(0.02))

    assert time.monotonic() - started >= 0.03
    assert fake.sleeps == []


def _sleep_as(module: str, seconds: float) -> None:
    """`module` adlı modülün içinden çağrılmış gibi time.sleep (sahte, çağıranı modül adından tanır)."""
    exec("import time\ntime.sleep(seconds)", {"__name__": module, "seconds": seconds})


def test_fake_leaves_storage_waits_real(fake: FakeSofaScore) -> None:
    """
    Depolama katmanı (sofascore_scraper.store) SofaScore'u değil dosya sistemini bekler (Windows'ta meşgul hedefe
    yeniden deneme): beklemesi atlanmaz ve kaydedilmez. Diğer uygulama modüllerininki atlanır.
    """
    started = time.monotonic()
    _sleep_as("sofascore_scraper.match_data_fetcher", 30.0)
    assert time.monotonic() - started < 2
    assert [(s.source, s.seconds) for s in fake.sleeps] == [("sofascore_scraper.match_data_fetcher", 30.0)]

    fake.reset_log()
    started = time.monotonic()
    _sleep_as("sofascore_scraper.store.files", 0.05)
    assert time.monotonic() - started >= 0.03  # saat çözünürlüğü kaba olabilir (Windows: ~16 ms)
    assert fake.sleeps == []


def test_fake_world_and_log_round_trip_through_json(tmp_path: Path) -> None:
    """Dünya ve hatalar JSON'dan kurulabilir, kayıt JSON'a yazılabilir: alt süreçte çalışan testler için."""
    source = FakeSofaScore.from_file(WORLD)
    source.fail("/event/9100001", 429, times=1, headers={"Retry-After": "7"})
    clone = FakeSofaScore.from_dict(json.loads(json.dumps(source.to_dict())))

    with clone:
        data = utils.make_api_request("/event/9100001")
    clone.save_log(tmp_path / "log.json")
    saved = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))

    assert data is not None and data["event"]["id"] == 9100001
    assert saved["canonical"] == ["sync /event/9100001 429", "sync /event/9100001 200"]
    assert [r["outcome"] for r in saved["requests"]] == ["429", "200"]
    assert saved["sleeps"][0] == {"source": REQUEST_LAYER, "seconds": 7.0, "kind": "sync"}  # Retry-After


def test_fake_restores_the_transport_on_uninstall() -> None:
    original_get, original_session = cffi_requests.get, utils.AsyncSession
    original_sleeps = (utils._sleep, utils._asleep, time.sleep, asyncio.sleep)

    with FakeSofaScore.from_file(WORLD):
        assert cffi_requests.get is not original_get

    assert (cffi_requests.get, utils.AsyncSession) == (original_get, original_session)
    assert (utils._sleep, utils._asleep, time.sleep, asyncio.sleep) == original_sleeps


# --- web işi ---------------------------------------------------------------------------------

# Tam güncellemenin üç çağrılış biçimi: aynı istekler ve aynı dosyalar, yalnızca iş günlüğü farklı.
# 99999 SofaScore'un listesinde yok: iş onu indirilebilir sezona (yeniden eskiye ikinci) çevirir.
FULL_UPDATE_PAYLOADS: Dict[str, Dict[str, Any]] = {
    "league": {"mode": "full", "league_id": LEAGUE},
    "all_leagues": {"mode": "full"},
    "selected_seasons": {"mode": "full", "selections": [{"league_id": LEAGUE, "season_ids": [SEASON_WEEKS, 99999]}]},
}


def test_web_job_full_update(
    fake: FakeSofaScore, run_job: RunJob, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sezon listesi → maç programı (haftalık turlar ve sayfalı liste) → detaylar → CSV."""
    runs: Dict[str, Dict[str, Any]] = {}
    for name, payload in FULL_UPDATE_PAYLOADS.items():
        data_dir = tmp_path / name  # her biçim boş bir veri dizininde
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        fake.reset_log()
        final = run_job(**payload)
        runs[name] = {"requests": fake.canonical_log(), "files": snapshot_tree(data_dir), "job": _job_summary(final)}

    for name, run in runs.items():
        assert run["requests"] == runs["league"]["requests"], name
        assert run["files"] == runs["league"]["files"], name
    assert_golden("job_full", {
        "requests": runs["league"]["requests"],
        "files": runs["league"]["files"],
        "jobs": {name: run["job"] for name, run in runs.items()},
    })


def test_web_job_full_update_run_again(fake: FakeSofaScore, run_job: RunJob, data_dir: Path) -> None:
    """
    İkinci çalıştırma (P14, tazelik kuralı): sezon listesi ve program tazedir (listing.SEASON_LIST_TTL_SECONDS,
    SCHEDULE_TTL_SECONDS), hiçbiri yeniden istenmez; boş gelen dilimler bir kez daha denenir. Üçüncüde hiç istek
    kalmaz. Süreler dolduktan sonraki çalıştırma: test_web_job_full_update_past_the_listing_ttl.
    """
    run_job(mode="full", league_id=LEAGUE)

    fake.reset_log()
    second = run_job(mode="full", league_id=LEAGUE)
    second_log = fake.canonical_log()
    fake.reset_log()
    third = run_job(mode="full", league_id=LEAGUE)

    assert_golden("job_full_rerun", {
        "second_run": {"requests": second_log, "job": _job_summary(second)},
        "third_run": {"requests": fake.canonical_log(), "job": _job_summary(third)},
        "markers": _markers(data_dir),
    })


def test_web_job_full_update_past_the_listing_ttl(
    fake: FakeSofaScore, run_job: RunJob, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Tazelik süreleri dolduysa: sezon listesi, tur listesi ve sayfalı program yeniden istenir; diskteki turlardan
    tamamlanmış olan hiç, tamamlanmamış olan ROUND_CACHE_TTL_SECONDS içinde istenmez (P14'ten önceki her
    çalıştırma böyleydi).
    """
    from sofascore_scraper.services import listing

    run_job(mode="full", league_id=LEAGUE)
    run_job(mode="full", league_id=LEAGUE)  # boş gelen dilimler ikinci kez: detay isteği kalmaz
    monkeypatch.setattr(listing, "SEASON_LIST_TTL_SECONDS", 0)
    monkeypatch.setattr(listing, "SCHEDULE_TTL_SECONDS", 0)
    fake.reset_log()

    final = run_job(mode="full", league_id=LEAGUE)

    assert sorted(fake.paths()) == sorted([
        SITE_ROOT, f"/unique-tournament/{LEAGUE}/seasons", SITE_ROOT,
        f"/unique-tournament/{LEAGUE}/season/{SEASON_WEEKS}/rounds", SITE_ROOT,
        f"/unique-tournament/{LEAGUE}/season/{SEASON_PAGES}/rounds",
        f"/unique-tournament/{LEAGUE}/season/{SEASON_PAGES}/events/last/0",
        f"/unique-tournament/{LEAGUE}/season/{SEASON_PAGES}/events/next/0",
    ])
    assert final["status"] == "Completed" and final["schedule_empty_seasons"] == 0


def test_web_job_with_a_refused_season_list_is_partial(fake: FakeSofaScore, run_job: RunJob, data_dir: Path) -> None:
    """
    P14: çekilemeyen sezon listesi "sezon yok" değildir: başarısız bir iş birimidir, iş günlüğüne yazılır ve iş
    `partial` biter. Bir programın başarısız turu da öyle.
    """

    def state(final: Dict[str, Any]) -> Any:
        """İşin bitiş durumu (`finished` olayı; eski durum metni `partial` için de "Completed"dır)."""
        events = deps.job_store().read_events(final["job_id"])
        return next(e["data"]["state"] for e in events if e["type"] == "finished")

    fake.fail(f"/unique-tournament/{LEAGUE}/seasons", 403)
    final = run_job(mode="full", league_id=LEAGUE)
    assert "[Running] The season list of league 17 could not be fetched (403)." in final["log"]
    assert state(final) == "partial"

    fake.clear_faults()
    fake.fail(f"/unique-tournament/{LEAGUE}/season/{SEASON_WEEKS}/events/round/2", 500)
    final = run_job(mode="full", league_id=LEAGUE)
    assert ("[Running] The match list of league 17, season 61627 could not be fetched completely (5xx)."
            in final["log"])
    assert final["schedule_empty_seasons"] == 0
    assert state(final) == "partial"


def test_web_job_details_only(fake: FakeSofaScore, run_job: RunJob, data_dir: Path) -> None:
    """Yalnızca detay: program istenmez; diskteki özetlerden eksik (full), kısmi (refill) ve geçici (refresh) maçlar."""
    run_job(mode="full", league_id=LEAGUE)
    detail_records.drop_slices(data_dir, 9100001, "statistics")  # kısmi → refill
    _delete_record(data_dir, 9100003)  # kayıt yok → full
    _make_provisional(data_dir, fake, 9100010)  # geçici → refresh
    _change_score(fake, 9100010, home=3)
    # 9100002: ilk çalıştırmada iki dilimi boş geldi → refill

    fake.reset_log()
    final = run_job(mode="details", league_id=LEAGUE)

    assert_golden("job_details_league", {
        "requests": fake.canonical_log(),
        "files": snapshot_tree(data_dir),
        "job": _job_summary(final),
    })


def test_web_job_explicit_match_ids(fake: FakeSofaScore, run_job: RunJob, data_dir: Path) -> None:
    """
    Kimliğiyle seçilen maçlar: lig planlarıyla aynı boru hattı (tek ısıtılmış oturum, eşzamanlı istekler);
    program ve özet dosyaları olmadan çalışır. Bitmemiş maç atlanır, başarısız sayılmaz.
    """
    final = run_job(mode="details", selections=[
        {"league_id": LEAGUE, "match_ids": [9100001, 9100002, NOT_STARTED]},
        {"league_id": 2361, "match_ids": [TENNIS]},
    ])

    assert len(fake.sessions) == 1
    assert_golden("job_explicit_match_ids", {
        "requests": fake.canonical_log(),
        "files": snapshot_tree(data_dir),
        "job": _job_summary(final),
    })


def test_web_job_runs_are_idempotent(fake: FakeSofaScore, run_job: RunJob, data_dir: Path) -> None:
    """
    Yeniden çalıştırma (docs/design/02-services.md 3.5): aynı işi arka arkaya çalıştırmak ikinci kez hiçbir detay
    isteği yapmaz; oturum da açılmaz. Kimliğiyle seçilen tam maçlarda bu ilk tekrardadır. Lig planında boş gelen
    dilimler (9100002) "yok" sayılmak için bir kez daha istenir, ardından o da durur.
    """
    picked = {"mode": "details", "selections": [{"league_id": LEAGUE, "match_ids": [9100001, 9100003]}]}
    league = {"mode": "details", "league_id": LEAGUE}
    runs: Dict[str, Any] = {}

    run_job(**picked)
    fake.reset_log()
    runs["picked_again"] = {"requests": fake.canonical_log(), "job": _job_summary(run_job(**picked))}

    run_job(mode="full", league_id=LEAGUE)
    for name in ("league_second", "league_third"):
        fake.reset_log()
        final = run_job(**league)
        runs[name] = {"requests": fake.canonical_log(), "job": _job_summary(final)}

    assert runs["picked_again"]["requests"] == [] and runs["league_third"]["requests"] == []
    assert fake.sessions == []
    assert_golden("job_idempotency", runs)


# --- yalnızca yenileme -----------------------------------------------------------------------

def test_refresh_only(fake: FakeSofaScore, data_dir: Path) -> None:
    """
    main.py --refresh-only'nin sırası: yenilenecekleri bul, her biri için yalnızca /event iste.
    Maçlar arasında sabit bekleme yok (eski 1 sn, PR #33'te kalktı): `pause_seconds` boş. Kalan tek
    bekleme istek katmanınındır: yanıt alınan her istekten sonraki WAIT_TIME ve, açıksa, ortak istek
    bütçesinin sırası (sofascore_scraper/throttle.py; testlerde kapalı: tests/conftest.py).
    """
    md = _fetcher(data_dir)
    md.fetch_matches_batch([9100001, 9100003, 9100010])
    for event_id in (9100001, 9100003, 9100010):
        _make_provisional(data_dir, fake, event_id)
    _change_score(fake, 9100003, home=2)  # skor düzeltildi
    fake.remove("/event/9100010")  # artık 404
    fake.reset_log()

    md.begin_job_cache()
    try:
        ids = md.refresh_due_ids(league_id=None)
        stats = md.refresh_matches(ids)
    finally:
        md.end_job_cache()

    request_waits = fake.slept(REQUEST_LAYER)
    assert all(0.2 <= seconds <= 0.7 for seconds in request_waits)  # WAIT_TIME_MIN + [0, WAIT_TIME_MAX]

    assert_golden("refresh_only", {
        "due_ids": ids,
        "stats": stats,
        "requests": fake.canonical_log(),
        "pause_seconds": fake.slept("sofascore_scraper.match_data_fetcher"),
        "request_layer_waits": len(request_waits),
        "files": {
            path: content
            for path, content in snapshot_tree(data_dir).items()
            if path.endswith(("basic.json", "observation.json", "score_changes.jsonl", "event.json.gz",
                              "manifest.json")) or path.startswith("changes/")
        },
    })


# --- "yok" işaretlerinin yeniden denetimi ----------------------------------------------------

def test_recheck_unavailable(fake: FakeSofaScore, run_job: RunJob, data_dir: Path) -> None:
    """İşaretleri geri almak istek atmaz; geri alınan dilimler sonraki indirmede yeniden istenir."""
    run_job(mode="full", league_id=LEAGUE)
    run_job(mode="details", league_id=LEAGUE)  # 9100002'nin iki dilimi ikinci kez boş: kesin "yok"
    # Eski sürümden kalma kayıt: 9100001 eski düzende, statistics dilimi doğrulanmadan "yok" sayılmış
    _as_legacy_record(data_dir, 9100001, drop=("statistics",), unavailable={"statistics": 2})
    md = _fetcher(data_dir)
    result: Dict[str, Any] = {"markers_before": _markers(data_dir)}

    fake.reset_log()
    result["default"] = md.reset_unavailable_markers(league_id=LEAGUE)
    result["default_again"] = md.reset_unavailable_markers(league_id=LEAGUE)
    result["markers_after_default"] = _markers(data_dir)
    assert fake.requests == []

    run_job(mode="details", league_id=LEAGUE)
    result["requests_after_default"] = fake.canonical_log()

    fake.reset_log()
    result["all"] = md.reset_unavailable_markers(league_id=LEAGUE, include_confirmed=True)
    result["markers_after_all"] = _markers(data_dir)
    assert fake.requests == []

    run_job(mode="details", league_id=LEAGUE)
    result["requests_after_all"] = fake.canonical_log()

    assert_golden("recheck_unavailable", result)
