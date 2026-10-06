"""
Gizli değerleri log satırlarından ve tanılama paketinden ayıklar.

Log dosyası ve tanılama paketi hata bildirimine eklenmek içindir; içlerinde token, cookie
ya da proxy parolası bulunmamalı. Üç katman birlikte çalışır:

  1. Bilinen değerler: .env dosyasındaki, adı gizli bir şeye benzeyen anahtarların değerleri
     (TOKEN, SECRET, PASSWORD, KEY, COOKIE...), ayrıca SOFA_CAPTCHA_TOKEN, web erişim belirteci
     SOFASCORE_API_TOKEN (yalnızca ortamda ayarlı olsa da) ve URL biçimli
     değerlerin (PROXY_URL) içindeki kullanıcı adı/parola; adres şemasız yazılmış olsa da
     ("kullanıcı:parola@host:8080"). Metinde geçtikleri her yerde, hangi biçimde yazılmış
     olurlarsa olsunlar `***` olur.
  2. Çalışma anında öğrenilen, hiçbir ayarın adı olmayan değerler (add_runtime_secret): `direct` canlı
     kaynağının sitenin sayfasından okuduğu push kimlik bilgisi gibi. Yalnızca süreç belleğindedir.
  3. Kalıplar: `scheme://kullanıcı:parola@host`, JWT (sofa_captcha böyle bir token),
     Cookie / Authorization / X-Captcha başlıkları, `token=...` gibi anahtar=değer çiftleri.
     Bunlar .env'de hiç yazmayan değerleri de (tarayıcıdan gelen cookie) yakalar.

Bu modül src.logger tarafından içe aktarılır; o yüzden yalnızca src.paths'e bağımlıdır.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

import dotenv

from src.paths import env_file_path

MASK = "***"

# .env'de adı ne olursa olsun gizli sayılan uygulama anahtarları (ortamdan da okunur: Docker -e)
KNOWN_SECRET_KEYS = ("SOFA_CAPTCHA_TOKEN", "SOFASCORE_API_TOKEN")
# Değeri URL olan ve içinde kimlik bilgisi taşıyabilen uygulama anahtarları
KNOWN_URL_KEYS = ("PROXY_URL", "API_BASE_URL")
# Değeri [[sink]] tablolarının JSON listesi olan anahtarlar: webhook adresinin yolu ve sorgusu da belirteç
# taşıyabilir (Slack, Discord...), bu yüzden `url` alanları mask_webhook_url ile gösterilir
KNOWN_SINK_LIST_KEYS = ("SOFASCORE_SINKS",)

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
# "https://host/yol?mail=a@b" gibi adresler eşleşmez. Parola '@' içerebilir: host'tan önceki
# SON '@' ayırır (açgözlü eşleşme), yoksa "user:p@ss@host" adresinde parolanın sonu açıkta kalırdı.
_URL_USERINFO_RE = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/?#]+@")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]*)?")
_BEARER_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{8,}", re.IGNORECASE)
# Başlıklar: değer tırnak içindeyse tırnağa, değilse satır sonuna kadar
_HEADER_RE = re.compile(
    r"""(["']?\b(?:set-cookie|cookie|proxy-authorization|authorization|x-captcha)["']?\s*[:=]\s*)"""
    r"""(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]+)""",
    re.IGNORECASE,
)
# anahtar=değer / "anahtar": "değer": adı gizli bir şeye benzeyen anahtarın değeri. Adı `_env` ile biten anahtar
# (`secret_env`, `token_env`) bir değişkenin ADINI taşır, değerini değil: "secret_env: expected ..." gibi bir hata
# iletisinde sonraki sözcük yutulmasın diye eşleşmez.
_PAIR_RE = re.compile(
    r"""(\b(?:sofa_captcha|cf_clearance|__cf_bm|[\w\-]{0,40}(?:token|password|passwd|secret|api[_\-]?key)[\w\-]{0,40})"""
    r"""(?<![_\-]env)["']?\s*[:=]\s*)("[^"\r\n]*"|'[^'\r\n]*'|[^\s"',;&}\]]+)""",
    re.IGNORECASE,
)

_lock = threading.Lock()
_cached_values: Tuple[str, ...] = ()
# URL değerlerindeki "kullanıcı:parola@" parçaları: uzunluk sınırı olmadan, `***@` ile değiştirilir
_cached_userinfo: Tuple[str, ...] = ()
_cached_at: Optional[float] = None
# .env'in son ayrıştırılmış hali: (yol, mtime_ns, boyut) → değerler
_file_cache: Tuple[Optional[Tuple[str, int, int]], Dict[str, str]] = (None, {})
# Bilinen değerler toplanırken aynı thread'den gelen log satırı için (bkz. secret_values)
_collecting = threading.local()
# Çalışma anında öğrenilen gizli değerler ve yazımları (add_runtime_secret), uzun olan önce. Yalnızca bellekte.
_runtime_lock = threading.Lock()
_runtime_values: Tuple[str, ...] = ()


def is_secret_key(key: str) -> bool:
    """Anahtarın adı gizli bir değere mi benziyor (TOKEN, PASSWORD, API_KEY...)?"""
    return key in KNOWN_SECRET_KEYS or bool(_SECRET_KEY_RE.search(key))


def _split_userinfo(value: str) -> Optional[Tuple[str, str, str]]:
    """
    "[scheme://]kullanıcı[:parola]@host[:port][/...]" → (baş, kullanıcı bilgisi, "@host..." ve sonrası);
    kullanıcı bilgisi yoksa None. urlsplit'e güvenmez: şemasız yazılmış ("user:pass@host:8080")
    bir proxy adresi de maskelenmeli; parola '@' içerebilir (host'tan önceki son '@' ayırır).
    """
    scheme, sep, rest = value.partition("://")
    if not sep:
        scheme, rest = "", value
    cut = min((i for i in (rest.find(c) for c in "/?#") if i >= 0), default=len(rest))
    userinfo, at, hostport = rest[:cut].rpartition("@")
    if not at or not userinfo:
        return None
    return scheme + sep, userinfo, at + hostport + rest[cut:]


def _url_credentials(value: str) -> List[str]:
    """URL biçimli bir değerdeki kullanıcı bilgisi parçaları (ham ve yüzde-çözülmüş)."""
    parts = _split_userinfo(value)
    if parts is None:
        return []
    userinfo = parts[1]
    found = []
    for piece in (userinfo, userinfo.partition(":")[2]):
        if piece:
            found.extend((piece, unquote(piece)))
    return found


def mask_url_userinfo(value: str) -> str:
    """Adresin kullanıcı bilgisini (varsa) `***` yapar; şemasız yazılmış adreste de."""
    parts = _split_userinfo(value)
    if parts is None:
        return value
    return f"{parts[0]}{MASK}{parts[2]}"


def mask_webhook_url(value: str) -> str:
    """
    Bir webhook adresinin gösterilecek hali: şema, host ve port kalır; kullanıcı bilgisi, yol, sorgu ve parça
    `***` olur ("https://***@hooks.example.org/***"). Bu adreslerde yolun ya da sorgunun kendisi kimlik
    bilgisidir; `config show` ve tanılama paketi adresi yalnızca bu haliyle yazar.
    """
    value = mask_url_userinfo(value)
    scheme, sep, rest = value.partition("://")
    if not sep:
        scheme, rest = "", value
    cut = min((i for i in (rest.find(c) for c in "/?#") if i >= 0), default=len(rest))
    if rest[cut:] in ("", "/"):
        return value
    return f"{scheme}{sep}{rest[:cut]}/{MASK}"


def _mask_sink_list(value: str) -> str:
    """
    SOFASCORE_SINKS: listedeki her tablonun `url` alanı maskelenir ve sink'lerin tanımadığı anahtarların değeri
    `***` olur (`ssc config show` ile aynı kural: src/config/loader.py `mask_sink_table`; yanlış yazılmış bir
    anahtar, ör. `webhook_url`, adresi taşıyabilir). Ayrıştırılamayan değer tümüyle `***` olur. Yükleyici işlev
    içinde içe aktarılır: bu modül hafif kalır (günlükçü onu süreç başında yükler).
    """
    from src.config import loader

    try:
        items = json.loads(value)
    except ValueError:
        return MASK
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        return MASK
    shown = [
        {k: (mask_webhook_url(v) if isinstance(v, str) else MASK) if k == "url" else v
         for k, v in loader.mask_sink_table(item).items()}
        for item in items
    ]
    return json.dumps(shown, ensure_ascii=False, separators=(",", ":"))


def _env_file_values() -> Dict[str, str]:
    """
    .env'deki dolu değerler. Dosya değişmediyse yeniden ayrıştırılmaz: python-dotenv ayrıştıramadığı
    her satır için log'a uyarı yazar; birkaç saniyede bir yeniden okumak log'u o uyarıyla doldururdu.
    """
    global _file_cache
    path = env_file_path()
    try:
        st = os.stat(path)
    except OSError:
        return {}
    signature = (str(path), st.st_mtime_ns, st.st_size)
    if _file_cache[0] != signature:
        try:
            values = {k: v for k, v in dotenv.dotenv_values(path).items() if v}
        except Exception:
            # .env okunamıyorsa yalnızca ortamdaki bilinen anahtarlar kullanılır
            values = {}
        _file_cache = (signature, values)
    return _file_cache[1]


def _collect_values() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(metinde aranacak gizli değerler, URL değerlerindeki "kullanıcı bilgisi@" parçaları)."""
    file_values = _env_file_values()
    values = set()
    userinfo = set()
    # .env'deki her anahtar için hem dosyadaki hem ortamdaki (çalışma anında değişmiş olabilir) değer
    for key in set(file_values) | set(KNOWN_SECRET_KEYS) | set(KNOWN_URL_KEYS):
        for value in (file_values.get(key), os.environ.get(key)):
            if not value:
                continue
            if is_secret_key(key):
                values.add(value)
            pieces = _url_credentials(value)
            values.update(pieces)
            if pieces:
                # Kısa bir parola tek başına aranmaz (MIN_SECRET_LENGTH), ama "kullanıcı:parola@"
                # olarak adresin içinde her zaman tanınır: şemasız adresi kalıplar yakalayamaz
                userinfo.update((pieces[0] + "@", pieces[1] + "@"))

    # Uzun olan önce: kısa bir değer, uzun olanın parçasıysa onu yarım maskelemesin
    def ordered(items) -> Tuple[str, ...]:
        return tuple(sorted(items, key=len, reverse=True))

    return ordered(v for v in values if len(v) >= MIN_SECRET_LENGTH), ordered(userinfo)


def _known() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Bilinen gizli değerler ve kullanıcı bilgisi parçaları (kısa süreli önbellekli)."""
    global _cached_values, _cached_userinfo, _cached_at
    now = time.monotonic()
    if _cached_at is None or now - _cached_at > _CACHE_SECONDS:
        if getattr(_collecting, "active", False):
            # Değerler toplanırken yazılan bir log satırı (python-dotenv ayrıştıramadığı .env satırını
            # logging ile uyarır) maskelenmek için yine buraya gelir: kilidi yeniden istemek süreci
            # kilitlerdi. O satır eldeki değerlerle (ve kalıplarla) maskelenir.
            return _cached_values, _cached_userinfo
        with _lock:
            _collecting.active = True
            try:
                _cached_values, _cached_userinfo = _collect_values()
                _cached_at = now
            finally:
                _collecting.active = False
    return _cached_values, _cached_userinfo


def secret_values() -> Tuple[str, ...]:
    """Metinde aranacak bilinen gizli değerler (kısa süreli önbellekli)."""
    return _known()[0]


def add_runtime_secret(value: Optional[str]) -> None:
    """
    Çalışma anında öğrenilen, adı olan bir ayar olmayan gizli değeri bu süreç boyunca her metinde maskeler (log
    satırları, tanılama özeti, hata iletileri). Değer yalnızca bellekte tutulur. JSON'a kaçışlı ve yüzde
    kodlanmış yazımları da aranır. MIN_SECRET_LENGTH'ten kısa değer aranmaz (her satırdaki aynı harfleri
    maskelerdi): çağıran böyle bir değeri hiçbir yere yazmamalıdır.
    """
    global _runtime_values
    if not isinstance(value, str) or len(value) < MIN_SECRET_LENGTH:
        return
    forms = {value, json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value)[1:-1], quote(value, safe="")}
    with _runtime_lock:
        merged = set(_runtime_values) | {form for form in forms if len(form) >= MIN_SECRET_LENGTH}
        _runtime_values = tuple(sorted(merged, key=len, reverse=True))


def runtime_secret_count() -> int:
    """Çalışma anında öğrenilmiş kaç gizli yazım aranıyor (değerlerin kendisi hiçbir yerden verilmez)."""
    return len(_runtime_values)


def refresh() -> None:
    """Ayar değişti (.env yazıldı / yeniden yüklendi): bilinen değerleri hemen yeniden oku."""
    global _cached_at, _file_cache
    if getattr(_collecting, "active", False):
        return  # toplama sürerken (aynı thread) çağrıldı: kilit zaten bizde
    with _lock:
        _cached_at = None
        _file_cache = (None, {})


def redact_text(text: str) -> str:
    """Metindeki gizli değerleri `***` ile değiştirir. Hiçbir koşulda hata fırlatmaz."""
    if not text:
        return text
    try:
        values, userinfo = _known()
        for value in _runtime_values:
            if value in text:
                text = text.replace(value, MASK)
        for value in values:
            if value in text:
                text = text.replace(value, MASK)
        for value in userinfo:
            if value in text:
                text = text.replace(value, MASK + "@")
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
    value = str(value)
    if key in KNOWN_SINK_LIST_KEYS:
        value = _mask_sink_list(value)
    if key in KNOWN_URL_KEYS:
        # Değer henüz .env'de / ortamda olmasa da (yeni yazılıyor) kimlik bilgisi yapısal olarak maskelenir
        value = mask_url_userinfo(value)
    return redact_text(value)


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
