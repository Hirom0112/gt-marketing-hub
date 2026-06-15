// Contact-recency color system (S9 Wave 4; ANALYSIS/enrollment-gap-analysis.md
// item 5). The backend derives a `contact_status` per family (fresh | overdue |
// followed_up | closed) — A-14, composed in the api layer from the audit log.
// This module is the SINGLE frontend home that maps that status onto a visual
// tone + the `--recency-*` tokens in theme.css. No component picks colors
// itself; they all resolve through here (visual INV-11), exactly as the UI
// primitives resolve through `ui/tokens.ts`.

import type { Tone } from '../ui';

// The four contact-recency statuses the backend emits (core ContactStatus).
export type ContactStatus = 'fresh' | 'overdue' | 'followed_up' | 'closed';

// The resolved visual triple for a status: a wash background, an AA-safe ink
// foreground, and a saturated solid for borders/dots — all theme.css tokens.
export interface RecencyVars {
  wash: string;
  ink: string;
  solid: string;
}

// Human-facing label + a longer title (tooltip) for each status.
interface RecencyMeta {
  label: string;
  title: string;
  tone: Tone;
}

const META: Record<ContactStatus, RecencyMeta> = {
  fresh: {
    label: 'Fresh',
    title: 'Fresh — new lead, still within the contact window',
    tone: 'neutral',
  },
  overdue: {
    label: 'Overdue',
    title: 'Overdue — aged past the follow-up threshold, not yet contacted',
    tone: 'signal',
  },
  followed_up: {
    label: 'Followed up',
    title: 'Followed up — contacted, deal not yet closed',
    tone: 'flow',
  },
  closed: {
    label: 'Closed',
    title: 'Closed — won and off the active worklist',
    tone: 'neutral',
  },
};

// True if `value` is one of the four known statuses (narrows `string` safely so
// no `any`/cast is needed at call sites that read an API string).
export function isContactStatus(value: string): value is ContactStatus {
  return value in META;
}

// The semantic Chip tone for a status — lets the recency chip reuse the frozen
// Chip primitive without inventing a fifth tone.
export function recencyTone(status: ContactStatus): Tone {
  return META[status].tone;
}

// The short status label (e.g. "Overdue").
export function recencyLabel(status: ContactStatus): string {
  return META[status].label;
}

// The longer tooltip title (e.g. for `title=` on a chip).
export function recencyTitle(status: ContactStatus): string {
  return META[status].title;
}

// The `--recency-*` token triple for a status. The token name embeds the exact
// status string, so this stays in lock-step with theme.css.
export function recencyVars(status: ContactStatus): RecencyVars {
  return {
    wash: `var(--recency-${status}-wash)`,
    ink: `var(--recency-${status}-ink)`,
    solid: `var(--recency-${status}-solid)`,
  };
}

// A stable className the acceptance tests assert on per status
// (e.g. "recency-overdue"). One source of truth for the tone class.
export function recencyClass(status: ContactStatus): string {
  return `recency-${status}`;
}

// ── Recovery-board situation bar (S9 W4 / Recovery Board front door) ──────────
// The headline numbers the Enrollment situation bar shows are DERIVED client-side
// from the already-fetched /work-queue rows — never hardcoded (INV-11 spirit).
// This pure summary lives here (the recency logic home) so it is unit-testable
// and reads the same ContactStatus the rows carry.

// The minimal row shape the summary reads from a /work-queue item — identity is
// irrelevant; only the recovery-relevant signals (recency + dollar value) matter.
export interface RecoverableRow {
  value: number;
  contact_status?: string | null;
}

// The three derived headline figures the situation bar renders.
export interface SituationSummary {
  // Families still on the active worklist that have NOT been followed up or
  // closed — i.e. an open, un-actioned recovery (fresh or overdue recency).
  stalled: number;
  // Families aged past the follow-up threshold and not yet contacted.
  overdue: number;
  // Total recoverable dollar value still in play — the sum of `value` over the
  // rows that are still recoverable (anything not already closed/won).
  recoverableValue: number;
}

// True if a row is still in play (recoverable) — not yet closed/won. An unknown
// or absent status is treated as recoverable (it's still on the active queue).
function isRecoverable(status: string | null | undefined): boolean {
  return status !== 'closed';
}

// True if a row is "stalled": still recoverable, not yet followed up, AND not a
// brand-new FRESH lead (A-17). A fresh lead is still inside its contact window —
// it hasn't gone cold yet, so it is NOT money the recovery loop is leaving on the
// table. Stalled is the un-actioned-AND-aged set the situation strip headlines.
function isStalled(status: string | null | undefined): boolean {
  return (
    isRecoverable(status) && status !== 'followed_up' && status !== 'fresh'
  );
}

// Fold a list of work-queue rows into the situation-bar headline figures. Pure:
// a function of its input alone, no fetch, no clock.
export function summarizeRecovery(rows: readonly RecoverableRow[]): SituationSummary {
  let stalled = 0;
  let overdue = 0;
  let recoverableValue = 0;
  for (const row of rows) {
    const status = row.contact_status;
    if (isStalled(status)) stalled += 1;
    if (status === 'overdue') overdue += 1;
    if (isRecoverable(status)) recoverableValue += row.value;
  }
  return { stalled, overdue, recoverableValue };
}
