import type { LucideIcon } from 'lucide-react';

// The full-height left nav rail (GT Pulse shell). It is the app's only chrome:
// the GT Pulse brand sits at the TOP (the dark logo on a small dark rounded tile
// so it reads on the light rail), then a vertical stack of workspace items (icon
// above a mono micro-label), with Settings + Help pushed to the bottom. Drives
// the Workspace state — pure presentational, no fetch, token-driven. The active
// item gets a soft flow-wash highlight; inactive items are muted; hover lifts to
// surface-2.
export interface SidebarItem<K extends string> {
  key: K;
  label: string;
  icon: LucideIcon;
}

export interface SidebarProps<K extends string> {
  primary: ReadonlyArray<SidebarItem<K>>;
  secondary: ReadonlyArray<SidebarItem<K>>;
  active: K;
  onSelect: (key: K) => void;
}

export default function Sidebar<K extends string>({
  primary,
  secondary,
  active,
  onSelect,
}: SidebarProps<K>): JSX.Element {
  function renderItem({ key, label, icon: Icon }: SidebarItem<K>): JSX.Element {
    const on = key === active;
    return (
      <button
        key={key}
        type="button"
        role="tab"
        aria-selected={on}
        title={label}
        data-testid={`sidebar-nav-${key}`}
        className={`sidebar-item${on ? ' is-active' : ''}`}
        onClick={() => onSelect(key)}
      >
        <Icon size={26} aria-hidden className="sidebar-item-icon" />
        <span className="sidebar-item-label">{label}</span>
      </button>
    );
  }

  return (
    <nav
      className="sidebar"
      data-testid="sidebar"
      role="tablist"
      aria-label="Workspace"
    >
      <div className="sidebar-brand" data-testid="sidebar-brand">
        <span className="sidebar-brand-tile">
          <img
            className="sidebar-brand-logo"
            src="/gt-pulse-logo.png"
            alt="GT Pulse"
          />
        </span>
      </div>

      <div className="sidebar-group sidebar-group-primary">
        {primary.map(renderItem)}
      </div>

      <div className="sidebar-group sidebar-group-secondary">
        {secondary.map(renderItem)}
      </div>
    </nav>
  );
}
