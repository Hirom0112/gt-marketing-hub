"""MarketingRecipe attribution tests (S6; CONTENT_SPEC §8.5; INV-7, P-4).

INV-7 (CLAUDE.md §1) / §8.5 ATTRIBUTION (LOCKED): Tom Babb's open AI-marketing
skills are modeled as runnable recipes ATTRIBUTED to Tom Babb — never claimed as
the builder's authorship. Every `MarketingRecipe` carries a non-empty
`attribution` field naming the source. Stripping or blanking the attribution is a
content-integrity violation. These tests pin that contract at the schema boundary:
a blank/missing `attribution` RAISES `ValidationError`; a recipe WITH attribution
validates.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas.brand import MarketingRecipe, RecipeParam


def _valid_param() -> dict[str, object]:
    """A minimal valid RecipeParam (§8.5)."""
    return {
        "key": "targetPrompt",
        "label": "Target AI-search prompt",
        "type": "string",
        "required": True,
    }


def _valid_kwargs() -> dict[str, object]:
    """A minimal, valid set of inputs for a MarketingRecipe (§8.5)."""
    return {
        "id": "01J0RECIPE000000000000000",
        "name": "GEO FAQ builder",
        "attribution": "Tom Babb (open AI-marketing skills)",
        "description": "Builds an FAQ block tuned to win an AI-search prompt.",
        "parameters": [_valid_param()],
        "prompt_template": "Build an FAQ for {{targetPrompt}}.",
        "version": 1,
        "provenance": {
            "generated_by": "synthetic_seed",
            "created_at": "2026-06-14T00:00:00Z",
        },
    }


def test_recipe_with_attribution_validates() -> None:
    """A recipe naming Tom Babb constructs and keeps the attribution (INV-7)."""
    recipe = MarketingRecipe(**_valid_kwargs())  # type: ignore[arg-type]
    assert "Tom Babb" in recipe.attribution
    assert len(recipe.parameters) == 1
    assert isinstance(recipe.parameters[0], RecipeParam)
    assert recipe.parameters[0].required is True


def test_blank_attribution_rejected() -> None:
    """A blank attribution RAISES — authorship may not be stripped (INV-7, §8.5)."""
    blank = _valid_kwargs()
    blank["attribution"] = ""
    with pytest.raises(ValidationError):
        MarketingRecipe(**blank)  # type: ignore[arg-type]


def test_missing_attribution_rejected() -> None:
    """A missing attribution RAISES — the field is [req] (INV-7, §8.5)."""
    missing = _valid_kwargs()
    del missing["attribution"]
    with pytest.raises(ValidationError):
        MarketingRecipe(**missing)  # type: ignore[arg-type]


def test_recipe_rejects_missing_required_field() -> None:
    """Other [req] recipe fields are enforced too (V-1 schema-validity)."""
    for field in ("name", "description", "parameters", "prompt_template", "version"):
        bad = _valid_kwargs()
        del bad[field]
        with pytest.raises(ValidationError):
            MarketingRecipe(**bad)  # type: ignore[arg-type]


def test_recipe_rejects_extra_field() -> None:
    """An unknown extra field is forbidden (extra='forbid')."""
    extra = _valid_kwargs()
    extra["claimed_by_builder"] = True
    with pytest.raises(ValidationError):
        MarketingRecipe(**extra)  # type: ignore[arg-type]


def test_recipe_param_type_enum_closed() -> None:
    """RecipeParam.type is a CLOSED enum (string/number/enum/channel; §8.5)."""
    bad = _valid_param()
    bad["type"] = "datetime"
    with pytest.raises(ValidationError):
        RecipeParam(**bad)  # type: ignore[arg-type]


def test_recipe_is_frozen() -> None:
    """The recipe is immutable once built — attribution cannot be mutated off."""
    recipe = MarketingRecipe(**_valid_kwargs())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        recipe.attribution = ""  # type: ignore[misc]
