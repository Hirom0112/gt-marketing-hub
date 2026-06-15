import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

// --------------------------------------------------------------------------- #
// S10 W3 — "Seed to HubSpot" button + capture/trace panel (proof-of-capture).
// --------------------------------------------------------------------------- #

// A seed-route response shape (POST /enrollment/families/{id}/seed).
const SEED_RESPONSE = {
  family_id: 'fam-123',
  simulated: false,
  deal_id: 'deal-99887766',
  contact_id: 'contact-11223344',
  stage: 'interest',
  seam_status: 'synced',
};

// A fetch stub that serves the GET /families/{id} payload, then the seed POST.
function mockSeedFetch(): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && /\/seed$/.test(url)) {
      return { ok: true, status: 200, json: async () => SEED_RESPONSE };
    }
    return { ok: true, status: 200, json: async () => INTEREST_PAYLOAD };
  });
  vi.stubGlobal('fetch', fn);
  return fn as unknown as ReturnType<typeof vi.fn>;
}

describe('DealView — Seed to HubSpot capture panel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the Seed to HubSpot button', async () => {
    mockSeedFetch();
    render(<DealView familyId="fam-123" />);
    expect(await screen.findByTestId('seed-hubspot')).toBeInTheDocument();
  });

  it('calls the seed route and shows the capture panel with live deep links', async () => {
    const fn = mockSeedFetch();
    render(<DealView familyId="fam-123" />);

    fireEvent.click(await screen.findByTestId('seed-hubspot'));

    // The capture panel surfaces once the seed succeeds.
    const panel = await screen.findByTestId('capture-panel');
    expect(panel).toBeInTheDocument();

    // It POSTed the seed route for this family.
    const seedCall = fn.mock.calls.find(
      ([url, init]) =>
        typeof url === 'string' &&
        /\/enrollment\/families\/fam-123\/seed$/.test(url) &&
        (init as RequestInit | undefined)?.method === 'POST',
    );
    expect(seedCall).toBeTruthy();

    // The Deal + Contact deep links point at the live portal record routes.
    const dealLink = screen.getByTestId('capture-deal-link');
    expect(dealLink).toHaveAttribute(
      'href',
      'https://app-na2.hubspot.com/contacts/246504420/record/0-3/deal-99887766',
    );
    const contactLink = screen.getByTestId('capture-contact-link');
    expect(contactLink).toHaveAttribute(
      'href',
      'https://app-na2.hubspot.com/contacts/246504420/record/0-1/contact-11223344',
    );

    // The seam badge flips to synced.
    expect(screen.getByTestId('capture-seam-status')).toHaveTextContent(
      'synced',
    );
  });
});

// --------------------------------------------------------------------------- #
// S12 W4 — work-panel additions: recovery-state tag, completion ring, seam dot,
// and the audited "Dismiss this family" reason picker (delegated write, INV-2).
// --------------------------------------------------------------------------- #

const STALLED_PAYLOAD = {
  deal_view: {
    ...ENROLLED_PAYLOAD.deal_view,
    recovery_state: 'stalled',
  },
  family: {},
  lead: {},
  app_form: {},
};

describe('DealView — S12 W4 work-panel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the recovery-state tag, completion ring, and a seam dot', async () => {
    mockFetch(STALLED_PAYLOAD);
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByTestId('deal-recovery-state')).toHaveTextContent(
      'Stalled',
    );
    // The completion ring (52px conic dial) shows the rounded application %.
    expect(screen.getByTestId('completion-ring-label')).toHaveTextContent('46%');
    // The seam field carries a colour-coded SeamDot alongside the named status.
    expect(screen.getByTestId('seam-dot')).toHaveAttribute('data-seam', 'synced');
  });

  it('opens the dismiss reason picker and delegates the write (no client write)', async () => {
    mockFetch(STALLED_PAYLOAD);
    const onDismiss = vi.fn();
    render(
      <DealView
        familyId="fam-123"
        dismissReasons={['Declined', 'Bad fit']}
        onDismiss={onDismiss}
      />,
    );

    fireEvent.click(await screen.findByTestId('dismiss-family-start'));
    // The reason rail appears; picking a reason calls back with (id, reason).
    fireEvent.click(
      await screen.findByTestId('dismiss-family-reason-Declined'),
    );
    expect(onDismiss).toHaveBeenCalledWith('fam-123', 'Declined');
  });
});
