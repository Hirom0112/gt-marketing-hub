"""Keep / discard — the §5.3 human-gated state write for content (FR-3.4/3.5; INV-2).

The marketing analog of the §5.2 approve path. The human's keep/discard is the
SOLE trigger for any state write (INV-2 — the deterministic core owns writes; the
LLM only proposes):

- :func:`keep` REQUIRES the candidate's :class:`ValidationResult` to have
  ``passed is True`` — you cannot keep an un-passed candidate (INV-3 / FR-4.3);
  a non-passing keep RAISES :class:`KeepRefused` (the API maps it to 409). On a
  valid keep it: promotes a ``kept`` + validated :class:`LibraryAsset` to the
  library (FR-3.4), AFFIRMS brand memory via
  :func:`app.ai.conditioning.apply_keep` (so the kept item conditions the NEXT
  batch — the FR-3.2 loop), and LOGS an ``approve`` decision (NFR-6).
- :func:`discard` STRENGTHENS a discard/dont signal via
  :func:`app.ai.conditioning.apply_discard` and LOGS a ``discard`` decision. It
  creates NO library asset and affirms NO exemplar.

This is the composition layer (CLAUDE.md §7): it may import ``app.ai`` /
``app.observability`` / ``app.adapters``. It hardcodes no tunable — the brand
weight step flows from ``params.brand_memory.weight_step`` through
``apply_keep`` / ``apply_discard`` (INV-11).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.ai.conditioning import apply_discard, apply_keep
from app.ai.schemas.brand import LibraryAsset, LibraryAssetType
from app.ai.schemas.content import (
    ContentFormat,
    LifecycleStage,
)
from app.observability.log_store import DecisionAction

if TYPE_CHECKING:
    from app.ai.schemas.content import ContentCandidate
    from app.core.eval_gate import ValidationResult
    from app.core.params import Params
    from app.marketing.library import ContentLibrary
    from app.observability.log_store import ObservabilityLog

# The audited reviewer identity. v1 has no auth; the operator is a fixed seam (A-3).
DEFAULT_HUMAN = "operator"

# Namespaced tag prefixes for the campaign axes a kept candidate carries
# (campaign-tagging-on-keep). These are LABELS, not tunables — they identify a
# kept asset's campaign theme and target GEO prompt so it is unambiguous and
# searchable in the library (FR-3.4). They are module constants, NOT params-file
# entries (CLAUDE §1 INV-11 / the task's tagging convention: label strings have no
# canonical params home). A candidate from the non-campaign route carries neither
# axis ⇒ neither tag is added (graceful).
CAMPAIGN_TAG_PREFIX = "campaign:"
GEO_TAG_PREFIX = "geo:"

# Map a content channel/format to a library asset type (§5). Falls back to COPY
# for any channel/format not in the explicit map — copy is the safe default unit.
_FORMAT_TO_ASSET_TYPE: dict[ContentFormat, LibraryAssetType] = {
    ContentFormat.FAQ_BLOCK: LibraryAssetType.FAQ_BLOCK,
    ContentFormat.COMPARISON_TABLE: LibraryAssetType.COMPARISON_TABLE,
    ContentFormat.BLOG_POST: LibraryAssetType.BLOG_POST,
    ContentFormat.IMAGE_BRIEF: LibraryAssetType.IMAGE,
    ContentFormat.VIDEO_SCRIPT: LibraryAssetType.VIDEO,
}


class KeepRefused(Exception):
    """Raised when keep is called on a candidate whose eval did not pass (INV-3).

    A candidate cannot reach the library / brand memory without a passing
    :class:`ValidationResult` (FR-4.3). The API maps this to HTTP 409.
    """


def _campaign_tags(campaign_theme: str | None, target_geo_prompt: str | None) -> list[str]:
    """The namespaced campaign-axis tags for a kept candidate (campaign-tagging-on-keep).

    Returns ``campaign:<theme>`` and/or ``geo:<prompt>`` for whichever axes are
    present. A candidate from the non-campaign route passes neither ⇒ an empty
    list (no campaign/geo tags — graceful, no crash).
    """
    tags: list[str] = []
    if campaign_theme:
        tags.append(f"{CAMPAIGN_TAG_PREFIX}{campaign_theme}")
    if target_geo_prompt:
        tags.append(f"{GEO_TAG_PREFIX}{target_geo_prompt}")
    return tags


def _search_text_for(candidate: ContentCandidate, campaign_tags: list[str]) -> str:
    """Denormalize a lower-cased search index from a candidate (FR-3.4 promotion).

    The library searches over this single field, so we build it once on promotion
    from the human-meaningful text: concept + copy + audience + channel + format,
    PLUS any campaign-axis tags so a kept campaign asset is findable by its theme /
    target GEO prompt (campaign-tagging-on-keep).
    """
    parts = [
        candidate.concept,
        candidate.copy_text,
        candidate.audience_tag.value,
        candidate.channel.value,
        candidate.format.value,
        *campaign_tags,
    ]
    return " ".join(part for part in parts if part).lower()


def _asset_from_candidate(
    candidate: ContentCandidate,
    validation_id: str,
    *,
    campaign_theme: str | None = None,
    target_geo_prompt: str | None = None,
) -> LibraryAsset:
    """Build the kept + validated :class:`LibraryAsset` promoted on keep (§5/FR-3.4).

    When the candidate originated from a CAMPAIGN batch, its campaign theme and
    target GEO prompt are threaded in as namespaced tags (``campaign:<theme>`` /
    ``geo:<prompt>``) and folded into ``search_text`` so the kept asset is findable
    by them (campaign-tagging-on-keep). A non-campaign candidate passes neither and
    gets only the existing audience/channel tags.
    """
    asset_type = _FORMAT_TO_ASSET_TYPE.get(candidate.format, LibraryAssetType.COPY)
    campaign_tags = _campaign_tags(campaign_theme, target_geo_prompt)
    return LibraryAsset(
        id=f"lib-{candidate.id}",
        title=candidate.concept,
        asset_type=asset_type,
        channel=candidate.channel,
        format=candidate.format,
        body=candidate.copy_text,
        source_ref=candidate.id,
        tags=[candidate.audience_tag.value, candidate.channel.value, *campaign_tags],
        search_text=_search_text_for(candidate, campaign_tags),
        validation=validation_id,
        lifecycle=LifecycleStage.KEPT,
        provenance=candidate.provenance,
    )


def keep(
    proposal_id: UUID,
    *,
    candidate: ContentCandidate,
    validation: ValidationResult,
    store: object,
    library: ContentLibrary,
    log: ObservabilityLog,
    params: Params,
    campaign_theme: str | None = None,
    target_geo_prompt: str | None = None,
) -> LibraryAsset:
    """Keep ``candidate`` — promote to library + brand memory + log approve (FR-3.4/3.5).

    REQUIRES ``validation.passed is True``; otherwise raises :class:`KeepRefused`
    (you cannot keep an un-passed candidate — INV-3). On a valid keep:

    1. promote a ``kept`` + validated :class:`LibraryAsset` to ``library``;
    2. AFFIRM brand memory (``apply_keep``) so it conditions the next batch
       (FR-3.2); the weight delta comes from params (INV-11);
    3. LOG an ``approve`` decision against ``proposal_id`` (NFR-6).

    Args:
        proposal_id: the logged proposal this candidate was surfaced under.
        candidate: the surfaced :class:`ContentCandidate` being kept.
        validation: the gate verdict — MUST have ``passed is True``.
        store: the brand-memory store (``apply_keep`` affirms an exemplar on it).
        library: the content library the kept asset is promoted to.
        log: the observability log (the approve decision is recorded here).
        params: the loaded params (the brand weight step, INV-11).
        campaign_theme: when the candidate came from a CAMPAIGN batch, its theme —
            tagged as ``campaign:<theme>`` (campaign-tagging-on-keep). Read back from
            the logged proposal (INV-2), never client-trusted. ``None`` for the
            non-campaign route ⇒ no campaign tag.
        target_geo_prompt: when set, the campaign's target GEO prompt — tagged as
            ``geo:<prompt>``. ``None`` ⇒ no geo tag.

    Returns:
        The promoted :class:`LibraryAsset`.

    Raises:
        KeepRefused: if ``validation.passed`` is not True.
    """
    if not validation.passed:
        raise KeepRefused(
            "cannot keep a candidate whose eval did not pass "
            f"(failed_rules={validation.failed_rules})"
        )

    # The library asset references the passing verdict; use the verdict's subject
    # ref / a stable id derived from the proposal so the audit chain links up.
    validation_id = validation.subject_ref or f"vr-{proposal_id}"
    asset = _asset_from_candidate(
        candidate,
        validation_id,
        campaign_theme=campaign_theme,
        target_geo_prompt=target_geo_prompt,
    )
    library.add(asset)

    # Affirm brand memory — the kept item conditions the NEXT batch (FR-3.2).
    apply_keep(store, candidate, params)  # type: ignore[arg-type]  # BrandMemoryStore boundary

    log.log_decision(proposal_id=proposal_id, human=DEFAULT_HUMAN, action=DecisionAction.APPROVE)
    return asset


def discard(
    proposal_id: UUID,
    *,
    candidate: ContentCandidate,
    store: object,
    log: ObservabilityLog,
    params: Params,
) -> None:
    """Discard ``candidate`` — strengthen a dont signal + log discard (FR-3.2; INV-2).

    Creates NO library asset and affirms NO exemplar. It strengthens the
    discard/dont signal via ``apply_discard`` (so the next batch reflects the
    rejection) and LOGS a ``discard`` decision (NFR-6). The weight delta comes
    from params (INV-11).
    """
    apply_discard(store, candidate, params)  # type: ignore[arg-type]  # BrandMemoryStore boundary
    log.log_decision(proposal_id=proposal_id, human=DEFAULT_HUMAN, action=DecisionAction.DISCARD)
