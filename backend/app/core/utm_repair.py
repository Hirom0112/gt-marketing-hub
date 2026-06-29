"""Deterministic UTM-repair core (Module 7 §7b) — explicit, lossless-or-aliased.

The honesty-mandate companion to :mod:`app.core.utm_health`. ``check_utm`` DETECTS
and FLAGS a broken UTM and NEVER mutates it — that mandate governs the silent READ
path. This module is the EXPLICIT, OWNER-TRIGGERED, AUDITED repair the spec's "Fix
log: UTM fixes applied, when, by whom" anticipates: given a broken UTM it returns a
:class:`UtmRepair` — the repaired mapping, the human-readable fixes actually
applied, whether the repair RESOLVED the UTM, and any reasons that remain. The
caller (the repair endpoint) writes the result back ONLY when ``resolved`` and logs
every fix to the audit trail; it never silently rewrites the read path.

The repair is deterministic and conservative — lossless normalization or an
explicit alias only, NEVER an invented value:

1. **trim** leading/trailing whitespace on every value;
2. **lowercase** ``utm_medium`` (UTM values are conventionally lowercase);
3. **alias** ``utm_medium`` via ``params.crm_ops.utm.medium_aliases`` (an
   ``{alias: canonical}`` table) — applied ONLY when the canonical result is in
   ``allowed_mediums`` (never a guess that leaves the medium still invalid);
4. **re-derive** :func:`app.core.utm_health.check_utm` over the repaired mapping —
   ``resolved`` is ``True`` only when the verdict is now ``ok``.

A MISSING required key (e.g. an absent ``utm_campaign``) is NEVER fabricated — it
stays unresolved. A ``utm_medium`` with no lowercase/alias path into the allowed set
stays unresolved. ``fixes`` lists only the changes ACTUALLY applied;
``remaining_reasons`` carries the ``check_utm`` reasons still present after repair.

Pure: stdlib + ``app.core.params`` + ``app.core.utm_health`` only — no I/O, no
adapters, no LLM (the core-purity test guards this).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.core.params import Params
from app.core.utm_health import check_utm

# The UTM key whose value is normalized (lowercased) + alias-mapped. The other
# required values are only TRIMMED (a conservative, lossless repair) — they are
# never lowercased/rewritten, mirroring the detect-only honesty of check_utm.
_MEDIUM_KEY = "utm_medium"


@dataclass(frozen=True, slots=True)
class UtmRepair:
    """The outcome of one deterministic UTM repair (Module 7 §7b).

    Frozen/derived: an immutable repair artifact. The endpoint persists
    :attr:`repaired` (and logs :attr:`fixes`) ONLY when :attr:`resolved`.

    Attributes:
        repaired: The repaired UTM mapping (trimmed values + a normalized/aliased
            ``utm_medium``). Carries the SAME keys as the input — never an invented
            required key.
        fixes: One human-readable line per change ACTUALLY applied (e.g.
            ``"utm_medium 'EMAIL' → 'email'"``). Empty when nothing changed.
        resolved: ``True`` only when :func:`check_utm` over :attr:`repaired` is
            ``ok`` (the repair fully fixed the UTM).
        remaining_reasons: The ``check_utm`` reasons still present after repair
            (empty when ``resolved``) — what a human must still fix by hand.
    """

    repaired: dict[str, str]
    fixes: tuple[str, ...]
    resolved: bool
    remaining_reasons: tuple[str, ...]


def repair_utm(utm: Mapping[str, str] | None, *, params: Params) -> UtmRepair:
    """Deterministically repair one UTM mapping (Module 7 §7b; lossless-or-aliased).

    Trims every value, lowercases + alias-maps ``utm_medium`` (the alias applied
    only when its canonical result is allowed), then re-derives :func:`check_utm`.
    A missing required key is NEVER fabricated, so a UTM missing (say)
    ``utm_campaign`` stays unresolved. The input is read, never mutated.

    Args:
        utm: The UTM parameters to repair, or ``None`` (treated as empty ⇒ every
            required key missing ⇒ unresolved, no fixes).
        params: The loaded params; ``crm_ops.utm`` supplies ``allowed_mediums`` and
            the ``medium_aliases`` table (INV-11).

    Returns:
        A :class:`UtmRepair` with the repaired mapping, the applied fixes, the
        resolved verdict, and any remaining reasons.
    """
    cfg = params.crm_ops.utm
    aliases = cfg.medium_aliases
    source: Mapping[str, str] = utm if utm is not None else {}

    repaired: dict[str, str] = {}
    fixes: list[str] = []
    for key, original in source.items():
        value = original.strip()  # (1) trim every value (lossless).
        if key == _MEDIUM_KEY:
            value = value.lower()  # (2) lowercase the medium.
            # (3) alias ONLY when the canonical maps into the allowed set — never a
            # guess that leaves the medium invalid.
            canonical = aliases.get(value)
            if canonical is not None and canonical in cfg.allowed_mediums:
                value = canonical
        repaired[key] = value
        if value != original:
            fixes.append(f"{key} {original!r} → {value!r}")

    # (4) re-derive the verdict over the repaired mapping (REUSED check_utm).
    health = check_utm(repaired, params=params)
    return UtmRepair(
        repaired=repaired,
        fixes=tuple(fixes),
        resolved=health.status == "ok",
        remaining_reasons=health.reasons,
    )
