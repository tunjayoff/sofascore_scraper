"""Gizli değer maskeleme (sofascore_scraper/redact.py): token, cookie, proxy kimlik bilgisi, .env'deki gizli değerler."""
from __future__ import annotations

import pytest

from sofascore_scraper import redact

JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3OTAwMDAwMDAsInN1YiI6InNvZmEifQ.c2lnbmF0dXJlLXZhbHVlLTEyMw"
PROXY = "http://scraper:Pr0xy-P4ss!word@proxy.example.com:8080"
API_KEY = "sk-live-0123456789abcdef"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Geçici bir .env: içeriği yazıp bilinen gizli değerleri yeniden okutan bir fonksiyon döner."""
    path = tmp_path / ".env"
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(path))
    for key in ("SOFA_CAPTCHA_TOKEN", "PROXY_URL"):
        monkeypatch.delenv(key, raising=False)

    def write(text: str) -> None:
        path.write_text(text, encoding="utf-8")
        redact.refresh()

    write("")
    yield write
    monkeypatch.undo()
    redact.refresh()


# --- kalıplar: .env'de hiç yazmayan değerler de maskelenir ------------------------------

@pytest.mark.parametrize(
    "text, leaked, kept",
    [
        (f"Proxy/Bağlantı hatası: could not connect to {PROXY} - proxy: açık", ["Pr0xy-P4ss!word", "scraper:"],
         ["proxy.example.com:8080", "proxy: açık"]),
        ("socks5h://user:s3cr3t@10.0.0.2:1080 reddedildi", ["s3cr3t", "user:"], ["10.0.0.2:1080"]),
        (f"sofa_captcha token alındı: {JWT}", [JWT, "eyJhbGci"], ["alındı"]),
        ("Cookie: sofa_captcha=abc123def; cf_clearance=xyz789.qwe", ["abc123def", "xyz789"], ["Cookie"]),
        ("Set-Cookie: sofa_captcha=abc123def; Path=/; HttpOnly", ["abc123def"], ["Set-Cookie"]),
        ("headers={'Cookie': 'sofa_captcha=abc123def', 'X-Captcha': 'tok-98765', 'User-Agent': 'Mozilla/5.0'}",
         ["abc123def", "tok-98765"], ["Mozilla/5.0", "User-Agent"]),
        ('{"Authorization": "Bearer abcdef0123456789", "Accept": "application/json"}', ["abcdef0123456789"],
         ["application/json"]),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", ["dXNlcjpwYXNzd29yZA"], ["Authorization"]),
        ("GET https://api.example.com/v1/x?access_token=tok_a1b2c3&page=2", ["tok_a1b2c3"], ["page=2", "api.example.com"]),
        ('payload {"password": "hunter2", "user": "ali"}', ["hunter2"], ['"user": "ali"']),
        ("api_key=XYZ-123-KEY, retries=3", ["XYZ-123-KEY"], ["retries=3"]),
        ("cf_clearance=0a1b2c3d4e; __cf_bm=ffeeddccbb", ["0a1b2c3d4e", "ffeeddccbb"], []),
    ],
)
def test_patterns_mask_representative_secrets(env_file, text, leaked, kept):
    out = redact.redact_text(text)
    for secret in leaked:
        assert secret not in out, out
    for part in kept:
        assert part in out, out
    assert redact.MASK in out


@pytest.mark.parametrize(
    "text",
    [
        "403 Forbidden (deneme 1/3): https://www.sofascore.com/api/v1/event/15632610/statistics",
        "sofa_captcha cookie'si hâlâ reddediliyor; silinip challenge yeniden çözülüyor",
        "Lig eklendi: Premier League (ID: 17) [bold]17[/bold] [/]",
        "GET https://example.com/search?mail=someone@example.org&x=1",
        "Yapılandırma yöneticisi başlatıldı: 3 lig yüklendi (config/leagues.txt)",
        "",
    ],
)
def test_ordinary_messages_are_untouched(env_file, text):
    assert redact.redact_text(text) == text


# --- bilinen değerler: .env ve ortam -----------------------------------------------------

def test_env_file_secret_is_masked_wherever_it_appears(env_file):
    env_file(f"MAX_CONCURRENT=5\nDATA_DIR=/srv/sofascore-data\nMY_SERVICE_API_KEY={API_KEY}\nDB_PASSWORD='correct horse battery'\n")
    out = redact.redact_text(f"upstream said {API_KEY} is invalid; db login with correct horse battery failed")
    assert API_KEY not in out
    assert "correct horse battery" not in out
    # Gizli olmayan .env değerleri maskelenmez
    assert redact.redact_text("veri dizini /srv/sofascore-data, 5 eşzamanlı istek") == (
        "veri dizini /srv/sofascore-data, 5 eşzamanlı istek"
    )


def test_captcha_token_from_environment_is_masked(env_file, monkeypatch):
    # Docker: token .env'de değil, ortamda (-e SOFA_CAPTCHA_TOKEN=...)
    monkeypatch.setenv("SOFA_CAPTCHA_TOKEN", "opaque-captcha-value-42")
    redact.refresh()
    assert "opaque-captcha-value-42" not in redact.redact_text("X-Captcha gönderildi (opaque-captcha-value-42)")


def test_proxy_password_is_masked_outside_the_url_too(env_file):
    # Parola yüzde-kodlu yazılmış; kütüphaneler hata mesajında çözülmüş halini de basabilir
    env_file("PROXY_URL=http://scraper:p%40ss%21word-9@proxy.example.com:8080\n")
    out = redact.redact_text("407 Proxy Authentication Required for scraper / p@ss!word-9 (p%40ss%21word-9)")
    assert "p@ss!word-9" not in out
    assert "p%40ss%21word-9" not in out


@pytest.mark.parametrize(
    "proxy, secrets, shown",
    [
        # Elle .env'e şemasız yazılmış adres: curl bunu kabul eder, kalıplar ise şema bekler
        ("scraper:Pr0xy-P4ss!word@proxy.example.com:8080", ["Pr0xy-P4ss!word", "scraper:"], "***@proxy.example.com:8080"),
        # Şemasız ve kısa parola: tek başına aranmayacak kadar kısa, adresin içinde yine de tanınır
        ("u:abc@10.0.0.5:1080", ["u:abc", ":abc@"], "***@10.0.0.5:1080"),
        # Parolada '@': host'tan önceki son '@' ayırır
        ("http://scraper:p@ss@proxy.example.com:8080", ["p@ss", "ss@proxy", "scraper:"], "http://***@proxy.example.com:8080"),
        ("http://u:a@b@proxy.example.com:8080", ["a@b", "@b@", "u:a"], "http://***@proxy.example.com:8080"),
        # Parolada ':'
        ("http://scraper:pa:ss:word@proxy.example.com:8080", ["pa:ss:word", "ss:word"], "http://***@proxy.example.com:8080"),
        # Parolasız, yalnızca kullanıcı adı (API anahtarı olarak kullanılan sağlayıcılar var)
        ("http://ApiKey-0123456789@proxy.example.com:8080", ["ApiKey-0123456789"], "http://***@proxy.example.com:8080"),
    ],
)
def test_proxy_credentials_are_masked_however_the_url_is_written(env_file, monkeypatch, proxy, secrets, shown):
    env_file(f"USE_PROXY=true\nPROXY_URL={proxy}\n")
    line = redact.redact_text(f"Proxy/Bağlantı hatası: curl: (7) Failed to connect via {proxy} - proxy: açık")
    setting = redact.mask_value("PROXY_URL", proxy)
    nested = repr(redact.redact_obj({"settings": {"PROXY_URL": proxy}, "log": [f"proxy {proxy} reddetti"]}))
    for secret in secrets:
        assert secret not in line, line
        assert secret not in setting, setting
        assert secret not in nested, nested
    assert setting == shown
    assert shown in line and "proxy: açık" in line


def test_proxy_setting_is_masked_before_it_is_known(env_file):
    # Değer henüz .env'de de ortamda da yok (ayar yazılırken log'a geçen değer): yapısal maskeleme
    assert redact.mask_value("PROXY_URL", "user:pw@10.1.1.1:3128") == "***@10.1.1.1:3128"
    assert redact.mask_value("PROXY_URL", "http://10.1.1.1:3128") == "http://10.1.1.1:3128"
    assert redact.mask_value("PROXY_URL", "http://10.1.1.1:3128/?mail=a@b") == "http://10.1.1.1:3128/?mail=a@b"
    assert redact.mask_url_userinfo("socks5://u:p@h:1/x@y") == "socks5://***@h:1/x@y"


def test_unparseable_env_line_does_not_stop_masking(env_file):
    # python-dotenv bu satırı atlar (ve logging ile uyarır); geri kalan değerler yine maskelenir
    env_file(f"MY_SERVICE_API_KEY={API_KEY}\nthis line has no equals sign\nPROXY_URL={PROXY}\n")
    out = redact.redact_text(f"anahtar {API_KEY}, proxy parolası Pr0xy-P4ss!word")
    assert API_KEY not in out and "Pr0xy-P4ss!word" not in out


def test_env_file_is_parsed_again_only_when_it_changes(env_file, monkeypatch):
    env_file(f"MY_SERVICE_API_KEY={API_KEY}\n")
    calls = []
    real = redact.dotenv.dotenv_values
    monkeypatch.setattr(redact.dotenv, "dotenv_values", lambda *a, **k: calls.append(a) or real(*a, **k))
    monkeypatch.setattr(redact, "_CACHE_SECONDS", -1.0)  # bilinen değerler her çağrıda yeniden toplanır
    for _ in range(5):
        assert API_KEY not in redact.redact_text(f"anahtar {API_KEY}")
    assert len(calls) == 1  # dosya değişmedi: python-dotenv'in "satır ayrıştırılamadı" uyarısı yinelenmez
    env_file("OTHER_SERVICE_TOKEN=second-secret-value\n")
    assert "second-secret-value" not in redact.redact_text("değer second-secret-value")
    assert len(calls) == 2


def test_runtime_change_is_picked_up_after_refresh(env_file, monkeypatch):
    assert redact.redact_text("değer: first-secret-value") == "değer: first-secret-value"
    env_file("UPSTREAM_SECRET=first-secret-value\n")
    assert "first-secret-value" not in redact.redact_text("değer: first-secret-value")


def test_short_values_are_not_searched_as_substrings(env_file):
    # 3 harflik bir "gizli" değer, her satırdaki aynı 3 harfi maskelememeli
    env_file("PIN_TOKEN=abc\n")
    assert redact.redact_text("alfabe abcdef") == "alfabe abcdef"


@pytest.mark.parametrize(
    "key, secret",
    [
        ("SOFA_CAPTCHA_TOKEN", True), ("MY_API_KEY", True), ("DB_PASSWORD", True), ("AWS_SECRET_ACCESS_KEY", True),
        ("SESSION_COOKIE", True), ("PROXY_PASS", True), ("AUTH_HEADER", True), ("GITHUB_TOKEN", True),
        ("DATA_DIR", False), ("MAX_CONCURRENT", False), ("LOG_LEVEL", False), ("PROXY_URL", False),
        ("APP_LANGUAGE", False), ("REQUEST_RATE_LIMIT", False), ("BRIDGE_BLOCKED_AFTER", False),
    ],
)
def test_is_secret_key(key, secret):
    assert redact.is_secret_key(key) is secret


def test_mask_value(env_file):
    assert redact.mask_value("SOFA_CAPTCHA_TOKEN", JWT) == redact.MASK
    assert redact.mask_value("SOFA_CAPTCHA_TOKEN", "") == ""
    assert redact.mask_value("MAX_CONCURRENT", "10") == "10"
    assert redact.mask_value("LOG_LEVEL", None) is None
    # PROXY_URL: sunucu görünür kalır, kimlik bilgisi gider
    assert redact.mask_value("PROXY_URL", PROXY) == "http://***@proxy.example.com:8080"


def test_redact_obj_masks_nested_values(env_file):
    doc = {
        "settings": {"PROXY_URL": PROXY, "SOFA_CAPTCHA_TOKEN": JWT, "MAX_CONCURRENT": "10"},
        "jobs": [{"log": [f"hata: {PROXY}", "tamam"], "matches_done": 3, "ok": True, "result": None}],
        "headers": {"cookie": "sofa_captcha=abc123def"},
    }
    out = redact.redact_obj(doc)
    flat = repr(out)
    for secret in ("Pr0xy-P4ss!word", JWT, "abc123def"):
        assert secret not in flat
    assert out["settings"]["MAX_CONCURRENT"] == "10"
    assert out["jobs"][0]["matches_done"] == 3 and out["jobs"][0]["ok"] is True
    assert out["jobs"][0]["log"][1] == "tamam"
    # Girdi değişmez
    assert doc["settings"]["SOFA_CAPTCHA_TOKEN"] == JWT


def test_a_sink_list_masks_the_url_and_every_option_the_sinks_do_not_know():
    """SOFASCORE_SINKS: `url` yapısal olarak, tanınmayan seçenek (adres taşıyabilir) tümüyle maskelenir (FX-15)."""
    raw = ('[{"type":"webhook","url":"https://u:p@hooks.example.org/x?k=1","webhook_url":"https://h/secret",'
           '"name":"a"}]')
    assert redact.mask_value("SOFASCORE_SINKS", raw) == (
        '[{"type":"webhook","url":"https://***@hooks.example.org/***","webhook_url":"***","name":"a"}]')
    assert redact.mask_value("SOFASCORE_SINKS", "not a list") == redact.MASK
    assert redact.mask_value("SOFASCORE_SINKS", '{"type": "webhook"}') == redact.MASK
