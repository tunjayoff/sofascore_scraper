"""
Push kaynağı `direct` (sofascore_scraper/services/live/direct_source.py; plan maddesi P31).

Gerçek sunucu, ağ ve tarayıcı yok. Push sunucusu bu süreçte çalışan sahte bir NATS-over-WebSocket sunucusudur
(FakeNatsServer, yalnızca 127.0.0.1): WebSocket el sıkışması, INFO, CONNECT denetimi, SUB/UNSUB, MSG, PING/PONG,
-ERR ve düşen bağlantı. Gönderdiği kareler kayıttandır (tests/fixtures/push/recorded_wire.jsonl; kimlik bilgisi,
istemci adresi ve sunucu bilgisi içermez). Kimlik bilgisi her testte çalışma anında, açıkça sahte parçalardan
kurulur.

  * hat üstünde: tek bağlantı, CONNECT sayfanın seçenekleriyle, yalnızca SUB/UNSUB/PING/PONG, joker yok, PUB yok
  * PING aralığı, sunucunun PING'ine PONG, düşen bağlantıdan sonra yeniden bağlanma ve yeniden abonelik
  * reddedilen kimlik bilgisi yeniden okunur; art arda başarısızlık sağlıksız sayılır; proxy varken bağlanılmaz
  * WebSocket istemcisi: parçalı mesaj, kontrol kareleri, uzun kare
  * kimlik bilgisi okuyucusu sahte bir köprüyle; maskeleme (sofascore_scraper/redact.py)
  * servis: push'tan gelen bitiş tek olaydır (`source` "direct"), kaynak değişikliği yazılır
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

import conftest
from sofascore_scraper import redact
from sofascore_scraper.services.live import direct_source as ds
from sofascore_scraper.services.live.direct_source import (
    Credential,
    CredentialUnavailable,
    DirectConnection,
    DirectSource,
    HandshakeRejected,
    WebSocket,
    parse_connect,
)
from sofascore_scraper.services.live.push_source import PushFeed, Signal
from sofascore_scraper.services.live.supervisor import LiveService, explicit_scope
from sofascore_scraper.store import Store, open_store
from test_live_push_source import FINISH, finish_frame, recorded, scenario, wait_for
from test_live_service import Stop

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
RECORDED_INFO = recorded()[0]["data"]


def fake_value(tag: str) -> str:
    """Açıkça sahte bir kimlik bilgisi; değişken adı bir kimlik bilgisi adı değildir (gizli değer tarayıcısı)."""
    return "-".join(("fake", "push", tag, "0000"))


def fake_credential(url: str, tag: str = "a") -> Credential:
    return Credential(url=url, options={"user": fake_value(tag), "pass": fake_value(tag + "p"), "lang": "nats.ws",
                                       "version": "1.0", "protocol": 1, "headers": True},
                      origin="http://127.0.0.1")


# --- sahte push sunucusu --------------------------------------------------------------------------------


def _read_frame(sock: socket.socket, buf: bytearray) -> Optional[Tuple[int, bytes]]:
    """İstemcinin bir karesi (maskeli olmalı); bağlantı kapanırsa None."""

    def need(n: int) -> bool:
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk:
                return False
            buf.extend(chunk)
        return True

    if not need(2):
        return None
    opcode, masked, n = buf[0] & 0x0F, bool(buf[1] & 0x80), buf[1] & 0x7F
    pos = 2
    if n == 126:
        if not need(4):
            return None
        n, pos = int.from_bytes(buf[2:4], "big"), 4
    elif n == 127:
        if not need(10):
            return None
        n, pos = int.from_bytes(buf[2:10], "big"), 10
    assert masked, "a client frame must be masked"
    if not need(pos + 4 + n):
        return None
    mask = bytes(buf[pos:pos + 4])
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(buf[pos + 4:pos + 4 + n]))
    del buf[:pos + 4 + n]
    return opcode, payload


def server_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    n = len(payload)
    first = (0x80 if fin else 0) | opcode
    if n < 126:
        return bytes([first, n]) + payload
    if n < 1 << 16:
        return bytes([first, 126]) + n.to_bytes(2, "big") + payload
    return bytes([first, 127]) + n.to_bytes(8, "big") + payload


class FakeNatsServer:
    """
    Süreç içi push sunucusu (127.0.0.1). Her bağlantıda: WebSocket el sıkışması, kayıtlı INFO, CONNECT'te
    beklenen kimlik bilgisi denetimi (yanlışsa `-ERR 'Authorization Violation'` ve kapanış), SUB/UNSUB kaydı,
    PING'e PONG. Testten: `msg`, `ping`, `drop`, `error`.
    """

    def __init__(self, accept: Optional[str] = None, *, handshake_status: int = 101, answer_ping: bool = True) -> None:
        self.accept = accept
        self.handshake_status = handshake_status
        self.answer_ping = answer_ping
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.url = f"ws://127.0.0.1:{self.listener.getsockname()[1]}/"
        self.lock = threading.Lock()
        self.connections = 0
        self.ops: List[str] = []  # istemcinin gönderdiği satırlar (CONNECT gövdesi olmadan)
        self.connects: List[Dict[str, Any]] = []
        self.headers: List[Dict[str, str]] = []
        self.subs: Dict[str, int] = {}  # bu bağlantıdaki konu → sid
        self.client: Optional[socket.socket] = None
        self.closed = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.closed:
            try:
                sock, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(sock,), daemon=True).start()

    def _handle(self, sock: socket.socket) -> None:
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                head += chunk
            lines = head.decode("latin-1").split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:] if h)}
            with self.lock:
                self.headers.append(headers)
                self.connections += 1
            if self.handshake_status != 101:
                sock.sendall(f"HTTP/1.1 {self.handshake_status} Forbidden\r\nContent-Length: 0\r\n\r\n".encode())
                sock.close()
                return
            accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            with self.lock:
                self.client = sock
                self.subs = {}
            sock.sendall(server_frame(0x2, RECORDED_INFO.encode()))
            buf = bytearray()
            while True:
                frame = _read_frame(sock, buf)
                if frame is None:
                    return
                opcode, payload = frame
                if opcode == 0x8:
                    return
                for line in payload.decode("utf-8").split("\r\n"):
                    if line:
                        self._op(sock, line)
        except OSError:
            return

    def _op(self, sock: socket.socket, line: str) -> None:
        verb, _, rest = line.partition(" ")
        with self.lock:
            self.ops.append(verb if verb == "CONNECT" else line)
        if verb == "CONNECT":
            options = json.loads(rest)
            with self.lock:
                self.connects.append(options)
            if self.accept is not None and options.get("user") != self.accept:
                # Gerçek sunucu gibi: hata satırı, sonra yazma yönünü kapatır (okunmamış veriyle `close` bağlantıyı
                # sıfırlar ve hata satırı istemciye hiç ulaşmayabilirdi)
                sock.sendall(server_frame(0x2, b"-ERR 'Authorization Violation'\r\n"))
                sock.shutdown(socket.SHUT_WR)
        elif verb == "SUB":
            subject, sid = rest.split(" ")
            with self.lock:
                self.subs[subject] = int(sid)
        elif verb == "UNSUB":
            with self.lock:
                self.subs = {s: i for s, i in self.subs.items() if str(i) != rest}
        elif verb == "PING" and self.answer_ping:
            sock.sendall(server_frame(0x2, b"PONG\r\n"))

    def send(self, data: bytes) -> None:
        assert self.client is not None
        self.client.sendall(data)

    def msg(self, subject: str, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode()
        sid = self.subs[subject]
        self.send(server_frame(0x2, b"MSG %s %d %d\r\n" % (subject.encode(), sid, len(payload)) + payload + b"\r\n"))

    def drop(self) -> None:
        if self.client is not None:
            try:
                self.client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.client.close()

    def count(self, op: str) -> int:
        with self.lock:
            return sum(1 for line in self.ops if line == op)

    def close(self) -> None:
        self.closed = True
        self.drop()
        self.listener.close()


class FakeReader:
    """Kimlik bilgisi okuyucusunun sahtesi: sırayla verilen kimlik bilgileri; bitince CredentialUnavailable."""

    def __init__(self, *credentials: Credential) -> None:
        self.credentials = list(credentials)
        self.reads: List[str] = []
        self.closed = 0

    def read(self, sport: str) -> Credential:
        self.reads.append(sport)
        if not self.credentials:
            raise CredentialUnavailable("no page here")
        return self.credentials.pop(0)

    def close(self) -> None:
        self.closed += 1


def fast(reader: Any, **options: Any) -> DirectConnection:
    """Testin hızında bir bağlantı: kısa beklemeler, proxy denetimi yok."""
    defaults: Dict[str, Any] = dict(blocked=lambda: None, recv_poll=0.02, reconnect_first=0.01, reconnect_max=0.05,
                                    read_retry_first=0.01, read_retry_max=0.05, stable_after=0.0)
    defaults.update(options)
    return DirectConnection(reader, **defaults)


def kinds(feed: PushFeed) -> List[str]:
    return [s.kind for s in feed.drain()]


def drain_until(feed: PushFeed, wanted: List[str], seconds: float = 5.0) -> List[Signal]:
    got: List[Signal] = []
    end = time.monotonic() + seconds
    while [s.kind for s in got if s.kind in wanted] != wanted:
        assert time.monotonic() < end, f"timed out: {[s.kind for s in got]}"
        got.extend(feed.drain())
        time.sleep(0.01)
    return got


@pytest.fixture
def server() -> Any:
    made: List[FakeNatsServer] = []

    def make(**options: Any) -> FakeNatsServer:
        made.append(FakeNatsServer(**options))
        return made[-1]

    yield make
    for s in made:
        s.close()


# --- hat üstünde --------------------------------------------------------------------------------------


def test_one_connection_subscribes_to_each_sport_and_sends_nothing_else(server: Any) -> None:
    srv = server(accept=fake_value("a"))
    reader = FakeReader(fake_credential(srv.url))
    conn = fast(reader)
    football, tennis = PushFeed("football"), PushFeed("tennis")
    for sport, feed in (("football", football), ("tennis", tennis)):
        source = DirectSource(sport, conn)
        source.feed = feed
        source.ensure_open()
    try:
        wait_for(lambda: conn.opens == 1)
        assert kinds(football) == ["open"] and kinds(tennis) == ["open"]
        assert srv.connects[0] == dict(fake_credential(srv.url).options)  # sayfanın seçenekleri, olduğu gibi
        assert srv.headers[0]["origin"] == "http://127.0.0.1"
        srv.msg("sport.football", FINISH)
        srv.msg("sport.tennis", {"id": 7, "status.code": 9})
        wait_for(lambda: conn.messages == 2)
        frames = [s for s in football.drain() if s.kind == "frame"]
        assert [s.frame for s in frames] == [FINISH] and frames[0].subject == "sport.football"
        assert [s.frame for s in tennis.drain() if s.kind == "frame"] == [{"id": 7, "status.code": 9}]
    finally:
        conn.close()
    assert srv.connections == 1 and reader.reads == ["football"] and reader.closed == 1
    assert srv.ops[:4] == ["CONNECT", "SUB sport.football 1", "SUB sport.tennis 2", "PING"]
    assert all(op.split(" ")[0] in ("CONNECT", "SUB", "UNSUB", "PING", "PONG") for op in srv.ops)
    assert not any("*" in op or ">" in op for op in srv.ops)
    assert list(conn.sent)[:4] == ["CONNECT", "SUB sport.football", "SUB sport.tennis", "PING"]


def test_the_client_pings_at_the_site_cadence_and_answers_the_server(server: Any) -> None:
    srv = server()
    conn = fast(FakeReader(fake_credential(srv.url)), ping_interval=0.2)
    DirectSource("football", conn).ensure_open()
    try:
        wait_for(lambda: conn.opens == 1)
        srv.send(server_frame(0x2, b"PING\r\n"))
        wait_for(lambda: srv.count("PONG") == 1)
        wait_for(lambda: srv.count("PING") >= 3)  # bağlanırken bir, sonra her 0,2 sn'de
    finally:
        conn.close()
    assert ds.PING_INTERVAL_SECONDS == 120.0  # gerçek aralık: sitenin istemcisininki


def test_a_dropped_connection_reconnects_and_subscribes_again(server: Any) -> None:
    srv = server()
    reader = FakeReader(fake_credential(srv.url))
    conn = fast(reader)
    source = DirectSource("football", conn)
    source.ensure_open()
    try:
        wait_for(lambda: conn.opens == 1)
        srv.drop()
        wait_for(lambda: conn.opens == 2)
        signals = drain_until(source.feed, ["open", "close", "open"])
        assert [s.kind for s in signals if s.kind in ("open", "close")] == ["open", "close", "open"]
        srv.msg("sport.football", FINISH)
        wait_for(lambda: conn.messages == 1)
    finally:
        conn.close()
    assert reader.reads == ["football"]  # olağan kopma: kimlik bilgisi yeniden okunmaz
    assert srv.connections == 2 and srv.count("SUB sport.football 1") == 2


def test_a_rejected_credential_is_read_again(server: Any) -> None:
    srv = server(accept=fake_value("new"))
    reader = FakeReader(fake_credential(srv.url, "old"), fake_credential(srv.url, "new"))
    conn = fast(reader)
    DirectSource("football", conn).ensure_open()
    try:
        wait_for(lambda: conn.opens == 1)
    finally:
        conn.close()
    assert reader.reads == ["football", "football"] and conn.rejections == 1
    assert [c["user"] for c in srv.connects] == [fake_value("old"), fake_value("new")]


def test_a_refused_handshake_counts_as_a_rejection(server: Any) -> None:
    srv = server(handshake_status=403)
    reader = FakeReader(fake_credential(srv.url), fake_credential(srv.url))
    conn = fast(reader)
    DirectSource("football", conn).ensure_open()
    try:
        # İki ret, sonra okunacak kimlik bilgisi kalmadı. Bağlantı okumayı kısa aralıkla yeniden dener: sayaç 3'te
        # durmaz, `== 3` yavaş bir makinede (macOS CI) o anı kaçırıp zaman aşımına düşüyordu (FX-15)
        wait_for(lambda: len(reader.reads) >= 3)
    finally:
        conn.close()
    assert conn.rejections == 2 and conn.opens == 0


def test_repeated_failures_make_the_source_unhealthy_and_keep_retrying(server: Any,
                                                                        caplog: pytest.LogCaptureFixture) -> None:
    reader = FakeReader()  # sayfa hiç bağlanmıyor
    conn = fast(reader, unhealthy_after=3)
    source = DirectSource("football", conn)
    with caplog.at_level(logging.WARNING):
        source.ensure_open()
        try:
            wait_for(lambda: len(reader.reads) >= 4)
        finally:
            conn.close()
    assert conn.unhealthy and conn.sessions == 0
    gone = [s.text for s in source.feed.drain() if s.kind == "gone"]
    assert len(gone) >= 3 and all(text.startswith("direct: the credential could not be read") for text in gone)
    assert sum("unhealthy" in r.getMessage() for r in caplog.records) == 1


def test_a_configured_proxy_is_never_bypassed(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper.client import transport

    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (True, "http://proxy.invalid:8080"))
    assert ds.proxy_in_use()
    reader = FakeReader(fake_credential("ws://127.0.0.1:9/"))
    attempts: List[Any] = []
    conn = DirectConnection(reader, connect=lambda c: attempts.append(c))
    source = DirectSource("football", conn)
    source.ensure_open()
    wait_for(lambda: not conn.running)
    conn.close()
    assert attempts == [] and reader.reads == [] and conn.unavailable == "a proxy is configured"
    assert [s.text for s in source.feed.drain()] == ["direct: a proxy is configured"]


def test_a_sport_added_and_removed_while_connected(server: Any) -> None:
    srv = server()
    conn = fast(FakeReader(fake_credential(srv.url)))
    football = DirectSource("football", conn)
    football.ensure_open()
    try:
        wait_for(lambda: conn.opens == 1)
        tennis = DirectSource("tennis", conn)
        tennis.ensure_open()
        wait_for(lambda: "sport.tennis" in srv.subs)
        assert [s.kind for s in drain_until(tennis.feed, ["open"])] == ["open"]
        football.close()
        wait_for(lambda: "sport.football" not in srv.subs)
    finally:
        conn.close()
    assert srv.ops.count("SUB sport.tennis 2") == 1 and "UNSUB 1" in srv.ops and srv.connections == 1


def test_a_silent_connection_is_dropped_and_opened_again(server: Any) -> None:
    srv = server(answer_ping=False)
    conn = fast(FakeReader(fake_credential(srv.url)), ping_interval=0.1, auth_wait=0.05)
    DirectSource("football", conn).ensure_open()
    try:
        wait_for(lambda: srv.connections >= 2)  # PONG hiç gelmez: kabul edilmiş sayılmaz, yeniden denenir
    finally:
        conn.close()
    assert conn.opens == 0 and conn.failures >= 1


def test_a_message_before_the_first_pong_opens_the_connection(server: Any) -> None:
    srv = server(answer_ping=False)
    conn = fast(FakeReader(fake_credential(srv.url)))
    source = DirectSource("football", conn)
    source.ensure_open()
    try:
        wait_for(lambda: "sport.football" in srv.subs)
        srv.msg("sport.football", FINISH)
        wait_for(lambda: conn.messages == 1)
    finally:
        conn.close()
    assert conn.opens == 1 and [s.kind for s in source.feed.drain()][:2] == ["open", "frame"]


def test_a_server_that_is_not_a_push_server_is_left(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    srv = server()
    monkeypatch.setattr(sys.modules[__name__], "RECORDED_INFO", "HELLO\r\n")
    conn = fast(FakeReader(fake_credential(srv.url)))
    DirectSource("football", conn).ensure_open()
    try:
        wait_for(lambda: conn.failures >= 1)
    finally:
        conn.close()
    assert conn.opens == 0 and srv.count("PING") == 0 and "CONNECT" not in srv.ops


def test_a_non_auth_error_is_reported_without_its_text(server: Any) -> None:
    srv = server()
    conn = fast(FakeReader(fake_credential(srv.url)))
    source = DirectSource("football", conn)
    source.ensure_open()
    try:
        wait_for(lambda: conn.opens == 1)
        srv.send(server_frame(0x2, b"-ERR 'Slow Consumer' " + fake_value("leak").encode() + b"\r\n"))
        signals = drain_until(source.feed, ["open", "error"])
    finally:
        conn.close()
    assert [s.text for s in signals if s.kind == "error"] == ["Slow Consumer"]


# --- WebSocket istemcisi -------------------------------------------------------------------------------


class Pipe:
    """WebSocket'in soketi yerine: önceden yazılmış sunucu baytları; gönderilenler kaydedilir."""

    def __init__(self, data: bytes = b"") -> None:
        self.data = bytearray(data)
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, value: float) -> None:
        pass

    def recv(self, n: int) -> bytes:
        if not self.data:
            raise socket.timeout()
        chunk, self.data = bytes(self.data[:n]), self.data[n:]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def close(self) -> None:
        self.closed = True


def test_fragmented_messages_control_frames_and_long_frames_are_read() -> None:
    big = b"x" * 70000
    data = (server_frame(0x1, b"PI", fin=False) + server_frame(0x9, b"hb") + server_frame(0x0, b"NG\r\n")
            + server_frame(0xA, b"") + server_frame(0x2, big) + server_frame(0x2, b"y" * 300))
    pipe = Pipe(data)
    ws = WebSocket(pipe)
    assert ws.recv(1.0) == b"PING\r\n"
    assert pipe.sent[0] == 0x8A  # PING kontrol karesine PONG (maskeli)
    assert ws.recv(1.0) == big and ws.recv(1.0) == b"y" * 300
    assert ws.recv(0.01) is None
    pipe.data.extend(server_frame(0x8, b"\x03\xe8"))
    with pytest.raises(ds.ConnectionClosed):
        ws.recv(1.0)


def test_a_message_over_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "MAX_MESSAGE_BYTES", 100)
    with pytest.raises(ds.WebSocketError):
        WebSocket(Pipe(server_frame(0x2, b"z" * 101))).recv(1.0)


def test_sent_frames_are_masked_and_sized() -> None:
    pipe = Pipe()
    ws = WebSocket(pipe)
    for size in (5, 300, 70000):
        pipe.sent.clear()
        ws.send_text("a" * size)
        frame = _read_frame(socket.socket(), bytearray(pipe.sent))  # tam kare arabellekte: soket okunmaz
        assert frame == (0x1, b"a" * size)


def test_the_handshake_is_checked(server: Any) -> None:
    srv = server(handshake_status=401)
    with pytest.raises(HandshakeRejected) as caught:
        WebSocket.connect(srv.url)
    assert caught.value.status == 401 and srv.url not in str(caught.value)
    with pytest.raises(ds.WebSocketError):
        WebSocket.connect("https://127.0.0.1/")


# --- kimlik bilgisi ----------------------------------------------------------------------------------


def test_the_connect_line_is_read_from_an_outgoing_frame() -> None:
    options = {"user": fake_value("u"), "lang": "nats.ws"}
    assert parse_connect(("CONNECT " + json.dumps(options) + "\r\nPING\r\n").encode()) == options
    assert parse_connect("SUB sport.football 1\r\n") is None
    assert parse_connect("CONNECT [1]\r\n") is None and parse_connect("CONNECT {bad\r\n") is None
    assert parse_connect(None) is None


def test_the_credential_never_shows_itself_and_is_masked_everywhere() -> None:
    credential = fake_credential("ws://127.0.0.1:9/" + fake_value("path"), "mask")
    assert fake_value("mask") not in repr(credential) and fake_value("mask") not in str(credential)
    ds.remember(credential)
    line = f"connect {json.dumps(dict(credential.options))} to {credential.url}"
    shown = redact.redact_text(line)
    assert fake_value("mask") not in shown and fake_value("maskp") not in shown and credential.url not in shown
    assert "nats.ws" in shown  # istemcinin dili gizli değildir
    assert redact.runtime_secret_count() > 0


def test_short_runtime_values_are_not_searched() -> None:
    before = redact.runtime_secret_count()
    redact.add_runtime_secret("abc")
    redact.add_runtime_secret(None)
    assert redact.runtime_secret_count() == before


def test_a_variable_name_after_secret_env_is_not_eaten() -> None:
    text = "sink 'hook': secret_env: expected a variable name; [server] token_env: expected text"
    assert redact.redact_text(text) == text
    assert redact.redact_text("token=" + fake_value("t")) == "token=***"


class CredentialSocket:
    """Playwright WebSocket'i: adres ve gelen/giden kare dinleyicileri."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.handlers: Dict[str, List[Callable[..., Any]]] = {}

    def on(self, name: str, handler: Callable[..., Any]) -> None:
        self.handlers.setdefault(name, []).append(handler)

    def emit(self, name: str, payload: Any) -> None:
        for handler in self.handlers.get(name, []):
            handler(payload)


class CredentialPage:
    def __init__(self, sockets: List[Tuple[str, List[Tuple[str, Any]]]]) -> None:
        self.sockets = sockets
        self.handlers: Dict[str, List[Callable[..., Any]]] = {}
        self.url = ""
        self.visits: List[str] = []
        self.closed = False

    def on(self, name: str, handler: Callable[..., Any]) -> None:
        self.handlers.setdefault(name, []).append(handler)

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.visits.append(url)
        self.url = url
        for address, frames in self.sockets:  # sitenin kodu bağlanır: önce başka bir sağlayıcı, sonra NATS
            ws = CredentialSocket(address)
            for handler in self.handlers.get("websocket", []):
                handler(ws)
            for direction, payload in frames:
                ws.emit(direction, payload)

    async def close(self) -> None:
        self.closed = True


class CredentialBridge:
    made: List["CredentialBridge"] = []

    def __init__(self, profile_dir: str, home_url: Optional[str] = None, *,
                 sockets: Optional[List[Tuple[str, List[Tuple[str, Any]]]]] = None) -> None:
        self.profile_dir, self.home_url = profile_dir, home_url
        self.page = CredentialPage(sockets or [])
        self.routes: List[Any] = []
        self.closed = False
        self.unrouted: List[str] = []
        self.context = type("Ctx", (), {"route": self._route, "new_page": self._new_page,
                                        "unroute_all": self._unroute_all})()
        CredentialBridge.made.append(self)

    async def _unroute_all(self, behavior: Optional[str] = None) -> None:
        # V9: kurallar sayfa kapanmadan kaldırılır; sürmekte olan istekler asılı görev bırakmaz
        assert not self.page.closed and not self.closed
        self.unrouted.append(str(behavior))

    async def ensure_ready(self) -> None:
        pass

    async def _route(self, pattern: str, handler: Any) -> None:
        self.routes.append(handler)

    async def _new_page(self) -> CredentialPage:
        return self.page

    async def solve_challenge(self) -> str:
        return "solved"

    async def close(self) -> None:
        self.closed = True


def install_bridge(monkeypatch: pytest.MonkeyPatch, sockets: List[Tuple[str, List[Tuple[str, Any]]]]) -> None:
    from sofascore_scraper.client import bridge

    CredentialBridge.made = []
    monkeypatch.setattr(bridge, "HOME_URL", "https://www.sofascore.com/tr")
    monkeypatch.setattr(bridge, "BrowserBridge", lambda profile_dir, home_url=None, report_health=True: CredentialBridge(
        profile_dir, home_url, sockets=sockets))


def test_the_browser_reader_takes_the_connect_frame_of_the_page_s_own_connection(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    options = {"user": fake_value("page"), "pass": fake_value("pagep"), "lang": "nats.ws"}
    connect = "CONNECT " + json.dumps(options) + "\r\nPING\r\n"
    address = "wss://push.invalid/"  # ayrılmış alan adı: gerçek bir sunucu değil
    install_bridge(monkeypatch, [
        ("wss://other.invalid/", [("framereceived", '{"hello": 1}'), ("framesent", "CONNECT {\"user\": \"x\"}\r\n")]),
        (address, [("framereceived", RECORDED_INFO.encode()), ("framesent", connect)]),
    ])
    reader = ds.BrowserCredentialReader(profile_dir=str(tmp_path / "chrome-live"))
    credential = reader.read("football")
    b = CredentialBridge.made[0]
    assert credential.options == options and credential.url == address
    assert credential.origin == "https://www.sofascore.com"
    assert b.profile_dir == str(tmp_path / "chrome-live") and b.home_url == "https://www.sofascore.com/robots.txt"
    assert b.page.visits == ["https://www.sofascore.com/tr/football"] and b.page.closed and b.closed
    assert len(b.routes) == 1 and fake_value("page") not in redact.redact_text(fake_value("page"))
    assert b.unrouted == ["ignoreErrors"]


def test_the_browser_reader_gives_up_when_the_page_opens_no_push_connection(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_bridge(monkeypatch, [("wss://other.invalid/", [("framereceived", "{}")])])
    monkeypatch.setattr(ds, "CAPTURE_SECONDS", 0.05)
    reader = ds.BrowserCredentialReader(profile_dir=str(tmp_path / "p"))
    with pytest.raises(CredentialUnavailable):
        reader.read("football")
    assert CredentialBridge.made[0].page.closed and CredentialBridge.made[0].closed
    assert CredentialBridge.made[0].unrouted == ["ignoreErrors"]


def test_a_credential_page_request_that_fails_while_closing_is_handed_back(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """V9: CONNECT okunup sayfa kapanırken başarısız olan istek Playwright'a geri verilir (görev asılı kalmaz)."""
    import asyncio
    from types import SimpleNamespace

    class PendingRoute:
        def __init__(self, url: str, kind: str) -> None:
            self.request = SimpleNamespace(url=url, resource_type=kind)
            self.handled: "asyncio.Future[bool]" = asyncio.get_running_loop().create_future()

        async def abort(self, reason: str = "") -> None:
            raise RuntimeError("Target page, context or browser has been closed")

        async def continue_(self) -> None:
            raise RuntimeError("Target page, context or browser has been closed")

        async def fallback(self) -> None:
            self.handled.set_result(False)

    reader = ds.BrowserCredentialReader(profile_dir=str(tmp_path / "p"))

    async def on_route(url: str, kind: str) -> bool:
        route = PendingRoute(url, kind)
        await reader._route(route)
        return await asyncio.wait_for(route.handled, 1.0)

    assert asyncio.run(on_route("https://www.google-analytics.com/collect", "fetch")) is False
    assert asyncio.run(on_route("https://www.sofascore.com/football", "document")) is False


# --- servis -----------------------------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(path))
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "48")
    return path


@pytest.fixture
def store(data_dir: Path) -> Store:
    return open_store(data_dir)


def stream(store: Store, name: str) -> List[Any]:
    return list(store.streams.read(streams=[name]).events)


def test_a_finish_from_the_direct_source_is_one_event_confirmed_with_one_request(
        store: Store, server: Any, data_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    live, done, api, clock = scenario()
    srv = server(accept=fake_value("svc"))
    conn = fast(FakeReader(fake_credential(srv.url, "svc")))

    def script(n: int) -> None:
        if n == 1:
            wait_for(lambda: conn.opens == 1)
        if n == 2:
            api.live["football"] = []
            api.events[500] = done
            srv.msg("sport.football", finish_frame(500, live["startTimestamp"]))
            wait_for(lambda: conn.messages == 1)

    service = LiveService(store, explicit_scope(["football"], tournament_ids=[17]), fetch=api, clock=clock,
                          sleep=clock.sleep, requested_source="direct", direct_connection=conn)
    with caplog.at_level(logging.DEBUG):
        report = service.run(Stop(clock, rounds=4, on_wait=script))
    events = stream(store, "live")
    assert [(e.type, e.source) for e in events] == [("live.status_changed", "direct")]
    assert (events[0].data["from"], events[0].data["to"]) == ("live", "completed")
    assert report.source == "direct" and report.confirmed == 1 and report.push_frames == 1
    assert report.leaders == {"football": "direct"} and not conn.running
    assert [(e.type, e.data) for e in stream(store, "system")] == [
        ("system.live_source_changed", {"sport": "football", "from": "poll", "to": "direct", "reason": "push_connected"})]
    assert "terms-of-use grey area" in caplog.text  # dört uyarı her başlangıçta log'da
    store.close()
    for needle in (fake_value("svc"), fake_value("svcp"), srv.url):
        assert needle not in caplog.text
        for path in data_dir.rglob("*"):
            if path.is_file():
                assert needle.encode() not in path.read_bytes(), path


def test_a_direct_source_that_never_connects_leaves_the_service_polling(store: Store) -> None:
    live, done, api, clock = scenario()
    reader = FakeReader()
    conn = fast(reader)

    def script(n: int) -> None:
        if n == 1:
            wait_for(lambda: len(reader.reads) >= 2)

    report = LiveService(store, explicit_scope(["football"], tournament_ids=[17]), fetch=api, clock=clock,
                         sleep=clock.sleep, requested_source="direct", direct_connection=conn
                         ).run(Stop(clock, rounds=65, on_wait=script))
    assert report.rounds == 3 and report.leaders == {"football": "poll"}  # 0, 30, 60. sn: poll_interval
    assert stream(store, "system") == [] and stream(store, "live") == []


assert conftest  # sınır denetimi ve kancalar conftest'te kurulur
