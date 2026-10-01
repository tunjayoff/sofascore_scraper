"""Tarayıcı köprüsünün sağlık sinyali (src/bridge_health.py): durum makinesi, uç noktalar, CLI mesajı."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import src.challenge_solver as cs
from src import bridge_health
from src.bridge_health import BLOCKED, DEGRADED, OK, BridgeHealth
from src.i18n import I18nManager

TH = {"degraded_after": 3, "blocked_after": 5, "blocked_min_seconds": 200.0}


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _health(**th):
    clock = Clock()
    return BridgeHealth(clock=clock, thresholds_fn=lambda: {**TH, **th}), clock


def _fail(h, n, kind=bridge_health.KIND_CHALLENGE, detail="HTTP 403"):
    for _ in range(n):
        h.record_failure(kind, detail)


# --- durum makinesi -----------------------------------------------------------------------------

def test_starts_ok_with_nothing_recorded():
    h, _ = _health()
    snap = h.snapshot()
    assert snap["state"] == OK and snap["consecutive_failures"] == 0
    assert snap["last_success_at"] is None and snap["last_error"] is None and snap["changed_at"] is None
    json.dumps(snap)  # uç noktalar bunu olduğu gibi döndürür


def test_failures_below_threshold_keep_it_ok():
    h, _ = _health()
    _fail(h, 2)
    assert h.state == OK and h.consecutive_failures == 2


def test_degraded_after_consecutive_failures():
    h, clock = _health()
    _fail(h, 3)
    snap = h.snapshot()
    assert snap["state"] == DEGRADED
    assert snap["consecutive_failures"] == 3
    assert snap["last_error"]["kind"] == "challenge" and snap["last_error"]["detail"] == "HTTP 403"
    assert snap["failing_since"] == snap["changed_at"] == "2026-09-21T14:13:20+00:00"


def test_success_resets_the_streak_and_recovers():
    h, clock = _health()
    _fail(h, 4)
    clock.t += 30
    h.record_success()
    snap = h.snapshot()
    assert snap["state"] == OK and snap["consecutive_failures"] == 0 and snap["failing_since"] is None
    assert snap["last_success_at"] == "2026-09-21T14:13:50+00:00"
    assert snap["last_error"]["kind"] == "challenge"  # son hata tarihçe olarak kalır
    _fail(h, 2)
    assert h.state == OK  # seri baştan sayılır


def test_blocked_needs_both_count_and_duration():
    h, clock = _health()
    _fail(h, 10)  # toplu indirme: tek başarısız çözüm 10 isteği birden düşürür
    assert h.state == DEGRADED  # sayı yeter, ama seri daha yeni başladı
    clock.t += 199
    _fail(h, 1)
    assert h.state == DEGRADED
    clock.t += 1
    _fail(h, 1)
    assert h.state == BLOCKED


def test_long_streak_with_few_failures_is_not_blocked():
    h, clock = _health()
    _fail(h, 3)
    clock.t += 3600
    _fail(h, 1)
    assert h.state == DEGRADED and h.consecutive_failures == 4  # < blocked_after


def test_state_does_not_change_without_a_request():
    """Zaman tek başına durumu değiştirmez: yeni kanıt yoksa "blocked" denmez."""
    h, clock = _health()
    _fail(h, 10)
    clock.t += 3600
    assert h.snapshot()["state"] == DEGRADED


def test_blocked_stays_blocked_until_a_success():
    h, clock = _health()
    _fail(h, 5)
    clock.t += 300
    _fail(h, 1)
    assert h.state == BLOCKED
    _fail(h, 3)
    assert h.state == BLOCKED
    h.record_success()
    assert h.state == OK


def test_blocked_threshold_never_below_degraded(monkeypatch):
    monkeypatch.setenv("BRIDGE_DEGRADED_AFTER", "8")
    monkeypatch.setenv("BRIDGE_BLOCKED_AFTER", "2")
    assert bridge_health.thresholds()["blocked_after"] == 8


@pytest.mark.parametrize("env,expected", [
    ({}, {"degraded_after": 3, "blocked_after": 10, "blocked_min_seconds": 200.0}),
    ({"BRIDGE_DEGRADED_AFTER": "5", "BRIDGE_BLOCKED_AFTER": "20", "BRIDGE_BLOCKED_MIN_SECONDS": "0"},
     {"degraded_after": 5, "blocked_after": 20, "blocked_min_seconds": 0.0}),
    ({"BRIDGE_DEGRADED_AFTER": "abc", "BRIDGE_BLOCKED_AFTER": "0", "BRIDGE_BLOCKED_MIN_SECONDS": "-1"},
     {"degraded_after": 3, "blocked_after": 10, "blocked_min_seconds": 200.0}),
])
def test_thresholds_come_from_env(monkeypatch, env, expected):
    for k in ("BRIDGE_DEGRADED_AFTER", "BRIDGE_BLOCKED_AFTER", "BRIDGE_BLOCKED_MIN_SECONDS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert bridge_health.thresholds() == expected


# --- log ve dinleyiciler: durum değişiminde bir kez --------------------------------------------

def test_logs_one_warning_per_state_change_not_per_request(caplog):
    h, clock = _health()
    with caplog.at_level(logging.INFO, logger="src.bridge_health"):
        _fail(h, 9)  # ok → degraded (3. istekte)
        clock.t += 300
        _fail(h, 9)  # degraded → blocked (ilk istekte)
        h.record_success()
        h.record_success()
    records = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert [lvl for lvl, _ in records] == ["WARNING", "WARNING", "INFO"]
    assert "ok → degraded" in records[0][1] and "art arda 3 istek" in records[0][1]
    assert "degraded → blocked" in records[1][1] and "SofaScore bizi engelliyor" in records[1][1]
    assert "blocked → ok" in records[2][1]


def test_browser_failures_are_not_blamed_on_sofascore(caplog):
    h, _ = _health(blocked_min_seconds=0)
    with caplog.at_level(logging.WARNING, logger="src.bridge_health"):
        _fail(h, 5, kind=bridge_health.KIND_BROWSER, detail="RuntimeError: no chromium")
    assert h.state == BLOCKED
    assert "Tarayıcı köprüsü çalışmıyor" in caplog.text and "SofaScore bizi engelliyor" not in caplog.text


def test_listeners_get_each_change_once_and_cannot_break_recording():
    h, _ = _health(blocked_min_seconds=0)
    seen = []

    def boom(snap, previous):
        raise RuntimeError("listener bug")

    h.add_listener(boom)
    h.add_listener(lambda snap, previous: seen.append((previous, snap["state"], snap["consecutive_failures"])))
    _fail(h, 7)
    h.record_success()
    assert seen == [("ok", "degraded", 3), ("degraded", "blocked", 5), ("blocked", "ok", 0)]


# --- köprüdeki kayıt noktaları ------------------------------------------------------------------

def _bridge(*results):
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token = "tok"
    bridge.ensure_ready = AsyncMock()
    bridge.evaluate = AsyncMock(side_effect=list(results))
    bridge.solve_challenge = AsyncMock(return_value=None)
    return bridge


OK_RES = {"status": 200, "ok": True, "data": {"a": 1}, "text": None}
CHALLENGE = {"status": 403, "ok": False, "data": None, "text": '{"error":{"code":403,"reason":"challenge"}}'}
FORBIDDEN = {"status": 403, "ok": False, "data": None, "text": "<html>Access denied</html>"}


def test_successful_fetch_records_success():
    asyncio.run(_bridge(OK_RES).fetch_json("/event/1"))
    snap = bridge_health.snapshot()
    assert snap["state"] == OK and snap["last_success_at"] is not None


def test_404_counts_as_an_answer():
    bridge_health.record_failure("challenge")
    assert asyncio.run(_bridge({"status": 404, "ok": False, "data": None, "text": ""}).fetch_json("/x")) == {"__404__": True}
    assert bridge_health.snapshot()["consecutive_failures"] == 0


def test_unsolved_challenge_records_a_challenge_failure():
    assert asyncio.run(_bridge(CHALLENGE).fetch_json("/event/1")) is None
    snap = bridge_health.snapshot()
    assert snap["consecutive_failures"] == 1 and snap["last_error"]["kind"] == "challenge"
    assert snap["last_error"]["detail"].startswith("HTTP 403")


def test_403_solved_and_retried_is_a_success():
    bridge = _bridge(CHALLENGE, OK_RES)
    bridge.solve_challenge = AsyncMock(return_value="new")
    assert asyncio.run(bridge.fetch_json("/event/1")) == {"a": 1}
    snap = bridge_health.snapshot()
    assert snap["consecutive_failures"] == 0 and snap["last_error"] is None


def test_403_still_rejected_after_solving_is_a_failure():
    bridge = _bridge(CHALLENGE, CHALLENGE)
    bridge.solve_challenge = AsyncMock(return_value="new")
    assert asyncio.run(bridge.fetch_json("/event/1")) is None
    assert bridge_health.snapshot()["consecutive_failures"] == 1


def test_403_without_challenge_is_a_hard_block():
    asyncio.run(_bridge(FORBIDDEN).fetch_json("/event/1"))
    assert bridge_health.snapshot()["last_error"]["kind"] == "forbidden"


@pytest.mark.parametrize("res", [
    {"status": 0, "ok": False, "data": None, "text": "TypeError: Failed to fetch"},
    {"status": 500, "ok": False, "data": None, "text": "oops"},
    {"status": 429, "ok": False, "data": None, "text": "slow down"},
])
def test_network_and_server_errors_neither_extend_nor_reset_the_streak(res):
    bridge_health.record_failure("challenge")
    asyncio.run(_bridge(res).fetch_json("/event/1"))
    snap = bridge_health.snapshot()
    assert snap["consecutive_failures"] == 1 and snap["last_error"]["kind"] == "challenge"


def test_repeated_blocked_requests_reach_degraded_through_the_bridge():
    bridge = _bridge(*[CHALLENGE] * 3)
    for _ in range(3):
        asyncio.run(bridge.fetch_json("/event/1"))
    assert bridge_health.snapshot()["state"] == DEGRADED


def test_failed_browser_launch_is_recorded(tmp_path):
    bridge = cs.BrowserBridge(profile_dir=str(tmp_path))
    bridge._launch = AsyncMock(side_effect=RuntimeError("chromium missing"))

    async def run():
        for _ in range(2):  # ikinci çağrı: yeniden deneme beklenirken de istek başarısızdır
            with pytest.raises(RuntimeError):
                await bridge.ensure_ready()

    asyncio.run(run())
    snap = bridge_health.snapshot()
    assert snap["consecutive_failures"] == 2 and snap["last_error"]["kind"] == "browser"
    assert bridge._launch.await_count == 1


def test_cancelled_launch_is_not_a_health_signal(tmp_path):
    bridge = cs.BrowserBridge(profile_dir=str(tmp_path))
    bridge._launch = AsyncMock(side_effect=asyncio.CancelledError())

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await bridge.ensure_ready()

    asyncio.run(run())
    assert bridge_health.snapshot()["consecutive_failures"] == 0


# --- uç noktalar --------------------------------------------------------------------------------

@pytest.fixture
def client():
    from src.web.app import app

    return TestClient(app)


HEALTH_KEYS = {
    "state", "consecutive_failures", "last_success_at", "last_failure_at", "failing_since", "changed_at",
    "last_error", "thresholds",
}


def test_health_endpoint_keeps_its_fields_and_adds_bridge(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["version"] == "2.0.0" and body["ui"] in ("vue-spa", "missing-dist")
    assert set(body["bridge"]) == HEALTH_KEYS
    assert body["bridge"]["state"] == "ok"
    assert body["throttle"] == {"enabled": False, "requests_per_second": 0.0, "shared": False, "error": None}


def test_health_endpoint_reports_a_blocked_bridge_without_failing_liveness(client, monkeypatch):
    monkeypatch.setenv("BRIDGE_BLOCKED_MIN_SECONDS", "0")
    for _ in range(10):
        bridge_health.record_failure("forbidden", "HTTP 403: Access denied")
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"  # sunucu ayakta: başlatıcı buna bakar
    bridge = r.json()["bridge"]
    assert bridge["state"] == "blocked" and bridge["consecutive_failures"] == 10
    assert bridge["last_error"] == {"kind": "forbidden", "detail": "HTTP 403: Access denied", "at": bridge["last_failure_at"]}
    assert bridge["thresholds"] == {"degraded_after": 3, "blocked_after": 10, "blocked_min_seconds": 0.0}


def test_bypass_status_keeps_its_fields_and_adds_health(client):
    for _ in range(3):
        bridge_health.record_failure("challenge", "HTTP 403")
    body = client.get("/api/bypass/status").json()
    assert body["status"] == "ready"
    assert body["has_token"] is False and body["is_valid"] is False
    assert "BrowserBridge" in body["mechanism"]
    assert set(body["health"]) == HEALTH_KEYS and body["health"]["state"] == "degraded"


def test_throttle_status_in_health(client, monkeypatch, tmp_path):
    from src import throttle

    monkeypatch.setenv("REQUEST_RATE_LIMIT", "5")
    monkeypatch.setenv("SOFASCORE_THROTTLE_DIR", str(tmp_path))
    throttle.reset_for_tests()
    try:
        assert client.get("/health").json()["throttle"] == {
            "enabled": True, "requests_per_second": 5.0, "shared": True, "error": None,
        }
    finally:
        throttle.reset_for_tests()


# --- CLI: iki dilde tek satır -------------------------------------------------------------------

def _snap(state, kind="challenge", count=4, last_success="2026-10-01T12:00:00+00:00"):
    return {
        "state": state,
        "consecutive_failures": count,
        "last_success_at": last_success,
        "last_error": {"kind": kind, "detail": "HTTP 403", "at": "2026-10-01T12:05:00+00:00"},
    }


@pytest.mark.parametrize("lang", ["tr", "en"])
def test_cli_messages_exist_in_both_languages(lang):
    i18n = I18nManager()
    i18n.set_language(lang)
    seen = set()
    for state in (DEGRADED, BLOCKED, OK):
        for kind in ("challenge", "forbidden", "browser"):
            msg = bridge_health.cli_message(_snap(state, kind), i18n.t)
            assert "\n" not in msg and "bridge_" not in msg and "{" not in msg  # tek satır, eksik anahtar yok
            seen.add(msg)
    assert len(seen) == 7  # 3 neden × 2 durum + düzelme
    never = bridge_health.cli_message(_snap(BLOCKED, last_success=None), i18n.t)
    assert i18n.t("bridge_health_never") in never


def test_cli_message_content():
    en = I18nManager()
    en.set_language("en")
    msg = bridge_health.cli_message(_snap(BLOCKED, "forbidden", count=12), en.t)
    assert "12 requests in a row" in msg and "403" in msg and "2026-" in msg  # yerel saat: gün dilime göre değişir
    tr = I18nManager()
    tr.set_language("tr")
    assert "art arda 4 istek" in bridge_health.cli_message(_snap(DEGRADED), tr.t)


def test_cli_locale_keys_match_between_languages():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    keys = {}
    for lang in ("tr", "en"):
        with open(os.path.join(root, "locales", f"{lang}.json"), encoding="utf-8") as f:
            keys[lang] = {k for k in json.load(f) if k.startswith("bridge_")}
    assert keys["tr"] == keys["en"] and len(keys["tr"]) == 7


def test_cli_prints_one_line_to_stderr_per_state_change(capsys, monkeypatch):
    monkeypatch.setenv("BRIDGE_BLOCKED_MIN_SECONDS", "0")
    bridge_health.add_listener(bridge_health.print_cli_line)  # main.py terminal modlarında ekler
    for _ in range(12):
        bridge_health.record_failure("challenge", "HTTP 403")
    bridge_health.record_success()
    captured = capsys.readouterr()
    assert captured.out == ""  # --watch'ta stdout olay akışıdır
    lines = [ln for ln in captured.err.splitlines() if ln.strip()]
    assert len(lines) == 3  # degraded, blocked, düzeldi — 13 istek için 13 satır değil
    assert lines[0].startswith("⚠") and lines[1].startswith("✖") and lines[2].startswith("✓")
