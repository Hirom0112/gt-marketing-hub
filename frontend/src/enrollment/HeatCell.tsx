// HeatCell (S12 W3) — the collapsed "busy day" tile. When a calendar day holds
// too many stalls to list as chips, it collapses to this full-bleed heat tile:
// the more stalls (and the more dollars at risk), the hotter it reads. Intensity
// drives the gradient opacity inline via a `--i` custom property so the ramp
// itself stays in theme.css (--heat-from / --heat-to channels) — no raw hex here.
//
// `intensity` is 0–1 (the caller computes it, e.g. min(1, count/120)). The cell
// tints from the palette's amber→red heat ramp and surfaces the count, the
// dollars at risk, and a "tap to triage →" affordance.

interface HeatCellProps {
  // Number of stalls collapsed into this day.
  count: number;
  // Pre-formatted dollars-at-risk string (e.g. "$24k") — the caller owns money
  // formatting so this component never reinvents it.
  atRisk: string;
  // Heat intensity 0–1 (caller-derived). Clamped here defensively.
  intensity: number;
  // Click → open the day's drill-down.
  onClick?: () => void;
}

export default function HeatCell({
  count,
  atRisk,
  intensity,
  onClick,
}: HeatCellProps): JSX.Element {
  const i = Math.max(0, Math.min(1, intensity));
  return (
    <button
      type="button"
      data-testid="heat-cell"
      onClick={onClick}
      style={
        {
          // `--i` feeds the inline gradient calc; the colour channels are tokens.
          '--i': i,
          flex: 1,
          width: '100%',
          minHeight: 74,
          border: 0,
          borderRadius: 'var(--r-sm)',
          padding: '7px 8px',
          textAlign: 'left',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'flex-end',
          gap: 2,
          color: 'var(--heat-cell-ink)',
          cursor: 'pointer',
          background:
            'linear-gradient(160deg, rgba(var(--heat-from), calc(.55 + var(--i) * .35)), rgba(var(--heat-to), calc(.6 + var(--i) * .4)))',
          transition: 'transform var(--dur) var(--ease)',
        } as React.CSSProperties
      }
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = 'translateY(-1px)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = 'translateY(0)';
      }}
    >
      <span
        className="mono"
        data-testid="heat-cell-count"
        style={{
          fontSize: 'var(--fs-heat)',
          fontWeight: 700,
          letterSpacing: '-0.03em',
          lineHeight: 1,
        }}
      >
        {count}
      </span>
      <span style={{ fontSize: 10, fontWeight: 600, opacity: 0.95 }}>
        stalls
      </span>
      <span
        className="mono"
        data-testid="heat-cell-risk"
        style={{ fontSize: 10.5, opacity: 0.95 }}
      >
        {atRisk} at risk
      </span>
      <span style={{ fontSize: 9, opacity: 0.8 }}>tap to triage →</span>
    </button>
  );
}
