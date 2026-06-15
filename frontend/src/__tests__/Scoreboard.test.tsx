import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Scoreboard from '../Scoreboard';

// Acceptance test (CLAUDE §4.2). The leadership scoreboard (FR-6.1) is a
// P2-readable view surfacing BOTH funnels — enrollment (draft proposals,
// approved, edited, rejected, undecided) and marketing/GEO (coverage vs the 0%
// baseline, the signed lift trend) — plus the eval status (overall green/red +
// per-eval pass/fail). A null geo_coverage renders as a dash placeholder, never
// "null". Native fetch only (≤2 runtime deps).

const SCOREBOARD = {
  enrollment: {
    draft_proposals: 12,
    approved: 7,
    edited: 3,
    rejected: 2,
    undecided: 5,
  },
  marketing: {
    geo_coverage: 0.3,
    geo_baseline: 0.0,
    geo_lift: 0.3,
  },
  evals: {
    passed: { message_safety_grounding: true, nudge_classifier: false },
    overall_green: false,
  },
};

const SCOREBOARD_NULL_GEO = {
  enrollment: {
    draft_proposals: 0,
    approved: 0,
    edited: 0,
    rejected: 0,
    undecided: 0,
  },
  marketing: {
    geo_coverage: null,
    geo_baseline: 0.0,
    geo_lift: 0.0,
  },
  evals: {
    passed: {},
    overall_green: true,
  },
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

describe('Scoreboard', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('leadershipScoreboardRendersBothFunnels: enrollment counts AND geo coverage/lift AND eval status', async () => {
    mockFetch(SCOREBOARD);
    render(<Scoreboard />);

    // Enrollment funnel numbers.
    expect(
      await screen.findByTestId('scoreboard-enrollment-draft_proposals'),
    ).toHaveTextContent('12');
    expect(
      screen.getByTestId('scoreboard-enrollment-approved'),
    ).toHaveTextContent('7');
    expect(screen.getByTestId('scoreboard-enrollment-edited')).toHaveTextContent(
      '3',
    );
    expect(
      screen.getByTestId('scoreboard-enrollment-rejected'),
    ).toHaveTextContent('2');
    expect(
      screen.getByTestId('scoreboard-enrollment-undecided'),
    ).toHaveTextContent('5');

    // Marketing / GEO funnel: coverage vs 0% baseline + signed lift trend.
    expect(screen.getByTestId('scoreboard-geo-coverage')).toHaveTextContent(
      '30%',
    );
    expect(screen.getByTestId('scoreboard-geo-baseline')).toHaveTextContent(
      '0%',
    );
    expect(screen.getByTestId('scoreboard-geo-lift')).toHaveTextContent('+30%');

    // Eval status: overall badge + per-eval pass/fail.
    const overall = screen.getByTestId('scoreboard-eval-overall');
    expect(overall).toBeInTheDocument();
    expect(overall).toHaveTextContent(/red/i);
  });

  it('renders a dash for null geo_coverage, never "null"', async () => {
    mockFetch(SCOREBOARD_NULL_GEO);
    render(<Scoreboard />);

    const coverage = await screen.findByTestId('scoreboard-geo-coverage');
    expect(coverage).toHaveTextContent('—');
    expect(coverage).not.toHaveTextContent('null');

    expect(screen.getByTestId('scoreboard-eval-overall')).toHaveTextContent(
      /green/i,
    );
  });
});
