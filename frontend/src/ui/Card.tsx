import type { CSSProperties, ReactNode } from 'react';

// A raised surface with a 12px radius + hairline border (theme tokens only).
// `pad` toggles the standard inner padding; turn it off for edge-to-edge lists.
export interface CardProps {
  children: ReactNode;
  pad?: boolean;
  className?: string;
  style?: CSSProperties;
}

export function Card({
  children,
  pad = true,
  className,
  style,
}: CardProps): JSX.Element {
  return (
    <div
      className={className}
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-lg)',
        boxShadow: 'var(--shadow-sm)',
        padding: pad ? 'var(--s-4)' : 0,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
