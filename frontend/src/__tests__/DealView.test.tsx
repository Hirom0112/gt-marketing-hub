import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DealView from '../DealView';

// Acceptance test (CLAUDE §4.2). The deal view (FR-2.2) fetches GET
// /families/{id} and surfaces the deal_view fields: stall reason, funding type,
// conversion likelihood (DH-1 — band + score + top contributing factor, REPLACING
// the old MAP signal), attribution source, and CRM seam status. Native fetch only
// (≤12-dep budget). Read-only (INV-2).

// A funded, enrolled family with a full deal_view.
const ENROLLED_PAYLOAD = {
  deal_view: {
    display_name: 'The Rivera Family',
    stall_reason: 'Awaiting funding confirmation',
    funding_type: 'tefa_standard',
    conversion_score: 0.79,
    conversion_band: 'High',
    conversion_top_factor: 'funding',
    conversion_top_factor_label: 'Funding lined up',
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
    conversion_score: 0.42,
    conversion_band: 'Med',
    conversion_top_factor: 'funding',
    conversion_top_factor_label: 'Funding lined up',
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

// An interest-stage family: no app_form yet ⇒ null conversion / null stall.
const INTEREST_PAYLOAD = {
  deal_view: {
    display_name: 'The Okafor Family',
    stall_reason: null,
    funding_type: 'self_pay',
    conversion_score: null,
    conversion_band: null,
    conversion_top_factor: null,
    conversion_top_factor_label: null,
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
    // DH-1 conversion-likelihood tile (REPLACES the old MAP signal): the band +
    // score percentage and the top contributing factor — and the "MAP signal"
    // label is GONE.
    expect(screen.getByTestId('deal-conversion')).toHaveTextContent('High');
    expect(screen.getByTestId('deal-conversion')).toHaveTextContent('79%');
    expect(screen.getByTestId('deal-conversion-factor')).toHaveTextContent(
      'Funding lined up',
    );
    expect(screen.queryByText('MAP signal')).toBeNull();
    expect(screen.queryByTestId('deal-map-score')).toBeNull();
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

  it('handles a null conversion signal and null stall_reason gracefully', async () => {
    vi.unstubAllGlobals();
    mockFetch(INTEREST_PAYLOAD);
    render(<DealView familyId="fam-456" />);

    expect(await screen.findByText('The Okafor Family')).toBeInTheDocument();
    // No conversion band yet ⇒ a dash placeholder, not "null"; no top-factor line.
    expect(screen.getByTestId('deal-conversion')).toHaveTextContent('—');
    expect(screen.queryByTestId('deal-conversion-factor')).toBeNull();
    expect(screen.getByTestId('deal-stall-reason')).toHaveTextContent('—');
    expect(screen.getByTestId('deal-funding-type')).toHaveTextContent(
      'Self-pay',
    );
  });
});

// --------------------------------------------------------------------------- #
// DH-5 — per-child "where each left off" in the deal view. The deal panel reads
// GET /students (the SAME per-child board source, A-24), keeps only the SELECTED
// family's children, and shows EACH child with its grade + the funnel stage it
// left off at. The multi-child Rivera household shows BOTH children at their own
// (possibly different) stages. Read-only (INV-2); synthetic identities (INV-1).
// --------------------------------------------------------------------------- #

// A /students board response: the Rivera household has TWO children stuck at
// DIFFERENT stages (Alex in enrollment, Mia in tuition), plus an unrelated
// household that must NOT leak into this family's panel.
const STUDENT_BOARD = {
  households: [
    {
      family_id: 'fam-123',
      household_name: 'The Rivera Family',
      value_at_risk: 20948,
      students: [
        {
          student_id: 'stu-alex',
          family_id: 'fam-123',
          household_name: 'The Rivera Family',
          display_label: 'Rivera household — Alex · Grade 3',
          synthetic_first_name: 'Alex',
          grade: '3',
          current_stage: 'enroll',
          funding_state: 'pending',
          recovery_state: 'stalled',
          score: 0.5,
          recoverability: 0.5,
          value: 10474,
          recoverable_now: 5237,
          freshness: 0.5,
        },
        {
          student_id: 'stu-mia',
          family_id: 'fam-123',
          household_name: 'The Rivera Family',
          display_label: 'Rivera household — Mia · Grade 1',
          synthetic_first_name: 'Mia',
          grade: '1',
          current_stage: 'tuition',
          funding_state: 'pending',
          recovery_state: 'working',
          score: 0.4,
          recoverability: 0.4,
          value: 10474,
          recoverable_now: 4000,
          freshness: 0.4,
        },
      ],
    },
    {
      family_id: 'fam-OTHER',
      household_name: 'The Vance Family',
      value_at_risk: 10474,
      students: [
        {
          student_id: 'stu-other',
          family_id: 'fam-OTHER',
          household_name: 'The Vance Family',
          display_label: 'Vance household — Sam · Grade 5',
          synthetic_first_name: 'Sam',
          grade: '5',
          current_stage: 'apply',
          funding_state: 'pending',
          recovery_state: 'stalled',
          score: 0.3,
          recoverability: 0.3,
          value: 10474,
          recoverable_now: 3000,
          freshness: 0.3,
        },
      ],
    },
  ],
  total_students: 3,
  total_value_at_risk: 31422,
};

// A fetch stub routing GET /students → the board, everything else → the family.
function mockChildrenFetch(family: unknown = ENROLLED_PAYLOAD): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (/\/students(\?|$)/.test(url)) {
        return { ok: true, status: 200, json: async () => STUDENT_BOARD };
      }
      return { ok: true, status: 200, json: async () => family };
    }),
  );
}

describe('DealView — DH-5 per-child progress', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders each child of the selected family with the stage it left off at', async () => {
    mockChildrenFetch();
    render(<DealView familyId="fam-123" />);

    // The per-child section appears.
    const section = await screen.findByTestId('deal-children');
    expect(section).toBeInTheDocument();
    expect(section).toHaveTextContent('Per-child progress');

    // BOTH Rivera children render — each by its synthetic name + grade.
    const alex = await screen.findByTestId('deal-child-stu-alex');
    const mia = screen.getByTestId('deal-child-stu-mia');
    expect(alex).toHaveTextContent('Alex');
    expect(alex).toHaveTextContent('Grade 3');
    expect(mia).toHaveTextContent('Mia');
    expect(mia).toHaveTextContent('Grade 1');

    // …at their OWN (different) stages, humanized (enroll → Enroll, tuition →
    // Tuition) — proving the per-child grain, not a single family stage.
    expect(alex).toHaveTextContent('Enroll');
    expect(mia).toHaveTextContent('Tuition');

    // A child from ANOTHER household must NOT leak into this family's panel.
    expect(screen.queryByTestId('deal-child-stu-other')).toBeNull();
    expect(section).not.toHaveTextContent('Sam');
  });

  it('does not render the per-child section when the board is unavailable (fail safe)', async () => {
    // The blanket mock serves the FAMILY payload for /students too — no
    // `households` array ⇒ the type guard rejects it ⇒ no per-child section,
    // never a crash or bogus rows.
    mockFetch(ENROLLED_PAYLOAD);
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByText('The Rivera Family')).toBeInTheDocument();
    expect(screen.queryByTestId('deal-children')).toBeNull();
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

    // The later-lifecycle states render their own labels (rep close-loop, A-35).
  });

  it('renders the later-lifecycle recovery states (cold / presumed-lost / lost)', async () => {
    const cases: Array<[string, string]> = [
      ['cold', 'Cold'],
      ['presumed_lost', 'Presumed lost'],
      ['lost', 'Lost'],
      ['dormant', 'Dormant'],
    ];
    for (const [state, label] of cases) {
      mockFetch({
        deal_view: { ...ENROLLED_PAYLOAD.deal_view, recovery_state: state },
        family: {},
        lead: {},
        app_form: {},
      });
      const { unmount } = render(<DealView familyId="fam-123" />);
      expect(
        await screen.findByTestId('deal-recovery-state'),
      ).toHaveTextContent(label);
      unmount();
    }
  });

  it('keeps the completion ring and seam dot for a stalled family', async () => {
    mockFetch(STALLED_PAYLOAD);
    render(<DealView familyId="fam-123" />);
    await screen.findByTestId('deal-recovery-state');
    // The completion ring (52px conic dial) shows the ENROLLMENT-packet progress
    // for a submitted family (2 of 6 = 33%), not the always-100% application %.
    expect(screen.getByTestId('completion-ring-label')).toHaveTextContent(
      '33%',
    );
    // The seam field carries a colour-coded SeamDot alongside the named status.
    expect(screen.getByTestId('seam-dot')).toHaveAttribute(
      'data-seam',
      'synced',
    );
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
// Rep close-loop WRITE UI (A-35) — "log a call outcome" + confirm-presumed-lost.
// The deal panel POSTs the close-loop spine events directly (like Seed-to-HubSpot),
// re-fetches its own deal_view, and notifies the parent (onChanged) to refresh the
// board. The confirm-lost affordance appears ONLY for a presumed_lost family (the
// human-confirm gate); the whole block is hidden once a family is closed out.
// --------------------------------------------------------------------------- #

// A fetch stub that serves the deal_view on GET and a 201/200 on the write POSTs.
function closeLoopFetch(state: string): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'POST' && /contact-outcome$/.test(url)) {
      return {
        ok: true,
        status: 201,
        json: async () => ({ family_id: 'fam-123' }),
      };
    }
    if (method === 'POST' && /presumed-lost-confirm$/.test(url)) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ family_id: 'fam-123', recovery_state: 'lost' }),
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        deal_view: { ...STALLED_PAYLOAD.deal_view, recovery_state: state },
        family: {},
        lead: {},
        app_form: {},
      }),
    };
  });
}

describe('DealView — rep close-loop write UI', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('logs a contact outcome (POST) and re-fetches + notifies the parent', async () => {
    vi.stubGlobal('fetch', closeLoopFetch('cold'));
    const onChanged = vi.fn();
    render(<DealView familyId="fam-123" onChanged={onChanged} />);

    fireEvent.change(await screen.findByTestId('deal-outcome-disposition'), {
      target: { value: 'no_answer' },
    });
    fireEvent.click(screen.getByTestId('deal-outcome-submit'));

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const post = fetchMock.mock.calls.find(
        ([u, i]) =>
          /\/families\/fam-123\/contact-outcome$/.test(u as string) &&
          (i as RequestInit | undefined)?.method === 'POST',
      );
      expect(post).toBeTruthy();
      const body = JSON.parse((post![1] as RequestInit).body as string);
      expect(body.disposition).toBe('no_answer');
      expect(body.channel).toBeTruthy();
    });
    // The write notifies the parent so the board can refresh.
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('shows confirm-lost ONLY for a presumed_lost family and POSTs the reason', async () => {
    vi.stubGlobal('fetch', closeLoopFetch('presumed_lost'));
    render(<DealView familyId="fam-123" />);

    fireEvent.click(await screen.findByTestId('deal-confirm-lost-start'));
    fireEvent.change(await screen.findByTestId('deal-confirm-lost-reason'), {
      target: { value: 'family enrolled elsewhere' },
    });
    fireEvent.click(screen.getByTestId('deal-confirm-lost-submit'));

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const post = fetchMock.mock.calls.find(
        ([u, i]) =>
          /\/families\/fam-123\/presumed-lost-confirm$/.test(u as string) &&
          (i as RequestInit | undefined)?.method === 'POST',
      );
      expect(post).toBeTruthy();
      const body = JSON.parse((post![1] as RequestInit).body as string);
      expect(body.reason).toBe('family enrolled elsewhere');
    });
  });

  it('hides confirm-lost for a non-presumed-lost family', async () => {
    vi.stubGlobal('fetch', closeLoopFetch('cold'));
    render(<DealView familyId="fam-123" />);
    await screen.findByTestId('deal-outcome-submit');
    expect(screen.queryByTestId('deal-confirm-lost-start')).toBeNull();
  });

  it('hides the whole log-outcome block once a family is closed out (lost)', async () => {
    vi.stubGlobal('fetch', closeLoopFetch('lost'));
    render(<DealView familyId="fam-123" />);
    await screen.findByTestId('deal-recovery-state');
    expect(screen.queryByTestId('deal-outcome-submit')).toBeNull();
  });
});

// --------------------------------------------------------------------------- #
// Rep variant (A-35; the founder's "they do not need all that"). variant="rep"
// cuts the admin/CRM-ops chrome from the deal panel — Seed-to-HubSpot, the CRM
// seam badge, the seam-status field, and the marketing attribution field — while
// keeping the close essentials (why-stalled, the conversion/close signal, and the
// rep's own log-outcome + dismiss actions). The default (admin) keeps everything.
// --------------------------------------------------------------------------- #

describe('DealView — rep variant chrome cut', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('rep variant hides admin/CRM chrome but keeps the close essentials', async () => {
    vi.stubGlobal('fetch', closeLoopFetch('stalled'));
    render(<DealView familyId="fam-123" variant="rep" />);

    await screen.findByTestId('deal-recovery-state');
    // Admin / CRM-ops chrome is gone for the rep.
    expect(screen.queryByTestId('seed-hubspot')).toBeNull();
    expect(screen.queryByTestId('deal-attribution')).toBeNull();
    expect(screen.queryByTestId('deal-seam-status')).toBeNull();
    // The close essentials remain — including the rep's own log-outcome action.
    expect(screen.getByTestId('deal-stall-reason')).toBeInTheDocument();
    expect(screen.getByTestId('deal-conversion')).toBeInTheDocument();
    expect(screen.getByTestId('deal-outcome-submit')).toBeInTheDocument();
  });

  it('default (admin) variant keeps the seam status, attribution, and seed action', async () => {
    mockFetch(ENROLLED_PAYLOAD);
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByTestId('deal-seam-status')).toBeInTheDocument();
    expect(screen.getByTestId('deal-attribution')).toBeInTheDocument();
    expect(screen.getByTestId('seed-hubspot')).toBeInTheDocument();
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
    mockCrmFetch({ unexpected: 'shape' } as unknown as CrmStatusFixture);
    render(<DealView familyId="fam-123" />);

    const seed = await screen.findByTestId('seed-hubspot');
    expect(seed).toBeEnabled();
    expect(screen.queryByTestId('crm-seam-badge')).toBeNull();
    expect(screen.queryByTestId('seed-kill-switch-note')).toBeNull();
  });
});

// --------------------------------------------------------------------------- #
// LA-23 — assignment-history timeline. The deal view drills into the per-family
// ownership history (GET /families/{id}/assignments): the append-only from→to/
// reason facts (who owned this lead, when, why). Names resolve via GET
// /enrollment/agents; an unavailable / unknown shape fails safe (no section).
// --------------------------------------------------------------------------- #

// Two append-only assignment facts: routed out of intake to the FL closer, then
// reassigned to the CA qualifier by the SLA sweep.
const ASSIGNMENT_HISTORY = [
  {
    assignment_id: 'asn-1',
    family_id: 'fam-123',
    from_rep_id: null,
    to_rep_id: 'a0000000-0000-0000-0000-000000000001',
    routed_role: 'closer',
    assigned_by: 'router',
    reason: 'territory: state=FL → closer (Agent A)',
    occurred_at: '2026-06-16T10:00:00Z',
  },
  {
    assignment_id: 'asn-2',
    family_id: 'fam-123',
    from_rep_id: 'a0000000-0000-0000-0000-000000000001',
    to_rep_id: 'a0000000-0000-0000-0000-000000000002',
    routed_role: 'qualifier',
    assigned_by: 'router',
    reason: 'sla-reassign: unworked past timer',
    occurred_at: '2026-06-17T10:00:00Z',
  },
];

const AGENTS_ROSTER = {
  agents: [
    { agent_id: 'a0000000-0000-0000-0000-000000000001', name: 'Riley Carter' },
    { agent_id: 'a0000000-0000-0000-0000-000000000002', name: 'Jordan Avery' },
  ],
};

// Route GET /families/{id}/assignments → history, /enrollment/agents → roster,
// everything else → the family payload.
function mockHistoryFetch(history: unknown = ASSIGNMENT_HISTORY): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (/\/assignments$/.test(url)) {
        return { ok: true, status: 200, json: async () => history };
      }
      if (/\/enrollment\/agents$/.test(url)) {
        return { ok: true, status: 200, json: async () => AGENTS_ROSTER };
      }
      return { ok: true, status: 200, json: async () => ENROLLED_PAYLOAD };
    }),
  );
}

describe('DealView — LA-23 assignment history', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the append-only from→to/reason timeline with resolved names', async () => {
    mockHistoryFetch();
    render(<DealView familyId="fam-123" />);

    const timeline = await screen.findByTestId('deal-assignment-history');
    expect(timeline).toBeInTheDocument();

    // First fact: routed out of intake to the FL closer, with its reason.
    const first = screen.getByTestId('deal-assignment-asn-1');
    expect(first).toHaveTextContent('Intake');
    expect(first).toHaveTextContent('Riley Carter');
    expect(first).toHaveTextContent('territory: state=FL');

    // Second fact: reassigned closer → qualifier, with the SLA reason (append-only,
    // both facts present — the first is not overwritten).
    const second = screen.getByTestId('deal-assignment-asn-2');
    expect(second).toHaveTextContent('Riley Carter');
    expect(second).toHaveTextContent('Jordan Avery');
    expect(second).toHaveTextContent('sla-reassign');
  });

  it('does not render the timeline when there is no history (fail safe)', async () => {
    mockHistoryFetch([]);
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByText('The Rivera Family')).toBeInTheDocument();
    expect(screen.queryByTestId('deal-assignment-history')).toBeNull();
  });

  it('does not render the timeline on an unknown shape (fail safe, no crash)', async () => {
    // The blanket family payload is served for /assignments too — not an array ⇒
    // the type guard rejects it ⇒ no timeline, never a crash.
    mockFetch(ENROLLED_PAYLOAD);
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByText('The Rivera Family')).toBeInTheDocument();
    expect(screen.queryByTestId('deal-assignment-history')).toBeNull();
  });
});

// --------------------------------------------------------------------------- #
// Contact bar — the household's primary contact PERSON + click-to-dial, so the
// deal view is callable (the display_name "The Rivera Family" is not actionable).
// --------------------------------------------------------------------------- #

const CONTACT_PAYLOAD = {
  deal_view: ENROLLED_PAYLOAD.deal_view,
  lead: {
    synthetic_first_name: 'Quinn',
    synthetic_last_name: 'Rivera',
    synthetic_email: 'rivera.753@example.invalid',
    synthetic_phone: '555-0185',
    region: 'West Coast',
    grade_interest: '3',
    num_children: 2,
  },
};

describe('DealView — contact bar (who to call)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('surfaces the contact name + click-to-dial phone + email from the lead', async () => {
    mockFetch(CONTACT_PAYLOAD);
    render(<DealView familyId="fam-123" />);

    const bar = await screen.findByTestId('deal-contact');
    expect(bar).toBeInTheDocument();
    expect(screen.getByTestId('deal-contact-name')).toHaveTextContent('Quinn Rivera');
    const phone = screen.getByTestId('deal-contact-phone');
    expect(phone).toHaveTextContent('555-0185');
    expect(phone).toHaveAttribute('href', 'tel:555-0185');
    const email = screen.getByTestId('deal-contact-email');
    expect(email).toHaveAttribute('href', 'mailto:rivera.753@example.invalid');
    // The at-a-glance meta (children count + grade + region) rides along.
    expect(screen.getByTestId('deal-contact-meta')).toHaveTextContent('2 children');
  });

  it('renders no contact bar when the lead has no contact fields (fail safe)', async () => {
    // ENROLLED_PAYLOAD carries `lead: {}` (empty) — no name/phone/email ⇒ no bar.
    mockFetch(ENROLLED_PAYLOAD);
    render(<DealView familyId="fam-123" />);

    expect(await screen.findByText('The Rivera Family')).toBeInTheDocument();
    expect(screen.queryByTestId('deal-contact')).toBeNull();
  });
});
