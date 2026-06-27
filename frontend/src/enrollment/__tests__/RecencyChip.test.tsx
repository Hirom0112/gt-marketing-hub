import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import RecencyChip from '../RecencyChip';
import { recencyClass } from '../recency';

// Acceptance test (CLAUDE §4.2). The contact-recency color system (S9 Wave 4,
// vision item 5) maps each `contact_status` to a tone CLASS — grey (fresh),
// red (overdue), light-green (followed_up), neutral (closed). This asserts the
// correct tone class is applied per status (the tints themselves are tokens).

describe('RecencyChip · contact color system', () => {
  it('applies the correct tone class per status', () => {
    const cases = [
      ['fresh', 'recency-fresh'],
      ['overdue', 'recency-overdue'],
      ['followed_up', 'recency-followed_up'],
      ['closed', 'recency-closed'],
    ] as const;

    for (const [status, expectedClass] of cases) {
      const { unmount } = render(<RecencyChip status={status} />);
      const chip = screen.getByTestId('recency-chip');
      expect(chip).toHaveClass(expectedClass);
      expect(chip).toHaveClass(recencyClass(status));
      expect(chip.getAttribute('data-recency')).toBe(status);
      unmount();
    }
  });

  it('renders an unknown status quietly without throwing', () => {
    render(<RecencyChip status="some_new_status" />);
    const chip = screen.getByTestId('recency-chip');
    expect(chip).toHaveClass('recency-unknown');
    expect(chip).toHaveTextContent('some_new_status');
  });
});
