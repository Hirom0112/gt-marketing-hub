'use client';

// Website & Digital Analytics (Module 13) — the GA4 surface for gt.school +
// anywhere.gt.school, wired end-to-end to the FastAPI backbone. Five sub-views (TabBar):
//   13a Overview       — sessions/pageviews + per-site split, new-vs-returning, blended
//                        bounce + duration, PDF downloads this week, top landing pages,
//                        the leadership-input panel (flags + analysis requests) + a
//                        leadership "request analysis" form.
//   13b Subpage perf   — every page across both sites, filter by site/page-type, sort by any
//                        column; refresh-candidates flagged; LEADERSHIP "⚑ flag for refresh"
//                        (POST → a Content brief in Module 3 + a Decision in Module 11).
//   13c Traffic sources— per-channel breakdown + social platform split + source×page matrix +
//                        the UTM source VALIDATION (the SAME check_utm CRM Ops uses — broken
//                        campaigns flagged at the ORIGIN; → CRM Ops attribution chain).
//   13d PDF & downloads— every asset ranked by weekly downloads, referring page + source,
//                        cumulative + week-over-week trend.
//   13e Conversion paths— landing→application funnel drop-off, key conversion pages by
//                        submission rate, homepage→ flows, and cross-site flow.
// The GA4 metrics come from a STOOD-IN simulated adapter (no live GA4 credential in this
// portal) — source_mode is rendered HONESTLY as a badge, never implied live. Every read
// falls back to a per-resource seed (lib/website-api) so the screen never blanks.

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { moduleById, type Role } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';
import { apiGet, apiPost, apiPatch } from '@/lib/api';
import {
  type OverviewResponse,
  type SubpagesResponse,
  type TrafficResponse,
  type DownloadsResponse,
  type PathsResponse,
  type InputsResponse,
  type Subpage,
  type PageFlag,
  type AnalysisRequest,
  type FlagPageResponse,
  type AnalysisRequest as _AR,
  SEED_OVERVIEW,
  SEED_SUBPAGES,
  SEED_TRAFFIC,
  SEED_DOWNLOADS_RESP,
  SEED_PATHS,
  SEED_INPUTS,
  sourceModeBadge,
  channelColor,
  trendArrow,
  humanLabel,
  pageLabel,
  fmtPctRate,
  fmtPct01,
  fmtNum,
  fmtDur,
  fmtDate,
  TARGET_KINDS,
} from '@/lib/website-api';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

interface Toast { msg: string; kind: 'ok' | 'err'; href?: string; }
type Notify = (m: string, k: 'ok' | 'err', href?: string) => void;
type Ctx = { role: Role; isLeader: boolean; refetch: () => void; notify: Notify };

// ============================ the module =====================================
export function WebsiteModule() {
  const { session } = useSession();
  const def = moduleById('website')!;
  // Writes are LEADERSHIP input (the spec) — leader/admin only.
  const isLeader = session.role === 'leader' || session.role === 'admin';
  const role = session.role;

  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [subpages, setSubpages] = useState<SubpagesResponse | null>(null);
  const [traffic, setTraffic] = useState<TrafficResponse | null>(null);
  const [downloads, setDownloads] = useState<DownloadsResponse | null>(null);
  const [paths, setPaths] = useState<PathsResponse | null>(null);
  const [inputs, setInputs] = useState<InputsResponse | null>(null);
  const [live, setLive] = useState(false);

  const load = useCallback(() => {
    apiGet<OverviewResponse>('/website/overview', role).then((d) => {
      if (d && d.site_rollup) { setOverview(d); setLive(true); }
      else { setOverview(SEED_OVERVIEW); setLive(false); }
    });
    apiGet<SubpagesResponse>('/website/subpages', role).then((d) => setSubpages(d && Array.isArray(d.pages) ? d : SEED_SUBPAGES));
    apiGet<TrafficResponse>('/website/traffic', role).then((d) => setTraffic(d && d.breakdown ? d : SEED_TRAFFIC));
    apiGet<DownloadsResponse>('/website/downloads', role).then((d) => setDownloads(d && Array.isArray(d.downloads) ? d : SEED_DOWNLOADS_RESP));
    apiGet<PathsResponse>('/website/paths', role).then((d) => setPaths(d && Array.isArray(d.funnel) ? d : SEED_PATHS));
    apiGet<InputsResponse>('/website/inputs', role).then((d) => setInputs(d && Array.isArray(d.page_flags) ? d : SEED_INPUTS));
  }, [role]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback<Notify>((msg, kind, href) => setToast({ msg, kind, href }), []);

  const ov = overview ?? SEED_OVERVIEW;
  const sp = subpages ?? SEED_SUBPAGES;
  const tr = traffic ?? SEED_TRAFFIC;
  const dl = downloads ?? SEED_DOWNLOADS_RESP;
  const pa = paths ?? SEED_PATHS;
  const inp = inputs ?? SEED_INPUTS;
  const ctx: Ctx = { role, isLeader, refetch: load, notify };

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        <Header idx={def.idx} title={def.title} owner={def.owner} live={live} sourceMode={ov.source_mode} />

        {tab === 0 && <OverviewTab ov={ov} inp={inp} ctx={ctx} />}
        {tab === 1 && <SubpagesTab sp={sp} ctx={ctx} />}
        {tab === 2 && <TrafficTab tr={tr} />}
        {tab === 3 && <DownloadsTab dl={dl} />}
        {tab === 4 && <PathsTab pa={pa} />}

        <div style={{ marginTop: 18, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          ⌖ {def.source} · the GA4 metrics are a STOOD-IN simulated read (no live GA4 credential in this portal — labelled, never faked live). Top landing pages feed Content (Module 3), PDF downloads feed the Resource Library (Module 12), UTM tags originate here and validate into CRM Ops (Module 7), conversion paths feed Nurture (Module 5).
        </div>
      </section>
    </>
  );
}

// ============================ header band ====================================
function Header({ idx, title, owner, live, sourceMode }: { idx: string; title: string; owner: string; live: boolean; sourceMode: string }) {
  const sm = sourceModeBadge(sourceMode);
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
        <span title="No live GA4 credential in this portal — the metrics are a stood-in simulated read, surfaced honestly." style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.3px', padding: '3px 9px', background: sm.bg, color: sm.color }}>⌖ {sm.label}</span>
      </div>
    </div>
  );
}

// =============================== 13a · OVERVIEW ==============================
function OverviewTab({ ov, inp, ctx }: { ov: OverviewResponse; inp: InputsResponse; ctx: Ctx }) {
  const r = ov.site_rollup;
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="SESSIONS · THIS WEEK" value={fmtNum(r.total_sessions)} sub={`${fmtNum(r.total_pageviews)} pageviews · both sites`} />
        <StatTile label="BOUNCE RATE · BLENDED" value={fmtPct01(r.avg_bounce_rate)} sub="session-weighted across sites" tone={r.avg_bounce_rate <= 0.5 ? 'ok' : 'warn'} />
        <StatTile label="AVG SESSION" value={fmtDur(r.avg_session_duration_s)} sub="duration · min:sec" />
        <StatTile label="NEW VS RETURNING" value={`${r.new_pct}/${r.returning_pct}`} sub="new % · returning %" />
      </div>

      {/* sessions by site + new/returning split */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
        <Panel title="Sessions by site" hint="gt.school vs. anywhere.gt.school">
          {ov.sites.map((s) => {
            const pct = r.total_sessions > 0 ? Math.round((100 * s.sessions) / r.total_sessions) : 0;
            return (
              <div key={s.site} style={{ padding: '9px 0', borderTop: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>{s.site}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink-2)' }}>{fmtNum(s.sessions)} · {pct}%</span>
                </div>
                <Bar pct={pct} color="var(--signal)" />
                <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>
                  bounce {fmtPct01(s.bounce_rate)} · {fmtDur(s.avg_session_duration_s)} avg · {fmtNum(s.pageviews)} pv
                </div>
              </div>
            );
          })}
        </Panel>

        <Panel title="PDF downloads this week" hint={`${fmtNum(ov.download_summary.total_weekly)} total · ${ov.download_summary.wow_delta_pct >= 0 ? '+' : ''}${ov.download_summary.wow_delta_pct}% WoW`}>
          {ov.top_downloads.map((d) => (
            <div key={d.file_name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, padding: '7px 0', borderTop: '1px solid var(--line)' }}>
              <span style={{ fontSize: 10.5, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.file_name}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)', flexShrink: 0 }}>{d.weekly_count}</span>
            </div>
          ))}
          <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 7 }}>→ feeds the <Link href="/resources" style={LINK}>Resource Library</Link></div>
        </Panel>
      </div>

      {/* top landing pages */}
      <Panel title="Top landing pages by traffic" hint={`${ov.top_landing_pages.length} pages · weekly trend`} bordered>
        {ov.top_landing_pages.map((p) => {
          const ta = trendArrow(p.trend_pct);
          return (
            <div key={`${p.site}${p.page_path}`} style={{ display: 'grid', gridTemplateColumns: '2fr 1fr .7fr .7fr', alignItems: 'center', gap: 8, padding: '8px 0', borderTop: '1px solid var(--line)' }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink)' }}>{pageLabel(p.page_path)}</span>
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{p.site}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)', textAlign: 'right' }}>{fmtNum(p.pageviews)}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: ta.color, textAlign: 'right' }}>{ta.glyph}{Math.abs(p.trend_pct)}%</span>
            </div>
          );
        })}
        <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 7 }}>→ feeds <Link href="/content" style={LINK}>Content</Link> performance context</div>
      </Panel>

      {/* leadership-input panel + request analysis */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 14, marginTop: 14 }}>
        <LeadershipInputs inp={inp} ctx={ctx} />
        <RequestAnalysisForm ctx={ctx} />
      </div>
    </>
  );
}

function LeadershipInputs({ inp, ctx }: { inp: InputsResponse; ctx: Ctx }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Leadership inputs</div>
        <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{inp.open_flag_count} open flag · {inp.open_request_count} open request</span>
      </div>
      <div style={{ padding: '10px 16px' }}>
        <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 6 }}>⚑ PAGE-REFRESH FLAGS</div>
        {inp.page_flags.map((f) => <FlagRow key={f.flag_id} f={f} ctx={ctx} />)}
        {inp.page_flags.length === 0 && <Empty>No page flags.</Empty>}
        <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600, margin: '12px 0 6px' }}>◎ ANALYSIS REQUESTS</div>
        {inp.analysis_requests.map((rq) => <RequestRow key={rq.request_id} rq={rq} ctx={ctx} />)}
        {inp.analysis_requests.length === 0 && <Empty>No analysis requests.</Empty>}
      </div>
    </div>
  );
}

function FlagRow({ f, ctx }: { f: PageFlag; ctx: Ctx }) {
  const [busy, setBusy] = useState(false);
  const open = f.status === 'open';
  const resolve = async () => {
    setBusy(true);
    const res = await apiPatch(`/website/pages/flag/${f.flag_id}`, ctx.role, { action: 'resolve' });
    setBusy(false);
    if (!res) { ctx.notify('Could not resolve — leadership access required and the backbone must be up.', 'err'); return; }
    ctx.notify(`Resolved the refresh flag on ${f.page_path}.`, 'ok'); ctx.refetch();
  };
  return (
    <div style={{ display: 'flex', gap: 9, padding: '7px 0', borderTop: '1px solid var(--line)', alignItems: 'flex-start' }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: open ? 'var(--signal)' : 'var(--ok)', flexShrink: 0, marginTop: 5 }} />
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{f.page_path} <span style={{ color: 'var(--ink-3)' }}>· {f.site}</span></div>
        <div style={{ fontSize: 10, color: 'var(--ink-2)', lineHeight: 1.4, marginTop: 2 }}>{f.reason}</div>
        <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginTop: 3 }}>
          {f.brief_entry_id && <Link href="/content" style={LINK}>→ Content brief</Link>}{f.brief_entry_id && f.decision_id ? ' · ' : ''}{f.decision_id && <Link href="/decision" style={LINK}>→ Decision</Link>}
        </div>
      </div>
      {open ? (
        ctx.isLeader
          ? <button onClick={resolve} disabled={busy} style={MINI_BTN}>{busy ? '…' : 'RESOLVE'}</button>
          : <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--signal)', fontWeight: 600 }}>OPEN</span>
      ) : <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ok)', fontWeight: 600 }}>✓ RESOLVED</span>}
    </div>
  );
}

function RequestRow({ rq, ctx }: { rq: AnalysisRequest; ctx: Ctx }) {
  const [busy, setBusy] = useState(false);
  const open = rq.status === 'open';
  const resolve = async () => {
    setBusy(true);
    const res = await apiPatch(`/website/analysis/${rq.request_id}`, ctx.role, { action: 'resolve' });
    setBusy(false);
    if (!res) { ctx.notify('Could not resolve — leadership access required and the backbone must be up.', 'err'); return; }
    ctx.notify(`Resolved the analysis request on ${rq.target}.`, 'ok'); ctx.refetch();
  };
  return (
    <div style={{ display: 'flex', gap: 9, padding: '7px 0', borderTop: '1px solid var(--line)', alignItems: 'flex-start' }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: open ? 'var(--gold)' : 'var(--ok)', flexShrink: 0, marginTop: 5 }} />
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{rq.target} <span style={{ color: 'var(--ink-3)' }}>· {rq.target_kind}</span></div>
        <div style={{ fontSize: 10, color: 'var(--ink-2)', lineHeight: 1.4, marginTop: 2 }}>{rq.question}</div>
        {rq.decision_id && <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginTop: 3 }}><Link href="/decision" style={LINK}>→ Decision Queue</Link></div>}
      </div>
      {open ? (
        ctx.isLeader
          ? <button onClick={resolve} disabled={busy} style={MINI_BTN}>{busy ? '…' : 'RESOLVE'}</button>
          : <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--gold)', fontWeight: 600 }}>OPEN</span>
      ) : <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ok)', fontWeight: 600 }}>✓ RESOLVED</span>}
    </div>
  );
}

function RequestAnalysisForm({ ctx }: { ctx: Ctx }) {
  const [target, setTarget] = useState('');
  const [kind, setKind] = useState<string>('page');
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!target.trim() || !question.trim()) { ctx.notify('Enter a target and a question.', 'err'); return; }
    setBusy(true);
    const res = await apiPost<_AR>('/website/analysis', ctx.role, { target, target_kind: kind, question });
    setBusy(false);
    if (!res || !res.request_id) { ctx.notify('Could not file the request — leadership access (the Marketing Lead / admin) required.', 'err'); return; }
    ctx.notify(`Requested analysis on ${target} → Decision Queue.`, 'ok', '/decision');
    setTarget(''); setQuestion(''); ctx.refetch();
  };

  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
      <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <span>Request analysis</span>
        <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 400, opacity: 0.85 }}>→ DECISION QUEUE</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '14px 16px' }}>
        {!ctx.isLeader && <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)', alignSelf: 'flex-start' }}>◌ LEADERSHIP INPUT — LEADER/ADMIN ONLY</span>}
        <Field label="TARGET (page path or campaign)">
          <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="/tuition or spring_open_house" style={INPUT} disabled={!ctx.isLeader} />
        </Field>
        <Field label="KIND">
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={SELECT} disabled={!ctx.isLeader}>
            {TARGET_KINDS.map((k) => <option key={k} value={k}>{humanLabel(k)}</option>)}
          </select>
        </Field>
        <Field label="QUESTION">
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} placeholder="What should the analysis answer?" style={{ ...INPUT, resize: 'vertical' }} disabled={!ctx.isLeader} />
        </Field>
        <button onClick={submit} disabled={!ctx.isLeader || busy} style={{ ...PRIMARY_BTN, opacity: !ctx.isLeader || busy ? 0.5 : 1, cursor: !ctx.isLeader || busy ? 'default' : 'pointer' }}>{busy ? 'FILING…' : 'FILE REQUEST →'}</button>
      </div>
    </div>
  );
}

// =============================== 13b · SUBPAGES =============================
const SUB_GRID = '1.7fr .9fr .7fr .7fr .6fr .6fr .6fr .9fr';
const SORTS: { key: string; label: string }[] = [
  { key: 'pageviews', label: 'Pageviews' },
  { key: 'unique_visitors', label: 'Unique' },
  { key: 'avg_time_on_page', label: 'Avg time' },
  { key: 'bounce_rate', label: 'Bounce' },
  { key: 'exit_rate', label: 'Exit' },
  { key: 'conversions', label: 'Conversions' },
];

function SubpagesTab({ sp, ctx }: { sp: SubpagesResponse; ctx: Ctx }) {
  const [site, setSite] = useState('');
  const [ptype, setPtype] = useState('');
  const [sortKey, setSortKey] = useState('pageviews');

  const shown = useMemo(() => {
    let rows = sp.pages.filter((p) => (!site || p.site === site) && (!ptype || p.page_type === ptype));
    const get = (p: Subpage): number => {
      switch (sortKey) {
        case 'unique_visitors': return p.unique_visitors;
        case 'avg_time_on_page': return p.avg_time_on_page_s;
        case 'bounce_rate': return p.bounce_rate;
        case 'exit_rate': return p.exit_rate;
        case 'conversions': return p.conversions;
        default: return p.pageviews;
      }
    };
    return [...rows].sort((a, b) => get(b) - get(a));
  }, [sp.pages, site, ptype, sortKey]);

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <select value={site} onChange={(e) => setSite(e.target.value)} style={{ ...SELECT, width: 'auto', minWidth: 150 }}>
            <option value="">All sites</option>
            {sp.sites.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={ptype} onChange={(e) => setPtype(e.target.value)} style={{ ...SELECT, width: 'auto', minWidth: 130 }}>
            <option value="">All page types</option>
            {sp.page_types.map((t) => <option key={t} value={t}>{humanLabel(t)}</option>)}
          </select>
          <select value={sortKey} onChange={(e) => setSortKey(e.target.value)} style={{ ...SELECT, width: 'auto', minWidth: 130 }}>
            {SORTS.map((s) => <option key={s.key} value={s.key}>Sort: {s.label}</option>)}
          </select>
        </div>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{shown.length} of {sp.pages.length} pages · refresh ≥ {fmtPct01(sp.bounce_warn_pct)} bounce</span>
      </div>

      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: SUB_GRID, fontFamily: MONO, fontSize: 8, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 14px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>PAGE</div>
          <div>TYPE · SITE</div>
          <div style={{ textAlign: 'right' }}>VIEWS</div>
          <div style={{ textAlign: 'right' }}>UNIQUE</div>
          <div style={{ textAlign: 'right' }}>TIME</div>
          <div style={{ textAlign: 'right' }}>BOUNCE</div>
          <div style={{ textAlign: 'right' }}>CONV</div>
          <div style={{ textAlign: 'right' }}>ACTION</div>
        </div>
        {shown.map((p) => {
          const ta = trendArrow(p.trend_pct);
          return (
            <div key={`${p.site}${p.page_path}`} style={{ display: 'grid', gridTemplateColumns: SUB_GRID, alignItems: 'center', padding: '8px 14px', borderBottom: '1px solid var(--line)', background: p.refresh_candidate ? 'var(--warn-soft)' : 'transparent' }}>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pageLabel(p.page_path)}</div>
              <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{humanLabel(p.page_type)} · {p.site.replace('.gt.school', '')}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{fmtNum(p.pageviews)} <span style={{ color: ta.color, fontSize: 9 }}>{ta.glyph}</span></div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{fmtNum(p.unique_visitors)}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{fmtDur(p.avg_time_on_page_s)}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: p.refresh_candidate ? 'var(--warn)' : 'var(--ink-2)', fontWeight: p.refresh_candidate ? 600 : 400 }}>{fmtPct01(p.bounce_rate)}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{p.conversions}</div>
              <div style={{ textAlign: 'right' }}><FlagButton page={p} ctx={ctx} /></div>
            </div>
          );
        })}
        {shown.length === 0 && <Empty>No pages match these filters.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Highlighted rows clear the {fmtPct01(sp.bounce_warn_pct)} bounce threshold — content-refresh candidates. {ctx.isLeader ? '"⚑ Flag" POSTs a Content refresh brief (Module 3) + raises a Decision (Module 11).' : 'Flagging a page for refresh is leadership input (the Marketing Lead / admin).'}
      </div>
    </>
  );
}

function FlagButton({ page, ctx }: { page: Subpage; ctx: Ctx }) {
  const [busy, setBusy] = useState(false);
  if (!ctx.isLeader) return <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>—</span>;
  const flag = async () => {
    setBusy(true);
    const reason = `${fmtPct01(page.bounce_rate)} bounce · ${page.trend_pct >= 0 ? '+' : ''}${page.trend_pct}% WoW — flagged for content refresh.`;
    const res = await apiPost<FlagPageResponse>('/website/pages/flag', ctx.role, { page_path: page.page_path, site: page.site, reason });
    setBusy(false);
    if (!res || !res.flag) { ctx.notify('Could not flag — leadership access required and the backbone must be up.', 'err'); return; }
    ctx.notify(`Flagged ${page.page_path} → "${res.brief_title}" drafted in Content + a Decision raised.`, 'ok', '/content');
    ctx.refetch();
  };
  return <button onClick={flag} disabled={busy} style={MINI_BTN}>{busy ? '…' : '⚑ FLAG'}</button>;
}

// =============================== 13c · TRAFFIC =============================
function TrafficTab({ tr }: { tr: TrafficResponse }) {
  const bd = tr.breakdown;
  const utm = tr.utm_validation;
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="SESSIONS · ALL CHANNELS" value={fmtNum(bd.total_sessions)} sub={`${bd.channels.length} channels`} />
        <StatTile label="CONVERSIONS" value={fmtNum(bd.total_conversions)} sub="across all sources" />
        <StatTile label="UTM HEALTH · CAMPAIGNS" value={`${utm.health_pct}%`} sub={`${utm.broken_count} of ${utm.total} broken at origin`} tone={utm.broken_count === 0 ? 'ok' : 'warn'} />
      </div>

      {/* channel breakdown */}
      <Panel title="Traffic by channel" hint="organic · direct · social · email · referral" bordered>
        {bd.channels.map((c) => (
          <div key={c.channel} style={{ padding: '8px 0', borderTop: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
                <span style={{ width: 9, height: 9, background: channelColor(c.channel) }} /> {humanLabel(c.channel)}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{fmtNum(c.sessions)} · {c.share_pct}% · conv {fmtPctRate(c.conversion_rate)}</span>
            </div>
            <Bar pct={c.share_pct} color={channelColor(c.channel)} />
          </div>
        ))}
        <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 8 }}>Social splits by platform: {bd.social_platforms.map((p) => `${humanLabel(p.platform)} ${fmtNum(p.sessions)}`).join(' · ')}</div>
      </Panel>

      {/* UTM validation → CRM Ops */}
      <div style={{ border: `1px solid ${utm.broken_count > 0 ? 'var(--warn)' : 'var(--ok)'}`, background: utm.broken_count > 0 ? 'var(--warn-soft)' : 'var(--ok-soft)', marginTop: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: `1px solid ${utm.broken_count > 0 ? 'var(--warn)' : 'var(--ok)'}` }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>UTM source validation</div>
          <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: utm.broken_count > 0 ? 'var(--warn)' : 'var(--ok)' }}>{utm.broken_count > 0 ? `⚠ ${utm.broken_count} BROKEN AT ORIGIN` : '✓ ALL HEALTHY'}</span>
        </div>
        <div style={{ padding: '10px 16px' }}>
          {utm.broken_campaigns.map((c, i) => (
            <div key={i} style={{ padding: '8px 0', borderTop: i ? '1px solid var(--line)' : 'none' }}>
              <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink)' }}>
                {c.utm_source || '∅'} / <span style={{ color: 'var(--signal)' }}>{c.utm_medium || '∅'}</span> / {c.utm_campaign || '∅'} <span style={{ color: 'var(--ink-3)' }}>· {fmtNum(c.sessions)} sessions → {c.landing_page}</span>
              </div>
              <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 2 }}>{c.reasons.join(' · ')}</div>
            </div>
          ))}
          {utm.broken_campaigns.length === 0 && <Empty>Every tagged campaign passes the rule set.</Empty>}
          <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 8 }}>The website is where UTM tags ORIGINATE — these flow into the <Link href="/crm" style={LINK}>CRM Ops</Link> attribution chain. Same rule set, detect-only (we flag, never auto-fix here).</div>
        </div>
      </div>

      {/* source × page matrix */}
      <Panel title="Source × page" hint="which channels land on which pages" bordered style={{ marginTop: 14 }}>
        {tr.source_pages.slice(0, 10).map((c, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr .7fr', alignItems: 'center', gap: 8, padding: '6px 0', borderTop: '1px solid var(--line)' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}><span style={{ width: 8, height: 8, background: channelColor(c.channel) }} />{humanLabel(c.channel)}</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink)' }}>{pageLabel(c.page_path)}</span>
            <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 600, color: 'var(--ink)', textAlign: 'right' }}>{fmtNum(c.sessions)}</span>
          </div>
        ))}
      </Panel>
    </>
  );
}

// =============================== 13d · DOWNLOADS ============================
const DL_GRID = '2.4fr 1.4fr .9fr .7fr .8fr .8fr';

function DownloadsTab({ dl }: { dl: DownloadsResponse }) {
  const s = dl.summary;
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="DOWNLOADS · THIS WEEK" value={fmtNum(s.total_weekly)} sub={`${s.wow_delta_pct >= 0 ? '+' : ''}${s.wow_delta_pct}% vs last week`} tone={s.wow_delta_pct >= 0 ? 'ok' : 'warn'} />
        <StatTile label="CUMULATIVE" value={fmtNum(s.total_cumulative)} sub="all-time downloads" />
        <StatTile label="TRACKED ASSETS" value={String(dl.downloads.length)} sub="PDFs + resources" />
      </div>
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: DL_GRID, fontFamily: MONO, fontSize: 8, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 14px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>FILE</div>
          <div>REFERRING PAGE</div>
          <div>SOURCE</div>
          <div style={{ textAlign: 'right' }}>WK</div>
          <div style={{ textAlign: 'right' }}>TREND</div>
          <div style={{ textAlign: 'right' }}>CUMUL.</div>
        </div>
        {dl.downloads.map((d) => {
          const trendPct = d.prev_weekly_count > 0 ? Math.round((100 * (d.weekly_count - d.prev_weekly_count)) / d.prev_weekly_count) : 0;
          const ta = trendArrow(trendPct);
          return (
            <div key={d.file_name} style={{ display: 'grid', gridTemplateColumns: DL_GRID, alignItems: 'center', padding: '9px 14px', borderBottom: '1px solid var(--line)' }}>
              <div style={{ fontSize: 10.5, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.file_name}</div>
              <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-2)' }}>{d.referring_page}</div>
              <div><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}><span style={{ width: 7, height: 7, background: channelColor(d.source) }} />{humanLabel(d.source)}</span></div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{d.weekly_count}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10, fontWeight: 600, color: ta.color }}>{ta.glyph}{Math.abs(trendPct)}%</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{fmtNum(d.cumulative_count)}</div>
            </div>
          );
        })}
        {dl.downloads.length === 0 && <Empty>No downloads tracked.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>Every asset carries its referring page + the visitor&rsquo;s original source. → the most-accessed resources feed the <Link href="/resources" style={LINK}>Resource Library</Link> (Module 12).</div>
    </>
  );
}

// =============================== 13e · PATHS ===============================
function PathsTab({ pa }: { pa: PathsResponse }) {
  const top = pa.funnel[0]?.sessions ?? 1;
  return (
    <>
      {/* funnel drop-off */}
      <Panel title="Landing → application funnel" hint="drop-off at each step" bordered>
        {pa.funnel.map((st, i) => (
          <div key={st.stage} style={{ padding: '9px 0', borderTop: i ? '1px solid var(--line)' : 'none' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>{humanLabel(st.stage)}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>
                {fmtNum(st.sessions)} · {st.of_top_pct}% of top{i > 0 ? ` · −${st.drop_from_prev_pct}% step` : ''}
              </span>
            </div>
            <Bar pct={Math.round((100 * st.sessions) / top)} color={i === pa.funnel.length - 1 ? 'var(--ok)' : 'var(--signal)'} />
          </div>
        ))}
      </Panel>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 14 }}>
        {/* key conversion pages */}
        <Panel title="Key conversion pages" hint="by form-submission rate">
          {pa.key_conversion_pages.map((p) => (
            <div key={`${p.site}${p.page_path}`} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 0', borderTop: '1px solid var(--line)' }}>
              <div>
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{pageLabel(p.page_path)}</div>
                <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{p.site} · {p.form_submissions}/{fmtNum(p.sessions)}</div>
              </div>
              <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 700, color: p.submission_rate >= 0.2 ? 'var(--ok)' : 'var(--ink)' }}>{fmtPctRate(p.submission_rate)}</span>
            </div>
          ))}
        </Panel>

        {/* homepage flows + cross-site */}
        <Panel title="User flow & cross-site">
          <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 4 }}>HOMEPAGE → NEXT</div>
          {pa.path_flows.map((f, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderTop: '1px solid var(--line)' }}>
              <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{f.from_page} → <span style={{ color: 'var(--ink)' }}>{f.to_page}</span></span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 600, color: 'var(--ink)' }}>{fmtNum(f.sessions)}</span>
            </div>
          ))}
          <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600, margin: '12px 0 4px' }}>CROSS-SITE FLOW</div>
          {pa.cross_site_flows.map((c, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderTop: '1px solid var(--line)' }}>
              <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{c.from_site.replace('.gt.school', '')} → {c.to_site.replace('.gt.school', '')}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 600, color: 'var(--ink)' }}>{fmtNum(c.sessions)}</span>
            </div>
          ))}
        </Panel>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 12 }}>The digital journey before a lead enters the funnel → feeds <Link href="/nurture" style={LINK}>Nurture &amp; Lifecycle</Link> (Module 5).</div>
    </>
  );
}

// ============================ shared primitives ==============================
function Panel({ title, hint, children, bordered, style }: { title: string; hint?: string; children: React.ReactNode; bordered?: boolean; style?: React.CSSProperties }) {
  return (
    <div style={{ border: bordered ? '1px solid var(--ink)' : '1px solid var(--line-2)', background: 'var(--card)', padding: bordered ? 0 : 14, ...style }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: bordered ? '10px 16px' : '0 0 8px', borderBottom: bordered ? '2px solid var(--ink)' : 'none', marginBottom: bordered ? 0 : 2 }}>
        <span style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>{title}</span>
        {hint && <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{hint}</span>}
      </div>
      <div style={{ padding: bordered ? '4px 16px 14px' : 0 }}>{children}</div>
    </div>
  );
}

function Bar({ pct, color }: { pct: number; color: string }) {
  return (
    <div style={{ height: 7, background: 'var(--card-2)', position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', inset: 0, width: `${Math.max(0, Math.min(100, pct))}%`, background: color }} />
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

function StatusPill({ live }: { live: boolean }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', color: live ? 'var(--ok)' : 'var(--ink-3)', background: live ? 'var(--ok-soft)' : 'var(--accent-soft)' }}>
      {live ? '● LIVE' : '○ SAMPLE · offline seed'}
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '22px 16px', textAlign: 'center', fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)' }}>{children}</div>;
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

const LINK: React.CSSProperties = { color: 'var(--ink)', fontWeight: 600 };
const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };
const MINI_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 8.5, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--signal)', background: 'var(--signal-soft)', color: 'var(--signal)', padding: '4px 8px', whiteSpace: 'nowrap' };
