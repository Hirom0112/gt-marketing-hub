'use client';

// Client-side session + theme. In v1 the role is switchable in the sidebar
// ("VIEWING AS" — admin / leader / operator) so the hard gates are demoable
// without real auth. Swapping in real auth later means replacing this provider
// and keeping the same Session shape from registry.ts.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { ModuleId, Role, Session } from './registry';

type Theme = 'light' | 'dark';

interface Ctx {
  session: Session;
  setRole: (r: Role) => void;
  theme: Theme;
  toggleTheme: () => void;
}

// Per-role identities used for the demo. The operator is the Grassroots Owner,
// matching the design prototype (owns `grassroots`, reads everything else).
const ROLE_PRESET: Record<Role, Session> = {
  admin: { role: 'admin', ownedModules: [], userName: 'Maya Chen', userRole: 'Admin · the Marketing Lead' },
  leader: { role: 'leader', ownedModules: [], userName: 'Dave Ruiz', userRole: 'Leader · Growth Marketing Officer' },
  operator: {
    role: 'operator',
    ownedModules: ['grassroots'] as ModuleId[],
    userName: 'Sam Okafor',
    userRole: 'Operator · the Grassroots Owner',
  },
};

const SessionContext = createContext<Ctx | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<Role>('leader');
  const [theme, setTheme] = useState<Theme>('light');

  const setRole = useCallback((r: Role) => setRoleState(r), []);
  const toggleTheme = useCallback(() => setTheme((t) => (t === 'light' ? 'dark' : 'light')), []);

  // Alt/⌘-T toggles theme, matching the design's keyboard affordance.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.altKey || e.metaKey) && (e.key === 't' || e.key === 'T')) {
        e.preventDefault();
        toggleTheme();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleTheme]);

  // Reflect theme onto <html data-theme> so the CSS variables flip.
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const value = useMemo<Ctx>(
    () => ({ session: ROLE_PRESET[role], setRole, theme, toggleTheme }),
    [role, theme, setRole, toggleTheme],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Ctx {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used within <SessionProvider>');
  return ctx;
}
