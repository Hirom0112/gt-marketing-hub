import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import HistoryList from '../HistoryList';

// Acceptance test (CLAUDE §4.2) for the REDESIGNED History archive (S13). A
// DIFFERENT surface from Triage: its own HistoryRow grammar, sub-tabs (All /
// Recovered / Dismissed with counts) that swap the columns, a recovered row that
// shows a detected-outcome chip + recovered $ + resolved date and NO operator, a
// dismissed row that shows a reason chip + operator + date, a name search, and NO
// checkbox / NO bulk / NO scope dial. It reads GET /work-queue?scope=history.

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
    // The W-redesign history fields (recovered).
    recovered_outcome: 'forms_cleared',
    resolved_at: '2026-06-03T09:00:00Z',
    dismiss_reason: null,
    dismissed_by: null,
    dismissed_at: null,
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
    // The W-redesign history fields (dismissed).
    recovered_outcome: null,
    resolved_at: null,
    dismiss_reason: 'Bad fit',
    dismissed_by: 'jordan',
    dismissed_at: '2026-05-25T09:00:00Z',
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

describe('HistoryList (S13 redesign)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('pulls the history scope (capped) and renders its OWN HistoryRow grammar', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    expect(await screen.findByTestId('history-list')).toBeInTheDocument();
    await waitFor(() => {
      expect(urlsCalled().some((u) => /scope=history&limit=200/.test(u))).toBe(
        true,
      );
    });
    // History uses history-row-*, NOT the triage drill-row-* grammar.
    expect(screen.getByTestId(`history-row-${FAM_R}`)).toBeInTheDocument();
    expect(screen.getByTestId(`history-row-${FAM_S}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`drill-row-${FAM_R}`)).not.toBeInTheDocument();
  });

  it('is control-less — no checkbox, no bulk, no scope dial', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    expect(screen.queryByTestId('triage-scope')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scope-day')).not.toBeInTheDocument();
    expect(screen.queryByTestId('select-all')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('bulk-rail-select-all'),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();
    // No checkbox anywhere on a history row.
    expect(
      screen.queryByTestId(`drill-row-check-${FAM_R}`),
    ).not.toBeInTheDocument();
  });

  it('classifies a confirmed-lost family as closed-out, never recovered/won (A-35)', async () => {
    // A lost row carries recovery_state='lost' with NO recovered/dismiss fields
    // (the read path only stamps those for recovered/dismissed). It must not be
    // mis-bucketed as recovered (won) — lost is closed-out, not a win.
    const lostRow = {
      ...HISTORY_ROWS[0],
      family_id: '00000000-0000-4000-8000-0000000000ff',
      display_name: 'The Lost Family',
      recovery_state: 'lost',
      recovered_outcome: null,
      resolved_at: null,
      dismiss_reason: null,
      dismissed_by: null,
      dismissed_at: null,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => [HISTORY_ROWS[0], lostRow],
      })),
    );
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    // Only the genuinely-recovered row counts as recovered (won).
    expect(screen.getByTestId('history-recovered-count')).toHaveTextContent(
      '1',
    );
    // The lost family lands in the closed-out (dismissed-side) bucket.
    expect(screen.getByTestId('history-dismissed-count')).toHaveTextContent(
      '1',
    );
  });

  it('sub-tabs carry counts and swap the columns (recovered vs dismissed)', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    expect(screen.getByTestId('history-subtabs')).toBeInTheDocument();
    expect(screen.getByTestId('history-tab-recovered')).toHaveTextContent(
      'recovered · 1',
    );
    expect(screen.getByTestId('history-tab-dismissed')).toHaveTextContent(
      'dismissed · 1',
    );

    // Recovered tab: only the recovered row.
    fireEvent.click(screen.getByTestId('history-tab-recovered'));
    expect(screen.getByTestId(`history-row-${FAM_R}`)).toBeInTheDocument();
    expect(
      screen.queryByTestId(`history-row-${FAM_S}`),
    ).not.toBeInTheDocument();

    // Dismissed tab: only the dismissed row.
    fireEvent.click(screen.getByTestId('history-tab-dismissed'));
    expect(screen.getByTestId(`history-row-${FAM_S}`)).toBeInTheDocument();
    expect(
      screen.queryByTestId(`history-row-${FAM_R}`),
    ).not.toBeInTheDocument();
  });

  it('a recovered row shows the detected-outcome chip + recovered $ + NO operator', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    expect(screen.getByTestId(`history-outcome-${FAM_R}`)).toHaveTextContent(
      'forms cleared',
    );
    expect(screen.getByTestId(`history-amount-${FAM_R}`)).toHaveTextContent(
      '$10,474',
    );
    // Resolved date (Jun 3), not the stall date.
    expect(screen.getByTestId(`history-when-${FAM_R}`)).toHaveTextContent(
      'Jun 3',
    );
    // The system detected it — no operator cell on a recovered row.
    expect(
      screen.queryByTestId(`history-operator-${FAM_R}`),
    ).not.toBeInTheDocument();
  });

  it('a dismissed row shows the reason chip + the operator + the dismissed date', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    expect(screen.getByTestId(`history-reason-${FAM_S}`)).toHaveTextContent(
      'Bad fit',
    );
    expect(screen.getByTestId(`history-operator-${FAM_S}`)).toHaveTextContent(
      'jordan',
    );
    expect(screen.getByTestId(`history-when-${FAM_S}`)).toHaveTextContent(
      'May 25',
    );
    // A "set aside at {stage}" subline.
    expect(screen.getByTestId(`history-row-${FAM_S}`)).toHaveTextContent(
      'set aside at apply',
    );
  });

  it('the name search filters the loaded page (client-side)', async () => {
    vi.stubGlobal('fetch', historyFetch());
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    fireEvent.change(screen.getByTestId('history-search'), {
      target: { value: 'reyes' },
    });
    expect(screen.getByTestId(`history-row-${FAM_R}`)).toBeInTheDocument();
    expect(
      screen.queryByTestId(`history-row-${FAM_S}`),
    ).not.toBeInTheDocument();
  });

  it('degrades gracefully when the backend history fields are null', async () => {
    // Older server: no recovered_outcome / resolved_at / dismiss_* fields.
    const legacy = HISTORY_ROWS.map((r) => ({
      ...r,
      recovered_outcome: null,
      resolved_at: null,
      dismiss_reason: null,
      dismissed_by: null,
      dismissed_at: null,
    }));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => legacy })),
    );
    render(<HistoryList />);

    await screen.findByTestId('history-list');
    // Still differentiated by recovery_state, still renders (falls back to
    // stall_date for the date + a generic outcome/reason).
    expect(screen.getByTestId(`history-row-${FAM_R}`)).toBeInTheDocument();
    expect(screen.getByTestId(`history-outcome-${FAM_R}`)).toHaveTextContent(
      'recovered',
    );
    expect(screen.getByTestId(`history-when-${FAM_R}`)).toHaveTextContent(
      'May 30',
    );
    expect(screen.getByTestId(`history-row-${FAM_S}`)).toHaveClass(
      'outcome-dismissed',
    );
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
