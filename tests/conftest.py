"""
Test yalıtımı: testler gerçek .env, config/, data/ ve state.db'ye dokunmamalı.

Ortam değişkenleri modül yüklenirken ayarlanır — sofascore_scraper.* modülleri import anında
.env'i ve DATA_DIR'i okuduğu için bunun herhangi bir test modülü import edilmeden
önce olması gerekir. Geçici dizine küçük, sentetik bir veri seti yazılır.

Bütün paket `STORE_SHADOW_CHECK=1` ile çalışır (plan maddesi ST-11): Store'un dışından eski düzen köklerine yazan
ürün kodu ve Store'un temizlemesi not edilir ve o testin sonunda katalog, aynı ağacın sıfırdan kurulmuş haliyle
karşılaştırılır (bkz. aşağıda "Gölge denetimi").
"""
from __future__ import annotations

import atexit
import csv
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Collection, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

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
# sofascore_scraper/language.py): açık ayar yok, ileti dili "C" → her makinede varsayılan dil (İngilizce).
# LC_MESSAGES yalnızca ileti dilidir; LANG'e dokunulmaz (karakter kodlaması ondan gelir).
for _k in ("APP_LANGUAGE", "LANGUAGE", "LC_ALL"):
    os.environ.pop(_k, None)
os.environ["LC_MESSAGES"] = "C"
# Log dosyası da geçici dizine: testler projedeki logs/ dizinine yazmaz (sofascore_scraper/logger.py)
for _k in ("LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT"):
    os.environ.pop(_k, None)
os.environ["LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["DATA_DIR"] = DATA_DIR
os.environ["SOFASCORE_CONFIG_DIR"] = CONFIG_DIR
os.environ["SOFASCORE_ENV_FILE"] = ENV_FILE
# Yapılandırma dosyası (sofascore.toml) aranmaz: proje kökündeki gerçek bir dosya testleri etkilemesin.
# Dosyayı sınayan testler SOFASCORE_CONFIG'i kendi geçici dosyalarına çevirir (tests/test_config_loader.py).
os.environ["SOFASCORE_CONFIG"] = "none"
# Ortak istek bütçesi (sofascore_scraper/throttle.py) testlerde kapalı ve yalıtılmış: testler makinedeki gerçek
# süreçlerin bütçe dosyasına dokunmaz, sahte uyku sayaçlarına fazladan bekleme girmez.
os.environ["REQUEST_RATE_LIMIT"] = "0"
os.environ["SOFASCORE_THROTTLE_DIR"] = os.path.join(_TMP, "throttle")
# Tarayıcı profili de geçici dizinde: uygulama başlangıçta profil dizininin izinlerini daraltır
# (sofascore_scraper/private_files.harden_secret_paths); testler kullanıcının gerçek profiline dokunmaz.
os.environ["SOFASCORE_BROWSER_PROFILE"] = os.path.join(_TMP, "browser-profile")
# TestClient "testserver" Host başlığını kullanır
os.environ["SOFASCORE_ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"
# Gölge denetimi (sofascore_scraper/store/api.py): ürün kodunun dokunduğu veri dizinleri not edilir, test sonunda karşılaştırılır
os.environ["STORE_SHADOW_CHECK"] = "1"


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
# Kural: DATA_DIR altındaki her şeye yalnızca sofascore_scraper/store/ dokunur. Statik denetim
# (tests/test_store_boundary.py) çağrıları kaynak koddan bulur; burası onun göremediğini, yani yolu
# çalışırken kurulan erişimleri yakalar. CPython `open`, `os.listdir`, `sqlite3.connect` gibi çağrılar
# için denetim olayı (audit event) üretir. Kanca, yolu test veri dizininin altında olan olaylarda
# çağrı yığınını içten dışa yürür:
#
#   * önce tests/ altında bir çerçeveye rastlarsa erişim testin kendisine aittir (fixture kurulumu): sayılmaz;
#   * önce sofascore_scraper/store/ altında bir çerçeveye rastlarsa erişim Store'undur: sayılmaz;
#   * önce sofascore_scraper/ altında başka bir çerçeveye rastlarsa ihlaldir: (dosya, satır, çağrı) olarak kaydedilir.
#
# Kayıtları değerlendiren (işlev adına çevirip `FS_ALLOWLIST` ve `NAMED_EXCEPTIONS` ile karşılaştıran)
# testler tests/test_store_boundary.py içindedir ve oturumun sonunda çalışır. Kanca yalnızca kaydeder,
# hiçbir çağrıyı engellemez.
#
# İzlenen dizinler: aşağıdaki DATA_DIR ve `DATA_DIR` ortam değişkeninin o anki değeri. Veri dizinini
# ortam değişkeni olmadan, doğrudan bağımsız değişkenle veren bir test (ör. `open_store(tmp_path)`)
# dizinini `conftest.STORE_BOUNDARY.add_data_dir(str(tmp_path))` ile bildirebilir.

# Yol taşıyan dosya sistemi olayları. os.stat / os.path.exists olay üretmez; onları statik denetim görür.
# `_winapi.CopyFile2(kaynak, hedef, bayraklar)`: Windows'ta Python 3.12'den beri `shutil.copy2` (ve onu kullanan
# `shutil.copytree`) dosyayı bununla kopyalar ve `shutil.copyfile` ya da `open` olayı üretmez. Yolları
# `shutil.copyfile`ınkiler gibi ilk iki bağımsız değişkendedir.
FS_AUDIT_EVENTS = frozenset({
    "open", "os.listdir", "os.scandir", "os.walk", "os.fwalk", "os.mkdir", "os.rmdir", "os.remove", "os.rename",
    "os.chmod", "os.chown", "os.utime", "os.truncate", "os.link", "os.symlink",
    "glob.glob", "glob.glob/2", "pathlib.Path.glob", "pathlib.Path.rglob", "pathlib.Path.walk",
    "shutil.rmtree", "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.move",
    "shutil.make_archive", "shutil.unpack_archive", "shutil.chown",
    "tempfile.mkstemp", "tempfile.mkdtemp", "sqlite3.connect", "_winapi.CopyFile2",
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

# (sofascore_scraper/ altındaki dosyanın depo köküne göre yolu, satır, çağrı) -> (ilk görüldüğü test, yol)
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
    sofascore_scraper/ çerçevesinin doğrudan çağırdığı Python işlevinin adı; kayıt gereksizse None.

    Standart kitaplık için `modül.işlev` (ör. `shutil.rmtree`, `os.makedirs`): tek bir çağrı her platformda
    aynı ada düşer, içeride hangi sistem çağrılarının yapıldığı (sürüme ve işletim sistemine göre değişir)
    ada karışmaz. Üçüncü taraf paketlerde yalnızca paket adı (ör. `pandas`): iç işlev adları sürümle değişir.

    Tembel yineleyiciler (os.walk, glob.iglob, Path.glob): çağrı anında kitaplığın kendi olayı zaten
    kaydedilir; yineleme sürerken sofascore_scraper/ çerçevesinin altında kitaplığın iç üreteçleri durur ve adları
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
        # Kayıtlardaki modül yolu diskteki harf büyüklüğünü korur (sofascore_scraper/SofaScoreUi.py); karşılaştırmalar
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


STORE_BOUNDARY = BoundaryRecorder(os.path.join(ROOT, "sofascore_scraper"), os.path.join(ROOT, "tests"), [DATA_DIR], follow_env=True)
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


# --- Gölge denetimi: testin kendi yazdıkları (plan maddesi ST-11) ----------------------------------
#
# Karşılaştırma "katalog dosyalarla eşit mi" sorusunu sorar. Test, depo açıkken veri dizinine kendisi dosya yazarsa
# (fixture kurulumu, elle bozma) katalog bunu bilemez. Denetim kancası üç şeyi Store'a bildirir:
#
#   * testin yazdığı yol (`shadow_edited`): dizin "kancasız değişti" olarak işaretlenir;
#   * ürün kodunun, böyle işaretli bir dizine dokunmak üzere olduğu (`shadow_resync`): katalog o anda, imzalara
#     güvenmeden uzlaştırılır (testler dosyaları yerinde, dizinin mtime'ını değiştirmeden düzenleyebilir).
#     Böylece ürün kodu, kataloğu dosyalarla eşit bir dizinde çalışmaya başlar ve yazdıkları gerçekten sınanır;
#   * ürün kodunun yazdığı yol (`shadow_written`): eski düzen köklerine Store'un dışından yazan ürün kodu kalmadı
#     (yazıcılar Store'dadır; eski kancalar FX-15'te kalktı). Böyle bir yazma, ardından katalog eşitlenmezse
#     test sonunda fark olarak bildirilir (deposu hiç açılmamış dizinde bile).
#
# Testin son ürün çağrısından sonra yazdıkları eşitlenmeden kalır; o dizinin karşılaştırması atlanır. Erişimi
# kimin yaptığına çağrı yığını karar verir: içten dışa ilk proje çerçevesi tests/ altındaysa test, sofascore_scraper/
# altındaysa ürün kodudur (sofascore_scraper/store/files.py çerçevesi atlanır: yardımcıyı çağırana bakılır; testler de onunla
# veri dizinini değiştirir, ör. tests/test_cli_migrate.py `files.remove_tree`). Store'un kendi
# erişimleri uzlaştırmayı tetiklemez: bir yazma işleminin ortasında olabilir ve okuma API'sinin testleri
# kataloğun arkasından bozulan dosyaları bilerek kurar.

_WRITE_AUDIT_EVENTS = frozenset({
    "os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.utime", "os.truncate", "os.link", "os.symlink",
    "shutil.rmtree", "shutil.copyfile", "shutil.copytree", "shutil.move", "shutil.unpack_archive",
    "_winapi.CopyFile2",
})
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_BY_TEST, _BY_PRODUCT, _BY_STORE = "test", "product", "store"


class ShadowEdits:
    """Denetim kancasının kaydedicisi: testin yazdıklarını ve ürün kodunun erişimlerini `sofascore_scraper.store.api`'ye bildirir."""

    def __init__(self, src_dir: str, tests_dir: str) -> None:
        self._src = _canonical(src_dir) + os.sep
        self._store = _canonical(os.path.join(src_dir, "store")) + os.sep
        self._store_files = _canonical(os.path.join(src_dir, "store", "files.py"))
        self._tests = _canonical(tests_dir) + os.sep
        self._owners: Dict[str, Optional[str]] = {}  # dosya adı -> _BY_*; None: proje dışı

    def _owner(self, filename: str) -> Optional[str]:
        if filename not in self._owners:
            owner: Optional[str] = None
            if not filename.startswith("<"):
                folded = _canonical(filename)
                if folded.startswith(self._tests):
                    owner = _BY_TEST
                elif folded == self._store_files:
                    owner = None  # dosya yardımcıları: erişimin sahibi onları çağırandır (Store ya da test)
                elif folded.startswith(self._store):
                    owner = _BY_STORE
                elif folded.startswith(self._src):
                    owner = _BY_PRODUCT
            self._owners[filename] = owner
        return self._owners[filename]

    @staticmethod
    def _writes(event: str, args: Tuple[Any, ...]) -> bool:
        if event == "open":
            mode, flags = (args + (None, None))[1:3]
            if isinstance(mode, str):
                return any(letter in mode for letter in "wax+")
            return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)
        return event in _WRITE_AUDIT_EVENTS

    def audit(self, event: str, args: Tuple[Any, ...]) -> None:
        # getattr: modül o an yükleniyor olabilir (kısmen kurulmuş modülde işlevler henüz yoktur)
        api = sys.modules.get("sofascore_scraper.store.api")
        watching = getattr(api, "shadow_watching", None)
        unsynced = getattr(api, "shadow_unsynced", None)
        if watching is None or unsynced is None or not watching():
            return
        writes = self._writes(event, args)
        pending = unsynced()
        if not writes and not pending:
            return
        frame = sys._getframe(2)
        owner = None
        while frame is not None and owner is None:
            owner = self._owner(frame.f_code.co_filename)
            frame = frame.f_back
        if owner == _BY_TEST and writes:
            reports = [api.shadow_edited]
        elif owner == _BY_PRODUCT:
            reports = [api.shadow_resync] if pending else []
            if writes and event != "os.mkdir":  # boş dizin kataloğu değiştirmez (yazıcılar kökleri kurarken açar)
                reports.append(api.shadow_written)
        else:
            return
        for arg in args[:2]:
            path = _audit_path(arg)
            if path is not None:
                for report in reports:
                    report(path)


BOUNDARY_RECORDERS.append(ShadowEdits(os.path.join(ROOT, "sofascore_scraper"), os.path.join(ROOT, "tests")))  # type: ignore[arg-type]


def _resync_before_planning() -> None:
    """
    Gölge denetimi, indirme planı (plan maddesi RD-3). Planlayıcılar (`_needs_detail_fetch`, `refresh_due_ids`,
    `collect_detail_match_ids`) dosyalara dokunmaz, kataloğa sorar; eskiden dosya okudukları için denetim
    kancası testin elle yazdıklarını o anda kataloğa alırdı (`shadow_resync`). Aynı güvence artık planın
    başında verilir: `QueryService.require_current` çağrılırken kancasız değişmiş bir dizin varsa katalog önce
    uzlaştırılır. Okuma API'sinin öteki çağrıları tetiklemez (kataloğun arkasından bozulan dosyaları bilerek
    kuran testler vardır).
    """
    from sofascore_scraper.services.query import QueryService
    from sofascore_scraper.store import api as store_api

    original = QueryService.require_current

    def require_current(self: QueryService) -> None:
        if store_api.shadow_unsynced():
            store_api.shadow_resync(self._store.data_dir)
        original(self)

    QueryService.require_current = require_current  # type: ignore[method-assign]


sys.addaudithook(_boundary_audit_hook)


# --- İş thread'leri --------------------------------------------------------------------------------------
#
# `JobManager.submit(background=True)` işi `job-<tür>` adlı bir thread'de yürütür (işin saati: `job-ticker-…`).
# İşin satırı "bitti" olduktan sonra da thread depoya dokunur: `job` akışına `job.finished` olayını yazar ve
# bitmiş işi okur (`JobManager._finish`, `JobManager.run`). Satırın bittiğini görüp hemen depoyu kapatan bir
# fixture, o an başka bir thread'in kullandığı SQLite bağlantısını kapatır; Python 3.14 bunda süreci
# segmentation fault ile düşürdü. İş deposunu kapatan fixture'lar önce testin başlattığı iş thread'lerini bekler.


def job_threads() -> FrozenSet[threading.Thread]:
    """Şu an yaşayan iş thread'leri (adı `job-` ile başlayanlar)."""
    return frozenset(thread for thread in threading.enumerate() if thread.name.startswith("job-"))


def join_job_threads(before: Collection[threading.Thread] = (), timeout: float = 20.0) -> None:
    """
    `before`da olmayan iş thread'lerinin bitmesini bekler (fixture kurulurken `job_threads()` ile alınır).
    Süre dolduğunda hâlâ yaşayan varsa AssertionError: depo o thread'in altından kapatılmamalı.
    """
    deadline = time.monotonic() + timeout
    started = [thread for thread in job_threads() if thread not in before]
    for thread in started:
        # `threading.enumerate()` başlatılmakta olan thread'i de döndürür: `start()` çağrılmış ama thread henüz
        # koşmaya başlamamıştır (ör. iş thread'inin `JobManager.run` içinde başlattığı `job-ticker-…`). Böyle bir
        # thread'e `join()` RuntimeError verir ("cannot join thread before it is started"). Onu başlatan
        # `Thread.start()` bu olay kurulana kadar döndüğünden olay kısa sürede kurulur; önce onu, sonra bitişi
        # bekleriz. `_started` CPython'un `Thread`'inde 3.10'dan beri vardır (genel bir karşılığı yoktur)
        if not thread._started.wait(max(0.0, deadline - time.monotonic())):  # type: ignore[attr-defined]
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
    never_started = sorted(
        thread.name for thread in started if not thread._started.is_set())  # type: ignore[attr-defined]
    assert not never_started, f"job threads still not started after {timeout} s: {never_started}"
    alive = sorted(thread.name for thread in started if thread.is_alive())
    assert not alive, f"job threads still running after {timeout} s: {alive}"


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
    import sofascore_scraper.utils as utils
    import sofascore_scraper.challenge_solver as cs
    from sofascore_scraper import bridge_health

    monkeypatch.setattr(utils, "_browser_first_until", 0.0)
    bridge_health.reset()  # köprü sağlık durumu da testten teste taşınmaz
    follows = sys.modules.get("sofascore_scraper.services.follows")
    if follows is not None:
        follows.clear_search_cache()  # saklanan SofaScore araması da (FX-20)
    if request.node.get_closest_marker("browser") is None:
        async def _no_real_browser(self):
            raise RuntimeError("tests must not launch a real browser (mark the test with @pytest.mark.browser)")

        monkeypatch.setattr(cs.BrowserBridge, "_launch", _no_real_browser)
    yield


@pytest.fixture(scope="session", autouse=True)
def _planning_resync() -> None:
    """
    `_resync_before_planning`'i oturumun ilk testinden önce kurar. pytest_configure'da değil: ürün modüllerini
    o kadar erken yüklemek loglamanın konsol akışını değiştirir (Windows'ta stderr'e yazan testler bozuldu).
    """
    _resync_before_planning()


@pytest.fixture(autouse=True)
def _close_stores_opened_by_the_test():
    """
    Testin `open_store` ile açtığı depolar test bitince kapatılır. Depolar süreç boyunca kayıt defterinde
    açık kalır (dizin başına bir tane); her testin kendi geçici dizinini açtığı bir oturumda bu, test başına
    birkaç açık SQLite dosyası demektir ve açık dosya sınırına ulaşılır (macOS'ta 256).

    Kapatmadan önce gölge denetimi çalışır: testte ürün kodunun eski düzen köklerine yazdığı ya da Store'un
    temizlediği her veri dizininin kataloğu, aynı ağacın sıfırdan kurulmuş haline eşit olmalıdır
    (`sofascore_scraper.store.api.shadow_check`). Fark, Store'un dışından yazan bir ürün kodunu ya da dizinleyicinin tek kaynağı
    yeniden dizinlerken yeniden kurmadan farklı davrandığını gösterir.
    """
    yield
    api = sys.modules.get("sofascore_scraper.store.api")  # cephe hiç yüklenmediyse açılmış depo da yoktur
    if api is None:
        return
    # Veri dizini silinmiş ama deposu hâlâ açık: Windows açık dosyayı silemez (WinError 32, catalog.db), testin
    # dizini silmesi orada başarısız olur. Linux'ta silme geçer; bu denetim aynı hatayı burada da yakalar.
    orphaned = sorted({str(store.data_dir) for store in api._registry.values()
                       if not store.closed and not store.data_dir.exists()})
    try:
        differences = api.shadow_check() if not orphaned else []
    finally:
        for store in list(api._registry.values()):
            store.close()
    assert not orphaned, ("data folder removed while its Store was still open (fails on Windows); close the "
                          "Store before removing the folder:\n" + "\n".join(orphaned))
    assert not differences, "catalog differs from a rebuild after this test:\n" + "\n".join(differences)
