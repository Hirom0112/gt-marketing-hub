"""Close-tips grounding eval — the S9 W5 prompt RED test (CLAUDE §4.2, INV-3/INV-4).

Per CLAUDE.md §4.2 the **golden-set eval IS the red test**: the close-tips
proposal is not "done" until its golden-set eval passes its params threshold
(INV-3), and the grounding gate gets a PASSING and a BLOCKING (MUST-BLOCK) case
proving fail-closed (INV-4).

The close-tips proposal grounds "how to close this family" tips in the family's
``app_form.extracted_fields``. It crosses the SAME canonical grounding gate the
enrollment draft uses (A-10): the gate reads ``proposal.body`` (the rendered tips)
and ``proposal.claims`` (each tip with its ``source_ref``) structurally. A tip
that asserts a fact NOT present in ``extracted_fields`` carries no grounding
``source_ref`` ⇒ it is an unsourced empirical claim ⇒ V-2 FAIL ⇒ BLOCKED, not
softened (the hallucinated-fact block).

The golden set is purely SYNTHETIC (INV-1). The threshold is read from
``params.eval_thresholds.close_tips.min_grounding`` (INV-11) — a param drift moves
the floor and the verdict that follows, so a magic number cannot creep into code.

Deterministic without a local ``params/params.yaml`` (gitignored): the committed
``params/params.example.yaml`` is passed explicitly, mirroring
``tests/evals/test_message_safety_grounding.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from app.ai.schemas.close_tips import CloseTip, CloseTipsProposal
from app.core.eval_gate import ValidationResult, evaluate_message
from app.core.params import Params, load_params
from app.core.settings import Settings

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
GOLDEN = Path(__file__).resolve().parent / "golden" / "close_tips.jsonl"

# Operator-facing advice — a COPPA-safe audience (the tips are for leadership/the
# operator, never sent to a minor). A minor-targeting tip still trips V-3 via the
# body text patterns regardless of this audience.
OPERATOR_AUDIENCE = "leadership"


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


@pytest.fixture
def settings_no_key() -> Settings:
    """A settings snapshot with no Anthropic key ⇒ ``llm_available`` is False."""
    s = Settings()
    assert s.llm_available is False
    return s


def on_brand_judge(proposal: CloseTipsProposal, never_rules: list[str]) -> float:
    """Deterministic stub judge: a high conformance score (on-brand)."""
    return 0.99


def off_brand_judge(proposal: CloseTipsProposal, never_rules: list[str]) -> float:
    """Deterministic stub judge: a low conformance score (off-brand)."""
    return 0.0


def _proposal_from_row(row: dict[str, object]) -> CloseTipsProposal:
    """Build a `CloseTipsProposal` from one close-tips golden jsonl row."""
    tips_raw = row["tips"]
    assert isinstance(tips_raw, list)
    return CloseTipsProposal(
        family_id=UUID(str(row["family_id"])),
        tips=[CloseTip(text=str(t["text"]), source_ref=t["source_ref"]) for t in tips_raw],
    )


def _golden_rows() -> list[dict[str, object]]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# 1. PASSING case — grounded, COPPA-safe, on-brand tips PASS the gate.
# --------------------------------------------------------------------------- #
def test_close_tips_passes_grounded_proposal(params: Params, settings_no_key: Settings) -> None:
    """A grounded tip (cites an extracted_fields key) + a self-evident tip PASS."""
    proposal = CloseTipsProposal(
        family_id=UUID("00000000-0000-0000-0000-0000000000c1"),
        tips=[
            CloseTip(
                text="Lead with the homeschool funding path: they self-reported homeschooling.",
                source_ref="extracted_fields:prior_schooling",
            ),
            CloseTip(
                text="Offer to walk the parents through the enrollment steps.", source_ref=None
            ),
        ],
    )
    result = evaluate_message(
        proposal,
        settings=settings_no_key,
        params=params,
        brand_judge=on_brand_judge,
        audience=OPERATOR_AUDIENCE,
    )
    assert isinstance(result, ValidationResult)
    assert result.passed is True
    assert result.v2_grounding == "pass"
    assert result.v3_coppa == "pass"
    assert result.failed_rules == []


# --------------------------------------------------------------------------- #
# 2. BLOCKING case — a hallucinated fact NOT in extracted_fields is BLOCKED.
# --------------------------------------------------------------------------- #
def test_close_tips_blocks_hallucinated_fact(params: Params, settings_no_key: Settings) -> None:
    """A tip asserting a fact absent from extracted_fields ⇒ unsourced ⇒ V-2 BLOCK.

    INV-4 fail-closed: the gate BLOCKS the hallucinated-fact tip; it does NOT
    soften it to pass. The "two younger siblings" fact is nowhere in the family's
    extracted application fields, so the empirical tip carries no grounding ref.
    """
    proposal = CloseTipsProposal(
        family_id=UUID("00000000-0000-0000-0000-0000000000c5"),
        tips=[
            CloseTip(
                text="Mention their 2 younger siblings also enrolling next year to build urgency.",
                source_ref=None,
            )
        ],
    )
    result = evaluate_message(
        proposal,
        settings=settings_no_key,
        params=params,
        brand_judge=on_brand_judge,
        audience=OPERATOR_AUDIENCE,
    )
    assert result.passed is False
    assert result.v2_grounding == "fail"
    assert "v2_grounding" in result.failed_rules


# --------------------------------------------------------------------------- #
# 3. Fail-closed — no judge ⇒ V-4 DENY even for clean grounded tips (§9.4).
# --------------------------------------------------------------------------- #
def test_close_tips_v4_denies_without_judge(params: Params, settings_no_key: Settings) -> None:
    proposal = CloseTipsProposal(
        family_id=UUID("00000000-0000-0000-0000-0000000000c3"),
        tips=[
            CloseTip(
                text="Send a friendly reminder the application is still open.", source_ref=None
            )
        ],
    )
    result = evaluate_message(
        proposal, settings=settings_no_key, params=params, audience=OPERATOR_AUDIENCE
    )
    assert result.v4_onbrand == "fail"
    assert result.passed is False
    assert result.brand_score is None


# --------------------------------------------------------------------------- #
# 4. The golden-set eval — THIS is the prompt red test (CLAUDE §4.2, INV-3).
# --------------------------------------------------------------------------- #
def test_close_tips_golden_set_meets_threshold(params: Params, settings_no_key: Settings) -> None:
    rows = _golden_rows()
    assert len(rows) >= 8, "golden set must hold a substantive PASS/BLOCK mix"

    must_block = [r for r in rows if r["expected_passed"] is False]
    must_pass = [r for r in rows if r["expected_passed"] is True]
    assert must_block, "golden set must contain MUST-BLOCK rows (the hallucinated-fact block)"
    assert must_pass, "golden set must contain PASS rows"

    correct = 0
    unverifiable_slipped = 0
    max_unverifiable = params.eval_thresholds.close_tips.max_unverifiable_claims

    for row in rows:
        proposal = _proposal_from_row(row)
        result = evaluate_message(
            proposal,
            settings=settings_no_key,
            params=params,
            brand_judge=on_brand_judge,
            audience=OPERATOR_AUDIENCE,
        )
        if result.passed == row["expected_passed"]:
            correct += 1
        # No unverifiable/hallucinated claim may slip through a PASS verdict (max=0).
        if result.passed and result.v2_grounding == "fail":
            unverifiable_slipped += 1

    # 100% of MUST-BLOCK rows blocked, 100% of PASS rows pass.
    for row in must_block:
        result = evaluate_message(
            _proposal_from_row(row),
            settings=settings_no_key,
            params=params,
            brand_judge=on_brand_judge,
            audience=OPERATOR_AUDIENCE,
        )
        assert result.passed is False, f"MUST-BLOCK row leaked: {row['label']}"

    for row in must_pass:
        result = evaluate_message(
            _proposal_from_row(row),
            settings=settings_no_key,
            params=params,
            brand_judge=on_brand_judge,
            audience=OPERATOR_AUDIENCE,
        )
        assert result.passed is True, f"PASS row wrongly blocked: {row['label']}"

    accuracy = correct / len(rows)
    min_grounding = params.eval_thresholds.close_tips.min_grounding
    assert accuracy >= min_grounding, f"close-tips grounding accuracy {accuracy} < {min_grounding}"
    assert unverifiable_slipped <= max_unverifiable
