"""
requirements.txt (aralıklar) ↔ constraints.txt (doğrulanmış tam sürümler) ↔ pyproject `dev` ekstrası.

CI ve kurulum `pip install -r requirements.txt -c constraints.txt` ile yapılır. Bu testler ağ
olmadan, bir sabitin aralığın dışına çıktığını (örn. Dependabot yalnızca birini güncellediğinde)
ya da bir paketin sabitsiz kaldığını kurulumdan önce yakalar.
"""
from __future__ import annotations

import os

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

try:
    import tomllib
except ImportError:  # Python 3.10: pytest'in bağımlılığı olarak gelir
    import tomli as tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPORTED_PYTHONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
PLATFORMS = ["linux", "win32", "darwin"]


def _requirements(filename: str) -> list[Requirement]:
    """Dosyadaki gereksinim satırları (yorumlar, boş satırlar ve -r/-c yönergeleri hariç)."""
    out = []
    with open(os.path.join(ROOT, filename), encoding="utf-8") as f:
        for raw in f:
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            out.append(Requirement(line))
    return out


def _env(python: str, platform: str = "linux") -> dict[str, str]:
    env = default_environment()
    env.update({"python_version": python, "python_full_version": f"{python}.0", "sys_platform": platform})
    return env


def _applies(req: Requirement, env: dict[str, str]) -> bool:
    return req.marker is None or req.marker.evaluate(env)


CONSTRAINTS = _requirements("constraints.txt")
RUNTIME = _requirements("requirements.txt")
DEV = _requirements("requirements-dev.txt")


def _pins_for(env: dict[str, str]) -> dict[str, list[Requirement]]:
    pins: dict[str, list[Requirement]] = {}
    for c in CONSTRAINTS:
        if _applies(c, env):
            pins.setdefault(canonicalize_name(c.name), []).append(c)
    return pins


def test_constraints_are_exact_pins_without_extras():
    """pip, constraints dosyasında ekstra ('paket[x]') kabul etmez; sabit de tek bir sürüm olmalı."""
    for c in CONSTRAINTS:
        specs = list(c.specifier)
        assert not c.extras, f"{c}: constraints dosyasında ekstra olamaz"
        assert len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version, f"{c}: tam sürüm değil"


@pytest.mark.parametrize("python", SUPPORTED_PYTHONS)
@pytest.mark.parametrize("platform", PLATFORMS)
def test_each_package_has_at_most_one_pin_per_environment(python, platform):
    """Aynı ortama iki farklı sabit uygulanırsa pip kurulumu çözemez."""
    clashes = {name: [str(p) for p in pins] for name, pins in _pins_for(_env(python, platform)).items() if len(pins) > 1}
    assert clashes == {}


@pytest.mark.parametrize("python", SUPPORTED_PYTHONS)
@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_direct_requirement_is_pinned_inside_its_allowed_range(python, platform):
    env = _env(python, platform)
    pins = _pins_for(env)
    problems = []
    for req in RUNTIME + DEV:
        if not _applies(req, env):
            continue
        found = pins.get(canonicalize_name(req.name))
        if not found:
            problems.append(f"{req.name}: constraints.txt içinde Python {python}/{platform} için sabit yok")
            continue
        version = next(iter(found[0].specifier)).version
        if not req.specifier.contains(version, prereleases=True):
            problems.append(f"{req.name}: sabit {version}, requirements aralığı '{req.specifier}' dışında")
    assert problems == []


def test_toml_reader_is_required_only_where_the_standard_library_lacks_it():
    """
    sofascore.toml'u Python 3.11+ standart kütüphanedeki tomllib okur; tomli yalnızca 3.10 için gerekir
    (src/config/loader.py) ve orada sabitlidir.
    """
    tomli = [req for req in RUNTIME if canonicalize_name(req.name) == "tomli"]
    assert len(tomli) == 1
    needed = [python for python in SUPPORTED_PYTHONS if _applies(tomli[0], _env(python))]
    assert needed == ["3.10"]
    assert "tomli" in _pins_for(_env("3.10")) and "tomli" not in _pins_for(_env("3.11"))


def test_dev_requirements_match_the_pyproject_dev_extra():
    """Araç listesi iki yerde durur (pip -r için requirements-dev.txt, pyproject `dev` ekstrası): aynı kalmalı."""
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        extra = tomllib.load(f)["project"]["optional-dependencies"]["dev"]

    def normalise(reqs) -> set[tuple[str, str]]:
        return {(canonicalize_name(r.name), str(r.specifier)) for r in reqs}

    assert normalise(DEV) == normalise(Requirement(item) for item in extra)


def test_dev_requirements_include_the_runtime_requirements():
    with open(os.path.join(ROOT, "requirements-dev.txt"), encoding="utf-8") as f:
        lines = [line.strip() for line in f]
    assert "-r requirements.txt" in lines
