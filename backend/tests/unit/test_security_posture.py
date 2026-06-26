"""Unit tests for the live RLS-posture core (``app.core.security_posture``).

These pin the D-RLS-7 exposed-schema semantics: the runtime posture checker must
agree, byte-for-byte in behavior, with the build-time
``tests/unit/test_migrations_rls.py::_security_definer_functions_in_public`` helper.
A definer-rights helper in the EXPOSED (public / PostgREST-reachable) schema flips
the posture RED; a definer-rights helper in a NON-exposed schema (e.g. ``private.``,
the documented-safe Supabase RBAC pattern, 0027/B1) is PERMITTED and stays green.
"""

from __future__ import annotations

from app.core.security_posture import evaluate_posture

# A well-formed table+ENABLE+FORCE+null-guarded-policy prelude so the other three
# checks pass and the `green` verdict turns solely on the D-RLS-7 check.
_WELL_FORMED = [
    "CREATE TABLE bar (id uuid PRIMARY KEY);",
    "ALTER TABLE bar ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE bar FORCE ROW LEVEL SECURITY;",
    "CREATE POLICY bar_sel ON bar FOR SELECT USING ((SELECT auth.uid()) IS NOT NULL);",
]


def _definer_check(result: object) -> object:
    from app.core.security_posture import PostureResult

    assert isinstance(result, PostureResult)
    return next(c for c in result.checks if c.name == "no_security_definer_in_exposed_schema")


def test_private_definer_helper_is_permitted() -> None:
    """A `private.` (non-exposed) SECURITY DEFINER helper PASSES the D-RLS-7 check.

    This is the gate-fix RED case: under the old blanket `SECURITY DEFINER`-token
    scan this failed (the legitimate private. helper tripped it). The exposed-schema
    semantics let the documented-safe Supabase RBAC pattern through.
    """
    migrations = [
        *_WELL_FORMED,
        "CREATE SCHEMA IF NOT EXISTS private;",
        "CREATE FUNCTION private.foo() RETURNS boolean LANGUAGE sql "
        "SECURITY DEFINER AS $$ SELECT true $$;",
    ]
    result = evaluate_posture(migrations)
    check = _definer_check(result)
    assert check.passed is True, check.detail
    # The rest of the set is well-formed, so the whole posture stays green.
    assert result.green is True, result


def test_public_definer_helper_fails() -> None:
    """An explicit `public.` SECURITY DEFINER helper FAILS the D-RLS-7 check (RED)."""
    migrations = [
        *_WELL_FORMED,
        "CREATE FUNCTION public.bar() RETURNS boolean LANGUAGE sql "
        "SECURITY DEFINER AS $$ SELECT true $$;",
    ]
    result = evaluate_posture(migrations)
    check = _definer_check(result)
    assert check.passed is False
    assert "public.bar" in check.detail, check.detail
    assert result.green is False, result


def test_unqualified_definer_helper_fails() -> None:
    """An UNQUALIFIED SECURITY DEFINER helper resolves to public ⇒ FAILS (RED)."""
    migrations = [
        *_WELL_FORMED,
        "CREATE OR REPLACE FUNCTION baz() RETURNS boolean LANGUAGE sql "
        "SECURITY DEFINER AS $$ SELECT true $$;",
    ]
    result = evaluate_posture(migrations)
    check = _definer_check(result)
    assert check.passed is False
    assert "baz" in check.detail, check.detail
    assert result.green is False, result


def test_no_definer_at_all_passes() -> None:
    """A migration set with no definer helper at all PASSES the D-RLS-7 check."""
    result = evaluate_posture(_WELL_FORMED)
    check = _definer_check(result)
    assert check.passed is True
    assert result.green is True, result
