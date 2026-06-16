import { render, screen, within } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from '../App';
import { DEFAULT_API_BASE_URL } from '../config';

// GT Pulse shell IA: the full-height LEFT sidebar is the only chrome — there is
// NO top bar. The sidebar carries the GT Pulse brand at the top + the nav stack;
// each workspace has a clean page-header zone (eyebrow + page title, with the API
// chip in the corner). The Enrollment situation metrics live in the page CONTENT,
// not in any global header. These tests assert that IA.
describe('App shell', () => {
  it('has no global top bar — the sidebar is the only chrome', () => {
    render(<App />);
    expect(screen.queryByTestId('app-topbar')).toBeNull();
    expect(screen.queryByTestId('app-wordmark')).toBeNull();
  });

  it('renders the GT Pulse brand at the top of the sidebar', () => {
    render(<App />);
    const sidebar = screen.getByTestId('sidebar');
    const brand = within(sidebar).getByTestId('sidebar-brand');
    expect(brand).toHaveTextContent(/GT Pulse/i);
    expect(
      within(brand).getByRole('img', { name: /GT Pulse/i }),
    ).toHaveAttribute('src', '/gt-pulse-logo.png');
  });

  it('puts the API chip in the page-header, not a global header', () => {
    render(<App />);
    // The API chip lives in the per-workspace page-header.
    expect(screen.getByTestId('api-base-url')).toBeInTheDocument();
    // The situation metrics are NOT in any global header (they live in the
    // Enrollment page content — see EnrollmentWorkspace tests).
    expect(screen.queryByTestId('situation-bar')).toBeNull();
  });

  it('renders the left sidebar with the five nav items', () => {
    render(<App />);
    const sidebar = screen.getByTestId('sidebar');
    expect(sidebar).toBeInTheDocument();
    for (const key of [
      'enrollment',
      'marketing',
      'leadership',
      'settings',
      'help',
    ]) {
      expect(
        within(sidebar).getByTestId(`sidebar-nav-${key}`),
      ).toBeInTheDocument();
    }
  });

  it('opens on Enrollment with its page title', () => {
    render(<App />);
    expect(screen.getByTestId('page-title')).toHaveTextContent(
      /enrollment recovery calendar/i,
    );
    expect(screen.getByTestId('sidebar-nav-enrollment')).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('switches workspace from the sidebar and swaps the page title', () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('sidebar-nav-leadership'));
    expect(screen.getByTestId('page-title')).toHaveTextContent(
      /leadership scoreboard/i,
    );

    fireEvent.click(screen.getByTestId('sidebar-nav-settings'));
    expect(screen.getByTestId('page-title')).toHaveTextContent(
      /configuration/i,
    );
    expect(screen.getByTestId('settings-workspace')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('sidebar-nav-help'));
    expect(screen.getByTestId('help-workspace')).toBeInTheDocument();
  });

  it('exposes the API base URL from config in the header', () => {
    render(<App />);
    expect(screen.getByTestId('api-base-url')).toHaveTextContent(
      DEFAULT_API_BASE_URL,
    );
  });
});
