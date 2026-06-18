import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ReconcileTab from '../ReconcileTab';

// Acceptance test (CLAUDE §4.2). The shared Reconcile tab lists HubSpot-vs-dashboard
// seam diffs (GET /seam — the same endpoint HouseholdReconcileBoard/MergeQueue call),
// and an inner "SIS Reconcile" toggle switches to the PAID_NOT_IN_SIS cohort from
// GET /enrollment/sis-buckets. Clicking any row emits onSelectIssue with the issue
// shape. Read-only GETs (INV-2).

const SEAM = [
  { family_id: 'fam-a', seam_status: 'unsynced' },
  { family_id: 'fam-b', seam_status: 'conflict' },
];

const SIS_BUCKETS = {
  buckets: [
    {
      bucket: 'paid_not_in_sis',
      count: 2,
      families: [{ family_id: 'fam-x' }, { family_id: 'fam-y' }],
    },
    { bucket: 'confirmed', count: 0, families: [] },
  ],
};

function mockApi(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (/\/seam$/.test(url))
        return { ok: true, status: 200, json: async () => SEAM };
      if (/\/enrollment\/sis-buckets$/.test(url))
        return { ok: true, status: 200, json: async () => SIS_BUCKETS };
      throw new Error(`unexpected fetch: ${url}`);
    }),
  );
}

describe('ReconcileTab', () => {
  beforeEach(() => mockApi());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('lists the HubSpot seam diffs from GET /seam', async () => {
    render(<ReconcileTab onSelectIssue={() => {}} />);
    await screen.findByTestId('reconcile-seam-rows');

    const rows = screen.getAllByTestId('reconcile-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute('data-family', 'fam-a');
    expect(screen.getByTestId('reconcile-seam-rows')).toHaveTextContent('unsynced');
    expect(screen.getByTestId('reconcile-seam-rows')).toHaveTextContent('conflict');
  });

  it('the SIS Reconcile inner filter shows the PAID_NOT_IN_SIS families', async () => {
    render(<ReconcileTab onSelectIssue={() => {}} />);
    await screen.findByTestId('reconcile-seam-rows');

    // Toggle to the SIS view.
    fireEvent.click(screen.getByText('SIS Reconcile'));

    const sisRows = await screen.findAllByTestId('reconcile-sis-row');
    expect(sisRows).toHaveLength(2);
    expect(sisRows[0]).toHaveAttribute('data-family', 'fam-x');
    expect(sisRows[1]).toHaveAttribute('data-family', 'fam-y');
  });

  it('clicking a seam row fires onSelectIssue with a seam issue', async () => {
    const onSelectIssue = vi.fn();
    render(<ReconcileTab onSelectIssue={onSelectIssue} />);
    await screen.findByTestId('reconcile-seam-rows');

    fireEvent.click(screen.getAllByTestId('reconcile-row')[1]);
    expect(onSelectIssue).toHaveBeenCalledWith({
      kind: 'seam',
      family_id: 'fam-b',
      status: 'conflict',
      seam_status: 'conflict',
    });
  });

  it('clicking a SIS row fires onSelectIssue with a sis issue', async () => {
    const onSelectIssue = vi.fn();
    render(<ReconcileTab onSelectIssue={onSelectIssue} />);
    await screen.findByTestId('reconcile-seam-rows');

    fireEvent.click(screen.getByText('SIS Reconcile'));
    const sisRows = await screen.findAllByTestId('reconcile-sis-row');
    fireEvent.click(sisRows[0]);

    expect(onSelectIssue).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'sis', family_id: 'fam-x' }),
    );
  });
});
