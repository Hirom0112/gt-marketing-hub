// CalendarChip (S12 W3) — the reusable two-line family chip the calendar lays
// out on a stall day (exported now so the loop wave can compose it). Two lines:
//   · name line (bold), and
//   · a meta row: the mono $value on the left, a 3-segment score bar on the right.
// A 3px left border carries the recency tint (the --recency-*-solid token). The
// score-bar segment count is derived from the score: ≥.85 → 3, ≥.65 → 2, else 1;
// "on" segments fill --flow, the rest sit on --line. Selecting calls onSelect.

import { isContactStatus, recencyClass, recencyTitle } from './recency';

interface CalendarChipProps {
  familyId: string;
  // Display name (e.g. "The Alvarez Family").
  name: string;
  // Pre-formatted mono value (e.g. "$10k") — caller owns money formatting.
  value: string;
  // Score 0–1 — drives the 3-segment bar.
  score: number;
  // Raw contact_status string (narrowed internally for the recency tint).
  contactStatus: string;
  // Whether this chip is the active family (ring highlight).
  active?: boolean;
  onSelect?: (familyId: string) => void;
}

// Pure helper: how many of the 3 score-bar segments are "on" for a score.
// ≥.85 → 3, ≥.65 → 2, else 1. Exported so the test can assert thresholds and so
// the loop wave can reuse the same derivation alongside the chip it belongs to.
// eslint-disable-next-line react-refresh/only-export-components
export function scoreSegments(score: number): number {
  if (score >= 0.85) return 3;
  if (score >= 0.65) return 2;
  return 1;
}

export default function CalendarChip({
  familyId,
  name,
  value,
  score,
  contactStatus,
  active = false,
  onSelect,
}: CalendarChipProps): JSX.Element {
  const known = isContactStatus(contactStatus);
  const recencyCls = known ? recencyClass(contactStatus) : 'recency-unknown';
  const borderColor = known
    ? `var(--recency-${contactStatus}-solid)`
    : 'var(--line-strong)';
  const on = scoreSegments(score);

  return (
    <button
      type="button"
      data-testid={`calendar-chip-${familyId}`}
      className={`mono-host ${recencyCls}`}
      title={known ? recencyTitle(contactStatus) : name}
      onClick={() => onSelect?.(familyId)}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 3,
        width: '100%',
        textAlign: 'left',
        font: 'inherit',
        borderRadius: 'var(--r-sm)',
        border: '1px solid var(--line)',
        borderLeft: `3px solid ${borderColor}`,
        background: 'var(--surface)',
        padding: '4px 6px 5px',
        cursor: 'pointer',
        boxShadow: active ? '0 0 0 2px var(--flow)' : 'none',
        transition:
          'transform var(--dur) var(--ease), box-shadow var(--dur) var(--ease)',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = 'translateY(-1px)';
        if (!active) e.currentTarget.style.boxShadow = 'var(--shadow-sm)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = 'translateY(0)';
        e.currentTarget.style.boxShadow = active
          ? '0 0 0 2px var(--flow)'
          : 'none';
      }}
    >
      <span
        style={{
          fontSize: 11.5,
          fontWeight: 700,
          letterSpacing: '-0.01em',
          lineHeight: 1.15,
        }}
      >
        {name}
      </span>
      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--s-2)',
        }}
      >
        <span
          className="mono"
          data-testid={`calendar-chip-value-${familyId}`}
          style={{ fontSize: 10, fontWeight: 600, color: 'var(--ink-soft)' }}
        >
          {value}
        </span>
        <span
          data-testid={`calendar-chip-bar-${familyId}`}
          data-segments={on}
          style={{ display: 'flex', gap: 2 }}
        >
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              style={{
                width: 8,
                height: 3,
                borderRadius: 2,
                background: i < on ? 'var(--flow)' : 'var(--line)',
              }}
            />
          ))}
        </span>
      </span>
    </button>
  );
}
