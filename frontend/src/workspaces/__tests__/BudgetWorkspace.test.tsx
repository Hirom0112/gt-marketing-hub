import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import BudgetWorkspace from '../BudgetWorkspace';
import App from '../../App';
import { SessionProvider } from '../../session/SessionContext';

// Acceptance test (CLAUDE §4.2) for the Budget Tracker workspace (B4 task 5). The
// workspace reads the leadership-gated GET /budget (four workstreams + a $365,000
// planned roll-up + a planned-vs-actual burn series), highlights flagged overruns,
// renders a CSS bar burn chart, and exposes a leadership-only add-entry form that
// POSTs /budget/entry → refresh. The fetch layer is stubbed at the native `fetch`
// level (apiFetch wraps it), and the seat is written to localStorage so the real
// SessionProvider scopes the add-entry control — mirroring the B2 nav tests.

const FAKE_TOKEN = 'header.payload.signature';

const BUDGET = {
  workstreams: [
    {
      workstream: 'grassroots',
      planned: 120000,
      actual: 90000,
      committed: 10000,
      remaining: 20000,
      variance: -0.05,
      flagged: false,
    },
    {
      workstream: 'content',
      planned: 95000,
      actual: 110000,
      committed: 5000,
      remaining: -20000,
      variance: 0.16,
      flagged: true,
    },
    {
      workstream: 'guerrilla',
      planned: 80000,
      actual: 60000,
      committed: 8000,
      remaining: 12000,
      variance: -0.1,
      flagged: false,
    },
    {
      workstream: 'ops',
      planned: 70000,
      actual: 65000,
      committed: 2000,
      remaining: 3000,
      variance: 0.0,
      flagged: false,
    },
  ],
  flagged: ['content'],
  rollup: {
    total_planned: 365000,
    total_actual: 325000,
    total_remaining: 15000,
    total_usd: 365000,
  },
  burn: [
    { workstream: 'grassroots', planned: 120000, actual: 90000 },
    { workstream: 'content', planned: 95000, actual: 110000 },
    { workstream: 'guerrilla', planned: 80000, actual: 60000 },
    { workstream: 'ops', planned: 70000, actual: 65000 },
  ],
};

// Seat the demo session (the SessionProvider reads this on mount), exactly as the
// B2 nav tests do — leader/admin gets the add-entry control, operator does not.
function seat(role: 'admin' | 'leader' | 'operator'): void {
  localStorage.setItem(
    'gt_demo_session',
    JSON.stringify({
      role,
      token: FAKE_TOKEN,
      expiresAt: Date.now() + 3_600_000,
      ...(role === 'operator'
        ? {
            agentId: 'a0000000-0000-4000-8000-000000000001',
            agentRank: 1,
            tier: 'closer',
            agentName: 'Riley Carter',
          }
        : {}),
    }),
  );
}

// A url+method-aware fetch stub. GET /budget returns the roll-up; POST /budget/entry
// returns an added row. `budgetStatus`/`entryStatus` let a case force a failure.
function stubApi(opts?: {
  budgetStatus?: number;
  budgetPayload?: unknown;
  entryStatus?: number;
}): ReturnType<typeof vi.fn> {
  const budgetStatus = opts?.budgetStatus ?? 200;
  const budgetPayload = opts?.budgetPayload ?? BUDGET;
  const entryStatus = opts?.entryStatus ?? 200;
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (/\/budget\/entry$/.test(url) && method === 'POST') {
      return Promise.resolve({
        ok: entryStatus >= 200 && entryStatus < 300,
        status: entryStatus,
        json: () => Promise.resolve({ ok: true }),
      } as Response);
    }
    if (/\/budget$/.test(url)) {
      return Promise.resolve({
        ok: budgetStatus >= 200 && budgetStatus < 300,
        status: budgetStatus,
        json: () => Promise.resolve(budgetPayload),
      } as Response);
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    } as Response);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

function renderSeated(): void {
  render(
    <SessionProvider>
      <BudgetWorkspace />
    </SessionProvider>,
  );
}

describe('BudgetWorkspace (B4)', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the four workstream rows + the $365,000 total from GET /budget', async () => {
    seat('admin');
    stubApi();
    renderSeated();

    await screen.findByTestId('budget-table');
    const rows = screen.getAllByTestId('budget-row');
    expect(rows).toHaveLength(4);
    const names = rows.map((r) => r.getAttribute('data-workstream'));
    expect(names).toEqual(['grassroots', 'content', 'guerrilla', 'ops']);
    // The roll-up footer shows the $365,000 planned total.
    expect(screen.getByTestId('budget-rollup')).toHaveTextContent('$365,000');
  });

  it('shows the over-budget highlight on a flagged workstream', async () => {
    seat('admin');
    stubApi();
    renderSeated();

    await screen.findByTestId('budget-table');
    const content = screen
      .getAllByTestId('budget-row')
      .find((r) => r.getAttribute('data-workstream') === 'content') as HTMLElement;
    expect(content.getAttribute('data-flagged')).toBe('true');
    // The signal "over budget" flag chip renders for the flagged row.
    expect(screen.getByTestId('budget-flag-content')).toBeInTheDocument();
    // A non-flagged row carries no flag chip.
    expect(screen.queryByTestId('budget-flag-grassroots')).toBeNull();
    // Variance is rendered as a signed percent.
    expect(screen.getByTestId('budget-variance-content')).toHaveTextContent('+16%');
  });

  it('the burn chart renders a bar per workstream', async () => {
    seat('admin');
    stubApi();
    renderSeated();

    await screen.findByTestId('burn-chart');
    expect(screen.getAllByTestId('burn-row')).toHaveLength(4);
    // Each workstream has a planned + actual bar; the over-budget actual bar
    // exists for the flagged workstream.
    expect(screen.getByTestId('burn-bar-actual-content')).toBeInTheDocument();
    expect(screen.getByTestId('burn-bar-planned-grassroots')).toBeInTheDocument();
  });

  it('the add-entry form POSTs /budget/entry and refreshes (leader seat)', async () => {
    seat('leader');
    const mock = stubApi();
    renderSeated();

    await screen.findByTestId('budget-entry-form');
    fireEvent.change(screen.getByTestId('budget-entry-workstream'), {
      target: { value: 'content' },
    });
    fireEvent.change(screen.getByTestId('budget-entry-kind'), {
      target: { value: 'actual' },
    });
    fireEvent.change(screen.getByTestId('budget-entry-amount'), {
      target: { value: '5000' },
    });
    fireEvent.click(screen.getByTestId('budget-entry-submit'));

    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([u, i]) =>
          /\/budget\/entry$/.test(String(u)) &&
          (i as RequestInit | undefined)?.method === 'POST',
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit).body));
      expect(body).toMatchObject({
        workstream: 'content',
        kind: 'actual',
        amount_usd: 5000,
      });
    });
    // A refresh re-pulls GET /budget (≥2 GETs: initial mount + post-entry).
    const gets = mock.mock.calls.filter(
      ([u, i]) =>
        /\/budget$/.test(String(u)) &&
        ((i as RequestInit | undefined)?.method ?? 'GET') === 'GET',
    );
    expect(gets.length).toBeGreaterThanOrEqual(2);
  });

  it('hides the add-entry form for an operator seat', async () => {
    seat('operator');
    stubApi();
    renderSeated();

    await screen.findByTestId('budget-table');
    expect(screen.queryByTestId('budget-entry-form')).toBeNull();
  });

  it('renders a read-only notice on a 403 entry POST (admin seat, no crash)', async () => {
    seat('admin');
    stubApi({ entryStatus: 403 });
    renderSeated();

    await screen.findByTestId('budget-entry-form');
    fireEvent.change(screen.getByTestId('budget-entry-amount'), {
      target: { value: '100' },
    });
    fireEvent.click(screen.getByTestId('budget-entry-submit'));

    expect(await screen.findByTestId('budget-entry-forbidden')).toBeInTheDocument();
    expect(screen.queryByTestId('budget-entry-form')).toBeNull();
  });

  it('fails safe with a quiet error on a non-OK budget fetch (no crash)', async () => {
    seat('admin');
    stubApi({ budgetStatus: 500, budgetPayload: {} });
    renderSeated();

    expect(await screen.findByTestId('budget-error')).toBeInTheDocument();
    expect(screen.queryByTestId('budget-table')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Nav-entry gating, exercised through the App shell (the nav lives in App). The
// Budget entry is leader + admin only — an operator never sees it (mirrors B2).
function stubShell(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (/\/auth\/demo-token/.test(url)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              access_token: FAKE_TOKEN,
              token_type: 'bearer',
              expires_in: 3600,
            }),
        } as Response);
      }
      if (/\/budget$/.test(url)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(BUDGET),
        } as Response);
      }
      const body = /\/enrollment\/calendar/.test(url)
        ? { month: '2026-06', entries: [] }
        : [];
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response);
    }),
  );
}

describe('Budget nav entry (B4)', () => {
  beforeEach(() => {
    localStorage.clear();
    stubShell();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the Budget nav entry for an admin seat', async () => {
    seat('admin');
    render(<App />);
    expect(await screen.findByTestId('sidebar-nav-budget')).toBeInTheDocument();
  });

  it('shows the Budget nav entry for a leader seat', async () => {
    seat('leader');
    render(<App />);
    expect(await screen.findByTestId('sidebar-nav-budget')).toBeInTheDocument();
  });

  it('hides the Budget nav entry for an operator seat', () => {
    seat('operator');
    render(<App />);
    expect(screen.queryByTestId('sidebar-nav-budget')).toBeNull();
  });
});
