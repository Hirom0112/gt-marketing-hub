"""Identity merge-queue surface — the dedup human-review read (ENROLLMENT_REFACTOR §5.2/§6).

This router is the thin HTTP composition over the pure dedup core
(:mod:`app.core.identity`). Duplicate-lead chaos is structural (a fresh
``family_id`` per application, plus HubSpot-native records with no
``gt_synthetic_id``), so the deterministic core resolves candidate PAIRS into a
fail-closed :class:`app.core.identity.MergeProposal`. A false merge is the
IDOR-grade danger this product exists to prevent (INV-4), so an ambiguous /
partial match is **flagged for a human, never auto-merged**.

``GET /merge-queue`` is that human-review queue:

  1. enumerate candidate record PAIRS from the repository, build
     :class:`IdentityCandidate`s from each family's lead identity hints
     (email / region / phone), and run the pure :func:`propose_merge`;
  2. keep only the **REVIEW_QUEUE** verdicts (a clean auto-MERGE candidate is a
     different, non-human-gated surface — this queue is the ambiguous pile);
  3. LOG each review-queue proposal to the §10 observability spine via
     :func:`ObservabilityLog.log_proposal` so it carries a real ``proposal_id``
     the existing ``POST /proposals/{id}/decision`` route can resolve (NFR-6;
     INV-2 — the proposal is created server-side by the deterministic core, never
     client-side);
  4. return the ``MergeCandidate[]`` shape the merge-queue UI is wired to.

**Idempotency.** A ``proposal_id`` is DERIVED (uuid5) from the household pair key
— the sorted ``(primary, duplicate)`` family ids — so re-polling yields the SAME
id and an already-logged proposal is skipped, never re-appended. The append-only
spine therefore never accumulates a duplicate entry per poll.

This module is the composition root for the merge surface: it may import
``app.observability`` and the repository; ``app.core.identity`` stays pure (INV-2).
The merge WRITE itself is owned by the decision route post-approval (see
:func:`app.api.ai_actions.decide_proposal`), never here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends

from app.api.deps import get_observability_log, get_repository
from app.core.identity import IdentityCandidate, MergeVerdict, propose_merge
from app.data.repository import FamilyRepository, JoinedFamily
from app.observability.log_store import ObservabilityLog

router = APIRouter(tags=["merge"])

# The §10 audit head for a logged identity-merge proposal — the discriminator the
# decision route branches on (merge vs nudge). One canonical home (no magic
# string drift): the decision route imports these same constants.
MERGE_FLOW = "identity_merge"
MERGE_SCHEMA_VERSION = "1"

RepositoryDep = Annotated[FamilyRepository, Depends(get_repository)]
LogDep = Annotated[ObservabilityLog, Depends(get_observability_log)]


def _candidate(joined: JoinedFamily) -> IdentityCandidate:
    """Build a dedup candidate from a joined family's lead identity hints.

    ``region`` / ``phone`` are lead-row fields (not first-class FamilyRecord
    columns); a family with no lead row falls back to its spine email and empty
    region/phone, so it can only ever match another record on an identical email
    (never on a spurious empty region/phone pair). All values are synthetic (INV-1).
    """
    lead = joined.lead
    if lead is not None:
        return IdentityCandidate(
            family_id=joined.family.family_id,
            synthetic_email=lead.synthetic_email,
            region=lead.region,
            synthetic_phone=lead.synthetic_phone,
        )
    return IdentityCandidate(
        family_id=joined.family.family_id,
        synthetic_email=joined.family.primary_contact_synthetic_email,
        region="",
        synthetic_phone="",
    )


def _pair_proposal_id(primary: UUID, duplicate: UUID) -> UUID:
    """A DETERMINISTIC proposal id for one household pair (idempotent re-polls).

    Derived (uuid5) from the sorted ``(primary, duplicate)`` ids so the same pair
    always maps to the same id — re-polling ``GET /merge-queue`` finds the
    already-logged proposal and skips re-logging it (the append-only spine never
    grows a duplicate per poll). The sort makes the id orientation-independent.
    """
    a, b = sorted((primary, duplicate), key=str)
    return uuid5(NAMESPACE_URL, f"{MERGE_FLOW}:{a}:{b}")


@router.get("/merge-queue", response_model=list[dict[str, object]])
def merge_queue(repository: RepositoryDep, log: LogDep) -> list[dict[str, object]]:
    """The dedup human-review queue — logged REVIEW_QUEUE proposals (INV-2/INV-4; NFR-6).

    Enumerates candidate family PAIRS, runs the pure :func:`propose_merge`, keeps
    the fail-closed REVIEW_QUEUE verdicts, LOGS each to the §10 spine (idempotent
    by the household pair key), and returns the ``MergeCandidate[]`` shape the UI
    consumes. The merge itself happens only on a later human ``approve`` decision
    (INV-2) — this read never mutates state beyond the append-only audit log.
    """
    joined = repository.list_joined()
    candidates = [_candidate(j) for j in joined]

    out: list[dict[str, object]] = []
    # Enumerate unordered pairs once (i < j). The seeded demo cohort is small; a
    # SQL-backed impl would push this to a blocking-keyed self-join, but the
    # contract (the pure core decides each pair) is identical.
    for i in range(len(candidates)):
        for k in range(i + 1, len(candidates)):
            proposal = propose_merge([candidates[i], candidates[k]])
            if proposal is None or proposal.verdict is not MergeVerdict.REVIEW_QUEUE:
                # No match, or a clean auto-MERGE candidate (a different surface):
                # this queue is the ambiguous, human-gated pile only (INV-4).
                continue

            proposal_id = _pair_proposal_id(
                proposal.primary_family_id, proposal.duplicate_family_id
            )
            # Idempotent: a pair already on the spine is not re-logged (append-only).
            if log.get_audit(proposal_id) is None:
                log.log_proposal(
                    proposal_id=proposal_id,
                    flow=MERGE_FLOW,
                    schema_version=MERGE_SCHEMA_VERSION,
                    # The full proposal artifact, so the decision route + the audit
                    # view can reconstruct the fold targets (INV-2: the deterministic
                    # core authored this payload, not an LLM).
                    payload=proposal.model_dump(mode="json"),
                    family_id=proposal.primary_family_id,
                )

            out.append(
                {
                    "proposal_id": str(proposal_id),
                    "verdict": proposal.verdict.value,
                    "primary_family_id": str(proposal.primary_family_id),
                    "duplicate_family_id": str(proposal.duplicate_family_id),
                    "matched_on": list(proposal.matched_on),
                    "conflicting_keys": list(proposal.conflicting_keys),
                    "summary": proposal.summary,
                }
            )
    return out
