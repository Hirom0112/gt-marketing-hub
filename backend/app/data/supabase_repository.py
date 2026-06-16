"""Supabase-backed :class:`FamilyRepository` — the production store seam (S14 W2).

This is the live half of the NFR-8 store seam (``app.data.repository``). The
in-memory impl stays the no-credential v1 fallback (A-3); this one is bound at
the composition root (``app.api.deps._build_repository``) whenever ``SUPABASE_URL``
is set. ``core/`` and the routers change zero lines — they depend on the
:class:`FamilyRepository` *interface*, never on a concrete store (NFR-8).

Transport (A-24, M1 corrected mechanism): the runtime is **stateless AWS
Lambda/Mangum** (ARCHITECTURE.md §12), so there is no in-memory cache and no
Supabase Realtime subscription — every method issues a **fresh PostgREST REST
GET each call** (query-per-request). The HTTP client is ``httpx`` (already a
runtime dep, the house pattern of ``app.adapters.hubspot.live_adapter``); we do
NOT add the ``supabase`` python client. PostgREST's embed syntax
(``select=*,leads_new(*),app_form(*),…``) yields the whole join in ONE request.

Auth (INV-5 / D-RLS-4): all reads use the **service_role** key (BYPASSRLS,
server-only). It is read from the environment here and never leaves the backend
— it must never appear in any ``frontend/`` / ``apply/`` / ``VITE_*`` path.

Join semantics (A-24): the cockpit read is **INNER join
``family_record ⋈ leads_new``, LEFT join ``app_form`` / ``enrollment_forms`` /
``community_profiles``**. A half-written submission (a ``family_record`` with no
``leads_new`` yet) must be INVISIBLE; but a thin interest lead (spine + lead
only, no ``app_form``) MUST appear. So a family surfaces iff it has a
``leads_new`` row.

Stage is **DERIVED on read** (A-24, M2): ``family_record.current_stage`` is a
stored write-time placeholder and is NOT authoritative. Every method that needs
a stage runs the pure :func:`app.core.stage_machine.derive_stage` over the
joined source rows; :meth:`pipeline_counts` groups by the DERIVED stage and
:meth:`list_families` filters by it.

Purity: like the in-memory impl, this module is plain data access — it imports
no ``app.ai`` / ``app.adapters`` modules. It depends on the pure data models, the
pure stage machine, and ``httpx``.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from app.core.params import Params
from app.core.stage_machine import FamilyInputs, derive_stage
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    LeadsNew,
    SeamStatus,
    Stage,
)
from app.data.repository import FamilyRepository, JoinedFamily

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a
# third party's URLs, the same carve-out as the HubSpot adapter's object paths).
_REST = "/rest/v1"
# The one-request embed: the spine plus all four source tables. ``leads_new`` is
# the INNER side (``!inner`` makes PostgREST drop a spine row with no lead — the
# partial-invisible rule); the other three are LEFT (a thin interest lead with no
# app_form still surfaces). ``*`` selects whatever columns the (frozen) cloud
# schema actually has, so a model field absent from the DB (e.g. a later-added
# default-1 ``num_children``) simply falls back to its pydantic default.
_FAMILY_EMBED = "*,leads_new!inner(*),app_form(*),enrollment_forms(*),community_profiles(*)"


@dataclass(frozen=True)
class DropOffPoint:
    """One family's last-known apply-flow position before exit (A-24 drop-off view).

    Metadata only — ``step`` / ``field_key`` / ``event_type`` (the closed
    ``apply_event_type`` set), never a typed value/content and never a child key
    (INV-1/INV-6/COPPA). Surfaced in the deal view as "where this family froze".
    ``None`` when the family emitted no ``apply_events``.
    """

    family_id: UUID
    step: str
    field_key: str | None
    event_type: str
    occurred_at: str | None


@dataclass(frozen=True)
class DropOffBucket:
    """One cohort drop-off heatmap cell — a count of exits at a step/field (A-24).

    Aggregate only: ``count`` families froze at this ``step`` (and ``field_key``,
    when present). No family/child identity — it answers *where the cohort
    freezes*, not *who*.
    """

    step: str
    field_key: str | None
    count: int


class SupabaseError(RuntimeError):
    """A non-2xx PostgREST response — fail loud rather than serve a partial read."""


class SupabaseFamilyRepository(FamilyRepository):
    """Live :class:`FamilyRepository` over Supabase PostgREST (service_role).

    Query-per-request: each method opens nothing long-lived; it issues a fresh
    GET via the injected (or per-call constructed) ``httpx`` client. The
    service_role key bypasses RLS so the cockpit reads across families (the
    server-only cross-family read path, D-RLS-4).

    Args:
        base_url: The Supabase project URL (``https://<ref>.supabase.co``).
        service_role_key: The server-only service_role JWT (BYPASSRLS). NEVER
            client-exposed (INV-5 / D-RLS-4).
        params: Loaded §8 params — supplies the stage machine's signature (the
            stage rules read no tunable today, but the deriver takes ``params``).
        client: An optional injected ``httpx.Client`` (tests pass one wired to a
            ``MockTransport``). When omitted, each request opens a short-lived
            client (stateless-runtime friendly).
        timeout: Per-request timeout seconds (a fixed transport setting, not a
            product tunable — same posture as the HubSpot adapter's client).
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_role_key: str,
        params: Params,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._key = service_role_key
        self._params = params
        self._client = client
        self._timeout = timeout

    # ------------------------------------------------------------------ I/O
    def _headers(self) -> dict[str, str]:
        """service_role auth on every request (apikey + Bearer)."""
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """One PostgREST GET → the decoded JSON array (fail loud on non-2xx)."""
        url = f"{self._base_url}{path}"
        headers = self._headers()
        if self._client is not None:
            response = self._client.get(url, params=params, headers=headers)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(url, params=params, headers=headers)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST GET {path} → {response.status_code}: {response.text[:300]}"
            )
        body: Any = response.json()
        if not isinstance(body, list):  # PostgREST returns an array for a table select.
            raise SupabaseError(f"PostgREST GET {path} returned a non-array body")
        return body

    # ---------------------------------------------------------------- mapping
    @staticmethod
    def _first(rows: object) -> dict[str, Any] | None:
        """An embedded one-to-many list → its first row (the join's single source row).

        PostgREST embeds a child table keyed by a non-unique ``family_id`` as a
        LIST. The §4.1 join is one source row per family, so we take the head (or
        ``None`` for a LEFT-join miss, where the list is empty).
        """
        if isinstance(rows, list) and rows:
            head = rows[0]
            if isinstance(head, dict):
                return head
        return None

    @staticmethod
    def _best_enrollment(rows: object) -> dict[str, Any] | None:
        """The MOST-ADVANCED ``enrollment_forms`` row when several exist.

        The insert-only apply flow (RLS grants INSERT, not UPDATE — 0003) can
        write more than one ``enrollment_forms`` row per family: a mid-flow row
        at the enroll step, then a second at the tuition step (all six signed,
        ``tuition_step_unlocked`` true). PostgREST embeds them as a list in no
        guaranteed order, so :meth:`_first`'s head pick could surface the LESS
        advanced row and derive ``enroll`` for a family that actually reached
        ``tuition``. We instead pick the furthest-progressed row — unlocked first,
        then most forms signed — so the DERIVED stage reflects the family's true
        furthest state (the contract the SPA writes against). Single-row families
        (the common case) are unaffected: the one row is the max.
        """
        if not isinstance(rows, list) or not rows:
            return None
        candidates = [r for r in rows if isinstance(r, dict)]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda r: (bool(r.get("tuition_step_unlocked")), int(r.get("forms_signed") or 0)),
        )

    def _to_joined(self, row: dict[str, Any]) -> JoinedFamily:
        """Map one embedded PostgREST row → a :class:`JoinedFamily`.

        ``family_record`` columns sit at the top level; the four source tables are
        nested under their table names. Pydantic fills any model field the (frozen)
        cloud schema lacks from its default, so a DB missing a later-added column
        round-trips cleanly. ``leads_new`` is the INNER side and is always present
        here (the embed dropped lead-less spine rows).
        """
        family = FamilyRecord.model_validate(row)
        lead_row = self._first(row.get("leads_new"))
        app_row = self._first(row.get("app_form"))
        # enrollment_forms can have >1 row under the insert-only flow; take the
        # most-advanced so a completed family derives `tuition`, not `enroll`.
        enroll_row = self._best_enrollment(row.get("enrollment_forms"))
        community_row = self._first(row.get("community_profiles"))
        return JoinedFamily(
            family=family,
            lead=LeadsNew.model_validate(lead_row) if lead_row is not None else None,
            app_form=AppForm.model_validate(app_row) if app_row is not None else None,
            enrollment_forms=(
                EnrollmentForms.model_validate(enroll_row) if enroll_row is not None else None
            ),
            community_profile=(
                CommunityProfile.model_validate(community_row)
                if community_row is not None
                else None
            ),
        )

    def _derived_stage(self, joined: JoinedFamily) -> Stage:
        """The §5.1 stage DERIVED from the joined source rows (A-24 M2), not stored."""
        inputs = FamilyInputs(
            app_form=joined.app_form,
            enrollment_forms=joined.enrollment_forms,
            stalled_since=joined.family.stalled_since,
        )
        return derive_stage(inputs, self._params)

    # ----------------------------------------------------------- joined reads
    def _fetch_joined(self) -> list[JoinedFamily]:
        """The cohort: the embedded INNER/LEFT join, mapped to :class:`JoinedFamily`."""
        rows = self._get(f"{_REST}/family_record", {"select": _FAMILY_EMBED})
        return [self._to_joined(row) for row in rows]

    def list_joined(self) -> list[JoinedFamily]:
        # Every family (with a lead) joined to its source rows — the work-queue's
        # input. A SQL store maps this to the same join the in-memory impl does.
        return self._fetch_joined()

    def get_family(self, family_id: UUID) -> JoinedFamily | None:
        rows = self._get(
            f"{_REST}/family_record",
            {"select": _FAMILY_EMBED, "family_id": f"eq.{family_id}"},
        )
        if not rows:
            return None
        return self._to_joined(rows[0])

    # --------------------------------------------------------------- spine reads
    def list_families(
        self,
        *,
        stage: Stage | None = None,
        funding_state: FundingState | None = None,
        seam_status: SeamStatus | None = None,
    ) -> list[FamilyRecord]:
        # Stage is DERIVED on read (A-24 M2), so the stage filter cannot push down
        # to a PostgREST `current_stage=eq.` predicate (that column is a stale
        # placeholder). We read the joined cohort, derive each stage, and filter
        # in-process — the same authoritative derivation `pipeline_counts` uses.
        # funding_state / seam_status ARE authoritative spine columns; they could
        # push down, but filtering here keeps one read path (the join) and one
        # source of truth, and the demo cohort is small (query-per-request).
        joined = self._fetch_joined()
        result: list[FamilyRecord] = []
        for jf in joined:
            family = jf.family
            if stage is not None and self._derived_stage(jf) != stage:
                continue
            if funding_state is not None and family.funding_state != funding_state:
                continue
            if seam_status is not None and family.crm_seam_status != seam_status:
                continue
            result.append(family)
        return result

    def pipeline_counts(self) -> dict[Stage, int]:
        # Group by the DERIVED stage (A-24 M2), zero-filling every §4.8 stage so
        # the dashboard always renders all four columns (the in-memory impl's
        # contract). The stored `current_stage` is never consulted.
        counts: dict[Stage, int] = dict.fromkeys(Stage, 0)
        for joined in self._fetch_joined():
            counts[self._derived_stage(joined)] += 1
        return counts

    # ------------------------------------------------------- drop-off views
    def drop_off_for_family(self, family_id: UUID) -> DropOffPoint | None:
        """The family's last apply-flow position before exit (A-24 deal-view view).

        Prefers the explicit ``last_step_before_exit`` event; absent that, the
        most-recent event of any kind. Metadata only (step/field/event), never a
        value/content or child key (INV-1/INV-6). ``None`` when the family emitted
        no ``apply_events``.
        """
        rows = self._get(
            f"{_REST}/apply_events",
            {
                "select": "step,field_key,event_type,occurred_at",
                "family_id": f"eq.{family_id}",
                "order": "occurred_at.desc",
            },
        )
        if not rows:
            return None
        exit_rows = [r for r in rows if r.get("event_type") == "last_step_before_exit"]
        chosen = exit_rows[0] if exit_rows else rows[0]
        return DropOffPoint(
            family_id=family_id,
            step=str(chosen.get("step", "")),
            field_key=chosen.get("field_key"),
            event_type=str(chosen.get("event_type", "")),
            occurred_at=chosen.get("occurred_at"),
        )

    def drop_off_heatmap(self) -> list[DropOffBucket]:
        """Cohort drop-off heatmap — exit counts grouped by step+field (A-24).

        Counts ``last_step_before_exit`` events per (``step``, ``field_key``): the
        cells where families freeze, aggregate only (no family/child identity).
        Ordered by descending count then step for a stable, scannable surface.
        """
        rows = self._get(
            f"{_REST}/apply_events",
            {
                "select": "step,field_key",
                "event_type": "eq.last_step_before_exit",
            },
        )
        tally: Counter[tuple[str, str | None]] = Counter()
        for row in rows:
            step = str(row.get("step", ""))
            field_key = row.get("field_key")
            tally[(step, field_key)] += 1
        buckets = [
            DropOffBucket(step=step, field_key=field_key, count=count)
            for (step, field_key), count in tally.items()
        ]
        buckets.sort(key=lambda b: (-b.count, b.step, b.field_key or ""))
        return buckets


def build_supabase_repository(params: Params) -> SupabaseFamilyRepository | None:
    """Construct the Supabase repo from the environment, or ``None`` when unbound.

    Reads ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the env
    at the composition root (the same `os.environ` read the existing
    ``COCKPIT_SCENARIO`` toggle does). Returns ``None`` when ``SUPABASE_URL`` is
    absent so the caller falls back to the in-memory store (A-3). A placeholder
    angle-bracket value (the ``.env.example`` sentinel) counts as unset — same
    posture as the Settings secret readers.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    return SupabaseFamilyRepository(base_url=url, service_role_key=key, params=params)
