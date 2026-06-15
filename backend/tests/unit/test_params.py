"""Params-loader tests (S0; ARCHITECTURE.md §8, CLAUDE.md INV-11, §4.1).

The params file is the single home for every magic number. `load_params`
parses it into typed Pydantic models; every consumer reads values from here,
never hardcoded. These tests assert the committed values come *from the YAML*
and that drift (missing key / wrong type) fails the build (TDD strict, §4.1).

The tests are deterministic without a local `params/params.yaml` (gitignored,
not created): they pass the committed `params/params.example.yaml` explicitly.
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import load_params

# The committed example file is the authoritative source for these tests.
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def test_loads_work_queue_and_funding_and_thresholds() -> None:
    """Typed params expose §8 values read from the YAML, not hardcoded."""
    params = load_params(EXAMPLE_PARAMS)

    assert params.work_queue.w_recoverability == 0.6
    assert params.funding.award_amounts.tefa_standard == 10474
    assert params.funding.installment_split == [0.25, 0.25, 0.50]
    assert params.eval_thresholds.message_safety_grounding.min_grounding == 0.95
