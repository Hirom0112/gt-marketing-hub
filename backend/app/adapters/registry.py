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

from app.adapters.analytics.base import AnalyticsAdapter
from app.adapters.analytics.simulated import SimulatedAnalyticsAdapter
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
from app.adapters.open_data.base import OpenDataAdapter
from app.adapters.open_data.live import LiveOpenDataAdapter
from app.adapters.open_data.seeded import SeededOpenDataAdapter
from app.adapters.payments.base import PaymentsAdapter
from app.adapters.payments.live import LivePaymentsAdapter
from app.adapters.payments.simulated import SimulatedPaymentsAdapter
from app.adapters.sentiment.base import SentimentAdapter
from app.adapters.sentiment.placeholder import PlaceholderSentimentAdapter
from app.adapters.sheets.base import SheetsAdapter
from app.adapters.sheets.live import LiveSheetsAdapter
from app.adapters.sheets.simulated import SimulatedSheetsAdapter
from app.adapters.sis.base import EnrollmentSystemAdapter
from app.adapters.social.base import SocialAdapter
from app.adapters.social.simulated import SimulatedSocialAdapter
from app.core.params import AwardAmounts, Crm, Params, load_params
from app.core.settings import (
    CrmMode,
    OpenDataMode,
    Settings,
    SheetsMode,
    StripeMode,
    get_settings,
)

# Default on-disk home for the persistent brand-memory store when no override is
# supplied (ASSUMPTIONS A-11). The path is a config seam (env > default), not a
# hardcoded magic in logic (INV-11) — overridable via ``BRAND_MEMORY_DB_PATH``.
_DEFAULT_BRAND_MEMORY_DB_PATH = "data/brand_memory.db"

# HubSpot CRM v3 base URL — the fixed third-party API host (not a tunable).
_HUBSPOT_BASE_URL = "https://api.hubapi.com"

# Stripe API base URL — the fixed third-party API host (not a tunable; A3).
_STRIPE_BASE_URL = "https://api.stripe.com"

# tryopendata.ai API base URL — the fixed third-party API host (not a tunable; E1).
_OPEN_DATA_BASE_URL = "https://api.tryopendata.ai"

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
        resilience=_load_params().resilience,
    )


def effective_payments_mode(settings: Settings) -> StripeMode:
    """The payments mode the registry would ACTUALLY select for ``settings`` (pure).

    The canonical home for the A3/INV-8 precedence, mirroring
    :func:`effective_crm_mode` so a read-only status surface can REPORT the
    effective seam state without constructing a live Stripe adapter (or an httpx
    client). :func:`get_payments_adapter` consumes this; nothing here does I/O.

    - ``STRIPE_MODE=simulate`` ⇒ ``"simulate"`` (the v1 default recorder; INV-9).
    - ``STRIPE_MODE=live`` + webhook secret + **kill switch on** ⇒ ``"simulate"`` —
      INV-8 degrades to the recorder; never a live call when the kill switch is on.
    - ``STRIPE_MODE=live`` + webhook secret + no kill switch ⇒ ``"live"``.
    - ``STRIPE_MODE=live`` + **no webhook secret** ⇒ ``"live"`` — a live INTENT that
      is misconfigured; :func:`get_payments_adapter` fails loud (``RuntimeError``)
      rather than silently degrade (mirrors the CRM live-without-token precedent).
      Reported as ``"live"`` so the misconfig stays visible, not hidden behind a
      false ``"simulate"``.
    """
    if settings.stripe_mode == "simulate":
        return "simulate"
    # STRIPE_MODE == "live": the kill switch forces simulate even with a secret
    # (INV-8). A missing webhook secret is a live INTENT that fails loud at
    # construction (get_payments_adapter) — reported here as the "live" intent.
    if settings.stripe_webhook_secret is not None and settings.stripe_kill_switch:
        return "simulate"
    return "live"


def get_payments_adapter() -> PaymentsAdapter:
    """Return the payments adapter impl for the current ``STRIPE_MODE`` (A3; INV-8/9).

    The payments boundary keys on its own ``STRIPE_MODE`` seam, independent of the
    v1 ``SEND_MODE`` lock (mirroring ``CRM_MODE``):

    - ``simulate`` (default) ⇒ a fresh :class:`SimulatedPaymentsAdapter` (records,
      never sends; still verifies webhooks offline; INV-9).
    - ``live`` + webhook secret + **kill switch set** ⇒ degrade to
      :class:`SimulatedPaymentsAdapter` (INV-8) — never a live call when the kill
      switch is on.
    - ``live`` + webhook secret + no kill switch ⇒ :class:`LivePaymentsAdapter`
      (real Stripe behind the per-run cap).
    - ``live`` + **no webhook secret** ⇒ ``RuntimeError`` — fail loud on misconfig
      rather than silently degrading (the CRM live-without-token precedent).

    The cap is the env override ``stripe_calls_per_run_cap`` when set, else the
    canonical ``params.stripe.calls_per_run_cap`` (INV-11); the signature tolerance
    is always ``params.stripe.tolerance_seconds``. Both are injected — the adapters
    read no settings/params themselves.

    Raises:
        RuntimeError: when ``STRIPE_MODE=live`` but no webhook secret is configured.
    """
    settings = get_settings()
    stripe_params = _load_params().stripe

    # STRIPE_MODE == "live" + no webhook secret ⇒ fail loud on misconfig. The
    # construction-time RuntimeError lives here; effective_payments_mode reports
    # the live INTENT (mirrors get_crm_adapter's live-without-token guard).
    if settings.stripe_mode == "live" and settings.stripe_webhook_secret is None:
        raise RuntimeError(
            "STRIPE_MODE='live' requires STRIPE_WEBHOOK_SECRET — none is configured. "
            "Fail loud on misconfig rather than silently degrade to simulated "
            "(INV-8/INV-9). Set the secret or use STRIPE_MODE='simulate'."
        )

    # Single canonical precedence: simulate (default), or live degraded to simulate
    # by the kill switch (INV-8). The simulated adapter still verifies webhooks, so
    # it is handed the configured webhook secret + the params tolerance.
    if effective_payments_mode(settings) == "simulate":
        return SimulatedPaymentsAdapter(
            webhook_secret=settings.stripe_webhook_secret,
            tolerance_seconds=stripe_params.tolerance_seconds,
        )

    # A live result implies a webhook secret — the no-secret case raised above.
    webhook_secret = settings.stripe_webhook_secret
    if webhook_secret is None:  # pragma: no cover — unreachable past the fail-loud guard
        raise RuntimeError(
            "STRIPE_MODE='live' requires STRIPE_WEBHOOK_SECRET — none is configured."
        )
    cap = (
        settings.stripe_calls_per_run_cap
        if settings.stripe_calls_per_run_cap is not None
        else stripe_params.calls_per_run_cap
    )
    client = httpx.Client(base_url=_STRIPE_BASE_URL)
    return LivePaymentsAdapter(
        client=client,
        secret_key=settings.stripe_secret_key,
        webhook_secret=webhook_secret,
        calls_per_run_cap=cap,
        tolerance_seconds=stripe_params.tolerance_seconds,
        resilience=_load_params().resilience,
    )


def effective_open_data_mode(settings: Settings) -> OpenDataMode:
    """The Open Data mode the registry would ACTUALLY select for ``settings`` (pure).

    The single source of truth for the E1/INV-8 precedence, extracted so a
    read-only status surface can REPORT the effective seam state without
    constructing a live adapter (or an httpx client) and without forking the
    precedence (INV-11 — one canonical home). :func:`get_open_data_adapter`
    consumes this; nothing here reads the env or does I/O. Mirrors
    :func:`effective_crm_mode` / :func:`effective_payments_mode`.

    - ``OPEN_DATA_MODE=simulate`` ⇒ ``"simulate"`` (the v1 default seeded source; INV-9).
    - ``OPEN_DATA_MODE=live`` + key + **kill switch on** ⇒ ``"simulate"`` — INV-8
      degrades to the seeded source; never a live query when the kill switch is on.
    - ``OPEN_DATA_MODE=live`` + key + no kill switch ⇒ ``"live"``.
    - ``OPEN_DATA_MODE=live`` + **no key** ⇒ ``"live"`` — a live INTENT that is
      misconfigured; :func:`get_open_data_adapter` fails loud (``RuntimeError``)
      rather than silently degrade (INV-9). Reported as ``"live"`` so the misconfig
      stays visible, not hidden behind a false ``"simulate"``.
    """
    if settings.open_data_mode == "simulate":
        return "simulate"
    # OPEN_DATA_MODE == "live": the kill switch forces simulate even with a key
    # (INV-8). A missing key is a live INTENT that fails loud at construction
    # (get_open_data_adapter) — reported here as the "live" intent.
    if settings.open_data_api_key is not None and settings.open_data_kill_switch:
        return "simulate"
    return "live"


def get_open_data_adapter() -> OpenDataAdapter:
    """Return the Open Data adapter impl for the current ``OPEN_DATA_MODE`` (E1; INV-8/9).

    The tryopendata.ai Texas-education enrichment boundary keys on its own
    ``OPEN_DATA_MODE`` seam, independent of the v1 ``SEND_MODE`` lock (mirroring
    ``CRM_MODE`` / ``STRIPE_MODE``):

    - ``simulate`` (default) ⇒ a fresh :class:`SeededOpenDataAdapter` (synthetic,
      offline, no key; INV-9) — the v1 default.
    - ``live`` + key + **kill switch set** ⇒ degrade to :class:`SeededOpenDataAdapter`
      (INV-8) — never a live query when the kill switch is on.
    - ``live`` + key + no kill switch ⇒ :class:`LiveOpenDataAdapter` (real
      tryopendata.ai over httpx behind the per-run query cap).
    - ``live`` + **no key** ⇒ ``RuntimeError`` — fail loud on misconfig rather than
      silently degrading (the CRM/Stripe live-without-token precedent; INV-9).

    The cap is the env override ``open_data_per_run_query_cap`` when set, else the
    canonical ``params.open_data.per_run_query_cap`` (INV-11). Config (key,
    datasets, cap) is injected — the adapter reads no settings/params itself. A
    runtime cap-exhaustion (the (cap+1)th query) raises
    :class:`~app.adapters.open_data.base.OpenDataBudgetExceededError`, which the
    caller degrades to seeded on (INV-8) — the coarser kill-switch sibling is here.

    Raises:
        RuntimeError: when ``OPEN_DATA_MODE=live`` but no ``od_live_`` key is configured.
    """
    settings = get_settings()

    # OPEN_DATA_MODE == "live" + no key ⇒ fail loud on misconfig (INV-9).
    # effective_open_data_mode reports the live INTENT; the RuntimeError lives here.
    if settings.open_data_mode == "live" and settings.open_data_api_key is None:
        raise RuntimeError(
            "OPEN_DATA_MODE='live' requires OPEN_DATA_API_KEY (an od_live_ key) — none is "
            "configured. Fail loud on misconfig rather than silently degrade to seeded "
            "(INV-9). Set the key or use OPEN_DATA_MODE='simulate'."
        )

    # Single canonical precedence: simulate (default), or live degraded to simulate
    # by the kill switch (INV-8). A live result needs a key (guaranteed non-None
    # by the guard above).
    if effective_open_data_mode(settings) == "simulate":
        return SeededOpenDataAdapter()

    # A live result implies a key — the no-key case raised above. Bind to a local
    # so the type narrows from ``str | None`` to ``str``.
    api_key = settings.open_data_api_key
    if api_key is None:  # pragma: no cover — unreachable past the fail-loud guard
        raise RuntimeError("OPEN_DATA_MODE='live' requires OPEN_DATA_API_KEY — none is configured.")
    open_data_params = _load_params().open_data
    cap = (
        settings.open_data_per_run_query_cap
        if settings.open_data_per_run_query_cap is not None
        else open_data_params.per_run_query_cap
    )
    client = httpx.Client(base_url=_OPEN_DATA_BASE_URL)
    return LiveOpenDataAdapter(
        client=client,
        api_key=api_key,
        datasets=open_data_params.datasets,
        per_run_query_cap=cap,
    )


# Repo root, used to resolve a relative GSHEETS_SA_KEY_PATH (e.g. the default
# "backend/.secrets/gsheets-sa.json") regardless of the process cwd. Same anchor as
# _EXAMPLE_PARAMS: registry.py → parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_sheets_key_path(raw: str) -> Path:
    """Resolve the service-account key path (relative ⇒ repo-root-anchored)."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path


def effective_sheets_mode(settings: Settings) -> SheetsMode:
    """The Sheets mode the registry would ACTUALLY select for ``settings`` (pure).

    The canonical home for the S-Sheets/INV-8 precedence, mirroring
    :func:`effective_payments_mode` so a read-only status surface (the Content
    module's sync pill) can REPORT the effective seam state without building a live
    Sheets service or touching the network. :func:`get_sheets_adapter` consumes
    this; nothing here does I/O.

    - ``SHEETS_MODE=simulate`` ⇒ ``"simulate"`` (the v1 default in-memory rows; INV-9).
    - ``SHEETS_MODE=live`` + sheet id + **kill switch on** ⇒ ``"simulate"`` — INV-8
      degrades to the in-memory adapter; never a live call when the kill switch is on.
    - ``SHEETS_MODE=live`` + sheet id + no kill switch ⇒ ``"live"``.
    - ``SHEETS_MODE=live`` + **no sheet id** ⇒ ``"live"`` — a live INTENT that is
      misconfigured; :func:`get_sheets_adapter` fails loud (``RuntimeError``) rather
      than silently degrade (INV-9). Reported as ``"live"`` so the misconfig stays
      visible, not hidden behind a false ``"simulate"``.
    """
    if settings.sheets_mode == "simulate":
        return "simulate"
    # SHEETS_MODE == "live": the kill switch forces simulate even with a sheet id
    # (INV-8). A missing sheet id / unreadable key file is a live INTENT that fails
    # loud at construction (get_sheets_adapter) — reported here as the "live" intent.
    if settings.gsheets_sheet_id is not None and settings.sheets_kill_switch:
        return "simulate"
    return "live"


def get_sheets_adapter() -> SheetsAdapter:
    """Return the Sheets adapter impl for the current ``SHEETS_MODE`` (S-Sheets; INV-8/9).

    The Content-tracker boundary keys on its own ``SHEETS_MODE`` seam, independent of
    the v1 ``SEND_MODE`` lock (mirroring ``CRM_MODE`` / ``STRIPE_MODE`` /
    ``OPEN_DATA_MODE``):

    - ``simulate`` (default) ⇒ a seeded :class:`SimulatedSheetsAdapter` (in-memory
      deterministic rows, no network; INV-9).
    - ``live`` + sheet id + readable key file + **kill switch set** ⇒ degrade to the
      seeded :class:`SimulatedSheetsAdapter` (INV-8) — never a live call when the
      kill switch is on.
    - ``live`` + sheet id + readable key file + no kill switch ⇒ a
      :class:`LiveSheetsAdapter` over a Google Sheets v4 service built from the
      service-account key, behind the per-run call cap.
    - ``live`` + **no sheet id OR no readable key file** ⇒ ``RuntimeError`` — fail
      loud on misconfig rather than silently degrading (the CRM/Stripe precedent).

    The Google client libs are imported LAZILY here (only on the live path) so
    importing the registry never requires ``google-*`` to be installed, and the live
    adapter stays unit-testable with an injected fake service (INV-9).

    Raises:
        RuntimeError: when ``SHEETS_MODE=live`` but the sheet id is unset or the
            service-account key file is missing/unreadable.
    """
    settings = get_settings()

    # SHEETS_MODE == "live" + misconfig ⇒ fail loud (INV-9). effective_sheets_mode
    # reports the live INTENT; the construction-time RuntimeError lives here.
    if settings.sheets_mode == "live":
        key_path = _resolve_sheets_key_path(settings.gsheets_sa_key_path)
        if settings.gsheets_sheet_id is None or not key_path.is_file():
            raise RuntimeError(
                "SHEETS_MODE='live' requires GSHEETS_SHEET_ID and a readable "
                "GSHEETS_SA_KEY_PATH (the service-account JSON). One is missing — "
                "fail loud on misconfig rather than silently degrade to simulated "
                "(INV-9). Set both, or use SHEETS_MODE='simulate'."
            )

    # Single canonical precedence: simulate (default), or live degraded to simulate
    # by the kill switch (INV-8).
    if effective_sheets_mode(settings) == "simulate":
        return SimulatedSheetsAdapter.seeded()

    # A live result implies a sheet id + readable key (the misconfig case raised
    # above). Build the Sheets v4 service from the service-account key (lazy import).
    sheet_id = settings.gsheets_sheet_id
    if sheet_id is None:  # pragma: no cover — unreachable past the fail-loud guard
        raise RuntimeError("SHEETS_MODE='live' requires GSHEETS_SHEET_ID — none is configured.")
    from google.oauth2 import service_account  # noqa: PLC0415 — lazy: live path only
    from googleapiclient.discovery import build  # noqa: PLC0415 — lazy: live path only

    key_path = _resolve_sheets_key_path(settings.gsheets_sa_key_path)
    # google-auth ships partial types: present ⇒ from_service_account_file is an
    # untyped def (no-untyped-call); absent ⇒ the override Any-ifies it and this ignore
    # is unused. warn_unused_ignores is relaxed for this module so both envs pass.
    creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(key_path), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return LiveSheetsAdapter(
        service=service,
        spreadsheet_id=sheet_id,
        tab=settings.gsheets_tab,
        calls_per_run_cap=settings.gsheets_calls_per_run_cap,
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


def get_analytics_adapter() -> AnalyticsAdapter:
    """Return the website-analytics (GA4) adapter for the current mode (Module 13, INV-9).

    The Module-13 boundary returns an **aggregate-only** analytics snapshot (no
    per-person/child-keyed field, INV-6). No live GA4 credential is provisioned for this
    portal, so a live read is OUT in v1; it has no dedicated mode var, so it shares the v1
    ``SEND_MODE`` lock as its mode seam (read only through
    :func:`app.core.settings.get_settings`), as sentiment/funding/geo do:

    - ``simulate`` (v1 lock) ⇒ a fresh :class:`SimulatedAnalyticsAdapter` (aggregate over
      synthetic data, ``source_mode='simulated'``, no live read; INV-6, INV-9).
    - ``live`` ⇒ ``NotImplementedError`` — no GA4 Data-API impl in v1; fail loud rather
      than silently poll a property (INV-9, INV-6).

    Raises:
        NotImplementedError: when ``SEND_MODE=live`` (no production impl in v1).
    """
    mode = get_settings().send_mode
    if mode == "simulate":
        return SimulatedAnalyticsAdapter()
    raise NotImplementedError(
        "No production AnalyticsAdapter in v1: SEND_MODE='live' is reserved for a "
        "supplied GA4 Data-API impl (Module 13; INV-9 fail-loud, INV-6 aggregate-only). "
        "v1 is locked to SEND_MODE='simulate'."
    )


def get_enrollment_system_adapter() -> EnrollmentSystemAdapter:
    """Return the SIS/enrollment-system adapter for the current ``SIS_MODE`` (INV-9).

    The agnostic SIS boundary (MULTI_AGENT_COCKPIT §4): the M5 reconcile core
    consumes :class:`~app.adapters.sis.base.RosterRecord` only and never knows
    which SIS is behind it. It keys on its own dedicated mode seam ``SIS_MODE``
    (read only through :func:`app.core.settings.get_settings`):

    - ``simulate`` (v1 default) ⇒ a ``SimulatedSISAdapter`` reading the synthetic
      roster (M5) — built over the same cohort the cockpit serves, selected by
      ``COCKPIT_SCENARIO`` exactly as ``deps.py`` does (DH-3): the curated demo
      cohort under ``COCKPIT_SCENARIO=demo``, else the default June cohort.
    - ``live`` ⇒ ``NotImplementedError`` — no ``LiveSISAdapter`` per a real SIS in
      v1; fail loud rather than silently read an external roster (INV-9).

    Mirrors the :func:`get_funding_signal_adapter` / :func:`get_geo_sampling_adapter`
    mode-seam pattern.

    Raises:
        NotImplementedError: when ``SIS_MODE=live`` — no real-SIS impl in v1.
    """
    mode = get_settings().sis_mode
    if mode == "simulate":
        # M5: the synthetic-roster-backed SimulatedSISAdapter. The roster MUST be
        # built over the SAME cohort the cockpit serves so the reconcile buckets
        # line up with the families on screen — so the adapter is scenario-aware,
        # mirroring how ``deps.py`` selects the served cohort by ``COCKPIT_SCENARIO``
        # (DH-3). INV-1 synthetic.
        from app.adapters.sis.simulated import SimulatedSISAdapter

        params = _load_params()
        # Read the scenario EXACTLY as deps.py does, so the two never diverge.
        scenario = (os.environ.get("COCKPIT_SCENARIO", "") or "").strip().lower()
        if scenario == "demo":
            # DH-3: align the roster to the served curated demo cohort
            # (``generate_demo_cohort``; deps.py `scenario == "demo"` branch). The
            # ROSTER divergence seed is independent of the cohort's own seed — it
            # only drives which paid family lands in which bucket. Reuse the shared
            # back_to_school seed (the same seed the demo-cohort divergence test
            # uses in tests/data/test_demo_cohort.py) so all three buckets seed
            # deterministically (INV-11: a named params value, not an inline magic).
            from app.data.synthetic import generate_demo_cohort

            return SimulatedSISAdapter.from_cohort(
                generate_demo_cohort(params=params),
                seed=params.back_to_school.seed,
                params=params,
            )
        # Any other / unset scenario ⇒ the unchanged default-cohort path: the roster
        # over ``generate(DEFAULT_FAMILY_COUNT, DEFAULT_SEED)``.
        from app.data.repository import DEFAULT_FAMILY_COUNT, DEFAULT_SEED

        return SimulatedSISAdapter.from_seed(
            n=DEFAULT_FAMILY_COUNT, seed=DEFAULT_SEED, params=params
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
