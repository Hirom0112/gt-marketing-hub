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


class Scorecard(_StrictModel):
    """kpi.scorecard — the weekly KPI scorecard's status band + pacing horizon (B5).

    The weekly scorecard (``core/weekly_scorecard.py``) reshapes the existing KPI
    sources into a per-metric this-week/last-week/delta/status/pace table; this is
    the single canonical home (INV-11) for the two dials it can't derive from the
    series itself:

    * status band — ``green_at``/``yellow_at`` as FRACTIONS of the metric's target.
      A metric is ``green`` when ``this_week >= green_at * target``, ``yellow`` when
      ``this_week >= yellow_at * target`` (but below green), else ``red``. The band
      must satisfy ``green_at >= yellow_at > 0`` (a higher bar can't sit below a
      lower one) — a drifted band fails the build at load (CLAUDE.md §4.1).
    * ``goal_date`` — the pacing horizon: the date the deterministic projection
      extrapolates the current value to. Weeks-to-goal is ``(goal_date - as_of)``
      with the injected reference date, so the projection carries no wall-clock.
    """

    green_at: float
    yellow_at: float
    goal_date: date

    @model_validator(mode="after")
    def _band_guard(self) -> Scorecard:
        if self.yellow_at <= 0.0:
            raise ValueError(f"kpi.scorecard.yellow_at must be > 0, got {self.yellow_at!r}")
        if self.green_at < self.yellow_at:
            raise ValueError(
                f"kpi.scorecard band must satisfy green_at >= yellow_at, got "
                f"green_at={self.green_at!r} < yellow_at={self.yellow_at!r}"
            )
        return self


class Kpi(_StrictModel):
    """FR-3.11 per-channel KPI rollup vs baseline/target (+ D-14 agent-KPI windows)."""

    # Channel-name keyed (keys are the `Channel` tokens).
    levers: dict[str, KpiLever]
    # Agent-dashboard time-window day-counts (D-14/D-15). Optional with the canonical
    # day/week/month defaults so a params file predating the field still loads.
    windows: KpiWindows = KpiWindows(day=1, week=7, month=30)
    # B5 weekly-scorecard status band + pacing horizon (the one canonical home).
    scorecard: Scorecard


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


class CrmSync(_StrictModel):
    """A2 CRM-as-truth incremental-poll tunables (RESEARCH_v2 §II.1; INV-11).

    The single canonical home for the CRM Search incremental poll's magic
    numbers — the pure planner (``core/crm_sync.py``) and the poller read them
    here, never a code literal:

    * ``result_cap`` — the 10,000-result cap per query; the window-chunking exists
      precisely so no single query approaches it.
    * ``chunk_days`` — the per-sub-window span (whole days) the [watermark, now]
      window is split into so one query stays under ``result_cap``.
    * ``search_qps`` — the CRM Search request-rate budget (queries/sec) the poller
      throttles to (HubSpot Search is more rate-limited than other CRM reads).

    The CRM Search 200-row page max is a FIXED HubSpot protocol ceiling, not a GT
    tunable, so its one home is the adapter's ``_SEARCH_PAGE_SIZE`` constant — not
    this params block (A-39).
    """

    result_cap: int
    chunk_days: int
    search_qps: int

    @model_validator(mode="after")
    def _bounds_valid(self) -> CrmSync:
        for name in ("result_cap", "chunk_days", "search_qps"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"crm_sync.{name} must be >= 1, got {value!r}")
        return self


class Stripe(_StrictModel):
    """A3 Stripe payments seam config (RESEARCH_v2 §II.2; INV-8/INV-11).

    The single canonical home for the Stripe boundary's magic numbers — the pure
    fulfillment core and the payments adapter read them here, never a code
    literal:

    * ``calls_per_run_cap`` — the hard per-run ceiling on outbound Stripe API
      calls (INV-8 guard, mirroring HubSpot's ``hubspot_calls_per_run_cap``): a
      breach degrades to the simulated adapter rather than a silent overspend.
    * ``tolerance_seconds`` — the webhook signature timestamp tolerance (Stripe's
      default 300 = 5 min, RESEARCH_v2 §II.2): a signed event whose timestamp is
      older than this is rejected as a replay.
    * ``fulfill_event_types`` — the Stripe event types that trigger fulfillment
      (e.g. ``checkout.session.completed``); any other event type is ignored.
    """

    calls_per_run_cap: int
    tolerance_seconds: int
    fulfill_event_types: list[str]

    @model_validator(mode="after")
    def _bounds_valid(self) -> Stripe:
        if self.calls_per_run_cap < 1:
            raise ValueError(
                f"stripe.calls_per_run_cap must be >= 1, got {self.calls_per_run_cap!r}"
            )
        if self.tolerance_seconds < 1:
            raise ValueError(
                f"stripe.tolerance_seconds must be >= 1, got {self.tolerance_seconds!r}"
            )
        if not self.fulfill_event_types:
            raise ValueError("stripe.fulfill_event_types must be non-empty")
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


class DataConfidence(_StrictModel):
    """A4 cross-module data-confidence threshold (INV-11).

    The single canonical home for the sync-parity floor below which the
    cross-module data-confidence banner activates: when overall sync-parity
    drops below ``min_parity`` the API surfaces the banner so a meaningfully
    out-of-sync cohort is visible rather than silently trusted. The API unit
    reads this value; this block only owns the threshold, never a code literal.

    ``min_parity`` is a FRACTION, so it MUST sit in [0.0, 1.0]; an out-of-range
    value is config drift and fails the build (CLAUDE.md §4.1).
    """

    min_parity: float

    @model_validator(mode="after")
    def _min_parity_is_fraction(self) -> DataConfidence:
        if not 0.0 <= self.min_parity <= 1.0:
            raise ValueError(
                f"data_confidence.min_parity must be in [0.0, 1.0], got {self.min_parity!r}"
            )
        return self


class Resilience(_StrictModel):
    """A5 retry/backoff tunables for a retryable adapter call (INV-11).

    The single canonical home for the resilience helper's magic numbers — the
    retry wrapper reads them here, never a code literal:

    * ``max_attempts`` — total attempts for a retryable call (1 initial + N
      retries); Stripe's sandbox does ~3 attempts (RESEARCH_v2 §II.2).
    * ``base_delay_ms`` — the exponential-backoff base delay (ms).
    * ``max_delay_ms`` — the backoff ceiling (ms) the exponential delay is
      clamped to.

    Each value MUST be ``>= 1``, and the ceiling MUST NOT sit below the base
    (a ``max_delay_ms < base_delay_ms`` is incoherent) — drift fails the build
    (CLAUDE.md §4.1).
    """

    max_attempts: int
    base_delay_ms: int
    max_delay_ms: int

    @model_validator(mode="after")
    def _bounds_valid(self) -> Resilience:
        for name in ("max_attempts", "base_delay_ms", "max_delay_ms"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"resilience.{name} must be >= 1, got {value!r}")
        if self.max_delay_ms < self.base_delay_ms:
            raise ValueError(
                "resilience.max_delay_ms must be >= base_delay_ms, got "
                f"{self.max_delay_ms!r} < {self.base_delay_ms!r}"
            )
        return self


class Rbac(_StrictModel):
    """B1 role-based access-control matrix — the single home (CLAUDE.md §7; INV-11).

    The one canonical home for the three-role permission matrix the authz core
    (``core/authz.py``) and the API ``require_role`` read; this block ONLY owns
    the surface, never a code literal:

    * ``roles`` — the closed set of roles. MUST be non-empty and contain the three
      canonical roles ``admin`` / ``leader`` / ``operator`` (a missing canonical
      role is config drift and fails the build, CLAUDE.md §4.1).
    * ``permissions`` — maps a NAMED PERMISSION → the list of roles that hold it
      (permission → roles). This direction is chosen so a ``permits`` lookup is the
      cleanest possible ``role in permissions.get(perm, [])`` (default-deny: an
      unknown permission grants nobody). Every role referenced here MUST be a
      declared role — a dangling/renamed role fails the build.
    """

    _CANONICAL_ROLES = ("admin", "leader", "operator")

    roles: list[str]
    # permission name → roles that hold it (permission → roles; see docstring).
    permissions: dict[str, list[str]]
    # demo_token_ttl_seconds — the lifetime (seconds) of a seat JWT minted by the
    # B1 demo-auth bridge (`POST /auth/demo-token`). The single home for the demo
    # token's expiry (INV-11 — no `now + 3600` literal in the endpoint). MUST be
    # >= 1 so a minted token is never already-expired.
    demo_token_ttl_seconds: int

    @model_validator(mode="after")
    def _ttl_positive(self) -> Rbac:
        if self.demo_token_ttl_seconds < 1:
            raise ValueError(
                f"rbac.demo_token_ttl_seconds must be >= 1, got {self.demo_token_ttl_seconds!r}"
            )
        return self

    @model_validator(mode="after")
    def _roles_complete(self) -> Rbac:
        if not self.roles:
            raise ValueError("rbac.roles must be non-empty")
        missing = [r for r in self._CANONICAL_ROLES if r not in self.roles]
        if missing:
            raise ValueError(
                f"rbac.roles must contain the canonical roles {list(self._CANONICAL_ROLES)!r}, "
                f"missing {missing!r}"
            )
        return self

    @model_validator(mode="after")
    def _no_dangling_role(self) -> Rbac:
        declared = set(self.roles)
        for perm, granted in self.permissions.items():
            dangling = [r for r in granted if r not in declared]
            if dangling:
                raise ValueError(
                    f"rbac.permissions[{perm!r}] references unknown role(s) {dangling!r}; "
                    f"every role must be declared in rbac.roles {self.roles!r}"
                )
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


class NurtureEscalation(_StrictModel):
    """nurture.escalation — the hot-family → Decision-Queue escalation bar (Module 11; INV-11).

    The single canonical home for the threshold at which a HOT family (high-value,
    at-risk, recoverable) auto-escalates from Nurture/Grassroots into the leadership
    Decision Queue. ``recoverable_now_min`` is the cutoff on the work-queue
    ``recoverable_now`` ranking key (``value × likelihood × freshness`` — the existing
    deriver, NOT a new score): a family at/above it is enqueued as ONE open escalation
    decision. A threshold, never a code literal — the auto-flag reads it here. MUST be
    ``> 0`` (a non-positive bar would escalate every stalled family).
    """

    recoverable_now_min: float

    @model_validator(mode="after")
    def _positive(self) -> NurtureEscalation:
        if self.recoverable_now_min <= 0:
            raise ValueError(
                f"nurture.escalation.recoverable_now_min must be > 0, got "
                f"{self.recoverable_now_min!r}"
            )
        return self


class NurtureLifecycle(_StrictModel):
    """nurture.lifecycle — Module 5 (Nurture & Lifecycle) view dials (INV-11).

    The single canonical home for the Module-5 surface's surfaced thresholds and label
    sets — the pure core (``core/nurture.py``) reads them here, never a code literal:

    * ``sla_window_hours`` — the first-contact SLA window (hours) a new applicant must
      be contacted within (the 5f compliance denominator). MUST be ``>= 1``.
    * ``stuck_in_stage_days`` — a deal idle in one pipeline stage longer than this many
      days reads STUCK (the 5c stuck alert). MUST be ``>= 1``.
    * ``sequence_health_min_open_pct`` / ``sequence_health_min_click_pct`` — a sequence
      whose average open/click rate falls below these PERCENT floors flags unhealthy
      (5d). Each a percent in [0, 100].
    * ``income_master_threshold_usd`` — the self-reported income (whole USD) at/above
      which a family is the >$160K "master" income band (the heatmap join + tiering).
      MUST be ``>= 1``.
    * ``engagement_tiers`` — the closed, ordered engagement-tier LABELS
      (clicked/opened/cold). The first two (clicked + opened) are the REACHABLE tiers;
      the last is cold. MUST be non-empty + free of duplicates.
    * ``theme_keyword_rules`` — theme label → the keyword list that v1 keyword tagging
      matches an inbound SMS against (5e). MUST be non-empty.
    * ``tier_planning_sizes`` — planning audience size per tier (T1/T2/T3) — the 5b
      tier-panel target sizes. MUST be non-empty; every size ``>= 0``.
    * ``pipeline_stage_order`` — the ordered deal-stage labels of the Enrollment Sales
      Pipeline (interest → … → closed_lost) the 5c distribution renders. Non-empty.
    * ``handoff_stages`` — the stage labels that count as a marketing→onboarding HANDOFF
      (enroll/tuition). MUST be non-empty + a subset of ``pipeline_stage_order``.
    * ``week_days`` / ``month_days`` — the weekly/monthly look-back windows (days) the
      handoff + SMS reply counts use. Each ``>= 1``.
    """

    sla_window_hours: int
    stuck_in_stage_days: int
    sequence_health_min_open_pct: float
    sequence_health_min_click_pct: float
    income_master_threshold_usd: int
    engagement_tiers: list[str]
    theme_keyword_rules: dict[str, list[str]]
    tier_planning_sizes: dict[str, int]
    pipeline_stage_order: list[str]
    handoff_stages: list[str]
    week_days: int
    month_days: int

    @model_validator(mode="after")
    def _bounds_valid(self) -> NurtureLifecycle:
        for name in ("sla_window_hours", "stuck_in_stage_days", "week_days", "month_days"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"nurture.lifecycle.{name} must be >= 1, got {value!r}")
        if self.income_master_threshold_usd < 1:
            raise ValueError(
                "nurture.lifecycle.income_master_threshold_usd must be >= 1, got "
                f"{self.income_master_threshold_usd!r}"
            )
        for name in ("sequence_health_min_open_pct", "sequence_health_min_click_pct"):
            value = getattr(self, name)
            if not 0.0 <= value <= 100.0:
                raise ValueError(f"nurture.lifecycle.{name} must be in [0, 100], got {value!r}")
        if not self.engagement_tiers:
            raise ValueError("nurture.lifecycle.engagement_tiers must be non-empty")
        if len(set(self.engagement_tiers)) != len(self.engagement_tiers):
            raise ValueError(
                f"nurture.lifecycle.engagement_tiers must not repeat, got {self.engagement_tiers!r}"
            )
        if not self.theme_keyword_rules:
            raise ValueError("nurture.lifecycle.theme_keyword_rules must be non-empty")
        if not self.tier_planning_sizes:
            raise ValueError("nurture.lifecycle.tier_planning_sizes must be non-empty")
        bad = {k: v for k, v in self.tier_planning_sizes.items() if v < 0}
        if bad:
            raise ValueError(f"nurture.lifecycle.tier_planning_sizes must be >= 0, got {bad!r}")
        if not self.pipeline_stage_order:
            raise ValueError("nurture.lifecycle.pipeline_stage_order must be non-empty")
        if not self.handoff_stages:
            raise ValueError("nurture.lifecycle.handoff_stages must be non-empty")
        unknown = [s for s in self.handoff_stages if s not in self.pipeline_stage_order]
        if unknown:
            raise ValueError(
                "nurture.lifecycle.handoff_stages must be a subset of pipeline_stage_order; "
                f"unknown {unknown!r}"
            )
        return self


class Nurture(_StrictModel):
    """nurture — the later-lifecycle policy dials (INV-11; all BUSINESS-owned).

    Every value here is a team dial, not engineering: how long until COLD, when a
    silent family is PRESUMED LOST (human-confirmed), the base re-contact cadence and
    touch cap before DORMANT, the channel order, the school-year anchors that ramp
    re-engagement, the hot-family escalation bar (Module 11) at which a high-value
    at-risk family auto-flags into the leadership Decision Queue, and the Module-5
    Nurture & Lifecycle view dials (``lifecycle``). Nurture EXECUTION (the actual drip
    sends) is HubSpot's job (the locked seam plan); the cockpit owns these dials and
    pushes the trigger.
    """

    cold_after_days: int
    presumed_lost: PresumedLost
    base_recontact_interval_months: int
    max_touches: int
    channel_priority: list[str]
    anchors: list[NurtureAnchor]
    long_horizon: LongHorizon
    escalation: NurtureEscalation
    lifecycle: NurtureLifecycle


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
    # app_runtime_read_token_ttl_seconds — lifetime (seconds) of the short-lived
    # HS256 read token the live Supabase repo mints to authenticate program-scoped
    # reads AS the non-`BYPASSRLS` `app_runtime` role (A-38). The token is minted
    # per request and consumed immediately; the TTL only needs to exceed one
    # request's wall-clock. INV-11: the one canonical home for this tunable.
    app_runtime_read_token_ttl_seconds: int = 300

    @model_validator(mode="after")
    def _read_token_ttl_positive(self) -> Programs:
        if self.app_runtime_read_token_ttl_seconds < 1:
            raise ValueError(
                "programs.app_runtime_read_token_ttl_seconds must be >= 1, got "
                f"{self.app_runtime_read_token_ttl_seconds!r}"
            )
        return self

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


class Budget(_StrictModel):
    """B4 marketing-budget partition + variance-flag threshold (INV-11).

    The single canonical home for the $365K marketing budget plan the variance
    reconciler (``core/budget.py``) reads; this block ONLY owns the surface,
    never a code literal:

    * ``total_usd`` — the whole marketing budget (whole USD).
    * ``variance_threshold`` — the actual-vs-planned OVERRUN fraction past which a
      workstream auto-flags (``> threshold`` flags; at/under does not). A FRACTION
      in (0, 1].
    * ``watch_frac`` — the health-band WATCH threshold as a FRACTION of planned: a
      workstream whose ``actual >= watch_frac * planned`` (but not yet over budget
      / past the variance threshold) reads ``watch`` rather than ``on_track``. A
      FRACTION in (0, 1] — the canonical home for the 10b per-workstream health
      indicator's lower band (the upper band reuses ``variance_threshold``).
    * ``workstreams`` — per-workstream planned spend (whole USD), keyed by
      workstream token.

    A ``model_validator(mode="after")`` enforces the PARTITION GUARD:
    ``sum(workstreams.values()) == total_usd`` exactly — this is the
    sum-to-$365K guarantee, and any drift raises at load (CLAUDE.md §4.1). The
    total and every workstream amount MUST be ``>= 1``.
    """

    total_usd: int
    variance_threshold: float
    watch_frac: float
    workstreams: dict[str, int]

    @model_validator(mode="after")
    def _partition_guard(self) -> Budget:
        if self.total_usd < 1:
            raise ValueError(f"budget.total_usd must be >= 1, got {self.total_usd!r}")
        if not 0.0 < self.variance_threshold <= 1.0:
            raise ValueError(
                f"budget.variance_threshold must be in (0, 1], got {self.variance_threshold!r}"
            )
        if not 0.0 < self.watch_frac <= 1.0:
            raise ValueError(f"budget.watch_frac must be in (0, 1], got {self.watch_frac!r}")
        if not self.workstreams:
            raise ValueError("budget.workstreams must be non-empty")
        bad = {k: v for k, v in self.workstreams.items() if v < 1}
        if bad:
            raise ValueError(f"budget.workstreams amounts must be >= 1, got {bad!r}")
        allocated = sum(self.workstreams.values())
        if allocated != self.total_usd:
            raise ValueError(
                f"budget.workstreams must partition total_usd exactly: "
                f"sum {allocated!r} != total_usd {self.total_usd!r}"
            )
        return self


class GrassrootsTargets(_StrictModel):
    """grassroots.targets — the four Grassroots goal-bar targets (Module 2; INV-11).

    The single canonical home for the targets the goal-progress core
    (``core/grassroots.py``) measures the roster against — never a code literal:

    * ``active_ambassadors`` — the target count of ACTIVE/CHAMPION ambassadors.
    * ``warm_intros`` — the target total of warm introductions made.
    * ``p2p_calls`` — the target total of peer-to-peer calls logged.
    * ``influenced_enrollments`` — the target count of grassroots-influenced
      enrollments (the attributed conversions).

    Every target MUST be ``>= 1`` (a zero target makes the progress bar's pct
    undefined — drift fails the build, CLAUDE.md §4.1).
    """

    active_ambassadors: int
    warm_intros: int
    p2p_calls: int
    influenced_enrollments: int

    @model_validator(mode="after")
    def _targets_positive(self) -> GrassrootsTargets:
        for name in ("active_ambassadors", "warm_intros", "p2p_calls", "influenced_enrollments"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"grassroots.targets.{name} must be >= 1, got {value!r}")
        return self


class GrassrootsMarketMap(_StrictModel):
    """grassroots.market_map — the community market-map category labels (Module 2).

    ``categories`` is the closed list of AGGREGATE community-segment labels (parent
    groups / homeschool co-ops / chess clubs / robotics teams / debate leagues / math
    circles / …) the market map groups nodes by — aggregate labels only, never a real
    org/person identity (INV-1/INV-6). The single canonical home (INV-11); the market
    summary reads it, never a code literal. MUST be non-empty and free of duplicates.
    """

    categories: list[str]

    @model_validator(mode="after")
    def _categories_valid(self) -> GrassrootsMarketMap:
        if not self.categories:
            raise ValueError("grassroots.market_map.categories must be non-empty")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError(
                f"grassroots.market_map.categories must not repeat a label, got {self.categories!r}"
            )
        return self


class GrassrootsSprintHealth(_StrictModel):
    """grassroots.sprint_health — the referral-sprint pacing band (Module 2; INV-11).

    The single canonical home for the threshold the sprint-health core
    (``core/grassroots.py``) reads — never a code literal. ``behind_pace_frac`` is the
    FRACTION of the linearly-expected conversions (``families_identified * elapsed``)
    a sprint must keep up with to read ``on_pace``; below it the sprint reads
    ``behind``. A FRACTION in (0, 1].
    """

    behind_pace_frac: float

    @model_validator(mode="after")
    def _frac_is_valid(self) -> GrassrootsSprintHealth:
        if not 0.0 < self.behind_pace_frac <= 1.0:
            raise ValueError(
                f"grassroots.sprint_health.behind_pace_frac must be in (0, 1], "
                f"got {self.behind_pace_frac!r}"
            )
        return self


class Grassroots(_StrictModel):
    """grassroots — Module 2 (Grassroots Engine) business tunables (INV-11).

    The single canonical home for the Grassroots surface's surfaced numbers — the
    deterministic core (``core/grassroots.py``) reads them here, never a code literal:

    * ``targets`` — the four goal-bar targets.
    * ``market_map`` — the aggregate community-category labels.
    * ``sprint_health`` — the referral-sprint pacing band.
    """

    targets: GrassrootsTargets
    market_map: GrassrootsMarketMap
    sprint_health: GrassrootsSprintHealth


class FieldEvents(_StrictModel):
    """field_events — Module 8 (Field Marketing & Events) business tunables (INV-11).

    The single canonical home for the Field & Events surface's surfaced configuration —
    the deterministic core (``core/field_events.py``) reads them here, never a code
    literal:

    * ``event_types`` — the closed list of GT-organized event-type LABELS (shadow_day /
      chess_tournament / ama / community_event / festival / webinar). Aggregate labels
      only (INV-1/INV-6). The 0039 ``field_event`` CHECK is the DB backstop. MUST be
      non-empty and free of duplicates.
    * ``upcoming_window_days`` — the look-ahead window (in days) the overview's
      ``upcoming_count`` counts events within. MUST be ``>= 1``.
    """

    event_types: list[str]
    upcoming_window_days: int

    @model_validator(mode="after")
    def _guard(self) -> FieldEvents:
        if not self.event_types:
            raise ValueError("field_events.event_types must be non-empty")
        if len(set(self.event_types)) != len(self.event_types):
            raise ValueError(
                f"field_events.event_types must not repeat a label, got {self.event_types!r}"
            )
        if self.upcoming_window_days < 1:
            raise ValueError(
                f"field_events.upcoming_window_days must be >= 1, got {self.upcoming_window_days!r}"
            )
        return self


class ContentCalendar(_StrictModel):
    """content.calendar — the editorial-calendar conflict rule (Module 3; INV-11).

    ``conflict_threshold`` is the number of calendar entries scheduled on the SAME
    day at/above which that day is flagged a SCHEDULING CONFLICT (too many pieces
    competing for one day). The single canonical home (INV-11); the conflict deriver
    (``core/content_analytics.py``) reads it, never a code literal. MUST be ``>= 2``
    (a threshold of 1 would flag every scheduled day).
    """

    conflict_threshold: int

    @model_validator(mode="after")
    def _threshold_valid(self) -> ContentCalendar:
        if self.conflict_threshold < 2:
            raise ValueError(
                f"content.calendar.conflict_threshold must be >= 2, got {self.conflict_threshold!r}"
            )
        return self


class ContentRankings(_StrictModel):
    """content.rankings — the per-piece top/bottom ranking sizes (Module 3; INV-11).

    ``top_n``/``bottom_n`` cap how many best/worst pieces the performance rollup
    surfaces. The single canonical home (INV-11); the ranking core reads them, never
    a code literal. Each MUST be ``>= 1``.
    """

    top_n: int
    bottom_n: int

    @model_validator(mode="after")
    def _sizes_positive(self) -> ContentRankings:
        for name in ("top_n", "bottom_n"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"content.rankings.{name} must be >= 1, got {value!r}")
        return self


class Content(_StrictModel):
    """content — Module 3 (Content & Thought Leadership) business tunables (INV-11).

    The single canonical home for the Content surface's surfaced numbers — the
    deterministic core (``core/content_analytics.py``) and the demo seed read them
    here, never a code literal:

    * ``channels`` — the closed list of content channel labels (substack / x /
      instagram / facebook / podcast / email / youtube). Aggregate labels only
      (INV-1/INV-6); the channel CATEGORIES live here, not in the migration.
    * ``x_conversion_rate`` — the "42% conversion engine" figure for X/Twitter: the
      demo seed derives X's conversions from this rate (so the surfaced ~42% is a
      REAL computed rate over seeded reach/clicks, never a hardcoded headline). A
      FRACTION in [0, 1].
    * ``calendar`` — the editorial-calendar conflict rule.
    * ``rankings`` — the per-piece top/bottom ranking sizes.
    """

    channels: list[str]
    x_conversion_rate: float
    calendar: ContentCalendar
    rankings: ContentRankings

    @model_validator(mode="after")
    def _content_valid(self) -> Content:
        if not self.channels:
            raise ValueError("content.channels must be non-empty")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError(f"content.channels must not repeat a label, got {self.channels!r}")
        if not 0.0 <= self.x_conversion_rate <= 1.0:
            raise ValueError(
                f"content.x_conversion_rate must be in [0, 1], got {self.x_conversion_rate!r}"
            )
        return self


class SummerCamp(_StrictModel):
    """summer_camp — Summer Camp dual-source reconcile business tunables (INV-11).

    The single canonical home for the Summer Camp program's surfaced business
    numbers that ``GET /summer/reconcile`` reads — never a code literal:

    * ``campus_capacity`` — per-campus seat capacity (whole seats), keyed by campus.
      The reconcile rollup measures registrations against THESE seats.
    * ``price_per_seat_usd`` — the list price of one camp seat (whole USD).
    * ``revenue_target_usd`` — the season revenue target (whole USD; a SEPARATE P&L
      from the marketing ``budget`` block).
    * ``registration_channels`` — the signup-channel LABELS the channel breakdown
      buckets registrations into (word_of_mouth / social / email / website). The
      seed's per-row channel assignment is keyed off this list's order (INV-11 — the
      labels' one home; the distribution weights are documented in the seed).
    * ``registration_window_days`` — the recent-window size (in days) the
      "registrations this week" count uses (default a 7-day week).

    Every numeric value MUST be ``>= 1``; capacities + channels MUST be non-empty (an
    empty rollup/channel set is meaningless — drift fails the build, CLAUDE.md §4.1).
    """

    campus_capacity: dict[str, int]
    price_per_seat_usd: int
    revenue_target_usd: int
    registration_channels: list[str]
    registration_window_days: int

    @model_validator(mode="after")
    def _guard(self) -> SummerCamp:
        if not self.campus_capacity:
            raise ValueError("summer_camp.campus_capacity must be non-empty")
        bad = {k: v for k, v in self.campus_capacity.items() if v < 1}
        if bad:
            raise ValueError(f"summer_camp.campus_capacity seats must be >= 1, got {bad!r}")
        if self.price_per_seat_usd < 1:
            raise ValueError(
                f"summer_camp.price_per_seat_usd must be >= 1, got {self.price_per_seat_usd!r}"
            )
        if self.revenue_target_usd < 1:
            raise ValueError(
                f"summer_camp.revenue_target_usd must be >= 1, got {self.revenue_target_usd!r}"
            )
        if not self.registration_channels:
            raise ValueError("summer_camp.registration_channels must be non-empty")
        if self.registration_window_days < 1:
            raise ValueError(
                "summer_camp.registration_window_days must be >= 1, got "
                f"{self.registration_window_days!r}"
            )
        return self


class CrmOpsUtm(_StrictModel):
    """crm_ops.utm — UTM-health rule set (TODO_v2 §C1; INV-11).

    The single canonical home for the deterministic UTM-health deriver
    (``core/utm_health.py``); the deriver reads these, never a code literal:

    * ``required_keys`` — the UTM keys that MUST be present and non-blank
      (``utm_source`` / ``utm_medium`` / ``utm_campaign``); a missing one flags
      the UTM ``broken``.
    * ``allowed_mediums`` — the closed set of acceptable ``utm_medium`` values; a
      ``utm_medium`` outside this set flags ``broken``.

    Both lists MUST be non-empty — an empty rule set would silently pass every
    UTM, defeating the honesty mandate (drift fails the build, CLAUDE.md §4.1).
    """

    required_keys: list[str]
    allowed_mediums: list[str]

    @model_validator(mode="after")
    def _non_empty(self) -> CrmOpsUtm:
        if not self.required_keys:
            raise ValueError("crm_ops.utm.required_keys must be non-empty")
        if not self.allowed_mediums:
            raise ValueError("crm_ops.utm.allowed_mediums must be non-empty")
        return self


class CrmOpsDataQuality(_StrictModel):
    """crm_ops.data_quality — the auto data-quality queue's severity order (§C1).

    ``severity_order`` lists the known issue kinds highest-severity → lowest; the
    queue (``core/data_quality.py``) ranks each detected issue by its position
    here (a ``conflict`` outranks a ``utm_broken`` outranks an
    ``unreliable_field``). The single canonical home for that ordering (INV-11).

    The list MUST be non-empty, free of duplicates, and contain ONLY the known
    issue kinds (:data:`_KNOWN_ISSUE_KINDS`) — a stray/renamed kind is config
    drift and fails the build (CLAUDE.md §4.1).
    """

    # The closed set of data-quality issue kinds. Kept here (not imported from
    # core/data_quality.py) because params is CONSUMED by that module — importing
    # back would be circular. The deriver's literals must match this set.
    _KNOWN_ISSUE_KINDS = (
        "conflict",
        "utm_broken",
        "unreliable_field",
        "mojibake",
        "missing_field",
    )

    severity_order: list[str]

    @model_validator(mode="after")
    def _order_valid(self) -> CrmOpsDataQuality:
        if not self.severity_order:
            raise ValueError("crm_ops.data_quality.severity_order must be non-empty")
        if len(set(self.severity_order)) != len(self.severity_order):
            raise ValueError(
                f"crm_ops.data_quality.severity_order must not repeat a kind, got "
                f"{self.severity_order!r}"
            )
        unknown = [k for k in self.severity_order if k not in self._KNOWN_ISSUE_KINDS]
        if unknown:
            raise ValueError(
                f"crm_ops.data_quality.severity_order entries must be known issue kinds "
                f"{list(self._KNOWN_ISSUE_KINDS)!r}, got unknown {unknown!r}"
            )
        return self


class CrmOpsLeadScoreTiers(_StrictModel):
    """crm_ops.lead_score.tiers — the cold/warm/hot tier cutoffs (Module 7; INV-11).

    A lead score sits in a tier by its value: ``cold`` below ``warm_min``, ``warm``
    in ``[warm_min, hot_min)``, ``hot`` at/above ``hot_min``. The single canonical
    home for the tier breakdown the lead-scoring view derives. ``warm_min`` MUST be
    strictly below ``hot_min`` (an inverted/equal pair is config drift, §4.1).
    """

    warm_min: int
    hot_min: int

    @model_validator(mode="after")
    def _ordered(self) -> CrmOpsLeadScoreTiers:
        if not self.warm_min < self.hot_min:
            raise ValueError(
                f"crm_ops.lead_score.tiers.warm_min must be < hot_min, got "
                f"{self.warm_min!r} / {self.hot_min!r}"
            )
        return self


class CrmOpsLeadScore(_StrictModel):
    """crm_ops.lead_score — the lead-score histogram + tier tunables (Module 7; INV-11).

    The single home for the LIVE HubSpot ``gt_lead_score`` read shape:

    * ``bands`` — the ascending histogram band EDGES (e.g. ``[0, 20, 40, 60, 80,
      100]`` ⇒ five ``[low, high)`` bands). MUST be ≥2 entries, strictly ascending.
    * ``threshold`` — the lead-score threshold the scoring model qualifies on.
    * ``tiers`` — the cold/warm/hot tier cutoffs.
    """

    bands: list[int]
    threshold: int
    tiers: CrmOpsLeadScoreTiers

    @model_validator(mode="after")
    def _bands_ascending(self) -> CrmOpsLeadScore:
        if len(self.bands) < 2:
            raise ValueError(f"crm_ops.lead_score.bands must have ≥2 edges, got {self.bands!r}")
        if any(b >= a for b, a in zip(self.bands, self.bands[1:], strict=False)):
            raise ValueError(
                f"crm_ops.lead_score.bands must be strictly ascending, got {self.bands!r}"
            )
        return self


class CrmOps(_StrictModel):
    """C1 CRM/Marketing-Operations data-quality tunables (TODO_v2 §C1; INV-11).

    The shared single home for the deterministic CRM-Ops cores — UTM-health, the
    auto data-quality queue, and field-reliability flags — which are cohesive
    (they share this block; the queue composes UTM-health). Every value is a
    tunable home (INV-11); the derivers read them here, never a code literal:

    * ``utm`` — the UTM-health rule set (required keys + allowed mediums).
    * ``data_quality`` — the queue's severity order over the known issue kinds.
    * ``unreliable_fields`` — the fields known to be low-trust, which the
      field-reliability flag (``core/field_reliability.py``) marks ``unreliable``
      and the queue surfaces as an ``unreliable_field`` issue.
    * ``parity_floor`` — the sync-parity fraction below which the cross-module
      data-confidence banner activates (reuses the A4 banner). A FRACTION, so it
      MUST sit in [0.0, 1.0]; an out-of-range value fails the build (§4.1).
    * ``lead_score`` — the LIVE HubSpot lead-score histogram + tier cutoffs (M7).
    * ``drift_alert_floor`` — the FIELD-level parity fraction below which a
      sync-parity drift alert fires (the 5d view). A fraction in [0.0, 1.0].
    * ``attribution_chain_steps`` — the ordered step labels of the attribution
      chain the source-tracking view renders (form → Supabase → HubSpot).
    """

    utm: CrmOpsUtm
    data_quality: CrmOpsDataQuality
    unreliable_fields: list[str]
    parity_floor: float
    lead_score: CrmOpsLeadScore
    drift_alert_floor: float
    attribution_chain_steps: list[str]

    @model_validator(mode="after")
    def _parity_floor_is_fraction(self) -> CrmOps:
        if not 0.0 <= self.parity_floor <= 1.0:
            raise ValueError(
                f"crm_ops.parity_floor must be in [0.0, 1.0], got {self.parity_floor!r}"
            )
        if not 0.0 <= self.drift_alert_floor <= 1.0:
            raise ValueError(
                f"crm_ops.drift_alert_floor must be in [0.0, 1.0], got {self.drift_alert_floor!r}"
            )
        if not self.attribution_chain_steps:
            raise ValueError("crm_ops.attribution_chain_steps must be non-empty")
        return self


class OpenDataDatasets(_StrictModel):
    """open_data.datasets — the tryopendata.ai ``tea/*`` dataset slugs (E1; INV-11).

    The single canonical home for the dataset slugs the live adapter queries over
    ``POST /v1/query`` (RESEARCH_v2 §II.3). These are TEA aggregate/district-level
    datasets only (INV-1/INV-6 — never child-level): the A–F accountability
    ratings, STAAR accountability, and PEIMS public-school finance. The adapter
    reads these here, never a code literal; each MUST be non-empty.
    """

    accountability_ratings: str
    staar: str
    peims_finance: str
    student_enrollment: str

    @model_validator(mode="after")
    def _non_empty(self) -> OpenDataDatasets:
        for name in ("accountability_ratings", "staar", "peims_finance", "student_enrollment"):
            value = getattr(self, name)
            if not value or not str(value).strip():
                raise ValueError(f"open_data.datasets.{name} must be non-empty")
        return self


class OpenDataDecisionChange(_StrictModel):
    """open_data.decision_change — the district-performance thresholds (E1; INV-11).

    The single canonical home for the decision-change CORE's thresholds (the core
    that CONSUMES these is a separate later unit). Every value is a tunable, never
    a code literal:

    * ``low_rating_grades`` — the A–F accountability grades that count as
      low-performing (a district at one of these is a candidate for the boost).
      MUST be non-empty.
    * ``staar_proficiency_floor`` — a STAAR proficiency BELOW this fraction counts
      as low; a FRACTION in [0.0, 1.0].
    * ``min_enrollment`` — the district enrollment at/above which the boost applies
      (a thin district is not boosted); MUST be ``>= 1``.
    * ``priority_boost`` — how much the recommendation priority moves when a
      district trips the low-performance signal.
    """

    low_rating_grades: list[str]
    staar_proficiency_floor: float
    min_enrollment: int
    priority_boost: int

    @model_validator(mode="after")
    def _bounds_valid(self) -> OpenDataDecisionChange:
        if not self.low_rating_grades:
            raise ValueError("open_data.decision_change.low_rating_grades must be non-empty")
        if not 0.0 <= self.staar_proficiency_floor <= 1.0:
            raise ValueError(
                "open_data.decision_change.staar_proficiency_floor must be in [0.0, 1.0], "
                f"got {self.staar_proficiency_floor!r}"
            )
        if self.min_enrollment < 1:
            raise ValueError(
                "open_data.decision_change.min_enrollment must be >= 1, "
                f"got {self.min_enrollment!r}"
            )
        return self


class OpenData(_StrictModel):
    """E1 tryopendata.ai Texas-education enrichment seam config (RESEARCH_v2 §II.3).

    The single canonical home (INV-11) for the Open Data adapter family's tunables —
    the live adapter and the later decision-change core read them here, never a code
    literal:

    * ``datasets`` — the ``tea/*`` dataset slugs (aggregate/district-level; INV-1).
    * ``per_run_query_cap`` — the INV-8 per-run query budget; the live adapter's
      (cap+1)th ``/v1/query`` raises rather than overspending the metered API. MUST
      be ``>= 1``.
    * ``decision_change`` — the district-performance thresholds.
    """

    datasets: OpenDataDatasets
    per_run_query_cap: int
    decision_change: OpenDataDecisionChange

    @model_validator(mode="after")
    def _cap_valid(self) -> OpenData:
        if self.per_run_query_cap < 1:
            raise ValueError(
                f"open_data.per_run_query_cap must be >= 1, got {self.per_run_query_cap!r}"
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
    crm_sync: CrmSync
    crm_ops: CrmOps
    open_data: OpenData
    stripe: Stripe
    security: Security
    data_confidence: DataConfidence
    resilience: Resilience
    rbac: Rbac
    budget: Budget
    grassroots: Grassroots
    field_events: FieldEvents
    content: Content
    summer_camp: SummerCamp


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
