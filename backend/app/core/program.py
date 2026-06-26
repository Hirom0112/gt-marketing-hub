"""Program identity + fail-closed resolver (A1 — single hardened database).

The cockpit runs MULTIPLE programs (fall enrollment, summer camp) out of one
hardened database; every tenant row is tagged with a ``program_id`` and the
RESTRICTIVE RLS isolates one program's data from another's. :class:`Program` is
the canonical enum of those program ids — its ``value`` IS the ``program_id``
stamped on rows and carried in the ``app_metadata`` claim.

``resolve_program`` turns an inbound token into a :class:`Program`. Per the
fail-closed posture (CLAUDE.md INV-2/INV-4), an unknown or absent token RAISES;
it must NEVER silently default to a program, since a wrong default would leak one
program's rows into another's view. The function is pure (no I/O) and lives in
the deterministic core (the core-purity test guards this).
"""

from __future__ import annotations

from enum import StrEnum


class Program(StrEnum):
    """A program tenant of the single hardened database (A1).

    The member ``value`` is the canonical ``program_id`` token — the string
    stamped on every tenant row and matched by the RESTRICTIVE RLS policy.
    """

    FALL_ENROLLMENT = "fall_enrollment"
    SUMMER_CAMP = "summer_camp"


def resolve_program(token: str | None) -> Program:
    """Resolve a program token to its :class:`Program`, failing closed.

    Args:
        token: The inbound program id (e.g. ``"fall_enrollment"``). ``None`` or
            any value that is not a known :class:`Program` is rejected.

    Returns:
        The matching :class:`Program` member.

    Raises:
        ValueError: if ``token`` is ``None`` or not a known program id. The
            resolver NEVER defaults to a program — an unknown token must fail
            closed so one program's data can never leak into another's view
            (CLAUDE.md INV-2/INV-4).
    """
    if token is None:
        raise ValueError("program token is required; refusing to default (fail-closed, A1)")
    try:
        return Program(token)
    except ValueError as exc:
        known = ", ".join(p.value for p in Program)
        raise ValueError(
            f"unknown program token {token!r}; expected one of: {known} (fail-closed, A1)"
        ) from exc
