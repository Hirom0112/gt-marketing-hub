"""Pure sync-parity aggregator (TODO_v2 §A4; ARCHITECTURE.md §4.7; INV-2/A-7).

Aggregates the per-family seam status (computed by
:func:`app.core.seam.derive_seam_status`) across a cohort into:

- **overall** — the fraction of rows whose seam status is ``SYNCED`` (rows fully
  in agreement with the CRM mirror).
- **by_field** — for each tracked field (``stage`` / ``funding_state`` /
  ``owner``) the fraction of rows whose DB value equals the mirror value.

This is the deterministic, *pure* core (A-7): a function of the cohort + mirrors
alone — no repository, adapter, or httpx import (the core-purity test guards
this). The API layer reads the active-program cohort and its simulated HubSpot
mirrors and feeds the (record, mirror) pairs in; the UI formats the raw floats
(this returns full precision, callers round for display).

The tracked-field set and its per-field DB/mirror accessors are single-sourced
from the seam's :data:`app.core.seam._TRACKED_FIELDS` so parity always measures
exactly the fields the seam reconciles (INV-11 — one canonical home for the
structural field policy); the per-field equality here is a plain value
comparison (``None``-vs-``None`` agrees; ``None``-vs-value disagrees), which is
how Python's ``==`` already behaves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.core.seam import _TRACKED_FIELDS, MirrorState, derive_seam_status
from app.data.models import FamilyRecord, SeamStatus


@dataclass(frozen=True, slots=True)
class ParityScore:
    """A cohort's sync-parity, as raw fractions in [0, 1] (A4).

    Frozen so a computed score can't mutate after the fact. Returns full-precision
    floats; the API/UI layer formats them (the worked-number test asserts to 4 dp).

    Attributes:
        overall: Rows whose seam status is ``SYNCED`` / total rows.
        by_field: Per-tracked-field DB-vs-mirror agreement fraction, keyed by the
            seam's field name (``stage`` / ``funding_state`` / ``owner``).
    """

    overall: float
    by_field: dict[str, float]


def compute_parity(pairs: Iterable[tuple[FamilyRecord, MirrorState]]) -> ParityScore:
    """Aggregate per-family seam status over a cohort into a :class:`ParityScore`.

    Args:
        pairs: The cohort as ``(FamilyRecord, MirrorState)`` rows — the DB record
            and its simulated HubSpot mirror. Consumed once (materialized
            internally), so a one-shot iterator is fine.

    Returns:
        The cohort's :class:`ParityScore`. An empty cohort reports full parity
        (overall and every per-field fraction = 1.0) — the documented boring
        choice: "no rows ⇒ nothing is out of sync".
    """
    cohort = list(pairs)
    total = len(cohort)
    if total == 0:
        return ParityScore(overall=1.0, by_field={field.name: 1.0 for field in _TRACKED_FIELDS})

    synced = sum(
        1 for record, mirror in cohort if derive_seam_status(record, mirror) is SeamStatus.SYNCED
    )
    by_field = {
        field.name: sum(
            1
            for record, mirror in cohort
            if field.local_value(record) == field.mirror_value(mirror)
        )
        / total
        for field in _TRACKED_FIELDS
    }
    return ParityScore(overall=synced / total, by_field=by_field)
