"""M5 — GET /enrollment/sis-buckets PII firewall (INV-1/INV-6).

TODO.md M5 item 4: the buckets roll-up returns ONLY ``{family_id, present,
confirmed_at, bucket}`` per family — the test asserts NO child name / DOB / grade
/ roster contact appears anywhere in the payload. The firewall is the response
shape itself; this proves the SIS roster's PII never crosses into the cockpit.

Repo and SIS adapter are overridden onto the SAME synthetic cohort so the
reconcile produces real divergence (🔴/🟡/✅) to roll up.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from app.adapters.registry import get_enrollment_system_adapter
from app.adapters.sis.simulated import SimulatedSISAdapter
from app.api import deps
from app.core.params import load_params
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import generate
from app.main import app

client = TestClient(app)

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# `student_id` is the per-CHILD grain (A-24) — an opaque owner-scoped uuid, NOT
# child PII, so it is an allowed firewall field. The PII protection is the
# exact-allowed-keys lock below + the forbidden PII substrings (name/dob/grade/…).
_ALLOWED_FAMILY_KEYS = {"family_id", "student_id", "present", "confirmed_at", "bucket"}
# Anything a roster row might carry about a household/minor must never appear. NOTE:
# the bare token "student" is NOT forbidden (student_id is a legitimate opaque uuid);
# the actual child-PII substrings below + the allowed-keys lock are the firewall.
_FORBIDDEN_KEY_SUBSTR = (
    "name",
    "dob",
    "birth",
    "grade",
    "email",
    "phone",
    "child",
    "address",
    "income",
)


def _all_keys(obj: object) -> Iterator[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _all_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _all_keys(item)


def test_buckets_leak_no_roster_pii() -> None:
    params = load_params(EXAMPLE_PARAMS)
    dataset = generate(60, seed=7)
    repo = InMemoryFamilyRepository(dataset, params=params)
    adapter = SimulatedSISAdapter.from_cohort(dataset, seed=7, params=params)

    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[get_enrollment_system_adapter] = lambda: adapter
    try:
        resp = client.get("/enrollment/sis-buckets")
    finally:
        app.dependency_overrides.pop(deps.get_repository, None)
        app.dependency_overrides.pop(get_enrollment_system_adapter, None)

    assert resp.status_code == 200
    body = resp.json()

    # --- shape: only buckets + total at the top level ---
    assert set(body) == {"buckets", "total"}
    assert isinstance(body["total"], int) and body["total"] > 0
    labels = {group["bucket"] for group in body["buckets"]}
    assert labels == {"paid_not_in_sis", "records_lag", "ambiguous", "confirmed"}

    # --- every entry carries EXACTLY the firewall fields, nothing else ---
    counted = 0
    with_student = 0
    for group in body["buckets"]:
        assert set(group) == {"bucket", "count", "families"}
        assert group["count"] == len(group["families"])
        for family in group["families"]:
            counted += 1
            extra = set(family) - _ALLOWED_FAMILY_KEYS
            assert not extra, f"PII firewall breached: unexpected family fields {extra}"
            assert isinstance(family["present"], bool)
            assert family["bucket"] == group["bucket"]
            # student_id is the per-child grain: when set it is an opaque UUID, never
            # a name/grade — a value-level firewall check (not just the key lock).
            sid = family.get("student_id")
            if sid is not None:
                with_student += 1
                UUID(sid)  # raises if not a uuid → not a name/PII string
    assert counted == body["total"]
    # The cohort has children, so the verdicts are attributed per-child (A-24).
    assert with_student > 0, "expected per-child SIS verdicts (student_id attributed)"

    # --- no PII-shaped key ANYWHERE in the payload (recursive) ---
    for key in _all_keys(body):
        low = key.lower()
        leaked = [sub for sub in _FORBIDDEN_KEY_SUBSTR if sub in low]
        assert not leaked, f"PII firewall breached: key '{key}' matches {leaked}"

    # --- the cohort exercises real divergence (not an all-confirmed shortcut) ---
    by_count = {group["bucket"]: group["count"] for group in body["buckets"]}
    assert by_count["confirmed"] >= 1
    assert by_count["paid_not_in_sis"] >= 1
    assert by_count["records_lag"] >= 1
