import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DealView from '../DealView';

// Acceptance test (CLAUDE §4.2). The deal view (FR-2.2) fetches GET
// /families/{id} and surfaces the deal_view fields: stall reason, funding type,
// MAP signal (map_score), attribution source, and CRM seam status. Native fetch
// only (≤12-dep budget). Read-only (INV-2).

// A funded, enrolled family with a full deal_view.
const ENROLLED_PAYLOAD = {
  deal_view: {
    display_name: 'The Rivera Family',
    stall_reason: 'Awaiting funding confirmation',
    funding_type: 'TEFA',
    map_score: 0.82,
    attribution_source: 'Paid Search',
    crm_seam_status: 'synced',
    completion_pct: 45.6,
    forms_signed: 2,
    forms_total: 6,
    next_unsigned_form: 'media_authorization',
    contact_status: 'followed_up',
    last_contact_at: '2026-06-12T10:00:00Z',
  },
  family: {},
  lead: {},
  app_form: {},
};

// An interest-stage family: no app_form yet ⇒ null map_score / null stall.
const INTEREST_PAYLOAD = {
  deal_view: {
    display_name: 'The Okafor Family',
    stall_reason: null,
    funding_type: 'Self-pay',
    map_score: null,
    attribution_source: 'Referral',
    crm_seam_status: 'unsynced',
  },
  family: {},
  lead: {},
  app_form: null,
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

describe('DealView', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    mockFetch(ENROLLED_PAYLOAD);
  });

  it('renders the deal view fields from GET /families/{id}', async () => {
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByText('The Rivera Family')).toBeInTheDocument();
    expect(
      await screen.findByText('Awaiting funding confirmation'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('deal-funding-type')).toHaveTextContent('TEFA');
    expect(screen.getByTestId('deal-map-score')).toHaveTextContent('0.82');
    expect(screen.getByTestId('deal-attribution')).toHaveTextContent(
      'Paid Search',
    );
    expect(screen.getByTestId('deal-seam-status')).toHaveTextContent('synced');
  });

  it('fetches the family by id (GET)', async () => {
    render(<DealView familyId="fam-123" />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/families\/fam-123$/);
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('shows the recency tint + where-they-left-off drop-off', async () => {
    render(<DealView familyId="fam-123" />);

    // The contact recency chip carries the followed_up tone class (light-green).
    const recency = await screen.findByTestId('deal-recency');
    expect(recency).toHaveClass('recency-followed_up');

    // The drop-off block surfaces completion %, form progress, and the stuck form.
    expect(screen.getByTestId('deal-completion')).toHaveTextContent(
      '45.6% application complete',
    );
    expect(screen.getByTestId('deal-completion')).toHaveTextContent(
      '2/6 forms signed',
    );
    expect(screen.getByTestId('deal-next-form')).toHaveTextContent(
      'media_authorization',
    );
  });

  it('handles a null map_score and null stall_reason gracefully', async () => {
    vi.unstubAllGlobals();
    mockFetch(INTEREST_PAYLOAD);
    render(<DealView familyId="fam-456" />);

    expect(await screen.findByText('The Okafor Family')).toBeInTheDocument();
    // No app_form ⇒ no MAP score yet; shown as a dash placeholder, not "null".
    expect(screen.getByTestId('deal-map-score')).toHaveTextContent('—');
    expect(screen.getByTestId('deal-stall-reason')).toHaveTextContent('—');
    expect(screen.getByTestId('deal-funding-type')).toHaveTextContent(
      'Self-pay',
    );
  });
});
