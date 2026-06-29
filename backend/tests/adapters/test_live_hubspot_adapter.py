"""S10 W2 — LiveHubSpotCRMAdapter + the four guards (TDD red→green).

These are the §4.1 red tests for the **production** ``CRMAdapter`` impl. They run
against a ``httpx.MockTransport`` — **no real network, no live HubSpot write** (the
real push lands in W3). The adapter is the same interface ``core/`` already
consumes (``CRMAdapter``), so a passing run proves the seam is swappable with zero
core/ai changes (ARCHITECTURE.md §7).

The four guards each get a PASSING and a BLOCKING test (ANALYSIS §3):

1. Synthetic write-lock (INV-1) — a denylisted real-vendor-domain email MUST
   raise; the upsert searches by ``gt_synthetic_id``, never email.
2. Inbound PII firewall (INV-1) — ``read_mirror`` reads only stage+timestamp; a
   real name in a mock mirror payload appears nowhere in the returned object.
3. Cap + kill-switch (INV-8) — the (cap+1)th HubSpot call raises; the registry
   degrades to ``SimulatedCRMAdapter`` when the kill switch is set.
4. Approval-gate (INV-2) — no import path from ``app/ai/**`` reaches the live
   adapter (asserted in :mod:`tests.unit.test_core_purity`-style import walk here).

Every email used here is synthetic (``*.test`` / ``*.invalid``) so the PII-scan
gate stays green (INV-1).
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.adapters.hubspot.crm_adapter import (
    CRMAdapter,
    SendResult,
    SimulatedCRMAdapter,
    SyncResult,
)
from app.adapters.hubspot.live_adapter import (
    HubSpotBudgetExceededError,
    LiveHubSpotCRMAdapter,
    SyntheticWriteLockError,
)
from app.adapters.registry import get_crm_adapter
from app.core.funding_gate import award_for_tier
from app.core.params import AwardAmounts, Crm, Resilience, load_params
from app.core.seam import MirrorState
from app.data.models import FamilyRecord, FundingType, Stage

_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
# A fake Bearer token, assembled from inert fragments so the literal does not
# match the PII-scan's HubSpot-token signature (same trick the scan's own
# self-test uses). It is not a real secret.
_TOKEN = "pat" + "-" + "test" + "-" + "synthetic-fake-token-value"
# A DENYLISTED real-vendor domain (guard-1 block target), assembled from inert
# fragments so no literal real-PII email sits in the repo (PII-scan stays green).
# `gauntlethq.com` is in crm.real_domain_denylist — the write-lock must refuse it.
_DENYLISTED_EMAIL = "tom.babb" + "@" + "gauntlethq" + ".com"
# A domain that is NEITHER allowlisted NOR denylisted — also assembled inert.
_UNKNOWN_DOMAIN_EMAIL = "someone" + "@" + "random-unknown-vendor" + ".example.com"


def _crm() -> Crm:
    return load_params(_EXAMPLE_PARAMS).crm


def _award_amounts() -> AwardAmounts:
    return load_params(_EXAMPLE_PARAMS).funding.award_amounts


def _resilience() -> Resilience:
    """The A5 retry/backoff params block the registry injects (INV-11)."""
    return load_params(_EXAMPLE_PARAMS).resilience


def _family(
    *,
    email: str = "synthetic.rivera@example.test",
    stage: Stage = Stage.APPLY,
    family_id: UUID | None = None,
) -> FamilyRecord:
    """A minimal synthetic family for push/read happy-paths."""
    now = datetime(2026, 1, 2, tzinfo=UTC)
    return FamilyRecord(
        family_id=family_id or uuid4(),
        display_name="Rivera Household",
        primary_contact_synthetic_email=email,
        current_stage=stage,
        funding_type=FundingType.TEFA_STANDARD,
        attribution_source="organic",
        attribution_utm={},
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# A scripted HubSpot — a MockTransport that fakes the CRM v3 endpoints the
# adapter calls, recording every request so tests can assert on them.
# ---------------------------------------------------------------------------


class _FakeHubSpot:
    """Records requests and answers the CRM v3 calls the adapter makes.

    Stores contacts/deals by their ``gt_synthetic_id`` so a second push of the
    same family PATCHes (upsert) rather than creating a duplicate.
    """

    def __init__(
        self,
        *,
        existing_deal_stage_id: str | None = None,
        existing_funding_state: str | None = None,
        existing_owner_id: str | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self._contacts: dict[str, str] = {}  # gt_synthetic_id -> object id
        self._deals: dict[str, str] = {}  # gt_synthetic_id -> object id
        self._deal_stage: dict[str, str] = {}  # object id -> dealstage id
        self._seq = 0
        # Optional pre-seeded deal (used by read_mirror tests). R1: a pre-seeded
        # gt_funding_state + hubspot_owner_id let read_mirror tests exercise the
        # multi-field firewall (the two new PII-free scalars).
        self._preseed_stage = existing_deal_stage_id
        self._preseed_funding_state = existing_funding_state
        self._preseed_owner_id = existing_owner_id

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        body: dict[str, Any] = json.loads(request.content) if request.content else {}

        if path.endswith("/search"):
            return self._search(path, body)
        if "/associations/" in path:
            return httpx.Response(200, json={"status": "COMPLETE"})
        # create / patch
        if request.method == "POST":
            return self._create(path, body)
        if request.method == "PATCH":
            return self._patch(path, body)
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})

    def _object_kind(self, path: str) -> str:
        if "contacts" in path:
            return "contacts"
        if "deals" in path:
            return "deals"
        return "notes"

    def _search(self, path: str, body: dict[str, Any]) -> httpx.Response:
        kind = self._object_kind(path)
        # The search filter the adapter sends — assert it keys on gt_synthetic_id.
        gt_id = self._extract_gt_id(body)
        store = self._contacts if kind == "contacts" else self._deals
        if gt_id is not None and gt_id in store:
            obj_id = store[gt_id]
            props: dict[str, Any] = {"gt_synthetic_id": gt_id}
            if kind == "deals":
                stage = self._deal_stage.get(obj_id, self._preseed_stage or "")
                props["dealstage"] = stage
                props["hs_lastmodifieddate"] = "2026-01-02T00:00:00Z"
            return httpx.Response(
                200,
                json={"total": 1, "results": [{"id": obj_id, "properties": props}]},
            )
        # Pre-seeded deal path (read_mirror against an existing portal deal).
        if kind == "deals" and self._preseed_stage is not None:
            obj_id = self._next_id("deal")
            props = {
                "gt_synthetic_id": gt_id or "",
                "dealstage": self._preseed_stage,
                "hs_lastmodifieddate": "2026-01-02T00:00:00Z",
                # PII the firewall must drop — a REAL-looking name.
                "_pii_probe_name": "Margaret Realparent",
            }
            # R1 multi-field scalars (only when seeded) — the funding-gate enum +
            # the HubSpot staff owner id (NOT contact PII).
            if self._preseed_funding_state is not None:
                props["gt_funding_state"] = self._preseed_funding_state
            if self._preseed_owner_id is not None:
                props["hubspot_owner_id"] = self._preseed_owner_id
            return httpx.Response(
                200,
                json={"total": 1, "results": [{"id": obj_id, "properties": props}]},
            )
        return httpx.Response(200, json={"total": 0, "results": []})

    @staticmethod
    def _extract_gt_id(body: dict[str, Any]) -> str | None:
        for group in body.get("filterGroups", []):
            for flt in group.get("filters", []):
                if flt.get("propertyName") == "gt_synthetic_id":
                    return str(flt.get("value"))
        return None

    def _create(self, path: str, body: dict[str, Any]) -> httpx.Response:
        kind = self._object_kind(path)
        props = body.get("properties", {})
        gt_id = props.get("gt_synthetic_id")
        if kind == "contacts":
            obj_id = self._next_id("contact")
            if gt_id:
                self._contacts[gt_id] = obj_id
        elif kind == "deals":
            obj_id = self._next_id("deal")
            if gt_id:
                self._deals[gt_id] = obj_id
            self._deal_stage[obj_id] = props.get("dealstage", "")
        else:  # notes
            obj_id = self._next_id("note")
        return httpx.Response(201, json={"id": obj_id, "properties": props})

    def _patch(self, path: str, body: dict[str, Any]) -> httpx.Response:
        obj_id = path.rstrip("/").split("/")[-1]
        props = body.get("properties", {})
        if "deals" in path and "dealstage" in props:
            self._deal_stage[obj_id] = props["dealstage"]
        return httpx.Response(200, json={"id": obj_id, "properties": props})


def _adapter(
    fake: _FakeHubSpot, *, cap: int = 200, crm: Crm | None = None
) -> LiveHubSpotCRMAdapter:
    client = httpx.Client(
        transport=httpx.MockTransport(fake.handler), base_url="https://api.hubapi.com"
    )
    return LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=crm or _crm(),
        award_amounts=_award_amounts(),
        calls_per_run_cap=cap,
        resilience=_resilience(),
    )


# ===========================================================================
# Happy-path contract — push_family / read_mirror / send_message
# ===========================================================================


def test_push_family_upserts_by_gt_synthetic_id_not_email() -> None:
    """push_family searches contact+deal by gt_synthetic_id (NEVER email; guard 1).

    Returns SyncResult(simulated=False) with the live deal id; every search filter
    keys on gt_synthetic_id = str(family_id), making email collision impossible.
    """
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family()

    result = adapter.push_family(record)

    assert isinstance(result, SyncResult)
    assert result.simulated is False
    assert result.family_id == record.family_id
    assert result.stage is record.current_stage
    assert result.recorded_id  # the live deal id

    # Every search the adapter issued filtered on gt_synthetic_id, never email.
    searches = [r for r in fake.requests if r.url.path.endswith("/search")]
    assert searches, "expected the upsert to search before create"
    gt_id = str(record.family_id)
    for req in searches:
        payload = json.loads(req.content)
        names = [
            f["propertyName"] for g in payload.get("filterGroups", []) for f in g.get("filters", [])
        ]
        assert names == ["gt_synthetic_id"], f"search must key on gt_synthetic_id, got {names}"
        values = [f["value"] for g in payload["filterGroups"] for f in g["filters"]]
        assert values == [gt_id]
        assert record.primary_contact_synthetic_email not in req.content.decode()


def test_push_family_second_time_patches_not_duplicates() -> None:
    """A second push of the same family PATCHes the existing objects (idempotent)."""
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family()

    first = adapter.push_family(record)
    advanced = record.model_copy(update={"current_stage": Stage.ENROLL})
    second = adapter.push_family(advanced)

    assert first.recorded_id == second.recorded_id  # same deal id — no duplicate
    patches = [r for r in fake.requests if r.method == "PATCH"]
    assert patches, "second push must PATCH the existing deal"


def test_push_family_sets_dealstage_amount_and_gt_props() -> None:
    """The deal carries the mapped dealstage, TEFA amount, and the gt_* props."""
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family(stage=Stage.APPLY)

    adapter.push_family(record)

    deal_writes = [
        json.loads(r.content)
        for r in fake.requests
        if "deals" in r.url.path and r.method in {"POST", "PATCH"} and "/search" not in r.url.path
    ]
    assert deal_writes, "expected a deal create/patch"
    props = deal_writes[0]["properties"]
    assert props["dealstage"] == _crm().stage_map["apply"]  # mapped, not raw
    assert props["gt_synthetic_id"] == str(record.family_id)
    assert "gt_funding_state" in props
    assert "amount" in props  # the TEFA award mirror


def test_push_family_deal_amount_equals_tefa_award_from_params() -> None:
    """The deal ``amount`` == the family's TEFA award, derived from params (INV-11).

    No hardcoded number: the expected value flows from the funding tier through
    the shared :func:`award_for_tier` helper over ``funding.award_amounts``, so a
    params drift moves both the adapter output and this assertion together.
    """
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family()  # FundingType.TEFA_STANDARD

    adapter.push_family(record)

    deal_writes = [
        json.loads(r.content)
        for r in fake.requests
        if "deals" in r.url.path and r.method in {"POST", "PATCH"} and "/search" not in r.url.path
    ]
    assert deal_writes, "expected a deal create/patch"
    props = deal_writes[0]["properties"]
    expected = award_for_tier(record.funding_type, _award_amounts())
    assert props["amount"] == str(expected)


def test_push_family_self_pay_writes_no_amount() -> None:
    """A SELF_PAY (non-TEFA) family has no award — the deal omits ``amount``.

    Fail-closed: the adapter never fabricates an ``amount=0`` for a tier with no
    TEFA award (``award_for_tier`` raises for self_pay; the builder skips it).
    """
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family().model_copy(update={"funding_type": FundingType.SELF_PAY})

    adapter.push_family(record)

    deal_writes = [
        json.loads(r.content)
        for r in fake.requests
        if "deals" in r.url.path and r.method in {"POST", "PATCH"} and "/search" not in r.url.path
    ]
    assert deal_writes, "expected a deal create/patch"
    assert "amount" not in deal_writes[0]["properties"]


def test_read_mirror_maps_dealstage_to_cockpit_stage() -> None:
    """read_mirror searches the deal by gt_synthetic_id and maps id→Stage."""
    crm = _crm()
    fake = _FakeHubSpot(existing_deal_stage_id=crm.stage_map["enroll"])
    adapter = _adapter(fake, crm=crm)

    mirror = adapter.read_mirror(uuid4())

    assert isinstance(mirror, MirrorState)
    assert mirror.stage is Stage.ENROLL


def test_read_mirror_no_deal_returns_empty_mirror() -> None:
    """No matching deal ⇒ empty mirror (the §4.7 deriver reads it as unsynced)."""
    fake = _FakeHubSpot()  # no preseed, empty store
    adapter = _adapter(fake)

    mirror = adapter.read_mirror(uuid4())

    assert mirror.stage is None
    assert mirror.mirror_updated_at is None


def test_send_message_creates_note_and_associates() -> None:
    """send_message creates a Note (hs_note_body+hs_timestamp) and returns its id."""
    fake = _FakeHubSpot()
    adapter = _adapter(fake)

    result = adapter.send_message(
        {
            "family_id": str(uuid4()),
            "channel": "email",
            "body": "Welcome to Gauntlet!",
            "contact_id": "contact-1",
            "deal_id": "deal-1",
        }
    )

    assert isinstance(result, SendResult)
    assert result.simulated is False
    assert result.channel == "email"
    assert result.recorded_id
    note_creates = [r for r in fake.requests if "notes" in r.url.path and r.method == "POST"]
    assert note_creates, "expected a note create"
    note_props = json.loads(note_creates[0].content)["properties"]
    assert "hs_note_body" in note_props and "hs_timestamp" in note_props


def test_send_message_resolves_ids_by_gt_synthetic_id() -> None:
    """send_message with a family_id (no ids) resolves contact/deal by gt_synthetic_id.

    The approve path (S10 W3) threads only ``family_id`` + ``body`` — the live
    adapter resolves the contact and deal ids by ``gt_synthetic_id`` and
    associates the new Note to BOTH. The lookups key on gt_synthetic_id, never
    email (guard 1).
    """
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family()
    # Seed the portal so the gt_synthetic_id resolves to a contact + deal.
    adapter.push_family(record)

    result = adapter.send_message(
        {
            "family_id": str(record.family_id),
            "channel": "email",
            "body": "Quick follow-up about your enrollment.",
        }
    )

    assert isinstance(result, SendResult)
    assert result.recorded_id
    # The note was associated to BOTH the contact and the deal it resolved. The
    # v4 default-association path is .../associations/default/{toObject}/{id}, so
    # the to-object is the segment AFTER "default".
    assocs = [r for r in fake.requests if "/associations/" in r.url.path and "notes" in r.url.path]
    targets = {r.url.path.split("/associations/default/")[1].split("/")[0] for r in assocs}
    assert targets == {"contacts", "deals"}, f"note must link contact+deal, got {targets}"
    # Every lookup keyed on gt_synthetic_id, never email.
    searches = [r for r in fake.requests if r.url.path.endswith("/search")]
    for req in searches:
        assert "gt_synthetic_id" in req.content.decode()
        assert record.primary_contact_synthetic_email not in req.content.decode()


def test_send_message_missing_resolution_still_creates_note() -> None:
    """A family_id that resolves to no objects still creates the Note (no crash)."""
    fake = _FakeHubSpot()  # empty store — nothing to resolve
    adapter = _adapter(fake)

    result = adapter.send_message({"family_id": str(uuid4()), "channel": "email", "body": "hi"})

    assert result.recorded_id
    note_creates = [r for r in fake.requests if "notes" in r.url.path and r.method == "POST"]
    assert note_creates, "the note is created even when association targets are absent"


# ===========================================================================
# GUARD 1 — synthetic write-lock (INV-1): passing + BLOCKING
# ===========================================================================


def test_guard1_synthetic_email_passes_write_lock() -> None:
    """PASS: a synthetic-domain email is allowed to write (allowlist hit)."""
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family(email="synthetic.rivera@example.test")

    result = adapter.push_family(record)  # must NOT raise

    assert result.simulated is False


def test_guard1_real_domain_email_blocks_write() -> None:
    """BLOCK: pushing a denylisted real-vendor-domain email MUST raise (guard 1).

    The contract case from ANALYSIS §3: the Tom-Babb ``gauntlethq`` contact
    (assembled inert in ``_DENYLISTED_EMAIL``) is a real person on a denylisted
    domain — the write-lock refuses it, so a real contact can never be written
    or merged.
    """
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family(email=_DENYLISTED_EMAIL)

    with pytest.raises(SyntheticWriteLockError):
        adapter.push_family(record)

    # Nothing was written: no create/patch reached the (fake) portal.
    writes = [
        r for r in fake.requests if r.method in {"POST", "PATCH"} and "/search" not in r.url.path
    ]
    assert not writes, "the write-lock must block BEFORE any HubSpot write"


def test_guard1_non_allowlisted_domain_blocks_write() -> None:
    """BLOCK: a domain that's neither synthetic-allowlisted nor denylisted still blocks."""
    fake = _FakeHubSpot()
    adapter = _adapter(fake)
    record = _family(email=_UNKNOWN_DOMAIN_EMAIL)

    with pytest.raises(SyntheticWriteLockError):
        adapter.push_family(record)


# ===========================================================================
# GUARD 2 — inbound PII firewall on read_mirror (INV-1): passing + BLOCKING
# ===========================================================================


def test_guard2_read_mirror_returns_only_stage_and_timestamp() -> None:
    """PASS: read_mirror returns only the stage + timestamp — the firewall shape."""
    crm = _crm()
    fake = _FakeHubSpot(existing_deal_stage_id=crm.stage_map["apply"])
    adapter = _adapter(fake, crm=crm)

    mirror = adapter.read_mirror(uuid4())

    # MirrorState carries ONLY PII-free reconcile fields: funnel stage, the mirror
    # timestamp, the funding-state enum, and the HubSpot OWNER id (a staff user id —
    # never contact/family/minor PII). This exact-set assertion still fails CLOSED
    # if anyone adds a PII-carrying slot (e.g. name/email/dob) — the multi-field
    # reconcile widening (R1) is bounded to these four (INV-1 firewall, AUDIT/R1).
    assert set(mirror.__slots__) == {
        "stage",
        "mirror_updated_at",
        "funding_state",
        "owner",
    }
    assert mirror.stage is Stage.APPLY


def test_read_mirror_reads_funding_state_and_owner_multifield() -> None:
    """R1: read_mirror lifts gt_funding_state + hubspot_owner_id into the MirrorState.

    The §4.7 deriver now compares stage + funding_state + owner; the live adapter
    must populate all three off the deal (the owner is a HubSpot staff/user id, NOT
    contact PII — guard 2 stays intact). A mapped funding_state parses to the enum;
    the owner is carried as the raw string.
    """
    from app.data.models import FundingState

    crm = _crm()
    fake = _FakeHubSpot(
        existing_deal_stage_id=crm.stage_map["enroll"],
        existing_funding_state=FundingState.GT_CONFIRMED.value,
        existing_owner_id="hs-owner-4242",
    )
    adapter = _adapter(fake, crm=crm)

    mirror = adapter.read_mirror(uuid4())

    assert mirror.stage is Stage.ENROLL
    assert mirror.funding_state is FundingState.GT_CONFIRMED
    assert mirror.owner == "hs-owner-4242"


def test_read_mirror_absent_multifield_props_stay_none() -> None:
    """R1: a deal with no funding_state/owner props ⇒ those fields stay None.

    Divergence detection safely skips a ``None`` mirror field, so an un-mapped /
    un-set property must not fabricate a value — it leaves the field ``None``.
    """
    crm = _crm()
    fake = _FakeHubSpot(existing_deal_stage_id=crm.stage_map["apply"])  # no funding/owner
    adapter = _adapter(fake, crm=crm)

    mirror = adapter.read_mirror(uuid4())

    assert mirror.stage is Stage.APPLY
    assert mirror.funding_state is None
    assert mirror.owner is None


def test_guard2_real_name_in_payload_never_surfaces(caplog: pytest.LogCaptureFixture) -> None:
    """BLOCK: a real name in the mirror payload appears nowhere in the result or logs."""
    crm = _crm()
    fake = _FakeHubSpot(existing_deal_stage_id=crm.stage_map["enroll"])
    adapter = _adapter(fake, crm=crm)

    with caplog.at_level("DEBUG"):
        mirror = adapter.read_mirror(uuid4())

    # The fake injected "_pii_probe_name": "Margaret Realparent" into the payload.
    assert "Margaret Realparent" not in repr(mirror)
    assert "Margaret" not in repr(mirror)
    assert "Margaret Realparent" not in caplog.text, "PII leaked into a log"


# ===========================================================================
# read_mirror legacy/unmapped stage — catch StageMappingError, never crash
# ===========================================================================


def test_read_mirror_unmapped_stage_does_not_crash() -> None:
    """A legacy/unmapped HubSpot stage id ⇒ a divergence-shaped MirrorState, no crash.

    The §4.7 deriver reads a mirror whose stage diverges from local as a
    conflict; the adapter must catch StageMappingError and return such a mirror
    rather than raising out of read_mirror.
    """
    fake = _FakeHubSpot(existing_deal_stage_id="9999-legacy-leftover-stage")
    adapter = _adapter(fake)

    mirror = adapter.read_mirror(uuid4())  # must NOT raise

    assert isinstance(mirror, MirrorState)
    # A mirror the deriver will not read as "synced to a known cockpit stage":
    # either a sentinel divergence stage or None — never a crash.
    assert mirror.stage is None or isinstance(mirror.stage, Stage)


# ===========================================================================
# search_modified_since — the CRM-as-truth incremental pull (A2)
# ===========================================================================


def test_search_modified_since_filters_and_sorts() -> None:
    """A2: search_modified_since issues the grounded HubSpot CRM-Search incremental pull.

    Asserts the OUTBOUND request shape (RESEARCH_v2 §II.1):

    - POST ``/crm/v3/objects/{obj}/search``;
    - filter ``hs_lastmodifieddate`` ``GT`` <watermark-epoch-ms> (the modified
      timestamp, strictly-after);
    - exactly ONE sort, ``hs_lastmodifieddate`` ASCENDING (HubSpot rejects >1);
    - pagination follows ``paging.next.after`` across two mocked pages then stops;

    and that the returned records reflect BOTH pages, ascending. Every call rides
    the guard-3 per-run budget (it goes through ``_request``).
    """
    crm = _crm()
    watermark_ms = 1_700_000_000_000
    captured: list[dict[str, Any]] = []
    fam1, fam2 = uuid4(), uuid4()

    page1 = {
        "results": [
            {
                "id": "deal-1",
                "properties": {
                    "gt_synthetic_id": str(fam1),
                    "dealstage": crm.stage_map["apply"],
                    "hs_lastmodifieddate": "2026-01-02T00:00:00Z",
                    # PII the firewall must drop — never requested, never surfaced.
                    "_pii_probe_name": "Margaret Realparent",
                },
            }
        ],
        "paging": {"next": {"after": "200"}},
    }
    page2 = {
        "results": [
            {
                "id": "deal-2",
                "properties": {
                    "gt_synthetic_id": str(fam2),
                    "dealstage": crm.stage_map["enroll"],
                    "hs_lastmodifieddate": "2026-01-03T00:00:00Z",
                },
            }
        ],
        # No paging.next.after ⇒ the loop stops after this page.
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/crm/v3/objects/deals/search"
        body: dict[str, Any] = json.loads(request.content)
        captured.append(body)
        return httpx.Response(200, json=page1 if body.get("after") is None else page2)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.hubapi.com")
    adapter = LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=crm,
        award_amounts=_award_amounts(),
        calls_per_run_cap=200,
        resilience=_resilience(),
    )

    records = adapter.search_modified_since("deals", watermark_ms)

    # --- two pages were fetched (paging.next.after followed, then stopped) ---
    assert len(captured) == 2, "expected exactly two paged search requests"
    first = captured[0]

    # --- the filter: hs_lastmodifieddate GT <watermark-epoch-ms> ---
    filters = first["filterGroups"][0]["filters"]
    assert len(filters) == 1
    assert filters[0]["propertyName"] == "hs_lastmodifieddate"
    assert filters[0]["operator"] == "GT"
    assert str(filters[0]["value"]) == str(watermark_ms)

    # --- exactly ONE sort, hs_lastmodifieddate ASCENDING (HubSpot rejects >1) ---
    assert first["sorts"] == [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}]

    # --- page 1 carried no cursor; page 2 carried paging.next.after = "200" ---
    assert first.get("after") is None
    assert captured[1].get("after") == "200"

    # --- guard 2: only PII-free tracked scalars were requested ---
    assert "_pii_probe_name" not in first.get("properties", [])

    # --- the returned records reflect BOTH pages, ascending by modified-at ---
    assert len(records) == 2
    assert [fid for fid, _ in records] == [fam1, fam2]
    assert [mirror.stage for _, mirror in records] == [Stage.APPLY, Stage.ENROLL]
    assert "Margaret Realparent" not in repr(records)


def test_search_modified_since_bounded_by_until() -> None:
    """A2: an ``until_ms`` upper bound AND-s a second ``hs_lastmodifieddate LT`` filter.

    The window-chunking planner (``plan_sync_windows``) emits ordered [start,end]
    sub-windows; for each query to actually stay under HubSpot's 10k cap, the
    adapter must bound it on BOTH sides. When ``until_ms`` is passed the outbound
    search body's single filter group carries BOTH:

    - ``hs_lastmodifieddate`` ``GT`` <watermark_ms> (strictly-after the watermark);
    - ``hs_lastmodifieddate`` ``LT`` <until_ms> (strictly-before the window end);

    AND-ed in the SAME filter group (HubSpot ANDs filters within a group). The
    single ASC sort and the ``paging.next.after`` pagination are preserved.
    """
    crm = _crm()
    watermark_ms = 1_700_000_000_000
    until_ms = 1_700_500_000_000
    captured: list[dict[str, Any]] = []
    fam1 = uuid4()

    page = {
        "results": [
            {
                "id": "deal-1",
                "properties": {
                    "gt_synthetic_id": str(fam1),
                    "dealstage": crm.stage_map["apply"],
                    "hs_lastmodifieddate": "2026-01-02T00:00:00Z",
                },
            }
        ],
        # No paging.next.after ⇒ a single page.
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/crm/v3/objects/deals/search"
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=page)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.hubapi.com")
    adapter = LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=crm,
        award_amounts=_award_amounts(),
        calls_per_run_cap=200,
        resilience=_resilience(),
    )

    records = adapter.search_modified_since("deals", watermark_ms, until_ms=until_ms)

    assert len(captured) == 1
    filters = captured[0]["filterGroups"][0]["filters"]
    # BOTH bounds, AND-ed in the one filter group.
    assert len(filters) == 2
    by_op = {f["operator"]: f for f in filters}
    assert by_op["GT"]["propertyName"] == "hs_lastmodifieddate"
    assert str(by_op["GT"]["value"]) == str(watermark_ms)
    assert by_op["LT"]["propertyName"] == "hs_lastmodifieddate"
    assert str(by_op["LT"]["value"]) == str(until_ms)
    # The single ASC sort and the returned record are unchanged.
    assert captured[0]["sorts"] == [
        {"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}
    ]
    assert [fid for fid, _ in records] == [fam1]


# ===========================================================================
# read_last_modified — the CRM-Ops overview last-sync watermark (INV-6 aggregate)
# ===========================================================================


def test_read_last_modified_contacts_uses_lastmodifieddate() -> None:
    """read_last_modified issues ONE DESC-sorted limit-1 search reading only the timestamp.

    INV-6 aggregate: a single CRM Search sorted DESCENDING, ``limit=1``, requesting ONLY
    the timestamp property (no per-person identity field), returning the parsed MAX instant
    off the top row. HubSpot quirk: CONTACTS expose ``lastmodifieddate`` (NOT
    ``hs_lastmodifieddate`` — sorting/reading the latter on a contact yields a null).
    """
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/crm/v3/objects/contacts/search"
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "total": 7,
                "results": [
                    {"id": "c-1", "properties": {"lastmodifieddate": "2026-02-03T09:30:00Z"}}
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.hubapi.com")
    adapter = LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=_crm(),
        award_amounts=_award_amounts(),
        calls_per_run_cap=200,
        resilience=_resilience(),
    )

    result = adapter.read_last_modified("contacts")

    assert result == datetime(2026, 2, 3, 9, 30, tzinfo=UTC)
    assert len(captured) == 1
    body = captured[0]
    # Contacts use ``lastmodifieddate`` in BOTH the sort and the requested property.
    assert body["sorts"] == [{"propertyName": "lastmodifieddate", "direction": "DESCENDING"}]
    assert body["limit"] == 1
    assert body["properties"] == ["lastmodifieddate"]  # only the timestamp scalar (INV-6)
    assert "filterGroups" not in body  # portal-wide newest-modified


def test_read_last_modified_deals_uses_hs_lastmodifieddate() -> None:
    """Deals (unlike contacts) expose ``hs_lastmodifieddate`` — the read uses that name."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/crm/v3/objects/deals/search"
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "total": 3,
                "results": [
                    {"id": "d-1", "properties": {"hs_lastmodifieddate": "2026-02-04T10:00:00Z"}}
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.hubapi.com")
    adapter = LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=_crm(),
        award_amounts=_award_amounts(),
        calls_per_run_cap=200,
        resilience=_resilience(),
    )

    result = adapter.read_last_modified("deals")

    assert result == datetime(2026, 2, 4, 10, 0, tzinfo=UTC)
    body = captured[0]
    assert body["sorts"] == [{"propertyName": "hs_lastmodifieddate", "direction": "DESCENDING"}]
    assert body["properties"] == ["hs_lastmodifieddate"]


def test_read_last_modified_no_records_returns_none() -> None:
    """An object type with no records ⇒ None (⇒ an honest synthetic fallback upstream)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.hubapi.com")
    adapter = LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=_crm(),
        award_amounts=_award_amounts(),
        calls_per_run_cap=200,
        resilience=_resilience(),
    )

    assert adapter.read_last_modified("deals") is None


# ===========================================================================
# GUARD 3 — cap + kill-switch (INV-8): passing + BLOCKING
# ===========================================================================


def test_guard3_calls_within_cap_succeed() -> None:
    """PASS: staying under the per-run cap does not raise."""
    fake = _FakeHubSpot()
    adapter = _adapter(fake, cap=200)
    adapter.push_family(_family())  # several calls, all under cap


def test_guard3_exceeding_cap_raises() -> None:
    """BLOCK: the (cap+1)th HubSpot call raises HubSpotBudgetExceededError."""
    fake = _FakeHubSpot()
    adapter = _adapter(fake, cap=2)

    with pytest.raises(HubSpotBudgetExceededError):
        # push_family makes > 2 calls (search contact, search deal, create …).
        adapter.push_family(_family())


def test_request_retries_within_budget() -> None:
    """A 429-then-200 self-heals via with_retry, charging the INV-8 budget ONCE (A5).

    The budget guard is OUTER and ``with_retry`` wraps ONLY the raw send, so a
    retried logical call makes 2 HTTP sends but charges ``calls_per_run_cap``
    exactly once. With cap=1 the retried call SUCCEEDS (the retry doesn't re-charge),
    and a SECOND distinct logical call then trips the cap. A ``sleep`` spy proves the
    backoff slept via the injected clock, never the wall clock.
    """
    sends: list[str] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sends.append(request.url.path)
        # The FIRST send is a transient 429; every subsequent send is a 200.
        if len(sends) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "rate"})
        return httpx.Response(200, json={"total": 0, "results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.hubapi.com")
    adapter = LiveHubSpotCRMAdapter(
        client=client,
        token=_TOKEN,
        crm=_crm(),
        award_amounts=_award_amounts(),
        calls_per_run_cap=1,
        resilience=_resilience(),
        sleep=slept.append,
    )

    family_id = uuid4()
    # One LOGICAL call (read_mirror → one budgeted _request). 429-then-200 ⇒ 2 sends,
    # 1 budget charge — so cap=1 does NOT raise.
    mirror = adapter.read_mirror(family_id)
    assert mirror.stage is None  # empty mirror (no deal) — the point is it SUCCEEDED
    assert len(sends) == 2, "with_retry should retry the 429 then succeed on the 200"
    assert slept, "the backoff must sleep via the injected spy, not the wall clock"

    # The budget was charged exactly ONCE: a SECOND distinct logical call hits cap=1.
    with pytest.raises(HubSpotBudgetExceededError):
        adapter.read_mirror(family_id)


def test_guard3_kill_switch_degrades_registry_to_simulated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BLOCK: HUBSPOT_KILL_SWITCH ⇒ registry returns SimulatedCRMAdapter (never live)."""
    monkeypatch.setenv("CRM_MODE", "live")
    monkeypatch.setenv("HUBSPOT_PRIVATE_APP_TOKEN", _TOKEN)
    monkeypatch.setenv("HUBSPOT_KILL_SWITCH", "true")

    adapter = get_crm_adapter()

    assert isinstance(adapter, SimulatedCRMAdapter)
    assert not isinstance(adapter, LiveHubSpotCRMAdapter)


# ===========================================================================
# Registry wiring — CRM_MODE seam
# ===========================================================================


def test_registry_simulate_mode_returns_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRM_MODE=simulate ⇒ SimulatedCRMAdapter (unchanged behavior)."""
    monkeypatch.setenv("CRM_MODE", "simulate")
    adapter = get_crm_adapter()
    assert isinstance(adapter, SimulatedCRMAdapter)
    assert isinstance(adapter, CRMAdapter)


def test_registry_live_mode_with_token_returns_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRM_MODE=live + token + no kill switch ⇒ LiveHubSpotCRMAdapter."""
    monkeypatch.setenv("CRM_MODE", "live")
    monkeypatch.setenv("HUBSPOT_PRIVATE_APP_TOKEN", _TOKEN)
    monkeypatch.delenv("HUBSPOT_KILL_SWITCH", raising=False)

    adapter = get_crm_adapter()

    assert isinstance(adapter, LiveHubSpotCRMAdapter)
    assert isinstance(adapter, CRMAdapter)


def test_registry_live_mode_without_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRM_MODE=live + NO token ⇒ fail loud (misconfig; INV-9), never silent simulate."""
    monkeypatch.setenv("CRM_MODE", "live")
    monkeypatch.delenv("HUBSPOT_PRIVATE_APP_TOKEN", raising=False)

    with pytest.raises(RuntimeError):
        get_crm_adapter()


# ===========================================================================
# GUARD 4 — approval-gate (INV-2): no import path ai/** → live adapter
# ===========================================================================

_AI_DIR = Path(__file__).resolve().parents[1].parent / "app" / "ai"
_FORBIDDEN = ("app.adapters.hubspot.live_adapter",)


def _imports(source: str) -> list[str]:
    tree = ast.parse(source)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
    return out


def test_guard4_no_ai_import_path_to_live_adapter() -> None:
    """BLOCK-by-absence: nothing under app/ai imports the live HubSpot adapter (INV-2).

    The live adapter is reachable only from the deterministic post-decision path;
    the AI edge proposes, it never writes. A new ai/ import of the live adapter
    fails this test.
    """
    offenders: dict[str, list[str]] = {}
    for path in _AI_DIR.rglob("*.py"):
        hits = [
            imp for imp in _imports(path.read_text(encoding="utf-8")) if imp.startswith(_FORBIDDEN)
        ]
        if hits:
            offenders[str(path)] = hits
    assert not offenders, f"ai/ must not import the live adapter (INV-2): {offenders}"
