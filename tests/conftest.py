"""
Test yalıtımı: testler gerçek .env, config/, data/ ve jobs.db'ye dokunmamalı.

Ortam değişkenleri modül yüklenirken ayarlanır — src.* modülleri import anında
.env'i ve DATA_DIR'i okuduğu için bunun herhangi bir test modülü import edilmeden
önce olması gerekir. Geçici dizine küçük, sentetik bir veri seti yazılır.
"""
from __future__ import annotations

import atexit
import csv
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="sofascore-tests-")
atexit.register(shutil.rmtree, _TMP, True)

DATA_DIR = os.path.join(_TMP, "data")
CONFIG_DIR = os.path.join(_TMP, "config")
ENV_FILE = os.path.join(_TMP, ".env")

LEAGUE_ID = 17
LEAGUE_NAME = "Premier League"
SEASON_ID = 96668
SEASON_NAME = "Premier League 26/27"
MATCH_IDS = (9000001, 9000002)

# Kullanıcının kabuğundan veya gerçek .env'den gelebilecek ayarları temizle
for _k in ("PROXY_URL", "USE_PROXY", "API_BASE_URL", "SOFA_CAPTCHA_TOKEN", "FETCH_ONLY_FINISHED", "SOFASCORE_API_TOKEN"):
    os.environ.pop(_k, None)
# Dil, testleri çalıştıranın kabuğuna bağlı olmasın (kural: açık ayar > sistem dili > İngilizce,
# src/language.py): açık ayar yok, ileti dili "C" → her makinede varsayılan dil (İngilizce).
# LC_MESSAGES yalnızca ileti dilidir; LANG'e dokunulmaz (karakter kodlaması ondan gelir).
for _k in ("APP_LANGUAGE", "LANGUAGE", "LC_ALL"):
    os.environ.pop(_k, None)
os.environ["LC_MESSAGES"] = "C"
# Log dosyası da geçici dizine: testler projedeki logs/ dizinine yazmaz (src/logger.py)
for _k in ("LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT"):
    os.environ.pop(_k, None)
os.environ["LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["DATA_DIR"] = DATA_DIR
os.environ["SOFASCORE_CONFIG_DIR"] = CONFIG_DIR
os.environ["SOFASCORE_ENV_FILE"] = ENV_FILE
# Yapılandırma dosyası (sofascore.toml) aranmaz: proje kökündeki gerçek bir dosya testleri etkilemesin.
# Dosyayı sınayan testler SOFASCORE_CONFIG'i kendi geçici dosyalarına çevirir (tests/test_config_loader.py).
os.environ["SOFASCORE_CONFIG"] = "none"
# Ortak istek bütçesi (src/throttle.py) testlerde kapalı ve yalıtılmış: testler makinedeki gerçek
# süreçlerin bütçe dosyasına dokunmaz, sahte uyku sayaçlarına fazladan bekleme girmez.
os.environ["REQUEST_RATE_LIMIT"] = "0"
os.environ["SOFASCORE_THROTTLE_DIR"] = os.path.join(_TMP, "throttle")
# Tarayıcı profili de geçici dizinde: uygulama başlangıçta profil dizininin izinlerini daraltır
# (src/private_files.harden_secret_paths); testler kullanıcının gerçek profiline dokunmaz.
os.environ["SOFASCORE_BROWSER_PROFILE"] = os.path.join(_TMP, "browser-profile")
# TestClient "testserver" Host başlığını kullanır
os.environ["SOFASCORE_ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _basic(mid: int, home: str, away: str, hs: int, as_: int, ts: int) -> dict:
    return {
        "id": mid,
        "tournament": {
            "name": LEAGUE_NAME,
            "uniqueTournament": {"id": LEAGUE_ID, "name": LEAGUE_NAME},
        },
        "season": {"id": SEASON_ID, "name": SEASON_NAME, "year": "26/27"},
        "roundInfo": {"round": 1},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": 1, "name": home},
        "awayTeam": {"id": 2, "name": away},
        "homeScore": {"current": hs},
        "awayScore": {"current": as_},
        "startTimestamp": ts,
    }


def _seed() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(os.path.join(CONFIG_DIR, "leagues.txt"), "w", encoding="utf-8") as f:
        f.write(f"# League configuration file\n# Format: League Name: ID\n\n{LEAGUE_NAME}: {LEAGUE_ID}\n")
    _write_json(os.path.join(CONFIG_DIR, "league_sports.json"), {str(LEAGUE_ID): "football"})
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("# test env\nMAX_CONCURRENT=5\n")

    league_dir = f"{LEAGUE_ID}_{LEAGUE_NAME.replace(' ', '_')}"
    season_slug = SEASON_NAME.replace(" ", "_").replace("/", "_")
    _write_json(
        os.path.join(DATA_DIR, "seasons", f"{league_dir}_seasons.json"),
        {"seasons": [{"id": SEASON_ID, "name": SEASON_NAME, "year": "26/27"}]},
    )

    rows = [
        (MATCH_IDS[0], "Arsenal", "Coventry City", 3, 0, "2026-08-21T22:00:00", 1787353200),
        (MATCH_IDS[1], "Hull City", "Manchester United", 2, 0, "2026-08-22T14:30:00", 1787409000),
    ]
    summary_csv = os.path.join(DATA_DIR, "matches", league_dir, f"{SEASON_ID}_{season_slug}_summary.csv")
    os.makedirs(os.path.dirname(summary_csv), exist_ok=True)
    with open(summary_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["round", "match_id", "home_team", "away_team", "home_score", "away_score",
                    "match_date", "status", "tournament", "season"])
        for mid, home, away, hs, as_, date, _ts in rows:
            w.writerow([1, mid, home, away, hs, as_, date, "Ended", LEAGUE_NAME, SEASON_NAME])

    # Yalnızca ilk maçın detayı var; ikincisi "eksik detay" senaryosu için boş bırakılır
    mid, home, away, hs, as_, _date, ts = rows[0]
    details = os.path.join(DATA_DIR, "match_details", league_dir, f"season_{season_slug}", str(mid))
    _write_json(os.path.join(details, "basic.json"), _basic(mid, home, away, hs, as_, ts))


_seed()


# --- Store sınırı: çalışma zamanı denetimi (docs/design/01-storage.md, bölüm 2.4) ------------------
#
# Kural: DATA_DIR altındaki her şeye yalnızca src/store/ dokunur. Statik denetim
# (tests/test_store_boundary.py) çağrıları kaynak koddan bulur; burası onun göremediğini, yani yolu
# çalışırken kurulan erişimleri yakalar. CPython `open`, `os.listdir`, `sqlite3.connect` gibi çağrılar
# için denetim olayı (audit event) üretir. Kanca, yolu test veri dizininin altında olan olaylarda
# çağrı yığınını içten dışa yürür:
#
#   * önce tests/ altında bir çerçeveye rastlarsa erişim testin kendisine aittir (fixture kurulumu): sayılmaz;
#   * önce src/store/ altında bir çerçeveye rastlarsa erişim Store'undur: sayılmaz;
#   * önce src/ altında başka bir çerçeveye rastlarsa ihlaldir: (dosya, satır, çağrı) olarak kaydedilir.
#
# Kayıtları değerlendiren (işlev adına çevirip `tests/store_boundary/baseline/` ile karşılaştıran)
# testler tests/test_store_boundary.py içindedir ve oturumun sonunda çalışır. Kanca yalnızca kaydeder,
# hiçbir çağrıyı engellemez.
#
# İzlenen dizinler: aşağıdaki DATA_DIR ve `DATA_DIR` ortam değişkeninin o anki değeri. Veri dizinini
# ortam değişkeni olmadan, doğrudan bağımsız değişkenle veren bir test (ör. `open_store(tmp_path)`)
# dizinini `conftest.STORE_BOUNDARY.add_data_dir(str(tmp_path))` ile bildirebilir.

# Yol taşıyan dosya sistemi olayları. os.stat / os.path.exists olay üretmez; onları statik denetim görür.
FS_AUDIT_EVENTS = frozenset({
    "open", "os.listdir", "os.scandir", "os.walk", "os.fwalk", "os.mkdir", "os.rmdir", "os.remove", "os.rename",
    "os.chmod", "os.chown", "os.utime", "os.truncate", "os.link", "os.symlink",
    "glob.glob", "glob.glob/2", "pathlib.Path.glob", "pathlib.Path.rglob", "pathlib.Path.walk",
    "shutil.rmtree", "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.move",
    "shutil.make_archive", "shutil.unpack_archive", "shutil.chown",
    "tempfile.mkstemp", "tempfile.mkdtemp", "sqlite3.connect",
})

# Tek bir sistem çağrısına karşılık gelen olaylar. Geri kalanlar bir kitaplık işlevinin kendi olayıdır
# (os.walk, glob.glob, shutil.rmtree ...).
_PRIMITIVE_AUDIT_EVENTS = frozenset({
    "open", "os.listdir", "os.scandir", "os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.chmod", "os.chown",
    "os.utime", "os.truncate", "os.link", "os.symlink", "sqlite3.connect",
})
_GENERATOR_FLAGS = 0x20 | 0x200  # CO_GENERATOR | CO_ASYNC_GENERATOR
_STDLIB_MODULES = frozenset(sys.stdlib_module_names)
_OS_PATH_MODULES = frozenset({"posixpath", "ntpath", "genericpath"})

# (src/ altındaki dosyanın depo köküne göre yolu, satır, çağrı) -> (ilk görüldüğü test, yol)
BoundaryKey = Tuple[str, int, str]


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _audit_path(arg: Any) -> Optional[str]:
    """Olay bağımsız değişkenindeki yol; dosya tanıtıcısı (int), None ve yol olmayan değerler için None."""
    if isinstance(arg, str):
        text = arg
    elif isinstance(arg, (bytes, os.PathLike)):
        try:
            text = os.fsdecode(arg)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if text.startswith("file:"):  # sqlite3.connect("file:/…?mode=ro", uri=True)
        try:
            text = urllib.request.url2pathname(urllib.parse.urlsplit(text).path)
        except (OSError, ValueError):
            return None
    return text


def _callee_name(frame: Any, event: str) -> Optional[str]:
    """
    src/ çerçevesinin doğrudan çağırdığı Python işlevinin adı; kayıt gereksizse None.

    Standart kitaplık için `modül.işlev` (ör. `shutil.rmtree`, `os.makedirs`): tek bir çağrı her platformda
    aynı ada düşer, içeride hangi sistem çağrılarının yapıldığı (sürüme ve işletim sistemine göre değişir)
    ada karışmaz. Üçüncü taraf paketlerde yalnızca paket adı (ör. `pandas`): iç işlev adları sürümle değişir.

    Tembel yineleyiciler (os.walk, glob.iglob, Path.glob): çağrı anında kitaplığın kendi olayı zaten
    kaydedilir; yineleme sürerken src/ çerçevesinin altında kitaplığın iç üreteçleri durur ve adları
    Python sürümüne göre değişir (`os._walk` yalnızca 3.11'e kadar var). O sistem çağrıları kaydedilmez.
    """
    module = str(frame.f_globals.get("__name__") or "<dynamic>")
    top = module.partition(".")[0]
    if top not in _STDLIB_MODULES:
        return top
    if top in _OS_PATH_MODULES:
        top = "os.path"
    code = frame.f_code
    name = code.co_name
    if code.co_flags & _GENERATOR_FLAGS and event in _PRIMITIVE_AUDIT_EVENTS:
        if (top, name) != ("pathlib", "iterdir"):  # Path.iterdir 3.12'ye kadar üreteçtir ve kendi olayı yoktur
            return None
    if code.co_argcount and code.co_varnames[0] == "self":  # yöntem: sınıf adıyla (`zipfile.ZipFile.write`)
        owner = "Path" if top == "pathlib" else type(frame.f_locals.get("self")).__name__
        name = owner if name == "__init__" else f"{owner}.{name}"
    return f"{top}.{name}"


class BoundaryRecorder:
    """
    Verilen veri dizinlerinin altına, `src_dir/store/` dışındaki bir `src_dir` çerçevesinden yapılan
    erişimleri toplar. `follow_env=True` ise `DATA_DIR` ortam değişkeninin o anki değeri de veri dizini
    sayılır (testlerin çoğu kendi geçici dizinini `monkeypatch.setenv("DATA_DIR", ...)` ile verir).
    """

    def __init__(self, src_dir: str, tests_dir: str, data_dirs: Sequence[str], *, follow_env: bool = False) -> None:
        # Kayıtlardaki modül yolu diskteki harf büyüklüğünü korur (src/SofaScoreUi.py); karşılaştırmalar
        # ise normcase ile yapılır (Windows). normcase uzunluğu değiştirmez, bu yüzden önek uzunluğu ortaktır.
        self._root_length = len(os.path.join(os.path.dirname(os.path.realpath(src_dir)), ""))
        self._src = _canonical(src_dir) + os.sep
        self._store = _canonical(os.path.join(src_dir, "store")) + os.sep
        self._tests = _canonical(tests_dir) + os.sep
        self._fixed_roots = self._expand(data_dirs)
        self._roots = self._fixed_roots
        self._follow_env = follow_env
        self._env_value: Optional[str] = None
        # dosya adı -> False: proje dışı (yürümeye devam), True: tests/ ya da store (serbest), str: ihlal eden modül
        self._frame_kind: Dict[str, Union[bool, str]] = {}
        self.records: Dict[BoundaryKey, Tuple[str, str]] = {}
        self.errors: List[str] = []
        self.current_test = ""
        self.full_run = False

    @staticmethod
    def _expand(data_dirs: Sequence[str]) -> Tuple[str, ...]:
        """Her dizinin hem verildiği hem de sembolik bağları çözülmüş biçimi (macOS: /var -> /private/var)."""
        roots: List[str] = []
        for data_dir in data_dirs:
            for form in (os.path.normcase(os.path.abspath(data_dir)), _canonical(data_dir)):
                if form not in roots:
                    roots.append(form)
        return tuple(roots)

    def add_data_dir(self, data_dir: str) -> None:
        """Bir dizini daha veri dizini sayar (ör. `DATA_DIR` ayarlamadan `open_store(tmp_path)` açan testler)."""
        extra = tuple(r for r in self._expand([data_dir]) if r not in self._fixed_roots)
        self._fixed_roots += extra
        self._roots += tuple(r for r in extra if r not in self._roots)

    def _current_roots(self) -> Tuple[str, ...]:
        if self._follow_env:
            env = os.environ.get("DATA_DIR")
            if env != self._env_value:
                self._env_value = env
                extra = self._expand([env]) if env and isinstance(env, str) else ()
                self._roots = self._fixed_roots + tuple(r for r in extra if r not in self._fixed_roots)
        return self._roots

    @staticmethod
    def _inside(path: str, roots: Tuple[str, ...]) -> bool:
        path = os.path.normcase(os.path.abspath(path))
        return any(path == root or path.startswith(root + os.sep) for root in roots)

    def _kind(self, filename: str) -> Union[bool, str]:
        kind = self._frame_kind.get(filename)
        if kind is None:
            kind = False
            if not filename.startswith("<"):  # "<string>", "<frozen importlib._bootstrap>": dosya değil
                real = os.path.realpath(filename)
                folded = os.path.normcase(real)
                if folded.startswith(self._store) or folded.startswith(self._tests):
                    kind = True
                elif folded.startswith(self._src):
                    kind = real[self._root_length:].replace(os.sep, "/")
            self._frame_kind[filename] = kind
        return kind

    def audit(self, event: str, args: Tuple[Any, ...]) -> None:
        """Denetim kancasından çağrılır: olayı tetikleyen çerçeve, bu yöntemin iki üstündedir."""
        roots = self._current_roots()
        hit = None
        for arg in args[:2]:  # os.rename, shutil.copyfile, os.link: kaynak ve hedef
            path = _audit_path(arg)
            if path is not None and self._inside(path, roots):
                hit = path
                break
        if hit is None:
            return
        frame = sys._getframe(2)
        callee = None
        while frame is not None:
            kind = self._kind(frame.f_code.co_filename)
            if kind is True:
                return
            if kind is not False:
                call = event.partition("/")[0] if callee is None else _callee_name(callee, event)
                if call is not None:
                    self.records.setdefault((str(kind), frame.f_lineno, call), (self.current_test, hit))
                return
            callee = frame
            frame = frame.f_back


STORE_BOUNDARY = BoundaryRecorder(os.path.join(ROOT, "src"), os.path.join(ROOT, "tests"), [DATA_DIR], follow_env=True)
# Kanca bir kez kurulur ve kaldırılamaz; denetleyicinin kendi testleri buraya geçici kaydedici ekler.
BOUNDARY_RECORDERS: List[BoundaryRecorder] = [STORE_BOUNDARY]


def _boundary_audit_hook(event: str, args: Tuple[Any, ...]) -> None:
    if event not in FS_AUDIT_EVENTS:
        return
    try:
        for recorder in BOUNDARY_RECORDERS:
            recorder.audit(event, args)
    except Exception as exc:  # kanca hiçbir çağrıyı bozmamalı; hata, oturum sonundaki testte görünür
        STORE_BOUNDARY.errors.append(f"{event}: {type(exc).__name__}: {exc}")


sys.addaudithook(_boundary_audit_hook)


import pytest  # noqa: E402


def _is_full_run(config: "pytest.Config") -> bool:
    """
    Oturum, depo ayarlarıyla bütün testleri mi çalıştırıyor? Yalnızca o zaman "bu ihlal artık görülmüyor"
    denebilir; dosya, `-k` ya da `-m` ile daraltılmış bir çalıştırmada görülmeyen ihlal bir şey kanıtlamaz.
    """
    option = config.option
    if getattr(option, "keyword", "") or getattr(option, "lf", False) or getattr(option, "stepwise", False):
        return False
    if getattr(option, "deselect", None) or getattr(option, "ignore", None) or getattr(option, "ignore_glob", None):
        return False
    if hasattr(config, "workerinput") or getattr(option, "numprocesses", None):  # pytest-xdist: işçiler parça görür
        return False
    addopts = list(config.getini("addopts"))
    default_markexpr = addopts[addopts.index("-m") + 1] if "-m" in addopts[:-1] else ""
    if getattr(option, "markexpr", "") != default_markexpr:
        return False
    if getattr(getattr(config, "args_source", None), "name", "") == "TESTPATHS":
        return True
    whole = {_canonical(ROOT), _canonical(os.path.join(ROOT, "tests"))}
    base = str(config.invocation_params.dir)
    return bool(config.args) and all(
        "::" not in arg and _canonical(os.path.join(base, arg)) in whole for arg in config.args
    )


def pytest_configure(config: "pytest.Config") -> None:
    config.addinivalue_line(
        "markers",
        "store_boundary_last: oturumun sonunda çalışır (çalışma zamanı Store sınırı kayıtlarını değerlendirir)",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: "pytest.Config", items: List["pytest.Item"]) -> None:
    last = [item for item in items if item.get_closest_marker("store_boundary_last") is not None]
    if last:
        chosen = set(map(id, last))
        items[:] = [item for item in items if id(item) not in chosen] + last
    STORE_BOUNDARY.full_run = _is_full_run(config)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: "pytest.Item"):
    STORE_BOUNDARY.current_test = item.nodeid
    yield
    STORE_BOUNDARY.current_test = ""


@pytest.fixture(autouse=True)
def _isolate_request_layer(request, monkeypatch):
    """
    Her test temiz bir istek katmanıyla başlar: "önce tarayıcı" modu bir testten diğerine
    taşınmaz. `browser` işaretli olmayan testler gerçek bir tarayıcı başlatamaz.
    """
    import src.utils as utils
    import src.challenge_solver as cs
    from src import bridge_health

    monkeypatch.setattr(utils, "_browser_first_until", 0.0)
    bridge_health.reset()  # köprü sağlık durumu da testten teste taşınmaz
    if request.node.get_closest_marker("browser") is None:
        async def _no_real_browser(self):
            raise RuntimeError("tests must not launch a real browser (mark the test with @pytest.mark.browser)")

        monkeypatch.setattr(cs.BrowserBridge, "_launch", _no_real_browser)
    yield
