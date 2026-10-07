"""
Uçtan uca: proxy parolası kaydedildikten sonra hiçbir yerden geri okunamamalı.

İki özellik birlikte denenir: proxy ayarları web API'sinden kaydedilir (sofascore_scraper/web/routes/settings.py),
istekler o proxy üzerinden gider ve başarısız olur (hata metninde proxy adresi geçer), sonra
parolanın API yanıtlarında, log dosyasında (sofascore_scraper/logger.py), /api/logs'ta ve tanılama özetinde /
paketinde (sofascore_scraper/diagnostics.py; web ve CLI yolu) bulunmadığı doğrulanır.

Çevrimdışı: yalnızca curl sahtedir; gerçek tarayıcıyı conftest engeller.
"""
from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from unittest.mock import patch
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

import sofascore_scraper.utils as utils
from conftest import LEAGUE_ID
from sofascore_scraper import diagnostics, redact
from sofascore_scraper import logger as app_logger
from sofascore_scraper.paths import env_file_path
from sofascore_scraper.web.app import app
from sofascore_scraper.web.deps import config_manager as _web_config
config_manager = _web_config()

client = TestClient(app)

HOST = "proxy.example:8080"
# Uydurma sınama değerleri (gerçek bir kimlik bilgisi değildir)
SAMPLE_PLAIN = "s3cr3t-Pa55"
SAMPLE_ENCODED = "p%40ss%3AW0rd%21"  # yüzde-kodlu '@', ':' ve '!': kütüphaneler çözülmüş halini de basabilir
SAMPLE_SHORT = "Zq7"  # tek başına aranmayacak kadar kısa: adresin içinde yine de maskelenmeli


@pytest.fixture
def app_log(tmp_path):
    """Uygulamanın gerçek log kurulumu (kök logger + dosya), geçici bir dizine ve DEBUG seviyesinde."""
    keys = ("LOG_DIR", "LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT", "LOG_LEVEL", "DEBUG")
    saved = {k: os.environ.get(k) for k in keys}
    saved_level = logging.getLogger().level
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["LOG_LEVEL"] = "DEBUG"
    for key in ("LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT", "DEBUG"):
        os.environ.pop(key, None)
    app_logger.setup_logger(force=True)
    yield tmp_path / "logs"
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    app_logger.setup_logger(force=True)
    logging.getLogger().setLevel(saved_level)


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    """Test .env dosyasını ve proxy ortam değişkenlerini eski haline döndürür."""
    path = env_file_path()
    with open(path, encoding="utf-8") as f:
        before = f.read()
    monkeypatch.setenv("PROXY_URL", "")
    monkeypatch.setenv("USE_PROXY", "false")
    redact.refresh()
    yield
    with open(path, "w", encoding="utf-8") as f:
        f.write(before)
    monkeypatch.undo()
    redact.refresh()


def _failing_requests_through(proxy_url: str, extra: str = "") -> dict:
    """
    Lig arama, sezon yenileme ve bağlantı testi: curl, bozuk bir proxy'nin vereceği türden bir
    hatayla başarısız olur ve hata metninde proxy adresini (parolasıyla) taşır. Yanıt metinlerini döndürür.
    """
    seen = []

    def curl(url, **kwargs):
        seen.append(kwargs.get("proxies"))
        raise ConnectionError(f"curl: (56) CONNECT tunnel failed, response 407 via {proxy_url}{extra}")

    with patch.object(utils, "_sleep"), patch.object(utils.cffi_requests, "get", side_effect=curl):
        search = client.post("/api/leagues/search-remote", params={"q": "premier"})
        refresh = client.post(f"/api/leagues/{LEAGUE_ID}/seasons/refresh")
    # İstekler gerçekten kayıtlı proxy ile (parolası tam) gönderildi: maskelenen şey kullanılan değer
    assert seen and all(p == {"http": proxy_url, "https": proxy_url} for p in seen), seen
    assert search.status_code == 502 and search.json()["detail"]["reason"] == "network"
    assert refresh.status_code == 502 and refresh.json()["detail"]["reason"] == "network"
    # Bağlantı testi: tarayıcı başlatılamaz (conftest), proxy ayarı tarayıcıya da verilecekti
    test = client.post("/api/bypass/test")
    assert test.status_code == 200 and test.json()["success"] is False
    return {"search": search.text, "refresh": refresh.text, "bypass test": test.text}


def _everything_a_user_could_share(log_dir, tmp_path) -> dict:
    """Parolanın sızabileceği her çıktı: ad → metin."""
    out = {}
    for handler in logging.getLogger().handlers:
        handler.flush()
    names = sorted(p.name for p in log_dir.iterdir() if not p.name.endswith(".lock"))
    assert "sofascore_scraper.log" in names
    for name in names:
        out[f"log file {name}"] = (log_dir / name).read_text(encoding="utf-8", errors="replace")

    out["GET /api/settings"] = client.get("/api/settings").text
    logs = client.get("/api/logs", params={"limit": 2000})
    assert logs.status_code == 200 and logs.json()["count"] > 0
    out["GET /api/logs"] = logs.text
    summary = client.get("/api/diagnostics")
    assert summary.status_code == 200
    out["GET /api/diagnostics"] = summary.text

    bundle = client.get("/api/diagnostics/bundle")
    assert bundle.status_code == 200
    cli_bundle = diagnostics.write_bundle(str(tmp_path / "cli-bundle.zip"), source="cli")
    with open(cli_bundle, "rb") as f:
        cli_bytes = f.read()
    for label, data in (("web bundle", bundle.content), ("cli bundle", cli_bytes)):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert sorted(zf.namelist()) == ["README.txt", "diagnostics.json", "log_tail.txt"]
            for name in zf.namelist():
                out[f"{label} {name}"] = zf.read(name).decode("utf-8")
    return out


def _assert_no_leak(outputs: dict, secrets) -> None:
    for where, text in outputs.items():
        for secret in secrets:
            assert secret not in text, f"proxy secret {secret!r} found in {where}"


@pytest.mark.parametrize("sample", [SAMPLE_PLAIN, SAMPLE_ENCODED, SAMPLE_SHORT])
def test_proxy_password_saved_in_settings_never_reaches_logs_or_diagnostics(app_log, tmp_path, sample):
    url = f"http://scraper:{sample}@{HOST}"
    decoded = unquote(sample)
    secrets = {sample, decoded, f"scraper:{sample}", f"scraper:{decoded}"}
    outputs = {}

    saved = client.post("/api/settings", json={"use_proxy": True, "proxy_url": url})
    assert saved.status_code == 200 and saved.json()["status"] == "success"
    assert os.environ["PROXY_URL"] == url  # tam değer yalnızca .env'de ve süreç ortamında
    outputs["POST /api/settings"] = saved.text

    outputs.update(_failing_requests_through(url, extra=f" (proxy auth failed for scraper:{decoded})"))

    # Form maskeli adresi geri gönderir: parola korunur; başka sunucuya taşınmaz (422)
    masked = client.get("/api/settings").json()["proxy_url"]
    assert masked == f"http://scraper:***@{HOST}"
    kept = client.post("/api/settings", json={"proxy_url": masked, "max_retries": 2})
    assert kept.status_code == 200 and os.environ["PROXY_URL"] == url
    refused = client.post("/api/settings", json={"proxy_url": "http://scraper:***@other.example:8080"})
    assert refused.status_code == 422 and os.environ["PROXY_URL"] == url
    outputs["POST /api/settings (masked)"] = kept.text
    outputs["POST /api/settings (other host)"] = refused.text

    outputs.update(_everything_a_user_could_share(app_log, tmp_path))
    _assert_no_leak(outputs, secrets)

    # Sınama boş değil: ilgili satırlar log'da, maskelenmiş halleriyle
    log_text = outputs["log file sofascore_scraper.log"]
    assert "PROXY_URL=***" in log_text
    assert "Proxy/Bağlantı hatası" in log_text or "İstek hatası" in log_text
    assert f"***@{HOST}" in log_text
    assert f"***@{HOST}" in outputs["web bundle log_tail.txt"]
    for name in ("GET /api/diagnostics", "web bundle diagnostics.json", "cli bundle diagnostics.json"):
        values = json.loads(outputs[name])["settings"]["values"]
        assert values["PROXY_URL"] == f"http://***@{HOST}", name
        assert values["USE_PROXY"] == "true", name


def test_hand_written_proxy_without_a_scheme_never_reaches_logs_or_diagnostics(app_log, tmp_path, monkeypatch):
    # Arayüz şemasız adresi kabul etmez; .env'e elle yazılabilir ve curl bunu kullanır
    url = f"scraper:{SAMPLE_PLAIN}@{HOST}"
    with open(env_file_path(), "a", encoding="utf-8") as f:
        f.write(f"USE_PROXY=true\nPROXY_URL={url}\n")
    monkeypatch.setenv("USE_PROXY", "true")
    monkeypatch.setenv("PROXY_URL", url)
    config_manager.reload_config()  # uygulama açılışında olduğu gibi .env okunur

    outputs = _failing_requests_through(url)
    assert client.get("/api/settings").json()["proxy_url"] == f"scraper:***@{HOST}"
    outputs.update(_everything_a_user_could_share(app_log, tmp_path))
    _assert_no_leak(outputs, {SAMPLE_PLAIN, f"scraper:{SAMPLE_PLAIN}"})

    assert f"***@{HOST}" in outputs["log file sofascore_scraper.log"]
    for name in ("GET /api/diagnostics", "web bundle diagnostics.json", "cli bundle diagnostics.json"):
        assert json.loads(outputs[name])["settings"]["values"]["PROXY_URL"] == f"***@{HOST}", name
