"""
Güvenilen ek sertifikalar: HTTPS'i kendi CA'sıyla yeniden imzalayan ağlar (HTTPS tarayan antivirüs, kurumsal TLS
denetimi) için (#225).

Depo, yapılandırma dizinindeki `ca/` klasörüdür (`<CONFIG_DIR>/ca/*.pem`; imajda /app/config volume'u). Oraya
konan her PEM sertifikası güvenilir sayılır: arayüzdeki "Trust this certificate" düğmesi ve `ssc trust add` ağın
o an sunduğu sertifikayı oraya yazar, kullanıcı da dosyayı elle koyabilir.

  HTTP istemcisi  `bundle_path()`: curl_cffi'nin bu ortamda zaten kullanacağı paket (ortam değişkeni, sistem deposu
                  ya da certifi) + depodaki sertifikalar, tek dosyada. İstek katmanı her istekte bu yolu `verify`
                  olarak verir: depo değişince yeniden başlatma gerekmez. Depo boşsa None (hiçbir şey değişmez).
  Tarayıcı        `browser_environment()`: Chromium (Linux) kullanıcının NSS veritabanındaki güven kayıtlarını
                  okur. Depodaki sertifikalar köprüye özel bir HOME altındaki NSS veritabanına yazılır
                  (sofascore_scraper/client/nssdb.py, ayrı süreçte) ve tarayıcı o HOME ile başlatılır. Doğrulama
                  gerçektir: zincir depodaki CA'ya kadar imzalanmış olmalıdır. (`--ignore-certificate-errors-spki-list`
                  bilerek kullanılmadı: zincire güvenilen CA'nın sertifikasını ekleyen herkesi kabul ediyor.)
  Yoklama         `probe()`: ağın API sunucusu için sunduğu zinciri DOĞRULAMADAN okur, şu anki güvenle doğrulanıp
                  doğrulanmadığını ve güvenilebilecek sertifikayı (sunulan en üstteki CA) bildirir. Yapılandırılmış
                  bir proxy varsa yoklama yapılmaz (doğrudan soket proxy'yi atlardı).

Sertifikalar standart kütüphaneyle çözülür (küçük bir DER yürüyücüsü); `cryptography` bağımlılığı yoktur.
"""
from __future__ import annotations

import base64
import contextlib
import ctypes.util
import hashlib
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from sofascore_scraper.private_files import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, make_private_dir, restrict_permissions

logger = logging.getLogger("Trust")

CA_DIR_NAME = "ca"
_BUNDLE_DIR = ".bundle"          # `ca/` altında; `*.pem` taramasına girmez
_BUNDLE_NAME = "ca-bundle.pem"
_BROWSER_HOME = "trust-home"     # tarayıcı profilinin (user data dir) altında köprüye özel HOME
PROBE_TIMEOUT = 10.0

# Yoklamanın "güvenilemez" nedenleri (API ve CLI aynı sözcükleri kullanır)
REASON_VERIFIED = "verified"                # zincir zaten doğrulanıyor: güvenilecek bir şey yok
REASON_PROXY = "proxy"                      # proxy yapılandırılmış: doğrudan yoklama onu atlardı
REASON_NOT_HTTPS = "not_https"              # API kökü https değil
REASON_UNREACHABLE = "unreachable"          # bağlantı kurulamadı
REASON_ROOT_NOT_SENT = "root_not_sent"      # ağ yalnızca yaprağı gönderiyor: CA sertifikası elle konmalı
REASON_CHAIN_UNAVAILABLE = "chain_unavailable"  # bu Python (< 3.13) yalnızca yaprağı okuyabiliyor
REASON_EXPIRED = "expired"                  # sunulan CA'nın süresi dolmuş
REASON_NOT_ENOUGH = "not_enough"            # o sertifikaya güvenmek de doğrulamayı geçirmiyor (ör. ad uyuşmuyor)
REASON_CHANGED = "changed"                  # onaylanan parmak izi artık sunulan sertifikanınki değil


# --- DER / X.509 ---------------------------------------------------------------------------------

_OID_CN = bytes((0x55, 0x04, 0x03))
_OID_O = bytes((0x55, 0x04, 0x0A))
_OID_BASIC_CONSTRAINTS = bytes((0x55, 0x1D, 0x13))
_STRING_ENCODINGS = {0x0C: "utf-8", 0x13: "ascii", 0x16: "ascii", 0x14: "latin-1", 0x1E: "utf-16-be", 0x1C: "utf-32-be"}
_PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)


def _tlv(data: bytes, pos: int, end: int) -> Tuple[int, int, int]:
    """`pos`taki DER öğesi: (etiket, gövdenin başı, gövdenin sonu). Sınır dışına taşan uzunluk ValueError."""
    if pos + 2 > end:
        raise ValueError("truncated DER")
    tag, first = data[pos], data[pos + 1]
    body = pos + 2
    if first & 0x80:
        count = first & 0x7F
        if count == 0 or count > 4 or body + count > end:
            raise ValueError("unsupported DER length")
        length = int.from_bytes(data[body:body + count], "big")
        body += count
    else:
        length = first
    if body + length > end:
        raise ValueError("DER length past the end")
    return tag, body, body + length


def _children(data: bytes, start: int, end: int) -> List[Tuple[int, int, int, int]]:
    """Bir gövdedeki öğeler: (etiket, öğenin başı, gövdenin başı, gövdenin sonu)."""
    items = []
    pos = start
    while pos < end:
        tag, body, stop = _tlv(data, pos, end)
        items.append((tag, pos, body, stop))
        pos = stop
    return items


def _name(data: bytes, start: int, end: int) -> Dict[str, Optional[str]]:
    """Name (RDNSequence) içinden ortak ad (CN) ve kuruluş (O)."""
    found: Dict[str, Optional[str]] = {"common_name": None, "organization": None}
    for _tag, _pos, rdn_start, rdn_end in _children(data, start, end):
        for _t, _p, attr_start, attr_end in _children(data, rdn_start, rdn_end):
            parts = _children(data, attr_start, attr_end)
            if len(parts) < 2 or parts[0][0] != 0x06:
                continue
            oid = data[parts[0][2]:parts[0][3]]
            key = "common_name" if oid == _OID_CN else "organization" if oid == _OID_O else None
            if key is None:
                continue
            tag, _p2, value_start, value_end = parts[1]
            text = data[value_start:value_end].decode(_STRING_ENCODINGS.get(tag, "utf-8"), "replace")
            found[key] = text  # birden çok varsa en sondaki (en özgül) kalır
    return found


def _time(data: bytes, tag: int, start: int, end: int) -> datetime:
    text = data[start:end].decode("ascii")
    if tag == 0x17:  # UTCTime: YYMMDDHHMMSSZ
        year = int(text[:2])
        text = ("19" if year >= 50 else "20") + text
    elif tag != 0x18:
        raise ValueError("unexpected time type")
    return datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _is_ca_extension(data: bytes, start: int, end: int) -> bool:
    """Uzantılar (SEQUENCE OF Extension) içinde basicConstraints cA=TRUE var mı?"""
    for _tag, _pos, ext_start, ext_end in _children(data, start, end):
        parts = _children(data, ext_start, ext_end)
        if not parts or parts[0][0] != 0x06 or data[parts[0][2]:parts[0][3]] != _OID_BASIC_CONSTRAINTS:
            continue
        octets = parts[-1]
        _t, seq_start, seq_end = _tlv(data, octets[2], octets[3])
        inner = _children(data, seq_start, seq_end)
        return bool(inner) and inner[0][0] == 0x01 and data[inner[0][2]:inner[0][3]] != b"\x00"
    return False


@dataclass(frozen=True)
class Certificate:
    """Bir X.509 sertifikasının arayüzde gösterilen alanları. `sha256`: DER'in parmak izi (küçük harf onaltılık)."""

    der: bytes = field(repr=False)
    subject: Dict[str, Optional[str]]
    issuer: Dict[str, Optional[str]]
    not_before: datetime
    not_after: datetime
    sha256: str
    is_ca: bool
    self_signed: bool

    @property
    def pem(self) -> str:
        return ssl.DER_cert_to_PEM_cert(self.der)

    def expired(self, now: Optional[datetime] = None) -> bool:
        return (now or datetime.now(timezone.utc)) > self.not_after

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": dict(self.subject),
            "issuer": dict(self.issuer),
            "sha256": self.sha256,
            "not_before": self.not_before.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "not_after": self.not_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expired": self.expired(),
            "is_ca": self.is_ca,
            "self_signed": self.self_signed,
        }


def parse_certificate(der: bytes) -> Certificate:
    """DER sertifikayı çözer; X.509 değilse ValueError."""
    try:
        tag, cert_start, cert_end = _tlv(der, 0, len(der))
        if tag != 0x30:
            raise ValueError("not a SEQUENCE")
        tbs_tag, tbs_start, tbs_end = _tlv(der, cert_start, cert_end)
        if tbs_tag != 0x30:
            raise ValueError("no TBSCertificate")
        items = _children(der, tbs_start, tbs_end)
        version1 = not items or items[0][0] != 0xA0
        if not version1:
            items = items[1:]
        # seri no, imza algoritması, issuer, validity, subject, subjectPublicKeyInfo, [uzantılar]
        if len(items) < 6:
            raise ValueError("short TBSCertificate")
        issuer_item, validity, subject_item = items[2], items[3], items[4]
        times = _children(der, validity[2], validity[3])
        if len(times) != 2:
            raise ValueError("bad validity")
        is_ca = False
        for tag, _pos, body, stop in items[6:]:
            if tag == 0xA3:  # [3] EXPLICIT Extensions
                _t, ext_start, ext_end = _tlv(der, body, stop)
                is_ca = _is_ca_extension(der, ext_start, ext_end)
        self_signed = der[issuer_item[1]:issuer_item[3]] == der[subject_item[1]:subject_item[3]]
        return Certificate(
            der=bytes(der),
            subject=_name(der, subject_item[2], subject_item[3]),
            issuer=_name(der, issuer_item[2], issuer_item[3]),
            not_before=_time(der, times[0][0], times[0][2], times[0][3]),
            not_after=_time(der, times[1][0], times[1][2], times[1][3]),
            sha256=hashlib.sha256(der).hexdigest(),
            # Uzantısız (v1) kendinden imzalı kökler eski CA'lardır: OpenSSL de onları CA sayar
            is_ca=is_ca or (version1 and self_signed),
            self_signed=self_signed,
        )
    except (ValueError, IndexError, UnicodeDecodeError) as e:
        raise ValueError(f"not an X.509 certificate: {e}") from None


def pem_certificates(text: str) -> List[Certificate]:
    """Bir PEM metnindeki sertifikalar, sırayla; bozuk bloklar atlanır."""
    found = []
    for block in _PEM_BLOCK.findall(text):
        try:
            found.append(parse_certificate(base64.b64decode("".join(block.split()), validate=True)))
        except ValueError:
            continue
    return found


def normalize_fingerprint(value: str) -> str:
    """Kullanıcının yazdığı parmak izi → küçük harf onaltılık (iki nokta ve boşluklar atılır); geçersizse ValueError."""
    text = re.sub(r"[\s:]", "", value or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ValueError("a SHA-256 fingerprint is 64 hexadecimal digits")
    return text


# --- depo ----------------------------------------------------------------------------------------


def ca_dir(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Deponun yeri: yapılandırma dizininin (sofascore_scraper/config/overrides.py ile aynı çözüm) `ca/` klasörü."""
    env = os.environ if environ is None else environ
    return (Path(env.get("SOFASCORE_CONFIG_DIR") or "config") / CA_DIR_NAME).absolute()


@dataclass(frozen=True)
class StoredCertificate:
    file: str
    certificate: Certificate

    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.file, **self.certificate.to_dict()}


def _pem_files(directory: Path) -> List[Path]:
    try:
        return sorted(p for p in directory.iterdir() if p.suffix.lower() == ".pem" and p.is_file())
    except OSError:
        return []


def stored(environ: Optional[Mapping[str, str]] = None) -> List[StoredCertificate]:
    """Depodaki sertifikalar (dosya adına göre sıralı; aynı sertifika bir kez). Okunamayan dosya atlanır."""
    found: List[StoredCertificate] = []
    seen = set()
    for path in _pem_files(ca_dir(environ)):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("Trusted certificate file %s cannot be read: %s", path, e)
            continue
        for certificate in pem_certificates(text):
            if certificate.sha256 not in seen:
                seen.add(certificate.sha256)
                found.append(StoredCertificate(path.name, certificate))
    return found


def add(certificate: Certificate, environ: Optional[Mapping[str, str]] = None) -> StoredCertificate:
    """Sertifikayı depoya yazar (0600; klasör 0700). Zaten depodaysa var olan kaydı döndürür."""
    for item in stored(environ):
        if item.certificate.sha256 == certificate.sha256:
            return item
    directory = ca_dir(environ)
    make_private_dir(str(directory))
    path = directory / f"trusted-{certificate.sha256[:16]}.pem"
    header = (
        "# Trusted by the user (sofascore-scraper). Whoever holds this certificate's key can read and change\n"
        "# what the app downloads. Remove the file to stop trusting it.\n"
        f"# subject: {certificate.subject.get('common_name') or '?'}\n"
        f"# sha256: {certificate.sha256}\n"
    )
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(header + certificate.pem)
        restrict_permissions(tmp, PRIVATE_FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    logger.warning(
        "Certificate trusted: %s (sha256 %s), stored in %s",
        certificate.subject.get("common_name") or "?", certificate.sha256, path,
    )
    return StoredCertificate(path.name, certificate)


def remove(sha256: str, environ: Optional[Mapping[str, str]] = None) -> bool:
    """
    Parmak izi verilen sertifikayı depodan çıkarır; depoda yoksa False. Dosyada başka sertifika da varsa
    (elle konmuş bir paket) yalnızca o blok çıkarılır.
    """
    wanted = normalize_fingerprint(sha256)
    removed = False
    for path in _pem_files(ca_dir(environ)):
        try:
            certificates = pem_certificates(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not any(c.sha256 == wanted for c in certificates):
            continue
        kept = [c for c in certificates if c.sha256 != wanted]
        if kept:
            path.write_text("".join(c.pem for c in kept), encoding="utf-8")
            restrict_permissions(str(path), PRIVATE_FILE_MODE)
        else:
            path.unlink()
        removed = True
    if removed:
        logger.warning("Certificate no longer trusted: sha256 %s", wanted)
    return removed


# --- HTTP istemcisi: birleşik paket ------------------------------------------------------------------


def base_bundle(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """
    curl_cffi'nin bu ortamda `verify` verilmeden kullanacağı CA dosyası; curl_cffi ile aynı sırayla:
    REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE (oturum), yoksa curl_cffi'nin içe aktarılırken seçtiği varsayılan
    (SSL_CERT_FILE, sistem deposu, certifi). Depodaki sertifikalar BUNUN ÜSTÜNE eklenir: açıkça verilmiş bir
    paket geçerliliğini korur.
    """
    env = os.environ if environ is None else environ
    explicit = env.get("REQUESTS_CA_BUNDLE") or env.get("CURL_CA_BUNDLE")
    if explicit:
        return explicit
    try:
        from curl_cffi import curl as _curl

        default = getattr(_curl, "DEFAULT_CACERT", None)
        if default:
            return str(default)
    except Exception:
        pass
    if env.get("SSL_CERT_FILE") and os.path.exists(env["SSL_CERT_FILE"]):
        return env["SSL_CERT_FILE"]
    cafile = ssl.get_default_verify_paths().cafile
    if cafile and os.path.exists(cafile):
        return cafile
    try:
        import certifi

        return certifi.where()
    except Exception:
        return None


_bundle_lock = threading.Lock()
_bundle_cache: Dict[str, Tuple[Any, str]] = {}


def _stat_key(path: Path) -> Tuple[str, int, int]:
    info = path.stat()
    return (path.name, info.st_mtime_ns, info.st_size)


def _write_private(path: Path, text: str) -> None:
    make_private_dir(str(path.parent))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        restrict_permissions(tmp, PRIVATE_FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def bundle_path(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """
    Depo boş değilse birleşik CA dosyasının yolu (temel paket + depodaki sertifikalar), boşsa None. Her istekte
    çağrılır: depo ya da temel paket değişmediyse yalnızca birkaç `stat` yapar, değiştiyse dosyayı yeniden yazar.
    Temel paket okunamıyorsa None (curl kendi hatasını verir).
    """
    directory = ca_dir(environ)
    files = _pem_files(directory)
    if not files:
        return None
    base = base_bundle(environ)
    try:
        signature: Any = (base, _stat_key(Path(base)) if base else None, tuple(_stat_key(p) for p in files))
    except OSError:
        return None
    key = str(directory)
    with _bundle_lock:
        cached = _bundle_cache.get(key)
        if cached and cached[0] == signature and os.path.exists(cached[1]):
            return cached[1]
        certificates = stored(environ)
        if not certificates:
            return None
        try:
            base_text = Path(base).read_text(encoding="utf-8", errors="replace") if base else ""
        except OSError:
            return None
        text = base_text.rstrip("\n") + "\n" + "".join(item.certificate.pem for item in certificates)
        target = directory / _BUNDLE_DIR / _BUNDLE_NAME
        try:
            _write_private(target, text)
        except OSError:
            # Depo salt okunur bağlanmış olabilir: paket geçici dizine yazılır
            digest = hashlib.sha256(key.encode()).hexdigest()[:12]
            owner = os.getuid() if hasattr(os, "getuid") else 0
            target = Path(tempfile.gettempdir()) / f"sofascore-ca-{owner}-{digest}" / _BUNDLE_NAME
            try:
                _write_private(target, text)
            except OSError as e:
                logger.warning("The CA bundle with the trusted certificates could not be written: %s", e)
                return None
        _bundle_cache[key] = (signature, str(target))
        return str(target)


def verify_file(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Doğrulayan bir TLS istemcisinin kullanması gereken CA dosyası: birleşik paket, depo boşsa temel paket."""
    return bundle_path(environ) or base_bundle(environ)


# --- tarayıcı: NSS veritabanı ---------------------------------------------------------------------


def browser_supported() -> bool:
    """Depo tarayıcıya uygulanabiliyor mu: Linux'ta Chromium NSS veritabanını okur ve libnss3 yüklüdür."""
    return sys.platform.startswith("linux") and ctypes.util.find_library("nss3") is not None


def browser_environment(profile_dir: str, environ: Optional[Mapping[str, str]] = None) -> Optional[Dict[str, str]]:
    """
    Tarayıcının başlatılacağı ortam: depodaki sertifikaları taşıyan NSS veritabanının bulunduğu, köprüye özel
    HOME. Depo boşsa ya da platform desteklemiyorsa None (tarayıcı olağan ortamla başlar). Veritabanı her
    başlatmada depodan yeniden kurulur: depodan çıkarılan sertifika tarayıcıdan da çıkar. Kullanıcının kendi
    `~/.pki/nssdb`'sine dokunulmaz.
    """
    certificates = stored(environ)
    if not certificates:
        return None
    if not browser_supported():
        logger.warning(
            "Trusted certificates are stored, but the built-in browser cannot be given them on this platform "
            "(it uses the trust store of the operating system)."
        )
        return None
    home = os.path.join(profile_dir, _BROWSER_HOME)
    database = os.path.join(home, ".pki", "nssdb")
    try:
        shutil.rmtree(database, ignore_errors=True)
        os.makedirs(database, mode=PRIVATE_DIR_MODE, exist_ok=True)
        pem_path = os.path.join(home, "trusted.pem")
        with open(pem_path, "w", encoding="utf-8") as out:
            out.write("".join(item.certificate.pem for item in certificates))
        done = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("nssdb.py")), database, pem_path],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("The trusted certificates could not be given to the browser: %s", e)
        return None
    if done.returncode != 0:
        logger.warning(
            "The trusted certificates could not be given to the browser: %s",
            (done.stderr or done.stdout).strip()[:300],
        )
        return None
    logger.info("The browser trusts %d stored certificate(s) (NSS database in %s)", len(certificates), home)
    return {**os.environ, "HOME": home}


# --- yoklama -------------------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """
    Ağın API sunucusu için sunduğu zincir. `presented`: güvenilebilecek sertifika (`can_trust` ise), yoksa None.
    `leaf`: sunucunun kendi sertifikası (kimin imzaladığını göstermek için; zincir okunabildiyse).
    """

    host: str
    port: int
    verified: bool = False
    chain_length: int = 0
    leaf: Optional[Certificate] = None
    presented: Optional[Certificate] = None
    can_trust: bool = False
    reason: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "verified": self.verified,
            "chain_length": self.chain_length,
            "leaf": self.leaf.to_dict() if self.leaf else None,
            "presented": self.presented.to_dict() if self.presented else None,
            "can_trust": self.can_trust,
            "reason": self.reason,
            "error": self.error,
        }


def target() -> Tuple[str, str, int]:
    """Yoklanan sunucu: yapılandırılmış API kökünün (`client.base_url`) şeması, adı ve portu."""
    from sofascore_scraper.config import loader
    from sofascore_scraper.config.settings import DEFAULT_API_BASE_URL

    try:
        base = loader.active_settings().client.effective_base_url or DEFAULT_API_BASE_URL
    except Exception:
        base = DEFAULT_API_BASE_URL
    url = urlparse(base)
    return url.scheme, url.hostname or "", url.port or (443 if url.scheme == "https" else 80)


def proxy_configured() -> bool:
    from sofascore_scraper.config import loader

    try:
        client = loader.active_settings().client
        return bool(client.use_proxy and (client.proxy or "").strip())
    except Exception:
        return False


def fetch_chain(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> Tuple[List[bytes], bool]:
    """
    Sunucunun sunduğu zincir (DER; yaprak başta), DOĞRULANMADAN. İkinci değer: zincirin tamamı okunabildi mi
    (Python 3.13+); eski sürümlerde yalnızca yaprak döner.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            getter = getattr(tls, "get_unverified_chain", None)
            if getter is not None:
                chain = [bytes(item) for item in getter() or ()]
                if chain:
                    return chain, True
            leaf = tls.getpeercert(binary_form=True)
            return ([leaf] if leaf else []), False


def _verifying_context(cafile: Optional[str]) -> ssl.SSLContext:
    """curl gibi doğrulayan bağlam: depodaki bir ara CA da güven çıpasıdır (kısmi zincir), katı X.509 denetimi yok."""
    context = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return context


def client_context(environ: Optional[Mapping[str, str]] = None) -> Optional[ssl.SSLContext]:
    """
    Depo boş değilse, depodaki sertifikalara da güvenen doğrulayan TLS bağlamı (Python'un kendi soketleri için:
    canlı `direct` kaynağı); depo boşsa None (çağıran olağan bağlamını kullanır).
    """
    path = bundle_path(environ)
    return _verifying_context(path) if path else None


def verifies(
    host: str, port: int, cafile: Optional[str], extra: Sequence[Certificate] = (), timeout: float = PROBE_TIMEOUT,
) -> Tuple[bool, Optional[str]]:
    """
    Sunucu `cafile` (+ `extra`) güveniyle doğrulanıyor mu: (evet/hayır, hata metni). curl gibi davranır: depodaki
    bir ara CA da güven çıpası sayılır (kısmi zincir) ve katı X.509 denetimleri uygulanmaz. Bağlantı hatası OSError.
    """
    context = _verifying_context(cafile)
    for certificate in extra:
        context.load_verify_locations(cadata=certificate.der)
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return True, None
    except ssl.SSLCertVerificationError as e:
        return False, e.verify_message or str(e)


def probe(timeout: float = PROBE_TIMEOUT, environ: Optional[Mapping[str, str]] = None) -> ProbeResult:
    """
    Ağ API sunucusu için ne sunuyor ve ona güvenilebilir mi? En çok üç TLS el sıkışması yapar (zinciri okumak,
    şu anki güvenle doğrulamak, adayla doğrulamak); HTTP isteği göndermez. Hata fırlatmaz: neden `reason`dadır.
    """
    scheme, host, port = target()
    result = ProbeResult(host=host, port=port)
    if proxy_configured():
        result.reason = REASON_PROXY
        return result
    if scheme != "https" or not host:
        result.reason = REASON_NOT_HTTPS
        return result
    try:
        chain_der, complete = fetch_chain(host, port, timeout)
        chain = [parse_certificate(der) for der in chain_der]
        if not chain:
            raise OSError("the server presented no certificate")
        result.chain_length = len(chain)
        result.leaf = chain[0]
        result.verified, result.error = verifies(host, port, verify_file(environ), timeout=timeout)
        if result.verified:
            result.reason = REASON_VERIFIED
            return result
        top = chain[-1]
        if not complete and not (top.is_ca and top.self_signed):
            result.reason = REASON_CHAIN_UNAVAILABLE
        elif not top.is_ca:
            result.reason = REASON_ROOT_NOT_SENT
        elif top.expired():
            result.reason = REASON_EXPIRED
        else:
            enough, error = verifies(host, port, verify_file(environ), extra=[top], timeout=timeout)
            if enough:
                result.presented, result.can_trust = top, True
            else:
                result.reason, result.error = REASON_NOT_ENOUGH, error
    except (OSError, ValueError) as e:
        result.reason, result.error = REASON_UNREACHABLE, f"{type(e).__name__}: {e}"
    return result


class TrustRefused(Exception):
    """Sertifikaya güvenilemedi: `reason` yoklamanın nedeni ya da REASON_CHANGED; `probe` o anki yoklama."""

    def __init__(self, reason: str, probe_result: ProbeResult) -> None:
        super().__init__(reason)
        self.reason = reason
        self.probe = probe_result


def trust_presented(
    sha256: Optional[str], environ: Optional[Mapping[str, str]] = None, timeout: float = PROBE_TIMEOUT,
) -> Tuple[StoredCertificate, ProbeResult]:
    """
    Ağın ŞU AN sunduğu sertifikayı depoya yazar. `sha256` verilmişse sunulan sertifikanın parmak izi ona eşit
    olmalıdır: kullanıcı tam olarak gördüğü sertifikayı onaylamıştır; arada değiştiyse TrustRefused("changed").
    """
    wanted = normalize_fingerprint(sha256) if sha256 is not None else None
    result = probe(timeout=timeout, environ=environ)
    if not result.can_trust or result.presented is None:
        raise TrustRefused(result.reason or REASON_UNREACHABLE, result)
    if wanted is not None and result.presented.sha256 != wanted:
        raise TrustRefused(REASON_CHANGED, result)
    return add(result.presented, environ), result
