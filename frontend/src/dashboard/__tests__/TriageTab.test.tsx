import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TriageTab from '../TriageTab';

// Acceptance test (CLAUDE §4.2) for the agent Triage tab (R6 / D-12). The tab
// reads the owner-scoped GET /work-queue (the verified agent_id in the bearer
// token already scopes the rows to this agent's assigned families) and surfaces ONLY the
// "falling through the cracks" rows: no `last_contact_at` logged OR an overdue
// follow-up. A contacted/working family with a recent contact is excluded. A row
// click selects the family; no cracks rows → the clean empty state.

const FAM_OVERDUE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const FAM_NO_CONTACT = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const FAM_HEALTHY = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

// Three owner-scoped rows: an overdue follow-up, an uncontacted family (no
// last_contact_at), and a healthy contacted/working family with a recent contact.
const ROWS = [
  {
    family_id: FAM_OVERDUE,
    display_name: 'The Alvarez Family',
    value: 60000,
    contact_status: 'overdue',
    recovery_state: 'stalled',
    current_stage: 'enroll',
    assigned_rep_id: 'rep-1',
    stall_date: '2026-06-13T09:00:00Z',
    num_children: 3,
    funding_type: 'tefa_standard',
    recoverable_now: 50000,
    last_contact_at: '2026-06-01T09:00:00Z',
  },
  {
    family_id: FAM_NO_CONTACT,
    display_name: 'The Bauer Family',
    value: 36000,
    contact_status: 'fresh',
    recovery_state: 'stalled',
    current_stage: 'apply',
    assigned_rep_id: 'rep-1',
    stall_date: '2026-06-13T12:00:00Z',
    num_children: 1,
    funding_type: 'self_pay',
    recoverable_now: 30000,
    last_contact_at: null,
  },
  {
    family_id: FAM_HEALTHY,
    display_name: 'The Cho Family',
    value: 10000,
    contact_status: 'followed_up',
    recovery_state: 'working',
    current_stage: 'enroll',
    assigned_rep_id: 'rep-1',
    stall_date: '2026-06-11T09:00:00Z',
    num_children: 2,
    funding_type: 'tefa_standard',
    recoverable_now: 8000,
    last_contact_at: '2026-06-12T09:00:00Z',
  },
];

function mockFetch(payload: unknown, ok = true, status = 200): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok,
      status,
      json: async () => payload,
    })),
  );
}

describe('TriageTab (R6 / D-12)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('surfaces only the falling-through-the-cracks rows (overdue + no contact)', async () => {
    mockFetch(ROWS);
    render(<TriageTab onSelectFamily={vi.fn()} />);

    await screen.findByTestId('triage-tab-rows');
    // The overdue follow-up and the uncontacted family surface.
    const rows = screen.getAllByTestId('triage-tab-row');
    const ids = rows.map((r) => r.getAttribute('data-family'));
    expect(ids).toContain(FAM_OVERDUE);
    expect(ids).toContain(FAM_NO_CONTACT);
    // The contacted/working family with a recent contact is NOT falling through.
    expect(ids).not.toContain(FAM_HEALTHY);
    expect(rows).toHaveLength(2);
  });

  it('shows each surfaced row with its why + value + recency chip', async () => {
    mockFetch(ROWS);
    render(<TriageTab onSelectFamily={vi.fn()} />);

    await screen.findByTestId('triage-tab-rows');
    expect(screen.getByText('The Alvarez Family')).toBeInTheDocument();
    expect(screen.getByText('Follow-up overdue')).toBeInTheDocument();
    expect(screen.getByText('No contact logged yet')).toBeInTheDocument();
    expect(screen.getByText('$60,000')).toBeInTheDocument();
    // Recency chips render (one per surfaced row).
    expect(screen.getAllByTestId('recency-chip')).toHaveLength(2);
  });

  it('fires onSelectFamily with the family id on row click', async () => {
    mockFetch(ROWS);
    const onSelectFamily = vi.fn();
    render(<TriageTab onSelectFamily={onSelectFamily} />);

    await screen.findByTestId('triage-tab-rows');
    const row = screen
      .getAllByTestId('triage-tab-row')
      .find((r) => r.getAttribute('data-family') === FAM_OVERDUE);
    fireEvent.click(row as HTMLElement);
    expect(onSelectFamily).toHaveBeenCalledWith(FAM_OVERDUE);
  });

  it('marks the selected row active', async () => {
    mockFetch(ROWS);
    render(
      <TriageTab onSelectFamily={vi.fn()} selectedFamilyId={FAM_NO_CONTACT} />,
    );

    await screen.findByTestId('triage-tab-rows');
    const row = screen
      .getAllByTestId('triage-tab-row')
      .find((r) => r.getAttribute('data-family') === FAM_NO_CONTACT);
    expect(row).toHaveClass('is-active');
  });

  it('shows the empty state when nothing is falling through the cracks', async () => {
    // Every row is healthy: contacted, working, recent contact.
    mockFetch([ROWS[2]]);
    render(<TriageTab onSelectFamily={vi.fn()} />);

    expect(await screen.findByTestId('empty-state')).toBeInTheDocument();
    expect(
      screen.getByText(/nothing falling through the cracks/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('triage-tab-rows')).not.toBeInTheDocument();
  });

  it('reads from GET /work-queue', async () => {
    mockFetch(ROWS);
    render(<TriageTab onSelectFamily={vi.fn()} />);

    await screen.findByTestId('triage-tab-rows');
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => /\/work-queue/.test(u))).toBe(true);
  });

  it('shows a clean error on a failed fetch', async () => {
    mockFetch({}, false, 500);
    render(<TriageTab onSelectFamily={vi.fn()} />);

    expect(await screen.findByTestId('triage-tab-error')).toBeInTheDocument();
    expect(screen.queryByTestId('triage-tab-rows')).not.toBeInTheDocument();
  });
});
