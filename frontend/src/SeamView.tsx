import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

// Seam-to-zero view (FR-2.7, milestone M-2). Fetches GET /seam — the non-synced
// CRM seam rows (unsynced / conflict) — and renders each with a reconcile button
// plus the live non-synced COUNT. M-2 is "reconcile lowers the non-synced
// count": clicking reconcile POSTs /seam/{id}/reconcile (a simulated adapter in
// v1, INV-9); on a 200 the row is removed optimistically and the count drops.
// Native fetch only (≤2 runtime deps). The deterministic core owns the write
// (INV-2) — this view only records the reconcile request and reflects its result.

// One non-synced seam row (backend GET /seam returns only non-synced rows).
interface SeamRow {
  family_id: string;
  seam_status: 'synced' | 'unsynced' | 'conflict';
}

// POST /seam/{id}/reconcile response.
interface ReconcileResponse {
  family_id: string;
  seam_status: string;
  applied: boolean;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: SeamRow[] };

export default function SeamView(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl}/seam`)
      .then((res) => {
        if (!res.ok) throw new Error(`seam request failed: ${res.status}`);
        return res.json() as Promise<SeamRow[]>;
      })
      .then((rows) => {
        if (!cancelled) setState({ status: 'ready', rows });
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

  function reconcile(familyId: string): void {
    fetch(`${apiBaseUrl}/seam/${familyId}/reconcile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`reconcile request failed: ${res.status}`);
        return res.json() as Promise<ReconcileResponse>;
      })
      .then((result) => {
        // M-2: a reconciled (now-synced) row drops out of the non-synced list,
        // lowering the count. Only remove on a confirmed applied sync.
        if (result.applied && result.seam_status === 'synced') {
          setState((prev) =>
            prev.status === 'ready'
              ? {
                  status: 'ready',
                  rows: prev.rows.filter((r) => r.family_id !== familyId),
                }
              : prev,
          );
        }
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }

  if (state.status === 'loading') {
    return <p data-testid="seam-loading">Loading seam…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="seam-error" role="alert">
        Could not load seam: {state.message}
      </p>
    );
  }

  return (
    <section aria-label="CRM seam" data-testid="seam-view">
      <h2>
        CRM seam — non-synced:{' '}
        <span data-testid="seam-count">{state.rows.length}</span>
      </h2>
      <ul className="seam-list">
        {state.rows.map((row) => (
          <li
            key={row.family_id}
            className="seam-row"
            data-testid="seam-row"
          >
            <div data-testid={`seam-row-${row.family_id}`} className="seam-row-inner">
              <span className="seam-family">{row.family_id}</span>
              <span className="seam-status">{row.seam_status}</span>
              <button
                type="button"
                data-testid={`reconcile-${row.family_id}`}
                onClick={() => reconcile(row.family_id)}
              >
                Reconcile
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
