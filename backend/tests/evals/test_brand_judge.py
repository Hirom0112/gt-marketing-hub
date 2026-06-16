"""V-4 brand-judge eval — the golden-set IS the red test (CLAUDE §4.2, INV-3/INV-4).

The real V-4 judge (`app/ai/brand_judge.py`) replaces the None stub so V-4 no
longer ALWAYS denies in dev (no key). Per CLAUDE §4.2 a new AI feature's
golden-set eval is its red test: the heuristic judge must score genuinely
on-brand GT copy ABOVE the params floor (so V-4 PASSES the good) and off-brand /
banned / bland copy BELOW it (so V-4 DENIES the bad). The gate's deterministic
V-1/V-2/V-3 still block banned patterns regardless of the judge (INV-4).

Every threshold reads from the committed example params (no magic number —
INV-11), mirroring `test_message_safety_grounding.py`. No live call: the
heuristic path is offline, and the LLM path uses an injected fake transport.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.ai.brand_judge import BrandJudge, heuristic_brand_score
from app.ai.client import AnthropicLLMClient, LLMResult
from app.ai.cost import RunBudget
from app.core.eval_gate import check_v4
from app.core.params import Params, load_params
from app.core.settings import Settings

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
GOLDEN = Path(__file__).resolve().parent / "golden" / "brand_judge.jsonl"


@dataclass(frozen=True)
class _Record:
    """A minimal :class:`~app.core.eval_gate.GatedRecord` — content-candidate shape."""

    copy_text: str
    claims: list[str] = field(default_factory=list)


@pytest.fixture
def params() -> Params:
    return load_params(EXAMPLE_PARAMS)


@pytest.fixture
def settings_no_key() -> Settings:
    s = Settings()
    assert s.llm_available is False
    return s


@pytest.fixture
def settings_with_key() -> Settings:
    s = Settings(anthropic_api_key="sk-test-not-real")
    assert s.llm_available is True
    return s


def _rows() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# 1. Golden-set red test — heuristic scores on-brand ABOVE, off-brand BELOW.
# --------------------------------------------------------------------------- #
def test_heuristic_golden_set_separates_on_off_brand(params: Params) -> None:
    rows = _rows()
    assert len(rows) >= 8, "golden set must hold a substantive on/off-brand mix"
    on = [r for r in rows if r["expected_onbrand"] is True]
    off = [r for r in rows if r["expected_onbrand"] is False]
    assert on and off, "golden set must contain BOTH on-brand and off-brand rows"

    floor = params.eval_thresholds.message_safety_grounding.min_grounding

    for row in on:
        score = heuristic_brand_score(_Record(copy_text=row["text"]), [])
        assert score >= floor, f"on-brand row scored below floor: {row['label']} ({score})"
    for row in off:
        score = heuristic_brand_score(_Record(copy_text=row["text"]), [])
        assert score < floor, f"off-brand row scored at/above floor: {row['label']} ({score})"


# --------------------------------------------------------------------------- #
# 2. The judge drives V-4: on-brand PASSES, off-brand/bland DENIES (fail-closed).
# --------------------------------------------------------------------------- #
def test_judge_passes_good_denies_bad_via_v4(params: Params, settings_no_key: Settings) -> None:
    judge = BrandJudge(settings=settings_no_key, params=params)
    rows = _rows()
    for row in rows:
        verdict, score = check_v4(
            _Record(copy_text=row["text"]),
            settings=settings_no_key,
            params=params,
            brand_judge=judge,
        )
        if row["expected_onbrand"]:
            assert verdict == "pass", f"on-brand wrongly denied: {row['label']}"
            assert score is not None  # passed rows carry the judge's proposed score.
        else:
            assert verdict == "fail", f"off-brand wrongly passed: {row['label']}"
            # A denied row is blocked EITHER by the gate's deterministic never-rule
            # pre-check (score None, judge never consulted — INV-4) OR by the judge
            # scoring below the floor (a real score). Both are fail-closed.


# --------------------------------------------------------------------------- #
# 3. Banned never-rule phrase ⇒ heuristic penalized below the floor.
# --------------------------------------------------------------------------- #
def test_never_rule_phrase_sinks_score(params: Params) -> None:
    floor = params.eval_thresholds.message_safety_grounding.min_grounding
    record = _Record(
        copy_text="GT School is a mastery-based gifted K-8 program for your family and child."
    )
    clean = heuristic_brand_score(record, [])
    assert clean >= floor  # on-brand without the banned phrase clears the floor.
    # The SAME on-brand copy, now containing an active never-phrase, sinks below.
    dirty = _Record(copy_text=record.copy_text + " act now or lose your spot")
    assert heuristic_brand_score(dirty, ["act now or lose your spot"]) < floor


# --------------------------------------------------------------------------- #
# 4. LLM path — an available edge with a fake transport returns the model score.
# --------------------------------------------------------------------------- #
def test_llm_backed_judge_uses_model_score(params: Params, settings_with_key: Settings) -> None:
    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return ("0.98", 10, 2)  # model replies a high on-brand score.

    client = AnthropicLLMClient(settings=settings_with_key, transport=transport)
    judge = BrandJudge(settings=settings_with_key, params=params, client=client)
    score = judge(_Record(copy_text="Bland generic copy with no brand signal at all."), [])
    # Even though the copy is bland (heuristic would deny), the LLM score wins.
    assert score == pytest.approx(0.98)


# --------------------------------------------------------------------------- #
# 5. LLM degraded reply ⇒ fall back to the heuristic (never a silent pass).
# --------------------------------------------------------------------------- #
def test_llm_degraded_falls_back_to_heuristic(params: Params, settings_with_key: Settings) -> None:
    class _DegradedClient:
        def complete(self, prompt: str, *, max_tokens: int, budget: RunBudget) -> LLMResult:
            return LLMResult(text="(unavailable)", degraded=True)

    judge = BrandJudge(settings=settings_with_key, params=params, client=_DegradedClient())
    floor = params.eval_thresholds.message_safety_grounding.min_grounding
    # Off-brand bland copy: the heuristic fallback must keep it BELOW the floor.
    assert judge(_Record(copy_text="Hello, a quick note about next steps."), []) < floor
    # On-brand copy: the heuristic fallback clears the floor.
    on = "GT School is a mastery-based gifted K-8 program for your family and child to enroll."
    assert judge(_Record(copy_text=on), []) >= floor


# --------------------------------------------------------------------------- #
# 6. Unparseable model reply ⇒ heuristic fallback (no silent pass).
# --------------------------------------------------------------------------- #
def test_llm_unparseable_reply_falls_back(params: Params, settings_with_key: Settings) -> None:
    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        return ("the score is high", 10, 2)  # no float ⇒ unparseable.

    client = AnthropicLLMClient(settings=settings_with_key, transport=transport)
    judge = BrandJudge(settings=settings_with_key, params=params, client=client)
    floor = params.eval_thresholds.message_safety_grounding.min_grounding
    assert judge(_Record(copy_text="Hello, a quick note about next steps."), []) < floor
