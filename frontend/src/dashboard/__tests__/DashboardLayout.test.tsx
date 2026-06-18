import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DashboardLayout } from '../DashboardLayout';

// Acceptance test (CLAUDE §4.2). The shared shell is slot-only and enforces the
// briefs' restraint rule: nothing renders above the KPI strip, and nothing below
// the two columns.
describe('DashboardLayout', () => {
  const slots = {
    kpiStrip: <div data-testid="t-kpi" />,
    tabBar: <div data-testid="t-tabbar" />,
    tabPanel: <div data-testid="t-tabpanel" />,
    detailPanel: <div data-testid="t-detail" />,
  };

  it('renders the kpi strip, tab bar, tab panel and detail panel slots', () => {
    render(<DashboardLayout {...slots} />);
    expect(screen.getByTestId('t-kpi')).toBeInTheDocument();
    expect(screen.getByTestId('t-tabbar')).toBeInTheDocument();
    expect(screen.getByTestId('t-tabpanel')).toBeInTheDocument();
    expect(screen.getByTestId('t-detail')).toBeInTheDocument();
  });

  it('renders nothing above the strip and nothing below the two columns', () => {
    const { container } = render(<DashboardLayout {...slots} />);
    const root = container.querySelector('.admin-dashboard');
    expect(root).not.toBeNull();
    const children = Array.from(root!.children);
    // First child is the strip; last child is the two-column grid. No sibling
    // before the strip, none after the grid.
    expect(children[0]).toHaveAttribute('data-testid', 'dashboard-kpi-strip');
    expect(children[children.length - 1]).toHaveAttribute(
      'data-testid',
      'dashboard-grid',
    );
  });

  it('renders the optional banner between the strip and the grid', () => {
    render(<DashboardLayout {...slots} banner={<div data-testid="t-banner" />} />);
    expect(screen.getByTestId('dashboard-banner')).toBeInTheDocument();
    expect(screen.getByTestId('t-banner')).toBeInTheDocument();
  });

  it('omits the banner slot when no banner is given', () => {
    render(<DashboardLayout {...slots} />);
    expect(screen.queryByTestId('dashboard-banner')).toBeNull();
  });
});
