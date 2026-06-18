import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// Acceptance test (CLAUDE §4.2) for the admin shell composition: a 3-metric KPI
// strip, a 4-tab work area (Leads/Students/Reconcile/Team Roster), and a right
// detail panel that is empty until a row is selected. The child tabs + detail
// panel are stubbed so this isolates the SHELL (tab switching + selection wiring);
// the tabs are covered by their own tests.

const ROWS = [
  { value: 1000, contact_status: 'overdue', recovery_state: 'stalled' },
  { value: 500, contact_status: 'fresh', recovery_state: 'working' },
];

vi.mock('../../config', () => ({
  apiFetch: vi.fn((url: string) =>
    Promise.resolve({
      ok: true,
      json: async () => (url.includes('/work-queue') ? ROWS : []),
    }),
  ),
}));

vi.mock('../../dashboard/LeadsTab', () => ({
  default: ({ onSelectFamily }: { onSelectFamily: (id: string) => void }) => (
    <button data-testid="stub-leads" onClick={() => onSelectFamily('fam-1')}>
      leads
    </button>
  ),
}));
vi.mock('../../dashboard/StudentsTab', () => ({
  default: () => <div data-testid="stub-students" />,
}));
vi.mock('../../dashboard/ReconcileTab', () => ({
  default: ({
    onSelectIssue,
  }: {
    onSelectIssue: (i: { kind: 'seam'; family_id: string; status: string }) => void;
  }) => (
    <button
      data-testid="stub-reconcile"
      onClick={() =>
        onSelectIssue({ kind: 'seam', family_id: 'fam-9', status: 'unsynced' })
      }
    >
      reconcile
    </button>
  ),
}));
vi.mock('../../dashboard/TeamRosterTab', () => ({
  default: () => <div data-testid="stub-roster" />,
}));
vi.mock('../../dashboard/DetailPanel', () => ({
  default: ({ familyId }: { familyId: string | null }) => (
    <div data-testid="stub-detail">{familyId ?? 'empty'}</div>
  ),
}));
vi.mock('../../dashboard/ReconcileDetail', () => ({
  default: ({ issue }: { issue: { family_id: string } }) => (
    <div data-testid="stub-reconcile-detail">{issue.family_id}</div>
  ),
}));

import AdminDashboard from '../AdminDashboard';

describe('AdminDashboard shell', () => {
  it('renders exactly 3 KPI metrics with the admin labels', async () => {
    render(<AdminDashboard />);
    await waitFor(() =>
      expect(screen.getAllByTestId('kpi-metric')).toHaveLength(3),
    );
    expect(screen.getByText('ACTIVE STALLS')).toBeInTheDocument();
    expect(screen.getByText('OVERDUE')).toBeInTheDocument();
    expect(screen.getByText('$ AT RISK')).toBeInTheDocument();
  });

  it('renders the 4 work-area tabs and defaults to Leads', () => {
    render(<AdminDashboard />);
    for (const label of ['Leads', 'Students', 'Reconcile', 'Team Roster']) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByTestId('stub-leads')).toBeInTheDocument();
  });

  it('switches tabs', () => {
    render(<AdminDashboard />);
    fireEvent.click(screen.getByRole('tab', { name: 'Team Roster' }));
    expect(screen.getByTestId('stub-roster')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Students' }));
    expect(screen.getByTestId('stub-students')).toBeInTheDocument();
  });

  it('right panel is empty until a row is selected, then shows the family', () => {
    render(<AdminDashboard />);
    expect(screen.getByTestId('stub-detail')).toHaveTextContent('empty');
    fireEvent.click(screen.getByTestId('stub-leads'));
    expect(screen.getByTestId('stub-detail')).toHaveTextContent('fam-1');
  });

  it('selecting a reconcile issue shows the reconcile detail in the right panel', () => {
    render(<AdminDashboard />);
    fireEvent.click(screen.getByRole('tab', { name: 'Reconcile' }));
    fireEvent.click(screen.getByTestId('stub-reconcile'));
    expect(screen.getByTestId('stub-reconcile-detail')).toHaveTextContent('fam-9');
  });
});
