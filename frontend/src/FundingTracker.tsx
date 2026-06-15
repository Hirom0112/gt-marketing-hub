import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

// Funding tracker (FR-2.6/2.7). Fetches GET /families/{id}/funding and surfaces
// the funding state, the funding tier (funding_type), the TEFA installment
// schedule, and a tuition LOCK badge. The tuition-unlock gate uses GT-controlled
// signals (INV-10) — `tuition_unlocked` reflects a confirmed first-installment
// receipt; this UI only renders that flag, it never computes it. Self-pay
// families have no TEFA schedule (installments:null) and render no schedule.
// Native fetch only (≤2 runtime deps). Read-only (INV-2). A null funding_type
// renders as a dash placeholder, never literal "null".

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
    return <p data-testid="funding-loading">Loading funding…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="funding-error" role="alert">
        Could not load funding: {state.message}
      </p>
    );
  }

  const funding = state.data;
  const unlocked = funding.tuition_unlocked;

  return (
    <section aria-label="Funding tracker" data-testid="funding-tracker">
      <h2>Funding</h2>
      <dl className="funding-fields">
        <dt>Funding state</dt>
        <dd data-testid="funding-state">{funding.funding_state}</dd>

        <dt>Funding type</dt>
        <dd data-testid="funding-type">{funding.funding_type ?? PLACEHOLDER}</dd>
      </dl>

      <span
        className={`tuition-badge ${unlocked ? 'unlocked' : 'locked'}`}
        data-testid="tuition-badge"
        role="status"
      >
        {unlocked ? 'Tuition unlocked' : 'Tuition locked'}
      </span>

      {funding.installments !== null && (
        <ol className="installment-schedule" data-testid="installment-schedule">
          {funding.installments.map((amount, index) => (
            <li
              // Installment amounts can repeat (25/25/50) — key by position.
              key={`${index}-${amount}`}
              className="installment-row"
              data-testid="installment-row"
            >
              <span className="installment-ordinal">Installment {index + 1}</span>
              <span className="installment-amount">{amount}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
