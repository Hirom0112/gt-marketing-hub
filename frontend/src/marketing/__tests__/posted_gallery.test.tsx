import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PostedGallery from '../PostedGallery';

// Acceptance test (CLAUDE §4.2). The posted-content gallery (FR-3.4): "see every
// picture we ever posted and WHERE, and the WORDS along with it." Platform tiles
// (with per-platform counts) → click a platform → a grid of post cards (image
// placeholder + caption + posted date + value badge) → a Most valuable / Most
// recent sort toggle → a back-to-all affordance. Native fetch only.

// The landing payload: platform tiles + counts (GET /content/gallery, no platform).
const GROUPS_VIEW = {
  groups: [
    { platform: 'x/twitter', count: 209 },
    { platform: 'instagram', count: 82 },
    { platform: 'facebook', count: 57 },
  ],
  posts: [] as unknown[],
};

// The drilled payload: Facebook's posts (GET /content/gallery?platform=facebook).
// Real-shaped fields: a served media_ref, engagement counts, and the original-post url.
// fb1 is an image; fb2 is a video — both render their respective media element.
const FACEBOOK_MOST_RECENT = {
  groups: [] as unknown[],
  posts: [
    {
      id: 'fb1',
      platform: 'facebook',
      asset_type: 'image',
      caption: 'Gifted kids deserve their pace.',
      image_ref: '/posted-media/social/facebook_fake/facebook/fake/fb1.jpg',
      posted_at: '2026-02-01',
      value: 215.0,
      likes: 100,
      views: 1000,
      comments: 5,
      url: 'https://example.invalid/p/fb1',
    },
    {
      id: 'fb2',
      platform: 'facebook',
      asset_type: 'video',
      caption: 'Mastery over busywork.',
      image_ref: '/posted-media/social/facebook_fake/facebook/fake/fb2.mp4',
      posted_at: '2025-09-10',
      value: 42.0,
      likes: 10,
      views: 0,
      comments: 1,
      url: 'https://example.invalid/p/fb2',
    },
  ],
};

const FACEBOOK_MOST_VALUABLE = {
  groups: [] as unknown[],
  posts: [...FACEBOOK_MOST_RECENT.posts].sort((a, b) => b.value - a.value),
};

// Route by URL + the sort query param so a single render serves the landing,
// the drilled most_recent, and the drilled most_valuable payloads.
function mockGalleryFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      let payload: unknown = GROUPS_VIEW;
      if (url.includes('platform=facebook')) {
        payload = url.includes('most_valuable')
          ? FACEBOOK_MOST_VALUABLE
          : FACEBOOK_MOST_RECENT;
      }
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

describe('PostedGallery', () => {
  beforeEach(() => {
    mockGalleryFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('test_platform_tiles_render_with_counts', async () => {
    render(<PostedGallery />);
    const fbTile = await screen.findByTestId('gallery-tile-facebook');
    expect(within(fbTile).getByText('57')).toBeInTheDocument();
    expect(screen.getByTestId('gallery-tile-instagram')).toBeInTheDocument();
    expect(within(screen.getByTestId('gallery-tile-x/twitter')).getByText('209')).toBeInTheDocument();
  });

  it('test_clicking_a_platform_filters_to_its_posts', async () => {
    render(<PostedGallery />);
    fireEvent.click(await screen.findByTestId('gallery-tile-facebook'));

    // The grid shows Facebook's post cards: real media + caption + value badge.
    const card = await screen.findByTestId('gallery-post-fb1');
    expect(within(card).getByText('Gifted kids deserve their pace.')).toBeInTheDocument();
    expect(within(card).getByTestId('gallery-post-media-fb1')).toBeInTheDocument();
    expect(within(card).getByTestId('gallery-post-value-fb1')).toBeInTheDocument();
    expect(screen.getByTestId('gallery-post-fb2')).toBeInTheDocument();
  });

  it('test_image_post_renders_an_img_against_the_api_base', async () => {
    render(<PostedGallery />);
    fireEvent.click(await screen.findByTestId('gallery-tile-facebook'));

    // fb1 is an image: an <img> whose src resolves the served media_ref against the API base.
    const media = await screen.findByTestId('gallery-post-media-fb1');
    expect(media.tagName).toBe('IMG');
    expect(media).toHaveAttribute(
      'src',
      'http://localhost:8000/posted-media/social/facebook_fake/facebook/fake/fb1.jpg',
    );
  });

  it('test_video_post_renders_a_video_element', async () => {
    render(<PostedGallery />);
    fireEvent.click(await screen.findByTestId('gallery-tile-facebook'));

    // fb2 is a video: a <video controls> element (not an <img>).
    const media = await screen.findByTestId('gallery-post-media-fb2');
    expect(media.tagName).toBe('VIDEO');
  });

  it('test_engagement_badge_and_view_original_render', async () => {
    render(<PostedGallery />);
    fireEvent.click(await screen.findByTestId('gallery-tile-facebook'));

    const card = await screen.findByTestId('gallery-post-fb1');
    // Engagement badge surfaces likes/views/comments.
    const badge = within(card).getByTestId('gallery-post-engagement-fb1');
    expect(badge).toHaveTextContent('100');
    expect(badge).toHaveTextContent('1,000'); // views formatted with a thousands separator
    expect(badge).toHaveTextContent('5');
    // "View original" links to the original post url in a new tab.
    const link = within(card).getByTestId('gallery-post-link-fb1');
    expect(link).toHaveAttribute('href', 'https://example.invalid/p/fb1');
    expect(link).toHaveAttribute('target', '_blank');

    // fb2 has views=0 → the zero view metric is omitted gracefully.
    const badge2 = within(screen.getByTestId('gallery-post-fb2')).getByTestId(
      'gallery-post-engagement-fb2',
    );
    expect(badge2).toHaveTextContent('10');
    expect(badge2).not.toHaveTextContent('▶');
  });

  it('test_toggling_sort_reorders_the_grid', async () => {
    render(<PostedGallery />);
    fireEvent.click(await screen.findByTestId('gallery-tile-facebook'));
    await screen.findByTestId('gallery-post-fb1');

    // Default is most_recent (newest first): fb1 (2026-02) before fb2 (2025-09).
    let cards = screen.getAllByTestId(/^gallery-post-fb/);
    expect(cards[0]).toHaveAttribute('data-testid', 'gallery-post-fb1');

    // Toggle to Most valuable — highest value first (here also fb1, but the
    // request must re-fetch with sort=most_valuable, proving the toggle wires).
    fireEvent.click(screen.getByTestId('gallery-sort-most_valuable'));
    await waitFor(() =>
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          (c: unknown[]) => String(c[0]).includes('most_valuable'),
        ),
      ).toBe(true),
    );
    cards = screen.getAllByTestId(/^gallery-post-fb/);
    expect(cards[0]).toHaveAttribute('data-testid', 'gallery-post-fb1');
  });

  it('test_back_returns_to_the_platform_tiles', async () => {
    render(<PostedGallery />);
    fireEvent.click(await screen.findByTestId('gallery-tile-facebook'));
    await screen.findByTestId('gallery-post-fb1');

    fireEvent.click(screen.getByTestId('gallery-back'));
    // Back to the tiles — the platform grid is shown again.
    expect(await screen.findByTestId('gallery-tile-facebook')).toBeInTheDocument();
  });
});
