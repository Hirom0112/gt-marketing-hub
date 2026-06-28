'use client';

// Per-module sub-view tab bar. Works UNCONTROLLED (own local active state — the
// default for modules whose tabs are cosmetic) or CONTROLLED: pass `active` +
// `onChange` and the parent drives which sub-view renders (the Dashboard does this).

import { useState } from 'react';

export function TabBar({
  tabs,
  active: controlledActive,
  onChange,
}: {
  tabs: string[];
  active?: number;
  onChange?: (index: number) => void;
}) {
  const [localActive, setLocalActive] = useState(0);
  const active = controlledActive ?? localActive;
  const setActive = (i: number) => {
    onChange?.(i);
    if (controlledActive === undefined) setLocalActive(i);
  };
  if (tabs.length <= 1) return null;
  return (
    <div style={{ display: 'flex', gap: 2, borderBottom: '1px solid var(--line-2)', padding: '0 22px', background: 'var(--card)' }}>
      {tabs.map((t, i) => (
        <Tab key={t} label={t} on={i === active} onClick={() => setActive(i)} />
      ))}
    </div>
  );
}

function Tab({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={on}
      style={{
        cursor: 'pointer',
        border: 'none',
        background: 'transparent',
        padding: '10px 13px 9px',
        fontFamily: 'Geist',
        fontSize: 12,
        fontWeight: on ? 600 : 500,
        letterSpacing: '.1px',
        color: on ? 'var(--ink)' : hover ? 'var(--ink-2)' : 'var(--ink-3)',
        borderBottom: `2px solid ${on ? 'var(--brand)' : hover ? 'var(--line)' : 'transparent'}`,
        marginBottom: -1,
        transition: 'color .15s var(--ease), border-bottom-color .15s var(--ease)',
      }}
    >
      {label}
    </button>
  );
}
