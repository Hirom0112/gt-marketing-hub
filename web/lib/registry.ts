// The GT Marketing Hub module registry + role model.
// One canonical source for: the 13 modules, their sidebar grouping/order, owners,
// data sources, sub-view tabs, and the hard role gates. The shell, sidebar, and
// every page read from here so access rules live in exactly one place.

export type Role = 'admin' | 'leader' | 'operator';

// Module ids are stable route segments (/grassroots, /decision, ...).
export type ModuleId =
  | 'home' | 'dashboard' | 'decision' | 'budget'
  | 'grassroots' | 'content' | 'camp' | 'events' | 'nurture'
  | 'crm' | 'admissions' | 'website' | 'resources';

export type Group = 'COMMAND' | 'GROWTH' | 'OPERATIONS';

export interface ModuleDef {
  id: ModuleId;
  idx: string;        // canonical spec number (01..13) shown as a mono index badge
  label: string;      // sidebar label
  title: string;      // header title (long form)
  group: Group;
  owner: string;      // owning role/person, human-readable
  source: string;     // primary data source
  tabs: string[];     // sub-view tab bar
}

// Ordered, grouped sidebar. The canonical spec number rides along as `idx`,
// but the *order* is domain-grouped (COMMAND / GROWTH / OPERATIONS) — leadership
// surfaces first, then the demand workstreams, then measurement/plumbing.
export const MODULES: ModuleDef[] = [
  // COMMAND — cross-cutting, leadership's primary surfaces
  { id: 'home', idx: '01', label: 'Executive Command', title: 'Executive Command Center',
    group: 'COMMAND', owner: 'All (personal)', source: 'Aggregates · all 12 modules',
    tabs: ['Home'] },
  { id: 'dashboard', idx: '06', label: 'KPI Scorecard', title: 'Dashboard / KPI Tracking',
    group: 'COMMAND', owner: 'the Marketing Lead', source: 'Reads all · owns nothing',
    tabs: ['Scorecard', 'Trends', 'SLA & ops health', 'Goal pacing', 'HubSpot mirror'] },
  { id: 'decision', idx: '11', label: 'Decision Queue', title: 'Decision Queue',
    group: 'COMMAND', owner: 'Leadership (submit: all)', source: 'Manual submission · leadership',
    tabs: ['Active decisions', 'History', 'Raise flow'] },
  { id: 'budget', idx: '10', label: 'Budget Tracker', title: 'Budget Tracker',
    group: 'COMMAND', owner: 'the Budget Owner', source: 'System of record · the Hub',
    tabs: ['Budget table', 'Burn chart', 'Spend by workstream', 'Variance alerts'] },

  // GROWTH — the workstreams that create demand
  { id: 'grassroots', idx: '02', label: 'Grassroots Engine', title: 'Grassroots Engine',
    group: 'GROWTH', owner: 'the Grassroots Owner', source: 'HubSpot + community.gt.school',
    tabs: ['Overview', 'Ambassadors', 'Market map', 'Referral sprints', 'Parent community', 'Event calendar'] },
  { id: 'content', idx: '03', label: 'Content & Thought Ldr', title: 'Content & Thought Leadership',
    group: 'GROWTH', owner: 'the Content Owner', source: 'Google Sheet + HubSpot + Meta',
    tabs: ['Overview', 'Production pipeline', 'Content calendar', 'Performance', 'Content library'] },
  { id: 'camp', idx: '04', label: 'Summer Camp', title: 'Summer Camp',
    group: 'GROWTH', owner: 'the Content Owner', source: 'summer.gt.school + reg form',
    tabs: ['Overview', 'Registration funnel', 'Content + campaigns', 'Sessions'] },
  { id: 'events', idx: '08', label: 'Field & Events', title: 'Field Marketing & Events',
    group: 'GROWTH', owner: 'the Field & Events Owner', source: 'Manual entry',
    tabs: ['Overview', 'Event tracker', 'Calendar', 'Priority recommendations'] },
  { id: 'nurture', idx: '05', label: 'Nurture & Lifecycle', title: 'Nurture & Lifecycle',
    group: 'GROWTH', owner: 'the Marketing Lead', source: 'Supabase app_form + HubSpot',
    tabs: ['Overview', 'Segments', 'Pipeline stages', 'Sequences', 'SMS inbox', 'SLA tracker'] },

  // OPERATIONS — measurement, plumbing, the shelf
  { id: 'crm', idx: '07', label: 'CRM / Marketing Ops', title: 'CRM / Marketing Operations',
    group: 'OPERATIONS', owner: 'the Marketing Lead', source: 'Supabase ⇄ HubSpot parity',
    tabs: ['Overview', 'Source tracking', 'Lead scoring', 'Sync parity', 'Data quality queue'] },
  { id: 'admissions', idx: '09', label: 'Admissions & VoC', title: 'Admissions & Voice of Customer',
    group: 'OPERATIONS', owner: 'the Admissions Owner', source: 'HubSpot Conversations + manual',
    tabs: ['Overview', 'Objection log', 'Objection-to-content bridge', 'Voice of families', 'Feedback loop'] },
  { id: 'website', idx: '13', label: 'Website & Digital', title: 'Website & Digital Analytics',
    group: 'OPERATIONS', owner: 'the Marketing Lead', source: 'GA4 · gt.school + anywhere.gt.school',
    tabs: ['Overview', 'Subpage performance', 'Traffic sources', 'PDF & downloads', 'Conversion paths'] },
  { id: 'resources', idx: '12', label: 'Resource Library', title: 'Resource Library',
    group: 'OPERATIONS', owner: 'All', source: 'Manual upload + linked docs',
    tabs: ['Library'] },
];

export const GROUP_ORDER: Group[] = ['COMMAND', 'GROWTH', 'OPERATIONS'];

export function moduleById(id: string): ModuleDef | undefined {
  return MODULES.find((m) => m.id === id);
}

// ---- Role gates (the hard rules) -------------------------------------------
// A session is a role plus, for operators, the module ids they own (write).
// Each operator (Grassroots/Content/Field&Events/Admissions Owner) owns a
// different set; the demo defaults to the Grassroots Owner.
export interface Session {
  role: Role;
  ownedModules: ModuleId[];
  userName: string;
  userRole: string; // display label under the avatar
}

// Open a module's page. Everyone can navigate everywhere; the Decision Queue
// page itself renders a restricted surface for operators (their own submissions
// only) — see canViewFullQueue. Real read-access stays gated inside the screen.
export function canView(session: Session, id: ModuleId): boolean {
  return true;
}

// See the FULL Decision Queue (all submissions, not just your own). Admin +
// Leader only; an operator sees only the items they submitted.
export function canViewFullQueue(session: Session): boolean {
  return session.role !== 'operator';
}

// Edit a workstream. Admin edits all; an operator edits only owned modules;
// a leader never edits a workstream (read + decide only).
export function canEditWorkstream(session: Session, id: ModuleId): boolean {
  if (session.role === 'admin') return true;
  if (session.role === 'operator') return session.ownedModules.includes(id);
  return false;
}

// Approve / reject / respond on the Decision Queue — LEADER ONLY.
// Admin views the queue but never decides; operator cannot even view it.
export function canDecide(session: Session): boolean {
  return session.role === 'leader';
}

// Anyone may submit a decision/proposal (operator: from an owned module).
export function canSubmitDecision(session: Session): boolean {
  return true;
}

// Set goals/targets — leadership only.
export function canSetGoals(session: Session): boolean {
  return session.role === 'leader';
}

// Comment: admin + leader comment on any workstream; operator on owned only.
export function canComment(session: Session, id: ModuleId): boolean {
  if (session.role === 'leader' || session.role === 'admin') return true;
  return session.ownedModules.includes(id);
}
