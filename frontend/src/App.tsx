import { useState } from 'react';
import {
  BarChart3,
  CircleHelp,
  LayoutGrid,
  LogOut,
  Megaphone,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import './theme.css';
import Sidebar, { type SidebarItem } from './Sidebar';
import LoginPage from './LoginPage';
import { SessionProvider, useSession } from './session/SessionContext';
import EnrollmentWorkspace from './workspaces/EnrollmentWorkspace';
import RepWorkspace from './workspaces/RepWorkspace';
import MarketingWorkspace from './workspaces/MarketingWorkspace';
import LeadershipWorkspace from './workspaces/LeadershipWorkspace';
import SettingsWorkspace from './workspaces/SettingsWorkspace';
import HelpWorkspace from './workspaces/HelpWorkspace';
import SecurityWorkspace from './workspaces/SecurityWorkspace';

// GT Pulse app shell — a full-height blue LEFT nav rail beside the fluid main
// column. There is NO top bar and NO page-header: the sidebar (GT Pulse logo +
// nav stack) is the only chrome, and each workspace owns its own content. The
// sidebar drives the single-mount Workspace state (Settings + Help are real
// surfaces). The main area is fully fluid (width:100%, scaling padding). The
// Enrollment situation summary lives in the page CONTENT (not any header).
type Workspace =
  | 'enrollment'
  | 'marketing'
  | 'leadership'
  | 'security'
  | 'settings'
  | 'help';

// Nav keys include the 'switch-seat' action (returns to the login gate) alongside
// the real workspaces.
type NavKey = Workspace | 'switch-seat';

const PRIMARY_NAV: ReadonlyArray<SidebarItem<NavKey>> = [
  { key: 'enrollment', label: 'Enrollment', icon: LayoutGrid },
  { key: 'marketing', label: 'Marketing', icon: Megaphone, badge: 'In progress' },
  { key: 'leadership', label: 'Leadership', icon: BarChart3, badge: 'In progress' },
];

// The Security / observability tab (M7) is an ADMIN-ONLY capability
// (MULTI_AGENT_COCKPIT §5 role model: ✅ admin, ❌ rep). It is injected into the
// secondary nav only for an admin seat — a rep must NEVER see it.
const SECURITY_NAV: SidebarItem<NavKey> = {
  key: 'security',
  label: 'Security',
  icon: ShieldCheck,
};

const SECONDARY_NAV: ReadonlyArray<SidebarItem<NavKey>> = [
  { key: 'settings', label: 'Settings', icon: Settings },
  { key: 'help', label: 'Help', icon: CircleHelp },
  { key: 'switch-seat', label: 'Switch seat', icon: LogOut },
];

export default function App(): JSX.Element {
  return (
    <SessionProvider>
      <AppShell />
    </SessionProvider>
  );
}

function AppShell(): JSX.Element {
  const { session, enter, leave } = useSession();
  const [workspace, setWorkspace] = useState<Workspace>('enrollment');

  // No seat chosen yet ⇒ the demo login gate (M1). The gate's chosen seat scopes
  // the whole app (and rides on the X-Demo-* headers via apiFetch).
  if (session === null) {
    return (
      <LoginPage
        onEnter={(s) => {
          enter(s);
          setWorkspace('enrollment');
        }}
      />
    );
  }

  function onSelect(key: NavKey): void {
    if (key === 'switch-seat') {
      leave();
      return;
    }
    setWorkspace(key);
  }

  // Admin-only: the Security tab is injected at the top of the secondary group
  // for an admin seat ONLY. A rep (agent) never sees the nav item OR the tab.
  const isAdmin = session.role === 'admin';
  const secondaryNav = isAdmin
    ? [SECURITY_NAV, ...SECONDARY_NAV]
    : SECONDARY_NAV;

  return (
    <div className="app-shell">
      <Sidebar
        primary={PRIMARY_NAV}
        secondary={secondaryNav}
        active={workspace}
        onSelect={onSelect}
      />

      <main className="app-main">
        <div className="page-body">
          {/* M2 — the rep gets ONE subset view (owner-scoped "My Queue" + close
              panel); the admin keeps the full command center. Branch by seat. */}
          {workspace === 'enrollment' &&
            (session.role === 'agent' ? (
              <RepWorkspace />
            ) : (
              <EnrollmentWorkspace />
            ))}
          {workspace === 'marketing' && <MarketingWorkspace />}
          {workspace === 'leadership' && <LeadershipWorkspace />}
          {/* Admin-only (M7): guard the render too, so a rep can never reach the
              Security surface even if the workspace state were forced. */}
          {workspace === 'security' && isAdmin && <SecurityWorkspace />}
          {workspace === 'settings' && <SettingsWorkspace />}
          {workspace === 'help' && <HelpWorkspace />}
        </div>
      </main>
    </div>
  );
}
