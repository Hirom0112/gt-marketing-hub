'use client';

// Per-module sub-view tab bar. Client-local active state for now (deep sub-view
// routing comes when each module's tabs are individually built out).

import { useState } from 'react';

export function TabBar({ tabs }: { tabs: string[] }) {
  const [active, setActive] = useState(0);
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
