"""data_confidence params-block tests (A4; CLAUDE.md §4.1, INV-11).

The ``data_confidence`` block is the single home (INV-11) for the sync-parity
threshold below which the cross-module data-confidence banner activates: overall
parity < ``min_parity`` ⇒ banner on. ``load_params`` parses the block into the
typed :class:`DataConfidence` model; a retuned/out-of-range key fails the build.
``min_parity`` is a fraction, so it MUST sit in [0.0, 1.0].
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.params import DataConfidence, load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_threshold_loads() -> None:
    """The data_confidence block loads into a typed model with the A4 value."""
    data_confidence = load_params(EXAMPLE_PARAMS).data_confidence

    assert isinstance(data_confidence, DataConfidence)
    assert data_confidence.min_parity == 0.95

    # Drift guard: a parity above 1.0 is not a fraction and is rejected.
    with pytest.raises(ValidationError):
        DataConfidence(min_parity=1.5)

    # Drift guard: a negative parity is not a fraction and is rejected.
    with pytest.raises(ValidationError):
        DataConfidence(min_parity=-0.1)
