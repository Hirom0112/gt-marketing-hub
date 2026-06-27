import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ReconcileDetail from '../ReconcileDetail';
import type { ReconcileIssue } from '../types';

// Acceptance test (CLAUDE §4.2). The right-panel reconcile detail renders the
// specific discrepancy + its resolution actions. A seam issue offers a push/sync
// action through POST /seam/{family_id}/reconcile (the deterministic core owns the
// write, INV-2; a flagged conflict fails closed, INV-4). A SIS issue (no write
// route) offers a review/escalate gesture, never an automated write.

const SEAM_ISSUE: ReconcileIssue = {
  kind: 'seam',
  family_id: 'fam-a',
  status: 'unsynced',
  seam_status: 'unsynced',
};

const SIS_ISSUE: ReconcileIssue = {
  kind: 'sis',
  family_id: 'fam-x',
  status: 'Paid · not in SIS',
};

function mockReconcile(result?: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'POST' && /\/seam\/[^/]+\/reconcile$/.test(url)) {
        return {
          ok: true,
          status: 200,
          json: async () =>
            result ?? { family_id: 'fam-a', applied: true, seam_status: 'synced' },
        };
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    }),
  );
}

describe('ReconcileDetail', () => {
  beforeEach(() => mockReconcile());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the seam discrepancy and a push/sync action that POSTs reconcile', async () => {
    const onResolved = vi.fn();
    render(<ReconcileDetail issue={SEAM_ISSUE} onResolved={onResolved} />);

    // The discrepancy is shown.
    expect(screen.getByTestId('reconcile-detail')).toHaveAttribute('data-kind', 'seam');
    expect(screen.getByTestId('reconcile-detail-family')).toHaveTextContent('fam-a');
    expect(screen.getByTestId('reconcile-detail-status')).toHaveTextContent('unsynced');

    // The push/sync action POSTs to /seam/{id}/reconcile and adopts the result.
    fireEvent.click(screen.getByTestId('reconcile-push-local'));

    await waitFor(() =>
      expect(screen.getByTestId('reconcile-detail-status')).toHaveTextContent('synced'),
    );
    expect(onResolved).toHaveBeenCalled();

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const post = fetchMock.mock.calls.find(
      ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
    ) as [string, RequestInit] | undefined;
    expect(post?.[0]).toMatch(/\/seam\/fam-a\/reconcile$/);
  });

  it('a flagged conflict fails closed · POSTs but stays unresolved (INV-4)', async () => {
    mockReconcile({ family_id: 'fam-a', applied: false, seam_status: 'conflict' });
    render(<ReconcileDetail issue={SEAM_ISSUE} />);

    fireEvent.click(screen.getByTestId('reconcile-flag-conflict'));

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      expect(
        fetchMock.mock.calls.some(
          ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
        ),
      ).toBe(true);
    });
    expect(screen.getByTestId('reconcile-detail-status')).toHaveTextContent('conflict');
  });

  it('a SIS issue shows the discrepancy and a review/escalate action (no write)', async () => {
    render(<ReconcileDetail issue={SIS_ISSUE} />);

    expect(screen.getByTestId('reconcile-detail')).toHaveAttribute('data-kind', 'sis');
    expect(screen.getByTestId('reconcile-detail-family')).toHaveTextContent('fam-x');
    expect(screen.getByTestId('reconcile-detail-sis-note')).toBeInTheDocument();

    // The review/escalate action is a local flag — it makes no fetch (no SIS write).
    fireEvent.click(screen.getByTestId('reconcile-sis-escalate'));
    expect(screen.getByTestId('reconcile-sis-escalated')).toBeInTheDocument();

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls.length).toBe(0);
  });
});
