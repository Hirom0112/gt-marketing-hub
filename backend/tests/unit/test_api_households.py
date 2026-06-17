"""API contract tests for the household roll-up endpoint (TODO.md R1).

``GET /households`` exposes ``repository.household_roll_up()`` (S14 W2): one row
per household, each child's DERIVED stage plus the household ``worst_stage``
rollup (the least-advanced child — the weakest link).

Like the drop-off routes, this degrades cleanly off the store seam: the roll-up
lives only on the live :class:`SupabaseFamilyRepository`; the in-memory v1
fallback (A-3) has no ``household_roll_up``, so the route returns an empty list
rather than a 500. These tests cover BOTH the in-memory fallback and a stub repo
that DOES expose the roll-up.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import deps
from app.data.models import Stage
from app.data.supabase_repository import HouseholdChildStage, HouseholdRollUp
from app.main import app

client = TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.pop(deps.get_repository, None)


def test_households_degrades_to_empty_on_in_memory_repo() -> None:
    """A repo without roll-up support ⇒ an empty household list, never a 500."""
    resp = client.get("/households")
    assert resp.status_code == 200
    assert resp.json() == {"households": []}


class _StubRollUpRepo:
    """A minimal repo exposing only the household_roll_up method the route consumes."""

    def __init__(self, rollups: list[HouseholdRollUp]) -> None:
        self._rollups = rollups

    def household_roll_up(self) -> list[HouseholdRollUp]:
        return self._rollups


def test_households_returns_roll_up_shape() -> None:
    """The response carries one row per household: user_id, family_id, children, worst_stage."""
    uid = uuid4()
    fid = uuid4()
    sid1, sid2 = uuid4(), uuid4()
    rollups = [
        HouseholdRollUp(
            user_id=uid,
            family_id=fid,
            children=(
                HouseholdChildStage(student_id=sid1, display_label="A — Alex", stage=Stage.TUITION),
                HouseholdChildStage(student_id=sid2, display_label="A — Bea", stage=Stage.INTEREST),
            ),
            worst_stage=Stage.INTEREST,
        ),
    ]
    app.dependency_overrides[deps.get_repository] = lambda: _StubRollUpRepo(rollups)

    resp = client.get("/households")
    assert resp.status_code == 200
    assert resp.json() == {
        "households": [
            {
                "user_id": str(uid),
                "family_id": str(fid),
                "children": [
                    {"student_id": str(sid1), "display_label": "A — Alex", "stage": "tuition"},
                    {"student_id": str(sid2), "display_label": "A — Bea", "stage": "interest"},
                ],
                "worst_stage": "interest",
            }
        ]
    }


def test_households_null_owner_serializes_as_none() -> None:
    """A server-only (NULL-owner) household keeps user_id null in the response."""
    fid = uuid4()
    sid = uuid4()
    rollups = [
        HouseholdRollUp(
            user_id=None,
            family_id=fid,
            children=(
                HouseholdChildStage(student_id=sid, display_label="Unowned", stage=Stage.APPLY),
            ),
            worst_stage=Stage.APPLY,
        ),
    ]
    app.dependency_overrides[deps.get_repository] = lambda: _StubRollUpRepo(rollups)

    resp = client.get("/households")
    assert resp.status_code == 200
    body = resp.json()
    assert body["households"][0]["user_id"] is None
    assert body["households"][0]["family_id"] == str(fid)
    assert UUID(body["households"][0]["children"][0]["student_id"]) == sid
