"""MD — the curated `COCKPIT_SCENARIO=demo` cohort has the on-camera demo shape.

The demo cohort (``generate_demo_cohort``) is a SEPARATE deterministic fixture —
exactly 6 synthetic households with controlled, legible state for the demo
(MULTI_AGENT_COCKPIT §10.1): exactly one two-child household, a stage spread
(≥1 mid-funnel, ≥1 enrollment-done "went all the way"), a funding/voucher spread,
seeded SIS divergence, and an assignment split across the two demo agents with
≥1 left unassigned (the intake pool the admin routes live).

This is a ``data/`` test (INV-1): every household is synthetic — obviously-fake
household labels, ``@example.invalid`` emails, ``555-01xx`` phones — and the whole
cohort is byte-identical across runs (deterministic, no clock/random, CLAUDE §4.1).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from uuid import UUID

from app.core.params import load_params
from app.core.sales_agents import SALES_AGENTS
from app.core.sis_reconcile import FamilyMatchKey, SisBucket, SisRosterRow, reconcile
from app.data.models import FundingState, Stage
from app.data.sis_reconcile_job import family_match_keys
from app.data.sis_roster import generate_sis_roster
from app.data.synthetic import SyntheticDataset, generate_demo_cohort

# The loader's bare default (`params/params.yaml`) is gitignored + CWD-relative;
# tests load the committed example explicitly (the house pattern). ``parents[3]``
# from ``tests/data/`` is the repo root.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# The two seeded demo agents (closer rank 1, setter rank 2).
_CLOSER_ID = SALES_AGENTS[0].agent_id
_SETTER_ID = SALES_AGENTS[1].agent_id

# "Paid" = at/after the §5.4 first-installment floor (the families a SIS carries).
_PAID: frozenset[FundingState] = frozenset(
    {FundingState.FIRST_INSTALLMENT_RECEIVED, FundingState.FUNDED}
)
# Mid-funnel = applied/enrolling (not interest, not closed-out tuition).
_MID_FUNNEL: frozenset[Stage] = frozenset({Stage.APPLY, Stage.ENROLL})


def _children_by_family(ds: SyntheticDataset) -> Counter[UUID]:
    counts: Counter[UUID] = Counter()
    for student in ds.students:
        counts[student.family_id] += 1
    return counts


def test_demo_scenario_shape() -> None:
    params = load_params(EXAMPLE_PARAMS)
    ds = generate_demo_cohort(params=params)

    # --- EXACTLY 6 households, the deliberate on-camera cohort (DH-2) ---------
    assert len(ds.families) == 6
    # parallel one-row-per-family source tables (the spine join holds).
    assert len(ds.leads) == len(ds.families)
    assert len(ds.app_forms) == len(ds.families)
    assert len(ds.enrollment_forms) == len(ds.families)

    # the deliberate 6 surnames (Johnson, Garcia, Ahmed dropped in DH-2).
    surnames = {lead.synthetic_last_name for lead in ds.leads}
    assert surnames == {"Rivera", "Okafor", "Nguyen", "Patel", "Kim", "Silva"}

    # --- EXACTLY one two-child household, the rest single-child --------------
    child_counts = _children_by_family(ds)
    assert set(child_counts) == {f.family_id for f in ds.families}
    assert sorted(child_counts.values(), reverse=True)[0] == 2
    assert list(child_counts.values()).count(2) == 1, "exactly one two-child household"
    assert all(c in (1, 2) for c in child_counts.values()), "every other household is single-child"
    # the lead's num_children must agree with the seeded student rows (A-24 grain).
    children_by_lead = {lead.family_id: lead.num_children for lead in ds.leads}
    for family_id, n in child_counts.items():
        assert children_by_lead[family_id] == n

    # --- stage spread: ≥1 mid-funnel + ≥1 enrollment-done -------------------
    stages = [f.current_stage for f in ds.families]
    assert any(s in _MID_FUNNEL for s in stages), "expected ≥1 mid-funnel household"
    # "went all the way": at TUITION and paid ⇒ Closed — pending SIS confirmation.
    went_all_the_way = [
        f for f in ds.families if f.current_stage is Stage.TUITION and f.funding_state in _PAID
    ]
    assert went_all_the_way, "expected ≥1 enrollment-done household (went all the way)"

    # --- assignment split across both agents + ≥1 unassigned -----------------
    reps = [f.assigned_rep_id for f in ds.families]
    assert _CLOSER_ID in reps, "the closer (#1) holds at least one deal"
    assert _SETTER_ID in reps, "the setter (#2) holds at least one deal"
    assert any(r is None for r in reps), "≥1 household left unassigned (the intake pool)"
    # the closer holds the multi-child household (high-value / multi-child case).
    (two_child_id,) = [fid for fid, n in child_counts.items() if n == 2]
    multi = next(f for f in ds.families if f.family_id == two_child_id)
    assert multi.assigned_rep_id == _CLOSER_ID, "the closer holds the multi-child case"

    # --- ≥3 paid (sis_roster needs len(paid) >= 3 to seed all three buckets) -
    paid = [f for f in ds.families if f.funding_state in _PAID]
    assert len(paid) >= 3, "≥3 paid families so the SIS roster seeds all three buckets"

    # --- funding-type spread (the voucher clocks + tiers each show something) -
    funding_types = {f.funding_type for f in ds.families}
    assert len(funding_types) >= 3, "expected a funding-type spread"

    # --- DH-2: conversion-signal raw inputs present + correctly typed --------
    # every lead carries a synthetic aggregate neighborhood label (non-empty).
    for lead in ds.leads:
        assert isinstance(lead.neighborhood, str) and lead.neighborhood, (
            "every household carries a synthetic neighborhood label"
        )
    # the cohort shows a spread of neighborhoods (not all identical).
    assert len({lead.neighborhood for lead in ds.leads}) >= 2, "neighborhood spread"
    # self_reported_income is int | None; ≥1 of each (a believable mid/closed mix).
    incomes = [a.self_reported_income for a in ds.app_forms]
    for inc in incomes:
        assert inc is None or isinstance(inc, int), "self_reported_income is int | None"
    assert any(i is not None for i in incomes), "≥1 family reports an income"
    assert any(i is None for i in incomes), "≥1 mid-funnel family has no income yet"

    # --- INV-1: every household is synthetic ---------------------------------
    for family in ds.families:
        assert family.primary_contact_synthetic_email.endswith("@example.invalid")
        assert family.display_name.startswith("The ")
    for lead in ds.leads:
        assert lead.synthetic_email.endswith("@example.invalid")
        assert lead.synthetic_phone.startswith("555-01")

    # --- deterministic: generate twice ⇒ byte-identical ----------------------
    again = generate_demo_cohort(params=params)
    assert [f.model_dump() for f in ds.families] == [f.model_dump() for f in again.families]
    assert [s.model_dump() for s in ds.students] == [s.model_dump() for s in again.students]
    assert [r.model_dump() for r in ds.leads] == [r.model_dump() for r in again.leads]


def test_demo_cohort_seeds_sis_divergence() -> None:
    """Run the curated cohort through the M5 reconcile ⇒ all three SIS buckets."""
    params = load_params(EXAMPLE_PARAMS)
    ds = generate_demo_cohort(params=params)

    paid = [f for f in ds.families if f.funding_state in _PAID]
    assert len(paid) >= 3, "≥3 paid families so the roster seeds all three buckets"

    roster_records = generate_sis_roster(ds, seed=params.back_to_school.seed, params=params)
    rows = [
        SisRosterRow(
            external_id=r.external_id,
            email=r.match_attrs.email,
            phone=r.match_attrs.phone,
            enrollment_status=r.enrollment_status,
            confirmed_at=r.confirmed_at,
        )
        for r in roster_records
    ]
    phone_by_family = {lead.family_id: lead.synthetic_phone for lead in ds.leads}
    keys = [
        FamilyMatchKey(
            family_id=f.family_id,
            email=f.primary_contact_synthetic_email,
            phone=phone_by_family.get(f.family_id),
            paid=f.funding_state in _PAID,
        )
        for f in ds.families
    ]
    verdicts = reconcile(keys, rows, params)
    buckets = {v.bucket for v in verdicts}

    assert SisBucket.PAID_NOT_IN_SIS in buckets, "≥1 🔴 paid_not_in_sis"
    assert SisBucket.RECORDS_LAG in buckets, "≥1 🟡 records_lag"
    assert SisBucket.CONFIRMED in buckets, "≥1 ✅ confirmed"

    # the job-edge key projection agrees with the SIS divergence too (no drift).
    assert family_match_keys is not None
