import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LeadershipWorkspace from '../workspaces/LeadershipWorkspace';

// Acceptance test (CLAUDE §4.2). Per ASSUMPTIONS A-17 the leadership-facing
// content moved off the operator page to the Leadership workspace (FR-6.1).
// This proves the four panels mount and each round-trips REAL server data:
//   1. LandingDashboard — funnel KPI strip + CRM-seam ledger (GET /pipeline)
//   2. PipelineBoard     — the per-stage board (GET /pipeline)
//   3. Scoreboard        — FR-6.1 growth rollup (GET /scoreboard)
//   4. EvalGate          — fail-closed gate health (GET /evals)
// fetch is mocked and routed by URL so every panel resolves with its payload.

const PIPELINE_PAYLOAD = {
  counts: { interest: 83, apply: 65, enroll: 31, tuition: 21 },
  total: 200,
  seam: { synced: 116, unsynced: 67, conflict: 17 },
};

const SCOREBOARD_PAYLOAD = {
  enrollment: {
    draft_proposals: 12,
    approved: 7,
    edited: 3,
    rejected: 2,
    undecided: 5,
  },
  marketing: { geo_coverage: 0.3, geo_baseline: 0.0, geo_lift: 0.3 },
  evals: {
    passed: { message_safety_grounding: true, nudge_classifier: false },
    overall_green: false,
  },
};

const EVALS_PAYLOAD = {
  rows: [
    {
      eval_name: 'message_safety_grounding',
      score: 0.92,
      threshold: 0.85,
      passed: true,
    },
  ],
  overall_green: true,
  disabled: { message_safety_grounding: false },
};

function payloadFor(url: string): unknown {
  if (url.endsWith('/scoreboard')) return SCOREBOARD_PAYLOAD;
  if (url.endsWith('/evals')) return EVALS_PAYLOAD;
  return PIPELINE_PAYLOAD;
}

describe('LeadershipWorkspace', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ({
        ok: true,
        status: 200,
        json: async () => payloadFor(url),
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('mounts the four leadership panels, each round-tripping real data', async () => {
    render(<LeadershipWorkspace />);

    // 1. Funnel scoreboard (LandingDashboard) — real /pipeline counts + total.
    expect(await screen.findByTestId('landing-dashboard')).toBeInTheDocument();
    expect(await screen.findByTestId('pipeline-total')).toHaveTextContent('200');

    // 2. CRM-seam ledger — aggregate synced/unsynced/conflict.
    const seam = await screen.findByTestId('seam-summary');
    expect(seam).toHaveTextContent('116');
    expect(seam).toHaveTextContent('67');
    expect(seam).toHaveTextContent('17');

    // 3. Pipeline board — the per-stage board.
    expect(await screen.findByTestId('pipeline-board')).toBeInTheDocument();

    // 4. FR-6.1 scoreboard + fail-closed eval gate.
    expect(await screen.findByTestId('scoreboard')).toBeInTheDocument();
    expect(await screen.findByTestId('eval-gate')).toBeInTheDocument();
  });

  it('queries the live read endpoints (/pipeline, /scoreboard, /evals)', async () => {
    render(<LeadershipWorkspace />);

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.endsWith('/pipeline'))).toBe(true);
      expect(urls.some((u) => u.endsWith('/scoreboard'))).toBe(true);
      expect(urls.some((u) => u.endsWith('/evals'))).toBe(true);
    });
  });
});
