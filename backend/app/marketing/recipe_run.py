"""Recipe-RUN orchestration — a parameterized template through the S4 §9 gate.

A :class:`~app.ai.schemas.brand.MarketingRecipe` (§8.5) is Tom Babb's open
AI-marketing skill modeled as a *runnable, parameterized template* (INV-7). This
module RUNS one:

  1. :func:`fill_template` substitutes the recipe's ``{{key}}`` placeholders with
     the operator-supplied values (a missing REQUIRED param RAISES
     :class:`RecipeParamMissing` — never a silent half-filled prompt);
  2. :func:`run_recipe` feeds that filled prompt into the SAME gated generation
     path the S4 content engine uses
     (:func:`app.ai.graphs.content_generate.generate_content_batch` →
     :func:`app.core.eval_gate.evaluate_message`). There is **no second gate**:
     candidates are validated by the one §9 gate exactly as S4 (INV-3/INV-4,
     A-10/A-13). Only PASSING candidates surface; banned-grounding candidates are
     WITHHELD/blocked (V-2, blocks-not-softens, INV-4); the kill switch / tripped
     budget degrades to persistent brand memory with NO live call (INV-8).

Every surfaced candidate is stamped with the recipe's ``provenance.recipe_ref``
so the audit trail (and INV-7 attribution) points back to the Tom-Babb recipe
that produced it — the run never strips that link.

Purity at the orchestration edge (CLAUDE.md §3), exactly like
``app.ai.graphs.content_generate``: this module consumes the
:class:`~app.ai.client.LLMClient` protocol and the brand-memory store boundary
and reuses the existing gate; it imports **no** ``anthropic`` / ``langgraph``.
Tests inject a fake transport + judge, so no live call ever runs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.ai.graphs.content_generate import (
    ContentBatchOutcome,
    SurfacedCandidate,
    generate_content_batch,
)
from app.ai.schemas.content import Channel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.adapters.brand_memory.base import BrandMemoryStore
    from app.ai.client import LLMClient
    from app.ai.cost import RunBudget
    from app.ai.schemas.brand import BrandRule, MarketingRecipe
    from app.core.eval_gate import BrandJudge
    from app.core.params import Params
    from app.core.settings import Settings

# Matches a ``{{key}}`` placeholder (optional surrounding whitespace inside the
# braces), capturing the bare key. Single-brace ``{key}`` text is left untouched.
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# When a recipe has no ``output_channel``, recipe output defaults to the GEO
# surface (AI-search content) — the recipe seeds (§11.3) are GEO/email-shaped and
# GEO is the §7 default for unbound recipe output. The channel only routes the
# batch through the gate; it never changes the gate's verdict.
_DEFAULT_CHANNEL = Channel.GEO


class RecipeParamMissing(KeyError):
    """A REQUIRED :class:`RecipeParam` had no supplied value at run time.

    Raised by :func:`fill_template` — a recipe is not run with a half-filled
    prompt; the missing required input fails loudly (a typed error the caller
    can surface) rather than leaking an unsubstituted ``{{key}}`` to the edge.
    """


def fill_template(recipe: MarketingRecipe, values: Mapping[str, object]) -> str:
    """Render ``recipe.prompt_template`` by substituting its ``{{key}}`` placeholders.

    Pure: no I/O, no LLM. For each :class:`RecipeParam`, the supplied
    ``values[key]`` (or the param's ``default`` when absent) replaces every
    ``{{key}}`` occurrence. A REQUIRED param with neither a supplied value nor a
    default RAISES :class:`RecipeParamMissing`. Unknown ``{{key}}`` placeholders
    not declared as parameters are left verbatim (the recipe author owns its
    template); single-brace ``{key}`` text is never touched.

    Args:
        recipe: the §8.5 recipe whose ``prompt_template`` is rendered.
        values: operator-supplied param values, keyed by ``RecipeParam.key``.

    Returns:
        The filled prompt string.

    Raises:
        RecipeParamMissing: a required param had no value and no default.
    """
    resolved: dict[str, str] = {}
    for param in recipe.parameters:
        if param.key in values:
            resolved[param.key] = str(values[param.key])
        elif param.default is not None:
            resolved[param.key] = param.default
        elif param.required:
            raise RecipeParamMissing(param.key)
        # An optional param with no value and no default ⇒ leave its placeholder.

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return resolved.get(key, match.group(0))

    return _PLACEHOLDER.sub(_replace, recipe.prompt_template)


def _stamp_recipe_ref(surfaced: SurfacedCandidate, recipe_id: str) -> SurfacedCandidate:
    """Return ``surfaced`` with the candidate's ``provenance.recipe_ref`` set.

    The §9 verdict is unchanged (the candidate already PASSED); only the audit
    link is added so the surfaced candidate points back to the Tom-Babb recipe
    that produced it (NFR-6, INV-7 — the run never strips the attribution link).
    """
    candidate = surfaced.candidate
    provenance = candidate.provenance.model_copy(update={"recipe_ref": recipe_id})
    stamped = candidate.model_copy(update={"provenance": provenance})
    return SurfacedCandidate(candidate=stamped, validation=surfaced.validation)


def run_recipe(
    recipe: MarketingRecipe,
    values: Mapping[str, object],
    *,
    store: BrandMemoryStore,
    client: LLMClient,
    budget: RunBudget,
    settings: Settings,
    params: Params,
    brand_judge: BrandJudge | None = None,
    brand_rules: list[BrandRule] | None = None,
) -> ContentBatchOutcome:
    """Run a recipe: fill its template, then gate the batch through the S4 §9 path.

    Fills ``recipe.prompt_template`` with ``values`` (:func:`fill_template`) and
    delegates to :func:`app.ai.graphs.content_generate.generate_content_batch`
    on the recipe's ``output_channel`` (GEO by default). That reuses the ONE eval
    gate (:func:`app.core.eval_gate.evaluate_message`): only PASSING candidates
    surface; banned-grounding candidates are withheld (INV-4); the kill switch /
    tripped budget degrades with NO live call (INV-8). No new gate is introduced.

    Each surfaced candidate is stamped with ``provenance.recipe_ref = recipe.id``
    so the audit/attribution link back to the Tom-Babb recipe is never dropped
    (INV-7, NFR-6).

    Args:
        recipe: the §8.5 recipe to run (its template + output channel).
        values: operator-supplied param values (see :func:`fill_template`).
        store: the persisted brand-memory store (conditioning source).
        client: the LLM edge seam (a fake transport is injected under test).
        budget: the per-run token/USD governor (INV-8).
        settings: the env seam; bounds the call / drives ``llm_available``.
        params: the loaded params; the eval thresholds read from here (INV-11).
        brand_judge: an INJECTED V-4 brand judge; ``None`` ⇒ V-4 fail-closed.
        brand_rules: optional §8.4 brand rules forwarded to the gate (A-10).

    Returns:
        The frozen :class:`ContentBatchOutcome` from the gated batch, with
        surfaced candidates carrying the recipe ref.
    """
    filled_prompt = fill_template(recipe, values)
    channel = recipe.output_channel if recipe.output_channel is not None else _DEFAULT_CHANNEL

    outcome = generate_content_batch(
        filled_prompt,
        channel,
        store=store,
        client=client,
        budget=budget,
        settings=settings,
        params=params,
        brand_judge=brand_judge,
        brand_rules=brand_rules,
    )

    surfaced = [_stamp_recipe_ref(item, recipe.id) for item in outcome.surfaced]
    return ContentBatchOutcome(
        surfaced=surfaced,
        withheld=outcome.withheld,
        degraded=outcome.degraded,
    )
