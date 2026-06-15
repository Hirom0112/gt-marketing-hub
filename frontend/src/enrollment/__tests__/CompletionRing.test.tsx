import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import CompletionRing from '../CompletionRing';

// Acceptance test (CLAUDE §4.2). The conic completion dial: sets the `--p` sweep
// inline from `pct`, shows the rounded mono "NN%", uses the ring tokens (not raw
// hex), clamps out-of-range input, and is labelled for assistive tech.

describe('CompletionRing', () => {
  it('renders the percent and sets the --p sweep', () => {
    render(<CompletionRing pct={46} />);
    const ring = screen.getByTestId('completion-ring');
    expect(ring.style.getPropertyValue('--p')).toBe('46');
    expect(screen.getByTestId('completion-ring-label')).toHaveTextContent(
      '46%',
    );
    expect(ring).toHaveAttribute('aria-label', '46% complete');
    expect(ring.style.background).toContain('var(--ring-fill)');
    expect(ring.style.background).toContain('var(--ring-track)');
  });

  it('rounds a fractional percent', () => {
    render(<CompletionRing pct={72.6} />);
    expect(
      screen.getByTestId('completion-ring').style.getPropertyValue('--p'),
    ).toBe('73');
  });

  it('clamps below 0 and above 100', () => {
    const { rerender } = render(<CompletionRing pct={-20} />);
    expect(
      screen.getByTestId('completion-ring').style.getPropertyValue('--p'),
    ).toBe('0');
    rerender(<CompletionRing pct={140} />);
    expect(
      screen.getByTestId('completion-ring').style.getPropertyValue('--p'),
    ).toBe('100');
  });
});
