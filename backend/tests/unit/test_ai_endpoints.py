"""Eval-gated AI action endpoint tests (FR-2.4; ARCH §5.2/§6; INV-2/INV-3/INV-4).

These acceptance tests drive the §5.2 doctrine end-to-end through the API:

  operator requests a draft → deterministic core assembles grounded context →
  AI edge produces a schema-validated proposal → the EVAL GATE runs → the
  proposal + its eval are LOGGED before reaching a human → only on PASS does the
  proposal surface → on approve the send is SIMULATED via the CRM adapter and
  the seam is recomputed.

The LLM is never called live: tests OVERRIDE ``get_llm_client`` with a client
whose injected transport returns canned text, and ``get_brand_judge`` with a
deterministic judge. Together they prove INV-3 (no un-evaled action reaches a
human) and INV-4 (the gate blocks, never softens) at the API boundary, and that
the decision endpoint is the ONLY state-applying path (NFR-6).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.schemas.enrollment_draft import DraftAction
from app.api import deps
from app.core.contact_log import last_contact_at
from app.core.notes import NoteAuthor, NoteKind
from app.core.settings import Settings
from app.data.notes_repository import InMemoryNotesRepository
from app.data.repository import InMemoryFamilyRepository
from app.main import app

client = TestClient(app)


# --------------------------------------------------------------------------- #
# Fixtures / fakes — no live LLM, no live send.
# --------------------------------------------------------------------------- #
def _a_family_id() -> UUID:
    """A real seeded family id from the app's in-memory repository."""
    repo: InMemoryFamilyRepository = deps.get_repository()  # type: ignore[assignment]
    return repo.list_families()[0].family_id


def _settings_with_key() -> Settings:
    """A settings snapshot with a key ⇒ ``llm_available`` True (still no live call)."""
    return Settings(anthropic_api_key="sk-test")


def _fake_transport(text: str):
    """A transport returning ``text`` with token counts — never calls out."""

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _llm_client_returning(text: str) -> LLMClient:
    """An AnthropicLLMClient wired to a fake transport (key present ⇒ live path)."""
    return AnthropicLLMClient(settings=_settings_with_key(), transport=_fake_transport(text))


def _on_brand_judge(score: float = 0.99):
    """A deterministic on-brand judge (V-4 pass)."""

    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return score

    return judge


def _proposal_json(family_id: UUID, *, body: str, sourced: bool = True) -> str:
    """A schema-conforming EnrollmentDraftProposal payload the transport returns."""
    claims = (
        [{"text": "Your TEFA standard award covers tuition.", "source_ref": "kb:tefa-standard"}]
        if sourced
        else []
    )
    return json.dumps(
        {
            "action": DraftAction.EMAIL.value,
            "family_id": str(family_id),
            "body": body,
            "claims": claims,
        }
    )


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    """Reset dependency overrides + the observability singleton around each test.

    The observability log is a module singleton (A-3); a fresh instance per test
    keeps proposal-id assertions independent.
    """
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    # Pin settings to a key-present snapshot so the live (transport) path runs.
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


# --------------------------------------------------------------------------- #
# 1. Draft surfaces ONLY a passing proposal; blocked proposals are still logged.
# --------------------------------------------------------------------------- #
def test_draft_returns_only_passing_proposal() -> None:
    """A clean grounded draft surfaces; a banned-claim draft is blocked yet logged.

    INV-3/INV-4 end-to-end through the API: the passing proposal AND its eval are
    in the observability log (GET /proposals/{id}); the blocked proposal is STILL
    logged (the audit proof) but ``surfaced`` is False with ``v2_grounding`` in
    the failing rules and no usable proposal body.
    """
    family_id = _a_family_id()

    # --- clean draft ⇒ surfaced + logged ---
    body = "Hello, a quick note about your enrollment and funding next steps."
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["surfaced"] is True
    assert data["degraded"] is False
    assert data["proposal"] is not None
    assert data["proposal"]["body"] == body
    proposal_id = data["proposal_id"]

    # The proposal + its eval are in the observability log.
    audit = client.get(f"/proposals/{proposal_id}")
    assert audit.status_code == 200
    audit_body = audit.json()
    assert audit_body["proposal"]["proposal_id"] == proposal_id
    assert len(audit_body["evals"]) >= 1
    assert audit_body["evals"][0]["passed"] is True

    # --- banned-claim draft ⇒ blocked, surfaced False, STILL logged ---
    blocked_body = "Students learn at 4X speed here — enroll today."
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=blocked_body, sourced=False)
    )
    blocked = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    )
    assert blocked.status_code == 200
    blocked_data = blocked.json()
    assert blocked_data["surfaced"] is False
    assert "v2_grounding" in blocked_data["failed_rules"]
    assert blocked_data["proposal"] is None  # no usable body to act on
    blocked_id = blocked_data["proposal_id"]

    # The blocked proposal is STILL logged with its failing eval (INV-4 audit).
    listing = client.get("/proposals")
    assert listing.status_code == 200
    logged_ids = {row["proposal"]["proposal_id"] for row in listing.json()}
    assert blocked_id in logged_ids
    assert proposal_id in logged_ids  # the passing one is logged too
    blocked_audit = client.get(f"/proposals/{blocked_id}").json()
    assert blocked_audit["evals"][0]["passed"] is False


# --------------------------------------------------------------------------- #
# 2. No judge ⇒ even a clean draft is blocked (V-4 deny; fail-closed at the API).
# --------------------------------------------------------------------------- #
def test_blocked_draft_surfaces_none_without_judge() -> None:
    """With no brand judge (no key) a clean draft is V-4-denied ⇒ surfaced False.

    Fail-closed at the API boundary: the default ``get_brand_judge`` returns None
    (no live judge wired), so V-4 denies even a clean, grounded draft.
    """
    family_id = _a_family_id()
    body = "Hello, a quick note about your enrollment and funding next steps."
    # No judge override (default None). Use a key-present client so we still reach
    # the gate (the proposal parses) — V-4 denies because no judge is injected.
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=body)
    )

    resp = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["surfaced"] is False
    assert "v4_onbrand" in data["failed_rules"]
    assert data["proposal"] is None


# --------------------------------------------------------------------------- #
# 3. Approve simulates a send + recomputes the seam; the sole state-write path.
# --------------------------------------------------------------------------- #
def test_decision_approve_simulates_send_and_logs() -> None:
    """Approve ⇒ logged decision + SIMULATED send + recomputed seam; discard ⇒ no send.

    The decision endpoint is the ONLY path applying an AI output: approve records
    a simulated send on the CRM adapter (INV-9) and returns a recomputed seam
    status; discard records the decision with no send.
    """
    family_id = _a_family_id()
    body = "Hello, a quick note about your enrollment and funding next steps."
    adapter = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    draft = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    ).json()
    assert draft["surfaced"] is True
    proposal_id = draft["proposal_id"]

    # --- approve ⇒ simulated send + seam recompute + logged decision ---
    decision = client.post(f"/proposals/{proposal_id}/decision", json={"action": "approve"})
    assert decision.status_code == 200
    dbody = decision.json()
    assert dbody["action"] == "approve"
    assert dbody["seam_status"] is not None
    assert len(adapter.sent_log) == 1  # a SIMULATED send was recorded (INV-9)
    assert adapter.sent_log[0].simulated is True

    # The decision is in the audit chain.
    audit = client.get(f"/proposals/{proposal_id}").json()
    assert any(d["action"] == "approve" for d in audit["decisions"])

    # --- discard ⇒ no send recorded ---
    draft2 = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    ).json()
    pid2 = draft2["proposal_id"]
    discard = client.post(f"/proposals/{pid2}/decision", json={"action": "discard"})
    assert discard.status_code == 200
    assert discard.json()["action"] == "discard"
    assert len(adapter.sent_log) == 1  # unchanged — discard sends nothing
    audit2 = client.get(f"/proposals/{pid2}").json()
    assert any(d["action"] == "discard" for d in audit2["decisions"])


# --------------------------------------------------------------------------- #
# 3b. Approve writes a deterministic auto follow-up note; edit/discard do not.
# --------------------------------------------------------------------------- #
def test_approve_writes_followup_note_and_stamps_contact() -> None:
    """Approve ⇒ a system/state_change follow-up note + a derivable last_contact_at.

    The approve path appends a DETERMINISTIC auto follow-up note (author=system,
    kind=state_change) for the proposal's family (S9 W2; A-8 — not an LLM call),
    and because the approve DECISION is logged the family's ``last_contact_at``
    derives non-None. Discard writes no such note and leaves contact un-stamped.
    """
    family_id = _a_family_id()
    body = "Hello, a quick note about your enrollment and funding next steps."
    notes_store = InMemoryNotesRepository()
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    app.dependency_overrides[deps.get_notes_repository] = lambda: notes_store

    # --- approve ⇒ a follow-up note is appended for the family ---
    draft = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    ).json()
    proposal_id = draft["proposal_id"]
    decision = client.post(f"/proposals/{proposal_id}/decision", json={"action": "approve"})
    assert decision.status_code == 200

    timeline = client.get(f"/families/{family_id}/notes").json()
    followups = [n for n in timeline if n["kind"] == NoteKind.STATE_CHANGE.value]
    assert len(followups) == 1
    note = followups[0]
    assert note["author"] == NoteAuthor.SYSTEM.value
    assert note["family_id"] == str(family_id)
    assert "Email sent (simulated)" in note["body"]
    # The body excerpt comes from the proposal draft body.
    assert body[:60] in note["body"]

    # Recency falls out of the logged approve decision (no new field needed).
    assert last_contact_at(deps.get_observability_log(), family_id) is not None

    # --- discard ⇒ no follow-up note appended ---
    draft2 = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    ).json()
    pid2 = draft2["proposal_id"]
    assert client.post(f"/proposals/{pid2}/decision", json={"action": "discard"}).status_code == 200
    timeline2 = client.get(f"/families/{family_id}/notes").json()
    followups2 = [n for n in timeline2 if n["kind"] == NoteKind.STATE_CHANGE.value]
    assert len(followups2) == 1  # unchanged — discard writes no note


# --------------------------------------------------------------------------- #
# 3c. Approve threads the live note through the adapter and surfaces its id.
# --------------------------------------------------------------------------- #
def test_approve_surfaces_live_note_id_from_adapter() -> None:
    """Approve passes family_id + body to send_message and returns the note id (S10 W3).

    The decision response carries ``note_id`` = the adapter's recorded send id, so
    the cockpit can deep-link the live HubSpot Note. The send_message message
    carries the family_id (so the live adapter resolves contact/deal) and the
    draft body (so the live Note body matches the auto-note). INV-2 holds: the
    live note fires only post-approval, from the deterministic decision route.
    """
    from app.adapters.hubspot.crm_adapter import SendResult, SimulatedCRMAdapter
    from app.core.seam import MirrorState
    from app.data.models import FamilyRecord

    family_id = _a_family_id()
    body = "Hello, a quick note about your enrollment and funding next steps."

    sent_messages: list[dict[str, object]] = []

    class _NoteAdapter(SimulatedCRMAdapter):
        def send_message(self, message: dict[str, object]) -> SendResult:
            sent_messages.append(message)
            return SendResult(simulated=False, recorded_id="live-note-55443322", channel="email")

        def read_mirror(self, family_id: object) -> MirrorState:  # type: ignore[override]
            return MirrorState(stage=None, mirror_updated_at=None)

    adapter = _NoteAdapter()
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    draft = client.post(
        "/ai/enrollment/draft", json={"family_id": str(family_id), "action": "email"}
    ).json()
    decision = client.post(
        f"/proposals/{draft['proposal_id']}/decision", json={"action": "approve"}
    )

    assert decision.status_code == 200
    dbody = decision.json()
    assert dbody["note_id"] == "live-note-55443322"
    # The send carried the family_id (for live id resolution) and the draft body.
    assert len(sent_messages) == 1
    assert sent_messages[0]["family_id"] == str(family_id)
    assert body[:40] in str(sent_messages[0]["body"])
    # Sanity: FamilyRecord import keeps the type checker honest about the seam type.
    assert FamilyRecord is not None


# --------------------------------------------------------------------------- #
# 4. A decision on an unknown proposal_id is a 404.
# --------------------------------------------------------------------------- #
def test_decision_on_unknown_proposal_404() -> None:
    """Deciding on a never-logged proposal_id ⇒ 404 (ARCH §10 causality)."""
    resp = client.post(f"/proposals/{uuid4()}/decision", json={"action": "approve"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 5. A draft for an unknown family is a 404.
# --------------------------------------------------------------------------- #
def test_draft_unknown_family_404() -> None:
    """Drafting for an unknown family ⇒ 404 (the family must exist to be grounded)."""
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(uuid4(), body="x")
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    resp = client.post("/ai/enrollment/draft", json={"family_id": str(uuid4()), "action": "email"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 6. The detail route 404s on an unknown proposal id.
# --------------------------------------------------------------------------- #
def test_get_unknown_proposal_404() -> None:
    """GET /proposals/{id} ⇒ 404 for a never-logged id."""
    assert client.get(f"/proposals/{uuid4()}").status_code == 404
