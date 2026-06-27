"""M7 security/observability — the edge detection middleware + the two panels.

MULTI_AGENT_COCKPIT.md §3/§7. This module is the API surface for the SecurityTab:

  * :class:`SecurityEdgeMiddleware` — a FastAPI/Starlette edge middleware that
    OBSERVES each request/response and RECORDS a ``security_event`` for a
    suspicious signal (a 401/403 burst, an oversized result set, a ``user_id``
    reassign attempt, an anon hit on an admin/service route). DETECTION, NOT inline
    blocking (honest scope, §7): it never rejects a request — RLS + the app-layer
    owner clamp are the inline boundary; this only feeds the suspicious-activity
    feed. Cheap + non-blocking. Every threshold is a param (INV-11), never a code
    literal.
  * ``GET /security/posture`` (Panel A) — the LIVE RLS posture: it runs the SAME
    static invariants ``test_migrations_rls`` runs (count invariants + the
    null-guard regex over the migration DDL) at runtime, so a table that loses its
    FORCE line flips it RED.
  * ``GET /security/events`` (Panel B) — the append-only suspicious-activity feed,
    a SIMULATED, clearly-labeled stream in v1 (INV-9).
  * ``POST /security/events/{id}/acknowledge`` — the §7 acknowledge/report action.

This is the composition root for the security surface; the populate path is the
app-layer ``service_role`` feed (``app.observability.security_log``), NOT a public
definer-rights helper (D-RLS-7). It makes no live call and writes no domain state.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.api.deps import actor_principal, get_security_event_log
from app.core.params import Params
from app.core.security_posture import PostureCheck, evaluate_posture
from app.core.settings import Settings
from app.observability.security_log import (
    ActorKind,
    SecurityEvent,
    SecurityEventLog,
    SecuritySignal,
    Severity,
    make_event,
)

router = APIRouter(tags=["security"])

SecurityLogDep = Annotated[SecurityEventLog, Depends(get_security_event_log)]

# Route-prefix tokens that name an admin/service-scoped surface — an ANON request
# to one of these is a §7 API5:2023 (Broken Function Level Auth) signal. Named
# constants (the wire spelling of route prefixes), not tunables (INV-11).
_ADMIN_SURFACE_TOKENS = ("/admin", "/service", "/internal")


class SecurityEdgeMiddleware(BaseHTTPMiddleware):
    """Edge detection middleware (§7 Panel B feed) — observe, classify, RECORD.

    For each request it derives the actor kind from the verified
    ``Authorization: Bearer`` JWT (B1: a token that verifies to a trusted principal
    ⇒ authenticated, else anon — the spoofable client-supplied role header was
    DELETED, S1), runs the response and the request body past the §7 signal classifiers, and
    records a ``security_event`` for any that fire. It NEVER blocks (detection only).

    Thresholds (``oversized_result_rows``, ``auth_failure_burst``,
    ``auth_failure_window_seconds``) come from ``params.security`` (INV-11). The
    auth-failure burst is counted per-actor over a rolling time window held in
    process memory (v1; A-3). ``settings`` carries the JWT-verifying secret; with no
    secret configured every token reads as ANON — detection-only and safe.
    """

    def __init__(
        self, app: ASGIApp, *, log: SecurityEventLog, params: Params, settings: Settings
    ) -> None:
        super().__init__(app)
        self._log = log
        self._params = params
        self._settings = settings
        # Per-actor rolling 401/403 timestamps for the burst detector (v1 in-memory).
        self._auth_failures: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        actor_kind, actor_ref = self._resolve_actor(request)
        path = request.url.path

        # Inspect a mutating body for a user_id-reassign attempt BEFORE dispatch
        # (the body stream is consumed once; Starlette caches it for the route).
        await self._maybe_record_user_id_reassign(request, actor_kind, actor_ref, path)

        response = await call_next(request)

        # A 401/403 ⇒ feed the per-actor burst detector + the anon-admin-route signal.
        if response.status_code in (401, 403):
            self._record_auth_signals(request, response, actor_kind, actor_ref, path)

        # An oversized list response ⇒ enumeration/scraping signal (API4:2023). The
        # BaseHTTPMiddleware response is a streaming response, so drain its body
        # iterator, classify the JSON, then hand back an equivalent response (the
        # body is consumed once — we must rebuild it to forward the bytes).
        return await self._classify_and_forward(response, actor_kind, actor_ref, path)

    # -- actor resolution -----------------------------------------------------

    def _resolve_actor(self, request: Request) -> tuple[ActorKind, str | None]:
        """Derive (actor_kind, actor_ref) from the verified Bearer JWT (B1; the S1 fix).

        Reuses :func:`actor_principal` (the non-raising sibling of the route
        dependency): a token that verifies to a trusted principal ⇒ AUTHENTICATED,
        with ``actor_ref`` the operator ``agent_id`` (else the auth ``user_id``); any
        failure (no/forged/expired token, no trusted role, no configured secret) ⇒
        ANON with no ref. Detection-only — it never blocks or raises.
        """
        principal = actor_principal(request.headers.get("Authorization"), self._settings)
        if principal is None:
            return ActorKind.ANON, None
        ref = principal.agent_id or principal.user_id
        return ActorKind.AUTHENTICATED, (str(ref) if ref is not None else None)

    # -- signal classifiers ---------------------------------------------------

    async def _maybe_record_user_id_reassign(
        self, request: Request, actor_kind: ActorKind, actor_ref: str | None, path: str
    ) -> None:
        """API3:2023 — a write body that sets `user_id` (a field the actor doesn't own)."""
        if request.method not in ("POST", "PUT", "PATCH"):
            return
        body = await request.body()
        if not body:
            return
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(payload, dict) and "user_id" in payload:
            self._log.record(
                make_event(
                    signal=SecuritySignal.USER_ID_REASSIGN_ATTEMPT,
                    actor_kind=actor_kind,
                    actor_ref=actor_ref,
                    surface=path,
                    severity=Severity.HIGH,
                    detail="Write body attempted to set user_id (property-level auth).",
                    simulated=False,
                )
            )

    def _record_auth_signals(
        self,
        request: Request,
        response: Response,
        actor_kind: ActorKind,
        actor_ref: str | None,
        path: str,
    ) -> None:
        """A07:2021 burst + API5:2023 anon-admin-route, both off a 401/403."""
        # API5:2023 — an ANON request to an admin/service-scoped surface.
        if actor_kind is ActorKind.ANON and any(tok in path for tok in _ADMIN_SURFACE_TOKENS):
            self._log.record(
                make_event(
                    signal=SecuritySignal.ANON_ADMIN_ROUTE,
                    actor_kind=actor_kind,
                    actor_ref=actor_ref,
                    surface=path,
                    severity=Severity.MEDIUM,
                    detail="Anonymous request hit an admin/service route (function-level auth).",
                    simulated=False,
                )
            )

        # A07:2021 — per-actor 401/403 burst over the rolling window.
        key = actor_ref or (request.client.host if request.client else "unknown")
        now = time.monotonic()
        window = self._params.security.auth_failure_window_seconds
        bucket = self._auth_failures[key]
        bucket.append(now)
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= self._params.security.auth_failure_burst:
            self._log.record(
                make_event(
                    signal=SecuritySignal.AUTH_FAILURE_BURST,
                    actor_kind=actor_kind,
                    actor_ref=actor_ref,
                    surface=path,
                    severity=Severity.MEDIUM,
                    detail=f"{len(bucket)} 401/403 responses within {window}s (brute force).",
                    simulated=False,
                )
            )
            bucket.clear()  # avoid re-firing every subsequent failure in the same window.

    async def _classify_and_forward(
        self, response: Response, actor_kind: ActorKind, actor_ref: str | None, path: str
    ) -> Response:
        """Drain the streaming response, classify it (API4:2023 oversized), re-emit it.

        ``BaseHTTPMiddleware`` hands back a streaming response whose body is a
        single-pass async iterator, so we buffer it once to inspect the JSON and
        then return a plain :class:`Response` carrying the same bytes/headers — the
        body is forwarded intact (DETECTION never alters the payload).
        """
        # Only JSON 200s with a list payload can be an oversized result; anything
        # else is forwarded unbuffered (cheap path).
        if response.status_code != 200 or "application/json" not in response.headers.get(
            "content-type", ""
        ):
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            payload = None
        if (
            isinstance(payload, list)
            and len(payload) >= self._params.security.oversized_result_rows
        ):
            self._log.record(
                make_event(
                    signal=SecuritySignal.OVERSIZED_RESULT,
                    actor_kind=actor_kind,
                    actor_ref=actor_ref,
                    surface=path,
                    severity=Severity.LOW,
                    detail=f"List response of {len(payload)} rows (enumeration/scraping).",
                    simulated=False,
                )
            )
        # Re-emit the buffered body with the original status + headers.
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


# ---------------------------------------------------------------------------
# Panel A — live RLS posture.
# ---------------------------------------------------------------------------


class PostureCheckView(BaseModel):
    """One named RLS invariant + its pass/fail + detail (Panel A row)."""

    name: str
    passed: bool
    detail: str


class PostureView(BaseModel):
    """The live RLS-posture verdict (Panel A).

    ``green`` is True only when every check passed — a regressed table flips it RED.
    """

    green: bool
    checks: list[PostureCheckView]


def _to_check_view(check: PostureCheck) -> PostureCheckView:
    return PostureCheckView(name=check.name, passed=check.passed, detail=check.detail)


@router.get("/security/posture", response_model=PostureView)
def get_security_posture() -> PostureView:
    """Panel A — the LIVE RLS posture (MULTI_AGENT_COCKPIT §7).

    Runs the SAME static invariants ``test_migrations_rls`` runs — the count
    invariants (CREATE TABLE == ENABLE == FORCE) + the per-policy ``auth.uid()``
    null-guard regex + the no-definer-rights check — over the committed migration
    DDL, at RUNTIME. Green when every public table is FORCE-RLS + null-guarded; a
    table that loses its FORCE line (or a policy that loses its guard) flips it RED,
    fail-closed. The build-time test and this panel read the same DDL, so they can
    never drift.
    """
    result = evaluate_posture()
    return PostureView(green=result.green, checks=[_to_check_view(c) for c in result.checks])


# ---------------------------------------------------------------------------
# Panel B — the append-only suspicious-activity feed.
# ---------------------------------------------------------------------------


class SecurityEventView(BaseModel):
    """One suspicious-activity feed row (Panel B), with its §7 OWASP mapping."""

    event_id: UUID
    occurred_at: str
    actor_kind: ActorKind
    actor_ref: str | None
    surface: str | None
    signal: SecuritySignal
    severity: Severity
    owasp: str
    detail: str | None
    simulated: bool
    acknowledged: bool


class SecurityEventsView(BaseModel):
    """The Panel B feed — the events + the v1 ``simulated`` label (INV-9)."""

    # INV-9: the v1 feed is a SIMULATED, clearly-labeled stream. True whenever ANY
    # live drain is absent (every event in v1 is simulated) — the panel renders the
    # 'simulated' badge like every other v1 adapter.
    simulated: bool
    events: list[SecurityEventView]


def _to_event_view(event: SecurityEvent) -> SecurityEventView:
    return SecurityEventView(
        event_id=event.event_id,
        occurred_at=event.occurred_at.isoformat(),
        actor_kind=event.actor_kind,
        actor_ref=event.actor_ref,
        surface=event.surface,
        signal=event.signal,
        severity=event.severity,
        owasp=event.owasp,
        detail=event.detail,
        simulated=event.simulated,
        acknowledged=event.acknowledged,
    )


@router.get("/security/events", response_model=SecurityEventsView)
def get_security_events(log: SecurityLogDep) -> SecurityEventsView:
    """Panel B — the append-only suspicious-activity feed (§7).

    Each row carries the §7 OWASP category id. In v1 the stream is SIMULATED and
    labeled (INV-9): ``simulated`` is True whenever every event is a simulated/
    recorded observation (no live log drain wired), so the panel badges it like
    every other v1 adapter.
    """
    events = log.list_events()
    all_simulated = all(e.simulated for e in events) if events else True
    return SecurityEventsView(
        simulated=all_simulated,
        events=[_to_event_view(e) for e in events],
    )


@router.post("/security/events/{event_id}/acknowledge", response_model=SecurityEventView)
def acknowledge_security_event(event_id: UUID, log: SecurityLogDep) -> SecurityEventView:
    """The §7 acknowledge/report action — flip a feed row to acknowledged.

    Append-style: the row's facts (signal, owasp, actor) never change; only the
    acknowledgement marker flips. An unknown id is a 404 (a clean miss).
    """
    acked = log.acknowledge(event_id)
    if acked is None:
        raise HTTPException(status_code=404, detail="unknown security event")
    return _to_event_view(acked)
