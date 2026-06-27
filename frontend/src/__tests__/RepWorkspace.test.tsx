import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { DEMO_AGENTS } from '../LoginPage';

// Redesign acceptance (briefs/gt-pulse-sales-agent-dashboard-redesign.md). This
// SUPERSEDES the old M2 "RepWorkspace subset" test: an agent seat now lands on the
// redesigned AgentDashboard (4-metric KPI strip + daily-motivation banner + the
// Leads/Triage/Students/Reconcile/KPI-Dashboard tabs), and an admin seat on the
// AdminDashboard. The enduring contract this still pins:
//   · the agent gets the AGENT surface, the admin gets the ADMIN surface (role gate);
//   · the agent's /work-queue read rides THROUGH apiFetch carrying the signed
//     Authorization: Bearer token (owner-scoped server-side by the verified
//     app_metadata.agent_id — the IDOR defense, M1 — no client-side filter).
// The deep close-panel/draft flows moved into the shared DetailPanel/AiDrafts and
// are covered by their own unit tests; this file asserts the shell landing only.

const AGENT = DEMO_AGENTS[0]!; // Riley Carter — closer seat
const FAKE_TOKEN = 'header.payload.signature';

const QUEUE_ROWS = [
  {
    family_id: 'f-rep-1',
    display_name: 'Rivera Household',
    value: 10474,
    contact_status: 'overdue',
    recovery_state: 'stalled',
    current_stage: 'application',
    assigned_rep_id: AGENT.id,
    stall_date: '2026-06-10T00:00:00Z',
    num_children: 1,
    funding_type: 'tefa_standard',
    recoverable_now: 8000,
    last_contact_at: null,
  },
];

const AGENT_KPIS = {
  leads_assigned: 4,
  contacts_made: 5,
  follow_ups_completed: 2,
  appointments_booked: 2,
  applications_started: 3,
  applications_completed: 1,
  conversion_rate: 0.25,
};

interface Call {
  url: string;
  init?: RequestInit;
}
let calls: Call[] = [];

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function installFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes('/work-queue'))
        return Promise.resolve(jsonResponse(QUEUE_ROWS));
      if (url.includes('/enrollment/agent-kpis'))
        return Promise.resolve(jsonResponse(AGENT_KPIS));
      if (url.includes('/enrollment/leads-calendar'))
        return Promise.resolve(jsonResponse({ month: '2026-06', days: [] }));
      if (url.includes('/enrollment/calendar'))
        return Promise.resolve(jsonResponse({ month: '2026-06', entries: [] }));
      if (url.includes('/students'))
        return Promise.resolve(jsonResponse([]));
      if (url.match(/\/families(\?|$)/))
        return Promise.resolve(
          jsonResponse([{ family_id: 'f-rep-1', display_name: 'Rivera Household' }]),
        );
      return Promise.resolve(jsonResponse({}));
    }),
  );
}

function enterAsRep(): void {
  localStorage.setItem(
    'gt_demo_session',
    JSON.stringify({
      role: 'operator',
      token: FAKE_TOKEN,
      expiresAt: Date.now() + 3_600_000,
      agentId: AGENT.id,
      agentRank: AGENT.rank,
      tier: AGENT.tier,
      agentName: AGENT.name,
    }),
  );
  render(<App />);
}

function enterAsAdmin(): void {
  localStorage.setItem('gt_demo_session', JSON.stringify({ role: 'admin' }));
  render(<App />);
}

describe('Agent seat lands on the redesigned AgentDashboard', () => {
  beforeEach(() => {
    localStorage.clear();
    installFetch();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the AgentDashboard (not the AdminDashboard) for an agent seat', async () => {
    enterAsRep();
    expect(await screen.findByTestId('agent-dashboard')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-dashboard')).toBeNull();
  });

  it('renders the 4-metric KPI strip + the daily-motivation banner', async () => {
    enterAsRep();
    await screen.findByTestId('agent-dashboard');
    for (const label of ['BOOKED', 'CONTACTED', 'OVERDUE', 'ACTIVE']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByTestId('dashboard-banner')).toBeInTheDocument();
  });

  it('renders the 5 work-area tabs', async () => {
    enterAsRep();
    await screen.findByTestId('agent-dashboard');
    for (const label of [
      'Leads',
      'Triage',
      'Students',
      'Reconcile',
      'KPI Dashboard',
    ]) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
  });

  it('reads /work-queue THROUGH apiFetch carrying the bearer token (owner-scoped, no client filter)', async () => {
    enterAsRep();
    await screen.findByTestId('agent-dashboard');
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes('/work-queue'))).toBe(true);
    });
    const wq = calls.find((c) => c.url.includes('/work-queue'))!;
    const headers = wq.init?.headers as Record<string, string> | undefined;
    expect(headers?.['Authorization']).toBe(`Bearer ${FAKE_TOKEN}`);
  });

  it('regression: an ADMIN seat lands on the AdminDashboard (3-metric strip), not the agent one', async () => {
    enterAsAdmin();
    expect(await screen.findByTestId('admin-dashboard')).toBeInTheDocument();
    const strip = screen.getByTestId('dashboard-kpi-strip');
    expect(within(strip).getByText('ACTIVE STALLS')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-dashboard')).toBeNull();
  });
});
