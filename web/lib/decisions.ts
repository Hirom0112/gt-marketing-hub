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
