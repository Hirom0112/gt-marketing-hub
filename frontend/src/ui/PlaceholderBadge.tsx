import { FlaskConical } from 'lucide-react';

// The INV-9 honesty marker: a small gold badge that labels a simulated /
// placeholder surface (image-gen, social posting, HubSpot writes, scraping).
// Keep this convention — it tells the operator the surface is not live.
export interface PlaceholderBadgeProps {
  label?: string;
}

export function PlaceholderBadge({
  label = 'PLACEHOLDER',
}: PlaceholderBadgeProps): JSX.Element {
  return (
    <span
      className="mono"
      title="Simulated surface — not a live integration (INV-9)"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: '8.5px',
        letterSpacing: '0.1em',
        color: 'var(--gate-ink)',
        background: 'var(--gate-wash)',
        border: '1px solid var(--gate)',
        borderRadius: 'var(--r-xs)',
        padding: '2px 6px',
      }}
    >
      <FlaskConical size={9} aria-hidden />
      {label}
    </span>
  );
}
