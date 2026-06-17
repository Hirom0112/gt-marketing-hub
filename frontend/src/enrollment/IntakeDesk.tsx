import { useEffect, useMemo, useState } from 'react';
import { AlarmClock, UserPlus } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card, Chip } from '../ui';
import { fmtDay, fmtUSD, fundingLabel } from './format';

// IntakeDesk (M3) — the ADMIN's distinct Intake/Unassigned ROUTING desk
// (MULTI_AGENT_COCKPIT.md §4/§5). Routing the unowned pool is a different verb
// than working a queue, so it is its own surface.
//
// It reads GET /families?owner=none via apiFetch — the unassigned pool
// (assigned_rep_id IS NULL). The owner=none scoping is enforced SERVER-SIDE (the
// M1 owner param); the desk additionally guards client-side so an owned family
// that leaks into the array is NEVER listed (PLAN.md M3 R2). Each row shows a
// per-row ROUTER PROPOSAL — the recommended agent/tier — and the Unowned-Alarm
// partition (families past the unowned-alarm window) sorts to the TOP.
//
// SCOPE: M3 surfaces the desk + the recommendation + a per-row Assign affordance.
// The actual assign POST /enrollment/families/bulk-assign is M4 — the Assign
// control's handler is deferred (TODO(M4)); this surface only LISTS + recommends.
//
// Read-only GET (INV-2). Synthetic only (INV-1); reads through apiFetch (INV-5).

// One unassigned family on the intake desk (the GET /families?owner=none shape).
interface IntakeFamily {
  family_id: string;
  display_name: string;
  // assigned_rep_id IS NULL for the unowned pool; present so the desk can guard
  // (R2) against an owned family that leaks into the payload.
  assigned_rep_id?: string | null;
  current_stage?: string;
  value?: number;
  funding_type?: string | null;
  intake_date?: string;
  // The unowned-alarm flag (past the unowned-alarm window) — the partition key.
  unowned_alarm?: boolean;
  // The router PROPOSAL (the recommended agent/tier). M4 owns the compute; the
  // desk renders whatever the backend supplies (or a derived hint when absent).
  recommended_agent_id?: string | null;
  recommended_agent_name?: string | null;
  recommended_tier?: string | null;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; families: IntakeFamily[] };

interface IntakeDeskProps {
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
  refreshKey?: number;
}

// The unowned-alarm partition sorts to the TOP; within each partition the oldest
// intake (the longest unowned) leads. Returns a NEW array (never mutates props).
function partitionByAlarm(families: readonly IntakeFamily[]): IntakeFamily[] {
  const byAge = (a: IntakeFamily, b: IntakeFamily): number => {
    const am = Date.parse(a.intake_date ?? '');
    const bm = Date.parse(b.intake_date ?? '');
    const av = Number.isNaN(am) ? Infinity : am;
    const bv = Number.isNaN(bm) ? Infinity : bm;
    return av - bv; // oldest (smallest ms) first
  };
  const alarmed = families.filter((f) => f.unowned_alarm === true).sort(byAge);
  const inWindow = families
    .filter((f) => f.unowned_alarm !== true)
    .sort(byAge);
  return [...alarmed, ...inWindow];
}

function IntakeRow({
  fam,
  selected,
  onSelectFamily,
}: {
  fam: IntakeFamily;
  selected: boolean;
  onSelectFamily?: (familyId: string) => void;
}): JSX.Element {
  const rec = fam.recommended_agent_name;
  const tier = fam.recommended_tier;
  return (
    <div
      data-testid="intake-row"
      className={`intake-row${selected ? ' is-active' : ''}`}
      onClick={() => onSelectFamily?.(fam.family_id)}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr auto auto',
        alignItems: 'center',
        gap: 'var(--s-3)',
        padding: 'var(--s-2) var(--s-4)',
        borderBottom: '1px solid var(--line-2)',
        cursor: onSelectFamily ? 'pointer' : 'default',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div
          data-testid="intake-name"
          className="intake-name"
          style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}
        >
          {fam.display_name}
        </div>
        <div
          className="lab"
          style={{ display: 'flex', gap: 'var(--s-2)', color: 'var(--muted)' }}
        >
          <span className="mono">{fmtUSD(fam.value ?? 0)}</span>
          <span>· {fundingLabel(fam.funding_type)}</span>
          {fam.intake_date ? <span>· in {fmtDay(fam.intake_date)}</span> : null}
        </div>
      </div>

      {/* The per-row router proposal — the recommended agent/tier. M4 computes;
          the desk shows the field (or "—" when the backend omits it). */}
      <div data-testid="intake-router-proposal" className="intake-proposal">
        {rec ? (
          <Chip tone="flow" title="Recommended owner (routing proposal)">
            → {rec}
            {tier ? ` · ${tier}` : ''}
          </Chip>
        ) : (
          <span className="lab" style={{ color: 'var(--muted)' }}>
            no recommendation
          </span>
        )}
      </div>

      {/* The Assign affordance — present but DEFERRED. M4 wires the
          POST /enrollment/families/bulk-assign handler. */}
      <Button
        data-testid="intake-assign"
        icon={UserPlus}
        // TODO(M4): wire POST /enrollment/families/bulk-assign (the actual
        // assign verb). M3 only surfaces the affordance + the recommendation.
        onClick={(ev) => {
          ev.stopPropagation();
          /* TODO(M4): fire bulk-assign */
        }}
      >
        Assign
      </Button>
    </div>
  );
}

export default function IntakeDesk({
  selectedFamilyId,
  onSelectFamily,
  refreshKey = 0,
}: IntakeDeskProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/families?owner=none`)
      .then((res) => {
        if (!res.ok) throw new Error(`families request failed: ${res.status}`);
        return res.json() as Promise<IntakeFamily[]>;
      })
      .then((families) => {
        if (!cancelled) setState({ status: 'ready', families });
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

  // R2 guard: list ONLY owner=none families (assigned_rep_id IS NULL), even if an
  // owned family leaked into the array. The server is the source of truth; this
  // is defense-in-depth so the desk can never leak an owned family.
  const unassigned = useMemo<IntakeFamily[]>(() => {
    if (state.status !== 'ready') return [];
    return state.families.filter(
      (f) => f.assigned_rep_id === null || f.assigned_rep_id === undefined,
    );
  }, [state]);

  const ordered = useMemo(() => partitionByAlarm(unassigned), [unassigned]);
  const alarmCount = ordered.filter((f) => f.unowned_alarm === true).length;

  if (state.status === 'loading') {
    return (
      <p data-testid="intake-loading" className="lab">
        Loading the intake desk…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="intake-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load the intake desk: {state.message}
      </p>
    );
  }

  return (
    <section aria-label="Intake desk" data-testid="intake-desk">
      <Card pad={false}>
        <div
          className="intake-head"
          data-testid="intake-head"
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 'var(--s-2)',
            padding: 'var(--s-3) var(--s-4)',
            borderBottom: '1px solid var(--line-2)',
          }}
        >
          <span style={{ fontWeight: 700 }}>Intake · Unassigned</span>
          <span className="lab" style={{ color: 'var(--muted)' }}>
            · <b data-testid="intake-total">{ordered.length}</b> unowned · route
            to an agent
          </span>
        </div>

        {ordered.length === 0 ? (
          <div className="worklist-empty rest" data-testid="intake-empty">
            <span className="lab">The intake desk is clear</span>
            <span className="worklist-empty-line">
              No unassigned families to route right now.
            </span>
          </div>
        ) : (
          <>
            {alarmCount > 0 && (
              <div
                data-testid="intake-alarm-partition"
                className="lab"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--s-1)',
                  padding: 'var(--s-2) var(--s-4)',
                  color: 'var(--signal-ink)',
                  background: 'var(--signal-wash)',
                  borderBottom: '1px solid var(--line-2)',
                  fontWeight: 700,
                }}
              >
                <AlarmClock size={12} aria-hidden />
                Unowned alarm · {alarmCount} past the routing window — route first
              </div>
            )}
            {ordered.map((fam) => (
              <IntakeRow
                key={fam.family_id}
                fam={fam}
                selected={fam.family_id === selectedFamilyId}
                onSelectFamily={onSelectFamily}
              />
            ))}
          </>
        )}
      </Card>
    </section>
  );
}
