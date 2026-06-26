"""Typed runtime settings — the env-var seam (TECH_STACK.md §5; INV-11).

Every environment variable this product reads has exactly one canonical home:
the registry in `TECH_STACK.md §5`. This module is the *code* mirror of that
registry — a typed, frozen reader with the §5 defaults baked in — so nothing in
the codebase reaches into `os.environ` directly (grep stays clean) and a missing
optional var degrades to its documented default rather than crashing.

Secrets (`ANTHROPIC_API_KEY`) default to ``None`` — absence is a first-class
state: the LLM edge degrades to deterministic/placeholder mode (kill-switch
posture, NFR-5), it does not raise. The v1 send/gen modes are *locked* to their
simulated/placeholder values (D-9, OUT-1/2/3); `live` is reserved for prod.

This is a shared contract (CLAUDE.md §7): changed here, consumed everywhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.program import Program

SendMode = Literal["simulate", "live"]
MediaGenMode = Literal["placeholder", "live"]
SocialPostMode = Literal["simulate", "live"]
# CRM_MODE is a *separate* seam from the v1 `send_mode` lock (D-9, OUT-3): the
# CRM/HubSpot boundary can go `live` independently — pushing SYNTHETIC data into
# a real portal behind the four guards (ANALYSIS/hubspot-complement-plan.md §3) —
# without unlocking the simulated send/social/media modes. v1 default stays
# `simulate`; `live` selects the production HubSpot adapter (S10 W2).
CrmMode = Literal["simulate", "live"]
# SIS_MODE is the agnostic enrollment-system (SIS) boundary seam (MULTI_AGENT_COCKPIT
# §4, INV-9): it selects the EnrollmentSystemAdapter impl the M5 reconcile core reads.
# v1 default `simulate` (reads the synthetic roster); `live` is reserved for a real
# SIS. Both impls are M5 — M0 ships only the seam, so the registry selector currently
# fails loud on either value (no impl yet). Separate from the `send_mode` lock.
SisMode = Literal["simulate", "live"]
# STRIPE_MODE is the payments boundary seam (A3, INV-9): it selects the
# PaymentsAdapter impl the fulfillment core reads. v1 default `simulate` (no live
# Stripe call); `live` selects the production Stripe adapter behind the INV-8 cap
# + kill switch. Separate from the `send_mode` lock — the payments seam keys on
# its own var, mirroring CRM_MODE.
StripeMode = Literal["simulate", "live"]
# COCKPIT_REPO is the explicit data-source override (TECH_STACK §5.1). It chooses
# the FamilyRepository the cockpit reads — it does NOT change either repo's
# behavior (doctrine-neutral). `auto` keeps the A-24 M5 default (SUPABASE_URL set
# ⇒ live, else synthetic); `synthetic` forces the in-memory cohort even with
# SUPABASE_URL set; `supabase` REQUIRES the live repo (fail loud on misconfig).
CockpitRepo = Literal["auto", "synthetic", "supabase"]


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var ('true'/'false', case-insensitive) with a default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple (empty/absent ⇒ default)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_int(name: str, default: int) -> int:
    """Parse an integer env var with a default (empty/absent ⇒ default)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """Parse a float env var with a default (empty/absent ⇒ default)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


class Settings(BaseModel):
    """Frozen snapshot of the §5 env registry the running process sees.

    Built via :meth:`from_env`. Defaults mirror `TECH_STACK.md §5` exactly so the
    code and the doc never drift (INV-11). Read it through :func:`get_settings`.
    """

    model_config = ConfigDict(frozen=True)

    # Anthropic — the only metered external API in v1 (§5).
    anthropic_api_key: str | None = None
    anthropic_model_primary: str = "claude-opus-4-8"
    anthropic_model_fast: str = "claude-sonnet-4-6"
    anthropic_model_cheap: str = "claude-haiku-4-5-20251001"
    anthropic_max_tokens: int = 16000

    # Spend / token governors (NFR-5; §6.2).
    llm_run_token_cap: int = 200000
    llm_kill_switch: bool = False
    cost_daily_usd_cap: float = 25.00

    # Outbound-mode locks — v1 is simulated/placeholder (D-9, OUT-1/2/3).
    send_mode: SendMode = "simulate"
    media_gen_mode: MediaGenMode = "placeholder"
    social_post_mode: SocialPostMode = "simulate"

    # CRM/HubSpot seam — flips to `live` independently of the send-mode lock so
    # the cockpit can push SYNTHETIC data into the real portal behind the four
    # guards (S10; ANALYSIS/hubspot-complement-plan.md §3). Default `simulate`.
    crm_mode: CrmMode = "simulate"

    # SIS/enrollment-system seam (§5; MULTI_AGENT_COCKPIT §4, INV-9). Selects the
    # EnrollmentSystemAdapter impl the M5 reconcile core reads. Default `simulate`
    # (synthetic roster); `live` is reserved for a real SIS. Both impls are M5 —
    # M0 wires only the seam, so the registry selector fails loud on either value.
    sis_mode: SisMode = "simulate"

    # Data-source override (§5.1). Selects the FamilyRepository the cockpit reads,
    # overriding the A-24 M5 "SUPABASE_URL ⇒ supabase" single-source default so the
    # operator can source the full `.env` (HubSpot token / Anthropic key / gallery
    # path) yet still force the rich synthetic cohort. `auto` = unchanged default;
    # `synthetic` = force the in-memory cohort even with SUPABASE_URL set;
    # `supabase` = require the live repo (fail loud when unbound). Doctrine-neutral.
    cockpit_repo: CockpitRepo = "auto"

    # A1 active-program selector (§5.1). The single hardened database is multi-program
    # (`fall_enrollment`, `summer_camp`, …); this is the raw `program_id` token THIS
    # deployment serves — the app stamps/filters every program-scoped row on it as
    # the app-layer defense-in-depth (the service_role read path bypasses RLS, so
    # isolation is enforced in code; A1 / PLAN_v2 §A1). It is DEPLOYMENT config, never
    # a client header (A-37). The raw string is validated fail-closed at the deps layer
    # via `app.core.program.resolve_program` (an unknown token raises). Default mirrors
    # the migration backfill (`Program.FALL_ENROLLMENT`); no magic literal (INV-11).
    gt_program_id: str = Program.FALL_ENROLLMENT.value

    # Supabase JWT signing secret (§5.2). The HS256 shared secret Supabase signs
    # its end-user JWTs with; the API verifies the ``Authorization: Bearer`` token
    # against it (``app.core.jwt_verify.verify_hs256``) to derive the VERIFIED
    # principal from ``app_metadata.role`` — the replacement for the spoofable
    # ``X-Demo-Role`` header (S1). Defaults to ``None`` — absence is first-class and
    # fails closed: with no secret, JWT auth cannot validate any token (401), it
    # NEVER default-allows. Server-side only — never shipped to the client.
    supabase_jwt_secret: str | None = None

    # HubSpot live-adapter config (§5.4). The token defaults to ``None`` —
    # absence is a first-class state: with no token the CRM edge can only run
    # `simulate` (the adapter agent's registry fails loud on `live` w/o a token).
    hubspot_private_app_token: str | None = None
    # INV-8: a hard per-run ceiling on HubSpot API calls. The account-shared
    # quota means overuse DoSes GT's real automation, so a breach degrades to the
    # SimulatedCRMAdapter (S10 W2 guard 3) — never a silent overspend.
    hubspot_calls_per_run_cap: int = 200
    # INV-8 kill switch: when ``True``, all live HubSpot writes are disabled and
    # the CRM edge degrades to the simulated adapter regardless of `crm_mode`.
    hubspot_kill_switch: bool = False

    # Stripe payments seam (§5.4; A3, INV-8/INV-9). `stripe_mode` flips to `live`
    # independently of the send-mode lock so the cockpit can take real (test-key)
    # payments behind the cap + kill switch. The secret key + webhook secret
    # default to ``None`` — absence is first-class: with no key the payments edge
    # can only run `simulate`. `stripe_calls_per_run_cap` is an OPTIONAL env
    # override of the params cap (mirrors HubSpot's cap posture); ``None`` ⇒ use
    # the params value. `stripe_kill_switch` disables all live Stripe calls (INV-8).
    stripe_mode: StripeMode = "simulate"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_kill_switch: bool = False
    stripe_calls_per_run_cap: int | None = None

    # Browser origins allowed to call the API (CORS; §5.1). The React app runs on
    # a separate origin (Vite dev server / built host) so it must be allow-listed
    # explicitly — never `*`, which would let any site call the API. Defaults to
    # the local dev origins; prod sets GT_CORS_ALLOW_ORIGINS to the real host.
    cors_allow_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:3000")

    # Filesystem ROOT of GT's scraped POSTED catalog (the real public-marketing posts),
    # read AT RUNTIME by the posted gallery (catalog at ``<root>/catalog/catalog.csv``,
    # media under ``<root>/<media_file>``). This is the **scoped INV-1 exception**
    # (ASSUMPTIONS): GT's own public marketing surfaced in GT's own internal cockpit —
    # NOTHING real is ever committed; the path is machine-local (an env var, not a param).
    # DISTINCT from ``GT_LIBRARY_PATH`` (TECH_STACK §5.1), which is DISTILL-ONLY (read
    # offline by ``scripts/distill_library.py``, NEVER at runtime); this one IS the
    # runtime catalog read. Empty / unset / a ``<…>`` sentinel ⇒ ``None`` ⇒ the gallery
    # falls back to the synthetic library gallery (and the static media mount is skipped).
    posted_catalog_root: Path | None = None

    @property
    def llm_available(self) -> bool:
        """True only when a live LLM call is permitted: a key is set AND no kill switch.

        The single predicate the AI edge consults before any live call — when
        False the caller degrades to the deterministic/template path (never a
        silent skip; NFR-5 fail-closed posture).
        """
        return self.anthropic_api_key is not None and not self.llm_kill_switch

    @classmethod
    def from_env(cls) -> Settings:
        """Read the §5 registry from the process environment, applying defaults."""
        key = os.environ.get("ANTHROPIC_API_KEY")
        # A placeholder value (the .env.example sentinel) counts as "unset".
        if key is not None and (key.strip() == "" or key.strip().startswith("<")):
            key = None

        send_mode = os.environ.get("SEND_MODE", "simulate").strip() or "simulate"
        media_mode = os.environ.get("MEDIA_GEN_MODE", "placeholder").strip() or "placeholder"
        social_mode = os.environ.get("SOCIAL_POST_MODE", "simulate").strip() or "simulate"
        crm_mode = os.environ.get("CRM_MODE", "simulate").strip() or "simulate"
        sis_mode = os.environ.get("SIS_MODE", "simulate").strip() or "simulate"
        stripe_mode = os.environ.get("STRIPE_MODE", "simulate").strip() or "simulate"

        # COCKPIT_REPO: lower-cased; empty / unset / a `<…>` sentinel ⇒ `auto` (the
        # current behavior). Any other value flows through to pydantic, which
        # validates it against the CockpitRepo literal (a typo fails loud at boot).
        cockpit_repo = (os.environ.get("COCKPIT_REPO", "") or "").strip().lower()
        if not cockpit_repo or cockpit_repo.startswith("<"):
            cockpit_repo = "auto"

        # GT_PROGRAM_ID: the active program token; empty / unset / a `<…>` sentinel ⇒
        # the migration-backfill default (`fall_enrollment`). Kept as the RAW string —
        # `resolve_program` validates it fail-closed at the deps layer (a typo there
        # raises, never silently defaulting to a program; A1 fail-closed posture).
        gt_program_id = (os.environ.get("GT_PROGRAM_ID", "") or "").strip()
        if not gt_program_id or gt_program_id.startswith("<"):
            gt_program_id = Program.FALL_ENROLLMENT.value

        # SUPABASE_JWT_SECRET: the HS256 verifying secret; a placeholder/sentinel
        # (the .env.example angle-bracket form or an empty string) counts as "unset"
        # ⇒ None ⇒ JWT auth fails closed (401). Same posture as ANTHROPIC_API_KEY.
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET")
        if jwt_secret is not None and (
            jwt_secret.strip() == "" or jwt_secret.strip().startswith("<")
        ):
            jwt_secret = None

        # A placeholder/sentinel token (the .env.example angle-bracket form or an
        # empty string) counts as "unset" — same posture as ANTHROPIC_API_KEY.
        hs_token = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
        if hs_token is not None and (hs_token.strip() == "" or hs_token.strip().startswith("<")):
            hs_token = None

        # Stripe secret key + webhook secret: a placeholder/sentinel (the
        # .env.example angle-bracket form or an empty string) counts as "unset" —
        # same first-class-absence posture as ANTHROPIC_API_KEY / the HubSpot token.
        stripe_key = os.environ.get("STRIPE_SECRET_KEY")
        if stripe_key is not None and (
            stripe_key.strip() == "" or stripe_key.strip().startswith("<")
        ):
            stripe_key = None
        stripe_webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        if stripe_webhook_secret is not None and (
            stripe_webhook_secret.strip() == "" or stripe_webhook_secret.strip().startswith("<")
        ):
            stripe_webhook_secret = None

        # STRIPE_CALLS_PER_RUN_CAP: an OPTIONAL env override of the params cap;
        # empty / unset ⇒ None ⇒ the params value is used (mirrors HubSpot's cap
        # posture but defaults to deferring to params rather than a hardcoded int).
        stripe_cap_raw = os.environ.get("STRIPE_CALLS_PER_RUN_CAP")
        stripe_calls_per_run_cap = (
            int(stripe_cap_raw)
            if stripe_cap_raw is not None and stripe_cap_raw.strip() != ""
            else None
        )

        # The posted-catalog root: empty / unset / a `<…>` sentinel ⇒ None (fall back to
        # the library gallery + skip the static media mount). Same posture as the secrets.
        catalog_raw = os.environ.get("GT_POSTED_CATALOG_ROOT")
        catalog_root: Path | None = None
        if (
            catalog_raw is not None
            and catalog_raw.strip()
            and not catalog_raw.strip().startswith("<")
        ):
            catalog_root = Path(catalog_raw.strip()).expanduser()

        return cls(
            anthropic_api_key=key,
            anthropic_model_primary=os.environ.get("ANTHROPIC_MODEL_PRIMARY", "claude-opus-4-8"),
            anthropic_model_fast=os.environ.get("ANTHROPIC_MODEL_FAST", "claude-sonnet-4-6"),
            anthropic_model_cheap=os.environ.get(
                "ANTHROPIC_MODEL_CHEAP", "claude-haiku-4-5-20251001"
            ),
            anthropic_max_tokens=_env_int("ANTHROPIC_MAX_TOKENS", 16000),
            llm_run_token_cap=_env_int("LLM_RUN_TOKEN_CAP", 200000),
            llm_kill_switch=_env_bool("LLM_KILL_SWITCH", False),
            cost_daily_usd_cap=_env_float("COST_DAILY_USD_CAP", 25.00),
            send_mode=send_mode,  # type: ignore[arg-type]
            media_gen_mode=media_mode,  # type: ignore[arg-type]
            social_post_mode=social_mode,  # type: ignore[arg-type]
            crm_mode=crm_mode,  # type: ignore[arg-type]
            sis_mode=sis_mode,  # type: ignore[arg-type]
            stripe_mode=stripe_mode,  # type: ignore[arg-type]
            stripe_secret_key=stripe_key,
            stripe_webhook_secret=stripe_webhook_secret,
            stripe_kill_switch=_env_bool("STRIPE_KILL_SWITCH", False),
            stripe_calls_per_run_cap=stripe_calls_per_run_cap,
            cockpit_repo=cockpit_repo,  # type: ignore[arg-type]
            gt_program_id=gt_program_id,
            supabase_jwt_secret=jwt_secret,
            hubspot_private_app_token=hs_token,
            hubspot_calls_per_run_cap=_env_int("HUBSPOT_CALLS_PER_RUN_CAP", 200),
            hubspot_kill_switch=_env_bool("HUBSPOT_KILL_SWITCH", False),
            cors_allow_origins=_env_list(
                "GT_CORS_ALLOW_ORIGINS",
                ("http://localhost:5173", "http://localhost:3000"),
            ),
            posted_catalog_root=catalog_root,
        )


def get_settings() -> Settings:
    """Return a freshly-read :class:`Settings` snapshot (the env seam).

    Re-reads `os.environ` each call so a test can `monkeypatch.setenv(...)` and
    observe the change; production reads it once at composition and caches the
    result in the dependency layer.
    """
    return Settings.from_env()


def posted_catalog_mount_root(settings: Settings) -> Path | None:
    """The directory to mount the posted media from, or ``None`` to skip the mount.

    The static ``/posted-media`` route (``app.main``) is wired ONLY when
    ``GT_POSTED_CATALOG_ROOT`` is set AND the directory actually exists — otherwise the
    mount is skipped and the gallery degrades to the synthetic library placeholders
    (graceful, never a boot failure on a missing path). A pure decision (existence check
    only) so the wiring can be tested without real files.
    """
    root = settings.posted_catalog_root
    if root is not None and root.is_dir():
        return root
    return None
