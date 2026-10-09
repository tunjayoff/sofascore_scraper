"""
Katman kuralları (plan maddesi ST-04; docs/design/01-storage.md bölüm 2.1 ve bölüm 2.4, madde 3).

Store en alt katmandır: ağ kodu ve politika içermez, hangi dilimin gerektiğini ya da bir işin ne zaman
bittiğini bilmez. Bu yüzden `sofascore_scraper/store/` kendi dışından yalnızca şu modülleri içe aktarabilir:

    sofascore_scraper.sports   sofascore_scraper.status   sofascore_scraper.slices   sofascore_scraper.exceptions   sofascore_scraper.version

Kural mandalsızdır: bugün ihlal yok, bundan sonra da olamaz. Üç denetim:

  * kaynak taraması: `sofascore_scraper/store/` altındaki her dosyanın `sofascore_scraper.*` içe aktarmaları (göreli ve işlev içi olanlar dahil);
  * yükleme denemesi: ayrı bir süreçte Store'un bütün modülleri yüklenir; izin verilen modüller de başka bir
    katmanı dolaylı olarak getirmemelidir (ör. `sofascore_scraper.status` bir gün `sofascore_scraper.utils`'i içe aktarırsa);
  * ağ: Store ne bir HTTP istemcisini ne de web çatısını içe aktarır.

Yüzler (docs/design/02-services.md 2.1, kural 1; plan maddesi P21): web yüzü (`sofascore_scraper/web`) yalnızca servisleri, iş
modelini, ayarları ve hata tablosunu içe aktarır (`sofascore_scraper.services`, `sofascore_scraper.jobs`, `sofascore_scraper.config`, `sofascore_scraper.errors`) ve
kendi paketini. Bugün servisi olmayan şeyler için başka modüller de içe aktarılır; her biri `WEB_ALSO_IMPORTS`
listesindedir, nedeniyle. Liste bir mandaldır: listede olmayan bir modül testi düşürür, artık içe aktarılmayan
bir girdi de (liste yalnızca küçülür; P30 eski rotalarla birlikte çoğunu siler). CLI yüzü (`sofascore_scraper/cli`) P19'undur.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "sofascore_scraper" / "store"

STORE_MAY_IMPORT = frozenset({"sofascore_scraper.sports", "sofascore_scraper.status", "sofascore_scraper.slices", "sofascore_scraper.exceptions", "sofascore_scraper.version"})

# Store'da yeri olmayan ağ kodu: HTTP istemcileri, tarayıcı köprüsü, web çatısı.
NETWORK_MODULES = frozenset({
    "requests", "httpx", "aiohttp", "urllib3", "curl_cffi", "websockets", "scrapling", "patchright", "playwright",
    "fastapi", "starlette", "uvicorn",
    "urllib.request", "http.client", "http.server", "ftplib", "smtplib",
})


# --- içe aktarmaları çıkarma -------------------------------------------------------------------------


def module_name(path: Path, root: Path) -> Tuple[str, str]:
    """(modülün tam adı, göreli içe aktarmaların çözüleceği paket): sofascore_scraper/store/files.py -> ('sofascore_scraper.store.files', 'sofascore_scraper.store')."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        return ".".join(parts[:-1]), ".".join(parts[:-1])
    return ".".join(parts), ".".join(parts[:-1])


def _absolute(module: Optional[str], level: int, package: str) -> str:
    if level == 0:
        return module or ""
    parts = package.split(".") if package else []
    if level > 1:
        parts = parts[: max(len(parts) - (level - 1), 0)]
    return ".".join(parts + ([module] if module else []))


def imported_modules(source: str, package: str) -> List[Tuple[int, str]]:
    """
    Kaynaktaki her içe aktarma için (satır, tam ad). `from a import b` hem 'a' hem 'a.b' olarak döner
    (b bir alt modül olabilir); `importlib.import_module("x")` ve `__import__("x")` sabit adla çağrıldıysa sayılır.
    """
    found: List[Tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(node.module, node.level, package)
            if base:
                found.append((node.lineno, base))
            for alias in node.names:
                if alias.name != "*":
                    found.append((node.lineno, f"{base}.{alias.name}" if base else alias.name))
        elif isinstance(node, ast.Call) and node.args:
            func, first = node.func, node.args[0]
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name in ("import_module", "__import__") and isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((node.lineno, first.value))
    return sorted(found)


def _is_or_under(module: str, names: frozenset) -> bool:
    return any(module == name or module.startswith(name + ".") for name in names)


def store_layer_violations(store_dir: Path, root: Path, allowed: frozenset = STORE_MAY_IMPORT) -> List[str]:
    """Store'un izin verilmeyen `sofascore_scraper.*` içe aktarmaları, `dosya:satır: modül` olarak."""
    package = ".".join(store_dir.relative_to(root).parts)  # "sofascore_scraper.store"
    top = package.split(".")[0]
    problems = []
    for path in sorted(store_dir.rglob("*.py")):
        _name, module_package = module_name(path, root)
        for line, module in imported_modules(path.read_text(encoding="utf-8"), module_package):
            if module != top and not module.startswith(top + "."):
                continue  # standart kitaplık ya da üçüncü taraf
            layer = ".".join(module.split(".")[:2])  # sofascore_scraper.utils.x -> sofascore_scraper.utils
            if module == top or layer == package or layer in allowed:
                continue
            problems.append(f"{path.relative_to(root).as_posix()}:{line}: {module}")
    return sorted(set(problems))


def network_imports(store_dir: Path, root: Path) -> List[str]:
    problems = []
    for path in sorted(store_dir.rglob("*.py")):
        _name, module_package = module_name(path, root)
        for line, module in imported_modules(path.read_text(encoding="utf-8"), module_package):
            if _is_or_under(module, NETWORK_MODULES):
                problems.append(f"{path.relative_to(root).as_posix()}:{line}: {module}")
    return sorted(set(problems))


# --- gerçek ağaç -------------------------------------------------------------------------------------


def test_store_imports_only_the_allowed_src_modules():
    problems = store_layer_violations(STORE_DIR, ROOT)
    assert not problems, (
        "sofascore_scraper/store/ yalnızca şunları içe aktarabilir: " + ", ".join(sorted(STORE_MAY_IMPORT))
        + " (docs/design/01-storage.md bölüm 2.1). Politika ve ağ kodu çağıranın işidir; değer olarak verin.\n  "
        + "\n  ".join(problems)
    )


def test_store_has_no_network_code():
    problems = network_imports(STORE_DIR, ROOT)
    assert not problems, "sofascore_scraper/store/ ağ kodu içermez (docs/design/01-storage.md bölüm 2.1):\n  " + "\n  ".join(problems)


def test_the_modules_the_store_may_import_exist():
    for module in sorted(STORE_MAY_IMPORT):
        assert (ROOT / (module.replace(".", "/") + ".py")).is_file(), f"{module} yok: izin listesi güncel değil"


_LOAD_STORE = """
import importlib, json, pkgutil, sys
import sofascore_scraper.store
failed = {}
for info in pkgutil.walk_packages(sofascore_scraper.store.__path__, "sofascore_scraper.store."):
    try:
        importlib.import_module(info.name)
    except Exception as exc:
        failed[info.name] = repr(exc)
print(json.dumps({"modules": sorted(sys.modules), "failed": failed}))
"""


def test_loading_the_store_loads_no_other_layer():
    """
    Store'un bütün modülleri temiz bir süreçte yüklenir. Yüklenen `sofascore_scraper.*` modülleri yalnızca Store'un
    kendisi ve izin verilenlerdir: izin verilen bir modül de başka bir katmanı dolaylı olarak getiremez.
    """
    done = subprocess.run([sys.executable, "-c", _LOAD_STORE], cwd=str(ROOT), capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["failed"] == {}
    loaded = [m for m in report["modules"] if m.startswith("sofascore_scraper.")]
    assert "sofascore_scraper.store.errors" in loaded
    foreign = [m for m in loaded if not (m == "sofascore_scraper.store" or m.startswith("sofascore_scraper.store.") or m in STORE_MAY_IMPORT)]
    assert foreign == [], f"Store yüklenince başka katmanlar da yüklendi: {foreign}"
    network = [m for m in report["modules"] if _is_or_under(m, NETWORK_MODULES - {"urllib.request", "http.client"})]
    assert network == [], f"Store yüklenince ağ kitaplıkları da yüklendi: {network}"


# --- yüzler: web --------------------------------------------------------------------------------------

WEB_DIR = ROOT / "sofascore_scraper" / "web"
FACE_MAY_IMPORT = frozenset({"sofascore_scraper.services", "sofascore_scraper.jobs", "sofascore_scraper.config", "sofascore_scraper.errors"})

# Web yüzünün bugün içe aktardığı başka modüller ve nedeni. Bir girdi, servisi geldiğinde silinir (2.x'in
# `/api` yollarının girdileri P30'la gitti).
WEB_ALSO_IMPORTS: Dict[str, str] = {
    "sofascore_scraper.store": "hata sınıfları (StoreError, LeaseHeld, JobStoreConflict ...), iş deposu ve "
                 "`open_store`: rotalar depoyu açıp servise verir (deps.store)",
    "sofascore_scraper.schema": "şema v1 kayıtlarının yanıt modelleri (sofascore_scraper/web/api/v1/records.py)",
    "sofascore_scraper.sports": "spor kayıt defteri (`/sports`, lig sporları)",
    "sofascore_scraper.version": "sürüm (`/health`, `/status`)",
    "sofascore_scraper.logger": "web sunucusunun log ayarı ve log satırları",
    "sofascore_scraper.redact": "hata ve log metinlerinin maskelenmesi",
    "sofascore_scraper.exceptions": "istek katmanının tipli hataları (sofascore_scraper/web/upstream.py) ve StorageError",
    "sofascore_scraper.bridge_health": "SofaScore'a erişimin durumu (`/health`, `/status`, bağlantı denetimi)",
    "sofascore_scraper.throttle": "ortak istek bütçesinin durumu (`/health`, `/status`)",
    "sofascore_scraper.client": "bağlantı denetimi tek istek atar (API kökü ve istek işlevi istemcinindir; 3.1'e "
                                "kadar istek işlevi sofascore_scraper.utils'ten geliyordu)",
    "sofascore_scraper.diagnostics": "log ve tanılama rotaları (servisi yok: sofascore_scraper/diagnostics.py)",
    "sofascore_scraper.config_manager": "web sürecinin yapılandırma yöneticisi (deps.config_manager; P30 Settings'e geçer)",
    "sofascore_scraper.paths": ".env yolu (uygulamanın başlangıcı)",
    "sofascore_scraper.private_files": ".env ve tarayıcı profilinin izinleri (uygulamanın başlangıcı)",
    "sofascore_scraper.config_files": "lig spor dosyasının yazımı (sofascore_scraper/web/league_sports.py)",
}


def face_imports(face_dir: Path, root: Path) -> Dict[str, List[str]]:
    """Yüzün kendi paketi ve izin verilen katmanlar dışındaki `sofascore_scraper.*` içe aktarmaları: katman → `dosya:satır` listesi."""
    package = ".".join(face_dir.relative_to(root).parts)  # "sofascore_scraper.web"
    top = package.split(".")[0]
    found: Dict[str, List[str]] = {}
    for path in sorted(face_dir.rglob("*.py")):
        _name, module_package = module_name(path, root)
        for line, module in imported_modules(path.read_text(encoding="utf-8"), module_package):
            if not module.startswith(top + "."):
                continue
            layer = ".".join(module.split(".")[:2])
            if layer == package or layer in FACE_MAY_IMPORT:
                continue
            found.setdefault(layer, []).append(f"{path.relative_to(root).as_posix()}:{line}")
    return found


def test_the_web_face_imports_only_services_and_the_listed_modules():
    found = face_imports(WEB_DIR, ROOT)
    unlisted = {layer: sorted(set(places)) for layer, places in found.items() if layer not in WEB_ALSO_IMPORTS}
    assert not unlisted, (
        "sofascore_scraper/web yalnızca sofascore_scraper.services, sofascore_scraper.jobs, sofascore_scraper.config ve sofascore_scraper.errors'u içe aktarır (02-services.md 2.1, kural 1); "
        "başka bir modül gerekiyorsa servisinden alın ya da WEB_ALSO_IMPORTS'a nedeniyle yazın:\n  "
        + "\n  ".join(f"{layer}: {', '.join(places)}" for layer, places in sorted(unlisted.items()))
    )
    stale = sorted(set(WEB_ALSO_IMPORTS) - set(found))
    assert not stale, f"WEB_ALSO_IMPORTS'ta artık içe aktarılmayan girdiler (listeden silin): {stale}"


def test_the_web_face_has_no_terminal_ui_and_no_writer():
    """Web yüzü terminal menüsünü, detay aşamasını ve boru hattını doğrudan içe aktarmaz: servisler üzerinden çalışır."""
    found = face_imports(WEB_DIR, ROOT)
    for layer in ("sofascore_scraper.SofaScoreUi", "sofascore_scraper.ui", "sofascore_scraper.services.detail_phase",
                  "sofascore_scraper.services.pipeline"):
        assert layer not in found, (layer, found.get(layer))


def test_the_web_routes_package_is_gone():
    """2.x'in rotaları yoktur: sofascore_scraper/web/routes, web/fetch_job.py (P21) ve web/api/legacy.py (P30)."""
    assert not (WEB_DIR / "routes").exists() and not (WEB_DIR / "fetch_job.py").exists()
    assert not (WEB_DIR / "api" / "legacy.py").exists()


def test_face_imports_reports_layers_with_their_places(tmp_path: Path):
    web = tmp_path / "sofascore_scraper" / "web"
    for rel, text in {
        "sofascore_scraper/web/__init__.py": "",
        "sofascore_scraper/web/app.py": "from sofascore_scraper.services.query import QueryService\nfrom sofascore_scraper.web import deps\nimport sofascore_scraper.utils\n",
        "sofascore_scraper/web/api/x.py": "def f():\n    from sofascore_scraper.store import open_store\n    from sofascore_scraper.jobs.model import JobKind\n",
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert face_imports(web, tmp_path) == {"sofascore_scraper.utils": ["sofascore_scraper/web/app.py:3"], "sofascore_scraper.store": ["sofascore_scraper/web/api/x.py:2", "sofascore_scraper/web/api/x.py:2"]}


# --- denetleyicinin kendi testleri -------------------------------------------------------------------


@pytest.mark.parametrize("source, package, expected", [
    ("import os\nimport sofascore_scraper.utils\n", "sofascore_scraper.store", ["os", "sofascore_scraper.utils"]),
    ("import sofascore_scraper.utils as u, json\n", "sofascore_scraper.store", ["json", "sofascore_scraper.utils"]),
    ("from sofascore_scraper.exceptions import StorageError\n", "sofascore_scraper.store", ["sofascore_scraper.exceptions", "sofascore_scraper.exceptions.StorageError"]),
    ("from sofascore_scraper import utils\n", "sofascore_scraper.store", ["sofascore_scraper", "sofascore_scraper.utils"]),
    ("from . import files\n", "sofascore_scraper.store", ["sofascore_scraper.store", "sofascore_scraper.store.files"]),
    ("from .errors import StoreError\n", "sofascore_scraper.store", ["sofascore_scraper.store.errors", "sofascore_scraper.store.errors.StoreError"]),
    ("from .. import utils\n", "sofascore_scraper.store", ["sofascore_scraper", "sofascore_scraper.utils"]),
    ("from ..web.jobs import JobStore\n", "sofascore_scraper.store", ["sofascore_scraper.web.jobs", "sofascore_scraper.web.jobs.JobStore"]),
    ("from ...utils import x\n", "sofascore_scraper.store.schema", ["sofascore_scraper.utils", "sofascore_scraper.utils.x"]),
    ("def f():\n    from sofascore_scraper.web import app\n    return app\n", "sofascore_scraper.store", ["sofascore_scraper.web", "sofascore_scraper.web.app"]),
    ("import importlib\nm = importlib.import_module('sofascore_scraper.jobs.model')\n", "sofascore_scraper.store", ["importlib", "sofascore_scraper.jobs.model"]),
    ("m = __import__('sofascore_scraper.client')\n", "sofascore_scraper.store", ["sofascore_scraper.client"]),
    ("if TYPE_CHECKING:\n    from sofascore_scraper.services.sync import SyncService\n", "sofascore_scraper.store",
     ["sofascore_scraper.services.sync", "sofascore_scraper.services.sync.SyncService"]),
])
def test_imported_modules(source: str, package: str, expected: List[str]):
    assert [module for _line, module in imported_modules(source, package)] == expected


def test_module_name():
    assert module_name(ROOT / "sofascore_scraper" / "store" / "files.py", ROOT) == ("sofascore_scraper.store.files", "sofascore_scraper.store")
    assert module_name(ROOT / "sofascore_scraper" / "store" / "__init__.py", ROOT) == ("sofascore_scraper.store", "sofascore_scraper.store")
    assert module_name(ROOT / "sofascore_scraper" / "store" / "schema" / "__init__.py", ROOT) == ("sofascore_scraper.store.schema", "sofascore_scraper.store.schema")


def _tree(tmp_path: Path, files: Dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path / "sofascore_scraper" / "store"


def test_layer_check_accepts_the_allowed_modules_and_the_store_itself(tmp_path: Path):
    store = _tree(tmp_path, {
        "sofascore_scraper/store/__init__.py": "from sofascore_scraper.store.errors import StoreError\n",
        "sofascore_scraper/store/errors.py": "from sofascore_scraper.exceptions import StorageError\nimport json, sqlite3\n",
        "sofascore_scraper/store/derive.py": (
            "from sofascore_scraper import sports, slices\nfrom sofascore_scraper.status import classify_status\nfrom sofascore_scraper.version import __version__\n"
            "from . import errors\nfrom .errors import StoreError\nimport pandas\n"
        ),
        "sofascore_scraper/store/schema/__init__.py": "from .. import errors\nfrom ..errors import StoreError\n",
    })
    assert store_layer_violations(store, tmp_path) == []
    assert network_imports(store, tmp_path) == []


def test_layer_check_reports_every_forbidden_import_with_its_line(tmp_path: Path):
    store = _tree(tmp_path, {
        "sofascore_scraper/store/__init__.py": "",
        "sofascore_scraper/store/api.py": (
            "from sofascore_scraper.utils import make_api_request\n"            # 1
            "from sofascore_scraper import config_manager\n"                    # 2
            "import sofascore_scraper.web.jobs\n"                               # 3
            "from ..services import sync\n"                       # 4
            "def f():\n"                                          # 5
            "    from sofascore_scraper.jobs.model import JobKind\n"            # 6
            "    import importlib\n"                              # 7
            "    return importlib.import_module('sofascore_scraper.match_data_fetcher')\n"  # 8
        ),
        "sofascore_scraper/store/schema/__init__.py": "from ...client.bridge import BrowserBridge\n",
    })
    assert store_layer_violations(store, tmp_path) == [
        "sofascore_scraper/store/api.py:1: sofascore_scraper.utils",
        "sofascore_scraper/store/api.py:1: sofascore_scraper.utils.make_api_request",
        "sofascore_scraper/store/api.py:2: sofascore_scraper.config_manager",
        "sofascore_scraper/store/api.py:3: sofascore_scraper.web.jobs",
        "sofascore_scraper/store/api.py:4: sofascore_scraper.services",
        "sofascore_scraper/store/api.py:4: sofascore_scraper.services.sync",
        "sofascore_scraper/store/api.py:6: sofascore_scraper.jobs.model",
        "sofascore_scraper/store/api.py:6: sofascore_scraper.jobs.model.JobKind",
        "sofascore_scraper/store/api.py:8: sofascore_scraper.match_data_fetcher",
        "sofascore_scraper/store/schema/__init__.py:1: sofascore_scraper.client.bridge",
        "sofascore_scraper/store/schema/__init__.py:1: sofascore_scraper.client.bridge.BrowserBridge",
    ]


def test_network_check_reports_http_clients_and_web_frameworks(tmp_path: Path):
    store = _tree(tmp_path, {
        "sofascore_scraper/store/__init__.py": "",
        "sofascore_scraper/store/export.py": (
            "import urllib.parse\n"                    # 1: ayrıştırma ağ değildir
            "import urllib.request\n"                  # 2
            "from curl_cffi import requests\n"         # 3
            "import httpx as client\n"                 # 4
            "from fastapi.responses import FileResponse\n"  # 5
            "from http import HTTPStatus\n"            # 6: sabitler ağ değildir
            "from http import client as http_client\n"  # 7
            "import sqlite3, socket\n"                 # 8: makine adı için socket serbest
        ),
    })
    assert network_imports(store, tmp_path) == [
        "sofascore_scraper/store/export.py:2: urllib.request",
        "sofascore_scraper/store/export.py:3: curl_cffi",
        "sofascore_scraper/store/export.py:3: curl_cffi.requests",
        "sofascore_scraper/store/export.py:4: httpx",
        "sofascore_scraper/store/export.py:5: fastapi.responses",
        "sofascore_scraper/store/export.py:5: fastapi.responses.FileResponse",
        "sofascore_scraper/store/export.py:7: http.client",
    ]
