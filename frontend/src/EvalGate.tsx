import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

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

export default function EvalGate(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  // Tracks an in-flight suite run so the control can show progress and not
  // double-fire.
  const [running, setRunning] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/evals`)
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
    fetch(`${apiBaseUrl}/evals/run`, {
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
    return <p data-testid="eval-gate-loading">Loading eval suite…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="eval-gate-error" role="alert">
        Could not load eval suite: {state.message}
      </p>
    );
  }

  const board = state.data;
  // Fail-closed: the gating eval is red when `disabled` flags it true.
  const gatingRed = board.disabled[GATING_EVAL] === true;

  return (
    <section aria-label="Eval gate" data-testid="eval-gate">
      <h2>Eval suite — fail-closed gate</h2>

      <table className="eval-rows">
        <thead>
          <tr>
            <th scope="col">Eval</th>
            <th scope="col">Score</th>
            <th scope="col">Threshold</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {board.rows.map((row) => (
            <tr key={row.eval_name} data-testid={`eval-row-${row.eval_name}`}>
              <td>{row.eval_name}</td>
              <td data-testid={`eval-score-${row.eval_name}`}>
                {row.score.toFixed(2)}
              </td>
              <td>{row.threshold.toFixed(2)}</td>
              <td data-testid={`eval-status-${row.eval_name}`}>
                {row.passed ? 'pass' : 'fail'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {gatingRed && (
        // INV-3 fail closed: a red gating eval disables the AI action; this red
        // notice explains why the action cannot be run.
        <p data-testid="eval-blocked" role="alert">
          The <strong>{GATING_EVAL}</strong> eval is <strong>red</strong> — the
          AI draft action is disabled until the eval passes. Fail closed: a red
          eval disables the action in the UI.
        </p>
      )}

      <div className="eval-gate-controls">
        <button
          type="button"
          data-testid="eval-gate-action"
          disabled={gatingRed}
        >
          Run AI draft
        </button>
        <button
          type="button"
          data-testid="eval-run"
          onClick={runSuite}
          disabled={running}
        >
          {running ? 'Running eval suite…' : 'Run eval suite'}
        </button>
      </div>
    </section>
  );
}
