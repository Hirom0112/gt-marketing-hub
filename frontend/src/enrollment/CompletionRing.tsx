// CompletionRing (S12 W3) — a 52px conic-gradient dial showing application
// completion. The fill (--ring-fill) sweeps to `pct`% of the circle over the
// track (--ring-track); a 40px punch-out in the centre carries the mono "NN%".
// Used in the deal panel's "where they left off" block. Token-driven — the ring
// colours live in theme.css, the component only sets the `--p` sweep inline.

interface CompletionRingProps {
  // Percent complete, 0–100 (clamped defensively).
  pct: number;
}

export default function CompletionRing({
  pct,
}: CompletionRingProps): JSX.Element {
  const p = Math.max(0, Math.min(100, Math.round(pct)));
  return (
    <div
      data-testid="completion-ring"
      role="img"
      aria-label={`${p}% complete`}
      style={
        {
          '--p': p,
          width: 52,
          height: 52,
          flex: 'none',
          borderRadius: '50%',
          display: 'grid',
          placeItems: 'center',
          background:
            'conic-gradient(var(--ring-fill) calc(var(--p) * 1%), var(--ring-track) 0)',
        } as React.CSSProperties
      }
    >
      <span
        className="mono"
        data-testid="completion-ring-label"
        style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          background: 'var(--surface)',
          display: 'grid',
          placeItems: 'center',
          fontSize: 11,
          fontWeight: 700,
        }}
      >
        {p}%
      </span>
    </div>
  );
}
