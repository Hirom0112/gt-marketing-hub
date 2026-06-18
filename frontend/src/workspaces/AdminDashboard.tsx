import { useEffect, useState } from 'react';
import {
  CalendarRange,
  GitCompareArrows,
  GraduationCap,
  UsersRound,
} from 'lucide-react';
import { TabBar } from '../ui';
import type { TabItem } from '../ui';
import { apiFetch } from '../config';
import { summarizeRecovery, type RecoverableRow } from '../enrollment/recency';
import { fmtUSD } from '../enrollment/format';
import { DashboardLayout } from '../dashboard/DashboardLayout';
import { KpiStrip } from '../dashboard/KpiStrip';
import LeadsTab from '../dashboard/LeadsTab';
import StudentsTab from '../dashboard/StudentsTab';
import ReconcileTab from '../dashboard/ReconcileTab';
import TeamRosterTab from '../dashboard/TeamRosterTab';
import DetailPanel from '../dashboard/DetailPanel';
import ReconcileDetail from '../dashboard/ReconcileDetail';
import type { ReconcileIssue } from '../dashboard/types';

// AdminDashboard — the redesigned admin command surface (briefs/gt-pulse-admin-
// dashboard-redesign.md). One screen: a full-width 3-metric KPI strip on top, then
// two columns — left a tabbed work area (Leads / Students / Reconcile / Team
// Roster), right the contextual detail panel. NOTHING above the strip or below the
// columns (brief: restraint). Admin sees ALL families (no owner scope). Composed
// entirely from the shared dashboard/* components + existing primitives (D-2/D-10).

type AdminTab = 'leads' | 'students' | 'reconcile' | 'roster';

const TABS: ReadonlyArray<TabItem<AdminTab>> = [
  { key: 'leads', label: 'Leads', icon: CalendarRange },
  { key: 'students', label: 'Students', icon: GraduationCap },
  { key: 'reconcile', label: 'Reconcile', icon: GitCompareArrows },
  { key: 'roster', label: 'Team Roster', icon: UsersRound },
];

export default function AdminDashboard(): JSX.Element {
  const [tab, setTab] = useState<AdminTab>('leads');
  const [familyId, setFamilyId] = useState<string | null>(null);
  const [issue, setIssue] = useState<ReconcileIssue | null>(null);
  const [rows, setRows] = useState<RecoverableRow[]>([]);

  // The KPI strip is derived client-side from the ONE /work-queue read (the same
  // source SituationBar uses) — never hardcoded (INV-11 spirit). If the read fails
  // the strip degrades to zeros but every tab stays usable (brief: trustworthy even
  // when the upstream is slow). /work-queue is the dashboard's own DB read.
  useEffect(() => {
    let cancelled = false;
    apiFetch('/work-queue')
      .then((r) =>
        r.ok
          ? (r.json() as Promise<RecoverableRow[]>)
          : Promise.reject(new Error(String(r.status))),
      )
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch(() => {
        /* keep last-known rows; the view stays usable */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sum = summarizeRecovery(rows);
  const metrics = [
    { label: 'ACTIVE STALLS', value: sum.stalled },
    {
      label: 'OVERDUE',
      value: sum.overdue,
      tone: sum.overdue > 0 ? ('signal' as const) : undefined,
    },
    { label: '$ AT RISK', value: fmtUSD(sum.recoverableValue) },
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
    <section data-testid="admin-dashboard" aria-label="Admin dashboard">
      <DashboardLayout
        kpiStrip={<KpiStrip metrics={metrics} />}
        tabBar={
          <TabBar
            tabs={TABS}
            active={tab}
            onSelect={setTab}
            ariaLabel="Admin work area"
          />
        }
        tabPanel={
          <>
            {tab === 'leads' && (
              <LeadsTab
                onSelectFamily={selectFamily}
                selectedFamilyId={familyId}
                showTriageFilter
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
            {tab === 'roster' && <TeamRosterTab />}
          </>
        }
        detailPanel={detail}
      />
    </section>
  );
}
