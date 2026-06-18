import { useEffect, useState } from 'react';
import { Database, GitMerge } from 'lucide-react';
import { apiFetch } from '../config';
import { Chip, WorkspaceToggle } from '../ui';
import type { Tone } from '../ui';
import type { ReconcileIssue } from './types';

// The Reconcile tab (admin-dashboard redesign). A segment toggles between two
// lists of differences staff sync/resolve:
//   · HubSpot diff — the non-synced cohort from GET /seam (family_id + seam_status);
//     each row's resolve/sync action runs through POST /seam/{id}/reconcile (the
//     same routes HouseholdReconcileBoard uses — no new backend route invented).
//   · SIS Reconcile — the PAID_NOT_IN_SIS group from GET /enrollment/sis-buckets:
//     families who paid but aren't matched in the SIS yet (a manual-check list).
// Clicking a row lifts the selected issue to the dashboard, which shows the
// discrepancy + its actions in the right panel. Read-only GETs (INV-2).

type Mode = 'seam' | 'sis';

const MODE_OPTIONS = [
  { key: 'seam' as const, label: 'HubSpot diff', icon: GitMerge },
  { key: 'sis' as const, label: 'SIS Reconcile', icon: Database },
];

interface SeamRow {
  family_id: string;
  seam_status: string;
}

interface SisFamilyStatus {
  family_id: string;
}
interface SisBucketGroup {
  bucket: string;
  families: SisFamilyStatus[];
}
interface SisBucketsResponse {
  buckets: SisBucketGroup[];
}

const SEAM_TONE: Record<string, Tone> = {
  synced: 'flow',
  unsynced: 'gate',
  conflict: 'signal',
};

function shortId(id: string): string {
  return id.slice(0, 8);
}

// A row is "active" when the shell's `selectedIssueKey` equals `${kind}:${family_id}`
// (the stable key the shell builds from the issue it last received). Highlighting
// works across both the seam and SIS lists from that single key.

interface ReconcileTabProps {
  onSelectIssue: (issue: ReconcileIssue) => void;
  selectedIssueKey?: string | null;
}

type LoadState<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: T[] };

export default function ReconcileTab({
  onSelectIssue,
  selectedIssueKey = null,
}: ReconcileTabProps): JSX.Element {
  const [mode, setMode] = useState<Mode>('seam');
  const [seam, setSeam] = useState<LoadState<SeamRow>>({ status: 'loading' });
  const [sis, setSis] = useState<LoadState<SisFamilyStatus>>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setSeam({ status: 'loading' });
    apiFetch(`/seam`)
      .then((res) => {
        if (!res.ok) throw new Error(`seam failed: ${res.status}`);
        return res.json() as Promise<SeamRow[]>;
      })
      .then((rows) => {
        if (!cancelled)
          setSeam({ status: 'ready', rows: Array.isArray(rows) ? rows : [] });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setSeam({
            status: 'error',
            message: err instanceof Error ? err.message : 'unknown error',
          });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setSis({ status: 'loading' });
    apiFetch(`/enrollment/sis-buckets`)
      .then((res) => {
        if (!res.ok) throw new Error(`sis-buckets failed: ${res.status}`);
        return res.json() as Promise<SisBucketsResponse>;
      })
      .then((data) => {
        if (cancelled) return;
        const group = (data.buckets ?? []).find(
          (g) => g.bucket === 'paid_not_in_sis',
        );
        setSis({ status: 'ready', rows: group?.families ?? [] });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setSis({
            status: 'error',
            message: err instanceof Error ? err.message : 'unknown error',
          });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section aria-label="Reconcile" data-testid="admin-tab-reconcile">
      <div className="admin-toolbar" style={{ marginBottom: 'var(--s-3)' }}>
        <div data-testid="reconcile-toggle">
          <WorkspaceToggle
            options={MODE_OPTIONS}
            active={mode}
            onSelect={setMode}
            ariaLabel="Reconcile view"
          />
        </div>
      </div>

      {mode === 'seam' ? (
        seam.status === 'loading' ? (
          <p data-testid="reconcile-seam-loading" className="lab">
            Loading the HubSpot diff…
          </p>
        ) : seam.status === 'error' ? (
          <p
            data-testid="reconcile-seam-error"
            role="alert"
            style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
          >
            Could not load the seam: {seam.message}
          </p>
        ) : seam.rows.length === 0 ? (
          <div className="admin-empty" data-testid="reconcile-seam-empty">
            <span className="admin-empty-title">Everything is in sync</span>
            <span className="admin-empty-body">
              No HubSpot-vs-dashboard differences right now.
            </span>
          </div>
        ) : (
          <div data-testid="reconcile-seam-rows">
            {seam.rows.map((r) => {
              const active = selectedIssueKey === `seam:${r.family_id}`;
              return (
                <button
                  key={r.family_id}
                  type="button"
                  data-testid="reconcile-row"
                  data-family={r.family_id}
                  className={`admin-row${active ? ' is-active' : ''}`}
                  onClick={() =>
                    onSelectIssue({
                      kind: 'seam',
                      family_id: r.family_id,
                      status: r.seam_status,
                      seam_status: r.seam_status,
                    })
                  }
                >
                  <span className="mono admin-row-name" title={r.family_id}>
                    {shortId(r.family_id)}
                  </span>
                  <Chip tone={SEAM_TONE[r.seam_status] ?? 'neutral'}>
                    {r.seam_status}
                  </Chip>
                </button>
              );
            })}
          </div>
        )
      ) : sis.status === 'loading' ? (
        <p data-testid="reconcile-sis-loading" className="lab">
          Reconciling against the SIS…
        </p>
      ) : sis.status === 'error' ? (
        <p
          data-testid="reconcile-sis-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load SIS reconcile: {sis.message}
        </p>
      ) : sis.rows.length === 0 ? (
        <div className="admin-empty" data-testid="reconcile-sis-empty">
          <span className="admin-empty-title">No SIS gaps</span>
          <span className="admin-empty-body">
            Every paid family is matched in the SIS.
          </span>
        </div>
      ) : (
        <div data-testid="reconcile-sis-rows">
          {sis.rows.map((r) => {
            const active = selectedIssueKey === `sis:${r.family_id}`;
            return (
              <button
                key={r.family_id}
                type="button"
                data-testid="reconcile-sis-row"
                data-family={r.family_id}
                className={`admin-row${active ? ' is-active' : ''}`}
                onClick={() =>
                  onSelectIssue({
                    kind: 'sis',
                    family_id: r.family_id,
                    status: 'Paid · not in SIS',
                  })
                }
              >
                <span className="mono admin-row-name" title={r.family_id}>
                  {shortId(r.family_id)}
                </span>
                <Chip tone="signal">Paid · not in SIS</Chip>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
