import { useEffect, useState } from 'react';
import { Globe, Lock, RefreshCw, Sparkles } from 'lucide-react';
import { apiBaseUrl } from '../config';
import { Button, Card, Chip, Stat } from '../ui';

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
  // GT-vs-competitor citation share (FR-3.7; growth-strategy Bet 3): GT ≈ 3% vs
  // competitors ≈ 50%. The ~3%-vs-~50% leadership view rendered as share bars.
  gt_citation_share?: number; // 0.0..1.0 — GT's own slice of cited domains
  competitor_citation_share?: Record<string, number>; // domain → 0.0..1.0
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

// One citation-share bar: a labelled domain + a fill proportional to its share
// of the cited slots, with the whole-percent figure. The GT bar (tone 'flow')
// reads tiny next to the competitor bars (tone 'signal') — the ~3% vs ~50% gap.
function ShareBar({
  testId,
  label,
  domain,
  share,
  tone,
}: {
  testId: string;
  label: string;
  domain: string;
  share: number;
  tone: 'flow' | 'signal';
}): JSX.Element {
  const width = `${Math.min(100, Math.max(0, share * 100))}%`;
  const fill = tone === 'flow' ? 'var(--flow)' : 'var(--signal)';
  return (
    <div data-testid={testId} data-domain={domain} style={{ display: 'grid', gap: 2 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 'var(--fs-sm)',
        }}
      >
        <span className="mono">{label}</span>
        <span className="mono">{pct(share)}</span>
      </div>
      <div
        aria-hidden
        style={{
          height: 8,
          borderRadius: 'var(--r-sm)',
          background: 'var(--surface-2)',
          overflow: 'hidden',
        }}
      >
        <div style={{ width, height: '100%', background: fill }} />
      </div>
    </div>
  );
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
    return (
      <p data-testid="geo-loading" className="lab">
        Loading GEO board…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <Card style={{ borderColor: 'var(--signal)' }}>
        <p
          data-testid="geo-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', margin: 0 }}
        >
          Could not load GEO board: {state.message}
        </p>
      </Card>
    );
  }

  const geo = state.data;
  const evalGreen = geo.enabled;
  // A simple ± confidence band: standard deviation (√variance) as a percent.
  // On too few samples we never assert a point estimate — the CI is widened.
  const ci = pct(Math.sqrt(Math.max(geo.variance, 0)));
  const liftTone = geo.lift > 0 ? 'flow' : geo.lift < 0 ? 'signal' : 'neutral';

  // GT-vs-competitor citation share (FR-3.7; growth-strategy Bet 3). Competitors
  // sorted high→low so the leader (the ~50% gap GT is measured against) reads
  // first. The bar width is the share as a percent of the slot stream.
  const gtShare = geo.gt_citation_share ?? 0;
  const competitorShare = geo.competitor_citation_share ?? {};
  const competitorRows = Object.entries(competitorShare).sort((a, b) => b[1] - a[1]);
  const hasShare = competitorRows.length > 0 || gtShare > 0;

  return (
    <section
      aria-label="GEO board"
      data-testid="geo-board"
      style={{ display: 'grid', gap: 'var(--s-4)' }}
    >
      <header
        style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}
      >
        <Globe size={16} aria-hidden style={{ color: 'var(--flow)' }} />
        <h2 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, margin: 0 }}>
          GEO — generative engine coverage
        </h2>
        <span style={{ marginLeft: 'auto' }}>
          <Chip tone={evalGreen ? 'flow' : 'signal'}>
            {evalGreen ? 'EVAL GREEN' : 'EVAL RED'}
          </Chip>
        </span>
      </header>

      <p className="lab" style={{ margin: 0 }}>
        Coverage measured against the 0% baseline GT starts from
      </p>

      {/* Metric strip — the headline GEO figures as big mono Stats. */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 'var(--s-3)',
        }}
      >
        <Card>
          <Stat label="Baseline" value={<span data-testid="geo-baseline">{pct(geo.baseline)}</span>} />
        </Card>
        <Card>
          <Stat
            label="Coverage (vs 0% baseline)"
            value={<span data-testid="geo-coverage">{pct(geo.coverage_mean)}</span>}
            tone={geo.coverage_mean > 0 ? 'flow' : 'signal'}
          />
        </Card>
        <Card>
          <Stat
            label="Lift trend"
            value={<span data-testid="geo-lift">{signedPct(geo.lift)}</span>}
            tone={liftTone}
          />
        </Card>
        <Card>
          <Stat
            label="Samples"
            value={<span data-testid="geo-sample-count">{geo.sample_count}</span>}
            note={
              <span data-testid="geo-variance">
                variance {geo.variance.toFixed(4)} (±{ci})
              </span>
            }
          />
        </Card>
      </div>

      {/* GT-vs-competitor citation share — the ~3%-GT vs ~50%-competitor
          leadership view (growth-strategy Bet 3). GT's own bar first, then the
          gifted-school competitors high→low; the gap is the whole point. */}
      {hasShare && (
        <Card>
          <p className="lab" style={{ margin: 0 }}>
            Citation share — who AI-search cites for these prompts
          </p>
          <div
            data-testid="geo-share-bars"
            style={{
              display: 'grid',
              gap: 'var(--s-2)',
              marginTop: 'var(--s-3)',
            }}
          >
            <ShareBar
              testId="geo-share-gt"
              label="GT School"
              domain="gtschool.com"
              share={gtShare}
              tone="flow"
            />
            {competitorRows.map(([domain, share]) => (
              <ShareBar
                key={domain}
                testId="geo-share-competitor"
                label={domain}
                domain={domain}
                share={share}
                tone="signal"
              />
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 'var(--s-2)',
          }}
        >
          <span className="lab">Engine</span>
          <span className="mono" data-testid="geo-engine" style={{ fontSize: 'var(--fs-sm)' }}>
            {geo.engine || PLACEHOLDER}
          </span>
        </div>

        {geo.insufficient_samples && (
          <div
            data-testid="geo-insufficient"
            role="status"
            style={{
              marginTop: 'var(--s-3)',
              padding: 'var(--s-3)',
              borderRadius: 'var(--r-sm)',
              background: 'var(--gate-wash)',
              border: '1px solid var(--gate)',
              color: 'var(--gate-ink)',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <strong>Insufficient samples</strong> — the CI is widened; this is
            not a point estimate. Run more sampling before trusting the coverage
            figure.
          </div>
        )}

        {geo.prompt_set.length > 0 && (
          <>
            <p className="lab" style={{ marginTop: 'var(--s-4)' }}>
              Buyer prompt set
            </p>
            <ul
              className="geo-prompt-set"
              data-testid="geo-prompt-set"
              style={{
                listStyle: 'none',
                margin: 0,
                padding: 0,
                display: 'grid',
                gap: 'var(--s-2)',
                marginTop: 'var(--s-2)',
              }}
            >
              {geo.prompt_set.map((prompt) => (
                <li
                  key={prompt}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--s-2)',
                    padding: '8px 10px',
                    borderRadius: 'var(--r-sm)',
                    background: 'var(--surface-2)',
                    border: '1px solid var(--line)',
                    fontSize: 'var(--fs-body)',
                  }}
                >
                  <Sparkles
                    size={13}
                    aria-hidden
                    style={{ color: 'var(--muted)', flexShrink: 0 }}
                  />
                  <span>{prompt}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>

      {evalGreen ? (
        <div>
          <Button
            variant="primary"
            icon={sampling ? RefreshCw : Sparkles}
            data-testid="geo-run-sampling"
            onClick={runSampling}
            disabled={sampling}
          >
            {sampling ? 'Running sampling…' : 'Run sampling (generate-to-win)'}
          </Button>
        </div>
      ) : (
        // INV-3 fail closed: a red GEO eval disables the generate-to-win action.
        // There is deliberately NO actionable control here — only the blocked
        // notice explaining why.
        <Card
          style={{
            borderColor: 'var(--signal)',
            background: 'var(--signal-wash)',
          }}
        >
          <div
            data-testid="geo-eval-blocked"
            role="alert"
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 'var(--s-2)',
              color: 'var(--signal-ink)',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <Lock size={15} aria-hidden style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              The GEO eval is <strong>red</strong> — the generate-to-win action
              is disabled until the eval passes. Coverage cannot be acted on
              while the eval gate is failing (fail closed).
            </span>
          </div>
        </Card>
      )}
    </section>
  );
}
