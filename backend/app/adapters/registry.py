"""Adapter registry — startup selection of impls by config (ARCHITECTURE.md §7, NFR-8).

§7 (authoritative): impls are "selected at startup by config (`adapters/registry.py`,
NFR-8). v1 wires all to Simulated. Going live = flipping config + supplying the
production impl, with zero changes to `core/` or `ai/`."

Most boundaries key on ``SEND_MODE`` and are locked to ``simulate`` in v1 (D-9,
OUT-1/2/3) — ``live`` **raises** ``NotImplementedError`` (no prod impl; fail loud).
The **CRM** boundary is the exception (S10): it has a production impl
(:class:`app.adapters.hubspot.live_adapter.LiveHubSpotCRMAdapter`) selected by its
own ``CRM_MODE`` seam, so it can push SYNTHETIC data into the real portal behind the
four guards without unlocking the simulated send/social/media modes. Every selector
reads **only** through :func:`app.core.settings.get_settings` (the §5 env seam;
never ``os.environ`` here).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from app.adapters.brand_memory.base import BrandMemoryStore
from app.adapters.brand_memory.sqlite_store import SqliteBrandMemoryStore
from app.adapters.funding.base import FundingSignalAdapter
from app.adapters.funding.simulated import SimulatedFundingSignalAdapter
from app.adapters.geo_sampling.base import GeoSamplingAdapter
from app.adapters.geo_sampling.simulated import SimulatedGeoSamplingAdapter
from app.adapters.hubspot.crm_adapter import CRMAdapter, SimulatedCRMAdapter
from app.adapters.hubspot.live_adapter import LiveHubSpotCRMAdapter
from app.adapters.media.base import MediaGenAdapter
from app.adapters.media.placeholder import PlaceholderMediaGenAdapter
from app.adapters.sentiment.base import SentimentAdapter
from app.adapters.sentiment.placeholder import PlaceholderSentimentAdapter
from app.adapters.sis.base import EnrollmentSystemAdapter
from app.adapters.social.base import SocialAdapter
from app.adapters.social.simulated import SimulatedSocialAdapter
from app.core.params import AwardAmounts, Crm, Params, load_params
from app.core.settings import CrmMode, Settings, get_settings

# Default on-disk home for the persistent brand-memory store when no override is
# supplied (ASSUMPTIONS A-11). The path is a config seam (env > default), not a
# hardcoded magic in logic (INV-11) — overridable via ``BRAND_MEMORY_DB_PATH``.
_DEFAULT_BRAND_MEMORY_DB_PATH = "data/brand_memory.db"

# HubSpot CRM v3 base URL — the fixed third-party API host (not a tunable).
_HUBSPOT_BASE_URL = "https://api.hubapi.com"

# Committed example params, used as a fallback when no local params.yaml exists
# (it is gitignored / absent in this env). Resolved relative to the repo root:
# backend/app/adapters/registry.py → parents[3] is the repo root. Same fallback
# the API dependency layer uses (app/api/deps.py) — same values either way (INV-11).
_EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"


def _load_params() -> Params:
    """Load params, falling back to the committed example.

    ``load_params()`` resolves ``params/params.yaml``; when that gitignored file is
    absent we fall back to ``params/params.example.yaml`` so the live adapter is
    constructable in any env (the example carries the same blocks; INV-11).
    """
    try:
        return load_params()
    except FileNotFoundError:
        return load_params(_EXAMPLE_PARAMS)


def _load_crm_params() -> Crm:
    """Load the ``crm`` params block (see :func:`_load_params`)."""
    return _load_params().crm


def _load_award_amounts() -> AwardAmounts:
    """Load the ``funding.award_amounts`` params block (see :func:`_load_params`)."""
    return _load_params().funding.award_amounts


def effective_crm_mode(settings: Settings) -> CrmMode:
    """The CRM mode the registry would ACTUALLY select for ``settings`` (pure).

    The single source of truth for the §7/INV-8 precedence, extracted so a
    read-only status surface can REPORT the effective seam state without
    constructing a live adapter (or an httpx client) and without forking the
    precedence (INV-11 — one canonical home). :func:`get_crm_adapter` consumes
    this; nothing here reads the env or does I/O.

    - ``CRM_MODE=simulate`` ⇒ ``"simulate"`` (the default recorder; INV-9).
    - ``CRM_MODE=live`` + token + **kill switch on** ⇒ ``"simulate"`` — guard 3
      (INV-8) degrades to the recorder; never a live call when the kill switch is on.
    - ``CRM_MODE=live`` + token + no kill switch ⇒ ``"live"`` (the live adapter).
    - ``CRM_MODE=live`` + **no token** ⇒ ``"live"`` — a live INTENT that is
      misconfigured; :func:`get_crm_adapter` fails loud (``RuntimeError``) rather
      than silently degrade (INV-9). Reported as ``"live"`` so the misconfig stays
      visible, not hidden behind a false ``"simulate"``.
    """
    if settings.crm_mode == "simulate":
        return "simulate"
    # CRM_MODE == "live": the kill switch forces simulate even with a valid token
    # (guard 3, INV-8). A missing token is a live INTENT that fails loud at
    # construction (get_crm_adapter) — reported here as the "live" intent.
    if settings.hubspot_private_app_token is not None and settings.hubspot_kill_switch:
        return "simulate"
    return "live"


def get_crm_adapter() -> CRMAdapter:
    """Return the CRM adapter impl for the current ``CRM_MODE`` (S10 W2; §7, NFR-8).

    The CRM boundary is the one with a production impl (the live HubSpot adapter),
    so it keys on its own ``CRM_MODE`` seam, independent of the v1 ``SEND_MODE`` lock:

    - ``simulate`` (default) ⇒ a fresh :class:`SimulatedCRMAdapter` (records, never
      sends; INV-9) — unchanged behavior.
    - ``live`` + token + **kill switch set** ⇒ degrade to :class:`SimulatedCRMAdapter`
      (guard 3, INV-8) — never a live call when the kill switch is on.
    - ``live`` + token + no kill switch ⇒ :class:`LiveHubSpotCRMAdapter` (pushes
      synthetic data behind the four guards).
    - ``live`` + **no token** ⇒ ``RuntimeError`` — fail loud on misconfig rather
      than silently degrading to simulated (INV-9).

    The simulate-vs-live decision is delegated to :func:`effective_crm_mode` (the
    one canonical precedence, INV-11); the only branch it can't carry is the
    fail-loud on ``live`` + no token, kept here at construction (INV-9).

    Raises:
        RuntimeError: when ``CRM_MODE=live`` but no HubSpot token is configured.
    """
    settings = get_settings()

    # CRM_MODE == "live" + no token ⇒ fail loud on misconfig (INV-9). effective_crm_mode
    # reports this as the live INTENT; the construction-time RuntimeError lives here.
    if settings.crm_mode == "live" and settings.hubspot_private_app_token is None:
        raise RuntimeError(
            "CRM_MODE='live' requires HUBSPOT_PRIVATE_APP_TOKEN — none is configured. "
            "Fail loud on misconfig rather than silently degrade to simulated "
            "(INV-9). Set the token or use CRM_MODE='simulate'."
        )

    # Single canonical precedence: simulate (default), or live degraded to simulate
    # by the kill switch (guard 3, INV-8). A live result needs a token (guaranteed
    # non-None by the guard above).
    if effective_crm_mode(settings) == "simulate":
        return SimulatedCRMAdapter()

    # A live result implies a token — the no-token case raised above. Bind to a
    # local so the type narrows from ``str | None`` to ``str`` (the guard above is
    # the runtime proof; this re-check is the static one, and fails loud if the
    # invariant is ever broken — never a None token to the live adapter, INV-9).
    token = settings.hubspot_private_app_token
    if token is None:  # pragma: no cover — unreachable past the fail-loud guard
        raise RuntimeError(
            "CRM_MODE='live' requires HUBSPOT_PRIVATE_APP_TOKEN — none is configured."
        )
    client = httpx.Client(base_url=_HUBSPOT_BASE_URL)
    return LiveHubSpotCRMAdapter(
        client=client,
        token=token,
        crm=_load_crm_params(),
        award_amounts=_load_award_amounts(),
        calls_per_run_cap=settings.hubspot_calls_per_run_cap,
    )


def get_funding_signal_adapter() -> FundingSignalAdapter:
    """Return the funding-signal adapter for the current mode (§7.2, FR-2.7).

    The §7.2 boundary reads a **GT-controlled** signal — never an Odyssey/TEFA
    status feed (INV-10; none exists, RESEARCH.md Q1). It shares the v1
    ``SEND_MODE`` lock as its mode seam (read only through
    :func:`app.core.settings.get_settings`):

    - ``simulate`` (v1 lock) ⇒ a fresh :class:`SimulatedFundingSignalAdapter`
      (synthetic, in-memory, no I/O; INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no production signal source in v1;
      fail loud rather than silently read an external feed (INV-9, INV-10).

    Raises:
        NotImplementedError: when ``SEND_MODE=live`` (no production impl in v1).
    """
    mode = get_settings().send_mode
    if mode == "simulate":
        return SimulatedFundingSignalAdapter()
    raise NotImplementedError(
        "No production FundingSignalAdapter in v1: SEND_MODE='live' is reserved "
        "for a supplied GT-controlled signal source (ARCHITECTURE.md §7.2; "
        "INV-9/INV-10 fail-loud). v1 is locked to SEND_MODE='simulate'."
    )


def get_geo_sampling_adapter() -> GeoSamplingAdapter:
    """Return the GEO sampling adapter for the current mode (§7.6, FR-3.7, FR-4.4).

    The §7.6 boundary does **repeated, variance-reported** sampling of an AI
    engine's citations (CONTENT_SPEC §7.4). Live polling of real AI engines is
    OUT in v1 (PROJECT §7), so it shares the v1 ``SEND_MODE`` lock as its mode
    seam (read only through :func:`app.core.settings.get_settings`):

    - ``simulate`` (v1 lock) ⇒ a fresh :class:`SimulatedGeoSamplingAdapter`
      (synthetic, offline, no live engine; INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no production GEO sampling impl in v1;
      fail loud rather than silently poll a live AI engine (INV-9).

    Raises:
        NotImplementedError: when ``SEND_MODE=live`` (no production impl in v1).
    """
    mode = get_settings().send_mode
    if mode == "simulate":
        return SimulatedGeoSamplingAdapter()
    raise NotImplementedError(
        "No production GeoSamplingAdapter in v1: SEND_MODE='live' is reserved "
        "for a supplied live AI-engine sampling impl (ARCHITECTURE.md §7.6; "
        "INV-9 fail-loud). v1 is locked to SEND_MODE='simulate'."
    )


def get_media_gen_adapter() -> MediaGenAdapter:
    """Return the media-gen adapter for the current mode (§7.3, OUT-1, INV-9).

    The §7.3 boundary generates image/video assets. Live generation is OUT in v1
    (PROJECT §7, OUT-1: **$0 spend**), so it keys on its own dedicated mode seam
    ``MEDIA_GEN_MODE`` (read only through :func:`app.core.settings.get_settings`):

    - ``placeholder`` (v1 lock) ⇒ a fresh :class:`PlaceholderMediaGenAdapter`
      (synthetic stub refs, no live gen, $0 spend; INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no production media-gen impl in v1;
      fail loud rather than silently generate and overspend (INV-9, OUT-1).

    Raises:
        NotImplementedError: when ``MEDIA_GEN_MODE=live`` (no production impl in v1).
    """
    mode = get_settings().media_gen_mode
    if mode == "placeholder":
        return PlaceholderMediaGenAdapter()
    raise NotImplementedError(
        "No production MediaGenAdapter in v1: MEDIA_GEN_MODE='live' is reserved "
        "for a supplied production media-gen impl (ARCHITECTURE.md §7.3; "
        "INV-9 fail-loud, OUT-1 $0 spend). v1 is locked to MEDIA_GEN_MODE='placeholder'."
    )


def get_social_adapter() -> SocialAdapter:
    """Return the social-posting adapter for the current mode (§7.4, OUT-2, INV-9).

    The §7.4 boundary schedules/publishes posts. Live posting is OUT in v1
    (PROJECT §7, OUT-2), so it keys on its own dedicated mode seam
    ``SOCIAL_POST_MODE`` (read only through :func:`app.core.settings.get_settings`):

    - ``simulate`` (v1 lock) ⇒ a fresh :class:`SimulatedSocialAdapter`
      (backend-held queue, simulated receipts, no live send; INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no production social impl in v1; fail
      loud rather than silently send (INV-9, OUT-2).

    Raises:
        NotImplementedError: when ``SOCIAL_POST_MODE=live`` (no production impl in v1).
    """
    mode = get_settings().social_post_mode
    if mode == "simulate":
        return SimulatedSocialAdapter()
    raise NotImplementedError(
        "No production SocialAdapter in v1: SOCIAL_POST_MODE='live' is reserved "
        "for a supplied production social-posting impl (ARCHITECTURE.md §7.4; "
        "INV-9 fail-loud). v1 is locked to SOCIAL_POST_MODE='simulate'."
    )


def get_sentiment_adapter() -> SentimentAdapter:
    """Return the sentiment-feed adapter for the current mode (§7.5, OUT-5, INV-6/9).

    The §7.5 boundary returns an **aggregate-only** sentiment summary (no minor
    targeting, INV-6). A live feed is OUT in v1 (PROJECT §7, OUT-5); it has no
    dedicated mode var, so it shares the v1 ``SEND_MODE`` lock as its mode seam
    (read only through :func:`app.core.settings.get_settings`), as funding/geo do:

    - ``simulate`` (v1 lock) ⇒ a fresh :class:`PlaceholderSentimentAdapter`
      (aggregate over synthetic data, ``source_mode='placeholder'``, no live
      feed; INV-6, INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no production sentiment impl in v1;
      fail loud rather than silently poll a live feed (INV-9, INV-6).

    Raises:
        NotImplementedError: when ``SEND_MODE=live`` (no production impl in v1).
    """
    mode = get_settings().send_mode
    if mode == "simulate":
        return PlaceholderSentimentAdapter()
    raise NotImplementedError(
        "No production SentimentAdapter in v1: SEND_MODE='live' is reserved for a "
        "supplied production sentiment-feed impl (ARCHITECTURE.md §7.5; "
        "INV-9 fail-loud, INV-6 aggregate-only). v1 is locked to SEND_MODE='simulate'."
    )


def get_enrollment_system_adapter() -> EnrollmentSystemAdapter:
    """Return the SIS/enrollment-system adapter for the current ``SIS_MODE`` (INV-9).

    The agnostic SIS boundary (MULTI_AGENT_COCKPIT §4): the M5 reconcile core
    consumes :class:`~app.adapters.sis.base.RosterRecord` only and never knows
    which SIS is behind it. It keys on its own dedicated mode seam ``SIS_MODE``
    (read only through :func:`app.core.settings.get_settings`):

    - ``simulate`` (v1 default) ⇒ a ``SimulatedSISAdapter`` reading the synthetic
      roster — **M5, not built yet**. M0 ships only this seam, so this currently
      fails **loud** rather than silently returning nothing (INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no ``LiveSISAdapter`` per a real SIS in
      v1; fail loud rather than silently read an external roster (INV-9).

    Mirrors the :func:`get_funding_signal_adapter` / :func:`get_geo_sampling_adapter`
    fail-loud pattern.

    Raises:
        NotImplementedError: always in M0 — no concrete impl exists yet (M5).
    """
    mode = get_settings().sis_mode
    if mode == "simulate":
        raise NotImplementedError(
            "No SimulatedSISAdapter yet — M5. M0 ships only the EnrollmentSystemAdapter "
            "interface + RosterRecord shape + this SIS_MODE seam (MULTI_AGENT_COCKPIT §4, "
            "INV-9). The synthetic-roster-backed SimulatedSISAdapter is built in M5 "
            "(TODO.md M5); until then SIS_MODE='simulate' fails loud."
        )
    raise NotImplementedError(
        "No LiveSISAdapter in v1: SIS_MODE='live' is reserved for a supplied "
        "production SIS impl per a real Student Information System "
        "(MULTI_AGENT_COCKPIT §4; INV-9 fail-loud). v1 default is SIS_MODE='simulate'."
    )


def get_brand_memory_store() -> BrandMemoryStore:
    """Return the persistent brand-memory store (FR-3.2, D-8, A-11, INV-9).

    Brand memory MUST be server-side persistent, not browser localStorage (D-8).
    No Postgres in this env (A-3), so per A-11 the v1 local impl is the
    stdlib-``sqlite3``-backed :class:`SqliteBrandMemoryStore` (no new
    dependency). A kept item survives store re-instantiation against the same
    on-disk path. The production Postgres table (with deny-by-default RLS, INV-5)
    is authored in ``app/data/migrations/0002_brand_memory.sql``.

    The backing file path is a config seam: ``BRAND_MEMORY_DB_PATH`` if set, else
    the documented default (INV-11 — a seam, not a hardcoded magic in logic).
    """
    db_path = os.environ.get("BRAND_MEMORY_DB_PATH") or _DEFAULT_BRAND_MEMORY_DB_PATH
    return SqliteBrandMemoryStore(db_path)
