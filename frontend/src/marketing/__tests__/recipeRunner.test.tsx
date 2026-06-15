import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MarketingBreadth from '../MarketingBreadth';

// Acceptance test (CLAUDE §4.2) for INV-7 / §8.5: the recipe runner lists
// recipes from GET /recipes and RENDERS the Tom Babb attribution. Marketing
// skills are attributed to Tom Babb in the UI — never stripped, never claimed
// as the builder's authorship. This proves authorship attribution surfaces in
// the DOM. Native fetch only (≤2 runtime deps).

const RECIPE_WITH_ATTRIBUTION = {
  id: 'rc-attr',
  name: 'High-intent nurture',
  attribution: 'Tom Babb (open AI-marketing skills)',
  description: 'Nurture high-intent families to enrollment',
  parameters: [{ name: 'cadence_days', description: 'days between touches' }],
};

function mockFetchRouted(recipes: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      let payload: unknown;
      if (url.includes('/recipes')) payload = recipes;
      else if (url.includes('/sentiment'))
        payload = {
          summary: {
            positive: 0,
            neutral: 0,
            negative: 0,
            total: 0,
            source_mode: 'placeholder',
          },
          records: [],
        };
      else if (url.includes('/pipeline'))
        payload = {
          concept: { status: 'ready' },
          image: { status: 'placeholder' },
          video: { status: 'placeholder' },
        };
      // The list endpoints (/creators, /kpi, /content/schedule) default to [].
      else payload = [];
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

describe('RecipeRunner (INV-7 attribution)', () => {
  beforeEach(() => {
    mockFetchRouted([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the Tom Babb attribution when listing a recipe', async () => {
    mockFetchRouted([RECIPE_WITH_ATTRIBUTION]);
    render(<MarketingBreadth />);

    // The recipe surfaces.
    expect(await screen.findByTestId('recipe-rc-attr')).toBeInTheDocument();

    // INV-7: the Tom Babb attribution text is visible in the DOM.
    await waitFor(() => {
      expect(screen.getByText(/Tom Babb/)).toBeInTheDocument();
    });
    expect(
      screen.getByTestId('recipe-attribution-rc-attr'),
    ).toHaveTextContent('Tom Babb (open AI-marketing skills)');
  });
});
