'use client';

// Left masthead + grouped module nav + the "VIEWING AS" role switcher + theme
// toggle — the GT Pulse deep-blue nav rail. The GT|Pulse logo sits on a light tile
// at the top; nav order is domain-grouped (COMMAND / GROWTH / OPERATIONS) with each
// row's canonical spec index. Role badges: OWN on an operator's own module, LEAD
// lock on the Decision Queue.

import Link from 'next/link';
import { useState } from 'react';
import { GROUP_ORDER, MODULES, canView } from '@/lib/registry';
import type { Group, ModuleDef, Role } from '@/lib/registry';
import { useSession } from '@/lib/session';

const MONO = 'JetBrains Mono';

const ROLE_BTNS: { k: Role; label: string }[] = [
  { k: 'admin', label: 'ADMIN' },
  { k: 'leader', label: 'LEADER' },
  { k: 'operator', label: 'OPER' },
];

export function Sidebar({ activeId }: { activeId: string }) {
  const { session, setRole, theme, toggleTheme } = useSession();

  return (
    <aside
      style={{
        width: 236,
        minWidth: 236,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--chrome)',
        borderRight: '1px solid var(--chrome-edge)',
      }}
    >
      {/* Masthead — GT|Pulse logo on the blue rail */}
      <div style={{ padding: '17px 14px 14px', borderBottom: '1px solid var(--chrome-edge)', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/gt-pulse-logo.png" alt="GT Pulse" style={{ width: 168, height: 'auto', display: 'block' }} />
        <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '1.1px', color: 'var(--chrome-fg)', marginTop: 7, textAlign: 'center', textTransform: 'uppercase', opacity: 0.82 }}>
          Marketing Hub · Operations Almanac
        </div>
      </div>

      {/* Grouped nav */}
      <nav style={{ flex: 1, overflowY: 'auto', padding: '6px 0 10px' }}>
        {GROUP_ORDER.map((group: Group) => (
          <div key={group} style={{ marginTop: 4 }}>
            <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '1.4px', color: 'var(--chrome-fg)', padding: '12px 16px 6px', fontWeight: 600, opacity: 0.62, textTransform: 'uppercase' }}>
              {group}
            </div>
            {MODULES.filter((m) => m.group === group).map((m: ModuleDef) => {
              const active = activeId === m.id;
              const viewable = canView(session, m.id);
              const owns = session.role === 'operator' && session.ownedModules.includes(m.id);
              const locked = m.id === 'decision' && session.role === 'operator';

              const row = (
                <div
                  className={viewable && !active ? 'navbtn' : undefined}
                  style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    cursor: viewable ? 'pointer' : 'not-allowed',
                    textAlign: 'left',
                    padding: '6px 13px',
                    fontFamily: 'Geist',
                    fontSize: 12.5,
                    color: active ? 'var(--chrome-fg-active)' : 'var(--chrome-fg)',
                    background: active ? 'var(--chrome-active)' : 'transparent',
                    borderLeft: `3px solid ${active ? 'var(--chrome-accent)' : 'transparent'}`,
                    fontWeight: active ? 600 : 400,
                    opacity: viewable ? 1 : 0.55,
                    transition: 'background .15s var(--ease), color .15s var(--ease), border-left-color .15s var(--ease)',
                  }}
                >
                  <span style={{ fontFamily: MONO, fontSize: 10, color: active ? 'var(--chrome-accent)' : 'var(--chrome-fg)', minWidth: 16, opacity: active ? 1 : 0.65 }}>{m.idx}</span>
                  <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.label}</span>
                  {owns && <Badge bg="var(--chrome-accent)" color="var(--chrome)">OWN</Badge>}
                  {locked && <Badge bg="var(--chrome-hover)" color="var(--chrome-fg-active)">LEAD</Badge>}
                </div>
              );

              return viewable ? (
                <Link key={m.id} href={`/${m.id}`} style={{ display: 'block' }}>{row}</Link>
              ) : (
                <div key={m.id} title="Leadership only (operators submit into the queue from their own module)" aria-disabled>
                  {row}
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      {/* VIEWING AS + theme */}
      <div style={{ borderTop: '1px solid var(--chrome-edge)', padding: '12px 14px' }}>
        <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '1px', color: 'var(--chrome-fg)', marginBottom: 7, opacity: 0.62, textTransform: 'uppercase' }}>Viewing As</div>
        <div style={{ display: 'flex', gap: 4, marginBottom: 10 }}>
          {ROLE_BTNS.map((r) => (
            <RoleButton key={r.k} active={session.role === r.k} label={r.label} onClick={() => setRole(r.k)} />
          ))}
        </div>
        <ThemeToggle theme={theme} onClick={toggleTheme} />
      </div>
    </aside>
  );
}

function RoleButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={active}
      style={{
        flex: 1,
        cursor: 'pointer',
        padding: '5px 2px',
        border: `1px solid ${active || hover ? 'var(--chrome-accent)' : 'var(--chrome-edge)'}`,
        background: active ? 'var(--chrome-accent)' : 'var(--chrome-hover)',
        color: active ? 'var(--chrome)' : hover ? 'var(--chrome-fg-active)' : 'var(--chrome-fg)',
        fontFamily: MONO,
        fontSize: 9,
        fontWeight: 600,
        letterSpacing: '.4px',
      }}
    >
      {label}
    </button>
  );
}

function ThemeToggle({ theme, onClick }: { theme: 'light' | 'dark'; onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '7px 10px',
        border: `1px solid ${hover ? 'var(--chrome-accent)' : 'var(--chrome-edge)'}`,
        background: 'var(--chrome-hover)',
        color: hover ? 'var(--chrome-fg-active)' : 'var(--chrome-fg)',
        fontFamily: MONO,
        fontSize: 10,
        letterSpacing: '.3px',
      }}
    >
      <span>{theme === 'light' ? '☀ LIGHT' : '☾ DARK'}</span>
      <span style={{ opacity: 0.6 }}>⌥T</span>
    </button>
  );
}

function Badge({ bg, color, children }: { bg: string; color: string; children: React.ReactNode }) {
  return (
    <span style={{ fontFamily: 'JetBrains Mono', fontSize: 8, letterSpacing: '.5px', padding: '1px 5px', borderRadius: 2, background: bg, color, fontWeight: 600 }}>
      {children}
    </span>
  );
}
