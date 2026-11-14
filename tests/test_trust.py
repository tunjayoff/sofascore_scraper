"""
Güvenilen ek sertifikalar (sofascore_scraper/client/trust.py; #225): DER çözümü, depo, birleşik CA dosyası ve
önceliği, ağın yoklanması, istek katmanının `verify` argümanı, tarayıcı ortamı.

Ağ yok: yoklama testleri 127.0.0.1'de açılan küçük bir TLS sunucusuna bağlanır. Sertifikalar ve sunucunun
anahtarları SAHTEDİR ve depoda durmaz: test oturumunda bir kez, yalnızca standart kütüphaneyle üretilir
(tests/trust_pki.py; `fake_pki` fixture'ı). Parmak izleri bu yüzden her oturumda başkadır.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import ssl
import stat
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

import trust_pki
from sofascore_scraper.client import bridge, transport, trust

HAS_CHAIN = hasattr(ssl.SSLSocket, "get_unverified_chain")  # Python 3.13+


def cert(name: str) -> trust.Certificate:
    """Oturumda üretilen PKI'nin `name` sertifikası, ürünün çözücüsüyle okunmuş."""
    (found,) = trust.pem_certificates(trust_pki.pki().pem(name))
    return found


def root_sha256() -> str:
    """Kök sertifikanın parmak izi, çözücüden bağımsız: üretilen DER'in SHA-256'sı."""
    return hashlib.sha256(trust_pki.pki().der("root")).hexdigest()


@pytest.fixture
def env(tmp_path: Path) -> Dict[str, str]:
    """Yalıtılmış yapılandırma dizini; CA ortam değişkenleri yok."""
    return {"SOFASCORE_CONFIG_DIR": str(tmp_path / "config")}


# --- DER -----------------------------------------------------------------------------------------


def test_a_root_certificate_is_decoded_with_names_dates_and_fingerprint() -> None:
    root = cert("root")
    assert root.subject == {"common_name": "Fake Inspection Root CA (test fixture)",
                            "organization": "Fake Inspection Örnek A.Ş."}  # UTF-8 ad
    assert root.issuer == root.subject and root.self_signed and root.is_ca
    assert root.sha256 == root_sha256() and root.der == trust_pki.pki().der("root")
    assert root.not_after.year == 2126 and root.not_after.tzinfo is timezone.utc  # GeneralizedTime
    assert root.not_before <= datetime.now(timezone.utc) and not root.expired()
    assert root.pem.startswith("-----BEGIN CERTIFICATE-----") and trust.pem_certificates(root.pem) == [root]


def test_intermediate_leaf_and_expired_certificates() -> None:
    issuing, leaf, expired = cert("issuing"), cert("leaf-two-tier"), cert("expired-root")
    assert issuing.is_ca and not issuing.self_signed
    assert issuing.issuer["common_name"] == "Fake Two-Tier Root CA (test fixture)"
    assert not leaf.is_ca and not leaf.self_signed and leaf.subject["common_name"] == "localhost"
    assert leaf.issuer == issuing.subject
    assert expired.expired() and expired.not_after == datetime(2020, 2, 1, tzinfo=timezone.utc)  # UTCTime
    assert expired.to_dict()["expired"] is True and expired.to_dict()["not_after"] == "2020-02-01T00:00:00Z"


@pytest.mark.parametrize("name", sorted(trust_pki.SPECS))
def test_every_generated_certificate_is_decoded_as_it_was_built(name: str) -> None:
    """Çözücünün okuduğu her alan, sertifikanın üretildiği değere eşittir (tests/trust_pki.py `SPECS`)."""
    spec = trust_pki.SPECS[name]
    der = trust_pki.pki().der(name)
    found = cert(name)
    assert found.der == der and found.sha256 == hashlib.sha256(der).hexdigest()
    assert (found.subject, found.issuer) == (spec.subject, trust_pki.SPECS[spec.issuer].subject)
    assert (found.is_ca, found.self_signed) == (spec.ca, spec.self_signed)
    assert (found.not_before, found.not_after) == (spec.not_before, spec.not_after)
    assert found.expired() is (spec.not_after < datetime.now(timezone.utc))


@pytest.mark.parametrize("name", sorted(trust_pki.SPECS))
def test_the_decoder_agrees_with_openssl(name: str) -> None:
    """
    Bağımsız kaynak: aynı sertifikayı OpenSSL'in kendi çözücüsü de okur (Python'un `ssl` modülünün iç
    yardımcısı; bu Python'da yoksa ya da çalışmıyorsa test atlanır). Üretici ile çözücü aynı yanlışı
    paylaşsa bu test yakalar.
    """
    decode = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decode is None:
        pytest.skip("ssl._ssl._test_decode_cert yok")
    try:
        info = decode(str(trust_pki.pki().cert_path(name)))
    except Exception as e:  # iç yardımcı: platforma göre davranışı güvence altında değil
        pytest.skip(f"ssl._ssl._test_decode_cert çalışmadı: {e}")

    def names(rdns: Any) -> Dict[str, Optional[str]]:
        flat = {key: value for rdn in rdns for key, value in rdn}
        return {"common_name": flat.get("commonName"), "organization": flat.get("organizationName")}

    found = cert(name)
    assert (found.subject, found.issuer) == (names(info["subject"]), names(info["issuer"]))
    assert found.self_signed is (info["subject"] == info["issuer"])
    assert found.not_before.timestamp() == ssl.cert_time_to_seconds(info["notBefore"])
    assert found.not_after.timestamp() == ssl.cert_time_to_seconds(info["notAfter"])
    assert info["version"] == 3 and ("subjectAltName" in info) is (not found.is_ca)


@pytest.mark.parametrize("junk", [b"", b"\x30", b"\x30\x82\xff\xff", b"hello world", b"\x30\x03\x02\x01\x01"])
def test_what_is_not_a_certificate_is_refused(junk: bytes) -> None:
    with pytest.raises(ValueError, match="not an X.509 certificate"):
        trust.parse_certificate(junk)
    # Kesilmiş bir sertifika çökme değil, ValueError'dır
    der = cert("root").der
    for cut in (10, 100, len(der) - 1):
        with pytest.raises(ValueError):
            trust.parse_certificate(der[:cut])


def test_pem_text_with_several_blocks_comments_and_a_broken_block() -> None:
    text = "# a comment\n" + cert("root").pem + "junk\n-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n" \
        + cert("issuing").pem
    assert [c.sha256 for c in trust.pem_certificates(text)] == [cert("root").sha256, cert("issuing").sha256]
    assert trust.pem_certificates("no certificate here") == []


def test_fingerprints_are_normalized_and_checked() -> None:
    spaced = ":".join(root_sha256()[i:i + 2] for i in range(0, 64, 2)).upper()
    assert trust.normalize_fingerprint(spaced) == root_sha256() == trust.normalize_fingerprint(f" {root_sha256()} ")
    for bad in ("", "abc", root_sha256()[:-1], root_sha256() + "0", "g" * 64):
        with pytest.raises(ValueError):
            trust.normalize_fingerprint(bad)


# --- depo ----------------------------------------------------------------------------------------


def test_the_store_is_the_ca_folder_of_the_config_directory(env: Dict[str, str], tmp_path: Path) -> None:
    assert trust.ca_dir(env) == tmp_path / "config" / "ca"
    assert trust.ca_dir({}) == Path("config").absolute() / "ca"
    assert trust.stored(env) == [] and trust.bundle_path(env) is None  # klasör yok: hiçbir şey değişmez


def test_add_writes_a_private_file_and_is_idempotent(env: Dict[str, str]) -> None:
    item = trust.add(cert("root"), env)
    path = trust.ca_dir(env) / item.file
    assert item.file == f"trusted-{root_sha256()[:16]}.pem" and path.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(trust.ca_dir(env).stat().st_mode) == 0o700
    assert "Remove the file to stop trusting it" in path.read_text(encoding="utf-8")
    assert trust.add(cert("root"), env) == item and len(list(trust.ca_dir(env).glob("*.pem"))) == 1
    assert [s.to_dict()["sha256"] for s in trust.stored(env)] == [root_sha256()]
    assert trust.stored(env)[0].to_dict()["file"] == item.file


def test_a_dropped_in_pem_file_is_trusted_and_other_files_are_ignored(env: Dict[str, str]) -> None:
    directory = trust.ca_dir(env)
    directory.mkdir(parents=True)
    (directory / "company.pem").write_text(cert("root").pem + cert("issuing").pem, encoding="utf-8")
    (directory / "again.PEM").write_text(cert("root").pem, encoding="utf-8")  # aynı sertifika: bir kez sayılır
    (directory / "notes.txt").write_text(cert("other-root").pem, encoding="utf-8")
    (directory / "broken.pem").write_text("not a certificate", encoding="utf-8")
    found = trust.stored(env)
    assert sorted(s.certificate.sha256 for s in found) == sorted([cert("root").sha256, cert("issuing").sha256])


def test_remove_deletes_the_file_or_only_the_block(env: Dict[str, str]) -> None:
    trust.add(cert("other-root"), env)
    directory = trust.ca_dir(env)
    (directory / "company.pem").write_text(cert("root").pem + cert("issuing").pem, encoding="utf-8")
    assert trust.remove(cert("other-root").sha256, env) is True
    assert trust.remove(cert("other-root").sha256, env) is False  # artık yok
    assert trust.remove(cert("root").sha256.upper(), env) is True  # paketten yalnızca o blok çıkar
    assert [s.certificate.sha256 for s in trust.stored(env)] == [cert("issuing").sha256]
    assert trust.remove(cert("issuing").sha256, env) is True and list(directory.glob("*.pem")) == []
    with pytest.raises(ValueError):
        trust.remove("not a fingerprint", env)


# --- birleşik paket ve öncelik --------------------------------------------------------------------


def test_the_bundle_is_the_base_bundle_plus_the_stored_certificates(env: Dict[str, str], tmp_path: Path) -> None:
    base = tmp_path / "base.pem"
    base.write_text(cert("other-root").pem, encoding="utf-8")
    env["CURL_CA_BUNDLE"] = str(base)
    trust.add(cert("root"), env)
    path = trust.bundle_path(env)
    assert path == str(trust.ca_dir(env) / ".bundle" / "ca-bundle.pem")
    got = [c.sha256 for c in trust.pem_certificates(Path(path).read_text(encoding="utf-8"))]
    assert got == [cert("other-root").sha256, cert("root").sha256]  # açıkça verilen paket korunur, depo eklenir
    if os.name == "posix":
        assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600
    # Paket depo taramasına girmez
    assert [s.certificate.sha256 for s in trust.stored(env)] == [cert("root").sha256]


def test_the_bundle_follows_the_store_without_a_restart(env: Dict[str, str], tmp_path: Path) -> None:
    base = tmp_path / "base.pem"
    base.write_text("", encoding="utf-8")
    env["REQUESTS_CA_BUNDLE"] = str(base)

    def fingerprints() -> List[str]:
        path = trust.bundle_path(env)
        return [c.sha256 for c in trust.pem_certificates(Path(path).read_text(encoding="utf-8"))] if path else []

    assert fingerprints() == []
    trust.add(cert("root"), env)
    assert fingerprints() == [cert("root").sha256]
    first = os.stat(trust.bundle_path(env)).st_mtime_ns
    assert os.stat(trust.bundle_path(env)).st_mtime_ns == first  # değişiklik yok: yeniden yazılmaz
    trust.add(cert("issuing"), env)
    assert sorted(fingerprints()) == sorted([cert("root").sha256, cert("issuing").sha256])
    trust.remove(cert("root").sha256, env)
    assert fingerprints() == [cert("issuing").sha256]
    trust.remove(cert("issuing").sha256, env)
    assert trust.bundle_path(env) is None  # depo boş: curl_cffi'nin varsayılanına dönülür


def test_base_bundle_precedence_matches_curl_cffi(env: Dict[str, str], tmp_path: Path) -> None:
    from curl_cffi import curl as cffi_curl

    assert trust.base_bundle(env) == cffi_curl.DEFAULT_CACERT  # ortamda bir şey yok: curl_cffi'nin varsayılanı
    assert os.path.exists(trust.base_bundle(env) or "")
    assert trust.base_bundle({**env, "CURL_CA_BUNDLE": "/a.pem"}) == "/a.pem"
    assert trust.base_bundle({**env, "CURL_CA_BUNDLE": "/a.pem", "REQUESTS_CA_BUNDLE": "/b.pem"}) == "/b.pem"


def test_an_unreadable_base_bundle_leaves_curl_alone(env: Dict[str, str]) -> None:
    trust.add(cert("root"), env)
    env["CURL_CA_BUNDLE"] = "/does/not/exist.pem"
    assert trust.bundle_path(env) is None  # curl kendi hatasını (77) verir; yanlış bir paket yazılmaz


def test_a_read_only_store_puts_the_bundle_into_the_temp_folder(env: Dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                                                tmp_path: Path) -> None:
    trust.add(cert("root"), env)
    monkeypatch.setattr(trust.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    real = trust._write_private

    def refuse_store(path: Path, text: str) -> None:
        if str(path).startswith(str(trust.ca_dir(env))):
            raise PermissionError("read-only")
        real(path, text)

    monkeypatch.setattr(trust, "_write_private", refuse_store)
    path = trust.bundle_path(env)
    assert path is not None and path.startswith(str(tmp_path / "tmp")) and cert("root").pem in Path(path).read_text()


# --- istek katmanı: verify ---------------------------------------------------------------------------


class Resp:
    status_code = 200
    headers: Dict[str, str] = {}
    text = "{}"

    def json(self) -> Dict[str, Any]:
        return {"ok": 1}


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "_throttle", lambda: None)
    monkeypatch.setattr(transport, "_sleep", lambda seconds: None)


def test_requests_pass_no_verify_while_the_store_is_empty(quiet: None, monkeypatch: pytest.MonkeyPatch,
                                                          tmp_path: Path) -> None:
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "config"))
    with patch.object(transport.cffi_requests, "get", return_value=Resp()) as get:
        assert transport.make_api_request("/x") == {"ok": 1}
    assert "verify" not in get.call_args.kwargs


def test_sync_async_and_warm_up_requests_pass_the_bundle_as_verify(quiet: None, monkeypatch: pytest.MonkeyPatch,
                                                                    tmp_path: Path) -> None:
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "config"))
    trust.add(cert("root"))
    bundle = trust.bundle_path()
    assert bundle and cert("root").pem in Path(bundle).read_text(encoding="utf-8")

    with patch.object(transport.cffi_requests, "get", return_value=Resp()) as get:
        assert transport.make_api_request("/x") == {"ok": 1}
    assert get.call_args.kwargs["verify"] == bundle

    seen: List[Dict[str, Any]] = []

    async def session_get(url: str, **kwargs: Any) -> Resp:
        seen.append(kwargs)
        return Resp()

    session = MagicMock()
    session.ssc_profile = session.ssc_profile_override = None
    session.get = session_get
    monkeypatch.setattr(transport.throttle, "wait_async", lambda: asyncio.sleep(0, result=0.0))
    assert asyncio.run(transport.make_api_request_async(session, "/a")) == {"ok": 1}
    asyncio.run(transport._warmup_session(session))
    assert [kwargs.get("verify") for kwargs in seen] == [bundle, bundle]

    # Depodan çıkınca bir sonraki istek yine curl_cffi'nin varsayılanıyla gider: yeniden başlatma yok
    trust.remove(cert("root").sha256)
    with patch.object(transport.cffi_requests, "get", return_value=Resp()) as get:
        transport.make_api_request("/x")
    assert "verify" not in get.call_args.kwargs


# --- yoklama: 127.0.0.1'de TLS sunucusu ------------------------------------------------------------


class TlsServer:
    """Verilen zinciri sunan, el sıkışmadan sonra bağlantıyı kapatan sunucu."""

    def __init__(self, chain: List[str], key: str, tmp_path: Path, pki: trust_pki.FakePki) -> None:
        chain_file = tmp_path / "chain.pem"
        chain_file.write_text("".join(pki.pem(name) for name in chain), encoding="utf-8")
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(chain_file), str(pki.key_path(key)))  # anahtar oturumun geçici dizininde
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.handshakes = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            self.handshakes += 1
            try:
                with self.context.wrap_socket(raw, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                raw.close()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: Dict[str, str], fake_pki: trust_pki.FakePki,
) -> Iterator[Any]:
    servers: List[TlsServer] = []
    # Temel paket: yalnızca ilgisiz bir CA (sistemin deposu testin sonucunu değiştirmesin)
    base = tmp_path / "base.pem"
    base.write_text(cert("other-root").pem, encoding="utf-8")
    env["CURL_CA_BUNDLE"] = str(base)
    monkeypatch.setattr(trust, "proxy_configured", lambda: False)

    def start(chain: List[str], key: str) -> TlsServer:
        server = TlsServer(chain, key, tmp_path, fake_pki)
        servers.append(server)
        monkeypatch.setattr(trust, "target", lambda: ("https", "127.0.0.1", server.port))
        return server

    yield start
    for server in servers:
        server.close()


def test_probe_offers_the_root_the_network_sends(serve: Any, env: Dict[str, str]) -> None:
    server = serve(["leaf", "root"], "leaf")
    result = trust.probe(environ=env)
    assert result.verified is False and result.leaf is not None and result.leaf.subject["common_name"] == "localhost"
    if not HAS_CHAIN:
        assert (result.can_trust, result.reason, result.chain_length) == (False, "chain_unavailable", 1)
        return
    assert (result.can_trust, result.reason, result.chain_length) == (True, None, 2)
    assert result.presented == cert("root") and "self-signed" in (result.error or "").replace("self signed", "self-signed")
    assert server.handshakes == 3  # zincir, şu anki güven, adayla doğrulama; HTTP isteği yok
    data = result.to_dict()
    assert data["presented"]["sha256"] == root_sha256() and data["host"] == "127.0.0.1" and data["port"] == server.port


@pytest.mark.skipif(not HAS_CHAIN, reason="zincir Python 3.13+ ile okunur")
def test_probe_offers_an_issuing_ca_when_the_root_is_not_sent(serve: Any, env: Dict[str, str]) -> None:
    serve(["leaf-two-tier", "issuing"], "leaf-two-tier")
    result = trust.probe(environ=env)
    assert result.can_trust and result.presented == cert("issuing") and not result.presented.self_signed
    # Ona güvenince doğrulanır (curl de ara CA'yı güven çıpası sayar)
    trust.add(result.presented, env)
    again = trust.probe(environ=env)
    assert (again.verified, again.can_trust, again.reason, again.presented) == (True, False, "verified", None)


def test_probe_says_root_not_sent_when_only_the_leaf_arrives(serve: Any, env: Dict[str, str]) -> None:
    serve(["leaf"], "leaf")
    result = trust.probe(environ=env)
    assert (result.verified, result.can_trust, result.presented) == (False, False, None)
    assert result.reason == ("root_not_sent" if HAS_CHAIN else "chain_unavailable")
    assert result.leaf is not None and result.leaf.issuer["common_name"] == "Fake Inspection Root CA (test fixture)"
    # Kök elle klasöre konunca doğrulanır
    directory = trust.ca_dir(env)
    directory.mkdir(parents=True)
    (directory / "exported.pem").write_text(cert("root").pem, encoding="utf-8")
    assert trust.probe(environ=env).reason == "verified"


def test_probe_reports_verified_proxy_not_https_and_unreachable(serve: Any, env: Dict[str, str],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(["leaf-other", "other-root"], "leaf-other")  # temel pakette güvenilen CA
    ok = trust.probe(environ=env)
    assert (ok.verified, ok.can_trust, ok.reason, ok.presented) == (True, False, "verified", None)

    before = server.handshakes
    monkeypatch.setattr(trust, "proxy_configured", lambda: True)
    assert trust.probe(environ=env).reason == "proxy" and server.handshakes == before  # proxy atlanmaz: bağlantı yok
    monkeypatch.setattr(trust, "proxy_configured", lambda: False)

    monkeypatch.setattr(trust, "target", lambda: ("http", "127.0.0.1", server.port))
    assert trust.probe(environ=env).reason == "not_https"

    server.close()
    monkeypatch.setattr(trust, "target", lambda: ("https", "127.0.0.1", server.port))
    gone = trust.probe(timeout=2, environ=env)
    assert (gone.reason, gone.can_trust, gone.chain_length) == ("unreachable", False, 0) and gone.error


@pytest.mark.skipif(not HAS_CHAIN, reason="zincir Python 3.13+ ile okunur")
def test_probe_does_not_offer_a_ca_that_would_not_help(serve: Any, env: Dict[str, str]) -> None:
    # Başka bir CA'nın imzaladığı yaprak + zincire eklenmiş ilgisiz kök: ona güvenmek bağlantıyı doğrulamaz
    serve(["leaf-two-tier", "root"], "leaf-two-tier")
    result = trust.probe(environ=env)
    assert (result.can_trust, result.reason, result.presented) == (False, "not_enough", None)


def test_the_probe_target_is_the_configured_api_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper.config import loader

    assert trust.target() == ("https", "www.sofascore.com", 443)
    settings = MagicMock()
    settings.client.effective_base_url = "https://mirror.example:8443/api/v1"
    settings.client.use_proxy, settings.client.proxy = True, " http://proxy:3128 "
    monkeypatch.setattr(loader, "active_settings", lambda: settings)
    assert trust.target() == ("https", "mirror.example", 8443) and trust.proxy_configured() is True
    settings.client.use_proxy = False
    assert trust.proxy_configured() is False


@pytest.mark.skipif(not HAS_CHAIN, reason="zincir Python 3.13+ ile okunur")
def test_trust_presented_stores_only_the_certificate_that_was_confirmed(serve: Any, env: Dict[str, str]) -> None:
    serve(["leaf", "root"], "leaf")
    with pytest.raises(trust.TrustRefused) as refused:
        trust.trust_presented(cert("issuing").sha256, env)  # kullanıcının gördüğü başka bir sertifikaydı
    assert refused.value.reason == "changed" and refused.value.probe.presented == cert("root")
    assert trust.stored(env) == []
    stored, probe = trust.trust_presented(root_sha256().upper(), env)
    assert stored.certificate == cert("root") and probe.can_trust and trust.stored(env) == [stored]
    with pytest.raises(trust.TrustRefused) as nothing:  # artık doğrulanıyor: güvenilecek bir şey yok
        trust.trust_presented(root_sha256(), env)
    assert nothing.value.reason == "verified"
    with pytest.raises(ValueError):
        trust.trust_presented("zz", env)


def test_client_context_trusts_the_store_for_python_sockets(serve: Any, env: Dict[str, str]) -> None:
    server = serve(["leaf", "root"], "leaf")
    assert trust.client_context(env) is None  # depo boş: çağıran olağan bağlamını kullanır
    trust.add(cert("root"), env)
    context = trust.client_context(env)
    assert context is not None
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            assert tls.version()


# --- tarayıcı --------------------------------------------------------------------------------------


def test_the_browser_starts_strict_and_without_an_environment_while_the_store_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "config"))
    assert bridge._context_args(str(tmp_path / "profile")) == {"ignore_https_errors": False}


def test_the_browser_gets_a_private_home_with_the_stored_certificates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Dict[str, str],
) -> None:
    trust.add(cert("root"), env)
    trust.add(cert("issuing"), env)
    calls: List[List[str]] = []

    def run(argv: List[str], **kwargs: Any) -> Any:
        calls.append(argv)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(trust, "browser_supported", lambda: True)
    monkeypatch.setattr(trust.subprocess, "run", run)
    profile = tmp_path / "profile"
    environment = trust.browser_environment(str(profile), env)
    home = profile / "trust-home"
    assert environment is not None and environment["HOME"] == str(home) and environment["PATH"] == os.environ["PATH"]
    (argv,) = calls
    assert argv[0] == sys.executable and argv[1].endswith("nssdb.py") and argv[2] == str(home / ".pki" / "nssdb")
    written = trust.pem_certificates(Path(argv[3]).read_text(encoding="utf-8"))
    assert sorted(c.sha256 for c in written) == sorted([cert("root").sha256, cert("issuing").sha256])


def test_the_browser_keeps_its_usual_environment_when_the_database_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Dict[str, str],
) -> None:
    assert trust.browser_environment(str(tmp_path / "profile"), env) is None  # depo boş
    trust.add(cert("root"), env)
    monkeypatch.setattr(trust, "browser_supported", lambda: False)
    assert trust.browser_environment(str(tmp_path / "profile"), env) is None  # platform desteklemiyor
    monkeypatch.setattr(trust, "browser_supported", lambda: True)
    monkeypatch.setattr(trust.subprocess, "run", lambda *a, **k: MagicMock(returncode=1, stdout="", stderr="boom"))
    assert trust.browser_environment(str(tmp_path / "profile"), env) is None  # tarayıcı sıkı kalır, her şeyi kabul etmez


def test_restart_for_trust_change_closes_a_running_browser_and_forgets_a_failed_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge.BrowserBridge, "_instance", None)
    assert bridge.restart_for_trust_change() is True  # tarayıcı hiç açılmamış: yapılacak bir şey yok

    closed: List[bool] = []

    class Fake:
        session: Optional[object] = object()
        _launch_failed_at = 123.0

        def __init__(self) -> None:
            self._init_lock = asyncio.Lock()

        async def close(self) -> None:
            closed.append(True)
            self.session = None

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        fake = Fake()
        monkeypatch.setattr(bridge.BrowserBridge, "_instance", fake)
        monkeypatch.setattr(bridge, "_loop", loop)
        assert bridge.restart_for_trust_change() is True
        assert closed == [True] and fake._launch_failed_at == 0.0
        assert bridge.restart_for_trust_change() is True and closed == [True]  # zaten kapalı
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def _nss_available() -> Tuple[bool, str]:
    return trust.browser_supported(), "libnss3 yok ya da Linux değil"


@pytest.mark.skipif(not _nss_available()[0], reason=_nss_available()[1])
def test_the_nss_database_is_written_by_the_helper_script(tmp_path: Path, env: Dict[str, str]) -> None:
    """Gerçek libnss3 ile (varsa): veritabanı dosyaları oluşur. Tarayıcının onu okuduğu Docker'da sınandı."""
    trust.add(cert("root"), env)
    environment = trust.browser_environment(str(tmp_path / "profile"), env)
    assert environment is not None
    database = Path(environment["HOME"]) / ".pki" / "nssdb"
    assert (database / "cert9.db").is_file() and (database / "cert9.db").stat().st_size > 0
