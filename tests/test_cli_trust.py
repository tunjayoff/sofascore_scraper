"""
`ssc trust list|show|add|remove` (sofascore_scraper/cli/commands/trust.py; #225). Çevrimdışı: ağın yoklaması
sahtedir. Komut soru sormaz: `add`, `--sha256` ya da `--yes` olmadan `confirmation_required` ile çıkar.
Sertifikalar sahtedir ve test oturumunda üretilir (tests/trust_pki.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

import trust_pki
from sofascore_scraper.client import trust
from test_cli_skeleton import CliRunner, cli  # noqa: F401  (fixture)

def cert(name: str) -> trust.Certificate:
    (found,) = trust.pem_certificates(trust_pki.pki().pem(name))
    return found


def root() -> trust.Certificate:
    """Sahte ağın sunduğu kök sertifika (oturumda üretilir: modül yüklenirken değil, ilk kullanımda)."""
    return cert("root")


def colons() -> str:
    """Kökün parmak izi, komutun yazdırdığı gibi: AB:CD:…"""
    return ":".join(root().sha256[i:i + 2] for i in range(0, 64, 2)).upper()


@pytest.fixture
def network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Sahte ağ: `presented`i sunar; depodaysa doğrulanır. Yapılandırma dizini teste özeldir."""
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(trust, "browser_supported", lambda: True)
    state: Dict[str, Any] = {"presented": root(), "reason": None, "probes": 0}

    def probe(timeout: float = 10.0, environ: Any = None) -> trust.ProbeResult:
        state["probes"] += 1
        result = trust.ProbeResult(host="www.sofascore.com", port=443, chain_length=2, leaf=cert("leaf"))
        if state["reason"]:
            result.reason = state["reason"]
        elif any(item.certificate == state["presented"] for item in trust.stored()):
            result.verified, result.reason = True, "verified"
        else:
            result.presented, result.can_trust = state["presented"], True
        return result

    monkeypatch.setattr(trust, "probe", probe)
    return state


def test_list_is_empty_and_names_the_folder(cli: CliRunner, network: Dict[str, Any], tmp_path: Path) -> None:  # noqa: F811
    run = cli("trust", "list")
    assert run.exit_code == 0 and run.stdout.strip() == f"No trusted certificates in {tmp_path / 'config' / 'ca'}"
    assert cli("trust", "list", "--json").data == {"directory": str(tmp_path / "config" / "ca"), "certificates": []}
    assert network["probes"] == 0  # liste ağa bakmaz


def test_show_prints_what_the_network_presents_and_how_to_trust_it(cli: CliRunner, network: Dict[str, Any]) -> None:  # noqa: F811
    run = cli("trust", "show")
    assert run.exit_code == 0
    assert "Server: www.sofascore.com:443" in run.stdout
    assert "Subject:     Fake Inspection Root CA (test fixture) (Fake Inspection Örnek A.Ş.)" in run.stdout
    assert f"SHA-256:     {colons()}" in run.stdout and "Valid until: 2126-" in run.stdout
    assert "can read and change what the app downloads" in run.stdout
    assert f"ssc trust add --sha256 {root().sha256}" in run.stdout
    data = cli("trust", "show", "--json").data
    assert (data["can_trust"], data["presented"]["sha256"], data["verified"]) == (True, root().sha256, False)
    assert trust.stored() == []  # göstermek güvenmek değildir


@pytest.mark.parametrize("reason,text", [
    ("verified", "there is nothing to trust"),
    ("proxy", "A proxy is configured"),
    ("root_not_sent", "does not send its CA certificate"),
    ("chain_unavailable", "3.13 or newer reads the chain"),
    ("unreachable", "could not be reached"),
    ("expired", "has expired"),
    ("not_enough", "would still not verify"),
    ("not_https", "not an https address"),
])
def test_show_explains_why_nothing_can_be_trusted(cli: CliRunner, network: Dict[str, Any], reason: str, text: str) -> None:  # noqa: F811
    network["reason"] = reason
    run = cli("trust", "show")
    assert run.exit_code == 0 and text in run.stdout and "ssc trust add" not in run.stdout
    if reason == "root_not_sent":  # kimin CA'sının dışa aktarılacağını söyler
        assert "signed by: Fake Inspection Root CA (test fixture)" in run.stdout


def test_add_never_asks_and_needs_the_fingerprint_or_yes(cli: CliRunner, network: Dict[str, Any]) -> None:  # noqa: F811
    run = cli("trust", "add", "--json")
    assert run.exit_code == 2 and run.error["code"] == "confirmation_required"
    assert root().sha256 in run.error["message"] and "--sha256" in run.error["message"] and "--yes" in run.error["message"]
    assert run.error["details"]["probe"]["presented"]["sha256"] == root().sha256
    assert trust.stored() == []
    assert cli("trust", "add", "--sha256", "abc", "--json").error["code"] == "invalid_request"


def test_add_with_the_fingerprint_stores_the_presented_certificate(cli: CliRunner, network: Dict[str, Any], tmp_path: Path) -> None:  # noqa: F811
    run = cli("trust", "add", "--sha256", colons())
    assert run.exit_code == 0, run.stderr
    assert f"Trusted: trusted-{root().sha256[:16]}.pem in {tmp_path / 'config' / 'ca'}" in run.stdout
    assert "The connection to the server now verifies." in run.stdout
    assert "reads it when that process restarts" in run.stderr  # not: çalışan tarayıcılar
    assert [item.certificate for item in trust.stored()] == [root()]
    listed = cli("trust", "list")
    assert f"trusted-{root().sha256[:16]}.pem" in listed.stdout and colons() in listed.stdout
    assert cli("trust", "list", "--json").data["certificates"][0]["sha256"] == root().sha256


def test_add_refuses_a_certificate_other_than_the_one_given(cli: CliRunner, network: Dict[str, Any]) -> None:  # noqa: F811
    network["presented"] = cert("other-root")
    run = cli("trust", "add", "--sha256", root().sha256, "--json")
    assert run.exit_code == 1 and run.error["code"] == "certificate_changed"
    assert cert("other-root").sha256 in run.error["message"] and run.error["details"]["reason"] == "changed"
    assert trust.stored() == []


def test_add_yes_trusts_what_is_presented_and_says_what_it_was(cli: CliRunner, network: Dict[str, Any]) -> None:  # noqa: F811
    data = cli("trust", "add", "--yes", "--json").data
    assert data["stored"]["sha256"] == root().sha256 and data["verified"] is True and data["browser_supported"] is True
    assert data["stored"]["subject"]["common_name"] == "Fake Inspection Root CA (test fixture)"


def test_add_fails_when_nothing_can_be_trusted(cli: CliRunner, network: Dict[str, Any]) -> None:  # noqa: F811
    network["reason"] = "root_not_sent"
    for flags in (("--yes",), ("--sha256", root().sha256)):
        run = cli("trust", "add", *flags, "--json")
        assert run.exit_code == 1 and run.error["code"] == "certificate_changed"
        assert run.error["details"]["reason"] == "root_not_sent" and "does not send its CA certificate" in run.error["message"]
    hint = cli("trust", "add", "--json")
    assert hint.error["code"] == "confirmation_required" and "nothing can be trusted now (root_not_sent)" in hint.error["message"]
    assert trust.stored() == []


def test_remove_by_fingerprint(cli: CliRunner, network: Dict[str, Any]) -> None:  # noqa: F811
    trust.add(root())
    run = cli("trust", "remove", colons())
    assert run.exit_code == 0 and f"No longer trusted: {colons()}" in run.stdout and trust.stored() == []
    again = cli("trust", "remove", root().sha256, "--json")
    assert again.exit_code == 1 and again.error["code"] == "not_found"
    assert cli("trust", "remove", "nope", "--json").error["code"] == "invalid_request"


def test_the_texts_are_translated(cli: CliRunner, network: Dict[str, Any]) -> None:  # noqa: F811
    run = cli("--lang", "tr", "trust", "show")
    assert "Ağ, uygulamanın güvenmediği bir sertifika sunuyor:" in run.stdout and "İmzalayan:" in run.stdout
    assert "güvenilen sertifika yok" in cli("--lang", "tr", "trust", "list").stdout
