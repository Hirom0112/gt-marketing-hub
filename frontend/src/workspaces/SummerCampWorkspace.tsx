import {
  Card,
  Chip,
  KpiCard,
  PlaceholderBadge,
} from '../ui';

// Module 4 — Summer Camp (GT Marketing Hub spec §3, Module 4). Registrations,
// capacity, and revenue across 4 campuses (3 running 2-week sessions, 1 running a
// 1-week session). A SEPARATE P&L line and a SEPARATE program from the Fall push —
// this is the product face of Phase-1 program isolation (the `summer_camp` program,
// never bleeding into `fall_enrollment`). Registration truth is dual-source
// (summer.gt.school + a registration form, reconciled to avoid double-counting);
// both are seeded stand-ins here and labeled SIMULATED (INV-9 honesty).

interface Campus {
  name: string;
  weeks: 1 | 2;
  dates: string;
  capacity: number;
  registered: number;
  paid: number;
  waitlist: number;
}

// Seeded — Summer 2026, 4 campuses (3x 2-week, 1x 1-week).
const CAMPUSES: readonly Campus[] = [
  { name: 'Austin', weeks: 2, dates: 'Jul 15 to Jul 26', capacity: 60, registered: 54, paid: 48, waitlist: 6 },
  { name: 'Dallas', weeks: 2, dates: 'Jul 15 to Jul 26', capacity: 60, registered: 51, paid: 44, waitlist: 3 },
  { name: 'Houston', weeks: 2, dates: 'Jul 22 to Aug 2', capacity: 50, registered: 50, paid: 47, waitlist: 12 },
  { name: 'San Antonio', weeks: 1, dates: 'Aug 5 to Aug 9', capacity: 40, registered: 28, paid: 22, waitlist: 0 },
];

const REVENUE_PER_FAMILY = 1800; // whole USD, seeded
const DAYS_TO_START = 18; // countdown, from config (today 2026-06-27)

// Registration funnel (spec 4b): Lead -> Registered (unpaid) -> Paid -> Attended.
const FUNNEL = [
  { stage: 'Lead', count: 420 },
  { stage: 'Registered', count: 183 },
  { stage: 'Paid', count: 161 },
  { stage: 'Attended', count: 0, projected: true },
] as const;

const cap = CAMPUSES.reduce((a, c) => a + c.capacity, 0);
const reg = CAMPUSES.reduce((a, c) => a + c.registered, 0);
const paid = CAMPUSES.reduce((a, c) => a + c.paid, 0);
const waitlist = CAMPUSES.reduce((a, c) => a + c.waitlist, 0);
const revenue = paid * REVENUE_PER_FAMILY;
const revenueTarget = cap * REVENUE_PER_FAMILY;

function pct(n: number, total: number): number {
  return total === 0 ? 0 : Math.round((n / total) * 100);
}

function MiniBar({ value, tone = 'flow' }: { value: number; tone?: 'flow' | 'ink' }): JSX.Element {
  return (
    <div style={{ height: 6, width: '100%', background: 'var(--line-2)', borderRadius: 999, overflow: 'hidden' }}>
      <div style={{ height: '100%', width: `${value}%`, background: tone === 'ink' ? 'var(--ink-soft)' : 'var(--flow)' }} />
    </div>
  );
}

const cellHead = {
  textAlign: 'left',
  padding: 'var(--s-2) var(--s-3)',
  fontSize: 11,
  letterSpacing: '0.04em',
  color: 'var(--ink-soft)',
  borderBottom: '1px solid var(--line)',
} as const;
const cell = {
  padding: 'var(--s-3)',
  borderBottom: '1px solid var(--line-2)',
  fontSize: 13,
} as const;

export default function SummerCampWorkspace(): JSX.Element {
  return (
    <section
      aria-label="Summer Camp workspace"
      data-testid="summer-camp-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)', maxWidth: 980 }}
    >
      <header style={{ display: 'grid', gap: 'var(--s-2)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-3)', flexWrap: 'wrap' }}>
          <h1 style={{ margin: 0 }}>Summer Camp</h1>
          <PlaceholderBadge label="SIMULATED summer.gt.school" />
        </div>
        <p style={{ margin: 0, color: 'var(--ink-soft)', maxWidth: '64ch' }}>
          Four campuses, Summer 2026. A separate program and P&L from Fall
          enrollment: the product face of Phase-1 program isolation
          (summer_camp, never crossing into fall_enrollment). Registration truth
          reconciles summer.gt.school and the registration form, no
          double-counting.
        </p>
      </header>

      {/* KPI strip. */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
          gap: 'var(--s-3)',
        }}
      >
        <KpiCard label="Capacity sold" value={`${pct(paid, cap)}%`} note={`${paid} of ${cap} seats`} />
        <KpiCard label="Registered to paid" value={`${pct(paid, reg)}%`} note={`${reg} registered`} />
        <KpiCard label="Revenue vs target" value={`$${(revenue / 1000).toFixed(0)}K`} note={`of $${(revenueTarget / 1000).toFixed(0)}K`} />
        <KpiCard label="Days to camp start" value={String(DAYS_TO_START)} note="Austin · Dallas" />
        <KpiCard label="Waitlist" value={String(waitlist)} tone={waitlist > 15 ? 'signal' : 'neutral'} note="across campuses" />
      </div>

      {/* Registration funnel. */}
      <Card>
        <p className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)', margin: '0 0 var(--s-3)' }}>
          REGISTRATION FUNNEL
        </p>
        <div style={{ display: 'grid', gap: 'var(--s-3)' }}>
          {FUNNEL.map((f, i) => {
            const prevItem = FUNNEL[i - 1];
            const prev = prevItem ? prevItem.count : f.count;
            const drop = i === 0 ? 0 : 100 - pct(f.count, prev);
            return (
              <div key={f.stage} style={{ display: 'grid', gap: 6 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                  <span style={{ fontWeight: 600 }}>
                    {f.stage}{' '}
                    {'projected' in f && f.projected ? (
                      <span style={{ fontWeight: 400, color: 'var(--ink-soft)', fontSize: 12 }}>
                        (projected · camp not started)
                      </span>
                    ) : null}
                  </span>
                  <span className="mono" style={{ color: 'var(--ink-soft)' }}>
                    {f.count}
                    {i > 0 && drop > 0 ? ` · -${drop}%` : ''}
                  </span>
                </div>
                <MiniBar value={pct(f.count, FUNNEL[0].count)} tone={'projected' in f && f.projected ? 'ink' : 'flow'} />
              </div>
            );
          })}
        </div>
      </Card>

      {/* Sessions (campuses) — editorial table, not an identical card grid. */}
      <Card>
        <p className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)', margin: '0 0 var(--s-2)' }}>
          SESSIONS BY CAMPUS
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={cellHead}>Campus</th>
              <th style={cellHead}>Session</th>
              <th style={{ ...cellHead, minWidth: 160 }}>Capacity sold</th>
              <th style={{ ...cellHead, textAlign: 'right' }}>Paid</th>
              <th style={{ ...cellHead, textAlign: 'right' }}>Waitlist</th>
            </tr>
          </thead>
          <tbody>
            {CAMPUSES.map((c) => (
              <tr key={c.name}>
                <td style={cell}><span style={{ fontWeight: 600 }}>{c.name}</span></td>
                <td style={cell}>
                  <Chip tone="neutral">{c.weeks}wk</Chip>{' '}
                  <span className="mono" style={{ color: 'var(--ink-soft)', fontSize: 11 }}>{c.dates}</span>
                </td>
                <td style={cell}>
                  <div style={{ display: 'grid', gap: 4 }}>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)' }}>
                      {c.paid}/{c.capacity} · {pct(c.paid, c.capacity)}%
                    </span>
                    <MiniBar value={pct(c.paid, c.capacity)} />
                  </div>
                </td>
                <td style={{ ...cell, textAlign: 'right' }} className="mono">{c.paid}</td>
                <td style={{ ...cell, textAlign: 'right' }} className="mono">
                  {c.waitlist > 0 ? (
                    <Chip tone={c.waitlist > 10 ? 'signal' : 'gate'}>{c.waitlist}</Chip>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <p className="mono" style={{ color: 'var(--ink-soft)', fontSize: 11, margin: 0 }}>
        Program: summer_camp · separate P&L from Module 10 budget · Module 4
      </p>
    </section>
  );
}
