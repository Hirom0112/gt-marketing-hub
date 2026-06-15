import type { ReactNode } from 'react';

// A labelled read-only value: a mono micro-label over a mono value, in an inset
// well. Used for dense fact grids (deal view, funding tracker).
export interface FieldProps {
  label: string;
  value: ReactNode;
}

export function Field({ label, value }: FieldProps): JSX.Element {
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        padding: '6px 9px',
        background: 'var(--surface-2)',
      }}
    >
      <div className="lab">{label}</div>
      <div
        className="mono"
        style={{ fontSize: 'var(--fs-sm)', marginTop: 2, color: 'var(--ink)' }}
      >
        {value}
      </div>
    </div>
  );
}
