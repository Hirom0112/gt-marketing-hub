#!/usr/bin/env python3
"""Runtime dependency-budget gate (TECH_STACK.md §4.1).

Asserts that ``backend/pyproject.toml``'s ``[project.dependencies]`` list holds
no more than ``MAX_RUNTIME_DEPS`` (15) entries. Dev/test tooling under
``[dependency-groups]`` does NOT count against the ceiling and is ignored.

Exit 0 if within budget, 1 if exceeded (or the file is unreadable).

Parsing uses the stdlib ``tomllib`` (Python 3.11+) — no third-party dep, so the
gate itself never spends budget.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

MAX_RUNTIME_DEPS = 15  # TECH_STACK.md §4.1 (LOCKED)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        pyproject = Path(argv[0])
    else:
        pyproject = Path(__file__).resolve().parent.parent / "backend" / "pyproject.toml"

    if not pyproject.is_file():
        print(f"dep-budget: cannot find {pyproject}", file=sys.stderr)
        return 1

    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)

    deps = data.get("project", {}).get("dependencies", [])
    count = len(deps)

    if count > MAX_RUNTIME_DEPS:
        print(
            f"dep-budget: FAILED — {count} runtime deps exceeds ceiling "
            f"{MAX_RUNTIME_DEPS} (TECH_STACK.md §4.1).",
            file=sys.stderr,
        )
        for d in deps:
            print(f"  - {d}", file=sys.stderr)
        print(
            "Exceeding the ceiling requires an ADR justifying the dependency "
            "against NFR-7. Remove a dep or write the code yourself.",
            file=sys.stderr,
        )
        return 1

    print(f"dep-budget: OK — {count}/{MAX_RUNTIME_DEPS} runtime deps (TECH_STACK.md §4.1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
