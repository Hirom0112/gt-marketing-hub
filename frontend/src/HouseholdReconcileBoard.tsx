import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, GitMerge, RefreshCw, Flag } from 'lucide-react';
import { apiFetch } from './config';
import { Button, Card, Chip } from './ui';
import type { Tone } from './ui';

// Household reconciliation board (ENROLLMENT_REFACTOR §6 Phase 1, §8.1). ONE row
// per household — each child's DERIVED stage and the household's CRM seam status —
// with the human reconcile controls. It joins three reads:
//   · GET /households  — the roll-up (one row/household, per-child derived stage,
//                        worst-stage).
//   · GET /seam        — the non-synced cohort (family_id + seam_status); a family
//                        absent from this list is SYNCED.
//   · GET /crm/status  — the CRM seam state, used to FAIL CLOSED.
// The human controls are PUSH_LOCAL (force the local truth to the CRM mirror),
// FLAG_CONFLICT (route ambiguous divergence to the conflict state — fail-closed),
// and MERGE-QUEUE (open the human-review merge queue for duplicate/native-record
// resolution). All three reconcile through POST /seam/{family_id}/reconcile — the
// deterministic core owns the write (INV-2); a flagged conflict stays conflict
// (INV-4 fail-closed). When the CRM seam is DOWN (kill switch on / effective_mode
// not a working mode), the push/flag controls are DISABLED — never a silent no-op
// (the INV-3/INV-8 fail-closed posture, surfaced in the UI). Native fetch only.

type SeamStatus = 'synced' | 'unsynced' | 'conflict';

interface HouseholdChild {
  student_id: string;
  display_label: string;
  stage: string;
}

interface HouseholdRollUp {
  user_id: string | null;
  family_id: string;
  worst_stage: string;
  children: HouseholdChild[];
}

interface HouseholdsResponse {
  households: HouseholdRollUp[];
}

// GET /seam row — only the non-synced cohort is returned (absent ⇒ synced).
interface SeamRow {
  family_id: string;
  seam_status: SeamStatus;
}

// GET /crm/status (backend app/api/crm_status.py). `effective_mode` is what the
// registry would ACTUALLY select (`simulate` even with CRM_MODE=live when the kill
// switch is on); the board reads it to fail closed against the REAL behavior.
interface CrmStatus {
  crm_mode: string;
  kill_switch: boolean;
  effective_mode: string;
  token_configured: boolean;
  calls_per_run_cap: number;
}

// POST /seam/{id}/reconcile response.
interface ReconcileResponse {
  family_id: string;
  applied: boolean;
  seam_status: SeamStatus;
}

interface BoardData {
  households: HouseholdRollUp[];
  seam: Record<string, SeamStatus>; // family_id → status (absent = synced)
  crm: CrmStatus;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: BoardData };

interface HouseholdReconcileBoardProps {
  // The conflict row's merge-queue control opens the human-review merge queue
  // (Task 3). Optional so the board renders standalone in tests.
  onOpenMergeQueue?: (familyId: string) => void;
}

// The CRM seam is "down" when the kill switch is on or the effective mode is not
// a working seam mode (simulate / live). Anything else (off / paused / unknown)
// fails the action closed.
function seamIsDown(crm: CrmStatus): boolean {
  if (crm.kill_switch) return true;
  const mode = crm.effective_mode.toLowerCase();
  return mode !== 'simulate' && mode !== 'live';
}

const STATUS_TONE: Record<SeamStatus, Tone> = {
  synced: 'flow',
  unsynced: 'gate',
  conflict: 'signal',
};

export default function HouseholdReconcileBoard({
  onOpenMergeQueue,
}: HouseholdReconcileBoardProps = {}): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch(`/households`).then((res) => {
        if (!res.ok) throw new Error(`households request failed: ${res.status}`);
        return res.json() as Promise<HouseholdsResponse>;
      }),
      apiFetch(`/seam`).then((res) => {
        if (!res.ok) throw new Error(`seam request failed: ${res.status}`);
        return res.json() as Promise<SeamRow[]>;
      }),
      apiFetch(`/crm/status`).then((res) => {
        if (!res.ok) throw new Error(`crm status request failed: ${res.status}`);
        return res.json() as Promise<CrmStatus>;
      }),
    ])
      .then(([households, seam, crm]) => {
        if (cancelled) return;
        const seamMap: Record<string, SeamStatus> = {};
        for (const row of seam) seamMap[row.family_id] = row.seam_status;
        setState({
          status: 'ready',
          data: { households: households.households, seam: seamMap, crm },
        });
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

  // Reconcile one household through the deterministic core (INV-2). The result's
  // `seam_status` is the AUTHORITATIVE post-reconcile status — a flagged conflict
  // fails closed (applied=false, still conflict; INV-4), so we always adopt the
  // returned status rather than optimistically assuming success.
  const reconcile = useCallback((familyId: string): void => {
    apiFetch(`/seam/${familyId}/reconcile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`reconcile request failed: ${res.status}`);
        return res.json() as Promise<ReconcileResponse>;
      })
      .then((result) => {
        setState((prev) =>
          prev.status === 'ready'
            ? {
                status: 'ready',
                data: {
                  ...prev.data,
                  seam: {
                    ...prev.data.seam,
                    [familyId]: result.seam_status,
                  },
                },
              }
            : prev,
        );
      })
      .catch(() => {
        // Network failure: leave the row untouched (no optimistic resolve).
      });
  }, []);

  if (state.status === 'loading') {
    return (
      <p data-testid="household-board-loading" className="lab">
        Loading households…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="household-board-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load the reconciliation board: {state.message}
      </p>
    );
  }

  const { households, seam, crm } = state.data;
  const crmDown = seamIsDown(crm);

  return (
    <section
      aria-label="Household reconciliation board"
      data-testid="household-reconcile-board"
    >
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
          <GitMerge size={11} aria-hidden /> Household reconciliation · one row per
          household
        </h2>
      </div>

      {crmDown && (
        <div
          data-testid="crm-down-notice"
          role="status"
          className="mono"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            marginBottom: 'var(--s-2)',
            fontSize: 'var(--fs-sm)',
            padding: '6px 10px',
            borderRadius: 'var(--r-sm)',
            color: 'var(--signal-ink)',
            background: 'var(--signal-wash)',
            border: '1px solid var(--signal)',
          }}
        >
          <AlertTriangle size={12} aria-hidden /> CRM seam unavailable
          {crm.kill_switch ? ' (kill switch on)' : ` (${crm.effective_mode})`} —
          reconcile controls disabled
        </div>
      )}

      <Card pad={false}>
        <ul
          className="household-list"
          style={{ listStyle: 'none', margin: 0, padding: 0 }}
        >
          {households.map((hh, i) => {
            const status: SeamStatus = seam[hh.family_id] ?? 'synced';
            const actionable = status !== 'synced';
            return (
              <li
                key={hh.family_id}
                className="household-row"
                data-testid="household-row"
                style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}
              >
                <div
                  data-testid={`household-row-${hh.family_id}`}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 'var(--s-3)',
                    padding: 'var(--s-3) var(--s-4)',
                  }}
                >
                  {/* Left: the household + its children's derived stages. */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      className="mono"
                      style={{
                        fontSize: 'var(--fs-sm)',
                        fontWeight: 600,
                        marginBottom: 'var(--s-1)',
                      }}
                    >
                      {hh.family_id}
                      <span
                        className="lab"
                        style={{ marginLeft: 'var(--s-2)', color: 'var(--muted)' }}
                      >
                        worst stage: {hh.worst_stage}
                      </span>
                    </div>
                    <ul
                      style={{
                        listStyle: 'none',
                        margin: 0,
                        padding: 0,
                        display: 'grid',
                        gap: '4px',
                      }}
                    >
                      {hh.children.map((child) => (
                        <li
                          key={child.student_id}
                          data-testid="household-child"
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: 'var(--s-2)',
                          }}
                        >
                          <span
                            style={{ fontSize: 'var(--fs-sm)', minWidth: 0 }}
                          >
                            {child.display_label}
                          </span>
                          <Chip>{child.stage}</Chip>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* Right: seam status + the human reconcile controls. */}
                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'flex-end',
                      gap: 'var(--s-2)',
                      flexShrink: 0,
                    }}
                  >
                    <span
                      data-testid={`seam-status-${hh.family_id}`}
                      className="seam-status"
                    >
                      <Chip tone={STATUS_TONE[status]}>{status}</Chip>
                    </span>

                    {actionable && (
                      <div
                        style={{
                          display: 'flex',
                          flexWrap: 'wrap',
                          justifyContent: 'flex-end',
                          gap: 'var(--s-1)',
                        }}
                      >
                        <Button
                          icon={RefreshCw}
                          variant="flow"
                          data-testid={`push-local-${hh.family_id}`}
                          disabled={crmDown}
                          title={
                            crmDown
                              ? 'CRM seam unavailable · reconcile disabled'
                              : 'Force the local truth to the CRM mirror'
                          }
                          onClick={() => reconcile(hh.family_id)}
                        >
                          Push local
                        </Button>
                        <Button
                          icon={Flag}
                          variant="signal"
                          data-testid={`flag-conflict-${hh.family_id}`}
                          disabled={crmDown}
                          title={
                            crmDown
                              ? 'CRM seam unavailable · reconcile disabled'
                              : 'Flag the divergence as a conflict (fail-closed)'
                          }
                          onClick={() => reconcile(hh.family_id)}
                        >
                          Flag conflict
                        </Button>
                        {status === 'conflict' && (
                          <Button
                            icon={GitMerge}
                            data-testid={`merge-queue-${hh.family_id}`}
                            title="Open the human-review merge queue"
                            onClick={() => onOpenMergeQueue?.(hh.family_id)}
                          >
                            Merge queue
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </Card>
    </section>
  );
}
