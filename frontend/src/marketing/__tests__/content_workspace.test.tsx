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
  // Two social copy assets (the "Social posts" segment) — tagged theme +
  // platform so the segment filters can narrow them.
  {
    id: 'asset-1',
    title: 'gifted_identity — proven caption',
    asset_type: 'copy',
    channel: 'instagram',
    body: 'Welcome to GT School — here is the next step for your gifted child.',
    source_ref: 'https://gt.school/welcome',
    tags: ['gifted_identity', 'instagram', 'social', 'proven'],
    search_text: 'welcome to gt school',
  },
  {
    id: 'asset-2',
    title: 'cost_tefa_esa — proven caption',
    asset_type: 'copy',
    channel: 'x',
    body: 'Texas families can apply TEFA toward GT School tuition.',
    source_ref: 'https://x.com/gtschool/status/1',
    tags: ['cost_tefa_esa', 'x/twitter', 'social', 'proven'],
    search_text: 'open house this spring tefa',
  },
  // One blog/resource article (the "Blog & resources" segment).
  {
    id: 'asset-3',
    title: 'A Day in the Life of a GT School Student',
    asset_type: 'blog_post',
    channel: 'landing_page',
    body: 'A long-form resource article about a day at GT School.',
    source_ref: 'https://anywhere.gt.school/resources/a-day',
    tags: ['blog', 'owned'],
    search_text: 'a day in the life',
  },
  // One plain website page (the "Website pages" segment).
  {
    id: 'asset-4',
    title: 'Academics',
    asset_type: 'blog_post',
    channel: 'landing_page',
    body: 'The academics page describing the mastery-based program.',
    source_ref: 'https://anywhere.gt.school/academics',
    tags: ['website', 'owned'],
    search_text: 'academics mastery based',
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

    // The Social segment is the default; its two copy assets render.
    expect(
      await screen.findByTestId('library-asset-asset-1'),
    ).toHaveTextContent('gifted_identity — proven caption');
    expect(
      screen.getByTestId('library-asset-asset-2'),
    ).toHaveTextContent('cost_tefa_esa — proven caption');
  });

  it('test_library_segments_render_with_counts', async () => {
    mockFetchRouted({ library: LIBRARY_ASSETS });
    render(<ContentWorkspace />);

    // Three segments, each with its own count (2 social, 1 blog, 1 website).
    const social = await screen.findByTestId('library-segment-social');
    const blog = screen.getByTestId('library-segment-blog');
    const website = screen.getByTestId('library-segment-website');
    expect(social).toHaveTextContent('2');
    expect(blog).toHaveTextContent('1');
    expect(website).toHaveTextContent('1');
  });

  it('test_switching_segment_shows_the_right_asset_kind', async () => {
    mockFetchRouted({ library: LIBRARY_ASSETS });
    render(<ContentWorkspace />);

    // Default segment = social: copy assets visible, blog/website hidden.
    expect(await screen.findByTestId('library-asset-asset-1')).toBeInTheDocument();
    expect(screen.queryByTestId('library-asset-asset-3')).toBeNull();
    expect(screen.queryByTestId('library-asset-asset-4')).toBeNull();

    // Switch to Blog & resources: only the blog article shows.
    fireEvent.click(screen.getByTestId('library-segment-blog'));
    expect(screen.getByTestId('library-asset-asset-3')).toBeInTheDocument();
    expect(screen.queryByTestId('library-asset-asset-1')).toBeNull();
    expect(screen.queryByTestId('library-asset-asset-4')).toBeNull();

    // Switch to Website pages: only the plain page shows.
    fireEvent.click(screen.getByTestId('library-segment-website'));
    expect(screen.getByTestId('library-asset-asset-4')).toBeInTheDocument();
    expect(screen.queryByTestId('library-asset-asset-3')).toBeNull();
  });

  it('test_theme_filter_narrows_the_social_segment', async () => {
    mockFetchRouted({ library: LIBRARY_ASSETS });
    render(<ContentWorkspace />);

    // Both social assets show before filtering.
    expect(await screen.findByTestId('library-asset-asset-1')).toBeInTheDocument();
    expect(screen.getByTestId('library-asset-asset-2')).toBeInTheDocument();

    // Filter to the gifted_identity theme: only asset-1 remains.
    fireEvent.change(screen.getByTestId('library-filter-theme'), {
      target: { value: 'gifted_identity' },
    });
    expect(screen.getByTestId('library-asset-asset-1')).toBeInTheDocument();
    expect(screen.queryByTestId('library-asset-asset-2')).toBeNull();
  });

  it('test_platform_filter_narrows_the_social_segment', async () => {
    mockFetchRouted({ library: LIBRARY_ASSETS });
    render(<ContentWorkspace />);

    expect(await screen.findByTestId('library-asset-asset-1')).toBeInTheDocument();

    // Filter to the x/twitter platform: only asset-2 remains.
    fireEvent.change(screen.getByTestId('library-filter-platform'), {
      target: { value: 'x/twitter' },
    });
    expect(screen.getByTestId('library-asset-asset-2')).toBeInTheDocument();
    expect(screen.queryByTestId('library-asset-asset-1')).toBeNull();
  });

  it('test_library_asset_expands_to_show_body_and_source', async () => {
    mockFetchRouted({ library: LIBRARY_ASSETS });
    render(<ContentWorkspace />);

    // The body + source are hidden until the row is expanded.
    const toggle = await screen.findByTestId('library-asset-toggle-asset-1');
    expect(screen.queryByTestId('library-asset-detail-asset-1')).toBeNull();

    fireEvent.click(toggle);

    // Expanding reveals the full copy and a link out to the original GT source.
    const detail = screen.getByTestId('library-asset-detail-asset-1');
    expect(detail).toHaveTextContent(/next step for your gifted child/);
    const source = screen.getByTestId('library-asset-source-asset-1');
    expect(source).toHaveAttribute('href', 'https://gt.school/welcome');
  });

  it('test_library_search_requeries_the_api', async () => {
    mockFetchRouted({ library: LIBRARY_ASSETS });
    render(<ContentWorkspace />);

    const search = await screen.findByTestId('library-search');
    fireEvent.change(search, { target: { value: 'gifted' } });

    // The library re-queries the FR-3.4 search endpoint with the typed q.
    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const searchCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes('/content/library?q=gifted'),
      );
      expect(searchCall).toBeTruthy();
    });
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
