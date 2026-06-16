"""Cross-run DAILY cost cap accumulator + kill-switch trip (NFR-5; INV-8; INV-11).

The per-run governor (`app/ai/cost.py::RunBudget`) hard-caps a SINGLE run. The
DAILY ceiling (`COST_DAILY_USD_CAP`, `Settings.cost_daily_usd_cap`) is a SEPARATE
cross-run mechanism: cumulative Anthropic spend per day, on breach the global
kill switch trips and every AI feature degrades deterministically (TECH_STACK §6.2).

The runtime is stateless (Lambda/Mangum; ARCH §12), so daily spend is DERIVED from
the append-only observability spine — exactly like `core/contact_log.last_contact_at`
and `core/scoreboard` derive state from the log — not from a module-global counter.
Each completed live run stamps its `budget.usd_spent` onto the proposal it logs;
`daily_usd_spent` sums those over a day; the AI endpoints fail closed BEFORE a live
call once today's logged spend reaches the cap (reusing the per-run kill switch's
degrade path — a PRE-TRIPPED budget, no second mechanism).

These are the RED tests:

1. pure aggregator sums only the target day's `usd_spent` (`day` injected);
2. the additive `usd_spent` field defaults to 0.0 (back-compat) and round-trips;
3. BLOCKING fail-closed: with the day's logged spend at/over the cap the next draft
   makes NO live call (an exploding transport must never fire) and degrades;
4. drift: the gate reads `settings.cost_daily_usd_cap`, never a literal.

Params come from the committed `params/params.example.yaml` (mirrors the cost-cap
suite). No live Anthropic call ever runs (fake/exploding transports throughout).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.ai.client import AnthropicLLMClient, LLMClient
from app.ai.cost import run_budget_for_today
from app.ai.schemas.enrollment_draft import DraftAction
from app.api import deps
from app.core.daily_spend import daily_usd_spent
from app.core.params import load_params
from app.core.settings import Settings
from app.main import app
from app.observability.log_store import InMemoryObservabilityLog, ProposalRecord

EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"

_DAY = date(2026, 6, 16)
_OTHER_DAY = date(2026, 6, 15)


def _params():
    return load_params(EXAMPLE_PARAMS)


def _at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 1. Pure aggregator — sums only the target day's usd_spent (day injected).
# --------------------------------------------------------------------------- #
def test_daily_usd_spent_sums_only_target_day() -> None:
    """`daily_usd_spent` sums `usd_spent` over the target day's proposals only."""
    log = InMemoryObservabilityLog()
    # Two runs today + one yesterday + one today with zero spend.
    log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        usd_spent=3.50,
        created_at=_at(_DAY, 9),
    )
    log.log_proposal(
        proposal_id=uuid4(),
        flow="content_generate",
        schema_version="1",
        payload={},
        usd_spent=1.25,
        created_at=_at(_DAY, 17),
    )
    log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        usd_spent=9.99,
        created_at=_at(_OTHER_DAY, 11),
    )
    log.log_proposal(
        proposal_id=uuid4(),
        flow="close_tips",
        schema_version="1",
        payload={},
        usd_spent=0.0,
        created_at=_at(_DAY, 20),
    )

    assert daily_usd_spent(log, day=_DAY) == pytest.approx(4.75)
    assert daily_usd_spent(log, day=_OTHER_DAY) == pytest.approx(9.99)
    assert daily_usd_spent(log, day=date(2099, 1, 1)) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 2. Additive field — defaults to 0.0 (back-compat) and round-trips.
# --------------------------------------------------------------------------- #
def test_proposal_record_usd_spent_defaults_zero() -> None:
    """A proposal logged WITHOUT `usd_spent` defaults to 0.0 (back-compat)."""
    log = InMemoryObservabilityLog()
    record = log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        created_at=_at(_DAY),
    )
    assert record.usd_spent == 0.0
    # The model itself validates with the field omitted.
    bare = ProposalRecord(
        proposal_id=uuid4(),
        flow="x",
        schema_version="1",
        payload={},
        created_at=_at(_DAY),
    )
    assert bare.usd_spent == 0.0


def test_proposal_record_usd_spent_round_trips() -> None:
    """A proposal logged WITH `usd_spent` carries it on the record."""
    log = InMemoryObservabilityLog()
    record = log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        usd_spent=2.34,
        created_at=_at(_DAY),
    )
    assert record.usd_spent == pytest.approx(2.34)


# --------------------------------------------------------------------------- #
# 3 + 4. run_budget_for_today — pre-trips at/over the daily cap, reads settings.
# --------------------------------------------------------------------------- #
def test_run_budget_for_today_pretrips_at_daily_cap() -> None:
    """At/over `cost_daily_usd_cap` the returned budget is pre-tripped (fail-closed)."""
    params = _params()
    cap = 5.00
    settings = Settings(anthropic_api_key="sk-test", cost_daily_usd_cap=cap)
    log = InMemoryObservabilityLog()
    log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        usd_spent=cap,  # exactly at the cap ⇒ tripped (>=)
        created_at=_at(_DAY),
    )
    budget = run_budget_for_today(settings=settings, params=params, log=log, today=_DAY)
    assert budget.tripped


def test_run_budget_for_today_under_cap_not_tripped() -> None:
    """Under the daily cap the budget is the normal per-run budget (not tripped)."""
    params = _params()
    settings = Settings(anthropic_api_key="sk-test", cost_daily_usd_cap=100.0)
    log = InMemoryObservabilityLog()
    log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        usd_spent=1.00,
        created_at=_at(_DAY),
    )
    budget = run_budget_for_today(settings=settings, params=params, log=log, today=_DAY)
    assert not budget.tripped


def test_run_budget_for_today_reads_settings_cap_not_a_literal() -> None:
    """DRIFT: the same logged spend trips under a low cap, not under a high one."""
    params = _params()
    log = InMemoryObservabilityLog()
    log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        usd_spent=10.00,
        created_at=_at(_DAY),
    )
    low = Settings(anthropic_api_key="sk-test", cost_daily_usd_cap=5.00)
    high = Settings(anthropic_api_key="sk-test", cost_daily_usd_cap=50.00)
    assert run_budget_for_today(settings=low, params=params, log=log, today=_DAY).tripped
    assert not run_budget_for_today(settings=high, params=params, log=log, today=_DAY).tripped


# --------------------------------------------------------------------------- #
# 3 (headline). BLOCKING — the daily cap trips the kill switch at the endpoint.
# --------------------------------------------------------------------------- #
def _settings_low_daily_cap() -> Settings:
    """Key present (so unavailability is NOT the reason) + a tiny daily cap."""
    return Settings(anthropic_api_key="sk-test", cost_daily_usd_cap=2.00)


def _exploding_client() -> LLMClient:
    """A live-keyed client whose transport must NEVER fire (fail-closed proof)."""

    def transport(prompt: str, *, max_tokens: int) -> tuple[str, int, int]:
        raise AssertionError("live transport invoked — daily kill switch did not trip closed")

    return AnthropicLLMClient(settings=_settings_low_daily_cap(), transport=transport)


def _on_brand_judge():
    def judge(proposal: object, never_rules: list[str]) -> float | None:
        return 0.99

    return judge


def _a_family_id() -> UUID:
    repo = deps.get_repository()
    return repo.list_families()[0].family_id  # type: ignore[attr-defined]


@pytest.fixture
def _client() -> Iterator[TestClient]:
    deps.reset_observability_log()
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_settings_dep] = _settings_low_daily_cap
    app.dependency_overrides[deps.get_llm_client] = _exploding_client
    app.dependency_overrides[deps.get_brand_judge] = _on_brand_judge
    yield TestClient(app)
    app.dependency_overrides.clear()
    deps.reset_observability_log()


def test_draft_blocks_when_daily_cap_reached(_client: TestClient) -> None:
    """With today's logged spend over the daily cap the draft makes NO live call.

    Seed the spine with a proposal carrying `usd_spent` above `cost_daily_usd_cap`
    dated TODAY; the next draft must degrade (deterministic template) WITHOUT the
    exploding transport ever firing — the cross-run daily kill switch trips closed.
    """
    family_id = _a_family_id()
    log = deps.get_observability_log()
    log.log_proposal(
        proposal_id=uuid4(),
        flow="enrollment_draft",
        schema_version="1",
        payload={},
        usd_spent=5.00,  # > the 2.00 daily cap
        created_at=datetime.now(UTC),
    )

    resp = _client.post(
        "/ai/enrollment/draft",
        json={"family_id": str(family_id), "action": DraftAction.EMAIL.value},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Degraded deterministic path — no live call (the exploding transport proves the
    # cross-run daily kill switch tripped closed). The drafting feature degrades to
    # the operator template (TECH_STACK §6.2): it is the deterministic stand-in, not a
    # model-authored draft — so it carries the [DEGRADED ...] marker, never live text.
    assert body["degraded"] is True
    assert "[DEGRADED" in body["proposal"]["body"]
