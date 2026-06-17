import { useState } from 'react';

// The demo sign-in gate (M1 login, MULTI_AGENT_COCKPIT.md §10.2). A simple blue
// page with a centered white rounded card carrying the GT Pulse logo and a seat
// picker. DEMO-ONLY role switch — NOT real authentication, no PII (INV-1); the
// server-side scoping is the M1 backend piece. Picking a seat enters the cockpit
// as that role; "Switch seat" (in the shell) returns here.

export type DemoRole = 'admin' | 'agent';

export interface DemoSession {
  role: DemoRole;
  /** The chosen agent (only when role === 'agent'). */
  agentId?: string;
  agentLabel?: string;
}

// The demo runs N=2 agents: #1 closer (the founder's seat) + #2 average/setter
// (MULTI_AGENT_COCKPIT.md §2). Synthetic, no PII.
const DEMO_AGENTS = [
  { id: 'agent-1', label: 'Agent #1 — Closer' },
  { id: 'agent-2', label: 'Agent #2 — Sales Agent' },
] as const;

const STORAGE_KEY = 'gt_demo_session';

/** Read a persisted demo seat (so a refresh keeps you signed in). */
export function loadSession(): DemoSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as DemoSession) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: DemoSession): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    /* non-fatal in the demo */
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* non-fatal */
  }
}

export default function LoginPage({
  onEnter,
}: {
  onEnter: (session: DemoSession) => void;
}): JSX.Element {
  const [role, setRole] = useState<DemoRole>('admin');
  const [agentId, setAgentId] = useState<string>(DEMO_AGENTS[0].id);

  function enter(): void {
    if (role === 'admin') {
      onEnter({ role: 'admin' });
      return;
    }
    const agent = DEMO_AGENTS.find((a) => a.id === agentId) ?? DEMO_AGENTS[0];
    onEnter({ role: 'agent', agentId: agent.id, agentLabel: agent.label });
  }

  return (
    <div className="login-page" data-testid="login-page">
      <form
        className="login-card"
        data-testid="login-card"
        onSubmit={(e) => {
          e.preventDefault();
          enter();
        }}
      >
        {/* The logo art is white-on-transparent; we recolor it to the brand navy
            via a CSS mask so it reads on the white card with NO background tile. */}
        <span className="login-logo" role="img" aria-label="GT Pulse" />
        <p className="login-sub">Demo sign-in — pick a seat</p>

        <div
          className="login-roles"
          role="tablist"
          aria-label="Choose your seat"
        >
          <button
            type="button"
            role="tab"
            aria-selected={role === 'admin'}
            data-testid="login-role-admin"
            className={`login-role-btn${role === 'admin' ? ' is-active' : ''}`}
            onClick={() => setRole('admin')}
          >
            Admin
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={role === 'agent'}
            data-testid="login-role-agent"
            className={`login-role-btn${role === 'agent' ? ' is-active' : ''}`}
            onClick={() => setRole('agent')}
          >
            Sales Agent
          </button>
        </div>

        {role === 'agent' && (
          <select
            className="login-select"
            data-testid="login-agent-select"
            aria-label="Sales agent"
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
          >
            {DEMO_AGENTS.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        )}

        <button type="submit" className="login-enter" data-testid="login-enter">
          Enter
        </button>

        <p className="login-foot">
          Demo role switch — not real authentication. All data is synthetic.
        </p>
      </form>
    </div>
  );
}
