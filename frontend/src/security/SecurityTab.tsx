import { useEffect, useState } from 'react';
import {
  CircleCheck,
  CircleX,
  ShieldAlert,
  ShieldCheck,
  Siren,
} from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card, Chip } from '../ui';

// M7 — the admin-only Security / observability tab (MULTI_AGENT_COCKPIT §7).
//
// Two panels:
//   Panel A — Live RLS posture (the REAL panel): GET /security/posture. Every
//     public table FORCE-RLS + null-guarded ⇒ a GREEN banner; a table that lost
//     its policy ⇒ a RED alarm listing the offending tables.
//   Panel B — Suspicious-activity feed (SIMULATED in v1, INV-9 honesty): GET
//     /security/events. The append-only security_event stream; each row carries
//     an OWASP mapping + severity + detail and an ACKNOWLEDGE action. The whole
//     feed is visibly labeled "simulated" — it is monitoring, not inline
//     blocking, and not a live drain yet.
//
// Read-only over apiFetch (carries the demo headers). No service_role (INV-5);
// synthetic data only (INV-1). Token-driven styling, no raw hex.

// ── Panel A: RLS posture contract (GET /security/posture → PostureView) ──────
// One named RLS invariant + its pass/fail + detail. The backend runs the SAME
// test_migrations_rls invariants at runtime, so each row is a CHECK (name/passed/
// detail), not a per-table column matrix.
interface PostureCheck {
  name: string;
  passed: boolean;
  detail: string;
}

interface PostureReport {
  green: boolean;
  checks: PostureCheck[];
}

// ── Panel B: suspicious-event contract (GET /security/events → SecurityEventsView)
// The backend wraps the feed: { simulated, events: [...] } (the v1 'simulated'
// label travels on the envelope, INV-9).
interface SecurityEvent {
  event_id: string;
  occurred_at: string;
  actor_kind: string;
  surface: string | null;
  signal: string;
  severity: string;
  owasp: string;
  detail: string | null;
  simulated: boolean;
  acknowledged?: boolean;
}

interface SecurityEventsReport {
  simulated: boolean;
  events: SecurityEvent[];
}

type PostureState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: PostureReport };

type EventsState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: SecurityEvent[] };

const cellStyle = {
  padding: '8px 12px',
  borderBottom: '1px solid var(--line)',
  textAlign: 'left' as const,
};

const headStyle = {
  padding: '8px 12px',
  borderBottom: '1px solid var(--line-strong)',
  textAlign: 'left' as const,
  color: 'var(--muted)',
};

// Map a severity string to a semantic chip tone (tones: neutral|signal|flow|gate).
function severityTone(severity: string): 'signal' | 'gate' | 'neutral' {
  const s = severity.toLowerCase();
  if (s === 'critical' || s === 'high') return 'signal';
  if (s === 'medium' || s === 'low') return 'gate';
  return 'neutral';
}

export default function SecurityTab(): JSX.Element {
  return (
    <section
      aria-label="Security workspace"
      data-testid="security-tab"
      style={{ display: 'grid', gap: 'var(--s-5)' }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          color: 'var(--muted)',
        }}
      >
        <ShieldCheck size={14} aria-hidden />
        <span className="lab">
          Security &amp; observability · live RLS posture &amp; suspicious-activity
          feed
        </span>
      </div>

      <PosturePanel />
      <EventsPanel />
    </section>
  );
}

// ── Panel A — live RLS posture (the real panel) ──────────────────────────────
function PosturePanel(): JSX.Element {
  const [state, setState] = useState<PostureState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/security/posture`)
      .then((res) => {
        if (!res.ok) throw new Error(`posture request failed: ${res.status}`);
        return res.json() as Promise<PostureReport>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === 'loading') {
    return (
      <p
        data-testid="posture-loading"
        className="mono"
        style={{ color: 'var(--muted)' }}
      >
        Loading RLS posture…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <Card>
        <p
          data-testid="posture-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', margin: 0 }}
        >
          Could not load RLS posture: {state.message}
        </p>
      </Card>
    );
  }

  const report = state.data;
  const offending = report.checks.filter((c) => !c.passed);
  // Banner state derives from the report's overall flag AND from any failed
  // check — any check not passed ⇒ red alarm; all passed ⇒ green banner. Fail
  // toward red.
  const green = report.green && offending.length === 0;

  return (
    <section aria-label="RLS posture" data-testid="posture-panel">
      <Card style={{ display: 'grid', gap: 'var(--s-4)' }}>
        <div>
          <div className="lab">Panel A · INV-5 deny-by-default RLS</div>
          <h2
            style={{
              fontSize: 'var(--fs-md)',
              fontWeight: 600,
              letterSpacing: '-0.01em',
              marginTop: 2,
            }}
          >
            Live RLS posture
          </h2>
        </div>

        {green ? (
          <p
            data-testid="posture-green"
            role="status"
            style={{
              display: 'flex',
              gap: 'var(--s-2)',
              alignItems: 'center',
              margin: 0,
              padding: 'var(--s-3)',
              borderRadius: 'var(--r-md)',
              background: 'var(--flow-wash)',
              border: '1px solid var(--flow)',
              color: 'var(--flow-ink)',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <ShieldCheck size={16} aria-hidden style={{ flexShrink: 0 }} />
            <span>
              All public tables are <strong>FORCE</strong>-RLS and null-guarded.
              Deny-by-default is intact.
            </span>
          </p>
        ) : (
          <p
            data-testid="posture-red"
            role="alert"
            style={{
              display: 'flex',
              gap: 'var(--s-2)',
              alignItems: 'flex-start',
              margin: 0,
              padding: 'var(--s-3)',
              borderRadius: 'var(--r-md)',
              background: 'var(--signal-wash)',
              border: '1px solid var(--signal)',
              color: 'var(--signal-ink)',
              fontSize: 'var(--fs-sm)',
              lineHeight: 'var(--lh-body)',
            }}
          >
            <Siren size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />
            <span>
              RLS alarm: <strong>{offending.length}</strong> check
              {offending.length === 1 ? '' : 's'} failed (a table lost FORCE-RLS or
              a policy lost the auth.uid() null guard). Deny-by-default is broken —
              investigate now.
            </span>
          </p>
        )}

        <div
          className="scroll"
          style={{
            overflowX: 'auto',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
          }}
        >
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <thead>
              <tr className="lab">
                <th scope="col" style={headStyle}>
                  Check
                </th>
                <th scope="col" style={headStyle}>
                  Detail
                </th>
                <th scope="col" style={{ ...headStyle, textAlign: 'right' }}>
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {report.checks.map((row) => {
                const StatusIcon = row.passed ? CircleCheck : CircleX;
                return (
                  <tr
                    key={row.name}
                    data-testid={`posture-row-${row.name}`}
                  >
                    <td
                      className="mono"
                      style={{ ...cellStyle, color: 'var(--ink)' }}
                    >
                      {row.name}
                    </td>
                    <td style={{ ...cellStyle, color: 'var(--muted)' }}>
                      {row.detail}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right' }}>
                      <span
                        className="mono"
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 'var(--s-1)',
                          color: row.passed
                            ? 'var(--flow-ink)'
                            : 'var(--signal-ink)',
                        }}
                      >
                        <StatusIcon size={14} aria-hidden />
                        {row.passed ? 'ok' : 'alarm'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </section>
  );
}

// ── Panel B — suspicious-activity feed (simulated in v1) ─────────────────────
function EventsPanel(): JSX.Element {
  const [state, setState] = useState<EventsState>({ status: 'loading' });
  // Locally-acknowledged event ids (the ack affordance is monitoring-only in v1;
  // it flips the row to acknowledged without a live write — INV-9).
  const [acked, setAcked] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/security/events`)
      .then((res) => {
        if (!res.ok) throw new Error(`events request failed: ${res.status}`);
        return res.json() as Promise<SecurityEventsReport>;
      })
      .then((report) => {
        // The backend wraps the feed: { simulated, events: [...] }.
        if (!cancelled) setState({ status: 'ready', data: report.events });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function acknowledge(eventId: string): void {
    setAcked((prev) => {
      const next = new Set(prev);
      next.add(eventId);
      return next;
    });
  }

  return (
    <section aria-label="Suspicious-activity feed" data-testid="events-panel">
      <Card style={{ display: 'grid', gap: 'var(--s-4)' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            gap: 'var(--s-3)',
            flexWrap: 'wrap',
          }}
        >
          <div>
            <div className="lab">Panel B · OWASP-mapped security_event feed</div>
            <h2
              style={{
                fontSize: 'var(--fs-md)',
                fontWeight: 600,
                letterSpacing: '-0.01em',
                marginTop: 2,
              }}
            >
              Suspicious-activity feed
            </h2>
          </div>
          {/* INV-9 honesty: the v1 stream is SIMULATED, not a live drain — label it. */}
          <Chip
            tone="gate"
            title="v1 runs on a simulated suspicious-event stream (not a live drain)"
          >
            simulated
          </Chip>
        </div>

        <p
          data-testid="events-scope-note"
          style={{
            display: 'flex',
            gap: 'var(--s-2)',
            alignItems: 'flex-start',
            margin: 0,
            color: 'var(--muted)',
            fontSize: 'var(--fs-sm)',
            lineHeight: 'var(--lh-body)',
          }}
        >
          <ShieldAlert
            size={14}
            aria-hidden
            style={{ flexShrink: 0, marginTop: 2 }}
          />
          <span>
            Monitoring, not inline blocking. This is a{' '}
            <strong>simulated</strong> suspicious-event stream (v1, INV-9) — each
            row carries its OWASP mapping and an acknowledge action.
          </span>
        </p>

        {state.status === 'loading' && (
          <p
            data-testid="events-loading"
            className="mono"
            style={{ color: 'var(--muted)', margin: 0 }}
          >
            Loading suspicious-activity feed…
          </p>
        )}

        {state.status === 'error' && (
          <p
            data-testid="events-error"
            role="alert"
            style={{ color: 'var(--signal-ink)', margin: 0 }}
          >
            Could not load suspicious-activity feed: {state.message}
          </p>
        )}

        {state.status === 'ready' &&
          (state.data.length === 0 ? (
            <p
              data-testid="events-empty"
              style={{ color: 'var(--muted)', margin: 0, fontSize: 'var(--fs-sm)' }}
            >
              No suspicious events on the simulated stream.
            </p>
          ) : (
            <div
              className="scroll"
              style={{
                overflowX: 'auto',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-md)',
              }}
            >
              <table
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  fontSize: 'var(--fs-sm)',
                }}
              >
                <thead>
                  <tr className="lab">
                    <th scope="col" style={headStyle}>
                      OWASP
                    </th>
                    <th scope="col" style={headStyle}>
                      Signal
                    </th>
                    <th scope="col" style={headStyle}>
                      Severity
                    </th>
                    <th scope="col" style={headStyle}>
                      Detail
                    </th>
                    <th scope="col" style={{ ...headStyle, textAlign: 'right' }}>
                      Action
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.map((ev) => {
                    const isAcked = acked.has(ev.event_id) || ev.acknowledged === true;
                    return (
                      <tr
                        key={ev.event_id}
                        data-testid={`event-row-${ev.event_id}`}
                      >
                        <td
                          data-testid={`event-owasp-${ev.event_id}`}
                          className="mono"
                          style={{ ...cellStyle, color: 'var(--ink)' }}
                        >
                          {ev.owasp}
                        </td>
                        <td className="mono" style={cellStyle}>
                          {ev.signal}
                        </td>
                        <td style={cellStyle}>
                          <Chip tone={severityTone(ev.severity)}>
                            {ev.severity}
                          </Chip>
                        </td>
                        <td style={{ ...cellStyle, color: 'var(--muted)' }}>
                          {ev.detail}
                        </td>
                        <td style={{ ...cellStyle, textAlign: 'right' }}>
                          {isAcked ? (
                            <span
                              data-testid={`event-acked-${ev.event_id}`}
                              className="mono"
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 'var(--s-1)',
                                color: 'var(--flow-ink)',
                              }}
                            >
                              <CircleCheck size={14} aria-hidden />
                              acknowledged
                            </span>
                          ) : (
                            <Button
                              data-testid={`event-ack-${ev.event_id}`}
                              onClick={() => acknowledge(ev.event_id)}
                            >
                              Acknowledge
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
      </Card>
    </section>
  );
}
