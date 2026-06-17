import { useState } from 'react';

// The demo sign-in gate (M1 login, MULTI_AGENT_COCKPIT.md §10.2). A simple blue
// page with a centered white rounded card carrying the GT Pulse logo and a seat
// picker. DEMO-ONLY role switch — NOT real authentication, no PII (INV-1); the
// server-side scoping is the M1 backend piece. Picking a seat enters the cockpit
// as that role; "Switch seat" (in the shell) returns here.

export type DemoRole = 'admin' | 'agent';

/** A sales agent's tier — "closer" is a tier, NOT a third role
 *  (MULTI_AGENT_COCKPIT.md §2.2). */
export type AgentTier = 'closer' | 'setter';

export interface DemoSession {
  role: DemoRole;
  /** The chosen agent's canonical agent_id uuid (only when role === 'agent'). */
  agentId?: string;
  /** The agent's pipeline rank (1 = closer seat, 2 = setter). */
  agentRank?: number;
  /** The agent's tier (only when role === 'agent'). */
  tier?: AgentTier;
  /** The agent's synthetic display name (only when role === 'agent'). */
  agentName?: string;
}

export interface DemoAgent {
  /** The canonical seeded agent_id uuid (migration 0013) — this is the value
   *  carried on X-Demo-Agent-Id so the backend's get_demo_principal scopes. */
  readonly id: string;
  readonly rank: number;
  readonly tier: AgentTier;
  readonly name: string;
}

// The demo runs exactly N=2 agents: rank 1 = closer (the founder's own seat),
// rank 2 = average/setter (MULTI_AGENT_COCKPIT.md §2.2, §10.1). "Closer" is a
// tier, not a third role. agent_ids are the canonical seeded uuids (migration
// 0013) — the header value MUST match them. Synthetic names, no PII (INV-1).
export const DEMO_AGENTS: ReadonlyArray<DemoAgent> = [
  {
    id: 'a0000000-0000-4000-8000-000000000001',
    rank: 1,
    tier: 'closer',
    name: 'Riley Carter',
  },
  {
    id: 'a0000000-0000-4000-8000-000000000002',
    rank: 2,
    tier: 'setter',
    name: 'Jordan Avery',
  },
] as const;

/** Human label for a tier badge. */
export function tierLabel(tier: AgentTier): string {
  return tier === 'closer' ? 'Closer' : 'Setter';
}

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
  const firstAgent = DEMO_AGENTS[0];
  const [agentId, setAgentId] = useState<string>(firstAgent?.id ?? '');

  function enter(): void {
    if (role === 'admin') {
      onEnter({ role: 'admin' });
      return;
    }
    const agent = DEMO_AGENTS.find((a) => a.id === agentId) ?? firstAgent;
    if (!agent) return;
    onEnter({
      role: 'agent',
      agentId: agent.id,
      agentRank: agent.rank,
      tier: agent.tier,
      agentName: agent.name,
    });
  }

  const selectedAgent =
    DEMO_AGENTS.find((a) => a.id === agentId) ?? firstAgent;

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
          <div className="login-agent-pick">
            <select
              className="login-select"
              data-testid="login-agent-select"
              aria-label="Sales agent"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
            >
              {DEMO_AGENTS.map((a) => (
                <option key={a.id} value={a.id}>
                  {`${a.name} — ${tierLabel(a.tier)}`}
                </option>
              ))}
            </select>
            {selectedAgent && (
              <span
                className={`login-tier-badge login-tier-${selectedAgent.tier}`}
                data-testid="login-tier-badge"
              >
                {tierLabel(selectedAgent.tier)}
              </span>
            )}
          </div>
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
