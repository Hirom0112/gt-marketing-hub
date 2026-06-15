import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import WorkQueue from '../WorkQueue';

// Acceptance test (CLAUDE §4.2). The work queue (FR-2.5) fetches GET
// /work-queue — a server-ranked list ordered by score desc — and renders the
// families IN THE ORDER RECEIVED (no client-side re-sort; the server owns the
// ranking). Each row shows the family's value and recoverability. Native fetch
// only (≤12-dep budget). Read-only (INV-2).

// Server returns this already-ranked (score desc). The component must preserve
// this exact order, even though the array below is monotonically decreasing.
const WORK_QUEUE_PAYLOAD = [
  {
    family_id: 'fam-a',
    display_name: 'The Alvarez Family',
    current_stage: 'enroll',
    score: 0.91,
    recoverability: 0.95,
    value: 10474,
    contact_status: 'overdue',
    last_contact_at: null,
  },
  {
    family_id: 'fam-b',
    display_name: 'The Bauer Family',
    current_stage: 'apply',
    score: 0.74,
    recoverability: 0.6,
    value: 30000,
  },
  {
    family_id: 'fam-c',
    display_name: 'The Castillo Family',
    current_stage: 'interest',
    score: 0.32,
    recoverability: 0.4,
    value: 2000,
  },
];

describe('WorkQueue', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => WORK_QUEUE_PAYLOAD,
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the families in the server-supplied order', async () => {
    render(<WorkQueue />);
    await screen.findByText('The Alvarez Family');

    const rows = screen.getAllByTestId('work-queue-row');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('The Alvarez Family');
    expect(rows[1]).toHaveTextContent('The Bauer Family');
    expect(rows[2]).toHaveTextContent('The Castillo Family');
  });

  it('shows each family value and recoverability', async () => {
    render(<WorkQueue />);
    const first = await screen.findByTestId('work-queue-row-fam-a');
    expect(within(first).getByTestId('row-value')).toHaveTextContent('10474');
    expect(within(first).getByTestId('row-recoverability')).toHaveTextContent(
      '0.95',
    );
  });

  it('tints a row by its contact_status (recency color system)', async () => {
    render(<WorkQueue />);
    const recencyChip = await screen.findByTestId(
      'work-queue-recency-fam-a',
    );
    // The overdue family carries the overdue recency class (the red tint).
    expect(recencyChip).toHaveClass('recency-overdue');
  });

  it('fetches the work queue endpoint read-only (GET)', async () => {
    render(<WorkQueue />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/work-queue$/);
    expect(init?.method ?? 'GET').toBe('GET');
  });
});
