import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ContentWorkspace from '../ContentWorkspace';

// Acceptance test (CLAUDE §4.2). The marketing operator's content surface
// (FR-3.1/3.4/3.5): enter a prompt → generate a batch of candidates → keep
// (== approve; lands in the library) / discard. The eval gate is enforced
// VISUALLY and fail-closed (INV-3 / INV-4 / FR-4.5): a SURFACED candidate
// renders its copy with keep/discard ENABLED; a BLOCKED candidate renders a
// blocked state with its failing rule and offers NO keep action; a DEGRADED
// batch (kill switch / no LLM, NFR-3) shows a degraded notice over the
// deterministic fallback set. Native fetch only (≤2 runtime deps). fireEvent
// only (no user-event dep).

// A batch: 2 surfaced candidates + 1 blocked (the INV-4 fail-closed surface).
const MIXED_BATCH = {
  batch_id: 'batch-1',
  blocked_count: 1,
  degraded: false,
  candidates: [
    {
      proposal_id: 'cand-a',
      copy: 'Unlock your child’s potential at GT School — enroll today.',
      channel: 'email',
      surfaced: true,
      degraded: false,
      failed_rules: [] as string[],
      validation: { passed: true },
    },
    {
      proposal_id: 'cand-b',
      copy: 'Join a community built for curious learners.',
      channel: 'email',
      surfaced: true,
      degraded: false,
      failed_rules: [] as string[],
      validation: { passed: true },
    },
    {
      proposal_id: 'cand-c',
      copy: 'Our students score 4X higher — guaranteed!',
      channel: 'email',
      surfaced: false,
      degraded: false,
      failed_rules: ['v2_grounding'],
      validation: { passed: false },
    },
  ],
};

// A degraded batch — no LLM / kill switch / cost cap (NFR-3). Candidates are the
// deterministic fallback set; the surface shows a degraded affordance.
const DEGRADED_BATCH = {
  batch_id: 'batch-2',
  blocked_count: 0,
  degraded: true,
  candidates: [
    {
      proposal_id: 'cand-fallback',
      copy: 'GT School — learn more about enrollment.',
      channel: 'email',
      surfaced: true,
      degraded: true,
      failed_rules: [] as string[],
      validation: { passed: true },
    },
  ],
};

const LIBRARY_ASSETS = [
  {
    id: 'asset-1',
    title: 'Welcome email',
    asset_type: 'email',
    search_text: 'welcome to gt school',
  },
  {
    id: 'asset-2',
    title: 'Open house promo',
    asset_type: 'social',
    search_text: 'open house this spring',
  },
];

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

// Routes by URL so a single render can serve the library GET and the generate
// POST (and the decision POST) distinct payloads.
function mockFetchRouted(routes: {
  generate?: unknown;
  decision?: unknown;
  library?: unknown;
}): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      let payload: unknown = {};
      if (url.includes('/ai/content/generate')) payload = routes.generate ?? {};
      else if (url.includes('/decision')) payload = routes.decision ?? {};
      else if (url.includes('/content/library')) payload = routes.library ?? [];
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

describe('ContentWorkspace', () => {
  beforeEach(() => {
    // Default: library starts empty; tests re-stub as needed.
    mockFetch([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('test_generate_renders_surfaced_and_blocks_failed', async () => {
    mockFetchRouted({ generate: MIXED_BATCH, library: [] });
    render(<ContentWorkspace />);

    fireEvent.change(screen.getByTestId('content-prompt'), {
      target: { value: 'a warm enrollment email' },
    });
    fireEvent.click(screen.getByTestId('content-generate'));

    // The 2 surfaced candidates render their copy with keep/discard ENABLED.
    const candA = await screen.findByTestId('candidate-cand-a');
    expect(within(candA).getByText(/Unlock your child/)).toBeInTheDocument();
    expect(within(candA).getByTestId('keep-cand-a')).toBeEnabled();
    expect(within(candA).getByTestId('discard-cand-a')).toBeEnabled();

    const candB = screen.getByTestId('candidate-cand-b');
    expect(within(candB).getByTestId('keep-cand-b')).toBeEnabled();

    // The blocked candidate renders a blocked state with its failing rule and
    // offers NO keep action (INV-4 fail closed).
    const blocked = screen.getByTestId('candidate-blocked-cand-c');
    expect(within(blocked).getByText(/v2_grounding/)).toBeInTheDocument();
    expect(screen.queryByTestId('keep-cand-c')).toBeNull();
  });

  it('test_keep_posts_approve_decision', async () => {
    mockFetchRouted({
      generate: MIXED_BATCH,
      decision: { proposal_id: 'cand-a', action: 'approve' },
      library: [],
    });
    render(<ContentWorkspace />);

    fireEvent.change(screen.getByTestId('content-prompt'), {
      target: { value: 'a warm enrollment email' },
    });
    fireEvent.click(screen.getByTestId('content-generate'));

    const keep = await screen.findByTestId('keep-cand-a');
    fireEvent.click(keep);

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const decisionCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes('/content/cand-a/decision'),
      );
      expect(decisionCall).toBeTruthy();
      const init = decisionCall?.[1] as RequestInit | undefined;
      expect(init?.method).toBe('POST');
      const body = JSON.parse(String(init?.body)) as { action: string };
      expect(body.action).toBe('approve');
    });
  });

  it('test_degraded_batch_shows_fallback_affordance', async () => {
    mockFetchRouted({ generate: DEGRADED_BATCH, library: [] });
    render(<ContentWorkspace />);

    fireEvent.change(screen.getByTestId('content-prompt'), {
      target: { value: 'anything' },
    });
    fireEvent.click(screen.getByTestId('content-generate'));

    // The degraded notice renders over the deterministic fallback set (NFR-3).
    expect(await screen.findByTestId('batch-degraded')).toBeInTheDocument();
    expect(
      screen.getByTestId('candidate-cand-fallback'),
    ).toBeInTheDocument();
  });

  it('test_library_panel_renders_kept_assets', async () => {
    mockFetchRouted({ library: LIBRARY_ASSETS });
    render(<ContentWorkspace />);

    expect(
      await screen.findByTestId('library-asset-asset-1'),
    ).toHaveTextContent('Welcome email');
    expect(
      screen.getByTestId('library-asset-asset-2'),
    ).toHaveTextContent('Open house promo');
  });

  it('requests the batch via POST /ai/content/generate', async () => {
    mockFetchRouted({ generate: MIXED_BATCH, library: [] });
    render(<ContentWorkspace />);

    fireEvent.change(screen.getByTestId('content-prompt'), {
      target: { value: 'a warm enrollment email' },
    });
    fireEvent.click(screen.getByTestId('content-generate'));

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const genCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes('/ai/content/generate'),
      );
      expect(genCall).toBeTruthy();
      const init = genCall?.[1] as RequestInit | undefined;
      expect(init?.method).toBe('POST');
      const body = JSON.parse(String(init?.body)) as { prompt: string };
      expect(body.prompt).toBe('a warm enrollment email');
    });
  });
});
