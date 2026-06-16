import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MarketingBreadth from '../MarketingBreadth';

// Acceptance tests (CLAUDE §4.2) for the marketing-breadth workspace (S6).
// Each panel makes its trust property VISIBLE: creators carry an aggregate
// data-mode badge (INV-6); sentiment carries a placeholder source badge
// (OUT-5); the KPI board surfaces signed lever deltas; the pipeline badges its
// image/video stages "placeholder" (OUT-1); the scheduler shows a BLOCKED post
// with NO send affordance (fail-closed INV-3/INV-4) and a simulated_sent
// receipt; the geo-targeting panel states aggregate-only (INV-6). Native fetch
// only (≤2 runtime deps); fireEvent only (no user-event dep).

const CREATORS = [
  {
    id: 'cr-1',
    display_handle: '@gifted_parents_hub',
    channel: 'youtube',
    audience_segment: 'parents-of-gifted',
    fit_score: 0.82,
    authenticity_score: 0.91,
    rationale: 'aligned audience',
    data_mode: 'aggregate',
    is_minor: false,
  },
];

const SENTIMENT = {
  summary: {
    positive: 12,
    neutral: 5,
    negative: 3,
    total: 20,
    source_mode: 'placeholder',
  },
  records: [
    {
      id: 's-1',
      channel: 'reddit',
      topic: 'enrollment',
      sentiment: 'positive',
      score: 0.7,
      excerpt: 'great program',
      source_mode: 'placeholder',
      observed_at: '2026-06-01T00:00:00Z',
    },
  ],
};

const KPI = [
  {
    channel: 'email',
    metric: 'open_rate',
    baseline: 20,
    target: 30,
    lever_delta: 8,
    target_gap: 2,
    target_met: false,
  },
  {
    channel: 'social',
    metric: 'engagement',
    baseline: 5,
    target: 6,
    lever_delta: 3,
    target_gap: -2,
    target_met: true,
  },
];

const PIPELINE = {
  concept: { status: 'ready', caption: 'A warm enrollment concept' },
  image: {
    status: 'placeholder',
    placeholder_uri: 'placeholder://image/1',
  },
  video: {
    status: 'placeholder',
    placeholder_uri: 'placeholder://video/1',
  },
};

const SCHEDULE = [
  {
    id: 'sp-1',
    channel: 'email',
    scheduled_for: '2026-06-20T09:00:00Z',
    dispatch_mode: 'simulated',
    dispatch_status: 'simulated_sent',
    simulated_result: 'delivered',
  },
  {
    id: 'sp-2',
    channel: 'social',
    scheduled_for: '2026-06-21T09:00:00Z',
    dispatch_mode: 'simulated',
    dispatch_status: 'blocked',
  },
];

const RECIPES = [
  {
    id: 'rc-1',
    name: 'Reactivation drip',
    attribution: 'Tom Babb (open AI-marketing skills)',
    description: 'Win back lapsed families',
    parameters: [],
  },
];

const GEO_TARGETING = {
  regions: [
    { region: 'Southwest', lead_count: 9, share: 0.5 },
    { region: 'Southeast', lead_count: 6, share: 0.3333 },
    { region: 'Midwest', lead_count: 3, share: 0.1667 },
  ],
  demand_metros: [
    { metro: 'Austin', state: 'TX' },
    { metro: 'Houston', state: 'TX' },
    { metro: 'Dallas', state: 'TX' },
    { metro: 'Raleigh', state: 'NC' },
  ],
  total: 18,
};

// Routes each GET to its payload so a single render serves every panel.
// `advanceStatus` lets a test force the pipeline-advance POST to 200 (unlocked
// next stage) or 422 (fail-closed blocked reason), exercising INV-3.
function mockFetchRouted(
  routes: Record<string, unknown>,
  advance?: { status: number; body: unknown },
): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      if (url.includes('/content/pipeline/advance') && method === 'POST') {
        const adv = advance ?? { status: 200, body: { next_stage: 'image' } };
        return {
          ok: adv.status < 400,
          status: adv.status,
          json: async () => adv.body,
        };
      }
      let payload: unknown = {};
      if (url.includes('/geo-targeting')) payload = routes.geoTargeting ?? {};
      else if (url.includes('/creators')) payload = routes.creators ?? [];
      else if (url.includes('/sentiment')) payload = routes.sentiment ?? {};
      else if (url.includes('/kpi')) payload = routes.kpi ?? [];
      else if (url.includes('/content/pipeline')) payload = routes.pipeline ?? {};
      else if (url.includes('/content/schedule'))
        payload = routes.schedule ?? [];
      else if (url.includes('/recipes')) payload = routes.recipes ?? [];
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

function renderAll(advance?: { status: number; body: unknown }): void {
  mockFetchRouted(
    {
      creators: CREATORS,
      sentiment: SENTIMENT,
      kpi: KPI,
      pipeline: PIPELINE,
      schedule: SCHEDULE,
      recipes: RECIPES,
      geoTargeting: GEO_TARGETING,
    },
    advance,
  );
  render(<MarketingBreadth />);
}

describe('MarketingBreadth', () => {
  beforeEach(() => {
    mockFetchRouted({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders creators with fit/authenticity and an aggregate badge (INV-6)', async () => {
    renderAll();
    const row = await screen.findByTestId('creator-cr-1');
    expect(within(row).getByTestId('creator-fit-cr-1')).toHaveTextContent(
      '82%',
    );
    expect(
      within(row).getByTestId('creator-authenticity-cr-1'),
    ).toHaveTextContent('91%');
    // The aggregate/synthetic data-mode badge is visible.
    expect(
      within(row).getByTestId('creator-data-mode-cr-1'),
    ).toHaveTextContent('aggregate');
  });

  it('renders the aggregate sentiment summary + a placeholder badge (OUT-5)', async () => {
    renderAll();
    expect(await screen.findByTestId('sentiment-positive')).toHaveTextContent(
      '12',
    );
    expect(screen.getByTestId('sentiment-neutral')).toHaveTextContent('5');
    expect(screen.getByTestId('sentiment-negative')).toHaveTextContent('3');
    // Placeholder source badge — not a live feed.
    expect(screen.getByTestId('sentiment-source-mode')).toHaveTextContent(
      'placeholder',
    );
  });

  it('renders per-channel lever deltas (signed)', async () => {
    renderAll();
    expect(await screen.findByTestId('kpi-lever-email')).toHaveTextContent(
      '+8',
    );
    expect(screen.getByTestId('kpi-lever-social')).toHaveTextContent('+3');
    expect(screen.getByTestId('kpi-met-social')).toHaveTextContent(
      'Target met',
    );
  });

  it('renders image/video pipeline stages as placeholder (OUT-1)', async () => {
    renderAll();
    expect(
      await screen.findByTestId('pipeline-image-badge'),
    ).toHaveTextContent('placeholder');
    expect(screen.getByTestId('pipeline-video-badge')).toHaveTextContent(
      'placeholder',
    );
  });

  it('shows a blocked post with NO send affordance and a simulated_sent receipt (fail-closed)', async () => {
    renderAll();

    // The blocked post renders a blocked status (fail-closed INV-3/INV-4).
    const blockedRow = await screen.findByTestId('schedule-sp-2');
    expect(
      within(blockedRow).getByTestId('schedule-blocked-sp-2'),
    ).toBeInTheDocument();
    // No send affordance exists on the blocked post.
    expect(
      within(blockedRow).queryByTestId('schedule-status-sp-2'),
    ).toBeNull();

    // The simulated_sent post renders its receipt.
    const sentRow = screen.getByTestId('schedule-sp-1');
    expect(
      within(sentRow).getByTestId('schedule-status-sp-1'),
    ).toHaveTextContent(/Simulated sent/);
    // Every dispatch is badged simulated (OUT-2).
    expect(
      within(sentRow).getByTestId('schedule-mode-sp-1'),
    ).toHaveTextContent('simulated');
  });

  it('renders an aggregate-only geo-targeting panel with region rows + metros (INV-6)', async () => {
    renderAll();
    // The aggregate trust badge is kept.
    expect(
      await screen.findByTestId('geo-targeting-aggregate-badge'),
    ).toHaveTextContent(/aggregate-only/i);
    // Real aggregate region rows from the endpoint render (count + share).
    const row = await screen.findByTestId('geo-region-Southwest');
    expect(row).toHaveTextContent('Southwest');
    expect(row).toHaveTextContent('9');
    // The strategy's named demand metros surface.
    const metros = screen.getByTestId('geo-demand-metros');
    expect(metros).toHaveTextContent('Austin');
    expect(metros).toHaveTextContent('Houston');
    expect(metros).toHaveTextContent('Dallas');
    expect(metros).toHaveTextContent('Raleigh');
  });

  it('renders sentiment records (excerpt + topic + sentiment + channel) (OUT-5)', async () => {
    renderAll();
    const record = await screen.findByTestId('sentiment-record-s-1');
    expect(record).toHaveTextContent('great program'); // excerpt
    expect(record).toHaveTextContent('enrollment'); // topic
    expect(record).toHaveTextContent('positive'); // sentiment
    expect(record).toHaveTextContent('reddit'); // channel
  });

  it('advances the pipeline and shows the unlocked next stage on pass (INV-3)', async () => {
    renderAll({ status: 200, body: { next_stage: 'image' } });
    const advance = await screen.findByTestId('pipeline-advance');
    fireEvent.click(advance);
    const result = await screen.findByTestId('pipeline-advance-result');
    expect(result).toHaveTextContent(/image/i);
  });

  it('shows the fail-closed blocked reason on a 422 advance (INV-3, fail-closed)', async () => {
    renderAll({ status: 422, body: { detail: 'stage not selected/validated' } });
    const advance = await screen.findByTestId('pipeline-advance');
    fireEvent.click(advance);
    const blocked = await screen.findByTestId('pipeline-advance-blocked');
    expect(blocked).toHaveTextContent(/blocked/i);
    expect(blocked).toHaveTextContent(/stage not selected/i);
  });

  it('POSTs to schedule a post and refreshes the list', async () => {
    mockFetchRouted({
      creators: CREATORS,
      sentiment: SENTIMENT,
      kpi: KPI,
      pipeline: PIPELINE,
      schedule: SCHEDULE,
      recipes: RECIPES,
      geoTargeting: GEO_TARGETING,
    });
    render(<MarketingBreadth />);

    const add = await screen.findByTestId('scheduler-add');
    fireEvent.click(add);

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const post = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]).includes('/content/schedule') &&
          (c[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(post).toBeTruthy();
      // The body must be a valid ScheduleRequest — channel + approval +
      // validation — not the empty {} that 422s server-side (B2).
      const init = post?.[1] as RequestInit | undefined;
      const body = JSON.parse(String(init?.body)) as {
        channel?: string;
        scheduled_for?: string;
        approval?: { decision?: string };
        validation?: { passed?: boolean };
      };
      expect(body.channel).toBe('email');
      expect(body.scheduled_for).toBeTruthy();
      expect(body.approval?.decision).toBe('approve');
      expect(body.validation?.passed).toBe(true);
    });
  });
});
