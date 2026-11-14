"""
Sertifikaları bir NSS veritabanına güvenilen CA olarak yazar (`certutil -A -t C,,` ile aynı iş; imajda certutil
yoktur, libnss3 ise Chromium'un bağımlılığı olarak vardır).

    python nssdb.py <veritabanı dizini> <PEM dosyası>

Ayrı süreçte çalışır (sofascore_scraper/client/trust.py `browser_environment`): NSS süreç genelinde durum tutar ve
ctypes ile çağrılır; bir hata uygulamanın sürecini düşürmesin. Yalnızca standart kütüphaneyi kullanır ve paket içe
aktarmaz (betik olarak çalıştırılır). Chromium (Linux) `$HOME/.pki/nssdb` altındaki bu veritabanını okur ve
zinciri buradaki CA'lara kadar gerçekten doğrular.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import os
import re
import sys
from typing import List

_PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)
# Sunucu sertifikaları için güvenilen CA (certutil'in "C,," bayrakları)
_TRUST_FLAGS = b"C,,"


class _SECItem(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_char_p), ("len", ctypes.c_uint)]


class _CertTrust(ctypes.Structure):
    _fields_ = [("ssl", ctypes.c_uint), ("email", ctypes.c_uint), ("object_signing", ctypes.c_uint)]


def import_certificates(database: str, ders: List[bytes]) -> None:
    """`ders`teki sertifikaları `database` dizinindeki (yoksa oluşturulan) NSS veritabanına güvenilen CA olarak yazar."""
    library = ctypes.util.find_library("nss3")
    if not library:
        raise RuntimeError("libnss3 is not installed")
    nss = ctypes.CDLL(library)
    nss.PK11_GetInternalKeySlot.restype = ctypes.c_void_p
    nss.PK11_NeedUserInit.argtypes = [ctypes.c_void_p]
    nss.PK11_InitPin.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    nss.PK11_FreeSlot.argtypes = [ctypes.c_void_p]
    nss.CERT_GetDefaultCertDB.restype = ctypes.c_void_p
    nss.CERT_NewTempCertificate.restype = ctypes.c_void_p
    nss.CERT_NewTempCertificate.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_SECItem), ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
    ]
    nss.PK11_ImportCert.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_int]
    nss.CERT_DecodeTrustString.argtypes = [ctypes.POINTER(_CertTrust), ctypes.c_char_p]
    nss.CERT_ChangeCertTrust.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_CertTrust)]
    nss.CERT_DestroyCertificate.argtypes = [ctypes.c_void_p]

    os.makedirs(database, mode=0o700, exist_ok=True)
    if nss.NSS_InitReadWrite(("sql:" + database).encode()) != 0:
        raise RuntimeError("the NSS database could not be opened: " + database)
    try:
        slot = nss.PK11_GetInternalKeySlot()
        if not slot:
            raise RuntimeError("NSS has no internal key slot")
        try:
            if nss.PK11_NeedUserInit(slot):
                nss.PK11_InitPin(slot, None, b"")  # yeni veritabanı: boş parola
            database_handle = nss.CERT_GetDefaultCertDB()
            for index, der in enumerate(ders):
                item = _SECItem(0, der, len(der))
                certificate = nss.CERT_NewTempCertificate(database_handle, ctypes.byref(item), None, 0, 1)
                if not certificate:
                    raise RuntimeError("certificate %d could not be decoded" % index)
                try:
                    nickname = ("sofascore-scraper trusted %d" % index).encode()
                    if nss.PK11_ImportCert(slot, certificate, 0, nickname, 0) != 0:
                        raise RuntimeError("certificate %d could not be imported" % index)
                    trust = _CertTrust()
                    if nss.CERT_DecodeTrustString(ctypes.byref(trust), _TRUST_FLAGS) != 0:
                        raise RuntimeError("the trust flags could not be decoded")
                    if nss.CERT_ChangeCertTrust(database_handle, certificate, ctypes.byref(trust)) != 0:
                        raise RuntimeError("certificate %d could not be marked as trusted" % index)
                finally:
                    nss.CERT_DestroyCertificate(certificate)
        finally:
            nss.PK11_FreeSlot(slot)
    finally:
        nss.NSS_Shutdown()


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: nssdb.py DATABASE_DIR PEM_FILE\n")
        return 2
    try:
        with open(argv[1], encoding="utf-8", errors="replace") as source:
            blocks = _PEM_BLOCK.findall(source.read())
        ders = [base64.b64decode("".join(block.split())) for block in blocks]
        import_certificates(argv[0], ders)
    except Exception as e:  # tek satırlık neden; çağıran loga yazar
        sys.stderr.write("{}: {}\n".format(type(e).__name__, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
