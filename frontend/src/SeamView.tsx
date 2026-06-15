import { useEffect, useState } from 'react';
import { RefreshCw, Unlink } from 'lucide-react';
import { apiBaseUrl } from './config';
import { Button, Card, Chip } from './ui';

// Seam-to-zero view (FR-2.7, milestone M-2). Fetches GET /seam — the non-synced
// CRM seam rows (unsynced / conflict) — and renders each with a reconcile button
// plus the live non-synced COUNT. M-2 is "reconcile lowers the non-synced
// count": clicking reconcile POSTs /seam/{id}/reconcile (a simulated adapter in
// v1, INV-9); on a 200 the row is removed optimistically and the count drops.
// Native fetch only (≤2 runtime deps). The deterministic core owns the write
// (INV-2) — this view only records the reconcile request and reflects its result.
// S8 Wave 2 re-skin: a hairline-divided ledger of seam rows with a signal-tinted
// non-synced counter and tone-coded status chips.

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
    return (
      <p data-testid="seam-loading" className="lab">
        Loading seam…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="seam-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load seam: {state.message}
      </p>
    );
  }

  const count = state.rows.length;

  return (
    <section aria-label="CRM seam" data-testid="seam-view">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <h2
          className="lab"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            margin: 0,
            fontWeight: 'normal',
          }}
        >
          <Unlink size={11} aria-hidden /> CRM seam — non-synced
        </h2>
        <span
          className="mono"
          data-testid="seam-count"
          style={{
            fontSize: 'var(--fs-sm)',
            fontWeight: 600,
            padding: '1px 8px',
            borderRadius: 'var(--r-xs)',
            color: count ? 'var(--signal-ink)' : 'var(--flow-ink)',
            background: count ? 'var(--signal-wash)' : 'var(--flow-wash)',
            border: `1px solid ${count ? 'var(--signal)' : 'var(--flow)'}`,
          }}
        >
          {count}
        </span>
      </div>

      <Card pad={false}>
        <ul className="seam-list" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {state.rows.map((row, i) => (
            <li
              key={row.family_id}
              className="seam-row"
              data-testid="seam-row"
              style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}
            >
              <div
                data-testid={`seam-row-${row.family_id}`}
                className="seam-row-inner"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--s-3)',
                  padding: 'var(--s-3) var(--s-4)',
                }}
              >
                <span
                  className="seam-family mono"
                  style={{ flex: 1, fontSize: 'var(--fs-sm)', fontWeight: 600, minWidth: 0 }}
                >
                  {row.family_id}
                </span>
                <span className="seam-status">
                  <Chip tone="signal">{row.seam_status}</Chip>
                </span>
                <Button
                  icon={RefreshCw}
                  data-testid={`reconcile-${row.family_id}`}
                  onClick={() => reconcile(row.family_id)}
                >
                  Reconcile
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </section>
  );
}
