"""Nothing publishes without explicit keep/approve (FR-3.5, P-2, INV-2).

The review gate enforces that a candidate cannot advance past review — into the
library, into brand memory, or anywhere "published" — without an explicit human
`keep`/`approve`. Nothing is auto-published. This is the content analog of INV-2:
the human decision is the sole state-write trigger.

Drives `app/marketing/review_queue.py` and the `POST /content/{proposal_id}/decision`
route, proving:
- a freshly generated candidate is NOT in the library (not published);
- a `discard` decision does NOT publish it;
- only an explicit `keep` publishes it (to the library + brand memory).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.client import AnthropicLLMClient, LLMClient
from app.api import deps
from app.core.params import load_params
from app.core.settings import Settings
from app.main import app
from app.marketing.review_queue import requires_human_decision

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

client = TestClient(app)


def _settings_with_key() -> Settings:
    return Settings(anthropic_api_key="sk-test")


def _candidate_json(copy_text: str) -> str:
    return json.dumps(
        [
            {
                "id": "cc-review-1",
                "batch_id": "batch-review-001",
                "prompt": "Draft on-brand copy.",
                "channel": "instagram",
                "format": "short_caption",
                "concept": "A mastery caption.",
                "copy": copy_text,
                "claims": [],
                "audience_tag": "prospective_parent",
                "lifecycle": "candidate",
                "decision": {"decision": "pending"},
                "provenance": {"generated_by": "llm", "created_at": "2026-01-01T00:00:00+00:00"},
            }
        ]
    )


def _fake_transport(text: str):
    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _llm_client_returning(text: str) -> LLMClient:
    return AnthropicLLMClient(settings=_settings_with_key(), transport=_fake_transport(text))


def _on_brand_judge(score: float = 0.99):
    def judge(record: object, never_rules: list[str]) -> float | None:
        return score

    return judge


def test_requires_human_decision_unit() -> None:
    """A candidate not yet keep/approved cannot be published (pure guard, P-2)."""
    # A pending (un-decided) candidate must not advance.
    assert requires_human_decision("keep") is False
    assert requires_human_decision("approve") is False
    assert requires_human_decision("discard") is True
    assert requires_human_decision("pending") is True


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    deps.reset_observability_log()
    deps.reset_content_library()
    deps.reset_brand_memory_store()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()
    deps.reset_content_library()
    deps.reset_brand_memory_store()


def test_generation_does_not_auto_publish() -> None:
    """A generated candidate is NOT in the library until an explicit keep (FR-3.5)."""
    params = load_params(EXAMPLE_PARAMS)
    app.dependency_overrides[deps.get_params] = lambda: params
    body = "Mastery-based gifted K-8 — see how a GT School day fits your child's pace."
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _candidate_json(body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    # Snapshot the library before generation.
    before = client.get("/content/library").json()
    before_ids = {a["id"] for a in before}

    gen = client.post(
        "/ai/content/generate", json={"prompt": "Draft copy.", "channel": "instagram"}
    ).json()
    assert len(gen["candidates"]) == 1
    proposal_id = gen["candidates"][0]["proposal_id"]

    # The generated candidate is NOT auto-published to the library.
    after_gen = client.get("/content/library").json()
    assert {a["id"] for a in after_gen} == before_ids
    assert all(body not in (a.get("body") or "") for a in after_gen)

    # A discard does NOT publish it either.
    discard = client.post(f"/content/{proposal_id}/decision", json={"action": "discard"})
    assert discard.status_code == 200
    after_discard = client.get("/content/library").json()
    assert {a["id"] for a in after_discard} == before_ids


def test_explicit_keep_publishes() -> None:
    """Only an explicit keep publishes the candidate to the library + memory (FR-3.5)."""
    params = load_params(EXAMPLE_PARAMS)
    app.dependency_overrides[deps.get_params] = lambda: params
    body = "Mastery-based gifted K-8 — a calm, concrete look at a GT School day."
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _candidate_json(body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    gen = client.post(
        "/ai/content/generate", json={"prompt": "Draft copy.", "channel": "instagram"}
    ).json()
    proposal_id = gen["candidates"][0]["proposal_id"]

    before = {a["id"] for a in client.get("/content/library").json()}

    keep = client.post(f"/content/{proposal_id}/decision", json={"action": "keep"})
    assert keep.status_code == 200

    after = client.get("/content/library").json()
    assert len(after) == len(before) + 1
    assert any(body in (a.get("body") or "") for a in after)
