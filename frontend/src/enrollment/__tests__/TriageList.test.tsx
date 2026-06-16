import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TriageList, { type TriageScope } from '../TriageList';
import type { DrillBulk, SortKey } from '../EnrollmentCalendar';

// Acceptance test (CLAUDE §4.2) for the REDESIGNED triage worklist (S13). The
// list is money-first: recoverable_now is the HERO cell, recency is a left-edge
// rail (no pill column), there is NO rank/score column or sort. A Day/Week scope
// with no anchor windows on the most-recent stall day in the loaded set (NEVER
// the wall clock — the Day-scope bug fix), and empty states carry one-tap
// remedies (never a blank panel). Bulk is always attached.

const FAM_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const FAM_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const FAM_C = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

const ACTIVE_ROWS = [
  {
    family_id: FAM_A,
    display_name: 'The Alvarez Family',
    current_stage: 'enroll',
    score: 0.91,
    recoverability: 0.95,
    value: 60000,
    stall_date: '2026-06-13T09:00:00Z',
    recoverable_now: 50000,
    freshness: 0.9,
    contact_status: 'overdue',
    last_contact_at: null,
    recovery_state: 'stalled',
  },
  {
    family_id: FAM_B,
    display_name: 'The Bauer Family',
    current_stage: 'apply',
    score: 0.74,
    recoverability: 0.6,
    value: 36000,
    stall_date: '2026-06-13T12:00:00Z',
    recoverable_now: 30000,
    freshness: 0.95,
    contact_status: 'fresh',
    last_contact_at: null,
    recovery_state: 'stalled',
  },
  {
    family_id: FAM_C,
    display_name: 'The Cho Family',
    current_stage: 'enroll',
    score: 0.5,
    recoverability: 0.5,
    value: 10000,
    stall_date: '2026-06-11T09:00:00Z',
    recoverable_now: 8000,
    freshness: 0.5,
    contact_status: 'followed_up',
    last_contact_at: '2026-06-12T09:00:00Z',
    recovery_state: 'working',
  },
];

function activeFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ACTIVE_ROWS,
  }));
}

function urlsCalled(): string[] {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

function noopBulk(overrides: Partial<DrillBulk> = {}): DrillBulk {
  return {
    selected: new Set<string>(),
    onToggle: vi.fn(),
    onSelectAll: vi.fn(),
    onClear: vi.fn(),
    onNudge: vi.fn(),
    onCapture: vi.fn(),
    onDismissStart: vi.fn(),
    pendingDismiss: false,
    reasons: [],
    onDismiss: vi.fn(),
    onCancelDismiss: vi.fn(),
    ...overrides,
  };
}

function renderList(
  opts: {
    scope?: TriageScope;
    anchorDate?: string;
    sort?: SortKey;
    onScopeChange?: (s: TriageScope, a?: string) => void;
    bulk?: DrillBulk;
  } = {},
): void {
  render(
    <TriageList
      scope={opts.scope ?? 'all'}
      anchorDate={opts.anchorDate}
      onScopeChange={opts.onScopeChange ?? vi.fn()}
      bulk={opts.bulk ?? noopBulk()}
      sort={opts.sort ?? 'recoverable'}
      onSort={vi.fn()}
    />,
  );
}

describe('TriageList (S13 redesign)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fetches the ACTIVE scope ONCE and renders the wave (no per-scope call)', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all' });

    expect(await screen.findByTestId('triage-list')).toBeInTheDocument();
    const calls = urlsCalled();
    expect(calls.some((u) => /\/work-queue\?scope=active/.test(u))).toBe(true);
    expect(calls.filter((u) => /\/work-queue/.test(u))).toHaveLength(1);
    expect(screen.queryByTestId('triage-loading')).not.toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_C}`)).toBeInTheDocument();
  });

  it('shows recoverable_now as the HERO cell on every row', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all' });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId(`drill-row-recoverable-${FAM_A}`)).toHaveTextContent(
      '$50,000',
    );
    expect(screen.getByTestId(`drill-row-recoverable-${FAM_C}`)).toHaveTextContent(
      '$8,000',
    );
    // The tier-1 readout sums recoverable_now (50k+30k+8k = $88,000), not value.
    expect(screen.getByTestId('triage-readout-money')).toHaveTextContent(
      '$88,000',
    );
  });

  it('defaults to recoverable-now ranking and keeps a stall-date column', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all', sort: 'recoverable' });

    await screen.findByTestId('triage-list');
    const rows = screen.getAllByTestId(/^drill-row-[a-f0-9-]+$/);
    expect(rows[0]).toHaveAttribute('data-testid', `drill-row-${FAM_A}`);
    expect(rows[2]).toHaveAttribute('data-testid', `drill-row-${FAM_C}`);
    expect(screen.getByTestId(`drill-row-date-${FAM_A}`)).toHaveTextContent('Jun 13');
    expect(screen.getByTestId(`drill-row-date-${FAM_C}`)).toHaveTextContent('Jun 11');
  });

  it('recency is a RAIL on the row, not a pill column; the sort drops score+date', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all' });

    await screen.findByTestId('triage-list');
    // The overdue row carries the saturated signal rail (the pill is gone).
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toHaveClass('rail-overdue');
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).not.toHaveTextContent(
      'Overdue',
    );
    const sortSelect = screen.getByTestId('list-sort') as HTMLSelectElement;
    const optionValues = Array.from(sortSelect.options).map((o) => o.value);
    expect(optionValues).not.toContain('date');
    expect(optionValues).not.toContain('score');
    expect(optionValues).toEqual(['recoverable', 'value', 'recency']);
  });

  it('DAY scope windows the active set to a single stall day', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'day', anchorDate: '2026-06-13T00:00:00Z' });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_B}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`drill-row-${FAM_C}`)).not.toBeInTheDocument();
  });

  it('a Day scope with NO anchor windows on the LATEST stall day in the set (not the clock)', async () => {
    vi.stubGlobal('fetch', activeFetch());
    // No anchorDate — the bug used to fall back to Date.now() (2026-06-16, outside
    // the synthetic range) and show 0 rows. The fix anchors to the max stall day
    // in the loaded set (Jun 13) → FAM_A + FAM_B, never blank.
    renderList({ scope: 'day', anchorDate: undefined });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_B}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`drill-row-${FAM_C}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId('triage-empty')).not.toBeInTheDocument();
  });

  it('WEEK scope widens the Day window to the 7-day window around the anchor', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'week', anchorDate: '2026-06-13T00:00:00Z' });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_B}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_C}`)).toBeInTheDocument();
  });

  it('the segmented scope dial widens the drill without leaving the list', async () => {
    vi.stubGlobal('fetch', activeFetch());
    const onScopeChange = vi.fn();
    renderList({
      scope: 'day',
      anchorDate: '2026-06-13T00:00:00Z',
      onScopeChange,
    });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId('triage-scope')).toBeInTheDocument();
    expect(screen.getByTestId('scope-day')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('scope-all'));
    expect(onScopeChange).toHaveBeenCalledWith('all', undefined);
  });

  it('keeps bulk ALWAYS attached — the select-all rail wires onSelectAll', async () => {
    vi.stubGlobal('fetch', activeFetch());
    const onSelectAll = vi.fn();
    renderList({ scope: 'all', bulk: noopBulk({ onSelectAll }) });

    await screen.findByTestId('triage-list');
    // 0 selected → the thin select-all rail (the dock absorbs select-all).
    fireEvent.click(screen.getByTestId('bulk-rail-select-all'));
    expect(onSelectAll).toHaveBeenCalledWith([FAM_A, FAM_B, FAM_C]);
  });

  it('renders the gate partition + BulkBar dock with selection recoverable sum', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({
      scope: 'all',
      bulk: noopBulk({
        selected: new Set([FAM_A]),
        partition: { willSend: 1, blocked: 0 },
      }),
    });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId('bulk-bar')).toBeInTheDocument();
    expect(screen.getByTestId('bulk-bar-partition')).toBeInTheDocument();
    // The dock shows the selection's recoverable total.
    expect(screen.getByTestId('bulk-bar-recoverable')).toHaveTextContent('$50,000');
  });

  it('an empty SCOPE shows widen remedies (not a blank panel, never "today")', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'day', anchorDate: '2026-01-01T00:00:00Z' });

    expect(await screen.findByTestId('triage-empty')).toBeInTheDocument();
    expect(screen.getByTestId('triage-empty-scope')).toBeInTheDocument();
    expect(screen.getByTestId('triage-widen-week')).toBeInTheDocument();
    expect(screen.getByTestId('triage-show-all')).toBeInTheDocument();
    expect(screen.getByTestId('triage-empty')).not.toHaveTextContent(/today/i);
    expect(screen.queryByTestId('triage-loading')).not.toBeInTheDocument();
  });

  it('the recency FILTER empties to a clear-filter remedy when no row matches', async () => {
    // A dataset where filtering to 'fresh' yields nothing (all overdue).
    const overdueOnly = ACTIVE_ROWS.map((r) => ({
      ...r,
      contact_status: 'overdue',
    }));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => overdueOnly })),
    );
    renderList({ scope: 'all' });

    await screen.findByTestId('triage-list');
    fireEvent.click(screen.getByTestId('recency-fresh'));
    expect(screen.getByTestId('triage-empty-filter')).toBeInTheDocument();
    expect(screen.getByTestId('triage-clear-filter')).toBeInTheDocument();
  });

  it('an empty ALL scope shows the calm rest state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })),
    );
    renderList({ scope: 'all' });

    expect(await screen.findByTestId('triage-empty-rest')).toBeInTheDocument();
    expect(screen.getByText(/the wave is clear/i)).toBeInTheDocument();
  });

  it('shows a clean error message on a failed fetch', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    renderList({ scope: 'all' });

    expect(await screen.findByTestId('triage-error')).toBeInTheDocument();
    expect(screen.queryByTestId('triage-loading')).not.toBeInTheDocument();
  });

  it('coerces a stray score/date sort to recoverable-now', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all', sort: 'score' });

    await screen.findByTestId('triage-list');
    const rows = screen.getAllByTestId(/^drill-row-[a-f0-9-]+$/);
    expect(rows[0]).toHaveAttribute('data-testid', `drill-row-${FAM_A}`);
    const sortSelect = screen.getByTestId('list-sort') as HTMLSelectElement;
    expect(sortSelect.value).toBe('recoverable');
  });
});
