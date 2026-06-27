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

// The R2 voucher-standing fields the enriched GET …/funding view now carries
// (program / next_action / due_by / days_remaining / at_risk /
// award_full_vs_prorated). A confirmed family has no open reconfirm gap.
const CONFIRMED_STANDING = {
  program: 'tx_tefa',
  next_action: 'Voucher confirmed · no action needed.',
  due_by: null,
  days_remaining: null,
  at_risk: false,
  award_full_vs_prorated: 'full',
};

// A TEFA family whose first installment has been received ⇒ tuition unlocked.
const UNLOCKED_PAYLOAD = {
  family_id: 'fam-a',
  funding_state: 'first_installment_received',
  funding_type: 'tefa_standard',
  installments: ['2618.50', '2618.50', '5237.00'],
  tuition_unlocked: true,
  ...CONFIRMED_STANDING,
};

// A TEFA family still awaiting the first installment ⇒ tuition locked.
const LOCKED_PAYLOAD = {
  family_id: 'fam-b',
  funding_state: 'awaiting_first_installment',
  funding_type: 'tefa_standard',
  installments: ['2618.50', '2618.50', '5237.00'],
  tuition_unlocked: false,
  ...CONFIRMED_STANDING,
};

// A self-pay family: no TEFA schedule (installments:null), tuition locked.
const SELF_PAY_PAYLOAD = {
  family_id: 'fam-c',
  funding_state: 'self_pay',
  funding_type: null,
  installments: null,
  tuition_unlocked: false,
  ...CONFIRMED_STANDING,
};

// An AWARDED-but-not-reconfirmed family near its select-by cutoff: the voucher
// standing carries an open deadline, an at-risk flag, and a next-action line. The
// demo's "ranked to the top of the work queue" family (ENROLLMENT_REFACTOR §8.2).
const AT_RISK_PAYLOAD = {
  family_id: 'fam-d',
  funding_state: 'awarded',
  funding_type: 'tefa_standard',
  installments: ['2618.50', '2618.50', '5237.00'],
  tuition_unlocked: false,
  program: 'tx_tefa',
  next_action: 'Family must reconfirm GT in the voucher portal.',
  due_by: '2026-07-01',
  days_remaining: 5,
  at_risk: true,
  award_full_vs_prorated: 'prorated',
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

  it('Test D: an awarded-but-unreconfirmed family shows the countdown, next-action line, and an at-risk badge', async () => {
    vi.unstubAllGlobals();
    mockFetch(AT_RISK_PAYLOAD);
    render(<FundingTracker familyId="fam-d" />);

    // The deadline countdown reads from days_remaining (and surfaces the due date).
    const countdown = await screen.findByTestId('voucher-countdown');
    expect(countdown).toHaveTextContent('5');
    expect(countdown).toHaveTextContent(/day/i);

    // The next-action line is the single next step the family must take.
    expect(screen.getByTestId('voucher-next-action')).toHaveTextContent(
      'Family must reconfirm GT in the voucher portal.',
    );

    // The at-risk badge is shown (selected/awarded, deadline at hand, prorating).
    const badge = screen.getByTestId('voucher-at-risk-badge');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent(/at[\s-]?risk/i);
  });

  it('Test E: a confirmed family (due_by:null) shows NO countdown and NO at-risk badge (fail-closed)', async () => {
    vi.unstubAllGlobals();
    mockFetch(UNLOCKED_PAYLOAD);
    render(<FundingTracker familyId="fam-a" />);

    await screen.findByText('first_installment_received');
    // No open reconfirm gap ⇒ no countdown clock and no at-risk badge. The voucher
    // lane never invents a deadline or risk it can't prove (fail-closed, INV-10).
    expect(screen.queryByTestId('voucher-countdown')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('voucher-at-risk-badge'),
    ).not.toBeInTheDocument();
    // The next-action line still renders the confirmed copy (a proven statement).
    expect(screen.getByTestId('voucher-next-action')).toHaveTextContent(
      'Voucher confirmed',
    );
  });
});
