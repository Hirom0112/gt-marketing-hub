"""Field-reliability flag tests (TODO_v2 §C1; CLAUDE.md INV-11).

A simple, params-driven honesty flag: the cockpit marks a low-trust field
``unreliable`` (with a reason) so a fragile value is visible rather than
silently trusted. A field is ``unreliable`` iff it is listed in
``params.crm_ops.unreliable_fields``, else ``reliable``.

Pure unit: no I/O, no adapters, no LLM — only the deriver + params.
"""

from __future__ import annotations

from pathlib import Path

from app.core.field_reliability import FieldReliability, field_flag
from app.core.params import load_params

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
_PARAMS = load_params(EXAMPLE_PARAMS)


def test_known_low_trust_field_is_unreliable() -> None:
    """A field listed in params.crm_ops.unreliable_fields ⇒ ``unreliable`` + reason."""
    assert "tefa_amount" in _PARAMS.crm_ops.unreliable_fields  # read from params

    flag = field_flag("tefa_amount", params=_PARAMS)

    assert isinstance(flag, FieldReliability)
    assert flag.field == "tefa_amount"
    assert flag.status == "unreliable"
    assert flag.reason  # a non-empty human reason.


def test_income_tier_is_unreliable() -> None:
    """The self-reported income BUCKET is low-trust ⇒ ``unreliable`` (C1, INV-1).

    Locks the corrected semantics: ``unreliable_fields`` names real low-trust
    model fields (``income_tier``/``attribution_source``), NOT the forbidden raw
    ``household_income`` PII token the PII-scan gate blocks.
    """
    assert "income_tier" in _PARAMS.crm_ops.unreliable_fields  # read from params

    flag = field_flag("income_tier", params=_PARAMS)

    assert flag.field == "income_tier"
    assert flag.status == "unreliable"
    assert flag.reason  # a non-empty human reason.


def test_normal_field_is_reliable() -> None:
    """A field NOT in the low-trust list ⇒ ``reliable`` with no reason."""
    assert "display_name" not in _PARAMS.crm_ops.unreliable_fields

    flag = field_flag("display_name", params=_PARAMS)

    assert flag.field == "display_name"
    assert flag.status == "reliable"
    assert flag.reason is None
