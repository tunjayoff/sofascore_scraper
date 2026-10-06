"""
Ortam ön denetimi (src/doctor.py): her denetim sahte bir kök dizin ve ortamla, tarayıcı denetimi
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

from src import doctor
from src.doctor import FAIL, OK, WARN, Context

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
        env = {"SOFASCORE_BROWSER_PROFILE": str(tmp_path / "profile")}
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
    ctx = make_ctx(environ={"SOFASCORE_BROWSER_PROFILE": ""})
    assert ctx.profile_dir() == Path(os.path.expanduser("~/.cache/sofascore_scraper/chrome_profile"))


def test_profile_setting_from_env_file(tmp_path):
    (tmp_path / ".env").write_text(f"SOFASCORE_BROWSER_PROFILE={tmp_path / 'from-file'}\n", encoding="utf-8")
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
    assert "SOFASCORE_BROWSER_PROFILE" in res.fix


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
    ctx = make_ctx(environ={"DATA_DIR": "mydata"})
    res = doctor.check_data_dir(ctx)
    assert (res.status, res.code) == (OK, "data_dir_ok")
    assert res.detail["path"] == str(ctx.root / "mydata") and not (ctx.root / "mydata").exists()
    (ctx.root / "mydata").mkdir()
    assert doctor.check_data_dir(ctx).status == OK


def test_data_dir_comes_from_env_file_and_process_env_wins(make_ctx, tmp_path):
    ctx = make_ctx(env_text=f"DATA_DIR={tmp_path / 'from-file'}\n")
    assert ctx.data_dir() == tmp_path / "from-file"
    ctx = make_ctx(env_text=f"DATA_DIR={tmp_path / 'from-file'}\n", environ={"DATA_DIR": str(tmp_path / "from-env")})
    assert ctx.data_dir() == tmp_path / "from-env"


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
    assert (data.status, data.code) == (FAIL, "data_dir_not_writable") and "DATA_DIR" in data.fix
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
        res = doctor.check_data_dir(make_ctx(environ={"DATA_DIR": str(locked / "deep" / "data")}))
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
    monkeypatch.setitem(sys.modules, "src.config", None)  # içe aktarma ImportError verir
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
    assert (res.status, res.code) == (WARN, "frontend_missing")  # terminal modları onsuz çalışır: hata değil
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
    """.env.example'ı kopyalayan yeni kurulum uyarısız başlamalı."""
    ctx = make_ctx(env_text=(REPO / ".env.example").read_text(encoding="utf-8"))
    res = doctor.check_env(ctx)
    assert (res.status, res.code) == (OK, "env_ok"), res.detail
    assert "SOFASCORE_BROWSER_PROFILE" in ctx.file_env and "SOFASCORE_CHROME_PROFILE" not in ctx.file_env


@pytest.mark.parametrize(
    "line, key",
    [
        ("MAX_CONCURRENT=abc", "MAX_CONCURRENT"),
        ("MAX_CONCURRENT=0", "MAX_CONCURRENT"),
        ("MAX_CONCURRENT=2.5", "MAX_CONCURRENT"),
        ("REQUEST_TIMEOUT=-1", "REQUEST_TIMEOUT"),
        ("WAIT_TIME_MIN=fast", "WAIT_TIME_MIN"),
        ("RATE_LIMIT_THRESHOLD_RATIO=1.5", "RATE_LIMIT_THRESHOLD_RATIO"),
        ("RATE_LIMIT_THRESHOLD_RATIO=0", "RATE_LIMIT_THRESHOLD_RATIO"),
        ("REFRESH_WINDOW_HOURS=nan", "REFRESH_WINDOW_HOURS"),
        ("BRIDGE_DEGRADED_AFTER=0", "BRIDGE_DEGRADED_AFTER"),
        ("USE_PROXY=1", "USE_PROXY"),
        ("FETCH_ONLY_FINISHED=yes", "FETCH_ONLY_FINISHED"),
        ("REQUEST_RATE_LIMIT=fast", "REQUEST_RATE_LIMIT"),
    ],
)
def test_env_invalid_value_fails_and_names_the_key(make_ctx, line, key):
    res = doctor.check_env(make_ctx(env_text=line + "\n"))
    assert (res.status, res.code) == (FAIL, "env_invalid")
    assert key in res.summary and ".env" in res.fix
    assert [p["key"] for p in res.detail["problems"]] == [key]


@pytest.mark.parametrize(
    "line",
    [
        "MAX_CONCURRENT=10", "MAX_CONCURRENT=", "MAX_RETRIES=0", "WAIT_TIME_MIN=0.2", "RATE_LIMIT_THRESHOLD_RATIO=1",
        "USE_PROXY=False", "USE_COLOR=TRUE", "REQUEST_RATE_LIMIT=", "REQUEST_RATE_LIMIT=off", "REQUEST_RATE_LIMIT=0",
        "REQUEST_RATE_LIMIT=2.5", "REFRESH_WINDOW_HOURS=0", "APP_LANGUAGE=en", "LOG_LEVEL=debug",
        "PROXY_URL=socks5://user:pw@127.0.0.1:1080\nUSE_PROXY=true", "API_BASE_URL=https://www.sofascore.com/api/v1",
        "SOMETHING_ELSE=whatever",
    ],
)
def test_env_valid_values_pass(make_ctx, line):
    res = doctor.check_env(make_ctx(env_text=line + "\n"))
    assert res.status == OK, res.detail


def test_env_proxy_enabled_without_url_fails(make_ctx):
    res = doctor.check_env(make_ctx(env_text="USE_PROXY=true\nPROXY_URL=\n"))
    assert res.status == FAIL and "PROXY_URL" in res.summary


def test_env_bad_proxy_url_fails_without_leaking_it(make_ctx):
    secret = "user:hunter2@proxy.example"
    res = doctor.check_env(make_ctx(env_text=f"USE_PROXY=true\nPROXY_URL={secret}\n"))
    assert res.status == FAIL
    assert "hunter2" not in json.dumps(res.to_dict())
    # proxy kapalıyken aynı değer yalnızca uyarıdır
    off = doctor.check_env(make_ctx(env_text=f"USE_PROXY=false\nPROXY_URL={secret}\n"))
    assert off.status == WARN and "hunter2" not in json.dumps(off.to_dict())


def test_env_soft_problems_are_warnings(make_ctx):
    for line, key in [
        ("APP_LANGUAGE=de", "APP_LANGUAGE"),
        ("LOG_LEVEL=LOUD", "LOG_LEVEL"),
        ("API_BASE_URL=http://localhost:9000/api", "API_BASE_URL"),
        ("SOFASCORE_CHROME_PROFILE=/tmp/p", "SOFASCORE_BROWSER_PROFILE"),  # eski, hiç okunmayan ad
    ]:
        res = doctor.check_env(make_ctx(env_text=line + "\n"))
        assert (res.status, res.code) == (WARN, "env_invalid"), line
        assert key in res.summary


def test_env_headed_browser_without_display_warns_only_on_linux(make_ctx):
    assert doctor.check_env(make_ctx(env_text="SOFASCORE_BROWSER_HEADED=1\n", platform="linux")).status == WARN
    with_display = make_ctx(env_text="SOFASCORE_BROWSER_HEADED=1\n", environ={"DISPLAY": ":0"}, platform="linux")
    assert doctor.check_env(with_display).status == OK
    assert doctor.check_env(make_ctx(env_text="SOFASCORE_BROWSER_HEADED=1\n", platform="darwin")).status == OK


def test_env_unparsable_line_fails_with_its_line_number(make_ctx):
    res = doctor.check_env(make_ctx(env_text="DATA_DIR=data\nthis is not a setting\nMAX_RETRIES=3\n"))
    assert (res.status, res.code) == (FAIL, "env_invalid")
    assert "2" in res.summary and "this is not a setting" in res.summary


def test_env_several_problems_are_all_listed_and_worst_status_wins(make_ctx):
    ctx = make_ctx(env_text="APP_LANGUAGE=de\nMAX_CONCURRENT=abc\nUSE_COLOR=maybe\n")
    res = doctor.check_env(ctx)
    assert res.status == FAIL
    assert {p["key"] for p in res.detail["problems"]} == {"APP_LANGUAGE", "MAX_CONCURRENT", "USE_COLOR"}
    assert {p["status"] for p in res.detail["problems"]} == {WARN, FAIL}
    text = doctor.render_text([res], ctx)
    assert text.count("\n       - ") == 3  # her sorun kendi satırında


def test_env_process_environment_overrides_the_file(make_ctx):
    """Uygulama load_dotenv'i override'sız çağırır: geçerli olan ortamdaki değerdir."""
    res = doctor.check_env(make_ctx(env_text="MAX_CONCURRENT=abc\n", environ={"MAX_CONCURRENT": "4"}))
    assert res.status == OK
    res = doctor.check_env(make_ctx(env_text="MAX_CONCURRENT=4\n", environ={"MAX_CONCURRENT": "abc"}))
    assert res.status == FAIL


@posix_only
def test_env_file_read_only_is_a_warning(make_ctx):
    if os.geteuid() == 0:
        pytest.skip("root her yere yazar")
    ctx = make_ctx(env_text="MAX_RETRIES=3\n")
    ctx.env_file.chmod(0o400)
    try:
        res = doctor.check_env(ctx)
    finally:
        ctx.env_file.chmod(0o600)
    assert res.status == WARN and "cannot be saved" in res.summary


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
    other.write_text("MAX_CONCURRENT=abc\n", encoding="utf-8")
    ctx = make_ctx(environ={"SOFASCORE_ENV_FILE": str(other)})
    assert ctx.env_file == other and doctor.check_env(ctx).status == FAIL


# --- çalıştırma, rapor, çıkış kodu -------------------------------------------------------------


def _ok_probe(python):
    return {"stage": "done", "installed": True, "launched": True, "browser_version": "1", "versions": _VERSIONS}


def test_run_checks_returns_every_check_once_and_never_the_live_one(make_ctx, monkeypatch):
    import src.challenge_solver as cs

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
    assert make_ctx(lang=None).lang == "en"  # src/language.py ile aynı varsayılan
    assert make_ctx(lang=None, environ={"APP_LANGUAGE": "tr"}).lang == "tr"
    assert make_ctx(lang=None, environ={"LANGUAGE": "tr_TR:tr"}).lang == "en"  # gettext değişkeni: yok sayılır
    # Açık ayar yoksa sistem dili; .env'deki açık ayar sistem dilinin önündedir
    assert make_ctx(lang=None, environ={"LANG": "tr_TR.UTF-8"}).lang == "tr"
    assert make_ctx(lang=None, environ={"LANG": "de_DE.UTF-8"}).lang == "en"
    assert make_ctx(lang=None, env_text="APP_LANGUAGE=tr\n").lang == "tr"
    assert make_ctx(lang=None, environ={"LANG": "tr_TR.UTF-8"}, env_text="APP_LANGUAGE=en\n").lang == "en"
    assert make_ctx(lang=None, environ={"LANG": "tr_TR.UTF-8"}, env_text="APP_LANGUAGE=\n").lang == "tr"


def test_locale_keys_exist_in_both_languages_and_cover_the_code():
    keys = {}
    for lang in ("tr", "en"):
        with open(REPO / "locales" / f"{lang}.json", encoding="utf-8") as f:
            keys[lang] = {k for k in json.load(f) if k.startswith("doctor_")}
    assert keys["tr"] == keys["en"]
    source = (REPO / "src" / "doctor.py").read_text(encoding="utf-8")
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
    for line in ("REQUEST_RATE_LIMIT=5", "REQUEST_RATE_LIMIT=2.5", "REQUEST_RATE_LIMIT="):
        assert doctor.check_budget(make_ctx(env_text=line + "\n")).status == OK, line
    slow = doctor.check_budget(make_ctx(env_text="REQUEST_RATE_LIMIT=2.5\n"))
    assert slow.detail == {"rate": 2.5, "default": 5.0, "source": "REQUEST_RATE_LIMIT"}


def test_budget_default_is_the_default_of_the_throttle():
    from src import throttle

    assert doctor.DEFAULT_REQUEST_RATE == throttle.DEFAULT_RATE_LIMIT
    assert doctor._RATE_OFF_WORDS == throttle._OFF_WORDS


def test_budget_above_the_default_is_a_warning(make_ctx):
    res = doctor.check_budget(make_ctx(env_text="REQUEST_RATE_LIMIT=12\n"))
    assert (res.status, res.code) == (WARN, "budget_above_default")
    assert res.summary.startswith("12 requests per second is above the default of 5")
    assert "SofaScore is more likely to block you" in res.summary
    assert "REQUEST_RATE_LIMIT=5" in res.fix and "rate = 5" in res.fix
    assert res.detail == {"rate": 12.0, "default": 5.0, "source": "REQUEST_RATE_LIMIT"}
    assert doctor.check_budget(make_ctx(env_text="REQUEST_RATE_LIMIT=5.5\n")).code == "budget_above_default"


@pytest.mark.parametrize("value", ["0", "off", "OFF", "false", "none", "disabled", "-3"])
def test_budget_off_is_a_warning(make_ctx, value):
    """0, kapatma sözcükleri ve negatif sayı sınırlayıcıyı kapatır (src/throttle.configured_rate ile aynı kural)."""
    res = doctor.check_budget(make_ctx(environ={"REQUEST_RATE_LIMIT": value}))
    assert (res.status, res.code, res.detail["rate"]) == (WARN, "budget_off", 0.0)
    assert res.summary.startswith("the limit is off") and res.fix


@pytest.mark.parametrize("value", ["fast", "nan", "inf"])
def test_budget_invalid_value_means_the_default(make_ctx, value):
    # Geçersiz değeri `env` denetimi bildirir; uygulama varsayılanı kullanır
    ctx = make_ctx(env_text=f"REQUEST_RATE_LIMIT={value}\n")
    assert doctor.check_budget(ctx).detail == {"rate": 5.0, "default": 5.0, "source": "default"}
    assert doctor.check_env(ctx).status == FAIL


def test_budget_matches_what_the_throttle_would_use(make_ctx, monkeypatch):
    from src import throttle

    for value in ("", "0", "off", "no", "3", "5", "7.5", "-1", "fast", "inf", " 12 "):
        monkeypatch.setenv("REQUEST_RATE_LIMIT", value)
        rate = doctor.check_budget(make_ctx(environ={"REQUEST_RATE_LIMIT": value})).detail["rate"]
        assert rate == throttle.configured_rate(), value


def test_budget_new_name_wins_and_process_environment_beats_the_file(make_ctx):
    res = doctor.check_budget(make_ctx(environ={"SOFASCORE_CLIENT__RATE": "3", "REQUEST_RATE_LIMIT": "20"}))
    assert (res.status, res.detail["rate"], res.detail["source"]) == (OK, 3.0, "SOFASCORE_CLIENT__RATE")
    res = doctor.check_budget(make_ctx(env_text="REQUEST_RATE_LIMIT=20\n", environ={"REQUEST_RATE_LIMIT": "4"}))
    assert (res.status, res.detail["rate"]) == (OK, 4.0)


def test_budget_uses_the_rate_the_caller_resolved(make_ctx):
    """Yeni CLI geçerli değeri (yapılandırma dosyası ve bayraklar dahil) kendisi verir."""
    ctx = make_ctx(env_text="REQUEST_RATE_LIMIT=2\n", request_rate=20.0, request_rate_source="/etc/sofascore.toml")
    res = doctor.check_budget(ctx)
    assert (res.code, res.detail) == ("budget_above_default", {"rate": 20.0, "default": 5.0, "source": "/etc/sofascore.toml"})
    assert doctor.check_budget(make_ctx(request_rate=0.0)).detail == {"rate": 0.0, "default": 5.0, "source": "settings"}


def test_budget_in_turkish(make_ctx):
    res = doctor.check_budget(make_ctx(lang="tr", environ={"REQUEST_RATE_LIMIT": "0"}))
    assert res.label == "İstek bütçesi" and res.summary.startswith("sınır kapalı")


def test_budget_is_a_regular_check(make_ctx, capsys):
    """
    `python main.py --doctor` `ssc doctor`un takma adı olunca (P19) bütçe denetimi her listeye girdi: iki giriş
    noktasının denetimleri aynıdır. `extra=True` eski çağıranlar için durur ve bir şey eklemez.
    """
    assert doctor.CHECK_IDS[-1] == "budget" and doctor.EXTRA_CHECK_IDS == ()
    ctx = make_ctx(environ={"REQUEST_RATE_LIMIT": "0"})
    plain = doctor.run_checks(ctx, skip=["browser"])
    assert [r.id for r in plain] == [i for i in doctor.CHECK_IDS if i != "browser"]
    assert plain[-1].code == "budget_off"
    assert [r.id for r in doctor.run_checks(ctx, skip=["browser"], extra=True)] == [r.id for r in plain]
    assert [r.id for r in doctor.run_checks(ctx, only=["budget"])] == ["budget"]
    assert doctor.main(["--only", "budget", "--json"]) == 0  # `python -m src.doctor` da tanır
    assert json.loads(capsys.readouterr().out)["checks"][0]["code"] == "budget_off"


def test_main_py_doctor_works_without_any_third_party_package(tmp_path):
    """
    `python -S` site-packages'ı kapatır: hiçbir bağımlılık kurulu değilken de `main.py --doctor`
    çalışmalı, eksik paketleri bildirmeli ve 1 ile çıkmalı (main.py'nin kendi import'ları çökmeden).
    """
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "APP_LANGUAGE")}
    env_file = tmp_path / ".env"
    env_file.write_text("MAX_CONCURRENT=5\nAPP_LANGUAGE=tr\n", encoding="utf-8")
    env["SOFASCORE_ENV_FILE"] = str(env_file)
    env["DATA_DIR"] = str(tmp_path / "data")
    proc = subprocess.run(
        [sys.executable, "-S", str(REPO / "main.py"), "--doctor", "--json", "--only", "python,packages,data_dir,env"],
        cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    assert proc.returncode == 1, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())  # `ssc doctor --json`un zarfı (P19: --doctor bir takma addır)
    assert envelope["command"] == "doctor" and envelope["ok"] is True
    out = envelope["data"]
    by_id = {c["id"]: c for c in out["checks"]}
    assert by_id["python"]["status"] == OK
    assert by_id["packages"]["code"] == "packages_missing" and by_id["packages"]["fix_command"][1:4] == ["-m", "pip", "install"]
    assert by_id["data_dir"]["status"] == OK
    assert by_id["env"]["status"] == OK and out["language"] == "tr"  # .env, dotenv olmadan okundu


def test_main_py_lists_doctor_in_help():
    proc = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "--doctor", "--help"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        env={**os.environ, "APP_LANGUAGE": "en"},
    )
    out = proc.stdout.decode()
    assert proc.returncode == 0 and "--json" in out and "--live" in out and "--strict" in out


# --- köprünün hata iletileri ------------------------------------------------------------------


def test_bridge_errors_point_at_the_doctor_not_at_playwright():
    solver = (REPO / "src" / "client" / "bridge.py").read_text(encoding="utf-8")  # köprü P24 ile taşındı
    health = (REPO / "src" / "bridge_health.py").read_text(encoding="utf-8")
    assert "playwright install" not in solver and "playwright install" not in health
    assert "--doctor" in solver and "--doctor" in health


def test_node_version_is_none_when_node_is_not_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert doctor.installed_node_version() is None
