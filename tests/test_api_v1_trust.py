"""
API v1: güvenilen ek sertifikalar (`/api/v1/trust`; sofascore_scraper/web/api/v1/trust.py, #225). Tümü çevrimdışı:
ağın yoklaması ve bağlantı denetiminin isteği sahtedir. Sertifikalar sahtedir ve test oturumunda üretilir
(tests/trust_pki.py); depoda ne sertifika ne anahtar durur.

  * liste, yoklama, güvenme (parmak izi onayı, 409 `certificate_changed`), çıkarma;
  * güvenlik: belirteç ayarlıyken belirteçsiz çağrı 401, başka kaynaktan (origin) yazma 403; sunucu güvenilecek
    sertifikayı istemciden almaz.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

import trust_pki
from sofascore_scraper import redact
from sofascore_scraper.client import bridge, trust
from sofascore_scraper.exceptions import NetworkError
from sofascore_scraper.web import deps, security
from sofascore_scraper.web.app import app

client = TestClient(app)
ACCESS = "-".join(("fake", "access", "value", "for", "tests", "only"))


def cert(name: str) -> trust.Certificate:
    (found,) = trust.pem_certificates(trust_pki.pki().pem(name))
    return found


def root() -> trust.Certificate:
    """Sahte ağın sunduğu kök sertifika (oturumda üretilir: modül yüklenirken değil, ilk kullanımda)."""
    return cert("root")


PROBE = {"target": "sofascore"}


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Kendi yapılandırma dizini; belirteç yok; tarayıcı yeniden başlatması sahte."""
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    monkeypatch.setattr(trust, "browser_supported", lambda: True)
    deps.attempt_limiter.reset()
    yield
    deps.attempt_limiter.reset()


@pytest.fixture
def restarts(monkeypatch: pytest.MonkeyPatch) -> List[bool]:
    calls: List[bool] = []
    monkeypatch.setattr(bridge, "restart_for_trust_change", lambda: calls.append(True) or True)
    return calls


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """
    Sahte ağ: `presented` sertifikasını sunar; depoda o varsa doğrulanır. Bağlantı denetiminin isteği de aynı
    kurala uyar (depoda yoksa curl'ün 60 hatası).
    """
    state: Dict[str, Any] = {"presented": root(), "reason": None, "probes": 0, "requests": 0}

    def trusted() -> bool:
        return any(item.certificate == state["presented"] for item in trust.stored())

    def probe(timeout: float = 10.0, environ: Any = None) -> trust.ProbeResult:
        state["probes"] += 1
        result = trust.ProbeResult(host="www.sofascore.com", port=443, chain_length=2, leaf=cert("leaf"))
        if state["reason"]:
            result.reason = state["reason"]
        elif trusted():
            result.verified, result.reason = True, "verified"
        else:
            result.presented, result.can_trust = state["presented"], True
            result.error = "self-signed certificate in certificate chain"
        return result

    def request(url: str, **kwargs: Any) -> Any:
        state["requests"] += 1
        if not trusted():
            raise NetworkError("Request failed: x: Failed to perform, curl: (60) SSL certificate OpenSSL verify "
                               "result: self signed certificate in certificate chain (19).")
        return {"events": [{"id": 1}]}

    monkeypatch.setattr(trust, "probe", probe)
    monkeypatch.setattr("sofascore_scraper.client.transport.make_api_request", request)
    return state


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv(security.TOKEN_ENV, ACCESS)
    redact.refresh()
    yield ACCESS
    monkeypatch.undo()
    redact.refresh()


# --- liste ve yoklama --------------------------------------------------------------------------------


def test_the_list_is_empty_until_something_is_trusted_or_dropped_in(tmp_path: Path) -> None:
    body = client.get("/api/v1/trust").json()
    assert body == {"data": [], "page": {"limit": 0, "next_cursor": None}}
    directory = tmp_path / "config" / "ca"
    directory.mkdir(parents=True)
    (directory / "company.pem").write_text(root().pem + cert("expired-root").pem, encoding="utf-8")
    items = data(client.get("/api/v1/trust"))
    assert [(i["file"], i["sha256"], i["expired"], i["is_ca"], i["self_signed"]) for i in items] == [
        ("company.pem", root().sha256, False, True, True),
        ("company.pem", cert("expired-root").sha256, True, True, True),
    ]
    assert items[0]["subject"] == {"common_name": "Fake Inspection Root CA (test fixture)",
                                   "organization": "Fake Inspection Örnek A.Ş."}
    assert items[0]["not_after"].endswith("Z") and items[0]["issuer"] == items[0]["subject"]


def test_the_probe_shows_who_signed_what_the_network_presents(network: Dict[str, Any]) -> None:
    probe = data(client.post("/api/v1/trust/probe", json=PROBE))
    assert (probe["verified"], probe["can_trust"], probe["reason"], probe["chain_length"]) == (False, True, None, 2)
    assert probe["presented"]["sha256"] == root().sha256
    assert probe["presented"]["subject"]["common_name"] == "Fake Inspection Root CA (test fixture)"
    assert probe["leaf"]["subject"]["common_name"] == "localhost" and probe["browser_supported"] is True
    assert (probe["host"], probe["port"]) == ("www.sofascore.com", 443)
    error(client.post("/api/v1/trust/probe"), 422, "invalid_request")  # bağlantı denetimi gibi gövdesini ister
    error(client.post("/api/v1/trust/probe", json={"target": "sofascore", "host": "evil.example"}), 422,
          "invalid_request")  # hedef seçilemez


@pytest.mark.parametrize("reason", ["proxy", "unreachable", "root_not_sent", "chain_unavailable", "expired",
                                    "not_enough", "not_https"])
def test_a_probe_that_cannot_offer_anything_is_a_result_with_a_reason(network: Dict[str, Any], reason: str) -> None:
    network["reason"] = reason
    probe = data(client.post("/api/v1/trust/probe", json=PROBE))
    assert (probe["can_trust"], probe["reason"], probe["presented"]) == (False, reason, None)


# --- güvenme -----------------------------------------------------------------------------------------


def test_trusting_stores_the_presented_certificate_and_checks_the_connection(
    network: Dict[str, Any], restarts: List[bool], tmp_path: Path,
) -> None:
    assert data(client.post("/api/v1/status/check", json={"target": "sofascore"}))["cause"] == "tls"
    fingerprint = data(client.post("/api/v1/trust/probe", json=PROBE))["presented"]["sha256"]
    outcome = data(client.post("/api/v1/trust", json={"sha256": fingerprint}))
    assert outcome["stored"]["sha256"] == root().sha256 and outcome["stored"]["file"].startswith("trusted-")
    assert (outcome["check"]["ok"], outcome["check"]["reason"], outcome["check"]["events_count"]) == (True, None, 1)
    assert (outcome["browser_supported"], outcome["browser_restart_needed"]) == (True, False)
    assert restarts == [True]  # bu sürecin tarayıcısı yeni depoyla yeniden başlar
    assert (tmp_path / "config" / "ca" / outcome["stored"]["file"]).is_file()
    assert [i["sha256"] for i in data(client.get("/api/v1/trust"))] == [root().sha256]
    assert data(client.post("/api/v1/trust/probe", json=PROBE))["reason"] == "verified"


def test_the_fingerprint_may_be_written_with_colons(network: Dict[str, Any], restarts: List[bool]) -> None:
    spaced = ":".join(root().sha256[i:i + 2] for i in range(0, 64, 2)).upper()
    assert data(client.post("/api/v1/trust", json={"sha256": spaced}))["stored"]["sha256"] == root().sha256


def test_a_certificate_that_changed_since_the_user_saw_it_is_refused_with_409(
    network: Dict[str, Any], restarts: List[bool],
) -> None:
    shown = data(client.post("/api/v1/trust/probe", json=PROBE))["presented"]["sha256"]
    network["presented"] = cert("other-root")  # onay ile istek arasında ağ başka bir sertifika sunmaya başladı
    body = error(client.post("/api/v1/trust", json={"sha256": shown}), 409, "certificate_changed")
    assert body["details"] == {"reason": "changed", "presented_sha256": cert("other-root").sha256}
    assert data(client.get("/api/v1/trust")) == [] and restarts == [] and network["requests"] == 0


def test_nothing_is_stored_when_nothing_can_be_trusted(network: Dict[str, Any], restarts: List[bool]) -> None:
    network["reason"] = "root_not_sent"
    body = error(client.post("/api/v1/trust", json={"sha256": root().sha256}), 409, "certificate_changed")
    assert body["details"] == {"reason": "root_not_sent", "presented_sha256": None}
    assert data(client.get("/api/v1/trust")) == []


def test_the_server_never_takes_the_certificate_from_the_client(network: Dict[str, Any]) -> None:
    error(client.post("/api/v1/trust"), 422, "invalid_request")
    error(client.post("/api/v1/trust", json={}), 422, "invalid_request")
    error(client.post("/api/v1/trust", json={"sha256": "abc"}), 422, "invalid_request")
    error(client.post("/api/v1/trust", json={"sha256": root().sha256, "pem": cert("other-root").pem}), 422,
          "invalid_request")
    error(client.post("/api/v1/trust", json={"sha256": ":" * 64}), 400, "invalid_request")  # desene uyar, iz değil
    assert data(client.get("/api/v1/trust")) == [] and network["probes"] == 0


def test_a_restart_is_asked_for_when_the_browser_could_not_be_restarted(
    network: Dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "restart_for_trust_change", lambda: False)
    outcome = data(client.post("/api/v1/trust", json={"sha256": root().sha256}))
    assert outcome["browser_restart_needed"] is True and outcome["check"]["ok"] is True


# --- çıkarma -----------------------------------------------------------------------------------------


def test_removing_a_trusted_certificate(network: Dict[str, Any], restarts: List[bool]) -> None:
    client.post("/api/v1/trust", json={"sha256": root().sha256})
    removed = data(client.delete(f"/api/v1/trust/{root().sha256.upper()}"))
    assert removed == {"sha256": root().sha256, "browser_restart_needed": False} and restarts == [True, True]
    assert data(client.get("/api/v1/trust")) == []
    assert data(client.post("/api/v1/status/check", json={"target": "sofascore"}))["cause"] == "tls"
    error(client.delete(f"/api/v1/trust/{root().sha256}"), 404, "not_found")
    error(client.delete("/api/v1/trust/not-a-fingerprint"), 422, "invalid_request")


# --- güvenlik ----------------------------------------------------------------------------------------

# `{root}`: testte kök sertifikanın parmak izi konur (`_filled`); sertifika oturumda üretildiği için parmak izi
# testler toplanırken bilinmez
WRITES = [
    ("post", "/api/v1/trust/probe", {"target": "sofascore"}),
    ("post", "/api/v1/trust", {"sha256": "{root}"}),
    ("delete", "/api/v1/trust/{root}", None),
]


def _filled(path: str, body: Optional[Dict[str, str]]) -> Tuple[str, Optional[Dict[str, str]]]:
    fingerprint = root().sha256
    filled = {key: value.replace("{root}", fingerprint) for key, value in body.items()} if body else body
    return path.replace("{root}", fingerprint), filled


@pytest.mark.parametrize("method,path,body", [("get", "/api/v1/trust", None), *WRITES])
def test_every_trust_route_needs_the_token_when_one_is_set(
    token: str, network: Dict[str, Any], restarts: List[bool], method: str, path: str, body: Any,
) -> None:
    path, body = _filled(path, body)
    anonymous = TestClient(app)
    r = anonymous.request(method, path, json=body) if body is not None else anonymous.request(method, path)
    error(r, 401, "unauthorized")
    wrong = anonymous.request(method, path, headers={"authorization": "Bearer nope"})
    error(wrong, 401, "unauthorized")
    assert trust.stored() == [] and network["probes"] == 0 and restarts == []


def test_with_the_token_the_routes_answer(token: str, network: Dict[str, Any], restarts: List[bool]) -> None:
    bearer = {"authorization": f"Bearer {token}"}
    assert data(TestClient(app).post("/api/v1/trust/probe", json=PROBE, headers=bearer))["can_trust"] is True
    assert data(TestClient(app).post("/api/v1/trust", json={"sha256": root().sha256}, headers=bearer))["check"]["ok"]


@pytest.mark.parametrize("method,path,body", WRITES)
@pytest.mark.parametrize("headers", [
    {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"},
    {"origin": "https://evil.example"},
])
def test_another_site_cannot_make_the_app_trust_or_forget_a_certificate(
    network: Dict[str, Any], restarts: List[bool], method: str, path: str, body: Any, headers: Dict[str, str],
) -> None:
    path, body = _filled(path, body)
    trust.add(root())
    network["presented"] = cert("other-root")
    r = client.request(method, path, json=body, headers=headers) if body is not None \
        else client.request(method, path, headers=headers)
    error(r, 403, "forbidden_origin")
    assert [item.certificate for item in trust.stored()] == [root()]  # ne eklendi ne çıkarıldı
    assert network["probes"] == 0 and restarts == []


def test_a_cross_origin_write_is_refused_with_a_valid_session_too(token: str, network: Dict[str, Any]) -> None:
    browser = TestClient(app)
    assert browser.post("/api/v1/auth/login", json={"token": token}).status_code == 200
    r = browser.post("/api/v1/trust", json={"sha256": root().sha256}, headers={"sec-fetch-site": "cross-site"})
    error(r, 403, "forbidden_origin")
    assert trust.stored() == []
