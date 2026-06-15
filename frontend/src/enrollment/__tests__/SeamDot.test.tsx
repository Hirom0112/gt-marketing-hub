import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import SeamDot, { type SeamStatus } from '../SeamDot';

// Acceptance test (CLAUDE §4.2). The seam dot colours by reconcile state from
// the palette tokens: synced → --flow, unsynced → --gate, conflict → --signal.

describe('SeamDot', () => {
  const cases: ReadonlyArray<[SeamStatus, string]> = [
    ['synced', 'var(--flow)'],
    ['unsynced', 'var(--gate)'],
    ['conflict', 'var(--signal)'],
  ];

  it.each(cases)('colours %s from its token', (status, token) => {
    render(<SeamDot status={status} />);
    const dot = screen.getByTestId('seam-dot');
    expect(dot).toHaveAttribute('data-seam', status);
    expect(dot.style.background).toBe(token);
  });
});
