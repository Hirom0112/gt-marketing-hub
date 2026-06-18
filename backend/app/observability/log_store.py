"""Observability audit spine — the append-only proposal/eval/decision log (NFR-6).

ARCHITECTURE.md §10 & §4.9; CLAUDE.md INV-2/INV-3, §6.7. "Every AI proposal, its
eval result, and the human decision are logged and queryable" (NFR-6). This module
is the in-memory write-through store that makes that true: a reviewer can reconstruct
*what did the AI propose, did it pass its eval, and what did the human do* for any
proposal id.

Causality (ARCH §10): a proposal is persisted BEFORE it can reach a human, so an eval
or a decision for a never-logged proposal is a programming error — it raises. Nothing
here is ever mutated or deleted: the store is the audit spine (THREAT_MODEL /
observability), so it exposes only *append* and *query* — there is deliberately no
update/delete API. Blocked proposals (a failing eval) stay logged with their failing
eval: that record is the "zero unverifiable claims escape" proof (INV-4 audit side).

This is a clean **sink** (CLAUDE.md §7 boundaries): it stores RESULTS handed to it,
it never *runs* evals or calls an LLM. It imports no ``anthropic``/``langgraph`` and
nothing from ``app.ai`` / ``app.core.eval_gate``. Its only enum is local — it does
**not** modify ``app/data/models.py`` (which has no ``DecisionAction``; the SQL enum
``decision_action`` lives in ``0001_init.sql`` and we mirror its tokens here).

v1 is in-memory (ASSUMPTIONS A-3). Production swaps a Supabase-backed impl behind the
same :class:`ObservabilityLog` interface — the identical seam pattern as
``app/data/repository.py`` — with zero changes to callers. Time is injectable so tests
are deterministic (mirrors ``app/core/seam.py``): callers may pin ``created_at``; the
default is a real timestamp, never asserted on in a pinned test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# §4.8/§4.9 enum — mirrors the SQL `decision_action` ENUM in 0001_init.sql.
# `app/data/models.py` has no DecisionAction, so we define it here (CLAUDE.md
# §7: this package may own its own enum; it does NOT edit data/models.py).
# StrEnum so it serializes to the exact Postgres tokens, matching the §4.8 style.
# ---------------------------------------------------------------------------


class DecisionAction(StrEnum):
    """`decisions.action` — the human's verdict on a proposal (§4.9).

    Mirrors the SQL ``decision_action`` enum (``0001_init.sql``): the reviewer
    either approves the proposal as-is, edits it (carrying ``edited_payload``),
    or discards it. INV-2: even ``approve`` is a decision recorded here, not a
    state write — the deterministic core owns writes.
    """

    APPROVE = "approve"
    EDIT = "edit"
    DISCARD = "discard"


# ---------------------------------------------------------------------------
# §4.9 records — application-side shapes mirroring the migration columns.
# Frozen (immutable) so an appended record can never be silently mutated: the
# only way state changes is a new append (the append-only audit invariant).
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Default wall-clock timestamp (UTC). Never used in a path a test pins."""
    return datetime.now(UTC)


class ProposalRecord(BaseModel):
    """`proposals` row — an AI proposal, persisted before it reaches a human (§4.9).

    Mirrors the migration columns exactly. INV-2: this is a *proposal*, never a
    state write; the deterministic core applies nothing from here without a
    recorded :class:`DecisionRecord`.
    """

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    family_id: UUID | None = None
    # The child this proposal targets, when the flow is per-student (A-24). None
    # for family-level proposals; keeps per-child contact distinct from a sibling's.
    student_id: UUID | None = None
    content_ref: UUID | None = None
    flow: str
    schema_version: str
    payload: dict[str, object] = Field(default_factory=dict)
    # Anthropic USD this run charged to its per-run budget (the cross-run DAILY
    # cap accumulator reads this — NFR-5; INV-8). Additive + default-0.0 so every
    # existing caller that omits it still validates (back-compat): a degraded /
    # non-live / pre-stamp proposal simply carries 0.0. `core/daily_spend.py` sums
    # this over a day; it is NEVER inferred from a per-token rate (INV-11) — the
    # caller stamps the completed budget's `usd_spent`.
    usd_spent: float = 0.0
    created_at: datetime


class EvalRecord(BaseModel):
    """`evals` row — one eval result attached to a proposal (§4.9).

    ``passed`` is the gate verdict (INV-3); ``score``/``threshold`` are the
    numbers behind it. A failing eval is still logged — that is the blocked-action
    audit (INV-4).
    """

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    eval_name: str
    score: float | None = None
    threshold: float | None = None
    passed: bool
    created_at: datetime


class DecisionRecord(BaseModel):
    """`decisions` row — the human verdict on a proposal (§4.9).

    ``edited_payload`` carries the human's edits when ``action`` is
    :attr:`DecisionAction.EDIT`; it is ``None`` for approve/discard.
    """

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    human: str
    action: DecisionAction
    edited_payload: dict[str, object] | None = None
    created_at: datetime


class DismissRecord(BaseModel):
    """A recovery-dismiss event — the ONE new write the S12 state machine adds (A-19).

    Dismiss is the only MANUAL removal of a family from the active recovery board
    (recovered is DETECTED, never a button). It is family-keyed (not
    proposal-keyed) and carries a REQUIRED ``reason`` so the audit always records
    *why* a family was set aside (INV-2: still a logged event on the spine, never a
    silent state mutation). Reversible: a later re-stall (a new ``stall_date``)
    supersedes it (see :meth:`ObservabilityLog.is_dismissed`).
    """

    model_config = ConfigDict(frozen=True)

    family_id: UUID
    # The child set aside, when the dismiss is per-student (A-24). None for a
    # family-level dismiss; a per-child dismiss never leaks to a sibling or the
    # family-level query (see :meth:`ObservabilityLog.is_dismissed`).
    student_id: UUID | None = None
    human: str
    reason: str = Field(min_length=1)
    created_at: datetime


class ContactChannel(StrEnum):
    """How a rep reached (or tried to reach) a family. SMS-first per the funnel data."""

    SMS = "sms"
    EMAIL = "email"
    CALL = "call"


class ContactDisposition(StrEnum):
    """The outcome of a contact attempt — the structured 'log a call outcome' taxonomy.

    ``NO_ANSWER``/``NO_REPLY``/``VOICEMAIL`` are the no-response dispositions that
    accrue toward the presumed-lost rule (the family went silent). The rest record a
    live result (``REACHED``, a payment commitment, or an explicit decline).
    """

    REACHED = "reached"
    NO_ANSWER = "no_answer"
    NO_REPLY = "no_reply"
    VOICEMAIL = "voicemail"
    WRONG_NUMBER = "wrong_number"
    COMMITTED_TO_PAY = "committed_to_pay"
    DECLINED = "declined"


# The dispositions that count as "no live response" — the silence the presumed-lost
# rule accumulates (a left voicemail is still no answer back). Named here so the
# policy (core/nurture.py) reads ONE canonical set, never a scattered literal.
NO_RESPONSE_DISPOSITIONS: frozenset[ContactDisposition] = frozenset(
    {
        ContactDisposition.NO_ANSWER,
        ContactDisposition.NO_REPLY,
        ContactDisposition.VOICEMAIL,
    }
)


class ContactOutcomeRecord(BaseModel):
    """A rep's logged contact attempt — an append-only spine event (NFR-6, INV-2).

    Mirrors :class:`DismissRecord`: family-keyed (optionally per-child), carries who
    acted and when, and is never mutated — the record IS the audit. ``promised_by``
    captures a commitment ("paying next week") so a follow-up can be scheduled and a
    premature nudge suppressed. A logged event, never a silent state write — the
    deterministic core derives lifecycle/recency FROM these (INV-2).
    """

    model_config = ConfigDict(frozen=True)

    family_id: UUID
    student_id: UUID | None = None
    channel: ContactChannel
    disposition: ContactDisposition
    human: str
    # A future-dated commitment captured from the call ("paying next week"); drives
    # the follow-up surface and suppresses a premature nudge. None = no promise.
    promised_by: date | None = None
    note: str = ""
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditView:
    """The joined audit chain for one proposal (NFR-6 reconstruction).

    A proposal plus every eval and every decision attached to it, in append
    order — the "what did the AI propose, did it pass, what did the human do"
    view a reviewer reads. Mirrors a §4.9 join keyed on ``proposal_id``.
    """

    proposal: ProposalRecord
    evals: list[EvalRecord] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The interface + the in-memory impl (the NFR-6 store seam; A-3).
# ---------------------------------------------------------------------------


class ObservabilityLog(ABC):
    """Append-only write-through audit log over the §4.9 spine (NFR-6).

    Every read/write endpoint depends on this interface, never a concrete store;
    production swaps a Supabase-backed impl with zero caller changes. The surface
    is deliberately append + query only — there is no update/delete (the log is
    the audit spine; mutating it would defeat its purpose).
    """

    @abstractmethod
    def log_proposal(
        self,
        *,
        proposal_id: UUID,
        flow: str,
        schema_version: str,
        payload: dict[str, object],
        family_id: UUID | None = None,
        student_id: UUID | None = None,
        content_ref: UUID | None = None,
        usd_spent: float = 0.0,
        created_at: datetime | None = None,
    ) -> ProposalRecord:
        """Persist an AI proposal BEFORE it reaches a human (ARCH §10). Append-only.

        ``student_id`` keys the proposal to one child for per-student flows (A-24);
        omit it for family-level proposals. ``usd_spent`` is the run's Anthropic USD
        for the cross-run DAILY cap accumulator (NFR-5); default 0.0 so callers that
        omit it (degraded / non-live runs) are unchanged.
        """
        raise NotImplementedError

    @abstractmethod
    def log_eval(
        self,
        *,
        proposal_id: UUID,
        eval_name: str,
        passed: bool,
        score: float | None = None,
        threshold: float | None = None,
        created_at: datetime | None = None,
    ) -> EvalRecord:
        """Attach an eval result to an already-logged proposal.

        Raises ``KeyError`` if ``proposal_id`` was never logged — you cannot eval
        a proposal that was never proposed (ARCH §10 causality).
        """
        raise NotImplementedError

    @abstractmethod
    def log_decision(
        self,
        *,
        proposal_id: UUID,
        human: str,
        action: DecisionAction,
        edited_payload: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> DecisionRecord:
        """Record the human verdict on an already-logged proposal.

        Raises ``KeyError`` if ``proposal_id`` was never logged (ARCH §10).
        """
        raise NotImplementedError

    @abstractmethod
    def get_audit(self, proposal_id: UUID) -> AuditView | None:
        """Reconstruct the joined proposal + its evals + its decisions (NFR-6).

        Returns ``None`` for an unknown proposal_id (a clean miss, not a raise) —
        querying an absent id is a legitimate read, unlike eval/decide on one.
        """
        raise NotImplementedError

    @abstractmethod
    def list_proposals(self) -> list[ProposalRecord]:
        """Every logged proposal, in append order (the audit index)."""
        raise NotImplementedError

    @abstractmethod
    def log_dismiss(
        self,
        *,
        family_id: UUID,
        student_id: UUID | None = None,
        human: str,
        reason: str,
        created_at: datetime | None = None,
    ) -> DismissRecord:
        """Append a recovery-dismiss event (A-19/A-24). Append-only.

        ``student_id`` sets aside ONE child (A-24); omit it for a family-level
        dismiss. Raises ``ValueError`` if ``reason`` is blank — a dismiss must say why.
        """
        raise NotImplementedError

    @abstractmethod
    def list_dismissals(self) -> list[DismissRecord]:
        """Every logged dismiss event, in append order (the dismiss audit index)."""
        raise NotImplementedError

    @abstractmethod
    def is_dismissed(
        self,
        family_id: UUID,
        *,
        student_id: UUID | None = None,
        restalled_after: datetime | None = None,
    ) -> bool:
        """Whether the latest matching dismiss still holds (A-19/A-24).

        Matches dismiss events on BOTH ``family_id`` and ``student_id`` — a
        family-level query (``student_id=None``) matches only family-level
        dismisses, and a per-child query matches only that child's, so a per-child
        dismiss never leaks to a sibling or the family (A-24). True when such a
        dismiss exists AND no later re-stall supersedes it. ``restalled_after`` is
        the current ``stall_date``
        (the API layer's derived re-stall instant): if it is strictly later than
        the latest dismiss, the family has re-stalled and is active again ⇒ False.
        """
        raise NotImplementedError

    @abstractmethod
    def log_contact_outcome(
        self,
        *,
        family_id: UUID,
        channel: ContactChannel,
        disposition: ContactDisposition,
        human: str,
        student_id: UUID | None = None,
        promised_by: date | None = None,
        note: str = "",
        created_at: datetime | None = None,
    ) -> ContactOutcomeRecord:
        """Append a rep's contact-attempt outcome (the 'log a call outcome' event).

        Append-only (INV-2): a logged event the core derives recency/lifecycle from,
        never a direct state write. ``promised_by`` captures a commitment date.
        """
        raise NotImplementedError

    @abstractmethod
    def list_contact_outcomes(self, family_id: UUID) -> list[ContactOutcomeRecord]:
        """This family's contact outcomes, in append order (owner-scoped at the API)."""
        raise NotImplementedError


class InMemoryObservabilityLog(ObservabilityLog):
    """In-memory append-only audit log (v1; ASSUMPTIONS A-3).

    Storage is plain append lists/dicts: a proposal index plus per-proposal eval
    and decision lists. There is no mutate/delete path — appending is the only
    way state changes. Production replaces this with a Supabase-backed
    :class:`ObservabilityLog` behind the same interface.
    """

    def __init__(self) -> None:
        # Insertion-ordered proposal index (dict preserves order) → audit index.
        self._proposals: dict[UUID, ProposalRecord] = {}
        # Append-only per-proposal histories (edit → re-eval is just more appends).
        self._evals: dict[UUID, list[EvalRecord]] = defaultdict(list)
        self._decisions: dict[UUID, list[DecisionRecord]] = defaultdict(list)
        # Append-only family-keyed dismiss events (A-19) — the one new write.
        self._dismissals: list[DismissRecord] = []
        # Append-only family-keyed contact-attempt outcomes (the close-loop event).
        self._contact_outcomes: list[ContactOutcomeRecord] = []

    def log_proposal(
        self,
        *,
        proposal_id: UUID,
        flow: str,
        schema_version: str,
        payload: dict[str, object],
        family_id: UUID | None = None,
        student_id: UUID | None = None,
        content_ref: UUID | None = None,
        usd_spent: float = 0.0,
        created_at: datetime | None = None,
    ) -> ProposalRecord:
        if proposal_id in self._proposals:
            # Append-only spine: a proposal_id is logged exactly once. Re-logging
            # would be a silent mutation of the audit head — disallow it.
            raise ValueError(f"proposal already logged: {proposal_id}")
        record = ProposalRecord(
            proposal_id=proposal_id,
            family_id=family_id,
            student_id=student_id,
            content_ref=content_ref,
            flow=flow,
            schema_version=schema_version,
            payload=payload,
            usd_spent=usd_spent,
            created_at=created_at if created_at is not None else _now(),
        )
        self._proposals[proposal_id] = record
        return record

    def log_eval(
        self,
        *,
        proposal_id: UUID,
        eval_name: str,
        passed: bool,
        score: float | None = None,
        threshold: float | None = None,
        created_at: datetime | None = None,
    ) -> EvalRecord:
        self._require_proposal(proposal_id)
        record = EvalRecord(
            proposal_id=proposal_id,
            eval_name=eval_name,
            score=score,
            threshold=threshold,
            passed=passed,
            created_at=created_at if created_at is not None else _now(),
        )
        self._evals[proposal_id].append(record)
        return record

    def log_decision(
        self,
        *,
        proposal_id: UUID,
        human: str,
        action: DecisionAction,
        edited_payload: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> DecisionRecord:
        self._require_proposal(proposal_id)
        record = DecisionRecord(
            proposal_id=proposal_id,
            human=human,
            action=action,
            edited_payload=edited_payload,
            created_at=created_at if created_at is not None else _now(),
        )
        self._decisions[proposal_id].append(record)
        return record

    def get_audit(self, proposal_id: UUID) -> AuditView | None:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return None
        return AuditView(
            proposal=proposal,
            # New lists each call so a reader can never mutate stored history.
            evals=list(self._evals.get(proposal_id, ())),
            decisions=list(self._decisions.get(proposal_id, ())),
        )

    def list_proposals(self) -> list[ProposalRecord]:
        return list(self._proposals.values())

    def log_dismiss(
        self,
        *,
        family_id: UUID,
        student_id: UUID | None = None,
        human: str,
        reason: str,
        created_at: datetime | None = None,
    ) -> DismissRecord:
        if not reason.strip():
            # A dismiss must record WHY (A-19); a blank reason is a programming
            # error, not a silent no-reason removal.
            raise ValueError("dismiss requires a non-blank reason (A-19)")
        record = DismissRecord(
            family_id=family_id,
            student_id=student_id,
            human=human,
            reason=reason,
            created_at=created_at if created_at is not None else _now(),
        )
        self._dismissals.append(record)
        return record

    def list_dismissals(self) -> list[DismissRecord]:
        return list(self._dismissals)

    def is_dismissed(
        self,
        family_id: UUID,
        *,
        student_id: UUID | None = None,
        restalled_after: datetime | None = None,
    ) -> bool:
        latest: datetime | None = None
        for record in self._dismissals:
            # Match on BOTH keys so a per-child dismiss never leaks to a sibling
            # or the family-level query, and vice-versa (A-24).
            if record.family_id != family_id or record.student_id != student_id:
                continue
            if latest is None or record.created_at > latest:
                latest = record.created_at
        if latest is None:
            return False
        # A re-stall strictly after the latest dismiss supersedes it (A-19).
        if restalled_after is not None and restalled_after > latest:
            return False
        return True

    def log_contact_outcome(
        self,
        *,
        family_id: UUID,
        channel: ContactChannel,
        disposition: ContactDisposition,
        human: str,
        student_id: UUID | None = None,
        promised_by: date | None = None,
        note: str = "",
        created_at: datetime | None = None,
    ) -> ContactOutcomeRecord:
        record = ContactOutcomeRecord(
            family_id=family_id,
            student_id=student_id,
            channel=channel,
            disposition=disposition,
            human=human,
            promised_by=promised_by,
            note=note,
            created_at=created_at if created_at is not None else _now(),
        )
        self._contact_outcomes.append(record)
        return record

    def list_contact_outcomes(self, family_id: UUID) -> list[ContactOutcomeRecord]:
        return [r for r in self._contact_outcomes if r.family_id == family_id]

    def _require_proposal(self, proposal_id: UUID) -> None:
        """Enforce ARCH §10 causality: no eval/decision before the proposal."""
        if proposal_id not in self._proposals:
            raise KeyError(f"unknown proposal_id (never logged): {proposal_id}")
