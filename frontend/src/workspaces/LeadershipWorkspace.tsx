import { BarChart3 } from 'lucide-react';
import EvalGate from '../EvalGate';
import Scoreboard from '../Scoreboard';

// S8 leadership workspace — the P2-readable view: the FR-6.1 scoreboard (both
// funnels + eval status) over the FR-4.5 fail-closed eval gate. Wave 2 re-skins
// both onto the shared token/primitive design system.
export default function LeadershipWorkspace(): JSX.Element {
  return (
    <section
      aria-label="Leadership workspace"
      className="leadership-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)' }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          color: 'var(--muted)',
        }}
      >
        <BarChart3 size={14} aria-hidden />
        <span className="lab">Leadership · growth rollup &amp; gate health</span>
      </div>
      <Scoreboard />
      <EvalGate />
    </section>
  );
}
