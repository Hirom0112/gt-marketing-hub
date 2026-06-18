import { useEffect, useState } from 'react';
import {
  CalendarRange,
  GitCompareArrows,
  Gauge,
  GraduationCap,
  TriangleAlert,
} from 'lucide-react';
import { TabBar } from '../ui';
import type { TabItem } from '../ui';
import { apiFetch } from '../config';
import { summarizeRecovery } from '../enrollment/recency';
import { useSession } from '../session/SessionContext';
import { DashboardLayout } from '../dashboard/DashboardLayout';
import { KpiStrip } from '../dashboard/KpiStrip';
import MotivationBanner from '../dashboard/MotivationBanner';
import LeadsTab from '../dashboard/LeadsTab';
import TriageTab from '../dashboard/TriageTab';
import StudentsTab from '../dashboard/StudentsTab';
import ReconcileTab from '../dashboard/ReconcileTab';
import AgentKpiTab from '../dashboard/AgentKpiTab';
import DetailPanel from '../dashboard/DetailPanel';
import ReconcileDetail from '../dashboard/ReconcileDetail';
import type { ReconcileIssue, WorkQueueRow } from '../dashboard/types';

// AgentDashboard — the redesigned sales-agent operating surface (briefs/gt-pulse-
// sales-agent-dashboard-redesign.md). One screen: a full-width 4-metric KPI strip
// (BOOKED / CONTACTED / OVERDUE / ACTIVE), a quiet daily-motivation banner, then
// two columns — left a tabbed work area (Leads / Triage / Students / Reconcile /
// KPI Dashboard), right the contextual detail panel. ASSIGNED families only — the
// backend owner-scopes every read via the X-Demo-Agent-Id header (M1 IDOR defense),
// so the shared tabs need no client-side filter. Composed from the same shared
// dashboard/* components as the admin shell (D-10).

type AgentTab = 'leads' | 'triage' | 'students' | 'reconcile' | 'kpis';

const TABS: ReadonlyArray<TabItem<AgentTab>> = [
  { key: 'leads', label: 'Leads', icon: CalendarRange },
  { key: 'triage', label: 'Triage', icon: TriangleAlert },
  { key: 'students', label: 'Students', icon: GraduationCap },
  { key: 'reconcile', label: 'Reconcile', icon: GitCompareArrows },
  { key: 'kpis', label: 'KPI Dashboard', icon: Gauge },
];

interface AgentKpis {
  appointments_booked: number;
  contacts_made: number;
}

// recovery_state values that count as the agent's live, active book (D-13 ACTIVE).
const ACTIVE_RECOVERY = new Set([
  'stalled',
  'working',
  'cold',
  'presumed_lost',
]);

export default function AgentDashboard(): JSX.Element {
  const { session } = useSession();
  const agentId = session?.agentId;

  const [tab, setTab] = useState<AgentTab>('leads');
  const [familyId, setFamilyId] = useState<string | null>(null);
  const [issue, setIssue] = useState<ReconcileIssue | null>(null);
  const [rows, setRows] = useState<WorkQueueRow[]>([]);
  const [kpis, setKpis] = useState<AgentKpis | null>(null);

  // The 4-metric strip (D-13): OVERDUE + ACTIVE fold from the owner-scoped
  // /work-queue read; BOOKED + CONTACTED come from the agent-kpis aggregate. Both
  // reads degrade to zeros without taking the view down (brief: stay usable).
  useEffect(() => {
    let cancelled = false;
    apiFetch('/work-queue')
      .then((r) =>
        r.ok
          ? (r.json() as Promise<WorkQueueRow[]>)
          : Promise.reject(new Error(String(r.status))),
      )
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch(() => {
        /* keep last-known rows */
      });
    apiFetch('/enrollment/agent-kpis?window=all')
      .then((r) =>
        r.ok
          ? (r.json() as Promise<AgentKpis>)
          : Promise.reject(new Error(String(r.status))),
      )
      .then((data) => {
        if (!cancelled) setKpis(data);
      })
      .catch(() => {
        /* strip's BOOKED/CONTACTED fall back to 0 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sum = summarizeRecovery(rows);
  const active = rows.filter((r) => ACTIVE_RECOVERY.has(r.recovery_state)).length;
  const metrics = [
    { label: 'BOOKED', value: kpis?.appointments_booked ?? 0 },
    { label: 'CONTACTED', value: kpis?.contacts_made ?? 0 },
    {
      label: 'OVERDUE',
      value: sum.overdue,
      tone: sum.overdue > 0 ? ('signal' as const) : undefined,
    },
    { label: 'ACTIVE', value: active },
  ];

  function selectFamily(id: string): void {
    setIssue(null);
    setFamilyId(id);
  }

  const detail =
    tab === 'reconcile' && issue ? (
      <ReconcileDetail issue={issue} />
    ) : (
      <DetailPanel familyId={familyId} />
    );

  return (
    <section data-testid="agent-dashboard" aria-label="Sales agent dashboard">
      <DashboardLayout
        kpiStrip={<KpiStrip metrics={metrics} />}
        banner={<MotivationBanner agentId={agentId ?? 'me'} />}
        tabBar={
          <TabBar
            tabs={TABS}
            active={tab}
            onSelect={setTab}
            ariaLabel="Sales agent work area"
          />
        }
        tabPanel={
          <>
            {tab === 'leads' && (
              <LeadsTab
                onSelectFamily={selectFamily}
                selectedFamilyId={familyId}
              />
            )}
            {tab === 'triage' && (
              <TriageTab
                onSelectFamily={selectFamily}
                selectedFamilyId={familyId}
              />
            )}
            {tab === 'students' && (
              <StudentsTab
                onSelectFamily={selectFamily}
                selectedFamilyId={familyId}
              />
            )}
            {tab === 'reconcile' && (
              <ReconcileTab
                onSelectIssue={setIssue}
                selectedIssueKey={issue ? `${issue.kind}:${issue.family_id}` : null}
              />
            )}
            {tab === 'kpis' && <AgentKpiTab agentId={agentId} />}
          </>
        }
        detailPanel={detail}
      />
    </section>
  );
}
