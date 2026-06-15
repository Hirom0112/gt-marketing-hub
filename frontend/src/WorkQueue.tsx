import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

// Work queue (FR-2.5). Fetches GET /work-queue — a list the server has already
// ranked by score desc — and renders the families IN THE ORDER RECEIVED. The
// server owns the ranking (the deterministic core, ARCHITECTURE §8); this UI is
// purely presentational and does NOT re-sort. Each row surfaces the family's
// value and recoverability. Native fetch only (≤12-dep budget). Read-only
// (INV-2).

// One server-ranked work-queue item (backend app/api/schemas.py).
interface WorkQueueItem {
  family_id: string;
  display_name: string;
  current_stage: string;
  score: number;
  recoverability: number;
  value: number;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: WorkQueueItem[] };

export default function WorkQueue(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl}/work-queue`)
      .then((res) => {
        if (!res.ok) throw new Error(`work-queue request failed: ${res.status}`);
        return res.json() as Promise<WorkQueueItem[]>;
      })
      .then((items) => {
        // Render in the server-supplied order — never re-sort client-side.
        if (!cancelled) setState({ status: 'ready', items });
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
    return <p data-testid="work-queue-loading">Loading work queue…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="work-queue-error" role="alert">
        Could not load work queue: {state.message}
      </p>
    );
  }

  return (
    <section aria-label="Work queue" data-testid="work-queue">
      <h2>Work queue</h2>
      <ol className="work-queue-list">
        {state.items.map((item) => (
          <li
            key={item.family_id}
            className="work-queue-row"
            data-testid="work-queue-row"
          >
            <div
              data-testid={`work-queue-row-${item.family_id}`}
              className="work-queue-row-inner"
            >
              <span className="row-name">{item.display_name}</span>
              <span className="row-stage">{item.current_stage}</span>
              <span className="row-value" data-testid="row-value">
                {item.value}
              </span>
              <span
                className="row-recoverability"
                data-testid="row-recoverability"
              >
                {item.recoverability}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
