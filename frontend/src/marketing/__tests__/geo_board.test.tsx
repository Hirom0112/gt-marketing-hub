import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import GeoBoard from '../GeoBoard';

// Acceptance test (CLAUDE §4.2). The GEO board (FR-3.7): GT starts from a 0%
// baseline in AI-engine answer coverage; the board surfaces coverage vs that
// 0% baseline, the lift trend (coverage − baseline), and the variance / CI
// (never a point estimate on too few samples — RESEARCH Q5). The GEO eval gate
// is enforced VISUALLY and fail-closed (INV-3): when enabled:false (red GEO
// eval) the generate-to-win / Run-sampling action is DISABLED and a red notice
// explains why; when enabled:true the action is available and clicking it POSTs
// /geo/sample and re-renders with the fresh result. Native fetch only (≤2
// runtime deps). fireEvent only (no user-event dep).

// A healthy GEO read: coverage 30% vs the 0% baseline ⇒ lift +30%; eval green.
// GT citation share ≈ 3% vs competitors ≈ 50% (growth-strategy Bet 3).
const GEO_ENABLED = {
  coverage_mean: 0.3,
  baseline: 0.0,
  lift: 0.3,
  variance: 0.0025,
  sample_count: 12,
  insufficient_samples: false,
  enabled: true,
  prompt_set: ['best online school for gifted kids', 'gt school reviews'],
  engine: 'simulated-llm',
  gt_citation_share: 0.03,
  competitor_citation_share: {
    'joinprisma.com': 0.5,
    'fusionacademy.com': 0.48,
    'davidsononline.org': 0.49,
    'k12.com': 0.51,
    'niche.com': 0.5,
  },
};

// A re-sampled read after a POST /geo/sample run: coverage climbs to 45%.
const GEO_SAMPLED = {
  coverage_mean: 0.45,
  baseline: 0.0,
  lift: 0.45,
  variance: 0.0016,
  sample_count: 24,
  insufficient_samples: false,
  enabled: true,
  prompt_set: ['best online school for gifted kids', 'gt school reviews'],
  engine: 'simulated-llm',
  gt_citation_share: 0.12,
  competitor_citation_share: {
    'joinprisma.com': 0.5,
    'k12.com': 0.51,
  },
};

// A red GEO eval (enabled:false) AND too few samples — fail-closed surface.
const GEO_DISABLED = {
  coverage_mean: 0.1,
  baseline: 0.0,
  lift: 0.1,
  variance: 0.04,
  sample_count: 1,
  insufficient_samples: true,
  enabled: false,
  prompt_set: ['best online school for gifted kids'],
  engine: 'simulated-llm',
  gt_citation_share: 0.01,
  competitor_citation_share: { 'joinprisma.com': 0.5 },
};

// A generate-to-win PASS: the piece published and re-sampling moved coverage.
const GEO_GENERATED_PUBLISHED = {
  ...GEO_SAMPLED,
  published: true,
  blocked: false,
  failed_rules: [] as string[],
};

// A generate-to-win BLOCK: the gate rejected the body; nothing published.
const GEO_GENERATED_BLOCKED = {
  ...GEO_ENABLED,
  published: false,
  blocked: true,
  failed_rules: ['v2_grounding', 'v4_onbrand'],
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

// Serves the initial GET /geo and a later POST /geo/sample distinct payloads.
function mockFetchRouted(routes: { get?: unknown; sample?: unknown }): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      const payload =
        init?.method === 'POST' ? (routes.sample ?? {}) : (routes.get ?? {});
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

// Routes GET /geo, POST /geo/sample, and POST /geo/generate to distinct payloads
// (both actions are POST, so route by URL too — not just method).
function mockFetchByUrl(routes: {
  get?: unknown;
  sample?: unknown;
  generate?: unknown;
}): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      let payload: unknown = routes.get ?? {};
      if (init?.method === 'POST' && String(url).includes('/geo/generate')) {
        payload = routes.generate ?? {};
      } else if (init?.method === 'POST' && String(url).includes('/geo/sample')) {
        payload = routes.sample ?? {};
      }
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

describe('GeoBoard', () => {
  beforeEach(() => {
    mockFetch(GEO_ENABLED);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders coverage vs the 0% baseline and the lift trend', async () => {
    mockFetch(GEO_ENABLED);
    render(<GeoBoard />);

    // The 0% baseline is shown explicitly.
    expect(await screen.findByTestId('geo-baseline')).toHaveTextContent('0%');
    // Coverage mean as a percentage.
    expect(screen.getByTestId('geo-coverage')).toHaveTextContent('30%');
    // The lift trend vs baseline (coverage − baseline), signed.
    expect(screen.getByTestId('geo-lift')).toHaveTextContent('+30%');
  });

  it('surfaces variance / CI', async () => {
    mockFetch(GEO_ENABLED);
    render(<GeoBoard />);
    expect(await screen.findByTestId('geo-variance')).toBeInTheDocument();
  });

  it('renders the GT-vs-competitor citation share bars (~3% vs ~50%)', async () => {
    mockFetch(GEO_ENABLED);
    render(<GeoBoard />);

    // GT's own share bar (the ~3% leadership figure).
    const gtBar = await screen.findByTestId('geo-share-gt');
    expect(gtBar).toHaveTextContent('3%');

    // A competitor's share bar (the ~50% they dominate at).
    const compBars = screen.getAllByTestId('geo-share-competitor');
    expect(compBars.length).toBeGreaterThan(0);
    expect(compBars.some((b) => b.textContent?.includes('50%'))).toBe(true);
    // The leading competitor is cited far more than GT — the gap is shown.
    expect(compBars.some((b) => b.textContent?.includes('k12.com'))).toBe(true);
  });

  it('disables the generate-to-win action when the GEO eval is red (INV-3 fail-closed)', async () => {
    mockFetch(GEO_DISABLED);
    render(<GeoBoard />);

    // The red GEO eval notice explains the block.
    expect(await screen.findByTestId('geo-eval-blocked')).toBeInTheDocument();

    // Fail-closed: the generate-to-win / Run-sampling control is not actionable.
    const control = screen.queryByTestId('geo-run-sampling');
    if (control !== null) {
      expect(control).toBeDisabled();
    }

    // Insufficient-samples notice — never assert a point estimate on too few.
    expect(screen.getByTestId('geo-insufficient')).toBeInTheDocument();
  });

  it('enables the action when the eval is green and re-samples on click', async () => {
    mockFetchRouted({ get: GEO_ENABLED, sample: GEO_SAMPLED });
    render(<GeoBoard />);

    const control = await screen.findByTestId('geo-run-sampling');
    expect(control).toBeEnabled();

    fireEvent.click(control);

    // Fires POST /geo/sample.
    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const sampleCall = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]).includes('/geo/sample') &&
          (c[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(sampleCall).toBeTruthy();
    });

    // The view updates with the fresh result (coverage 45%, lift +45%).
    await waitFor(() => {
      expect(screen.getByTestId('geo-coverage')).toHaveTextContent('45%');
      expect(screen.getByTestId('geo-lift')).toHaveTextContent('+45%');
    });
  });

  it('generate-to-win POSTs the target prompt, publishes, and moves coverage', async () => {
    mockFetchByUrl({ get: GEO_ENABLED, generate: GEO_GENERATED_PUBLISHED });
    render(<GeoBoard />);

    const input = await screen.findByTestId('geo-generate-prompt');
    fireEvent.change(input, {
      target: { value: 'best online gifted school in texas' },
    });
    fireEvent.click(screen.getByTestId('geo-generate'));

    // Fires POST /geo/generate with the target prompt.
    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const call = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]).includes('/geo/generate') &&
          (c[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String((call?.[1] as RequestInit).body)) as {
        target_prompt: string;
      };
      expect(body.target_prompt).toBe('best online gifted school in texas');
    });

    // The published outcome shows and the board re-renders with moved coverage.
    expect(await screen.findByTestId('geo-generate-published')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('geo-coverage')).toHaveTextContent('45%');
    });
  });

  it('generate-to-win surfaces the blocked outcome when the gate rejects (fail-closed)', async () => {
    mockFetchByUrl({ get: GEO_ENABLED, generate: GEO_GENERATED_BLOCKED });
    render(<GeoBoard />);

    const input = await screen.findByTestId('geo-generate-prompt');
    fireEvent.change(input, {
      target: { value: 'kids learn 4x faster guaranteed' },
    });
    fireEvent.click(screen.getByTestId('geo-generate'));

    // The blocked outcome lists the failing rules; nothing published (INV-4).
    const blocked = await screen.findByTestId('geo-generate-blocked');
    expect(blocked).toHaveTextContent(/v2_grounding/);
    expect(screen.queryByTestId('geo-generate-published')).toBeNull();
  });
});
