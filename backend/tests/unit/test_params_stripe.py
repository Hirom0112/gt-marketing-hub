"""stripe params-block tests (A3; RESEARCH_v2 §II.2; CLAUDE.md §4.1, INV-8/INV-11).

The ``stripe`` block is the single home (INV-11) for the Stripe payments seam's
tunables: the ``calls_per_run_cap`` per-run outbound API budget (INV-8 guard,
mirroring HubSpot's cap), the webhook signature ``tolerance_seconds`` (Stripe
default 300 = 5 min, RESEARCH_v2 §II.2), and the ``fulfill_event_types`` that
trigger fulfillment. ``load_params`` parses the block into the typed
:class:`Stripe` model; a renamed/retuned/out-of-range key fails the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import Stripe, load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_stripe_block_loads() -> None:
    """The stripe block loads into a typed Stripe model with the §II.2 values."""
    stripe = load_params(EXAMPLE_PARAMS).stripe

    assert isinstance(stripe, Stripe)
    assert stripe.calls_per_run_cap == 50
    assert stripe.tolerance_seconds == 300  # Stripe default 5-min signature tolerance
    assert stripe.fulfill_event_types == ["checkout.session.completed"]

    # Drift guard: a non-positive call cap is rejected.
    with pytest.raises(ValidationError):
        Stripe(
            calls_per_run_cap=0,
            tolerance_seconds=300,
            fulfill_event_types=["checkout.session.completed"],
        )

    # Drift guard: a non-positive tolerance is rejected.
    with pytest.raises(ValidationError):
        Stripe(
            calls_per_run_cap=50,
            tolerance_seconds=0,
            fulfill_event_types=["checkout.session.completed"],
        )

    # Drift guard: an empty fulfill_event_types list is rejected.
    with pytest.raises(ValidationError):
        Stripe(
            calls_per_run_cap=50,
            tolerance_seconds=300,
            fulfill_event_types=[],
        )
