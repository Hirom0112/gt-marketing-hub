import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import IntakeDesk from '../enrollment/IntakeDesk';

// M3 acceptance (MULTI_AGENT_COCKPIT.md §4/§5; PLAN.md M3 R2). The
// Intake/Unassigned routing desk is the ADMIN's distinct surface for ROUTING the
// unowned pool — a different verb than working a queue. It:
//   · reads GET /families?owner=none via apiFetch (owner-scoped SERVER-SIDE —
//     the backend returns only assigned_rep_id IS NULL families; no client
//     filtering of an owned family in). The "owner=none filter" guard (R2):
//     this desk MUST NOT list an owned family even if one leaked into the array.
//   · shows a per-row ROUTER PROPOSAL (the recommended agent/tier) — the
//     recommendation surface; the ACTUAL assign POST is M4 (deferred).
//   · sorts the Unowned-Alarm partition (families past the unowned-alarm window)
//     to the TOP, ahead of the in-window pool.
//
// The actual Assign FIRING is M4 — this test asserts the desk LISTS the
// unassigned + shows the recommendation + the Unowned-Alarm partition order, NOT
// that clicking assigns.

// Synthetic unassigned families (UUID-shaped; no PII, INV-1). Two are past the
// unowned-alarm window (alarmed) and one is still inside it. They are returned in
// a deliberately NON-alarm-first order so the test proves the desk re-sorts.
const FAM_IN_WINDOW = 'c0000000-0000-4000-8000-000000000001';
const FAM_ALARM_OLD = 'c0000000-0000-4000-8000-000000000002';
const FAM_ALARM_OLDER = 'c0000000-0000-4000-8000-000000000003';
// An OWNED family that (defensively) should never be listed by the desk.
const FAM_OWNED_LEAK = 'c0000000-0000-4000-8000-000000000009';

const RILEY = 'a0000000-0000-4000-8000-000000000001';

const INTAKE_PAYLOAD = [
  {
    family_id: FAM_IN_WINDOW,
    display_name: 'The Quinn Family',
    assigned_rep_id: null,
    current_stage: 'interest',
    value: 10474,
    intake_date: '2026-06-16T09:00:00Z',
    unowned_alarm: false,
    recommended_agent_id: RILEY,
    recommended_agent_name: 'Riley Carter',
    recommended_tier: 'closer',
  },
  {
    family_id: FAM_ALARM_OLD,
    display_name: 'The Rhodes Family',
    assigned_rep_id: null,
    current_stage: 'apply',
    value: 30000,
    intake_date: '2026-06-10T09:00:00Z',
    unowned_alarm: true,
    recommended_agent_id: RILEY,
    recommended_agent_name: 'Riley Carter',
    recommended_tier: 'closer',
  },
  {
    family_id: FAM_ALARM_OLDER,
    display_name: 'The Sato Family',
    assigned_rep_id: null,
    current_stage: 'interest',
    value: 10474,
    intake_date: '2026-06-05T09:00:00Z',
    unowned_alarm: true,
    recommended_agent_id: RILEY,
    recommended_agent_name: 'Riley Carter',
    recommended_tier: 'closer',
  },
];

function installFetch(body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/families')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(body),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      } as Response);
    }),
  );
}

function urlsCalled(): string[] {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

describe('IntakeDesk', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('intakeDeskReadsOwnerNoneAndShowsRouterProposals', async () => {
    installFetch(INTAKE_PAYLOAD);
    render(<IntakeDesk />);

    // The desk reads the unassigned pool with the owner=none param (M1 owner
    // filter) — the server returns only assigned_rep_id IS NULL families.
    await waitFor(() => {
      expect(urlsCalled().some((u) => /owner=none/.test(u))).toBe(true);
    });

    // It lists every unassigned family.
    await screen.findByText('The Quinn Family');
    expect(screen.getByText('The Rhodes Family')).toBeInTheDocument();
    expect(screen.getByText('The Sato Family')).toBeInTheDocument();

    // Each row carries a router proposal — the recommended agent (the routing
    // recommendation surface; the actual assign POST is M4).
    const proposals = screen.getAllByTestId('intake-router-proposal');
    expect(proposals).toHaveLength(3);
    expect(proposals[0]).toHaveTextContent('Riley Carter');
  });

  it('intakeDeskSortsUnownedAlarmPartitionToTop', async () => {
    installFetch(INTAKE_PAYLOAD);
    render(<IntakeDesk />);

    await screen.findByText('The Quinn Family');

    // The alarmed (past the unowned-alarm window) families sort ABOVE the
    // in-window family, regardless of the server's array order.
    const rows = screen.getAllByTestId('intake-row');
    const names = rows.map((r) => within(r).getByTestId('intake-name').textContent);
    // The two alarmed families come first; the in-window one is last.
    expect(names.indexOf('The Quinn Family')).toBe(names.length - 1);
    expect(names.slice(0, 2)).toEqual(
      expect.arrayContaining(['The Rhodes Family', 'The Sato Family']),
    );

    // The Unowned-Alarm partition is visibly headed so the admin reads it as a
    // distinct, surfaced-first block.
    expect(screen.getByTestId('intake-alarm-partition')).toBeInTheDocument();
  });

  it('intakeDeskListsOnlyOwnerNoneNeverAnOwnedFamily', async () => {
    // R2 guard: even if an OWNED family leaks into the payload, the desk renders
    // only the owner=none pool (assigned_rep_id IS NULL). Prove the owned family
    // is ABSENT.
    const leaked = [
      ...INTAKE_PAYLOAD,
      {
        family_id: FAM_OWNED_LEAK,
        display_name: 'The Owned Family',
        assigned_rep_id: RILEY,
        current_stage: 'enroll',
        value: 10474,
        intake_date: '2026-06-12T09:00:00Z',
        unowned_alarm: false,
        recommended_agent_id: RILEY,
        recommended_agent_name: 'Riley Carter',
        recommended_tier: 'closer',
      },
    ];
    installFetch(leaked);
    render(<IntakeDesk />);

    await screen.findByText('The Quinn Family');
    expect(screen.queryByText('The Owned Family')).not.toBeInTheDocument();
    expect(screen.getAllByTestId('intake-row')).toHaveLength(3);
  });

  it('intakeDeskAssignAffordanceIsDeferredToM4', async () => {
    installFetch(INTAKE_PAYLOAD);
    render(<IntakeDesk />);

    await screen.findByText('The Quinn Family');

    // The per-row Assign control is PRESENT (the routing affordance) but its
    // firing is deferred to M4 — there is exactly one per listed family.
    const assigns = screen.getAllByTestId('intake-assign');
    expect(assigns).toHaveLength(3);
    // The control surfaces the routing verb; M4 wires its handler.
    expect(assigns[0]).toHaveTextContent(/assign/i);
  });
});
