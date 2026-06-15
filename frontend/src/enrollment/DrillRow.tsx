// DrillRow (S12 W3) — one dense tabular row in the ranked drill / show-all list.
// Six columns on a fixed grid: a teal-when-on checkbox, a zero-padded rank, the
// family name + stuck-step subline, the mono recovery value (right-aligned), the
// recency Chip (REUSED primitive, tone from recencyTone), and the mono score.
// Selected rows wash teal (--flow-wash); hover lifts to --surface-2. The grid
// template is shared with the header via DRILL_GRID so the two stay aligned.

import type { CSSProperties } from 'react';
import { Chip } from '../ui';
import { isContactStatus, recencyLabel, recencyTone } from './recency';

// The shared 6-col grid template — header + every row read from this one place.
export const DRILL_GRID = '26px 22px 1fr 96px 64px 52px';

interface DrillRowProps {
  // Stable id (used by selection callbacks + testids).
  familyId: string;
  // 1-based rank within the current sort (rendered zero-padded).
  rank: number;
  // Display name (e.g. "The Alvarez Family").
  name: string;
  // The stuck step, human-readable (e.g. "enrollment agreement").
  stuckStep: string;
  // Pre-formatted mono value (e.g. "$10,474") — caller owns money formatting.
  value: string;
  // Pre-formatted mono score (e.g. "0.91").
  score: string;
  // Raw contact_status string (narrowed internally for the recency Chip tone).
  contactStatus: string;
  // Whether this row's checkbox is ticked.
  selected?: boolean;
  // Whether this row is the active family in the panel (washes the whole row).
  active?: boolean;
  // Toggle the checkbox (does not select the family).
  onToggle?: (familyId: string) => void;
  // Open this family in the panel.
  onSelect?: (familyId: string) => void;
}

// Header helper — a `qhead` row whose columns line up with every DrillRow.
export function DrillRowHead(): JSX.Element {
  const cell: CSSProperties = { whiteSpace: 'nowrap' };
  return (
    <div
      data-testid="drill-head"
      className="lab"
      style={{
        display: 'grid',
        gridTemplateColumns: DRILL_GRID,
        gap: 'var(--s-2)',
        padding: 'var(--s-2) var(--s-4)',
        borderBottom: '1px solid var(--line-2)',
        color: 'var(--muted)',
      }}
    >
      <span style={cell}>sel</span>
      <span style={cell}>#</span>
      <span style={cell}>family · stuck on</span>
      <span style={{ ...cell, textAlign: 'right' }}>value</span>
      <span style={{ ...cell, textAlign: 'center' }}>recency</span>
      <span style={{ ...cell, textAlign: 'right' }}>score</span>
    </div>
  );
}

export default function DrillRow({
  familyId,
  rank,
  name,
  stuckStep,
  value,
  score,
  contactStatus,
  selected = false,
  active = false,
  onToggle,
  onSelect,
}: DrillRowProps): JSX.Element {
  const tone = isContactStatus(contactStatus)
    ? recencyTone(contactStatus)
    : 'neutral';
  const label = isContactStatus(contactStatus)
    ? recencyLabel(contactStatus)
    : contactStatus;

  return (
    <button
      type="button"
      data-testid={`drill-row-${familyId}`}
      onClick={() => onSelect?.(familyId)}
      style={{
        display: 'grid',
        gridTemplateColumns: DRILL_GRID,
        gap: 'var(--s-2)',
        alignItems: 'center',
        width: '100%',
        textAlign: 'left',
        border: 0,
        borderBottom: '1px solid var(--line-2)',
        padding: 'var(--s-3) var(--s-4)',
        background: active ? 'var(--flow-wash)' : 'var(--surface)',
        cursor: 'pointer',
        font: 'inherit',
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = 'var(--surface-2)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = active
          ? 'var(--flow-wash)'
          : 'var(--surface)';
      }}
    >
      <span
        role="checkbox"
        aria-checked={selected}
        aria-label={`Select ${name}`}
        tabIndex={0}
        data-testid={`drill-row-check-${familyId}`}
        className={selected ? 'ck on' : 'ck'}
        onClick={(e) => {
          e.stopPropagation();
          onToggle?.(familyId);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            onToggle?.(familyId);
          }
        }}
        style={{
          width: 16,
          height: 16,
          display: 'grid',
          placeItems: 'center',
          borderRadius: 'var(--r-xs)',
          fontSize: 11,
          border: `1.5px solid ${selected ? 'var(--flow)' : 'var(--line)'}`,
          background: selected ? 'var(--flow)' : 'var(--surface)',
          color: selected ? 'var(--on-ink)' : 'transparent',
          cursor: 'pointer',
        }}
      >
        {selected ? '✓' : ''}
      </span>
      <span
        className="mono"
        style={{ fontSize: 11, color: 'var(--muted)' }}
      >
        {String(rank).padStart(2, '0')}
      </span>
      <span
        style={{
          fontWeight: 600,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {name}
        <small
          style={{
            display: 'block',
            color: 'var(--muted)',
            fontWeight: 500,
            fontSize: 11,
          }}
        >
          {stuckStep}
        </small>
      </span>
      <span
        className="mono"
        style={{ fontSize: 12, fontWeight: 600, textAlign: 'right' }}
      >
        {value}
      </span>
      <span style={{ display: 'flex', justifyContent: 'center' }}>
        <Chip tone={tone}>{label}</Chip>
      </span>
      <span
        className="mono"
        style={{ fontSize: 12, color: 'var(--ink-soft)', textAlign: 'right' }}
      >
        {score}
      </span>
    </button>
  );
}
