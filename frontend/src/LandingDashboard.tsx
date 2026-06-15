import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

// Read-only landing dashboard (FR-2.1/2.6). Renders the four per-stage pipeline
// counts + a CRM-seam summary from GET /pipeline, using the native fetch API
// (no new runtime dependency — stays within the ≤12-dep budget). Read-only:
// it never mutates state and issues a single GET.

// Shape of the FastAPI PipelineResponse (backend app/api/schemas.py).
interface PipelineResponse {
  counts: Record<string, number>;
  total: number;
  seam: Record<string, number>;
}

// Funnel stages in funnel order (§4.8 Stage) with display labels.
const STAGES: ReadonlyArray<readonly [key: string, label: string]> = [
  ['interest', 'Interest'],
  ['apply', 'Apply'],
  ['enroll', 'Enroll'],
  ['tuition', 'Tuition'],
];

// CRM-seam statuses (§4.7) with display labels.
const SEAM_STATUSES: ReadonlyArray<readonly [key: string, label: string]> = [
  ['synced', 'Synced'],
  ['unsynced', 'Unsynced'],
  ['conflict', 'Conflict'],
];

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: PipelineResponse };

export default function LandingDashboard(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    // GET only — the landing surface is read-only (INV-2 for S0).
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
    return <p data-testid="pipeline-loading">Loading pipeline…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="pipeline-error" role="alert">
        Could not load pipeline: {state.message}
      </p>
    );
  }

  const { counts, total, seam } = state.data;

  return (
    <section aria-label="Pipeline overview" data-testid="landing-dashboard">
      <h2>Pipeline</h2>
      <p data-testid="pipeline-total">{total} families</p>

      <ul className="pipeline-stages">
        {STAGES.map(([key, label]) => (
          <li key={key} data-testid={`pipeline-stage-${key}`}>
            <span className="stage-label">{label}</span>
            <span className="stage-count" data-testid="stage-count">
              {counts[key] ?? 0}
            </span>
          </li>
        ))}
      </ul>

      <h2>CRM seam</h2>
      <ul className="seam-summary" data-testid="seam-summary">
        {SEAM_STATUSES.map(([key, label]) => (
          <li key={key}>
            <span className="seam-label">{label}</span>
            <span className="seam-count" data-testid={`seam-${key}`}>
              {seam[key] ?? 0}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
