"""A kept candidate from a CAMPAIGN batch carries its campaign theme + GEO prompt as tags.

`campaign-tagging-on-keep` (the §5.3 keep/library write path, FR-3.4/3.5; INV-2):
when a candidate is generated from `POST /ai/content/campaign` (which carries a
`theme` / `channel` / `audience` / optional `target_geo_prompt`) and the operator
then KEEPS it, the kept :class:`LibraryAsset` must carry its campaign theme and
target GEO prompt as namespaced tags (``campaign:<theme>`` / ``geo:<prompt>``) and
be findable by them in library search.

INV-2 spine: the keep endpoint does NOT trust client input — it rebuilds the
candidate from the logged proposal payload. So the axes are persisted INTO the
logged proposal at GENERATE time and read back at KEEP time. A candidate from the
NON-campaign `/ai/content/generate` route has no axes ⇒ keep produces NO campaign
tags (graceful, no crash). The keep-refused-on-unpassed-eval behavior (INV-3) is
unchanged.
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
    return Settings(anthropic_api_key="sk-test")


def _candidate_json(copy_text: str) -> str:
    return json.dumps(
        [
            {
                "id": "cc-camp-keep-1",
                "batch_id": "batch-camp-keep-001",
                "prompt": "Draft an on-brand GT School campaign post.",
                "channel": "instagram",
                "format": "short_caption",
                "concept": "A mastery campaign caption.",
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


def _wire(body: str) -> None:
    params = load_params(EXAMPLE_PARAMS)
    app.dependency_overrides[deps.get_params] = lambda: params
    app.dependency_overrides[deps.get_llm_client] = lambda: _llm_client_returning(
        _candidate_json(body)
    )
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge


def test_keep_from_campaign_tags_theme_and_geo() -> None:
    """A kept campaign candidate carries ``campaign:<theme>`` + ``geo:<prompt>`` tags."""
    body = "Mastery-based gifted K-8 in Austin. See how a GT School day fits your child."
    _wire(body)

    gen = client.post(
        "/ai/content/campaign",
        json={
            "theme": "cost_tefa_esa",
            "channel": "instagram",
            "audience": "prospective_parent",
            "target_geo_prompt": "best gifted school in Austin Texas",
            "count": 1,
        },
    ).json()
    surfaced = [c for c in gen["candidates"] if c["surfaced"]]
    proposal_id = surfaced[0]["proposal_id"]

    keep = client.post(f"/content/{proposal_id}/decision", json={"action": "keep"})
    assert keep.status_code == 200
    asset = keep.json()["library_asset"]
    assert asset is not None

    # The existing audience/channel tags are preserved.
    assert "prospective_parent" in asset["tags"]
    assert "instagram" in asset["tags"]
    # The campaign axes are now tagged with their namespaced prefixes.
    assert "campaign:cost_tefa_esa" in asset["tags"]
    assert "geo:best gifted school in Austin Texas" in asset["tags"]
    # And folded (lower-cased) into the search index.
    assert "campaign:cost_tefa_esa" in asset["search_text"]
    assert "geo:best gifted school in austin texas" in asset["search_text"]


def test_library_search_finds_kept_campaign_asset_by_theme() -> None:
    """The kept campaign asset is findable by its campaign theme in library search (FR-3.4)."""
    body = "Texas families can apply TEFA toward GT School tuition — here's the next step."
    _wire(body)

    gen = client.post(
        "/ai/content/campaign",
        json={
            "theme": "cost_tefa_esa",
            "channel": "instagram",
            "audience": "prospective_parent",
            "target_geo_prompt": "best gifted school in Austin Texas",
            "count": 1,
        },
    ).json()
    proposal_id = [c for c in gen["candidates"] if c["surfaced"]][0]["proposal_id"]
    keep = client.post(f"/content/{proposal_id}/decision", json={"action": "keep"})
    assert keep.status_code == 200

    # Findable by the campaign theme tag.
    by_tag = client.get("/content/library", params={"tag": "campaign:cost_tefa_esa"})
    assert by_tag.status_code == 200
    assert any(body in (a.get("body") or "") for a in by_tag.json())

    # Findable by the theme as free text over search_text.
    by_text = client.get("/content/library", params={"q": "campaign:cost_tefa_esa"})
    assert by_text.status_code == 200
    assert any(body in (a.get("body") or "") for a in by_text.json())


def test_keep_from_noncampaign_route_has_no_campaign_tags() -> None:
    """A kept candidate from `/ai/content/generate` has NO campaign/geo tags (graceful)."""
    body = "Mastery-based gifted K-8 — a calm, concrete look at a GT School day."
    _wire(body)

    gen = client.post(
        "/ai/content/generate", json={"prompt": "Draft copy.", "channel": "instagram"}
    ).json()
    proposal_id = gen["candidates"][0]["proposal_id"]

    keep = client.post(f"/content/{proposal_id}/decision", json={"action": "keep"})
    assert keep.status_code == 200
    asset = keep.json()["library_asset"]
    assert asset is not None

    # The existing tags are present; no campaign/geo tags appear (no crash).
    assert "prospective_parent" in asset["tags"]
    assert "instagram" in asset["tags"]
    assert not any(t.startswith("campaign:") for t in asset["tags"])
    assert not any(t.startswith("geo:") for t in asset["tags"])
