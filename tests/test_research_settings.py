"""
FX-32: araştırma betikleri ayarlarını 3.1'in okuduğu adlarla koyar.

3.1 (P30) 2.x'in ortam adlarını okumaz. Betikler `LOG_LEVEL` ve `SOFASCORE_BROWSER_PROFILE` koymaya devam edince keşif
tarayıcısı sessizce uygulamanın varsayılan tarayıcı profiline düşüyordu; araştırma o profili asla paylaşmamalı.
Betikler artık `SOFASCORE_LOG__LEVEL` ve `SOFASCORE_CLIENT__BROWSER_PROFILE` koyar ve köprüyü
`sofascore_scraper.client.bridge` adıyla içe aktarır (`sofascore_scraper.challenge_solver` takma adı kalktı).

Her betik temiz bir alt süreçte içe aktarılır: köprü profilini (DEFAULT_PROFILE_DIR) ilk içe aktarılışında okur, test
sürecinde o çoktan olmuştur. Alt sürecin HOME'u testin geçici dizinidir: araştırma profilinin yolu oraya düşer, hiçbir
şey gerçek ~/.cache altına yazılmaz. Ağa çıkılmaz (yalnızca içe aktarma).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

# Araştırma betiklerinin ortak hız kilidi fcntl kullanır (Linux/macOS araçları)
pytest.importorskip("fcntl")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")

_PROBE = r"""
import json, logging, sys
sys.path.insert(0, sys.argv[1])
import importlib
importlib.import_module(sys.argv[2])
from sofascore_scraper import logger
from sofascore_scraper.client import bridge
from sofascore_scraper.config import loader
from sofascore_scraper.paths import DEFAULT_BROWSER_PROFILE_DIR
loaded = loader.active()
print(json.dumps({
    "profile": loaded.settings.client.browser_profile,
    "bridge_default": bridge.DEFAULT_PROFILE_DIR,
    "get_instance_default": bridge.BrowserBridge.get_instance.__defaults__[0],
    "app_default": DEFAULT_BROWSER_PROFILE_DIR,
    "log_level": loaded.settings.log.level,
    "resolved_level": logger.resolve_level(),
    "warnings": [w.code for w in loaded.warnings],
    "alias_loaded": "sofascore_scraper.challenge_solver" in sys.modules,
}))
"""

# Betik -> kendi araştırma profili (~/.cache/sofascore_research altında)
SCRIPT_PROFILES = {
    "explore_all_sports": "chrome_all_sports",
    "_research_common": "chrome_research",
    "push_channel_run": "chrome_push_run",
    "push_light_probe": "chrome_push_light",
    "push_direct_probe": "chrome_push_direct",
}


def _import_in_clean_process(module: str, home, profile=None) -> dict:
    env = dict(os.environ)
    # Test sürecinin profili (conftest) ve log seviyesi verilmemiş sayılır: betiğin koyduğu değer görünsün
    for name in ("SOFASCORE_CLIENT__BROWSER_PROFILE", "SOFASCORE_LOG__LEVEL", "SOFASCORE_LOG__DEBUG",
                 "LOG_LEVEL", "SOFASCORE_BROWSER_PROFILE"):
        env.pop(name, None)
    if profile is not None:
        env["SOFASCORE_CLIENT__BROWSER_PROFILE"] = profile
    env["HOME"] = str(home)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-c", _PROBE, SCRIPTS, module], cwd=str(home), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("module", sorted(SCRIPT_PROFILES))
def test_importing_a_research_script_sets_its_profile_and_log_level_by_the_3_1_names(module, tmp_path):
    if module == "push_direct_probe":
        pytest.importorskip("aiohttp")
    home = tmp_path / "home"
    home.mkdir()
    out = _import_in_clean_process(module, home)

    research = str(home / ".cache" / "sofascore_research" / SCRIPT_PROFILES[module])
    assert out["profile"] == research
    assert out["bridge_default"] == research and out["get_instance_default"] == research
    assert os.path.normpath(os.path.expanduser(out["app_default"])) != research
    assert out["log_level"] == "WARNING" and out["resolved_level"] == 30
    assert "legacy_name" not in out["warnings"]  # 2.x adı koyulmadı
    assert out["alias_loaded"] is False
    # İçe aktarma profili yaratmaz (köprü onu tarayıcıyı açarken kurar)
    assert not (home / ".cache" / "sofascore_research").exists()


def test_an_explicit_profile_wins_over_the_research_default(tmp_path):
    """setdefault: araştırmacının açıkça verdiği (3.1 adıyla) profil korunur."""
    home = tmp_path / "home"
    home.mkdir()
    chosen = str(tmp_path / "chosen-profile")
    out = _import_in_clean_process("explore_all_sports", home, profile=chosen)
    assert out["profile"] == chosen and out["bridge_default"] == chosen


def test_no_script_uses_a_2x_environment_name():
    """scripts/ altındaki Python betikleri 3.1'in okumadığı 2.x adlarını koymaz ve okumaz."""
    from sofascore_scraper.config import loader

    offenders = []
    for name in sorted(os.listdir(SCRIPTS)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
            text = f.read()
        for legacy in loader.LEGACY_NAMES:
            for quoted in (f'"{legacy}"', f"'{legacy}'"):
                if quoted in text:
                    offenders.append(f"{name}: {legacy}")
    assert offenders == []
    assert not os.path.exists(os.path.join(ROOT, "sofascore_scraper", "challenge_solver.py"))
