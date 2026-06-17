import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react';
import {
  type DemoSession,
  loadSession,
  saveSession,
  clearSession,
} from '../LoginPage';

// The demo session context (M1). It carries the chosen seat — role + (for an
// agent) rank, agentId, tier — to the whole app, backed by the SAME localStorage
// home as LoginPage (ONE storage key; this module reuses load/save/clear rather
// than duplicating persistence). The login gate calls `enter`; "Switch seat"
// calls `leave`. DEMO-ONLY scope switch, not real auth (INV-1). Later slices read
// `useSession()` to scope reads; the API layer reads the stored session to attach
// the X-Demo-Role / X-Demo-Agent-Id headers (config.ts).

export interface SessionContextValue {
  /** The current seat, or null when sitting at the login gate. */
  session: DemoSession | null;
  /** Enter the cockpit as the given seat (persists + sets state). */
  enter: (session: DemoSession) => void;
  /** Return to the login gate (clears the persisted seat). */
  leave: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  const [session, setSession] = useState<DemoSession | null>(() =>
    loadSession(),
  );

  const enter = useCallback((next: DemoSession): void => {
    saveSession(next);
    setSession(next);
  }, []);

  const leave = useCallback((): void => {
    clearSession();
    setSession(null);
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({ session, enter, leave }),
    [session, enter, leave],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

/** Read the demo session context. Throws if used outside a SessionProvider. */
export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (ctx === null) {
    throw new Error('useSession must be used within a SessionProvider');
  }
  return ctx;
}
