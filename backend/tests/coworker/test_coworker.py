"""The M6 coworker gate — the read-proxy + the SOLE gated write (MULTI_AGENT_COCKPIT).

These tests drive :mod:`app.coworker.core` against the REAL app in-process: the
FastAPI ``TestClient`` satisfies the coworker's :class:`HttpClient` seam (same
``.get``/``.post`` → response-with-``.json()`` shape an ``httpx.Client`` has), so the
SAME core that runs behind the MCP transport in prod is exercised here — no live
server, no live LLM, no live send.

The three headline properties (the gate):

1. ``test_check_in_returns_closer_queue_only`` — ``check_in`` composes the FOUR
   blocks AND includes ONLY the closer's assigned families (a different agent's
   families are absent — the M1 owner-scoping holds THROUGH the coworker; INV-5).
2. ``test_draft_block_is_surfaced_verbatim`` — when ``/ai/enrollment/draft`` returns
   ``surfaced=false`` (a banned-claim, eval-red draft), the coworker surfaces the
   ``failed_rules`` VERBATIM, carries NO message body, and does NOT call the
   decision route (never softens/retries — INV-4 fail-closed).
3. ``test_confirm_writes_only_via_decision_route`` — ``confirm`` hits ONLY
   ``POST /proposals/{id}/decision`` (the SOLE write path), returns the
   decision/note id, and the coworker package references NO direct HubSpot/CRM
   client (the source-grep guard) — this is the defining invariant (INV-2/INV-9).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.adapters.hubspot.crm_adapter import SimulatedCRMAdapter
from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.schemas.enrollment_draft import DraftAction
from app.api import deps
from app.core.settings import Settings
from app.coworker import core
from app.data.notes_repository import InMemoryNotesRepository
from app.data.repository import InMemoryFamilyRepository
from app.data.synthetic import SyntheticDataset, generate
from app.main import app

client = TestClient(app)

# The two canonical seeded demo agents (0013_sales_agents.sql) — agent #1 is the
# closer the coworker authenticates as (MULTI_AGENT_COCKPIT §10.3).
CLOSER = UUID("a0000000-0000-4000-8000-000000000001")  # Riley Carter (closer)
OTHER_AGENT = UUID("a0000000-0000-4000-8000-000000000002")  # Jordan Avery (setter)


# --------------------------------------------------------------------------- #
# Fixtures / fakes — no live LLM, no live send (the same posture as test_ai_endpoints).
# --------------------------------------------------------------------------- #
def _settings_with_key() -> Settings:
    """A settings snapshot with a key ⇒ ``llm_available`` True (still no live call)."""
    return Settings(anthropic_api_key="sk-test")


def _fake_transport(text: str):
    """A transport returning ``text`` with token counts — never calls out."""

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _llm_client_returning(text: str) -> LLMClient:
    return AnthropicLLMClient(settings=_settings_with_key(), transport=_fake_transport(text))


def _on_brand_judge(score: float = 0.99):
    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return score

    return judge


def _proposal_json(family_id: UUID, *, body: str, sourced: bool = True) -> str:
    """A schema-conforming EnrollmentDraftProposal the fake transport returns."""
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


def _seed_assigned_repo() -> tuple[InMemoryFamilyRepository, list[UUID], list[UUID]]:
    """An in-memory repo split across the two demo agents (the §2.6 owner-scope seed).

    First three families → the closer; next three → the other agent; the rest left
    unassigned. Returns the repo + the closer's ids + the other agent's ids so the
    test can assert membership/absence THROUGH the coworker.
    """
    base = generate(n=24, seed=42)
    families = list(base.families)
    closer_ids: list[UUID] = []
    other_ids: list[UUID] = []
    for i, fam in enumerate(families):
        if i < 3:
            families[i] = fam.model_copy(update={"assigned_rep_id": CLOSER})
            closer_ids.append(fam.family_id)
        elif i < 6:
            families[i] = fam.model_copy(update={"assigned_rep_id": OTHER_AGENT})
            other_ids.append(fam.family_id)
        else:
            families[i] = fam.model_copy(update={"assigned_rep_id": None})
    dataset = SyntheticDataset(
        families=families,
        leads=list(base.leads),
        app_forms=list(base.app_forms),
        enrollment_forms=list(base.enrollment_forms),
        community_profiles=list(base.community_profiles),
        students=list(base.students),
        student_app_forms=list(base.student_app_forms),
        student_enrollment_forms=list(base.student_enrollment_forms),
    )
    return InMemoryFamilyRepository(dataset), closer_ids, other_ids


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


# --------------------------------------------------------------------------- #
# A recording HTTP client that wraps the TestClient and logs every call URL, so a
# test can prove WHICH routes the coworker hit (the write-path invariant).
# --------------------------------------------------------------------------- #
class _RecordingClient:
    """Wraps the TestClient, recording (verb, url) for every call the coworker makes."""

    def __init__(self, inner: TestClient) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []

    def get(self, url, *, headers=None):  # type: ignore[no-untyped-def]
        self.calls.append(("GET", url))
        return self._inner.get(url, headers=headers)

    def post(self, url, *, json=None, headers=None):  # type: ignore[no-untyped-def]
        self.calls.append(("POST", url))
        return self._inner.post(url, json=json, headers=headers)


# --------------------------------------------------------------------------- #
# 1. /check-in returns the CLOSER's queue only — owner-scoping holds through the proxy.
# --------------------------------------------------------------------------- #
def test_check_in_returns_closer_queue_only() -> None:
    """``check_in`` composes the four blocks from the closer's OWN book only (IDOR).

    The coworker authenticates as the closer; the other agent's families must be
    ABSENT from every block (the M1 owner clamp holds through the second client).
    """
    repo, closer_ids, other_ids = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo

    briefing = core.check_in(client, str(CLOSER), top_n=50)

    # The four blocks are present (the briefing shape).
    assert briefing.agent_id == str(CLOSER)
    seen = {row.family_id for row in briefing.who_to_contact}

    # ONLY the closer's families surface — the other agent's are absent (IDOR).
    closer_str = {str(fid) for fid in closer_ids}
    other_str = {str(fid) for fid in other_ids}
    assert seen <= closer_str, "the coworker must surface only the closer's own families"
    for fid in other_str:
        assert fid not in seen, "a foreign agent's family must NOT leak through the coworker"

    # Block scoping holds across every per-family block too (no foreign family id).
    for block in (briefing.pending_notes, briefing.hygiene_gaps, briefing.voucher_clocks):
        for item in block:
            assert item.family_id not in other_str, "foreign family leaked into a check-in block"
            assert item.family_id in closer_str

    # The four blocks are actually composed (voucher-clocks always populate per top
    # family; the structured briefing carries all four fields).
    assert briefing.voucher_clocks, "voucher-clocks block must be composed from funding reads"
    assert all(vc.program for vc in briefing.voucher_clocks)


# --------------------------------------------------------------------------- #
# 2. A blocked (surfaced=false) draft is surfaced VERBATIM — never softened (INV-4).
# --------------------------------------------------------------------------- #
def test_draft_block_is_surfaced_verbatim() -> None:
    """A banned-claim draft blocks ⇒ the coworker shows failed_rules verbatim, no body.

    INV-4 fail-closed: the coworker NEVER rewrites/softens/retries a blocked draft,
    and never confirms it (no write off a block).
    """
    repo, closer_ids, _ = _seed_assigned_repo()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    family_id = closer_ids[0]

    # A banned "4X speed" claim ⇒ the grounding gate blocks it (surfaced=false).
    blocked_body = "Students learn at 4X speed here — enroll today."
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=blocked_body, sourced=False)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    recorder = _RecordingClient(client)
    outcome = core.draft(recorder, str(CLOSER), str(family_id), action="email")

    # The block is surfaced VERBATIM: not surfaced, failed_rules carried through,
    # and NO message body (the coworker fabricates nothing — INV-4).
    assert outcome.surfaced is False
    assert outcome.message is None, "a blocked draft must carry NO message body (never softened)"
    assert outcome.proposal is None
    assert "v2_grounding" in outcome.failed_rules, "the gate's rule must pass through verbatim"
    assert outcome.proposal_id, "a proposal_id is still returned (it was logged)"

    # The blocked body never leaks back through the coworker (no soften/rewrite).
    assert "4X" not in (outcome.message or ""), "the coworker must not echo a softened claim"

    # And the coworker did NOT call the decision route off a block (no write).
    posted = [url for verb, url in recorder.calls if verb == "POST"]
    assert not any("/decision" in url for url in posted), "no write may follow a blocked draft"


# --------------------------------------------------------------------------- #
# 3. confirm() writes ONLY through the decision route — the defining invariant.
# --------------------------------------------------------------------------- #
def test_confirm_writes_only_via_decision_route() -> None:
    """``confirm`` routes the ONE write through the decision route — never HubSpot direct.

    (a) it returns the decision/note id from ``POST /proposals/{id}/decision``;
    (b) the ONLY POST the coworker makes for the write is that decision route; and
    (c) a source grep of the coworker package finds NO direct HubSpot/CRM client.
    """
    repo, closer_ids, _ = _seed_assigned_repo()
    notes_store = InMemoryNotesRepository()
    crm = SimulatedCRMAdapter()
    app.dependency_overrides[deps.get_repository] = lambda: repo
    app.dependency_overrides[deps.get_notes_repository] = lambda: notes_store
    app.dependency_overrides[deps.get_crm_adapter_dep] = lambda: crm
    family_id = closer_ids[0]

    # A clean grounded draft ⇒ surfaced ⇒ confirmable.
    body = "Hello, a quick note about your enrollment and funding next steps."
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _proposal_json(family_id, body=body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    recorder = _RecordingClient(client)
    drafted = core.draft(recorder, str(CLOSER), str(family_id), action="email")
    assert drafted.surfaced is True

    result = core.confirm(recorder, str(CLOSER), drafted.proposal_id, decision="approve")

    # (a) the decision route returned the decision + the recorded send id.
    assert result.action == "approve"
    assert result.proposal_id == drafted.proposal_id
    assert result.note_id is not None, "the decision route returns the recorded send/note id"

    # The audit spine recorded the write with a timestamp (NFR-6) — the coworker
    # reports the timestamp via the audit, keeping confirm() to the one write call.
    audit = client.get(f"/proposals/{drafted.proposal_id}").json()
    assert audit["decisions"], "the decision was logged to the audit spine (timestamped)"
    assert audit["decisions"][0]["action"] == "approve"

    # (b) the SOLE write the coworker made is the decision route — no other POST
    # mutates state (the draft POST is the eval-gated proposal, not a state write;
    # there is NO HubSpot/CRM route in the coworker's call list).
    posts = [url for verb, url in recorder.calls if verb == "POST"]
    decision_posts = [u for u in posts if "/decision" in u]
    assert len(decision_posts) == 1, "exactly one write — the decision route"
    for url in posts:
        assert "/decision" in url or url == "/ai/enrollment/draft", (
            f"the coworker made an unexpected POST: {url}"
        )
        # Never a HubSpot/CRM endpoint directly.
        assert "hubspot" not in url.lower() and "/crm" not in url.lower()


def test_coworker_package_has_no_direct_hubspot_or_crm_client() -> None:
    """Source guard: the coworker package imports NO HubSpot/CRM client/adapter.

    The defining invariant (MULTI_AGENT_COCKPIT §2.5; INV-2/INV-9) made structural:
    the coworker's ONLY write seam is the decision route, so the package must never
    reference a CRM adapter, a HubSpot client, or a push_family/push_student call.
    """
    pkg = Path(core.__file__).parent
    banned = re.compile(
        r"\b(hubapi|hubspot|crm_adapter|CRMAdapter|push_family|push_student|get_crm_adapter)\b",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for path in pkg.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        # Strip docstrings/comments cheaply: the invariant is legitimately NAMED in
        # prose, so only flag a banned token on a line that is not a comment and not
        # inside the module docstring. We check executable references by excluding
        # lines that are pure comments; the prose lives in docstrings which we drop.
        in_doc = src.split('"""')
        executable = "".join(in_doc[0::2])  # segments OUTSIDE the docstring quotes
        code_lines = [line for line in executable.splitlines() if not line.lstrip().startswith("#")]
        if banned.search("\n".join(code_lines)):
            offenders.append(path.name)
    assert not offenders, f"coworker package references a HubSpot/CRM client in: {offenders}"
