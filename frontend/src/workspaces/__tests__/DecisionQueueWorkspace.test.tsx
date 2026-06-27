import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DecisionQueueWorkspace from '../DecisionQueueWorkspace';

// Acceptance test (CLAUDE §4.2) for the consolidated Decision Queue workspace (B2).
// The queue reads the leader-gated GET /decisions, lists OPEN decisions in the reused
// review-card layout, and lets a leader approve / reject / need-info each one
// (POST /decisions/{id}/action → refresh). The fetch layer is stubbed at the native
// `fetch` level (apiFetch wraps it), mirroring TriageTab/DataConfidenceBanner.

const D1 = '11111111-1111-4111-8111-111111111111';
const D2 = '22222222-2222-4222-8222-222222222222';

const OPEN_DECISIONS = [
  {
    id: D1,
    source: 'nurture',
    payload: { family: 'The Alvarez Family', reason: 'high-value stall' },
    state: 'open',
  },
  {
    id: D2,
    source: 'field',
    payload: { family: 'The Bauer Family', reason: 'budget exception' },
    state: 'open',
  },
];

// A url+method-aware fetch stub. GET /decisions returns the queue; POST .../action
// returns the decided row. `decisionsPayload`/`status` let a case override the GET.
function stubApi(opts?: {
  decisionsPayload?: unknown;
  decisionsStatus?: number;
}): ReturnType<typeof vi.fn> {
  const decisionsPayload = opts?.decisionsPayload ?? OPEN_DECISIONS;
  const decisionsStatus = opts?.decisionsStatus ?? 200;
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (/\/decisions\/[^/]+\/action$/.test(url) && method === 'POST') {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ id: D1, source: 'nurture', payload: {}, state: 'decided' }),
      } as Response);
    }
    if (/\/decisions$/.test(url)) {
      return Promise.resolve({
        ok: decisionsStatus >= 200 && decisionsStatus < 300,
        status: decisionsStatus,
        json: () => Promise.resolve(decisionsPayload),
      } as Response);
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) } as Response);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

describe('DecisionQueueWorkspace (B2)', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the OPEN decisions from GET /decisions in review cards', async () => {
    stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const cards = screen.getAllByTestId('decision-card');
    expect(cards).toHaveLength(2);
    const ids = cards.map((c) => c.getAttribute('data-decision'));
    expect(ids).toContain(D1);
    expect(ids).toContain(D2);
    // The source + a readable payload summary render.
    expect(screen.getByText('nurture')).toBeInTheDocument();
    expect(screen.getByText('The Alvarez Family')).toBeInTheDocument();
  });

  it('clicking Approve POSTs {action:"approve"} to /decisions/{id}/action and refreshes', async () => {
    const mock = stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const card = screen
      .getAllByTestId('decision-card')
      .find((c) => c.getAttribute('data-decision') === D1) as HTMLElement;
    fireEvent.click(card.querySelector('[data-testid="decision-approve"]') as HTMLElement);

    await waitFor(() => {
      const actionCall = mock.mock.calls.find(
        ([u, i]) =>
          /\/decisions\/.+\/action$/.test(String(u)) &&
          (i as RequestInit | undefined)?.method === 'POST',
      );
      expect(actionCall).toBeTruthy();
      const body = JSON.parse(String((actionCall?.[1] as RequestInit).body));
      expect(body.action).toBe('approve');
      // The action URL targets the clicked decision.
      expect(String(actionCall?.[0])).toContain(`/decisions/${D1}/action`);
    });
    // A refresh re-pulls GET /decisions (≥2 GETs: initial + post-action).
    const getCalls = mock.mock.calls.filter(
      ([u, i]) =>
        /\/decisions$/.test(String(u)) &&
        ((i as RequestInit | undefined)?.method ?? 'GET') === 'GET',
    );
    expect(getCalls.length).toBeGreaterThanOrEqual(2);
  });

  it('blocks Need-info with an empty comment client-side (no POST, surfaces required state)', async () => {
    const mock = stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const card = screen
      .getAllByTestId('decision-card')
      .find((c) => c.getAttribute('data-decision') === D1) as HTMLElement;
    // Reveal the comment field, then submit with it empty.
    fireEvent.click(card.querySelector('[data-testid="decision-need-info"]') as HTMLElement);
    fireEvent.click(card.querySelector('[data-testid="need-info-submit"]') as HTMLElement);

    // The required state is surfaced and NO action POST was made.
    expect(card.querySelector('[data-testid="need-info-required"]')).toBeTruthy();
    const actionCall = mock.mock.calls.find(([u]) => /\/action$/.test(String(u)));
    expect(actionCall).toBeUndefined();
  });

  it('submits Need-info once a comment is provided', async () => {
    const mock = stubApi();
    render(<DecisionQueueWorkspace />);

    await screen.findByTestId('decision-queue-list');
    const card = screen
      .getAllByTestId('decision-card')
      .find((c) => c.getAttribute('data-decision') === D1) as HTMLElement;
    fireEvent.click(card.querySelector('[data-testid="decision-need-info"]') as HTMLElement);
    fireEvent.change(card.querySelector('[data-testid="need-info-comment"]') as HTMLElement, {
      target: { value: 'Need the funding letter' },
    });
    fireEvent.click(card.querySelector('[data-testid="need-info-submit"]') as HTMLElement);

    await waitFor(() => {
      const actionCall = mock.mock.calls.find(([u]) => /\/action$/.test(String(u)));
      expect(actionCall).toBeTruthy();
      const body = JSON.parse(String((actionCall?.[1] as RequestInit).body));
      expect(body.action).toBe('need_info');
      expect(body.comment).toBe('Need the funding letter');
    });
  });

  it('renders the "Leadership only" state on a 403 (no crash)', async () => {
    stubApi({ decisionsStatus: 403, decisionsPayload: { detail: 'forbidden' } });
    render(<DecisionQueueWorkspace />);

    expect(await screen.findByTestId('decision-queue-forbidden')).toBeInTheDocument();
    expect(screen.getByText('Leadership only')).toBeInTheDocument();
    expect(screen.queryByTestId('decision-queue-list')).not.toBeInTheDocument();
  });

  it('fails safe with a quiet error on a non-OK fetch (no crash)', async () => {
    stubApi({ decisionsStatus: 500, decisionsPayload: {} });
    render(<DecisionQueueWorkspace />);

    expect(await screen.findByTestId('decision-queue-error')).toBeInTheDocument();
    expect(screen.queryByTestId('decision-queue-list')).not.toBeInTheDocument();
  });

  it('shows the open count, reflecting the number of open decisions', async () => {
    stubApi();
    render(<DecisionQueueWorkspace />);
    await screen.findByTestId('decision-queue-list');
    expect(screen.getByTestId('decision-open-count')).toHaveTextContent('2 open');
  });
});
