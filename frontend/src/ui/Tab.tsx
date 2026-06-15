import type { LucideIcon } from 'lucide-react';

// A single pill tab + a horizontal TabBar of them. `active` drives the filled
// ink state; the bar is keyboard- and screen-reader-friendly (role="tablist").
export interface TabProps {
  label: string;
  active: boolean;
  onSelect: () => void;
  icon?: LucideIcon;
}

export function Tab({
  label,
  active,
  onSelect,
  icon: Icon,
}: TabProps): JSX.Element {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onSelect}
      style={{
        fontFamily: 'var(--mono)',
        fontSize: '11.5px',
        display: 'inline-flex',
        alignItems: 'center',
        gap: '7px',
        padding: '8px 12px',
        border: 'none',
        borderRadius: 'var(--r-md)',
        background: active ? 'var(--ink)' : 'transparent',
        color: active ? 'var(--on-ink)' : 'var(--muted)',
        cursor: 'pointer',
        whiteSpace: 'nowrap',
        transition: 'background var(--dur) var(--ease), color var(--dur) var(--ease)',
      }}
    >
      {Icon ? <Icon size={14} aria-hidden /> : null}
      {label}
    </button>
  );
}

export interface TabItem<K extends string> {
  key: K;
  label: string;
  icon?: LucideIcon;
}

export interface TabBarProps<K extends string> {
  tabs: ReadonlyArray<TabItem<K>>;
  active: K;
  onSelect: (key: K) => void;
  ariaLabel?: string;
}

export function TabBar<K extends string>({
  tabs,
  active,
  onSelect,
  ariaLabel,
}: TabBarProps<K>): JSX.Element {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="scroll"
      style={{
        display: 'flex',
        gap: 'var(--s-1)',
        overflowX: 'auto',
        padding: 'var(--s-1)',
        background: 'var(--surface)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-lg)',
      }}
    >
      {tabs.map((t) => (
        <Tab
          key={t.key}
          label={t.label}
          icon={t.icon}
          active={t.key === active}
          onSelect={() => onSelect(t.key)}
        />
      ))}
    </div>
  );
}
