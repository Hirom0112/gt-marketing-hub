import type { LucideIcon } from 'lucide-react';

// The top-bar segmented control that selects the active workspace. Generic over
// the workspace key so the App owns the union type; renders an accessible
// segmented control (role="tablist") with the active segment filled in ink.
export interface WorkspaceOption<K extends string> {
  key: K;
  label: string;
  icon: LucideIcon;
}

export interface WorkspaceToggleProps<K extends string> {
  options: ReadonlyArray<WorkspaceOption<K>>;
  active: K;
  onSelect: (key: K) => void;
  ariaLabel?: string;
}

export function WorkspaceToggle<K extends string>({
  options,
  active,
  onSelect,
  ariaLabel = 'Workspace',
}: WorkspaceToggleProps<K>): JSX.Element {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      style={{
        display: 'inline-flex',
        gap: 'var(--s-1)',
        padding: 'var(--s-1)',
        background: 'var(--paper)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-lg)',
      }}
    >
      {options.map(({ key, label, icon: Icon }) => {
        const on = key === active;
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onSelect(key)}
            style={{
              fontFamily: 'var(--mono)',
              fontSize: '12px',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              padding: '7px 14px',
              border: 'none',
              borderRadius: 'var(--r-md)',
              background: on ? 'var(--ink)' : 'transparent',
              color: on ? 'var(--on-ink)' : 'var(--ink-soft)',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              boxShadow: on ? 'var(--shadow-sm)' : 'none',
              transition:
                'background var(--dur) var(--ease), color var(--dur) var(--ease)',
            }}
          >
            <Icon size={15} aria-hidden />
            {label}
          </button>
        );
      })}
    </div>
  );
}
