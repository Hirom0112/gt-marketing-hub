"""UTM-health deriver tests (TODO_v2 §C1; CLAUDE.md §4.1, INV-4/INV-11).

The CRM-Ops UTM-health core DETECTS and FLAGS a malformed/broken UTM; it never
mutates or normalizes it (the honesty mandate — a broken UTM stays flagged red,
never silently fixed, mirroring INV-4). Rules read from
``params.crm_ops.utm`` (INV-11): a well-formed UTM is ``ok``; a missing/blank
required key, a non-allowed ``utm_medium``, or a malformed value is ``broken``,
with the offending key(s) + a human reason recorded.

Pure unit: no I/O, no adapters, no LLM — only the deriver + params.
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import load_params
from app.core.utm_health import UtmHealth, check_utm

# The committed example file is the authoritative source for these tests (INV-11).
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
_PARAMS = load_params(EXAMPLE_PARAMS)


def test_well_formed_utm_is_ok() -> None:
    """All required keys present + lowercase + an allowed medium ⇒ ``ok``."""
    utm = {
        "utm_source": "newsletter",
        "utm_medium": "email",
        "utm_campaign": "spring_open_house",
    }
    health = check_utm(utm, params=_PARAMS)

    assert isinstance(health, UtmHealth)
    assert health.status == "ok"
    assert health.offending_keys == ()
    assert health.reasons == ()


def test_missing_required_key_is_broken() -> None:
    """A missing required key ⇒ ``broken`` with that key offending (detect-only)."""
    # utm_campaign (a required key) is absent.
    utm = {"utm_source": "newsletter", "utm_medium": "email"}
    health = check_utm(utm, params=_PARAMS)

    assert health.status == "broken"
    assert "utm_campaign" in health.offending_keys
    assert health.reasons  # a human-readable reason is recorded.
    # The deriver NEVER mutates/normalizes the input UTM (honesty mandate, INV-4).
    assert "utm_campaign" not in utm


def test_disallowed_medium_is_broken() -> None:
    """A ``utm_medium`` outside the allowed set ⇒ ``broken`` + ``utm_medium``."""
    utm = {
        "utm_source": "newsletter",
        "utm_medium": "telepathy",  # not in params.crm_ops.utm.allowed_mediums
        "utm_campaign": "spring_open_house",
    }
    health = check_utm(utm, params=_PARAMS)

    assert health.status == "broken"
    assert "utm_medium" in health.offending_keys
    assert health.reasons


def test_rules_read_from_params() -> None:
    """The allowed mediums + required keys are the params values, never literals."""
    cfg = _PARAMS.crm_ops.utm
    assert "email" in cfg.allowed_mediums
    assert "telepathy" not in cfg.allowed_mediums
    assert "utm_campaign" in cfg.required_keys
