"""Content-record grounding gate tests — A-10, CONTENT_SPEC §9 (INV-3/INV-4).

The S2 gate (`app/core/eval_gate.py`) already enforces V-1..V-4 over the
`EnrollmentDraftProposal` (`.body`). A-10 extends that *same* canonical gate so
an S4 `ContentCandidate` (`.copy_text`, closed `audience_tag`, `claims`) flows
through it too, with the full §9.6 `ValidationResult` shape and a V-4 wired to
`BrandRule` never-rules. There is NO second gate.

Per CLAUDE.md §4.2: each rule keeps a PASS and a MUST-BLOCK case (INV-4
fail-closed). These tests read every threshold from the committed example params
(no magic number — INV-11), mirroring `test_message_safety_grounding.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.schemas.brand import (
    BrandRule,
    EnforcedBy,
    RuleType,
    Severity,
)
from app.ai.schemas.content import (
    AudienceTag,
    Channel,
    ContentCandidate,
    ContentFormat,
    Decision,
    GeneratedBy,
    HumanDecision,
    LifecycleStage,
    Provenance,
)
from app.core.eval_gate import RuleVerdict, ValidationResult, evaluate_message
from app.core.params import Params, load_params
from app.core.settings import Settings

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


@pytest.fixture
def settings_no_key() -> Settings:
    s = Settings()
    assert s.llm_available is False
    return s


def on_brand_judge(record: object, never_rules: list[str]) -> float:
    """Deterministic stub judge: a high conformance score (on-brand)."""
    return 0.99


def _provenance() -> Provenance:
    return Provenance(generated_by=GeneratedBy.LLM, created_at="2026-06-14T00:00:00Z")


def _candidate(*, copy_text: str, audience: AudienceTag = AudienceTag.PROSPECTIVE_PARENT,
               claims: list[str] | None = None) -> ContentCandidate:
    return ContentCandidate(
        id="cand-1",
        batch_id="batch-1",
        prompt="draft a friendly enrollment caption",
        channel=Channel.EMAIL,
        format=ContentFormat.SHORT_CAPTION,
        concept="warm enrollment invite",
        copy=copy_text,
        claims=claims or [],
        audience_tag=audience,
        lifecycle=LifecycleStage.CANDIDATE,
        decision=HumanDecision(decision=Decision.PENDING),
        provenance=_provenance(),
    )


# --------------------------------------------------------------------------- #
# §9.6 — passed is the AND of v1..v4; threshold_ref required/non-empty.
# --------------------------------------------------------------------------- #
def test_validation_result_passed_is_and_of_v1_v4() -> None:
    """`passed == v1 ∧ v2 ∧ v3 ∧ v4` for every combination; threshold_ref required."""
    for v1 in (RuleVerdict.PASS, RuleVerdict.FAIL):
        for v2 in (RuleVerdict.PASS, RuleVerdict.FAIL):
            for v3 in (RuleVerdict.PASS, RuleVerdict.FAIL):
                for v4 in (RuleVerdict.PASS, RuleVerdict.FAIL):
                    expected = all(
                        v is RuleVerdict.PASS for v in (v1, v2, v3, v4)
                    )
                    result = ValidationResult(
                        v1_schema=v1,
                        v2_grounding=v2,
                        v3_coppa=v3,
                        v4_onbrand=v4,
                        passed=expected,
                        threshold_ref="eval_thresholds.message_safety_grounding",
                    )
                    assert result.passed is expected

    # threshold_ref is required and non-empty (§9.3/§9.6).
    result = ValidationResult(
        v1_schema=RuleVerdict.PASS,
        v2_grounding=RuleVerdict.PASS,
        v3_coppa=RuleVerdict.PASS,
        v4_onbrand=RuleVerdict.PASS,
        passed=True,
    )
    assert result.threshold_ref
    # The §9.6 enrichment fields default to None so existing S2 callers don't break.
    assert result.subject_ref is None
    assert result.subject_type is None
    assert result.judge_model_ref is None
    assert result.provenance_ref is None


# --------------------------------------------------------------------------- #
# A ContentCandidate flows through the SAME gate (copy_text, audience_tag).
# --------------------------------------------------------------------------- #
def test_content_candidate_flows_through_gate(
    params: Params, settings_no_key: Settings
) -> None:
    # Clean, adult-audience, sourced/empty claims, on-brand judge ⇒ passes.
    clean = _candidate(
        copy_text="Thanks for your interest in GT School — we'd love to help your "
        "family explore enrollment.",
    )
    ok = evaluate_message(
        clean, settings=settings_no_key, params=params, brand_judge=on_brand_judge,
        audience=clean.audience_tag.value,
    )
    assert isinstance(ok, ValidationResult)
    assert ok.passed is True
    assert ok.v1_schema == "pass"
    assert ok.v2_grounding == "pass"
    assert ok.v3_coppa == "pass"
    assert ok.v4_onbrand == "pass"

    # MUST-BLOCK: a "4X speed" multiplier in copy_text ⇒ V-2 FAIL (INV-4).
    multiplier = _candidate(
        copy_text="Our students learn at 4X speed compared to traditional schools.",
    )
    blocked = evaluate_message(
        multiplier, settings=settings_no_key, params=params, brand_judge=on_brand_judge,
        audience=multiplier.audience_tag.value,
    )
    assert blocked.passed is False
    assert blocked.v2_grounding == "fail"
    assert "v2_grounding" in blocked.failed_rules

    # MUST-BLOCK: a minor-targeting signal in copy_text ⇒ V-3 FAIL (INV-6).
    minor = _candidate(
        copy_text="Hey kids! Ask your 12-year-old @timmy_grade6 to sign up.",
    )
    minor_blocked = evaluate_message(
        minor, settings=settings_no_key, params=params, brand_judge=on_brand_judge,
        audience=minor.audience_tag.value,
    )
    assert minor_blocked.passed is False
    assert minor_blocked.v3_coppa == "fail"
    assert "v3_coppa" in minor_blocked.failed_rules


# --------------------------------------------------------------------------- #
# V-4 wired to BrandRule: an ACTIVE `never` rule blocks absolutely.
# --------------------------------------------------------------------------- #
def test_v4_blocks_active_never_brand_rule(
    params: Params, settings_no_key: Settings
) -> None:
    active_never = BrandRule(
        id="rule-1",
        rule_type=RuleType.NEVER,
        statement="speed multipliers",
        enforced_by=EnforcedBy.BRAND,
        severity=Severity.BLOCK,
        active=True,
        provenance=_provenance(),
    )
    inactive_never = BrandRule(
        id="rule-2",
        rule_type=RuleType.NEVER,
        statement="speed multipliers",
        enforced_by=EnforcedBy.BRAND,
        severity=Severity.BLOCK,
        active=False,
        provenance=_provenance(),
    )

    record = _candidate(
        copy_text="GT School uses speed multipliers to teach your child.",
    )

    # Active never-rule violated ⇒ V-4 FAIL even with an on-brand judge.
    result = evaluate_message(
        record, settings=settings_no_key, params=params, brand_judge=on_brand_judge,
        audience=record.audience_tag.value, brand_rules=[active_never],
    )
    assert result.v4_onbrand == "fail"
    assert result.passed is False
    assert "v4_onbrand" in result.failed_rules

    # An INACTIVE never-rule does NOT block (judge decides; on-brand ⇒ pass).
    inactive_result = evaluate_message(
        record, settings=settings_no_key, params=params, brand_judge=on_brand_judge,
        audience=record.audience_tag.value, brand_rules=[inactive_never],
    )
    assert inactive_result.v4_onbrand == "pass"
    assert inactive_result.passed is True


# --------------------------------------------------------------------------- #
# Fail-closed still holds for content records: no judge + no key ⇒ V-4 DENY.
# --------------------------------------------------------------------------- #
def test_content_v4_denies_when_judge_unavailable(
    params: Params, settings_no_key: Settings
) -> None:
    record = _candidate(
        copy_text="Thanks for reaching out — we're glad to help your family enroll.",
    )
    result = evaluate_message(
        record, settings=settings_no_key, params=params,
        audience=record.audience_tag.value,
    )
    assert result.v4_onbrand == "fail"
    assert result.passed is False
    assert result.brand_score is None
