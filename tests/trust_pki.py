"""
Güvenilen sertifika testlerinin (tests/test_trust.py, test_api_v1_trust.py, test_cli_trust.py) SAHTE PKI'si.

Depoda ne anahtar ne sertifika durur: her şey test oturumunda bir kez, yalnızca standart kütüphaneyle üretilir
(`cryptography` yok, `openssl` ikilisi yok: CI Linux, macOS ve Windows'ta, Python 3.10 ve 3.14 ile çalışır) ve
geçici bir dizine yazılır. Anahtarlar rastgeledir (`secrets`); oturum bitince dizinle birlikte silinir.

    pki()                    oturumun PKI'si (ilk çağrıda üretilir, sonra aynısı döner)
    pki().pem("root")        sertifikanın PEM metni          pki().der("root")   DER baytları
    pki().key_path("leaf")   yaprağın özel anahtarının dosyası (127.0.0.1'deki TLS sunucusu için)
    SPECS["root"]            sertifikanın üretildiği değerler (testlerin beklentileri bunlardan okunur)

Aileler (her ailenin kendi RSA-2048 anahtar çifti vardır; aile içindeki her sertifika onu kullanır):

    inspection   root (kendinden imzalı CA) ─ leaf (localhost)            expired-root (2020'de süresi dolmuş CA)
    two-tier     two-tier-root ─ issuing (ara CA, pathlen 0) ─ leaf-two-tier
    other        other-root ─ leaf-other

Sertifikalar X.509 v3'tür ve sha256WithRSAEncryption (PKCS#1 v1.5) ile imzalanır: Debian/Ubuntu'nun varsayılan
güvenlik düzeyi küçük anahtarları ve SHA-1'i reddeder. CA'lar basicConstraints (kritik, cA) ve keyUsage
(keyCertSign, cRLSign), yapraklar subjectAltName (localhost, 127.0.0.1) ve extendedKeyUsage (serverAuth) taşır;
hepsinde subjectKeyIdentifier, kendinden imzalı olmayanlarda authorityKeyIdentifier vardır (Python 3.13+'ın
varsayılan katı doğrulaması bunları ister).
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- DER ---------------------------------------------------------------------------------------------


def _tlv(tag: int, body: bytes) -> bytes:
    size = len(body)
    if size < 0x80:
        head = bytes([size])
    else:
        encoded = size.to_bytes((size.bit_length() + 7) // 8, "big")
        head = bytes([0x80 | len(encoded)]) + encoded
    return bytes([tag]) + head + body


def _seq(*parts: bytes) -> bytes:
    return _tlv(0x30, b"".join(parts))


def _set(*parts: bytes) -> bytes:
    return _tlv(0x31, b"".join(parts))


def _int(value: int) -> bytes:
    # En kısa artı sayı gösterimi: en üst bit doluysa başa 00 gelir
    return _tlv(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def _oid(dotted: str) -> bytes:
    arcs = [int(part) for part in dotted.split(".")]
    body = bytes([40 * arcs[0] + arcs[1]])
    for arc in arcs[2:]:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


def _bits(data: bytes, unused: int = 0) -> bytes:
    return _tlv(0x03, bytes([unused]) + data)


def _octets(data: bytes) -> bytes:
    return _tlv(0x04, data)


def _time(moment: datetime) -> bytes:
    # RFC 5280: 2049'a kadar UTCTime (iki haneli yıl), sonrası GeneralizedTime
    if moment.year < 2050:
        return _tlv(0x17, moment.strftime("%y%m%d%H%M%SZ").encode("ascii"))
    return _tlv(0x18, moment.strftime("%Y%m%d%H%M%SZ").encode("ascii"))


_NULL = b"\x05\x00"
_TRUE = b"\x01\x01\xff"
_OID_RSA = _oid("1.2.840.113549.1.1.1")
_OID_SHA256_RSA = _oid("1.2.840.113549.1.1.11")
_OID_SHA256 = _oid("2.16.840.1.101.3.4.2.1")
_OID_ORGANIZATION = _oid("2.5.4.10")
_OID_COMMON_NAME = _oid("2.5.4.3")
_OID_SUBJECT_KEY_ID = _oid("2.5.29.14")
_OID_KEY_USAGE = _oid("2.5.29.15")
_OID_SUBJECT_ALT_NAME = _oid("2.5.29.17")
_OID_BASIC_CONSTRAINTS = _oid("2.5.29.19")
_OID_AUTHORITY_KEY_ID = _oid("2.5.29.35")
_OID_EXT_KEY_USAGE = _oid("2.5.29.37")
_OID_SERVER_AUTH = _oid("1.3.6.1.5.5.7.3.1")
_SIGNATURE_ALGORITHM = _seq(_OID_SHA256_RSA, _NULL)


def _pem(label: str, der: bytes) -> str:
    body = base64.b64encode(der).decode("ascii")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN {0}-----\n{1}\n-----END {0}-----\n".format(label, "\n".join(lines))


# --- RSA ---------------------------------------------------------------------------------------------

_E = 65537
KEY_BITS = 2048


def _small_primes(limit: int = 2000) -> List[int]:
    sieve = bytearray([1]) * limit
    sieve[0:2] = b"\x00\x00"
    for number in range(2, int(limit ** 0.5) + 1):
        if sieve[number]:
            sieve[number * number::number] = bytearray(len(range(number * number, limit, number)))
    return [number for number in range(limit) if sieve[number]]


_SMALL_PRIMES = _small_primes()


def _is_probable_prime(candidate: int, rounds: int = 24) -> bool:
    """Miller–Rabin, rastgele tabanlarla (yanılma olasılığı en çok 4^-rounds)."""
    for prime in _SMALL_PRIMES:
        if candidate % prime == 0:
            return candidate == prime
    odd, twos = candidate - 1, 0
    while odd % 2 == 0:
        odd //= 2
        twos += 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(candidate - 3) + 2, odd, candidate)
        if x in (1, candidate - 1):
            continue
        for _ in range(twos - 1):
            x = pow(x, 2, candidate)
            if x == candidate - 1:
                break
        else:
            return False
    return True


def _prime(bits: int) -> int:
    while True:
        # En üst iki bit dolu: iki asalın çarpımı tam `2 * bits` bit olur; tek sayı
        candidate = secrets.randbits(bits) | (0b11 << (bits - 2)) | 1
        if candidate % _E != 1 and _is_probable_prime(candidate):
            return candidate


@dataclass(frozen=True)
class RsaKey:
    """Bir RSA anahtar çifti. Yalnızca testlerin geçici dizinine yazılır; depoya hiçbir zaman girmez."""

    n: int
    d: int = field(repr=False)
    p: int = field(repr=False)
    q: int = field(repr=False)
    e: int = _E

    @classmethod
    def generate(cls, bits: int = KEY_BITS) -> "RsaKey":
        while True:
            p, q = _prime(bits // 2), _prime(bits // 2)
            if p != q:
                break
        return cls(n=p * q, d=pow(_E, -1, (p - 1) * (q - 1)), p=p, q=q)

    @property
    def size(self) -> int:
        return (self.n.bit_length() + 7) // 8

    def public_key_der(self) -> bytes:
        """RSAPublicKey (PKCS#1): SubjectPublicKeyInfo'nun bit dizisindeki yapı."""
        return _seq(_int(self.n), _int(self.e))

    def subject_public_key_info(self) -> bytes:
        return _seq(_seq(_OID_RSA, _NULL), _bits(self.public_key_der()))

    def key_id(self) -> bytes:
        # RFC 7093 yöntemi: açık anahtarın SHA-256'sının ilk 160 biti
        return hashlib.sha256(self.public_key_der()).digest()[:20]

    def sign(self, message: bytes) -> bytes:
        """RSASSA-PKCS1-v1_5, SHA-256 (Çin kalan teoremiyle: düz `pow(m, d, n)`den birkaç kat hızlı)."""
        digest_info = _seq(_seq(_OID_SHA256, _NULL), _octets(hashlib.sha256(message).digest()))
        padded = b"\x00\x01" + b"\xff" * (self.size - len(digest_info) - 3) + b"\x00" + digest_info
        m = int.from_bytes(padded, "big")
        m_p = pow(m % self.p, self.d % (self.p - 1), self.p)
        m_q = pow(m % self.q, self.d % (self.q - 1), self.q)
        signature = m_q + self.q * ((pow(self.q, -1, self.p) * (m_p - m_q)) % self.p)
        assert pow(signature, self.e, self.n) == m  # üretilen imza açık anahtarla doğrulanır
        return signature.to_bytes(self.size, "big")

    def private_pem(self) -> str:
        """PKCS#1 RSAPrivateKey, PEM. Etiket parçalardan kurulur: kaynakta bir anahtar başlığı görünmesin."""
        der = _seq(
            _int(0), _int(self.n), _int(self.e), _int(self.d), _int(self.p), _int(self.q),
            _int(self.d % (self.p - 1)), _int(self.d % (self.q - 1)), _int(pow(self.q, -1, self.p)),
        )
        return _pem(" ".join(("RSA", "PRIVATE", "KEY")), der)


# --- sertifikalar --------------------------------------------------------------------------------------

VALID_FROM = datetime(2025, 1, 1, tzinfo=timezone.utc)
VALID_UNTIL = datetime(2126, 1, 1, tzinfo=timezone.utc)  # 2050 sonrası: GeneralizedTime


@dataclass(frozen=True)
class Spec:
    """Bir sertifikanın üretildiği değerler; testler beklentilerini buradan okur."""

    name: str
    family: str
    organization: str
    common_name: str
    issuer: str  # imzalayan sertifikanın adı; kendi adı: kendinden imzalı
    ca: bool
    path_length: Optional[int] = None
    not_before: datetime = VALID_FROM
    not_after: datetime = VALID_UNTIL

    @property
    def self_signed(self) -> bool:
        return self.issuer == self.name

    @property
    def subject(self) -> Dict[str, Optional[str]]:
        return {"common_name": self.common_name, "organization": self.organization}


SPECS: Dict[str, Spec] = {spec.name: spec for spec in (
    Spec("root", "inspection", "Fake Inspection Örnek A.Ş.", "Fake Inspection Root CA (test fixture)", "root", True),
    Spec("leaf", "inspection", "Fake Test Server", "localhost", "root", False),
    Spec("expired-root", "inspection", "Fake Expired", "Fake Expired Root CA (test fixture)", "expired-root", True,
         not_before=datetime(2020, 1, 1, tzinfo=timezone.utc), not_after=datetime(2020, 2, 1, tzinfo=timezone.utc)),
    Spec("two-tier-root", "two-tier", "Fake Two-Tier", "Fake Two-Tier Root CA (test fixture)", "two-tier-root", True),
    Spec("issuing", "two-tier", "Fake Two-Tier", "Fake Two-Tier Issuing CA (test fixture)", "two-tier-root", True,
         path_length=0),
    Spec("leaf-two-tier", "two-tier", "Fake Test Server", "localhost", "issuing", False),
    Spec("other-root", "other", "Fake Other", "Fake Other Root CA (test fixture)", "other-root", True),
    Spec("leaf-other", "other", "Fake Test Server", "localhost", "other-root", False),
)}
FAMILIES: Tuple[str, ...] = tuple(dict.fromkeys(spec.family for spec in SPECS.values()))


def _name(spec: Spec) -> bytes:
    """Name: O sonra CN, ikisi de UTF8String (openssl'in `-utf8` ile yazdığı gibi)."""
    return _seq(
        _set(_seq(_OID_ORGANIZATION, _tlv(0x0C, spec.organization.encode("utf-8")))),
        _set(_seq(_OID_COMMON_NAME, _tlv(0x0C, spec.common_name.encode("utf-8")))),
    )


def _extension(oid: bytes, value: bytes, critical: bool = False) -> bytes:
    return _seq(oid, _TRUE if critical else b"", _octets(value))


def _extensions(spec: Spec, key: RsaKey, issuer_key: RsaKey) -> bytes:
    found = []
    if spec.ca:
        constraints = _seq(_TRUE, _int(spec.path_length) if spec.path_length is not None else b"")
        found.append(_extension(_OID_BASIC_CONSTRAINTS, constraints, critical=True))
        # keyCertSign (bit 5) ve cRLSign (bit 6): 0000 0110, son bit kullanılmıyor
        found.append(_extension(_OID_KEY_USAGE, _bits(b"\x06", unused=1), critical=True))
    else:
        found.append(_extension(_OID_BASIC_CONSTRAINTS, _seq()))
        found.append(_extension(_OID_EXT_KEY_USAGE, _seq(_OID_SERVER_AUTH)))
        names = _seq(_tlv(0x82, b"localhost"), _tlv(0x87, bytes((127, 0, 0, 1))))
        found.append(_extension(_OID_SUBJECT_ALT_NAME, names))
    found.append(_extension(_OID_SUBJECT_KEY_ID, _octets(key.key_id())))
    if not spec.self_signed:
        found.append(_extension(_OID_AUTHORITY_KEY_ID, _seq(_tlv(0x80, issuer_key.key_id()))))
    return _tlv(0xA3, _seq(*found))


def build_certificate(spec: Spec, key: RsaKey, issuer: Spec, issuer_key: RsaKey) -> bytes:
    """`spec`in sertifikası (DER), `issuer_key` ile imzalı."""
    serial = (int.from_bytes(secrets.token_bytes(16), "big") >> 1) | 1
    tbs = _seq(
        _tlv(0xA0, _int(2)),  # v3
        _int(serial),
        _SIGNATURE_ALGORITHM,
        _name(issuer),
        _seq(_time(spec.not_before), _time(spec.not_after)),
        _name(spec),
        key.subject_public_key_info(),
        _extensions(spec, key, issuer_key),
    )
    return _seq(tbs, _SIGNATURE_ALGORITHM, _bits(issuer_key.sign(tbs)))


# --- oturumun PKI'si -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class FakePki:
    directory: Path
    certificates: Dict[str, bytes] = field(repr=False)
    seconds: float  # üretimin süresi (anahtarlar + sertifikalar)

    def der(self, name: str) -> bytes:
        return self.certificates[name]

    def pem(self, name: str) -> str:
        return _pem("CERTIFICATE", self.certificates[name])

    def sha256(self, name: str) -> str:
        return hashlib.sha256(self.certificates[name]).hexdigest()

    def cert_path(self, name: str) -> Path:
        return self.directory / f"{name}.pem"

    def key_path(self, name: str) -> Path:
        """`name` sertifikasının özel anahtarı (ailesinin anahtarı)."""
        return self.directory / f"{SPECS[name].family}.key"


def generate(directory: Path) -> FakePki:
    """PKI'yi üretir; sertifikaları ve aile anahtarlarını `directory`ye yazar."""
    started = time.perf_counter()
    keys = {family: RsaKey.generate() for family in FAMILIES}
    certificates = {
        name: build_certificate(spec, keys[spec.family], SPECS[spec.issuer], keys[SPECS[spec.issuer].family])
        for name, spec in SPECS.items()
    }
    directory.mkdir(parents=True, exist_ok=True)
    made = FakePki(directory=directory, certificates=certificates, seconds=0.0)
    for name in certificates:
        made.cert_path(name).write_text(made.pem(name), encoding="ascii")
    for family, key in keys.items():
        path = directory / f"{family}.key"
        path.write_text(key.private_pem(), encoding="ascii")
        path.chmod(0o600)
    return FakePki(directory=directory, certificates=certificates, seconds=time.perf_counter() - started)


@lru_cache(maxsize=1)
def pki() -> FakePki:
    """Oturumun PKI'si: ilk çağrıda geçici bir dizine üretilir, süreç bitince dizin silinir."""
    directory = Path(tempfile.mkdtemp(prefix="sofascore-test-pki-"))
    atexit.register(shutil.rmtree, str(directory), True)
    return generate(directory)
