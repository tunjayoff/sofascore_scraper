"""
Push kaynağı `page` (sofascore_scraper/services/live/push_source.py) ve servisteki yeri (supervisor.py; plan maddesi P24).

Gerçek tarayıcı ve ağ yok. Kareler kayıttandır: tests/fixtures/push/recorded_wire.jsonl, sitenin kendi
bağlantısında dinlenmiş karelerin gövdeleri (research/all_sports/ws.jsonl) NATS metin protokolünde yeniden
yazılmış hali; kimlik bilgisi, istemci IP'si ve sunucu bilgisi içermez (INFO'dan yalnızca version,
auth_required, tls_required). Sayfa, Playwright'ın olay arayüzünü taklit eden sahte bir sayfadır.

  * ayrıştırıcı: kayıtlı kareler, bölünmüş ve birleşik kareler, HMSG, bozuk başlık, INFO gövdesinin atılması
  * birleştirme: kare son bilinen nesneye yazılır; eski yoklama yeniyi ezmez
  * servis: push'tan gelen bitiş tek olaydır ve tek /event ile onaylanır; aynı geçişi gören yoklama ikinci olay
    yazmaz; kaçan kare bir sonraki yoklama turuyla iyileşir; kaynak değişiklikleri `system.live_source_changed`
  * kimlik bilgisi: giden karelere dinleyici kurulmaz; sahte kimlik bilgisi ve IP hiçbir yere yazılmaz
  * gerçek açıcı (BrowserPageOpener) sahte bir köprüyle: profil, sayfa adresi, istek kuralları, captcha, kapanış
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

import conftest
from sofascore_scraper.services.live import push_source as ps
from sofascore_scraper.services.live.push_source import (
    LastKnown,
    NatsReader,
    PageSource,
    PushFeed,
    attach,
    frame_body,
    merge_frame,
    route_action,
)
from sofascore_scraper.services.live.supervisor import LiveService, explicit_scope
from sofascore_scraper.status import StatusClass, classify_status
from sofascore_scraper.store import Store, open_store
from test_live_service import FB_DONE, FB_LIVE, Clock, FakeApi, Stop, fetched, fx

PUSH_FIXTURES = Path(__file__).parent / "fixtures" / "push"


def recorded() -> List[Dict[str, Any]]:
    lines = (PUSH_FIXTURES / "recorded_wire.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def recorded_body(predicate: Callable[[Dict[str, Any]], bool]) -> Dict[str, Any]:
    for row in recorded():
        ops = NatsReader().feed(row["data"])
        if ops and ops[0].kind == "MSG":
            body = frame_body(ops[0])
            if body is not None and predicate(body):
                return body
    raise AssertionError("no recorded frame matches")


def wire(subject: str, body: Dict[str, Any], sid: int = 1) -> str:
    payload = json.dumps(body, separators=(",", ":"))
    return f"MSG {subject} {sid} {len(payload.encode('utf-8'))}\r\n{payload}\r\n"


FINISH = recorded_body(lambda b: b.get("status.type") == "finished" and "homeScore.current" in b)
INFO = "INFO " + json.dumps({"version": "2.12.15", "auth_required": True, "tls_required": True}) + "\r\n"


# --- ayrıştırıcı -----------------------------------------------------------------------------------


def test_every_recorded_frame_is_read() -> None:
    reader = NatsReader()
    kinds: Dict[str, int] = defaultdict(int)
    subjects = set()
    for row in recorded():
        for op in reader.feed(row["data"]):
            kinds[op.kind] += 1
            subjects.add(op.subject)
            if op.kind == "MSG":
                assert frame_body(op) is not None
    rows = recorded()
    assert kinds["INFO"] == 1 and kinds["MSG"] == len(rows) - 1
    assert {"sport.football", "sport.tennis"} <= subjects and any(s.startswith("odds.") for s in subjects)


def test_ops_split_across_frames_and_joined_in_one_frame_are_read() -> None:
    data = (INFO + "PING\r\n" + wire("sport.football", FINISH) + "PONG\r\n").encode("utf-8")
    reader = NatsReader()
    ops = []
    for i in range(0, len(data), 7):  # 7 baytlık parçalar: başlık ve gövde kare sınırında bölünür
        ops.extend(reader.feed(data[i:i + 7]))
    assert [op.kind for op in ops] == ["INFO", "PING", "MSG", "PONG"]
    assert frame_body(ops[2]) == FINISH
    assert NatsReader().feed(data)[2].payload == ops[2].payload


def test_the_info_body_is_never_kept() -> None:
    op = NatsReader().feed('INFO {"server_id":"x","client_ip":"203.0.113.7","version":"2.12.15"}\r\n')[0]
    assert op.kind == "INFO" and op.payload == b"" and op.text == "" and "203.0.113.7" not in repr(op)


def test_hmsg_bad_headers_errors_and_unknown_ops() -> None:
    body = json.dumps({"id": 7, "status.code": 6}).encode()
    header = b"NATS/1.0\r\n\r\n"
    data = (b"HMSG sport.football 1 %d %d\r\n" % (len(header), len(header) + len(body)) + header + body + b"\r\n"
            + b"MSG sport.football 1 x\r\n" + b"-ERR 'Authorization Violation' <\x01>\r\n" + b"+OK\r\nWHAT\r\n")
    ops = NatsReader().feed(data)
    assert [op.kind for op in ops] == ["MSG", "-ERR", "+OK", "OTHER"]
    assert frame_body(ops[0]) == {"id": 7, "status.code": 6}
    assert ops[1].text == "Authorization Violation"
    assert NatsReader().feed("-ERR 'something else entirely'\r\n")[0].text == "other"


def test_a_stream_that_never_completes_is_given_up() -> None:
    reader = NatsReader()
    reader.feed(f"MSG sport.football 1 {ps.MAX_PENDING_BYTES * 2}\r\n")
    reader.feed(b"x" * (ps.MAX_PENDING_BYTES + 1))
    assert reader.broken and reader.feed(INFO) == []


def test_frames_without_an_integer_id_or_not_objects_are_dropped() -> None:
    for payload in (b"[1]", b"{\"id\": \"1\"}", b"not json", b"\xff"):
        assert frame_body(ps.NatsOp("MSG", "sport.football", payload)) is None


def test_only_the_sport_subject_and_event_subjects_are_wanted() -> None:
    assert ps.subject_wanted("sport.football", "football")
    assert ps.subject_wanted("event.17212341", "football")
    assert not ps.subject_wanted("sport.tennis", "football")
    assert not ps.subject_wanted("odds.433067180", "football")
    assert not ps.subject_wanted("event.x", "football")


# --- birleştirme -----------------------------------------------------------------------------------


def test_a_recorded_finish_frame_completes_the_last_known_event() -> None:
    base = fx(FB_LIVE, FINISH["id"])
    before = copy.deepcopy(base)
    merged = merge_frame(base, FINISH)
    assert base == before  # verilen nesne değişmez
    assert classify_status(merged) is StatusClass.COMPLETED
    assert merged["homeScore"]["current"] == FINISH["homeScore.current"]
    assert merged["homeScore"]["period1"] == base["homeScore"]["period1"]  # karede olmayan alan kalır
    assert merged["winnerCode"] == FINISH["winnerCode"] and merged["lastPeriod"] is None
    assert merged["changes"]["changeTimestamp"] == FINISH["changes.changeTimestamp"]


def test_a_code_only_frame_drops_a_stale_type_that_contradicts_it() -> None:
    base = {"id": 1, "status": {"code": 0, "type": "notstarted", "description": "Not started"}}
    # kopma sırasında başlama karesi (tür değişikliği) kaçtı; sonraki kare yalnız kodu taşır
    merged = merge_frame(base, {"id": 1, "status.code": 7, "status.description": "2nd half"})
    assert "type" not in merged["status"] and classify_status(merged) is StatusClass.LIVE
    kept = merge_frame({"id": 1, "status": {"code": 6, "type": "inprogress"}}, {"id": 1, "status.code": 7})
    assert kept["status"]["type"] == "inprogress"


def test_a_frame_creates_missing_nodes() -> None:
    merged = merge_frame({"id": 1, "time": 5}, {"id": 1, "time.played": 600, "homeScore.point": "15"})
    assert merged["time"] == {"played": 600} and merged["homeScore"] == {"point": "15"}


def test_an_older_poll_does_not_replace_a_newer_push_frame() -> None:
    known = LastKnown()
    live = fx(FB_LIVE, 5)
    assert known.seed(live) and known.apply(6, {"id": 6}) is None
    pushed = known.apply(5, {"id": 5, "homeScore.current": 2, "changes.changeTimestamp": 1790687900})
    assert pushed is not None and pushed["homeScore"]["current"] == 2
    assert not known.seed(live)  # CDN'den gelen eski liste (changeTimestamp daha küçük)
    assert known.get(5)["homeScore"]["current"] == 2
    newer = copy.deepcopy(live)
    newer["changes"]["changeTimestamp"] = 1790687950
    assert known.seed(newer) and known.seed(live)  # yoklamanın kendisi tutulunca karşılaştırma yok


def test_the_last_known_store_is_bounded() -> None:
    known = LastKnown(cap=3)
    for n in range(5):
        known.seed({"id": n})
    assert len(known) == 3 and 0 not in known and 4 in known


# --- kuyruk ve sayfa -------------------------------------------------------------------------------


class FakeWebSocket:
    """Playwright WebSocket'inin olay arayüzü."""

    def __init__(self) -> None:
        self.handlers: Dict[str, List[Callable[..., Any]]] = defaultdict(list)

    def on(self, name: str, handler: Callable[..., Any]) -> None:
        self.handlers[name].append(handler)

    def receive(self, data: Any) -> None:
        for handler in self.handlers["framereceived"]:
            handler(data)

    def send(self, data: Any) -> None:
        for handler in self.handlers["framesent"]:
            handler(data)

    def close(self) -> None:
        for handler in self.handlers["close"]:
            handler(self)


class FakePage:
    """Playwright sayfasının olay arayüzü; sitenin kodu gibi bağlanır, abone olur ve yeniden bağlanır."""

    def __init__(self) -> None:
        self.handlers: Dict[str, List[Callable[..., Any]]] = defaultdict(list)
        self.sockets: List[FakeWebSocket] = []

    def on(self, name: str, handler: Callable[..., Any]) -> None:
        self.handlers[name].append(handler)

    def emit(self, name: str, *args: Any) -> None:
        for handler in self.handlers[name]:
            handler(*args)

    def connect(self, credential: str = "", info: str = INFO, sport: str = "football") -> FakeWebSocket:
        ws = FakeWebSocket()
        self.sockets.append(ws)
        self.emit("websocket", ws)
        ws.receive(info.encode("utf-8"))  # nats.ws ikili kare gönderir
        ws.send(f'CONNECT {{"user":"{credential}","pass":"{credential}","lang":"nats.ws"}}\r\n')
        ws.send(f"SUB sport.{sport} 1\r\n")
        return ws

    @property
    def ws(self) -> FakeWebSocket:
        return self.sockets[-1]


class FakeOpener:
    """Sayfa açıcının sahtesi: `attach` ile gerçek dinleyicileri sahte sayfaya bağlar."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.pages: Dict[str, FakePage] = {}
        self.opened: List[str] = []
        self.closed = False

    def open(self, sport: str, feed: PushFeed) -> Any:
        self.opened.append(sport)
        if self.fail:
            raise RuntimeError("no browser here")
        page = FakePage()
        attach(page, feed)
        self.pages[sport] = page
        return SimpleNamespace(ready=True, failed=None, close=lambda: None)

    def close(self) -> None:
        self.closed = True


def test_other_websockets_of_the_page_are_ignored() -> None:
    feed = PushFeed("football", clock=lambda: 1.0)
    page = FakePage()
    attach(page, feed)
    other = FakeWebSocket()
    page.emit("websocket", other)
    other.receive('{"tracker": 1}\r\n')  # başka bir sağlayıcının akışı
    other.receive(wire("sport.football", FINISH))
    assert feed.drain() == []
    page.connect()
    page.ws.receive("PING\r\n" + wire("sport.football", FINISH) + wire("odds.1", {"id": 1}))
    kinds = [s.kind for s in feed.drain()]
    assert kinds == ["open", "ping", "frame"]


def test_no_listener_is_attached_to_outgoing_frames() -> None:
    feed = PushFeed("football")
    page = FakePage()
    attach(page, feed)
    ws = page.connect(credential="fake-" + "credential")
    assert not ws.handlers.get("framesent") and {k for k, v in ws.handlers.items() if v} == {"framereceived", "close"}


def test_a_closed_connection_and_a_closed_page_are_reported() -> None:
    feed = PushFeed("football", clock=lambda: 2.0)
    page = FakePage()
    attach(page, feed)
    first = page.connect()
    second = page.connect()  # yeniden bağlanma: eskisi kapanmadan yenisi açılabilir
    first.close()
    assert [s.kind for s in feed.drain()] == ["open", "open"]
    second.close()
    assert [s.kind for s in feed.drain()] == ["close"]
    page.connect()
    page.emit("crash", page)
    assert [(s.kind, s.text) for s in feed.drain()] == [("open", ""), ("close", ""), ("gone", "page crashed")]


def test_an_overflowing_queue_reports_a_gap() -> None:
    feed = PushFeed("football", max_queue=3)
    page = FakePage()
    attach(page, feed)
    page.connect()
    for n in range(5):
        page.ws.receive(wire("sport.football", {"id": n, "status.code": 6}))
    signals = feed.drain()
    assert "gap" in [s.kind for s in signals] and feed.dropped >= 2


def test_the_page_source_reopens_a_closed_page_with_back_off() -> None:
    clock = Clock(1000.0)
    opener = FakeOpener()
    source = PageSource("football", opener, clock=clock)
    source.ensure_open()
    source.ensure_open()
    assert opener.opened == ["football"]
    opener.pages["football"].emit("close", None)
    assert [s.kind for s in source.drain()] == ["gone"]
    source.ensure_open()
    assert opener.opened == ["football"]  # bekleme süresi dolmadı
    clock.now += ps.OPEN_RETRY_FIRST_SECONDS
    source.ensure_open()
    assert opener.opened == ["football", "football"]
    opener.pages["football"].connect()
    assert [s.kind for s in source.drain()] == ["open"] and source.failures == 0


def test_the_page_source_survives_an_opener_that_raises() -> None:
    clock = Clock(1000.0)
    source = PageSource("football", FakeOpener(fail=True), clock=clock)
    source.ensure_open()
    assert [s.kind for s in source.drain()] == ["gone"] and source.failures == 1
    assert source.retry_at == 1000.0 + ps.OPEN_RETRY_FIRST_SECONDS


@pytest.mark.parametrize("url, kind, since, expected", [
    ("https://www.sofascore.com/tr/football", "document", 0, "continue"),
    ("https://www.sofascore.com/_next/static/app.js", "script", 999, "continue"),
    ("https://img.sofascore.com/api/v1/team/1/image", "image", 0, "abort"),
    ("https://www.sofascore.com/static/font.woff2", "font", 0, "abort"),
    ("https://www.sofascore.com/api/v1/sport/football/events/live", "fetch", 10, "throttle"),
    ("https://www.sofascore.com/api/v1/sport/football/events/live", "fetch", 60, "abort"),
    ("https://www.sofascore.com/api/v1/token/push", "xhr", 3600, "throttle"),
    ("https://www.sofascore.com/api/v1/config/country", "fetch", 3600, "throttle"),
    ("https://pagead2.googlesyndication.com/pagead/js", "script", 0, "abort"),
    ("https://analytics.google.com/g/collect", "fetch", 0, "abort"),
    ("https://challenges.cloudflare.com/turnstile/v0/api.js", "script", 0, "continue"),
    ("https://challenges.cloudflare.com/x.png", "image", 0, "abort"),
    ("https://notsofascore.com/x", "script", 0, "abort"),
    ("data:image/png;base64,AAAA", "image", 0, "continue"),
])
def test_the_page_requests_are_throttled_or_blocked(url: str, kind: str, since: float, expected: str) -> None:
    assert route_action(url, kind, since) == expected


def test_the_live_profile_is_next_to_the_bridge_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SOFASCORE_BROWSER_PROFILE", str(tmp_path / "chrome") + "/")
    assert ps.live_profile_dir() == str(tmp_path / "chrome") + "-live"


def test_the_sport_page_and_the_quiet_page_follow_the_bridge_home(monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper.client import bridge

    monkeypatch.setattr(bridge, "HOME_URL", "https://www.sofascore.com/tr")
    assert ps.sport_page_url("ice-hockey") == "https://www.sofascore.com/tr/ice-hockey"
    assert ps.quiet_url() == "https://www.sofascore.com/robots.txt"


# --- servis: push + yoklama --------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(path))
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "48")
    return path


@pytest.fixture
def store(data_dir: Path) -> Store:
    return open_store(data_dir)


def page_service(store: Store, api: FakeApi, clock: Clock, opener: Any, **kwargs: Any) -> LiveService:
    return LiveService(store, explicit_scope(["football"], tournament_ids=[17]), fetch=api, clock=clock,
                       sleep=clock.sleep, requested_source="page", page_opener=opener, **kwargs)


def stream(store: Store, name: str) -> List[Any]:
    return list(store.streams.read(streams=[name]).events)


def finish_frame(eid: int, start_ts: float) -> Dict[str, Any]:
    """Kayıtlı bitiş karesi, bu maçın id'siyle ve değişiklik anı maçın başlangıcından sonra."""
    frame = dict(FINISH)
    frame["id"] = eid
    frame["homeScore.normaltime"], frame["awayScore.normaltime"] = FINISH["homeScore.current"], FINISH["awayScore.current"]
    frame["changes.changeTimestamp"] = int(start_ts + 2 * 3600)
    return frame


def scenario(clock_offset: float = 0.0):
    live = fx(FB_LIVE, 500, tournament_id=17)
    done = fx(FB_DONE, 500, tournament_id=17)
    done["startTimestamp"] = live["startTimestamp"]
    api = FakeApi({"football": [live]}, {500: live})
    clock = Clock(fetched(FB_LIVE) + clock_offset)
    return live, done, api, clock


def test_a_finish_from_push_is_one_event_confirmed_with_one_request(store: Store) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect()
        if n == 2:
            api.live["football"] = []
            api.events[500] = done
            page.ws.receive(wire("sport.football", finish_frame(500, live["startTimestamp"])).encode())

    report = page_service(store, api, clock, opener).run(Stop(clock, rounds=4, on_wait=script))
    events = stream(store, "live")
    assert [(e.type, e.source) for e in events] == [("live.status_changed", "page")]
    assert (events[0].data["from"], events[0].data["to"]) == ("live", "completed")
    assert events[0].data["score"]["home"] == FINISH["homeScore.current"]
    # Yoklama turları (açılış, bağlanma) ve bitişe yakın maçın sayfası; bitiş onayı tek istek
    assert api.calls == ["/sport/football/events/live", "/event/500", "/sport/football/events/live", "/event/500"]
    assert report.confirmed == 1 and report.push_frames == 1 and report.source == "page"
    assert store.events.get(500) is not None
    changes = [(e.type, e.data) for e in stream(store, "system")]
    assert changes == [("system.live_source_changed",
                        {"sport": "football", "from": "poll", "to": "page", "reason": "push_connected"})]
    assert report.leaders == {"football": "page"} and opener.closed


def test_a_stale_event_page_is_not_stored_and_the_confirmation_is_retried(store: Store) -> None:
    from sofascore_scraper.services.live import supervisor

    live, done, api, clock = scenario()
    opener = FakeOpener()

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect()
        if n == 2:  # push bitişi gösterir; maç sayfası (CDN) hâlâ canlı der
            page.ws.receive(wire("sport.football", finish_frame(500, live["startTimestamp"])))
        if n == 10:
            api.events[500] = done

    report = page_service(store, api, clock, opener).run(Stop(clock, rounds=30, on_wait=script))
    assert [e.type for e in stream(store, "live")] == ["live.status_changed"]
    assert report.confirmed == 1 and store.events.get(500) is not None
    assert api.calls == ["/sport/football/events/live", "/event/500",  # açılış turu (bitişe yakın maç)
                         "/sport/football/events/live",  # bağlanma turu
                         "/event/500",  # bitişte: sayfa hâlâ canlı der, saklanmaz
                         "/event/500"]  # CONFIRM_RETRY_SECONDS sonra: bitmiş, saklanır
    assert supervisor.CONFIRM_RETRY_SECONDS == 20.0


def test_a_transition_seen_by_poll_and_by_push_is_stored_once(store: Store) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()
    scored = copy.deepcopy(live)
    scored["homeScore"].update(current=2, display=2)
    scored["changes"]["changeTimestamp"] += 30

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect()
            api.live["football"] = [scored]  # yoklama skoru push'tan önce görür (bağlanma sonrası tur)
        if n == 2:
            frame = {"id": 500, "homeScore.current": 2, "homeScore.display": 2,
                     "changes.changeTimestamp": scored["changes"]["changeTimestamp"]}
            page.ws.receive(wire("sport.football", frame))

    page_service(store, api, clock, opener).run(Stop(clock, rounds=4, on_wait=script))
    assert [(e.type, e.source) for e in stream(store, "live")] == [("live.score_changed", "poll")]


def test_a_missed_frame_is_healed_by_the_next_poll_round(store: Store) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect()
        if n == 3:  # bağlantı düşer; bitiş karesi bu arada gelir ve kaçar
            page.ws.close()
            api.live["football"] = []
            api.events[500] = done

    report = page_service(store, api, clock, opener).run(Stop(clock, rounds=5, on_wait=script))
    events = stream(store, "live")
    assert [(e.type, e.source, e.data["to"]) for e in events] == [("live.status_changed", "poll", "completed")]
    reasons = [(e.data["from"], e.data["to"], e.data["reason"]) for e in stream(store, "system")]
    assert reasons == [("poll", "page", "push_connected"), ("page", "poll", "push_disconnected")]
    assert report.source_switches == 2 and report.leaders == {"football": "poll"}


def test_an_older_poll_after_a_push_frame_does_not_move_the_score_back(store: Store) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect()
        if n == 2:
            frame = {"id": 500, "homeScore.current": 2, "homeScore.display": 2,
                     "changes.changeTimestamp": live["changes"]["changeTimestamp"] + 60}
            page.ws.receive(wire("sport.football", frame))
        if n == 3:  # yeniden bağlanma → yoklama turu; liste (CDN) bir önceki hali verir
            page.ws.close()
            page.connect()

    page_service(store, api, clock, opener).run(Stop(clock, rounds=5, on_wait=script))
    events = stream(store, "live")
    assert [(e.type, e.source, e.data["to"]) for e in events] == [("live.score_changed", "page",
                                                                   {"home": 2, "away": 0})]
    assert api.calls.count("/sport/football/events/live") == 3  # açılış, bağlanma, yeniden bağlanma


def test_an_event_push_shows_first_is_looked_up_once(store: Store) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()
    other = fx(FB_DONE, 901, tournament_id=99)
    newcomer = fx(FB_DONE, 900, tournament_id=17)
    api.events.update({900: newcomer, 901: other})

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect()
        if n == 2:
            for eid in (900, 901, 901, 777):
                page.ws.receive(wire("sport.football", {"id": eid, "status.code": 100, "status.type": "finished"}))
            page.ws.receive(wire("sport.football", {"id": 778, "homeScore.current": 1}))  # durum değil: sorulmaz

    page_service(store, api, clock, opener, confirm=False).run(Stop(clock, rounds=3, on_wait=script))
    assert [api.calls.count(f"/event/{eid}") for eid in (900, 901, 777, 778)] == [1, 1, 1, 0]
    state = store.watch.load("football")
    assert state["900"]["class"] == "completed" and "901" not in state
    assert stream(store, "live") == []  # önceki hali bilinmeyen maç: geçiş olayı yok (yoklamanın ilk görüşü gibi)


def test_a_page_that_cannot_open_leaves_the_service_polling(store: Store) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener(fail=True)
    report = page_service(store, api, clock, opener).run(Stop(clock, rounds=65))
    assert report.rounds == 3  # 0, 30, 60. sn: poll_interval
    assert opener.opened == ["football"] * 4  # 0, 5, 15, 35. sn: artan aralıklarla yeniden
    assert stream(store, "system") == [] and report.leaders == {"football": "poll"}


def test_the_lookups_of_unknown_events_are_budgeted(store: Store) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect()
            for eid in range(1000, 1020):
                page.ws.receive(wire("sport.football", {"id": eid, "status.code": 6, "status.type": "inprogress"}))

    page_service(store, api, clock, opener).run(Stop(clock, rounds=3, on_wait=script))
    looked_up = [c for c in api.calls if c.startswith("/event/1")]
    assert len(looked_up) == 6  # UNKNOWN_LOOKUPS_PER_MINUTE


def test_no_credential_or_client_address_reaches_a_file_a_log_or_an_event(store: Store, data_dir: Path,
                                                                           caplog: pytest.LogCaptureFixture) -> None:
    live, done, api, clock = scenario()
    opener = FakeOpener()
    credential = "fake-" + "push-" + "credential-0000"
    address = "203.0.113." + "77"  # belgelere ayrılmış adres bloğu
    info = "INFO " + json.dumps({"version": "2.12.15", "auth_required": True, "client_ip": address,
                                 "server_id": "fake-server"}) + "\r\n"

    def script(n: int) -> None:
        page = opener.pages["football"]
        if n == 1:
            page.connect(credential=credential, info=info)
        if n == 2:
            api.events[500] = done
            page.ws.receive("-ERR 'Authorization Violation' " + credential + "\r\n")
            page.ws.receive(wire("sport.football", finish_frame(500, live["startTimestamp"])))

    with caplog.at_level(logging.DEBUG):
        page_service(store, api, clock, opener).run(Stop(clock, rounds=4, on_wait=script))
    assert stream(store, "live")  # senaryo gerçekten koştu
    store.close()
    for needle in (credential, address, "fake-server"):
        assert needle not in caplog.text
        for path in data_dir.rglob("*"):
            if path.is_file():
                assert needle.encode() not in path.read_bytes(), path


# --- gerçek açıcı, sahte köprüyle ------------------------------------------------------------------


class FakeRoute:
    def __init__(self, url: str, kind: str, page: Any) -> None:
        self.request = SimpleNamespace(url=url, resource_type=kind, frame=SimpleNamespace(page=page))
        self.result: Optional[str] = None

    async def abort(self, reason: str = "") -> None:
        self.result = "abort"

    async def continue_(self) -> None:
        self.result = "continue"


class FakeAsyncPage(FakePage):
    def __init__(self, captcha_first: bool = False) -> None:
        super().__init__()
        self.url = ""
        self.visits: List[str] = []
        self.captcha_first = captcha_first
        self.closed = False

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.visits.append(url)
        self.url = url
        if self.captcha_first and len(self.visits) == 1:
            self.url = "https://www.sofascore.com/captcha.html?redirectUrl=x"

    async def close(self) -> None:
        self.closed = True


class FakeBridge:
    made: List["FakeBridge"] = []

    def __init__(self, profile_dir: str, home_url: Optional[str] = None, *, captcha_first: bool = False,
                 fail: bool = False) -> None:
        self.profile_dir, self.home_url = profile_dir, home_url
        self.routes: List[Any] = []
        self.pages: List[FakeAsyncPage] = []
        self.solved = 0
        self.closed = False
        self.captcha_first = captcha_first
        self.fail = fail
        self.unrouted: List[str] = []
        self.context = SimpleNamespace(route=self._route, new_page=self._new_page, unroute_all=self._unroute_all)
        FakeBridge.made.append(self)

    async def _unroute_all(self, behavior: Optional[str] = None) -> None:
        assert not self.closed, "the rules are removed before the browser closes"
        self.unrouted.append(str(behavior))

    async def ensure_ready(self) -> None:
        if self.fail:
            raise RuntimeError("cannot launch")

    async def _route(self, pattern: str, handler: Any) -> None:
        self.routes.append((pattern, handler))

    async def _new_page(self) -> FakeAsyncPage:
        page = FakeAsyncPage(self.captcha_first)
        self.pages.append(page)
        return page

    async def solve_challenge(self) -> str:
        self.solved += 1
        return "solved"

    async def close(self) -> None:
        self.closed = True


def wait_for(predicate: Callable[[], bool], seconds: float = 5.0) -> None:
    end = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


@pytest.fixture
def fake_bridge(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    from sofascore_scraper.client import bridge

    FakeBridge.made = []

    def install(**options: Any) -> None:
        monkeypatch.setattr(bridge, "BrowserBridge", lambda profile_dir, home_url=None, report_health=True: FakeBridge(
            profile_dir, home_url, **options))

    install()
    return install


def test_the_opener_uses_the_live_profile_and_opens_the_sport_page(fake_bridge: Any, tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper.client import bridge

    monkeypatch.setattr(bridge, "HOME_URL", "https://www.sofascore.com/tr")
    opener = ps.BrowserPageOpener(profile_dir=str(tmp_path / "chrome-live"))
    feed = PushFeed("football")
    handle = opener.open("football", feed)
    wait_for(lambda: handle.ready)
    b = FakeBridge.made[0]
    assert b.profile_dir == str(tmp_path / "chrome-live") and b.home_url == "https://www.sofascore.com/robots.txt"
    page = b.pages[0]
    assert page.visits == ["https://www.sofascore.com/tr/football"] and len(b.routes) == 1
    page.connect()
    assert [s.kind for s in feed.drain()] == ["open"]

    second = opener.open("tennis", PushFeed("tennis"))
    wait_for(lambda: second.ready)
    assert len(FakeBridge.made) == 1 and len(b.routes) == 1  # tek tarayıcı, kurallar bir kez

    handler = b.routes[0][1]
    results = []
    for url, kind in (("https://www.google-analytics.com/collect", "fetch"),
                      ("https://www.sofascore.com/api/v1/sport/football/events/live", "fetch")):
        route = FakeRoute(url, kind, page)
        with monkeypatch.context() as m:
            waited: List[str] = []

            async def slot(waited: List[str] = waited, url: str = url) -> None:
                waited.append(url)

            m.setattr(bridge, "_wait_for_slot", slot)
            asyncio.run(handler(route))
        results.append((route.result, bool(waited)))
    assert results == [("abort", False), ("continue", True)]

    handle.close()
    assert page.closed and b.unrouted == []  # tek sayfa kapanır: kurallar öteki sayfalar için kalır
    opener.close()
    assert b.closed and b.unrouted == ["ignoreErrors"]


class PendingRoute(FakeRoute):
    """
    Playwright'ın Route'u gibi: işleyici bitince `_on_route` görevi `handled` sonucunu bekler; bu sonucu
    yalnızca başarılı bir continue_/abort ya da fallback verir. Kapanan sayfada continue_/abort hata verir.
    """

    def __init__(self, url: str, kind: str, page: Any) -> None:
        super().__init__(url, kind, page)
        self.handled: "asyncio.Future[bool]" = asyncio.get_running_loop().create_future()

    async def abort(self, reason: str = "") -> None:
        raise RuntimeError("Target page, context or browser has been closed")

    async def continue_(self) -> None:
        raise RuntimeError("Target page, context or browser has been closed")

    async def fallback(self) -> None:
        if self.handled.done():
            raise RuntimeError("Route is already handled!")
        self.handled.set_result(False)


def test_a_request_that_fails_while_the_page_closes_is_handed_back(fake_bridge: Any, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """V9: kapanırken başarısız olan istek bırakılırsa Playwright'ın `_on_route` görevi asılı kalır ve çöpe gider."""
    from sofascore_scraper.client import bridge

    async def slot() -> None:
        pass

    monkeypatch.setattr(bridge, "_wait_for_slot", slot)
    opener = ps.BrowserPageOpener(profile_dir=str(tmp_path / "p"))
    handle = opener.open("football", PushFeed("football"))
    wait_for(lambda: handle.ready)
    b = FakeBridge.made[0]
    handler, page = b.routes[0][1], b.pages[0]

    async def on_route(url: str, kind: str) -> bool:
        route = PendingRoute(url, kind, page)
        await handler(route)
        return await asyncio.wait_for(route.handled, 1.0)  # asılı kalsaydı zaman aşımı

    for url, kind in (("https://www.google-analytics.com/collect", "fetch"),
                      ("https://www.sofascore.com/api/v1/sport/football/events/live", "fetch"),
                      ("https://www.sofascore.com/football", "document")):
        assert asyncio.run(on_route(url, kind)) is False
    opener.close()


def test_the_opener_solves_a_captcha_once_and_reports_a_failed_start(fake_bridge: Any, tmp_path: Path) -> None:
    fake_bridge(captcha_first=True)
    opener = ps.BrowserPageOpener(profile_dir=str(tmp_path / "p"))
    handle = opener.open("football", PushFeed("football"))
    wait_for(lambda: handle.ready)
    b = FakeBridge.made[0]
    assert b.solved == 1 and len(b.pages[0].visits) == 2
    opener.close()

    fake_bridge(fail=True)
    opener = ps.BrowserPageOpener(profile_dir=str(tmp_path / "q"))
    feed = PushFeed("football")
    handle = opener.open("football", feed)
    wait_for(lambda: handle.failed is not None)
    assert [(s.kind, s.text) for s in feed.drain()] == [("gone", "open failed: RuntimeError")]
    opener.close()


def test_the_browser_library_offers_what_the_close_path_uses() -> None:
    """V9 düzeltmesinin dayandığı API: `BrowserContext.unroute_all(behavior=...)` ve `Route.fallback()`."""
    import inspect

    api = pytest.importorskip("patchright.async_api")
    assert "behavior" in inspect.signature(api.BrowserContext.unroute_all).parameters
    assert callable(getattr(api.Route, "fallback", None))


assert conftest  # sınır denetimi ve kancalar conftest'te kurulur
