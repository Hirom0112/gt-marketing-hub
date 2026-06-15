import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Stat } from '../Stat';

// Acceptance test (CLAUDE §4.2). Stat gains an optional `barPct` mini funnel-share
// bar: a 4px track (--line-2) with a --flow fill at width:pct% — the leadership
// KPI's share-of-funnel rail. Omitted by default; clamped 0–100.

describe('Stat — funnel-share bar', () => {
  it('omits the bar when barPct is undefined', () => {
    render(<Stat label="Interest" value={42} />);
    expect(screen.queryByTestId('stat-bar')).toBeNull();
  });

  it('renders the bar fill at the given percent from tokens', () => {
    render(<Stat label="Apply" value={30} barPct={64} />);
    expect(screen.getByTestId('stat-bar').style.background).toBe(
      'var(--line-2)',
    );
    const fill = screen.getByTestId('stat-bar-fill');
    expect(fill.style.width).toBe('64%');
    expect(fill.style.background).toBe('var(--flow)');
  });

  it('clamps the bar width to 0–100', () => {
    const { rerender } = render(<Stat label="x" value={1} barPct={140} />);
    expect(screen.getByTestId('stat-bar-fill').style.width).toBe('100%');
    rerender(<Stat label="x" value={1} barPct={-10} />);
    expect(screen.getByTestId('stat-bar-fill').style.width).toBe('0%');
  });
});
