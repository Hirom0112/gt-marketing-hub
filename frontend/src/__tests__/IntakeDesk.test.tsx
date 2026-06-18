import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
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

  it('intakeDeskSurfacesOnePerRowAssignControl', async () => {
    installFetch(INTAKE_PAYLOAD);
    render(<IntakeDesk />);

    await screen.findByText('The Quinn Family');

    // The per-row Assign control is PRESENT (the routing verb) — exactly one per
    // listed family. M4 wires its handler (asserted below).
    const assigns = screen.getAllByTestId('intake-assign');
    expect(assigns).toHaveLength(3);
    expect(assigns[0]).toHaveTextContent(/assign/i);
  });

  // ── M4: the Assign verb fires the SINGLE gated assignment write ──────────────
  // A two-mode fetch: GET /families?owner=none returns the supplied desk payload;
  // POST /enrollment/families/bulk-assign returns ok. Capture every POST body so
  // the tests can assert the exact { family_ids, agent_id } payload, and prove
  // the desk re-pulls owner=none AFTER a successful assign.
  function installAssignFetch(
    deskBodies: unknown[],
  ): { posts: Array<{ url: string; body: unknown }> } {
    const posts: Array<{ url: string; body: unknown }> = [];
    let getCount = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes('/bulk-assign')) {
          posts.push({
            url,
            body: init?.body ? JSON.parse(String(init.body)) : undefined,
          });
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ assigned: 1 }),
          } as Response);
        }
        if (url.includes('/leads/auto-assign')) {
          // The deterministic router over the whole pool — returns each decision
          // WITH its reason (the WHY the receipt renders).
          posts.push({
            url,
            body: init?.body ? JSON.parse(String(init.body)) : undefined,
          });
          const results = [FAM_IN_WINDOW, FAM_ALARM_OLD, FAM_ALARM_OLDER].map(
            (family_id) => ({
              family_id,
              agent_id: RILEY,
              routed_role: 'closer',
              rule: 'territory',
              reason: 'territory: state=FL → pool [Riley Carter]; weighted RR → Riley Carter',
              owner_match: false,
              held: false,
            }),
          );
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                batch_id: 'auto-assign-test',
                counts: { assigned: 3, held: 0 },
                results,
              }),
          } as Response);
        }
        if (url.includes('/families')) {
          // Each owner=none GET pulls the NEXT desk body (re-pull proof): the
          // initial list, then the post-assign list (assigned families gone).
          const body = deskBodies[Math.min(getCount, deskBodies.length - 1)];
          getCount += 1;
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
    return { posts };
  }

  it('intakeDeskAssignFiresBulkAssignForThatFamilyAndRePulls', async () => {
    // After the assign, the re-pull returns the desk WITHOUT the Sato family
    // (now owned) — proving the assigned family drops out of the unowned pool.
    const afterAssign = INTAKE_PAYLOAD.filter(
      (f) => f.family_id !== FAM_ALARM_OLDER,
    );
    const { posts } = installAssignFetch([INTAKE_PAYLOAD, afterAssign]);
    render(<IntakeDesk />);

    await screen.findByText('The Sato Family');
    expect(screen.getAllByTestId('intake-row')).toHaveLength(3);

    // Click the Sato row's Assign. The Sato row is first (oldest alarmed) — find
    // it by row, then its Assign control, to assert the precise family_id.
    const satoRow = screen
      .getAllByTestId('intake-row')
      .find((r) => within(r).queryByText('The Sato Family') !== null);
    expect(satoRow).toBeDefined();
    fireEvent.click(within(satoRow as HTMLElement).getByTestId('intake-assign'));

    // It fires POST /enrollment/families/bulk-assign with a 1-element family_ids
    // for THAT family, targeting its displayed recommended agent (RILEY).
    await waitFor(() => expect(posts).toHaveLength(1));
    const post = posts[0];
    expect(post).toBeDefined();
    expect(post?.url).toContain('/enrollment/families/bulk-assign');
    expect(post?.body).toEqual({
      family_ids: [FAM_ALARM_OLDER],
      agent_id: RILEY,
    });

    // RE-PULL proof: the desk re-reads owner=none and the assigned Sato family is
    // gone — only two rows remain.
    await waitFor(() => {
      expect(screen.queryByText('The Sato Family')).not.toBeInTheDocument();
    });
    expect(screen.getAllByTestId('intake-row')).toHaveLength(2);
  });

  it('intakeDeskAutoRouteAllFiresDeterministicRouterAndShowsReasons', async () => {
    // After auto-route, the re-pull returns an EMPTY desk (all routed).
    const { posts } = installAssignFetch([INTAKE_PAYLOAD, []]);
    render(<IntakeDesk />);

    await screen.findByText('The Quinn Family');

    fireEvent.click(screen.getByTestId('intake-auto-route'));

    // Auto-route fires the DETERMINISTIC router endpoint over the whole pool —
    // ONE POST /enrollment/leads/auto-assign, NOT per-row bulk-assign.
    await waitFor(() => expect(posts.length).toBeGreaterThanOrEqual(1));
    expect(
      posts.some((p) => p.url.includes('/enrollment/leads/auto-assign')),
    ).toBe(true);

    // The receipt surfaces each routed family + its REASON (the explainability
    // mandate — NFR-6 / "deterministic and explainable").
    const receipt = await screen.findByTestId('intake-route-receipt');
    expect(
      within(receipt).getAllByTestId('intake-route-receipt-row'),
    ).toHaveLength(3);
    expect(receipt.textContent).toContain('territory: state=FL');

    // RE-PULL proof: the desk is now clear (all families routed out of owner=none).
    await screen.findByTestId('intake-empty');
    expect(screen.queryByTestId('intake-row')).not.toBeInTheDocument();
  });
});
