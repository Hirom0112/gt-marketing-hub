import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DrillRow, { DRILL_GRID, DrillRowHead } from '../DrillRow';
import { recencyLabel } from '../recency';

// Acceptance test (CLAUDE §4.2). A dense drill row: zero-padded rank, name +
// stuck-step subline, mono value, a recency Chip (reused primitive, tone from
// recencyTone), and a teal-when-on checkbox whose toggle does NOT select the
// row. Header + row share the DRILL_GRID template.

const FID = 'fam-1';

function row(props = {}) {
  return (
    <DrillRow
      familyId={FID}
      rank={3}
      name="The Alvarez Family"
      stuckStep="enrollment agreement"
      value="$10,474"
      score="0.91"
      contactStatus="overdue"
      {...props}
    />
  );
}

describe('DrillRow', () => {
  it('renders rank zero-padded, name, stuck step, value, score', () => {
    render(row());
    const r = screen.getByTestId(`drill-row-${FID}`);
    expect(r).toHaveTextContent('03');
    expect(r).toHaveTextContent('The Alvarez Family');
    expect(r).toHaveTextContent('enrollment agreement');
    expect(r).toHaveTextContent('$10,474');
    expect(r).toHaveTextContent('0.91');
  });

  it('renders the recency label via the reused Chip', () => {
    render(row({ contactStatus: 'overdue' }));
    expect(screen.getByTestId(`drill-row-${FID}`)).toHaveTextContent(
      recencyLabel('overdue'),
    );
  });

  it('selecting the row fires onSelect but ticking the checkbox does not', () => {
    const onSelect = vi.fn();
    const onToggle = vi.fn();
    render(row({ onSelect, onToggle }));

    fireEvent.click(screen.getByTestId(`drill-row-check-${FID}`));
    expect(onToggle).toHaveBeenCalledWith(FID);
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId(`drill-row-${FID}`));
    expect(onSelect).toHaveBeenCalledWith(FID);
  });

  it('reflects selection on the checkbox', () => {
    render(row({ selected: true }));
    const ck = screen.getByTestId(`drill-row-check-${FID}`);
    expect(ck).toHaveAttribute('aria-checked', 'true');
    expect(ck).toHaveTextContent('✓');
  });

  it('header and row share one grid template', () => {
    render(<DrillRowHead />);
    expect(screen.getByTestId('drill-head').style.gridTemplateColumns).toBe(
      DRILL_GRID,
    );
  });
});
