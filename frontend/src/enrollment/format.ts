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
