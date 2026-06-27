import { render, screen, within, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

// GT Pulse shell IA: the full-height blue LEFT sidebar is the ONLY chrome — no
// top bar and no page-header (no app-wide title/eyebrow/API chip). The sidebar
// carries the GT Pulse logo at the top + the nav stack; each workspace owns its
// own content. The Enrollment situation metrics live in the page CONTENT.
//
// The app now opens on the demo login gate (M1); these shell tests sign in first
// (Admin seat) to reach the cockpit. Sign-in mints a signed token via
// POST /auth/demo-token (B1), so a fetch stub serves the token + empty reads.
const FAKE_TOKEN = 'header.payload.signature';

async function enterCockpit(): Promise<void> {
  render(<App />);
  fireEvent.click(screen.getByTestId('login-enter'));
  await screen.findByTestId('sidebar');
}

describe('App shell', () => {
  beforeEach(() => {
    localStorage.clear(); // the gate persists the seat; isolate each test
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
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('has no top bar and no page-header chrome — the sidebar is the only chrome', async () => {
    await enterCockpit();
    expect(screen.queryByTestId('app-topbar')).toBeNull();
    expect(screen.queryByTestId('app-wordmark')).toBeNull();
    expect(screen.queryByTestId('page-title')).toBeNull();
    expect(screen.queryByTestId('api-base-url')).toBeNull();
  });

  it('renders the GT Pulse logo at the top of the sidebar', async () => {
    await enterCockpit();
    const sidebar = screen.getByTestId('sidebar');
    const brand = within(sidebar).getByTestId('sidebar-brand');
    expect(
      within(brand).getByRole('img', { name: /GT Pulse/i }),
    ).toHaveAttribute('src', '/gt-pulse-logo.png');
  });

  it('renders the left sidebar with the nav items (incl. switch-seat)', async () => {
    await enterCockpit();
    const sidebar = screen.getByTestId('sidebar');
    expect(sidebar).toBeInTheDocument();
    // Entered as Admin ⇒ the admin-only Security tab (M7) is present too.
    for (const key of [
      'enrollment',
      'marketing',
      'leadership',
      'security',
      'settings',
      'help',
      'switch-seat',
    ]) {
      expect(
        within(sidebar).getByTestId(`sidebar-nav-${key}`),
      ).toBeInTheDocument();
    }
  });

  it('a rep (sales agent) sees ONLY Enrollment — Marketing/Leadership/Security are admin-only', () => {
    // Seat a rep session directly (the admin-only surfaces must be gated out).
    localStorage.setItem(
      'gt_demo_session',
      JSON.stringify({
        role: 'operator',
        token: FAKE_TOKEN,
        expiresAt: Date.now() + 3_600_000,
        agentId: 'a0000000-0000-4000-8000-000000000001',
        agentRank: 1,
        tier: 'closer',
        agentName: 'Riley Carter',
      }),
    );
    render(<App />);
    const sidebar = screen.getByTestId('sidebar');
    expect(within(sidebar).getByTestId('sidebar-nav-enrollment')).toBeInTheDocument();
    // Admin-only surfaces are absent for a rep.
    for (const key of ['marketing', 'leadership', 'security']) {
      expect(within(sidebar).queryByTestId(`sidebar-nav-${key}`)).toBeNull();
    }
    // The rep still has the shared secondary items.
    expect(within(sidebar).getByTestId('sidebar-nav-settings')).toBeInTheDocument();
    expect(within(sidebar).getByTestId('sidebar-nav-switch-seat')).toBeInTheDocument();
  });

  it('opens on Enrollment', async () => {
    await enterCockpit();
    expect(screen.getByTestId('sidebar-nav-enrollment')).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('switches workspace from the sidebar', async () => {
    await enterCockpit();
    fireEvent.click(screen.getByTestId('sidebar-nav-settings'));
    expect(screen.getByTestId('settings-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('sidebar-nav-settings')).toHaveAttribute(
      'aria-selected',
      'true',
    );

    fireEvent.click(screen.getByTestId('sidebar-nav-help'));
    expect(screen.getByTestId('help-workspace')).toBeInTheDocument();
  });
});
