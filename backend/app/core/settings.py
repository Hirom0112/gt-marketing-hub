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

SendMode = Literal["simulate", "live"]
MediaGenMode = Literal["placeholder", "live"]
SocialPostMode = Literal["simulate", "live"]
# CRM_MODE is a *separate* seam from the v1 `send_mode` lock (D-9, OUT-3): the
# CRM/HubSpot boundary can go `live` independently — pushing SYNTHETIC data into
# a real portal behind the four guards (ANALYSIS/hubspot-complement-plan.md §3) —
# without unlocking the simulated send/social/media modes. v1 default stays
# `simulate`; `live` selects the production HubSpot adapter (S10 W2).
CrmMode = Literal["simulate", "live"]


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

        # A placeholder/sentinel token (the .env.example angle-bracket form or an
        # empty string) counts as "unset" — same posture as ANTHROPIC_API_KEY.
        hs_token = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
        if hs_token is not None and (hs_token.strip() == "" or hs_token.strip().startswith("<")):
            hs_token = None

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
