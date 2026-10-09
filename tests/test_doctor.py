"""
Ortam ön denetimi (sofascore_scraper/doctor.py): her denetim sahte bir kök dizin ve ortamla, tarayıcı denetimi
sahte bir yoklamayla (probe) sınanır. Hiçbir test SofaScore'a bağlanmaz; gerçek tarayıcı yalnızca
`browser` işaretli testte (varsayılan olarak seçilmez) ve yalnızca about:blank ile açılır.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sofascore_scraper import doctor
from sofascore_scraper.doctor import FAIL, OK, WARN, Context

REPO = Path(__file__).resolve().parents[1]
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX izinleri / sembolik bağ")


@pytest.fixture
def make_ctx(tmp_path):
    """Sahte proje kökü + yalıtılmış ortam; gerçek os.environ ve gerçek .env hiç okunmaz."""

    def _make(env_text: str | None = None, environ: dict | None = None, lang: str = "en", **kwargs) -> Context:
        root = tmp_path / "project"
        root.mkdir(exist_ok=True)
        if env_text is not None:
            (root / ".env").write_text(env_text, encoding="utf-8")
        env = {"SOFASCORE_CLIENT__BROWSER_PROFILE": str(tmp_path / "profile")}
        env.update(environ or {})
        kwargs.setdefault("hostname", "thishost")
        kwargs.setdefault("platform", "linux")
        return Context(root=root, environ=env, lang=lang, **kwargs)

    return _make


# --- Python ve paketler -----------------------------------------------------------------------


def test_python_version_ok_and_too_old(make_ctx):
    ok = doctor.check_python(make_ctx(version_info=(3, 10, 0)))
    assert (ok.status, ok.code) == (OK, "python_ok") and ok.fix is None
    old = doctor.check_python(make_ctx(version_info=(3, 9, 18)))
    assert (old.status, old.code) == (FAIL, "python_too_old")
    assert "3.9.18" in old.summary and "3.10" in old.fix


def test_packages_all_importable(make_ctx):
    seen = []
    res = doctor.check_packages(make_ctx(), import_module=seen.append, installed_version=lambda dist: None)
    assert (res.status, res.code) == (OK, "packages_ok")
    assert seen == [module for module, _ in doctor.REQUIRED_MODULES]


def test_packages_missing_names_the_pip_packages_and_the_fix(make_ctx):
    def fake_import(name):
        if name in ("patchright", "dotenv"):
            raise ImportError(f"No module named {name!r}")

    ctx = make_ctx(python="/venv/bin/python")
    res = doctor.check_packages(ctx, import_module=fake_import)
    assert (res.status, res.code) == (FAIL, "packages_missing")
    assert "patchright" in res.summary and "python-dotenv" in res.summary
    assert res.fix_command == ["/venv/bin/python", "-m", "pip", "install", "-r", str(ctx.root / "requirements.txt")]
    assert "pip install -r" in res.fix
    assert set(res.detail["missing"]) == {"patchright", "python-dotenv"}


def test_packages_fix_installs_with_the_constraints_file_when_there_is_one(make_ctx):
    ctx = make_ctx(python="/venv/bin/python")
    (ctx.root / "constraints.txt").write_text("rich==15.0.0\n", encoding="utf-8")
    expected = [
        "/venv/bin/python", "-m", "pip", "install",
        "-r", str(ctx.root / "requirements.txt"), "-c", str(ctx.root / "constraints.txt"),
    ]
    assert doctor.pip_install_command(ctx) == expected

    def nothing_installed(name):
        raise ImportError(name)

    res = doctor.check_packages(ctx, import_module=nothing_installed)
    assert res.fix_command == expected and "constraints.txt" in res.fix
    # Gerçek depo: kısıt dosyası var, doctor'ın önerdiği komut onu kullanır
    assert doctor.pip_install_command(Context(root=REPO, environ={}))[-2:] == ["-c", str(REPO / "constraints.txt")]


def test_packages_broken_install_counts_as_missing(make_ctx):
    def fake_import(name):
        if name == "curl_cffi":
            raise OSError("libstdc++.so.6: cannot open shared object file")

    res = doctor.check_packages(make_ctx(), import_module=fake_import)
    assert res.status == FAIL and "curl_cffi" in res.summary


def test_packages_version_differs_from_pin_is_a_warning(make_ctx):
    ctx = make_ctx()
    (ctx.root / "requirements.txt").write_text(
        "# yorum\n-c constraints.txt\nscrapling[fetchers]==0.4.15\npandas>=2.1.0,<4\n", encoding="utf-8"
    )
    (ctx.root / "constraints.txt").write_text("patchright==1.63.0\nplaywright==1.63.0\n", encoding="utf-8")
    installed = {"scrapling": "0.4.15", "patchright": "1.70.0", "playwright": "1.63.0", "pandas": "3.0.0"}
    res = doctor.check_packages(ctx, import_module=lambda name: None, installed_version=installed.get)
    assert (res.status, res.code) == (WARN, "packages_version_mismatch")
    assert res.detail["mismatch"] == {"patchright": {"installed": "1.70.0", "required": "1.63.0"}}
    assert res.fix_command is None  # sürüm farkı kendiliğinden "düzeltilmez"

    installed["patchright"] = "1.63.0"
    assert doctor.check_packages(ctx, import_module=lambda name: None, installed_version=installed.get).status == OK


def test_required_modules_cover_requirements_txt():
    """requirements.txt'e eklenen paket doctor'ın listesine de girmeli."""
    wanted = set()
    for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        wanted.add(re.split(r"[\[<>=!~ ;]", line, maxsplit=1)[0].lower().replace("_", "-"))
    known = {dist.lower().replace("_", "-") for _, dist in doctor.REQUIRED_MODULES}
    assert wanted and wanted <= known, wanted - known
    # Ters yön de: kaldırılan bir paket (3.0'da terminal menüsüyle colorama ve tqdm, P26) listede kalmamalı.
    # patchright ve playwright scrapling[fetchers] ile gelir: requirements.txt'te ayrı satırları yoktur.
    assert known - wanted == {"patchright", "playwright"}, known - wanted
    assert not {"colorama", "tqdm"} & known


# --- tarayıcı ---------------------------------------------------------------------------------

_VERSIONS = {"scrapling": "0.4.15", "patchright": "1.63.0", "playwright": "1.63.0"}


def test_install_command_targets_the_driver_the_bridge_uses():
    cmd = doctor.browser_install_command("/venv/bin/python")
    assert cmd == ["/venv/bin/python", "-m", "patchright", "install", "chromium", "--no-shell"]
    assert "playwright" not in cmd


def test_browser_ok(make_ctx):
    calls = []

    def probe(python):
        calls.append(python)
        return {
            "stage": "done", "installed": True, "launched": True, "executable": "/cache/chrome",
            "browser_version": "153.0.8010.12", "versions": _VERSIONS,
        }

    res = doctor.check_browser(make_ctx(python="/venv/bin/python"), probe=probe)
    assert (res.status, res.code) == (OK, "browser_ok")
    assert "153.0.8010.12" in res.summary and "1.63.0" in res.summary
    assert calls == ["/venv/bin/python"]  # yoklama, uygulamayı çalıştıracak Python ile yapılır
    assert res.detail["executable"] == "/cache/chrome"


def test_browser_missing_offers_the_patchright_install(make_ctx):
    probe = lambda python: {  # noqa: E731
        "stage": "driver", "installed": False, "launched": False,
        "executable": "/cache/ms-playwright/chromium-1243/chrome-linux64/chrome", "versions": _VERSIONS,
    }
    res = doctor.check_browser(make_ctx(python="/venv/bin/python"), probe=probe)
    assert (res.status, res.code) == (FAIL, "browser_missing")
    assert res.fix_command == doctor.browser_install_command("/venv/bin/python")
    assert "-m patchright install chromium" in res.fix and "playwright install" not in res.fix
    assert "Google Chrome" in res.summary  # kurulu Chrome'un kullanılmadığı açıkça söylenir
    assert "chromium-1243" in res.summary


def test_browser_installed_but_cannot_start_on_linux(make_ctx):
    error = (
        "TargetClosedError: BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
        "Browser logs:\n"
        "chrome: error while loading shared libraries: libnss3.so: cannot open shared object file\n"
    )
    probe = lambda python: {  # noqa: E731
        "stage": "launch", "installed": True, "launched": False, "executable": "/cache/chrome",
        "versions": _VERSIONS, "error": error,
    }
    res = doctor.check_browser(make_ctx(platform="linux"), probe=probe)
    assert (res.status, res.code) == (FAIL, "browser_launch_failed")
    assert "libnss3.so" in res.summary  # asıl neden, ilk satırdaki genel ileti değil
    assert "patchright install-deps chromium" in res.fix
    assert res.fix_command is None  # sudo gerektirir: otomatik çalıştırılmaz
    assert "libnss3.so" in res.detail["error"]


def test_browser_cannot_start_on_other_platforms_suggests_reinstall(make_ctx):
    probe = lambda python: {  # noqa: E731
        "stage": "launch", "installed": True, "launched": False, "executable": "C:/chrome.exe",
        "versions": _VERSIONS, "error": "Error: spawn UNKNOWN",
    }
    res = doctor.check_browser(make_ctx(platform="win32"), probe=probe)
    assert res.code == "browser_launch_failed" and "spawn UNKNOWN" in res.summary
    assert "--force" in res.fix and "install-deps" not in res.fix


def test_browser_timeout(make_ctx):
    res = doctor.check_browser(make_ctx(), probe=lambda python: {"stage": "launch", "timed_out": True, "timeout": 90})
    assert (res.status, res.code) == (FAIL, "browser_launch_timeout") and "90" in res.summary


def test_browser_driver_not_importable_points_at_pip(make_ctx):
    probe = lambda python: {  # noqa: E731
        "stage": "import", "installed": False, "launched": False,
        "error": "ModuleNotFoundError: No module named 'patchright'",
    }
    res = doctor.check_browser(make_ctx(), probe=probe)
    assert (res.status, res.code) == (FAIL, "browser_driver_missing")
    assert "patchright" in res.summary and "pip install -r" in res.fix
    assert res.fix_command is None  # tarayıcı kurmak işe yaramaz: önce paketler


def test_probe_script_is_valid_python_and_never_leaves_about_blank():
    compile(doctor._PROBE_SCRIPT, "<probe>", "exec")
    assert "about:blank" in doctor._PROBE_SCRIPT
    assert "sofascore" not in doctor._PROBE_SCRIPT.lower()
    assert "http" not in doctor._PROBE_SCRIPT.lower()


def test_probe_parses_the_marker_line_and_cleans_its_profile(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        assert os.path.isdir(cmd[3])  # geçici profil, yoklama sürerken var
        payload = {"stage": "done", "installed": True, "launched": True, "browser_version": "1.2.3"}
        out = "some library noise\n\nDOCTOR_PROBE " + json.dumps(payload) + "\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out.encode(), stderr=b"")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    res = doctor.probe_browser("/venv/bin/python", timeout=12)
    assert res["launched"] is True and res["browser_version"] == "1.2.3"
    cmd = seen["cmd"]
    assert cmd[:2] == ["/venv/bin/python", "-c"] and cmd[2] == doctor._PROBE_SCRIPT and cmd[4] == "launch"
    assert seen["kwargs"]["timeout"] == 12
    assert not os.path.exists(cmd[3])  # geçici profil silindi
    assert cmd[3] != os.path.expanduser("~/.cache/sofascore_scraper/chrome_profile")  # gerçek profile dokunulmaz


def test_probe_timeout_and_garbage_output(monkeypatch):
    def timing_out(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(doctor.subprocess, "run", timing_out)
    res = doctor.probe_browser("py", timeout=5)
    assert res["timed_out"] is True and res["launched"] is False

    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"Traceback\nSyntaxError: boom\n"),
    )
    res = doctor.probe_browser("py")
    assert res["launched"] is False and res["stage"] == "import" and "SyntaxError: boom" in res["error"]

    def no_python(cmd, **kwargs):
        raise FileNotFoundError("py")

    monkeypatch.setattr(doctor.subprocess, "run", no_python)
    assert "FileNotFoundError" in doctor.probe_browser("py")["error"]


@pytest.mark.browser
def test_probe_really_starts_the_bridge_browser_on_about_blank():
    """Gerçek tarayıcı (varsayılan olarak seçilmez): yalnızca about:blank, SofaScore'a istek yok."""
    res = doctor.probe_browser(sys.executable)
    assert res.get("installed") and res.get("launched"), res


# --- tarayıcı profili -------------------------------------------------------------------------


def _profile(ctx: Context) -> Path:
    path = ctx.profile_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_profile_missing_dir_is_fine_when_it_can_be_created(make_ctx):
    res = doctor.check_profile(make_ctx())
    assert (res.status, res.code) == (OK, "profile_new")
    assert not Path(res.detail["path"]).exists()  # denetim dizini oluşturmaz


def test_profile_existing_without_lock(make_ctx):
    ctx = make_ctx()
    path = _profile(ctx)
    res = doctor.check_profile(ctx)
    assert (res.status, res.code) == (OK, "profile_ok")
    assert list(path.iterdir()) == []  # yazma denemesinin dosyası geride kalmaz


def test_profile_empty_setting_means_default(make_ctx):
    ctx = make_ctx(environ={"SOFASCORE_CLIENT__BROWSER_PROFILE": ""})
    assert ctx.profile_dir() == Path(os.path.expanduser("~/.cache/sofascore_scraper/chrome_profile"))


def test_profile_setting_from_env_file(tmp_path):
    (tmp_path / ".env").write_text(f"SOFASCORE_CLIENT__BROWSER_PROFILE={tmp_path / 'from-file'}\n", encoding="utf-8")
    ctx = Context(root=tmp_path, environ={}, lang="en")  # süreç ortamında yok: .env'deki değer geçerli
    assert ctx.profile_dir() == tmp_path / "from-file"


@posix_only
def test_profile_locked_by_another_host_fails(make_ctx):
    ctx = make_ctx(hostname="thishost")
    path = _profile(ctx)
    os.symlink("old-container-1234", path / "SingletonLock")
    res = doctor.check_profile(ctx)
    assert (res.status, res.code) == (FAIL, "profile_locked_other_host")
    assert "old-container-1234" in res.summary
    assert str(path / "SingletonLock") in res.fix and "SingletonSocket" in res.fix


@posix_only
def test_profile_lock_of_a_dead_process_is_reported(make_ctx):
    ctx = make_ctx(hostname="thishost")
    path = _profile(ctx)
    os.symlink("thishost-4242", path / "SingletonLock")
    asked = []
    res = doctor.check_profile(ctx, pid_alive=lambda pid: asked.append(pid) or False)
    assert (res.status, res.code) == (WARN, "profile_stale_lock")
    assert asked == [4242] and "4242" in res.summary
    assert str(path / "SingletonLock") in res.fix
    assert res.detail["lock_pid"] == 4242


@posix_only
def test_profile_in_use_by_a_running_browser(make_ctx):
    ctx = make_ctx(hostname="thishost")
    path = _profile(ctx)
    os.symlink("thishost-777", path / "SingletonLock")
    res = doctor.check_profile(
        ctx, pid_alive=lambda pid: True, pid_command=lambda pid: f"/cache/chrome --user-data-dir={path} --headless"
    )
    assert (res.status, res.code) == (WARN, "profile_in_use")
    assert "777" in res.summary and "777" in res.fix


@posix_only
def test_profile_lock_pid_reused_by_another_program_counts_as_stale(make_ctx):
    ctx = make_ctx(hostname="thishost")
    path = _profile(ctx)
    os.symlink("thishost-777", path / "SingletonLock")
    res = doctor.check_profile(ctx, pid_alive=lambda pid: True, pid_command=lambda pid: "/usr/bin/vim notes.txt")
    assert res.code == "profile_stale_lock"
    # komut satırı okunamıyorsa temkinli davranılır: kullanımda sayılır
    res = doctor.check_profile(ctx, pid_alive=lambda pid: True, pid_command=lambda pid: None)
    assert res.code == "profile_in_use"


@posix_only
def test_profile_not_writable_fails(make_ctx):
    if os.geteuid() == 0:
        pytest.skip("root her yere yazar")
    ctx = make_ctx()
    path = _profile(ctx)
    path.chmod(0o500)
    try:
        res = doctor.check_profile(ctx)
    finally:
        path.chmod(0o700)
    assert (res.status, res.code) == (FAIL, "profile_not_writable")
    assert "SOFASCORE_CLIENT__BROWSER_PROFILE" in res.fix


def test_profile_path_is_a_file_fails(make_ctx, tmp_path):
    (tmp_path / "profile").write_text("x")
    res = doctor.check_profile(make_ctx())
    assert (res.status, res.code) == (FAIL, "profile_not_writable")


@posix_only
def test_pid_alive_for_this_process_and_a_dead_one():
    assert doctor._pid_alive(os.getpid()) is True
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert doctor._pid_alive(proc.pid) is False


# --- veri ve yapılandırma dizinleri -----------------------------------------------------------


def test_data_dir_relative_to_project_root_and_created_later(make_ctx):
    ctx = make_ctx(environ={"SOFASCORE_STORAGE__DATA_DIR": "mydata"})
    res = doctor.check_data_dir(ctx)
    assert (res.status, res.code) == (OK, "data_dir_ok")
    assert res.detail["path"] == str(ctx.root / "mydata") and not (ctx.root / "mydata").exists()
    (ctx.root / "mydata").mkdir()
    assert doctor.check_data_dir(ctx).status == OK


def test_data_dir_comes_from_env_file_and_process_env_wins(make_ctx, tmp_path):
    ctx = make_ctx(env_text=f"SOFASCORE_STORAGE__DATA_DIR={tmp_path / 'from-file'}\n")
    assert ctx.data_dir() == tmp_path / "from-file"
    ctx = make_ctx(env_text=f"SOFASCORE_STORAGE__DATA_DIR={tmp_path / 'from-file'}\n", environ={"SOFASCORE_STORAGE__DATA_DIR": str(tmp_path / "from-env")})
    assert ctx.data_dir() == tmp_path / "from-env"


def test_data_dir_comes_from_the_configuration_file_like_ssc_status(make_ctx, tmp_path):
    """F1 (FX-23): `ssc doctor` sofascore.toml'daki storage.data_dir'i görmüyor, ./data gösteriyordu."""
    ctx = make_ctx(env_text="")
    (ctx.root / "sofascore.toml").write_text(f"[storage]\ndata_dir = {json.dumps(str(tmp_path / 'from-toml'))}\n",
                                            encoding="utf-8")
    ctx = make_ctx()
    assert ctx.data_dir() == tmp_path / "from-toml"
    res = doctor.check_data_dir(ctx)
    assert res.detail["path"] == str(tmp_path / "from-toml")
    # Ortam (uygulama `.env`'i ortama yükler) dosyanın önünde (yükleyicinin katman sırası)
    ctx = make_ctx(environ={"SOFASCORE_STORAGE__DATA_DIR": str(tmp_path / "from-env")})
    assert ctx.data_dir() == tmp_path / "from-env"
    assert make_ctx(env_text=f"SOFASCORE_STORAGE__DATA_DIR={tmp_path / 'from-file'}\n").data_dir() == tmp_path / "from-file"
    # Geçersiz bir yapılandırma dosyası: config denetimi bildirir, veri dizini ortamdan ve .env'den
    (ctx.root / "sofascore.toml").write_text("[storage\n", encoding="utf-8")
    assert make_ctx(env_text=f"SOFASCORE_STORAGE__DATA_DIR={tmp_path / 'from-file'}\n").data_dir() == tmp_path / "from-file"


@posix_only
def test_data_and_config_dir_not_writable_fail(make_ctx):
    if os.geteuid() == 0:
        pytest.skip("root her yere yazar")
    ctx = make_ctx()
    for name in ("data", "config"):
        (ctx.root / name).mkdir()
        (ctx.root / name).chmod(0o500)
    try:
        data, config = doctor.check_data_dir(ctx), doctor.check_config_dir(ctx)
    finally:
        for name in ("data", "config"):
            (ctx.root / name).chmod(0o700)
    assert (data.status, data.code) == (FAIL, "data_dir_not_writable") and "SOFASCORE_STORAGE__DATA_DIR" in data.fix
    assert (config.status, config.code) == (FAIL, "config_dir_not_writable") and "leagues.txt" in config.fix


@posix_only
def test_dir_that_cannot_be_created_fails(make_ctx):
    if os.geteuid() == 0:
        pytest.skip("root her yere yazar")
    ctx = make_ctx()
    locked = ctx.root / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        res = doctor.check_data_dir(make_ctx(environ={"SOFASCORE_STORAGE__DATA_DIR": str(locked / "deep" / "data")}))
    finally:
        locked.chmod(0o700)
    assert res.status == FAIL


def test_config_dir_follows_the_override(make_ctx, tmp_path):
    ctx = make_ctx(environ={"SOFASCORE_CONFIG_DIR": str(tmp_path / "cfg")})
    assert doctor.check_config_dir(ctx).detail["path"] == str(tmp_path / "cfg")


# --- yapılandırma dosyası (FX-15) -------------------------------------------------------------


def test_config_without_a_file_is_ok(make_ctx):
    res = doctor.check_config(make_ctx())
    assert (res.status, res.code, res.label) == (OK, "config_none", "Config file")
    assert res.detail == {"path": None, "search_disabled": False}
    assert "sofascore.toml" in res.summary


def test_config_search_turned_off_is_ok(make_ctx):
    res = doctor.check_config(make_ctx(environ={"SOFASCORE_CONFIG": "none"}))
    assert (res.status, res.code) == (OK, "config_none")
    assert res.detail["search_disabled"] is True and "SOFASCORE_CONFIG=none" in res.summary


def test_a_valid_config_file_in_the_project_or_the_config_folder(make_ctx, tmp_path):
    ctx = make_ctx()
    (ctx.root / "sofascore.toml").write_text('[client]\nretries = 2\n', encoding="utf-8")
    res = doctor.check_config(ctx)
    assert (res.status, res.code) == (OK, "config_ok") and res.detail["path"] == str(ctx.root / "sofascore.toml")

    other = make_ctx(environ={"SOFASCORE_CONFIG_DIR": str(tmp_path / "cfg")})
    (ctx.root / "sofascore.toml").unlink()
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "sofascore.toml").write_text("[storage]\ndata_dir = \"data\"\n", encoding="utf-8")
    assert doctor.check_config(other).detail["path"] == str(tmp_path / "cfg" / "sofascore.toml")


@pytest.mark.parametrize("text, fragment", [
    ("[client\nretries = 2\n", "sofascore.toml"),  # TOML olarak okunamaz
    ("[client]\nretries = -1\n", "retries: must be at least 0"),  # aralık dışı
    ("[nope]\nx = 1\n", "nope"),  # bilinmeyen bölüm
])
def test_a_broken_config_file_fails_with_the_loader_message(make_ctx, text, fragment):
    ctx = make_ctx()
    (ctx.root / "sofascore.toml").write_text(text, encoding="utf-8")
    res = doctor.check_config(ctx)
    assert (res.status, res.code) == (FAIL, "config_invalid")
    assert fragment in res.detail["error"] and "ssc config validate" in res.fix


def test_a_named_config_file_that_is_missing_fails(make_ctx, tmp_path):
    res = doctor.check_config(make_ctx(environ={"SOFASCORE_CONFIG": str(tmp_path / "missing.toml")}))
    assert (res.status, res.code) == (FAIL, "config_invalid") and "not found" in res.detail["error"]


def test_a_data_folder_from_the_config_file_that_cannot_be_written_fails(make_ctx, tmp_path):
    ctx = make_ctx()
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    (ctx.root / "sofascore.toml").write_text(f"[storage]\ndata_dir = {json.dumps(str(blocker))}\n", encoding="utf-8")
    res = doctor.check_config(ctx)
    assert (res.status, res.code) == (FAIL, "config_dir_not_writable")
    assert res.detail["directory"] == {"key": "storage.data_dir", "path": str(blocker), "error": "not a directory"}
    assert "storage.data_dir" in res.fix


def test_config_is_not_checked_without_the_packages_of_the_loader(make_ctx, monkeypatch):
    monkeypatch.setitem(sys.modules, "sofascore_scraper.config", None)  # içe aktarma ImportError verir
    res = doctor.check_config(make_ctx())
    assert (res.status, res.code) == (WARN, "config_unchecked") and "Error" in res.detail["error"]


# --- web arayüzü ------------------------------------------------------------------------------


def test_frontend_built(make_ctx):
    ctx = make_ctx()
    (ctx.root / "frontend" / "dist").mkdir(parents=True)
    (ctx.root / "frontend" / "dist" / "index.html").write_text("<html></html>")
    res = doctor.check_frontend(ctx, node_version=lambda: pytest.fail("derleme varken Node aranmaz"))
    assert (res.status, res.code) == (OK, "frontend_ok")


def test_frontend_missing_is_a_warning_with_the_build_command(make_ctx):
    res = doctor.check_frontend(make_ctx(), node_version=lambda: (22, 12, 0))
    assert (res.status, res.code) == (WARN, "frontend_missing")  # komut satırı (ssc) onsuz çalışır: hata değil
    assert "npm install && npm run build" in res.fix and "nodejs.org" not in res.fix
    assert res.detail["node"] == "22.12.0" and res.detail["node_supported"] is True


@pytest.mark.parametrize("version", [None, (18, 20, 0), (20, 18, 9), (22, 11, 0), (21, 7, 0)])
def test_frontend_missing_without_usable_node_says_to_install_node(make_ctx, version):
    res = doctor.check_frontend(make_ctx(), node_version=lambda: version)
    assert res.status == WARN and "nodejs.org" in res.fix and "20.19" in res.fix
    assert res.detail["node_supported"] is False


@pytest.mark.parametrize("version", [(20, 19, 0), (20, 20, 1), (22, 12, 0), (22, 20, 0), (24, 0, 0), (26, 10, 0)])
def test_supported_node_versions(version):
    assert doctor.node_is_supported(version) is True


# --- .env -------------------------------------------------------------------------------------


def test_env_missing_file_is_ok(make_ctx):
    res = doctor.check_env(make_ctx())
    assert (res.status, res.code) == (OK, "env_missing") and res.detail["exists"] is False


def test_env_example_passes_its_own_check(make_ctx):
    """.env.example'ı kopyalayan yeni kurulum uyarısız başlamalı: yalnızca 3.1 adları, hepsi geçerli."""
    ctx = make_ctx(env_text=(REPO / ".env.example").read_text(encoding="utf-8"))
    res = doctor.check_env(ctx)
    assert (res.status, res.code) == (OK, "env_ok"), res.detail
    assert doctor.check_config(ctx).status == OK
    assert set(ctx.file_env) == {"SOFASCORE_CLIENT__CAPTCHA_TOKEN", "SOFASCORE_SERVER__TOKEN"}  # geri kalanı yorum


@pytest.mark.parametrize(
    "line, key",
    [
        ("SOFASCORE_CLIENT__MAX_CONCURRENT=abc", "SOFASCORE_CLIENT__MAX_CONCURRENT"),
        ("SOFASCORE_CLIENT__MAX_CONCURRENT=0", "SOFASCORE_CLIENT__MAX_CONCURRENT"),
        ("SOFASCORE_CLIENT__MAX_CONCURRENT=2.5", "SOFASCORE_CLIENT__MAX_CONCURRENT"),
        ("SOFASCORE_CLIENT__TIMEOUT_SECONDS=-1", "SOFASCORE_CLIENT__TIMEOUT_SECONDS"),
        ("SOFASCORE_CLIENT__WAIT_TIME_MIN=fast", "SOFASCORE_CLIENT__WAIT_TIME_MIN"),
        ("SOFASCORE_BREAKER__RATE_LIMIT_RATIO=1.5", "SOFASCORE_BREAKER__RATE_LIMIT_RATIO"),
        ("SOFASCORE_BRIDGE__DEGRADED_AFTER=0", "SOFASCORE_BRIDGE__DEGRADED_AFTER"),
        ("SOFASCORE_CLIENT__USE_PROXY=maybe", "SOFASCORE_CLIENT__USE_PROXY"),
        ("SOFASCORE_CLIENT__RATE=fast", "SOFASCORE_CLIENT__RATE"),
        ("SOFASCORE_CLIENT__RATEE=1", "SOFASCORE_CLIENT__RATEE"),
    ],
)
def test_env_invalid_value_fails_the_config_check_and_names_the_key(make_ctx, line, key):
    """Değerleri ayar yükleyicisi denetler (`config` denetimi; uygulamanın açılışta vereceği hata)."""
    ctx = make_ctx(env_text=line + "\n")
    res = doctor.check_config(ctx)
    assert (res.status, res.code) == (FAIL, "config_invalid")
    assert key in res.summary
    assert doctor.check_env(ctx).status == OK


@pytest.mark.parametrize(
    "line",
    [
        "SOFASCORE_CLIENT__MAX_CONCURRENT=10", "SOFASCORE_CLIENT__MAX_CONCURRENT=", "SOFASCORE_CLIENT__RETRIES=0",
        "SOFASCORE_CLIENT__WAIT_TIME_MIN=0.2", "SOFASCORE_BREAKER__RATE_LIMIT_RATIO=1", "SOFASCORE_CLIENT__USE_PROXY=False",
        "SOFASCORE_DISPLAY__USE_COLOR=TRUE", "SOFASCORE_CLIENT__RATE=", "SOFASCORE_CLIENT__RATE=off",
        "SOFASCORE_CLIENT__RATE=0", "SOFASCORE_CLIENT__RATE=2.5", "SOFASCORE_REFRESH__WINDOW_HOURS=0",
        "SOFASCORE_DISPLAY__LANGUAGE=en", "SOFASCORE_LOG__LEVEL=debug",
        "SOFASCORE_CLIENT__PROXY=socks5://user:pw@127.0.0.1:1080\nSOFASCORE_CLIENT__USE_PROXY=true",
        "SOFASCORE_CLIENT__BASE_URL=https://www.sofascore.com/api/v1", "SOMETHING_ELSE=whatever",
    ],
)
def test_env_valid_values_pass(make_ctx, line):
    ctx = make_ctx(env_text=line + "\n")
    res = doctor.check_env(ctx)
    assert res.status == OK, res.detail
    assert doctor.check_config(ctx).status == OK


def test_env_proxy_password_never_reaches_the_report(make_ctx):
    secret = "user:hunter2@proxy.example"
    for text in (f"SOFASCORE_CLIENT__PROXY={secret}\n", f"SOFASCORE_CLIENT__USE_PROXY=false\nSOFASCORE_CLIENT__PROXY={secret}\n",
                 f"SOFASCORE_CLIENT__PROXY={secret}\nSOFASCORE_CLIENT__RETRIES=many\n", f"PROXY_URL={secret}\n"):
        ctx = make_ctx(env_text=text)
        for res in (doctor.check_env(ctx), doctor.check_config(ctx)):
            assert "hunter2" not in json.dumps(res.to_dict()), text


@pytest.mark.parametrize(
    "line, name, new",
    [
        ("MAX_CONCURRENT=4", "MAX_CONCURRENT", "SOFASCORE_CLIENT__MAX_CONCURRENT (or client.max_concurrent in the config file)"),
        ("DATA_DIR=data", "DATA_DIR", "SOFASCORE_STORAGE__DATA_DIR (or storage.data_dir in the config file)"),
        ("APP_LANGUAGE=tr", "APP_LANGUAGE", "SOFASCORE_DISPLAY__LANGUAGE (or display.language in the config file)"),
        ("SOFASCORE_API_TOKEN=x", "SOFASCORE_API_TOKEN", "SOFASCORE_SERVER__TOKEN"),
        ("SAVE_EMPTY_ROUNDS=true", "SAVE_EMPTY_ROUNDS", "nothing (the setting was removed)"),
    ],
)
def test_env_legacy_names_are_warnings_that_name_the_new_name(make_ctx, line, name, new):
    """
    3.1 2.x'in adlarını okumaz (plan maddesi P30): kullanıcıya neyi yeniden adlandıracağı ve adın nerede durduğu
    söylenir. Çözüm satırı `.env`'i yalnızca ad oradaysa gösterir (FX-33).
    """
    in_file = make_ctx(env_text=line + "\n")
    in_environ = make_ctx(env_text="", environ=dict([line.split("=", 1)]))  # .env boş (aynı kök)
    for ctx, where, origin in ((in_file, ".env", "env_file"), (in_environ, "the environment", "environment")):
        res = doctor.check_env(ctx)
        assert (res.status, res.code) == (WARN, "env_invalid")
        assert res.summary == f"{name} (set in {where}) is no longer read (the 2.x names were removed in 3.1): use {new}"
        assert [(p["key"], p["origin"]) for p in res.detail["problems"]] == [(name, origin)]
    assert doctor.check_env(in_file).fix.startswith(f"Edit {in_file.env_file} ")
    fix = doctor.check_env(in_environ).fix
    assert fix.startswith(f"Remove or rename {name} where the environment of the app is set") and ".env " not in fix
    # Boş bırakılmış eski ad (2.x'in .env.example'ı böyle kopyalanmış olabilir) sessiz
    assert doctor.check_env(make_ctx(env_text=line.split("=")[0] + "=\n")).status == OK


def test_env_legacy_name_origin_follows_the_loader_rule(make_ctx):
    """
    Uygulama `.env`'i ortama yükler: ortamda `.env`'deki değerle duran ad `.env`'indir; başka bir değerle duran ad
    ortamındır (yükleyicinin `legacy_warnings` kuralı). İkisi birden varsa çözüm ikisini de söyler.
    """
    loaded = doctor.check_env(make_ctx(env_text="DATA_DIR=data\n", environ={"DATA_DIR": "data"}))
    assert [p["origin"] for p in loaded.detail["problems"]] == ["env_file"]
    assert "(set in .env)" in loaded.summary and loaded.fix.startswith("Edit ")

    shadowed = doctor.check_env(make_ctx(env_text="DATA_DIR=data\n", environ={"DATA_DIR": "other"}))
    assert "(set in the environment)" in shadowed.summary and shadowed.fix.startswith("Remove or rename DATA_DIR ")

    ctx = make_ctx(env_text="MAX_CONCURRENT=4\n", environ={"DATA_DIR": "data", "LOG_LEVEL": "debug"})
    both = doctor.check_env(ctx)
    assert {(p["key"], p["origin"]) for p in both.detail["problems"]} == {
        ("MAX_CONCURRENT", "env_file"), ("DATA_DIR", "environment"), ("LOG_LEVEL", "environment"),
    }
    assert both.fix.startswith(f"Edit {ctx.env_file}, and remove or rename ")
    assert "DATA_DIR" in both.fix and "LOG_LEVEL" in both.fix and "MAX_CONCURRENT" not in both.fix

    tr = doctor.check_env(make_ctx(env_text="", environ={"DATA_DIR": "data"}, lang="tr"))
    assert "DATA_DIR (ortamda tanımlı) artık okunmuyor" in tr.summary
    assert tr.fix.startswith("DATA_DIR değişkenini uygulamanın ortamının kurulduğu yerde")


def test_env_soft_problems_are_warnings(make_ctx):
    for line, key in [
        ("SOFASCORE_CLIENT__BASE_URL=http://localhost:9000/api", "SOFASCORE_CLIENT__BASE_URL"),
        ("SOFASCORE_CHROME_PROFILE=/tmp/p", "SOFASCORE_CLIENT__BROWSER_PROFILE"),  # eski, hiç okunmayan ad
    ]:
        res = doctor.check_env(make_ctx(env_text=line + "\n"))
        assert (res.status, res.code) == (WARN, "env_invalid"), line
        assert key in res.summary


def test_env_headed_browser_without_display_warns_only_on_linux(make_ctx):
    headed = "SOFASCORE_CLIENT__BROWSER_HEADED=true\n"
    assert doctor.check_env(make_ctx(env_text=headed, platform="linux")).status == WARN
    with_display = make_ctx(env_text=headed, environ={"DISPLAY": ":0"}, platform="linux")
    assert doctor.check_env(with_display).status == OK
    assert doctor.check_env(make_ctx(env_text=headed, platform="darwin")).status == OK


def test_env_unparsable_line_fails_with_its_line_number(make_ctx):
    res = doctor.check_env(make_ctx(
        env_text="SOFASCORE_STORAGE__DATA_DIR=data\nthis is not a setting\nSOFASCORE_CLIENT__RETRIES=3\n",
    ))
    assert (res.status, res.code) == (FAIL, "env_invalid")
    assert "2" in res.summary and "this is not a setting" in res.summary


def test_env_several_problems_are_all_listed_and_worst_status_wins(make_ctx):
    ctx = make_ctx(env_text="APP_LANGUAGE=de\nnot a setting\nSOFASCORE_CHROME_PROFILE=/x\n")
    res = doctor.check_env(ctx)
    assert res.status == FAIL
    assert {p["key"] for p in res.detail["problems"]} == {"APP_LANGUAGE", "line 2", "SOFASCORE_CHROME_PROFILE"}
    assert {p["status"] for p in res.detail["problems"]} == {WARN, FAIL}
    text = doctor.render_text([res], ctx)
    assert text.count("\n       - ") == 3  # her sorun kendi satırında


def test_env_process_environment_overrides_the_file(make_ctx):
    """Uygulama load_dotenv'i override'sız çağırır: geçerli olan ortamdaki değerdir."""
    name = "SOFASCORE_CLIENT__MAX_CONCURRENT"
    assert doctor.check_config(make_ctx(env_text=f"{name}=abc\n", environ={name: "4"})).status == OK
    assert doctor.check_config(make_ctx(env_text=f"{name}=4\n", environ={name: "abc"})).status == FAIL


def test_env_file_is_read_without_python_dotenv(make_ctx, monkeypatch):
    """Paketler kurulmadan da (dotenv yok) dizinler ve profil .env'den doğru okunmalı."""
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setitem(sys.modules, "dotenv.parser", None)
    text = "# comment\n\nexport DATA_DIR='my data'\nMAX_CONCURRENT=7 # inline\nAPP_LANGUAGE=\"en\"\nnot a setting\n"
    ctx = make_ctx(env_text=text)
    assert ctx.file_env == {"DATA_DIR": "my data", "MAX_CONCURRENT": "7", "APP_LANGUAGE": "en"}
    assert ctx.env_bad_lines == [(6, "not a setting")]


def test_env_file_parsed_like_the_app_does(make_ctx):
    ctx = make_ctx(env_text="# comment\nDATA_DIR='my data'\nMAX_CONCURRENT=7 # inline\n")
    assert ctx.file_env == {"DATA_DIR": "my data", "MAX_CONCURRENT": "7"} and ctx.env_bad_lines == []


def test_env_file_path_follows_the_override(make_ctx, tmp_path):
    other = tmp_path / "elsewhere.env"
    other.write_text("not a setting\n", encoding="utf-8")
    ctx = make_ctx(environ={"SOFASCORE_ENV_FILE": str(other)})
    assert ctx.env_file == other and doctor.check_env(ctx).status == FAIL


# --- çalıştırma, rapor, çıkış kodu -------------------------------------------------------------


def _ok_probe(python):
    return {"stage": "done", "installed": True, "launched": True, "browser_version": "1", "versions": _VERSIONS}


def test_run_checks_returns_every_check_once_and_never_the_live_one(make_ctx, monkeypatch):
    from sofascore_scraper.client import bridge as cs

    monkeypatch.setattr(cs, "fetch_api_via_browser_sync", lambda *a, **k: pytest.fail("canlı istek atılmamalı"))
    results = doctor.run_checks(make_ctx(), browser_probe=_ok_probe)
    assert [r.id for r in results] == list(doctor.CHECK_IDS)
    assert "live" not in doctor.CHECK_IDS
    assert all(r.status in (OK, WARN, FAIL) and r.label and r.summary for r in results)
    assert all(r.fix for r in results if r.status != OK)  # her sorunun tek satırlık çözümü var


def test_run_checks_only_and_skip(make_ctx):
    ctx = make_ctx()
    assert [r.id for r in doctor.run_checks(ctx, only=["python", "env"])] == ["python", "env"]
    skipped = doctor.run_checks(ctx, skip=["browser", "packages"])
    assert "browser" not in [r.id for r in skipped] and "python" in [r.id for r in skipped]


def test_a_crashing_check_does_not_stop_the_others(make_ctx, monkeypatch):
    def boom(ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "CHECKS", (("python", doctor.check_python), ("env", boom), ("data_dir", doctor.check_data_dir)))
    results = doctor.run_checks(make_ctx())
    assert [r.id for r in results] == ["python", "env", "data_dir"]
    assert (results[1].status, results[1].code) == (FAIL, "check_crashed") and "kaboom" in results[1].summary


def test_report_and_exit_code(make_ctx):
    ctx = make_ctx()
    ok = doctor.check_python(ctx)
    warn = doctor.check_frontend(ctx, node_version=lambda: None)
    fail = doctor.check_python(make_ctx(version_info=(3, 8, 0)))

    assert doctor.exit_code([ok]) == 0
    assert doctor.exit_code([ok, warn]) == 0 and doctor.exit_code([ok, warn], strict=True) == 1
    assert doctor.exit_code([ok, warn, fail]) == 1

    rep = doctor.report([ok, warn, fail], ctx)
    assert rep["status"] == FAIL and rep["ok"] is False
    assert rep["counts"] == {"ok": 1, "warn": 1, "fail": 1}
    assert rep["language"] == "en" and rep["root"] == str(ctx.root)
    check = json.loads(json.dumps(rep))["checks"][1]  # JSON'a çevrilebilir
    assert set(check) == {"id", "status", "code", "label", "summary", "fix", "fix_command", "detail"}
    assert doctor.report([ok, warn])["ok"] is True and doctor.report([ok, warn])["status"] == WARN
    assert doctor.report([])["status"] == OK


def test_text_output_in_both_languages(make_ctx):
    results = lambda ctx: [  # noqa: E731
        doctor.check_python(ctx),
        doctor.check_browser(ctx, probe=lambda python: {"stage": "driver", "installed": False, "executable": "/x", "versions": _VERSIONS}),
    ]
    en_ctx, tr_ctx = make_ctx(lang="en"), make_ctx(lang="tr")
    en, tr = doctor.render_text(results(en_ctx), en_ctx), doctor.render_text(results(tr_ctx), tr_ctx)
    assert "[ OK ] Python" in en and "[FAIL] Browser:" in en and "Fix: Run:" in en and "1 failed" in en
    assert "[FAIL] Tarayıcı:" in tr and "Çözüm:" in tr and "1 hata" in tr
    assert "-m patchright install chromium" in en and "-m patchright install chromium" in tr


def test_language_follows_app_language_like_the_app(make_ctx):
    assert make_ctx(lang=None).lang == "en"  # sofascore_scraper/language.py ile aynı varsayılan
    assert make_ctx(lang=None, environ={"SOFASCORE_DISPLAY__LANGUAGE": "tr"}).lang == "tr"
    assert make_ctx(lang=None, environ={"APP_LANGUAGE": "tr"}).lang == "en"  # 2.x adı: 3.1 okumaz
    assert make_ctx(lang=None, environ={"LANGUAGE": "tr_TR:tr"}).lang == "en"  # gettext değişkeni: yok sayılır
    # Açık ayar yoksa sistem dili; .env'deki açık ayar sistem dilinin önündedir
    assert make_ctx(lang=None, environ={"LANG": "tr_TR.UTF-8"}).lang == "tr"
    assert make_ctx(lang=None, environ={"LANG": "de_DE.UTF-8"}).lang == "en"
    assert make_ctx(lang=None, env_text="SOFASCORE_DISPLAY__LANGUAGE=tr\n").lang == "tr"
    assert make_ctx(lang=None, environ={"LANG": "tr_TR.UTF-8"}, env_text="SOFASCORE_DISPLAY__LANGUAGE=en\n").lang == "en"
    assert make_ctx(lang=None, environ={"LANG": "tr_TR.UTF-8"}, env_text="SOFASCORE_DISPLAY__LANGUAGE=\n").lang == "tr"


def test_locale_keys_exist_in_both_languages_and_cover_the_code():
    keys = {}
    for lang in ("tr", "en"):
        with open(REPO / "locales" / f"{lang}.json", encoding="utf-8") as f:
            keys[lang] = {k for k in json.load(f) if k.startswith("doctor_")}
    assert keys["tr"] == keys["en"]
    source = (REPO / "sofascore_scraper" / "doctor.py").read_text(encoding="utf-8")
    used = set(re.findall(r'"(doctor_[a-z_]+)"', source))
    used.discard("doctor_label_")
    used |= {"doctor_label_" + check_id for check_id in doctor.CHECK_IDS + doctor.EXTRA_CHECK_IDS + ("live",)}
    assert used <= keys["en"], used - keys["en"]
    assert keys["en"] <= used, keys["en"] - used  # kullanılmayan çeviri kalmasın


# --- istek bütçesi (yalnızca extra=True ile: yeni CLI) ------------------------------------------


def test_budget_at_or_below_the_default_is_ok(make_ctx):
    res = doctor.check_budget(make_ctx())
    assert (res.status, res.code, res.fix) == (OK, "budget_ok", None)
    assert res.detail == {"rate": 5.0, "default": 5.0, "source": "default"}
    assert res.summary == "5 requests per second (the default is 5)" and res.label == "Request budget"
    for line in ("SOFASCORE_CLIENT__RATE=5", "SOFASCORE_CLIENT__RATE=2.5", "SOFASCORE_CLIENT__RATE="):
        assert doctor.check_budget(make_ctx(env_text=line + "\n")).status == OK, line
    slow = doctor.check_budget(make_ctx(env_text="SOFASCORE_CLIENT__RATE=2.5\n"))
    assert slow.detail == {"rate": 2.5, "default": 5.0, "source": "SOFASCORE_CLIENT__RATE"}


def test_budget_default_is_the_default_of_the_throttle():
    from sofascore_scraper import throttle
    from sofascore_scraper.config.settings import Settings

    assert doctor.DEFAULT_REQUEST_RATE == throttle.DEFAULT_RATE_LIMIT == Settings().client.rate


def test_budget_above_the_default_is_a_warning(make_ctx):
    res = doctor.check_budget(make_ctx(env_text="SOFASCORE_CLIENT__RATE=12\n"))
    assert (res.status, res.code) == (WARN, "budget_above_default")
    assert res.summary.startswith("12 requests per second is above the default of 5")
    assert "SofaScore is more likely to block you" in res.summary
    assert "SOFASCORE_CLIENT__RATE=5" in res.fix and "rate = 5" in res.fix
    assert res.detail == {"rate": 12.0, "default": 5.0, "source": "SOFASCORE_CLIENT__RATE"}
    assert doctor.check_budget(make_ctx(env_text="SOFASCORE_CLIENT__RATE=5.5\n")).code == "budget_above_default"


@pytest.mark.parametrize("value", ["0", "off", "OFF"])
def test_budget_off_is_a_warning(make_ctx, value):
    """0 ve "off" sınırlayıcıyı kapatır (`client.rate`; sofascore_scraper/throttle.configured_rate ile aynı kural)."""
    res = doctor.check_budget(make_ctx(environ={"SOFASCORE_CLIENT__RATE": value}))
    assert (res.status, res.code, res.detail["rate"]) == (WARN, "budget_off", 0.0)
    assert res.summary.startswith("the limit is off") and res.fix


@pytest.mark.parametrize("value", ["fast", "nan", "inf", "false", "-3"])
def test_budget_invalid_value_means_the_default(make_ctx, value):
    # Geçersiz değeri `config` denetimi bildirir (uygulama açılmaz); bütçe denetimi varsayılanı gösterir
    ctx = make_ctx(env_text=f"SOFASCORE_CLIENT__RATE={value}\n")
    if value != "-3":
        assert doctor.check_budget(ctx).detail == {"rate": 5.0, "default": 5.0, "source": "default"}
    assert doctor.check_config(ctx).status == FAIL


def test_budget_matches_what_the_throttle_would_use(make_ctx, monkeypatch):
    from sofascore_scraper import throttle

    for value in ("", "0", "off", "3", "5", "7.5", " 12 "):
        monkeypatch.setenv("SOFASCORE_CLIENT__RATE", value)
        rate = doctor.check_budget(make_ctx(environ={"SOFASCORE_CLIENT__RATE": value})).detail["rate"]
        assert rate == throttle.configured_rate(), value


def test_budget_process_environment_beats_the_file_and_the_2x_name_is_not_read(make_ctx):
    res = doctor.check_budget(make_ctx(env_text="SOFASCORE_CLIENT__RATE=20\n", environ={"SOFASCORE_CLIENT__RATE": "4"}))
    assert (res.status, res.detail["rate"], res.detail["source"]) == (OK, 4.0, "SOFASCORE_CLIENT__RATE")
    res = doctor.check_budget(make_ctx(env_text="", environ={"REQUEST_RATE_LIMIT": "20"}))
    assert (res.status, res.detail["rate"], res.detail["source"]) == (OK, 5.0, "default")


def test_budget_uses_the_rate_the_caller_resolved(make_ctx):
    """Yeni CLI geçerli değeri (yapılandırma dosyası ve bayraklar dahil) kendisi verir."""
    ctx = make_ctx(env_text="SOFASCORE_CLIENT__RATE=2\n", request_rate=20.0, request_rate_source="/etc/sofascore.toml")
    res = doctor.check_budget(ctx)
    assert (res.code, res.detail) == ("budget_above_default", {"rate": 20.0, "default": 5.0, "source": "/etc/sofascore.toml"})
    assert doctor.check_budget(make_ctx(request_rate=0.0)).detail == {"rate": 0.0, "default": 5.0, "source": "settings"}


def test_budget_in_turkish(make_ctx):
    res = doctor.check_budget(make_ctx(lang="tr", environ={"SOFASCORE_CLIENT__RATE": "0"}))
    assert res.label == "İstek bütçesi" and res.summary.startswith("sınır kapalı")


def test_budget_is_a_regular_check(make_ctx, capsys):
    """
    `python main.py --doctor` `ssc doctor`un takma adı olunca (P19) bütçe denetimi her listeye girdi: iki giriş
    noktasının denetimleri aynıdır. `extra=True` eski çağıranlar için durur ve bir şey eklemez.
    """
    assert doctor.CHECK_IDS[-1] == "budget" and doctor.EXTRA_CHECK_IDS == ()
    ctx = make_ctx(environ={"SOFASCORE_CLIENT__RATE": "0"})
    plain = doctor.run_checks(ctx, skip=["browser"])
    assert [r.id for r in plain] == [i for i in doctor.CHECK_IDS if i != "browser"]
    assert plain[-1].code == "budget_off"
    assert [r.id for r in doctor.run_checks(ctx, skip=["browser"], extra=True)] == [r.id for r in plain]
    assert [r.id for r in doctor.run_checks(ctx, only=["budget"])] == ["budget"]
    assert doctor.main(["--only", "budget", "--json"]) == 0  # `python -m sofascore_scraper.doctor` da tanır
    assert json.loads(capsys.readouterr().out)["checks"][0]["code"] == "budget_off"


def test_main_py_doctor_works_without_any_third_party_package(tmp_path):
    """
    `python -S` site-packages'ı kapatır: hiçbir bağımlılık kurulu değilken de `main.py doctor`
    çalışmalı, eksik paketleri bildirmeli ve 1 ile çıkmalı (main.py'nin kendi import'ları çökmeden).
    """
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "SOFASCORE_DISPLAY__LANGUAGE")}
    env_file = tmp_path / ".env"
    env_file.write_text("SOFASCORE_CLIENT__MAX_CONCURRENT=5\nSOFASCORE_DISPLAY__LANGUAGE=tr\n", encoding="utf-8")
    env["SOFASCORE_ENV_FILE"] = str(env_file)
    env["SOFASCORE_STORAGE__DATA_DIR"] = str(tmp_path / "data")
    proc = subprocess.run(
        [sys.executable, "-S", str(REPO / "main.py"), "--json", "doctor", "--only", "python,packages,data_dir,env"],
        cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    assert proc.returncode == 1, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())  # `ssc doctor --json`un zarfı
    assert envelope["command"] == "doctor" and envelope["ok"] is True
    out = envelope["data"]
    by_id = {c["id"]: c for c in out["checks"]}
    assert by_id["python"]["status"] == OK
    assert by_id["packages"]["code"] == "packages_missing" and by_id["packages"]["fix_command"][1:4] == ["-m", "pip", "install"]
    assert by_id["data_dir"]["status"] == OK
    assert by_id["env"]["status"] == OK and out["language"] == "tr"  # .env, dotenv olmadan okundu


def test_main_py_lists_doctor_in_help():
    proc = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "doctor", "--help"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        env={**os.environ, "SOFASCORE_DISPLAY__LANGUAGE": "en"},
    )
    out = proc.stdout.decode()
    assert proc.returncode == 0 and "--json" in out and "--live" in out and "--strict" in out


# --- köprünün hata iletileri ------------------------------------------------------------------


def test_bridge_errors_point_at_the_doctor_not_at_playwright():
    solver = (REPO / "sofascore_scraper" / "client" / "bridge.py").read_text(encoding="utf-8")  # köprü P24 ile taşındı
    health = (REPO / "sofascore_scraper" / "bridge_health.py").read_text(encoding="utf-8")
    assert "playwright install" not in solver and "playwright install" not in health
    # FX-23 (F34): İngilizce ve bugünkü komut, `python main.py --doctor` değil
    assert "ssc doctor" in solver and "ssc doctor" in health
    assert "main.py --doctor" not in solver and "main.py --doctor" not in health


def test_node_version_is_none_when_node_is_not_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert doctor.installed_node_version() is None
