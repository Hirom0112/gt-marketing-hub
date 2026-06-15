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
