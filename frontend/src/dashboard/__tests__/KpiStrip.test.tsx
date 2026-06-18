import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { KpiStrip } from '../KpiStrip';

// Acceptance test (CLAUDE §4.2). KpiStrip lays out whatever metrics the shell
// passes — 3 for admin, 4 for the sales agent — with no knowledge of the data.
describe('KpiStrip', () => {
  it('renders 3 admin metrics', () => {
    render(
      <KpiStrip
        metrics={[
          { label: 'ACTIVE STALLS', value: 12 },
          { label: 'OVERDUE', value: 5 },
          { label: '$ AT RISK', value: '$48,200' },
        ]}
      />,
    );
    expect(screen.getAllByTestId('kpi-metric')).toHaveLength(3);
    expect(screen.getByText('ACTIVE STALLS')).toBeInTheDocument();
    expect(screen.getByText('$48,200')).toBeInTheDocument();
  });

  it('renders 4 sales-agent metrics', () => {
    render(
      <KpiStrip
        metrics={[
          { label: 'BOOKED', value: 3 },
          { label: 'CONTACTED', value: 9 },
          { label: 'OVERDUE', value: 4, tone: 'signal' },
          { label: 'ACTIVE', value: 16 },
        ]}
      />,
    );
    expect(screen.getAllByTestId('kpi-metric')).toHaveLength(4);
    expect(screen.getByText('BOOKED')).toBeInTheDocument();
  });
});
