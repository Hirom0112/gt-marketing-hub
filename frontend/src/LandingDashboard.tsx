import { useEffect, useState } from 'react';
import { Link2, Users } from 'lucide-react';
import { apiFetch } from './config';
import { Card } from './ui';

// Read-only landing dashboard (FR-2.1/2.6). Renders the four per-stage pipeline
// counts + a CRM-seam summary from GET /pipeline, using the native fetch API
// (no new runtime dependency — stays within the ≤12-dep budget). Read-only:
// it never mutates state and issues a single GET. S8 Wave 2 re-skin: the counts
// become a KPI strip; the seam summary becomes a compact tinted ledger so the
// off-baseline statuses (unsynced / conflict) read as attention surfaces.

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

// CRM-seam statuses (§4.7) with display labels + semantic tone. A synced row is
// healthy (flow); unsynced/conflict are the seam that needs work (signal).
const SEAM_STATUSES: ReadonlyArray<
  readonly [key: string, label: string, accent: string]
> = [
  ['synced', 'Synced', 'var(--flow)'],
  ['unsynced', 'Unsynced', 'var(--signal)'],
  ['conflict', 'Conflict', 'var(--signal)'],
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
    apiFetch(`/pipeline`)
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
      <p data-testid="pipeline-loading" className="lab">
        Loading pipeline…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="pipeline-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load pipeline: {state.message}
      </p>
    );
  }

  const { counts, total, seam } = state.data;

  return (
    <section aria-label="Pipeline overview" data-testid="landing-dashboard">
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 'var(--s-2)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <h2
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            fontSize: 'var(--fs-md)',
            fontWeight: 700,
            margin: 0,
          }}
        >
          <Users size={15} aria-hidden /> Pipeline
        </h2>
        <span className="mono" style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>
          <span data-testid="pipeline-total">{total} families</span> open
        </span>
      </div>

      <ul
        className="pipeline-stages"
        style={{
          listStyle: 'none',
          margin: '0 0 var(--s-4)',
          padding: 0,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 'var(--s-3)',
        }}
      >
        {STAGES.map(([key, label]) => (
          <li key={key} data-testid={`pipeline-stage-${key}`}>
            <Card>
              <div className="stage-label lab">{label}</div>
              <div
                className="stage-count mono"
                data-testid="stage-count"
                style={{
                  fontSize: 'var(--fs-stat)',
                  fontWeight: 600,
                  lineHeight: 1.1,
                  marginTop: 'var(--s-1)',
                  color: 'var(--ink)',
                }}
              >
                {counts[key] ?? 0}
              </div>
            </Card>
          </li>
        ))}
      </ul>

      <div
        className="lab"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <Link2 size={11} aria-hidden /> CRM seam
      </div>
      <Card pad={false}>
        <ul
          className="seam-summary"
          data-testid="seam-summary"
          style={{ listStyle: 'none', margin: 0, padding: 0 }}
        >
          {SEAM_STATUSES.map(([key, label, accent], i) => (
            <li
              key={key}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: 'var(--s-3) var(--s-4)',
                borderTop: i ? '1px solid var(--line)' : 'none',
              }}
            >
              <span
                className="seam-label"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 'var(--s-2)',
                  fontSize: 'var(--fs-sm)',
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: 'var(--r-pill)',
                    background: accent,
                  }}
                />
                {label}
              </span>
              <span
                className="seam-count mono"
                data-testid={`seam-${key}`}
                style={{
                  fontSize: 'var(--fs-md)',
                  fontWeight: 600,
                  color: accent,
                }}
              >
                {seam[key] ?? 0}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </section>
  );
}
