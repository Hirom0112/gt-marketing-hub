"""M5 — pure SIS match + bucket classifier (deterministic core; INV-2/INV-11).

The reconcile core matches GT's **paid** families against a school's SIS roster
(:class:`~app.adapters.sis.base.RosterRecord` — the only SIS view it consumes,
INV-9) on a normalized email/phone score, then sorts each into a bucket whose
boundaries come entirely from ``params.sis`` (INV-11):

* ✅ ``confirmed``        — confident match, SIS row confirmed.
* 🟡 ``records_lag``      — confident match, SIS row not yet confirmed.
* 🔴 ``paid_not_in_sis``  — paid family, no SIS match.
* ``ambiguous``          — partial (phone-only) match in the uncertain band; it
  carries the candidate SIS id for the **human merge queue** and is NEVER an
  auto-merge/auto-confirm (INV-2/INV-4).

No I/O, no LLM, no clock: a pure function of (families, roster, params). Non-paid
families are not reconciled — a SIS has no reason to carry them yet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.adapters.sis.base import RosterRecord
from app.core.params import Params

_EXACT_EMAIL_CONFIDENCE = 1.0  # an exact email match is a full-confidence match
_NO_MATCH_CONFIDENCE = 0.0


class SisBucket(StrEnum):
    """The reconcile verdict for one paid family (MULTI_AGENT_COCKPIT §6)."""

    CONFIRMED = "confirmed"
    RECORDS_LAG = "records_lag"
    PAID_NOT_IN_SIS = "paid_not_in_sis"
    AMBIGUOUS = "ambiguous"  # → human merge queue, never a silent merge


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

    Only ``family_id`` / ``present`` / ``confirmed_at`` / ``bucket`` cross to the
    cockpit (the PII firewall). ``matched_external_id`` is the SIS's own opaque id
    (never child PII) kept for routing an ``ambiguous`` verdict to the merge queue.
    """

    family_id: UUID
    present: bool
    confirmed_at: datetime | None
    bucket: SisBucket
    matched_external_id: str | None = None


def _norm_email(value: str | None) -> str | None:
    return value.strip().lower() if value else None


def _norm_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value) if value else ""
    return digits or None


def _best_match(
    key: FamilyMatchKey, roster: list[RosterRecord], phone_only: float
) -> tuple[float, RosterRecord | None]:
    """Return the highest-confidence (score, record) for ``key`` over the roster."""
    best_score = _NO_MATCH_CONFIDENCE
    best_record: RosterRecord | None = None
    key_email, key_phone = _norm_email(key.email), _norm_phone(key.phone)
    for record in roster:
        rec_email, rec_phone = (
            _norm_email(record.match_attrs.email),
            _norm_phone(record.match_attrs.phone),
        )
        if key_email and rec_email and key_email == rec_email:
            score = _EXACT_EMAIL_CONFIDENCE
        elif key_phone and rec_phone and key_phone == rec_phone:
            score = phone_only
        else:
            score = _NO_MATCH_CONFIDENCE
        if score > best_score:
            best_score, best_record = score, record
    return best_score, best_record


def reconcile_family(key: FamilyMatchKey, roster: list[RosterRecord], params: Params) -> SisVerdict:
    """Classify one paid family against the roster into a :class:`SisBucket`."""
    sis = params.sis
    score, record = _best_match(key, roster, sis.phone_only_confidence)

    if score >= sis.match_confidence_cutoff:
        # A confident match. Confirmed only if the SIS row itself is confirmed;
        # otherwise the SIS is lagging behind GT's recorded payment.
        if (
            record is not None
            and record.enrollment_status == "confirmed"
            and record.confirmed_at is not None
            and score >= sis.confirmed_min_confidence
        ):
            return SisVerdict(
                key.family_id, True, record.confirmed_at, SisBucket.CONFIRMED, record.external_id
            )
        return SisVerdict(
            key.family_id, True, None, SisBucket.RECORDS_LAG, record.external_id if record else None
        )

    if score > sis.paid_not_in_sis_max_confidence:
        # Uncertain — a candidate exists but is below the confident cutoff: route
        # the pair to the human merge queue, never an auto-merge (INV-2/INV-4).
        return SisVerdict(
            key.family_id, True, None, SisBucket.AMBIGUOUS, record.external_id if record else None
        )

    # No usable match for a paid family ⇒ paid-not-in-SIS.
    return SisVerdict(key.family_id, False, None, SisBucket.PAID_NOT_IN_SIS)


def reconcile(
    keys: Iterable[FamilyMatchKey], roster: Iterable[RosterRecord], params: Params
) -> list[SisVerdict]:
    """Reconcile every PAID family against the roster (input order preserved)."""
    records = list(roster)
    return [reconcile_family(key, records, params) for key in keys if key.paid]
