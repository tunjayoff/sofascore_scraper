"""
CLI goldenları: `python main.py <bayraklar>` bugün ne yapıyor? Her bayrak için stdout, stderr, çıkış kodu,
SofaScore'a giden istekler ve yazılan dosyalar.

`main.py` gerçek bir alt süreçte çalışır. P19'dan beri bir geçiş kabuğudur: eski bayraklar yeni CLI'nin
komutlarına çevrilir (src/cli/legacy_flags.py; `--headless --update-all` → `sync`, `--refresh-only` → `refresh`,
`--csv-export` → `export`, `--watch` → `watch --source poll --stdout`, ...), stderr'e tek bir kullanımdan kalkma satırı
yazılır, loglar stderr'e gider ve çıkış kodları yeni tablonundur (src/cli/exit_codes.py: 3 kısmi, 4 devre kesici,
5 depolama, 6 kilit, 130/143 iptal). Terminal menüsü (bayraksız) ve `--web` eskisi gibidir. Ağ yoktur: alt sürecin PYTHONPATH'ine yalnızca burada eklenen
`cli_env/sitecustomize.py`, istek katmanı yüklendiği anda G-01'in sahte taşıyıcısını (tests/fakes/sofascore.py)
kurar ve süreç kapanırken kaydını dosyaya yazar. Üretim kodu değişmez.

Alt süreç kendi kum havuzunda çalışır (veri, config, .env, log, istek bütçesi, tarayıcı profili ve HOME geçici
dizinde) ve ortamı sıfırdan kurulur: testi çalıştıranın kabuğundaki ayarlar (PROXY_URL, LOG_LEVEL, ...) sızmaz.

Beklenen çıktılar fixtures/cli/*.golden.json'dadır (yeniden üretmek: `UPDATE_GOLDENS=1 python -m pytest
tests/characterization/test_cli_goldens.py`). Bu dosya bugünkü davranışı olduğu gibi kaydeder; doğru olduğunu
söylemez. `main.py`nin davranışını değiştiren iş (plan: P10, P19) goldenları yeniden üretir ve her farkı
PR metninde sayar.

P10'dan beri headless kipler (--headless, --refresh-only, --recheck-unavailable) terminal arayüzünü kurmaz:
servis bağlamını kurar, SyncService ve MaintenanceService'i çağırır ve veri dizinine yazarken dizinin yazar
kilidini tutar (--watch: `watcher:<spor>` kilidi). Kilit başka bir süreçteyse çıkış kodu 6'dır
(lease_refused.golden.json).

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

import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import site
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union

import pytest

from characterization import STATE_DIR, UPDATE_ENV, WORLD, snapshot_tree
import detail_records
from characterization.test_fetch_flows import _as_legacy_record, _change_score, _delete_record, _make_provisional
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
# Süreç ömrüne bağlı değerler: eski izleyicinin olay zamanı ve son maç sayfası anı; canlı servisin olay zarfının
# yazılma anı (`ts`, sofascore.event/1)
_WATCH_VOLATILE = frozenset({"at_utc", "last_event_poll", "ts"})
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
            # Makinedeki bir sofascore.toml (proje kökünde ya da kullanıcının config dizininde) sızmasın
            "SOFASCORE_CONFIG": "none",
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


def terminal_ui_modules(run: CliRun) -> List[str]:
    """Süreçte yüklenmiş terminal arayüzü modülleri (src/SofaScoreUi.py, src/ui/): headless kiplerde boş olmalı."""
    return [
        name for name in run.process["src_modules"]
        if name == "src.SofaScoreUi" or name == "src.ui" or name.startswith("src.ui.")
    ]


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
DOCTOR_PORTABLE_CHECKS = "profile,data_dir,config_dir,config,env"


def _doctor_golden(run: CliRun) -> Dict[str, Any]:
    golden = run.golden()
    golden["src_modules"] = run.process["src_modules"]
    return golden


def _doctor_report(box: Sandbox, run: CliRun) -> Dict[str, Any]:
    """stdout'taki JSON zarfının raporu (`ssc doctor --json`un `data`sı); içindeki yollar adlarıyla."""
    envelope = json.loads(run.raw_stdout)
    assert (envelope["ok"], envelope["command"]) == (True, "doctor")
    report: Dict[str, Any] = box.normalise_json(envelope["data"])
    return report


def deprecated(*commands: str) -> str:
    """Eski bayrakların stderr'e yazdığı tek satır (src/cli/legacy_flags.py)."""
    return "main.py flags are deprecated and will be removed; this run is: " + " && ".join(
        f"ssc {command}" for command in commands)


def test_doctor_json(box: Sandbox) -> None:
    """`--doctor --json`: stdout yalnızca rapordur; uygulamanın ağır modülleri (istek katmanı, log) yüklenmez."""
    run = run_cli(box, "--doctor", "--json", "--only", DOCTOR_PORTABLE_CHECKS)

    report = _doctor_report(box, run)
    assert (run.exit_code, report["ok"]) == (0, True)
    assert run.stderr == [deprecated(f"doctor --json --only {DOCTOR_PORTABLE_CHECKS}")]
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
    # P19: `--doctor` `ssc doctor`un takma adı; istek bütçesi denetimi de listede
    assert [check["id"] for check in report["checks"]] == [
        "python", "packages", "profile", "data_dir", "config_dir", "config", "frontend", "env", "budget",
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


def _new_cli_usage_error(run: CliRun) -> Dict[str, Any]:
    """
    Yeni CLI'nin kullanım hatası (stderr: kullanımdan kalkma satırı, hata, kullanım satırı, ipucu). Kullanım
    satırının yazımı Python sürümüne bağlıdır: goldena girmez.
    """
    golden = run.golden()
    golden["stderr"] = [line for line in run.stderr if not (isinstance(line, str) and line.startswith("usage: "))]
    return golden


def test_usage_errors(new_box: NewBox) -> None:
    cases: Dict[str, Dict[str, Any]] = {}
    # Bayrak eksikleri argümanlar ayrıştırıldıktan sonra anlaşılır: servis bağlamı kurulmuş, veri dizinleri açılmıştır
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
    # --doctor kalan argümanları `ssc doctor`a verir: main.py'nin diğer bayrakları orada kullanım hatasıdır
    cases["doctor_with_another_flag"] = _new_cli_usage_error(
        run_cli(new_box("doctor"), "--headless", "--doctor", "--json"))

    for name, case in cases.items():
        assert case["exit_code"] == 2, name
        assert case["requests"] == [], name
        # Kullanım hatası hiçbir şey çalıştırmadan gelir: veri dizini oluşmaz (bölüm 15, satır 46)
        assert not any(path.startswith("data/") for kind in case["files"].values() for path in kind), name
    assert_cli_golden("usage_errors", cases)


def test_help_lists_todays_flags(box: Sandbox) -> None:
    """Yardım metninin kendisi çeviridir (locales/*.json) ve goldena girmez; bayrakların listesi girer."""
    run = run_cli(box, "--help")

    usage = "\n".join(run.printed()).split("\n\n", 1)[0]  # "usage: main.py [-h] [--version] ..." bölümü
    options = sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", usage)))
    assert usage.startswith("usage: main.py")
    assert (run.exit_code, run.stderr, run.requests) == (0, [], [])
    assert_cli_golden("help", {"exit_code": run.exit_code, "options": options, "files": run.files})


def test_without_arguments_a_short_help_replaces_the_menu(box: Sandbox) -> None:
    """
    Terminal menüsü 3.0'da kaldırıldı (P26): bayraksız çalıştırma web arayüzünü (`ssc serve`) ve komutları
    (`ssc --help`) gösteren kısa bir yardımı stderr'e yazar ve 2 ile çıkar. Hiçbir şey çalışmaz: istek yok,
    veri dizini oluşmaz, stdin okunmaz. Yardımın metni çeviridir (locales: cli_no_menu) ve goldena girmez.
    """
    run = run_cli(box, stdin="0\n")

    assert run.exit_code == 2
    assert run.stdout == []
    text = "\n".join(line for line in run.stderr if isinstance(line, str))
    assert "ssc serve" in text and "ssc --help" in text and "sync" in text
    assert run.requests == []
    assert not any(path.startswith("data/") for kind in run.files.values() for path in kind)
    assert terminal_ui_modules(run) == []


# --- --headless --update-all ----------------------------------------------------------------

SAME_AS_ALL_LEAGUES = "<same as headless_update_all>"


def test_headless_update_all(seed: Seed) -> None:
    """
    Tüm ligler, boş veri dizini: sezonlar → maç programı → detaylar. Terminal arayüzü yüklenmez. P19: `ssc sync`
    olarak çalışır; stdout'ta yalnızca sonuç satırı, loglar ve kullanımdan kalkma satırı stderr'de.
    """
    assert seed.run.exit_code == 0
    assert terminal_ui_modules(seed.run) == []
    assert seed.run.stderr[0] == deprecated("sync")
    assert not any(isinstance(line, str) and line.startswith("LOG ") for line in seed.run.stdout)
    assert_cli_golden("headless_update_all", seed.run.golden())


def test_headless_update_one_league(box: Sandbox, seed: Seed) -> None:
    """--league-id: aynı istekler ve aynı dosyalar; iki yol da aynı servis akışıdır (SyncService)."""
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
        detail_records.drop_slices(box.data, 9100001, "statistics")  # kısmi → refill
        _delete_record(box.data, 9100003)  # kayıt yok → full
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
    SofaScore maç isteklerini 403 ile reddediyor: devre kesilir, neden stderr'e yazılır, çıkış kodu 4 (P19'dan önce 2).
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

    assert {name: run.exit_code for name, run in runs.items()} == {"all_leagues": 4, "one_league": 4}
    assert_cli_golden("headless_update_breaker", {name: run.golden() for name, run in runs.items()})


def test_headless_update_blocked_from_the_first_request(new_box: NewBox, world: FakeSofaScore) -> None:
    """
    SofaScore her isteği 403 ile reddediyor, eşikler varsayılan: sezon listesi alınamaz, yapılacak iş kalmaz.
    Devre kesilmez (bir ligde tek istek başarısız olur; eşik 20). Alınamayan sezon listesi başarısız bir liste
    birimidir (P14): iş `partial` biter ve çıkış kodu 3'tür (P19'dan önce 0: bölüm 15'teki "başarısızlık 0 ile
    çıkıyor" kusuru).
    """
    world.fail("*", 403)
    runs = {
        name: run_cli(new_box(name), "--headless", "--update-all", *extra, world=world)
        for name, extra in (("all_leagues", []), ("one_league", ["--league-id", str(LEAGUE)]))
    }

    assert {name: run.exit_code for name, run in runs.items()} == {"all_leagues": 3, "one_league": 3}
    assert_cli_golden("headless_update_blocked", {name: run.golden() for name, run in runs.items()})


@pytest.mark.skipif(os.name != "posix", reason="POSIX izin bitleri gerekir (Windows'ta dizin salt okunur yapılamaz)")
def test_headless_storage_error_exits_with_5(new_box: NewBox) -> None:
    """
    Detaylar diske yazılamıyor (izin yok): iş durur, neden stderr'e yazılır, çıkış kodu 5 (P19'dan önce 1). İki
    yolda da servis StorageError'ı olduğu gibi yükseltir.
    """
    if os.geteuid() == 0:
        pytest.skip("root her dizine yazabilir")
    runs: Dict[str, CliRun] = {}
    for name, extra in (("all_leagues", []), ("one_league", ["--league-id", str(LEAGUE)])):
        box = new_box(name, data="settled")  # yapılacak tek iş, yazılamayacak olan maç
        _delete_record(box.data, 9100003)
        # Maçın v3 dizininin üst dizini (src/store/layout.py `event_dir`): yeni maç oraya yayımlanamaz
        events_dir = box.data / "v3" / "events" / "9" / "100"
        os.chmod(events_dir, 0o555)
        try:
            runs[name] = run_cli(box, "--headless", "--update-all", "--fetch-mode", "details", *extra)
        finally:
            os.chmod(events_dir, 0o755)

    assert {name: run.exit_code for name, run in runs.items()} == {"all_leagues": 5, "one_league": 5}
    assert_cli_golden("headless_storage_error", {name: run.golden() for name, run in runs.items()})


def test_a_data_dir_that_is_a_file_is_a_storage_error(box: Sandbox) -> None:
    """
    Veri dizininin yerinde bir dosya var. P19'dan önce bu beklenmeyen bir hataydı (ileti stdout'ta, iz dökümü,
    çıkış kodu 1); şimdi depolama hatasıdır: stdout boş, okunur ileti stderr'de, iz dökümü yok, çıkış kodu 5.
    İşletim sisteminin hata metni goldena girmez.
    """
    box.data.write_text("not a directory", encoding="utf-8")

    run = run_cli(box, "--headless", "--update-all")

    assert run.exit_code == 5
    assert run.stdout == []
    assert any(isinstance(line, str) and line.startswith("Storage error: ") for line in run.stderr)
    assert "Traceback (most recent call last):" not in run.stderr
    assert run.requests == []


# --- --headless --csv-export ----------------------------------------------------------------

def test_headless_csv_export(new_box: NewBox) -> None:
    """
    `ssc export` olarak çalışır; dosya eskisi gibi match_details/processed/ altına yazılır. Dışa aktarılacak maç
    yoksa dosya yazılmaz ve çıkış kodu 1'dir (`not_found`; P19'dan önce hata satırıyla 0).
    """
    with_data = run_cli(new_box("with-data", data="seed"), "--headless", "--csv-export")
    empty = run_cli(new_box("empty"), "--headless", "--csv-export")

    assert (with_data.exit_code, empty.exit_code) == (0, 1)
    assert [path for path in with_data.files["added"] if path.startswith("data/")] == [
        "data/match_details/processed/all_matches_<epoch>.csv"]
    assert not any(path.startswith("data/") for kind in empty.files.values() for path in kind if path != "data/")
    assert (with_data.requests, empty.requests) == ([], [])
    assert terminal_ui_modules(with_data) == []
    assert_cli_golden("headless_csv_export", {"with_data": with_data.golden(), "empty_data_dir": empty.golden()})


# --- --config ve --data-dir -----------------------------------------------------------------

def test_config_flag_with_a_leagues_file_is_ignored_with_a_warning(box: Sandbox, seed: Seed) -> None:
    """
    --config bir lig dosyası (`.txt`) gösterirse yok sayılır, eskisi gibi; P19'dan beri bunu bir uyarı söyler
    (--config artık yapılandırma dosyasıdır). İstekler, dosyalar ve sonuç aynıdır.
    """
    other = box.root / "other-leagues.txt"
    other.write_text("LaLiga: 8\n", encoding="utf-8")

    run = run_cli(box, "--headless", "--update-all", "--config", str(other))

    assert run.argv[-2:] == ["--config", "<SANDBOX>/other-leagues.txt"]
    warning = [line for line in run.stderr if isinstance(line, str) and "looks like a leagues file" in line]
    assert len(warning) == 1 and warning[0].startswith("Warning: --config <SANDBOX>/other-leagues.txt")
    stderr = [line for line in run.stderr if line not in warning]
    assert {**run.golden(), "argv": seed.run.argv, "stderr": stderr} == seed.run.golden()


def test_config_flag_names_the_config_file(new_box: NewBox) -> None:
    """
    --config yapılandırma dosyasıdır (P19): bozuk bir dosya `config_invalid`, çıkış kodu 2, iz dökümü yok,
    istek yok, veri dizini oluşmaz (bölüm 15: yok sayılan --config ve bozuk dosyanın iz dökümü).
    """
    box = new_box()
    broken = box.root / "sofascore.toml"
    broken.write_text('schema = 1\n[client]\nrate = "fast"\n', encoding="utf-8")

    run = run_cli(box, "--headless", "--update-all", "--config", str(broken))

    assert (run.exit_code, run.requests, run.stdout) == (2, [], [])
    assert any(isinstance(line, str) and line.startswith("Configuration error: ") for line in run.stderr)
    assert "Traceback (most recent call last):" not in run.stderr
    assert not any(path.startswith("data/") for kind in run.files.values() for path in kind)


def test_data_dir_flag_overrides_the_environment(box: Sandbox, seed: Seed) -> None:
    """--data-dir, DATA_DIR'in önündedir: aynı istekler, aynı dosyalar, başka dizinde."""
    run = run_cli(box, "--headless", "--update-all", "--data-dir", str(box.root / "alt-data"))

    def moved(text: str) -> str:
        return text.replace("alt-data/", "data/", 1) if text.startswith("alt-data/") else text

    assert (run.exit_code, run.requests) == (0, seed.run.requests)
    assert {moved(path): summary for path, summary in run.files["added"].items()} == seed.run.files["added"]
    assert _map_lines(run.stdout, lambda line: line.replace("<SANDBOX>/alt-data", "<SANDBOX>/data")) == seed.run.stdout


# --- --refresh-only -------------------------------------------------------------------------

def test_refresh_only(seeded: Sandbox, world: FakeSofaScore) -> None:
    """
    Geçici kayıtlar için yalnızca /event: biri aynı, birinin skoru düzeltilmiş, biri artık 404. Biri alınamadığı
    için iş `partial` biter: çıkış kodu 3 (P19'dan önce 0).
    """
    for event_id in (9100001, 9100003, 9100010):
        _make_provisional(seeded.data, world, event_id)
    _change_score(world, 9100003, home=2)
    world.remove("/event/9100010")

    run = run_cli(seeded, "--refresh-only", world=world)

    assert run.exit_code == 3
    assert terminal_ui_modules(run) == []
    assert_cli_golden("refresh_only", run.golden())


def test_refresh_only_exit_codes(new_box: NewBox, world: FakeSofaScore) -> None:
    """
    0: yenilenecek kayıt yok ya da hepsi yenilendi; 3: en az biri alınamadı (P19'dan önce: hepsi başarısızsa 1,
    biri yenilendiyse 0); 4: devre kesildi, kalanlar denenmedi (P19'dan önce 2).
    """
    nothing_due = run_cli(new_box("nothing-due", data="seed"), "--refresh-only")

    gone = FakeSofaScore.from_file(WORLD)
    all_failed_box = new_box("all-failed", data="seed")
    _make_provisional(all_failed_box.data, gone, 9100001)
    gone.remove("/event/9100001")
    all_failed = run_cli(all_failed_box, "--refresh-only", world=gone)

    # Maçlar sırayla (P13'ten beri yenileme eşzamanlıdır): devreyi hangi isteğin keseceği belirli kalsın
    blocked_box = new_box("blocked", data="seed", env_lines=["RATE_LIMIT_THRESHOLD_CONSECUTIVE=2", "MAX_CONCURRENT=1"])
    for event_id in (9100001, 9100003, 9100010):
        _make_provisional(blocked_box.data, world, event_id)
    world.fail("/event/*", 403)
    breaker = run_cli(blocked_box, "--refresh-only", world=world)
    # --ignore-rate-limit devre kesiciyi kapatır: aynı durumda her maç denenir, sonuç "hepsi başarısız" olur
    ignored = run_cli(blocked_box, "--refresh-only", "--ignore-rate-limit", world=world)

    assert (nothing_due.exit_code, all_failed.exit_code, breaker.exit_code, ignored.exit_code) == (0, 3, 4, 3)
    assert_cli_golden("refresh_only_exit_codes", {
        "nothing_due": nothing_due.golden(),
        "all_failed": all_failed.golden(),
        "stopped_by_breaker": breaker.golden(),
        "breaker_ignored": ignored.golden(),
    })


def test_refresh_legacy_flag(seeded: Sandbox) -> None:
    """
    observation.json'ı olmayan eski kayıt kesin sayılır; --refresh-legacy ile bir kez yenilenir. Gözlemsiz kayıt
    yalnızca eski düzende olabilir: 9100001 önceki bir sürümün kaydına çevrilir (tests/legacy_writer.py).
    """
    _as_legacy_record(seeded.data, 9100001)

    without_flag = run_cli(seeded, "--refresh-only")
    with_flag = run_cli(seeded, "--refresh-only", "--refresh-legacy")
    again = run_cli(seeded, "--refresh-only", "--refresh-legacy")

    assert (without_flag.requests, again.requests) == ([], [])
    assert_cli_golden("refresh_legacy", {
        "without_flag": without_flag.golden(), "with_flag": with_flag.golden(), "with_flag_again": again.golden(),
    })


# --- --recheck-unavailable ------------------------------------------------------------------

def test_recheck_unavailable(new_box: NewBox, settled: Seed) -> None:
    """
    İşaretleri geri almak istek atmaz. Varsayılan kip yalnızca eski sürümden kalan (doğrulanmamış) işaretleri
    açar; `all` kesinleşmiş olanları da. --headless ile birlikte verilirse önce işaretler açılır, sonra indirilir.
    """
    # 9100002'nin iki dilimi ikinci kez boş geldi: kesin "yok"
    steps: Dict[str, Dict[str, Any]] = {"details_run_confirms_markers": settled.run.golden()}
    seeded = new_box(data="settled")
    # Eski sürümden kalma kayıt: 9100001 eski düzende, statistics dilimi doğrulanmadan "yok" sayılmış
    _as_legacy_record(seeded.data, 9100001, drop=("statistics",), unavailable={"statistics": 2})

    default = run_cli(seeded, "--recheck-unavailable")
    assert terminal_ui_modules(default) == []
    steps["default"] = default.golden()
    steps["default_again"] = run_cli(seeded, "--recheck-unavailable", "--league-id", str(LEAGUE)).golden()
    steps["other_league"] = run_cli(seeded, "--recheck-unavailable", "all", "--league-id", "8").golden()
    steps["all_then_download"] = run_cli(
        seeded, "--recheck-unavailable", "all", "--headless", "--update-all", "--fetch-mode", "details"
    ).golden()

    for name in ("default", "default_again", "other_league"):
        assert (steps[name]["exit_code"], steps[name]["requests"]) == (0, []), name
    assert_cli_golden("recheck_unavailable", steps)


# --- veri dizini başka bir sürecin kilidinde ------------------------------------------------

# Veri dizininin bir kilidini alıp "ready <pid>" yazan ve stdin kapanana kadar tutan süreç
_LEASE_HOLDER = """
import os, sys
from src.store import open_store

store = open_store(sys.argv[1])
with store.lease(sys.argv[2], purpose=sys.argv[3]):
    print("ready", os.getpid(), flush=True)
    sys.stdin.readline()
store.close()
"""
# Kilidin alındığı an: eski metinde "2026-10-02 12:00:00 UTC", yeni CLI'nin sahip satırında ISO-8601
_LEASE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2} UTC|T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")


@contextlib.contextmanager
def lease_held_elsewhere(data_dir: Path, name: str, purpose: str) -> Iterator[int]:
    """Blok boyunca `data_dir`in `name` kilidini başka bir süreç tutar; o sürecin pid'ini verir."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _LEASE_HOLDER, str(data_dir), name, purpose],
        cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout is not None
        ready = proc.stdout.readline().split()
        if ready[:1] != ["ready"]:
            proc.kill()
            pytest.fail(f"the lease holder did not start: {proc.communicate()[1]}")
        yield int(ready[1])
    finally:
        if proc.poll() is None:
            try:
                _out, err = proc.communicate("\n", timeout=TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                _out, err = proc.communicate()
            assert proc.returncode == 0, err
        else:
            proc.communicate()


def _refused_golden(run: CliRun, holder_pid: int) -> Dict[str, Any]:
    """Reddedilen çalıştırmanın goldenı: sahibin pid'i, makine adı ve kilidin alındığı an adlarıyla."""
    host = re.escape(socket.gethostname())

    def mask(line: str) -> str:
        line = re.sub(rf"\b(pid|process|süreç:)([ =]){holder_pid}\b", r"\1\2<pid>", line)
        line = re.sub(rf"\b(host|makine:|on)([ =]){host}(?=[, ]|$)", r"\1\2<host>", line)
        return _LEASE_TIME.sub("<time>", line)

    golden = run.golden()
    golden["stdout"] = _map_lines(run.stdout, mask)
    golden["stderr"] = _map_lines(run.stderr, mask)
    return golden


def test_a_second_writer_is_refused_with_exit_code_6(new_box: NewBox) -> None:
    """
    Veri dizininin yazar kilidi başka bir süreçteyken (bir web işi ya da başka bir komut satırı çalıştırması)
    indirme, yenileme ve yeniden denetim başlamaz: kilidi kimin tuttuğu (pid, makine, amaç, başlangıç)
    stderr'e uygulamanın dilinde yazılır, çıkış kodu 6'dır, istek atılmaz ve veri değişmez. CSV dışa aktarma
    yazar kilidi almaz ve çalışır. Kilit bırakılınca aynı komut çalışır. P19: sahip yeni CLI'nin hata satırıyla
    (`job_running`, "Held by process ... on ... since ...") yazılır.
    """
    box = new_box("en", data="seed")
    turkish = new_box("tr", data="seed", env_lines=["APP_LANGUAGE=tr"])
    refused: Dict[str, CliRun] = {}
    cases: Dict[str, Dict[str, Any]] = {}

    with lease_held_elsewhere(box.data, "writer", "job") as pid:
        refused["update_all"] = run_cli(box, "--headless", "--update-all")
        refused["refresh_only"] = run_cli(box, "--refresh-only")
        refused["recheck_unavailable"] = run_cli(box, "--recheck-unavailable")
        refused["recheck_then_update"] = run_cli(box, "--recheck-unavailable", "all", "--headless", "--update-all")
        for name, run in refused.items():
            cases[name] = _refused_golden(run, pid)
        csv_export = run_cli(box, "--headless", "--csv-export")
    with lease_held_elsewhere(turkish.data, "writer", "job") as pid:
        refused["update_all_tr"] = run_cli(turkish, "--headless", "--update-all")
        cases["update_all_tr"] = _refused_golden(refused["update_all_tr"], pid)
    released = run_cli(box, "--refresh-only")

    for name, run in refused.items():
        assert (run.exit_code, run.requests) == (6, []), name
        assert not any(path.startswith("data/") for kind in run.files.values() for path in kind), name
        assert "Traceback" not in "\n".join(line for line in run.stderr if isinstance(line, str)), name
    assert (csv_export.exit_code, released.exit_code) == (0, 0)
    assert list(csv_export.files["added"]) == ["data/match_details/processed/all_matches_<epoch>.csv"]
    cases["csv_export_is_not_refused"] = csv_export.golden()
    cases["after_the_lease_is_released"] = released.golden()
    assert_cli_golden("lease_refused", cases)


# --- --watch --------------------------------------------------------------------------------

LIVE_EVENT = 9300001


def test_watch_event_ids(box: Sandbox, world: FakeSofaScore) -> None:
    """
    P19: `--watch` `ssc watch --source poll --stdout`tur (karar D18: tarayıcı başlatmaz). Olaylar stdout'a satır
    satır (`sofascore.event/1` zarfı), özet stderr'e yazılır; durum ve olay günlüğü depodadır ve yeniden
    başlatmada kaldığı yerden sürer. İlk çalıştırma: maç oynanıyor, --watch-hours dolunca çıkılır. İkinci
    çalıştırma: maç canlı listeden düşmüş ve bitmiş; izlenen tüm maçlar bitince servis kendiliğinden çıkar.
    """
    listed = world.event(LIVE_EVENT)
    listed["homeScore"].update(current=2, display=2, period1=2)  # /event ile canlı liste arasında gol
    world.add("/sport/football/events/live", {"events": [listed]})
    argv = ["--watch", "--sport", "football", "--event-ids", str(LIVE_EVENT)]

    # Süre her turun sonunda denetlenir: sınır ne kadar kısa olursa olsun ilk tur tamamlanır
    first = run_cli(box, *argv, "--watch-hours", "0.000000001", world=world)

    finished = copy.deepcopy(listed)
    finished["status"] = {"code": 100, "description": "Ended", "type": "finished"}
    finished["winnerCode"] = 1
    finished["changes"]["changeTimestamp"] += 5400
    world.add_event(finished)
    world.add("/sport/football/events/live", {"events": []})
    second = run_cli(box, *argv, world=world)

    assert terminal_ui_modules(first) == []
    for run in (first, second):  # olay satırlarındaki zaman ve günlüğün kimliği
        run.stdout = _map_lines(
            run.stdout, lambda line: json.dumps(_mask(json.loads(line)), sort_keys=True) if line.startswith("{") else line
        )
    assert (first.exit_code, second.exit_code) == (0, 0)
    assert_cli_golden("watch_event_ids", {"match_in_play": first.golden(), "restart_after_the_match": second.golden()})


def test_a_second_watcher_for_the_same_sport_is_refused(box: Sandbox) -> None:
    """
    Aynı veri dizininde aynı sporun eski izleyicisi (`watcher:<spor>` kilidi) çalışıyorsa canlı servis başlamaz
    (`instance_running`, çıkış kodu 6, istek yok); yazar kilidi ise başka bir kilittir: bir izleyici çalışırken
    yenileme reddedilmez.
    """
    argv = ["--watch", "--sport", "football", "--event-ids", str(LIVE_EVENT)]

    with lease_held_elsewhere(box.data, "watcher:football", "watch") as pid:
        watch = run_cli(box, *argv)
        refresh = run_cli(box, "--refresh-only")

    assert (watch.exit_code, watch.requests) == (6, [])
    assert refresh.exit_code == 0
    assert_cli_golden("lease_refused_watch", _refused_golden(watch, pid))


# --- --diagnostics --------------------------------------------------------------------------

def test_diagnostics_writes_a_bundle_relative_to_the_invoking_directory(box: Sandbox) -> None:
    """Göreli yol, kullanıcının komutu çalıştırdığı dizine göre çözülür (main.py depo köküne chdir etse de)."""
    run = run_cli(box, "--diagnostics", "out/bundle.zip")

    assert (run.exit_code, run.requests) == (0, [])
    assert run.printed() == ["Diagnostics bundle written: <SANDBOX>/cwd/out/bundle.zip"]
    assert run.stderr == [deprecated("diagnostics --out <SANDBOX>/cwd/out/bundle.zip")]
    assert run.files["added"]["cwd/out/bundle.zip"] == "<binary>"
    assert_cli_golden("diagnostics", run.golden())
