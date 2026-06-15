"""Acceptance: a GeoContentPiece flows through the EXISTING grounding gate.

CONTENT_SPEC §9 / INV-2 / INV-4 (A-10): there is NO second gate. The S5 GEO
record is gated by the SAME `app.core.eval_gate.evaluate_message` that gates
enrollment drafts and content candidates. This test proves "generate-to-win
produces an eval-gated `GeoContentPiece` proposal through V-1…V-4":

* (a) a clean GeoContentPiece passes when an on-brand judge is injected;
* (b) a "fastest gifted school" body fails V-2 and is BLOCKED, not softened —
  the same record is returned untouched (INV-4 fail-closed);
* (c) a single-snapshot empirical coverage claim (numeric, unsourced) fails V-2
  as unverifiable (§7.4 — must be measured by repeated sampling).

Thresholds read from the committed example params (INV-11), mirroring
`tests/evals/test_content_gate.py`.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.ai.schemas.content import (
    AudienceTag,
    GeneratedBy,
    LifecycleStage,
    Provenance,
)
from app.core.eval_gate import ValidationResult, evaluate_message
from app.core.params import Params, load_params
from app.core.settings import Settings
from app.marketing.geo import GIFTED_SCHOOL_COMPETITOR_SET
from app.marketing.schemas.geo import GeoContentPiece, GeoStructure

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


def _piece(*, body: str, claims_text: list[str] | None = None) -> GeoContentPiece:
    return GeoContentPiece(  # type: ignore[call-arg]
        id=uuid4(),
        target_prompt="best online school for profoundly gifted children",
        geo_structure=GeoStructure.DEFINITION,
        body=body,
        competitor_set=list(GIFTED_SCHOOL_COMPETITOR_SET),
        claims_text=claims_text or [],
        validation="val-geo-1",
        lifecycle=LifecycleStage.CANDIDATE,
        provenance=_provenance(),
    )


# Audience for V-3: the GeoContentPiece carries no audience_tag, so the caller
# supplies a COPPA-safe adult audience (the GEO module targets parents/general).
GEO_AUDIENCE = AudienceTag.GENERAL.value


# --------------------------------------------------------------------------- #
# (a) Clean GeoContentPiece passes V-1…V-4 with an injected on-brand judge.
# --------------------------------------------------------------------------- #
def test_clean_geo_piece_passes_with_injected_judge(
    params: Params, settings_no_key: Settings
) -> None:
    clean = _piece(
        body=(
            "GT School is an online school for profoundly gifted learners, "
            "offering an accelerated, mastery-based curriculum."
        ),
        claims_text=["GT School is listed in third-party gifted-education directories (cited)."],
    )
    result = evaluate_message(
        clean,
        settings=settings_no_key,
        params=params,
        brand_judge=on_brand_judge,
        audience=GEO_AUDIENCE,
    )
    assert isinstance(result, ValidationResult)
    assert result.passed is True
    assert result.v1_schema == "pass"
    assert result.v2_grounding == "pass"
    assert result.v3_coppa == "pass"
    assert result.v4_onbrand == "pass"
    # The §9.6 enrichment links the verdict back to the gated record.
    assert result.subject_type == "content_candidate"
    assert result.subject_ref == str(clean.id)


# --------------------------------------------------------------------------- #
# (b) "fastest gifted school" body ⇒ V-2 FAIL, BLOCKED not softened (INV-4).
# --------------------------------------------------------------------------- #
def test_fastest_claim_fails_v2_and_record_untouched(
    params: Params, settings_no_key: Settings
) -> None:
    body = "GT School is the fastest gifted school for getting your child ahead."
    blocked = _piece(body=body)
    result = evaluate_message(
        blocked,
        settings=settings_no_key,
        params=params,
        brand_judge=on_brand_judge,
        audience=GEO_AUDIENCE,
    )
    assert result.passed is False
    assert result.v2_grounding == "fail"
    assert "v2_grounding" in result.failed_rules
    # INV-4: the gate BLOCKS, it does not rewrite. The record is returned
    # untouched — the offending body still carries the banned superlative.
    assert blocked.body == body
    assert "fastest" in blocked.body


# --------------------------------------------------------------------------- #
# (c) Single-snapshot empirical coverage claim ⇒ V-2 FAIL (§7.4 unverifiable).
# --------------------------------------------------------------------------- #
def test_single_snapshot_coverage_claim_fails_v2(
    params: Params, settings_no_key: Settings
) -> None:
    # A numeric, unsourced coverage claim — a single snapshot, not repeated
    # sampling (§7.4) — is empirical-but-unsourced ⇒ V-2 FAIL.
    snapshot = _piece(
        body="GT School appears prominently in AI search answers about gifted education.",
        claims_text=["GT appears in 40% of AI answers about gifted schools."],
    )
    result = evaluate_message(
        snapshot,
        settings=settings_no_key,
        params=params,
        brand_judge=on_brand_judge,
        audience=GEO_AUDIENCE,
    )
    assert result.passed is False
    assert result.v2_grounding == "fail"
    assert "v2_grounding" in result.failed_rules
