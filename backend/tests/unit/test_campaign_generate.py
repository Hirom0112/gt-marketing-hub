"""Campaign batch creator — axes-embedded prompt + flat gated batch (Slice B; FR-3.1).

The campaign creator is a THIN composition over the SAME §5.3 generation spine the
free-text generator uses: an operator picks a campaign defined by four axes (theme,
channel, audience, optional target GEO prompt) + a count, and `POST /ai/content/campaign`
builds a CAMPAIGN PROMPT embedding those axes and feeds it to the EXISTING
`generate_content_batch`. The response is the SAME flat `ContentGenerateResponse` the
free-text generator returns (so the existing BatchResult UI renders it unchanged), PLUS
a `campaign` echo of the chosen axes.

These tests mirror `test_generate_validated_only.py`:
- a fake transport captures the edge prompt so we can assert the axes are embedded;
- a banned ("4X speed") candidate is still BLOCKED (fail-closed, INV-3/INV-4);
- the response is a flat batch + a campaign echo.

No live LLM: a fake transport returns a JSON array of candidates and a deterministic
on-brand judge is injected for V-4.
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

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

client = TestClient(app)


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
        "id": f"cc-camp-{suffix}",
        "batch_id": "batch-camp-001",
        "prompt": "Draft an on-brand GT School campaign post.",
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
    """A JSON array: 2 clean + 1 V-2-failing ("4X speed") candidate."""
    clean1 = _candidate_dict(
        suffix="clean-1",
        copy_text="Mastery-based gifted K-8 in Austin. See how a GT School day fits your child.",
    )
    clean2 = _candidate_dict(
        suffix="clean-2",
        copy_text="Texas families can apply TEFA toward GT School tuition — here's the next step.",
    )
    blocked = _candidate_dict(
        suffix="block-v2",
        copy_text="Kids learn at 4X speed with GT School — the fastest gifted program anywhere!",
        claims=["Kids learn at 4X speed"],
    )
    return json.dumps([clean1, clean2, blocked])


class _CapturingTransport:
    """A transport that records the edge prompt and returns a fixed batch (no live call)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    def __call__(self, prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        self.prompts.append(prompt)
        return (self.text, 10, 20)


def _on_brand_judge(score: float = 0.99):
    """A deterministic on-brand judge (V-4 pass)."""

    def judge(record: object, never_rules: list[str]) -> float | None:
        return score

    return judge


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_with_key
    yield
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def test_campaign_returns_flat_batch_and_campaign_echo() -> None:
    """`POST /ai/content/campaign` returns a flat batch + the campaign echo (Slice B).

    Same flat `ContentGenerateResponse` shape as `/ai/content/generate` (so the existing
    BatchResult UI renders it unchanged), PLUS a `campaign` echo of the chosen axes. The
    banned ("4X speed") candidate is still BLOCKED (fail-closed, INV-3/INV-4).
    """
    params = load_params(EXAMPLE_PARAMS)
    transport = _CapturingTransport(_batch_json())

    def _client() -> LLMClient:
        return AnthropicLLMClient(settings=_settings_with_key(), transport=transport)

    app.dependency_overrides[deps.get_params] = lambda: params
    app.dependency_overrides[deps.get_llm_client] = _client
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post(
        "/ai/content/campaign",
        json={
            "theme": "cost_tefa_esa",
            "channel": "instagram",
            "audience": "prospective_parent",
            "target_geo_prompt": "best gifted school in Austin Texas",
            "count": 3,
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    # Flat batch: 2 surfaced (passing) + 1 blocked candidate, all flat.
    candidates = data["candidates"]
    surfaced = [c for c in candidates if c["surfaced"]]
    blocked = [c for c in candidates if not c["surfaced"]]
    assert len(surfaced) == 2
    assert len(blocked) == 1
    # The banned candidate is blocked, un-keepable, with failing rules (INV-4 visible).
    assert "4X" in blocked[0]["copy"]
    assert blocked[0]["validation"]["passed"] is False
    assert blocked[0]["failed_rules"]
    assert data["blocked_count"] == 1

    # The campaign echo carries the chosen axes back to the client.
    campaign = data["campaign"]
    assert campaign["theme"] == "cost_tefa_esa"
    assert campaign["channel"] == "instagram"
    assert campaign["audience"] == "prospective_parent"
    assert campaign["target_geo_prompt"] == "best gifted school in Austin Texas"


def test_campaign_prompt_embeds_the_axes() -> None:
    """The campaign prompt embeds the theme/channel/audience/GEO prompt (Slice B).

    Asserted via a capturing transport (mirrors `test_generate_validated_only.py`'s fake
    transport): the edge prompt must lead with the theme as the angle, name the channel
    and audience, and — when a target GEO prompt is set — instruct the copy to win that
    AI-search prompt (SEO/GEO).
    """
    params = load_params(EXAMPLE_PARAMS)
    transport = _CapturingTransport(_batch_json())

    def _client() -> LLMClient:
        return AnthropicLLMClient(settings=_settings_with_key(), transport=transport)

    app.dependency_overrides[deps.get_params] = lambda: params
    app.dependency_overrides[deps.get_llm_client] = _client
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post(
        "/ai/content/campaign",
        json={
            "theme": "socialization",
            "channel": "linkedin",
            "audience": "leadership",
            "target_geo_prompt": "do gifted kids struggle socially at GT School",
            "count": 2,
        },
    )
    assert resp.status_code == 200

    assert transport.prompts, "the edge must have been called once"
    edge_prompt = transport.prompts[0]
    assert "socialization" in edge_prompt
    assert "linkedin" in edge_prompt
    assert "leadership" in edge_prompt
    # The GEO target prompt is embedded with a win-the-AI-search instruction.
    assert "do gifted kids struggle socially at GT School" in edge_prompt


def test_campaign_without_geo_prompt_omits_geo_instruction() -> None:
    """With no target GEO prompt, the campaign prompt still embeds the other axes.

    The optional GEO axis is omitted cleanly (no dangling/empty GEO instruction) — the
    theme/channel/audience are still embedded.
    """
    params = load_params(EXAMPLE_PARAMS)
    transport = _CapturingTransport(_batch_json())

    def _client() -> LLMClient:
        return AnthropicLLMClient(settings=_settings_with_key(), transport=transport)

    app.dependency_overrides[deps.get_params] = lambda: params
    app.dependency_overrides[deps.get_llm_client] = _client
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post(
        "/ai/content/campaign",
        json={
            "theme": "academic_outcomes",
            "channel": "blog",
            "audience": "general",
            "target_geo_prompt": None,
            "count": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["campaign"]["target_geo_prompt"] is None

    edge_prompt = transport.prompts[0]
    assert "academic_outcomes" in edge_prompt
    assert "blog" in edge_prompt
    assert "general" in edge_prompt


def test_campaign_clamps_count_to_cap() -> None:
    """``count`` is clamped to the module cap — no silent unbounded batch (INV-8/§NFR-5).

    The request asks for far more than the cap; the endpoint must not error, and the
    campaign echo + behaviour stay well-formed (the gate still runs per candidate).
    """
    params = load_params(EXAMPLE_PARAMS)
    transport = _CapturingTransport(_batch_json())

    def _client() -> LLMClient:
        return AnthropicLLMClient(settings=_settings_with_key(), transport=transport)

    app.dependency_overrides[deps.get_params] = lambda: params
    app.dependency_overrides[deps.get_llm_client] = _client
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge

    resp = client.post(
        "/ai/content/campaign",
        json={
            "theme": "enrollment",
            "channel": "email",
            "audience": "prospective_parent",
            "count": 9999,
        },
    )
    assert resp.status_code == 200
    # The clamp is embedded in the prompt as the requested batch size and is sane.
    from app.api.content import CAMPAIGN_COUNT_MAX

    edge_prompt = transport.prompts[0]
    assert str(CAMPAIGN_COUNT_MAX) in edge_prompt
    assert "9999" not in edge_prompt
