"""MCP stdio server — a THIN wrapper exposing the coworker core as MCP tools (M6).

This is GLUE: it imports the tested :mod:`app.coworker.core` and wires its
read-proxy + the one gated write to MCP tools so Claude Desktop can drive
``/check-in`` and the ``draft→confirm`` loop (MULTI_AGENT_COCKPIT §10.3/§10.4).

**Import-guarded on purpose.** The ``mcp`` SDK is NOT a project dependency (see
ASSUMPTIONS A-33) — the cockpit gate must NOT require the MCP transport to pass
(the core logic is what the gate tests; the wrapper is glue). So this module
import-guards the SDK: ``import app.coworker.mcp_server`` never raises, and
:func:`build_server` raises a clear, actionable error only if you actually try to
run the transport without the SDK installed.

The coworker authenticates AS the closer via the demo-principal headers
(``X-Demo-Role: agent`` + ``X-Demo-Agent-Id: <closer id>``) — see
:func:`app.coworker.core.closer_headers`. Every tool here is a READ except
``confirm``, which routes through the SOLE write path
(``POST /proposals/{id}/decision``) — the coworker NEVER writes HubSpot directly
(INV-2/INV-9).

Run (once the ``mcp`` SDK is installed + the cockpit API is up)::

    COCKPIT_BASE_URL=http://localhost:8000 \
    COWORKER_AGENT_ID=a0000000-0000-4000-8000-000000000001 \
    python -m app.coworker.mcp_server
"""

from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING, Any

from app.coworker import core

if TYPE_CHECKING:
    import httpx

# The seeded closer (Agent #1, Riley Carter) — the default principal the coworker
# authenticates as (MULTI_AGENT_COCKPIT §10.3). Overridable via COWORKER_AGENT_ID.
DEFAULT_CLOSER_AGENT_ID = "a0000000-0000-4000-8000-000000000001"
DEFAULT_BASE_URL = "http://localhost:8000"

# Import-guard the MCP SDK: absent ⇒ the module still imports (the gate tests the
# core, not the transport). Only build_server() raises if you run the transport.
try:  # pragma: no cover - exercised only when the optional SDK is installed
    from mcp.server.fastmcp import FastMCP

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - the default in this env (no mcp dep)
    FastMCP = None
    _MCP_AVAILABLE = False


def _agent_id() -> str:
    """The closer agent id the coworker authenticates as (env override + default)."""
    return os.environ.get("COWORKER_AGENT_ID", DEFAULT_CLOSER_AGENT_ID)


def _base_url() -> str:
    """The cockpit API base URL the read-proxy points at."""
    return os.environ.get("COCKPIT_BASE_URL", DEFAULT_BASE_URL)


def _http_client() -> httpx.Client:
    """A live httpx client against the cockpit API — the prod HttpClient seam impl.

    httpx is already a runtime dep; its ``Client`` satisfies :class:`core.HttpClient`
    (same ``.get``/``.post`` → response-with-``.json()`` shape the TestClient has),
    so the SAME core runs in the gate (TestClient) and in prod (httpx) unchanged. It
    is a context manager (``with``), so the per-tool ``with _http_client()`` closes
    the connection after each call.
    """
    import httpx

    return httpx.Client(base_url=_base_url(), timeout=30.0)


def build_server() -> Any:
    """Build the MCP server with the coworker tools wired to the core (glue).

    Raises a clear, actionable error if the optional ``mcp`` SDK is not installed —
    the gate never reaches this path (it tests the core), so importing this module
    is always safe; only running the transport needs the SDK (A-33).
    """
    if not _MCP_AVAILABLE:  # pragma: no cover - only when the SDK is absent
        raise RuntimeError(
            "The 'mcp' SDK is not installed. The coworker CORE is dependency-free "
            "and fully tested; the MCP transport is optional glue. Install it "
            "(`uv add mcp` / `pip install mcp`) to run the stdio server, or drive "
            "the core directly. See coworker/README.md + ASSUMPTIONS A-33."
        )

    server = FastMCP("gt-coworker")

    @server.tool()
    def check_in() -> dict[str, Any]:  # pragma: no cover - transport glue
        """Run /check-in: the closer's four blocks (who-to-contact / pending-notes /
        hygiene-gaps / voucher-clocks), scoped to the closer's own book."""
        with _http_client() as client:
            briefing = core.check_in(client, _agent_id())
        return dataclasses.asdict(briefing)

    @server.tool()
    def draft_followup(
        family_id: str, action: str = "email"
    ) -> dict[str, Any]:  # pragma: no cover - transport glue
        """Draft an eval-gated follow-up for a family. A BLOCKED draft is surfaced
        VERBATIM (failed_rules, no message) — never softened or retried (INV-4)."""
        with _http_client() as client:
            outcome = core.draft(client, _agent_id(), family_id, action=action)
        return dataclasses.asdict(outcome)

    @server.tool()
    def confirm(
        proposal_id: str, decision: str = "approve"
    ) -> dict[str, Any]:  # pragma: no cover - transport glue
        """Confirm a drafted proposal through the SOLE write path
        (POST /proposals/{id}/decision). The coworker never writes HubSpot directly."""
        with _http_client() as client:
            result = core.confirm(client, _agent_id(), proposal_id, decision=decision)
        return dataclasses.asdict(result)

    return server


def main() -> None:  # pragma: no cover - transport entrypoint
    """Run the MCP stdio server (requires the optional ``mcp`` SDK)."""
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
