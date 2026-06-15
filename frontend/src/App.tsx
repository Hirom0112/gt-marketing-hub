import { apiBaseUrl } from './config';
import LandingDashboard from './LandingDashboard';

// App shell + the read-only S0 landing dashboard (FR-2.1). The pipeline
// board, deal view, and work queue arrive as later TDD slices (see TODO.md).
// The shell resolves its API base URL from the build-time env (TECH_STACK §5.1)
// and mounts the landing dashboard, which reads GET /pipeline.
export default function App(): JSX.Element {
  return (
    <main className="app-shell">
      <header>
        <h1>GT Growth Cockpit</h1>
        <p>Enrollment &amp; growth operations cockpit</p>
      </header>
      <p data-testid="api-base-url">API base URL: {apiBaseUrl}</p>
      <LandingDashboard />
    </main>
  );
}
