"""
Paketleme dosyalarının statik kontrolleri (Docker çalıştırılmaz, ağ yok).

İmajın kendisi yayın iş akışında derlenip duman testinden geçer; buradaki testler güvenlikle
ilgili varsayılanların (root olmayan kullanıcı, yalnızca 127.0.0.1'de yayımlanan port, imaja
kullanıcı verisi girmemesi) bir düzenlemede sessizce kaybolmasını önler. Giriş noktası (docker/entrypoint.sh)
sahte bir `python` ile gerçekten çalıştırılır: hangi komutu hangi izin listesiyle başlattığı sınanır (P25).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = Path(ROOT)


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

    # Sürümün tek kaynağı imajda olmalı (sofascore_scraper/version.py çalışma anında okur)
    assert any(ln.startswith("COPY ") and "pyproject.toml" in ln for ln in final_stage)
    # Web arayüzü Node aşamasında derlenip kopyalanır; çalışma imajında Node yoktur
    assert any(ln.startswith("COPY --from=frontend") and "frontend/dist" in ln for ln in final_stage)
    assert any(ln.startswith("ENTRYPOINT ") and "sofascore-entrypoint" in ln for ln in final_stage)
    # Paketler CI'ın test ettiği sabit sürümlerle kurulur
    assert any(ln.startswith("COPY ") and "requirements.txt" in ln and "constraints.txt" in ln for ln in final_stage)
    assert any("pip install -r requirements.txt -c constraints.txt" in ln for ln in final_stage)


def test_image_installs_the_package_for_the_ssc_command():
    """
    İmajda `ssc` komutu vardır (REN-1): paket kodu ve pyproject.toml kopyalandıktan sonra proje düzenlenebilir
    kipte, bağımlılıksız kurulur (bağımlılıklar requirements.txt + constraints.txt ile kuruldu).
    """
    ins = _instructions(_read("Dockerfile"))
    final_stage = ins[max(i for i, ln in enumerate(ins) if ln.startswith("FROM ")):]
    scripts = re.search(r"^\[project\.scripts\]\s*\nssc = \"([\w.]+):main\"", _read("pyproject.toml"), flags=re.M)
    assert scripts and scripts.group(1) == "sofascore_scraper.cli.main"

    def index(predicate: Callable[[str], bool]) -> int:
        found = [i for i, ln in enumerate(final_stage) if predicate(ln)]
        assert found, "instruction missing"
        return found[0]

    package = index(lambda ln: ln.startswith("COPY ") and ln.split()[1:] == ["sofascore_scraper/", "./sofascore_scraper/"])
    pyproject = index(lambda ln: ln.startswith("COPY ") and "pyproject.toml" in ln)
    install = index(lambda ln: ln.startswith("RUN ") and "pip install --no-deps -e ." in ln)
    user = index(lambda ln: ln.startswith("USER "))
    assert package < install and pyproject < install, "the project is installed after its files are copied"
    assert install < user, "installed as root, the app user only reads it"


def test_dockerignore_is_an_allowlist_without_user_state():
    patterns = [
        ln.strip() for ln in _read(".dockerignore").splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    assert patterns[0] == "*", "everything is excluded unless allowed"
    allowed = {p[1:].rstrip("/") for p in patterns if p.startswith("!")}
    for needed in (
        "pyproject.toml", "requirements.txt", "constraints.txt", "main.py", "sofascore_scraper", "locales", "frontend", "docker", "LICENSE",
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
    code = [w for ln in text.splitlines() if not ln.lstrip().startswith("#") for w in ln.split()]
    # Web sunucusu `ssc serve` ile başlar (karar D17); doğrudan uvicorn ve eski `--web` yolu yoktur
    assert "sofascore_scraper.cli.main serve" in " ".join(code)
    assert "uvicorn" not in code and "--web" not in code
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sh yok")
    r = subprocess.run([sh, "-n", path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def _compose() -> str:
    return "\n".join(ln for ln in _read("docker-compose.yml").splitlines() if not ln.lstrip().startswith("#"))


def _services(compose: str) -> dict:
    """Servis adı → o servisin metni (yorumsuz). Basit YAML: servisler iki boşlukla girintili."""
    body = compose.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    parts = re.split(r"^  ([a-z][a-z0-9-]*):\s*$", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_compose_publishes_on_localhost_only():
    text = _compose()
    ports = re.findall(r'^\s*-\s*"?([0-9.:\[\]a-fA-F]*\d+:\d+)"?\s*(?:#.*)?$', text, flags=re.M)
    assert ports == ["127.0.0.1:8000:8000"], "the web app has no login; the example must not expose it to the network"
    assert "shm_size" in text


def test_compose_runs_serve_with_an_explicit_allow_list_and_watch_only_on_request():
    """Karar D17: Compose örneği izin listesini açıkça verir. Canlı servis ayrı bir konteynerdir (`--profile live`)."""
    services = _services(_compose())
    assert set(services) == {"sofascore-scraper", "sofascore-watch"}
    web, watch = services["sofascore-scraper"], services["sofascore-watch"]
    assert 'command: ["serve"]' in web
    assert re.search(r'^\s+SOFASCORE_ALLOWED_HOSTS: "localhost,127\.0\.0\.1,\[::1\]"$', web, flags=re.M)
    assert "SOFASCORE_API_TOKEN:" not in web  # yalnızca yorumda: örnek belirteç yayımlanmaz
    # --idle: without a live follow it waits instead of exiting with 2 and being restarted forever
    assert 'command: ["watch", "--idle"]' in watch and 'profiles: ["live"]' in watch
    assert "ports:" not in watch and "disable: true" in watch


def test_compose_keeps_every_image_volume_in_a_named_volume():
    """
    İmajın VOLUME dizinleri (log dosyası dahil) her serviste adlandırılmış volume'da durur, anonim volume'da
    değil. Canlı servis tarayıcı profilini ve log klasörünü sunucuyla paylaşmaz (Chromium profil başına tek süreç
    açar; iki süreç tek log dosyasını döndürmemeli). Canlı sayfaların profili (`/app/browser-profile-live`,
    imajın VOLUME'u değildir) de canlı serviste kendi volume'unda durur: çözülmüş challenge konteyner
    yenilenince kaybolmaz (FX-22).
    """
    compose = _compose()
    declared = set(re.findall(r"^  ([a-z][a-z0-9-]*):\s*$", compose.split("\nvolumes:\n", 1)[1], flags=re.M))
    image_volumes = re.findall(r'"(/app/[a-z-]+)"', next(
        ln for ln in _instructions(_read("Dockerfile")) if ln.startswith("VOLUME ")
    ))
    assert sorted(image_volumes) == ["/app/browser-profile", "/app/config", "/app/data", "/app/logs"]
    used = set()
    per_service = {}
    extra = {"sofascore-scraper": [], "sofascore-watch": ["/app/browser-profile-live"]}
    for name, text in _services(compose).items():
        mounts = {path: volume for volume, path in
                  re.findall(r"^\s*-\s*([a-z][a-z0-9-]*):(/app/[a-z-]+)\b", text, flags=re.M)}
        assert sorted(mounts) == sorted(image_volumes + extra[name]), name
        per_service[name] = mounts
        used |= set(mounts.values())
    assert used == declared
    web, watch = per_service["sofascore-scraper"], per_service["sofascore-watch"]
    assert web["/app/data"] == watch["/app/data"] and web["/app/config"] == watch["/app/config"]
    assert web["/app/browser-profile"] != watch["/app/browser-profile"]
    assert web["/app/logs"] != watch["/app/logs"]
    assert watch["/app/browser-profile-live"] not in web.values()


def test_image_uses_the_new_setting_name_for_the_browser_profile_and_serve_by_default():
    final_stage = _instructions(_read("Dockerfile"))
    env = " ".join(ln for ln in final_stage if ln.startswith("ENV "))
    # P09: yeni ad ayardır (yapılandırma dosyası varken eski ad tek başına "legacy name" uyarısı verirdi); eski ad
    # ayarları yüklemeden ortamı okuyanlar (doctor) için aynı değerle durur
    assert "SOFASCORE_CLIENT__BROWSER_PROFILE=/app/browser-profile" in env
    assert "SOFASCORE_BROWSER_PROFILE=/app/browser-profile" in env
    assert final_stage[-1] == 'CMD ["serve"]'
    # `ssc watch`un kendi profili (<profil>-live) uygulama kullanıcısına ait bir dizindedir
    assert any("/app/browser-profile-live" in ln and "chown app:app" in ln for ln in final_stage)



# --- Docker giriş noktası ------------------------------------------------------------------------------


def _entrypoint(tmp_path: Path, *args: str, env: Optional[Dict[str, str]] = None,
                files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """docker/entrypoint.sh'i sahte bir `python` ile çalıştırır: hangi komutun hangi ortamla başlatıldığı."""
    sh = shutil.which("sh")
    if sh is None or os.name == "nt":
        pytest.skip("needs a POSIX sh")
    app = tmp_path / "app"
    bin_dir = tmp_path / "bin"
    for directory in (app, bin_dir):
        directory.mkdir(exist_ok=True)
    for name, text in (files or {}).items():
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        (app / name).write_text(text, encoding="utf-8")
    record = tmp_path / "record.json"
    fake = bin_dir / "python"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['ENTRYPOINT_RECORD'], 'w') as f:\n"
        "    json.dump({'argv': sys.argv[1:], 'hosts': os.environ.get('SOFASCORE_SERVER__ALLOWED_HOSTS')}, f)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    base = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "APP_HOME": str(app),
            "SOFASCORE_ENV_FILE": str(app / "config" / ".env"), "ENTRYPOINT_RECORD": str(record)}
    proc = subprocess.run([sh, str(REPO / "docker" / "entrypoint.sh"), *args], env={**base, **(env or {})},
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(record.read_text(encoding="utf-8"))


def test_entrypoint_starts_serve_on_every_interface_with_the_loopback_names(tmp_path: Path) -> None:
    started = _entrypoint(tmp_path)
    assert started["argv"] == ["-m", "sofascore_scraper.cli.main", "serve", "--host", "0.0.0.0", "--port", "8000"]
    assert started["hosts"] == "localhost,127.0.0.1,[::1]"
    # Sonraki seçenekler serve'e geçer; HOST ve PORT ortamdan
    started = _entrypoint(tmp_path, "serve", "--dev", env={"PORT": "9000", "HOST": "::"})
    assert started["argv"] == ["-m", "sofascore_scraper.cli.main", "serve", "--host", "::", "--port", "9000", "--dev"]
    # 2.x'in `web` takma adı 3.1'de kalktı (P30): CLI'ye bilinmeyen bir komut olarak gider (kullanım hatası)
    assert _entrypoint(tmp_path, "web")["argv"] == ["-m", "sofascore_scraper.cli.main", "web"]


@pytest.mark.parametrize("env,files", [
    ({"SOFASCORE_ALLOWED_HOSTS": "box.lan"}, {}),
    ({"SOFASCORE_SERVER__ALLOWED_HOSTS": "box.lan"}, {}),
    ({}, {"config/.env": "SOFASCORE_ALLOWED_HOSTS=box.lan\n"}),
    ({}, {"sofascore.toml": "schema = 1\n"}),
    ({}, {"config/sofascore.toml": "schema = 1\n"}),
    ({"SOFASCORE_CONFIG": "/somewhere/sofascore.toml"}, {}),
])
def test_entrypoint_leaves_an_allow_list_given_anywhere_alone(tmp_path: Path, env: Dict[str, str],
                                                              files: Dict[str, str]) -> None:
    started = _entrypoint(tmp_path, env=env, files=files)
    assert started["hosts"] == env.get("SOFASCORE_SERVER__ALLOWED_HOSTS")


def test_entrypoint_gives_the_loopback_names_when_the_config_search_is_off(tmp_path: Path) -> None:
    started = _entrypoint(tmp_path, env={"SOFASCORE_CONFIG": "none"}, files={"sofascore.toml": "schema = 1\n"})
    assert started["hosts"] == "localhost,127.0.0.1,[::1]"


def test_entrypoint_passes_everything_else_to_the_cli(tmp_path: Path) -> None:
    assert _entrypoint(tmp_path, "--version")["argv"] == ["-m", "sofascore_scraper.cli.main", "--version"]
    started = _entrypoint(tmp_path, "watch", "--source", "poll")
    assert started["argv"] == ["-m", "sofascore_scraper.cli.main", "watch", "--source", "poll"]
    assert started["hosts"] is None


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock yok")
def test_entrypoint_clears_a_stale_chromium_lock_of_the_live_profile_for_watch(tmp_path: Path) -> None:
    """
    Compose örneği canlı sayfaların profilini (<profil>-live) bir volume'da tutar: yenilenen konteynerde orada da
    eski konteynerin bayat SingletonLock'u kalır ve Chromium açılmaz. `watch` iki profilin de bayat kilidini
    siler; başka komutlar canlı profile dokunmaz.
    """
    if os.name == "nt":
        pytest.skip("needs a POSIX sh")
    profile = tmp_path / "profile"
    live = tmp_path / "profile-live"
    env = {"SOFASCORE_CLIENT__BROWSER_PROFILE": str(profile)}

    def stale() -> None:
        for directory in (profile, live):
            directory.mkdir(exist_ok=True)
            (directory / "SingletonLock").unlink(missing_ok=True)
            os.symlink("old-container-1234", directory / "SingletonLock")

    stale()
    _entrypoint(tmp_path, "sync", env=env)
    assert not (profile / "SingletonLock").is_symlink() and (live / "SingletonLock").is_symlink()

    stale()
    _entrypoint(tmp_path, "watch", env=env)
    assert not (profile / "SingletonLock").is_symlink() and not (live / "SingletonLock").is_symlink()
