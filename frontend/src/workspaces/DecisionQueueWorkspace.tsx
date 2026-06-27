import { useCallback, useEffect, useState } from 'react';
import {
  Check,
  ClipboardCheck,
  HelpCircle,
  Lock,
  XCircle,
} from 'lucide-react';
import { apiFetch } from '../config';
import { Button } from '../ui';
import { EmptyState } from '../dashboard/EmptyState';

// Consolidated Decision Queue workspace (TODO_v2 §B2). A leader/admin-only review
// surface: it reads the leader-gated GET /decisions (an OPEN-only queue) and lets a
// leader approve / reject / need-info each item. Every verdict POSTs to
// /decisions/{id}/action and refreshes the list. The card layout REUSES the
// enrollment review-card (ActionPanel's `.proposal` article: a surface-2 well with a
// source label, a readable payload summary, and the decision button row) so the
// queue reads as the same product — no new card system.
//
// Fail-closed posture: a 403 (an operator who somehow reaches this) renders a
// "Leadership only" empty state, never a crash; any other fetch error renders a quiet
// error and never takes down the dashboard. The deterministic backend owns all state
// writes (INV-2) — this panel only surfaces the queue and records the human decision.

/** One Decision-Queue row (the wire shape of the backend DecisionResponse). */
export interface Decision {
  id: string;
  source: string;
  payload: Record<string, unknown>;
  state: 'open' | 'decided' | 'in_flight';
  /** Present on some feeds; not part of the OPEN-list projection — optional. */
  created_at?: string;
}

type DecisionAction = 'approve' | 'reject' | 'need_info';

type LoadState =
  | { status: 'loading' }
  | { status: 'forbidden' } // 403 — leader/admin only
  | { status: 'error'; message: string }
  | { status: 'ready'; decisions: Decision[] };

interface DecisionQueueWorkspaceProps {
  /** Fired after a successful action so the caller (App) can refresh the nav
   *  open-count badge — the queue owns its own list refresh regardless. */
  onChanged?: () => void;
}

export default function DecisionQueueWorkspace({
  onChanged,
}: DecisionQueueWorkspaceProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  const load = useCallback(() => {
    setState({ status: 'loading' });
    apiFetch('/decisions')
      .then((res) => {
        // Fail-closed: an operator reaching this surface is 403 — render the
        // "Leadership only" empty state, never a crash.
        if (res.status === 403) {
          setState({ status: 'forbidden' });
          return null;
        }
        if (!res.ok) throw new Error(`decisions request failed: ${res.status}`);
        return res.json() as Promise<Decision[]>;
      })
      .then((data) => {
        if (data === null) return; // forbidden — already handled
        setState({ status: 'ready', decisions: data });
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function act(id: string, action: DecisionAction, comment?: string): void {
    apiFetch(`/decisions/${id}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...(comment ? { comment } : {}) }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`action failed: ${res.status}`);
        return res.json();
      })
      .then(() => {
        load(); // refresh the queue
        onChanged?.(); // let App re-pull the nav badge count
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }

  if (state.status === 'loading') {
    return (
      <section data-testid="decision-queue" aria-label="Decision queue">
        <QueueHeader count={null} />
        <p
          data-testid="decision-queue-loading"
          className="mono"
          style={{ marginTop: 'var(--s-3)', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
        >
          Loading the decision queue…
        </p>
      </section>
    );
  }

  // Fail-closed: leader/admin only — an operator (403) sees this, not a crash.
  if (state.status === 'forbidden') {
    return (
      <section data-testid="decision-queue" aria-label="Decision queue">
        <div data-testid="decision-queue-forbidden">
          <EmptyState
            icon={<Lock size={18} aria-hidden />}
            title="Leadership only"
            body="The decision queue is available to leadership and admin seats."
          />
        </div>
      </section>
    );
  }

  // Fail-safe: an error renders quietly and never crashes the dashboard.
  if (state.status === 'error') {
    return (
      <section data-testid="decision-queue" aria-label="Decision queue">
        <QueueHeader count={null} />
        <p
          data-testid="decision-queue-error"
          role="alert"
          style={{ marginTop: 'var(--s-3)', fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
        >
          Could not load the decision queue: {state.message}
        </p>
      </section>
    );
  }

  // Only OPEN decisions are actionable (the backend list is OPEN-only; filter
  // defensively so a non-open never renders an approvable card).
  const open = state.decisions.filter((d) => d.state === 'open');

  return (
    <section data-testid="decision-queue" aria-label="Decision queue">
      <QueueHeader count={open.length} />

      {open.length === 0 ? (
        <div data-testid="decision-queue-empty" style={{ marginTop: 'var(--s-3)' }}>
          <EmptyState
            icon={<ClipboardCheck size={18} aria-hidden />}
            title="Queue clear"
            body="No decisions are waiting on leadership right now."
          />
        </div>
      ) : (
        <div
          data-testid="decision-queue-list"
          style={{ display: 'grid', gap: 'var(--s-3)', marginTop: 'var(--s-3)' }}
        >
          {open.map((decision) => (
            <DecisionCard
              key={decision.id}
              decision={decision}
              onAct={act}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function QueueHeader({ count }: { count: number | null }): JSX.Element {
  return (
    <div
      className="lab"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--s-1)',
        color: 'var(--muted)',
      }}
    >
      <ClipboardCheck size={12} aria-hidden /> Decision queue
      {count !== null && (
        <span data-testid="decision-open-count" style={{ color: 'var(--ink)' }}>
          {' '}
          — {count} open
        </span>
      )}
    </div>
  );
}

// A human-readable one-line value for a payload field. Objects/arrays are
// JSON-stringified so a structured payload still reads at a glance.
function summarizeValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

interface DecisionCardProps {
  decision: Decision;
  onAct: (id: string, action: DecisionAction, comment?: string) => void;
}

// One review card — the SAME `.proposal` review-card shell ActionPanel uses (a
// surface-2 well + decision button row), repurposed for a queued decision.
function DecisionCard({ decision, onAct }: DecisionCardProps): JSX.Element {
  const [needInfo, setNeedInfo] = useState(false);
  const [comment, setComment] = useState('');
  const [required, setRequired] = useState(false);

  const entries = Object.entries(decision.payload);

  function submitNeedInfo(): void {
    // Need-info REQUIRES a comment — block client-side and surface the required
    // state rather than POSTing an empty comment (the backend would 422 anyway).
    if (comment.trim() === '') {
      setRequired(true);
      return;
    }
    onAct(decision.id, 'need_info', comment.trim());
  }

  return (
    <article
      data-testid="decision-card"
      data-decision={decision.id}
      className="proposal"
      style={{
        padding: 'var(--s-3)',
        borderRadius: 'var(--r-md)',
        border: '1px solid var(--line)',
        background: 'var(--surface-2)',
      }}
    >
      <div
        className="lab"
        data-testid="decision-source"
        style={{ color: 'var(--muted)', marginBottom: 'var(--s-2)' }}
      >
        {decision.source}
      </div>

      {entries.length > 0 ? (
        <dl
          data-testid="decision-payload"
          style={{
            display: 'grid',
            gridTemplateColumns: 'auto 1fr',
            gap: '2px var(--s-3)',
            margin: 0,
            fontSize: 'var(--fs-sm)',
          }}
        >
          {entries.map(([key, value]) => (
            <div key={key} style={{ display: 'contents' }}>
              <dt className="mono" style={{ color: 'var(--muted)' }}>
                {key}
              </dt>
              <dd style={{ margin: 0, color: 'var(--ink)' }}>
                {summarizeValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p
          data-testid="decision-payload"
          style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: 0 }}
        >
          No additional detail.
        </p>
      )}

      {needInfo && (
        <div style={{ marginTop: 'var(--s-3)' }}>
          <textarea
            data-testid="need-info-comment"
            aria-label="Need-info comment"
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
              if (required) setRequired(false);
            }}
            rows={2}
            placeholder="What information is needed?"
            style={{
              width: '100%',
              fontFamily: 'var(--sans)',
              fontSize: 'var(--fs-body)',
              color: 'var(--ink)',
              background: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--r-sm)',
              padding: 'var(--s-2)',
              resize: 'vertical',
            }}
          />
          {required && (
            <p
              data-testid="need-info-required"
              role="alert"
              style={{
                margin: 'var(--s-1) 0 0',
                fontSize: 'var(--fs-sm)',
                color: 'var(--signal-ink)',
              }}
            >
              A comment is required to request more info.
            </p>
          )}
        </div>
      )}

      <div
        className="proposal-decisions"
        style={{ display: 'flex', gap: 'var(--s-2)', marginTop: 'var(--s-3)', flexWrap: 'wrap' }}
      >
        <Button
          variant="signal"
          icon={Check}
          data-testid="decision-approve"
          onClick={() => onAct(decision.id, 'approve')}
        >
          Approve
        </Button>
        <Button
          icon={XCircle}
          data-testid="decision-reject"
          onClick={() => onAct(decision.id, 'reject')}
        >
          Reject
        </Button>
        {needInfo ? (
          <Button
            icon={HelpCircle}
            data-testid="need-info-submit"
            onClick={submitNeedInfo}
          >
            Send need-info
          </Button>
        ) : (
          <Button
            icon={HelpCircle}
            data-testid="decision-need-info"
            onClick={() => setNeedInfo(true)}
          >
            Need info
          </Button>
        )}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Nav badge feed — a tiny standalone hook the shell (App) uses to drive the
// leadership nav's open-count badge. It reads the SAME leader-gated GET /decisions
// and counts OPEN rows; disabled (no fetch) for any non-leader seat. Fail-safe: a
// 403 or any error yields 0 so the badge never blocks the shell.
export function useOpenDecisionCount(
  enabled: boolean,
): { count: number; refresh: () => void } {
  const [count, setCount] = useState(0);

  const refresh = useCallback(() => {
    if (!enabled) {
      setCount(0);
      return;
    }
    apiFetch('/decisions')
      .then((res) => (res.ok ? (res.json() as Promise<Decision[]>) : null))
      .then((data) => {
        if (data === null) return; // 403 / non-OK — leave the count as-is
        setCount(data.filter((d) => d.state === 'open').length);
      })
      .catch(() => {
        /* fail-safe: keep the last-known count; never crash the shell */
      });
  }, [enabled]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { count, refresh };
}
