"""Production HubSpot CRM adapter — pushes SYNTHETIC data live (S10 W2; INV-1/2/8/9).

This is the **Production** half of the §7.1 ``CRMAdapter`` seam. It pushes
synthetic family records into the real HubSpot portal over the CRM v3 API, behind
the **four guards** that make synthetic→live safe (``ANALYSIS/hubspot-complement-plan.md``
§3). The simulated impl (:class:`app.adapters.hubspot.crm_adapter.SimulatedCRMAdapter`)
stays the v1 default; this one is selected only when ``CRM_MODE=live`` with a token
and no kill switch (see :mod:`app.adapters.registry`). ``core/`` and ``ai/`` change
zero lines — they depend on the :class:`CRMAdapter` interface, not this class.

The four guards (each has a passing AND a blocking test):

1. **Synthetic write-lock (INV-1).** Before ANY write, the contact email's domain
   MUST be in ``crm.synthetic_email_domains`` and NOT in ``crm.real_domain_denylist``,
   else :class:`SyntheticWriteLockError`. The upsert idempotency key is
   ``gt_synthetic_id = str(family_id)`` — **never email** — so an email collision
   with a real contact is structurally impossible.
2. **Inbound PII firewall (INV-1).** ``read_mirror`` reads ONLY the deal's stage +
   timestamp; it never reads/returns/persists a contact name/phone/real email. The
   returned :class:`MirrorState` carries only ``stage`` + ``mirror_updated_at``.
3. **Cap + kill-switch (INV-8).** A per-run HubSpot call budget; the (cap+1)th call
   raises :class:`HubSpotBudgetExceededError`. The env kill switch degrades the
   registry to the simulated adapter (handled in the registry, not here).
4. **Approval-gate (INV-2).** Only the deterministic post-decision path constructs
   this adapter; nothing under ``app/ai`` imports it (asserted by a test import walk).

Tests run against a ``httpx.MockTransport`` — no real network, no live write (the
real push lands in W3). The HTTP client is **injected** so the adapter never opens
a socket in a test.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from app.adapters.hubspot.crm_adapter import (
    CRMAdapter,
    SendResult,
    SyncResult,
    is_mirrorable,
)
from app.adapters.hubspot.stage_map import (
    StageMappingError,
    cockpit_stage_to_hubspot_id,
    hubspot_id_to_cockpit_stage,
)
from app.core.funding_gate import award_for_tier
from app.core.params import AwardAmounts, Crm
from app.core.seam import MirrorState
from app.data.models import FamilyRecord
from app.marketing.schemas.publish import PlatformDispatch, PublishRequest

logger = logging.getLogger(__name__)

# HubSpot CRM v3 object paths (the live API surface, not a tunable — these are the
# API's own URLs, INV-11 does not apply to a third party's fixed routes).
_CONTACTS = "/crm/v3/objects/contacts"
_DEALS = "/crm/v3/objects/deals"
_NOTES = "/crm/v3/objects/notes"
# The idempotency property — the upsert key (guard 1). NEVER email.
_GT_SYNTHETIC_ID = "gt_synthetic_id"


class SyntheticWriteLockError(RuntimeError):
    """Guard 1 (INV-1): a write was attempted for a non-synthetic contact email.

    Raised before any HubSpot write when the email's domain is not in
    ``crm.synthetic_email_domains`` or is in ``crm.real_domain_denylist`` — a real
    contact (e.g. one on a denylisted vendor domain) can never be written or merged.
    """


class HubSpotBudgetExceededError(RuntimeError):
    """Guard 3 (INV-8): the per-run HubSpot call budget was exhausted.

    The account-shared quota means overuse DoSes GT's real automation, so a breach
    fails closed here rather than silently overspending. The registry's kill switch
    is the coarser sibling (degrade to simulated); this is the per-run ceiling.
    """


class LiveHubSpotCRMAdapter(CRMAdapter):
    """Production ``CRMAdapter`` — live HubSpot writes of synthetic data (S10 W2).

    Args:
        client: An injected ``httpx.Client`` (tests pass one wired to a
            ``MockTransport``). Its ``base_url`` should be ``https://api.hubapi.com``.
        token: The HubSpot Private App token (Bearer auth).
        crm: The loaded ``crm`` params block — the stage map, the write-lock
            allow/deny lists, and the ``gt_*`` property names (INV-11).
        award_amounts: The ``funding.award_amounts`` params block — the per-tier
            TEFA award the deal mirrors onto the HubSpot standard ``amount``
            property (INV-11; the number flows from the funding tier, never a
            literal here).
        calls_per_run_cap: The guard-3 per-run HubSpot call budget (INV-8).
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        token: str,
        crm: Crm,
        award_amounts: AwardAmounts,
        calls_per_run_cap: int,
    ) -> None:
        self._client = client
        self._crm = crm
        self._award_amounts = award_amounts
        self._cap = calls_per_run_cap
        self._calls_made = 0
        # Default Bearer auth on every request; explicit per-call headers merge.
        self._client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------ I/O
    def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        """One budgeted HubSpot call — guard 3 (INV-8) trips on the (cap+1)th.

        The budget is checked BEFORE the call, so an exhausted budget never reaches
        the network (fail closed). A non-2xx response raises via ``raise_for_status``.
        """
        if self._calls_made >= self._cap:
            raise HubSpotBudgetExceededError(
                f"HubSpot per-run call budget exhausted ({self._cap}); "
                f"degrade to simulated (INV-8) rather than overspend the shared quota."
            )
        self._calls_made += 1
        response = self._client.request(method, path, json=json)
        response.raise_for_status()
        return response

    # ------------------------------------------------------------- guard 1
    def _assert_synthetic(self, email: str) -> None:
        """Guard 1 (INV-1): block any write of a non-synthetic contact email.

        Domain must be allowlisted AND not denylisted; otherwise fail closed. This
        runs before the upsert, so a real contact never reaches a HubSpot write.
        """
        domain = email.rsplit("@", 1)[-1].strip().lower()
        denylist = {d.strip().lower() for d in self._crm.real_domain_denylist}
        allowlist = {d.strip().lower() for d in self._crm.synthetic_email_domains}
        if domain in denylist or domain not in allowlist:
            raise SyntheticWriteLockError(
                f"refusing to write a non-synthetic contact: domain {domain!r} is not "
                f"in synthetic_email_domains (or is denylisted). Only synthetic data "
                f"crosses the seam (INV-1, guard 1)."
            )

    # ----------------------------------------------------------- search/upsert
    def _search_by_gt_id(
        self, object_path: str, gt_id: str, properties: list[str]
    ) -> dict[str, Any] | None:
        """Search one object type by ``gt_synthetic_id`` (the upsert key; guard 1).

        Returns the first matching object (``{"id", "properties"}``) or ``None``.
        The filter keys on ``gt_synthetic_id`` ONLY — never email — so the upsert
        can't collide with a real contact.
        """
        payload = {
            "filterGroups": [
                {"filters": [{"propertyName": _GT_SYNTHETIC_ID, "operator": "EQ", "value": gt_id}]}
            ],
            "properties": properties,
            "limit": 1,
        }
        body = self._request("POST", f"{object_path}/search", json=payload).json()
        results = body.get("results") or []
        if not results:
            return None
        first: dict[str, Any] = results[0]
        return first

    def _upsert(self, object_path: str, gt_id: str, properties: dict[str, Any]) -> str:
        """Create-or-patch one object keyed by ``gt_synthetic_id``; return its id."""
        existing = self._search_by_gt_id(object_path, gt_id, [_GT_SYNTHETIC_ID])
        if existing is not None:
            obj_id = str(existing["id"])
            self._request("PATCH", f"{object_path}/{obj_id}", json={"properties": properties})
            return obj_id
        created = self._request("POST", object_path, json={"properties": properties}).json()
        return str(created["id"])

    def _associate(self, from_path: str, from_id: str, to_object: str, to_id: str) -> None:
        """Ensure a DEFAULT association exists between two objects (idempotent).

        Uses the CRM **v4** default-association endpoint
        (``/crm/v4/objects/{from}/{id}/associations/default/{to}/{id}``), which
        creates the HubSpot-defined default labels (e.g. deal↔contact) without a
        caller-supplied association type id. The v3 PUT without a type id 404s on
        the live portal, so v4-default is the correct surface. ``from_path`` is a
        v3 object path (``/crm/v3/objects/{type}``); we lift the object type off
        its tail to build the v4 URL so the call sites stay unchanged.
        """
        from_type = from_path.rsplit("/", 1)[-1]
        path = f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_object}/{to_id}"
        self._request("PUT", path)

    # --------------------------------------------------------- property builders
    def _contact_properties(self, record: FamilyRecord) -> dict[str, Any]:
        """The contact identity + ``gt_synthetic_id`` (the upsert key, not email)."""
        props: dict[str, Any] = {
            _GT_SYNTHETIC_ID: str(record.family_id),
            "email": record.primary_contact_synthetic_email,
        }
        # Only push gt_* contact props the params block declares (INV-11).
        return props

    def _deal_properties(self, record: FamilyRecord) -> dict[str, Any]:
        """The deal: mapped ``dealstage``, TEFA ``amount``, and the declared gt_* props."""
        props: dict[str, Any] = {
            _GT_SYNTHETIC_ID: str(record.family_id),
            "dealstage": cockpit_stage_to_hubspot_id(record.current_stage, self._crm),
            "dealname": record.display_name,
        }
        # TEFA award → HubSpot standard `amount` (INV-11: the number flows from the
        # family's funding tier via the shared award helper, never a literal). A
        # non-TEFA tier (self_pay) or an unset tier has no award — skip the prop
        # (no `amount=0` write) rather than fabricate one. Serialized as a plain
        # decimal string, the form HubSpot's `amount` (number) accepts.
        if record.funding_type is not None:
            try:
                props["amount"] = str(award_for_tier(record.funding_type, self._award_amounts))
            except ValueError:
                pass  # non-TEFA tier (e.g. self_pay) — no award to mirror.
        # gt_* deal props, each gated on the params declaration (INV-11) and on the
        # record actually carrying the value (None ⇒ skip, no empty writes).
        declared = set(self._crm.gt_properties.deal)
        if "gt_funding_state" in declared and record.funding_state is not None:
            props["gt_funding_state"] = record.funding_state.value
        if "gt_stall_reason" in declared and record.stall_reason is not None:
            props["gt_stall_reason"] = record.stall_reason.value
        if "gt_priority" in declared and record.work_queue_score is not None:
            props["gt_priority"] = record.work_queue_score
        return props

    # --------------------------------------------------------------- interface
    def push_family(self, family_record: FamilyRecord) -> SyncResult:
        """Upsert Contact+Deal (by ``gt_synthetic_id``) and associate them (§7.1).

        Guard 1 runs first: a non-synthetic email blocks BEFORE any write. The
        idempotency key is ``gt_synthetic_id``, so re-pushing patches rather than
        duplicating. Returns the live deal id as ``recorded_id``.
        """
        # Guard 1 (INV-1) — fail closed before any network write.
        self._assert_synthetic(family_record.primary_contact_synthetic_email)

        gt_id = str(family_record.family_id)
        contact_id = self._upsert(_CONTACTS, gt_id, self._contact_properties(family_record))
        deal_id = self._upsert(_DEALS, gt_id, self._deal_properties(family_record))
        self._associate(_DEALS, deal_id, "contacts", contact_id)

        return SyncResult(
            simulated=False,
            recorded_id=deal_id,
            contact_id=contact_id,
            family_id=family_record.family_id,
            stage=family_record.current_stage,
        )

    def read_mirror(self, family_id: UUID) -> MirrorState:
        """Read ONLY the deal's stage + timestamp (guard 2 — inbound PII firewall).

        Searches the deal by ``gt_synthetic_id`` and reads ``dealstage`` +
        ``hs_lastmodifieddate``. A contact name/phone/real email is NEVER read,
        returned, or logged — :class:`MirrorState` structurally carries only the
        stage and timestamp. An unmapped/legacy stage id is caught
        (:class:`StageMappingError`) and surfaced as a divergence-shaped mirror so
        the §4.7 deriver flags a conflict rather than the adapter crashing.
        """
        gt_id = str(family_id)
        # Read ONLY stage + timestamp — never a contact/identity property (guard 2).
        match = self._search_by_gt_id(
            _DEALS, gt_id, ["dealstage", "hs_lastmodifieddate", _GT_SYNTHETIC_ID]
        )
        if match is None:
            return MirrorState(stage=None, mirror_updated_at=None)

        # Pull ONLY the two safe scalars off the payload — never the whole dict, so
        # any stray contact PII the portal returned never enters app memory/logs.
        properties = match.get("properties", {})
        stage_id = properties.get("dealstage")
        mirror_updated_at = _parse_hs_timestamp(properties.get("hs_lastmodifieddate"))

        if not stage_id:
            return MirrorState(stage=None, mirror_updated_at=mirror_updated_at)

        try:
            stage = hubspot_id_to_cockpit_stage(str(stage_id), self._crm)
        except StageMappingError:
            # Legacy/unmapped stage (e.g. a leftover non-funnel stage). Surface a
            # divergence: stage=None reads as "unsynced/diverged" to the §4.7
            # deriver — never crash out of read_mirror (fail closed, don't raise).
            logger.warning(
                "read_mirror: deal holds an unmapped HubSpot stage id; "
                "surfacing as divergence (no crash, no PII)."
            )
            return MirrorState(stage=None, mirror_updated_at=mirror_updated_at)

        return MirrorState(stage=stage, mirror_updated_at=mirror_updated_at)

    def send_message(self, message: dict[str, Any]) -> SendResult:
        """Create a Note (``hs_note_body`` + ``hs_timestamp``) and associate it (§7.1).

        Associates the note to the contact and/or deal. The ids may be supplied
        directly (``contact_id`` / ``deal_id``), OR resolved from a ``family_id``
        by ``gt_synthetic_id`` (the upsert key; guard 1 — never email). The
        approve path (S10 W3) threads only ``family_id`` + ``body``, so this
        resolution lets the deterministic decision route write a Note that lands
        on the same Contact + Deal ``push_family`` created. A ``family_id`` that
        resolves to nothing still creates the Note (no crash) — the note is the
        durable record even if association targets are absent. Returns the live
        note id as ``recorded_id``.
        """
        channel = str(message.get("channel", "email"))
        body = str(message.get("body", ""))

        # Resolve association ids: prefer explicit ids, else look up by the
        # family's gt_synthetic_id (never email — guard 1). Resolution happens
        # BEFORE the note create so a budget breach (guard 3) fails closed early.
        contact_id = message.get("contact_id")
        deal_id = message.get("deal_id")
        family_id = message.get("family_id")
        if family_id is not None and (contact_id is None or deal_id is None):
            gt_id = str(family_id)
            if contact_id is None:
                contact_id = self._resolve_id(_CONTACTS, gt_id)
            if deal_id is None:
                deal_id = self._resolve_id(_DEALS, gt_id)

        timestamp = _hs_now_ms()
        created = self._request(
            "POST",
            _NOTES,
            json={"properties": {"hs_note_body": body, "hs_timestamp": timestamp}},
        ).json()
        note_id = str(created["id"])

        if contact_id:
            self._associate(_NOTES, note_id, "contacts", str(contact_id))
        if deal_id:
            self._associate(_NOTES, note_id, "deals", str(deal_id))

        return SendResult(simulated=False, recorded_id=note_id, channel=channel)

    def _resolve_id(self, object_path: str, gt_id: str) -> str | None:
        """Resolve a contact/deal object id by ``gt_synthetic_id`` (never email)."""
        match = self._search_by_gt_id(object_path, gt_id, [_GT_SYNTHETIC_ID])
        return None if match is None else str(match["id"])

    # ----------------------------------------------------- GT Social Post mirror
    def _social_post_properties(
        self, dispatch: PlatformDispatch, request: PublishRequest
    ) -> dict[str, Any]:
        """Build the GT Social Post props from a dispatch + its request (W3).

        The idempotency key is ``gt_synthetic_id = str(post_id)`` — NEVER a
        contact identity (INV-1). Every other gt_* prop is gated on the params
        declaration (INV-11) AND on the value being present (``None`` ⇒ skip, no
        empty writes). ``gt_content_ref`` prefers the asset ref, then candidate.
        """
        declared = set(self._crm.gt_properties.social_post)
        id_prop = self._crm.gt_social_post_object.id_property
        props: dict[str, Any] = {id_prop: str(dispatch.post_id)}
        if "gt_platform" in declared:
            props["gt_platform"] = dispatch.channel.value
        if "gt_dispatch_status" in declared:
            props["gt_dispatch_status"] = dispatch.dispatch_status.value
        if "gt_scheduled_for" in declared:
            props["gt_scheduled_for"] = request.scheduled_for
        if "gt_campaign_theme" in declared and request.campaign_theme is not None:
            props["gt_campaign_theme"] = request.campaign_theme
        if "gt_content_ref" in declared:
            content_ref = request.asset_ref or request.candidate_ref
            if content_ref is not None:
                props["gt_content_ref"] = str(content_ref)
        if "gt_simulated_receipt" in declared and dispatch.simulated_result is not None:
            props["gt_simulated_receipt"] = dispatch.simulated_result
        return props

    def mirror_social_post(
        self, dispatch: PlatformDispatch, *, request: PublishRequest
    ) -> str | None:
        """Upsert one GT Social Post custom object behind the four guards (W3).

        The cockpit is the primary observability plane; this writes the SECOND
        screen so the team can monitor publishing on HubSpot too. Idempotent on
        ``gt_synthetic_id = str(post_id)`` (NEVER a contact identity — INV-1, so a
        real-contact collision is structurally impossible here). A non-mirrorable
        dispatch (skipped/blocked/failed/capped) returns ``None`` with NO HubSpot
        call. Each call rides guard 3's per-run budget + the registry kill switch
        (INV-8). Returns the live custom-object id.
        """
        if not is_mirrorable(dispatch):
            return None
        object_path = f"/crm/v3/objects/{self._crm.gt_social_post_object.object_type}"
        gt_id = str(dispatch.post_id)
        return self._upsert(object_path, gt_id, self._social_post_properties(dispatch, request))


def _parse_hs_timestamp(raw: object) -> datetime | None:
    """Parse a HubSpot ISO-8601 timestamp (``hs_lastmodifieddate``) to a datetime.

    Tolerant of the trailing ``Z`` and of a missing value (returns ``None``); a
    malformed value also degrades to ``None`` rather than raising — the timestamp
    is only used for conflict recency, never a correctness gate.
    """
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _hs_now_ms() -> int:
    """Current time as the epoch-millisecond timestamp HubSpot's notes expect."""
    return int(datetime.now(tz=UTC).timestamp() * 1000)
