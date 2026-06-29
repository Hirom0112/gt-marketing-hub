'use client';

// Renders one Home widget from its WidgetContent spec (kind-dispatched), wrapped
// in a card chrome with the source tag, a size-cycle control, and a remove (×).
// The same renderer covers all 44 library widgets.

import { useEffect, useState } from 'react';
import type { WidgetContent, WidgetDef, WidgetSize } from '@/lib/widgets';
import type { LiveStatus } from '@/lib/home-live';
import { useSession } from '@/lib/session';
import { canViewFullQueue } from '@/lib/registry';
import { apiGet } from '@/lib/api';
import { type ApiDecision, workstreamLabel } from '@/lib/decisions';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

const SPAN: Record<WidgetSize, number> = { small: 4, medium: 6, large: 12 };
const NEXT_SIZE: Record<WidgetSize, WidgetSize> = { small: 'medium', medium: 'large', large: 'small' };
const SIZE_GLYPH: Record<WidgetSize, string> = { small: '▫', medium: '▭', large: '▬' };

export { SPAN, NEXT_SIZE };

export function WidgetCard({
  def, size, editing, onResize, onRemove, dragHandlers, liveContent, status,
}: {
  def: WidgetDef;
  size: WidgetSize;
  editing: boolean;
  onResize: () => void;
  onRemove: () => void;
  dragHandlers?: React.HTMLAttributes<HTMLDivElement>;
  liveContent?: WidgetContent;
  status?: LiveStatus;
}) {
  const [hover, setHover] = useState(false);
  // The decision-queue preview reads the live queue in its own Body → always live.
  const effStatus: LiveStatus = def.id === 'decision-queue-preview' ? 'live' : status ?? 'sample';
  return (
    <div
      {...(editing ? dragHandlers : {})}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        gridColumn: `span ${SPAN[size]}`,
        border: `1px solid ${hover ? 'var(--line)' : 'var(--line-2)'}`,
        background: hover ? 'var(--card-2)' : 'var(--card)',
        padding: 14,
        position: 'relative',
        cursor: editing ? 'grab' : 'default',
        transition: 'background .15s var(--ease), border-color .15s var(--ease)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 9 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600, textTransform: 'uppercase' }}>
          {def.label}
        </span>
        {editing ? (
          <span style={{ display: 'flex', gap: 4 }}>
            <CtlButton onClick={onResize} title="Resize">{SIZE_GLYPH[size]}</CtlButton>
            <CtlButton onClick={onRemove} title="Remove" signal>×</CtlButton>
          </span>
        ) : (
          <StatusChip status={effStatus} source={def.source} />
        )}
      </div>
      <Body def={def} content={liveContent ?? def.content} />
    </div>
  );
}

// The honesty pill: tells the reader at a glance whether the number is live wire data,
// a real round-trip over a stood-in source (GA4), or static seed.
function StatusChip({ status, source }: { status: LiveStatus; source: string }) {
  const meta: Record<LiveStatus, { glyph: string; label: string; bg: string; color: string }> = {
    live: { glyph: '●', label: 'LIVE', bg: 'var(--ok-soft)', color: 'var(--ok)' },
    simulated: { glyph: '◐', label: 'STOOD-IN', bg: 'var(--warn-soft)', color: 'var(--warn)' },
    sample: { glyph: '○', label: 'SAMPLE', bg: 'var(--accent-soft)', color: 'var(--ink-3)' },
  };
  const m = meta[status];
  return (
    <span
      title={
        status === 'live' ? `Live wire data · ${source}`
        : status === 'simulated' ? `Live API round-trip over a stood-in source · ${source}`
        : `Static sample (no live endpoint yet) · ${source}`
      }
      style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, letterSpacing: '.3px', padding: '1px 6px', background: m.bg, color: m.color, whiteSpace: 'nowrap' }}
    >
      {m.glyph} {m.label} · {source}
    </span>
  );
}

// Decision-queue preview — leadership-only (spec Module 11). Fetches the top open
// decisions from the live backbone; operators get an empty/locked state; a failed
// fetch falls back to an empty line (fail-soft).
function DecisionPreviewBody() {
  const { session } = useSession();
  const allowed = canViewFullQueue(session); // admin + leader
  const [rows, setRows] = useState<ApiDecision[] | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!allowed) { setLoaded(true); return; }
    let live = true;
    apiGet<ApiDecision[]>('/decisions?view=active', session.role).then((data) => {
      if (!live) return;
      if (Array.isArray(data)) setRows(data);
      setLoaded(true);
    });
    return () => { live = false; };
  }, [allowed, session.role]);

  if (!allowed) {
    return (
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-3)', display: 'flex', alignItems: 'center', gap: 7 }}>
        <span>🔒</span> Leadership-only — the decision queue is hidden for your seat.
      </div>
    );
  }
  if (!loaded) return <div style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-3)' }}>Loading…</div>;
  const top = (rows ?? []).slice(0, 3);
  if (top.length === 0) {
    return <div style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-3)' }}>No open decisions — the queue is clear.</div>;
  }
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
      {top.map((d) => (
        <li key={d.id} style={{ fontSize: 11.5, color: 'var(--ink-2)', display: 'flex', gap: 7, alignItems: 'baseline' }}>
          <span style={{ color: d.priority === 'urgent' ? 'var(--signal)' : 'var(--gold)' }}>·</span>
          <span style={{ flex: 1 }}>
            {d.question || '(untitled)'}
            <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginLeft: 6 }}>
              {workstreamLabel(d.workstream)} · awaiting leader
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function CtlButton({ onClick, title, signal, children }: { onClick: () => void; title: string; signal?: boolean; children: React.ReactNode }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        cursor: 'pointer',
        fontFamily: MONO,
        fontSize: 10,
        lineHeight: 1,
        padding: '2px 5px',
        border: `1px solid ${signal ? 'var(--signal)' : hover ? 'var(--ink-3)' : 'var(--line-2)'}`,
        background: signal ? (hover ? 'var(--signal-soft)' : 'var(--card-2)') : hover ? 'var(--accent-soft)' : 'var(--card-2)',
        color: signal ? 'var(--signal)' : 'var(--ink-2)',
      }}
    >
      {children}
    </button>
  );
}

function Body({ def, content }: { def: WidgetDef; content: WidgetContent }) {
  // The Decision-Queue preview is leadership-only and reads the live open queue.
  if (def.id === 'decision-queue-preview') return <DecisionPreviewBody />;
  const c = content;
  switch (c.kind) {
    case 'stat':
      return (
        <>
          <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 30, lineHeight: 1.05, letterSpacing: '-.5px', color: 'var(--ink)' }}>
            {c.value}
            {c.delta && <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: c.deltaColor ?? 'var(--ok)', marginLeft: 6, letterSpacing: 0 }}>{c.delta}</span>}
          </div>
          {c.sub && <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 4 }}>{c.sub}</div>}
        </>
      );
    case 'progress':
      return (
        <>
          <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 28, lineHeight: 1.05, letterSpacing: '-.5px', color: 'var(--ink)' }}>{c.value}</div>
          <div style={{ height: 6, background: 'var(--card-2)', marginTop: 8, border: '1px solid var(--line)' }}>
            <div style={{ width: `${c.pct}%`, height: '100%', background: c.color ?? 'var(--gold)' }} />
          </div>
          {c.sub && <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 5 }}>{c.sub}</div>}
        </>
      );
    case 'bars':
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {c.rows.map((r) => (
            <div key={r.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 10, color: r.muted ? 'var(--ink-2)' : 'var(--ink)', width: 92, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
              <div style={{ flex: 1, height: 10, background: 'var(--card-2)' }}>
                <div style={{ width: `${r.width}%`, height: '100%', background: r.muted ? 'var(--ink-3)' : 'var(--gold)' }} />
              </div>
              <span style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 600, color: r.muted ? 'var(--ink-2)' : 'var(--ink)', width: 34, textAlign: 'right' }}>{r.pct}</span>
            </div>
          ))}
        </div>
      );
    case 'split':
      return (
        <>
          <div style={{ display: 'flex', height: 24, border: '1px solid var(--line)' }}>
            {c.segs.map((s, i) => (
              <div key={i} style={{ width: `${s.w}%`, background: s.color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 9, color: s.textColor ?? 'var(--on-brand)', fontWeight: 600 }}>{s.value}</div>
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 5 }}>
            {c.segs.map((s, i) => <span key={i}>{s.label}</span>)}
          </div>
          {c.sub && <div style={{ fontSize: 10, color: 'var(--signal)', marginTop: 6, fontWeight: 600 }}>{c.sub}</div>}
        </>
      );
    case 'tiers':
      return (
        <>
          <div style={{ display: 'flex', gap: 14 }}>
            {c.items.map((t) => (
              <div key={t.label}>
                <div style={{ fontFamily: DISPLAY, fontSize: 22, fontWeight: 600, lineHeight: 1.1, letterSpacing: '-.4px', color: 'var(--ink)' }}>{t.n}</div>
                <div style={{ fontSize: 9.5, color: 'var(--ink-2)', marginTop: 2 }}>{t.label}</div>
              </div>
            ))}
          </div>
          {c.sub && <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginTop: 9 }}>{c.sub}</div>}
        </>
      );
    case 'list':
      return (
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 5 }}>
          {c.items.map((it, i) => (
            <li key={i} style={{ fontSize: 11.5, color: 'var(--ink-2)', display: 'flex', gap: 7 }}>
              <span style={{ color: 'var(--gold)' }}>·</span><span>{it}</span>
            </li>
          ))}
        </ul>
      );
    case 'narrative':
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          {c.fields.map((f) => (
            <div key={f.label}>
              <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 3 }}>{f.label.toUpperCase()}</div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>{f.text}</div>
            </div>
          ))}
        </div>
      );
  }
}
