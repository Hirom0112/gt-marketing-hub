import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PipelineBoard from '../PipelineBoard';

// Acceptance test (CLAUDE §4.2 — UI is acceptance-test-driven). The pipeline
// board (FR-2.1) renders four funnel columns — Interest / Apply / Enroll /
// Tuition — each with its count from a mocked GET /pipeline, using native fetch
// (no new runtime dependency, ≤12-dep budget).

const PIPELINE_PAYLOAD = {
  counts: { interest: 83, apply: 65, enroll: 31, tuition: 21 },
  total: 200,
  seam: { synced: 116, unsynced: 67, conflict: 17 },
};

describe('PipelineBoard', () => {
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

  it('renders the four funnel columns with labels', async () => {
    render(<PipelineBoard />);
    for (const label of ['Interest', 'Apply', 'Enroll', 'Tuition']) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
  });

  it('renders each column count from GET /pipeline', async () => {
    render(<PipelineBoard />);
    for (const [stage, count] of Object.entries(PIPELINE_PAYLOAD.counts)) {
      const column = await screen.findByTestId(`pipeline-column-${stage}`);
      expect(within(column).getByTestId('column-count')).toHaveTextContent(
        String(count),
      );
    }
  });

  it('calls the configured /pipeline endpoint read-only (GET)', async () => {
    render(<PipelineBoard />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/pipeline$/);
    expect(init?.method ?? 'GET').toBe('GET');
  });
});
