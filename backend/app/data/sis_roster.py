"""M5 — synthetic SIS roster generator (INV-1, INV-9).

The reconcile core (M5) matches GT's pipeline families against a school's Student
Information System roster to detect divergence. In v1 the roster is SYNTHETIC,
derived from the synthetic cohort: most paid families appear confirmed on the SIS
(→ ✅), a few are absent (→ 🔴 paid_not_in_sis), and a few are present but the SIS
has not caught up (→ 🟡 records_lag). Every row is a normalized
:class:`~app.adapters.sis.base.RosterRecord` — the only shape the reconcile core
consumes (INV-9) — and carries only synthetic identifiers (INV-1).
"""

from __future__ import annotations

import random
from datetime import timedelta
from uuid import UUID

from app.adapters.sis.base import MatchAttrs, RosterRecord
from app.core.params import Params
from app.data.models import FundingState
from app.data.synthetic import _EPOCH, SyntheticDataset

# "Paid" = at/after the §5.4 first-installment floor — the families a SIS should
# already carry; divergence from that expectation is what the buckets surface.
_PAID: frozenset[FundingState] = frozenset(
    {FundingState.FIRST_INSTALLMENT_RECEIVED, FundingState.FUNDED}
)

_CONFIRMED = "confirmed"
_PENDING = "pending"


def generate_sis_roster(
    dataset: SyntheticDataset, *, seed: int, params: Params
) -> list[RosterRecord]:
    """Build a deterministic synthetic SIS roster with seeded divergence.

    Determinism: the paid cohort is sorted by ``family_id`` and every random draw
    comes from ``random.Random(seed)`` — same dataset + seed ⇒ byte-identical
    roster. Divergence is seeded structurally so all three buckets are reachable:
    the first paid family is omitted (🔴 paid_not_in_sis), the second is present
    but unconfirmed (🟡 records_lag), and the rest are confirmed (✅).
    """
    rng = random.Random(seed)
    lag_days = params.sis.records_lag_days
    phone_by_family = {lead.family_id: lead.synthetic_phone for lead in dataset.leads}

    paid = sorted(
        (f for f in dataset.families if f.funding_state in _PAID),
        key=lambda f: str(f.family_id),
    )
    enough = len(paid) >= 3

    roster: list[RosterRecord] = []
    for index, family in enumerate(paid):
        # 🔴 paid_not_in_sis — no SIS row at all for this paid family.
        if enough and index == 0:
            continue
        external_id = f"SIS-{UUID(int=rng.getrandbits(128), version=4)}"
        attrs = MatchAttrs(
            email=family.primary_contact_synthetic_email,
            phone=phone_by_family.get(family.family_id),
        )
        # 🟡 records_lag — on the roster, but the SIS has not confirmed yet.
        if enough and index == 1:
            roster.append(
                RosterRecord(
                    external_id=external_id,
                    match_attrs=attrs,
                    enrollment_status=_PENDING,
                    confirmed_at=None,
                )
            )
            continue
        # ✅ confirmed — present + confirmed within the records-lag window.
        confirmed_at = _EPOCH - timedelta(
            days=rng.randint(0, max(lag_days - 1, 0)), minutes=rng.randint(0, 1439)
        )
        roster.append(
            RosterRecord(
                external_id=external_id,
                match_attrs=attrs,
                enrollment_status=_CONFIRMED,
                confirmed_at=confirmed_at,
            )
        )
    return roster
