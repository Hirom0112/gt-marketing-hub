import { useState } from 'react';
import { CalendarDays, List } from 'lucide-react';
import { WorkspaceToggle } from '../ui';
import LeadsCalendar from './LeadsCalendar';
import LeadsList from './LeadsList';

// The shared Leads tab (redesign R2) — two view modes behind a WorkspaceToggle:
// Calendar (default) and List. Clicking a day or an agent chip in the calendar
// switches to the list pre-filtered to that day (+agent for a chip). The tab is
// scope-agnostic (the backend owner-scopes both reads); `showTriageFilter` only
// surfaces the admin Leads-list Triage facet. Selecting a row lifts the family id.

type Mode = 'calendar' | 'list';

interface LeadsTabProps {
  onSelectFamily: (familyId: string) => void;
  selectedFamilyId?: string | null;
  // Admin shell surfaces the in-list Triage facet; agent shell has its own tab.
  showTriageFilter?: boolean;
  // Tests pin the calendar's opening month; production resolves the latest month.
  initialMonth?: string;
  // Narrow the calendar to a single owner (agent shell).
  owner?: string;
}

const MODE_OPTIONS = [
  { key: 'calendar' as const, label: 'Calendar', icon: CalendarDays },
  { key: 'list' as const, label: 'List', icon: List },
];

export default function LeadsTab({
  onSelectFamily,
  selectedFamilyId = null,
  showTriageFilter = false,
  initialMonth,
  owner,
}: LeadsTabProps): JSX.Element {
  const [mode, setMode] = useState<Mode>('calendar');
  // The filter handed to the list when arriving from the calendar. A manual toggle
  // to List clears it (a fresh manual list starts unfiltered, All scope).
  const [listFilter, setListFilter] = useState<{
    day?: number;
    agentId?: string;
  } | null>(null);

  function drillToList(filter: { day: number; agentId?: string }): void {
    setListFilter(filter);
    setMode('list');
  }

  return (
    <section aria-label="Leads" data-testid="admin-tab-leads">
      <div className="admin-toolbar" style={{ marginBottom: 'var(--s-3)' }}>
        <div data-testid="leads-view-toggle">
          <WorkspaceToggle
            options={MODE_OPTIONS}
            active={mode}
            onSelect={(m) => {
              // A manual switch to the list drops any pinned calendar filter.
              if (m === 'list') setListFilter(null);
              setMode(m);
            }}
            ariaLabel="Leads view"
          />
        </div>
      </div>

      {mode === 'calendar' ? (
        <LeadsCalendar
          initialMonth={initialMonth}
          owner={owner}
          onDrillToList={drillToList}
        />
      ) : (
        <LeadsList
          // Remount the list when the calendar-supplied filter changes so the
          // pre-filter is applied as the list's initial state.
          key={
            listFilter
              ? `${listFilter.day ?? ''}:${listFilter.agentId ?? ''}`
              : 'manual'
          }
          onSelectFamily={onSelectFamily}
          selectedFamilyId={selectedFamilyId}
          initialFilter={listFilter ?? undefined}
          showTriageFilter={showTriageFilter}
        />
      )}
    </section>
  );
}
