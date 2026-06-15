import { describe, expect, it } from 'vitest';
import { summarizeRecovery } from '../recency';

// Unit test (CLAUDE §4.1) for the pure situation-bar summary. The headline
// figures are DERIVED from the /work-queue rows — never hardcoded (INV-11
// spirit). Stalled = still recoverable AND not yet followed up (fresh/overdue);
// overdue = contact_status 'overdue'; recoverable $ = sum of value over rows not
// closed.
describe('summarizeRecovery', () => {
  it('derives stalled / overdue / recoverable $ from the rows', () => {
    const rows = [
      { value: 10474, contact_status: 'overdue' },
      { value: 30000, contact_status: 'fresh' },
      { value: 2000, contact_status: 'followed_up' },
      { value: 9999, contact_status: 'closed' },
    ];
    const s = summarizeRecovery(rows);
    // overdue + fresh are stalled (un-actioned); followed_up + closed are not.
    expect(s.stalled).toBe(2);
    // only the overdue row.
    expect(s.overdue).toBe(1);
    // everything except the closed (won, off-worklist) row.
    expect(s.recoverableValue).toBe(10474 + 30000 + 2000);
  });

  it('treats an unknown / absent status as a recoverable, stalled row', () => {
    const rows = [
      { value: 500, contact_status: null },
      { value: 700 },
    ];
    const s = summarizeRecovery(rows);
    expect(s.stalled).toBe(2);
    expect(s.overdue).toBe(0);
    expect(s.recoverableValue).toBe(1200);
  });

  it('returns zeros for an empty queue', () => {
    expect(summarizeRecovery([])).toEqual({
      stalled: 0,
      overdue: 0,
      recoverableValue: 0,
    });
  });
});
