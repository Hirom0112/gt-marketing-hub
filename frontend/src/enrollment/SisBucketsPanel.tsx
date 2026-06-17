import { useEffect, useState } from 'react';
import { Database } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card } from '../ui';

// SisBucketsPanel (M5) — the admin SIS reconcile roll-up
// (MULTI_AGENT_COCKPIT.md §6). Reads GET /enrollment/sis-buckets: the daily
// reconcile job's verdicts grouped into buckets. The admin sees, at a glance,
// which PAID families diverge from the school's Student Information System and
// acts on each:
//   · 🔴 paid_not_in_sis — paid on GT's side but absent from the SIS → ASSIGN
//     (route a rep to chase it; the assign firing is M4).
//   · 🟡 records_lag — matched but the SIS hasn't confirmed → PROPOSE (a
//     human-gated action on the decision spine — NEVER a silent write, INV-2/4).
//   · ⚪ ambiguous — a partial (phone-only) match → REVIEW in the merge queue
//     (never an auto-merge, INV-4).
//   · ✅ confirmed — reconciled on both sides; no action.
//
// Read-only GET (INV-2); the payload is the PII firewall — only
// {family_id, present, confirmed_at, bucket}, never child PII (INV-1/INV-6).
// Synthetic only; reads through apiFetch (INV-5).

type Bucket = 'paid_not_in_sis' | 'records_lag' | 'ambiguous' | 'confirmed';

interface SisFamilyStatus {
  family_id: string;
  present: boolean;
  confirmed_at: string | null;
  bucket: Bucket;
}

interface SisBucketGroup {
  bucket: Bucket;
  count: number;
  families: SisFamilyStatus[];
}

interface SisBucketsResponse {
  buckets: SisBucketGroup[];
  total: number;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: SisBucketsResponse };

interface BucketMeta {
  label: string;
  emoji: string;
  // The per-row action label, or null when the bucket needs no action (✅).
  action: 'Assign' | 'Propose' | 'Review' | null;
}

const BUCKET_META: Record<Bucket, BucketMeta> = {
  paid_not_in_sis: { label: 'Paid · not in SIS', emoji: '🔴', action: 'Assign' },
  records_lag: { label: 'Records lag', emoji: '🟡', action: 'Propose' },
  ambiguous: { label: 'Ambiguous match', emoji: '⚪', action: 'Review' },
  confirmed: { label: 'Confirmed', emoji: '✅', action: null },
};

// Fixed admin order: the discrepancies that need action first, then the
// all-clear confirmed (mirrors the backend _SIS_BUCKET_ORDER).
const BUCKET_ORDER: Bucket[] = [
  'paid_not_in_sis',
  'records_lag',
  'ambiguous',
  'confirmed',
];

function shortId(familyId: string): string {
  return familyId.slice(0, 8);
}

interface SisBucketsPanelProps {
  refreshKey?: number;
  // 🔴 Assign and 🟡 Propose firing is wired by the parent (M4 bulk-assign /
  // the decision spine). The panel never writes directly — it surfaces the
  // action and delegates (INV-2/INV-4); absent callbacks ⇒ inert affordances.
  onAssign?: (familyId: string) => void;
  onPropose?: (familyId: string) => void;
  onReview?: (familyId: string) => void;
}

export default function SisBucketsPanel({
  refreshKey = 0,
  onAssign,
  onPropose,
  onReview,
}: SisBucketsPanelProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/enrollment/sis-buckets`)
      .then((res) => {
        if (!res.ok) throw new Error(`sis-buckets request failed: ${res.status}`);
        return res.json() as Promise<SisBucketsResponse>;
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
  }, [refreshKey]);

  if (state.status === 'loading') {
    return (
      <p data-testid="sis-buckets-loading" className="lab">
        Reconciling against the SIS…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="sis-buckets-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load SIS reconcile: {state.message}
      </p>
    );
  }

  // Tolerate an early/empty backend shape (a payload missing buckets/total) —
  // render all-zero groups rather than crash the workspace it mounts in.
  const groups: SisBucketGroup[] = Array.isArray(state.data.buckets)
    ? state.data.buckets
    : [];
  const total = typeof state.data.total === 'number' ? state.data.total : 0;
  const byBucket = new Map<Bucket, SisBucketGroup>(
    groups.map((g) => [g.bucket, g]),
  );

  function fireAction(bucket: Bucket, familyId: string): void {
    if (bucket === 'paid_not_in_sis') onAssign?.(familyId);
    else if (bucket === 'records_lag') onPropose?.(familyId);
    else if (bucket === 'ambiguous') onReview?.(familyId);
  }

  return (
    <section aria-label="SIS reconcile" data-testid="sis-buckets">
      <Card pad={false}>
        <div
          className="lab"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            padding: 'var(--s-3) var(--s-4)',
            borderBottom: '1px solid var(--line-2)',
            color: 'var(--muted)',
          }}
        >
          <Database size={12} aria-hidden /> SIS reconcile · {total} paid
          families
        </div>

        {BUCKET_ORDER.map((bucket) => {
          const group = byBucket.get(bucket);
          const meta = BUCKET_META[bucket];
          const families = group?.families ?? [];
          const count = group?.count ?? 0;
          return (
            <div key={bucket} data-testid={`sis-bucket-${bucket}`}>
              <div
                className="lab"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: 'var(--s-2) var(--s-4)',
                  background: 'var(--surface)',
                  borderBottom: '1px solid var(--line-2)',
                  color: 'var(--ink)',
                  fontWeight: 600,
                }}
              >
                <span>
                  <span aria-hidden>{meta.emoji}</span> {meta.label}
                </span>
                <span
                  className="mono"
                  data-testid={`sis-bucket-count-${bucket}`}
                  style={{ color: 'var(--muted)' }}
                >
                  {count}
                </span>
              </div>

              {families.map((fam) => (
                <div
                  key={fam.family_id}
                  data-testid="sis-bucket-row"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 'var(--s-3)',
                    padding: 'var(--s-2) var(--s-4)',
                    borderBottom: '1px solid var(--line-2)',
                  }}
                >
                  <span
                    className="mono"
                    data-testid="sis-row-family"
                    title={fam.family_id}
                    style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
                  >
                    {shortId(fam.family_id)}
                  </span>
                  {meta.action ? (
                    <Button
                      variant={bucket === 'paid_not_in_sis' ? 'signal' : 'default'}
                      onClick={() => fireAction(bucket, fam.family_id)}
                    >
                      {meta.action}
                    </Button>
                  ) : (
                    <span
                      className="lab"
                      aria-hidden
                      style={{ color: 'var(--flow-ink)' }}
                    >
                      ✓
                    </span>
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </Card>
    </section>
  );
}
