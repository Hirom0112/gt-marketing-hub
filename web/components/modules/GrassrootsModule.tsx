'use client';

// Grassroots Engine (Module 2) — the demo OPERATOR's OWN module.
//   • The Grassroots Owner (Sam Okafor) owns `grassroots`, so when viewed as that
//     operator (or admin) the screen shows WRITE affordances; everyone else gets
//     a read-only surface — proving the canEditWorkstream write-gate end-to-end.
//   • DUAL-SOURCE: ambassadors reconcile HubSpot (ambassador-tracking property)
//     against community.gt.school, with a reconciled indicator.
//   • Parent-led events LIVE here; Field & Events reads them read-only.
//   • Cross-links: testimonials auto-stub to Content; hot families push to
//     Admissions + the Decision Queue.
// All data inlined as typed consts; styling matches the Nurture/Decision pattern.

import { useEffect, useState } from 'react';
import { canEditWorkstream, moduleById } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';
import { apiGet } from '@/lib/api';

const MONO = 'JetBrains Mono';
const ARCHIVO = 'Fraunces';

// ---- Types -----------------------------------------------------------------
interface Goal {
  label: string;
  value: number;
  target: number;
  unit?: string;
  color: string; // bar fill token
}
interface PipelineStage {
  stage: string;
  count: number;
}
interface Ambassador {
  name: string;
  segment: string;
  status: string;
  statusColor: string; // text token
  statusBg: string; // bg token
  intros: number;
  p2p: number;
  lastTouch: string;
  provenance?: Provenance; // dual-source origin (live reconcile only)
  hasConflict?: boolean; // a tracked attribute disagrees across sources
}

// ---- Live dual-source reconcile (GET /ambassadors/reconcile) ---------------
// The reconciled UNION + conflicts + source-health from the backend's pure
// reconciler (HubSpot ambassador-tracking ⊕ community.gt.school). Falls back to
// the inline seed below when the backbone is unreachable (static preview).
type Provenance = 'both' | 'hubspot-only' | 'community-only';
interface ReconcileRow {
  synthetic_name: string;
  segment: string;
  region: string;
  status: string;
  intros: number;
  p2p: number;
  last_touch: string;
  provenance: Provenance;
  has_conflict: boolean;
  conflicting_fields: string[];
}
interface ReconcileResponse {
  union: ReconcileRow[];
  conflicts: { synthetic_name: string; field: string; hubspot_value: string; community_value: string }[];
  counts: { union: number; matched: number; hubspot_only: number; community_only: number; conflicts: number };
  sources: { name: string; count: number; synced_minutes_ago: number; healthy: boolean }[];
  source_health: string;
  reconciled_minutes_ago: number;
}

// Status label → color tokens (the reconciler returns a status string, so the
// row colors are DERIVED here rather than carried per-row like the seed).
const STATUS_STYLE: Record<string, { color: string; bg: string }> = {
  Champion: { color: 'var(--gold)', bg: 'var(--gold-soft)' },
  Active: { color: 'var(--ok)', bg: 'var(--ok-soft)' },
  Onboarded: { color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
  Outreached: { color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
};
const STATUS_FALLBACK = { color: 'var(--ink-3)', bg: 'var(--accent-soft)' };

// One reconciled union row → the table's display shape.
function mapReconciledRow(r: ReconcileRow): Ambassador {
  const style = STATUS_STYLE[r.status] ?? STATUS_FALLBACK;
  return {
    name: r.synthetic_name,
    segment: `${r.segment} · ${r.region}`,
    status: r.status,
    statusColor: style.color,
    statusBg: style.bg,
    intros: r.intros,
    p2p: r.p2p,
    lastTouch: r.last_touch,
    provenance: r.provenance,
    hasConflict: r.has_conflict,
  };
}
type MapStatus = 'cold' | 'outreach' | 'in-conversation' | 'active' | 'closed';
interface MapNode {
  category: string;
  status: MapStatus;
  leads: number;
}
interface Sprint {
  name: string;
  window: string;
  ambassadors: number;
  families: number;
  conversions: number;
  health: string;
  hc: string; // health text token
  hbg: string; // health bg token
}
interface AmbEvent {
  name: string;
  host: string;
  date: string;
  type: string;
  rsvp: number;
  attendance: string;
}

// ---- Seed data -------------------------------------------------------------
const GOALS: Goal[] = [
  { label: 'Ambassadors active', value: 47, target: 25, color: 'var(--gold)' },
  { label: 'Warm intros', value: 138, target: 200, color: 'var(--gold)' },
  { label: 'P2P calls logged', value: 34, target: 50, color: 'var(--gold)' },
  { label: 'Influenced enrollments', value: 18, target: 30, color: 'var(--gold)' },
];

const PIPELINE: PipelineStage[] = [
  { stage: 'Prospect', count: 64 },
  { stage: 'Outreached', count: 38 },
  { stage: 'Onboarded', count: 29 },
  { stage: 'Active', count: 47 },
  { stage: 'Champion', count: 12 },
];

const AMBASSADORS: Ambassador[] = [
  { name: 'Renata Fields', segment: 'Robotics parent · Austin', status: 'Champion', statusColor: 'var(--gold)', statusBg: 'var(--gold-soft)', intros: 14, p2p: 9, lastTouch: '2d' },
  { name: 'Marcus Bell', segment: 'Homeschool co-op · Plano', status: 'Active', statusColor: 'var(--ok)', statusBg: 'var(--ok-soft)', intros: 8, p2p: 6, lastTouch: '4d' },
  { name: 'Priya Nair', segment: 'Chess club · Round Rock', status: 'Active', statusColor: 'var(--ok)', statusBg: 'var(--ok-soft)', intros: 6, p2p: 4, lastTouch: '1d' },
  { name: 'Devon Carter', segment: 'Math circle · Frisco', status: 'Onboarded', statusColor: 'var(--ink-2)', statusBg: 'var(--accent-soft)', intros: 2, p2p: 1, lastTouch: '6d' },
  { name: 'Aisha Rahman', segment: 'Parent group · Houston', status: 'Outreached', statusColor: 'var(--ink-3)', statusBg: 'var(--accent-soft)', intros: 0, p2p: 0, lastTouch: '9d' },
];

const MAP_STATUS_META: Record<MapStatus, { label: string; color: string; bg: string }> = {
  cold: { label: 'COLD', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
  outreach: { label: 'OUTREACH', color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
  'in-conversation': { label: 'IN CONVO', color: 'var(--gold)', bg: 'var(--gold-soft)' },
  active: { label: 'ACTIVE', color: 'var(--ok)', bg: 'var(--ok-soft)' },
  closed: { label: 'CLOSED', color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
};

const MAP_NODES: MapNode[] = [
  { category: 'Parent groups · Austin metro', status: 'active', leads: 41 },
  { category: 'Robotics teams · FIRST/VEX', status: 'in-conversation', leads: 19 },
  { category: 'Chess clubs · DFW', status: 'active', leads: 23 },
  { category: 'Math circles · statewide', status: 'outreach', leads: 7 },
  { category: 'Homeschool co-ops · Hill Country', status: 'in-conversation', leads: 14 },
  { category: 'STEM meetups · Houston', status: 'cold', leads: 0 },
];

const SPRINTS: Sprint[] = [
  { name: 'Robotics season referral push', window: 'Jun 16 – Jun 29', ambassadors: 8, families: 52, conversions: 6, health: 'ON PACE', hc: 'var(--ok)', hbg: 'var(--ok-soft)' },
  { name: 'Chess-club summer intro sprint', window: 'Jun 23 – Jul 06', ambassadors: 5, families: 31, conversions: 2, health: 'BEHIND', hc: 'var(--warn)', hbg: 'var(--warn-soft)' },
];

const EVENTS: AmbEvent[] = [
  { name: 'Robotics demo + GT info night', host: 'Renata Fields', date: 'Jul 09', type: 'Info night', rsvp: 34, attendance: '—' },
  { name: 'Chess club parent coffee', host: 'Priya Nair', date: 'Jul 12', type: 'Meetup', rsvp: 18, attendance: '—' },
  { name: 'Homeschool co-op open house', host: 'Marcus Bell', date: 'Jun 21', type: 'Open house', rsvp: 27, attendance: '22' },
];

const CROSS_LINKS: string[] = [
  'Testimonials auto-stub → Content & Thought Leadership (draft for review).',
  'Hot families push → Admissions & VoC + the Decision Queue (escalation).',
  'Parent-led events live here → Field & Events reads the calendar read-only.',
];

// ---- Component -------------------------------------------------------------
export function GrassrootsModule() {
  const { session } = useSession();
  const def = moduleById('grassroots')!;
  const canEdit = canEditWorkstream(session, 'grassroots');

  // Live dual-source reconcile; null until loaded (or if the backbone is down).
  const [recon, setRecon] = useState<ReconcileResponse | null>(null);
  useEffect(() => {
    let active = true;
    apiGet<ReconcileResponse>('/ambassadors/reconcile', session.role).then((data) => {
      if (active && data && Array.isArray(data.union) && data.union.length > 0) {
        setRecon(data);
      }
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  const reconLive = recon !== null;
  // The ambassador table is the reconciled UNION when live, else the inline seed.
  const ambassadors: Ambassador[] = reconLive ? recon!.union.map(mapReconciledRow) : AMBASSADORS;

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* Header band: title + edit-state chip + (gated) action buttons */}
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 14, borderBottom: '1px solid var(--line)', paddingBottom: 12 }}>
          <div>
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '1px', color: 'var(--ink-3)', marginBottom: 5 }}>
              MODULE {def.idx} · OWNER: {def.owner.toUpperCase()}
            </div>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 16, color: 'var(--ink)' }}>{def.title}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <span
              style={{
                fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px',
                background: canEdit ? 'var(--gold-soft)' : 'var(--accent-soft)',
                color: canEdit ? 'var(--gold)' : 'var(--ink-3)',
              }}
            >
              {canEdit ? '✎ EDITABLE — your workstream' : '◌ READ-ONLY'}
            </span>
            {canEdit && (
              <>
                <ActBtn>+ Log P2P call</ActBtn>
                <ActBtn>+ Add market-map node</ActBtn>
                <ActBtn>Request testimonial</ActBtn>
              </>
            )}
          </div>
        </div>

        {/* DUAL-SOURCE reconcile note */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 14px', border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)' }}>DUAL-SOURCE</span>
          <span style={{ fontSize: 11.5, color: 'var(--ink-2)', flex: 1, minWidth: 280 }}>
            Ambassadors reconcile <b>HubSpot</b> (ambassador-tracking property) against <b>community.gt.school</b>. Counts shown are the reconciled union.
          </span>
          <ReconcileBadge recon={recon} live={reconLive} />
        </div>

        {/* Goal tracker — 4 progress bars */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Goal tracker</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>quarter-to-date · targets set by leadership</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '14px 22px' }}>
            {GOALS.map((g) => {
              const pct = Math.min(100, Math.round((g.value / g.target) * 100));
              const exceeded = g.value >= g.target;
              return (
                <div key={g.label}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
                    <span style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{g.label}</span>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink)' }}>
                      <span style={{ fontWeight: 600 }}>{g.value}</span>
                      <span style={{ color: 'var(--ink-3)' }}> / {g.target}</span>
                      {exceeded && <span style={{ color: 'var(--ok)', marginLeft: 6, fontSize: 9 }}>EXCEEDED</span>}
                    </span>
                  </div>
                  <div style={{ height: 7, background: 'var(--card-2)', border: '1px solid var(--line)', position: 'relative' }}>
                    <div style={{ position: 'absolute', inset: 0, width: `${pct}%`, background: g.color }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Ambassador pipeline: stage funnel + table */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Ambassador pipeline</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>Prospect → Champion · HUBS × community</span>
          </div>
          {/* stage funnel */}
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${PIPELINE.length}, 1fr)`, gap: 2, padding: '12px 16px', borderBottom: '1px solid var(--line-2)' }}>
            {PIPELINE.map((p, i) => (
              <div key={p.stage} style={{ textAlign: 'center', position: 'relative' }}>
                <div style={{ fontFamily: MONO, fontWeight: 600, fontSize: 20, color: 'var(--ink)' }}>{p.count}</div>
                <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 2 }}>{p.stage}</div>
                {i < PIPELINE.length - 1 && (
                  <span style={{ position: 'absolute', right: -1, top: 8, color: 'var(--ink-3)', fontSize: 11 }}>→</span>
                )}
              </div>
            ))}
          </div>
          {/* table header */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1.4fr 1.6fr .9fr .6fr .6fr .6fr',
              fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)',
              padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600,
            }}
          >
            <div>NAME</div>
            <div>SEGMENT</div>
            <div>STATUS</div>
            <div style={{ textAlign: 'right' }}>INTROS</div>
            <div style={{ textAlign: 'right' }}>P2P</div>
            <div style={{ textAlign: 'right' }}>TOUCH</div>
          </div>
          {ambassadors.map((a) => (
            <div
              key={a.name}
              style={{
                display: 'grid',
                gridTemplateColumns: '1.4fr 1.6fr .9fr .6fr .6fr .6fr',
                alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)',
              }}
            >
              <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                {a.name}
                {a.provenance && a.provenance !== 'both' && <ProvTag p={a.provenance} />}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{a.segment}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: a.statusBg, color: a.statusColor }}>{a.status}</span>
                {a.hasConflict && (
                  <span
                    title="Sources disagree on this ambassador's status — flagged for review, not auto-resolved."
                    style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 5px', background: 'var(--warn-soft)', color: 'var(--warn)' }}
                  >
                    ⚠ CONFLICT
                  </span>
                )}
              </div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{a.intros}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{a.p2p}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)' }}>{a.lastTouch}</div>
            </div>
          ))}
        </div>

        {/* Market map + Referral sprints side by side */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
          {/* Market map */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', borderBottom: '2px solid var(--ink)' }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Market map</div>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>aggregate nodes · no child data</span>
            </div>
            {MAP_NODES.map((n) => {
              const m = MAP_STATUS_META[n.status];
              return (
                <div key={n.category} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderBottom: '1px solid var(--line)' }}>
                  <span style={{ flex: 1, fontSize: 11, color: 'var(--ink-2)' }}>{n.category}</span>
                  <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', minWidth: 70, textAlign: 'center', background: m.bg, color: m.color }}>{m.label}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)', minWidth: 24, textAlign: 'right' }}>{n.leads}</span>
                </div>
              );
            })}
            <div style={{ padding: '8px 14px', fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>
              leads generated per node · {canEdit ? 'add nodes via the header CTA' : 'read-only view'}
            </div>
          </div>

          {/* Referral sprints */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', borderBottom: '2px solid var(--ink)' }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Referral sprints</div>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>2-week windows · active</span>
            </div>
            <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {SPRINTS.map((s) => (
                <div key={s.name} style={{ border: '1px solid var(--line-2)', background: 'var(--card-2)', padding: '11px 12px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                    <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink)' }}>{s.name}</span>
                    <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: s.hbg, color: s.hc }}>{s.health}</span>
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginBottom: 8 }}>⌖ {s.window}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
                    <SprintStat label="ENLISTED" value={s.ambassadors} />
                    <SprintStat label="FAMILIES" value={s.families} />
                    <SprintStat label="CONVERSIONS" value={s.conversions} color="var(--ok)" />
                  </div>
                </div>
              ))}
              {canEdit ? (
                <button
                  style={{ cursor: 'pointer', fontFamily: MONO, fontSize: 9.5, fontWeight: 600, padding: '8px 0', border: '1px dashed var(--ink-3)', background: 'transparent', color: 'var(--ink-2)' }}
                >
                  + Launch new sprint
                </button>
              ) : (
                <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', textAlign: 'center', padding: '6px 0' }}>
                  read-only · sprint launch is owner-gated
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Parent-led event calendar */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Parent-led event calendar</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>lives here · Field &amp; Events reads read-only</span>
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1.7fr 1.1fr .7fr .9fr .6fr .7fr',
              fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)',
              padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600,
            }}
          >
            <div>EVENT</div>
            <div>HOST AMBASSADOR</div>
            <div>DATE</div>
            <div>TYPE</div>
            <div style={{ textAlign: 'right' }}>RSVP</div>
            <div style={{ textAlign: 'right' }}>ATTEND</div>
          </div>
          {EVENTS.map((e) => (
            <div
              key={e.name}
              style={{
                display: 'grid',
                gridTemplateColumns: '1.7fr 1.1fr .7fr .9fr .6fr .7fr',
                alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)',
              }}
            >
              <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{e.name}</div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{e.host}</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{e.date}</div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{e.type}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{e.rsvp}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{e.attendance}</div>
            </div>
          ))}
        </div>

        {/* Cross-links note */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.7px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 9 }}>CROSS-MODULE LINKS</div>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
            {CROSS_LINKS.map((l) => (
              <li key={l} style={{ fontSize: 12, color: 'var(--ink-2)', display: 'flex', gap: 7 }}>
                <span style={{ color: 'var(--gold)' }}>→</span> {l}
              </li>
            ))}
          </ul>
        </div>

        <div style={{ marginTop: 18, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          ⌖ {def.source}
        </div>
      </section>
    </>
  );
}

// ---- Helpers ---------------------------------------------------------------
// The RECONCILED badge — reflects REAL reconciler output (matched count,
// conflict count, source health, freshness) when live, else a muted SAMPLE pill.
function ReconcileBadge({ recon, live }: { recon: ReconcileResponse | null; live: boolean }) {
  if (!live || !recon) {
    return (
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>
        ○ SAMPLE · seed data
      </span>
    );
  }
  const { counts, reconciled_minutes_ago, source_health } = recon;
  const conflicts = counts.conflicts;
  const ok = source_health === 'ok';
  // Conflicts present → draw attention (warn); otherwise a clean reconcile (ok).
  const color = conflicts > 0 ? 'var(--warn)' : ok ? 'var(--ok)' : 'var(--ink-3)';
  const bg = conflicts > 0 ? 'var(--warn-soft)' : ok ? 'var(--ok-soft)' : 'var(--accent-soft)';
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: bg, color }}>
      ● RECONCILED · {counts.matched} matched · {conflicts} conflict{conflicts === 1 ? '' : 's'} · {reconciled_minutes_ago}m ago
    </span>
  );
}

// A tiny source-origin tag for a union row that exists in only ONE source.
function ProvTag({ p }: { p: Provenance }) {
  const label = p === 'hubspot-only' ? 'HUBS-ONLY' : 'COMM-ONLY';
  return (
    <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, letterSpacing: '.3px', padding: '1px 5px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>
      {label}
    </span>
  );
}

function ActBtn({ children }: { children: React.ReactNode }) {
  return (
    <button
      style={{
        cursor: 'pointer', fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '5px 9px',
        border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)',
      }}
    >
      {children}
    </button>
  );
}

function SprintStat({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--card)', padding: '6px 8px' }}>
      <div style={{ fontFamily: MONO, fontSize: 7.5, letterSpacing: '.3px', color: 'var(--ink-3)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontFamily: MONO, fontWeight: 600, fontSize: 15, color: color ?? 'var(--ink)', marginTop: 2 }}>{value}</div>
    </div>
  );
}
