import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { DEMO_AGENTS } from '../LoginPage';

// M2 acceptance (MULTI_AGENT_COCKPIT.md §4/§5, PLAN.md M2 R1/R2). The founder's
// requirement, verbatim: "make sure the sales agent has only 1 dashboard where
// they can see everything needed … they do not need all that" (the admin view).
//
// A logged-in REP gets ONE simple view: the owner-scoped "My Queue" (the EXISTING
// TriageList, with its recency facets) + the close-panel stack, fronted by a
// rep-scoped SituationBar (their book). The ADMIN-ONLY surfaces — Calendar,
// Students board, Reconcile board, MergeQueue, the Intake/view toggle, Security —
// are ABSENT. There is NO parallel write path: acting rides the SAME gated
// POST /proposals/{id}/decision the admin ActionPanel already uses.
//
// Data is owner-scoped SERVER-SIDE: apiFetch attaches X-Demo-Role: agent +
// X-Demo-Agent-Id from the session, and the backend clamps the agent to its own
// assigned_rep_id (the IDOR defense, M1). This test does NOT assert the clamp
// (that's the backend's contract) — it asserts the rep reads /work-queue THROUGH
// apiFetch (so the headers ride) and renders the subset composition.

const AGENT = DEMO_AGENTS[0]!; // Riley Carter — closer seat

// One synthetic family on the rep's owner-scoped queue (no PII, INV-1).
const QUEUE_ROWS = [
  {
    family_id: 'f-rep-1',
    display_name: 'Rivera Household',
    current_stage: 'application',
    score: 0.7,
    recoverability: 0.82,
    value: 10474,
    num_children: 1,
    funding_type: 'tefa_standard',
    stall_date: '2026-06-10T00:00:00Z',
    recoverable_now: 8000,
    freshness: 0.5,
    contact_status: 'overdue',
    recovery_state: 'stalled',
    last_contact_at: null,
  },
];

const FAMILIES = [{ family_id: 'f-rep-1', display_name: 'Rivera Household' }];

// A deal_view payload shaped to DealViewData so DealView renders its root section.
const DEAL = {
  display_name: 'Rivera Household',
  stall_reason: 'No response',
  funding_type: 'tefa_standard',
  map_score: null,
  attribution_source: 'organic',
  crm_seam_status: 'in_sync',
  completion_pct: 0.4,
  forms_signed: 2,
  forms_total: 6,
  next_unsigned_form: 'enrollment_agreement',
  contact_status: 'overdue',
  recovery_state: 'stalled',
};

// Route the cockpit's mount-time fetches to synthetic payloads. We capture every
// (url, init) so we can assert the rep's reads carried the X-Demo-Agent-Id header
// (owner-scoped via apiFetch — no client-side filtering, no unscoped read).
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
      // The admin EnrollmentCalendar reads /enrollment/calendar → { entries }.
      if (url.includes('/enrollment/calendar'))
        return Promise.resolve(jsonResponse({ month: '2026-06', entries: [] }));
      // The family roster (RepWorkspace + EnrollmentWorkspace mount fetch).
      if (url.match(/\/families(\?|$)/))
        return Promise.resolve(jsonResponse(FAMILIES));
      // DealView reads /families/{id} → { deal_view }.
      if (url.match(/\/families\/[^/]+$/))
        return Promise.resolve(jsonResponse({ deal_view: DEAL }));
      // Any other GET the panel makes (notes, funding, close-tips, drop-off,
      // crm/status) → an empty-but-OK shape so the components reach a render.
      if (url.includes('/notes')) return Promise.resolve(jsonResponse([]));
      if (url.includes('/funding'))
        return Promise.resolve(
          jsonResponse({
            family_id: 'f-rep-1',
            funding_state: 'self_pay',
            funding_type: 'self_pay',
            installments: null, // self-pay → no TEFA schedule (no .map crash)
            tuition_unlocked: true,
            program: 'Self-pay',
            next_action: 'None',
            due_by: null,
            days_remaining: null,
            at_risk: false,
            award_full_vs_prorated: 'full',
          }),
        );
      // The eval scoreboard CloseTipsPanel reads to drive its fail-closed UI.
      if (url.includes('/evals'))
        return Promise.resolve(jsonResponse({ disabled: {} }));
      // The AI draft route — a surfaced, eval-passed proposal so ActionPanel
      // renders the approvable body (the approve POSTs /proposals/{id}/decision).
      if (url.includes('/ai/enrollment/draft'))
        return Promise.resolve(
          jsonResponse({
            proposal_id: 'p-rep-1',
            surfaced: true,
            degraded: false,
            failed_rules: [],
            proposal: {
              action: 'email',
              family_id: 'f-rep-1',
              body: 'Hi — following up on your application.',
              claims: [],
            },
          }),
        );
      // The gated decision route — the ONLY write path (same as the admin).
      if (url.match(/\/proposals\/[^/]+\/decision$/))
        return Promise.resolve(jsonResponse({ action: 'approve' }));
      return Promise.resolve(jsonResponse({}));
    }),
  );
}

function enterAsRep(): void {
  // Seed the agent seat the same way the gate persists it (one storage key).
  localStorage.setItem(
    'gt_demo_session',
    JSON.stringify({
      role: 'agent',
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

describe('RepWorkspace (M2 — the rep gets ONE subset view)', () => {
  beforeEach(() => {
    localStorage.clear();
    installFetch();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the rep workspace (not the admin EnrollmentWorkspace) for an agent seat', async () => {
    enterAsRep();
    expect(await screen.findByTestId('rep-workspace')).toBeInTheDocument();
  });

  it('renders "My Queue" — the owner-scoped TriageList with its recency facets', async () => {
    enterAsRep();
    expect(await screen.findByTestId('triage-list')).toBeInTheDocument();
    // The EXISTING recency facets/dial are present (no new list — §4).
    expect(screen.getByTestId('triage-recency')).toBeInTheDocument();
    expect(screen.getByTestId('triage-scope')).toBeInTheDocument();
  });

  it('renders the rep SituationBar (my book: to-contact / overdue / $ at risk)', async () => {
    enterAsRep();
    const bar = await screen.findByTestId('rep-situation-bar');
    expect(bar).toBeInTheDocument();
    // The three figures derive from the SAME owner-scoped /work-queue read.
    expect(
      within(bar).getByTestId('rep-situation-tocontact'),
    ).toBeInTheDocument();
    expect(
      within(bar).getByTestId('rep-situation-overdue'),
    ).toBeInTheDocument();
    expect(within(bar).getByTestId('rep-situation-atrisk')).toBeInTheDocument();
  });

  it('the rep reads /work-queue THROUGH apiFetch carrying the agent header (server-scoped, no client filter)', async () => {
    enterAsRep();
    await screen.findByTestId('triage-list');
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes('/work-queue'))).toBe(true);
    });
    const wq = calls.find((c) => c.url.includes('/work-queue'))!;
    const headers = wq.init?.headers as Record<string, string> | undefined;
    expect(headers?.['X-Demo-Role']).toBe('agent');
    expect(headers?.['X-Demo-Agent-Id']).toBe(AGENT.id);
  });

  it('ABSENT: the admin Calendar / Students / Reconcile / MergeQueue / view-toggle surfaces', async () => {
    enterAsRep();
    await screen.findByTestId('triage-list');
    // Calendar (the heat find surface) — rep sees their list, not the calendar.
    expect(screen.queryByTestId('enrollment-calendar')).toBeNull();
    // Per-child Students board.
    expect(screen.queryByTestId('student-board')).toBeNull();
    // The truth-layer Reconcile board + the human-review merge queue.
    expect(screen.queryByTestId('household-reconcile-board')).toBeNull();
    expect(screen.queryByTestId('reconcile-merge-queue')).toBeNull();
    // The admin left-view toggle (Calendar/Students/Reconcile/History switch).
    expect(screen.queryByTestId('enrollment-view-toggle')).toBeNull();
  });

  it('the close panel is reachable — selecting a family shows DealView + the gated ActionPanel', async () => {
    enterAsRep();
    await screen.findByTestId('triage-list');
    // A family row from the owner-scoped queue.
    const row = await screen.findByTestId('drill-row-f-rep-1');
    fireEvent.click(row);
    expect(await screen.findByTestId('deal-view')).toBeInTheDocument();
    // The SAME eval-gated ActionPanel the admin uses — the ONLY write path.
    expect(screen.getByTestId('action-panel')).toBeInTheDocument();
  });

  it('the acting write goes through POST /proposals/{id}/decision (no parallel write path)', async () => {
    enterAsRep();
    await screen.findByTestId('triage-list');
    fireEvent.click(await screen.findByTestId('drill-row-f-rep-1'));
    await screen.findByTestId('action-panel');
    // Request a draft, then approve — the approve must POST the gated decision route.
    fireEvent.click(screen.getByTestId('draft-email'));
    fireEvent.click(await screen.findByTestId('approve-action'));
    await waitFor(() => {
      const decisionCall = calls.find((c) =>
        /\/proposals\/[^/]+\/decision$/.test(c.url),
      );
      expect(decisionCall).toBeDefined();
      expect(decisionCall?.init?.method).toBe('POST');
    });
  });

  it('regression: an ADMIN seat still gets the FULL EnrollmentWorkspace (only the rep was gated)', async () => {
    enterAsAdmin();
    // The admin keeps the calendar + the left-view toggle; the rep workspace is absent.
    expect(
      await screen.findByTestId('enrollment-calendar'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('enrollment-view-toggle')).toBeInTheDocument();
    expect(screen.queryByTestId('rep-workspace')).toBeNull();
  });

  // The founder's ask: "a calendar so they can see the people to contact and when
  // they were assigned … calendar view and they can switch to list view." The rep
  // gets their OWN list/calendar toggle (its own testid — NOT the admin one), and
  // the calendar is the owner-scoped recovery flavor (anchor=stall, no ?anchor=intake).
  it('toggles My Queue between the ranked list and an owner-scoped calendar', async () => {
    enterAsRep();
    // Opens on the ranked list (the calendar is not the default).
    await screen.findByTestId('triage-list');
    expect(screen.queryByTestId('enrollment-calendar')).toBeNull();

    // Flip to the calendar via the rep's OWN toggle.
    const toggle = screen.getByTestId('rep-view-toggle');
    fireEvent.click(within(toggle).getByText('Calendar'));
    expect(
      await screen.findByTestId('enrollment-calendar'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('triage-list')).toBeNull();
    // It read the owner-scoped calendar route (recovery flavor — no admin attribution).
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/enrollment/calendar'))).toBe(
        true,
      ),
    );
    expect(calls.some((c) => c.url.includes('anchor=intake'))).toBe(false);

    // Flip back to the list.
    fireEvent.click(within(toggle).getByText('List'));
    expect(await screen.findByTestId('triage-list')).toBeInTheDocument();
  });
});
