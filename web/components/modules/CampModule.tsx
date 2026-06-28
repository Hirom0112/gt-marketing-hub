'use client';

// Summer Camp (Module 4) — a SEPARATE P&L workstream, not part of the $365K
// marketing budget. Registrations reconcile two sources with no double-count:
//   • summer.gt.school (primary) + a standalone registration form → dedup'd.
//   • Funnel: Lead → Registered (unpaid) → Paid → Attended, sliceable by
//     campus / age group. Four campuses (3× two-week, 1× one-week) sum to the
//     aggregate capacity of 350 seats.
//   • Ads are paused — there is intentionally NO paid-acquisition view.
// Live wiring: GET /summer/reconcile drives the dedup banner, the per-campus
// rollup, the funnel, and revenue-vs-target — the REAL reconciler output (each
// registrant counted once; ambiguity held for review). Falls back to the inline
// seed when the backbone is unreachable (the BudgetModule apiGet pattern).

import { useEffect, useState } from 'react';
import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet } from '@/lib/api';

const MONO = 'JetBrains Mono';
const ARCHIVO = 'Fraunces';

// ---- Types -----------------------------------------------------------------
interface Stat {
  label: string;
  value: string;
  valueSub?: string; // small inline qualifier (e.g. "/350")
  valueColor: string;
  note: string;
  hero?: boolean;
}
interface FunnelStage {
  stage: string;
  count: number;
  drop: string; // drop-off into this stage from the prior one
  color: string; // bar fill token
}
interface Campus {
  name: string;
  city: string;
  dates: string;
  duration: '1wk' | '2wk';
  capacity: number;
  registered: number;
  paid: number;
  waitlist: number;
}

// Live GET /summer/reconcile shape (app/api/summer.py). Every field guarded.
interface SummerCampusRow {
  campus: string;
  capacity: number;
  registered: number;
  paid: number;
  lead: number;
  seats_remaining: number;
  pct_sold: number;
}
interface SummerReconcileResponse {
  program_id?: string;
  per_campus?: SummerCampusRow[];
  totals?: { capacity: number; registered: number; paid: number; lead: number };
  dedup?: {
    raw_source_rows: number;
    unique_registrations: number;
    duplicates_merged: number;
    sources?: { source: string; rows: number }[];
    conflicts?: unknown[];
  };
  revenue?: {
    paid_registrations: number;
    price_per_seat_usd: number;
    revenue_usd: number;
    target_usd: number;
    pct_to_target: number;
  };
}

// ---- Seed data -------------------------------------------------------------
// Per-campus numbers sum to the aggregate (capacity 350 / registered 288 /
// paid 219 / waitlist 21) so the dual-source reconciliation holds. Also the
// source of stable per-campus META (city / dates / duration) the live rollup
// (which carries only the numbers) is decorated with.
const CAMPUSES: Campus[] = [
  { name: 'Austin', city: 'Mueller campus', dates: 'Jun 16 – Jun 27', duration: '2wk', capacity: 100, registered: 86, paid: 66, waitlist: 8 },
  { name: 'Dallas', city: 'Knox–Henderson campus', dates: 'Jul 7 – Jul 18', duration: '2wk', capacity: 100, registered: 84, paid: 63, waitlist: 6 },
  { name: 'Houston', city: 'Heights campus', dates: 'Jul 21 – Aug 1', duration: '2wk', capacity: 90, registered: 78, paid: 60, waitlist: 5 },
  { name: 'San Antonio', city: 'Pearl campus', dates: 'Aug 4 – Aug 8', duration: '1wk', capacity: 60, registered: 40, paid: 30, waitlist: 2 },
];

// Stable per-campus presentation meta (the reconciler carries only the numbers).
const CAMPUS_META: Record<string, { city: string; dates: string; duration: '1wk' | '2wk' }> =
  Object.fromEntries(CAMPUSES.map((c) => [c.name, { city: c.city, dates: c.dates, duration: c.duration }]));

const SEED_LEAD = 642; // top-of-funnel leads (no lead-source in the reconciler — seed)
const SEED_REVENUE_TARGET = 260_000;

// ---- Component -------------------------------------------------------------
export function CampModule() {
  const def = moduleById('camp')!;
  const { session } = useSession();
  const [live, setLive] = useState<SummerReconcileResponse | null>(null);

  useEffect(() => {
    let active = true;
    apiGet<SummerReconcileResponse>('/summer/reconcile', session.role).then((data) => {
      if (active && data && Array.isArray(data.per_campus) && data.per_campus.length > 0) {
        setLive(data);
      }
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  const isLive = live !== null;

  // Per-campus rollup: decorate the live numbers with stable meta, else the seed.
  const campuses: Campus[] = isLive
    ? live!.per_campus!.map((pc) => {
        const meta = CAMPUS_META[pc.campus] ?? { city: '—', dates: '', duration: '2wk' as const };
        return {
          name: pc.campus,
          city: meta.city,
          dates: meta.dates,
          duration: meta.duration,
          capacity: pc.capacity,
          registered: pc.registered,
          paid: pc.paid,
          // Overflow above capacity is the waitlist (0 when under capacity).
          waitlist: Math.max(0, pc.registered - pc.capacity),
        };
      })
    : CAMPUSES;

  const agg = campuses.reduce(
    (a, c) => ({
      capacity: a.capacity + c.capacity,
      registered: a.registered + c.registered,
      paid: a.paid + c.paid,
      waitlist: a.waitlist + c.waitlist,
    }),
    { capacity: 0, registered: 0, paid: 0, waitlist: 0 },
  );

  // Dedup banner figures — REAL reconciler output when live.
  const dupMerged = live?.dedup?.duplicates_merged ?? 0;
  const conflicts = live?.dedup?.conflicts?.length ?? 0;
  const uniqueReg = live?.dedup?.unique_registrations ?? agg.registered;

  // Revenue vs target.
  const revenueUsd = live?.revenue?.revenue_usd ?? 214_000;
  const revenueTarget = live?.revenue?.target_usd ?? SEED_REVENUE_TARGET;
  const revenuePct = live?.revenue?.pct_to_target ?? Math.round((revenueUsd / revenueTarget) * 100);

  const stats = buildStats(agg, revenueUsd, revenueTarget, revenuePct);

  // Funnel — Registered / Paid from the live totals; Lead is seed (no source).
  const leadCount = SEED_LEAD;
  const regCount = live?.totals?.registered ?? agg.registered;
  const paidCount = live?.totals?.paid ?? agg.paid;
  const funnel: FunnelStage[] = [
    { stage: 'Lead', count: leadCount, drop: '—', color: 'var(--ink-3)' },
    { stage: 'Registered (unpaid)', count: regCount, drop: pctDrop(leadCount, regCount), color: 'var(--gold)' },
    { stage: 'Paid', count: paidCount, drop: pctDrop(regCount, paidCount), color: 'var(--ok)' },
    { stage: 'Attended', count: 0, drop: 'pending', color: 'var(--gold)' },
  ];
  const funnelMax = Math.max(...funnel.map((s) => s.count));

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* Data-source pill */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
          <StatusPill live={isLive} />
        </div>

        {/* Dual-source reconciliation banner */}
        <div
          style={{
            border: '1px solid var(--ink)',
            background: 'var(--card)',
            padding: '13px 16px',
            marginBottom: 14,
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <div style={{ flex: 1, minWidth: 320 }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>
              Registrations reconcile two sources — no double-counting
            </div>
            <div style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 3, lineHeight: 1.5, maxWidth: 640 }}>
              <b>summer.gt.school</b> (primary) is merged with the standalone <b>registration form</b>;
              records are deduplicated on the household identity key before any count is shown
              {isLive && (
                <>
                  {' '}— <b>{dupMerged}</b> duplicate {dupMerged === 1 ? 'appearance' : 'appearances'} folded across both sources
                  {conflicts > 0 && (
                    <>
                      , <b style={{ color: 'var(--warn)' }}>{conflicts}</b> held for review
                    </>
                  )}
                </>
              )}
              . Summer Camp is a <b>separate P&amp;L</b> — it does <b>not</b> roll into the $365K marketing budget.
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
            <span
              style={{
                fontFamily: MONO,
                fontSize: 9.5,
                fontWeight: 600,
                padding: '4px 10px',
                background: conflicts > 0 ? 'var(--warn-soft)' : 'var(--ok-soft)',
                color: conflicts > 0 ? 'var(--warn)' : 'var(--ok)',
              }}
            >
              {conflicts > 0
                ? `⚑ RECONCILED · ${conflicts} TO REVIEW`
                : `✓ RECONCILED · ${dupMerged} MERGED · 0 DUPLICATES`}
            </span>
            <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
              summer.gt.school ⊕ reg form → {uniqueReg} unique
            </span>
          </div>
        </div>

        {/* Overview stat grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
          {stats.map((s) => (
            <div
              key={s.label}
              style={{
                border: `1px solid ${s.hero ? 'var(--ink)' : 'var(--line-2)'}`,
                background: s.hero ? 'var(--card-2)' : 'var(--card)',
                padding: 13,
              }}
            >
              <div
                style={{
                  fontFamily: MONO,
                  fontSize: 9,
                  letterSpacing: '.4px',
                  color: s.hero ? 'var(--ink)' : 'var(--ink-3)',
                  fontWeight: s.hero ? 600 : 400,
                }}
              >
                {s.label}
              </div>
              <div style={{ fontFamily: s.hero ? ARCHIVO : MONO, fontWeight: s.hero ? 700 : 600, fontSize: s.hero ? 27 : 22, color: s.valueColor, marginTop: 5, lineHeight: 1.05 }}>
                {s.value}
                {s.valueSub && <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)', fontWeight: 400 }}> {s.valueSub}</span>}
              </div>
              <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 2 }}>{s.note}</div>
            </div>
          ))}
          {/* No paid-acquisition note rides the trailing grid cell */}
          <div
            style={{
              border: '1px dashed var(--line-2)',
              background: 'var(--paper)',
              padding: 13,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
            }}
          >
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
              ⏸ NO PAID-ACQUISITION VIEW
            </div>
            <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.4 }}>
              Ads are paused for camp — growth is organic + referral only.
            </div>
          </div>
        </div>

        {/* Registration funnel */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '10px 16px',
              borderBottom: '2px solid var(--ink)',
            }}
          >
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Registration funnel</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>
              sliceable by campus / age group · Lead → Registered → Paid → Attended
            </span>
          </div>
          <div style={{ padding: '14px 16px', display: 'grid', gap: 9 }}>
            {funnel.map((s) => {
              const pct = Math.round((s.count / funnelMax) * 100);
              return (
                <div key={s.stage} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ width: 138, fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{s.stage}</div>
                  <div style={{ flex: 1, height: 22, background: 'var(--card-2)', position: 'relative', overflow: 'hidden' }}>
                    <div style={{ width: `${Math.max(pct, 2)}%`, height: '100%', background: s.color, opacity: 0.85 }} />
                  </div>
                  <div style={{ width: 56, textAlign: 'right', fontFamily: MONO, fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
                    {s.count}
                  </div>
                  <div
                    style={{
                      width: 56,
                      textAlign: 'right',
                      fontFamily: MONO,
                      fontSize: 10,
                      color: s.drop.indexOf('−') === 0 ? 'var(--warn)' : 'var(--ink-3)',
                    }}
                  >
                    {s.drop}
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ padding: '0 16px 12px', fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
            Drop-off shown per stage · Attended fills as sessions run.
          </div>
        </div>

        {/* Sessions — four campus cards */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            borderBottom: '1px solid var(--line)',
            paddingBottom: 8,
            marginBottom: 12,
          }}
        >
          <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 14, color: 'var(--ink)' }}>Sessions</div>
          <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
            3 campuses · 2-week · 1 campus · 1-week — sums to {agg.registered} reg / {agg.capacity} seats
          </span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          {campuses.map((c) => (
            <CampusCard key={c.name} c={c} />
          ))}
        </div>
      </section>
    </>
  );
}

// ---- Stat builder ----------------------------------------------------------
// Dynamic stats are SUMMED from the reconciled per-campus rollup (live or seed) so
// they can never disagree with the campus cards; the rest are static demo context.
function buildStats(
  agg: { capacity: number; registered: number; paid: number; waitlist: number },
  revenueUsd: number,
  revenueTarget: number,
  revenuePct: number,
): Stat[] {
  const capPct = agg.capacity > 0 ? Math.round((agg.registered / agg.capacity) * 100) : 0;
  const paidPct = agg.registered > 0 ? Math.round((agg.paid / agg.registered) * 100) : 0;
  return [
    {
      label: 'CAPACITY SOLD',
      value: `${capPct}%`,
      valueSub: `${agg.registered} / ${agg.capacity} seats`,
      valueColor: 'var(--ink)',
      note: 'across 4 campuses · reconciled',
      hero: true,
    },
    { label: 'REGISTRATIONS THIS WEEK', value: '34', valueColor: 'var(--ink)', note: '+11 vs prior week' },
    { label: 'REGISTERED → PAID', value: `${paidPct}%`, valueColor: 'var(--ok)', note: `${agg.paid} paid of ${agg.registered} registered` },
    { label: 'DAYS TO CAMP START', value: '38', valueColor: 'var(--ink)', note: 'first session · Austin' },
    { label: 'TOP SIGNUP CHANNEL', value: 'Organic', valueColor: 'var(--ink)', note: 'summer.gt.school direct · 41%' },
    {
      label: 'REVENUE',
      value: `$${Math.round(revenueUsd / 1000)}K`,
      valueSub: `/ $${Math.round(revenueTarget / 1000)}K target`,
      valueColor: 'var(--ink)',
      note: `${revenuePct}% to target · separate P&L`,
    },
    { label: 'WAITLIST / OVERFLOW', value: `${agg.waitlist}`, valueColor: agg.waitlist > 0 ? 'var(--warn)' : 'var(--ink-3)', note: 'across full sessions' },
  ];
}

function pctDrop(prev: number, next: number): string {
  if (prev <= 0) return '—';
  return `−${Math.round((1 - next / prev) * 100)}%`;
}

// ---- Subcomponents ---------------------------------------------------------
function CampusCard({ c }: { c: Campus }) {
  const sold = c.capacity > 0 ? Math.round((c.registered / c.capacity) * 100) : 0;
  const twoWeek = c.duration === '2wk';
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
      <div style={{ padding: '12px 13px 10px', borderBottom: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 14, color: 'var(--ink)' }}>{c.name}</span>
          <span
            style={{
              fontFamily: MONO,
              fontSize: 8.5,
              fontWeight: 600,
              padding: '2px 7px',
              background: twoWeek ? 'var(--gold-soft)' : 'var(--accent-soft)',
              color: twoWeek ? 'var(--gold)' : 'var(--ink-2)',
            }}
          >
            {twoWeek ? '2-WEEK' : '1-WEEK'}
          </span>
        </div>
        <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>{c.city}</div>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-2)', marginTop: 4 }}>{c.dates}</div>
      </div>

      {/* capacity bar */}
      <div style={{ padding: '11px 13px 9px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', fontWeight: 600 }}>CAPACITY SOLD</span>
          <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{sold}%</span>
        </div>
        <div style={{ height: 6, background: 'var(--card-2)', overflow: 'hidden' }}>
          <div style={{ width: `${sold}%`, height: '100%', background: 'var(--gold)', opacity: 0.9 }} />
        </div>
      </div>

      {/* per-campus numbers */}
      <div style={{ padding: '4px 13px 13px', display: 'grid', gap: 6 }}>
        <CardRow label="Capacity" value={c.capacity} />
        <CardRow label="Registered" value={c.registered} valueColor="var(--ink)" strong />
        <CardRow label="Paid" value={c.paid} valueColor="var(--ok)" />
        <CardRow label="Waitlist" value={c.waitlist} valueColor={c.waitlist > 0 ? 'var(--warn)' : 'var(--ink-3)'} />
      </div>
    </div>
  );
}

function CardRow({
  label,
  value,
  valueColor = 'var(--ink-2)',
  strong = false,
}: {
  label: string;
  value: number;
  valueColor?: string;
  strong?: boolean;
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
      <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: strong ? 700 : 600, color: valueColor }}>{value}</span>
    </div>
  );
}

// Green "● LIVE" when the reconciler responded; muted "○ SAMPLE" on seed fallback.
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
