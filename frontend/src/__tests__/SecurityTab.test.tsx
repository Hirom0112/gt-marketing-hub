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

// M7 acceptance (CLAUDE §4.2; MULTI_AGENT_COCKPIT §7, §5 role model). The
// admin-only Security / observability tab:
//   Panel A — live RLS posture: all tables ok ⇒ GREEN banner; any table missing
//     FORCE/null-guard ⇒ RED alarm (the real panel).
//   Panel B — OWASP-mapped security_event feed: each row shows its OWASP mapping
//     + severity + detail + an acknowledge action; the whole feed is LABELED
//     "simulated" (v1, INV-9 honesty).
// The tab is ADMIN-ONLY: a rep (agent) seat NEVER sees the nav item or the tab.
//
// fetch is mocked (mirrors EvalGate/RepWorkspace tests); the app enters via the
// persisted demo seat (one storage key), same as the other shell tests.

const AGENT = DEMO_AGENTS[0]!; // Riley Carter — closer seat (a rep)

// GET /security/posture → PostureView { green, checks:[{name,passed,detail}] }.
// All invariants pass ⇒ green banner.
const POSTURE_GREEN = {
  green: true,
  checks: [
    { name: 'force_rls', passed: true, detail: 'every table FORCE-RLS' },
    { name: 'null_guard', passed: true, detail: 'every policy null-guarded' },
  ],
};

// A check failed (a table lost FORCE-RLS) ⇒ red alarm.
const POSTURE_RED = {
  green: false,
  checks: [
    { name: 'force_rls', passed: false, detail: 'proposals lost FORCE-RLS' },
    { name: 'null_guard', passed: true, detail: 'every policy null-guarded' },
  ],
};

// GET /security/events → SecurityEventsView { simulated, events:[...] } (wrapped).
const EVENTS = [
  {
    event_id: 'evt-1',
    occurred_at: '2026-06-17T10:00:00Z',
    actor_kind: 'agent',
    surface: '/families/{id}',
    signal: 'cross_owner_read',
    severity: 'high',
    owasp: 'API1:2023 BOLA',
    detail: 'agent attempted to read a family outside its assigned book',
    simulated: true,
    acknowledged: false,
  },
  {
    event_id: 'evt-2',
    occurred_at: '2026-06-17T09:30:00Z',
    actor_kind: 'anonymous',
    surface: '/proposals',
    signal: 'unauth_write_attempt',
    severity: 'medium',
    owasp: 'API5:2023 BFLA',
    detail: 'unauthenticated principal attempted a proposal write',
    simulated: true,
    acknowledged: false,
  },
];

const EVENTS_REPORT = { simulated: true, events: EVENTS };

// Routes GET /security/posture + /security/events to distinct payloads. Any
// other endpoint (e.g. the rep workspace's /work-queue, hit when we enter as a
// rep to prove the tab is absent) gets an empty array so unrelated surfaces
// mount cleanly without noise.
function installFetch(posture: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const u = String(url);
      let payload: unknown = [];
      if (u.includes('/security/events')) payload = EVENTS_REPORT;
      else if (u.includes('/security/posture')) payload = posture;
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

function enterAsAdmin(): void {
  localStorage.setItem('gt_demo_session', JSON.stringify({ role: 'admin' }));
  render(<App />);
}

function enterAsRep(): void {
  localStorage.setItem(
    'gt_demo_session',
    JSON.stringify({
      role: 'operator',
      token: 'header.payload.signature',
      expiresAt: Date.now() + 3_600_000,
      agentId: AGENT.id,
      agentRank: AGENT.rank,
      tier: AGENT.tier,
      agentName: AGENT.name,
    }),
  );
  render(<App />);
}

// Click into the Security tab from the (admin) sidebar.
function openSecurity(): void {
  fireEvent.click(screen.getByTestId('sidebar-nav-security'));
}

describe('SecurityTab (M7 — admin-only Security / observability)', () => {
  beforeEach(() => {
    localStorage.clear();
    installFetch(POSTURE_GREEN);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('an ADMIN sees the Security nav item and the tab renders', async () => {
    enterAsAdmin();
    expect(screen.getByTestId('sidebar-nav-security')).toBeInTheDocument();
    openSecurity();
    expect(await screen.findByTestId('security-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('security-tab')).toBeInTheDocument();
  });

  it('Panel A shows a GREEN banner when every table is FORCE-RLS + null-guarded', async () => {
    installFetch(POSTURE_GREEN);
    enterAsAdmin();
    openSecurity();
    const panel = await screen.findByTestId('posture-panel');
    expect(within(panel).getByTestId('posture-green')).toBeInTheDocument();
    expect(within(panel).queryByTestId('posture-red')).toBeNull();
    // The per-check rows render.
    expect(within(panel).getByTestId('posture-row-force_rls')).toBeInTheDocument();
  });

  it('Panel A raises a RED alarm when a table lost its FORCE-RLS policy', async () => {
    installFetch(POSTURE_RED);
    enterAsAdmin();
    openSecurity();
    const panel = await screen.findByTestId('posture-panel');
    expect(within(panel).getByTestId('posture-red')).toBeInTheDocument();
    expect(within(panel).queryByTestId('posture-green')).toBeNull();
    // The offending check is listed and flagged.
    const offending = within(panel).getByTestId('posture-row-force_rls');
    expect(offending).toHaveTextContent('alarm');
  });

  it('Panel B lists OWASP-mapped events, is LABELED "simulated", and offers acknowledge', async () => {
    enterAsAdmin();
    openSecurity();
    const panel = await screen.findByTestId('events-panel');
    // INV-9 honesty: the v1 feed is visibly labeled "simulated".
    expect(panel).toHaveTextContent(/simulated/i);
    // Each row carries its OWASP mapping.
    expect(within(panel).getByTestId('event-owasp-evt-1')).toHaveTextContent(
      'API1:2023 BOLA',
    );
    expect(within(panel).getByTestId('event-owasp-evt-2')).toHaveTextContent(
      'API5:2023 BFLA',
    );
    // An acknowledge affordance exists per event.
    expect(within(panel).getByTestId('event-ack-evt-1')).toBeInTheDocument();
  });

  it('acknowledging an event flips the row to acknowledged', async () => {
    enterAsAdmin();
    openSecurity();
    await screen.findByTestId('events-panel');
    fireEvent.click(screen.getByTestId('event-ack-evt-1'));
    await waitFor(() =>
      expect(screen.getByTestId('event-acked-evt-1')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('event-ack-evt-1')).toBeNull();
  });

  it('a REP (agent) NEVER sees the Security nav item or the tab', async () => {
    enterAsRep();
    await screen.findByTestId('sidebar');
    // The nav item is absent for a rep.
    expect(screen.queryByTestId('sidebar-nav-security')).toBeNull();
    // And the tab/workspace never renders.
    expect(screen.queryByTestId('security-workspace')).toBeNull();
    expect(screen.queryByTestId('security-tab')).toBeNull();
  });
});
