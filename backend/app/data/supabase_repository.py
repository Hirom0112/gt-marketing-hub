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
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.core.jwt_verify import sign_hs256
from app.core.params import Params
from app.core.program import Program
from app.core.stage_machine import FamilyInputs, derive_stage
from app.data.models import (
    AppForm,
    CommunityProfile,
    EnrollmentForms,
    FamilyRecord,
    FundingState,
    LeadAssignment,
    LeadsNew,
    SeamStatus,
    Stage,
    Student,
)
from app.data.repository import (
    FamilyRepository,
    HouseholdChildStage,
    HouseholdRollUp,
    JoinedFamily,
    JoinedStudent,
    OwnerScope,
    _matches_owner,
    roll_up_households,
    student_stage_counts,
)

# Re-exported for back-compat: these moved to `app.data.repository` (the shared
# home both stores import from, since this module already imports from there — so
# the helper/dataclasses live there to avoid a circular import). Existing callers
# that import them from here keep working.
__all__ = ["HouseholdChildStage", "HouseholdRollUp"]

# PostgREST surface (the API's own fixed routes — INV-11 does not apply to a
# third party's URLs, the same carve-out as the HubSpot adapter's object paths).
_REST = "/rest/v1"
# A-38 read-path identity (protocol constants, one-home here — same carve-out as
# the adapter's fixed strings, A-39). The minted read token carries `role` =
# `app_runtime` so the Supabase request pipeline `SET ROLE`s to the non-`BYPASSRLS`
# server role; `aud` = `authenticated` matches PostgREST's audience; `sub` is a
# FIXED synthetic service principal (the cockpit is one trusted server, not an
# end user) — it only has to be a non-null UUID to satisfy the policies' `auth.uid()
# IS NOT NULL` guard. It owns no rows, so it can read ONLY via the program-scoped
# `app_runtime` policies (0031), never the owner-scoped ones.
_APP_RUNTIME_ROLE = "app_runtime"
_APP_RUNTIME_AUD = "authenticated"
_APP_RUNTIME_SUB = "00000000-0000-0000-0000-0000000000ff"
# The one-request embed: the spine plus all four source tables. ``leads_new`` is
# the INNER side (``!inner`` makes PostgREST drop a spine row with no lead — the
# partial-invisible rule); the other three are LEFT (a thin interest lead with no
# app_form still surfaces). ``*`` selects whatever columns the (frozen) cloud
# schema actually has, so a model field absent from the DB (e.g. a later-added
# default-1 ``num_children``) simply falls back to its pydantic default.
_FAMILY_EMBED = "*,leads_new!inner(*),app_form(*),enrollment_forms(*),community_profiles(*)"
# The per-child read (TODO.md R1): one row per ``student`` joined to its OWN
# application + enrollment packet and its parent household. PostgREST resolves
# ``app_form`` / ``enrollment_forms`` as to-ONE embeds via the student's
# ``app_form_id`` / ``enrollment_form_id`` FK columns (per-child, A-24), and the
# parent ``family_record`` (with its lead + community_profile) via the
# ``family_id`` FK. ``family_record!inner`` drops a child whose household row is
# absent (a defensive orphan guard, mirroring the family read's lead ``!inner``).
# ``*`` selects whatever columns the frozen cloud schema has, so a model field
# the DB lacks falls back to its pydantic default.
_STUDENT_EMBED = (
    "*,family_record!inner(*,leads_new(*),community_profiles(*)),app_form(*),enrollment_forms(*)"
)

# A1 app-layer program isolation (PLAN_v2 §A1; ASSUMPTIONS A-37). The backend reads
# Supabase over the service_role key, which BYPASSES RLS (the proper non-BYPASSRLS
# `app_runtime` connection swap lands with B1's auth rewrite — ASSUMPTIONS A-38), so
# the RESTRICTIVE program policy in 0024 does NOT bound the app's own read path. This
# set is the defense-in-depth: every program-scoped table read carries an explicit
# `program_id=eq.<active>` filter and every insert/update stamps it, so the cockpit can
# only touch the active program's rows. The 9 tables are the EXACT program-partitioned
# set from `0024_program_isolation.sql` / A-37 — `community_profiles` and
# `assignment_cursor` are NOT here (operational/global, untagged). Pinned inline like
# the migration's own table list (the schema vocabulary's canonical home, INV-11).
_PROGRAM_SCOPED_TABLES: frozenset[str] = frozenset(
    {
        "family_record",
        "leads_new",
        "app_form",
        "enrollment_forms",
        "apply_events",
        "student",
        "voucher_event",
        "sis_status",
        "lead_assignment",
    }
)


def _is_program_scoped(path: str) -> bool:
    """True when a PostgREST path targets an A1 program-scoped table (0024 / A-37).

    Pure decision over the trailing table segment of the REST path (e.g.
    ``"/rest/v1/family_record"`` → ``True``; ``"/rest/v1/assignment_cursor"`` →
    ``False``). The query-builder boundary uses it to decide whether to inject the
    active ``program_id`` filter/stamp (the app-layer isolation, defense-in-depth).
    """
    return path.rsplit("/", 1)[-1] in _PROGRAM_SCOPED_TABLES


@dataclass(frozen=True)
class DropOffPoint:
    """One family's last-known apply-flow position before exit (A-24 drop-off view).

    Step → form → field granularity (0006): ``step`` ∈ {interest, apply, enroll,
    tuition}, ``form_key`` the sub-form id (e.g. ``data_collection_consent``;
    ``None`` for step-level events), ``field_key`` the field within it. Metadata
    only — ``form_key`` is a STRUCTURAL form id, never a typed value/content and
    never a child key (INV-1/INV-6/COPPA). Surfaced in the deal view as "stopped
    at Enroll · Data Collection Consent · signature line". ``None`` when the
    family emitted no ``apply_events``.
    """

    family_id: UUID
    step: str
    form_key: str | None
    field_key: str | None
    event_type: str
    occurred_at: str | None


@dataclass(frozen=True)
class DropOffBucket:
    """One cohort drop-off heatmap cell — a count of exits at a step/form/field (A-24).

    Aggregate only: ``count`` families froze at this ``step`` / ``form_key`` /
    ``field_key`` (any of ``form_key`` / ``field_key`` ``None`` for coarser
    events). No family/child identity — it answers *where the cohort freezes*, not
    *who*. ``form_key`` is a structural sub-form id (INV-1/INV-6).
    """

    step: str
    form_key: str | None
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
        program: The active :class:`~app.core.program.Program` (A1). When set, every
            program-scoped read carries a ``program_id=eq.<program>`` filter and every
            insert/update stamps it — the app-layer isolation that bounds the
            service_role read path to the active program (it BYPASSES the 0024
            RESTRICTIVE RLS, so isolation is enforced in code; PLAN_v2 §A1 / A-38).
            ``None`` (the test/back-compat default) applies NO program filter, so the
            existing PostgREST contract tests are unchanged.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_role_key: str,
        params: Params,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        program: Program | None = None,
        anon_key: str | None = None,
        jwt_secret: str | None = None,
        app_runtime_reads: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._key = service_role_key
        self._params = params
        self._client = client
        self._timeout = timeout
        self._program = program
        # A-38 read-path swap: when enabled (and a program + JWT secret + anon key
        # are present), program-scoped READS authenticate AS the non-`BYPASSRLS`
        # `app_runtime` role via a minted, program-claim-carrying token so RLS — not
        # just the app-layer `_program_filtered` — enforces program isolation. Writes
        # stay on service_role (INV-5 / D-RLS-4). Disabled by default ⇒ unchanged
        # service_role read path (the existing MockTransport contract tests + the
        # pre-provisioning posture both keep working).
        self._anon_key = anon_key
        self._jwt_secret = jwt_secret
        self._app_runtime_reads = app_runtime_reads

    # ------------------------------------------------------------------ I/O
    def _headers(self) -> dict[str, str]:
        """service_role auth on every request (apikey + Bearer)."""
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
        }

    def _app_runtime_reads_active(self, path: str) -> bool:
        """Whether THIS read should authenticate as ``app_runtime`` (A-38).

        Fail-closed: the swap engages ONLY when explicitly enabled AND a program is
        active AND both the JWT secret (to mint/sign the token) and the anon key (the
        PostgREST ``apikey``) are present AND the path targets a program-scoped table.
        Any missing piece falls back to the service_role read path — never a silent
        un-authenticated read.
        """
        return (
            self._app_runtime_reads
            and self._program is not None
            and bool(self._jwt_secret)
            and bool(self._anon_key)
            and _is_program_scoped(path)
        )

    def _mint_read_token(self) -> str:
        """Mint the short-lived HS256 token that authenticates a read AS ``app_runtime``.

        Carries ``role=app_runtime`` (the Supabase pipeline ``SET ROLE``s to the
        non-`BYPASSRLS` server role) and ``app_metadata.program_id`` = the active
        program (the 0031 program-read policy + the 0024 RESTRICTIVE policy both key
        on this claim), so RLS bounds the read to exactly this program. Signed with
        the same ``SUPABASE_JWT_SECRET`` Supabase uses (stdlib HS256 via
        :func:`app.core.jwt_verify.sign_hs256` — no new dep). Only called when
        :meth:`_app_runtime_reads_active` is true, so the secret/program are present.
        """
        assert self._jwt_secret is not None and self._program is not None
        ttl = self._params.programs.app_runtime_read_token_ttl_seconds
        claims: dict[str, Any] = {
            "role": _APP_RUNTIME_ROLE,
            "sub": _APP_RUNTIME_SUB,
            "aud": _APP_RUNTIME_AUD,
            "exp": int(time.time()) + ttl,
            "app_metadata": {"program_id": self._program.value},
        }
        return sign_hs256(claims, secret=self._jwt_secret)

    def _read_headers(self, path: str) -> dict[str, str]:
        """Auth headers for a GET — ``app_runtime`` (A-38) when active, else service_role.

        The A-38 read path uses the anon key as the PostgREST ``apikey`` and the
        minted ``app_runtime`` token as the Bearer, so the request is RLS-bounded to
        the active program. Otherwise the unchanged service_role headers (BYPASSRLS).
        """
        if not self._app_runtime_reads_active(path):
            return self._headers()
        assert self._anon_key is not None
        return {
            "apikey": self._anon_key,
            "Authorization": f"Bearer {self._mint_read_token()}",
            "Accept": "application/json",
        }

    def _program_filtered(self, path: str, params: dict[str, str]) -> dict[str, str]:
        """Add the active ``program_id=eq.<program>`` filter on a program-scoped read.

        The app-layer isolation chokepoint for reads (A1): when a program is active
        and the path targets a program-scoped table, the cohort is bounded to that
        program even though the service_role connection bypasses RLS. A no-op when no
        program is active (test/back-compat) or the table is operational/global
        (A-37). Never overwrites a caller-supplied ``program_id`` (none does today).
        """
        if self._program is None or not _is_program_scoped(path):
            return params
        if "program_id" in params:
            return params
        return {**params, "program_id": f"eq.{self._program.value}"}

    def _program_stamped(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Stamp the active ``program_id`` onto a write to a program-scoped table.

        The write-side of the app-layer isolation (A1): an insert/update can only
        land a row in the active program (the WITH-CHECK analog the bypassed RLS
        would otherwise enforce). A no-op when no program is active or the table is
        operational/global. Distinct from a row's domain ``program`` field (e.g.
        ``voucher_event.program`` = ``tx_tefa``) — ``program_id`` is the A1 TENANT tag.
        """
        if self._program is None or not _is_program_scoped(path):
            return payload
        return {**payload, "program_id": self._program.value}

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """One PostgREST GET → the decoded JSON array (fail loud on non-2xx).

        Reads authenticate as ``app_runtime`` (A-38, RLS-enforced program isolation)
        when that swap is active for this path, else over service_role. The app-layer
        ``_program_filtered`` is retained as belt-and-suspenders under both.
        """
        params = self._program_filtered(path, params)
        url = f"{self._base_url}{path}"
        headers = self._read_headers(path)
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

    def _patch(self, path: str, params: dict[str, str], payload: dict[str, Any]) -> None:
        """One PostgREST PATCH (the reconcile write seam — TODO.md R1; fail loud).

        A row-scoped UPDATE via the service_role key (BYPASSRLS, server-only —
        INV-5 / D-RLS-4): the ``params`` carry the ``family_id=eq.<id>`` filter so
        exactly one row is written. ``Prefer: return=minimal`` keeps the response
        body empty (we do not re-read here). The key never leaves the backend.
        """
        # A1: AND the active program onto the row filter (defense-in-depth — a
        # cross-program family_id cannot be PATCHed under the active program).
        params = self._program_filtered(path, params)
        url = f"{self._base_url}{path}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        if self._client is not None:
            response = self._client.patch(url, params=params, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.patch(url, params=params, headers=headers, json=payload)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST PATCH {path} → {response.status_code}: {response.text[:300]}"
            )

    def _post(self, path: str, payload: dict[str, Any]) -> None:
        """One PostgREST POST (the append-only insert seam — TODO.md R2; fail loud).

        A row INSERT via the service_role key (BYPASSRLS, server-only — INV-5 /
        D-RLS-4). Used for the append-only ``voucher_event`` timeline: a state
        transition is a fact, never updated after insert (the table grants only
        SELECT + INSERT). ``Prefer: return=minimal`` keeps the response body empty
        (we do not re-read here). The key never leaves the backend.
        """
        # A1: stamp the active program onto the inserted row (the WITH-CHECK analog).
        payload = self._program_stamped(path, payload)
        url = f"{self._base_url}{path}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        if self._client is not None:
            response = self._client.post(url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise SupabaseError(
                f"PostgREST POST {path} → {response.status_code}: {response.text[:300]}"
            )

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
    def _embedded(value: object) -> dict[str, Any] | None:
        """A PostgREST embed → its single row, accepting BOTH embed shapes.

        A one-to-many embed (keyed by a non-unique FK) is a LIST; a to-one embed
        (keyed by an FK column, e.g. ``student.app_form_id → app_form``) is a
        single OBJECT (or null). The per-child read mixes both, so this accepts a
        list (take the head) or a dict (use it directly); a miss yields ``None``.
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value:
            head = value[0]
            if isinstance(head, dict):
                return head
        return None

    @staticmethod
    def _household_only(rows: object) -> list[dict[str, Any]]:
        """Keep only HOUSEHOLD-grain rows (``student_id`` NULL) from an embedded list.

        The ``app_form`` / ``enrollment_forms`` tables hold BOTH the household packet
        (``student_id`` NULL) and one packet per CHILD (``student_id`` set) — all
        sharing ``family_id`` — so a PostgREST FAMILY embed returns them together.
        The household (deal) grain must read ONLY the household packet; the per-child
        packets are read by the student joins via the student embed, not here. Without
        this, a multi-child family picks up a child's (possibly fully-signed) packet
        and misderives the HOUSEHOLD as forms-cleared / ``tuition`` / ``recovered`` —
        the bug that dropped the one 2-child demo family out of the active triage.
        A row with no ``student_id`` key (pre-student cloud data) counts as household,
        so this is backward-compatible.
        """
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict) and r.get("student_id") is None]

    @staticmethod
    def _child_only(rows: object) -> list[dict[str, Any]]:
        """Keep only per-CHILD rows (``student_id`` set) from an embedded list.

        The complement of :meth:`_household_only`: the household grain uses this ONLY
        as a fallback when a family has no household-grain packet at all (a per-child
        LIVE application, A-24) — picking the furthest child's packet so the household
        still shows aggregate progress instead of an empty ``interest``.
        """
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict) and r.get("student_id") is not None]

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
        # app_form / enrollment_forms hold BOTH the household packet and the per-child
        # packets (all share family_id); the HOUSEHOLD grain reads only its own
        # (student_id NULL) — never a child's — so a multi-child family doesn't borrow
        # a child's progress (the Rivera forms-cleared/recovered bug). When there is NO
        # household packet (a per-child LIVE application writes only child packets,
        # A-24), the household grain falls back to the FURTHEST child packet so the
        # household still shows sensible aggregate progress — the fallback only fires
        # when no household-grain row exists, so synthetic households are unaffected.
        app_rows = row.get("app_form")
        app_row = self._first(self._household_only(app_rows)) or self._first(
            self._child_only(app_rows)
        )
        enroll_rows = row.get("enrollment_forms")
        enroll_row = self._best_enrollment(
            self._household_only(enroll_rows)
        ) or self._best_enrollment(self._child_only(enroll_rows))
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

    def list_joined(self, *, owner: OwnerScope = None) -> list[JoinedFamily]:
        # Every family (with a lead) joined to its source rows — the work-queue's
        # input. A SQL store maps this to the same join the in-memory impl does.
        # ``owner`` applies the M1 deal-ownership scope in-process (one read path,
        # like the stage filter); a production push-down would add an
        # `assigned_rep_id=eq.` predicate, but the demo cohort is small.
        return [jf for jf in self._fetch_joined() if _matches_owner(jf.family, owner)]

    def _student_derived_stage(self, joined: JoinedStudent) -> Stage:
        """The §5.1 stage DERIVED from the CHILD's own source rows (A-24 M2).

        Identical contract to :meth:`_derived_stage` for a family, but over the
        student's own ``app_form`` / ``enrollment_forms`` (resolved per-child via
        the student's FK columns), so each child's funnel position is its own.
        """
        inputs = FamilyInputs(
            app_form=joined.app_form,
            enrollment_forms=joined.enrollment_forms,
            stalled_since=joined.student.stalled_since,
        )
        return derive_stage(inputs, self._params)

    def _to_joined_student(self, row: dict[str, Any]) -> JoinedStudent:
        """Map one embedded ``student`` PostgREST row → a :class:`JoinedStudent`.

        ``student`` columns sit at the top level; the parent ``family_record`` is
        nested (with its own ``leads_new`` / ``community_profiles`` embeds), and
        the child's own ``app_form`` / ``enrollment_forms`` are to-one embeds via
        the student's FK columns. The student's stored ``current_stage`` is a
        write-time placeholder; the caller re-derives it on read (A-24 M2).
        """
        family_row = row.get("family_record")
        if not isinstance(family_row, dict):
            raise SupabaseError("student row missing its embedded family_record")
        family = FamilyRecord.model_validate(family_row)
        lead_row = self._embedded(family_row.get("leads_new"))
        community_row = self._embedded(family_row.get("community_profiles"))
        # The child's own app/enrollment are to-ONE FK embeds — PostgREST returns a
        # single object (or null), not a list. `_embedded` accepts both shapes.
        app_row = self._embedded(row.get("app_form"))
        enroll_row = self._embedded(row.get("enrollment_forms"))
        return JoinedStudent(
            student=Student.model_validate(row),
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

    def list_students(self, *, owner: OwnerScope = None) -> list[JoinedStudent]:
        # The real household→child grain (TODO.md R1): one JoinedStudent per child
        # in the live `student` table, joined to its OWN app_form/enrollment_forms
        # (per-child, via the student's FK columns) and its parent household
        # (family_record + lead + community_profile). Stage is DERIVED on read with
        # the SAME pure stage_machine the family path uses (A-24 M2) and written
        # back onto the student so downstream consumers (the per-child queue,
        # recovery state) read the authoritative funnel position, not the stored
        # placeholder. service_role bypasses RLS for this cross-household read
        # (D-RLS-4). When the live `student` table is empty, this is [] — the same
        # empty board as before, now backed by a real query rather than a stub.
        # ``owner`` scopes each child by its PARENT household's assigned_rep_id (the
        # deal owner) — the M1 server-side ownership scope.
        rows = self._get(f"{_REST}/student", {"select": _STUDENT_EMBED})
        result: list[JoinedStudent] = []
        for row in rows:
            joined = self._to_joined_student(row)
            if not _matches_owner(joined.family, owner):
                continue
            stage = self._student_derived_stage(joined)
            # Round-trip the DERIVED stage onto the (frozen) student model so every
            # consumer reads the authoritative funnel position (A-24 M2).
            student = joined.student.model_copy(update={"current_stage": stage})
            result.append(
                JoinedStudent(
                    student=student,
                    family=joined.family,
                    lead=joined.lead,
                    app_form=joined.app_form,
                    enrollment_forms=joined.enrollment_forms,
                    community_profile=joined.community_profile,
                )
            )
        return result

    def household_roll_up(self) -> list[HouseholdRollUp]:
        """Group children by household → one row per household with per-child stages.

        Delegates to the shared pure :func:`app.data.repository.roll_up_households`
        (DRY: the same grouping both stores use). ``list_students`` here already
        writes the DERIVED stage (A-24 M2) onto each student, so the helper's
        "stage is already derived" contract holds. Households are keyed by
        ``family_record.user_id`` (TODO.md R1; ``None`` is its own server-only
        group); each row carries every child's stage + a ``worst_stage`` rollup
        (the least-advanced child). Behavior is unchanged by the extraction.
        """
        return roll_up_households(self.list_students())

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
        owner: OwnerScope = None,
    ) -> list[FamilyRecord]:
        # Stage is DERIVED on read (A-24 M2), so the stage filter cannot push down
        # to a PostgREST `current_stage=eq.` predicate (that column is a stale
        # placeholder). We read the joined cohort, derive each stage, and filter
        # in-process — the same authoritative derivation `pipeline_counts` uses.
        # funding_state / seam_status ARE authoritative spine columns; they could
        # push down, but filtering here keeps one read path (the join) and one
        # source of truth, and the demo cohort is small (query-per-request).
        # ``owner`` is the M1 deal-ownership scope, applied through the SAME shared
        # predicate the in-memory store uses (identical scoping across both stores).
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
            if not _matches_owner(family, owner):
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

    def student_pipeline_counts(self) -> dict[Stage, int]:
        # Per-CHILD tally (A-24): each child by its OWN derived stage. list_students
        # already derives + round-trips the stage; the shared counter re-derives
        # idempotently so both stores agree.
        return student_stage_counts(self.list_students(), self._params)

    # ----------------------------------------------------------- write seam
    @staticmethod
    def _json_value(value: object) -> Any:
        """Coerce a field value to its JSON form (an enum → its ``.value`` string)."""
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def mark_synced(self, family_id: UUID, synced_at: datetime) -> None:
        # PATCH crm_synced_at on the one spine row via service_role (INV-5). The
        # reconcile flow's push_local/accept commit — the deterministic core
        # derived the value (INV-2); this only persists it.
        self._patch(
            f"{_REST}/family_record",
            {"family_id": f"eq.{family_id}"},
            {"crm_synced_at": synced_at.isoformat()},
        )

    def apply_field(self, family_id: UUID, field: str, value: object) -> None:
        # PATCH one adopted tracked field (ACCEPT_MIRROR) on the spine row. Enums
        # serialize to their string value so PostgREST stores the DB column form.
        self._patch(
            f"{_REST}/family_record",
            {"family_id": f"eq.{family_id}"},
            {field: self._json_value(value)},
        )

    def update_attribution_utm(self, family_id: UUID, utm: dict[str, object]) -> None:
        # PATCH the attribution_utm jsonb column on the one spine row via
        # service_role (INV-5 / D-RLS-4) — the Module-7 UTM repair write. The whole
        # blob is replaced with the already-repaired mapping (the deterministic core
        # owns the derivation, INV-2; this only persists it). PostgREST stores a
        # dict directly as jsonb; an id with no row is a silent no-op.
        self._patch(
            f"{_REST}/family_record",
            {"family_id": f"eq.{family_id}"},
            {"attribution_utm": utm},
        )

    def assign_families(
        self, family_ids: list[UUID], agent_id: UUID, assigned_at: datetime
    ) -> list[UUID]:
        # M4 deterministic assignment write (A-30): PATCH assigned_rep_id +
        # assigned_at on each named spine row via service_role (server-only —
        # INV-5 / D-RLS-4). The deterministic core owns the write (INV-2), never
        # an LLM call. One PATCH per id (row-scoped filter); the columns are the
        # 0013_sales_agents.sql `assigned_rep_id` FK + `assigned_at`. PostgREST
        # silently no-ops an id with no matching row, mirroring the in-memory skip.
        for family_id in family_ids:
            self._patch(
                f"{_REST}/family_record",
                {"family_id": f"eq.{family_id}"},
                {"assigned_rep_id": str(agent_id), "assigned_at": assigned_at.isoformat()},
            )
        return list(family_ids)

    def unassign_families(self, family_ids: list[UUID], unassigned_at: datetime) -> list[UUID]:
        # Return to the intake pool: NULL assigned_rep_id via service_role PATCH.
        for family_id in family_ids:
            self._patch(
                f"{_REST}/family_record",
                {"family_id": f"eq.{family_id}"},
                {"assigned_rep_id": None, "assigned_at": unassigned_at.isoformat()},
            )
        return list(family_ids)

    def append_assignment_event(
        self,
        *,
        family_id: UUID,
        from_rep_id: UUID | None,
        to_rep_id: UUID | None,
        routed_role: str | None,
        assigned_by: str,
        reason: str,
        batch_id: str | None = None,
    ) -> None:
        # Append one immutable ownership-history fact to lead_assignment (0017):
        # service_role POST (BYPASSRLS, server-only — INV-5/D-RLS-4), append-only
        # (a POST, never UPDATE/DELETE). The deterministic core owns the decision
        # (INV-2); this only LOGS it.
        self._post(
            f"{_REST}/lead_assignment",
            {
                "assignment_id": str(uuid4()),
                "family_id": str(family_id),
                "from_rep_id": str(from_rep_id) if from_rep_id is not None else None,
                "to_rep_id": str(to_rep_id) if to_rep_id is not None else None,
                "routed_role": routed_role,
                "assigned_by": assigned_by,
                "reason": reason,
                "batch_id": batch_id,
            },
        )

    def list_assignments(self, family_id: UUID) -> list[LeadAssignment]:
        rows = self._get(
            f"{_REST}/lead_assignment",
            {"family_id": f"eq.{family_id}", "order": "occurred_at.asc"},
        )
        return [LeadAssignment.model_validate(r) for r in rows]

    def read_cursors(self) -> dict[str, int]:
        rows = self._get(f"{_REST}/assignment_cursor", {"select": "pool_key,next_index"})
        return {str(r["pool_key"]): int(r["next_index"]) for r in rows}

    def write_cursor(self, pool_key: str, next_index: int) -> None:
        # Upsert the per-pool cursor (service_role). PATCH the row; if no row yet,
        # POST it. assignment_cursor is server-only (no client grant, deny-all RLS).
        existing = self._get(
            f"{_REST}/assignment_cursor",
            {"pool_key": f"eq.{pool_key}", "select": "pool_key"},
        )
        if existing:
            self._patch(
                f"{_REST}/assignment_cursor",
                {"pool_key": f"eq.{pool_key}"},
                {"next_index": next_index, "updated_at": "now()"},
            )
        else:
            self._post(
                f"{_REST}/assignment_cursor",
                {"pool_key": pool_key, "next_index": next_index},
            )

    def append_voucher_event(
        self,
        *,
        family_id: UUID,
        from_state: FundingState | None,
        to_state: FundingState,
        program: str,
        signal: str,
        student_id: UUID | None = None,
    ) -> None:
        # Append one row to the immutable voucher_event timeline (TODO.md R2): a
        # funding-state transition fact (from→to + the GT-controlled signal +
        # program), feeding the work-queue deadline ranking + §10 observability.
        # service_role POST (BYPASSRLS, server-only — INV-5 / D-RLS-4). The
        # deterministic core owns the transition (INV-2); this only LOGS the fact
        # after it happened. Append-only: a POST, never an UPDATE/DELETE. Enums
        # serialize to their .value; `from_state` may be None (an origin event).
        self._post(
            f"{_REST}/voucher_event",
            {
                "voucher_event_id": str(uuid4()),
                "family_id": str(family_id),
                "student_id": str(student_id) if student_id is not None else None,
                "from_state": self._json_value(from_state) if from_state is not None else None,
                "to_state": self._json_value(to_state),
                "program": program,
                "signal": signal,
            },
        )

    # ------------------------------------------------------- drop-off views
    def drop_off_for_family(self, family_id: UUID) -> DropOffPoint | None:
        """The family's last apply-flow position before exit (A-24 deal-view view).

        Step → form → field granularity (0006). Prefers the latest explicit
        ``last_step_before_exit`` event; absent that, the most-recent event of any
        kind. "Latest" orders by ``nav_seq`` desc (the monotonic per-family
        navigation index) then ``occurred_at`` desc — so a same-timestamp tie is
        broken by who came later in the navigation order. Returns ``step`` /
        ``form_key`` / ``field_key``: metadata only, ``form_key`` a structural
        sub-form id, never a value/content or child key (INV-1/INV-6). ``None``
        when the family emitted no ``apply_events``.
        """
        rows = self._get(
            f"{_REST}/apply_events",
            {
                "select": "step,form_key,field_key,event_type,occurred_at,nav_seq",
                "family_id": f"eq.{family_id}",
                # nav_seq desc then occurred_at desc — the "latest navigation
                # position" order (the contract's tie-break, nulls sort last).
                "order": "nav_seq.desc.nullslast,occurred_at.desc",
            },
        )
        if not rows:
            return None
        # Defensive re-sort: do not trust the server's order to be the contract's
        # exact key — apply it here so the tie-break is deterministic in-process.
        ordered = sorted(rows, key=self._nav_order_key, reverse=True)
        exit_rows = [r for r in ordered if r.get("event_type") == "last_step_before_exit"]
        chosen = exit_rows[0] if exit_rows else ordered[0]
        return DropOffPoint(
            family_id=family_id,
            step=str(chosen.get("step", "")),
            form_key=chosen.get("form_key"),
            field_key=chosen.get("field_key"),
            event_type=str(chosen.get("event_type", "")),
            occurred_at=chosen.get("occurred_at"),
        )

    @staticmethod
    def _nav_order_key(row: dict[str, Any]) -> tuple[int, str]:
        """Sort key for "latest navigation position": (nav_seq, occurred_at).

        ``nav_seq`` is the primary key (a missing one sorts earliest as -1);
        ``occurred_at`` breaks a nav_seq tie. Used with ``reverse=True`` so the
        latest position is first.
        """
        nav_seq = row.get("nav_seq")
        nav = int(nav_seq) if nav_seq is not None else -1
        return (nav, str(row.get("occurred_at") or ""))

    def drop_off_heatmap(self) -> list[DropOffBucket]:
        """Cohort drop-off heatmap — exit counts grouped by step+form+field (A-24).

        Counts ``last_step_before_exit`` events per (``step``, ``form_key``,
        ``field_key``): the cells where families freeze, aggregate only (no
        family/child identity). Ordered by descending count then ``step`` /
        ``form_key`` / ``field_key`` for a stable, scannable surface.
        """
        rows = self._get(
            f"{_REST}/apply_events",
            {
                "select": "step,form_key,field_key",
                "event_type": "eq.last_step_before_exit",
            },
        )
        tally: Counter[tuple[str, str | None, str | None]] = Counter()
        for row in rows:
            step = str(row.get("step", ""))
            form_key = row.get("form_key")
            field_key = row.get("field_key")
            tally[(step, form_key, field_key)] += 1
        buckets = [
            DropOffBucket(step=step, form_key=form_key, field_key=field_key, count=count)
            for (step, form_key, field_key), count in tally.items()
        ]
        buckets.sort(key=lambda b: (-b.count, b.step, b.form_key or "", b.field_key or ""))
        return buckets


def build_supabase_repository(
    params: Params, *, program: Program | None = None
) -> SupabaseFamilyRepository | None:
    """Construct the Supabase repo from the environment, or ``None`` when unbound.

    Reads ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` directly from the env
    at the composition root (the same `os.environ` read the existing
    ``COCKPIT_SCENARIO`` toggle does). Returns ``None`` when ``SUPABASE_URL`` is
    absent so the caller falls back to the in-memory store (A-3). A placeholder
    angle-bracket value (the ``.env.example`` sentinel) counts as unset — same
    posture as the Settings secret readers.

    ``program`` is the A1 active program (resolved fail-closed from ``GT_PROGRAM_ID``
    at the composition root). When set it bounds every program-scoped read/write to
    that program (the app-layer isolation over the service_role read path, PLAN_v2
    §A1 / A-38). ``None`` leaves the repo program-agnostic.

    A-38: when ``SUPABASE_APP_RUNTIME_READS`` is truthy AND the anon key + JWT secret
    are configured AND a program is active, program-scoped READS authenticate as the
    non-`BYPASSRLS` ``app_runtime`` role (RLS-enforced program isolation, 0031), not
    service_role. Any missing piece falls back to the unchanged service_role read
    path (fail-closed; the swap never silently half-engages).
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or url.startswith("<"):
        return None
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key or key.startswith("<"):
        return None
    anon = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    anon_key = anon if anon and not anon.startswith("<") else None
    secret = (os.environ.get("SUPABASE_JWT_SECRET") or "").strip()
    jwt_secret = secret if secret and not secret.startswith("<") else None
    app_runtime_reads = (os.environ.get("SUPABASE_APP_RUNTIME_READS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return SupabaseFamilyRepository(
        base_url=url,
        service_role_key=key,
        params=params,
        program=program,
        anon_key=anon_key,
        jwt_secret=jwt_secret,
        app_runtime_reads=app_runtime_reads,
    )
