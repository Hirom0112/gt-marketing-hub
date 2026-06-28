'use client';

// Content & Thought Leadership (Module 3) — the Content Owner's workshop.
//   • Production tracker is SYNCED read+write to a Google Sheet (the owner's
//     existing tool of record); summer-camp content lives in Module 4, not here.
//   • Pipeline kanban: Concept → In production → Review → Scheduled → Published.
//   • Calendar is channel-color-coded with a same-day conflict marker.
//   • Performance is honest: per-channel reach/click/conv, but UTM attribution
//     per piece is UNRELIABLE — Module 7 owns the rebuild, so we don't fake it.
//   • Brand-voice auditor runs SUGGEST-EDITS (non-blocking); library is tagged.
// All data inlined as typed consts; ported faithfully from the design prototype.

import { useCallback, useEffect, useState } from 'react';
import { moduleById, type Role } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';
import { useSession } from '@/lib/session';
import { apiGet } from '@/lib/api';

const MONO = 'JetBrains Mono';
const ARCHIVO = 'Fraunces';

// ---- Types -----------------------------------------------------------------
type ContentType = 'video' | 'podcast' | 'article' | 'social' | 'email';
type Channel = 'Substack' | 'X' | 'Instagram' | 'Facebook' | 'Podcast' | 'Email' | 'YouTube';
type Column = 'Concept' | 'In production' | 'Review' | 'Scheduled' | 'Published';

interface TopStat {
  label: string;
  value: string;
  valueSub?: string;
  valueColor: string;
  note: string;
}
interface Card {
  name: string;
  owner: string;
  type: ContentType;
  due: string;
  stub?: string; // cross-link origin badge, when auto-created
  raw?: SheetRow; // the live sheet row backing this card (live mode → enables write-back)
}
interface KanbanCol {
  col: Column;
  cards: Card[];
}
interface CalDay {
  day: number;
  channels: Channel[];
  conflict?: boolean;
}
interface PerfRow {
  channel: string;
  reach: string;
  clicks: string;
  conv: string;
  convColor: string;
  tag?: 'top' | 'bottom';
}
interface VoiceSuggestion {
  before: string;
  after: string;
  rule: string;
}
interface LibItem {
  title: string;
  type: ContentType;
  persona: string;
  tier: string;
  channel: Channel;
}

// ---- Live kanban (GET/POST /content/kanban — the real Google-Sheet seam) ----
// The backend ContentRow + the GET payload (rows grouped by the five canonical
// stages) and the honest sync block. We render whatever the backend returns; when
// it is unreachable we fall back to the inline KANBAN seed below.
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

// Per-role demo-seat token cache, mirroring lib/api.ts's read path so the kanban
// POST (the write half) authenticates exactly like every GET does.
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

// Upsert one row back to the sheet (a move or an add). Fails soft → false.
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

// Map an API row → the existing Card display shape (name=title, due=target_date).
function rowToCard(row: SheetRow): Card {
  return {
    name: row.title,
    owner: row.owner,
    type: (TYPE_GLYPH as Record<string, string>)[row.type] ? (row.type as ContentType) : 'article',
    due: row.target_date,
    raw: row,
  };
}

// ---- Channel color map -----------------------------------------------------
const CHANNEL_COLOR: Record<Channel, { bg: string; fg: string }> = {
  Substack: { bg: 'var(--signal-soft)', fg: 'var(--signal)' },
  X: { bg: 'var(--ink)', fg: 'var(--paper)' },
  Instagram: { bg: 'var(--gold-soft)', fg: 'var(--gold)' },
  Facebook: { bg: 'var(--accent-soft)', fg: 'var(--ink-2)' },
  Podcast: { bg: 'var(--ok-soft)', fg: 'var(--ok)' },
  Email: { bg: 'var(--warn-soft)', fg: 'var(--warn)' },
  YouTube: { bg: 'var(--signal-soft)', fg: 'var(--signal)' },
};
const TYPE_GLYPH: Record<ContentType, string> = {
  video: '▶',
  podcast: '🎙',
  article: '¶',
  social: '✦',
  email: '✉',
};

// ---- Seed data -------------------------------------------------------------
const TOP_STATS: TopStat[] = [
  { label: 'PRODUCTIONS IN FLIGHT', value: '14', valueSub: '4 in review', valueColor: 'var(--ink)', note: 'across 5 pipeline stages' },
  { label: 'PUBLISHED MTD', value: '9', valueSub: '/ 12 goal', valueColor: 'var(--gold)', note: '75% of monthly target · 4 days left' },
  { label: 'X / TWITTER CONVERSION', value: '42%', valueColor: 'var(--ink)', note: 'the pre-sold engine · top channel' },
  { label: 'SUBSTACK SUBSCRIBERS', value: '6,180', valueColor: 'var(--ink)', note: 'manual count v1 · API deferred' },
];

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

// Compact month grid (weeks of July). channels[] drives the color dots.
const CAL_DAYS: CalDay[] = [
  { day: 1, channels: ['X'] },
  { day: 2, channels: ['YouTube', 'Substack'] },
  { day: 3, channels: ['Instagram'] },
  { day: 4, channels: [] },
  { day: 5, channels: ['Email'] },
  { day: 6, channels: ['Substack', 'X', 'Instagram', 'Facebook'], conflict: true },
  { day: 7, channels: ['Email'] },
  { day: 8, channels: ['X', 'Podcast'] },
  { day: 9, channels: ['Instagram', 'Facebook'] },
  { day: 10, channels: ['Substack'] },
  { day: 11, channels: ['Podcast', 'X', 'YouTube'] },
  { day: 12, channels: ['X'] },
  { day: 13, channels: [] },
  { day: 14, channels: ['Facebook'] },
  { day: 15, channels: ['YouTube', 'Instagram', 'X', 'Email'], conflict: true },
  { day: 16, channels: ['X'] },
  { day: 17, channels: ['Substack'] },
  { day: 18, channels: ['Instagram', 'Podcast'] },
  { day: 19, channels: ['Email'] },
  { day: 20, channels: ['X', 'Facebook'] },
  { day: 21, channels: [] },
];

const PERF_ROWS: PerfRow[] = [
  { channel: 'X / Twitter', reach: '128K', clicks: '5,240', conv: '42%', convColor: 'var(--ink)', tag: 'top' },
  { channel: 'Substack', reach: '6,180', clicks: '1,910', conv: '31%', convColor: 'var(--ink)' },
  { channel: 'Email', reach: '14,300', clicks: '2,070', conv: '24%', convColor: 'var(--ink)' },
  { channel: 'Podcast', reach: '9,400', clicks: '610', conv: '19%', convColor: 'var(--ink)' },
  { channel: 'Instagram', reach: '41K', clicks: '1,180', conv: '11%', convColor: 'var(--ink-2)' },
  { channel: 'Facebook', reach: '22K', clicks: '430', conv: '6%', convColor: 'var(--ink-3)', tag: 'bottom' },
];

const VOICE_SUGGESTIONS: VoiceSuggestion[] = [
  {
    before: 'Our students get 4X the results of traditional school.',
    after: 'Our students move at their own pace with mastery-based learning.',
    rule: 'V-2 grounding · "4X" is an unverifiable claim',
  },
  {
    before: 'The best school money can buy for your kid.',
    after: 'A learning model built around how your child actually learns.',
    rule: 'V-4 on-brand · softer, family-first register',
  },
];

const LIBRARY: LibItem[] = [
  { title: 'Alpha-X day-in-the-life', type: 'video', persona: 'High-intent parent', tier: 'T1', channel: 'YouTube' },
  { title: 'Why we test on X', type: 'article', persona: 'Skeptic', tier: 'T2', channel: 'Substack' },
  { title: 'Tuition / ESA FAQ', type: 'article', persona: 'Cost-sensitive', tier: 'T3', channel: 'Facebook' },
  { title: 'K–2 sweet-spot carousel', type: 'social', persona: 'Early-grade parent', tier: 'T1', channel: 'Instagram' },
  { title: 'AGL podcast with Pam', type: 'podcast', persona: 'Researcher', tier: 'T2', channel: 'Podcast' },
  { title: 'Week-in-review newsletter', type: 'email', persona: 'Engaged lead', tier: 'T2', channel: 'Email' },
];

const LIB_TAGS: string[] = ['persona', 'tier', 'channel', 'type', 'T1', 'T2', 'T3', 'video', 'article', 'social'];

// ---- Component -------------------------------------------------------------
export function ContentModule() {
  const def = moduleById('content')!;
  const { session } = useSession();

  // Live kanban from the Google-Sheet seam; null until loaded / when unreachable.
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

  const connected = live !== null; // backend reachable (vs. static seed fallback)
  const sync = live?.sync ?? null;
  const isLiveSheet = sync?.mode === 'live';

  // Columns + stage order: the live payload when present, else the inline seed.
  const stages: string[] = live ? live.stages : KANBAN.map((k) => k.col);
  const columns: { col: string; cards: Card[] }[] = live
    ? live.columns.map((c) => ({ col: c.stage, cards: c.cards.map(rowToCard) }))
    : KANBAN.map((k) => ({ col: k.col, cards: k.cards }));

  // Move a card to the NEXT stage and write it back to the sheet.
  const moveCard = useCallback(
    async (card: Card) => {
      if (!card.raw || busy) return;
      const idx = stages.indexOf(card.raw.stage);
      if (idx < 0 || idx >= stages.length - 1) return; // already at the last stage
      const next = stages[idx + 1];
      setBusy(true);
      const ok = await postKanbanRow({ ...card.raw, stage: next }, session.role);
      if (ok) await refresh();
      setBusy(false);
    },
    [stages, busy, session.role, refresh],
  );

  // Add a new card to a stage (title via prompt) and write it back to the sheet.
  const addCard = useCallback(
    async (stage: string) => {
      if (busy || typeof window === 'undefined') return;
      const title = window.prompt(`New ${stage} card — title?`)?.trim();
      if (!title) return;
      setBusy(true);
      const ok = await postKanbanRow(
        {
          title,
          type: 'article',
          stage,
          owner: 'the Content Owner',
          channel: 'Substack',
          utm: '',
          target_date: '',
        },
        session.role,
      );
      if (ok) await refresh();
      setBusy(false);
    },
    [busy, session.role, refresh],
  );

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* Sync banner — the label is DERIVED from the real seam, never hardcoded */}
        <SyncBanner connected={connected} isLiveSheet={isLiveSheet} tab={sync?.tab ?? null} />

        {/* Top stat row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
          {TOP_STATS.map((s, i) => {
            const hero = i === 2; // X/Twitter — the pre-sold engine
            return (
              <div
                key={s.label}
                style={{
                  border: `1px solid ${hero ? 'var(--ink)' : 'var(--line-2)'}`,
                  background: 'var(--card)',
                  padding: 13,
                }}
              >
                <div
                  style={{
                    fontFamily: MONO,
                    fontSize: 9,
                    letterSpacing: '.4px',
                    color: 'var(--ink-3)',
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

        {/* Production pipeline — kanban */}
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
                        style={{
                          fontFamily: MONO,
                          fontSize: 10,
                          lineHeight: 1,
                          cursor: busy ? 'default' : 'pointer',
                          border: '1px solid var(--line-2)',
                          background: 'var(--card)',
                          color: 'var(--ink-2)',
                          padding: '1px 5px',
                        }}
                      >
                        +
                      </button>
                    )}
                  </span>
                </div>
                {kc.cards.map((c) => (
                  <KanbanCard
                    key={c.name}
                    card={c}
                    onMove={connected && c.raw ? () => moveCard(c) : undefined}
                    busy={busy}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Calendar + brand voice auditor */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 14, marginBottom: 14 }}>
          {/* content calendar */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Content calendar</div>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>July · color = channel</span>
            </div>
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 11 }}>
              A ⚑ marks a same-day pile-up (4+ pieces) — flag to re-space the schedule.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
              {(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const).map((d) => (
                <div key={d} style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', textAlign: 'center', fontWeight: 600 }}>{d}</div>
              ))}
              {CAL_DAYS.map((cd) => (
                <CalCell key={cd.day} day={cd} />
              ))}
            </div>
            {/* legend */}
            <div style={{ display: 'flex', gap: 8, marginTop: 11, flexWrap: 'wrap' }}>
              {(Object.keys(CHANNEL_COLOR) as Channel[]).map((ch) => (
                <span key={ch} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 8, color: 'var(--ink-2)' }}>
                  <span style={{ width: 8, height: 8, background: CHANNEL_COLOR[ch].bg, border: `1px solid ${CHANNEL_COLOR[ch].fg}`, display: 'inline-block' }} />
                  {ch}
                </span>
              ))}
            </div>
          </div>

          {/* brand voice auditor */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Brand voice auditor</div>
              <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: 'var(--warn-soft)', color: 'var(--warn)' }}>SUGGEST-EDITS</span>
            </div>
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 11, lineHeight: 1.4 }}>
              Non-blocking — surfaces inline rewrites on a draft; the writer keeps or dismisses. (The hard grounding gate that blocks lives on outbound sends.)
            </div>
            {VOICE_SUGGESTIONS.map((v) => (
              <div key={v.before} style={{ borderTop: '1px solid var(--line)', padding: '9px 0' }}>
                <div style={{ fontSize: 10.5, color: 'var(--ink-3)', textDecoration: 'line-through', lineHeight: 1.4 }}>{v.before}</div>
                <div style={{ fontSize: 11, color: 'var(--ink)', marginTop: 4, lineHeight: 1.4 }}>
                  <span style={{ color: 'var(--ok)', fontFamily: MONO, fontSize: 9, marginRight: 5 }}>→</span>
                  {v.after}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--gold)', marginTop: 4 }}>{v.rule}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Performance table */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Performance · by channel</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>Meta + HubSpot · channel-level</span>
          </div>
          {/* honesty banner — UTM per-piece attribution is unreliable */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '8px 16px',
              borderBottom: '1px solid var(--line-2)',
              background: 'var(--signal-soft)',
            }}
          >
            <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: 'var(--signal)' }}>⚠ UTM ATTRIBUTION UNRELIABLE</span>
            <span style={{ fontSize: 10.5, color: 'var(--broken)' }}>
              Per-piece attribution is broken — channel rollups below are directional only. Module 7 (CRM / Ops) owns the UTM rebuild. We don&apos;t fabricate per-piece conversion.
            </span>
          </div>
          {/* header */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1.4fr .8fr .8fr .8fr .9fr',
              fontFamily: MONO,
              fontSize: 8.5,
              letterSpacing: '.3px',
              color: 'var(--ink-3)',
              padding: '8px 16px',
              borderBottom: '1px solid var(--line-2)',
              fontWeight: 600,
            }}
          >
            <div>CHANNEL</div>
            <div style={{ textAlign: 'right' }}>REACH</div>
            <div style={{ textAlign: 'right' }}>CLICKS</div>
            <div style={{ textAlign: 'right' }}>CONV</div>
            <div style={{ textAlign: 'center' }}>NOTE</div>
          </div>
          {PERF_ROWS.map((r) => (
            <div
              key={r.channel}
              style={{
                display: 'grid',
                gridTemplateColumns: '1.4fr .8fr .8fr .8fr .9fr',
                alignItems: 'center',
                padding: '9px 16px',
                borderBottom: '1px solid var(--line)',
              }}
            >
              <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{r.channel}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{r.reach}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{r.clicks}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 11, fontWeight: 600, color: r.convColor }}>{r.conv}</div>
              <div style={{ display: 'flex', justifyContent: 'center' }}>
                {r.tag === 'top' && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: 'var(--ok-soft)', color: 'var(--ok)' }}>▲ TOP</span>}
                {r.tag === 'bottom' && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>▼ BOTTOM</span>}
              </div>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 14, padding: '9px 16px', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-2)' }}>
              ▲ TOP — <b style={{ color: 'var(--ink)' }}>X / Twitter</b> at 42% conv (the pre-sold engine)
            </span>
            <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-2)' }}>
              ▼ BOTTOM — <b style={{ color: 'var(--ink)' }}>Facebook</b> at 6% conv · reallocate spend
            </span>
          </div>
        </div>

        {/* Content library */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Content library</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{LIBRARY.length} pieces · tagged archive</span>
          </div>
          {/* search + tag chips */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderBottom: '1px solid var(--line-2)', flexWrap: 'wrap' }}>
            <div
              style={{
                flex: 1,
                minWidth: 180,
                fontFamily: MONO,
                fontSize: 10,
                color: 'var(--ink-3)',
                border: '1px solid var(--line-2)',
                background: 'var(--paper)',
                padding: '6px 10px',
              }}
            >
              ⌕ search by persona, tier, channel, type…
            </div>
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
              {LIB_TAGS.map((t) => (
                <span key={t} style={{ fontFamily: MONO, fontSize: 8, padding: '2px 7px', background: 'var(--accent-soft)', color: 'var(--ink-2)' }}>{t}</span>
              ))}
            </div>
          </div>
          {LIBRARY.map((it) => (
            <div key={it.title} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)', minWidth: 16, textAlign: 'center' }}>{TYPE_GLYPH[it.type]}</span>
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{it.title}</span>
              <Tag>{it.persona}</Tag>
              <Tag>{it.tier}</Tag>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 8,
                  fontWeight: 600,
                  padding: '2px 7px',
                  background: CHANNEL_COLOR[it.channel].bg,
                  color: CHANNEL_COLOR[it.channel].fg,
                }}
              >
                {it.channel}
              </span>
            </div>
          ))}
        </div>

        {/* Cross-link note */}
        <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card)', padding: '11px 14px' }}>
          <div style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)', marginBottom: 6 }}>CROSS-MODULE WIRING</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>
            ⌖ Testimonials surfaced in <b style={{ color: 'var(--ink)' }}>Grassroots</b> auto-stub a production card here (see <i>Family Interviews ×5</i>).{' '}
            Top objections logged in <b style={{ color: 'var(--ink)' }}>Admissions</b> auto-create a content brief (see <i>&quot;is it a real school?&quot;</i>).
          </div>
        </div>
      </section>
    </>
  );
}

// ---- Helper subcomponents --------------------------------------------------
// Sync banner — the label is DERIVED from the real seam state (live vs simulated),
// never a hardcoded "SYNCED". A live Google Sheet says SYNCED; the simulated seam
// says so honestly; an unreachable backend shows the seed-sample state.
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
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        border: '1px solid var(--line-2)',
        background: 'var(--card)',
        padding: '10px 14px',
        marginBottom: 14,
        flexWrap: 'wrap',
      }}
    >
      <span
        style={{
          fontFamily: MONO,
          fontSize: 9,
          fontWeight: 600,
          padding: '3px 9px',
          background: pill.bg,
          color: pill.fg,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: pill.dot, display: 'inline-block' }} />
        {pill.label}
      </span>
      <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{copy}</span>
      <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginLeft: 'auto' }}>
        Excludes summer-camp content → Module 4
      </span>
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
            style={{
              fontFamily: MONO,
              fontSize: 10,
              lineHeight: 1,
              cursor: busy ? 'default' : 'pointer',
              border: '1px solid var(--line-2)',
              background: 'var(--card-2)',
              color: 'var(--ink-2)',
              padding: '1px 5px',
            }}
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

function CalCell({ day }: { day: CalDay }) {
  return (
    <div
      style={{
        border: `1px solid ${day.conflict ? 'var(--signal)' : 'var(--line)'}`,
        background: 'var(--card-2)',
        minHeight: 46,
        padding: '4px 5px',
        position: 'relative',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{day.day}</span>
        {day.conflict && <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--signal)', fontWeight: 600 }}>⚑</span>}
      </div>
      <div style={{ display: 'flex', gap: 2, flexWrap: 'wrap', marginTop: 3 }}>
        {day.channels.map((ch, i) => (
          <span
            key={`${day.day}-${ch}-${i}`}
            title={ch}
            style={{ width: 7, height: 7, background: CHANNEL_COLOR[ch].bg, border: `1px solid ${CHANNEL_COLOR[ch].fg}`, display: 'inline-block' }}
          />
        ))}
      </div>
    </div>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 8, padding: '2px 7px', background: 'var(--accent-soft)', color: 'var(--ink-2)' }}>{children}</span>
  );
}
