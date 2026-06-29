"""Summer-camp dual-source registration reconciler (D2; INV-2 / INV-4 / INV-11).

Summer camp takes registrations from TWO sources that overlap:

* ``summer.gt.school`` — the primary registration site, and
* a standalone **registration form** (a separate intake).

The same family can land in BOTH. Counting the raw union would double-count every
overlapping registrant, inflating capacity-sold and revenue. This module is the
**deterministic core** that merges the two sources on a stable identity key so each
registrant is counted **exactly once**, then rolls the unique set up per campus
(registered vs capacity, paid vs lead) and surfaces a conflicts/duplicates list.

It follows the fail-closed dedup spine of :mod:`app.core.identity`: matching is a
purely structural exact-match on a normalized identity key (no threshold, no magic
number — INV-11), and an AMBIGUOUS match (same identity, but the two sources
disagree on which campus) is **held for human review, never silently merged into a
campus** (INV-4). A wrong merge would misstate a campus's capacity, so ambiguity
fails closed exactly as a false household merge does.

PURE (CLAUDE.md §3): no I/O, no LLM, no adapter imports, no clock. ``reconcile`` is
a pure function of (rows, per-campus capacities). The capacities are passed IN — the
core holds no campus constants (INV-11: those live in the synthetic source /
``params``). The synthetic registration sources live in
:mod:`app.data.synthetic_summer`; the HTTP surface is :mod:`app.api.summer`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core.program import Program

# The program this reconciler is isolated to (Phase-1 program isolation, A1). Its
# value IS the ``program_id`` stamped on every ``camp_registration`` row (0032) and
# the one canonical home for the token (INV-11: app/core/program.py owns the vocab).
PROGRAM_ID: str = Program.SUMMER_CAMP.value

_NON_PHONE_DIGITS = re.compile(r"\D+")


# ---------------------------------------------------------------------------
# Input — one registration row from one source (synthetic-named, INV-1). Carries
# ONLY aggregate, non-child identity: a household contact (synthetic email/phone)
# and an AGGREGATE grade band — never a child name / DOB / precise geo (INV-1/INV-6).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CampRegistration:
    """One camp registration as seen in one source (INV-1 synthetic; INV-6 aggregate).

    Attributes:
        external_id: The source's own opaque registration id (never child PII).
        source: Which source emitted it (e.g. ``"summer_site"`` /
            ``"registration_form"``) — for the dedup provenance summary.
        campus: The campus the registrant signed up for.
        child_grade_band: An AGGREGATE grade band (e.g. ``"K-2"``) — never a child's
            name, DOB, or precise data (INV-1/INV-6/COPPA-safe).
        synthetic_email: The household contact email (synthetic; INV-1) — the
            primary dedup key.
        synthetic_phone: The household contact phone (synthetic; INV-1) — the
            fallback dedup key when no email is present.
        paid: Whether the registration has been paid (vs a registered-but-unpaid
            lead).
        registration_channel: How the family signed up (word_of_mouth / social /
            email / website). ``None`` when the source did not record it (the dedup
            core ignores this field; it is carried for the channel breakdown).
        attended: Whether the child attended (camp is in the FUTURE in Phase 1, so
            every row is honestly ``False`` — the funnel surfaces it as pending).
        registered_at: When the registration arrived — for the recent-window
            ("registrations this week") count. ``None`` when not recorded.
    """

    external_id: str
    source: str
    campus: str
    child_grade_band: str
    synthetic_email: str | None
    synthetic_phone: str | None
    paid: bool
    registration_channel: str | None = None
    attended: bool = False
    registered_at: datetime | None = None


# ---------------------------------------------------------------------------
# Outputs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CampusRollup:
    """One campus's deduped rollup — registered/paid against capacity."""

    campus: str
    capacity: int
    registered: int
    paid: int
    lead: int  # registered-but-unpaid = registered - paid
    seats_remaining: int  # capacity - registered (never negative here)


@dataclass(frozen=True, slots=True)
class RegistrationConflict:
    """An AMBIGUOUS registrant — same identity, conflicting campus (fail-closed).

    The registrant matched across sources on the identity key but the sources
    disagree on the campus, so the reconciler cannot decide which campus to credit.
    Per INV-4 it is HELD OUT of every campus count and flagged here for a human —
    never silently merged into one campus (a false merge would misstate capacity).
    """

    dedup_key: str
    campuses: tuple[str, ...]
    external_ids: tuple[str, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class SourceCount:
    """Raw (pre-dedup) row count emitted by one source — the dedup provenance."""

    source: str
    rows: int


@dataclass(frozen=True, slots=True)
class SummerReconciliation:
    """The full deduped reconcile result for the summer-camp program."""

    program_id: str
    per_campus: tuple[CampusRollup, ...]
    total_capacity: int
    total_registered: int
    total_paid: int
    total_lead: int
    raw_source_rows: int  # site rows + form rows (the un-deduped union size)
    unique_registrations: int  # distinct registrants counted (the no-double-count #)
    duplicates_merged: int  # rows folded by dedup (raw appearances beyond the first)
    conflicts: tuple[RegistrationConflict, ...]
    sources: tuple[SourceCount, ...]


# ---------------------------------------------------------------------------
# Normalization — structural, deterministic, no tunables (mirrors identity.py).
# ---------------------------------------------------------------------------


def _norm_email(value: str | None) -> str | None:
    return value.strip().casefold() if value and value.strip() else None


def _norm_phone(value: str | None) -> str | None:
    digits = _NON_PHONE_DIGITS.sub("", value) if value else ""
    return digits or None


def _dedup_key(row: CampRegistration) -> str | None:
    """A stable household identity key for ``row`` (email, else phone, else None).

    Namespaced (``"email:"`` / ``"phone:"``) so an email can never collide with a
    phone value. A row with NEITHER contact cannot be matched — it returns ``None``
    and is treated as its own unique registrant (never false-merged with another).
    """
    email = _norm_email(row.synthetic_email)
    if email:
        return f"email:{email}"
    phone = _norm_phone(row.synthetic_phone)
    if phone:
        return f"phone:{phone}"
    return None


def reconcile(
    rows: Iterable[CampRegistration], capacities: Mapping[str, int]
) -> SummerReconciliation:
    """Merge the two sources' rows on the identity key, counting each registrant ONCE.

    Args:
        rows: The union of both sources' registration rows (order-independent).
        capacities: ``campus -> capacity`` — the campus universe + seat counts (the
            caller's one home for the numbers; the core holds none, INV-11). Rollup
            order follows this mapping's order.

    Returns:
        A :class:`SummerReconciliation`: per-campus registered/paid vs capacity, the
        totals, the dedup provenance (raw vs unique vs merged), and the fail-closed
        conflicts list.

    Dedup rules (deterministic, fail-closed — INV-2/INV-4):

    * rows sharing an identity key AND agreeing on campus ⇒ ONE unique registration
      (``paid`` is OR-ed: paid in EITHER source ⇒ paid). Every extra appearance is
      counted in ``duplicates_merged`` — the proof a double-count was prevented.
    * rows sharing an identity key but disagreeing on campus ⇒ an AMBIGUOUS
      :class:`RegistrationConflict`, HELD OUT of all campus counts (never merged).
    * a row with no identity key ⇒ its own unique registration (never false-merged).
    """
    rows = list(rows)

    keyed: dict[str, list[CampRegistration]] = {}
    unkeyed: list[CampRegistration] = []
    source_rows: dict[str, int] = {}
    for row in rows:
        source_rows[row.source] = source_rows.get(row.source, 0) + 1
        key = _dedup_key(row)
        if key is None:
            unkeyed.append(row)
        else:
            keyed.setdefault(key, []).append(row)

    # Resolve to a list of unique (campus, paid) registrants.
    resolved: list[tuple[str, bool]] = [(row.campus, row.paid) for row in unkeyed]
    duplicates_merged = 0
    conflicts: list[RegistrationConflict] = []

    for key, group in keyed.items():
        campuses = {g.campus for g in group}
        if len(campuses) == 1:
            # Clean match: same person, same campus across sources ⇒ count ONCE.
            resolved.append((next(iter(campuses)), any(g.paid for g in group)))
            duplicates_merged += len(group) - 1
        else:
            # Ambiguous: same identity, conflicting campus ⇒ fail closed (INV-4).
            ordered = tuple(sorted(campuses))
            conflicts.append(
                RegistrationConflict(
                    dedup_key=key,
                    campuses=ordered,
                    external_ids=tuple(sorted(g.external_id for g in group)),
                    summary=(
                        f"Registrant {key} appears across {len(ordered)} campuses "
                        f"({', '.join(ordered)}) — held for human review, not counted "
                        f"toward any campus (fail-closed)."
                    ),
                )
            )

    # Per-campus rollup over the capacity universe.
    reg_by: dict[str, int] = {}
    paid_by: dict[str, int] = {}
    for campus, paid in resolved:
        reg_by[campus] = reg_by.get(campus, 0) + 1
        if paid:
            paid_by[campus] = paid_by.get(campus, 0) + 1

    per_campus: list[CampusRollup] = []
    for campus, capacity in capacities.items():
        registered = reg_by.get(campus, 0)
        paid_count = paid_by.get(campus, 0)
        per_campus.append(
            CampusRollup(
                campus=campus,
                capacity=capacity,
                registered=registered,
                paid=paid_count,
                lead=registered - paid_count,
                seats_remaining=capacity - registered,
            )
        )

    total_registered = sum(c.registered for c in per_campus)
    total_paid = sum(c.paid for c in per_campus)

    return SummerReconciliation(
        program_id=PROGRAM_ID,
        per_campus=tuple(per_campus),
        total_capacity=sum(c.capacity for c in per_campus),
        total_registered=total_registered,
        total_paid=total_paid,
        total_lead=total_registered - total_paid,
        raw_source_rows=len(rows),
        unique_registrations=len(resolved),
        duplicates_merged=duplicates_merged,
        conflicts=tuple(conflicts),
        sources=tuple(SourceCount(source=s, rows=n) for s, n in sorted(source_rows.items())),
    )


# ---------------------------------------------------------------------------
# Phase-1 dimensions over the SAME deduped registrant set (all PURE / clock-free —
# any "now"/reference is INJECTED by the caller, never read here, INV-2). Each helper
# dedups on the SAME identity key as :func:`reconcile`, so a both-sources registrant
# is counted ONCE here too (no double-count leaks into the channel/funnel/recency).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelCount:
    """One signup-channel slice over the deduped registrant set."""

    channel: str
    count: int
    pct: float  # count / total_unique * 100, rounded to 1dp


@dataclass(frozen=True, slots=True)
class FunnelStage:
    """One funnel stage — its count, the drop from the previous stage, and pending."""

    stage: str
    count: int
    drop_off_pct: float  # (prev - count) / prev * 100, 0.0 for the first stage
    pending: bool  # True ⇒ the figure is not-yet-realized (e.g. attendance, camp future)


@dataclass(frozen=True, slots=True)
class CampusWaitlist:
    """One campus's overflow beyond capacity (registered − capacity, never negative)."""

    campus: str
    capacity: int
    registered: int
    waitlisted: int


def _unique_registrants(rows: Iterable[CampRegistration]) -> list[CampRegistration]:
    """Collapse ``rows`` to ONE representative per identity (the dedup spine reused).

    Rows sharing an identity key fold to a single representative (the first seen);
    an un-keyed row (no email/phone) is its own registrant — IDENTICAL grouping to
    :func:`reconcile`, so every Phase-1 dimension counts each registrant exactly once.
    A conflicting-campus group is NOT special-cased here (these dimensions are not
    campus-credited), so its representative still counts once.
    """
    seen: dict[str, CampRegistration] = {}
    unkeyed: list[CampRegistration] = []
    for row in rows:
        key = _dedup_key(row)
        if key is None:
            unkeyed.append(row)
        elif key not in seen:
            seen[key] = row
    return [*seen.values(), *unkeyed]


def channel_breakdown(rows: Iterable[CampRegistration]) -> tuple[ChannelCount, ...]:
    """The signup-channel breakdown over the DEDUPED registrant set, sorted desc.

    Each unique registrant contributes its ``registration_channel`` once (a missing
    channel is bucketed as ``"unknown"`` — honest, never dropped). Sorted by count
    desc then channel asc (stable, deterministic) so the first row is the top channel.
    """
    registrants = _unique_registrants(rows)
    total = len(registrants)
    counts: dict[str, int] = {}
    for r in registrants:
        channel = r.registration_channel or "unknown"
        counts[channel] = counts.get(channel, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(
        ChannelCount(channel=ch, count=n, pct=round(n / total * 100, 1) if total else 0.0)
        for ch, n in ordered
    )


def registration_funnel(result: SummerReconciliation, attended: int) -> tuple[FunnelStage, ...]:
    """The Lead → Registered → Paid → Attended funnel with per-step drop-off.

    The instrumented progression is Registered → Paid → Attended; counts come
    straight from the deduped reconcile result plus the injected ``attended`` count.
    ``drop_off_pct`` for a stage is the share lost from the PREVIOUS stage. Two stages
    are flagged ``pending`` (their drop-off is forced to 0.0, never a misleading
    number): "Lead" — pre-registration inquiries are NOT instrumented in the synthetic
    registration sources, so it is floored at ``total_registered`` (everyone who
    registered was at least a lead) rather than fabricated; and "Attended" — camp is in
    the future, so a 0 there is honestly "not yet", never a real drop to zero.
    """
    steps: tuple[tuple[str, int, bool], ...] = (
        ("Lead", result.total_registered, True),
        ("Registered", result.total_registered, False),
        ("Paid", result.total_paid, False),
        ("Attended", attended, True),
    )
    stages: list[FunnelStage] = []
    prev: int | None = None
    for stage, count, pending in steps:
        if pending or prev is None or prev == 0:
            drop = 0.0
        else:
            drop = round((prev - count) / prev * 100, 1)
        stages.append(FunnelStage(stage=stage, count=count, drop_off_pct=drop, pending=pending))
        prev = count
    return tuple(stages)


def registrations_in_window(rows: Iterable[CampRegistration], *, now: datetime, days: int) -> int:
    """Count DEDUPED registrants whose ``registered_at`` is within the last ``days``.

    ``now`` is INJECTED (the helper never reads a clock — INV-2). The window is the
    half-open ``(now - days, now]``: a registration exactly ``days`` ago is just
    outside it. A registrant with no ``registered_at`` is not counted (honest).
    """
    if days <= 0:
        return 0
    cutoff = now - timedelta(days=days)
    count = 0
    for r in _unique_registrants(rows):
        ts = r.registered_at
        if ts is not None and cutoff < ts <= now:
            count += 1
    return count


def waitlist_by_campus(result: SummerReconciliation) -> tuple[CampusWaitlist, ...]:
    """Per-campus overflow beyond capacity (registered − capacity, clamped at 0).

    Computed from the deduped per-campus rollup, so it is honest: with the seeded
    fill (every campus under capacity) every ``waitlisted`` is 0; an over-subscribed
    campus surfaces a real overflow.
    """
    return tuple(
        CampusWaitlist(
            campus=c.campus,
            capacity=c.capacity,
            registered=c.registered,
            waitlisted=max(0, c.registered - c.capacity),
        )
        for c in result.per_campus
    )


def days_until(target: date, *, now: date) -> int:
    """Whole days from ``now`` to ``target`` (negative once ``target`` has passed).

    ``now`` is INJECTED (clock-free — INV-2). Used for the camp-start countdown
    (earliest session ``starts_on`` minus the injected today).
    """
    return (target - now).days
