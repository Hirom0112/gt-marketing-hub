import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MergeQueue from '../MergeQueue';

// Acceptance test (CLAUDE §4.2). The merge-queue human-review UI
// (ENROLLMENT_REFACTOR §5.2, §6 Phase 1) surfaces the REVIEW_QUEUE candidates
// produced by the deterministic `propose_merge` core — ambiguous/partial
// identity matches that must NEVER auto-merge (INV-2 proposal-not-write, INV-4
// fail-closed). A human approves or rejects each candidate, and the decision is
// logged to the proposal/decision spine (POST /proposals/{id}/decision with
// action approve/discard). The UI never merges on its own — it only records the
// human verdict against an already-logged merge PROPOSAL.

// One REVIEW_QUEUE candidate as surfaced by the merge-queue read. It carries the
// already-logged `proposal_id` (the spine key the decision route writes against)
// plus the MergeProposal shape from core/identity.py.
const CANDIDATES = [
  {
    proposal_id: 'prop-1',
    verdict: 'review_queue',
    primary_family_id: 'fam-a',
    duplicate_family_id: 'fam-b',
    matched_on: ['email', 'region'],
    conflicting_keys: ['phone'],
    summary: 'Same email + region, phones differ — review before merging.',
  },
  {
    proposal_id: 'prop-2',
    verdict: 'review_queue',
    primary_family_id: 'fam-c',
    duplicate_family_id: 'fam-d',
    matched_on: ['phone'],
    conflicting_keys: ['email'],
    summary: 'Shared phone, emails differ — ambiguous, review before merging.',
  },
];

function mockApi(decisionResult?: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'POST' && /\/proposals\/[^/]+\/decision$/.test(url)) {
        return {
          ok: true,
          status: 200,
          json: async () =>
            decisionResult ?? { proposal_id: 'prop-1', action: 'approve' },
        };
      }
      if (/\/merge-queue$/.test(url)) {
        return { ok: true, status: 200, json: async () => CANDIDATES };
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    }),
  );
}

describe('MergeQueue', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    mockApi();
  });

  it('Test A: lists each REVIEW_QUEUE candidate with its matched/conflicting keys and summary', async () => {
    render(<MergeQueue />);

    await screen.findByTestId('merge-candidate-prop-1');
    expect(screen.getAllByTestId('merge-candidate')).toHaveLength(2);

    const first = screen.getByTestId('merge-candidate-prop-1');
    expect(first).toHaveTextContent('fam-a');
    expect(first).toHaveTextContent('fam-b');
    // The human reviewer sees WHY it is ambiguous: matched + conflicting keys.
    expect(first).toHaveTextContent(/email/);
    expect(first).toHaveTextContent(/phone/);
    expect(first).toHaveTextContent(
      'Same email + region, phones differ — review before merging.',
    );
  });

  it('Test B: approving a candidate POSTs the decision spine with action=approve', async () => {
    render(<MergeQueue />);
    await screen.findByTestId('merge-candidate-prop-1');

    fireEvent.click(screen.getByTestId('merge-approve-prop-1'));

    await waitFor(() =>
      expect(
        screen.queryByTestId('merge-candidate-prop-1'),
      ).not.toBeInTheDocument(),
    );

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const post = fetchMock.mock.calls.find(
      ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
    ) as [string, RequestInit] | undefined;
    expect(post?.[0]).toMatch(/\/proposals\/prop-1\/decision$/);
    expect(post?.[1].method).toBe('POST');
    const body = JSON.parse(String(post?.[1].body)) as { action: string };
    expect(body.action).toBe('approve');
  });

  it('Test C: rejecting a candidate POSTs the decision spine with action=discard (never auto-merge)', async () => {
    render(<MergeQueue />);
    await screen.findByTestId('merge-candidate-prop-2');

    fireEvent.click(screen.getByTestId('merge-reject-prop-2'));

    await waitFor(() =>
      expect(
        screen.queryByTestId('merge-candidate-prop-2'),
      ).not.toBeInTheDocument(),
    );

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const post = fetchMock.mock.calls.find(
      ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
    ) as [string, RequestInit] | undefined;
    expect(post?.[0]).toMatch(/\/proposals\/prop-2\/decision$/);
    const body = JSON.parse(String(post?.[1].body)) as { action: string };
    expect(body.action).toBe('discard');
  });

  it('Test D: an empty queue renders a clean empty state (no candidates to review)', async () => {
    vi.unstubAllGlobals();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })),
    );
    render(<MergeQueue />);
    expect(await screen.findByTestId('merge-queue-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('merge-candidate')).not.toBeInTheDocument();
  });
});
