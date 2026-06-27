import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from '../App';
import { loadSession, type DemoSession } from '../LoginPage';
import { authHeaders, apiFetch } from '../config';

const CLOSER_ID = 'a0000000-0000-4000-8000-000000000001';
const SETTER_ID = 'a0000000-0000-4000-8000-000000000002';
const FAKE_TOKEN = 'header.payload.signature';

// B1 demo login gate (acceptance). The gate renders before the cockpit shell;
// picking a seat trades it for a REAL signed JWT (POST /auth/demo-token) and
// enters the app; "Switch seat" returns to the gate. The token rides on
// Authorization: Bearer for every cockpit call — the verified-principal bridge
// that replaced the old spoofable client-spelled role header. Synthetic only (INV-1).
describe('Demo login gate', () => {
  beforeEach(() => {
    localStorage.clear();
    // The login gate mints a token, then the cockpit fetches on mount; a stub
    // serves both. /auth/demo-token returns a fake signed token; the Calendar
    // expects {entries:[]}; everything else defaults to an empty list.
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

  it('shows the gate (logo + Admin/Leadership/Sales seats) before any cockpit chrome', () => {
    render(<App />);
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /GT Pulse/i })).toBeInTheDocument();
    expect(screen.getByTestId('login-role-admin')).toBeInTheDocument();
    expect(screen.getByTestId('login-role-leader')).toBeInTheDocument();
    expect(screen.getByTestId('login-role-operator')).toBeInTheDocument();
    // The cockpit sidebar is NOT mounted until a seat is chosen.
    expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument();
  });

  it('reveals the agent picker only when the Sales Agent (operator) seat is chosen', () => {
    render(<App />);
    expect(screen.queryByTestId('login-agent-select')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('login-role-operator'));
    expect(screen.getByTestId('login-agent-select')).toBeInTheDocument();
  });

  it('enters the cockpit on Enter and returns to the gate on Switch seat', async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-enter'));
    // Mint is async; the shell (sidebar) mounts once the token resolves.
    expect(await screen.findByTestId('sidebar')).toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    // Switch seat returns to the gate.
    fireEvent.click(screen.getByTestId('sidebar-nav-switch-seat'));
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });

  it('lists the two seeded synthetic agents WITH tier badges', () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-role-operator'));
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

  it('stores the operator seat (closer uuid + tier + token) when agent #1 enters', async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-role-operator'));
    fireEvent.click(screen.getByTestId('login-enter'));
    await waitFor(() => expect(loadSession()).not.toBeNull());
    const stored = loadSession() as DemoSession;
    expect(stored.role).toBe('operator');
    expect(stored.agentId).toBe(CLOSER_ID);
    expect(stored.tier).toBe('closer');
    expect(stored.agentRank).toBe(1);
    expect(stored.agentName).toBe('Riley Carter');
    expect(stored.token).toBe(FAKE_TOKEN);
    expect(typeof stored.expiresAt).toBe('number');
  });

  it('stores the setter seat when agent #2 is selected', async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-role-operator'));
    fireEvent.change(screen.getByTestId('login-agent-select'), {
      target: { value: SETTER_ID },
    });
    expect(screen.getByTestId('login-tier-badge')).toHaveTextContent('Setter');
    fireEvent.click(screen.getByTestId('login-enter'));
    await waitFor(() => expect(loadSession()).not.toBeNull());
    const stored = loadSession() as DemoSession;
    expect(stored.agentId).toBe(SETTER_ID);
    expect(stored.tier).toBe('setter');
  });

  it('stores an admin seat (no agent id, with token) when Admin enters', async () => {
    render(<App />);
    // Admin is the default role; enter straight away.
    fireEvent.click(screen.getByTestId('login-enter'));
    await waitFor(() => expect(loadSession()).not.toBeNull());
    const stored = loadSession() as DemoSession;
    expect(stored.role).toBe('admin');
    expect(stored.agentId).toBeUndefined();
    expect(stored.token).toBe(FAKE_TOKEN);
  });

  it('stores a leader seat (no agent id, with token) when Leadership enters', async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('login-role-leader'));
    fireEvent.click(screen.getByTestId('login-enter'));
    await waitFor(() => expect(loadSession()).not.toBeNull());
    const stored = loadSession() as DemoSession;
    expect(stored.role).toBe('leader');
    expect(stored.agentId).toBeUndefined();
    expect(stored.token).toBe(FAKE_TOKEN);
  });

  it('surfaces an error and stores NOTHING when the token mint fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (/\/auth\/demo-token/.test(url)) {
          return Promise.resolve({
            ok: false,
            status: 503,
            json: () => Promise.resolve({}),
          } as Response);
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([]),
        } as Response);
      }),
    );
    render(<App />);
    fireEvent.click(screen.getByTestId('login-enter'));
    expect(await screen.findByTestId('login-error')).toBeInTheDocument();
    // No broken/partial seat is persisted; the gate is still up.
    expect(loadSession()).toBeNull();
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });
});

// Focused unit tests of the auth header wiring (config.ts) — the verified bearer
// token that every cockpit API call must carry so the backend can scope by the
// signed app_metadata.role (B1; replaced the old spoofable client-spelled principal).
describe('bearer auth header wiring', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('attaches Authorization: Bearer <token> when a token is stored', () => {
    localStorage.setItem(
      'gt_demo_session',
      JSON.stringify({
        role: 'operator',
        token: FAKE_TOKEN,
        expiresAt: Date.now() + 3_600_000,
        agentId: CLOSER_ID,
        agentRank: 1,
        tier: 'closer',
        agentName: 'Riley Carter',
      } satisfies DemoSession),
    );
    expect(authHeaders()).toEqual({ Authorization: `Bearer ${FAKE_TOKEN}` });

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
    expect((init.headers as Record<string, string>)['Authorization']).toBe(
      `Bearer ${FAKE_TOKEN}`,
    );
    vi.unstubAllGlobals();
  });

  it('attaches NO Authorization header when no token is stored', () => {
    expect(authHeaders()).toEqual({});

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
    expect(headers['Authorization']).toBeUndefined();
    // The caller's init (method) is preserved.
    expect(init.method).toBe('GET');
    vi.unstubAllGlobals();
  });
});
