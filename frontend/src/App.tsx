import { useState } from 'react';
import {
  BarChart3,
  CircleHelp,
  LayoutGrid,
  Megaphone,
  Settings,
} from 'lucide-react';
import './theme.css';
import { apiBaseUrl } from './config';
import { Chip } from './ui';
import Sidebar, { type SidebarItem } from './Sidebar';
import EnrollmentWorkspace from './workspaces/EnrollmentWorkspace';
import MarketingWorkspace from './workspaces/MarketingWorkspace';
import LeadershipWorkspace from './workspaces/LeadershipWorkspace';
import SettingsWorkspace from './workspaces/SettingsWorkspace';
import HelpWorkspace from './workspaces/HelpWorkspace';

// S14 app shell — a full-width top BRAND BAR over a [LEFT nav rail][main] row.
// The brand bar carries the GT mark + "GT Growth Cockpit" wordmark on the left,
// and on the right the Enrollment situation summary (a single bordered pill)
// plus the API base URL chip (TECH_STACK §5.1) in the far corner. Below it, the
// sidebar is nav-only (icon/label items) and drives the single-mount Workspace
// state; Settings + Help join the union as real config/help surfaces. The main
// area stays fully fluid (width:100%, scaling padding) — no hard max-width. Each
// workspace's page-header carries a large page title with a mono eyebrow below
// it. The Enrollment situation summary stays owned by its workspace (least-
// coupled — the /work-queue fetch doesn't move) and PORTALS into the brand bar's
// `#situation-slot`.
type Workspace =
  | 'enrollment'
  | 'marketing'
  | 'leadership'
  | 'settings'
  | 'help';

interface WorkspaceMeta {
  eyebrow: string;
  title: string;
}

const META: Record<Workspace, WorkspaceMeta> = {
  enrollment: {
    eyebrow: 'Enrollment',
    title: 'Enrollment Recovery Calendar',
  },
  marketing: { eyebrow: 'Marketing', title: 'Marketing' },
  leadership: { eyebrow: 'Leadership', title: 'Leadership Scoreboard' },
  settings: { eyebrow: 'Settings', title: 'Configuration' },
  help: { eyebrow: 'Help', title: 'How This Works' },
};

const PRIMARY_NAV: ReadonlyArray<SidebarItem<Workspace>> = [
  { key: 'enrollment', label: 'Enrollment', icon: LayoutGrid },
  { key: 'marketing', label: 'Marketing', icon: Megaphone },
  { key: 'leadership', label: 'Leadership', icon: BarChart3 },
];

const SECONDARY_NAV: ReadonlyArray<SidebarItem<Workspace>> = [
  { key: 'settings', label: 'Settings', icon: Settings },
  { key: 'help', label: 'Help', icon: CircleHelp },
];

export default function App(): JSX.Element {
  const [workspace, setWorkspace] = useState<Workspace>('enrollment');
  const meta = META[workspace];

  return (
    <div className="app-shell">
      <header className="app-topbar" data-testid="app-topbar">
        <div className="app-topbar-brand" data-testid="app-wordmark">
          <span className="app-topbar-mark" aria-hidden>
            GT
          </span>
          <span className="app-topbar-wordmark">GT Growth Cockpit</span>
        </div>
        <div className="app-topbar-aside">
          {/* The Enrollment situation summary renders into this slot via the
              workspace's own SituationBar (kept where the /work-queue fetch
              lives); other workspaces leave it empty. */}
          <div id="situation-slot" className="app-topbar-situation" />
          <Chip tone="flow" title="Connected API base URL (TECH_STACK §5.1)">
            <span data-testid="api-base-url">API · {apiBaseUrl}</span>
          </Chip>
        </div>
      </header>

      <div className="app-row">
        <Sidebar
          primary={PRIMARY_NAV}
          secondary={SECONDARY_NAV}
          active={workspace}
          onSelect={setWorkspace}
        />

        <main className="app-main">
          <header className="page-header">
            <div className="page-header-lede">
              <h1 className="page-title" data-testid="page-title">
                {meta.title}
              </h1>
              <span className="lab page-eyebrow">{meta.eyebrow}</span>
            </div>
          </header>

          <div className="page-body">
            {workspace === 'enrollment' && <EnrollmentWorkspace />}
            {workspace === 'marketing' && <MarketingWorkspace />}
            {workspace === 'leadership' && <LeadershipWorkspace />}
            {workspace === 'settings' && <SettingsWorkspace />}
            {workspace === 'help' && <HelpWorkspace />}
          </div>
        </main>
      </div>
    </div>
  );
}
