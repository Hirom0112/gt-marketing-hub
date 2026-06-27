import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { LayoutGrid, X } from 'lucide-react';
import { GridLayout, useContainerWidth } from 'react-grid-layout';
import type { Layout } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import { apiFetch } from '../config';
import { Button, Chip } from '../ui';
import {
  WIDGET_BY_ID,
  WIDGET_GROUPS,
  WIDGETS,
  STARTER_IDS,
  type WidgetDef,
} from './widgetRegistry';
import { WidgetPlaceholder } from './widgets';

// ComposableHome (TODO_v2 §B3 / U4) — the per-user composable Home: a
// draggable/resizable react-grid-layout of cockpit widgets, a grouped picker to
// add/remove them, and a default starter pack — all persisted per-user through
// the backend `/home/layout` seam (GET merges saved + starter pack; PUT upserts).
//
// RGL API CHOICE: react-grid-layout@2.2.3's **v2 config-object API** — the
// `GridLayout` component fed `gridConfig`/`dragConfig`/`resizeConfig` objects,
// plus the v2 `useContainerWidth` hook for the responsive width (the v1 flat-prop
// API lives behind `react-grid-layout/legacy`; we use v2 deliberately). The
// persisted JSON ({i,x,y,w,h}) is identical across v1/v2.

// One RGL placement — the {i,x,y,w,h} the backend persists (a subset of RGL's
// LayoutItem; the extra optional LayoutItem fields are not persisted).
export interface Placement {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

const GRID_COLS = 12;
const DEFAULT_W = 4;
const DEFAULT_H = 4;
const PERSIST_DEBOUNCE_MS = 500;

// Narrow an RGL Layout (readonly LayoutItem[]) to our persisted Placement shape —
// only the five canonical keys are persisted.
function normalize(layout: Layout): Placement[] {
  return layout.map((it) => ({ i: it.i, x: it.x, y: it.y, w: it.w, h: it.h }));
}

// The fail-safe fallback grid: the starter ids laid out 3-per-row on the 12-col
// grid. Used ONLY when GET /home/layout cannot load — the Home must never crash
// or render empty just because its layout read failed.
function fallbackLayout(): Placement[] {
  return STARTER_IDS.map((id, slot) => ({
    i: id,
    x: (slot % 3) * DEFAULT_W,
    y: Math.floor(slot / 3) * DEFAULT_H,
    w: DEFAULT_W,
    h: DEFAULT_H,
  }));
}

export default function ComposableHome(): JSX.Element {
  const [layout, setLayout] = useState<Placement[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  // Hydration guard: RGL fires onLayoutChange on first render too; we must not
  // persist the layout we just GET'd back at the server. Flipped true once the
  // initial GET settles.
  const hydrated = useRef(false);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { width, containerRef } = useContainerWidth();

  // --- initial load -------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    apiFetch('/home/layout')
      .then((r) =>
        r.ok
          ? (r.json() as Promise<Placement[]>)
          : Promise.reject(new Error(String(r.status))),
      )
      .then((data) => {
        if (cancelled) return;
        setLayout(Array.isArray(data) ? data : fallbackLayout());
      })
      .catch(() => {
        // Fail safe: render the starter pack rather than crash on a failed read.
        if (!cancelled) setLayout(fallbackLayout());
      })
      .finally(() => {
        if (cancelled) return;
        setLoaded(true);
        hydrated.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // --- debounced persist --------------------------------------------------
  // RGL's onLayoutChange fires repeatedly during a drag/resize; debounce the PUT
  // so we persist once the gesture settles (~500ms idle). Picker add/remove
  // routes through the SAME debounced persist. We do NOT apply the PUT response
  // to state: within a session the local layout is source of truth (the backend
  // re-hydrates missing starter widgets, which would otherwise snap a just-removed
  // starter back mid-session); the merge is honored on the next fresh GET.
  const persist = useCallback((next: Placement[]): void => {
    if (debounceTimer.current !== null) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      void apiFetch('/home/layout', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout: next }),
      }).catch(() => {
        /* a failed persist is non-fatal; the next change retries */
      });
    }, PERSIST_DEBOUNCE_MS);
  }, []);

  // Tear down a pending timer on unmount so a late PUT can't fire post-unmount.
  useEffect(() => {
    return () => {
      if (debounceTimer.current !== null) clearTimeout(debounceTimer.current);
    };
  }, []);

  function onLayoutChange(next: Layout): void {
    const placements = normalize(next);
    setLayout(placements);
    // Skip the first-render echo so we don't re-persist the GET result.
    if (hydrated.current) persist(placements);
  }

  // --- picker add / remove ------------------------------------------------
  const present = new Set(layout.map((p) => p.i));

  function addWidget(id: string): void {
    const baseY = layout.reduce((m, p) => Math.max(m, p.y + p.h), 0);
    const next: Placement[] = [
      ...layout,
      { i: id, x: 0, y: baseY, w: DEFAULT_W, h: DEFAULT_H },
    ];
    setLayout(next);
    persist(next);
  }

  function removeWidget(id: string): void {
    const next = layout.filter((p) => p.i !== id);
    setLayout(next);
    persist(next);
  }

  function toggleWidget(id: string): void {
    if (present.has(id)) removeWidget(id);
    else addWidget(id);
  }

  return (
    <section data-testid="composable-home" aria-label="Home">
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--s-3)',
          marginBottom: 'var(--s-3)',
        }}
      >
        <h1 style={{ margin: 0, fontSize: '1.1rem', display: 'flex', gap: 8, alignItems: 'center' }}>
          <LayoutGrid size={18} aria-hidden /> Home
        </h1>
        <Button
          variant="default"
          onClick={() => setPickerOpen((o) => !o)}
          aria-expanded={pickerOpen}
        >
          {pickerOpen ? 'Close' : 'Add widgets'}
        </Button>
      </header>

      <div style={{ display: 'flex', gap: 'var(--s-3)', alignItems: 'flex-start' }}>
        {/* RGL's useContainerWidth types the ref as RefObject<T | null> (React 19
            shape); bridge to React 18's ref prop. */}
        <div
          ref={containerRef as RefObject<HTMLDivElement>}
          style={{ flex: 1, minWidth: 0 }}
        >
          {loaded && (
            <GridLayout
              width={width}
              layout={layout}
              onLayoutChange={onLayoutChange}
              gridConfig={{ cols: GRID_COLS, rowHeight: 60, margin: [16, 16] }}
              dragConfig={{
                enabled: true,
                handle: '.home-widget__handle',
                cancel: '.home-widget__remove',
              }}
              resizeConfig={{ enabled: true, handles: ['se'] }}
            >
              {layout.map((p) => (
                <div key={p.i} className="home-widget">
                  <WidgetTile id={p.i} onRemove={() => removeWidget(p.i)} />
                </div>
              ))}
            </GridLayout>
          )}
        </div>

        {pickerOpen && (
          <WidgetPicker present={present} onToggle={toggleWidget} />
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// One placed widget: a header (the drag handle + group tag + remove button) over
// the widget's component body. An id the frontend registry doesn't know (a
// placement the backend kept that this build doesn't map) degrades to a labeled
// placeholder rather than crashing.
function WidgetTile({
  id,
  onRemove,
}: {
  id: string;
  onRemove: () => void;
}): JSX.Element {
  const def: WidgetDef | undefined = WIDGET_BY_ID.get(id);
  const Body = def?.Component ?? (() => <WidgetPlaceholder label={id} />);
  return (
    <div
      className="home-widget__frame"
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--surface)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-lg)',
        boxShadow: 'var(--shadow-sm)',
        overflow: 'hidden',
      }}
    >
      <div
        className="home-widget__handle"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          padding: 'var(--s-2) var(--s-3)',
          borderBottom: '1px solid var(--line-2)',
          cursor: 'move',
          background: 'var(--surface-2)',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <strong
            style={{
              fontSize: '0.82rem',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {def?.label ?? id}
          </strong>
          {def && <Chip tone="neutral">{def.group}</Chip>}
        </span>
        <button
          type="button"
          className="home-widget__remove"
          onClick={onRemove}
          aria-label={`Remove ${def?.label ?? id}`}
          title="Remove widget"
          style={{
            display: 'inline-flex',
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            color: 'var(--muted)',
            padding: 2,
            borderRadius: 'var(--r-xs)',
          }}
        >
          <X size={14} aria-hidden />
        </button>
      </div>
      <div
        className="home-widget__body"
        style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 'var(--s-3)' }}
      >
        <Body />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The grouped widget picker: every catalog widget, sectioned by group, each a
// checkbox whose checked state is membership in the current layout. Checking adds
// the widget; unchecking removes it. The group is shown as the source/section tag.
function WidgetPicker({
  present,
  onToggle,
}: {
  present: ReadonlySet<string>;
  onToggle: (id: string) => void;
}): JSX.Element {
  return (
    <aside
      data-testid="widget-picker"
      aria-label="Add widgets"
      style={{
        width: 260,
        flexShrink: 0,
        maxHeight: '78vh',
        overflow: 'auto',
        background: 'var(--surface)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-lg)',
        boxShadow: 'var(--shadow-sm)',
        padding: 'var(--s-3)',
      }}
    >
      <h2 style={{ margin: '0 0 var(--s-2)', fontSize: '0.9rem' }}>Widgets</h2>
      {WIDGET_GROUPS.map((group) => {
        const inGroup = WIDGETS.filter((w) => w.group === group);
        return (
          <fieldset
            key={group}
            data-testid={`picker-group-${group}`}
            style={{ border: 'none', padding: 0, margin: '0 0 var(--s-3)' }}
          >
            <legend
              className="lab"
              style={{ color: 'var(--muted)', padding: 0, marginBottom: 4 }}
            >
              {group}
            </legend>
            {inGroup.map((w) => (
              <label
                key={w.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '3px 0',
                  fontSize: '0.85rem',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={present.has(w.id)}
                  onChange={() => onToggle(w.id)}
                  aria-label={w.label}
                />
                <span>{w.label}</span>
                {w.starter && <Chip tone="flow">starter</Chip>}
              </label>
            ))}
          </fieldset>
        );
      })}
    </aside>
  );
}
