import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SeamView from '../SeamView';

// Acceptance test (CLAUDE §4.2). The seam-to-zero view (FR-2.7, milestone M-2)
// fetches GET /seam — the non-synced CRM seam rows (unsynced / conflict) — and
// renders each with a reconcile button plus the live non-synced COUNT. M-2 is
// "reconcile lowers the non-synced count": clicking reconcile POSTs
// /seam/{id}/reconcile (simulated adapter, INV-9), the row is removed, and the
// count drops. Native fetch only (≤2 runtime deps). The deterministic core owns
// the write (INV-2) — this view records the reconcile request only.

const SEAM_PAYLOAD = [
  { family_id: 'fam-a', seam_status: 'unsynced' },
  { family_id: 'fam-b', seam_status: 'conflict' },
];

function mockSeamList(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => SEAM_PAYLOAD,
    })),
  );
}

describe('SeamView', () => {
  beforeEach(() => {
    mockSeamList();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fetches the seam endpoint (GET) and shows the non-synced count', async () => {
    render(<SeamView />);

    await screen.findByTestId('seam-row-fam-a');
    expect(screen.getByTestId('seam-count')).toHaveTextContent('2');

    const rows = screen.getAllByTestId('seam-row');
    expect(rows).toHaveLength(2);

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/seam$/);
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('reconciling a row POSTs /seam/{id}/reconcile and the non-synced count drops (M-2)', async () => {
    render(<SeamView />);

    await screen.findByTestId('seam-row-fam-a');
    expect(screen.getByTestId('seam-count')).toHaveTextContent('2');

    // The reconcile POST returns the row now synced + applied.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          family_id: 'fam-a',
          seam_status: 'synced',
          applied: true,
        }),
      })),
    );

    fireEvent.click(screen.getByTestId('reconcile-fam-a'));

    // The reconciled row is removed and the count drops to 1.
    await waitFor(() =>
      expect(screen.queryByTestId('seam-row-fam-a')).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('seam-count')).toHaveTextContent('1');
    expect(screen.getByTestId('seam-row-fam-b')).toBeInTheDocument();

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/seam\/fam-a\/reconcile$/);
    expect(init?.method).toBe('POST');
  });
});
