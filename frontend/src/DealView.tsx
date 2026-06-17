import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, ExternalLink, UploadCloud, XCircle } from 'lucide-react';
import { hubspotContactUrl, hubspotDealUrl, apiFetch } from './config';
import { Button, Chip } from './ui';
import RecencyChip from './enrollment/RecencyChip';
import CompletionRing from './enrollment/CompletionRing';
import SeamDot, { type SeamStatus } from './enrollment/SeamDot';
import { fundingLabel } from './enrollment/format';

// Deal view (FR-2.2). Fetches GET /families/{id} and surfaces the deal_view
// summary: stall reason, funding type, conversion likelihood (DH-1 — REPLACES the
// old MAP signal: band + score + top contributing factor), attribution source, and
// CRM seam status. Native fetch only (≤12-dep budget). Read-only (INV-2). Interest-
// stage families have no app_form, so stall_reason / conversion fields can be null
// — those render as an em-dash placeholder, never literal "null".
// S8 Wave 2 re-skin: matches the reference deal panel — a name header with a
// funding chip, a "Why they haven't converted" stall callout in a signal wash,
// and a two-column field grid built from the Field primitive.

// The deal_view object nested in the FastAPI /families/{id} response.
interface DealViewData {
  display_name: string;
  stall_reason: string | null;
  funding_type: string;
  // DH-1 conversion-likelihood signal — REPLACES the old `map_score` MAP signal:
  // who is most likely to enroll (a [0,1] score + a coarse band) and the top
  // contributing factor, surfaced "to use it to close". Composed in the API layer.
  conversion_score?: number | null;
  conversion_band?: string | null;
  conversion_top_factor_label?: string | null;
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

// GET /crm/status (S14 W4) — the read-only CRM seam state the operator UI reads
// to fail closed on the live-push action (INV-3 pattern; INV-8 kill switch). NO
// secret: `token_configured` is a bool, the token itself is never surfaced.
interface CrmStatus {
  crm_mode: 'simulate' | 'live';
  kill_switch: boolean;
  // What the registry would ACTUALLY select — `simulate` when the kill switch is
  // on even though crm_mode=live, so the indicator reflects real behavior.
  effective_mode: 'simulate' | 'live';
  token_configured: boolean;
  calls_per_run_cap: number;
}

// A response shape only counts as a CrmStatus if it carries the discriminating
// fields — so a stray GET that resolves to some OTHER payload (e.g. a test fetch
// stub that serves the family object for every URL) does NOT masquerade as CRM
// status and silently disable the action. Fail OPEN on an unknown shape: absent /
// malformed status ⇒ no kill-switch banner, the action stays enabled (the kill
// switch only fail-closes on a POSITIVE kill_switch=true from the real endpoint).
function isCrmStatus(value: unknown): value is CrmStatus {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.kill_switch === 'boolean' &&
    typeof v.effective_mode === 'string' &&
    typeof v.crm_mode === 'string'
  );
}

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

// DH-1 conversion-likelihood tile — REPLACES the old "MAP signal" tile in the
// deal-view field grid. Shows the coarse band (High/Med/Low) with a tone-coded
// dot, the [0,1] score as a percentage, and the single top contributing factor
// (e.g. "Funding lined up") so the operator sees who is most likely to enroll and
// WHY — the close signal. Mirrors the DealField look. Read-only (INV-2).
function ConversionField({
  band,
  score,
  topFactorLabel,
}: {
  band: string | null;
  score: number | null;
  topFactorLabel: string | null;
}): JSX.Element {
  // Band → tone color (reuses the signal palette). Unknown band ⇒ neutral ink.
  const tone =
    band === 'High'
      ? 'var(--ok, #1a7f37)'
      : band === 'Med'
        ? 'var(--warn, #9a6700)'
        : band === 'Low'
          ? 'var(--signal-ink, #6b7280)'
          : 'var(--ink)';
  const pct = score === null ? null : Math.round(score * 100);
  const headline =
    band === null ? PLACEHOLDER : pct === null ? band : `${band} · ${pct}%`;
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        padding: '6px 9px',
        background: 'var(--surface-2)',
      }}
    >
      <div className="lab">Conversion likelihood</div>
      <div
        className="mono"
        data-testid="deal-conversion"
        style={{
          fontSize: 'var(--fs-sm)',
          marginTop: 2,
          color: tone,
          fontWeight: 600,
        }}
      >
        {headline}
      </div>
      {topFactorLabel != null && (
        <div
          data-testid="deal-conversion-factor"
          style={{
            fontSize: 'var(--fs-xs, 11px)',
            marginTop: 2,
            color: 'var(--signal-ink, #6b7280)',
          }}
        >
          Top factor: {topFactorLabel}
        </div>
      )}
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
  // The CRM seam state (S14 W4) — null until /crm/status resolves (or if it is
  // unavailable / an unknown shape, in which case we fail OPEN: no banner, the
  // live-push stays enabled; the kill switch only blocks on a positive true).
  const [crm, setCrm] = useState<CrmStatus | null>(null);

  function seedToHubSpot(): void {
    setSeed({ status: 'seeding' });
    apiFetch(`/enrollment/families/${familyId}/seed`, {
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
    // Surface the CRM seam state so the live-push action fails closed when the
    // kill switch is on (S14 W4; INV-3/INV-8). Fail OPEN on any error / unknown
    // shape — a missing status never silently disables the action.
    let cancelled = false;
    apiFetch(`/crm/status`)
      .then((res) => (res.ok ? (res.json() as Promise<unknown>) : null))
      .then((data) => {
        if (!cancelled) setCrm(isCrmStatus(data) ? data : null);
      })
      .catch(() => {
        if (!cancelled) setCrm(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/families/${familyId}`)
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
  // Fail closed (INV-8): the kill switch disables the live-push action. Only a
  // POSITIVE kill_switch=true from /crm/status blocks — an absent / unknown status
  // leaves the action enabled (fail open on missing state, never silently off).
  const killSwitchOn = crm?.kill_switch === true;
  // A-23 — show the operator-facing label ("Texas voucher" / "Self-pay"), never
  // the raw enum. Voucher tiers (any TEFA) take the gate tone, self-pay the flow.
  const isTefa = deal.funding_type.toLowerCase().includes('tefa');
  const fundingDisplay = fundingLabel(deal.funding_type);

  // "Where they left off" — show the stage they're ACTUALLY stuck in, not the
  // always-100% application %. The APPLICATION (Interest form) and the 6-form
  // ENROLLMENT packet are two distinct stages: once the application is submitted
  // (completion ≥ 100) the family is in the packet, so the ring + line track FORM
  // progress (e.g. "2 of 6") and "stuck on" names the next unsigned form.
  // Otherwise they're still in the application: the ring + line track the app %.
  const completion = deal.completion_pct;
  const appSubmitted = completion != null && completion >= 100;
  const inEnrollment = appSubmitted && deal.forms_total != null;
  const enrollPct =
    inEnrollment && deal.forms_total
      ? Math.round(((deal.forms_signed ?? 0) / deal.forms_total) * 100)
      : 0;
  const dropoffRingPct = inEnrollment ? enrollPct : (completion ?? 0);
  const showDropoffRing = completion != null || inEnrollment;
  // The "stuck on <form>" signal only makes sense once they're IN the packet —
  // a pre-submit family is stuck in the application, not on form #1.
  const stuckForm =
    inEnrollment && deal.next_unsigned_form != null
      ? deal.next_unsigned_form.replace(/_/g, ' ')
      : null;

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
            {showDropoffRing && <CompletionRing pct={dropoffRingPct} />}
            <div style={{ minWidth: 0 }}>
              <div
                data-testid="deal-completion"
                className="mono"
                style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
              >
                {inEnrollment
                  ? `Application ✓ submitted · Enrollment ${deal.forms_signed ?? 0} of ${deal.forms_total} forms`
                  : completion != null
                    ? `${completion}% application complete`
                    : PLACEHOLDER}
              </div>
              {stuckForm != null && (
                <div
                  data-testid="deal-next-form"
                  style={{
                    marginTop: 'var(--s-1)',
                    fontSize: 'var(--fs-sm)',
                    color: 'var(--signal-ink)',
                  }}
                >
                  Stuck on: {stuckForm}
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
        <ConversionField
          band={deal.conversion_band ?? null}
          score={deal.conversion_score ?? null}
          topFactorLabel={deal.conversion_top_factor_label ?? null}
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

      {/* CRM seam indicator (S14 W4) — surfaces the HubSpot kill switch / CRM mode
          so the operator can SEE the seam state and the live-push fails closed when
          the kill switch is on (INV-3 pattern; INV-8). NO secret is shown. */}
      <CrmSeamBadge crm={crm} />

      {/* "Seed to HubSpot" (S10 W3) — push this synthetic family live into the
          real portal, then surface the captured Deal + Contact ids as deep links.
          The deterministic backend route owns the write (INV-2); this button only
          triggers it and renders the proof. The live-push FAILS CLOSED when the
          HubSpot kill switch is on (S14 W4; INV-8) — disabled with a reason. */}
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
          disabled={seed.status === 'seeding' || killSwitchOn}
          title={
            killSwitchOn
              ? 'HubSpot kill switch is ON — live sync is disabled (INV-8). Clear HUBSPOT_KILL_SWITCH to re-enable.'
              : undefined
          }
        >
          {seed.status === 'seeding' ? 'Seeding…' : 'Seed to HubSpot'}
        </Button>
        {killSwitchOn && (
          <span
            data-testid="seed-kill-switch-note"
            role="status"
            style={{ fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
          >
            Kill switch ON — live sync disabled
          </span>
        )}
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

// The CRM seam indicator (S14 W4). Surfaces the effective HubSpot seam so the
// operator SEES the state and the live-push fails closed when the kill switch is
// on (INV-3 pattern; INV-8). NO secret: it reads only the booleans/mode from
// /crm/status. Renders nothing until the status resolves (fail open on absent).
//   - kill switch ON ⇒ a signal-tone "Kill switch ON — live sync disabled" chip.
//   - effective live ⇒ a flow-tone "CRM: LIVE" chip (writes land in the portal).
//   - otherwise      ⇒ a neutral "CRM: Simulated" chip (recorded, never sent).
function CrmSeamBadge({ crm }: { crm: CrmStatus | null }): JSX.Element | null {
  if (crm === null) return null;
  if (crm.kill_switch) {
    return (
      <div data-testid="crm-seam-badge" style={{ marginTop: 'var(--s-3)' }}>
        <span data-testid="crm-seam-state" data-crm-effective={crm.effective_mode}>
          <Chip tone="signal" title="HubSpot kill switch is ON — live writes are disabled (INV-8).">
            Kill switch ON — live sync disabled
          </Chip>
        </span>
      </div>
    );
  }
  const live = crm.effective_mode === 'live';
  return (
    <div data-testid="crm-seam-badge" style={{ marginTop: 'var(--s-3)' }}>
      <span data-testid="crm-seam-state" data-crm-effective={crm.effective_mode}>
        <Chip
          tone={live ? 'flow' : 'neutral'}
          title={
            live
              ? 'CRM seam is LIVE — synthetic pushes land in the real HubSpot portal.'
              : 'CRM seam is simulated — pushes are recorded, never sent (INV-9).'
          }
        >
          {live ? 'CRM: LIVE' : 'CRM: Simulated'}
        </Chip>
      </span>
    </div>
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
