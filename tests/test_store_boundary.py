"""
Store sınırı: DATA_DIR'e yalnızca src/store/ dokunur (docs/design/01-storage.md bölüm 2.1 ve 2.4; plan maddesi ST-04).

İki denetim, tek bir mandal (ratchet):

  statik           src/ altındaki (src/store/ dışı) her modül `ast` ile taranır. Dosya sistemi ve sqlite3
                   çağrıları ile `src.store.<altmodül>` içe aktarmaları ihlaldir.
  çalışma zamanı   tests/conftest.py'deki denetim kancası, testler koşarken test veri dizinine src/store/
                   dışından yapılan erişimleri kaydeder; buradaki testler onları oturumun sonunda değerlendirir.

Bugünkü ihlaller `tests/store_boundary/baseline/<modül>.txt` dosyalarında durur (kaynak modül başına bir dosya),
`işlev:çağrı` satırları olarak:

    [static]
    MatchFetcher._load_round:open x2
    MatchFetcher._save_round:os.makedirs
    [runtime]
    _dir_state:tempfile.mkstemp

  [static]   Kaynakta görülen çağrılar. Aynı işlevdeki aynı çağrının sayısı da tutulur (`x2`); sayı artarsa
             bu da yeni ihlaldir.
  [runtime]  Testler koşarken görülen, ama statik denetimin o işlevde hiçbir şey izlemediği erişimler: yolu
             çalışırken kurulan çağrılar ve statik kuraldan muaf modüllerin DATA_DIR'e dokunduğu yerler.
             [static] altında zaten duran bir işlev için ayrıca satır tutulmaz: aynı ihlal iki kez yazılmaz ve
             eski kodu koşturan yeni bir test listeyi büyütmez.

  * Listede olmayan bir ihlal testi düşürür: yeni kod DATA_DIR'e Store üzerinden erişir.
  * Listede olup artık görülmeyen bir satır da testi düşürür: ihlali gideren PR satırı da siler. Böylece
    listeler yalnızca küçülür; geçişin son PR'ı (ST-28) dizini siler.

Satırları elle düzenlemek yerine:

    STORE_BOUNDARY_UPDATE=prune   python -m pytest   # yalnızca artık görülmeyen satırları siler, sayıları düşürür
    STORE_BOUNDARY_UPDATE=rewrite python -m pytest   # listeleri bugünkü durumdan yeniden yazar: satır EKLEYEBİLİR

`rewrite`, kod bir modülden ötekine taşındığında (ihlal de onunla taşınır) ya da bir işlev yeniden adlandırıldığında
kullanılır; eklenen her satır PR açıklamasında anılmalıdır. Çalışma zamanı bölümü yalnızca bütün testler koşarken
güncellenir (dosya ya da `-k` ile daraltılmış bir çalıştırma görmediği ihlal hakkında bir şey söyleyemez).

Statik denetimin göremedikleri (takma adla ya da `getattr` ile yapılan çağrılar, türü çıkarılamayan `Path`
nesneleri, üçüncü taraf kitaplıkların açtığı dosyalar) çalışma zamanı denetimine kalır; onun göremedikleri
(`os.path.exists` gibi olay üretmeyen çağrılar, testlerin hiç koşturmadığı kod, `DATA_DIR` yerine doğrudan
`data_dir=` ile verilen dizinler) statik denetime.
"""
from __future__ import annotations

import ast
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import pytest

import conftest

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
BASELINE_DIR = Path(__file__).resolve().parent / "store_boundary" / "baseline"
UPDATE = os.getenv("STORE_BOUNDARY_UPDATE", "").strip().lower()
MODULE_SCOPE = "<module>"
IMPORT_PREFIX = "import "
DESIGN = "docs/design/01-storage.md bölüm 2.4"

# Statik dosya sistemi kuralından muaf modüller (01-storage.md 2.4'teki liste): DATA_DIR dışındaki dosyalara
# meşru olarak dokunurlar. Muafiyet yalnızca dosya sistemi çağrılarını kapsar; `src.store.<altmodül>` içe
# aktarma kuralı ve çalışma zamanı denetimi bu modüller için de geçerlidir.
FS_ALLOWLIST: Dict[str, str] = {
    "src/config_manager.py": "yapılandırma dosyaları ve .env",
    "src/config/": "yapılandırma dosyaları ve .env",
    "src/paths.py": "yapılandırma ve tarayıcı profili yolları",
    "src/i18n.py": "çeviri dosyaları (locales/)",
    "src/doctor.py": "ortam yoklamaları",
    "src/throttle.py": "istek bütçesi dosyaları",
    "src/challenge_solver.py": "tarayıcı profili",
    "src/logger.py": "log dosyaları",
    "src/diagnostics.py": "log dosyaları ve tanılama paketi",
    "src/sinks/file.py": "dosya sink'inin kendi çıktı yolu",
    "src/web/app.py": "statik dosyalar (frontend/dist)",
    "src/web/missing_ui.py": "statik dosyalar (yardım sayfası)",
}
# Tasarımın saydığı ama henüz var olmayan modüller (P09 ve P22 ile gelir).
ALLOWLIST_PLANNED = frozenset({"src/config/", "src/sinks/file.py"})

# Diske dokunan işlevler. Çağrı olmasa da adın anılması (ör. `map(os.remove, paths)`) ihlal sayılır.
FS_FUNCTIONS = frozenset({
    "open", "io.open", "io.FileIO", "os.open", "codecs.open", "gzip.open", "bz2.open", "lzma.open", "tarfile.open",
    "os.listdir", "os.scandir", "os.walk", "os.fwalk",
    "os.remove", "os.unlink", "os.rename", "os.renames", "os.replace",
    "os.makedirs", "os.mkdir", "os.rmdir", "os.removedirs",
    "os.stat", "os.lstat", "os.access", "os.chmod", "os.chown", "os.utime", "os.truncate",
    "os.link", "os.symlink", "os.readlink",
    "os.path.exists", "os.path.lexists", "os.path.isfile", "os.path.isdir", "os.path.islink", "os.path.ismount",
    "os.path.getsize", "os.path.getmtime", "os.path.getctime", "os.path.getatime", "os.path.samefile",
    "glob.glob", "glob.iglob",
    "sqlite3.connect",
    "zipfile.ZipFile", "zipfile.is_zipfile",
})
# Bu modüllerin her üyesi ihlaldir (`shutil.*`, `tempfile.*`); yalnızca diske dokunmayanlar ayrık tutulur.
FS_MODULES = frozenset({"shutil", "tempfile"})
FS_MODULE_EXEMPT = frozenset({"shutil.get_terminal_size"})
OS_PATH_MODULES = frozenset({"posixpath", "ntpath", "genericpath"})

# `x.yöntem(...)`: alıcının türü bilinmese de pathlib'e özgü sayılan yöntemler ...
PATH_METHODS_ALWAYS = frozenset({
    "read_text", "read_bytes", "write_text", "write_bytes", "iterdir", "rglob", "is_file", "is_dir", "is_symlink",
    "is_mount", "mkdir", "rmdir", "unlink", "touch", "symlink_to", "hardlink_to", "samefile", "lstat", "readlink",
})
# ... ve adı başka türlerde de geçenler (str.replace, re.Match.group ...): yalnızca alıcı bir Path ise.
PATH_METHODS_IF_PATH = frozenset({
    "open", "exists", "stat", "glob", "rename", "replace", "chmod", "walk", "owner", "group",
})
PATH_TYPES = frozenset({"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.WindowsPath"})
PATH_FACTORIES = PATH_TYPES | {"pathlib.Path.home", "pathlib.Path.cwd"}
PATH_RETURNING_METHODS = frozenset({
    "with_name", "with_suffix", "with_stem", "with_segments", "resolve", "absolute", "expanduser", "joinpath",
    "relative_to",
})
PATH_YIELDING_METHODS = frozenset({"iterdir", "rglob"})
DATAFRAME_WRITERS = frozenset({"to_csv", "to_parquet", "to_excel", "to_pickle", "to_feather", "to_hdf"})
SKIPPED_FIELDS = frozenset({"annotation", "returns"})  # tür açıklamaları çağrı değildir


@dataclass(frozen=True)
class Finding:
    """Bir ihlal: `function` içinde `call`. Modül düzeyindeki kod için function == "<module>"."""

    function: str
    call: str
    line: int

    @property
    def is_import(self) -> bool:
        return self.call.startswith(IMPORT_PREFIX)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.function, self.call)


# --- kaynak tarama -----------------------------------------------------------------------------------


class Scopes:
    """Satır -> o satırı içeren en içteki işlevin ya da sınıfın nitelikli adı (`Sınıf.yöntem`, `dış.iç`)."""

    def __init__(self, tree: ast.AST) -> None:
        self._spans: List[Tuple[int, int, str]] = []
        self._collect(tree, "")

    def _collect(self, node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                self._spans.append((child.lineno, child.end_lineno or child.lineno, name))
                self._collect(child, name + ".")
            else:
                self._collect(child, prefix)

    def qualname(self, line: int) -> str:
        best: Optional[Tuple[int, str]] = None
        for start, end, name in self._spans:
            if start <= line <= end and (best is None or start >= best[0]):
                best = (start, name)
        return best[1] if best else MODULE_SCOPE


def _package_of(module_path: str) -> str:
    """'src/web/routes/data.py' -> 'src.web.routes'; 'src/web/__init__.py' -> 'src.web'."""
    return ".".join(module_path[: -len(".py")].split("/")[:-1])


def _absolute_module(module: Optional[str], level: int, package: str) -> str:
    """`from ..x import y` içindeki modülün tam adı."""
    if level == 0:
        return module or ""
    parts = package.split(".") if package else []
    if level > 1:
        parts = parts[: max(len(parts) - (level - 1), 0)]
    return ".".join(parts + ([module] if module else []))


def _chain(node: ast.AST) -> Optional[List[str]]:
    """`a.b.c` -> ['a', 'b', 'c']; zincirin dibi bir ad değilse None."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return parts[::-1]


def _mentions_path(annotation: Optional[ast.AST]) -> bool:
    if annotation is None:
        return False
    return any(
        (isinstance(n, ast.Name) and n.id in ("Path", "PurePath"))
        or (isinstance(n, ast.Attribute) and n.attr in ("Path", "PurePath"))
        or (isinstance(n, ast.Constant) and isinstance(n.value, str) and "Path" in n.value)
        for n in ast.walk(annotation)
    )


class _Scanner:
    def __init__(self, tree: ast.Module, module_path: str, store_submodules: Set[str]) -> None:
        self.tree = tree
        self.package = _package_of(module_path)
        self.store_submodules = store_submodules
        self.scopes = Scopes(tree)
        self.findings: List[Tuple[int, int, Finding]] = []
        self.aliases: Dict[str, str] = {}
        self.imported_submodules: Set[str] = set()
        self.path_names: Set[str] = set()
        self.path_attrs: Set[str] = set()
        self.path_functions: Set[str] = set()
        self.open_is_builtin = True

    def run(self) -> List[Finding]:
        self._collect_imports()
        self._collect_bindings()
        self._infer_paths()
        self._visit(self.tree)
        return [finding for _line, _col, finding in sorted(self.findings, key=lambda item: item[:2])]

    def _add(self, node: ast.AST, call: str) -> None:
        line = getattr(node, "lineno", 0)
        self.findings.append((line, getattr(node, "col_offset", 0), Finding(self.scopes.qualname(line), call, line)))

    # --- içe aktarmalar -------------------------------------------------------------------------

    def _store_submodule(self, module: str) -> Optional[str]:
        """'src.store.files.x' -> 'src.store.files'; Store kökü ya da başka bir paket için None."""
        if module.startswith("src.store."):
            return module
        return None

    def _collect_imports(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.aliases[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.aliases[top] = top
                    sub = self._store_submodule(alias.name)
                    if sub:
                        self._add(node, IMPORT_PREFIX + sub)
                        self.imported_submodules.add(sub.split(".")[2])
            elif isinstance(node, ast.ImportFrom):
                base = _absolute_module(node.module, node.level, self.package)
                sub = self._store_submodule(base)
                if sub:
                    self._add(node, IMPORT_PREFIX + sub)
                    self.imported_submodules.add(sub.split(".")[2])
                for alias in node.names:
                    if alias.name == "*":
                        if base in FS_MODULES or base in ("os", "os.path", "glob", "sqlite3", "zipfile"):
                            self._add(node, f"{base}.*")
                        continue
                    self.aliases[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
                    if base == "src.store" and alias.name in self.store_submodules:
                        self._add(node, f"{IMPORT_PREFIX}src.store.{alias.name}")
                        self.imported_submodules.add(alias.name)

    def _collect_bindings(self) -> None:
        """`open` adı modülde yeniden tanımlanmışsa (işlev, parametre, atama, import) yerleşik `open` sayılmaz."""
        if "open" in self.aliases:
            self.open_is_builtin = False
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == "open":
                self.open_is_builtin = False
            elif isinstance(node, ast.arg) and node.arg == "open":
                self.open_is_builtin = False
            elif isinstance(node, ast.Name) and node.id == "open" and isinstance(node.ctx, ast.Store):
                self.open_is_builtin = False

    # --- ad çözümleme ---------------------------------------------------------------------------

    def _resolve(self, parts: Sequence[str]) -> Optional[str]:
        """İçe aktarılan adlar üzerinden tam ad: `osp.join` -> 'os.path.join'. Bilinmeyen ad için None."""
        head = parts[0]
        if head in self.aliases:
            full = self.aliases[head].split(".") + list(parts[1:])
        elif head == "open" and len(parts) == 1 and self.open_is_builtin:
            return "open"
        elif head == "__import__" and len(parts) == 1:
            return "__import__"
        else:
            return None
        if full[0] in OS_PATH_MODULES:
            full = ["os", "path"] + full[1:]
        return ".".join(full)

    def _resolved(self, node: ast.AST) -> Optional[str]:
        parts = _chain(node)
        return self._resolve(parts) if parts else None

    def _violation_of(self, name: str, head_target: str) -> Optional[str]:
        parts = name.split(".")
        for size in range(len(parts), 0, -1):
            candidate = ".".join(parts[:size])
            if candidate in FS_FUNCTIONS:
                return candidate
        if len(parts) >= 2 and parts[0] in FS_MODULES:
            member = ".".join(parts[:2])
            return None if member in FS_MODULE_EXEMPT else member
        if len(parts) == 2 and parts[0] == "pandas" and parts[1].startswith("read_"):
            return name
        # Alt modüle kök üzerinden erişim: `from src import store; store.files.write_bytes(...)`
        if (len(parts) >= 3 and parts[:2] == ["src", "store"] and parts[2] in self.store_submodules
                and head_target in ("src", "src.store") and parts[2] not in self.imported_submodules):
            return f"{IMPORT_PREFIX}src.store.{parts[2]}"
        return None

    # --- Path çıkarımı --------------------------------------------------------------------------

    def _is_path(self, node: Optional[ast.AST]) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Name):
            return node.id in self.path_names
        if isinstance(node, ast.Call):
            if self._resolved(node.func) in PATH_FACTORIES:
                return True
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in PATH_RETURNING_METHODS and self._is_path(node.func.value):
                    return True
                return node.func.attr in self.path_functions
            return isinstance(node.func, ast.Name) and node.func.id in self.path_functions
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._is_path(node.left) or self._is_path(node.right)
        if isinstance(node, ast.Attribute):
            if node.attr == "parent" and self._is_path(node.value):
                return True
            return node.attr in self.path_attrs
        if isinstance(node, ast.Subscript):
            value = node.value
            return isinstance(value, ast.Attribute) and value.attr == "parents" and self._is_path(value.value)
        if isinstance(node, ast.IfExp):
            return self._is_path(node.body) or self._is_path(node.orelse)
        if isinstance(node, ast.NamedExpr):
            return self._is_path(node.value)
        return False

    def _yields_paths(self, node: ast.AST) -> bool:
        """`for p in x.iterdir()` / `sorted(x.glob(...))`: döngü değişkeni bir Path'tir."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args \
                and node.func.id in ("sorted", "list", "reversed", "tuple", "tqdm"):
            return self._yields_paths(node.args[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in PATH_YIELDING_METHODS:
                return True
            return node.func.attr == "glob" and self._is_path(node.func.value)
        if isinstance(node, ast.Attribute):
            return node.attr == "parents" and self._is_path(node.value)
        return False

    def _mark_path(self, target: ast.AST) -> bool:
        if isinstance(target, ast.Name) and target.id not in self.path_names:
            self.path_names.add(target.id)
            return True
        if isinstance(target, ast.Attribute) and target.attr not in self.path_attrs:
            self.path_attrs.add(target.attr)
            return True
        return False

    def _infer_paths(self) -> None:
        """
        Hangi adların Path taşıdığını kaba biçimde çıkarır (modül genelinde, ada göre): `Path(...)` atanan
        adlar, `Path` açıklamalı parametreler ve alanlar, `-> Path` dönen işlevler, Path üzerindeki döngüler.
        """
        nodes = list(ast.walk(self.tree))
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _mentions_path(node.returns):
                    self.path_functions.add(node.name)
                args = node.args
                for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                    if arg is not None and _mentions_path(arg.annotation):
                        self.path_names.add(arg.arg)
            elif isinstance(node, ast.AnnAssign) and _mentions_path(node.annotation):
                self._mark_path(node.target)
        changed = True
        while changed:
            changed = False
            for node in nodes:
                if isinstance(node, ast.Assign) and self._is_path(node.value):
                    changed |= any([self._mark_path(target) for target in node.targets])
                elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and self._is_path(node.value):
                    changed |= self._mark_path(node.target)
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and self._yields_paths(node.iter):
                    changed |= self._mark_path(node.target)

    # --- gezinme --------------------------------------------------------------------------------

    def _visit(self, node: ast.AST) -> None:
        if isinstance(node, (ast.Attribute, ast.Name)) and isinstance(node.ctx, ast.Load):
            parts = _chain(node)
            if parts is not None:
                name = self._resolve(parts)
                if name is not None:
                    call = self._violation_of(name, self.aliases.get(parts[0], ""))
                    if call is not None:
                        self._add(node, call)
                return  # zincirin içinde başka ifade yok
        if isinstance(node, ast.Call):
            self._visit_call(node)
        for field, value in ast.iter_fields(node):
            if field in SKIPPED_FIELDS:
                continue
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    self._visit(child)

    def _visit_call(self, node: ast.Call) -> None:
        target = self._resolved(node.func)
        if target in ("importlib.import_module", "__import__") and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                sub = self._store_submodule(first.value)
                if sub:
                    self._add(node, IMPORT_PREFIX + sub)
            return
        if target is not None or not isinstance(node.func, ast.Attribute):
            return  # modül işlevi: ad üzerinden zaten değerlendirildi
        method, receiver = node.func.attr, node.func.value
        if method in DATAFRAME_WRITERS:
            self._add(node, f"DataFrame.{method}")
        elif method in PATH_METHODS_ALWAYS or (method in PATH_METHODS_IF_PATH and self._is_path(receiver)):
            self._add(node, f"Path.{method}")


def scan_source(source: str, module_path: str, store_submodules: Iterable[str] = ()) -> List[Finding]:
    """Bir modülün kaynağındaki bütün ihlaller (muafiyet uygulanmadan), satır sırasıyla."""
    return _Scanner(ast.parse(source), module_path, set(store_submodules)).run()


def store_submodules(src_dir: Path) -> Set[str]:
    store = src_dir / "store"
    if not store.is_dir():
        return set()
    names = {p.stem for p in store.glob("*.py") if p.stem != "__init__"}
    return names | {p.name for p in store.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}


def is_allowlisted(module_path: str) -> bool:
    return any(module_path == entry or (entry.endswith("/") and module_path.startswith(entry))
               for entry in FS_ALLOWLIST)


def iter_modules(src_dir: Path) -> Iterator[Tuple[str, Path]]:
    """src/ altındaki, src/store/ dışındaki her modül: (depo köküne göre yol, dosya)."""
    base = src_dir.parent
    for path in sorted(src_dir.rglob("*.py")):
        rel = path.relative_to(base).as_posix()
        if not rel.startswith(f"{src_dir.name}/store/"):
            yield rel, path


def scan_tree(src_dir: Path) -> Dict[str, List[Finding]]:
    """Modül -> mandala giren ihlaller. Muaf modüllerde yalnızca içe aktarma ihlalleri kalır."""
    submodules = store_submodules(src_dir)
    result: Dict[str, List[Finding]] = {}
    for rel, path in iter_modules(src_dir):
        findings = scan_source(path.read_text(encoding="utf-8"), rel, submodules)
        if is_allowlisted(rel):
            findings = [f for f in findings if f.is_import]
        if findings:
            result[rel] = findings
    return result


# --- baseline dosyaları ------------------------------------------------------------------------------

StaticEntries = Dict[Tuple[str, str], int]
RuntimeEntries = Set[Tuple[str, str]]


@dataclass
class Baseline:
    static: StaticEntries
    runtime: RuntimeEntries

    def __bool__(self) -> bool:
        return bool(self.static or self.runtime)


def baseline_name(module_path: str) -> str:
    """'src/web/routes/data.py' -> 'src.web.routes.data.txt'"""
    return module_path[: -len(".py")].replace("/", ".") + ".txt"


def module_of_baseline(name: str) -> str:
    return name[: -len(".txt")].replace(".", "/") + ".py"


def _split_entry(text: str, where: str) -> Tuple[str, str]:
    function, sep, call = text.partition(":")
    if not sep or not function or not call or function != function.strip() or call != call.strip():
        raise ValueError(f"{where}: `işlev:çağrı` bekleniyordu: {text!r}")
    return function, call


def parse_baseline(text: str, where: str = "baseline") -> Baseline:
    baseline = Baseline({}, set())
    section = ""
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        at = f"{where}:{number}"
        if line in ("[static]", "[runtime]"):
            section = line[1:-1]
        elif section == "static":
            entry, count = line, 1
            head, sep, tail = line.rpartition(" x")
            if sep and tail.isdigit():
                entry, count = head, int(tail)
            key = _split_entry(entry, at)
            if key in baseline.static or count < 1:
                raise ValueError(f"{at}: yinelenen ya da geçersiz satır: {line!r}")
            baseline.static[key] = count
        elif section == "runtime":
            key = _split_entry(line, at)
            if key in baseline.runtime:
                raise ValueError(f"{at}: yinelenen satır: {line!r}")
            baseline.runtime.add(key)
        else:
            raise ValueError(f"{at}: satır bir [static] ya da [runtime] bölümünde olmalı: {line!r}")
    return baseline


def format_baseline(module_path: str, baseline: Baseline) -> str:
    lines = [
        f"# {module_path}: Store sınırı ihlalleri ({DESIGN}). Bu liste yalnızca küçülebilir.",
        "# Bir ihlali gideren PR satırını da siler; açıklama: tests/test_store_boundary.py",
    ]
    if baseline.static:
        lines.append("[static]")
        for (function, call), count in sorted(baseline.static.items()):
            lines.append(f"{function}:{call}" + (f" x{count}" if count > 1 else ""))
    if baseline.runtime:
        lines.append("[runtime]")
        lines.extend(f"{function}:{call}" for function, call in sorted(baseline.runtime))
    return "\n".join(lines) + "\n"


def read_baselines(directory: Path) -> Dict[str, Baseline]:
    """Modül yolu -> baseline. Dizin yoksa boş (geçiş bittiğinde dizin silinir)."""
    result: Dict[str, Baseline] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.txt")):
            result[module_of_baseline(path.name)] = parse_baseline(path.read_text(encoding="utf-8"), path.name)
    return result


def write_baselines(directory: Path, baselines: Dict[str, Baseline]) -> None:
    """Verilen durumu diske yazar; boş kalan modülün dosyasını siler."""
    directory.mkdir(parents=True, exist_ok=True)
    keep = set()
    for module_path, baseline in baselines.items():
        if baseline:
            path = directory / baseline_name(module_path)
            path.write_text(format_baseline(module_path, baseline), encoding="utf-8", newline="\n")
            keep.add(path.name)
    for path in directory.glob("*.txt"):
        if path.name not in keep:
            path.unlink()


# --- karşılaştırma -----------------------------------------------------------------------------------


def count_findings(findings: Dict[str, List[Finding]]) -> Dict[str, StaticEntries]:
    return {module: dict(Counter(f.key for f in items)) for module, items in findings.items()}


@dataclass(frozen=True)
class Drift:
    """Baseline ile bugünkü durum arasındaki tek bir fark."""

    module: str
    function: str
    call: str
    expected: int  # baseline'daki sayı (çalışma zamanı için 0 ya da 1)
    actual: int

    @property
    def entry(self) -> str:
        return f"{self.function}:{self.call}"


def compare_static(found: Dict[str, StaticEntries], baselines: Dict[str, Baseline]) -> Tuple[List[Drift], List[Drift]]:
    """(yeni ihlaller, artık görülmeyen baseline satırları)"""
    new: List[Drift] = []
    stale: List[Drift] = []
    for module in sorted(set(found) | set(baselines)):
        actual = found.get(module, {})
        expected = baselines[module].static if module in baselines else {}
        for key in sorted(set(actual) | set(expected)):
            drift = Drift(module, key[0], key[1], expected.get(key, 0), actual.get(key, 0))
            if drift.actual > drift.expected:
                new.append(drift)
            elif drift.actual < drift.expected:
                stale.append(drift)
    return new, stale


def compare_runtime(observed: Dict[str, RuntimeEntries], baselines: Dict[str, Baseline]) -> Tuple[List[Drift], List[Drift]]:
    new: List[Drift] = []
    stale: List[Drift] = []
    for module in sorted(set(observed) | set(baselines)):
        actual = observed.get(module, set())
        expected = baselines[module].runtime if module in baselines else set()
        new.extend(Drift(module, f, c, 0, 1) for f, c in sorted(actual - expected))
        stale.extend(Drift(module, f, c, 1, 0) for f, c in sorted(expected - actual))
    return new, stale


def _describe_stale(stale: Sequence[Drift], section: str) -> str:
    lines = [f"Baseline'da olup artık görülmeyen {len(stale)} satır ([{section}]). İhlal giderildiyse satırı da silin"
             " (ya da: STORE_BOUNDARY_UPDATE=prune python -m pytest):"]
    for d in stale:
        change = "satırı silin" if d.actual == 0 else f"sayıyı x{d.actual} yapın" if d.actual > 1 else "` xN` ekini silin"
        lines.append(f"  tests/store_boundary/baseline/{baseline_name(d.module)}: `{d.entry}`"
                     f" (baseline {d.expected}, şimdi {d.actual}) -> {change}")
    return "\n".join(lines)


# --- çalışma zamanı kayıtları ------------------------------------------------------------------------


def resolve_records(records: Dict[conftest.BoundaryKey, Tuple[str, str]], root: Path
                    ) -> Dict[str, Dict[Tuple[str, str], Tuple[int, str, str]]]:
    """Kancanın (dosya, satır, çağrı) kayıtları -> modül -> {(işlev, çağrı): (satır, ilk test, yol)}."""
    scopes: Dict[str, Scopes] = {}
    result: Dict[str, Dict[Tuple[str, str], Tuple[int, str, str]]] = {}
    for (module, line, call), (test, path) in sorted(records.items()):
        if module not in scopes:
            scopes[module] = Scopes(ast.parse((root / module).read_text(encoding="utf-8")))
        result.setdefault(module, {}).setdefault((scopes[module].qualname(line), call), (line, test, path))
    return result


def uncovered_runtime(observed: Dict[str, Dict[Tuple[str, str], Tuple[int, str, str]]],
                      findings: Dict[str, List[Finding]]) -> Dict[str, Dict[Tuple[str, str], Tuple[int, str, str]]]:
    """
    Statik denetimin zaten izlediği işlevlerdeki gözlemleri çıkarır. `findings`, `scan_tree`'nin sonucudur
    (muaf modüllerin dosya sistemi çağrıları orada yoktur; onların DATA_DIR erişimleri burada kalır).
    """
    covered = {(module, f.function) for module, items in findings.items() for f in items if not f.is_import}
    result = {}
    for module, items in observed.items():
        kept = {key: value for key, value in items.items() if (module, key[0]) not in covered}
        if kept:
            result[module] = kept
    return result


# === gerçek ağaç: statik denetim =====================================================================


def _update_static(found: Dict[str, StaticEntries], baselines: Dict[str, Baseline]) -> None:
    for module in set(found) | set(baselines):
        baseline = baselines.setdefault(module, Baseline({}, set()))
        actual = found.get(module, {})
        if UPDATE == "rewrite":
            baseline.static = dict(actual)
        else:  # prune: yalnızca küçült
            baseline.static = {k: min(n, actual[k]) for k, n in baseline.static.items() if k in actual}
    write_baselines(BASELINE_DIR, baselines)


def test_update_mode_is_known():
    assert UPDATE in ("", "prune", "rewrite"), "STORE_BOUNDARY_UPDATE yalnızca `prune` ya da `rewrite` olabilir"


def test_static_no_new_violations():
    findings = scan_tree(SRC_DIR)
    found = count_findings(findings)
    baselines = read_baselines(BASELINE_DIR)
    if UPDATE in ("prune", "rewrite"):
        _update_static(found, baselines)
        baselines = read_baselines(BASELINE_DIR)
    new, _stale = compare_static(found, baselines)
    if new:
        lines_of = {(m, f.key): f.line for m, items in findings.items() for f in reversed(items)}
        shown = "\n".join(
            f"  {d.module}:{lines_of[(d.module, (d.function, d.call))]}  {d.entry}  (baseline {d.expected}, şimdi {d.actual})"
            for d in new
        )
        pytest.fail(
            f"Store sınırı: {len(new)} yeni ihlal ({DESIGN}).\n{shown}\n"
            "src/store/ dışındaki kod DATA_DIR'e dokunmaz ve yalnızca `from src.store import ...` kullanır: erişimi\n"
            "Store üzerinden yapın. Kod başka bir modülden taşındıysa ihlal de taşınır: baseline dosyalarını\n"
            "STORE_BOUNDARY_UPDATE=rewrite ile yeniden yazın ve eklenen satırları PR açıklamasında anın.",
            pytrace=False,
        )


def test_static_no_stale_baseline_entries():
    _new, stale = compare_static(count_findings(scan_tree(SRC_DIR)), read_baselines(BASELINE_DIR))
    if stale:
        pytest.fail(_describe_stale(stale, "static"), pytrace=False)


def test_allowlist_names_real_modules_with_a_reason():
    for entry, reason in FS_ALLOWLIST.items():
        assert reason.strip(), f"{entry}: gerekçe yok"
        assert entry.startswith("src/") and not entry.startswith("src/store/"), entry
        target = ROOT / entry
        exists = target.is_dir() if entry.endswith("/") else target.is_file()
        assert exists or entry in ALLOWLIST_PLANNED, f"{entry}: böyle bir modül yok (muafiyet listesinden çıkarın)"
    assert ALLOWLIST_PLANNED <= set(FS_ALLOWLIST)


def test_store_itself_is_not_scanned():
    assert all(not rel.startswith("src/store/") for rel, _path in iter_modules(SRC_DIR))
    assert {"src/match_data_fetcher.py", "src/web/routes/data.py"} <= {rel for rel, _path in iter_modules(SRC_DIR)}
    assert {"errors", "codec", "files", "layout", "manifest"} <= store_submodules(SRC_DIR)


# === gerçek ağaç: çalışma zamanı denetimi (oturumun sonunda) =========================================


def _observed_runtime() -> Dict[str, Dict[Tuple[str, str], Tuple[int, str, str]]]:
    return uncovered_runtime(resolve_records(dict(conftest.STORE_BOUNDARY.records), ROOT), scan_tree(SRC_DIR))


def _stale_runtime_is_meaningful() -> Optional[str]:
    """Görülmeyen bir satırın "giderildi" anlamına gelmesi için koşullar; sağlanmıyorsa nedeni döner."""
    if not conftest.STORE_BOUNDARY.full_run:
        return "testlerin yalnızca bir bölümü koştu: görülmeyen ihlal bir şey kanıtlamaz"
    if os.name == "nt":
        return "Windows'ta bazı testler atlanır (UTC olmayan makinede okuyucu altın dosyaları, POSIX'e özgü testler)"
    return None


@pytest.mark.store_boundary_last
def test_runtime_hook_raised_no_errors():
    assert conftest.STORE_BOUNDARY.errors == [], "denetim kancası hata verdi (tests/conftest.py)"


@pytest.mark.store_boundary_last
def test_runtime_no_new_violations():
    observed = _observed_runtime()
    baselines = read_baselines(BASELINE_DIR)
    entries = {module: set(items) for module, items in observed.items()}
    if UPDATE in ("prune", "rewrite"):
        reason = _stale_runtime_is_meaningful()
        if reason is not None:
            pytest.skip(f"[runtime] bölümleri güncellenmedi: {reason}")
        for module in set(entries) | set(baselines):
            baseline = baselines.setdefault(module, Baseline({}, set()))
            seen = entries.get(module, set())
            baseline.runtime = set(seen) if UPDATE == "rewrite" else baseline.runtime & seen
        write_baselines(BASELINE_DIR, baselines)
        baselines = read_baselines(BASELINE_DIR)
    new, _stale = compare_runtime(entries, baselines)
    if new:
        shown = []
        for d in new:
            line, test, path = observed[d.module][(d.function, d.call)]
            shown.append(f"  {d.module}:{line}  {d.entry}\n      yol: {path}\n"
                         f"      ilk görüldüğü test: {test or '(test dışı: import/toplama)'}")
        pytest.fail(
            f"Store sınırı: testler koşarken {len(new)} yeni ihlal görüldü ({DESIGN}).\n" + "\n".join(shown) + "\n"
            "Test veri dizinine src/store/ dışındaki koddan erişildi: erişimi Store üzerinden yapın. Kod başka bir\n"
            "modülden taşındıysa: STORE_BOUNDARY_UPDATE=rewrite python -m pytest (eklenen satırları PR'da anın).",
            pytrace=False,
        )


@pytest.mark.store_boundary_last
def test_runtime_no_stale_baseline_entries():
    reason = _stale_runtime_is_meaningful()
    if reason is not None:
        pytest.skip(reason)
    entries = {module: set(items) for module, items in _observed_runtime().items()}
    _new, stale = compare_runtime(entries, read_baselines(BASELINE_DIR))
    if stale:
        pytest.fail(_describe_stale(stale, "runtime"), pytrace=False)


@pytest.mark.store_boundary_last
def test_baseline_files_are_canonical_and_name_existing_modules():
    """
    Her dosya var olan bir modüle aittir, boş değildir ve `format_baseline`'ın yazacağı biçimdedir (sıralı).
    Güncelleme kipinde dosyalar oturumun sonunda yazıldığı için bu test de sonda çalışır.
    """
    problems = []
    for path in sorted(BASELINE_DIR.glob("*")) if BASELINE_DIR.is_dir() else []:
        if path.suffix != ".txt":
            problems.append(f"{path.name}: baseline dizininde yalnızca <modül>.txt dosyaları durur")
            continue
        module = module_of_baseline(path.name)
        text = path.read_text(encoding="utf-8")
        baseline = parse_baseline(text, path.name)
        if not (ROOT / module).is_file():
            problems.append(f"{path.name}: {module} yok (modül silindiyse dosyayı da silin)")
        elif module.startswith("src/store/"):
            problems.append(f"{path.name}: src/store/ için baseline tutulmaz")
        elif not baseline:
            problems.append(f"{path.name}: boş (dosyayı silin)")
        elif text != format_baseline(module, baseline):
            problems.append(f"{path.name}: biçim bozuk ya da sırasız (STORE_BOUNDARY_UPDATE=prune yeniden yazar)")
        elif is_allowlisted(module) and any(not call.startswith(IMPORT_PREFIX) for _f, call in baseline.static):
            problems.append(f"{path.name}: {module} dosya sistemi kuralından muaf; [static] altında yalnızca import durur")
        else:
            tracked = {function for function, call in baseline.static if not call.startswith(IMPORT_PREFIX)}
            for function, call in sorted(baseline.runtime):
                if function in tracked:
                    problems.append(f"{path.name}: `{function}:{call}` gereksiz; işlev [static] altında zaten izleniyor")
    assert not problems, "\n".join(problems)


# === denetleyicinin kendi testleri: statik tarama ====================================================

SUBMODULES = ("files", "codec", "errors", "schema")


def calls(source: str, module_path: str = "src/reader.py") -> List[str]:
    return [f"{f.function}:{f.call}" for f in scan_source(source, module_path, SUBMODULES)]


@pytest.mark.parametrize("source, expected", [
    # yerleşik open, modül düzeyinde ve işlev içinde
    ("f = open('x')\n", ["<module>:open"]),
    ("def load(p):\n    with open(p) as f:\n        return f.read()\n", ["load:open"]),
    # tasarımdaki listenin her ailesi
    ("import os\ndef f(p):\n    os.listdir(p)\n    os.scandir(p)\n    os.walk(p)\n",
     ["f:os.listdir", "f:os.scandir", "f:os.walk"]),
    ("import os\ndef f(p, q):\n    os.remove(p)\n    os.unlink(p)\n    os.rename(p, q)\n    os.replace(p, q)\n",
     ["f:os.remove", "f:os.unlink", "f:os.rename", "f:os.replace"]),
    ("import os\ndef f(p):\n    os.makedirs(p)\n    os.mkdir(p)\n    os.rmdir(p)\n    os.stat(p)\n",
     ["f:os.makedirs", "f:os.mkdir", "f:os.rmdir", "f:os.stat"]),
    ("import os\ndef f(p):\n    return os.path.exists(p), os.path.isfile(p), os.path.isdir(p)\n",
     ["f:os.path.exists", "f:os.path.isfile", "f:os.path.isdir"]),
    ("import os\ndef f(p):\n    return os.path.getsize(p), os.path.getmtime(p), os.path.getctime(p)\n",
     ["f:os.path.getsize", "f:os.path.getmtime", "f:os.path.getctime"]),
    ("import glob\ndef f(p):\n    return glob.glob(p), glob.iglob(p)\n", ["f:glob.glob", "f:glob.iglob"]),
    ("import shutil\ndef f(p, q):\n    shutil.rmtree(p)\n    shutil.copy2(p, q)\n    shutil.disk_usage(p)\n",
     ["f:shutil.rmtree", "f:shutil.copy2", "f:shutil.disk_usage"]),
    ("import tempfile\ndef f():\n    return tempfile.mkstemp(), tempfile.NamedTemporaryFile()\n",
     ["f:tempfile.mkstemp", "f:tempfile.NamedTemporaryFile"]),
    ("import sqlite3\ndef f(p):\n    return sqlite3.connect(p)\n", ["f:sqlite3.connect"]),
    ("import zipfile\ndef f(p):\n    return zipfile.ZipFile(p)\n", ["f:zipfile.ZipFile"]),
    ("import pandas as pd\ndef f(p):\n    df = pd.read_csv(p)\n    df.to_csv(p)\n",
     ["f:pandas.read_csv", "f:DataFrame.to_csv"]),
    # takma adlar ve `from` içe aktarmaları
    ("import os as _os\ndef f(p):\n    _os.listdir(p)\n", ["f:os.listdir"]),
    ("from os import listdir, path as osp\ndef f(p):\n    return listdir(p), osp.exists(p)\n",
     ["f:os.listdir", "f:os.path.exists"]),
    ("from os.path import exists as there\ndef f(p):\n    return there(p)\n", ["f:os.path.exists"]),
    ("import os.path as osp\ndef f(p):\n    return osp.isdir(p)\n", ["f:os.path.isdir"]),
    ("import posixpath\ndef f(p):\n    return posixpath.exists(p)\n", ["f:os.path.exists"]),
    ("from shutil import rmtree\ndef f(p):\n    rmtree(p)\n", ["f:shutil.rmtree"]),
    ("from sqlite3 import connect\ndef f(p):\n    return connect(p)\n", ["f:sqlite3.connect"]),
    ("from shutil import *\n", ["<module>:shutil.*"]),
    ("def f(p):\n    import os\n    return os.listdir(p)\n", ["f:os.listdir"]),
    # çağrı olmadan anılan işlev de ihlaldir
    ("import os\ndef f(paths):\n    return list(map(os.remove, paths))\n", ["f:os.remove"]),
    ("import atexit, shutil\natexit.register(shutil.rmtree, 'x', True)\n", ["<module>:shutil.rmtree"]),
    ("import os\ndef f(p):\n    return sorted(p, key=os.path.getmtime)\n", ["f:os.path.getmtime"]),
    # nitelikli adlar: sınıf, iç işlev, lambda ve liste üreteci dıştaki işleve yazılır
    ("import os\nclass A:\n    def m(self, p):\n        return os.listdir(p)\n", ["A.m:os.listdir"]),
    ("import os\nclass A:\n    class B:\n        async def m(self, p):\n            return os.listdir(p)\n",
     ["A.B.m:os.listdir"]),
    ("import os\ndef outer(p):\n    def inner():\n        return os.listdir(p)\n    return inner\n",
     ["outer.inner:os.listdir"]),
    ("import os\ndef f(ps):\n    return [os.stat(p) for p in ps], (lambda p: os.rmdir(p))\n",
     ["f:os.stat", "f:os.rmdir"]),
    # pathlib: türden bağımsız yöntem adları
    ("def f(p):\n    p.mkdir()\n    return p.read_text(), p.is_file(), list(p.iterdir())\n",
     ["f:Path.mkdir", "f:Path.read_text", "f:Path.is_file", "f:Path.iterdir"]),
    # pathlib: adı başka türlerde de geçen yöntemler yalnızca alıcı Path ise
    ("from pathlib import Path\ndef f(p):\n    return Path(p).exists(), Path(p).stat(), Path(p).open()\n",
     ["f:Path.exists", "f:Path.stat", "f:Path.open"]),
    ("from pathlib import Path\ndef f(d: Path):\n    return (d / 'a').exists(), d.parent.glob('*'), d.replace(d)\n",
     ["f:Path.exists", "f:Path.glob", "f:Path.replace"]),
    ("import pathlib\ndef f(raw):\n    target = pathlib.Path(raw).expanduser()\n    return target.exists()\n",
     ["f:Path.exists"]),
    ("from pathlib import Path\nclass A:\n    def __init__(self, raw):\n        self.root = Path(raw)\n"
     "    def m(self):\n        return self.root.exists()\n", ["A.m:Path.exists"]),
    ("from pathlib import Path\ndef base() -> Path:\n    return Path('x')\ndef f():\n    return base().exists()\n",
     ["f:Path.exists"]),
    ("def f(d):\n    for child in sorted(d.iterdir()):\n        if child.exists():\n            child.rename('x')\n",
     ["f:Path.iterdir", "f:Path.exists", "f:Path.rename"]),
    # alt modül içe aktarmaları: yalnızca paket kökü serbest
    ("from src.store import StoreError, open_store\n", []),
    ("import src.store\n", []),
    ("from src.store.files import write_bytes\n", ["<module>:import src.store.files"]),
    ("import src.store.files\n", ["<module>:import src.store.files"]),
    ("import src.store.files as files\n", ["<module>:import src.store.files"]),
    ("from src.store import files, StoreError\n", ["<module>:import src.store.files"]),
    ("from src.store.schema import catalog\n", ["<module>:import src.store.schema"]),
    ("def f():\n    from src.store.codec import read_payload\n    return read_payload\n",
     ["f:import src.store.codec"]),
    ("import importlib\ndef f():\n    return importlib.import_module('src.store.codec')\n",
     ["f:import src.store.codec"]),
    ("def f():\n    return __import__('src.store.codec')\n", ["f:import src.store.codec"]),
    ("from src import store\ndef f(p):\n    return store.files.read_bytes(p)\n", ["f:import src.store.files"]),
    ("import src.store\ndef f(p):\n    return src.store.codec.read_payload(p)\n", ["f:import src.store.codec"]),
])
def test_scanner_finds(source: str, expected: List[str]):
    assert calls(source) == expected


@pytest.mark.parametrize("source", [
    # yol hesabı diske dokunmaz
    "import os\ndef f(p):\n    return os.path.join(p, 'a'), os.path.basename(p), os.path.abspath(p), os.sep\n",
    "from pathlib import Path\ndef f(p):\n    return Path(p).name, Path(p).parent / 'x', Path(p).with_suffix('.gz')\n",
    # aynı adlı ama dosya sistemiyle ilgisiz yöntemler
    "def f(s, m, d):\n    return s.replace('a', 'b'), m.group(1), d.rename(columns={}), s.exists(), d.open()\n",
    "import webbrowser\ndef f(u):\n    webbrowser.open(u)\n",
    "from webbrowser import open\ndef f(u):\n    open(u)\n",
    "def open(url):\n    return url\ndef f(u):\n    return open(u)\n",
    "def f(open, u):\n    return open(u)\n",
    # diske dokunmayan modül üyeleri
    "import shutil, zipfile, sqlite3\ndef f():\n    return shutil.get_terminal_size(), zipfile.ZIP_DEFLATED, sqlite3.Row\n",
    "import pandas as pd\ndef f(rows):\n    return pd.DataFrame(rows).to_dict('records')\n",
    # tür açıklamaları çağrı değildir
    "import sqlite3, zipfile\ndef f(conn: sqlite3.Connection, z: 'zipfile.ZipFile') -> zipfile.ZipFile:\n    return z\n",
    # başka paketlerin `store` adlı modülleri
    "from src.storefront.files import x\nfrom other.store.files import y\n",
    "import json\ndef f(text):\n    return json.loads(text)\n",
])
def test_scanner_ignores(source: str):
    assert calls(source) == []


def test_scanner_resolves_relative_imports_of_store_submodules():
    assert calls("from .store.files import write_bytes\n", "src/fsutil.py") == ["<module>:import src.store.files"]
    assert calls("from ..store import codec\n", "src/web/jobs.py") == ["<module>:import src.store.codec"]
    assert calls("from ...store.errors import StoreError\n", "src/web/routes/data.py") == \
        ["<module>:import src.store.errors"]
    assert calls("from . import files\n", "src/web/routes/data.py") == []
    assert calls("from .store import StoreError\n", "src/fsutil.py") == []
    assert calls("from .store import files\n", "src/services/__init__.py") == []  # src.services.store: başka paket


def test_scanner_counts_every_occurrence_and_reports_lines():
    findings = scan_source("import os\n\ndef f(p):\n    os.listdir(p)\n    return os.listdir(p), open(p)\n", "src/x.py")
    assert [(f.function, f.call, f.line) for f in findings] == [
        ("f", "os.listdir", 4), ("f", "os.listdir", 5), ("f", "open", 5)]
    assert count_findings({"src/x.py": findings}) == {"src/x.py": {("f", "os.listdir"): 2, ("f", "open"): 1}}


def test_scopes_map_lines_to_the_innermost_definition():
    scopes = Scopes(ast.parse(
        "x = 1\n"                 # 1
        "class A:\n"              # 2
        "    y = 2\n"             # 3
        "    @property\n"         # 4
        "    def m(self):\n"      # 5
        "        def inner():\n"  # 6
        "            return 1\n"  # 7
        "        return inner\n"  # 8
        "def f():\n"              # 9
        "    return [\n"          # 10
        "        1]\n"            # 11
    ))
    assert [scopes.qualname(n) for n in range(1, 12)] == [
        "<module>", "A", "A", "A", "A.m", "A.m.inner", "A.m.inner", "A.m", "f", "f", "f"]


# --- sahte bir kaynak ağacı üzerinde uçtan uca --------------------------------------------------------


def make_tree(tmp_path: Path, files: Dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path / "src"


def test_scan_tree_skips_the_store_and_applies_the_allowlist(tmp_path: Path):
    src = make_tree(tmp_path, {
        "src/store/__init__.py": "",
        "src/store/files.py": "import os\ndef write(p):\n    os.replace(p, p)\n",
        "src/reader.py": "import os\nfrom src.store.files import write\ndef load(p):\n    return os.listdir(p)\n",
        "src/clean.py": "from src.store import StoreError\n",
        "src/doctor.py": "import os\nfrom src.store import files\ndef probe(p):\n    return os.path.exists(p)\n",
        "src/web/app.py": "from pathlib import Path\nINDEX = Path('dist').is_dir()\n",
    })
    found = scan_tree(src)
    assert {m: [f"{f.function}:{f.call}" for f in items] for m, items in found.items()} == {
        "src/reader.py": ["<module>:import src.store.files", "load:os.listdir"],
        "src/doctor.py": ["<module>:import src.store.files"],  # muaf modülde yalnızca içe aktarma kuralı kalır
    }


def test_ratchet_passes_only_when_baseline_equals_the_code(tmp_path: Path):
    src = make_tree(tmp_path, {
        "src/store/__init__.py": "",
        "src/reader.py": "import os\ndef load(p):\n    return os.listdir(p), os.listdir(p), open(p)\n",
    })
    directory = tmp_path / "baseline"
    found = count_findings(scan_tree(src))

    # baseline yok: her ihlal yeni
    new, stale = compare_static(found, read_baselines(directory))
    assert [(d.module, d.entry, d.expected, d.actual) for d in new] == [
        ("src/reader.py", "load:open", 0, 1), ("src/reader.py", "load:os.listdir", 0, 2)]
    assert stale == []

    # baseline bugünkü durum: temiz
    write_baselines(directory, {"src/reader.py": Baseline(dict(found["src/reader.py"]), set())})
    assert (directory / "src.reader.txt").read_text(encoding="utf-8").splitlines()[2:] == [
        "[static]", "load:open", "load:os.listdir x2"]
    assert compare_static(found, read_baselines(directory)) == ([], [])

    # aynı işleve üçüncü bir çağrı eklendi: yeni ihlal
    (src / "reader.py").write_text(
        "import os\ndef load(p):\n    return os.listdir(p), os.listdir(p), os.listdir(p), open(p)\n", encoding="utf-8")
    new, stale = compare_static(count_findings(scan_tree(src)), read_baselines(directory))
    assert [(d.entry, d.expected, d.actual) for d in new] == [("load:os.listdir", 2, 3)] and stale == []

    # bir çağrı giderildi, baseline'a dokunulmadı: bayat satır
    (src / "reader.py").write_text("import os\ndef load(p):\n    return os.listdir(p)\n", encoding="utf-8")
    new, stale = compare_static(count_findings(scan_tree(src)), read_baselines(directory))
    assert new == []
    assert [(d.entry, d.expected, d.actual) for d in stale] == [("load:open", 1, 0), ("load:os.listdir", 2, 1)]
    message = _describe_stale(stale, "static")
    assert "src.reader.txt: `load:open` (baseline 1, şimdi 0) -> satırı silin" in message
    assert "`load:os.listdir` (baseline 2, şimdi 1) -> ` xN` ekini silin" in message

    # modül silindi: dosyası bütünüyle bayat
    (src / "reader.py").unlink()
    new, stale = compare_static(count_findings(scan_tree(src)), read_baselines(directory))
    assert new == [] and len(stale) == 2


def test_ratchet_for_runtime_entries():
    baselines = {"src/a.py": Baseline({}, {("f", "open"), ("g", "os.listdir")})}
    assert compare_runtime({"src/a.py": {("f", "open"), ("g", "os.listdir")}}, baselines) == ([], [])
    new, stale = compare_runtime({"src/a.py": {("f", "open")}, "src/b.py": {("h", "pandas")}}, baselines)
    assert [(d.module, d.entry) for d in new] == [("src/b.py", "h:pandas")]
    assert [(d.module, d.entry) for d in stale] == [("src/a.py", "g:os.listdir")]


def test_baseline_text_round_trip_and_canonical_form():
    baseline = Baseline({("B.m", "open"): 1, ("<module>", "import src.store.files"): 1, ("a", "os.walk"): 3},
                        {("B.m", "pandas"), ("a", "shutil.rmtree")})
    text = format_baseline("src/web/x.py", baseline)
    assert text.splitlines() == [
        f"# src/web/x.py: Store sınırı ihlalleri ({DESIGN}). Bu liste yalnızca küçülebilir.",
        "# Bir ihlali gideren PR satırını da siler; açıklama: tests/test_store_boundary.py",
        "[static]",
        "<module>:import src.store.files",
        "B.m:open",
        "a:os.walk x3",
        "[runtime]",
        "B.m:pandas",
        "a:shutil.rmtree",
    ]
    parsed = parse_baseline(text)
    assert (parsed.static, parsed.runtime) == (baseline.static, baseline.runtime)
    assert format_baseline("src/web/x.py", parsed) == text
    assert baseline_name("src/web/x.py") == "src.web.x.txt" and module_of_baseline("src.web.x.txt") == "src/web/x.py"
    assert parse_baseline("# yalnızca açıklama\n\n").static == {} and not parse_baseline("")


@pytest.mark.parametrize("text", [
    "f:open\n",                       # bölüm başlığı yok
    "[static]\nf:open\nf:open\n",     # yinelenen satır
    "[runtime]\nf:open\nf:open\n",
    "[static]\nno-colon\n",
    "[static]\nf:\n",
    "[static]\nf:open x0\n",
])
def test_malformed_baseline_is_rejected(text: str):
    with pytest.raises(ValueError):
        parse_baseline(text)


def test_write_baselines_removes_files_that_became_empty(tmp_path: Path):
    write_baselines(tmp_path, {"src/a.py": Baseline({("f", "open"): 1}, set()), "src/b.py": Baseline({}, {("g", "open")})})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.a.txt", "src.b.txt"]
    write_baselines(tmp_path, {"src/a.py": Baseline({}, set()), "src/b.py": Baseline({}, {("g", "open")})})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.b.txt"]


# === denetleyicinin kendi testleri: çalışma zamanı kancası ===========================================


class Sandbox:
    """
    Geçici bir proje: `src/`, `src/store/`, `tests/` altında sahte modüller ve bir veri dizini. Modüller
    dosya adlarıyla derlenir; böylece çerçeveleri gerçek kancaya o dizinlerdeki kod gibi görünür.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "data"
        self.data.mkdir()
        (self.data / "a.json").write_text("{}", encoding="utf-8")
        self.recorder = conftest.BoundaryRecorder(str(root / "src"), str(root / "tests"), [str(self.data)])

    def module(self, rel: str, source: str, name: str = "sandbox") -> Dict[str, object]:
        namespace: Dict[str, object] = {"__name__": name}
        exec(compile(source, str(self.root / rel), "exec"), namespace)
        return namespace

    def seen(self) -> List[Tuple[str, int, str]]:
        return sorted(self.recorder.records)


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[Sandbox]:
    box = Sandbox(tmp_path)
    conftest.BOUNDARY_RECORDERS.append(box.recorder)
    try:
        yield box
    finally:
        conftest.BOUNDARY_RECORDERS.remove(box.recorder)


def test_hook_records_direct_access_from_src(sandbox: Sandbox):
    reader = sandbox.module("src/reader.py", (
        "import os\n"                          # 1
        "def load(path):\n"                    # 2
        "    with open(path) as f:\n"          # 3
        "        return f.read()\n"            # 4
        "def names(path):\n"                   # 5
        "    return os.listdir(path)\n"        # 6
    ))
    assert reader["load"](str(sandbox.data / "a.json")) == "{}"
    assert reader["names"](str(sandbox.data)) == ["a.json"]
    assert sandbox.seen() == [("src/reader.py", 3, "open"), ("src/reader.py", 6, "os.listdir")]
    test, path = sandbox.recorder.records[("src/reader.py", 3, "open")]
    assert path == str(sandbox.data / "a.json")
    assert test == ""  # yalnızca oturumun kaydedicisi test adını bilir


def test_hook_names_the_library_function_src_called(sandbox: Sandbox):
    """Kitaplığın içeride yaptığı sistem çağrıları (platforma göre değişir) tek bir ada toplanır."""
    writer = sandbox.module("src/writer.py", (
        "import os, shutil, zipfile\n"                         # 1
        "from pathlib import Path\n"                           # 2
        "def run(data):\n"                                     # 3
        "    os.makedirs(os.path.join(data, 'x', 'y'))\n"      # 4
        "    Path(data, 'x', 'n.txt').write_text('1')\n"       # 5
        "    with zipfile.ZipFile(os.path.join(data, 'b.zip'), 'w') as z:\n"  # 6
        "        z.write(os.path.join(data, 'a.json'), 'a.json')\n"           # 7
        "    for _root, _dirs, _files in os.walk(data):\n"     # 8
        "        pass\n"                                       # 9
        "    shutil.rmtree(os.path.join(data, 'x'))\n"         # 10
    ))
    writer["run"](str(sandbox.data))
    assert sandbox.seen() == [
        ("src/writer.py", 4, "os.makedirs"),
        ("src/writer.py", 5, "pathlib.Path.write_text"),
        ("src/writer.py", 6, "zipfile.ZipFile"),
        ("src/writer.py", 7, "zipfile.ZipFile.write"),
        ("src/writer.py", 8, "os.walk"),
        ("src/writer.py", 10, "shutil.rmtree"),
    ]


def test_hook_names_lazy_iterators_once_whatever_the_python_version(sandbox: Sandbox):
    """
    Yineleme sürerken src/ çerçevesinin altında kitaplığın iç üreteçleri durur (3.11'e kadar `os._walk`,
    `glob._iglob`, 3.13'ten beri pathlib'in glob yardımcıları). Kayıtta yalnızca çağrılan işlevin adı kalır.
    """
    (sandbox.data / "sub").mkdir()
    (sandbox.data / "sub" / "b.json").write_text("{}", encoding="utf-8")
    lazy = sandbox.module("src/lazy.py", (
        "import glob, os\n"                                             # 1
        "from pathlib import Path\n"                                    # 2
        "def run(data):\n"                                              # 3
        "    seen = []\n"                                               # 4
        "    for root, _dirs, files in os.walk(data):\n"                # 5
        "        seen += files\n"                                       # 6
        "    for name in glob.iglob(os.path.join(data, '**', '*.json'), recursive=True):\n"  # 7
        "        seen.append(name)\n"                                   # 8
        "    for child in Path(data).glob('*/*.json'):\n"               # 9
        "        seen.append(child)\n"                                  # 10
        "    seen += sorted(Path(data).rglob('*.json'))\n"              # 11
        "    for child in Path(data).iterdir():\n"                      # 12
        "        seen.append(child)\n"                                  # 13
        "    seen += glob.glob(os.path.join(data, '*', '*.json'))\n"    # 14
        "    return seen\n"                                             # 15
    ))
    assert len(lazy["run"](str(sandbox.data))) == 10
    assert sandbox.seen() == [
        ("src/lazy.py", 5, "os.walk"),
        ("src/lazy.py", 7, "glob.iglob"),
        ("src/lazy.py", 9, "pathlib.Path.glob"),
        ("src/lazy.py", 11, "pathlib.Path.rglob"),
        ("src/lazy.py", 12, "pathlib.Path.iterdir"),
        ("src/lazy.py", 14, "glob.glob"),
    ]


def test_hook_names_third_party_packages_by_their_top_level_name(sandbox: Sandbox):
    library = sandbox.module("site-packages/fastframes/io/parsers.py", (
        "def _open(path):\n"
        "    return open(path).read()\n"
        "def read_table(path):\n"
        "    return _open(path)\n"
    ), name="fastframes.io.parsers")
    reader = sandbox.module("src/reader.py", "def load(read, path):\n    return read(path)\n")
    reader["load"](library["read_table"], str(sandbox.data / "a.json"))
    assert sandbox.seen() == [("src/reader.py", 2, "fastframes")]


def test_hook_keeps_the_case_of_the_module_path(sandbox: Sandbox):
    """Baseline dosyaları modül yolunu olduğu gibi taşır (src/SofaScoreUi.py); Windows'ta da küçültülmemeli."""
    module = sandbox.module("src/ui/MatchUi.py", "import os\ndef names(path):\n    return os.listdir(path)\n")
    module["names"](str(sandbox.data))
    assert sandbox.seen() == [("src/ui/MatchUi.py", 3, "os.listdir")]


def test_hook_ignores_access_through_the_store(sandbox: Sandbox):
    files = sandbox.module("src/store/files.py", "def read(path):\n    return open(path).read()\n")
    reader = sandbox.module("src/reader.py", "def load(read, path):\n    return read(path)\n")
    assert reader["load"](files["read"], str(sandbox.data / "a.json")) == "{}"
    assert sandbox.seen() == []


def test_hook_ignores_access_made_by_test_code(sandbox: Sandbox):
    """Fixture kuran test kodu serbesttir; src/ içinden çağrılan bir test yardımcısı da (ör. sahte yazıcı)."""
    helper = sandbox.module("tests/helper.py", "def seed(path):\n    open(path, 'w').close()\n")
    helper["seed"](str(sandbox.data / "seeded.json"))
    caller = sandbox.module("src/caller.py", "def run(callback, path):\n    return callback(path)\n")
    caller["run"](helper["seed"], str(sandbox.data / "seeded.json"))
    assert sandbox.seen() == []


def test_hook_walks_past_generated_code_without_a_file(sandbox: Sandbox, monkeypatch):
    """`<string>` gibi dosyası olmayan çerçeveler (dataclass'ın ürettiği kod, exec) hangi dizinde olunursa olunsun atlanır."""
    (sandbox.root / "tests").mkdir()
    monkeypatch.chdir(sandbox.root / "tests")  # "<string>" göreli bir yol sayılsaydı tests/ altına düşerdi
    generated: Dict[str, Any] = {}
    exec(compile("def read(path):\n    return open(path).read()\n", "<string>", "exec"), generated)
    reader = sandbox.module("src/reader.py", "def load(read, path):\n    return read(path)\n")
    assert reader["load"](generated["read"], str(sandbox.data / "a.json")) == "{}"
    assert sandbox.seen() == [("src/reader.py", 2, "<dynamic>")]


def test_hook_ignores_paths_outside_the_data_dir(sandbox: Sandbox, tmp_path: Path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data-other").mkdir()
    reader = sandbox.module("src/reader.py", (
        "import os\n"
        "def load(path):\n"
        "    return open(path).read()\n"
        "def names(path):\n"
        "    return os.listdir(path)\n"
    ))
    reader["load"](str(tmp_path / "config.json"))
    reader["names"](str(tmp_path / "data-other"))  # "data" ile başlayan kardeş dizin veri dizini değildir
    reader["names"](str(tmp_path))
    assert sandbox.seen() == []
    reader["names"](str(sandbox.data))  # dizinin kendisi içeride sayılır
    assert sandbox.seen() == [("src/reader.py", 5, "os.listdir")]


def test_hook_sees_relative_paths_path_objects_bytes_and_the_second_argument(sandbox: Sandbox, monkeypatch):
    reader = sandbox.module("src/reader.py", (
        "import os\n"                             # 1
        "def load(path):\n"                       # 2
        "    return open(path).read()\n"          # 3
        "def move(src, dst):\n"                   # 4
        "    os.replace(src, dst)\n"              # 5
    ))
    monkeypatch.chdir(sandbox.root)
    reader["load"](os.path.join("data", "a.json"))
    assert sandbox.seen() == [("src/reader.py", 3, "open")]
    sandbox.recorder.records.clear()
    reader["load"](sandbox.data / "a.json")
    reader["load"](os.fsencode(str(sandbox.data / "a.json")))
    reader["load"](os.path.join(str(sandbox.data), "..", "data", "a.json"))
    assert sandbox.seen() == [("src/reader.py", 3, "open")]
    sandbox.recorder.records.clear()
    outside = sandbox.root / "incoming.json"
    outside.write_text("{}", encoding="utf-8")
    reader["move"](str(outside), str(sandbox.data / "moved.json"))  # kaynak dışarıda, hedef veri dizininde
    assert sandbox.seen() == [("src/reader.py", 5, "os.rename")]


def test_hook_sees_sqlite_connections_including_uri_form(sandbox: Sandbox):
    db = sandbox.module("src/db.py", (
        "import sqlite3\n"
        "def plain(path):\n"
        "    sqlite3.connect(path).close()\n"
        "def readonly(uri):\n"
        "    sqlite3.connect(uri, uri=True).close()\n"
    ))
    path = sandbox.data / "state.db"
    db["plain"](str(path))
    db["readonly"](f"{path.as_uri()}?mode=ro")
    assert sandbox.seen() == [("src/db.py", 3, "sqlite3.connect"), ("src/db.py", 5, "sqlite3.connect")]


def test_hook_follows_the_data_dir_environment_variable(tmp_path: Path, monkeypatch):
    """Oturumun kaydedicisi gibi kurulan bir kaydedici, DATA_DIR'in o anki değerini de veri dizini sayar."""
    fixed, other = tmp_path / "fixed", tmp_path / "other"
    for directory in (fixed, other):
        directory.mkdir()
    recorder = conftest.BoundaryRecorder(str(tmp_path / "src"), str(tmp_path / "tests"), [str(fixed)], follow_env=True)
    source = "import os\ndef names(path):\n    return os.listdir(path)\n"
    namespace: Dict[str, object] = {}
    exec(compile(source, str(tmp_path / "src" / "reader.py"), "exec"), namespace)
    conftest.BOUNDARY_RECORDERS.append(recorder)
    try:
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "elsewhere"))
        namespace["names"](str(other))
        assert recorder.records == {}
        monkeypatch.setenv("DATA_DIR", str(other))
        namespace["names"](str(other))
        namespace["names"](str(fixed))  # sabit dizin her zaman izlenir
        assert len(recorder.records) == 1 and {path for _test, path in recorder.records.values()} == {str(other)}
        monkeypatch.delenv("DATA_DIR")
        recorder.records.clear()
        namespace["names"](str(other))
        namespace["names"](str(fixed))
        assert {path for _test, path in recorder.records.values()} == {str(fixed)}
    finally:
        conftest.BOUNDARY_RECORDERS.remove(recorder)


def test_hook_can_be_told_about_another_data_dir(sandbox: Sandbox, tmp_path: Path):
    extra = tmp_path / "second-data"
    extra.mkdir()
    reader = sandbox.module("src/reader.py", "import os\ndef names(path):\n    return os.listdir(path)\n")
    reader["names"](str(extra))
    assert sandbox.seen() == []
    sandbox.recorder.add_data_dir(str(extra))
    reader["names"](str(extra))
    assert sandbox.seen() == [("src/reader.py", 3, "os.listdir")]


def test_session_recorder_watches_the_test_data_dir_and_ignores_this_module():
    recorder = conftest.STORE_BOUNDARY
    assert conftest.BOUNDARY_RECORDERS[0] is recorder
    before = dict(recorder.records)
    assert os.listdir(conftest.DATA_DIR)  # tests/ çerçevesi: kaydedilmez
    with os.scandir(os.path.join(conftest.DATA_DIR, "seasons")) as entries:
        assert [entry.name for entry in entries]
    assert recorder.records == before
    assert all(module.startswith("src/") and not module.startswith("src/store/") for module, _l, _c in recorder.records)


def _fake_config(args: Sequence[str] = ("tests",), source: str = "TESTPATHS", **options: Any) -> Any:
    defaults = dict(keyword="", markexpr="not live and not browser", lf=False, stepwise=False, deselect=None,
                    ignore=None, ignore_glob=None)
    return SimpleNamespace(
        option=SimpleNamespace(**{**defaults, **options}),
        getini=lambda name: ["-m", "not live and not browser"] if name == "addopts" else [],
        args_source=SimpleNamespace(name=source),
        args=list(args),
        invocation_params=SimpleNamespace(dir=ROOT),
    )


def test_stale_entries_are_judged_only_when_the_whole_suite_runs():
    full = conftest._is_full_run
    assert full(_fake_config())  # `python -m pytest`
    assert full(_fake_config(["tests"], "ARGS")) and full(_fake_config([str(ROOT)], "ARGS"))
    assert full(_fake_config([str(ROOT / "tests")], "ARGS"))
    assert not full(_fake_config(["tests/test_store_boundary.py"], "ARGS"))
    assert not full(_fake_config(["tests/characterization"], "ARGS"))
    assert not full(_fake_config(["tests::TestX"], "ARGS"))
    assert not full(_fake_config([], "ARGS"))
    assert not full(_fake_config(keyword="boundary"))
    assert not full(_fake_config(markexpr="browser and not live"))  # CI'daki tarayıcı işi
    assert not full(_fake_config(markexpr=""))
    assert not full(_fake_config(lf=True)) and not full(_fake_config(stepwise=True))
    assert not full(_fake_config(deselect=["tests/test_x.py::test_y"]))
    assert not full(_fake_config(ignore=["tests/test_x.py"])) and not full(_fake_config(ignore_glob=["*x*"]))
    assert not full(_fake_config(numprocesses=4))  # pytest-xdist
    worker = _fake_config()
    worker.workerinput = {}
    assert not full(worker)


def test_runtime_tests_are_moved_to_the_end_of_the_session():
    def item(name: str, last: bool = False) -> Any:
        return SimpleNamespace(name=name, get_closest_marker=lambda marker: object() if last else None)

    items = [item("a"), item("runtime_1", last=True), item("b"), item("runtime_2", last=True), item("c")]
    before = conftest.STORE_BOUNDARY.full_run
    try:
        conftest.pytest_collection_modifyitems(_fake_config(keyword="x"), items)
        assert [i.name for i in items] == ["a", "b", "c", "runtime_1", "runtime_2"]
        assert conftest.STORE_BOUNDARY.full_run is False
    finally:
        conftest.STORE_BOUNDARY.full_run = before


def test_runtime_entries_are_kept_only_for_functions_the_static_check_does_not_track(tmp_path: Path):
    src = make_tree(tmp_path, {
        "src/store/__init__.py": "",
        "src/reader.py": (
            "import os\n"                                   # 1
            "from src.store import files\n"                 # 2
            "def listed(p):\n"                              # 3
            "    return os.listdir(p)\n"                    # 4
            "def dynamic(p):\n"                             # 5
            "    return getattr(os, 'listdir')(p)\n"        # 6
        ),
        "src/store/files.py": "",
        "src/doctor.py": "import tempfile\ndef probe(p):\n    return tempfile.mkstemp(dir=p)\n",
    })
    records = {
        ("src/reader.py", 4, "os.listdir"): ("t1", "/d"),         # statik denetim `listed`i zaten izliyor
        ("src/reader.py", 6, "os.listdir"): ("t2", "/d"),         # yalnızca çalışırken görülür
        ("src/reader.py", 2, "pandas"): ("", "/d/x.csv"),         # modül düzeyi: içe aktarma ihlali örtmez
        ("src/doctor.py", 3, "tempfile.mkstemp"): ("t3", "/d"),   # muaf modül: statik izleme yok
    }
    findings = scan_tree(src)
    assert {m: [f"{f.function}:{f.call}" for f in items] for m, items in findings.items()} == {
        "src/reader.py": ["<module>:import src.store.files", "listed:os.listdir"]}
    assert uncovered_runtime(resolve_records(records, tmp_path), findings) == {
        "src/reader.py": {("<module>", "pandas"): (2, "", "/d/x.csv"), ("dynamic", "os.listdir"): (6, "t2", "/d")},
        "src/doctor.py": {("probe", "tempfile.mkstemp"): (3, "t3", "/d")},
    }
    assert uncovered_runtime({"src/reader.py": {("listed", "os.listdir"): (4, "t1", "/d")}}, findings) == {}


def test_resolve_records_turns_lines_into_function_names(tmp_path: Path):
    make_tree(tmp_path, {"src/reader.py": (
        "import os\n"                         # 1
        "LISTING = os.listdir('.')\n"         # 2
        "class Reader:\n"                     # 3
        "    def load(self, p):\n"            # 4
        "        with open(p) as f:\n"        # 5
        "            return [open(q) for q in f]\n"  # 6
    )})
    records = {
        ("src/reader.py", 2, "os.listdir"): ("", "/d"),
        ("src/reader.py", 6, "open"): ("tests/test_b.py::test_two", "/d/b"),
        ("src/reader.py", 5, "open"): ("tests/test_a.py::test_one", "/d/a"),
    }
    assert resolve_records(records, tmp_path) == {"src/reader.py": {
        ("<module>", "os.listdir"): (2, "", "/d"),
        ("Reader.load", "open"): (5, "tests/test_a.py::test_one", "/d/a"),  # aynı işlev: tek satır, ilk görülen
    }}
