import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FundingTracker from '../FundingTracker';

// Acceptance test (CLAUDE §4.2). The funding tracker (FR-2.6/2.7) fetches GET
// /families/{id}/funding and surfaces the funding state, the funding tier
// (funding_type), the TEFA installment schedule, and a tuition LOCK badge. The
// tuition unlock gate (INV-10) is GT-controlled: `tuition_unlocked` reflects a
// confirmed first-installment receipt — the UI renders it as a visible badge.
// Self-pay families have no installment schedule (installments:null) and must
// not crash. Native fetch only (≤2 runtime deps). Read-only (INV-2).

// A TEFA family whose first installment has been received ⇒ tuition unlocked.
const UNLOCKED_PAYLOAD = {
  family_id: 'fam-a',
  funding_state: 'first_installment_received',
  funding_type: 'tefa_standard',
  installments: ['2618.50', '2618.50', '5237.00'],
  tuition_unlocked: true,
};

// A TEFA family still awaiting the first installment ⇒ tuition locked.
const LOCKED_PAYLOAD = {
  family_id: 'fam-b',
  funding_state: 'awaiting_first_installment',
  funding_type: 'tefa_standard',
  installments: ['2618.50', '2618.50', '5237.00'],
  tuition_unlocked: false,
};

// A self-pay family: no TEFA schedule (installments:null), tuition locked.
const SELF_PAY_PAYLOAD = {
  family_id: 'fam-c',
  funding_state: 'self_pay',
  funding_type: null,
  installments: null,
  tuition_unlocked: false,
};

function mockFetch(payload: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => payload,
    })),
  );
}

describe('FundingTracker', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    mockFetch(UNLOCKED_PAYLOAD);
  });

  it('fetches the funding endpoint for the family (GET)', async () => {
    render(<FundingTracker familyId="fam-a" />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/families\/fam-a\/funding$/);
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('Test A: tuition_unlocked:true shows the funding state, tier, schedule, and an unlocked badge', async () => {
    render(<FundingTracker familyId="fam-a" />);

    expect(
      await screen.findByText('first_installment_received'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('funding-type')).toHaveTextContent('Texas voucher');

    // The three TEFA installment amounts render in order.
    const schedule = screen.getByTestId('installment-schedule');
    expect(schedule).toHaveTextContent('2618.50');
    expect(schedule).toHaveTextContent('5237.00');
    const rows = screen.getAllByTestId('installment-row');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('2618.50');
    expect(rows[1]).toHaveTextContent('2618.50');
    expect(rows[2]).toHaveTextContent('5237.00');

    // The tuition lock badge reads UNLOCKED.
    const badge = screen.getByTestId('tuition-badge');
    expect(badge).toHaveTextContent(/unlocked/i);

    // Unlocked ⇒ the schedule is the REAL installment schedule, not "projected".
    const caption = screen.getByTestId('installment-caption');
    expect(caption).toHaveTextContent('TEFA installment schedule');
    expect(caption).not.toHaveTextContent(/projected/i);
  });

  it('Test B: tuition_unlocked:false shows a locked badge + a PROJECTED schedule', async () => {
    vi.unstubAllGlobals();
    mockFetch(LOCKED_PAYLOAD);
    render(<FundingTracker familyId="fam-b" />);

    await screen.findByText('awaiting_first_installment');
    const badge = screen.getByTestId('tuition-badge');
    expect(badge).toHaveTextContent(/locked/i);
    expect(badge).not.toHaveTextContent(/unlocked/i);

    // Locked ⇒ the ladder is labelled PROJECTED (pending award/first installment),
    // so it never reads as "voucher connected" next to an early funding state.
    expect(screen.getByTestId('installment-caption')).toHaveTextContent(
      /projected/i,
    );
  });

  it('Test C: installments:null (self-pay) renders no schedule and does not crash', async () => {
    vi.unstubAllGlobals();
    mockFetch(SELF_PAY_PAYLOAD);
    render(<FundingTracker familyId="fam-c" />);

    expect(await screen.findByText('self_pay')).toBeInTheDocument();
    // No TEFA schedule for self-pay families.
    expect(screen.queryByTestId('installment-schedule')).not.toBeInTheDocument();
    expect(screen.queryByTestId('installment-row')).not.toBeInTheDocument();
    // A null funding_type renders as a dash placeholder, never literal "null".
    expect(screen.getByTestId('funding-type')).not.toHaveTextContent('null');
    // Self-pay is still tuition locked.
    expect(screen.getByTestId('tuition-badge')).toHaveTextContent(/locked/i);
  });
});
