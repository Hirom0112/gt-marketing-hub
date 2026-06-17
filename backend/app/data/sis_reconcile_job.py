"""M5 — the daily SIS reconcile job (server-side orchestration; INV-1/INV-9).

Glues the cockpit's families to the SIS boundary: pull the joined families from
the repository, read the roster from the :class:`EnrollmentSystemAdapter`
(``SIS_MODE``), and run the pure :func:`app.core.sis_reconcile.reconcile` matcher.

A family is matched ONLY on its household contact (email/phone) — never on a
child key (INV-1/INV-6). In v1 the verdicts are recomputed on read over the
in-memory cohort; the Supabase path persists each verdict to the ``sis_status``
table via ``service_role`` (D-RLS-4) for the family status page to read.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.adapters.sis.base import EnrollmentSystemAdapter
from app.core.params import Params
from app.core.sis_reconcile import (
    PAID_FUNDING_STATES,
    FamilyMatchKey,
    SisRosterRow,
    SisVerdict,
    reconcile,
)
from app.data.repository import FamilyRepository, JoinedFamily


def family_match_keys(joined: Iterable[JoinedFamily]) -> list[FamilyMatchKey]:
    """Project joined families to the matcher's keys (household contact only)."""
    return [
        FamilyMatchKey(
            family_id=jf.family.family_id,
            email=jf.family.primary_contact_synthetic_email,
            phone=jf.lead.synthetic_phone if jf.lead else None,
            paid=jf.family.funding_state in PAID_FUNDING_STATES,
        )
        for jf in joined
    ]


def run_sis_reconcile(
    repository: FamilyRepository, adapter: EnrollmentSystemAdapter, params: Params
) -> list[SisVerdict]:
    """Reconcile the cockpit's paid families against the SIS roster (the job)."""
    joined = repository.list_joined()
    # Convert the adapter boundary's RosterRecords into the core's flat row shape
    # (keeps app.core free of an app.adapters import — ARCHITECTURE §3 purity).
    rows = [
        SisRosterRow(
            external_id=record.external_id,
            email=record.match_attrs.email,
            phone=record.match_attrs.phone,
            enrollment_status=record.enrollment_status,
            confirmed_at=record.confirmed_at,
        )
        for record in adapter.fetch_roster()
    ]
    return reconcile(family_match_keys(joined), rows, params)
