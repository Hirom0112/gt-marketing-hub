import { useState } from 'react';
import {
  BarChart3,
  BookMarked,
  CircleHelp,
  ClipboardCheck,
  Home as HomeIcon,
  LayoutGrid,
  LineChart,
  LogOut,
  Megaphone,
  Tent,
  Settings,
  ShieldCheck,
  Wallet,
} from 'lucide-react';
import './theme.css';
import Sidebar, { type SidebarItem } from './Sidebar';
import LoginPage from './LoginPage';
import { SessionProvider, useSession } from './session/SessionContext';
import ComposableHome from './home/ComposableHome';
import AdminDashboard from './workspaces/AdminDashboard';
import AgentDashboard from './workspaces/AgentDashboard';
import MarketingWorkspace from './workspaces/MarketingWorkspace';
import LeadershipWorkspace from './workspaces/LeadershipWorkspace';
import DecisionQueueWorkspace, {
  useOpenDecisionCount,
} from './workspaces/DecisionQueueWorkspace';
import SettingsWorkspace from './workspaces/SettingsWorkspace';
import ResourceLibraryWorkspace from './workspaces/ResourceLibraryWorkspace';
import WebsiteAnalyticsWorkspace from './workspaces/WebsiteAnalyticsWorkspace';
import SummerCampWorkspace from './workspaces/SummerCampWorkspace';
import HelpWorkspace from './workspaces/HelpWorkspace';
import SecurityWorkspace from './workspaces/SecurityWorkspace';
import BudgetWorkspace from './workspaces/BudgetWorkspace';

// GT Pulse app shell — a full-height blue LEFT nav rail beside the fluid main
// column. There is NO top bar and NO page-header: the sidebar (GT Pulse logo +
// nav stack) is the only chrome, and each workspace owns its own content. The
// sidebar drives the single-mount Workspace state (Settings + Help are real
// surfaces). The main area is fully fluid (width:100%, scaling padding). The
// Enrollment situation summary lives in the page CONTENT (not any header).
type Workspace =
  | 'home'
  | 'enrollment'
  | 'marketing'
  | 'leadership'
  | 'decisions'
  | 'budget'
  | 'security'
  | 'resources'
  | 'analytics'
  | 'camp'
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
  { key: 'home', label: 'Home', icon: HomeIcon },
  { key: 'enrollment', label: 'Enrollment', icon: LayoutGrid },
];

const ADMIN_PRIMARY_NAV: ReadonlyArray<SidebarItem<NavKey>> = [
  { key: 'home', label: 'Home', icon: HomeIcon },
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
  { key: 'switch-seat', label: 'Sign out', icon: LogOut },
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

  // The consolidated Decision Queue (B2) is leader/admin only — an operator never
  // sees the nav entry OR the surface (the backend also 403s the GET defensively).
  // Computed before any early return so the badge hook's call order is stable
  // (rules of hooks): it is enabled only for a seated leader/admin.
  const isLeaderOrAdmin =
    session?.role === 'admin' || session?.role === 'leader';

  // The leadership nav open-count badge — counts OPEN decisions (leader/admin only).
  const { count: openDecisions, refresh: refreshDecisions } =
    useOpenDecisionCount(isLeaderOrAdmin);

  // No seat chosen yet ⇒ the demo login gate (M1). The gate's chosen seat scopes
  // the whole app (and rides as a signed `Authorization: Bearer` token via apiFetch).
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

  // The Decision Queue nav entry, shown to leader + admin. Its open-count rides as
  // the existing sidebar badge (consistent with the other nav badges).
  const decisionsNav: SidebarItem<NavKey> = {
    key: 'decisions',
    label: 'Decisions',
    icon: ClipboardCheck,
    ...(openDecisions > 0 ? { badge: String(openDecisions) } : {}),
  };

  // The Budget Tracker nav entry (B4), shown to leader + admin alongside the
  // Decision Queue — an operator never sees the nav item OR the surface.
  const budgetNav: SidebarItem<NavKey> = {
    key: 'budget',
    label: 'Budget',
    icon: Wallet,
  };

  // Resource Library (spec Module 12) — owner is "All", so every seat sees it.
  const resourcesNav: SidebarItem<NavKey> = {
    key: 'resources',
    label: 'Library',
    icon: BookMarked,
  };

  // Website & Digital Analytics (spec Module 13) — the Marketing Lead's GA4 lens.
  // Admin-only in our current model (alongside Marketing/Leadership).
  const analyticsNav: SidebarItem<NavKey> = {
    key: 'analytics',
    label: 'Analytics',
    icon: LineChart,
    badge: 'Simulated',
  };

  // Summer Camp (spec Module 4) — the Content Owner's separate-program lens.
  // Admin-only here; the program-isolation backbone keeps it off the Fall path.
  const campNav: SidebarItem<NavKey> = {
    key: 'camp',
    label: 'Summer Camp',
    icon: Tent,
  };

  const primaryNav: ReadonlyArray<SidebarItem<NavKey>> = isAdmin
    ? [...ADMIN_PRIMARY_NAV, campNav, decisionsNav, budgetNav, analyticsNav, resourcesNav]
    : isLeaderOrAdmin
      ? [...REP_PRIMARY_NAV, decisionsNav, budgetNav, resourcesNav]
      : [...REP_PRIMARY_NAV, resourcesNav];
  const secondaryNav = isAdmin
    ? [SECURITY_NAV, ...SECONDARY_NAV]
    : SECONDARY_NAV;

  // A rep must never land on (or deep-link to) an admin-only workspace. If the
  // active workspace isn't in the seat's nav, fall back to enrollment.
  const allowed = new Set<Workspace>([
    'home',
    'enrollment',
    'resources',
    'settings',
    'help',
  ]);
  if (isLeaderOrAdmin) {
    allowed.add('decisions');
    allowed.add('budget');
  }
  if (isAdmin) {
    allowed.add('marketing');
    allowed.add('leadership');
    allowed.add('security');
    allowed.add('analytics');
    allowed.add('camp');
  }
  const activeWorkspace: Workspace = allowed.has(workspace)
    ? workspace
    : 'enrollment';

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
          {/* Composable Home (B3) · the per-user widget grid; available to every
              signed-in seat as the customizable overview. */}
          {activeWorkspace === 'home' && <ComposableHome />}
          {activeWorkspace === 'enrollment' &&
            (session.role === 'operator' ? (
              <AgentDashboard />
            ) : (
              <AdminDashboard />
            ))}
          {/* Admin-only surfaces · gated by primaryNav AND guarded here so a rep
              can never reach Marketing/Leadership/Security even if forced. */}
          {activeWorkspace === 'marketing' && isAdmin && <MarketingWorkspace />}
          {activeWorkspace === 'leadership' && isAdmin && <LeadershipWorkspace />}
          {/* Decision Queue (B2) · leader + admin only; gated by primaryNav AND
              guarded here so an operator can never reach it even if forced. */}
          {activeWorkspace === 'decisions' && isLeaderOrAdmin && (
            <DecisionQueueWorkspace
              onChanged={refreshDecisions}
              canDecide={session.role === 'leader'}
            />
          )}
          {/* Budget Tracker (B4) · leader + admin only; gated by primaryNav AND
              guarded here so an operator can never reach it even if forced. */}
          {activeWorkspace === 'budget' && isLeaderOrAdmin && <BudgetWorkspace />}
          {activeWorkspace === 'security' && isAdmin && <SecurityWorkspace />}
          {activeWorkspace === 'resources' && <ResourceLibraryWorkspace />}
          {activeWorkspace === 'analytics' && isAdmin && (
            <WebsiteAnalyticsWorkspace />
          )}
          {activeWorkspace === 'camp' && isAdmin && <SummerCampWorkspace />}
          {activeWorkspace === 'settings' && <SettingsWorkspace />}
          {activeWorkspace === 'help' && <HelpWorkspace />}
        </div>
      </main>
    </div>
  );
}
