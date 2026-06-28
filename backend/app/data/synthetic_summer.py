"""Synthetic summer-camp registration sources (D2; INV-1 / INV-6 / INV-11).

The summer-camp module reconciles TWO overlapping registration sources —
``summer.gt.school`` (the primary site) and a standalone **registration form** —
without double-counting (see :mod:`app.core.summer_reconcile`). Neither real source
exists in v1, so this module STANDS THEM IN with deterministic, PII-safe synthetic
rows, deliberately shaped so dedup MATTERS: a third of registrants appear in BOTH
sources (and so MUST collapse to one), the rest in only one.

Synthetic-only mandate (INV-1 / INV-6 / NFR-1):

* every contact is synthetic — emails end ``@example.invalid`` and phones sit in the
  NANP fictitious ``555-01xx`` block (the same markers as :mod:`app.data.synthetic`);
* there is NO child PII — a registration carries only a household contact and an
  AGGREGATE grade band (``"K-2"`` …), never a child name / DOB / precise geo (INV-6).

Determinism (CLAUDE.md §4.1): all draws come from a single ``random.Random(seed)`` so
the same seed yields byte-identical rows. A "both-sources" registrant shares the SAME
email/phone/campus/paid across its two source rows (only the opaque ``external_id``
differs), so the reconciler matches them on the identity key and counts them once.

INV-11 (no magic numbers in code): the campus capacities and the per-campus target
fill live HERE as documented module constants — the seed source's one home. In a
live build they belong under ``params.summer_camp`` (reported in the handoff); a
SQL/seed module reading ``params/params.yaml`` is out of scope for v1, exactly as the
synthetic enrollment generator pins its own seed shape inline.
"""

from __future__ import annotations

import random

from app.core.summer_reconcile import CampRegistration

# --- INV-11 PARAM HOME (reported): would live as params.summer_camp.campus_capacity.
# Per-campus seat capacity; the four campuses roll up to TOTAL_CAPACITY (a fixed
# total). Mirrors the four campuses the cockpit's Camp module shows.
CAMPUS_CAPACITY: dict[str, int] = {
    "Austin": 100,  # Mueller campus, 2-week
    "Dallas": 100,  # Knox–Henderson campus, 2-week
    "Houston": 90,  # Heights campus, 2-week
    "San Antonio": 60,  # Pearl campus, 1-week
}
# The fixed total the per-campus capacities roll up to (350 seats).
TOTAL_CAPACITY: int = sum(CAMPUS_CAPACITY.values())

# --- INV-11 PARAM HOME (reported): would live as params.summer_camp.fill targets.
# The deterministic synthetic fill — DISTINCT registrants per campus (each strictly
# under capacity), and how many of them are paid (vs registered-but-unpaid leads).
_REGISTERED_TARGET: dict[str, int] = {
    "Austin": 86,
    "Dallas": 84,
    "Houston": 78,
    "San Antonio": 40,
}  # → 288 unique registrants total (the no-double-count target)
_PAID_TARGET: dict[str, int] = {
    "Austin": 66,
    "Dallas": 63,
    "Houston": 60,
    "San Antonio": 30,
}  # → 219 paid total

# Aggregate grade bands only — NEVER a child's actual grade/name/DOB (INV-1/INV-6).
_GRADE_BANDS: tuple[str, ...] = ("K-2", "3-5", "6-8")

# Source labels (the dedup provenance keys).
SOURCE_SITE = "summer_site"  # summer.gt.school
SOURCE_FORM = "registration_form"  # the standalone registration form

# Default deterministic seed (the camp launch date, as an int) — same seed ⇒ same rows.
DEFAULT_SEED = 20_260_601


def _email(slug: str, i: int) -> str:
    """A synthetic household email (INV-1 ``@example.invalid`` marker)."""
    return f"camp-{slug}-{i:03d}@example.invalid"


def _phone(i: int) -> str:
    """A synthetic phone in the NANP fictitious 555-01xx block (INV-1)."""
    return f"512-555-01{i % 100:02d}"


def generate_summer_sources(
    seed: int = DEFAULT_SEED,
) -> tuple[list[CampRegistration], list[CampRegistration]]:
    """Generate the two overlapping synthetic sources (site rows, form rows).

    Deterministic for a given ``seed``. For each campus we mint ``_REGISTERED_TARGET``
    DISTINCT registrants; ``_PAID_TARGET`` of them are paid. Each registrant is placed
    into the site, the form, or BOTH on a fixed rotation (``i % 3``) so roughly a third
    overlap and MUST be deduped. A both-sources registrant emits one site row and one
    form row that share email/phone/campus/grade/paid (only ``external_id`` differs).

    Returns:
        ``(site_rows, form_rows)`` — the two raw, un-deduped source lists. Their union
        contains every overlapping registrant TWICE; :func:`reconcile` collapses them.
    """
    rng = random.Random(seed)
    site: list[CampRegistration] = []
    form: list[CampRegistration] = []

    for campus, n in _REGISTERED_TARGET.items():
        paid_n = _PAID_TARGET[campus]
        slug = campus.lower().replace(" ", "")
        for i in range(n):
            band = _GRADE_BANDS[rng.randrange(len(_GRADE_BANDS))]
            email = _email(slug, i)
            phone = _phone(i)
            paid = i < paid_n  # the first paid_n registrants are paid

            site_row = CampRegistration(
                external_id=f"site-{slug}-{i:03d}",
                source=SOURCE_SITE,
                campus=campus,
                child_grade_band=band,
                synthetic_email=email,
                synthetic_phone=phone,
                paid=paid,
            )
            # The SAME registrant in the form source — only external_id/source differ,
            # so the reconciler matches them on the identity key and counts them once.
            form_row = CampRegistration(
                external_id=f"form-{slug}-{i:03d}",
                source=SOURCE_FORM,
                campus=campus,
                child_grade_band=band,
                synthetic_email=email,
                synthetic_phone=phone,
                paid=paid,
            )

            presence = i % 3  # 0 ⇒ both, 1 ⇒ site only, 2 ⇒ form only
            if presence == 0:
                site.append(site_row)
                form.append(form_row)
            elif presence == 1:
                site.append(site_row)
            else:
                form.append(form_row)

    return site, form


def generate_summer_dataset(
    seed: int = DEFAULT_SEED,
) -> tuple[list[CampRegistration], dict[str, int]]:
    """Convenience: the union of both source rows plus the per-campus capacities.

    Returns ``(rows, capacities)`` ready to hand straight to
    :func:`app.core.summer_reconcile.reconcile`.
    """
    site, form = generate_summer_sources(seed)
    return [*site, *form], dict(CAMPUS_CAPACITY)
