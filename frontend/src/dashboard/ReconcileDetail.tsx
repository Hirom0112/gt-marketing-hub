import { useState } from 'react';
import { Database, Flag, GitMerge, RefreshCw } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card, Chip } from '../ui';
import type { Tone } from '../ui';
import SeamDot, { type SeamStatus } from '../enrollment/SeamDot';
import type { ReconcileIssue } from './types';

// The right-panel view for a selected RECONCILE issue (admin-dashboard redesign).
// It shows the specific discrepancy + its resolution/sync actions:
//   · seam — the HubSpot-vs-dashboard divergence; Push local / Flag conflict run
//     through POST /seam/{family_id}/reconcile (the deterministic core owns the
//     write, INV-2; a flagged conflict stays conflict — INV-4 fail-closed).
//   · sis — a paid-but-not-in-SIS family; a manual-check item (no auto write —
//     there is no SIS write route, and we never invent one), so the panel states
//     the discrepancy + the manual next step honestly.

interface ReconcileResponse {
  family_id: string;
  applied: boolean;
  seam_status: string;
}

const SEAM_TONE: Record<string, Tone> = {
  synced: 'flow',
  unsynced: 'gate',
  conflict: 'signal',
};

function dotFor(status: string): SeamStatus {
  return status === 'synced'
    ? 'synced'
    : status === 'conflict'
      ? 'conflict'
      : 'unsynced';
}

interface ReconcileDetailProps {
  issue: ReconcileIssue;
  // Notified after a resolve so the tab list re-pulls the latest status.
  onResolved?: () => void;
}

export default function ReconcileDetail({
  issue,
  onResolved,
}: ReconcileDetailProps): JSX.Element {
  const [status, setStatus] = useState<string>(issue.seam_status ?? issue.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // SIS issues have no write route (there is no SIS write — we never invent one),
  // so escalate is a local "flag for human review" gesture (INV-2: no state write).
  const [escalated, setEscalated] = useState(false);

  function reconcile(): void {
    setBusy(true);
    setError(null);
    apiFetch(`/seam/${issue.family_id}/reconcile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`reconcile failed: ${res.status}`);
        return res.json() as Promise<ReconcileResponse>;
      })
      .then((data) => {
        setStatus(data.seam_status);
        onResolved?.();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'unknown error');
      })
      .finally(() => setBusy(false));
  }

  return (
    <Card>
      <div className="admin-panel" data-testid="reconcile-detail" data-kind={issue.kind}>
        <h2 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}>
          {issue.kind === 'seam' ? 'HubSpot discrepancy' : 'SIS discrepancy'}
        </h2>

        <section className="admin-section">
          <div className="admin-section-title">
            {issue.kind === 'seam' ? (
              <GitMerge size={12} aria-hidden />
            ) : (
              <Database size={12} aria-hidden />
            )}
            Family
          </div>
          <span
            className="mono admin-kv-name"
            data-testid="reconcile-detail-family"
            title={issue.family_id}
          >
            {issue.family_id}
          </span>
        </section>

        <div className="admin-panel-rule" />

        {issue.kind === 'seam' ? (
          <>
            <section className="admin-section">
              <div className="admin-section-title">Seam status</div>
              <span
                data-testid="reconcile-detail-status"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-2)' }}
              >
                <SeamDot status={dotFor(status)} />
                <Chip tone={SEAM_TONE[status] ?? 'neutral'}>{status}</Chip>
              </span>
            </section>

            <section className="admin-section">
              <div className="admin-section-title">Resolve</div>
              <p className="admin-kv-sub">
                Push the local truth to the HubSpot mirror, or flag an ambiguous
                divergence as a conflict for human review (fail-closed).
              </p>
              <div style={{ display: 'flex', gap: 'var(--s-2)', flexWrap: 'wrap' }}>
                <Button
                  icon={RefreshCw}
                  variant="flow"
                  data-testid="reconcile-push-local"
                  disabled={busy || status === 'synced'}
                  onClick={reconcile}
                >
                  Push local
                </Button>
                <Button
                  icon={Flag}
                  variant="signal"
                  data-testid="reconcile-flag-conflict"
                  disabled={busy}
                  onClick={reconcile}
                >
                  Flag conflict
                </Button>
              </div>
              {error !== null && (
                <span
                  data-testid="reconcile-detail-error"
                  role="alert"
                  style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
                >
                  {error}
                </span>
              )}
            </section>
          </>
        ) : (
          <section className="admin-section">
            <div className="admin-section-title">Paid · not in SIS</div>
            <p className="admin-kv-sub" data-testid="reconcile-detail-sis-note">
              This family paid on GT&apos;s side but isn&apos;t matched in the
              school&apos;s Student Information System yet. This is a manual-check
              item — confirm the enrollment in the SIS, then it clears on the next
              reconcile. No automated write is made from here.
            </p>
            <div style={{ display: 'flex', gap: 'var(--s-2)', flexWrap: 'wrap' }}>
              <Button
                icon={Flag}
                variant="signal"
                data-testid="reconcile-sis-escalate"
                disabled={escalated}
                onClick={() => setEscalated(true)}
              >
                {escalated ? 'Flagged for review' : 'Review · escalate'}
              </Button>
            </div>
            {escalated && (
              <p
                className="admin-kv-sub"
                role="status"
                data-testid="reconcile-sis-escalated"
              >
                Flagged for a human SIS review — confirm the enrollment in the SIS,
                then it clears on the next reconcile.
              </p>
            )}
          </section>
        )}
      </div>
    </Card>
  );
}
