import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import CloseTipsPanel from '../enrollment/CloseTipsPanel';

// Acceptance test (CLAUDE §4.2) for the S9 Wave 5 eval-gated "how to close" tips.
// The panel renders grounded close tips via the eval-gated action and is DISABLED
// (standard disabled treatment) when its consolidated `close_tips` eval is RED
// (INV-3 fail-closed). Native fetch only; URL-aware mock for GET /evals +
// POST /ai/enrollment/close-tips.

// A consolidated scoreboard with the close_tips row GREEN (action enabled).
const EVALS_GREEN = {
  rows: [{ eval_name: 'close_tips', score: 1.0, threshold: 0.95, passed: true }],
  overall_green: true,
  disabled: { close_tips: false },
};

// A consolidated scoreboard with the close_tips row RED (action disabled).
const EVALS_RED = {
  rows: [{ eval_name: 'close_tips', score: 0.2, threshold: 0.95, passed: false }],
  overall_green: false,
  disabled: { close_tips: true },
};

// A surfaced (passing) close-tips proposal — grounded tips render.
const SURFACED_TIPS = {
  proposal_id: 'tips-1',
  surfaced: true,
  degraded: false,
  failed_rules: [] as string[],
  proposal: {
    family_id: 'fam-a',
    tips: [
      {
        text: 'Lead with the homeschool funding path; they self-reported homeschooling.',
        source_ref: 'extracted_fields:prior_schooling',
      },
      { text: 'Offer to walk the parents through the enrollment steps.', source_ref: null },
    ],
  },
};

// A blocked close-tips proposal — the per-proposal gate did not surface it.
const BLOCKED_TIPS = {
  proposal_id: 'tips-2',
  surfaced: false,
  degraded: false,
  failed_rules: ['close_tips_grounding'],
  proposal: null,
};

// URL-aware fetch mock: GET /evals returns `evals`, POST /close-tips returns `tips`.
function mockFetch(evals: unknown, tips: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const payload = url.includes('/evals') ? evals : tips;
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

describe('CloseTipsPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('test_surfaces_grounded_tips_when_eval_green', async () => {
    mockFetch(EVALS_GREEN, SURFACED_TIPS);
    render(<CloseTipsPanel familyId="fam-a" />);

    // The action is enabled when close_tips is green.
    const action = await screen.findByTestId('close-tips-action');
    await waitFor(() => expect(action).not.toBeDisabled());

    fireEvent.click(action);

    // The grounded tips render with their extracted_fields citation.
    const list = await screen.findByTestId('close-tips-list');
    expect(within(list).getByText(/homeschool funding path/)).toBeInTheDocument();
    expect(within(list).getByText(/extracted_fields:prior_schooling/)).toBeInTheDocument();
    // No blocked surface on a passing proposal.
    expect(screen.queryByTestId('close-tips-blocked')).not.toBeInTheDocument();
  });

  it('test_red_eval_disables_action_in_ui', async () => {
    mockFetch(EVALS_RED, SURFACED_TIPS);
    render(<CloseTipsPanel familyId="fam-a" />);

    // Fail-closed: a red close_tips eval disables the action + shows the notice.
    expect(await screen.findByTestId('close-tips-eval-blocked')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId('close-tips-action')).toBeDisabled(),
    );

    // The disabled action cannot produce tips.
    fireEvent.click(screen.getByTestId('close-tips-action'));
    expect(screen.queryByTestId('close-tips-list')).not.toBeInTheDocument();
  });

  it('test_blocked_proposal_shows_failed_rules_not_tips', async () => {
    mockFetch(EVALS_GREEN, BLOCKED_TIPS);
    render(<CloseTipsPanel familyId="fam-a" />);

    const action = await screen.findByTestId('close-tips-action');
    await waitFor(() => expect(action).not.toBeDisabled());
    fireEvent.click(action);

    // Blocked, not softened: the failed rule shows, no tips list.
    const blocked = await screen.findByTestId('close-tips-blocked');
    expect(within(blocked).getByText(/close_tips_grounding/)).toBeInTheDocument();
    expect(screen.queryByTestId('close-tips-list')).not.toBeInTheDocument();
  });
});
