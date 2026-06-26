"""Program enum + resolver tests (A1; CLAUDE.md INV-2/INV-4 fail-closed posture).

The single hardened database tags every tenant row with a ``program_id``. The
program token a request carries must resolve to a known :class:`Program` member;
an unknown or absent token must FAIL CLOSED (raise), never silently default to a
program — a silent default would cross-pollinate one program's data into
another's view. ``resolve_program`` is pure (no I/O), so it lives in the
deterministic core and is exhaustively asserted here (TDD strict, §4.1).
"""

from __future__ import annotations

import pytest

from app.core.program import Program, resolve_program


def test_program_resolution_rejects_unknown() -> None:
    """Known tokens resolve; unknown/None fail closed (never a silent default)."""
    # Known tokens map to their enum member.
    assert resolve_program("fall_enrollment") is Program.FALL_ENROLLMENT
    assert resolve_program("summer_camp") is Program.SUMMER_CAMP

    # The token equals the enum value (StrEnum) — the value IS the program_id.
    assert Program.FALL_ENROLLMENT.value == "fall_enrollment"
    assert Program.SUMMER_CAMP.value == "summer_camp"

    # Unknown token fails closed — raises, never defaults to a program.
    with pytest.raises(ValueError):
        resolve_program("marketing")
    with pytest.raises(ValueError):
        resolve_program("")

    # A None token (no program supplied) also fails closed.
    with pytest.raises(ValueError):
        resolve_program(None)
