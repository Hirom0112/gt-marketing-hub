"""Core-purity invariant guard (ARCHITECTURE.md §3; CLAUDE.md INV-2/§1).

The deterministic core (`app/core/`) must be pure: it may NOT import from
`app.ai` or `app.adapters`. The edge depends on the core, never the reverse.
This test parses every module under `core/` and fails if any forbidden
import appears — it passes now (core is empty) and guards the invariant as
the core fills in later slices.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import app.core

CORE_DIR = Path(app.core.__file__).resolve().parent
FORBIDDEN_ROOTS = ("app.ai", "app.adapters")


def _core_module_names() -> list[str]:
    """Every importable module under app.core (recursively)."""
    return [
        name
        for _, name, _ in pkgutil.walk_packages(app.core.__path__, prefix=f"{app.core.__name__}.")
    ]


def _forbidden_imports(source: str) -> list[str]:
    """Return any imports in `source` that target a forbidden root."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_ROOTS):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(FORBIDDEN_ROOTS):
                hits.append(module)
    return hits


def test_core_source_has_no_forbidden_imports() -> None:
    """Static check: no core source file imports app.ai / app.adapters."""
    offenders: dict[str, list[str]] = {}
    for path in CORE_DIR.rglob("*.py"):
        hits = _forbidden_imports(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path)] = hits
    assert not offenders, f"core/ imports forbidden modules: {offenders}"


def test_core_modules_import_cleanly() -> None:
    """Dynamic check: importing every core module triggers no forbidden import."""
    for name in _core_module_names():
        importlib.import_module(name)
