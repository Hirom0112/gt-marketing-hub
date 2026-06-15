import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

// Pipeline board (FR-2.1). Renders the four funnel columns — Interest / Apply /
// Enroll / Tuition — each with its per-stage count from GET /pipeline, using the
// native fetch API (no new runtime dependency — stays within the ≤12-dep
// budget). Read-only: issues a single GET and never mutates state (INV-2).

// Shape of the FastAPI PipelineResponse (backend app/api/schemas.py).
interface PipelineResponse {
  counts: Record<string, number>;
  total: number;
  seam: Record<string, number>;
}

// Funnel stages in funnel order (§4.8 Stage) with display labels.
const COLUMNS: ReadonlyArray<readonly [key: string, label: string]> = [
  ['interest', 'Interest'],
  ['apply', 'Apply'],
  ['enroll', 'Enroll'],
  ['tuition', 'Tuition'],
];

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: PipelineResponse };

export default function PipelineBoard(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl}/pipeline`)
      .then((res) => {
        if (!res.ok) throw new Error(`pipeline request failed: ${res.status}`);
        return res.json() as Promise<PipelineResponse>;
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
    return <p data-testid="pipeline-board-loading">Loading pipeline…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="pipeline-board-error" role="alert">
        Could not load pipeline: {state.message}
      </p>
    );
  }

  const { counts } = state.data;

  return (
    <section aria-label="Pipeline board" data-testid="pipeline-board">
      <h2>Pipeline board</h2>
      <ol className="pipeline-board-columns">
        {COLUMNS.map(([key, label]) => (
          <li
            key={key}
            className="pipeline-board-column"
            data-testid={`pipeline-column-${key}`}
          >
            <span className="column-label">{label}</span>
            <span className="column-count" data-testid="column-count">
              {counts[key] ?? 0}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
