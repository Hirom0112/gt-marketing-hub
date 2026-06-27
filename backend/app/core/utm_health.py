"""UTM-health deriver (TODO_v2 §C1; CLAUDE.md INV-4/INV-11).

A pure deriver for the CRM-Ops data-quality layer: given a campaign's UTM
parameters it returns a frozen :class:`UtmHealth` verdict — ``ok`` or ``broken``
with the offending key(s) and a human reason. Per the honesty mandate (the brief;
mirroring INV-4) it **DETECTS and FLAGS, never auto-corrects**: a broken UTM
stays flagged red, never silently normalized or rewritten to pass. The input
mapping is read, never mutated.

A UTM is ``broken`` when ANY of these hold (the small, documented rule set, all
read from ``params.crm_ops.utm`` — INV-11):

1. a ``required_key`` is missing or blank (whitespace-only counts as blank);
2. ``utm_medium`` is present but NOT in ``allowed_mediums``;
3. a present required value is MALFORMED — it carries leading/trailing
   whitespace, or contains an uppercase letter (UTM values are conventionally
   lowercase). We FLAG the malformation; we never trim/lowercase it for the user.

A UTM is ``ok`` only when every required key is present, well-formed, and the
medium is allowed.

Pure: stdlib + ``app.core.params`` only — no I/O, no adapters, no LLM (the
core-purity test guards this).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.core.params import Params

UtmStatus = Literal["ok", "broken"]


@dataclass(frozen=True, slots=True)
class UtmHealth:
    """A UTM's health verdict (TODO_v2 §C1).

    Frozen/derived: an immutable detection artifact, never a state write.

    Attributes:
        status: ``ok`` when every rule passes, else ``broken``.
        offending_keys: The UTM key(s) that failed a rule (deduplicated, in
            first-seen order). Empty when ``ok``.
        reasons: One human-readable reason per detected problem (parallel to the
            problems found). Empty when ``ok``.
    """

    status: UtmStatus
    offending_keys: tuple[str, ...]
    reasons: tuple[str, ...]


def _malformation(key: str, value: str) -> str | None:
    """A human reason if ``value`` is malformed, else ``None`` (detect-only).

    The documented rule set: a value must carry no leading/trailing whitespace
    and must be lowercase. We report the malformation; we NEVER return a
    normalized value (the honesty mandate — flag, don't fix).
    """
    if value != value.strip():
        return f"{key!r} has leading/trailing whitespace: {value!r}"
    if value != value.lower():
        return f"{key!r} must be lowercase: {value!r}"
    return None


def check_utm(utm: Mapping[str, str] | None, *, params: Params) -> UtmHealth:
    """Derive the :class:`UtmHealth` verdict for one UTM mapping (TODO_v2 §C1).

    Rules read from ``params.crm_ops.utm`` (INV-11). The input is read, never
    mutated/normalized — a broken UTM is flagged, not fixed (honesty mandate,
    mirroring INV-4).

    Args:
        utm: The campaign UTM parameters, or ``None`` (treated as empty ⇒ every
            required key is missing).
        params: The loaded params; ``crm_ops.utm`` supplies the rule set.

    Returns:
        ``ok`` only when all required keys are present, well-formed, and the
        medium is allowed; otherwise ``broken`` with the offending key(s) +
        reason(s).
    """
    cfg = params.crm_ops.utm
    values: Mapping[str, str] = utm if utm is not None else {}

    offending: list[str] = []
    reasons: list[str] = []

    def _flag(key: str, reason: str) -> None:
        if key not in offending:
            offending.append(key)
        reasons.append(reason)

    # Rule 1 + 3: every required key present, non-blank, well-formed.
    for key in cfg.required_keys:
        value = values.get(key)
        if value is None or value.strip() == "":
            _flag(key, f"required key {key!r} is missing or blank")
            continue
        malformed = _malformation(key, value)
        if malformed is not None:
            _flag(key, malformed)

    # Rule 2: a present utm_medium must be in the allowed set. (A blank/missing
    # medium is already flagged above if it is a required key.)
    medium = values.get("utm_medium")
    if medium is not None and medium.strip() != "" and medium not in cfg.allowed_mediums:
        _flag("utm_medium", f"utm_medium {medium!r} not in allowed mediums {cfg.allowed_mediums!r}")

    status: UtmStatus = "broken" if offending else "ok"
    return UtmHealth(status=status, offending_keys=tuple(offending), reasons=tuple(reasons))
