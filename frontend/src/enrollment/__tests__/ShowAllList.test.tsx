import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ShowAllList from '../ShowAllList';
import type { DrillBulk, SortKey } from '../EnrollmentCalendar';

// Acceptance test (CLAUDE §4.2) for the rebuilt Show-all list. The list fetches
// GET /work-queue?scope=active (the small live recovery queue — the loading hang
// is gone) and ?scope=history&limit=200 for History. On the STALL DATE sort it
// groups rows under sticky day headers (day · count · $ at risk, newest first);
// other sorts are a flat ranked list with a stall-date column on every row. A
// real fetch error / empty state shows a clean message, never a perpetual load.

const FAM_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const FAM_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const FAM_C = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const FAM_R = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';

// Two families stall on Jun 13, one on Jun 11 — exercises day grouping.
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
];

// A routed fetch mock — active vs history by the scope query param. Records the
// URLs so the test can assert the list pulls the right scope.
function routedFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string) => {
    const u = String(url);
    const payload = /scope=history/.test(u) ? HISTORY_ROWS : ACTIVE_ROWS;
    return { ok: true, status: 200, json: async () => payload };
  });
}

function urlsCalled(): string[] {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

// A no-op bulk wiring (the bulk routes are the workspace's; this view delegates).
function noopBulk(): DrillBulk {
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
  };
}

function renderList(sort: SortKey): void {
  render(
    <ShowAllList bulk={noopBulk()} sort={sort} onSort={vi.fn()} />,
  );
}

describe('ShowAllList', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fetches the ACTIVE scope and renders the working set fast (no hang)', async () => {
    vi.stubGlobal('fetch', routedFetch());
    renderList('recoverable');

    expect(await screen.findByTestId('show-all-list')).toBeInTheDocument();
    // The active scope is requested (the small live queue, not the full cohort).
    expect(urlsCalled().some((u) => /\/work-queue\?scope=active/.test(u))).toBe(true);
    // The loading placeholder is gone once the rows resolve.
    expect(screen.queryByTestId('show-all-loading')).not.toBeInTheDocument();
    expect(screen.getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
  });

  it('shows a mono stall-date column on every row in a flat (non-date) sort', async () => {
    vi.stubGlobal('fetch', routedFetch());
    renderList('recoverable');

    await screen.findByTestId('show-all-list');
    // Every row carries its stall date even when the sort is not by date.
    expect(screen.getByTestId(`drill-row-date-${FAM_A}`)).toHaveTextContent('Jun 13');
    expect(screen.getByTestId(`drill-row-date-${FAM_C}`)).toHaveTextContent('Jun 11');
    // No day-group headers in a flat sort.
    expect(screen.queryByTestId('day-group-head-2026-06-13')).not.toBeInTheDocument();
  });

  it('GROUPS rows under sticky day headers on the stall-date sort', async () => {
    vi.stubGlobal('fetch', routedFetch());
    renderList('date');

    await screen.findByTestId('show-all-list');

    // Newest day first: Jun 13 group before Jun 11 group.
    const head13 = await screen.findByTestId('day-group-head-2026-06-13');
    const head11 = screen.getByTestId('day-group-head-2026-06-11');
    expect(head13).toBeInTheDocument();
    expect(head11).toBeInTheDocument();
    expect(
      head13.compareDocumentPosition(head11) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // The Jun 13 header reads "Sat Jun 13 · 2 stalls · $96k at risk"
    // (60000 + 36000 = 96000 → $96k).
    expect(head13).toHaveTextContent('Jun 13');
    expect(head13).toHaveTextContent('2 stalls');
    expect(head13).toHaveTextContent('$96k at risk');
    // The Jun 11 header is the single working family.
    expect(head11).toHaveTextContent('1 stall');

    // The two Jun-13 families sit under the Jun 13 group, ranked by recoverable.
    const group13 = screen.getByTestId('day-group-2026-06-13');
    expect(within(group13).getByTestId(`drill-row-${FAM_A}`)).toBeInTheDocument();
    expect(within(group13).getByTestId(`drill-row-${FAM_B}`)).toBeInTheDocument();
  });

  it('switches to the HISTORY scope (capped pull, recovered rows, no checkboxes)', async () => {
    vi.stubGlobal('fetch', routedFetch());
    renderList('recoverable');

    await screen.findByTestId('show-all-list');
    fireEvent.click(screen.getByTestId('scope-history'));

    // History pulls ?scope=history&limit=200 (never streams the long tail).
    await waitFor(() => {
      expect(urlsCalled().some((u) => /scope=history&limit=200/.test(u))).toBe(true);
    });
    expect(await screen.findByTestId(`drill-row-${FAM_R}`)).toBeInTheDocument();
    // History rows can't be bulk-acted — no select-all and no BulkBar dock.
    expect(screen.queryByTestId('select-all')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bulk-bar')).not.toBeInTheDocument();
  });

  it('shows a clean empty message (not a perpetual loading) when a scope is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })),
    );
    renderList('recoverable');

    expect(await screen.findByTestId('show-all-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('show-all-loading')).not.toBeInTheDocument();
  });

  it('shows a clean error message on a failed fetch', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    renderList('recoverable');

    expect(await screen.findByTestId('show-all-error')).toBeInTheDocument();
    expect(screen.queryByTestId('show-all-loading')).not.toBeInTheDocument();
  });
});
