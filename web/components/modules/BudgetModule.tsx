'use client';

// Budget Tracker (Module 10) — the reconciliation surface, fully wired to the
// FastAPI backbone (GET /budget · POST /budget/entry · PUT /budget/planned).
//   • One number, everywhere: the four workstreams reconcile to the $365K FY26 plan.
//   • Any workstream >10% over plan auto-flags to the Decision Queue (INV: budget
//     variance >10% escalates) — surfaced in 10d + linked to /decision.
//   • Function owners enter their OWN committed + actual spend (operator-gated);
//     leadership re-plans allocations. System of record is the Hub; Summer Camp is a
//     separate P&L and never rolls in.
//
// Four controlled sub-views (TabBar): 10a Budget table · 10b Burn chart ·
// 10c Spend by workstream · 10d Variance alerts. Fail-soft: any null fetch falls
// back to the seed below so the screen renders with the backbone down.

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { moduleById, canEditWorkstream, type ModuleId, type Role, type Session } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import {
  apiGet,
  apiPost,
  apiPut,
  type BudgetResponse,
  type BudgetWorkstream,
  type BudgetHealth,
} from '@/lib/api';
import { LineChart } from '@/components/modules/DashboardTabs';

const MONO = 'JetBrains Mono';

// ---- Workstream metadata ----------------------------------------------------
// Backend workstream tokens → display name + owner + the ModuleId used for the
// per-owner edit gate (canEditWorkstream). Guerrilla/Ops have no operator module
// (leadership / the Marketing Lead own them), so they carry no moduleId.
interface WsMeta {
  name: string;
  owner: string;
  moduleId?: ModuleId;
}
const WORKSTREAM_META: Record<string, WsMeta> = {
  grassroots: { name: 'Grassroots marketing', owner: 'the Grassroots Owner', moduleId: 'grassroots' },
  content: { name: 'Thought leadership + content engine', owner: 'the Content Owner', moduleId: 'content' },
  guerrilla: { name: 'Guerrilla / earned media bets', owner: 'Leadership' },
  ops: { name: 'Marketing foundations + operations', owner: 'the Marketing Lead' },
};
const WORKSTREAM_ORDER = ['grassroots', 'content', 'guerrilla', 'ops'];

// The spec's pre-loaded "recommended" allocations (Module 10), a local constant —
// distinct from the backend's CURRENT planned (which leadership can re-plan).
const RECOMMENDED: Record<string, number> = {
  grassroots: 210_000,
  content: 90_000,
  guerrilla: 40_000,
  ops: 25_000,
};

// A stable color per workstream — reused across the donut + chart legends.
const WS_COLOR: Record<string, string> = {
  grassroots: 'var(--brand)',
  content: 'var(--signal)',
  guerrilla: 'var(--warn)',
  ops: 'var(--ok)',
};
const wsColor = (ws: string) => WS_COLOR[ws] ?? 'var(--ink-3)';
const wsName = (ws: string) => WORKSTREAM_META[ws]?.name ?? ws;
const wsOwner = (ws: string) => WORKSTREAM_META[ws]?.owner ?? '—';

// ---- Seed fallback (mirrors the backend demo seed) --------------------------
// Rendered when the backbone is unreachable (apiGet → null). Honest "○ SAMPLE".
const SEED: BudgetResponse = {
  workstreams: [
    { workstream: 'grassroots', planned: 210_000, committed: 20_000, actual: 150_000, remaining: 60_000, variance: -0.2857, flagged: false, health: 'on_track' },
    { workstream: 'content', planned: 90_000, committed: 5_000, actual: 80_000, remaining: 10_000, variance: -0.1111, flagged: false, health: 'watch' },
    { workstream: 'guerrilla', planned: 40_000, committed: 3_000, actual: 45_000, remaining: -5_000, variance: 0.125, flagged: true, health: 'at_risk' },
    { workstream: 'ops', planned: 25_000, committed: 2_000, actual: 18_000, remaining: 7_000, variance: -0.28, flagged: false, health: 'on_track' },
  ],
  flagged: ['guerrilla'],
  rollup: {
    total_planned: 365_000,
    total_actual: 293_000,
    total_remaining: 72_000,
    total_usd: 365_000,
    projected_burnout: '2026-06-19',
  },
  burn: [
    { workstream: 'grassroots', planned: 210_000, actual: 150_000 },
    { workstream: 'content', planned: 90_000, actual: 80_000 },
    { workstream: 'guerrilla', planned: 40_000, actual: 45_000 },
    { workstream: 'ops', planned: 25_000, actual: 18_000 },
  ],
  burn_series: [
    { week_start: '2026-05-04', cumulative_actual: 60_000, cumulative_planned: 60_833 },
    { week_start: '2026-05-11', cumulative_actual: 110_000, cumulative_planned: 121_667 },
    { week_start: '2026-05-18', cumulative_actual: 165_000, cumulative_planned: 182_500 },
    { week_start: '2026-05-25', cumulative_actual: 215_000, cumulative_planned: 243_333 },
    { week_start: '2026-06-01', cumulative_actual: 260_000, cumulative_planned: 304_167 },
    { week_start: '2026-06-08', cumulative_actual: 293_000, cumulative_planned: 365_000 },
  ],
};

// ---- Owner / role gating helpers --------------------------------------------
// Leadership (admin/leader) may enter ANY workstream and re-plan; an OPERATOR may
// enter only the workstream(s) they own (canEditWorkstream over the mapped module).
function operatorWorkstreams(session: Session): string[] {
  return WORKSTREAM_ORDER.filter((ws) => {
    const mid = WORKSTREAM_META[ws]?.moduleId;
    return mid ? canEditWorkstream(session, mid) : false;
  });
}
function canEnterAnyWorkstream(role: Role): boolean {
  return role === 'admin' || role === 'leader';
}
function isLeadership(role: Role): boolean {
  return role === 'admin' || role === 'leader';
}

interface Toast {
  msg: string;
  kind: 'ok' | 'err';
}

// =========================== the module ======================================
export function BudgetModule() {
  const def = moduleById('budget')!;
  const { session } = useSession();
  const [data, setData] = useState<BudgetResponse | null>(null); // null = loading
  const [isLive, setIsLive] = useState(false);
  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  const load = useCallback(() => {
    apiGet<BudgetResponse>('/budget', session.role).then((res) => {
      if (res && Array.isArray(res.workstreams) && res.workstreams.length > 0) {
        setData(res);
        setIsLive(true);
      } else {
        setData(SEED);
        setIsLive(false);
      }
    });
  }, [session.role]);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-dismiss a toast after a few seconds.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback((msg: string, kind: 'ok' | 'err') => setToast({ msg, kind }), []);

  if (data === null) {
    return (
      <>
        <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
        <section className="scr" style={{ padding: '20px 22px 40px' }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)' }}>Loading budget…</div>
        </section>
      </>
    );
  }

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      {tab === 0 && <BudgetTableTab data={data} isLive={isLive} role={session.role} session={session} refetch={load} notify={notify} />}
      {tab === 1 && <BurnChartTab data={data} isLive={isLive} />}
      {tab === 2 && <SpendByWorkstreamTab data={data} isLive={isLive} />}
      {tab === 3 && <VarianceAlertsTab data={data} isLive={isLive} />}
    </>
  );
}

// ============================ 10a Budget table ===============================
const TABLE_GRID = '1.7fr 1fr .85fr .85fr .85fr .85fr .95fr';

function BudgetTableTab({
  data,
  isLive,
  role,
  session,
  refetch,
  notify,
}: {
  data: BudgetResponse;
  isLive: boolean;
  role: Role;
  session: Session;
  refetch: () => void;
  notify: (msg: string, kind: 'ok' | 'err') => void;
}) {
  const rows = orderedRows(data);
  const totals = rows.reduce(
    (a, r) => ({
      recommended: a.recommended + (RECOMMENDED[r.workstream] ?? r.planned),
      planned: a.planned + r.planned,
      committed: a.committed + r.committed,
      actual: a.actual + r.actual,
      remaining: a.remaining + r.remaining,
    }),
    { recommended: 0, planned: 0, committed: 0, actual: 0, remaining: 0 },
  );
  const burnout = data.rollup.projected_burnout;

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          SYSTEM OF RECORD · THE HUB
        </span>
        <StatusPill live={isLive} />
      </div>

      {/* Summary cards — total plan / actual / remaining / projected burn-out */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
        <div style={{ border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', opacity: 0.8 }}>TOTAL PLAN · FY26</div>
          <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 30, lineHeight: 1.05, marginTop: 7 }}>{fmtFull(data.rollup.total_planned)}</div>
          <div style={{ fontSize: 10, opacity: 0.8, marginTop: 4 }}>The same number, everywhere.</div>
        </div>
        <SumCard label="ACTUAL SPEND" value={fmtK(data.rollup.total_actual)} note={`${fmt1(pctOf(data.rollup.total_actual, data.rollup.total_planned))}% burned`} />
        <SumCard label="REMAINING" value={fmtK(data.rollup.total_remaining)} note={`of ${fmtK(data.rollup.total_planned)} planned`} />
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>PROJECTED BURN-OUT</div>
          <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 26, lineHeight: 1.1, marginTop: 7, color: burnout ? 'var(--signal)' : 'var(--ink)' }}>
            {burnout ? fmtDate(burnout) : 'No burn-out'}
          </div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{burnout ? 'at recent weekly burn rate' : 'idle / under-pace budget'}</div>
        </div>
      </div>

      {/* Workstream table */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: TABLE_GRID, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', padding: '9px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>WORKSTREAM</div>
          <div style={{ textAlign: 'right' }}>RECOMMENDED</div>
          <div style={{ textAlign: 'right' }}>PLANNED</div>
          <div style={{ textAlign: 'right' }}>COMMITTED</div>
          <div style={{ textAlign: 'right' }}>ACTUAL</div>
          <div style={{ textAlign: 'right' }}>REMAINING</div>
          <div style={{ textAlign: 'center' }}>HEALTH</div>
        </div>

        {rows.map((r) => (
          <div key={r.workstream} style={{ display: 'grid', gridTemplateColumns: TABLE_GRID, alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
            <div>
              <div style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{wsName(r.workstream)}</div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{wsOwner(r.workstream)}</div>
            </div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, color: 'var(--ink-3)' }}>{fmtK(RECOMMENDED[r.workstream] ?? r.planned)}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, color: 'var(--ink-2)' }}>{fmtK(r.planned)}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, color: 'var(--ink-2)' }}>{fmtK(r.committed)}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(r.actual)}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, color: r.remaining < 0 ? 'var(--signal)' : 'var(--ink-2)' }}>{fmtK(r.remaining)}</div>
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <HealthChip health={r.health} />
            </div>
          </div>
        ))}

        {/* TOTAL — summed from the rows above (never hardcoded) */}
        <div style={{ display: 'grid', gridTemplateColumns: TABLE_GRID, alignItems: 'center', padding: '13px 16px', borderTop: '2px solid var(--ink)', background: 'var(--card-2)' }}>
          <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>TOTAL</div>
          <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(totals.recommended)}</div>
          <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(totals.planned)}</div>
          <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(totals.committed)}</div>
          <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(totals.actual)}</div>
          <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(totals.remaining)}</div>
          <div />
        </div>
      </div>

      {/* Spend-entry form (owner-gated) + planned edit (leadership only) */}
      <div style={{ display: 'grid', gridTemplateColumns: isLeadership(role) ? '1fr 1fr' : '1fr', gap: 14, marginTop: 14 }}>
        <EntryForm role={role} session={session} refetch={refetch} notify={notify} />
        {isLeadership(role) && <PlannedEditor role={role} rows={rows} refetch={refetch} notify={notify} />}
      </div>

      <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.6 }}>
        ⌖ Each function owner enters their own committed + actual spend; leadership re-plans allocations. The four workstreams reconcile to {fmtK(data.rollup.total_planned)}. Summer Camp is a separate P&amp;L and does <b>not</b> roll into this total. Any workstream &gt;10% over plan auto-flags to the Decision Queue.
      </div>
    </section>
  );
}

// ---- Spend-entry form (POST /budget/entry) — owner-gated --------------------
function EntryForm({
  role,
  session,
  refetch,
  notify,
}: {
  role: Role;
  session: Session;
  refetch: () => void;
  notify: (msg: string, kind: 'ok' | 'err') => void;
}) {
  const owned = operatorWorkstreams(session);
  const anyWs = canEnterAnyWorkstream(role);
  const options = anyWs ? WORKSTREAM_ORDER : owned;
  const [workstream, setWorkstream] = useState(options[0] ?? '');
  const [kind, setKind] = useState<'committed' | 'actual'>('actual');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  // No enterable workstream (e.g. an operator who owns none) — honest empty state.
  if (options.length === 0) {
    return (
      <div style={CARD}>
        <div style={CARD_HEAD}>RECORD SPEND</div>
        <div style={{ padding: '14px 16px', fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)', lineHeight: 1.6 }}>
          You don&apos;t own a budget workstream, so there&apos;s nothing to record here. Owners enter their own committed + actual spend.
        </div>
      </div>
    );
  }

  const submit = async () => {
    const amt = Number(amount);
    if (!workstream || !Number.isFinite(amt) || amt <= 0) {
      notify('Enter a positive amount.', 'err');
      return;
    }
    setSaving(true);
    const res = await apiPost<BudgetResponse>('/budget/entry', role, {
      workstream,
      kind,
      amount_usd: amt,
      note: note.trim() || undefined,
    });
    setSaving(false);
    if (!res) {
      notify('Could not record spend — check your access and that the backbone is up.', 'err');
      return;
    }
    const flagged = Array.isArray(res.flagged) && res.flagged.includes(workstream);
    notify(
      flagged
        ? `Recorded ${fmtK(amt)} ${kind} on ${wsName(workstream)}. That pushed it >10% over plan → auto-flagged to the Decision Queue.`
        : `Recorded ${fmtK(amt)} ${kind} on ${wsName(workstream)}.`,
      'ok',
    );
    setAmount('');
    setNote('');
    refetch();
  };

  return (
    <div style={CARD}>
      <div style={{ ...CARD_HEAD, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>RECORD SPEND</span>
        <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 400, opacity: 0.85 }}>
          {anyWs ? 'ANY WORKSTREAM' : 'YOUR WORKSTREAM ONLY'}
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '14px 16px' }}>
        <label style={FIELD_LABEL}>
          WORKSTREAM
          {anyWs ? (
            <select value={workstream} onChange={(e) => setWorkstream(e.target.value)} style={SELECT}>
              {options.map((ws) => (
                <option key={ws} value={ws}>{wsName(ws)}</option>
              ))}
            </select>
          ) : (
            // Operator: locked to their owned workstream (cannot pick another).
            <input value={wsName(workstream)} readOnly style={{ ...INPUT, color: 'var(--ink-2)', background: 'var(--card-2)' }} />
          )}
        </label>
        <div style={{ display: 'flex', gap: 10 }}>
          <label style={{ ...FIELD_LABEL, flex: 1 }}>
            KIND
            <select value={kind} onChange={(e) => setKind(e.target.value as 'committed' | 'actual')} style={SELECT}>
              <option value="actual">Actual (spent)</option>
              <option value="committed">Committed (not yet spent)</option>
            </select>
          </label>
          <label style={{ ...FIELD_LABEL, flex: 1 }}>
            AMOUNT (USD)
            <input type="number" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0" style={INPUT} />
          </label>
        </div>
        <label style={FIELD_LABEL}>
          NOTE (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Q3 ambassador stipends" style={INPUT} />
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>
            {saving ? 'RECORDING…' : 'RECORD SPEND'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Planned-amount editor (PUT /budget/planned) — leadership only ----------
function PlannedEditor({
  role,
  rows,
  refetch,
  notify,
}: {
  role: Role;
  rows: BudgetWorkstream[];
  refetch: () => void;
  notify: (msg: string, kind: 'ok' | 'err') => void;
}) {
  const [workstream, setWorkstream] = useState(rows[0]?.workstream ?? WORKSTREAM_ORDER[0]);
  const current = rows.find((r) => r.workstream === workstream);
  const [planned, setPlanned] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const amt = Math.round(Number(planned));
    if (!workstream || !Number.isFinite(amt) || amt < 1) {
      notify('Enter a planned amount of at least $1.', 'err');
      return;
    }
    setSaving(true);
    const res = await apiPut<BudgetResponse>('/budget/planned', role, { workstream, planned_usd: amt });
    setSaving(false);
    if (!res) {
      notify('Could not re-plan — leadership access required and the backbone must be up.', 'err');
      return;
    }
    notify(`Re-planned ${wsName(workstream)} to ${fmtK(amt)}.`, 'ok');
    setPlanned('');
    refetch();
  };

  return (
    <div style={CARD}>
      <div style={{ ...CARD_HEAD, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>RE-PLAN ALLOCATION</span>
        <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 400, opacity: 0.85 }}>LEADERSHIP ONLY</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '14px 16px' }}>
        <label style={FIELD_LABEL}>
          WORKSTREAM
          <select value={workstream} onChange={(e) => setWorkstream(e.target.value)} style={SELECT}>
            {rows.map((r) => (
              <option key={r.workstream} value={r.workstream}>{wsName(r.workstream)}</option>
            ))}
          </select>
        </label>
        <label style={FIELD_LABEL}>
          NEW PLANNED (USD)
          <input
            type="number"
            min={1}
            value={planned}
            onChange={(e) => setPlanned(e.target.value)}
            placeholder={current ? String(current.planned) : '0'}
            style={INPUT}
          />
        </label>
        <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          Current: {current ? fmtFull(current.planned) : '—'}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>
            {saving ? 'SAVING…' : 'UPDATE PLAN'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================ 10b Burn chart =================================
function BurnChartTab({ data, isLive }: { data: BudgetResponse; isLive: boolean }) {
  const pts = data.burn_series ?? [];
  const empty = pts.length === 0;
  const lastActual = pts.length ? pts[pts.length - 1].cumulative_actual : 0;
  const lastPlanned = pts.length ? pts[pts.length - 1].cumulative_planned : 0;
  const series = [
    { label: 'Cumulative actual', color: 'var(--signal)', points: pts.map((p) => p.cumulative_actual) },
    { label: 'Cumulative plan', color: 'var(--ink-3)', points: pts.map((p) => p.cumulative_planned) },
  ];

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
        <StatusPill live={isLive} />
      </div>
      <div style={CARD}>
        <div style={{ ...CARD_HEAD, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>CUMULATIVE BURN · ACTUAL vs PLAN</span>
          {!empty && (
            <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 400, opacity: 0.85 }}>
              {fmtDate(pts[0].week_start)} → {fmtDate(pts[pts.length - 1].week_start)}
            </span>
          )}
        </div>
        {empty ? (
          <div style={{ padding: '28px 16px', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.6 }}>
            No dated actual spend yet — the weekly burn series accrues as owners record actual spend. Nothing fabricated here.
          </div>
        ) : (
          <div style={{ padding: '16px' }}>
            <LineChart series={series} sharedScale />
            <div style={{ display: 'flex', gap: 18, marginTop: 10, flexWrap: 'wrap' }}>
              {series.map((s) => (
                <span key={s.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--ink-2)' }}>
                  <span style={{ width: 14, height: 2, background: s.color, display: 'inline-block' }} /> {s.label}
                </span>
              ))}
            </div>
            <p style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.6 }}>
              ◆ Cumulative actual spend ({fmtK(lastActual)} to date) vs a straight even-pace plan line ({fmtK(lastPlanned)} by the same week), on one shared scale. Below the plan line = under-pace; above it = burning faster than planned.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

// ====================== 10c Spend by workstream (donut) ======================
function SpendByWorkstreamTab({ data, isLive }: { data: BudgetResponse; isLive: boolean }) {
  const rows = orderedRows(data).filter((r) => r.actual > 0);
  const total = rows.reduce((a, r) => a + r.actual, 0);
  const segments = rows.map((r) => ({ label: wsName(r.workstream), value: r.actual, color: wsColor(r.workstream), ws: r.workstream }));

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
        <StatusPill live={isLive} />
      </div>
      <div style={CARD}>
        <div style={CARD_HEAD}>SHARE OF ACTUAL SPEND</div>
        {total === 0 ? (
          <div style={{ padding: '28px 16px', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-3)' }}>
            No actual spend recorded yet — the breakdown appears once owners record actual spend.
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 28, alignItems: 'center', padding: '20px 16px', flexWrap: 'wrap' }}>
            <Donut segments={segments} total={total} />
            <div style={{ flex: 1, minWidth: 220, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {segments.map((s) => (
                <div key={s.ws} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ width: 11, height: 11, background: s.color, display: 'inline-block', flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: 12.5, color: 'var(--ink)' }}>{s.label}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink-2)' }}>{fmtK(s.value)}</span>
                  <span style={{ fontFamily: 'Fraunces', fontSize: 14, fontWeight: 600, color: 'var(--ink)', minWidth: 48, textAlign: 'right' }}>
                    {fmt1(pctOf(s.value, total))}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        <div style={{ padding: '10px 16px', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', borderTop: '1px solid var(--line)' }}>
          Share of {fmtK(total)} total actual spend across {segments.length} workstream{segments.length === 1 ? '' : 's'}.
        </div>
      </div>
    </section>
  );
}

// A small dependency-free SVG donut (stroke-dasharray arcs, starting at 12 o'clock).
function Donut({ segments, total }: { segments: { label: string; value: number; color: string }[]; total: number }) {
  const size = 168;
  const stroke = 30;
  const radius = (size - stroke) / 2;
  const c = 2 * Math.PI * radius;
  const cx = size / 2;
  let offset = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="spend by workstream donut chart">
      <g transform={`rotate(-90 ${cx} ${cx})`}>
        <circle cx={cx} cy={cx} r={radius} fill="none" stroke="var(--line)" strokeWidth={stroke} />
        {segments.map((s) => {
          const frac = total > 0 ? s.value / total : 0;
          const dash = frac * c;
          const el = (
            <circle
              key={s.label}
              cx={cx}
              cy={cx}
              r={radius}
              fill="none"
              stroke={s.color}
              strokeWidth={stroke}
              strokeDasharray={`${dash} ${c - dash}`}
              strokeDashoffset={-offset}
            />
          );
          offset += dash;
          return el;
        })}
      </g>
      <text x={cx} y={cx - 4} textAnchor="middle" style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 22, fill: 'var(--ink)' }}>
        {fmtK(total)}
      </text>
      <text x={cx} y={cx + 14} textAnchor="middle" style={{ fontFamily: MONO, fontSize: 8.5, fill: 'var(--ink-3)' }}>
        ACTUAL
      </text>
    </svg>
  );
}

// ========================= 10d Variance alerts ===============================
function VarianceAlertsTab({ data, isLive }: { data: BudgetResponse; isLive: boolean }) {
  // Flagged (>10% over plan) OR health at_risk — fail toward escalation.
  const flaggedSet = new Set(data.flagged ?? []);
  const alerts = orderedRows(data).filter((r) => flaggedSet.has(r.workstream) || r.health === 'at_risk' || r.flagged);

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          &gt;10% OVER PLAN · AUTO-FLAGGED
        </span>
        <StatusPill live={isLive} />
      </div>

      {alerts.length === 0 ? (
        <div style={{ border: '1px solid var(--ok)', background: 'var(--ok-soft)', padding: '20px 16px' }}>
          <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 16, color: 'var(--ink)' }}>All workstreams on plan</div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)', marginTop: 6 }}>
            No workstream is &gt;10% over plan. A new overrun auto-flags here and routes to the Decision Queue.
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {alerts.map((r) => {
            const overPct = r.planned > 0 ? ((r.actual - r.planned) / r.planned) * 100 : 0;
            return (
              <Link
                key={r.workstream}
                href="/decision"
                style={{
                  textDecoration: 'none',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 14,
                  padding: '14px 16px',
                  background: 'var(--signal-soft)',
                  border: '1px solid var(--signal)',
                  transition: 'background .15s var(--ease)',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-soft)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--signal-soft)')}
              >
                <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--signal)', color: 'var(--on-signal)', whiteSpace: 'nowrap' }}>
                  ⚑ {overPct > 0 ? `+${fmt1(overPct)}%` : 'AT RISK'}
                </span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 600 }}>{wsName(r.workstream)}</div>
                  <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-2)', marginTop: 2 }}>
                    planned {fmtK(r.planned)} · actual {fmtK(r.actual)} · {r.remaining < 0 ? `${fmtK(Math.abs(r.remaining))} over` : `${fmtK(r.remaining)} left`}
                  </div>
                </div>
                <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--signal)', fontWeight: 600, whiteSpace: 'nowrap' }}>auto-flagged → Decision Queue</span>
              </Link>
            );
          })}
        </div>
      )}

      <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', marginTop: 14, lineHeight: 1.6 }}>
        ⌖ A workstream &gt;10% over plan emits exactly one open <b>budget_variance</b> decision; once leadership decides it, a fresh overrun re-flags. Health bands (on-track / watch / at-risk) come straight from the reconcile.
      </div>
    </section>
  );
}

// ============================ shared bits ====================================
function orderedRows(data: BudgetResponse): BudgetWorkstream[] {
  const ws = data.workstreams ?? [];
  const rank = (t: string) => {
    const i = WORKSTREAM_ORDER.indexOf(t);
    return i === -1 ? WORKSTREAM_ORDER.length : i;
  };
  return [...ws].sort((a, b) => rank(a.workstream) - rank(b.workstream));
}

const HEALTH_STYLE: Record<BudgetHealth, { label: string; bg: string; color: string }> = {
  on_track: { label: 'ON TRACK', bg: 'var(--ok-soft)', color: 'var(--ok)' },
  watch: { label: 'WATCH', bg: 'var(--warn-soft)', color: 'var(--warn)' },
  at_risk: { label: 'AT RISK', bg: 'var(--signal-soft)', color: 'var(--signal)' },
};
function HealthChip({ health }: { health: BudgetHealth }) {
  const s = HEALTH_STYLE[health] ?? HEALTH_STYLE.on_track;
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: s.bg, color: s.color, whiteSpace: 'nowrap' }}>
      {s.label}
    </span>
  );
}

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const ok = toast.kind === 'ok';
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        margin: '12px 22px 0',
        padding: '10px 14px',
        background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)',
        border: `1px solid ${ok ? 'var(--ok)' : 'var(--signal)'}`,
      }}
    >
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: ok ? 'var(--ok)' : 'var(--signal)' }}>{ok ? '✓ DONE' : '⚠ ERROR'}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink)' }}>{toast.msg}</span>
      <button onClick={onClose} aria-label="Dismiss" style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>✕</button>
    </div>
  );
}

function StatusPill({ live }: { live: boolean }) {
  return (
    <span
      style={{
        fontFamily: MONO,
        fontSize: 9,
        fontWeight: 600,
        letterSpacing: '.4px',
        padding: '3px 8px',
        borderRadius: 2,
        whiteSpace: 'nowrap',
        color: live ? 'var(--ok)' : 'var(--ink-3)',
        background: live ? 'var(--ok-soft)' : 'var(--accent-soft)',
      }}
    >
      {live ? '● LIVE' : '○ SAMPLE'}
    </span>
  );
}

function SumCard({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>{label}</div>
      <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 30, lineHeight: 1.05, marginTop: 7, color: 'var(--ink)' }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{note}</div>
    </div>
  );
}

// ---- shared style objects ---------------------------------------------------
const CARD: React.CSSProperties = { border: '1px solid var(--line-2)', background: 'var(--card)' };
const CARD_HEAD: React.CSSProperties = {
  padding: '10px 16px',
  borderBottom: '2px solid var(--ink)',
  background: 'var(--ink)',
  color: 'var(--paper)',
  fontFamily: 'Fraunces',
  fontWeight: 700,
  fontSize: 13,
  letterSpacing: '.3px',
};
const FIELD_LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 };
const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2 };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2 };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };

// ---- Money / number formatting ---------------------------------------------
function fmtK(n: number): string {
  return `$${(n / 1000).toFixed(1)}K`;
}
function fmtFull(n: number): string {
  return `$${Math.round(n).toLocaleString('en-US')}`;
}
function fmt1(n: number): string {
  return n.toFixed(1);
}
function pctOf(part: number, whole: number): number {
  return whole > 0 ? (part / whole) * 100 : 0;
}
function fmtDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
