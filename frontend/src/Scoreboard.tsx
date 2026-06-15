import { useEffect, useState } from 'react';
import {
  CircleCheck,
  CircleX,
  Globe,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { apiBaseUrl } from './config';
import { Card, Chip, type Tone } from './ui';

// Leadership scoreboard (FR-6.1). A P2-readable view fronting BOTH funnels:
//   - Enrollment: draft proposals → approved / edited / rejected / undecided.
//   - Marketing / GEO: coverage vs the 0% baseline, and the signed lift trend.
//   - Eval status: the overall green/red badge + per-eval pass/fail.
//
// A null geo_coverage renders as a dash placeholder, never "null" — coverage is
// not yet measured (GeoBoard's PLACEHOLDER convention). Native fetch only (≤2
// runtime deps). Read-only (INV-2) — this view renders the server's rollup.
//
// S8 Wave 2 re-skin: re-styled onto the shared token/primitive design system
// (Card / Stat / Chip + theme tokens). Semantic tones: `flow` for green/
// healthy/passing, `signal` for red/blocked/off-baseline. No raw hex (INV-11).

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

// The enrollment funnel stages, in funnel order, with their human labels and a
// semantic tone — approved reads `flow` (good), rejected `signal` (attrition),
// the rest stay neutral so the eye lands on the outcomes that matter to P2.
const ENROLLMENT_STAGES: ReadonlyArray<
  readonly [key: keyof EnrollmentFunnel, label: string, tone: Tone]
> = [
  ['draft_proposals', 'Draft proposals', 'neutral'],
  ['approved', 'Approved', 'flow'],
  ['edited', 'Edited', 'neutral'],
  ['rejected', 'Rejected', 'signal'],
  ['undecided', 'Undecided', 'neutral'],
];

// Section heading — mono micro-label over an editorial title, consistent across
// the three scoreboard panels.
function PanelHeading({
  label,
  title,
  trailing,
}: {
  label: string;
  title: string;
  trailing?: JSX.Element;
}): JSX.Element {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
        gap: 'var(--s-3)',
        marginBottom: 'var(--s-3)',
      }}
    >
      <div>
        <div className="lab">{label}</div>
        <h3
          style={{
            fontSize: 'var(--fs-md)',
            fontWeight: 600,
            letterSpacing: '-0.01em',
            marginTop: 2,
          }}
        >
          {title}
        </h3>
      </div>
      {trailing ?? null}
    </div>
  );
}

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
    return (
      <p data-testid="scoreboard-loading" className="mono" style={{ color: 'var(--muted)' }}>
        Loading scoreboard…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <Card>
        <p
          data-testid="scoreboard-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', margin: 0 }}
        >
          Could not load scoreboard: {state.message}
        </p>
      </Card>
    );
  }

  const { enrollment, marketing, evals } = state.data;
  const evalNames = Object.keys(evals.passed);

  const liftValue = signedPct(marketing.geo_lift);
  const liftPositive = marketing.geo_lift > 0;
  const liftNegative = marketing.geo_lift < 0;
  const liftTone: Tone = liftPositive
    ? 'flow'
    : liftNegative
      ? 'signal'
      : 'neutral';
  const LiftIcon = liftNegative ? TrendingDown : TrendingUp;

  return (
    <section
      aria-label="Leadership scoreboard"
      data-testid="scoreboard"
      style={{ display: 'grid', gap: 'var(--s-4)' }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 'var(--s-3)',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <div className="lab">FR-6.1 · leadership rollup</div>
          <h2
            style={{
              fontSize: 'var(--fs-lg)',
              fontWeight: 700,
              letterSpacing: '-0.02em',
              marginTop: 2,
            }}
          >
            Leadership scoreboard
          </h2>
        </div>
        <Chip tone={evals.overall_green ? 'flow' : 'signal'}>
          evals {evals.overall_green ? 'green' : 'red'}
        </Chip>
      </div>

      {/* Enrollment funnel — KPI stat strip. */}
      <Card className="scoreboard-enrollment">
        <PanelHeading label="Enrollment funnel" title="Proposal outcomes" />
        <dl
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
            gap: 'var(--s-4)',
            margin: 0,
          }}
        >
          {ENROLLMENT_STAGES.map(([key, label, tone]) => (
            <div key={key}>
              <dt className="lab">{label}</dt>
              <dd
                data-testid={`scoreboard-enrollment-${key}`}
                className="mono"
                style={{
                  fontSize: 'var(--fs-stat)',
                  fontWeight: 600,
                  lineHeight: 1.1,
                  marginTop: 'var(--s-1)',
                  marginLeft: 0,
                  color: tone === 'neutral' ? 'var(--ink)' : `var(--${tone})`,
                }}
              >
                {enrollment[key]}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      {/* Marketing / GEO — coverage vs baseline + signed lift. */}
      <Card className="scoreboard-marketing">
        <PanelHeading
          label="Marketing · GEO"
          title="Coverage & lift"
          trailing={
            <Chip tone={liftTone}>
              <Globe size={11} aria-hidden style={{ verticalAlign: '-1px', marginRight: 4 }} />
              {liftValue} lift
            </Chip>
          }
        />
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: 'var(--s-4)',
          }}
        >
          <dl style={{ margin: 0 }}>
            <dt className="lab">Coverage (vs 0% baseline)</dt>
            <dd
              data-testid="scoreboard-geo-coverage"
              className="mono"
              style={{
                fontSize: 'var(--fs-stat)',
                fontWeight: 600,
                lineHeight: 1.1,
                marginTop: 'var(--s-1)',
                marginLeft: 0,
                color: 'var(--ink)',
              }}
            >
              {marketing.geo_coverage === null
                ? PLACEHOLDER
                : pct(marketing.geo_coverage)}
            </dd>
          </dl>

          <dl style={{ margin: 0 }}>
            <dt className="lab">Baseline</dt>
            <dd
              data-testid="scoreboard-geo-baseline"
              className="mono"
              style={{
                fontSize: 'var(--fs-md)',
                fontWeight: 600,
                marginTop: 'var(--s-1)',
                marginLeft: 0,
                color: 'var(--muted)',
              }}
            >
              {pct(marketing.geo_baseline)}
            </dd>
          </dl>

          <dl style={{ margin: 0 }}>
            <dt className="lab">Lift trend</dt>
            <dd
              data-testid="scoreboard-geo-lift"
              className="mono"
              style={{
                fontSize: 'var(--fs-stat)',
                fontWeight: 600,
                lineHeight: 1.1,
                marginTop: 'var(--s-1)',
                marginLeft: 0,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 'var(--s-1)',
                color: liftTone === 'neutral' ? 'var(--ink)' : `var(--${liftTone})`,
              }}
            >
              <LiftIcon size={20} aria-hidden />
              {liftValue}
            </dd>
          </dl>
        </div>
      </Card>

      {/* Eval status — overall badge + per-eval pass/fail rows. */}
      <Card className="scoreboard-evals">
        <PanelHeading
          label="Eval status"
          title="Gate health"
          trailing={
            <span
              data-testid="scoreboard-eval-overall"
              role="status"
              className={evals.overall_green ? 'eval-green' : 'eval-red'}
            >
              <Chip tone={evals.overall_green ? 'flow' : 'signal'}>
                Overall: {evals.overall_green ? 'green' : 'red'}
              </Chip>
            </span>
          }
        />
        {evalNames.length > 0 ? (
          <ul
            className="scoreboard-eval-list"
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'grid',
              gap: 'var(--s-1)',
            }}
          >
            {evalNames.map((name) => {
              const passed = evals.passed[name];
              const StatusIcon = passed ? CircleCheck : CircleX;
              return (
                <li
                  key={name}
                  data-testid={`scoreboard-eval-${name}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 'var(--s-3)',
                    padding: '8px 10px',
                    borderRadius: 'var(--r-sm)',
                    background: 'var(--surface-2)',
                    border: '1px solid var(--line)',
                  }}
                >
                  <span
                    className="mono"
                    style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
                  >
                    {name}
                  </span>
                  <span
                    className="mono"
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 'var(--s-1)',
                      fontSize: 'var(--fs-sm)',
                      color: passed ? 'var(--flow-ink)' : 'var(--signal-ink)',
                    }}
                  >
                    <StatusIcon size={14} aria-hidden />
                    {passed ? 'pass' : 'fail'}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="mono" style={{ color: 'var(--muted)', margin: 0, fontSize: 'var(--fs-sm)' }}>
            No evals reported.
          </p>
        )}
      </Card>
    </section>
  );
}
