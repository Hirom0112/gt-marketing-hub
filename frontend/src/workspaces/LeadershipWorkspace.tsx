import { BarChart3, Layers } from 'lucide-react';
import EvalGate from '../EvalGate';
import LandingDashboard from '../LandingDashboard';
import PipelineBoard from '../PipelineBoard';
import Scoreboard from '../Scoreboard';

// S11 leadership workspace — the P2-readable view. Per ASSUMPTIONS A-17, the
// leadership-facing content the operator page used to carry now lives here,
// where FR-6.1 says it belongs. The operator page is two surfaces (calendar +
// family panel); this is the scoreboard/overview surface.
//
// Top-to-bottom narrative:
//   1. Funnel scoreboard + CRM-seam ledger — LandingDashboard (live GET
//      /pipeline: the four stage counts as a KPI strip + the synced/unsynced/
//      conflict aggregate ledger). Aggregate seam is the leadership/ops view;
//      per-family seam now lives in the operator panel.
//   2. Pipeline board — the per-stage funnel board (live GET /pipeline). Moved
//      off the operator page; it is a leadership/overview artifact.
//   3. Leadership scoreboard — FR-6.1 growth rollup (both funnels + eval status).
//   4. Eval gate — FR-4.5 / INV-3 fail-closed gate health.
//
// Every panel round-trips real server data. Native fetch only; no raw hex —
// shared theme tokens + ui primitives so Leadership reads as the same product
// as the operator page.
export default function LeadershipWorkspace(): JSX.Element {
  return (
    <section
      aria-label="Leadership workspace"
      data-testid="leadership-workspace"
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
        <span className="lab">
          Leadership · funnel, seam ledger, growth rollup &amp; gate health
        </span>
      </div>

      {/* 1. Funnel scoreboard (KPI strip) + CRM-seam ledger — live GET /pipeline. */}
      <LandingDashboard />

      {/* 2. Pipeline board — the per-stage funnel, moved off the operator page. */}
      <section
        aria-label="Pipeline overview"
        style={{ display: 'grid', gap: 'var(--s-3)' }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
            color: 'var(--muted)',
          }}
        >
          <Layers size={14} aria-hidden />
          <span className="lab">Pipeline overview</span>
        </div>
        <PipelineBoard />
      </section>

      {/* 3. FR-6.1 growth rollup (both funnels + eval status). */}
      <Scoreboard />

      {/* 4. FR-4.5 / INV-3 fail-closed gate health. */}
      <EvalGate />
    </section>
  );
}
