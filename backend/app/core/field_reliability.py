"""Field-reliability flag (TODO_v2 §C1; CLAUDE.md INV-11).

A small, params-driven honesty flag for the CRM-Ops data-quality layer: the
cockpit honestly marks a known low-trust field ``unreliable`` (with a reason) so a
fragile value is visible rather than silently trusted. A field is ``unreliable``
iff it is listed in ``params.crm_ops.unreliable_fields`` (INV-11), else
``reliable``.

Pure: stdlib + ``app.core.params`` only — no I/O, no adapters, no LLM (the
core-purity test guards this).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.params import Params

FieldStatus = Literal["reliable", "unreliable"]


@dataclass(frozen=True, slots=True)
class FieldReliability:
    """A field's reliability verdict (TODO_v2 §C1).

    Frozen/derived: an immutable detection artifact, never a state write.

    Attributes:
        field: The field name evaluated.
        status: ``unreliable`` when the field is a known low-trust field, else
            ``reliable``.
        reason: A human-readable reason when ``unreliable``; ``None`` when
            ``reliable``.
    """

    field: str
    status: FieldStatus
    reason: str | None


def field_flag(field_name: str, *, params: Params) -> FieldReliability:
    """Flag ``field_name`` ``reliable`` / ``unreliable`` from params (TODO_v2 §C1).

    ``unreliable`` (with a reason) iff ``field_name`` is in
    ``params.crm_ops.unreliable_fields`` (INV-11), else ``reliable``.

    Args:
        field_name: The field to evaluate.
        params: The loaded params; ``crm_ops.unreliable_fields`` is the low-trust
            list.

    Returns:
        The :class:`FieldReliability` verdict.
    """
    if field_name in params.crm_ops.unreliable_fields:
        return FieldReliability(
            field=field_name,
            status="unreliable",
            reason=f"{field_name!r} is a known low-trust field — value flagged unreliable.",
        )
    return FieldReliability(field=field_name, status="reliable", reason=None)
