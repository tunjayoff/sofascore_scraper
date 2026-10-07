"""
Store'un açık API'sinin anlık görüntüsü (plan maddesi ST-04; docs/design/01-storage.md bölüm 2.4, dördüncü test).

`sofascore_scraper.store.__all__` içindeki her ad için `tests/fixtures/store_api/<Ad>.txt` dosyası durur: sınıfın tabanları,
açık yöntemlerinin imzaları, alanları ve `__init__` içinde atanan açık öznitelikleri (işlevler için imza,
sabitler ve tür takma adları için atamanın kendisi). Dizindeki dosyaların kümesi `__all__`'un kendisidir;
ortak bir liste dosyası yoktur, böylece farklı sınıfları genişleten iki PR aynı dosyaya dokunmaz.

Açık bir imzada geçen ama `__all__`'da olmayan Store sınıfları da (ör. bir yöntemin döndürdüğü tür ya da bir
taban sınıf) kendi dosyalarıyla izlenir: imzada görünen her şey API'nin parçasıdır.

Görüntü kaynaktan (`ast`) üretilir, çalışan nesnelerden değil: çıktı Python sürümüne ve tür açıklamalarının
değerlendirilme biçimine bağlı kalmaz. Ayrı bir test, kaynakta bulunan tanımın gerçekten `sofascore_scraper.store`'dan
içe aktarılan nesne olduğunu doğrular.

API bilerek değiştiyse:

    STORE_API_UPDATE=1 python -m pytest tests/test_store_api_surface.py

Dosyalardaki fark PR'da görünür; API değişikliği böylece her zaman incelemeden geçer.
"""
from __future__ import annotations

import ast
import difflib
import inspect
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

import pytest

import sofascore_scraper.store

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "sofascore_scraper.store"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "fixtures" / "store_api"
UPDATE = os.getenv("STORE_API_UPDATE") == "1"
INDENT = "    "
ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _absolute(module: Optional[str], level: int, package: str) -> str:
    if level == 0:
        return module or ""
    parts = package.split(".") if package else []
    if level > 1:
        parts = parts[: max(len(parts) - (level - 1), 0)]
    return ".".join(parts + ([module] if module else []))


def _statements(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """Modül düzeyindeki deyimler; `if` ve `try` bloklarının içindekiler de modül düzeyinde sayılır."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, ast.If):
            yield from _statements(stmt.body)
            yield from _statements(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            yield from _statements(stmt.body)
            for handler in stmt.handlers:
                yield from _statements(handler.body)
            yield from _statements(stmt.orelse)
            yield from _statements(stmt.finalbody)


@dataclass(frozen=True)
class Located:
    """Bir adın kaynakta bulunduğu yer. kind: class | function | value | module | external"""

    module: str
    node: ast.AST
    kind: str
    name: str


class Sources:
    """Depodaki modüllerin ayrıştırılmış kaynağı ve adların tanımlandığı yerin bulunması."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._trees: Dict[str, Optional[ast.Module]] = {}

    def path(self, module: str) -> Optional[Path]:
        if not module:
            return None
        base = self.root.joinpath(*module.split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    def tree(self, module: str) -> Optional[ast.Module]:
        if module not in self._trees:
            path = self.path(module)
            self._trees[module] = ast.parse(path.read_text(encoding="utf-8")) if path else None
        return self._trees[module]

    def package(self, module: str) -> str:
        path = self.path(module)
        return module if path is not None and path.name == "__init__.py" else module.rpartition(".")[0]

    def locate(self, module: str, name: str, _depth: int = 0) -> Optional[Located]:
        """`module` içinde `name` adının bağlandığı son deyim; içe aktarmalar tanımın olduğu modüle kadar izlenir."""
        tree = self.tree(module)
        if tree is None or _depth > 10:
            return None
        found: Optional[Located] = None
        for stmt in _statements(tree.body):
            if isinstance(stmt, ast.ClassDef) and stmt.name == name:
                found = Located(module, stmt, "class", name)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
                found = Located(module, stmt, "function", name)
            elif isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
                found = Located(module, stmt, "value", name)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == name:
                found = Located(module, stmt, "value", name)
            elif isinstance(stmt, ast.ImportFrom):
                for alias in stmt.names:
                    if (alias.asname or alias.name) != name:
                        continue
                    base = _absolute(stmt.module, stmt.level, self.package(module))
                    target = self.locate(base, alias.name, _depth + 1)
                    if target is None and self.path(f"{base}.{alias.name}") is not None:
                        target = Located(f"{base}.{alias.name}", stmt, "module", alias.name)
                    found = target or Located(base, stmt, "external", alias.name)
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if (alias.asname or alias.name.split(".")[0]) == name:
                        target = alias.name if alias.asname else alias.name.split(".")[0]
                        kind = "module" if self.path(target) is not None else "external"
                        found = Located(target, stmt, kind, "")
        return found

    def relative_path(self, module: str) -> str:
        path = self.path(module)
        return path.relative_to(self.root).as_posix() if path else module


# --- görüntü üretimi ---------------------------------------------------------------------------------


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _argument(arg: ast.arg, default: Optional[ast.expr]) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text += f": {ast.unparse(arg.annotation)}"
    if default is not None:
        text += (" = " if arg.annotation is not None else "=") + ast.unparse(default)
    return text


def _arguments(args: ast.arguments) -> str:
    """İmza metni. `ast.unparse(arguments)` yerine elle yazılır: biçim Python sürümünden bağımsız kalır."""
    parts: List[str] = []
    positional = [*args.posonlyargs, *args.args]
    defaults: List[Optional[ast.expr]] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for index, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
        parts.append(_argument(arg, default))
        if args.posonlyargs and index == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg is not None:
        parts.append("*" + _argument(args.vararg, None))
    elif args.kwonlyargs:
        parts.append("*")
    parts.extend(_argument(arg, default) for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True))
    if args.kwarg is not None:
        parts.append("**" + _argument(args.kwarg, None))
    return ", ".join(parts)


def _signature(node: ast.AST, indent: str) -> List[str]:
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    lines = [f"{indent}@{ast.unparse(decorator)}" for decorator in node.decorator_list]
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    lines.append(f"{indent}{prefix} {node.name}({_arguments(node.args)}){returns}")
    return lines


def _instance_attributes(init: ast.AST, indent: str) -> List[str]:
    """`__init__` içinde `self.ad = ...` ile atanan açık öznitelikler, ilk atanma sırasıyla."""
    lines: List[str] = []
    seen = set()
    nodes = sorted(
        (n for n in ast.walk(init) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))),
        key=lambda n: (n.lineno, n.col_offset),
    )
    for node in nodes:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        flat = [e for t in targets for e in (t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t])]
        for target in flat:
            if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"
                    and _is_public(target.attr) and target.attr not in seen):
                seen.add(target.attr)
                annotation = f": {ast.unparse(node.annotation)}" if isinstance(node, ast.AnnAssign) else ""
                lines.append(f"{indent}self.{target.attr}{annotation}")
    return lines


def _render_class(node: ast.ClassDef, indent: str = "") -> List[str]:
    lines = [f"{indent}@{ast.unparse(decorator)}" for decorator in node.decorator_list]
    bases = [ast.unparse(base) for base in node.bases]
    bases += [f"{keyword.arg}={ast.unparse(keyword.value)}" if keyword.arg else f"**{ast.unparse(keyword.value)}"
              for keyword in node.keywords]
    lines.append(f"{indent}class {node.name}({', '.join(bases)}):" if bases else f"{indent}class {node.name}:")
    is_enum = any(base.split(".")[-1] in ENUM_BASES for base in bases)
    inner = indent + INDENT
    body: List[str] = []
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_public(stmt.name) or _is_dunder(stmt.name):
                body.extend(_signature(stmt, inner))
                if stmt.name == "__init__":
                    body.extend(_instance_attributes(stmt, inner))
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and _is_public(stmt.target.id):
                body.append(inner + ast.unparse(stmt))
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and _is_public(target.id):
                    # Sabit değerleri (ör. hata iletisi metni) API değildir; enum üyelerinin değerleri öyledir.
                    body.append(inner + (ast.unparse(stmt) if is_enum else f"{target.id} = ..."))
        elif isinstance(stmt, ast.ClassDef) and _is_public(stmt.name):
            body.extend(_render_class(stmt, inner))
    return lines + (body or [f"{inner}pass"])


def render(located: Located) -> List[str]:
    node = located.node
    if located.kind == "class":
        assert isinstance(node, ast.ClassDef)
        return _render_class(node)
    if located.kind == "function":
        return _signature(node, "")
    if located.kind == "value":
        return ast.unparse(node).splitlines()
    if located.kind == "module":
        return [f"module {located.module}"]
    return [f"from {located.module} import {located.name}" if located.name else f"import {located.module}"]


def build_snapshots(sources: Sources, names: Sequence[str], package: str = PACKAGE) -> Dict[str, str]:
    """
    Ad -> dosya metni. `names` paketin `__all__`'udur; imzalarda geçen, paketin içinde tanımlı ama dışa
    açılmamış sınıflar da eklenir (kendi imzalarında geçenlerle birlikte).
    """
    snapshots: Dict[str, str] = {}
    queue = [(name, package, True) for name in names]
    while queue:
        name, module, exported = queue.pop(0)
        if name in snapshots:
            continue
        located = sources.locate(module, name)
        if located is None:
            snapshots[name] = f"# {package}.{name}\n# kaynakta tanımı bulunamadı\n"
            continue
        lines = render(located)
        header = f"# {package}.{name}" if exported else \
            f"# {name}: {package}.__all__ içinde değil; açık bir imzada geçtiği için izleniyor"
        snapshots[name] = "\n".join([header, f"# kaynak: {sources.relative_path(located.module)}", *lines]) + "\n"
        if located.kind not in ("class", "function", "value"):
            continue
        for identifier in dict.fromkeys(IDENTIFIER.findall("\n".join(lines))):
            if identifier in snapshots or any(identifier == queued for queued, _m, _e in queue):
                continue
            target = sources.locate(located.module, identifier)
            if target is not None and target.kind == "class" and \
                    (target.module == package or target.module.startswith(package + ".")):
                queue.append((identifier, located.module, False))
    return snapshots


# --- gerçek paket ------------------------------------------------------------------------------------


def _current() -> Dict[str, str]:
    return build_snapshots(Sources(ROOT), list(sofascore_scraper.store.__all__))


def _stored() -> Dict[str, str]:
    if not SNAPSHOT_DIR.is_dir():
        return {}
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(SNAPSHOT_DIR.glob("*.txt"))}


def test_all_lists_unique_public_names():
    names = list(sofascore_scraper.store.__all__)
    assert all(isinstance(name, str) and name.isidentifier() and _is_public(name) for name in names), names
    assert len(names) == len(set(names)), "sofascore_scraper.store.__all__ içinde yinelenen ad var"
    folded = [name.casefold() for name in build_snapshots(Sources(ROOT), names)]
    assert len(folded) == len(set(folded)), \
        "yalnızca büyük/küçük harfle ayrışan iki ad var: Windows ve macOS'ta dosyaları çakışır"


def test_every_name_in_all_is_the_object_the_snapshot_describes():
    """Görüntü kaynaktan üretilir; kaynakta bulunan tanım, gerçekten içe aktarılan nesne olmalı."""
    sources = Sources(ROOT)
    for name in sofascore_scraper.store.__all__:
        obj = getattr(sofascore_scraper.store, name)
        located = sources.locate(PACKAGE, name)
        assert located is not None, f"{name}: sofascore_scraper/store içinde tanımı bulunamadı"
        if located.kind == "class":
            assert inspect.isclass(obj) and (obj.__module__, obj.__name__) == (located.module, located.name), name
        elif located.kind == "function":
            assert callable(obj) and getattr(obj, "__module__", None) == located.module, name
        elif located.kind == "module":
            assert inspect.ismodule(obj) and obj.__name__ == located.module, name


def test_package_root_binds_no_public_name_outside_all():
    """`from sofascore_scraper.store import X` ile alınabilen her açık ad `__all__`'da (ve dolayısıyla görüntüde) olmalı."""
    tree = Sources(ROOT).tree(PACKAGE)
    assert tree is not None
    bound = set()
    for stmt in _statements(tree.body):
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(stmt.name)
        elif isinstance(stmt, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in stmt.names)
        elif isinstance(stmt, ast.Import):
            bound.update(alias.asname or alias.name.split(".")[0] for alias in stmt.names)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            bound.update(t.id for t in targets if isinstance(t, ast.Name))
    leaked = sorted(name for name in bound if _is_public(name) and name not in sofascore_scraper.store.__all__ and name != "sofascore_scraper")
    assert leaked == [], f"sofascore_scraper/store/__init__.py şu adları bağlıyor ama __all__'a koymuyor: {leaked}"


def test_public_api_matches_the_snapshot_files():
    current = _current()
    if UPDATE:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        for path in SNAPSHOT_DIR.glob("*.txt"):
            if path.stem not in current:
                path.unlink()
        for name, text in current.items():
            (SNAPSHOT_DIR / f"{name}.txt").write_text(text, encoding="utf-8", newline="\n")
    stored = _stored()
    problems: List[str] = []
    for name in sorted(set(current) - set(stored)):
        problems.append(f"{name}: yeni açık ad, tests/fixtures/store_api/{name}.txt yok")
    for name in sorted(set(stored) - set(current)):
        problems.append(f"{name}: tests/fixtures/store_api/{name}.txt var ama ad artık açık API'de değil")
    for name in sorted(set(current) & set(stored)):
        if current[name] != stored[name]:
            diff = difflib.unified_diff(stored[name].splitlines(), current[name].splitlines(),
                                        f"{name}.txt (kayıtlı)", f"{name} (şimdiki)", lineterm="")
            problems.append("\n".join(diff))
    if problems:
        pytest.fail(
            "Store'un açık API'si kayıtlı görüntüden farklı:\n\n" + "\n\n".join(problems) + "\n\n"
            "Değişiklik bilerek yapıldıysa: STORE_API_UPDATE=1 python -m pytest tests/test_store_api_surface.py\n"
            "ve farkı PR açıklamasında anın.",
            pytrace=False,
        )


def test_error_classes_are_part_of_the_snapshot():
    """Bugünkü API yalnızca hata sınıflarıdır (ST-03); görüntünün gerçekten onları içerdiğini sabitler."""
    current = _current()
    assert set(current) >= {"StoreError", "LeaseHeld", "SchemaTooNew", "PayloadCorrupt"}
    assert "class LeaseHeld(StoreError):" in current["LeaseHeld"]
    assert "self.pid" in current["LeaseHeld"] and "def from_exception(" in current["StoreError"]


# --- üreticinin kendi testleri -----------------------------------------------------------------------


def _package(tmp_path: Path, files: Dict[str, str]) -> Sources:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Sources(tmp_path)


API_MODULE = '''
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Iterator, Literal, Optional

from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.store.lease import Lease

Need = Literal["none", "partial", "full"]
FORMAT: int = 1


class Kind(str, Enum):
    EVENT = "event"
    SEASON = "season"


@dataclass(frozen=True)
class Ref:
    """Belge dizgisi görüntüye girmez."""
    kind: Kind
    id: int
    tournament_id: Optional[int] = None
    tags: tuple = field(default_factory=tuple)
    VERSION: ClassVar[int] = 2
    _cache = {}
    label = "ref"

    @classmethod
    def event(cls, id: int) -> "Ref":
        return cls(Kind.EVENT, id)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    def _private(self):
        return None


class Store(_Base, metaclass=Meta):
    def __init__(self, data_dir, *, readonly: bool = False) -> None:
        self.data_dir = data_dir
        self._conn = None
        self.readonly: bool = readonly
        self.events, self.jobs = None, None
        if readonly:
            self.data_dir = str(data_dir)

    def lease(self, name: str, *, purpose: str = "", wait: float = 0.0) -> Lease: ...
    async def close(self) -> None: ...
    def __enter__(self) -> "Store": ...
    def __iter__(self) -> Iterator[Ref]: ...

    class Options:
        strict = True


class _Base:
    def info(self) -> dict: ...


def open_store(data_dir: str | None = None, *, create: bool = True, readonly: bool = False) -> Store:
    return Store(data_dir)
'''

LEASE_MODULE = '''
class Lease:
    def __enter__(self) -> "Lease": ...
    def release(self) -> None: ...
    def owner(self) -> LeaseOwner: ...


class LeaseOwner:
    pid: int


class Unrelated:
    pass
'''


@pytest.fixture
def package(tmp_path: Path) -> Sources:
    return _package(tmp_path, {
        "sofascore_scraper/exceptions.py": "class StorageError(Exception):\n    pass\n",
        "sofascore_scraper/store/__init__.py": (
            "from sofascore_scraper.store.api import FORMAT, Kind, Need, Ref, Store, open_store\n"
            "from sofascore_scraper.store import lease as leases\n"
            "from .errors import StoreError as Error\n"
            "import json\n"
            "from os import PathLike\n"
        ),
        "sofascore_scraper/store/api.py": API_MODULE,
        "sofascore_scraper/store/lease.py": LEASE_MODULE,
        "sofascore_scraper/store/errors.py": "from sofascore_scraper.exceptions import StorageError\n\n\nclass StoreError(StorageError):\n    code = 1\n",
    })


def _body(text: str) -> List[str]:
    return text.splitlines()[2:]


def test_snapshot_of_a_dataclass(package: Sources):
    text = build_snapshots(package, ["Ref"])["Ref"]
    assert text.splitlines()[:2] == ["# sofascore_scraper.store.Ref", "# kaynak: sofascore_scraper/store/api.py"]
    assert _body(text) == [
        "@dataclass(frozen=True)",
        "class Ref:",
        "    kind: Kind",
        "    id: int",
        "    tournament_id: Optional[int] = None",
        "    tags: tuple = field(default_factory=tuple)",
        "    VERSION: ClassVar[int] = 2",
        "    label = ...",
        "    @classmethod",
        "    def event(cls, id: int) -> 'Ref'",
        "    @property",
        "    def key(self) -> str",
    ]


def test_snapshot_of_a_class_with_init_attributes_dunders_and_a_nested_class(package: Sources):
    assert _body(build_snapshots(package, ["Store"])["Store"]) == [
        "class Store(_Base, metaclass=Meta):",
        "    def __init__(self, data_dir, *, readonly: bool = False) -> None",
        "    self.data_dir",
        "    self.readonly: bool",
        "    self.events",
        "    self.jobs",
        "    def lease(self, name: str, *, purpose: str = '', wait: float = 0.0) -> Lease",
        "    async def close(self) -> None",
        "    def __enter__(self) -> 'Store'",
        "    def __iter__(self) -> Iterator[Ref]",
        "    class Options:",
        "        strict = ...",
    ]


def test_snapshot_of_enum_function_and_values(package: Sources):
    snapshots = build_snapshots(package, ["Kind", "open_store", "Need", "FORMAT"])
    assert _body(snapshots["Kind"]) == ["class Kind(str, Enum):", "    EVENT = 'event'", "    SEASON = 'season'"]
    assert _body(snapshots["open_store"]) == [
        "def open_store(data_dir: str | None = None, *, create: bool = True, readonly: bool = False) -> Store"]
    assert _body(snapshots["Need"]) == ["Need = Literal['none', 'partial', 'full']"]
    assert _body(snapshots["FORMAT"]) == ["FORMAT: int = 1"]


def test_snapshot_follows_reexports_to_the_defining_module(package: Sources):
    snapshots = build_snapshots(package, ["Error", "leases", "json", "PathLike", "Missing"])
    assert snapshots["Error"].splitlines() == [
        "# sofascore_scraper.store.Error", "# kaynak: sofascore_scraper/store/errors.py", "class StoreError(StorageError):", "    code = ..."]
    assert snapshots["leases"].splitlines() == ["# sofascore_scraper.store.leases", "# kaynak: sofascore_scraper/store/lease.py", "module sofascore_scraper.store.lease"]
    assert _body(snapshots["json"]) == ["import json"]
    assert _body(snapshots["PathLike"]) == ["from os import PathLike"]
    assert snapshots["Missing"] == "# sofascore_scraper.store.Missing\n# kaynakta tanımı bulunamadı\n"


def test_classes_reached_through_public_signatures_are_tracked_too(package: Sources):
    """Store.lease -> Lease -> LeaseOwner ve taban sınıf _Base izlenir; imzalarda geçmeyen Unrelated izlenmez."""
    snapshots = build_snapshots(package, ["Store", "open_store"])
    assert sorted(snapshots) == ["Kind", "Lease", "LeaseOwner", "Ref", "Store", "_Base", "open_store"]
    assert snapshots["Lease"].splitlines()[:2] == [
        "# Lease: sofascore_scraper.store.__all__ içinde değil; açık bir imzada geçtiği için izleniyor",
        "# kaynak: sofascore_scraper/store/lease.py",
    ]
    assert _body(snapshots["_Base"]) == ["class _Base:", "    def info(self) -> dict"]
    assert _body(snapshots["LeaseOwner"]) == ["class LeaseOwner:", "    pid: int"]
    # sofascore_scraper/exceptions.py Store paketinin dışında: StorageError tabanı izlenmez
    assert "StorageError" not in build_snapshots(package, ["Error"])


def test_an_exported_class_keeps_the_exported_header_when_also_reached_through_a_signature(package: Sources):
    snapshots = build_snapshots(package, ["open_store", "Store", "Ref"])
    assert snapshots["Store"].startswith("# sofascore_scraper.store.Store\n") and snapshots["Ref"].startswith("# sofascore_scraper.store.Ref\n")


def test_signature_text_covers_every_kind_of_parameter():
    tree = ast.parse(
        "def f(a, b: int, /, c=1, d: str = 'x', *args: int, e, f: bool = True, **kw: object) -> None: ...\n"
        "def g(*, key): ...\n"
        "def h(): ...\n"
        "def i(a, /): ...\n"
    )
    assert [_signature(node, "")[0] for node in tree.body] == [
        "def f(a, b: int, /, c=1, d: str = 'x', *args: int, e, f: bool = True, **kw: object) -> None",
        "def g(*, key)",
        "def h()",
        "def i(a, /)",
    ]


def test_empty_class_renders_pass(package: Sources):
    sources = _package(package.root, {"sofascore_scraper/store/empty.py": "class Marker(Exception):\n    '''yalnızca belge'''\n"})
    located = sources.locate("sofascore_scraper.store.empty", "Marker")
    assert located is not None and render(located) == ["class Marker(Exception):", "    pass"]
