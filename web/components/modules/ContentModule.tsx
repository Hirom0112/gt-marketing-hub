'use client';

// Content & Thought Leadership (Module 3) — the Content Owner's workshop.
//   • Production tracker is SYNCED read+write to a Google Sheet (the owner's
//     existing tool of record); summer-camp content lives in Module 4, not here.
//   • Pipeline kanban: Concept → In production → Review → Scheduled → Published.
//     (Untouched live seam — GET/POST /content/kanban, with a DERIVED sync banner.)
//   • Hero stats, calendar, performance, library and the brand-voice auditor are
//     wired to the live FastAPI endpoints (app/api/content.py) via lib/content-api;
//     each falls back to a distinct seed so the screen never blanks.
//   • Calendar is channel-color-coded with a same-day conflict marker (⚑) coming
//     from the API; drag-to-reschedule persists (owner-gated, canEditWorkstream).
//   • Performance is honest: per-channel reach/click/conv + a source_kind
//     provenance label, but UTM attribution per piece is UNRELIABLE — we surface
//     unattributable_count and don't fabricate per-piece precision.
//   • Brand-voice auditor runs SUGGEST-EDITS (advisory, non-blocking, INV-2) and
//     surfaces its mode (LLM vs HEURISTIC).

import { useCallback, useEffect, useState } from 'react';
import { moduleById, canEditWorkstream, type Role } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet, apiPost } from '@/lib/api';
import {
  CHANNEL_COLOR,
  channelColor,
  channelLabel,
  sourceKindStyle,
  statusStyle,
  fmtShortDate,
  fmtStamp,
  SEED_OVERVIEW,
  SEED_CALENDAR,
  SEED_TESTIMONIALS,
  SEED_PERFORMANCE,
  SEED_LIBRARY,
  type ChannelKey,
  type ContentOverview,
  type ContentCalendar,
  type CalendarEntry,
  type TestimonialStub,
  type ContentPerformance,
  type LibraryAsset,
  type BrandVoiceResult,
} from '@/lib/content-api';

const MONO = 'JetBrains Mono';
const ARCHIVO = 'Fraunces';

// ---- Kanban types (the live Google-Sheet seam — LEFT UNTOUCHED) ------------
type ContentType = 'video' | 'podcast' | 'article' | 'social' | 'email';
type Column = 'Concept' | 'In production' | 'Review' | 'Scheduled' | 'Published';

interface Card {
  name: string;
  owner: string;
  type: ContentType;
  due: string;
  stub?: string;
  raw?: SheetRow;
}
interface KanbanCol {
  col: Column;
  cards: Card[];
}
interface SheetRow {
  title: string;
  type: string;
  stage: string;
  owner: string;
  channel: string;
  utm: string;
  target_date: string;
}
interface KanbanColApi {
  stage: string;
  cards: SheetRow[];
}
interface SyncBlock {
  mode: 'live' | 'simulate';
  synced: boolean;
  tab: string | null;
  sheet_id: string | null;
}
interface KanbanApi {
  stages: string[];
  rows: SheetRow[];
  columns: KanbanColApi[];
  sync: SyncBlock;
}

const TYPE_GLYPH: Record<ContentType, string> = {
  video: '▶',
  podcast: '🎙',
  article: '¶',
  social: '✦',
  email: '✉',
};

// Per-role demo-seat token cache for the kanban POST (mirrors lib/api.ts).
const _kanbanTokenCache: Partial<Record<Role, string>> = {};
async function mintKanbanToken(role: Role): Promise<string | null> {
  if (_kanbanTokenCache[role]) return _kanbanTokenCache[role]!;
  try {
    const r = await fetch('/api/auth/demo-token', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ role }),
    });
    if (!r.ok) return null;
    const j = (await r.json()) as { access_token?: string };
    if (j.access_token) {
      _kanbanTokenCache[role] = j.access_token;
      return j.access_token;
    }
    return null;
  } catch {
    return null;
  }
}
async function postKanbanRow(row: SheetRow, role: Role): Promise<boolean> {
  const token = await mintKanbanToken(role);
  try {
    const r = await fetch('/api/content/kanban', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(row),
    });
    return r.ok;
  } catch {
    return false;
  }
}
function rowToCard(row: SheetRow): Card {
  return {
    name: row.title,
    owner: row.owner,
    type: (TYPE_GLYPH as Record<string, string>)[row.type] ? (row.type as ContentType) : 'article',
    due: row.target_date,
    raw: row,
  };
}

// ---- Kanban seed fallback (rendered only when the backbone is unreachable) --
const KANBAN: KanbanCol[] = [
  {
    col: 'Concept',
    cards: [
      { name: 'Advisor Series', owner: 'the Content Owner', type: 'video', due: 'Jul 18' },
      { name: 'Objection: "is it a real school?"', owner: 'the Content Owner', type: 'article', due: 'Jul 09', stub: 'Admissions brief' },
      { name: 'ESA explainer thread', owner: 'Pamela Hobart', type: 'social', due: 'Jul 12' },
    ],
  },
  {
    col: 'In production',
    cards: [
      { name: 'Thailand videographer shoot', owner: 'the Content Owner', type: 'video', due: 'Jul 15' },
      { name: 'Family Interviews ×5', owner: 'the Content Owner', type: 'video', due: 'Jul 20', stub: 'Grassroots testimonial' },
      { name: 'AGL podcast with Pam', owner: 'Pamela Hobart', type: 'podcast', due: 'Jul 11' },
    ],
  },
  {
    col: 'Review',
    cards: [
      { name: 'Sizzle Reel for Joe', owner: 'the Content Owner', type: 'video', due: 'Jul 08' },
      { name: 'Mastery-based learning op-ed', owner: 'Pamela Hobart', type: 'article', due: 'Jul 10' },
      { name: 'Week-in-review newsletter', owner: 'the Content Owner', type: 'email', due: 'Jul 07' },
      { name: 'Founder Q&A clip', owner: 'the Content Owner', type: 'social', due: 'Jul 09' },
    ],
  },
  {
    col: 'Scheduled',
    cards: [
      { name: 'K–2 sweet-spot carousel', owner: 'the Content Owner', type: 'social', due: 'Jul 06' },
      { name: 'Substack: "Why we test on X"', owner: 'Pamela Hobart', type: 'article', due: 'Jul 06' },
    ],
  },
  {
    col: 'Published',
    cards: [
      { name: 'Alpha-X day-in-the-life', owner: 'the Content Owner', type: 'video', due: 'Jul 02' },
      { name: 'Tuition / ESA FAQ post', owner: 'the Content Owner', type: 'article', due: 'Jun 30' },
    ],
  },
];

// ---- Component -------------------------------------------------------------
export function ContentModule() {
  const def = moduleById('content')!;
  const { session } = useSession();
  const canEdit = canEditWorkstream(session, 'content'); // admin always; operator only if owns 'content' (demo: admin only)

  // Live kanban from the Google-Sheet seam (UNTOUCHED).
  const [live, setLive] = useState<KanbanApi | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const data = await apiGet<KanbanApi>('/content/kanban', session.role);
    if (data && Array.isArray(data.columns)) setLive(data);
  }, [session.role]);

  useEffect(() => {
    let active = true;
    apiGet<KanbanApi>('/content/kanban', session.role).then((data) => {
      if (active && data && Array.isArray(data.columns)) setLive(data);
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  const connected = live !== null;
  const sync = live?.sync ?? null;
  const isLiveSheet = sync?.mode === 'live';

  const stages: string[] = live ? live.stages : KANBAN.map((k) => k.col);
  const columns: { col: string; cards: Card[] }[] = live
    ? live.columns.map((c) => ({ col: c.stage, cards: c.cards.map(rowToCard) }))
    : KANBAN.map((k) => ({ col: k.col, cards: k.cards }));

  const moveCard = useCallback(
    async (card: Card) => {
      if (!card.raw || busy) return;
      const idx = stages.indexOf(card.raw.stage);
      if (idx < 0 || idx >= stages.length - 1) return;
      const next = stages[idx + 1];
      setBusy(true);
      const ok = await postKanbanRow({ ...card.raw, stage: next }, session.role);
      if (ok) await refresh();
      setBusy(false);
    },
    [stages, busy, session.role, refresh],
  );

  const addCard = useCallback(
    async (stage: string) => {
      if (busy || typeof window === 'undefined') return;
      const title = window.prompt(`New ${stage} card — title?`)?.trim();
      if (!title) return;
      setBusy(true);
      const ok = await postKanbanRow(
        { title, type: 'article', stage, owner: 'the Content Owner', channel: 'Substack', utm: '', target_date: '' },
        session.role,
      );
      if (ok) await refresh();
      setBusy(false);
    },
    [busy, session.role, refresh],
  );

  // ---- Live wiring for the (formerly seed-only) sections -------------------
  const [overview, setOverview] = useState<ContentOverview | null>(null);
  const [calendar, setCalendar] = useState<ContentCalendar | null>(null);
  const [testimonials, setTestimonials] = useState<TestimonialStub[] | null>(null);
  const [perf, setPerf] = useState<ContentPerformance | null>(null);

  const refetchCalendar = useCallback(async () => {
    const c = await apiGet<ContentCalendar>('/content/calendar', session.role);
    if (c && Array.isArray(c.entries)) setCalendar(c);
  }, [session.role]);

  useEffect(() => {
    let active = true;
    apiGet<ContentOverview>('/content/overview', session.role).then((d) => {
      if (active && d) setOverview(d);
    });
    apiGet<ContentCalendar>('/content/calendar', session.role).then((d) => {
      if (active && d && Array.isArray(d.entries)) setCalendar(d);
    });
    apiGet<TestimonialStub[]>('/content/testimonial-stubs', session.role).then((d) => {
      if (active && Array.isArray(d)) setTestimonials(d);
    });
    apiGet<ContentPerformance>('/content/performance', session.role).then((d) => {
      if (active && d && Array.isArray(d.channels)) setPerf(d);
    });
    return () => {
      active = false;
    };
  }, [session.role]);

  const ov = overview ?? SEED_OVERVIEW;
  const ovLive = overview !== null;
  const cal = calendar ?? SEED_CALENDAR;
  const calLive = calendar !== null;
  const stubs = testimonials ?? SEED_TESTIMONIALS;
  const stubsLive = testimonials !== null;
  const pf = perf ?? SEED_PERFORMANCE;
  const pfLive = perf !== null;

  // Drag-to-reschedule (owner-gated). On drop, persist + refetch.
  const rescheduleEntry = useCallback(
    async (entryId: string, newDate: string) => {
      if (!canEdit || busy) return;
      const entry = cal.entries.find((e) => e.entry_id === entryId);
      if (!entry || entry.scheduled_date === newDate) return;
      setBusy(true);
      const ok = await apiPost('/content/calendar/reschedule', session.role, { entry_id: entryId, new_date: newDate });
      if (ok) await refetchCalendar();
      setBusy(false);
    },
    [canEdit, busy, cal.entries, session.role, refetchCalendar],
  );

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* Sync banner — the label is DERIVED from the real seam, never hardcoded */}
        <SyncBanner connected={connected} isLiveSheet={isLiveSheet} tab={sync?.tab ?? null} />

        {/* 3a — hero stats (live from /content/overview) */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
          <HeroStat
            label="PRODUCTIONS IN FLIGHT"
            value={String(ov.productions_in_flight)}
            valueSub={`${ov.on_track} on track`}
            note={`${ov.on_track_pct}% on-track across the pipeline`}
            live={ovLive}
          />
          <HeroStat
            label="PUBLISHING THIS WEEK"
            value={String(ov.this_week_publish_count)}
            note="scheduled to ship in the next 7 days"
            live={ovLive}
          />
          <HeroStat
            label="X / TWITTER CONVERSION"
            value={`${ov.x_conversion_rate_pct}%`}
            note="the pre-sold engine · top channel"
            hero
            live={ovLive}
          />
          <HeroStat
            label="TOP PERFORMER"
            value={String(ov.top_piece_conversions)}
            valueSub="conv"
            note={ov.top_piece_title}
            live={ovLive}
          />
        </div>

        {/* Recently captured testimonials — Grassroots cross-link (from /content/testimonial-stubs) */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Recently captured testimonials</div>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <LiveDot live={stubsLive} />
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{stubs.length} draft stub{stubs.length === 1 ? '' : 's'} · from Grassroots</span>
            </span>
          </div>
          {stubs.length === 0 ? (
            <div style={{ padding: '12px 16px', fontSize: 10.5, color: 'var(--ink-3)' }}>No testimonial stubs captured yet.</div>
          ) : (
            <div style={{ display: 'flex', gap: 12, padding: '12px 16px', overflowX: 'auto' }}>
              {stubs.map((t) => (
                <div key={t.asset_id} style={{ minWidth: 230, maxWidth: 280, border: '1px solid var(--line)', background: 'var(--card-2)', padding: '10px 11px' }}>
                  <div style={{ fontSize: 11, color: 'var(--ink)', fontWeight: 600, lineHeight: 1.3 }}>{t.title}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 5, lineHeight: 1.4 }}>“{t.body}”</div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
                    <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '2px 6px', background: 'var(--gold-soft)', color: 'var(--gold)' }}>
                      ⟲ {t.source_ref.replace(/_/g, ' ')}
                    </span>
                    <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{fmtStamp(t.created_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Production pipeline — kanban (UNTOUCHED LIVE SEAM) */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Production pipeline</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>
              {isLiveSheet ? 'kanban · live ⇄ Google Sheet' : connected ? 'kanban · simulated seam' : 'kanban · sample'}
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${stages.length}, 1fr)`, gap: 1, background: 'var(--line)' }}>
            {columns.map((kc) => (
              <div key={kc.col} style={{ background: 'var(--card-2)', padding: '10px 9px', minHeight: 240 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 9 }}>
                  <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.3px', color: 'var(--ink-2)' }}>{kc.col}</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{kc.cards.length}</span>
                    {connected && (
                      <button
                        type="button"
                        onClick={() => addCard(kc.col)}
                        disabled={busy}
                        title={`Add a card to ${kc.col} — writes back to the sheet`}
                        style={{ fontFamily: MONO, fontSize: 10, lineHeight: 1, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink-2)', padding: '1px 5px' }}
                      >
                        +
                      </button>
                    )}
                  </span>
                </div>
                {kc.cards.map((c) => (
                  <KanbanCard key={c.name} card={c} onMove={connected && c.raw ? () => moveCard(c) : undefined} busy={busy} />
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* 3c Calendar + 3e brand voice auditor */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 14, marginBottom: 14 }}>
          <CalendarPanel cal={cal} live={calLive} canEdit={canEdit} busy={busy} onReschedule={rescheduleEntry} />
          <BrandVoicePanel role={session.role} />
        </div>

        {/* 3d Performance */}
        <PerformancePanel pf={pf} live={pfLive} />

        {/* 3e Content library */}
        <LibraryPanel role={session.role} libraryCount={ov.library_count} />

        {/* Cross-link note */}
        <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card)', padding: '11px 14px' }}>
          <div style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)', marginBottom: 6 }}>CROSS-MODULE WIRING</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>
            ⌖ Testimonials surfaced in <b style={{ color: 'var(--ink)' }}>Grassroots</b> auto-stub a draft here (see the strip above).{' '}
            Top objections logged in <b style={{ color: 'var(--ink)' }}>Admissions</b> auto-create a content brief (see <i>&quot;is it a real school?&quot;</i>).
          </div>
        </div>
      </section>
    </>
  );
}

// ---- 3a hero stat ----------------------------------------------------------
function HeroStat({ label, value, valueSub, note, hero, live }: { label: string; value: string; valueSub?: string; note: string; hero?: boolean; live: boolean }) {
  return (
    <div style={{ border: `1px solid ${hero ? 'var(--ink)' : 'var(--line-2)'}`, background: 'var(--card)', padding: 13, position: 'relative' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: hero ? 600 : 400 }}>{label}</div>
        <LiveDot live={live} />
      </div>
      <div style={{ fontFamily: hero ? ARCHIVO : MONO, fontWeight: hero ? 700 : 600, fontSize: hero ? 27 : 22, color: 'var(--ink)', marginTop: 5, lineHeight: 1.05 }}>
        {value}
        {valueSub && <span style={{ fontFamily: MONO, fontWeight: 600, fontSize: 12, color: 'var(--ink-3)' }}> {valueSub}</span>}
      </div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 2, lineHeight: 1.3 }}>{note}</div>
    </div>
  );
}

// Tiny ● LIVE / ○ SAMPLE dot — derived honestly from whether the live fetch landed.
function LiveDot({ live }: { live: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: MONO, fontSize: 7.5, fontWeight: 600, color: live ? 'var(--ok)' : 'var(--ink-3)' }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: live ? 'var(--ok)' : 'var(--ink-3)', display: 'inline-block' }} />
      {live ? 'LIVE' : 'SAMPLE'}
    </span>
  );
}

// ---- 3c Calendar panel (live + drag-to-reschedule) -------------------------
function CalendarPanel({ cal, live, canEdit, busy, onReschedule }: { cal: ContentCalendar; live: boolean; canEdit: boolean; busy: boolean; onReschedule: (id: string, date: string) => void }) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overDay, setOverDay] = useState<string | null>(null);

  // Pick the month with the most entries → render a real Mon-start month grid.
  const counts: Record<string, number> = {};
  for (const e of cal.entries) {
    const ym = e.scheduled_date.slice(0, 7);
    counts[ym] = (counts[ym] || 0) + 1;
  }
  const ym = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || '2026-06';
  const [y, m] = ym.split('-').map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const firstDow = new Date(y, m - 1, 1).getDay(); // 0=Sun
  const lead = (firstDow + 6) % 7; // Mon-start offset
  const monthLabel = new Date(y, m - 1, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

  const byDate: Record<string, CalendarEntry[]> = {};
  for (const e of cal.entries) (byDate[e.scheduled_date] ||= []).push(e);
  const conflictSet = new Set(cal.conflict_dates);

  const cells: (string | null)[] = [];
  for (let i = 0; i < lead; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(`${ym}-${String(d).padStart(2, '0')}`);

  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
        <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Content calendar</div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <LiveDot live={live} />
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{monthLabel} · color = channel</span>
        </span>
      </div>
      <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 11, lineHeight: 1.4 }}>
        A ⚑ marks a same-day pile-up ({cal.conflict_threshold}+ pieces) — flag to re-space the schedule.{' '}
        {canEdit ? (
          <b style={{ color: 'var(--ink-2)' }}>Drag an entry to another day to reschedule.</b>
        ) : (
          <span style={{ color: 'var(--ink-3)' }}>Read-only — only the content owner (admin) can drag to reschedule.</span>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
        {(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const).map((d) => (
          <div key={d} style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', textAlign: 'center', fontWeight: 600 }}>{d}</div>
        ))}
        {cells.map((iso, i) =>
          iso === null ? (
            <div key={`pad-${i}`} />
          ) : (
            <CalCell
              key={iso}
              iso={iso}
              day={Number(iso.slice(8))}
              entries={byDate[iso] || []}
              conflict={conflictSet.has(iso)}
              canEdit={canEdit}
              busy={busy}
              dragId={dragId}
              isOver={overDay === iso}
              onDragStartEntry={setDragId}
              onDragEnd={() => { setDragId(null); setOverDay(null); }}
              onDragOverDay={() => canEdit && setOverDay(iso)}
              onDropDay={() => {
                if (dragId) onReschedule(dragId, iso);
                setDragId(null);
                setOverDay(null);
              }}
            />
          ),
        )}
      </div>
      {/* legend */}
      <div style={{ display: 'flex', gap: 8, marginTop: 11, flexWrap: 'wrap' }}>
        {(Object.keys(CHANNEL_COLOR) as ChannelKey[]).map((ch) => (
          <span key={ch} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 8, color: 'var(--ink-2)' }}>
            <span style={{ width: 8, height: 8, background: CHANNEL_COLOR[ch].bg, border: `1px solid ${CHANNEL_COLOR[ch].fg}`, display: 'inline-block' }} />
            {ch}
          </span>
        ))}
      </div>
    </div>
  );
}

function CalCell({
  iso, day, entries, conflict, canEdit, busy, dragId, isOver, onDragStartEntry, onDragEnd, onDragOverDay, onDropDay,
}: {
  iso: string; day: number; entries: CalendarEntry[]; conflict: boolean; canEdit: boolean; busy: boolean;
  dragId: string | null; isOver: boolean;
  onDragStartEntry: (id: string) => void; onDragEnd: () => void; onDragOverDay: () => void; onDropDay: () => void;
}) {
  return (
    <div
      onDragOver={(e) => { if (canEdit && dragId) { e.preventDefault(); onDragOverDay(); } }}
      onDrop={(e) => { if (canEdit && dragId) { e.preventDefault(); onDropDay(); } }}
      style={{
        border: `1px solid ${isOver ? 'var(--brand)' : conflict ? 'var(--signal)' : 'var(--line)'}`,
        background: isOver ? 'var(--brand-soft, var(--accent-soft))' : 'var(--card-2)',
        minHeight: 62,
        padding: '4px 4px',
        position: 'relative',
        opacity: busy ? 0.7 : 1,
      }}
      title={iso}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{day}</span>
        {conflict && <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--signal)', fontWeight: 600 }}>⚑</span>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginTop: 3 }}>
        {entries.map((e) => {
          const col = channelColor(e.channel);
          return (
            <div
              key={e.entry_id}
              draggable={canEdit}
              onDragStart={(ev) => {
                if (!canEdit) return;
                ev.dataTransfer.setData('text/plain', e.entry_id);
                ev.dataTransfer.effectAllowed = 'move';
                onDragStartEntry(e.entry_id);
              }}
              onDragEnd={onDragEnd}
              title={`${e.title} · ${channelLabel(e.channel)} · ${e.status}${canEdit ? ' (drag to reschedule)' : ''}`}
              style={{
                fontFamily: MONO,
                fontSize: 7.5,
                lineHeight: 1.2,
                padding: '2px 4px',
                background: col.bg,
                color: col.fg,
                border: `1px solid ${col.fg}`,
                cursor: canEdit ? 'grab' : 'default',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                opacity: dragId === e.entry_id ? 0.4 : 1,
              }}
            >
              {e.title}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---- 3e Brand-voice auditor (advisory; surfaces mode + score) --------------
const VOICE_SAMPLE = "the best school money can buy, act now, guaranteed amazing results";

function BrandVoicePanel({ role }: { role: Role }) {
  const [text, setText] = useState(VOICE_SAMPLE);
  const [result, setResult] = useState<BrandVoiceResult | null>(null);
  const [running, setRunning] = useState(false);

  const audit = useCallback(async () => {
    const t = text.trim();
    if (!t) return;
    setRunning(true);
    const r = await apiPost<BrandVoiceResult>('/content/brand-voice/suggest', role, { text: t });
    if (r) setResult(r);
    setRunning(false);
  }, [text, role]);

  // Auto-run once on mount so the panel demonstrates a live result.
  useEffect(() => {
    let active = true;
    (async () => {
      const r = await apiPost<BrandVoiceResult>('/content/brand-voice/suggest', role, { text: VOICE_SAMPLE });
      if (active && r) setResult(r);
    })();
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role]);

  const scorePct = result ? Math.round(result.brand_score * 100) : null;

  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
        <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Brand voice auditor</div>
        <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: 'var(--warn-soft)', color: 'var(--warn)' }}>SUGGEST-EDITS</span>
      </div>
      <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 9, lineHeight: 1.4 }}>
        Non-blocking — surfaces inline rewrites on a draft; the writer keeps or dismisses. (The hard grounding gate lives on outbound sends.)
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder="Paste draft copy to audit against GT voice…"
        style={{ width: '100%', resize: 'vertical', fontFamily: 'inherit', fontSize: 11, lineHeight: 1.4, color: 'var(--ink)', border: '1px solid var(--line-2)', background: 'var(--paper)', padding: '7px 9px', boxSizing: 'border-box' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
        <button
          type="button"
          onClick={audit}
          disabled={running || !text.trim()}
          style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 600, cursor: running ? 'default' : 'pointer', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', padding: '5px 12px' }}
        >
          {running ? 'Auditing…' : 'Audit voice'}
        </button>
        {result && (
          <>
            <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}>
              brand score <b style={{ color: scorePct !== null && scorePct >= 70 ? 'var(--ok)' : 'var(--warn)' }}>{scorePct}%</b>
            </span>
            <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: result.mode === 'llm' ? 'var(--ok-soft)' : 'var(--accent-soft)', color: result.mode === 'llm' ? 'var(--ok)' : 'var(--ink-2)' }}>
              MODE · {result.mode.toUpperCase()}
            </span>
          </>
        )}
      </div>
      {result && (
        <div style={{ marginTop: 6 }}>
          {result.suggestions.length === 0 ? (
            <div style={{ fontSize: 10.5, color: 'var(--ok)', padding: '9px 0' }}>✓ On voice — no suggested rewrites.</div>
          ) : (
            result.suggestions.map((v, i) => (
              <div key={`${v.before}-${i}`} style={{ borderTop: '1px solid var(--line)', padding: '8px 0' }}>
                <div style={{ fontSize: 10.5, color: 'var(--ink-3)', textDecoration: 'line-through', lineHeight: 1.4 }}>{v.before}</div>
                <div style={{ fontSize: 11, color: 'var(--ink)', marginTop: 3, lineHeight: 1.4 }}>
                  <span style={{ color: 'var(--ok)', fontFamily: MONO, fontSize: 9, marginRight: 5 }}>→</span>
                  {v.after}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--gold)' }}>{v.rule}</span>
                  {v.kind && <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '1px 5px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>{v.kind}</span>}
                </div>
              </div>
            ))
          )}
          <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginTop: 8, lineHeight: 1.4 }}>{result.note}</div>
        </div>
      )}
    </div>
  );
}

// ---- 3d Performance --------------------------------------------------------
function PerformancePanel({ pf, live }: { pf: ContentPerformance; live: boolean }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
        <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Performance · by channel</div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <LiveDot live={live} />
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>Meta + HubSpot · channel-level</span>
        </span>
      </div>
      {/* honesty banner — UTM per-piece attribution is unreliable */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px', borderBottom: '1px solid var(--line-2)', background: 'var(--signal-soft)', flexWrap: 'wrap' }}>
        <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: 'var(--signal)' }}>⚠ UTM ATTRIBUTION UNRELIABLE</span>
        <span style={{ fontSize: 10.5, color: 'var(--broken)' }}>
          Per-piece attribution is broken — channel rollups below are directional only. Module 7 (CRM / Ops) owns the UTM rebuild.{' '}
          <b style={{ color: 'var(--ink)' }}>{pf.unattributable_count} pieces not UTM-attributable.</b> We don&apos;t fabricate per-piece conversion.
        </span>
      </div>
      {/* header */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr .8fr .8fr .8fr 1.1fr', fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
        <div>CHANNEL</div>
        <div style={{ textAlign: 'right' }}>REACH</div>
        <div style={{ textAlign: 'right' }}>CLICKS</div>
        <div style={{ textAlign: 'right' }}>CONV</div>
        <div style={{ textAlign: 'right' }}>SOURCE / NOTE</div>
      </div>
      {pf.channels.map((r) => {
        const sk = sourceKindStyle(r.source_kind);
        return (
          <div key={r.channel} style={{ display: 'grid', gridTemplateColumns: '1.3fr .8fr .8fr .8fr 1.1fr', alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <span style={{ width: 8, height: 8, background: channelColor(r.channel).bg, border: `1px solid ${channelColor(r.channel).fg}`, display: 'inline-block' }} />
              <span style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{channelLabel(r.channel)}</span>
            </div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{r.reach.toLocaleString()}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{r.clicks.toLocaleString()}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{r.conversion_rate_pct}%</div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6 }}>
              {r.is_top && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: 'var(--ok-soft)', color: 'var(--ok)' }}>▲ TOP</span>}
              {r.is_bottom && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>▼ BOTTOM</span>}
              <span title={`source_kind: ${r.source_kind}`} style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: sk.bg, color: sk.color }}>{sk.label}</span>
            </div>
          </div>
        );
      })}

      {/* per-piece top / bottom rankings (utm-attributed flag surfaced) */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
        <PieceList title="▲ Top pieces" pieces={pf.top_pieces} />
        <PieceList title="▼ Bottom pieces" pieces={pf.bottom_pieces} borderLeft />
      </div>
    </div>
  );
}

function PieceList({ title, pieces, borderLeft }: { title: string; pieces: ContentPerformance['top_pieces']; borderLeft?: boolean }) {
  return (
    <div style={{ borderTop: '1px solid var(--line-2)', borderLeft: borderLeft ? '1px solid var(--line)' : undefined, padding: '10px 16px' }}>
      <div style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.3px', color: 'var(--ink-3)', marginBottom: 7 }}>{title}</div>
      {pieces.map((p, i) => (
        <div key={`${p.piece_title}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderTop: i === 0 ? undefined : '1px solid var(--line)' }}>
          <span style={{ flex: 1, fontSize: 10.5, color: 'var(--ink)', lineHeight: 1.3 }}>{p.piece_title}</span>
          <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '1px 6px', background: channelColor(p.channel).bg, color: channelColor(p.channel).fg }}>{channelLabel(p.channel)}</span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 600, color: 'var(--ink)', minWidth: 34, textAlign: 'right' }}>{p.conversion_rate_pct}%</span>
          <span title={p.utm_attributed ? 'UTM-attributed' : 'not UTM-attributable'} style={{ fontFamily: MONO, fontSize: 9, color: p.utm_attributed ? 'var(--ok)' : 'var(--ink-3)' }}>
            {p.utm_attributed ? '⛓' : '⛓̸'}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---- 3e Content library (live search + tag chips) --------------------------
function LibraryPanel({ role, libraryCount }: { role: Role; libraryCount: number }) {
  const [q, setQ] = useState('');
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [assets, setAssets] = useState<LibraryAsset[] | null>(null);
  const [loading, setLoading] = useState(false);

  // The facet chips the demo surfaces (a curated slice of the live tag space).
  const TAG_CHIPS = ['x/twitter', 'instagram', 'facebook', 'youtube', 'gifted_identity', 'academic_outcomes', 'parent_story', 'cost_tefa_esa', 'ai_platform', 'owned'];

  const fetchLibrary = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (q.trim()) params.set('q', q.trim());
    for (const t of activeTags) params.append('tag', t);
    const qs = params.toString();
    const data = await apiGet<LibraryAsset[]>(`/content/library${qs ? `?${qs}` : ''}`, role);
    if (Array.isArray(data)) setAssets(data);
    else setAssets(null);
    setLoading(false);
  }, [q, activeTags, role]);

  // Refetch on filter change (debounced for the search box).
  useEffect(() => {
    const id = setTimeout(fetchLibrary, 250);
    return () => clearTimeout(id);
  }, [fetchLibrary]);

  const live = assets !== null;
  const list = (assets ?? SEED_LIBRARY);
  const shown = list.slice(0, 12); // cap the render; full count is surfaced in the header
  const toggleTag = (t: string) => setActiveTags((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));

  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
        <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Content library</div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <LiveDot live={live} />
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>
            {live ? `${list.length} match${list.length === 1 ? '' : 'es'}` : `${libraryCount} pieces`} · kept + validated archive
          </span>
        </span>
      </div>
      {/* search + tag chips */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderBottom: '1px solid var(--line-2)', flexWrap: 'wrap' }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="⌕ search copy, persona, channel, topic…"
          style={{ flex: 1, minWidth: 200, fontFamily: MONO, fontSize: 10, color: 'var(--ink)', border: '1px solid var(--line-2)', background: 'var(--paper)', padding: '6px 10px' }}
        />
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          {TAG_CHIPS.map((t) => {
            const on = activeTags.includes(t);
            return (
              <button
                key={t}
                type="button"
                onClick={() => toggleTag(t)}
                style={{ fontFamily: MONO, fontSize: 8, padding: '3px 7px', cursor: 'pointer', border: `1px solid ${on ? 'var(--ink)' : 'var(--line-2)'}`, background: on ? 'var(--ink)' : 'var(--accent-soft)', color: on ? 'var(--paper)' : 'var(--ink-2)' }}
              >
                {t}
              </button>
            );
          })}
        </div>
      </div>
      {loading && <div style={{ padding: '8px 16px', fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', borderBottom: '1px solid var(--line)' }}>searching…</div>}
      {shown.length === 0 ? (
        <div style={{ padding: '14px 16px', fontSize: 10.5, color: 'var(--ink-3)' }}>No assets match these filters.</div>
      ) : (
        shown.map((a) => {
          const fromGrassroots = a.source_ref?.toLowerCase().includes('grassroots') || a.tags?.some((t) => t.toLowerCase().includes('grassroots'));
          return (
            <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.title}</span>
              <span style={{ fontFamily: MONO, fontSize: 8, padding: '2px 7px', background: 'var(--accent-soft)', color: 'var(--ink-2)' }}>{a.asset_type}</span>
              {(a.tags || []).slice(0, 2).map((t) => (
                <span key={t} style={{ fontFamily: MONO, fontSize: 8, padding: '2px 7px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>{t}</span>
              ))}
              <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: channelColor(a.channel).bg, color: channelColor(a.channel).fg }}>{channelLabel(a.channel)}</span>
              <span
                title={`provenance: ${a.provenance?.generated_by ?? 'unknown'} · source ${a.source_ref}`}
                style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '2px 6px', background: fromGrassroots ? 'var(--gold-soft)' : 'var(--ok-soft)', color: fromGrassroots ? 'var(--gold)' : 'var(--ok)' }}
              >
                {fromGrassroots ? '⟲ grassroots' : (a.provenance?.generated_by ?? 'kept')}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}

// ---- Sync banner (UNTOUCHED) -----------------------------------------------
function SyncBanner({ connected, isLiveSheet, tab }: { connected: boolean; isLiveSheet: boolean; tab: string | null }) {
  const pill = isLiveSheet
    ? { label: 'GOOGLE SHEET · SYNCED', bg: 'var(--ok-soft)', fg: 'var(--ok)', dot: 'var(--ok)' }
    : connected
      ? { label: 'GOOGLE SHEET · SIMULATED', bg: 'var(--warn-soft)', fg: 'var(--warn)', dot: 'var(--warn)' }
      : { label: 'GOOGLE SHEET · SAMPLE', bg: 'var(--accent-soft)', fg: 'var(--ink-3)', dot: 'var(--ink-3)' };
  const copy = isLiveSheet
    ? `Production tracker is read + write to the Content Owner's Google Sheet${tab ? ` · ${tab}` : ''} — edits here flow both ways.`
    : connected
      ? 'Simulated seam (no live sheet bound) — moves & adds flow both ways in-session. Set SHEETS_MODE=live + GSHEETS_SHEET_ID to sync the real sheet.'
      : 'Showing seed data — the backbone is unreachable; the kanban is read-only sample.';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, border: '1px solid var(--line-2)', background: 'var(--card)', padding: '10px 14px', marginBottom: 14, flexWrap: 'wrap' }}>
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: pill.bg, color: pill.fg, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: pill.dot, display: 'inline-block' }} />
        {pill.label}
      </span>
      <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{copy}</span>
      <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginLeft: 'auto' }}>Excludes summer-camp content → Module 4</span>
    </div>
  );
}

function KanbanCard({ card, onMove, busy }: { card: Card; onMove?: () => void; busy?: boolean }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '8px 9px', marginBottom: 7 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
        <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)', lineHeight: 1.3 }}>{TYPE_GLYPH[card.type]}</span>
        <span style={{ flex: 1, fontSize: 10.5, color: 'var(--ink)', fontWeight: 500, lineHeight: 1.3 }}>{card.name}</span>
        {onMove && (
          <button
            type="button"
            onClick={onMove}
            disabled={busy}
            title="Advance to the next stage — writes back to the sheet"
            style={{ fontFamily: MONO, fontSize: 10, lineHeight: 1, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--line-2)', background: 'var(--card-2)', color: 'var(--ink-2)', padding: '1px 5px' }}
          >
            →
          </button>
        )}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginTop: 5 }}>{card.owner}</div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
        <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-2)' }}>{card.type} · due {card.due}</span>
      </div>
      {card.stub && (
        <div style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '2px 5px', marginTop: 5, background: 'var(--gold-soft)', color: 'var(--gold)', display: 'inline-block' }}>
          ⟲ {card.stub}
        </div>
      )}
    </div>
  );
}
