"""
CLI goldenları: `python main.py <bayraklar>` bugün ne yapıyor? Her bayrak için stdout, stderr, çıkış kodu,
SofaScore'a giden istekler ve yazılan dosyalar.

`main.py` gerçek bir alt süreçte çalışır. Ağ yoktur: alt sürecin PYTHONPATH'ine yalnızca burada eklenen
`cli_env/sitecustomize.py`, istek katmanı yüklendiği anda G-01'in sahte taşıyıcısını (tests/fakes/sofascore.py)
kurar ve süreç kapanırken kaydını dosyaya yazar. Üretim kodu değişmez.

Alt süreç kendi kum havuzunda çalışır (veri, config, .env, log, istek bütçesi, tarayıcı profili ve HOME geçici
dizinde) ve ortamı sıfırdan kurulur: testi çalıştıranın kabuğundaki ayarlar (PROXY_URL, LOG_LEVEL, ...) sızmaz.

Beklenen çıktılar fixtures/cli/*.golden.json'dadır (yeniden üretmek: `UPDATE_GOLDENS=1 python -m pytest
tests/characterization/test_cli_goldens.py`). Bu dosya bugünkü davranışı olduğu gibi kaydeder; doğru olduğunu
söylemez. `main.py`nin davranışını değiştiren iş (plan: P10, P19) goldenları yeniden üretir ve her farkı
PR metninde sayar.

Karşılaştırmadan önce çıktıdan çalıştırma anına bağlı kısımlar ayıklanır:
  - log satırlarının zamanı ve süreç numarası ("LOG INFO Main: ..." kalır)
  - kum havuzunun ve deponun yolları (<SANDBOX>, <REPO>); Windows'ta yol ayırıcısı
  - ilerleme çubuğunun (tqdm) ara durumları ve hızı (yalnızca son durum kalır); dosya adındaki çalıştırma zamanı
  - bir async oturum açıkken yazılan stdout satırlarının sırası: eşzamanlı görevlerin ve thread'lerin çıktısı
    `{"concurrent": [...]}` içinde sıralanmış olarak karşılaştırılır (istek kaydındaki kuralın aynısı)
Dosyalar tests/characterization/__init__.py'deki `snapshot_tree` ile özetlenir ve çalıştırmadan önceki
durumla farkı (eklenen / değişen / silinen) goldena yazılır; `.meta/` altı sayılmaz.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import site
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import pytest

from characterization import STATE_DIR, UPDATE_ENV, WORLD, snapshot_tree
from characterization.test_fetch_flows import _change_score, _make_provisional, _match_dir
from fakes.sofascore import REQUEST_LAYER_MODULES, FakeSofaScore
from src.version import __version__

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "main.py"
CLI_ENV_DIR = Path(__file__).parent / "cli_env"
CLI_FIXTURES = Path(__file__).parent / "fixtures" / "cli"

LEAGUE = 17
TIMEOUT_SECONDS = 120


def _load_cli_env() -> Any:
    """Alt sürecin sitecustomize modülü: ortam değişkeni adları ve işaretler tek yerde dursun diye buradan okunur."""
    spec = importlib.util.spec_from_file_location("_cli_golden_sitecustomize", CLI_ENV_DIR / "sitecustomize.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # etkinleştiren değişkenler bu süreçte yok: hiçbir şey kurmaz
    return module


cli_env = _load_cli_env()

# Alt sürece testi çalıştıranın ortamından geçen değişkenler: yalnızca işletim sisteminin ve Python'un
# çalışması için gerekenler. Uygulamanın okuduğu hiçbir ayar bu listede değildir.
_PASS_THROUGH = (
    "PATH", "TMPDIR", "TZ", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
    # Windows
    "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "OS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS",
)
# Sahtenin göremeyeceği bir istek olursa (ör. yeni bir HTTP kitaplığı) SofaScore'a değil, kimsenin
# dinlemediği yerel bir porta gider ve hemen düşer.
_DEAD_PROXY = "http://127.0.0.1:9"

_LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (?P<level>[A-Z]+)\s+\[\d+\] (?P<rest>.*)$")
# tqdm: "etiket:  50%|█████     | 2/4 [00:00<00:00, 9.1it/s]" ya da, toplam bilinmiyorsa, "etiket: 0it [00:00, ?it/s]"
_TQDM = re.compile(r"^(?P<label>.*?):\s+(?:\d+%\|.*\|\s*(?P<done>\d+/\d+)|(?P<count>\d+)it) \[.*\]$")
# Dosya adındaki çalıştırma zamanı (processed/all_matches_<epoch>.csv)
_EPOCH_IN_NAME = re.compile(r"_\d{9,}(?=\.\w)")
_CONCURRENT_MARK = re.compile(f"({re.escape(cli_env.CONCURRENT_BEGIN)}|{re.escape(cli_env.CONCURRENT_END)})")
# Süreç ömrüne bağlı değerler: izleyicinin olay zamanı ve son maç sayfası anı
_WATCH_VOLATILE = frozenset({"at_utc", "last_event_poll"})
# Beklemesi sayılmayan kaynak: izleyicinin şeridi yalnızca iki istek arası 1 sn'den kısaysa bekler
_UNPINNED_SLEEP_SOURCES = frozenset({"src.throttle"})

# Bir akışın karşılaştırılabilir hali: sıralı satırlar ve {"concurrent": [sıralanmış satırlar]} bölümleri
Stream = List[Union[str, Dict[str, List[str]]]]


# --- kum havuzu -------------------------------------------------------------------------------

@dataclass
class Sandbox:
    """Alt sürecin dokunabileceği her şey: `root` altı goldenda `files` olarak özetlenir."""

    base: Path

    @property
    def root(self) -> Path:
        return self.base / "sandbox"

    @property
    def harness(self) -> Path:
        """Sahte dünyanın ve kayıtların durduğu yer: uygulama burayı görmez, özete girmez."""
        return self.base / "harness"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @property
    def cwd(self) -> Path:
        """Kullanıcının komutu çalıştırdığı dizin (main.py depo köküne chdir eder)."""
        return self.root / "cwd"

    @classmethod
    def create(cls, base: Path, env_lines: Sequence[str] = ()) -> "Sandbox":
        box = cls(base)
        for directory in (box.config, box.cwd, box.root / "home", box.harness):
            directory.mkdir(parents=True)
        (box.config / "leagues.txt").write_text(
            f"# League configuration file\n# Format: League Name: ID\n\nPremier League: {LEAGUE}\n",
            encoding="utf-8", newline="\n",
        )
        (box.config / "league_sports.json").write_text(json.dumps({str(LEAGUE): "football"}), encoding="utf-8")
        box.write_env(*env_lines)
        return box

    def write_env(self, *lines: str) -> None:
        """`.env`: goldenların dayandığı eşzamanlılık ve senaryonun ayarları."""
        text = "\n".join(("# cli golden env", "MAX_CONCURRENT=5", *lines)) + "\n"
        self.env_file.write_text(text, encoding="utf-8", newline="\n")
        # 0600: main.py başlangıçta .env'in izinlerini daraltır ve bunu loglar (yalnızca POSIX); daraltılacak
        # bir şey olmazsa çıktı her platformda aynıdır
        os.chmod(self.env_file, 0o600)

    def environ(self, world: Path) -> Dict[str, str]:
        env = {key: os.environ[key] for key in _PASS_THROUGH if key in os.environ}
        home = str(self.root / "home")
        env.update({
            "HOME": home,
            "USERPROFILE": home,
            "PYTHONPATH": str(CLI_ENV_DIR),
            "PYTHONIOENCODING": "utf-8",
            # Dil: açık ayar yok, ileti dili "C" → her makinede İngilizce (src/language.py)
            "LC_MESSAGES": "C",
            "DATA_DIR": str(self.data),
            "SOFASCORE_CONFIG_DIR": str(self.config),
            "SOFASCORE_ENV_FILE": str(self.env_file),
            "LOG_DIR": str(self.root / "logs"),
            # Ortak istek bütçesi kapalı ve yalıtılmış (tests/conftest.py ile aynı): fetch goldenları da böyle
            "REQUEST_RATE_LIMIT": "0",
            "SOFASCORE_THROTTLE_DIR": str(self.root / "throttle"),
            "SOFASCORE_BROWSER_PROFILE": str(self.root / "browser-profile"),
            "HTTP_PROXY": _DEAD_PROXY,
            "HTTPS_PROXY": _DEAD_PROXY,
            "ALL_PROXY": _DEAD_PROXY,
            cli_env.WORLD_ENV: str(world),
            cli_env.OUT_ENV: str(self.harness / "out"),
            cli_env.REQUEST_LAYER_ENV: ",".join(REQUEST_LAYER_MODULES),
        })
        if site.ENABLE_USER_SITE:  # paketler kullanıcı dizinindeyse HOME değişince kaybolmasın
            env["PYTHONUSERBASE"] = site.getuserbase()
        return env

    # --- çıktının çalıştırma anına bağlı kısımları -----------------------------------------

    def normalise(self, text: str) -> str:
        """Kum havuzunun ve deponun yollarını adlarıyla değiştirir; Windows'ta yol ayırıcısını "/" yapar."""
        replacements = [
            (str(self.root), "<SANDBOX>"),
            (os.path.realpath(self.root), "<SANDBOX>"),
            (str(REPO), "<REPO>"),
            (os.path.realpath(REPO), "<REPO>"),
        ]
        for raw, name in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
            for form in (raw, raw.replace("\\", "/")):
                text = text.replace(form, name)
        if os.sep == "\\":
            text = text.replace("\\", "/")
        return _EPOCH_IN_NAME.sub("_<epoch>", text)

    def normalise_json(self, value: Any) -> Any:
        """Ayrıştırılmış JSON'daki her metne `normalise` uygular (JSON metninde "\\" kaçış karakteridir)."""
        if isinstance(value, dict):
            return {key: self.normalise_json(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.normalise_json(item) for item in value]
        return self.normalise(value) if isinstance(value, str) else value

    def _lines(self, text: str) -> List[str]:
        out: List[str] = []
        progress: Optional[str] = None
        lines = text.split("\n")
        if lines and not lines[-1]:
            lines.pop()  # son satırın sonu; boş satır değil
        for line in lines:
            parts = line.split("\r")
            if len(parts) > 1:  # satır başına dönüp üzerine yazılan satır (ilerleme çubuğu)
                parts = [part for part in parts if part]
            for part in parts:
                bar = _TQDM.match(part)
                if bar:
                    if progress == bar["label"]:
                        out.pop()  # aynı çubuğun önceki durumu
                    progress = bar["label"]
                    out.append(f"<progress> {bar['label']}: {bar['done'] or bar['count']}")
                    continue
                progress = None
                log = _LOG_LINE.match(part)
                out.append(f"LOG {log['level']} {log['rest']}" if log else part.rstrip())
        return out

    def stream(self, raw: bytes) -> Stream:
        """stdout ya da stderr'in karşılaştırılabilir hali (modül belgesindeki ayıklamalar)."""
        text = self.normalise(raw.decode("utf-8", "replace").replace("\r\n", "\n"))
        out: Stream = []
        concurrent = False
        for chunk in _CONCURRENT_MARK.split(text):
            if chunk == cli_env.CONCURRENT_BEGIN:
                concurrent = True
            elif chunk == cli_env.CONCURRENT_END:
                concurrent = False
            elif concurrent:
                out.append({"concurrent": sorted(self._lines(chunk))})
            else:
                out.extend(self._lines(chunk))
        return out

    def snapshot(self) -> Dict[str, Any]:
        """Kum havuzundaki dosyaların özeti; boş dizinler "<empty dir>" olarak görünür."""
        tree = snapshot_tree(self.root)
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root)
            if STATE_DIR in rel.parts:
                continue
            key = rel.as_posix()
            if path.is_dir():
                if not any(path.iterdir()):
                    tree[key + "/"] = "<empty dir>"
            elif path.name == ".env":
                tree[key] = _digest(path.read_text(encoding="utf-8"))
            elif path.name == "watch_events.jsonl":
                tree[key] = [_mask(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]
            elif path.name.startswith("watch_state_") and path.suffix == ".json":
                tree[key] = _mask(json.loads(path.read_text(encoding="utf-8")))
        return tree


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]


def _mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "<volatile>" if k in _WATCH_VOLATILE else _mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Çalıştırmanın dosyalara etkisi. İçi dolan boş dizin "silinmiş" sayılmaz: içindekiler eklenmiştir."""
    changes: Dict[str, Any] = {
        "added": {k: v for k, v in after.items() if k not in before},
        "changed": {k: v for k, v in after.items() if k in before and before[k] != v},
        "removed": sorted(k for k in before if k not in after and not k.endswith("/")),
    }
    return {kind: value for kind, value in changes.items() if value}


def _map_lines(stream: Stream, change: Callable[[str], str]) -> Stream:
    """Akışın her satırına `change` uygular (eşzamanlı bölümler yeniden sıralanır)."""
    return [
        {"concurrent": sorted(change(line) for line in entry["concurrent"])} if isinstance(entry, dict) else change(entry)
        for entry in stream
    ]


# --- çalıştırma -------------------------------------------------------------------------------

@dataclass
class CliRun:
    argv: List[str]
    exit_code: int
    stdout: Stream
    stderr: Stream
    raw_stdout: str  # ayıklanmamış stdout (JSON raporları buradan ayrıştırılır)
    requests: List[Any]  # sahtenin karşılaştırılabilir kaydı (FakeSofaScore.canonical_log)
    sleeps: Dict[str, int]  # kaynağa göre atlanan bekleme sayısı
    files: Dict[str, Any]
    process: Dict[str, Any]  # sitecustomize'ın süreç özeti

    def golden(self) -> Dict[str, Any]:
        return {
            "argv": self.argv,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "request_layer_loaded": self.process["fake_installed"],
            "requests": self.requests,
            "sleeps": self.sleeps,
            "files": self.files,
        }

    def printed(self) -> List[str]:
        """stdout'un log olmayan satırları: kullanıcıya yazılan metin (sıralı bölümlerden)."""
        return [line for line in self.stdout if isinstance(line, str) and not line.startswith("LOG ")]


def run_cli(box: Sandbox, *argv: str, world: Optional[FakeSofaScore] = None, stdin: str = "") -> CliRun:
    """`python main.py argv` çalıştırır; `world` verilmezse fetch goldenlarının dünyası kullanılır."""
    world_path = WORLD
    if world is not None:
        world_path = box.harness / "world.json"
        world_path.write_text(json.dumps(world.to_dict()), encoding="utf-8")
    out_dir = box.harness / "out"
    shutil.rmtree(out_dir, ignore_errors=True)
    before = box.snapshot()
    try:
        proc = subprocess.run(
            [sys.executable, str(MAIN), *argv],
            cwd=box.cwd, env=box.environ(world_path), input=stdin.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"main.py {' '.join(argv)} did not exit in {TIMEOUT_SECONDS} s\nstdout: {exc.stdout!r}\nstderr: {exc.stderr!r}")
    process_log = out_dir / cli_env.PROCESS_LOG
    assert process_log.exists(), (
        f"cli_env/sitecustomize.py was not loaded or could not save its log.\n"
        f"exit code {proc.returncode}\nstderr: {proc.stderr.decode('utf-8', 'replace')}"
    )
    process = json.loads(process_log.read_text(encoding="utf-8"))
    requests: List[Any] = []
    sleeps: Dict[str, int] = {}
    transport_log = out_dir / cli_env.TRANSPORT_LOG
    if transport_log.exists():
        transport = json.loads(transport_log.read_text(encoding="utf-8"))
        requests = transport["canonical"]
        for sleep in transport["sleeps"]:
            if sleep["source"] not in _UNPINNED_SLEEP_SOURCES:
                sleeps[sleep["source"]] = sleeps.get(sleep["source"], 0) + 1
    assert process["fake_installed"] == transport_log.exists()
    return CliRun(
        argv=[box.normalise(arg) for arg in argv],
        exit_code=proc.returncode,
        stdout=box.stream(proc.stdout),
        stderr=box.stream(proc.stderr),
        raw_stdout=proc.stdout.decode("utf-8", "replace"),
        requests=requests,
        sleeps=dict(sorted(sleeps.items())),
        files=_diff(before, box.snapshot()),
        process=process,
    )


def assert_cli_golden(name: str, actual: Dict[str, Any]) -> None:
    """`actual`ı fixtures/cli/{name}.golden.json ile karşılaştırır; UPDATE_GOLDENS=1 ise dosyayı yazar."""
    path = CLI_FIXTURES / f"{name}.golden.json"
    text = json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if os.environ.get(UPDATE_ENV):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"golden missing: {path} (run with {UPDATE_ENV}=1 and review the result)"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(text) == expected, f"{path.name} differs; if the change is intended, regenerate with {UPDATE_ENV}=1"


# --- fixture'lar ------------------------------------------------------------------------------

@dataclass
class Seed:
    box: Sandbox
    run: CliRun


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> Seed:
    """`--headless --update-all`ın boş veri dizinindeki çalıştırması: hem golden hem diğer senaryoların başlangıcı."""
    seed_box = Sandbox.create(tmp_path_factory.mktemp("cli-seed"))
    return Seed(seed_box, run_cli(seed_box, "--headless", "--update-all"))


@pytest.fixture(scope="module")
def settled(tmp_path_factory: pytest.TempPathFactory, seed: Seed) -> Seed:
    """
    Tam güncellemenin ardından bir `--fetch-mode details` çalıştırması: ilkinde boş gelen iki dilim (9100002)
    ikinci kez boş gelir ve kesin "yok" sayılır. Bu durumda yapılacak detay işi kalmamıştır.
    """
    settled_box = Sandbox.create(tmp_path_factory.mktemp("cli-settled"))
    shutil.copytree(seed.box.data, settled_box.data)
    return Seed(settled_box, run_cli(settled_box, "--headless", "--update-all", "--fetch-mode", "details"))


NewBox = Callable[..., Sandbox]


@pytest.fixture
def new_box(tmp_path: Path, request: pytest.FixtureRequest) -> NewBox:
    """
    Kum havuzu üretir. `data`: veri dizininin başlangıcı: verilmezse boş, "seed" tam güncelleme yapılmış
    dizinin, "settled" ardından detayları da tamamlanmış dizinin kopyası.
    """

    def create(name: str = "box", *, data: Optional[str] = None, env_lines: Sequence[str] = ()) -> Sandbox:
        created = Sandbox.create(tmp_path / name, env_lines)
        if data is not None:
            source: Seed = request.getfixturevalue(data)
            assert source.run.exit_code == 0
            shutil.copytree(source.box.data, created.data)
        return created

    return create


@pytest.fixture
def box(new_box: NewBox) -> Sandbox:
    return new_box()


@pytest.fixture
def seeded(new_box: NewBox) -> Sandbox:
    return new_box(data="seed")


@pytest.fixture
def world() -> FakeSofaScore:
    """Senaryonun değiştirebileceği dünya. Bu süreçte kurulmaz: alt sürece JSON olarak verilir."""
    return FakeSofaScore.from_file(WORLD)


# --- alt süreç kurulumunun kendisi -----------------------------------------------------------

def test_cli_env_does_nothing_without_its_variables(box: Sandbox) -> None:
    """
    sitecustomize, etkinleştiren değişkenler yokken hiçbir şey kurmaz: uygulamanın başlattığı alt süreçler
    (PYTHONPATH'i devralırlar, değişkenleri devralmazlar) etkilenmez ve üst sürecin kaydının üzerine yazmaz.
    """
    env = box.environ(WORLD)
    for name in (cli_env.WORLD_ENV, cli_env.OUT_ENV, cli_env.REQUEST_LAYER_ENV):
        del env[name]
    probe = "import sys, sitecustomize; print(sitecustomize.__file__); print([type(f).__name__ for f in sys.meta_path])"

    proc = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)

    loaded_from, finders = proc.stdout.splitlines()
    assert Path(loaded_from).resolve() == (CLI_ENV_DIR / "sitecustomize.py").resolve()
    assert "_AfterImportFinder" not in finders
    assert not (box.harness / "out").exists()


def test_cli_env_hides_its_variables_from_the_application(box: Sandbox) -> None:
    """Değişkenler okunduktan sonra süreç ortamından silinir; sahte, istek katmanı yüklenmeden kurulmaz."""
    env = box.environ(WORLD)
    probe = (
        "import os, sys; "
        "print(sorted(k for k in os.environ if k.startswith('SOFASCORE_CLI_GOLDEN'))); "
        "print([type(f).__name__ for f in sys.meta_path])"
    )

    proc = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)

    leaked, finders = proc.stdout.splitlines()
    assert leaked == "[]"
    assert "_AfterImportFinder" in finders
    process = json.loads((box.harness / "out" / cli_env.PROCESS_LOG).read_text(encoding="utf-8"))
    assert process == {"fake_installed": False, "src_modules": []}
    assert not (box.harness / "out" / cli_env.TRANSPORT_LOG).exists()


# --- erken yanıtlanan bayraklar: --version, --doctor -----------------------------------------

@pytest.mark.parametrize("argv", [["--version"], ["--headless", "--update-all", "--version", "--no-such-flag"]])
def test_version(box: Sandbox, argv: List[str]) -> None:
    """
    Tek satır, yan etkisiz: argümanlar ayrıştırılmadan ve ağır modüller yüklenmeden yanıtlanır (yanındaki
    bayraklar, geçersiz olanlar dahil, yok sayılır); hiçbir dosya ya da dizin oluşmaz.
    """
    run = run_cli(box, *argv)

    assert run.stdout == [f"SofaScore Scraper {__version__}"]
    assert (run.exit_code, run.stderr, run.files) == (0, [], {})
    assert run.process == {"fake_installed": False, "src_modules": ["src", "src.version"]}


# Tarayıcı başlatmayan ve makineye (Python sürümü, kurulu paketler, Node) bağlı olmayan denetimler
DOCTOR_PORTABLE_CHECKS = "profile,data_dir,config_dir,env"


def _doctor_golden(run: CliRun) -> Dict[str, Any]:
    golden = run.golden()
    golden["src_modules"] = run.process["src_modules"]
    return golden


def _doctor_report(box: Sandbox, run: CliRun) -> Dict[str, Any]:
    """stdout'taki JSON raporu; içindeki yollar adlarıyla."""
    report: Dict[str, Any] = box.normalise_json(json.loads(run.raw_stdout))
    return report


def test_doctor_json(box: Sandbox) -> None:
    """`--doctor --json`: stdout yalnızca rapordur; uygulamanın ağır modülleri (istek katmanı, log) yüklenmez."""
    run = run_cli(box, "--doctor", "--json", "--only", DOCTOR_PORTABLE_CHECKS)

    report = _doctor_report(box, run)
    assert (run.exit_code, report["ok"], run.stderr) == (0, True, [])
    assert not run.process["fake_installed"]
    golden = _doctor_golden(run)
    golden["stdout"] = report
    assert_cli_golden("doctor_json", golden)


def test_doctor_reports_env_problems_with_exit_code_1(new_box: NewBox) -> None:
    """Geçersiz .env değeri bir hatadır (fail): rapor (metin ya da JSON) stdout'ta, çıkış kodu 1."""
    box = new_box(env_lines=["REQUEST_TIMEOUT=soon", "USE_PROXY=yes", "API_BASE_URL=https://example.org/api"])

    text = run_cli(box, "--doctor", "--only", "env")
    as_json = run_cli(box, "--doctor", "--json", "--only", "env")

    assert (text.exit_code, as_json.exit_code) == (1, 1)
    golden = _doctor_golden(as_json)
    golden["stdout"] = _doctor_report(box, as_json)
    assert_cli_golden("doctor_env_problems", {"text": _doctor_golden(text), "json": golden})


def test_doctor_json_shape_with_the_machine_dependent_checks(box: Sandbox) -> None:
    """
    Makineye bağlı denetimlerle (python, packages, frontend) raporun biçimi: içerik goldena yazılmaz.
    Tarayıcı denetimi atlanır (gerçek bir Chromium başlatır); --live hiç çalıştırılmaz.
    """
    run = run_cli(box, "--doctor", "--json", "--skip", "browser")

    report = _doctor_report(box, run)
    assert sorted(report) == ["checks", "counts", "language", "ok", "root", "status"]
    assert [check["id"] for check in report["checks"]] == [
        "python", "packages", "profile", "data_dir", "config_dir", "frontend", "env",
    ]
    for check in report["checks"]:
        assert sorted(check) == ["code", "detail", "fix", "fix_command", "id", "label", "status", "summary"]
    assert report["counts"] == {
        status: sum(1 for check in report["checks"] if check["status"] == status) for status in ("ok", "warn", "fail")
    }
    assert (report["root"], report["language"]) == ("<REPO>", "en")
    assert report["ok"] == (report["status"] != "fail")
    assert run.exit_code == (0 if report["ok"] else 1)
    assert (run.requests, run.files) == ([], {})
    assert not run.process["fake_installed"]


# --- kullanım hataları (çıkış kodu 2) --------------------------------------------------------

def _argparse_error(run: CliRun) -> Dict[str, Any]:
    """
    argparse'ın hata çıktısı: kullanım özeti ve seçenek listesinin yazımı Python sürümüne göre değişir;
    goldena yalnızca "<program>: error: ..." satırı (seçenek listesi olmadan) girer.
    """
    golden = run.golden()
    lines = [line for line in run.stderr if isinstance(line, str)]
    errors = [line.split(" (choose from", 1)[0] for line in lines if ": error: " in line]
    assert lines[0].startswith("usage: ") and len(errors) == 1
    golden["stderr"] = errors
    return golden


def test_usage_errors(new_box: NewBox) -> None:
    cases: Dict[str, Dict[str, Any]] = {}
    # Bayrak eksikleri argümanlar ayrıştırıldıktan sonra anlaşılır: arayüz nesnesi kurulmuş, veri dizinleri açılmıştır
    cases["headless_without_an_action"] = run_cli(new_box("headless"), "--headless").golden()
    cases["headless_without_an_action_tr"] = run_cli(
        new_box("headless-tr", env_lines=["APP_LANGUAGE=tr"]), "--headless"
    ).golden()
    cases["watch_without_sport"] = run_cli(new_box("watch-sport"), "--watch", "--event-ids", "9300001").golden()
    cases["watch_without_scope"] = run_cli(new_box("watch-scope"), "--watch", "--sport", "football").golden()
    # Yerel olmayan --host, izin listesi olmadan: sunucu başlatılmaz
    cases["web_public_host_without_allow_list"] = run_cli(
        new_box("web"), "--web", "--host", "0.0.0.0", "--port", "0"
    ).golden()
    cases["unknown_flag"] = _argparse_error(run_cli(new_box("unknown"), "--headless", "--update-all", "--no-such-flag"))
    cases["invalid_choice"] = _argparse_error(run_cli(new_box("choice"), "--headless", "--fetch-mode", "everything"))
    cases["invalid_recheck_mode"] = _argparse_error(run_cli(new_box("recheck"), "--recheck-unavailable", "some"))
    # --doctor kalan argümanları kendi ayrıştırıcısına verir: main.py'nin diğer bayrakları orada hatadır
    cases["doctor_with_another_flag"] = _argparse_error(run_cli(new_box("doctor"), "--headless", "--doctor", "--json"))

    for name, case in cases.items():
        assert case["exit_code"] == 2, name
        assert case["requests"] == [], name
    assert_cli_golden("usage_errors", cases)


def test_help_lists_todays_flags(box: Sandbox) -> None:
    """Yardım metninin kendisi çeviridir (locales/*.json) ve goldena girmez; bayrakların listesi girer."""
    run = run_cli(box, "--help")

    usage = "\n".join(run.printed()).split("\n\n", 1)[0]  # "usage: main.py [-h] [--version] ..." bölümü
    options = sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", usage)))
    assert usage.startswith("usage: main.py")
    assert (run.exit_code, run.stderr, run.requests) == (0, [], [])
    assert_cli_golden("help", {"exit_code": run.exit_code, "options": options, "files": run.files})


def test_interactive_menu_is_the_default_mode(box: Sandbox) -> None:
    """Bayraksız çalıştırma terminal menüsünü açar; "0" çıkar. Menünün metni goldena girmez (3.0'da kalkıyor)."""
    run = run_cli(box, stdin="0\n")

    assert run.exit_code == 0
    assert "LOG INFO Main: İnteraktif mod başlatılıyor" in run.stdout
    assert run.requests == []


# --- --headless --update-all ----------------------------------------------------------------

SAME_AS_ALL_LEAGUES = "<same as headless_update_all>"


def test_headless_update_all(seed: Seed) -> None:
    """Tüm ligler, boş veri dizini: sezonlar → maç programı → detaylar."""
    assert seed.run.exit_code == 0
    assert_cli_golden("headless_update_all", seed.run.golden())


def test_headless_update_one_league(box: Sandbox, seed: Seed) -> None:
    """--league-id: aynı istekler ve aynı dosyalar; kullanıcıya yazılan metin başka bir koddan gelir."""
    run = run_cli(box, "--headless", "--update-all", "--league-id", str(LEAGUE))

    assert run.requests == seed.run.requests
    assert run.files == seed.run.files
    assert_cli_golden(
        "headless_update_league", {**run.golden(), "requests": SAME_AS_ALL_LEAGUES, "files": SAME_AS_ALL_LEAGUES}
    )


def test_headless_update_all_again(seeded: Sandbox) -> None:
    """İkinci çalıştırma: program yeniden istenir, boş gelen dilimler bir kez daha denenir; üçüncüde detay isteği kalmaz."""
    second = run_cli(seeded, "--headless", "--update-all")
    third = run_cli(seeded, "--headless", "--update-all")

    assert (second.exit_code, third.exit_code) == (0, 0)
    assert_cli_golden("headless_update_all_again", {"second_run": second.golden(), "third_run": third.golden()})


def test_headless_details_only(new_box: NewBox, world: FakeSofaScore) -> None:
    """
    --fetch-mode details: program istenmez; tüm ligler ve --league-id aynı istekleri atar, aynı dosyaları yazar.
    Her ihtiyaçtan bir maç vardır (fetch goldenlarındaki job_details_league ile aynı kurulum).
    """
    _change_score(world, 9100010, home=3)
    runs: Dict[str, CliRun] = {}
    for name, extra in (("all_leagues", []), ("one_league", ["--league-id", str(LEAGUE)])):
        box = new_box(name, data="seed")
        (_match_dir(box.data, 9100001) / "statistics.json").unlink()  # kısmi → refill
        shutil.rmtree(_match_dir(box.data, 9100003))  # kayıt yok → full
        _make_provisional(box.data, world, 9100010)  # geçici → refresh (skoru SofaScore'da düzeltilmiş)
        # 9100002: ilk çalıştırmada iki dilimi boş geldi → refill
        runs[name] = run_cli(box, "--headless", "--update-all", "--fetch-mode", "details", *extra, world=world)

    assert runs["one_league"].requests == runs["all_leagues"].requests
    assert runs["one_league"].files == runs["all_leagues"].files
    assert_cli_golden("headless_details", {
        "all_leagues": runs["all_leagues"].golden(),
        "one_league": {**runs["one_league"].golden(), "requests": "<same as all_leagues>", "files": "<same as all_leagues>"},
    })


def test_headless_update_stopped_by_the_breaker(new_box: NewBox, world: FakeSofaScore) -> None:
    """
    SofaScore maç isteklerini 403 ile reddediyor: devre kesilir, neden stderr'e yazılır, çıkış kodu 2.
    Eşik, başarısız olacak istek sayısına (4 maç) eşittir: devre son istekte kesilir ve sonuç, eşzamanlı
    görevlerin sırasına bağlı kalmaz (devre kesilince kalan isteklerin gönderilmediğini fetch testleri sabitler).
    """
    world.fail("/event/*", 403)
    runs = {
        name: run_cli(
            new_box(name, env_lines=["RATE_LIMIT_THRESHOLD_CONSECUTIVE=4"]), "--headless", "--update-all", *extra,
            world=world,
        )
        for name, extra in (("all_leagues", []), ("one_league", ["--league-id", str(LEAGUE)]))
    }

    assert {name: run.exit_code for name, run in runs.items()} == {"all_leagues": 2, "one_league": 2}
    assert_cli_golden("headless_update_breaker", {name: run.golden() for name, run in runs.items()})


def test_headless_update_blocked_from_the_first_request(new_box: NewBox, world: FakeSofaScore) -> None:
    """
    SofaScore her isteği 403 ile reddediyor, eşikler varsayılan: sezon listesi alınamaz, yapılacak iş kalmaz.
    Devre kesilmez (bir ligde tek istek başarısız olur; eşik 20) ve çıkış kodu 0'dır.
    """
    world.fail("*", 403)
    runs = {
        name: run_cli(new_box(name), "--headless", "--update-all", *extra, world=world)
        for name, extra in (("all_leagues", []), ("one_league", ["--league-id", str(LEAGUE)]))
    }

    assert {name: run.exit_code for name, run in runs.items()} == {"all_leagues": 0, "one_league": 0}
    assert_cli_golden("headless_update_blocked", {name: run.golden() for name, run in runs.items()})


@pytest.mark.skipif(os.name != "posix", reason="POSIX izin bitleri gerekir (Windows'ta dizin salt okunur yapılamaz)")
def test_headless_storage_error_exits_with_1(new_box: NewBox) -> None:
    """
    Detaylar diske yazılamıyor (izin yok): iş durur, neden stderr'e yazılır, çıkış kodu 1. --league-id yolunda
    hata doğrudan yükselir; tüm ligler yolunda menü katmanı yutar ve main.py `last_storage_error`dan okur.
    """
    if os.geteuid() == 0:
        pytest.skip("root her dizine yazabilir")
    runs: Dict[str, CliRun] = {}
    for name, extra in (("all_leagues", []), ("one_league", ["--league-id", str(LEAGUE)])):
        box = new_box(name, data="settled")  # yapılacak tek iş, yazılamayacak olan maç
        season_dir = _match_dir(box.data, 9100003).parent
        shutil.rmtree(season_dir / "9100003")
        os.chmod(season_dir, 0o555)
        try:
            runs[name] = run_cli(box, "--headless", "--update-all", "--fetch-mode", "details", *extra)
        finally:
            os.chmod(season_dir, 0o755)

    assert {name: run.exit_code for name, run in runs.items()} == {"all_leagues": 1, "one_league": 1}
    assert_cli_golden("headless_storage_error", {name: run.golden() for name, run in runs.items()})


def test_unexpected_error_exits_with_1(box: Sandbox) -> None:
    """
    Beklenmeyen hata (burada: veri dizininin yerinde bir dosya var): ileti ve log dosyasının yolu stdout'a,
    iz dökümü stderr'e yazılır, çıkış kodu 1. İşletim sisteminin hata metni goldena girmez.
    """
    box.data.write_text("not a directory", encoding="utf-8")

    run = run_cli(box, "--headless", "--update-all")

    assert run.exit_code == 1
    assert any(line.startswith("An unexpected error occurred: ") for line in run.printed())
    assert "Please check the log file for details: <SANDBOX>/logs/sofascore_scraper.log" in run.printed()
    assert "Traceback (most recent call last):" in run.stderr
    assert run.requests == []


# --- --headless --csv-export ----------------------------------------------------------------

def test_headless_csv_export(new_box: NewBox) -> None:
    with_data = run_cli(new_box("with-data", data="seed"), "--headless", "--csv-export")
    empty = run_cli(new_box("empty"), "--headless", "--csv-export")

    assert (with_data.exit_code, empty.exit_code) == (0, 0)
    assert (with_data.requests, empty.requests) == ([], [])
    assert_cli_golden("headless_csv_export", {"with_data": with_data.golden(), "empty_data_dir": empty.golden()})


# --- --config ve --data-dir -----------------------------------------------------------------

def test_config_flag_is_ignored(box: Sandbox, seed: Seed) -> None:
    """
    --config bugün ölü bir bayraktır (docs/design/02-services.md 1.7): ConfigManager tekildir ve main.py yolu
    vermeden önce src/utils.py tarafından kurulmuştur. Başka bir lig dosyası gösterilse de sonuç değişmez.
    """
    other = box.root / "other-leagues.txt"
    other.write_text("LaLiga: 8\n", encoding="utf-8")

    run = run_cli(box, "--headless", "--update-all", "--config", str(other))

    assert run.argv[-2:] == ["--config", "<SANDBOX>/other-leagues.txt"]
    assert {**run.golden(), "argv": seed.run.argv} == seed.run.golden()


def test_data_dir_flag_overrides_the_environment(box: Sandbox, seed: Seed) -> None:
    """--data-dir, DATA_DIR'in önündedir: aynı istekler, aynı dosyalar, başka dizinde."""
    run = run_cli(box, "--headless", "--update-all", "--data-dir", str(box.root / "alt-data"))

    def moved(text: str) -> str:
        return text.replace("alt-data/", "data/", 1) if text.startswith("alt-data/") else text

    assert (run.exit_code, run.requests) == (0, seed.run.requests)
    assert {moved(path): summary for path, summary in run.files["added"].items()} == seed.run.files["added"]
    assert _map_lines(run.stdout, lambda line: line.replace("<SANDBOX>/alt-data", "<SANDBOX>/data")) == seed.run.stdout
