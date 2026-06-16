import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PublishMonitor from '../PublishMonitor';

// Acceptance test (CLAUDE §4.2) for the Publish & Monitor panel (FR-3.6).
//
// The DUAL-SCREEN observability plane: one piece is fanned out across N platforms;
// the cockpit shows per-platform tracking (status chips, media, HubSpot mirror
// status + link). INV-3 fail-closed: when the gating eval is RED the publish
// button is DISABLED with a visible reason. INV-4: a FAILED validation blocks all
// dispatches and the row says so (never softened). fireEvent only (no user-event).

// A green status — publish enabled.
const STATUS_ENABLED = {
  action_enabled: true,
  eval_name: 'message_safety_grounding',
};

// A red status — the gating eval is red; publish disabled (INV-3 fail-closed).
const STATUS_DISABLED = {
  action_enabled: false,
  eval_name: 'message_safety_grounding',
};

// A pre-existing monitor feed: one published piece fanned out to two platforms,
// one sent + mirrored (second screen), one blocked. Plus a placeholder image.
const MONITOR_FEED = [
  {
    request_id: 'req-1',
    body: 'Discover GT School',
    scheduled_for: '2026-07-01T09:00:00Z',
    campaign_theme: 'gifted_identity',
    dispatches: [
      {
        post_id: 'p-ig',
        channel: 'instagram',
        dispatch_status: 'simulated_sent',
        sent: true,
        blocked: false,
        capped: false,
        simulated_receipt: 'sim-receipt-ig',
        mirror_status: 'mirrored',
        hubspot_object_id: 'sim-gtsp-p-ig',
      },
      {
        post_id: 'p-x',
        channel: 'x',
        dispatch_status: 'blocked',
        sent: false,
        blocked: true,
        capped: false,
        simulated_receipt: null,
        mirror_status: 'skipped',
        hubspot_object_id: null,
      },
    ],
    media: [
      {
        kind: 'image',
        placeholder_uri: 'placeholder://gt-media/image/abc.png',
        asset_url: null,
        cost_estimate_ref: 'TECH_STACK.md#6-cost-model:media-gen',
        is_placeholder: true,
        brief: 'gifted identity',
        render_hint: '1024x1024 png',
      },
    ],
    hubspot_object_id: 'sim-gtsp-p-ig',
    validation_passed: true,
    failed_rules: [] as string[],
  },
];

// The created row returned by POST /content/publish (an all-sent fan-out).
const PUBLISH_CREATED = {
  request_id: 'req-2',
  body: 'New post copy',
  scheduled_for: '2026-07-01T09:00:00Z',
  campaign_theme: null,
  dispatches: [
    {
      post_id: 'p2-ig',
      channel: 'instagram',
      dispatch_status: 'simulated_sent',
      sent: true,
      blocked: false,
      capped: false,
      simulated_receipt: 'sim-receipt-2',
      mirror_status: 'mirrored',
      hubspot_object_id: 'sim-gtsp-p2-ig',
    },
  ],
  media: [],
  hubspot_object_id: 'sim-gtsp-p2-ig',
  validation_passed: true,
  failed_rules: [] as string[],
  action_enabled: true,
};

// Routes the three GETs/POST by URL + method to distinct payloads.
function mockFetchByUrl(routes: {
  status?: unknown;
  monitor?: unknown;
  publish?: unknown;
}): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      let payload: unknown = {};
      const u = String(url);
      if (init?.method === 'POST' && u.includes('/content/publish')) {
        payload = routes.publish ?? {};
      } else if (u.includes('/publish/status')) {
        payload = routes.status ?? STATUS_ENABLED;
      } else if (u.includes('/publish/monitor')) {
        payload = routes.monitor ?? [];
      }
      return { ok: true, status: 200, json: async () => payload };
    }),
  );
}

describe('PublishMonitor', () => {
  beforeEach(() => {
    mockFetchByUrl({ status: STATUS_ENABLED, monitor: MONITOR_FEED });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the monitor feed with per-platform chips and mirror status', async () => {
    mockFetchByUrl({ status: STATUS_ENABLED, monitor: MONITOR_FEED });
    render(<PublishMonitor />);

    // One row per published piece.
    const rows = await screen.findAllByTestId('monitor-row');
    expect(rows.length).toBe(1);

    // Per-platform status chips — instagram sent, x blocked.
    const igChip = screen.getByTestId('monitor-chip-instagram');
    expect(igChip).toHaveAttribute('data-status', 'sent');
    const xChip = screen.getByTestId('monitor-chip-x');
    expect(xChip).toHaveAttribute('data-status', 'blocked');

    // HubSpot mirror status — the second screen indicator + the object id link.
    expect(screen.getByTestId('monitor-mirror-mirrored')).toBeInTheDocument();
    const link = screen.getByTestId('monitor-mirror-link');
    expect(link).toHaveTextContent('sim-gtsp-p-ig');

    // Media thumbnail (placeholder ok).
    expect(screen.getByTestId('monitor-media-image')).toBeInTheDocument();
  });

  it('composes and submits a publish, fanning out to selected channels', async () => {
    // Initial monitor feed is empty; after publishing the server feed reflects the
    // new row (the client reconciles its optimistic prepend with the next GET).
    let monitorCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const u = String(url);
        let payload: unknown = {};
        if (init?.method === 'POST' && u.includes('/content/publish')) {
          payload = PUBLISH_CREATED;
        } else if (u.includes('/publish/status')) {
          payload = STATUS_ENABLED;
        } else if (u.includes('/publish/monitor')) {
          monitorCalls += 1;
          payload = monitorCalls === 1 ? [] : [PUBLISH_CREATED];
        }
        return { ok: true, status: 200, json: async () => payload };
      }),
    );
    render(<PublishMonitor />);

    // Compose: write a body (keep instagram selected), toggle image gen.
    const bodyInput = await screen.findByTestId('publish-body');
    fireEvent.change(bodyInput, { target: { value: 'New post copy' } });
    fireEvent.click(screen.getByTestId('publish-generate-image'));

    // With an enabled eval + a body + a channel, the button becomes actionable.
    await waitFor(() =>
      expect(screen.getByTestId('publish-submit')).toBeEnabled(),
    );

    fireEvent.click(screen.getByTestId('publish-submit'));

    // POST /content/publish fired with the composed body + selected channels.
    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const call = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]).includes('/content/publish') &&
          (c[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(call).toBeTruthy();
      const sent = JSON.parse(String((call?.[1] as RequestInit).body)) as {
        body: string;
        channels: string[];
        generate_image: boolean;
      };
      expect(sent.body).toBe('New post copy');
      expect(sent.channels).toContain('instagram');
      expect(sent.generate_image).toBe(true);
    });

    // The created row appears in the monitor board.
    await waitFor(() =>
      expect(screen.getByTestId('monitor-chip-instagram')).toBeInTheDocument(),
    );
  });

  it('disables the publish button when the gating eval is red (INV-3 fail-closed)', async () => {
    mockFetchByUrl({ status: STATUS_DISABLED, monitor: [] });
    render(<PublishMonitor />);

    // The red-eval notice explains the block.
    expect(
      await screen.findByTestId('publish-eval-blocked'),
    ).toBeInTheDocument();

    // Fail-closed: the publish button is disabled even with a body + channel.
    fireEvent.change(screen.getByTestId('publish-body'), {
      target: { value: 'a post' },
    });
    expect(screen.getByTestId('publish-submit')).toBeDisabled();
  });

  it('blocks all dispatches on a failed validation and says so (INV-4)', async () => {
    const baseRow = MONITOR_FEED[0]!;
    const blockedRow = {
      ...baseRow,
      request_id: 'req-blocked',
      validation_passed: false,
      failed_rules: ['v2_grounding', 'v4_onbrand'],
      dispatches: baseRow.dispatches.map((d) => ({
        ...d,
        sent: false,
        blocked: true,
        mirror_status: 'skipped',
        hubspot_object_id: null,
      })),
      hubspot_object_id: null,
    };
    mockFetchByUrl({ status: STATUS_ENABLED, monitor: [blockedRow] });
    render(<PublishMonitor />);

    // The blocked row shows the failing rules — the gate is shown, not softened.
    const failed = await screen.findByTestId('monitor-failed-rules');
    expect(failed).toHaveTextContent(/v2_grounding/);
    // No platform reads as sent; the mirror is clean (nothing went out).
    expect(screen.getByTestId('monitor-chip-instagram')).toHaveAttribute(
      'data-status',
      'blocked',
    );
    expect(screen.getByTestId('monitor-mirror-skipped')).toBeInTheDocument();
  });
});
