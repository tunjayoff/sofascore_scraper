"""
Gizli değerleri log satırlarından ve tanılama paketinden ayıklar.

Log dosyası ve tanılama paketi hata bildirimine eklenmek içindir; içlerinde token, cookie
ya da proxy parolası bulunmamalı. İki katman birlikte çalışır:

  1. Bilinen değerler: .env dosyasındaki, adı gizli bir şeye benzeyen anahtarların değerleri
     (TOKEN, SECRET, PASSWORD, KEY, COOKIE...), ayrıca SOFA_CAPTCHA_TOKEN ve URL biçimli
     değerlerin (PROXY_URL) içindeki kullanıcı adı/parola. Metinde geçtikleri her yerde,
     hangi biçimde yazılmış olurlarsa olsunlar `***` olur.
  2. Kalıplar: `scheme://kullanıcı:parola@host`, JWT (sofa_captcha böyle bir token),
     Cookie / Authorization / X-Captcha başlıkları, `token=...` gibi anahtar=değer çiftleri.
     Bunlar .env'de hiç yazmayan değerleri de (tarayıcıdan gelen cookie) yakalar.

Bu modül src.logger tarafından içe aktarılır; o yüzden yalnızca src.paths'e bağımlıdır.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import unquote, urlsplit

import dotenv

from src.paths import env_file_path

MASK = "***"

# .env'de adı ne olursa olsun gizli sayılan uygulama anahtarları (ortamdan da okunur: Docker -e)
KNOWN_SECRET_KEYS = ("SOFA_CAPTCHA_TOKEN",)
# Değeri URL olan ve içinde kimlik bilgisi taşıyabilen uygulama anahtarları
KNOWN_URL_KEYS = ("PROXY_URL", "API_BASE_URL")

# Bundan kısa değerler "bilinen değer" olarak aranmaz: 3 harflik bir parola her log satırındaki
# aynı 3 harfi maskelerdi. Kısa değerler yine de kalıplarla (URL, anahtar=değer) yakalanır.
MIN_SECRET_LENGTH = 6

# Bilinen değerler bu kadar saniyede bir yeniden okunur (ayar değişince refresh() hemen yeniler)
_CACHE_SECONDS = 5.0

_SECRET_KEY_RE = re.compile(
    r"TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|CREDENTIAL|COOKIE|CAPTCHA|SIGNATURE|PRIVATE|APIKEY|SESSION"
    r"|(?:^|_)(?:KEY|AUTH|AUTHORIZATION|PASS|PWD)(?:$|_)",
    re.IGNORECASE,
)

# scheme://kullanıcı[:parola]@host — '/', '?' ve '#' kullanıcı bilgisinde olamaz; böylece
# "https://host/yol?mail=a@b" gibi adresler eşleşmez.
_URL_USERINFO_RE = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@?#]+@")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]*)?")
_BEARER_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{8,}", re.IGNORECASE)
# Başlıklar: değer tırnak içindeyse tırnağa, değilse satır sonuna kadar
_HEADER_RE = re.compile(
    r"""(["']?\b(?:set-cookie|cookie|proxy-authorization|authorization|x-captcha)["']?\s*[:=]\s*)"""
    r"""(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]+)""",
    re.IGNORECASE,
)
# anahtar=değer / "anahtar": "değer": adı gizli bir şeye benzeyen anahtarın değeri
_PAIR_RE = re.compile(
    r"""(\b(?:sofa_captcha|cf_clearance|__cf_bm|[\w\-]{0,40}(?:token|password|passwd|secret|api[_\-]?key)[\w\-]{0,40})"""
    r"""["']?\s*[:=]\s*)("[^"\r\n]*"|'[^'\r\n]*'|[^\s"',;&}\]]+)""",
    re.IGNORECASE,
)

_lock = threading.Lock()
_cached_values: Tuple[str, ...] = ()
_cached_at: Optional[float] = None


def is_secret_key(key: str) -> bool:
    """Anahtarın adı gizli bir değere mi benziyor (TOKEN, PASSWORD, API_KEY...)?"""
    return key in KNOWN_SECRET_KEYS or bool(_SECRET_KEY_RE.search(key))


def _url_credentials(value: str) -> Iterable[str]:
    """URL biçimli bir değerdeki kullanıcı bilgisi parçaları (ham ve yüzde-çözülmüş)."""
    if "://" not in value or "@" not in value:
        return ()
    try:
        parts = urlsplit(value)
        netloc = parts.netloc
        password = parts.password
    except ValueError:
        return ()
    found = []
    userinfo = netloc.rsplit("@", 1)[0] if "@" in netloc else ""
    for piece in (userinfo, password):
        if piece:
            found.extend((piece, unquote(piece)))
    return found


def _env_file_values() -> Dict[str, str]:
    try:
        return {k: v for k, v in dotenv.dotenv_values(env_file_path()).items() if v}
    except Exception:
        # .env okunamıyorsa yalnızca ortamdaki bilinen anahtarlar kullanılır
        return {}


def _collect_values() -> Tuple[str, ...]:
    file_values = _env_file_values()
    values = set()
    # .env'deki her anahtar için hem dosyadaki hem ortamdaki (çalışma anında değişmiş olabilir) değer
    for key in set(file_values) | set(KNOWN_SECRET_KEYS) | set(KNOWN_URL_KEYS):
        for value in (file_values.get(key), os.environ.get(key)):
            if not value:
                continue
            if is_secret_key(key):
                values.add(value)
            values.update(_url_credentials(value))
    # Uzun olan önce: kısa bir değer, uzun olanın parçasıysa onu yarım maskelemesin
    return tuple(sorted((v for v in values if len(v) >= MIN_SECRET_LENGTH), key=len, reverse=True))


def secret_values() -> Tuple[str, ...]:
    """Metinde aranacak bilinen gizli değerler (kısa süreli önbellekli)."""
    global _cached_values, _cached_at
    now = time.monotonic()
    if _cached_at is None or now - _cached_at > _CACHE_SECONDS:
        with _lock:
            _cached_values = _collect_values()
            _cached_at = now
    return _cached_values


def refresh() -> None:
    """Ayar değişti (.env yazıldı / yeniden yüklendi): bilinen değerleri hemen yeniden oku."""
    global _cached_at
    with _lock:
        _cached_at = None


def redact_text(text: str) -> str:
    """Metindeki gizli değerleri `***` ile değiştirir. Hiçbir koşulda hata fırlatmaz."""
    if not text:
        return text
    try:
        for value in secret_values():
            if value in text:
                text = text.replace(value, MASK)
        text = _URL_USERINFO_RE.sub(rf"\1{MASK}@", text)
        text = _HEADER_RE.sub(rf"\1{MASK}", text)
        text = _BEARER_RE.sub(rf"\1 {MASK}", text)
        text = _PAIR_RE.sub(rf"\1{MASK}", text)
        text = _JWT_RE.sub(MASK, text)
        return text
    except Exception:
        # Maskeleme çalışmıyorsa satırı olduğu gibi yazmak yerine hiç yazma
        return "[redaction failed: message withheld]"


def mask_value(key: str, value: Optional[str]) -> Optional[str]:
    """Bir ayarın gösterilecek hali: gizli anahtar tümüyle, diğerlerinde yalnız gizli parçalar maskelenir."""
    if value is None or value == "":
        return value
    if is_secret_key(key):
        return MASK
    return redact_text(str(value))


def redact_obj(obj: Any) -> Any:
    """JSON'a benzer bir yapının kopyası: gizli adlı anahtarların değerleri ve tüm metinler maskelenir."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and is_secret_key(k) and isinstance(v, (str, bytes)) and v:
                out[k] = MASK
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
