import {
  type ContactStatus,
  isContactStatus,
  recencyClass,
  recencyLabel,
  recencyTitle,
  recencyVars,
} from './recency';

// A small contact-recency chip (S9 Wave 4). Renders a family's `contact_status`
// as a tinted pill using the `--recency-*` tokens — grey (fresh), red (overdue),
// light-green (followed_up), neutral (closed). It carries a `recency-<status>`
// class so the acceptance test can assert the right tone per status, and a
// `recency-chip` testid so callers can find it. Tolerant of an unknown status
// string (renders quietly as the raw value) so a backend addition never throws.

interface RecencyChipProps {
  // The raw `contact_status` string off the API (narrowed internally).
  status: string;
  // Optional testid override (defaults to "recency-chip").
  testId?: string;
}

export default function RecencyChip({
  status,
  testId = 'recency-chip',
}: RecencyChipProps): JSX.Element {
  if (!isContactStatus(status)) {
    // Unknown status — fail quiet, never throw. Neutral, raw text.
    return (
      <span
        className="mono recency-unknown"
        data-testid={testId}
        data-recency="unknown"
        style={{
          display: 'inline-block',
          fontSize: 'var(--fs-chip)',
          lineHeight: 1.6,
          borderRadius: 'var(--r-xs)',
          padding: '2px 8px',
          color: 'var(--muted)',
          background: 'var(--paper)',
          border: '1px solid var(--line)',
        }}
      >
        {status}
      </span>
    );
  }
  const known: ContactStatus = status;
  const v = recencyVars(known);
  return (
    <span
      title={recencyTitle(known)}
      className={`mono ${recencyClass(known)}`}
      data-testid={testId}
      data-recency={known}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--s-1)',
        fontSize: 'var(--fs-chip)',
        lineHeight: 1.6,
        borderRadius: 'var(--r-xs)',
        padding: '2px 8px',
        whiteSpace: 'nowrap',
        color: v.ink,
        background: v.wash,
        border: `1px solid ${v.solid}`,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: 'var(--r-pill)',
          background: v.solid,
        }}
      />
      {recencyLabel(known)}
    </span>
  );
}
