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
from typing import Literal

from pydantic import BaseModel, ConfigDict

SendMode = Literal["simulate", "live"]
MediaGenMode = Literal["placeholder", "live"]
SocialPostMode = Literal["simulate", "live"]


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

    # Browser origins allowed to call the API (CORS; §5.1). The React app runs on
    # a separate origin (Vite dev server / built host) so it must be allow-listed
    # explicitly — never `*`, which would let any site call the API. Defaults to
    # the local dev origins; prod sets GT_CORS_ALLOW_ORIGINS to the real host.
    cors_allow_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:3000")

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
            cors_allow_origins=_env_list(
                "GT_CORS_ALLOW_ORIGINS",
                ("http://localhost:5173", "http://localhost:3000"),
            ),
        )


def get_settings() -> Settings:
    """Return a freshly-read :class:`Settings` snapshot (the env seam).

    Re-reads `os.environ` each call so a test can `monkeypatch.setenv(...)` and
    observe the change; production reads it once at composition and caches the
    result in the dependency layer.
    """
    return Settings.from_env()
