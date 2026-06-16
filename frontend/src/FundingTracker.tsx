import { useEffect, useState } from 'react';
import { Lock, LockOpen } from 'lucide-react';
import { apiBaseUrl } from './config';
import { Card } from './ui';
import { fundingLabel } from './enrollment/format';

// Funding tracker (FR-2.6/2.7). Fetches GET /families/{id}/funding and surfaces
// the funding state, the funding tier (funding_type), the TEFA installment
// schedule, and a tuition LOCK badge. The tuition-unlock gate uses GT-controlled
// signals (INV-10) — `tuition_unlocked` reflects a confirmed first-installment
// receipt; this UI only renders that flag, it never computes it. Self-pay
// families have no TEFA schedule (installments:null) and render no schedule.
// Native fetch only (≤2 runtime deps). Read-only (INV-2). A null funding_type
// renders as a dash placeholder, never literal "null". S8 Wave 2 re-skin: gold
// (gate) funding tone, a lock badge, and an installment ladder of inset rows.

// GET /families/{id}/funding response (backend app/api/schemas.py).
interface FundingView {
  family_id: string;
  funding_state: string;
  funding_type: string | null;
  installments: string[] | null; // TEFA amounts as strings; null for self-pay
  tuition_unlocked: boolean;
}

interface FundingTrackerProps {
  familyId: string;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: FundingView };

const PLACEHOLDER = '—';

export default function FundingTracker({
  familyId,
}: FundingTrackerProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/families/${familyId}/funding`)
      .then((res) => {
        if (!res.ok) throw new Error(`funding request failed: ${res.status}`);
        return res.json() as Promise<FundingView>;
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
  }, [familyId]);

  if (state.status === 'loading') {
    return (
      <p data-testid="funding-loading" className="lab">
        Loading funding…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="funding-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load funding: {state.message}
      </p>
    );
  }

  const funding = state.data;
  const unlocked = funding.tuition_unlocked;

  return (
    <section aria-label="Funding tracker" data-testid="funding-tracker">
      <div
        className="lab"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          marginBottom: 'var(--s-2)',
        }}
      >
        {unlocked ? <LockOpen size={11} aria-hidden /> : <Lock size={11} aria-hidden />}{' '}
        Funding &amp; TEFA gate
      </div>
      <h2 style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
        Funding
      </h2>

      <Card>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: 'var(--s-3)',
          }}
        >
          <dl className="funding-fields" style={{ margin: 0 }}>
            <dt className="lab">Funding state</dt>
            <dd
              data-testid="funding-state"
              className="mono"
              style={{
                margin: '2px 0 var(--s-2)',
                fontSize: 'var(--fs-sm)',
                color: 'var(--ink)',
              }}
            >
              {funding.funding_state}
            </dd>

            <dt className="lab">Funding type</dt>
            <dd
              data-testid="funding-type"
              className="mono"
              style={{ margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
            >
              {funding.funding_type ? fundingLabel(funding.funding_type) : PLACEHOLDER}
            </dd>
          </dl>

          <span
            className={`tuition-badge mono ${unlocked ? 'unlocked' : 'locked'}`}
            data-testid="tuition-badge"
            role="status"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--s-1)',
              flexShrink: 0,
              fontSize: 'var(--fs-chip)',
              padding: '4px 9px',
              borderRadius: 'var(--r-xs)',
              whiteSpace: 'nowrap',
              color: unlocked ? 'var(--flow-ink)' : 'var(--gate-ink)',
              background: unlocked ? 'var(--flow-wash)' : 'var(--gate-wash)',
              border: `1px solid ${unlocked ? 'var(--flow)' : 'var(--gate)'}`,
            }}
          >
            {unlocked ? <LockOpen size={11} aria-hidden /> : <Lock size={11} aria-hidden />}
            {unlocked ? 'Tuition unlocked' : 'Tuition locked'}
          </span>
        </div>

        {funding.installments !== null && (
          <>
            {/* The schedule is PROJECTED until funding is actually awarded +
                disbursed (tuition still locked) — it's what they WOULD receive,
                not money in hand. Labelled so it never reads as "voucher
                connected" next to funding_state=none (INV-10: GT-controlled
                signals drive the real state). */}
            <div
              className="lab"
              data-testid="installment-caption"
              style={{
                marginTop: 'var(--s-3)',
                color: unlocked ? 'var(--flow-ink)' : 'var(--muted)',
              }}
            >
              {unlocked
                ? 'TEFA installment schedule'
                : 'Projected schedule — pending award + first installment'}
            </div>
            <ol
              className="installment-schedule"
              data-testid="installment-schedule"
              style={{
                listStyle: 'none',
                margin: 'var(--s-2) 0 0',
                padding: 0,
                display: 'grid',
                gap: 'var(--s-2)',
                opacity: unlocked ? 1 : 0.6,
              }}
            >
            {funding.installments.map((amount, index) => (
              <li
                // Installment amounts can repeat (25/25/50) — key by position.
                key={`${index}-${amount}`}
                className="installment-row"
                data-testid="installment-row"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '6px 10px',
                  borderRadius: 'var(--r-sm)',
                  background: 'var(--gate-wash)',
                  border: '1px solid var(--gate)',
                }}
              >
                <span className="installment-ordinal lab" style={{ color: 'var(--gate-ink)' }}>
                  Installment {index + 1}
                </span>
                <span
                  className="installment-amount mono"
                  style={{
                    fontSize: 'var(--fs-sm)',
                    fontWeight: 600,
                    color: 'var(--gate-ink)',
                  }}
                >
                  {amount}
                </span>
              </li>
            ))}
            </ol>
          </>
        )}
      </Card>
    </section>
  );
}
