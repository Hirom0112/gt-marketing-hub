import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LeadsList from '../LeadsList';

// Acceptance test (CLAUDE §4.2). The shared Leads LIST (redesign R2) reads
// GET /work-queue (owner-scoped) and a one-shot GET /students for the student-name
// search index (D-17). Filters: time scope (Day/Week/All), status (Overdue/Fresh/
// Working/Contacted), search over family AND student name, and an admin Triage
// facet (families with no logged contact OR overdue follow-up). Each row shows the
// family, student name(s), a RecencyChip status, last activity, and the next action
// date. A row click lifts the family id. Read-only GET (INV-2).

const AGENT_ONE = 'a0000000-0000-4000-8000-000000000001';
const FAM_A = 'fam-aaaa';
const FAM_B = 'fam-bbbb';
const FAM_C = 'fam-cccc';

// FAM_A: assigned, overdue, never contacted, stalls Jun 16 (a triage crack).
// FAM_B: assigned, fresh, contacted Jun 10, stalls Jun 12 (NOT a crack).
// FAM_C: unassigned, working state, contacted, stalls Jun 02.
const QUEUE = [
  {
    family_id: FAM_A,
    display_name: 'The Alvarez Family',
    value: 10474,
    contact_status: 'overdue',
    recovery_state: 'stalled',
    current_stage: 'enroll',
    assigned_rep_id: AGENT_ONE,
    stall_date: '2026-06-16T09:00:00Z',
    num_children: 2,
    funding_type: 'tefa_standard',
    recoverable_now: 9000,
    last_contact_at: null,
  },
  {
    family_id: FAM_B,
    display_name: 'The Bauer Family',
    value: 30000,
    contact_status: 'fresh',
    recovery_state: 'stalled',
    current_stage: 'apply',
    assigned_rep_id: AGENT_ONE,
    stall_date: '2026-06-12T09:00:00Z',
    num_children: 1,
    funding_type: 'self_pay',
    recoverable_now: 20000,
    last_contact_at: '2026-06-10T09:00:00Z',
  },
  {
    family_id: FAM_C,
    display_name: 'The Castillo Family',
    value: 5000,
    contact_status: 'followed_up',
    recovery_state: 'working',
    current_stage: 'enroll',
    assigned_rep_id: null,
    stall_date: '2026-06-02T09:00:00Z',
    num_children: 1,
    funding_type: 'tefa_standard',
    recoverable_now: 4000,
    last_contact_at: '2026-06-01T09:00:00Z',
  },
];

const STUDENTS = {
  households: [
    {
      family_id: FAM_A,
      students: [
        { synthetic_first_name: 'Mateo' },
        { synthetic_first_name: 'Lucia' },
      ],
    },
    { family_id: FAM_B, students: [{ synthetic_first_name: 'Sophie' }] },
    { family_id: FAM_C, students: [{ synthetic_first_name: 'Diego' }] },
  ],
};

function listFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string) => {
    if (/\/students/.test(url))
      return { ok: true, status: 200, json: async () => STUDENTS };
    return { ok: true, status: 200, json: async () => QUEUE };
  });
}

async function renderList(
  props: Partial<React.ComponentProps<typeof LeadsList>> = {},
): Promise<void> {
  vi.stubGlobal('fetch', listFetch());
  render(<LeadsList onSelectFamily={vi.fn()} {...props} />);
  // Wait for the queue rows AND the /students index to land (the student names
  // only appear once the second fetch resolves).
  await screen.findByTestId('leads-list-rows');
  await waitFor(() =>
    expect(
      screen.getAllByTestId('lead-row-students')[0],
    ).toHaveTextContent('Mateo, Lucia'),
  );
}

describe('LeadsList (redesign R2)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders a row with family, student name(s), status, last activity + next action', async () => {
    await renderList();
    const rows = screen.getAllByTestId('lead-row');
    expect(rows).toHaveLength(3);

    const alvarez = rows[0] as HTMLElement;
    expect(alvarez).toHaveTextContent('The Alvarez Family');
    expect(alvarez).toHaveTextContent('Mateo, Lucia');
    // Status chip carries the overdue tone.
    expect(alvarez.querySelector('.recency-overdue')).not.toBeNull();
    // Last activity (never contacted → "—") + next action (the stall date).
    expect(alvarez).toHaveTextContent('Last activity —');
    expect(alvarez).toHaveTextContent('Next action Jun 16');
  });

  it('the status filter narrows rows to the chosen contact status', async () => {
    await renderList();
    expect(screen.getAllByTestId('lead-row')).toHaveLength(3);

    fireEvent.change(screen.getByTestId('leads-filter-status'), {
      target: { value: 'overdue' },
    });
    const rows = screen.getAllByTestId('lead-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('The Alvarez Family');
  });

  it('the time scope filter narrows rows to the anchored day', async () => {
    // initialFilter pins DAY scope on Jun 16 → only FAM_A (stalls Jun 16).
    await renderList({ initialFilter: { day: 16 } });
    const rows = screen.getAllByTestId('lead-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('The Alvarez Family');

    // Widening to All restores every row.
    fireEvent.click(screen.getByTestId('leads-scope-all'));
    expect(screen.getAllByTestId('lead-row')).toHaveLength(3);
  });

  it('search matches the family name AND the student name', async () => {
    await renderList();

    // Family-name match.
    fireEvent.change(screen.getByTestId('leads-search'), {
      target: { value: 'bauer' },
    });
    let rows = screen.getAllByTestId('lead-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('The Bauer Family');

    // Student-name match (D-17) — "Diego" is FAM_C's child, not in any family name.
    fireEvent.change(screen.getByTestId('leads-search'), {
      target: { value: 'diego' },
    });
    rows = screen.getAllByTestId('lead-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('The Castillo Family');
  });

  it('the Triage facet (admin) surfaces only the falling-through-the-cracks rows', async () => {
    await renderList({ showTriageFilter: true });
    fireEvent.click(screen.getByTestId('leads-triage'));
    // Only FAM_A (overdue + never contacted) is a crack; FAM_B/FAM_C are not.
    const rows = screen.getAllByTestId('lead-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('The Alvarez Family');
  });

  it('the Triage facet is hidden when showTriageFilter is off', async () => {
    await renderList();
    expect(screen.queryByTestId('leads-triage')).toBeNull();
  });

  it('a row click fires onSelectFamily with the family id', async () => {
    vi.stubGlobal('fetch', listFetch());
    const onSelect = vi.fn();
    render(<LeadsList onSelectFamily={onSelect} />);
    const [row] = await screen.findAllByTestId('lead-row');
    fireEvent.click(row as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith(FAM_A);
  });
});
