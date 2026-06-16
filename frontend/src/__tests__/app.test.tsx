import { render, screen, within } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from '../App';
import { DEFAULT_API_BASE_URL } from '../config';

// S14 shell IA: the old top tab-bar header was replaced by a LEFT sidebar nav +
// a clean page-header zone (eyebrow + page title, with the API chip in the
// corner). These tests assert the sidebar nav IA and the per-workspace page
// title rather than the retired "GT Growth Cockpit" top heading.
describe('App shell', () => {
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
    expect(screen.getByTestId('page-title')).toHaveTextContent(/configuration/i);
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
