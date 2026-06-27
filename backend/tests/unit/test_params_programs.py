"""programs params block tests (A1; CLAUDE.md INV-11, §4.1 drift-fails-build).

The single hardened database is multi-program; ``programs:`` is the canonical
home (INV-11) for which programs are active and which one this deployment serves.
``load_params`` parses it into the typed :class:`Programs` model, validating each
id against the :class:`Program` enum (Task 1) and asserting the selected
``active_program_id`` is one of the ``active_program_ids`` — config drift (a
selected program not in the active list, or an unknown token) fails the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import Programs, load_params
from app.core.program import Program

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_programs_block_loads() -> None:
    """The programs block loads into a typed Programs model and self-validates."""
    params = load_params(EXAMPLE_PARAMS)

    programs = params.programs
    assert isinstance(programs, Programs)

    # Active ids parse into Program enum members (validated against Task 1's enum).
    assert Program.FALL_ENROLLMENT in programs.active_program_ids
    assert all(isinstance(p, Program) for p in programs.active_program_ids)

    # The selected active program is one of the active ids.
    assert programs.active_program_id in programs.active_program_ids

    # A-38: the app_runtime read-token TTL is a positive tunable with a one-home in
    # params (the live repo mints this token to read AS app_runtime, RLS-bounded).
    assert programs.app_runtime_read_token_ttl_seconds >= 1

    # Drift guard: an active_program_id NOT in the active list fails at load time.
    with pytest.raises(ValidationError):
        Programs(
            active_program_ids=[Program.FALL_ENROLLMENT],
            active_program_id=Program.SUMMER_CAMP,
        )

    # Drift guard: an unknown program token is rejected against the Program enum.
    with pytest.raises(ValidationError):
        Programs(active_program_ids=["marketing"], active_program_id="marketing")
