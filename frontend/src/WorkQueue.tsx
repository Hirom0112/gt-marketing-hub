import { useEffect, useState } from 'react';
import { ListOrdered } from 'lucide-react';
import { apiBaseUrl } from './config';
import { Card, Chip } from './ui';

// Work queue (FR-2.5). Fetches GET /work-queue — a list the server has already
// ranked by score desc — and renders the families IN THE ORDER RECEIVED. The
// server owns the ranking (the deterministic core, ARCHITECTURE §8); this UI is
// purely presentational and does NOT re-sort. Each row surfaces the family's
// value and recoverability. Native fetch only (≤12-dep budget). Read-only
// (INV-2). S8 Wave 2 re-skin: a ranked card list with a rank index, mono metrics,
// and a stage chip.

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
    return (
      <p data-testid="work-queue-loading" className="lab">
        Loading work queue…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="work-queue-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load work queue: {state.message}
      </p>
    );
  }

  return (
    <section aria-label="Work queue" data-testid="work-queue">
      <div
        className="lab"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <ListOrdered size={11} aria-hidden /> Work queue — ranked by recovery
        value
      </div>
      <h2 style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
        Work queue
      </h2>
      <Card pad={false}>
        <ol
          className="work-queue-list"
          style={{ listStyle: 'none', margin: 0, padding: 0 }}
        >
          {state.items.map((item, i) => (
            <li
              key={item.family_id}
              className="work-queue-row"
              data-testid="work-queue-row"
              style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}
            >
              <div
                data-testid={`work-queue-row-${item.family_id}`}
                className="work-queue-row-inner"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--s-3)',
                  padding: 'var(--s-3) var(--s-4)',
                }}
              >
                <span
                  className="mono"
                  aria-hidden
                  style={{
                    fontSize: 'var(--fs-sm)',
                    fontWeight: 600,
                    color: 'var(--muted)',
                    width: 18,
                    textAlign: 'right',
                  }}
                >
                  {i + 1}
                </span>
                <span
                  className="row-name"
                  style={{
                    flex: 1,
                    fontSize: 'var(--fs-body)',
                    fontWeight: 600,
                    minWidth: 0,
                  }}
                >
                  {item.display_name}
                </span>
                <span className="row-stage">
                  <Chip>{item.current_stage}</Chip>
                </span>
                <span
                  className="row-value mono"
                  data-testid="row-value"
                  title="Recovery value"
                  style={{
                    fontSize: 'var(--fs-sm)',
                    fontWeight: 600,
                    color: 'var(--gate)',
                    minWidth: 56,
                    textAlign: 'right',
                  }}
                >
                  {item.value}
                </span>
                <span
                  className="row-recoverability mono"
                  data-testid="row-recoverability"
                  title="Recoverability"
                  style={{
                    fontSize: 'var(--fs-sm)',
                    color: 'var(--flow)',
                    minWidth: 40,
                    textAlign: 'right',
                  }}
                >
                  {item.recoverability}
                </span>
              </div>
            </li>
          ))}
        </ol>
      </Card>
    </section>
  );
}
