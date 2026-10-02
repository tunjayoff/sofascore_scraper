"""
Veri bütünlüğü: dilim isteklerinin tipli sonucu ve "bu dilim bu maçta yok" işaretleri.

Sabitlenen kurallar:
  - Yalnızca kesin yanıt (HTTP 404 ya da içinde veri olmayan 200) "yok" sayılır. Başarısız istek
    (403/429/5xx/zaman aşımı/ağ/bozuk yanıt) sayılmaz, nedeni ve zamanıyla dilimin hata kaydına
    yazılır, sonraki çalıştırmada yeniden istenir ve devre kesiciyi besler. Async ve sync yollar aynı.
  - Eski sürümlerden kalan işaretler kendiliğinden sıfırlanmaz; --recheck-unavailable ile yeniden
    denetime açılır (kesin yanıtla doğrulanmış olanlar korunur).

Sayaçlar ve hata kayıtları Store'dadır (plan maddesi ST-21; eski düzende `_unavailable.json` ve
`_slice_status.json`): testler onları `Store.events.slice(...)` ile okur. Eski sürümün işaret dosyaları olan
kayıtlar tests/legacy_writer.py ile eski düzende kurulur.

Gerçek ağ yok: istek katmanının taşıyıcısı (oturumun `get`'i) ya da kendisi sahte. P13'ten beri her yol aynı
boru hattıdır (src/services/pipeline.py): eski "async yol" ve "sync yol" testleri aynı kodu iki giriş noktasından
(tam çekim ve refill) sınar; oturumun ısınma isteği sahte oturumda yoktur.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import legacy_writer
import src.utils as utils
from src.exceptions import APIError, DataParsingError, NetworkError, RateLimitError, ResourceNotFoundError
from src.match_data_fetcher import (DETAIL_SLICE_KEYS, SLICE_EMPTY, SLICE_FAILED, SLICE_OK,
                                    UNAVAILABLE_AFTER_ATTEMPTS, UNAVAILABLE_FILE, MatchDataFetcher, SliceOutcome)
from src.store import SliceInfo, open_store
from src.store import api as store_api

MID = "4242"
BASE = "https://www.sofascore.com/api/v1"
EVENT = f"{BASE}/event/{MID}"
SLICE_PATHS = {
    "statistics": "/statistics",
    "team_streaks": "/team-streaks",
    "pregame_form": "/pregame-form",
    "h2h": "/h2h",
    "lineups": "/lineups",
    "incidents": "/incidents",
}
CFG = {"max_retries": 3, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}

# İçinde veri olan yanıtlar (match_detail_slice_present bunları "var" sayar)
PRESENT = {
    "statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "shots"}]}]}]},
    "team_streaks": {"general": [{"name": "Wins", "value": "3"}]},
    "pregame_form": {"homeTeam": {"form": ["W"]}, "awayTeam": {"form": ["L"]}},
    "h2h": {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}},
    "lineups": {"home": {"players": [{"player": {"id": 1}}]}, "away": {"players": []}},
    "incidents": {"incidents": [{"incidentType": "goal"}]},
}


def _basic(mid: str = MID) -> dict:
    return {
        "id": int(mid),
        "tournament": {"id": 900, "name": "Cup", "uniqueTournament": {"id": 77, "name": "Cup"},
                       "category": {"sport": {"slug": "football"}}},
        "season": {"id": 5, "name": "Cup 26", "year": "26"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": 1, "name": "A"},
        "awayTeam": {"id": 2, "name": "B"},
        "homeScore": {"current": 1},
        "awayScore": {"current": 0},
        "startTimestamp": 1790000000,
    }


def _fetcher(tmp_path, threshold: int = 1000) -> MatchDataFetcher:
    cfg = MagicMock()
    cfg.get_rate_limit_threshold_consecutive.return_value = threshold
    cfg.get_rate_limit_threshold_ratio.return_value = 2.0  # oran kuralı kapalı
    cfg.get_server_error_threshold_consecutive.return_value = 1000
    cfg.get_max_concurrent.return_value = 1
    return MatchDataFetcher(cfg, data_dir=str(tmp_path))


def _info(f: MatchDataFetcher, key: str, mid: str = MID) -> SliceInfo:
    """Dilimin Store'daki durumu: sayaçlar, hata kaydı, yük."""
    return open_store(f.data_dir).events.slice(int(mid), key)


def _counts(f: MatchDataFetcher, mid: str = MID) -> Dict[str, int]:
    """"Yok" sayımları (kesin + doğrulanmamış), sayımı olan dilimler: eski `_unavailable.json`'ın karşılığı."""
    infos = open_store(f.data_dir).events.slices(int(mid))
    return {i.key: i.empty_count + i.unverified_empty_count for i in infos
            if i.empty_count + i.unverified_empty_count}


def _errors(f: MatchDataFetcher, mid: str = MID) -> Dict[str, str]:
    """Hata kaydı olan dilimler → neden."""
    return {i.key: i.error.reason for i in open_store(f.data_dir).events.slices(int(mid)) if i.error is not None}


def _legacy_dir(mid: str = MID) -> str:
    return f"match_details/77_Cup/season_Cup_26/{mid}"


def _slice_of(url: str) -> str:
    """URL'den dilim anahtarı ("" = /event/{id})."""
    tail = url.split(f"/event/{MID}", 1)[1] if f"/event/{MID}" in url else url.rsplit("/", 1)[-1]
    return next((k for k, p in SLICE_PATHS.items() if tail == p), "")


class Resp:
    def __init__(self, code: int, body: Any = None, text: str = "", bad_json: bool = False):
        self.status_code = code
        self.reason = "x"
        self.headers: Dict[str, str] = {}
        self._body = body
        self.text = text
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("Expecting value")
        return self._body


@contextlib.contextmanager
def _request_layer():
    """Gerçek istek katmanı, beklemesiz ve proxy'siz."""
    async def no_asleep(_sec):
        return None

    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils, "_asleep", side_effect=no_asleep), \
            patch.object(utils, "_sleep", side_effect=lambda _sec: None):
        yield


def _curl(basic: dict, slices: Dict[str, Any], calls: List[str]):
    """
    cffi_requests.get'in sahtesi: /event → basic; dilimler `slices`'tan (Resp, istisna ya da gövde).
    Tabloda olmayan dilim 404 döner.
    """
    def fake_get(url, **_kw):
        calls.append(url)
        key = _slice_of(url)
        if not key:
            return Resp(200, {"event": basic})
        answer = slices.get(key, Resp(404))
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Resp) else Resp(200, answer)

    return fake_get


def _async_api(basic: dict, slices: Dict[str, Any], calls: List[str]):
    """make_api_request_async'in sahtesi: dilim başına gövde ya da fırlatılacak tipli hata."""
    async def fake(session, url, max_retries=None, **_kw):
        calls.append(url)
        key = _slice_of(url)
        if not key:
            return {"event": basic}
        answer = slices.get(key, {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fake


def _session_of(fake_get: Any):
    """`create_session_async`'in sahtesi: ısınmasız bir oturum; `get`i verilen (senkron ya da eşyordam) işlevdir."""
    @contextlib.asynccontextmanager
    async def session_ctx():
        async def get(url, **kw):
            answer = fake_get(url, **kw)
            return await answer if asyncio.iscoroutine(answer) else answer

        session = MagicMock()
        session.get = AsyncMock(side_effect=get)
        yield session

    return session_ctx


@contextlib.contextmanager
def _curl_layer(basic: dict, slices: Dict[str, Any], calls: List[str]):
    """Gerçek istek katmanı, sahte oturumla (`_curl`'ün yanıtları)."""
    with _request_layer(), patch("src.utils.create_session_async", _session_of(_curl(basic, slices, calls))):
        yield


def _fetch_async(f: MatchDataFetcher, slices: Dict[str, Any], basic: dict | None = None) -> List[str]:
    """Maçın tam çekimi; dilim yanıtları istek katmanının yerine geçen sahteden (tipli hatalar olduğu gibi)."""
    calls: List[str] = []
    with patch("src.utils.make_api_request_async", new=_async_api(basic or _basic(), slices, calls)), \
            patch("src.utils.create_session_async", _session_of(lambda url, **kw: None)):
        f.fetch_match_data(MID)
    return calls


TRANSIENT = [
    ("429", RateLimitError(status_code=429, url="/x"), 429),
    ("5xx", RateLimitError(status_code=503, url="/x"), 503),
    ("403", APIError("HTTP 403 Forbidden", status_code=403), 403),
    ("5xx", APIError("HTTP 500", status_code=500), 500),
    ("timeout", NetworkError("İstek başarısız: curl: (28) Operation timed out"), None),
    ("network", NetworkError("İstek başarısız: curl: (7) Failed to connect"), None),
    ("parse", DataParsingError("JSON ayrıştırma hatası"), None),
]


# --- tipli sonuç ---------------------------------------------------------------------------

def test_outcome_from_error_separates_not_found_from_failure():
    assert SliceOutcome.from_error(ResourceNotFoundError("x")).status == SLICE_EMPTY
    failed = SliceOutcome.from_error(RateLimitError(status_code=429, url="/x"))
    assert (failed.status, failed.reason, failed.http_status, failed.failed) == (SLICE_FAILED, "429", 429, True)


@pytest.mark.parametrize("body,status", [
    (PRESENT["incidents"], SLICE_OK),
    ({"incidents": []}, SLICE_EMPTY),  # 200 ama içinde veri yok
    ({}, SLICE_EMPTY),
    (None, SLICE_EMPTY),
])
def test_answered_slice_is_ok_only_when_it_carries_data(tmp_path, body, status):
    assert _fetcher(tmp_path)._answered_outcome("incidents", body).status == status


# --- async yol (toplu indirme) -------------------------------------------------------------

@pytest.mark.parametrize("reason,error,http_status", TRANSIENT, ids=[f"{t[0]}-{t[2]}" for t in TRANSIENT])
def test_async_transient_slice_error_is_recorded_not_counted(tmp_path, reason, error, http_status):
    f = _fetcher(tmp_path)
    slices = dict(PRESENT, lineups=error)
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS + 1):  # eski kuralda ikinci denemede "yok" olurdu
        _fetch_async(f, slices)

    assert _counts(f) == {}
    record = _info(f, "lineups").error
    assert record.reason == reason and record.http_status == http_status
    assert record.count == UNAVAILABLE_AFTER_ATTEMPTS + 1 and record.at is not None and record.at.utcoffset() is not None
    assert not _info(f, "lineups").has_payload
    assert "lineups" in f._expected_slice_keys(int(MID), "football")
    assert f._compute_detail_need(MID) == "refill"  # maç tam görünmüyor


def test_async_429_on_slice_is_retried_on_the_next_run(tmp_path):
    f = _fetcher(tmp_path)
    _fetch_async(f, dict(PRESENT, lineups=RateLimitError(status_code=429, url="/x")))
    _fetch_async(f, dict(PRESENT, lineups=RateLimitError(status_code=429, url="/x")))

    # Sonraki çalıştırma: maç "refill" ister ve dilim yeniden istenir; bu kez geliyor
    calls: List[str] = []
    with _curl_layer(_basic(), PRESENT, calls):
        assert f._needs_detail_fetch(MID) == "refill"
        f.refill_missing_match_slices(MID)
    assert calls == [EVENT, f"{EVENT}/lineups"]
    assert open_store(f.data_dir).events.payload(int(MID), "lineups") == PRESENT["lineups"]
    assert _errors(f) == {}  # hata kaydı veri gelince silinir
    assert f._compute_detail_need(MID) == "none"


def test_async_404_on_slice_is_counted_and_stops_being_expected(tmp_path):
    f = _fetcher(tmp_path)
    slices = dict(PRESENT, lineups=ResourceNotFoundError("x"))
    _fetch_async(f, slices)
    assert _counts(f) == {"lineups": 1}
    assert f._compute_detail_need(MID) == "refill"

    _fetch_async(f, slices)
    assert _counts(f) == {"lineups": UNAVAILABLE_AFTER_ATTEMPTS}
    assert _info(f, "lineups").empty_count == UNAVAILABLE_AFTER_ATTEMPTS
    assert "lineups" not in f._expected_slice_keys(int(MID), "football")
    assert f._compute_detail_need(MID) == "none"


def test_async_empty_200_is_counted(tmp_path):
    f = _fetcher(tmp_path)
    _fetch_async(f, dict(PRESENT, incidents={"incidents": []}, lineups={}))
    assert _counts(f) == {"lineups": 1, "incidents": 1}
    assert _info(f, "incidents").has_payload  # içinde veri olmayan gövde de saklanır


def test_definitive_answer_after_a_failure_clears_the_error(tmp_path):
    f = _fetcher(tmp_path)
    _fetch_async(f, dict(PRESENT, lineups=APIError("HTTP 403 Forbidden", status_code=403)))
    _fetch_async(f, dict(PRESENT, lineups=ResourceNotFoundError("x")))
    entry = _info(f, "lineups")
    assert entry.error is None and entry.empty_count == 1


def test_async_slice_failure_through_the_real_request_layer(tmp_path):
    """Sahte taşıyıcı 429 döndürür: istek katmanı RateLimitError fırlatır, dilim "yok" sayılmaz."""
    f = _fetcher(tmp_path)
    basic = _basic()

    async def fake_get(url, **_kw):
        key = _slice_of(url)
        if not key:
            return Resp(200, {"event": basic})
        return Resp(429) if key == "statistics" else Resp(200, PRESENT[key])

    with _request_layer(), patch("src.utils.create_session_async", _session_of(fake_get)):
        data = f.fetch_match_data(MID)
    assert data["statistics"] is None
    assert _counts(f) == {}
    assert _errors(f) == {"statistics": "429"}


def test_breaker_trips_on_blocked_slices_and_does_not_mark_them_unavailable(tmp_path):
    """/event geliyor ama alt dilimler 429: eskiden kesici bunu hiç görmüyordu, dilimler "yok" oluyordu."""
    f = _fetcher(tmp_path, threshold=6)
    ids = [str(n) for n in range(2001, 2011)]

    async def fake_get(url, **_kw):
        mid = url.split("/event/", 1)[1].split("/", 1)[0]
        return Resp(200, {"event": _basic(mid)}) if url.endswith(f"/event/{mid}") else Resp(429)

    failed: List[str] = []
    with _request_layer(), patch("src.utils.create_session_async", _session_of(fake_get)):
        results = asyncio.run(f.fetch_matches_batch_async(ids, max_concurrent=1, failed_callback=failed.append))

    assert f.rate_limit_breaker_triggered is True
    assert f.last_status_counts.get("429") == 6
    assert list(results) == ["2001"]  # ilk maçın altı dilimi devreyi kesti; ikinci maç hiç başlamadı
    assert failed == []
    assert _counts(f, "2001") == {}
    assert _errors(f, "2001") == {k: "429" for k in DETAIL_SLICE_KEYS}
    assert f._compute_detail_need("2001") == "refill"  # sonraki çalıştırmada yeniden denenecek


# --- refill (eskiden sync yol: tek maç indirme ve refill) ------------------------------------

def _stored(f: MatchDataFetcher) -> None:
    """Yalnızca olay yükü saklanan bitmiş maç (refill tüm dilimleri ister)."""
    f._save_match_data(MID, {"basic": _basic()})


@pytest.mark.parametrize("answer,reason,http_status", [
    (Resp(429), "429", 429),
    (Resp(503), "5xx", 503),
    (Resp(403, text="no"), "403", 403),
    (Resp(500), "5xx", 500),
    (RuntimeError("curl: (28) Operation timed out after 10000 ms"), "timeout", None),
    (RuntimeError("curl: (7) Failed to connect"), "network", None),
    (Resp(200, bad_json=True), "parse", None),
], ids=["429", "503", "403", "500", "timeout", "network", "parse"])
def test_sync_refill_transient_error_is_recorded_not_counted(tmp_path, answer, reason, http_status):
    f = _fetcher(tmp_path)
    _stored(f)
    slices = dict(PRESENT, statistics=answer)
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS + 1):
        with _curl_layer(_basic(), slices, []):
            f.refill_missing_match_slices(MID)

    assert _counts(f) == {}
    record = _info(f, "statistics").error
    assert (record.reason, record.http_status) == (reason, http_status)
    assert record.count == UNAVAILABLE_AFTER_ATTEMPTS + 1
    assert f._compute_detail_need(MID) == "refill"


def test_sync_refill_retries_the_failed_slice_on_the_next_run(tmp_path):
    f = _fetcher(tmp_path)
    _stored(f)
    with _curl_layer(_basic(), dict(PRESENT, statistics=Resp(429)), []):
        f.refill_missing_match_slices(MID)

    calls: List[str] = []
    with _curl_layer(_basic(), PRESENT, calls):
        f.refill_missing_match_slices(MID)
    assert calls == [EVENT, f"{EVENT}/statistics"]  # yalnızca başarısız olan dilim yeniden istendi
    assert open_store(f.data_dir).events.payload(int(MID), "statistics") == PRESENT["statistics"]
    assert _errors(f) == {}
    assert f._compute_detail_need(MID) == "none"


def test_sync_refill_404_is_counted(tmp_path):
    f = _fetcher(tmp_path)
    _stored(f)
    slices = {k: v for k, v in PRESENT.items() if k != "lineups"}  # lineups: 404
    for expected in range(1, UNAVAILABLE_AFTER_ATTEMPTS + 1):
        calls: List[str] = []
        with _curl_layer(_basic(), slices, calls):
            f.refill_missing_match_slices(MID)
        assert f"{EVENT}/lineups" in calls
        assert _counts(f) == {"lineups": expected}
    assert f._compute_detail_need(MID) == "none"

    # Artık beklenmiyor: bir sonraki refill dilimi istemez (maç zaten tam)
    calls = []
    with _curl_layer(_basic(), slices, calls):
        f.refill_missing_match_slices(MID)
    assert calls == [EVENT]


def test_sync_full_fetch_separates_failure_from_empty(tmp_path):
    f = _fetcher(tmp_path)
    slices = dict(PRESENT, lineups=Resp(429), incidents={"incidents": []})
    slices.pop("pregame_form")  # 404
    with _curl_layer(_basic(), slices, []):
        data = f.fetch_match_data(MID)
    assert data["lineups"] is None
    assert _counts(f) == {"pregame_form": 1, "incidents": 1}
    lineups = _info(f, "lineups")
    assert lineups.error.reason == "429" and lineups.empty_count == 0 and lineups.state == "error"
    assert _info(f, "pregame_form").empty_count == 1


def test_a_slice_request_separates_a_failure_from_a_missing_resource(tmp_path):
    """Dilim isteğinin hatası yutulmaz: 429 başarısız istektir, 404 kesin "yok"tur (nedeni "404")."""
    from src.match_data_fetcher import SingleFetchReport

    f = _fetcher(tmp_path)
    failed, missing = SingleFetchReport(), SingleFetchReport()
    with _curl_layer(_basic(), dict(PRESENT, lineups=Resp(429)), []):
        f.fetch_match_data(MID, report=failed)
    with _curl_layer(_basic(), {k: v for k, v in PRESENT.items() if k != "lineups"}, []):
        f.fetch_match_data(MID, report=missing)
    lineups = failed.slices["lineups"]
    assert (lineups.status, lineups.reason, lineups.http_status) == (SLICE_FAILED, "429", 429)
    lineups = missing.slices["lineups"]
    assert (lineups.status, lineups.reason, lineups.http_status) == (SLICE_EMPTY, "404", 404)


# --- eski işaretlerin yeniden denetimi -----------------------------------------------------

def _legacy_record(f: MatchDataFetcher, match_data: Dict[str, Any], outcomes: Any = None) -> Path:
    """Eski bir sürümün yazdığı kayıt (eski düzen); maç dizinini döndürür."""
    return Path(legacy_writer.save_legacy(f.data_dir, MID, match_data, outcomes))


def _legacy_markers(match_dir: Path, markers: Dict[str, int]) -> None:
    """Eski sürümün işaret dosyası; eski yazıcılar gibi ardından Store'un kancası (katalog dosyayı görür)."""
    (match_dir / UNAVAILABLE_FILE).write_text(json.dumps(markers), encoding="utf-8")
    store_api.shadow_event(match_dir.parents[3], match_dir.name, match_dir)


def _files(match_dir: Path) -> Dict[str, bytes]:
    return {p.name: p.read_bytes() for p in match_dir.iterdir()}


def test_recheck_reopens_legacy_markers(tmp_path):
    f = _fetcher(tmp_path)
    match_dir = _legacy_record(f, {"basic": _basic(), **{k: v for k, v in PRESENT.items()
                                                          if k not in ("lineups", "incidents")}})
    _legacy_markers(match_dir, {"lineups": 2, "incidents": 2})  # eski sürüm: neden boş geldiği bilinmiyor
    before = _files(match_dir)
    assert f._compute_detail_need(MID) == "none"

    assert f.reset_unavailable_markers() == {"matches": 1, "slices": 2, "scanned": 1}
    assert _counts(f) == {}
    assert f._compute_detail_need(MID) == "refill"
    # Kayıt v3'e yükseltildi; eski dizine dokunulmadı (karar 4)
    assert open_store(f.data_dir).events.get(int(MID)).layout == "v3" and _files(match_dir) == before
    # İkinci kez çalıştırmak hiçbir şeyi değiştirmez
    assert f.reset_unavailable_markers() == {"matches": 0, "slices": 0, "scanned": 0}


def test_recheck_keeps_markers_confirmed_by_a_definitive_answer(tmp_path):
    f = _fetcher(tmp_path)
    slices = dict(PRESENT, lineups=ResourceNotFoundError("x"))
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS):
        _fetch_async(f, slices)
    before = _counts(f)

    assert f.reset_unavailable_markers() == {"matches": 0, "slices": 0, "scanned": 1}
    assert _counts(f) == before == {"lineups": UNAVAILABLE_AFTER_ATTEMPTS}
    assert f._compute_detail_need(MID) == "none"

    assert f.reset_unavailable_markers(include_confirmed=True) == {"matches": 1, "slices": 1, "scanned": 1}
    assert _counts(f) == {} and _errors(f) == {} and _info(f, "lineups").state == "not_requested"
    assert f._compute_detail_need(MID) == "refill"


def test_recheck_drops_only_the_unverified_part_of_a_count(tmp_path):
    f = _fetcher(tmp_path)
    match_dir = _legacy_record(f, {"basic": _basic(), **PRESENT, "lineups": None},
                               {"lineups": SliceOutcome.from_error(ResourceNotFoundError("x"))})  # 1 kesin "yok"
    _legacy_markers(match_dir, {"lineups": 2})  # biri eski sürümden
    assert (_info(f, "lineups").empty_count, _info(f, "lineups").unverified_empty_count) == (1, 1)
    assert f.reset_unavailable_markers()["slices"] == 1
    assert _counts(f) == {"lineups": 1} and _info(f, "lineups").empty_count == 1


def test_recheck_can_be_limited_to_one_league(tmp_path):
    f = _fetcher(tmp_path)
    match_dir = _legacy_record(f, {"basic": _basic()})
    _legacy_markers(match_dir, {"lineups": 2})
    assert f.reset_unavailable_markers(league_id=999)["scanned"] == 0
    assert _counts(f) == {"lineups": 2}
    assert f.reset_unavailable_markers(league_id=77)["slices"] == 1
    assert _counts(f) == {}


def test_recheck_cli_flag_resets_and_exits_without_network(tmp_path, monkeypatch, capsys):
    import main as cli

    f = _fetcher(tmp_path)
    match_dir = _legacy_record(f, {"basic": _basic()})
    _legacy_markers(match_dir, {"lineups": 2, "incidents": 1})
    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"])  # main --data-dir ortamı değiştirir: test sonunda geri al
    monkeypatch.setattr("sys.argv", ["main.py", "--recheck-unavailable", "--data-dir", str(tmp_path)])
    with patch.object(utils.cffi_requests, "get", side_effect=AssertionError("ağ isteği yapılmamalı")):
        assert cli.main() == 0
    assert _counts(f) == {}
    assert (match_dir / UNAVAILABLE_FILE).exists()  # eski dizine dokunulmaz: geçerli kopya artık v3'te
    assert "1" in capsys.readouterr().out


@pytest.mark.parametrize("argv,expected", [
    ([], None),
    (["--recheck-unavailable"], "legacy"),
    (["--recheck-unavailable", "all"], "all"),
])
def test_recheck_cli_flag_values(monkeypatch, argv, expected):
    import main as cli

    monkeypatch.setattr("sys.argv", ["main.py", *argv])
    assert cli.parse_arguments().recheck_unavailable == expected
