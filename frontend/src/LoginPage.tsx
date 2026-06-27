import { useState } from 'react';
import { apiBaseUrl } from './config';

// The demo sign-in gate (M1 login → B1 verified auth, MULTI_AGENT_COCKPIT.md
// §10.2). A simple blue page with a centered white rounded card carrying the GT
// Pulse logo and a seat picker. Picking a seat now trades it for a REAL signed
// JWT minted by the backend (`POST /auth/demo-token`) — the verified-principal
// bridge that replaced the old spoofable client-spelled role header. Still DEMO-only and
// synthetic (INV-1): the token is signed over synthetic seats, no PII, no real
// account. "Switch seat" (in the shell) returns here.

/** The three backend roles (app_metadata.role): the leadership/admin lens, the
 *  leadership read view, and a single sales operator's owner-scoped seat. The old
 *  M1 `agent` seat is now `operator` (preserving its agent_id). */
export type DemoRole = 'admin' | 'leader' | 'operator';

/** A sales operator's tier — "closer" is a tier, NOT a role
 *  (MULTI_AGENT_COCKPIT.md §2.2). */
export type AgentTier = 'closer' | 'setter';

export interface DemoSession {
  role: DemoRole;
  /** The signed seat JWT minted by `POST /auth/demo-token`; sent as
   *  `Authorization: Bearer <token>` on every cockpit API call (config.ts). */
  token?: string;
  /** Absolute expiry (epoch ms) derived from the mint's `expires_in`. Stored for
   *  observability/debugging; expiry is enforced server-side (an expired token
   *  401s and the user re-enters) — there is NO client-side silent refresh. */
  expiresAt?: number;
  /** The chosen agent's canonical agent_id uuid (only when role === 'operator'). */
  agentId?: string;
  /** The agent's pipeline rank (1 = closer seat, 2 = setter). */
  agentRank?: number;
  /** The agent's tier (only when role === 'operator'). */
  tier?: AgentTier;
  /** The agent's synthetic display name (only when role === 'operator'). */
  agentName?: string;
}

export interface DemoAgent {
  /** The canonical seeded agent_id uuid (migration 0013) — this is the value
   *  signed into the operator token's `app_metadata.agent_id` so the backend's
   *  get_principal owner-scopes the seat. */
  readonly id: string;
  readonly rank: number;
  readonly tier: AgentTier;
  readonly name: string;
}

// The demo runs exactly N=2 agents: rank 1 = closer (the founder's own seat),
// rank 2 = average/setter (MULTI_AGENT_COCKPIT.md §2.2, §10.1). "Closer" is a
// tier, not a role. agent_ids are the canonical seeded uuids (migration 0013) —
// the signed token's agent_id MUST match them. Synthetic names, no PII (INV-1).
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

/** The OAuth-bearer-shaped response from `POST /auth/demo-token`. */
interface DemoTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** Trade a chosen seat for a REAL signed JWT from the backend's demo-auth bridge
 *  (`POST /auth/demo-token`, body `{role, agent_id?}`). Throws on a non-OK
 *  response so the gate can surface an error and NOT store a broken session. */
async function fetchDemoToken(
  role: DemoRole,
  agentId?: string,
): Promise<DemoTokenResponse> {
  const body: { role: DemoRole; agent_id?: string } = { role };
  if (agentId) body.agent_id = agentId;
  const res = await fetch(`${apiBaseUrl}/auth/demo-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`demo-token mint failed (${res.status})`);
  }
  return (await res.json()) as DemoTokenResponse;
}

export default function LoginPage({
  onEnter,
}: {
  onEnter: (session: DemoSession) => void;
}): JSX.Element {
  const [role, setRole] = useState<DemoRole>('admin');
  const firstAgent = DEMO_AGENTS[0];
  const [agentId, setAgentId] = useState<string>(firstAgent?.id ?? '');
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  async function enter(): Promise<void> {
    setError(null);
    setBusy(true);
    // The operator seat carries its canonical agent_id; admin/leader do not.
    const agent =
      role === 'operator'
        ? (DEMO_AGENTS.find((a) => a.id === agentId) ?? firstAgent)
        : undefined;
    if (role === 'operator' && !agent) {
      setBusy(false);
      return;
    }
    try {
      const minted = await fetchDemoToken(role, agent?.id);
      const session: DemoSession = {
        role,
        token: minted.access_token,
        expiresAt: Date.now() + minted.expires_in * 1000,
      };
      if (agent) {
        session.agentId = agent.id;
        session.agentRank = agent.rank;
        session.tier = agent.tier;
        session.agentName = agent.name;
      }
      onEnter(session);
    } catch {
      // Fail closed: surface the error, store NOTHING (no broken/partial seat).
      setError('Could not sign in. Is the API running? Try again.');
    } finally {
      setBusy(false);
    }
  }

  const selectedAgent =
    DEMO_AGENTS.find((a) => a.id === agentId) ?? firstAgent;

  const ROLE_SEATS: ReadonlyArray<{ value: DemoRole; label: string }> = [
    { value: 'admin', label: 'Admin' },
    { value: 'leader', label: 'Leadership' },
    { value: 'operator', label: 'Operator' },
  ];

  return (
    <div className="login-page" data-testid="login-page">
      <form
        className="login-card"
        data-testid="login-card"
        onSubmit={(e) => {
          e.preventDefault();
          void enter();
        }}
      >
        {/* The logo art is white-on-transparent; we recolor it to the brand navy
            via a CSS mask so it reads on the white card with NO background tile. */}
        <span className="login-logo" role="img" aria-label="GT Pulse" />
        <p className="login-sub">Demo sign-in · pick a seat</p>

        <div
          className="login-roles"
          role="tablist"
          aria-label="Choose your seat"
        >
          {ROLE_SEATS.map((seat) => (
            <button
              key={seat.value}
              type="button"
              role="tab"
              aria-selected={role === seat.value}
              data-testid={`login-role-${seat.value}`}
              className={`login-role-btn${role === seat.value ? ' is-active' : ''}`}
              onClick={() => setRole(seat.value)}
            >
              {seat.label}
            </button>
          ))}
        </div>

        {role === 'operator' && (
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
                  {`${a.name} · ${tierLabel(a.tier)}`}
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

        <button
          type="submit"
          className="login-enter"
          data-testid="login-enter"
          disabled={busy}
        >
          {busy ? 'Signing in…' : 'Enter'}
        </button>

        {error && (
          <p className="login-error" role="alert" data-testid="login-error">
            {error}
          </p>
        )}

        <p className="login-foot">
          Demo sign-in · a real signed token over synthetic seats, no PII.
        </p>
      </form>
    </div>
  );
}
