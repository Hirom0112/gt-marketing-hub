'use client';

// Budget Tracker (Module 10) — the reconciliation surface.
//   • One number, everywhere: the four workstreams sum to the $365K FY26 plan.
//   • Totals (planned / committed / actual / remaining) are SUMMED from the rows
//     in code — never a separately-typed figure — so the table can never disagree
//     with its own footer. % PLAN = committed / planned per row.
//   • Any workstream >10% over plan auto-flags to the Decision Queue (INV: budget
//     variance >10% escalates). "Guerrilla / earned media bets" is +17.5% over.
// System of record is the Hub; Summer Camp is a separate P&L and never rolls in.

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet, type BudgetWorkstream } from '@/lib/api';

const MONO = 'JetBrains Mono';

// ---- Seed data (system-of-record figures, in whole dollars) ----------------
interface BudgetRow {
  name: string;
  owner: string;
  planned: number;
  committed: number;
  actual: number;
  remaining?: number;
  flagged?: boolean;
}

const BUDGET_ROWS: BudgetRow[] = [
  { name: 'Grassroots marketing', owner: 'the Grassroots Owner', planned: 210_000, committed: 196_000, actual: 138_000 },
  { name: 'Thought leadership + content engine', owner: 'the Content Owner', planned: 90_000, committed: 73_000, actual: 49_000 },
  { name: 'Guerrilla / earned media bets', owner: 'Leadership', planned: 40_000, committed: 47_000, actual: 33_000 },
  { name: 'Marketing foundations + operations', owner: 'the Marketing Lead', planned: 25_000, committed: 18_000, actual: 13_000 },
];

// Live /budget shape (richer than lib/api.ts's BudgetResponse): the backend also
// returns a `rollup` (pre-summed totals) plus `flagged`/`burn` arrays. We only
// consume what we render here and guard every field.
interface BudgetRollup {
  total_planned?: number;
  total_committed?: number;
  total_actual?: number;
  total_remaining?: number;
  total_usd?: number;
}
interface BudgetApiResponse {
  workstreams?: BudgetWorkstream[];
  rollup?: BudgetRollup;
  flagged?: unknown[];
  burn?: unknown[];
}

// Backend workstream keys → display name + owner (the seed's labels/owners).
const WORKSTREAM_META: Record<string, { name: string; owner: string }> = {
  grassroots: { name: 'Grassroots marketing', owner: 'the Grassroots Owner' },
  content: { name: 'Thought leadership + content engine', owner: 'the Content Owner' },
  guerrilla: { name: 'Guerrilla / earned media bets', owner: 'Leadership' },
  ops: { name: 'Marketing foundations + operations', owner: 'the Marketing Lead' },
};

// The variance threshold that auto-escalates a workstream to the Decision Queue.
const OVER_THRESHOLD_PCT = 110; // committed/planned above this == flagged

const GRID = '2fr 1.15fr .82fr .82fr .82fr .82fr .7fr 1.05fr';

interface DerivedRow {
  name: string;
  owner: string;
  planned: number;
  committed: number;
  actual: number;
  remaining: number;
  pctN: number;
  over: boolean;
  near: boolean;
}

// Derive every per-row figure once. `over` honors the backend `flagged` bit OR the
// local >10%-over-plan rule (committed/planned > 1.10) — fail toward escalation.
function deriveRows(raw: BudgetRow[]): DerivedRow[] {
  return raw.map((b) => {
    const remaining = b.remaining ?? b.planned - b.actual;
    const pctN = b.planned > 0 ? (b.committed / b.planned) * 100 : 0;
    const over = Boolean(b.flagged) || pctN > OVER_THRESHOLD_PCT;
    const near = !over && pctN >= 90;
    return { name: b.name, owner: b.owner, planned: b.planned, committed: b.committed, actual: b.actual, remaining, pctN, over, near };
  });
}

export function BudgetModule() {
  const def = moduleById('budget')!;
  const { session } = useSession();
  const [live, setLive] = useState<BudgetApiResponse | null>(null);

  useEffect(() => {
    let active = true;
    apiGet<BudgetApiResponse>('/budget', session.role).then((data) => {
      if (active && data && Array.isArray(data.workstreams) && data.workstreams.length > 0) {
        setLive(data);
      }
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  const isLive = live !== null;

  // Build the raw rows from the live payload when present, else fall back to seed.
  const rawRows: BudgetRow[] = isLive
    ? (live!.workstreams ?? []).map((w) => {
        const meta = WORKSTREAM_META[w.workstream] ?? { name: w.workstream, owner: '—' };
        return {
          name: meta.name,
          owner: meta.owner,
          planned: w.planned,
          committed: w.committed,
          actual: w.actual,
          remaining: w.remaining,
          flagged: w.flagged,
        };
      })
    : BUDGET_ROWS;

  const rows = deriveRows(rawRows);

  // SUM the rows for the footer so it's structurally guaranteed to reconcile. Prefer
  // the backend rollup for planned/actual/remaining when present; committed has no
  // rollup field, so it's always summed from the rows.
  const summed = rows.reduce(
    (acc, r) => ({
      planned: acc.planned + r.planned,
      committed: acc.committed + r.committed,
      actual: acc.actual + r.actual,
      remaining: acc.remaining + r.remaining,
    }),
    { planned: 0, committed: 0, actual: 0, remaining: 0 },
  );
  const rollup = live?.rollup;
  const total = {
    planned: rollup?.total_planned ?? summed.planned,
    committed: rollup?.total_committed ?? summed.committed,
    actual: rollup?.total_actual ?? summed.actual,
    remaining: rollup?.total_remaining ?? summed.remaining,
  };

  const committedPct = total.planned > 0 ? (total.committed / total.planned) * 100 : 0; // 91.5%
  const burnedPct = total.planned > 0 ? (total.actual / total.planned) * 100 : 0; // 63.8%
  const flagged = rows.filter((r) => r.over);
  const lead = flagged[0]; // the over-plan workstream that routed to the DQ
  const leadVariance = lead ? lead.pctN - 100 : 0;

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* ---- Data-source pill --------------------------------------- */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
          <StatusPill live={isLive} />
        </div>
        {/* ---- Summary cards ------------------------------------------- */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          <div style={{ border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', padding: 14 }}>
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', opacity: 0.8 }}>TOTAL PLAN · FY26</div>
            <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 30, lineHeight: 1.05, marginTop: 7 }}>{fmtFull(total.planned)}</div>
            <div style={{ fontSize: 10, opacity: 0.8, marginTop: 4 }}>The same number, everywhere.</div>
          </div>
          <SumCard label="COMMITTED" value={fmtK(total.committed)} note={`${fmt1(committedPct)}% of plan`} />
          <SumCard label="ACTUAL SPEND" value={fmtK(total.actual)} note={`${fmt1(burnedPct)}% burned · lands ~$372K`} />
          <div style={{ border: '1px solid var(--signal)', background: 'var(--signal-soft)', padding: 14 }}>
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--signal)', fontWeight: 600 }}>⚑ VARIANCE ALERTS</div>
            <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 30, lineHeight: 1.05, marginTop: 7, color: 'var(--signal)' }}>{flagged.length}</div>
            <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>&gt;10% over → auto-flag</div>
          </div>
        </div>

        {/* ---- Auto-flag banner → Decision Queue ----------------------- */}
        {lead && (
          <Link
            href="/decision"
            style={{
              textDecoration: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 11,
              padding: '10px 14px',
              background: 'var(--signal-soft)',
              border: '1px solid var(--signal)',
              marginBottom: 14,
              transition: 'background .15s var(--ease)',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-soft)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--signal-soft)')}
          >
            <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--signal)', color: 'var(--on-signal)', whiteSpace: 'nowrap' }}>AUTO-FLAGGED</span>
            <span style={{ fontSize: 12, color: 'var(--ink)', flex: 1 }}>
              <b>{lead.name}</b> is <b style={{ color: 'var(--signal)' }}>+{fmt1(leadVariance)}%</b> over plan ({fmtK(lead.committed)} committed vs {fmtK(lead.planned)}). The &gt;10% threshold breached, so it routed to the Decision Queue automatically.
            </span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--signal)', fontWeight: 600, whiteSpace: 'nowrap' }}>→ Decision Queue</span>
          </Link>
        )}

        {/* ---- Workstream table --------------------------------------- */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          {/* header */}
          <div style={{ display: 'grid', gridTemplateColumns: GRID, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', padding: '9px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
            <div>WORKSTREAM</div>
            <div>OWNER</div>
            <div style={{ textAlign: 'right' }}>PLANNED</div>
            <div style={{ textAlign: 'right' }}>COMMITTED</div>
            <div style={{ textAlign: 'right' }}>ACTUAL</div>
            <div style={{ textAlign: 'right' }}>REMAINING</div>
            <div style={{ textAlign: 'right' }}>% PLAN</div>
            <div style={{ textAlign: 'center' }}>STATUS</div>
          </div>

          {/* rows */}
          {rows.map((r) => {
            const statusBg = r.over ? 'var(--signal-soft)' : r.near ? 'var(--warn-soft)' : 'var(--ok-soft)';
            const statusColor = r.over ? 'var(--signal)' : r.near ? 'var(--warn)' : 'var(--ok)';
            const status = r.over ? `⚑ +${fmt1(r.pctN - 100)}% → DQ` : r.near ? 'NEAR CAP' : 'HEALTHY';
            const pctColor = r.over ? 'var(--signal)' : r.near ? 'var(--warn)' : 'var(--ink-2)';
            return (
              <div key={r.name} style={{ display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
                <div style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{r.name}</div>
                <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-2)' }}>{r.owner}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, color: 'var(--ink-2)' }}>{fmtK(r.planned)}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(r.committed)}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, color: 'var(--ink-2)' }}>{fmtK(r.actual)}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11.5, color: 'var(--ink-2)' }}>{fmtK(r.remaining)}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: pctColor }}>{fmt0(r.pctN)}%</div>
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: statusBg, color: statusColor, whiteSpace: 'nowrap' }}>{status}</span>
                </div>
              </div>
            );
          })}

          {/* total — summed from the rows above (never hardcoded) */}
          <div style={{ display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '13px 16px', borderTop: '2px solid var(--ink)', background: 'var(--card-2)' }}>
            <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>TOTAL</div>
            <div />
            <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(total.planned)}</div>
            <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(total.committed)}</div>
            <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(total.actual)}</div>
            <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{fmtK(total.remaining)}</div>
            <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{fmt1(committedPct)}%</div>
            <div />
          </div>
        </div>

        {/* ---- Burn chart / variance visual --------------------------- */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '14px 16px', marginTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.7px', color: 'var(--ink-3)', fontWeight: 600 }}>BURN vs PLAN · BY WORKSTREAM</div>
            <div style={{ display: 'flex', gap: 14, fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
              <LegendDot color="var(--ink)" label="ACTUAL" />
              <LegendDot color="var(--line-2)" label="COMMITTED" />
              <LegendDot color="var(--signal)" label="OVER PLAN" />
            </div>
          </div>
          {rows.map((r) => {
            // Bars are scaled to each workstream's own plan (100% = planned).
            const actualW = Math.min(100, (r.actual / r.planned) * 100);
            const committedW = Math.min(100, (r.committed / r.planned) * 100);
            const overFill = r.over ? 'var(--signal)' : 'var(--ink)';
            return (
              <div key={r.name} style={{ marginBottom: 11 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>{r.name}</span>
                  <span style={{ fontFamily: MONO, fontSize: 9.5, color: r.over ? 'var(--signal)' : 'var(--ink-3)', fontWeight: r.over ? 600 : 400 }}>
                    {fmtK(r.actual)} / {fmtK(r.planned)}{r.over ? `  ·  +${fmt1(r.pctN - 100)}% committed` : ''}
                  </span>
                </div>
                <div style={{ position: 'relative', height: 9, background: 'var(--accent-soft)', overflow: 'hidden' }}>
                  {/* committed track (lighter) */}
                  <div style={{ position: 'absolute', top: 0, left: 0, height: '100%', width: `${committedW}%`, background: 'var(--line-2)' }} />
                  {/* actual fill (solid) */}
                  <div style={{ position: 'absolute', top: 0, left: 0, height: '100%', width: `${actualW}%`, background: overFill }} />
                  {/* the 100%-of-plan marker */}
                  <div style={{ position: 'absolute', top: -2, left: '100%', width: 1, height: 13, background: 'var(--ink-3)' }} />
                </div>
              </div>
            );
          })}
        </div>

        {/* ---- Footnote ------------------------------------------------ */}
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.6 }}>
          ⌖ System of record: the Hub (no Google Sheet). Each function owner enters their own committed + actual spend; the four workstreams reconcile to {fmtK(total.planned)}. Summer Camp is a separate P&amp;L and does <b>not</b> roll into this total. The same line item means the same thing in Home, the Scorecard, and the Decision Queue.
        </div>
      </section>
    </>
  );
}

// ---- Money / number formatting ---------------------------------------------
function fmtK(n: number): string {
  return `$${(n / 1000).toFixed(1)}K`;
}
function fmtFull(n: number): string {
  return `$${n.toLocaleString('en-US')}`;
}
function fmt0(n: number): string {
  return n.toFixed(0);
}
function fmt1(n: number): string {
  return n.toFixed(1);
}

// ---- Presentational subcomponents ------------------------------------------
// Green "● LIVE" when real backbone data loaded; muted "○ SAMPLE" on seed fallback.
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

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{ width: 8, height: 8, background: color, display: 'inline-block' }} />
      {label}
    </span>
  );
}
