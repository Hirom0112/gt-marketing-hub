import { useEffect, useState } from 'react';
import {
  CircleCheck,
  CircleX,
  Play,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';
import { apiFetch } from './config';
import { Button, Card, Chip } from './ui';

// Consolidated eval-suite gate (FR-4.5 / INV-3 fail-closed).
//
// One eval scoreboard fronts every AI action. This component fetches the
// consolidated suite (GET /evals), renders each eval row (name, score,
// threshold, pass/fail), and enforces the gate VISUALLY and fail-closed: when
// the gating eval (`message_safety_grounding`) is RED — flagged in `disabled`
// — the representative gated AI action is DISABLED and a red notice explains
// why. A red eval disables the action in the UI; fail closed. When green the
// action is enabled. A "Run eval suite" control POSTs /evals/run and re-renders
// with the fresh scoreboard (mirrors GeoBoard's runSampling).
//
// Native fetch only (≤2 runtime deps). Read/propose only (INV-2) — this UI does
// not own the evals; it renders the server's result and requests a re-run.
//
// S8 Wave 2 re-skin: re-styled onto the shared token/primitive design system.
// Semantic tones: `flow` for passing, `signal` for red/blocked. No raw hex
// (INV-11). The fail-closed posture (red eval → disabled action + red notice)
// stays visible and intact.

// The eval whose red state fails-closed the representative gated AI action.
const GATING_EVAL = 'message_safety_grounding';

// One eval row in the consolidated scoreboard (matches the backend contract).
interface EvalRow {
  eval_name: string;
  score: number;
  threshold: number;
  passed: boolean;
}

// GET /evals and POST /evals/run response (the consolidated scoreboard).
interface EvalScoreboard {
  rows: EvalRow[];
  overall_green: boolean;
  disabled: { [evalName: string]: boolean };
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: EvalScoreboard };

const cellStyle = {
  padding: '8px 12px',
  borderBottom: '1px solid var(--line)',
  textAlign: 'left' as const,
};

const headStyle = {
  padding: '8px 12px',
  borderBottom: '1px solid var(--line-strong)',
  textAlign: 'left' as const,
  color: 'var(--muted)',
};

export default function EvalGate(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  // Tracks an in-flight suite run so the control can show progress and not
  // double-fire.
  const [running, setRunning] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/evals`)
      .then((res) => {
        if (!res.ok) throw new Error(`evals request failed: ${res.status}`);
        return res.json() as Promise<EvalScoreboard>;
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

  function runSuite(): void {
    setRunning(true);
    apiFetch(`/evals/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`evals run failed: ${res.status}`);
        return res.json() as Promise<EvalScoreboard>;
      })
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      })
      .finally(() => setRunning(false));
  }

  if (state.status === 'loading') {
    return (
      <p data-testid="eval-gate-loading" className="mono" style={{ color: 'var(--muted)' }}>
        Loading eval suite…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <Card>
        <p
          data-testid="eval-gate-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', margin: 0 }}
        >
          Could not load eval suite: {state.message}
        </p>
      </Card>
    );
  }

  const board = state.data;
  // Fail-closed: the gating eval is red when `disabled` flags it true.
  const gatingRed = board.disabled[GATING_EVAL] === true;

  return (
    <section aria-label="Eval gate" data-testid="eval-gate">
      <Card style={{ display: 'grid', gap: 'var(--s-4)' }}>
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
            <div className="lab">FR-4.5 · INV-3 fail-closed</div>
            <h2
              style={{
                fontSize: 'var(--fs-md)',
                fontWeight: 600,
                letterSpacing: '-0.01em',
                marginTop: 2,
              }}
            >
              Eval suite — fail-closed gate
            </h2>
          </div>
          <Chip tone={board.overall_green ? 'flow' : 'signal'}>
            suite {board.overall_green ? 'green' : 'red'}
          </Chip>
        </div>

        <div
          className="scroll"
          style={{
            overflowX: 'auto',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
          }}
        >
          <table
            className="eval-rows"
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <thead>
              <tr className="lab">
                <th scope="col" style={headStyle}>
                  Eval
                </th>
                <th scope="col" style={{ ...headStyle, textAlign: 'right' }}>
                  Score
                </th>
                <th scope="col" style={{ ...headStyle, textAlign: 'right' }}>
                  Threshold
                </th>
                <th scope="col" style={{ ...headStyle, textAlign: 'right' }}>
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {board.rows.map((row) => {
                const StatusIcon = row.passed ? CircleCheck : CircleX;
                return (
                  <tr key={row.eval_name} data-testid={`eval-row-${row.eval_name}`}>
                    <td className="mono" style={{ ...cellStyle, color: 'var(--ink)' }}>
                      {row.eval_name}
                    </td>
                    <td
                      data-testid={`eval-score-${row.eval_name}`}
                      className="mono"
                      style={{ ...cellStyle, textAlign: 'right' }}
                    >
                      {row.score.toFixed(2)}
                    </td>
                    <td
                      className="mono"
                      style={{ ...cellStyle, textAlign: 'right', color: 'var(--muted)' }}
                    >
                      {row.threshold.toFixed(2)}
                    </td>
                    <td
                      data-testid={`eval-status-${row.eval_name}`}
                      style={{ ...cellStyle, textAlign: 'right' }}
                    >
                      <span
                        className="mono"
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 'var(--s-1)',
                          color: row.passed
                            ? 'var(--flow-ink)'
                            : 'var(--signal-ink)',
                        }}
                      >
                        <StatusIcon size={14} aria-hidden />
                        {row.passed ? 'pass' : 'fail'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {gatingRed && (
          // INV-3 fail closed: a red gating eval disables the AI action; this red
          // notice explains why the action cannot be run.
          <p
            data-testid="eval-blocked"
            role="alert"
            style={{
              display: 'flex',
              gap: 'var(--s-2)',
              alignItems: 'flex-start',
              margin: 0,
              padding: 'var(--s-3)',
              borderRadius: 'var(--r-md)',
              background: 'var(--signal-wash)',
              border: '1px solid var(--signal)',
              color: 'var(--signal-ink)',
              fontSize: 'var(--fs-sm)',
              lineHeight: 'var(--lh-body)',
            }}
          >
            <ShieldAlert size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />
            <span>
              The <strong>{GATING_EVAL}</strong> eval is <strong>red</strong> —
              the AI draft action is disabled until the eval passes. Fail closed:
              a red eval disables the action in the UI.
            </span>
          </p>
        )}

        <div
          className="eval-gate-controls"
          style={{ display: 'flex', gap: 'var(--s-2)', flexWrap: 'wrap' }}
        >
          <Button
            data-testid="eval-gate-action"
            variant="primary"
            icon={Sparkles}
            disabled={gatingRed}
          >
            Run AI draft
          </Button>
          <Button
            data-testid="eval-run"
            icon={Play}
            onClick={runSuite}
            disabled={running}
          >
            {running ? 'Running eval suite…' : 'Run eval suite'}
          </Button>
        </div>
      </Card>
    </section>
  );
}
