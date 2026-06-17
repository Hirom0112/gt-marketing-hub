import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import AgentRoster from '../AgentRoster';

// M3 admin per-agent roster. AgentRoster consumes GET /enrollment/agents — the
// CANONICAL backend shape (app/api/schemas.py AgentsResponse):
//   { agents: AgentRollup[], unowned: AgentRollup }
// where AgentRollup = { agent_id, synthetic_name, tier, queue_size, stall_rate,
// close_rate, load }. NOTE: `unowned` is a FULL rollup object (null identity),
// NOT a bare count — this test pins that contract so the M3-frontend/backend
// shape drift (unowned typed as `number`) can never silently regress (the
// unowned-pool size would otherwise never render against the live endpoint).
const AGENTS_PAYLOAD = {
  agents: [
    {
      agent_id: 'a0000000-0000-4000-8000-000000000001',
      synthetic_name: 'Riley Carter',
      tier: 'closer',
      queue_size: 8,
      stall_rate: 0.25,
      close_rate: 0.5,
      load: 0.2,
    },
    {
      agent_id: 'a0000000-0000-4000-8000-000000000002',
      synthetic_name: 'Jordan Avery',
      tier: 'setter',
      queue_size: 6,
      stall_rate: 0.5,
      close_rate: 0.333,
      load: 0.15,
    },
  ],
  // The intake pool — a full rollup with a null identity (NOT a number).
  unowned: {
    agent_id: null,
    synthetic_name: null,
    tier: null,
    queue_size: 10,
    stall_rate: 0.4,
    close_rate: 0.1,
    load: 0.25,
  },
};

describe('AgentRoster (admin per-agent roll-up)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(AGENTS_PAYLOAD),
        } as Response),
      ),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders one row per registered agent with name + queue size', async () => {
    render(<AgentRoster />);
    const rows = await screen.findAllByTestId('roster-row');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByTestId('roster-name')).toHaveTextContent(
      'Riley Carter',
    );
    expect(within(rows[0]!).getByTestId('roster-queue')).toHaveTextContent('8');
  });

  it('renders the unowned bucket size from the rollup object (not a bare count)', async () => {
    render(<AgentRoster />);
    // The contract regression guard: the backend sends unowned as a full
    // AgentRollup; the roster must read unowned.queue_size (=10), not treat
    // unowned as a number (which would render nothing).
    const unowned = await screen.findByTestId('roster-unowned');
    expect(unowned).toHaveTextContent('10');
  });
});
