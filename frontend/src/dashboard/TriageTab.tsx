import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../config';
import RecencyChip from '../enrollment/RecencyChip';
import { fmtUSD } from '../enrollment/format';
import { isContactStatus } from '../enrollment/recency';
import { EmptyState } from './EmptyState';
import type { WorkQueueRow } from './types';

// The agent Triage tab (R6 / D-12). Answers "who is falling through the cracks
// right now?" off the owner-scoped GET /work-queue (the X-Demo-Agent-Id header
// already scopes the response to this agent's assigned families). A row surfaces
// when ANY of the brief's signals hold: no contact recorded / no logged sales
// activity / no follow-up recorded — i.e. no `last_contact_at` — OR an overdue
// follow-up (`contact_status === 'overdue'`). Recency semantics come from
// recency.ts (the single home for contact-status meaning), never hardcoded here.
// Read-only GET (INV-2).

// The work-queue row carries a contact-recency timestamp the shared type omits
// (it reads only the ranking fields). The triage predicate needs it, so we read
// it via a local widening — same source row, just the one extra optional field.
type TriageRow = WorkQueueRow & { last_contact_at?: string | null };

// True when a family is "falling through the cracks" (D-12): nothing has been
// logged against it yet (no contact/activity/follow-up → no `last_contact_at`)
// OR its follow-up has gone overdue. The overdue check reuses the canonical
// ContactStatus narrowing so a backend status rename can't silently drift this.
function isFallingThroughCracks(row: TriageRow): boolean {
  const noContactLogged =
    row.last_contact_at === null || row.last_contact_at === undefined;
  const overdue =
    isContactStatus(row.contact_status) && row.contact_status === 'overdue';
  return noContactLogged || overdue;
}

// The human reason a row surfaced — the "why it's surfaced" cell. An overdue
// follow-up is the more actionable signal, so it wins the label when both hold.
function surfaceReason(row: TriageRow): string {
  if (isContactStatus(row.contact_status) && row.contact_status === 'overdue') {
    return 'Follow-up overdue';
  }
  return 'No contact logged yet';
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: TriageRow[] };

interface TriageTabProps {
  onSelectFamily: (familyId: string) => void;
  selectedFamilyId?: string | null;
}

export default function TriageTab({
  onSelectFamily,
  selectedFamilyId = null,
}: TriageTabProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch('/work-queue')
      .then((res) => {
        if (!res.ok) throw new Error(`work-queue failed: ${res.status}`);
        return res.json() as Promise<TriageRow[]>;
      })
      .then((rows) => {
        if (cancelled) return;
        setState({ status: 'ready', rows: Array.isArray(rows) ? rows : [] });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const cracks = useMemo(
    () =>
      state.status === 'ready'
        ? state.rows.filter(isFallingThroughCracks)
        : [],
    [state],
  );

  return (
    <section aria-label="Triage" data-testid="admin-tab-triage">
      {state.status === 'loading' && (
        <p data-testid="triage-tab-loading" className="lab">
          Loading the worklist…
        </p>
      )}

      {state.status === 'error' && (
        <p
          data-testid="triage-tab-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load the worklist: {state.message}
        </p>
      )}

      {state.status === 'ready' &&
        (cracks.length === 0 ? (
          <EmptyState title="Nothing falling through the cracks" />
        ) : (
          <div data-testid="triage-tab-rows">
            {cracks.map((row) => (
              <button
                key={row.family_id}
                type="button"
                data-testid="triage-tab-row"
                data-family={row.family_id}
                className={`admin-row${selectedFamilyId === row.family_id ? ' is-active' : ''}`}
                onClick={() => onSelectFamily(row.family_id)}
              >
                <span style={{ minWidth: 0 }}>
                  <span className="admin-row-name">{row.display_name}</span>
                  <span className="admin-row-sub">{surfaceReason(row)}</span>
                </span>
                <RecencyChip status={row.contact_status} />
                <span className="admin-row-value">{fmtUSD(row.value)}</span>
              </button>
            ))}
          </div>
        ))}
    </section>
  );
}
