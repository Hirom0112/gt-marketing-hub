"""Seeded OpenDataAdapter — synthetic, in-memory, no I/O (E1; INV-1/INV-9).

The v1 default impl of the ``OpenDataAdapter`` boundary (INV-9): it returns typed
:class:`DistrictEnrichment` rows from an in-memory fixture, with **no httpx client
at all** — so "aggregate, offline, not a live API" (INV-1/INV-6/INV-9) is a
structural property, provable from the source text alone (no live transport to
mock). It is the degrade target the registry falls back to when the live edge is
killed / cap-exhausted (INV-8).

The fixture carries at least one A-rated and one D/F-rated district so the later
decision-change core has BOTH poles to exercise. Determinism mirrors
:class:`app.adapters.funding.simulated.SimulatedFundingSignalAdapter`: a named
district returns its fixed fixture row; any OTHER ``district_id`` is derived
deterministically from a salted digest, so the same id always reads the same
enrichment across calls and across fresh adapter instances.
"""

from __future__ import annotations

import hashlib

from app.adapters.open_data.base import DistrictEnrichment, OpenDataAdapter

# The A–F grade band a derived district draws from (aggregate accountability).
_GRADES = ("A", "B", "C", "D", "F")


def _digest_int(district_id: str, salt: str) -> int:
    """A stable, salted BLAKE2b digest of ``district_id`` as an int (pure, no I/O)."""
    raw = hashlib.blake2b(f"{salt}:{district_id}".encode(), digest_size=8).digest()
    return int.from_bytes(raw, "big")


class SeededOpenDataAdapter(OpenDataAdapter):
    """In-memory synthetic source for district enrichment (INV-1/INV-9).

    Two modes, both pure and offline:

    - **Fixture** — the named fixture districts (incl. the A-rated and D/F-rated
      poles) return their fixed aggregate rows.
    - **Derived** — any other ``district_id`` is derived deterministically from the
      id via salted digests, so the degrade-to-seeded path (INV-8) always returns a
      stable, typed row for a live id it has never seen.

    No network client exists on this class — aggregate-offline is therefore a
    structural property, not a configured behaviour (INV-1/INV-6/INV-9).
    """

    # The two fixture poles the decision-change core needs (named so tests + the
    # core can reference them without hardcoding an id literal).
    A_RATED_DISTRICT = "057905"  # synthetic TEA-style district id, A-rated
    LOW_RATED_DISTRICT = "101912"  # synthetic TEA-style district id, F-rated

    # The named fixture rows (aggregate, district-level only — INV-1/INV-6).
    _FIXTURE: dict[str, DistrictEnrichment] = {
        A_RATED_DISTRICT: DistrictEnrichment(
            district_id=A_RATED_DISTRICT,
            d_rating="A",
            staar_proficiency=0.78,
            per_pupil_spend=11_200.0,
            enrollment=1_450,
        ),
        LOW_RATED_DISTRICT: DistrictEnrichment(
            district_id=LOW_RATED_DISTRICT,
            d_rating="F",
            staar_proficiency=0.31,
            per_pupil_spend=9_100.0,
            enrollment=820,
        ),
        # A mid-band district so the fixture is not just the two poles.
        "031903": DistrictEnrichment(
            district_id="031903",
            d_rating="C",
            staar_proficiency=0.55,
            per_pupil_spend=10_300.0,
            enrollment=2_100,
        ),
    }

    def __init__(self, fixture: dict[str, DistrictEnrichment] | None = None) -> None:
        # Optional override of the synthetic fixture; never required, never mutated.
        self._fixture: dict[str, DistrictEnrichment] = dict(fixture or self._FIXTURE)

    def district_enrichment(self, district_id: str) -> DistrictEnrichment:
        """Return the aggregate enrichment for ``district_id`` (E1; INV-1/INV-6).

        A named fixture district wins; any other id is derived deterministically
        from the id. No I/O, no external API (INV-9).
        """
        fixed = self._fixture.get(district_id)
        if fixed is not None:
            return fixed
        # Derived: stable, salted digests per field — aggregate, never child-keyed.
        grade = _GRADES[_digest_int(district_id, "rating") % len(_GRADES)]
        staar = round((_digest_int(district_id, "staar") % 1001) / 1000.0, 4)
        # Per-pupil spend in a plausible aggregate band ($8,000–$15,999, whole USD).
        per_pupil = float(8_000 + _digest_int(district_id, "spend") % 8_000)
        # Enrollment in a plausible aggregate band (200–10,199 students).
        enrollment = 200 + _digest_int(district_id, "enrollment") % 10_000
        return DistrictEnrichment(
            district_id=district_id,
            d_rating=grade,
            staar_proficiency=staar,
            per_pupil_spend=per_pupil,
            enrollment=enrollment,
        )
