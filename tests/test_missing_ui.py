"""Web arayüzü derlenmemişken: ham JSON yerine iki dilli yardım sayfası; API çalışmaya devam eder."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.web.app as app_module
from src.web.missing_ui import MISSING_UI_HTML, RELEASES_URL

client = TestClient(app_module.app)


@pytest.fixture
def no_build(tmp_path, monkeypatch):
    """frontend/dist yokmuş gibi: uygulama derlemeye her istekte bakar."""
    dist = tmp_path / "frontend" / "dist"
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)
    return dist


@pytest.mark.parametrize("path", ["/", "/leagues", "/matches/9000001", "/favicon.svg"])
def test_help_page_instead_of_json_when_the_build_is_missing(no_build, path):
    r = client.get(path)
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"  # derlemeden sonra yenileme arayüzü göstersin
    assert r.text == MISSING_UI_HTML
    assert "SPA not built" not in r.text


def test_help_page_is_bilingual_and_says_how_to_get_the_ui():
    html = MISSING_UI_HTML
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert '<section lang="en">' in html and '<section lang="tr">' in html
    assert "The web interface is not built" in html and "Web arayüzü derlenmemiş" in html
    assert html.count("npm run build") == 2  # nasıl derlenir: iki dilde
    assert html.count("scripts/start_web.py") == 2  # başlatıcı kendisi derler
    assert html.count(RELEASES_URL) == 2  # derlenmiş arayüz nereden alınır
    assert html.count("20.19") == 2 and html.count("--doctor") == 2
    assert 'href="/docs"' in html and 'href="/health"' in html  # API hâlâ çalışıyor


def test_help_page_is_self_contained():
    """Arayüz yokken dış kaynak, betik ya da derlenmiş dosya gerekmemeli."""
    html = MISSING_UI_HTML.lower()
    for forbidden in ("<script", "<link", "<img", "src=", "@import", "url("):
        assert forbidden not in html, forbidden


def test_api_and_health_keep_working_without_the_build(no_build):
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    leagues = client.get("/api/leagues")
    assert leagues.status_code == 200 and isinstance(leagues.json(), list)
    assert client.get("/api/settings").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    # bilinmeyen API yolu yardım sayfası değil, JSON 404
    missing = client.get("/api/no-such-route")
    assert missing.status_code == 404 and missing.headers["content-type"].startswith("application/json")
    assert client.get("/api").status_code == 404


def test_building_while_the_server_runs_needs_no_restart(no_build):
    assert client.get("/").status_code == 503
    no_build.mkdir(parents=True)
    (no_build / "index.html").write_text("<!doctype html><title>built</title>", encoding="utf-8")
    (no_build / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    root = client.get("/")
    assert root.status_code == 200 and "built" in root.text
    assert client.get("/leagues").status_code == 200  # SPA yönlendirmesi
    assert client.get("/favicon.svg").text == "<svg/>"


def test_dist_without_index_also_gets_the_help_page(no_build):
    no_build.mkdir(parents=True)  # yarıda kalmış derleme: klasör var, index.html yok
    r = client.get("/")
    assert r.status_code == 503 and r.text == MISSING_UI_HTML


def test_path_traversal_is_still_blocked_without_index(no_build, tmp_path):
    no_build.mkdir(parents=True)
    (tmp_path / "frontend" / "secret.txt").write_text("secret", encoding="utf-8")
    r = client.get("/..%2fsecret.txt")
    assert r.text == MISSING_UI_HTML
