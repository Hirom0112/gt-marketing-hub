import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Database, ShieldCheck } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card, Chip } from '../ui';

// CRM-Ops Data-Quality panel (TODO_v2 §C1). The Marketing/CRM-Ops view that sits
// BESIDE the merge queue on the reconcile surface: a sync-parity header (+ the
// data-confidence banner), the severity-ordered data-quality queue (broken UTMs,
// conflicts, unreliable fields), and field-reliability badges.
//
// Honesty mandate (the reason this panel exists): a broken UTM stays flagged RED.
// We NEVER auto-fix or re-render a broken UTM as "fixed" — the deterministic core
// owns any write (INV-2), and a conflict only resolves through the existing
// proposal/decision spine on an explicit human verdict (INV-4 fail-closed). The
// panel reads GET /crm/ops and reuses the signal (warning) token treatment shared
// by HouseholdReconcileBoard / DataConfidenceBanner — no invented palette.

type DqKind = 'conflict' | 'utm_broken' | 'unreliable_field';

// One severity-ordered data-quality issue. `proposal_id` is OPTIONAL: a conflict
// that has already been logged to the spine carries the id the decision route
// writes against (so we can offer a "Reconcile" verdict); an issue without one is
// rendered read-only/flagged — we NEVER fabricate an id (INV-2).
interface DqIssue {
  entity_id: string;
  kind: DqKind;
  severity: number;
  detail: string;
  proposal_id?: string | null;
}

// Per-entity UTM breakage detail — which keys are broken and why. Keyed by
// entity_id so a `utm_broken` issue can surface the offending keys as red chips.
interface BrokenEntity {
  entity_id: string;
  offending_keys: string[];
  reasons: string[];
}

interface UtmHealth {
  ok: number;
  broken: number;
  broken_entities: BrokenEntity[];
}

interface FieldFlag {
  field: string;
  status: 'reliable' | 'unreliable';
  reason: string | null;
}

// GET /crm/ops payload (backend CRM-Ops contract). `dq_queue` is ALREADY
// severity-ordered (conflict first) — the panel preserves the server order.
interface CrmOps {
  parity_overall: number;
  parity_by_field: Record<string, number>;
  data_confidence_banner: boolean;
  dq_queue: DqIssue[];
  utm_health: UtmHealth;
  field_flags: FieldFlag[];
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: CrmOps };

export default function DataQualityPanel(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  // Conflict proposals the human has already actioned through the spine — they
  // leave the queue. Broken UTMs are NEVER added here (no auto-fix).
  const [resolved, setResolved] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    apiFetch('/crm/ops')
      .then((res) => {
        if (!res.ok) throw new Error(`crm ops request failed: ${res.status}`);
        return res.json() as Promise<CrmOps>;
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

  // Record the human verdict on a conflict through the proposal/decision spine —
  // the SOLE state-applying path (INV-2), the SAME call MergeQueue uses. The core
  // owns the write; this client never reconciles on its own. On a logged decision
  // the conflict leaves the queue (a UTM breakage can never reach this path).
  const reconcile = useCallback((proposalId: string): void => {
    apiFetch(`/proposals/${proposalId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'approve' }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`decision request failed: ${res.status}`);
        return res.json();
      })
      .then(() => {
        setResolved((prev) => {
          const next = new Set(prev);
          next.add(proposalId);
          return next;
        });
      })
      .catch(() => {
        // Network failure: leave the conflict in the queue (no optimistic
        // resolve — a conflict must never appear fixed without a logged decision).
      });
  }, []);

  if (state.status === 'loading') {
    return (
      <p data-testid="data-quality-loading" className="lab">
        Loading data-quality…
      </p>
    );
  }
  if (state.status === 'error') {
    // Quiet notice — a CRM-ops read that fails must not crash the reconcile
    // surface it sits on.
    return (
      <p
        data-testid="data-quality-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load data-quality: {state.message}
      </p>
    );
  }

  const { parity_overall, data_confidence_banner, dq_queue, utm_health, field_flags } =
    state.data;

  // 0..1 ratio → one-decimal percent (matches DataConfidenceBanner).
  const parityPct = Math.round(parity_overall * 1000) / 10;
  // The parity indicator goes RED on the server's confidence decision (INV-11:
  // the threshold lives server-side, not as a client magic number).
  const parityTone = data_confidence_banner ? 'signal' : 'flow';

  // entity_id → its broken-UTM detail, so a utm_broken issue can surface the
  // offending keys as red chips.
  const brokenByEntity: Record<string, BrokenEntity> = {};
  for (const be of utm_health.broken_entities) brokenByEntity[be.entity_id] = be;

  const visibleQueue = dq_queue.filter(
    (i) => !(i.proposal_id != null && resolved.has(i.proposal_id)),
  );

  return (
    <section aria-label="Data quality" data-testid="data-quality-panel">
      {/* Sync-parity header. */}
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
          <Database size={11} aria-hidden /> CRM-Ops data quality — sync parity
        </h2>
        <span
          data-testid="data-quality-parity"
          style={{ marginLeft: 'auto' }}
          title={
            data_confidence_banner
              ? 'Parity below the trusted threshold'
              : 'Parity healthy'
          }
        >
          <Chip tone={parityTone}>parity {parityPct}%</Chip>
        </span>
      </div>

      {/* Data-confidence banner — reuses the shared signal-wash warning treatment. */}
      {data_confidence_banner && (
        <div
          className="dash-banner"
          data-testid="data-quality-confidence-banner"
          role="alert"
          style={{
            background: 'var(--signal-wash)',
            border: '1px solid var(--signal)',
            color: 'var(--signal-ink)',
            marginBottom: 'var(--s-2)',
          }}
        >
          <AlertTriangle size={16} aria-hidden style={{ flexShrink: 0 }} />
          <span style={{ flex: 1, minWidth: 0 }}>
            CRM↔cockpit sync parity has dropped to{' '}
            <strong>{parityPct}%</strong>, below the trusted threshold — figures
            may be stale until the seam reconciles.
          </span>
        </div>
      )}

      {/* Severity-ordered data-quality queue (server order preserved). */}
      {visibleQueue.length === 0 ? (
        <Card>
          <p
            data-testid="data-quality-empty"
            className="lab"
            style={{ margin: 0 }}
          >
            No data-quality issues.
          </p>
        </Card>
      ) : (
        <Card pad={false}>
          <ul
            className="dq-list"
            style={{ listStyle: 'none', margin: 0, padding: 0 }}
          >
            {visibleQueue.map((issue, i) => {
              const broken =
                issue.kind === 'utm_broken'
                  ? brokenByEntity[issue.entity_id]
                  : undefined;
              const canReconcile =
                issue.kind === 'conflict' &&
                issue.proposal_id != null &&
                issue.proposal_id !== '';
              return (
                <li
                  key={`${issue.entity_id}-${issue.kind}-${i}`}
                  className="dq-issue"
                  data-testid="data-quality-issue"
                  data-kind={issue.kind}
                  style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}
                >
                  <div
                    data-testid={`data-quality-issue-${issue.entity_id}`}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 'var(--s-3)',
                      padding: 'var(--s-3) var(--s-4)',
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: 'flex',
                          flexWrap: 'wrap',
                          alignItems: 'center',
                          gap: 'var(--s-1)',
                          marginBottom: 'var(--s-1)',
                        }}
                      >
                        <span
                          className="mono"
                          style={{ fontSize: 'var(--fs-sm)', fontWeight: 600 }}
                        >
                          {issue.entity_id}
                        </span>
                        {/* Broken UTM → RED chip(s) of the offending keys. It is
                            flagged, NEVER shown as fixed (honesty mandate). */}
                        {issue.kind === 'utm_broken' &&
                          (broken && broken.offending_keys.length > 0 ? (
                            broken.offending_keys.map((k) => (
                              <span
                                key={`utm-${k}`}
                                data-testid={`data-quality-utm-chip-${issue.entity_id}`}
                              >
                                <Chip tone="signal" title="Broken UTM — flagged">
                                  ⚠ utm: {k}
                                </Chip>
                              </span>
                            ))
                          ) : (
                            <span
                              data-testid={`data-quality-utm-chip-${issue.entity_id}`}
                            >
                              <Chip tone="signal" title="Broken UTM — flagged">
                                ⚠ broken UTM
                              </Chip>
                            </span>
                          ))}
                        {issue.kind === 'conflict' && (
                          <Chip tone="signal">conflict</Chip>
                        )}
                        {issue.kind === 'unreliable_field' && (
                          <Chip tone="gate">unreliable</Chip>
                        )}
                      </div>
                      <p
                        style={{
                          margin: 0,
                          fontSize: 'var(--fs-sm)',
                          color: 'var(--ink)',
                        }}
                      >
                        {issue.detail}
                      </p>
                      {broken && broken.reasons.length > 0 && (
                        <p
                          className="lab"
                          style={{
                            margin: 'var(--s-1) 0 0',
                            color: 'var(--muted)',
                          }}
                        >
                          {broken.reasons.join(' · ')}
                        </p>
                      )}
                    </div>

                    {/* Conflict with a logged proposal → human reconcile verdict
                        on the existing spine. No proposal id ⇒ read-only/flagged.
                        Broken UTMs get NO action — they stay flagged red. */}
                    <div style={{ flexShrink: 0 }}>
                      {canReconcile ? (
                        <Button
                          variant="signal"
                          data-testid={`data-quality-reconcile-${issue.proposal_id}`}
                          title="Reconcile — record the human verdict on the spine (logged)"
                          onClick={() => reconcile(issue.proposal_id as string)}
                        >
                          Reconcile
                        </Button>
                      ) : (
                        issue.kind === 'conflict' && (
                          <span
                            className="lab"
                            data-testid={`data-quality-flagged-${issue.entity_id}`}
                            style={{ color: 'var(--signal-ink)' }}
                          >
                            flagged
                          </span>
                        )
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      {/* Field-reliability badges — the honest low-trust markers. */}
      {field_flags.length > 0 && (
        <div style={{ marginTop: 'var(--s-3)' }}>
          <h3
            className="lab"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--s-1)',
              margin: '0 0 var(--s-2)',
              fontWeight: 'normal',
            }}
          >
            <ShieldCheck size={11} aria-hidden /> Field reliability
          </h3>
          <div
            data-testid="data-quality-field-flags"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 'var(--s-1)',
            }}
          >
            {field_flags.map((f) => {
              const unreliable = f.status === 'unreliable';
              return (
                <span
                  key={f.field}
                  data-testid={`data-quality-field-${f.field}`}
                  title={f.reason ?? undefined}
                >
                  <Chip tone={unreliable ? 'signal' : 'flow'}>
                    {f.field}: {unreliable ? 'unreliable' : 'reliable'}
                    {unreliable && f.reason ? ` — ${f.reason}` : ''}
                  </Chip>
                </span>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
