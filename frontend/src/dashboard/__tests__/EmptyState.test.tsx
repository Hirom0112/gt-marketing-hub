import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EmptyState } from '../EmptyState';

// Acceptance test (CLAUDE §4.2). The shared clean empty state for unselected
// panels and empty tabs.
describe('EmptyState', () => {
  it('renders a title and a one-line body', () => {
    render(<EmptyState title="No family selected" body="Pick a row to see details." />);
    expect(screen.getByText('No family selected')).toBeInTheDocument();
    expect(screen.getByText('Pick a row to see details.')).toBeInTheDocument();
  });

  it('renders an optional icon', () => {
    render(<EmptyState icon={<svg data-testid="ico" />} title="Empty" />);
    expect(screen.getByTestId('empty-state-icon')).toBeInTheDocument();
  });

  it('omits icon and body when not given', () => {
    render(<EmptyState title="Empty" />);
    expect(screen.queryByTestId('empty-state-icon')).toBeNull();
    expect(screen.getByText('Empty')).toBeInTheDocument();
  });
});
