import { useEffect, useState } from 'react';
import { apiBaseUrl } from '../config';

// GEO board (FR-3.7 / INV-3 fail-closed).
//
// GT starts from a 0% baseline in AI-engine answer coverage (how often the ICP
// prompt set surfaces GT in a generative engine's answer). This board surfaces:
//   - the 0% BASELINE explicitly (GT measures coverage VS that baseline),
//   - COVERAGE (coverage_mean) as a percentage,
//   - the LIFT TREND (coverage − baseline), signed, vs the baseline,
//   - VARIANCE / CI — a ± confidence band derived from variance; when there are
//     too few samples we widen the CI and refuse a point estimate (RESEARCH Q5
//     "Don't Measure Once" — never assert a point estimate on too few samples).
//
// The GEO eval gate is enforced VISUALLY and fail-closed (INV-3): when the eval
// is RED (enabled:false) the generate-to-win / Run-sampling action is DISABLED
// and a red notice explains why — a red eval disables the action in the UI,
// fail closed. When the eval is GREEN the action is available; clicking it runs
// a repeated-sampling pass (POST /geo/sample) and re-renders the fresh result.
//
// Native fetch only (≤2 runtime deps). Read/propose only (INV-2) — this UI does
// not own the GEO measurement; it renders the server's result and requests a
// re-sample. A null/empty engine renders as a dash placeholder, never "null".

// GET /geo and POST /geo/sample response (matches the backend GEO endpoints).
interface GeoTrackingView {
  coverage_mean: number; // 0.0..1.0
  baseline: number; // 0.0 — the 0% baseline GT starts from
  lift: number; // coverage_mean - baseline
  variance: number;
  sample_count: number;
  insufficient_samples: boolean;
  enabled: boolean; // false ⇒ GEO eval is RED ⇒ disable generate-to-win
  prompt_set: string[];
  engine: string;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: GeoTrackingView };

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

export default function GeoBoard(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  // Tracks an in-flight re-sampling run so the control can show progress and
  // not double-fire.
  const [sampling, setSampling] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/geo`)
      .then((res) => {
        if (!res.ok) throw new Error(`geo request failed: ${res.status}`);
        return res.json() as Promise<GeoTrackingView>;
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

  function runSampling(): void {
    setSampling(true);
    fetch(`${apiBaseUrl}/geo/sample`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`geo sample failed: ${res.status}`);
        return res.json() as Promise<GeoTrackingView>;
      })
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      })
      .finally(() => setSampling(false));
  }

  if (state.status === 'loading') {
    return <p data-testid="geo-loading">Loading GEO board…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="geo-error" role="alert">
        Could not load GEO board: {state.message}
      </p>
    );
  }

  const geo = state.data;
  const evalGreen = geo.enabled;
  // A simple ± confidence band: standard deviation (√variance) as a percent.
  // On too few samples we never assert a point estimate — the CI is widened.
  const ci = pct(Math.sqrt(Math.max(geo.variance, 0)));

  return (
    <section aria-label="GEO board" data-testid="geo-board">
      <h2>GEO — generative engine coverage</h2>

      <dl className="geo-fields">
        <dt>Baseline</dt>
        <dd data-testid="geo-baseline">{pct(geo.baseline)}</dd>

        <dt>Coverage (vs 0% baseline)</dt>
        <dd data-testid="geo-coverage">{pct(geo.coverage_mean)}</dd>

        <dt>Lift trend</dt>
        <dd data-testid="geo-lift">{signedPct(geo.lift)}</dd>

        <dt>Variance (± CI)</dt>
        <dd data-testid="geo-variance">
          {geo.variance.toFixed(4)} (±{ci})
        </dd>

        <dt>Samples</dt>
        <dd data-testid="geo-sample-count">{geo.sample_count}</dd>

        <dt>Engine</dt>
        <dd data-testid="geo-engine">{geo.engine || PLACEHOLDER}</dd>
      </dl>

      {geo.insufficient_samples && (
        <p data-testid="geo-insufficient" role="status">
          <strong>Insufficient samples</strong> — the CI is widened; this is not
          a point estimate. Run more sampling before trusting the coverage
          figure.
        </p>
      )}

      {geo.prompt_set.length > 0 && (
        <ul className="geo-prompt-set" data-testid="geo-prompt-set">
          {geo.prompt_set.map((prompt) => (
            <li key={prompt}>{prompt}</li>
          ))}
        </ul>
      )}

      {evalGreen ? (
        <button
          type="button"
          data-testid="geo-run-sampling"
          onClick={runSampling}
          disabled={sampling}
        >
          {sampling ? 'Running sampling…' : 'Run sampling (generate-to-win)'}
        </button>
      ) : (
        // INV-3 fail closed: a red GEO eval disables the generate-to-win action.
        // There is deliberately NO actionable control here — only the blocked
        // notice explaining why.
        <p data-testid="geo-eval-blocked" role="alert">
          The GEO eval is <strong>red</strong> — the generate-to-win action is
          disabled until the eval passes. Coverage cannot be acted on while the
          eval gate is failing (fail closed).
        </p>
      )}
    </section>
  );
}
