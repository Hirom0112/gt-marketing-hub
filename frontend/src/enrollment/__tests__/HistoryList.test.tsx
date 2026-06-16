import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import HistoryList from '../HistoryList';

// Acceptance test (CLAUDE §4.2) for the History view (S13 W1, A-22). Recovered/
// dismissed families are EVICTED out of the triage list into their OWN clearly-
// separate view — an audit/lookback dataset, NOT the triage worklist at any
// scope. It reads GET /work-queue?scope=history&limit=200 and is strictly
// READ-ONLY: no scope dial, no select-all, no BulkBar, no recover/capture/dismiss.

const FAM_R = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const FAM_S = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';

const HISTORY_ROWS = [
  {
    family_id: FAM_R,
    display_name: 'The Reyes Family',
    current_stage: 'tuition',
    score: 0.8,
    recoverability: 0.9,
    value: 10474,
    stall_date: '2026-05-30T09:00:00Z',
    recoverable_now: 9000,
    freshness: 0.4,
    contact_status: 'closed',
    last_contact_at: '2026-06-01T09:00:00Z',
    recovery_state: 'recovered',
  },
  {
    family_id: FAM_S,
    display_name: 'The Singh Family',
    current_stage: 'apply',
    score: 0.3,
    recoverability: 0.2,
    value: 30000,
    stall_date: '2026-05-20T09:00:00Z',
    recoverable_now: 0,
    freshness: 0.1,
    contact_status: 'closed',
    last_contact_at: null,
    recovery_state: 'dismissed',
  },
];

function historyFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => HISTORY_ROWS,
  }));
}

function urlsCalled(): string[] {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

describe('HistoryList (S13 W1)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('pulls the history scope (capped) and renders recovered/dismissed rows', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    expect(await screen.findByTestId('history-list')).toBeInTheDocument();
    await waitFor(() => {
      expect(urlsCalled().some((u) => /scope=history&limit=200/.test(u))).toBe(true);
    });
    expect(screen.getByTestId(`drill-row-${FAM_R}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_S}`)).toBeInTheDocument();
  });

  it('is strictly READ-ONLY — no scope dial, no select-all, no BulkBar', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    // None of the triage/bulk affordances exist here (the IA separation).
    expect(screen.queryByTestId('triage-scope')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scope-day')).not.toBeInTheDocument();
    expect(screen.queryByTestId('select-all')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();
    // The row's checkbox is inert (no onToggle wired) — clicking it never selects
    // for a bulk action in this read-only view.
    const check = screen.getByTestId(`drill-row-check-${FAM_R}`);
    expect(check).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(check);
    expect(check).toHaveAttribute('aria-checked', 'false');
  });

  it('shows a clean empty message when history is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })),
    );
    render(<HistoryList />);

    expect(await screen.findByTestId('history-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('history-loading')).not.toBeInTheDocument();
  });

  it('shows a clean error message on a failed fetch', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    render(<HistoryList />);

    expect(await screen.findByTestId('history-error')).toBeInTheDocument();
    expect(screen.queryByTestId('history-loading')).not.toBeInTheDocument();
  });
});
