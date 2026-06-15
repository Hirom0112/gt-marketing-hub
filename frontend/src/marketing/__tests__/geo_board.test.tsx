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
});
