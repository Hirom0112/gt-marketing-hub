import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TriageList, { type TriageScope } from '../TriageList';
import type { DrillBulk, SortKey } from '../EnrollmentCalendar';

// Acceptance test (CLAUDE §4.2) for the scoped triage list (S13 W1, A-22). The
// triage list is the OVERFLOW CONSOLE — the unscoped end of the calendar's drill,
// NOT a second surface. It fetches GET /work-queue?scope=active ONCE and scopes
// CLIENT-SIDE by a stall_date window via a Day / Week / All dial (no per-scope
// endpoints). Default sort = recoverable-now at every scope; bulk always
// attached. The S12 date-sort + day-grouping are GONE (the calendar owns "by
// date"); the stall_date stays as an informational column on every row. History
// is NOT a scope here — it lives in its own view (see HistoryList.test.tsx).

const FAM_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const FAM_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const FAM_C = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

// Two families stall on Jun 13, one on Jun 11 — exercises the day/week windows.
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
    contact_status: 'working',
    last_contact_at: '2026-06-12T09:00:00Z',
    recovery_state: 'working',
  },
];

// All three stall in the same week (Jun 7–13 2026, Sunday-anchored), so a Week
// scope anchored in that week includes them all; Day=Jun 13 includes only A+B.
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

describe('TriageList (S13 W1)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fetches the ACTIVE scope ONCE and renders the wave (no hang, no per-scope call)', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all' });

    expect(await screen.findByTestId('triage-list')).toBeInTheDocument();
    // Exactly the active scope is requested — scoping is a client filter (A-22).
    const calls = urlsCalled();
    expect(calls.some((u) => /\/work-queue\?scope=active/.test(u))).toBe(true);
    expect(calls.filter((u) => /\/work-queue/.test(u))).toHaveLength(1);
    expect(screen.queryByTestId('triage-loading')).not.toBeInTheDocument();
    // All-scope shows all three families.
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_C}`)).toBeInTheDocument();
  });

  it('defaults to recoverable-now ranking and keeps a stall-date column on every row', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all', sort: 'recoverable' });

    await screen.findByTestId('triage-list');
    // Recoverable-now order: A (50k) before B (30k) before C (8k).
    const rows = screen.getAllByTestId(/^drill-row-[a-f0-9-]+$/);
    expect(rows[0]).toHaveAttribute('data-testid', `drill-row-${FAM_A}`);
    expect(rows[2]).toHaveAttribute('data-testid', `drill-row-${FAM_C}`);
    // The informational stall-date column is present on every row.
    expect(screen.getByTestId(`drill-row-date-${FAM_A}`)).toHaveTextContent('Jun 13');
    expect(screen.getByTestId(`drill-row-date-${FAM_C}`)).toHaveTextContent('Jun 11');
  });

  it('offers NO date-sort and renders NO day-group headers (the calendar owns date)', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all' });

    await screen.findByTestId('triage-list');
    // The sort dropdown no longer has a "stall date" option.
    const sortSelect = screen.getByTestId('list-sort') as HTMLSelectElement;
    const optionValues = Array.from(sortSelect.options).map((o) => o.value);
    expect(optionValues).not.toContain('date');
    expect(optionValues).toEqual(['recoverable', 'value', 'score', 'recency']);
    // No S12 day-group headers anywhere.
    expect(screen.queryByTestId('day-group-head-2026-06-13')).not.toBeInTheDocument();
  });

  it('DAY scope windows the active set to a single stall day', async () => {
    vi.stubGlobal('fetch', activeFetch());
    // Anchored on Jun 13 → only the two Jun-13 families, not the Jun-11 one.
    renderList({ scope: 'day', anchorDate: '2026-06-13T00:00:00Z' });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_B}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`drill-row-${FAM_C}`)).not.toBeInTheDocument();
  });

  it('WEEK scope widens the Day window to the 7-day window around the anchor', async () => {
    vi.stubGlobal('fetch', activeFetch());
    // Jun 7–13 2026 is one Sunday-anchored week → all three families.
    renderList({ scope: 'week', anchorDate: '2026-06-13T00:00:00Z' });

    await screen.findByTestId('triage-list');
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_B}`)).toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_C}`)).toBeInTheDocument();
  });

  it('the scope dial (Day/Week/All) widens the drill without leaving the list', async () => {
    vi.stubGlobal('fetch', activeFetch());
    const onScopeChange = vi.fn();
    renderList({
      scope: 'day',
      anchorDate: '2026-06-13T00:00:00Z',
      onScopeChange,
    });

    await screen.findByTestId('triage-list');
    // The dial is present and reflects the Day scope.
    expect(screen.getByTestId('scope-day')).toHaveAttribute('aria-pressed', 'true');
    // Widen to All — the workspace is told to drop the anchor.
    fireEvent.click(screen.getByTestId('scope-all'));
    expect(onScopeChange).toHaveBeenCalledWith('all', undefined);
  });

  it('keeps the bulk bar ALWAYS attached (select-all + dock) at every scope', async () => {
    vi.stubGlobal('fetch', activeFetch());
    const onSelectAll = vi.fn();
    renderList({ scope: 'all', bulk: noopBulk({ onSelectAll }) });

    await screen.findByTestId('triage-list');
    // Select-all is wired (bulk attached everywhere — A-22).
    fireEvent.click(screen.getByTestId('select-all'));
    expect(onSelectAll).toHaveBeenCalledWith([FAM_A, FAM_B, FAM_C]);
  });

  it('renders the gate partition + BulkBar dock when a selection is live', async () => {
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
  });

  it('shows a clean empty message (not a perpetual load) when a scope has no stalls', async () => {
    vi.stubGlobal('fetch', activeFetch());
    // A day with no stalls in the active set.
    renderList({ scope: 'day', anchorDate: '2026-01-01T00:00:00Z' });

    expect(await screen.findByTestId('triage-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('triage-loading')).not.toBeInTheDocument();
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

  it('coerces a stray date sort to recoverable-now (the date sort no longer exists)', async () => {
    vi.stubGlobal('fetch', activeFetch());
    renderList({ scope: 'all', sort: 'date' });

    await screen.findByTestId('triage-list');
    // Falls back to recoverable-now ordering: A (50k) first.
    const rows = screen.getAllByTestId(/^drill-row-[a-f0-9-]+$/);
    expect(rows[0]).toHaveAttribute('data-testid', `drill-row-${FAM_A}`);
    // And the dropdown reflects the coerced value, not 'date'.
    const sortSelect = screen.getByTestId('list-sort') as HTMLSelectElement;
    expect(sortSelect.value).toBe('recoverable');
  });
});
