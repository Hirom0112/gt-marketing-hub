import { useEffect, useState } from 'react';
import {
  BarChart3,
  CalendarDays,
  Ban,
  FileText,
  MapPin,
  MessageSquare,
  Play,
  Plus,
  Users,
} from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card, Chip, PlaceholderBadge, Stat } from '../ui';

// Marketing-breadth workspace (S6 / FR-3.8/3.9, OUT-1/2/5, INV-3/4/6/7).
//
// One read-only-leaning surface that composes the staged-pipeline breadth
// panels. Each panel makes its trust property VISIBLE rather than implied:
//   - Creator discovery (GET /creators): fit + authenticity scores with an
//     AGGREGATE/synthetic badge per row (INV-6 — creator/competitor data is
//     aggregate-only, never individual-minor; we render is_minor as a guard).
//   - Sentiment monitor (GET /sentiment): the aggregate pos/neu/neg summary with
//     a "placeholder" source badge (OUT-5 — not a live feed).
//   - KPI board (GET /kpi): per-channel metric vs baseline/target, the signed
//     lever delta vs baseline, and a target-met indicator.
//   - Staged pipeline (GET /content/pipeline): concept → image → video, image & video
//     clearly badged "placeholder" (OUT-1 — no live media gen in v1).
//   - Scheduler (GET/POST /content/schedule): scheduled posts with their
//     dispatch_status; a BLOCKED post is shown blocked with NO send affordance
//     (fail-closed, INV-3/INV-4); dispatch_mode is always simulated (OUT-2).
//   - Geo targeting (FR-3.9): an explicit aggregate-only panel — no child-keyed
//     targeting (INV-6). No fabricated data; the trust property is the content.
//
// Plus the recipe runner (GET /recipes) which RENDERS the Tom Babb attribution
// (§8.5, INV-7) — authorship is never stripped from the UI.
//
// Native fetch only (≤2 runtime deps). Read/propose only (INV-2): the scheduler
// POST records a simulated dispatch; the deterministic core owns all writes.

// ---------------------------------------------------------------------------
// API contract types (all snake_case, matching the backend exactly).
// ---------------------------------------------------------------------------

interface Creator {
  id: string;
  display_handle: string;
  channel: string;
  audience_segment: string;
  fit_score: number; // 0.0..1.0
  authenticity_score: number; // 0.0..1.0
  rationale: string;
  data_mode: string; // e.g. "aggregate" / "synthetic"
  is_minor: boolean;
}

interface SentimentSummary {
  positive: number;
  neutral: number;
  negative: number;
  total: number;
  source_mode: string; // e.g. "placeholder"
}

interface SentimentRecord {
  id: string;
  channel: string;
  topic: string;
  sentiment: string;
  score?: number;
  excerpt?: string;
  source_mode: string;
  observed_at: string;
}

interface SentimentResponse {
  summary: SentimentSummary;
  records: SentimentRecord[];
}

interface KpiRow {
  channel: string;
  metric: string;
  baseline: number;
  target: number;
  lever_delta: number; // signed, vs baseline
  target_gap: number;
  target_met: boolean;
}

interface ScheduledPost {
  id: string;
  channel: string;
  scheduled_for: string;
  dispatch_mode: string; // always "simulated" in v1
  dispatch_status: 'queued' | 'simulated_sent' | 'blocked';
  simulated_result?: string;
}

interface PipelineStage {
  status: string;
  placeholder_uri?: string;
  caption?: string;
  prompt?: string;
}

interface PipelineResponse {
  concept: PipelineStage;
  image: PipelineStage; // status "placeholder"
  video: PipelineStage; // status "placeholder"
}

interface RegionDemand {
  region: string;
  lead_count: number;
  share: number; // 0..1
}

interface DemandMetro {
  metro: string;
  state: string;
}

interface GeoTargeting {
  regions: RegionDemand[];
  demand_metros: DemandMetro[];
  total: number;
}

interface RecipeParameter {
  name: string;
  description?: string;
}

interface Recipe {
  id: string;
  name: string;
  attribution: string; // names Tom Babb (INV-7)
  description: string;
  parameters: RecipeParameter[];
}

// ---------------------------------------------------------------------------
// Generic load state + a small typed GET helper.
// ---------------------------------------------------------------------------

type LoadState<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T };

function useGet<T>(path: string, nonce = 0): LoadState<T> {
  const [state, setState] = useState<LoadState<T>>({ status: 'loading' });
  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(path)
      .then((res) => {
        if (!res.ok) throw new Error(`request failed: ${res.status}`);
        return res.json() as Promise<T>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path, nonce]);
  return state;
}

function pct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

function signed(value: number): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value}`;
}

// ---------------------------------------------------------------------------
// Small shared panel chrome.
// ---------------------------------------------------------------------------

interface PanelProps {
  title: string;
  icon: typeof Users;
  testid: string;
  children: React.ReactNode;
  badge?: React.ReactNode;
}

function Panel({
  title,
  icon: Icon,
  testid,
  children,
  badge,
}: PanelProps): JSX.Element {
  return (
    <Card
      className="mb-panel"
      data-testid={testid}
      style={{ display: 'grid', gap: 'var(--s-3)' }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
        }}
      >
        <Icon size={15} aria-hidden style={{ color: 'var(--ink-soft)' }} />
        <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, margin: 0 }}>
          {title}
        </h3>
        {badge ? <span style={{ marginLeft: 'auto' }}>{badge}</span> : null}
      </div>
      {children}
    </Card>
  );
}

const errStyle: React.CSSProperties = {
  color: 'var(--signal-ink)',
  margin: 0,
  fontSize: 'var(--fs-sm)',
};

const emptyStyle: React.CSSProperties = {
  color: 'var(--muted)',
  margin: 0,
  fontSize: 'var(--fs-sm)',
};

const rowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 'var(--s-3)',
  flexWrap: 'wrap',
  padding: '10px 12px',
  borderRadius: 'var(--r-sm)',
  background: 'var(--surface-2)',
  border: '1px solid var(--line)',
};

const listStyle: React.CSSProperties = {
  listStyle: 'none',
  margin: 0,
  padding: 0,
  display: 'grid',
  gap: 'var(--s-2)',
};

// ---------------------------------------------------------------------------
// Workspace surface.
// ---------------------------------------------------------------------------

export default function MarketingBreadth(): JSX.Element {
  return (
    <section
      aria-label="Marketing breadth"
      data-testid="marketing-breadth"
      className="marketing-breadth"
      style={{ display: 'grid', gap: 'var(--s-4)' }}
    >
      <header
        style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}
      >
        <BarChart3 size={16} aria-hidden style={{ color: 'var(--ink-soft)' }} />
        <h2 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, margin: 0 }}>
          Marketing breadth
        </h2>
      </header>
      <CreatorPanel />
      <SentimentPanel />
      <KpiPanel />
      <PipelinePanel />
      <SchedulerPanel />
      <GeoTargetingPanel />
      <RecipeRunner />
    </section>
  );
}

// 1. Creator-discovery report ------------------------------------------------
function CreatorPanel(): JSX.Element {
  const state = useGet<Creator[]>('/creators');
  return (
    <Panel title="Creator discovery" icon={Users} testid="creator-panel">
      {state.status === 'loading' && (
        <p data-testid="creator-loading" className="lab">
          Loading creators…
        </p>
      )}
      {state.status === 'error' && (
        <p data-testid="creator-error" role="alert" style={errStyle}>
          Could not load creators: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.data.length === 0 ? (
          <p data-testid="creator-empty" style={emptyStyle}>
            No creators discovered yet.
          </p>
        ) : (
          <ul className="creator-list" style={listStyle}>
            {state.data.map((c) => (
              <li
                key={c.id}
                className="creator-row"
                data-testid={`creator-${c.id}`}
                style={rowStyle}
              >
                <div style={{ flex: 1, minWidth: 160 }}>
                  <div
                    className="creator-handle mono"
                    style={{ fontSize: 'var(--fs-body)', fontWeight: 600 }}
                  >
                    {c.display_handle}
                  </div>
                  <div
                    className="creator-channel lab"
                    style={{ marginTop: 2 }}
                  >
                    {c.channel}
                  </div>
                </div>
                <span
                  className="mono"
                  data-testid={`creator-fit-${c.id}`}
                  style={{ fontSize: 'var(--fs-sm)', color: 'var(--flow)' }}
                >
                  Fit {pct(c.fit_score)}
                </span>
                <span
                  className="mono"
                  data-testid={`creator-authenticity-${c.id}`}
                  style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
                >
                  Authenticity {pct(c.authenticity_score)}
                </span>
                {/* INV-6: aggregate-only; the data_mode badge makes the trust
                    property visible. A minor is never individually targetable. */}
                <span
                  className="badge badge-aggregate"
                  data-testid={`creator-data-mode-${c.id}`}
                >
                  <Chip tone="flow">{c.data_mode}</Chip>
                </span>
              </li>
            ))}
          </ul>
        ))}
    </Panel>
  );
}

// 2. Sentiment monitor -------------------------------------------------------
function SentimentPanel(): JSX.Element {
  const state = useGet<SentimentResponse>('/sentiment');
  return (
    <Panel
      title="Sentiment monitor"
      icon={MessageSquare}
      testid="sentiment-panel"
      badge={
        state.status === 'ready' ? (
          // OUT-5: not a live feed — the source_mode placeholder badge says so.
          <span
            className="badge badge-placeholder"
            data-testid="sentiment-source-mode"
          >
            <PlaceholderBadge label={state.data.summary.source_mode} />
          </span>
        ) : undefined
      }
    >
      {state.status === 'loading' && (
        <p data-testid="sentiment-loading" className="lab">
          Loading sentiment…
        </p>
      )}
      {state.status === 'error' && (
        <p data-testid="sentiment-error" role="alert" style={errStyle}>
          Could not load sentiment: {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <div
          className="sentiment-summary"
          data-testid="sentiment-summary"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))',
            gap: 'var(--s-3)',
          }}
        >
          <Stat
            label="Positive"
            tone="flow"
            value={
              <span data-testid="sentiment-positive">
                {state.data.summary.positive}
              </span>
            }
          />
          <Stat
            label="Neutral"
            value={
              <span data-testid="sentiment-neutral">
                {state.data.summary.neutral}
              </span>
            }
          />
          <Stat
            label="Negative"
            tone="signal"
            value={
              <span data-testid="sentiment-negative">
                {state.data.summary.negative}
              </span>
            }
          />
          <Stat
            label="Total"
            value={
              <span data-testid="sentiment-total">
                {state.data.summary.total}
              </span>
            }
          />
        </div>
      )}
      {/* OUT-5: surface the seeded placeholder records (excerpt + topic +
          sentiment + channel) — context behind the summary tiles. The
          placeholder source badge above says this is not a live feed. */}
      {state.status === 'ready' && (state.data.records ?? []).length > 0 && (
        <ul
          className="sentiment-records"
          data-testid="sentiment-records"
          style={listStyle}
        >
          {(state.data.records ?? []).map((rec) => (
            <li
              key={rec.id}
              className="sentiment-record"
              data-testid={`sentiment-record-${rec.id}`}
              style={{ ...rowStyle, alignItems: 'flex-start' }}
            >
              <div style={{ flex: 1, minWidth: 200 }}>
                <div
                  className="sentiment-excerpt"
                  style={{ fontSize: 'var(--fs-sm)' }}
                >
                  {rec.excerpt ?? rec.topic}
                </div>
                <div
                  className="sentiment-meta lab"
                  style={{ marginTop: 2, color: 'var(--muted)' }}
                >
                  <span data-testid={`sentiment-record-topic-${rec.id}`}>
                    {rec.topic}
                  </span>
                  {' · '}
                  <span data-testid={`sentiment-record-channel-${rec.id}`}>
                    {rec.channel}
                  </span>
                </div>
              </div>
              <span
                className="sentiment-polarity"
                data-testid={`sentiment-record-sentiment-${rec.id}`}
              >
                <Chip
                  tone={
                    rec.sentiment === 'positive'
                      ? 'flow'
                      : rec.sentiment === 'negative'
                        ? 'signal'
                        : 'neutral'
                  }
                >
                  {rec.sentiment}
                </Chip>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// 3. KPI board with levers ---------------------------------------------------
function KpiPanel(): JSX.Element {
  const state = useGet<KpiRow[]>('/kpi');
  return (
    <Panel title="KPI board" icon={BarChart3} testid="kpi-panel">
      {state.status === 'loading' && (
        <p data-testid="kpi-loading" className="lab">
          Loading KPIs…
        </p>
      )}
      {state.status === 'error' && (
        <p data-testid="kpi-error" role="alert" style={errStyle}>
          Could not load KPIs: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.data.length === 0 ? (
          <p data-testid="kpi-empty" style={emptyStyle}>
            No KPIs yet.
          </p>
        ) : (
          <ul className="kpi-list" style={listStyle}>
            {state.data.map((k) => (
              <li
                key={`${k.channel}-${k.metric}`}
                className="kpi-row"
                data-testid={`kpi-${k.channel}`}
                style={rowStyle}
              >
                <div style={{ flex: 1, minWidth: 140 }}>
                  <div
                    className="kpi-channel mono"
                    style={{ fontSize: 'var(--fs-body)', fontWeight: 600 }}
                  >
                    {k.channel}
                  </div>
                  <div className="kpi-metric lab" style={{ marginTop: 2 }}>
                    {k.metric}
                  </div>
                </div>
                <span
                  className="mono"
                  data-testid={`kpi-baseline-${k.channel}`}
                  style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
                >
                  Baseline {k.baseline}
                </span>
                <span
                  className="mono"
                  data-testid={`kpi-target-${k.channel}`}
                  style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
                >
                  Target {k.target}
                </span>
                {/* The lever delta, signed, vs baseline. */}
                <span
                  className="mono"
                  data-testid={`kpi-lever-${k.channel}`}
                  style={{
                    fontSize: 'var(--fs-sm)',
                    fontWeight: 600,
                    color:
                      k.lever_delta >= 0 ? 'var(--flow)' : 'var(--signal)',
                  }}
                >
                  Lever {signed(k.lever_delta)}
                </span>
                <span
                  data-testid={`kpi-met-${k.channel}`}
                  className={k.target_met ? 'kpi-met' : 'kpi-unmet'}
                >
                  <Chip tone={k.target_met ? 'flow' : 'signal'}>
                    {k.target_met ? 'Target met' : 'Below target'}
                  </Chip>
                </span>
              </li>
            ))}
          </ul>
        ))}
    </Panel>
  );
}

// 4. Staged-pipeline view ----------------------------------------------------
// The cheapest-first advance gate (INV-3) is wired to a "select → advance"
// control: it POSTs the current stage + its human-selection status + validation
// to /content/pipeline/advance. A 200 unlocks the next (costlier) stage; a 422
// shows the fail-closed blocked reason (the stage is NOT advanced). Image/video
// stay placeholder-badged (OUT-1) — no live media gen.
function PipelinePanel(): JSX.Element {
  const state = useGet<PipelineResponse>('/content/pipeline');
  type Advance =
    | { kind: 'idle' }
    | { kind: 'advancing' }
    | { kind: 'unlocked'; nextStage: string }
    | { kind: 'blocked'; reason: string };
  const [advance, setAdvance] = useState<Advance>({ kind: 'idle' });

  function advanceConcept(): void {
    setAdvance({ kind: 'advancing' });
    // The §4 guard advances the concept only when it is human-selected AND holds
    // a passing validation; the server returns 422 (fail-closed) otherwise.
    apiFetch(`/content/pipeline/advance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stage: 'concept',
        status: 'selected',
        validation: { passed: true },
      }),
    })
      .then(async (res) => {
        const data = (await res.json()) as {
          next_stage?: string;
          detail?: string;
        };
        if (res.ok && data.next_stage) {
          setAdvance({ kind: 'unlocked', nextStage: data.next_stage });
        } else {
          // 422 (or any non-200): show the fail-closed blocked reason; the stage
          // is NOT advanced (INV-3). We never silently advance on a non-200.
          setAdvance({
            kind: 'blocked',
            reason: data.detail ?? `advance refused (${res.status})`,
          });
        }
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setAdvance({ kind: 'blocked', reason: message });
      });
  }

  return (
    <Panel title="Staged pipeline" icon={Play} testid="pipeline-panel">
      {state.status === 'loading' && (
        <p data-testid="pipeline-loading" className="lab">
          Loading pipeline…
        </p>
      )}
      {state.status === 'error' && (
        <p data-testid="pipeline-error" role="alert" style={errStyle}>
          Could not load pipeline: {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <ol
          className="pipeline-stages"
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 'var(--s-2)',
          }}
        >
          <li
            className="pipeline-stage"
            data-testid="pipeline-concept"
            style={{ ...rowStyle, justifyContent: 'space-between' }}
          >
            <span className="stage-name" style={{ fontWeight: 600 }}>
              Concept
            </span>
            <span className="stage-status">
              <Chip tone="flow">{state.data.concept.status}</Chip>
            </span>
          </li>
          <PlaceholderStage
            name="Image"
            stage={state.data.image}
            testid="pipeline-image"
          />
          <PlaceholderStage
            name="Video"
            stage={state.data.video}
            testid="pipeline-video"
          />
        </ol>
      )}
      {state.status === 'ready' && (
        <div
          className="pipeline-advance"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: 'var(--s-2)',
          }}
        >
          <Button
            icon={Play}
            data-testid="pipeline-advance"
            onClick={advanceConcept}
            disabled={advance.kind === 'advancing'}
          >
            {advance.kind === 'advancing'
              ? 'Advancing…'
              : 'Select & advance concept'}
          </Button>
          {advance.kind === 'unlocked' && (
            <span role="status" data-testid="pipeline-advance-result">
              <Chip tone="flow">Unlocked next stage: {advance.nextStage}</Chip>
            </span>
          )}
          {advance.kind === 'blocked' && (
            // Fail-closed (INV-3): a refused advance shows the blocked reason and
            // does NOT advance the stage. There is no override affordance.
            <span
              role="alert"
              data-testid="pipeline-advance-blocked"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 'var(--s-1)',
                color: 'var(--signal-ink)',
                fontSize: 'var(--fs-sm)',
                fontWeight: 600,
              }}
            >
              <Ban size={13} aria-hidden /> Blocked — {advance.reason}
            </span>
          )}
        </div>
      )}
    </Panel>
  );
}

// Image/video stages are placeholders in v1 (OUT-1: no live media gen). The
// placeholder badge makes that explicit; we never present a fabricated asset.
function PlaceholderStage({
  name,
  stage,
  testid,
}: {
  name: string;
  stage: PipelineStage;
  testid: string;
}): JSX.Element {
  return (
    <li
      className="pipeline-stage"
      data-testid={testid}
      style={{ ...rowStyle, justifyContent: 'space-between' }}
    >
      <span className="stage-name" style={{ fontWeight: 600 }}>
        {name}
      </span>
      <span className="badge badge-placeholder" data-testid={`${testid}-badge`}>
        <PlaceholderBadge
          label={stage.status === 'placeholder' ? 'placeholder' : stage.status}
        />
      </span>
      {stage.placeholder_uri && (
        <span
          className="stage-uri mono"
          data-testid={`${testid}-uri`}
          style={{
            fontSize: 'var(--fs-micro)',
            color: 'var(--muted)',
            flexBasis: '100%',
          }}
        >
          {stage.placeholder_uri}
        </span>
      )}
    </li>
  );
}

// 5. Scheduler ---------------------------------------------------------------
function SchedulerPanel(): JSX.Element {
  const [nonce, setNonce] = useState(0);
  const state = useGet<ScheduledPost[]>('/content/schedule', nonce);
  const [scheduling, setScheduling] = useState(false);

  function schedule(): void {
    setScheduling(true);
    // The backend ScheduleRequest requires channel/scheduled_for/approval/
    // validation; the gate decides simulated_sent vs blocked from approval +
    // validation.passed (INV-3/INV-4). v1 schedules an approved + validated
    // email post one day out, which simulate-sends (dispatch_mode is forced to
    // simulated server-side, OUT-2). A channel/asset picker is a later slice.
    const scheduledFor = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
    apiFetch(`/content/schedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel: 'email',
        scheduled_for: scheduledFor,
        approval: { decision: 'approve' },
        validation: { passed: true },
      }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`schedule failed: ${res.status}`);
        return res.json() as Promise<ScheduledPost>;
      })
      .then(() => setNonce((n) => n + 1))
      .catch(() => {
        // A failed schedule leaves the list unchanged; the control re-enables.
      })
      .finally(() => setScheduling(false));
  }

  return (
    <Panel title="Scheduler" icon={CalendarDays} testid="scheduler-panel">
      {state.status === 'loading' && (
        <p data-testid="scheduler-loading" className="lab">
          Loading schedule…
        </p>
      )}
      {state.status === 'error' && (
        <p data-testid="scheduler-error" role="alert" style={errStyle}>
          Could not load schedule: {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <>
          <div>
            <Button
              variant="primary"
              icon={Plus}
              data-testid="scheduler-add"
              onClick={schedule}
              disabled={scheduling}
            >
              {scheduling ? 'Scheduling…' : 'Schedule post'}
            </Button>
          </div>
          {state.data.length === 0 ? (
            <p data-testid="scheduler-empty" style={emptyStyle}>
              No scheduled posts yet.
            </p>
          ) : (
            <ul className="schedule-list" style={listStyle}>
              {state.data.map((post) => (
                <ScheduledPostRow key={post.id} post={post} />
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

function ScheduledPostRow({ post }: { post: ScheduledPost }): JSX.Element {
  const blocked = post.dispatch_status === 'blocked';
  return (
    <li
      className={`schedule-row ${blocked ? 'blocked' : ''}`}
      data-testid={`schedule-${post.id}`}
      data-status={post.dispatch_status}
      style={{
        ...rowStyle,
        borderColor: blocked ? 'var(--signal)' : 'var(--line)',
        background: blocked ? 'var(--signal-wash)' : 'var(--surface-2)',
      }}
    >
      <span
        className="schedule-channel mono"
        style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}
      >
        {post.channel}
      </span>
      <span
        className="schedule-when lab"
        style={{ flex: 1, minWidth: 120 }}
      >
        {post.scheduled_for}
      </span>
      {/* OUT-2: every v1 dispatch is simulated — badge it. */}
      <span
        className="badge badge-simulated"
        data-testid={`schedule-mode-${post.id}`}
      >
        <PlaceholderBadge label={post.dispatch_mode} />
      </span>
      {blocked ? (
        // Fail-closed (INV-3/INV-4): a blocked post shows a red blocked status
        // and offers NO send affordance. There is deliberately no send control.
        <span
          className="schedule-status blocked"
          data-testid={`schedule-blocked-${post.id}`}
          role="alert"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            color: 'var(--signal-ink)',
            fontSize: 'var(--fs-sm)',
            fontWeight: 600,
          }}
        >
          <Ban size={13} aria-hidden /> Blocked — not dispatched
        </span>
      ) : (
        <span className="schedule-status" data-testid={`schedule-status-${post.id}`}>
          <Chip tone={post.dispatch_status === 'simulated_sent' ? 'flow' : 'neutral'}>
            {post.dispatch_status === 'simulated_sent'
              ? `Simulated sent${post.simulated_result ? ` — ${post.simulated_result}` : ''}`
              : 'Queued'}
          </Chip>
        </span>
      )}
    </li>
  );
}

// 6. Geo-targeting view ------------------------------------------------------
// FR-3.9 / INV-6: aggregate-only, no child-keyed targeting. The panel now renders
// the real AGGREGATE region rollup from GET /geo-targeting (per-region lead count
// + share) plus the strategy's named demand metros. Every field is aggregate —
// there is NO per-child / per-minor row by construction (INV-6). The aggregate
// trust badge is kept so the property stays visible.
function GeoTargetingPanel(): JSX.Element {
  const state = useGet<GeoTargeting>('/geo-targeting');
  return (
    <Panel
      title="Geo targeting"
      icon={MapPin}
      testid="geo-targeting-panel"
      badge={
        <span
          className="badge badge-aggregate"
          data-testid="geo-targeting-aggregate-badge"
        >
          <Chip tone="flow">Aggregate-only — no child-keyed targeting</Chip>
        </span>
      }
    >
      <p
        data-testid="geo-targeting-note"
        style={{
          fontSize: 'var(--fs-sm)',
          color: 'var(--ink-soft)',
          margin: 0,
        }}
      >
        Demand is rolled up from aggregate region signals only. Per the threat
        model (INV-6), GT never keys targeting to an individual minor and never
        scrapes minors; only aggregate, de-identified geo data is used.
      </p>
      {state.status === 'loading' && (
        <p data-testid="geo-targeting-loading" className="lab">
          Loading geo demand…
        </p>
      )}
      {state.status === 'error' && (
        <p data-testid="geo-targeting-error" role="alert" style={errStyle}>
          Could not load geo demand: {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <>
          {(state.data.regions ?? []).length === 0 ? (
            <p data-testid="geo-targeting-empty" style={emptyStyle}>
              No aggregate region demand yet.
            </p>
          ) : (
            <ul className="geo-region-list" style={listStyle}>
              {(state.data.regions ?? []).map((r) => (
                <li
                  key={r.region}
                  className="geo-region-row"
                  data-testid={`geo-region-${r.region}`}
                  style={rowStyle}
                >
                  <span
                    className="geo-region-name"
                    style={{
                      flex: 1,
                      minWidth: 140,
                      fontSize: 'var(--fs-body)',
                      fontWeight: 600,
                    }}
                  >
                    {r.region}
                  </span>
                  <span
                    className="mono"
                    data-testid={`geo-region-count-${r.region}`}
                    style={{ fontSize: 'var(--fs-sm)', color: 'var(--flow)' }}
                  >
                    {r.lead_count} leads
                  </span>
                  <span
                    className="mono"
                    style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
                  >
                    {pct(r.share)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {/* The strategy's named demand metros (aggregate metro labels, INV-6). */}
          <div
            className="geo-demand-metros"
            data-testid="geo-demand-metros"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 'var(--s-2)',
              alignItems: 'center',
            }}
          >
            <span className="lab" style={{ color: 'var(--muted)' }}>
              Demand metros:
            </span>
            {(state.data.demand_metros ?? []).map((m) => (
              <span
                key={`${m.metro}-${m.state}`}
                data-testid={`geo-metro-${m.metro}`}
              >
                <Chip tone="neutral">
                  {m.metro}, {m.state}
                </Chip>
              </span>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

// Recipe runner (INV-7) ------------------------------------------------------
// Lists recipes from GET /recipes and RENDERS the Tom Babb attribution for each
// (§8.5, INV-7 — authorship is surfaced in the UI, never stripped). "Run" POSTs
// the recipe (simulated); the attribution stays visible regardless.
function RecipeRunner(): JSX.Element {
  const state = useGet<Recipe[]>('/recipes');
  const [ran, setRan] = useState<Record<string, boolean>>({});

  function run(id: string): void {
    apiFetch(`/recipes/${id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`recipe run failed: ${res.status}`);
        return res.json();
      })
      .then(() => setRan((prev) => ({ ...prev, [id]: true })))
      .catch(() => {
        // A failed run leaves the recipe runnable for a retry.
      });
  }

  return (
    <Panel title="Recipe runner" icon={FileText} testid="recipe-runner">
      {state.status === 'loading' && (
        <p data-testid="recipe-loading" className="lab">
          Loading recipes…
        </p>
      )}
      {state.status === 'error' && (
        <p data-testid="recipe-error" role="alert" style={errStyle}>
          Could not load recipes: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.data.length === 0 ? (
          <p data-testid="recipe-empty" style={emptyStyle}>
            No recipes yet.
          </p>
        ) : (
          <ul className="recipe-list" style={listStyle}>
            {state.data.map((recipe) => (
              <li
                key={recipe.id}
                className="recipe-row"
                data-testid={`recipe-${recipe.id}`}
                style={{ ...rowStyle, alignItems: 'flex-start' }}
              >
                <div style={{ flex: 1, minWidth: 200 }}>
                  <div
                    className="recipe-name"
                    style={{ fontSize: 'var(--fs-body)', fontWeight: 600 }}
                  >
                    {recipe.name}
                  </div>
                  <div
                    className="recipe-description"
                    style={{
                      fontSize: 'var(--fs-sm)',
                      color: 'var(--muted)',
                      marginTop: 2,
                    }}
                  >
                    {recipe.description}
                  </div>
                  {/* INV-7: Tom Babb's marketing skills are attributed in the
                      UI, never claimed as the builder's authorship. */}
                  <div
                    className="recipe-attribution"
                    data-testid={`recipe-attribution-${recipe.id}`}
                    style={{
                      fontSize: 'var(--fs-sm)',
                      color: 'var(--flow-ink)',
                      marginTop: 'var(--s-2)',
                      fontStyle: 'italic',
                    }}
                  >
                    Marketing skills by {recipe.attribution}
                  </div>
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--s-2)',
                  }}
                >
                  <Button
                    icon={Play}
                    data-testid={`recipe-run-${recipe.id}`}
                    onClick={() => run(recipe.id)}
                  >
                    Run recipe
                  </Button>
                  {ran[recipe.id] && (
                    <span
                      role="status"
                      data-testid={`recipe-ran-${recipe.id}`}
                    >
                      <Chip tone="flow">Recipe run (simulated)</Chip>
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ))}
    </Panel>
  );
}
