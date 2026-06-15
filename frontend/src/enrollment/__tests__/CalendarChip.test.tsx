import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import CalendarChip, { scoreSegments } from '../CalendarChip';
import { recencyClass } from '../recency';

// Acceptance test (CLAUDE §4.2). The reusable two-line calendar chip: bold name,
// a meta row with mono value + a 3-segment score bar (≥.85→3, ≥.65→2, else 1),
// a recency-tinted 3px left border, and onSelect on click.

const FID = 'fam-9';

describe('CalendarChip — score segments', () => {
  it('derives the on-segment count from the score thresholds', () => {
    expect(scoreSegments(0.9)).toBe(3);
    expect(scoreSegments(0.85)).toBe(3);
    expect(scoreSegments(0.7)).toBe(2);
    expect(scoreSegments(0.65)).toBe(2);
    expect(scoreSegments(0.4)).toBe(1);
  });
});

describe('CalendarChip', () => {
  function chip(props = {}) {
    return (
      <CalendarChip
        familyId={FID}
        name="The Bauer Family"
        value="$30k"
        score={0.91}
        contactStatus="overdue"
        {...props}
      />
    );
  }

  it('renders the name, mono value, and recency tint class', () => {
    render(chip());
    const c = screen.getByTestId(`calendar-chip-${FID}`);
    expect(c).toHaveTextContent('The Bauer Family');
    expect(screen.getByTestId(`calendar-chip-value-${FID}`)).toHaveTextContent(
      '$30k',
    );
    expect(c).toHaveClass(recencyClass('overdue'));
  });

  it('renders a 3-segment bar reflecting the score', () => {
    render(chip({ score: 0.7 }));
    expect(
      screen.getByTestId(`calendar-chip-bar-${FID}`),
    ).toHaveAttribute('data-segments', '2');
  });

  it('fires onSelect when clicked', () => {
    const onSelect = vi.fn();
    render(chip({ onSelect }));
    fireEvent.click(screen.getByTestId(`calendar-chip-${FID}`));
    expect(onSelect).toHaveBeenCalledWith(FID);
  });
});
