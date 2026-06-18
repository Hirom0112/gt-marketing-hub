import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlarmClock, UserPlus, Wand2 } from 'lucide-react';
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
// M4 WIRES the assign verb: the per-row Assign (and an "Auto-route all" control)
// fire POST /enrollment/families/bulk-assign — the SINGLE gated assignment write
// (deterministic, INV-2; the backend's route_family owns the ROUTING math, the
// desk only assigns to the DISPLAYED recommendation) — then RE-PULL the desk so
// the now-assigned families drop out of the owner=none pool.
//
// The GET is read-only (INV-2); the only write is the bulk-assign route, through
// apiFetch (which carries the demo headers; never a service_role, INV-5).
// Synthetic only (INV-1).

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
  busy,
  onSelectFamily,
  onAssign,
}: {
  fam: IntakeFamily;
  selected: boolean;
  busy: boolean;
  onSelectFamily?: (familyId: string) => void;
  onAssign: (fam: IntakeFamily) => void;
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

      {/* The Assign verb (M4) — fires POST /enrollment/families/bulk-assign for
          this one family, targeting its DISPLAYED recommended agent (the
          backend's route_family owns the math; the desk assigns to what it
          shows). Disabled with no recommendation, or while a write is in flight.
          The row click is suppressed so Assign never re-selects the family. */}
      <Button
        data-testid="intake-assign"
        icon={UserPlus}
        disabled={busy || !fam.recommended_agent_id}
        onClick={(ev) => {
          ev.stopPropagation();
          onAssign(fam);
        }}
      >
        Assign
      </Button>
    </div>
  );
}

// Fire the SINGLE gated assignment write for a batch of families to one agent.
// (A single-row Assign is just a 1-element family_ids.) Resolves to true on a
// 2xx; the caller re-pulls the desk so the assigned families drop out of the
// owner=none pool. The backend logs the decision server-side (INV-2).
async function postBulkAssign(
  familyIds: readonly string[],
  agentId: string,
): Promise<boolean> {
  const res = await apiFetch('/enrollment/families/bulk-assign', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ family_ids: familyIds, agent_id: agentId }),
  });
  return res.ok;
}

// One routed lead from POST /enrollment/leads/auto-assign — the DETERMINISTIC
// router's decision + its human-readable reason. agent_id null ⇒ HELD (ambiguous
// identity / parked); held leads stay in the pool.
interface AutoAssignResult {
  family_id: string;
  agent_id: string | null;
  routed_role: string | null;
  rule: string;
  reason: string;
  owner_match: boolean;
  held: boolean;
}

interface AutoAssignResponse {
  batch_id: string;
  counts: { assigned: number; held: number };
  results: AutoAssignResult[];
}

// Fire the deterministic router over the WHOLE unassigned intake pool (empty body
// ⇒ route every owner=none family). Unlike the per-row bulk-assign, this runs the
// real route_lead precedence server-side (owner-match → territory → readiness →
// income → weighted RR) and returns each decision WITH its reason (NFR-6). The
// deterministic core owns the write (INV-2); the UI only renders the result.
async function postAutoAssign(): Promise<AutoAssignResponse> {
  const res = await apiFetch('/enrollment/leads/auto-assign', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`auto-assign request failed: ${res.status}`);
  return (await res.json()) as AutoAssignResponse;
}

export default function IntakeDesk({
  selectedFamilyId,
  onSelectFamily,
  refreshKey = 0,
}: IntakeDeskProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  // An internal re-pull counter — bumped after a successful assign so the desk
  // re-reads owner=none and the now-assigned families disappear. Combined with
  // the prop refreshKey so the assign logic stays confined to this component
  // (no workspace plumbing needed).
  const [localRefresh, setLocalRefresh] = useState(0);
  // The assign write in flight (disables the controls); and a transient failure
  // banner (surfaced, never silent — matches the desk's role="alert" idiom).
  const [assigning, setAssigning] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);
  // The last auto-route's per-lead decisions (with reasons), surfaced as a
  // transient receipt so the operator sees WHY each lead routed where it did.
  const [routeReceipt, setRouteReceipt] = useState<
    { name: string; result: AutoAssignResult }[] | null
  >(null);

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
  }, [refreshKey, localRefresh]);

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

  // Assign one family to its DISPLAYED recommended agent, then re-pull. The UI
  // never recomputes routing (route_family is the backend's) — it assigns to the
  // recommendation the desk already shows.
  const assignOne = useCallback(
    async (fam: IntakeFamily): Promise<void> => {
      if (!fam.recommended_agent_id) return;
      setAssigning(true);
      setAssignError(null);
      try {
        const ok = await postBulkAssign(
          [fam.family_id],
          fam.recommended_agent_id,
        );
        if (!ok) throw new Error('assign request failed');
        setLocalRefresh((n) => n + 1); // re-pull owner=none
      } catch (err: unknown) {
        setAssignError(err instanceof Error ? err.message : 'assign failed');
      } finally {
        setAssigning(false);
      }
    },
    [],
  );

  // Auto-route all: run the DETERMINISTIC router over the whole unassigned pool
  // (one POST /enrollment/leads/auto-assign — no client-side routing). The server
  // returns each decision + its reason; we capture display names BEFORE the
  // re-pull (the routed families drop out of owner=none) so the receipt can name
  // them, then re-pull once.
  const autoRouteAll = useCallback(async (): Promise<void> => {
    if (ordered.length === 0) return;
    const nameById = new Map(ordered.map((f) => [f.family_id, f.display_name]));
    setAssigning(true);
    setAssignError(null);
    try {
      const resp = await postAutoAssign();
      setRouteReceipt(
        resp.results.map((result) => ({
          name: nameById.get(result.family_id) ?? result.family_id,
          result,
        })),
      );
      setLocalRefresh((n) => n + 1); // re-pull owner=none once
    } catch (err: unknown) {
      setAssignError(err instanceof Error ? err.message : 'auto-route failed');
    } finally {
      setAssigning(false);
    }
  }, [ordered]);

  const routableCount = ordered.length;

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
          {/* Auto-route all — assigns every listed family to its displayed
              recommended agent (the backend's route_family math), then re-pulls.
              Disabled while a write is in flight or nothing is routable. */}
          <Button
            data-testid="intake-auto-route"
            icon={Wand2}
            variant="flow"
            disabled={assigning || routableCount === 0}
            onClick={() => {
              void autoRouteAll();
            }}
            style={{ marginLeft: 'auto' }}
          >
            Auto-route all
          </Button>
        </div>

        {assignError !== null && (
          <p
            data-testid="intake-assign-error"
            role="alert"
            style={{
              margin: 0,
              padding: 'var(--s-2) var(--s-4)',
              color: 'var(--signal-ink)',
              background: 'var(--signal-wash)',
              fontSize: 'var(--fs-sm)',
              borderBottom: '1px solid var(--line-2)',
            }}
          >
            Could not route: {assignError}
          </p>
        )}

        {routeReceipt !== null && routeReceipt.length > 0 && (
          <div
            data-testid="intake-route-receipt"
            style={{
              padding: 'var(--s-2) var(--s-4)',
              borderBottom: '1px solid var(--line-2)',
              background: 'var(--rest, var(--surface-2))',
            }}
          >
            <div
              className="lab"
              style={{ fontWeight: 700, marginBottom: 'var(--s-1)' }}
            >
              Routed {routeReceipt.filter((r) => !r.result.held).length} ·
              held {routeReceipt.filter((r) => r.result.held).length} — why:
            </div>
            <ul
              style={{
                listStyle: 'none',
                margin: 0,
                padding: 0,
                display: 'flex',
                flexDirection: 'column',
                gap: 'var(--s-1)',
              }}
            >
              {routeReceipt.map(({ name, result }) => (
                <li
                  key={result.family_id}
                  data-testid="intake-route-receipt-row"
                  style={{ fontSize: 'var(--fs-sm)', lineHeight: 1.4 }}
                >
                  <b>{name}</b>
                  {result.owner_match && (
                    <span className="lab" style={{ color: 'var(--muted)' }}>
                      {' '}
                      · owner-match
                    </span>
                  )}
                  {result.held && (
                    <span style={{ color: 'var(--signal-ink)' }}> · HELD</span>
                  )}
                  <div style={{ color: 'var(--muted)' }}>{result.reason}</div>
                </li>
              ))}
            </ul>
          </div>
        )}

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
                busy={assigning}
                onSelectFamily={onSelectFamily}
                onAssign={(f) => {
                  void assignOne(f);
                }}
              />
            ))}
          </>
        )}
      </Card>
    </section>
  );
}
