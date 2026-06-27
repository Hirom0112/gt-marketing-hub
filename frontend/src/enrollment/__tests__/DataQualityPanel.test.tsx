import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DataQualityPanel from '../DataQualityPanel';

// Acceptance test (CLAUDE §4.2) for the CRM-Ops Data-Quality panel (TODO_v2 §C1).
// It reads GET /crm/ops and renders the sync-parity header (+ the data-confidence
// banner), the SEVERITY-ORDERED data-quality queue (conflict first), red UTM
// chips for broken UTMs, and field-reliability badges. Honesty mandate: a broken
// UTM stays flagged RED and is NEVER shown as fixed; a conflict only resolves via
// the existing proposal/decision spine on an explicit human verdict (INV-2/INV-4).
// The fetch layer is stubbed (native fetch; apiFetch wraps it), mirroring
// MergeQueue.test / DataConfidenceBanner.test.

const OPS = {
  parity_overall: 0.842,
  parity_by_field: { stage: 0.9, value: 0.78 },
  data_confidence_banner: true,
  // Severity-ordered by the API: conflict first, then utm_broken.
  dq_queue: [
    {
      entity_id: 'deal-1',
      kind: 'conflict',
      severity: 9,
      detail: 'Stage differs between CRM and cockpit.',
      proposal_id: 'prop-1',
    },
    {
      entity_id: 'contact-7',
      kind: 'utm_broken',
      severity: 5,
      detail: 'Outbound link carries a malformed UTM.',
    },
    {
      entity_id: 'field-source',
      kind: 'unreliable_field',
      severity: 2,
      detail: 'lead_source is unreliable.',
    },
  ],
  utm_health: {
    ok: 12,
    broken: 1,
    broken_entities: [
      {
        entity_id: 'contact-7',
        offending_keys: ['utm_campaign'],
        reasons: ['empty value'],
      },
    ],
  },
  field_flags: [
    { field: 'stage', status: 'reliable', reason: null },
    { field: 'lead_source', status: 'unreliable', reason: 'high null rate' },
  ],
};

function mockApi(payload: unknown, decisionResult?: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'POST' && /\/proposals\/[^/]+\/decision$/.test(url)) {
        return {
          ok: true,
          status: 200,
          json: async () => decisionResult ?? { ok: true },
        };
      }
      if (/\/crm\/ops$/.test(url)) {
        return { ok: true, status: 200, json: async () => payload };
      }
      throw new Error(`unexpected fetch: ${method} ${url}`);
    }),
  );
}

describe('DataQualityPanel (C1)', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the parity header + the data-confidence banner when data_confidence_banner is true', async () => {
    mockApi(OPS);
    render(<DataQualityPanel />);

    expect(await screen.findByTestId('data-quality-parity')).toHaveTextContent(
      '84.2%',
    );
    const banner = screen.getByTestId('data-quality-confidence-banner');
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent('84.2%');
  });

  it('renders a broken UTM as a red chip with the offending key (never shown as fixed)', async () => {
    mockApi(OPS);
    render(<DataQualityPanel />);

    const chip = await screen.findByTestId('data-quality-utm-chip-contact-7');
    expect(chip).toHaveTextContent('utm_campaign');
    // Honesty mandate: no "fixed"/"resolved" affordance on a broken UTM.
    expect(chip).not.toHaveTextContent(/fixed|resolved/i);
    // A broken UTM gets no reconcile action — it stays flagged.
    expect(
      screen.queryByTestId('data-quality-reconcile-contact-7'),
    ).not.toBeInTheDocument();
  });

  it('renders the dq queue in severity order · conflict before utm_broken (server order preserved)', async () => {
    mockApi(OPS);
    render(<DataQualityPanel />);

    await screen.findByTestId('data-quality-issue-deal-1');
    const issues = screen.getAllByTestId('data-quality-issue');
    expect(issues[0]).toHaveAttribute('data-kind', 'conflict');
    expect(issues[1]).toHaveAttribute('data-kind', 'utm_broken');
  });

  it('wires a conflict with a proposal_id to the decision spine; a broken UTM has no such action', async () => {
    mockApi(OPS);
    render(<DataQualityPanel />);

    const reconcile = await screen.findByTestId('data-quality-reconcile-prop-1');
    fireEvent.click(reconcile);

    await waitFor(() =>
      expect(
        screen.queryByTestId('data-quality-issue-deal-1'),
      ).not.toBeInTheDocument(),
    );

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const post = fetchMock.mock.calls.find(
      ([, i]) => (i as RequestInit | undefined)?.method === 'POST',
    ) as [string, RequestInit] | undefined;
    expect(post?.[0]).toMatch(/\/proposals\/prop-1\/decision$/);
    expect(post?.[1].method).toBe('POST');
  });

  it('renders field-reliability badges with an unreliable marker + reason', async () => {
    mockApi(OPS);
    render(<DataQualityPanel />);

    const flag = await screen.findByTestId('data-quality-field-lead_source');
    expect(flag).toHaveTextContent('unreliable');
    expect(flag).toHaveTextContent('high null rate');
  });

  it('renders a clean empty state when the dq queue is empty', async () => {
    mockApi({
      ...OPS,
      data_confidence_banner: false,
      dq_queue: [],
      utm_health: { ok: 12, broken: 0, broken_entities: [] },
    });
    render(<DataQualityPanel />);

    expect(await screen.findByTestId('data-quality-empty')).toBeInTheDocument();
    expect(
      screen.queryByTestId('data-quality-confidence-banner'),
    ).not.toBeInTheDocument();
  });

  it('renders without crashing on a fetch error (quiet notice)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('network down'))),
    );
    render(<DataQualityPanel />);
    expect(await screen.findByTestId('data-quality-error')).toBeInTheDocument();
  });
});
