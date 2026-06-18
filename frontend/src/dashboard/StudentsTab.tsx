import { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { apiFetch } from '../config';
import { Chip } from '../ui';
import type { Tone } from '../ui';
import type { FamilySummary, WorkQueueRow } from './types';

// The Students tab (admin-dashboard redesign). Searches every family in the
// pipeline (GET /families, household display_name only — D-4) and tags each with a
// derived status chip: Closed / Awaiting SIS / Working / No Contact. The status is
// cross-referenced from /work-queue (recovery_state + contact_status) and
// /enrollment/sis-buckets (the PAID_NOT_IN_SIS cohort). Results render only once a
// query is typed and are capped for responsiveness. Click a family → right panel.
// Read-only GETs (INV-2).

const RESULT_CAP = 50;
const CLOSED_STATES = new Set(['recovered', 'lost', 'dismissed', 'dormant']);

type Status = 'no_contact' | 'working' | 'awaiting_sis' | 'closed';

const STATUS_META: Record<Status, { label: string; tone: Tone }> = {
  no_contact: { label: 'No Contact', tone: 'signal' },
  working: { label: 'Working', tone: 'flow' },
  awaiting_sis: { label: 'Awaiting SIS', tone: 'gate' },
  closed: { label: 'Closed', tone: 'neutral' },
};

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

interface StudentsData {
  families: FamilySummary[];
  // family_id → its work-queue row (recovery_state + contact_status).
  queue: Map<string, WorkQueueRow>;
  // family_ids that paid but aren't matched in the SIS yet.
  paidNotInSis: Set<string>;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: StudentsData };

function deriveStatus(
  familyId: string,
  queue: Map<string, WorkQueueRow>,
  paidNotInSis: Set<string>,
): Status {
  const row = queue.get(familyId);
  if (row && CLOSED_STATES.has(row.recovery_state)) return 'closed';
  if (paidNotInSis.has(familyId)) return 'awaiting_sis';
  if (row && (row.recovery_state === 'working' || row.contact_status === 'followed_up'))
    return 'working';
  return 'no_contact';
}

interface StudentsTabProps {
  selectedFamilyId: string | null;
  onSelectFamily: (familyId: string) => void;
}

export default function StudentsTab({
  selectedFamilyId,
  onSelectFamily,
}: StudentsTabProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    Promise.all([
      apiFetch(`/families`).then((res) => {
        if (!res.ok) throw new Error(`families failed: ${res.status}`);
        return res.json() as Promise<FamilySummary[]>;
      }),
      apiFetch(`/work-queue`)
        .then((res) => (res.ok ? (res.json() as Promise<WorkQueueRow[]>) : []))
        .catch(() => [] as WorkQueueRow[]),
      apiFetch(`/enrollment/sis-buckets`)
        .then((res) => (res.ok ? (res.json() as Promise<SisBucketsResponse>) : null))
        .catch(() => null),
    ])
      .then(([families, queueRows, sis]) => {
        if (cancelled) return;
        const queue = new Map<string, WorkQueueRow>();
        for (const r of Array.isArray(queueRows) ? queueRows : [])
          queue.set(r.family_id, r);
        const paidNotInSis = new Set<string>();
        const buckets = sis?.buckets ?? [];
        for (const g of buckets) {
          if (g.bucket === 'paid_not_in_sis') {
            for (const f of g.families) paidNotInSis.add(f.family_id);
          }
        }
        setState({
          status: 'ready',
          data: {
            families: Array.isArray(families) ? families : [],
            queue,
            paidNotInSis,
          },
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

  const results = useMemo(() => {
    if (state.status !== 'ready') return [];
    const q = query.trim().toLowerCase();
    if (q === '') return [];
    return state.data.families
      .filter((f) => f.display_name.toLowerCase().includes(q))
      .slice(0, RESULT_CAP);
  }, [state, query]);

  return (
    <section aria-label="Students" data-testid="admin-tab-students">
      <label
        className="history-tools"
        style={{
          display: 'flex',
          gap: 'var(--s-1)',
          alignItems: 'center',
          marginBottom: 'var(--s-3)',
        }}
      >
        <Search size={13} aria-hidden style={{ color: 'var(--muted)' }} />
        <input
          className="history-search"
          data-testid="students-search"
          aria-label="Search families"
          placeholder="Search family name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1 }}
        />
      </label>

      {state.status === 'loading' && (
        <p data-testid="students-loading" className="lab">
          Loading the pipeline…
        </p>
      )}
      {state.status === 'error' && (
        <p
          data-testid="students-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load families: {state.message}
        </p>
      )}

      {state.status === 'ready' &&
        (query.trim() === '' ? (
          <div className="admin-empty" data-testid="students-empty">
            <span className="admin-empty-title">Search the pipeline</span>
            <span className="admin-empty-body">
              Type a family name to find a household and see its status across the
              recovery queue and the SIS reconcile.
            </span>
          </div>
        ) : results.length === 0 ? (
          <div className="admin-empty" data-testid="students-no-results">
            <span className="admin-empty-title">No families match</span>
            <span className="admin-empty-body">
              No household name contains “{query.trim()}”.
            </span>
          </div>
        ) : (
          <div data-testid="students-rows">
            {results.map((f) => {
              const status = deriveStatus(
                f.family_id,
                state.data.queue,
                state.data.paidNotInSis,
              );
              const meta = STATUS_META[status];
              return (
                <button
                  key={f.family_id}
                  type="button"
                  data-testid="student-row"
                  data-family={f.family_id}
                  data-status={status}
                  className={`admin-row${selectedFamilyId === f.family_id ? ' is-active' : ''}`}
                  onClick={() => onSelectFamily(f.family_id)}
                >
                  <span className="admin-row-name">{f.display_name}</span>
                  <Chip tone={meta.tone}>{meta.label}</Chip>
                </button>
              );
            })}
          </div>
        ))}
    </section>
  );
}
