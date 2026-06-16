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
