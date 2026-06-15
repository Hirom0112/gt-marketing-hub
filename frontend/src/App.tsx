import { apiBaseUrl } from './config';

// Minimal app shell. Feature surfaces (pipeline board, deal view,
// dashboard) are intentionally absent — they arrive as later TDD slices
// (see TODO.md). This shell only proves the app boots and resolves its
// API base URL from the build-time env (TECH_STACK §5.1).
export default function App(): JSX.Element {
  return (
    <main className="app-shell">
      <header>
        <h1>GT Growth Cockpit</h1>
        <p>Enrollment &amp; growth operations cockpit</p>
      </header>
      <p data-testid="api-base-url">API base URL: {apiBaseUrl}</p>
    </main>
  );
}
