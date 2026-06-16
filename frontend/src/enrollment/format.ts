// Shared money formatting for the recovery loop (S12 W4). One canonical home so
// the calendar chip, heat cell at-risk, drill rows, situation strip, and toasts
// all read identically — the components themselves never reinvent it.

// Full USD, no cents: 10474 → "$10,474" (drill rows, situation $, toasts).
export function fmtUSD(value: number): string {
  if (!Number.isFinite(value)) return '$0';
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
}

// Short UTC day label: "2026-06-13T..." → "Jun 13" (drill stall-date column).
// Empty / unparseable input → "—" so a missing stall date renders cleanly.
export function fmtDay(iso: string): string {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '—';
  return new Date(ms).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

// Whole days elapsed since an ISO instant, as a compact mono age: "12d", "0d".
// Empty / unparseable / future input → "—". Day-bucketed in UTC so it matches the
// stall-date column (the age cell differentiates same-recency rows by how long
// they've sat, not an identical word).
export function fmtAge(iso: string, now: number = Date.now()): string {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return '—';
  const start = (t: number): number => {
    const d = new Date(t);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  };
  const days = Math.round((start(now) - start(ms)) / 86_400_000);
  if (days < 0) return '—';
  return `${days}d`;
}

// Recoverability / likelihood as a whole percent: 0.84 → "84%", clamped [0,1].
// The triage row's HERO — "how likely can I save this one" (A-23, funnel depth +
// recency + responsiveness). Non-finite → "—".
export function fmtPct(value: number): string {
  if (!Number.isFinite(value)) return '—';
  const clamped = Math.max(0, Math.min(1, value));
  return `${Math.round(clamped * 100)}%`;
}

// Child count as a compact label: 1 → "1 child", 3 → "3 kids" (A-23 — the value
// driver, since every targeted family pays the same per-child tuition). 0/neg → "".
export function fmtKids(n: number): string {
  if (!Number.isFinite(n) || n < 1) return '';
  return n === 1 ? '1 child' : `${Math.round(n)} kids`;
}

// Funding tier → the operator-facing label (A-23). Every targeted family is
// full-pay: a Texas voucher (TEFA standard) or self-pay. Maps the raw enum to
// plain words; an unknown / null tier → "—".
export function fundingLabel(fundingType: string | null | undefined): string {
  switch ((fundingType ?? '').toLowerCase()) {
    case 'tefa_standard':
      return 'Texas voucher';
    case 'self_pay':
      return 'Self-pay';
    case 'tefa_disability':
      return 'Voucher (IEP)';
    case 'tefa_homeschool':
      return 'Voucher (homeschool)';
    default:
      return '—';
  }
}

// snake_case → Title Case for the apply-flow drop-off telemetry (S15 W2). A
// step/form_key/field_key segment ("data_collection_consent") humanizes to
// "Data Collection Consent". Null/empty segments are dropped by the caller, so
// this never has to render "Null". Metadata only — these are STRUCTURAL ids
// (step/form/field), never a typed value or child key (INV-1/INV-6).
export function humanizeSegment(value: string | null | undefined): string {
  if (value == null) return '';
  return value
    .split(/[_\s]+/)
    .filter((word) => word.length > 0)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

// Join the present drop-off segments into the operator headline, omitting null
// segments: (enroll, data_collection_consent, signature) → "Enroll · Data
// Collection Consent · Signature"; (apply, null, null) → "Apply".
export function dropOffPath(
  step: string,
  formKey?: string | null,
  fieldKey?: string | null,
): string {
  return [step, formKey, fieldKey]
    .map((seg) => humanizeSegment(seg))
    .filter((seg) => seg.length > 0)
    .join(' · ');
}

// Compact: 10474 → "$10k", 2618.5 → "$2.6k", 900 → "$900" (chips, heat at-risk).
export function shortDollars(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '$0';
  if (value >= 1000) {
    const k = value / 1000;
    const rounded = k >= 10 ? Math.round(k) : Math.round(k * 10) / 10;
    return `$${rounded}k`;
  }
  return `$${Math.round(value)}`;
}
