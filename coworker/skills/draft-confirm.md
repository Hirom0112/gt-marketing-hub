---
name: draft-confirm
description: Draft an eval-gated follow-up for a family, surface it (or its block) verbatim, and on the rep's confirmation write it through the one gated decision route. Use when the rep says "draft a follow-up", "write to this family", "send a nudge", "follow up with <family>".
---

# draft → confirm — the act loop

This is how the closer coworker sends a follow-up. It is a **two-step loop**: draft
first (eval-gated), then confirm only on the rep's explicit go-ahead. The write
**always** routes through the one gated decision route — the coworker **never writes
to HubSpot directly** (MULTI_AGENT_COCKPIT §2.5; INV-2/INV-9).

## Step 1 — draft

Call the `draft_followup` MCP tool with the `family_id` (and optionally `action`:
`email` | `nudge` | `faq`, default `email`). It calls the eval-gated
`POST /ai/enrollment/draft`. Two outcomes:

- **`surfaced: true`** — the draft passed the grounding/safety eval. Show the rep the
  proposed `message` and the `proposal_id`. Ask: *"Send this?"* Do not send yet.
- **`surfaced: false`** — the draft was **blocked** (failed the eval, e.g. an
  unverifiable "4X speed" claim, a non-COPPA-safe line, or an off-brand message; or a
  red eval suite disabled the action). The response carries `failed_rules` and **no
  message body**.

### When a draft is blocked, surface it VERBATIM (INV-4)

This is non-negotiable. When `surfaced: false`:

- **Show the `failed_rules` exactly as returned.** Do not paraphrase them away.
- **Do NOT rewrite, soften, or "fix" the blocked message** to get it to pass.
- **Do NOT re-run the draft hoping for a different result.** The gate blocks; it does
  not negotiate.
- **Do NOT confirm a blocked draft.** There is nothing to send.

Tell the rep plainly: *"That draft was blocked by the safety/grounding gate
(`<failed_rules>`). I won't rewrite it to slip past the gate. Want to try a different
angle, or skip this family?"*

## Step 2 — confirm

Only after the rep confirms a **surfaced** draft, call the `confirm` MCP tool with the
`proposal_id` (and `decision: approve`, the default; `discard` to drop it). This calls
`POST /proposals/{proposal_id}/decision` — the **sole write path**. It:

- logs the human decision to the audit spine (timestamped, NFR-6),
- records the (simulated v1) send through the deterministic core's CRM adapter, and
- returns the recorded `note_id` + the recomputed seam status.

Report back to the rep: the decision was recorded, the note id, and (from the audit)
the timestamp. That's the whole loop: `/check-in` → draft → confirm → it writes
through the gated route → syncs to the CRM.

## Guardrails

- **One write path.** The only write you ever make is `confirm` (the decision route).
  You have no HubSpot/CRM tool and must never claim to have written to HubSpot
  directly.
- **Fail closed.** A blocked draft stays blocked. Never present a softened version.
- **Confirm is the human gate.** Never auto-confirm; always wait for the rep's
  explicit go-ahead on a surfaced draft.
