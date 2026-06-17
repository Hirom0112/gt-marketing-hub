"""M5 — the pure SIS match + bucket classifier (server-side, deterministic).

TODO.md M5 item 3: the matcher compares GT's paid families against the SIS roster
on normalized email/phone and assigns each a bucket per ``params.sis``:

* ✅ ``confirmed``        — a confident match whose SIS row is confirmed.
* 🟡 ``records_lag``      — a confident match whose SIS row has NOT confirmed yet.
* 🔴 ``paid_not_in_sis``  — a paid family with no SIS match.
* ``ambiguous``          — a partial (phone-only) match in the uncertain band; it
  routes to the human merge queue (INV-2/INV-4), **never a silent merge**.

Pure core: thresholds come from params (the test fails if a param drifts), no
I/O, no LLM (INV-2/INV-11). Non-paid families are not reconciled (a SIS has no
reason to carry them yet).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.core.params import load_params
from app.core.sis_reconcile import FamilyMatchKey, SisBucket, SisRosterRow, reconcile

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

_FID_CONFIRMED = UUID(int=1)
_FID_LAG = UUID(int=2)
_FID_ABSENT = UUID(int=3)
_FID_AMBIGUOUS = UUID(int=4)
_FID_UNPAID = UUID(int=5)


def test_match_and_bucket() -> None:
    params = load_params(EXAMPLE_PARAMS)
    confirmed_at = datetime(2026, 6, 10, tzinfo=UTC)

    keys = [
        FamilyMatchKey(_FID_CONFIRMED, "a@example.invalid", "555-0110", paid=True),
        FamilyMatchKey(_FID_LAG, "b@example.invalid", "555-0120", paid=True),
        FamilyMatchKey(_FID_ABSENT, "c@example.invalid", "555-0130", paid=True),
        FamilyMatchKey(_FID_AMBIGUOUS, "d@example.invalid", "555-0140", paid=True),
        FamilyMatchKey(_FID_UNPAID, "e@example.invalid", "555-0150", paid=False),
    ]
    roster = [
        # exact-email + confirmed ⇒ ✅
        SisRosterRow(
            external_id="SIS-1",
            email="a@example.invalid",
            phone="555-0110",
            enrollment_status="confirmed",
            confirmed_at=confirmed_at,
        ),
        # exact-email but SIS not confirmed ⇒ 🟡
        SisRosterRow(
            external_id="SIS-2",
            email="b@example.invalid",
            phone="555-0120",
            enrollment_status="pending",
            confirmed_at=None,
        ),
        # phone-only match (different email) ⇒ ambiguous → merge queue
        SisRosterRow(
            external_id="SIS-9",
            email="someone-else@example.invalid",
            phone="555-0140",
            enrollment_status="confirmed",
            confirmed_at=confirmed_at,
        ),
        # a confirmed row for the UNPAID family — must still be ignored (not paid)
        SisRosterRow(
            external_id="SIS-5",
            email="e@example.invalid",
            phone="555-0150",
            enrollment_status="confirmed",
            confirmed_at=confirmed_at,
        ),
    ]

    verdicts = {v.family_id: v for v in reconcile(keys, roster, params)}

    # Non-paid families are not reconciled at all.
    assert _FID_UNPAID not in verdicts
    assert set(verdicts) == {_FID_CONFIRMED, _FID_LAG, _FID_ABSENT, _FID_AMBIGUOUS}

    assert verdicts[_FID_CONFIRMED].bucket is SisBucket.CONFIRMED
    assert verdicts[_FID_CONFIRMED].present is True
    assert verdicts[_FID_CONFIRMED].confirmed_at == confirmed_at

    assert verdicts[_FID_LAG].bucket is SisBucket.RECORDS_LAG
    assert verdicts[_FID_LAG].present is True
    assert verdicts[_FID_LAG].confirmed_at is None

    assert verdicts[_FID_ABSENT].bucket is SisBucket.PAID_NOT_IN_SIS
    assert verdicts[_FID_ABSENT].present is False

    # The ambiguous tail is flagged for human review, NOT silently confirmed/merged.
    amb = verdicts[_FID_AMBIGUOUS]
    assert amb.bucket is SisBucket.AMBIGUOUS
    assert amb.bucket is not SisBucket.CONFIRMED
    assert amb.matched_external_id == "SIS-9"  # the candidate the human reviews


def test_thresholds_read_from_params_not_hardcoded() -> None:
    """A confidence-param drift must change classification (INV-11 guard)."""
    params = load_params(EXAMPLE_PARAMS)
    key = [FamilyMatchKey(_FID_AMBIGUOUS, "d@example.invalid", "555-0140", paid=True)]
    roster = [
        SisRosterRow(
            external_id="SIS-9",
            email="other@example.invalid",
            phone="555-0140",
            enrollment_status="confirmed",
            confirmed_at=datetime(2026, 6, 10, tzinfo=UTC),
        )
    ]
    # With the shipped cutoff (0.9) the phone-only 0.6 match is ambiguous.
    assert reconcile(key, roster, params)[0].bucket is SisBucket.AMBIGUOUS

    # Drift the floor up past the phone-only score ⇒ it falls to paid_not_in_sis.
    drifted = params.model_copy(
        update={"sis": params.sis.model_copy(update={"paid_not_in_sis_max_confidence": 0.7})}
    )
    assert reconcile(key, roster, drifted)[0].bucket is SisBucket.PAID_NOT_IN_SIS
