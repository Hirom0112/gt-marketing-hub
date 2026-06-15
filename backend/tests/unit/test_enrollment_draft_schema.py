"""EnrollmentDraftProposal schema tests (S2; INV-2, FR-2.4, CONTENT_SPEC §9.2).

INV-2 (CLAUDE.md §1): an LLM result is a schema-validated **proposal**, never
coerced into state. These tests pin the safeguard at the schema boundary — a
malformed payload must RAISE `pydantic.ValidationError`, not be silently coerced
into a "valid" proposal. The model forbids extras, freezes its fields, and types
each field strictly enough that wrong-typed input fails closed.

CONTENT_SPEC §9.2 (V-2 grounding): every empirical `Claim` carries its
`source_ref` so the grounding gate (a separate agent) CAN check it; here we only
model the data so it is checkable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.ai.schemas.enrollment_draft import Claim, DraftAction, EnrollmentDraftProposal
from pydantic import ValidationError


def _valid_kwargs() -> dict[str, object]:
    """A minimal, valid set of inputs for an EnrollmentDraftProposal."""
    return {
        "action": "email",
        "family_id": uuid4(),
        "body": "Hi Rivera family — your application is one form short of complete.",
        "claims": [
            {"text": "GT School is tuition-free for TEFA-eligible families.", "source_ref": "kb:tefa"},
        ],
    }


def test_accepts_wellformed() -> None:
    """A well-formed payload builds a frozen proposal with typed fields (FR-2.4)."""
    proposal = EnrollmentDraftProposal(**_valid_kwargs())  # type: ignore[arg-type]
    assert proposal.action is DraftAction.EMAIL
    assert proposal.body.startswith("Hi Rivera family")
    assert len(proposal.claims) == 1
    assert proposal.claims[0].text.startswith("GT School")
    assert proposal.claims[0].source_ref == "kb:tefa"

    # `claims` defaults to an empty list (a draft with no empirical claims).
    no_claims = _valid_kwargs()
    del no_claims["claims"]
    bare = EnrollmentDraftProposal(**no_claims)  # type: ignore[arg-type]
    assert bare.claims == []

    # An unsourced claim is representable (source_ref None) — V-2's job to fail it.
    claim = Claim(text="We are great.")
    assert claim.source_ref is None


def test_rejects_malformed() -> None:
    """Malformed input RAISES ValidationError — never coerced (INV-2)."""
    # (a) unknown action enum value is rejected.
    bad_action = _valid_kwargs()
    bad_action["action"] = "phone_call"
    with pytest.raises(ValidationError):
        EnrollmentDraftProposal(**bad_action)  # type: ignore[arg-type]

    # (b) missing required field — family_id.
    no_family = _valid_kwargs()
    del no_family["family_id"]
    with pytest.raises(ValidationError):
        EnrollmentDraftProposal(**no_family)  # type: ignore[arg-type]

    # (b) missing required field — body.
    no_body = _valid_kwargs()
    del no_body["body"]
    with pytest.raises(ValidationError):
        EnrollmentDraftProposal(**no_body)  # type: ignore[arg-type]

    # (c) extra/unexpected top-level field is forbidden (extra="forbid").
    extra = _valid_kwargs()
    extra["llm_confidence"] = 0.97
    with pytest.raises(ValidationError):
        EnrollmentDraftProposal(**extra)  # type: ignore[arg-type]

    # (d) wrong type not coerced — family_id is not a UUID.
    bad_uuid = _valid_kwargs()
    bad_uuid["family_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        EnrollmentDraftProposal(**bad_uuid)  # type: ignore[arg-type]

    # (d) wrong type not coerced — claims is not a list.
    bad_claims = _valid_kwargs()
    bad_claims["claims"] = "GT is tuition-free"
    with pytest.raises(ValidationError):
        EnrollmentDraftProposal(**bad_claims)  # type: ignore[arg-type]


def test_rejects_empty_body() -> None:
    """An empty body is rejected (min_length 1) — an empty draft is not a draft."""
    empty = _valid_kwargs()
    empty["body"] = ""
    with pytest.raises(ValidationError):
        EnrollmentDraftProposal(**empty)  # type: ignore[arg-type]


def test_proposal_is_frozen() -> None:
    """The proposal is immutable once built — a proposal is not mutated into state."""
    proposal = EnrollmentDraftProposal(**_valid_kwargs())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        proposal.body = "rewritten"  # type: ignore[misc]

    claim = proposal.claims[0]
    with pytest.raises(ValidationError):
        claim.text = "rewritten"  # type: ignore[misc]


def test_claim_forbids_extra_field() -> None:
    """A nested Claim forbids extras too — no smuggled fields past the gate."""
    with pytest.raises(ValidationError):
        Claim(text="GT is tuition-free.", verified=True)  # type: ignore[call-arg]
