import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SisBucketsPanel from '../enrollment/SisBucketsPanel';

// M5 acceptance (MULTI_AGENT_COCKPIT.md §6; TODO.md M5). The admin SIS reconcile
// panel renders the buckets from GET /enrollment/sis-buckets and, per bucket,
// exposes the right human action — 🔴 paid_not_in_sis → ASSIGN, 🟡 records_lag →
// PROPOSE (on the decision spine, never a silent write, INV-2/INV-4). The payload
// is the PII firewall (only family_id/present/confirmed_at/bucket).

// Synthetic UUID-shaped ids (no PII, INV-1).
const FAM_ABSENT = '11111111-0000-4000-8000-000000000001';
const FAM_LAG = '22222222-0000-4000-8000-000000000002';
const FAM_AMB = '33333333-0000-4000-8000-000000000003';
const FAM_OK1 = '44444444-0000-4000-8000-000000000004';
const FAM_OK2 = '55555555-0000-4000-8000-000000000005';

const PAYLOAD = {
  buckets: [
    {
      bucket: 'paid_not_in_sis',
      count: 1,
      families: [
        { family_id: FAM_ABSENT, present: false, confirmed_at: null, bucket: 'paid_not_in_sis' },
      ],
    },
    {
      bucket: 'records_lag',
      count: 1,
      families: [
        { family_id: FAM_LAG, present: true, confirmed_at: null, bucket: 'records_lag' },
      ],
    },
    {
      bucket: 'ambiguous',
      count: 1,
      families: [
        { family_id: FAM_AMB, present: true, confirmed_at: null, bucket: 'ambiguous' },
      ],
    },
    {
      bucket: 'confirmed',
      count: 2,
      families: [
        { family_id: FAM_OK1, present: true, confirmed_at: '2026-06-10T00:00:00Z', bucket: 'confirmed' },
        { family_id: FAM_OK2, present: true, confirmed_at: '2026-06-11T00:00:00Z', bucket: 'confirmed' },
      ],
    },
  ],
  total: 5,
};

function installFetch(body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
      } as Response),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('SisBucketsPanel (M5 acceptance)', () => {
  it('renders the SIS buckets; 🔴 exposes Assign, 🟡 exposes Propose', async () => {
    installFetch(PAYLOAD);
    render(<SisBucketsPanel />);

    const absent = await screen.findByTestId('sis-bucket-paid_not_in_sis');
    const lag = screen.getByTestId('sis-bucket-records_lag');
    const amb = screen.getByTestId('sis-bucket-ambiguous');
    const confirmed = screen.getByTestId('sis-bucket-confirmed');

    // buckets render with their counts
    expect(screen.getByTestId('sis-bucket-count-paid_not_in_sis').textContent).toContain('1');
    expect(screen.getByTestId('sis-bucket-count-records_lag').textContent).toContain('1');
    expect(screen.getByTestId('sis-bucket-count-confirmed').textContent).toContain('2');

    // 🔴 paid_not_in_sis exposes Assign and NOT Propose
    expect(within(absent).getByRole('button', { name: 'Assign' })).toBeTruthy();
    expect(within(absent).queryByRole('button', { name: 'Propose' })).toBeNull();

    // 🟡 records_lag exposes Propose (the decision-spine action) and NOT Assign
    expect(within(lag).getByRole('button', { name: 'Propose' })).toBeTruthy();
    expect(within(lag).queryByRole('button', { name: 'Assign' })).toBeNull();

    // ⚪ ambiguous → Review (merge queue); ✅ confirmed → no action button
    expect(within(amb).getByRole('button', { name: 'Review' })).toBeTruthy();
    expect(within(confirmed).queryByRole('button')).toBeNull();
  });

  it('delegates Assign/Propose to the parent — never a silent write', async () => {
    installFetch(PAYLOAD);
    const onAssign = vi.fn();
    const onPropose = vi.fn();
    render(<SisBucketsPanel onAssign={onAssign} onPropose={onPropose} />);

    const absent = await screen.findByTestId('sis-bucket-paid_not_in_sis');
    fireEvent.click(within(absent).getByRole('button', { name: 'Assign' }));
    expect(onAssign).toHaveBeenCalledWith(FAM_ABSENT);

    const lag = screen.getByTestId('sis-bucket-records_lag');
    fireEvent.click(within(lag).getByRole('button', { name: 'Propose' }));
    expect(onPropose).toHaveBeenCalledWith(FAM_LAG);

    // Only the initial GET happened — the panel itself performs no write.
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
