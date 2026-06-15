"""Message safety/grounding gate tests — FR-4.3, INV-3/INV-4 (CONTENT_SPEC §9).

This is the architectural heart of the product: the gate that enforces
INV-3 (no AI action reaches a human un-evaled) and INV-4 (the gate BLOCKS,
never softens — fail-closed). `pass = V-1 ∧ V-2 ∧ V-3 ∧ V-4`; ANY single FAIL
⇒ BLOCKED, with no soften-and-continue path (§9.3).

Per CLAUDE.md §4.2 each rule gets a PASSING and a BLOCKING (MUST-BLOCK) test,
and the golden-set eval (`test_draft_golden_set_meets_threshold`) IS the prompt
red test: 100% of MUST-BLOCK rows blocked, 100% of PASS rows passing, zero
unverifiable claims slipping through, measured grounding accuracy ≥ the
params `min_grounding` floor (no magic number in code — INV-11).

Deterministic without a local `params/params.yaml` (gitignored, not created):
the committed `params/params.example.yaml` is passed explicitly, mirroring
`tests/unit/test_work_queue.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from app.ai.schemas.enrollment_draft import Claim, DraftAction, EnrollmentDraftProposal
from app.core.eval_gate import ValidationResult, evaluate_message
from app.core.params import Params, load_params
from app.core.settings import Settings

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
GOLDEN = Path(__file__).resolve().parent / "golden" / "enrollment_drafts.jsonl"

FID = UUID("00000000-0000-0000-0000-0000000000aa")


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


@pytest.fixture
def settings_no_key() -> Settings:
    """A settings snapshot with no Anthropic key ⇒ `llm_available` is False.

    This mirrors the build env: there is NO key, so a real judge is
    unavailable and V-4 must rely on an injected stub or deny (§9.4).
    """
    s = Settings()
    assert s.llm_available is False
    return s


def on_brand_judge(proposal: EnrollmentDraftProposal, never_rules: list[str]) -> float:
    """Deterministic stub judge: a high conformance score (on-brand)."""
    return 0.99


def off_brand_judge(proposal: EnrollmentDraftProposal, never_rules: list[str]) -> float:
    """Deterministic stub judge: a low conformance score (off-brand)."""
    return 0.0


# --------------------------------------------------------------------------- #
# 1. Happy path — a clean grounded, COPPA-safe, on-brand draft PASSES.
# --------------------------------------------------------------------------- #
def test_gate_passes_grounded_message(params: Params, settings_no_key: Settings) -> None:
    proposal = EnrollmentDraftProposal(
        action=DraftAction.EMAIL,
        family_id=FID,
        body="Thanks for your interest in GT School. We'd love to walk your "
        "family through the enrollment steps.",
        claims=[Claim(text="GT School helps families enroll.", source_ref="kb://about/overview")],
    )
    result = evaluate_message(
        proposal, settings=settings_no_key, params=params, brand_judge=on_brand_judge
    )
    assert isinstance(result, ValidationResult)
    assert result.passed is True
    assert result.v1_schema == "pass"
    assert result.v2_grounding == "pass"
    assert result.v3_coppa == "pass"
    assert result.v4_onbrand == "pass"
    assert result.failed_rules == []
    assert result.threshold_ref == "eval_thresholds.message_safety_grounding"


# --------------------------------------------------------------------------- #
# 2. MUST-BLOCK — an unverifiable "4X speed" multiplier FAILS V-2 (INV-4).
# --------------------------------------------------------------------------- #
def test_gate_blocks_unverifiable_4x_claim(params: Params, settings_no_key: Settings) -> None:
    proposal = EnrollmentDraftProposal(
        action=DraftAction.EMAIL,
        family_id=FID,
        body="Our students learn at 4X speed compared to traditional schools.",
        claims=[],
    )
    result = evaluate_message(
        proposal, settings=settings_no_key, params=params, brand_judge=on_brand_judge
    )
    assert result.passed is False
    assert result.v2_grounding == "fail"
    assert "v2_grounding" in result.failed_rules


# --------------------------------------------------------------------------- #
# 3. MUST-BLOCK — a minor-targeting / minor-PII signal FAILS V-3 (INV-4).
# --------------------------------------------------------------------------- #
def test_gate_blocks_minor_targeting(params: Params, settings_no_key: Settings) -> None:
    proposal = EnrollmentDraftProposal(
        action=DraftAction.EMAIL,
        family_id=FID,
        body="Reach out to your 12-year-old @timmy_grade6 to confirm his schedule.",
        claims=[],
    )
    result = evaluate_message(
        proposal, settings=settings_no_key, params=params, brand_judge=on_brand_judge
    )
    assert result.passed is False
    assert result.v3_coppa == "fail"
    assert "v3_coppa" in result.failed_rules


# --------------------------------------------------------------------------- #
# 4. Fail-closed — no injected judge + no key ⇒ V-4 DENY even if 1/2/3 pass.
# --------------------------------------------------------------------------- #
def test_v4_denies_when_judge_unavailable(params: Params, settings_no_key: Settings) -> None:
    """§9.4: an unavailable judge degrades the gate to DENY, never silent pass."""
    proposal = EnrollmentDraftProposal(
        action=DraftAction.FAQ,
        family_id=FID,
        body="Thanks for reaching out. We're glad to help your family enroll.",
        claims=[],
    )
    # No brand_judge injected; settings.llm_available is False ⇒ judge unavailable.
    result = evaluate_message(proposal, settings=settings_no_key, params=params)
    assert result.v1_schema == "pass"
    assert result.v2_grounding == "pass"
    assert result.v3_coppa == "pass"
    assert result.v4_onbrand == "fail"
    assert result.passed is False
    assert "v4_onbrand" in result.failed_rules
    assert result.brand_score is None


# --------------------------------------------------------------------------- #
# 5. V-2 — an unsourced empirical claim FAILS; a sourced/self-evident one passes.
# --------------------------------------------------------------------------- #
def test_unsourced_empirical_claim_fails_v2(params: Params, settings_no_key: Settings) -> None:
    unsourced = EnrollmentDraftProposal(
        action=DraftAction.NUDGE,
        family_id=FID,
        body="Ninety percent of our families re-enroll the following year.",
        claims=[
            Claim(
                text="Ninety percent of our families re-enroll the following year.", source_ref=None
            )
        ],
    )
    blocked = evaluate_message(
        unsourced, settings=settings_no_key, params=params, brand_judge=on_brand_judge
    )
    assert blocked.passed is False
    assert blocked.v2_grounding == "fail"
    assert "v2_grounding" in blocked.failed_rules

    sourced = EnrollmentDraftProposal(
        action=DraftAction.NUDGE,
        family_id=FID,
        body="Many families complete enrollment in under ten minutes.",
        claims=[
            Claim(
                text="Many families complete enrollment in under ten minutes.",
                source_ref="kb://enrollment/completion-time-2026",
            )
        ],
    )
    allowed = evaluate_message(
        sourced, settings=settings_no_key, params=params, brand_judge=on_brand_judge
    )
    assert allowed.v2_grounding == "pass"


# --------------------------------------------------------------------------- #
# 6. Off-brand never-rule / low score FAILS V-4 even with a judge present.
# --------------------------------------------------------------------------- #
def test_v4_blocks_off_brand_low_score(params: Params, settings_no_key: Settings) -> None:
    proposal = EnrollmentDraftProposal(
        action=DraftAction.FAQ,
        family_id=FID,
        body="Thanks for reaching out. We're glad to help your family enroll.",
        claims=[],
    )
    result = evaluate_message(
        proposal, settings=settings_no_key, params=params, brand_judge=off_brand_judge
    )
    assert result.v4_onbrand == "fail"
    assert result.passed is False
    assert "v4_onbrand" in result.failed_rules


# --------------------------------------------------------------------------- #
# 7. Golden-set eval — THIS is the prompt red test (CLAUDE §4.2, INV-3).
# --------------------------------------------------------------------------- #
def test_draft_golden_set_meets_threshold(params: Params, settings_no_key: Settings) -> None:
    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    assert len(rows) >= 8, "golden set must hold a substantive PASS/BLOCK mix"

    must_block = [r for r in rows if r["expected_passed"] is False]
    must_pass = [r for r in rows if r["expected_passed"] is True]
    assert must_block, "golden set must contain MUST-BLOCK rows"
    assert must_pass, "golden set must contain PASS rows"

    correct = 0
    unverifiable_slipped = 0
    max_unverifiable = params.eval_thresholds.message_safety_grounding.max_unverifiable_claims

    for row in rows:
        # An audience outside the COPPA-safe set is modeled via the body alone in
        # the proposal schema; minor-targeting rows carry the signal in `body`.
        proposal = EnrollmentDraftProposal(
            action=DraftAction(row["action"]),
            family_id=UUID(row["family_id"]),
            body=row["body"],
            claims=[Claim(text=c["text"], source_ref=c["source_ref"]) for c in row["claims"]],
        )
        result = evaluate_message(
            proposal,
            settings=settings_no_key,
            params=params,
            brand_judge=on_brand_judge,
            audience=row["audience"],
        )
        if result.passed == row["expected_passed"]:
            correct += 1
        # No unverifiable claim may slip through a PASS verdict (max=0).
        if result.passed and result.v2_grounding == "fail":
            unverifiable_slipped += 1

    # 100% of MUST-BLOCK rows blocked, 100% of PASS rows pass.
    for row in must_block:
        proposal = EnrollmentDraftProposal(
            action=DraftAction(row["action"]),
            family_id=UUID(row["family_id"]),
            body=row["body"],
            claims=[Claim(text=c["text"], source_ref=c["source_ref"]) for c in row["claims"]],
        )
        result = evaluate_message(
            proposal,
            settings=settings_no_key,
            params=params,
            brand_judge=on_brand_judge,
            audience=row["audience"],
        )
        assert result.passed is False, f"MUST-BLOCK row leaked: {row['label']}"

    for row in must_pass:
        proposal = EnrollmentDraftProposal(
            action=DraftAction(row["action"]),
            family_id=UUID(row["family_id"]),
            body=row["body"],
            claims=[Claim(text=c["text"], source_ref=c["source_ref"]) for c in row["claims"]],
        )
        result = evaluate_message(
            proposal,
            settings=settings_no_key,
            params=params,
            brand_judge=on_brand_judge,
            audience=row["audience"],
        )
        assert result.passed is True, f"PASS row wrongly blocked: {row['label']}"

    accuracy = correct / len(rows)
    min_grounding = params.eval_thresholds.message_safety_grounding.min_grounding
    assert accuracy >= min_grounding, f"grounding accuracy {accuracy} < {min_grounding}"
    assert unverifiable_slipped <= max_unverifiable
