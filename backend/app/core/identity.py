"""Identity resolution / dedup core (TODO.md R1; INV-2 / INV-4 / INV-11).

Duplicate-lead chaos is structural today: ``family_id`` is minted fresh per
application, so the same parent applying twice becomes two families and two
HubSpot contacts, and HubSpot-NATIVE records (the tier-(c) problem) arrive with
no ``gt_synthetic_id`` at all. This module is the **deterministic core** for

1. a stable household identity key (:func:`household_key`), and
2. resolving candidate records into a fail-closed, human-approved
   :class:`MergeProposal` (:func:`propose_merge`) — **without ever auto-merging
   two distinct families**.

It follows the proposal/decision spine of ``app.core.seam`` (the
``propose_*`` / fail-closed pattern): a match yields a *proposal*, never a
write. The deterministic core owns all writes; an LLM never does (INV-2). A
false merge is as dangerous as the IDOR this product exists to prevent, so an
ambiguous match is flagged for a human, never silently merged (INV-4).

PURE (CLAUDE.md §3): no I/O, no LLM, no adapter imports. Matching is a purely
deterministic exact-match rule on a normalized identity key, so it needs **no
threshold** — there is no magic number to externalize (INV-11): normalization
is structural (casing/whitespace/punctuation), not a tunable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.data.models import FamilyRecord

# ---------------------------------------------------------------------------
# Normalization — structural, deterministic, no tunables (INV-11).
# ---------------------------------------------------------------------------

_NON_PHONE_DIGITS = re.compile(r"\D+")


def _normalize_email(raw: str) -> str:
    """Casefold + trim an email to a comparison key.

    Strips surrounding whitespace and lowercases (emails are case-insensitive
    for matching here): ``"  Rivera@Example.TEST "`` ⇒ ``"rivera@example.test"``.
    """
    return raw.strip().casefold()


def _normalize_region(raw: str) -> str:
    """Casefold + trim a region label to a comparison key (aggregate only, P-4)."""
    return raw.strip().casefold()


def _normalize_phone(raw: str) -> str:
    """Reduce a phone to its digits so punctuation/spacing don't block a match.

    ``"(512) 555.0100"`` and ``"512-555-0100"`` both normalize to
    ``"5125550100"``. An empty/blank phone normalizes to ``""``.
    """
    return _NON_PHONE_DIGITS.sub("", raw)


# ---------------------------------------------------------------------------
# Candidate input — carries the identity hints propose_merge matches on.
# region/phone are not first-class FamilyRecord columns (they live on the lead
# row / HubSpot native record), so the candidate supplies them explicitly. This
# also lets a HubSpot-NATIVE record with no gt_synthetic_id participate: it only
# needs a family_id placeholder + its contact hints.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IdentityCandidate:
    """One record's identity hints for dedup matching (synthetic-named, INV-1).

    Attributes:
        family_id: The candidate's current family id (freshly minted per
            application today — that is the duplication this module resolves).
        synthetic_email: The primary contact email (synthetic; INV-1).
        region: The aggregate region label (no precise geo of minors; P-4).
        synthetic_phone: The primary contact phone (synthetic; INV-1).
    """

    family_id: UUID
    synthetic_email: str
    region: str
    synthetic_phone: str


# ---------------------------------------------------------------------------
# Verdict + proposal — the fail-closed, human-approved artifact (INV-2 / INV-4).
# ---------------------------------------------------------------------------


class MergeVerdict(StrEnum):
    """The deterministic dedup outcome for two candidates.

    - ``merge`` — an exact match on a normalized identity key with no
      conflicting key: propose a merge (still a PROPOSAL, never an auto-write —
      INV-2).
    - ``review_queue`` — an ambiguous / partial match (one identity key agrees,
      another conflicts): flag for a human, **never** auto-merge (INV-4
      fail-closed). A false merge is the IDOR-grade danger this prevents.
    """

    MERGE = "merge"
    REVIEW_QUEUE = "review_queue"


class MergeProposal(BaseModel):
    """A proposed (or flagged) dedup resolution for two candidate records.

    Frozen: like :class:`app.core.seam.ReconcileProposal`, a proposal is an
    immutable artifact handed to the human-approval path, never mutated in
    place. ``applied`` is always ``False`` — the deterministic core emits a
    proposal; a human approves before any write happens (INV-2). The wiring (a
    merge-queue UI, reconcile consuming this) is a later task.

    Attributes:
        verdict: :class:`MergeVerdict` — ``merge`` or ``review_queue``.
        primary_family_id: The family kept as the survivor (the lexicographically
            smaller id, for a stable, deterministic choice).
        duplicate_family_id: The family proposed to fold into the primary.
        matched_on: The normalized identity keys that agreed (e.g.
            ``("email", "region")`` or ``("phone",)``), for the human reviewer.
        conflicting_keys: The identity keys that disagreed — empty for a clean
            ``merge``, populated for a ``review_queue`` verdict.
        summary: A human-readable one-line description of the proposal.
        applied: Always ``False`` — a proposal is never an auto-write (INV-2).
    """

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    verdict: MergeVerdict
    primary_family_id: UUID
    duplicate_family_id: UUID
    matched_on: tuple[str, ...]
    conflicting_keys: tuple[str, ...]
    summary: str
    applied: bool = False


# ---------------------------------------------------------------------------
# household_key — stable household identity (pure, deterministic).
# ---------------------------------------------------------------------------


def household_key(record: FamilyRecord) -> str:
    """Return a stable household-identity key for a family record.

    Rule (documented): the ``user_id`` ownership root is the household root
    where present — every record owned by the same user is the same household
    (THREAT_MODEL §6 D-RLS-2: ``auth.uid() = user_id`` is the ownership spine).
    When ``user_id`` is ``None`` (a server-only marketing-lead row, D-RLS-3) the
    record falls back to its own ``family_id`` so it still keys deterministically
    and never collides with a user-owned household.

    The key is namespaced (``"user:"`` / ``"family:"``) so a ``user_id`` can
    never coincide with a ``family_id`` value.

    Args:
        record: The :class:`app.data.models.FamilyRecord` to key.

    Returns:
        A stable string key; equal keys ⇒ same household.
    """
    if record.user_id is not None:
        return f"user:{record.user_id}"
    return f"family:{record.family_id}"


# ---------------------------------------------------------------------------
# propose_merge — deterministic exact-match dedup, fail-closed (INV-2 / INV-4).
# ---------------------------------------------------------------------------


def propose_merge(candidates: list[IdentityCandidate]) -> MergeProposal | None:
    """Propose (or flag) a merge for a pair of candidate records.

    Deterministic exact-match rule on normalized identity keys — no threshold,
    no magic number (INV-11). Two keys are evaluated:

    - **identity key** = normalized email + region (the household contact), and
    - **phone** = normalized phone digits.

    The verdict, fail-closed (INV-2 / INV-4):

    - both candidates agree on the identity key AND (phone agrees OR a phone is
      absent on either side) ⇒ :attr:`MergeVerdict.MERGE` — a *proposal* (never
      an auto-write);
    - the identity key agrees but the phones are present and *differ* ⇒
      :attr:`MergeVerdict.REVIEW_QUEUE` — an ambiguous/partial match, flagged for
      a human, **never** auto-merged;
    - the identity key disagrees but the phones are present and *match* ⇒
      :attr:`MergeVerdict.REVIEW_QUEUE` — same email differs / shared phone is
      ambiguous, flagged for a human;
    - nothing agrees ⇒ ``None`` (no proposal). Two genuinely different families
      that merely share a region never match (region alone is not an identity
      key), so they stay separate.

    Exactly two candidates are expected (a registration row + a HubSpot-native
    record, the tier-(c) case). Fewer than two ⇒ ``None``.

    Args:
        candidates: The candidate records to resolve (a pair).

    Returns:
        A :class:`MergeProposal`, or ``None`` when there is no match at all.
    """
    if len(candidates) < 2:
        return None

    a, b = candidates[0], candidates[1]

    email_match = _normalize_email(a.synthetic_email) == _normalize_email(b.synthetic_email)
    region_match = _normalize_region(a.region) == _normalize_region(b.region)
    identity_match = email_match and region_match

    phone_a = _normalize_phone(a.synthetic_phone)
    phone_b = _normalize_phone(b.synthetic_phone)
    phones_present = bool(phone_a) and bool(phone_b)
    phone_match = phones_present and phone_a == phone_b

    # Stable survivor: the lexicographically smaller family_id is the primary.
    primary, duplicate = sorted((a.family_id, b.family_id), key=str)

    # Clean merge: contact identity agrees and the phone does not contradict it.
    if identity_match and (phone_match or not phones_present):
        matched: list[str] = ["email", "region"]
        if phone_match:
            matched.append("phone")
        return MergeProposal(
            verdict=MergeVerdict.MERGE,
            primary_family_id=primary,
            duplicate_family_id=duplicate,
            matched_on=tuple(matched),
            conflicting_keys=(),
            summary=(
                "Exact match on " + " + ".join(matched) + " — propose merging duplicate "
                f"family {duplicate} into {primary} (human approval required)."
            ),
        )

    # Ambiguous: identity agrees but phones present-and-differ — never auto-merge.
    if identity_match and phones_present and not phone_match:
        return MergeProposal(
            verdict=MergeVerdict.REVIEW_QUEUE,
            primary_family_id=primary,
            duplicate_family_id=duplicate,
            matched_on=("email", "region"),
            conflicting_keys=("phone",),
            summary=(
                "Partial match: email + region agree but phones differ — flagged "
                "for human review, not auto-merged (fail-closed)."
            ),
        )

    # Ambiguous: identity disagrees but a shared phone — flag, never auto-merge.
    if not identity_match and phone_match:
        conflicting = tuple(
            k for k, ok in (("email", email_match), ("region", region_match)) if not ok
        )
        return MergeProposal(
            verdict=MergeVerdict.REVIEW_QUEUE,
            primary_family_id=primary,
            duplicate_family_id=duplicate,
            matched_on=("phone",),
            conflicting_keys=conflicting,
            summary=(
                "Partial match: phone agrees but " + "/".join(conflicting) + " differ — "
                "flagged for human review, not auto-merged (fail-closed)."
            ),
        )

    # No identity-key agreement at all (region alone is never an identity key):
    # two distinct families stay separate.
    return None
