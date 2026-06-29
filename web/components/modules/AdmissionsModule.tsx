'use client';

// Admissions & Voice of Customer (Module 9) — the listening post, wired end-to-end to
// the FastAPI backbone. Five controlled sub-views (TabBar):
//   9a Overview      — admission numbers by week (applicants/shadow/offers/deposits),
//                      top-3 objections + frequency, the 4-week theme trend, feedback
//                      open count, notable quotes, objection→resolution time, bridge hit-rate.
//   9b Objection log — themed/frequency-counted/trended verbatims with theme + source
//                      filters, sort by frequency; source rendered as an HONEST provenance
//                      chip (BDR/event = manual · SMS = HubSpot-Conv synthetic mirror · form).
//   9c Bridge        — bridge hit-rate + per-brief produced status + did-frequency-decrease,
//                      and a per-objection OWNER-gated "→ send to Content brief" (POST →
//                      shows in Content/Module 3 as "brief from admissions").
//   9d Voice         — qualitative quote feed, a prominent rotating quote-of-the-week, and
//                      the family sentiment ratio. The §7.5 sentiment source_mode is surfaced
//                      HONESTLY as a badge ("PLACEHOLDER · AGGREGATE") — never implied live.
//   9e Feedback loop — items by category + status, the 7-day closure rate, an OWNER-gated
//                      file-feedback form (actionable → Decision Queue), and a LEADER/ADMIN
//                      action/close (PATCH). Actionable feedback also surfaces to the Lead.
// Every read falls back to a per-resource seed (lib/admissions-api) so the screen never
// blanks; the LIVE/SAMPLE pill is honest, and each surface renders provenance from the
// backend `source`/`source_mode` field — never a hard-coded badge.

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { canEditWorkstream, moduleById, type Role } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';
import { apiGet, apiPost, apiPatch } from '@/lib/api';
import {
  type OverviewResponse,
  type Objection,
  type VoiceResponse,
  type FeedbackResponse,
  type FeedbackItem,
  type BridgeResponse,
  type BriefResponse,
  type SourceBadgeInfo,
  SEED_OVERVIEW,
  SEED_OBJECTIONS_RESP,
  SEED_VOICE_RESP,
  SEED_FEEDBACK_RESP,
  SEED_BRIDGE_RESP,
  badgeStyle,
  objectionSourceBadge,
  sentimentSourceBadge,
  trendMeta,
  sourceLabel,
  humanLabel,
  urgencyStyle,
  toneColor,
  categoryStyle,
  feedbackStatusStyle,
  FEEDBACK_CATEGORIES,
  fmtDate,
} from '@/lib/admissions-api';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

interface Toast { msg: string; kind: 'ok' | 'err'; href?: string; }
type Notify = (m: string, k: 'ok' | 'err', href?: string) => void;
type Ctx = { role: Role; canEdit: boolean; isLeader: boolean; refetch: () => void; notify: Notify };

// ============================ the module =====================================
export function AdmissionsModule() {
  const { session } = useSession();
  const def = moduleById('admissions')!;
  const canEdit = canEditWorkstream(session, 'admissions'); // admin always; operator only if owns 'admissions'
  const isLeader = session.role === 'leader' || session.role === 'admin';
  const role = session.role;

  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [objections, setObjections] = useState<Objection[] | null>(null);
  const [voice, setVoice] = useState<VoiceResponse | null>(null);
  const [feedback, setFeedback] = useState<FeedbackResponse | null>(null);
  const [bridge, setBridge] = useState<BridgeResponse | null>(null);
  const [live, setLive] = useState(false);

  const load = useCallback(() => {
    apiGet<OverviewResponse>('/admissions/overview', role).then((d) => {
      if (d && Array.isArray(d.weekly_stats)) { setOverview(d); setLive(true); }
      else { setOverview(SEED_OVERVIEW); setLive(false); }
    });
    apiGet<Objection[]>('/admissions/objections', role).then((d) => setObjections(Array.isArray(d) ? d : SEED_OBJECTIONS_RESP));
    apiGet<VoiceResponse>('/admissions/voice', role).then((d) => setVoice(d && Array.isArray(d.quotes) ? d : SEED_VOICE_RESP));
    apiGet<FeedbackResponse>('/admissions/feedback', role).then((d) => setFeedback(d && Array.isArray(d.items) ? d : SEED_FEEDBACK_RESP));
    apiGet<BridgeResponse>('/admissions/bridge', role).then((d) => setBridge(d && Array.isArray(d.bridges) ? d : SEED_BRIDGE_RESP));
  }, [role]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback<Notify>((msg, kind, href) => setToast({ msg, kind, href }), []);

  const ov = overview ?? SEED_OVERVIEW;
  const objs = objections ?? SEED_OBJECTIONS_RESP;
  const voc = voice ?? SEED_VOICE_RESP;
  const fb = feedback ?? SEED_FEEDBACK_RESP;
  const br = bridge ?? SEED_BRIDGE_RESP;
  const ctx: Ctx = { role, canEdit, isLeader, refetch: load, notify };

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        <Header idx={def.idx} title={def.title} owner={def.owner} canEdit={canEdit} live={live} />

        {tab === 0 && <OverviewTab ov={ov} />}
        {tab === 1 && <ObjectionLogTab objs={objs} />}
        {tab === 2 && <BridgeTab br={br} objs={objs} {...ctx} />}
        {tab === 3 && <VoiceTab voc={voc} />}
        {tab === 4 && <FeedbackTab fb={fb} {...ctx} />}

        <div style={{ marginTop: 18, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>⌖ {def.source} · objections → Content briefs (Module 3) · actionable feedback → Decision Queue (Module 11) + the Marketing Lead</div>
      </section>
    </>
  );
}

// ============================ header band ====================================
function Header({ idx, title, owner, canEdit, live }: { idx: string; title: string; owner: string; canEdit: boolean; live: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 14, borderBottom: '1px solid var(--line)', paddingBottom: 12 }}>
      <div>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '1px', color: 'var(--ink-3)', marginBottom: 5 }}>
          MODULE {idx} · OWNER: {owner.toUpperCase()}
        </div>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 16, color: 'var(--ink)' }}>{title}</div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <StatusPill live={live} />
        <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: canEdit ? 'var(--gold-soft)' : 'var(--accent-soft)', color: canEdit ? 'var(--gold)' : 'var(--ink-3)' }}>
          {canEdit ? '✎ EDITABLE — your workstream' : '◌ READ-ONLY'}
        </span>
      </div>
    </div>
  );
}

// =============================== 9a · OVERVIEW ===============================
function OverviewTab({ ov }: { ov: OverviewResponse }) {
  const hr = ov.bridge_hit_rate;
  const top = ov.top_objections[0];
  const trendEntries = Object.entries(ov.objection_trend);
  return (
    <>
      {/* top stat row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="CONTENT-BRIDGE HIT RATE" value={`${hr.hit_rate_pct}%`} sub={`${hr.produced}/${hr.total} briefs produced`} tone={hr.hit_rate_pct >= 60 ? 'ok' : 'warn'} />
        <StatTile label="OBJECTION → RESOLUTION" value={`${ov.objection_to_resolution_days}d`} sub="surfaced → published · avg" tone={ov.objection_to_resolution_days <= 2 ? 'ok' : 'warn'} />
        <StatTile label="FEEDBACK OPEN" value={String(ov.feedback_open_count)} sub="items awaiting action" tone={ov.feedback_open_count > 0 ? 'warn' : 'ok'} />
        <StatTile label="TOP OBJECTION · WK" value={top ? humanLabel(top.theme) : '—'} sub={top ? `${top.week_count} this week · ${trendMeta(top.trend).label}` : 'no objections logged'} />
      </div>

      {/* admission numbers by week */}
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Admission numbers by week</div>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>applicants · shadow days · offers · deposits</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr .8fr .8fr .8fr .8fr', fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
          <div>WEEK OF</div>
          <div style={{ textAlign: 'right' }}>APPLICANTS</div>
          <div style={{ textAlign: 'right' }}>SHADOW DAYS</div>
          <div style={{ textAlign: 'right' }}>OFFERS</div>
          <div style={{ textAlign: 'right' }}>DEPOSITS</div>
        </div>
        {ov.weekly_stats.map((s) => (
          <div key={s.week_of} style={{ display: 'grid', gridTemplateColumns: '1fr .8fr .8fr .8fr .8fr', alignItems: 'center', padding: '8px 16px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ fontSize: 11, color: 'var(--ink)' }}>{fmtDate(s.week_of)}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, color: 'var(--ink)' }}>{s.applicants}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, color: 'var(--ink-2)' }}>{s.shadow_days}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, color: 'var(--ink-2)' }}>{s.offers}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ok)' }}>{s.deposits}</div>
          </div>
        ))}
      </div>

      {/* top objections + theme trend */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 14, marginBottom: 14 }}>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 3 }}>Top objections this week</div>
          <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 10 }}>by weekly frequency · the bridge auto-stubs the rising ones</div>
          {ov.top_objections.map((o) => {
            const tm = trendMeta(o.trend);
            return (
              <div key={o.objection_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderTop: '1px solid var(--line)' }}>
                <span style={CHIP}>{humanLabel(o.theme)}</span>
                <span style={{ flex: 1, fontSize: 10.5, color: 'var(--ink-2)', fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>&ldquo;{o.example_quote}&rdquo;</span>
                <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 700, color: tm.color }}>{tm.glyph}</span>
                <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 600, color: 'var(--ink)', minWidth: 22, textAlign: 'right' }}>{o.week_count}</span>
              </div>
            );
          })}
          {ov.top_objections.length === 0 && <Empty>No objections logged this week.</Empty>}
        </div>

        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 3 }}>Objection theme trend</div>
          <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 10 }}>{trendEntries.length} themes · ↑ rising · → stable · ↓ falling</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
            {trendEntries.map(([theme, t]) => {
              const tm = trendMeta(t);
              return (
                <span key={theme} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 9.5, fontWeight: 600, padding: '4px 9px', borderRadius: 2, background: 'var(--accent-soft)', color: 'var(--ink-2)' }}>
                  {humanLabel(theme)} <span style={{ color: tm.color, fontSize: 12 }}>{tm.glyph}</span>
                </span>
              );
            })}
          </div>
        </div>
      </div>

      {/* notable quotes + cross-links */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 14 }}>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 10 }}>Notable family quotes</div>
          {ov.notable_quotes.map((q) => (
            <div key={q.quote_id} style={{ display: 'flex', gap: 11, padding: '9px 0', borderTop: '1px solid var(--line)' }}>
              <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: toneColor(q.sentiment), flexShrink: 0, marginTop: 4 }} />
              <div>
                <div style={{ fontSize: 11.5, color: 'var(--ink)', lineHeight: 1.45 }}>&ldquo;{q.quote}&rdquo;</div>
                <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>⌖ {humanLabel(q.theme)} · {q.sentiment}{q.is_quote_of_week ? ' · ★ quote of the week' : ''}</div>
              </div>
            </div>
          ))}
          {ov.notable_quotes.length === 0 && <Empty>No quotes captured yet.</Empty>}
        </div>
        <CrossLinks />
      </div>
    </>
  );
}

// =============================== 9b · OBJECTION LOG ==========================
const OBJ_GRID = '1.4fr .55fr .5fr 1.5fr 2.4fr';

function ObjectionLogTab({ objs }: { objs: Objection[] }) {
  const [theme, setTheme] = useState('');
  const [source, setSource] = useState('');
  const [sortDesc, setSortDesc] = useState(true);

  const themes = useMemo(() => Array.from(new Set(objs.map((o) => o.theme))), [objs]);
  const sources = useMemo(() => Array.from(new Set(objs.map((o) => o.source))), [objs]);

  const shown = useMemo(() => {
    let rows = objs.filter((o) => (!theme || o.theme === theme) && (!source || o.source === source));
    rows = [...rows].sort((a, b) => (sortDesc ? b.week_count - a.week_count : a.week_count - b.week_count));
    return rows;
  }, [objs, theme, source, sortDesc]);

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={theme} onChange={(e) => setTheme(e.target.value)} style={{ ...SELECT, width: 'auto', minWidth: 150 }}>
            <option value="">All themes</option>
            {themes.map((t) => <option key={t} value={t}>{humanLabel(t)}</option>)}
          </select>
          <select value={source} onChange={(e) => setSource(e.target.value)} style={{ ...SELECT, width: 'auto', minWidth: 140 }}>
            <option value="">All sources</option>
            {sources.map((s) => <option key={s} value={s}>{sourceLabel(s)}</option>)}
          </select>
        </div>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{shown.length} of {objs.length} objections</span>
      </div>

      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: OBJ_GRID, fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>THEME</div>
          <button onClick={() => setSortDesc((s) => !s)} style={{ textAlign: 'right', fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink)', fontWeight: 600, background: 'transparent', border: 'none', cursor: 'pointer', padding: 0 }}>FREQ {sortDesc ? '↓' : '↑'}</button>
          <div style={{ textAlign: 'center' }}>TREND</div>
          <div>SOURCE</div>
          <div>EXAMPLE VERBATIM</div>
        </div>
        {shown.map((o) => {
          const tm = trendMeta(o.trend);
          const sb = objectionSourceBadge(o.source);
          const sbs = badgeStyle(sb.tone);
          return (
            <div key={o.objection_id} style={{ display: 'grid', gridTemplateColumns: OBJ_GRID, alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <div><span style={CHIP}>{humanLabel(o.theme)}</span></div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
                {o.week_count}
                <span style={{ fontSize: 8.5, fontWeight: 400, color: 'var(--ink-3)' }}> /{o.cumulative_count}</span>
              </div>
              <div style={{ textAlign: 'center', fontFamily: MONO, fontSize: 14, fontWeight: 700, color: tm.color }}>{tm.glyph}</div>
              <div><span title={sb.label} style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, letterSpacing: '.3px', padding: '2px 7px', borderRadius: 2, background: sbs.bg, color: sbs.color, whiteSpace: 'nowrap' }}>{sourceLabel(o.source)}</span></div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-2)', fontStyle: 'italic', lineHeight: 1.4 }}>&ldquo;{o.example_quote}&rdquo;</div>
            </div>
          );
        })}
        {shown.length === 0 && <Empty>No objections match these filters.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Provenance is honest per source: BDR-call / event rows are MANUAL notes, FORM rows are captured fields, and SMS rows are the HubSpot Conversations SYNTHETIC MIRROR (no live feed). Cumulative is the all-time count; frequency sorts the week. Turn a rising objection into a Content brief on the bridge tab.
      </div>
    </>
  );
}

// =============================== 9c · BRIDGE ================================
function BridgeTab({ br, objs, canEdit, role, notify, refetch }: { br: BridgeResponse; objs: Objection[] } & Ctx) {
  const hr = br.hit_rate;
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="BRIDGE HIT RATE" value={`${hr.hit_rate_pct}%`} sub={`${hr.produced} of ${hr.total} briefs reached publish`} tone={hr.hit_rate_pct >= 60 ? 'ok' : 'warn'} />
        <StatTile label="BRIEFS PRODUCED" value={`${hr.produced}/${hr.total}`} sub="objection → content stubs" />
        <StatTile label="OBJECTION → RESOLUTION" value={`${hr.avg_resolution_days}d`} sub="surfaced → published · avg" tone={hr.avg_resolution_days <= 2 ? 'ok' : 'warn'} />
      </div>

      {/* hit-rate bar */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '12px 16px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>BRIDGE HIT-RATE</span>
        <div style={{ flex: 1, height: 7, background: 'var(--card-2)', position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, hr.hit_rate_pct)}%`, background: 'var(--gold)' }} />
        </div>
        <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{hr.hit_rate_pct}%</span>
      </div>

      {/* per-brief tracker */}
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Per-brief status</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr .9fr 1.2fr', fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
          <div>OBJECTION THEME</div>
          <div>PRODUCED</div>
          <div style={{ textAlign: 'right' }}>FREQ Δ</div>
          <div style={{ textAlign: 'right' }}>SURFACED → PUB</div>
        </div>
        {br.bridges.map((b) => (
          <div key={b.bridge_id} style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr .9fr 1.2fr', alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
            <div><span style={CHIP}>{humanLabel(b.objection_theme)}</span></div>
            <div>
              <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: b.produced ? 'var(--ok-soft)' : 'var(--accent-soft)', color: b.produced ? 'var(--ok)' : 'var(--ink-3)' }}>
                {b.produced ? '✓ PUBLISHED' : '○ PENDING'}
              </span>
            </div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5 }}>
              {b.freq_after != null ? (
                <span style={{ color: b.frequency_decreased ? 'var(--ok)' : 'var(--ink-2)' }}>
                  {b.freq_before}→{b.freq_after}{b.frequency_decreased ? ' ↓' : ''}
                </span>
              ) : (
                <span style={{ color: 'var(--ink-3)' }}>{b.freq_before}→—</span>
              )}
            </div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{fmtDate(b.surfaced_at)} → {fmtDate(b.published_at)}</div>
          </div>
        ))}
        {br.bridges.length === 0 && <Empty>No content bridges yet — send an objection to a brief below.</Empty>}
      </div>

      {/* objection → send-to-brief */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Send an objection to a Content brief</div>
          {!canEdit && <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ SEND-TO-BRIEF — OWNER-GATED</span>}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)' }}>
          {objs.map((o, i) => (
            <BriefCard key={o.objection_id} o={o} canEdit={canEdit} role={role} notify={notify} refetch={refetch} rightBorder={i % 2 === 0} />
          ))}
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Hit-rate = produced briefs / total bridges (computed). Freq Δ shows whether the objection cooled after the brief published (a fall is the win). {canEdit ? '"→ Send to Content brief" POSTs a DRAFT calendar entry owned by admissions — it shows in Content (Module 3) as "Brief from admissions".' : 'Sending an objection to a brief is owner-gated (the Admissions Owner / admin).'}
      </div>
    </>
  );
}

function BriefCard({ o, canEdit, role, notify, refetch, rightBorder }: { o: Objection; canEdit: boolean; role: Role; notify: Notify; refetch: () => void; rightBorder: boolean }) {
  const [busy, setBusy] = useState(false);
  const u = urgencyStyle(o.urgency);

  const send = async () => {
    setBusy(true);
    const res = await apiPost<BriefResponse>(`/admissions/objections/${o.objection_id}/brief`, role, { title: `Brief from admissions: ${humanLabel(o.theme)}` });
    setBusy(false);
    if (!res || !res.entry_id) { notify('Could not create the brief — owner access (Admissions Owner / admin) required and the backbone must be up.', 'err'); return; }
    notify(`Drafted "${res.title}" (${res.channel}) → Content calendar as "brief from admissions".`, 'ok', '/content');
    refetch();
  };

  return (
    <div style={{ padding: 14, borderRight: rightBorder ? '1px solid var(--line)' : 'none', borderBottom: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 7, gap: 8 }}>
        <span style={CHIP}>{humanLabel(o.theme)}</span>
        <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: u.bg, color: u.color, whiteSpace: 'nowrap' }}>{u.label}</span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 2 }}>VERBATIM EXAMPLE</div>
      <div style={{ fontSize: 10.5, color: 'var(--ink-2)', fontStyle: 'italic', lineHeight: 1.4, marginBottom: 8 }}>&ldquo;{o.example_quote}&rdquo;</div>
      <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 2 }}>SUGGESTED ANGLE</div>
      <div style={{ fontSize: 10.5, color: 'var(--ink)', lineHeight: 1.45, marginBottom: 8 }}>Address &ldquo;{humanLabel(o.theme).toLowerCase()}&rdquo; head-on for the {o.persona} — proof + a clear next step.</div>
      <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 2 }}>TARGET PERSONA</div>
      <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginBottom: 10 }}>{o.persona}</div>
      {canEdit ? (
        <button onClick={send} disabled={busy} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--signal)', background: 'var(--signal-soft)', color: 'var(--signal)', padding: '6px 12px', opacity: busy ? 0.6 : 1 }}>{busy ? 'SENDING…' : '→ SEND TO CONTENT BRIEF'}</button>
      ) : (
        <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ OWNER-GATED</span>
      )}
    </div>
  );
}

// =============================== 9d · VOICE =================================
function VoiceTab({ voc }: { voc: VoiceResponse }) {
  const sm = sentimentSourceBadge(voc.sentiment_source_mode);
  const qs = voc.quote_sentiment;
  const fs = voc.feed_sentiment;
  return (
    <>
      {/* quote of the week — prominent */}
      {voc.quote_of_week && (
        <div style={{ border: '1px solid var(--gold)', background: 'var(--gold-soft)', padding: 16, marginBottom: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--gold)', fontWeight: 600, marginBottom: 8 }}>★ QUOTE OF THE WEEK</div>
          <div style={{ fontFamily: DISPLAY, fontSize: 16, fontWeight: 600, color: 'var(--ink)', lineHeight: 1.5 }}>&ldquo;{voc.quote_of_week.quote}&rdquo;</div>
          <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-2)', marginTop: 9 }}>⌖ {humanLabel(voc.quote_of_week.theme)} · {voc.quote_of_week.source.replace(/_/g, ' ')} · {voc.quote_of_week.sentiment}</div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 14 }}>
        {/* qualitative feed */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Voice of families</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{voc.quotes.length} notable verbatims</span>
          </div>
          {voc.quotes.map((q) => (
            <div key={q.quote_id} style={{ display: 'flex', gap: 11, padding: '11px 16px', borderBottom: '1px solid var(--line)' }}>
              <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: toneColor(q.sentiment), flexShrink: 0, marginTop: 4 }} />
              <div>
                <div style={{ fontSize: 11.5, color: 'var(--ink)', lineHeight: 1.45 }}>&ldquo;{q.quote}&rdquo;</div>
                <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>⌖ {humanLabel(q.theme)} · {q.source.replace(/_/g, ' ')}</div>
              </div>
            </div>
          ))}
          {voc.quotes.length === 0 && <Empty>No voice quotes captured yet.</Empty>}
        </div>

        {/* sentiment ratios */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 12.5, color: 'var(--ink)' }}>Family sentiment · quotes</div>
            <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 11 }}>positive / neutral / negative · {qs.total} verbatims</div>
            <SentimentBar pos={qs.positive_pct} neu={qs.neutral_pct} neg={qs.negative_pct} />
            <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
              <Legend color="var(--ok)" label={`positive ${qs.positive_pct}%`} />
              <Legend color="var(--ink-3)" label={`neutral ${qs.neutral_pct}%`} />
              <Legend color="var(--signal)" label={`negative ${qs.negative_pct}%`} />
            </div>
          </div>

          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, flex: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
              <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 12.5, color: 'var(--ink)' }}>Window aggregate</div>
              <SourceBadge info={sm} />
            </div>
            <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 11 }}>§7.5 sentiment adapter · {fs.total} mentions</div>
            <SentimentBar pos={fs.positive_pct} neu={fs.neutral_pct} neg={fs.negative_pct} />
            <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
              <Legend color="var(--ok)" label={`positive ${fs.positive_pct}%`} />
              <Legend color="var(--ink-3)" label={`neutral ${fs.neutral_pct}%`} />
              <Legend color="var(--signal)" label={`negative ${fs.negative_pct}%`} />
            </div>
          </div>
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        The window aggregate carries source_mode <b>&ldquo;{voc.sentiment_source_mode}&rdquo;</b> — it is the §7.5 placeholder adapter over synthetic data (aggregate-only, never a live feed, never child-keyed). Per-week sentiment trend is deferred until the live feed lands; the quote ratio is counted over the seeded verbatims.
      </div>
    </>
  );
}

function SentimentBar({ pos, neu, neg }: { pos: number; neu: number; neg: number }) {
  return (
    <div style={{ display: 'flex', height: 14, overflow: 'hidden', border: '1px solid var(--line)' }}>
      <div style={{ width: `${pos}%`, background: 'var(--ok)' }} />
      <div style={{ width: `${neu}%`, background: 'var(--ink-3)' }} />
      <div style={{ width: `${neg}%`, background: 'var(--signal)' }} />
    </div>
  );
}

// =============================== 9e · FEEDBACK LOOP =========================
function FeedbackTab({ fb, canEdit, isLeader, role, notify, refetch }: { fb: FeedbackResponse } & Ctx) {
  const [showForm, setShowForm] = useState(false);
  const cr = fb.closure_rate;
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <div style={{ border: `1px solid ${cr.closure_rate_pct >= 70 ? 'var(--ok)' : 'var(--warn)'}`, background: cr.closure_rate_pct >= 70 ? 'var(--ok-soft)' : 'var(--warn-soft)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: cr.closure_rate_pct >= 70 ? 'var(--ok)' : 'var(--warn)', fontWeight: 600 }}>7-DAY CLOSURE RATE</div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 28, lineHeight: 1.05, marginTop: 7, color: 'var(--ink)' }}>{cr.closure_rate_pct}%</div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{cr.within_sla}/{cr.actioned} actioned within SLA</div>
        </div>
        <StatTile label="OPEN ITEMS" value={String(cr.open_count)} sub="awaiting action" tone={cr.open_count > 0 ? 'warn' : 'ok'} />
        <StatTile label="ACTIONED" value={String(cr.actioned)} sub={`of ${cr.total} total`} />
        <StatTile label="TOTAL FILED" value={String(cr.total)} sub='"marketing needs to know X"' />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>actionable items also flag to the Decision Queue + the Marketing Lead</span>
        {canEdit ? (
          <button onClick={() => setShowForm((s) => !s)} style={{ ...PRIMARY_BTN, cursor: 'pointer' }}>{showForm ? '✕ CLOSE FORM' : '+ FILE FEEDBACK'}</button>
        ) : (
          <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ FILE FEEDBACK — OWNER-GATED</span>
        )}
      </div>

      {canEdit && showForm && (
        <div style={{ marginBottom: 14 }}>
          <FeedbackForm role={role} notify={notify} refetch={() => { refetch(); setShowForm(false); }} />
        </div>
      )}

      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        {fb.items.map((item) => (
          <FeedbackRow key={item.item_id} item={item} isLeader={isLeader} role={role} notify={notify} refetch={refetch} />
        ))}
        {fb.items.length === 0 && <Empty>No feedback filed yet.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Closure rate = actioned items closed within the SLA / actioned items (computed). {canEdit ? 'Filing an ACTIONABLE item enqueues an open admissions item on the Decision Queue (Module 11) for the Marketing Lead.' : 'Filing is owner-gated (the Admissions Owner / admin).'} {isLeader ? 'As leadership you can action/close an item below.' : 'Action/close is leader/admin-only.'}
      </div>
    </>
  );
}

function FeedbackForm({ role, notify, refetch }: { role: Role; notify: Notify; refetch: () => void }) {
  const [summary, setSummary] = useState('');
  const [category, setCategory] = useState<string>(FEEDBACK_CATEGORIES[0]);
  const [actionable, setActionable] = useState(false);
  const [recommendation, setRecommendation] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!summary.trim()) { notify('Add a summary — "marketing needs to know X".', 'err'); return; }
    setSaving(true);
    const res = await apiPost<FeedbackItem>('/admissions/feedback', role, { summary: summary.trim(), category, actionable, recommendation: recommendation.trim() });
    setSaving(false);
    if (!res || !res.item_id) { notify('Could not file feedback — owner access required and the backbone must be up.', 'err'); return; }
    if (res.actionable && res.decision_id) notify('Feedback filed — flagged to the Decision Queue for the Marketing Lead.', 'ok', '/decision');
    else notify('Feedback filed to the marketing loop.', 'ok');
    setSummary(''); setRecommendation(''); setActionable(false);
    refetch();
  };

  return (
    <FormCard title="FILE FEEDBACK" tag="OWNER · POST /admissions/feedback">
      <Field label="SUMMARY — &quot;MARKETING NEEDS TO KNOW X&quot;"><textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} placeholder="e.g. Families read 2-hour learning as less school…" style={{ ...INPUT, resize: 'vertical' }} /></Field>
      <Row>
        <Field label="CATEGORY"><select value={category} onChange={(e) => setCategory(e.target.value)} style={SELECT}>{FEEDBACK_CATEGORIES.map((c) => <option key={c} value={c}>{humanLabel(c)}</option>)}</select></Field>
        <Field label="ACTIONABLE?">
          <button type="button" onClick={() => setActionable((a) => !a)} style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, cursor: 'pointer', padding: '7px 12px', border: `1px solid ${actionable ? 'var(--signal)' : 'var(--line-2)'}`, background: actionable ? 'var(--signal-soft)' : 'var(--card)', color: actionable ? 'var(--signal)' : 'var(--ink-3)', textAlign: 'left' }}>{actionable ? '✓ flags to Decision Queue' : '○ not actionable'}</button>
        </Field>
      </Row>
      <Field label="RECOMMENDATION (OPTIONAL)"><input value={recommendation} onChange={(e) => setRecommendation(e.target.value)} placeholder="suggested next step for the Lead" style={INPUT} /></Field>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{actionable ? 'an actionable item enqueues a Decision-Queue item' : 'owner + raised-by are stamped server-side'}</span>
        <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>{saving ? 'FILING…' : 'FILE FEEDBACK'}</button>
      </div>
    </FormCard>
  );
}

function FeedbackRow({ item, isLeader, role, notify, refetch }: { item: FeedbackItem; isLeader: boolean; role: Role; notify: Notify; refetch: () => void }) {
  const [busy, setBusy] = useState(false);
  const cs = categoryStyle(item.category);
  const ss = feedbackStatusStyle(item.status);
  const isOpen = item.status === 'open';

  const patch = async (action: 'action' | 'close') => {
    setBusy(true);
    const res = await apiPatch<FeedbackItem>(`/admissions/feedback/${item.item_id}`, role, { action });
    setBusy(false);
    if (!res || !res.item_id) { notify('Could not update — leader/admin access required and the backbone must be up.', 'err'); return; }
    notify(`Feedback ${action === 'close' ? 'closed' : 'actioned'}.`, 'ok');
    refetch();
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: '1px solid var(--line)' }}>
      <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: cs.bg, color: cs.color, minWidth: 128, textAlign: 'center' }}>{humanLabel(item.category)}</span>
      <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.4 }}>{item.summary}</span>
      {item.actionable && (
        <Link href="/decision" title="actionable → Decision Queue" style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: 'var(--signal-soft)', color: 'var(--signal)', textDecoration: 'none', whiteSpace: 'nowrap' }}>⚑ DECISION QUEUE</Link>
      )}
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 9px', borderRadius: 2, background: ss.bg, color: ss.color, minWidth: 78, textAlign: 'center' }}>{ss.label}</span>
      {isLeader && isOpen ? (
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => patch('action')} disabled={busy} style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--gold)', background: 'var(--gold-soft)', color: 'var(--gold)', padding: '4px 9px', opacity: busy ? 0.6 : 1 }}>ACTION</button>
          <button onClick={() => patch('close')} disabled={busy} style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--ok)', background: 'var(--ok-soft)', color: 'var(--ok)', padding: '4px 9px', opacity: busy ? 0.6 : 1 }}>CLOSE</button>
        </div>
      ) : !isLeader && isOpen ? (
        <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '3px 8px', background: 'var(--accent-soft)', color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>◌ LEADER-ONLY</span>
      ) : (
        <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', whiteSpace: 'nowrap', minWidth: 78, textAlign: 'center' }}>{item.actioned_at ? fmtDate(item.actioned_at) : '—'}</span>
      )}
    </div>
  );
}

// ============================ shared bits ====================================
function CrossLinks() {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.7px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 9 }}>CROSS-MODULE LINKS</div>
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 7 }}>
        {[
          <>Rising objections draft a brief into <Link href="/content" style={LINK}>Content</Link> (objection → content bridge).</>,
          <>Actionable feedback flags to the <Link href="/decision" style={LINK}>Decision Queue</Link> for the Marketing Lead.</>,
          <>Hot families + objections mirror from <Link href="/nurture" style={LINK}>Nurture</Link> (SMS inbox).</>,
          <>The listening-post numbers feed the <Link href="/dashboard" style={LINK}>KPI Scorecard</Link>.</>,
        ].map((l, i) => (
          <li key={i} style={{ fontSize: 11.5, color: 'var(--ink-2)', display: 'flex', gap: 7 }}>
            <span style={{ color: 'var(--gold)' }}>→</span> <span>{l}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatTile({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: 'ok' | 'warn' }) {
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'warn' ? 'var(--warn)' : 'var(--ink)';
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>{label}</div>
      <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 26, lineHeight: 1.05, marginTop: 7, color }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{sub}</div>
    </div>
  );
}

function SourceBadge({ info }: { info: SourceBadgeInfo }) {
  const s = badgeStyle(info.tone);
  return <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, letterSpacing: '.3px', padding: '2px 8px', background: s.bg, color: s.color }}>⌖ {info.label}</span>;
}

function StatusPill({ live }: { live: boolean }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', color: live ? 'var(--ok)' : 'var(--ink-3)', background: live ? 'var(--ok-soft)' : 'var(--accent-soft)' }}>
      {live ? '● LIVE' : '○ SAMPLE · offline seed'}
    </span>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ width: 9, height: 9, background: color }} />
      <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{label}</span>
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '28px 16px', textAlign: 'center', fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)' }}>{children}</div>;
}

function FormCard({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
      <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, letterSpacing: '.3px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span>{title}</span>
        {tag && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 400, opacity: 0.85, whiteSpace: 'nowrap' }}>{tag}</span>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '14px 16px' }}>{children}</div>
    </div>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>{children}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const ok = toast.kind === 'ok';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '12px 22px 0', padding: '10px 14px', background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)', border: `1px solid ${ok ? 'var(--ok)' : 'var(--signal)'}` }}>
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: ok ? 'var(--ok)' : 'var(--signal)' }}>{ok ? '✓ DONE' : '⚠ ERROR'}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink)' }}>{toast.msg}</span>
      {ok && toast.href && <Link href={toast.href} style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, color: 'var(--ok)' }}>open →</Link>}
      <button onClick={onClose} aria-label="Dismiss" style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>✕</button>
    </div>
  );
}

const CHIP: React.CSSProperties = { fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--accent-soft)', color: 'var(--ink-2)', whiteSpace: 'nowrap' };
const LINK: React.CSSProperties = { color: 'var(--ink)', fontWeight: 600 };
const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };
