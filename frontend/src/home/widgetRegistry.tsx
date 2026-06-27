import type { ComponentType } from 'react';
import PipelineBoard from '../PipelineBoard';
import Scoreboard from '../Scoreboard';
import SecurityTab from '../security/SecurityTab';
import DecisionQueueWorkspace from '../workspaces/DecisionQueueWorkspace';
import DataConfidenceBanner from '../dashboard/DataConfidenceBanner';
import {
  KpiStripWidget,
  WorkQueueWidget,
  CrmStatusWidget,
  WidgetPlaceholder,
} from './widgets';

// The frontend Home widget catalog — the SINGLE frontend home for the composable
// Home id vocabulary (TODO_v2 §B3 / U4). This MIRRORS the backend's canonical
// catalog (`app/core/widget_registry.py` — REGISTRY_IDS, 36 ids across 7 groups)
// EXACTLY: a drift here would silently drop widgets server-side (the backend
// `merge_starter_pack` reconcile drops any placement whose id is not in
// REGISTRY_IDS). Per CLAUDE INV-11 the id vocabulary has exactly one canonical
// home on each side and the two must agree; this is the frontend's. Neither side
// invents ids of its own.
//
// Each id maps to either a REAL cockpit component (where one exists — the eight
// starter-pack widgets all do) or an honest `WidgetPlaceholder` tile. We do NOT
// build 36 brand-new dashboards: the point of B3 is the composable FRAME + the
// registry, with real widgets where they exist and labeled placeholders
// otherwise.

export type WidgetGroup =
  | 'Enrollment'
  | 'Marketing'
  | 'Leadership'
  | 'CRM'
  | 'Security'
  | 'Funding'
  | 'Observability';

export interface WidgetDef {
  /** Canonical react-grid-layout placement key (`i`) — mirrors the backend id. */
  id: string;
  /** Functional surface the widget belongs to (sections the picker). */
  group: WidgetGroup;
  /** Human label shown on the tile header + in the picker. */
  label: string;
  /** Whether the backend ships this in the default new-user starter pack. */
  starter: boolean;
  /** The component rendered in the grid cell (a real surface or a placeholder). */
  Component: ComponentType;
}

// A small helper so the 28 placeholder ids stay one line each.
function placeholder(label: string): ComponentType {
  const C = (): JSX.Element => <WidgetPlaceholder label={label} />;
  C.displayName = `Placeholder(${label})`;
  return C;
}

// The full 36-id catalog, in backend declaration order. `starter: true` on the
// eight default widgets matches STARTER_IDS exactly (the backend owns the merge;
// these flags only drive the picker's "in starter pack" affordance + the
// fail-safe fallback grid).
export const WIDGETS: readonly WidgetDef[] = [
  // ----------------------------------------------------------- Enrollment (11)
  { id: 'kpi_strip', group: 'Enrollment', label: 'Enrollment KPI Strip', starter: true, Component: KpiStripWidget },
  { id: 'work_queue', group: 'Enrollment', label: 'Work Queue', starter: true, Component: WorkQueueWidget },
  { id: 'pipeline_board', group: 'Enrollment', label: 'Pipeline Board', starter: true, Component: PipelineBoard },
  { id: 'deal_view', group: 'Enrollment', label: 'Deal View', starter: false, Component: placeholder('Deal View') },
  { id: 'funding_tracker', group: 'Enrollment', label: 'Funding Tracker', starter: false, Component: placeholder('Funding Tracker') },
  { id: 'seam_reconcile', group: 'Enrollment', label: 'Seam Reconcile', starter: false, Component: placeholder('Seam Reconcile') },
  { id: 'assignment_board', group: 'Enrollment', label: 'Assignment Board', starter: false, Component: placeholder('Assignment Board') },
  { id: 'sla_sweep', group: 'Enrollment', label: 'SLA Sweep', starter: false, Component: placeholder('SLA Sweep') },
  { id: 'contact_outcomes', group: 'Enrollment', label: 'Contact Outcomes', starter: false, Component: placeholder('Contact Outcomes') },
  { id: 'merge_queue', group: 'Enrollment', label: 'Merge Queue', starter: false, Component: placeholder('Merge Queue') },
  { id: 'notes_timeline', group: 'Enrollment', label: 'Notes Timeline', starter: false, Component: placeholder('Notes Timeline') },
  // ------------------------------------------------------------- Marketing (9)
  { id: 'content_library', group: 'Marketing', label: 'Content Library', starter: false, Component: placeholder('Content Library') },
  { id: 'geo_module', group: 'Marketing', label: 'GEO Module', starter: false, Component: placeholder('GEO Module') },
  { id: 'brand_memory', group: 'Marketing', label: 'Brand Memory', starter: false, Component: placeholder('Brand Memory') },
  { id: 'scheduler', group: 'Marketing', label: 'Scheduler', starter: false, Component: placeholder('Scheduler') },
  { id: 'sentiment', group: 'Marketing', label: 'Sentiment', starter: false, Component: placeholder('Sentiment') },
  { id: 'creator_intel', group: 'Marketing', label: 'Creator Intel', starter: false, Component: placeholder('Creator Intel') },
  { id: 'recipes', group: 'Marketing', label: 'Recipes', starter: false, Component: placeholder('Recipes') },
  { id: 'publish_monitor', group: 'Marketing', label: 'Publish Monitor', starter: false, Component: placeholder('Publish Monitor') },
  { id: 'campaign_analytics', group: 'Marketing', label: 'Campaign Analytics', starter: false, Component: placeholder('Campaign Analytics') },
  // ------------------------------------------------------------ Leadership (6)
  { id: 'scoreboard', group: 'Leadership', label: 'Scoreboard', starter: true, Component: Scoreboard },
  { id: 'decision_queue', group: 'Leadership', label: 'Decision Queue', starter: true, Component: DecisionQueueWorkspace },
  { id: 'budget_tracker', group: 'Leadership', label: 'Budget Tracker', starter: false, Component: placeholder('Budget Tracker') },
  { id: 'kpi_scorecard', group: 'Leadership', label: 'KPI Scorecard', starter: false, Component: placeholder('KPI Scorecard') },
  { id: 'eval_scoreboard', group: 'Leadership', label: 'Eval Scoreboard', starter: false, Component: placeholder('Eval Scoreboard') },
  { id: 'agent_rollup', group: 'Leadership', label: 'Agent Rollup', starter: false, Component: placeholder('Agent Rollup') },
  // ------------------------------------------------------------------- CRM (4)
  { id: 'crm_status', group: 'CRM', label: 'CRM Status', starter: true, Component: CrmStatusWidget },
  { id: 'data_confidence', group: 'CRM', label: 'Data Confidence', starter: true, Component: DataConfidenceBanner },
  { id: 'sync_parity', group: 'CRM', label: 'Sync Parity', starter: false, Component: placeholder('Sync Parity') },
  { id: 'crm_poll_status', group: 'CRM', label: 'CRM Poll Status', starter: false, Component: placeholder('CRM Poll Status') },
  // -------------------------------------------------------------- Security (2)
  { id: 'rls_posture', group: 'Security', label: 'RLS Posture', starter: true, Component: SecurityTab },
  { id: 'security_feed', group: 'Security', label: 'Security Feed', starter: false, Component: placeholder('Security Feed') },
  // --------------------------------------------------------------- Funding (2)
  { id: 'tefa_tracker', group: 'Funding', label: 'TEFA Tracker', starter: false, Component: placeholder('TEFA Tracker') },
  { id: 'payments_ledger', group: 'Funding', label: 'Payments Ledger', starter: false, Component: placeholder('Payments Ledger') },
  // --------------------------------------------------------- Observability (2)
  { id: 'audit_timeline', group: 'Observability', label: 'Audit Timeline', starter: false, Component: placeholder('Audit Timeline') },
  { id: 'proposal_log', group: 'Observability', label: 'Proposal Log', starter: false, Component: placeholder('Proposal Log') },
];

// The id → definition index (the map the Home reads per placement). One canonical
// lookup; an unknown id (a placement the backend kept but the frontend doesn't
// know) falls back to a placeholder at the call site.
export const WIDGET_BY_ID: ReadonlyMap<string, WidgetDef> = new Map(
  WIDGETS.map((w) => [w.id, w]),
);

// The groups in display order (drives the picker's sections).
export const WIDGET_GROUPS: readonly WidgetGroup[] = [
  'Enrollment',
  'Marketing',
  'Leadership',
  'CRM',
  'Security',
  'Funding',
  'Observability',
];

// The starter ids (mirrors backend STARTER_IDS) — used only for the fail-safe
// fallback grid when GET /home/layout can't load.
export const STARTER_IDS: readonly string[] = WIDGETS.filter(
  (w) => w.starter,
).map((w) => w.id);
