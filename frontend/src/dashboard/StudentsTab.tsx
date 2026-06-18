import { useEffect, useMemo, useState } from 'react';
import { Search, Users } from 'lucide-react';
import { apiFetch } from '../config';
import { Chip } from '../ui';
import type { Tone } from '../ui';
import EmptyState from './EmptyState';
import type { FamilySummary, WorkQueueRow } from './types';

// The Students tab (GT Pulse dashboard redesign — shared by both shells). A
// searchable roster of every family in the configured window: family names from
// GET /families, student names indexed once from GET /students (D-17), each tagged
// with a derived status chip (Awaiting SIS > Closed > Working > No Contact, D-18).
// Status is cross-referenced from /work-queue (recovery_state + contact_status)
// and /enrollment/sis-buckets (the PAID_NOT_IN_SIS cohort). Search matches family
// AND student names (parent names are not in the list payload — see the returned
// decision). Click a family → right panel. Owner-scoped server-side. Read-only
// GETs (INV-2).

const RESULT_CAP = 50;
// "Closed" — the family is done (recovered/funded). The recovery state machine
// uses `recovered` for a won-back family; `funded` covers the paid/enrolled end.
const CLOSED_STATES = new Set(['recovered', 'funded']);

type Status = 'no_contact' | 'working' | 'awaiting_sis' | 'closed';

const STATUS_META: Record<Status, { label: string; tone: Tone }> = {
  no_contact: { label: 'No Contact', tone: 'signal' },
  working: { label: 'Working', tone: 'flow' },
  awaiting_sis: { label: 'Awaiting SIS', tone: 'gate' },
  closed: { label: 'Closed', tone: 'neutral' },
};

// GET /students board shape (StudentBoardResponse) — one household group per
// family, each carrying its children's synthetic first names. We index only the
// names for search (D-17); the board's stage/score terms are ignored here.
interface StudentRow {
  synthetic_first_name?: string;
  display_label?: string;
}
interface StudentHouseholdGroup {
  family_id: string;
  students: StudentRow[];
}
interface StudentBoardResponse {
  households: StudentHouseholdGroup[];
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

interface StudentsData {
  families: FamilySummary[];
  // family_id → its work-queue row (recovery_state + contact_status).
  queue: Map<string, WorkQueueRow>;
  // family_ids that paid but aren't matched in the SIS yet.
  paidNotInSis: Set<string>;
  // family_id → its children's names, lower-cased, for search (D-17).
  studentNames: Map<string, string>;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: StudentsData };

// Chip-derivation precedence (D-18): a family can match more than one state; the
// chip shows the most action-relevant. Awaiting SIS (paid, unmatched — the
// actionable case) > Closed (recovered/funded) > Working (working/contacted) >
// No Contact (stalled & never contacted).
function deriveStatus(
  familyId: string,
  queue: Map<string, WorkQueueRow>,
  paidNotInSis: Set<string>,
): Status {
  if (paidNotInSis.has(familyId)) return 'awaiting_sis';
  const row = queue.get(familyId);
  if (row && CLOSED_STATES.has(row.recovery_state)) return 'closed';
  // Working = an actively-worked family: the `working` recovery state, or a
  // family that's been contacted/followed-up (contact_status `followed_up`).
  if (
    row &&
    (row.recovery_state === 'working' || row.contact_status === 'followed_up')
  )
    return 'working';
  return 'no_contact';
}

interface StudentsTabProps {
  onSelectFamily: (familyId: string) => void;
  selectedFamilyId?: string | null;
}

export default function StudentsTab({
  onSelectFamily,
  selectedFamilyId = null,
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
      // D-17: one-shot /students fetch → family_id → [student names] index, so
      // search matches student names without a per-row deal-view fetch.
      apiFetch(`/students`)
        .then((res) => (res.ok ? (res.json() as Promise<StudentBoardResponse>) : null))
        .catch(() => null),
    ])
      .then(([families, queueRows, sis, students]) => {
        if (cancelled) return;
        const queue = new Map<string, WorkQueueRow>();
        for (const r of Array.isArray(queueRows) ? queueRows : [])
          queue.set(r.family_id, r);
        const paidNotInSis = new Set<string>();
        for (const g of sis?.buckets ?? []) {
          if (g.bucket === 'paid_not_in_sis') {
            for (const f of g.families) paidNotInSis.add(f.family_id);
          }
        }
        const studentNames = new Map<string, string>();
        for (const h of students?.households ?? []) {
          const names = h.students
            .map((s) => s.synthetic_first_name ?? s.display_label ?? '')
            .filter((n) => n !== '')
            .join(' ')
            .toLowerCase();
          if (names !== '') studentNames.set(h.family_id, names);
        }
        setState({
          status: 'ready',
          data: {
            families: Array.isArray(families) ? families : [],
            queue,
            paidNotInSis,
            studentNames,
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
    const { families, studentNames } = state.data;
    return families
      .filter(
        (f) =>
          f.display_name.toLowerCase().includes(q) ||
          (studentNames.get(f.family_id)?.includes(q) ?? false),
      )
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
          aria-label="Search families and students"
          placeholder="Search family or student name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1 }}
        />
      </label>

      {state.status === 'loading' && (
        <p data-testid="students-loading" className="lab">
          Loading the roster…
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
          <EmptyState
            icon={<Users size={16} aria-hidden />}
            title="Search the roster"
            body="Type a family or student name to find a household and see its status across the recovery queue and the SIS reconcile."
          />
        ) : results.length === 0 ? (
          <EmptyState
            title="No families match"
            body={`No family or student name contains “${query.trim()}”.`}
          />
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
