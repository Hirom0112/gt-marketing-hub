"""resilience params-block tests (A5; CLAUDE.md §4.1, INV-11).

The ``resilience`` block is the single home (INV-11) for the retry/backoff
tunables a retryable adapter call uses — total attempts and the exponential
backoff base/ceiling. ``load_params`` parses the block into the typed
:class:`Resilience` model; a retuned/out-of-range key fails the build. Each
value MUST be ``>= 1`` and the backoff ceiling MUST NOT sit below the base.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import Resilience, load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_resilience_block_loads() -> None:
    """The resilience block loads into a typed model with the A5 defaults."""
    resilience = load_params(EXAMPLE_PARAMS).resilience

    assert isinstance(resilience, Resilience)
    assert resilience.max_attempts == 3
    assert resilience.base_delay_ms == 200
    assert resilience.max_delay_ms == 5000

    # Drift guard: a non-positive attempt count is rejected.
    with pytest.raises(ValidationError):
        Resilience(max_attempts=0, base_delay_ms=200, max_delay_ms=5000)

    # Drift guard: a ceiling below the base is incoherent and rejected.
    with pytest.raises(ValidationError):
        Resilience(max_attempts=3, base_delay_ms=5000, max_delay_ms=200)
