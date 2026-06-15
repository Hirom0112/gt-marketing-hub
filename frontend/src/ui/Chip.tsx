import type { ReactNode } from 'react';
import { type Tone, toneVars } from './tokens';

// A small mono status chip. `tone` picks the semantic colour; `tone="neutral"`
// is the quiet default. Optionally rendered as a soft outline instead of a fill.
export interface ChipProps {
  children: ReactNode;
  tone?: Tone;
  title?: string;
}

export function Chip({
  children,
  tone = 'neutral',
  title,
}: ChipProps): JSX.Element {
  const t = toneVars(tone);
  return (
    <span
      title={title}
      className="mono"
      style={{
        display: 'inline-block',
        fontSize: 'var(--fs-chip)',
        lineHeight: 1.6,
        borderRadius: 'var(--r-xs)',
        padding: '2px 8px',
        whiteSpace: 'nowrap',
        color: t.fg,
        background: t.wash,
        border: `1px solid ${tone === 'neutral' ? 'var(--line)' : t.solid}`,
      }}
    >
      {children}
    </span>
  );
}
