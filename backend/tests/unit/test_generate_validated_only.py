"""Content-generation graph + API surface only PASSING candidates (FR-3.1; INV-2/3).

The §5.3 marketing-generation doctrine, the content analog of the §5.2 enrollment
flow: operator prompt → deterministic core assembles brand-conditioned context →
the AI edge returns a BATCH of schema-validated candidate proposals → the eval
gate runs on EACH → only PASSING candidates surface; FAILING candidates are
WITHHELD (not shown) but their proposal + failing eval are LOGGED (INV-4 audit).
A malformed candidate is DROPPED at the parse boundary, never coerced (INV-2).

These tests drive both:
- `app.ai.graphs.content_generate.generate_content_batch` (the graph), and
- `POST /ai/content/generate` (the API surface) — a batch where one candidate
  carries a "4X speed" claim that the gate BLOCKS. The blocked candidate is NOT
  in the surfaced set but IS in `GET /proposals` (logged); no state write occurs
  for it.

No live LLM: a fake transport returns a JSON array of candidates, and a
deterministic on-brand judge is injected for V-4.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.cost import RunBudget
from app.ai.graphs.content_generate import ContentBatchOutcome, generate_content_batch
from app.ai.schemas.content import Channel
from app.api import deps
from app.core.params import load_params
from app.core.settings import Settings
from app.main import app

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

client = TestClient(app)


# --------------------------------------------------------------------------- #
# Helpers — no live LLM, no live send.
# --------------------------------------------------------------------------- #
def _settings_with_key() -> Settings:
    """A settings snapshot with a key ⇒ ``llm_available`` True (still no live call)."""
    return Settings(anthropic_api_key="sk-test")


def _candidate_dict(
    *,
    suffix: str,
    copy_text: str,
    channel: str = "instagram",
    claims: list[str] | None = None,
    audience: str = "prospective_parent",
) -> dict[str, object]:
    """A schema-conforming ContentCandidate dict the fake transport emits."""
    return {
        "id": f"cc-gen-{suffix}",
        "batch_id": "batch-gen-001",
        "prompt": "Draft on-brand GT School copy.",
        "channel": channel,
        "format": "short_caption",
        "concept": f"Concept {suffix}.",
        "copy": copy_text,
        "claims": claims or [],
        "audience_tag": audience,
        "lifecycle": "candidate",
        "decision": {"decision": "pending"},
        "provenance": {"generated_by": "llm", "created_at": "2026-01-01T00:00:00+00:00"},
    }


def _batch_json() -> str:
    """A JSON array of candidates: 2 clean + 1 malformed + 1 V-2-failing ("4X speed")."""
    clean1 = _candidate_dict(
        suffix="clean-1",
        copy_text="Mastery-based gifted K-8. See how a GT School day fits your child's pace.",
    )
    clean2 = _candidate_dict(
        suffix="clean-2",
        copy_text="Thanks for your interest in GT School. Here is the next step for your family.",
        channel="email",
    )
    # Malformed: missing required `concept` ⇒ DROPPED at the boundary (INV-2).
    malformed = {
        "id": "cc-gen-malformed",
        "batch_id": "batch-gen-001",
        "prompt": "x",
        "channel": "instagram",
        "format": "short_caption",
        "copy": "Missing a concept field — should be dropped, never coerced.",
        "audience_tag": "general",
        "lifecycle": "candidate",
        "decision": {"decision": "pending"},
        "provenance": {"generated_by": "llm", "created_at": "2026-01-01T00:00:00+00:00"},
    }
    # V-2 BLOCK: an unverifiable performance multiplier ("4X speed").
    blocked = _candidate_dict(
        suffix="block-v2",
        copy_text="Kids learn at 4X speed with GT School — the fastest gifted program anywhere!",
        claims=["Kids learn at 4X speed"],
    )
    return json.dumps([clean1, clean2, malformed, blocked])


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

    def judge(record: object, never_rules: list[str]) -> float | None:
        return score

    return judge


# --------------------------------------------------------------------------- #
# Graph-level: only passing candidates surface; malformed dropped; blocked counted.
# --------------------------------------------------------------------------- #
def test_graph_surfaces_only_passing_candidates(tmp_path: Path) -> None:
    """The graph returns only PASSING candidates, each with a passing ValidationResult.

    The malformed candidate is dropped at the parse boundary (INV-2); the "4X
    speed" candidate fails V-2 and is WITHHELD (counted, not surfaced). Each
    surfaced candidate carries `provenance.brand_memory_refs` from the
    conditioning block.
    """
    from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
    from app.data.synthetic import generate_brand_memory

    params = load_params(EXAMPLE_PARAMS)
    store = SqliteBrandMemoryStore(
        tmp_path / "brand.sqlite3", weight_step=params.brand_memory.weight_step
    )
    for item in generate_brand_memory():
        store.upsert(item)

    settings = _settings_with_key()
    budget = RunBudget.from_config(settings=settings, params=params)
    client_ = _llm_client_returning(_batch_json())

    outcome: ContentBatchOutcome = generate_content_batch(
        "Draft Instagram + email copy.",
        Channel.INSTAGRAM,
        store=store,
        client=client_,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=_on_brand_judge(),
    )

    # Two clean candidates surface; each has a passing ValidationResult.
    assert len(outcome.surfaced) == 2
    for candidate, validation in outcome.surfaced:
        assert validation.passed is True
        # Provenance carries the brand-memory refs that conditioned the batch.
        assert candidate.provenance.brand_memory_refs

    # The "4X speed" candidate was withheld (blocked); the malformed one dropped.
    # withheld_count counts gated-but-failing candidates (the malformed never parsed).
    assert outcome.withheld_count == 1
    assert outcome.degraded is False


# --------------------------------------------------------------------------- #
# API-level: blocked candidate does not surface but IS logged.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def test_api_generate_surfaces_only_passing_blocked_logged(tmp_path: Path) -> None:
    """`POST /ai/content/generate` surfaces only passing candidates; blocked is logged.

    The blocked ("4X speed") candidate is NOT in the surfaced set but IS in
    `GET /proposals` with a FAILING eval (INV-4 audit side). The malformed
    candidate never surfaces and never writes state (INV-2).
    """
    params = load_params(EXAMPLE_PARAMS)
    app.dependency_overrides[deps.get_params] = lambda: params
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(_batch_json())
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post(
        "/ai/content/generate", json={"prompt": "Draft copy.", "channel": "instagram"}
    )
    assert resp.status_code == 200
    data = resp.json()

    # Only passing candidates surface, each with a proposal_id + passing validation.
    surfaced = data["candidates"]
    assert len(surfaced) == 2
    surfaced_copies = {c["candidate"]["copy"] for c in surfaced}
    assert all("4X" not in c for c in surfaced_copies)
    for entry in surfaced:
        assert entry["proposal_id"]
        assert entry["validation"]["passed"] is True

    assert data["blocked_count"] == 1

    # The blocked candidate IS logged (audit) with a FAILING eval.
    listing = client.get("/proposals")
    assert listing.status_code == 200
    rows = listing.json()
    # At least one logged proposal failed its eval (the "4X speed" candidate).
    assert any(any(e["passed"] is False for e in row["evals"]) for row in rows), (
        "the blocked candidate must be logged with a failing eval"
    )
    # And at least two passed (the surfaced ones).
    passed_rows = [row for row in rows if any(e["passed"] for e in row["evals"])]
    assert len(passed_rows) >= 2
