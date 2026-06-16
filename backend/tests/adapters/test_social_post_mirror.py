"""publish-monitor W3 — GT Social Post mirror on both CRM adapters.

The cockpit is the primary observability plane; the GT Social Post custom object
is the SECOND screen. These tests prove the mirror on BOTH impls (simulated
default, live under CRM_MODE=live) against a ``httpx.MockTransport`` — **no real
network, no live HubSpot write** (INV-9). The four guards still hold: a real
domain is never touched (INV-1; the mirror keys on str(post_id), NEVER PII), the
per-run cap + kill switch fail closed (INV-8), and a non-published dispatch is
NOT mirrored.

Every value read here flows from ``params.example.yaml`` (INV-11) — the gt_*
prop names and the custom object type id live in params, never a code literal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.adapters.hubspot.crm_adapter import (
    SimulatedCRMAdapter,
    apply_mirror_results,
    is_mirrorable,
)
from app.adapters.hubspot.live_adapter import (
    HubSpotBudgetExceededError,
    LiveHubSpotCRMAdapter,
)
from app.ai.schemas.content import Channel
from app.core.params import AwardAmounts, Crm, load_params
from app.marketing.schemas.publish import (
    MirrorStatus,
    PlatformDispatch,
    PublishMonitor,
    PublishRequest,
)
from app.marketing.schemas.scheduling import DispatchStatus

_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
_TOKEN = "pat" + "-" + "test" + "-" + "synthetic-fake-token-value"


def _crm() -> Crm:
    return load_params(_EXAMPLE_PARAMS).crm


def _award_amounts() -> AwardAmounts:
    return load_params(_EXAMPLE_PARAMS).funding.award_amounts


def _request(*, request_id: UUID | None = None) -> PublishRequest:
    return PublishRequest(
        id=request_id or uuid4(),
        channels=(Channel.INSTAGRAM, Channel.X),
        body="GT Anywhere is open for fall enrollment.",
        assetRef=uuid4(),
        scheduledFor="2026-08-01T09:00:00Z",
        campaignTheme="back-to-school",
    )


def _dispatch(
    *,
    post_id: UUID | None = None,
    channel: Channel = Channel.INSTAGRAM,
    status: DispatchStatus = DispatchStatus.SIMULATED_SENT,
    capped: bool = False,
    mirror_status: MirrorStatus = MirrorStatus.PENDING,
    simulated_result: str | None = "sim-receipt-abc",
) -> PlatformDispatch:
    return PlatformDispatch(
        post_id=post_id or uuid4(),
        channel=channel,
        dispatch_status=status,
        simulated_result=simulated_result,
        capped=capped,
        mirror_status=mirror_status,
    )


# ---------------------------------------------------------------------------
# A scripted HubSpot for the custom-object upsert (search → create/patch).
# ---------------------------------------------------------------------------


class _FakeHubSpot:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._objects: dict[str, str] = {}  # gt_synthetic_id -> object id
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"gtsp-{self._seq}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        if path.endswith("/search"):
            gt_id = self._extract_gt_id(body)
            if gt_id is not None and gt_id in self._objects:
                obj_id = self._objects[gt_id]
                return httpx.Response(
                    200,
                    json={
                        "total": 1,
                        "results": [{"id": obj_id, "properties": {"gt_synthetic_id": gt_id}}],
                    },
                )
            return httpx.Response(200, json={"total": 0, "results": []})
        if request.method == "POST":
            props = body.get("properties", {})
            gt_id = props.get("gt_synthetic_id")
            obj_id = self._next_id()
            if gt_id:
                self._objects[gt_id] = obj_id
            return httpx.Response(201, json={"id": obj_id, "properties": props})
        if request.method == "PATCH":
            obj_id = path.rstrip("/").split("/")[-1]
            return httpx.Response(
                200, json={"id": obj_id, "properties": body.get("properties", {})}
            )
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})

    @staticmethod
    def _extract_gt_id(body: dict[str, Any]) -> str | None:
        for group in body.get("filterGroups", []):
            for flt in group.get("filters", []):
                if flt.get("propertyName") == "gt_synthetic_id":
                    return str(flt.get("value"))
        return None


def _live(fake: _FakeHubSpot, *, cap: int = 200, crm: Crm | None = None) -> LiveHubSpotCRMAdapter:
    client = httpx.Client(
        transport=httpx.MockTransport(fake.handler), base_url="https://api.hubapi.com"
    )
    return LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=crm or _crm(),
        award_amounts=_award_amounts(),
        calls_per_run_cap=cap,
    )


# ===========================================================================
# is_mirrorable — the pure predicate
# ===========================================================================


def test_is_mirrorable_only_for_sent_pending_uncapped() -> None:
    assert is_mirrorable(_dispatch())  # simulated_sent + pending + not capped
    assert not is_mirrorable(_dispatch(status=DispatchStatus.BLOCKED))
    assert not is_mirrorable(_dispatch(status=DispatchStatus.FAILED))
    assert not is_mirrorable(_dispatch(capped=True))
    assert not is_mirrorable(_dispatch(mirror_status=MirrorStatus.SKIPPED))
    assert not is_mirrorable(_dispatch(mirror_status=MirrorStatus.MIRRORED))


# ===========================================================================
# Simulated impl — deterministic id, records-never-sends, flips status
# ===========================================================================


def test_simulated_mirror_returns_deterministic_id() -> None:
    """Same dispatch ⇒ same synthetic id (no wall clock / uuid4); recorded."""
    adapter = SimulatedCRMAdapter()
    request = _request()
    dispatch = _dispatch(post_id=uuid4())

    first = adapter.mirror_social_post(dispatch, request=request)
    second = adapter.mirror_social_post(dispatch, request=request)

    assert first == second == f"sim-gtsp-{dispatch.post_id}"
    assert adapter.mirrored_log[0] == (first, dispatch.post_id)


def test_simulated_mirror_skips_non_mirrorable() -> None:
    """A blocked/capped dispatch returns None and records nothing."""
    adapter = SimulatedCRMAdapter()
    request = _request()

    assert (
        adapter.mirror_social_post(_dispatch(status=DispatchStatus.BLOCKED), request=request)
        is None
    )
    assert adapter.mirror_social_post(_dispatch(capped=True), request=request) is None
    assert adapter.mirrored_log == []


# ===========================================================================
# apply_mirror_results — pure monitor fold
# ===========================================================================


def test_apply_mirror_results_flips_status_and_sets_object_id() -> None:
    sent = _dispatch(channel=Channel.INSTAGRAM)
    blocked = _dispatch(
        channel=Channel.X, status=DispatchStatus.BLOCKED, mirror_status=MirrorStatus.SKIPPED
    )
    monitor = PublishMonitor(request_id=uuid4(), dispatches=(sent, blocked))

    mirror_ids: dict[UUID, str | None] = {sent.post_id: "gtsp-77", blocked.post_id: None}
    updated = apply_mirror_results(monitor, mirror_ids)

    by_id = {d.post_id: d for d in updated.dispatches}
    assert by_id[sent.post_id].mirror_status is MirrorStatus.MIRRORED
    assert by_id[blocked.post_id].mirror_status is MirrorStatus.SKIPPED  # untouched
    assert updated.hubspot_object_id == "gtsp-77"
    # Purity: the original monitor is unchanged (frozen, model_copy used).
    assert monitor.dispatches[0].mirror_status is MirrorStatus.PENDING


def test_apply_mirror_results_no_mirrors_leaves_monitor() -> None:
    pending = _dispatch()
    monitor = PublishMonitor(request_id=uuid4(), dispatches=(pending,))
    updated = apply_mirror_results(monitor, {pending.post_id: None})
    assert updated.dispatches[0].mirror_status is MirrorStatus.PENDING
    assert updated.hubspot_object_id is None


def test_simulated_end_to_end_mirror_then_fold() -> None:
    """The full W3 loop on synthetic data by default (INV-9): mirror → fold."""
    adapter = SimulatedCRMAdapter()
    request = _request()
    d1 = _dispatch(post_id=uuid4(), channel=Channel.INSTAGRAM)
    d2 = _dispatch(
        post_id=uuid4(),
        channel=Channel.X,
        status=DispatchStatus.BLOCKED,
        mirror_status=MirrorStatus.SKIPPED,
    )
    monitor = PublishMonitor(request_id=request.id, dispatches=(d1, d2))

    mirror_ids = {
        d.post_id: adapter.mirror_social_post(d, request=request) for d in monitor.dispatches
    }
    updated = apply_mirror_results(monitor, mirror_ids)

    by_id = {d.post_id: d for d in updated.dispatches}
    assert by_id[d1.post_id].mirror_status is MirrorStatus.MIRRORED
    assert by_id[d2.post_id].mirror_status is MirrorStatus.SKIPPED
    assert updated.hubspot_object_id == f"sim-gtsp-{d1.post_id}"


# ===========================================================================
# Live impl — upsert behind guards
# ===========================================================================


def test_live_mirror_upserts_custom_object_keyed_on_post_id() -> None:
    """PASS: live mirror creates the GT Social Post, keyed on str(post_id) (INV-1)."""
    fake = _FakeHubSpot()
    adapter = _live(fake)
    request = _request()
    dispatch = _dispatch(post_id=uuid4())

    obj_id = adapter.mirror_social_post(dispatch, request=request)

    assert obj_id  # the live custom-object id
    crm = _crm()
    object_type = crm.gt_social_post_object.object_type
    # The write targeted the custom object path from params (INV-11).
    creates = [
        r
        for r in fake.requests
        if object_type in r.url.path and r.method == "POST" and "/search" not in r.url.path
    ]
    assert creates, "expected a custom-object create on the params-declared object type"
    props = json.loads(creates[0].content)["properties"]
    assert props["gt_synthetic_id"] == str(dispatch.post_id)  # NEVER PII
    assert props["gt_platform"] == dispatch.channel.value
    assert props["gt_dispatch_status"] == dispatch.dispatch_status.value
    assert props["gt_scheduled_for"] == request.scheduled_for
    assert props["gt_campaign_theme"] == request.campaign_theme
    assert props["gt_content_ref"] == str(request.asset_ref)
    # Every search keyed on gt_synthetic_id — never any contact identity.
    for r in fake.requests:
        if r.url.path.endswith("/search"):
            assert "gt_synthetic_id" in r.content.decode()


def test_live_mirror_second_time_patches_not_duplicates() -> None:
    """A re-mirror of the same post PATCHes (idempotent on gt_synthetic_id)."""
    fake = _FakeHubSpot()
    adapter = _live(fake)
    request = _request()
    dispatch = _dispatch(post_id=uuid4())

    first = adapter.mirror_social_post(dispatch, request=request)
    second = adapter.mirror_social_post(dispatch, request=request)

    assert first == second
    patches = [r for r in fake.requests if r.method == "PATCH"]
    assert patches, "the second mirror must PATCH the existing object"


def test_live_mirror_skips_non_mirrorable_with_no_call() -> None:
    """A blocked/capped dispatch is NOT mirrored — and makes NO HubSpot call."""
    fake = _FakeHubSpot()
    adapter = _live(fake)
    request = _request()

    assert (
        adapter.mirror_social_post(_dispatch(status=DispatchStatus.BLOCKED), request=request)
        is None
    )
    assert adapter.mirror_social_post(_dispatch(capped=True), request=request) is None
    assert fake.requests == [], "a non-mirrorable dispatch must not touch HubSpot"


def test_live_mirror_cap_forces_no_overspend() -> None:
    """BLOCK (INV-8): the (cap+1)th call raises — the mirror fails closed."""
    fake = _FakeHubSpot()
    adapter = _live(fake, cap=1)  # search ok, create would be the 2nd call
    request = _request()

    with pytest.raises(HubSpotBudgetExceededError):
        adapter.mirror_social_post(_dispatch(post_id=uuid4()), request=request)


# ===========================================================================
# Params — the new social_post block loads
# ===========================================================================


def test_params_social_post_properties_load() -> None:
    crm = _crm()
    assert "gt_synthetic_id" in crm.gt_properties.social_post
    assert "gt_platform" in crm.gt_properties.social_post
    assert crm.gt_social_post_object.id_property == "gt_synthetic_id"
    assert crm.gt_social_post_object.object_type  # non-empty placeholder/live id
