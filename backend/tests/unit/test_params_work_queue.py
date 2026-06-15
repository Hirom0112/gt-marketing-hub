"""Work-queue params-block tests (S1; ARCHITECTURE.md §8, CLAUDE.md §4.1, INV-11).

The `work_queue` block (§8) is the single home for the scorer's tunables:
weights, recoverability sub-weights, value baseline/multiplier, and the stall
window. These tests pin every committed value read from the YAML so the build
fails the instant a param drifts (TDD strict, CLAUDE.md §4.1) — they assert the
*typed* `work_queue` accessor exposes exactly the §8 values, never a default.

Deterministic without a local `params/params.yaml` (gitignored, not created):
the committed `params/params.example.yaml` is passed explicitly.
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_work_queue_params_loaded() -> None:
    """The typed `work_queue` block exposes exactly the §8 values from the YAML.

    Asserts the headline weights, the three recoverability sub-weights, the
    value baseline + funded multiplier, and the stall window — each read from
    the committed example file. Any drift (renamed/retuned key) flips one of
    these assertions red (CLAUDE.md §4.1, INV-11).
    """
    work_queue = load_params(EXAMPLE_PARAMS).work_queue

    # Headline weights (§8): score = w_recoverability·recoverability + w_value·value.
    assert work_queue.w_recoverability == 0.6
    assert work_queue.w_value == 0.4

    # Recoverability sub-weights, each normalized into the [0,1] composite (§8).
    assert work_queue.recoverability.stall_recency_weight == 0.5
    assert work_queue.recoverability.stage_proximity_weight == 0.3
    assert work_queue.recoverability.responsiveness_weight == 0.2

    # Value baseline + funded weighting (§8); value_max derives from these (A-1).
    assert work_queue.value.tuition_annual_default == 10400
    assert work_queue.value.funded_multiplier == 1.0

    # Stall window in days (§5.1) — older than this ⇒ flagged.
    assert work_queue.stall_window_days == 14
