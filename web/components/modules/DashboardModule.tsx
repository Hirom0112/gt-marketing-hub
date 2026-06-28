'use client';

// Dashboard / KPI Tracking (Module 6) — the canonical weekly scorecard + sub-views.
//   • Scorecard: one row per KPI (this/last/Δ/target/status), every number carrying
//     its source. Click a row → a panel with the exact table.column, kind, formula.
//   • Tabs switch real content: Trends, SLA & ops health, Goal pacing, HubSpot mirror.
//   • "Reads all · owns nothing" — identical for every user/role; authoritative for
//     no number (each cites its single source of record).

import { useEffect, useState } from 'react';
import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet } from '@/lib/api';
import {
  type KpiRow,
  type WeeklyScorecardApi,
  type ConnectorApi,
  type ConnectorsApi,
  KPI_ROWS,
  kindOf,
  toKpiRow,
  fmtValue,
} from '@/lib/scorecard-view';
import { TrendsTab, SlaOpsTab, GoalPacingTab, HubSpotMirrorTab } from '@/components/modules/DashboardTabs';

const MONO = 'JetBrains Mono';
const GRID = '2.2fr 1.3fr .9fr .9fr .7fr .8fr 1.3fr';

export function DashboardModule() {
  const def = moduleById('dashboard')!;
  const { session } = useSession();
  const [live, setLive] = useState<WeeklyScorecardApi | null>(null);
  const [connectors, setConnectors] = useState<ConnectorApi[] | null>(null);
  const [tab, setTab] = useState(0);

  useEffect(() => {
    let active = true;
    apiGet<WeeklyScorecardApi>('/scorecard/weekly', session.role).then((data) => {
      if (active && data && Array.isArray(data.metrics) && data.metrics.length > 0) {
        setLive(data);
      }
    });
    apiGet<ConnectorsApi>('/scorecard/connectors', session.role).then((data) => {
      if (active && data && Array.isArray(data.connectors)) setConnectors(data.connectors);
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  const isLive = live !== null;
  const rows: KpiRow[] = isLive ? (live!.metrics ?? []).map(toKpiRow) : KPI_ROWS;
  const goalDate = live?.goal_date ?? null;

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      <FreshnessStrip connectors={connectors} />
      {tab === 0 && <ScorecardTab rows={rows} isLive={isLive} asOf={live?.as_of} />}
      {tab === 1 && <TrendsTab rows={rows} />}
      {tab === 2 && <SlaOpsTab rows={rows} />}
      {tab === 3 && <GoalPacingTab rows={rows} goalDate={goalDate} />}
      {tab === 4 && <HubSpotMirrorTab rows={rows} />}
    </>
  );
}

// ----------------------------- Scorecard (tab 6a) ----------------------------
function ScorecardTab({ rows, isLive, asOf }: { rows: KpiRow[]; isLive: boolean; asOf?: string }) {
  const [selected, setSelected] = useState<string | null>(null);
  const selectedRow = rows.find((r) => r.key === selected) ?? null;

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <CalloutCards rows={rows} />
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 16 }}>
        {/* Inverted header band */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '11px 16px',
            borderBottom: '2px solid var(--ink)',
            background: 'var(--ink)',
            color: 'var(--paper)',
          }}
        >
          <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 15, letterSpacing: '.3px' }}>
            CANONICAL WEEKLY SCORECARD
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: '.4px', opacity: 0.85 }}>
              {isLive && asOf ? `WEEK OF ${asOf}` : 'WEEK OF JUN 22'} · READS ALL · OWNS NOTHING
            </span>
            <span
              style={{
                fontFamily: MONO,
                fontSize: 9,
                fontWeight: 600,
                letterSpacing: '.4px',
                padding: '3px 8px',
                borderRadius: 2,
                whiteSpace: 'nowrap',
                color: isLive ? 'var(--ok)' : 'var(--ink-3)',
                background: isLive ? 'var(--ok-soft)' : 'var(--accent-soft)',
              }}
            >
              {isLive ? '● LIVE' : '○ SAMPLE'}
            </span>
          </div>
        </div>

        {/* Column header row */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: GRID,
            fontFamily: MONO,
            fontSize: 9,
            letterSpacing: '.4px',
            color: 'var(--ink-3)',
            padding: '8px 16px',
            borderBottom: '1px solid var(--line-2)',
            fontWeight: 600,
          }}
        >
          <div>METRIC</div>
          <div>SOURCE</div>
          <div style={{ textAlign: 'right' }}>THIS WK</div>
          <div style={{ textAlign: 'right' }}>LAST WK</div>
          <div style={{ textAlign: 'right' }}>Δ</div>
          <div style={{ textAlign: 'right' }}>TARGET</div>
          <div style={{ textAlign: 'center' }}>STATUS</div>
        </div>

        {/* KPI rows — each row is a button that opens its source panel */}
        {rows.map((k, i) => {
          const kind = kindOf(k.prov.kind);
          const on = k.key === selected;
          return (
            <button
              key={k.key}
              onClick={() => setSelected(on ? null : k.key)}
              aria-pressed={on}
              title="Click to see where this number comes from"
              style={{
                width: '100%',
                textAlign: 'left',
                border: 'none',
                cursor: 'pointer',
                display: 'grid',
                gridTemplateColumns: GRID,
                alignItems: 'center',
                padding: '11px 16px',
                borderBottom: '1px solid var(--line)',
                borderLeft: `3px solid ${on ? 'var(--brand)' : 'transparent'}`,
                background: on ? 'var(--accent-soft)' : i % 2 ? 'var(--card-2)' : 'transparent',
                font: 'inherit',
                color: 'inherit',
              }}
            >
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{k.name}</span>
                <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{k.note}</span>
              </div>
              {/* SOURCE — readable system + a kind dot */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: kind.dot, flexShrink: 0 }} />
                <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <span style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                    {k.prov.system}
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 8, letterSpacing: '.3px', color: 'var(--ink-3)' }}>
                    {kind.label}
                  </span>
                </span>
              </div>
              <div style={{ textAlign: 'right', fontFamily: 'Fraunces', fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>
                {k.now}
              </div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>{k.last}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: k.deltaColor }}>
                {k.delta}
              </div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, color: 'var(--ink-2)' }}>{k.target}</div>
              <div style={{ display: 'flex', justifyContent: 'center' }}>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 9,
                    fontWeight: 600,
                    letterSpacing: '.4px',
                    padding: '3px 8px',
                    borderRadius: 2,
                    background: k.statusBg,
                    color: k.statusColor,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {k.status}
                </span>
              </div>
            </button>
          );
        })}
      </div>

      {/* SOURCE PANEL — opens when a row is clicked: exactly where the number is from */}
      {selectedRow && <SourcePanel row={selectedRow} live={isLive} onClose={() => setSelected(null)} />}

      {/* Footnote / honesty band */}
      <div style={{ display: 'flex', gap: 14, fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', flexWrap: 'wrap', marginTop: 14 }}>
        <span>◆ Click any row to see its single source of record.</span>
        <span style={{ color: 'var(--signal)' }}>⃠ Hatched = uninstrumented / broken — never shown as on-track.</span>
        <span>Referenced live in the Monday meeting (agenda item 2 · the Marketing Lead).</span>
      </div>
    </section>
  );
}

// Connector freshness strip (spec 6: "last sync per connector"). One chip per data
// source, colored by mode, so you see at a glance which backbones are live vs
// simulated vs stood-in — and when each last synced.
function relTime(iso: string | null): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const h = Math.round(mins / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function FreshnessStrip({ connectors }: { connectors: ConnectorApi[] | null }) {
  if (!connectors || connectors.length === 0) return null;
  const dot = (mode: string, kind: string) =>
    kind === 'our_db' ? 'var(--brand)' : mode === 'live' ? 'var(--ok)' : mode === 'stood_in' ? 'var(--ink-3)' : 'var(--warn)';
  const modeLabel = (c: ConnectorApi) =>
    c.kind === 'our_db' ? 'live' : c.mode === 'stood_in' ? 'stood-in' : c.mode;
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        flexWrap: 'wrap',
        padding: '8px 22px',
        borderBottom: '1px solid var(--line)',
        background: 'var(--card-2)',
      }}
    >
      <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
        DATA FRESHNESS
      </span>
      {connectors.map((c) => (
        <span key={c.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: dot(c.mode, c.kind) }} />
          <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>{c.name}</span>
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
            {c.last_sync ? relTime(c.last_sync) : modeLabel(c)}
          </span>
        </span>
      ))}
    </div>
  );
}

// Auto-identified callout cards (spec 6: "biggest mover" + "red flags"), derived
// from the weekly deltas/status — no second source, just a read over the rows.
function CalloutCards({ rows }: { rows: KpiRow[] }) {
  // Biggest mover: largest absolute real week-over-week delta (needs a prior week).
  const movers = rows
    .filter((r) => r.statusKey !== 'uninstrumented' && r.sparkline.length >= 2)
    .map((r) => ({ row: r, change: r.nowNum - r.sparkline[r.sparkline.length - 2] }))
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
  const mover = movers[0] ?? null;
  // Red flags: anything AT RISK, or behind its pace toward target.
  const flags = rows.filter(
    (r) => r.statusKey === 'red' || (r.targetNum > 0 && r.statusKey !== 'uninstrumented' && r.projection < r.targetNum),
  );

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '12px 14px' }}>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          ▲ BIGGEST MOVER
        </div>
        {mover ? (
          <>
            <div style={{ fontFamily: 'Fraunces', fontSize: 16, fontWeight: 600, color: 'var(--ink)', marginTop: 6 }}>
              {mover.row.name}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: mover.change >= 0 ? 'var(--ok)' : 'var(--signal)', marginTop: 2 }}>
              {mover.change >= 0 ? '▲' : '▼'} {fmtValue(Math.abs(mover.change), mover.row.targetNum)} week-over-week · now {mover.row.now}
            </div>
          </>
        ) : (
          <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)', marginTop: 8 }}>
            Accruing — week-over-week movers appear once the backbone has &gt;1 week of history.
          </div>
        )}
      </div>
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '12px 14px' }}>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--signal)', fontWeight: 600 }}>
          ⚑ RED FLAGS
        </div>
        {flags.length ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
            {flags.map((f) => (
              <span key={f.key} style={{ fontFamily: MONO, fontSize: 9.5, padding: '3px 8px', borderRadius: 2, background: 'var(--signal-soft)', color: 'var(--signal)' }}>
                {f.name} · {f.now}
                {f.targetNum > 0 ? ` / ${f.target}` : ''}
              </span>
            ))}
          </div>
        ) : (
          <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)', marginTop: 8 }}>No metric below threshold this week.</div>
        )}
      </div>
    </div>
  );
}

// The source panel — the "oh, I know where this is coming from" surface.
function SourcePanel({ row, live, onClose }: { row: KpiRow; live: boolean; onClose: () => void }) {
  const kind = kindOf(row.prov.kind);
  const trust = live
    ? row.prov.kind === 'uninstrumented'
      ? { label: 'NOT INSTRUMENTED', color: 'var(--ink-2)', bg: 'var(--accent-soft)' }
      : row.prov.kind === 'stood_in'
        ? { label: 'STOOD-IN', color: 'var(--ink-2)', bg: 'var(--accent-soft)' }
        : { label: 'LIVE', color: 'var(--ok)', bg: 'var(--ok-soft)' }
    : { label: 'SAMPLE', color: 'var(--ink-3)', bg: 'var(--accent-soft)' };

  return (
    <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 4 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '10px 16px',
          borderBottom: '1px solid var(--line-2)',
          background: 'var(--card-2)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: kind.dot }} />
          <span style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 13.5, color: 'var(--ink)' }}>{row.name}</span>
          <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '2px 7px', borderRadius: 2, background: trust.bg, color: trust.color }}>
            {trust.label}
          </span>
        </div>
        <button
          onClick={onClose}
          aria-label="Close source panel"
          style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 13, color: 'var(--ink-3)', padding: 2 }}
        >
          ✕
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', rowGap: 10, columnGap: 14, padding: '14px 16px' }}>
        <Field label="SYSTEM" value={row.prov.system} />
        <Field label="LOCATOR" value={row.prov.locator} mono />
        <Field label="KIND" value={`${kind.label} — ${kind.tip}`} />
        <Field label="HOW IT'S COMPUTED" value={row.prov.compute} />
        <Field label="LAST SYNC" value={row.prov.lastSync ?? (live ? 'point-in-time (no weekly history yet)' : '—')} mono />
      </div>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600, paddingTop: 2 }}>
        {label}
      </div>
      <div style={{ fontFamily: mono ? MONO : 'Geist', fontSize: mono ? 11 : 12.5, color: 'var(--ink)', lineHeight: 1.45 }}>
        {value}
      </div>
    </>
  );
}
