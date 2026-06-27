"""Bulk action endpoint tests (S12 W2; A-20; INV-2/INV-3/INV-8/INV-9; NFR-6).

Bulk is a THIN batch UX over the existing per-family gated spine — never a new
write path (A-20). These acceptance tests drive the three bulk routes:

  * ``POST /ai/enrollment/bulk-nudge`` — N per-family eval-gated drafts; the
    partition ``{sent, blocked, capped}``. The CRITICAL fail-closed test asserts a
    RED-eval family is ``blocked`` with NO send + NO approve-decision audit record
    (INV-3/4), while passing families are ``sent`` with a note_id.
  * ``POST /enrollment/families/bulk-seed`` — loops the SIMULATED CRM push (INV-9);
    seam DERIVED, not asserted.
  * ``POST /enrollment/families/bulk-dismiss`` — loops ``log_dismiss``; a blank
    reason is rejected 422; dismissed families derive ``recovery_state=dismissed``.

The INV-8 test asserts a bulk-nudge over the per-run cap returns ``capped`` and
does not draft/send beyond the cap. The LLM is never called live: tests OVERRIDE
``get_llm_client`` with a fake transport and ``get_brand_judge`` with a
deterministic judge, exactly as the single-route tests do.
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
from app.core.recovery_state import RecoveryState
from app.core.settings import Settings
from app.data.repository import InMemoryFamilyRepository
from app.main import app
from tests.conftest import install_test_principal_override

client = TestClient(app)


# --------------------------------------------------------------------------- #
# Fixtures / fakes — no live LLM, no live send (mirrors test_ai_endpoints).
# --------------------------------------------------------------------------- #
def _family_ids(n: int) -> list[UUID]:
    """``n`` real seeded family ids, in stable repo order."""
    repo: InMemoryFamilyRepository = deps.get_repository()  # type: ignore[assignment]
    return [f.family_id for f in repo.list_families()[:n]]


def _settings_with_key() -> Settings:
    return Settings(anthropic_api_key="sk-test")


def _fake_transport(text: str):
    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _llm_client_returning(text: str) -> LLMClient:
    return AnthropicLLMClient(settings=_settings_with_key(), transport=_fake_transport(text))


def _on_brand_judge(score: float = 0.99):
    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return score

    return judge


_CLEAN_BODY = "Hello, a quick note about your enrollment and funding next steps."
_BANNED_BODY = "Students learn at 4X speed here — enroll today."


def _proposal_for(action: str, *, body: str, sourced: bool = True):
    """A transport that echoes a schema-conforming proposal for the drafted family.

    The body is fixed; ``family_id`` in the payload is overwritten by the gate from
    the request anyway (the gate reads ``.body``/``.claims``), so a constant id is
    fine — the eval verdict turns on the body's grounding, not the id.
    """
    claims = (
        [{"text": "Your TEFA standard award covers tuition.", "source_ref": "kb:tefa-standard"}]
        if sourced
        else []
    )

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        payload = json.dumps(
            {
                "action": action,
                "family_id": str(uuid4()),
                "body": body,
                "claims": claims,
            }
        )
        return (payload, 10, 20)

    return AnthropicLLMClient(settings=_settings_with_key(), transport=transport)


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    # Restore the conftest token-aware principal shim wiped by the clear() above,
    # WITHOUT touching get_settings_dep (this module manages its own keyed settings).
    install_test_principal_override(settings=False)
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


# --------------------------------------------------------------------------- #
# 1. THE critical fail-closed test — a red-eval family in a bulk set is blocked,
#    with NO send and NO approve-decision audit record (INV-3/4).
# --------------------------------------------------------------------------- #
def test_bulk_nudge_blocks_red_eval_family() -> None:
    """A red-eval family ⇒ blocked w/ failed_rules, NO send, NO approve-audit; pass ⇒ sent.

    Two families: one whose draft body is clean (V-2 pass) and one whose body
    carries a banned "4X speed" claim (V-2 FAIL). The red family must be in
    ``blocked`` with ``v2_grounding`` in ``failed_rules``, and the audit chain for
    its proposal must show its eval ``passed=False`` AND carry NO approve decision
    AND record NO simulated send (fail-closed: blocked is logged, never sent —
    INV-3/4). The clean family is in ``sent`` with a note_id.
    """
    clean_id, red_id = _family_ids(2)
    adapter = SimulatedCRMAdapter()

    # Run the bulk call with a transport that returns the CLEAN body for the clean
    # family and the BANNED body for the red family — keyed off the prompt, which
    # embeds the family's display name (the deal view is grounded per family).
    repo: InMemoryFamilyRepository = deps.get_repository()  # type: ignore[assignment]
    clean_name = repo.get_family(clean_id).family.display_name  # type: ignore[union-attr]

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        body = _CLEAN_BODY if clean_name in prompt else _BANNED_BODY
        sourced = clean_name in prompt
        claims = (
            [{"text": "Your TEFA award covers tuition.", "source_ref": "kb:tefa"}]
            if sourced
            else []
        )
        payload = json.dumps(
            {
                "action": DraftAction.NUDGE.value,
                "family_id": str(uuid4()),
                "body": body,
                "claims": claims,
            }
        )
        return (payload, 10, 20)

    app.dependency_overrides[deps.get_llm_client] = lambda: AnthropicLLMClient(
        settings=_settings_with_key(), transport=transport
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    resp = client.post(
        "/ai/enrollment/bulk-nudge",
        json={"family_ids": [str(clean_id), str(red_id)], "action": "nudge"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # The partition: one sent, one blocked, none capped.
    assert data["counts"] == {"sent": 1, "blocked": 1, "capped": 0}

    sent_ids = {row["family_id"] for row in data["sent"]}
    blocked = {row["family_id"]: row for row in data["blocked"]}
    assert str(clean_id) in sent_ids
    assert str(red_id) in blocked
    assert "v2_grounding" in blocked[str(red_id)]["failed_rules"]
    # The clean family's send carries a note_id (the recorded adapter send id).
    assert all(row["note_id"] for row in data["sent"])

    # --- fail-closed audit assertions for the RED family ---
    # Exactly ONE simulated send was recorded — for the clean family only.
    assert len(adapter.sent_log) == 1

    # Find the red family's logged proposal and assert: eval failed, NO approve.
    proposals = client.get("/proposals").json()
    red_proposals = [p for p in proposals if p["proposal"]["family_id"] == str(red_id)]
    assert len(red_proposals) == 1
    red_audit = red_proposals[0]
    assert red_audit["evals"][0]["passed"] is False  # logged blocked eval (audit)
    assert red_audit["decisions"] == []  # NO approve-decision (no state write)

    # The clean family's proposal DID get an approve decision (the batch approval).
    clean_proposals = [p for p in proposals if p["proposal"]["family_id"] == str(clean_id)]
    assert len(clean_proposals) == 1
    assert any(d["action"] == "approve" for d in clean_proposals[0]["decisions"])


# --------------------------------------------------------------------------- #
# 2. INV-8 — a bulk-nudge exceeding the per-run cap returns capped, never overspends.
# --------------------------------------------------------------------------- #
def test_bulk_nudge_respects_per_run_cap() -> None:
    """Families beyond ``bulk.nudge_per_run_cap`` go to ``capped``, never drafted/sent.

    We pin a tiny cap via a params override and submit more families than the cap.
    Exactly ``cap`` families are eval-drafted (so at most ``cap`` sends recorded),
    and the rest land in ``capped`` — the metered edge is never overspent (INV-8).
    """
    ids = _family_ids(5)
    adapter = SimulatedCRMAdapter()

    # Override params with a cap of 2 (a copy of the real params, cap retuned).
    base = deps.get_params()
    capped_params = base.model_copy(
        update={"bulk": base.bulk.model_copy(update={"nudge_per_run_cap": 2})}
    )
    app.dependency_overrides[deps.get_params] = lambda: capped_params
    app.dependency_overrides[deps.get_llm_client] = lambda: _proposal_for(
        DraftAction.NUDGE.value, body=_CLEAN_BODY
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    resp = client.post(
        "/ai/enrollment/bulk-nudge",
        json={"family_ids": [str(i) for i in ids], "action": "nudge"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Only the first 2 (the cap) are processed; the remaining 3 are capped.
    assert data["counts"]["capped"] == 3
    assert data["counts"]["sent"] + data["counts"]["blocked"] == 2
    # At most `cap` sends recorded — the edge was never driven past the cap (INV-8).
    assert len(adapter.sent_log) <= 2
    # The capped families are exactly the tail of the selection (order preserved).
    assert data["capped"] == [str(i) for i in ids[2:]]


# --------------------------------------------------------------------------- #
# 3. bulk-seed loops the simulated push; every known family captured.
# --------------------------------------------------------------------------- #
def test_bulk_seed_records_all_simulated() -> None:
    """bulk-seed pushes every known family through the SIMULATED adapter (INV-9).

    Each captured row carries the recorded deal id + a DERIVED seam_status. The
    adapter's push log length equals the captured count (records, never sends).
    """
    ids = _family_ids(3)
    adapter = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: adapter

    resp = client.post("/enrollment/families/bulk-seed", json={"family_ids": [str(i) for i in ids]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["counts"]["captured"] == 3
    assert {row["family_id"] for row in data["captured"]} == {str(i) for i in ids}
    assert all(row["deal_id"] for row in data["captured"])
    assert all("seam_status" in row for row in data["captured"])
    # Records, never sends: one recorded push per family.
    assert len(adapter.pushed_log) == 3
    assert all(p.simulated for p in adapter.pushed_log)
    assert data["batch_id"].startswith("bulk-seed-")


# --------------------------------------------------------------------------- #
# 4. bulk-dismiss requires a reason (422 on blank) and derives dismissed.
# --------------------------------------------------------------------------- #
def test_bulk_dismiss_requires_reason() -> None:
    """A blank reason ⇒ 422 (the one new write must say why; A-19)."""
    ids = _family_ids(2)
    resp = client.post(
        "/enrollment/families/bulk-dismiss",
        json={"family_ids": [str(i) for i in ids], "reason": ""},
    )
    assert resp.status_code == 422


def test_bulk_dismiss_derives_dismissed_state() -> None:
    """bulk-dismiss logs a dismiss per family ⇒ each derives recovery_state=dismissed.

    After the bulk dismiss the families' work-queue rows derive
    ``recovery_state=dismissed`` (the dismiss event holds until a re-stall
    supersedes it — and the seeded families' stall_date precedes the dismiss).
    """
    ids = _family_ids(2)
    resp = client.post(
        "/enrollment/families/bulk-dismiss",
        json={"family_ids": [str(i) for i in ids], "reason": "Out of budget this cycle"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["counts"]["dismissed"] == 2
    assert set(data["dismissed"]) == {str(i) for i in ids}

    # The dismissed families derive recovery_state=dismissed — now in the HISTORY
    # scope (they have left the active recovery queue, which the default returns).
    queue = client.get("/work-queue", params={"scope": "history"}).json()
    by_id = {row["family_id"]: row for row in queue}
    for fid in ids:
        assert by_id[str(fid)]["recovery_state"] == RecoveryState.DISMISSED.value


# --------------------------------------------------------------------------- #
# 5. A batch_id is deterministic for the same selection (NFR-6 audit-group tag).
# --------------------------------------------------------------------------- #
def test_bulk_dismiss_batch_id_is_deterministic() -> None:
    """The same family selection ⇒ the same batch_id (a stable audit-group handle)."""
    ids = _family_ids(2)
    body = {"family_ids": [str(i) for i in ids], "reason": "test"}
    first = client.post("/enrollment/families/bulk-dismiss", json=body).json()["batch_id"]
    # Reordered selection yields the same id (sorted internally).
    body_rev = {"family_ids": [str(ids[1]), str(ids[0])], "reason": "test"}
    second = client.post("/enrollment/families/bulk-dismiss", json=body_rev).json()["batch_id"]
    assert first == second
