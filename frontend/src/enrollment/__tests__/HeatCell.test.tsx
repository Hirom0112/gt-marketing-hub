import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import HeatCell from '../HeatCell';

// Acceptance test (CLAUDE §4.2). The collapsed busy-day heat tile shows the
// stall count, the dollars at risk, drives its gradient opacity from an inline
// `--i` intensity (clamped 0–1, no raw hex), and fires onClick to triage.

describe('HeatCell', () => {
  it('renders the count, risk, and triage affordance', () => {
    render(<HeatCell count={68} atRisk="$24k" intensity={0.5} />);
    expect(screen.getByTestId('heat-cell-count')).toHaveTextContent('68');
    expect(screen.getByTestId('heat-cell-risk')).toHaveTextContent(
      '$24k at risk',
    );
    expect(screen.getByTestId('heat-cell')).toHaveTextContent(
      'tap to triage →',
    );
  });

  it('sets the inline --i intensity from the prop and uses the token ramp', () => {
    render(<HeatCell count={120} atRisk="$50k" intensity={0.5} />);
    const cell = screen.getByTestId('heat-cell');
    expect(cell.style.getPropertyValue('--i')).toBe('0.5');
    // The background interpolates the theme heat-channel tokens, not raw hex.
    expect(cell.style.background).toContain('var(--heat-from)');
    expect(cell.style.background).toContain('var(--heat-to)');
  });

  it('clamps intensity above 1 down to 1', () => {
    render(<HeatCell count={400} atRisk="$99k" intensity={3.3} />);
    expect(
      screen.getByTestId('heat-cell').style.getPropertyValue('--i'),
    ).toBe('1');
  });

  it('fires onClick when tapped', () => {
    const onClick = vi.fn();
    render(
      <HeatCell count={9} atRisk="$4k" intensity={0.1} onClick={onClick} />,
    );
    fireEvent.click(screen.getByTestId('heat-cell'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
