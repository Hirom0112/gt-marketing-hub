// DrillRow (S13 redesign) — one dense row in the TRIAGE worklist. The redesign
// kills the "AI slop" pill-wall: the recency Chip column is GONE (it's a 3px
// left-edge RAIL now), the rank column and the score column are GONE (the ordered
// list + the magnitude bar convey priority; score is a model internal). What's
// loud is the MONEY: recoverable_now is the hero cell (mono, 15px, weight 700,
// tabular nums), with a neutral magnitude bar under the family name showing where
// the recoverable dollars sit relative to the rest of the scope. Face value stays
// as a quieter secondary cell so an operator can sanity-check "$4k of an $18k
// deal". A mono age cell ("12d") differentiates overdue rows by how long they've
// sat, not an identical word. The grid template is shared with the header via
// DRILL_GRID. History does NOT use this row — it has its own grammar (HistoryRow).

// The shared grid: rail(via border, not a col) · checkbox · name+bar · hero
// recoverable · secondary value · age · stall-date.
export const DRILL_GRID = '26px 1fr 120px 84px 56px 72px';

// Map a raw contact_status onto the recency rail class. Only OVERDUE is the loud
// saturated rail; fresh is a sharp neutral rail; working/followed_up is teal.
// eslint-disable-next-line react-refresh/only-export-components
export function railClass(contactStatus: string): string {
  if (contactStatus === 'overdue') return 'rail-overdue';
  if (contactStatus === 'followed_up' || contactStatus === 'working')
    return 'rail-working';
  // fresh (and any unknown) → the sharp neutral rail.
  return 'rail-fresh';
}

interface DrillRowProps {
  // Stable id (used by selection callbacks + testids).
  familyId: string;
  // Display name (e.g. "The Alvarez Family") — the ONLY sans element on the row.
  name: string;
  // The stuck step, human-readable — rendered as a mono uppercase system tag.
  stuckStep: string;
  // Pre-formatted mono stall-date (e.g. "Jun 13") — caller owns date formatting.
  stallDate: string;
  // Pre-formatted mono age (e.g. "12d") — caller owns the age formatting.
  age: string;
  // Pre-formatted HERO recoverable-now (e.g. "$50,000") — the loudest cell.
  recoverable: string;
  // Pre-formatted secondary face value (e.g. "$60,000") — the sanity-check cell.
  value: string;
  // The magnitude fraction (0..1) = recoverable_now / max-in-scope → bar width.
  magnitude: number;
  // Raw contact_status string (→ the recency rail class).
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

// Header helper — a `qhead` row whose columns line up with every DrillRow (the
// rail is a border, so the header carries a transparent rail border too).
export function DrillRowHead(): JSX.Element {
  return (
    <div
      data-testid="drill-head"
      className="lab drill-head"
      style={{ gridTemplateColumns: DRILL_GRID }}
    >
      <span>sel</span>
      <span>family · stuck on</span>
      <span style={{ textAlign: 'right' }}>recoverable</span>
      <span style={{ textAlign: 'right' }}>value</span>
      <span>age</span>
      <span style={{ textAlign: 'right' }}>stalled</span>
    </div>
  );
}

export default function DrillRow({
  familyId,
  name,
  stuckStep,
  stallDate,
  age,
  recoverable,
  value,
  magnitude,
  contactStatus,
  selected = false,
  active = false,
  onToggle,
  onSelect,
}: DrillRowProps): JSX.Element {
  const pct = Math.max(0, Math.min(1, magnitude)) * 100;
  return (
    <button
      type="button"
      data-testid={`drill-row-${familyId}`}
      data-rail={railClass(contactStatus)}
      onClick={() => onSelect?.(familyId)}
      className={`drill-row ${railClass(contactStatus)}${active ? ' is-active' : ''}`}
      style={{ gridTemplateColumns: DRILL_GRID }}
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

      <span style={{ minWidth: 0 }}>
        <span className="drill-name">{name}</span>
        <small className="drill-step">{stuckStep}</small>
        {/* The magnitude bar — where the recoverable money sits (neutral, not red). */}
        <span
          className="drill-bar-track"
          data-testid={`drill-row-bar-${familyId}`}
          aria-hidden
        >
          <span className="drill-bar-fill" style={{ width: `${pct}%` }} />
        </span>
      </span>

      <span className="drill-hero" data-testid={`drill-row-recoverable-${familyId}`}>
        {recoverable}
      </span>
      <span className="drill-value">{value}</span>
      <span className="drill-age" data-testid={`drill-row-age-${familyId}`}>
        {age}
      </span>
      <span className="drill-date" data-testid={`drill-row-date-${familyId}`}>
        {stallDate}
      </span>
    </button>
  );
}
