"""The coworker proxy core — owner-scoped reads + the ONE gated write (M6).

Pure-ish proxy-client logic over an HTTP client seam (:class:`HttpClient`), scoped
by the closer's demo headers (MULTI_AGENT_COCKPIT §10.3). It is a SECOND client of
the same owner-scoped cockpit reads the UI uses — so the M1 IDOR clamp
(``resolve_owner_scope``) holds through the coworker unchanged; this module adds no
scoping of its own and never touches ``service_role`` (INV-5).

The headline invariant (MULTI_AGENT_COCKPIT §2.5; INV-2/INV-9): the coworker
performs **NO direct HubSpot write**. The ONLY mutating call this module makes is
``POST /proposals/{id}/decision`` (the sole audit-spine write path). It imports no
HubSpot/CRM client/adapter — :func:`confirm` is the single write seam.

The four ``/check-in`` blocks (MULTI_AGENT_COCKPIT §10.4) are composed from the
owner-scoped reads:

1. **who-to-contact ranked** — from ``GET /work-queue?owner=me`` (the closer's own
   ranked recovery queue; ``owner=me`` resolves to self via the demo principal, and
   the agent clamp ignores it regardless).
2. **pending-notes** — the latest manual/auto note per top family
   (``GET /families/{id}/notes``) — what was last said, so the rep has context.
3. **hygiene-gaps** — derived, deterministic flags over the reads: an unsynced CRM
   seam (``GET /seam``) and a never-contacted family (``contact_status`` on the
   queue row). NOT an LLM judgment — a plain projection.
4. **voucher-clocks** — from ``GET /families/{id}/funding`` (voucher standing): the
   ``due_by`` / ``days_remaining`` / ``at_risk`` deadline per top family.

The HTTP client seam is intentionally minimal so BOTH a live ``httpx.Client`` (prod)
and FastAPI's ``TestClient`` (the gate) satisfy it without an adapter — the core is
tested against the real app in-process, no live server required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# How many ranked queue rows the briefing surfaces, and how deep the per-family
# enrichments (pending-notes / voucher-clocks) go. A briefing presentation bound
# (not a decision threshold), so it is a justified named constant here rather than
# a params tunable — no AI action is gated on it (CLAUDE §1, INV-11 spirit).
CHECK_IN_TOP_N = 10

# The owner sentinel the coworker passes — ``owner=me`` reads as "my own book". The
# server clamps an operator to self regardless (the IDOR defense), so this is belt-
# and-suspenders intent, not the security boundary.
OWNER_ME = "me"


@runtime_checkable
class HttpResponse(Protocol):
    """The minimal response shape both httpx and the TestClient return."""

    status_code: int

    def json(self) -> Any: ...


@runtime_checkable
class HttpClient(Protocol):
    """The HTTP client seam — satisfied by ``httpx.Client`` AND FastAPI's TestClient.

    Only the two verbs the coworker uses are required. The coworker makes exactly
    one kind of mutating call (a POST to the decision route); every other call is a
    read GET. No HubSpot/CRM client is referenced anywhere in this module.
    """

    def get(self, url: str, *, headers: dict[str, str] | None = ...) -> HttpResponse: ...

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = ...,
        headers: dict[str, str] | None = ...,
    ) -> HttpResponse: ...


def closer_headers(token: str) -> dict[str, str]:
    """The verified-identity header that authenticates the coworker AS the closer (§10.3).

    ``Authorization: Bearer <jwt>`` — a signed Supabase-shaped JWT for the closer's
    operator role/agent (B1: the verified successor to the deleted, spoofable
    client-supplied role header, S1). The caller mints the token (the MCP server signs it
    with the configured ``SUPABASE_JWT_SECRET``; the gate signs it with the test
    secret), so the coworker core stays free of any signing secret — it only
    FORWARDS the bearer token. So ``/check-in`` returns the closer's queue only (the
    server's owner clamp does the scoping); this is the ONLY scoping the coworker
    applies.
    """
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Briefing models — the four /check-in blocks (read-only projections).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WhoToContact:
    """One ranked who-to-contact row (block 1) — projected off a work-queue row."""

    family_id: str
    display_name: str
    current_stage: str
    score: float
    recoverable_now: float
    contact_status: str
    last_contact_at: str | None


@dataclass(frozen=True)
class PendingNote:
    """The latest note on a top family (block 2) — context for the next touch."""

    family_id: str
    display_name: str
    body: str
    author: str
    created_at: str


@dataclass(frozen=True)
class HygieneGap:
    """One derived hygiene flag (block 3) — a deterministic gap, never an LLM call."""

    family_id: str
    display_name: str
    kind: str  # "seam_unsynced" | "never_contacted"
    detail: str


@dataclass(frozen=True)
class VoucherClock:
    """One voucher deadline (block 4) — from the family's funding standing."""

    family_id: str
    display_name: str
    program: str
    next_action: str
    due_by: str | None
    days_remaining: int | None
    at_risk: bool


@dataclass(frozen=True)
class CheckInBriefing:
    """The composed ``/check-in`` briefing — the four blocks, closer-scoped (§10.4)."""

    agent_id: str
    who_to_contact: list[WhoToContact] = field(default_factory=list)
    pending_notes: list[PendingNote] = field(default_factory=list)
    hygiene_gaps: list[HygieneGap] = field(default_factory=list)
    voucher_clocks: list[VoucherClock] = field(default_factory=list)


def _require_ok(resp: HttpResponse, what: str) -> Any:
    """Return the JSON body or raise a clear error — the coworker never guesses."""
    if resp.status_code != 200:
        raise CoworkerReadError(f"{what} failed: HTTP {resp.status_code}")
    return resp.json()


class CoworkerReadError(RuntimeError):
    """A read-proxy call to the cockpit returned a non-200 — surfaced, not swallowed."""


class CoworkerWriteError(RuntimeError):
    """The sole write path (the decision route) returned a non-200 — surfaced."""


def check_in(
    client: HttpClient,
    agent_id: str,
    *,
    token: str,
    top_n: int = CHECK_IN_TOP_N,
) -> CheckInBriefing:
    """Compose the four ``/check-in`` blocks from the owner-scoped reads (§10.4).

    Authenticates AS the closer with the signed ``token`` (``closer_headers``) and
    reads only the closer's own book — the server's owner clamp
    (``resolve_owner_scope``) does the scoping, so a foreign rep's families are NEVER
    returned through the coworker (the IDOR defense holds; INV-5). ``agent_id`` is the
    closer's id for the briefing label / ``owner=me`` intent; the verified token is
    what the server actually scopes on. READ-ONLY: no POST and no write.

    The four blocks:

    1. who-to-contact ranked — ``GET /work-queue?owner=me`` (already ranked by
       ``recoverable_now`` desc), capped to ``top_n``.
    2. pending-notes — the most-recent note per top family (``GET
       /families/{id}/notes``), so the rep knows what was last said.
    3. hygiene-gaps — derived flags: a never-contacted family (``contact_status ==
       "overdue"``/no ``last_contact_at``) and an unsynced CRM seam (``GET /seam``).
       Deterministic projection, not an LLM judgment.
    4. voucher-clocks — the funding standing per top family (``GET
       /families/{id}/funding``): the ``due_by`` / ``days_remaining`` / ``at_risk``.
    """
    headers = closer_headers(token)

    # Block 1: the ranked queue, scoped to the closer's own book. owner=me is intent;
    # the server clamps an agent to self regardless (the IDOR defense). scope=active
    # is the default (the live recovery queue) — the rep's actual to-do list.
    queue = _require_ok(
        client.get(f"/work-queue?owner={OWNER_ME}", headers=headers),
        "/work-queue",
    )
    top = queue[:top_n]
    who_to_contact = [
        WhoToContact(
            family_id=row["family_id"],
            display_name=row["display_name"],
            current_stage=row["current_stage"],
            score=row["score"],
            recoverable_now=row["recoverable_now"],
            contact_status=row["contact_status"],
            last_contact_at=row.get("last_contact_at"),
        )
        for row in top
    ]

    # Block 3a: the unsynced CRM seam — same owner scoping. A family on this list has
    # local truth the CRM has not caught up to (a hygiene gap to clear).
    seam = _require_ok(
        client.get(f"/seam?owner={OWNER_ME}", headers=headers),
        "/seam",
    )
    unsynced_ids = {r["family_id"] for r in seam}

    # Per-family enrichments for the top rows (blocks 2, 3b, 4). Bounded by top_n so
    # the briefing never fans out across the whole book.
    pending_notes: list[PendingNote] = []
    hygiene_gaps: list[HygieneGap] = []
    voucher_clocks: list[VoucherClock] = []
    by_name = {row["family_id"]: row["display_name"] for row in top}

    for row in top:
        fid = row["family_id"]
        name = row["display_name"]

        # Block 2: the latest note (what was last said). The notes list is
        # chronological; the last entry is the most recent.
        notes = _require_ok(
            client.get(f"/families/{fid}/notes", headers=headers),
            f"/families/{fid}/notes",
        )
        if notes:
            latest = notes[-1]
            pending_notes.append(
                PendingNote(
                    family_id=fid,
                    display_name=name,
                    body=latest["body"],
                    author=latest["author"],
                    created_at=latest["created_at"],
                )
            )

        # Block 3b: a never-contacted hygiene gap — no recorded outbound yet.
        if row.get("last_contact_at") is None:
            hygiene_gaps.append(
                HygieneGap(
                    family_id=fid,
                    display_name=name,
                    kind="never_contacted",
                    detail=f"No recorded outbound (contact_status={row['contact_status']}).",
                )
            )

        # Block 4: the voucher clock — the funding standing's deadline.
        funding = _require_ok(
            client.get(f"/families/{fid}/funding", headers=headers),
            f"/families/{fid}/funding",
        )
        voucher_clocks.append(
            VoucherClock(
                family_id=fid,
                display_name=name,
                program=funding["program"],
                next_action=funding["next_action"],
                due_by=funding.get("due_by"),
                days_remaining=funding.get("days_remaining"),
                at_risk=funding["at_risk"],
            )
        )

    # Block 3a: append the seam-unsynced gaps (only for families in the top slice so
    # the briefing stays focused; full-book seam is its own surface).
    for fid in unsynced_ids:
        if fid in by_name:
            hygiene_gaps.append(
                HygieneGap(
                    family_id=fid,
                    display_name=by_name[fid],
                    kind="seam_unsynced",
                    detail="CRM seam not synced — local truth ahead of the CRM mirror.",
                )
            )

    return CheckInBriefing(
        agent_id=agent_id,
        who_to_contact=who_to_contact,
        pending_notes=pending_notes,
        hygiene_gaps=hygiene_gaps,
        voucher_clocks=voucher_clocks,
    )


@dataclass(frozen=True)
class DraftOutcome:
    """The eval-gated draft outcome, surfaced to the rep VERBATIM (INV-4).

    When ``surfaced`` is False (a blocked / degraded / eval-red draft) the coworker
    surfaces ``failed_rules`` VERBATIM and carries NO ``message`` body — it NEVER
    softens, rewrites, or retries a blocked draft to pass (INV-4). ``proposal_id``
    is always present so the rep can act through the decision route on a passing
    draft; a blocked draft is shown as-blocked and NOT confirmed.
    """

    proposal_id: str
    surfaced: bool
    degraded: bool
    failed_rules: list[str]
    message: str | None  # the proposal body — present ONLY when surfaced is True
    proposal: dict[str, Any] | None  # the full surfaced proposal, or None when blocked


def draft(
    client: HttpClient,
    agent_id: str,
    family_id: str,
    *,
    token: str,
    action: str = "email",
) -> DraftOutcome:
    """Draft an enrollment follow-up via the eval-gated route — surface it VERBATIM.

    Calls ``POST /ai/enrollment/draft`` (the eval-gated path; INV-3) as the closer.
    The coworker reflects the response EXACTLY (INV-4):

    * ``surfaced=True`` ⇒ carry the proposal body as ``message`` so the rep can
      confirm it through the decision route.
    * ``surfaced=False`` ⇒ a blocked/degraded/eval-red draft: carry the
      ``failed_rules`` VERBATIM, ``message=None``, ``proposal=None``. The coworker
      does NOT soften the copy, does NOT rewrite it to pass, and does NOT auto-retry
      — a blocked draft is shown as blocked (INV-4 fail-closed). It also does NOT
      call the decision route on a blocked draft (no write off a block).

    This function is READ-shaped (it produces a proposal but writes nothing to
    state): the proposal is logged server-side; the WRITE happens only when the rep
    confirms via :func:`confirm`.
    """
    headers = closer_headers(token)
    resp = client.post(
        "/ai/enrollment/draft",
        json={"family_id": family_id, "action": action},
        headers=headers,
    )
    body = _require_ok(resp, "/ai/enrollment/draft")
    surfaced = bool(body["surfaced"])
    proposal = body.get("proposal") if surfaced else None
    # Surface the body ONLY on pass — a blocked draft carries no usable message, and
    # the coworker never fabricates one (INV-4). failed_rules is passed through
    # VERBATIM, exactly as the gate reported it.
    message = proposal.get("body") if (surfaced and isinstance(proposal, dict)) else None
    return DraftOutcome(
        proposal_id=body["proposal_id"],
        surfaced=surfaced,
        degraded=bool(body["degraded"]),
        failed_rules=list(body.get("failed_rules", [])),
        message=message,
        proposal=proposal if surfaced else None,
    )


@dataclass(frozen=True)
class ConfirmResult:
    """The result of confirming a proposal through the SOLE write path (§2.5)."""

    proposal_id: str
    action: str
    send_simulated: bool
    note_id: str | None
    seam_status: str | None


def confirm(
    client: HttpClient,
    agent_id: str,
    proposal_id: str,
    *,
    token: str,
    decision: str = "approve",
) -> ConfirmResult:
    """Confirm a drafted proposal — the SOLE write path (``POST .../decision``).

    This is the ONLY mutating call the coworker makes (MULTI_AGENT_COCKPIT §2.5;
    INV-2/INV-9). It routes through ``POST /proposals/{id}/decision`` — the gated
    decision route that logs the human verdict to the audit spine (NFR-6) and, on
    approve, records the SIMULATED send through the deterministic core's CRM
    adapter. The coworker NEVER writes to HubSpot directly; there is no HubSpot/CRM
    client imported in this module — the decision route is the single seam.

    Returns the recorded ``note_id`` (the audit/send handle) + the recomputed seam
    status; the caller reports the timestamp via the audit (``GET
    /proposals/{id}``), keeping this function's surface to the one write call.
    """
    headers = closer_headers(token)
    resp = client.post(
        f"/proposals/{proposal_id}/decision",
        json={"action": decision},
        headers=headers,
    )
    if resp.status_code != 200:
        raise CoworkerWriteError(
            f"/proposals/{proposal_id}/decision failed: HTTP {resp.status_code}"
        )
    body = resp.json()
    return ConfirmResult(
        proposal_id=body["proposal_id"],
        action=body["action"],
        send_simulated=bool(body.get("send_simulated", False)),
        note_id=body.get("note_id"),
        seam_status=body.get("seam_status"),
    )
