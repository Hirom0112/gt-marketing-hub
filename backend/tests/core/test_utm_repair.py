"""UTM-repair deriver tests (Module 7 §7b; CLAUDE.md §4.1, INV-4/INV-11).

The EXPLICIT, audited repair companion to ``check_utm``: a deterministic,
lossless-or-aliased normalization (trim → lowercase medium → alias medium → re-derive
health). It NEVER invents a missing required key and NEVER aliases a medium into
something still disallowed. Rules + the alias table read from
``params.crm_ops.utm`` (INV-11).

Pure unit: no I/O, no adapters, no LLM — only the deriver + params.
"""

from __future__ import annotations

from pathlib import Path

from app.core.params import load_params
from app.core.utm_repair import UtmRepair, repair_utm

# The committed example file is the authoritative source for these tests (INV-11).
EXAMPLE_PARAMS = Path(__file__).resolve().parents[3] / "params" / "params.example.yaml"
_PARAMS = load_params(EXAMPLE_PARAMS)


def test_lowercase_medium_resolves() -> None:
    """An uppercase but otherwise-valid medium is lowercased ⇒ resolved + a fix logged."""
    utm = {
        "utm_source": "newsletter",
        "utm_medium": "EMAIL",
        "utm_campaign": "spring_open_house",
    }
    result = repair_utm(utm, params=_PARAMS)

    assert isinstance(result, UtmRepair)
    assert result.resolved is True
    assert result.repaired["utm_medium"] == "email"
    assert result.remaining_reasons == ()
    assert any("utm_medium" in f and "EMAIL" in f and "email" in f for f in result.fixes)
    # The input mapping is read, never mutated.
    assert utm["utm_medium"] == "EMAIL"


def test_alias_map_resolves_qr_code_to_event() -> None:
    """A known-bad medium spelling is mapped via medium_aliases onto an allowed canonical."""
    utm = {
        "utm_source": "flyer",
        "utm_medium": "QR_Code",  # → lowercased "qr_code" → aliased "event" (allowed)
        "utm_campaign": "fall_fair",
    }
    result = repair_utm(utm, params=_PARAMS)

    assert result.resolved is True
    assert result.repaired["utm_medium"] == "event"
    assert any("event" in f for f in result.fixes)


def test_trim_whitespace_resolves() -> None:
    """Leading/trailing whitespace on values is trimmed ⇒ resolved."""
    utm = {
        "utm_source": "  newsletter ",
        "utm_medium": " email ",
        "utm_campaign": "spring ",
    }
    result = repair_utm(utm, params=_PARAMS)

    assert result.resolved is True
    assert result.repaired == {
        "utm_source": "newsletter",
        "utm_medium": "email",
        "utm_campaign": "spring",
    }
    assert result.fixes  # at least one trim was applied


def test_missing_campaign_stays_unresolved() -> None:
    """A missing required key is NEVER fabricated ⇒ unresolved with a remaining reason."""
    utm = {"utm_source": "newsletter", "utm_medium": "EMAIL"}  # no utm_campaign
    result = repair_utm(utm, params=_PARAMS)

    assert result.resolved is False
    assert "utm_campaign" not in result.repaired  # never invented
    assert result.remaining_reasons  # the missing-key reason survives
    # The fixable part (the medium) was still normalized.
    assert result.repaired["utm_medium"] == "email"


def test_disallowed_medium_with_no_alias_stays_unresolved() -> None:
    """A medium with no lowercase/alias path into the allowed set stays broken."""
    utm = {
        "utm_source": "newsletter",
        "utm_medium": "telepathy",  # not allowed, not in medium_aliases
        "utm_campaign": "spring",
    }
    result = repair_utm(utm, params=_PARAMS)

    assert result.resolved is False
    assert result.repaired["utm_medium"] == "telepathy"  # unchanged (no alias)
    assert result.fixes == ()  # nothing applied
    assert result.remaining_reasons


def test_none_input_is_unresolved() -> None:
    """A None/absent UTM ⇒ unresolved, no fixes, no invented keys."""
    result = repair_utm(None, params=_PARAMS)
    assert result.resolved is False
    assert result.repaired == {}
    assert result.fixes == ()
