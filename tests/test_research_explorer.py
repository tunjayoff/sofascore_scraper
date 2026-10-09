"""
FX-29: araştırma tarayıcısı (scripts/explore_all_sports.py) her SofaScore isteğini yine sayar, sıraya sokar ve kaydeder.

Kök neden (2026-10-08 canlı doğrulama, V3): istekler `context.route` içinde ortak kilitten (≤ 1 istek/sn) sıra
bekliyordu. Playwright sürücüsü aynı türden 10.000'i aşan nesnenin en eski 1.000'ini toplar; engellenen
görsellerin yeniden deneme seli saniyede yüzlerce Route yaratınca sırada bekleyen Route'lar toplanıyor,
`route.continue_()` "The object has been collected to prevent unbounded heap growth" ile düşüyor, istek ne
gönderiliyor ne kaydediliyordu. Düzeltme: istekler keşif sayfasının CDP oturumunda (Fetch alanı) durdurulur;
bekleyen istek yalnızca bir kimlik dizgisidir.

FX-29b (2026-10-09 canlı doğrulama): SofaScore `/football/match/...` belgesini ~0,8 sn sonra istemci tarafında
`/tr/football/match/...` belgesine yeniden yükler. Eski belgenin sırada bekleyen istekleri Chromium'da sessizce ölür
(ne sayfaya ne patchright'a hata düşer); her biri yine de 1 sn sıra yiyip continueRequest'te "Invalid InterceptionId"
alıyordu, yeni belgenin istekleri adım bitene kadar arkada kalıyordu (1 gönderim, 29 hata). Düzeltme: sıra alınmadan
önce ve sonra isteğin canlılığı yan etkisiz bir çağrıyla (Fetch.getResponseBody) denetlenir; ölü istek sıra almaz.

İki katman:
  - Tarayıcısız birim testleri (her CI işinde): kesicinin kararları, her durdurulan isteğin tam bir kez yanıtlanması,
    kilit aralığı, bütçe, koşu başına bütçe (--new-run), yanıt kaydının sağlamlığı ve maskeleme.
  - `browser` testi (CI'nin "browser bridge (offline)" işi): gerçek patchright + Chromium, yerel sahte site ve görsel
    seli altında; tarayıcının vekil sunucusu da sahte sitedir, hiçbir istek makineden çıkmaz (SofaScore'a hiç gidilmez).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

# Araştırma script'lerinin ortak hız kilidi fcntl kullanır (Linux/macOS araçları); Windows'ta bu modül atlanır
pytest.importorskip("fcntl")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def _import_explorer():
    """Script'ler içe aktarılırken ortam değişkeni (SOFASCORE_LOG__LEVEL) koyar: test sürecinin ortamı değişmesin."""
    saved = dict(os.environ)
    sys.path.insert(0, SCRIPTS)
    try:
        import _research_common
        import explore_all_sports
    finally:
        sys.path.remove(SCRIPTS)
        os.environ.clear()
        os.environ.update(saved)
    return explore_all_sports, _research_common


ex_mod, rc = _import_explorer()

FAKE_HOST = re.compile(r"(^|\.)fakescore\.test$")
GAP = 0.05
CLIENT_IP = "203.0.113.77"


class FakeCDP:
    """
    CDP oturumu: gönderilen komutları kaydeder. `fail_ids`: continueRequest hata verir (iptal edilmiş istek).
    `dead`: Chromium'un sessizce bıraktığı istekler (her Fetch çağrısı "Invalid InterceptionId"); canlı istekte
    getResponseBody, Chromium gibi Request aşamasında yan etkisiz bir hata verir.
    """

    def __init__(self, fail_ids=()):
        self.sent = []
        self.fail_ids = set(fail_ids)
        self.dead = set()
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    async def send(self, method, params=None):
        params = params or {}
        self.sent.append((time.monotonic(), method, params))
        rid = params.get("requestId")
        if method.startswith("Fetch.") and rid in self.dead:
            raise RuntimeError(f"Protocol error ({method}): Invalid InterceptionId.")
        if method == "Fetch.getResponseBody":
            raise RuntimeError("Protocol error (Fetch.getResponseBody): Can only get response body on "
                               "HeadersReceived pattern matched requests.")
        if method == "Fetch.continueRequest" and rid in self.fail_ids:
            raise RuntimeError("Protocol error (Fetch.continueRequest): Invalid InterceptionId.")
        return {}

    def answers(self):
        """requestId → [yanıt yöntemleri] (canlılık denetimi yanıt değildir)."""
        out = {}
        for _, method, params in self.sent:
            if method.startswith("Fetch.") and method != "Fetch.getResponseBody" and "requestId" in params:
                out.setdefault(params["requestId"], []).append(method)
        return out


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.cdp = FakeCDP()
        self.handlers = {}
        self.pages = []

    async def new_cdp_session(self, page):
        return self.cdp

    def on(self, event, handler):
        self.handlers[event] = handler

    async def route(self, *a, **k):  # Route kullanılmamalı (FX-29)
        raise AssertionError("the explorer must not use context.route")


class FakeBridge:
    def __init__(self):
        self.context = FakeContext()
        self.page = FakePage(self.context)
        self.context.pages = [self.page]
        self.solves = 0

    async def ensure_ready(self):
        pass

    async def close(self):
        pass

    async def _solve_on_captcha_page(self):
        self.solves += 1
        return "token"


@pytest.fixture
def rate_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_RATE_FILE", str(tmp_path / "ratelimit"))
    monkeypatch.setattr(rc, "MIN_REQUEST_GAP", GAP)
    return tmp_path / "ratelimit"


def make_explorer(tmp_path, **kw):
    kw.setdefault("sofa_host", FAKE_HOST)
    kw.setdefault("bridge", FakeBridge())
    return ex_mod.Explorer(str(tmp_path / "out"), **kw)


def paused(rid, url, resource_type="Fetch", network_id=None, frame_id=None):
    ev = {"requestId": rid, "request": {"url": url, "method": "GET"}, "resourceType": resource_type}
    if network_id:
        ev["networkId"] = network_id
    if frame_id:
        ev["frameId"] = frame_id
    return ev


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def all_output(out_dir) -> str:
    text = []
    for base, _, files in os.walk(out_dir):
        for name in files:
            with open(os.path.join(base, name), encoding="utf-8") as f:
                text.append(f.read())
    return "\n".join(text)


# --- kesici: her durdurulan istek tam bir kez yanıtlanır -------------------------------------------------------

@pytest.mark.asyncio
async def test_every_paused_request_is_answered_exactly_once(tmp_path, rate_file):
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    events = (
        [paused(f"api-{i}", f"https://www.fakescore.test/api/v1/event/{i}/statistics") for i in range(8)]
        + [paused(f"xhr-{i}", f"https://www.fakescore.test/api/v1/team/{i}", "XHR") for i in range(2)]
        + [paused(f"tp-{i}", f"https://ads.example.net/bid/{i}?token=s3cret") for i in range(3)]
        + [paused(f"img-{i}", f"https://img.fakescore.test/team/{i}/image", "Image") for i in range(5)]
        + [paused("media-0", "https://cdn.example.net/v.mp4", "Media"),
           paused("font-0", "https://www.fakescore.test/f.woff2", "Font")]
    )
    await asyncio.gather(*(ex.on_paused(cdp, ev) for ev in events))

    answers = cdp.answers()
    assert set(answers) == {ev["requestId"] for ev in events}
    assert all(len(methods) == 1 for methods in answers.values()), answers
    assert all(answers[f"api-{i}"] == ["Fetch.continueRequest"] for i in range(8))
    assert all(answers[f"xhr-{i}"] == ["Fetch.continueRequest"] for i in range(2))
    assert all(answers[f"tp-{i}"] == ["Fetch.continueRequest"] for i in range(3))
    assert all(answers[f"img-{i}"] == ["Fetch.fulfillRequest"] for i in range(5))  # ağa gitmez, yeniden denenmez
    assert answers["media-0"] == answers["font-0"] == ["Fetch.failRequest"]
    assert ex.api_count == 10  # yalnızca SofaScore XHR/fetch sayılır
    assert ex.blocked == 7
    assert ex.intercept_errors == 0


@pytest.mark.asyncio
async def test_sofascore_requests_leave_one_slot_apart(tmp_path, rate_file):
    """Ortak kilit: aynı anda durdurulan istekler bile en az MIN_REQUEST_GAP arayla gönderilir."""
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    await asyncio.gather(*(ex.on_paused(cdp, paused(f"r{i}", f"https://www.fakescore.test/api/v1/event/{i}"))
                           for i in range(6)))
    times = sorted(t for t, method, _ in cdp.sent if method == "Fetch.continueRequest")
    assert len(times) == 6
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert min(gaps) >= GAP * 0.8, gaps


@pytest.mark.asyncio
async def test_idle_and_over_budget_requests_are_not_sent(tmp_path, rate_file):
    ex = make_explorer(tmp_path, max_requests=2)
    cdp = FakeCDP()
    await ex.on_paused(cdp, paused("idle", "https://www.fakescore.test/api/v1/event/1"))
    ex.active = True
    for i in range(3):
        await ex.on_paused(cdp, paused(f"b{i}", f"https://www.fakescore.test/api/v1/event/{i}"))
    answers = cdp.answers()
    assert answers["idle"] == ["Fetch.failRequest"]
    assert answers["b0"] == answers["b1"] == ["Fetch.continueRequest"]
    assert answers["b2"] == ["Fetch.failRequest"]
    failed = [p for _, m, p in cdp.sent if m == "Fetch.failRequest"]
    assert all(p["errorReason"] == "BlockedByClient" for p in failed)
    assert (ex.idle_dropped, ex.api_count, ex.blocked) == (1, 2, 1)


@pytest.mark.asyncio
async def test_request_queued_when_the_step_ends_is_dropped(tmp_path, rate_file, monkeypatch):
    ex = make_explorer(tmp_path)
    ex.active = True
    release = asyncio.Event()

    async def slow_slot(*args):
        await release.wait()
        return "slot"

    monkeypatch.setattr(ex, "_take_slot", slow_slot)
    cdp = FakeCDP()
    task = asyncio.ensure_future(ex.on_paused(cdp, paused("late", "https://www.fakescore.test/api/v1/event/1")))
    await asyncio.sleep(0.01)
    ex.active = False  # komut bitti; istek hâlâ sırada
    release.set()
    await task
    assert cdp.answers() == {"late": ["Fetch.failRequest"]}
    assert (ex.api_count, ex.idle_dropped) == (0, 1)


@pytest.mark.asyncio
async def test_request_cancelled_while_queued_is_logged_and_not_counted(tmp_path, rate_file):
    """continueRequest hata verirse (sayfa değişti, istek iptal) satır yazılır, bütçe düşülür, diğerleri etkilenmez."""
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP(fail_ids={"gone"})
    await asyncio.gather(
        ex.on_paused(cdp, paused("gone", "https://www.fakescore.test/api/v1/event/1")),
        ex.on_paused(cdp, paused("ok", "https://www.fakescore.test/api/v1/event/2")),
    )
    assert ex.api_count == 1
    assert ex.intercept_errors == 1
    rows = [r for r in read_rows(tmp_path / "out" / "pages.jsonl") if r["op"] == "intercept-error"]
    assert len(rows) == 1 and rows[0]["url"].endswith("/api/v1/event/1") and "InterceptionId" in rows[0]["error"]


# --- FX-29b: sıra beklerken ölen istek sıra almaz, yanıtlanmaz, gönderilmez ----------------------------------------

def _recorded_slots(monkeypatch, gap):
    """Kilitten alınan her sıranın anı (sıra = gerçekten beklenen MIN_REQUEST_GAP)."""
    monkeypatch.setattr(rc, "MIN_REQUEST_GAP", gap)
    slots = []
    real = rc._wait_rate_slot

    def recorded():
        real()
        slots.append(time.monotonic())

    monkeypatch.setattr(rc, "_wait_rate_slot", recorded)
    return slots


def _sent_by_page(ex, cdp, rid, url, network_id, loader_id, frame_id="main"):
    """Sayfanın isteği: önce Network.requestWillBeSent (belgesiyle), sonra Fetch.requestPaused."""
    ex.on_request_will_be_sent({"requestId": network_id, "type": "Fetch", "frameId": frame_id, "loaderId": loader_id,
                                "request": {"url": url}})
    return asyncio.ensure_future(ex.on_paused(cdp, paused(rid, url, network_id=network_id, frame_id=frame_id)))


@pytest.mark.asyncio
async def test_requests_of_a_replaced_document_take_no_slot_and_are_never_sent(tmp_path, rate_file, monkeypatch):
    """
    Canlı koşul (2026-10-09): SofaScore `/football/match/...` adresini ~0,8 sn sonra `/tr/football/match/...`
    belgesine yeniden yükler. Eski belgenin sıradaki istekleri Chromium'da sessizce ölür; eskiden her biri yine de
    1 sn sıra yiyip continueRequest'te "Invalid InterceptionId" ile düşüyor, yeni belgenin istekleri onların
    arkasında adım bitene kadar bekliyordu. Şimdi belge değişince sıra almadan bırakılırlar.
    """
    slots = _recorded_slots(monkeypatch, 0.3)
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    ex.on_frame_navigated({"frame": {"id": "main", "loaderId": "doc1"}})
    old = [_sent_by_page(ex, cdp, f"old-{i}", f"https://www.fakescore.test/api/v1/event/{i}/lineups", f"n{i}", "doc1")
           for i in range(5)]
    await asyncio.sleep(0.1)  # ilki gönderildi, ikincisi kilitte sıra bekliyor
    cdp.dead.update(f"old-{i}" for i in range(1, 5))  # Chromium eski belgenin bekleyen isteklerini bıraktı
    ex.on_frame_navigated({"frame": {"id": "main", "loaderId": "doc2"}})  # sayfa yeni belgeye geçti
    new = [_sent_by_page(ex, cdp, f"new-{i}", f"https://www.fakescore.test/api/v1/event/{i}/statistics", f"m{i}", "doc2")
           for i in range(2)]
    await asyncio.gather(*old, *new)

    answers = cdp.answers()
    assert answers["old-0"] == ["Fetch.continueRequest"]
    assert not [rid for rid in answers if rid.startswith("old-") and rid != "old-0"]  # ölüler yanıtlanmaz
    assert answers["new-0"] == answers["new-1"] == ["Fetch.continueRequest"]
    assert ex.api_count == 3 and ex.gone == 4 and ex.intercept_errors == 0
    assert len(slots) <= 3 + 1  # en fazla bir sıra (o an kilitte bekleyen) boşa gider
    gone = [r for r in read_rows(tmp_path / "out" / "pages.jsonl") if r["op"] == "request-gone"]
    assert len(gone) == 4 and all(r["reason"] == "the page loaded a new document" for r in gone)
    assert sum(r["slot_used"] for r in gone) <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("network_id", ["n1", None])
async def test_request_dropped_without_any_event_is_found_before_its_slot(tmp_path, rate_file, monkeypatch, network_id):
    """
    Canlıda eski belgenin kapanırken attığı isteklerin Network olayı hiç gelmez (belgesi, çoğu zaman networkId'si de
    bilinmez). Karar olaylara değil, sıra alınmadan hemen önceki canlılık denetimine (Fetch.getResponseBody) dayanır.
    """
    slots = _recorded_slots(monkeypatch, 0.2)
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    first = asyncio.ensure_future(ex.on_paused(cdp, paused("r0", "https://www.fakescore.test/api/v1/event/0",
                                                           network_id="n0")))
    await asyncio.sleep(0.02)
    cdp.dead.add("r1")
    await asyncio.gather(first, ex.on_paused(cdp, paused("r1", "https://www.fakescore.test/api/v1/odds/providers/XX/web",
                                                         network_id=network_id)))
    assert cdp.answers() == {"r0": ["Fetch.continueRequest"]}
    assert (ex.api_count, ex.gone, ex.intercept_errors, len(slots)) == (1, 1, 0, 1)
    row = [r for r in read_rows(tmp_path / "out" / "pages.jsonl") if r["op"] == "request-gone"][0]
    assert row["reason"] == "dropped by Chromium before its turn" and row["slot_used"] is False


@pytest.mark.asyncio
async def test_request_dropped_while_waiting_for_its_slot_is_not_sent(tmp_path, rate_file, monkeypatch):
    """Sıra beklenirken ölen istek: sıra kullanılmış olur ama continueRequest denenmez, hata sayılmaz."""
    _recorded_slots(monkeypatch, 0.3)
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    tasks = [asyncio.ensure_future(ex.on_paused(cdp, paused(f"r{i}", f"https://www.fakescore.test/api/v1/event/{i}",
                                                            network_id=f"n{i}"))) for i in range(2)]
    await asyncio.sleep(0.1)  # r1 kilitte, sırasını bekliyor
    cdp.dead.add("r1")
    await asyncio.gather(*tasks)
    assert cdp.answers() == {"r0": ["Fetch.continueRequest"]}
    assert (ex.api_count, ex.gone, ex.intercept_errors) == (1, 1, 0)
    row = [r for r in read_rows(tmp_path / "out" / "pages.jsonl") if r["op"] == "request-gone"][0]
    assert row["reason"] == "dropped by Chromium while it waited for its slot" and row["slot_used"] is True


@pytest.mark.asyncio
async def test_request_cancelled_by_the_page_while_queued_takes_no_slot(tmp_path, rate_file, monkeypatch):
    slots = _recorded_slots(monkeypatch, 0.2)
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    tasks = [_sent_by_page(ex, cdp, f"r{i}", f"https://www.fakescore.test/api/v1/event/{i}", f"n{i}", "doc1")
             for i in range(3)]
    await asyncio.sleep(0.05)
    cdp.dead.add("r2")
    ex.on_loading_failed({"requestId": "n2", "errorText": "net::ERR_ABORTED", "canceled": True})
    await asyncio.gather(*tasks)
    assert set(cdp.answers()) == {"r0", "r1"}
    assert (ex.api_count, ex.gone, ex.intercept_errors, len(slots)) == (2, 1, 0, 2)
    row = [r for r in read_rows(tmp_path / "out" / "pages.jsonl") if r["op"] == "request-gone"][0]
    assert row["reason"] == "failed while queued: net::ERR_ABORTED" and row["slot_used"] is False


@pytest.mark.asyncio
async def test_request_paused_again_by_chromium_keeps_its_place_and_is_sent_once(tmp_path, rate_file, monkeypatch):
    """Chromium aynı isteği (aynı networkId) yeni kimlikle yeniden durdurursa eski kimlik geçersizdir: yenisi bir kez."""
    _recorded_slots(monkeypatch, 0.2)
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    url = "https://www.fakescore.test/api/v1/event/7"
    tasks = [_sent_by_page(ex, cdp, "other", "https://www.fakescore.test/api/v1/event/6", "n6", "doc1"),
             _sent_by_page(ex, cdp, "first-try", url, "n7", "doc1")]
    await asyncio.sleep(0.05)
    cdp.dead.add("first-try")  # eski kimlik geçersiz
    await ex.on_paused(cdp, paused("second-try", url, network_id="n7", frame_id="main"))
    await asyncio.gather(*tasks)
    assert cdp.answers() == {"other": ["Fetch.continueRequest"], "second-try": ["Fetch.continueRequest"]}
    assert (ex.api_count, ex.restarts, ex.intercept_errors) == (2, 1, 0)


@pytest.mark.asyncio
async def test_queued_requests_are_dropped_without_a_slot_when_the_step_ends(tmp_path, rate_file, monkeypatch):
    slots = _recorded_slots(monkeypatch, 0.2)
    ex = make_explorer(tmp_path)
    ex.active = True
    cdp = FakeCDP()
    tasks = [_sent_by_page(ex, cdp, f"r{i}", f"https://www.fakescore.test/api/v1/event/{i}", f"n{i}", "doc1")
             for i in range(4)]
    await asyncio.sleep(0.05)
    ex.active = False  # komut bitti: sıradakiler hemen, sıra almadan düşer
    await asyncio.gather(*tasks)
    answers = cdp.answers()
    assert answers["r0"] == ["Fetch.continueRequest"]
    assert sum(1 for rid in ("r1", "r2", "r3") if answers.get(rid) == ["Fetch.failRequest"]) == 3
    assert ex.idle_dropped == 3 and len(slots) <= 2


@pytest.mark.asyncio
async def test_probe_is_held_and_answered_locally_without_budget(tmp_path, rate_file):
    """Öz denetim: .invalid adresine giden fetch tutulur ve yerelde yanıtlanır; boştayken de, sayılmadan."""
    ex = make_explorer(tmp_path)
    cdp = FakeCDP()

    async def evaluate(js, url):
        await ex.on_paused(cdp, paused("probe-1", url))
        body = [p["body"] for _, m, p in cdp.sent if m == "Fetch.fulfillRequest"][-1]
        return "ok:" + base64.b64decode(body).decode()

    ex.page = type("P", (), {"url": "https://www.fakescore.test/football", "evaluate": staticmethod(evaluate)})()
    res = await ex.probe({"hold": 0.2})
    assert res["ok"] is True and res["paused"] and res["answered"] and res["held_s"] == 0.2
    assert cdp.answers() == {"probe-1": ["Fetch.fulfillRequest"]}
    headers = {h["name"]: h["value"] for h in cdp.sent[-1][2]["responseHeaders"]}
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert (ex.api_count, ex.idle_dropped, ex.blocked) == (0, 0, 0)
    assert [r for r in read_rows(tmp_path / "out" / "pages.jsonl") if r["op"] == "probe"][0]["ok"] is True


@pytest.mark.asyncio
async def test_start_intercepts_through_cdp_not_context_route(tmp_path, rate_file, monkeypatch):
    """Kesici CDP Fetch'tir: Route nesnesi yaratılmaz (toplanacak nesne yok); service worker atlanır."""
    cs = ex_mod.cs
    monkeypatch.setattr(cs, "HOME_URL", cs.HOME_URL)
    monkeypatch.setattr(cs, "CAPTCHA_URL", cs.CAPTCHA_URL)
    bridge = FakeBridge()
    extra = FakePage(bridge.context)
    bridge.context.pages.append(extra)
    ex = make_explorer(tmp_path, bridge=bridge, quiet_url="http://quiet.fakescore.test/robots.txt")
    await ex.start()

    methods = [m for _, m, _ in bridge.context.cdp.sent]
    assert methods == ["Network.enable", "Network.setBypassServiceWorker", "Page.enable", "Fetch.enable"]
    patterns = bridge.context.cdp.sent[-1][2]["patterns"]
    assert {p["resourceType"] for p in patterns} == {"Image", "Media", "Font", "XHR", "Fetch", "EventSource"}
    assert all(p["requestStage"] == "Request" for p in patterns)
    assert {"Fetch.requestPaused", "Network.requestWillBeSent", "Network.loadingFailed", "Page.frameNavigated",
            "Page.frameDetached"} <= set(bridge.context.cdp.handlers)
    assert {"response", "requestfailed", "page"} <= set(bridge.context.handlers)
    assert getattr(extra, "closed", False)  # kesicisiz sayfa kalmaz
    assert cs.HOME_URL == "http://quiet.fakescore.test/robots.txt"  # açılış API çağırmayan sayfaya


@pytest.mark.asyncio
async def test_quiet_page_patch_reaches_the_attributes_the_bridge_reads(tmp_path, rate_file, monkeypatch):
    """
    FX-32: betik köprüyü `sofascore_scraper.client.bridge` adıyla içe aktarır (2.x takma adı kalktı). Açılış ve captcha
    adresinin yaması köprünün okuduğu modül değişkenlerine ulaşır: ana sayfa `_home`, captcha çözümü `CAPTCHA_URL`.
    """
    from sofascore_scraper.client import bridge as bridge_mod

    assert ex_mod.cs is bridge_mod and rc.cs is bridge_mod
    assert "sofascore_scraper.challenge_solver" not in sys.modules
    monkeypatch.setattr(bridge_mod, "HOME_URL", bridge_mod.HOME_URL)
    monkeypatch.setattr(bridge_mod, "CAPTCHA_URL", bridge_mod.CAPTCHA_URL)
    quiet = "http://quiet.fakescore.test/robots.txt"
    await make_explorer(tmp_path, quiet_url=quiet).start()

    assert bridge_mod.HOME_URL == quiet
    assert bridge_mod.CAPTCHA_URL == "https://www.sofascore.com/captcha.html?redirectUrl=" + \
        "http%3A%2F%2Fquiet.fakescore.test%2Frobots.txt"

    # Gerçek köprü sınıfı (tarayıcısız): açılış adresini ve captcha sayfasını yamalı değişkenlerden okur
    real = bridge_mod.BrowserBridge.__new__(bridge_mod.BrowserBridge)
    assert real._home() == quiet
    fetched = []
    tokens = iter([None, "solved"])

    class Session:
        async def fetch(self, url, **_kw):
            fetched.append(url)

    class Context:
        async def cookies(self, _url):
            value = next(tokens, "solved")
            return [{"name": "sofa_captcha", "value": value}] if value else []

    real.session, real.context = Session(), Context()
    assert await real._solve_on_captcha_page() == "solved"
    assert fetched == [bridge_mod.CAPTCHA_URL]


@pytest.mark.asyncio
async def test_popups_are_closed_but_the_captcha_page_is_not(tmp_path, rate_file):
    bridge = FakeBridge()
    ex = make_explorer(tmp_path, bridge=bridge)
    ex.page = bridge.page
    popup = FakePage(bridge.context)
    ex.on_page(popup)
    await asyncio.gather(*ex._tasks)
    assert getattr(popup, "closed", False)
    ex._solving = True
    captcha = FakePage(bridge.context)
    ex.on_page(captcha)
    assert not ex._tasks and not getattr(captcha, "closed", False)


@pytest.mark.asyncio
async def test_challenge_solve_takes_a_slot_and_is_counted(tmp_path, rate_file):
    """Captcha çözümünün tek SofaScore isteği (token/captcha) Scrapling'in sayfasındadır: önceden sayılır."""
    bridge = FakeBridge()
    ex = make_explorer(tmp_path, bridge=bridge)
    t0 = time.monotonic()
    assert await ex._solve_challenge() == "token"
    assert bridge.solves == 1 and ex.api_count == 1 and not ex._solving
    assert time.monotonic() - t0 >= GAP * 0.8  # çözümden sonra da bir aralık


# --- kayıt -------------------------------------------------------------------------------------------------------

class FakeRequest:
    def __init__(self, url, resource_type="fetch", frame_url="https://www.fakescore.test/football", failure=None):
        self.url = url
        self.resource_type = resource_type
        self.method = "GET"
        self.failure = failure
        self.frame = type("F", (), {"url": frame_url})()


class FakeResponse:
    def __init__(self, url, status=200, body=b"{}", resource_type="fetch", error=None,
                 frame_url="https://www.fakescore.test/football"):
        self.url = url
        self.status = status
        self.headers = {"cache-control": "max-age=10"}
        self.request = FakeRequest(url, resource_type, frame_url)
        self._body = body
        self._error = error
        self.body_reads = 0

    async def all_headers(self):
        if self._error:
            raise self._error
        return {"cache-control": "max-age=10", "age": "3"}

    async def body(self):
        self.body_reads += 1
        if self._error:
            raise self._error
        return self._body


@pytest.mark.asyncio
async def test_response_is_recorded_even_when_its_body_was_collected(tmp_path, rate_file):
    ex = make_explorer(tmp_path)
    err = Exception("Response.body: The object has been collected to prevent unbounded heap growth.")
    await ex.on_response(FakeResponse("https://www.fakescore.test/api/v1/event/5/lineups", error=err))
    rows = read_rows(tmp_path / "out" / "requests.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == 200
    assert row["pattern"] == "www.fakescore.test/api/v1/event/{id}/lineups"
    assert row["cache_control"] == "max-age=10"
    assert "collected" in row["body_error"]
    assert row["run_id"] == ex.run_id


@pytest.mark.asyncio
async def test_sofascore_body_is_sampled_and_events_extracted(tmp_path, rate_file):
    ex = make_explorer(tmp_path)
    ex.sport = "football"
    body = {"events": [{"id": 9, "status": {"code": 100, "type": "finished"}, "startTimestamp": 1}]}
    await ex.on_response(FakeResponse("https://www.fakescore.test/api/v1/event/9", body=json.dumps(body).encode()))
    row = read_rows(tmp_path / "out" / "requests.jsonl")[0]
    assert row["response_keys"] == ["events"] and row["sample_file"]
    events = read_rows(tmp_path / "out" / "events" / "football.jsonl")
    assert [e["id"] for e in events] == [9] and events[0]["run_id"] == ex.run_id


@pytest.mark.asyncio
async def test_third_party_urls_keep_only_host_and_first_segment(tmp_path, rate_file):
    ex = make_explorer(tmp_path)
    query = "q=leak-marker"  # imzalı token yerine geçen işaret: kayıtlarda hiç görünmemeli
    resp = FakeResponse(f"https://widgets.sir.example.com/abc123/lmt/match/1?{query}",
                        body=json.dumps({"note": query}).encode(),
                        frame_url=f"https://ads.example.net/frame/x?{query}")
    await ex.on_response(resp)
    ex.on_request_failed(FakeRequest(f"https://tracker.example.org/collect/v2?{query}", failure="net::ERR_ABORTED"))
    rows = read_rows(tmp_path / "out" / "requests.jsonl")
    assert [r["url"] for r in rows] == ["https://widgets.sir.example.com/abc123", "https://tracker.example.org/collect"]
    assert rows[0]["origin"] == "https://ads.example.net/frame"
    assert rows[0]["pattern"] == "widgets.sir.example.com/abc123"
    assert resp.body_reads == 0  # üçüncü taraf gövdesi okunmaz, örneği yazılmaz
    assert "leak-marker" not in all_output(tmp_path / "out")


def test_only_the_explorers_own_cancellations_are_skipped(tmp_path, rate_file):
    """Gerçek ağ hataları (ERR_FAILED dahil) kaydedilir; yalnızca bu script'in iptali (BlockedByClient) trafik değildir."""
    ex = make_explorer(tmp_path)
    ex.on_request_failed(FakeRequest("https://www.fakescore.test/api/v1/event/1", failure="net::ERR_BLOCKED_BY_CLIENT"))
    ex.on_request_failed(FakeRequest("https://www.fakescore.test/api/v1/event/2", failure="net::ERR_FAILED"))
    ex.on_request_failed(FakeRequest("https://www.fakescore.test/x.png", resource_type="image", failure="net::ERR_FAILED"))
    rows = read_rows(tmp_path / "out" / "requests.jsonl")
    assert [(r["url"], r["status"], r["failure"]) for r in rows] == [
        ("https://www.fakescore.test/api/v1/event/2", None, "net::ERR_FAILED")]


def test_safe_url():
    s = ex_mod.safe_url
    assert s("https://www.sofascore.com/api/v1/event/1?x=1") == "https://www.sofascore.com/api/v1/event/1?x=1"
    assert s("https://api.sofascore.app/api/v1/team/1/image") == "https://api.sofascore.app/api/v1/team/1/image"
    assert s("https://lmt.example.com/a/b/c?token=1") == "https://lmt.example.com/a"
    assert s("https://example.com/") == "https://example.com/"
    assert s("data:image/png;base64,AAAA") == "data:"
    assert s("") == ""


# --- WebSocket (NATS) maskeleme ----------------------------------------------------------------------------------

class FakeWebSocket:
    def __init__(self, url):
        self.url = url
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


def test_nats_frames_are_masked_before_writing(tmp_path, rate_file):
    ex = make_explorer(tmp_path)
    ws = FakeWebSocket("wss://ws.fakescore.test:9222/")
    ex.on_websocket(ws)
    info = {"server_id": "S1", "client_id": 7, "client_ip": CLIENT_IP, "max_payload": 1048576}
    ws.handlers["framereceived"](f"INFO {json.dumps(info)}\r\n")
    msg = b'{"status.code": 100, "a": 1}'
    ws.handlers["framereceived"](b"MSG sport.football 1 %d\r\n%s\r\n" % (len(msg), msg))
    ws.handlers["framesent"]('CONNECT {"user":"u-name","pass":"p-word","auth_token":"tok","verbose":false}\r\n'
                             'SUB sport.football 1\r\nPUB _EVENTS 18\r\n{"analytics": true}\r\n')
    ws.handlers["framereceived"]("INFO {not json " + CLIENT_IP + "\r\n")
    rows = read_rows(tmp_path / "out" / "ws.jsonl")
    infos = [r for r in rows if r.get("op") == "INFO"]
    assert json.loads(infos[0]["body"])["client_ip"] == "<redacted>"
    assert json.loads(infos[0]["body"])["server_id"] == "S1"
    connect = next(r["line"] for r in rows if r.get("line", "").startswith("CONNECT "))
    opts = json.loads(connect[len("CONNECT "):])
    assert opts["user"] == opts["pass"] == opts["auth_token"] == "<redacted>" and opts["verbose"] is False
    lines = [r["line"] for r in rows if r["kind"] == "sent"]
    assert "SUB sport.football 1" in lines and not any("analytics" in line for line in lines)
    text = all_output(tmp_path / "out")
    for leaked in (CLIENT_IP, "u-name", "p-word", '"tok"'):
        assert leaked not in text


def test_mask_helpers_reject_unparsed_payloads():
    assert ex_mod.mask_info("garbage " + CLIENT_IP) == "<unparsed, redacted>"
    assert ex_mod.mask_info("[1, 2]") == "<unparsed, redacted>"
    assert ex_mod.mask_connect("CONNECT {broken") == "CONNECT <unparsed, redacted>"


# --- FX-29c: istemci konumu ve adresler yazılmadan maskelenir ---------------------------------------------------------

# Sahte değerler (belgeleme aralıkları, uydurma şehir); gerçek country/alpha2 gövdesiyle aynı anahtarlar
FAKE_CITY = "Exampleville"
FAKE_FINGERPRINT = "t00d0000h0_fakefingerprint"
COUNTRY_BODY = {"alpha2": "XX", "continent_code": "EU", "country": "Exampleland", "city": FAKE_CITY,
                "ip": "192.0.2.10", "f": FAKE_FINGERPRINT, "region_code": "EX-01"}
PUSH_V4 = "198.51.100.7"
PUSH_V6 = "2001:db8:0:1::"


@pytest.mark.asyncio
async def test_country_alpha2_body_is_redacted_everywhere(tmp_path, rate_file):
    ex = make_explorer(tmp_path)
    ex.sport = "football"
    await ex.on_response(FakeResponse("https://www.fakescore.test/api/v1/country/alpha2",
                                      body=json.dumps(COUNTRY_BODY).encode()))
    row = read_rows(tmp_path / "out" / "requests.jsonl")[0]
    assert row["response_keys"] == sorted(COUNTRY_BODY)
    with open(os.path.join(ex_mod.ROOT, row["sample_file"]), encoding="utf-8") as f:
        body = json.load(f)["body"]
    assert body == {**COUNTRY_BODY, "city": "<redacted>", "ip": "<redacted>", "f": "<redacted>",
                    "region_code": "<redacted>"}
    text = all_output(tmp_path / "out")
    for leaked in (FAKE_CITY, "192.0.2.10", FAKE_FINGERPRINT, "EX-01"):
        assert leaked not in text


def test_redact_masks_geo_keys_at_any_depth_but_keeps_venues():
    body = {
        "a": {"geo": {"lat": 1.5, "lon": 2.5}, "client_ip": "192.0.2.11", "postal": "00000"},
        "list": [{"latitude": 1.0, "longitude": 2.0, "lat": None, "name": "x"}],
        "event": {"venue": {"city": {"name": FAKE_CITY}, "venueCoordinates": {"latitude": 1.0, "longitude": 2.0}}},
        "note": f"seen from 192.0.2.12 and [{PUSH_V6}]:9222 at 12:30:45, app 2.29.1",
    }
    out = ex_mod.redact(body)
    assert out["a"] == {"geo": "<redacted>", "client_ip": "<redacted>", "postal": "<redacted>"}
    assert out["list"] == [{"latitude": "<redacted>", "longitude": "<redacted>", "lat": None, "name": "x"}]
    assert out["event"] == body["event"]  # stadyum bilgisi herkese açık: dokunulmaz
    assert out["note"] == "seen from <redacted> and [<redacted>]:9222 at 12:30:45, app 2.29.1"
    assert ex_mod.redact(out) == out  # iki kez geçmek bir şey değiştirmez


@pytest.mark.parametrize("text, expected", [
    ("1.2.3", "1.2.3"), ("1.2.3.4.5", "1.2.3.4.5"), ("256.1.1.1", "256.1.1.1"), ("12:30:45", "12:30:45"),
    ("std::string", "std::string"), ("ts 1790856215.508", "ts 1790856215.508"),
    (f"{PUSH_V4}:9222", "<redacted>:9222"), (f"[{PUSH_V6}]:9222", "[<redacted>]:9222"),
    ("::ffff:192.0.2.1", "<redacted>"), ("fe80::1%eth0", "<redacted>"), ("ip=192.0.2.13.", "ip=<redacted>."),
])
def test_mask_ips(text, expected):
    assert ex_mod.mask_ips(text) == expected


def test_nats_info_addresses_are_masked_in_every_field(tmp_path, rate_file):
    ex = make_explorer(tmp_path)
    ws = FakeWebSocket("wss://ws.fakescore.test:9222/")
    ex.on_websocket(ws)
    info = {"server_id": "S1", "server_name": "push-1", "host": PUSH_V4, "port": 9222, "ip": PUSH_V4,
            "client_ip": CLIENT_IP, "cluster": "nats", "connect_urls": [f"{PUSH_V4}:9222", f"[{PUSH_V6}]:9222"]}
    ws.handlers["framereceived"](f"INFO {json.dumps(info)}\r\n")
    msg = json.dumps({"status.code": 100, "ip": CLIENT_IP, "note": f"via {PUSH_V4}"}).encode()
    ws.handlers["framereceived"](b"MSG sport.football 1 %d\r\n%s\r\n" % (len(msg), msg))
    raw = f"not json {PUSH_V4}".encode()
    ws.handlers["framereceived"](b"MSG sport.tennis 2 %d\r\n%s\r\n" % (len(raw), raw))
    ws.handlers["framesent"](f"SUB host.{PUSH_V4} 1\r\n")
    rows = read_rows(tmp_path / "out" / "ws.jsonl")
    body = json.loads(next(r for r in rows if r.get("op") == "INFO")["body"])
    assert body == {**info, "host": "<redacted>", "ip": "<redacted>", "client_ip": "<redacted>",
                    "connect_urls": ["<redacted>:9222", "[<redacted>]:9222"]}
    msgs = {r["subject"]: r["body"] for r in rows if r.get("op") == "MSG"}
    assert msgs["sport.football"] == {"status.code": 100, "ip": "<redacted>", "note": "via <redacted>"}
    assert msgs["sport.tennis"] == "not json <redacted>"
    assert [r["line"] for r in rows if r["kind"] == "sent"] == ["SUB host.<redacted> 1"]
    text = all_output(tmp_path / "out")
    for leaked in (PUSH_V4, PUSH_V6, CLIENT_IP):
        assert leaked not in text


# --- koşu başına bütçe --------------------------------------------------------------------------------------------

LEGACY_STATE = {"started": 1790856030.37, "api_requests": 3788, "updated": "2026-10-01T14:01:57+00:00"}


def _legacy_out(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "_state.json").write_text(json.dumps(LEGACY_STATE), encoding="utf-8")
    rows = [{"url": "https://www.sofascore.com/api/v1/event/1", "host": "www.sofascore.com", "status": 200}] * 3
    (out / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return out


def _args(out, *extra):
    return ex_mod._parser().parse_args(["serve", "ctl", "--out", str(out), *extra])


def test_spent_run_is_not_resumed_and_its_state_is_left_alone(tmp_path, rate_file):
    out = _legacy_out(tmp_path)
    before = (out / "_state.json").read_bytes()
    with pytest.raises(SystemExit) as exc:
        ex_mod.explorer_from_args(_args(out), bridge=FakeBridge())
    assert "--new-run" in str(exc.value)
    assert (out / "_state.json").read_bytes() == before


def test_new_run_starts_a_fresh_budget_and_keeps_the_old_run(tmp_path, rate_file):
    out = _legacy_out(tmp_path)
    old_rows = (out / "requests.jsonl").read_bytes()
    ex = ex_mod.explorer_from_args(_args(out, "--new-run", "--run-id", "lv-2026-10-09", "--max-requests", "400",
                                         "--max-hours", "2"), bridge=FakeBridge())
    assert (ex.run_id, ex.api_count, ex.max_requests, ex.max_seconds) == ("lv-2026-10-09", 0, 400, 7200)
    assert not ex.over_budget()
    assert "/event/{id}" in ex.known  # bilinen pattern'ler koşulardan bağımsızdır (new_patterns tüm kayda göre)
    state = json.loads((out / "_state.json").read_text(encoding="utf-8"))
    assert state["run_id"] == "lv-2026-10-09" and state["api_requests"] == 0
    assert state["previous_runs"] == [LEGACY_STATE]
    assert (out / "requests.jsonl").read_bytes() == old_rows  # eski kayıtlar silinmez

    # Bu koşunun satırları run_id taşır; sürdürülen koşu yalnızca kendi satırlarını sayar
    ex.on_request_failed(FakeRequest("https://www.sofascore.com/api/v1/event/2", failure="net::ERR_FAILED"))
    ex._save_state()
    again = ex_mod.Explorer(str(out), bridge=FakeBridge())
    assert again.run_id == "lv-2026-10-09" and again.api_count == 1
    assert json.loads((out / "_state.json").read_text(encoding="utf-8"))["previous_runs"] == [LEGACY_STATE]

    # İkinci yeni koşu: iki eski koşu da saklanır; aynı kimlik ikinci kez kullanılamaz
    with pytest.raises(SystemExit):
        ex_mod.explorer_from_args(_args(out, "--new-run", "--run-id", "lv-2026-10-09"), bridge=FakeBridge())
    third = ex_mod.explorer_from_args(_args(out, "--new-run"), bridge=FakeBridge())
    runs = json.loads((out / "_state.json").read_text(encoding="utf-8"))["previous_runs"]
    assert [r.get("run_id") for r in runs] == [None, "lv-2026-10-09"] and third.run_id not in (None, "lv-2026-10-09")


def test_resuming_with_another_run_id_is_refused(tmp_path, rate_file):
    out = _legacy_out(tmp_path)
    ex_mod.explorer_from_args(_args(out, "--new-run", "--run-id", "a"), bridge=FakeBridge())
    with pytest.raises(SystemExit) as exc:
        ex_mod.explorer_from_args(_args(out, "--run-id", "b"), bridge=FakeBridge())
    assert "--new-run" in str(exc.value)


def test_ws_subjects_file_is_per_run(tmp_path, rate_file):
    out = _legacy_out(tmp_path)
    legacy = ex_mod.Explorer(str(out), bridge=FakeBridge())
    assert legacy.ws_subjects_path().endswith(os.sep + "ws_subjects.json")
    new = ex_mod.Explorer(str(out), new_run=True, run_id="r2", bridge=FakeBridge())
    assert new.ws_subjects_path().endswith(os.sep + "ws_subjects_r2.json")


# --- gerçek tarayıcı, yerel sahte site, görsel seli --------------------------------------------------------------

N_API = 12
FLOOD = 12_000  # sürücünün tür başına 10.000 nesne sınırını aşacak kadar görsel isteği

PAGE = """<!doctype html><html><head><title>0</title></head><body><script>
const N = %(n)d, FLOOD = %(flood)d;
let done = 0, made = 0;
for (let i = 0; i < N; i++) {
  fetch('/api/v1/event/' + (100 + i) + '/statistics')
    .then(r => r.json()).catch(() => null)
    .then(() => { done++; document.title = String(done); });
}
fetch('http://ads.thirdparty.test/bid/abc?q=page-marker', {mode: 'no-cors'}).catch(() => null);
// Görsel seli: API istekleri sırada beklerken binlerce görsel isteği (her biri kesiciden geçer)
const timer = setInterval(() => {
  for (let j = 0; j < 100 && made < FLOOD; j++, made++) {
    const img = new Image();
    img.src = (made %% 2 ? 'http://img.fakescore.test' : '') + '/img/' + made + '.png';
  }
  if (made >= FLOOD) clearInterval(timer);
}, 1);
</script></body></html>"""


class _Site(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: list = []
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_CONNECT(self):  # https tüneli asla açılmaz
        with self.lock:
            self.hits.append((time.monotonic(), "CONNECT", self.path))
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        parts = urlsplit(self.path)
        host = parts.hostname or (self.headers.get("Host") or "").split(":")[0]
        path = parts.path if parts.scheme else self.path.split("?")[0]
        with self.lock:
            self.hits.append((time.monotonic(), host, path))
        if path.startswith("/api/"):
            body, ctype = json.dumps({"event": {"id": 1}, "path": path}).encode(), "application/json"
        elif path == "/page":
            body, ctype = (PAGE % {"n": N_API, "flood": FLOOD}).encode(), "text/html; charset=utf-8"
        else:
            body, ctype = b"{}", "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


class _PlaywrightBridge:
    """Explorer'ın köprüden kullandığı yüzey: bağlam, sayfa, ensure_ready, close."""

    def __init__(self, context, page):
        self.context = context
        self.page = page

    async def ensure_ready(self):
        pass

    async def close(self):
        pass


@pytest.mark.browser
@pytest.mark.asyncio
async def test_explorer_records_every_request_under_an_image_flood(tmp_path, rate_file, monkeypatch):
    patchright = pytest.importorskip("patchright.async_api")
    gap = 0.4
    monkeypatch.setattr(rc, "MIN_REQUEST_GAP", gap)
    monkeypatch.setattr(ex_mod, "PAGE_GAP", 0.0)
    slots = []
    wait_slot = rc._wait_rate_slot

    def recorded_slot():
        wait_slot()
        # Sıranın anı kilit dosyasına yazılan zamandır (aralık ona göre beklenir); dönüşten sonra okunan saat, görsel
        # seli altında iş parçacığı gecikmesiyle kayabilir. Bu süreçte sıralar tek tek alınır: dosya bu sıranındır.
        with open(rc._RATE_FILE, encoding="utf-8") as f:
            slots.append(float(f.read().strip()))

    monkeypatch.setattr(rc, "_wait_rate_slot", recorded_slot)
    cs = ex_mod.cs
    monkeypatch.setattr(cs, "HOME_URL", cs.HOME_URL)
    monkeypatch.setattr(cs, "CAPTCHA_URL", cs.CAPTCHA_URL)

    _Site.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy = f"http://127.0.0.1:{server.server_address[1]}"
    out = tmp_path / "out"
    try:
        async with patchright.async_playwright() as p:
            # Tüm trafik sahte siteden geçer (vekil): .test alan adları dışarıda çözülmez, https tüneli açılmaz
            browser = await p.chromium.launch(headless=True, channel="chromium", proxy={"server": proxy})
            try:
                context = await browser.new_context()
                # Scrapling de bağlama başlangıç betiği ekler; patchright onları kendi kesicisiyle enjekte eder
                await context.add_init_script("window.__probe = 1")
                page = await context.new_page()
                ex = ex_mod.Explorer(str(out), sofa_host=FAKE_HOST, bridge=_PlaywrightBridge(context, page),
                                     quiet_url="http://www.fakescore.test/robots.txt")
                await ex.start()
                t0 = time.monotonic()
                res = await ex.goto({"url": "http://www.fakescore.test/page", "sport": "football",
                                     "page_type": "event", "settle": 2, "dwell": 90})
                ex.active = False
                assert res["error"] is None, res
                assert await page.title() == str(N_API)
                elapsed = time.monotonic() - t0
                for _ in range(120):  # selin sonu (görseller kesiciden geçmeye devam ediyor olabilir)
                    if ex.blocked >= FLOOD:
                        break
                    await asyncio.sleep(0.25)

                # Komut bitti: sayfanın kendi isteği gönderilmez
                idle = await page.evaluate(
                    "() => fetch('/api/v1/event/999/idle').then(() => 'sent', () => 'dropped')")
                assert idle == "dropped"
                await asyncio.sleep(0.5)
                await asyncio.gather(*list(ex._tasks))
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()

    hits = list(_Site.hits)
    api_hits = [(t, path) for t, host, path in hits if path.startswith("/api/")]
    assert len(api_hits) == N_API, api_hits  # her API isteği tam bir kez gitti, boştaki gitmedi
    # Kilit sırası en az `gap` arayla verildi; sunucuya varış anları ağ gecikmesiyle oynar, ortalama hız yine ≤ 1/gap
    assert len(slots) == N_API and min(b - a for a, b in zip(slots, slots[1:], strict=False)) >= gap * 0.95
    times = sorted(t for t, _ in api_hits)
    assert times[-1] - times[0] >= (N_API - 1) * gap * 0.9
    assert not [h for h in hits if h[2].startswith("/img/")]  # görseller ağa hiç gitmedi
    assert not [h for h in hits if "sofascore" in str(h[1]).lower()]
    assert ex.api_count == N_API and ex.intercept_errors == 0
    assert ex.idle_dropped >= 1
    assert ex.blocked >= FLOOD, ex.blocked  # sel gerçekten oldu (sürücünün nesne sınırının üstü)

    rows = read_rows(out / "requests.jsonl")
    api_rows = [r for r in rows if "/api/v1/" in r["url"]]
    assert sorted(r["url"] for r in api_rows) == sorted(
        f"http://www.fakescore.test/api/v1/event/{100 + i}/statistics" for i in range(N_API))
    assert all(r["status"] == 200 and r["pattern"] == "www.fakescore.test/api/v1/event/{id}/statistics"
               and r["response_keys"] == ["event", "path"] and "body_error" not in r for r in api_rows), api_rows
    third = [r for r in rows if r["host"] == "ads.thirdparty.test"]
    assert third and all(r["url"] == "http://ads.thirdparty.test/bid" for r in third)
    assert "page-marker" not in all_output(out)
    assert not [r for r in read_rows(out / "pages.jsonl") if r["op"] in ("route-error", "intercept-error")]
    assert elapsed < 80


# --- FX-29b: gerçek tarayıcı, sayfa sırada istek varken yeni belgeye geçiyor ------------------------------------

N_OLD, N_NEW = 8, 4
REDIRECT_PAGE = """<!doctype html><html><head><title>%(doc)s</title></head><body><script>
for (let i = 0; i < %(n)d; i++) fetch('/api/v1/event/%(doc)s/' + i).then(r => r.text()).catch(() => null);
%(redirect)s
</script></body></html>"""


class _RedirectSite(BaseHTTPRequestHandler):
    """
    SofaScore'un canlıda yaptığının küçük kopyası: `/football/match` belgesi API isteklerini atar, ~0,3 sn sonra
    istemci tarafında `/tr/football/match` belgesine yeniden yüklenir; yeni belge kendi isteklerini atar.
    """

    protocol_version = "HTTP/1.1"
    hits: list = []
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        with self.lock:
            self.hits.append((time.monotonic(), "CONNECT", self.path))
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        parts = urlsplit(self.path)
        host, path = parts.hostname or "", parts.path
        with self.lock:
            self.hits.append((time.monotonic(), host, path))
        if path == "/football/match":
            body = REDIRECT_PAGE % {"doc": "old", "n": N_OLD,
                                    "redirect": "setTimeout(() => location.replace('/tr/football/match'), 300);"}
            ctype = "text/html; charset=utf-8"
        elif path == "/tr/football/match":
            body, ctype = REDIRECT_PAGE % {"doc": "new", "n": N_NEW, "redirect": ""}, "text/html; charset=utf-8"
        elif path.startswith("/api/"):
            body, ctype = json.dumps({"event": {"id": 1}, "path": path}), "application/json"
        else:
            body, ctype = "User-agent: *", "text/plain"
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass


@pytest.mark.browser
@pytest.mark.asyncio
async def test_page_reloading_into_a_new_document_does_not_starve_its_requests(tmp_path, rate_file, monkeypatch):
    """
    FX-29b canlı koşulu (2026-10-09, `goto` → istemci tarafı `/tr/` yeniden yüklemesi): eski belgenin sıradaki
    istekleri Chromium'da sessizce ölür (ne sayfaya ne patchright'a hata düşer). Önceki sürüm her birine 1 sıra
    harcayıp continueRequest'te "Invalid InterceptionId" alıyordu; yeni belgenin istekleri arkada kalıyordu.
    Beklenen: ölüler sıra almaz, hiçbiri ağa gitmez, yeni belgenin her isteği bir kez gider ve kaydedilir.
    """
    patchright = pytest.importorskip("patchright.async_api")
    slots = _recorded_slots(monkeypatch, 0.5)
    monkeypatch.setattr(ex_mod, "PAGE_GAP", 0.0)
    cs = ex_mod.cs
    monkeypatch.setattr(cs, "HOME_URL", cs.HOME_URL)
    monkeypatch.setattr(cs, "CAPTCHA_URL", cs.CAPTCHA_URL)

    _RedirectSite.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectSite)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = tmp_path / "out"
    try:
        async with patchright.async_playwright() as p:
            browser = await p.chromium.launch(headless=True, channel="chromium",
                                              proxy={"server": f"http://127.0.0.1:{server.server_address[1]}"})
            try:
                context = await browser.new_context()
                await context.add_init_script("window.__probe = 1")
                page = await context.new_page()
                await page.goto("http://www.fakescore.test/robots.txt")
                ex = ex_mod.Explorer(str(out), sofa_host=FAKE_HOST, bridge=_PlaywrightBridge(context, page),
                                     quiet_url="http://www.fakescore.test/robots.txt")
                await ex.start()
                probe = await ex.probe({"hold": 1.0})  # öz denetim: bekletilen istek yerelde yanıtlanır
                res = await ex.goto({"url": "http://www.fakescore.test/football/match", "sport": "football",
                                     "page_type": "event", "settle": 2, "dwell": 30})
                ex.active = False
                await asyncio.sleep(0.5)
                await asyncio.gather(*list(ex._tasks))
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()

    assert probe["ok"] is True and probe["held_s"] == 1.0, probe
    assert res["url"] == "http://www.fakescore.test/tr/football/match", res
    hits = list(_RedirectSite.hits)
    assert not [h for h in hits if "explorer-probe" in str(h[1]) + str(h[2])]  # öz denetim ağa çıkmadı
    old_hits = [path for _, _, path in hits if path.startswith("/api/v1/event/old/")]
    new_hits = [path for _, _, path in hits if path.startswith("/api/v1/event/new/")]
    assert sorted(new_hits) == sorted(f"/api/v1/event/new/{i}" for i in range(N_NEW))  # her biri bir kez
    assert len(set(old_hits)) == len(old_hits) and len(old_hits) < N_OLD  # ölüler gönderilmedi
    assert ex.intercept_errors == 0 and res["step_intercept_errors"] == 0, res
    assert res["page_requests"] == ex.api_count == len(old_hits) + N_NEW  # sayılan = giden
    assert res["step_gone"] == ex.gone == N_OLD - len(old_hits), res
    assert len(slots) <= ex.api_count + 1  # ölülere sıra harcanmadı (en fazla o an kilitte bekleyen)
    rows = read_rows(out / "requests.jsonl")
    new_rows = [r for r in rows if "/api/v1/event/new/" in r["url"]]
    assert sorted(r["url"] for r in new_rows) == sorted(
        f"http://www.fakescore.test/api/v1/event/new/{i}" for i in range(N_NEW))
    assert all(r["status"] == 200 and r["response_keys"] == ["event", "path"] for r in new_rows), new_rows
    gone = [r for r in read_rows(out / "pages.jsonl") if r["op"] == "request-gone"]
    assert len(gone) == ex.gone and all("/api/v1/event/old/" in r["url"] for r in gone)
