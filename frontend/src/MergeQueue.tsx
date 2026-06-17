import { useCallback, useEffect, useState } from 'react';
import { Check, GitMerge, X } from 'lucide-react';
import { apiFetch } from './config';
import { Button, Card, Chip } from './ui';

// Merge-queue human-review UI (ENROLLMENT_REFACTOR §5.2, §6 Phase 1).
//
// Surfaces the REVIEW_QUEUE candidates produced by the deterministic
// `propose_merge` core (backend `core/identity.py`): ambiguous / partial identity
// matches — same email but different phones, or a shared phone with different
// emails — where a wrong auto-merge is the IDOR-grade danger this product exists
// to prevent. The core NEVER auto-merges (INV-2: a merge is a PROPOSAL, never a
// state write; INV-4: ambiguity fails closed and waits for a human). This UI is
// that human gate: it lists each candidate with the keys that AGREED and the keys
// that CONFLICTED, and lets a reviewer APPROVE (fold the duplicate into the
// primary) or REJECT (keep them separate) — recording the verdict through the
// existing proposal/decision spine (`POST /proposals/{proposal_id}/decision`,
// the SOLE state-applying path, NFR-6). It never resolves a merge on its own.
//
// ⚠ BACKEND GAP (reported as NEEDS_CONTEXT): `propose_merge` is a pure core
// function with NO HTTP surface today, and no route logs a merge proposal to the
// spine. This UI is wired to the natural contract — a `GET /merge-queue` read
// that exposes the logged REVIEW_QUEUE proposals (each carrying its spine
// `proposal_id`), and the existing `POST /proposals/{id}/decision` for the
// verdict — so it lights up the moment those endpoints land. Until then it
// degrades cleanly to its empty state rather than fabricating candidates.

// One REVIEW_QUEUE candidate — the MergeProposal shape (core/identity.py) plus
// the spine `proposal_id` the decision route writes against. `proposal_id` is the
// id of the ALREADY-LOGGED merge proposal; the UI only records a human verdict
// against it (it never creates the proposal client-side — INV-2).
interface MergeCandidate {
  proposal_id: string;
  verdict: string; // always "review_queue" for the queue
  primary_family_id: string;
  duplicate_family_id: string;
  matched_on: string[]; // identity keys that agreed
  conflicting_keys: string[]; // identity keys that disagreed
  summary: string;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; candidates: MergeCandidate[] };

// The spine verdict: approve folds the duplicate into the primary; discard keeps
// them separate. Both are logged decisions (NFR-6); neither is computed here.
type Verdict = 'approve' | 'discard';

export default function MergeQueue(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/merge-queue`)
      .then((res) => {
        if (!res.ok) throw new Error(`merge-queue request failed: ${res.status}`);
        return res.json() as Promise<MergeCandidate[]>;
      })
      .then((candidates) => {
        if (!cancelled) setState({ status: 'ready', candidates });
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

  // Record the human verdict on the proposal/decision spine — the SOLE
  // state-applying path (INV-2). The merge itself happens server-side ONLY on an
  // approve decision; this client never merges. On a logged decision the
  // candidate leaves the queue.
  const decide = useCallback((proposalId: string, action: Verdict): void => {
    apiFetch(`/proposals/${proposalId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`decision request failed: ${res.status}`);
        return res.json();
      })
      .then(() => {
        setState((prev) =>
          prev.status === 'ready'
            ? {
                status: 'ready',
                candidates: prev.candidates.filter(
                  (c) => c.proposal_id !== proposalId,
                ),
              }
            : prev,
        );
      })
      .catch(() => {
        // Network failure: leave the candidate in the queue (no optimistic drop —
        // a merge must never appear resolved without a logged decision).
      });
  }, []);

  if (state.status === 'loading') {
    return (
      <p data-testid="merge-queue-loading" className="lab">
        Loading merge queue…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="merge-queue-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load the merge queue: {state.message}
      </p>
    );
  }

  return (
    <section aria-label="Merge queue" data-testid="merge-queue">
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
          <GitMerge size={11} aria-hidden /> Merge queue — human review (never
          auto-merge)
        </h2>
      </div>

      {state.candidates.length === 0 ? (
        <Card>
          <p data-testid="merge-queue-empty" className="lab" style={{ margin: 0 }}>
            No duplicate households to review.
          </p>
        </Card>
      ) : (
        <Card pad={false}>
          <ul
            className="merge-list"
            style={{ listStyle: 'none', margin: 0, padding: 0 }}
          >
            {state.candidates.map((c, i) => (
              <li
                key={c.proposal_id}
                className="merge-candidate"
                data-testid="merge-candidate"
                style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}
              >
                <div
                  data-testid={`merge-candidate-${c.proposal_id}`}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 'var(--s-3)',
                    padding: 'var(--s-3) var(--s-4)',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      className="mono"
                      style={{
                        fontSize: 'var(--fs-sm)',
                        fontWeight: 600,
                        marginBottom: 'var(--s-1)',
                      }}
                    >
                      {c.primary_family_id}
                      <span
                        className="lab"
                        style={{ margin: '0 var(--s-1)', color: 'var(--muted)' }}
                      >
                        ←
                      </span>
                      {c.duplicate_family_id}
                    </div>
                    <p
                      style={{
                        margin: '0 0 var(--s-2)',
                        fontSize: 'var(--fs-sm)',
                        color: 'var(--ink)',
                      }}
                    >
                      {c.summary}
                    </p>
                    <div
                      style={{
                        display: 'flex',
                        flexWrap: 'wrap',
                        alignItems: 'center',
                        gap: 'var(--s-1)',
                      }}
                    >
                      <span className="lab" style={{ color: 'var(--muted)' }}>
                        matched
                      </span>
                      {c.matched_on.map((k) => (
                        <Chip key={`m-${k}`} tone="flow">
                          {k}
                        </Chip>
                      ))}
                      <span
                        className="lab"
                        style={{
                          color: 'var(--muted)',
                          marginLeft: 'var(--s-2)',
                        }}
                      >
                        conflict
                      </span>
                      {c.conflicting_keys.map((k) => (
                        <Chip key={`c-${k}`} tone="signal">
                          {k}
                        </Chip>
                      ))}
                    </div>
                  </div>

                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 'var(--s-1)',
                      flexShrink: 0,
                    }}
                  >
                    <Button
                      icon={Check}
                      variant="flow"
                      data-testid={`merge-approve-${c.proposal_id}`}
                      title="Approve the merge — fold the duplicate into the primary (logged)"
                      onClick={() => decide(c.proposal_id, 'approve')}
                    >
                      Approve
                    </Button>
                    <Button
                      icon={X}
                      data-testid={`merge-reject-${c.proposal_id}`}
                      title="Reject — keep the households separate (logged)"
                      onClick={() => decide(c.proposal_id, 'discard')}
                    >
                      Reject
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}
