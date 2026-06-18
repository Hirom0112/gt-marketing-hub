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
import AdminDashboard from './workspaces/AdminDashboard';
import AgentDashboard from './workspaces/AgentDashboard';
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

// Enrollment is the only primary surface a rep (sales agent) sees — their seat is
// the owner-scoped workspace. Marketing + Leadership are ADMIN-ONLY surfaces
// (MULTI_AGENT_COCKPIT §5 role model: a rep gets just their queue + close panel,
// never the marketing/leadership lenses), so they are gated to the admin seat
// exactly like the Security tab.
const REP_PRIMARY_NAV: ReadonlyArray<SidebarItem<NavKey>> = [
  { key: 'enrollment', label: 'Enrollment', icon: LayoutGrid },
];

const ADMIN_PRIMARY_NAV: ReadonlyArray<SidebarItem<NavKey>> = [
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
  const primaryNav = isAdmin ? ADMIN_PRIMARY_NAV : REP_PRIMARY_NAV;
  const secondaryNav = isAdmin
    ? [SECURITY_NAV, ...SECONDARY_NAV]
    : SECONDARY_NAV;

  // A rep must never land on (or deep-link to) an admin-only workspace. If the
  // active workspace isn't in the rep's nav, fall back to enrollment.
  const repAllowed = new Set<Workspace>(['enrollment', 'settings', 'help']);
  const activeWorkspace: Workspace =
    isAdmin || repAllowed.has(workspace) ? workspace : 'enrollment';

  return (
    <div className="app-shell">
      <Sidebar
        primary={primaryNav}
        secondary={secondaryNav}
        active={activeWorkspace}
        onSelect={onSelect}
      />

      <main className="app-main">
        <div className="page-body">
          {/* Redesign: the admin lands on the AdminDashboard (KPI strip + Leads/
              Students/Reconcile/Team Roster) and the sales agent on the owner-scoped
              AgentDashboard (4-metric strip + motivation banner + Leads/Triage/
              Students/Reconcile/KPI Dashboard). Branch by seat. */}
          {activeWorkspace === 'enrollment' &&
            (session.role === 'agent' ? (
              <AgentDashboard />
            ) : (
              <AdminDashboard />
            ))}
          {/* Admin-only surfaces — gated by primaryNav AND guarded here so a rep
              can never reach Marketing/Leadership/Security even if forced. */}
          {activeWorkspace === 'marketing' && isAdmin && <MarketingWorkspace />}
          {activeWorkspace === 'leadership' && isAdmin && <LeadershipWorkspace />}
          {activeWorkspace === 'security' && isAdmin && <SecurityWorkspace />}
          {activeWorkspace === 'settings' && <SettingsWorkspace />}
          {activeWorkspace === 'help' && <HelpWorkspace />}
        </div>
      </main>
    </div>
  );
}
