'use client';

// Home / Executive Command Center — the personal, composable dashboard.
// Each user builds their own Home from the 44-widget library: add/remove via the
// "+ Add widget" picker, resize (small/medium/large), drag to reorder, and the
// layout saves PER USER to localStorage (keyed on the signed-in name). New users
// get the default starter pack. Home aggregates — every widget reads its owning
// module's number; it never recomputes one.

import { useEffect, useRef, useState } from 'react';
import { STARTER_IDS, widgetById, WIDGETS, type WidgetSize } from '@/lib/widgets';
import { useSession } from '@/lib/session';
import { WidgetCard, NEXT_SIZE } from '@/components/home/WidgetCard';
import { WidgetPicker } from '@/components/home/WidgetPicker';
import { fetchHomeLive, type HomeLive } from '@/lib/home-live';

const MONO = 'JetBrains Mono';

interface Layout { ids: string[]; sizes: Record<string, WidgetSize>; }
const keyFor = (user: string) => `gt-home-layout:${user}`;

export function HomeModule() {
  const { session } = useSession();
  const [ids, setIds] = useState<string[]>(STARTER_IDS);
  const [sizes, setSizes] = useState<Record<string, WidgetSize>>({});
  const [editing, setEditing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [live, setLive] = useState<HomeLive | null>(null);
  const loaded = useRef(false);
  const dragIdx = useRef<number | null>(null);

  // Pull the live aggregates from every owning module once (fails soft per endpoint →
  // those widgets just stay on their labelled SAMPLE seed).
  useEffect(() => {
    let on = true;
    fetchHomeLive(session.role).then((d) => { if (on) setLive(d); });
    return () => { on = false; };
  }, [session.role]);

  // Load this user's saved layout once on mount.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(keyFor(session.userName));
      if (raw) {
        const l = JSON.parse(raw) as Layout;
        if (Array.isArray(l.ids)) setIds(l.ids.filter(widgetById));
        if (l.sizes) setSizes(l.sizes);
      }
    } catch { /* first run / private mode — fall back to starter pack */ }
    loaded.current = true;
  }, [session.userName]);

  // Persist on change (after the initial load, so we don't clobber saved state).
  useEffect(() => {
    if (!loaded.current) return;
    try { localStorage.setItem(keyFor(session.userName), JSON.stringify({ ids, sizes } satisfies Layout)); } catch { /* ignore */ }
  }, [ids, sizes, session.userName]);

  const sizeOf = (id: string): WidgetSize => sizes[id] ?? widgetById(id)?.size ?? 'small';
  const toggle = (id: string) => setIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  const remove = (id: string) => setIds((cur) => cur.filter((x) => x !== id));
  const resize = (id: string) => setSizes((s) => ({ ...s, [id]: NEXT_SIZE[sizeOf(id)] }));
  const reorder = (to: number) => {
    const from = dragIdx.current;
    if (from === null || from === to) return;
    setIds((cur) => { const next = [...cur]; const [m] = next.splice(from, 1); next.splice(to, 0, m); return next; });
    dragIdx.current = to;
  };

  const greeting = `Good morning, ${session.userName.split(' ')[0]}.`;
  const placed = new Set(ids);
  // Count placed widgets currently showing live wire data (incl. the live decision queue).
  const liveCount = ids.filter(
    (id) => id === 'decision-queue-preview' || (live?.status[id] && live.status[id] !== 'sample'),
  ).length;

  return (
    <section className="scr" style={{ padding: '20px 22px 40px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 16, borderBottom: '1px solid var(--line)', paddingBottom: 12 }}>
        <div>
          <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 22, letterSpacing: '-.4px', lineHeight: 1.1, color: 'var(--ink)' }}>{greeting}</div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)', marginTop: 5 }}>
            Personal dashboard · <b style={{ color: 'var(--ink-2)' }}>{ids.length}</b> widgets placed ·{' '}
            <b style={{ color: 'var(--ok)' }}>● {liveCount} live</b> from the backbone ·{' '}
            <span style={{ color: 'var(--ink-3)' }}>each pill shows ● LIVE / ◐ stood-in / ○ sample</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 7, position: 'relative' }}>
          <ChipButton active={editing} onClick={() => setEditing((e) => !e)}>{editing ? '✓ DONE EDITING' : '⊞ EDIT LAYOUT'}</ChipButton>
          <ChipButton solid onClick={() => setPickerOpen((p) => !p)}>+ ADD WIDGET ▾</ChipButton>
          {pickerOpen && <WidgetPicker placed={placed} onToggle={toggle} onClose={() => setPickerOpen(false)} />}
        </div>
      </div>

      {ids.length === 0 ? (
        <div style={{ padding: '48px 0', textAlign: 'center', color: 'var(--ink-3)', fontFamily: MONO, fontSize: 12 }}>
          No widgets placed. Use <b style={{ color: 'var(--ink-2)' }}>+ Add widget</b> to build your Home.
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 12 }}>
          {ids.map((id, i) => {
            const def = widgetById(id);
            if (!def) return null;
            return (
              <WidgetCard
                key={id}
                def={def}
                size={sizeOf(id)}
                editing={editing}
                liveContent={live?.content[id]}
                status={live?.status[id]}
                onResize={() => resize(id)}
                onRemove={() => remove(id)}
                dragHandlers={{
                  draggable: true,
                  onDragStart: () => { dragIdx.current = i; },
                  onDragOver: (e) => { e.preventDefault(); reorder(i); },
                  onDragEnd: () => { dragIdx.current = null; },
                }}
              />
            );
          })}
        </div>
      )}

      {editing && (
        <div style={{ marginTop: 14, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          Drag cards to reorder · ▫▭▬ to resize · × to remove · layout saves to <b style={{ color: 'var(--ink-2)' }}>{session.userName}</b>
        </div>
      )}
    </section>
  );
}

function ChipButton({ active = false, solid = false, onClick, children }: { active?: boolean; solid?: boolean; onClick: () => void; children: React.ReactNode }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={active}
      style={{
        cursor: 'pointer',
        fontFamily: MONO,
        fontSize: 9,
        letterSpacing: '.4px',
        padding: '5px 10px',
        border: `1px solid ${solid || active || hover ? 'var(--ink)' : 'var(--line-2)'}`,
        background: solid ? 'var(--ink)' : active ? 'var(--accent-soft)' : hover ? 'var(--accent-soft)' : 'transparent',
        color: solid ? 'var(--paper)' : active || hover ? 'var(--ink)' : 'var(--ink-3)',
      }}
    >
      {children}
    </button>
  );
}
