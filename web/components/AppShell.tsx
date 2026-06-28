'use client';

// The hub chrome: a fixed left masthead/sidebar + a top bar, with the active
// module's page rendered in the scroll area. Active module is derived from the
// URL so deep links and the nav stay in sync.

import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { moduleById } from '@/lib/registry';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';

function activeIdFromPath(pathname: string): string {
  const seg = pathname.split('/').filter(Boolean)[0];
  return seg || 'home';
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const activeId = activeIdFromPath(pathname);
  const active = moduleById(activeId);

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        width: '100%',
        overflow: 'hidden',
        background: 'var(--paper)',
        color: 'var(--ink)',
        fontSize: 13,
        lineHeight: 1.4,
      }}
    >
      <Sidebar activeId={activeId} />
      <main style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <TopBar active={active} />
        <div style={{ flex: 1, overflowY: 'auto' }}>{children}</div>
      </main>
    </div>
  );
}
