"""
Veri bütünlüğü: dilim isteklerinin tipli sonucu ve "bu dilim bu maçta yok" işaretleri.

Sabitlenen kurallar:
  - Yalnızca kesin yanıt (HTTP 404 ya da içinde veri olmayan 200) "yok" sayılır. Başarısız istek
    (403/429/5xx/zaman aşımı/ağ/bozuk yanıt) sayılmaz, nedeni ve zamanıyla _slice_status.json'a
    yazılır, sonraki çalıştırmada yeniden istenir ve devre kesiciyi besler. Async ve sync yollar aynı.
  - Eski sürümlerden kalan işaretler kendiliğinden sıfırlanmaz; --recheck-unavailable ile yeniden
    denetime açılır (kesin yanıtla doğrulanmış olanlar korunur).

Gerçek ağ yok: istek katmanının taşıyıcısı (curl) ya da kendisi sahte.
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

import src.utils as utils
from src.exceptions import APIError, DataParsingError, NetworkError, RateLimitError, ResourceNotFoundError
from src.match_data_fetcher import (DETAIL_SLICE_KEYS, SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_STATUS_FILE,
                                    UNAVAILABLE_AFTER_ATTEMPTS, UNAVAILABLE_FILE, MatchDataFetcher, SliceOutcome)

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


def _match_dir(f: MatchDataFetcher, mid: str = MID) -> Path:
    return next(p.parent for p in Path(f.match_details_dir).rglob("basic.json") if p.parent.name == mid)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _fetch_async(f: MatchDataFetcher, slices: Dict[str, Any], basic: dict | None = None) -> List[str]:
    calls: List[str] = []
    with patch("src.utils.make_api_request_async", new=_async_api(basic or _basic(), slices, calls)):
        asyncio.run(f._fetch_match_data_async(object(), MID))
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
    match_dir = _match_dir(f)

    assert not (match_dir / UNAVAILABLE_FILE).exists()
    record = _json(match_dir / SLICE_STATUS_FILE)["lineups"]["error"]
    assert record["reason"] == reason and record["status"] == http_status
    assert record["count"] == UNAVAILABLE_AFTER_ATTEMPTS + 1 and record["at"].endswith("+00:00")
    assert not (match_dir / "lineups.json").exists()
    assert "lineups" in f._expected_slices(str(match_dir), "football")
    assert f._compute_detail_need(MID) == "refill"  # maç tam görünmüyor


def test_async_429_on_slice_is_retried_on_the_next_run(tmp_path):
    f = _fetcher(tmp_path)
    _fetch_async(f, dict(PRESENT, lineups=RateLimitError(status_code=429, url="/x")))
    _fetch_async(f, dict(PRESENT, lineups=RateLimitError(status_code=429, url="/x")))

    # Sonraki çalıştırma: maç "refill" ister ve dilim yeniden istenir; bu kez geliyor
    calls: List[str] = []
    with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=_curl(_basic(), PRESENT, calls)):
        assert f._needs_detail_fetch(MID) == "refill"
        f.refill_missing_match_slices(MID)
    assert calls == [EVENT, f"{EVENT}/lineups"]
    match_dir = _match_dir(f)
    assert _json(match_dir / "lineups.json") == PRESENT["lineups"]
    assert not (match_dir / SLICE_STATUS_FILE).exists()  # hata kaydı veri gelince silinir
    assert f._compute_detail_need(MID) == "none"


def test_async_404_on_slice_is_counted_and_stops_being_expected(tmp_path):
    f = _fetcher(tmp_path)
    slices = dict(PRESENT, lineups=ResourceNotFoundError("x"))
    _fetch_async(f, slices)
    match_dir = _match_dir(f)
    assert _json(match_dir / UNAVAILABLE_FILE) == {"lineups": 1}
    assert f._compute_detail_need(MID) == "refill"

    _fetch_async(f, slices)
    assert _json(match_dir / UNAVAILABLE_FILE) == {"lineups": UNAVAILABLE_AFTER_ATTEMPTS}
    assert _json(match_dir / SLICE_STATUS_FILE)["lineups"]["empty"]["count"] == UNAVAILABLE_AFTER_ATTEMPTS
    assert "lineups" not in f._expected_slices(str(match_dir), "football")
    assert f._compute_detail_need(MID) == "none"


def test_async_empty_200_is_counted(tmp_path):
    f = _fetcher(tmp_path)
    _fetch_async(f, dict(PRESENT, incidents={"incidents": []}, lineups={}))
    assert _json(_match_dir(f) / UNAVAILABLE_FILE) == {"lineups": 1, "incidents": 1}


def test_definitive_answer_after_a_failure_clears_the_error(tmp_path):
    f = _fetcher(tmp_path)
    _fetch_async(f, dict(PRESENT, lineups=APIError("HTTP 403 Forbidden", status_code=403)))
    _fetch_async(f, dict(PRESENT, lineups=ResourceNotFoundError("x")))
    entry = _json(_match_dir(f) / SLICE_STATUS_FILE)["lineups"]
    assert "error" not in entry and entry["empty"]["count"] == 1


def test_async_slice_failure_through_the_real_request_layer(tmp_path):
    """Sahte taşıyıcı 429 döndürür: istek katmanı RateLimitError fırlatır, dilim "yok" sayılmaz."""
    f = _fetcher(tmp_path)
    basic = _basic()

    async def fake_get(url, **_kw):
        key = _slice_of(url)
        if not key:
            return Resp(200, {"event": basic})
        return Resp(429) if key == "statistics" else Resp(200, PRESENT[key])

    session = MagicMock()
    session.get = AsyncMock(side_effect=fake_get)
    with _request_layer():
        data = asyncio.run(f._fetch_match_data_async(session, MID))
    assert data["statistics"] is None
    match_dir = _match_dir(f)
    assert not (match_dir / UNAVAILABLE_FILE).exists()
    assert _json(match_dir / SLICE_STATUS_FILE)["statistics"]["error"]["reason"] == "429"


def test_breaker_trips_on_blocked_slices_and_does_not_mark_them_unavailable(tmp_path):
    """/event geliyor ama alt dilimler 429: eskiden kesici bunu hiç görmüyordu, dilimler "yok" oluyordu."""
    f = _fetcher(tmp_path, threshold=6)
    ids = [str(n) for n in range(2001, 2011)]

    async def fake_get(url, **_kw):
        mid = url.split("/event/", 1)[1].split("/", 1)[0]
        return Resp(200, {"event": _basic(mid)}) if url.endswith(f"/event/{mid}") else Resp(429)

    @contextlib.asynccontextmanager
    async def session_ctx():
        session = MagicMock()
        session.get = AsyncMock(side_effect=fake_get)
        yield session

    failed: List[str] = []
    with _request_layer(), patch("src.utils.create_session_async", session_ctx), \
            patch("src.match_data_fetcher.asyncio.sleep", new=AsyncMock()):
        results = asyncio.run(f.fetch_matches_batch_async(ids, max_concurrent=1, failed_callback=failed.append))

    assert f.rate_limit_breaker_triggered is True
    assert f.last_status_counts.get("429") == 6
    assert list(results) == ["2001"]  # ilk maçın altı dilimi devreyi kesti; ikinci maç hiç başlamadı
    assert failed == []
    match_dir = next(Path(f.match_details_dir).rglob("basic.json")).parent
    assert not (match_dir / UNAVAILABLE_FILE).exists()
    errors = json.loads((match_dir / SLICE_STATUS_FILE).read_text())
    assert {key: entry["error"]["reason"] for key, entry in errors.items()} == {k: "429" for k in DETAIL_SLICE_KEYS}
    assert f._compute_detail_need("2001") == "refill"  # sonraki çalıştırmada yeniden denenecek


# --- sync yol (tek maç indirme ve refill) --------------------------------------------------

def _stored(f: MatchDataFetcher) -> Path:
    """Diskte yalnızca basic'i olan bitmiş maç (refill tüm dilimleri ister)."""
    f._save_match_data(MID, {"basic": _basic()})
    return _match_dir(f)


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
    match_dir = _stored(f)
    slices = dict(PRESENT, statistics=answer)
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS + 1):
        with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=_curl(_basic(), slices, [])):
            f.refill_missing_match_slices(MID)

    assert not (match_dir / UNAVAILABLE_FILE).exists()
    record = _json(match_dir / SLICE_STATUS_FILE)["statistics"]["error"]
    assert (record["reason"], record["status"]) == (reason, http_status)
    assert record["count"] == UNAVAILABLE_AFTER_ATTEMPTS + 1
    assert f._compute_detail_need(MID) == "refill"


def test_sync_refill_retries_the_failed_slice_on_the_next_run(tmp_path):
    f = _fetcher(tmp_path)
    match_dir = _stored(f)
    with _request_layer(), patch.object(
        utils.cffi_requests, "get", side_effect=_curl(_basic(), dict(PRESENT, statistics=Resp(429)), [])
    ):
        f.refill_missing_match_slices(MID)

    calls: List[str] = []
    with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=_curl(_basic(), PRESENT, calls)):
        f.refill_missing_match_slices(MID)
    assert calls == [EVENT, f"{EVENT}/statistics"]  # yalnızca başarısız olan dilim yeniden istendi
    assert _json(match_dir / "statistics.json") == PRESENT["statistics"]
    assert not (match_dir / SLICE_STATUS_FILE).exists()
    assert f._compute_detail_need(MID) == "none"


def test_sync_refill_404_is_counted(tmp_path):
    f = _fetcher(tmp_path)
    match_dir = _stored(f)
    slices = {k: v for k, v in PRESENT.items() if k != "lineups"}  # lineups: 404
    for expected in range(1, UNAVAILABLE_AFTER_ATTEMPTS + 1):
        calls: List[str] = []
        with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=_curl(_basic(), slices, calls)):
            f.refill_missing_match_slices(MID)
        assert f"{EVENT}/lineups" in calls
        assert _json(match_dir / UNAVAILABLE_FILE) == {"lineups": expected}
    assert f._compute_detail_need(MID) == "none"

    # Artık beklenmiyor: bir sonraki refill dilimi istemez (maç zaten tam)
    calls = []
    with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=_curl(_basic(), slices, calls)):
        f.refill_missing_match_slices(MID)
    assert calls == [EVENT]


def test_sync_full_fetch_separates_failure_from_empty(tmp_path):
    f = _fetcher(tmp_path)
    slices = dict(PRESENT, lineups=Resp(429), incidents={"incidents": []})
    slices.pop("pregame_form")  # 404
    with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=_curl(_basic(), slices, [])):
        data = f.fetch_match_data(MID)
    assert data["lineups"] is None
    match_dir = _match_dir(f)
    assert _json(match_dir / UNAVAILABLE_FILE) == {"pregame_form": 1, "incidents": 1}
    status = _json(match_dir / SLICE_STATUS_FILE)
    assert status["lineups"]["error"]["reason"] == "429" and "empty" not in status["lineups"]
    assert status["pregame_form"]["empty"]["count"] == 1


def test_sync_helper_raises_instead_of_swallowing(tmp_path):
    """Eski adlı yardımcılar da hatayı yutmaz: None yalnızca "kaynak yok" demektir."""
    f = _fetcher(tmp_path)
    with _request_layer(), patch.object(utils.cffi_requests, "get", return_value=Resp(429)):
        with pytest.raises(RateLimitError):
            f._fetch_lineups(MID)
    with _request_layer(), patch.object(utils.cffi_requests, "get", return_value=Resp(404)):
        assert f._fetch_lineups(MID) is None


# --- eski işaretlerin yeniden denetimi -----------------------------------------------------

def _legacy_markers(match_dir: Path, markers: Dict[str, int]) -> None:
    (match_dir / UNAVAILABLE_FILE).write_text(json.dumps(markers), encoding="utf-8")


def test_recheck_reopens_legacy_markers(tmp_path):
    f = _fetcher(tmp_path)
    f._save_match_data(MID, {"basic": _basic(), **{k: v for k, v in PRESENT.items() if k not in ("lineups", "incidents")}})
    match_dir = _match_dir(f)
    _legacy_markers(match_dir, {"lineups": 2, "incidents": 2})  # eski sürüm: neden boş geldiği bilinmiyor
    assert f._compute_detail_need(MID) == "none"

    assert f.reset_unavailable_markers() == {"matches": 1, "slices": 2, "scanned": 1}
    assert not (match_dir / UNAVAILABLE_FILE).exists()
    assert f._compute_detail_need(MID) == "refill"
    # İkinci kez çalıştırmak hiçbir şeyi değiştirmez
    assert f.reset_unavailable_markers() == {"matches": 0, "slices": 0, "scanned": 0}


def test_recheck_keeps_markers_confirmed_by_a_definitive_answer(tmp_path):
    f = _fetcher(tmp_path)
    slices = dict(PRESENT, lineups=ResourceNotFoundError("x"))
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS):
        _fetch_async(f, slices)
    match_dir = _match_dir(f)
    before = (match_dir / UNAVAILABLE_FILE).read_text()

    assert f.reset_unavailable_markers() == {"matches": 0, "slices": 0, "scanned": 1}
    assert (match_dir / UNAVAILABLE_FILE).read_text() == before
    assert f._compute_detail_need(MID) == "none"

    assert f.reset_unavailable_markers(include_confirmed=True) == {"matches": 1, "slices": 1, "scanned": 1}
    assert not (match_dir / UNAVAILABLE_FILE).exists() and not (match_dir / SLICE_STATUS_FILE).exists()
    assert f._compute_detail_need(MID) == "refill"


def test_recheck_drops_only_the_unverified_part_of_a_count(tmp_path):
    f = _fetcher(tmp_path)
    _fetch_async(f, dict(PRESENT, lineups=ResourceNotFoundError("x")))  # 1 kesin "yok"
    match_dir = _match_dir(f)
    _legacy_markers(match_dir, {"lineups": 2})  # biri eski sürümden
    assert f.reset_unavailable_markers()["slices"] == 1
    assert _json(match_dir / UNAVAILABLE_FILE) == {"lineups": 1}


def test_recheck_can_be_limited_to_one_league(tmp_path):
    f = _fetcher(tmp_path)
    f._save_match_data(MID, {"basic": _basic()})
    match_dir = _match_dir(f)
    _legacy_markers(match_dir, {"lineups": 2})
    assert f.reset_unavailable_markers(league_id=999)["scanned"] == 0
    assert (match_dir / UNAVAILABLE_FILE).exists()
    assert f.reset_unavailable_markers(league_id=77)["slices"] == 1


def test_recheck_cli_flag_resets_and_exits_without_network(tmp_path, monkeypatch, capsys):
    import main as cli

    f = _fetcher(tmp_path)
    f._save_match_data(MID, {"basic": _basic()})
    _legacy_markers(_match_dir(f), {"lineups": 2, "incidents": 1})
    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"])  # main --data-dir ortamı değiştirir: test sonunda geri al
    monkeypatch.setattr("sys.argv", ["main.py", "--recheck-unavailable", "--data-dir", str(tmp_path)])
    with patch.object(utils.cffi_requests, "get", side_effect=AssertionError("ağ isteği yapılmamalı")):
        assert cli.main() == 0
    assert not (_match_dir(f) / UNAVAILABLE_FILE).exists()
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
