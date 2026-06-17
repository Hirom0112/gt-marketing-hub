import { useState } from 'react';
import {
  BarChart3,
  CircleHelp,
  LayoutGrid,
  Megaphone,
  Settings,
} from 'lucide-react';
import './theme.css';
import Sidebar, { type SidebarItem } from './Sidebar';
import EnrollmentWorkspace from './workspaces/EnrollmentWorkspace';
import MarketingWorkspace from './workspaces/MarketingWorkspace';
import LeadershipWorkspace from './workspaces/LeadershipWorkspace';
import SettingsWorkspace from './workspaces/SettingsWorkspace';
import HelpWorkspace from './workspaces/HelpWorkspace';

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
  | 'settings'
  | 'help';

const PRIMARY_NAV: ReadonlyArray<SidebarItem<Workspace>> = [
  { key: 'enrollment', label: 'Enrollment', icon: LayoutGrid },
  { key: 'marketing', label: 'Marketing', icon: Megaphone, badge: 'In progress' },
  { key: 'leadership', label: 'Leadership', icon: BarChart3 },
];

const SECONDARY_NAV: ReadonlyArray<SidebarItem<Workspace>> = [
  { key: 'settings', label: 'Settings', icon: Settings },
  { key: 'help', label: 'Help', icon: CircleHelp },
];

export default function App(): JSX.Element {
  const [workspace, setWorkspace] = useState<Workspace>('enrollment');

  return (
    <div className="app-shell">
      <Sidebar
        primary={PRIMARY_NAV}
        secondary={SECONDARY_NAV}
        active={workspace}
        onSelect={setWorkspace}
      />

      <main className="app-main">
        <div className="page-body">
          {workspace === 'enrollment' && <EnrollmentWorkspace />}
          {workspace === 'marketing' && <MarketingWorkspace />}
          {workspace === 'leadership' && <LeadershipWorkspace />}
          {workspace === 'settings' && <SettingsWorkspace />}
          {workspace === 'help' && <HelpWorkspace />}
        </div>
      </main>
    </div>
  );
}
