'use client';

// Summer Camp (Module 4) — a SEPARATE P&L workstream, not part of the $365K
// marketing budget. Four controlled sub-views (TabBar):
//   4a Overview            — reconciled stat grid (capacity sold / regs this week /
//                            paid % / days-to-start / top channel / camp content
//                            shipped / revenue-vs-target with an HONEST basis label /
//                            waitlist) + the dual-source dedup banner + the
//                            "ads paused — organic only" note.
//   4b Registration funnel — Lead → Registered → Paid → Attended from funnel[],
//                            with drop-off % + the pending flag rendered honestly,
//                            SLICEABLE by campus / grade band / source (re-fetches
//                            /summer/reconcile with the query params).
//   4c Content + campaigns — the camp-tagged subset of the live content board
//                            (GET /summer/content) — read-only; it LIVES in Module 3.
//   4d Sessions            — four campus cards (per_campus ⊕ sessions[]) with a
//                            per-campus drill-in + an OWNER-GATED "propose session/
//                            pricing change" → POST /summer/session-change → the
//                            Decision Queue.
// Live wiring: GET /summer/reconcile + GET /summer/content. Each falls back to a
// distinct seed (lib/camp-api) so the screen never blanks; the LIVE/SAMPLE pill is
// derived honestly from whether the fetch landed.

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { moduleById, canEditWorkstream, type Role, type Session } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet, apiPost } from '@/lib/api';
import { channelColor, channelLabel } from '@/lib/content-api';
import {
  SEED_RECONCILE,
  SEED_CONTENT,
  GRADE_BANDS,
  SOURCE_OPTIONS,
  CHANGE_TYPES,
  campusCity,
  revenueBasisLabel,
  regChannelLabel,
  regChannelColor,
  fmtSessionRange,
  fmtSessionDate,
  type SummerReconcile,
  type SummerContent,
  type FunnelStageRow,
  type SummerCampusRow,
  type SummerSession,
  type DecisionResponse,
} from '@/lib/camp-api';

const MONO = 'JetBrains Mono';
const ARCHIVO = 'Fraunces';

interface Toast {
  msg: string;
  kind: 'ok' | 'err';
}

// =========================== the module ======================================
export function CampModule() {
  const def = moduleById('camp')!;
  const { session } = useSession();
  const [data, setData] = useState<SummerReconcile | null>(null); // null = loading
  const [isLive, setIsLive] = useState(false);
  const [content, setContent] = useState<SummerContent | null>(null);
  const [contentLive, setContentLive] = useState(false);
  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    let active = true;
    apiGet<SummerReconcile>('/summer/reconcile', session.role).then((res) => {
      if (!active) return;
      if (res && Array.isArray(res.per_campus) && res.per_campus.length > 0) {
        setData(res);
        setIsLive(true);
      } else {
        setData(SEED_RECONCILE);
        setIsLive(false);
      }
    });
    apiGet<SummerContent>('/summer/content', session.role).then((res) => {
      if (!active) return;
      if (res && Array.isArray(res.columns)) {
        setContent(res);
        setContentLive(true);
      } else {
        setContent(SEED_CONTENT);
        setContentLive(false);
      }
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback((msg: string, kind: 'ok' | 'err') => setToast({ msg, kind }), []);

  if (data === null) {
    return (
      <>
        <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
        <section className="scr" style={{ padding: '20px 22px 40px' }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)' }}>Loading summer camp…</div>
        </section>
      </>
    );
  }

  const cnt = content ?? SEED_CONTENT;

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      {tab === 0 && <OverviewTab data={data} isLive={isLive} content={cnt} />}
      {tab === 1 && <FunnelTab base={data} role={session.role} baseLive={isLive} />}
      {tab === 2 && <ContentTab content={cnt} live={contentLive} />}
      {tab === 3 && (
        <SessionsTab data={data} isLive={isLive} role={session.role} session={session} notify={notify} />
      )}
    </>
  );
}

// ============================ 4a Overview ====================================
function OverviewTab({ data, isLive, content }: { data: SummerReconcile; isLive: boolean; content: SummerContent }) {
  const t = data.totals;
  const capPct = t.capacity > 0 ? Math.round((t.registered / t.capacity) * 100) : 0;
  const paidPct = t.registered > 0 ? Math.round((t.paid / t.registered) * 100) : 0;

  const dupMerged = data.dedup?.duplicates_merged ?? 0;
  const conflicts = data.dedup?.conflicts?.length ?? 0;
  const uniqueReg = data.dedup?.unique_registrations ?? t.registered;

  // Top signup channel — live top of registration_channels (never hardcoded).
  const topCh = (data.registration_channels ?? [])[0];

  // Earliest scheduled session → the "days to camp start" context line.
  const firstSession = [...(data.sessions ?? [])].sort((a, b) => a.starts_on.localeCompare(b.starts_on))[0];
  const firstCampuses = (data.sessions ?? [])
    .filter((s) => firstSession && s.starts_on === firstSession.starts_on)
    .map((s) => s.campus)
    .join(' & ');

  // Camp content shipped this week — Scheduled + Live camp-tagged rows.
  const shipped = (content.rows ?? []).filter((r) => r.stage === 'Scheduled' || r.stage === 'Live').length;

  // Revenue + honest basis label.
  const rev = data.revenue;
  const basis = revenueBasisLabel(rev?.basis ?? '');

  // Waitlist / overflow across sessions.
  const waitTotal = (data.waitlist ?? []).reduce((a, w) => a + (w.waitlisted ?? 0), 0);

  const stats: Stat[] = [
    {
      label: 'CAPACITY SOLD',
      value: `${capPct}%`,
      valueSub: `${t.registered} / ${t.capacity} seats`,
      valueColor: 'var(--ink)',
      note: 'across 4 campuses · reconciled',
      hero: true,
      bar: capPct,
    },
    {
      label: 'REGISTRATIONS THIS WEEK',
      value: String(data.registrations_this_week ?? 0),
      valueColor: 'var(--ink)',
      note: 'new registrations · last 7 days',
    },
    {
      label: 'REGISTERED → PAID',
      value: `${paidPct}%`,
      valueColor: 'var(--ok)',
      note: `${t.paid} paid of ${t.registered} registered`,
    },
    {
      label: 'DAYS TO CAMP START',
      value: String(data.days_to_camp_start ?? 0),
      valueColor: 'var(--ink)',
      note: firstSession ? `first session ${fmtSessionDate(firstSession.starts_on)} · ${firstCampuses}` : 'first session',
    },
    {
      label: 'TOP SIGNUP CHANNEL',
      value: topCh ? regChannelLabel(topCh.channel) : '—',
      valueColor: topCh ? regChannelColor(topCh.channel) : 'var(--ink-3)',
      note: topCh ? `${topCh.pct}% of registrations · organic` : 'no channel data',
    },
    {
      label: 'CAMP CONTENT SHIPPED',
      value: String(shipped),
      valueColor: 'var(--ink)',
      note: 'scheduled / live · camp-tagged → Module 3',
    },
    {
      label: 'REVENUE',
      value: `$${Math.round((rev?.revenue_usd ?? 0) / 1000)}K`,
      valueSub: `/ $${Math.round((rev?.target_usd ?? 0) / 1000)}K target`,
      valueColor: 'var(--ink)',
      note: `${rev?.pct_to_target ?? 0}% to target · ${basis.label}`,
    },
    {
      label: 'WAITLIST / OVERFLOW',
      value: String(waitTotal),
      valueColor: waitTotal > 0 ? 'var(--warn)' : 'var(--ink-3)',
      note: waitTotal > 0 ? 'across full sessions' : 'no overflow yet · seats remain',
    },
  ];

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          SUMMER CAMP · SEPARATE P&amp;L
        </span>
        <StatusPill live={isLive} />
      </div>

      {/* Dual-source reconciliation banner */}
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: '13px 16px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 320 }}>
          <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>
            Registrations reconcile two sources — no double-counting
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 3, lineHeight: 1.5, maxWidth: 660 }}>
            <b>summer.gt.school</b> (primary) is merged with the standalone <b>registration form</b>; records are
            deduplicated on the household identity key before any count is shown
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
          <span style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 600, padding: '4px 10px', background: conflicts > 0 ? 'var(--warn-soft)' : 'var(--ok-soft)', color: conflicts > 0 ? 'var(--warn)' : 'var(--ok)' }}>
            {conflicts > 0 ? `⚑ RECONCILED · ${conflicts} TO REVIEW` : `✓ RECONCILED · ${dupMerged} MERGED · 0 DUPLICATES`}
          </span>
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
            summer.gt.school ⊕ reg form → {uniqueReg} unique
          </span>
        </div>
      </div>

      {/* Stat grid (all live) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        {stats.map((s) => (
          <StatCard key={s.label} s={s} />
        ))}
        {/* No paid-acquisition note rides the trailing grid cell */}
        <div style={{ border: '1px dashed var(--line-2)', background: 'var(--paper)', padding: 13, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
            ⏸ NO PAID-ACQUISITION VIEW
          </div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.4 }}>
            Ads are paused for camp — growth is organic + referral only.
          </div>
        </div>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.6 }}>
        ⌖ Every number above is summed from the live dual-source reconcile (each registrant counted once). Revenue
        basis is surfaced honestly — <b>{basis.label}</b>{basis.live ? '' : ' (not yet Stripe-collected)'}. The funnel,
        sessions and camp content live in the tabs above.
      </div>
    </section>
  );
}

interface Stat {
  label: string;
  value: string;
  valueSub?: string;
  valueColor: string;
  note: string;
  hero?: boolean;
  bar?: number; // optional 0..100 capacity bar
}
function StatCard({ s }: { s: Stat }) {
  return (
    <div style={{ border: `1px solid ${s.hero ? 'var(--ink)' : 'var(--line-2)'}`, background: s.hero ? 'var(--card-2)' : 'var(--card)', padding: 13 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: s.hero ? 'var(--ink)' : 'var(--ink-3)', fontWeight: s.hero ? 600 : 400 }}>
        {s.label}
      </div>
      <div style={{ fontFamily: s.hero ? ARCHIVO : MONO, fontWeight: s.hero ? 700 : 600, fontSize: s.hero ? 27 : 22, color: s.valueColor, marginTop: 5, lineHeight: 1.05 }}>
        {s.value}
        {s.valueSub && <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)', fontWeight: 400 }}> {s.valueSub}</span>}
      </div>
      {typeof s.bar === 'number' && (
        <div style={{ height: 5, background: 'var(--card)', border: '1px solid var(--line)', marginTop: 7, overflow: 'hidden' }}>
          <div style={{ width: `${Math.min(s.bar, 100)}%`, height: '100%', background: 'var(--gold)', opacity: 0.9 }} />
        </div>
      )}
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: s.bar !== undefined ? 5 : 2 }}>{s.note}</div>
    </div>
  );
}

// ====================== 4b Registration funnel ===============================
const STAGE_COLOR: Record<string, string> = {
  Lead: 'var(--ink-3)',
  Registered: 'var(--gold)',
  Paid: 'var(--ok)',
  Attended: 'var(--signal)',
};

function FunnelTab({ base, role, baseLive }: { base: SummerReconcile; role: Role; baseLive: boolean }) {
  const [campus, setCampus] = useState('');
  const [gradeBand, setGradeBand] = useState('');
  const [source, setSource] = useState('');
  const [data, setData] = useState<SummerReconcile>(base);
  const [loading, setLoading] = useState(false);

  const campusOptions = (base.per_campus ?? []).map((c) => c.campus);
  const sliced = !!(campus || gradeBand || source);

  // Re-fetch /summer/reconcile with the active slice (each param ANDs the previous).
  useEffect(() => {
    let active = true;
    const params = new URLSearchParams();
    if (campus) params.set('campus', campus);
    if (gradeBand) params.set('grade_band', gradeBand);
    if (source) params.set('source', source);
    const qs = params.toString();
    if (!qs) {
      setData(base);
      return;
    }
    setLoading(true);
    apiGet<SummerReconcile>(`/summer/reconcile?${qs}`, role).then((res) => {
      if (!active) return;
      if (res && Array.isArray(res.funnel)) setData(res);
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [campus, gradeBand, source, role, base]);

  const funnel: FunnelStageRow[] = data.funnel ?? [];
  const funnelMax = Math.max(1, ...funnel.map((s) => s.count));
  const t = data.totals;
  const reset = () => {
    setCampus('');
    setGradeBand('');
    setSource('');
  };

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          LEAD → REGISTERED → PAID → ATTENDED
        </span>
        <StatusPill live={baseLive} />
      </div>

      {/* Slicers */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '12px 16px', marginBottom: 14, display: 'flex', alignItems: 'flex-end', gap: 14, flexWrap: 'wrap' }}>
        <Slicer label="CAMPUS" value={campus} onChange={setCampus} options={[{ value: '', label: 'All campuses' }, ...campusOptions.map((c) => ({ value: c, label: c }))]} />
        <Slicer label="GRADE BAND" value={gradeBand} onChange={setGradeBand} options={[{ value: '', label: 'All grades' }, ...GRADE_BANDS.map((g) => ({ value: g, label: `Grades ${g}` }))]} />
        <Slicer label="SOURCE" value={source} onChange={setSource} options={[{ value: '', label: 'Both sources' }, ...SOURCE_OPTIONS]} />
        {sliced && (
          <button onClick={reset} style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--line-2)', background: 'var(--card-2)', color: 'var(--ink-2)', padding: '7px 12px' }}>
            ✕ CLEAR SLICE
          </button>
        )}
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 9, color: loading ? 'var(--gold)' : 'var(--ink-3)' }}>
          {loading ? 'slicing…' : sliced ? 're-fetched /summer/reconcile' : 'showing all registrations'}
        </span>
      </div>

      {/* Funnel */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Registration funnel</div>
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
            {sliced ? `slice: ${[campus, gradeBand && `grades ${gradeBand}`, source && SOURCE_OPTIONS.find((o) => o.value === source)?.label].filter(Boolean).join(' · ')}` : 'all registrations'} · {t.registered} reg / {t.paid} paid
          </span>
        </div>
        <div style={{ padding: '16px', display: 'grid', gap: 10 }}>
          {funnel.map((s) => {
            const pct = Math.round((s.count / funnelMax) * 100);
            return (
              <div key={s.stage}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ width: 96, fontSize: 12, color: 'var(--ink)', fontWeight: 500 }}>{s.stage}</div>
                  <div style={{ flex: 1, height: 22, background: 'var(--card-2)', position: 'relative', overflow: 'hidden' }}>
                    <div style={{ width: `${Math.max(pct, 2)}%`, height: '100%', background: STAGE_COLOR[s.stage] ?? 'var(--gold)', opacity: 0.85 }} />
                  </div>
                  <div style={{ width: 50, textAlign: 'right', fontFamily: MONO, fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{s.count}</div>
                  <div style={{ width: 56, textAlign: 'right', fontFamily: MONO, fontSize: 10, color: s.drop_off_pct > 0 ? 'var(--warn)' : 'var(--ink-3)' }}>
                    {s.drop_off_pct > 0 ? `−${s.drop_off_pct}%` : '—'}
                  </div>
                </div>
                {s.pending && (
                  <div style={{ marginLeft: 108, marginTop: 3, fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
                    {s.stage === 'Lead'
                      ? '⚑ pending · count ≥ registered (pre-registration is not instrumented yet)'
                      : s.stage === 'Attended'
                        ? '⚑ pending · camp runs in August — attendance fills as sessions run'
                        : '⚑ pending'}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div style={{ padding: '0 16px 12px', fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          Drop-off shown per stage (the % lost entering that stage). Pending stages are labeled honestly — nothing fabricated.
        </div>
      </div>
    </section>
  );
}

function Slicer({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ fontFamily: 'Geist', fontSize: 12, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, minWidth: 150 }}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  );
}

// ====================== 4c Content + campaigns ===============================
function ContentTab({ content, live }: { content: SummerContent; live: boolean }) {
  const cols = content.columns ?? [];
  const total = (content.rows ?? []).length;
  const sync = content.sync;

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          CAMP-TAGGED CONTENT (utm ^= camp_)
        </span>
        <StatusPill live={live} />
      </div>

      {/* Cross-link note — this content LIVES in Module 3 */}
      <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card)', padding: '11px 14px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)' }}>↪ CROSS-MODULE</span>
        <span style={{ flex: 1, fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.5 }}>
          Camp content lives in <Link href="/content" style={{ color: 'var(--ink)', fontWeight: 600 }}>Module 3 · Content</Link> — this board is a{' '}
          <b>read-only</b> filter to the {total} camp-tagged piece{total === 1 ? '' : 's'}. Edits happen in the content owner&apos;s pipeline.
        </span>
        <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: sync?.mode === 'live' ? 'var(--ok-soft)' : 'var(--warn-soft)', color: sync?.mode === 'live' ? 'var(--ok)' : 'var(--warn)' }}>
          {sync?.mode === 'live' ? '● GOOGLE SHEET · SYNCED' : '○ SIMULATED SEAM'}
        </span>
      </div>

      {/* Camp content board (by stage) */}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols.length || 1}, 1fr)`, gap: 1, background: 'var(--line)', border: '1px solid var(--line-2)' }}>
        {cols.map((col) => (
          <div key={col.stage} style={{ background: 'var(--card-2)', padding: '10px 9px', minHeight: 220 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 9 }}>
              <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.3px', color: 'var(--ink-2)' }}>{col.stage}</span>
              <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{col.cards.length}</span>
            </div>
            {col.cards.map((c) => {
              const col2 = channelColor(c.channel);
              return (
                <div key={c.title} style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '8px 9px', marginBottom: 7 }}>
                  <div style={{ fontSize: 10.5, color: 'var(--ink)', fontWeight: 500, lineHeight: 1.3 }}>{c.title}</div>
                  <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginTop: 5 }}>{c.owner}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 6, flexWrap: 'wrap' }}>
                    <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: col2.bg, color: col2.fg }}>{channelLabel(c.channel)}</span>
                    <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-2)' }}>{c.type} · {c.target_date}</span>
                  </div>
                  {c.utm && (
                    <div style={{ fontFamily: MONO, fontSize: 7.5, color: 'var(--gold)', marginTop: 5 }}>⛓ {c.utm}</div>
                  )}
                </div>
              );
            })}
            {col.cards.length === 0 && (
              <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', padding: '4px 2px' }}>—</div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

// ============================ 4d Sessions ====================================
function SessionsTab({
  data,
  isLive,
  role,
  session,
  notify,
}: {
  data: SummerReconcile;
  isLive: boolean;
  role: Role;
  session: Session;
  notify: (msg: string, kind: 'ok' | 'err') => void;
}) {
  const canEdit = canEditWorkstream(session, 'camp'); // admin always; operator only if owns 'camp' (demo: admin only)
  const campuses = data.per_campus ?? [];
  const sessionByCampus: Record<string, SummerSession> = {};
  for (const s of data.sessions ?? []) sessionByCampus[s.campus] = s;
  const waitByCampus: Record<string, number> = {};
  for (const w of data.waitlist ?? []) waitByCampus[w.campus] = w.waitlisted ?? 0;

  const [open, setOpen] = useState<string | null>(null);
  const agg = campuses.reduce((a, c) => ({ registered: a.registered + c.registered, capacity: a.capacity + c.capacity }), { registered: 0, capacity: 0 });

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          3× TWO-WEEK · 1× ONE-WEEK — {agg.registered} REG / {agg.capacity} SEATS
        </span>
        <StatusPill live={isLive} />
      </div>

      {/* Owner-gating note */}
      <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card)', padding: '10px 14px', marginBottom: 14, fontSize: 10.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>
        {canEdit ? (
          <>
            <b style={{ color: 'var(--ink)' }}>You can propose changes.</b> Expand a campus to propose a session/pricing change — it
            posts to the leadership <Link href="/decision" style={{ color: 'var(--ink)', fontWeight: 600 }}>Decision Queue</Link> (owner-gated).
          </>
        ) : (
          <>
            Expand a campus for the per-campus rollup. <b>Read-only</b> — only the camp owner (admin) proposes session/pricing
            changes; leadership decides them in the <Link href="/decision" style={{ color: 'var(--ink)', fontWeight: 600 }}>Decision Queue</Link>.
          </>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        {campuses.map((c) => (
          <CampusCard
            key={c.campus}
            c={c}
            session={sessionByCampus[c.campus]}
            waitlist={waitByCampus[c.campus] ?? 0}
            open={open === c.campus}
            onToggle={() => setOpen(open === c.campus ? null : c.campus)}
            canEdit={canEdit}
            role={role}
            notify={notify}
          />
        ))}
      </div>
    </section>
  );
}

function CampusCard({
  c,
  session,
  waitlist,
  open,
  onToggle,
  canEdit,
  role,
  notify,
}: {
  c: SummerCampusRow;
  session?: SummerSession;
  waitlist: number;
  open: boolean;
  onToggle: () => void;
  canEdit: boolean;
  role: Role;
  notify: (msg: string, kind: 'ok' | 'err') => void;
}) {
  const sold = c.capacity > 0 ? Math.round((c.registered / c.capacity) * 100) : 0;
  const twoWeek = session?.duration === '2wk';
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', gridColumn: open ? '1 / -1' : undefined }}>
      <div style={{ padding: '12px 13px 10px', borderBottom: '1px solid var(--line)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 14, color: 'var(--ink)' }}>{c.campus}</span>
          {session && (
            <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: twoWeek ? 'var(--gold-soft)' : 'var(--accent-soft)', color: twoWeek ? 'var(--gold)' : 'var(--ink-2)' }}>
              {twoWeek ? '2-WEEK' : '1-WEEK'}
            </span>
          )}
        </div>
        <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>{campusCity(c.campus)}</div>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-2)', marginTop: 4 }}>
          {session ? fmtSessionRange(session.starts_on, session.ends_on) : '—'}
        </div>
      </div>

      {/* capacity bar */}
      <div style={{ padding: '11px 13px 9px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', fontWeight: 600 }}>CAPACITY SOLD</span>
          <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{sold}%</span>
        </div>
        <div style={{ height: 6, background: 'var(--card-2)', overflow: 'hidden' }}>
          <div style={{ width: `${Math.min(sold, 100)}%`, height: '100%', background: 'var(--gold)', opacity: 0.9 }} />
        </div>
      </div>

      <div style={{ padding: '4px 13px 10px', display: 'grid', gap: 6 }}>
        <CardRow label="Capacity" value={c.capacity} />
        <CardRow label="Registered" value={c.registered} valueColor="var(--ink)" strong />
        <CardRow label="Paid" value={c.paid} valueColor="var(--ok)" />
        <CardRow label="Seats remaining" value={c.seats_remaining} valueColor={c.seats_remaining <= 0 ? 'var(--warn)' : 'var(--ink-2)'} />
        <CardRow label="Waitlist" value={waitlist} valueColor={waitlist > 0 ? 'var(--warn)' : 'var(--ink-3)'} />
      </div>

      {/* drill-in toggle */}
      <button onClick={onToggle} style={{ width: '100%', fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', cursor: 'pointer', border: 'none', borderTop: '1px solid var(--line)', background: open ? 'var(--card-2)' : 'transparent', color: 'var(--ink-2)', padding: '8px 0' }}>
        {open ? '▲ CLOSE' : '▼ DRILL IN'}
      </button>

      {open && (
        <div style={{ borderTop: '1px solid var(--line)', padding: '14px 16px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18 }}>
          {/* per-campus rollup + roster placeholder */}
          <div>
            <div style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)', marginBottom: 8 }}>PER-CAMPUS ROLLUP</div>
            <div style={{ display: 'grid', gap: 6, maxWidth: 320 }}>
              <CardRow label="Lead (pending instrumentation)" value={c.lead} />
              <CardRow label="Registered" value={c.registered} valueColor="var(--ink)" strong />
              <CardRow label="Paid" value={c.paid} valueColor="var(--ok)" />
              <CardRow label="Pct sold" value={c.pct_sold} suffix="%" />
            </div>
            <div style={{ marginTop: 12, border: '1px dashed var(--line-2)', background: 'var(--paper)', padding: '10px 12px' }}>
              <div style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.3px', color: 'var(--ink-3)' }}>ROSTER · ATTENDANCE</div>
              <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.5 }}>
                Pending — camp runs in August. No roster or attendance is shown (and <b>no child PII</b> ever lands in the Hub); the
                attendance funnel fills as sessions run.
              </div>
            </div>
          </div>

          {/* owner-gated propose-change */}
          <ProposeChange campus={c.campus} canEdit={canEdit} role={role} notify={notify} />
        </div>
      )}
    </div>
  );
}

function ProposeChange({ campus, canEdit, role, notify }: { campus: string; canEdit: boolean; role: Role; notify: (msg: string, kind: 'ok' | 'err') => void }) {
  const [changeType, setChangeType] = useState(CHANGE_TYPES[0].value);
  const [detail, setDetail] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    const res = await apiPost<DecisionResponse>('/summer/session-change', role, {
      campus,
      change_type: changeType,
      detail: detail.trim(),
    });
    setSaving(false);
    if (!res || !res.id) {
      notify('Could not propose the change — camp-owner (admin) access is required and the backbone must be up.', 'err');
      return;
    }
    notify(`Proposed a ${changeType.replace(/_/g, ' ')} change at ${campus} → open in the Decision Queue.`, 'ok');
    setDetail('');
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)' }}>PROPOSE SESSION / PRICING CHANGE</span>
        <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: canEdit ? 'var(--ok-soft)' : 'var(--accent-soft)', color: canEdit ? 'var(--ok)' : 'var(--ink-3)' }}>
          {canEdit ? 'OWNER · ENABLED' : 'READ-ONLY'}
        </span>
      </div>
      {canEdit ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 360 }}>
          <label style={FIELD_LABEL}>
            CHANGE TYPE
            <select value={changeType} onChange={(e) => setChangeType(e.target.value)} style={SELECT}>
              {CHANGE_TYPES.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <label style={FIELD_LABEL}>
            DETAIL (optional)
            <input value={detail} onChange={(e) => setDetail(e.target.value)} placeholder={`e.g. add a 3rd ${campus} week`} style={INPUT} />
          </label>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, alignItems: 'center' }}>
            <Link href="/decision" style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>view queue →</Link>
            <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>
              {saving ? 'PROPOSING…' : 'PROPOSE CHANGE'}
            </button>
          </div>
        </div>
      ) : (
        <div style={{ border: '1px dashed var(--line-2)', background: 'var(--paper)', padding: '12px 14px', fontSize: 10.5, color: 'var(--ink-2)', lineHeight: 1.5, maxWidth: 360 }}>
          Only the <b>camp owner (admin)</b> can propose a session or pricing change. Leadership reviews and decides any proposal in the{' '}
          <Link href="/decision" style={{ color: 'var(--ink)', fontWeight: 600 }}>Decision Queue</Link>.
        </div>
      )}
    </div>
  );
}

// ============================ shared bits ====================================
function CardRow({ label, value, valueColor = 'var(--ink-2)', strong = false, suffix = '' }: { label: string; value: number; valueColor?: string; strong?: boolean; suffix?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
      <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: strong ? 700 : 600, color: valueColor }}>{value}{suffix}</span>
    </div>
  );
}

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const ok = toast.kind === 'ok';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '12px 22px 0', padding: '10px 14px', background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)', border: `1px solid ${ok ? 'var(--ok)' : 'var(--signal)'}` }}>
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: ok ? 'var(--ok)' : 'var(--signal)' }}>{ok ? '✓ DONE' : '⚠ ERROR'}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink)' }}>{toast.msg}</span>
      {ok && <Link href="/decision" style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, color: 'var(--ok)' }}>open →</Link>}
      <button onClick={onClose} aria-label="Dismiss" style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>✕</button>
    </div>
  );
}

function StatusPill({ live }: { live: boolean }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, whiteSpace: 'nowrap', color: live ? 'var(--ok)' : 'var(--ink-3)', background: live ? 'var(--ok-soft)' : 'var(--accent-soft)' }}>
      {live ? '● LIVE' : '○ SAMPLE'}
    </span>
  );
}

// ---- shared style objects ---------------------------------------------------
const FIELD_LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 };
const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2 };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2 };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };
