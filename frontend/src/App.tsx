import ActionPanel from './ActionPanel';
import { apiBaseUrl } from './config';
import FundingTracker from './FundingTracker';
import LandingDashboard from './LandingDashboard';
import SeamView from './SeamView';
import WorkQueue from './WorkQueue';

// App shell + the read-only S0 landing dashboard (FR-2.1) and the S1 enrollment
// work queue (FR-2.5). The shell resolves its API base URL from the build-time
// env (TECH_STACK §5.1), mounts the landing dashboard (GET /pipeline), and the
// enrollment workspace's work queue (GET /work-queue). The pipeline board and
// deal view are routed per-family surfaces wired in a later UI slice.
export default function App(): JSX.Element {
  return (
    <main className="app-shell">
      <header>
        <h1>GT Growth Cockpit</h1>
        <p>Enrollment &amp; growth operations cockpit</p>
      </header>
      <p data-testid="api-base-url">API base URL: {apiBaseUrl}</p>
      <LandingDashboard />
      <section aria-label="Enrollment workspace" className="enrollment-workspace">
        <WorkQueue />
        <ActionPanel familyId="fam-a" />
        <FundingTracker familyId="fam-a" />
        <SeamView />
      </section>
    </main>
  );
}
