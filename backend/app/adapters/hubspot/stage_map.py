"""Pure cockpit-Stage ↔ HubSpot-stage-id mapping (S10 W1; INV-11, INV-4 posture).

The cockpit's four-stage funnel (``interest → apply → enroll → tuition``,
``app.data.models.Stage``) and a real HubSpot pipeline's stage ids are bridged by
a table in ``params/params.yaml`` (``crm.stage_map``) that the provisioning
script (``scripts/provision_hubspot.py``) writes after relabelling the live
portal. Keeping the table in params (not in code) means a stage id has exactly
one home (INV-11) and the bridge is portable to GT's real pipeline by
re-provisioning — no code change.

This module is **pure**: it imports no HTTP client and does no I/O. It takes the
already-loaded :class:`app.core.params.Crm` block and converts in memory. Every
lookup **fails closed** (INV-4 posture): an unmapped cockpit stage or an unknown
HubSpot id raises :class:`StageMappingError` rather than guessing or silently
returning ``None`` — the live adapter must never push a deal to a wrong/missing
stage. The map covers the four funnel stages; ``crm.stage_map`` also carries a
``closed_lost`` entry for the adapter, but that is **not** a cockpit funnel
``Stage`` and so has no reverse mapping (its id reverse-maps to *fail-closed*).
"""

from __future__ import annotations

from app.core.params import Crm
from app.data.models import Stage


class StageMappingError(KeyError):
    """A cockpit stage or HubSpot stage id has no mapping in ``crm.stage_map``.

    Raised instead of returning a default so the CRM boundary fails closed
    (INV-4 posture): a missing mapping is a config error, not a silent fallback.
    """


def cockpit_stage_to_hubspot_id(stage: Stage, crm: Crm) -> str:
    """Return the HubSpot deal stage id for a cockpit ``Stage`` (reads params).

    Args:
        stage: A cockpit funnel stage.
        crm: The loaded ``crm`` params block (``load_params().crm``).

    Returns:
        The HubSpot stage id mapped in ``crm.stage_map`` for ``stage.value``.

    Raises:
        StageMappingError: if ``stage`` is absent from ``crm.stage_map`` — the
            map drifted from the cockpit enum; fail closed (INV-4).
    """
    try:
        return crm.stage_map[stage.value]
    except KeyError as exc:
        raise StageMappingError(
            f"cockpit stage {stage.value!r} is not in crm.stage_map "
            f"(have {sorted(crm.stage_map)}); fail-closed (INV-4, INV-11)"
        ) from exc


def hubspot_id_to_cockpit_stage(stage_id: str, crm: Crm) -> Stage:
    """Return the cockpit ``Stage`` for a HubSpot stage id (reverse of params map).

    Only the four funnel stages reverse-map; the ``closed_lost`` entry in
    ``crm.stage_map`` is a HubSpot-only terminal stage with no cockpit ``Stage``,
    so its id raises (it is not a funnel stage).

    Args:
        stage_id: A HubSpot deal stage id.
        crm: The loaded ``crm`` params block.

    Returns:
        The cockpit ``Stage`` whose ``crm.stage_map`` id equals ``stage_id``.

    Raises:
        StageMappingError: if no cockpit funnel stage maps to ``stage_id`` —
            fail closed (INV-4) rather than returning ``None``.
    """
    # Build the reverse index only from the four funnel stages, so a non-funnel
    # id (e.g. closed_lost) correctly fails closed.
    reverse: dict[str, Stage] = {}
    for stage in Stage:
        mapped = crm.stage_map.get(stage.value)
        if mapped is not None:
            reverse[mapped] = stage
    try:
        return reverse[stage_id]
    except KeyError as exc:
        raise StageMappingError(
            f"HubSpot stage id {stage_id!r} maps to no cockpit funnel Stage "
            f"(funnel ids {sorted(reverse)}); fail-closed (INV-4)"
        ) from exc
