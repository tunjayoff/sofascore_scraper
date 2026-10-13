"""
S2: canlı push kaynağının soak kaydedicisi (scripts/soak_live.py).

Ağ yok, SofaScore yok, tarayıcı yok. `direct` için push sunucusu tests/test_live_direct_source.py'deki süreç içi
sahte NATS sunucusudur (yalnızca 127.0.0.1; kayıtlı INFO ve kareler); `page` için sayfa tests/test_live_push_source.py'deki
sahte Playwright sayfasıdır. Maç sayfaları ve canlı liste sahte bir istek işlevidir (tests/test_live_service.py
FakeApi). Kimlik bilgisi her testte açıkça sahte parçalardan kurulur. Kaydedici uygulamanın gerçek parçalarını
çalıştırır: LiveService, DirectConnection ya da PageSource, hakem, yoklama kaynağı.

  * kareler kaydedilir: yollar, yalnızca durum ve skor değerleri, changeTimestamp; tetiklenen maç sayfası okuması
    ve "kare → /event/{id}" gecikmesi (CDN'in eski yanıtı yinelenir)
  * öldürme: hakem yoklamaya geçer, bağlantı (ya da sayfa) yeniden açılır, hakem push'a döner; kesinti penceresi,
    yedek süresi, el sıkışma → INFO süresi
  * yoklama öndeyken dakikalık okumalar: öncelik, en uzun süredir okunmayan, dakikanın payı, atlananlar
  * bütçe bitince REST okuması yapılmaz
  * gizlilik: kayıtta IPv4/IPv6 yok, user/pass/auth_token değeri yok, kimlik bilgisi, sunucu adresi, INFO gövdesi
    yok; kimlik bilgisi karşılaştırması yalnızca same/different; tests/test_research_privacy.py denetimi temiz
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

import pytest

import conftest
import test_research_privacy as privacy
from sofascore_scraper.services.live import push_source as ps
from sofascore_scraper.services.live import supervisor as sv
from sofascore_scraper.services.live.push_source import PushFeed, attach
from test_live_direct_source import FakeNatsServer, FakeReader, fake_credential, fake_value, server_frame
from test_live_push_source import FakePage, finish_frame, wait_for, wire
from test_live_service import FB_DONE, FB_LIVE, FakeApi, fx

pytest.importorskip("fcntl")  # araştırma script'lerinin ortak kilidi (Linux/macOS araçları)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def _import_soak() -> Any:
    """Script içe aktarılırken ortam değişkeni koyar (profil, log seviyesi): test sürecinin ortamı değişmesin."""
    saved = dict(os.environ)
    sys.path.insert(0, SCRIPTS)
    try:
        import soak_live
    finally:
        sys.path.remove(SCRIPTS)
        os.environ.clear()
        os.environ.update(saved)
    return soak_live


soak_live = _import_soak()

# Sayfanın INFO karesi istemcinin adresini taşır (belgeleme aralığından sahte adres): hiçbir yere yazılmamalı
PAGE_INFO = "INFO " + json.dumps({"version": "2.12.15", "auth_required": True, "client_ip": "198.51.100.9",
                                  "connect_urls": ["203.0.113.4:9222"]}) + "\r\n"


# --- yardımcılar ------------------------------------------------------------------------------------------


def rows(run: Path, name: str) -> List[Dict[str, Any]]:
    path = run / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def events(run: Path, name: str, event: str) -> List[Dict[str, Any]]:
    return [r for r in rows(run, name) if r.get("event") == event]


def reads_by(run: Path, who: str) -> List[Dict[str, Any]]:
    return [r for r in rows(run, "reads") if r.get("by") == who]


def summary(run: Path) -> Dict[str, Any]:
    return json.loads((run / "summary.json").read_text(encoding="utf-8"))


class SlowReader(FakeReader):
    """Kimlik bilgisi okuması biraz sürer: okuma sırasında kaynak örneklemesi sıklaşmalı."""

    def read(self, sport: str) -> Any:
        time.sleep(0.3)
        return super().read(sport)


class KillableOpener:
    """Sayfa açıcının sahtesi: gerçek dinleyiciler (attach) sahte sayfaya bağlanır; tutamacın kapanışı sayfayı kapatır."""

    def __init__(self) -> None:
        self.pages: Dict[str, FakePage] = {}
        self.opened: List[str] = []

    def open(self, sport: str, feed: PushFeed) -> Any:
        self.opened.append(sport)
        page = FakePage()
        attach(page, feed)
        self.pages[sport] = page
        state = {"closed": False}

        def close() -> None:
            if not state["closed"]:
                state["closed"] = True
                page.emit("close")

        return SimpleNamespace(ready=True, failed=None, close=close)

    def close(self) -> None:
        pass


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data"
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(path))
    monkeypatch.setenv("SOFASCORE_REFRESH__WINDOW_HOURS", "48")
    return path


@pytest.fixture
def fast_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Servis karelere 50 ms'de bir bakar, istekleri arasında 1 sn beklemez, kapanan sayfayı 0,3 sn sonra açar."""
    monkeypatch.setattr(sv, "PUSH_DRAIN_SECONDS", 0.05)
    monkeypatch.setattr(sv, "MIN_REQUEST_SPACING_SECONDS", 0.01)
    monkeypatch.setattr(ps, "OPEN_RETRY_FIRST_SECONDS", 0.3)


@pytest.fixture
def servers() -> Iterator[Any]:
    made: List[FakeNatsServer] = []

    def make(**options: Any) -> FakeNatsServer:
        made.append(FakeNatsServer(**options))
        return made[-1]

    yield make
    for s in made:
        s.close()


@pytest.fixture
def soaks() -> Iterator[Any]:
    made: List[Any] = []

    def make(tmp_path: Path, data_dir: Path, srv: Any, api: FakeApi, *, sports=("football",),
             budget: int = 100, reader: Any = None, **options: Any) -> Any:
        run = tmp_path / "research" / "soak" / "t-run"
        conn = dict(blocked=lambda: None, recv_poll=0.02, reconnect_first=0.5, reconnect_max=1.0,
                    read_retry_first=0.05, read_retry_max=0.1, stable_after=0.0)
        conn.update(options.pop("connection_options", {}))
        if options.get("source") not in ("page", "off"):
            options["reader"] = reader or FakeReader(fake_credential(srv.url, "soak"))
            options["connection_options"] = conn
        options.setdefault("fallback_window", 60.0)
        options.setdefault("joined_random", 0)
        soak = soak_live.Soak(
            str(run), list(sports), run_id="t-run", data_dir=str(data_dir), budget=budget, base_fetch=api,
            gate=lambda: None, resource_seconds=0.2, reading_resource_seconds=0.05, event_retry_seconds=0.2,
            **options)
        made.append(soak)
        return soak, run

    yield make
    for soak in made:
        if soak.running:
            soak.stop()


def live_and_done(eid: int = 500) -> Any:
    live = fx(FB_LIVE, eid, tournament_id=17)
    done = fx(FB_DONE, eid, tournament_id=17)
    done["startTimestamp"] = live["startTimestamp"]
    return live, done


def first_half(eid: int) -> Dict[str, Any]:
    """İlk yarıda bir maç: bitişe yakın değil, servisin yoklama kaynağı sayfasını okumaz."""
    ev = fx(FB_LIVE, eid, tournament_id=17)
    ev["status"] = {"code": 6, "type": "inprogress", "description": "1st half"}
    return ev


def switches(soak: Any, sport: str = "football") -> List[str]:
    return [f"{s['from']}>{s['to']}" for s in soak.switches if s.get("sport") == sport]


# --- kareler ve maç sayfası okuması ---------------------------------------------------------------------------


def test_frames_are_recorded_and_a_finish_triggers_one_event_read_with_its_latency(
        tmp_path: Path, data_dir: Path, fast_service: None, servers: Any, soaks: Any) -> None:
    srv = servers(accept=fake_value("soak"))
    live, done = live_and_done()
    api = FakeApi({"football": [live]}, {500: live})  # maç sayfası (CDN) önce eski hali verir
    soak, run = soaks(tmp_path, data_dir, srv, api, reader=SlowReader(fake_credential(srv.url, "soak")))
    soak.start()
    wait_for(lambda: switches(soak) == ["poll>direct"] and api.calls.count("/sport/football/events/live") >= 1)
    frame = finish_frame(500, live["startTimestamp"])
    srv.msg("sport.football", frame)
    wait_for(lambda: len(reads_by(run, "recorder")) >= 1)
    done["homeScore"]["current"], done["awayScore"]["current"] = frame["homeScore.current"], frame["awayScore.current"]
    api.events[500] = done  # sayfa bitişi ve son skoru artık gösteriyor: yineleme onu okur
    wait_for(lambda: soak.stats["football"].reads_ok == 1)
    soak.stop()

    [row] = rows(run, "frames")
    assert row["sport"] == "football" and row["event_id"] == 500 and row["subject"] == "sport"
    assert row["paths"] == sorted(k for k in frame if k != "id")
    assert "winnerCode" in row["paths"] and "status.description" in row["paths"]
    # değer yalnızca durum kodu/türü ve skor alanları için
    assert set(row["values"]) == {k for k in frame if k in ("status.code", "status.type")
                                  or k.startswith(("homeScore.", "awayScore."))}
    assert row["values"]["status.type"] == "finished" and row["values"]["homeScore.current"] == frame["homeScore.current"]
    assert row["change_ts"] == frame["changes.changeTimestamp"] and row["triggers"] == ["finish", "score"]
    assert row["lag_s"] == pytest.approx(row["ts"] - frame["changes.changeTimestamp"], abs=0.01)

    reads = reads_by(run, "recorder")
    assert [(r["attempt"], r["reflects"], r["final"]) for r in reads] == [(1, False, False), (2, True, True)]
    assert reads[0]["retry_in_s"] == pytest.approx(0.2) and reads[0]["read_class"] == "live"
    assert reads[1]["trigger"]["kinds"] == ["finish", "score"] and reads[1]["trigger"]["sources"] == ["frame"]
    assert reads[1]["trigger_to_read_s"] >= 0.2 and reads[1]["status"] == 200
    assert reads[1]["trigger"]["ts"] == pytest.approx(row["ts"], abs=0.01)
    lists = [r for r in rows(run, "reads") if r["kind"] == "list"]
    assert lists and all(r["by"] == "service" and r["sport"] == "football" and r["live_count"] == 1 for r in lists)

    s = summary(run)
    fb = s["sports_detail"]["football"]
    assert s["final"] is True and s["source"] == "direct" and fb["frames"] == 1 and fb["finish_frames"] == 1
    assert fb["finish_push_lag_s"]["n"] == 1 and fb["trigger_to_event_read_s"]["finish"]["n"] == 1
    assert fb["event_reads_reflecting"] == 1 and fb["triggers"] == {"finish": 1, "score": 1}
    assert s["requests"]["used"] == len(api.calls) and s["requests"]["by_who_kind_status"]["recorder:event:200"] == 2
    assert any("aiohttp" in n and "standard-library" in n for n in s["notes"])
    # kimlik bilgisi okunurken kaynaklar sık örneklenir; okumanın zirvesi özette
    [read] = s["connection"]["credential_reads"]
    assert read["sport"] == "football" and read["ok"] and read["samples"] >= 2 and read["peak_rss_total_mb"] > 0
    assert read["connect_vs_previous"] is None  # ilk okuma: karşılaştırılacak önceki yok
    assert s["connection"]["credential_read_peak_rss_total_mb"] == read["peak_rss_total_mb"]
    resources = rows(run, "resources")
    assert any(r["credential_read"] for r in resources)
    assert {"rss_self_mb", "rss_bridge_mb", "rss_live_mb", "rss_other_mb", "rss_total_mb"} <= set(resources[0])
    assert set(s["resources"]["peak_rss_mb"]) == {"self", "bridge", "live", "total"}


def test_the_longest_msg_gap_while_the_list_shows_live_matches_is_reported(
        tmp_path: Path, data_dir: Path, fast_service: None, servers: Any, soaks: Any) -> None:
    srv = servers(accept=fake_value("soak"))
    live, _ = live_and_done()
    api = FakeApi({"football": [live]}, {500: live})
    soak, run = soaks(tmp_path, data_dir, srv, api)
    soak.start()
    wait_for(lambda: switches(soak) == ["poll>direct"])
    time.sleep(0.3)
    soak.stop()
    [gap, *_] = summary(run)["sports_detail"]["football"]["longest_msg_gaps_while_live"]
    assert gap["seconds"] > 0.3 and gap["live_count_max"] == 1
    assert 0 < gap["leader_push_share"] < 1 and gap["pongs"] >= 1  # açılıştan önce yoklama önde
    assert {"connection_open_share", "server_pings", "client_pings", "longest_without_ping_or_pong_s"} <= set(gap)


# --- öldürme: yoklamaya geçiş ve geri dönüş ----------------------------------------------------------------------


def test_a_kill_switches_to_poll_reconnects_and_switches_back(
        tmp_path: Path, data_dir: Path, fast_service: None, servers: Any, soaks: Any) -> None:
    srv = servers(accept=fake_value("soak"))
    live, _ = live_and_done()
    api = FakeApi({"football": [live]}, {500: live})
    soak, run = soaks(tmp_path, data_dir, srv, api)
    soak.start()
    wait_for(lambda: switches(soak) == ["poll>direct"])
    lists_before = api.calls.count("/sport/football/events/live")
    assert soak.kill("kill-every")
    wait_for(lambda: switches(soak) == ["poll>direct", "direct>poll", "poll>direct"])
    assert srv.connections == 2 and srv.subs == {"sport.football": 1}
    soak.stop()

    leader = [r for r in rows(run, "leader") if r["event"] == "switch"]
    assert [(r["from"], r["to"], r["reason"], r["fallback"]) for r in leader] == [
        ("poll", "direct", "push_connected", "stop"), ("direct", "poll", "push_disconnected", "start"),
        ("poll", "direct", "push_connected", "stop")]
    assert api.calls.count("/sport/football/events/live") > lists_before  # yoklama turu yedekte
    [kill] = events(run, "connection", "kill")
    assert kill["cause"] == "kill-every" and kill["done"] is True and kill["session"] == 1
    ends = events(run, "connection", "session_end")
    assert ends[0]["killed"] == "kill-every" and ends[0]["outcome"] == "dropped"
    subs = events(run, "connection", "subscribe")
    assert [(s["session"], s["subject"], s["resubscribe"]) for s in subs] == [
        (1, "sport.football", False), (2, "sport.football", True)]
    infos = events(run, "connection", "info")
    assert [i["session"] for i in infos] == [1, 2] and all(i["handshake_to_info_s"] >= 0 for i in infos)
    assert [h["status"] for h in events(run, "connection", "handshake")] == [101, 101]
    assert len(events(run, "connection", "credential_read")) == 1  # kopmada kimlik bilgisi yeniden okunmaz

    s = summary(run)
    windows = s["connection"]["downtime_windows"]
    assert windows[0]["cause"] == "start"
    killed = [w for w in windows if w["cause"] == "dropped: closed by the server (kill-every)"]
    assert len(killed) == 1 and not killed[0]["until_run_end"]
    assert s["sports_detail"]["football"]["downtime_windows"][1]["cause"] == killed[0]["cause"]
    assert 0 < s["sports_detail"]["football"]["fallback_time_share"] < 1
    assert s["connection"]["handshake_to_info_s"]["n"] == 2 and s["connection"]["sessions"] >= 2
    assert [k["cause"] for k in s["connection"]["kills"]] == ["kill-every"]
    assert [w["to"] for w in s["leader_switches"]] == ["direct", "poll", "direct"]


def test_the_page_source_is_recorded_and_a_kill_closes_its_page(
        tmp_path: Path, data_dir: Path, fast_service: None, soaks: Any) -> None:
    live, _ = live_and_done()
    api = FakeApi({"football": [live]}, {500: live})
    opener = KillableOpener()
    soak, run = soaks(tmp_path, data_dir, None, api, source="page", page_opener=opener)
    soak.start()
    wait_for(lambda: "football" in opener.pages and api.calls.count("/sport/football/events/live") >= 1)
    page = opener.pages["football"]
    page.connect(credential=fake_value("page"), info=PAGE_INFO)  # sitenin kodu CONNECT gönderir: dinlenmez
    wait_for(lambda: switches(soak) == ["poll>page"])
    page.ws.receive(("PING\r\n" + wire("sport.football", finish_frame(500, live["startTimestamp"]))
                     + "-ERR 'Slow Consumer'\r\n").encode())
    wait_for(lambda: len(rows(run, "frames")) == 1 and soak.errors)
    assert soak.kill("kill-at")
    wait_for(lambda: switches(soak) == ["poll>page", "page>poll"] and len(opener.opened) == 2)
    opener.pages["football"].connect(info=PAGE_INFO)
    wait_for(lambda: switches(soak) == ["poll>page", "page>poll", "poll>page"])
    soak.stop()

    [frame] = rows(run, "frames")
    assert frame["event_id"] == 500 and frame["triggers"]
    assert [(r["from"], r["to"], r["reason"]) for r in rows(run, "leader") if r["event"] == "switch"] == [
        ("poll", "page", "push_connected"), ("page", "poll", "push_unavailable"), ("poll", "page", "push_connected")]
    [kill] = events(run, "connection", "kill")
    assert kill["pages"] == ["football"] and kill["done"] is True
    gone = events(run, "connection", "page_gone")
    assert gone[0]["reason"] == "page closed (kill-at)"
    assert [i["session"] for i in events(run, "connection", "info")] == [1, 2]
    assert [e["text"] for e in events(run, "connection", "error")] == ["Slow Consumer"]
    assert len(events(run, "connection", "server_ping")) == 1
    assert events(run, "connection", "page_open")[0]["sport"] == "football"
    s = summary(run)
    assert s["source"] == "page" and any("page" in n for n in s["notes"][:1])
    assert any(w["cause"] == "page closed (kill-at)" for w in s["sports_detail"]["football"]["downtime_windows"])
    assert s["connection"]["credential_reads"] == []
    assert_private(run, needles=(fake_value("page"), "198.51.100.9", "203.0.113.4", "client_ip", "connect_urls"))


def test_run_follows_the_kill_schedule_and_writes_the_summary(
        tmp_path: Path, data_dir: Path, fast_service: None, servers: Any, soaks: Any) -> None:
    srv = servers(accept=fake_value("soak"))
    live, _ = live_and_done()
    api = FakeApi({"football": [live]}, {500: live})
    soak, run = soaks(tmp_path, data_dir, srv, api, kill_at=0.01)  # 0,6 sn sonra bir kez
    soak.run(hours=2.0 / 3600, tick=0.05)
    assert not soak.running
    [kill] = events(run, "connection", "kill")
    assert kill["cause"] == "kill-at" and kill["done"] is True
    s = summary(run)
    assert s["final"] and 1.9 < s["duration_s"] < 10 and switches(soak)[:2] == ["poll>direct", "direct>poll"]


def test_the_kill_schedule() -> None:
    k = soak_live.KillSchedule(every_minutes=10, at_minutes=3)
    assert [k.due(t) for t in (0, 179, 180, 181, 599, 600, 601, 1250, 1300)] == [
        None, None, "kill-at", None, None, "kill-every", None, "kill-every", None]
    assert soak_live.KillSchedule().due(10 ** 6) is None


# --- liste öndeyken: joined maçlar 60 sn'de bir, diğerleri hız elverdikçe -----------------------------------


def minute_rows(run: Path) -> List[Dict[str, Any]]:
    return [r for r in rows(run, "fallback") if r.get("event") == "minute"]


def test_while_the_list_leads_joined_matches_are_read_every_minute_and_others_as_the_rate_allows(
        tmp_path: Path, data_dir: Path, fast_service: None, soaks: Any) -> None:
    matches = {eid: first_half(eid) for eid in (601, 602, 603)}
    api = FakeApi({"football": list(matches.values())}, dict(matches))
    # push hiç bağlanmaz (kimlik bilgisi yok): liste hep önde. Dakika 1 sn, hız 4/sn, listeye pay 0,5 → dakikada 2
    soak, run = soaks(tmp_path, data_dir, None, api, reader=FakeReader(), fallback_window=1.0, rate=4.0,
                      list_share=0.5, joined_ids=[603], poll_interval=1.0)
    soak.start()
    wait_for(lambda: len([r for r in minute_rows(run) if r["live"] and not r["partial"]]) >= 4, seconds=15)
    soak.stop()

    minutes = [r for r in minute_rows(run) if r["live"] and not r["partial"]]
    assert all((r["live"], r["joined_live"], r["cap"], r["cut"]) == (3, 1, 2, "share") for r in minutes)
    assert all(r["reads"] == {"joined": 1, "listed": 1, "other": 0} for r in minutes)
    assert all(r["skipped"] == {"joined": 0, "listed": 1} for r in minutes)
    by_minute: Dict[float, List[Any]] = {}
    for r in reads_by(run, "fallback"):
        by_minute.setdefault(r["minute"], []).append((r["reason"], r["event_id"]))
    full = [by_minute[m["minute"]] for m in minutes]
    assert all(reads[0] == ("joined", 603) for reads in full)  # acil: joined önce
    # sonra birikmiş iş, en uzun süredir okunmayan önce: 601 ile 602 sırayla
    listed = [reads[1][1] for reads in full[:4]]
    assert listed in ([601, 602, 601, 602], [602, 601, 602, 601]) and all(r[1][0] == "listed" for r in full)
    assert reads_by(run, "recorder") == []  # liste değişmedi: tetiklenen okuma yok

    s = summary(run)
    fb = s["sports_detail"]["football"]["fallback"]
    assert fb["skip_rate"]["joined"] == 0.0 and fb["skip_rate"]["listed"] == pytest.approx(0.5, abs=1e-3)
    assert fb["skipped"] == {"joined": 0, "listed": len(minutes)} and fb["worst_minute"]["skipped"]["listed"] == 1
    assert s["fallback"]["skip_rate"] == fb["skip_rate"]
    gaps = s["sports_detail"]["football"]["read_gaps_while_list_leads_s"]
    assert gaps["joined"]["n"] >= 3 and gaps["joined"]["median"] == pytest.approx(1.0, abs=0.3)
    assert gaps["others"]["median"] == pytest.approx(2.0, abs=0.4) and gaps["others_matches"] == 2
    assert list(gaps["joined_per_match"]) == ["603"]
    assert s["read_gaps_while_list_leads_s"]["joined"]["n"] == gaps["joined"]["n"]
    discovery = s["sports_detail"]["football"]["discovery_while_list_leads"]
    assert discovery["list_reads"] >= 3 and discovery["list_reads_per_minute"] > 0
    assert s["sports_detail"]["football"]["fallback_time_share"] == 1.0
    assert s["requests"]["achieved_per_second"] > 0 and s["joined"]["fixed"] == 1


def test_random_live_football_matches_are_joined_and_replaced_when_they_finish(
        tmp_path: Path, data_dir: Path, fast_service: None, soaks: Any) -> None:
    matches = {eid: first_half(eid) for eid in (601, 602, 603)}
    api = FakeApi({"football": list(matches.values())}, dict(matches))
    soak, run = soaks(tmp_path, data_dir, None, api, reader=FakeReader(), fallback_window=0.5, rate=20.0,
                      joined_random=2, joined_rng=__import__("random").Random(7), poll_interval=1.0)
    soak.start()
    wait_for(lambda: len(soak.joined.ids()) == 2)
    first = set(soak.joined.ids())
    gone = min(first)
    api.live["football"] = [m for eid, m in matches.items() if eid != gone]  # biri bitti: listeden düştü
    wait_for(lambda: gone not in soak.joined.ids() and len(soak.joined.ids()) == 2, seconds=5)
    wait_for(lambda: any(r["joined_live"] == 2 and r["live"] == 2 for r in minute_rows(run)), seconds=5)
    soak.stop()
    assert soak.joined.ids() == frozenset(set(matches) - {gone})
    changes = [(r["event"], r["event_id"]) for r in rows(run, "fallback") if r["event"].startswith("joined_")]
    assert changes[:2] == [("joined_join", e) for e in sorted(first)]
    assert ("joined_leave", gone) in changes and len([c for c in changes if c[0] == "joined_join"]) == 3


def test_the_joined_set() -> None:
    import random

    js = soak_live.JoinedSet([99], 2, rng=random.Random(1))
    assert js.refresh("tennis", [1, 2, 3]) == [] and js.ids() == {99}
    joined = js.refresh("football", [1, 2, 3, 99])
    assert [c for c, _ in joined] == ["join", "join"] and 99 not in {e for _, e in joined}
    picks = set(js.ids()) - {99}
    keep = min(picks)
    changes = js.refresh("football", [keep, 5, 6])
    assert ("leave", max(picks)) in changes and len(js.ids() - {99}) == 2 and keep in js.ids()
    assert js.ever >= picks | {99}
    assert soak_live.JoinedSet([], 0).refresh("football", [1]) == []


def test_while_push_leads_there_are_no_minute_reads(
        tmp_path: Path, data_dir: Path, fast_service: None, servers: Any, soaks: Any) -> None:
    srv = servers(accept=fake_value("soak"))
    matches = {eid: first_half(eid) for eid in (601, 602)}
    api = FakeApi({"football": list(matches.values())}, dict(matches))
    soak, run = soaks(tmp_path, data_dir, srv, api, fallback_window=0.5)
    soak.start()
    wait_for(lambda: switches(soak) == ["poll>direct"])
    reads_then = len(reads_by(run, "fallback"))
    time.sleep(1.2)
    soak.stop()
    later = [r for r in minute_rows(run) if r["minute"] > soak.switches[0]["ts"] + 0.5]
    assert later == [] and len(reads_by(run, "fallback")) == reads_then


def test_the_fallback_share_and_queue_order() -> None:
    fb = soak_live.FallbackReads.__new__(soak_live.FallbackReads)
    soak_live.FallbackReads.__init__(fb, SimpleNamespace(), rate=1.0, share=0.5, window=60.0)
    assert fb.cap == 30
    fb._soak = SimpleNamespace(last_read={("football", 1): 10.0, ("football", 2): 5.0, ("football", 7): 3.0,
                                          ("football", 8): 1.0})
    order = fb.queue(["football"], {"football": [1, 2, 3, 7, 8]}, frozenset({7, 8, 3}))
    # joined önce (hiç okunmamış, sonra en eski), sonra listed (en uzun süredir okunmayan önce)
    assert order == [("joined", "football", 3), ("joined", "football", 8), ("joined", "football", 7),
                     ("listed", "football", 2), ("listed", "football", 1)]


# --- bütçe ------------------------------------------------------------------------------------------------------


def test_the_budget_stops_rest_reads(tmp_path: Path, data_dir: Path, fast_service: None, servers: Any,
                                     soaks: Any) -> None:
    srv = servers(accept=fake_value("soak"))
    live, done = live_and_done()
    api = FakeApi({"football": [live]}, {500: done})
    soak, run = soaks(tmp_path, data_dir, srv, api, budget=1)
    soak.start()
    wait_for(lambda: switches(soak) == ["poll>direct"])
    srv.msg("sport.football", finish_frame(500, live["startTimestamp"]))
    wait_for(lambda: soak.stats["football"].skipped_budget == 1)
    soak.stop()
    assert api.calls == ["/sport/football/events/live"]  # bütçe tek okumaydı: sonrası ağa çıkmadı
    [note] = [r for r in rows(run, "reads") if r["kind"] == "budget_exhausted"]  # bir kez yazılır
    assert (note["used"], note["budget"]) == (1, 1)
    s = summary(run)
    assert s["requests"]["used"] == 1 and s["requests"]["refused"] >= 2
    assert s["sports_detail"]["football"]["event_reads_skipped_budget"] == 1
    assert len(rows(run, "frames")) == 1  # push kaydı sürer


def test_the_default_budget_stays_below_one_request_per_second() -> None:
    for hours in (0.5, 1, 6, 24):
        for rate in (0.5, 1.0, 3.0):
            assert soak_live.default_budget(hours, rate) <= hours * 3600


def test_the_pacer_spaces_reads() -> None:
    now = [0.0]
    slept: List[float] = []

    def sleep(s: float) -> None:
        slept.append(s)

    pacer = soak_live.Pacer(2.0, mono=lambda: now[0], sleep=sleep)
    for _ in range(3):
        pacer.wait()
    assert slept == [0.5, 1.0]


# --- gizlilik ---------------------------------------------------------------------------------------------------


SECRET_KEYS = {"user", "pass", "auth_token", "jwt", "nkey", "sig", "token"}


def secret_keys(o: Any) -> List[str]:
    if isinstance(o, dict):
        found = [k for k in o if k in SECRET_KEYS]
        return found + [k for v in o.values() for k in secret_keys(v)]
    if isinstance(o, list):
        return [k for v in o for k in secret_keys(v)]
    return []


def assert_private(run: Path, needles: Any = ()) -> None:
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(run.iterdir()) if p.is_file()}
    assert set(texts) == {"frames.jsonl", "connection.jsonl", "leader.jsonl", "reads.jsonl", "requests.jsonl",
                          "fallback.jsonl", "resources.jsonl", "summary.json"}
    for name, text in texts.items():
        # IPv4/IPv6 yok: yerel ve belgeleme aralıkları da (izin verilen aralık olmadan)
        assert privacy.ip_literals(text, allowed=()) == [], name
        for needle in (*needles, "ws://", "wss://", "CONNECT {", "auth_required", "2.12.15", "***"):
            assert needle not in text, (name, needle)
        docs = [json.loads(text)] if name.endswith(".json") else [json.loads(x) for x in text.splitlines() if x]
        for doc in docs:
            assert secret_keys(doc) == [], (name, doc)
    # araştırma ağacının gizlilik denetimi bu koşunun klasöründe de temiz
    assert privacy.scan(str(run.parent.parent)) == {"ipv4": [], "ipv6": [], "geo": []}


def test_a_recorded_run_holds_no_credential_address_or_info_body(
        tmp_path: Path, data_dir: Path, fast_service: None, servers: Any, soaks: Any) -> None:
    srv = servers(accept=fake_value("soak"))
    live, done = live_and_done()
    api = FakeApi({"football": [live]}, {500: done})
    compared: List[FakeReader] = []

    def compare_reader() -> FakeReader:
        compared.append(FakeReader(fake_credential(srv.url, "other")))
        return compared[-1]

    soak, run = soaks(tmp_path, data_dir, srv, api, sports=("football", "tennis"), compare_sports=True,
                      compare_factory=compare_reader)
    soak.start()
    wait_for(lambda: switches(soak, "football") == ["poll>direct"] and len(soak.compares) == 1)
    frame = finish_frame(500, live["startTimestamp"])
    done["homeScore"]["current"], done["awayScore"]["current"] = frame["homeScore.current"], frame["awayScore.current"]
    srv.msg("sport.football", frame)
    srv.send(server_frame(0x2, b"-ERR 'Slow Consumer'\r\n"))
    srv.send(server_frame(0x2, b"-ERR 'not a known text from 198.51.100.7 and 2001:db8::7'\r\n"))
    wait_for(lambda: soak.stats["football"].reads_ok == 1 and sum(soak.errors.values()) == 2)
    assert soak.kill("kill-at")
    wait_for(lambda: switches(soak, "football")[-1:] == ["poll>direct"] and srv.connections == 2)
    soak.stop()

    # kimlik bilgisi tenis sayfasından da okundu; yalnızca same/different yazılır
    [cmp] = events(run, "connection", "credential_compare")
    assert (cmp["read_from"], cmp["compared_with"], cmp["connect_options"], cmp["address"]) == (
        "football", "tennis", "different", "same")
    assert compared and compared[0].reads == ["tennis"]
    assert sorted(e["text"] for e in events(run, "connection", "error")) == ["Slow Consumer", "other"]
    connect = events(run, "connection", "connect_sent")
    assert len(connect) == 2 and all(set(c) == {"ts", "mono", "event", "session"} for c in connect)
    port = srv.url.rsplit(":", 1)[1].strip("/")
    assert_private(run, needles=(fake_value("soak"), fake_value("soakp"), fake_value("other"), fake_value("otherp"),
                                 srv.url, "127.0.0.1", f":{port}", "198.51.100.7"))


# --- değişiklik izleyici -----------------------------------------------------------------------------------------


def _ev(eid: int, code: int, stype: str, home: int, away: int, ts: int) -> Dict[str, Any]:
    return {"id": eid, "status": {"code": code, "type": stype}, "homeScore": {"current": home},
            "awayScore": {"current": away}, "changes": {"changeTimestamp": ts}}


def test_the_change_tracker_finds_the_changes_that_need_an_event_read() -> None:
    t = soak_live.ChangeTracker()
    assert t.listing("football", [_ev(1, 6, "inprogress", 0, 0, 100), _ev(2, 7, "inprogress", 1, 1, 100)]) == []
    kinds = lambda changes: {eid: sorted(k) for eid, k, _ in changes}  # noqa: E731
    # skor değişimi, yeni canlı maç, listeden düşme
    changes = t.listing("football", [_ev(1, 6, "inprogress", 1, 0, 110), _ev(3, 6, "inprogress", 0, 0, 110)])
    assert kinds(changes) == {1: ["score"], 3: ["new_live"], 2: ["dropped"]}
    # CDN'in eski listesi yok sayılır
    assert t.listing("football", [_ev(1, 6, "inprogress", 0, 0, 105), _ev(3, 6, "inprogress", 0, 0, 110)]) == []
    # kare: bitiş (skorla birlikte tek kare), devre arası (durum), yalnızca kodlu kare
    k, expect, cls = t.frame("football", {"id": 1, "status.code": 100, "status.type": "finished",
                                          "homeScore.current": 2, "changes.changeTimestamp": 120})
    assert k == {"finish", "score"} and cls == "completed" and expect == {"class": "completed", "home": 2, "away": 0}
    assert t.frame("football", {"id": 3, "status.code": 31, "status.type": "inprogress"})[0] == set()
    assert t.frame("football", {"id": 3, "cardsCode": "01"})[0] == set()
    # bilinmeyen maçın başlama karesi: yeni canlı maç
    assert t.frame("football", {"id": 9, "status.code": 6, "status.type": "inprogress"})[0] == {"new_live"}
    assert t.frame("football", {"id": 9, "status.code": 70})[0] == {"status"}  # tür düşer, kod iptal (void)
    # okunan maç sayfası durumu tetikleyicisiz günceller: aynı skor karesi yeniden okuma istemez
    t.observe_event("football", _ev(3, 6, "inprogress", 1, 0, 130))
    assert t.frame("football", {"id": 3, "homeScore.current": 1})[0] == set()


def test_reflects_checks_what_the_trigger_expects() -> None:
    finished = _ev(1, 100, "finished", 2, 0, 1)
    live = _ev(1, 7, "inprogress", 2, 0, 1)
    assert soak_live.reflects({"finish"}, {"class": "completed"}, finished)
    assert not soak_live.reflects({"finish"}, {"class": "completed"}, live)
    assert soak_live.reflects({"score"}, {"class": "live", "home": 2, "away": 0}, live)
    assert not soak_live.reflects({"score"}, {"class": "live", "home": 3, "away": 0}, live)
    assert soak_live.reflects({"dropped"}, {}, finished) and not soak_live.reflects({"dropped"}, {}, live)


def test_quiet_gaps_count_only_time_with_live_matches() -> None:
    positive = soak_live.step_positive([(0.0, 0), (10.0, 3), (50.0, 0), (60.0, 2)], 0.0, 100.0)
    assert positive == [(10.0, 50.0), (60.0, 100.0)]
    gaps = soak_live.quiet_gaps([20.0, 30.0, 80.0], positive, 0.0, 100.0)
    assert gaps[:3] == [(30.0, 50.0), (60.0, 80.0), (80.0, 100.0)]
    assert soak_live.complement([(5.0, 10.0), (20.0, 30.0)], 0.0, 40.0) == [(0.0, 5.0), (10.0, 20.0), (30.0, 40.0)]
    assert soak_live.leader_intervals([(0.0, "poll"), (5.0, "direct"), (9.0, "poll")], 0.0, 10.0, "direct") == [
        (5.0, 9.0)]


def test_browser_processes_are_told_apart_by_their_profile() -> None:
    assert soak_live.classify_browser("/home/x/.cache/sofascore_research/chrome_soak-live") == "live"
    assert soak_live.classify_browser("/home/x/.cache/sofascore_research/chrome_soak") == "bridge"
    assert soak_live.classify_browser(None) == "other"
    sample = soak_live.sample_resources()
    assert sample["rss_self_mb"] > 0
    if sample.get("procfs", True):
        assert sample["rss_total_mb"] >= sample["rss_self_mb"] and set(sample["processes"]) == {
            "bridge", "live", "other"}


# --- komut satırı ------------------------------------------------------------------------------------------------


def test_a_used_run_id_is_refused_before_anything_starts(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    out = tmp_path / "soak"
    (out / "r1").mkdir(parents=True)
    (out / "r1" / "frames.jsonl").write_text("", encoding="utf-8")
    assert soak_live.main(["--out", str(out), "--run-id", "r1", "--hours", "1"]) == 2
    assert "already has output" in capsys.readouterr().err
    assert soak_live.main(["--out", str(out), "--run-id", "r 2"]) == 2
    assert soak_live.main(["--out", str(out), "--run-id", "r3", "--data-dir", str(out / "r3" / "data")]) == 2
    assert soak_live.main(["--out", str(out), "--run-id", "r4", "--source", "page",
                           "--compare-credential-sports"]) == 2
    assert soak_live.main(["--out", str(out), "--run-id", "r5", "--source", "off", "--kill-at", "5"]) == 2
    assert soak_live.main(["--out", str(out), "--run-id", "r6", "--source", "off",
                           "--compare-credential-sports"]) == 2
    assert not any((out / r).exists() for r in ("r3", "r4", "r5", "r6"))


def test_the_options_parse() -> None:
    args = soak_live._parser().parse_args(["--sports", "football, tennis", "--kill-every", "40", "--kill-at", "5",
                                           "--compare-credential-sports", "--rate", "0.8", "--list-share", "0.25",
                                           "--joined-random-soccer", "5"])
    assert args.sports == ["football", "tennis"] and args.kill_every == 40 and args.kill_at == 5
    assert args.compare_credential_sports and args.max_requests is None and args.hours == soak_live.DEFAULT_HOURS
    assert args.joined_ids == [] and args.joined_random_soccer == 5 and args.rate == 0.8 and args.list_share == 0.25
    assert args.source == "direct" and soak_live._parser().parse_args([]).joined_random_soccer == 20
    assert soak_live._parser().parse_args(["--source", "off"]).source == "off"
    schedule = soak_live._parser().parse_args(["--rate-schedule", "60:1, 120:5"]).rate_schedule
    assert schedule.phases == [(60.0, 1.0), (120.0, 5.0)]
    for bad in (["--sports", "foot ball"], ["--list-share", "1"], ["--source", "auto"],
                ["--rate-schedule", "60"], ["--rate-schedule", "60:0"], ["--rate-schedule", "x:1"]):
        with pytest.raises(SystemExit):
            soak_live._parser().parse_args(bad)


assert conftest  # sınır denetimi ve kancalar conftest'te kurulur


def test_the_joined_ids_file_uses_the_c_message_shape(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = tmp_path / "joined.json"
    path.write_text(json.dumps({"ids": [17124861, 16947598, 17124861]}), encoding="utf-8")
    assert soak_live._parser().parse_args(["--joined-ids", str(path)]).joined_ids == [17124861, 16947598]
    # bozuk dosya ya da 50.000'den çok id: c gibi yok sayılır (hata değil, uyarı)
    for bad in ("17124861 16947598", json.dumps([1, 2]), json.dumps({"ids": "1"}), json.dumps({"ids": [1, "2"]}),
                json.dumps({"ids": [True]}), json.dumps({"ids": list(range(1, 50_002))})):
        path.write_text(bad, encoding="utf-8")
        assert soak_live._parser().parse_args(["--joined-ids", str(path)]).joined_ids == []
    assert soak_live._parser().parse_args(["--joined-ids", str(tmp_path / "missing.json")]).joined_ids == []
    assert soak_live.parse_joined(json.dumps({"ids": list(range(1, 50_001))})) is not None
    assert "ignoring it" in capsys.readouterr().err


# --- hız evreleri ve dakikalık istek satırları ---------------------------------------------------------------------


def test_the_rate_schedule() -> None:
    sched = soak_live.RateSchedule.parse("60:1,120:5")
    assert sched.bounds() == [(0.0, 3600.0, 1.0), (3600.0, float("inf"), 5.0)]
    assert [sched.rate_at(t) for t in (0, 3599, 3600, 10 ** 6)] == [1.0, 1.0, 5.0, 5.0]
    assert sched.phase_at(3600) == 1 and sched.max_rate == 5.0
    assert sched.requests_in(3 * 3600) == 3600 * 1 + 7200 * 5  # son evre koşu bitene kadar sürer
    assert soak_live.default_budget(3, schedule=sched) == 39600
    assert soak_live.RateSchedule((), 0.5).rate_at(10 ** 6) == 0.5


def test_peak_and_percentiles() -> None:
    assert soak_live.peak_in_window([0.0, 0.2, 0.9, 1.0, 1.5, 3.0]) == 3
    assert soak_live.peak_in_window([]) == 0
    assert soak_live.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5
    assert soak_live.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10 and soak_live.percentile([], 0.5) is None


def test_the_challenge_counter_reads_the_client_s_own_log_lines() -> None:
    counter = soak_live.ChallengeCounter(mono=lambda: 5.0)
    counter.install()
    try:
        utils, solver = logging.getLogger("Utils"), logging.getLogger("ChallengeSolver")
        utils.warning("Rate limited or server busy (429); waiting 5 s")
        utils.warning("403 Forbidden (attempt 1/3): https://api.example.invalid/x")
        utils.info("Turnstile challenge detected; fetching through the browser bridge")
        solver.info("The API answered 403 with a challenge; solving Turnstile")
        solver.info("Solving the Cloudflare Turnstile challenge (captcha.html)")
        solver.info("Turnstile challenge solved.")
        solver.warning("Browser fetch failed (status 403): nope")
        utils.info("an unrelated line")
    finally:
        counter.uninstall()
    summary_ = soak_live.challenge_summary(counter.between(0.0, 10.0))
    assert summary_["429"] == 1 and summary_["403"] == {"curl": 1, "curl_challenge": 1, "browser_challenge": 1,
                                                       "browser": 1}
    assert summary_["turnstile"] == {"attempts": 1, "solved": 1, "failed": 0}
    assert logging.getLogger("Utils").propagate and logging.getLogger("Utils").level == logging.NOTSET


def test_requests_are_recorded_per_minute_and_per_rate_phase(
        tmp_path: Path, data_dir: Path, fast_service: None, soaks: Any) -> None:
    matches = {eid: first_half(eid) for eid in (601, 602)}
    api = FakeApi({"football": list(matches.values())}, dict(matches))
    # "dakika" 0,5 sn; ilk evre 0,9 sn 2/sn, sonra 50/sn
    sched = soak_live.RateSchedule([(0.015, 2.0), (1.0, 50.0)])
    soak, run = soaks(tmp_path, data_dir, None, api, reader=FakeReader(), rate_schedule=sched, minute_seconds=0.5,
                      fallback_window=0.5, list_share=0.5, poll_interval=1.0)
    soak.start()
    logging.getLogger("Utils").info("Turnstile challenge detected; fetching through the browser bridge")
    logging.getLogger("ChallengeSolver").info("Turnstile challenge solved.")
    wait_for(lambda: len([r for r in rows(run, "requests") if r["phase"] == 1]) >= 2, seconds=10)
    soak.stop()

    minutes = rows(run, "requests")
    assert [m["minute"] for m in minutes] == list(range(len(minutes)))
    assert minutes[0]["phase"] == 0 and minutes[0]["rate"] == 2.0 and minutes[-1]["rate"] == 50.0
    assert minutes[0]["client"]["403"]["curl_challenge"] == 1 and minutes[0]["client"]["turnstile"]["solved"] == 1
    for m in minutes:
        assert m["total"] == sum(m["by_kind"].values()) == sum(m["by_who"].values())
        assert m["peak_1s"] <= m["total"] and set(m["latency_s"]) == {"p50", "p95"}
    assert sum(m["total"] for m in minutes) == soak.client.used
    assert any(m["by_who"].get("fallback") for m in minutes)
    s = summary(run)
    phases = s["rate_phases"]
    assert [p["rate"] for p in phases] == [2.0, 50.0] and phases[0]["end_min"] == pytest.approx(0.015)
    assert sum(p["total"] for p in phases) == soak.client.used and phases[0]["client"]["turnstile"]["solved"] == 1
    assert phases[1]["achieved_per_second"] > 0 and "peak_1s" in phases[1]


# --- push kapalı (--source off): hakemin önderi hep yoklama ------------------------------------------------------


def test_with_push_off_the_list_leads_the_whole_run_and_minute_reads_happen(
        tmp_path: Path, data_dir: Path, fast_service: None, soaks: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_browser(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("push off: no credential reader and no page opener")

    monkeypatch.setattr(soak_live.ds, "BrowserCredentialReader", no_browser)
    monkeypatch.setattr(ps, "BrowserPageOpener", no_browser)
    matches = {eid: first_half(eid) for eid in (601, 602, 603)}
    api = FakeApi({"football": list(matches.values())}, dict(matches))
    soak, run = soaks(tmp_path, data_dir, None, api, source="off", fallback_window=1.0, rate=4.0, list_share=0.5,
                      joined_ids=[603], poll_interval=1.0, minute_seconds=0.5)
    assert soak.reader is None and soak.connection is None and soak.opener is None
    soak.start()
    wait_for(lambda: len([r for r in minute_rows(run) if r["live"] and not r["partial"]]) >= 2, seconds=15)
    assert soak.kill("test") is False  # kapatılacak push yok
    soak.stop()

    leader = rows(run, "leader")
    assert [(r["event"], r["leader"], r["reason"]) for r in leader] == [("start", "poll", "push_off")]
    assert soak.switches == [] and soak.service.report.leaders == {"football": "poll"}
    conn = [r["event"] for r in rows(run, "connection")]
    assert conn[0] == "run_start" and rows(run, "connection")[0]["source"] == "off"
    assert set(conn) <= {"run_start", "kill", "run_end"}  # oturum, kimlik bilgisi, sayfa yok
    minutes = [r for r in minute_rows(run) if r["live"] and not r["partial"]]
    assert all(r["reads"]["joined"] == 1 and r["reads"]["listed"] == 1 for r in minutes)
    assert len(reads_by(run, "fallback")) >= 2 * len(minutes)
    assert rows(run, "requests") and sum(m["total"] for m in rows(run, "requests")) == soak.client.used

    s = summary(run)
    assert s["source"] == "off" and s["notes"][0] == soak_live.OFF_NOTE
    football = s["sports_detail"]["football"]
    assert football["fallback_time_share"] == 1.0 and football["time_to_first_push_s"] is None
    assert football["fallback"]["skipped"]["joined"] == 0
    assert s["connection"]["sessions"] == 0 and s["connection"]["credential_reads"] == []
    assert s["leader_switches"] == [] and s["requests"]["used"] == soak.client.used > 0


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return 1_790_000_000.0 + self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)


class FakeStop:
    """threading.Event yerine: beklemek sahte saati ilerletir (en çok `until`'e kadar); `until`'de kurulur."""

    def __init__(self, clock: FakeClock, until: float = float("inf")) -> None:
        self.clock = clock
        self.until = until
        self.flag = False

    def is_set(self) -> bool:
        return self.flag or self.clock.t >= self.until

    def set(self) -> None:
        self.flag = True

    def wait(self, timeout: Any = None) -> bool:
        if not self.is_set() and timeout:
            self.clock.sleep(min(timeout, self.until - self.clock.t))
        return self.is_set()


def test_with_push_off_a_rate_phase_from_1_to_5_raises_the_requests_per_minute(
        tmp_path: Path, data_dir: Path) -> None:
    """Sahte saatle, thread'siz: liste okuması + kaydedicinin dakikası, üç dakika; 1 dk 1/sn, sonra 5/sn."""
    clock = FakeClock()
    matches = {eid: first_half(eid) for eid in range(700, 900)}  # 200 canlı maç: dakikanın payı hep dolar
    api = FakeApi({"football": list(matches.values())}, dict(matches))
    sched = soak_live.RateSchedule.parse("1:1,60:5")
    pacer: List[Any] = []
    run = tmp_path / "research" / "soak" / "t-off"
    soak = soak_live.Soak(str(run), ["football"], run_id="t-off", data_dir=str(data_dir), budget=1000,
                          source="off", base_fetch=api, gate=lambda: pacer[0].wait(), rate_schedule=sched,
                          list_share=0.5, joined_random=0, wall=clock.wall, mono=clock.mono)
    pacer.append(soak_live.Pacer(soak.current_rate, mono=clock.mono, sleep=clock.sleep))
    stop = FakeStop(clock)
    soak._stop = stop
    soak.begin()
    for _ in range(3):
        soak._service_fetch("/sport/football/events/live")  # servisin liste okuması gibi
        soak.fallback._minute(["football"], stop)
    # dakikalık istek satırları: koşunun kendi dakika döngüsü, sahte saat koşunun başından sonuna yeniden yürür
    end, clock.t = clock.t, soak.started_mono
    soak._stop = FakeStop(clock, until=end)
    soak._minute_loop()
    assert clock.t == end
    soak.stop()

    fallback = [(r["cap"], r["reads"]["listed"], r["skipped"]["listed"], r["cut"]) for r in minute_rows(run)]
    assert fallback == [(30, 30, 170, "share"), (150, 150, 50, "share"), (150, 150, 50, "share")]
    minutes = [m for m in rows(run, "requests") if not m["partial"]]
    assert [(m["phase"], m["rate"], m["total"]) for m in minutes] == [(0, 1.0, 31), (1, 5.0, 151), (1, 5.0, 151)]
    assert all(m["by_kind"] == {"event": m["total"] - 1, "list": 1} for m in minutes)
    assert minutes[0]["peak_1s"] == 1 and minutes[1]["peak_1s"] == 5
    s = summary(run)
    assert [(p["rate"], p["total"]) for p in s["rate_phases"]] == [(1.0, 31), (5.0, 302)]
    assert s["sports_detail"]["football"]["fallback_time_share"] == 1.0 and s["requests"]["used"] == 333


assert conftest  # sınır denetimi ve kancalar conftest'te kurulur
