import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { AlertTriangle, Plus, Wallet } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card, Chip } from '../ui';
import { useSession } from '../session/SessionContext';
import { fmtUSD } from '../enrollment/format';

// Budget Tracker workspace (TODO_v2 §B4 task 5 — the $365K four-workstream tracker).
// It reads the leadership-gated GET /budget (a per-workstream roll-up of
// planned/actual/committed/remaining + a flagged-overrun list + a totals roll-up +
// a planned-vs-actual burn series) and renders:
//   · one row per workstream with a clear over-budget highlight (the shared
//     `--signal` wash/ink/border treatment HouseholdReconcileBoard's CRM-down
//     notice and the Chip `signal` tone use — no invented palette),
//   · a CSS bar burn chart (planned bar + actual bar per workstream, over-budget
//     bars in the signal tone) — no charting dependency, just div-width bars,
//   · a leadership-only add-entry form that POSTs /budget/entry and refreshes.
// The add-entry control is shown ONLY for an admin/leader seat (role from
// SessionContext); an operator never sees it, and a defensive 403/401 on the POST
// renders a read-only notice instead of crashing. The deterministic backend owns
// all writes (INV-2) — a >10% overrun auto-creates a Decision-Queue item server-side;
// this surface only records the spend and re-reads. Fail-safe throughout: a fetch
// error renders a quiet notice, never a dashboard crash.

/** One workstream row (the wire shape of the backend budget roll-up). */
export interface BudgetWorkstream {
  workstream: string;
  planned: number;
  actual: number;
  committed: number;
  remaining: number;
  /** Over/under-budget as a fraction (0.11 = 11% over). */
  variance: number;
  /** True when the workstream is >10% over (server-computed). */
  flagged: boolean;
}

/** One burn-chart datapoint — planned vs actual per workstream. */
export interface BudgetBurn {
  workstream: string;
  planned: number;
  actual: number;
}

export interface BudgetRollup {
  total_planned: number;
  total_actual: number;
  total_remaining: number;
  total_usd: number;
}

export interface BudgetResponse {
  workstreams: BudgetWorkstream[];
  flagged: string[];
  rollup: BudgetRollup;
  burn: BudgetBurn[];
}

/** The spend kinds an entry can record (matches the backend POST body). */
type EntryKind = 'recommended' | 'planned' | 'committed' | 'actual';
const ENTRY_KINDS: ReadonlyArray<EntryKind> = [
  'recommended',
  'planned',
  'committed',
  'actual',
];

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: BudgetResponse };

// snake/lower → Title Case for a workstream key ("guerrilla" → "Guerrilla",
// "field_ops" → "Field Ops"). The backend keys are structural, never PII.
function titleCase(key: string): string {
  return key
    .split(/[_\s]+/)
    .filter((w) => w.length > 0)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

// Variance fraction → a signed whole percent: 0.11 → "+11%", -0.05 → "-5%",
// 0 → "0%". Non-finite → "—".
function fmtVariance(value: number): string {
  if (!Number.isFinite(value)) return '—';
  const pct = Math.round(value * 100);
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct}%`;
}

export default function BudgetWorkspace(): JSX.Element {
  const { session } = useSession();
  // Leadership-only add-entry: admin + leader seats may add spend; an operator
  // never sees the form (the backend also 403s the POST defensively).
  const canAddEntry =
    session?.role === 'admin' || session?.role === 'leader';

  const [state, setState] = useState<LoadState>({ status: 'loading' });

  const load = useCallback(() => {
    setState({ status: 'loading' });
    apiFetch('/budget')
      .then((res) => {
        if (!res.ok) throw new Error(`budget request failed: ${res.status}`);
        return res.json() as Promise<BudgetResponse>;
      })
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (state.status === 'loading') {
    return (
      <section data-testid="budget-workspace" aria-label="Budget tracker">
        <BudgetHeader />
        <p
          data-testid="budget-loading"
          className="lab"
          style={{ marginTop: 'var(--s-3)', color: 'var(--muted)' }}
        >
          Loading the budget…
        </p>
      </section>
    );
  }

  // Fail-safe: a fetch error renders quietly and never crashes the dashboard.
  if (state.status === 'error') {
    return (
      <section data-testid="budget-workspace" aria-label="Budget tracker">
        <BudgetHeader />
        <p
          data-testid="budget-error"
          role="alert"
          style={{
            marginTop: 'var(--s-3)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--signal-ink)',
          }}
        >
          Could not load the budget: {state.message}
        </p>
      </section>
    );
  }

  const { workstreams, rollup, burn } = state.data;

  return (
    <section data-testid="budget-workspace" aria-label="Budget tracker">
      <BudgetHeader />

      <div style={{ marginTop: 'var(--s-4)' }}>
        <BudgetTable workstreams={workstreams} rollup={rollup} />
      </div>

      <div style={{ marginTop: 'var(--s-5)' }}>
        <BurnChart burn={burn} workstreams={workstreams} />
      </div>

      {canAddEntry && (
        <div style={{ marginTop: 'var(--s-5)' }}>
          <AddEntryForm workstreams={workstreams} onAdded={load} />
        </div>
      )}
    </section>
  );
}

function BudgetHeader(): JSX.Element {
  return (
    <h2
      className="lab"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--s-1)',
        margin: 0,
        fontWeight: 'normal',
        color: 'var(--muted)',
      }}
    >
      <Wallet size={12} aria-hidden /> Budget tracker — four workstreams
    </h2>
  );
}

// ---------------------------------------------------------------------------
// The roll-up table — one row per workstream + a totals footer. Flagged
// (over-budget) rows carry the shared signal wash/border treatment.
function BudgetTable({
  workstreams,
  rollup,
}: {
  workstreams: BudgetWorkstream[];
  rollup: BudgetRollup;
}): JSX.Element {
  return (
    <Card pad={false}>
      <table
        data-testid="budget-table"
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: 'var(--fs-sm)',
        }}
      >
        <thead>
          <tr>
            <Th align="left">Workstream</Th>
            <Th align="right">Planned</Th>
            <Th align="right">Actual</Th>
            <Th align="right">Committed</Th>
            <Th align="right">Remaining</Th>
            <Th align="right">Variance</Th>
          </tr>
        </thead>
        <tbody>
          {workstreams.map((w) => (
            <tr
              key={w.workstream}
              data-testid="budget-row"
              data-workstream={w.workstream}
              data-flagged={w.flagged ? 'true' : 'false'}
              style={{
                borderTop: '1px solid var(--line)',
                // Over-budget highlight: the shared signal wash (same token the
                // CRM-down notice + the Chip `signal` tone use).
                background: w.flagged ? 'var(--signal-wash)' : 'transparent',
              }}
            >
              <td style={{ padding: 'var(--s-2) var(--s-3)' }}>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 'var(--s-2)',
                    color: w.flagged ? 'var(--signal-ink)' : 'var(--ink)',
                    fontWeight: 600,
                  }}
                >
                  {titleCase(w.workstream)}
                  {w.flagged && (
                    <span data-testid={`budget-flag-${w.workstream}`}>
                      <Chip tone="signal">
                        <AlertTriangle size={10} aria-hidden /> over budget
                      </Chip>
                    </span>
                  )}
                </span>
              </td>
              <Td>{fmtUSD(w.planned)}</Td>
              <Td>{fmtUSD(w.actual)}</Td>
              <Td>{fmtUSD(w.committed)}</Td>
              <Td>{fmtUSD(w.remaining)}</Td>
              <td
                data-testid={`budget-variance-${w.workstream}`}
                style={{
                  padding: 'var(--s-2) var(--s-3)',
                  textAlign: 'right',
                  fontFamily: 'var(--mono)',
                  color: w.flagged ? 'var(--signal-ink)' : 'var(--muted)',
                  fontWeight: w.flagged ? 600 : 400,
                }}
              >
                {fmtVariance(w.variance)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr
            data-testid="budget-rollup"
            style={{ borderTop: '2px solid var(--line-strong)' }}
          >
            <td
              style={{
                padding: 'var(--s-2) var(--s-3)',
                fontWeight: 600,
              }}
            >
              Total
            </td>
            <Td bold>{fmtUSD(rollup.total_planned)}</Td>
            <Td bold>{fmtUSD(rollup.total_actual)}</Td>
            <Td>—</Td>
            <Td bold>{fmtUSD(rollup.total_remaining)}</Td>
            <Td>—</Td>
          </tr>
        </tfoot>
      </table>
    </Card>
  );
}

function Th({
  children,
  align,
}: {
  children: React.ReactNode;
  align: 'left' | 'right';
}): JSX.Element {
  return (
    <th
      className="lab"
      style={{
        textAlign: align,
        padding: 'var(--s-2) var(--s-3)',
        color: 'var(--muted)',
        fontWeight: 'normal',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  bold,
}: {
  children: React.ReactNode;
  bold?: boolean;
}): JSX.Element {
  return (
    <td
      style={{
        padding: 'var(--s-2) var(--s-3)',
        textAlign: 'right',
        fontFamily: 'var(--mono)',
        color: 'var(--ink)',
        fontWeight: bold ? 600 : 400,
      }}
    >
      {children}
    </td>
  );
}

// ---------------------------------------------------------------------------
// The burn chart — planned vs actual per workstream as paired CSS bars. No
// charting dependency: a div-width track normalised against the largest value
// across the series, so an over-budget actual bar visibly overruns its planned
// bar. Over-budget actual bars use the signal tone.
function BurnChart({
  burn,
  workstreams,
}: {
  burn: BudgetBurn[];
  workstreams: BudgetWorkstream[];
}): JSX.Element {
  const flaggedSet = new Set(
    workstreams.filter((w) => w.flagged).map((w) => w.workstream),
  );
  // Normalise every bar against the largest planned-or-actual figure so the bars
  // are comparable and an overrun reads at a glance. Guard a 0 max (no divide).
  const maxVal = Math.max(
    1,
    ...burn.flatMap((b) => [b.planned, b.actual]),
  );

  return (
    <Card>
      <div
        className="lab"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          color: 'var(--muted)',
          marginBottom: 'var(--s-3)',
        }}
      >
        Burn — planned vs actual
      </div>
      <div data-testid="burn-chart" style={{ display: 'grid', gap: 'var(--s-3)' }}>
        {burn.map((b) => {
          const over = flaggedSet.has(b.workstream) || b.actual > b.planned;
          const plannedPct = (b.planned / maxVal) * 100;
          const actualPct = (b.actual / maxVal) * 100;
          return (
            <div
              key={b.workstream}
              data-testid="burn-row"
              data-workstream={b.workstream}
            >
              <div
                className="mono"
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  fontSize: 'var(--fs-sm)',
                  marginBottom: '4px',
                  color: over ? 'var(--signal-ink)' : 'var(--ink)',
                }}
              >
                <span>{titleCase(b.workstream)}</span>
                <span style={{ color: 'var(--muted)' }}>
                  {fmtUSD(b.actual)} / {fmtUSD(b.planned)}
                </span>
              </div>
              {/* Planned bar — the neutral baseline track-fill. */}
              <Bar
                pct={plannedPct}
                color="var(--line-strong)"
                testid={`burn-bar-planned-${b.workstream}`}
                label="planned"
              />
              {/* Actual bar — flow normally, signal when over budget. */}
              <Bar
                pct={actualPct}
                color={over ? 'var(--signal)' : 'var(--flow)'}
                testid={`burn-bar-actual-${b.workstream}`}
                label="actual"
              />
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function Bar({
  pct,
  color,
  testid,
  label,
}: {
  pct: number;
  color: string;
  testid: string;
  label: string;
}): JSX.Element {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div
      style={{
        height: 8,
        borderRadius: 'var(--r-pill)',
        background: 'var(--line-2)',
        overflow: 'hidden',
        marginTop: 2,
      }}
    >
      <div
        data-testid={testid}
        aria-label={label}
        style={{ height: '100%', width: `${clamped}%`, background: color }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Leadership add-entry form. POSTs /budget/entry then refreshes the roll-up.
// Shown only for an admin/leader seat (the parent gates it); a defensive 403/401
// on the POST renders a read-only notice rather than crashing.
function AddEntryForm({
  workstreams,
  onAdded,
}: {
  workstreams: BudgetWorkstream[];
  onAdded: () => void;
}): JSX.Element {
  const first = workstreams[0]?.workstream ?? '';
  const [workstream, setWorkstream] = useState(first);
  const [kind, setKind] = useState<EntryKind>('actual');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  function submit(e: FormEvent): void {
    e.preventDefault();
    setError(null);
    const amountUsd = Number.parseFloat(amount);
    if (!Number.isFinite(amountUsd) || amountUsd <= 0) {
      setError('Enter a positive amount.');
      return;
    }
    setSubmitting(true);
    apiFetch('/budget/entry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        workstream,
        kind,
        amount_usd: amountUsd,
        ...(note.trim() ? { note: note.trim() } : {}),
      }),
    })
      .then((res) => {
        // Fail-closed: an operator who somehow reaches the POST is 403/401 —
        // render a read-only notice, never a crash.
        if (res.status === 403 || res.status === 401) {
          setForbidden(true);
          return null;
        }
        if (!res.ok) throw new Error(`entry failed: ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (data === null) return; // forbidden — already handled
        setAmount('');
        setNote('');
        onAdded(); // re-read the roll-up (picks up any auto-created decision)
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setError(message);
      })
      .finally(() => setSubmitting(false));
  }

  if (forbidden) {
    return (
      <Card>
        <p
          data-testid="budget-entry-forbidden"
          role="status"
          style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
        >
          Adding budget entries is available to leadership and admin seats.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <div
        className="lab"
        style={{ color: 'var(--muted)', marginBottom: 'var(--s-3)' }}
      >
        Add budget entry
      </div>
      <form
        data-testid="budget-entry-form"
        onSubmit={submit}
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'flex-end',
          gap: 'var(--s-3)',
        }}
      >
        <label style={labelStyle}>
          <span className="lab">Workstream</span>
          <select
            data-testid="budget-entry-workstream"
            value={workstream}
            onChange={(e) => setWorkstream(e.target.value)}
            style={controlStyle}
          >
            {workstreams.map((w) => (
              <option key={w.workstream} value={w.workstream}>
                {titleCase(w.workstream)}
              </option>
            ))}
          </select>
        </label>

        <label style={labelStyle}>
          <span className="lab">Kind</span>
          <select
            data-testid="budget-entry-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as EntryKind)}
            style={controlStyle}
          >
            {ENTRY_KINDS.map((k) => (
              <option key={k} value={k}>
                {titleCase(k)}
              </option>
            ))}
          </select>
        </label>

        <label style={labelStyle}>
          <span className="lab">Amount (USD)</span>
          <input
            data-testid="budget-entry-amount"
            type="number"
            min="0"
            step="0.01"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="0.00"
            style={controlStyle}
          />
        </label>

        <label style={{ ...labelStyle, flex: 1, minWidth: 160 }}>
          <span className="lab">Note (optional)</span>
          <input
            data-testid="budget-entry-note"
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. flyer print run"
            style={controlStyle}
          />
        </label>

        <Button
          type="submit"
          variant="flow"
          icon={Plus}
          data-testid="budget-entry-submit"
          disabled={submitting}
        >
          Add entry
        </Button>
      </form>
      {error && (
        <p
          data-testid="budget-entry-error"
          role="alert"
          style={{
            margin: 'var(--s-2) 0 0',
            fontSize: 'var(--fs-sm)',
            color: 'var(--signal-ink)',
          }}
        >
          {error}
        </p>
      )}
    </Card>
  );
}

const labelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '4px',
};

const controlStyle: React.CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 'var(--fs-sm)',
  color: 'var(--ink)',
  background: 'var(--surface)',
  border: '1px solid var(--line)',
  borderRadius: 'var(--r-sm)',
  padding: '7px 9px',
};
