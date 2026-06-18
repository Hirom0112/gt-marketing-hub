import { useState } from 'react';
import { CalendarDays, List } from 'lucide-react';
import { WorkspaceToggle } from '../ui';
import LeadsCalendar from './LeadsCalendar';
import LeadsList, { type DayAnchor, type TimeScope } from './LeadsList';

// The Leads tab (admin-dashboard redesign) — two view modes behind a toggle:
// Calendar (default) and List. Clicking an agent chip in the calendar switches to
// the list pre-filtered to that agent + that day. The agent/scope/day filter state
// lives HERE so the calendar can drive it; the list owns its own status/search/
// triage facets. Selecting a lead row lifts the family id to the dashboard.

type Mode = 'calendar' | 'list';

interface LeadsTabProps {
  selectedFamilyId: string | null;
  onSelectFamily: (familyId: string) => void;
  // Tests pin the calendar's opening month; production resolves the latest month.
  initialMonth?: string;
}

const MODE_OPTIONS = [
  { key: 'calendar' as const, label: 'Calendar', icon: CalendarDays },
  { key: 'list' as const, label: 'List', icon: List },
];

export default function LeadsTab({
  selectedFamilyId,
  onSelectFamily,
  initialMonth,
}: LeadsTabProps): JSX.Element {
  const [mode, setMode] = useState<Mode>('calendar');
  const [agentFilter, setAgentFilter] = useState<string | null>(null);
  const [scope, setScope] = useState<TimeScope>('all');
  const [dayAnchor, setDayAnchor] = useState<DayAnchor | null>(null);

  function pickAgentDay(agentId: string, day: number, month: string): void {
    setAgentFilter(agentId);
    setDayAnchor({ month, day });
    setScope('day');
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
              // Leaving the calendar for the list manually clears the day anchor
              // (a fresh manual list starts at All scope, no pinned day).
              if (m === 'list' && mode === 'calendar') {
                /* keep any chip-set anchor only when arriving via a chip */
              }
              setMode(m);
            }}
            ariaLabel="Leads view"
          />
        </div>
      </div>

      {mode === 'calendar' ? (
        <LeadsCalendar initialMonth={initialMonth} onPickAgentDay={pickAgentDay} />
      ) : (
        <LeadsList
          selectedFamilyId={selectedFamilyId}
          onSelectFamily={onSelectFamily}
          agentFilter={agentFilter}
          onAgentFilter={(v) => {
            setAgentFilter(v);
            // Manually changing the agent clears the pinned calendar day.
            setDayAnchor(null);
          }}
          scope={scope}
          onScope={(s) => {
            setScope(s);
            if (s === 'all') setDayAnchor(null);
          }}
          dayAnchor={dayAnchor}
        />
      )}
    </section>
  );
}
