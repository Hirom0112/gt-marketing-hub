"""TEFA installment-math tests (S3; FR-2.7; ARCHITECTURE.md §8; CLAUDE.md §4.1).

The TEFA funding tiers are a worked target (CLAUDE.md §4.1): the standard award
is **$10,474.00**, split **25 / 25 / 50** into three installments that must sum
back to the award with zero rounding drift. Money math is `Decimal`, quantized
to cents, and every amount + split is read FROM params (INV-11) — never a
literal in `funding_gate.py`.

The expected per-installment schedules are asserted to the exact cent for all
three tiers; the last installment is `award − sum(prior)` so the schedule always
reconciles to the award (sum == award exactly).

Deterministic without a local `params/params.yaml` (gitignored, not created):
the committed `params/params.example.yaml` is passed explicitly, mirroring
`tests/unit/test_work_queue.py`.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.core.funding_gate import compute_installments
from app.core.params import Params, load_params
from app.data.models import FundingType

# The committed example file is the authoritative params source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params() -> Params:
    return load_params(EXAMPLE_PARAMS)


def _cents(value: str) -> Decimal:
    return Decimal(value)


def test_standard_installments_exact_cents() -> None:
    """STANDARD: $10,474.00 split 25/25/50 ⇒ [2618.50, 2618.50, 5237.00]."""
    params = _params()
    installments = compute_installments(FundingType.TEFA_STANDARD, params)

    assert installments == [_cents("2618.50"), _cents("2618.50"), _cents("5237.00")]
    assert sum(installments) == _cents("10474.00")


def test_disability_installments_exact_cents() -> None:
    """DISABILITY: $30,000.00 split 25/25/50 ⇒ [7500.00, 7500.00, 15000.00]."""
    params = _params()
    installments = compute_installments(FundingType.TEFA_DISABILITY, params)

    assert installments == [_cents("7500.00"), _cents("7500.00"), _cents("15000.00")]
    assert sum(installments) == _cents("30000.00")


def test_homeschool_installments_exact_cents() -> None:
    """HOMESCHOOL: $2,000.00 split 25/25/50 ⇒ [500.00, 500.00, 1000.00]."""
    params = _params()
    installments = compute_installments(FundingType.TEFA_HOMESCHOOL, params)

    assert installments == [_cents("500.00"), _cents("500.00"), _cents("1000.00")]
    assert sum(installments) == _cents("2000.00")


def test_installments_are_decimal_quantized_to_cents() -> None:
    """Every installment is a `Decimal` quantized to two decimal places."""
    params = _params()
    for tier in (
        FundingType.TEFA_STANDARD,
        FundingType.TEFA_DISABILITY,
        FundingType.TEFA_HOMESCHOOL,
    ):
        installments = compute_installments(tier, params)
        assert all(isinstance(amount, Decimal) for amount in installments)
        assert all(amount == amount.quantize(Decimal("0.01")) for amount in installments)


def test_installments_reconcile_to_award_for_every_tier() -> None:
    """The schedule sums back to the award exactly — no rounding drift (FR-2.7).

    The award per tier is read from params (INV-11); the last installment is
    `award − sum(prior)`, so the total reconciles to the award with zero drift.
    """
    params = _params()
    awards = {
        FundingType.TEFA_STANDARD: params.funding.award_amounts.tefa_standard,
        FundingType.TEFA_DISABILITY: params.funding.award_amounts.tefa_disability,
        FundingType.TEFA_HOMESCHOOL: params.funding.award_amounts.tefa_homeschool,
    }
    for tier, award in awards.items():
        installments = compute_installments(tier, params)
        assert sum(installments) == Decimal(str(award)).quantize(Decimal("0.01"))
        # One installment per split entry (FR-2.7: ~Jul1 / Oct1 / Feb1).
        assert len(installments) == len(params.funding.installment_split)


def test_non_tefa_tier_is_rejected() -> None:
    """SELF_PAY has no TEFA award; computing installments is rejected (fail-closed)."""
    params = _params()
    try:
        compute_installments(FundingType.SELF_PAY, params)
    except ValueError:
        return
    raise AssertionError("expected ValueError for a non-TEFA tier")
