import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DrillRow, { DRILL_GRID, DrillRowHead, railClass } from '../DrillRow';

// Acceptance test (CLAUDE §4.2) for the A-23 triage row. RECOVERABILITY
// (likelihood %) is the HERO cell — "the further they went, the more recoverable";
// the money is the HONEST secondary: $ face value (children × per-child tuition)
// over the child-count driver ("3 kids"). Funnel depth (stuck step) + the funding
// label sit under the name; recency is a left-edge RAIL (not a Chip); the rank +
// score columns are gone. Header + row share the DRILL_GRID template.

const FID = 'fam-1';

function row(props = {}) {
  return (
    <DrillRow
      familyId={FID}
      name="The Alvarez Family"
      stuckStep="enrollment agreement"
      funding="Texas voucher"
      stallDate="Jun 13"
      age="12d"
      likelihood="84%"
      value="$31,200"
      kids="3 kids"
      magnitude={0.8}
      contactStatus="overdue"
      {...props}
    />
  );
}

describe('DrillRow (A-23 redesign)', () => {
  it('leads with the recoverability HERO + the value·kids secondary, age + date', () => {
    render(row());
    const r = screen.getByTestId(`drill-row-${FID}`);
    expect(r).toHaveTextContent('The Alvarez Family');
    expect(r).toHaveTextContent('enrollment agreement');
    // The loud hero is the recoverability likelihood (NOT a dollar).
    expect(screen.getByTestId(`drill-row-likelihood-${FID}`)).toHaveTextContent(
      '84%',
    );
    // The money is the honest secondary: real value + the child-count driver.
    expect(screen.getByTestId(`drill-row-value-${FID}`)).toHaveTextContent(
      '$31,200',
    );
    expect(screen.getByTestId(`drill-row-kids-${FID}`)).toHaveTextContent('3 kids');
    // The funding label rides under the name next to the stuck step.
    expect(r).toHaveTextContent('Texas voucher');
    expect(screen.getByTestId(`drill-row-age-${FID}`)).toHaveTextContent('12d');
    expect(screen.getByTestId(`drill-row-date-${FID}`)).toHaveTextContent('Jun 13');
  });

  it('shows NO rank and NO score column', () => {
    render(row());
    const r = screen.getByTestId(`drill-row-${FID}`);
    // The score readout ("0.91") is gone (it's not even a prop anymore).
    expect(r).not.toHaveTextContent('0.91');
    // The grid has 6 cells (ck, name+bar, hero, value, age, date) — the rail is a
    // border, and there is no 7th rank/score column.
    expect(DRILL_GRID.split(' ')).toHaveLength(6);
  });

  it('renders recency as a left-edge RAIL, not a Chip/pill', () => {
    render(row({ contactStatus: 'overdue' }));
    const r = screen.getByTestId(`drill-row-${FID}`);
    // The rail is the saturated signal rail for overdue.
    expect(r).toHaveClass('rail-overdue');
    expect(r).toHaveAttribute('data-rail', 'rail-overdue');
    // No "Overdue" pill text on the row (the rail replaced the word).
    expect(r).not.toHaveTextContent('Overdue');
  });

  it('railClass maps recency: overdue→signal, working→teal, fresh→neutral', () => {
    expect(railClass('overdue')).toBe('rail-overdue');
    expect(railClass('followed_up')).toBe('rail-working');
    expect(railClass('working')).toBe('rail-working');
    expect(railClass('fresh')).toBe('rail-fresh');
  });

  it('renders the magnitude bar (recoverability / likelihood width)', () => {
    render(row({ magnitude: 0.5 }));
    const bar = screen.getByTestId(`drill-row-bar-${FID}`);
    const fill = bar.firstElementChild as HTMLElement;
    expect(fill.style.width).toBe('50%');
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
