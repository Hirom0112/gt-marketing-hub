"""Params loader + validation (ARCHITECTURE.md §8; CLAUDE.md INV-11).

The params file (`params/params.yaml`) is the single home for every magic
number — queue weights, TEFA amounts, eval thresholds, cost caps, latency
budgets, geo settings. Nothing in `core/`, `ai/`, or `adapters/` hardcodes a
tunable; each consumer reads it from here (INV-11). This module parses the
YAML into typed Pydantic v2 models — one nested model per top-level §8 block —
and validates it at load: a missing required key or a wrong type raises a
clear, typed error, so config drift fails the build (CLAUDE.md §4.1).

This module is part of the deterministic core and stays pure: it imports
nothing from `app.ai` or `app.adapters` (the core-purity test guards this).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.core.program import Program

# Default location of the params file, relative to the repo root, when neither
# an explicit path nor the PARAMS_PATH env var is supplied.
_DEFAULT_PARAMS_PATH = Path("params/params.yaml")


class _StrictModel(BaseModel):
    """Base for every params block: reject unknown keys, validate strictly.

    `extra="forbid"` turns a stray/renamed key into a validation error rather
    than silently ignoring it, so the params file cannot drift away from the
    schema without the build failing (CLAUDE.md §4.1).
    """

    model_config = ConfigDict(extra="forbid")


class Recoverability(_StrictModel):
    """work_queue.recoverability sub-factors, each normalized to [0,1].

    ``stage_proximity_weight`` is the dominant sub-factor (A-23): how far a family
    got down the Interest→Tuition funnel before stalling is the primary "who can I
    save first" signal — the further they went, the more recoverable.
    """

    stall_recency_weight: float
    stage_proximity_weight: float
    responsiveness_weight: float
    # Normalizer for the responsiveness sub-factor (A-5): the aggregate
    # `community_profile.engagement_signals["email_opens"]` count is divided by
    # this to map into [0,1]. Aggregate only — no child-keyed signal (INV-6).
    responsiveness_email_opens_max: int


class WorkQueueValue(_StrictModel):
    """work_queue.value — per-child tuition × the family's child count (A-23).

    Every targeted family pays the same full GT-Anywhere tuition per child (Texas
    voucher = self-pay), so the only thing that varies value across families is how
    many children they enrolled (the Interest form's "How many children? 1–5+",
    funnel-map §4D). ``value = num_children × tuition_annual_default``;
    ``max_children`` (the "5+" cap) normalizes it via ``value_max`` so the value
    term stays in [0,1]. The old per-family ``funded_multiplier``/``variance`` hash
    jitter is GONE — value spread is now a real funnel signal, not noise.
    """

    tuition_annual_default: float
    max_children: int

    @model_validator(mode="after")
    def _max_children_valid(self) -> WorkQueueValue:
        if self.max_children < 1:
            raise ValueError(
                f"work_queue.value.max_children must be >= 1, got {self.max_children!r}"
            )
        return self


class WorkQueue(_StrictModel):
    """FR-2.5 work-queue scorer weights and sub-factors (§8).

    ``freshness_window_days``/``freshness_floor`` (S12) drive the
    ``recoverable_now`` freshness decay: freshness falls linearly from 1.0 at the
    stall anchor to ``freshness_floor`` once ``freshness_window_days`` have
    elapsed (never reaching 0, so a long-stalled family stays rankable).

    ``w_deadline``/``deadline_horizon_days`` (R2) add the deadline-proximity term:
    ``score += w_deadline · deadline_proximity``, where ``deadline_proximity``
    rises from 0 (≥ ``deadline_horizon_days`` out, or no deadline / not at risk)
    to 1.0 (deadline today or past). A family AWARDED/SELECTED but not yet
    RECONFIRMED near a voucher deadline is about to LOSE its award, so the term
    floats it to the top of the queue. ``w_deadline`` is a tunable (default set so
    an at-risk near-deadline family outranks a higher-value non-urgent one).
    """

    w_recoverability: float
    w_value: float
    w_deadline: float
    recoverability: Recoverability
    value: WorkQueueValue
    stall_window_days: int
    freshness_window_days: int
    freshness_floor: float
    deadline_horizon_days: int

    @model_validator(mode="after")
    def _deadline_horizon_positive(self) -> WorkQueue:
        if self.deadline_horizon_days < 1:
            raise ValueError(
                f"work_queue.deadline_horizon_days must be >= 1, got {self.deadline_horizon_days!r}"
            )
        return self


class ContactWindows(_StrictModel):
    """enrollment.contact — contact-recency color thresholds (S9 W1; INV-11).

    The single home for the recency deriver's day windows. Age is measured in
    whole days from a family's ``created_at``: an uncontacted family is *fresh*
    (grey) while ``age_days <= grey_window_days`` and *overdue* (red) once
    ``age_days >= overdue_days``. Read by ``core/contact_status.py`` — never
    hardcoded.
    """

    grey_window_days: int
    overdue_days: int


class Enrollment(_StrictModel):
    """S9 enrollment-depth tunables (§8). Currently the contact-recency windows."""

    contact: ContactWindows


class AgentPolicy(_StrictModel):
    """One sales agent's tunable ROUTING POLICY (LEAD_ASSIGNMENT.md §10/§11).

    The DB ``sales_agent`` row is the stable identity/FK anchor (agent_id, rank,
    synthetic_name, hubspot_owner_id); the *routing policy* lives here, keyed by
    agent_id, so "adding a real agent later is config, not code". Every value is a
    tunable home (INV-11): the router reads territory/role/status/weight/capacity
    from here, never a code literal.

    * ``territory`` — the state codes this agent covers (the §4 territory rule).
    * ``role`` — ``closer`` (hot/ready-to-deposit leads) or ``qualifier`` (the
      setter/BDR seat; early-stage leads). The product term; the DB ``tier`` token
      stays ``closer|setter`` for back-compat.
    * ``status`` — ``available`` | ``out`` | ``onboarding``; a non-available agent
      is skipped in rotation (§8). ``at_capacity`` is DERIVED (queue_size ≥ cap),
      never stored.
    * ``weight`` — the weighted-round-robin share (§7); ``1`` = flat.
    * ``capacity_cap`` — the per-agent hard cap; cap BEATS weight (a capped agent
      overflows to the next in ring order, §7).
    """

    territory: list[str]
    role: Literal["closer", "qualifier"]
    status: Literal["available", "out", "onboarding"] = "available"
    weight: int = 1
    capacity_cap: int


class Territory(_StrictModel):
    """Territory-routing policy (LEAD_ASSIGNMENT.md §4).

    ``fallback`` governs a family whose ``state`` no agent covers: ``round_robin_all``
    routes across all available agents (loudly logged); ``intake_park`` holds the
    lead for an admin (a human gate). Default round_robin_all (never unrouted).
    """

    fallback: Literal["round_robin_all", "intake_park"] = "round_robin_all"


class IncomeRouting(_StrictModel):
    """Income-tier routing policy (LEAD_ASSIGNMENT.md §6). ``tefa_eligible_tiers``
    are the income buckets that mark a voucher-track family (a prioritization /
    tiebreak signal in v1, not a hard pool gate)."""

    tefa_eligible_tiers: list[str]


class RoundRobin(_StrictModel):
    """Round-robin policy (LEAD_ASSIGNMENT.md §7). ``weighted`` honors per-agent
    ``weight`` over a weight-expanded ring; ``flat`` ignores weights. Cap always
    beats weight regardless of mode."""

    mode: Literal["weighted", "flat"] = "weighted"


class Sla(_StrictModel):
    """SLA-reassignment policy (LEAD_ASSIGNMENT.md §9).

    * ``unworked_reassign_days`` — assigned + no contact past this ⇒ reassign.
    * ``max_reassignments`` — SLA hops before escalating to intake (anti-ping-pong).
    * ``cooldown_days`` — re-sweep cool-down after a reassignment (timer restarts).
    * ``owned_breach`` — what an SLA breach does to an OWNER-matched (incl.
      self-reported) lead: ``alert`` raises an admin alert without silently moving
      it (the "one source of truth" default); ``auto_reassign`` reroutes it.
    """

    unworked_reassign_days: int
    max_reassignments: int
    cooldown_days: int
    owned_breach: Literal["alert", "auto_reassign"] = "alert"


class Assignment(_StrictModel):
    """Owner-authority assignment split + routing/alarm tunables (MULTI_AGENT_COCKPIT §2.2, §4).

    'Closer' is an agent **tier**, not a third role; the split is the single
    ``closer_rank_max`` tunable (demo = 1 ⇒ agents at rank <= 1 are the closer
    tier). The other values drive the assignment/triage layer (M2):

    * ``high_value_threshold`` — annual-tuition $ at/above which a family is
      routed as HIGH VALUE (a dollar figure, NOT a [0,1] score).
    * ``high_likelihood_threshold`` — recoverability/likelihood in [0,1] at/above
      which a family is routed as HIGH LIKELIHOOD.
    * ``deadline_alarm_days`` — a family within this many days of a voucher
      deadline raises the deadline alarm.
    * ``unowned_alarm_days`` — an UNOWNED family older than this many days raises
      the unowned alarm (nobody has picked it up).
    * ``per_tier_load_cap`` — the max open families a single agent tier may hold
      before the queue stops auto-assigning to it (load governance).

    Every value is a tunable home (INV-11); the assignment layer reads them here,
    never a code literal.
    """

    closer_rank_max: int
    high_value_threshold: float
    high_likelihood_threshold: float
    deadline_alarm_days: int
    unowned_alarm_days: int
    per_tier_load_cap: int

    # --- Lead-assignment routing policy (LEAD_ASSIGNMENT.md §11). Agent defs are
    # config (not hardcoded): territory/role/availability/weight/capacity per
    # agent_id, plus the territory/income/round-robin/SLA policy knobs. ---
    agents: dict[str, AgentPolicy]
    territory: Territory
    income_routing: IncomeRouting
    round_robin: RoundRobin
    sla: Sla


class Sis(_StrictModel):
    """SIS reconcile bucket rules (MULTI_AGENT_COCKPIT §6; INV-11).

    The seam reconcile matches GT-side records against the SIS export on a
    normalized email/phone match score, then sorts each match into one of three
    buckets — ``confirmed`` / ``paid_not_in_sis`` / ``records_lag`` — while the
    ambiguous tail routes to the merge queue. The bucket boundaries are tunables,
    not hardcoded thresholds (M5 consumes these):

    * ``match_confidence_cutoff`` — below this normalized match score the pair is
      ambiguous and routes to the merge queue rather than auto-bucketing.
    * ``confirmed_min_confidence`` — at/above this score a GT↔SIS pair is a
      CONFIRMED match (present and reconciled on both sides).
    * ``paid_not_in_sis_max_confidence`` — a GT-side paid family whose best SIS
      match scores at/below this is treated as PAID-NOT-IN-SIS (paid on GT's side
      but missing from the SIS export).
    * ``records_lag_days`` — a confirmed-paid family absent from the SIS export
      this many days past payment is flagged RECORDS-LAG (expected to appear once
      the SIS catches up) rather than as a hard discrepancy.
    * ``phone_only_confidence`` — the normalized match score a phone-only match
      earns (email mismatched/absent). Sits in the ambiguous band by default so a
      phone-only match routes to the merge queue, never an auto-confirm.
    """

    match_confidence_cutoff: float
    confirmed_min_confidence: float
    paid_not_in_sis_max_confidence: float
    records_lag_days: int
    phone_only_confidence: float


class Bulk(_StrictModel):
    """S12 W2 bulk-action governance (A-20; INV-8 — the per-run cap + kill).

    ``nudge_per_run_cap`` is the hard ceiling on how many families a single
    bulk-nudge run may SEND: an eval-passing family beyond the cap is deferred to
    ``capped`` rather than overspending the metered edge (INV-8). The single
    canonical home for the bulk cap — never a code literal.
    """

    nudge_per_run_cap: int


class BackToSchool(_StrictModel):
    """S12 W2 back-to-school volume cohort shape (A-21; INV-1/INV-11).

    The SEPARATE deterministic synthetic cohort (NOT the default June world): a
    surge of ``count`` active stalls with a single-day Aug spike. Every value is
    a tunable home (INV-11); the cohort is drawn from its own ``seed`` so the
    default stream stays byte-identical (the determinism guard holds). ``spike_*``
    anchor the spike day, ``spike_share`` is the fraction of the cohort whose
    ``stalled_since`` lands on that day, and ``spread_days`` bands the rest.
    """

    count: int
    seed: int
    spike_year: int
    spike_month: int
    spike_day: int
    spike_share: float
    spread_days: int


class SecondaryBump(_StrictModel):
    """A single within-month inquiry bump in the realistic cohort (a one-day cluster)."""

    year: int
    month: int
    day: int
    count: int


class Realistic(_StrictModel):
    """The realistic-cadence synthetic cohort shape (INV-1/INV-11).

    A SEPARATE deterministic cohort calibrated to GT's measured top-of-funnel
    cadence (aggregate-only): ``total`` inquiries spread across ``monthly_counts``
    within the ``[window_start, window_end]`` inquiry window, with one campaign
    spike (``spike_count`` on the ``spike_*`` day) and optional ``secondary_bumps``.
    A recent ``active_count`` slice are UNRESOLVED stalls whose ``stalled_since``
    falls in the last ``active_window_days`` (the ACTIVE board), of which
    ``dismissed_count`` are set aside via a logged dismiss event; the rest of the
    cohort is HISTORY (derives RECOVERED). Drawn from its own ``seed`` so the
    default + back_to_school streams stay byte-identical.
    """

    total: int
    seed: int
    window_start_year: int
    window_start_month: int
    window_start_day: int
    window_end_year: int
    window_end_month: int
    window_end_day: int
    # Year-month string ("2026-01") → inquiry count. Keys are validated as data;
    # the sum is asserted against ``total`` so the calibration can't silently drift.
    monthly_counts: dict[str, int]
    spike_year: int
    spike_month: int
    spike_day: int
    spike_count: int
    secondary_bumps: list[SecondaryBump]
    active_count: int
    active_window_days: int
    dismissed_count: int

    @model_validator(mode="after")
    def _shape_is_consistent(self) -> Realistic:
        month_total = sum(self.monthly_counts.values())
        if month_total != self.total:
            raise ValueError(
                f"realistic.monthly_counts must sum to total {self.total}, got {month_total}"
            )
        if self.spike_count > self.total:
            raise ValueError("realistic.spike_count cannot exceed total")
        if self.dismissed_count > self.active_count:
            raise ValueError("realistic.dismissed_count cannot exceed active_count")
        if self.active_count > self.total:
            raise ValueError("realistic.active_count cannot exceed total")
        if self.active_window_days <= 0:
            raise ValueError("realistic.active_window_days must be positive")
        return self


class AwardAmounts(_StrictModel):
    """funding.award_amounts — TEFA tiers, $/yr (RESEARCH.md Q1)."""

    tefa_standard: float
    tefa_disability: float
    tefa_homeschool: float


class Funding(_StrictModel):
    """FR-2.7 funding amounts + installment split + tuition-unlock gate (§8)."""

    award_amounts: AwardAmounts
    installment_split: list[float]
    tuition_unlock_state: str

    @field_validator("installment_split")
    @classmethod
    def _split_sums_to_one(cls, value: list[float]) -> list[float]:
        """The installment split must partition the award exactly (FR-2.7).

        A split that does not sum to 1.0 would leave the award over- or
        under-disbursed; drift here fails the build (CLAUDE.md INV-11, §4.1).
        A tiny float-epsilon tolerance absorbs YAML decimal representation only.
        """
        total = sum(value)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"funding.installment_split must sum to 1.0, got {total!r} ({value!r})"
            )
        return value


class VoucherWindows(_StrictModel):
    """voucher_programs.<key>.windows — the per-program deadline calendar (R2).

    The genuinely-NEW window values the standing engine needs (the award AMOUNTS
    stay in ``funding`` — INV-11, one canonical home):

    * ``parent_select_deadline`` — the date by which a family must SELECT/confirm
      the school (the SELECTED_GT → RECONFIRMED reconfirm step's deadline).
    * ``full_award_cutoff`` — the last date a confirmation still earns the FULL
      award; after it the award prorates by enrollment date.
    * ``reconfirm_required`` — whether the program has the parent-reconfirm/lock-in
      gap at all (the "$X lost on a deadline" at-risk window).

    ``verified``/``confidence`` carry the rule's provenance so an UNVERIFIED rule
    is visible and never silently load-bearing (the ANALYSIS docs hedge some
    dates). Dates are stored as concrete ``date`` DATA, not month/day comments.
    """

    parent_select_deadline: date
    full_award_cutoff: date
    reconfirm_required: bool
    verified: bool
    confidence: str

    @model_validator(mode="after")
    def _cutoff_not_before_select(self) -> VoucherWindows:
        if self.full_award_cutoff < self.parent_select_deadline:
            raise ValueError(
                "voucher windows.full_award_cutoff must be >= parent_select_deadline, got "
                f"{self.full_award_cutoff!r} < {self.parent_select_deadline!r}"
            )
        return self


class InstallmentScheduleEntry(_StrictModel):
    """One installment in a program's disbursement schedule (R2; DATA, not a comment).

    ``fraction`` is this installment's share of the award (mirrors
    ``funding.installment_split`` — the SPLIT is shared math, the per-program
    DUE DATE is the new value). ``due_month``/``due_day`` carry the calendar due
    date AS DATA so the schedule's timing is a config row, never a YAML comment.
    """

    fraction: float
    due_month: int
    due_day: int

    @model_validator(mode="after")
    def _due_date_valid(self) -> InstallmentScheduleEntry:
        if not 1 <= self.due_month <= 12:
            raise ValueError(f"installment due_month must be 1..12, got {self.due_month!r}")
        if not 1 <= self.due_day <= 31:
            raise ValueError(f"installment due_day must be 1..31, got {self.due_day!r}")
        if not 0.0 <= self.fraction <= 1.0:
            raise ValueError(f"installment fraction must be in [0,1], got {self.fraction!r}")
        return self


class VoucherSettingLock(_StrictModel):
    """voucher_programs.<key>.setting_lock — irreversible setting rules (R2).

    ``irreversible_settings`` lists the learning-SETTING tokens that, once chosen,
    cannot be changed (e.g. homeschool/other is frozen and cannot upgrade to the
    private-school rate — CONFIRMED in the ANALYSIS docs). ``verified``/``confidence``
    carry the rule's provenance so an unverified lock is visible.
    """

    irreversible_settings: list[str]
    verified: bool
    confidence: str


class VoucherProgram(_StrictModel):
    """A single voucher program's RULES + DEADLINES (R2; multi-state = config row).

    Proves multi-state expansion is a CONFIG ROW, not a code change: ``tx_tefa``
    and ``az_esa`` are two instances of this same model. It owns ONLY the new
    window/rule values — the award AMOUNTS keep their canonical home in ``funding``
    (INV-11, no duplicated value). ``tuition_unlock_state`` names the funding-state
    threshold at/after which tuition unlocks (composes ``funding_gate``).
    """

    program_name: str
    funding_year: int
    windows: VoucherWindows
    installment_schedule: list[InstallmentScheduleEntry]
    setting_lock: VoucherSettingLock
    tuition_unlock_state: str

    @model_validator(mode="after")
    def _schedule_non_empty(self) -> VoucherProgram:
        if not self.installment_schedule:
            raise ValueError("voucher program installment_schedule must be non-empty")
        return self


class NudgeTrigger(_StrictModel):
    """FR-4.1 nudge classifier thresholds."""

    min_precision: float
    min_recall: float


class DocExtraction(_StrictModel):
    """FR-4.2 doc-extraction accuracy threshold."""

    min_accuracy: float


class MessageSafetyGrounding(_StrictModel):
    """FR-4.3 grounding/safety gate — gates §5.2 and §5.3.

    Two DISTINCT thresholds, one canonical home each (INV-11):

    * ``min_grounding`` is the V-2 grounding floor — an unverifiable "4X/2X"
      performance claim must be BLOCKED at this bar; it is never lowered (INV-4).
    * ``min_brand_score`` is the V-4 brand-voice bar — the LLM brand judge scores
      genuinely on-brand copy around 0.85, so V-4 is decoupled from the V-2 floor
      (reusing 0.95 wrongly blocked legitimate on-brand generation). REQUIRED, so
      config drift fails the build (CLAUDE.md §4.1).
    """

    min_grounding: float
    min_brand_score: float
    max_unverifiable_claims: int
    require_coppa_safe: bool


class CloseTips(_StrictModel):
    """S9 W5 "how to close" tips — grounded in app_form.extracted_fields (FR-4.3).

    Same shape as :class:`MessageSafetyGrounding`: the close-tips proposal crosses
    the SAME canonical grounding gate (A-10), so a hallucinated fact (a tip absent
    from ``extracted_fields``) fails V-2 and is BLOCKED, not softened (INV-4). The
    golden-set eval is gated by ``min_grounding`` (INV-3).
    """

    min_grounding: float
    max_unverifiable_claims: int
    require_coppa_safe: bool


class GeoTracking(_StrictModel):
    """FR-4.4 GEO repeated-sampling thresholds."""

    min_samples_per_prompt: int
    report_variance: bool


class EvalThresholds(_StrictModel):
    """FR-4.x eval thresholds; an action below threshold is BLOCKED/disabled (§8)."""

    nudge_trigger: NudgeTrigger
    doc_extraction: DocExtraction
    message_safety_grounding: MessageSafetyGrounding
    close_tips: CloseTips
    geo_tracking: GeoTracking


class CostCaps(_StrictModel):
    """NFR-5 hard per-run caps; exceeding ⇒ deterministic/placeholder (§9)."""

    anthropic_per_run_usd: float
    media_gen_per_run_usd: float


class ModelPricing(_StrictModel):
    """Per-model token rates, $/MTok (TECH_STACK §6.1).

    Pure data — the canonical per-million-token input/output prices. The actual
    token→USD computation lives in the AI layer (`app/ai/pricing.py`), NOT here:
    `core/` stays pure (no logic/IO), it just owns the rates so they have exactly
    one home (INV-11).
    """

    input_per_mtok: float
    output_per_mtok: float


class AnthropicPricing(_StrictModel):
    """TECH_STACK §6.1 token pricing — per-model $/MTok rates keyed by model id.

    The single canonical home for the live cost model (INV-11): the AI layer's
    pricing helper reads these to convert reported tokens into the real USD the
    per-run/daily caps charge, never a code literal. Keys are the model ids that
    match ``ANTHROPIC_MODEL_*`` (§5.3); an id absent here is a config gap and the
    helper fails loud rather than charging $0.
    """

    models: dict[str, ModelPricing]


class LatencyBudgetMs(_StrictModel):
    """NFR-9 latency budgets, milliseconds."""

    ai_proposal: int


class Geo(_StrictModel):
    """FR-3.7 GEO prompt-set + cadence + 0% baseline + generate-to-win lift (§8)."""

    prompt_set_size: int
    cadence: str
    baseline_coverage: float
    # generate-to-win flywheel (FR-3.7): the GT cite-likelihood buckets (of 256)
    # the simulated engine uses for a PROMPT THAT HAS BEEN WON (a GEO piece was
    # generated, gate-passed, and published). The single canonical home for the
    # lift amount (INV-11); the simulated adapter reads it, never a code literal.
    published_cite_buckets: int


class BrandMemory(_StrictModel):
    """FR-3.2 brand-memory conditioning loop tunables (CONTENT_SPEC §8.3.2).

    `weight_step` is the affirm/discard weight delta — keeping a candidate adds
    it to the conditioning weight, discarding subtracts it. It is the canonical
    home for the value the brand store currently defaults in code
    (`SqliteBrandMemoryStore`'s `_DEFAULT_WEIGHT_STEP`), closing that INV-11 gap.
    """

    weight_step: float


class LibraryIngestNormalization(_StrictModel):
    """library_ingest.normalization — per-platform engagement caps (INV-11).

    Scraped engagement is NOT comparable across platforms: X / YouTube carry a
    `views_plays` count in the tens-of-thousands while Instagram / Facebook /
    TikTok carry a `likes` count in the low hundreds. The distill + loader
    normalize each post's raw engagement by its platform cap into [0,1], so
    `weight` ranks WITHIN a platform (an X view and an IG like are never
    compared directly). Each cap divides that platform's raw signal; the result
    is clamped to [0,1]. The canonical home for those caps — never a code
    literal.
    """

    instagram_likes_max: int
    facebook_likes_max: int
    tiktok_likes_max: int
    x_views_max: int
    youtube_views_max: int


class LibraryIngest(_StrictModel):
    """Scraper-library ingest tunables (Phase-1 marketing; INV-11).

    The distilled `brand_library.json` (GT's OWN proven public marketing) seeds
    brand memory, GEO prompts, and the content library. `top_n_per_theme` caps
    how many exemplars the distill keeps per INSIGHTS theme so the seed stays
    small and deterministic; `normalization` holds the per-platform engagement
    caps that map raw engagement into a comparable [0,1] `weight`. The library
    ROOT path is NOT a param — it is the `GT_LIBRARY_PATH` env var (TECH_STACK
    §5); the committed in-repo JSON path is a fixed code constant, not a tunable.
    """

    top_n_per_theme: int
    normalization: LibraryIngestNormalization

    @model_validator(mode="after")
    def _top_n_positive(self) -> LibraryIngest:
        if self.top_n_per_theme < 1:
            raise ValueError(
                f"library_ingest.top_n_per_theme must be >= 1, got {self.top_n_per_theme!r}"
            )
        return self


class PostedGalleryEngagement(_StrictModel):
    """FR-3.4 posted-gallery — REAL-catalog engagement composite weights (INV-11).

    When the gallery sources from the REAL posted catalog (the scoped INV-1 exception,
    ASSUMPTIONS), a post's ``value`` is a REAL engagement composite:
    ``like_weight·likes + view_weight·views + comment_weight·comments``. The three
    weights live here so the formula is never a code literal — a drifted weight moves
    the ranking and the test fails. Comments outweigh views (a comment is a far stronger
    signal than a passive view), hence the asymmetric defaults.

    This is DISTINCT from the synthetic ``value_min``/``value_max``/``posted_within_days``
    band on :class:`PostedGallery`, which the LIBRARY-FALLBACK path keeps using when no
    real catalog is configured.
    """

    like_weight: float
    view_weight: float
    comment_weight: float

    @model_validator(mode="after")
    def _weights_non_negative(self) -> PostedGalleryEngagement:
        for name in ("like_weight", "view_weight", "comment_weight"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"posted_gallery.engagement.{name} must be >= 0, got {value!r}")
        return self


class PostedGallery(_StrictModel):
    """FR-3.4 posted-content gallery — value + posted_at tunables (INV-11).

    Two value paths, both params-homed here:

    * REAL-catalog path — ``value`` is a real engagement composite weighted by
      :class:`PostedGalleryEngagement` (``engagement``). This is the live gallery when
      ``GT_POSTED_CATALOG_ROOT`` is configured (the scoped INV-1 exception, ASSUMPTIONS).
    * LIBRARY-FALLBACK path — no real catalog ⇒ the gallery falls back to the kept
      library, whose "most valuable" sort key is a DETERMINISTIC SYNTHETIC value: a
      stable hash of the asset id mapped into ``[value_min, value_max]`` (the same
      placeholder posture as the work-queue value spread). ``posted_within_days`` bounds
      the matching synthetic ``posted_at`` (a stable hash backdates each post into the
      window before the fixed import epoch for the "most recent" sort).

    Every value is params-homed so none is a code literal.
    """

    value_min: float
    value_max: float
    posted_within_days: int
    engagement: PostedGalleryEngagement

    @model_validator(mode="after")
    def _band_and_window_valid(self) -> PostedGallery:
        if self.value_max <= self.value_min:
            raise ValueError(
                "posted_gallery.value_max must be > value_min, got "
                f"{self.value_max!r} <= {self.value_min!r}"
            )
        if self.posted_within_days < 1:
            raise ValueError(
                f"posted_gallery.posted_within_days must be >= 1, got {self.posted_within_days!r}"
            )
        return self


class CreatorScoringFit(_StrictModel):
    """FR-3.8 creator-discovery fit sub-weights (CONTENT_SPEC §8.1).

    Each weight multiplies a [0,1] sub-factor; together they form the fit
    score. They MUST partition to 1.0 so the consumer can trust the fit score
    stays in [0,1] — a drifted set fails to load (INV-11, §4.1).
    """

    topic_match_weight: float
    audience_match_weight: float
    brand_alignment_weight: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> CreatorScoringFit:
        total = self.topic_match_weight + self.audience_match_weight + self.brand_alignment_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"creator_scoring.fit weights must sum to 1.0, got {total!r}")
        return self


class CreatorScoringAuthenticity(_StrictModel):
    """FR-3.8 creator-discovery authenticity sub-weights (CONTENT_SPEC §8.1).

    Sub-weights over [0,1] sub-factors; MUST sum to 1.0. The consumer applies
    `spam_signal_weight` as a penalty (higher spam signal LOWERS authenticity).
    """

    follower_authenticity_weight: float
    engagement_consistency_weight: float
    spam_signal_weight: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> CreatorScoringAuthenticity:
        total = (
            self.follower_authenticity_weight
            + self.engagement_consistency_weight
            + self.spam_signal_weight
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"creator_scoring.authenticity weights must sum to 1.0, got {total!r}")
        return self


class CreatorScoring(_StrictModel):
    """FR-3.8 creator-discovery fit + authenticity + surface threshold."""

    fit: CreatorScoringFit
    authenticity: CreatorScoringAuthenticity
    # Minimum fit score for a creator to surface in the discovery list.
    surface_threshold: float


class KpiLever(_StrictModel):
    """FR-3.11 per-channel KPI lever: baseline (current) + target.

    `lever delta = metric - baseline`; the consumer rolls these up vs target.
    """

    baseline: float
    target: float


class KpiWindows(_StrictModel):
    """kpi.windows — the agent-KPI time-window day-counts (D-14/D-15; INV-11).

    The agent dashboard's Day/Week/Month/All filter narrows the personal-KPI and
    roster aggregations by a trailing window. ``day``/``week``/``month`` are the
    whole-day spans of those windows (``all`` is unbounded and carries no day-count).
    Homed here — the aggregation reads these, never a hardcoded 1/7/30 literal.
    """

    day: int
    week: int
    month: int


class Kpi(_StrictModel):
    """FR-3.11 per-channel KPI rollup vs baseline/target (+ D-14 agent-KPI windows)."""

    # Channel-name keyed (keys are the `Channel` tokens).
    levers: dict[str, KpiLever]
    # Agent-dashboard time-window day-counts (D-14/D-15). Optional with the canonical
    # day/week/month defaults so a params file predating the field still loads.
    windows: KpiWindows = KpiWindows(day=1, week=7, month=30)


class Scheduler(_StrictModel):
    """FR-3.6 / OUT-2 content scheduler — dispatch is SIMULATED in v1.

    The publish-monitor slice fans one publish request out to a subset of
    ``publish_channels`` (a subset of the LOCKED ``Channel`` enum, CONTENT_SPEC
    §2.1 — social-publishable feeds only). ``daily_caps`` is the per-platform max
    SIMULATED dispatches/day; an over-cap channel is forced ``blocked`` (INV-8
    governance posture). Both live here as the one canonical home (INV-11).
    """

    # Never 'live' in v1 (INV-9, OUT-2): the field is typed shut to simulated.
    dispatch_mode: str
    # Social-publishable channel tokens the fan-out may target (subset of Channel).
    publish_channels: list[str]
    # Per-platform-token max simulated dispatches/day (quota guard; over-cap ⇒ blocked).
    daily_caps: dict[str, int]

    @field_validator("dispatch_mode")
    @classmethod
    def _dispatch_is_simulated(cls, value: str) -> str:
        if value != "simulated":
            raise ValueError(
                f"scheduler.dispatch_mode must be 'simulated' in v1, got {value!r} (INV-9, OUT-2)"
            )
        return value

    @model_validator(mode="after")
    def _caps_cover_channels(self) -> Scheduler:
        if not self.publish_channels:
            raise ValueError("scheduler.publish_channels must be non-empty")
        missing = [c for c in self.publish_channels if c not in self.daily_caps]
        if missing:
            raise ValueError(
                f"scheduler.daily_caps must define a cap for every publish channel; "
                f"missing {missing!r}"
            )
        bad = {k: v for k, v in self.daily_caps.items() if v < 1}
        if bad:
            raise ValueError(f"scheduler.daily_caps values must be >= 1, got {bad!r}")
        return self


class CrmGtProperties(_StrictModel):
    """crm.gt_properties — the gt_* custom HubSpot property internal names (S10).

    Provisioned by ``scripts/provision_hubspot.py`` and read by the live adapter
    so a property name lives in exactly one place (INV-11): the adapter never
    hardcodes ``gt_synthetic_id`` et al.

    ``social_post`` are the gt_* properties on the **GT Social Post** custom
    object (publish-monitor W3): the second-screen mirror of each dispatched
    social post. Same INV-11 posture — the mirror adapter reads these names,
    never a code literal.
    """

    deal: list[str]
    contact: list[str]
    social_post: list[str]


class CrmSocialPostObject(_StrictModel):
    """crm.gt_social_post_object — the GT Social Post custom object config (W3).

    The publish-monitor mirror upserts one custom-object record per dispatched
    social post so the team can monitor publishing on the HubSpot screen too. Per
    INV-11 the object's API identifiers live here (provisioned by
    ``scripts/provision_hubspot.py``), never hardcoded in the adapter:

    - ``object_type`` is the CRM v3 object identifier the adapter puts in the URL
      path (``/crm/v3/objects/{object_type}``) — HubSpot accepts either the
      ``fullyQualifiedName`` (``p<portal>_gt_social_post``) or the object type id
      (``2-XXXXXXX``). A placeholder ships in the example; the live params.yaml
      carries the provisioned value.
    - ``id_property`` is the idempotency upsert key on the object — the
      ``gt_synthetic_id`` analogue keyed on ``str(post_id)`` (NEVER any contact
      identity; INV-1).
    """

    object_type: str
    id_property: str


class Crm(_StrictModel):
    """S10 HubSpot CRM seam config (ANALYSIS/hubspot-complement-plan.md §4).

    Every value the live adapter and the provisioning script need that is not
    code: the cockpit-stage ↔ HubSpot-stage-id map (portable to GT's real
    pipeline by re-provisioning), the synthetic write-lock allowlist/denylist
    (guard 1, INV-1), and the gt_* property names (INV-11).
    """

    # Cockpit Stage enum value → HubSpot deal stage id. The live adapter looks a
    # stage up here; a missing stage must fail closed (INV-4 posture) — enforced
    # in the pure mapping helper, not silently defaulted.
    stage_map: dict[str, str]
    synthetic_email_domains: list[str]
    real_domain_denylist: list[str]
    gt_properties: CrmGtProperties
    gt_social_post_object: CrmSocialPostObject

    @model_validator(mode="after")
    def _social_post_id_property_declared(self) -> Crm:
        """The custom object's upsert key MUST be in the social_post prop list.

        Keeps the two homes consistent (INV-11): the idempotency key the mirror
        upserts on (``gt_social_post_object.id_property``) has to be a property the
        provisioner declares on ``gt_properties.social_post``, else a drift would
        let the adapter key on a property that was never created.
        """
        if self.gt_social_post_object.id_property not in self.gt_properties.social_post:
            raise ValueError(
                "crm.gt_social_post_object.id_property "
                f"{self.gt_social_post_object.id_property!r} must appear in "
                f"crm.gt_properties.social_post {self.gt_properties.social_post!r}"
            )
        return self


class Security(_StrictModel):
    """M7 security/observability detection thresholds (MULTI_AGENT_COCKPIT §7; INV-11).

    The edge middleware (DETECTION, defense-in-depth — never inline blocking)
    records a `security_event` when an observed signal crosses one of these
    tunable thresholds. Each is the single canonical home (INV-11) — the
    middleware never hardcodes a count:

    * ``oversized_result_rows`` — a list response carrying at/above this many rows
      is flagged ``oversized_result`` (API4:2023 Unrestricted Resource
      Consumption — an enumeration/scraping/wide-band pull signal).
    * ``auth_failure_burst`` — at/above this many 401/403 responses from one actor
      within the rolling window is flagged ``auth_failure_burst`` (A07:2021
      Identification & Authentication Failures).
    * ``auth_failure_window_seconds`` — the rolling window (seconds) over which the
      auth-failure burst is counted.
    """

    oversized_result_rows: int
    auth_failure_burst: int
    auth_failure_window_seconds: int

    @model_validator(mode="after")
    def _thresholds_positive(self) -> Security:
        for name in ("oversized_result_rows", "auth_failure_burst", "auth_failure_window_seconds"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"security.{name} must be >= 1, got {value!r}")
        return self


class ConversionWeights(_StrictModel):
    """conversion.weights — the five conversion-likelihood dimension weights (DH-1).

    Each weight multiplies a [0,1] dimension sub-score; together they form the
    conversion-likelihood score. They MUST partition to 1.0 so the consumer can
    trust the score stays in [0,1] — a drifted set fails to load (INV-11, §4.1),
    mirroring :class:`CreatorScoringFit`. The five dimensions:

    * ``affluence`` — neighborhood-affluence weight (richer area ⇒ more likely to
      afford tuition; an AGGREGATE area label only — P-4/INV-6).
    * ``income`` — self-reported family income weight (higher ⇒ higher).
    * ``children`` — number-of-children weight (more children ⇒ more commitment/value).
    * ``funding`` — funding-type weight (a funded voucher path ⇒ money is lined up).
    * ``depth`` — application-depth weight (the REUSED ``recoverability`` term —
      deeper into the funnel ⇒ higher; NOT a new funnel score).
    """

    affluence: float
    income: float
    children: float
    funding: float
    depth: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ConversionWeights:
        total = self.affluence + self.income + self.children + self.funding + self.depth
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"conversion.weights must sum to 1.0, got {total!r}")
        return self


class Conversion(_StrictModel):
    """DH-1 conversion-likelihood signal tunables (ARCHITECTURE.md §8; INV-11).

    The single home for every magic number the conversion-likelihood scorer
    (``core/conversion.py``) needs — it replaces the meaningless "MAP signal" in
    the deal view with a deterministic, params-weighted "who is most likely to
    enroll, and the top contributing factor" signal:

    * ``weights`` — the five dimension weights (partition to 1.0).
    * ``band_high_cutoff`` / ``band_med_cutoff`` — score thresholds for the coarse
      band: ``score >= band_high_cutoff`` ⇒ "High", ``>= band_med_cutoff`` ⇒ "Med",
      else "Low" (``band_high_cutoff`` MUST exceed ``band_med_cutoff``).
    * ``neighborhood_affluence`` — the coarse AGGREGATE-area-label → [0,1] affluence
      table (P-4/INV-6: an area label, never precise minor geo);
      ``neighborhood_affluence_default`` is the affluence for an UNKNOWN label.
    * ``income_reference`` — the self-reported income (whole USD) that maps to ~1.0
      affluence-of-income (``income/reference`` clamped to [0,1]).
    * ``income_neutral`` — the NEUTRAL [0,1] contribution used when income is
      ``None`` (not provided): missing income is UNKNOWN, never treated as low/zero.
    * ``num_children_cap`` — the child count that maps to 1.0 (``num_children/cap``
      clamped to [0,1]).
    * ``funding_affinity`` — funding-type token → [0,1] close affinity (a funded
      voucher path ⇒ money is lined up ⇒ higher); ``funding_affinity_default`` is
      the affinity for an unknown/None funding type.
    """

    weights: ConversionWeights
    band_high_cutoff: float
    band_med_cutoff: float
    neighborhood_affluence: dict[str, float]
    neighborhood_affluence_default: float
    income_reference: float
    income_neutral: float
    num_children_cap: int
    funding_affinity: dict[str, float]
    funding_affinity_default: float

    @model_validator(mode="after")
    def _bounds_valid(self) -> Conversion:
        if not self.band_med_cutoff < self.band_high_cutoff:
            raise ValueError(
                "conversion.band_high_cutoff must exceed band_med_cutoff, got "
                f"{self.band_high_cutoff!r} <= {self.band_med_cutoff!r}"
            )
        if self.num_children_cap < 1:
            raise ValueError(
                f"conversion.num_children_cap must be >= 1, got {self.num_children_cap!r}"
            )
        if self.income_reference <= 0:
            raise ValueError(
                f"conversion.income_reference must be > 0, got {self.income_reference!r}"
            )
        return self


class PresumedLost(_StrictModel):
    """nurture.presumed_lost — the auto-SURFACED 'presumed lost' suggestion.

    After ``after_attempts`` no-answer/no-reply contact outcomes within
    ``within_days``, the family is flagged 'presumed lost' for a HUMAN to confirm
    (``requires_human_confirm``). The machine never silently drops a warm lead — it
    only suggests; a person confirms LOST (mirrors the dismiss pattern, INV-2).
    """

    after_attempts: int
    within_days: int
    requires_human_confirm: bool


class NurtureAnchor(_StrictModel):
    """nurture.anchors[] — a recurring school-year re-engagement window.

    A calendar window (``month``/``day``, recurring yearly) the funnel pulses on
    (voucher deadline, school-selection deadline, back-to-school). Re-engagement
    pressure ramps over the ``ramp_days`` before the date. ``name`` is a program /
    calendar label (no PII). Read by the deterministic anchor-pressure deriver.
    """

    name: str
    month: int
    day: int
    ramp_days: int

    @model_validator(mode="after")
    def _valid_calendar(self) -> NurtureAnchor:
        if not 1 <= self.month <= 12:
            raise ValueError(f"nurture anchor {self.name!r} month must be 1-12, got {self.month!r}")
        if not 1 <= self.day <= 31:
            raise ValueError(f"nurture anchor {self.name!r} day must be 1-31, got {self.day!r}")
        if self.ramp_days < 0:
            raise ValueError(
                f"nurture anchor {self.name!r} ramp_days must be >= 0, got {self.ramp_days!r}"
            )
        return self


class LongHorizon(_StrictModel):
    """nurture.long_horizon — the long-drip track for future-enrollment families.

    Incoming-kindergarten / future-grade families are nurtured over a long horizon
    (``drip_months``) — kept warm, never treated as lost. ``channel_priority`` orders
    outreach channels (lead with the channel that historically out-responded).
    """

    drip_months: int
    channel_priority: list[str]


class Nurture(_StrictModel):
    """nurture — the later-lifecycle policy dials (INV-11; all BUSINESS-owned).

    Every value here is a team dial, not engineering: how long until COLD, when a
    silent family is PRESUMED LOST (human-confirmed), the base re-contact cadence and
    touch cap before DORMANT, the channel order, and the school-year anchors that ramp
    re-engagement. Nurture EXECUTION (the actual drip sends) is HubSpot's job (the
    locked seam plan); the cockpit owns these dials and pushes the trigger.
    """

    cold_after_days: int
    presumed_lost: PresumedLost
    base_recontact_interval_months: int
    max_touches: int
    channel_priority: list[str]
    anchors: list[NurtureAnchor]
    long_horizon: LongHorizon


class Programs(_StrictModel):
    """A1 program-isolation config — the active programs of the single DB (INV-11).

    The single hardened database is multi-program; this block is the one
    canonical home for which programs are live and which one THIS deployment
    serves. Each id is validated against the :class:`~app.core.program.Program`
    enum (a stray/renamed token fails to load), and ``active_program_id`` MUST be
    one of ``active_program_ids`` — a selected program absent from the active list
    is config drift and fails the build (CLAUDE.md §4.1). The resolved
    ``active_program_id`` is the ``program_id`` the API layer stamps/filters on;
    it is NEVER taken from a client header (it is deployment config, A1).
    """

    active_program_ids: list[Program]
    active_program_id: Program

    @model_validator(mode="after")
    def _active_id_in_list(self) -> Programs:
        if self.active_program_id not in self.active_program_ids:
            raise ValueError(
                f"programs.active_program_id {self.active_program_id.value!r} must be one of "
                f"active_program_ids {[p.value for p in self.active_program_ids]!r}"
            )
        return self

    @model_validator(mode="after")
    def _active_ids_non_empty_unique(self) -> Programs:
        if not self.active_program_ids:
            raise ValueError("programs.active_program_ids must be non-empty")
        if len(set(self.active_program_ids)) != len(self.active_program_ids):
            raise ValueError(
                f"programs.active_program_ids must be unique, got "
                f"{[p.value for p in self.active_program_ids]!r}"
            )
        return self


class Params(_StrictModel):
    """Typed view of the whole params file — one field per §8 top-level block."""

    programs: Programs
    work_queue: WorkQueue
    conversion: Conversion
    enrollment: Enrollment
    assignment: Assignment
    nurture: Nurture
    sis: Sis
    bulk: Bulk
    back_to_school: BackToSchool
    realistic: Realistic
    funding: Funding
    # Per-program voucher RULES + DEADLINES (R2). Keyed by program (tx_tefa,
    # az_esa, …) so a new state is a CONFIG ROW, not a code change. Owns only the
    # new window/rule values; award AMOUNTS stay in ``funding`` (INV-11).
    voucher_programs: dict[str, VoucherProgram]
    eval_thresholds: EvalThresholds
    cost_caps: CostCaps
    anthropic_pricing: AnthropicPricing
    latency_budget_ms: LatencyBudgetMs
    geo: Geo
    brand_memory: BrandMemory
    library_ingest: LibraryIngest
    posted_gallery: PostedGallery
    creator_scoring: CreatorScoring
    kpi: Kpi
    scheduler: Scheduler
    crm: Crm
    security: Security


def _resolve_path(path: Path | None) -> Path:
    """Resolve which params file to load.

    Precedence: explicit `path` arg → `PARAMS_PATH` env var → the default
    `params/params.yaml`.
    """
    if path is not None:
        return path
    env_path = os.environ.get("PARAMS_PATH")
    if env_path:
        return Path(env_path)
    return _DEFAULT_PARAMS_PATH


def load_params(path: Path | None = None) -> Params:
    """Load and validate the params file into typed Pydantic models.

    Args:
        path: Explicit params file to load. If omitted, falls back to the
            `PARAMS_PATH` env var, then to `params/params.yaml`.

    Returns:
        A validated `Params` object exposing every §8 block as typed fields.

    Raises:
        FileNotFoundError: if the resolved path does not exist.
        pydantic.ValidationError: if a required key is missing, an unknown key
            is present, or a value has the wrong type (drift fails the build).
    """
    resolved = _resolve_path(path)
    raw: Any = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return Params.model_validate(raw)
