import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import HouseholdReconcileBoard from '../HouseholdReconcileBoard';

// Acceptance test (CLAUDE §4.2). The household reconciliation board
// (ENROLLMENT_REFACTOR §6 Phase 1, §8.1) renders ONE row per household, each
// child's DERIVED stage, the household's CRM seam status, and the human
// reconcile controls (PUSH_LOCAL / FLAG_CONFLICT / merge-queue). It joins
// GET /households (the roll-up) with GET /seam (the non-synced cohort), and
// reads GET /crm/status to FAIL CLOSED: if the CRM seam is down/unavailable the
// push/flag controls are DISABLED (never a silent no-op). The deterministic core
// owns the write (INV-2); a flagged conflict fails closed (INV-4).

const HOUSEHOLDS_PAYLOAD = {
  households: [
    {
      user_id: 'user-1',
      family_id: 'fam-a',
      worst_stage: 'interest',
      children: [
        { student_id: 'stu-1', display_label: 'Rivera · Alex · Grade 3', stage: 'enroll' },
        { student_id: 'stu-2', display_label: 'Rivera · Mia · Grade 1', stage: 'interest' },
      ],
    },
    {
      user_id: 'user-2',
      family_id: 'fam-b',
      worst_stage: 'apply',
      children: [
        { student_id: 'stu-3', display_label: 'Chen · Sam · Grade 5', stage: 'apply' },
      ],
    },
    {
      user_id: 'user-3',
      family_id: 'fam-c',
      worst_stage: 'enroll',
      children: [
        { student_id: 'stu-4', display_label: 'Okafor · Zoe · Grade 2', stage: 'enroll' },
      ],
    },
  ],
};

// fam-a is out of sync (conflict), fam-b unsynced; fam-c is absent ⇒ synced.
const SEAM_PAYLOAD = [
  { family_id: 'fam-a', seam_status: 'conflict' },
  { family_id: 'fam-b', seam_status: 'unsynced' },
];

// CRM seam UP: live/simulate, kill switch off ⇒ controls enabled.
const CRM_UP = {
  crm_mode: 'simulate',
  kill_switch: false,
  effective_mode: 'simulate',
  token_configured: false,
  calls_per_run_cap: 25,
};

// CRM seam DOWN: kill switch flipped on ⇒ effective_mode forced to a non-live
// "off" posture; the board fails closed and disables the push/flag controls.
const CRM_DOWN = {
  crm_mode: 'live',
  kill_switch: true,
  effective_mode: 'off',
  token_configured: true,
  calls_per_run_cap: 25,
};

// Route the three GETs to their payloads; the reconcile POST returns `result`.
function mockApi(crm: unknown, result?: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'POST' && /\/reconcile$/.test(url)) {
        return {
          ok: true,
          status: 200,
          json: async () =>
            result ?? { family_id: 'fam-a', applied: true, seam_status: 'synced' },
        };
      }
      if (/\/households$/.test(url)) {
        return { ok: true, status: 200, json: async () => HOUSEHOLDS_PAYLOAD };
      }
      if (/\/seam$/.test(url)) {
        return { ok: true, status: 200, json: async () => SEAM_PAYLOAD };
      }
      if (/\/crm\/status$/.test(url)) {
        return { ok: true, status: 200, json: async () => crm };
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    }),
  );
}

describe('HouseholdReconcileBoard', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    mockApi(CRM_UP);
  });

  it('Test A: renders one row per household with each child stage and the seam status', async () => {
    render(<HouseholdReconcileBoard />);

    // One row per household.
    await screen.findByTestId('household-row-fam-a');
    expect(screen.getAllByTestId('household-row')).toHaveLength(3);

    // Each child's derived stage is shown under its household.
    const rowA = screen.getByTestId('household-row-fam-a');
    expect(rowA).toHaveTextContent('Rivera · Alex · Grade 3');
    expect(rowA).toHaveTextContent('Rivera · Mia · Grade 1');
    expect(screen.getAllByTestId('household-child')).toHaveLength(4);

    // The seam status is joined from GET /seam (conflict / unsynced / synced).
    expect(screen.getByTestId('seam-status-fam-a')).toHaveTextContent(/conflict/i);
    expect(screen.getByTestId('seam-status-fam-b')).toHaveTextContent(/unsynced/i);
    // fam-c is absent from /seam ⇒ rendered as synced.
    expect(screen.getByTestId('seam-status-fam-c')).toHaveTextContent(/synced/i);
  });

  it('Test B: PUSH_LOCAL on an unsynced row POSTs reconcile and the seam status updates to synced', async () => {
    render(<HouseholdReconcileBoard />);

    await screen.findByTestId('household-row-fam-b');
    // The reconcile POST returns fam-b now synced.
    mockApi(CRM_UP, { family_id: 'fam-b', applied: true, seam_status: 'synced' });

    fireEvent.click(screen.getByTestId('push-local-fam-b'));

    await waitFor(() =>
      expect(screen.getByTestId('seam-status-fam-b')).toHaveTextContent(/synced/i),
    );

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const post = fetchMock.mock.calls.find(
      ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
    ) as [string, RequestInit] | undefined;
    expect(post?.[0]).toMatch(/\/seam\/fam-b\/reconcile$/);
    expect(post?.[1].method).toBe('POST');
  });

  it('Test C: FLAG_CONFLICT on a conflict row fails closed · POSTs but the row stays conflict (INV-4)', async () => {
    render(<HouseholdReconcileBoard />);

    await screen.findByTestId('household-row-fam-a');
    // A flagged conflict fails closed: the backend returns applied=false, still conflict.
    mockApi(CRM_UP, { family_id: 'fam-a', applied: false, seam_status: 'conflict' });

    fireEvent.click(screen.getByTestId('flag-conflict-fam-a'));

    // The row is NOT silently resolved — it stays conflict (fail-closed).
    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      expect(
        fetchMock.mock.calls.some(
          ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
        ),
      ).toBe(true);
    });
    expect(screen.getByTestId('seam-status-fam-a')).toHaveTextContent(/conflict/i);
  });

  it('Test D: a conflict row offers a merge-queue control (the human-review affordance)', async () => {
    render(<HouseholdReconcileBoard />);
    await screen.findByTestId('household-row-fam-a');
    // The ambiguous-divergence (conflict) row routes to the human-review merge queue.
    expect(screen.getByTestId('merge-queue-fam-a')).toBeInTheDocument();
  });

  it('Test E: FAIL CLOSED · when the CRM seam is down the push/flag controls are DISABLED', async () => {
    vi.unstubAllGlobals();
    mockApi(CRM_DOWN);
    render(<HouseholdReconcileBoard />);

    await screen.findByTestId('household-row-fam-b');

    // The push + flag controls are disabled (not a silent no-op) when the seam is down.
    expect(screen.getByTestId('push-local-fam-b')).toBeDisabled();
    expect(screen.getByTestId('flag-conflict-fam-a')).toBeDisabled();

    // A visible reason is surfaced so the operator knows WHY the action is off.
    expect(screen.getByTestId('crm-down-notice')).toBeInTheDocument();

    // Clicking a disabled control fires NO POST (the gate truly blocks).
    fireEvent.click(screen.getByTestId('push-local-fam-b'));
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(
      fetchMock.mock.calls.some(
        ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
      ),
    ).toBe(false);
  });
});
