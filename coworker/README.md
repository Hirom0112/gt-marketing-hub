# GT Coworker — the rep-facing Claude closer (M6)

The **coworker** lets the founder connect **Claude Desktop as the closer** (Agent #1)
and work the cockpit conversationally: run `/check-in`, then a `draft → confirm`
loop. It is a **read-proxy** of the owner-scoped cockpit reads plus the **one gated
write path**.

> **Defining invariant (MULTI_AGENT_COCKPIT §2.5; CLAUDE INV-2/INV-9):** the
> coworker performs **NO direct HubSpot write**. Every write — coworker or UI —
> routes through the single gated decision route
> `POST /proposals/{proposal_id}/decision`, timestamped to the audit spine (NFR-6).
> The coworker imports no HubSpot/CRM client; the decision route is its only write
> seam.

## Architecture

```
Claude Desktop ──(MCP stdio)──▶ app.coworker.mcp_server  (thin glue, import-guarded)
                                        │
                                        ▼
                                app.coworker.core         (the TESTED proxy core)
                                   check_in / draft / confirm
                                        │  httpx.Client (prod) / TestClient (gate)
                                        ▼
                                GT Cockpit API  (FastAPI, owner-scoped reads
                                                 + the one gated decision route)
```

- **`app/coworker/core.py`** — the dependency-free, fully gate-tested proxy core. It
  talks to the cockpit over a minimal `HttpClient` seam that **both** `httpx.Client`
  (prod) and FastAPI's `TestClient` (the gate) satisfy — so the same code path the
  MCP transport runs is exercised in the test suite, no live server required.
- **`app/coworker/mcp_server.py`** — a thin stdio wrapper exposing the core as MCP
  tools. **Import-guarded:** the `mcp` SDK is *not* a project dependency (see
  ASSUMPTIONS A-33), so importing this module never fails; only running the
  transport needs the SDK.

## The closer principal (MULTI_AGENT_COCKPIT §10.3)

The coworker authenticates **as the closer** using the demo-principal headers — the
same owner-scoping the cockpit UI uses. The MCP connection is just a second client
of the owner-scoped reads, so `/check-in` returns **the closer's queue only**.

| Header | Value |
| --- | --- |
| `X-Demo-Role` | `agent` |
| `X-Demo-Agent-Id` | `a0000000-0000-4000-8000-000000000001` (Riley Carter, the closer) |

These are set automatically by `app.coworker.core.closer_headers(agent_id)`; the
server's owner clamp (`resolve_owner_scope`) enforces self-scoping regardless — a
foreign rep's families can never leak through the coworker (the IDOR defense, INV-5).

## Running the MCP server

The core is dependency-free; the MCP **transport** is optional glue. To run it:

```bash
# 1. Install the optional MCP SDK (NOT a project dependency by default — A-33).
uv add mcp          # or: pip install mcp

# 2. Start the cockpit API (the coworker is a client of it).
uv run uvicorn app.main:app --port 8000

# 3. Run the coworker MCP stdio server, pointed at the cockpit + the closer.
COCKPIT_BASE_URL=http://localhost:8000 \
COWORKER_AGENT_ID=a0000000-0000-4000-8000-000000000001 \
python -m app.coworker.mcp_server
```

### Claude Desktop config (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "gt-coworker": {
      "command": "python",
      "args": ["-m", "app.coworker.mcp_server"],
      "env": {
        "COCKPIT_BASE_URL": "http://localhost:8000",
        "COWORKER_AGENT_ID": "a0000000-0000-4000-8000-000000000001"
      }
    }
  }
}
```

## The MCP tools

| Tool | Routes hit | Writes? |
| --- | --- | --- |
| `check_in` | `GET /work-queue?owner=me`, `GET /seam`, `GET /families/{id}/notes`, `GET /families/{id}/funding` | **No** (read-only) |
| `draft_followup(family_id, action)` | `POST /ai/enrollment/draft` (eval-gated) | No state write (logs a proposal) |
| `confirm(proposal_id, decision)` | `POST /proposals/{id}/decision` | **Yes — the SOLE write path** |

A **blocked draft** (`surfaced=false`) is surfaced **verbatim** — its `failed_rules`
are passed through unchanged, with no message body. The coworker never softens,
rewrites, or retries a blocked draft (INV-4 fail-closed), and never confirms one.

See the per-flow skills: [`skills/check-in.md`](skills/check-in.md),
[`skills/draft-confirm.md`](skills/draft-confirm.md).
