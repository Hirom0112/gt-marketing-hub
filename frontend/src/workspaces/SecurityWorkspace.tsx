import SecurityTab from '../security/SecurityTab';

// M7 — the admin-only Security / observability workspace. Thin wrapper around
// SecurityTab (Panel A: live RLS posture; Panel B: simulated OWASP-mapped
// suspicious-activity feed). Mounted ONLY for an admin seat (App.tsx role-gates
// both the nav item and the render); a rep never sees this surface.
export default function SecurityWorkspace(): JSX.Element {
  return (
    <section
      aria-label="Security workspace"
      data-testid="security-workspace"
    >
      <SecurityTab />
    </section>
  );
}
