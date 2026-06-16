import { useState } from 'react';
import { BarChart3, LayoutGrid, Megaphone } from 'lucide-react';
import './theme.css';
import { apiBaseUrl } from './config';
import { Chip, WorkspaceToggle, type WorkspaceOption } from './ui';
import EnrollmentWorkspace from './workspaces/EnrollmentWorkspace';
import MarketingWorkspace from './workspaces/MarketingWorkspace';
import LeadershipWorkspace from './workspaces/LeadershipWorkspace';

// S8 Wave 1 app shell — the new three-workspace IA (Enrollment / Marketing /
// Leadership) selected from a top-bar toggle. Exactly one workspace mounts at a
// time. This wave delivers the dep + tokens + primitives + shell; the inner
// real components are mounted UNCHANGED via thin workspace containers (Wave 2
// re-skins them). The API base URL (TECH_STACK §5.1) is surfaced as a status
// chip + a testid the acceptance test reads.
type Workspace = 'enrollment' | 'marketing' | 'leadership';

const WORKSPACES: ReadonlyArray<WorkspaceOption<Workspace>> = [
  { key: 'enrollment', label: 'Enrollment', icon: LayoutGrid },
  { key: 'marketing', label: 'Marketing', icon: Megaphone },
  { key: 'leadership', label: 'Leadership', icon: BarChart3 },
];

export default function App(): JSX.Element {
  const [workspace, setWorkspace] = useState<Workspace>('enrollment');

  return (
    <div
      className="app-shell"
      style={{
        minHeight: '100vh',
        background: 'var(--paper)',
        color: 'var(--ink)',
      }}
    >
      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 10,
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-4)',
          flexWrap: 'wrap',
          padding: '12px 20px',
          background: 'var(--surface)',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <h1
          style={{
            fontSize: 'var(--fs-lg)',
            fontWeight: 700,
            letterSpacing: '-0.02em',
            margin: 0,
          }}
        >
          GT Growth Cockpit
        </h1>

        <WorkspaceToggle
          options={WORKSPACES}
          active={workspace}
          onSelect={setWorkspace}
          ariaLabel="Workspace"
        />

        <div
          style={{
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
          }}
        >
          <Chip tone="flow" title="Connected API base URL (TECH_STACK §5.1)">
            <span data-testid="api-base-url">API · {apiBaseUrl}</span>
          </Chip>
        </div>
      </header>

      <main
        style={{
          maxWidth: 1640,
          margin: '0 auto',
          padding: '20px 20px 64px',
        }}
      >
        {workspace === 'enrollment' && <EnrollmentWorkspace />}
        {workspace === 'marketing' && <MarketingWorkspace />}
        {workspace === 'leadership' && <LeadershipWorkspace />}
      </main>
    </div>
  );
}
