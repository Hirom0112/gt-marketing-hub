"""Keep promotes to library + brand memory; non-passing keep refused (FR-3.4/3.5).

The §5.3 keep/discard loop (the marketing analog of the §5.2 approve path):

- keep on a PASSING candidate creates a `kept` + validated `LibraryAsset` AND
  affirms a `BrandMemoryItem` (so the NEXT conditioning includes it — the FR-3.2
  loop) AND logs an approve decision.
- keep on a NON-PASSING candidate is REFUSED (you cannot keep an un-passed
  candidate — INV-3 / P-2): no library asset, no affirm.
- discard does NEITHER (no library asset, no affirm); it strengthens a
  discard/dont signal and logs a discard decision.

Drives `app/marketing/keep_discard.py` against a real persistent brand-memory
store, an in-memory content library, and the observability log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
from app.ai.conditioning import assemble_conditioning
from app.ai.schemas.brand import BrandMemoryKind
from app.ai.schemas.content import (
    AudienceTag,
    Channel,
    ContentCandidate,
    ContentFormat,
    GeneratedBy,
    HumanDecision,
    LifecycleStage,
    Provenance,
)
from app.core.eval_gate import RuleVerdict, ValidationResult
from app.core.params import load_params
from app.marketing.keep_discard import KeepRefused, discard, keep
from app.marketing.library import InMemoryContentLibrary
from app.observability.log_store import DecisionAction, InMemoryObservabilityLog

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _candidate(copy_text: str, *, suffix: str = "keep") -> ContentCandidate:
    return ContentCandidate(
        id=f"cc-{suffix}",
        batch_id="batch-001",
        prompt="Draft on-brand copy.",
        channel=Channel.INSTAGRAM,
        format=ContentFormat.SHORT_CAPTION,
        concept="A mastery-based caption.",
        copy=copy_text,
        claims=[],
        audience_tag=AudienceTag.PROSPECTIVE_PARENT,
        lifecycle=LifecycleStage.CANDIDATE,
        decision=HumanDecision(),
        provenance=Provenance(generated_by=GeneratedBy.LLM, created_at="2026-01-01T00:00:00+00:00"),
    )


def _passing() -> ValidationResult:
    return ValidationResult(
        v1_schema=RuleVerdict.PASS,
        v2_grounding=RuleVerdict.PASS,
        v3_coppa=RuleVerdict.PASS,
        v4_onbrand=RuleVerdict.PASS,
        passed=True,
    )


def _failing() -> ValidationResult:
    return ValidationResult(
        v1_schema=RuleVerdict.PASS,
        v2_grounding=RuleVerdict.FAIL,
        v3_coppa=RuleVerdict.PASS,
        v4_onbrand=RuleVerdict.PASS,
        passed=False,
        failed_rules=["v2_grounding"],
    )


def _store(tmp_path: Path) -> SqliteBrandMemoryStore:
    params = load_params(EXAMPLE_PARAMS)
    return SqliteBrandMemoryStore(
        tmp_path / "brand.sqlite3", weight_step=params.brand_memory.weight_step
    )


def _log_proposal(log: InMemoryObservabilityLog, candidate: ContentCandidate):
    from uuid import uuid4

    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        flow="content_generate",
        schema_version="1",
        payload=candidate.model_dump(mode="json"),
        content_ref=None,
    )
    log.log_eval(proposal_id=proposal_id, eval_name="message_safety_grounding", passed=True)
    return proposal_id


def test_keep_passing_promotes_library_and_memory(tmp_path: Path) -> None:
    """Keep on a PASSING candidate: a kept LibraryAsset + affirmed BrandMemory + approve."""
    params = load_params(EXAMPLE_PARAMS)
    store = _store(tmp_path)
    library = InMemoryContentLibrary()
    log = InMemoryObservabilityLog()

    candidate = _candidate(
        "Mastery-based gifted K-8. See how a GT School day fits your child's pace."
    )
    proposal_id = _log_proposal(log, candidate)

    asset = keep(
        proposal_id,
        candidate=candidate,
        validation=_passing(),
        store=store,
        library=library,
        log=log,
        params=params,
    )

    # A kept + validated LibraryAsset was promoted.
    assert asset.lifecycle is LifecycleStage.KEPT
    assert asset.validation  # non-empty validation id
    assert library.search() == [asset]

    # An exemplar now conditions the NEXT batch (the FR-3.2 loop).
    block = assemble_conditioning(store, Channel.INSTAGRAM)
    assert any(candidate.copy_text in line for line in block.text.splitlines())
    exemplars = [i for i in store.list_active() if i.kind is BrandMemoryKind.EXEMPLAR]
    assert any(e.content == candidate.copy_text for e in exemplars)

    # An approve decision was logged.
    audit = log.get_audit(proposal_id)
    assert audit is not None
    assert any(d.action is DecisionAction.APPROVE for d in audit.decisions)


def test_keep_non_passing_is_refused(tmp_path: Path) -> None:
    """Keep on a NON-PASSING candidate is REFUSED — no library asset, no affirm (INV-3)."""
    params = load_params(EXAMPLE_PARAMS)
    store = _store(tmp_path)
    library = InMemoryContentLibrary()
    log = InMemoryObservabilityLog()

    candidate = _candidate("Kids learn at 4X speed!", suffix="bad")
    proposal_id = _log_proposal(log, candidate)

    with pytest.raises(KeepRefused):
        keep(
            proposal_id,
            candidate=candidate,
            validation=_failing(),
            store=store,
            library=library,
            log=log,
            params=params,
        )

    # No library asset, no exemplar affirmed.
    assert library.search() == []
    exemplars = [i for i in store.list_active() if i.kind is BrandMemoryKind.EXEMPLAR]
    assert all(e.content != candidate.copy_text for e in exemplars)


def test_discard_neither_library_nor_affirm(tmp_path: Path) -> None:
    """Discard creates NO library asset and NO exemplar; it logs a discard decision."""
    params = load_params(EXAMPLE_PARAMS)
    store = _store(tmp_path)
    library = InMemoryContentLibrary()
    log = InMemoryObservabilityLog()

    candidate = _candidate("An off-brand caption we reject.", suffix="disc")
    proposal_id = _log_proposal(log, candidate)

    discard(proposal_id, candidate=candidate, store=store, log=log, params=params)

    # No library asset; no exemplar carrying the discarded copy.
    assert library.search() == []
    exemplars = [i for i in store.list_active() if i.kind is BrandMemoryKind.EXEMPLAR]
    assert all(e.content != candidate.copy_text for e in exemplars)

    # A discard decision was logged.
    audit = log.get_audit(proposal_id)
    assert audit is not None
    assert any(d.action is DecisionAction.DISCARD for d in audit.decisions)
