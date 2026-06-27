import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DecisionQueueWorkspace from '../DecisionQueueWorkspace';
import App from '../../App';

// Acceptance test (CLAUDE §4.2) for the consolidated Decision Queue workspace (B2).
// The queue reads the leader-gated GET /decisions, lists OPEN decisions in the reused
// review-card layout, and lets a leader approve / reject / need-info each one
// (POST /decisions/{id}/action → refresh). The fetch layer is stubbed at the native
// `fetch` level (apiFetch wraps it), mirroring TriageTab/DataConfidenceBanner.

const D1 = '11111111-1111-4111-8111-111111111111';
const D2 = '22222222-2222-4222-8222-222222222222';

const OPEN_DECISIONS = [
  {
    id: D1,
    source: 'nurture',
    payload: { family: 'The Alvarez Family', reason: 'high-value stall' },
    state: 'open',
  },
  {
    id: D2,
    source: 'field',
    payload: { family: 'The Bauer Family', reason: 'budget exception' },
    state: 'open',
  },
];

// A url+method-aware fetch stub. GET /decisions returns the queue; POST .../action
// returns the decided row. `decisionsPayload`/`status` let a case override the GET.
function stubApi(opts?: {
  decisionsPayload?: unknown;
  decisionsStatus?: number;
}): ReturnType<typeof vi.fn> {
  const decisionsPayload = opts?.decisionsPayload ?? OPEN_DECISIONS;
  const decisionsStatus = opts?.decisionsStatus ?? 200;
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (/\/decisions\/[^/]+\/action$/.test(url) && method === 'POST') {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ id: D1, source: 'nurture', payload: {}, state: 'decided' }),
      } as Response);
    }
    if (/\/decisions$/.test(url)) {
      return Promise.resolve({
        ok: decisionsStatus >= 200 && decisionsStatus < 300,
        status: decisionsStatus,
        json: () => Promise.resolve(decisionsPayload),
      } as Response);
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) } as Response);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

describe('DecisionQueueWorkspace (B2)', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the OPEN decisions from GET /decisions in review cards', async () => {
    stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const cards = screen.getAllByTestId('decision-card');
    expect(cards).toHaveLength(2);
    const ids = cards.map((c) => c.getAttribute('data-decision'));
    expect(ids).toContain(D1);
    expect(ids).toContain(D2);
    // The source + a readable payload summary render.
    expect(screen.getByText('nurture')).toBeInTheDocument();
    expect(screen.getByText('The Alvarez Family')).toBeInTheDocument();
  });

  it('clicking Approve POSTs {action:"approve"} to /decisions/{id}/action and refreshes', async () => {
    const mock = stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const card = screen
      .getAllByTestId('decision-card')
      .find((c) => c.getAttribute('data-decision') === D1) as HTMLElement;
    fireEvent.click(card.querySelector('[data-testid="decision-approve"]') as HTMLElement);

    await waitFor(() => {
      const actionCall = mock.mock.calls.find(
        ([u, i]) =>
          /\/decisions\/.+\/action$/.test(String(u)) &&
          (i as RequestInit | undefined)?.method === 'POST',
      );
      expect(actionCall).toBeTruthy();
      const body = JSON.parse(String((actionCall?.[1] as RequestInit).body));
      expect(body.action).toBe('approve');
      // The action URL targets the clicked decision.
      expect(String(actionCall?.[0])).toContain(`/decisions/${D1}/action`);
    });
    // A refresh re-pulls GET /decisions (≥2 GETs: initial + post-action).
    const getCalls = mock.mock.calls.filter(
      ([u, i]) =>
        /\/decisions$/.test(String(u)) &&
        ((i as RequestInit | undefined)?.method ?? 'GET') === 'GET',
    );
    expect(getCalls.length).toBeGreaterThanOrEqual(2);
  });

  it('blocks Need-info with an empty comment client-side (no POST, surfaces required state)', async () => {
    const mock = stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const card = screen
      .getAllByTestId('decision-card')
      .find((c) => c.getAttribute('data-decision') === D1) as HTMLElement;
    // Reveal the comment field, then submit with it empty.
    fireEvent.click(card.querySelector('[data-testid="decision-need-info"]') as HTMLElement);
    fireEvent.click(card.querySelector('[data-testid="need-info-submit"]') as HTMLElement);

    // The required state is surfaced and NO action POST was made.
    expect(card.querySelector('[data-testid="need-info-required"]')).toBeTruthy();
    const actionCall = mock.mock.calls.find(([u]) => /\/action$/.test(String(u)));
    expect(actionCall).toBeUndefined();
  });

  it('submits Need-info once a comment is provided', async () => {
    const mock = stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const card = screen
      .getAllByTestId('decision-card')
      .find((c) => c.getAttribute('data-decision') === D1) as HTMLElement;
    fireEvent.click(card.querySelector('[data-testid="decision-need-info"]') as HTMLElement);
    fireEvent.change(card.querySelector('[data-testid="need-info-comment"]') as HTMLElement, {
      target: { value: 'Need the funding letter' },
    });
    fireEvent.click(card.querySelector('[data-testid="need-info-submit"]') as HTMLElement);

    await waitFor(() => {
      const actionCall = mock.mock.calls.find(([u]) => /\/action$/.test(String(u)));
      expect(actionCall).toBeTruthy();
      const body = JSON.parse(String((actionCall?.[1] as RequestInit).body));
      expect(body.action).toBe('need_info');
      expect(body.comment).toBe('Need the funding letter');
    });
  });

  it('renders the "Leadership only" state on a 403 (no crash)', async () => {
    stubApi({ decisionsStatus: 403, decisionsPayload: { detail: 'forbidden' } });
    render(<DecisionQueueWorkspace />);

    expect(await screen.findByTestId('decision-queue-forbidden')).toBeInTheDocument();
    expect(screen.getByText('Leadership only')).toBeInTheDocument();
    expect(screen.queryByTestId('decision-queue-list')).not.toBeInTheDocument();
  });

  it('fails safe with a quiet error on a non-OK fetch (no crash)', async () => {
    stubApi({ decisionsStatus: 500, decisionsPayload: {} });
    render(<DecisionQueueWorkspace />);

    expect(await screen.findByTestId('decision-queue-error')).toBeInTheDocument();
    expect(screen.queryByTestId('decision-queue-list')).not.toBeInTheDocument();
  });

  it('shows the open count, reflecting the number of open decisions', async () => {
    stubApi();
    render(<DecisionQueueWorkspace />);
    await screen.findByTestId('decision-queue-list');
    expect(screen.getByTestId('decision-open-count')).toHaveTextContent('2 open');
  });
});

// ---------------------------------------------------------------------------
// Open-Data enrichment card + source badge + trigger (TODO_v2 §E1). The headline
// brief: "an Open Data query that changes a decision." A boosting query enqueues
// an `open_data_enrichment` decision whose payload carries the district
// enrichment, the recommendation change, the provenance, and the data_source
// (live OpenData vs seeded). The card renders that change + provenance + a SOURCE
// BADGE; a trigger control runs the query and refreshes the queue on a change.
const DISTRICT_ID = '057905'; // a Texas TEA district id

function enrichmentDecision(dataSource: 'live' | 'seeded') {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    source: 'open_data_enrichment',
    payload: {
      district_id: DISTRICT_ID,
      enrichment: {
        district_id: DISTRICT_ID,
        d_rating: 'D',
        staar_proficiency: 0.34,
        per_pupil_spend: 9120,
        enrollment: 1200,
      },
      recommendation: { base_priority: 50, new_priority: 78, delta: 28 },
      provenance: {
        reason: 'Under-served district · Open Data signals boost outreach priority.',
        signals: ['low_rating', 'staar_below_floor', 'enrollment_at_min'],
      },
      data_source: dataSource,
    },
    state: 'open',
  };
}

// A fetch stub that also serves POST /open-data/enrich. `enrichResponse` lets a
// case control the enrich result (changed vs unchanged).
function stubEnrichApi(opts?: {
  decisionsPayload?: unknown;
  enrichResponse?: unknown;
}): ReturnType<typeof vi.fn> {
  const decisionsPayload = opts?.decisionsPayload ?? OPEN_DECISIONS;
  const enrichResponse = opts?.enrichResponse ?? {
    district_id: DISTRICT_ID,
    enrichment: enrichmentDecision('seeded').payload.enrichment,
    recommendation_changed: true,
    new_priority: 78,
    provenance: enrichmentDecision('seeded').payload.provenance,
    data_source: 'seeded',
  };
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (/\/open-data\/enrich$/.test(url) && method === 'POST') {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(enrichResponse),
      } as Response);
    }
    if (/\/decisions$/.test(url)) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(decisionsPayload),
      } as Response);
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) } as Response);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

describe('DecisionQueueWorkspace · Open-Data enrichment (E1)', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the district rating, the recommendation delta, the provenance signals, and a Seeded source badge', async () => {
    stubEnrichApi({ decisionsPayload: [enrichmentDecision('seeded')] });
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    // District + vitals.
    expect(screen.getByTestId('enrichment-district')).toHaveTextContent(DISTRICT_ID);
    expect(screen.getByTestId('enrichment-rating')).toHaveTextContent('D');
    expect(screen.getByTestId('enrichment-enrollment')).toHaveTextContent('1,200');
    // The recommendation change: base → new and the +delta.
    const rec = screen.getByTestId('enrichment-recommendation');
    expect(rec).toHaveTextContent('50 → 78');
    expect(rec).toHaveTextContent('+28');
    // Provenance signals render as chips.
    const signals = screen.getByTestId('enrichment-signals');
    expect(signals).toHaveTextContent('low_rating');
    expect(signals).toHaveTextContent('staar_below_floor');
    expect(signals).toHaveTextContent('enrollment_at_min');
    // The SOURCE BADGE distinguishes seeded (muted INV-9 tone).
    const badge = screen.getByTestId('enrichment-source-badge');
    expect(badge).toHaveAttribute('data-source', 'seeded');
    expect(badge).toHaveTextContent('Seeded');
  });

  it('renders a "Live OpenData" source badge for a live data_source', async () => {
    stubEnrichApi({ decisionsPayload: [enrichmentDecision('live')] });
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const badge = screen.getByTestId('enrichment-source-badge');
    expect(badge).toHaveAttribute('data-source', 'live');
    expect(badge).toHaveTextContent('Live OpenData');
  });

  it('keeps the generic approve action working on an enrichment card', async () => {
    const mock = stubEnrichApi({ decisionsPayload: [enrichmentDecision('seeded')] });
    // The action route returns a decided row.
    render(<DecisionQueueWorkspace />);
    await screen.findByTestId('decision-queue-list');
    const card = screen.getByTestId('decision-card');
    fireEvent.click(card.querySelector('[data-testid="decision-approve"]') as HTMLElement);
    await waitFor(() => {
      const actionCall = mock.mock.calls.find(([u]) => /\/action$/.test(String(u)));
      expect(actionCall).toBeTruthy();
    });
  });

  it('the "Run Open Data enrichment" control POSTs /open-data/enrich and refreshes the queue on a changed response', async () => {
    const mock = stubEnrichApi({ decisionsPayload: OPEN_DECISIONS });
    render(<DecisionQueueWorkspace />);
    await screen.findByTestId('decision-queue-list');

    fireEvent.change(screen.getByTestId('open-data-district-input'), {
      target: { value: DISTRICT_ID },
    });
    fireEvent.click(screen.getByTestId('open-data-run'));

    await waitFor(() => {
      const post = mock.mock.calls.find(
        ([u, i]) =>
          /\/open-data\/enrich$/.test(String(u)) &&
          (i as RequestInit | undefined)?.method === 'POST',
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit).body));
      expect(body.district_id).toBe(DISTRICT_ID);
    });
    // A changed response surfaces the inline note and re-pulls GET /decisions.
    expect(await screen.findByTestId('open-data-changed')).toBeInTheDocument();
    const getCalls = mock.mock.calls.filter(
      ([u, i]) =>
        /\/decisions$/.test(String(u)) &&
        ((i as RequestInit | undefined)?.method ?? 'GET') === 'GET',
    );
    expect(getCalls.length).toBeGreaterThanOrEqual(2);
  });

  it('shows the quiet no-change note on a recommendation_changed:false response', async () => {
    stubEnrichApi({
      decisionsPayload: OPEN_DECISIONS,
      enrichResponse: {
        district_id: DISTRICT_ID,
        enrichment: enrichmentDecision('live').payload.enrichment,
        recommendation_changed: false,
        new_priority: 50,
        provenance: { changed: false, reason: 'well-rated' },
        data_source: 'live',
      },
    });
    render(<DecisionQueueWorkspace />);
    await screen.findByTestId('decision-queue-list');

    fireEvent.change(screen.getByTestId('open-data-district-input'), {
      target: { value: DISTRICT_ID },
    });
    fireEvent.click(screen.getByTestId('open-data-run'));

    expect(await screen.findByTestId('open-data-no-change')).toHaveTextContent('No change');
    expect(screen.queryByTestId('open-data-changed')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Nav-entry + open-count badge gating, exercised through the App shell (the nav
// lives in App). A seated session is written to localStorage (as the app tests do),
// and the same url-aware fetch stub serves the token + the OPEN queue.
const FAKE_TOKEN = 'header.payload.signature';

function seat(role: 'admin' | 'leader' | 'operator'): void {
  localStorage.setItem(
    'gt_demo_session',
    JSON.stringify({
      role,
      token: FAKE_TOKEN,
      expiresAt: Date.now() + 3_600_000,
      ...(role === 'operator'
        ? {
            agentId: 'a0000000-0000-4000-8000-000000000001',
            agentRank: 1,
            tier: 'closer',
            agentName: 'Riley Carter',
          }
        : {}),
    }),
  );
}

function stubShell(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (/\/auth\/demo-token/.test(url)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ access_token: FAKE_TOKEN, token_type: 'bearer', expires_in: 3600 }),
        } as Response);
      }
      if (/\/decisions$/.test(url)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(OPEN_DECISIONS),
        } as Response);
      }
      const body = /\/enrollment\/calendar/.test(url) ? { month: '2026-06', entries: [] } : [];
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response);
    }),
  );
}

describe('Decision Queue nav entry + badge (B2)', () => {
  beforeEach(() => {
    localStorage.clear();
    stubShell();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the Decisions nav entry for an admin seat', async () => {
    seat('admin');
    render(<App />);
    expect(await screen.findByTestId('sidebar-nav-decisions')).toBeInTheDocument();
  });

  it('shows the Decisions nav entry for a leader seat', async () => {
    seat('leader');
    render(<App />);
    expect(await screen.findByTestId('sidebar-nav-decisions')).toBeInTheDocument();
  });

  it('hides the Decisions nav entry for an operator seat', () => {
    seat('operator');
    render(<App />);
    expect(screen.queryByTestId('sidebar-nav-decisions')).toBeNull();
  });

  it('renders the open-count badge reflecting the number of open decisions', async () => {
    seat('leader');
    render(<App />);
    const nav = await screen.findByTestId('sidebar-nav-decisions');
    await waitFor(() => expect(nav).toHaveTextContent('2'));
  });
});
