"""M5 — pure SIS match + bucket classifier (deterministic core; INV-2/INV-11).

The reconcile core matches GT's **paid** families against a school's SIS roster
on a normalized email/phone score, then sorts each into a bucket whose boundaries
come entirely from ``params.sis`` (INV-11):

* ✅ ``confirmed``        — confident match, SIS row confirmed.
* 🟡 ``records_lag``      — confident match, SIS row not yet confirmed.
* 🔴 ``paid_not_in_sis``  — paid family, no SIS match.
* ``ambiguous``          — partial (phone-only) match in the uncertain band; it
  carries the candidate SIS id for the **human merge queue** and is NEVER an
  auto-merge/auto-confirm (INV-2/INV-4).

Core purity (ARCHITECTURE §3): this module imports nothing from ``app.adapters``
or ``app.ai``. The SIS boundary shape lives in ``app.adapters.sis.base``; the
matcher consumes a flat core-local :class:`SisRosterRow` instead, and the edge
(``app.data.sis_reconcile_job``) converts the adapter's ``RosterRecord`` into it.
No I/O, no LLM, no clock: a pure function of (families, roster, params). Non-paid
families are not reconciled — a SIS has no reason to carry them yet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.core.params import Params
from app.data.models import FundingState

_EXACT_EMAIL_CONFIDENCE = 1.0  # an exact email match is a full-confidence match
_NO_MATCH_CONFIDENCE = 0.0

# "Paid" = at/after the §5.4 first-installment floor — the families a SIS should
# already carry; divergence from that is what the reconcile buckets surface. The
# canonical home, consumed by the roster generator and the reconcile job.
PAID_FUNDING_STATES: frozenset[FundingState] = frozenset(
    {FundingState.FIRST_INSTALLMENT_RECEIVED, FundingState.FUNDED}
)


class SisBucket(StrEnum):
    """The reconcile verdict for one paid family (MULTI_AGENT_COCKPIT §6)."""

    CONFIRMED = "confirmed"
    RECORDS_LAG = "records_lag"
    PAID_NOT_IN_SIS = "paid_not_in_sis"
    AMBIGUOUS = "ambiguous"  # → human merge queue, never a silent merge


@dataclass(frozen=True)
class SisRosterRow:
    """The flat, core-local view of one SIS roster row the matcher consumes.

    Mirrors the fields of the adapter boundary's ``RosterRecord`` (email/phone
    flattened off ``match_attrs``) so the pure core need not import
    ``app.adapters`` (ARCHITECTURE §3 core purity). The edge converts.
    """

    external_id: str
    email: str | None
    phone: str | None
    enrollment_status: str
    confirmed_at: datetime | None = None


@dataclass(frozen=True)
class FamilyMatchKey:
    """The GT-side identity the matcher compares against the SIS roster.

    Built at the API layer from a ``JoinedFamily`` (the cockpit never matches on
    child PII — only the household contact email/phone, INV-1/INV-6). ``paid``
    flags a family at/after the §5.4 first-installment floor.
    """

    family_id: UUID
    email: str | None
    phone: str | None
    paid: bool


@dataclass(frozen=True)
class SisVerdict:
    """One family's reconcile outcome — the firewall fields plus a merge handle.

    Only ``family_id`` / ``student_id`` / ``present`` / ``confirmed_at`` / ``bucket``
    cross to the cockpit (the PII firewall — ``student_id`` is an opaque owner-scoped
    uuid, NOT child PII). ``matched_external_id`` is the SIS's own opaque id (never
    child PII) kept for routing an ``ambiguous`` verdict to the merge queue.

    ``student_id`` is the per-CHILD grain (A-24): ``None`` = a household-grain verdict;
    set = this verdict attributed to one enrolled child under the matched household
    (:func:`expand_to_students`). The MATCH is always household-only — a child is
    never matched on its own data (INV-6).
    """

    family_id: UUID
    present: bool
    confirmed_at: datetime | None
    bucket: SisBucket
    matched_external_id: str | None = None
    student_id: UUID | None = None


def _norm_email(value: str | None) -> str | None:
    return value.strip().lower() if value else None


def _norm_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value) if value else ""
    return digits or None


def _best_match(
    key: FamilyMatchKey, roster: list[SisRosterRow], phone_only: float
) -> tuple[float, SisRosterRow | None]:
    """Return the highest-confidence (score, row) for ``key`` over the roster."""
    best_score = _NO_MATCH_CONFIDENCE
    best_row: SisRosterRow | None = None
    key_email, key_phone = _norm_email(key.email), _norm_phone(key.phone)
    for row in roster:
        row_email, row_phone = _norm_email(row.email), _norm_phone(row.phone)
        if key_email and row_email and key_email == row_email:
            score = _EXACT_EMAIL_CONFIDENCE
        elif key_phone and row_phone and key_phone == row_phone:
            score = phone_only
        else:
            score = _NO_MATCH_CONFIDENCE
        if score > best_score:
            best_score, best_row = score, row
    return best_score, best_row


def reconcile_family(key: FamilyMatchKey, roster: list[SisRosterRow], params: Params) -> SisVerdict:
    """Classify one paid family against the roster into a :class:`SisBucket`."""
    sis = params.sis
    score, row = _best_match(key, roster, sis.phone_only_confidence)

    if score >= sis.match_confidence_cutoff:
        # A confident match. Confirmed only if the SIS row itself is confirmed;
        # otherwise the SIS is lagging behind GT's recorded payment.
        if (
            row is not None
            and row.enrollment_status == "confirmed"
            and row.confirmed_at is not None
            and score >= sis.confirmed_min_confidence
        ):
            return SisVerdict(
                key.family_id, True, row.confirmed_at, SisBucket.CONFIRMED, row.external_id
            )
        return SisVerdict(
            key.family_id, True, None, SisBucket.RECORDS_LAG, row.external_id if row else None
        )

    if score > sis.paid_not_in_sis_max_confidence:
        # Uncertain — a candidate exists but is below the confident cutoff: route
        # the pair to the human merge queue, never an auto-merge (INV-2/INV-4).
        return SisVerdict(
            key.family_id, True, None, SisBucket.AMBIGUOUS, row.external_id if row else None
        )

    # No usable match for a paid family ⇒ paid-not-in-SIS.
    return SisVerdict(key.family_id, False, None, SisBucket.PAID_NOT_IN_SIS)


def reconcile(
    keys: Iterable[FamilyMatchKey], roster: Iterable[SisRosterRow], params: Params
) -> list[SisVerdict]:
    """Reconcile every PAID family against the roster (input order preserved)."""
    rows = list(roster)
    return [reconcile_family(key, rows, params) for key in keys if key.paid]


def expand_to_students(
    verdicts: Iterable[SisVerdict],
    students_by_family: Mapping[UUID, list[UUID]],
) -> list[SisVerdict]:
    """Attribute each HOUSEHOLD verdict to each enrolled CHILD under it (A-24).

    Pure. The matcher stays household-only — it never sees child data (INV-6) — and
    this is the SEPARATE step that attaches the household's ✅/🟡/🔴 to its children
    by opaque ``student_id`` (a uuid, not PII), one verdict per child. SIS confirms
    the household is enrolled; the children under it inherit that status, so a parent
    sees per-child confirmation on a call. A family with NO known children keeps a
    single household-grain verdict (``student_id`` None) — back-compat. Order is
    preserved (household order; children in their ``students_by_family`` order).
    """
    out: list[SisVerdict] = []
    for verdict in verdicts:
        children = students_by_family.get(verdict.family_id, [])
        if not children:
            out.append(verdict)
            continue
        out.extend(replace(verdict, student_id=student_id) for student_id in children)
    return out
