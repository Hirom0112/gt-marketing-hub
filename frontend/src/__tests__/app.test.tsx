import { render, screen, within, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from '../App';

// GT Pulse shell IA: the full-height blue LEFT sidebar is the ONLY chrome — no
// top bar and no page-header (no app-wide title/eyebrow/API chip). The sidebar
// carries the GT Pulse logo at the top + the nav stack; each workspace owns its
// own content. The Enrollment situation metrics live in the page CONTENT.
describe('App shell', () => {
  it('has no top bar and no page-header chrome — the sidebar is the only chrome', () => {
    render(<App />);
    expect(screen.queryByTestId('app-topbar')).toBeNull();
    expect(screen.queryByTestId('app-wordmark')).toBeNull();
    expect(screen.queryByTestId('page-title')).toBeNull();
    expect(screen.queryByTestId('api-base-url')).toBeNull();
  });

  it('renders the GT Pulse logo at the top of the sidebar', () => {
    render(<App />);
    const sidebar = screen.getByTestId('sidebar');
    const brand = within(sidebar).getByTestId('sidebar-brand');
    expect(
      within(brand).getByRole('img', { name: /GT Pulse/i }),
    ).toHaveAttribute('src', '/gt-pulse-logo.png');
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

  it('opens on Enrollment', () => {
    render(<App />);
    expect(screen.getByTestId('sidebar-nav-enrollment')).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('switches workspace from the sidebar', () => {
    render(<App />);
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
