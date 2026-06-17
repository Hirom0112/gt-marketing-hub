"""Recipe-RUN tests — seed attribution (INV-7) + recipe-run through the S4 gate.

S6 marketing recipes (CONTENT_SPEC §8.5; INV-7, P-4; INV-3/INV-4, A-10/A-13).

Two concerns are pinned here:

1. **Seed-level Tom-Babb attribution (INV-7, §8.5).** Every recipe the synthetic
   seed emits (:func:`app.data.synthetic.generate_recipes`) MUST carry a non-empty
   ``attribution`` naming Tom Babb — his open AI-marketing skills are ATTRIBUTED,
   never claimed as the builder's authorship. A negative case proves a blank
   attribution RAISES at the schema boundary (the field is [req], ``min_length=1``);
   stripping authorship is not representable.

2. **Recipe-RUN reuses the EXISTING S4 §9 gate (no new gate, INV-3/INV-4).** A
   recipe is a parameterized template: filling its ``prompt_template`` and feeding
   it into the SAME gated generation path the S4 content engine uses
   (:func:`app.ai.graphs.content_generate.generate_content_batch` →
   :func:`app.core.eval_gate.evaluate_message`). A clean candidate SURFACES; a
   candidate carrying a banned grounding pattern ("4X speed") is WITHHELD/blocked
   (V-2, blocks-not-softens, INV-4); the kill switch degrades with NO live call
   (INV-8). The fake-transport + on-brand-judge + brand-memory store fixtures
   mirror the S4 ``test_generate_validated_only`` / kill-switch tests exactly so
   the same real gate is being exercised.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.cost import RunBudget
from app.ai.graphs.content_generate import ContentBatchOutcome
from app.ai.schemas.brand import MarketingRecipe, RecipeParam, RecipeParamType
from app.ai.schemas.content import Channel
from app.core.params import load_params
from app.core.settings import Settings
from app.data.synthetic import generate_recipes
from app.marketing.recipe_run import RecipeParamMissing, fill_template, run_recipe

EXAMPLE_PARAMS = Path(__file__).resolve().parents[4] / "params" / "params.example.yaml"


# --------------------------------------------------------------------------- #
# 1. Seed-level Tom-Babb attribution (INV-7, §8.5).
# --------------------------------------------------------------------------- #
def test_every_recipe_attributes_tom_babb() -> None:
    """Every seeded recipe carries a non-empty Tom-Babb attribution (INV-7, §8.5).

    The SEED-level guarantee over :func:`generate_recipes` (the schema boundary is
    covered by ``test_marketing_recipe_attribution``). A negative case proves a
    blank ``attribution`` RAISES — a non-Tom-Babb / blank value FAILS closed.
    """
    recipes = generate_recipes()
    assert recipes, "the seed must emit at least one recipe"
    for recipe in recipes:
        assert recipe.attribution, f"{recipe.id} has a blank attribution (INV-7)"
        assert "Tom Babb" in recipe.attribution, (
            f"{recipe.id} attribution must name Tom Babb (INV-7)"
        )

    # Negative: a blank attribution is not representable (schema enforces it).
    base = recipes[0].model_dump()
    base["attribution"] = ""
    with pytest.raises(ValidationError):
        MarketingRecipe.model_validate(base)


# --------------------------------------------------------------------------- #
# 2. fill_template — pure placeholder substitution; missing required RAISES.
# --------------------------------------------------------------------------- #
def _recipe_with_template(template: str, params: list[RecipeParam]) -> MarketingRecipe:
    """A minimal valid recipe with a chosen ``prompt_template`` + parameters."""
    return MarketingRecipe(
        id="recipe-test-fill",
        name="Test fill recipe",
        attribution="Marketing skills attributed to Tom Babb (GT School).",
        description="A recipe for exercising fill_template.",
        parameters=params,
        prompt_template=template,
        version=1,
        provenance={  # type: ignore[arg-type]
            "generated_by": "synthetic_seed",
            "created_at": "2026-06-14T00:00:00Z",
        },
    )


def test_fill_template_substitutes_placeholders() -> None:
    """``{{key}}`` placeholders are replaced with the provided values (LOCKED)."""
    recipe = _recipe_with_template(
        "Write an FAQ for {{targetPrompt}} vs {{competitorSet}}.",
        [
            RecipeParam(
                key="targetPrompt",
                label="Target prompt",
                type=RecipeParamType.STRING,
                required=True,
            ),
            RecipeParam(
                key="competitorSet",
                label="Competitor set",
                type=RecipeParamType.STRING,
                required=True,
            ),
        ],
    )
    filled = fill_template(
        recipe, {"targetPrompt": "best gifted school", "competitorSet": "Acme Online"}
    )
    assert filled == "Write an FAQ for best gifted school vs Acme Online."
    assert "{{" not in filled


def test_fill_template_missing_required_raises() -> None:
    """A missing REQUIRED param RAISES a typed ``RecipeParamMissing`` error."""
    recipe = _recipe_with_template(
        "Write an FAQ for {{targetPrompt}}.",
        [
            RecipeParam(
                key="targetPrompt",
                label="Target prompt",
                type=RecipeParamType.STRING,
                required=True,
            )
        ],
    )
    with pytest.raises(RecipeParamMissing):
        fill_template(recipe, {})


# --------------------------------------------------------------------------- #
# 3. run_recipe — reuses the S4 gate. Fixtures mirror the S4 content tests.
# --------------------------------------------------------------------------- #
def _settings_with_key() -> Settings:
    """Settings with a key ⇒ ``llm_available`` True (still no live call under test)."""
    return Settings(anthropic_api_key="sk-test")


def _candidate_dict(
    *, suffix: str, copy_text: str, claims: list[str] | None = None
) -> dict[str, object]:
    """A schema-conforming ContentCandidate dict the fake transport emits."""
    return {
        "id": f"cc-recipe-{suffix}",
        "batch_id": "batch-recipe-001",
        "prompt": "Run recipe.",
        "channel": "geo",
        "format": "faq_block",
        "concept": f"Concept {suffix}.",
        "copy": copy_text,
        "claims": claims or [],
        "audience_tag": "prospective_parent",
        "lifecycle": "candidate",
        "decision": {"decision": "pending"},
        "provenance": {"generated_by": "recipe", "created_at": "2026-01-01T00:00:00+00:00"},
    }


def _clean_batch_json() -> str:
    """One clean candidate that should PASS the §9 gate."""
    clean = _candidate_dict(
        suffix="clean",
        copy_text="GT School is a mastery-based gifted program. Here is how a day fits your child.",
    )
    return json.dumps([clean])


def _banned_batch_json() -> str:
    """One candidate with a banned grounding multiplier ("4X speed") ⇒ BLOCKED (V-2)."""
    banned = _candidate_dict(
        suffix="banned",
        copy_text="Kids learn at 4X speed with GT School — the fastest gifted program anywhere!",
        claims=["Kids learn at 4X speed"],
    )
    return json.dumps([banned])


def _fake_transport(text: str):
    """A transport returning ``text`` with token counts — never calls out."""

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return (text, 10, 20)

    return transport


def _llm_client_returning(text: str) -> LLMClient:
    """An AnthropicLLMClient wired to a fake transport (key present ⇒ live path)."""
    return AnthropicLLMClient(settings=_settings_with_key(), transport=_fake_transport(text))


def _exploding_transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
    """A transport that MUST never be called on the degraded path."""
    raise AssertionError("live LLM call attempted under kill switch / cost cap")


def _on_brand_judge(score: float = 0.99):
    """A deterministic on-brand judge (V-4 pass)."""

    def judge(record: object, never_rules: list[str]) -> float | None:
        return score

    return judge


def _seed_store(tmp_path: Path, params):
    """A brand-memory store seeded with the synthetic brand memory (S4 fixture)."""
    from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
    from app.data.synthetic import generate_brand_memory

    store = SqliteBrandMemoryStore(
        tmp_path / "brand.sqlite3", weight_step=params.brand_memory.weight_step
    )
    for item in generate_brand_memory():
        store.upsert(item)
    return store


def _geo_recipe() -> MarketingRecipe:
    """The GEO FAQ-builder seed recipe (a GEO-channel, Tom-Babb recipe)."""
    return next(r for r in generate_recipes() if r.output_channel is Channel.GEO)


def test_run_recipe_clean_candidate_surfaces(tmp_path: Path) -> None:
    """A clean candidate from a recipe run SURFACES — it passes the real §9 gate."""
    params = load_params(EXAMPLE_PARAMS)
    settings = _settings_with_key()
    store = _seed_store(tmp_path, params)
    budget = RunBudget.from_config(settings=settings, params=params)
    recipe = _geo_recipe()

    outcome: ContentBatchOutcome = run_recipe(
        recipe,
        {"targetPrompt": "best gifted online school", "competitorSet": "Acme Online"},
        client=_llm_client_returning(_clean_batch_json()),
        budget=budget,
        settings=settings,
        params=params,
        store=store,
        brand_judge=_on_brand_judge(),
    )

    assert len(outcome.surfaced) == 1
    candidate, validation = outcome.surfaced[0]
    assert validation.passed is True
    assert outcome.withheld_count == 0
    assert outcome.degraded is False
    # Tom-Babb attribution is not dropped by the run (INV-7): provenance carries it.
    assert candidate.provenance.recipe_ref == recipe.id


def test_run_recipe_banned_pattern_blocked(tmp_path: Path) -> None:
    """A banned-pattern candidate is WITHHELD, not surfaced — the gate blocks (INV-4)."""
    params = load_params(EXAMPLE_PARAMS)
    settings = _settings_with_key()
    store = _seed_store(tmp_path, params)
    budget = RunBudget.from_config(settings=settings, params=params)
    recipe = _geo_recipe()

    outcome = run_recipe(
        recipe,
        {"targetPrompt": "best gifted online school", "competitorSet": "Acme Online"},
        client=_llm_client_returning(_banned_batch_json()),
        budget=budget,
        settings=settings,
        params=params,
        store=store,
        brand_judge=_on_brand_judge(),
    )

    assert outcome.surfaced == []
    assert outcome.withheld_count == 1
    blocked_validation = outcome.withheld[0].validation
    assert blocked_validation.passed is False


def test_run_recipe_kill_switch_degrades_no_live_call(tmp_path: Path) -> None:
    """Kill switch ⇒ degraded path, NO live call (the exploding transport never runs)."""
    params = load_params(EXAMPLE_PARAMS)
    settings = Settings(anthropic_api_key="sk-test", llm_kill_switch=True)
    assert settings.llm_available is False
    store = _seed_store(tmp_path, params)
    budget = RunBudget.from_config(settings=settings, params=params)
    client = AnthropicLLMClient(settings=settings, transport=_exploding_transport)
    recipe = _geo_recipe()

    outcome = run_recipe(
        recipe,
        {"targetPrompt": "best gifted online school", "competitorSet": "Acme Online"},
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        store=store,
        brand_judge=_on_brand_judge(),
    )

    assert outcome.degraded is True
    for _candidate, validation in outcome.surfaced:
        assert validation.passed is True
