import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';
import { Card } from './ui';

// Pipeline board (FR-2.1). Renders the four funnel columns — Interest / Apply /
// Enroll / Tuition — each with its per-stage count from GET /pipeline, using the
// native fetch API (no new runtime dependency — stays within the ≤12-dep
// budget). Read-only: issues a single GET and never mutates state (INV-2).
// S8 Wave 2 re-skin: the four columns sit on the editorial token system, each
// stage tinted by its semantic tone.

// Shape of the FastAPI PipelineResponse (backend app/api/schemas.py).
interface PipelineResponse {
  counts: Record<string, number>;
  total: number;
  seam: Record<string, number>;
}

// Funnel stages in funnel order (§4.8 Stage) with display labels + the semantic
// accent each column reads in. Tuition is the funding gate (gold); Enroll is the
// healthy end of the deterministic funnel (green).
const COLUMNS: ReadonlyArray<
  readonly [key: string, label: string, accent: string]
> = [
  ['interest', 'Interest', 'var(--muted)'],
  ['apply', 'Apply', 'var(--ink-soft)'],
  ['enroll', 'Enroll', 'var(--flow)'],
  ['tuition', 'Tuition', 'var(--gate)'],
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
    return (
      <p data-testid="pipeline-board-loading" className="lab">
        Loading pipeline…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="pipeline-board-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load pipeline: {state.message}
      </p>
    );
  }

  const { counts } = state.data;

  return (
    <section aria-label="Pipeline board" data-testid="pipeline-board">
      <div className="lab" style={{ marginBottom: 'var(--s-2)' }}>
        Pipeline board — the four-stage funnel
      </div>
      <h2
        style={{
          position: 'absolute',
          width: 1,
          height: 1,
          overflow: 'hidden',
          clip: 'rect(0 0 0 0)',
        }}
      >
        Pipeline board
      </h2>
      <ol
        className="pipeline-board-columns scroll"
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 0,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 'var(--s-3)',
        }}
      >
        {COLUMNS.map(([key, label, accent]) => (
          <li
            key={key}
            className="pipeline-board-column"
            data-testid={`pipeline-column-${key}`}
          >
            <Card pad style={{ background: 'var(--paper)' }}>
              <div
                className="mono"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 'var(--s-2)',
                }}
              >
                <span
                  className="column-label lab"
                  style={{ color: accent, letterSpacing: 'var(--tracking-lab)' }}
                >
                  {label}
                </span>
                <span
                  className="column-count mono"
                  data-testid="column-count"
                  style={{
                    fontSize: 'var(--fs-stat)',
                    fontWeight: 600,
                    lineHeight: 1,
                    color: 'var(--ink)',
                  }}
                >
                  {counts[key] ?? 0}
                </span>
              </div>
              <div
                aria-hidden
                style={{
                  height: 3,
                  marginTop: 'var(--s-3)',
                  borderRadius: 'var(--r-pill)',
                  background: accent,
                  opacity: 0.55,
                }}
              />
            </Card>
          </li>
        ))}
      </ol>
    </section>
  );
}
