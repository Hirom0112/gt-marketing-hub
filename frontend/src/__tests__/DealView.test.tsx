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
    funding_type: 'tefa_standard',
    map_score: 0.82,
    attribution_source: 'Paid Search',
    crm_seam_status: 'synced',
    // Application submitted (100%), now stalled 2/6 into the ENROLLMENT packet.
    completion_pct: 100,
    forms_signed: 2,
    forms_total: 6,
    next_unsigned_form: 'health_form',
    contact_status: 'followed_up',
    last_contact_at: '2026-06-12T10:00:00Z',
  },
  family: {},
  lead: {},
  app_form: {},
};

// An application-stage family: still IN the application (60%), no enrollment forms
// started — must NOT show a misleading "stuck on form #1".
const APPLICATION_PAYLOAD = {
  deal_view: {
    display_name: 'The Vance Family',
    stall_reason: 'Application incomplete',
    funding_type: 'self_pay',
    map_score: null,
    attribution_source: 'Referral',
    crm_seam_status: 'unsynced',
    completion_pct: 60,
    forms_signed: 0,
    forms_total: 6,
    next_unsigned_form: 'enrollment_agreement',
    contact_status: 'overdue',
    last_contact_at: null,
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
    funding_type: 'self_pay',
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
    expect(screen.getByTestId('deal-funding-type')).toHaveTextContent(
      'Texas voucher',
    );
    expect(screen.getByTestId('deal-map-score')).toHaveTextContent('0.82');
    expect(screen.getByTestId('deal-attribution')).toHaveTextContent(
      'Paid Search',
    );
    expect(screen.getByTestId('deal-seam-status')).toHaveTextContent('synced');
  });

  it('fetches the family by id (GET)', async () => {
    render(<DealView familyId="fam-123" />);
    // The component fires two GETs on mount — the family record AND the CRM seam
    // status (S14 W4). Find the family call by URL rather than asserting a total
    // count, so the added /crm/status fetch doesn't break this assertion.
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const familyCall = fetchMock.mock.calls.find(([url]) =>
      /\/families\/fam-123$/.test(url as string),
    ) as [string, RequestInit?] | undefined;
    expect(familyCall).toBeTruthy();
    const [url, init] = familyCall as [string, RequestInit?];
    expect(url).toMatch(/\/families\/fam-123$/);
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('shows the recency tint + where-they-left-off drop-off', async () => {
    render(<DealView familyId="fam-123" />);

    // The contact recency chip carries the followed_up tone class (light-green).
    const recency = await screen.findByTestId('deal-recency');
    expect(recency).toHaveClass('recency-followed_up');

    // The drop-off block reads the ENROLLMENT-packet stage they're actually stuck
    // in (application already submitted) — not the always-100% application %.
    expect(screen.getByTestId('deal-completion')).toHaveTextContent(
      'Application ✓ submitted',
    );
    expect(screen.getByTestId('deal-completion')).toHaveTextContent(
      'Enrollment 2 of 6 forms',
    );
    // The stuck form name is humanized (underscores → spaces).
    expect(screen.getByTestId('deal-next-form')).toHaveTextContent(
      'health form',
    );
  });

  it('tracks application % (not enrollment) while still in the application', async () => {
    vi.unstubAllGlobals();
    mockFetch(APPLICATION_PAYLOAD);
    render(<DealView familyId="fam-app" />);

    // Pre-submit: the line tracks the application %, NOT "Enrollment 0 of 6".
    expect(await screen.findByTestId('deal-completion')).toHaveTextContent(
      '60% application complete',
    );
    expect(screen.getByTestId('deal-completion')).not.toHaveTextContent(
      'Enrollment',
    );
    // And it must NOT claim they're "stuck on" enrollment form #1 — they haven't
    // reached the packet yet (the misleading-signal guard).
    expect(screen.queryByTestId('deal-next-form')).toBeNull();
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
    // The completion ring (52px conic dial) shows the ENROLLMENT-packet progress
    // for a submitted family (2 of 6 = 33%), not the always-100% application %.
    expect(screen.getByTestId('completion-ring-label')).toHaveTextContent('33%');
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

// --------------------------------------------------------------------------- #
// S14 W4 — CRM seam badge + live-push kill switch. The deal view reads GET
// /crm/status and FAILS CLOSED (INV-3 pattern; INV-8): a positive kill_switch
// disables the "Seed to HubSpot" live-push and shows the operator a reason. An
// absent / unknown / errored status FAILS OPEN — the action stays enabled.
// --------------------------------------------------------------------------- #

// A CRM-status shape (GET /crm/status). NO secret: token_configured is a bool.
interface CrmStatusFixture {
  crm_mode: 'simulate' | 'live';
  kill_switch: boolean;
  effective_mode: 'simulate' | 'live';
  token_configured: boolean;
  calls_per_run_cap: number;
}

// A fetch stub that ROUTES by URL: GET /crm/status → the CRM-status fixture,
// GET /families/{id} → the family payload, POST …/seed → the seed response. This
// mirrors that the component fires two GETs on mount (family + crm/status).
function mockCrmFetch(
  crm: CrmStatusFixture,
  family: unknown = ENROLLED_PAYLOAD,
): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    if (/\/crm\/status$/.test(url)) {
      return { ok: true, status: 200, json: async () => crm };
    }
    if (init?.method === 'POST' && /\/seed$/.test(url)) {
      return { ok: true, status: 200, json: async () => SEED_RESPONSE };
    }
    return { ok: true, status: 200, json: async () => family };
  });
  vi.stubGlobal('fetch', fn);
  return fn as unknown as ReturnType<typeof vi.fn>;
}

describe('DealView — S14 W4 CRM seam badge + kill switch', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fails closed when the kill switch is ON: disables the live-push + shows the note', async () => {
    mockCrmFetch({
      crm_mode: 'live',
      kill_switch: true,
      effective_mode: 'simulate',
      token_configured: true,
      calls_per_run_cap: 50,
    });
    render(<DealView familyId="fam-123" />);

    // The live-push action is DISABLED (INV-8 fail-closed).
    const seed = await screen.findByTestId('seed-hubspot');
    expect(seed).toBeDisabled();
    // …with an operator-facing reason.
    expect(
      await screen.findByTestId('seed-kill-switch-note'),
    ).toHaveTextContent('Kill switch ON — live sync disabled');
    // …and the seam badge reads the kill-switch state.
    expect(await screen.findByTestId('crm-seam-badge')).toBeInTheDocument();
    expect(screen.getByTestId('crm-seam-state')).toHaveTextContent(
      'Kill switch ON — live sync disabled',
    );
  });

  it('shows CRM: LIVE and enables the live-push when effective mode is live', async () => {
    mockCrmFetch({
      crm_mode: 'live',
      kill_switch: false,
      effective_mode: 'live',
      token_configured: true,
      calls_per_run_cap: 50,
    });
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByTestId('crm-seam-state')).toHaveTextContent(
      'CRM: LIVE',
    );
    const seed = await screen.findByTestId('seed-hubspot');
    expect(seed).toBeEnabled();
    expect(screen.queryByTestId('seed-kill-switch-note')).toBeNull();
  });

  it('shows CRM: Simulated and enables the live-push when effective mode is simulate', async () => {
    mockCrmFetch({
      crm_mode: 'simulate',
      kill_switch: false,
      effective_mode: 'simulate',
      token_configured: false,
      calls_per_run_cap: 50,
    });
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByTestId('crm-seam-state')).toHaveTextContent(
      'CRM: Simulated',
    );
    const seed = await screen.findByTestId('seed-hubspot');
    expect(seed).toBeEnabled();
    expect(screen.queryByTestId('seed-kill-switch-note')).toBeNull();
  });

  it('fails OPEN when /crm/status is non-ok: no badge, no note, action enabled', async () => {
    // /crm/status 503 ⇒ the component never gets a CrmStatus ⇒ no badge, the
    // live-push stays enabled (a missing status never silently disables it).
    const fn = vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/crm\/status$/.test(url)) {
        return { ok: false, status: 503, json: async () => ({}) };
      }
      if (init?.method === 'POST' && /\/seed$/.test(url)) {
        return { ok: true, status: 200, json: async () => SEED_RESPONSE };
      }
      return { ok: true, status: 200, json: async () => ENROLLED_PAYLOAD };
    });
    vi.stubGlobal('fetch', fn);
    render(<DealView familyId="fam-123" />);

    // The deal loads and the live-push is enabled (fail open).
    const seed = await screen.findByTestId('seed-hubspot');
    expect(seed).toBeEnabled();
    expect(screen.queryByTestId('crm-seam-badge')).toBeNull();
    expect(screen.queryByTestId('seed-kill-switch-note')).toBeNull();
  });

  it('fails OPEN when the fetch rejects: action enabled, no kill-switch note', async () => {
    const fn = vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/crm\/status$/.test(url)) {
        throw new Error('network down');
      }
      if (init?.method === 'POST' && /\/seed$/.test(url)) {
        return { ok: true, status: 200, json: async () => SEED_RESPONSE };
      }
      return { ok: true, status: 200, json: async () => ENROLLED_PAYLOAD };
    });
    vi.stubGlobal('fetch', fn);
    render(<DealView familyId="fam-123" />);

    const seed = await screen.findByTestId('seed-hubspot');
    expect(seed).toBeEnabled();
    expect(screen.queryByTestId('crm-seam-badge')).toBeNull();
    expect(screen.queryByTestId('seed-kill-switch-note')).toBeNull();
  });

  it('fails OPEN on an unknown status shape: the type guard rejects it, action enabled', async () => {
    // /crm/status resolves 200 but to some OTHER payload (no kill_switch field).
    // isCrmStatus must reject it ⇒ no badge, action enabled (no silent disable).
    mockCrmFetch(
      { unexpected: 'shape' } as unknown as CrmStatusFixture,
    );
    render(<DealView familyId="fam-123" />);

    const seed = await screen.findByTestId('seed-hubspot');
    expect(seed).toBeEnabled();
    expect(screen.queryByTestId('crm-seam-badge')).toBeNull();
    expect(screen.queryByTestId('seed-kill-switch-note')).toBeNull();
  });
});
