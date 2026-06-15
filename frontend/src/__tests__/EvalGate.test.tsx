import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EvalGate from '../EvalGate';

// Acceptance test (CLAUDE §4.2). The eval gate (FR-4.5 / INV-3) consolidates the
// eval-suite scoreboard and enforces the gate VISUALLY and fail-closed: when the
// gating eval (`message_safety_grounding`) is RED — `disabled` flags it true —
// the representative gated AI action is DISABLED and a red notice explains why;
// when green, the action is enabled. A "Run eval suite" control POSTs /evals/run
// and re-renders with the fresh scoreboard. Native fetch only (≤2 runtime deps).
// fireEvent only (no user-event dep).

// All-green scoreboard: the gating eval passes ⇒ action enabled.
const EVALS_GREEN = {
  rows: [
    {
      eval_name: 'message_safety_grounding',
      score: 0.95,
      threshold: 0.9,
      passed: true,
    },
    {
      eval_name: 'nudge_classifier',
      score: 0.88,
      threshold: 0.85,
      passed: true,
    },
  ],
  overall_green: true,
  disabled: { message_safety_grounding: false, nudge_classifier: false },
};

// Re-run scoreboard after POST /evals/run: still green, fresh scores.
const EVALS_RERUN = {
  rows: [
    {
      eval_name: 'message_safety_grounding',
      score: 0.97,
      threshold: 0.9,
      passed: true,
    },
  ],
  overall_green: true,
  disabled: { message_safety_grounding: false },
};

// Red scoreboard: the gating eval fails ⇒ action disabled, blocked notice shown.
const EVALS_RED = {
  rows: [
    {
      eval_name: 'message_safety_grounding',
      score: 0.42,
      threshold: 0.9,
      passed: false,
    },
    {
      eval_name: 'nudge_classifier',
      score: 0.88,
      threshold: 0.85,
      passed: true,
    },
  ],
  overall_green: false,
  disabled: { message_safety_grounding: true, nudge_classifier: false },
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

// Serves the initial GET /evals and a later POST /evals/run distinct payloads.
function mockFetchRouted(routes: { get?: unknown; run?: unknown }): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      const payload =
        init?.method === 'POST' ? (routes.run ?? {}) : (routes.get ?? {});
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

describe('EvalGate', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('evalRedDisablesActionButton: red gating eval disables the action and shows the blocked notice (INV-3 fail-closed)', async () => {
    mockFetch(EVALS_RED);
    render(<EvalGate />);

    // The red-eval notice explains the block.
    expect(await screen.findByTestId('eval-blocked')).toBeInTheDocument();

    // Fail-closed: the representative gated AI action is disabled.
    expect(screen.getByTestId('eval-gate-action')).toBeDisabled();

    // The failing row renders its name, score, threshold and a fail status.
    const board = screen.getByTestId('eval-gate');
    expect(board).toHaveTextContent('message_safety_grounding');
  });

  it('enables the action when all evals are green', async () => {
    mockFetch(EVALS_GREEN);
    render(<EvalGate />);

    const action = await screen.findByTestId('eval-gate-action');
    expect(action).toBeEnabled();
    // No blocked notice when green.
    expect(screen.queryByTestId('eval-blocked')).not.toBeInTheDocument();
  });

  it('runs the eval suite via POST /evals/run and re-renders the fresh result', async () => {
    mockFetchRouted({ get: EVALS_GREEN, run: EVALS_RERUN });
    render(<EvalGate />);

    const runButton = await screen.findByTestId('eval-run');
    fireEvent.click(runButton);

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const runCall = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]).includes('/evals/run') &&
          (c[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(runCall).toBeTruthy();
    });

    // Fresh score surfaces after the re-run.
    await waitFor(() => {
      expect(screen.getByTestId('eval-gate')).toHaveTextContent('0.97');
    });
  });
});
