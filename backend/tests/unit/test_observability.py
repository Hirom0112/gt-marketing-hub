"""Observability audit-spine tests (NFR-6; ARCHITECTURE.md §10, §4.9; CLAUDE.md §4.1).

The §4.9 spine — ``proposals → evals → decisions`` — is the audit trail that lets a
reviewer reconstruct "what did the AI propose, did it pass its eval, what did the
human do." NFR-6 demands every AI proposal, its eval result, and the human decision
be logged and queryable. ARCH §10 adds the causality rule: a proposal is persisted
BEFORE it can reach a human — so you cannot eval or decide a proposal that was never
logged.

This is the in-memory v1 store (ASSUMPTIONS A-3; production swaps to Supabase behind
the same interface, mirroring ``app/data/repository.py``). These tests pin:

- the full proposal → eval → decision audit chain (NFR-6),
- causality: eval/decision for an unknown proposal_id raises (ARCH §10),
- append-only: no public mutate/delete; a re-eval APPENDS (edit→re-eval history),
- blocked proposals (failing eval) are STILL logged (ARCH §10 audit).

Time is injected so assertions are deterministic (mirrors ``app/core/seam.py``): no
``datetime.now()`` in any pinned path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.observability.log_store import (
    DecisionAction,
    DecisionRecord,
    DismissRecord,
    EvalRecord,
    InMemoryObservabilityLog,
    ProposalRecord,
)

# Fixed instants so created_at assertions are exact and reproducible.
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
_T2 = datetime(2026, 1, 1, 12, 10, 0, tzinfo=UTC)


def test_proposal_eval_decision_logged() -> None:
    """The NFR-6 audit chain: proposal → eval(passed) → decision(approve).

    ``get_audit`` reconstructs all three, and causality holds — the proposal was
    logged before the decision (logging for an unknown proposal_id raises).
    """
    log = InMemoryObservabilityLog()
    proposal_id = uuid4()
    family_id = uuid4()

    proposal = log.log_proposal(
        proposal_id=proposal_id,
        family_id=family_id,
        flow="enrollment_nudge",
        schema_version="v1",
        payload={"draft": "Hi there"},
        created_at=_T0,
    )
    assert isinstance(proposal, ProposalRecord)
    assert proposal.proposal_id == proposal_id
    assert proposal.family_id == family_id
    assert proposal.flow == "enrollment_nudge"
    assert proposal.schema_version == "v1"
    assert proposal.payload == {"draft": "Hi there"}
    assert proposal.created_at == _T0

    eval_record = log.log_eval(
        proposal_id=proposal_id,
        eval_name="grounding",
        score=0.95,
        threshold=0.9,
        passed=True,
        created_at=_T1,
    )
    assert isinstance(eval_record, EvalRecord)
    assert eval_record.proposal_id == proposal_id
    assert eval_record.passed is True
    assert eval_record.score == 0.95
    assert eval_record.threshold == 0.9

    decision = log.log_decision(
        proposal_id=proposal_id,
        human="reviewer@example.invalid",
        action=DecisionAction.APPROVE,
        edited_payload=None,
        created_at=_T2,
    )
    assert isinstance(decision, DecisionRecord)
    assert decision.proposal_id == proposal_id
    assert decision.action is DecisionAction.APPROVE
    assert decision.edited_payload is None

    # The joined view reconstructs the whole chain.
    audit = log.get_audit(proposal_id)
    assert audit is not None
    assert audit.proposal.proposal_id == proposal_id
    assert [e.eval_name for e in audit.evals] == ["grounding"]
    assert audit.evals[0].passed is True
    assert [d.action for d in audit.decisions] == [DecisionAction.APPROVE]

    # Causality / ordering: the proposal exists before the decision references it.
    assert audit.proposal.created_at < audit.decisions[0].created_at


def test_eval_for_unknown_proposal_raises() -> None:
    """You cannot eval or decide a proposal that was never proposed (ARCH §10)."""
    log = InMemoryObservabilityLog()
    unknown = uuid4()

    with pytest.raises(KeyError):
        log.log_eval(
            proposal_id=unknown,
            eval_name="grounding",
            score=0.5,
            threshold=0.9,
            passed=False,
        )

    with pytest.raises(KeyError):
        log.log_decision(
            proposal_id=unknown,
            human="reviewer@example.invalid",
            action=DecisionAction.DISCARD,
            edited_payload=None,
        )

    # And get_audit on an unknown id is a clean miss, not a crash.
    assert log.get_audit(unknown) is None


def test_append_only_no_mutation() -> None:
    """No public mutate/delete; a second eval/decision APPENDS to the history.

    A proposal can carry an edit → re-eval history: a failing eval, an editing
    decision, then a passing re-eval. ``get_audit`` returns the full sequence in
    append order.
    """
    log = InMemoryObservabilityLog()
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        family_id=uuid4(),
        flow="enrollment_nudge",
        schema_version="v1",
        payload={"draft": "first"},
        created_at=_T0,
    )

    log.log_eval(
        proposal_id=proposal_id,
        eval_name="grounding",
        score=0.40,
        threshold=0.9,
        passed=False,
        created_at=_T0,
    )
    log.log_decision(
        proposal_id=proposal_id,
        human="reviewer@example.invalid",
        action=DecisionAction.EDIT,
        edited_payload={"draft": "second"},
        created_at=_T1,
    )
    log.log_eval(
        proposal_id=proposal_id,
        eval_name="grounding",
        score=0.95,
        threshold=0.9,
        passed=True,
        created_at=_T2,
    )

    audit = log.get_audit(proposal_id)
    assert audit is not None
    # Both evals are retained, in append order (edit → re-eval history).
    assert [e.passed for e in audit.evals] == [False, True]
    assert [e.score for e in audit.evals] == [0.40, 0.95]
    assert [d.action for d in audit.decisions] == [DecisionAction.EDIT]
    assert audit.decisions[0].edited_payload == {"draft": "second"}

    # There is no public API to mutate or delete a logged record (append-only spine).
    for forbidden in ("update_proposal", "delete_proposal", "update_eval", "delete_eval"):
        assert not hasattr(log, forbidden)


def test_blocked_proposal_is_still_logged() -> None:
    """A proposal whose eval failed (blocked) is STILL in the log (ARCH §10).

    Blocked outbound actions are logged with their failing eval — that record is
    the "zero unverifiable claims escape" audit proof. The proposal appears in
    ``list_proposals`` and its failing eval is reconstructable via ``get_audit``.
    """
    log = InMemoryObservabilityLog()
    proposal_id = uuid4()
    log.log_proposal(
        proposal_id=proposal_id,
        family_id=uuid4(),
        flow="content_draft",
        schema_version="v1",
        payload={"draft": "4X better results"},
        created_at=_T0,
    )
    log.log_eval(
        proposal_id=proposal_id,
        eval_name="grounding",
        score=0.10,
        threshold=0.9,
        passed=False,
        created_at=_T1,
    )

    # The blocked proposal is still in the log.
    assert proposal_id in {p.proposal_id for p in log.list_proposals()}

    audit = log.get_audit(proposal_id)
    assert audit is not None
    assert audit.evals[0].passed is False
    # Blocked ⇒ no decision yet, but the proposal + failing eval are auditable.
    assert audit.decisions == []


# ---------------------------------------------------------------------------
# S12 W1 — dismiss event (the one new write on the spine; A-19).
# ---------------------------------------------------------------------------


def test_dismiss_event_recorded_and_queryable() -> None:
    """A dismiss event carries a required reason and is queryable as the latest state.

    Dismiss is the ONLY manual recovery removal (A-19): it appends a
    :class:`DismissRecord` (reason required) to the append-only spine and
    ``is_dismissed`` returns True for that family thereafter.
    """
    log = InMemoryObservabilityLog()
    family_id = uuid4()

    # No dismiss yet ⇒ not dismissed.
    assert log.is_dismissed(family_id) is False

    record = log.log_dismiss(
        family_id=family_id,
        human="operator@example.invalid",
        reason="enrolled elsewhere",
        created_at=_T0,
    )
    assert isinstance(record, DismissRecord)
    assert record.family_id == family_id
    assert record.reason == "enrolled elsewhere"
    assert record.created_at == _T0

    # Now dismissed, and the event is listed.
    assert log.is_dismissed(family_id) is True
    assert family_id in {d.family_id for d in log.list_dismissals()}


def test_dismiss_requires_a_reason() -> None:
    """A dismiss with an empty reason is rejected — the audit needs a why (A-19)."""
    log = InMemoryObservabilityLog()
    with pytest.raises(ValueError):
        log.log_dismiss(family_id=uuid4(), human="op@example.invalid", reason="   ")


def test_latest_dismiss_is_superseded_by_a_later_restall() -> None:
    """A re-stall AFTER a dismiss supersedes it (the family is active again; A-19).

    ``is_dismissed`` takes an optional ``restalled_after`` instant: if the family
    re-stalled after the latest dismiss, the dismiss no longer holds.
    """
    log = InMemoryObservabilityLog()
    family_id = uuid4()
    log.log_dismiss(
        family_id=family_id, human="op@example.invalid", reason="went cold", created_at=_T0
    )
    # A re-stall after the dismiss supersedes it.
    assert log.is_dismissed(family_id, restalled_after=_T2) is False
    # A re-stall BEFORE the dismiss does not.
    assert log.is_dismissed(family_id, restalled_after=_T0) is True
