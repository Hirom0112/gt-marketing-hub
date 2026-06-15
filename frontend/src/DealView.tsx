import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { apiBaseUrl } from './config';
import { Chip } from './ui';

// Deal view (FR-2.2). Fetches GET /families/{id} and surfaces the deal_view
// summary: stall reason, funding type, MAP signal (map_score), attribution
// source, and CRM seam status. Native fetch only (≤12-dep budget). Read-only
// (INV-2). Interest-stage families have no app_form, so map_score / stall_reason
// can be null — those render as an em-dash placeholder, never literal "null".
// S8 Wave 2 re-skin: matches the reference deal panel — a name header with a
// funding chip, a "Why they haven't converted" stall callout in a signal wash,
// and a two-column field grid built from the Field primitive.

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

// A labelled read-only value whose VALUE element carries the testid the
// acceptance test reads. (The Field primitive doesn't forward a testid, so this
// thin local field mirrors its look while keeping the assertion target.)
function DealField({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId: string;
}): JSX.Element {
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        padding: '6px 9px',
        background: 'var(--surface-2)',
      }}
    >
      <div className="lab">{label}</div>
      <div
        className="mono"
        data-testid={testId}
        style={{ fontSize: 'var(--fs-sm)', marginTop: 2, color: 'var(--ink)' }}
      >
        {value}
      </div>
    </div>
  );
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
    return (
      <p data-testid="deal-view-loading" className="lab">
        Loading deal…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="deal-view-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load deal: {state.message}
      </p>
    );
  }

  const deal = state.data;
  const isTefa = deal.funding_type.toLowerCase() === 'tefa';

  return (
    <section aria-label="Deal view" data-testid="deal-view">
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 'var(--s-2)',
        }}
      >
        <h2
          data-testid="deal-display-name"
          style={{ fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}
        >
          {deal.display_name}
        </h2>
        <Chip tone={isTefa ? 'gate' : 'flow'}>{deal.funding_type}</Chip>
      </div>

      <div
        style={{
          marginTop: 'var(--s-3)',
          padding: 'var(--s-3) var(--s-4)',
          background: 'var(--signal-wash)',
          border: '1px solid var(--signal)',
          borderRadius: 'var(--r-md)',
        }}
      >
        <div
          className="lab"
          style={{
            color: 'var(--signal-ink)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
          }}
        >
          <AlertTriangle size={11} aria-hidden /> Why they haven&apos;t converted
        </div>
        <div
          data-testid="deal-stall-reason"
          style={{
            marginTop: 'var(--s-1)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--signal-ink)',
          }}
        >
          {display(deal.stall_reason)}
        </div>
      </div>

      <dl
        className="deal-fields"
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 'var(--s-2)',
          margin: 'var(--s-3) 0 0',
        }}
      >
        <DealField
          label="Funding type"
          value={deal.funding_type}
          testId="deal-funding-type"
        />
        <DealField
          label="MAP signal"
          value={deal.map_score === null ? PLACEHOLDER : String(deal.map_score)}
          testId="deal-map-score"
        />
        <DealField
          label="Attribution source"
          value={deal.attribution_source}
          testId="deal-attribution"
        />
        <DealField
          label="CRM seam status"
          value={deal.crm_seam_status}
          testId="deal-seam-status"
        />
      </dl>
    </section>
  );
}
