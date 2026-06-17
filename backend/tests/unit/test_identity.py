"""Identity-resolution / dedup core (TODO.md R1; INV-2/INV-4/INV-11).

Golden fixtures for the deterministic household-identity + merge-proposal core:

- ``household_key`` is stable and pure: the ``user_id`` ownership root
  (THREAT_MODEL §6 D-RLS-2) is the household root where present, else the
  ``family_id`` falls back so a server-only marketing row (``user_id`` null)
  still keys deterministically.
- ``propose_merge`` is deterministic and **fail-closed** (INV-2/INV-4):
    * exact normalized match (email + region, or phone) ⇒ a MERGE *proposal*
      (never an auto-write — still requires human approval);
    * same person with different email casing/whitespace/punctuation ⇒ matched
      after normalization ⇒ MERGE proposal;
    * two genuinely different families that merely share a region ⇒ NO merge
      (they stay separate — a false merge is the IDOR-grade danger this exists
      to prevent);
    * a partial / ambiguous match (one identity key agrees, another conflicts)
      ⇒ a REVIEW_QUEUE verdict — flagged for a human, **never** auto-merged;
    * no match ⇒ no proposal (``None``).

``household_key`` consumes a :class:`FamilyRecord`; ``propose_merge`` consumes
:class:`IdentityCandidate` wrappers that carry the identity hints (email,
region, phone) — region/phone are not first-class ``FamilyRecord`` columns, so
the candidate wrapper supplies them explicitly. The module is PURE
(CLAUDE.md §3): no I/O, no LLM, no adapter imports — guarded by
``test_core_purity.py``.
"""

from __future__ import annotations

from uuid import UUID

from app.core.identity import (
    IdentityCandidate,
    MergeProposal,
    MergeVerdict,
    household_key,
    propose_merge,
)
from app.data.models import FamilyRecord, Stage

# ---------------------------------------------------------------------------
# Fixtures. Synthetic-named PII fields (INV-1).
# ---------------------------------------------------------------------------

_USER_A = UUID("11111111-1111-1111-1111-111111111111")
_FAM_1 = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_FAM_2 = UUID("aaaaaaaa-0000-0000-0000-000000000002")


def _record(*, family_id: UUID, user_id: UUID | None = None, email: str) -> FamilyRecord:
    """Minimal FamilyRecord for household_key fixtures."""
    return FamilyRecord(
        family_id=family_id,
        user_id=user_id,
        display_name="Rivera household",
        primary_contact_synthetic_email=email,
        current_stage=Stage.INTEREST,
        attribution_source="organic",
        attribution_utm={},
    )


def _candidate(
    *,
    family_id: UUID,
    email: str,
    region: str = "austin-metro",
    phone: str = "512-555-0100",
) -> IdentityCandidate:
    """Identity candidate carrying the dedup hints (email/region/phone)."""
    return IdentityCandidate(
        family_id=family_id,
        synthetic_email=email,
        region=region,
        synthetic_phone=phone,
    )


# ---------------------------------------------------------------------------
# household_key
# ---------------------------------------------------------------------------


def test_household_key_uses_user_id_root_when_present() -> None:
    """user_id is the household root: two records under one user share a key."""
    rec_a = _record(family_id=_FAM_1, user_id=_USER_A, email="rivera@example.test")
    rec_b = _record(family_id=_FAM_2, user_id=_USER_A, email="rivera+two@example.test")
    assert household_key(rec_a) == household_key(rec_b)
    assert str(_USER_A) in household_key(rec_a)


def test_household_key_falls_back_to_family_id_when_no_user() -> None:
    """A server-only row (user_id None) keys deterministically off family_id."""
    rec = _record(family_id=_FAM_1, user_id=None, email="rivera@example.test")
    assert household_key(rec) == household_key(rec)  # stable / pure
    assert str(_FAM_1) in household_key(rec)


def test_household_key_distinct_for_distinct_users() -> None:
    """Different ownership roots ⇒ different household keys."""
    other_user = UUID("22222222-2222-2222-2222-222222222222")
    rec_a = _record(family_id=_FAM_1, user_id=_USER_A, email="a@example.test")
    rec_b = _record(family_id=_FAM_2, user_id=other_user, email="b@example.test")
    assert household_key(rec_a) != household_key(rec_b)


# ---------------------------------------------------------------------------
# propose_merge — exact match ⇒ MERGE proposal (still human-approved, INV-2)
# ---------------------------------------------------------------------------


def test_exact_duplicate_proposes_merge() -> None:
    """Same email + region + phone ⇒ a MERGE proposal."""
    registration = _candidate(family_id=_FAM_1, email="rivera@example.test")
    hubspot_native = _candidate(family_id=_FAM_2, email="rivera@example.test")
    proposal = propose_merge([registration, hubspot_native])
    assert proposal is not None
    assert isinstance(proposal, MergeProposal)
    assert proposal.verdict is MergeVerdict.MERGE
    assert {proposal.primary_family_id, proposal.duplicate_family_id} == {_FAM_1, _FAM_2}
    assert proposal.applied is False  # INV-2: a proposal, never an auto-write.


def test_normalization_matches_casing_and_whitespace() -> None:
    """Same person, different email casing/whitespace + phone punctuation ⇒ MERGE."""
    a = _candidate(family_id=_FAM_1, email="rivera@example.test", phone="512-555-0100")
    b = _candidate(family_id=_FAM_2, email="  Rivera@Example.TEST ", phone="(512) 555.0100")
    proposal = propose_merge([a, b])
    assert proposal is not None
    assert proposal.verdict is MergeVerdict.MERGE


# ---------------------------------------------------------------------------
# propose_merge — NEVER auto-merge two distinct families (INV-4)
# ---------------------------------------------------------------------------


def test_same_region_different_families_no_merge() -> None:
    """Two genuinely different families sharing a region must stay separate."""
    fam_a = _candidate(
        family_id=_FAM_1, email="alvarez@example.test", phone="512-555-0001", region="austin-metro"
    )
    fam_b = _candidate(
        family_id=_FAM_2,
        email="washington@example.test",
        phone="512-555-0002",
        region="austin-metro",
    )
    assert propose_merge([fam_a, fam_b]) is None


def test_no_match_returns_none() -> None:
    """Nothing in common ⇒ no proposal."""
    fam_a = _candidate(
        family_id=_FAM_1, email="a@example.test", phone="512-555-0001", region="north"
    )
    fam_b = _candidate(
        family_id=_FAM_2, email="b@example.test", phone="617-555-0002", region="south"
    )
    assert propose_merge([fam_a, fam_b]) is None


def test_ambiguous_partial_match_goes_to_review_queue() -> None:
    """One key agrees, another conflicts ⇒ REVIEW_QUEUE, never auto-merge.

    Same email + region but a *different* phone is an ambiguous/partial match:
    flagged for a human, never silently merged (INV-4 fail-closed).
    """
    a = _candidate(
        family_id=_FAM_1, email="shared@example.test", region="austin-metro", phone="512-555-1111"
    )
    b = _candidate(
        family_id=_FAM_2, email="shared@example.test", region="austin-metro", phone="512-555-9999"
    )
    proposal = propose_merge([a, b])
    assert proposal is not None
    assert proposal.verdict is MergeVerdict.REVIEW_QUEUE
    assert proposal.applied is False


def test_proposal_is_frozen() -> None:
    """A MergeProposal is an immutable artifact handed to the approval path."""
    a = _candidate(family_id=_FAM_1, email="rivera@example.test")
    b = _candidate(family_id=_FAM_2, email="rivera@example.test")
    proposal = propose_merge([a, b])
    assert proposal is not None
    try:
        proposal.verdict = MergeVerdict.REVIEW_QUEUE  # type: ignore[misc]
    except Exception:  # noqa: BLE001 — pydantic raises on a frozen mutation
        return
    raise AssertionError("MergeProposal must be frozen / immutable")
