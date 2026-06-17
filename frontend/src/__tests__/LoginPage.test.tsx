import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import App from '../App';

// M1 demo login gate (acceptance). The gate renders before the cockpit shell;
// picking a seat enters the app; "Switch seat" returns to the gate. Demo-only,
// no real auth (INV-1).
describe('Demo login gate', () => {
  beforeEach(() => {
    localStorage.clear();
    // The cockpit fetches on mount; a stub keeps acceptance focused on the gate.
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve([]),
        } as Response),
      ),
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
});
