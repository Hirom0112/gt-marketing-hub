'use client';

// The "+ Add widget" dropdown: a search box + the library grouped by category,
// a checkbox per widget with its source tag, and a Done button. Toggling a row
// adds/removes the widget from the user's Home immediately.

import { useMemo, useState } from 'react';
import { CATEGORY_ORDER, WIDGETS, type Category } from '@/lib/widgets';

const MONO = 'JetBrains Mono';

export function WidgetPicker({
  placed, onToggle, onClose,
}: {
  placed: Set<string>;
  onToggle: (id: string) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState('');
  const [closeHover, setCloseHover] = useState(false);

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return CATEGORY_ORDER.map((cat: Category) => ({
      cat,
      items: WIDGETS.filter((w) => w.category === cat && (!needle || w.label.toLowerCase().includes(needle) || w.source.toLowerCase().includes(needle))),
    })).filter((g) => g.items.length > 0);
  }, [q]);

  return (
    <div style={{ position: 'absolute', top: '100%', right: 0, marginTop: 6, width: 380, maxHeight: 520, overflowY: 'auto', background: 'var(--card)', border: '1px solid var(--ink)', boxShadow: '0 12px 32px -10px rgba(20, 28, 46, .42)', zIndex: 50 }}>
      <div style={{ position: 'sticky', top: 0, background: 'var(--card)', borderBottom: '1px solid var(--line-2)', padding: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search 44 widgets…"
          style={{ flex: 1, padding: '6px 9px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', fontFamily: 'Geist', fontSize: 12 }}
        />
        <button
          onClick={onClose}
          onMouseEnter={() => setCloseHover(true)}
          onMouseLeave={() => setCloseHover(false)}
          style={{ cursor: 'pointer', fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '6px 12px', border: '1px solid var(--ink)', background: closeHover ? 'var(--ink-2)' : 'var(--ink)', color: 'var(--paper)' }}
        >
          DONE
        </button>
      </div>

      {groups.map((g) => (
        <div key={g.cat}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.8px', color: 'var(--ink-3)', fontWeight: 600, padding: '10px 12px 4px', textTransform: 'uppercase' }}>{g.cat}</div>
          {g.items.map((w) => (
            <PickerRow key={w.id} label={w.label} source={w.source} on={placed.has(w.id)} onClick={() => onToggle(w.id)} />
          ))}
        </div>
      ))}
    </div>
  );
}

function PickerRow({ label, source, on, onClick }: { label: string; source: string; on: boolean; onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={on}
      style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 9, padding: '6px 12px', border: 'none', background: on ? 'var(--accent-soft)' : hover ? 'var(--card-2)' : 'transparent', cursor: 'pointer', textAlign: 'left' }}
    >
      <span style={{ width: 14, height: 14, border: `1px solid ${on ? 'var(--ink)' : hover ? 'var(--ink-3)' : 'var(--line-2)'}`, background: on ? 'var(--ink)' : 'transparent', color: 'var(--paper)', fontSize: 10, lineHeight: '13px', textAlign: 'center', flexShrink: 0 }}>{on ? '✓' : ''}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink)' }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{source}</span>
    </button>
  );
}
