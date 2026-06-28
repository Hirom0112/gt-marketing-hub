'use client';

// Nurture & Lifecycle (Module 5) — the most data-rich screen.
//   • T1 / T2 / T3 segments off Supabase app_form (not HubSpot fields).
//   • Engagement-tier × attribute heatmap: clicked → 52% commit vs cold 16%;
//     income is the master variable; grade K–2 is the sweet spot.
//   • Sequences are read-only (HubSpot runs every send) + SMS inbox themes.
//   • 24-hr follow-up SLA at 78% (target 90%, owner-attributable).
// Ported faithfully from the design prototype; all data inlined as typed consts.

import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';

const MONO = 'JetBrains Mono';
const ARCHIVO = 'Fraunces';

// ---- Types -----------------------------------------------------------------
interface TopStat {
  label: string;
  value: string;
  valueSub?: string; // small inline qualifier (e.g. "vs 16%")
  valueColor: string;
  note: string;
}
interface Segment {
  tier: string;
  count: string;
  desc: string;
}
interface HeatCell {
  value: string;
  bg: string;
  color: string;
  opacity?: number;
}
interface HeatRow {
  label: string;
  cells: HeatCell[];
}
interface Sequence {
  name: string;
  type: string;
  aud: string;
  open: string;
  health: string;
  sc: string; // status text color token
  sbg: string; // status bg token
}
interface SmsTheme {
  tag: string;
  note: string;
  count: number;
  c: string; // tag text color token
  bg: string; // tag bg token
}

// ---- Seed data -------------------------------------------------------------
const TOP_STATS: TopStat[] = [
  {
    label: 'TOP CONVERSION PREDICTOR',
    value: '52%',
    valueSub: 'vs 16%',
    valueColor: 'var(--ink)',
    note: 'clicked-cohort commit vs cold',
  },
  { label: '24-HR SLA', value: '78%', valueColor: 'var(--warn)', note: '9 late · target 90% · HUBS' },
  { label: 'MKTG → ONBOARDING HANDOFF', value: '26', valueColor: 'var(--ink)', note: 'this week · 71% onboard rate' },
  { label: 'MULTI-TOUCH ATTRIBUTION', value: '——', valueColor: 'var(--broken)', note: 'UTM broken · not reported' },
];

const SEGMENTS: Segment[] = [
  {
    tier: 'T1 · messaging cohort',
    count: '~40',
    desc: 'Pamela Hobart + the Marketing Lead own messaging tests for the highest-intent families.',
  },
  {
    tier: 'T2 · conversion sprint',
    count: '3,142',
    desc: 'Email-driven; ~323 families per sales rep, with TX geo-targeting on the subset.',
  },
  {
    tier: 'T3 · waitlist',
    count: '1,124',
    desc: 'Three sub-buckets — ESA-planned, ESA-ineligible, no-indicator. Out-of-pocket is the largest-impact group.',
  },
];

const HEAT_COLS: string[] = ['$160K+', 'TX geo', 'Grade K–2', 'Alpha-X'];
// Color encodes conversion %: one BLUE ramp where opacity rises with the
// conversion rate (Clicked is hottest, Opened mid); cold cells fall back to
// --accent-soft. The Alpha-X cell is the warmest blue, not an alarm.
const HEAT_ROWS: HeatRow[] = [
  {
    label: 'Clicked',
    cells: [
      { value: '38%', bg: 'var(--gold)', color: 'var(--on-brand)', opacity: 0.88 },
      { value: '31%', bg: 'var(--gold)', color: 'var(--on-brand)', opacity: 0.82 },
      { value: '41%', bg: 'var(--gold)', color: 'var(--on-brand)', opacity: 0.94 },
      { value: '44%', bg: 'var(--gold)', color: 'var(--on-brand)', opacity: 1 },
    ],
  },
  {
    label: 'Opened',
    cells: [
      { value: '22%', bg: 'var(--gold)', color: 'var(--ink)', opacity: 0.5 },
      { value: '18%', bg: 'var(--gold)', color: 'var(--ink)', opacity: 0.42 },
      { value: '24%', bg: 'var(--gold)', color: 'var(--ink)', opacity: 0.55 },
      { value: '21%', bg: 'var(--gold)', color: 'var(--ink)', opacity: 0.48 },
    ],
  },
  {
    label: 'Cold',
    cells: [
      { value: '9%', bg: 'var(--accent-soft)', color: 'var(--ink-3)' },
      { value: '7%', bg: 'var(--accent-soft)', color: 'var(--ink-3)' },
      { value: '11%', bg: 'var(--accent-soft)', color: 'var(--ink-3)' },
      { value: '8%', bg: 'var(--accent-soft)', color: 'var(--ink-3)' },
    ],
  },
];
const HEAT_CHIPS: string[] = [
  '$160K+ ≈ 25% regardless of geo',
  'K–2 sweet spot · 609 apps',
  '"I follow Alpha on X" = 27.4%',
];

const SEQUENCES: Sequence[] = [
  { name: 'Apply Now — fall', type: 'nurture', aud: '2,310', open: '31%', health: 'OK', sc: 'var(--ok)', sbg: 'var(--ok-soft)' },
  { name: 'Tour no-show re-engage', type: 're-engage', aud: '214', open: '28%', health: 'OK', sc: 'var(--ok)', sbg: 'var(--ok-soft)' },
  { name: 'Summer camp — seats filling', type: 'event', aud: '1,180', open: '34%', health: 'OK', sc: 'var(--ok)', sbg: 'var(--ok-soft)' },
  { name: 'Lapsed inquiry win-back', type: 're-engage', aud: '3,420', open: '19%', health: 'WATCH', sc: 'var(--warn)', sbg: 'var(--warn-soft)' },
  { name: 'Welcome / onboarding', type: 'welcome', aud: '58', open: '53%', health: 'OK', sc: 'var(--ok)', sbg: 'var(--ok-soft)' },
];

const SMS_THEMES: SmsTheme[] = [
  { tag: 'TUITION', note: 'cost / price / ESA questions', count: 58, c: 'var(--signal)', bg: 'var(--signal-soft)' },
  { tag: 'NO REPLY', note: '"haven’t heard back" follow-ups', count: 41, c: 'var(--warn)', bg: 'var(--warn-soft)' },
  { tag: 'ACCREDITATION', note: 'is it a real school / diploma', count: 27, c: 'var(--ink-3)', bg: 'var(--accent-soft)' },
  { tag: 'READY', note: 'ready to enroll / next steps', count: 19, c: 'var(--gold)', bg: 'var(--gold-soft)' },
  { tag: 'SCHEDULING', note: 'start dates / shadow days', count: 16, c: 'var(--ink-3)', bg: 'var(--accent-soft)' },
];

// ---- Component -------------------------------------------------------------
export function NurtureModule() {
  const def = moduleById('nurture')!;

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* Top stat row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
          {TOP_STATS.map((s, i) => {
            const hero = i === 0;
            return (
              <div
                key={s.label}
                style={{
                  border: `1px solid ${hero ? 'var(--gold)' : 'var(--line-2)'}`,
                  background: hero ? 'var(--gold-soft)' : 'var(--card)',
                  padding: 13,
                }}
              >
                <div
                  style={{
                    fontFamily: MONO,
                    fontSize: 9,
                    letterSpacing: '.4px',
                    color: hero ? 'var(--gold)' : 'var(--ink-3)',
                    fontWeight: hero ? 600 : 400,
                  }}
                >
                  {s.label}
                </div>
                <div style={{ fontFamily: hero ? ARCHIVO : MONO, fontWeight: hero ? 700 : 600, fontSize: hero ? 27 : 22, color: s.valueColor, marginTop: 5, lineHeight: 1.05 }}>
                  {s.value}
                  {s.valueSub && <span style={{ fontFamily: MONO, fontWeight: 600, fontSize: 12, color: 'var(--ink-3)' }}> {s.valueSub}</span>}
                </div>
                <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 2 }}>{s.note}</div>
              </div>
            );
          })}
        </div>

        {/* Segments + heatmap */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.55fr', gap: 14, marginBottom: 14 }}>
          {/* segments */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Segments · T1 / T2 / T3</div>
            <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 11 }}>
              ⌖ Supabase app_form (not HubSpot fields) · TEFA cohort frozen → 2027
            </div>
            {SEGMENTS.map((s) => (
              <div key={s.tier} style={{ padding: '9px 0', borderTop: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 600 }}>{s.tier}</span>
                  <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{s.count}</span>
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 2, lineHeight: 1.4 }}>{s.desc}</div>
              </div>
            ))}
          </div>

          {/* engagement × attribute heatmap (hero) */}
          <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Engagement × attribute heatmap</div>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>conversion % · SUPA × HUBS</span>
            </div>
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 11 }}>
              Income is the master variable; grade K–2 is the sweet spot.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '70px repeat(4, 1fr)', gap: 3 }}>
              {/* header row */}
              <div />
              {HEAT_COLS.map((c) => (
                <div key={c} style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', textAlign: 'center', fontWeight: 600 }}>
                  {c}
                </div>
              ))}
              {/* data rows */}
              {HEAT_ROWS.map((row) => (
                <Fragmented key={row.label} row={row} />
              ))}
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
              {HEAT_CHIPS.map((chip) => (
                <span key={chip} style={{ fontFamily: MONO, fontSize: 8, padding: '2px 7px', background: 'var(--accent-soft)', color: 'var(--ink-2)' }}>
                  {chip}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Sequences + SMS inbox */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 14 }}>
          {/* sequences */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Sequences</div>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>read-only · HubSpot runs every sequence</span>
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1.7fr .9fr .7fr .7fr .8fr',
                fontFamily: MONO,
                fontSize: 8.5,
                letterSpacing: '.3px',
                color: 'var(--ink-3)',
                padding: '8px 16px',
                borderBottom: '1px solid var(--line-2)',
                fontWeight: 600,
              }}
            >
              <div>SEQUENCE</div>
              <div>TYPE</div>
              <div style={{ textAlign: 'right' }}>AUD</div>
              <div style={{ textAlign: 'right' }}>OPEN</div>
              <div style={{ textAlign: 'center' }}>HEALTH</div>
            </div>
            {SEQUENCES.map((q) => (
              <div
                key={q.name}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1.7fr .9fr .7fr .7fr .8fr',
                  alignItems: 'center',
                  padding: '9px 16px',
                  borderBottom: '1px solid var(--line)',
                }}
              >
                <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{q.name}</div>
                <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}>{q.type}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{q.aud}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{q.open}</div>
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: q.sbg, color: q.sc }}>{q.health}</span>
                </div>
              </div>
            ))}
          </div>

          {/* SMS inbox themes */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', borderBottom: '2px solid var(--ink)' }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>SMS inbox themes</div>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>HubSpot Conv.</span>
            </div>
            {SMS_THEMES.map((m) => (
              <div key={m.tag} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderBottom: '1px solid var(--line)' }}>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 9,
                    fontWeight: 600,
                    padding: '2px 7px',
                    background: m.bg,
                    color: m.c,
                    minWidth: 84,
                    textAlign: 'center',
                  }}
                >
                  {m.tag}
                </span>
                <span style={{ flex: 1, fontSize: 10.5, color: 'var(--ink-2)' }}>{m.note}</span>
                <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{m.count}</span>
              </div>
            ))}
            <div style={{ padding: '8px 14px', fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>
              v1 keyword rules · LLM auto-theme deferred to v2 · flag-to-hot-family → Decision Queue
            </div>
          </div>
        </div>
      </section>
    </>
  );
}

// One heatmap row: a label cell + its four conversion cells.
function Fragmented({ row }: { row: HeatRow }) {
  return (
    <>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink)', display: 'flex', alignItems: 'center', fontWeight: 600 }}>
        {row.label}
      </div>
      {row.cells.map((cell, i) => (
        <div
          key={`${row.label}-${i}`}
          style={{
            background: cell.bg,
            opacity: cell.opacity,
            color: cell.color,
            fontFamily: MONO,
            fontSize: 11,
            fontWeight: 600,
            textAlign: 'center',
            padding: '11px 0',
          }}
        >
          {cell.value}
        </div>
      ))}
    </>
  );
}
