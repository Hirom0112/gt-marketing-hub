import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LandingDashboard from '../LandingDashboard';

// Acceptance test (CLAUDE §4.2 — UI is acceptance-test-driven, not red→green
// pixel ceremony). The landing dashboard is read-only: it renders the four
// per-stage counts + a seam-status summary from a mocked GET /pipeline, using
// the native fetch API (no new runtime dependency, ≤12-dep budget).

// A deterministic /pipeline payload shaped like the FastAPI PipelineResponse,
// plus the seam summary the dashboard surfaces (FR-2.1).
const PIPELINE_PAYLOAD = {
  counts: { interest: 83, apply: 65, enroll: 31, tuition: 21 },
  total: 200,
  seam: { synced: 116, unsynced: 67, conflict: 17 },
};

describe('LandingDashboard', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => PIPELINE_PAYLOAD,
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the four per-stage counts from GET /pipeline', async () => {
    render(<LandingDashboard />);

    // Each stage renders its count after the fetch resolves.
    for (const [stage, count] of Object.entries(PIPELINE_PAYLOAD.counts)) {
      const card = await screen.findByTestId(`pipeline-stage-${stage}`);
      expect(within(card).getByTestId('stage-count')).toHaveTextContent(
        String(count),
      );
    }

    // The total is surfaced too.
    expect(await screen.findByTestId('pipeline-total')).toHaveTextContent(
      '200',
    );
  });

  it('renders a seam-status summary', async () => {
    render(<LandingDashboard />);

    const seam = await screen.findByTestId('seam-summary');
    expect(within(seam).getByTestId('seam-synced')).toHaveTextContent('116');
    expect(within(seam).getByTestId('seam-unsynced')).toHaveTextContent('67');
    expect(within(seam).getByTestId('seam-conflict')).toHaveTextContent('17');
  });

  it('calls the configured /pipeline endpoint, read-only (GET)', async () => {
    render(<LandingDashboard />);

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/pipeline$/);
    // Read-only: no method override means GET; never a mutating verb.
    expect(init?.method ?? 'GET').toBe('GET');
  });
});
