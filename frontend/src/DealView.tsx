import { useEffect, useState } from 'react';
import { apiBaseUrl } from './config';

// Deal view (FR-2.2). Fetches GET /families/{id} and surfaces the deal_view
// summary: stall reason, funding type, MAP signal (map_score), attribution
// source, and CRM seam status. Native fetch only (≤12-dep budget). Read-only
// (INV-2). Interest-stage families have no app_form, so map_score / stall_reason
// can be null — those render as an em-dash placeholder, never literal "null".

// The deal_view object nested in the FastAPI /families/{id} response.
interface DealViewData {
  display_name: string;
  stall_reason: string | null;
  funding_type: string;
  map_score: number | null;
  attribution_source: string;
  crm_seam_status: string;
}

// We only read deal_view; the rest of the family response is ignored here.
interface FamilyResponse {
  deal_view: DealViewData;
}

interface DealViewProps {
  familyId: string;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: DealViewData };

const PLACEHOLDER = '—';

function display(value: string | null): string {
  return value ?? PLACEHOLDER;
}

export default function DealView({ familyId }: DealViewProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/families/${familyId}`)
      .then((res) => {
        if (!res.ok) throw new Error(`family request failed: ${res.status}`);
        return res.json() as Promise<FamilyResponse>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data: data.deal_view });
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
    return <p data-testid="deal-view-loading">Loading deal…</p>;
  }
  if (state.status === 'error') {
    return (
      <p data-testid="deal-view-error" role="alert">
        Could not load deal: {state.message}
      </p>
    );
  }

  const deal = state.data;

  return (
    <section aria-label="Deal view" data-testid="deal-view">
      <h2 data-testid="deal-display-name">{deal.display_name}</h2>
      <dl className="deal-fields">
        <dt>Stall reason</dt>
        <dd data-testid="deal-stall-reason">{display(deal.stall_reason)}</dd>

        <dt>Funding type</dt>
        <dd data-testid="deal-funding-type">{deal.funding_type}</dd>

        <dt>MAP signal</dt>
        <dd data-testid="deal-map-score">
          {deal.map_score === null ? PLACEHOLDER : String(deal.map_score)}
        </dd>

        <dt>Attribution source</dt>
        <dd data-testid="deal-attribution">{deal.attribution_source}</dd>

        <dt>CRM seam status</dt>
        <dd data-testid="deal-seam-status">{deal.crm_seam_status}</dd>
      </dl>
    </section>
  );
}
