// DrillRow (A-23 redesign) — one dense row in the TRIAGE worklist. The row leads
// with RECOVERABILITY (likelihood), the thing that actually decides who to chase
// first: "the further they went down the funnel, the more recoverable they are."
// The hero cell is the likelihood % (mono, loud), and the magnitude bar under the
// name encodes that same likelihood relative to the rest of the scope. The old
// per-family recoverable-$ hero is GONE (it rode on synthetic hash noise) — the
// money now lives as the HONEST secondary: the face value ($ = children × the
// per-child GT-Anywhere tuition) with the child count that drives it ("3 kids"),
// because every targeted family pays the same per child. Funnel depth (the stuck
// step) + the funding label (Texas voucher / Self-pay) sit under the name. A mono
// age cell ("12d") differentiates overdue rows by how long they've sat. The grid
// template is shared with the header via DRILL_GRID. History has its own grammar
// (HistoryRow).

// The shared grid: rail(via border, not a col) · checkbox · name+meta+bar · hero
// likelihood · value+kids · age · stall-date.
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
  // Funding label ("Texas voucher" / "Self-pay") — sits next to the stuck step.
  funding?: string;
  // Pre-formatted mono stall-date (e.g. "Jun 13") — caller owns date formatting.
  stallDate: string;
  // Pre-formatted mono age (e.g. "12d") — caller owns the age formatting.
  age: string;
  // Pre-formatted HERO likelihood (recoverability, e.g. "84%") — the loudest cell.
  likelihood: string;
  // Pre-formatted secondary face value (e.g. "$31,200") — children × tuition.
  value: string;
  // Pre-formatted child-count label (e.g. "3 kids") — the value driver (A-23).
  kids: string;
  // The magnitude fraction (0..1) = recoverability / max-in-scope → bar width.
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
      <span style={{ textAlign: 'right' }}>likely</span>
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
  funding,
  stallDate,
  age,
  likelihood,
  value,
  kids,
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
        <small className="drill-step">
          {stuckStep}
          {funding ? <span className="drill-funding"> · {funding}</span> : null}
        </small>
        {/* The magnitude bar · likelihood relative to the rest of the scope. */}
        <span
          className="drill-bar-track"
          data-testid={`drill-row-bar-${familyId}`}
          aria-hidden
        >
          <span className="drill-bar-fill" style={{ width: `${pct}%` }} />
        </span>
      </span>

      <span className="drill-hero" data-testid={`drill-row-likelihood-${familyId}`}>
        {likelihood}
      </span>
      <span className="drill-value-cell">
        <span className="drill-value" data-testid={`drill-row-value-${familyId}`}>
          {value}
        </span>
        {kids ? (
          <small className="drill-kids" data-testid={`drill-row-kids-${familyId}`}>
            {kids}
          </small>
        ) : null}
      </span>
      <span className="drill-age" data-testid={`drill-row-age-${familyId}`}>
        {age}
      </span>
      <span className="drill-date" data-testid={`drill-row-date-${familyId}`}>
        {stallDate}
      </span>
    </button>
  );
}
