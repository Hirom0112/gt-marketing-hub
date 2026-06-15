import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

// Leadership scoreboard (FR-6.1). A P2-readable view fronting BOTH funnels:
//   - Enrollment: draft proposals → approved / edited / rejected / undecided.
//   - Marketing / GEO: coverage vs the 0% baseline, and the signed lift trend.
//   - Eval status: the overall green/red badge + per-eval pass/fail.
//
// A null geo_coverage renders as a dash placeholder, never "null" — coverage is
// not yet measured (GeoBoard's PLACEHOLDER convention). Native fetch only (≤2
// runtime deps). Read-only (INV-2) — this view renders the server's rollup.

const PLACEHOLDER = '—';

// Format a 0..1 fraction as a whole-percent string (e.g. 0.3 → "30%").
function pct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

// Signed whole-percent (the lift trend reads as a delta vs the baseline).
function signedPct(fraction: number): string {
  const value = Math.round(fraction * 100);
  const sign = value > 0 ? '+' : '';
  return `${sign}${value}%`;
}

// GET /scoreboard response (matches the backend rollup contract).
interface EnrollmentFunnel {
  draft_proposals: number;
  approved: number;
  edited: number;
  rejected: number;
  undecided: number;
}

interface MarketingFunnel {
  geo_coverage: number | null;
  geo_baseline: number;
  geo_lift: number;
}

interface EvalStatus {
  passed: { [evalName: string]: boolean };
  overall_green: boolean;
}

interface ScoreboardView {
  enrollment: EnrollmentFunnel;
  marketing: MarketingFunnel;
  evals: EvalStatus;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: ScoreboardView };

// The enrollment funnel stages, in funnel order, with their human labels.
const ENROLLMENT_STAGES: ReadonlyArray<
  readonly [key: keyof EnrollmentFunnel, label: string]
> = [
  ['draft_proposals', 'Draft proposals'],
  ['approved', 'Approved'],
  ['edited', 'Edited'],
  ['rejected', 'Rejected'],
  ['undecided', 'Undecided'],
];

export default function Scoreboard(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/scoreboard`)
      .then((res) => {
        if (!res.ok) throw new Error(`scoreboard request failed: ${res.status}`);
        return res.json() as Promise<ScoreboardView>;
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
  }, []);

  if (state.status === 'loading') {
    return <p data-testid="scoreboard-loading">Loading scoreboard…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="scoreboard-error" role="alert">
        Could not load scoreboard: {state.message}
      </p>
    );
  }

  const { enrollment, marketing, evals } = state.data;
  const evalNames = Object.keys(evals.passed);

  return (
    <section aria-label="Leadership scoreboard" data-testid="scoreboard">
      <h2>Leadership scoreboard</h2>

      <div className="scoreboard-enrollment">
        <h3>Enrollment funnel</h3>
        <dl>
          {ENROLLMENT_STAGES.map(([key, label]) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd data-testid={`scoreboard-enrollment-${key}`}>
                {enrollment[key]}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="scoreboard-marketing">
        <h3>Marketing — GEO coverage</h3>
        <dl>
          <dt>Coverage (vs 0% baseline)</dt>
          <dd data-testid="scoreboard-geo-coverage">
            {marketing.geo_coverage === null
              ? PLACEHOLDER
              : pct(marketing.geo_coverage)}
          </dd>

          <dt>Baseline</dt>
          <dd data-testid="scoreboard-geo-baseline">
            {pct(marketing.geo_baseline)}
          </dd>

          <dt>Lift trend</dt>
          <dd data-testid="scoreboard-geo-lift">
            {signedPct(marketing.geo_lift)}
          </dd>
        </dl>
      </div>

      <div className="scoreboard-evals">
        <h3>Eval status</h3>
        <p
          data-testid="scoreboard-eval-overall"
          role="status"
          className={evals.overall_green ? 'eval-green' : 'eval-red'}
        >
          Overall: <strong>{evals.overall_green ? 'green' : 'red'}</strong>
        </p>
        {evalNames.length > 0 && (
          <ul className="scoreboard-eval-list">
            {evalNames.map((name) => (
              <li key={name} data-testid={`scoreboard-eval-${name}`}>
                {name}: {evals.passed[name] ? 'pass' : 'fail'}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
