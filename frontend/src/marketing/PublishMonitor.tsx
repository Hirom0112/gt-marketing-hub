import { useCallback, useEffect, useState } from 'react';
import {
  Ban,
  CheckCircle2,
  Image as ImageIcon,
  Lock,
  RefreshCw,
  Send,
  Video,
} from 'lucide-react';
import { hubspotPortalId, apiFetch } from '../config';
import { Button, Card, Chip } from '../ui';

// Publish & Monitor panel (publish-monitor W4/W5; FR-3.6 / INV-2/3/4/8/9).
//
// The DUAL-SCREEN observability plane. One content piece is fanned out across N
// social platforms at once; the cockpit shows the FULL per-platform tracking
// (which post went where, status, media, HubSpot-mirror status + link) and
// HubSpot is the second screen (the GT Social Post mirror).
//
//   - The COMPOSER: pick channels via checkboxes, write the content, toggle
//     image/video gen, schedule, and approve, then POST /content/publish.
//   - The MONITOR board: GET /publish/monitor — one row per published piece with
//     per-platform status CHIPS (sent / blocked / capped), media thumbnails
//     (placeholder ok), and the HubSpot mirror status + (when present) the GT
//     Social Post object id/link (the "second screen" indicator).
//
// INV-3 fail-closed: the publish action is eval-gated. GET /publish/status carries
// `action_enabled` — when the gating eval is RED the publish button is DISABLED
// with a visible reason (a red eval disables the action in the UI). A FAILED
// validation BLOCKS all dispatches and the row says so (INV-4 — never softened).
//
// Native fetch only (≤2 runtime deps). Read/propose only on the client (INV-2):
// the deterministic backend core owns the fan-out + all writes; this UI requests
// the publish and renders the server's monitor feed.

// ---------------------------------------------------------------------------
// API contract types (snake_case, matching the backend exactly).
// ---------------------------------------------------------------------------

interface MediaRef {
  kind: string; // "image" | "video"
  placeholder_uri: string;
  asset_url: string | null;
  cost_estimate_ref: string;
  is_placeholder: boolean;
  brief?: string | null;
  render_hint?: string | null;
}

interface Dispatch {
  post_id: string;
  channel: string;
  dispatch_status: string;
  sent: boolean;
  blocked: boolean;
  capped: boolean;
  simulated_receipt: string | null;
  mirror_status: string; // "pending" | "mirrored" | "skipped"
  hubspot_object_id: string | null;
}

interface PublishMonitorRow {
  request_id: string;
  body: string;
  scheduled_for: string;
  campaign_theme?: string | null;
  dispatches: Dispatch[];
  media: MediaRef[];
  hubspot_object_id: string | null;
  validation_passed: boolean;
  failed_rules: string[];
}

interface PublishResponse extends PublishMonitorRow {
  action_enabled: boolean;
}

interface PublishStatus {
  action_enabled: boolean;
  eval_name: string;
}

// The targetable social channels (the backend rejects anything off this set; it
// is the subset of the LOCKED Channel enum the scheduler publishes to). Kept in
// sync with params.scheduler.publish_channels — an off-list channel 422s.
const PUBLISH_CHANNELS = ['instagram', 'tiktok', 'x', 'linkedin'] as const;
type PublishChannel = (typeof PUBLISH_CHANNELS)[number];

// A HubSpot deep link for a mirrored GT Social Post custom object. The object
// type id for the custom object is portal-specific; we link the portal record
// search so the "second screen" indicator is click-through even without the
// custom-object type id baked in.
function hubspotMirrorUrl(objectId: string): string {
  return `https://app-na2.hubspot.com/contacts/${hubspotPortalId}/objects/0-1/views/all/list?query=${encodeURIComponent(
    objectId,
  )}`;
}

// ---------------------------------------------------------------------------
// Per-platform status chip — the cockpit's first-screen tracking signal.
// ---------------------------------------------------------------------------
function DispatchChip({ dispatch }: { dispatch: Dispatch }): JSX.Element {
  const tone = dispatch.sent ? 'flow' : dispatch.capped ? 'gate' : 'signal';
  const label = dispatch.sent ? 'sent' : dispatch.capped ? 'capped' : 'blocked';
  return (
    <span
      data-testid={`monitor-chip-${dispatch.channel}`}
      data-status={label}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
    >
      <Chip tone={tone}>
        {dispatch.channel} · {label}
      </Chip>
    </span>
  );
}

// ---------------------------------------------------------------------------
// One monitor row — both screens for one published piece.
// ---------------------------------------------------------------------------
function MonitorRow({ row }: { row: PublishMonitorRow }): JSX.Element {
  const mirrored = row.dispatches.filter((d) => d.mirror_status === 'mirrored');
  return (
    <div data-testid="monitor-row">
      <Card style={{ display: 'grid', gap: 'var(--s-3)' }}>
        <div
          style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--s-2)' }}
        >
          <span style={{ flex: 1, fontSize: 'var(--fs-body)' }}>
            {row.body}
          </span>
          <Chip tone={row.validation_passed ? 'flow' : 'signal'}>
            {row.validation_passed ? 'validated' : 'blocked'}
          </Chip>
        </div>

        <div className="lab" style={{ fontSize: 'var(--fs-sm)' }}>
          scheduled {row.scheduled_for}
          {row.campaign_theme ? ` · ${row.campaign_theme}` : ''}
        </div>

        {!row.validation_passed && row.failed_rules.length > 0 && (
          <div
            data-testid="monitor-failed-rules"
            role="alert"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--s-2)',
              color: 'var(--signal-ink)',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <Ban size={14} aria-hidden style={{ flexShrink: 0 }} />
            <span>
              Blocked by the grounding gate — no dispatch:{' '}
              <strong>{row.failed_rules.join(', ')}</strong>
            </span>
          </div>
        )}

        {/* Screen one: per-platform status chips. */}
        <div
          data-testid="monitor-dispatches"
          style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--s-2)' }}
        >
          {row.dispatches.map((d) => (
            <DispatchChip key={d.post_id} dispatch={d} />
          ))}
        </div>

        {/* Media thumbnails (placeholder ok — $0, OUT-1). */}
        {row.media.length > 0 && (
          <div
            data-testid="monitor-media"
            style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--s-2)' }}
          >
            {row.media.map((m) => (
              <div
                key={m.placeholder_uri}
                data-testid={`monitor-media-${m.kind}`}
                title={m.placeholder_uri}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '6px 10px',
                  borderRadius: 'var(--r-sm)',
                  background: 'var(--surface-2)',
                  border: '1px solid var(--line)',
                  fontSize: 'var(--fs-sm)',
                }}
              >
                {m.kind === 'video' ? (
                  <Video size={14} aria-hidden />
                ) : (
                  <ImageIcon size={14} aria-hidden />
                )}
                <span className="mono">{m.render_hint ?? m.kind}</span>
                {m.is_placeholder && <Chip>placeholder</Chip>}
              </div>
            ))}
          </div>
        )}

        {/* Screen two: the HubSpot GT Social Post mirror status + link. */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
            flexWrap: 'wrap',
          }}
        >
          <span className="lab" style={{ fontSize: 'var(--fs-sm)' }}>
            HubSpot mirror
          </span>
          {mirrored.length > 0 ? (
            <span
              data-testid="monitor-mirror-mirrored"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
            >
              <Chip tone="flow">{mirrored.length} mirrored</Chip>
              {row.hubspot_object_id && (
                <a
                  data-testid="monitor-mirror-link"
                  href={hubspotMirrorUrl(row.hubspot_object_id)}
                  target="_blank"
                  rel="noreferrer"
                  className="mono"
                  style={{ fontSize: 'var(--fs-sm)', color: 'var(--flow-ink)' }}
                >
                  {row.hubspot_object_id}
                </a>
              )}
            </span>
          ) : (
            <span data-testid="monitor-mirror-skipped">
              <Chip tone="signal">none mirrored</Chip>
            </span>
          )}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The composer + monitor board.
// ---------------------------------------------------------------------------
export default function PublishMonitor(): JSX.Element {
  const [body, setBody] = useState('');
  const [scheduledFor, setScheduledFor] = useState('2026-07-01T09:00:00Z');
  const [channels, setChannels] = useState<Record<PublishChannel, boolean>>({
    instagram: true,
    tiktok: false,
    x: true,
    linkedin: false,
  });
  const [approve, setApprove] = useState(true);
  const [genImage, setGenImage] = useState(false);
  const [genVideo, setGenVideo] = useState(false);

  const [feed, setFeed] = useState<PublishMonitorRow[]>([]);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The eval-gate flag (INV-3): null while loading, then enabled/disabled.
  const [actionEnabled, setActionEnabled] = useState<boolean | null>(null);
  const [evalName, setEvalName] = useState<string>('message_safety_grounding');

  const loadFeed = useCallback((): void => {
    apiFetch(`/publish/monitor`)
      .then((res) => {
        if (!res.ok) throw new Error(`monitor request failed: ${res.status}`);
        return res.json() as Promise<PublishMonitorRow[]>;
      })
      .then((rows) => setFeed(rows))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'unknown error');
      });
  }, []);

  const loadStatus = useCallback((): void => {
    apiFetch(`/publish/status`)
      .then((res) => {
        if (!res.ok) throw new Error(`status request failed: ${res.status}`);
        return res.json() as Promise<PublishStatus>;
      })
      .then((s) => {
        setActionEnabled(s.action_enabled);
        setEvalName(s.eval_name);
      })
      .catch(() => {
        // Fail-closed on a status error: disable the action (never silently allow).
        setActionEnabled(false);
      });
  }, []);

  useEffect(() => {
    loadStatus();
    loadFeed();
  }, [loadStatus, loadFeed]);

  function toggleChannel(channel: PublishChannel): void {
    setChannels((prev) => ({ ...prev, [channel]: !prev[channel] }));
  }

  const selectedChannels = PUBLISH_CHANNELS.filter((c) => channels[c]);
  const canPublish =
    actionEnabled === true &&
    !publishing &&
    body.trim().length > 0 &&
    selectedChannels.length > 0;

  function submitPublish(): void {
    if (!canPublish) return;
    setPublishing(true);
    setError(null);
    apiFetch(`/content/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        body: body.trim(),
        channels: selectedChannels,
        scheduled_for: scheduledFor,
        approval: { decision: approve ? 'approve' : 'pending' },
        generate_image: genImage,
        generate_video: genVideo,
      }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`publish failed: ${res.status}`);
        return res.json() as Promise<PublishResponse>;
      })
      .then((created) => {
        // Optimistically prepend the new row, then reconcile with the server feed.
        setFeed((prev) => [created, ...prev]);
        loadFeed();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'unknown error');
      })
      .finally(() => setPublishing(false));
  }

  const evalGreen = actionEnabled === true;

  return (
    <section
      aria-label="Publish and monitor"
      data-testid="publish-monitor"
      style={{ display: 'grid', gap: 'var(--s-4)' }}
    >
      <header
        style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}
      >
        <Send size={16} aria-hidden style={{ color: 'var(--flow)' }} />
        <h2 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, margin: 0 }}>
          Publish &amp; Monitor — fan out across platforms
        </h2>
        <span style={{ marginLeft: 'auto' }}>
          <Chip tone={evalGreen ? 'flow' : 'signal'}>
            {evalGreen ? 'EVAL GREEN' : 'EVAL RED'}
          </Chip>
        </span>
      </header>

      {/* Composer. */}
      <Card style={{ display: 'grid', gap: 'var(--s-3)' }}>
        <p className="lab" style={{ margin: 0 }}>
          Compose one piece, fan it out to every selected platform, mirror to
          HubSpot
        </p>

        <textarea
          data-testid="publish-body"
          aria-label="Content body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Write the post copy to publish across platforms…"
          rows={3}
          style={{
            fontFamily: 'var(--sans)',
            fontSize: 'var(--fs-body)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
            padding: 'var(--s-2) var(--s-3)',
            background: 'var(--surface-2)',
            color: 'var(--ink)',
            resize: 'vertical',
          }}
        />

        {/* Channel checkboxes. */}
        <div
          data-testid="publish-channels"
          style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--s-3)' }}
        >
          {PUBLISH_CHANNELS.map((channel) => (
            <label
              key={channel}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 'var(--fs-body)',
              }}
            >
              <input
                type="checkbox"
                data-testid={`publish-channel-${channel}`}
                checked={channels[channel]}
                onChange={() => toggleChannel(channel)}
              />
              <span className="mono">{channel}</span>
            </label>
          ))}
        </div>

        {/* Schedule + media + approval toggles. */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 'var(--s-3)',
            alignItems: 'center',
          }}
        >
          <label
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <span className="lab">Schedule</span>
            <input
              data-testid="publish-scheduled-for"
              aria-label="Scheduled for"
              value={scheduledFor}
              onChange={(e) => setScheduledFor(e.target.value)}
              style={{
                fontFamily: 'var(--mono)',
                fontSize: 'var(--fs-sm)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-sm)',
                padding: '4px 8px',
                background: 'var(--surface-2)',
                color: 'var(--ink)',
              }}
            />
          </label>
          <label
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <input
              type="checkbox"
              data-testid="publish-generate-image"
              checked={genImage}
              onChange={() => setGenImage((v) => !v)}
            />
            <ImageIcon size={14} aria-hidden /> <span>image</span>
          </label>
          <label
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <input
              type="checkbox"
              data-testid="publish-generate-video"
              checked={genVideo}
              onChange={() => setGenVideo((v) => !v)}
            />
            <Video size={14} aria-hidden /> <span>video</span>
          </label>
          <label
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <input
              type="checkbox"
              data-testid="publish-approve"
              checked={approve}
              onChange={() => setApprove((v) => !v)}
            />
            <span>approve (human review)</span>
          </label>
        </div>

        {/* Fail-closed notice + the publish button. */}
        {actionEnabled === false && (
          <div
            data-testid="publish-eval-blocked"
            role="alert"
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 'var(--s-2)',
              padding: 'var(--s-3)',
              borderRadius: 'var(--r-sm)',
              background: 'var(--signal-wash)',
              border: '1px solid var(--signal)',
              color: 'var(--signal-ink)',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <Lock
              size={15}
              aria-hidden
              style={{ flexShrink: 0, marginTop: 2 }}
            />
            <span>
              The <strong>{evalName}</strong> eval is <strong>red</strong> —
              publish is disabled until the eval passes (fail closed, INV-3).
            </span>
          </div>
        )}

        <div>
          <Button
            variant="primary"
            icon={Send}
            data-testid="publish-submit"
            onClick={submitPublish}
            disabled={!canPublish}
          >
            {publishing ? 'Publishing…' : 'Publish to selected platforms'}
          </Button>
        </div>

        {error && (
          <p
            data-testid="publish-error"
            role="alert"
            style={{
              color: 'var(--signal-ink)',
              margin: 0,
              fontSize: 'var(--fs-sm)',
            }}
          >
            {error}
          </p>
        )}
      </Card>

      {/* Monitor board — the dual-screen feed. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
        <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}>
          Monitor — per-platform tracking + HubSpot second screen
        </h3>
        <span style={{ marginLeft: 'auto' }}>
          <Button
            icon={RefreshCw}
            data-testid="monitor-refresh"
            onClick={loadFeed}
          >
            Refresh
          </Button>
        </span>
      </div>

      {feed.length === 0 ? (
        <Card>
          <p data-testid="monitor-empty" className="lab" style={{ margin: 0 }}>
            <CheckCircle2 size={14} aria-hidden /> Nothing published yet —
            compose a piece above to fan it out.
          </p>
        </Card>
      ) : (
        <div
          data-testid="monitor-board"
          style={{ display: 'grid', gap: 'var(--s-3)' }}
        >
          {feed.map((row) => (
            <MonitorRow key={row.request_id} row={row} />
          ))}
        </div>
      )}
    </section>
  );
}
