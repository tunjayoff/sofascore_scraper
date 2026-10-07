"""
Webhook sözleşmesi (plan maddesi P22; docs/design/02-services.md bölüm 5.3), bu makinede çalışan gerçek bir
HTTP sunucusuna karşı: gövde ve başlıklar, HMAC imzası, sıra, toplu gönderim, yeniden deneme (sahte saatle),
yeniden başlamadan sonra yineleme, 410 ile kapanma, yaş sınırı ve tek seferlik komutun çıkıştaki teslimi.

Sunucu yalnızca 127.0.0.1'i dinler; hiçbir test dışarıya istek atmaz. Alıcı tarafın yapacağı doğrulama
(`verify_signature`) burada sözleşmenin parçası olarak sınanır.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import logging
import math
import socket
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

import conftest
from sofascore_scraper import sinks
from sofascore_scraper.config import SinkSpec
from sofascore_scraper.jobs.manager import JobManager, local_origin
from sofascore_scraper.jobs.model import JobKind
from sofascore_scraper.sinks.base import Envelope, EventFilter, FatalSinkError, RetryableSinkError
from sofascore_scraper.sinks.dispatcher import Dispatcher
from sofascore_scraper.sinks.webhook import (
    WEBHOOK_SCHEMA,
    WebhookSink,
    sign,
    signature_header,
    verify_signature,
)
from sofascore_scraper.store import JobStore, Store, StoreBusy, StreamEvent, open_store
from sofascore_scraper.version import __version__

# Gizli değerlerin yerini tutan sınama değerleri: bilerek sahte, çalışırken parçalardan kurulur (depoda gizli
# değere benzeyen bir sabit durmaz). Testler bu metinleri hata metinlerinde, loglarda ve olaylarda arar.
SECRET = "-".join(("fake", "contract", "signing", "value"))
PATH_PART = "-".join(("fake", "path", "part"))
QUERY_PART = "-".join(("fake", "query", "part"))
TOKEN_PATH = f"/services/T000/B000/{PATH_PART}?sig={QUERY_PART}"  # yolu ve sorgusu belirteç olan adres
T0 = 1_790_000_000.0
DAY = 86400.0


class FakeClock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Request:
    def __init__(self, path: str, headers: Dict[str, str], body: bytes) -> None:
        self.path = path
        self.headers = headers
        self.body = body

    @property
    def json(self) -> Dict[str, Any]:
        return json.loads(self.body)

    @property
    def seqs(self) -> List[int]:
        return [event["seq"] for event in self.json["events"]]


class Receiver:
    """
    Yerel alıcı: gelen her POST'u kaydeder ve sıradaki yanıtı verir. `responses` boşsa 200 döner. Bir yanıt
    (durum, başlıklar, gecikme saniyesi) üçlüsüdür.
    """

    def __init__(self) -> None:
        self.requests: List[Request] = []
        self.responses: List[Tuple[int, Dict[str, str], float]] = []
        self.always: Optional[int] = None
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - http.server'ın adı
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                receiver.requests.append(Request(self.path, {k: v for k, v in self.headers.items()}, body))
                if receiver.always is not None:
                    status, headers, delay = receiver.always, {}, 0.0
                elif receiver.responses:
                    status, headers, delay = receiver.responses.pop(0)
                else:
                    status, headers, delay = 200, {}, 0.0
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(status)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                except OSError:
                    pass  # istemci zaman aşımına uğrayıp bağlantıyı kapattı

            def do_GET(self) -> None:  # noqa: N802 - izlenen bir yönlendirme buraya düşerdi
                receiver.requests.append(Request(self.path, {"method": "GET"}, b""))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args: Any) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def url(self, path: str = "/hooks/sofascore") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def respond(self, *statuses: int, headers: Optional[Dict[str, str]] = None, delay: float = 0.0) -> None:
        self.responses.extend((status, dict(headers or {}), delay) for status in statuses)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yerel sunucuya giden istek, testi çalıştıranın ortamındaki bir proxy'den geçmez."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    server = Receiver()
    yield server
    server.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    data_dir = tmp_path / "data"
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    return open_store(data_dir)


def emit(store: Store, type_: str = "live.status_changed", *, ts: Optional[float] = None, **fields: Any) -> int:
    data = fields.pop("data", {"from": "live", "to": "completed"})
    event = StreamEvent(type=type_, data=data, ts=ts, source=fields.pop("source", "poll"), **fields)
    seq = store.streams.append(type_.split(".")[0], [event])[0]
    assert seq is not None
    return seq


def hook(receiver: Receiver, clock: FakeClock, **kwargs: Any) -> WebhookSink:
    kwargs.setdefault("secret", SECRET)
    kwargs.setdefault("linger_seconds", 0.0)
    kwargs.setdefault("timeout_seconds", 5.0)
    events = EventFilter(kwargs.pop("events", ("*",)))
    return WebhookSink("ops", kwargs.pop("url", receiver.url()), events, clock=clock, **kwargs)


def dispatch(store: Store, sink: WebhookSink, clock: FakeClock) -> Dispatcher:
    dispatcher = Dispatcher(store, [sink], clock=clock, jitter=lambda: 1.0)
    dispatcher.register()
    return dispatcher


def last_error(store: Store, name: str = "ops") -> Optional[str]:
    row = store._state.connection().execute("SELECT last_error FROM sink_cursors WHERE sink = ?", (name,)).fetchone()
    return row[0] if row is not None else None


def envelope(seq: int = 1, type_: str = "live.status_changed", stream_id: str = "abc") -> Envelope:
    return Envelope(stream="live", seq=seq, type=type_, ts=T0, data={"n": seq}, event_id=7, sport="football",
                    tournament_id=17, source="poll", stream_id=stream_id)


# === istek =======================================================================================


def test_the_request_is_a_json_post_with_the_headers_of_the_contract(receiver: Receiver, clock: FakeClock):
    sink = hook(receiver, clock)
    sink.deliver([envelope(5), envelope(9)])
    (request,) = receiver.requests
    assert request.path == "/hooks/sofascore"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["User-Agent"] == f"sofascore-scraper/{__version__}"
    assert request.headers["X-Sofascore-Seq-First"] == "5" and request.headers["X-Sofascore-Seq-Last"] == "9"
    assert request.headers["Idempotency-Key"] == "abc:5-9"
    assert str(uuid.UUID(request.headers["X-Sofascore-Delivery"])) == request.headers["X-Sofascore-Delivery"]
    assert WEBHOOK_SCHEMA == "sofascore.webhook/1"
    assert request.json == {
        "schema": "sofascore.webhook/1",
        "sink": "ops",
        "events": [envelope(5).to_dict(), envelope(9).to_dict()],
    }
    assert list(request.json) == ["schema", "sink", "events"]
    assert request.body.isascii() and b"\n" not in request.body


def test_the_signature_is_hmac_sha256_of_timestamp_dot_body(receiver: Receiver, clock: FakeClock):
    hook(receiver, clock).deliver([envelope()])
    (request,) = receiver.requests
    header = request.headers["X-Sofascore-Signature"]
    timestamp, digest = (part.split("=", 1)[1] for part in header.split(","))
    assert header == f"t={int(T0)},v1={digest}" and timestamp == str(int(T0))
    expected = hmac.new(SECRET.encode(), f"{int(T0)}.".encode() + request.body, hashlib.sha256).hexdigest()
    assert digest == expected == sign(SECRET, int(T0), request.body)
    assert header == signature_header(SECRET.encode(), int(T0), request.body)  # anahtar bayt da olabilir


def test_a_receiver_can_verify_the_signature_and_reject_old_or_forged_requests(receiver: Receiver, clock: FakeClock):
    hook(receiver, clock).deliver([envelope()])
    (request,) = receiver.requests
    header = request.headers["X-Sofascore-Signature"]
    assert verify_signature(SECRET, header, request.body, now=T0 + 10)
    assert verify_signature(SECRET, header, request.body, tolerance=None)
    assert not verify_signature(SECRET, header, request.body, now=T0 + 301)  # eski istek (yeniden oynatma)
    assert not verify_signature(SECRET, header, request.body, now=T0 - 301)
    assert not verify_signature(SECRET, header, request.body)  # sahte saat geçmişte: gerçek saate göre eski
    assert not verify_signature("another secret", header, request.body, now=T0)
    assert not verify_signature(SECRET, header, request.body + b" ", now=T0)
    forged = f"t={int(T0) + 5},v1={header.split('v1=')[1]}"
    assert not verify_signature(SECRET, forged, request.body, now=T0)  # zaman damgası imzanın parçasıdır
    for malformed in (None, "", "v1=abc", "t=1", "t=x,v1=abc", "nonsense"):
        assert not verify_signature(SECRET, malformed, request.body, now=T0)


def test_a_forged_signature_with_other_characters_is_rejected_not_an_error(receiver: Receiver, clock: FakeClock):
    """Alıcı tarafı: ASCII olmayan ya da bozuk bir başlık doğrulamayı çökertmez (yanıt 401 olur, 500 değil)."""
    hook(receiver, clock).deliver([envelope()])
    request = receiver.requests[0]
    good = request.headers["X-Sofascore-Signature"]
    stamp = good.split(",")[0]
    for forged in (f"{stamp},v1=\u00e9\u00e9", f"{stamp},v1=" + "\u0131" * 64, f"{stamp},v1=\udcff", "t=\u0661,v1=ab",
                   f"{stamp},v1=" + good.split("v1=")[1].upper()):
        assert verify_signature(SECRET, forged, request.body, tolerance=None) is False, forged
    assert verify_signature(SECRET, good, request.body, tolerance=None)


def test_an_unsigned_webhook_sends_no_signature(receiver: Receiver, clock: FakeClock):
    sink = hook(receiver, clock, secret=None)
    sink.deliver([envelope()])
    assert not sink.signed and "X-Sofascore-Signature" not in receiver.requests[0].headers
    assert "Idempotency-Key" in receiver.requests[0].headers


def test_credentials_in_the_address_become_a_basic_authorization_header(receiver: Receiver, clock: FakeClock):
    parts = ("us@er", "p:ss")  # adresin kullanıcı bilgisi: yüzde kodlaması gereken karakterlerle, sahte değerler
    userinfo = ":".join(urllib.parse.quote(part, safe="") for part in parts)
    url = receiver.url("/hooks/x?a=1").replace("http://", "http://" + userinfo + "@")
    sink = hook(receiver, clock, url=url)
    sink.deliver([envelope()])
    (request,) = receiver.requests
    assert request.path == "/hooks/x?a=1"
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(":".join(parts).encode()).decode()
    assert userinfo.split(":")[0] not in repr(sink) and parts[0] not in repr(sink) and sink.host == "127.0.0.1"
    assert "Authorization" not in receiver.requests[0].headers.get("X-Sofascore-Signature", "")
    plain = hook(receiver, clock)
    plain.deliver([envelope()])
    assert "Authorization" not in receiver.requests[1].headers


def test_an_empty_batch_sends_nothing(receiver: Receiver, clock: FakeClock):
    hook(receiver, clock).deliver([])
    assert receiver.requests == []


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_every_2xx_is_success(receiver: Receiver, clock: FakeClock, status: int):
    receiver.respond(status)
    hook(receiver, clock).deliver([envelope()])
    assert len(receiver.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
def test_other_statuses_are_retried(receiver: Receiver, clock: FakeClock, status: int):
    receiver.respond(status)
    with pytest.raises(RetryableSinkError, match=f"the receiver answered HTTP {status}"):
        hook(receiver, clock).deliver([envelope()])


def test_410_is_fatal(receiver: Receiver, clock: FakeClock):
    receiver.respond(410)
    with pytest.raises(FatalSinkError, match="410 Gone"):
        hook(receiver, clock).deliver([envelope()])


def test_retry_after_is_passed_on(receiver: Receiver, clock: FakeClock):
    receiver.respond(429, headers={"Retry-After": "7"})
    receiver.respond(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})  # tarih biçimi yok sayılır
    sink = hook(receiver, clock)
    with pytest.raises(RetryableSinkError) as limited:
        sink.deliver([envelope()])
    with pytest.raises(RetryableSinkError) as unavailable:
        sink.deliver([envelope()])
    assert limited.value.retry_after == 7.0 and unavailable.value.retry_after is None


def test_a_redirect_is_not_followed(receiver: Receiver, clock: FakeClock):
    elsewhere = Receiver()
    try:
        receiver.respond(302, headers={"Location": elsewhere.url("/elsewhere")})
        receiver.respond(307, headers={"Location": elsewhere.url("/elsewhere")})
        sink = hook(receiver, clock)
        with pytest.raises(RetryableSinkError, match="the receiver answered HTTP 302"):
            sink.deliver([envelope()])
        with pytest.raises(RetryableSinkError, match="the receiver answered HTTP 307"):
            sink.deliver([envelope()])
        assert elsewhere.requests == []  # imzalı gövde yapılandırmada yazmayan bir adrese gitmez
    finally:
        elsewhere.close()


def test_a_refused_connection_is_retried_and_the_error_names_no_address(clock: FakeClock):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # kapatılınca kimsenin dinlemediği bir port
    sink = WebhookSink("ops", f"http://127.0.0.1:{port}/hooks/{PATH_PART}", secret=SECRET, clock=clock,
                       timeout_seconds=2.0)
    with pytest.raises(RetryableSinkError) as caught:
        sink.deliver([envelope()])
    text = str(caught.value)
    assert text.startswith("network error (") and PATH_PART not in text and SECRET not in text
    assert caught.value.__cause__ is None  # asıl istisna (adresi taşıyabilir) zincire eklenmez


def test_a_slow_receiver_times_out(receiver: Receiver, clock: FakeClock):
    receiver.respond(200, delay=5.0)
    sink = hook(receiver, clock, timeout_seconds=0.3)
    started = time.monotonic()
    with pytest.raises(RetryableSinkError, match=r"network error \("):
        sink.deliver([envelope()])
    assert time.monotonic() - started < 4.0  # yanıt beklenmedi


@pytest.mark.parametrize("url", ["ftp://example.org/x", "file:///etc/passwd", "example.org/hook", "http://", ""])
def test_only_http_addresses_are_accepted(url: str):
    with pytest.raises(ValueError, match="http:// or https://"):
        WebhookSink("ops", url, secret=SECRET)


@pytest.mark.parametrize("size", [0, 101])
def test_batch_size_is_between_1_and_100(size: int):
    with pytest.raises(ValueError, match="between 1 and 100"):
        WebhookSink("ops", "https://example.org/hook", secret=SECRET, batch_size=size)
    with pytest.raises(ValueError, match="secret is empty"):
        WebhookSink("ops", "https://example.org/hook", secret="")


# === dağıtıcıyla: sıra, toplu gönderim, yeniden deneme ===========================================


def test_events_arrive_in_order_in_batches_with_their_sequence_range(store: Store, receiver: Receiver, clock: FakeClock):
    sink = hook(receiver, clock, batch_size=2, events=("live.*",))
    dispatcher = dispatch(store, sink, clock)
    seqs = []
    for n in range(5):
        seqs.append(emit(store, data={"n": n}))
        emit(store, "job.started", source="job")  # sink'in istemediği olaylar araya girer: numaralar ardışık değil
    assert dispatcher.step() == math.inf
    assert [request.seqs for request in receiver.requests] == [seqs[0:2], seqs[2:4], seqs[4:]]
    stream_id = store.streams.head().stream_id
    for request in receiver.requests:
        first, last = request.seqs[0], request.seqs[-1]
        assert request.headers["X-Sofascore-Seq-First"] == str(first)
        assert request.headers["X-Sofascore-Seq-Last"] == str(last)
        assert request.headers["Idempotency-Key"] == f"{stream_id}:{first}-{last}"
        assert verify_signature(SECRET, request.headers["X-Sofascore-Signature"], request.body, now=clock.time())
    assert len({request.headers["X-Sofascore-Delivery"] for request in receiver.requests}) == 3
    assert [event["data"]["n"] for request in receiver.requests for event in request.json["events"]] == [0, 1, 2, 3, 4]


def test_a_batch_waits_up_to_the_flush_interval_for_more_events(store: Store, receiver: Receiver, clock: FakeClock):
    sink = hook(receiver, clock, batch_size=20, linger_seconds=1.0)
    dispatcher = dispatch(store, sink, clock)
    first = emit(store)
    assert dispatcher.step() == 1.0 and receiver.requests == []
    clock.advance(0.5)
    second = emit(store)
    clock.advance(0.5)
    dispatcher.step()
    assert [request.seqs for request in receiver.requests] == [[first, second]]  # bir saniye içinde tek istek


def test_a_failed_batch_is_retried_with_backoff_and_the_same_delivery_id(store: Store, receiver: Receiver,
                                                                        clock: FakeClock):
    sink = hook(receiver, clock, batch_size=2)
    dispatcher = dispatch(store, sink, clock)
    head = [emit(store), emit(store)]
    receiver.respond(500, 503)
    assert dispatcher.step() == 1.0
    assert last_error(store) == "the receiver answered HTTP 500"
    later = emit(store)
    assert dispatcher.step() == 1.0 and len(receiver.requests) == 1  # süre dolmadan istek atılmaz
    clock.advance(1.0)
    assert dispatcher.step() == 2.0
    clock.advance(2.0)
    assert dispatcher.step() == math.inf
    # Baştaki toplu gönderim üç kez, aynı içerik ve aynı kimlikle; sonraki olay ancak ondan sonra
    assert [request.seqs for request in receiver.requests] == [head, head, head, [later]]
    retries, following = receiver.requests[:3], receiver.requests[3]
    assert len({request.headers["X-Sofascore-Delivery"] for request in retries}) == 1
    assert len({request.headers["Idempotency-Key"] for request in retries}) == 1
    assert len({request.body for request in retries}) == 1
    assert following.headers["X-Sofascore-Delivery"] != retries[0].headers["X-Sofascore-Delivery"]
    # İmza her denemede o anın zaman damgasıyla yeniden üretilir
    stamps = [request.headers["X-Sofascore-Signature"].split(",")[0] for request in retries]
    assert stamps == [f"t={int(T0)}", f"t={int(T0) + 1}", f"t={int(T0) + 3}"]
    assert all(verify_signature(SECRET, request.headers["X-Sofascore-Signature"], request.body, tolerance=None)
               for request in receiver.requests)
    assert last_error(store) is None


def test_retry_after_delays_the_next_attempt(store: Store, receiver: Receiver, clock: FakeClock):
    dispatcher = dispatch(store, hook(receiver, clock), clock)
    emit(store, ts=clock.time())
    receiver.respond(429, headers={"Retry-After": "45"})
    assert dispatcher.step() == 45.0
    clock.advance(44)
    dispatcher.step()
    assert len(receiver.requests) == 1
    clock.advance(1)
    dispatcher.step()
    assert len(receiver.requests) == 2


def test_410_disables_the_sink_until_restart(store: Store, receiver: Receiver, clock: FakeClock,
                                             caplog: pytest.LogCaptureFixture):
    dispatcher = dispatch(store, hook(receiver, clock), clock)
    first = emit(store)
    receiver.respond(410)
    with caplog.at_level(logging.ERROR, logger="Sinks"):
        assert dispatcher.step() == math.inf
    second = emit(store)
    clock.advance(3600)
    dispatcher.step()
    assert len(receiver.requests) == 1  # kapalı sink'e istek atılmaz
    assert last_error(store) == "disabled until restart: the receiver answered 410 Gone"
    assert "Sink ops is disabled until restart" in caplog.text
    restarted = dispatch(store, hook(receiver, clock), clock)  # yeniden başlama: konum yerinde
    restarted.step()
    assert receiver.requests[-1].seqs == [first, second]


def test_events_are_replayed_after_a_crash_between_delivery_and_cursor(store: Store, receiver: Receiver,
                                                                      clock: FakeClock, monkeypatch: pytest.MonkeyPatch):
    dispatcher = dispatch(store, hook(receiver, clock), clock)
    seqs = [emit(store), emit(store)]
    with monkeypatch.context() as patch:
        patch.setattr(store.streams, "set_cursor", lambda *a, **k: (_ for _ in ()).throw(StoreBusy("busy")))
        dispatcher.step()  # alıcı aldı; süreç konumu yazamadan öldü
    assert [request.seqs for request in receiver.requests] == [seqs]
    more = emit(store)
    dispatch(store, hook(receiver, clock), clock).step()
    # Yeniden başlamadan sonra aynı olaylar yine gelir (en az bir kez); alıcı `seq` ile ayıklar
    assert receiver.requests[1].seqs == seqs + [more]
    received: Dict[int, Dict[str, Any]] = {}
    for request in receiver.requests:
        for event in request.json["events"]:
            received.setdefault(event["seq"], event)
    assert sorted(received) == seqs + [more]
    assert receiver.requests[0].json["events"] == receiver.requests[1].json["events"][:2]  # yinelenen olay aynıdır


def test_events_older_than_max_age_are_dropped_and_delivery_continues(store: Store, receiver: Receiver,
                                                                     clock: FakeClock):
    sink = hook(receiver, clock, batch_size=2, max_age_seconds=DAY, events=("live.*", "system.sink_dropped"))
    dispatcher = dispatch(store, sink, clock)
    old = [emit(store, ts=clock.time() - DAY - 1) for _ in range(3)]
    fresh = emit(store, ts=clock.time())
    receiver.always = 500
    assert dispatcher.step() == 1.0
    assert len(receiver.requests) == 1  # eski olayların her biri için ayrı istek atılmaz
    dropped = [dict(r.data) for r in store.streams.read(streams=["system"], types=["system.sink_dropped"]).events]
    assert dropped == [{"sink": "ops", "reason": "max_age", "first_seq": old[0], "last_seq": old[-1], "count": 3,
                        "max_age_seconds": DAY}]
    receiver.always = None
    clock.advance(1.0)
    dispatcher.step()
    delivered = receiver.requests[1].json["events"]
    assert [event["seq"] for event in delivered][0] == fresh
    assert delivered[1]["type"] == "system.sink_dropped" and delivered[1]["data"]["count"] == 3
    assert delivered[1]["stream"] == "system" and delivered[1]["source"] == "system"


def test_default_max_age_is_one_day_and_fresh_events_block_instead_of_being_dropped(store: Store, receiver: Receiver,
                                                                                    clock: FakeClock):
    sink = hook(receiver, clock)
    assert sink.max_age_seconds == DAY and sink.batch_size == 20
    dispatcher = dispatch(store, sink, clock)
    seq = emit(store, ts=clock.time())
    receiver.always = 503
    for _ in range(5):
        clock.advance(dispatcher.step())
    assert store.streams.read(streams=["system"]).events == ()
    receiver.always = None
    assert dispatcher.step() == math.inf  # alıcı düzelince olay teslim edilir
    assert receiver.requests[-1].seqs == [seq] and len(receiver.requests) == 6


def test_errors_logs_and_events_never_carry_the_secret_or_the_address(store: Store, receiver: Receiver, clock: FakeClock,
                                                                      caplog: pytest.LogCaptureFixture):
    url = receiver.url(TOKEN_PATH)
    sink = hook(receiver, clock, url=url, max_age_seconds=DAY)
    dispatcher = dispatch(store, sink, clock)
    emit(store, ts=clock.time() - 2 * DAY)
    receiver.always = 500
    with caplog.at_level(logging.DEBUG):
        dispatcher.step()
        clock.advance(1)
        emit(store, ts=clock.time())
        dispatcher.step()
    assert receiver.requests[0].path == TOKEN_PATH  # istek doğru adrese gitti
    everything = json.dumps({
        "logs": caplog.text,
        "error": last_error(store),
        "events": [dict(record.data) for record in store.streams.read().events],
        "status": [status.to_dict() for status in dispatcher.status()],
        "repr": repr(sink),
    })
    for private in (SECRET, PATH_PART, QUERY_PART, "/services/"):
        assert private not in everything, private
    assert "ops" in caplog.text and repr(sink) == "<WebhookSink 'ops' host='127.0.0.1' signed=True>"


# === tek seferlik komut: çıkışta teslim ==========================================================


def test_a_one_shot_command_posts_its_job_events_at_exit(store: Store, receiver: Receiver,
                                                         monkeypatch: pytest.MonkeyPatch):
    """Yapılandırmadaki webhook, işin başladığını ve bittiğini komut çıkarken alır (`drain`, en çok 10 sn)."""
    monkeypatch.setenv("SOFASCORE_HOOK_SECRET", SECRET)
    spec = SinkSpec(name="ops", type="webhook", url=receiver.url(), secret_env="SOFASCORE_HOOK_SECRET",
                    events=("job.*",))
    dispatcher = sinks.dispatcher_for(store, [spec])
    assert dispatcher is not None
    dispatcher.register()
    manager = JobManager(JobStore.for_store(store), cancel_poll=0.02, heartbeat=0.05)
    job = manager.submit(JobKind.SYNC, {"mode": "details"}, lambda handle: None, origin=local_origin("cli"),
                         background=False)
    report = sinks.drain_at_exit(dispatcher)
    assert report is not None and report.complete and report.delivered == 2
    (request,) = receiver.requests  # toplu gönderimin dolması beklenmedi: iki olay tek istekte
    assert [event["type"] for event in request.json["events"]] == ["job.started", "job.finished"]
    assert all(event["data"]["job_id"] == job.id for event in request.json["events"])
    assert request.json["events"][1]["data"]["state"] == "succeeded"
    assert verify_signature(SECRET, request.headers["X-Sofascore-Signature"], request.body)
    assert store.lease_holder("sinks") is None


def test_nothing_is_posted_without_a_configured_sink(store: Store, receiver: Receiver):
    assert sinks.dispatcher_for(store, []) is None
    emit(store)
    assert sinks.drain_at_exit(None) is None and receiver.requests == []
    assert store._state.connection().execute("SELECT count(*) FROM sink_cursors").fetchone()[0] == 0
