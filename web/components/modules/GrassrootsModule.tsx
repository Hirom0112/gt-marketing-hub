'use client';

// Grassroots Engine (Module 2) — the demo OPERATOR's OWN module, wired end-to-end
// to the FastAPI backbone (Phase 2).
//   • Six controlled sub-views: Overview · Ambassadors · Market map · Referral
//     sprints · Parent community · Event calendar. Every panel reads a LIVE
//     endpoint (GET /grassroots/*), with a per-resource seed fallback so the screen
//     never blanks when the backbone is down (honest "○ SAMPLE").
//   • DUAL-SOURCE: ambassadors reconcile HubSpot (ambassador-tracking property)
//     against community.gt.school (GET /ambassadors/reconcile) — the RECONCILED
//     badge + per-row provenance/conflict tags are joined onto the live roster.
//   • OWNER-gated writes (canEditWorkstream → operator-owns-grassroots + admin;
//     leader & foreign operators get a read-only surface): log P2P, add map node,
//     launch sprint, log event, plus the two cross-module links (flag hot family →
//     Decision Queue; request testimonial → Content draft). On success we refetch
//     so goals/counts update live, and surface a toast.

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { canEditWorkstream, moduleById } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';
import { apiGet, apiPost } from '@/lib/api';
import {
  type OverviewResponse,
  type AmbassadorRow,
  type MarketMapResponse,
  type SprintRow,
  type EventRow,
  type ReconcileResponse,
  type Provenance,
  type MarketNodeRequest,
  type SprintRequest,
  type EventRequest,
  type HotFamilyRequest,
  type TestimonialRequest,
  SEED_OVERVIEW,
  SEED_AMBASSADORS,
  SEED_MARKET,
  SEED_SPRINTS,
  SEED_EVENTS,
  PIPELINE_ORDER,
  pipelineLabel,
  goalLabel,
  ambStatusStyle,
  mapStatusStyle,
  MAP_STATUS_OPTIONS,
  sprintHealthStyle,
  eventTypeLabel,
  EVENT_TYPE_OPTIONS,
  fmtShortDate,
} from '@/lib/grassroots-api';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

interface Toast { msg: string; kind: 'ok' | 'err'; }

// ============================ the module =====================================
export function GrassrootsModule() {
  const { session } = useSession();
  const def = moduleById('grassroots')!;
  const canEdit = canEditWorkstream(session, 'grassroots'); // operator-owns + admin

  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  // Live state per resource (+ a live/seed flag for the status pills).
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [ambassadors, setAmbassadors] = useState<AmbassadorRow[] | null>(null);
  const [market, setMarket] = useState<MarketMapResponse | null>(null);
  const [sprints, setSprints] = useState<SprintRow[] | null>(null);
  const [events, setEvents] = useState<EventRow[] | null>(null);
  const [recon, setRecon] = useState<ReconcileResponse | null>(null);
  const [live, setLive] = useState(false);

  const role = session.role;

  const load = useCallback(() => {
    apiGet<OverviewResponse>('/grassroots/overview', role).then((d) => {
      if (d && Array.isArray(d.goals) && d.goals.length > 0) { setOverview(d); setLive(true); }
      else { setOverview(SEED_OVERVIEW); setLive(false); }
    });
    apiGet<AmbassadorRow[]>('/grassroots/ambassadors', role).then((d) =>
      setAmbassadors(Array.isArray(d) && d.length > 0 ? d : SEED_AMBASSADORS),
    );
    apiGet<MarketMapResponse>('/grassroots/market-map', role).then((d) =>
      setMarket(d && Array.isArray(d.nodes) ? d : SEED_MARKET),
    );
    apiGet<SprintRow[]>('/grassroots/sprints', role).then((d) =>
      setSprints(Array.isArray(d) ? d : SEED_SPRINTS),
    );
    apiGet<EventRow[]>('/grassroots/events', role).then((d) =>
      setEvents(Array.isArray(d) ? d : SEED_EVENTS),
    );
    apiGet<ReconcileResponse>('/ambassadors/reconcile', role).then((d) => {
      if (d && Array.isArray(d.union) && d.union.length > 0) setRecon(d);
    });
  }, [role]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5200);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback((msg: string, kind: 'ok' | 'err') => setToast({ msg, kind }), []);

  // Resolve everything to a render value (seed until first load resolves).
  const ov = overview ?? SEED_OVERVIEW;
  const amb = ambassadors ?? SEED_AMBASSADORS;
  const mk = market ?? SEED_MARKET;
  const sp = sprints ?? SEED_SPRINTS;
  const ev = events ?? SEED_EVENTS;

  const ctx = { role, canEdit, refetch: load, notify, live };

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        <Header def={def} canEdit={canEdit} live={live} recon={recon} />

        {tab === 0 && <OverviewTab ov={ov} recon={recon} {...ctx} />}
        {tab === 1 && <AmbassadorsTab amb={amb} ov={ov} recon={recon} {...ctx} />}
        {tab === 2 && <MarketMapTab mk={mk} {...ctx} />}
        {tab === 3 && <SprintsTab sp={sp} {...ctx} />}
        {tab === 4 && <ParentCommunityTab ev={ev} ov={ov} {...ctx} />}
        {tab === 5 && <EventCalendarTab ev={ev} amb={amb} {...ctx} />}

        <div style={{ marginTop: 18, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>⌖ {def.source}</div>
      </section>
    </>
  );
}

type Ctx = { role: 'admin' | 'leader' | 'operator'; canEdit: boolean; refetch: () => void; notify: (m: string, k: 'ok' | 'err') => void; live: boolean };

// ============================ header band ====================================
function Header({ def, canEdit, live, recon }: { def: ReturnType<typeof moduleById> & {}; canEdit: boolean; live: boolean; recon: ReconcileResponse | null }) {
  const d = def!;
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 14, borderBottom: '1px solid var(--line)', paddingBottom: 12 }}>
      <div>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '1px', color: 'var(--ink-3)', marginBottom: 5 }}>
          MODULE {d.idx} · OWNER: {d.owner.toUpperCase()}
        </div>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 16, color: 'var(--ink)' }}>{d.title}</div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <StatusPill live={live} />
        <span
          style={{
            fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px',
            background: canEdit ? 'var(--gold-soft)' : 'var(--accent-soft)',
            color: canEdit ? 'var(--gold)' : 'var(--ink-3)',
          }}
        >
          {canEdit ? '✎ EDITABLE — your workstream' : '◌ READ-ONLY'}
        </span>
      </div>
    </div>
  );
}

// =============================== 2a · OVERVIEW ===============================
function OverviewTab({ ov, recon, role, canEdit, refetch, notify }: { ov: OverviewResponse; recon: ReconcileResponse | null } & Ctx) {
  const reconLive = recon !== null;
  return (
    <>
      {/* DUAL-SOURCE reconcile note */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 14px', border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)' }}>DUAL-SOURCE</span>
        <span style={{ fontSize: 11.5, color: 'var(--ink-2)', flex: 1, minWidth: 280 }}>
          Ambassadors reconcile <b>HubSpot</b> (ambassador-tracking property) against <b>community.gt.school</b>. Counts shown are the reconciled union.
        </span>
        <ReconcileBadge recon={recon} live={reconLive} />
      </div>

      {/* Goal tracker — 4 progress bars (LIVE from /grassroots/overview) */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Goal tracker</div>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>quarter-to-date · targets set by leadership</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '14px 22px' }}>
          {ov.goals.map((g) => {
            const pct = Math.min(100, g.pct);
            const exceeded = g.value >= g.target;
            return (
              <div key={g.key}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
                  <span style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{goalLabel(g.key)}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink)' }}>
                    <span style={{ fontWeight: 600 }}>{g.value}</span>
                    <span style={{ color: 'var(--ink-3)' }}> / {g.target}</span>
                    {exceeded && <span style={{ color: 'var(--ok)', marginLeft: 6, fontSize: 9 }}>EXCEEDED</span>}
                  </span>
                </div>
                <div style={{ height: 7, background: 'var(--card-2)', border: '1px solid var(--line)', position: 'relative' }}>
                  <div style={{ position: 'absolute', inset: 0, width: `${pct}%`, background: 'var(--gold)' }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Pipeline funnel */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Ambassador pipeline</div>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>Prospect → Champion · HUBS × community</span>
        </div>
        <PipelineFunnel pipeline={ov.pipeline} />
      </div>

      {/* Headline stat tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="AMBASSADORS" value={ov.headline.ambassadors_total} sub="tracked in the roster" />
        <StatTile label="SPRINTS" value={ov.headline.sprints_total} sub={`${ov.headline.sprints_active ?? 0} active`} />
        <StatTile label="MARKET NODES" value={ov.headline.market_nodes_total} sub="aggregate communities" />
        <StatTile label="EVENTS" value={ov.headline.events_total} sub={`${ov.headline.events_upcoming ?? 0} upcoming`} />
        <StatTile label="WARM INTROS" value={ov.goals.find((g) => g.key === 'warm_intros')?.value ?? 0} sub="reconciled union" />
        <StatTile label="INFLUENCED ENROLL." value={ov.goals.find((g) => g.key === 'influenced_enrollments')?.value ?? 0} sub="ambassador-attributed" />
      </div>

      {/* Cross-module links note */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.7px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 9 }}>CROSS-MODULE LINKS</div>
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {[
            'Testimonials auto-stub → Content & Thought Leadership (draft for review).',
            'Hot families push → the Decision Queue (leadership escalation).',
            'Parent-led events live here → Field & Events reads the calendar read-only.',
          ].map((l) => (
            <li key={l} style={{ fontSize: 12, color: 'var(--ink-2)', display: 'flex', gap: 7 }}>
              <span style={{ color: 'var(--gold)' }}>→</span> {l}
            </li>
          ))}
        </ul>
      </div>

      {/* Cross-link write actions (owner-gated) */}
      {canEdit ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <FlagHotFamilyForm role={role} refetch={refetch} notify={notify} />
          <TestimonialForm role={role} notify={notify} />
        </div>
      ) : (
        <ReadOnlyNote>Hot-family escalation and testimonial drafting are owner-gated — visible to the Grassroots Owner (and admin).</ReadOnlyNote>
      )}
    </>
  );
}

// ============================ 2b · AMBASSADORS ===============================
function AmbassadorsTab({ amb, ov, recon, role, canEdit, refetch, notify }: { amb: AmbassadorRow[]; ov: OverviewResponse; recon: ReconcileResponse | null } & Ctx) {
  // Join the reconcile conflict flags onto the live roster by email.
  const conflictEmails = useMemo(() => {
    const s = new Set<string>();
    if (recon) {
      for (const r of recon.union) if (r.has_conflict) s.add(r.synthetic_email.trim().toLowerCase());
      for (const c of recon.conflicts) if (c.synthetic_email) s.add(c.synthetic_email.trim().toLowerCase());
    }
    return s;
  }, [recon]);

  const [fSeg, setFSeg] = useState('');
  const [fStatus, setFStatus] = useState('');
  const [fRegion, setFRegion] = useState('');

  const segments = useMemo(() => Array.from(new Set(amb.map((a) => a.segment))).sort(), [amb]);
  const statuses = useMemo(() => Array.from(new Set(amb.map((a) => a.status))).sort(), [amb]);
  const regions = useMemo(() => Array.from(new Set(amb.map((a) => a.region))).sort(), [amb]);

  const filtered = amb.filter((a) =>
    (!fSeg || a.segment === fSeg) && (!fStatus || a.status === fStatus) && (!fRegion || a.region === fRegion),
  );

  const [busyId, setBusyId] = useState<string | null>(null);
  const logP2p = async (a: AmbassadorRow) => {
    setBusyId(a.ambassador_id);
    const res = await apiPost<AmbassadorRow>(`/grassroots/ambassador/${a.ambassador_id}/log-p2p`, role, {});
    setBusyId(null);
    if (!res) { notify('Could not log the P2P call — owner access required and the backbone must be up.', 'err'); return; }
    notify(`Logged a P2P call for ${a.synthetic_name} (now ${res.p2p_calls}). Goals updated.`, 'ok');
    refetch();
  };

  return (
    <>
      {/* Pipeline funnel (from /overview) */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Pipeline stages</div>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>reconciled union</span>
        </div>
        <PipelineFunnel pipeline={ov.pipeline} />
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', padding: '11px 13px', border: '1px solid var(--line-2)', background: 'var(--card-2)', marginBottom: 14 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>FILTER</span>
        <FilterSelect value={fSeg} onChange={setFSeg} all="All segments" options={segments} />
        <FilterSelect value={fStatus} onChange={setFStatus} all="Any status" options={statuses} labeler={(s) => ambStatusStyle(s).label} />
        <FilterSelect value={fRegion} onChange={setFRegion} all="Any region" options={regions} />
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{filtered.length} of {amb.length} shown</span>
      </div>

      {/* Roster table */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: GRID_AMB(canEdit), fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>NAME</div>
          <div>SEGMENT · REGION</div>
          <div>STATUS</div>
          <div style={{ textAlign: 'right' }}>INTROS</div>
          <div style={{ textAlign: 'right' }}>P2P</div>
          <div style={{ textAlign: 'right' }}>TOUCH</div>
          {canEdit && <div style={{ textAlign: 'right' }}>ACTION</div>}
        </div>
        {filtered.map((a) => {
          const st = ambStatusStyle(a.status);
          const conflict = conflictEmails.has(a.synthetic_email.trim().toLowerCase());
          return (
            <div key={a.ambassador_id} style={{ display: 'grid', gridTemplateColumns: GRID_AMB(canEdit), alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                {a.synthetic_name}
                {a.provenance && a.provenance !== 'both' && <ProvTag p={a.provenance} />}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{a.segment} · {a.region}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
                <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: st.bg, color: st.color }}>{st.label}</span>
                {conflict && (
                  <span title="Sources disagree on this ambassador — flagged for review, not auto-resolved." style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 5px', background: 'var(--warn-soft)', color: 'var(--warn)' }}>⚠ CONFLICT</span>
                )}
              </div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{a.intros}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{a.p2p_calls}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)' }}>{fmtShortDate(a.last_touch)}</div>
              {canEdit && (
                <div style={{ textAlign: 'right' }}>
                  <button
                    onClick={() => logP2p(a)}
                    disabled={busyId === a.ambassador_id}
                    style={{ cursor: busyId === a.ambassador_id ? 'default' : 'pointer', fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '4px 8px', border: '1px solid var(--ink)', background: 'var(--card)', color: 'var(--ink)', opacity: busyId === a.ambassador_id ? 0.5 : 1 }}
                  >
                    + P2P
                  </button>
                </div>
              )}
            </div>
          );
        })}
        {filtered.length === 0 && <Empty>No ambassadors match these filters.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Roster reconciles HubSpot ⊕ community.gt.school; rows present in only one source carry an origin tag, status disagreements a ⚠ conflict. {canEdit ? 'Log a P2P call inline — it increments the live goal.' : 'P2P logging is owner-gated.'}
      </div>
    </>
  );
}
const GRID_AMB = (canEdit: boolean) => (canEdit ? '1.4fr 1.7fr 1fr .55fr .5fr .6fr .6fr' : '1.4fr 1.8fr 1fr .6fr .55fr .65fr');

// ============================ 2c · MARKET MAP ================================
function MarketMapTab({ mk, role, canEdit, refetch, notify }: { mk: MarketMapResponse } & Ctx) {
  return (
    <>
      {/* Per-category coverage summary */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Coverage by category</div>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>contacted / total · leads</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10, padding: 14 }}>
          {mk.summary.map((c) => (
            <div key={c.category} style={{ border: '1px solid var(--line)', background: 'var(--card-2)', padding: '10px 12px' }}>
              <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink)', marginBottom: 6 }}>{c.category}</div>
              <div style={{ height: 6, background: 'var(--card)', border: '1px solid var(--line)', position: 'relative', marginBottom: 6 }}>
                <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, c.coverage_pct)}%`, background: c.coverage_pct >= 100 ? 'var(--ok)' : c.coverage_pct > 0 ? 'var(--gold)' : 'var(--line-2)' }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
                <span>{c.contacted}/{c.total} contacted · {c.coverage_pct}%</span>
                <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{c.leads} leads</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Node table */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Market-map nodes</div>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>aggregate nodes · no child data</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1.6fr 1fr .7fr .8fr', fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
          <div>CATEGORY</div>
          <div>CONTACT</div>
          <div>STATUS</div>
          <div style={{ textAlign: 'right' }}>LEADS</div>
          <div style={{ textAlign: 'right' }}>ACTIVITY</div>
        </div>
        {mk.nodes.map((n) => {
          const m = mapStatusStyle(n.status);
          return (
            <div key={n.node_id} style={{ display: 'grid', gridTemplateColumns: '1.1fr 1.6fr 1fr .7fr .8fr', alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{n.category}</div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{n.contact_label || '—'}</div>
              <div><span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: m.bg, color: m.color }}>{m.label}</span></div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{n.leads_generated}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)' }}>{fmtShortDate(n.last_activity)}</div>
            </div>
          );
        })}
      </div>

      {canEdit ? <AddNodeForm mk={mk} role={role} refetch={refetch} notify={notify} /> : <ReadOnlyNote>Adding / updating market-map nodes is owner-gated.</ReadOnlyNote>}
    </>
  );
}

// ============================ 2d · REFERRAL SPRINTS =========================
function SprintsTab({ sp, role, canEdit, refetch, notify }: { sp: SprintRow[] } & Ctx) {
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12, marginBottom: 14 }}>
        {sp.map((s) => {
          const h = sprintHealthStyle(s.health);
          return (
            <div key={s.sprint_id} style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '11px 14px', borderBottom: '2px solid var(--ink)', gap: 8 }}>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)' }}>{s.name}</span>
                <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: h.bg, color: h.color, whiteSpace: 'nowrap' }}>{h.label}</span>
              </div>
              <div style={{ padding: 14 }}>
                <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginBottom: 10 }}>⌖ {fmtShortDate(s.window_start)} – {fmtShortDate(s.window_end)} · {s.status.toUpperCase()}</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
                  <SprintStat label="ENLISTED" value={s.ambassadors_enlisted} />
                  <SprintStat label="FAMILIES" value={s.families_identified} />
                  <SprintStat label="CONVERSIONS" value={s.conversions} color="var(--ok)" />
                </div>
              </div>
            </div>
          );
        })}
        {sp.length === 0 && <Empty>No referral sprints yet.</Empty>}
      </div>
      {canEdit ? <LaunchSprintForm role={role} refetch={refetch} notify={notify} /> : <ReadOnlyNote>Launching a referral sprint is owner-gated.</ReadOnlyNote>}
    </>
  );
}

// ============================ 2e · PARENT COMMUNITY =========================
function ParentCommunityTab({ ev, ov, role, canEdit, refetch, notify }: { ev: EventRow[]; ov: OverviewResponse } & Ctx) {
  // REAL signals derived from instrumented data only.
  const totalRsvp = ev.reduce((a, e) => a + e.rsvp_count, 0);
  const totalAttend = ev.reduce((a, e) => a + e.attendance_count, 0);
  const recordedRsvp = ev.filter((e) => e.attendance_count > 0).reduce((a, e) => a + e.rsvp_count, 0);
  const attendRate = recordedRsvp > 0 ? Math.round((totalAttend / recordedRsvp) * 100) : null;
  const totalConv = ev.reduce((a, e) => a + e.conversions_influenced, 0);
  const activeCommunity = (ov.pipeline.active ?? 0) + (ov.pipeline.champion ?? 0);
  const champions = ov.pipeline.champion ?? 0;

  return (
    <>
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '11px 14px', marginBottom: 14 }}>
        <div style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>
          The parent community signal, built from <b>instrumented</b> data only — parent-led event attendance and the active/champion ambassador base. Relationship-quality metrics that are not yet instrumented are labelled as such rather than fabricated.
        </div>
      </div>

      {/* REAL community signals */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="ACTIVE COMMUNITY" value={activeCommunity} sub={`${champions} champions · active + champion ambassadors`} />
        <StatTile label="EVENT ATTENDANCE" value={totalAttend} sub={`of ${totalRsvp} RSVPs across ${ev.length} parent-led events`} />
        <StatTile label="CONVERSIONS INFLUENCED" value={totalConv} sub="attributed to parent-led events" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 8 }}>ATTENDANCE RATE</div>
          {attendRate !== null ? (
            <>
              <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 30, color: 'var(--ink)' }}>{attendRate}%</div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 4 }}>{totalAttend} attended / {recordedRsvp} RSVPs on events with recorded attendance.</div>
            </>
          ) : (
            <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>No attendance recorded yet — accrues as owners log event attendance.</div>
          )}
        </div>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 8 }}>WARM INTROS · COMMUNITY</div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 30, color: 'var(--ink)' }}>{ov.goals.find((g) => g.key === 'warm_intros')?.value ?? 0}</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 4 }}>reconciled warm intros from the ambassador base.</div>
        </div>
      </div>

      {/* Honest NOT-INSTRUMENTED panel */}
      <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card-2)', padding: 14 }}>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 10 }}>NOT INSTRUMENTED — STOOD IN</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
          {[
            { k: 'Parent NPS', why: 'needs a survey instrument wired to community.gt.school' },
            { k: 'Family retention / churn', why: 'needs a longitudinal enrollment join — not yet sourced' },
          ].map((x) => (
            <div key={x.k} style={{ border: '1px solid var(--line)', background: 'var(--card)', padding: '10px 12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink)' }}>{x.k}</span>
                <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '1px 5px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>NOT INSTRUMENTED</span>
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 4 }}>{x.why}.</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        {canEdit ? (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <FlagHotFamilyForm role={role} refetch={refetch} notify={notify} />
            <TestimonialForm role={role} notify={notify} />
          </div>
        ) : (
          <ReadOnlyNote>Flagging a hot family and drafting testimonials are owner-gated.</ReadOnlyNote>
        )}
      </div>
    </>
  );
}

// ============================ 2f · EVENT CALENDAR ===========================
function EventCalendarTab({ ev, amb, role, canEdit, refetch, notify }: { ev: EventRow[]; amb: AmbassadorRow[] } & Ctx) {
  const nameById = useMemo(() => {
    const m: Record<string, string> = {};
    for (const a of amb) m[a.ambassador_id] = a.synthetic_name;
    return m;
  }, [amb]);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const sorted = [...ev].sort((a, b) => a.date.localeCompare(b.date));

  return (
    <>
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Parent-led event calendar</div>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>source of truth here · Field &amp; Events reads read-only</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1.1fr .7fr 1fr .8fr .6fr .7fr', fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
          <div>EVENT</div>
          <div>HOST</div>
          <div>DATE</div>
          <div>TYPE</div>
          <div style={{ textAlign: 'right' }}>RSVP</div>
          <div style={{ textAlign: 'right' }}>ATTEND</div>
          <div style={{ textAlign: 'right' }}>CONV.</div>
        </div>
        {sorted.map((e) => {
          const past = new Date(`${e.date}T00:00:00`) < today;
          return (
            <div key={e.event_id} style={{ display: 'grid', gridTemplateColumns: '1.7fr 1.1fr .7fr 1fr .8fr .6fr .7fr', alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                {e.event_name}
                {!past && <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '1px 5px', background: 'var(--ok-soft)', color: 'var(--ok)' }}>UPCOMING</span>}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{(e.host_ambassador_id && nameById[e.host_ambassador_id]) || '—'}</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{fmtShortDate(e.date)}</div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{eventTypeLabel(e.event_type)}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{e.rsvp_count}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{past || e.attendance_count > 0 ? e.attendance_count : '—'}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ok)' }}>{e.conversions_influenced || '—'}</div>
            </div>
          );
        })}
        {sorted.length === 0 && <Empty>No parent-led events logged yet.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginBottom: 14 }}>
        ⌖ Parent-led events are written ONLY here; Field &amp; Events consumes this calendar read-only. Seed dates sit around mid-June 2026, so most read as past relative to today.
      </div>
      {canEdit ? <LogEventForm amb={amb} role={role} refetch={refetch} notify={notify} /> : <ReadOnlyNote>Logging a parent-led event is owner-gated.</ReadOnlyNote>}
    </>
  );
}

// ============================ WRITE FORMS ====================================
function AddNodeForm({ mk, role, refetch, notify }: { mk: MarketMapResponse; role: Ctx['role']; refetch: () => void; notify: Ctx['notify'] }) {
  const [category, setCategory] = useState('');
  const [contact, setContact] = useState('');
  const [status, setStatus] = useState('cold');
  const [leads, setLeads] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!category.trim()) { notify('A category is required.', 'err'); return; }
    setSaving(true);
    const body: MarketNodeRequest = {
      category: category.trim(),
      contact_label: contact.trim() || undefined,
      status,
      leads_generated: leads.trim() ? Math.max(0, Math.round(Number(leads))) : 0,
    };
    const res = await apiPost('/grassroots/market-map/node', role, body);
    setSaving(false);
    if (!res) { notify('Could not save the node — owner access required.', 'err'); return; }
    notify(`Saved market-map node "${category.trim()}".`, 'ok');
    setCategory(''); setContact(''); setLeads(''); setStatus('cold');
    refetch();
  };

  return (
    <FormCard title="ADD / UPDATE MARKET-MAP NODE" tag="OWNER">
      <Row>
        <Field label="CATEGORY"><input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Parent groups" style={INPUT} /></Field>
        <Field label="CONTACT LABEL"><input value={contact} onChange={(e) => setContact(e.target.value)} placeholder="e.g. Austin parent group list" style={INPUT} /></Field>
      </Row>
      <Row>
        <Field label="STATUS">
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={SELECT}>
            {MAP_STATUS_OPTIONS.map((s) => <option key={s} value={s}>{mapStatusStyle(s).label}</option>)}
          </select>
        </Field>
        <Field label="LEADS GENERATED"><input type="number" min={0} value={leads} onChange={(e) => setLeads(e.target.value)} placeholder="0" style={INPUT} /></Field>
      </Row>
      <SubmitRow saving={saving} onClick={submit} label="SAVE NODE" />
    </FormCard>
  );
}

function LaunchSprintForm({ role, refetch, notify }: { role: Ctx['role']; refetch: () => void; notify: Ctx['notify'] }) {
  const [name, setName] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [enlisted, setEnlisted] = useState('');
  const [families, setFamilies] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!name.trim() || !start || !end) { notify('Name, start and end dates are required.', 'err'); return; }
    if (end < start) { notify('The end date must be on/after the start date.', 'err'); return; }
    setSaving(true);
    const body: SprintRequest = {
      name: name.trim(), window_start: start, window_end: end,
      ambassadors_enlisted: enlisted.trim() ? Math.max(0, Math.round(Number(enlisted))) : 0,
      families_identified: families.trim() ? Math.max(0, Math.round(Number(families))) : 0,
    };
    const res = await apiPost('/grassroots/sprint', role, body);
    setSaving(false);
    if (!res) { notify('Could not launch the sprint — owner access required.', 'err'); return; }
    notify(`Launched referral sprint "${name.trim()}".`, 'ok');
    setName(''); setStart(''); setEnd(''); setEnlisted(''); setFamilies('');
    refetch();
  };

  return (
    <FormCard title="LAUNCH NEW SPRINT" tag="OWNER">
      <Field label="NAME"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Robotics-season referral sprint" style={INPUT} /></Field>
      <Row>
        <Field label="WINDOW START"><input type="date" value={start} onChange={(e) => setStart(e.target.value)} style={INPUT} /></Field>
        <Field label="WINDOW END"><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} style={INPUT} /></Field>
      </Row>
      <Row>
        <Field label="ENLISTED"><input type="number" min={0} value={enlisted} onChange={(e) => setEnlisted(e.target.value)} placeholder="0" style={INPUT} /></Field>
        <Field label="FAMILIES IDENTIFIED"><input type="number" min={0} value={families} onChange={(e) => setFamilies(e.target.value)} placeholder="0" style={INPUT} /></Field>
      </Row>
      <SubmitRow saving={saving} onClick={submit} label="LAUNCH SPRINT" />
    </FormCard>
  );
}

function LogEventForm({ amb, role, refetch, notify }: { amb: AmbassadorRow[]; role: Ctx['role']; refetch: () => void; notify: Ctx['notify'] }) {
  const [name, setName] = useState('');
  const [type, setType] = useState('coffee_chat');
  const [date, setDate] = useState('');
  const [host, setHost] = useState('');
  const [location, setLocation] = useState('');
  const [rsvp, setRsvp] = useState('');
  const [attend, setAttend] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!name.trim() || !date) { notify('Event name and date are required.', 'err'); return; }
    setSaving(true);
    const body: EventRequest = {
      event_name: name.trim(), event_type: type, date,
      host_ambassador_id: host || undefined,
      location_label: location.trim() || undefined,
      rsvp_count: rsvp.trim() ? Math.max(0, Math.round(Number(rsvp))) : 0,
      attendance_count: attend.trim() ? Math.max(0, Math.round(Number(attend))) : 0,
    };
    const res = await apiPost('/grassroots/event', role, body);
    setSaving(false);
    if (!res) { notify('Could not log the event — owner access required.', 'err'); return; }
    notify(`Logged parent-led event "${name.trim()}".`, 'ok');
    setName(''); setDate(''); setHost(''); setLocation(''); setRsvp(''); setAttend('');
    refetch();
  };

  return (
    <FormCard title="LOG PARENT-LED EVENT" tag="OWNER">
      <Row>
        <Field label="EVENT NAME"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Robotics open house Q&A" style={INPUT} /></Field>
        <Field label="TYPE">
          <select value={type} onChange={(e) => setType(e.target.value)} style={SELECT}>
            {EVENT_TYPE_OPTIONS.map((t) => <option key={t} value={t}>{eventTypeLabel(t)}</option>)}
          </select>
        </Field>
      </Row>
      <Row>
        <Field label="DATE"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={INPUT} /></Field>
        <Field label="HOST AMBASSADOR">
          <select value={host} onChange={(e) => setHost(e.target.value)} style={SELECT}>
            <option value="">— none —</option>
            {amb.map((a) => <option key={a.ambassador_id} value={a.ambassador_id}>{a.synthetic_name}</option>)}
          </select>
        </Field>
      </Row>
      <Row>
        <Field label="LOCATION"><input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. Plano" style={INPUT} /></Field>
        <Field label="RSVP"><input type="number" min={0} value={rsvp} onChange={(e) => setRsvp(e.target.value)} placeholder="0" style={INPUT} /></Field>
      </Row>
      <Field label="ATTENDANCE"><input type="number" min={0} value={attend} onChange={(e) => setAttend(e.target.value)} placeholder="0" style={INPUT} /></Field>
      <SubmitRow saving={saving} onClick={submit} label="LOG EVENT" />
    </FormCard>
  );
}

function FlagHotFamilyForm({ role, refetch, notify }: { role: Ctx['role']; refetch: () => void; notify: Ctx['notify'] }) {
  const [label, setLabel] = useState('');
  const [reason, setReason] = useState('');
  const [priority, setPriority] = useState('normal');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!label.trim()) { notify('A (synthetic) family label is required.', 'err'); return; }
    setSaving(true);
    const body: HotFamilyRequest = {
      family_label: label.trim(),
      reason: reason.trim() || undefined,
      recommendation: reason.trim() || undefined,
      priority,
    };
    const res = await apiPost('/grassroots/hot-family', role, body);
    setSaving(false);
    if (!res) { notify('Could not flag — owner access required.', 'err'); return; }
    notify(`Flagged hot family "${label.trim()}" → escalated to the Decision Queue.`, 'ok');
    setLabel(''); setReason(''); setPriority('normal');
    refetch();
  };

  return (
    <FormCard title="FLAG HOT FAMILY" tag="→ DECISION QUEUE">
      <Field label="FAMILY LABEL" hint="synthetic / aggregate — no PII">
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Austin robotics family (T1)" style={INPUT} />
      </Field>
      <Field label="REASON / RECOMMENDATION">
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} placeholder="Why escalate, and the proposed next step…" style={{ ...INPUT, resize: 'vertical' }} />
      </Field>
      <Field label="PRIORITY">
        <select value={priority} onChange={(e) => setPriority(e.target.value)} style={SELECT}>
          <option value="normal">Normal</option>
          <option value="urgent">Urgent</option>
        </select>
      </Field>
      <SubmitRow saving={saving} onClick={submit} label="FLAG → QUEUE" footer={<Link href="/decision" style={{ fontFamily: MONO, fontSize: 9, color: 'var(--brand)', textDecoration: 'none' }}>open the Decision Queue →</Link>} />
    </FormCard>
  );
}

function TestimonialForm({ role, notify }: { role: Ctx['role']; notify: Ctx['notify'] }) {
  const [title, setTitle] = useState('');
  const [quote, setQuote] = useState('');
  const [attribution, setAttribution] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!title.trim() || !quote.trim()) { notify('A title and a (synthetic) quote are required.', 'err'); return; }
    setSaving(true);
    const body: TestimonialRequest = { title: title.trim(), quote: quote.trim(), attribution_label: attribution.trim() || undefined };
    const res = await apiPost('/grassroots/testimonial', role, body);
    setSaving(false);
    if (!res) { notify('Could not draft the testimonial — owner access required.', 'err'); return; }
    notify(`Drafted testimonial "${title.trim()}" → Content library (draft for review).`, 'ok');
    setTitle(''); setQuote(''); setAttribution('');
  };

  return (
    <FormCard title="REQUEST TESTIMONIAL" tag="→ CONTENT (DRAFT)">
      <Field label="TITLE"><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Robotics parent on the GT community" style={INPUT} /></Field>
      <Field label="QUOTE" hint="synthetic / aggregate — no PII">
        <textarea value={quote} onChange={(e) => setQuote(e.target.value)} rows={2} placeholder="The community made the difference for our family…" style={{ ...INPUT, resize: 'vertical' }} />
      </Field>
      <Field label="ATTRIBUTION LABEL"><input value={attribution} onChange={(e) => setAttribution(e.target.value)} placeholder="e.g. Robotics parent, Austin" style={INPUT} /></Field>
      <SubmitRow saving={saving} onClick={submit} label="DRAFT TESTIMONIAL" />
    </FormCard>
  );
}

// ============================ shared bits ====================================
function PipelineFunnel({ pipeline }: { pipeline: Record<string, number> }) {
  const stages = PIPELINE_ORDER.filter((s) => s in pipeline);
  const order = stages.length ? stages : Object.keys(pipeline);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${order.length || 1}, 1fr)`, gap: 2, padding: '12px 16px' }}>
      {order.map((s, i) => (
        <div key={s} style={{ textAlign: 'center', position: 'relative' }}>
          <div style={{ fontFamily: MONO, fontWeight: 600, fontSize: 20, color: 'var(--ink)' }}>{pipeline[s]}</div>
          <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 2 }}>{pipelineLabel(s)}</div>
          {i < order.length - 1 && <span style={{ position: 'absolute', right: -1, top: 8, color: 'var(--ink-3)', fontSize: 11 }}>→</span>}
        </div>
      ))}
    </div>
  );
}

function StatTile({ label, value, sub }: { label: string; value: number; sub: string }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>{label}</div>
      <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 28, lineHeight: 1.05, marginTop: 7, color: 'var(--ink)' }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{sub}</div>
    </div>
  );
}

function SprintStat({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--card-2)', padding: '6px 8px' }}>
      <div style={{ fontFamily: MONO, fontSize: 7.5, letterSpacing: '.3px', color: 'var(--ink-3)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontFamily: MONO, fontWeight: 600, fontSize: 15, color: color ?? 'var(--ink)', marginTop: 2 }}>{value}</div>
    </div>
  );
}

function ReconcileBadge({ recon, live }: { recon: ReconcileResponse | null; live: boolean }) {
  if (!live || !recon) {
    return <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>○ SAMPLE · seed data</span>;
  }
  const { counts, reconciled_minutes_ago, source_health } = recon;
  const conflicts = counts.conflicts;
  const ok = source_health === 'ok';
  const color = conflicts > 0 ? 'var(--warn)' : ok ? 'var(--ok)' : 'var(--ink-3)';
  const bg = conflicts > 0 ? 'var(--warn-soft)' : ok ? 'var(--ok-soft)' : 'var(--accent-soft)';
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: bg, color }}>
      ● RECONCILED · {counts.matched} matched · {conflicts} conflict{conflicts === 1 ? '' : 's'} · {reconciled_minutes_ago}m ago
    </span>
  );
}

function ProvTag({ p }: { p: Provenance }) {
  const label = p === 'hubspot-only' ? 'HUBS-ONLY' : 'COMM-ONLY';
  return <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, letterSpacing: '.3px', padding: '1px 5px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>{label}</span>;
}

function StatusPill({ live }: { live: boolean }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', color: live ? 'var(--ok)' : 'var(--ink-3)', background: live ? 'var(--ok-soft)' : 'var(--accent-soft)' }}>
      {live ? '● LIVE' : '○ SAMPLE'}
    </span>
  );
}

function ReadOnlyNote({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card-2)', padding: '12px 14px', fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.6 }}>
      ◌ READ-ONLY · {children}
    </div>
  );
}

function FilterSelect({ value, onChange, all, options, labeler }: { value: string; onChange: (v: string) => void; all: string; options: string[]; labeler?: (s: string) => string }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={{ fontFamily: 'Geist', fontSize: 11.5, padding: '5px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2 }}>
      <option value="">{all}</option>
      {options.map((o) => <option key={o} value={o}>{labeler ? labeler(o) : o}</option>)}
    </select>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '28px 16px', textAlign: 'center', fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)' }}>{children}</div>;
}

function FormCard({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
      <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, letterSpacing: '.3px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>{title}</span>
        {tag && <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 400, opacity: 0.85 }}>{tag}</span>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '14px 16px' }}>{children}</div>
    </div>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>{children}</div>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
      <span>{label}{hint && <span style={{ marginLeft: 7, fontWeight: 400, opacity: 0.8 }}>{hint}</span>}</span>
      {children}
    </label>
  );
}

function SubmitRow({ saving, onClick, label, footer }: { saving: boolean; onClick: () => void; label: string; footer?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: footer ? 'space-between' : 'flex-end', alignItems: 'center', gap: 12 }}>
      {footer}
      <button onClick={onClick} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>{saving ? 'SAVING…' : label}</button>
    </div>
  );
}

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const ok = toast.kind === 'ok';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '12px 22px 0', padding: '10px 14px', background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)', border: `1px solid ${ok ? 'var(--ok)' : 'var(--signal)'}` }}>
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: ok ? 'var(--ok)' : 'var(--signal)' }}>{ok ? '✓ DONE' : '⚠ ERROR'}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink)' }}>{toast.msg}</span>
      <button onClick={onClose} aria-label="Dismiss" style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>✕</button>
    </div>
  );
}

// ---- shared style objects ---------------------------------------------------
const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };
