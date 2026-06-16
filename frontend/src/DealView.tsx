import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, ExternalLink, UploadCloud, XCircle } from 'lucide-react';
import {
  apiBaseUrl,
  hubspotContactUrl,
  hubspotDealUrl,
} from './config';
import { Button, Chip } from './ui';
import RecencyChip from './enrollment/RecencyChip';
import CompletionRing from './enrollment/CompletionRing';
import SeamDot, { type SeamStatus } from './enrollment/SeamDot';
import { fundingLabel } from './enrollment/format';

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
  // S9 Wave 4 drop-off + recency projection (api-composed; may be null for an
  // interest-stage family with no app_form / no recency yet).
  completion_pct?: number | null;
  forms_signed?: number | null;
  forms_total?: number | null;
  next_unsigned_form?: string | null;
  contact_status?: string | null;
  // S12 W1 — the derived recovery state (A-19), composed in the API layer.
  recovery_state?: 'stalled' | 'working' | 'recovered' | 'dismissed' | null;
}

// We only read deal_view; the rest of the family response is ignored here.
interface FamilyResponse {
  deal_view: DealViewData;
}

// POST /enrollment/families/{id}/seed response (S10 W3). The live HubSpot Deal +
// Contact ids are the proof-of-capture the capture panel deep-links; seam_status
// flips to `synced` once the push lands.
interface SeedResponse {
  family_id: string;
  simulated: boolean;
  deal_id: string;
  contact_id: string | null;
  stage: string;
  seam_status: string;
}

type SeedState =
  | { status: 'idle' }
  | { status: 'seeding' }
  | { status: 'error'; message: string }
  | { status: 'captured'; data: SeedResponse };

interface DealViewProps {
  familyId: string;
  // Bump to force a re-fetch (e.g. after an approved follow-up updates recency).
  refreshKey?: number;
  // The audited dismiss reasons (S12 W4; A-19) for the "Dismiss this family"
  // picker. The dismiss WRITE is owned by the parent (one route) — DealView only
  // offers the reason and calls back; it never writes (INV-2).
  dismissReasons?: readonly string[];
  onDismiss?: (familyId: string, reason: string) => void;
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

// The CRM-seam status as a CLEAN NAMED CHIP with a SeamDot (S12 W4) — never a raw
// UUID (A-17). The seam is the forward step's state: synced (in HubSpot / flow),
// conflict (needs a human / signal), unsynced or anything else (not yet pushed /
// neutral). The `deal-seam-status` testid carries the named status the suite reads.
function SeamField({ status }: { status: string }): JSX.Element {
  const normalized = status.toLowerCase();
  const tone: 'flow' | 'signal' | 'neutral' =
    normalized === 'synced'
      ? 'flow'
      : normalized === 'conflict'
        ? 'signal'
        : 'neutral';
  const dotStatus: SeamStatus =
    normalized === 'synced'
      ? 'synced'
      : normalized === 'conflict'
        ? 'conflict'
        : 'unsynced';
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        padding: '6px 9px',
        background: 'var(--surface-2)',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <div className="lab">HubSpot seam</div>
      <span
        data-testid="deal-seam-status"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-2)' }}
      >
        <SeamDot status={dotStatus} />
        <Chip tone={tone}>{status}</Chip>
      </span>
    </div>
  );
}

// The recovery-state tag in the panel header (S12 W4; A-19). recovered/working
// read teal (forward progress), dismissed neutral, stalled signal.
function RecoveryTag({ state }: { state: string }): JSX.Element {
  const tone: 'flow' | 'signal' | 'neutral' =
    state === 'recovered' || state === 'working'
      ? 'flow'
      : state === 'stalled'
        ? 'signal'
        : 'neutral';
  const label =
    state === 'working'
      ? 'Working'
      : state === 'recovered'
        ? 'Recovered'
        : state === 'dismissed'
          ? 'Dismissed'
          : 'Stalled';
  return (
    <span data-testid="deal-recovery-state">
      <Chip tone={tone}>{label}</Chip>
    </span>
  );
}

export default function DealView({
  familyId,
  refreshKey,
  dismissReasons,
  onDismiss,
}: DealViewProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [seed, setSeed] = useState<SeedState>({ status: 'idle' });
  // The "Dismiss this family" reason picker (closed by default).
  const [dismissing, setDismissing] = useState(false);

  function seedToHubSpot(): void {
    setSeed({ status: 'seeding' });
    fetch(`${apiBaseUrl}/enrollment/families/${familyId}/seed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`seed request failed: ${res.status}`);
        return res.json() as Promise<SeedResponse>;
      })
      .then((data) => setSeed({ status: 'captured', data }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setSeed({ status: 'error', message });
      });
  }

  useEffect(() => {
    // A new family resets the capture state (no stale ids across selections).
    setSeed({ status: 'idle' });
    setDismissing(false);
  }, [familyId]);

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
  }, [familyId, refreshKey]);

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
  // A-23 — show the operator-facing label ("Texas voucher" / "Self-pay"), never
  // the raw enum. Voucher tiers (any TEFA) take the gate tone, self-pay the flow.
  const isTefa = deal.funding_type.toLowerCase().includes('tefa');
  const fundingDisplay = fundingLabel(deal.funding_type);

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
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          {deal.recovery_state != null && (
            <RecoveryTag state={deal.recovery_state} />
          )}
          {deal.contact_status != null && (
            <RecencyChip status={deal.contact_status} testId="deal-recency" />
          )}
          <Chip tone={isTefa ? 'gate' : 'flow'}>{fundingDisplay}</Chip>
        </div>
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

      {/* Where they left off — application completion + form progress (FR-2.2;
          S9 Wave 4). Rendered only when the family has application/form data. */}
      {(deal.completion_pct != null || deal.forms_total != null) && (
        <div
          data-testid="deal-dropoff"
          style={{
            marginTop: 'var(--s-3)',
            padding: 'var(--s-3) var(--s-4)',
            background: 'var(--surface-2)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
          }}
        >
          <div className="lab">Where they left off</div>
          <div
            style={{
              marginTop: 'var(--s-2)',
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--s-3)',
            }}
          >
            {deal.completion_pct != null && (
              <CompletionRing pct={deal.completion_pct} />
            )}
            <div style={{ minWidth: 0 }}>
              <div
                data-testid="deal-completion"
                className="mono"
                style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
              >
                {deal.completion_pct == null
                  ? PLACEHOLDER
                  : `${deal.completion_pct}% application complete`}
                {deal.forms_total != null
                  ? ` · ${deal.forms_signed ?? 0}/${deal.forms_total} forms signed`
                  : ''}
              </div>
              {deal.next_unsigned_form != null && (
                <div
                  data-testid="deal-next-form"
                  style={{
                    marginTop: 'var(--s-1)',
                    fontSize: 'var(--fs-sm)',
                    color: 'var(--signal-ink)',
                  }}
                >
                  Stuck on: {deal.next_unsigned_form}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

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
          value={fundingDisplay}
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
        <SeamField
          status={
            seed.status === 'captured'
              ? seed.data.seam_status
              : deal.crm_seam_status
          }
        />
      </dl>

      {/* "Seed to HubSpot" (S10 W3) — push this synthetic family live into the
          real portal, then surface the captured Deal + Contact ids as deep links.
          The deterministic backend route owns the write (INV-2); this button only
          triggers it and renders the proof. */}
      <div
        style={{
          marginTop: 'var(--s-3)',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          flexWrap: 'wrap',
        }}
      >
        <Button
          variant="primary"
          icon={UploadCloud}
          data-testid="seed-hubspot"
          onClick={seedToHubSpot}
          disabled={seed.status === 'seeding'}
        >
          {seed.status === 'seeding' ? 'Seeding…' : 'Seed to HubSpot'}
        </Button>
        {seed.status === 'error' && (
          <span
            data-testid="seed-error"
            role="alert"
            style={{ fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
          >
            {seed.message}
          </span>
        )}
        {/* Dismiss this family (S12 W4; A-19) — an audited remove from the active
            board. The WRITE is the parent's (one route); this only opens the
            reason picker and calls back. Hidden once already dismissed. */}
        {onDismiss !== undefined &&
          dismissReasons !== undefined &&
          deal.recovery_state !== 'dismissed' &&
          deal.recovery_state !== 'recovered' && (
            <Button
              icon={XCircle}
              data-testid="dismiss-family-start"
              onClick={() => setDismissing((on) => !on)}
            >
              Dismiss this family…
            </Button>
          )}
      </div>

      {dismissing && onDismiss !== undefined && dismissReasons !== undefined && (
        <div
          data-testid="dismiss-family-reasons"
          style={{
            marginTop: 'var(--s-2)',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 'var(--s-2)',
            alignItems: 'center',
            padding: 'var(--s-2) var(--s-3)',
            background: 'var(--surface-2)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
          }}
        >
          <span className="lab">reason:</span>
          {dismissReasons.map((r) => (
            <button
              key={r}
              type="button"
              data-testid={`dismiss-family-reason-${r}`}
              onClick={() => {
                onDismiss(familyId, r);
                setDismissing(false);
              }}
              style={{
                border: '1px solid var(--line)',
                background: 'var(--surface)',
                fontSize: 11.5,
                fontWeight: 600,
                padding: '5px 10px',
                borderRadius: 'var(--r-pill)',
                color: 'var(--ink)',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {r}
            </button>
          ))}
          <button
            type="button"
            data-testid="dismiss-family-cancel"
            onClick={() => setDismissing(false)}
            style={{
              border: '1px solid transparent',
              background: 'transparent',
              color: 'var(--muted)',
              fontSize: 11.5,
              fontWeight: 600,
              padding: '5px 10px',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            cancel
          </button>
        </div>
      )}

      {seed.status === 'captured' && (
        <CapturePanel data={seed.data} />
      )}
    </section>
  );
}

// The proof-of-capture panel (S10 W3). Renders the live HubSpot Deal + Contact
// ids returned by the seed route as click-through deep links into the real
// portal, plus the flipped seam badge — "✓ captured in HubSpot."
function CapturePanel({ data }: { data: SeedResponse }): JSX.Element {
  return (
    <div
      data-testid="capture-panel"
      role="status"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3) var(--s-4)',
        background: 'var(--flow-wash)',
        border: '1px solid var(--flow)',
        borderRadius: 'var(--r-md)',
      }}
    >
      <div
        className="lab"
        style={{
          color: 'var(--flow-ink)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
        }}
      >
        <CheckCircle2 size={11} aria-hidden /> Captured in HubSpot
        {data.simulated ? ' (simulated)' : ''}
      </div>
      <div
        style={{
          marginTop: 'var(--s-2)',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--s-3)',
        }}
      >
        <CaptureLink
          label="Deal"
          href={hubspotDealUrl(data.deal_id)}
          id={data.deal_id}
          testId="capture-deal-link"
        />
        {data.contact_id != null && (
          <CaptureLink
            label="Contact"
            href={hubspotContactUrl(data.contact_id)}
            id={data.contact_id}
            testId="capture-contact-link"
          />
        )}
        <div>
          <div className="lab">Seam</div>
          <div
            data-testid="capture-seam-status"
            className="mono"
            style={{ fontSize: 'var(--fs-sm)', color: 'var(--flow-ink)', marginTop: 2 }}
          >
            {data.seam_status}
          </div>
        </div>
      </div>
    </div>
  );
}

// One labelled deep link into a live HubSpot record.
function CaptureLink({
  label,
  href,
  id,
  testId,
}: {
  label: string;
  href: string;
  id: string;
  testId: string;
}): JSX.Element {
  return (
    <div>
      <div className="lab">{label}</div>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        data-testid={testId}
        className="mono"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          fontSize: 'var(--fs-sm)',
          color: 'var(--flow-ink)',
          marginTop: 2,
        }}
      >
        {id}
        <ExternalLink size={11} aria-hidden />
      </a>
    </div>
  );
}
