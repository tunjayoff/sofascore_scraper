"""src/jobs paketi: taşınan ilerleme modülü, eski içe aktarma yolu ve katman kuralı."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import List

import src.jobs as jobs_pkg
import src.jobs.progress as new_progress
import src.web.progress as old_progress

JOBS_DIR = Path(jobs_pkg.__file__).parent


# --- taşıma ve eski yol ------------------------------------------------------------


def test_old_import_path_re_exports_the_same_objects():
    for name in ("JobProgress", "PHASE_WEIGHTS", "MAX_FAILED_LISTED", "_ETA_MIN_DONE", "_ETA_MIN_ELAPSED"):
        assert getattr(old_progress, name) is getattr(new_progress, name), name


def test_job_progress_lives_in_the_jobs_package():
    assert new_progress.JobProgress.__module__ == "src.jobs.progress"
    assert jobs_pkg.JobProgress is new_progress.JobProgress
    assert jobs_pkg.PHASE_WEIGHTS is new_progress.PHASE_WEIGHTS
    assert jobs_pkg.MAX_FAILED_LISTED == new_progress.MAX_FAILED_LISTED


def test_shim_holds_no_logic_of_its_own():
    tree = ast.parse(Path(old_progress.__file__).read_text(encoding="utf-8"))
    kinds = {type(node) for node in tree.body}
    assert not kinds & {ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef}


# --- katman kuralı (docs/design/02-services.md 2.1, madde 5) -------------------------


def _imported_modules(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_jobs_package_imports_no_face_no_sql_no_file_access():
    forbidden_prefixes = ("src.web", "src.ui", "src.cli", "src.SofaScoreUi")
    forbidden_modules = {"sqlite3", "os", "pathlib", "shutil", "io"}
    files = sorted(JOBS_DIR.glob("*.py"))
    assert files, "src/jobs is empty"
    for path in files:
        for module in _imported_modules(path):
            assert not module.startswith(forbidden_prefixes), f"{path.name} imports {module}"
            assert module.split(".")[0] not in forbidden_modules, f"{path.name} imports {module}"
