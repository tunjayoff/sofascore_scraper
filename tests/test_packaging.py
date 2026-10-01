"""
Paketleme dosyalarının statik kontrolleri (Docker çalıştırılmaz, ağ yok).

İmajın kendisi yayın iş akışında derlenip duman testinden geçer; buradaki testler güvenlikle
ilgili varsayılanların (root olmayan kullanıcı, yalnızca 127.0.0.1'de yayımlanan port, imaja
kullanıcı verisi girmemesi) bir düzenlemede sessizce kaybolmasını önler.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _instructions(dockerfile: str) -> list[str]:
    """Yorumlar atılmış, satır devamları birleştirilmiş Dockerfile talimatları."""
    lines = [ln for ln in dockerfile.splitlines() if not ln.lstrip().startswith("#")]
    joined = re.sub(r"\\\n", " ", "\n".join(lines))
    return [re.sub(r"\s+", " ", ln).strip() for ln in joined.splitlines() if ln.strip()]


def test_dockerfile_runs_as_non_root_with_healthcheck_and_volumes():
    ins = _instructions(_read("Dockerfile"))
    final_stage = ins[max(i for i, ln in enumerate(ins) if ln.startswith("FROM ")):]

    users = [ln.split()[1] for ln in final_stage if ln.startswith("USER ")]
    assert users and users[-1] not in ("root", "0"), "image must not run as root"

    health = [ln for ln in final_stage if ln.startswith("HEALTHCHECK ")]
    assert len(health) == 1 and "/health" in health[0]

    volumes = " ".join(ln for ln in final_stage if ln.startswith("VOLUME "))
    for path in ("/app/data", "/app/config", "/app/logs"):
        assert path in volumes

    # Sürümün tek kaynağı imajda olmalı (src/version.py çalışma anında okur)
    assert any(ln.startswith("COPY ") and "pyproject.toml" in ln for ln in final_stage)
    # Web arayüzü Node aşamasında derlenip kopyalanır; çalışma imajında Node yoktur
    assert any(ln.startswith("COPY --from=frontend") and "frontend/dist" in ln for ln in final_stage)
    assert any(ln.startswith("ENTRYPOINT ") and "sofascore-entrypoint" in ln for ln in final_stage)
    # Paketler CI'ın test ettiği sabit sürümlerle kurulur
    assert any(ln.startswith("COPY ") and "requirements.txt" in ln and "constraints.txt" in ln for ln in final_stage)
    assert any("pip install -r requirements.txt -c constraints.txt" in ln for ln in final_stage)


def test_dockerignore_is_an_allowlist_without_user_state():
    patterns = [
        ln.strip() for ln in _read(".dockerignore").splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    assert patterns[0] == "*", "everything is excluded unless allowed"
    allowed = {p[1:].rstrip("/") for p in patterns if p.startswith("!")}
    for needed in (
        "pyproject.toml", "requirements.txt", "constraints.txt", "main.py", "src", "locales", "frontend", "docker", "LICENSE",
    ):
        assert needed in allowed
    # Kullanıcı durumu ve yerel çıktılar imaja/bağlama girmemeli
    for forbidden in (".env", "data", "config", "config/leagues.txt", "config/league_sports.json", ".git", ".venv"):
        assert forbidden not in allowed
    assert {"frontend/node_modules", "frontend/dist"} <= set(patterns)


def test_entrypoint_is_a_valid_lf_shell_script():
    path = os.path.join(ROOT, "docker", "entrypoint.sh")
    with open(path, "rb") as f:
        raw = f.read()
    assert raw.startswith(b"#!/bin/sh\n")
    assert b"\r" not in raw, "CRLF breaks the shebang inside the container"
    text = raw.decode("utf-8")
    # Web sunucusu main.py --host 0.0.0.0 ile başlatılmaz (o yol 0.0.0.0'ı ağa açılmış sayar:
    # SOFASCORE_ALLOWED_HOSTS ister ve belirteç yoksa uyarır; bkz. docker/entrypoint.sh)
    assert "uvicorn src.web.app:app" in text
    assert "--web" not in [w for ln in text.splitlines() if not ln.lstrip().startswith("#") for w in ln.split()]
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sh yok")
    r = subprocess.run([sh, "-n", path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def test_compose_publishes_on_localhost_only():
    text = "\n".join(ln for ln in _read("docker-compose.yml").splitlines() if not ln.lstrip().startswith("#"))
    ports = re.findall(r'^\s*-\s*"?([0-9.:\[\]a-fA-F]*\d+:\d+)"?\s*(?:#.*)?$', text, flags=re.M)
    assert ports == ["127.0.0.1:8000:8000"], "the web app has no login; the example must not expose it to the network"
    assert "shm_size" in text


def test_compose_keeps_every_image_volume_in_a_named_volume():
    """İmajın VOLUME dizinleri (log dosyası dahil) Compose'da adlandırılmış volume'da durur, anonim volume'da değil."""
    compose = "\n".join(ln for ln in _read("docker-compose.yml").splitlines() if not ln.lstrip().startswith("#"))
    mounts = dict(re.findall(r"^\s*-\s*([a-z][a-z0-9-]*):(/app/[a-z-]+)\b", compose, flags=re.M))
    declared = set(re.findall(r"^  ([a-z][a-z0-9-]*):\s*$", compose.split("\nvolumes:\n", 1)[1], flags=re.M))
    image_volumes = re.findall(r'"(/app/[a-z-]+)"', next(
        ln for ln in _instructions(_read("Dockerfile")) if ln.startswith("VOLUME ")
    ))
    assert sorted(image_volumes) == ["/app/browser-profile", "/app/config", "/app/data", "/app/logs"]
    assert sorted(mounts.values()) == sorted(image_volumes)
    assert set(mounts) == declared

