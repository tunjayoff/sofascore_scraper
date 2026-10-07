"""
Yazarken öneri (plan maddesi FX-20): katalogdan ad önerisi ve SofaScore aramasının sunucu tarafı.

  * `GET /api/v1/catalog/suggest`: adında metin geçen kayıtlı turnuvalar ve takımlar, arama sonucunun biçiminde;
    SofaScore'a istek gitmez; adı metinle başlayanlar önce, sonra bir sözcüğü metinle başlayanlar, sonra öteki;
    her birinde takip edilenler önce;
  * `POST /api/v1/tournaments/search`: yanıt sunucuda 10 dakika saklanır (aynı metin, büyük-küçük harf ve boşluk
    farkı önemsiz, istek atmaz); hatalı yanıt saklanmaz; istek ortak bütçeden sıra alır (sofascore_scraper/throttle.py);
  * istemci bağlantıyı keserse henüz gönderilmemiş istek gönderilmez (bütçede sıra beklerken kesilen istek sırasını
    geri verir).

Ağ yok: istekler tests/fakes/sofascore.py'nin sahte taşıyıcısına gider ya da curl çağrısı sayılır ve reddedilir.
"""
from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from characterization import WORLD, pin_default_settings
from fakes.sofascore import FakeSofaScore
from sofascore_scraper import throttle
from sofascore_scraper.client import endpoints
from sofascore_scraper.client import transport
from sofascore_scraper.client.context import FetchCancelled
from sofascore_scraper.config import loader
from sofascore_scraper.services import follows as follows_module
from sofascore_scraper.store import FollowSpec, Store, open_store
from sofascore_scraper.web.api.v1 import tournaments as tournaments_routes
from sofascore_scraper.web.app import app

client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures" / "fx19"
LALIGA, PL = sf.LALIGA.id, sf.PL.id


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return open_store(fixture.data_dir)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return open_store(tmp_path / "data")


def search_all_body() -> Dict[str, Any]:
    return copy.deepcopy(json.loads((FIXTURES / "search_all.json").read_text(encoding="utf-8"))["body"])


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        world.add(endpoints.search_all("galatasaray"), search_all_body())
        yield world


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def api_paths(fake: FakeSofaScore) -> List[str]:
    return [path for path in fake.paths() if path.startswith("/")]


def search(q: str, **body: Any) -> Any:
    return client.post("/api/v1/tournaments/search", json={"q": q, "kinds": ["tournament", "team", "player"], **body})


# --- katalogdan öneri ------------------------------------------------------------------------------------


def test_the_catalog_suggests_tournaments_and_teams_without_a_request(canonical: Store) -> None:
    with FakeSofaScore.from_file(WORLD) as world:
        hits = data(client.get("/api/v1/catalog/suggest", params={"q": "la"}))
        assert world.paths() == []
    # adı "la" ile başlayan önce, sonra adında geçenler (ada göre)
    assert [(h["kind"], h["name"]) for h in hits][:4] == [
        ("tournament", "LaLiga"), ("team", "Aston Villa"), ("team", "Atlanta Hawks"), ("team", "Crystal Palace")]
    laliga = hits[0]
    assert (laliga["id"], laliga["sport"], laliga["followed"]) == (LALIGA, "football", False)
    assert all(h["kind"] in ("tournament", "team") for h in hits)
    assert len(hits) <= 8


def test_a_word_start_and_a_follow_come_first(canonical: Store) -> None:
    by_word = data(client.get("/api/v1/catalog/suggest", params={"q": "hawks"}))
    assert [h["name"] for h in by_word] == ["Atlanta Hawks"]
    villa = data(client.get("/api/v1/catalog/suggest", params={"q": "villa"}))
    assert [h["name"] for h in villa] == ["Villarreal", "Aston Villa"]  # adın başı, sonra bir sözcüğün başı
    hits = data(client.get("/api/v1/catalog/suggest", params={"q": "la"}))
    palace = next(h for h in hits if h["name"] == "Crystal Palace")
    canonical.follows.add(FollowSpec(kind="team", entity_id=palace["id"], name="Crystal Palace"), origin="api")
    hits = data(client.get("/api/v1/catalog/suggest", params={"q": "la"}))
    assert hits[0]["name"] == "LaLiga"  # adı metinle başlayan, takip edilenden önce
    assert (hits[1]["name"], hits[1]["followed"]) == ("Crystal Palace", True)


def test_suggestions_by_sport_case_accents_and_limit(canonical: Store) -> None:
    assert [h["name"] for h in data(client.get("/api/v1/catalog/suggest", params={"q": "LÁLİGA"}))] == ["LaLiga"]
    basketball = data(client.get("/api/v1/catalog/suggest", params={"q": "la", "sport": "basketball"}))
    assert basketball and all(h["sport"] == "basketball" for h in basketball)
    assert len(data(client.get("/api/v1/catalog/suggest", params={"q": "a", "limit": 3}))) == 3
    assert data(client.get("/api/v1/catalog/suggest", params={"q": "zzzz"})) == []
    assert client.get("/api/v1/catalog/suggest", params={"q": ""}).status_code == 422
    assert client.get("/api/v1/catalog/suggest", params={"q": "a", "limit": 21}).status_code == 422


def test_an_empty_data_directory_suggests_nothing(store: Store) -> None:
    assert data(client.get("/api/v1/catalog/suggest", params={"q": "la"})) == []


# --- SofaScore araması: sunucuda saklanan yanıt -------------------------------------------------------


def test_the_same_text_again_sends_nothing(store: Store, fake: FakeSofaScore) -> None:
    first = data(search("galatasaray"))
    assert data(search("  GalataSaray ")) == first
    assert data(search("galatasaray", kinds=["player"])) == [h for h in first if h["kind"] == "player"]
    assert api_paths(fake) == ["/search/all?q=galatasaray&page=0"]
    # tournament-only search keeps its own endpoint (and its own entry)
    fake.add(endpoints.search_unique_tournaments("galatasaray"), {"uniqueTournaments": []})
    assert data(client.post("/api/v1/tournaments/search", json={"q": "galatasaray"})) == []
    assert api_paths(fake)[-1] == "/search/unique-tournaments/galatasaray"
    assert len(api_paths(fake)) == 2


def test_followed_is_read_again_for_a_kept_answer(store: Store, fake: FakeSofaScore) -> None:
    assert not any(h["followed"] for h in data(search("galatasaray")))
    store.follows.add(FollowSpec(kind="team", entity_id=3061, name="Galatasaray"), origin="api")
    hits = data(search("galatasaray"))
    assert [h["id"] for h in hits if h["followed"]] == [3061]
    assert len(api_paths(fake)) == 1


def test_a_not_found_answer_is_kept_and_a_failure_is_not(store: Store, fake: FakeSofaScore) -> None:
    assert data(search("nothing")) == [] and data(search("nothing")) == []
    assert api_paths(fake) == ["/search/all?q=nothing&page=0"]
    fake.fail(endpoints.search_all("galatasaray"), 500, times=1)
    assert search("galatasaray").status_code == 502
    assert data(search("galatasaray"))
    assert data(search("galatasaray"))
    assert api_paths(fake)[1:] == ["/search/all?q=galatasaray&page=0"] * 2


def test_inner_spaces_are_sent_once(store: Store, fake: FakeSofaScore) -> None:
    data(search("la   liga"))
    data(search("La Liga"))
    assert api_paths(fake) == ["/search/all?q=la%20liga&page=0"]


def test_a_kept_answer_expires() -> None:
    now = [100.0]
    cache = follows_module._SearchCache(600.0, 2, clock=lambda: now[0])
    cache.put((False, "la"), [1])
    assert cache.get((False, "la")) == (True, [1])
    now[0] += 599.0
    assert cache.get((False, "la")) == (True, [1])
    now[0] += 1.0
    assert cache.get((False, "la")) == (False, None)
    cache.put((False, "a"), None)
    cache.put((False, "b"), [2])
    cache.get((False, "a"))
    cache.put((False, "c"), [3])  # en eski kullanılan ("b") çıkar
    assert cache.get((False, "b")) == (False, None)
    assert cache.get((False, "a")) == (True, None)
    assert follows_module.SEARCH_CACHE_SECONDS == 600.0


def test_a_search_takes_its_turn_in_the_request_budget(store: Store, fake: FakeSofaScore,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    turns: List[float] = []
    real = throttle.reserve

    def counted() -> Any:
        turns.append(time.monotonic())
        return real()

    monkeypatch.setattr(throttle, "reserve", counted)
    data(search("galatasaray"))
    assert len(turns) == 1
    data(search("galatasaray"))
    assert len(turns) == 1  # saklanan yanıt: istek yok, sıra yok


# --- istemci gidince istek gönderilmez ---------------------------------------------------------------


class _Gone:
    """Bir isteğin yerine: `after` saniye sonra bağlantı kesilir (`receive` o an `http.disconnect` verir)."""

    def __init__(self, after: float) -> None:
        self.at = time.monotonic() + after
        self.asked = 0

    async def receive(self) -> Dict[str, Any]:
        self.asked += 1
        await asyncio.sleep(max(0.0, self.at - time.monotonic()))
        return {"type": "http.disconnect"}


@pytest.fixture
def slow_budget(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Ortak bütçe dolu: sıradaki istek 30 sn bekler. curl çağrısı sayılır ve reddedilir."""
    lane = throttle.lane("fx20-test", 30.0, shared=False)
    lane.reserve()  # ilk sıra alındı; sonraki 30 sn sonra
    monkeypatch.setattr(throttle, "reserve", lane.reserve)
    sent: List[str] = []

    def no_request(url: str, **kwargs: Any) -> Any:
        sent.append(url)
        raise AssertionError("no request may be sent")

    monkeypatch.setattr(transport.cffi_requests, "get", no_request)
    return lane, sent


def test_a_search_left_by_its_client_is_never_sent(store: Store, slow_budget: Any) -> None:
    lane, sent = slow_budget
    service = follows_module.FollowsService(store, follows_module.ConfigLeagues())
    request = _Gone(after=0.3)
    started = time.monotonic()
    with pytest.raises(FetchCancelled):
        asyncio.run(tournaments_routes.run_while_connected(
            request, lambda: service.search("la", kinds=("tournament", "team", "player"))))  # type: ignore[arg-type]
    assert time.monotonic() - started < 5.0
    assert sent == [] and request.asked == 1
    # the turn was given back: the next one waits about 30 s, not 60 s
    assert float(lane.reserve()) < 31.0


def test_a_connected_client_gets_its_answer(store: Store, fake: FakeSofaScore) -> None:
    service = follows_module.FollowsService(store, follows_module.ConfigLeagues())
    hits = asyncio.run(tournaments_routes.run_while_connected(
        _Gone(after=3600), lambda: service.search("galatasaray", kinds=("team",))))  # type: ignore[arg-type]
    assert [h.id for h in hits] == [3061]


def test_a_closed_connection_through_the_whole_app(store: Store, slow_budget: Any) -> None:
    """Uçtan uca, ASGI üzerinden (güvenlik ara katmanı dahil): gövde gelir, sonra istemci bağlantıyı keser."""
    lane, sent = slow_budget
    body = json.dumps({"q": "la", "kinds": ["tournament", "team", "player"]}).encode()
    gone = threading.Event()
    statuses: List[int] = []

    async def scenario() -> None:
        first = [True]

        async def receive() -> Dict[str, Any]:
            if first[0]:
                first[0] = False
                return {"type": "http.request", "body": body, "more_body": False}
            while not gone.is_set():
                await asyncio.sleep(0.02)
            return {"type": "http.disconnect"}

        async def send(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/api/v1/tournaments/search", "raw_path": b"/api/v1/tournaments/search",
            "query_string": b"", "root_path": "",
            "headers": [(b"host", b"127.0.0.1:8000"), (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
            "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8000),
        }
        call = asyncio.ensure_future(app(scope, receive, send))
        await asyncio.sleep(0.3)
        assert not call.done()  # waiting for its turn in the budget
        gone.set()
        await asyncio.wait_for(call, timeout=5.0)

    asyncio.run(scenario())
    assert sent == []
    assert statuses in ([], [tournaments_routes.CLIENT_CLOSED])
    assert float(lane.reserve()) < 31.0
