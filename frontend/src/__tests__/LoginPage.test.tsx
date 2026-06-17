import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import App from '../App';
import { loadSession, type DemoSession } from '../LoginPage';
import { demoHeaders, apiFetch } from '../config';

const CLOSER_ID = 'a0000000-0000-4000-8000-000000000001';
const SETTER_ID = 'a0000000-0000-4000-8000-000000000002';

// M1 demo login gate (acceptance). The gate renders before the cockpit shell;
// picking a seat enters the app; "Switch seat" returns to the gate. Demo-only,
// no real auth (INV-1).
describe('Demo login gate', () => {
  beforeEach(() => {
    localStorage.clear();
    // The cockpit fetches on mount; a stub keeps acceptance focused on the gate.
    // The default Enrollment view mounts the Calendar, which expects an object
    // with an `entries` array; everything else defaults to an empty list.
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = /\/enrollment\/calendar/.test(url)
          ? { month: '2026-06', entries: [] }
          : [];
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(body),
        } as Response);
      }),
    );
  });

  it('shows the gate (logo + Admin/Sales seats) before any cockpit chrome', () => {
    render(<App />);
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /GT Pulse/i })).toBeInTheDocument();
    expect(screen.getByTestId('login-role-admin')).toBeInTheDocument();
    expect(screen.getByTestId('login-role-agent')).toBeInTheDocument();
    // The cockpit sidebar is NOT mounted until a seat is chosen.
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
  });

  it('reveals the agent picker only when the Sales Agent seat is chosen', () => {
    render(<App />);
    expect(screen.queryByTestId('login-agent-select')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('login-role-agent'));
    expect(screen.getByTestId('login-agent-select')).toBeInTheDocument();
  });

  it('enters the cockpit on Enter and returns to the gate on Switch seat', () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-enter'));
    // The shell (sidebar) is now mounted; the gate is gone.
    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    // Switch seat returns to the gate.
    fireEvent.click(screen.getByTestId('sidebar-nav-switch-seat'));
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });

  it('lists the two seeded synthetic agents WITH tier badges', () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-role-agent'));
    const select = screen.getByTestId('login-agent-select') as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.textContent);
    expect(options).toEqual([
      'Riley Carter — Closer',
      'Jordan Avery — Setter',
    ]);
    // The visible tier badge reflects the (default first) selected agent.
    expect(screen.getByTestId('login-tier-badge')).toHaveTextContent('Closer');
    expect(select.value).toBe(CLOSER_ID);
  });

  it('stores the canonical closer uuid + tier when agent #1 enters', () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-role-agent'));
    fireEvent.click(screen.getByTestId('login-enter'));
    const stored = loadSession() as DemoSession;
    expect(stored.role).toBe('agent');
    expect(stored.agentId).toBe(CLOSER_ID);
    expect(stored.tier).toBe('closer');
    expect(stored.agentRank).toBe(1);
    expect(stored.agentName).toBe('Riley Carter');
  });

  it('stores the setter seat when agent #2 is selected', () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-role-agent'));
    fireEvent.change(screen.getByTestId('login-agent-select'), {
      target: { value: SETTER_ID },
    });
    expect(screen.getByTestId('login-tier-badge')).toHaveTextContent('Setter');
    fireEvent.click(screen.getByTestId('login-enter'));
    const stored = loadSession() as DemoSession;
    expect(stored.agentId).toBe(SETTER_ID);
    expect(stored.tier).toBe('setter');
  });

  it('stores an admin seat (no agent id) when Admin enters', () => {
    render(<App />);
    // Admin is the default role; enter straight away.
    fireEvent.click(screen.getByTestId('login-enter'));
    const stored = loadSession() as DemoSession;
    expect(stored.role).toBe('admin');
    expect(stored.agentId).toBeUndefined();
  });
});

// Focused unit tests of the header wiring (config.ts) — the demo principal that
// every cockpit API call must carry so the backend can scope server-side.
describe('demo principal header wiring', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('attaches X-Demo-Role + X-Demo-Agent-Id for an agent seat', () => {
    localStorage.setItem(
      'gt_demo_session',
      JSON.stringify({
        role: 'agent',
        agentId: CLOSER_ID,
        agentRank: 1,
        tier: 'closer',
        agentName: 'Riley Carter',
      } satisfies DemoSession),
    );
    expect(demoHeaders()).toEqual({
      'X-Demo-Role': 'agent',
      'X-Demo-Agent-Id': CLOSER_ID,
    });

    const fetchMock = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve([]) } as Response),
    );
    vi.stubGlobal('fetch', fetchMock);
    void apiFetch('/work-queue');
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toMatch(/\/work-queue$/);
    expect((init.headers as Record<string, string>)['X-Demo-Role']).toBe('agent');
    expect((init.headers as Record<string, string>)['X-Demo-Agent-Id']).toBe(
      CLOSER_ID,
    );
    vi.unstubAllGlobals();
  });

  it('attaches only X-Demo-Role for an admin seat (no agent id)', () => {
    localStorage.setItem(
      'gt_demo_session',
      JSON.stringify({ role: 'admin' } satisfies DemoSession),
    );
    expect(demoHeaders()).toEqual({ 'X-Demo-Role': 'admin' });

    const fetchMock = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve([]) } as Response),
    );
    vi.stubGlobal('fetch', fetchMock);
    void apiFetch('/pipeline', { method: 'GET' });
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const headers = init.headers as Record<string, string>;
    expect(headers['X-Demo-Role']).toBe('admin');
    expect(headers['X-Demo-Agent-Id']).toBeUndefined();
    // The caller's init (method) is preserved.
    expect(init.method).toBe('GET');
    vi.unstubAllGlobals();
  });
});
