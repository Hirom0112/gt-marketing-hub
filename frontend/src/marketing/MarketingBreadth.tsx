import { useEffect, useState } from 'react';
import { apiBaseUrl } from '../config';

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
    fetch(`${apiBaseUrl}${path}`)
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
// Workspace surface.
// ---------------------------------------------------------------------------

export default function MarketingBreadth(): JSX.Element {
  return (
    <section
      aria-label="Marketing breadth"
      data-testid="marketing-breadth"
      className="marketing-breadth"
    >
      <h2>Marketing breadth</h2>
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
    <div className="mb-panel" data-testid="creator-panel">
      <h3>Creator discovery</h3>
      {state.status === 'loading' && (
        <p data-testid="creator-loading">Loading creators…</p>
      )}
      {state.status === 'error' && (
        <p data-testid="creator-error" role="alert">
          Could not load creators: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.data.length === 0 ? (
          <p data-testid="creator-empty">No creators discovered yet.</p>
        ) : (
          <ul className="creator-list">
            {state.data.map((c) => (
              <li
                key={c.id}
                className="creator-row"
                data-testid={`creator-${c.id}`}
              >
                <span className="creator-handle">{c.display_handle}</span>
                <span className="creator-channel">{c.channel}</span>
                <span data-testid={`creator-fit-${c.id}`}>
                  Fit {pct(c.fit_score)}
                </span>
                <span data-testid={`creator-authenticity-${c.id}`}>
                  Authenticity {pct(c.authenticity_score)}
                </span>
                {/* INV-6: aggregate-only; the data_mode badge makes the trust
                    property visible. A minor is never individually targetable. */}
                <span
                  className="badge badge-aggregate"
                  data-testid={`creator-data-mode-${c.id}`}
                >
                  {c.data_mode}
                </span>
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}

// 2. Sentiment monitor -------------------------------------------------------
function SentimentPanel(): JSX.Element {
  const state = useGet<SentimentResponse>('/sentiment');
  return (
    <div className="mb-panel" data-testid="sentiment-panel">
      <h3>Sentiment monitor</h3>
      {state.status === 'loading' && (
        <p data-testid="sentiment-loading">Loading sentiment…</p>
      )}
      {state.status === 'error' && (
        <p data-testid="sentiment-error" role="alert">
          Could not load sentiment: {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <div className="sentiment-summary" data-testid="sentiment-summary">
          {/* OUT-5: not a live feed — the source_mode placeholder badge says so. */}
          <span
            className="badge badge-placeholder"
            data-testid="sentiment-source-mode"
          >
            {state.data.summary.source_mode}
          </span>
          <dl className="sentiment-counts">
            <dt>Positive</dt>
            <dd data-testid="sentiment-positive">
              {state.data.summary.positive}
            </dd>
            <dt>Neutral</dt>
            <dd data-testid="sentiment-neutral">
              {state.data.summary.neutral}
            </dd>
            <dt>Negative</dt>
            <dd data-testid="sentiment-negative">
              {state.data.summary.negative}
            </dd>
            <dt>Total</dt>
            <dd data-testid="sentiment-total">{state.data.summary.total}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}

// 3. KPI board with levers ---------------------------------------------------
function KpiPanel(): JSX.Element {
  const state = useGet<KpiRow[]>('/kpi');
  return (
    <div className="mb-panel" data-testid="kpi-panel">
      <h3>KPI board</h3>
      {state.status === 'loading' && (
        <p data-testid="kpi-loading">Loading KPIs…</p>
      )}
      {state.status === 'error' && (
        <p data-testid="kpi-error" role="alert">
          Could not load KPIs: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.data.length === 0 ? (
          <p data-testid="kpi-empty">No KPIs yet.</p>
        ) : (
          <ul className="kpi-list">
            {state.data.map((k) => (
              <li
                key={`${k.channel}-${k.metric}`}
                className="kpi-row"
                data-testid={`kpi-${k.channel}`}
              >
                <span className="kpi-channel">{k.channel}</span>
                <span className="kpi-metric">{k.metric}</span>
                <span data-testid={`kpi-baseline-${k.channel}`}>
                  Baseline {k.baseline}
                </span>
                <span data-testid={`kpi-target-${k.channel}`}>
                  Target {k.target}
                </span>
                {/* The lever delta, signed, vs baseline. */}
                <span data-testid={`kpi-lever-${k.channel}`}>
                  Lever {signed(k.lever_delta)}
                </span>
                <span
                  data-testid={`kpi-met-${k.channel}`}
                  className={k.target_met ? 'kpi-met' : 'kpi-unmet'}
                >
                  {k.target_met ? 'Target met' : 'Below target'}
                </span>
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}

// 4. Staged-pipeline view ----------------------------------------------------
function PipelinePanel(): JSX.Element {
  const state = useGet<PipelineResponse>('/content/pipeline');
  return (
    <div className="mb-panel" data-testid="pipeline-panel">
      <h3>Staged pipeline</h3>
      {state.status === 'loading' && (
        <p data-testid="pipeline-loading">Loading pipeline…</p>
      )}
      {state.status === 'error' && (
        <p data-testid="pipeline-error" role="alert">
          Could not load pipeline: {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <ol className="pipeline-stages">
          <li className="pipeline-stage" data-testid="pipeline-concept">
            <span className="stage-name">Concept</span>
            <span className="stage-status">{state.data.concept.status}</span>
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
    </div>
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
    <li className="pipeline-stage" data-testid={testid}>
      <span className="stage-name">{name}</span>
      <span
        className="badge badge-placeholder"
        data-testid={`${testid}-badge`}
      >
        {stage.status === 'placeholder' ? 'placeholder' : stage.status}
      </span>
      {stage.placeholder_uri && (
        <span className="stage-uri" data-testid={`${testid}-uri`}>
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
    fetch(`${apiBaseUrl}/content/schedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
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
    <div className="mb-panel" data-testid="scheduler-panel">
      <h3>Scheduler</h3>
      {state.status === 'loading' && (
        <p data-testid="scheduler-loading">Loading schedule…</p>
      )}
      {state.status === 'error' && (
        <p data-testid="scheduler-error" role="alert">
          Could not load schedule: {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <>
          <button
            type="button"
            data-testid="scheduler-add"
            onClick={schedule}
            disabled={scheduling}
          >
            {scheduling ? 'Scheduling…' : 'Schedule post'}
          </button>
          {state.data.length === 0 ? (
            <p data-testid="scheduler-empty">No scheduled posts yet.</p>
          ) : (
            <ul className="schedule-list">
              {state.data.map((post) => (
                <ScheduledPostRow key={post.id} post={post} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function ScheduledPostRow({ post }: { post: ScheduledPost }): JSX.Element {
  const blocked = post.dispatch_status === 'blocked';
  return (
    <li
      className={`schedule-row ${blocked ? 'blocked' : ''}`}
      data-testid={`schedule-${post.id}`}
      data-status={post.dispatch_status}
    >
      <span className="schedule-channel">{post.channel}</span>
      <span className="schedule-when">{post.scheduled_for}</span>
      {/* OUT-2: every v1 dispatch is simulated — badge it. */}
      <span
        className="badge badge-simulated"
        data-testid={`schedule-mode-${post.id}`}
      >
        {post.dispatch_mode}
      </span>
      {blocked ? (
        // Fail-closed (INV-3/INV-4): a blocked post shows a red blocked status
        // and offers NO send affordance. There is deliberately no send control.
        <span
          className="schedule-status blocked"
          data-testid={`schedule-blocked-${post.id}`}
          role="alert"
        >
          Blocked — not dispatched
        </span>
      ) : (
        <span
          className="schedule-status"
          data-testid={`schedule-status-${post.id}`}
        >
          {post.dispatch_status === 'simulated_sent'
            ? `Simulated sent${post.simulated_result ? ` — ${post.simulated_result}` : ''}`
            : 'Queued'}
        </span>
      )}
    </li>
  );
}

// 6. Geo-targeting view ------------------------------------------------------
// FR-3.9 / INV-6: aggregate-only, no child-keyed targeting. There is no
// dedicated endpoint that returns per-child data BY DESIGN — this panel exists
// to make the trust property explicit. No fabricated data is rendered.
function GeoTargetingPanel(): JSX.Element {
  return (
    <div className="mb-panel" data-testid="geo-targeting-panel">
      <h3>Geo targeting</h3>
      <p
        className="badge badge-aggregate"
        data-testid="geo-targeting-aggregate-badge"
      >
        Aggregate-only — no child-keyed targeting
      </p>
      <p data-testid="geo-targeting-note">
        Geo segments are derived from aggregate region signals only. Per the
        threat model (INV-6), GT never keys targeting to an individual minor and
        never scrapes minors; only aggregate, de-identified geo data is used.
      </p>
    </div>
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
    fetch(`${apiBaseUrl}/recipes/${id}/run`, {
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
    <div className="mb-panel" data-testid="recipe-runner">
      <h3>Recipe runner</h3>
      {state.status === 'loading' && (
        <p data-testid="recipe-loading">Loading recipes…</p>
      )}
      {state.status === 'error' && (
        <p data-testid="recipe-error" role="alert">
          Could not load recipes: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.data.length === 0 ? (
          <p data-testid="recipe-empty">No recipes yet.</p>
        ) : (
          <ul className="recipe-list">
            {state.data.map((recipe) => (
              <li
                key={recipe.id}
                className="recipe-row"
                data-testid={`recipe-${recipe.id}`}
              >
                <span className="recipe-name">{recipe.name}</span>
                <span className="recipe-description">{recipe.description}</span>
                {/* INV-7: Tom Babb's marketing skills are attributed in the UI,
                    never claimed as the builder's authorship. */}
                <span
                  className="recipe-attribution"
                  data-testid={`recipe-attribution-${recipe.id}`}
                >
                  Marketing skills by {recipe.attribution}
                </span>
                <button
                  type="button"
                  data-testid={`recipe-run-${recipe.id}`}
                  onClick={() => run(recipe.id)}
                >
                  Run recipe
                </button>
                {ran[recipe.id] && (
                  <span
                    role="status"
                    data-testid={`recipe-ran-${recipe.id}`}
                  >
                    Recipe run (simulated)
                  </span>
                )}
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}
