"""M5 — the synthetic SIS roster generator seeds all three reconcile outcomes.

TODO.md M5 item 1: the generator produces a *deterministic* roster over the
synthetic cohort in which, once the M5 matcher runs, all three buckets appear:
some GT-paid families are ABSENT from the SIS (→ 🔴 paid_not_in_sis), some are
present but LAGGING (→ 🟡 records_lag), and most are present + confirmed (→ ✅).

This is a `data/` test (INV-1): the roster is built from the synthetic cohort and
carries only synthetic identifiers (``@example.invalid`` emails, ``555-01xx``
phones). The roster is the SIS's own view, so each row is a
:class:`~app.adapters.sis.base.RosterRecord` — the only shape the reconcile core
ever consumes (INV-9).
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import load_params
from app.data.models import FundingState
from app.data.sis_roster import generate_sis_roster
from app.data.synthetic import SyntheticDataset, generate

# The loader's bare default (`params/params.yaml`) is gitignored and CWD-relative;
# tests load the committed example explicitly (the house pattern). ``parents[3]``
# from ``tests/data/`` is the repo root.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

# "Paid" = at/after the §5.4 first-installment floor (the families a SIS should
# already carry). Mirrors ``funding.tuition_unlock_state``.
_PAID: frozenset[FundingState] = frozenset(
    {FundingState.FIRST_INSTALLMENT_RECEIVED, FundingState.FUNDED}
)


def _paid_emails(ds: SyntheticDataset) -> set[str]:
    return {f.primary_contact_synthetic_email for f in ds.families if f.funding_state in _PAID}


def test_seeded_divergence() -> None:
    params = load_params(EXAMPLE_PARAMS)
    ds = generate(60, seed=7)
    roster = generate_sis_roster(ds, seed=7, params=params)

    # Deterministic: same dataset + seed ⇒ byte-identical roster.
    again = generate_sis_roster(ds, seed=7, params=params)
    assert [r.model_dump() for r in roster] == [r.model_dump() for r in again]

    paid = _paid_emails(ds)
    assert paid, "fixture precondition: the cohort must contain paid families"

    roster_emails = {r.match_attrs.email for r in roster}

    # 🔴 paid_not_in_sis — at least one paid family has NO roster row at all.
    absent = paid - roster_emails
    assert absent, "expected >=1 paid family absent from the SIS roster (🔴 seed)"

    # 🟡 records_lag — at least one paid family IS on the roster but the SIS row
    # has not caught up (not yet 'confirmed' / no confirmed_at).
    lagging = [
        r
        for r in roster
        if r.match_attrs.email in paid
        and (r.enrollment_status != "confirmed" or r.confirmed_at is None)
    ]
    assert lagging, "expected >=1 paid family present-but-lagging on the SIS roster (🟡 seed)"

    # ✅ confirmed — the MAJORITY of paid families are present and confirmed.
    confirmed = {
        r.match_attrs.email
        for r in roster
        if r.enrollment_status == "confirmed" and r.confirmed_at is not None
    }
    assert len(paid & confirmed) > len(paid) // 2, "expected most paid families confirmed (✅)"

    # INV-1: every roster contact stays in the synthetic sink.
    for r in roster:
        if r.match_attrs.email is not None:
            assert r.match_attrs.email.endswith("@example.invalid")
        if r.match_attrs.phone is not None:
            assert r.match_attrs.phone.startswith("555-01")
