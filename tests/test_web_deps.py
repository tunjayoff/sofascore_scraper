"""
Web yüzünün paylaşılan nesneleri (src/web/deps.py; plan maddesi P21): eski `src/web/routes/common.py`nin modül
durumu buraya taşındı ve ilk kullanımda kurulur.

  * uygulamayı içe aktarmak lig dosyasını ve iş deposunu oluşturmaz (plan bölüm 15, satır 6 ve 27'nin web tarafı);
  * yapılandırma yöneticisi süreçteki tek ConfigManager'dır; iş deposu yapılandırılmış veri dizinindedir;
  * eski arayüzün iş görüntüsü hep aynı sözlüktür ve yerinde güncellenir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.config_manager import ConfigManager
from src.web import deps

ROOT = Path(__file__).resolve().parent.parent

_IMPORT_APP = """
import json, os, sys
from src.web.app import app
from src.web import deps
before = {"leagues": os.path.exists(os.path.join(os.environ["SOFASCORE_CONFIG_DIR"], "leagues.txt")),
          "state": os.path.exists(os.path.join(os.environ["DATA_DIR"], ".meta", "state.db")),
          "manager": deps._config_manager is not None, "jobs": deps._job_store is not None}
deps.job_store()
after = {"leagues": os.path.exists(os.path.join(os.environ["SOFASCORE_CONFIG_DIR"], "leagues.txt")),
         "state": os.path.exists(os.path.join(os.environ["DATA_DIR"], ".meta", "state.db"))}
print(json.dumps({"before": before, "after": after}))
"""


def test_importing_the_app_creates_no_league_file_and_no_job_store(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("SOFASCORE_API_TOKEN",)}
    env.update(
        DATA_DIR=str(tmp_path / "data"), SOFASCORE_CONFIG_DIR=str(tmp_path / "config"), SOFASCORE_CONFIG="none",
        SOFASCORE_ENV_FILE=str(tmp_path / ".env"), LOG_DIR=str(tmp_path / "logs"),
        SOFASCORE_BROWSER_PROFILE=str(tmp_path / "profile"),
    )
    done = subprocess.run([sys.executable, "-c", _IMPORT_APP], cwd=str(ROOT), env=env, capture_output=True,
                          text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["before"] == {"leagues": False, "state": False, "manager": False, "jobs": False}
    assert report["after"] == {"leagues": True, "state": True}


def test_the_shared_objects_are_the_process_wide_ones() -> None:
    assert deps.config_manager() is ConfigManager()
    store = deps.job_store()
    assert store is deps.job_store()
    assert os.path.dirname(store.db_path) == os.path.join(os.path.abspath(deps.config_manager().get_data_dir()), ".meta")


def test_the_job_mirror_is_one_dict_updated_in_place() -> None:
    first = deps.refresh_job_mirror()
    second = deps.refresh_job_mirror()
    assert first is second and first == deps.job_store().snapshot()
