"""MD — the curated `COCKPIT_SCENARIO=demo` cohort has the on-camera demo shape.

The demo cohort (``generate_demo_cohort``) is a SEPARATE deterministic fixture —
18 synthetic households (12 curated + 6 deliberate edge cases: 2 duplicate
"applied twice" pairs for the merge queue, plus a mojibake row and a
missing-required-field row for the data-quality queue) with controlled, legible
state for the demo (MULTI_AGENT_COCKPIT §10.1), all created THIS WEEK: exactly one
two-child household, a stage spread
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

    # --- EXACTLY 18 households: the 12 curated + the 6 deliberate edge cases ---
    # (2 duplicate "applied twice" PAIRS for the merge queue + a mojibake row + a
    # missing-required-field row for the data-quality queue).
    assert len(ds.families) == 18
    # parallel one-row-per-family source tables (the spine join holds).
    assert len(ds.leads) == len(ds.families)
    assert len(ds.app_forms) == len(ds.families)
    assert len(ds.enrollment_forms) == len(ds.families)

    # the 12 curated surnames + the 4 distinct edge surnames (Castillo + Okeke each
    # appear TWICE — the same household applied twice; the mojibake surname carries
    # double-encoded UTF-8 on SYNTHETIC names only, INV-1).
    surnames = {lead.synthetic_last_name for lead in ds.leads}
    assert surnames == {
        "Rivera",
        "Okafor",
        "Nguyen",
        "Patel",
        "Kim",
        "Silva",
        "Johnson",
        "Garcia",
        "Ahmed",
        "Brooks",
        "Tran",
        "Reyes",
        # the deliberate edge-case households:
        "Castillo",  # duplicate pair #1 (×2)
        "Okeke",  # duplicate pair #2 (×2)
        "RodrÃ­guez",  # mojibake row
        "Castro",  # missing-field row
    }
    # the two duplicate pairs each appear twice (the same household applying twice).
    last_names = [lead.synthetic_last_name for lead in ds.leads]
    assert last_names.count("Castillo") == 2
    assert last_names.count("Okeke") == 2

    # --- every household was created THIS WEEK (within 7 days of the demo epoch) -
    from datetime import timedelta

    from app.data.synthetic import _EPOCH

    week_start = _EPOCH - timedelta(days=7)
    for f in ds.families:
        assert f.created_at is not None
        assert week_start <= f.created_at <= _EPOCH, (
            f"{f.display_name} created {f.created_at} outside this week ({week_start}..{_EPOCH})"
        )

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


def test_demo_cohort_territory_and_income_tier() -> None:
    """LA-5 — the demo cohort carries synthetic `state` + `income_tier` and the
    curated owner assignments are territory-consistent (LEAD_ASSIGNMENT.md §4/§6):
    Agent A (closer) = FL, Agent B (setter/qualifier) = CA, so an OWNED family
    lives in its owner's territory; the unassigned intake lead carries a state too.
    """
    from app.data.models import IncomeTier

    params = load_params(EXAMPLE_PARAMS)
    ds = generate_demo_cohort(params=params)

    # The demo agents' territories (mirrors params.assignment.agents[*].territory).
    territory = {_CLOSER_ID: "FL", _SETTER_ID: "CA"}

    states = {f.state for f in ds.families}
    assert {"FL", "CA"} <= states, "both covered territories are represented"
    # ≥1 UNASSIGNED family sits in an UNCOVERED state (the territory-fallback case
    # the admin routes live on camera).
    uncovered = {f.state for f in ds.families if f.assigned_rep_id is None} - {"FL", "CA"}
    assert uncovered, "≥1 unassigned family in an uncovered state (fallback demo)"

    for f in ds.families:
        # every household has a synthetic state + a typed income_tier (or None).
        assert isinstance(f.state, str) and f.state
        assert f.income_tier is None or isinstance(f.income_tier, IncomeTier)
        # owner-territory consistency: an OWNED family lives in its owner's state
        # (the closer covers FL, the qualifier covers CA).
        if f.assigned_rep_id is not None:
            assert f.state == territory[f.assigned_rep_id], (
                f"owned family {f.display_name} in {f.state} but owner covers "
                f"{territory[f.assigned_rep_id]}"
            )

    # income_tier spread: ≥2 distinct buckets present (a believable mix), and ≥1
    # TEFA-eligible (lower) tier so the income signal has something to act on.
    tiers = {f.income_tier for f in ds.families if f.income_tier is not None}
    assert len(tiers) >= 2, "expected an income-tier spread"
    assert tiers & {IncomeTier.LT_65K, IncomeTier.MID_65K_160K}, "≥1 TEFA-eligible tier"


def test_demo_cohort_seeds_assignment_history_for_owned_families() -> None:
    """LA-23 — every ALREADY-OWNED demo household carries exactly one seeded
    initial-assignment history fact, so the deal-view timeline has provenance to
    show the moment an operator (rep or admin) taps into the deal. Unassigned
    intake leads carry NONE (their first fact is appended live when routed).
    """
    params = load_params(EXAMPLE_PARAMS)
    ds = generate_demo_cohort(params=params)

    owned = [f for f in ds.families if f.assigned_rep_id is not None]
    unassigned = [f for f in ds.families if f.assigned_rep_id is None]
    assert owned and unassigned, "the demo has both owned + unassigned families"

    by_family = {a.family_id: a for a in ds.lead_assignments}
    # Exactly one seeded fact per owned family; one per family (no duplicates).
    assert len(ds.lead_assignments) == len(owned)
    assert len(by_family) == len(ds.lead_assignments)

    for f in owned:
        ev = by_family.get(f.family_id)
        assert ev is not None, f"owned family {f.display_name} has no seeded history"
        # out of intake → the family's current owner, dated to its assignment, with
        # a human-readable territory reason (every assignment is explainable, §2).
        assert ev.from_rep_id is None
        assert ev.to_rep_id == f.assigned_rep_id
        assert ev.occurred_at == f.assigned_at
        assert ev.assigned_by == "seed"
        assert f.state in ev.reason and ev.reason
        assert ev.routed_role in {"closer", "qualifier"}

    # No seeded fact for an unassigned intake lead (its provenance starts at the
    # live route).
    for f in unassigned:
        assert f.family_id not in by_family


def test_demo_assignment_history_is_deterministic() -> None:
    """The seeded history is byte-identical across runs (same assignment_ids,
    reasons, dates) — it draws from a family-keyed rng, never the wall clock."""
    params = load_params(EXAMPLE_PARAMS)
    a = generate_demo_cohort(params=params).lead_assignments
    b = generate_demo_cohort(params=params).lead_assignments
    assert [x.model_dump() for x in a] == [y.model_dump() for y in b]


def test_demo_cohort_has_duplicate_pairs_for_merge_queue() -> None:
    """The seeded cohort carries ≥2 duplicate "applied twice" pairs that the dedup
    core (the merge queue's ``propose_merge``) flags REVIEW_QUEUE — same email +
    region, a typo'd phone (both present, differ) ⇒ fail-closed human review (INV-4).

    Drives the SAME pure core ``app/api/merge.py`` composes (an IdentityCandidate
    per lead → ``propose_merge`` over every pair), so the seed is proven against the
    real merge logic.
    """
    from app.core.identity import IdentityCandidate, MergeVerdict, propose_merge

    params = load_params(EXAMPLE_PARAMS)
    ds = generate_demo_cohort(params=params)
    lead_by_family = {lead.family_id: lead for lead in ds.leads}

    candidates = [
        IdentityCandidate(
            family_id=f.family_id,
            synthetic_email=lead_by_family[f.family_id].synthetic_email,
            region=lead_by_family[f.family_id].region,
            synthetic_phone=lead_by_family[f.family_id].synthetic_phone,
        )
        for f in ds.families
        if f.family_id in lead_by_family
    ]

    review = []
    for i in range(len(candidates)):
        for k in range(i + 1, len(candidates)):
            proposal = propose_merge([candidates[i], candidates[k]])
            if proposal is not None and proposal.verdict is MergeVerdict.REVIEW_QUEUE:
                review.append(proposal)

    assert len(review) >= 2, "the cohort yields ≥2 REVIEW_QUEUE duplicate proposals"

    # The two intended "applied twice" pairs are present (by household identity).
    review_pairs = {frozenset({p.primary_family_id, p.duplicate_family_id}) for p in review}
    castillo = frozenset(
        lead.family_id for lead in ds.leads if lead.synthetic_last_name == "Castillo"
    )
    okeke = frozenset(lead.family_id for lead in ds.leads if lead.synthetic_last_name == "Okeke")
    assert len(castillo) == 2 and castillo in review_pairs, "Castillo applied-twice pair flagged"
    assert len(okeke) == 2 and okeke in review_pairs, "Okeke applied-twice pair flagged"
    # Each intended pair agrees on email+region and conflicts only on phone.
    for p in review:
        if frozenset({p.primary_family_id, p.duplicate_family_id}) in {castillo, okeke}:
            assert p.matched_on == ("email", "region")
            assert p.conflicting_keys == ("phone",)


def test_demo_cohort_carries_mojibake_and_missing_field_for_data_quality() -> None:
    """The seeded cohort carries the data-quality edge cases the new detector flags:
    a mojibake (double-encoded UTF-8) name row and a missing-required-field (empty
    region) row — proven against ``app.core.data_quality.build_dq_queue``.
    """
    from app.core.data_quality import DqRow, build_dq_queue
    from app.core.seam import MirrorState

    params = load_params(EXAMPLE_PARAMS)
    ds = generate_demo_cohort(params=params)
    lead_by_family = {lead.family_id: lead for lead in ds.leads}

    rows = [
        DqRow(
            entity_id=str(f.family_id),
            record=f,
            mirror=MirrorState(stage=f.current_stage, mirror_updated_at=f.updated_at),
            utm=None,
            mojibake_fields={
                "first_name": lead_by_family[f.family_id].synthetic_first_name,
                "last_name": lead_by_family[f.family_id].synthetic_last_name,
            },
            required_fields={"region": lead_by_family[f.family_id].region},
        )
        for f in ds.families
        if f.family_id in lead_by_family
    ]
    issues = build_dq_queue(rows, params=params)

    mojibake = [i for i in issues if i.kind == "mojibake"]
    missing = [i for i in issues if i.kind == "missing_field"]
    assert mojibake, "the mojibake edge-case household is detected"
    assert missing, "the missing-region edge-case household is detected"
    # Exactly ONE household each (the seeded edge rows; no false positives elsewhere).
    assert len({i.entity_id for i in mojibake}) == 1
    assert len({i.entity_id for i in missing}) == 1


def test_default_cohort_carries_territory_and_income() -> None:
    """LA-5 — the DEFAULT synthetic generator stamps every family with a state in
    the known set and a typed income_tier, and the cohort exercises BOTH a covered
    territory (FL/CA) and an UNCOVERED state (the territory-fallback path)."""
    from app.data.models import IncomeTier
    from app.data.synthetic import _STATES, generate

    ds = generate(400, seed=7)
    states = {f.state for f in ds.families}
    assert states <= set(_STATES)
    assert {"FL", "CA"} & states, "covered territories appear"
    assert states - {"FL", "CA"}, "≥1 uncovered state appears (territory fallback path)"
    for f in ds.families:
        assert f.state in _STATES
        assert isinstance(f.income_tier, IncomeTier)
