"""Auto data-quality queue tests (TODO_v2 §C1; CLAUDE.md INV-4/INV-11).

``build_dq_queue`` derives, per row, a ``conflict`` issue (reusing
``seam.derive_seam_status``), a ``utm_broken`` issue (reusing
``utm_health.check_utm``), and an ``unreliable_field`` issue (a low-trust field
present), returning ONLY rows with ≥1 issue, severity-ordered per
``params.crm_ops.data_quality.severity_order`` (conflict highest). It DETECTS
and FLAGS — nothing is auto-corrected (the honesty mandate, INV-4).

Pure unit: no I/O, no adapters, no LLM — the deriver + params + the reused
pure cores (seam, utm_health).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.data_quality import DqIssue, DqRow, build_dq_queue
from app.core.params import load_params
from app.core.seam import MirrorState
from app.data.models import FamilyRecord, Stage

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
_PARAMS = load_params(EXAMPLE_PARAMS)

# A fixed clock so seam derivation is exact and reproducible (cf. test_seam.py).
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_BEFORE = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
_AFTER = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)

_GOOD_UTM = {
    "utm_source": "newsletter",
    "utm_medium": "email",
    "utm_campaign": "spring_open_house",
}
_BROKEN_UTM = {"utm_source": "newsletter", "utm_medium": "email"}  # missing utm_campaign


def _record(*, current_stage: Stage = Stage.APPLY) -> FamilyRecord:
    """A FamilyRecord seeded with just the seam-relevant columns."""
    return FamilyRecord(
        family_id=uuid4(),
        display_name="The Rivera Family",
        primary_contact_synthetic_email="rivera.synthetic@example.invalid",
        current_stage=current_stage,
        attribution_source="referral",
        attribution_utm={"utm_source": "newsletter"},
        updated_at=_T0,
        crm_synced_at=_AFTER,  # crm fresh ⇒ synced baseline (mirror permitting).
    )


def _synced_mirror() -> MirrorState:
    """A mirror that agrees on the tracked field and is fresh ⇒ synced seam."""
    return MirrorState(stage=Stage.APPLY, mirror_updated_at=_AFTER)


def _conflict_mirror() -> MirrorState:
    """A mirror diverging on stage with equal recency ⇒ conflict seam (§4.7)."""
    return MirrorState(stage=Stage.ENROLL, mirror_updated_at=_T0)


def test_only_rows_with_issues_returned_and_severity_ordered() -> None:
    """A 4-row cohort with exactly 1 conflict + 1 broken-UTM ⇒ those 2 issues only.

    The conflict issue is ordered BEFORE the utm_broken issue (severity, conflict
    highest), independent of row order.
    """
    # Row order deliberately puts the broken-UTM row BEFORE the conflict row so
    # the ordering proof is about severity, not insertion order.
    rows = [
        DqRow(entity_id="clean-1", record=_record(), mirror=_synced_mirror(), utm=_GOOD_UTM),
        DqRow(entity_id="utm-bad", record=_record(), mirror=_synced_mirror(), utm=_BROKEN_UTM),
        DqRow(entity_id="conflict", record=_record(), mirror=_conflict_mirror(), utm=_GOOD_UTM),
        DqRow(entity_id="clean-2", record=_record(), mirror=_synced_mirror(), utm=_GOOD_UTM),
    ]

    issues = build_dq_queue(rows, params=_PARAMS)

    assert all(isinstance(i, DqIssue) for i in issues)
    # Exactly the two problem rows surface, conflict first (severity ordering).
    assert [(i.entity_id, i.kind) for i in issues] == [
        ("conflict", "conflict"),
        ("utm-bad", "utm_broken"),
    ]
    # The severity rank proves the order (conflict strictly higher than utm_broken).
    assert issues[0].severity < issues[1].severity


def test_unreliable_field_present_is_flagged() -> None:
    """A row carrying a present low-trust field ⇒ an ``unreliable_field`` issue."""
    unreliable = _PARAMS.crm_ops.unreliable_fields[0]
    rows = [
        DqRow(
            entity_id="leaky",
            record=_record(),
            mirror=_synced_mirror(),
            utm=_GOOD_UTM,
            present_fields=(unreliable,),
        )
    ]

    issues = build_dq_queue(rows, params=_PARAMS)

    assert [(i.entity_id, i.kind) for i in issues] == [("leaky", "unreliable_field")]


def test_clean_cohort_is_empty() -> None:
    """Every row clean (synced seam + ok UTM + no low-trust field) ⇒ empty tuple."""
    rows = [
        DqRow(entity_id=f"clean-{n}", record=_record(), mirror=_synced_mirror(), utm=_GOOD_UTM)
        for n in range(3)
    ]

    assert build_dq_queue(rows, params=_PARAMS) == ()


# --------------------------------------------------------------------------- #
# mojibake — double-encoded-UTF-8 corruption in a synthetic ASCII text field.
# --------------------------------------------------------------------------- #
def test_mojibake_clean_field_is_not_flagged() -> None:
    """A row whose ``mojibake_fields`` are all plain ASCII ⇒ NO mojibake issue."""
    rows = [
        DqRow(
            entity_id="clean-name",
            record=_record(),
            mirror=_synced_mirror(),
            utm=_GOOD_UTM,
            mojibake_fields={"first_name": "Jose", "last_name": "Rodriguez"},
        )
    ]
    assert build_dq_queue(rows, params=_PARAMS) == ()


def test_mojibake_double_encoded_field_is_flagged() -> None:
    """A field carrying the double-encoded-UTF-8 signature ⇒ a ``mojibake`` issue.

    ``"JosÃ©"`` / ``"RodrÃ­guez"`` are "José" / "Rodríguez" UTF-8 bytes mis-decoded
    as Latin-1 — the canonical CRM-import corruption (U+00C3 lead byte).
    """
    rows = [
        DqRow(
            entity_id="garbled",
            record=_record(),
            mirror=_synced_mirror(),
            utm=_GOOD_UTM,
            mojibake_fields={"first_name": "JosÃ©", "last_name": "RodrÃ­guez"},
        )
    ]
    issues = build_dq_queue(rows, params=_PARAMS)
    kinds = [(i.entity_id, i.kind) for i in issues]
    # Both mojibake fields are flagged, attributed to the row.
    assert kinds == [("garbled", "mojibake"), ("garbled", "mojibake")]
    expected_rank = _PARAMS.crm_ops.data_quality.severity_order.index("mojibake")
    assert all(i.severity == expected_rank for i in issues)


# --------------------------------------------------------------------------- #
# missing_field — a required field is empty / None.
# --------------------------------------------------------------------------- #
def test_missing_field_present_value_is_not_flagged() -> None:
    """A row whose required fields are all populated ⇒ NO missing_field issue."""
    rows = [
        DqRow(
            entity_id="complete",
            record=_record(),
            mirror=_synced_mirror(),
            utm=_GOOD_UTM,
            required_fields={"region": "Southeast", "phone": "555-0100"},
        )
    ]
    assert build_dq_queue(rows, params=_PARAMS) == ()


def test_missing_field_empty_or_none_is_flagged() -> None:
    """An empty/whitespace-only or ``None`` required field ⇒ a ``missing_field`` issue."""
    rows = [
        DqRow(
            entity_id="blank-region",
            record=_record(),
            mirror=_synced_mirror(),
            utm=_GOOD_UTM,
            required_fields={"region": "", "phone": None},
        )
    ]
    issues = build_dq_queue(rows, params=_PARAMS)
    assert [(i.entity_id, i.kind) for i in issues] == [
        ("blank-region", "missing_field"),
        ("blank-region", "missing_field"),
    ]
    expected_rank = _PARAMS.crm_ops.data_quality.severity_order.index("missing_field")
    assert all(i.severity == expected_rank for i in issues)
