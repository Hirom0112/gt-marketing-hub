// Decision Queue seed data + helpers (mock, ported from the design file).
// The queue is the async decision layer: proposals from operators, plus
// auto-flags from Budget (variance >10%), CRM (sync drift), and hot-family
// escalations. Status transitions are leader-only (see registry.canDecide).

export type DqStatus = 'pending' | 'approved' | 'rejected' | 'needinfo';

export interface Decision {
  id: string;
  type: string;     // AUTO · BUDGET | PROPOSAL | GOAL CHANGE | HOT FAMILY | AUTO · SYNC
  title: string;
  detail: string;
  from: string;     // source module
  by: string;       // raised by
  amount: string;
  age: string;
  status: DqStatus;
  mine: boolean;    // submitted by the demo operator (Grassroots Owner)
}

export const SEED_DECISIONS: Decision[] = [
  { id: 'DQ-118', type: 'AUTO · BUDGET', title: 'Guerrilla / earned media bets +17.5% over plan',
    detail: 'Committed $47.0K vs $40.0K planned. The >10% variance threshold was breached, so the Budget Tracker routed this here automatically. Reallocate, approve the overage, or cut scope?',
    from: 'Budget Tracker', by: 'System · auto-flag', amount: '+$7.0K', age: '2h', status: 'pending', mine: false },
  { id: 'DQ-117', type: 'PROPOSAL', title: 'Fund parent-panel guerrilla bet for the Aug push',
    detail: '$8K for two parent-panel pop-ups in high-income TX zips during the late-July conversion phase. Projected 30–40 warm intros.',
    from: 'Grassroots Engine', by: 'the Grassroots Owner', amount: '+$8.0K', age: '1d', status: 'pending', mine: true },
  { id: 'DQ-116', type: 'PROPOSAL', title: 'Approve Joe sizzle-reel + Thailand videographer travel',
    detail: 'Founder content: sizzle reel for Joe and a 5-family interview shoot. Travel + production within content workstream.',
    from: 'Content & Thought Leadership', by: 'the Content Owner', amount: '+$6.0K', age: '1d', status: 'pending', mine: false },
  { id: 'DQ-115', type: 'GOAL CHANGE', title: 'Raise ambassador-influenced enrollment target 30 → 40',
    detail: 'Ambassador attribution pacing ahead; leadership proposes a higher bar for end-of-August review.',
    from: 'KPI Scorecard', by: 'Leadership', amount: '—', age: '2d', status: 'approved', mine: false },
  { id: 'DQ-114', type: 'HOT FAMILY', title: 'Urgent follow-up — T1 family flagged from SMS',
    detail: 'High-intent T1 family ($160K+, K-2, clicked) went quiet after a tuition question. Escalated from Nurture / Admissions for a personal response.',
    from: 'Nurture & Lifecycle', by: 'the Admissions Owner', amount: '—', age: '3d', status: 'needinfo', mine: false },
  { id: 'DQ-112', type: 'AUTO · SYNC', title: 'Sync parity drop — income field at 94%',
    detail: 'Supabase ⇄ HubSpot income-field parity fell below threshold; data-confidence banner is live app-wide. Acknowledge and prioritize the fix?',
    from: 'CRM / Marketing Ops', by: 'System · auto-flag', amount: '—', age: '4d', status: 'rejected', mine: false },
  { id: 'DQ-108', type: 'PROPOSAL', title: 'Toolkit budget for 12 new ambassadors',
    detail: 'Onboarding kits + first-event materials for the new ambassador cohort.',
    from: 'Grassroots Engine', by: 'the Grassroots Owner', amount: '+$2.4K', age: '9d', status: 'approved', mine: true },
];

export interface StatusMeta { label: string; color: string; bg: string; }

export function statusMeta(s: DqStatus): StatusMeta {
  switch (s) {
    case 'pending': return { label: 'PENDING', color: 'var(--signal)', bg: 'var(--signal-soft)' };
    case 'approved': return { label: 'APPROVED', color: 'var(--ok)', bg: 'var(--ok-soft)' };
    case 'rejected': return { label: 'CLOSED', color: 'var(--ink-3)', bg: 'var(--accent-soft)' };
    case 'needinfo': return { label: 'NEED-INFO', color: 'var(--warn)', bg: 'var(--warn-soft)' };
  }
}

export function typeColor(t: string): string {
  if (t.indexOf('AUTO') === 0) return 'var(--signal)';
  if (t === 'GOAL CHANGE') return 'var(--gold)';
  if (t === 'HOT FAMILY') return 'var(--signal)';
  return 'var(--ink-2)';
}

// ---- Live backend shapes (GET/POST /decisions) ----------------------------
// The wire shape from app/api/decisions.py (DecisionResponse). `state` is the raw
// backbone state machine value; `/decisions/mine` adds `latest_comment`.

export type ApiState = 'open' | 'decided' | 'in_flight';
export type ApiPriority = 'urgent' | 'normal';
export type ApiWorkstream =
  | 'content' | 'grassroots' | 'field_events' | 'budget' | 'admissions' | 'nurture';

export interface ApiDecision {
  id: string;
  source: string;
  state: ApiState;
  question: string;
  raised_by: string;
  workstream: string;
  recommendation: string;
  budget_ask: number | null;
  due_date: string | null;       // YYYY-MM-DD
  priority: string;              // urgent | normal
  resolution_date: string | null; // iso
  outcome: string | null;          // approve | reject | need_info | null (latest verdict)
  created_at?: string;
}

// `/decisions/mine` extends the row with the leader's latest action comment.
export interface MyApiDecision extends ApiDecision {
  latest_comment: string | null;
}

// The raise form's payload (POST /decisions). raised_by is stamped server-side.
export interface RaiseBody {
  question: string;
  recommendation: string;
  workstream: ApiWorkstream;
  budget_ask?: number | null;
  due_date?: string | null;
  priority: ApiPriority;
}

export const WORKSTREAM_OPTIONS: { value: ApiWorkstream; label: string }[] = [
  { value: 'content', label: 'Content & Thought Leadership' },
  { value: 'grassroots', label: 'Grassroots Engine' },
  { value: 'field_events', label: 'Field & Events' },
  { value: 'budget', label: 'Budget' },
  { value: 'admissions', label: 'Admissions' },
  { value: 'nurture', label: 'Nurture & Lifecycle' },
];

export function workstreamLabel(w: string): string {
  return WORKSTREAM_OPTIONS.find((o) => o.value === w)?.label ?? (w || '—');
}

// The display outcome, from the API's real verdict (`outcome` = the latest action)
// combined with `state`:
//   • outcome approve → APPROVED, reject → REJECTED, need_info → NEED-INFO (open)
//   • open + no verdict → PENDING
//   • in_flight → IN FLIGHT
// `resolved` is a defensive fallback for a decided row with no recorded verdict.
export type Outcome = 'pending' | 'needinfo' | 'approved' | 'rejected' | 'resolved' | 'inflight';

export function outcomeOf(d: { state: ApiState; outcome?: string | null; latest_comment?: string | null }): Outcome {
  if (d.outcome === 'approve') return 'approved';
  if (d.outcome === 'reject') return 'rejected';
  if (d.state === 'in_flight') return 'inflight';
  if (d.outcome === 'need_info') return 'needinfo';
  if (d.state === 'decided') return 'resolved';
  // open with no verdict (or a stale need-info comment without the flag)
  if (d.latest_comment) return 'needinfo';
  return 'pending';
}

export function outcomeMeta(o: Outcome): StatusMeta {
  switch (o) {
    case 'pending': return { label: 'PENDING', color: 'var(--signal)', bg: 'var(--signal-soft)' };
    case 'needinfo': return { label: 'NEED-INFO', color: 'var(--warn)', bg: 'var(--warn-soft)' };
    case 'approved': return { label: 'APPROVED', color: 'var(--ok)', bg: 'var(--ok-soft)' };
    case 'rejected': return { label: 'REJECTED', color: 'var(--signal)', bg: 'var(--signal-soft)' };
    case 'resolved': return { label: 'RESOLVED', color: 'var(--ok)', bg: 'var(--ok-soft)' };
    case 'inflight': return { label: 'IN FLIGHT', color: 'var(--brand)', bg: 'var(--accent-soft)' };
  }
}

// Relative age from an iso timestamp ("2h", "1d") — matches the seed's compact style.
export function relAge(iso: string | null | undefined): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m`;
  const h = Math.round(mins / 60);
  return h < 24 ? `${h}h` : `${Math.round(h / 24)}d`;
}

export function fmtBudget(n: number | null | undefined): string | null {
  if (n === null || n === undefined) return null;
  const abs = Math.abs(n);
  const s = abs >= 1000 ? `$${(abs / 1000).toFixed(abs % 1000 === 0 ? 0 : 1)}K` : `$${abs}`;
  return n < 0 ? `-${s}` : s;
}
