"""Pure cockpit-Stage ↔ HubSpot-stage-id mapping (S10 W1; INV-11, INV-4 posture).

The map reads ``crm.stage_map`` from ``params/params.yaml`` (INV-11 — a stage id
lives in exactly one place, the params file the provisioning script writes). The
helper is **pure** (no I/O): it takes the loaded ``Params`` and a ``Stage`` and
returns the HubSpot stage id, raising on anything unmapped so the boundary fails
**closed** (INV-4 posture — never silently push a deal to a missing/guessed
stage). These are the §4.1 red→green tests for that helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.hubspot.stage_map import (
    StageMappingError,
    cockpit_stage_to_hubspot_id,
    hubspot_id_to_cockpit_stage,
)
from app.core.params import Crm, CrmGtProperties, load_params
from app.data.models import Stage

# The committed example params (schema-complete) — passed explicitly so the
# test is cwd-independent, mirroring the other params-reading tests.
_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _params_crm() -> Crm:
    return load_params(_EXAMPLE_PARAMS).crm


def test_all_four_stages_map_to_a_hubspot_id() -> None:
    """Every cockpit funnel stage resolves to a non-empty HubSpot stage id."""
    crm = _params_crm()
    for stage in Stage:
        stage_id = cockpit_stage_to_hubspot_id(stage, crm)
        assert isinstance(stage_id, str) and stage_id, f"{stage} → empty id"


def test_round_trip_for_all_four_stages() -> None:
    """Cockpit stage → HubSpot id → cockpit stage returns the original (lossless)."""
    crm = _params_crm()
    for stage in Stage:
        stage_id = cockpit_stage_to_hubspot_id(stage, crm)
        assert hubspot_id_to_cockpit_stage(stage_id, crm) is stage


def test_unmapped_stage_raises_fail_closed() -> None:
    """A stage absent from params raises — never a silent default (INV-4 posture)."""
    crm = _params_crm()
    incomplete = crm.model_copy(
        update={"stage_map": {k: v for k, v in crm.stage_map.items() if k != "tuition"}}
    )
    with pytest.raises(StageMappingError):
        cockpit_stage_to_hubspot_id(Stage.TUITION, incomplete)


def test_unknown_hubspot_id_raises_fail_closed() -> None:
    """An id with no cockpit stage raises rather than returning None (fail-closed)."""
    crm = _params_crm()
    with pytest.raises(StageMappingError):
        hubspot_id_to_cockpit_stage("not-a-real-stage-id", crm)


def test_helper_reads_params_not_hardcoded_ids() -> None:
    """Drift a param id and the helper must follow it (proves it reads params)."""
    crm = _params_crm()
    drifted = crm.model_copy(update={"stage_map": {**crm.stage_map, "interest": "999999"}})
    assert cockpit_stage_to_hubspot_id(Stage.INTEREST, drifted) == "999999"
    assert hubspot_id_to_cockpit_stage("999999", drifted) is Stage.INTEREST


def test_closed_lost_is_not_a_cockpit_funnel_stage() -> None:
    """closed_lost is mapped for the adapter but is not in the 4-stage funnel enum.

    The four cockpit ``Stage`` values map; ``closed_lost`` is an extra HubSpot
    terminal stage kept in params for the adapter, with no cockpit ``Stage``.
    """
    crm = _params_crm()
    assert "closed_lost" in crm.stage_map
    assert set(crm.stage_map) - {"closed_lost"} == {s.value for s in Stage}
    # Its id reverse-maps to None-of-the-enum ⇒ fail-closed, not a funnel stage.
    with pytest.raises(StageMappingError):
        hubspot_id_to_cockpit_stage(crm.stage_map["closed_lost"], crm)


def test_crm_gt_properties_shape() -> None:
    """The gt_* property names the adapter consumes are present in params (INV-11)."""
    crm = _params_crm()
    assert isinstance(crm.gt_properties, CrmGtProperties)
    assert "gt_synthetic_id" in crm.gt_properties.deal
    assert "gt_synthetic_id" in crm.gt_properties.contact
