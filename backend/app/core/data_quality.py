"""Auto data-quality queue (TODO_v2 §C1; CLAUDE.md INV-4/INV-11).

A pure deriver for the CRM-Ops data-quality layer: it sweeps a cohort of rows and
emits one :class:`DqIssue` per detected problem, returning ONLY the rows that
carry at least one issue, **severity-ordered** per
``params.crm_ops.data_quality.severity_order`` (``conflict`` highest). A clean
cohort yields an empty tuple.

Five issue kinds — the first three DETECTED by REUSING an existing pure core
(never re-implemented), the last two pure structural checks over the row's fields:

* ``conflict`` — the Supabase↔HubSpot seam diverges: ``seam.derive_seam_status``
  yields :data:`SeamStatus.CONFLICT` for the row's record + mirror.
* ``utm_broken`` — ``utm_health.check_utm`` flags the row's UTM ``broken``.
* ``unreliable_field`` — the row carries a present field listed in
  ``params.crm_ops.unreliable_fields`` (a known low-trust value).
* ``mojibake`` — a synthetic ASCII text field carries the double-encoded-UTF-8
  signature (U+00C3 / U+00C2) or any non-ASCII char — a CRM-import corruption
  (e.g. ``"JosÃ© RodrÃ­guez"`` for ``"José Rodríguez"``).
* ``missing_field`` — a required field is empty/``None`` (a blank an import
  silently dropped).

Per the honesty mandate (mirroring INV-4) this DETECTS and FLAGS — nothing is
auto-corrected. Pure: stdlib + ``app.core.params`` + the reused pure cores
(``seam``, ``utm_health``) only — no I/O, no adapters, no LLM.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.core.params import Params
from app.core.seam import MirrorState, derive_seam_status
from app.core.utm_health import check_utm
from app.data.models import FamilyRecord, SeamStatus

DqKind = Literal["conflict", "utm_broken", "unreliable_field", "mojibake", "missing_field"]

# The double-encoded-UTF-8 mojibake signature: the high-Latin-1 lead bytes a UTF-8
# string mis-decoded as Latin-1 produces (U+00C3 / U+00C2). Their presence — or ANY
# non-ASCII char in a field meant to be plain synthetic ASCII — means the text was
# corrupted on a CRM import. Pure stdlib ``str`` only; a single canonical home.
_MOJIBAKE_SIGNATURE: tuple[str, ...] = ("Ã", "Â")


def _has_mojibake(value: str) -> bool:
    """True when ``value`` carries the mojibake signature or any non-ASCII char.

    A field documented as plain synthetic ASCII (a household label / contact name)
    should never hold a high-codepoint char; one present is the double-encoded
    corruption this flags. ``value.isascii()`` is the general catch; the explicit
    signature documents the canonical ``Ã``/``Â`` lead-byte tell.
    """
    return any(sig in value for sig in _MOJIBAKE_SIGNATURE) or not value.isascii()


def _is_missing(value: str | None) -> bool:
    """True when a required field is ``None`` or empty/whitespace-only."""
    return value is None or not value.strip()


@dataclass(frozen=True, slots=True)
class DqRow:
    """One cohort row carrying enough to derive its data-quality issues (§C1).

    Attributes:
        entity_id: The stable id this row's issues are attributed to.
        record: The family record whose seam status is derived against ``mirror``.
        mirror: The simulated HubSpot mirror's view of this row (seam input).
        utm: The row's campaign UTM parameters, or ``None`` (UTM-health input).
        present_fields: The field names actually present/populated on this row;
            any that are low-trust (``params.crm_ops.unreliable_fields``) raise an
            ``unreliable_field`` issue. Defaults to empty.
        mojibake_fields: ``{field_name: value}`` of synthetic ASCII text fields
            (e.g. a contact name / household label) to check for the
            double-encoded-UTF-8 corruption — any flagged by :func:`_has_mojibake`
            raise a ``mojibake`` issue. Defaults to empty (no check).
        required_fields: ``{field_name: value}`` of fields that MUST be populated;
            any empty/``None`` (per :func:`_is_missing`) raise a ``missing_field``
            issue. Defaults to empty (no check).
    """

    entity_id: str
    record: FamilyRecord
    mirror: MirrorState
    utm: Mapping[str, str] | None = None
    present_fields: tuple[str, ...] = field(default_factory=tuple)
    mojibake_fields: Mapping[str, str | None] = field(default_factory=dict)
    required_fields: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DqIssue:
    """One detected data-quality problem (§C1).

    Frozen/derived: an immutable detection artifact, never a state write.

    Attributes:
        entity_id: The row the problem belongs to.
        kind: The issue kind (one of the known kinds).
        severity: The rank from ``severity_order`` — the index of ``kind`` in the
            params list, so a LOWER value is HIGHER severity (``conflict`` = 0).
            Sorting issues by this ascending yields the severity order.
        detail: A human-readable description of the detected problem.
    """

    entity_id: str
    kind: DqKind
    severity: int
    detail: str


def build_dq_queue(rows: Sequence[DqRow], *, params: Params) -> tuple[DqIssue, ...]:
    """Detect every data-quality issue across ``rows``, severity-ordered (§C1).

    For each row, REUSES the existing pure cores to detect issues:
    ``seam.derive_seam_status`` (a ``CONFLICT`` ⇒ ``conflict`` issue),
    ``utm_health.check_utm`` (``broken`` ⇒ ``utm_broken`` issue), and the
    ``params.crm_ops.unreliable_fields`` membership of each ``present_fields``
    entry (⇒ ``unreliable_field`` issue). Returns ONLY rows with ≥1 issue, sorted
    by severity (``params.crm_ops.data_quality.severity_order``, ``conflict``
    highest); a clean cohort ⇒ empty tuple.

    Nothing is auto-corrected — issues are DETECTED and FLAGGED (mirroring INV-4).

    Args:
        rows: The cohort to sweep.
        params: The loaded params; ``crm_ops`` supplies the severity order +
            unreliable-field list, and the UTM rule set the reused
            ``check_utm`` reads.

    Returns:
        The detected issues, severity-ordered (highest first). Stable within a
        severity rank (row order, then conflict → utm_broken → unreliable_field).
    """
    order = params.crm_ops.data_quality.severity_order
    rank = {kind: index for index, kind in enumerate(order)}
    unreliable = set(params.crm_ops.unreliable_fields)

    issues: list[DqIssue] = []
    for row in rows:
        if derive_seam_status(row.record, row.mirror) is SeamStatus.CONFLICT:
            issues.append(
                DqIssue(
                    entity_id=row.entity_id,
                    kind="conflict",
                    severity=rank["conflict"],
                    detail="Supabase↔HubSpot seam diverges — needs a reconcile decision.",
                )
            )

        health = check_utm(row.utm, params=params)
        if health.status == "broken":
            issues.append(
                DqIssue(
                    entity_id=row.entity_id,
                    kind="utm_broken",
                    severity=rank["utm_broken"],
                    detail="Broken UTM: " + "; ".join(health.reasons),
                )
            )

        for field_name in row.present_fields:
            if field_name in unreliable:
                issues.append(
                    DqIssue(
                        entity_id=row.entity_id,
                        kind="unreliable_field",
                        severity=rank["unreliable_field"],
                        detail=f"Low-trust field {field_name!r} present — value is unreliable.",
                    )
                )

        for field_name, value in row.mojibake_fields.items():
            if value is not None and _has_mojibake(value):
                issues.append(
                    DqIssue(
                        entity_id=row.entity_id,
                        kind="mojibake",
                        severity=rank["mojibake"],
                        detail=(
                            f"Field {field_name!r} carries mojibake "
                            f"(double-encoded text) — {value!r}."
                        ),
                    )
                )

        for field_name, value in row.required_fields.items():
            if _is_missing(value):
                issues.append(
                    DqIssue(
                        entity_id=row.entity_id,
                        kind="missing_field",
                        severity=rank["missing_field"],
                        detail=f"Required field {field_name!r} is empty/missing.",
                    )
                )

    # Stable sort by severity rank: conflict (highest) first. Equal-rank issues
    # keep their detection order (row order, then per-row kind order above:
    # conflict → utm_broken → unreliable_field → mojibake → missing_field).
    issues.sort(key=lambda issue: issue.severity)
    return tuple(issues)
